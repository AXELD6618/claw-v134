# -*- coding: utf-8 -*-
"""
「停牌复牌连板」可埋伏性实证（Round34）

锚点案例：新华传媒 600825
  2026-09-04 停牌 → 2026-09-21 复牌，一字板 11 连（5.31 → 15.16，+185%），
  复牌后每日 open=high=low=close，开板前无量。
  停牌前 4 日连续放量 1.6~2.2 倍、累计仅 +5.1%。

要回答的问题：这类行情能不能提前埋伏？
  ① 复牌首日能否买进（一字板 = 开盘即涨停 = 不可成交）
  ② 停牌前特征能否识别（放量/涨幅/位置是否有区分度）
  ③ 若能识别，全市场命中率与 PF 是多少

方法：
  - 市场日历 = 000001.csv 的日期序列（平安银行极少停牌）
  - 个股缺失 ≥3 个交易日 → 判定停牌，缺口区间 = [前一 bar, 后一 bar)
  - 复牌首日涨幅 ≥ 涨停阈值-0.5% 且 open==close → 一字板
  - 停牌前特征用缺口前最后 5 个 bar 计算
输出：research/suspend_study.json + 控制台
"""
import glob
import json
import os

import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

DAILY = r"E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
CAL_FILE = os.path.join(DAILY, "000001.csv")


def limit_ratio(code):
    return board_limit_pct(code)



def calendar():
    df = pd.read_csv(CAL_FILE, usecols=["日期"])
    return sorted(df["日期"].astype(str).str.slice(0, 10).unique())


