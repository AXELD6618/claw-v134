#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""r31_forward.py — r31_breakout 观察位的前瞻结算器
=============================================================================
职责
----
读 backtest_cache/r31_obs.jsonl（r31_watch --record 落的只追加观察账本），
对每个已有记录的交易日起算「按日配对超额」，再套 r31_watch.STOP_RULES
给出 WATCH / REJECTED / PROMOTE_CANDIDATE 裁决。

口径（唯一，勿改）
------------------
  excess_d = mean(当日 r31 信号股 trail5 收益) − mean(当日可买子集 trail5 收益)
  「可买子集」= 全市场 T 日未涨停（ret[T] < lim − 0.005）的股票
  两侧必须**同一退出口径、同一持有期**，否则超额无意义。
  trail5 = T+1 涨停则兑现，否则自最高收盘回撤 5% 离场，T+5 兜底。

为什么用配对超额而不是绝对收益
------------------------------
基准在**大跌日**组合 PF 就有 1.506、日均 +0.572%（跌后反弹）。任何"跌日买入"
类策略用绝对收益都能轻松过 1.30，等于把市场 Beta 记成 alpha。只有配对超额
才扣掉了当日市场整体涨跌，剩下的才是因子的贡献。

data 不足的日期
---------------
某个 T 日到不了 T+5（还没走完）→ 该日不参与结算（记 pending），
绝不用「已有几天」凑出半截收益 —— 那会系统性高估（回撤往往在后段）。

用法
----
  .venv/Scripts/python.exe research/r31_forward.py            # 结算并打印
  .venv/Scripts/python.exe research/r31_forward.py --json     # 只输出 JSON
