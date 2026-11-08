# -*- coding: utf-8 -*-
"""
research/r31_variants.py — r31_breakout 的条件化变体搜索（只读研究，不改任何生产代码）

## 目的
裸版 r31_breakout（sd10 = sh(roll_std(ret,10),1) < 0.01 且 ret > 0.03）实测
日均超额 +86.76bp / 配对 t=3.08，但 Purged 5 折仅 3/5、2026Q3 为负、20bp 成本后不显著，
结论是 WATCH。本脚本在裸版之上叠加 14 个条件化变体，逐一用**同一套口径**检验，
看能否救到可部署（PROMOTE）。裸版本身作为对照基线一并输出。

## 口径（严格照抄 holygrail_excess_probe.py / r31_robustness.py）
- TEST 区间 2024-07-01 起；idx 需满足 T+1 间隔<=4 日、T+5 间隔<=9 日
- BUY 可买子集：T 日未涨停（阈值 lim-0.005，lim 按代码前缀 300/301/688/689->20%，
  8/4/920->30%，其余->10%）且 ret_hg 有效
- ret_hg：T+1 若涨停则按 T+1 收盘兑现(r1)，否则持有到 T+5 收盘(r5)
- 核心指标 = 按日配对超额：excess_d = mean(信号股当日 ret_hg) - mean(可买子集全部股当日 ret_hg)
  再对超额序列做配对 t 检验（n = 交易日数）
- 成本：仅在信号腿扣双边，net_excess = gross_excess - 2 * single_side_cost
- Purged 5 折：折间丢弃 5 个交易日（复用 r31_robustness.make_purged_folds）

## 判定标准（写死，不事后改）
PROMOTE 需同时满足 6 条：
  C1 日均超额 > 0
  C2 配对 t >= 2.0
  C3 Purged 5 折中正折数 >= 4
  C4 单边 10bp 成本后 t >= 2.0
  C5 2026Q3、2026Q4 超额均不为负（>= 0）
  C6 交易日数 >= 100 且 n >= 500
WATCH = 满足 C1/C2/C6 但 C3/C4/C5 未满足部分；其余 REJECT。

## 铁律
只读 daily；唯一写出为 backtest_cache/r31_variants.json；不改任何生产代码/生产 json；不 git 提交。

运行：
E:/WorkBuddy_dot_workbuddy/Claw/.venv/Scripts/python.exe \
    E:/WorkBuddy_dot_workbuddy/Claw/research/r31_variants.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT_JSON = os.path.join(PROJ, "backtest_cache", "r31_variants.json")

TEST_START = "2024-07-01"
MAX_T5_GAP = 9
MAX_T1_GAP = 4
MIN_ROWS = 17            # 与 r31_robustness.MIN_ROWS_R31 完全一致，保证裸版可复现
REQUIRED_COLUMNS = ("日期", "开盘", "收盘", "最高", "最低", "成交量")

N_FOLDS = 5
PURGE_DAYS = 5
COST_10BP = 0.0010       # 判定标准用的是单边 10bp
COST_20BP = 0.0020       # 参考
DAILY_CAP = 20           # 变体 12：当日全市场触发数上限

# ---- 判定阈值（写死）----
GATE_T = 2.0
GATE_POS_FOLDS = 4
GATE_MIN_DAYS = 100
GATE_MIN_N = 500
RECENT_QUARTERS = ("2026Q3", "2026Q4")

sys.path.insert(0, os.path.join(PROJ, "research"))
from holygrail_align_probe import build_signals, limit_of, roll_mean, roll_std, roll_max, sh  # noqa: E402
from r31_robustness import day_agg, iso_date, make_purged_folds, paired_metrics, series_stats  # noqa: E402


# ==================================================================== 变体定义
# 每个变体 = 裸版 r31_breakout 之上叠加一个条件。mask 建立在「已触发裸版的事件」之上。
def build_variant_masks(ev: dict) -> "list[tuple[str, str, np.ndarray]]":
    """返回 [(名称, 判定表达式, bool mask)]。ev 为裸版事件级特征字典。"""
    ret = ev["ret"]
    volr5 = ev["volr5"]
    above20 = ev["above20"]
    above60 = ev["above60"]
    ret20 = ev["ret20"]
    gene = ev["gene"]
    newhigh = ev["newhigh"]
    prevret = ev["prevret"]
    amp = ev["amp"]
    ok = np.isfinite

    v = []
    v.append(("v01_volr5_ge_1.5", "volr5 >= 1.5", ok(volr5) & (volr5 >= 1.5)))
    v.append(("v02_volr5_ge_2.0", "volr5 >= 2.0", ok(volr5) & (volr5 >= 2.0)))
    v.append(("v03_close_gt_ma20", "close > MA20", above20))
    v.append(("v04_close_gt_ma60", "close > MA60", above60))
    v.append(("v05_ret_band_0.095", "ret in (0.03, 0.095]", (ret <= 0.095)))
    v.append(("v06_ret20_lt_0.20", "ret20 < 0.20", ok(ret20) & (ret20 < 0.20)))
    v.append(("v07_ret20_in_0_0.20", "ret20 in [0, 0.20]", ok(ret20) & (ret20 >= 0.0) & (ret20 < 0.20)))
    v.append(("v08_limitup_gene20", "近20日出现过涨停", gene))
    v.append(("v09_new_high20", "当日收盘创20日新高", newhigh))
    v.append(("v10_not_crashed", "T-1 涨幅 > -0.02", ok(prevret) & (prevret > -0.02)))
    v.append(("v11_amp_lt_0.10", "振幅 < 0.10", ok(amp) & (amp < 0.10)))
    v.append(("v12_daily_cap_20", "当日全市场触发数 <= 20", ev["cap20"]))
    v.append(("v13_volr5_1.5_ma20", "volr5 >= 1.5 & close > MA20",
              ok(volr5) & (volr5 >= 1.5) & above20))
    v.append(("v14_volr5_1.5_ma20_ret095", "volr5 >= 1.5 & close > MA20 & ret in (0.03, 0.095]",
              ok(volr5) & (volr5 >= 1.5) & above20 & (ret <= 0.095)))
    return v


# ==================================================================== 扫描
def scan_market():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    limit = int(os.environ.get("RV_LIMIT", "0"))
    if limit > 0:
        files = files[:limit]
    start_num = int(np.datetime64(TEST_START, "D").astype(np.int64))
    base_d, base_r = [], []
    ev_parts = {k: [] for k in ("date", "ret_hg", "ret", "volr5", "above20",
                                "above60", "ret20", "gene", "newhigh", "prevret", "amp")}
    read_errors, skipped_short, mismatch = [], [], []
    checked = 0

    for fi, fp in enumerate(files, start=1):
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            df = pd.read_csv(fp, dtype={"日期": str})
            if not set(REQUIRED_COLUMNS).issubset(df.columns):
                if df.shape[1] != len(REQUIRED_COLUMNS):
                    raise ValueError("列数异常: %d" % df.shape[1])
                df = pd.read_csv(fp, header=None, names=REQUIRED_COLUMNS, dtype={"日期": str})
            df = df.sort_values("日期").reset_index(drop=True)
        except Exception as exc:
            read_errors.append({"file": os.path.basename(fp), "error": str(exc)})
            continue
        if len(df) < MIN_ROWS:
            skipped_short.append(code)
            continue

        close = df["收盘"].to_numpy(float)
        open_ = df["开盘"].to_numpy(float)
        high = df["最高"].to_numpy(float)
        low = df["最低"].to_numpy(float)
        vol = df["成交量"].to_numpy(float)
        dnum = df["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
        n = len(close)
        lim = limit_of(code)
        zt_thr = lim - 0.005

        prev = np.concatenate([[np.nan], close[:-1]])
        pct = close / prev - 1.0
        pct[0] = np.nan
        ret = pct.copy()

        idx = np.arange(1, n - 5)
        valid = (dnum[idx] >= start_num) \
            & (dnum[idx + 5] - dnum[idx] <= MAX_T5_GAP) \
            & (dnum[idx + 1] - dnum[idx] <= MAX_T1_GAP)
        idx = idx[valid]
        if idx.size == 0:
            continue

        r1 = close[idx + 1] / close[idx] - 1.0
        r5 = close[idx + 5] / close[idx] - 1.0
        ret_hg = np.where(pct[idx + 1] >= zt_thr, r1, r5)
        buyable = (pct[idx] < zt_thr) & np.isfinite(ret_hg)
        if not buyable.any():
            continue
        base_d.append(dnum[idx][buyable])
        base_r.append(ret_hg[buyable])

        # ---- 特征（与 build_signals 同口径，全部只用 T 日及以前数据）----
        sd10 = sh(roll_std(ret, 10), 1)
        sig = (~np.isnan(sd10[idx])) & (sd10[idx] < 0.01) & (ret[idx] > 0.03)

        # 自检：与 build_signals 的 r31_breakout 必须逐位一致（两侧都叠加 buyable 后比对）
        if checked < 400:
            ref = build_signals(close, open_, high, low, vol, lim)["r31_breakout"][idx]
            if not np.array_equal(sig & buyable, ref & buyable):
                mismatch.append(code)
            checked += 1

        sig &= buyable
        if not sig.any():
            continue
        ii = idx[sig]

        vol5_prev = pd.Series(vol).rolling(5).mean().shift(1).to_numpy(float)
        volr5_all = vol / np.where(vol5_prev > 0, vol5_prev, np.nan)
        ma20_all = roll_mean(close, 20)
        ma60_all = roll_mean(close, 60)
        ret20_all = np.full(n, np.nan)
        if n > 20:
            ret20_all[20:] = close[20:] / close[:-20] - 1.0
        is_zt = ret >= zt_thr
        gene_all = pd.Series(is_zt.astype(float)).rolling(20, min_periods=1).max().to_numpy() > 0
        hi20_all = roll_max(high, 20)
        amp_all = (high - low) / prev

        ev_parts["date"].append(dnum[ii])
        ev_parts["ret_hg"].append(ret_hg[sig])
        ev_parts["ret"].append(ret[ii])
        ev_parts["volr5"].append(volr5_all[ii])
        ev_parts["above20"].append(close[ii] > ma20_all[ii])
        ev_parts["above60"].append(close[ii] > ma60_all[ii])
        ev_parts["ret20"].append(ret20_all[ii])
        ev_parts["gene"].append(gene_all[ii])
        ev_parts["newhigh"].append(close[ii] >= hi20_all[ii] * 0.999)
        ev_parts["prevret"].append(sh(pct, 1)[ii])
        ev_parts["amp"].append(amp_all[ii])

        if fi % 800 == 0:
            print("  ...%d/%d" % (fi, len(files)), flush=True)

    base_dates = np.concatenate(base_d).astype(np.int64, copy=False)
    base_rets = np.concatenate(base_r).astype(float, copy=False)
    ev = {k: np.concatenate(v) for k, v in ev_parts.items()}
    meta = {
        "n_files": len(files),
        "read_errors": read_errors[:20],
        "n_read_errors": len(read_errors),
        "n_skipped_short": len(skipped_short),
        "signal_definition_selfcheck_files": checked,
        "signal_definition_mismatches": mismatch[:20],
    }
    return base_dates, base_rets, ev, meta


# ==================================================================== 评估
def evaluate(name, definition, dates, rets, baseline_map, market_dates, folds, quarter_index):
    """对一个变体（或裸版）算全套指标 + 判定。"""
    rec, detail = paired_metrics(dates, rets, baseline_map)
    n_days = int(rec["n_days"] or 0)
    mean_bp = rec["mean_excess_bp"]
    tstat = rec["t_excess"]

    # Purged K 折
    fold_rows = []
    pos_folds = 0
    for fold_id, fold_dates, _gap in folds:
        fr, _ = paired_metrics(dates, rets, baseline_map, allowed_dates=fold_dates)
        pos = bool(fr["mean_excess_bp"] is not None and fr["mean_excess_bp"] > 0)
        pos_folds += int(pos)
        fold_rows.append({
            "fold": fold_id,
            "start": iso_date(int(fold_dates[0])),
            "end": iso_date(int(fold_dates[-1])),
            "n_days": fr["n_days"],
            "mean_excess_bp": fr["mean_excess_bp"],
            "positive": pos,
        })

    # 季度细分
    quarters = []
    for q, q_dates in quarter_index:
        qr, _ = paired_metrics(dates, rets, baseline_map, allowed_dates=q_dates)
        quarters.append({
            "quarter": q,
            "market_days": int(len(q_dates)),
            "n_days": qr["n_days"],
            "n": qr["n"],
            "mean_excess_bp": qr["mean_excess_bp"],
            "t_excess": qr["t_excess"],
        })
    qmap = {x["quarter"]: x for x in quarters}

    # 成本
    gross = detail["excess"]
    cost_rows = []
    for c in (COST_10BP, COST_20BP):
        net = gross - 2.0 * c
        cr = series_stats(net)
        cr["single_side_cost_bp"] = c * 10000
        cr["round_trip_cost_bp"] = 2.0 * c * 10000
        cost_rows.append(cr)
    c10 = cost_rows[0]

    counts = detail["counts"].astype(float)
    cap = {
        "mean_signals_per_day": round(float(counts.mean()), 3) if len(counts) else 0.0,
        "median_signals_per_day": round(float(np.median(counts)), 2) if len(counts) else 0.0,
        "p95_signals_per_day": round(float(np.percentile(counts, 95)), 2) if len(counts) else 0.0,
        "max_signals_per_day": int(counts.max()) if len(counts) else 0,
    }

    # ---- 判定（写死，不修改）----
    c1 = bool(mean_bp is not None and mean_bp > 0)
    c2 = bool(tstat is not None and tstat >= GATE_T)
    c3 = bool(pos_folds >= GATE_POS_FOLDS)
    c4 = bool(c10["t_excess"] is not None and c10["t_excess"] >= GATE_T)
    recent = [qmap.get(q) for q in RECENT_QUARTERS]
    recent_vals = [(q, r["mean_excess_bp"]) for q, r in zip(RECENT_QUARTERS, recent)]
    c5 = bool(all(r is not None and r["mean_excess_bp"] is not None and r["mean_excess_bp"] >= 0
                  for r in recent))
    c6 = bool(n_days >= GATE_MIN_DAYS and int(rec["n"]) >= GATE_MIN_N)

    conds = {"C1_mean_excess_pos": c1, "C2_t_ge_2": c2, "C3_pos_folds_ge_4": c3,
             "C4_cost10bp_t_ge_2": c4, "C5_recent2Q_not_negative": c5, "C6_days_ge_100_n_ge_500": c6}
    failed = [k for k, v in conds.items() if not v]
    if all(conds.values()):
        verdict = "PROMOTE"
    elif c1 and c2 and c6:
        verdict = "WATCH"
    else:
        verdict = "REJECT"

    nominal_p = None
    if tstat is not None and n_days > 1:
        nominal_p = float(2.0 * student_t.sf(abs(tstat), n_days - 1))

    out = {
        "name": name,
        "definition": definition,
        "n": int(rec["n"]),
        "n_days": n_days,
        "start": rec.get("start"),
        "end": rec.get("end"),
        "mean_excess_bp": mean_bp,
        "t_excess": tstat,
        "sharpe_excess": rec["sharpe_excess"],
        "win_rate_day": rec["win_rate_day"],
        "std_excess_bp": rec["std_excess_bp"],
        "nominal_two_sided_p": None if nominal_p is None else round(nominal_p, 8),
        "purged_5fold": {
            "positive_folds": pos_folds,
            "fold_mean_excess_bp": [r["mean_excess_bp"] for r in fold_rows],
            "folds": fold_rows,
        },
        "quarters": quarters,
        "recent_two_quarters": [{"quarter": q, "mean_excess_bp": v} for q, v in recent_vals],
        "cost_10bp_single_side": {
            "mean_excess_bp": c10["mean_excess_bp"],
            "t_excess": c10["t_excess"],
            "win_rate_day": c10["win_rate_day"],
        },
        "cost_20bp_single_side": {
            "mean_excess_bp": cost_rows[1]["mean_excess_bp"],
            "t_excess": cost_rows[1]["t_excess"],
        },
        "break_even_single_side_bp": round(max(float(gross.mean()) * 10000 / 2.0, 0.0), 2) if len(gross) else 0.0,
        "capacity": cap,
        "conditions": conds,
        "failed_conditions": failed,
        "verdict": verdict,
    }
    return out


def main():
    print("[scan] 读取全市场日线 ...", flush=True)
    base_dates, base_rets, ev, meta = scan_market()
    market_dates, base_mean, base_cnt = day_agg(base_dates, base_rets)
    baseline_map = dict(zip(market_dates.tolist(), base_mean.tolist()))
    print("[scan] 基准可买子集 n=%d 交易日=%d 日均=%.4f%% | 裸版事件 n=%d"
          % (len(base_rets), len(market_dates), base_mean.mean() * 100, len(ev["date"])), flush=True)
    print("[scan] 裸版定义自检文件数=%d 不一致=%d" % (meta["signal_definition_selfcheck_files"],
                                                    len(meta["signal_definition_mismatches"])), flush=True)

    # 变体 12：当日全市场触发数 <= 20（两遍：先聚合裸版每日计数，再过滤交易日）
    sig_dates = ev["date"]
    _, _, sig_cnt = day_agg(sig_dates, ev["ret_hg"])
    ud, inv = np.unique(sig_dates, return_inverse=True)
    per_day = np.bincount(inv, minlength=len(ud))
    allowed_days = ud[per_day <= DAILY_CAP]
    ev["cap20"] = np.isin(sig_dates, allowed_days)
    cap_info = {
        "daily_cap": DAILY_CAP,
        "signal_days_total": int(len(ud)),
        "days_with_cap_le_20": int(len(allowed_days)),
        "mean_signals_per_signal_day": round(float(per_day.mean()), 3),
        "median_signals_per_signal_day": round(float(np.median(per_day)), 2),
        "max_signals_per_day": int(per_day.max()),
    }
    print("[cap] 触发日=%d 其中触发数<=20 的交易日=%d (日均触发 %.2f, 最大 %d)"
          % (cap_info["signal_days_total"], cap_info["days_with_cap_le_20"],
             cap_info["mean_signals_per_signal_day"], cap_info["max_signals_per_day"]), flush=True)

    folds, purged = make_purged_folds(market_dates, N_FOLDS, PURGE_DAYS)
    dstr = pd.to_datetime([iso_date(int(x)) for x in market_dates])
    qlab = np.asarray([str(x.to_period("Q")) for x in dstr], dtype=object)
    quarter_index = [(q, market_dates[qlab == q]) for q in dict.fromkeys(qlab.tolist())]

    variants = []
    # ---- 裸版对照基线 ----
    base_row = evaluate("r31_breakout_裸版", "sd10 < 0.01 & ret > 0.03",
                        sig_dates, ev["ret_hg"], baseline_map, market_dates, folds, quarter_index)
    base_row["is_baseline"] = True
    variants.append(base_row)

    # ---- 14 个条件化变体 ----
    for name, definition, mask in build_variant_masks(ev):
        if not mask.any():
            variants.append({"name": name, "definition": definition, "n": 0, "n_days": 0,
                             "verdict": "REJECT", "failed_conditions": ["no_events"]})
            continue
        row = evaluate(name, definition, sig_dates[mask], ev["ret_hg"][mask],
                       baseline_map, market_dates, folds, quarter_index)
        row["is_baseline"] = False
        variants.append(row)

    # ---- 多重检验 ----
    tested = [v for v in variants if not v.get("is_baseline")]
    n_tests = len(tested)
    bonf_alpha = 0.05 / n_tests if n_tests else 0.05
    scored = [v for v in tested if v.get("nominal_two_sided_p") is not None]
    best = min(scored, key=lambda v: v["nominal_two_sided_p"]) if scored else None
    best_any = min([v for v in variants if v.get("nominal_two_sided_p") is not None],
                   key=lambda v: v["nominal_two_sided_p"]) if any(
        v.get("nominal_two_sided_p") is not None for v in variants) else None
    mt = {
        "n_variants_tested": n_tests,
        "n_including_baseline": len(variants),
        "bonferroni_alpha_0.05_over_N": round(bonf_alpha, 8),
        "bonferroni_alpha_including_baseline": round(0.05 / len(variants), 8),
        "best_variant": None if best is None else {
            "name": best["name"],
            "nominal_two_sided_p": best["nominal_two_sided_p"],
            "bonferroni_adjusted_p": round(min(best["nominal_two_sided_p"] * n_tests, 1.0), 8),
            "passes_bonferroni": bool(best["nominal_two_sided_p"] <= bonf_alpha),
            "t_excess": best["t_excess"],
            "mean_excess_bp": best["mean_excess_bp"],
        },
        "best_overall_incl_baseline": None if best_any is None else {
            "name": best_any["name"],
            "nominal_two_sided_p": best_any["nominal_two_sided_p"],
            "passes_bonferroni_excl_baseline": bool(best_any["nominal_two_sided_p"] <= bonf_alpha),
            "t_excess": best_any["t_excess"],
        },
        "note": "N=14 个变体的多重检验；Bonferroni 后 p 阈值=0.05/14。",
    }

    # ---- 打印 ----
    order = sorted(variants, key=lambda v: -(v.get("mean_excess_bp") if v.get("mean_excess_bp") is not None else -1e9))
    hdr = "%-26s %6s %5s %10s %7s %7s %7s %6s %6s %9s %9s %s"
    print("\n" + "=" * 150)
    print(hdr % ("变体", "n", "天数", "日均超额bp", "t", "Sharpe", "日胜率", "正折", "10bp_t", "2026Q3", "2026Q4", "裁决"))
    print("=" * 150)
    for v in order:
        if v.get("mean_excess_bp") is None:
            print(hdr % (v["name"], v.get("n", 0), v.get("n_days", 0), "-", "-", "-", "-", "-", "-", "-", "-", v["verdict"]))
            continue
        q3 = next((x["mean_excess_bp"] for x in v["recent_two_quarters"] if x["quarter"] == "2026Q3"), None)
        q4 = next((x["mean_excess_bp"] for x in v["recent_two_quarters"] if x["quarter"] == "2026Q4"), None)
        f = lambda x: "-" if x is None else "%.1f" % x
        print(hdr % (v["name"], v["n"], v["n_days"], "%.2f" % v["mean_excess_bp"], "%.2f" % v["t_excess"],
                     "%.2f" % v["sharpe_excess"], "%.3f" % v["win_rate_day"],
                     "%d/5" % v["purged_5fold"]["positive_folds"],
                     "%.2f" % v["cost_10bp_single_side"]["t_excess"], f(q3), f(q4), v["verdict"]))
    print("=" * 150)
    for v in order:
        if v.get("failed_conditions"):
            print("  %-26s 未过: %s" % (v["name"], ", ".join(v["failed_conditions"])))
    print("\n[多重检验] N=%d  Bonferroni p阈值=%.6f  最佳变体=%s 名义p=%.6f 校正p=%.6f -> %s"
          % (mt["n_variants_tested"], mt["bonferroni_alpha_0.05_over_N"],
             mt["best_variant"]["name"], mt["best_variant"]["nominal_two_sided_p"],
             mt["best_variant"]["bonferroni_adjusted_p"],
             "过" if mt["best_variant"]["passes_bonferroni"] else "不过"))

    out = {
        "meta": {
            "script": "research/r31_variants.py",
            "test_start": TEST_START,
            "test_end": iso_date(int(market_dates[-1])),
            "market_days": int(len(market_dates)),
            "daily_files": meta["n_files"],
            "read_errors": meta["n_read_errors"],
            "skipped_short": meta["n_skipped_short"],
            "signal_definition_selfcheck": {
                "files_checked": meta["signal_definition_selfcheck_files"],
                "mismatches": meta["signal_definition_mismatches"],
                "note": "本脚本重算的裸版 r31_breakout 与 build_signals() 输出逐位比对",
            },
            "purged_folds": {"n_folds": N_FOLDS, "purge_days": PURGE_DAYS,
                             "purged_dates": [iso_date(int(x)) for x in purged]},
            "criteria": {
                "C1": "日均超额 > 0", "C2": "配对 t >= 2.0", "C3": "Purged 5 折正折数 >= 4",
                "C4": "单边 10bp 成本后 t >= 2.0", "C5": "2026Q3/2026Q4 超额均 >= 0",
                "C6": "交易日数 >= 100 且 n >= 500",
                "PROMOTE": "C1..C6 全过", "WATCH": "C1&C2&C6 过但 C3/C4/C5 部分未过",
                "REJECT": "其余",
            },
        },
        "baseline_market": {
            "n": int(len(base_rets)),
            "n_days": int(len(market_dates)),
            "mean_day_pct": round(float(base_mean.mean()) * 100, 4),
        },
        "daily_cap_filter": cap_info,
        "variants": order,
        "multiple_testing": mt,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print("\n[out] %s" % OUT_JSON)


if __name__ == "__main__":
    main()
