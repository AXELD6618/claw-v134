# -*- coding: utf-8 -*-
"""
多源线索交叉验证引擎（Round34 核心）

问题：单一情报源已证伪（龙虎榜 PF 0.79、涨跌幅 PF 0.47~0.80）。
      那「多源共振」是否真的能提升？——必须测，不能假设。

线索定义（全部 lag=1，T 日尾盘可知）：
  L1 榜       : T-1 上龙虎榜                        (快变量 · 资金异动)
  L2 机构买   : T-1 机构专用席位净买 > 0             (快变量 · 机构方向)
  L3 增持     : 近 20 交易日有股东增持公告            (慢变量 · 内部人真金白银)
  L4 大宗溢价 : 近 5 日大宗加权溢价率 > 0             (慢变量 · 有人溢价收货)
  L5 无解禁   : 未来 30 自然日无解禁                  (排雷 · 不是线索是保险)

组合：
  S1 = L1              （单快）
  S2 = L2
  S3 = L3              （单慢）
  S4 = L4
  S12 = L1 & L2        （快×快共振）
  S34 = L3 & L4        （慢×慢共振）
  S1234 = (L1|L2) & (L3|L4)   （快×慢共振 ← 真正意义上的多源交叉）
  S1234L5 = S1234 & L5

验收（铁律不变）：hit_multiple ≥ 1.15 且 组合 PF ≥ 1.30，且必须优于随机基线。

用法：python research/lead_engine.py
"""
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OOS_START = "2024-07-01"
K = 10


def limit_ratio(code):
    return board_limit_pct(code)



def load_gz(p):
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return pd.read_csv(f, dtype={"code": str})
    except Exception:
        return pd.DataFrame()


def build_panel():
    """全市场可买 stock-day 面板（OOS 段）。"""
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    D, C, R, LOW, BUY, T1, T5 = [], [], [], [], [], [], []
    for k, code in enumerate(codes):
        try:
            df = pd.read_csv(os.path.join(DAILY, f"{code}.csv"),
                             usecols=["日期", "开盘", "收盘"]).dropna()
        except Exception:
            continue
        if len(df) < 40:
            continue
        d = df["日期"].astype(str).str.slice(0, 10).values
        o = df["开盘"].values.astype(float)
        c = df["收盘"].values.astype(float)
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
        C.append(np.array([code] * len(idx)))
        R.append(ret1[idx].astype(np.float32))
        LOW.append((c0 < ma20[idx]))
        BUY.append((gapup | zt1).astype(np.float32))
        T1.append((c[idx + 1] / c0 - 1.0).astype(np.float32))
        T5.append((c[idx + 5] / c0 - 1.0).astype(np.float32))
        if (k + 1) % 1500 == 0:
            print(f"  panel ...{k+1}/{len(codes)}", flush=True)
    return pd.DataFrame({
        "date": np.concatenate(D), "code": np.concatenate(C),
        "ret1": np.concatenate(R), "low": np.concatenate(LOW),
        "buy": np.concatenate(BUY), "t1": np.concatenate(T1), "t5": np.concatenate(T5)})


