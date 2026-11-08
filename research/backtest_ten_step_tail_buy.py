#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十步尾盘买入法 · OOS 回测（科学严谨评估，非投资建议）

用户原始 10 步：
  1. 看大盘选时：短期反弹1层/中期上升3层/长期上升7层或满仓
  2. 筛涨幅：13:30 看涨幅榜，只留涨幅 3%—5%
  3. 筛量比：剔除量比<1（保留有资金关注）
  4. 筛换手率：剔除换手率<5% 或 >10%
  5. 筛市值：只留流通市值 50亿—200亿 中盘
  6. 看成交量：剔除忽高忽低，保留温和放量
  7. 看K线：剔除高位长上影、上方压力明显
  8. 看分时强弱：全天运行在分时均价线上方，且强于大盘
  9. 等买点：14:30 左右创当日新高后回踩，均价线不破、重新向上介入
  10. 次日卖出：高/平/低开上涨，跌破分时均价线和趋势线离场；开盘走弱跌破前低止损
核心：尾盘找强势股，次日早盘兑现。

==================================================================
数据可用性标注（决定本方法在本系统能否落地 / 本代理回测能测什么）
==================================================================
  步骤 | 可日线代理            | 需 TDX 盘中/StatInfo
  -----+-----------------------+--------------------------
   1   | 个股 ma20>ma60(代理)  | 全市场宽度趋势(已有 regime gate)
   2   | EOD 收盘涨幅∈[3%,5%]  | 13:30 实时涨幅快照
   3   | vo/MA5(vo)>1(代理)    | 真实量比(StatInfo)
   4   | —— 日线无 ——         | 换手率(StatInfo)
   5   | —— 日线无 ——         | 流通市值(StatInfo/floatcap)
   6   | vo>前日 且 vo>MA5(vo) | 日内量能节奏
   7   | 高位长上影比(日线)    | 上方压力(形态/筹码)
   8   | —— 日线无 ——         | 分时均价线+强于大盘(分钟)
   9   | —— 日线无 ——         | 14:30 新高回踩均价线(分钟)
  10   | 次日开盘/收盘收益     | 分时跌破均价线/趋势线离场(分钟)

→ 本代理回测只测【日线可算的壳】：步骤1(代理)+2(代理)+3(代理)+6(代理)+7 + 次日收益(10代理)。
  步骤4/5/8/9 是该方法真正的"强势/择时"内核，日线无法复刻——代理结果只是【地板值】，
  不是对完整方法的判决。完整判决必须经 TDX 盘中数据走系统 OOS 配对超额门控
  (n_test≥200 ∧ 命中≥1.15 ∧ PF≥1.30 ∧ 均值间隙>20bp ∧ t≥2.0)。

信号(日线代理壳) @ 锚定日 T（尾盘≈T 收盘买入，次日兑现）：
  S1 中期上升(代理): ma20[T] > ma60[T]
  S2 收盘涨幅∈[3%,5%]: 0.03 <= r_T <= 0.05
  S3 量比代理>1: vo[T] / MA5(vo)[T] > 1.0
  S6 温和放量: vo[T] > vo[T-1] 且 vo[T] > MA5(vo)[T]
  S7 非高位长上影: hi[T]>=0.95*HH20 且 上影比>0.4 → 剔除
  入场 = T 收盘；收益：next_open=op[T+1]/cl[T]-1；next_close=cl[T+1]/cl[T]-1；h5=cl[T+5]/cl[T]-1
  配对基准：逐笔按 horizon H 配对随机同宇宙同 H 收益（真配对）。
  判决：paired_excess>0 ∧ |t|≥2.0 ∧ PF>1.0 → EDGE_CANDIDATE；否则 REJECTED。
