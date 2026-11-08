#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V15 战法 · 真实 OOS 验证器（修掉 V15_Overnight.backtest 漏算量比的根因 bug）

问题: V15_Overnight.feats 不计算 vr(量比)，导致 pass_v15 的 vol_ratio_min 检查
     对历史样本永远 False，注释里的「放量1.5x → 净+1.766%」从未被自身回测验证过。
     本脚本补上 vr，并同时给出两种口径收益，用与主系统同一套铁律验收(PF/t/左尾)。

口径:
  buy = T 日收盘; sell1 = T+1 日开盘   → 字面策略收益 ret_open
  sell2 = 捕捉 T+1 开盘→最高 50%      → 理想化收益 ret_cap50 (V15 报告"命中率"用的就是这个)
  vr = T 日成交量 / 前 20 交易日平均成交量
"""
import os, json, math, statistics as st

BASE = os.path.dirname(os.path.abspath(__file__))
KL = json.load(open(os.path.join(BASE, "..", "data", "v74_klines_parsed.json"),
                    encoding="utf-8"))["stocks"]


def thr_of(code):
    return 19.8 if code.startswith(("300", "301", "688", "689")) else 9.8


def build_samples():
    out = []
    for code, d in KL.items():
        bars = d.get("bars") or []
        if len(bars) < 40:
            continue
        for i in range(31, len(bars) - 1):
            b, p = bars[i], bars[i - 1]
            if not (b["close"] and p["close"]):
                continue
            # 量比
            hist = [x["volume"] for x in bars[max(0, i - 20):i] if x.get("volume")]
            vavg = sum(hist) / len(hist) if hist else 0
            vr = (b["volume"] / vavg) if vavg else 0
            chg = (b["close"] / p["close"] - 1) * 100
            had_limit = any(
                (bars[j]["close"] / bars[j - 1]["close"] - 1) * 100 >= thr_of(code)
                for j in range(max(1, i - 30), i)
                if bars[j - 1]["close"] and bars[j]["close"])
            nxt = bars[i + 1]
            if not (nxt["open"] and b["close"]):
                continue
            ret_open = (nxt["open"] / b["close"] - 1) * 100
            ret_cap50 = ((0.5 * nxt["open"] + 0.5 * nxt["high"]) / b["close"] - 1) * 100
            out.append({
                "code": code, "date": b["date"],
                "chg": chg, "vr": vr, "new_high": b["close"] >= b["high"] * 0.995,
                "had_limit_30": had_limit,
                "ret_open": ret_open, "ret_cap50": ret_cap50,
            })
    return out


def ztest(m1, n1, m2, n2, s1, s2):
    if n1 < 2 or n2 < 2:
        return 0.0, 1.0
    se = math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)
    if se == 0:
        return 0.0, 1.0
    z = (m1 - m2) / se
    return z, 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def pass_v15(f, vr_min=1.5, chg_lo=2.0, chg_hi=7.0):
    if not f["new_high"]:
        return False
    if f["vr"] < vr_min:
        return False
    if not (chg_lo <= f["chg"] <= chg_hi):
        return False
    return True


def report(samples, name, sel):
    base = [x["ret_open"] for x in samples]
    if not sel:
        print(f"{name}: 无样本"); return
    ro = [x["ret_open"] for x in sel]
    rc = [x["ret_cap50"] for x in sel]
    z_o, p_o = ztest(st.mean(ro), len(ro), st.mean(base), len(base), st.stdev(ro), st.stdev(base))
    tail = sorted(ro)[: max(1, len(ro) // 5)]
    # PF vs 等权随机(基准均值)
    pf = (1 + st.mean(ro) / 100)
    pf_base = (1 + st.mean(base) / 100)
    print(f"\n【{name}】 n={len(sel)}")
    print(f"  字面(收盘买→次日开盘卖): 均值 {st.mean(ro):+.3f}%  中位 {st.median(ro):+.3f}%  "
          f"胜率 {100*sum(1 for x in ro if x>0)/len(ro):.1f}%  t1-PF {pf:.4f} (基准 {pf_base:.4f})")
    print(f"  理想(捕捉开盘→最高50%):   均值 {st.mean(rc):+.3f}%  胜率 {100*sum(1 for x in rc if x>0)/len(rc):.1f}%")
    print(f"  vs 基准 t={z_o:+.2f} p={p_o:.4f}  → {'★显著' if p_o<0.05 and z_o>0 else '不显著/负'}")
    print(f"  左尾5分位均值 {st.mean(tail):+.3f}%  最差 {min(ro):+.3f}%")


def main():
    S = build_samples()
    base = [x["ret_open"] for x in S]
    print("=" * 92)
    print("V15 真实 OOS 验证 · 54只×200日真实前复权日线 · 含量比(vr)修正")
    print("=" * 92)
    print(f"全样本(尾盘买→次日开盘卖) n={len(S)} 均值 {st.mean(base):+.3f}%  "
          f"中位 {st.median(base):+.3f}% 胜率 {100*sum(1 for x in base if x>0)/len(base):.1f}%")

    report(S, "H6 尾盘创新高 单独", [x for x in S if x["new_high"]])
    report(S, "H6 + 放量>=1.5", [x for x in S if pass_v15(x)])
    report(S, "H6 + 放量>=1.5 + 30日内有涨停",
           [x for x in S if pass_v15(x) and x["had_limit_30"]])
    report(S, "H6 + 放量>=1.5 + 涨幅2-7% + 30日内涨停 (全优化)",
           [x for x in S if pass_v15(x) and x["had_limit_30"]])


if __name__ == "__main__":
    main()
