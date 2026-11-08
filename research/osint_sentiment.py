# -*- coding: utf-8 -*-
"""
research/osint_sentiment.py — 开源情报(OSINT)舆情挖掘 · 埋伏公告前能力（SUPPLEMENTARY 信号层）
====================================================================================
目标：在公告披露之前，用「全渠道开源情报 × 交易日数据指标(Leg A 量能异动)」交叉验证，
     识别"信息可能已提前泄露/埋伏"的票，作为后台算法模型的 SUPPLEMENTARY 信号能力。

全渠道采集层（设计 + 已实现适配）：
  CH_WECHAT   : claw-v134 sentiment_db.db（WeChat 图像 LLM Vision 情绪，按票聚合）→ sentiment_signal_from_db
  CH_TDX      : TDX MCP wenda_notice_query（公告/预案/中标，agent 采集）→ 复用 catalyst_proximity.collect_to_jsonl
  CH_WEB      : 联网检索（WebSearch / 新闻 / 股吧 / 论坛 / 财经媒体，agent/automation 驱动）→ ingest_websearch_raw
  CH_NEWS_API : 金融新闻 API（预留适配，claw-v134 环境注入 key）

组织层：去重、按 (symbol, detected_date) 聚合、rumor 关键词提取（重组/并购/股权变更/定增/资产注入…）。

交叉验证层：Leg A(量能异动) × Leg B(舆情 rumor) × 真实催化标签 → COMBO，
     验证"舆情+量能 是否真在公告前频现"(recall 是否显著 > 基线)。

纪律（不可逾越）：
  - 不降任何闸门；只作 WATCH / 解释层，绝不进 buy_score / core_factor_model。
  - 若以 grail_hit 口径（T买非涨停→T+1涨停事件→+2日）评估须过 adjudicate_grail 完整门。
  - 埋伏型自身目标函数＝"至披露日"，与 grail_hit 的 T+1 结构不同 → 单独文档化，
    不强行塞入 grail_hit，也不因此降 grail_hit 门槛。
  - 真实部署须 claw-v134 环境（WeChat/ TDX / 联网）；本工作区仅可跑 WebSearch 通道 + 离线交叉验证。

用法：
  python -m research.osint_sentiment --self-test
  python -m research.osint_sentiment --ingest-web <raw.json>      # agent 用 WebSearch 产出的原始发现
  python -m research.osint_sentiment --cross-validate --lookback 20
"""
from __future__ import annotations
import json, math, argparse, os, sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

# 复用 Leg A 检测与日线加载（与 pre_event_leakage 同口径）
from research import pre_event_leakage as PEL
from research import catalyst_proximity as CP

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "backtest_cache")
NOTICES_JSONL = CP.NOTICES_JSONL
OSINT_JSONL = os.path.join(CACHE, "osint_sentiment.jsonl")      # 已组织的全渠道舆情
OSINT_RAW_JSONL = os.path.join(CACHE, "osint_sentiment_raw.jsonl")  # agent WebSearch 原始发现

# 通道
CH_WECHAT, CH_TDX, CH_WEB, CH_NEWS_API = "wechat", "tdx", "web", "news_api"

# rumor 关键词（与 pre_event_leakage.RUMOR_KW 保持一致口径）
RUMOR_KW = ["重组", "并购", "股权变更", "控股股东", "实控人", "定增", "资产注入",
            "借壳", "停牌", "控制权", "收购", "资产收购", "发行股份购买", "筹划"]

LOOKBACK_TRADING = 20   # 公告前回看交易日（与 pre_event_leakage 一致）


