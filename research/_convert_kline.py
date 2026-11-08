# -*- coding: utf-8 -*-
"""
tdx_kline 响应落盘转换器（离线，避免 100k+ 响应进入对话上下文）
=========================================================================
tdx_kline(period="0") 返回体常 >100k 字符，框架会把它存到
  tool-results/mcp-tdx-connector-tdx_kline-*.txt
本脚本读取该 txt，抽取其中 JSON（"详细K线数据:" 之后的 {…}），
原样写入 research/raw_minute/{code}.json，供 build_minute_cache.py 转换。

用法：python research/_convert_kline.py <txt_path> <code>
"""
import json, os, sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJ, "research", "raw_minute")

def extract_json(text):
    """定位 '详细K线数据:' 之后的第一个 '{'，用括号匹配(尊重字符串/转义)找到
    与之配对的最近 '}'，避免把 JSON 之后的 MCP 元数据一并吞入导致 'Extra data'。"""
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

def main():
    if len(sys.argv) < 3:
        print("usage: _convert_kline.py <txt_path> <code>"); sys.exit(2)
    txt, code = sys.argv[1], sys.argv[2]
    if not os.path.exists(txt):
        print(f"[convert] txt 不存在: {txt}"); sys.exit(1)
    text = open(txt, encoding="utf-8", errors="ignore").read()
    js = extract_json(text)
    if not js:
        print(f"[convert] {code}: 未找到 JSON"); sys.exit(1)
    try:
        obj = json.loads(js)
    except Exception as e:
        print(f"[convert] {code}: JSON 解析失败: {e}"); sys.exit(1)
    rows = obj.get("Rows") or obj.get("rows") or []
    if not rows:
        print(f"[convert] {code}: 无 Rows"); sys.exit(1)
    os.makedirs(RAW, exist_ok=True)
    out = os.path.join(RAW, f"{code}.json")
    json.dump(obj, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    ds = [r.get("Data") for r in rows if r.get("Data")]
    print(f"[convert] {code}: {len(rows)} bars, {min(ds)}~{max(ds)} -> {out}")

if __name__ == "__main__":
    main()
