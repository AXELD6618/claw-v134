# -*- coding: utf-8 -*-
"""
「启动前一日下跌」形态实证研究（用户主任务1）

口径澄清：用户说"起爆(涨停)在启动前一个交易日下跌"。
  起爆日 = T+1，启动前一日 = T = 我们尾盘选股的当天。
  所以真正可部署的问题是：**T 日收阴(ret1<0) 是否提高 T+1 涨停/高开/连涨的概率**。
  这正好是 T 日收盘即可知的特征，无未来信息。

标签（全部从 T+1 起算，T 日未涨停=可买子集）：
  zt1        T+1 收盘涨停
  gapup      T+1 开盘涨幅 > 1%
  buy        T+1 高开或涨停（现行 BUY 口径近似）
  up3        T+1/T+2/T+3 三日均上涨
  cum5_10    T+1..T+5 累计 ≥ +10%
  cum5_20    T+1..T+5 累计 ≥ +20%（起爆级）

分层：ret1 分桶 × MA20 上/下（强势回调 vs 趋势下跌）。时间切分 TRAIN/OOS。
"""
import glob
import os
import sys

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
LABELS = ["zt1", "gapup", "buy", "up3", "cum5_10", "cum5_20"]


def limit_ratio(code):
    return board_limit_pct(code)



def bucket_of(r):
    for i, (_, lo, hi) in enumerate(BUCKETS):
        if lo <= r < hi:
            return i
    return -1


def process(code, acc):
    p = os.path.join(DAILY, f"{code}.csv")
    try:
        df = pd.read_csv(p, usecols=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
    except Exception:
        return 0
    if len(df) < 40:
        return 0
    df = df.dropna()
    d = df["日期"].astype(str).str.slice(0, 10).values
    o = df["开盘"].values.astype(np.float64)
    c = df["收盘"].values.astype(np.float64)
    n = len(c)
    if n < 40:
        return 0

    prev = np.concatenate([[np.nan], c[:-1]])
    ret1 = c / prev - 1.0
    ma20 = pd.Series(c).rolling(20).mean().values
    lim = limit_ratio(code)

    # 未来 5 日窗口需要 i+5 存在；T 日索引 i
    idx = np.arange(30, n - 5)
    if len(idx) == 0:
        return 0

    r1 = ret1[idx]
    # 可买子集：T 日未涨停
    buyable = (r1 < lim - 0.005) & np.isfinite(r1)
    idx, r1 = idx[buyable], r1[buyable]
    if len(idx) == 0:
        return 0

    c0 = c[idx]
    nxt_o = o[idx + 1]
    nxt_c = c[idx + 1]
    dT = d[idx]

    zt_lim = c0 * (1.0 + lim)
    zt1 = (nxt_c >= zt_lim - 0.011).astype(np.int8)
    gapup = (nxt_o / c0 - 1.0 > 0.01).astype(np.int8)
    buy = ((gapup == 1) | (zt1 == 1)).astype(np.int8)
    up3 = ((c[idx + 1] > c0) & (c[idx + 2] > c[idx + 1]) &
           (c[idx + 3] > c[idx + 2])).astype(np.int8)
    cum5 = (c[idx + 5] / c0 - 1.0)
    cum5_10 = (cum5 >= 0.10).astype(np.int8)
    cum5_20 = (cum5 >= 0.20).astype(np.int8)

    strong = (c0 > ma20[idx]).astype(np.int8)
    is_train = (dT <= TRAIN_END).astype(np.int8)

    bk = np.array([bucket_of(x) for x in r1], dtype=np.int8)

    for i, name in enumerate(LABELS):
        y = [zt1, gapup, buy, up3, cum5_10, cum5_20][i]
        for split in (0, 1, 2):          # 0=ALL 1=TRAIN 2=OOS
            m = np.ones(len(idx), bool) if split == 0 else (
                is_train == 1 if split == 1 else is_train == 0)
            for sv in (0, 1, 2):          # 0=全部 1=MA20上 2=MA20下
                ms = m if sv == 0 else (m & (strong == (1 if sv == 1 else 0)))
                if ms.sum() == 0:
                    continue
                for b in range(len(BUCKETS)):
                    mb = ms & (bk == b)
                    k = int(mb.sum())
                    if k == 0:
                        continue
                    h = int(y[mb].sum())
                    acc[(name, split, sv, b, 0)] += k
                    acc[(name, split, sv, b, 1)] += h
    return len(idx)


def main():
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    print(f"股票数 {len(codes)}", flush=True)
    from collections import defaultdict
    acc = defaultdict(int)
    tot = 0
    for i, code in enumerate(codes):
        tot += process(code, acc)
        if (i + 1) % 1000 == 0:
            print(f"  ...{i+1}/{len(codes)}  stock-day={tot:,}", flush=True)
    print(f"累计可买 stock-day {tot:,}\n", flush=True)

    rows = []
    for name in LABELS:
        for split, sname in ((0, "ALL"), (1, "TRAIN"), (2, "OOS")):
            for sv, vname in ((0, "全部"), (1, "MA20上"), (2, "MA20下")):
                base_k = sum(acc[(name, split, sv, b, 0)] for b in range(len(BUCKETS)))
                base_h = sum(acc[(name, split, sv, b, 1)] for b in range(len(BUCKETS)))
                if base_k == 0:
                    continue
                base = base_h / base_k
                for b, (bname, _, _) in enumerate(BUCKETS):
                    k = acc[(name, split, sv, b, 0)]
                    h = acc[(name, split, sv, b, 1)]
                    if k < 200:
                        continue
                    p = h / k
                    lift = p / base if base > 0 else np.nan
                    se = np.sqrt(base * (1 - base) / k) if 0 < base < 1 else np.nan
                    z = (p - base) / se if se and se > 0 else np.nan
                    rows.append({"label": name, "split": sname, "layer": vname,
                                 "bucket": bname, "n": k, "hits": h,
                                 "rate": p, "base": base, "lift": lift, "z": z})
    out = pd.DataFrame(rows)
    out.to_csv("research/prev_down_study.csv", index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 200, "display.max_rows", 400)
    for name in LABELS:
        for split in ("ALL", "OOS"):
            sub = out[(out.label == name) & (out.split == split)]
            if sub.empty:
                continue
            print(f"\n===== {name} / {split} =====")
            piv = sub.pivot_table(index="bucket", columns="layer",
                                  values="lift", aggfunc="first")
            piv = piv.reindex([b[0] for b in BUCKETS])
            nb = sub[sub.layer == "全部"].set_index("bucket")["n"].reindex([b[0] for b in BUCKETS])
            base = sub[sub.layer == "全部"]["base"].iloc[0] if not sub[sub.layer == "全部"].empty else float("nan")
            piv.insert(0, "n", nb)
            print(f"  基线率 {base:.4f}")
            print(piv.round(3).to_string())
    print("\n[OK] 落盘 research/prev_down_study.csv")


if __name__ == "__main__":
    main()