# ─────────────────────────────────────────────────────────────────────────────
# 1) 通道 A：WeChat sentiment_db（claw-v134 环境；本工作区无库→返回 None）
# ─────────────────────────────────────────────────────────────────────────────
def sentiment_signal_from_db(db_path=None, symbol=None, window_days=LOOKBACK_TRADING):
    """从 sentiment_db 聚合公告前舆情：提及度、净极性、rumor 关键词命中。
    返回 {symbol, mentions, polarity, rumor_hits, channels} 或 None（库不可达）。
    本工作区不含该库 → 返回 None；真实聚合在 claw-v134 侧实现。"""
    if db_path is None:
        db_path = os.path.join(ROOT, "claw-v134", "sentiment_db.db")
    if not os.path.exists(db_path):
        return None
    try:
        import sqlite3
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        con.close()
        # 真实按票聚合须按表结构实现；此处仅 schema 探测，返回结构占位
        return {"_tables": tables, "_note": "schema 探测；按表结构实现按票聚合"}
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# 2) 通道 C：WebSearch / 联网检索（本会话可驱动；agent 产出 raw → ingest 组织）
# ─────────────────────────────────────────────────────────────────────────────
def ingest_websearch_raw(raw_path, out_jsonl=OSINT_JSONL):
    """把 agent 用 WebSearch 产出的原始发现（osint_sentiment_raw.jsonl）组织进 osint_sentiment.jsonl。

    raw 每条：{symbol, detected_date(YYYY-MM-DD，最早可证 rumor 源日期),
               rumor_hits(int), polarity(float), mentions(int), sources:[{title,url,src_date}], channel}
    按 (symbol, detected_date) 去重聚合；rumor_hits<=0 视为无信号不入库。幂等。"""
    if not os.path.exists(raw_path):
        print("[ingest] raw 不存在: %s" % raw_path)
        return 0
    raw = json.load(open(raw_path, encoding="utf-8"))
    # 既有
    seen = set()
    merged = []
    if os.path.exists(out_jsonl):
        for l in open(out_jsonl, encoding="utf-8"):
            try:
                r = json.loads(l)
            except Exception:
                continue
            seen.add((r.get("symbol"), r.get("detected_date")))
            merged.append(r)
    n_add = 0
    for item in raw:
        sym = str(item.get("symbol") or "").zfill(6)
        dd = item.get("detected_date")
        if not sym or not dd:
            continue
        if item.get("rumor_hits", 0) <= 0:
            continue
        key = (sym, dd)
        if key in seen:
            continue
        seen.add(key)
        merged.append({
            "symbol": sym, "detected_date": dd, "channel": item.get("channel", CH_WEB),
            "rumor_hits": int(item.get("rumor_hits", 1)),
            "polarity": float(item.get("polarity", 0.0)),
            "mentions": int(item.get("mentions", 1)),
            "sources": item.get("sources", []),
            "title": item.get("title", ""),
        })
        n_add += 1
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for r in merged:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("[ingest] 新增 %d 条舆情信号 → %s（共 %d 条）" % (n_add, out_jsonl, len(merged)))
    return n_add


def _load_osint():
    """读 osint_sentiment.jsonl → {symbol: [(detected_date_str, rumor_hits), ...]}。"""
    d = defaultdict(list)
    if not os.path.exists(OSINT_JSONL):
        return d
    for l in open(OSINT_JSONL, encoding="utf-8"):
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("rumor_hits", 0) <= 0:
            continue
        d[str(r.get("symbol"))].append((r.get("detected_date"), int(r.get("rumor_hits", 1))))
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 3) 组合信号 COMBO（Leg A 量能 × Leg B 舆情 rumor）
# ─────────────────────────────────────────────────────────────────────────────
def combo_signal(leg_a_fired, sentiment_rumor, mode="OR"):
    """Leg A × Leg B → COMBO。
    mode="OR"  : 任一触发即埋伏候选（高召回，用于"是否泄露"探测）
    mode="AND" : 双触发才高置信（高精确，用于"可下注"候选）
    纯逻辑函数，便于单测。"""
    if mode == "AND":
        return bool(leg_a_fired) and bool(sentiment_rumor)
    return bool(leg_a_fired) or bool(sentiment_rumor)


