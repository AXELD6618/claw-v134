# -*- coding: utf-8 -*-
"""
research/r31_holding_probe.py — r31_breakout 最后一项预注册假设检验：持有期 × 移动止损

## 为什么做这一轮
r31_breakout 是系统里唯一有正且显著按日配对超额的因子（裸版 +86.78bp / t=3.080）。
已跑过且全部失败：14 个横截面条件化变体、缩量、市场宽度。已测止损：-5%/-3%
（t 提到 3.144/2.859，但 2026Q3 仍为负）。**剩下未测的只有持有期与移动止损。**

## 预注册假设（共 12 格，Bonferroni α = 0.05/12 = 0.004167），先声明再测
A. 持有期扫描（6 格）：收益 = close[T+k]/close[T] - 1，k ∈ {1,2,3,5,8,10}
   基准**同口径**（可买子集同持有期 cum_k），否则不同持有期的市场漂移会污染超额。
   日历缺口保护 MAX_TK_GAP = 2*k + 3（沿用 T+1 间隔 <= 4 天）。
B. 移动/固定止损（6 格）：在持有到 T+5 的基础上
   移动止盈止损（收盘制）：首个 j 使 r_j <= max_{i<=j} r_i - X 即于当日收盘离场
       X ∈ {0.03, 0.05, 0.08}
   固定止损（收盘制，与已测 H5 同函数）：首个 j 使 r_j <= -Y 即于当日收盘离场
       Y ∈ {0.04, 0.06, 0.10}
   基准 = 可买子集 ret_hg（与 r31_preregistered 完全一致，以便对上 H5 锚点）

## 对照（不计入 12 格，仅用于口径校验）
CTRL_A  k=5 锚点复现：MAX_T5_GAP=9、ret_hg（T+1涨停兑现r1否则r5）、基准 ret_hg
        → 必须复现 n=3735 / 410天 / +86.78bp / t=3.080 / 成本后 2.370 / 正折 4/5 / 26Q3=-24.2
CTRL_B  固定止损 -5%（= 已测 H5）→ 必须复现 +85.37bp / t=3.144 / 成本后 2.407 / 26Q3=-20.4
另外输出 A_k5 的两种拆解（gap=9 纯持有 / gap=13 纯持有）以定位口径差异来源。

## 铁律
只读 backtest_cache/daily；不改任何生产代码/生产 json；不 git 提交。
"""
from __future__ import annotations
import os, glob, json, math
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT_JSON = os.path.join(PROJ, "backtest_cache", "r31_holding_probe.json")

TEST_START = "2024-07-01"
MAX_T1_GAP = 4
MAX_T5_GAP = 9          # 原口径（锚点复现用）
N_HYP = 12
ALPHA = 0.05 / N_HYP
LIMIT = int(os.environ.get("HG_LIMIT", "0"))

HOLD_KS = [1, 2, 3, 5, 8, 10]
TRAIL_XS = [0.03, 0.05, 0.08]
STOP_YS = [0.04, 0.06, 0.10]
STOP_CTRL = 0.05        # 已测 H5，作对照

OFF = int(np.datetime64("2024-01-01", "D").astype(np.int64))
NDAY = 1400
_qidx = pd.to_datetime((OFF + np.arange(NDAY)).astype("datetime64[D]"))
QLAB = _qidx.year.astype(str).str.cat(_qidx.quarter.astype(str), sep="Q").to_numpy()


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
    """按交易日聚合（只存日级 sum/cnt，避免百万级事件数组）。"""
    def __init__(self):
        self.s = np.zeros(NDAY)
        self.c = np.zeros(NDAY)

    def add(self, dnum, rh):
        rh = np.asarray(rh, float)
        i = np.asarray(dnum, np.int64) - OFF
        m = (~np.isnan(rh)) & (i >= 0) & (i < NDAY)
        if not m.any():
            return
        ii = i[m]
        self.s += np.bincount(ii, weights=rh[m], minlength=NDAY)
        self.c += np.bincount(ii, minlength=NDAY).astype(float)

    def days(self):
        idx = np.nonzero(self.c > 0)[0]
        return idx + OFF, self.s[idx] / self.c[idx]

    @property
    def n(self):
        return int(self.c.sum())


