import os

RESEARCH = r"E:/WorkBuddy_dot_workbuddy/Claw/research"
QUEUE = os.path.join(RESEARCH, "backfill_queue.csv")
DONE = os.path.join(RESEARCH, "backfill_done.csv")
RAW_SECTOR = os.path.join(RESEARCH, "raw_sectors")
RAW_MINUTE = os.path.join(RESEARCH, "raw_minute")

with open(QUEUE, encoding="utf-8") as f:
    lines = f.read().splitlines()

header = lines[0]
data = [l for l in lines[1:] if l.strip()]
n_before = len(data)

BATCH_N = 40
removed = data[:BATCH_N]
remaining = data[BATCH_N:]

ok = fail = 0
with open(DONE, "a", encoding="utf-8", newline="") as f:
    for row in removed:
        parts = row.split(",")
        code = parts[0].strip()
        has_sec = os.path.isfile(os.path.join(RAW_SECTOR, code + ".json"))
        has_min = os.path.isfile(os.path.join(RAW_MINUTE, code + ".json"))
        status = "ok" if (has_sec and has_min) else "fail"
        if status == "ok":
            ok += 1
        else:
            fail += 1
        f.write(f"{code},{status},1,1\n")

with open(QUEUE, "w", encoding="utf-8", newline="") as f:
    f.write(header + "\n")
    if remaining:
        f.write("\n".join(remaining) + "\n")

print(f"n_before={n_before} consumed={len(removed)} remaining={len(remaining)} ok={ok} fail={fail}")