"""
from __future__ import annotations

import os
import sys
import glob
import json
import argparse
import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from r31_watch import (DAILY, HOLD_MAX, LIMIT_EPS, STOP_RULES, TRAIL,  # noqa: E402
                       load_obs, r31_mask, scan, stop_verdict)
from holygrail_align_probe import limit_of  # noqa: E402


def _trail5_path(close, i, lim):
    """与 r31_watch.trail5_exit 同口径，但返回 (ret, kind) 供批量基线复用（浮点数组入参）。"""
    n = len(close)
    if i + HOLD_MAX >= n:
        return None, "PENDING"
    zt_thr = lim - LIMIT_EPS
    c0 = close[i]
    if close[i + 1] / c0 - 1.0 >= zt_thr:
        return float(close[i + 1] / c0 - 1.0), "T1_LIMITUP"
    pk, r, kind = 0.0, None, None
    for j in range(1, HOLD_MAX + 1):
        rj = float(close[i + j] / c0 - 1.0)
        pk = max(pk, rj)
        if rj <= pk - TRAIL:                     # 与 r31_watch 同一常量，防口径漂移
            r, kind = rj, "TRAIL"
            break
    if r is None:
        r, kind = float(close[i + HOLD_MAX] / c0 - 1.0), "HOLD5"
    return r, kind


def build_day_excess(dates):
    """一次遍历全市场日线，算每个目标日的信号侧与基线侧 trail5 收益。

    返回 dict{date: {"sig": [...], "base": [...]}}  —— 未走完 T+5 的日期不在结果里。
    """
    want = set(str(d) for d in dates)
    out = defaultdict(lambda: {"sig": [], "base": []})

    for fp in sorted(glob.glob(os.path.join(DAILY, "*.csv"))):
        code = os.path.splitext(os.path.basename(fp))[0]
        if len(code) != 6 or not code.isdigit():
            continue
        try:
            df = pd.read_csv(fp, dtype={"日期": str})
        except Exception:
            continue
        if not {"日期", "收盘"}.issubset(set(df.columns)) or len(df) < 12:
            continue
        dcol = df["日期"].astype(str).str.slice(0, 10).to_numpy()
        hit = np.isin(dcol, list(want))
        if not hit.any():
            continue
        try:
            close = df["收盘"].to_numpy(float)
        except Exception:
            continue
        prev = np.concatenate([[np.nan], close[:-1]])
        with np.errstate(invalid="ignore", divide="ignore"):
            ret = close / prev - 1.0
        ret[0] = np.nan

        lim = limit_of(code)
        zt_thr = lim - LIMIT_EPS
        idxs = np.where(hit)[0]
        # 该股在同一日期可能有多行（脏数据）→ 只取最后一行，与 r31_watch 一致
        seen = {}
        for i in idxs:
            seen[dcol[i]] = int(i)

        # 信号侧：复用 r31_watch 的 mask，保证口径逐字一致
        sig_mask, _sd10 = r31_mask(ret)

        for d, i in seen.items():
            r, _k = _trail5_path(close, i, lim)
            if r is None:
                continue                          # T+5 未走完 → 该日整日 pending
            if sig_mask[i] and np.isfinite(ret[i]) and ret[i] < zt_thr:
                out[d]["sig"].append(r)
            # 基线：T 日未涨停即可买（不要求信号）
            if np.isfinite(ret[i]) and ret[i] < zt_thr:
                out[d]["base"].append(r)
    return dict(out)


def settle(obs=None, verbose=True):
    """对观察账本做前瞻结算 → dict（含 verdict）。"""
    obs = obs if obs is not None else load_obs()
    if not obs:
        v = {"verdict": "WATCH", "n_days": 0, "n_records": 0,
             "reasons": ["观察账本为空（尚未实推累积）→ 继续 WATCH"],
             "n_days_needed": STOP_RULES["min_signal_days"],
             "deadline": STOP_RULES["deadline"]}
        if verbose:
            print("=" * 78)
            print("[r31_forward] 观察账本为空 → 尚无前瞻样本")
            print("=" * 78)
            print("  裁决: WATCH（需累积 %d 个信号日，或到 %s 强制结算）"
                  % (STOP_RULES["min_signal_days"], STOP_RULES["deadline"]))
            print("  下一步: 先让 selection_push 实推（严禁 --dry）落账本，再跑本脚本结算")
        return v

    dates = sorted({k[0] for k in obs})
    data = build_day_excess(dates)

    day_excess, pending = {}, []
    for d in dates:
        g = data.get(d)
        if not g or not g["sig"] or not g["base"]:
            pending.append(d)                     # T+5 未走完 或 当日无信号股
            continue
        day_excess[d] = float(np.mean(g["sig"]) - np.mean(g["base"]))

    quarters = defaultdict(float)
    for d, x in day_excess.items():
        try:
            dt = datetime.date.fromisoformat(d)
            quarters["%dQ%d" % (dt.year, (dt.month - 1) // 3 + 1)] += x
        except Exception:
            pass

    v = stop_verdict(day_excess, quarters=dict(quarters))
    v["n_records"] = len(obs)
    v["pending_dates"] = pending
    v["quarters"] = {k: round(val * 1e4, 2) for k, val in sorted(quarters.items())}

    if verbose:
        print("=" * 78)
        print("[r31_forward] 观察账本 %d 条 / %d 个交易日（已结算 %d，pending %d）"
              % (len(obs), len(dates), len(day_excess), len(pending)))
        print("=" * 78)
        if pending:
            print("  pending（T+5 未走完，不参与结算，绝不用半截收益凑）: %s" % ", ".join(pending[:12])
                  + (" ..." if len(pending) > 12 else ""))
        if day_excess:
            for d in sorted(day_excess):
                print("  %s  超额 %+8.2fbp" % (d, day_excess[d] * 1e4))
            print("-" * 78)
            print("  日均超额 %+.2fbp ｜ 配对 t=%s ｜ 结算天数 %d"
                  % (v.get("mean_excess_bp") or 0,
                     v.get("paired_t") if v.get("paired_t") is not None else "NA",
                     v.get("n_days") or 0))
        if quarters:
            print("  分季超额: " + ", ".join("%s %+.1fbp" % (k, val * 1e4)
                                            for k, val in sorted(quarters.items())))
        print("-" * 78)
        print("  裁决: %s" % v["verdict"])
        for r in v["reasons"]:
            print("    · %s" % r)
        print("  停止规则: %s" % json.dumps(STOP_RULES, ensure_ascii=False))
    return v


def main():
    ap = argparse.ArgumentParser(description="r31 观察位前瞻结算（按日配对超额）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--backfill", action="store_true",
                    help="对账本里缺失的历史交易日补扫（**仅调试用**，会污染'前瞻'定义）")
    a = ap.parse_args()
    v = settle(verbose=not a.json)
    if a.json:
        print(json.dumps(v, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
