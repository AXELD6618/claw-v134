# -*- coding: utf-8 -*-
"""
research/r31_robustness.py — r31_breakout 严格稳健性验证（只读研究）

数据源仅为 backtest_cache/daily/*.csv；唯一写出为
backtest_cache/r31_robustness.json。统计口径复用 holygrail_excess_probe.py：
每个信号日先对信号股收益等权，再减同日全市场可买子集等权收益，最后对
按日配对超额序列做 t 检验。

运行：
E:/WorkBuddy_dot_workbuddy/Claw/.venv/Scripts/python.exe \
    E:/WorkBuddy_dot_workbuddy/Claw/research/r31_robustness.py
"""
from __future__ import annotations

import glob
import json
import math
import os
import sys
from collections import deque

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT_JSON = os.path.join(PROJ, "backtest_cache", "r31_robustness.json")
TEST_START = "2024-07-01"
MAX_T5_GAP = 9
MAX_T1_GAP = 4
PURGE_DAYS = 5
N_FOLDS = 5
DISCOVERY_FACTOR_TRIALS = 35
SD_THRESHOLDS = (0.006, 0.008, 0.010, 0.012, 0.015, 0.020)
RET_THRESHOLDS = (0.020, 0.025, 0.030, 0.040, 0.050)
COSTS_SINGLE_SIDE = (0.0005, 0.0010, 0.0020)
ANCHOR_SD = 0.010
ANCHOR_RET = 0.030
REQUIRED_COLUMNS = ("日期", "开盘", "收盘", "最高", "最低", "成交量")
MIN_ROWS_R31 = 17  # 10个完整历史收益 + T日 + T+5标签所需的最小行数

sys.path.insert(0, os.path.join(PROJ, "research"))
from holygrail_align_probe import limit_of, roll_std, sh  # noqa: E402


def iso_date(day_number: int) -> str:
    return str(np.datetime64(int(day_number), "D"))


def finite_round(value, digits=4):
    if value is None or not np.isfinite(value):
        return None
    return round(float(value), digits)


def day_agg(dates: np.ndarray, returns: np.ndarray):
    unique_dates, inverse = np.unique(dates, return_inverse=True)
    counts = np.bincount(inverse, minlength=len(unique_dates)).astype(np.int64)
    sums = np.bincount(inverse, weights=returns, minlength=len(unique_dates))
    return unique_dates, sums / counts, counts


def series_stats(excess: np.ndarray) -> dict:
    values = np.asarray(excess, dtype=float)
    values = values[np.isfinite(values)]
    n = len(values)
    if n == 0:
        return {
            "n_days": 0,
            "mean_excess_bp": None,
            "t_excess": None,
            "sharpe_excess": None,
            "win_rate_day": None,
            "std_excess_bp": None,
        }
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if n > 1 else 0.0
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else None
    sharpe = mean / std * math.sqrt(252) if std > 0 else None
    return {
        "n_days": int(n),
        "mean_excess_bp": finite_round(mean * 10000, 4),
        "t_excess": finite_round(t_stat, 4),
        "sharpe_excess": finite_round(sharpe, 4),
        "win_rate_day": finite_round(float((values > 0).mean()), 4),
        "std_excess_bp": finite_round(std * 10000, 4),
    }


def paired_metrics(
    event_dates: np.ndarray,
    event_returns: np.ndarray,
    baseline_map: dict[int, float],
    allowed_dates: np.ndarray | None = None,
    with_thirds: bool = False,
):
    mask = np.isfinite(event_returns)
    if allowed_dates is not None:
        mask &= np.isin(event_dates, allowed_dates)
    dates = event_dates[mask]
    returns = event_returns[mask]
    if len(returns) == 0:
        record = {"n": 0, **series_stats(np.zeros(0))}
        if with_thirds:
            record["three_equal_signal_day_segments"] = []
        return record, {
            "dates": np.zeros(0, dtype=np.int64),
            "excess": np.zeros(0),
            "counts": np.zeros(0, dtype=np.int64),
        }

    unique_dates, signal_mean, counts = day_agg(dates, returns)
    baseline = np.array([baseline_map[int(d)] for d in unique_dates], dtype=float)
    excess = signal_mean - baseline
    record = {"n": int(len(returns)), **series_stats(excess)}
    record["start"] = iso_date(int(unique_dates[0]))
    record["end"] = iso_date(int(unique_dates[-1]))

    if with_thirds:
        thirds = []
        for segment_id, idx in enumerate(np.array_split(np.arange(len(unique_dates)), 3), start=1):
            if len(idx) == 0:
                continue
            segment_stats = series_stats(excess[idx])
            segment_stats.update(
                {
                    "segment": segment_id,
                    "start": iso_date(int(unique_dates[idx[0]])),
                    "end": iso_date(int(unique_dates[idx[-1]])),
                    "n": int(counts[idx].sum()),
                }
            )
            thirds.append(segment_stats)
        record["three_equal_signal_day_segments"] = thirds

    return record, {"dates": unique_dates, "excess": excess, "counts": counts}


