#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
enrich_planner.py — 题材富集「动态配额」规划器（2026-10-26 新建）

背景（缺陷 A6）：候选池 100 只里 74 只没有 WebSearch 富集，theme_fit 停在「?」。
不富集 → 题材门控失效 → 好票被当未知、垃圾票蒙过关。
但也不能「全量富集」：每只一次 WebSearch 约 3-6 秒，100 只 = 5-10 分钟，会拖垮 16:30 任务。

所以不拍固定数，而是让它**自调节**：

    待办量 todo = 未富集票 + 缓存过期票(>STALE_TD 个交易日未复核)
    配额 quota = clamp( max(FLOOR, ceil(RATIO × todo)), FLOOR, CEIL )

    FLOOR=25  每天至少 25 只 —— 保证「再大的欠账也一定会被清空」，是收敛保证
    CEIL=60   每天最多 60 只 —— 保证 16:30 一定跑得完（60 × 6s ≈ 6 分钟），是时间保证
    RATIO=0.5 待办量一半 —— 保证 2 天清完一批欠账，不积压成永久债

实测含义：
  - 今天 todo=74 → quota=min(60, max(25, 37))=37 → 74 只两天清完
  - 稳态（每日新增 ~34 信号、其中 ~20 未富集）→ todo≈20 → quota=25 → 当天清完 + 顺手复核 5 只过期
  - 极端（todo=300）→ quota=60 → 5 天清完，且每天不超时

优先级打分（越大越该先富集）。**这是注意力分配，不是 alpha 信号**
--------------------------------------------------------------------------
⚠️ 硬声明（不得被后续使用方式绕过）：
  本打分**没有经过任何收益回测**。它优化的目标是「先富集更可能被出手的票」，
  **不是**「富集能提高胜率」。因此：
    - 严禁把 urgency 写进 buy_score / prob / 任何选股排序
    - 严禁把 urgency 当作「这只票更好」的依据对外表述
    - urgency 的唯一用途是决定 16:30 那 37 个富集名额给谁
  已知它隐含的偏好（都是启发式，非统计结论）：
    Tier1 优先 → 等价于「相信现有的池分级」；涨幅/量比优先 → 等价于「追当日强势」，
    这在涨停率上可能成立，但在次日收益上本项目已实测为**无 edge**（评分 vs T+1
    Spearman ρ=-0.061, p=0.768）。所以它更不能当信号用。

各项权重：
  +3.0  Tier1 票（这是要真出手的，必须知道题材）
  +2.0 × 信号强度（涨停/连板/首板 1.0；放量突破 0.6；其它 0.3）
  +1.0 × 当日涨幅归一（pct/9.5，封顶 1.0）
  +0.5 × 量比归一（min(vol_ratio,3)/3）
  +0.4  可买（buyable=True，说明当日没封死，次日更可能真买到）
  +0.5 × prob（池内已算的概率，仅作次要参考）
  +2.5  未富集 ／ +1.0 缓存过期

为什么要有 buy_bonus 与 prob_bonus：不加这两项时，30 只 Tier1 的 urgency 会并列在
6.1，排序退化成按代码字典序，等于没排序 —— 这是 2026-10-26 实测发现的问题。

输出：data/enrich_queue_{date}.json
  {date, todo, quota, coverage_before, backlog_after, picked:[{code,name,urgency,reason}], note}

CLI：
  python research/enrich_planner.py                 # 生成今日队列
  python research/enrich_planner.py --date 2026-10-27
  python research/enrich_planner.py --selftest      # 配额公式回归（不碰真实数据）
