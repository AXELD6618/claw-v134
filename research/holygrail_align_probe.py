# -*- coding: utf-8 -*-
"""
research/holygrail_align_probe.py — 圣杯口径对齐探针 v2（只读研究，不改任何生产代码）

## v1 结论（2026-10-28 全量 5225 只）
圣杯对齐口径下 oversold 命中倍数 1.1561 / 事件PF 1.3064 / t=11.7，
而旧口径（H5 收益>0）下同因子命中倍数仅 0.759 → **口径错配假设获强支持**。

## v1 的两个硬缺陷（v2 必须修，否则结论不可信）
1. **逐事件 PF ≠ 组合日 PF**：历史上正是用逐事件 PF(1.3826) 误判 oversold 为 PROMOTE，
   规范门一算组合日 PF=0.686 即 REJECT。v2 必须同时给出**组合日等权 PF / Sharpe**，
   并以组合日 PF 为铁律口径。
2. **涨停阈值写死 9.5%**：300/301/688/689 是 20cm、8/4/920 是 30cm。
   写死 0.095 会把「创业板涨 10%」误判为涨停 → 误杀出可买子集，
   也会把「创业板真涨停(20%)」误判为涨停基因。v2 按代码前缀取真实涨跌幅限制。

## v2 做什么
只读 backtest_cache/daily/*.csv（列序 日期,开盘,收盘,最高,最低,成交量，前复权）：
  - 圣杯正例标签（T未涨停 & (T+1高开|T+1涨停) & T+1..T+5上涨天数>=2）
  - 双口径收益：hg_ret（退出纪律：T+1涨停兑现r1，否则持到T+5 r5）与 r5（旧口径）
  - 每个因子输出：事件级 PF/均值/Welch-t + **组合日等权 PF/Sharpe/t**
  - 因子宇宙从 6 个扩到 30 个（全部 DAILY_ONLY，只用日线 5 列）

## 铁律
只读 daily；不改 buy_score / factor_registry / core_factor_model / 任何生产代码；不 git 提交。
门控以**组合日 PF** 为准：n>=200 且 命中倍数>=1.15 且 pf_day>=1.30 且 t>=2.0
"""
from __future__ import annotations
import os, glob, json, math, sys
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT_JSON = os.path.join(PROJ, "backtest_cache", "holygrail_align_probe.json")

TEST_START = "2024-07-01"
MAX_T5_GAP = 9
MAX_T1_GAP = 4
LIMIT = int(os.environ.get("HG_LIMIT", "0"))   # >0 只跑前 N 只（调试）
MIN_N = 200
GATE_HIT = 1.15
GATE_PF = 1.30
GATE_T = 2.0


def limit_of(code: str) -> float:
    return board_limit_pct(code)



def pf(vals):
    v = np.asarray(vals, float)
    v = v[~np.isnan(v)]
    pos = v[v > 0].sum()
    neg = -v[v < 0].sum()
    if neg <= 0:
        return 99.0 if pos > 0 else 0.0
    if pos <= 0:
        return 0.0
    return float(pos / neg)


def welch_from_stats(ma, va, na, mb, vb, nb):
    if na < 2 or nb < 2:
        return 0.0
    se = math.sqrt(max(va, 0.0) / na + max(vb, 0.0) / nb)
    return float((ma - mb) / se) if se > 0 else 0.0


def roll_mean(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).mean().to_numpy()


def roll_std(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).std().to_numpy()


def roll_max(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).max().to_numpy()


def roll_min(a, w):
    return pd.Series(a, dtype=float).rolling(w, min_periods=w).min().to_numpy()


def sh(a, k, fill=None):
    """下移 k 位（前 k 位填 fill），不做 np.roll 环绕 —— 环绕会污染尾部。"""
    a = np.asarray(a)
    if k <= 0:
        return a
    if fill is None:
        fill = False if a.dtype == bool else np.nan
    out = np.full(len(a), fill, dtype=a.dtype)
    out[k:] = a[:-k]
    return out


