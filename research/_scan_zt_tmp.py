#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描全市场近 ~15 交易日内的「连续 2+ 涨停」票，并标注启动前一日状态。临时诊断。"""
import os, glob, numpy as np
from research.board_limit import board_limit_pct, is_limit_up

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_cache", "daily")

def lp_for(code):
    return board_limit_pct(code)

def load(code):
    p = os.path.join(D, code + ".csv")
    try:
        a = np.loadtxt(p, delimiter=",", dtype=str, skiprows=1)
    except Exception:
        return None
    if a.ndim != 2 or a.shape[0] < 60:
        return None
    try:
        dates = a[:, 0]; cl = a[:, 2].astype(float)
    except Exception:
        return None
    return dates, cl

def main():
    files = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(D, "*.csv"))]
    files = [f for f in files if f[:1].isdigit()]
    hits = []
    for code in files:
        L = load(code)
        if L is None:
            continue
        dates, cl = L
        n = len(cl)
        lp = lp_for(code)
        prev = cl[:-1]
        thr = np.round(prev * (1.0 + lp), 2) - 0.005
        is_zt = cl[1:] >= thr                     # 长度 n-1；is_zt[k] ~ cl[k+1]
        m = len(is_zt)
        if m < 15:
            continue
        w0 = m - 15                                  # 窗口起点（is_zt 全局索引）
        seg = np.concatenate([[False], is_zt[w0:], [False]])
        N = len(seg)
        i = 1
        while i < N - 1:
            if seg[i]:
                j = i
                while seg[j]:
                    j += 1
                if (j - i) >= 2:
                    g0 = w0 + (i - 1)                 # is_zt 全局起点
                    run_start_cli = g0 + 1             # close 索引
                    prev_cli = run_start_cli - 1
                    if prev_cli >= 1:
                        lp_p = lp_for(code)
                        pre_zt = cl[prev_cli] >= round(cl[prev_cli-1]*(1+lp_p),2)-0.005
                        pre_dt = cl[prev_cli] <= round(cl[prev_cli-1]*(1-lp_p),2)+0.005
                        status = "涨停" if pre_zt else ("跌停" if pre_dt else "正常")
                        pre_ret = cl[prev_cli]/cl[prev_cli-1]-1.0 if cl[prev_cli-1]>0 else 0
                        hits.append((code, dates[run_start_cli], j-i, dates[prev_cli], status, round(float(pre_ret)*100,2)))
                i = j
            else:
                i += 1
    seen = set(); uniq = []
    for h in hits:
        k = (h[0], h[1])
        if k in seen: continue
        seen.add(k); uniq.append(h)
    uniq.sort(key=lambda x: (x[1], x[0]), reverse=True)
    print("近15交易日 连续2+涨停 票数: %d" % len(uniq))
    for h in uniq:
        flag = "" if h[4] == "正常" else "  <<< 前一日非'正常'(%s)" % h[4]
        print("%s 启动=%s 连板=%d 前一日=%s(%s) 前收涨跌=%.2f%%%s" % (h[0], h[1], h[2], h[3], h[4], h[5], flag))
    return uniq

if __name__ == "__main__":
    main()