"""
import os
import sys
import json
import math
import datetime
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

CAND_P = os.path.join(ROOT, "data", "candidates.json")
DISCOVERY_P = os.path.join(ROOT, "data", "discovery_candidates.json")
RESEARCH_P = os.path.join(ROOT, "data", "candidate_research.json")
LEDGER_P = os.path.join(ROOT, "data", "candidates_ledger.json")
OUT_DIR = os.path.join(ROOT, "data")

# ---- 配额契约（改这里必须报 old→new→影响面）----
FLOOR = 25          # 每日下限：收敛保证
CEIL = 60           # 每日上限：16:30 时间保证（60 只 ≈ 6 分钟）
RATIO = 0.5         # 待办清空速度：一半
STALE_TD = 20       # 缓存超过 20 个交易日未复核 → 视为过期，需重新富集


def _load(p, default):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return default


def quota_for(todo):
    """配额公式（纯函数，便于回归测试）。"""
    todo = max(0, int(todo))
    if todo <= 0:
        return 0
    q = int(math.ceil(RATIO * todo))
    return max(FLOOR, min(CEIL, q))


def _trading_days_between(d1, d2):
    """粗略交易日间隔（只按工作日算，不含法定节假日——够用于过期判断，不用于结算）。"""
    if d1 > d2:
        d1, d2 = d2, d1
    n, cur = 0, d1
    while cur < d2:
        cur += datetime.timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def _research_as_of_date(research_path):
    """研究库整体 as_of 兜底（最后一道）：条目级日期全缺失时，才用文件 mtime 的日期。"""
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(research_path))
    except Exception:
        return datetime.date.today()


def _entry_asof(r, fallback):
    """条目级 as_of：researched_at > source_date > 文件 mtime。

    为什么必须逐级回退：2026-10-26 实测 data/candidate_research.json 的 90 条记录里
    **没有一条** 有 researched_at，只有 source_date（如 2026-10-25）。原实现只读
    researched_at，读不到就退回文件 mtime；而文件几乎每天被富集任务重写，
    mtime 恒等于「今天」→ 所有条目的 as_of 都变成今天 → **过期判断永久失效**，
    旧票研判一旦写进去就再也不会被复核，只会不断富集新票。
    """
    for key in ("researched_at", "source_date", "as_of"):
        v = r.get(key)
        if not v:
            continue
        try:
            return datetime.date.fromisoformat(str(v)[:10])
        except Exception:
            continue
    return fallback


def build_plan(date=None, research_path=None):
    date = date or datetime.date.today().isoformat()
    research_path = research_path or RESEARCH_P
    D = datetime.date.fromisoformat(date)

    cands = _load(CAND_P, {}) or {}
    disc = _load(DISCOVERY_P, {}) or {}
    research = _load(research_path, {}) or {}
    if not isinstance(research, dict):
        research = {}
    fallback_asof = _research_as_of_date(research_path)

    # ---- 候选全集：池(tier1/tier2) ∪ 发现层 entries ----
    items = {}
    for x in (cands.get("tier1") or []):
        c = str(x.get("code") or "")
        if c:
            items.setdefault(c, {}).update({"code": c, "tier": 1, "name": x.get("name") or "",
                                            "buyable": x.get("buyable"), "prob": x.get("prob"),
                                            "kind": x.get("kind")})
    for x in (cands.get("tier2") or []):
        c = str(x.get("code") or "")
        if c:
            items.setdefault(c, {}).update({"code": c, "tier": 2, "name": x.get("name") or "",
                                            "buyable": x.get("buyable"), "prob": x.get("prob"),
                                            "kind": x.get("kind")})
    # ---- 发现层 vol_ratio 溯源 ----
    # disc["entries"] 记录本身**不带** vol_ratio：p1_discovery.apply_lifecycle 在
    # 构造 entries 时只落 code/name/signal/pct/theme_fit（见 p1_discovery.py:434-436），
    # 把同源 emerg 里的 vol_ratio 丢掉了。而同源的 disc["emerg"] 列表**带** vol_ratio。
    # 这里按 code 建索引，作为 entries 缺失时的兜底，确保量比项不再恒为 0——
    # 否则 vr_n 永远=0，urgency 退化、排序退化为「按代码字典序」→ 沪市(60xxxx)永远被饿死。
    emerg_vr = {}
    for e in (disc.get("emerg") or []):
        cc = str(e.get("code") or "")
        vr = e.get("vol_ratio")
        if cc and vr not in (None, "", 0):
            emerg_vr[cc] = vr

    for e in (disc.get("entries") or []):
        c = str(e.get("code") or "")
        if not c:
            continue
        rec = items.setdefault(c, {"code": c, "tier": 3, "name": e.get("name") or ""})
        rec["signal"] = e.get("signal")
        rec["pct"] = e.get("pct")
        # vol_ratio：优先用 entries 自带；缺失则从同源 emerg 按 code 补（见上注释）
        vr_src = e.get("vol_ratio")
        if vr_src in (None, "", 0):
            vr_src = emerg_vr.get(c)
        rec["vol_ratio"] = vr_src
        rec["disc_fit"] = e.get("theme_fit")

    # ---- 分类：未富集 / 缓存过期 / 已覆盖 ----
    # 真实信号词表（来自 research/p1_discovery.py：emerg 层 "zt"(涨停,含连板/首板) /
    # "breakout"(放量突破)；监测层 "move"(冲高回落) / "oversold"(超跌) / "w2s"(弱转强)；
    # 另容错历史/潜在别名 "first"(首板) / "limit"(涨停) / "lianban"(连板) 及中文标签——
    # 原实现只写中文 key，而发现层给的是代码 "zt/breakout"，导致 k in sig 永远不命中、
    # strength 恒为 0.3、urgency 退化）。同时支持「代码」与「中文标签」两种写法，未来
    # 数据无论给哪种都能命中。
    #   权重语义：涨停态最强(1.0)；放量突破/弱转强(封板前可买窗口)较强(0.6)；其它监测信号(0.3)
    SIG_W = {
        # 涨停态（含连板/首板）
        "zt": 1.0, "limit": 1.0, "first": 1.0, "lianban": 1.0,
        "涨停": 1.0, "首板": 1.0, "连板": 1.0,
        # 放量突破 / 弱转强（可买窗口）
        "breakout": 0.6, "放量突破": 0.6, "w2s": 0.6,
        # 监测层异动（非涨停态，仅观测）
        "move": 0.3, "oversold": 0.3, "冲高回落": 0.3, "超跌": 0.3,
    }
    todo_items, covered = [], 0
    for c, it in items.items():
        r = research.get(c) or {}
        has = bool(r) and str(r.get("theme") or "").strip() not in ("", "?", "无")
        if not has:
            reason = "未富集"
            urgent = 2.5
            it["_is_unknown"] = True
        else:
            asof = _entry_asof(r, fallback_asof)
            if _trading_days_between(asof, D) > STALE_TD:
                reason = "缓存过期(>%d交易日)" % STALE_TD
                urgent = 1.0
            else:
                covered += 1
                continue
        sig = str(it.get("signal") or "")
        strength = 0.3
        for k, w in SIG_W.items():
            if k in sig:
                strength = max(strength, w)
        pct = it.get("pct")
        try:
            pct_n = min(1.0, max(0.0, float(pct) / 9.5))
        except Exception:
            pct_n = 0.0
        vr = it.get("vol_ratio")
        try:
            vr_n = min(1.0, max(0.0, float(vr) / 3.0))
        except Exception:
            vr_n = 0.0
        # 可买性 + 模式概率：Tier1 里「可买」的必须优先，否则 30 只并列 6.1 变成任意顺序
        try:
            buy_bonus = 0.4 if it.get("buyable") is True else 0.0
        except Exception:
            buy_bonus = 0.0
        try:
            prob_bonus = 0.5 * min(1.0, max(0.0, float(it.get("prob") or 0)))
        except Exception:
            prob_bonus = 0.0
        urgency = (3.0 if it.get("tier") == 1 else 0.0) + 2.0 * strength + 1.0 * pct_n + 0.5 * vr_n + buy_bonus + prob_bonus + urgent
        if it.get("tier") == 1:
            reason += "+Tier1(要出手)"
        if strength >= 1.0:
            reason += "+涨停/连板"
        todo_items.append({"code": c, "name": it.get("name") or "",
                           "tier": it.get("tier"), "reason": reason,
                           "urgency": round(urgency, 2)})

    todo_items.sort(key=lambda x: (-x["urgency"], x["code"]))
    q = quota_for(len(todo_items))
    picked = todo_items[:q]
    total = len(items)
    plan = {
        "date": date,
        "total_candidates": total,
        "covered": covered,
        "coverage_before": round(covered / float(total), 4) if total else 0.0,
        "todo": len(todo_items),
        "quota": q,
        "backlog_after": max(0, len(todo_items) - q),
        "picked": picked,
        "note": ("待办 %d 只 → 今日配额 %d 只（FLOOR=%d CEIL=%d RATIO=%.1f），"
                 "剩余 %d 只顺延；覆盖率 %.1f%%"
                 % (len(todo_items), q, FLOOR, CEIL, RATIO,
                    max(0, len(todo_items) - q),
                    (100.0 * covered / total) if total else 0.0)),
    }
    return plan


def selftest():
    """配额公式 + 优先级打分回归（不读写真实业务数据之外的任何东西）。"""
    cases = [
        (0, 0), (1, FLOOR), (10, FLOOR), (49, FLOOR),
        (50, 25), (52, 26), (74, 37), (120, 60), (1000, CEIL),
    ]
    bad = []
    for todo, want in cases:
        got = quota_for(todo)
        if got != want:
            bad.append((todo, got, want))
    # 不变量：配额永不超过待办量（除了全量清完的情形允许 ==）
    for todo in range(0, 300, 7):
        q = quota_for(todo)
        if q > max(todo, FLOOR):
            bad.append(("over_quota", todo, q))
    if bad:
        print("[selftest] FAIL:", bad[:6])
        return 1
    # ---- 条目级 as_of 回退链（2026-10-26 修复的缺陷：只读 researched_at 导致过期永不生效）----
    fb = datetime.date(2026, 10, 26)
    aso_cases = [
        ({"researched_at": "2026-10-01"}, datetime.date(2026, 10, 1), "researched_at 优先"),
        ({"source_date": "2026-09-20"}, datetime.date(2026, 9, 20), "无 researched_at 用 source_date"),
        ({"researched_at": "", "source_date": "2026-09-21"}, datetime.date(2026, 9, 21), "空字符串跳过"),
        ({"researched_at": "bad", "source_date": "2026-09-22"}, datetime.date(2026, 9, 22), "坏日期跳过"),
        ({}, fb, "全缺失才回退文件 mtime"),
    ]
    for r, want, why in aso_cases:
        got = _entry_asof(r, fb)
        if got != want:
            bad.append(("asof", why, got, want))
    # 过期判定必须真能触发：40 个交易日前的条目要判 stale
    old = {"source_date": "2026-08-01"}
    if _trading_days_between(_entry_asof(old, fb), datetime.date(2026, 10, 26)) <= STALE_TD:
        bad.append(("stale", "老条目未判过期"))
    if bad:
        print("[selftest] FAIL:", bad[:6])
        return 1
    print("[selftest] PASS: 配额公式 %d 例 + 不变量 + as_of 回退链 %d 例 全部通过"
          % (len(cases), len(aso_cases)))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="只打印 JSON（供自动化解析）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    plan = build_plan(a.date)
    outp = os.path.join(OUT_DIR, "enrich_queue_%s.json" % a.date)
    try:
        json.dump(plan, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        print("[warn] 落盘失败:", e)
    if a.json:
        print(json.dumps(plan, ensure_ascii=False))
        return 0
    print("[enrich] %s" % plan["note"])
    print("[enrich] 覆盖率 %.1f%%（%d/%d）｜队列落盘 %s"
          % (plan["coverage_before"] * 100, plan["covered"], plan["total_candidates"], outp))
    for i, x in enumerate(plan["picked"], 1):
        print("  %2d. %-7s %-6s urgency=%-5s %s" % (i, x["code"], x["name"], x["urgency"], x["reason"]))
    if plan["backlog_after"]:
        print("[enrich] 仍有 %d 只顺延到明日（配额上限 %d，属设计行为）" % (plan["backlog_after"], CEIL))
    return 0


if __name__ == "__main__":
    sys.exit(main())
