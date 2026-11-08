#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""新华传媒 600825 连续涨停序列 + 关键日定位（本地日线，前复权）"""
import csv, os
BASE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(BASE, "..", "backtest_cache", "daily", "600825.csv")
rows = []
with open(P, encoding="utf-8") as f:
    hdr = f.readline().strip()
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        a = ln.split(",")
        if len(a) < 6:
            continue
        try:
            rows.append([a[0], float(a[1]), float(a[2]), float(a[3]), float(a[4]), float(a[5])])
        except Exception:
            pass
print("字段:", hdr)
print("总行数", len(rows), "首", rows[0][0], "尾", rows[-1][0])

# 连板段（主板涨停≈前收*1.10，用 >=9.5% 容差；T日未跌停且收盘≈最高更稳，这里用涨幅阈值）
segs, cur, prev = [], [], None
gap_broken = False
for i, r in enumerate(rows):
    d, o, c, h, l, v = r
    pct = (c / prev - 1) if prev else None
    # 交易日历缺口>7天视为断档，连板计数也断开
    if prev is not None and i > 0:
        pd = int(rows[i-1][0].replace("-", "")); cd = int(d.replace("-", ""))
        if cd - pd > 7:
            gap_broken = True
    zt = (pct is not None and pct >= 0.095)
    if zt:
        cur.append((d, round(pct*100, 2), v, round(c, 2)))
    else:
        if cur:
            segs.append(cur)
        cur = []
    prev = c
if cur:
    segs.append(cur)

print("\n=== 连续涨停段(>=3板) ===")
for s in segs:
    if len(s) >= 3:
        print(f"{s[0][0]} ~ {s[-1][0]}  连板数={len(s)}  首板涨幅={s[0][1]}%  末收={s[-1][3]}")

# 2026-10 全量
print("\n=== 2026-10 逐日 ===")
prev = None
for r in rows:
    if r[0] >= "2026-10-01":
        d, o, c, h, l, v = r
        pct = (c/prev-1)*100 if prev else 0.0
        zt = "ZT" if pct >= 9.5 else ""
        print(f"{d} 开{o:.2f} 收{c:.2f} 高{h:.2f} 低{l:.2f} 量{v:.0f}  {pct:+.2f}% {zt}")
    prev = r[2]

# 19日定位
print("\n=== 2026-10-19 上下文 ===")
prev = None
for i, r in enumerate(rows):
    if r[0] in ("2026-10-16", "2026-10-19", "2026-10-20"):
        d, o, c, h, l, v = r
        pct = (c/prev-1)*100 if prev else 0.0
        print(f"{d} 开{o:.2f} 收{c:.2f} 高{h:.2f} 低{l:.2f} 量{v:.0f}  {pct:+.2f}%")
    prev = r[2]
