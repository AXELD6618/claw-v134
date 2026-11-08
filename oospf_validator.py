# -*- coding: utf-8 -*-
"""
oospf_validator.py — OOS Top-K / 组合 PF 验证框架 (限up_base 研究层铁律落地)
================================================================================
铁律工程化：选股只认「Top-K 命中倍数 + 组合 PF」，单因子 IC≈0 ≠ 可部署。
本脚本把这条铁律落成可复现的验证器：

  1. 时间前向切分：TRAIN ≤ 2024-06-30（仅建基线/基准），TEST ≥ 2024-07-01（纯 OOS，
     信号计算只用 ≤t 的历史，无未来函数）。
  2. 每个因子在每个交易日产出布尔信号（尾盘收盘入场代理）；建仓后持 H∈{1,3,5,10} 日
     close-to-close 前向收益。
  3. 逐日等权组合：
       - 基准组合 = 当日全市场有效 stock-day 等权（买全市场）；
       - 信号组合 = 当日所有信号股等权（无信号日持币=0）。
     由此得到组合权益曲线、组合 PF、命中率。
  4. 命中倍数 = 信号组合命中率 / 基准组合命中率；PF 比 = 信号 PF / 基准 PF。
  5. 边缘显著性：TEST 事件池均值、t 统计、95% 正态 CI；TRAIN vs TEST 均值漂移（过拟合探针）。
  6. 随机空对照：相同事件频率的乱序信号，作为「纯偶然」基准。

裁定门槛（写在 adjudicate()，可复核）：
  PROMOTE iff  n_test(H5)≥200
              AND 命中倍数(H5) ≥ 1.15
              AND 信号PF(H5)   ≥ 1.30
              AND 信号均值(H5) > 基准均值(H5) + 0.20（绝对 20bp 经济间隙，成本后口径）
              AND t_stat(H5)   ≥ 2.0
              [可选加严，缺省不强制以保向后兼容]
              AND DSR(H5)      ≥ 0.95（deflated Sharpe，按 N_TRIALS_SEARCH 多重检验校正）
              AND WhiteRC p     < 0.10（跨因子全局数据窥探护栏）
              AND PurgedCV 正向折占比 ≥ 0.60（事件窗 embargo 时序稳定性，López de Prado）
  否则 REJECT（含 样本不足）。成本后：信号均值已扣 COST_RT 往返成本代理。
  注：Purged-CV（H=5 重叠标签 + 时段窥探）已由 purged_cv_stats 接入为可选加严 AND 条件；
      此前仅靠 NW HAC lag=4 修正 SE，无法识别"edge 集中于少数时段"的时序不稳定性，现已补齐。

因子（与 R31/R32 既有定义一致，便于对账）：
  R31_breakout : 横后突破 = consol(10日收益std<1%) & up_big(ret>3%)，收盘入场
  ylim         : 昨日涨停 = is_limit[t-1] & ~is_limit[t]，收盘入场
  R32_d2       : 日不落 3 日形态 D2 入场（与 round32_backtest 同口径）
  random       : 频率匹配随机空对照

数据：backtest_cache/daily/*.csv（5225 只，open/close/high/low/vol）。
输出：backtest_cache/oospf_report.json + 控制台；run_all() 另写 oospf_REPORT.md。
"""
from __future__ import annotations
import json, time, math
from pathlib import Path
import numpy as np
import pandas as pd
from research.nw_stats import nw_tstat
from research.multiple_testing import deflated_sharpe_ratio, white_reality_check
from research.purged_cv import purged_kfold_indices

PROJ = Path(__file__).resolve().parent
CACHE = PROJ / "backtest_cache"
DAILY = CACHE / "daily"

# ── 时间前向切分 ───────────────────────────────────────────────
TRAIN_END = "2024-06-30"
TEST_START = "2024-07-01"
TEST_START_I = int(TEST_START.replace("-", ""))
TRAIN_END_I = int(TRAIN_END.replace("-", ""))

HORIZONS = [1, 3, 5, 10]
PRIMARY_H = 5
MIN_LEN = 60          # 一只票最少交易日（覆盖所有因子历史需求）

# ── 门 B 完整化：多重检验 / 成本校正（DSR + WhiteRC + 成本后；Purged-CV 待补）──
# 这些是新增强制/可选加严项，全部为 AND 条件，只抬高门槛、绝不降低既有铁律。
N_TRIALS_SEARCH = 32        # 累计候选因子/战法搜索次数（deflated Sharpe 多重检验惩罚基数；须随真实搜索量更新）
DSR_MIN = 0.95              # deflated Sharpe 多重检验通过线（Bailey & López de Prado 2014）
WHITE_RC_P_MAX = 0.10       # White(2000) 现实检验 p 值上限（跨因子全局数据窥探护栏）
COST_RT = 0.002             # A 股尾买持有 H5 往返成本代理 ~0.2%（佣+印+滑点），成本后从信号均值扣除
WHITE_RC_N_BOOT = 500       # 自动跑默认 block bootstrap 次数（手动可上调以提高 p 值精度）

