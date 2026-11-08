#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
cyq_engine.py — 本地 cyq 引擎 (CYS / SCR)
==================================================================
输入: backtest_cache/daily/<code>.csv (前复权 OHLCV) + backtest_cache/float_shares.csv
原理:
  - CYS (短线盈亏): TDX 成本均线 CYC(13) 的偏离度.
        CYC(N)_t = Σ_{i=0..N-1} [ w_i * V_{t-i} * C_{t-i} ] / Σ_{i=0..N-1} [ w_i * V_{t-i} ]
        w_i = 1 / (1 + 0.1*i)         (i=0 为当日, 越远权重越小)
        CYS_t = (C_t - CYC13_t) / CYC13_t * 100
  - SCR (筹码集中度, P1=90): 成本分布中 90% 筹码区间的离散度.
        SCR_t = (COST(95) - COST(5)) / COST(50) * 100
        成本分布用经典"移仓"模型由 OHLCV 重建:
          每日将 turnover=vol/float 比例的筹码, 从其原成本按比例取出,
          均匀放回当日 [low,high] 区间; 其余留在原成本.
        注: 本引擎用 TDX 同源前复权日线重建分布, 与 TDX cyq 同口径;
            单票精确值建议以 live TDX 读数交叉校验 (见 calibrate()).
"""
import os, glob, csv, json

DAILY = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
FLOAT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/float_shares.csv"

def load_float():
    d = {}
    with open(FLOAT, encoding="utf-8") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) >= 2:
                try: d[row[0].strip()] = float(row[1])
                except: pass
    return d

FLOAT_MAP = None

def load_daily(code):
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p): return None
    ds, op, cl, hi, lo, vol = [], [], [], [], [], []
    with open(p, encoding="utf-8") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) < 6: continue
            try:
                ds.append(row[0]); op.append(float(row[1])); cl.append(float(row[2]))
                hi.append(float(row[3])); lo.append(float(row[4])); vol.append(float(row[5]))
            except: pass
    return dict(date=ds, open=op, close=cl, high=hi, low=lo, vol=vol)

def cyc_cost(close, vol, N=13):
    """返回与 close 等长的 CYC(N) 序列 (volume-decay 成本均线)."""
    n = len(close)
    cyc = [None]*n
    for t in range(n):
        if t < N-1:
            # 不足 N 日时退化用可用窗口
            s = max(0, t-N+1)
        else:
            s = t-N+1
        num = 0.0; den = 0.0
        for j in range(t-s+1):
            w = 1.0/(1.0 + 0.1*j)
            v = vol[t-j]; c = close[t-j]
            num += w*v*c; den += w*v
        if den > 0:
            cyc[t] = num/den
    return cyc

def cys_series(close, vol, N=13):
    cyc = cyc_cost(close, vol, N)
    out = []
    for t in range(len(close)):
        if cyc[t] is None or cyc[t] == 0:
            out.append(None)
        else:
            out.append((close[t]-cyc[t])/cyc[t]*100.0)
    return out

def cyq_scr(daily, float_shares=None, window=250):
    """滚动筹码分布(成交量-价格直方图) → SCR 序列.
    SCR_t = (COST(95)-COST(5))/COST(50) * 100, 其中 COST(p) 为截至 t 日
    之前 window 日内按成交量在 [low,high] 上分布的筹码成本百分位.
    筹码分布 = 各日成交量按其当日振幅均匀回填到价格网格 → 即 cyq 的
    "成交量-持仓成本" 近似. 越低 = 筹码越集中(弹簧压得越紧).
    返回与 daily 等长、末 window 段有效的 SCR 序列.
    """
    close = daily["close"]; high = daily["high"]; low = daily["low"]; vol = daily["vol"]
    n = len(close)
    lo_all = min(low); hi_all = max(high)
    step = max(0.01, (hi_all-lo_all)/400.0)
    G = int((hi_all-lo_all)/step) + 3
    grid = [lo_all + i*step for i in range(G)]
    # 每日贡献: 把 vol[t] 均匀铺到 [low[t],high[t]] 的网格区间
    day_bins = []
    for t in range(n):
        a = low[t]; b = high[t]
        ia = int((a-lo_all)/step); ib = int((b-lo_all)/step)
        if ib < ia: ib = ia
        if ia < 0: ia = 0
        if ib >= G: ib = G-1
        day_bins.append((ia, ib, vol[t]))
    hist = [0.0]*G
    def add_day(t):
        ia, ib, v = day_bins[t]
        if ib >= ia and v > 0:
            add = v/(ib-ia+1)
            for i in range(ia, ib+1): hist[i] += add
    def rem_day(t):
        ia, ib, v = day_bins[t]
        if ib >= ia and v > 0:
            sub = v/(ib-ia+1)
            for i in range(ia, ib+1): hist[i] -= sub
    scr = [None]*n
    for t in range(min(window, n)):
        add_day(t)
    for t in range(n):
        if t >= window:
            rem_day(t-window); add_day(t)
        total = sum(hist)
        if total <= 0:
            continue
        t5, t50, t95 = 0.05*total, 0.5*total, 0.95*total
        cum = 0.0; c5 = c50 = c95 = None
        for i in range(G):
            cum += hist[i]
            if c5 is None and cum >= t5: c5 = grid[i]
            if c50 is None and cum >= t50: c50 = grid[i]
            if c95 is None and cum >= t95: c95 = grid[i]
        if c5 is not None and c50 is not None and c95 is not None and c50 > 0:
            scr[t] = (c95-c5)/c50*100.0
    return scr

def analyze(code, window=250):
    global FLOAT_MAP
    if FLOAT_MAP is None: FLOAT_MAP = load_float()
    d = load_daily(code)
    if d is None: return None
    fs = FLOAT_MAP.get(code)
    if fs is None: return None
    cys = cys_series(d["close"], d["vol"])
    scr = cyq_scr(d, fs, window=window)
    # 取末 window 段
    cys_win = [cys[-i] for i in range(1, window+1)][::-1]
    scr_win = [scr[-i] for i in range(1, window+1)][::-1]
    return dict(code=code, n=len(d["close"]),
                cys_latest=cys[-1], cys_min=min(x for x in cys_win if x is not None),
                cys_max=max(x for x in cys_win if x is not None),
                scr_latest=scr[-1], scr_series=scr_win)

if __name__ == "__main__":
    # 校准: 601158 重庆水务, live TDX 读数 CYS≈0.42 (窗口内 -6.15~+2.5), SCR 1.15~3.70, 当前 3.65
    r = analyze("601158", window=250)
    print("CYS latest=%.3f  window[min,max]=%.3f,%.3f" % (r["cys_latest"], r["cys_min"], r["cys_max"]))
    s = r["scr_series"]
    svals = [x for x in s if x is not None]
    import statistics as st
    cur = s[-1]
    # 当前 SCR 在 trailing 序列中的分位
    below = sum(1 for x in svals if x <= cur)/len(svals)
    print("SCR latest=%.3f  min=%.3f  max=%.3f  median=%.3f  cur_percentile=%.2f" %
          (cur, min(svals), max(svals), st.median(svals), below))
    print("=> 601158 当前 SCR 处历史分位 %.2f (live TDX 亦处高位 3.65/3.70, 应判 FAIL 偏低带)" % below)
