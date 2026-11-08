# -*- coding: utf-8 -*-
"""
research/holygrail_regime_probe.py — 圣杯探针 v3：截面共振根部治疗 + 稳定性检验

## v2 已证实的结构性矛盾（2026-10-28 全量 5225 只 / 513 个交易日）
命中倍数最高的超跌类（os_g4 1.3810 / reversal 1.2921 / oversold 1.1566）
    组合日 PF 全部 <0.65、日均 -1%、Sharpe <0  → 截面共振
组合日唯一盈利的 r31_breakout（PF_day 1.815 / 日均+1.09% / Sharpe 2.84）
    命中倍数仅 0.9490  → 卡在「命中>=1.15」这一条
=> 两条门槛互斥，n_factors=0 的真因

## v3 要回答的两个问题
Q1 超跌类能否救？截面共振是**市场级**效应（大跌日所有超跌票同步走弱），
   那么在「当日市场不大跌」的子集里，超跌信号的组合日 PF 能否 >=1.30？
   市场状态用**当日全市场涨跌幅均值**（T 日收盘已知，无前视）刻画，不引外部数据。
Q2 r31_breakout 的 PF_day 1.815 是真 edge 还是 Bull 区间偶然？
   按日期三等分做子区间稳定性检验。

## 输出
每个因子 × 6 个日子集：ALL / CALM(mkt>=-1%) / CRASH(mkt<-1%) / P1 / P2 / P3
指标：n、命中倍数、PF事件、PF组合日、日均%、Sharpe(ann)、t
门控（铁律）：n>=200 且 命中倍数>=1.15 且 PF_day>=1.30 且 t>=2.0

## 铁律
只读 daily；不改任何生产代码；不 git 提交。
"""
from __future__ import annotations
import os, glob, json, math
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT_JSON = os.path.join(PROJ, "backtest_cache", "holygrail_regime_probe.json")

TEST_START = "2024-07-01"
MAX_T5_GAP = 9
MAX_T1_GAP = 4
LIMIT = int(os.environ.get("HG_LIMIT", "0"))
CRASH_THR = float(os.environ.get("HG_CRASH", "-0.01"))

MIN_N = 200
GATE_HIT, GATE_PF, GATE_T = 1.15, 1.30, 2.0

KEEP = ["oversold", "os_g3", "os_g4", "reversal", "reversal_scan_unified",
        "r31_breakout", "r31_breakout_v2", "r31_breakout_ma20",
        "pullback_lowvol_2560", "d47_capitulation",
        "dual_stabilize_2560", "low_vola_consolid"]


def limit_of(code):
    return board_limit_pct(code)



def sh(a, k, fill=None):
    a = np.asarray(a)
    if k <= 0:
        return a
    if fill is None:
        fill = False if a.dtype == bool else np.nan
    out = np.full(len(a), fill, dtype=a.dtype)
    out[k:] = a[:-k]
    return out


def roll_mean(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).mean().to_numpy()


def roll_std(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).std().to_numpy()


def roll_max(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).max().to_numpy()


def pf(vals):
    v = np.asarray(vals, float)
    v = v[~np.isnan(v)]
    pos, neg = v[v > 0].sum(), -v[v < 0].sum()
    if neg <= 0:
        return 99.0 if pos > 0 else 0.0
    return float(pos / neg) if pos > 0 else 0.0


