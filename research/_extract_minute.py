import json, os
SRC = r"C:\Users\SGM-AXELD\.workbuddy\projects\e-WorkBuddy_dot_workbuddy-Claw\2c810e2f-b51e-4923-9ec7-b8e785aed999\tool-results\mcp-tdx-connector-tdx_kline-1792853596697-6cc784.txt"
OUT = r"e:\WorkBuddy_dot_workbuddy\Claw\research\raw_minute\603278.json"
txt = open(SRC, encoding="utf-8", errors="replace").read()
head = txt.split("[MCP App候选元数据]")[0]
i = head.find("{")
j = head.rfind("}")
obj = json.loads(head[i:j + 1])
rows = obj.get("Rows")
if rows is None:
    rows = obj.get("response", {}).get("transformed", {}).get("tables", [{}])[0].get("rows")
print("rows:", len(rows) if rows else 0)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(obj, open(OUT, "w", encoding="utf-8"))
print("wrote", OUT)