# ── 门 B 完整化：Purged-CV（事件窗 embargo 时序稳定性，López de Prado）──
# NW HAC 仅修正 SE，无法识别"edge 集中在少数时段"的时序不稳定性；Purged-CV 用
# embargo 切分时间折，验证 edge 在多数时间折正向，杜绝 H 重叠标签泄漏 + 时段窥探。
PURGED_CV_N_SPLITS = 5            # 时间折数（连续块，非随机）
PURGED_CV_EMBARGO = PRIMARY_H - 1 # embargo = H-1 = 4（标签窗口长）
PURGED_CV_POS_FRAC_MIN = 0.60     # 正向折占比下限（≥3/5 折正向 = 时序稳定）

# ── R31/R32 既有参数（与 round31/round32 一致，便于对账）──────────
CONS_STD = 0.01       # 横盘：10日收益标准差<1%
LIMIT = 0.095         # 涨停代理
VOL_LO, VOL_HI = 1.1, 1.5
SMALL_BODY = 0.03
MA_MIN = 20


def fwd_arr(close, k):
    n = len(close)
    out = np.full(n, np.nan)
    if n > k:
        out[:n - k] = close[k:] / close[:-k] - 1.0
    return out


def rolling(arr, w, fn="mean"):
    s = pd.Series(arr, dtype=float)
    if fn == "mean":
        return s.rolling(w, min_periods=w).mean().to_numpy()
    return s.rolling(w, min_periods=w).std().to_numpy()


# ── 因子函数：输入每票数组，输出布尔信号(长度 n) ──────────────────
def factor_r31_breakout(close, vol):
    n = len(close)
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    cons = rolling(ret, 10, "std")
    cons = np.roll(cons, 1)            # shift(1) 避免未来函数
    cons[0] = np.nan
    consol = (~np.isnan(cons)) & (cons < CONS_STD)
    up_big = ret > 0.03
    return consol & up_big


def factor_ylim(close):
    n = len(close)
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    is_limit = ret >= LIMIT
    is_limit_prev = np.roll(is_limit, 1)
    is_limit_prev[0] = False
    return is_limit_prev & (~is_limit)


def factor_r32_d2(close, openp, low, vol):
    n = len(close)
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    is_limit = ret >= LIMIT
    ma5 = rolling(close, 5)
    ma10 = rolling(close, 10)
    ma20 = rolling(close, 20)
    sig = np.zeros(n, dtype=bool)
    for i in range(MA_MIN + 2, n - 0):
        i0, i1, i2 = i - 2, i - 1, i
        if i0 < MA_MIN:
            continue
        # D0 扇面
        fan = (ma5[i0] > ma10[i0] > ma20[i0] and close[i0] > ma5[i0]
               and ma5[i0] > ma5[i0 - 1] and ma10[i0] > ma10[i0 - 1] and ma20[i0] > ma20[i0 - 1])
        if not (is_limit[i0] and fan):
            continue
        c0d = close[i0]
        # D1 洗盘
        o1, c1, l1, v1 = openp[i1], close[i1], low[i1], vol[i1]
        v0 = vol[i0]
        d1 = (o1 < c0d) and (c1 < o1) and (c1 < c0d) and (v1 >= VOL_LO * v0) and (v1 <= VOL_HI * v0) and (l1 >= low[i0])
        if not d1:
            continue
        # D2 企稳入腹
        o2, c2, v2 = openp[i2], close[i2], vol[i2]
        if o2 <= 0:
            continue
        body = (c2 - o2) / o2
        d2 = (c2 > o2) and (0 < body <= SMALL_BODY) and (v2 < v1) and (close[i1] <= c2 <= openp[i1])
        if d2:
            sig[i2] = True
    return sig


def factor_oversold(close, vol):
    """深跌放量(oversold_sig)：T日收盘跌幅 pct=(close/prev_close-1) <= -0.04 且
    放量 volr=vol/MA5(前5日量,不含当日) >= 1.5。PIT 严格：prev_close=前一行收盘。
    与 moves_oversold_oos 定义一致（分数单位），OOS(2023~2026 全市场5225只)已 PROMOTE
    （PF_t5=1.3826>随机真及格线1.259、命中倍数>=1.15、t>=2.0、PF_vs_bottom>=1.30、
    DSR>=0.95、WhiteRC p<0.10、二元方向正）。事件型特征：仅做有/无二元判定，
    禁止按跌幅强度排序（严重度反序）。宁波东力(10-27收-6.46%→10-28+10.03%)即其实例。"""
    n = len(close)
    if n < 6:
        return np.zeros(n, dtype=bool)
    prev = np.concatenate([[np.nan], close[:-1]])
    pct = close / prev - 1.0
    pct[0] = np.nan
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)  # MA5 不含当日
    volr = vol / vol5
    sig = (~np.isnan(pct)) & (pct <= -0.04) & (~np.isnan(volr)) & (volr >= 1.5)
    return sig


