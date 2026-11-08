#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 TDX kline MCP 的 persisted-output 文本中提取最外层 JSON -> _raw_{code}.json。

MCP 结果文件结构:
  [中文头部文本]
  {
    "Setcode": ..., "Code": ..., "Period": ..., "Rows": [ ... ],
    "AttachInfo": {...}, "ListHead": {...},
    "candidateEntities": {...}, "candidateDataCardRequirements": [...]
  }
中文头部不是 JSON, 故需从第一个 '{' 做括号配对抽取最外层对象。
"""
import os, sys, json, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INTRADAY = os.path.join(ROOT, "backtest_cache", "intraday")


def extract_outer_json(text):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def main():
    src_dir = sys.argv[1] if len(sys.argv) > 1 else "."
    files = sorted(glob.glob(os.path.join(src_dir, "*.txt")))
    for fp in files:
        if "render" in fp:
            continue
        text = open(fp, encoding="utf-8", errors="replace").read()
        js = extract_outer_json(text)
        if not js:
            print("[skip] no json: %s" % os.path.basename(fp))
            continue
        try:
            obj = json.loads(js)
        except Exception as e:
            print("[skip] bad json %s: %s" % (os.path.basename(fp), e))
            continue
        code = obj.get("Code")
        if not code:
            print("[skip] no Code: %s" % os.path.basename(fp))
            continue
        rows = obj.get("Rows") or []
        outp = os.path.join(INTRADAY, "_raw_%s.json" % code)
        with open(outp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        print("[ok] %s -> _raw_%s.json  rows=%d  keys=%s" %
              (os.path.basename(fp), code, len(rows), list(obj.keys())))


if __name__ == "__main__":
    main()
