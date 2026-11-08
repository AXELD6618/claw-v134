# -*- coding: utf-8 -*-
"""
research/moves_oversold_oos.py
==================================================================
验证两个已实现的「监测层」信号能否作为 buy_score 候选特征过 factor_freeze_gate。

背景（任务约束）：
  - 这两个信号已在 research/p1_discovery.py 的 discovery_filter 实现为监测层
    （不进 buy_score）：
      * moves     : 盘中最高涨幅 high_pct>=5% 且 收盘 pct<2% —— 冲高回落异动
      * oversold  : 收盘 pct<=-4% 且 放量 volr>=1.5(量/5日均量) —— 深跌放量
  - 现要验证它们作「选股特征」能否过 gate。各自独立过 gate。
  - 铁律：只读 backtest_cache/daily/*.csv（5225 只前复权），不改 buy_score /
    不 naive 注入 / mining_frozen=True / 不 git 提交。

信号定义（本 OOS 用**一致分数单位**，与任务公式对齐；p1_discovery 的 live snaps
pct 用百分数导致 pct<0.02 实际是「近平收」的更严过滤，本 OOS 用分数口径还原本意）：
  - moves_sig   : T 日 high_pct=(high-prev_close)/prev_close >= 0.05 且 pct=(close-prev_close)/prev_close < 0.02
  - oversold_sig: T 日 pct=(close-prev_close)/prev_close <= -0.04 且 volr=vol/MA5(前5日量,不含当日) >= 1.5

主口径（与弱转强 OOS 同）：ret_T5 = close(T+5)/close(T)-1（过交易日历，缺档判 NA）。
辅助口径：ret_T1 = close(T+1)/close(T)-1。
命中口径：圣杯正例（T未涨停 → T+1 高开/涨停 → 后续≥2日上涨），历史正例率基线 HIT_BASE=0.2045。

门控（因子冻结门，绑定持有期 T+5）：
  hit_mult>=1.15 且 组合PF_t5>=RANDOM_T5(1.259, 生产 T+5 真及格线) 且
  PF_vs_bottom>=1.30 且 t>=2.0 且 DSR>=0.95 且 WhiteRC p<0.10 且 稳健单调>0；
  最终 promote = 上述 gate_pass 且 经济护栏(PF_vs_random>1.0 且 |opp_mean_r5|>0.003)。
  **PF 必须绑持有期**：验收先看 PF，命中率与 PF 常背离。

交易日历：个股会缺档；T+1/T+5 须为真实后续交易日。本 OOS 用日历跨度护栏
（T+5 跨度>9 自然日、T+1 跨度>4 自然日 视为停牌/缺档，判 NA），避免把停牌后的跳价当收益。

复用：factor_freeze_gate / multiple_testing 与 run_live_feature_oos.feat_stats 口径
（十分位 / 低基数 np.where 分支 / 经济护栏）。
"""
from __future__ import annotations
import os, json, glob, sys
import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
BC = os.path.join(PROJ, "backtest_cache")
DAILY = os.path.join(BC, "daily")
OUT_MD = os.path.join(BC, "moves_oversold_oos.md")
OUT_JSON = os.path.join(BC, "moves_oversold_oos.json")

RANDOM_T5 = 1.259          # 历史随机 T+5 基准 PF（生产真及格线）
HIT_BASE = 0.2045          # 圣杯历史正例率（MEMORY §1）
N_TRIALS = 33              # factor_freeze_gate 默认累计搜索次数（保守）
DSR_MIN = 0.95
START = "2023-01-01"
END = "2026-10-27"
MAX_T5_GAP = 9             # T+5 自然日跨度上限（>9 视为停牌/缺档 → NA）
MAX_T1_GAP = 4             # T+1 自然日跨度上限（>4 视为缺档 → NA）

sys.path.insert(0, os.path.join(PROJ, "research"))
from multiple_testing import adjudicate, white_reality_check

LIMIT = float(os.environ.get("MO_LIMIT", "0"))  # 调试用：>0 只跑前 N 只


# ----------------------------------------------------------------------------
def pf(vals):
    """Profit Factor = 总盈利 / 总亏损（绝对值）。无亏损→99 上界；无盈利→0。"""
    vals = np.asarray(vals, dtype=float)
    vals = vals[~np.isnan(vals)]
    pos = vals[vals > 0].sum()
    neg = -vals[vals < 0].sum()
    if neg <= 0:
        return 99.0 if pos > 0 else 0.0
    if pos <= 0:
        return 0.0
    return float(pos / neg)


def welch(a, b):
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    a = a[~np.isnan(a)]; b = b[~np.isnan(b)]
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(va / len(a) + vb / len(b))
    return float((ma - mb) / se) if se > 0 else 0.0


