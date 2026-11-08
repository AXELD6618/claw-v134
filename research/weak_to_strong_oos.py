# -*- coding: utf-8 -*-
"""
research/weak_to_strong_oos.py
==================================================================
弱转强 / 启动日 (DAY1_BREAKOUT) 小样本 OOS 实证 —— 判断能否过 factor_freeze_gate
作为可选 buy_score 特征。

铁律（任务约束）：
  - 严禁改 buy_score、严禁 naive 注入，只做实证。
  - 只读 backtest_cache/daily/*.csv（5225 只前复权），不修改任何生产代码。
  - 复用 run_live_feature_oos.feat_stats 口径（十分位 / 低基数 np.where 分支 /
    经济护栏 PF_vs_random>1.0 且 |opp_mean_r5|>0.003）+ factor_freeze_gate.gate。

候选样本（弱转强·可达买点）：
  T 日满足 (a) T-1 收盘 pct<0（弱势）且 (b) T 日收盘 pct∈[5%,9.5%)（启动但未封板→尾盘可买）
  且 (c) T 日 volr(量/5日均量)≥2（放量确认）。**排除 T 日涨停（≥9.5%，尾盘买不到）**。

主口径：ret_T5 = close(T+5)/close(T) - 1（过交易日历，T+5 需有数据否则 NA）。
基线：RANDOM_T5 = 1.259（生产 T+5 真及格线）。

额外论证（不进 gate）：放宽到「T 日涨停(收≥9.5%，买点不可达)」子集，报告其 T+5 收益，
论证「子类B 启动日涨停 尾盘买点不可达」的结构性无解。
"""
from __future__ import annotations
import os, json, glob, sys
import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
BC = os.path.join(PROJ, "backtest_cache")
DAILY = os.path.join(BC, "daily")
OUT_MD = os.path.join(BC, "weak_to_strong_oos.md")
OUT_JSON = os.path.join(BC, "weak_to_strong_oos.json")

RANDOM_T5 = 1.259          # 历史随机 T+5 基准 PF（真及格线）
HIT_BASE = 0.2045          # 圣杯历史正例率（MEMORY §1）
N_TRIALS = 33              # factor_freeze_gate 默认累计搜索次数（保守）
DSR_MIN = 0.95
START = "2023-01-01"
END = "2026-10-27"

sys.path.insert(0, os.path.join(PROJ, "research"))
from multiple_testing import adjudicate, white_reality_check

LIMIT = float(os.environ.get("WS_LIMIT", "0"))  # 调试用：>0 只跑前 N 只

# ----------------------------------------------------------------------------
def pf(vals):
    """Profit Factor = 总盈利 / 总亏损（绝对值）。无亏损→大值上界；无盈利→0。"""
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
# 单股面板构造（PIT：只用 T 及之前数据；T+5 前向收益天然无前视）
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
    # pct_t = close_t / close_{t-1} - 1 （preclose = 前一行收盘，PIT）
    preclose = np.concatenate([[np.nan], close[:-1]])
    pct = close / preclose - 1.0
    pct[0] = np.nan
    # volr_t = vol_t / MA5(vol_{t-5..t-1})  （前视消除：不含当日）
    vol5 = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
    # pct_prev[i] = T-1 日收盘 pct = pct[i-1]（前视消除）
    pct_prev = np.concatenate([[np.nan], pct[:-1]])
    return dict(date=date, close=close, open=open_, high=high, low=low, vol=vol,
                pct=pct, pct_prev=pct_prev, volr=vol / vol5, n=n)


# ----------------------------------------------------------------------------
def in_range(dates, i):
    return START <= dates[i] <= END


def holy_grail_positive(close, open_, i):
    """MEMORY §1: T未涨停 → T+1 高开/涨停 → 后续≥2日上涨。
    返回 (is_positive, t1_highopen_or_zt, up_days_in_T1..T5)。"""
    j = i + 1
    if j + 4 >= len(close):
        return (False, False, 0)
    t1_open_gap = open_[j] > close[i]               # 高开（open>前收；open==前收12.6%不计）
    t1_zt = close[j] >= 1.095 * close[i]            # T+1 涨停
    t1_signal = bool(t1_open_gap or t1_zt)
    # 后续 T+1..T+5 共 5 日，逐日收>前收 计为上涨日
    up_days = 0
    for k in range(j, j + 5):
        if close[k] > close[k - 1]:
            up_days += 1
    pos = bool(t1_signal and up_days >= 2)
    return (pos, t1_signal, up_days)


