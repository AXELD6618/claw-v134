# -*- coding: utf-8 -*-
"""
妖股回填 · 原始响应清洗器
=======================
tdx_indicator_select / tdx_kline 的 MCP 返回体常在 JSON 之后追加
"[MCP App候选元数据]..." 大段元数据；kline 还在 JSON 前包了
"【名称】代码 | ... 详细K线数据:" 头。本脚本把 research/raw_sectors/*.json
与 research/raw_minute/*.json 中的「原始 MCP 文本」清洗为纯 JSON：
  - 截掉 [MCP App候选元数据] 及其之后内容
  - 取首个 { 或 [ 做括号匹配，得到平衡 JSON
  - 若已是合法 JSON 则跳过；若含 {"resp":...} 包裹则解包
用法：python research/clean_raw_json.py <dir>
"""
import json, os, glob, sys


def extract(text):
    idx = text.find("[MCP App候选元数据]")
    if idx >= 0:
        text = text[:idx]
    i = -1
    for ch in ("{", "["):
        p = text.find(ch)
        if p >= 0 and (i < 0 or p < i):
            i = p
    if i < 0:
        return None
    opening = text[i]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for k in range(i, len(text)):
        c = text[k]
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
        elif c == opening:
            depth += 1
        elif c == closing:
            depth -= 1
            if depth == 0:
                return text[i:k + 1]
    return None


def main():
    if len(sys.argv) < 2:
        print("usage: clean_raw_json.py <dir>")
        sys.exit(2)
    d = sys.argv[1]
    files = sorted(glob.glob(os.path.join(d, "*.json")))
    n_clean = 0
    n_skip = 0
    n_fail = 0
    for fp in files:
        code = os.path.splitext(os.path.basename(fp))[0]
        text = open(fp, encoding="utf-8", errors="ignore").read()
        try:
            obj = json.loads(text)
            valid = True
        except Exception:
            obj = None
            valid = False
        if valid:
            n_skip += 1
            continue
        js = extract(text)
        if not js:
            print(f"  [FAIL] {code}: 未找到 JSON")
            n_fail += 1
            continue
        try:
            obj = json.loads(js)
        except Exception as e:
            print(f"  [FAIL] {code}: JSON 解析失败: {e}")
            n_fail += 1
            continue
        if isinstance(obj, dict) and "resp" in obj and isinstance(obj["resp"], (list, dict)):
            obj = obj["resp"]
        json.dump(obj, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        n_items = len(obj.get("Rows", [])) if isinstance(obj, dict) else len(obj)
        print(f"  [OK] {code}: 清洗完成 ({n_items} rows/items)")
        n_clean += 1
    print(f"[clean] dir={d} 清洗 {n_clean} / 跳过(已干净) {n_skip} / 失败 {n_fail}")


if __name__ == "__main__":
    main()