def make_purged_folds(market_dates: np.ndarray, n_folds=5, purge_days=5):
    """生成互不标签重叠的连续评估折；相邻评估折之间丢弃 purge_days 个交易日。"""
    dates = np.asarray(market_dates, dtype=np.int64)
    usable = len(dates) - purge_days * (n_folds - 1)
    if usable < n_folds:
        raise ValueError("交易日不足以生成带 purge 的时间折")
    base, extra = divmod(usable, n_folds)
    sizes = [base + (1 if i < extra else 0) for i in range(n_folds)]
    folds = []
    purged = []
    cursor = 0
    for fold_id, size in enumerate(sizes, start=1):
        test_dates = dates[cursor : cursor + size]
        cursor += size
        gap = np.zeros(0, dtype=np.int64)
        if fold_id < n_folds:
            gap = dates[cursor : cursor + purge_days]
            cursor += purge_days
            purged.extend(gap.tolist())
        folds.append((fold_id, test_dates, gap))
    if cursor != len(dates):
        raise AssertionError("Purged fold 未完整消费交易日序列")
    return folds, np.asarray(purged, dtype=np.int64)


def contiguous_good_component(grid_lookup: dict, sd_values: tuple, ret_values: tuple):
    anchor = (sd_values.index(ANCHOR_SD), ret_values.index(ANCHOR_RET))

    def is_good(i, j):
        row = grid_lookup[(sd_values[i], ret_values[j])]
        return (
            row["mean_excess_bp"] is not None
            and row["mean_excess_bp"] > 0
            and row["t_excess"] is not None
            and row["t_excess"] >= 2.0
        )

    if not is_good(*anchor):
        return []
    seen = {anchor}
    queue = deque([anchor])
    while queue:
        i, j = queue.popleft()
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ni, nj = i + di, j + dj
                if not (0 <= ni < len(sd_values) and 0 <= nj < len(ret_values)):
                    continue
                if (ni, nj) not in seen and is_good(ni, nj):
                    seen.add((ni, nj))
                    queue.append((ni, nj))
    return sorted(seen)


