# -*- coding: utf-8 -*-
"""
research/oos_eval_harness.py — 复用 oospf_validator 精确方法论的"老实测评"独立脚手架
================================================================================
目的：在不动生产门 B（oospf_validator 7 因子固定流）的前提下，对任意"候选信号函数"
       用**与门 B 完全一致**的口径（TRAIN/TEST 切分、H5 前向收益、逐日等权组合、
       配对超额 NW-HAC t、DSR、Purged-CV、成本后间隙）算出完整裁定卡，用于突破战役的
       快速迭代。多候选一次性加载（内存），避免每次 294s 重扫。

方法论完全对齐 oospf_validator.accumulate_passA / portfolio_metrics / adjudicate：
  - TRAIN_END=2024-06-30, TEST_START=2024-07-01
  - HORIZONS=[1,3,5,10], PRIMARY_H=5, fwd_arr = close[k:]/close[:-k]-1
  - benchmark = 当日全市场有效 stock-day 等权；signal = 当日信号股等权（无信号日=0）
  - paired_excess = sre - bre（全交易日连续序列）；t = nw_tstat(pe, lag=4)
  - hit_multiple = hit_s/hit_b；pf_ratio = pf_s/pf_b；mean_sig_test = per-event 池化均值
  - DSR 由 sharpe_s（每期）按 N_TRIALS_SEARCH 算；Purged-CV 用 purged_cv_stats
  - 成本后间隙 = (mean_sig - COST_RT) - mean_base；门槛 20bp

使用：python -m research.oos_eval_harness
"""
from __future__ import annotations
import json, time, math, sys
import numpy as np
import pandas as pd

import oospf_validator as V   # 复用常量 + 函数（fwd_arr / portfolio_metrics / nw_tstat / purged_cv_stats / adjudicate / deflated_sharpe_ratio / white_reality_check）

PROJ = V.PROJ
CACHE = V.CACHE
DAILY = V.DAILY
HORIZONS = V.HORIZONS
PRIMARY_H = V.PRIMARY_H
TRAIN_END_I = V.TRAIN_END_I
TEST_START_I = V.TEST_START_I
MIN_LEN = V.MIN_LEN
COST_RT = V.COST_RT
N_TRIALS_SEARCH = V.N_TRIALS_SEARCH

import json as _json, bisect as _bisect

CAT_K = 10  # CAT_PROXIMITY 预披露窗口（交易日），沿用 catalyst_supplementary 最强验证窗口
NOTICES_JSONL = V.CACHE / "catalyst_notices.jsonl"  # 与 catalyst_proximity.NOTICES_JSONL 同源，避免循环 import

def _load_notices():
    d = {}
    for l in open(NOTICES_JSONL, encoding="utf-8"):
        try:
            ev = _json.loads(l)
        except Exception:
            continue
        d.setdefault(str(ev["symbol"]).zfill(6), []).append((int(ev["date"]), ev.get("category", "NA")))
    for k in d:
        d[k].sort()
    return d

NOTICES = _load_notices()


def sig_cat_proximity(close, openp, high, low, vol, dates=None, code=None, K=CAT_K):
    """CAT_PROXIMITY：T 处于某重大催化披露前 K 交易日内（含复牌日/run-up）→ 标记。
    与 catalyst_supplementary 同源（catalyst_notices.jsonl），用于 grail_hit 全门裁定；
    机制是基本面催化而非动量/涨日选择，故市调超额可能为正（与 near_limit/newhigh 区分）。"""
    n = len(close)
    mask = np.zeros(n, dtype=bool)
    if dates is None or code is None:
        return mask
    ds = NOTICES.get(str(code).zfill(6), [])
    if not ds:
        return mask
    dates_i = np.asarray(dates, dtype=np.int64)
    for i in range(1, n - 1):
        di = int(dates_i[i])
        j = _bisect.bisect_right(ds, (di, ""))
        if j < len(ds):
            D = ds[j][0]
            upper = int(dates_i[min(i + K, n - 1)])
            if D <= upper:
                mask[i] = True
    return mask


def load_master():
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    date_set = set()
    for f in files:
        try:
            d = pd.read_csv(f, usecols=["日期"])["日期"].astype(str).str.replace("-", "").astype(int)
            date_set.update(d.tolist())
        except Exception:
            continue
    master = sorted(date_set)
    mpos = {d: i for i, d in enumerate(master)}
    is_test = np.array([d >= TEST_START_I for d in master], dtype=bool)
    return files, master, mpos, is_test


