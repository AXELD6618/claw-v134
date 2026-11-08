# -*- coding: utf-8 -*-
"""
「T 日涨跌幅分桶 → T+1/T+5 收益分布」实证（prev_down_study 的第二层验证）

目的：排除"波动率放大"假象。只看 P(涨20%) 会被高波动股骗——
大跌股涨20%概率高，但跌20%概率同样高。必须看：
  均值 / 中位数 / 标准差 / 夏普代理 均值÷std / 左尾 P(≤-10%) / 右尾 P(≥+10%)

若均值随分桶单调变化且左右尾同向放大 → 纯波动，不可部署。
若均值为正且左尾未同步放大 → 真实方向性 edge。
"""
import glob
import os
from collections import defaultdict

import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

DAILY = r"E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
TRAIN_END = "2024-06-30"
BUCKETS = [
    ("<-7%", -9.99, -0.07), ("-7~-5%", -0.07, -0.05), ("-5~-3%", -0.05, -0.03),
    ("-3~-1%", -0.03, -0.01), ("-1~0%", -0.01, 0.0), ("0~1%", 0.0, 0.01),
    ("1~3%", 0.01, 0.03), ("3~5%", 0.03, 0.05), ("5~7%", 0.05, 0.07),
    ("7~9.5%", 0.07, 0.095),
]
NB = len(BUCKETS)
# 直方图：-60%~+60%，步长 0.5% → 240 bin；最后一格收 +inf，第一格收 -inf
HLO, HHI, HSTEP = -0.60, 0.60, 0.005
NH = int((HHI - HLO) / HSTEP) + 2


def hbin(x):
    i = np.floor((x - HLO) / HSTEP).astype(np.int32) + 1
    return np.clip(i, 0, NH - 1)


def limit_ratio(code):
    return board_limit_pct(code)



HORIZONS = {"t1": 1, "t5": 5}


def process(code, ssum, scnt, shist):
    p = os.path.join(DAILY, f"{code}.csv")
    try:
        df = pd.read_csv(p, usecols=["日期", "开盘", "收盘"])
    except Exception:
        return
    if len(df) < 40:
        return
    df = df.dropna()
    d = df["日期"].astype(str).str.slice(0, 10).values
    c = df["收盘"].values.astype(np.float64)
    n = len(c)
    prev = np.concatenate([[np.nan], c[:-1]])
    ret1 = c / prev - 1.0
    ma20 = pd.Series(c).rolling(20).mean().values
    lim = limit_ratio(code)

    idx = np.arange(30, n - 5)
    if len(idx) == 0:
        return
    r1 = ret1[idx]
    ok = (r1 < lim - 0.005) & np.isfinite(r1)
    idx, r1 = idx[ok], r1[ok]
    if len(idx) == 0:
        return

    bk = np.full(len(r1), -1, np.int8)
    for b, (_, lo, hi) in enumerate(BUCKETS):
        bk[(r1 >= lo) & (r1 < hi)] = b
    strong = (c[idx] > ma20[idx]).astype(np.int8)
    is_train = (d[idx] <= TRAIN_END).astype(np.int8)
    c0 = c[idx]

    for hname, h in HORIZONS.items():
        r = c[idx + h] / c0 - 1.0
        for split in (0, 1, 2):
            m = np.ones(len(idx), bool) if split == 0 else (
                is_train == 1 if split == 1 else is_train == 0)
            for sv in (0, 1, 2):
                ms = m if sv == 0 else (m & (strong == (1 if sv == 1 else 0)))
                for b in range(NB):
                    mb = ms & (bk == b)
                    k = int(mb.sum())
                    if k == 0:
                        continue
                    v = r[mb]
                    key = (hname, split, sv, b)
                    ssum[key] += float(v.sum())
                    ssum[(hname, split, sv, b, "sq")] += float((v * v).sum())
                    scnt[key] += k
                    np.add.at(shist[key], hbin(v), 1)


def quantile(hist, total, q):
    csum = np.cumsum(hist)
    t = q * total
    i = int(np.searchsorted(csum, t))
    i = min(max(i, 0), NH - 1)
    return HLO + (i - 1) * HSTEP + HSTEP / 2.0


def main():
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    ssum = defaultdict(float)
    scnt = defaultdict(int)
    shist = defaultdict(lambda: np.zeros(NH, np.int64))
    for i, code in enumerate(codes):
        process(code, ssum, scnt, shist)
        if (i + 1) % 1500 == 0:
            print(f"  ...{i+1}/{len(codes)}", flush=True)

    rows = []
    for hname in HORIZONS:
        for split, sname in ((0, "ALL"), (1, "TRAIN"), (2, "OOS")):
            for sv, vname in ((0, "全部"), (1, "MA20上"), (2, "MA20下")):
                for b, (bname, _, _) in enumerate(BUCKETS):
                    k = scnt[(hname, split, sv, b)]
                    if k < 300:
                        continue
                    s = ssum[(hname, split, sv, b)]
                    sq = ssum[(hname, split, sv, b, "sq")]
                    mean = s / k
                    sd = np.sqrt(max(sq / k - mean * mean, 0.0))
                    hist = shist[(hname, split, sv, b)]
                    rows.append({
                        "h": hname, "split": sname, "layer": vname, "bucket": bname, "n": k,
                        "mean": mean, "median": quantile(hist, k, 0.5),
                        "p05": quantile(hist, k, 0.05), "p95": quantile(hist, k, 0.95),
                        "sd": sd, "sharpe": mean / sd if sd > 0 else np.nan,
                        "p_le_-10": hist[:hbin(np.array([-0.10]))[0]].sum() / k,
                        "p_ge_+10": hist[hbin(np.array([0.10]))[0]:].sum() / k,
                    })
    out = pd.DataFrame(rows)
    out.to_csv("research/prev_down_returns.csv", index=False, encoding="utf-8-sig")
    pd.set_option("display.width", 240, "display.max_rows", 400)
    for hname in HORIZONS:
        for split in ("ALL", "OOS"):
            sub = out[(out.h == hname) & (out.split == split)]
            if sub.empty:
                continue
            print(f"\n===== {hname} / {split} =====")
            for col in ("mean", "median", "sharpe", "p_le_-10", "p_ge_+10"):
                piv = sub.pivot_table(index="bucket", columns="layer", values=col, aggfunc="first")
                piv = piv.reindex([b[0] for b in BUCKETS])
                print(f"-- {col} --")
                print((piv * (100 if col != "sharpe" else 1)).round(3).to_string())
    print("\n[OK] research/prev_down_returns.csv")


if __name__ == "__main__":
    main()