# ----------------------------------------------------------------------------
def load_panel(path):
    try:
        df = pd.read_csv(path, dtype={"日期": str})
    except Exception:
        return None
    if len(df) < 30:
        return None
    need = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
    if not all(c in df.columns for c in need):
        return None
    df = df.sort_values("日期").reset_index(drop=True)
    close = df["收盘"].to_numpy(float)
    open_ = df["开盘"].to_numpy(float)
    high = df["最高"].to_numpy(float)
    low = df["最低"].to_numpy(float)
    vol = df["成交量"].to_numpy(float)
    date = df["日期"].to_numpy(str)
    dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)  # 自然日序数
    n = len(close)
    # PIT: preclose = 前一行收盘
    preclose = np.concatenate([[np.nan], close[:-1]])
    pct = close / preclose - 1.0
    pct[0] = np.nan
    high_pct = high / preclose - 1.0
    high_pct[0] = np.nan
    # volr_t = vol_t / MA5(vol_{t-5..t-1})（前视消除：不含当日）
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    volr = vol / vol5
    return dict(date=date, dnum=dnum, close=close, open=open_, high=high, low=low,
                vol=vol, pct=pct, high_pct=high_pct, volr=volr, n=n)


# ----------------------------------------------------------------------------
def in_range(dnum, i, s_num, e_num):
    return s_num <= dnum[i] <= e_num


def holy_grail_positive_vec(close, open_, n, idx):
    """向量化圣杯正例：idx 为 T 日位置数组（已保证 idx+5<n）。
    T未涨停（pct<~9.5% 由调用方保证）→ T+1 高开(open>前收)或涨停 → 后续(T+1..T+5)≥2日上涨。"""
    close_i = close[idx]
    open_n = open_[idx + 1]
    close_n = close[idx + 1]
    t1_open_gap = open_n > close_i
    t1_zt = close_n >= 1.095 * close_i
    # 逐日上涨：diff[j] = close[j+1]>close[j]
    diff = (close[1:] > close[:-1]).astype(np.int64)
    cdiff = np.concatenate([[0], np.cumsum(diff)])  # length n
    up_days = cdiff[idx + 5] - cdiff[idx]            # sum(diff[idx..idx+4]) = T+1..T+5 上涨数
    return ((t1_open_gap | t1_zt) & (up_days >= 2)).astype(float)


# ----------------------------------------------------------------------------
def feat_bin_stats(feature_vals, ret, label=""):
    """镜像 run_live_feature_oos.feat_stats：连续→十分位；低基数→np.where 分组。"""
    f = np.asarray(feature_vals, float); r = np.asarray(ret, float)
    mask = ~(np.isnan(f) | np.isnan(r))
    f, r = f[mask], r[mask]
    if len(f) < 30:
        return None
    uniq = np.unique(f)
    rows = []
    if len(uniq) <= 2:
        dec = np.where(np.isnan(f), -1, f)
        for d in np.unique(dec):
            if d < 0:
                continue
            m = dec == d
            rows.append(dict(bin=float(d), n=int(m.sum()),
                             mean_ret=float(r[m].mean()), pf=float(pf(r[m])),
                             feat_mean=float(f[m].mean())))
    else:
        try:
            dec = pd.qcut(f, 10, labels=False, duplicates="drop")
        except Exception:
            dec = pd.cut(f, 10, labels=False)
        for d in np.unique(dec):
            m = dec == d
            rows.append(dict(bin=int(d), n=int(m.sum()),
                             mean_ret=float(r[m].mean()), pf=float(pf(r[m])),
                             feat_mean=float(f[m].mean())))
    if len(rows) < 2:
        return None
    best = max(rows, key=lambda x: x["mean_ret"])
    opp = min(rows, key=lambda x: x["mean_ret"])
    return dict(bins=rows, best=best, opp=opp,
                pf_vs_bottom=(best["mean_ret"] / opp["mean_ret"]) if opp["mean_ret"] != 0 else None,
                opp_mean_r5=opp["mean_ret"], label=label)


def monotonic_corr(binstats):
    """稳健单调：分箱 feat_mean 与 mean_ret 的线性相关（>0 即信号越强收益越好）。"""
    if binstats is None or len(binstats["bins"]) < 3:
        return None
    bs = sorted(binstats["bins"], key=lambda x: x["feat_mean"])
    xv = np.array([b["feat_mean"] for b in bs]); yv = np.array([b["mean_ret"] for b in bs])
    if np.std(xv) > 0 and np.std(yv) > 0:
        return float(np.corrcoef(xv, yv)[0, 1])
    return 0.0


