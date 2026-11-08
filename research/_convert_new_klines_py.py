# -*- coding: utf-8 -*-
"""
幂等批量转换 tool-results 中 tdx_kline 落盘 txt -> research/raw_minute/{code}.json
用 Python 正则提取 code（先【名称】300XXX，再 "Code":"300XXX"），避免 grep 多字节否定字符类不稳。
已存在的 {code}.json 跳过。
"""
import json, os, re, glob, sys

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
RESEARCH = os.path.join(PROJ, "research")
BASE = r"C:/Users/SGM-AXELD/.workbuddy/projects/e-WorkBuddy_dot_workbuddy-毕方灵犀通达/70577520-07fb-40b6-82c9-d65629215046/tool-results"
RAW = os.path.join(RESEARCH, "raw_minute")
os.makedirs(RAW, exist_ok=True)

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
                return text[i:k+1]
    return None

def get_code(text):
    m = re.search(r'【([^】】]*)】(\d{6})', text)
    if m:
        return m.group(2)
    m = re.search(r'"Code":\s*"(\d{6})"', text)
    if m:
        return m.group(1)
    return None

txts = sorted(glob.glob(os.path.join(BASE, "mcp-tdx-connector-tdx_kline-*.txt")))
n = 0
skip = 0
fail = 0
for txt in txts:
    text = open(txt, encoding="utf-8", errors="ignore").read()
    code = get_code(text)
    if not code:
        print(f"SKIP no-code: {os.path.basename(txt)}")
        skip += 1
        continue
    out = os.path.join(RAW, f"{code}.json")
    if os.path.exists(out):
        skip += 1
        continue
    js = extract_json(text)
    if not js:
        print(f"FAIL no-json: {code} ({os.path.basename(txt)})")
        fail += 1
        continue
    try:
        obj = json.loads(js)
    except Exception as e:
        print(f"FAIL json-err: {code} {e}")
        fail += 1
        continue
    rows = obj.get("Rows") or obj.get("rows") or []
    if not rows:
        print(f"FAIL no-rows: {code}")
        fail += 1
        continue
    json.dump(obj, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    ds = [r.get("Data") for r in rows if r.get("Data")]
    print(f"CONVERT {code}: {len(rows)} bars {min(ds)}~{max(ds)}")
    n += 1

print(f"CONVERTED_NEW={n} SKIPPED={skip} FAILED={fail}")
