# -*- coding: utf-8 -*-
"""_filter_20261105.py — 过滤 fetched_20261105.json，只保留「缓存末日 == 2026-11-04」的票。

理由（2026-10-29 踩坑）：refresh_daily.py 只做「> 末日即追加」，若不前置过滤，
长期缺档票（末日停在 10-09~10-19）会被塞进单根 T 日空洞，制造假放量/假跳空。
同时内置本轮 TDX MCP 权威抽样值，逐项校验批量源 OHLC（容差 0.011），不过则 exit 3 不落盘。
"""
from __future__ import annotations
import os, sys, json, glob

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
TARGET = "2026-11-05"
PREV = "2026-11-04"
TOL = 0.011

# 本轮 tdx_kline(period=4, tqFlag=0) 实测权威值：Open/High/Low/Close
MCP = {
    "000001": (11.71, 11.77, 11.56, 11.64),
    "300750": (257.40, 272.19, 255.93, 264.06),
    "688981": (107.60, 113.84, 107.60, 110.69),
    "600537": (1.72, 1.86, 1.69, 1.86),
    "300839": (14.82, 15.40, 14.44, 15.19),
    "688146": (141.17, 154.53, 139.50, 149.99),
    "688325": (85.94, 92.90, 84.45, 92.46),
    "600892": (5.28, 5.49, 4.88, 5.14),
}


def tail_date(p):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 512))
            txt = f.read().decode("utf-8-sig", "ignore")
        for ln in reversed(txt.splitlines()):
            a = ln.strip().split(",")
            if len(a) >= 6 and a[0] and a[0] != "日期":
                return a[0]
    except OSError:
        pass
    return ""


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "data", "fetched_20261105.json")
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJ, "data", "fetched_20261105_ok.json")
    data = json.load(open(src, encoding="utf-8"))
    print(f"[filter] 抓取 {len(data)} 只, target={TARGET} prev={PREV}")

    # 1) MCP 抽样校验
    bad = []
    for code, (o, h, l, c) in MCP.items():
        r = data.get(code)
        if not r:
            bad.append((code, "MISS", None)); continue
        x = r[0]
        got = (float(x["Open"]), float(x["High"]), float(x["Low"]), float(x["Close"]))
        dif = max(abs(got[i] - v) for i, v in enumerate((o, h, l, c)))
        if dif > TOL:
            bad.append((code, "DIFF", f"{got} vs {(o,h,l,c)} max={dif:.4f}"))
        else:
            print(f"  [MCP-OK] {code} maxdiff={dif:.4f}")
    if bad:
        print("[filter] MCP 校验失败，拒绝落盘：")
        for b in bad:
            print("   ", b)
        return 3

    # 2) 只留缓存末日 == PREV 的票
    keep, drop_stale, drop_nofile = {}, 0, 0
    for code, rows in data.items():
        p = os.path.join(DAILY, f"{code}.csv")
        if not os.path.exists(p):
            drop_nofile += 1; continue
        ld = tail_date(p)
        if ld == PREV:
            keep[code] = rows
        else:
            drop_stale += 1

    json.dump(keep, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[filter] 保留 {len(keep)} / 末日非{PREV}剔除 {drop_stale} / 无缓存文件 {drop_nofile}")
    print(f"[filter] -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
