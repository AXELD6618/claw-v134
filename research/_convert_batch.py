# -*- coding: utf-8 -*-
"""
批量把本批 40 个 tdx_kline 落盘 txt 转为 raw_minute/{code}.json。
复用 _convert_kline.py 的 extract_json，逐文件调用，结构一致。
映射来自本批 MCP 调用结果记录（txt 文件名 <-> code）。
"""
import os, sys, json, importlib.util

PROJ = r"E:\WorkBuddy_dot_workbuddy\Claw"
TOOLRES = (r"C:\Users\SGM-AXELD\.workbuddy\projects"
           r"\e-WorkBuddy_dot_workbuddy-毕方灵犀通达"
           r"\9c056ea3-35f8-466a-b918-966ae97802c5\tool-results")
RAW = os.path.join(PROJ, "research", "raw_minute")

# 代码 -> tdx_kline 落盘 txt 的 stem（不含 "mcp-tdx-connector-tdx_kline-" 前缀与 .txt）
M = {
    "000635": "1793018705190-c8ab85",
    "000637": "1793018884864-ec9fc4",
    "000652": "1793018878855-ad6c9c",
    "000663": "1793018884862-a53e27",
    "000676": "1793018878855-f95d58",
    "000726": "1793018884860-e12db6",
    "000795": "1793018880071-b30884",
    "000798": "1793018884856-1427f4",
    "000862": "1793018884857-5b18d5",
    "000880": "1793018878856-902380",
    "000882": "1793018880077-79dab2",
    "000883": "1793018884859-418309",
    "000888": "1793018880070-523864",
    "000929": "1793018884863-f63873",
    "000933": "1793018884862-467823",
    "000963": "1793018884861-51296a",
    "000970": "1793018884858-f13336",
    "001228": "1793018880070-16a5ea",
    "001234": "1793018880069-d3eaae",
    "001248": "1793018884152-c5f4f1",
    "001280": "1793018880071-816d2b",
    "001282": "1793018961140-8f39cb",
    "001324": "1793018961676-fe6c73",
    "001378": "1793018961754-f31adb",
    "001391": "1793018961132-ec597f",
    "002008": "1793018961752-1faca6",
    "002020": "1793018961836-9a714f",
    "002030": "1793018961753-c5054a",
    "002038": "1793018961721-f1d0d6",
    "002109": "1793018963498-c7257f",
    "002146": "1793018961722-694079",
    "002148": "1793018961837-85eeb8",
    "002163": "1793018963497-815727",
    "002167": "1793018961114-65052b",
    "002170": "1793018961753-a2c218",
    "002192": "1793018960882-40406e",
    "002193": "1793018963497-0e0658",
    "002206": "1793018963496-69a707",
    "002208": "1793018961697-146625",
    "002225": "1793018963497-eb705a",
}

# 复用 _convert_kline.py 的提取逻辑
spec = importlib.util.spec_from_file_location(
    "ck", os.path.join(PROJ, "research", "_convert_kline.py"))
ck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ck)


def convert_one(txt, code):
    if not os.path.exists(txt):
        print(f"[batch] {code}: txt 缺失 {txt}")
        return False
    text = open(txt, encoding="utf-8", errors="ignore").read()
    js = ck.extract_json(text)
    if not js:
        print(f"[batch] {code}: 未找到 JSON")
        return False
    try:
        obj = json.loads(js)
    except Exception as e:
        print(f"[batch] {code}: JSON 解析失败: {e}")
        return False
    rows = obj.get("Rows") or obj.get("rows") or []
    if not rows:
        print(f"[batch] {code}: 无 Rows")
        return False
    os.makedirs(RAW, exist_ok=True)
    out = os.path.join(RAW, f"{code}.json")
    json.dump(obj, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    ds = [r.get("Data") for r in rows if r.get("Data")]
    print(f"[batch] {code}: {len(rows)} bars, {min(ds)}~{max(ds)} -> {out}")
    return True


def main():
    ok = 0
    fail = 0
    for code, stem in M.items():
        txt = os.path.join(TOOLRES, f"mcp-tdx-connector-tdx_kline-{stem}.txt")
        if convert_one(txt, code):
            ok += 1
        else:
            fail += 1
    print(f"[batch] done ok={ok} fail={fail} total={len(M)}")


if __name__ == "__main__":
    main()
