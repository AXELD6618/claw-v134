# -*- coding: utf-8 -*-
"""
research/r31_preregistered.py — r31_breakout 预注册假设检验（v5）

## 为什么必须"预注册"
前一轮 14 个条件化变体全部失败，且暴露病灶：
  裸版季度累计超额 2024H2 +33,546bp / 2025 +426bp / 2026 +1,658bp
  → 前两个季度占全部正贡献 69.3%，是**状态性暴露**不是持续 alpha。
若在已看到的 14 个结果里回头挑参数，就是事后选择偏差。
因此本脚本**先声明假设再测**，多重检验按声明数 Bonferroni 校正。

## 预注册假设（共 8 条，α_bonf = 0.05/8 = 0.00625）
H1 r31 × 缩量突破        volr5 ∈ [0.8, 1.5]        （依据：放量是这条因子上的负向条件）
H2 r31 × 更严缩量        volr5 < 1.0
H3 r31 × 市场宽度开      breadth(全市场 close>MA20 占比) >= 0.50
H4 r31 × 市场宽度更严    breadth >= 0.60
H5 r31 × 止损 -5%        T+1..T+5 任一日累计 <= -5% 即离场（依据：日度超额 std 570bp，左尾肥）
H6 r31 × 止损 -3%
H7 r31 × 缩量 × 宽度     volr5∈[0.8,1.5] & breadth>=0.50
H8 r31 × 缩量 × 止损 × 宽度（三者叠加，最严组合）

对照：r31_breakout 裸版（必须一并输出）

## 口径（与前序严格一致）
BUY 可买子集 = T 日未涨停（20cm/30cm 按代码前缀）；TEST >= 2024-07-01；
ret_hg = T+1 涨停则 r1 兑现，否则持到 T+5(r5)；止损变体按上面的规则改写；
核心指标 = **按日配对超额**（信号股等权 − 可买子集等权，再对超额序列做 t）。

## PROMOTE 门槛（写死）
C1 日均超额>0 / C2 配对t>=2.0 / C3 Purged5折正折数>=4 /
C4 单边10bp成本后 t>=2.0 / C5 2026Q3 与 2026Q4 超额均>=0 / C6 n>=500 且 交易日>=100

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
OUT_JSON = os.path.join(PROJ, "backtest_cache", "r31_preregistered.json")

TEST_START = "2024-07-01"
MAX_T5_GAP = 9
MAX_T1_GAP = 4
LIMIT = int(os.environ.get("HG_LIMIT", "0"))
N_HYP = 8
ALPHA = 0.05 / N_HYP


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


class Acc:
    def __init__(self):
        self.d = []; self.rh = []; self.lab = []

    def add(self, d, rh, lab):
        self.d.append(d); self.rh.append(rh); self.lab.append(lab)

    def fin(self):
        d = np.concatenate(self.d); rh = np.concatenate(self.rh); lab = np.concatenate(self.lab)
        ok = ~np.isnan(rh)
        return d[ok], rh[ok], lab[ok]


def day_agg(d, rh):
    ud, inv = np.unique(d, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(ud)).astype(float)
    return ud, np.bincount(inv, weights=rh, minlength=len(ud)) / cnt


def two_sided_p(t, n):
    """正态近似双尾 p（n 较大时足够）。"""
    z = abs(float(t))
    return math.erfc(z / math.sqrt(2.0))


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:LIMIT]
    s_num = np.datetime64(TEST_START, "D").astype(np.int64)

    base = Acc()
    H = {k: Acc() for k in ["base_r31", "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"]}
    brd_up, brd_tot = {}, {}

    for fi, fp in enumerate(files):
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, dtype={"日期": str}).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < 25:
            continue
        close = df["收盘"].to_numpy(float); open_ = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float); low = df["最低"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
        n = len(close); lim = limit_of(code)
        if n < 26:
            continue
        idx = np.arange(1, n - 5)
        valid = (dnum[idx] >= s_num) & (dnum[idx + 5] - dnum[idx] <= MAX_T5_GAP) \
                & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
        idx = idx[valid]
        if idx.size == 0:
            continue

        prev = np.concatenate([[np.nan], close[:-1]])
        ret = close / prev - 1.0; ret[0] = np.nan
        zt_thr = lim - 0.005
        not_zt_T = ret[idx] < zt_thr
        ok = not_zt_T & (~np.isnan(ret[idx]))
        if not ok.any():
            continue
        idx = idx[ok]

        # 市场宽度：全市场 close>MA20 占比（T 日收盘已知）
        ma20 = pd.Series(close).rolling(20, min_periods=20).mean().to_numpy()
        above = (~np.isnan(ma20)) & (close > ma20)
        for a, b in zip(dnum[idx].tolist(), above[idx].tolist()):
            brd_up[a] = brd_up.get(a, 0) + (1 if b else 0)
            brd_tot[a] = brd_tot.get(a, 0) + 1

        # 收益路径
        cum = np.stack([close[idx + k] / close[idx] - 1.0 for k in range(1, 6)], axis=1)
        r1 = cum[:, 0]; r5 = cum[:, 4]
        t1_zt = ret[idx + 1] >= zt_thr
        ret_hg = np.where(t1_zt, r1, r5)

        def with_stop(stop):
            out = np.where(t1_zt, r1, cum[:, 4])
            hit = cum[:, 0] <= -stop
            out = np.where(hit, cum[:, 0], out)
            for k in range(1, 5):
                h = (cum[:, k] <= -stop) & (~hit) & (~t1_zt)
                out = np.where(h, cum[:, k], out)
                hit = hit | h
            return out

        # 圣杯标签（仅用于报告正例率）
        cdiff = np.concatenate([[0], np.cumsum((close[1:] > close[:-1]).astype(np.int64))])
        label = ((ret[idx] < zt_thr) & ((open_[idx + 1] > close[idx]) | t1_zt)
                 & ((cdiff[idx + 5] - cdiff[idx]) >= 2))

        dts = dnum[idx]
        base.add(dts, ret_hg, label)

        # r31 信号
        sd10 = sh(pd.Series(ret).rolling(10, min_periods=10).std().to_numpy(), 1)
        vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
        volr5 = vol / np.where(vol5 > 0, vol5, np.nan)
        sig = (~np.isnan(sd10[idx])) & (sd10[idx] < 0.01) & (ret[idx] > 0.03)
        vr = volr5[idx]
        dt = dts

        def put(k, mask, rh):
            if not mask.any():
                return
            H[k].add(dt[mask], rh[mask], label[mask])

        rh5 = with_stop(0.05); rh3 = with_stop(0.03)
        put("base_r31", sig, ret_hg)
        put("H1", sig & (vr >= 0.8) & (vr <= 1.5), ret_hg)
        put("H2", sig & (vr < 1.0), ret_hg)
        put("H5", sig, rh5)
        put("H6", sig, rh3)
        if (fi + 1) % 800 == 0:
            print("  ...%d/%d" % (fi + 1, len(files)), flush=True)

    # 第二遍：需要 breadth 的 H3/H4/H7/H8（breadth 需全市场统计后才能算）
    breadth = {k: brd_up[k] / max(brd_tot.get(k, 1), 1) for k in brd_up}
    for fi, fp in enumerate(files):
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, dtype={"日期": str}).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < 25:
            continue
        close = df["收盘"].to_numpy(float); open_ = df["开盘"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
        n = len(close); lim = limit_of(code)
        if n < 26:
            continue
        idx = np.arange(1, n - 5)
        valid = (dnum[idx] >= s_num) & (dnum[idx + 5] - dnum[idx] <= MAX_T5_GAP) \
                & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
        idx = idx[valid]
        if idx.size == 0:
            continue
        prev = np.concatenate([[np.nan], close[:-1]])
        ret = close / prev - 1.0; ret[0] = np.nan
        zt_thr = lim - 0.005
        idx = idx[(ret[idx] < zt_thr) & (~np.isnan(ret[idx]))]
        if idx.size == 0:
            continue
        cum = np.stack([close[idx + k] / close[idx] - 1.0 for k in range(1, 6)], axis=1)
        t1_zt = ret[idx + 1] >= zt_thr
        ret_hg = np.where(t1_zt, cum[:, 0], cum[:, 4])

        def with_stop(stop):
            out = np.where(t1_zt, cum[:, 0], cum[:, 4])
            hit = cum[:, 0] <= -stop
            out = np.where(hit, cum[:, 0], out)
            for k in range(1, 5):
                h = (cum[:, k] <= -stop) & (~hit) & (~t1_zt)
                out = np.where(h, cum[:, k], out)
                hit = hit | h
            return out

        cdiff = np.concatenate([[0], np.cumsum((close[1:] > close[:-1]).astype(np.int64))])
        label = ((open_[idx + 1] > close[idx]) | t1_zt) & ((cdiff[idx + 5] - cdiff[idx]) >= 2)
        sd10 = sh(pd.Series(ret).rolling(10, min_periods=10).std().to_numpy(), 1)
        vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
        volr5 = vol / np.where(vol5 > 0, vol5, np.nan)
        sig = (~np.isnan(sd10[idx])) & (sd10[idx] < 0.01) & (ret[idx] > 0.03)
        vr = volr5[idx]; dt = dnum[idx]
        br = np.array([breadth.get(int(x), 0.0) for x in dt.tolist()])
        b50 = br >= 0.50; b60 = br >= 0.60
        sq = (vr >= 0.8) & (vr <= 1.5)
        rh5 = with_stop(0.05)

        def put(k, mask, rh):
            if not mask.any():
                return
            H[k].add(dt[mask], rh[mask], label[mask])

        put("H3", sig & b50, ret_hg)
        put("H4", sig & b60, ret_hg)
        put("H7", sig & sq & b50, ret_hg)
        put("H8", sig & sq & b50, rh5)

    bd, brh, blab = base.fin()
    bud, bmean = day_agg(bd, brh)
    bmap = dict(zip(bud.tolist(), bmean.tolist()))
    print("=" * 100)
    print("[基准·可买子集] n=%d 交易日=%d 日均=%.4f%% 圣杯正例率=%.4f"
          % (len(brh), len(bud), bmean.mean() * 100, blab.mean()))
    print("[预注册] 假设数=%d  Bonferroni alpha=%.5f" % (N_HYP, ALPHA))
    print("=" * 100)

    order = ["base_r31", "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8"]
    desc = {"base_r31": "r31 裸版(对照)", "H1": "r31×缩量[0.8,1.5]", "H2": "r31×缩量<1.0",
            "H3": "r31×宽度>=0.50", "H4": "r31×宽度>=0.60", "H5": "r31×止损-5%",
            "H6": "r31×止损-3%", "H7": "r31×缩量×宽度", "H8": "r31×缩量×宽度×止损"}
    out = {"alpha_bonferroni": ALPHA, "n_hypotheses": N_HYP, "baseline": {}, "results": {}}
    out["baseline"] = {"n": int(len(brh)), "n_days": int(len(bud)),
                       "mean_day_pct": round(float(bmean.mean()) * 100, 4),
                       "pos_rate": round(float(blab.mean()), 4)}

    hdr = "%-8s %-20s %7s %6s %10s %8s %8s %8s %6s %s"
    print(hdr % ("编号", "假设", "n", "天数", "日均超额bp", "t", "10bp后t", "正折", "2026Q3", "裁决"))
    for k in order:
        if k not in H or not H[k].d:
            continue
        d, rh, lab = H[k].fin()
        ud, fmean = day_agg(d, rh)
        common = np.isin(ud, bud)
        if common.sum() < 100:
            continue
        du = ud[common]; fv = fmean[common]
        bv = np.array([bmap[int(x)] for x in du.tolist()])
        ex = fv - bv
        nd = len(ex); sd = ex.std(ddof=1)
        t = float(ex.mean() / (sd / math.sqrt(nd))) if sd > 0 else 0.0
        # 成本 10bp 单边 → 往返 20bp
        ex_c = ex - 0.0020
        t_c = float(ex_c.mean() / (ex_c.std(ddof=1) / math.sqrt(nd))) if ex_c.std(ddof=1) > 0 else 0.0
        # Purged 5 折
        srt = np.argsort(du)
        folds = np.array_split(srt, 5)
        purge = 5
        pos_folds = 0; fold_vals = []
        for gi, gi_idx in enumerate(folds):
            lo = max(0, gi_idx.min() - purge * gi) if gi > 0 else gi_idx.min()
            keep = gi_idx[(gi_idx >= (folds[gi - 1].max() + purge))] if gi > 0 else gi_idx
            e = ex[keep]
            v = float(e.mean() * 10000)
            fold_vals.append(round(v, 1))
            if v > 0:
                pos_folds += 1
        # 季度
        dstr = pd.to_datetime(du.astype("datetime64[D]"))
        q = dstr.year.astype(str) + "Q" + dstr.quarter.astype(str)
        q26 = {}
        for qq in ["2026Q3", "2026Q4"]:
            m = np.asarray(q == qq)
            q26[qq] = round(float(ex[m].mean() * 10000), 1) if m.sum() > 0 else None
        p = two_sided_p(t, nd)
        C1 = ex.mean() > 0
        C2 = t >= 2.0
        C3 = pos_folds >= 4
        C4 = t_c >= 2.0
        C5 = (q26.get("2026Q3") is not None and q26["2026Q3"] >= 0) and \
             (q26.get("2026Q4") is not None and q26["2026Q4"] >= 0)
        C6 = len(rh) >= 500 and nd >= 100
        verdict = "PROMOTE" if (C1 and C2 and C3 and C4 and C5 and C6) else \
                  ("WATCH" if (C1 and C2 and C6) else "REJECT")
        rec = {"desc": desc[k], "n": int(len(rh)), "n_days": int(nd),
               "mean_excess_bp": round(float(ex.mean() * 10000), 2), "t": round(t, 3),
               "t_cost10bp": round(t_c, 3), "pos_folds": pos_folds,
               "fold_bp": fold_vals, "q2026Q3": q26.get("2026Q3"), "q2026Q4": q26.get("2026Q4"),
               "p_nominal": round(p, 6), "p_bonf_pass": bool(p < ALPHA),
               "C": [bool(C1), bool(C2), bool(C3), bool(C4), bool(C5), bool(C6)],
               "verdict": verdict}
        out["results"][k] = rec
        print(hdr % (k, desc[k], rec["n"], rec["n_days"], "%.2f" % rec["mean_excess_bp"],
                     "%.3f" % rec["t"], "%.3f" % rec["t_cost10bp"], "%d/5" % pos_folds,
                     "%.1f" % rec["q2026Q3"] if rec["q2026Q3"] is not None else "NA",
                     verdict))
    print("\n[门槛] C1超额>0 / C2 t>=2 / C3 Purged正折>=4 / C4 10bp后t>=2 / C5 26Q3&26Q4>=0 / C6 n>=500&天数>=100")
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[out] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
