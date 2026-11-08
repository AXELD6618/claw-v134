# -*- coding: utf-8 -*-
"""
「T 日涨跌幅 / 位置」组合级 OOS 验证（铁律落地：只认 Top-K 命中倍数 + 组合 PF）

背景：单因子条件率显示 T+1 涨停率在 T 日大跌(<−7%)时达基线 2.6~3.0 倍，
但 T+1 均值为负、5 日中位数为负 → 疑似纯波动放大。
本脚本用组合口径裁决：每日在可买子集中按候选因子取 Top-K，看真实兑现。

候选因子（全部 T 日收盘可知，无未来信息）：
  mom      ret1                    越大越前（动量）
  rev      -ret1                   越小越前（反转）
  extreme  |ret1|                  越极端越前（U 型）
  rev_low  -ret1 且 close<MA20      低位超跌反弹
  rev_high -ret1 且 close>MA20      高位破位（风险验证，应为负）
  ext_low  |ret1| 且 close<MA20
基线对照：每日随机无放回抽 K 只（rng 固定种子），跑 20 次取均值。

输出 research/prev_move_oos.json + 控制台表。
"""
import glob
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

DAILY = r"E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
OOS_START = "2024-07-01"
KS = [5, 10, 20]
FACTORS = ["mom", "rev", "extreme", "rev_low", "rev_high", "ext_low"]


def limit_ratio(code):
    return board_limit_pct(code)



def build_panel():
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    dts, cds, r1s, lows, buys, t1s, t5s = [], [], [], [], [], [], []
    base_days = defaultdict(lambda: [0, 0])   # date -> [n, hits]
    for k, code in enumerate(codes):
        try:
            df = pd.read_csv(os.path.join(DAILY, f"{code}.csv"),
                             usecols=["日期", "开盘", "收盘"])
        except Exception:
            continue
        if len(df) < 40:
            continue
        df = df.dropna()
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
        buy = (gapup | zt1).astype(np.float32)
        t1 = (c[idx + 1] / c0 - 1.0).astype(np.float32)
        t5 = (c[idx + 5] / c0 - 1.0).astype(np.float32)
        low = (c0 < ma20[idx]).astype(np.float32)
        dd = d[idx]
        dts.append(dd)
        cds.append(np.array([code] * len(idx)))
        r1s.append(ret1[idx].astype(np.float32))
        lows.append(low)
        buys.append(buy)
        t1s.append(t1)
        t5s.append(t5)
        for j, day in enumerate(dd):
            b = base_days[day]
            b[0] += 1
            b[1] += int(buy[j])
        if (k + 1) % 1500 == 0:
            print(f"  ...{k+1}/{len(codes)}", flush=True)

    panel = pd.DataFrame({
        "date": np.concatenate(dts), "code": np.concatenate(cds),
        "ret1": np.concatenate(r1s), "low": np.concatenate(lows),
        "buy": np.concatenate(buys), "t1": np.concatenate(t1s), "t5": np.concatenate(t5s),
    })
    return panel, base_days


def score(panel, factor):
    r = panel["ret1"].values
    if factor == "mom":
        return r
    if factor == "rev":
        return -r
    if factor == "extreme":
        return np.abs(r)
    if factor == "rev_low":
        return np.where(panel["low"].values > 0, -r, -np.inf)
    if factor == "rev_high":
        return np.where(panel["low"].values == 0, -r, -np.inf)
    if factor == "ext_low":
        return np.where(panel["low"].values > 0, np.abs(r), -np.inf)
    raise ValueError(factor)


def evaluate(panel, base_days, rng, n_rand=20):
    out = []
    dates = panel["date"].values
    order = np.argsort(dates, kind="mergesort")
    panel = panel.iloc[order].reset_index(drop=True)
    dates = panel["date"].values
    uniq, starts = np.unique(dates, return_index=True)
    ends = np.append(starts[1:], len(dates))

    for factor in FACTORS:
        sc = score(panel, factor)
        for K in KS:
            picks = []
            for s, e in zip(starts, ends):
                sl = sc[s:e]
                if not np.isfinite(sl).any():
                    continue
                kk = min(K, int(np.isfinite(sl).sum()))
                pos = np.argpartition(-sl, kk - 1)[:kk]
                picks.append(s + pos)
            if not picks:
                continue
            pi = np.concatenate(picks)
            sub = panel.iloc[pi]
            n = len(sub)
            hits = sub["buy"].sum()
            base_rate = sum(v[1] for v in base_days.values()) / max(sum(v[0] for v in base_days.values()), 1)
            hit_mult = (hits / n) / base_rate if base_rate > 0 else np.nan
            p = sub["t1"].values
            pf = p[p > 0].sum() / abs(p[p < 0].sum()) if (p < 0).any() else np.nan
            # 随机基线
            rb_hit, rb_pf = [], []
            for _ in range(n_rand):
                ridx = []
                for s, e in zip(starts, ends):
                    kk = min(K, e - s)
                    ridx.append(s + rng.choice(e - s, kk, replace=False))
                rr = panel.iloc[np.concatenate(ridx)]
                rb_hit.append(rr["buy"].mean())
                q = rr["t1"].values
                rb_pf.append(q[q > 0].sum() / abs(q[q < 0].sum()) if (q < 0).any() else np.nan)
            out.append({
                "factor": factor, "K": K, "n_picks": n, "days": len(picks),
                "hit_rate": hits / n, "hit_multiple": hit_mult,
                "t1_mean": float(np.mean(p)), "t1_median": float(np.median(p)),
                "pf_t1": pf, "t5_mean": float(sub["t5"].mean()),
                "rand_hit_rate": float(np.mean(rb_hit)),
                "rand_pf": float(np.nanmean(rb_pf)),
            })
    return pd.DataFrame(out)


def main():
    print("[1/2] 构建 OOS 面板 ...", flush=True)
    panel, base_days = build_panel()
    print(f"      面板 {len(panel):,} 行 / {panel['date'].nunique()} 交易日 / "
          f"{panel['code'].nunique()} 只", flush=True)
    print("[2/2] 组合验证 ...", flush=True)
    rng = np.random.default_rng(20261018)
    res = evaluate(panel, base_days, rng)
    res.to_json("research/prev_move_oos.json", orient="records", force_ascii=False, indent=1)
    pd.set_option("display.width", 220, "display.max_rows", 200)
    print()
    print(res.round(4).to_string(index=False))

    print("\n===== 裁决（铁律：hit_multiple≥1.15 且 pf_t1≥1.30）=====")
    for _, r in res.iterrows():
        verdict = "PROMOTE?" if (r.hit_multiple >= 1.15 and r.pf_t1 >= 1.30) else "REJECT"
        star = "★" if r.hit_multiple >= 1.15 and r.pf_t1 >= 1.30 else " "
        print(f"{star} {r.factor:9s} K={r.K:<3d} hit×{r.hit_multiple:.3f} "
              f"PF={r.pf_t1:.3f} t1均值={r.t1_mean*100:+.3f}% "
              f"(随机 hit×{(r.rand_hit_rate/(sum(v[1] for v in base_days.values())/sum(v[0] for v in base_days.values()))):.3f} "
              f"PF={r.rand_pf:.3f}) → {verdict}")
    print("\n[OK] research/prev_move_oos.json")


if __name__ == "__main__":
    main()
