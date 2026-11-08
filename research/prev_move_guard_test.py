# -*- coding: utf-8 -*-
"""
破位排除守卫的增量验证：把「T 日大跌」组从可选池中剔除，组合 PF 是否提升？

背景：prev_move_oos 显示按 T 日涨跌幅极端度取 Top-K，18 个组合全 REJECT 且
PF 0.47~0.80（随机 1.08~1.10）。既然「选它们」亏钱，那么「不选它们」应该赚钱。
本脚本直接量化这个减法：

  池 A = 全部可买（T 日未涨停）
  池 B = A 剔除 高位破位（close>MA20 且 ret1<-7%）
  池 C = A 剔除 双向极端（|ret1|>7%）
  池 D = A 剔除 高位破位 + 低位暴跌（ret1<-10%）

对每个池跑：随机 K 只（20 次）、mom Top-K、以及全池等权（benchmark）。
看 PF / t1 均值 / 命中率的差异，差值即守卫的真实增量。
"""
import glob
import json
import os

import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

DAILY = r"E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
OOS_START = "2024-07-01"
K = 10


def limit_ratio(code):
    return board_limit_pct(code)



def build():
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    D, R, LOW, BUY, T1, T5 = [], [], [], [], [], []
    for k, code in enumerate(codes):
        try:
            df = pd.read_csv(os.path.join(DAILY, f"{code}.csv"),
                             usecols=["日期", "开盘", "收盘"]).dropna()
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
        ma20 = pd.Series(c).rolling(20).mean().values
        lim = limit_ratio(code)
        idx = np.arange(30, n - 5)
        if len(idx) == 0:
            continue
        m = (d[idx] >= OOS_START) & np.isfinite(ret1[idx]) & (ret1[idx] < lim - 0.005)
        idx = idx[m]
        if len(idx) == 0:
            continue
        c0 = c[idx]
        gapup = (o[idx + 1] / c0 - 1.0 > 0.01)
        zt1 = (c[idx + 1] >= c0 * (1 + lim) - 0.011)
        D.append(d[idx])
        R.append(ret1[idx].astype(np.float32))
        LOW.append((c0 < ma20[idx]).astype(bool))
        BUY.append((gapup | zt1).astype(np.float32))
        T1.append((c[idx + 1] / c0 - 1.0).astype(np.float32))
        T5.append((c[idx + 5] / c0 - 1.0).astype(np.float32))
        if (k + 1) % 1500 == 0:
            print(f"  ...{k+1}/{len(codes)}", flush=True)
    return pd.DataFrame({
        "date": np.concatenate(D), "ret1": np.concatenate(R),
        "low": np.concatenate(LOW), "buy": np.concatenate(BUY),
        "t1": np.concatenate(T1), "t5": np.concatenate(T5)})


POOLS = {
    "A_all": lambda p: np.ones(len(p), bool),
    "B_no_high_break": lambda p: ~((~p["low"].values) & (p["ret1"].values < -0.07)),
    "C_no_extreme": lambda p: np.abs(p["ret1"].values) <= 0.07,
    "D_no_highbreak_plus_crash": lambda p: (~((~p["low"].values) & (p["ret1"].values < -0.07))) &
                                            (p["ret1"].values >= -0.10),
}


def pf(x):
    x = np.asarray(x, dtype=float)
    return x[x > 0].sum() / abs(x[x < 0].sum()) if (x < 0).any() else np.nan


def main():
    print("[1/2] 建面板 ...", flush=True)
    p = build()
    print(f"      {len(p):,} 行 / {p['date'].nunique()} 日", flush=True)
    rng = np.random.default_rng(20261018)
    rows = []
    print("[2/2] 分池评估 ...\n", flush=True)
    for name, fn in POOLS.items():
        mask = fn(p)
        sub = p[mask]
        # 全池等权
        rows.append({"pool": name, "n": len(sub), "cover": len(sub) / len(p),
                     "kind": "等权全池", "hit": sub["buy"].mean(),
                     "t1_mean": sub["t1"].mean(), "pf_t1": pf(sub["t1"]),
                     "t5_mean": sub["t5"].mean(), "pf_t5": pf(sub["t5"])})
        # 随机 K
        rh, rp, rp5 = [], [], []
        for _ in range(20):
            idx = []
            for _, g in sub.groupby("date"):
                kk = min(K, len(g))
                if kk == 0:
                    continue
                idx.append(g.index.values[rng.choice(len(g), kk, replace=False)])
            if not idx:
                continue
            s = sub.loc[np.concatenate(idx)]
            rh.append(s["buy"].mean())
            rp.append(pf(s["t1"]))
            rp5.append(pf(s["t5"]))
        rows.append({"pool": name, "n": len(sub), "cover": len(sub) / len(p),
                     "kind": f"随机K={K}", "hit": np.mean(rh),
                     "t1_mean": np.nan, "pf_t1": np.nanmean(rp),
                     "t5_mean": np.nan, "pf_t5": np.nanmean(rp5)})
        # mom Top-K
        idx = []
        for _, g in sub.groupby("date"):
            idx.append(g.nlargest(min(K, len(g)), "ret1").index.values)
        s = sub.loc[np.concatenate(idx)] if idx else sub.iloc[:0]
        rows.append({"pool": name, "n": len(s), "cover": len(sub) / len(p),
                     "kind": f"mom Top{K}", "hit": s["buy"].mean() if len(s) else np.nan,
                     "t1_mean": s["t1"].mean() if len(s) else np.nan,
                     "pf_t1": pf(s["t1"]) if len(s) else np.nan,
                     "t5_mean": s["t5"].mean() if len(s) else np.nan,
                     "pf_t5": pf(s["t5"]) if len(s) else np.nan})
    out = pd.DataFrame(rows)
    out.to_json("research/prev_move_guard_test.json", orient="records",
                force_ascii=False, indent=1)
    pd.set_option("display.width", 200, "display.max_rows", 50)
    print(out.round(4).to_string(index=False))

    a = out[out.pool == "A_all"].set_index("kind")
    print("\n===== 守卫增量（相对 A_all 同口径）=====")
    for _, r in out[out.pool != "A_all"].iterrows():
        b = a.loc[r["kind"]]
        print(f"  {r['pool']:26s} {r['kind']:12s} "
              f"PF_t1 {b['pf_t1']:.3f}→{r['pf_t1']:.3f} ({r['pf_t1']-b['pf_t1']:+.3f})  "
              f"PF_t5 {b['pf_t5']:.3f}→{r['pf_t5']:.3f} ({r['pf_t5']-b['pf_t5']:+.3f})  "
              f"覆盖 {r['cover']*100:.1f}%")
    print("\n[OK] research/prev_move_guard_test.json")


if __name__ == "__main__":
    main()