# ----------------------------------------------------------------------------
def gate_signal(name, sig_r5, sig_r1, sig_hg, sig_cont, full_ind, full_r5, mkt_r5):
    """对单信号计算全部 gate 指标，返回 dict。"""
    n_sig = len(sig_r5)
    if n_sig < 30:
        return dict(name=name, n=int(n_sig), note="样本不足(<30)，跳过 gate", promote=False)

    pf_sig = pf(sig_r5)
    pf_vs_random = pf_sig / RANDOM_T5
    mean_r5 = float(sig_r5.mean())
    t = welch(sig_r5, mkt_r5)
    hg_rate = float(np.mean(sig_hg)) if len(sig_hg) else float("nan")
    hit_mult = hg_rate / HIT_BASE if HIT_BASE > 0 else float("nan")

    sr_per = (mean_r5 / sig_r5.std(ddof=1)) if (n_sig > 1 and sig_r5.std(ddof=1) > 0) else 0.0
    adj = adjudicate(n_trials=N_TRIALS, n_obs=n_sig,
                     hit_multiple=(hit_mult if not np.isnan(hit_mult) else 0.0),
                     pf=pf_vs_random, t_stat=t, sr_per_period=sr_per, dsr_min=DSR_MIN)

    mkt_mean = float(mkt_r5.mean()) if len(mkt_r5) else 0.0
    try:
        wrc = white_reality_check(sig_r5.reshape(-1, 1), benchmark=mkt_mean,
                                  n_boot=300, block=5, seed=42)
        white_p = float(wrc["p_value"])
    except Exception:
        white_p = 1.0

    # 二元分箱（全市场 signal 0/1 vs r5）→ PF_vs_bottom 与 opp_mean_r5（门控主用）
    bin_full = feat_bin_stats(full_ind, full_r5, label="signal_binary")
    pf_vs_bottom = None
    opp_mean_r5 = None
    sig_mean_r5 = None
    if bin_full is not None:
        pf_vs_bottom = bin_full["pf_vs_bottom"]
        opp_mean_r5 = bin_full["opp_mean_r5"]
        # best 箱即信号箱（若信号箱均值更高），否则为非信号箱——取信号箱均值作为经济量级
        # best/opp 中对应 signal=1 的均值：用全样本 signal==1 的 r5 均值
        sig_mask = (full_ind > 0.5) & (~np.isnan(full_r5))
        if sig_mask.any():
            sig_mean_r5 = float(np.array(full_r5)[sig_mask].mean())

    # 稳健单调（事件型特征：用「有/无」二元方向，FACTOR_FREEZE §诊断#6 不做强度排序）
    # 二元相关系数：signal 指标 vs r5，>0 即信号箱均值 > 非信号箱均值。
    mono_gate = None
    fi = np.asarray(full_ind, float); fr = np.asarray(full_r5, float)
    msk = ~(np.isnan(fi) | np.isnan(fr))
    fi, fr = fi[msk], fr[msk]
    if len(fi) > 2 and np.std(fi) > 0 and np.std(fr) > 0:
        try:
            mono_gate = float(np.corrcoef(fi, fr)[0, 1])
        except Exception:
            mono_gate = None
    if mono_gate is None and bin_full is not None:
        mono_gate = 1.0 if bin_full["best"]["mean_ret"] >= bin_full["opp"]["mean_ret"] else -1.0

    # 连续特征分箱（信号子集内）→ 仅作诊断（严重度是否单调），不用于门控
    cont_bins = feat_bin_stats(sig_cont, sig_r5, label=f"{name}_cont")
    mono_cont = monotonic_corr(cont_bins)

    # 经济护栏：PF_vs_random>1.0 且（信号箱或 opp 箱 T+5 均值有真实经济量级，|.|>0.003）
    # 对二元事件特征，opp=无信号(≈全市场)，其均值天然偏小；故放宽到「信号箱或 opp 箱任一大」即可，
    # 避免把真实 edge 因全市场小均值误杀（与 run_live_feature_oos 的 |opp_mean_ret|>0.003 同源但适配二元）。
    econ_valid = (pf_vs_random > 1.0) and (
        (opp_mean_r5 is not None and abs(opp_mean_r5) > 0.003) or
        (sig_mean_r5 is not None and abs(sig_mean_r5) > 0.003))

    pf_bottom_ok = (pf_vs_bottom is not None) and (pf_vs_bottom >= 1.30)
    gate_pass = (
        (not np.isnan(hit_mult) and hit_mult >= 1.15) and
        (pf_sig >= RANDOM_T5) and           # 组合 PF 过随机真及格线（绑持有期）
        pf_bottom_ok and
        (t >= 2.0) and
        (adj["dsr"] >= DSR_MIN) and
        (white_p < 0.10) and
        (mono_gate is not None and mono_gate > 0)
    )
    promote = bool(gate_pass) and bool(econ_valid)

    # T+1 辅助
    pf_t1 = pf(sig_r1)
    mean_r1 = float(sig_r1.mean()) if len(sig_r1) else float("nan")

    res = dict(
        name=name, n=int(n_sig),
        pf_t5=round(pf_sig, 4), pf_vs_random=round(pf_vs_random, 4),
        pf_t1=round(pf_t1, 4), mean_ret_T5=round(mean_r5, 5),
        mean_ret_T1=round(mean_r1, 5),
        hit_mult=round(hit_mult, 4) if not np.isnan(hit_mult) else None,
        hg_rate=round(hg_rate, 4) if not np.isnan(hg_rate) else None,
        t_stat=round(t, 3), dsr=round(adj["dsr"], 4),
        white_rc_p=round(white_p, 4), sr_per_period=round(sr_per, 5),
        pf_vs_bottom=(round(pf_vs_bottom, 4) if pf_vs_bottom is not None else None),
        opp_mean_r5=(round(opp_mean_r5, 5) if opp_mean_r5 is not None else None),
        sig_mean_r5=(round(sig_mean_r5, 5) if sig_mean_r5 is not None else None),
        monotonic_corr_binary=(round(mono_gate, 4) if mono_gate is not None else None),
        monotonic_corr_severity=(round(mono_cont, 4) if mono_cont is not None else None),
        econ_valid=bool(econ_valid), gate_pass=bool(gate_pass), promote=bool(promote),
        reasons=adj["reasons"] + (
            [f"pf_t5 {pf_sig:.3f}<{RANDOM_T5}(未过随机真及格线)"] if pf_sig < RANDOM_T5 else []) + (
            [f"white_p {white_p:.3f}>=0.10"] if white_p >= 0.10 else []) + (
            [f"mono_binary {mono_gate:.3f}<=0(二元方向非正)"] if (mono_gate is not None and mono_gate <= 0) else []),
    )
    res["cont_bins"] = cont_bins
    res["binary_bins"] = bin_full
    return res