def factor_oversold_deep(close, vol):
    """深超跌放量(oversold_deep)：pct < -5.5%（深跌口袋）。

    2026-10-28 反解发现前序假设方向相反：宽口径(<= -4%) mean=1.02%、浅超跌(-4%~-5.5%) mean=0.271%(<基线0.540%)，
    反解深跌子集 mean≈1.49% —— 即 edge 在**深跌**而非浅超跌。本因子直接测深跌口袋过规范门。"""
    n = len(close)
    if n < 6:
        return np.zeros(n, dtype=bool)
    prev = np.concatenate([[np.nan], close[:-1]])
    pct = close / prev - 1.0
    pct[0] = np.nan
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    volr = vol / vol5
    sig = (~np.isnan(pct)) & (pct < -0.055) & (~np.isnan(volr)) & (volr >= 1.5)
    return sig


def factor_oversold_shallow(close, vol):
    """浅超跌放量(oversold_shallow)：宽口径 oversold_sig 的**窄区间解锁版**(2026-10-28 用户授权重测)。

    定义：T日收盘跌幅 pct 落在 **[-5.5%, -4%]**（即 -0.055 <= pct <= -0.04）且 volr>=1.5。
    动机：既有严重度分析显示 oversold 的 edge 集中在**浅超跌**，深跌(-8%+)反弱；
    宽口径(pct<=-4%)因混入深跌票在组合口径下截面共振 → 组合PF 0.686 REJECT。
    收窄到浅超跌区间隔离该效应，再过规范门（组合PF>=1.30 且 命中倍数>=1.15）。
    """
    n = len(close)
    if n < 6:
        return np.zeros(n, dtype=bool)
    prev = np.concatenate([[np.nan], close[:-1]])
    pct = close / prev - 1.0
    pct[0] = np.nan
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)  # MA5 不含当日
    volr = vol / vol5
    sig = ((~np.isnan(pct)) & (pct <= -0.04) & (pct >= -0.055)
           & (~np.isnan(volr)) & (volr >= 1.5))
    return sig


def build_master(date_set):
    master = sorted(date_set)
    mpos = {d: i for i, d in enumerate(master)}
    return master, mpos


