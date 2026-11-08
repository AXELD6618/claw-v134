#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S11 regime 分切 OOS: 断层反包负期望说明回测 regime≠实盘, 须牛/熊/震荡分段验证.

按自然年拆分 2018-2026, 逐年统计连板惯性(LB)/断层反包(DC) 的:
  n, T+1涨停率, T+1收益均值, 中位数, 胜率, 剔除涨停后条件均值.
并聚合为 牛(2019-2021)/熊(2018,2022,2023)/近期(2024-2026) 三段对比,
定位断层反包收益期望在哪个 regime 成立/失效 (账户回撤62% 的 regime 漂移证据).
独立读原始日线, 与生产模型无关.
"""
import os, sys, glob, json
from collections import defaultdict
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p1_edge_oos as E

PROJ = E.PROJ
REGIME = {"2018": "熊", "2019": "牛", "2020": "牛", "2021": "牛",
          "2022": "熊", "2023": "熊", "2024": "近期", "2025": "近期", "2026": "近期"}


def main():
    files = sorted(glob.glob(os.path.join(E.DAILY, "*.csv")))
    yr = defaultdict(lambda: {"LB": [], "DC": []})
    for fn in files:
        r = E.proc(fn)
        if r is None:
            continue
        for s in r[0]:
            yr[s[1][:4]][s[0]].append(s)
    # 逐年
    yearly = {}
    print(f"{'年':>5} {'regime':>4} {'edge':>4} {'n':>7} {'T+1涨停':>8} {'收益均值':>8} {'中位':>8} {'胜率':>6} {'剔除涨停':>8}")
    for y in sorted(yr):
        for kind in ("LB", "DC"):
            g = yr[y][kind]
            if not g:
                continue
            t1zt = np.mean([s[3] for s in g])
            t1r = np.array([s[2] for s in g])
            ex = t1r[~np.array([s[3] for s in g], bool)]
            row = dict(n=len(g), t1_zt=float(t1zt), ret_mean=float(t1r.mean()),
                       ret_med=float(np.median(t1r)), win=float((t1r > 0).mean()),
                       ret_ex_zt=float(ex.mean()) if len(ex) else None)
            yearly.setdefault(y, {})[kind] = row
            print(f"{y:>5} {REGIME.get(y,'?'):>4} {kind:>4} {len(g):>7} {t1zt:>8.3f} {t1r.mean():>+8.4f} {np.median(t1r):>+8.4f} {row['win']:>6.3f} {row['ret_ex_zt']:>+8.4f}")
    # regime 聚合
    print("=" * 70)
    print("regime 聚合 (牛 2019-21 / 熊 2018,22,23 / 近期 2024-26):")
    agg = defaultdict(lambda: {"LB": [], "DC": []})
    for y in yr:
        for kind in ("LB", "DC"):
            agg[REGIME.get(y, "?")][kind] += yr[y][kind]
    summary = {}
    for rg in ("牛", "熊", "近期"):
        print(f"--- {rg} ---")
        summary[rg] = {}
        for kind in ("LB", "DC"):
            g = agg[rg][kind]
            if not g:
                continue
            t1zt = np.mean([s[3] for s in g])
            t1r = np.array([s[2] for s in g])
            ex = t1r[~np.array([s[3] for s in g], bool)]
            print(f"  {kind}: n={len(g)} T+1涨停={t1zt:.3f} 收益均值={t1r.mean():+.4f} 剔除涨停={ex.mean():+.4f}")
            summary[rg][kind] = dict(n=len(g), t1_zt=float(t1zt),
                                     ret_mean=float(t1r.mean()), ret_ex_zt=float(ex.mean()))
    print("=" * 70)
    print("S11 判定: 断层反包(DC) 收益均值 在 牛/熊/近期 各 regime:")
    for rg in ("牛", "熊", "近期"):
        dc = summary.get(rg, {}).get("DC")
        if dc:
            print(f"  {rg}: 收益均值={dc['ret_mean']:+.4f} 剔除涨停={dc['ret_ex_zt']:+.4f} -> {'正期望(可用)' if dc['ret_mean']>0 else '负期望(禁用)'}")
    json.dump({"yearly": yearly, "regime_agg": summary},
              open(os.path.join(PROJ, "reports", "regime_oos_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("[saved] reports/regime_oos_result.json")


if __name__ == "__main__":
    main()