def build_signals(close, open_, high, low, vol, lim):
    n = len(close)
    prev = np.concatenate([[np.nan], close[:-1]])
    pct = close / prev - 1.0
    pct[0] = np.nan
    ret = pct
    zt_thr = lim - 0.005
    is_zt = ret >= zt_thr
    is_dt = ret <= -zt_thr
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    vol20 = pd.Series(vol).rolling(20).mean().shift(1).to_numpy(float)
    volr5 = vol / np.where(vol5 > 0, vol5, np.nan)
    volr20 = vol / np.where(vol20 > 0, vol20, np.nan)
    vma5 = pd.Series(vol).rolling(5).mean().to_numpy(float)
    vma60 = pd.Series(vol).rolling(60, min_periods=60).mean().to_numpy(float)
    ma20 = roll_mean(close, 20); ma25 = roll_mean(close, 25); ma60 = roll_mean(close, 60)
    hi20 = roll_max(high, 20)
    ret20 = np.full(n, np.nan)
    if n > 20:
        ret20[20:] = close[20:] / close[:-20] - 1.0
    sd10 = sh(roll_std(ret, 10), 1)
    gene = pd.Series(is_zt.astype(float)).rolling(20, min_periods=1).max().to_numpy() > 0
    down = (ret < 0).astype(np.int64)
    streak = np.zeros(n, np.int64); cur = 0
    for i in range(n):
        cur = cur + 1 if down[i] else 0
        streak[i] = cur
    vol5_sum = pd.Series(vol).rolling(5).sum().to_numpy(float)
    vol5_prev5 = pd.Series(vol).rolling(10).sum().to_numpy(float) - vol5_sum

    s = {}
    s["oversold"] = (pct <= -0.04) & (volr5 >= 1.5)
    s["os_g3"] = (pct <= -0.05) & (pct > -0.07) & (volr5 >= 1.5)
    s["os_g4"] = (pct <= -0.07) & (~is_dt) & (volr5 >= 1.5)
    s["reversal"] = (pct <= -0.05) & (pct >= -0.08) & (~is_dt)
    s["reversal_scan_unified"] = (pct > -0.08) & (pct <= -0.03) & (~is_dt)
    s["r31_breakout"] = (~np.isnan(sd10)) & (sd10 < 0.01) & (ret > 0.03)
    s["r31_breakout_v2"] = s["r31_breakout"] & (volr5 >= 1.5) & (ret < 0.095)
    s["r31_breakout_ma20"] = s["r31_breakout"] & (close > ma20)
    prev5_above = np.ones(n, bool)
    for k in range(1, 6):
        prev5_above &= (sh(close, k) > sh(ma25, k))
    prev5_above[:6] = False
    near_ma25 = (~np.isnan(ma25)) & (np.abs(close - ma25) / ma25 <= 0.03) & (close <= ma25 * 1.03)
    pullback = near_ma25 & prev5_above
    s["pullback_lowvol_2560"] = (pullback & (vma5 < vma60) & (close >= ma25 * 0.97) & (vol < vma5))
    s["d47_capitulation"] = (streak >= 5) & (ret20 < -0.25) & (vol5_sum < 0.8 * vol5_prev5)
    s["dual_stabilize_2560"] = (pullback & (~np.isnan(vma60))
                                & (np.abs(vma5 - vma60) / np.maximum(vma60, 1e-9) <= 0.10)
                                & (vol > vma5 * 1.10))
    s["low_vola_consolid"] = (~np.isnan(sd10)) & (sd10 < 0.015) & (close > ma20) & (volr5 >= 1.3)
    return s


class Acc:
    def __init__(self):
        self.d = []; self.lab = []; self.rh = []; self.r5 = []

    def add(self, d, lab, rh, r5):
        self.d.append(d); self.lab.append(lab); self.rh.append(rh); self.r5.append(r5)


def fin(acc):
    d = np.concatenate(acc.d); lab = np.concatenate(acc.lab).astype(float)
    rh = np.concatenate(acc.rh); r5 = np.concatenate(acc.r5)
    ok = ~np.isnan(rh)
    return d[ok], lab[ok], rh[ok], r5[ok]


