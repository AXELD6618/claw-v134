# -*- coding: utf-8 -*-
"""临时回填脚本：修复 600036 日线缺口（10-19 坏行 + 10-20~10-30 缺失）。
数据来源：mcp__tdx-connector__tdx_kline(600036, setcode=1, period=4, wantNum=30) 真实返回。
CSV 列序：日期,开盘,收盘,最高,最低,成交量（与 refresh_daily.row_to_line 一致，量为手）。
"""
import os
P = os.path.join("backtest_cache", "daily", "600036.csv")

# TDX 真实行 (Date, Open, High, Low, Close, Volume手, RawVolume股)
TDX = {
    "2026-10-19": (42.30, 43.05, 43.16, 42.15, 732049.76),
    "2026-10-20": (42.95, 42.52, 43.13, 42.18, 760948.48),
    "2026-10-21": (42.60, 42.28, 42.60, 42.00, 748180.24),
    "2026-10-22": (41.87, 41.82, 42.30, 41.45, 989244.00),
    "2026-10-23": (41.70, 41.77, 42.38, 41.42, 1232173.68),
    "2026-10-26": (41.49, 42.50, 42.50, 41.43, 951382.88),
    "2026-10-27": (42.30, 42.99, 43.06, 42.04, 867283.04),
    "2026-10-28": (42.51, 42.13, 42.68, 41.85, 1077898.96),
    "2026-10-29": (42.50, 42.94, 43.03, 42.09, 1087494.48),
    "2026-10-30": (43.29, 43.55, 43.55, 42.80, 1027158.16),
}

lines = open(P, encoding="utf-8-sig").read().splitlines()
header = lines[0]
rows = lines[1:]
by_date = {}
order = []
for ln in rows:
    a = ln.split(",")
    by_date[a[0]] = a
    order.append(a[0])

before_last = order[-1]
fixed = []
appended = []
# 修 10-19 坏行（签名：开=高=低=收 且 量极小）
for d, (o, c, h, l, v) in TDX.items():
    if d in by_date:
        old = by_date[d]
        is_stub = (old[1] == old[2] == old[3] == old[4])  # 四价相等=占位坏行
        if is_stub or abs(float(old[1]) - o) > 0.5:  # 开盘价都对不上=损坏
            by_date[d] = [d, f"{o:.2f}", f"{c:.2f}", f"{h:.2f}", f"{l:.2f}", f"{v:.2f}"]
            fixed.append(d)
    else:
        by_date[d] = [d, f"{o:.2f}", f"{c:.2f}", f"{h:.2f}", f"{l:.2f}", f"{v:.2f}"]
        order.append(d)
        appended.append(d)

# 按日期排序重写
order = sorted(set(order))
out = [header] + [",".join(by_date[d]) for d in order]
open(P, "w", encoding="utf-8").write("\n".join(out) + "\n")

print("[backfill] 修复坏行:", fixed)
print("[backfill] 追加行:", appended)
print("[backfill] 末行(修复前):", before_last)
print("[backfill] 末行(修复后):", order[-1])
print("[backfill] 总行数:", len(out) - 1)
# 校验：末 3 行
print("[backfill] 末 3 行:")
for d in order[-3:]:
    print("   ", ",".join(by_date[d]))
