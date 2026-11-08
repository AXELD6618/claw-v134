# -*- coding: utf-8 -*-
"""
妖股回填 · 收尾脚本（幂等、压缩上下文安全）
==========================================
1) 合并 backfill_done.csv 与所有 backfill_done_chunk*.csv -> backfill_done.csv（按 code 去重，保留最新状态）
2) 队列瘦身：把 backfill_queue.csv 中「已在 backfill_done.csv 出现过的 code」剔除，仅留剩余待处理行
用法：python research/merge_done_and_trim_queue.py
"""
import csv, os, glob

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(PROJ, "research", "backfill_queue.csv")
DONE = os.path.join(PROJ, "research", "backfill_done.csv")


def read_codes_done():
    done = {}  # code -> (status, sector, minute)
    if os.path.exists(DONE):
        with open(DONE, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("code"):
                    done[row["code"]] = (row.get("status", ""), row.get("sector", ""), row.get("minute", ""))
    # 合并 chunk 分片
    for fp in glob.glob(os.path.join(PROJ, "research", "backfill_done_chunk*.csv")):
        with open(fp, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("code"):
                    done[row["code"]] = (row.get("status", ""), row.get("sector", ""), row.get("minute", ""))
    return done


def main():
    done = read_codes_done()
    # 回写合并后的 done
    with open(DONE, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "status", "sector", "minute"])
        for code, (st, se, mi) in sorted(done.items()):
            w.writerow([code, st, se, mi])
    # 队列瘦身
    kept = 0
    removed = 0
    with open(QUEUE, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0] if rows else ["code", "need_sector", "need_minute"]
    body = [r for r in rows[1:] if len(r) >= 1 and r[0].strip()]
    out_body = []
    for r in body:
        if r[0].strip() in done:
            removed += 1
        else:
            out_body.append(r)
            kept += 1
    with open(QUEUE, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(out_body)
    print(f"[merge] done 合并后共 {len(done)} 条")
    print(f"[trim] 队列移除 {removed} 行，保留 {kept} 行")


if __name__ == "__main__":
    main()