"""
import os, sys, json, glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
D = os.path.join(ROOT, "backtest_cache", "daily")

def sma(x, n):
    if len(x) < n:
        return np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = (c[n:] - c[:-n]) / n
    return np.concatenate([np.full(n - 1, np.nan), out])

def preload():
    files = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(D, "*.csv"))]
    files = [f for f in files if f[:1].isdigit()]
    stocks = []
    for code in files:
        p = os.path.join(D, code + ".csv")
        try:
            arr = np.loadtxt(p, delimiter=",", dtype=str, skiprows=1)
        except Exception:
            continue
        if arr.ndim != 2 or arr.shape[0] < 71:
            continue
        try:
            op = arr[:, 1].astype(float); cl = arr[:, 2].astype(float)
            hi = arr[:, 3].astype(float); lo = arr[:, 4].astype(float); vo = arr[:, 5].astype(float)
        except Exception:
            continue
        if np.any(cl <= 0):
            continue
        stocks.append((code, op, cl, hi, lo, vo))
    return stocks

def run():
    stocks = preload()
    print("[TS] stocks=%d" % len(stocks))

    nx_open, nx_close, h5 = [], [], []
    sig_days = 0

    for code, op, cl, hi, lo, vo in stocks:
        n = len(cl)
        ma20 = sma(cl, 20); ma60 = sma(cl, 60); ma5v = sma(vo, 5)
        r = np.concatenate([[np.nan], cl[1:] / cl[:-1] - 1.0])
        hh20 = pd.Series(hi).rolling(20).max().values
        up_shadow = (hi - np.maximum(op, cl)) / (hi - lo + 1e-9)
        near_high = hi >= 0.95 * hh20
        s1 = ma20 > ma60
        s2 = (r >= 0.03) & (r <= 0.05)
        s3 = vo / ma5v > 1.0
        s6 = (vo > np.concatenate([[np.nan], vo[:-1]])) & (vo > ma5v)
        s7_bad = near_high & (up_shadow > 0.4)
        sig = s1 & s2 & s3 & s6 & (~s7_bad) & (~np.isnan(ma60)) & (ma20 > 0) & (ma5v > 0)
        idx = np.where(sig)[0]
        idx = idx[(idx >= 20) & (idx < n - 6)]
        for i in idx:
            entry = cl[i]
            if entry <= 0:
                continue
            sig_days += 1
            nx_open.append(op[i+1] / entry - 1.0)
            nx_close.append(cl[i+1] / entry - 1.0)
            if i + 5 < n:
                h5.append(cl[i+5] / entry - 1.0)

    def stats(v):
        v = np.asarray(v, dtype=float)
        if len(v) == 0:
            return dict(n=0)
        pos = v[v > 0]; neg = v[v < 0]
        pf = (pos.sum() / abs(neg.sum())) if neg.sum() != 0 else float('inf')
        return dict(n=len(v), win=float(len(pos)/len(v)), mean=float(v.mean()),
                    median=float(np.median(v)), pf=float(pf))

    s_o, s_c, s5 = stats(nx_open), stats(nx_close), stats(h5)
    out = {"strategy": "十步尾盘买入法(日线代理壳,缺4/5/8/9)",
           "signal_days": sig_days,
           "next_open": s_o, "next_close": s_c, "hold5": s5}

    if sig_days > 0:
        rng = np.random.default_rng(20261105)
        pool = [(op, cl, len(cl)) for (_, op, cl, _, _, _) in stocks]
        def paired(buf, H):
            nb = len(buf)
            rand = np.zeros(nb)
            for k in range(nb):
                ok = False
                for _a in range(50):
                    c2 = pool[rng.integers(0, len(pool))]
                    op2, cl2, m2 = c2
                    if m2 <= 71 + H:
                        continue
                    d2 = rng.integers(70, m2 - H)   # d2<=m2-H-1 ⇒ d2+1+H<=m2-1
                    e2 = op2[d2 + 1] if H == 1 else cl2[d2 + 1]
                    if e2 > 0 and d2 + 1 + H < m2:
                        rand[k] = cl2[d2 + 1 + H] / e2 - 1.0; ok = True; break
                if not ok:
                    rand[k] = 0.0
            sig = np.array(buf, dtype=float)
            pv = sig - rand
            pe = pv.mean(); se = pv.std(ddof=1)/np.sqrt(len(pv))
            t = pe/se if se > 0 else 0.0
            edge = (pe > 0) and (abs(t) >= 2.0) and (stats(buf).get("pf", 0) > 1.0)
            return dict(random_mean=float(rand.mean()), paired_excess=float(pe),
                        paired_t=float(t), verdict="EDGE_CANDIDATE" if edge else "REJECTED")
        out["paired_next_open"] = paired(nx_open, 1)
        out["paired_next_close"] = paired(nx_close, 1)
        out["paired_hold5"] = paired(h5, 5)

    print(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    with open(os.path.join(HERE, "ten_step_tail_buy_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)

if __name__ == "__main__":
    run()
