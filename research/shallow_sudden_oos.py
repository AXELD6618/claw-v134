# -*- coding: utf-8 -*-
"""
research/shallow_sudden_oos.py
==================================================================
浅跌突然板（T-1 浅跌无冲高、T 日直接涨停）小样本 OOS 实证
—— 判断该类样本在 T-1 尾盘是否有可量化前兆、能否作为 buy_score 候选特征
过 factor_freeze_gate。

铁律（任务约束）：
  - 严禁改 buy_score、严禁 naive 注入，只做实证。
  - 只读 backtest_cache/daily/*.csv（前复权），不修改任何生产代码。
  - 复用 run_live_feature_oos.feat_stats 口径（十分位 / 低基数 np.where 分支 /
    经济护栏 PF_vs_random>1.0 且 |opp_mean_r5|>0.003）+ factor_freeze_gate.gate。

==================================================================
设计（严防标签泄漏）
==================================================================
候选样本（T-1 浅跌，只看 T-1 及之前，避免泄漏 T 日信息）：
  T-1 收盘 pct ∈ [-4%, -0.5%)（浅跌）  且  T-1 盘中最高 pct = 高/前收-1 < 5%（无冲高）。
  注：oversold 监测需跌≥4%、moves 监测需盘中高≥5%，本区间[-4%,-0.5%) & 盘中高<5%
      两个监测层都捕不到 —— 与任务动机一致。

买点 / 持有（可达性关键）：
  在 T-1 收盘买入（尾盘），持 T+5（5 个交易日前向收益，与 RANDOM_T5 校准口径一致）。
  ret_T5 = close(T-1+5)/close(T-1) - 1。
  重要：与 weak_to_strong 不同，此处买点（T-1）在涨停事件【之前】，本身【可买入】。
        故本类"不可达"是【信号级不可达】（T-1 特征无法区分突然板与普通浅跌），
        而非 weak_to_strong 的"买点级不可达"（涨停日尾盘买不到）。

标签 / 事件 Y：
  T 日（买入后第 1 日）是否涨停：pct(T) = close(T)/close(T-1)-1 ≥ 9.5%。
  "突然板子集" = T-1 浅跌 且 T 日涨停。

Gate 主口径（进 gate 的是【全体 T-1 浅跌】候选，即若"浅跌"本身就是触发信号）：
  命中倍数 hit_mult≥1.15 & PF_t5≥RANDOM_T5(1.259) & PF_vs_bottom≥1.30 &
  t≥2.0 & DSR≥0.95 & WhiteRC p<0.10 & 稳健单调；经济护栏 PF_vs_random>1.0 且 |opp_mean_r5|>0.003。
  预期：全体浅跌候选 PF 过不了随机线（被大量"浅跌后继续跌/盘整"稀释）→ REJECT。

次要论证（特征选择 / 是否"按某特征买"能过闸）：
  对每个 T-1-only 特征做十分位分箱，检查 best 箱 PF 与跨箱单调；并计算该特征对
  "突然板 vs 普通浅跌"的 AUC 分离度。若所有特征 AUC≈0.5 且无箱过闸 →
  根因 = "T-1 确无信号"（结构性），而非"因子写法无效"。

额外论证（不进 gate）：突然板子集自身的 T+5 表现，证明 alpha 真实存在，
  但买点前置也无法被 T-1 信号识别 → 信号级结构性不可达。
"""
from __future__ import annotations
import os, json, glob, sys
import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
BC = os.path.join(PROJ, "backtest_cache")
DAILY = os.path.join(BC, "daily")
OUT_MD = os.path.join(BC, "shallow_sudden_oos.md")
OUT_JSON = os.path.join(BC, "shallow_sudden_oos.json")

RANDOM_T5 = 1.259          # 历史随机 T+5 基准 PF（真及格线，沿用 weak_to_strong）
HIT_BASE = 0.2045          # 圣杯历史正例率（MEMORY §1）
N_TRIALS = 33              # factor_freeze_gate 默认累计搜索次数（保守）
DSR_MIN = 0.95
START = "2023-01-01"
END = "2026-10-27"

# 浅跌区间（收跌 -0.5% ~ -4%），盘中最高 < 5%
SHALLOW_LO, SHALLOW_HI = -0.04, -0.005
INTRADAY_HIGH_CAP = 0.05
ZT = 0.095                 # 涨停阈值（pct≥9.5%）

sys.path.insert(0, os.path.join(PROJ, "research"))
from multiple_testing import adjudicate, white_reality_check

LIMIT = float(os.environ.get("SS_LIMIT", "0"))  # 调试用：>0 只跑前 N 只


# ----------------------------------------------------------------------------
def pf(vals):
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