def accumulate_passA(files, mpos, is_test_master):
    """Pass A: 收集日期 + 计算 3 个真实因子 + 基准。返回所有累加器与事件池。"""
    M = len(mpos)
    # benchmark 累加器（全市场，train/test 分开）
    bench_sum = {"train": {h: np.zeros(M) for h in HORIZONS},
                 "test": {h: np.zeros(M) for h in HORIZONS}}
    bench_cnt = {"train": {h: np.zeros(M) for h in HORIZONS},
                 "test": {h: np.zeros(M) for h in HORIZONS}}
    # 信号累加器
    _FACTORS = ["R31_breakout", "ylim", "R32_d2", "oversold_sig", "oversold_shallow", "oversold_deep"]
    sig_sum = {f: {"train": {h: np.zeros(M) for h in HORIZONS},
                   "test": {h: np.zeros(M) for h in HORIZONS}} for f in _FACTORS}
    sig_cnt = {f: {"train": {h: np.zeros(M) for h in HORIZONS},
                   "test": {h: np.zeros(M) for h in HORIZONS}} for f in _FACTORS}
    # 事件池（TEST 池化收益，用于 t 统计 / CI / train-test 漂移）
    events = {f: {h: [] for h in HORIZONS} for f in _FACTORS}

    N_valid_test = 0
    N_events_test = {"R31_breakout": 0, "ylim": 0, "R32_d2": 0, "oversold_sig": 0, "oversold_shallow": 0, "oversold_deep": 0}

    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float)
        openp = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float)
        low = df["最低"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        n = len(close)
        # 日期 → master idx
        dates_i = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        keys = np.array([mpos[d] for d in dates_i], dtype=np.int64)

        # 因子
        f_r31 = factor_r31_breakout(close, vol)
        f_ylim = factor_ylim(close)
        f_r32 = factor_r32_d2(close, openp, low, vol)
        f_oversold = factor_oversold(close, vol)
        f_shallow = factor_oversold_shallow(close, vol)
        f_deep = factor_oversold_deep(close, vol)
        factors = {"R31_breakout": f_r31, "ylim": f_ylim, "R32_d2": f_r32,
                    "oversold_sig": f_oversold, "oversold_shallow": f_shallow, "oversold_deep": f_deep}

        for h in HORIZONS:
            fh = fwd_arr(close, h)
            valid = ~np.isnan(fh)
            vk = keys[valid]
            vf = fh[valid]
            # 基准
            btrain = ~is_test_master[vk]
            btest = is_test_master[vk]
            bench_sum["train"][h] += np.bincount(vk[btrain], weights=vf[btrain], minlength=M)
            bench_cnt["train"][h] += np.bincount(vk[btrain], minlength=M)
            bench_sum["test"][h] += np.bincount(vk[btest], weights=vf[btest], minlength=M)
            bench_cnt["test"][h] += np.bincount(vk[btest], minlength=M)
            N_valid_test += int(btest.sum())
            # 各因子信号
            for fname, farr in factors.items():
                sm = farr & valid
                sk = keys[sm]
                sf = fh[sm]
                st = is_test_master[sk]
                sig_sum[fname]["train"][h] += np.bincount(sk[~st], weights=sf[~st], minlength=M)
                sig_cnt[fname]["train"][h] += np.bincount(sk[~st], minlength=M)
                sig_sum[fname]["test"][h] += np.bincount(sk[st], weights=sf[st], minlength=M)
                sig_cnt[fname]["test"][h] += np.bincount(sk[st], minlength=M)
                # TEST 事件池
                tm = sm & is_test_master[keys]
                ev = fh[tm]
                if ev.size:
                    events[fname][h].extend(ev.tolist())
                N_events_test[fname] += int((sm & is_test_master[keys]).sum())

        if (fi + 1) % 500 == 0:
            print(f"  [passA] {fi+1}/{len(files)} 已处理")

    return bench_sum, bench_cnt, sig_sum, sig_cnt, events, N_valid_test, N_events_test


def accumulate_random(files, mpos, is_test_master, p, seed=20260701):
    """Pass B: 仅随机空对照，频率 p（与真实因子聚合事件率匹配）。"""
    M = len(mpos)
    sig_sum = {"random": {"train": {h: np.zeros(M) for h in HORIZONS},
                          "test": {h: np.zeros(M) for h in HORIZONS}}}
    sig_cnt = {"random": {"train": {h: np.zeros(M) for h in HORIZONS},
                          "test": {h: np.zeros(M) for h in HORIZONS}}}
    events = {"random": {h: [] for h in HORIZONS}}
    rng_master = np.random.default_rng(seed)
    fi = 0
    for f in files:
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        fi += 1
        close = df["收盘"].to_numpy(float)
        n = len(close)
        dates_i = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        keys = np.array([mpos[d] for d in dates_i], dtype=np.int64)
        rng = np.random.default_rng(seed + fi)
        rand_base = rng.random(n) < p
        for h in HORIZONS:
            fh = fwd_arr(close, h)
            valid = (~np.isnan(fh)) & rand_base
            sk = keys[valid]
            sf = fh[valid]
            st = is_test_master[sk]
            sig_sum["random"]["train"][h] += np.bincount(sk[~st], weights=sf[~st], minlength=M)
            sig_cnt["random"]["train"][h] += np.bincount(sk[~st], minlength=M)
            sig_sum["random"]["test"][h] += np.bincount(sk[st], weights=sf[st], minlength=M)
            sig_cnt["random"]["test"][h] += np.bincount(sk[st], minlength=M)
            tm = valid & is_test_master[keys]
            ev = fh[tm]
            if ev.size:
                events["random"][h].extend(ev.tolist())
    print(f"  [passB] random 完成，共 {fi} 只")
    return sig_sum, sig_cnt, events


def portfolio_metrics(sig_sum_split, sig_cnt_split, bench_sum_split, bench_cnt_split):
    """由逐日累加器算组合权益/PF/命中率/CAGR/Sharpe。"""
    M = len(bench_cnt_split)
    days = np.where(bench_cnt_split > 0)[0]
    if days.size == 0:
        return None
    bc = bench_cnt_split[days]
    bs = bench_sum_split[days]
    sc = sig_cnt_split[days]
    ss = sig_sum_split[days]
    bre = bs / bc
    sre = np.where(sc > 0, ss / sc, 0.0)      # 无信号日持币
    # ── 按日配对超额（doctrine: GOALS_AND_METRICS.md §3）──
    # days=np.where(bench_cnt>0)[0] 已按 master 交易日升序 → 该序列可承载 HAC。
    # (a) 主判据口径：全交易日连续序列（无信号日 sre=0 即持币，日间隔连续）。
    paired_excess = sre - bre
    # (b) 次口径（仅诊断，绝不进 adjudicate）：仅信号日。此序列打断日间隔，
    #     固定 lag HAC 在此仅供参考，不能作为部署依据（见 t_stat_signal_days_only）。
    sig_mask = sc > 0
    paired_excess_signal_days = paired_excess[sig_mask]
    n_signal_days = int(sig_mask.sum())
    signal_day_coverage = n_signal_days / max(1, days.size)
    eq_b = np.cumprod(1.0 + bre)
    eq_s = np.cumprod(1.0 + sre)
    pos_b = bre[bre > 0]; neg_b = bre[bre < 0]
    pos_s = sre[sre > 0]; neg_s = sre[sre < 0]
    pf_b = (pos_b.sum() / abs(neg_b.sum())) if neg_b.size and abs(neg_b.sum()) > 0 else float("inf")
    pf_s = (pos_s.sum() / abs(neg_s.sum())) if neg_s.size and abs(neg_s.sum()) > 0 else float("inf")
    hit_b = float((bre > 0).mean())
    hit_s = float((sre > 0).mean())
    N = days.size
    cagr_b = eq_b[-1] ** (252.0 / N) - 1.0 if eq_b[-1] > 0 else float("nan")
    cagr_s = eq_s[-1] ** (252.0 / N) - 1.0 if eq_s[-1] > 0 else float("nan")
    sd = sre.std()
    sharpe_s = (sre.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0
    return dict(eq_b=float(eq_b[-1]), eq_s=float(eq_s[-1]), pf_b=pf_b, pf_s=pf_s,
                hit_b=hit_b, hit_s=hit_s, cagr_b=cagr_b, cagr_s=cagr_s,
                sharpe_s=sharpe_s, n_days=int(N),
                paired_excess=paired_excess,
                paired_excess_signal_days=paired_excess_signal_days,
                n_signal_days=n_signal_days,
                signal_day_coverage=signal_day_coverage)


def purged_cv_stats(pe, label_span, n_splits=PURGED_CV_N_SPLITS, embargo=None):
    """Purged K-Fold 时间稳定性检验（López de Prado 事件窗 embargo）。

    输入：pe = 时序日配对超额收益（已按交易日升序）；label_span = 持有期 H（标签窗口长）。
    做法：将时序分 n_splits 个连续时间折；每折作测试集，训练集剔除"标签窗口 [i, i+H-1]
          与测试区间重叠"的样本并 embargo 测试后 H-1 日，杜绝 H 重叠标签泄漏。
    返回：各折均值 / NW-HAC t、正向折占比(pos_frac)、t≥2 折占比(frac_pos_t)。
    含义：pos_frac 低（<PURGED_CV_POS_FRAC_MIN）= edge 集中于少数时段，具数据窥探嫌疑；
          NW HAC 已修正 SE，但无法识别"时序不稳"，此即 Purged-CV 的独特贡献。
    """
    pe = np.asarray(pe, float)
    pe = pe[np.isfinite(pe)]
    if pe.size < n_splits * 5:
        return {"pos_frac": float("nan"), "frac_pos_t": float("nan"),
                "fold_means": [], "fold_ts": [], "n": int(pe.size)}
    if embargo is None:
        embargo = label_span - 1
    t1 = np.arange(pe.size) + (label_span - 1)   # 第 i 日标签窗口末端 = i + (H-1)
    fold_means, fold_ts = [], []
    for tr, te in purged_kfold_indices(pe.size, n_splits, embargo, t1):
        if len(te) == 0:
            continue
        sub = pe[te]
        if sub.size == 0:
            continue
        fold_means.append(float(sub.mean()))
        tt, _ = nw_tstat(sub, lag=label_span - 1)
        fold_ts.append(float(tt) if math.isfinite(tt) else float("nan"))
    fold_means = np.asarray(fold_means, float)
    fold_ts = np.asarray(fold_ts, float)
    pos_frac = float((fold_means > 0).mean()) if fold_means.size else float("nan")
    frac_pos_t = float((fold_ts >= 2.0).mean()) if fold_ts.size else float("nan")
    return {"pos_frac": round(pos_frac, 4), "frac_pos_t": round(frac_pos_t, 4),
            "fold_means": [round(x, 5) for x in fold_means.tolist()],
            "fold_ts": [round(x, 3) for x in fold_ts.tolist()],
            "n": int(pe.size)}


def adjudicate(res):
    """铁律裁定（门 B 完整化）。res = 某因子 H5 的指标字典。

    既有门槛（不得改数值）：n>=200 / 命中倍数>=1.15 / PF>=1.30 /
    均值间隙>20bp(成本后口径) / t>=2.0。
    新增可选加严（缺省不强制，以保向后兼容）：
      - DSR>=0.95：deflated Sharpe，按 N_TRIALS_SEARCH 多重检验校正；
      - WhiteRC p<0.10：跨因子全局数据窥探护栏（由 run_all 注入同一 p 值）；
      - 成本后均值间隙：mean_sig_test_net 优先于 mean_sig_test 做 20bp 间隙判定。
    """
    n = res.get("n_events_test", 0)
    if n < 200:
        return "REJECT(样本不足)", "n_test<200"
    hm = res["hit_multiple"]
    pf = res["pf_sig_test"]
    ms = res["mean_sig_test"]
    mb = res["mean_base_test"]
    # 成本后：优先用 net（已扣 COST_RT）；缺省用 gross（向后兼容既有产物）
    ms_net = res.get("mean_sig_test_net", None)
    gap_ms = ms_net if ms_net is not None else ms
    t = res["t_stat"]
    # 保护（不改任何门槛数值）：t=None / nan / inf / 非数值一律视为「不满足 t>=2.0」→ REJECT，
    # 且绝不抛 TypeError；用 -inf 保证其进入下方 reasons 明细。
    try:
        t = float(t)
    except (TypeError, ValueError):
        t = float("-inf")
    if not math.isfinite(t):
        t = float("-inf")
    dsr = res.get("dsr", None)
    wrp = res.get("white_rc_p", None)
    ok = (hm >= 1.15) and (pf >= 1.30) and (gap_ms > mb + 0.20) and (t >= 2.0)
    reasons = []
    if hm < 1.15: reasons.append(f"命中倍数{hm:.3f}<1.15")
    if pf < 1.30: reasons.append(f"PF{pf:.3f}<1.30")
    if not (gap_ms > mb + 0.20): reasons.append(f"均值间隙{(gap_ms-mb):.3f}≤20bp")
    if t < 2.0: reasons.append(f"t={t:.2f}<2.0")
    if dsr is not None and dsr < DSR_MIN:
        ok = False; reasons.append(f"DSR{dsr:.3f}<{DSR_MIN}(多重检验未过)")
    if wrp is not None and wrp >= WHITE_RC_P_MAX:
        ok = False; reasons.append(f"WhiteRC p={wrp:.3f}>={WHITE_RC_P_MAX}(数据窥探未过)")
    pcv = res.get("purged_cv_pos_frac", None)
    if pcv is not None and math.isfinite(pcv) and pcv < PURGED_CV_POS_FRAC_MIN:
        ok = False; reasons.append(f"PurgedCV正向折占比{pcv:.2f}<{PURGED_CV_POS_FRAC_MIN}(时序不稳/窥探嫌疑)")
    if ok:
        return "PROMOTE", "命中倍数≥1.15 且 PF≥1.30 且 均值间隙>20bp(成本后) 且 t≥2.0" + \
               (f" 且 DSR≥{DSR_MIN}" if dsr is not None else "") + \
               (f" 且 WhiteRC p<{WHITE_RC_P_MAX}" if wrp is not None else "") + \
               (f" 且 PurgedCV≥{PURGED_CV_POS_FRAC_MIN}" if pcv is not None else "")
    return "REJECT", ";".join(reasons)


def summarize_factor(fname, sig_sum, sig_cnt, bench_sum, bench_cnt, events):
    out = {"factor": fname, "horizons": {}}
    for h in HORIZONS:
        mt = portfolio_metrics(sig_sum[fname]["test"][h], sig_cnt[fname]["test"][h],
                               bench_sum["test"][h], bench_cnt["test"][h])
        mr = portfolio_metrics(sig_sum[fname]["train"][h], sig_cnt[fname]["train"][h],
                               bench_sum["train"][h], bench_cnt["train"][h])
        # 描述统计（per-signal 池化收益）：保留原值/原语义，但不再喂 t。
        ev_test = np.array(events[fname][h], dtype=float)
        ev_train = None  # train 事件未单独存（用组合均值代理）
        n_test = int(ev_test.size)
        mean_sig = float(ev_test.mean()) if n_test else float("nan")
        std_sig = float(ev_test.std()) if n_test else float("nan")

        # ── t 估计量修复：日配对超额序列 (a) 上的 NW HAC t（lag=H-1=4）──
        # 不再用 per-signal 池化 iid `mean/(std/sqrt(n))`：H=5 重叠标签使 iid SE
        # 系统性低估、放大 t，可把无 edge 因子误判过 t>=2.0 门槛。
        pe = np.asarray(mt["paired_excess"], float)          # 全交易日连续序列
        t_stat, se_hac = nw_tstat(pe, lag=PRIMARY_H - 1)
        pe_fin = pe[np.isfinite(pe)]
        mean_pe = float(pe_fin.mean()) if pe_fin.size else float("nan")
        if np.isfinite(se_hac) and np.isfinite(mean_pe):
            ci = [mean_pe - 1.96 * se_hac, mean_pe + 1.96 * se_hac]
        else:
            ci = [float("nan"), float("nan")]

        # (b) 次口径（仅诊断，绝不进 adjudicate）：仅信号日，打断日间隔，
        #     固定 lag HAC 在此仅供参考。
        pe_sig = np.asarray(mt["paired_excess_signal_days"], float)
        t_sig_only, _ = nw_tstat(pe_sig, lag=PRIMARY_H - 1)
        # 基准均值（TEST 池化）= bench_sum/bench_cnt 全量均值
        bdays = np.where(bench_cnt["test"][h] > 0)[0]
        base_mean = float((bench_sum["test"][h][bdays] / bench_cnt["test"][h][bdays]).mean()) if bdays.size else float("nan")
        hit_mult = (mt["hit_s"] / mt["hit_b"]) if mt["hit_b"] > 0 else float("nan")
        pf_ratio = (mt["pf_s"] / mt["pf_b"]) if mt["pf_b"] and mt["pf_b"] != float("inf") else float("nan")
        out["horizons"][h] = dict(
            n_events_test=n_test,
            mean_sig_test=round(mean_sig * 100, 4),
            mean_base_test=round(base_mean * 100, 4),
            std_sig=round(std_sig * 100, 4),
            # t_stat 现为「日配对超额序列 (a) 的 NW HAC t」；保持 float，nan 留 float('nan') 不转 None。
            t_stat=(round(t_stat, 3) if math.isfinite(t_stat) else float("nan")),
            ci95_lo=round(ci[0] * 100, 4), ci95_hi=round(ci[1] * 100, 4),
            hit_sig_test=round(mt["hit_s"], 4), hit_base_test=round(mt["hit_b"], 4),
            hit_multiple=round(hit_mult, 4) if hit_mult == hit_mult else None,
            pf_sig_test=round(mt["pf_s"], 4), pf_base_test=round(mt["pf_b"], 4),
            pf_ratio=round(pf_ratio, 4) if pf_ratio == pf_ratio else None,
            cagr_sig_test=round(mt["cagr_s"] * 100, 3), cagr_base_test=round(mt["cagr_b"] * 100, 3),
            sharpe_sig_test=round(mt["sharpe_s"], 3),
            eq_sig_test=round(mt["eq_s"], 4), eq_base_test=round(mt["eq_b"], 4),
            n_days_test=mt["n_days"],
            # ── 新增：NW HAC 诊断字段（(a) 主判据 / (b) 仅诊断）──
            mean_paired_excess_pct=(round(mean_pe * 100, 4) if math.isfinite(mean_pe) else float("nan")),
            se_hac=(round(se_hac, 8) if math.isfinite(se_hac) else float("nan")),
            t_stat_method="nw_hac_bartlett",
            nw_lag=PRIMARY_H - 1,
            n_days_paired=mt["n_days"],
            t_stat_signal_days_only=(round(t_sig_only, 3) if math.isfinite(t_sig_only) else float("nan")),
            signal_day_coverage=round(mt["signal_day_coverage"], 4),
            n_signal_days=mt["n_signal_days"],
            # ── 门 B 完整化：成本后（往返成本代理）+ DSR（多重检验）──
            mean_sig_test_net=(round((mean_sig - COST_RT) * 100, 4) if math.isfinite(mean_sig) else float("nan")),
            dsr=(round(deflated_sharpe_ratio(
                     (mt["sharpe_s"] / math.sqrt(252.0)) if math.isfinite(mt["sharpe_s"]) else 0.0,
                     n_trials=N_TRIALS_SEARCH, n_obs=max(int(mt["n_days"]), 2)), 4)
                 if math.isfinite(mt["sharpe_s"]) else float("nan")),
            cost_rt=COST_RT,
        )
        if h == PRIMARY_H:
            # Purged-CV（事件窗 embargo 时序稳定性）：对 H5 日配对超额做 embargo 时间折切分，
            # 验证 edge 在多数时间折正向（>PURGED_CV_POS_FRAC_MIN），防 H 重叠标签泄漏 + 时段窥探。
            _pcv = purged_cv_stats(pe, PRIMARY_H, PURGED_CV_N_SPLITS, PURGED_CV_EMBARGO)
            out["horizons"][h]["purged_cv_pos_frac"] = _pcv["pos_frac"]
            out["horizons"][h]["purged_cv_frac_pos_t"] = _pcv["frac_pos_t"]
            out["horizons"][h]["purged_cv_fold_means"] = _pcv["fold_means"]
            out["horizons"][h]["purged_cv_fold_ts"] = _pcv["fold_ts"]
            out["horizons"][h]["purged_cv_n"] = _pcv["n"]
            # 仅供 run_all 汇总 WhiteRC 使用；落盘前由 run_all 用 pop 剔除，不进产物 schema
            out["horizons"][h]["_pe_h5"] = pe.tolist()
        # train 漂移
        if mr:
            bdays_tr = np.where(bench_cnt["train"][h] > 0)[0]
            base_mean_tr = float((bench_sum["train"][h][bdays_tr] / bench_cnt["train"][h][bdays_tr]).mean()) if bdays_tr.size else float("nan")
            out["horizons"][h]["mean_sig_train"] = None  # 未存 train 事件池
            out["horizons"][h]["mean_base_train"] = round(base_mean_tr * 100, 4)
            out["horizons"][h]["cagr_sig_train"] = round(mr["cagr_s"] * 100, 3)
    # 主 horizon 裁定
    h5 = out["horizons"][PRIMARY_H]
    verdict, reason = adjudicate(h5)
    out["verdict_H5"] = verdict
    out["verdict_reason_H5"] = reason
    return out


def run_all():
    t0 = time.time()
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    print(f"[init] {len(files)} 只日线；切分 TRAIN≤{TRAIN_END} / TEST≥{TEST_START}")

    # Pass A：先扫一遍收集日期建立 master，再用同一遍算真实因子
    # 为减少一遍 IO，Pass A 同时收集日期集合与累加（日期集合用全局 set）
    date_set = set()
    # 第一小趟：仅日期（快）
    for f in files:
        try:
            d = pd.read_csv(f, usecols=["日期"])["日期"].astype(str).str.replace("-", "").astype(int)
            date_set.update(d.tolist())
        except Exception:
            continue
    master, mpos = build_master(date_set)
    print(f"[master] {len(master)} 个交易日；TEST 区间 {master[-1] if False else TEST_START}…")
    is_test_master = np.array([d >= TEST_START_I for d in master], dtype=bool)

    bench_sum, bench_cnt, sig_sum, sig_cnt, events, N_valid_test, N_events_test = \
        accumulate_passA(files, mpos, is_test_master)
    print(f"[passA] 完成。N_valid_test={N_valid_test}；各因子TEST事件数={N_events_test}")

    # 随机空对照频率 p（聚合事件率）
    tot_ev = sum(N_events_test.values())
    p = tot_ev / N_valid_test if N_valid_test else 0.0
    print(f"[null] p_random={p:.5f}（聚合事件率）")
    r_sum, r_cnt, r_ev = accumulate_random(files, mpos, is_test_master, p)
    # 合并 random 进 sig_sum
    sig_sum["random"] = r_sum["random"]
    sig_cnt["random"] = r_cnt["random"]
    events["random"] = r_ev["random"]

    # 汇总 + 裁定
    report = {"generated": time.strftime("%Y-%m-%d %H:%M"),
              "split": dict(train_end=TRAIN_END, test_start=TEST_START, n_trading_days=len(master)),
              "N_valid_test_days_stock": N_valid_test,
              "null_p": p,
              "factors": {}}
    for fname in ["R31_breakout", "ylim", "R32_d2", "random", "oversold_sig", "oversold_shallow", "oversold_deep"]:
        report["factors"][fname] = summarize_factor(fname, sig_sum, sig_cnt, bench_sum, bench_cnt, events)

    # ── 门 B 完整化：WhiteRC（全局数据窥探护栏）跨 7 因子 H5 日配对超额序列 block bootstrap ──
    _fac = ["R31_breakout", "ylim", "R32_d2", "random", "oversold_sig", "oversold_shallow", "oversold_deep"]
    _pe_cols = []
    for _fn in _fac:
        _pe = report["factors"][_fn]["horizons"][PRIMARY_H].pop("_pe_h5", None)
        if _pe is not None:
            _pe_cols.append(np.asarray(_pe, dtype=float))
    white_rc_p = float("nan")
    if _pe_cols:
        _R = np.column_stack(_pe_cols)
        _rc = white_reality_check(_R, benchmark=0.0, n_boot=WHITE_RC_N_BOOT, block=5, seed=20261107)
        white_rc_p = round(float(_rc["p_value"]), 4)
    report["white_rc_p"] = white_rc_p
    report["white_rc_n_boot"] = WHITE_RC_N_BOOT
    for _fn in _fac:
        report["factors"][_fn]["horizons"][PRIMARY_H]["white_rc_p"] = white_rc_p

    # WhiteRC 为全局护栏，须重算裁定：adjudicate 在 summarize_factor 内首次运行时
    # white_rc_p 尚未注入，故此处用已注入 white_rc_p 的 H5 重算 verdict/verdict_reason，
    # 确保裁定理由反映 WhiteRC 加严项（wrp>=WHITE_RC_P_MAX 即 REJECT）。
    for _fn in _fac:
        _h5 = report["factors"][_fn]["horizons"][PRIMARY_H]
        _v, _rs = adjudicate(_h5)
        report["factors"][_fn]["verdict_H5"] = _v
        report["factors"][_fn]["verdict_reason_H5"] = _rs

    (CACHE / "oospf_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台
    print("\n=== OOS Top-K / 组合 PF 验证结果（TEST ≥ %s）===" % TEST_START)
    hdr = f"{'因子':14s} {'H':>2s} {'n':>7s} {'信号均值%':>9s} {'基准均值%':>9s} {'命中倍数':>8s} {'PF':>7s} {'PF比':>6s} {'Sharpe':>7s} {'裁定':>10s}"
    print(hdr)
    for fname in ["R31_breakout", "ylim", "R32_d2", "random", "oversold_sig", "oversold_shallow", "oversold_deep"]:
        for h in HORIZONS:
            d = report["factors"][fname]["horizons"][h]
            print(f"{fname:14s} {h:>2d} {d['n_events_test']:>7d} {d['mean_sig_test']:>9.3f} {d['mean_base_test']:>9.3f} "
                  f"{(d['hit_multiple'] or 0):>8.3f} {d['pf_sig_test']:>7.3f} {(d['pf_ratio'] or 0):>6.3f} {d['sharpe_sig_test']:>7.3f} "
                  f"{report['factors'][fname]['verdict_H5'] if h==PRIMARY_H else ''}")
    print(f"\n[done] t={time.time()-t0:.0f}s -> backtest_cache/oospf_report.json")
    return report


if __name__ == "__main__":
    run_all()