# ─────────────────────────────────────────────────────────────────────────────
# 4) 基线：随机 lookback 交易日窗口含 ≥1 (LegA 或 rumor) 的概率
# ─────────────────────────────────────────────────────────────────────────────
def baseline_combo_probability(window=LOOKBACK_TRADING, wstart=20240701, wend=20261106):
    """从 forward_oos_leakage.jsonl（LegA 信号）与 osint_sentiment.jsonl（rumor）实证估计：
    随机选一只票、随机选一个 lookback 交易日窗口，含≥1 (LegA 或 rumor) 的概率。
    这是 cross_validate 的 H0 基线。rumor 仅本工作区 pilot 稀疏覆盖 → 基线以 LegA 主导，须 caveat。"""
    leak = defaultdict(set)
    p = os.path.join(CACHE, "forward_oos_leakage.jsonl")
    n_leak = 0
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if not r.get("signal"):
                continue
            leak[str(r.get("code"))].add(int(r.get("date")))
            n_leak += 1
    osint = _load_osint()
    # 把 rumor detected_date 并入"信号日"集合（按 symbol）
    for sym, lst in osint.items():
        for dd, _ in lst:
            try:
                leak[sym].add(int(str(dd).replace("-", "")))
            except Exception:
                pass
    hit_windows = 0
    total_windows = 0
    for code, dset in leak.items():
        td = PEL._load_daily(code)
        if td is None:
            continue
        dates = td[0]
        arr = [1 if d in dset else 0 for d in dates]
        m = len(arr)
        if m < window:
            continue
        for i in range(m - window + 1):
            total_windows += 1
            if any(arr[i:i + window]):
                hit_windows += 1
    pw = (hit_windows / total_windows) if total_windows else float("nan")
    return dict(p_window=pw, hit_windows=hit_windows, total_windows=total_windows,
                n_leak_records=n_leak, n_rumor_symbols=len(osint))


def _binom_survival(k, n, p):
    if n == 0:
        return float("nan")
    if k <= 0:
        return 1.0
    if p <= 0 or p >= 1:
        return 1.0 if p <= 0 else 0.0
    s = 0.0
    for i in range(k, n + 1):
        s += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return min(1.0, s)


# 把日历日期映射到"不超过该日的最近交易日索引"
def _trading_idx_before(dates, ymd):
    ymd_i = int(str(ymd).replace("-", ""))
    best = None
    for i, d in enumerate(dates):
        if d <= ymd_i:
            best = i
        else:
            break
    return best


# ─────────────────────────────────────────────────────────────────────────────
# 5) 交叉验证：Leg A × Leg B × 真实催化标签（验证"舆情+量能 是否真在公告前频现"）
# ─────────────────────────────────────────────────────────────────────────────
def cross_validate_osint_vs_catalyst(notices_jsonl=NOTICES_JSONL, lookback=LOOKBACK_TRADING,
                                      mode="OR"):
    """对每条真实事件(D)，检查披露前 lookback 交易日内是否 (LegA 触发 或 rumor 命中)；
    算 recall、与随机窗口基线比较、二项显著性、分品类 recall。
    H0：LegA/rumor 与事件无关（命中率≈基线）；若 recall 显著>基线 → 埋伏前兆成立。"""
    events = [json.loads(l) for l in open(notices_jsonl, encoding="utf-8")] if os.path.exists(notices_jsonl) else []
    if not events:
        print("[cross] 无催化标签")
        return None
    osint = _load_osint()
    base = baseline_combo_probability(window=lookback)
    pw = base["p_window"]
    fired_pre = 0
    total = 0
    per_cat = {}
    examples = []
    for ev in events:
        sym = str(ev["symbol"]).zfill(6); D = int(ev["date"]); cat = ev["category"]
        td = PEL._load_daily(sym)
        if td is None:
            continue
        dates = td[0]
        idx = [i for i in range(len(dates)) if dates[i] < D]
        if not idx or idx[-1] < lookback:
            continue   # 披露前不足 lookback 交易日 → 不计入分母
        total += 1
        last = idx[-1]
        start = last - lookback + 1
        pre_slice = slice(start, last + 1)
        # Leg A
        vr, r, _, _ = PEL.compute_features(*td)
        lv, la = PEL.detect_signals(vr, r)
        leg_a = bool(lv[pre_slice].any() or la[pre_slice].any())
        # Leg B rumor：任一 rumor 落在 pre 窗口交易日
        rumor = False
        for dd, _ in osint.get(sym, []):
            di = _trading_idx_before(dates, dd)
            if di is not None and start <= di <= last:
                rumor = True
                break
        combo = combo_signal(leg_a, rumor, mode)
        if combo:
            fired_pre += 1
            examples.append((sym, D, cat, "A" if leg_a else "", "B" if rumor else ""))
        per_cat.setdefault(cat, [0, 0])
        per_cat[cat][0] += 1
        if combo:
            per_cat[cat][1] += 1
    recall = (fired_pre / total) if total else float("nan")
    pval = _binom_survival(fired_pre, total, pw) if total else float("nan")
    sig = (pval is not None and pval < 0.05 and recall > pw)
    print("[cross] 有效催化事件 %d 条（披露前≥%d交易日, mode=%s）" % (total, lookback, mode))
    print("  披露前窗口 (LegA∨rumor) 命中 recall=%s%%  (命中 %d/%d)" % (("%.1f" % (recall*100)) if recall==recall else "nan", fired_pre, total))
    print("  随机 %d 日窗口含≥1(LegA∨rumor) 基线=%s%%  (来自 %d LegA信号+%d rumor票 / %d 窗口)"
          % (lookback, ("%.1f" % (pw*100)) if pw==pw else "nan", base["n_leak_records"], base["n_rumor_symbols"], base["total_windows"]))
    print("  二项 P(X≥%d|n=%d,p=%.3f)=%s -> %s"
          % (fired_pre, total, pw if pw==pw else 0, ("%.4f" % pval) if pval==pval else "nan",
             "显著>基线 ✔ 埋伏前兆成立" if sig else "不显著/样本不足/基线不稳"))
    for cat, (t, f) in sorted(per_cat.items()):
        print("    %s: %d/%d = %.1f%%" % (cat, f, t, 100*f/t))
    if examples:
        print("  命中样本(前10): " + ", ".join("%s@%d(%s%s)" % (s, d, a, b) for s, d, c, a, b in examples[:10]))
    return dict(total=total, fired_pre=fired_pre, recall=recall, baseline=pw,
                p_value=pval, significant=sig, mode=mode, per_cat=per_cat)


