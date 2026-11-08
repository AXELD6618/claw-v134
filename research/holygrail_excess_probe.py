# -*- coding: utf-8 -*-
"""
research/holygrail_excess_probe.py — 圣杯探针 v4：按日配对超额（扣 Beta）

## 为什么必须做 v4
v3 发现：基准在「大跌日」的组合日 PF=1.506、日均+0.572%（跌后反弹是市场级 Beta）。
于是任何"跌日买入"类信号都能轻松跨过「绝对 PF_day>=1.30」这条门槛，
但它的 PF 其实**低于同日条件基准** —— 绝对门槛会把 Beta 误判成 alpha。

## v4 的正确口径
对每个交易日 d：
    excess_d = mean(信号股在 d 的 ret_hg) - mean(可买子集全部股在 d 的 ret_hg)
然后对这个**按日配对**的超额序列做检验：
    - mean_excess_bp  日均超额（基点）
    - t_excess        配对 t（n = 交易日数，不是事件数！）
    - sharpe_excess   超额序列年化 Sharpe
    - win_rate_day    超额为正的交易日占比
    - PF_excess       超额序列的盈亏比（一致性）
同时保留圣杯命中倍数（事件级，vs 同期条件基准）。

## 门控（v4）
n_days>=100 且 命中倍数>=1.15 且 t_excess>=2.0 且 mean_excess_bp>0
（PF_day 改为相对条件基线的 PF_day_ratio = pf_day_factor / pf_day_base 一并报告）

## 铁律
只读 daily；不改任何生产代码；不 git 提交。
"""
from __future__ import annotations
import os, glob, json, math
import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT_JSON = os.path.join(PROJ, "backtest_cache", "holygrail_excess_probe.json")

TEST_START = "2024-07-01"
MAX_T5_GAP = 9
MAX_T1_GAP = 4
LIMIT = int(os.environ.get("HG_LIMIT", "0"))
MIN_DAYS = 100
GATE_HIT, GATE_T = 1.15, 2.0

import sys
sys.path.insert(0, os.path.join(PROJ, "research"))
from holygrail_align_probe import build_signals, limit_of  # noqa: E402


class Acc:
    def __init__(self):
        self.d = []; self.lab = []; self.rh = []

    def add(self, d, lab, rh):
        self.d.append(d); self.lab.append(lab); self.rh.append(rh)

    def fin(self):
        d = np.concatenate(self.d); lab = np.concatenate(self.lab).astype(float)
        rh = np.concatenate(self.rh)
        ok = ~np.isnan(rh)
        return d[ok], lab[ok], rh[ok]