def with_stop(cum, t1_zt, stop):
    """固定止损（收盘制）—— 逐字沿用 r31_preregistered.with_stop。"""
    out = np.where(t1_zt, cum[:, 0], cum[:, 4])
    hit = cum[:, 0] <= -stop
    out = np.where(hit, cum[:, 0], out)
    for k in range(1, 5):
        h = (cum[:, k] <= -stop) & (~hit) & (~t1_zt)
        out = np.where(h, cum[:, k], out)
        hit = hit | h
    return out


def with_trail(cum, t1_zt, x):
    """移动止盈止损（收盘制）：首个 j 使 r_j <= (截至 j 的最高收盘收益) - X 即当日收盘离场。
    峰值从入场价 0 起算；T+1 涨停沿用 ret_hg 的 r1 兑现，不套退出规则（与固定止损一致）。"""
    out = np.where(t1_zt, cum[:, 0], cum[:, 4])
    hit = np.zeros(len(out), bool)
    pk = np.zeros(len(out))
    for j in range(5):
        cur = cum[:, j]
        pk = np.maximum(pk, cur)
        h = (cur <= pk - x) & (~hit) & (~t1_zt)
        out = np.where(h, cur, out)
        hit = hit | h
    return out


def two_sided_p(t, n):
    return math.erfc(abs(float(t)) / math.sqrt(2.0))


def _ac(e, lag):
    if len(e) <= lag + 1:
        return float("nan")
    x = e - e.mean()
    return float(np.dot(x[lag:], x[:-lag]) / np.dot(x, x))


def _nw_t(e, lag):
    """Newey-West(HAC, Bartlett) 校正的均值 t：持有期跨多日时相邻交易日的超额高度重叠，
    普通 t 假设 iid，会被自相关显著放大。lag 取持有期长度。"""
    n = len(e)
    x = e - e.mean()
    g0 = float(np.dot(x, x) / n)
    s = g0
    for j in range(1, min(lag, n - 1) + 1):
        gj = float(np.dot(x[j:], x[:-j]) / n)
        s += 2.0 * (1.0 - j / (lag + 1.0)) * gj
    if s <= 0:
        return 0.0
    return float(e.mean() / math.sqrt(s / n))