def attach_leads(p):
    n = len(p)
    p["L1"] = 0
    p["L2"] = 0
    p["L3"] = 0
    p["L4"] = 0
    p["L5"] = 0

    # L1/L2：来自 intel_features（已 lag=1）
    feat = load_gz(os.path.join(INTEL, "intel_features.csv.gz"))
    if not feat.empty:
        feat["code"] = feat["code"].str.zfill(6)
        cols = [c for c in ("lhb_prev", "inst_net", "inst_net_cap") if c in feat.columns]
        f = feat[["code", "date"] + cols].copy()
        f = f.merge(p[["code", "date"]].assign(_i=np.arange(n)), on=["code", "date"], how="inner")
        if len(f):
            ii = f["_i"].values
            if "lhb_prev" in f.columns:
                p.loc[p.index[ii], "L1"] = (f["lhb_prev"].values > 0).astype(int)
            if "inst_net" in f.columns:
                p.loc[p.index[ii], "L2"] = (f["inst_net"].values > 0).astype(int)
        print(f"[L1/L2] 覆盖 {int(p['L1'].sum()):,} / {int(p['L2'].sum()):,} 行")

    # L3/L4：慢变量事件，按 code 展开到后续 20/5 个交易日
    ev = load_gz(os.path.join(INTEL, "intel_pool_events.csv.gz"))
    if not ev.empty:
        ev["code"] = ev["code"].str.zfill(6)
        # code → 该票在面板内的行索引（按日期有序）
        p_sorted = p.sort_values(["code", "date"])
        idx_by_code = p_sorted.groupby("code").indices
        date_order = p_sorted["date"].values
        for src, win, col in (("inc", 20, "L3"), ("blk", 5, "L4")):
            sub = ev[ev["src"] == src]
            if col == "L4" and "avg_px" in sub.columns:
                sub = sub[pd.to_numeric(sub["avg_px"], errors="coerce") > 0]  # 溢价
            hit = 0
            for code, g in sub.groupby("code"):
                rows = idx_by_code.get(code)
                if rows is None:
                    continue
                rd = date_order[rows]
                for d in g["date"].values:
                    j = np.searchsorted(rd, d, side="left")
                    if j >= len(rd):
                        continue
                    p.loc[p.index[rows[j:j + win]], col] = 1
                    hit += 1
            print(f"[{col}] 事件 {hit:,} 条已展开")

    # L5：未来 30 自然日无解禁（解禁日提前公告，T 日可知）
    lf = load_gz(os.path.join(INTEL, "intel_pool_lift.csv.gz"))
    if not lf.empty:
        lf["code"] = lf["code"].str.zfill(6)
        p_sorted = p.sort_values(["code", "date"])
        idx_by_code = p_sorted.groupby("code").indices
        date_order = p_sorted["date"].values
        nn = 0
        for code, g in lf.groupby("code"):
            rows = idx_by_code.get(code)
            if rows is None:
                continue
            rd = date_order[rows]
            for d in g["d"].values:
                j0 = np.searchsorted(rd, d, side="left")
                # 解禁日往前推 30 自然日内的交易日打标
                for j in range(max(0, j0 - 25), j0):
                    p.loc[p.index[rows[j]], "L5"] = 1
                    nn += 1
        print(f"[L5] 解禁窗口标记 {nn:,} 行")
    p["L5"] = (p["L5"] == 0).astype(int)   # 1 = 无解禁（安全）
    return p


def pf(x):
    x = np.asarray(x, float)
    return x[x > 0].sum() / abs(x[x < 0].sum()) if (x < 0).any() else np.nan


