# -*- coding: utf-8 -*-
"""
Step 4: 消费队列前 40 行 -> 追加 backfill_done.csv，并重写队列只留剩余行。
"""
import os

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(PROJ, "research", "backfill_queue.csv")
DONE = os.path.join(PROJ, "research", "backfill_done.csv")
BATCH = 40

# 读队列
with open(QUEUE, encoding="utf-8") as f:
    lines = [ln.rstrip("\n") for ln in f if ln.strip() != ""]
header = lines[0]
data = lines[1:]

n_before = len(data)
batch = data[:BATCH]
rest = data[BATCH:]
n_after = len(rest)

assert n_after < n_before, f"n_after({n_after}) >= n_before({n_before})，停止！"

# 构建 done 行（全部成功）
done_rows = []
for row in batch:
    parts = row.split(",")
    code = parts[0]
    need_sector = parts[1] if len(parts) > 1 else "1"
    need_minute = parts[2] if len(parts) > 2 else "0"
    sector = "1" if need_sector == "1" else "0"
    minute = "1" if need_minute == "1" else "0"
    # 本批全部抓取成功
    status = "ok"
    done_rows.append(f"{code},{status},{sector},{minute}")

# 写回队列（表头 + 剩余）
with open(QUEUE, "w", encoding="utf-8") as f:
    f.write(header + "\n")
    for r in rest:
        f.write(r + "\n")
    f.write("")  # 末尾不强制空行

# 追加 done（文件可能已有内容，无表头则补表头）
write_header = not os.path.exists(DONE) or os.path.getsize(DONE) == 0
with open(DONE, "a", encoding="utf-8") as f:
    if write_header:
        f.write("code,status,sector,minute\n")
    for r in done_rows:
        f.write(r + "\n")

print(f"[step4] n_before={n_before} n_after={n_after} 本批消费 {n_before - n_after} 行")
print(f"[step4] 追加 done 行数={len(done_rows)} （status=ok 全部）")
print(f"[step4] 队列剩余数据行={n_after}")