def main():
    cal = calendar()
    cidx = {d: i for i, d in enumerate(cal)}
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    print(f"日历 {len(cal)} 日（{cal[0]} ~ {cal[-1]}）/ 股票 {len(codes)}")

    cases = []
    for k, code in enumerate(codes):
        try:
            df = pd.read_csv(os.path.join(DAILY, f"{code}.csv"),
                             usecols=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
        except Exception:
            continue
        if len(df) < 60:
            continue
        df = df.dropna()
        ds = df["日期"].astype(str).str.slice(0, 10).values
        o = df["开盘"].values.astype(float)
        c = df["收盘"].values.astype(float)
        h = df["最高"].values.astype(float)
        lo = df["最低"].values.astype(float)
        v = df["成交量"].values.astype(float)
        n = len(c)
        pos = np.array([cidx.get(x, -1) for x in ds])
        if (pos < 0).any():
            continue
        gap = np.diff(pos) - 1          # 缺失的交易日数
        lim = limit_ratio(code)
        for i in np.where(gap >= 3)[0]:
            if i < 25 or i + 6 >= n:
                continue
            pre_c = c[i]
            rp_o, rp_c, rp_h, rp_l = o[i + 1], c[i + 1], h[i + 1], lo[i + 1]
            rp_ret = rp_c / pre_c - 1.0
            one_word = (abs(rp_o - rp_h) < 1e-6) and (abs(rp_o - rp_l) < 1e-6)
            rp_zt = rp_ret >= lim - 0.005
            # 复牌后连续涨停天数（一字板计）
            chain = 0
            for j in range(i + 1, min(n, i + 12)):
                if c[j] / c[j - 1] - 1.0 >= lim - 0.005:
                    chain += 1
                else:
                    break
            # 停牌前 5 日特征
            v20 = v[max(0, i - 20):i].mean()
            vr5 = v[i - 4:i + 1].mean() / v20 if v20 > 0 else np.nan
            ret5 = c[i] / c[i - 5] - 1.0 if i >= 5 else np.nan
            ret20 = c[i] / c[i - 20] - 1.0 if i >= 20 else np.nan
            ma20 = c[max(0, i - 20):i + 1].mean()
            above = c[i] > ma20
            hi60 = h[max(0, i - 60):i + 1].max()
            near_hi = c[i] >= hi60 * 0.97
            cases.append({
                "code": code, "last_day": ds[i], "resume_day": ds[i + 1],
                "gap_days": int(gap[i]), "pre_close": pre_c,
                "rp_ret": rp_ret, "rp_zt": bool(rp_zt), "one_word": bool(one_word),
                "chain": chain, "vr5": float(vr5) if np.isfinite(vr5) else None,
                "ret5": float(ret5) if np.isfinite(ret5) else None,
                "ret20": float(ret20) if np.isfinite(ret20) else None,
                "above_ma20": bool(above), "near_hi60": bool(near_hi),
            })
        if (k + 1) % 1500 == 0:
            print(f"  ...{k+1}/{len(codes)} 案例 {len(cases)}", flush=True)

    d = pd.DataFrame(cases)
    print(f"\n停牌事件（缺口≥3 交易日）共 {len(d):,} 例 / {d['code'].nunique()} 只")
    if d.empty:
        return 1

    print(f"\n=== ① 复牌首日能否买进 ===")
    print(f"  一字板占复牌首日比例: {d['one_word'].mean()*100:.2f}%")
    print(f"  复牌首日涨停比例:     {d['rp_zt'].mean()*100:.2f}%")
    buyable = d[~d["one_word"]]
    print(f"  非一字板（可成交）:   {len(buyable):,} 例，其中涨停 {buyable['rp_zt'].mean()*100:.2f}%")

    print(f"\n=== ② 连板分布（复牌后连续涨停天数）===")
    vc = d["chain"].value_counts().sort_index()
    for k2, v2 in vc.items():
        if k2 <= 12:
            print(f"  {k2:2d} 连板: {v2:5d} 例 ({v2/len(d)*100:5.2f}%)")

    big = d[d["chain"] >= 3]
    print(f"\n  ≥3 连板: {len(big):,} 例 ({len(big)/len(d)*100:.2f}%)，"
          f"其中一字板首日 {big['one_word'].mean()*100:.1f}%")

    print(f"\n=== ③ 停牌前特征是否有区分度（≥3连板 vs 其他）===")
    for col in ("vr5", "ret5", "ret20"):
        a = big[col].dropna()
        b = d[d["chain"] < 3][col].dropna()
        if len(a) < 20 or len(b) < 20:
            continue
        print(f"  {col:6s} 连板组均值 {a.mean():+.4f} vs 其他 {b.mean():+.4f} "
              f"（差 {a.mean()-b.mean():+.4f}）")
    for col in ("above_ma20", "near_hi60"):
        print(f"  {col:11s} 连板组 {big[col].mean()*100:5.1f}% vs 其他 "
              f"{d[d['chain']<3][col].mean()*100:5.1f}%")

    print(f"\n=== ④ 停牌前放量分档 → P(≥3连板) ===")
    dd = d.dropna(subset=["vr5"])
    base = (dd["chain"] >= 3).mean()
    print(f"  基线 P(≥3连板) = {base*100:.2f}%")
    for lo_, hi_ in [(0, 1), (1, 1.5), (1.5, 2), (2, 3), (3, 99)]:
        s = dd[(dd["vr5"] >= lo_) & (dd["vr5"] < hi_)]
        if len(s) < 30:
            continue
        p = (s["chain"] >= 3).mean()
        print(f"  放量 {lo_}~{hi_}×: n={len(s):5d} P={p*100:5.2f}% "
              f"lift={p/base:5.3f}")

    print(f"\n=== ⑤ 全市场概率：一天遇到一个'新华传媒'有多难 ===")
    span_days = (pd.to_datetime(cal[-1]) - pd.to_datetime(cal[0])).days
    per_day_big = len(big) / len(cal)
    print(f"  {len(cal)} 个交易日里 ≥3 连板复牌 {len(big)} 例 → 平均每交易日 {per_day_big:.3f} 例")
    print(f"  全市场 5000+ 只票，单只票在某日'即将停牌复牌连板'的概率约 "
          f"{len(big)/(len(codes)*len(cal))*100:.5f}%")

    out = {"n_events": len(d), "n_codes": int(d["code"].nunique()),
           "one_word_rate": float(d["one_word"].mean()),
           "rp_zt_rate": float(d["rp_zt"].mean()),
           "chain_ge3": int(len(big)), "chain_ge3_rate": float(len(big) / len(d)),
           "chain_ge3_one_word": float(big["one_word"].mean()) if len(big) else None}
    with open("research/suspend_study.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    d.to_csv("research/suspend_study.csv", index=False, encoding="utf-8-sig")
    print("\n[OK] research/suspend_study.json / .csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