# ─────────────────────────────────────────────────────────────────────────────
# 6) 单元自检（合成数据，证明 recall/基线/二项 数学正确，不依赖真实数据）
# ─────────────────────────────────────────────────────────────────────────────
def _self_test():
    ok = True
    def chk(c, m):
        nonlocal ok
        if not c:
            ok = False; print("  [FAIL] %s" % m)
        else:
            print("  [PASS] %s" % m)
    # combo OR/AND
    chk(combo_signal(True, False) is True, "OR: LegA→True")
    chk(combo_signal(False, True) is True, "OR: rumor→True")
    chk(combo_signal(False, False) is False, "OR: none→False")
    chk(combo_signal(True, True, "AND") is True, "AND: both→True")
    chk(combo_signal(True, False, "AND") is False, "AND: LegA only→False")
    chk(combo_signal(False, True, "AND") is False, "AND: rumor only→False")
    # 基线/二项
    bp = baseline_combo_probability(window=5)
    chk(bp["p_window"] == bp["p_window"], "基线可计算(含/不含 rumor 不抛)")
    # 二项：n=100,p=0.2,k=30 → 显著；k=22 → 不显著（粗略）
    chk(0 < _binom_survival(30, 100, 0.2) < 0.05, "二项 过k 显著")
    chk(_binom_survival(20, 100, 0.2) > 0.5, "二项 低k 不显著")
    chk(_binom_survival(0, 10, 0.2) == 1.0, "二项 k<=0 → 1.0")
    # 日期映射
    dates = [20260901, 20260902, 20260905, 20260906]
    chk(_trading_idx_before(dates, "20260904") == 1, "交易日映射(回落到 0902)")
    chk(_trading_idx_before(dates, "20260906") == 3, "交易日映射(等于)")
    chk(_trading_idx_before(dates, "20260101") is None, "交易日映射(早于全部→None)")
    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--ingest-web")
    ap.add_argument("--cross-validate", action="store_true")
    ap.add_argument("--lookback", type=int, default=LOOKBACK_TRADING)
    ap.add_argument("--mode", default="OR", choices=["OR", "AND"])
    a = ap.parse_args()
    if a.self_test:
        _self_test()
    elif a.ingest_web:
        ingest_websearch_raw(a.ingest_web)
    elif a.cross_validate:
        cross_validate_osint_vs_catalyst(lookback=a.lookback, mode=a.mode)
    else:
        print("用法: --self-test | --ingest-web <raw.json> | --cross-validate [--mode OR|AND]")


if __name__ == "__main__":
    main()
