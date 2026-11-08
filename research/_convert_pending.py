# -*- coding: utf-8 -*-
"""离线转换 tool-results 里的 tdx_kline txt -> research/raw_minute/{Code}.json
批次安全：只处理本批 27 个 need_minute=1 的 code，绝不写队列外 code。
用法：python _convert_pending.py
"""
import os, json, glob, sys

TOOLRESULTS = r"C:\Users\SGM-AXELD\.workbuddy\projects\e-WorkBuddy_dot_workbuddy-毕方灵犀通达\cc7ffe44-a666-4db5-b976-bd4cdec278e8\tool-results"
RAW_MIN = "E:/WorkBuddy_dot_workbuddy/Claw/research/raw_minute"

# 本批 need_minute=1 的 27 个 code（队列前 40 行）
BATCH_MIN = {
    "688067","688077","688080","688108","688160","688161","688178","688197","688212","688302",
    "688303","688305","688314","688395","688410","688420","688539","688571","688590","688619",
    "688656","688667","688708","688712","688727","688767","688816",
}

def extract_json(text):
    anchor = "详细K线数据:"
    start = text.find(anchor)
    i = text.find("{", start) if start >= 0 else text.find("{")
    if i < 0:
        return None
    depth = 0; in_str = False; esc = False
    for k in range(i, len(text)):
        ch = text[k]
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': in_str = False
            continue
        if ch == '"': in_str = True
        elif ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[i:k+1]
    return None

def main():
    os.makedirs(RAW_MIN, exist_ok=True)
    txts = sorted(glob.glob(os.path.join(TOOLRESULTS, "mcp-tdx-connector-tdx_kline-*.txt")))
    print("found txt files:", len(txts))
    ok = fail = skip = 0
    for t in txts:
        text = open(t, encoding="utf-8", errors="ignore").read()
        js = extract_json(text)
        if not js:
            print("  SKIP no-json:", os.path.basename(t)); fail += 1; continue
        try:
            obj = json.loads(js)
        except Exception as e:
            print("  SKIP parse-fail:", os.path.basename(t), e); fail += 1; continue
        code = obj.get("Code") or obj.get("code")
        if code not in BATCH_MIN:
            print(f"  SKIP out-of-batch {code}"); skip += 1; continue
        rows = obj.get("Rows") or obj.get("rows") or []
        if not rows:
            print(f"  SKIP no-rows {code}"); fail += 1; continue
        out = os.path.join(RAW_MIN, f"{code}.json")
        json.dump(obj, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        ds = [r.get("Data") for r in rows if r.get("Data")]
        print(f"  OK {code}: {len(rows)} bars {min(ds)}~{max(ds)}")
        ok += 1
    print(f"CONVERT SUMMARY: ok={ok} fail={fail} skip={skip}")

if __name__ == "__main__":
    main()
