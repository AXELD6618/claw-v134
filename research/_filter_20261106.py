# -*- coding: utf-8 -*-
"""2026-11-06 单轮核验器（一次性，可复用改日期常量）

作用：
 1. 用本轮真实 TDX MCP 返回的 7 只权威 OHLC 校验批量源 fetched_20261106.json
    （容差 0.011，不过 -> exit 3，绝不落盘）
 2. 按「缓存末日 == T-1 = 2026-11-05」过滤，只保留增量可衔接的票
    （不过滤会把 31 只长期缺档票塞成单根 T 日空洞）
 3. 输出 data/fetched_20261106_ok.json
"""
from __future__ import annotations
import os, sys, json

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")

T = "20261106"
T_DASH = "2026-11-06"
PREV = "2026-11-05"
TOL = 0.011

# 本轮 tdx_kline(code, setcode, period="4") 返回值，20261106 一根
MCP = {
    "000001": {"Open": 11.62, "High": 11.85, "Low": 11.57, "Close": 11.81},
    "000333": {"Open": 87.52, "High": 89.87, "Low": 87.47, "Close": 89.60},
    "300750": {"Open": 258.78, "High": 269.89, "Low": 251.88, "Close": 254.22},
    "688036": {"Open": 63.07, "High": 67.29, "Low": 63.00, "Close": 65.00},
    "600537": {"Open": 1.80, "High": 2.05, "Low": 1.80, "Close": 2.05},
    "601318": {"Open": 54.49, "High": 54.67, "Low": 53.80, "Close": 54.50},
    "002594": {"Open": 85.00, "High": 86.57, "Low": 84.80, "Close": 85.23},
    "300308": {"Open": 433.50, "High": 463.33, "Low": 427.34, "Close": 448.00},
}

# 曾抽样但不在缓存 universe 内的票（600519 茅台：backtest_cache 无此 CSV），单列不计失败
OUT_OF_UNIVERSE = {"600519": "缓存 universe 无 600519.csv"}


def tail_date(code: str) -> str:
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p):
        return ""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 4096))
            txt = f.read().decode("utf-8-sig", "ignore")
    except OSError:
        return ""
    for ln in reversed(txt.splitlines()):
        a = ln.strip().split(",")
        if len(a) >= 6 and a[0] and a[0] != "日期":
            d = a[0].strip().replace("-", "")
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else ""
    return ""


def main() -> int:
    src = os.path.join(PROJ, "data", f"fetched_{T}.json")
    fetched = json.load(open(src, encoding="utf-8"))
    print(f"[check] 批量源 {len(fetched)} 只")

    # ---- 1. MCP 反向校验 ----
    for code, why in OUT_OF_UNIVERSE.items():
        print(f"  [n/a] {code} 未参与校验：{why}")
    bad = []
    for code, ref in MCP.items():
        rows = fetched.get(code)
        if not rows:
            bad.append((code, "缺数据", None))
            continue
        r = rows[0]
        for k in ("Open", "High", "Low", "Close"):
            got = float(r[k])
            if abs(got - ref[k]) > TOL:
                bad.append((code, f"{k} 批量={got} MCP={ref[k]}", got))
                break
        else:
            print(f"  [ok] {code} O{r['Open']} H{r['High']} L{r['Low']} C{r['Close']} 与 MCP 一致")
    if bad:
        print("[check] MCP 校验失败，禁止落盘：")
        for b in bad:
            print("   ", b)
        return 3
    print(f"[check] MCP {len(MCP)}/{len(MCP)} 全部一致（容差 {TOL}）")

    # ---- 2. 末日过滤 ----
    ok, stale, missing = {}, 0, 0
    for code, rows in fetched.items():
        ld = tail_date(code)
        if ld == "":
            missing += 1
            continue
        if ld != PREV:
            stale += 1
            continue
        ok[code] = rows
    out = os.path.join(PROJ, "data", f"fetched_{T}_ok.json")
    json.dump(ok, open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[check] 可衔接(末日=={PREV}) {len(ok)} / 末日非 {PREV} {stale} / 无缓存 {missing}")
    print(f"[check] -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
