# -*- coding: utf-8 -*-
"""
情报特征 OOS 裁决（Round33）：龙虎榜 / 机构席位 是否对 T+1 有真实增量

铁律：hit_multiple≥1.15 且 组合 PF≥1.30 才谈部署；样本不足直接标 INSUFFICIENT。
与 R10 的区别（重要，不是重复投入）：
  R10 只用 lhb_prev 二值 × NOL 标签（正例率 0.072%，已知不可学）× 20 日样本 → 无增量。
  本脚本用 净买额/成交额、机构净买/流通市值、5日累计、席位方向 等细粒度 × BUY/gapup 标签 × 全历史。

用法：python research/intel_edge_test.py [--horizon t1|t5]
"""
import argparse
import glob
import gzip
import os
import sys

import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
FEAT = os.path.join(PROJ, "backtest_cache", "intel", "intel_features.csv.gz")
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
FACTORS = ["lhb_prev", "inst_prev", "lhb_net_ratio", "inst_net_cap",
           "inst_net_sign", "lhb_deal_ratio", "lhb_chg", "lhb_cnt_5d",
           "lhb_net_ratio_5d", "inst_net_cap_5d"]


def limit_ratio(code):
    return board_limit_pct(code)



def build_labels(codes):
    rows = []
    for code in codes:
        p = os.path.join(DAILY, f"{code}.csv")
        if not os.path.exists(p):
            continue
        try:
            df = pd.read_csv(p, usecols=["日期", "开盘", "收盘"]).dropna()
        except Exception:
            continue
        if len(df) < 40:
            continue
        d = df["日期"].astype(str).str.slice(0, 10).values
        o = df["开盘"].values.astype(np.float64)
        c = df["收盘"].values.astype(np.float64)
        n = len(c)
        prev = np.concatenate([[np.nan], c[:-1]])
        ret1 = c / prev - 1.0
        lim = limit_ratio(code)
        idx = np.arange(1, n - 5)
        if len(idx) == 0:
            continue
        m = np.isfinite(ret1[idx]) & (ret1[idx] < lim - 0.005)
        idx = idx[m]
        if len(idx) == 0:
            continue
        c0 = c[idx]
        gapup = (o[idx + 1] / c0 - 1.0 > 0.01)
        zt1 = (c[idx + 1] >= c0 * (1 + lim) - 0.011)
        rows.append(pd.DataFrame({
            "code": code, "date": d[idx],
            "buy": (gapup | zt1).astype(np.int8),
            "t1": (c[idx + 1] / c0 - 1.0).astype(np.float32),
            "t5": (c[idx + 5] / c0 - 1.0).astype(np.float32),
        }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def auc(y, s):
    ok = np.isfinite(s)
    y, s = y[ok], s[ok]
    if len(y) == 0 or y.min() == y.max():
        return np.nan
    r = pd.Series(s).rank().values
    n1 = y.sum()
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return np.nan
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", default="t1", choices=["t1", "t5"])
    ap.add_argument("--topk", type=int, default=10)
    a = ap.parse_args()

    if not os.path.exists(FEAT):
        print("[ERR] 缺 intel_features.csv.gz，先跑 research/intel_features.py", file=sys.stderr)
        return 1
    feat = pd.read_csv(FEAT, compression="gzip", dtype={"code": str})
    feat["code"] = feat["code"].str.zfill(6)
    print(f"[feat] {len(feat):,} 行（仅上榜标的）/ {feat['date'].nunique()} 可用日")

    # ★ 必须用全市场面板做左连接：特征表只含"上过榜"的票（约 3%），
    #   直接 inner join 会把没上榜的 97% 样本全部丢掉，基线率被抬到天上去。
    all_codes = [os.path.splitext(os.path.basename(f))[0]
                 for f in glob.glob(os.path.join(DAILY, "*.csv"))]
    lab = build_labels(all_codes)
    print(f"[market] {len(lab):,} 行可买 stock-day / {lab['date'].nunique()} 日 / {lab['code'].nunique()} 只")

    m = lab.merge(feat, on=["code", "date"], how="left", suffixes=("", "_i"))
    fill0 = [c for c in feat.columns if c not in ("code", "date")]
    for c in fill0:
        if c in m.columns:
            m[c] = pd.to_numeric(m[c], errors="coerce").fillna(0.0)
    covered = (m["lhb_prev"] > 0).mean() if "lhb_prev" in m.columns else 0.0
    print(f"[join] {len(m):,} 行 ｜ 情报覆盖 {covered*100:.2f}% "
          f"（=上榜标的占比，与全市场基线自洽）")

    m = m.dropna(subset=["buy"])
    base = m["buy"].mean()
    nd = m["date"].nunique()
    print(f"[base] BUY 正例率 {base:.4f} / {nd} 交易日")
    adequate = nd >= 240 and m["buy"].sum() >= 2000
    print(f"[样本] {'充足' if adequate else '⚠ 不足（需 ≥240 日且 ≥2000 正例）'}\n")

    print(f"{'因子':18s} {'AUC':>6s} {'有事件n':>9s} {'有事件hit':>9s} {'无事件hit':>9s} {'倍数':>6s} "
          f"{'事件组t1':>9s} {'事件组PF':>8s}")
    rows = []
    for f in FACTORS:
        if f not in m.columns:
            continue
        s = m[f].values.astype(float)
        if np.nanstd(s) == 0:
            print(f"{f:18s} {'—':>6s}  (常量)")
            continue
        A = auc(m["buy"].values.astype(int), s)
        has = s != 0
        h1, h0 = m["buy"].values[has], m["buy"].values[~has]
        r1 = h1.mean() if len(h1) else np.nan
        r0 = h0.mean() if len(h0) else np.nan
        hm = r1 / r0 if r0 and r0 > 0 else np.nan
        p1 = m[a.horizon].values[has]
        pf1 = (p1[p1 > 0].sum() / abs(p1[p1 < 0].sum())) if len(p1) and (p1 < 0).any() else np.nan
        print(f"{f:18s} {A:6.3f} {len(h1):9,d} {r1:9.4f} {r0:9.4f} {hm:6.3f} "
              f"{p1.mean()*100:+8.3f}% {pf1:8.3f}")
        rows.append({"factor": f, "auc": A, "n_event": int(len(h1)),
                     "hit_event": r1, "hit_noevent": r0, "hit_multiple": hm,
                     "event_t1_mean": float(p1.mean()), "event_pf": float(pf1)})

    # 组合 Top-K（每日按因子取前 K）——向量化：按 (date, -score) 排序后取组内前 K
    print(f"\n===== 每日 Top-{a.topk} 组合验证（同 R10 对照口径）=====")
    # ★ 必须先按 date 稳定排序，否则 factorize 出来的 code 不单调，
    #   searchsorted 找不到组边界，所有因子的 Top-K 会退化成同一组（本轮踩过）。
    m = m.sort_values("date", kind="mergesort").reset_index(drop=True)
    dcode, _ = pd.factorize(m["date"].values, sort=True)
    n = len(m)
    cnt = np.bincount(dcode)
    starts = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    grp_start = starts[dcode]
    pos_in_grp = np.arange(n) - grp_start
    print(f"    分组自检: {len(cnt)} 组, 组内首尾一致 "
          f"{'OK' if cnt.sum() == n else 'FAIL'}")

    def topk_mask(score):
        # 按 (date, -score) 排序后，第 i 个元素在组内的名次 = i - 组起点
        order = np.lexsort((-score, dcode))
        rank_sorted = np.arange(n) - starts[dcode[order]]
        mask = np.zeros(n, bool)
        mask[order[rank_sorted < a.topk]] = True
        return mask

    def rand_mask(rng):
        pr = rng.random(n)
        order = np.lexsort((pr, dcode))
        rank_sorted = np.arange(n) - starts[dcode[order]]
        mask = np.zeros(n, bool)
        mask[order[rank_sorted < a.topk]] = True
        return mask

    comb = []
    for f in FACTORS:
        if f not in m.columns:
            continue
        s = m[f].values.astype(float)
        if np.nanstd(s) == 0:
            continue
        mk = topk_mask(s)
        hr = m["buy"].values[mk].mean()
        rng = np.random.default_rng(7)
        rbase = float(np.mean([m["buy"].values[rand_mask(rng)].mean() for _ in range(5)]))
        p = m[a.horizon].values[mk]
        pf = p[p > 0].sum() / abs(p[p < 0].sum()) if (p < 0).any() else np.nan
        verdict = ("PROMOTE?" if (hr / base >= 1.15 and pf >= 1.30) else
                   ("INSUFFICIENT" if not adequate else "REJECT"))
        print(f"  {f:18s} 命中率 {hr:.4f} vs 全市场 {base:.4f} → ×{hr/base:.3f}"
              f" ｜ 随机 {rbase:.4f} → ×{rbase/base:.3f} ｜ PF {pf:.3f} ｜ {verdict}")
        comb.append({"factor": f, "topk_hit": float(hr), "hit_multiple": float(hr / base),
                     "rand_hit": float(rbase), "pf": float(pf), "verdict": verdict})

    import json
    def _f(x):
        try:
            return float(x)
        except Exception:
            return None
    singles = [{k: (_f(v) if k != "factor" else v) for k, v in r.items()} for r in rows]
    with open(os.path.join(PROJ, "research", "intel_edge_test.json"), "w", encoding="utf-8") as fh:
        json.dump({"days": int(nd), "base": float(base), "adequate": bool(adequate),
                   "single": singles, "topk": comb}, fh, ensure_ascii=False, indent=1)
    print("\n[OK] research/intel_edge_test.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
