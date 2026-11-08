# -*- coding: utf-8 -*-
"""
research/pre_event_leakage.py — 埋伏公告前能力（§12 build #2 延伸 + 舆情交叉验证）
====================================================================================
目标：在公告披露之前，用「交易日数据指标异常(量/价/盘口) × 公告前舆情异动」交叉验证，
     识别"信息可能已提前泄露/埋伏"的票，作为后台算法模型的 SUPPLEMENTARY 信号能力。

三层结构：
  Leg A 数据指标（可历史回测，本地日线即可）：
    - 量能异常 LEAKAGE_VOL：放量但不涨（vol_ratio>3 且 |ret|<3%）→ 收集/泄露签名
    - 横盘累积 LEAKAGE_ACCUM：前期低波动低量 + 温和放量微涨 → 埋伏累积
  Leg B 舆情（前向采集，本地无历史归档）：
    - 从 claw-v134 的 sentiment_db.db（WeChat 图像 LLM Vision 情绪）按票聚合
      提及度 + 极性 + "重组/并购/股权变更/定增" 类关键词，作 SENTIMENT_PRE 信号
  Cross 交叉验证：
    - Leg A × 真实催化标签（TDX MCP 采集历史公告）→ 验证"放量异动是否真在公告前频现"
    - Leg A × Leg B → 双触发 = 高置信埋伏候选（COMBO）

纪律（不可逾越）：
  - 不降任何闸门；只作 WATCH / 解释层，绝不进 buy_score / core_factor_model。
  - 若以 grail_hit 口径（T买非涨停→T+1涨停事件→+2日）评估 Leg A，须过 adjudicate_grail 完整门。
  - 埋伏型本身最优持有期是"至披露日"，与 grail_hit 的 T+1 结构不同 → 单独文档化，
    不强行塞入 grail_hit，也不因此降 grail_hit 门槛。

用法：
  python -m research.pre_event_leakage --self-test
  python -m research.pre_event_leakage --backtest --wstart 20240701 --wend 20261106
  python -m research.pre_event_leakage --eval <jsonl> --base <rate>
  python -m research.pre_event_leakage --cross-validate --lookback 20
"""
from __future__ import annotations
import json, math, time, argparse
from pathlib import Path

import numpy as np
import pandas as pd
import oospf_validator as V
from research.forward_oos import make_record, append_records, eval_records
from research import catalyst_proximity as CP

LIMIT = V.LIMIT
MIN_LEN = V.MIN_LEN
DAILY = V.DAILY
NOTICES_JSONL = CP.NOTICES_JSONL

# Leg A 参数
VOL_BASE = 20          # 量能基线窗口
VOL_SPIKE = 3.0        # 放量阈值（相对 MA20）
FRESH_VOL = 2.5        # 前一交易日量比上限（确保是"新发"异动而非持续极端）
QUIET_VOL = 1.3        # 横盘期量比上限
QUIET_STD = 0.02       # 横盘期日收益标准差上限
ACCUM_VOL = 2.0        # 累积启动量比
ACCUM_UP = 0.005       # 累积启动微涨
SMALL_MOVE = 0.03      # 放量不涨的"不涨"定义


# ── 特征与信号（纯函数，可单测）──
def compute_features(dates, open_, close, high, low, vol):
    """返回 vol_ratio, ret, intraday_pos, gap 数组（与 daily 列对齐）。"""
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    vol_ratio = np.full(n, np.nan)
    for i in range(1, n):
        base = vol[max(0, i - VOL_BASE):i]
        if base.size and base.mean() > 0:
            vol_ratio[i] = vol[i] / base.mean()
    hl = high - low
    intraday_pos = np.divide(close - low, hl, out=np.full_like(close, np.nan),
                             where=hl > 0)
    gap = np.full(n, np.nan); gap[1:] = open_[1:] / close[:-1] - 1.0
    return vol_ratio, ret, intraday_pos, gap


