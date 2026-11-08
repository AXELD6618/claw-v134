#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
超跌反弹假设 OOS 验证：连跌≥7交易日(每日均非跌停) → 尾盘买入 → T+1 表现
========================================================================
假设(用户提出):
  筛选: 当日下跌 且 未跌停 且 连续下跌不少于7个交易日
  动作: 尾盘(T收盘)买入
  疑问: T+1 低开/高开后, 盘中或收盘前会上涨或涨停?

验证口径(与主系统同一铁律):
  gap      = T+1开盘 / T收盘 - 1        (低开<0 / 高开>0)
  intra_up = T+1最高 / T收盘 - 1        (盘中是否涨过 T 收盘价)
  close_r  = T+1收盘 / T收盘 - 1        (收盘涨跌, 即字面持有收益)
  limit_up = T+1收盘 >= T收盘*(1+涨停阈)
  同时报左尾: P(T+1 续跌≥5%) / P(T+1 跌停)
  PF 代理 = mean(1+close_r/100); 与全样本基线 t 检验
"""
import os, json, math, statistics as st

BASE = os.path.dirname(os.path.abspath(__file__))
KL = json.load(open(os.path.join(BASE, "..", "data", "v74_klines_parsed.json"),
                    encoding="utf-8"))["stocks"]


def thr_of(code):
    return 19.8 if code.startswith(("300", "301", "688", "689")) else 9.8


def down_thr(code):
    return (19.8 if code.startswith(("300", "301", "688", "689")) else 9.8) / 100.0


def main(min_streak=7):
    rows = []          # 所有 T 日(基线)
    sig = []           # 连跌≥min_streak 且 T 未跌停
    streak_hist = {}
    for code, d in KL.items():
        bars = d.get("bars") or []
        if len(bars) < min_streak + 5:
            continue
        dt = down_thr(code)
        streak = 0
        for i in range(1, len(bars)):
            b, p = bars[i], bars[i - 1]
            if not (b["close"] and p["close"]):
                streak = 0
                continue
            is_down = b["close"] < p["close"]
            streak = streak + 1 if is_down else 0
            # 基线: T 日任意
            if i + 1 < len(bars):
                nxt = bars[i + 1]
                if nxt["open"] and b["close"]:
                    rows.append((code, (nxt["open"] / b["close"] - 1) * 100,
                                        (nxt["high"] / b["close"] - 1) * 100,
                                        (nxt["close"] / b["close"] - 1) * 100,
                                        thr_of(code)))
            # 信号: 连跌达标 且 当日未跌停
            if streak >= min_streak:
                streak_hist[streak] = streak_hist.get(streak, 0) + 1
                drop = (b["close"] / p["close"] - 1) * 100
                if drop <= -dt * 100 + 0.01:      # 当日已跌停 → 排除
                    continue
                if i + 1 >= len(bars):
                    continue
                nxt = bars[i + 1]
                if not (nxt["open"] and nxt["high"] and nxt["close"] and b["close"]):
                    continue
                gap = (nxt["open"] / b["close"] - 1) * 100
                intra = (nxt["high"] / b["close"] - 1) * 100
                close_r = (nxt["close"] / b["close"] - 1) * 100
                lu = nxt["close"] >= b["close"] * (1 + thr_of(code) / 100 * 0.999)
                sig.append({"code": code, "gap": gap, "intra": intra,
                            "close_r": close_r, "limit_up": lu,
                            "streak": streak, "t_close": b["close"]})

    base_close = [r[3] for r in rows]
    print("=" * 92)
    print("超跌反弹假设 OOS 验证 · 54只×200日真实前复权日线 · 连跌≥%d日且未跌停" % min_streak)
    print("=" * 92)
    print(f"基线(任意T日→T+1收盘) n={len(base_close)} 均值 {st.mean(base_close):+.3f}%  "
          f"胜率 {100*sum(1 for x in base_close if x>0)/len(base_close):.1f}%")
    if not sig:
        print("无满足条件的信号样本。")
        return
    print(f"\n信号样本 n={len(sig)}  连跌长度分布(部分): " +
          ", ".join(f"{k}日×{streak_hist[k]}" for k in sorted(streak_hist) if k < 15) +
          (f" … 最长 {max(streak_hist)}日" if streak_hist else ""))

    gap = [x["gap"] for x in sig]
    intra = [x["intra"] for x in sig]
    close_r = [x["close_r"] for x in sig]
    lu = [x["limit_up"] for x in sig]

    def pct(v, cond): return 100 * sum(1 for x in v if cond(x)) / len(v)

    print(f"\n【T+1 开盘】")
    print(f"  低开占比 {pct(gap, lambda v: v<0):.1f}%  高开占比 {pct(gap, lambda v: v>0):.1f}%  "
          f"均值 {st.mean(gap):+.3f}%")
    print(f"【T+1 盘中】最高曾较T收盘: 均值 {st.mean(intra):+.3f}%  "
          f"盘中涨过T收盘占比 {pct(intra, lambda v: v>0):.1f}%  "
          f"盘中涨≥2%占比 {pct(intra, lambda v: v>=2):.1f}%")
    print(f"【T+1 收盘】均值 {st.mean(close_r):+.3f}%  中位 {st.median(close_r):+.3f}%  "
          f"收盘上涨占比 {pct(close_r, lambda v: v>0):.1f}%  "
          f"涨停占比 {100*sum(lu)/len(lu):.1f}%")
    # PF 代理 + t 检验
    pf = 1 + st.mean(close_r) / 100
    pf_base = 1 + st.mean(base_close) / 100
    z, p = ztest(st.mean(close_r), len(close_r), st.mean(base_close), len(base_close),
                 st.stdev(close_r), st.stdev(base_close))
    print(f"  t1-PF(收盘) = {pf:.4f}  基线 {pf_base:.4f}  相对基线 t={z:+.2f} p={p:.4f}")
    # 左尾
    tail5 = sorted(close_r)[: max(1, len(close_r) // 5)]
    cont_drop = pct(close_r, lambda v: v <= -5)
    ld = pct(close_r, lambda v: v <= -(19.8 if sig[0]["code"].startswith(("300","301","688","689")) else 9.8))
    print(f"【左尾】续跌≥5%占比 {cont_drop:.1f}%  续跌停占比 {ld:.1f}%  "
          f"最差 {min(close_r):+.3f}%  左尾5分位均值 {st.mean(tail5):+.3f}%")

    # 按连跌长度分组看是否越长越反弹
    print(f"\n【按连跌长度分组(收盘收益)】")
    for k in sorted(streak_hist):
        g = [x["close_r"] for x in sig if x["streak"] == k]
        if len(g) >= 5:
            print(f"  {k}日连跌 n={len(g)}: 收盘均值 {st.mean(g):+.3f}%  胜率 "
                  f"{100*sum(1 for x in g if x>0)/len(g):.1f}%")


def ztest(m1, n1, m2, n2, s1, s2):
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0
    se = math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)
    if se == 0:
        return 0.0, 1.0
    z = (m1 - m2) / se
    return z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


if __name__ == "__main__":
    main(7)
