#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新华传媒 600825 启动前6个月 + 启动日前7个连续交易日 全维度分析（含近似筹码）"""
import csv, os, math, statistics
BASE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(BASE, "..", "backtest_cache", "daily", "600825.csv")
rows = []
with open(P, encoding="utf-8") as f:
    f.readline()
    for ln in f:
        a = ln.strip().split(",")
        if len(a) < 6:
            continue
        try:
            rows.append([a[0], float(a[1]), float(a[2]), float(a[3]), float(a[4]), float(a[5])])
        except Exception:
            pass
idx = {r[0]: i for i, r in enumerate(rows)}
FLOAT = 1.045e9  # 流通股本(股)

start_i = next(i for i, r in enumerate(rows) if r[0] == "2026-09-21")
print("启动日", rows[start_i][0], "收", rows[start_i][2])

# ---- 前7个连续交易日 (start_i 之前 7 个有记录交易日) ----
pre7 = rows[start_i - 7: start_i]
print("\n=== 启动日前7个连续交易日 ===")
print(f"{'日期':12} {'开':>7} {'收':>7} {'高':>7} {'低':>7} {'量(手)':>10} {'换手%':>7}")
for r in pre7:
    d, o, c, h, l, v = r
    turn = v * 100 / FLOAT * 100
    print(f"{d:12} {o:7.2f} {c:7.2f} {h:7.2f} {l:7.2f} {v:10.0f} {turn:7.2f}")

# ---- 前6个月窗口 2026-03-21 ~ 2026-09-20 ----
pre6 = [r for r in rows if "2026-03-21" <= r[0] <= "2026-09-20"]
print(f"\n=== 启动前6个月窗口 (2026-03-21 ~ 2026-09-20) ===")
print(f"交易日数={len(pre6)}  首{pre6[0][0]}收{pre6[0][2]:.2f}  末{pre6[-1][0]}收{pre6[-1][2]:.2f}")
closes = [r[2] for r in pre6]
hi = max(r[3] for r in pre6); lo = min(r[4] for r in pre6)
print(f"最高={hi:.2f} 最低={lo:.2f} 区间振幅={(hi/lo-1)*100:.1f}%")
print(f"窗口首->末涨跌={(pre6[-1][2]/pre6[0][2]-1)*100:+.1f}%")
rets = [pre6[i][2]/pre6[i-1][2]-1 for i in range(1, len(pre6))]
vol = statistics.pstdev(rets) * math.sqrt(242) * 100
print(f"日收益年化波动率≈{vol:.1f}%")
turns = [r[5]*100/FLOAT*100 for r in pre6]
print(f"平均日换手={statistics.mean(turns):.2f}%  最大={max(turns):.2f}%  最小={min(turns):.2f}%")
# 量能趋势: 前3月 vs 后3月 日均量
h1 = pre6[:len(pre6)//2]; h2 = pre6[len(pre6)//2:]
v1 = statistics.mean(r[5]*100/FLOAT*100 for r in h1)
v2 = statistics.mean(r[5]*100/FLOAT*100 for r in h2)
print(f"前半段日均换手={v1:.2f}%  后半段日均换手={v2:.2f}%  量能变化={(v2/v1-1)*100:+.1f}%")
def ma(n):
    return sum(r[2] for r in pre6[-n:])/n if len(pre6) >= n else None
last = pre6[-1][2]
for n in (20, 60, 120, 250):
    m = ma(n)
    if m:
        print(f"MA{n}: 末价{last:.2f} 成本线{m:.2f} 位置={'上方' if last>m else '下方'} 偏离{(last/m-1)*100:+.1f}%")

# ---- 近似筹码获利比 (close vs 过去120日量加权成本 + 年内分位) ----
print("\n=== 近似筹码获利比 (启动前, 基于本地日线) ===")
print("方法: 年内收盘价分位 = 当日close在过往242交易日中的百分位; 120日VWAP为量加权成本")
for r in pre7:
    di = idx[r[0]]
    window = rows[max(0, di-120): di+1]
    vwap = sum(x[2]*x[5] for x in window) / sum(x[5] for x in window)
    yr = [x[2] for x in rows[max(0, di-242): di+1]]
    pct = sum(1 for y in yr if y < r[2]) / len(yr) * 100
    print(f"{r[0]} 收{r[2]:.2f} 120日VWAP={vwap:.2f}({'获利' if r[2]>vwap else '套牢'}) 年内分位={pct:.1f}%")

# ---- 启动前股东户数(中报)已在外部确认: 4.40万户 环比-9.40% ----
print("\n=== 筹码集中度信号(外部已确认) ===")
print("2026中报股东户数=4.40万户, 人均2.38万股, 环比-9.40% (停牌前筹码趋于集中)")