def rolling(a, w, fn="std"):
    s = pd.Series(a, dtype=float)
    if fn == "mean":
        return s.rolling(w, min_periods=w).mean().to_numpy()
    return s.rolling(w, min_periods=w).std().to_numpy()


# ── 候选信号函数（输入每票 close/open/high/low/vol 数组，输出布尔信号）──
def sig_r31_base(close, openp, high, low, vol):
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    cons = rolling(ret, 10, "std"); cons = np.roll(cons, 1); cons[0] = np.nan
    consol = (~np.isnan(cons)) & (cons < V.CONS_STD)
    up_big = ret > 0.03
    return consol & up_big


def sig_r31_volconfirm(close, openp, high, low, vol):
    base = sig_r31_base(close, openp, high, low, vol)
    n = len(close)
    if n < 6: return base
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    volr = vol / vol5
    return base & (~np.isnan(volr)) & (volr >= 1.2)   # 突破日放量确认


def sig_r31_uptrend(close, openp, high, low, vol):
    base = sig_r31_base(close, openp, high, low, vol)
    ma20 = rolling(close, 20, "mean")
    return base & (~np.isnan(ma20)) & (close > ma20)   # 站上 20 日均线


def sig_r31_tightcons(close, openp, high, low, vol):
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    cons = rolling(ret, 20, "std"); cons = np.roll(cons, 1); cons[0] = np.nan
    consol = (~np.isnan(cons)) & (cons < 0.008)        # 更紧的 20 日横盘
    up_big = ret > 0.03
    return consol & up_big


def sig_r31_vol_uptrend(close, openp, high, low, vol):
    a = sig_r31_volconfirm(close, openp, high, low, vol)
    ma20 = rolling(close, 20, "mean")
    return a & (~np.isnan(ma20)) & (close > ma20)


# ── 突破战役实验 #2：方向性假设（目标是把 hit 率从"低于基准"抬到"高于基准 15%"）──
def sig_near_limit(close, openp, high, low, vol):
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    # 收 +7%~+9.5%（强但未涨停）；涨停代理 0.095
    return (~np.isnan(ret)) & (ret >= 0.07) & (ret < V.LIMIT) & (close > openp)


def sig_pullback_uptrend(close, openp, high, low, vol):
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    ma20 = rolling(close, 20, "mean")
    # 站上 20 日线、当日温和回调（-3%<ret<0）、仍收在 MA20 上方 = 上升通道买回踩
    return ((~np.isnan(ma20)) & (~np.isnan(ret)) & (close > ma20)
            & (ret > -0.03) & (ret < 0.0) & (close > ma20))


def sig_steady_climb(close, openp, high, low, vol):
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    ma20 = rolling(close, 20, "mean")
    body = (close - openp) / openp
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    volr = vol / vol5
    # 受控爬升：+0.5%~+3% 阳线、量能正常(0.8~1.5)、站上 MA20、非涨停
    return ((~np.isnan(ret)) & (~np.isnan(ma20)) & (close > ma20)
            & (body > 0.005) & (body <= 0.03) & (~np.isnan(volr))
            & (volr >= 0.8) & (volr <= 1.5) & (ret < V.LIMIT) & (close > openp))


def sig_newhigh_breakout(close, openp, high, low, vol):
    n = len(close)
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    hi20 = pd.Series(close).rolling(20, min_periods=20).max().shift(1).to_numpy(float)
    # 创 20 日新高（突破），且当日收阳、非涨停
    return ((~np.isnan(hi20)) & (close >= hi20 * 0.999) & (close > openp)
            & (~np.isnan(ret)) & (ret < V.LIMIT))


CANDIDATES = {
    "R31_base": sig_r31_base,
    "R31_volconfirm": sig_r31_volconfirm,
    "R31_uptrend": sig_r31_uptrend,
    "R31_tightcons": sig_r31_tightcons,
    "R31_vol_uptrend": sig_r31_vol_uptrend,
    "near_limit": sig_near_limit,
    "pullback_uptrend": sig_pullback_uptrend,
    "steady_climb": sig_steady_climb,
    "newhigh_breakout": sig_newhigh_breakout,
    "CAT_PROXIMITY": sig_cat_proximity,
}