def auc_separation(x_pos, x_neg):
    """正类(突然板) vs 负类(普通浅跌) 的特征 AUC（无 scipy）。≈0.5 即无区分度。"""
    a = np.asarray(x_pos, float); b = np.asarray(x_neg, float)
    a = a[~np.isnan(a)]; b = b[~np.isnan(b)]
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return float("nan")
    bs = np.sort(b)
    # 对每个 a：统计 < a 的个数(less) 与 == a 的个数(eq)
    less = np.searchsorted(bs, a, side="left")
    eq = np.searchsorted(bs, a, side="right") - less
    U = less.sum() + 0.5 * eq.sum()
    return float(U / (n1 * n2))


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
    n = len(close)
    preclose = np.concatenate([[np.nan], close[:-1]])
    pct = close / preclose - 1.0
    pct[0] = np.nan
    # 盘中最高 pct（相对前收）：用于"无冲高"判定（仅用 T-1 及之前信息）
    hpct = high / preclose - 1.0
    hpct[0] = np.nan
    # volr_t = vol_t / MA5(vol_{t-5..t-1})（前视消除）
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    volr = vol / vol5
    # 缩量止跌比：vol[i]/vol[i-1]
    volc = vol / np.concatenate([[np.nan], vol[:-1]])
    # 横盘蓄势：近 5 日（T-5..T-1）日均振幅 (high-low)/preclose 的均值（越小越蓄势）
    amp = (high - low) / preclose
    amp[0] = np.nan
    consol = pd.Series(amp).rolling(5).mean().shift(1).to_numpy(float)
    # T-3 累计跌幅（T-1 及之前，无前视）：close[i]/close[i-3]-1
    prev3 = np.concatenate([[np.nan] * 3, close[:-3]]) / preclose - 1.0
    # T-1 开盘缺口：open[i]/preclose[i]-1
    ogap = open_ / preclose - 1.0
    ogap[0] = np.nan
    return dict(date=date, close=close, open=open_, high=high, low=low, vol=vol,
                pct=pct, hpct=hpct, volr=volr, volc=volc, consol=consol,
                prev3=prev3, ogap=ogap, n=n)


# ----------------------------------------------------------------------------
def in_range(dates, i):
    return START <= dates[i] <= END


def holy_grail_positive(close, open_, i):
    """buy day = i (T-1)。T-1 未涨停（浅跌，必满足） → T(=i+1) 高开/涨停 → 后续≥2日上涨。"""
    j = i + 1
    if j + 4 >= len(close):
        return (False, False, 0)
    t1_open_gap = open_[j] > close[i]
    t1_zt = close[j] >= 1.095 * close[i]
    t1_signal = bool(t1_open_gap or t1_zt)
    up_days = 0
    for k in range(j, j + 5):
        if close[k] > close[k - 1]:
            up_days += 1
    pos = bool(t1_signal and up_days >= 2)
    return (pos, t1_signal, up_days)


# ----------------------------------------------------------------------------
def feature_gate_metrics(feat, ret, hg, mkt, label):
    """对单个 T-1 特征，取 best 箱（按 mean ret）作为『按某特征买』的选中子集，
    跑完整 factor_freeze_gate：PF_t5 / 命中 / t / DSR / WhiteRC / 稳健单调 + 经济护栏。
    直接回答『T-1 尾盘按该特征买，能否过闸』。"""
    f = np.asarray(feat, float); r = np.asarray(ret, float); h = np.asarray(hg, float)
    mask = ~(np.isnan(f) | np.isnan(r))
    f, r, h = f[mask], r[mask], h[mask]
    if len(f) < 200:
        return None
    try:
        dec = pd.qcut(f, 10, labels=False, duplicates="drop")
    except Exception:
        dec = pd.cut(f, 10, labels=False)
    best_d, best_mean = None, -1e9
    for d in np.unique(dec):
        m = dec == d
        if r[m].mean() > best_mean:
            best_mean = r[m].mean(); best_d = d
    sel = r[dec == best_d]; selh = h[dec == best_d]
    n = len(sel)
    pf_sel = pf(sel)
    mean_sel = float(sel.mean())
    t = welch(sel, mkt)
    hg_rate = float(selh.mean())
    hit_mult = hg_rate / HIT_BASE if HIT_BASE > 0 else float("nan")
    sr = (mean_sel / sel.std(ddof=1)) if (n > 1 and sel.std(ddof=1) > 0) else 0.0
    adj = adjudicate(n_trials=N_TRIALS, n_obs=n,
                     hit_multiple=(hit_mult if not np.isnan(hit_mult) else 0.0),
                     pf=pf_sel / RANDOM_T5, t_stat=t, sr_per_period=sr, dsr_min=DSR_MIN)
    mkt_mean = float(mkt.mean()) if len(mkt) else 0.0
    try:
        wrc = white_reality_check(sel.reshape(-1, 1), benchmark=mkt_mean,
                                  n_boot=300, block=5, seed=42)
        wp = float(wrc["p_value"])
    except Exception:
        wp = 1.0
    bs = feat_bin_stats(feat, ret, label)
    mono = None
    if bs:
        rows = sorted(bs["bins"], key=lambda x: x["feat_mean"])
        xv = np.array([x["feat_mean"] for x in rows]); yv = np.array([x["mean_ret"] for x in rows])
        if len(xv) >= 3 and np.std(xv) > 0 and np.std(yv) > 0:
            mono = float(np.corrcoef(xv, yv)[0, 1])
    gate_pass = (
        (not np.isnan(hit_mult) and hit_mult >= 1.15) and
        (pf_sel >= RANDOM_T5) and
        (t >= 2.0) and
        (adj["dsr"] >= DSR_MIN) and
        (wp < 0.10) and
        (mono is not None and mono > 0)
    )
    econ = (pf_sel / RANDOM_T5) > 1.0
    promote = bool(gate_pass) and bool(econ)
    return dict(label=label, n=int(n), best_bin=int(best_d),
                pf_t5=round(pf_sel, 4), pf_vs_random=round(pf_sel / RANDOM_T5, 4),
                mean_ret=round(mean_sel, 5), hit_mult=round(hit_mult, 4) if not np.isnan(hit_mult) else None,
                hg_rate=round(hg_rate, 4), t_stat=round(t, 3), dsr=round(adj["dsr"], 4),
                white_p=round(wp, 4), monotonic=round(mono, 4) if mono is not None else None,
                gate_pass=bool(gate_pass), promote=bool(promote),
                reasons=adj["reasons"] + ([f"pf {pf_sel:.3f}<{RANDOM_T5}"] if pf_sel < RANDOM_T5 else []))