def evaluate(sig_acc, base_acc, nw_lag=0):
    d, fmean = sig_acc.days()
    bd, bmean = base_acc.days()
    bmap = dict(zip(bd.tolist(), bmean.tolist()))
    common = np.isin(d, bd)
    du = d[common]
    if len(du) < 100:
        return None
    fv = fmean[common]
    bv = np.array([bmap[int(x)] for x in du.tolist()])
    ex = fv - bv
    nd = len(ex)
    sd = ex.std(ddof=1) if nd > 1 else 0.0
    t = float(ex.mean() / (sd / math.sqrt(nd))) if sd > 0 else 0.0
    sharpe = float(ex.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0
    wr = float((ex > 0).mean())
    ex_c = ex - 0.0020                      # 单边 10bp → 往返 20bp
    sdc = ex_c.std(ddof=1)
    t_c = float(ex_c.mean() / (sdc / math.sqrt(nd))) if sdc > 0 else 0.0
    # Purged 5 折（逐字沿用 r31_preregistered）
    srt = np.argsort(du)
    folds = np.array_split(srt, 5)
    purge = 5
    pos_folds = 0
    fold_vals = []
    for gi, gi_idx in enumerate(folds):
        keep = gi_idx[(gi_idx >= (folds[gi - 1].max() + purge))] if gi > 0 else gi_idx
        e = ex[keep]
        v = float(e.mean() * 10000)
        fold_vals.append(round(v, 1))
        if v > 0:
            pos_folds += 1
    q = QLAB[du - OFF]
    qout = {}
    qdays = {}
    for qq in ["2026Q3", "2026Q4"]:
        m = (q == qq)
        qdays[qq] = int(m.sum())
        qout[qq] = round(float(ex[m].mean() * 10000), 1) if m.sum() > 0 else None
    rec = {
        "q_days": qdays,
        "ac1": round(_ac(ex, 1), 4),
        "ac5": round(_ac(ex, 5), 4),
        "t_nw": round(_nw_t(ex, nw_lag), 3),
        "n": int(sig_acc.n), "n_days": int(nd),
        "mean_excess_bp": round(float(ex.mean() * 10000), 2),
        "t": round(t, 3),
        "sharpe_excess": round(sharpe, 3),
        "win_rate_day": round(wr, 4),
        "t_cost10bp": round(t_c, 3),
        "pos_folds": pos_folds, "fold_bp": fold_vals,
        "q2026Q3": qout["2026Q3"], "q2026Q4": qout["2026Q4"],
        "p_nominal": round(two_sided_p(t, nd), 6),
        "p_bonf_pass": bool(two_sided_p(t, nd) < ALPHA),
    }
    C1 = ex.mean() > 0
    C2 = t >= 2.0
    C3 = pos_folds >= 4
    C4 = t_c >= 2.0
    C5 = (qout["2026Q3"] is not None and qout["2026Q3"] >= 0) and \
         (qout["2026Q4"] is not None and qout["2026Q4"] >= 0)
    C6 = int(sig_acc.n) >= 500 and nd >= 100
    rec["C"] = [bool(C1), bool(C2), bool(C3), bool(C4), bool(C5), bool(C6)]
    rec["verdict"] = "PROMOTE" if all([C1, C2, C3, C4, C5, C6]) else \
                     ("WATCH" if (C1 and C2 and C6) else "REJECT")
    return rec


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:LIMIT]
    s_num = np.datetime64(TEST_START, "D").astype(np.int64)

    base_hg = Acc()                                   # 可买子集 ret_hg（原口径基准）
    base_lab = Acc()                                  # 圣杯标签（仅基准正例率校验）
    base_k = {k: Acc() for k in HOLD_KS}              # 可买子集 cum_k
    base_k5_p9 = Acc()                                # 可买子集 cum_5（gap=9，口径拆解）
    sig_k = {k: Acc() for k in HOLD_KS}
    sig_k5_p9 = Acc()
    ctrl_a = Acc()                                    # k=5 锚点（ret_hg）
    B_rules = {}
    B_base_rules = {}
    for x in TRAIL_XS:
        B_rules[("trail", x)] = Acc()
        B_base_rules[("trail", x)] = Acc()
    for y in STOP_YS + [STOP_CTRL]:
        B_rules[("stop", y)] = Acc()
        B_base_rules[("stop", y)] = Acc()

    for fi, fp in enumerate(files):
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, dtype={"日期": str}).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < 25:
            continue
        close = df["收盘"].to_numpy(float)
        open_ = df["开盘"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
        n = len(close)
        if n < 26:
            continue
        lim = limit_of(code)
        zt_thr = lim - 0.005
        prev = np.concatenate([[np.nan], close[:-1]])
        ret = close / prev - 1.0
        ret[0] = np.nan
        sd10 = sh(pd.Series(ret).rolling(10, min_periods=10).std().to_numpy(), 1)

        # ---------- A：持有期扫描 ----------
        for k in HOLD_KS:
            idx = np.arange(1, n - k)
            if idx.size == 0:
                continue
            valid = (dnum[idx] >= s_num) \
                    & (dnum[idx + k] - dnum[idx] <= 2 * k + 3) \
                    & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
            idx = idx[valid]
            ok = (ret[idx] < zt_thr) & (~np.isnan(ret[idx]))
            idx = idx[ok]
            if idx.size == 0:
                continue
            rk = close[idx + k] / close[idx] - 1.0
            base_k[k].add(dnum[idx], rk)
            sg = (~np.isnan(sd10[idx])) & (sd10[idx] < 0.01) & (ret[idx] > 0.03)
            if sg.any():
                sig_k[k].add(dnum[idx][sg], rk[sg])

        # ---------- k=5 口径拆解（gap=9 纯持有） ----------
        idx = np.arange(1, n - 5)
        if idx.size == 0:
            continue
        valid = (dnum[idx] >= s_num) & (dnum[idx + 5] - dnum[idx] <= MAX_T5_GAP) \
                & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
        idx5 = idx[valid]
        ok = (ret[idx5] < zt_thr) & (~np.isnan(ret[idx5]))
        idx5 = idx5[ok]
        if idx5.size == 0:
            continue
        cum = np.stack([close[idx5 + j] / close[idx5] - 1.0 for j in range(1, 6)], axis=1)
        t1_zt = ret[idx5 + 1] >= zt_thr
        ret_hg = np.where(t1_zt, cum[:, 0], cum[:, 4])
        sg5 = (~np.isnan(sd10[idx5])) & (sd10[idx5] < 0.01) & (ret[idx5] > 0.03)
        dts5 = dnum[idx5]

        base_k5_p9.add(dts5, cum[:, 4])
        if sg5.any():
            sig_k5_p9.add(dts5[sg5], cum[sg5, 4])

        # ---------- 锚点 + B 止损格 ----------
        cdiff = np.concatenate([[0], np.cumsum((close[1:] > close[:-1]).astype(np.int64))])
        label = ((ret[idx5] < zt_thr) & ((open_[idx5 + 1] > close[idx5]) | t1_zt)
                 & ((cdiff[idx5 + 5] - cdiff[idx5]) >= 2))
        base_hg.add(dts5, ret_hg)
        base_lab.add(dts5, label.astype(float))
        if sg5.any():
            ctrl_a.add(dts5[sg5], ret_hg[sg5])
        for key, acc in B_rules.items():
            kind, p = key
            rh = with_trail(cum, t1_zt, p) if kind == "trail" else with_stop(cum, t1_zt, p)
            B_base_rules[key].add(dts5, rh)
            if sg5.any():
                acc.add(dts5[sg5], rh[sg5])
        if (fi + 1) % 800 == 0:
            print("  ...%d/%d" % (fi + 1, len(files)), flush=True)

    # ---------------- 基准校验 ----------------
    bd, bmean = base_hg.days()
    blab = base_lab.s.sum() / max(base_lab.c.sum(), 1)
    print("=" * 112)
    print("[基准·可买子集 ret_hg] n=%d 交易日=%d 日均=%.4f%% 圣杯正例率=%.4f"
          % (int(base_hg.n), len(bd), bmean.mean() * 100, blab))
    print("[预期] n=2550519 交易日=513 日均=0.2778%% 圣杯正例率=0.3075")
    for k in HOLD_KS:
        dk, mk = base_k[k].days()
        print("   [基准 cum_%d] n=%d 交易日=%d 日均=%.4f%%" % (k, int(base_k[k].n), len(dk), mk.mean() * 100))
    print("=" * 112)

    out = {"test_start": TEST_START, "n_hypotheses": N_HYP, "alpha_bonferroni": ALPHA,
           "baseline": {"n": int(base_hg.n), "n_days": int(len(bd)),
                        "mean_day_pct": round(float(bmean.mean() * 100), 4),
                        "pos_rate": round(float(blab), 4)},
           "A": {}, "B": {}, "controls": {}, "diagnostics": {}}

    HDR = "%-9s %-16s %7s %6s %10s %8s %8s %8s %8s %6s %9s %9s %10s %7s %s"
    print("\n### A. 持有期扫描（收益=close[T+k]/close[T]-1，基准=可买子集同持有期）")
    print(HDR % ("格", "定义", "n", "天数", "日均超额bp", "t", "Sharpe", "日胜率",
                 "10bp后t", "正折", "2026Q3", "2026Q4", "名义p", "Bonf", "裁决"))
    for k in HOLD_KS:
        r = evaluate(sig_k[k], base_k[k], nw_lag=k)
        if r is None:
            print("%-9s %-16s %s" % ("A_k%d" % k, "持有 T+%d" % k, "样本不足（<100 交易日）"))
            continue
        r["desc"] = "持有 T+%d" % k
        out["A"]["k%d" % k] = r
        print(HDR % ("A_k%d" % k, r["desc"], r["n"], r["n_days"], "%.2f" % r["mean_excess_bp"],
                     "%.3f" % r["t"], "%.3f" % r["sharpe_excess"], "%.4f" % r["win_rate_day"],
                     "%.3f" % r["t_cost10bp"], "%d/5" % r["pos_folds"],
                     "%.1f" % r["q2026Q3"] if r["q2026Q3"] is not None else "NA",
                     "%.1f" % r["q2026Q4"] if r["q2026Q4"] is not None else "NA",
                     "%.6f" % r["p_nominal"], "PASS" if r["p_bonf_pass"] else "-", r["verdict"]))

    print("\n### B. 移动止损 / 固定止损（持有 T+5 基础上，基准=可买子集 ret_hg）")
    print(HDR % ("格", "定义", "n", "天数", "日均超额bp", "t", "Sharpe", "日胜率",
                 "10bp后t", "正折", "2026Q3", "2026Q4", "名义p", "Bonf", "裁决"))
    bkeys = [("trail", x) for x in TRAIL_XS] + [("stop", y) for y in STOP_YS]
    for key in bkeys:
        kind, p = key
        nm = ("B_trail%d" % round(p * 100)) if kind == "trail" else ("B_stop%d" % round(p * 100))
        desc = ("移动止损 -%.0f%%" % (p * 100)) if kind == "trail" else ("固定止损 -%.0f%%" % (p * 100))
        r = evaluate(B_rules[key], base_hg, nw_lag=5)
        if r is None:
            print("%-9s %-16s %s" % (nm, desc, "样本不足（<100 交易日）"))
            continue
        r["desc"] = desc
        # 诊断：与"同规则的可买子集"比（剥离市场级左尾截断）
        rm = evaluate(B_rules[key], B_base_rules[key], nw_lag=5)
        r["t_matched_base"] = rm["t"] if rm else None
        r["mean_excess_bp_matched"] = rm["mean_excess_bp"] if rm else None
        out["B"][nm] = r
        print(HDR % (nm, desc, r["n"], r["n_days"], "%.2f" % r["mean_excess_bp"],
                     "%.3f" % r["t"], "%.3f" % r["sharpe_excess"], "%.4f" % r["win_rate_day"],
                     "%.3f" % r["t_cost10bp"], "%d/5" % r["pos_folds"],
                     "%.1f" % r["q2026Q3"] if r["q2026Q3"] is not None else "NA",
                     "%.1f" % r["q2026Q4"] if r["q2026Q4"] is not None else "NA",
                     "%.6f" % r["p_nominal"], "PASS" if r["p_bonf_pass"] else "-", r["verdict"]))

    # ---------------- 口径校验对照 ----------------
    print("\n### 口径校验（对照格，不计入 12 格）")
    ra = evaluate(ctrl_a, base_hg, nw_lag=5)
    if ra:
        ra["desc"] = "CTRL 锚点: k=5 ret_hg"
        ra["expect"] = "n=3735 天数=410 bp=86.78 t=3.080 t_cost=2.370 正折=4/5 26Q3=-24.2"
        out["controls"]["CTRL_A_k5_anchor"] = ra
        print("  CTRL_A（k=5, MAX_T5_GAP=9, ret_hg, 基准 ret_hg）")
        print("     实得: n=%d 天数=%d bp=%.2f t=%.3f t_cost=%.3f 正折=%d/5 26Q3=%.1f 26Q4=%.1f"
              % (ra["n"], ra["n_days"], ra["mean_excess_bp"], ra["t"], ra["t_cost10bp"],
                 ra["pos_folds"], ra["q2026Q3"], ra["q2026Q4"]))
        print("     预期: %s" % ra["expect"])
    rb = evaluate(B_rules[("stop", STOP_CTRL)], base_hg, nw_lag=5)
    if rb:
        rb["desc"] = "CTRL 固定止损 -5%（=已测 H5）"
        rb["expect"] = "bp=85.37 t=3.144 t_cost=2.407 正折=4/5 26Q3=-20.4"
        out["controls"]["CTRL_B_stop5"] = rb
        print("  CTRL_B（固定止损 -5%）")
        print("     实得: n=%d 天数=%d bp=%.2f t=%.3f t_cost=%.3f 正折=%d/5 26Q3=%.1f 26Q4=%.1f"
              % (rb["n"], rb["n_days"], rb["mean_excess_bp"], rb["t"], rb["t_cost10bp"],
                 rb["pos_folds"], rb["q2026Q3"], rb["q2026Q4"]))
        print("     预期: %s" % rb["expect"])
    rk9 = evaluate(sig_k5_p9, base_k5_p9, nw_lag=5)
    if rk9:
        rk9["desc"] = "k=5 纯持有 cum5, gap=9, 基准=可买子集 cum5"
        out["controls"]["CTRL_A_k5_pure_gap9"] = rk9
        print("  拆解 k=5 纯持有(gap=9, 同口径基准): n=%d 天数=%d bp=%.2f t=%.3f"
              % (rk9["n"], rk9["n_days"], rk9["mean_excess_bp"], rk9["t"]))

    # ---------------- 补充诊断：重叠持有自相关 + 季度天数 ----------------
    print("\n### 补充诊断（不改变预注册门槛，仅用于判读）")
    print("%-9s %-16s %8s %8s %9s %10s %12s" %
          ("格", "定义", "ac1", "ac5", "NW-t", "Q3天数", "Q4天数"))
    for k in HOLD_KS:
        r = out["A"].get("k%d" % k)
        if not r:
            continue
        print("%-9s %-16s %8.3f %8.3f %9.3f %10d %12d" %
              ("A_k%d" % k, r["desc"], r["ac1"], r["ac5"], r["t_nw"],
               r["q_days"]["2026Q3"], r["q_days"]["2026Q4"]))
    for key in bkeys:
        kind, p = key
        nm = ("B_trail%d" % round(p * 100)) if kind == "trail" else ("B_stop%d" % round(p * 100))
        r = out["B"].get(nm)
        if not r:
            continue
        print("%-9s %-16s %8.3f %8.3f %9.3f %10d %12d" %
              (nm, r["desc"], r["ac1"], r["ac5"], r["t_nw"],
               r["q_days"]["2026Q3"], r["q_days"]["2026Q4"]))

    # ---------------- 裁决 ----------------
    all_cells = {}
    for k, r in out["A"].items():
        all_cells["A_k%s" % k] = r
    for k, r in out["B"].items():
        all_cells[k] = r
    pos = [r for r in all_cells.values() if r["mean_excess_bp"] > 0]
    pool = pos if pos else list(all_cells.values())
    best = max(pool, key=lambda r: r["t"])
    best_name = [k for k, v in all_cells.items() if v is best][0]
    verdict_final = best["verdict"]
    c7 = None
    if best_name.startswith("A_k"):
        ki = HOLD_KS.index(int(best_name[3:]))
        nb = [j for j in (ki - 1, ki + 1) if 0 <= j < len(HOLD_KS)]
        nb_ok = []
        for j in nb:
            rj = out["A"].get("k%d" % HOLD_KS[j])
            nb_ok.append(bool(rj and rj["t"] >= 1.5 and rj["mean_excess_bp"] > 0))
        c7 = bool(nb_ok) and all(nb_ok)
        out["C7"] = {"best": best_name, "neighbors": [HOLD_KS[j] for j in nb],
                     "neighbor_ok": nb_ok, "pass": c7}
        if not c7:
            verdict_final = "WATCH" if verdict_final == "PROMOTE" else verdict_final
    out["best_cell"] = best_name
    out["best_cell_detail"] = best
    out["final_verdict"] = verdict_final
    print("\n" + "=" * 112)
    print("[最优格] %s  %s  bp=%.2f  t=%.3f  裁决=%s" %
          (best_name, best["desc"], best["mean_excess_bp"], best["t"], verdict_final))
    print("[门槛] C1超额>0 / C2 t>=2 / C3 正折>=4 / C4 10bp后t>=2 / C5 26Q3&26Q4>=0 / C6 n>=500&天数>=100"
          " / C7 若最优格在A则相邻格 t>=1.5 且超额>0")
    print("=" * 112)
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[out] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