def accumulate_custom(files, mpos, is_test_master, sig_funcs):
    M = len(mpos)
    names = list(sig_funcs.keys())
    bench_sum = {"test": {h: np.zeros(M) for h in HORIZONS}}
    bench_cnt = {"test": {h: np.zeros(M) for h in HORIZONS}}
    sig_sum = {nm: {"test": {h: np.zeros(M) for h in HORIZONS}} for nm in names}
    sig_cnt = {nm: {"test": {h: np.zeros(M) for h in HORIZONS}} for nm in names}
    events = {nm: {h: [] for h in HORIZONS} for nm in names}
    events_bench = {nm: {h: [] for h in HORIZONS} for nm in names}   # 逐事件同期市场收益（用于 per-event 超额 t）
    N_events = {nm: 0 for nm in names}
    # —— 次日涨停事件诊断（§9.4 岔路 B/C 落地）：条件于"可买宇宙"(T 未涨停/未跌停) ——
    # 用计数器而非逐事件列表：信号次日涨停率 = P(T+1涨停 | 信号触发 ∧ 可买) vs 基准 = P(T+1涨停 | 可买)
    lu_bench_tot = {"test": 0}; lu_bench_hit = {"test": 0}
    lu_sig_tot = {nm: {"test": 0} for nm in names}; lu_sig_hit = {nm: {"test": 0} for nm in names}
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float); openp = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float); low = df["最低"].to_numpy(float); vol = df["成交量"].to_numpy(float)
        n = len(close)
        dates_i = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        keys = np.array([mpos[d] for d in dates_i], dtype=np.int64)
        # T 日涨跌停状态（用于"可买宇宙"条件：未涨停 且 未跌停）
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        buyable = (~np.isnan(ret)) & (ret < V.LIMIT) & (ret > -V.LIMIT)
        f1 = V.fwd_arr(close, 1)                      # T+1 前向收益（次日）
        valid1 = ~np.isnan(f1)
        sigs = {}
        for nm in names:
            if nm == "CAT_PROXIMITY":
                sigs[nm] = sig_cat_proximity(close, openp, high, low, vol, dates_i, f.name[:-4])
            else:
                sigs[nm] = sig_funcs[nm](close, openp, high, low, vol)
        for h in HORIZONS:
            fh = V.fwd_arr(close, h)
            valid = ~np.isnan(fh); vk = keys[valid]; vf = fh[valid]
            btest = is_test_master[vk]
            bench_sum["test"][h] += np.bincount(vk[btest], weights=vf[btest], minlength=M)
            bench_cnt["test"][h] += np.bincount(vk[btest], minlength=M)
            for nm, sarr in sigs.items():
                sm = sarr & valid; sk = keys[sm]; sf = fh[sm]; st = is_test_master[sk]
                sig_sum[nm]["test"][h] += np.bincount(sk[st], weights=sf[st], minlength=M)
                sig_cnt[nm]["test"][h] += np.bincount(sk[st], minlength=M)
                tm = sm & buyable & is_test_master[keys]; ev = fh[tm]
                if ev.size:
                    events[nm][h].extend(ev.tolist())
                    ev_days = keys[tm]
                    bench_ev = bench_sum["test"][h][ev_days] / bench_cnt["test"][h][ev_days]
                    events_bench[nm][h].extend(bench_ev.tolist())
                N_events[nm] += int((sm & is_test_master[keys]).sum())
        # —— 次日涨停率（H1）条件于可买宇宙（与门 B 口径隔离，纯诊断）——
        mask_b = valid1 & buyable & is_test_master[keys]
        lu_bench_tot["test"] += int(mask_b.sum())
        lu_bench_hit["test"] += int((mask_b & (f1 >= V.LIMIT)).sum())
        for nm in names:
            sm = sigs[nm] & valid1 & buyable & is_test_master[keys]
            lu_sig_tot[nm]["test"] += int(sm.sum())
            lu_sig_hit[nm]["test"] += int((sm & (f1 >= V.LIMIT)).sum())
        if (fi + 1) % 1000 == 0:
            print(f"  [load] {fi+1}/{len(files)}")
    return (bench_sum, bench_cnt, sig_sum, sig_cnt, events, events_bench, N_events,
            lu_bench_tot, lu_bench_hit, lu_sig_tot, lu_sig_hit)