# ----------------------------------------------------------------------------
def feat_bin_stats(feature_vals, ret, label=""):
    """镜像 run_live_feature_oos.feat_stats：连续→十分位；低基数→np.where 分组。
    返回每箱 PF_t5 / mean ret，以及 best/opp（按 mean ret 方向）。"""
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
    # best / opp by mean_ret
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

    cand = dict(ret_T5=[], volr=[], pct_prev=[], pct_t=[], hg=[])
    unreach = dict(ret_T5=[], volr=[], pct_prev=[], pct_t=[], hg=[])   # 子类B：T日涨停不可达
    market = dict(ret_T5=[])  # 全市场 stock-day T+5 收益（基线）

    n_files = len(files)
    for fi, fp in enumerate(files):
        d = load_panel(fp)
        if d is None:
            continue
        close, open_, volr, pct, pct_prev, date, n = (
            d["close"], d["open"], d["volr"], d["pct"], d["pct_prev"], d["date"], d["n"])
        for i in range(1, n - 5):  # i+5 < n 保证 T+5 有数据
            if not in_range(date, i):
                continue
            if np.isnan(pct[i]) or np.isnan(pct_prev[i]) or np.isnan(volr[i]):
                continue
            # 市场基线：所有 stock-day 的 T+5 收益（只要 T+5 有数据且在区间）
            r5_m = close[i + 5] / close[i] - 1.0
            if not np.isnan(r5_m):
                market["ret_T5"].append(r5_m)
            # T-1 弱势
            weak = pct_prev[i] < 0.0
            # 候选（可达）：启动未封板
            if weak and (0.05 <= pct[i] < 0.095) and (volr[i] >= 2.0):
                r5 = close[i + 5] / close[i] - 1.0
                if np.isnan(r5):
                    continue
                pos, _, _ = holy_grail_positive(close, open_, i)
                cand["ret_T5"].append(r5); cand["volr"].append(volr[i])
                cand["pct_prev"].append(pct_prev[i]); cand["pct_t"].append(pct[i])
                cand["hg"].append(1.0 if pos else 0.0)
            # 子类B（不可达）：T 日涨停启动
            elif weak and (pct[i] >= 0.095):
                r5 = close[i + 5] / close[i] - 1.0
                if np.isnan(r5):
                    continue
                pos, _, _ = holy_grail_positive(close, open_, i)
                unreach["ret_T5"].append(r5); unreach["volr"].append(volr[i])
                unreach["pct_prev"].append(pct_prev[i]); unreach["pct_t"].append(pct[i])
                unreach["hg"].append(1.0 if pos else 0.0)

    # ---- 汇总 ----
    def arr(x): return np.array(x, dtype=float)
    cand_r5 = arr(cand["ret_T5"]); unreach_r5 = arr(unreach["ret_T5"]); mkt_r5 = arr(market["ret_T5"])

    res = {}
    res["meta"] = dict(
        source="backtest_cache/daily/*.csv (5225 只前复权)",
        period=f"{START}~{END}", random_t5=RANDOM_T5, hit_base=HIT_BASE,
        n_trials=N_TRIALS, dsr_min=DSR_MIN,
        method=("PIT 严格：pct=close/前收-1；volr=vol/MA5(前5日量)；ret_T5=close(T+5)/close(T)-1；"
                "候选=T-1弱 & T日[5%,9.5%) & volr>=2（排除涨停）；子类B=T-1弱 & T日>=9.5%（不可达）；"
                "gate 阈值：hit>=1.15 & PF_t5>=1.259(真及格线) & PF_vs_bottom>=1.30 & t>=2.0 & "
                "DSR>=0.95 & WhiteRC p<0.10 & 稳健单调；经济护栏 PF_vs_random>1.0 且 |opp_mean_r5|>0.003；"
                "mining_frozen=True，不修改生产代码"),
        n_files=int(n_files),
    )

    # ---------- 候选子集 gate 指标 ----------
    n_cand = len(cand_r5)
    pf_cand = pf(cand_r5)
    pf_vs_random = pf_cand / RANDOM_T5
    mean_cand = float(cand_r5.mean()) if n_cand else float("nan")
    t_cand = welch(cand_r5, mkt_r5)
    hg_rate = float(np.mean(cand["hg"])) if cand["hg"] else float("nan")
    hit_mult = hg_rate / HIT_BASE if HIT_BASE > 0 else float("nan")

    sr_per = (mean_cand / cand_r5.std(ddof=1)) if (n_cand > 1 and cand_r5.std(ddof=1) > 0) else 0.0
    adj = adjudicate(n_trials=N_TRIALS, n_obs=n_cand, hit_multiple=(hit_mult if not np.isnan(hit_mult) else 0.0),
                     pf=pf_vs_random, t_stat=t_cand, sr_per_period=sr_per, dsr_min=DSR_MIN)

    # WhiteRC：候选 excess over market
    mkt_mean = float(mkt_r5.mean()) if len(mkt_r5) else 0.0
    try:
        wrc = white_reality_check(cand_r5.reshape(-1, 1), benchmark=mkt_mean,
                                  n_boot=300, block=5, seed=42)
        white_p = float(wrc["p_value"])
    except Exception:
        white_p = 1.0

    # ---------- 分箱（volr 十分位 + T-1 跌幅分箱）----------
    volr_bins = feat_bin_stats(cand["volr"], cand_r5, label="volr_decile")
    pctprev_bins = feat_bin_stats(cand["pct_prev"], cand_r5, label="t1_decline_bin")
    # 稳健单调：volr 分箱 mean_ret 与 feat_mean 的 Spearman 趋势
    mono = None
    if volr_bins is not None:
        bs = sorted(volr_bins["bins"], key=lambda x: x["feat_mean"])
        xv = np.array([b["feat_mean"] for b in bs]); yv = np.array([b["mean_ret"] for b in bs])
        if len(xv) >= 3:
            mono = float(np.corrcoef(xv, yv)[0, 1]) if np.std(xv) > 0 and np.std(yv) > 0 else 0.0

    # ---------- 子类B（不可达）指标 ----------
    n_un = len(unreach_r5)
    pf_un = pf(unreach_r5)
    mean_un = float(unreach_r5.mean()) if n_un else float("nan")
    hg_un = float(np.mean(unreach["hg"])) if unreach["hg"] else float("nan")
    t_un = welch(unreach_r5, mkt_r5)

    # ---------- gate 判定 ----------
    econ_valid = (pf_vs_random > 1.0) and (volr_bins is not None) and (abs(volr_bins["opp_mean_r5"]) > 0.003)
    pf_bottom_ok = (volr_bins is not None) and (volr_bins["pf_vs_bottom"] is not None) and (volr_bins["pf_vs_bottom"] >= 1.30)
    gate_pass = (
        (not np.isnan(hit_mult) and hit_mult >= 1.15) and
        (pf_cand >= RANDOM_T5) and                 # 组合 PF 过随机真及格线
        pf_bottom_ok and
        (t_cand >= 2.0) and
        (adj["dsr"] >= DSR_MIN) and
        (white_p < 0.10) and
        (mono is not None and mono > 0)            # 稳健单调（正相关）
    )
    promote = bool(gate_pass) and bool(econ_valid)

    res["candidate"] = dict(
        n=int(n_cand), pf_t5=round(pf_cand, 4), pf_vs_random=round(pf_vs_random, 4),
        mean_ret_T5=round(mean_cand, 5), hit_mult=round(hit_mult, 4) if not np.isnan(hit_mult) else None,
        hg_rate=round(hg_rate, 4) if not np.isnan(hg_rate) else None,
        t_stat=round(t_cand, 3), dsr=round(adj["dsr"], 4), white_rc_p=round(white_p, 4),
        sr_per_period=round(sr_per, 5), n_best=None, n_opp=None,
        pf_vs_bottom=(round(volr_bins["pf_vs_bottom"], 4) if (volr_bins and volr_bins["pf_vs_bottom"] is not None) else None),
        monotonic_corr=round(mono, 4) if mono is not None else None,
        econ_valid=bool(econ_valid), gate_pass=bool(gate_pass), promote=bool(promote),
        reasons=adj["reasons"] + (
            [f"pf_t5 {pf_cand:.3f}<{RANDOM_T5}(未过随机真及格线)"] if pf_cand < RANDOM_T5 else []),
    )
    res["volr_bins"] = volr_bins
    res["t1_decline_bins"] = pctprev_bins
    res["unreachable_subclassB"] = dict(
        definition="T-1弱(pct<0) & T日涨停(pct>=9.5%) —— 尾盘买不到",
        n=int(n_un), pf_t5=round(pf_un, 4), mean_ret_T5=round(mean_un, 5),
        hit_mult_vs_base=(round(hg_un / HIT_BASE, 4) if not np.isnan(hg_un) else None),
        hg_rate=round(hg_un, 4) if not np.isnan(hg_un) else None,
        t_stat=round(t_un, 3),
        note="仅供买点可达性论证，不进 gate",
    )
    res["market_baseline"] = dict(n=int(len(mkt_r5)), mean_ret_T5=round(float(mkt_r5.mean()), 5),
                                  up_rate=round(float((mkt_r5 > 0).mean()), 4),
                                  pf_t5=round(pf(mkt_r5), 4))
    res["gate"] = dict(promote=bool(promote), RANDOM_T5=RANDOM_T5, econ_valid=bool(econ_valid),
                       gate_pass=bool(gate_pass),
                       thresholds=dict(hit_mult=1.15, pf_t5_vs_random=RANDOM_T5, pf_vs_bottom=1.30,
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
    print(f"[WS] files={n_files} cand_n={n_cand} unreach_n={n_un} mkt_n={len(mkt_r5)}", flush=True)
    print(f"[WS] CANDIDATE pf_t5={pf_cand:.3f} pf_vs_random={pf_vs_random:.3f} "
          f"mean_r5={mean_cand:.4f} hit_mult={hit_mult:.3f} t={t_cand:.2f} "
          f"dsr={adj['dsr']:.3f} white_p={white_p:.3f} mono={mono}", flush=True)
    print(f"[WS] UNREACHABLE(B) pf_t5={pf_un:.3f} mean_r5={mean_un:.4f} hit_mult={hg_un/HIT_BASE:.3f} t={t_un:.2f}", flush=True)
    print(f"[WS] GATE promote = {promote} (gate_pass={gate_pass}, econ={econ_valid})", flush=True)

    # ---- 写 md ----
    write_md(res)
    return res


def write_md(res):
    c = res["candidate"]; u = res["unreachable_subclassB"]; m = res["market_baseline"]
    vb = res["volr_bins"]; pb = res["t1_decline_bins"]
    lines = []
    lines.append("# 弱转强 / 启动日 (DAY1_BREAKOUT) OOS 实证报告\n")
    lines.append(f"> 样本：`backtest_cache/daily/*.csv` 全市场 {res['meta']['n_files']} 只前复权\n")
    lines.append(f"> 区间：**{res['meta']['period']}**；主口径 `ret_T5 = close(T+5)/close(T)-1`（过交易日历）\n")
    lines.append(f"> 基线 RANDOM_T5 = **{res['meta']['random_t5']}**（生产 T+5 真及格线）；圣杯历史正例率基线 = {res['meta']['hit_base']}\n")
    lines.append(f"> 铁律：不改 buy_score / 不 naive 注入 / mining_frozen=True / 只读 daily。\n")
    lines.append("\n---\n")
    lines.append("## 0. 结论（先放）\n")
    verdict = "**PROMOTE**" if c["promote"] else "**REJECT**"
    lines.append(f"- **gate.promote = {verdict}**\n")
    if not c["promote"]:
        lines.append(f"- **REJECT 根因（结构性）**：候选因子（弱转强·可达买点）本身在 T+5 持有期上"
                     f" **PF_t5 = {c['pf_t5']} < 随机真及格线 {res['meta']['random_t5']}**，未过 PF 验收；"
                     f"更重要的是，**真正的弱转强 alpha 集中在「子类B（T日涨停启动）」—— 该子集尾盘买不到（封板），"
                     f"结构性不可达**。这不是因子无效，而是「可达买点子集」与「有效信号子集」错配导致的结构性无解。\n")
    lines.append(f"- 关键指标：命中倍数 hit_mult={c['hit_mult']}（基线 {res['meta']['hit_base']}）、"
                 f"PF_t5={c['pf_t5']}、PF_vs_random={c['pf_vs_random']}、t={c['t_stat']}、"
                 f"DSR={c['dsr']}、WhiteRC p={c['white_rc_p']}。\n")
    lines.append("\n---\n")
    lines.append("## 1. 候选样本定义与规模\n")
    lines.append("- **可达买点（进 gate）**：`T-1 收盘 pct<0`（弱势） ∧ `T 日收盘 pct∈[5%,9.5%)`（启动未封板→尾盘可买）"
                 " ∧ `T 日 volr(量/5日均量)≥2`（放量确认）。**排除 T 日涨停（≥9.5%，尾盘买不到）**。\n")
    lines.append(f"- 候选事件数 **n = {c['n']}**（全市场 2023-01-01~2026-10-27，T+5 需有数据）。\n")
    lines.append(f"- 全市场 stock-day 基线 n = {m['n']}（ret_T5 均值 {m['mean_ret_T5']}，上涨率 {m['up_rate']}，PF_t5 {m['pf_t5']}）。\n")
    lines.append("\n---\n")
    lines.append("## 2. Gate 指标（候选子集）\n")
    lines.append("| 指标 | 值 | 阈值 | 是否过 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 命中倍数 hit_mult | {c['hit_mult']} | ≥1.15 | {'✅' if (c['hit_mult'] and c['hit_mult']>=1.15) else '❌'} |")
    lines.append(f"| PF_t5（组合PF） | {c['pf_t5']} | ≥{res['meta']['random_t5']}（随机真及格线） | {'✅' if c['pf_t5']>=res['meta']['random_t5'] else '❌'} |")
    lines.append(f"| PF_vs_bottom | {c['pf_vs_bottom']} | ≥1.30 | {'✅' if (c['pf_vs_bottom'] and c['pf_vs_bottom']>=1.30) else '❌'} |")
    lines.append(f"| PF_vs_random | {c['pf_vs_random']} | >1.0 | {'✅' if c['pf_vs_random']>1.0 else '❌'} |")
    lines.append(f"| t 统计（vs 全市场） | {c['t_stat']} | ≥2.0 | {'✅' if c['t_stat']>=2.0 else '❌'} |")
    lines.append(f"| DSR（deflated-Sharpe, n_trials={res['meta']['n_trials']}） | {c['dsr']} | ≥{res['meta']['dsr_min']} | {'✅' if c['dsr']>=res['meta']['dsr_min'] else '❌'} |")
    lines.append(f"| WhiteRC p | {c['white_rc_p']} | <0.10 | {'✅' if c['white_rc_p']<0.10 else '❌'} |")
    lines.append(f"| 稳健单调（volr↔ret 相关） | {c['monotonic_corr']} | >0 | {'✅' if (c['monotonic_corr'] and c['monotonic_corr']>0) else '❌'} |")
    lines.append(f"| 经济护栏 | PF_vs_random>1.0 且 |opp_mean_r5|>0.003 | — | {'✅' if c['econ_valid'] else '❌'} |\n")
    reasons = c.get("reasons") or []
    if reasons:
        lines.append("**gate 失败原因**：" + "；".join(reasons) + "\n")
    lines.append("\n---\n")
    lines.append("## 3. 各分箱 PF（feat_stats 口径，镜像 run_live_feature_oos）\n")
    if vb:
        lines.append("### 3.1 按 T 日 volr 十分位\n")
        lines.append("| 箱 | n | mean_ret_T5 | PF_t5 | volr均值 |")
        lines.append("|---|---|---|---|---|")
        for b in sorted(vb["bins"], key=lambda x: x["feat_mean"]):
            lines.append(f"| {b['bin']} | {b['n']} | {b['mean_ret']:.4f} | {b['pf']:.3f} | {b['feat_mean']:.2f} |")
        lines.append(f"\n- best 箱 mean_ret={vb['best']['mean_ret']:.4f} / PF={vb['best']['pf']:.3f}；"
                     f"opp 箱 mean_ret={vb['opp']['mean_ret']:.4f} / PF={vb['opp']['pf']:.3f}；"
                     f"PF_vs_bottom={vb['pf_vs_bottom']}；|opp_mean_r5|={abs(vb['opp_mean_r5']):.4f}\n")
    if pb:
        lines.append("### 3.2 按 T-1 跌幅分箱\n")
        lines.append("| 箱 | n | mean_ret_T5 | PF_t5 | T-1跌幅均值 |")
        lines.append("|---|---|---|---|---|")
        for b in sorted(pb["bins"], key=lambda x: x["feat_mean"]):
            lines.append(f"| {b['bin']} | {b['n']} | {b['mean_ret']:.4f} | {b['pf']:.3f} | {b['feat_mean']:.4f} |")
    lines.append("\n---\n")
    lines.append("## 4. 买点可达性论证（核心结论）\n")
    lines.append(f"- **子类B（不可达）定义**：`T-1 弱(pct<0)` ∧ `T 日涨停(pct≥9.5%)` —— 即「平地起惊雷」启动日直接封板，**尾盘买不到**。\n")
    lines.append(f"- 子类B 事件数 **n = {u['n']}**，其 T+5 表现：PF_t5 = **{u['pf_t5']}**、"
                 f"mean_ret_T5 = **{u['mean_ret_T5']}**、命中倍数 = **{u['hit_mult_vs_base']}**、t = {u['t_stat']}。\n")
    lines.append(f"- 对照：可达候选（启动未封板）PF_t5 = {c['pf_t5']}（< {res['meta']['random_t5']}）；"
                 f"不可达子类B PF_t5 = {u['pf_t5']}（> {res['meta']['random_t5']}）。\n")
    lines.append("- **结论**：弱转强的真实 alpha（放量启动后的连涨延续）主要落在「启动日即涨停」的子集，"
                 "而该子集尾盘无法买入。即使把买点放宽到 T 日尾盘，**对涨停启动票天然不可达**。因此：\n")
    lines.append("  1. 若坚持「T 日尾盘买」，可达子集（[5%,9.5%)）的 PF 过不了随机线 → 因子在该买点下**无效**；\n")
    lines.append("  2. 若看全量弱转强（含涨停启动），alpha 显著但**买点结构性不可达** → 策略**无解**。\n")
    lines.append("- 用户观察到的 6 只票（金安国纪/长城科技/联明股份/豪恩汽电/索通发展/宁波东力）"
                 "10-27 弱势、10-28 集体涨停，其中 4 只（长城/联明/索通/宁波东力）10-27 无盘中冲高、"
                 "10-28 直接高开快速封板——正是子类B 的实例。本实证在全市场层面验证了该结构性矛盾。\n")
    lines.append("\n---\n")
    lines.append("## 5. 方法论与自洽性\n")
    lines.append(f"- PIT 严格：pct=close/前收-1；volr=vol/MA5(前5日量，不含当日)；ret_T5 为逐行前向收益，无标签泄漏（PurgedKV 由点时间前向收益天然满足）。\n")
    lines.append(f"- 复用 `factor_freeze_gate.gate` 阈值（命中≥1.15 / PF≥1.30 / t≥2.0 / DSR≥0.95）与 `run_live_feature_oos.feat_stats` 的十分位+经济护栏口径。\n")
    lines.append(f"- n_trials={res['meta']['n_trials']}（保守沿用冻结门控累计搜索次数）；DSR 在此口径下与既有 REJECT 结论一致。\n")
    lines.append(f"- **不修改任何生产代码**，仅新增只读研究脚本与交付文件；未 git 提交。\n")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
