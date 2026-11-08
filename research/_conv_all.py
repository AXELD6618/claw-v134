# -*- coding: utf-8 -*-
"""
分钟线落盘批量转换器（离线读 tool-results 之外的 txt，写入 research/raw_minute）。
仅处理当前 backfill_queue.csv 中出现的 code，避免把无关会话的 kline txt 误转。
用法：python research/_conv_all.py
"""
import json, os, glob

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJ, "research", "raw_minute")
QUEUE = os.path.join(PROJ, "research", "backfill_queue.csv")
BASE = r"C:\Users\SGM-AXELD\.workbuddy\projects\e-WorkBuddy_dot_workbuddy-毕方灵犀通达\063146c9-09c1-4065-aa13-38cd44428cd6\tool-results"


def load_queue_codes():
    codes = set()
    if os.path.exists(QUEUE):
        with open(QUEUE, encoding="utf-8") as f:
            next(f, None)  # 表头
            for line in f:
                c = line.split(",")[0].strip()
                if c:
                    codes.add(c)
    return codes


def extract_json(text):
    anchor = "详细K线数据:"
    start = text.find(anchor)
    i = text.find("{", start) if start >= 0 else text.find("{")
    if i < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for k in range(i, len(text)):
        ch = text[k]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    return None


def main():
    os.makedirs(RAW, exist_ok=True)
    qcodes = load_queue_codes()
    txts = glob.glob(os.path.join(BASE, "*tdx_kline*.txt"))
    print(f"[conv_all] queue codes={len(qcodes)}; txt files={len(txts)}")
    n_new = 0
    n_skip = 0
    n_ignore = 0
    for fp in sorted(txts):
        try:
            text = open(fp, encoding="utf-8", errors="ignore").read()
        except Exception as e:
            print(f"  skip {os.path.basename(fp)}: {e}")
            continue
        js = extract_json(text)
        if not js:
            print(f"  no-json {os.path.basename(fp)}")
            continue
        try:
            obj = json.loads(js)
        except Exception as e:
            print(f"  parse-fail {os.path.basename(fp)}: {e}")
            continue
        code = str(obj.get("Code") or obj.get("code") or "")
        if not code:
            print(f"  no-code {os.path.basename(fp)}")
            continue
        if code not in qcodes:
            n_ignore += 1
            continue
        out = os.path.join(RAW, f"{code}.json")
        if os.path.exists(out):
            n_skip += 1
            continue
        json.dump(obj, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        rows = obj.get("Rows") or []
        ds = [r.get("Data") for r in rows if r.get("Data")]
        daterange = f"{min(ds)}~{max(ds)}" if ds else "?"
        print(f"  wrote {code}: {len(rows)} bars {daterange}")
        n_new += 1
    print(f"[conv_all] new={n_new} skip_exists={n_skip} ignored_nonqueue={n_ignore}")


if __name__ == "__main__":
    main()
