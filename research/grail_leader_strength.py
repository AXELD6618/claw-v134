# -*- coding: utf-8 -*-
"""
research/grail_leader_strength.py — 龙头/追高 讲座 → grail_hit 缺口 OOS 诊断
================================================================
用户讲座核心(2026-11-08):
  "最大的风险是做弱势" / "只做龙头、剔除跟风杂毛" / "追趋势非日内轮动" /
  "人气流动性溢价来自新题材预期差".

本脚本将"非弱势/趋势确认"作为候选质量维度，在 resume 台账(309)上 OOS 检验:
  弱势(close<T日MA20 或 近20日收益<0)是否显著拖累 grail_hit 边缘(后续≥2日上涨 +
  T+1涨停事件 = grail_full)。若 弱势组 grail_full 显著低于 强势组，则"剔除弱势"是
  legit 候选质量改进(SUPPLEMENTARY 解释层/过滤，非 buy_score)，可能提升 resume 子集
  时序稳定性(PCV)——直接服务于核心目标。

纪律: 本脚本只报告，绝不改 core_factor_model / buy_score / 闸门。任何晋升须完整 OOS 门。
"""
from __future__ import annotations
import json, glob, os
import numpy as np

DAILY = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
LEDGER = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/_grail_hit_resume_sim_ledger.jsonl"


def _load_daily(code):
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p):
        return None
    ds, cl = [], []
    with open(p, encoding="utf-8") as f:
        next(f, None)
        for row in f:
            r = row.strip().split(",")
            if len(r) < 6:
                continue
            try:
                ds.append(int(r[0].replace("-", "")))
                cl.append(float(r[2]))
            except Exception:
                pass
    if len(cl) < 60:
        return None
    return ds, np.array(cl, float)


def _ma20_strength(dates, close, tdate):
    """返回 (idx, ma20, ret20, is_weak, is_trend_up) 或 None。"""
    try:
        idx = next(i for i, d in enumerate(dates) if d == int(tdate))
    except StopIteration:
        return None
    if idx < 20:
        return None
    ma20 = float(np.mean(close[idx - 19: idx + 1]))
    price = close[idx]
    ret20 = price / close[idx - 20] - 1.0
    is_weak = (price < ma20) or (ret20 < 0)
    is_trend_up = (price > ma20) and (ret20 > 0)
    return idx, ma20, ret20, is_weak, is_trend_up


def _rate(mask, key):
    sub = [r for r in mask]
    if not sub:
        return (0.0, 0)
    return (float(sum(1 for r in sub if r.get(key))) / len(sub), len(sub))


def main():
    recs = [json.loads(l) for l in open(LEDGER, encoding="utf-8")]
    strong, weak = [], []
    trend_up, not_trend = [], []
    for r in recs:
        d = _load_daily(r["symbol"])
        if d is None:
            continue
        res = _ma20_strength(d[0], d[1], r["T_date"])
        if res is None:
            continue
        idx, ma20, ret20, is_weak, is_trend_up = res
        r["_ret20"] = ret20
        r["_ma20"] = ma20
        (weak if is_weak else strong).append(r)
        (trend_up if is_trend_up else not_trend).append(r)

    def block(name, grp):
        gf = _rate(grp, "grail_full")
        zt = _rate(grp, "is_zt_T1")
        u2 = _rate(grp, "is_up_T2")
        u3 = _rate(grp, "is_up_T3")
        print(f"\n  ── {name} (n={gf[1]}) ──")
        print(f"    grail_full率 : {gf[0]*100:.2f}%")
        print(f"    T+1 涨停率   : {zt[0]*100:.2f}%")
        print(f"    T+2 上涨率   : {u2[0]*100:.2f}%")
        print(f"    T+3 上涨率   : {u3[0]*100:.2f}%")
        return gf

    print("=" * 64)
    print("  龙头/追高 讲座 → grail_hit resume 子集 OOS 诊断")
    print("=" * 64)
    gf_s = block("强势(close≥MA20 且 20日收益≥0)", strong)
    gf_w = block("弱势(close<MA20 或 20日收益<0)", weak)

    # 两比例 z 检验 (grail_full): 强势 vs 弱势
    ns, ps = gf_s[1], gf_s[0]
    nw, pw = gf_w[1], gf_w[0]
    if ns and nw and pw > 0 and pw < 1 and ps > 0 and ps < 1:
        se = ((ps * (1 - ps) / ns) + (pw * (1 - pw) / nw)) ** 0.5
        z = (ps - pw) / se
        print(f"\n  grail_full 强势-弱势 z = {z:.2f}  (正=强势显著更好)")
        lift = ps / pw if pw > 0 else float("inf")
        print(f"  grail_full lift(强/弱) = {lift:.2f}×")

    gf_t = block("趋势确认(close>MA20 且 20日收益>0)", trend_up)
    gf_n = block("非趋势确认", not_trend)
    nt, pt = gf_t[1], gf_t[0]
    nn, pn = gf_n[1], gf_n[0]
    if nt and nn and pt > 0 and pt < 1 and pn > 0 and pn < 1:
        se = ((pt * (1 - pt) / nt) + (pn * (1 - pn) / nn)) ** 0.5
        z = (pt - pn) / se
        print(f"\n  趋势确认 vs 非趋势 grail_full z = {z:.2f}")
        print(f"  grail_full lift(趋势/非趋势) = {pt/pn:.2f}×" if pn > 0 else "")

    print("\n  结论: 若 弱势组 grail_full 显著低于 强势组(z≥2 且 lift≥1.5)，则")
    print("  '剔除弱势' 是 legit 候选质量改进(SUPPLEMENTARY)，可重测 resume PCV。")


if __name__ == "__main__":
    main()
