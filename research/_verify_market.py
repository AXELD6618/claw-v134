# -*- coding: utf-8 -*-
"""校验批量源(qt_gtimg) vs TDX MCP 权威抽样, 通过后产出 fetched.json。

规则:
  - MCP 抽样的 T 日 OHLC 与批量源逐项比对, 容差 0.011
  - 通过率 < 100% 时报出偏差明细(不静默放行), exit 3 不落盘
  - fetched.json 只保留「缓存末日 == T-1」的票 (末日更早的长期缺档票不写, 避免制造空洞)

用法(参数化后可直接换日复用):
  python research/_verify_market.py --target 2026-10-29 --prev 2026-10-28 \
      --market data/market_20261029.json --mcp data/mcp_sample_20261029.json \
      --out data/fetched_20261029.json
  # 不带参数 = 2026-10-28 那轮的历史默认值(向后兼容)
"""
import argparse
import glob
import json
import os
import sys

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
TOL = 0.011

# 2026-10-28 那轮的默认值, 保持向后兼容(不带 --mcp 时使用)
DEF_TARGET = "20261028"
DEF_PREV = "2026-10-27"
DEF_MCP = {
    "300839": (19.76, 20.12, 18.42, 20.05),
    "003021": (92.50, 101.75, 90.65, 97.41),
    "301563": (111.02, 112.80, 107.00, 110.93),
    "301413": (60.07, 72.83, 59.48, 69.30),
    "301502": (71.00, 77.90, 71.00, 74.38),
    "603278": (17.05, 17.93, 17.05, 17.92),
    "301023": (31.77, 35.00, 31.72, 33.97),
    "002724": (12.30, 13.53, 12.25, 13.40),
    "600293": (4.01, 4.09, 3.84, 3.85),
    "603330": (9.47, 9.84, 9.00, 9.58),
    "603350": (29.52, 31.15, 29.10, 30.80),
    "603728": (57.00, 60.54, 55.55, 58.74),
    "002249": (7.82, 8.46, 7.76, 8.02),
    "001360": (14.75, 15.55, 14.69, 15.38),
    "603089": (10.66, 11.20, 10.59, 10.59),
    "002076": (2.02, 2.10, 1.94, 2.06),
}


def norm_date(s: str) -> str:
    s = str(s).strip()
    return s.replace("-", "")


def last_date(p):
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            lines = f.read().strip().split("\n")
        for ln in reversed(lines):
            a = ln.strip().split(",")
            if a and a[0] and a[0] != "日期":
                s = a[0].strip()
                return s if "-" in s else f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    except Exception:
        return ""
    return ""


def verify(mkt, target, mcp):
    ok = bad = missing = 0
    for code, (o, h, l, c) in mcp.items():
        rows = mkt.get(code) or []
        row = next((r for r in rows if norm_date(r.get("Data")) == norm_date(target)), None)
        if not row:
            missing += 1
            print(f"[MISS] {code}: 批量源无 {target} 行")
            continue
        got = tuple(float(row[k]) for k in ("Open", "High", "Low", "Close"))
        diff = max(abs(a - b) for a, b in zip(got, (o, h, l, c)))
        if diff <= TOL:
            ok += 1
        else:
            bad += 1
            print(f"[DIFF] {code}: MCP={o},{h},{l},{c} 批量={got} maxdiff={diff:.4f}")
    n = len(mcp)
    print(f"[verify] MCP 抽样 {n} 只: 一致 {ok} / 偏差 {bad} / 缺失 {missing} (容差 {TOL})")
    return ok, bad, missing


def build(mkt, target, prev):
    files = glob.glob(os.path.join(DAILY, "*.csv"))
    print(f"[build] 缓存文件 {len(files)} 只")
    fetched, n_prev, n_gap, n_norow = {}, 0, 0, 0
    gap_codes = []
    for p in files:
        code = os.path.basename(p).split(".")[0]
        ld = last_date(p)
        rows = mkt.get(code) or []
        row = next((r for r in rows if norm_date(r.get("Data")) == norm_date(target)), None)
        if row is None:
            n_norow += 1
            continue
        if ld == prev:
            fetched[code] = [row]
            n_prev += 1
        else:
            n_gap += 1
            gap_codes.append(f"{code}@{ld}")
    print(f"[build] 可续行(末日={prev}) {n_prev} 只 / 长期缺档跳过 {n_gap} 只 / 当日无数据 {n_norow} 只")
    if gap_codes:
        print("[build] 跳过样本: " + ", ".join(gap_codes[:15]))
    return fetched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="2026-10-28")
    ap.add_argument("--prev", default="2026-10-27")
    ap.add_argument("--market", default=os.path.join(PROJ, "data", "market_20261028.json"))
    ap.add_argument("--mcp", default=None, help="MCP 抽样 json: {code: [O,H,L,C]}")
    ap.add_argument("--out", default=os.path.join(PROJ, "data", "fetched_20261028.json"))
    a = ap.parse_args()

    mcp = dict(DEF_MCP)
    if a.mcp:
        raw = json.load(open(a.mcp, encoding="utf-8"))
        mcp = {k: tuple(v) for k, v in raw.items()}
    mkt = json.load(open(a.market, encoding="utf-8"))
    print(f"[market] 批量源覆盖 {len(mkt)} 只 | target={a.target} prev={a.prev} | MCP 抽样 {len(mcp)} 只")

    ok, bad, missing = verify(mkt, a.target, mcp)
    if bad or missing:
        print("[ABORT] 抽样校验未全通, 不落盘")
        return 3
    fetched = build(mkt, a.target, a.prev)
    json.dump(fetched, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[OK] fetched -> {a.out} ({len(fetched)} 只)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