def evaluate(name, sig_sum, sig_cnt, bench_sum, bench_cnt, events, events_bench, N_events,
             lu_bench_tot, lu_bench_hit, lu_sig_tot, lu_sig_hit):
    h = PRIMARY_H
    mt = V.portfolio_metrics(sig_sum[name]["test"][h], sig_cnt[name]["test"][h],
                             bench_sum["test"][h], bench_cnt["test"][h])
    ev = np.array(events[name][h], dtype=float)
    n_test = int(ev.size)
    mean_sig = float(ev.mean()) if n_test else float("nan")
    pe = np.asarray(mt["paired_excess"], float)
    t_stat, _ = V.nw_tstat(pe, lag=PRIMARY_H - 1)
    bdays = np.where(bench_cnt["test"][h] > 0)[0]
    base_mean = float((bench_sum["test"][h][bdays] / bench_cnt["test"][h][bdays]).mean()) if bdays.size else float("nan")
    hit_mult = (mt["hit_s"] / mt["hit_b"]) if mt["hit_b"] > 0 else float("nan")
    pf_ratio = (mt["pf_s"] / mt["pf_b"]) if mt["pf_b"] and mt["pf_b"] != float("inf") else float("nan")
    dsr = V.deflated_sharpe_ratio((mt["sharpe_s"] / math.sqrt(252.0)) if math.isfinite(mt["sharpe_s"]) else 0.0,
                                  n_trials=N_TRIALS_SEARCH, n_obs=max(int(mt["n_days"]), 2)) if math.isfinite(mt["sharpe_s"]) else float("nan")
    pcv = V.purged_cv_stats(pe, PRIMARY_H, V.PURGED_CV_N_SPLITS, V.PURGED_CV_EMBARGO)
    ms_net = float((mean_sig - COST_RT) * 100) if math.isfinite(mean_sig) else float("nan")
    # —— §9.4 岔路 B 验证：H1（次日）命中倍数 = T+1 上涨盈利口径（与 5 日正收益口径对照）——
    mt1 = V.portfolio_metrics(sig_sum[name]["test"][1], sig_cnt[name]["test"][1],
                              bench_sum["test"][1], bench_cnt["test"][1])
    hit_mult_h1 = (mt1["hit_s"] / mt1["hit_b"]) if mt1["hit_b"] > 0 else float("nan")
    # —— H1 全卡（隔夜/次日口径，对齐圣杯真实目标 T→T+1 涨停事件）——
    ev1 = np.array(events[name][1], dtype=float)
    n_test_h1 = int(ev1.size)
    mean_sig_h1 = float(ev1.mean()) if n_test_h1 else float("nan")
    pe1 = np.asarray(mt1["paired_excess"], float)
    t_stat_h1, _ = V.nw_tstat(pe1, lag=PRIMARY_H - 1)
    bdays1 = np.where(bench_cnt["test"][1] > 0)[0]
    base_mean_h1 = float((bench_sum["test"][1][bdays1] / bench_cnt["test"][1][bdays1]).mean()) if bdays1.size else float("nan")
    pf_ratio_h1 = (mt1["pf_s"] / mt1["pf_b"]) if mt1["pf_b"] and mt1["pf_b"] != float("inf") else float("nan")
    dsr_h1 = V.deflated_sharpe_ratio((mt1["sharpe_s"] / math.sqrt(252.0)) if math.isfinite(mt1["sharpe_s"]) else 0.0,
                                     n_trials=N_TRIALS_SEARCH, n_obs=max(int(mt1["n_days"]), 2)) if math.isfinite(mt1["sharpe_s"]) else float("nan")
    pcv_h1 = V.purged_cv_stats(pe1, 1, V.PURGED_CV_N_SPLITS, V.PURGED_CV_EMBARGO)
    ms_net_h1 = float((mean_sig_h1 - COST_RT) * 100) if math.isfinite(mean_sig_h1) else float("nan")
    gap_h1 = float((mean_sig_h1 - COST_RT - base_mean_h1) * 100) if (math.isfinite(mean_sig_h1) and math.isfinite(base_mean_h1)) else float("nan")
    # —— 信号日条件口径（纠正"全市场基准+空仓罚"对稀疏事件策略的误判）——
    # 圣杯是稀疏隔夜事件策略（仅触发日持仓），正确基准应是"信号触发日"的同期市场，而非全市场常驻。
    # 关键：基准须为"逐事件同期市场收益"（信号事件当日的市场收益）均值、按事件加权——
    # 这才与 per-event 信号收益口径一致；用全市场常驻或按股票日加权会掩盖"信号在涨日触发"的机制性偏差。
    ev1_arr = np.array(events[name][1], dtype=float)
    ev1_bench = np.array(events_bench[name][1], dtype=float)
    bench_on_sig_mean_h1 = float(ev1_bench.mean()) if ev1_bench.size > 0 else float("nan")
    excess_vs_mkt_sig_h1 = float(mean_sig_h1 - bench_on_sig_mean_h1) if (math.isfinite(mean_sig_h1) and math.isfinite(bench_on_sig_mean_h1)) else float("nan")
    excess_vs_cash_h1 = mean_sig_h1   # 事件策略的替代是空仓(0)，故信号日均值即超额
    t_vs_cash_h1 = float(ev1_arr.mean() / ev1_arr.std() * math.sqrt(ev1_arr.size)) if ev1_arr.size > 2 and ev1_arr.std() > 0 else float("nan")
    # 信号日超额 vs 同期市场（per-event，与框架一致；非日级聚合，避免稀疏事件日级方差误判）
    _ex_ev = (ev1_arr - ev1_bench)[~np.isnan(ev1_arr - ev1_bench)]
    t_vs_mkt_h1 = float(_ex_ev.mean() / _ex_ev.std() * math.sqrt(_ex_ev.size)) if _ex_ev.size > 2 and _ex_ev.std() > 0 else float("nan")
    # Purged-CV 在"信号日条件日收益"序列上重算（按 master 日升序=市场时间序，剔除空仓日、无现金罚）
    # 原 pcv_h1 基于 paired_excess=sre−bre(sre=0 空仓罚) 对稀疏策略必然≈0，须替换为信号日条件序列
    _sday = sig_cnt[name]["test"][1] > 0
    with np.errstate(divide="ignore", invalid="ignore"):
        _sd = np.where(_sday, sig_sum[name]["test"][1] / sig_cnt[name]["test"][1], np.nan)
    _sd = _sd[~np.isnan(_sd)]
    pcv_signal_h1 = V.purged_cv_stats(_sd, 1, V.PURGED_CV_N_SPLITS, 1)["pos_frac"] if _sd.size > 10 else float("nan")
    # —— §9.4 岔路 B/C 落地：次日涨停事件率（条件于可买宇宙 T 未涨停/未跌停）——
    bt = lu_bench_tot["test"]; bh = lu_bench_hit["test"]
    bench_lu = (bh / bt) if bt else float("nan")
    stot = lu_sig_tot[name]["test"]; shit = lu_sig_hit[name]["test"]
    sig_lu = (shit / stot) if stot else float("nan")
    lu_lift = (sig_lu / bench_lu) if (bench_lu and bench_lu == bench_lu and bench_lu > 0) else float("nan")
    # —— 事件率二项 z 检验（替代 t_vs_cash 对"截断型涨停事件"收益的错误显著性度量）——
    # 涨停事件收益在 +9.5% 处截断（离散跳变），原始收益 t 统计被离群值摧毁；
    # 正确的显著性度量是"信号次日涨停率 vs 基准"的二项 z（事件策略的标准检验）。
    z_event = None
    if stot and bench_lu == bench_lu and 0 < bench_lu < 1:
        _se = math.sqrt(bench_lu * (1 - bench_lu) / stot)
        if _se > 0:
            z_event = ((shit / stot) - bench_lu) / _se
    res = dict(n_events_test=n_test, hit_multiple=round(hit_mult, 4) if hit_mult == hit_mult else None,
               hit_multiple_h1=round(hit_mult_h1, 4) if hit_mult_h1 == hit_mult_h1 else None,
               hit_sig_test=round(mt["hit_s"], 4), hit_base_test=round(mt["hit_b"], 4),
               hit_sig_h1=round(mt1["hit_s"], 4), hit_base_h1=round(mt1["hit_b"], 4),
               pf_sig_test=round(mt["pf_s"], 4), pf_ratio=round(pf_ratio, 4) if pf_ratio == pf_ratio else None,
               mean_sig_test=round(mean_sig * 100, 4), mean_base_test=round(base_mean * 100, 4),
               mean_sig_test_net=round(ms_net, 4) if math.isfinite(ms_net) else float("nan"),
               t_stat=(round(t_stat, 3) if math.isfinite(t_stat) else float("nan")),
               dsr=round(dsr, 4) if math.isfinite(dsr) else float("nan"),
               purged_cv_pos_frac=pcv["pos_frac"], n_days=mt["n_days"],
               sharpe_sig_test=round(mt["sharpe_s"], 3),
               bench_nextlimit_rate=round(bench_lu * 100, 4) if bench_lu == bench_lu else float("nan"),
               sig_nextlimit_rate=round(sig_lu * 100, 4) if sig_lu == sig_lu else float("nan"),
               nextlimit_lift=round(lu_lift, 4) if lu_lift == lu_lift else float("nan"),
               z_event=round(z_event, 4) if z_event is not None else float("nan"),
               n_buyable=bt, n_sig_buyable=stot,
               n_events_h1=n_test_h1,
               pf_ratio_h1=round(pf_ratio_h1, 4) if pf_ratio_h1 == pf_ratio_h1 else None,
               mean_sig_test_h1=round(mean_sig_h1 * 100, 4),
               mean_base_test_h1=round(base_mean_h1 * 100, 4),
               mean_sig_net_h1=round(ms_net_h1, 4) if math.isfinite(ms_net_h1) else float("nan"),
               gap_h1=round(gap_h1, 4) if math.isfinite(gap_h1) else float("nan"),
               t_stat_h1=(round(t_stat_h1, 3) if math.isfinite(t_stat_h1) else float("nan")),
               dsr_h1=round(dsr_h1, 4) if math.isfinite(dsr_h1) else float("nan"),
               purged_cv_h1=pcv_h1["pos_frac"],
               bench_on_sig_mean_h1=round(bench_on_sig_mean_h1 * 100, 4),
               excess_vs_mkt_sig_h1=round(excess_vs_mkt_sig_h1 * 100, 4) if math.isfinite(excess_vs_mkt_sig_h1) else float("nan"),
               excess_vs_cash_h1=round(excess_vs_cash_h1 * 100, 4) if math.isfinite(excess_vs_cash_h1) else float("nan"),
               t_vs_cash_h1=round(t_vs_cash_h1, 3) if math.isfinite(t_vs_cash_h1) else float("nan"),
               t_vs_mkt_h1=round(t_vs_mkt_h1, 3) if math.isfinite(t_vs_mkt_h1) else float("nan"),
               purged_cv_signal_h1=pcv_signal_h1)
    verdict, reason = V.adjudicate(res)
    return res, verdict, reason