def build_signals(close, open_, high, low, vol, lim):
    """返回 dict: 因子名 -> bool 数组（T日信号，不含 BUY 前提）。全部 DAILY_ONLY。"""
    n = len(close)
    prev = np.concatenate([[np.nan], close[:-1]])
    pct = close / prev - 1.0
    pct[0] = np.nan
    ret = pct.copy()
    zt_thr = lim - 0.005
    is_zt = ret >= zt_thr
    is_dt = ret <= -zt_thr

    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    vol20 = pd.Series(vol).rolling(20).mean().shift(1).to_numpy(float)
    volr5 = vol / np.where(vol5 > 0, vol5, np.nan)
    volr20 = vol / np.where(vol20 > 0, vol20, np.nan)
    vma5 = pd.Series(vol).rolling(5).mean().to_numpy(float)
    vma60 = pd.Series(vol).rolling(60, min_periods=60).mean().to_numpy(float)

    ma5 = roll_mean(close, 5)
    ma10 = roll_mean(close, 10)
    ma20 = roll_mean(close, 20)
    ma25 = roll_mean(close, 25)
    ma60 = roll_mean(close, 60)
    ma250 = roll_mean(close, 250)
    hi20 = roll_max(high, 20)
    hi250 = roll_max(high, 250)
    lo250 = roll_min(low, 250)

    amp = (high - low) / prev
    body = np.abs(close - open_)
    rng = np.maximum(high - low, 1e-9)
    lower_sh = (np.minimum(open_, close) - low) / np.maximum(body, 1e-9)
    body_ratio = body / rng

    ret20 = np.full(n, np.nan)
    if n > 20:
        ret20[20:] = close[20:] / close[:-20] - 1.0
    dist20 = close / hi20 - 1.0
    sd10 = sh(roll_std(ret, 10), 1)
    gene = pd.Series(is_zt.astype(float)).rolling(20, min_periods=1).max().to_numpy() > 0

    down = (ret < 0).astype(np.int64)
    streak = np.zeros(n, np.int64)
    cur = 0
    for i in range(n):
        cur = cur + 1 if down[i] else 0
        streak[i] = cur

    vol5_sum = pd.Series(vol).rolling(5).sum().to_numpy(float)
    vol5_prev5 = pd.Series(vol).rolling(10).sum().to_numpy(float) - vol5_sum

    mom5 = np.full(n, np.nan)
    if n > 5:
        mom5[5:] = close[5:] / close[:-5] - 1.0

    s = {}

    # ---- 既有 6 个（v1 已测，保留对照） ----
    s["r31_breakout"] = (~np.isnan(sd10)) & (sd10 < 0.01) & (ret > 0.03)
    s["coiled_spring"] = (~np.isnan(ma20)) & (close > ma20) & (volr5 >= 1.5)
    s["pre_breakout"] = ((~np.isnan(ret20)) & (ret20 > 0.10) & (dist20 > -0.06)
                         & (volr20 > 1.3) & gene)
    s["reversal"] = (pct <= -0.05) & (pct >= -0.08) & (~is_dt)
    s["oversold"] = (pct <= -0.04) & (volr5 >= 1.5)
    s["lianban_prev_zt"] = sh(is_zt, 1, False)

    # ---- 新增：弱转强 / 新买点 ----
    opct = open_ / prev - 1.0
    s["w2s_weak_to_strong"] = (opct <= 0.0) & (pct >= 0.07) & (pct < zt_thr) & (volr5 >= 1.5)
    s["wts_strict"] = (sh(pct, 1) < 0) & (pct >= 0.05) & (pct < zt_thr) & (volr5 >= 2.0)
    s["w2s_relaxed"] = (opct <= 0.0) & (pct >= 0.04) & (pct < zt_thr) & (volr5 >= 1.2)

    # ---- 新增：2560 量价体系 ----
    cross_up_ma25 = (~np.isnan(ma25)) & (sh(close, 1) <= sh(ma25, 1)) & (close > ma25)
    cross_up_vma = (~np.isnan(vma60)) & (sh(vma5, 1) <= sh(vma60, 1)) & (vma5 > vma60)
    s["dual_break_2560"] = cross_up_ma25 & cross_up_vma
    prev5_above = np.ones(n, bool)
    for k in range(1, 6):
        prev5_above &= (sh(close, k) > sh(ma25, k))
    prev5_above[:6] = False
    near_ma25 = (~np.isnan(ma25)) & (np.abs(close - ma25) / ma25 <= 0.03) & (close <= ma25 * 1.03)
    pullback = near_ma25 & prev5_above
    s["dual_stabilize_2560"] = (pullback & (~np.isnan(vma60))
                                & (np.abs(vma5 - vma60) / np.maximum(vma60, 1e-9) <= 0.10)
                                & (vol > vma5 * 1.10))
    s["pullback_lowvol_2560"] = (pullback & (vma5 < vma60) & (close >= ma25 * 0.97) & (vol < vma5))

    # ---- 新增：形态（向量化：子线在 T-2..T-5 内，母线为 T-1） ----
    mo_h, mo_l = sh(high, 1), sh(low, 1)
    mo_b = np.abs(sh(close, 1) - sh(open_, 1))
    harami = np.zeros(n, bool)
    for k in range(2, 6):
        harami |= ((sh(high, k) <= mo_h) & (sh(low, k) >= mo_l)
                   & (np.abs(sh(close, k) - sh(open_, k)) < mo_b))
    s["harami_breakout"] = harami & (close > mo_h) & (vol >= 1.5 * vol20)
    s["harami_lowctx"] = s["harami_breakout"] & (close <= ma60)

    # ---- 新增：反转维度 ----
    s["d47_capitulation"] = (streak >= 5) & (ret20 < -0.25) & (vol5_sum < 0.8 * vol5_prev5)
    s["d49_lower_shadow"] = ((lower_sh > 2.0) & (close > open_)
                             & (low <= roll_min(low, 20) * 1.005) & (mom5 < 0))
    s["reversal7_streak"] = (streak >= 7) & (~is_dt)
    s["reversal_scan_unified"] = (pct > -0.08) & (pct <= -0.03) & (~is_dt)

    # ---- 新增：趋势/位置 ----
    s["t_day_drop_ma20up"] = (ret < 0) & (close > ma20)
    s["above_ma20"] = close > ma20
    s["new_high20_vol"] = (~np.isnan(hi20)) & (close >= hi20 * 0.999) & (volr5 >= 1.5)
    s["ret20_momentum"] = (~np.isnan(ret20)) & (ret20 > 0.05) & (ret20 < 0.20) & (volr5 >= 1.2)
    s["low_vola_consolid"] = (~np.isnan(sd10)) & (sd10 < 0.015) & (close > ma20) & (volr5 >= 1.3)

    # ---- 新增：cloud_holy_grail 打分器布尔化 ----
    s["hg_f7_safe_band"] = (pct >= 0.01) & (pct < 0.07)
    s["hg_f6_amplitude"] = (amp >= 0.03) & (amp < 0.12)
    s["hg_f8_volratio"] = volr5 > 2.0
    s["v74_limit_up_gene"] = gene
    s["v74_lower_shadow"] = lower_sh > 1.5
    s["body_ratio_small"] = body_ratio < 0.3

    # ---- 反向对照 ----
    high_pct = high / prev - 1.0
    s["moves_avoid_REV"] = (high_pct >= 0.05) & (pct < 0.02)

    # ---- oversold 梯度（单调性检验） ----
    s["os_g1"] = (pct <= -0.03) & (pct > -0.04) & (volr5 >= 1.5)
    s["os_g2"] = (pct <= -0.04) & (pct > -0.05) & (volr5 >= 1.5)
    s["os_g3"] = (pct <= -0.05) & (pct > -0.07) & (volr5 >= 1.5)
    s["os_g4"] = (pct <= -0.07) & (~is_dt) & (volr5 >= 1.5)
    s["os_volr3"] = (pct <= -0.04) & (volr5 >= 3.0)
    return s


