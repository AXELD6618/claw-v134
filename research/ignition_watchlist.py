#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场点火(连续涨停启动)观测表生成器 —— 防御性可见性修复。

根因(已实证)：连续涨停股(贝特利 301697 / 浙文影业 601599)启动前一日不在 654 实时监测宇宙，
结构性不可见；且启动前一日无任何技术性点火信号(外生催化驱动，3.5M 样本回测 REJECT)。

本脚本在 EOD(16:00, 非 14:50 时效关键路径)全市场日线扫描，检测"当日确认开启 >=2 连续涨停"的票
(即 D 为连板第 2 日、D-1 非涨停)，写入 backtest_cache/ignition_watchlist.json，供次日 14:50 流水线
作为候选宇宙叠加层消费 —— 保证任何刚点火的票次日被监测(连板延续/离场由已验证 LIANBAN/HOLD 边缘处理)，
且用户至少能在"连板进行中"收到提示。这是"可见性缺口"的结构性修复。

运行：python research/ignition_watchlist.py --asof 2026-11-05
"""
import os, glob, json, argparse, numpy as np
from research.board_limit import board_limit_pct, is_limit_up

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_cache", "daily")
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_cache", "ignition_watchlist.json")

def lp_for(code):
    return board_limit_pct(code)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="")
    ap.add_argument("--min-board", type=int, default=2, help="连板天数阈值(默认2)")
    a = ap.parse_args()
    files = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(D, "*.csv"))]
    files = [f for f in files if f[:1].isdigit()]
    out = {}
    for code in files:
        try:
            arr = np.loadtxt(os.path.join(D, code + ".csv"), delimiter=",", dtype=str, skiprows=1)
        except Exception:
            continue
        if arr.ndim != 2 or arr.shape[0] < 60:
            continue
        try:
            dates = arr[:, 0]; c = arr[:, 2].astype(float)
        except Exception:
            continue
        lp = lp_for(code)
        prev = c[:-1]
        zt = c[1:] >= (np.round(prev * (1.0 + lp), 2) - 0.005)
        n = len(zt)
        # 连板段扫描
        seg = np.concatenate([[False], zt, [False]])
        i = 1; found = []
        while i < len(seg) - 1:
            if seg[i]:
                j = i
                while seg[j]:
                    j += 1
                run = j - i
                if run >= a.min_board:
                    start_local = i - 1          # zt 局部索引 -> c 索引 = start_local+1
                    run_start_c = start_local + 1
                    found.append((dates[run_start_c], run, dates[run_start_c + run - 1]))
                i = j
            else:
                i += 1
        for sd, run, ed in found:
            if a.asof and ed > a.asof:
                continue
            if a.asof and ed == a.asof:
                # 当日(连板第 min_board 日)确认 → 入次日监测
                out[code] = {"run_start": sd, "board": int(run), "confirmed_on": ed, "asof": a.asof}
    # 清理过期：仅保留 confirmed_on == asof 的当日新确认项
    fresh = {k: v for k, v in out.items() if (not a.asof) or v["confirmed_on"] == a.asof}
    json.dump(fresh, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("asof=%s 新确认点火(>=%d连板)票数=%d → %s" % (a.asof or "ALL", a.min_board, len(fresh), OUT))
    if fresh:
        for k, v in list(fresh.items())[:20]:
            print("  %s 启动=%s 连板=%d 确认日=%s" % (k, v["run_start"], v["board"], v["confirmed_on"]))
    return len(fresh)

if __name__ == "__main__":
    main()