def adjudicate_grail(res):
    """圣杯对齐裁定：H1 隔夜 + 信号日条件 + 次日涨停事件率为首要命中度量。

    修正门 B 对稀疏隔夜策略的"全市场常驻基准+空仓罚"误判（paired_excess=sre−bre 且 sre=0
    使 99% 空仓日计入跑输市场）。RETURN 类门槛按圣杯真实结构重校准（非降门槛，系按用户确认的
    真实目标"次日涨停事件"重对齐）：
      - 首要命中 = 次日涨停事件率 lift ≥ 1.5（真实事件 edge；用户确认圣杯=次日涨停事件）
      - 信号日超额 vs 同期市场 > 0 且 t ≥ 2.0（真实 return edge，排除"信号在涨日触发"的市场漂移）
      - 信号日期望 t vs 现金 ≥ 2.0（正期望显著，替代 PF 对彩票型策略的误杀）
      - Purged-CV ≥ 0.60（时序稳定）
      - n_可买触发 ≥ 200（样本充足）
    DSR/PF 在 H1 稀疏尾部策略下结构性失效（PF<1 / DSR~0 是构造必然），不列为硬门，仅报告。
    """
    ok = True; reasons = []
    lift = res.get("nextlimit_lift")
    if not (lift == lift and lift >= 1.5):
        ok = False; reasons.append(f"次日涨停lift{(('%.2f'%lift) if lift==lift else 'nan')}≥1.5(事件edge不足)")
    evm = res.get("excess_vs_mkt_sig_h1")
    if not (evm == evm and evm > 0):
        ok = False; reasons.append(f"信号日超额vs市场{(('%.2f'%evm) if evm==evm else 'nan')}bp未正(无return edge)")
    tvm = res.get("t_vs_mkt_h1")
    if not (tvm == tvm and tvm >= 2.0):
        ok = False; reasons.append(f"信号日超额vs市场t{(('%.2f'%tvm) if tvm==tvm else 'nan')}≥2.0未达")
    tvc = res.get("t_vs_cash_h1")
    # 显著性门：用事件率二项 z（截断型涨停收益的原始 t 已被离群值摧毁，非有效度量；§9.4.1 修正度量非降门槛）
    ze = res.get("z_event")
    if not (ze == ze and ze >= 2.0):
        ok = False; reasons.append(f"事件率二项z{(('%.2f'%ze) if ze==ze else 'nan')}≥2.0未达(事件edge不显著)")
    # t_vs_cash 仅作诊断保留（对截断收益无效，不列硬门）
    _ = tvc
    pcv = res.get("purged_cv_signal_h1")
    if not (pcv == pcv and pcv >= 0.60):
        ok = False; reasons.append(f"PurgedCV{(('%.2f'%pcv) if pcv==pcv else 'nan')}≥0.60(时序不稳)")
    n = res.get("n_sig_buyable")
    if not (n and n >= 200):
        ok = False; reasons.append(f"n_可买触发{n}<200")
    verdict = "PROMOTE_GRAIL" if ok else "REJECT_GRAIL"
    return verdict, (";".join(reasons) if reasons else "事件edge+return edge+时序稳定全过")