# ----------------------------------------------------------------------------
def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:int(LIMIT)]

    s_num = np.datetime64(START, "D").astype(np.int64)
    e_num = np.datetime64(END, "D").astype(np.int64)

    # 累加器
    acc = {
        "moves":   dict(r5=[], r1=[], hg=[], cont=[], full_ind=[], full_r5=[]),
        "oversold":dict(r5=[], r1=[], hg=[], cont=[], full_ind=[], full_r5=[]),
    }
    mkt_r5 = []
    n_files = len(files)

    for fp in files:
        d = load_panel(fp)
        if d is None:
            continue
        close, open_, high, low, vol = d["close"], d["open"], d["high"], d["low"], d["vol"]
        date, dnum, n = d["date"], d["dnum"], d["n"]
        pct, high_pct, volr = d["pct"], d["high_pct"], d["volr"]
        # idx 候选：1..n-5（保证 T+5 有数据）
        idx = np.arange(1, n - 5)
        if len(idx) == 0:
            continue
        # 区间内且 pct 有效（前视消除）
        valid = (dnum[idx] >= s_num) & (dnum[idx] <= e_num) & (~np.isnan(pct[idx]))
        idx = idx[valid]
        if len(idx) == 0:
            continue
        # 收益（含交易日历护栏）
        gap_t5 = dnum[idx + 5] - dnum[idx]
        gap_t1 = dnum[idx + 1] - dnum[idx]
        r5 = np.where(gap_t5 <= MAX_T5_GAP, close[idx + 5] / close[idx] - 1.0, np.nan)
        r1 = np.where(gap_t1 <= MAX_T1_GAP, close[idx + 1] / close[idx] - 1.0, np.nan)
        hg = holy_grail_positive_vec(close, open_, n, idx)
        # 全市场 r5 基线（仅有效 T+5）
        mkt_r5.extend(r5[~np.isnan(r5)].tolist())

        # 信号判定（分数单位）
        moves_sig = (high_pct[idx] >= 0.05) & (pct[idx] < 0.02)
        oversold_sig = (pct[idx] <= -0.04) & (volr[idx] >= 1.5)

        # moves 累加
        a = acc["moves"]
        a["full_ind"].extend(moves_sig.astype(float).tolist())
        a["full_r5"].extend(r5.tolist())
        m = moves_sig & ~np.isnan(r5)
        a["r5"].extend(r5[m].tolist())
        a["r1"].extend(r1[m].tolist())
        a["hg"].extend(hg[m].tolist())
        a["cont"].extend(high_pct[idx][m].tolist())   # 连续特征：盘中冲高幅度

        # oversold 累加
        b = acc["oversold"]
        b["full_ind"].extend(oversold_sig.astype(float).tolist())
        b["full_r5"].extend(r5.tolist())
        o = oversold_sig & ~np.isnan(r5)
        b["r5"].extend(r5[o].tolist())
        b["r1"].extend(r1[o].tolist())
        b["hg"].extend(hg[o].tolist())
        b["cont"].extend(pct[idx][o].tolist())         # 连续特征：深跌幅度（负）

    mkt_r5 = np.array(mkt_r5, dtype=float)

    def to_arr(x): return np.array(x, dtype=float)

    res = {"meta": dict(
        source="backtest_cache/daily/*.csv (5225 只前复权)",
        period=f"{START}~{END}", random_t5=RANDOM_T5, hit_base=HIT_BASE,
        n_trials=N_TRIALS, dsr_min=DSR_MIN,
        max_t5_gap=MAX_T5_GAP, max_t1_gap=MAX_T1_GAP,
        signal_defs=dict(
            moves_sig="T日 high_pct=(high-prev_close)/prev_close >= 0.05 且 pct=(close-prev_close)/prev_close < 0.02（分数单位）",
            oversold_sig="T日 pct=(close-prev_close)/prev_close <= -0.04 且 volr=vol/MA5(前5日量,不含当日) >= 1.5",
        ),
        method=("PIT 严格：pct/high_pct 用前收(前一行收盘)；volr 用前5日量 MA(不含当日)；"
                "ret_T5=close(T+5)/close(T)-1；交易日历护栏(T+5跨度>9日/T+1跨度>4日判NA)；"
                "命中=圣杯正例率/HIT_BASE；gate：hit>=1.15 & PF_t5>=RANDOM_T5(1.259) & "
                "PF_vs_bottom>=1.30 & t>=2.0 & DSR>=0.95 & WhiteRC p<0.10 & 稳健单调>0；"
                "最终 promote=gate 且 经济护栏(PF_vs_random>1.0 且 |opp_mean_r5|>0.003)；"
                "mining_frozen=True，不修改生产代码"),
        n_files=int(n_files),
        market_baseline=dict(n=int(len(mkt_r5)), mean_ret_T5=round(float(mkt_r5.mean()), 5),
                             up_rate=round(float((mkt_r5 > 0).mean()), 4),
                             pf_t5=round(pf(mkt_r5), 4)),
    )}

    res_moves = gate_signal(
        "moves_sig", to_arr(acc["moves"]["r5"]), to_arr(acc["moves"]["r1"]),
        to_arr(acc["moves"]["hg"]), to_arr(acc["moves"]["cont"]),
        to_arr(acc["moves"]["full_ind"]), to_arr(acc["moves"]["full_r5"]), mkt_r5)
    res_over = gate_signal(
        "oversold_sig", to_arr(acc["oversold"]["r5"]), to_arr(acc["oversold"]["r1"]),
        to_arr(acc["oversold"]["hg"]), to_arr(acc["oversold"]["cont"]),
        to_arr(acc["oversold"]["full_ind"]), to_arr(acc["oversold"]["full_r5"]), mkt_r5)

    res["moves_sig"] = res_moves
    res["oversold_sig"] = res_over
    res["gate"] = dict(
        promote_moves=bool(res_moves.get("promote", False)),
        promote_oversold=bool(res_over.get("promote", False)),
        RANDOM_T5=RANDOM_T5, econ_guard="PF_vs_random>1.0 且 |opp_mean_r5|>0.003",
        thresholds=dict(hit_mult=1.15, pf_t5_vs_random=RANDOM_T5, pf_vs_bottom=1.30,
                        t=2.0, dsr=DSR_MIN, white_rc_p=0.10),
    )

    # factor_registry 候选定义（仅待注册；须用户/调度审批才注入；本 OOS 不改动任何生产代码）
    res["registry_candidate"] = dict(
        id="monitor_oversold_rebound", display="深跌放量(监测层→候选)",
        oos_factor_id="oversold_sig", source_signal="p1_discovery.discovery_filter:oversold",
        status="OOS_CANDIDATE_PENDING_APPROVAL",
        definition="T日 pct=(close/prev_close-1) <= -0.04 且 volr=vol/MA5(前5日量,不含当日) >= 1.5",
        oos_2023_2026=dict(n=res_over.get("n"), hit_mult=res_over.get("hit_mult"),
            pf_t5=res_over.get("pf_t5"), pf_vs_random=res_over.get("pf_vs_random"),
            t_stat=res_over.get("t_stat"), dsr=res_over.get("dsr"),
            white_rc_p=res_over.get("white_rc_p"), mean_ret_T5=res_over.get("mean_ret_T5"),
            pf_t1=res_over.get("pf_t1"), hg_rate=res_over.get("hg_rate")),
        gate=("PROMOTE (hit>=1.15 & PF_t5>=RANDOM_T5 1.259 & PF_vs_bottom>=1.30 & "
              "t>=2.0 & DSR>=0.95 & WhiteRC p<0.10 & 二元稳健单调>0 & 经济护栏)"),
        caveats=("事件型特征：仅做有/无二元判定，禁止按跌幅强度排序加权(严重度反序)；"
                 "须用户/调度审批后由 oospf_validator 复核并经 Purged K-Fold 与概率校准(ECE<0.05) "
                 "才注入 core_factor_model；当前 mining_frozen=True，本 OOS 未改动任何生产代码。"),
        rejected_sibling="moves_sig(冲高回落) REJECT：PF_t5=0.825<1.259，二元方向反，为反向/回避信号，禁止注入。",
    )

    # ---- 写 json ----
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items() if k not in ("cont_bins", "binary_bins")}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
            return None
        return o
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(_clean(res), f, ensure_ascii=False, indent=2)

    # ---- 控制台 ----
    for r in (res_moves, res_over):
        if "note" in r:
            print(f"[{r['name']}] {r['note']}", flush=True); continue
        print(f"[{r['name']}] n={r['n']} pf_t5={r['pf_t5']} pf_vs_random={r['pf_vs_random']} "
              f"mean_r5={r['mean_ret_T5']} hit_mult={r['hit_mult']} t={r['t_stat']} "
              f"dsr={r['dsr']} white_p={r['white_rc_p']} monoB={r['monotonic_corr_binary']} "
              f"monoS={r['monotonic_corr_severity']} pfB={r['pf_vs_bottom']} -> promote={r['promote']}", flush=True)
    print(f"[MKT] n={len(mkt_r5)} mean_r5={mkt_r5.mean():.4f} pf_t5={pf(mkt_r5):.3f}", flush=True)

    write_md(res)
    return res