def main():
    print("[1/3] 建面板 ...", flush=True)
    p = build_panel()
    print(f"      {len(p):,} 行 / {p['date'].nunique()} 日 / {p['code'].nunique()} 只", flush=True)
    print("[2/3] 挂线索 ...", flush=True)
    p = attach_leads(p)
    p = p.sort_values("date", kind="mergesort").reset_index(drop=True)

    combos = {
        "S1_榜":            p["L1"] == 1,
        "S2_机构买":        p["L2"] == 1,
        "S3_增持":          p["L3"] == 1,
        "S4_大宗溢价":      p["L4"] == 1,
        "S12_榜×机构买":    (p["L1"] == 1) & (p["L2"] == 1),
        "S34_增持×大宗":    (p["L3"] == 1) & (p["L4"] == 1),
        "S1234_快×慢":      ((p["L1"] == 1) | (p["L2"] == 1)) & ((p["L3"] == 1) | (p["L4"] == 1)),
        "S1234L5_共振+无解禁": ((p["L1"] == 1) | (p["L2"] == 1)) & ((p["L3"] == 1) | (p["L4"] == 1)) & (p["L5"] == 1),
    }
    base = p["buy"].mean()
    print(f"\n[3/3] 基线 BUY 正例率 {base:.4f}\n")
    print(f"{'组合':22s} {'n':>8s} {'命中率':>8s} {'倍数':>6s} {'t1均值':>8s} {'PF':>6s} {'裁决':>10s}")
    rows = []
    for name, mk in combos.items():
        s = p[mk]
        if len(s) < 300:
            print(f"{name:22s} {len(s):8,d}  (样本不足)")
            continue
        hr = s["buy"].mean()
        hm = hr / base
        m1 = s["t1"].mean()
        f1 = pf(s["t1"])
        ok = (hm >= 1.15) and (f1 >= 1.30)
        v = "PROMOTE?" if ok else "REJECT"
        print(f"{name:22s} {len(s):8,d} {hr:8.4f} {hm:6.3f} {m1*100:+7.3f}% {f1:6.3f} {v:>10s}")
        rows.append({"combo": name, "n": int(len(s)), "hit": float(hr),
                     "hit_multiple": float(hm), "t1_mean": float(m1), "pf": float(f1), "verdict": v})

    # 随机基线（同采样量）
    rng = np.random.default_rng(20261019)
    rb = []
    for _ in range(5):
        ii = rng.choice(len(p), min(len(p), 200000), replace=False)
        rb.append(pf(p["t1"].values[ii]))
    rand_pf = float(np.mean(rb))
    print(f"\n随机基线 PF = {rand_pf:.3f}（20 万行抽样 ×5）")

    # ── 第二阶段：池内择时（慢变量选池 → 技术面选时）────────────────
    print("\n===== 池内择时：慢变量圈池 + 技术面排序 =====")
    pools = {"池=增持": p["L3"] == 1, "池=大宗溢价": p["L4"] == 1,
             "池=增持或大宗": (p["L3"] == 1) | (p["L4"] == 1)}
    stage2 = []
    for pname, pmk in pools.items():
        sub = p[pmk]
        if len(sub) < 2000:
            print(f"{pname}: 样本 {len(sub):,} 不足")
            continue
        for fname, score in (("ret1大(动量)", sub["ret1"].values),
                             ("ret1小(反转)", -sub["ret1"].values),
                             ("低位优先", np.where(sub["low"].values, 1.0, 0.0))):
            d2 = pd.factorize(sub["date"].values, sort=True)[0]
            cnt2 = np.bincount(d2)
            st2 = np.concatenate([[0], np.cumsum(cnt2)[:-1]])
            order = np.lexsort((-score, d2))
            rank2 = np.arange(len(sub)) - st2[d2[order]]
            mk = np.zeros(len(sub), bool)
            mk[order[rank2 < K]] = True
            s2 = sub[mk]
            if len(s2) < 200:
                continue
            hr2 = s2["buy"].mean()
            f2 = pf(s2["t1"].values)
            ok2 = (hr2 / base >= 1.15) and (f2 >= 1.30)
            print(f"{pname:12s} x {fname:12s} n={len(s2):6,d} 命中 {hr2:.4f} "
                  f"(x{hr2/base:.3f}) t1 {s2['t1'].mean()*100:+.3f}% "
                  f"PF {f2:.3f} {'PROMOTE?' if ok2 else 'REJECT'}")
            stage2.append({"pool": pname, "rank": fname, "n": int(len(s2)),
                           "hit": float(hr2), "hit_multiple": float(hr2 / base),
                           "t1_mean": float(s2["t1"].mean()), "pf": float(f2),
                           "verdict": "PROMOTE?" if ok2 else "REJECT"})

    with open(os.path.join(PROJ, "research", "lead_engine.json"), "w", encoding="utf-8") as f:
        json.dump({"base": float(base), "rows": rows, "stage2": stage2,
                   "rand_pf": rand_pf,
                   "coverage": {k: float(np.mean(v)) for k, v in combos.items()}},
                  f, ensure_ascii=False, indent=1)
    print("\n[OK] research/lead_engine.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