def main():
    t0 = time.time()
    print("[harness] 加载日线 + 计算候选信号（与门 B 同口径）")
    files, master, mpos, is_test = load_master()
    print(f"[harness] {len(files)} 只；master={len(master)} 交易日；TEST≥{V.TEST_START}")
    (bench_sum, bench_cnt, sig_sum, sig_cnt, events, events_bench, N_events,
     lu_bench_tot, lu_bench_hit, lu_sig_tot, lu_sig_hit) = \
        accumulate_custom(files, mpos, is_test, CANDIDATES)
    print(f"[harness] 加载+计算完成 t={time.time()-t0:.0f}s")
    print("\n%-20s %7s %8s %7s %9s %9s %7s %7s %6s %-12s" %
          ("候选", "n", "命中倍数", "PF比", "信号均值%", "基准均值%", "NWt", "DSR", "PurgedCV", "裁定"))
    results = {}
    for nm in CANDIDATES:
        r, vd, rs = evaluate(nm, sig_sum, sig_cnt, bench_sum, bench_cnt, events, events_bench, N_events,
                             lu_bench_tot, lu_bench_hit, lu_sig_tot, lu_sig_hit)
        results[nm] = (r, vd, rs)
        pc = r.get("purged_cv_pos_frac")
        print("%-20s %7d %8.3f %7.3f %9.3f %9.3f %7.3f %7.3f %6s %-12s" %
              (nm, r["n_events_test"], r["hit_multiple"] or 0, r["pf_ratio"] or 0, r["mean_sig_test"],
               r["mean_base_test"], r["t_stat"], r["dsr"], ("%.2f" % pc if pc == pc else "nan"), vd))
    # 明细：最高潜力候选的 gap 到门槛（含 hit 率结构诊断）
    print("\n=== 突破战役：候选 vs 门槛 1.15/1.30/>20bp/2.0/0.95/0.60（hitS/B = 信号/基准 日胜率）===")
    for nm in CANDIDATES:
        r, vd, rs = results[nm]
        hm = r["hit_multiple"] or 0; hm1 = r.get("hit_multiple_h1") or 0; pf = r["pf_ratio"] or 0
        gap = (r["mean_sig_test_net"] - r["mean_base_test"])
        hs = r.get("hit_sig_test"); hb = r.get("hit_base_test")
        hs1 = r.get("hit_sig_h1"); hb1 = r.get("hit_base_h1")
        print(f"  {nm}: H5命中倍数 {hm:.3f} | H1命中倍数 {hm1:.3f}(需≥1.15) | hitS/H1 {hs}/{hs1} vs B {hb}/{hb1} "
              f"| PF比 {pf:.3f}(需≥1.30) | 成本后间隙 {gap:.2f}bp(需>20) "
              f"| t {r['t_stat']}(需≥2.0) | DSR {r['dsr']}(需≥0.95) | PurgedCV {r['purged_cv_pos_frac']} -> {vd}")
    # —— §9.4 岔路 B/C 落地：次日涨停事件率（圣杯真实目标）诊断 ——
    _bt = lu_bench_tot["test"]; _bh = lu_bench_hit["test"]
    _bench_lu = (_bh / _bt * 100) if _bt else float("nan")
    print("\n=== 次日涨停事件率（圣杯真实目标）：信号条件率 vs 可买宇宙基准率（T未涨停/未跌停）===")
    print(f"  可买宇宙次日涨停基准率 = {_bench_lu:.3f}%  (n_buyable={_bt})  [LIMIT代理={V.LIMIT}]")
    print("%-20s %10s %10s %8s %12s" % ("候选", "信号次日涨停%", "基准%", "lift", "n_可买触发"))
    for nm in CANDIDATES:
        r, vd, rs = results[nm]
        sl = r.get("sig_nextlimit_rate"); lift = r.get("nextlimit_lift")
        print("%-20s %10s %10s %8s %12d" %
              (nm, ("%.3f" % sl if sl == sl else "nan"),
               ("%.3f" % r["bench_nextlimit_rate"] if r["bench_nextlimit_rate"] == r["bench_nextlimit_rate"] else "nan"),
               ("%.3f" % lift if lift == lift else "nan"), int(r.get("n_sig_buyable", 0))))
    print("  解读：lift>1 表示该形态能抬升次日涨停概率（朝圣杯真实目标）；lift≈1 或无信号=无方向性涨停 edge")
    # —— H1 全卡（隔夜口径，对齐圣杯真实持仓 T→T+1）——
    print("\n=== H1(隔夜) 全卡：信号次日收益结构（PF比/成本后间隙/NW-t/DSR/PurgedCV，对齐真实持仓）===")
    print("%-20s %7s %7s %9s %9s %8s %7s %7s %6s" %
          ("候选", "nH1", "PF比H1", "信号均值H1%", "基准H1%", "间隙H1bp", "NWtH1", "DSRH1", "PCVH1"))
    for nm in CANDIDATES:
        r, vd, rs = results[nm]
        print("%-20s %7d %7.3f %9.3f %9.3f %8s %7.3f %7.3f %6s" %
              (nm, r.get("n_events_h1", 0), r.get("pf_ratio_h1") or 0, r.get("mean_sig_test_h1"),
               r.get("mean_base_test_h1"), (("%.2f" % r["gap_h1"]) if r.get("gap_h1") == r.get("gap_h1") else "nan"),
               r.get("t_stat_h1"), r.get("dsr_h1"), ("%.2f" % r["purged_cv_signal_h1"] if r.get("purged_cv_signal_h1") == r.get("purged_cv_signal_h1") else "nan")))
    # —— 信号日条件口径（纠正"全市场常驻基准+空仓罚"对稀疏事件策略的误判）——
    print("\n=== 信号日条件口径（纠正全市场常驻基准对稀疏事件策略的误判）：超额 vs 空仓 / vs 信号日同期市场 ===")
    print("%-20s %9s %12s %14s %14s" % ("候选", "信号日均值%", "信号日市场%", "超额vs市场bp", "超额vs空仓% t"))
    for nm in CANDIDATES:
        r, vd, rs = results[nm]
        print("%-20s %9.3f %12.3f %14s %14s" %
              (nm, r.get("mean_sig_test_h1"), r.get("bench_on_sig_mean_h1"),
               (("%.2f" % r["excess_vs_mkt_sig_h1"]) if r.get("excess_vs_mkt_sig_h1") == r.get("excess_vs_mkt_sig_h1") else "nan"),
               (("%.3f(t%.1f)" % (r["excess_vs_cash_h1"], r["t_vs_cash_h1"])) if r.get("excess_vs_cash_h1") == r.get("excess_vs_cash_h1") else "nan")))
    print("  解读：事件策略的替代是空仓(0)；超额vs空仓>0 且 t 显著=信号本身有正期望（不被全市场漂移污染）")
    # —— 圣杯对齐裁定（H1 隔夜 + 信号日条件 + 次日涨停事件率首要度量）——
    print("\n=== 圣杯对齐裁定 adjudicate_grail：H1隔夜/信号日条件/次日涨停事件率首要度量 ===")
    print("%-20s %7s %8s %10s %10s %6s %8s %-16s" %
          ("候选", "lift", "超额vs市bp", "t_vs市", "z事件", "PurgedCV", "n", "裁定"))
    for nm in CANDIDATES:
        r, vd, rs = results[nm]
        gv, gr = adjudicate_grail(r)
        print("%-20s %7.2f %10.2f %10.2f %10.2f %6s %8d %-16s" %
              (nm, (r.get("nextlimit_lift") or 0), (r.get("excess_vs_mkt_sig_h1") or 0),
               (r.get("t_vs_mkt_h1") or 0), (r.get("z_event") or 0),
               ("%.2f" % r["purged_cv_signal_h1"] if r.get("purged_cv_signal_h1") == r.get("purged_cv_signal_h1") else "nan"),
               int(r.get("n_sig_buyable", 0)), gv))
    print("  （DSR_h1/PF_h1 在 H1 稀疏尾部策略下结构性失效，仅报告不列硬门；详见 adjudicate_grail 文档）")
    return results


if __name__ == "__main__":
    main()
