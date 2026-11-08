# -*- coding: utf-8 -*-
"""_audit_20261105.py — 独立复算连板台账 + 缓存日期分布（不复用 gen_lianban_watchlist 任何代码）。

涨停口径（交易所口径，非比例阈值）：
  close >= round(prev_close * (1+lp), 2) - 0.005
  lp: 4/8/92 开头 30% | 30/688/689 20% | 其余 10%（主板 ST 也是 10%）
"""
from __future__ import annotations
import os, glob, json, collections
from research.board_limit import board_limit_pct, is_limit_up

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
T = "2026-11-05"


def lp_of(code):
    return board_limit_pct(code)



def is_zt(code, prev, close):
    lim = round(prev * (1 + lp_of(code)), 2)
    return close >= lim - 0.005


def main():
    dates = collections.Counter()
    boards = {}
    for p in glob.glob(os.path.join(DAILY, "*.csv")):
        code = os.path.basename(p)[:6]
        try:
            with open(p, "rb") as f:
                f.seek(0, 2); sz = f.tell()
                f.seek(max(0, sz - 2000))
                txt = f.read().decode("utf-8-sig", "ignore")
        except OSError:
            continue
        rows = []
        for ln in txt.splitlines():
            a = ln.strip().split(",")
            if len(a) >= 6 and a[0] and a[0] != "日期":
                try:
                    rows.append((a[0], float(a[1]), float(a[2]), float(a[3]), float(a[4]), float(a[5])))
                except ValueError:
                    pass
        if not rows:
            continue
        dates[rows[-1][0]] += 1
        if rows[-1][0] != T or len(rows) < 2:
            continue
        # 从末日往前数连续涨停
        n = 0
        for i in range(len(rows) - 1, 0, -1):
            d, o, c, h, l, v = rows[i]
            prev = rows[i - 1][2]
            if v <= 0 or o <= 0 or h <= 0 or l <= 0 or prev <= 0:
                break
            if is_zt(code, prev, c):
                n += 1
            else:
                break
        if n >= 2:
            boards[code] = (n, rows[-1][2])

    print("[audit] 缓存日期分布(最新5档):")
    for d, n in sorted(dates.items())[-5:]:
        print(f"   {d}: {n}")
    print(f"[audit] 总文件 {sum(dates.values())} / 末日=={T} {dates.get(T,0)}")
    print(f"[audit] 独立复算 连板>=2: {len(boards)} 只")
    for code, (n, c) in sorted(boards.items(), key=lambda x: (-x[1][0], x[0])):
        print(f"   {code} {n}板 close={c:.2f}")

    wl = json.load(open(os.path.join(PROJ, "backtest_cache", "lianban_watchlist.json"), encoding="utf-8"))
    script = {c["code"]: c["board_len"] for c in wl["codes"]}
    print(f"[audit] 脚本台账 {len(script)} 只")
    only_a = set(boards) - set(script)
    only_b = set(script) - set(boards)
    diff = [(k, boards[k][0], script[k]) for k in set(boards) & set(script) if boards[k][0] != script[k]]
    print(f"[audit] 仅独立复算有: {sorted(only_a)}")
    print(f"[audit] 仅脚本有: {sorted(only_b)}")
    print(f"[audit] 板数不一致: {diff}")
    ok = not only_a and not only_b and not diff
    print(f"[audit] 一致性: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