def build_lookahead_audit() -> dict:
    probe = np.array([1.0, 2.0, 3.0, 4.0])
    shifted = sh(probe, 1)
    shift_ok = bool(
        np.isnan(shifted[0])
        and np.array_equal(shifted[1:], np.array([1.0, 2.0, 3.0]))
        and shifted[-1] != probe[-1]
    )
    checks = [
        {
            "input": "ret[T]",
            "definition": "close[T] / close[T-1] - 1",
            "latest_information": "T日收盘",
            "known_at_T_close": True,
            "conclusion": "用于当日涨幅>阈值与T日是否涨停；不引用T+1。",
        },
        {
            "input": "roll_std(ret, 10) 后 sh(..., 1)",
            "window": "sd10[T] = std(ret[T-10], ..., ret[T-1])，共10个收益",
            "latest_information": "T-1日收盘",
            "known_at_T_close": True,
            "conclusion": "rolling在T-1处的窗口经下移1格对齐到T，明确排除ret[T]。",
        },
        {
            "input": "sh()",
            "definition": "out[k:] = a[:-k]，头部填NaN/False",
            "known_at_T_close": True,
            "conclusion": "不使用np.roll，尾部不会被数组头部环绕污染。",
        },
        {
            "input": "涨停阈值",
            "definition": "300/301/688/689为20%，8/4/920为30%，其余10%；判定lim-0.005",
            "latest_information": "证券代码和T日收盘收益",
            "known_at_T_close": True,
            "conclusion": "BUY仅要求T日未涨停，与原探针一致。",
        },
        {
            "input": "r1/r5退出收益",
            "definition": "若ret[T+1]>=lim-0.005则close[T+1]/close[T]-1，否则close[T+5]/close[T]-1",
            "latest_information": "分别在T+1或T+5退出时获得",
            "known_at_T_close": False,
            "conclusion": "只作为未来实现收益标签，不参与T日信号；路径规则可按T+1收盘状态执行。",
        },
    ]
    return {
        "checks": checks,
        "shift_probe": {
            "input": probe.tolist(),
            "shift_1_output": [None if np.isnan(x) else float(x) for x in shifted],
            "no_wrap_passed": shift_ok,
        },
        "forward_looking_bias_found": False,
        "conclusion": "未发现信号前视：T日信号只使用T日及以前数据；T+1/T+5仅定义随后兑现收益。",
        "execution_caveat": "在T日最终收盘价确认ret与涨停状态后又按同一收盘价成交，是尾盘/收盘撮合的理想化假设；这属于执行可实现性风险，不是数据前视。",
    }


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    start_num = int(np.datetime64(TEST_START, "D").astype(np.int64))
    base_dates_parts = []
    base_returns_parts = []
    candidate_dates_parts = []
    candidate_returns_parts = []
    candidate_sd_parts = []
    candidate_ret_parts = []
    read_errors = []
    skipped_short = []
    recovered_headerless = []
    raw_start_dates = []
    raw_end_dates = []
    loaded = 0

    for file_index, file_path in enumerate(files, start=1):
        code = os.path.splitext(os.path.basename(file_path))[0]
        try:
            frame = pd.read_csv(file_path, dtype={"日期": str})
            if not set(REQUIRED_COLUMNS).issubset(frame.columns):
                if frame.shape[1] != len(REQUIRED_COLUMNS):
                    raise ValueError(f"列数异常: {frame.shape[1]}")
                frame = pd.read_csv(
                    file_path,
                    header=None,
                    names=REQUIRED_COLUMNS,
                    dtype={"日期": str},
                )
                recovered_headerless.append(code)
            frame = frame.sort_values("日期").reset_index(drop=True)
            if len(frame) < MIN_ROWS_R31:
                skipped_short.append(code)
                continue
            close = frame["收盘"].to_numpy(float)
            open_ = frame["开盘"].to_numpy(float)
            high = frame["最高"].to_numpy(float)
            low = frame["最低"].to_numpy(float)
            volume = frame["成交量"].to_numpy(float)
            del open_, high, low, volume  # 本因子与收益口径不使用这些列，但读取以验证列存在。
            dates = frame["日期"].to_numpy().astype("datetime64[D]").astype(np.int64)
            raw_start_dates.append(int(dates[0]))
            raw_end_dates.append(int(dates[-1]))
        except Exception as exc:
            read_errors.append({"file": os.path.basename(file_path), "error": str(exc)})
            continue

        n = len(close)
        indices = np.arange(1, n - 5)
        valid = (
            (dates[indices] >= start_num)
            & (dates[indices + 5] - dates[indices] <= MAX_T5_GAP)
            & (dates[indices + 1] - dates[indices] <= MAX_T1_GAP)
        )
        indices = indices[valid]
        if indices.size == 0:
            loaded += 1
            continue

        previous = np.concatenate([[np.nan], close[:-1]])
        daily_ret = close / previous - 1.0
        daily_ret[0] = np.nan
        limit_threshold = limit_of(code) - 0.005
        r1 = close[indices + 1] / close[indices] - 1.0
        r5 = close[indices + 5] / close[indices] - 1.0
        realized = np.where(daily_ret[indices + 1] >= limit_threshold, r1, r5)
        buyable = (daily_ret[indices] < limit_threshold) & np.isfinite(realized)

        base_dates_parts.append(dates[indices][buyable])
        base_returns_parts.append(realized[buyable])

        sd10 = sh(roll_std(daily_ret, 10), 1)
        broad = (
            buyable
            & np.isfinite(sd10[indices])
            & (sd10[indices] < max(SD_THRESHOLDS))
            & (daily_ret[indices] > min(RET_THRESHOLDS))
        )
        if broad.any():
            candidate_dates_parts.append(dates[indices][broad])
            candidate_returns_parts.append(realized[broad])
            candidate_sd_parts.append(sd10[indices][broad])
            candidate_ret_parts.append(daily_ret[indices][broad])
        loaded += 1
        if file_index % 800 == 0:
            print(f"  ...{file_index}/{len(files)}", flush=True)

    if not base_dates_parts:
        raise RuntimeError("没有可用的全市场基准样本")
    base_dates = np.concatenate(base_dates_parts).astype(np.int64, copy=False)
    base_returns = np.concatenate(base_returns_parts).astype(float, copy=False)
    candidate_dates = np.concatenate(candidate_dates_parts).astype(np.int64, copy=False)
    candidate_returns = np.concatenate(candidate_returns_parts).astype(float, copy=False)
    candidate_sd = np.concatenate(candidate_sd_parts).astype(float, copy=False)
    candidate_ret = np.concatenate(candidate_ret_parts).astype(float, copy=False)

    market_dates, baseline_daily, baseline_counts = day_agg(base_dates, base_returns)
    baseline_map = dict(zip(market_dates.tolist(), baseline_daily.tolist()))

    grid_rows = []
    grid_lookup = {}
    anchor_mask = None
    anchor_record = None
    anchor_detail = None
    for sd_threshold in SD_THRESHOLDS:
        for ret_threshold in RET_THRESHOLDS:
            mask = (candidate_sd < sd_threshold) & (candidate_ret > ret_threshold)
            record, detail = paired_metrics(
                candidate_dates[mask],
                candidate_returns[mask],
                baseline_map,
                with_thirds=True,
            )
            record.update(
                {
                    "sd10_threshold": sd_threshold,
                    "ret_threshold": ret_threshold,
                    "significant_positive": bool(
                        record["mean_excess_bp"] is not None
                        and record["mean_excess_bp"] > 0
                        and record["t_excess"] is not None
                        and record["t_excess"] >= 2.0
                    ),
                }
            )
            grid_rows.append(record)
            grid_lookup[(sd_threshold, ret_threshold)] = record
            if sd_threshold == ANCHOR_SD and ret_threshold == ANCHOR_RET:
                anchor_mask = mask
                anchor_record = record
                anchor_detail = detail

    if anchor_mask is None or anchor_record is None or anchor_detail is None:
        raise AssertionError("未找到基准参数格")

    sd_values = tuple(SD_THRESHOLDS)
    ret_values = tuple(RET_THRESHOLDS)
    anchor_i = sd_values.index(ANCHOR_SD)
    anchor_j = ret_values.index(ANCHOR_RET)
    adjacent = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == 0 and dj == 0:
                continue
            ni, nj = anchor_i + di, anchor_j + dj
            if 0 <= ni < len(sd_values) and 0 <= nj < len(ret_values):
                adjacent.append(grid_lookup[(sd_values[ni], ret_values[nj])])
    component = contiguous_good_component(grid_lookup, sd_values, ret_values)
    component_cells = [
        {"sd10_threshold": sd_values[i], "ret_threshold": ret_values[j]} for i, j in component
    ]
    grid_assessment = {
        "positive_cells": int(sum((r["mean_excess_bp"] or 0) > 0 for r in grid_rows)),
        "significant_positive_cells": int(sum(r["significant_positive"] for r in grid_rows)),
        "adjacent_cells": len(adjacent),
        "adjacent_positive_cells": int(sum((r["mean_excess_bp"] or 0) > 0 for r in adjacent)),
        "adjacent_significant_positive_cells": int(sum(r["significant_positive"] for r in adjacent)),
        "anchor_is_island": bool(
            anchor_record["significant_positive"]
            and not any(r["significant_positive"] for r in adjacent)
        ),
        "anchor_good_component": component_cells,
        "anchor_good_component_size": len(component_cells),
        "plateau_rule": "锚点相邻8格中至少4格为正且t>=2，视为局部高原；0格则视为孤岛。",
        "local_plateau": bool(sum(r["significant_positive"] for r in adjacent) >= 4),
    }
    if component_cells:
        grid_assessment["anchor_component_range"] = {
            "sd10_min": min(x["sd10_threshold"] for x in component_cells),
            "sd10_max": max(x["sd10_threshold"] for x in component_cells),
            "ret_min": min(x["ret_threshold"] for x in component_cells),
            "ret_max": max(x["ret_threshold"] for x in component_cells),
        }

    folds, purged_dates = make_purged_folds(market_dates, N_FOLDS, PURGE_DAYS)
    fold_rows = []
    for fold_id, fold_dates, gap_after in folds:
        fold_record, _ = paired_metrics(
            candidate_dates[anchor_mask],
            candidate_returns[anchor_mask],
            baseline_map,
            allowed_dates=fold_dates,
        )
        fold_record.update(
            {
                "fold": fold_id,
                "market_start": iso_date(int(fold_dates[0])),
                "market_end": iso_date(int(fold_dates[-1])),
                "market_days": int(len(fold_dates)),
                "purge_after": [iso_date(int(x)) for x in gap_after],
                "positive": bool(
                    fold_record["mean_excess_bp"] is not None
                    and fold_record["mean_excess_bp"] > 0
                ),
            }
        )
        fold_rows.append(fold_record)
    fold_means = np.array(
        [r["mean_excess_bp"] for r in fold_rows if r["mean_excess_bp"] is not None], dtype=float
    )
    purged_cv = {
        "n_folds": N_FOLDS,
        "purge_trading_days_between_folds": PURGE_DAYS,
        "purged_dates": [iso_date(int(x)) for x in purged_dates],
        "n_purged_dates": int(len(purged_dates)),
        "folds": fold_rows,
        "positive_folds": int(sum(r["positive"] for r in fold_rows)),
        "fold_mean_excess_bp_mean": finite_round(fold_means.mean(), 4),
        "fold_mean_excess_bp_std": finite_round(
            fold_means.std(ddof=1) if len(fold_means) > 1 else 0.0, 4
        ),
        "interpretation_rule": "1折为正=不可信；至少4折为正=稳健；2至3折=混合证据。",
    }

    date_strings = pd.to_datetime(
        [iso_date(int(x)) for x in market_dates]
    )
    quarter_labels = np.asarray([str(x.to_period("Q")) for x in date_strings], dtype=object)
    quarters = []
    for quarter in dict.fromkeys(quarter_labels.tolist()):
        quarter_dates = market_dates[quarter_labels == quarter]
        quarter_record, quarter_detail = paired_metrics(
            candidate_dates[anchor_mask],
            candidate_returns[anchor_mask],
            baseline_map,
            allowed_dates=quarter_dates,
        )
        quarter_record.update(
            {
                "quarter": quarter,
                "market_start": iso_date(int(quarter_dates[0])),
                "market_end": iso_date(int(quarter_dates[-1])),
                "market_days": int(len(quarter_dates)),
                "is_partial_quarter": bool(
                    quarter == quarter_labels[-1]
                    and pd.Timestamp(iso_date(int(quarter_dates[-1])))
                    < pd.Period(quarter, freq="Q").end_time.normalize()
                ),
                "sum_excess_bp": finite_round(quarter_detail["excess"].sum() * 10000, 4),
            }
        )
        quarters.append(quarter_record)
    valid_quarters = [q for q in quarters if q["mean_excess_bp"] is not None]
    positive_contribution = sum(max(q["sum_excess_bp"], 0.0) for q in valid_quarters)
    ranked_positive = sorted(
        [max(q["sum_excess_bp"], 0.0) for q in valid_quarters], reverse=True
    )
    top_two_share = (
        sum(ranked_positive[:2]) / positive_contribution if positive_contribution > 0 else None
    )
    means_in_time = [q["mean_excess_bp"] for q in valid_quarters]
    monotonic_decrease = bool(
        len(means_in_time) >= 2
        and all(b <= a for a, b in zip(means_in_time, means_in_time[1:]))
    )
    time_slices = {
        "method": "按513个基准交易日的自然季度切分；季度内仍按信号日配对超额统计。",
        "quarters": quarters,
        "recent_two_quarters": quarters[-2:],
        "strictly_monotonic_decrease": monotonic_decrease,
        "top_two_positive_contribution_share": finite_round(top_two_share, 4),
        "pattern": (
            "strict_monotone_decay"
            if monotonic_decrease
            else "concentrated_market_windows"
            if top_two_share is not None and top_two_share >= 0.60
            else "mixed_by_quarter"
        ),
    }

    gross_excess = anchor_detail["excess"]
    costs = []
    for single_side_cost in COSTS_SINGLE_SIDE:
        net_excess = gross_excess - 2.0 * single_side_cost
        cost_record = series_stats(net_excess)
        cost_record.update(
            {
                "single_side_cost_pct": single_side_cost * 100,
                "single_side_cost_bp": single_side_cost * 10000,
                "round_trip_cost_bp": 2.0 * single_side_cost * 10000,
            }
        )
        costs.append(cost_record)

    active_counts = anchor_detail["counts"].astype(float)
    count_map = dict(zip(anchor_detail["dates"].tolist(), active_counts.tolist()))
    all_day_counts = np.array([count_map.get(int(d), 0.0) for d in market_dates], dtype=float)
    capacity = {
        "all_eligible_market_days": {
            "n_days": int(len(all_day_counts)),
            "mean_signals_per_day": finite_round(all_day_counts.mean(), 4),
            "min_signals_per_day": int(all_day_counts.min()),
            "max_signals_per_day": int(all_day_counts.max()),
            "median_signals_per_day": finite_round(np.median(all_day_counts), 2),
            "p95_signals_per_day": finite_round(np.percentile(all_day_counts, 95), 2),
        },
        "active_signal_days_only": {
            "n_days": int(len(active_counts)),
            "mean_signals_per_day": finite_round(active_counts.mean(), 4),
            "min_signals_per_day": int(active_counts.min()),
            "max_signals_per_day": int(active_counts.max()),
            "median_signals_per_day": finite_round(np.median(active_counts), 2),
            "p95_signals_per_day": finite_round(np.percentile(active_counts, 95), 2),
        },
    }
    transaction_costs = {
        "assumption": "仅在可交易的信号组合腿扣除买入+卖出成本；全市场机会集基准不视为实际交易组合，因此不扣成本。",
        "gross": series_stats(gross_excess),
        "scenarios": costs,
        "break_even_single_side_bp": finite_round(
            max(float(gross_excess.mean()) * 10000 / 2.0, 0.0), 4
        ),
        "break_even_round_trip_bp": finite_round(
            max(float(gross_excess.mean()) * 10000, 0.0), 4
        ),
        "signal_frequency": capacity,
    }

    lookahead_audit = build_lookahead_audit()
    nominal_p_two_sided = float(
        2.0 * student_t.sf(abs(anchor_record["t_excess"]), anchor_record["n_days"] - 1)
    )
    multiple_testing = {
        "discovery_factor_trials": DISCOVERY_FACTOR_TRIALS,
        "nominal_two_sided_p": finite_round(nominal_p_two_sided, 8),
        "bonferroni_alpha": finite_round(0.05 / DISCOVERY_FACTOR_TRIALS, 8),
        "bonferroni_adjusted_p": finite_round(
            min(nominal_p_two_sided * DISCOVERY_FACTOR_TRIALS, 1.0), 8
        ),
        "passes_bonferroni_5pct": bool(
            nominal_p_two_sided <= 0.05 / DISCOVERY_FACTOR_TRIALS
        ),
        "note": "r31_breakout是35因子OOS筛选后留下的候选；该项是选择偏差背景，不替代五项稳健性检验。",
    }
    recent_positive = sum(
        q["mean_excess_bp"] is not None and q["mean_excess_bp"] > 0
        for q in time_slices["recent_two_quarters"]
    )
    cost_20bp = next(x for x in costs if x["single_side_cost_bp"] == 20.0)
    anchor_significant = bool(anchor_record["significant_positive"])
    reject_reasons = []
    if lookahead_audit["forward_looking_bias_found"]:
        reject_reasons.append("存在前视")
    if not anchor_significant:
        reject_reasons.append("锚点参数不再显著为正")
    if purged_cv["positive_folds"] <= 1:
        reject_reasons.append("Purged 5折仅至多1折为正")
    if recent_positive == 0:
        reject_reasons.append("最近两个季度均非正超额")

    promote_conditions = {
        "no_lookahead": not lookahead_audit["forward_looking_bias_found"],
        "anchor_significant_positive": anchor_significant,
        "local_parameter_plateau": grid_assessment["local_plateau"],
        "at_least_4_positive_folds": purged_cv["positive_folds"] >= 4,
        "recent_two_quarters_both_positive": recent_positive == 2,
        "t_after_20bp_single_side_cost_at_least_2": bool(
            cost_20bp["t_excess"] is not None and cost_20bp["t_excess"] >= 2.0
        ),
        "passes_bonferroni_for_35_discovery_trials": multiple_testing[
            "passes_bonferroni_5pct"
        ],
    }
    if reject_reasons:
        verdict = "REJECT"
        verdict_reason = "；".join(reject_reasons)
    elif all(promote_conditions.values()):
        verdict = "PROMOTE"
        verdict_reason = "参数、时间折、近期和成本检验全部达到预设稳健条件。"
    else:
        verdict = "WATCH"
        verdict_reason = "锚点仍有正超额且未触发硬拒绝，但未同时通过全部PROMOTE稳健条件。"

    output = {
        "script": "research/r31_robustness.py",
        "generated_from": "backtest_cache/daily/*.csv",
        "test_start": TEST_START,
        "data": {
            "files_discovered": len(files),
            "files_loaded": loaded,
            "minimum_rows_required": MIN_ROWS_R31,
            "headerless_files_recovered": len(recovered_headerless),
            "headerless_codes": recovered_headerless,
            "files_skipped_short": len(skipped_short),
            "skipped_short_codes": skipped_short,
            "read_error_count": len(read_errors),
            "read_errors": read_errors,
            "baseline_n": int(len(base_returns)),
            "baseline_market_days": int(len(market_dates)),
            "raw_data_start": iso_date(min(raw_start_dates)),
            "raw_data_end": iso_date(max(raw_end_dates)),
            "baseline_start": iso_date(int(market_dates[0])),
            "baseline_end": iso_date(int(market_dates[-1])),
            "baseline_end_note": "最后可入场日早于原始数据末日5个交易行，因为收益标签需要T+5收盘。",
            "baseline_mean_day_pct": finite_round(baseline_daily.mean() * 100, 4),
            "baseline_mean_cross_section_size": finite_round(baseline_counts.mean(), 2),
        },
        "factor": {
            "name": "r31_breakout",
            "definition": "sd10[T] < 0.010 and ret[T] > 0.030",
            "anchor": anchor_record,
        },
        "parameter_sensitivity": {
            "sd10_thresholds": list(SD_THRESHOLDS),
            "ret_thresholds": list(RET_THRESHOLDS),
            "grid": grid_rows,
            "assessment": grid_assessment,
        },
        "purged_kfold": purged_cv,
        "time_slices": time_slices,
        "lookahead_audit": lookahead_audit,
        "transaction_costs_and_capacity": transaction_costs,
        "multiple_testing_context": multiple_testing,
        "decision": {
            "verdict": verdict,
            "reason": verdict_reason,
            "reject_reasons": reject_reasons,
            "promote_conditions": promote_conditions,
            "rules": {
                "REJECT": "存在前视、锚点失去显著正超额、5折至多1折为正、或最近两季度均非正，任一触发。",
                "PROMOTE": "无前视、锚点显著、参数局部高原、至少4折为正、最近两季度均正、单边20bp成本后t>=2、且通过35次发现检验的Bonferroni校正，全部满足。",
                "WATCH": "未触发REJECT，但未满足全部PROMOTE条件。",
            },
        },
    }

    with open(OUT_JSON, "w", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2, allow_nan=False)

    print("=" * 92)
    print(
        "[anchor] n=%d days=%d excess=%.2fbp t=%.2f folds+=%d/5 verdict=%s"
        % (
            anchor_record["n"],
            anchor_record["n_days"],
            anchor_record["mean_excess_bp"],
            anchor_record["t_excess"],
            purged_cv["positive_folds"],
            verdict,
        )
    )
    print(f"[out] {OUT_JSON}")


if __name__ == "__main__":
    main()
