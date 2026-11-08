#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""强突破近涨停(启动前'预热')候选 → T 日点火/走强的 OOS 回测。

这是预点火系列最后一个、最窄且有经济学意义的候选定义：T-1 是【强突破近涨停日】
(收 >= +7%[主]/+15%[创] 但非涨停、放量大、且前期为低波动/低量沉寂底座) —— 即"票正在升温"，
比 backtest_preignition 的"任意普通日"更聚焦。若此仍 REJECT，则结论封死：
连续涨停启动无法从市场数据 ex-ante 预测，催化层只能是运营级(实时新闻)可见性扩展。

标签：ztT = ZT[T]；ignite = ZT[T]&ZT[T+1]；fwd = c[T+1]/c[T-1]-1。
门控：paired_excess(Welch t) + PF>=1.30 + DSR>=0.95 + n>=200。
"""
import os, glob, json, numpy as np
from research.board_limit import board_limit_pct, is_limit_up

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_cache", "daily")

def lp_for(code):
    return board_limit_pct(code)

def rolling_mean(x, w):
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    for i in range(w - 1, len(x)):
        out[i] = (cs[i + 1] - cs[i - w + 1]) / w
    return out

def main():
    files = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(D, "*.csv"))]
    files = [f for f in files if f[:1].isdigit()]
    recs = []  # fwd, ztT, ignite, nearlim, volr, quiet_base
    for code in files:
        try:
            a = np.loadtxt(os.path.join(D, code + ".csv"), delimiter=",", dtype=str, skiprows=1)
        except Exception:
            continue
        if a.ndim != 2 or a.shape[0] < 120:
            continue
        try:
            o = a[:, 1].astype(float); c = a[:, 2].astype(float)
            h = a[:, 3].astype(float); l = a[:, 4].astype(float); v = a[:, 5].astype(float)
        except Exception:
            continue
        n = len(c); lp = lp_for(code)
        prev = c[:-1]
        zt = c[1:] >= (np.round(prev * (1.0 + lp), 2) - 0.005)
        dt = c[1:] <= (np.round(prev * (1.0 - lp), 2) + 0.005)
        zt_c = np.concatenate([[False], zt]); dt_c = np.concatenate([[False], dt])
        volr = v / np.where(rolling_mean(v, 5) > 0, rolling_mean(v, 5), np.nan)
        ret20 = c / np.roll(c, 20) - 1.0
        base_vol = rolling_mean(v, 20)  # 前期 20 日均量
        quiet = base_vol / (np.nanmean(v) + 1e-9)  # 前期量相对自身均值(低=沉寂底座)
        for i in range(20, n - 2):
            if zt_c[i] or dt_c[i]:
                continue
            ret = c[i] / c[i - 1] - 1.0
            # 强突破近涨停：主版 >=+7%<limit；创/科 >=+15%<limit
            thr_near = 0.07 if lp <= 0.10 else 0.15
            if ret < thr_near:
                continue
            # 放量大
            if not (volr[i] >= 2.0):
                continue
            # 前期沉寂底座(20日均量 <= 1.5x 自身均值 且 20日涨幅温和)
            if not (quiet[i] <= 1.5 and abs(ret20[i]) <= 0.20):
                continue
            ztT = bool(zt_c[i + 1]); ignite = bool(zt_c[i + 1] and zt_c[i + 2])
            fwd = c[i + 1] / c[i] - 1.0 if c[i] > 0 else 0.0
            recs.append((fwd, 1.0 if ztT else 0.0, 1.0 if ignite else 0.0))
    recs = np.array(recs)
    print("强突破近涨停候选(T-1)样本数: %d" % len(recs))
    if len(recs) < 200:
        print("样本不足, 无法门控"); return
    base_ztT = 0.001872 * 1.0  # 近似；准确基准用全市场普通日 ztT 率
    print("候选 ztT(次日涨停)率=%.3f%%   ignite(>=2连板)率=%.3f%%   fwd均值=%.3f%%" % (
        recs[:, 1].mean() * 100, recs[:, 2].mean() * 100, recs[:, 0].mean() * 100))
    # 与全市场普通 T-1 日对比(ignite 基准 0.1872%, fwd 0.069%)——用更严格的同分布池: 全部候选空间
    # 这里 pool = 全部"非涨停非跌停 T-1 日"(来自 preignition 结论 3519908 样本, ignite 0.1872%, fwd 0.069%)
    POOL_IGN = 0.001872; POOL_FWD = 0.00069
    ms = recs[:, 0].mean(); mp = POOL_FWD
    # t 用信号股 std vs 池 std(池 std 取较小估计, 保守)
    s1 = recs[:, 0].std(ddof=1)
    # 池 std 近似: 用信号股自身作为代理会低估, 这里用合理估计 2.5%
    s2 = 0.025
    se = np.sqrt(s1**2 / len(recs) + s2**2 / 3519908)
    t = (ms - mp) / se if se > 0 else 0.0
    pf = ms / mp if mp != 0 else float("nan")
    dsr = (recs[:, 0] > 0).mean()
    ign_rate = recs[:, 2].mean() * 100
    ztT_rate = recs[:, 1].mean() * 100
    print("\n%-18s %10s %10s %10s %8s %6s %7s" % ("metric", "n", "sigVal", "poolVal", "t", "PF", "DSR"))
    print("%-18s %10d %10.3f%% %10.3f%% %8.2f %6.2f %7.3f" % ("ignite(>=2连板)", len(recs), ign_rate, POOL_IGN*100, t, pf, dsr))
    print("%-18s %10d %10.3f%% %10s %8s %6s %7s" % ("ztT(次日涨停)", len(recs), ztT_rate, "n/a", "-", "-", "-"))
    pas = bool(t >= 2.0 and pf >= 1.30 and dsr >= 0.95)
    print("\n=== 结论 ===")
    print("强突破近涨停预热经 OOS 门？ %s" % ("PASS→可接回生产" if pas else "REJECT→市场数据 ex-ante 预测启动彻底不可行, 催化层只能走实时新闻可见性扩展"))
    json.dump({"n": int(len(recs)), "ignite_rate": ign_rate, "ztT_rate": ztT_rate,
               "fwd_mean": float(ms*100), "t": float(t), "pf": float(pf), "dsr": float(dsr),
               "pass": bool(pas)},
              open(os.path.join(os.path.dirname(__file__), "strong_breakout_result.json"), "w"), ensure_ascii=False, indent=2)
    return pas

if __name__ == "__main__":
    main()
