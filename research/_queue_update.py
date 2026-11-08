# -*- coding: utf-8 -*-
"""
第 4 步：把本批已处理的前 40 行从队列移除，并把 40 个 code 追加到 backfill_done.csv。
sector/minute 标志来自队列原始 need_sector/need_minute 列（1=需且成功，0=未请求）。
本批 40 行全部转换成功，故 status 统一 ok。
"""
import os

Q = r"E:\WorkBuddy_dot_workbuddy\Claw\research\backfill_queue.csv"
DONE = r"E:\WorkBuddy_dot_workbuddy\Claw\research\backfill_done.csv"
BATCH = 40

with open(Q, encoding="utf-8") as f:
    raw = [l.rstrip("\n") for l in f]
# 过滤空行，但保留表头
nonempty = [l for l in raw if l.strip() != ""]
header = nonempty[0]
data = nonempty[1:]
n_before = len(data)

processed = data[:BATCH]
remaining = data[BATCH:]

# 重写队列：仅留剩余行
with open(Q, "w", encoding="utf-8", newline="") as f:
    f.write(header + "\n")
    for r in remaining:
        f.write(r + "\n")

# 追加到 backfill_done（保留既有内容，仅追加）
with open(DONE, "a", encoding="utf-8", newline="") as f:
    for r in processed:
        parts = r.split(",")
        code = parts[0]
        ns = parts[1] if len(parts) > 1 else "0"
        nm = parts[2] if len(parts) > 2 else "0"
        sector_flag = 1 if ns == "1" else 0
        minute_flag = 1 if nm == "1" else 0
        f.write(f"{code},ok,{sector_flag},{minute_flag}\n")

print(f"[queue] n_before={n_before} processed={len(processed)} remaining={len(remaining)}")
print(f"[queue] consumed={n_before - len(remaining)}")
sector_codes = [r.split(',')[0] for r in processed if r.split(',')[1] == '1']
print(f"[queue] sector_requested={len(sector_codes)} -> {sector_codes}")