def day_agg(d, rh):
    ud, inv = np.unique(d, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(ud)).astype(float)
    return ud, np.bincount(inv, weights=rh, minlength=len(ud)) / cnt, cnt


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:LIMIT]
    s_num = np.datetime64(TEST_START, "D").astype(np.int64)
    base = Acc(); accs = {}; names = None

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
        cdiff = np.concatenate([[0], np.cumsum((close[1:] > close[:-1]).astype(np.int64))])
        label = (not_zt_T & ((open_[idx + 1] > close[idx]) | (pct[idx + 1] >= zt_thr))
                 & ((cdiff[idx + 5] - cdiff[idx]) >= 2))
        buyable = not_zt_T & (~np.isnan(ret_hg))
        base.add(dnum[idx][buyable], label[buyable], ret_hg[buyable])
        sig = build_signals(close, open_, high, low, vol, lim)
        if names is None:
            names = sorted(sig.keys())
        for nm in names:
            m = buyable & sig[nm][idx]
            if not m.any():
                continue
            accs.setdefault(nm, Acc()).add(dnum[idx][m], label[m], ret_hg[m])
        if (fi + 1) % 800 == 0:
            print("  ...%d/%d" % (fi + 1, len(files)), flush=True)

    bd, blab, brh = base.fin()
    bud, bmean, bcnt = day_agg(bd, brh)
    bmap = dict(zip(bud.tolist(), bmean.tolist()))
    print("=" * 104)
    print("[基准·可买子集] n=%d 交易日=%d 圣杯正例率=%.4f 日均=%.4f%%"
          % (len(brh), len(bud), blab.mean(), bmean.mean() * 100))
    print("=" * 104)

    out = {"test_start": TEST_START, "n_files": len(files),
           "baseline": {"n": int(len(brh)), "n_days": int(len(bud)),
                        "pos_rate": round(float(blab.mean()), 4),
                        "mean_day_pct": round(float(bmean.mean()) * 100, 4)},
           "factors": {}}
    rows = []
    for nm in (names or []):
        if nm not in accs:
            continue
        d, lab, rh = accs[nm].fin()
        ud, fmean, fcnt = day_agg(d, rh)
        # 按日配对：只保留两端都有数据的交易日
        common = np.isin(ud, bud)
        if common.sum() < MIN_DAYS:
            continue
        du = ud[common]; fv = fmean[common]
        bv = np.array([bmap[int(x)] for x in du.tolist()])
        ex = fv - bv
        nd = len(ex)
        mean_bp = float(ex.mean() * 10000)
        sd = ex.std(ddof=1) if nd > 1 else 0.0
        t = float(ex.mean() / (sd / math.sqrt(nd))) if sd > 0 else 0.0
        sharpe = float(ex.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0
        wr = float((ex > 0).mean())
        pos = ex[ex > 0].sum(); neg = -ex[ex < 0].sum()
        pf_ex = float(pos / neg) if neg > 0 else (99.0 if pos > 0 else 0.0)
        # 同期条件基准
        bm = np.isin(bd, du)
        pos_rate = float(lab[np.isin(d, du)].mean())
        base_rate = float(blab[bm].mean())
        hit = pos_rate / base_rate if base_rate > 0 else float("nan")
        # 子区间（三等分）稳定性
        q = np.array_split(np.argsort(np.argsort(du)), 3)
        stab = []
        for gi, gi_idx in enumerate(q):
            e = ex[gi_idx]
            stab.append(round(float(e.mean() * 10000), 1))
        rec = {"n": int(len(rh)), "n_days": int(nd), "hit_multiple": round(hit, 4),
               "mean_excess_bp": round(mean_bp, 2), "t_excess": round(t, 2),
               "sharpe_excess": round(sharpe, 2), "win_rate_day": round(wr, 4),
               "pf_excess": round(pf_ex, 3),
               "excess_p1_bp": stab[0], "excess_p2_bp": stab[1], "excess_p3_bp": stab[2],
               "monotone_ok": bool(all(x > 0 for x in stab))}
        rec["GATE"] = bool(rec["hit_multiple"] >= GATE_HIT and rec["t_excess"] >= GATE_T
                           and rec["mean_excess_bp"] > 0 and rec["monotone_ok"])
        rec["GATE_LOOSE"] = bool(rec["t_excess"] >= GATE_T and rec["mean_excess_bp"] > 0)
        out["factors"][nm] = rec
        rows.append((nm, rec))
    rows.sort(key=lambda x: -x[1]["mean_excess_bp"])
    hdr = "%-22s %7s %6s %8s %9s %8s %7s %8s %8s %6s %s"
    print(hdr % ("因子", "n", "天数", "命中倍数", "日均超额bp", "t(配对)", "Sharpe", "日胜率", "PF超额", "三段", "门控"))
    for nm, r in rows:
        seg = "%d/%d/%d" % (r["excess_p1_bp"], r["excess_p2_bp"], r["excess_p3_bp"])
        print(hdr % (nm, r["n"], r["n_days"], "%.4f" % r["hit_multiple"], "%.2f" % r["mean_excess_bp"],
                     "%.2f" % r["t_excess"], "%.2f" % r["sharpe_excess"], "%.4f" % r["win_rate_day"],
                     "%.3f" % r["pf_excess"], seg,
                     "PASS" if r["GATE"] else ("T+" if r["GATE_LOOSE"] else "-")))
    print("\n[门控·v4] n_days>=100 且 命中倍数>=1.15 且 t_excess>=2.0 且 日均超额>0 且 三个子区间均>0")
    print("[T+]=仅超额显著（有真选股 alpha）但圣杯命中倍数未达 1.15")
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[out] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
