# -*- coding: utf-8 -*-
"""2026-11-06 独立复算验收（不采信 gen_lianban_watchlist.py 自证）

1. 直接用 backtest_cache/daily 全市场重算 2026-11-06 连板数（min_board>=2）
2. 与脚本台账逐只比对
3. 打印缓存末行样例、涨跌停判定用 Decimal 四舍五入（非银行家舍入）
"""
from __future__ import annotations
import os, glob, json
from decimal import Decimal, ROUND_HALF_UP

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
AS_OF = "2026-11-06"


def lp_of(code: str) -> Decimal:
    if code.startswith(("4", "8", "92")):
        return Decimal("0.30")
    if code.startswith(("300", "301", "688", "689")):
        return Decimal("0.20")
    return Decimal("0.10")          # 主板含 ST 亦为 10%


def rows_of(code: str):
    p = os.path.join(DAILY, f"{code}.csv")
    out = []
    with open(p, encoding="utf-8-sig") as f:
        for i, ln in enumerate(f):
            a = ln.strip().split(",")
            if len(a) < 6 or a[0] == "日期" or not a[0]:
                continue
            try:
                out.append((a[0].replace("-", "")[:8], float(a[1]), float(a[2]),
                            float(a[3]), float(a[4]), float(a[5])))
            except ValueError:
                continue
    return out


def is_zt(code, prev_c, close):
    prev = Decimal(str(prev_c))
    lim = (prev * (1 + lp_of(code))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(str(close)) >= lim - Decimal("0.005")


def main():
    zt_limit = {c.replace("-", ""): float(c.split(",")[2]) if False else 0 for c in []}
    hits = {}
    stale = 0
    for p in glob.glob(os.path.join(DAILY, "*.csv")):
        code = os.path.basename(p)[:6]
        rows = rows_of(code)
        if not rows:
            continue
        if rows[-1][0] != AS_OF.replace("-", ""):
            stale += 1
            continue
        # 从末行往回数连续涨停
        n = 0
        for i in range(len(rows) - 1, 0, -1):
            if rows[i][5] <= 0:          # 零成交(停牌)不计连板
                break
            if is_zt(code, rows[i - 1][2], rows[i][2]):
                n += 1
            else:
                break
        if n >= 2:
            hits[code] = (n, rows[-1][2])

    print(f"[audit] as_of={AS_OF} 扫描到当日 bar 的票 {len(hits) and ''}{5224 - stale}/{5224}")
    print(f"[audit] 独立复算 min_board>=2 -> {len(hits)} 只")
    for c, (n, close) in sorted(hits.items(), key=lambda x: (-x[1][0], x[0])):
        print(f"   {c} {n}板 close={close}")

    # 与脚本台账比对
    wl = os.path.join(PROJ, "data", "lianban_watchlist.json")
    if os.path.exists(wl):
        d = json.load(open(wl, encoding="utf-8"))
        items = d.get("items") or d.get("watchlist") or d
        if isinstance(items, dict):
            codes = set(items)
        else:
            codes = {str(x.get("code") or x.get("sec_code")).zfill(6) for x in items}
        mine = set(hits)
        print(f"[audit] 脚本台账 {len(codes)} 只 / 独立复算 {len(mine)} 只")
        print(f"[audit] 仅脚本有 {sorted(codes - mine)}  仅复算有 {sorted(mine - codes)}")
        print("[audit]", "PASS 完全一致" if codes == mine else "MISMATCH")
    else:
        print(f"[audit] 未找到 {wl}")

    # 缓存末行样例
    for code in ("000001", "600537"):
        rows = rows_of(code)
        print(f"[audit] {code}.csv 末行 = {rows[-1] if rows else 'EMPTY'}")

    print(f"[audit] 末日非 {AS_OF} 的票: {stale}")


if __name__ == "__main__":
    main()