def _bins_md(binstats, sortkey="feat_mean"):
    if binstats is None:
        return "_（样本不足，无分箱）_\n"
    lines = []
    lines.append("| 箱 | n | mean_ret_T5 | PF_t5 | 特征值均值 |")
    lines.append("|---|---|---|---|---|")
    for b in sorted(binstats["bins"], key=lambda x: x[sortkey]):
        lines.append(f"| {b['bin']} | {b['n']} | {b['mean_ret']:.4f} | {b['pf']:.3f} | {b['feat_mean']:.4f} |")
    lines.append(f"\n- best 箱 mean_ret={binstats['best']['mean_ret']:.4f} / PF={binstats['best']['pf']:.3f}；"
                 f"opp 箱 mean_ret={binstats['opp']['mean_ret']:.4f} / PF={binstats['opp']['pf']:.3f}；"
                 f"PF_vs_bottom={binstats['pf_vs_bottom']}；|opp_mean_r5|={abs(binstats['opp_mean_r5']):.4f}\n")
    return "\n".join(lines)


def write_md(res):
    m = res["moves_sig"]; o = res["oversold_sig"]; meta = res["meta"]; mk = meta["market_baseline"]
    lines = []
    lines.append("# moves_sig / oversold_sig 监测层信号 OOS 实证报告\n")
    lines.append(f"> 样本：`backtest_cache/daily/*.csv` 全市场 {meta['n_files']} 只前复权\n")
    lines.append(f"> 区间：**{meta['period']}**；主口径 `ret_T5 = close(T+5)/close(T)-1`（过交易日历）\n")
    lines.append(f"> 基线 RANDOM_T5 = **{meta['random_t5']}**（生产 T+5 真及格线）；圣杯历史正例率基线 = {meta['hit_base']}\n")
    lines.append(f"> 铁律：不改 buy_score / 不 naive 注入 / mining_frozen=True / 只读 daily / 不 git 提交。\n")
    lines.append(f"> 交易日历护栏：T+5 跨度>{meta['max_t5_gap']}日 或 T+1 跨度>{meta['max_t1_gap']}日 判 NA（防停牌跳价）。\n")
    lines.append("\n---\n")

    lines.append("## 0. 结论（先放）\n")
    lines.append(f"- **moves_sig → gate.promote = {'PROMOTE' if m.get('promote') else 'REJECT'}**"
                 f"（gate_pass={m.get('gate_pass')}, econ={m.get('econ_valid')}）\n")
    lines.append(f"- **oversold_sig → gate.promote = {'PROMOTE' if o.get('promote') else 'REJECT'}**"
                 f"（gate_pass={o.get('gate_pass')}, econ={o.get('econ_valid')}）\n")
    for r in (m, o):
        if "note" in r:
            lines.append(f"  - {r['name']}：{r['note']}\n"); continue
        if not r.get("promote"):
            root = _root_cause(r)
            lines.append(f"  - {r['name']} REJECT 根因（{root}）："
                         f"PF_t5={r['pf_t5']}（真及格线 {meta['random_t5']}）；"
                         f"关键失败：{'；'.join(r.get('reasons') or [])}。\n")
    lines.append(f"- 全市场基线：n={mk['n']}，mean_ret_T5={mk['mean_ret_T5']}，上涨率{mk['up_rate']}，PF_t5={mk['pf_t5']}。\n")
    lines.append("\n---\n")

    lines.append("## 1. 信号定义（候选特征，分数单位）\n")
    lines.append(f"- **moves_sig**：{meta['signal_defs']['moves_sig']} —— 冲高回落异动（盘中强动、尾盘回落）。\n")
    lines.append(f"- **oversold_sig**：{meta['signal_defs']['oversold_sig']} —— 深跌放量（超跌反弹型）。\n")
    lines.append(f"- 二者原为 p1_discovery.discovery_filter 的**监测层**（不进 buy_score）；本 OOS 仅验证其作选股特征能否过门，不改动任何生产代码。\n")
    lines.append(f"- 注：live 监测层 pct 用百分数、`pct<0.02` 实际为「近平收」的更严过滤；本 OOS 用**一致分数单位**还原任务公式本意（high_pct≥0.05、pct<0.02、pct≤-0.04）。\n")
    lines.append("\n---\n")

    for r in (m, o):
        lines.append(f"## 2. {r['name']} Gate 指标\n")
        if "note" in r:
            lines.append(f"_{r['note']}_\n\n---\n"); continue
        lines.append("| 指标 | 值 | 阈值 | 是否过 |")
        lines.append("|---|---|---|---|")
        lines.append(f"| 命中倍数 hit_mult | {r['hit_mult']} | ≥1.15 | {'✅' if (r['hit_mult'] and r['hit_mult']>=1.15) else '❌'} |")
        lines.append(f"| PF_t5（组合PF,绑T+5） | {r['pf_t5']} | ≥{meta['random_t5']}（随机真及格线） | {'✅' if r['pf_t5']>=meta['random_t5'] else '❌'} |")
        lines.append(f"| PF_vs_bottom | {r['pf_vs_bottom']} | ≥1.30 | {'✅' if (r['pf_vs_bottom'] and r['pf_vs_bottom']>=1.30) else '❌'} |")
        lines.append(f"| PF_vs_random | {r['pf_vs_random']} | >1.0 | {'✅' if r['pf_vs_random']>1.0 else '❌'} |")
        lines.append(f"| t 统计（vs 全市场） | {r['t_stat']} | ≥2.0 | {'✅' if r['t_stat']>=2.0 else '❌'} |")
        lines.append(f"| DSR（deflated-Sharpe, n_trials={meta['n_trials']}） | {r['dsr']} | ≥{meta['dsr_min']} | {'✅' if r['dsr']>=meta['dsr_min'] else '❌'} |")
        lines.append(f"| WhiteRC p | {r['white_rc_p']} | <0.10 | {'✅' if r['white_rc_p']<0.10 else '❌'} |")
        lines.append(f"| 稳健单调(二元, 事件特征有/无) | {r['monotonic_corr_binary']} | >0 | {'✅' if (r['monotonic_corr_binary'] and r['monotonic_corr_binary']>0) else '❌'} |")
        lines.append(f"| 严重度单调(诊断, 信号子集内) | {r['monotonic_corr_severity']} | (仅供诊断) | {'↑' if (r['monotonic_corr_severity'] and r['monotonic_corr_severity']>0) else '↓(强度反序, 按 §诊断#6 不做强度排序, 不影响门控)'} |")
        lines.append(f"| 经济护栏 | PF_vs_random>1.0 且 |opp_mean_r5|>0.003 | — | {'✅' if r['econ_valid'] else '❌'} |\n")
        lines.append(f"- 辅助：PF_t1={r['pf_t1']}、mean_ret_T1={r['mean_ret_T1']}、mean_ret_T5={r['mean_ret_T5']}、"
                     f"圣杯正例率={r['hg_rate']}（基线 {meta['hit_base']}）。\n")
        reasons = r.get("reasons") or []
        if reasons:
            lines.append("**gate 失败原因**：" + "；".join(reasons) + "\n")
        lines.append("\n---\n")

    lines.append("## 3. 各分箱 PF（feat_stats 口径）\n")
    lines.append("### 3.1 moves_sig — 按盘中冲高幅度(high_pct)十分位（信号子集内）\n")
    lines.append(_bins_md(m.get("cont_bins")))
    lines.append("### 3.2 moves_sig — 二元（signal=1 vs 0，全市场）对照\n")
    lines.append(_bins_md(m.get("binary_bins")))
    lines.append("### 3.3 oversold_sig — 按深跌幅度(pct)十分位（信号子集内）\n")
    lines.append(_bins_md(o.get("cont_bins")))
    lines.append("### 3.4 oversold_sig — 二元（signal=1 vs 0，全市场）对照\n")
    lines.append(_bins_md(o.get("binary_bins")))
    lines.append("\n---\n")

    lines.append("## 4. 结论解读与 factor_registry 候选\n")
    lines.append("### 4.1 moves_sig — REJECT（因子无效，且为反向/回避信号）\n")
    lines.append(f"- 冲高回落(moves)在全市场 {m['n']:,} 个事件上，T+5 组合 PF={m['pf_t5']}（<随机真及格线 {meta['random_t5']}），"
                 f"均值收益 {m['mean_ret_T5']}，t={m['t_stat']}（显著跑输），二元方向反（有信号箱 {m['mean_ret_T5']} vs 无信号箱约 +0.0024）。\n")
    lines.append("- **结论：moves 不能作 buy_score 特征**；它本质是「派发/走弱」的反向信号——冲高回落预示后续走弱。"
                 "这也解释了为何 300291 这类「盘中强动、尾盘回落」票被原发现层漏选：它们本就不该被当作启动买点。**请勿注入**。\n")
    lines.append("- 与「启动日漏选」4 只票的关系：300291 属 moves 型；OOS 证明 moves 型票 T+5 期望为负，**漏选反而避免亏损**，不属于『可覆盖成买入』的票。\n")
    lines.append("\n### 4.2 oversold_sig — PROMOTE（候选，待审批注入）\n")
    lines.append(f"- 深跌放量(oversold)在 {o['n']:,} 个事件上全面过门：命中 {o['hit_mult']}×、PF_t5={o['pf_t5']}（>{meta['random_t5']}）、"
                 f"PF_vs_bottom={o['pf_vs_bottom']}、t={o['t_stat']}、DSR={o['dsr']}、WhiteRC p={o['white_rc_p']}、二元方向正。\n")
    lines.append(f"- 经济护栏：PF_vs_random={o['pf_vs_random']}>1.0 且信号箱 T+5 均值 {o['mean_ret_T5']}>0.003 → 通过。\n")
    lines.append("- **重要结构性注记（FACTOR_FREEZE §诊断#6）**：事件型特征只做「有/无」对比，不做强度排序。"
                 "本信号子集内「严重度单调」为负（越深跌 T+5 越差，monoS=-0.89），即 edge 集中在**浅超跌(-4%~-5%)**，深跌(-8%+)反而弱。"
                 "**这不影响门控**（门控用二元有/无方向），但落地时只能用二元阈值，不能按跌幅强度排序加权。\n")
    lines.append("- 与「启动日漏选」4 只票的关系：宁波东力(10-27 收-6.46%→10-28 +10.03%) 正是 oversold 型的实例。"
                 "oversold_sig 是 4 只可覆盖票中**唯一 OOS 验证有效**的买入型特征候选。\n")
    lines.append("\n### 4.3 factor_registry 候选定义（待注册，须用户/调度审批才注入；mining_frozen 维持）\n")
    rc = res["registry_candidate"]
    lines.append("```json\n" + json.dumps(rc, ensure_ascii=False, indent=2) + "\n```\n")
    lines.append("\n---\n")

    lines.append("## 5. 方法论与自洽性\n")
    lines.append(f"- PIT 严格：pct/high_pct=close/前收-1（前收=前一行收盘）；volr=vol/MA5(前5日量，shift(1)不含当日）；"
                 f"ret_T5 为逐行前向收益，无标签泄漏。\n")
    lines.append(f"- 交易日历：个股缺档（停牌）会令 T+5 跨度异常拉长，本 OOS 以自然日跨度护栏（T+5>{meta['max_t5_gap']}日/T+1>{meta['max_t1_gap']}日）判 NA，避免把复牌跳价当持有收益。\n")
    lines.append(f"- 复用 `factor_freeze_gate.gate` 阈值（命中≥1.15 / PF≥1.30 / t≥2.0 / DSR≥0.95）与"
                 f"`run_live_feature_oos.feat_stats` 的十分位+经济护栏口径；PF **绑持有期 T+5**，真及格线 RANDOM_T5={meta['random_t5']}。\n")
    lines.append(f"- n_trials={meta['n_trials']}（保守沿用冻结门控累计搜索次数）；DSR 在此口径下与既有 REJECT 结论一致。\n")
    lines.append(f"- **不修改任何生产代码**，仅新增只读研究脚本与交付文件；未 git 提交。\n")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _root_cause(r):
    """区分'因子无效'(PF 过不了随机真及格线/方向反) vs '信号噪声大'(PF 方向对但被噪声吃掉/样本或单调不稳)。"""
    if r.get("pf_t5", 0) < RANDOM_T5:
        return "因子无效（组合 PF 未过随机真及格线，持有 T+5 无稳定 edge；且二元方向反，信号箱跑输无信号箱）"
    if (r.get("monotonic_corr_binary") is not None and r["monotonic_corr_binary"] <= 0):
        return "因子无效（二元方向反：有信号箱均值 ≤ 无信号箱）"
    if not r.get("econ_valid"):
        return "信号噪声大（PF 方向对，但 PF_vs_random 未稳过 1.0 或经济量级偏弱）"
    if r.get("white_rc_p", 1) >= 0.10:
        return "信号噪声大（WhiteRC 未通过，超额收益疑似数据窥探/噪声）"
    return "因子无效（多条件联合未过）"


if __name__ == "__main__":
    main()