def metrics(d, lab, rh, r5, bd, blab, brh):
    """bd/blab/brh = 同日子集下的基准。"""
    n = len(rh)
    if n == 0:
        return None
    ud, inv = np.unique(d, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(ud)).astype(float)
    dmean = np.bincount(inv, weights=rh, minlength=len(ud)) / cnt
    sharpe = float(dmean.mean() / dmean.std(ddof=1) * math.sqrt(252)) if len(dmean) > 1 and dmean.std(ddof=1) > 0 else 0.0
    vb = brh.var(ddof=1) if len(brh) > 1 else 0.0
    se = math.sqrt(max(rh.var(ddof=1), 0) / n + max(vb, 0) / len(brh)) if n > 1 and len(brh) > 1 else 0.0
    t = float((rh.mean() - brh.mean()) / se) if se > 0 else 0.0
    hit = float(lab.mean()) / blab.mean() if blab.mean() > 0 else float("nan")
    return {"n": int(n), "hit_multiple": round(hit, 4), "pf_event": round(pf(rh), 4),
            "pf_day": round(pf(dmean), 4), "mean_day_pct": round(float(dmean.mean()) * 100, 4),
            "sharpe_ann": round(sharpe, 2), "t_stat": round(t, 2), "n_days": int(len(ud))}


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:LIMIT]
    s_num = np.datetime64(TEST_START, "D").astype(np.int64)
    base = Acc(); accs = {}
    mkt_sum = {}; mkt_cnt = {}

    for fi, fp in enumerate(files):
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, dtype={"日期": str}).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < 70:
            continue
        close = df["收盘"].to_numpy(float); open_ = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float); low = df["最低"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
        n = len(close); lim = limit_of(code)
        idx = np.arange(1, n - 5)
        valid = (dnum[idx] >= s_num) & (dnum[idx + 5] - dnum[idx] <= MAX_T5_GAP) \
                & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
        idx = idx[valid]
        if idx.size == 0:
            continue
        prev = np.concatenate([[np.nan], close[:-1]])
        pct = close / prev - 1.0; pct[0] = np.nan
        r1 = close[idx + 1] / close[idx] - 1.0
        r5 = close[idx + 5] / close[idx] - 1.0
        zt_thr = lim - 0.005
        ret_hg = np.where(pct[idx + 1] >= zt_thr, r1, r5)
        not_zt_T = pct[idx] < zt_thr
        label = (not_zt_T & ((open_[idx + 1] > close[idx]) | (pct[idx + 1] >= zt_thr))
                 & ((np.concatenate([[0], np.cumsum((close[1:] > close[:-1]).astype(np.int64))])[idx + 5]
                     - np.concatenate([[0], np.cumsum((close[1:] > close[:-1]).astype(np.int64))])[idx]) >= 2))
        buyable = not_zt_T & (~np.isnan(ret_hg)) & (~np.isnan(r5))
        base.add(dnum[idx][buyable], label[buyable], ret_hg[buyable], r5[buyable])
        # 市场状态：当日全市场涨跌幅均值（T 收盘已知）
        pk = pct[idx][buyable]; dk = dnum[idx][buyable]
        for a, b in zip(dk.tolist(), pk.tolist()):
            mkt_sum[a] = mkt_sum.get(a, 0.0) + (b if not math.isnan(b) else 0.0)
            mkt_cnt[a] = mkt_cnt.get(a, 0) + (0 if math.isnan(b) else 1)
        sig = build_signals(close, open_, high, low, vol, lim)
        for nm in KEEP:
            m = buyable & sig[nm][idx]
            if not m.any():
                continue
            accs.setdefault(nm, Acc()).add(dnum[idx][m], label[m], ret_hg[m], r5[m])
        if (fi + 1) % 800 == 0:
            print("  ...%d/%d" % (fi + 1, len(files)), flush=True)

    bd, blab, brh, br5 = fin(base)
    mkt = {k: mkt_sum[k] / max(mkt_cnt.get(k, 1), 1) for k in mkt_sum}
    ud_all = np.unique(bd)
    calm = np.array([mkt.get(int(x), 0.0) >= CRASH_THR for x in ud_all])
    ud_sorted = np.sort(ud_all)
    p1 = np.array([x < ud_sorted[len(ud_sorted) // 3] for x in ud_all])
    p2 = np.array([(x >= ud_sorted[len(ud_sorted) // 3]) and (x < ud_sorted[2 * len(ud_sorted) // 3]) for x in ud_all])
    p3 = np.array([x >= ud_sorted[2 * len(ud_sorted) // 3] for x in ud_all])
    subsets = {"ALL": np.ones(len(ud_all), bool), "CALM": calm, "CRASH": ~calm,
               "P1": p1, "P2": p2, "P3": p3}
    keep_days = {k: set(ud_all[v].tolist()) for k, v in subsets.items()}

    print("=" * 92)
    print("[基准·可买子集] n=%d 交易日=%d 圣杯正例率=%.4f 组合日PF=%.3f 日均=%.4f%%"
          % (len(brh), len(ud_all), blab.mean(), pf(np.bincount(np.unique(bd, return_inverse=True)[1],
             weights=brh) / np.bincount(np.unique(bd, return_inverse=True)[1])),
             (np.bincount(np.unique(bd, return_inverse=True)[1], weights=brh)
              / np.bincount(np.unique(bd, return_inverse=True)[1])).mean() * 100))
    print("市场大跌日(全市场均值<%.0f%%)占比: %.1f%%" % (CRASH_THR * 100, 100 * (~calm).mean()))
    print("=" * 92)

    out = {"crash_thr": CRASH_THR, "baseline": {}, "factors": {}}
    for sk, _ in subsets.items():
        m = np.isin(bd, np.array(sorted(keep_days[sk])))
        dmean = np.bincount(np.unique(bd[m], return_inverse=True)[1], weights=brh[m]) / \
            np.bincount(np.unique(bd[m], return_inverse=True)[1])
        out["baseline"][sk] = {"n": int(m.sum()), "pos_rate": round(float(blab[m].mean()), 4),
                               "pf_day": round(pf(dmean), 4),
                               "mean_day_pct": round(float(dmean.mean()) * 100, 4)}

    hdr = "%-22s %-5s %7s %8s %8s %8s %8s %7s %s"
    print(hdr % ("因子", "子集", "n", "命中倍数", "PF事件", "PF组合日", "日均%", "t", "门控"))
    for nm in KEEP:
        if nm not in accs:
            continue
        d, lab, rh, r5 = fin(accs[nm])
        out["factors"][nm] = {}
        for sk, _ in subsets.items():
            days = keep_days[sk]
            fm = np.isin(d, np.array(sorted(days)))
            bm = np.isin(bd, np.array(sorted(days)))
            if fm.sum() < MIN_N or bm.sum() < MIN_N:
                continue
            r = metrics(d[fm], lab[fm], rh[fm], r5[fm], bd[bm], blab[bm], brh[bm])
            if r is None:
                continue
            r["GATE"] = bool(r["hit_multiple"] >= GATE_HIT and r["pf_day"] >= GATE_PF and r["t_stat"] >= GATE_T)
            out["factors"][nm][sk] = r
            print(hdr % (nm, sk, r["n"], "%.4f" % r["hit_multiple"], "%.3f" % r["pf_event"],
                         "%.3f" % r["pf_day"], "%.4f" % r["mean_day_pct"], "%.2f" % r["t_stat"],
                         "PASS" if r["GATE"] else "-"))
        print("-" * 92)
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[out] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