class Acc:
    __slots__ = ("d", "lab", "rh", "r5")

    def __init__(self):
        self.d = []; self.lab = []; self.rh = []; self.r5 = []

    def add(self, d, lab, rh, r5):
        self.d.append(d); self.lab.append(lab); self.rh.append(rh); self.r5.append(r5)


def summarize(acc, base_stats):
    d = np.concatenate(acc.d) if acc.d else np.zeros(0, np.int64)
    lab = np.concatenate(acc.lab).astype(float) if acc.lab else np.zeros(0)
    rh = np.concatenate(acc.rh) if acc.rh else np.zeros(0)
    r5 = np.concatenate(acc.r5) if acc.r5 else np.zeros(0)
    ok = ~np.isnan(rh)
    d, lab, rh, r5 = d[ok], lab[ok], rh[ok], r5[ok]
    n = len(rh)
    if n == 0:
        return None
    mean, var = float(rh.mean()), float(rh.var(ddof=1)) if n > 1 else 0.0
    t = welch_from_stats(mean, var, n, base_stats[0], base_stats[1], base_stats[2])
    # 组合日等权
    ud, inv = np.unique(d, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(ud)).astype(float)
    dsum = np.bincount(inv, weights=rh, minlength=len(ud))
    dmean = dsum / cnt
    pf_day = pf(dmean)
    if len(dmean) > 1 and dmean.std(ddof=1) > 0:
        sharpe_ann = float(dmean.mean() / dmean.std(ddof=1) * math.sqrt(252))
        t_day = float(dmean.mean() / dmean.std(ddof=1) * math.sqrt(len(dmean)))
    else:
        sharpe_ann = 0.0; t_day = 0.0
    # 旧口径（H5 收益>0）
    hit_old = float((r5 > 0).mean())
    d5 = np.bincount(inv, weights=r5, minlength=len(ud)) / cnt
    return {
        "n": int(n),
        "pos_rate": round(float(lab.mean()), 4),
        "hit_multiple": round(float(lab.mean()) / base_stats[3], 4),
        "pf_event": round(pf(rh), 4),
        "mean_ret_pct": round(mean * 100, 4),
        "t_stat": round(t, 3),
        "pf_day": round(pf_day, 4),
        "sharpe_ann": round(sharpe_ann, 3),
        "t_day": round(t_day, 3),
        "n_days": int(len(ud)),
        "mean_day_pct": round(float(dmean.mean()) * 100, 4),
        "hit_old": round(hit_old, 4),
        "pf_day_old": round(pf(d5), 4),
    }


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:LIMIT]
    s_num = np.datetime64(TEST_START, "D").astype(np.int64)

    base = Acc()
    accs = {}
    names = None

    for fi, fp in enumerate(files):
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, dtype={"日期": str}).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < 70:
            continue
        close = df["收盘"].to_numpy(float)
        open_ = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float)
        low = df["最低"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
        n = len(close)
        lim = limit_of(code)

        idx = np.arange(1, n - 5)
        valid = (dnum[idx] >= s_num) & (dnum[idx + 5] - dnum[idx] <= MAX_T5_GAP) \
                & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
        idx = idx[valid]
        if idx.size == 0:
            continue

        prev = np.concatenate([[np.nan], close[:-1]])
        pct = close / prev - 1.0
        pct[0] = np.nan
        r1 = close[idx + 1] / close[idx] - 1.0
        r5 = close[idx + 5] / close[idx] - 1.0
        zt_thr = lim - 0.005
        ret_hg = np.where(pct[idx + 1] >= zt_thr, r1, r5)

        not_zt_T = pct[idx] < zt_thr
        t1_gap = open_[idx + 1] > close[idx]
        t1_zt = pct[idx + 1] >= zt_thr
        diff = (close[1:] > close[:-1]).astype(np.int64)
        cdiff = np.concatenate([[0], np.cumsum(diff)])
        up_days = cdiff[idx + 5] - cdiff[idx]
        label = (not_zt_T & (t1_gap | t1_zt) & (up_days >= 2))

        buyable = not_zt_T & (~np.isnan(ret_hg)) & (~np.isnan(r5))
        base.add(dnum[idx][buyable], label[buyable], ret_hg[buyable], r5[buyable])

        sig = build_signals(close, open_, high, low, vol, lim)
        if names is None:
            names = sorted(sig.keys())
        for nm in names:
            m = buyable & sig[nm][idx]
            if not m.any():
                continue
            a = accs.setdefault(nm, Acc())
            a.add(dnum[idx][m], label[m], ret_hg[m], r5[m])

        if (fi + 1) % 500 == 0:
            print("  ...%d/%d" % (fi + 1, len(files)), flush=True)

    bl = np.concatenate(base.lab).astype(float)
    br = np.concatenate(base.rh)
    br5 = np.concatenate(base.r5)
    ok = ~np.isnan(br)
    bl, br, br5 = bl[ok], br[ok], br5[ok]
    bd = np.concatenate(base.d)[ok]
    base_stats = (float(br.mean()), float(br.var(ddof=1)), len(br), float(bl.mean()))
    ud, inv = np.unique(bd, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(ud)).astype(float)
    bdm = np.bincount(inv, weights=br, minlength=len(ud)) / cnt
    bd5 = np.bincount(inv, weights=br5, minlength=len(ud)) / cnt

    print("=" * 100)
    print("[基准·可买子集 T未涨停] n=%d  圣杯正例率=%.4f  事件PF=%.3f  均值=%.4f%%"
          % (len(br), base_stats[3], pf(br), base_stats[0] * 100))
    print("                        组合日: n_days=%d  PF=%.3f  日均=%.4f%%  Sharpe(ann)=%.3f  旧口径命中=%.4f 旧PF_day=%.3f"
          % (len(ud), pf(bdm), bdm.mean() * 100,
             bdm.mean() / bdm.std(ddof=1) * math.sqrt(252), (br5 > 0).mean(), pf(bd5)))
    print("=" * 100)

    out = {"version": 2, "test_start": TEST_START, "n_files": len(files),
           "baseline": {"n": int(len(br)), "pos_rate": round(base_stats[3], 4),
                        "pf_event": round(pf(br), 4),
                        "mean_ret_pct": round(base_stats[0] * 100, 4),
                        "n_days": int(len(ud)), "pf_day": round(pf(bdm), 4),
                        "mean_day_pct": round(float(bdm.mean() * 100), 4),
                        "sharpe_ann": round(float(bdm.mean() / bdm.std(ddof=1) * math.sqrt(252)), 3),
                        "hit_old": round(float((br5 > 0).mean()), 4),
                        "pf_day_old": round(pf(bd5), 4)},
           "factors": {}}
    rows = []
    for nm in (names or []):
        if nm not in accs:
            continue
        r = summarize(accs[nm], base_stats)
        if r is None or r["n"] < MIN_N:
            continue
        r["GATE_EVENT"] = bool(r["hit_multiple"] >= GATE_HIT and r["pf_event"] >= GATE_PF
                               and r["t_stat"] >= GATE_T)
        r["GATE_DAY"] = bool(r["hit_multiple"] >= GATE_HIT and r["pf_day"] >= GATE_PF
                             and r["t_stat"] >= GATE_T)
        out["factors"][nm] = r
        rows.append((nm, r))
    rows.sort(key=lambda x: (-x[1]["hit_multiple"]))
    hdr = "%-22s %7s %8s %8s %8s %8s %8s %8s %8s %6s %s"
    print(hdr % ("因子", "n", "正例率", "命中倍数", "PF事件", "PF组合日", "日均%", "Sharpe", "t", "旧命中", "门控(组合日)"))
    for nm, r in rows:
        print(hdr % (nm, r["n"], "%.4f" % r["pos_rate"], "%.4f" % r["hit_multiple"],
                     "%.3f" % r["pf_event"], "%.3f" % r["pf_day"], "%.4f" % r["mean_day_pct"],
                     "%.2f" % r["sharpe_ann"], "%.2f" % r["t_stat"], "%.4f" % r["hit_old"],
                     "PASS" if r["GATE_DAY"] else ("EVT" if r["GATE_EVENT"] else "REJECT")))
    print("\n[门控·铁律=组合日] n>=200 且 命中倍数>=1.15 且 PF_day>=1.30 且 t>=2.0")
    print("[EVT]=仅逐事件口径过线（历史教训：逐事件过线≠可部署，以组合日为准）")
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[out] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