def detect_signals(vol_ratio, ret):
    """返回 (leak_vol_mask, leak_accum_mask) 布尔数组。"""
    n = len(ret)
    leak_vol = np.zeros(n, dtype=bool)
    leak_accum = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(vol_ratio[i]) or np.isnan(ret[i]):
            continue
        if not (-LIMIT < ret[i] < LIMIT):      # 必须可买入（非涨停）
            continue
        # LEAKAGE_VOL：放量不涨（新发异动）
        if (vol_ratio[i] > VOL_SPIKE and abs(ret[i]) < SMALL_MOVE
                and (i == 1 or np.isnan(vol_ratio[i - 1]) or vol_ratio[i - 1] < FRESH_VOL)):
            leak_vol[i] = True
        # LEAKAGE_ACCUM：前期低波动低量 + 温和放量微涨
        if i >= VOL_BASE:
            w = slice(i - VOL_BASE, i)
            if (np.nanmean(vol_ratio[w]) < QUIET_VOL and np.nanstd(ret[w]) < QUIET_STD
                    and vol_ratio[i] > ACCUM_VOL and ret[i] > ACCUM_UP):
                leak_accum[i] = True
    return leak_vol, leak_accum


# ── 回测（grail_hit 口径：T 买非涨停 → T+1 涨停事件）──
def backtest(wstart, wend, out_jsonl=None):
    out_jsonl = Path(out_jsonl) if out_jsonl else (V.CACHE / "forward_oos_leakage.jsonl")
    mkt_sum, mkt_cnt = {}, {}
    base_tot = base_hit = 0
    recs = []
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        code = f.name[:-4]
        close = df["收盘"].to_numpy(float)
        open_ = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float)
        low = df["最低"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        n = len(close)
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        for i in range(1, n - 1):
            di = dates[i]
            if not (wstart <= di <= wend) or np.isnan(ret[i]):
                continue
            # 市场同日均值（窗口内）
            mkt_sum[di] = mkt_sum.get(di, 0.0) + ret[i]
            mkt_cnt[di] = mkt_cnt.get(di, 0) + 1
            # 基准率（可买宇宙次日涨停率）
            if -LIMIT < ret[i] < LIMIT:
                base_tot += 1
                if ret[i + 1] >= LIMIT:
                    base_hit += 1
        # 特征 + 信号
        vr, r, _, _ = compute_features(dates, open_, close, high, low, vol)
        lv, la = detect_signals(vr, r)
        for i in range(1, n - 1):
            di = dates[i]
            if not (wstart <= di <= wend):
                continue
            f1 = ret[i + 1]
            if np.isnan(f1):
                continue
            mc = mkt_cnt.get(di, 0)
            mkt = (mkt_sum.get(di, 0.0) / mc) if mc > 0 else float("nan")
            zt = f1 >= LIMIT
            if lv[i]:
                recs.append(make_record(di, code, "LEAKAGE_VOL", True, f1, zt, mkt))
            if la[i]:
                recs.append(make_record(di, code, "LEAKAGE_ACCUM", True, f1, zt, mkt))
        if (fi + 1) % 1000 == 0:
            print(f"  [bt load] {fi+1}/{len(files)}")
    base_rate = (base_hit / base_tot) if base_tot else float("nan")
    append_records(str(out_jsonl), recs)
    print(f"[backtest] 窗口 {wstart}~{wend}  信号记录 {len(recs)}  "
          f"基准可买次日涨停率={base_rate*100:.3f}%  → {out_jsonl}")
    return base_rate, len(recs)


# ── 基线：随机 lookback 日窗口含 ≥1 LEAKAGE 的概率（从 forward_oos_leakage.jsonl 实证估计）──
def baseline_leakage_probability(window=20, wstart=20240701, wend=20261106):
    """用真实回测产物估计：随机选一只票、随机选一个 lookback 日窗口，含≥1 LEAKAGE 的概率。
    这是 cross_validate 的 H0 基线：若事件前窗口命中率显著高于它，说明 LEAKAGE 确为埋伏前兆。"""
    from collections import defaultdict
    leak = defaultdict(set)
    n_rec = 0
    p = V.CACHE / "forward_oos_leakage.jsonl"
    if not p.exists():
        return dict(p_window=float("nan"), hit_windows=0, total_windows=0, n_leak_records=0)
    for line in open(p, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if not r.get("signal"):
            continue
        leak[str(r.get("code"))].add(int(r.get("date")))
        n_rec += 1
    hit_windows = 0
    total_windows = 0
    for code, dset in leak.items():
        td = _load_daily(code)
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
                n_leak_records=n_rec)


def _binom_survival(k, n, p):
    """P(X >= k) under Bin(n, p)；n 小，直接 math.comb（不引第三方依赖）。"""
    if n == 0:
        return float("nan")
    if k <= 0:
        return 1.0
    if p <= 0:
        return 1.0
    if p >= 1:
        return 0.0
    s = 0.0
    for i in range(k, n + 1):
        s += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
    return min(1.0, s)


# ── 交叉验证：Leg A × 真实催化标签（验证"放量异动是否真在公告前频现"）──
def cross_validate_leakage_vs_catalyst(notices_jsonl=NOTICES_JSONL, lookback=20):
    """读 catalyst_notices.jsonl（material 事件，agent 经 MCP 采集），对每条事件(D)检查其披露前
    lookback 交易日内是否触发 LEAKAGE_VOL/ACCUM；算 recall、与随机窗口基线比较、二项显著性、分品类 recall。
    返回结构化结果。H0：LEAKAGE 与事件无关（命中率≈基线）；若 recall 显著>基线 → 埋伏前兆成立。"""
    events = [json.loads(l) for l in open(notices_jsonl, encoding="utf-8")] if Path(notices_jsonl).exists() else []
    if not events:
        print("[cross] 无催化标签（先经 MCP 采集 catalyst_notices.jsonl）")
        return None
    base = baseline_leakage_probability(window=lookback)
    pw = base["p_window"]
    fired_pre = 0
    total = 0
    per_cat = {}
    examples = []
    for ev in events:
        sym = ev["symbol"]; D = int(ev["date"]); cat = ev["category"]
        td = _load_daily(sym)
        if td is None:
            continue
        dates, open_, close, high, low, vol = td
        # 需要披露前至少 lookback 个交易日（否则前窗口不足，跳过且不计入分母）
        idx = [i for i in range(len(dates)) if dates[i] < D]
        if not idx or idx[-1] < lookback:
            continue
        total += 1
        vr, r, _, _ = compute_features(*td)
        lv, la = detect_signals(vr, r)
        last = idx[-1]
        start = last - lookback + 1
        pre = slice(start, last + 1)
        fired = bool(lv[pre].any() or la[pre].any())
        if fired:
            fired_pre += 1
            examples.append((sym, D, cat))
        per_cat.setdefault(cat, [0, 0])
        per_cat[cat][0] += 1
        if fired:
            per_cat[cat][1] += 1
    recall = (fired_pre / total) if total else float("nan")
    pval = _binom_survival(fired_pre, total, pw) if total else float("nan")
    sig = (pval is not None and pval < 0.05 and recall > pw)
    print(f"[cross] 有效催化事件 {total} 条（披露前≥{lookback}交易日）")
    print(f"  披露前 {lookback} 日内触发 LEAKAGE 比例(recall)={recall*100:.1f}%  (命中 {fired_pre}/{total})")
    print(f"  随机 {lookback} 日窗口含≥1 LEAKAGE 基线={pw*100:.1f}%  "
          f"(来自 {base['n_leak_records']} 条信号 / {base['total_windows']} 窗口)")
    print(f"  二项检验 P(X≥{fired_pre}|n={total},p={pw:.3f})={pval:.4f}  "
          f"-> {'显著>基线 ✔ 埋伏前兆成立' if sig else '不显著/样本不足（须≥100事件）'}")
    for cat, (t, f) in sorted(per_cat.items()):
        print(f"    {cat}: {f}/{t} = {100*f/t:.1f}%")
    if examples:
        print("  命中样本(前10): " + ", ".join(f"{s}@{d}({c})" for s, d, c in examples[:10]))
    return dict(total=total, fired_pre=fired_pre, recall=recall, baseline=pw,
                p_value=pval, significant=sig, per_cat=per_cat)


def build_notices_from_raw(raw_path, out_jsonl=NOTICES_JSONL):
    """把 wenda raw({code:{ok,data:[[标题,时间,链接,来源,摘要]...]}}) 转 catalyst_notices.jsonl。
    仅保留 material 催化事件（classify_catalyst 过滤）；幂等（collect_to_jsonl 内部按 (symbol,date,title) 去重）。"""
    raw = json.load(open(raw_path, encoding="utf-8"))
    items = []
    for code, resp in raw.items():
        if not isinstance(resp, dict):
            continue
        data = resp.get("data")
        if not data:
            continue
        items.append((code, data))
    n = CP.collect_to_jsonl(items, out_jsonl)
    return n


def _load_daily(code):
    f = DAILY / f"{code}.csv"
    if not f.exists():
        return None
    try:
        # 兼容：部分文件缺表头（如 688313.csv 首行为数据），首行被当表头→df["收盘"]抛错→静默丢符号。
        # 修复：探测首行是否含列名，缺则按规范列赋值（数据债 688313 等）。
        with f.open(encoding="utf-8", errors="replace") as _fh:
            _first = _fh.readline()
        _cols = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
        _has_header = ("日期" in _first) or ("开盘" in _first) or ("收盘" in _first)
        if _has_header:
            df = pd.read_csv(f, header=0)
        else:
            df = pd.read_csv(f, header=None)
            df.columns = _cols
        # 兜底：极端情形首行被误当表头→重读并赋值
        if "收盘" not in df.columns:
            df = pd.read_csv(f, header=None)
            df.columns = _cols
        df = df.sort_values("日期").reset_index(drop=True)
    except Exception:
        return None
    if len(df) < MIN_LEN:
        return None
    close = df["收盘"].to_numpy(float)
    open_ = df["开盘"].to_numpy(float)
    high = df["最高"].to_numpy(float)
    low = df["最低"].to_numpy(float)
    vol = df["成交量"].to_numpy(float)
    dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
    return dates, open_, close, high, low, vol


# ── Leg B 舆情脚手架（集成 claw-v134 sentiment_db.db；本工作区无该库，故为接口）──
SENTIMENT_DB_DEFAULT = Path("E:/WorkBuddy_dot_workbuddy/claw-v134/sentiment_db.db")
RUMOR_KW = ["重组", "并购", "股权变更", "控股股东", "实控人", "定增", "资产注入", "借壳", "停牌"]


def sentiment_signal_from_db(db_path=SENTIMENT_DB_DEFAULT, symbol=None, window_days=20):
    """从 sentiment_db 聚合公告前舆情：提及度、净极性、rumor 关键词命中。
    返回 {symbol: {mentions, polarity, rumor_hits}} 或 None（库不可达时）。
    本工作区不含该库 → 返回 None，由 agent 在 claw-v134 环境下调用。"""
    import sqlite3
    if not Path(db_path).exists():
        return None
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        # 仅作 schema 探测；真实聚合需按库表结构实现（claw-v134 侧）
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        con.close()
        return {"_tables": tables, "_note": "schema 探测；按表结构实现按票聚合"}
    except Exception as e:
        return {"error": str(e)}


def combo_signal(leakage_fired, sentiment):
    """Leg A × Leg B：量能异动 且 舆情 rumor 命中 → 高置信埋伏候选（COMBO）。
    纯逻辑函数，便于单测。"""
    if not leakage_fired:
        return False
    if not sentiment:
        return False
    rumor = sentiment.get("rumor_hits", 0)
    return rumor > 0


# ── 单元自检 ──
def _self_test():
    ok = True
    def chk(c, m):
        nonlocal ok
        if not c:
            ok = False; print(f"  [FAIL] {m}")
    # 合成：第 10 天放量(5x)但不涨(1%) → LEAKAGE_VOL
    n = 30
    close = np.ones(n); ret = np.zeros(n); vol = np.ones(n)
    for i in range(1, n):
        close[i] = close[i-1] * (1 + ret[i])
    ret[10] = 0.01; vol[10] = 5.0   # 放量不涨
    vr, r, _, _ = compute_features(np.arange(n)+20240101, close, close, close, close, vol)
    lv, la = detect_signals(vr, r)
    chk(lv[10], "LEAKAGE_VOL 应在第10天触发")
    # 横盘累积：前 10 天低量低波动，第 20 天温和放量微涨
    close2 = np.ones(n); vol2 = np.ones(n)
    close2[20] = 1.01   # ret[20]=1% 微涨
    vol2[20] = 2.5
    vr2, r2, _, _ = compute_features(np.arange(n)+20240101, close2, close2, close2, close2, vol2)
    _, la2 = detect_signals(vr2, r2)
    chk(la2[20], "LEAKAGE_ACCUM 应在第20天触发")
    # 普通日不应触发
    close3 = np.ones(n); vol3 = np.ones(n); ret3 = np.zeros(n)
    vr3, r3, _, _ = compute_features(np.arange(n)+20240101, close3, close3, close3, close3, vol3)
    lv3, la3 = detect_signals(vr3, r3)
    chk(not lv3.any() and not la3.any(), "普通日应无信号")
    # combo 逻辑
    chk(combo_signal(True, {"rumor_hits": 1}), "LEAKAGE+rumor → COMBO True")
    chk(not combo_signal(True, {"rumor_hits": 0}), "LEAKAGE 无 rumor → COMBO False")
    chk(not combo_signal(False, {"rumor_hits": 1}), "无 LEAKAGE → COMBO False")
    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--backtest", action="store_true")
    ap.add_argument("--eval")
    ap.add_argument("--base", type=float)
    ap.add_argument("--cross-validate", action="store_true")
    ap.add_argument("--build-from-raw")
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--wstart", type=int, default=20240701)
    ap.add_argument("--wend", type=int, default=20261106)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    if a.self_test:
        _self_test()
    elif a.backtest:
        t0 = time.time()
        backtest(a.wstart, a.wend, a.out)
        print(f"  [done] t={time.time()-t0:.0f}s")
    elif a.eval:
        recs = [json.loads(l) for l in open(a.eval, encoding="utf-8")]
        base = a.base if a.base else float("nan")
        feats = sorted(set(r["feature"] for r in recs if r.get("signal")))
        print(f"[eval] {a.eval}  特征={feats}  基准率={base*100 if base==base else 'nan'}%")
        for ft in feats:
            r = eval_records(recs, ft, base)
            print(f"  {ft}: n={r['n']} lift={r['lift']:.3f} 次日涨停={r['sig_zt']*100:.3f}% "
                  f"超额vs市={r['excess_vs_mkt']:.2f}bp t市={r['t_vs_mkt']} t空={r['t_vs_cash']} "
                  f"PCV={r['purged_cv']} -> {r['verdict']}")
    elif a.build_from_raw:
        build_notices_from_raw(a.build_from_raw)
    elif a.cross_validate:
        cross_validate_leakage_vs_catalyst(lookback=a.lookback)
    else:
        print("用法: --self-test | --backtest | --eval <jsonl> --base <r> | --build-from-raw <raw.json> | --cross-validate")


if __name__ == "__main__":
    main()