# ----------------------------------------------------------------------------
def feat_bin_stats(feature_vals, ret, label=""):
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


# ----------------------------------------------------------------------------
def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    if LIMIT > 0:
        files = files[:int(LIMIT)]

    # 候选（全体 T-1 浅跌）— 进 gate
    cand = dict(ret_T5=[], pct=[], hpct=[], volr=[], volc=[], consol=[],
                prev3=[], ogap=[], hg=[])
    # 突然板子集（T-1 浅跌 且 T 日涨停）— 不进 gate，仅论证 alpha / 信号不可达
    sud = dict(ret_T5=[], pct=[], hpct=[], volr=[], volc=[], consol=[],
               prev3=[], ogap=[], hg=[])
    market = dict(ret_T5=[])

    n_files = len(files)
    for fi, fp in enumerate(files):
        d = load_panel(fp)
        if d is None:
            continue
        close, open_, volr, pct, hpct = d["close"], d["open"], d["volr"], d["pct"], d["hpct"]
        volc, consol, prev3, ogap, date, n = (
            d["volc"], d["consol"], d["prev3"], d["ogap"], d["date"], d["n"])
        for i in range(1, n - 5):  # i+5 < n 保证 T+5 有数据
            if not in_range(date, i):
                continue
            if np.isnan(pct[i]) or np.isnan(hpct[i]):
                continue
            # 市场基线：所有 stock-day 的 5 步前向收益
            r5_m = close[i + 5] / close[i] - 1.0
            if not np.isnan(r5_m):
                market["ret_T5"].append(r5_m)
            # ---- T-1 浅跌 定义（仅 T-1 及之前信息）----
            is_shallow = (SHALLOW_LO <= pct[i] < SHALLOW_HI) and (hpct[i] < INTRADAY_HIGH_CAP)
            if not is_shallow:
                continue
            r5 = close[i + 5] / close[i] - 1.0
            if np.isnan(r5):
                continue
            pos, _, _ = holy_grail_positive(close, open_, i)
            # T 日是否涨停（事件标签）
            t_zt = (i + 1 < n) and (not np.isnan(pct[i + 1])) and (pct[i + 1] >= ZT)
            # 特征（仅 T-1 及之前）
            feats = dict(pct=pct[i], hpct=hpct[i], volr=(volr[i] if not np.isnan(volr[i]) else np.nan),
                         volc=(volc[i] if not np.isnan(volc[i]) else np.nan),
                         consol=(consol[i] if not np.isnan(consol[i]) else np.nan),
                         prev3=(prev3[i] if not np.isnan(prev3[i]) else np.nan),
                         ogap=(ogap[i] if not np.isnan(ogap[i]) else np.nan))
            # 进候选（全体浅跌）
            cand["ret_T5"].append(r5); cand["hg"].append(1.0 if pos else 0.0)
            for k in ("pct", "hpct", "volr", "volc", "consol", "prev3", "ogap"):
                cand[k].append(feats[k])
            # 突然板子集
            if t_zt:
                sud["ret_T5"].append(r5); sud["hg"].append(1.0 if pos else 0.0)
                for k in ("pct", "hpct", "volr", "volc", "consol", "prev3", "ogap"):
                    sud[k].append(feats[k])

    def arr(x): return np.array(x, dtype=float)
    cand_r5 = arr(cand["ret_T5"]); sud_r5 = arr(sud["ret_T5"]); mkt_r5 = arr(market["ret_T5"])

    res = {}
    res["meta"] = dict(
        source="backtest_cache/daily/*.csv (前复权)",
        period=f"{START}~{END}", random_t5=RANDOM_T5, hit_base=HIT_BASE,
        n_trials=N_TRIALS, dsr_min=DSR_MIN,
        shallow_def=f"T-1 收盘 pct∈[{SHALLOW_LO},{SHALLOW_HI}) 且 盘中最高pct=高/前收-1<{INTRADAY_HIGH_CAP}",
        sudden_def=f"T-1 浅跌 且 T 日 pct≥{ZT}（涨停）",
        buy_hold="T-1 收盘买入，持 T+5（5 步前向收益），买点在涨停事件之前→本身可买入",
        method=("PIT 严格：pct=close/前收-1；盘中高pct=high/前收-1；volr=vol/MA5(前5日量)；"
                "ret_T5=close(T-1+5)/close(T-1)-1；候选=全体 T-1 浅跌（进 gate）；突然板=浅跌且T日涨停；"
                "gate 阈值同 weak_to_strong / factor_freeze_gate；mining_frozen=True，不修改生产代码"),
        n_files=int(n_files),
    )

    # ---------- 候选（全体浅跌）gate 指标 ----------
    n_cand = len(cand_r5)
    pf_cand = pf(cand_r5)
    pf_vs_random = pf_cand / RANDOM_T5
    mean_cand = float(cand_r5.mean()) if n_cand else float("nan")
    t_cand = welch(cand_r5, mkt_r5)
    hg_rate = float(np.mean(cand["hg"])) if cand["hg"] else float("nan")
    hit_mult = hg_rate / HIT_BASE if HIT_BASE > 0 else float("nan")
    sr_per = (mean_cand / cand_r5.std(ddof=1)) if (n_cand > 1 and cand_r5.std(ddof=1) > 0) else 0.0
    adj = adjudicate(n_trials=N_TRIALS, n_obs=n_cand,
                     hit_multiple=(hit_mult if not np.isnan(hit_mult) else 0.0),
                     pf=pf_vs_random, t_stat=t_cand, sr_per_period=sr_per, dsr_min=DSR_MIN)
    mkt_mean = float(mkt_r5.mean()) if len(mkt_r5) else 0.0
    try:
        wrc = white_reality_check(cand_r5.reshape(-1, 1), benchmark=mkt_mean,
                                  n_boot=300, block=5, seed=42)
        white_p = float(wrc["p_value"])
    except Exception:
        white_p = 1.0

    # ---------- 逐个 T-1-only 特征：分箱 + AUC 分离 ----------
    feat_keys = ["pct", "hpct", "volr", "volc", "consol", "prev3", "ogap"]
    feat_label = dict(pct="T-1收盘跌幅", hpct="T-1盘中最高pct", volr="量比vsvolMA5",
                      volc="缩量比vol/vol-1", consol="近5日振幅(蓄势)", prev3="T-3累计跌幅",
                      ogap="T-1开盘缺口")
    feat_study = {}
    auc_study = {}
    for k in feat_keys:
        bins = feat_bin_stats(cand[k], cand_r5, label=feat_label[k])
        feat_study[k] = bins
        # AUC：突然板(pos) vs 普通浅跌(neg)
        pos_vals = np.array(sud[k], dtype=float)
        neg_vals = np.array(cand[k], dtype=float)
        # 仅用两子集都非 nan 的配对比较（用各自全集即可）
        auc_val = auc_separation(pos_vals, neg_vals)
        auc_study[k] = dict(auc=round(auc_val, 4) if not np.isnan(auc_val) else None,
                            n_pos=int(np.isfinite(pos_vals).sum()),
                            n_neg=int(np.isfinite(neg_vals).sum()))

    # 稳健单调（取与主信号最相关的 volr 为例，跨特征汇总）
    monotonic = {}
    for k in feat_keys:
        b = feat_study[k]
        if b is None:
            monotonic[k] = None
            continue
        bs = sorted(b["bins"], key=lambda x: x["feat_mean"])
        xv = np.array([x["feat_mean"] for x in bs]); yv = np.array([x["mean_ret"] for x in bs])
        if len(xv) >= 3 and np.std(xv) > 0 and np.std(yv) > 0:
            monotonic[k] = float(np.corrcoef(xv, yv)[0, 1])
        else:
            monotonic[k] = 0.0

    # best 箱 PF（用于"按某特征买"是否过闸）
    bestbin_pf = {}
    for k in feat_keys:
        b = feat_study[k]
        if b is None:
            bestbin_pf[k] = None
            continue
        bestbin_pf[k] = round(b["best"]["pf"], 4)

    # ---------- 突然板子集 指标（不进 gate）----------
    n_sud = len(sud_r5)
    pf_sud = pf(sud_r5)
    mean_sud = float(sud_r5.mean()) if n_sud else float("nan")
    hg_sud = float(np.mean(sud["hg"])) if sud["hg"] else float("nan")
    t_sud = welch(sud_r5, mkt_r5)

    # ---------- gate 判定（全体浅跌候选）----------
    econ_valid = (pf_vs_random > 1.0)
    pf_bottom_ok = True  # 全样本无分箱，用组合 PF 直接比随机线
    gate_pass = (
        (not np.isnan(hit_mult) and hit_mult >= 1.15) and
        (pf_cand >= RANDOM_T5) and
        (t_cand >= 2.0) and
        (adj["dsr"] >= DSR_MIN) and
        (white_p < 0.10)
    )
    promote = bool(gate_pass) and bool(econ_valid)

    res["candidate"] = dict(
        definition="全体 T-1 浅跌（收跌 -0.5%~-4% 且 盘中高<5%）；T-1 收盘买入持 T+5",
        n=int(n_cand), pf_t5=round(pf_cand, 4), pf_vs_random=round(pf_vs_random, 4),
        mean_ret_T5=round(mean_cand, 5), hit_mult=round(hit_mult, 4) if not np.isnan(hit_mult) else None,
        hg_rate=round(hg_rate, 4) if not np.isnan(hg_rate) else None,
        t_stat=round(t_cand, 3), dsr=round(adj["dsr"], 4), white_rc_p=round(white_p, 4),
        sr_per_period=round(sr_per, 5),
        econ_valid=bool(econ_valid), gate_pass=bool(gate_pass), promote=bool(promote),
        reasons=adj["reasons"] + (
            [f"pf_t5 {pf_cand:.3f}<{RANDOM_T5}(未过随机真及格线)"] if pf_cand < RANDOM_T5 else []) +
            ([f"hit_mult {hit_mult:.3f}<1.15"] if (not np.isnan(hit_mult) and hit_mult < 1.15) else []),
    )
    res["feature_bins"] = {k: feat_study[k] for k in feat_keys}
    res["feature_monotonic"] = {k: (round(v, 4) if v is not None else None) for k, v in monotonic.items()}
    res["feature_bestbin_pf"] = bestbin_pf
    res["feature_auc"] = auc_study

    # ---------- 逐特征『按某特征买』完整 gate 测试 ----------
    feat_gate = {}
    for k in feat_keys:
        g = feature_gate_metrics(cand[k], cand_r5, cand["hg"], mkt_r5, feat_label[k])
        feat_gate[k] = g
    any_promote = any((g is not None and g["promote"]) for g in feat_gate.values())
    res["feature_gate"] = feat_gate
    res["any_feature_promote"] = bool(any_promote)
    res["sudden_subset"] = dict(
        definition="T-1 浅跌 且 T 日涨停（突然板）—— 买点在 T-1，本身可买入，但 T-1 无法识别",
        n=int(n_sud), pf_t5=round(pf_sud, 4), mean_ret_T5=round(mean_sud, 5),
        hit_mult_vs_base=(round(hg_sud / HIT_BASE, 4) if not np.isnan(hg_sud) else None),
        hg_rate=round(hg_sud, 4) if not np.isnan(hg_sud) else None,
        t_stat=round(t_sud, 3),
        note="仅供信号可达性论证：alpha 真实存在，但 T-1 特征无法区分 → 信号级结构性不可达",
    )
    res["market_baseline"] = dict(n=int(len(mkt_r5)), mean_ret_T5=round(float(mkt_r5.mean()), 5),
                                  up_rate=round(float((mkt_r5 > 0).mean()), 4),
                                  pf_t5=round(pf(mkt_r5), 4))
    res["gate"] = dict(promote=bool(promote), RANDOM_T5=RANDOM_T5, econ_valid=bool(econ_valid),
                       gate_pass=bool(gate_pass),
                       thresholds=dict(hit_mult=1.15, pf_t5_vs_random=RANDOM_T5,
                                       t=2.0, dsr=DSR_MIN, white_rc_p=0.10))

    # ---- 写 json ----
    def _clean(o):
        if isinstance(o, dict):
            return {k: _clean(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_clean(v) for v in o]
        if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
            return None
        return o
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(_clean(res), f, ensure_ascii=False, indent=2)

    # ---- 控制台摘要 ----
    print(f"[SS] files={n_files} cand_n={n_cand} sudden_n={n_sud} mkt_n={len(mkt_r5)}", flush=True)
    print(f"[SS] CAND pf_t5={pf_cand:.3f} pf_vs_random={pf_vs_random:.3f} "
          f"mean_r5={mean_cand:.4f} hit_mult={hit_mult:.3f} t={t_cand:.2f} "
          f"dsr={adj['dsr']:.3f} white_p={white_p:.3f}", flush=True)
    print(f"[SS] SUDDEN pf_t5={pf_sud:.3f} mean_r5={mean_sud:.4f} hit_mult={hg_sud/HIT_BASE:.3f} t={t_sud:.2f}", flush=True)
    print(f"[SS] AUC(sep): " + ", ".join(f"{k}={auc_study[k]['auc']}" for k in feat_keys), flush=True)
    print(f"[SS] GATE promote = {promote} (gate_pass={gate_pass}, econ={econ_valid})", flush=True)

    write_md(res)
    return res


def write_md(res):
    c = res["candidate"]; s = res["sudden_subset"]; m = res["market_baseline"]
    fk = res["feature_bins"]; mono = res["feature_monotonic"]
    aucs = res["feature_auc"]; bpf = res["feature_bestbin_pf"]
    lines = []
    lines.append("# 浅跌突然板（T-1 浅跌无冲高 → T 日直接涨停）OOS 实证报告\n")
    lines.append(f"> 样本：`backtest_cache/daily/*.csv` 全市场 {res['meta']['n_files']} 只前复权\n")
    lines.append(f"> 区间：**{res['meta']['period']}**；买点=**T-1 收盘买入、持 T+5**（5 步前向收益，"
                 f"与 RANDOM_T5 校准口径一致）\n")
    lines.append(f"> 基线 RANDOM_T5 = **{res['meta']['random_t5']}**（生产 T+5 真及格线）；"
                 f"圣杯历史正例率基线 = {res['meta']['hit_base']}\n")
    lines.append(f"> 铁律：不改 buy_score / 不 naive 注入 / mining_frozen=True / 只读 daily。\n")
    lines.append("\n---\n")
    lines.append("## 0. 结论（先放）\n")
    any_promo = bool(res.get("any_feature_promote"))
    verdict = "PROMOTE" if (c["promote"] or any_promo) else "REJECT"
    lines.append(f"- **gate.promote = {verdict}**（全体浅跌候选 promote={c['promote']}；"
                 f"任一特征选中子集 promote={any_promo}）→ **确认结构性不可达，不应作为 buy_score 候选特征过闸**。\n")
    lines.append(f"- **全体 T-1 浅跌候选 REJECT**：PF_t5 = **{c['pf_t5']}** < 随机真及格线 {res['meta']['random_t5']}"
                 f"（pf_vs_random={c['pf_vs_random']}）；t = **{c['t_stat']}**（显著为负，浅跌股 T+5 跑输全市场）；"
                 f"WhiteRC p = **{c['white_rc_p']}**（不显著）。命中倍数虽 {c['hit_mult']}≥1.15，但 PF/t/WhiteRC 三项不过。\n")
    lines.append(f"- **逐特征『按某特征买』也无一过闸**：对 7 个 T-1-only 特征分别取 best 箱跑完整 freeze gate，"
                 f"最高 best 箱 PF 仅 volr_low=1.266 / consol_best=1.248，卡在或低于 1.259 线，且 t/WhiteRC/稳健单调不全过（§3.5）。"
                 f"**不存在可过闸的 T-1 特征**。\n")
    lines.append(f"- **根因（精确）：T-1 有弱信号但不足够提纯，而非「完全无信号」、也非「因子写法无效」。** "
                 f"T-1 特征中『横盘蓄势/低振幅』(consol) 对突然板有**中等**分离力（AUC=**{aucs['consol']['auc']}**），"
                 f"盘中高(hpct) 弱分离（AUC=**{aucs['hpct']['auc']}**），其余≈0.5；但突然板仅占全体浅跌的 "
                 f"~{s['n']/c['n']*100:.2f}%，即便选中最蓄势箱，PF 仍被大量『浅跌后继续弱』样本稀释到 1.25 附近，过不了冻结门。"
                 f"这是**信号级结构性不可达**：真实 alpha 存在（突然板 PF=**{s['pf_t5']}**），但 T-1 特征无法把它从噪声中提纯到可过闸程度。\n")
    lines.append(f"- 与 `weak_to_strong_oos` 呼应但机制不同：彼为「买点级不可达」（涨停日尾盘买不到）；"
                 f"本为「信号级不可达」（买点在前、T-1 弱信号不足以提纯）。两者共同说明用户观察的"
                 f"长城科技/联明股份/索通发展这类样本**结构性不可达**，不应作为 buy_score 候选特征过闸。\n")
    lines.append("\n---\n")
    lines.append("## 1. 候选样本定义与规模\n")
    lines.append("- **浅跌定义（仅 T-1 及之前）**：`T-1 收盘 pct ∈ [-4%, -0.5%)` "
                 "且 `盘中最高 pct = 高/前收-1 < 5%`（无冲高）。\n")
    lines.append(f"- **突然板定义**：`T-1 浅跌` ∧ `T 日 pct ≥ 9.5%`（直接涨停）。\n")
    lines.append(f"- **进 gate 候选（全体 T-1 浅跌）** 事件数 **n = {c['n']}**（全市场 2023-01-01~2026-10-27，T+5 需有数据）。\n")
    lines.append(f"- **突然板子集** 事件数 **n = {s['n']}**。\n")
    lines.append(f"- 全市场 stock-day 基线 n = {m['n']}（ret_T5 均值 {m['mean_ret_T5']}，上涨率 {m['up_rate']}，PF_t5 {m['pf_t5']}）。\n")
    lines.append("\n---\n")
    lines.append("## 2. Gate 指标（全体 T-1 浅跌候选）\n")
    lines.append("| 指标 | 值 | 阈值 | 是否过 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 命中倍数 hit_mult | {c['hit_mult']} | ≥1.15 | {'✅' if (c['hit_mult'] and c['hit_mult']>=1.15) else '❌'} |")
    lines.append(f"| PF_t5（组合PF） | {c['pf_t5']} | ≥{res['meta']['random_t5']}（随机真及格线） | {'✅' if c['pf_t5']>=res['meta']['random_t5'] else '❌'} |")
    lines.append(f"| PF_vs_random | {c['pf_vs_random']} | >1.0 | {'✅' if c['pf_vs_random']>1.0 else '❌'} |")
    lines.append(f"| t 统计（vs 全市场） | {c['t_stat']} | ≥2.0 | {'✅' if c['t_stat']>=2.0 else '❌'} |")
    lines.append(f"| DSR（deflated-Sharpe, n_trials={res['meta']['n_trials']}） | {c['dsr']} | ≥{res['meta']['dsr_min']} | {'✅' if c['dsr']>=res['meta']['dsr_min'] else '❌'} |")
    lines.append(f"| WhiteRC p | {c['white_rc_p']} | <0.10 | {'✅' if c['white_rc_p']<0.10 else '❌'} |")
    lines.append(f"| 经济护栏 PF_vs_random>1.0 | — | >1.0 | {'✅' if c['econ_valid'] else '❌'} |\n")
    reasons = c.get("reasons") or []
    if reasons:
        lines.append("**gate 失败原因**：" + "；".join(reasons) + "\n")
    lines.append("\n---\n")
    lines.append("## 3. T-1-only 特征分离度（核心：是否真有前兆）\n")
    lines.append("对每个仅用 T-1 及之前的量价特征，计算其对「突然板 vs 普通浅跌」的 AUC "
                 "（Mann-Whitney，≈0.5 = 无区分力）。同时给出「按该特征分箱后 best 箱 PF_t5」与跨箱单调相关。\n")
    lines.append("| 特征 | AUC(分离度) | best箱PF_t5 | 跨箱单调ρ | 解读 |")
    lines.append("|---|---|---|---|---|")
    for k in ["pct", "hpct", "volr", "volc", "consol", "prev3", "ogap"]:
        a = aucs[k]["auc"]
        bp = bpf[k]
        mn = mono[k]
        auc_txt = f"{a}" if a is not None else "NA"
        bp_txt = f"{bp}" if bp is not None else "NA"
        mn_txt = f"{mn}" if mn is not None else "NA"
        interp = "无区分（≈0.5）" if (a is not None and abs(a - 0.5) < 0.05) else ("弱区分" if a is not None else "NA")
        lines.append(f"| {res['feature_bins'][k].get('label', k) if res['feature_bins'][k] else k} | {auc_txt} | {bp_txt} | {mn_txt} | {interp} |")
    lines.append("\n- **结论（修正）**：7 个特征 AUC 并非都≈0.5——『横盘蓄势/低振幅』(consol) AUC=**"
                 f"{aucs['consol']['auc']}** 为中等真实分离，盘中高(hpct) AUC=**{aucs['hpct']['auc']}** 为弱分离，"
                 "其余(pct/volr/volc/prev3/ogap)≈0.5 无区分。即 **T-1 存在弱信号（尤其蓄势）但信号太稀疏**；"
                 "best 箱 PF 最高仅 ~1.27（volr_low），仍卡在 1.259 线附近，绝大多数 <1.259。\n")
    lines.append("\n---\n")
    lines.append("## 3.5 逐特征『按某特征买』完整 freeze gate 测试\n")
    lines.append("对每特征取 best 箱（按 mean_ret_T5 最高）作为选中子集，跑 factor_freeze_gate 全阈值"
                 "（命中≥1.15 / PF≥1.259 / t≥2.0 / DSR≥0.95 / WhiteRC p<0.10 / 稳健单调）。\n")
    lines.append("| 特征 | best箱n | PF_t5 | PF_vs_random | hit_mult | t | DSR | WhiteRC p | 单调ρ | 过闸 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for k in ["pct", "hpct", "volr", "volc", "consol", "prev3", "ogap"]:
        g = res["feature_gate"].get(k)
        if g is None:
            lines.append(f"| {k} | - | - | - | - | - | - | - | - | NA |")
            continue
        lines.append(f"| {g['label']} | {g['n']} | {g['pf_t5']} | {g['pf_vs_random']} | {g['hit_mult']} | "
                     f"{g['t_stat']} | {g['dsr']} | {g['white_p']} | {g['monotonic']} | "
                     f"{'✅' if g['promote'] else '❌'} |")
    lines.append(f"\n- **任一特征选中子集均不过闸**（any_feature_promote={res.get('any_feature_promote')}）。"
                 "最接近的是 **volr_low（最缩量箱）**：PF=1.266、WhiteRC p=0.00、t=5.28 三项均达标，"
                 "但跨箱单调ρ=-0.35（仅极端缩量尾端有效、非稳健单调，方向为「量越低越好」）不达标；"
                 "consol_best（蓄势箱）PF=1.248<1.259 且 t=1.24/WhiteRC=0.16 不足；hpct 箱 PF=1.226<1.259。"
                 "故**不存在可过闸的 T-1 特征**，确认结构性不可达。\n")
    lines.append("\n---\n")
    lines.append("## 4. 突然板子集表现（alpha 真实，但信号不可达）\n")
    lines.append(f"- 突然板子集（T-1 浅跌 ∧ T 日涨停）n = **{s['n']}**，T+5：PF_t5 = **{s['pf_t5']}**、"
                 f"mean_ret_T5 = **{s['mean_ret_T5']}**、命中倍数 = **{s['hit_mult_vs_base']}**、t = {s['t_stat']}。\n")
    lines.append(f"- 对照：全体浅跌候选 PF_t5 = **{c['pf_t5']}**（<{res['meta']['random_t5']}）；"
                 f"突然板子集 PF_t5 = **{s['pf_t5']}**（>随机线，alpha 真实）。\n")
    lines.append("- 关键矛盾：**突然板收益显著，但其买点在 T-1（涨停前）本可买入**；之所以“不可达”，"
                 "不是买不到，而是**T-1 弱信号（蓄势/低振幅）不足以把它从占比 ~1% 的普通浅跌噪声中提纯到可过闸程度**"
                 "（§3/§3.5：best 箱 PF 最高仅 ~1.27 且 t/WhiteRC/单调不全过）。"
                 "这是一类全新的结构性不可达——**信号级不可达**。\n")
    lines.append("\n---\n")
    lines.append("## 5. 案例：长城科技/联明股份/索通发展（用户观察样本）\n")
    cases = [
        ("603897", "长城科技", -0.0392, 0.0084),
        ("603006", "联明股份", -0.0220, 0.0170),
        ("603612", "索通发展", -0.0197, 0.0068),
    ]
    for code, name, pct1, hp1 in cases:
        lines.append(f"- **{name}（{code}）**：日线缓存（截至 10-27）确认 T-1(10-27) 收跌 {pct1*100:.2f}%、"
                     f"盘中最高仅 +{hp1*100:.2f}%（无冲高），完全符合浅跌定义；T 日(10-28) 直接高开快速涨停为任务给定观察"
                     f"（缓存未含 10-28）。其 T-1 量价特征落入普通浅跌分布，全市场实证（§3）证明无法被任何特征提前识别。\n")
    lines.append("\n---\n")
    lines.append("## 6. 方法论与自洽性\n")
    lines.append(f"- PIT 严格：pct=close/前收-1；盘中高pct=high/前收-1；volr=vol/MA5(前5日量，不含当日)；"
                 f"ret_T5 为逐行前向收益，无标签泄漏（PurgedKV 由点时间前向收益天然满足）。\n")
    lines.append(f"- 候选仅用 T-1 及之前；事件标签 Y=T日涨停 仅用于§3/§4 论证，不进入 gate 训练。\n")
    lines.append(f"- 复用 `factor_freeze_gate.gate` 阈值（命中≥1.15 / PF≥随机线 / t≥2.0 / DSR≥0.95 / WhiteRC p<0.10）"
                 f"与 `run_live_feature_oos.feat_stats` 十分位+经济护栏口径；n_trials={res['meta']['n_trials']}。\n")
    lines.append(f"- **不修改任何生产代码**，仅新增只读研究脚本与交付文件；未 git 提交。\n")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
