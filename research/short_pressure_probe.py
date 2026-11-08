#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""short_pressure_probe.py — 卖压预警(short_pressure_watch) OOS 前检定
=============================================================================
验证前提：若"高卖压"股票的前向收益显著低于"低卖压"股票，则把卖压作为
**规避/惩罚**信号是真实 edge，可进解释层并据 paired-excess 门裁定是否升 WATCH→PROMOTE；
反之则维持纯展示，不进 buy_score。

口径（与 r31_forward 唯一一致）
-------------------------------
  对每交易日 T：可买子集 = T 日未涨停(ret<T < lim-eps)的全市场股票。
  按 short_pressure 分高/低两组，分别等权算当日前向收益(T→T+5)。
    excess_d(T) = mean(LOW_returns) − mean(HIGH_returns)     # >0 表示高卖压确实更亏
  再对 excess_d 序列做 t 检验（Newey-West 校正，防日间自相关）。
  另做交割周/非交割周分层，检验"月度交割放大抛压"假说。

特征来源（PIT，T+1 可用）
  - 解禁供给：intel_pool_lift.csv.gz（fr>=5% 且解禁日∈[T, T+90d]）
  - 机构净卖：intel_features.csv.gz（最近龙虎榜 inst_net_cap<0，前向填充至 T）
  - 大宗变现：intel_pool_events.csv.gz（src=='blk'，近90日 notional/流通市值>=3% → 高卖压）
  - 交割周：expiry_calendar.is_expiry_week(T)（市场级）

注：转融通/融券余额/公募出借/量化融券做空/中证金融股东结构**无本地数据**，
本探针不纳入，仅在结论中说明其仅能作解释文字。减持预披露/股权质押亦无数据→仅解释层。

用法
----
  .venv/Scripts/python.exe research/short_pressure_probe.py
  .venv/Scripts/python.exe research/short_pressure_probe.py --json
"""
from __future__ import annotations

import os
import sys
import glob
import json
import argparse
import datetime as _dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

INTEL = os.path.join(ROOT, "backtest_cache", "intel")
DAILY = os.path.join(ROOT, "backtest_cache", "daily")

from expiry_calendar import is_expiry_week, trading_days  # noqa: E402
from holygrail_align_probe import limit_of  # noqa: E402
from short_pressure import (  # noqa: E402
    UNLOCK_HORIZON_DAYS, INST_NETCAP_FULL,
    BLOCK_HORIZON_DAYS, BLOCK_RATIO_FULL,
)

LIMIT_EPS = 0.005
WIN = ("2024-07-01", "2026-10-28")   # 与其他 OOS 测试同窗口


def _ord(d):
    return int(str(d)[:10].replace("-", ""))


def _nw_t(x):
    """Newey-West 校正 t（单边，x 为日度超额序列）。"""
    x = np.asarray(x, dtype=float)
    n = x.size
    if n < 5:
        return None
    x = x - x.mean()
    q = int(4 * (n / 100.0) ** 0.25)
    q = min(q, n - 1)
    gamma0 = float(np.dot(x, x)) / n
    var = gamma0
    for lag in range(1, q + 1):
        g = float(np.dot(x[lag:], x[:-lag])) / n
        var += 2.0 * (1.0 - lag / (q + 1.0)) * g
    if var <= 0:
        return None
    se = (var / n) ** 0.5
    if se == 0:
        return None
    return float(x.mean() / se)   # x 已被去中心化，均值=0 → 这里用原序列均值
    # 注：上面 x 已减均值，t 应基于原序列均值/se；重写如下：
    # (见下方调用处用 raw mean)


def nw_t_from_raw(raw):
    raw = np.asarray(raw, dtype=float)
    n = raw.size
    if n < 5:
        return None
    x = raw - raw.mean()
    q = int(4 * (n / 100.0) ** 0.25)
    q = min(q, n - 1)
    gamma0 = float(np.dot(x, x)) / n
    var = gamma0
    for lag in range(1, q + 1):
        g = float(np.dot(x[lag:], x[:-lag])) / n
        var += 2.0 * (1.0 - lag / (q + 1.0)) * g
    if var <= 0:
        return None
    se = (var / n) ** 0.5
    if se == 0:
        return None
    return float(raw.mean() / se)


def build_signal_maps():
    """返回 lift_map / lhb_map（按 code 预计算，供每日文件快速查表）。"""
    lift_map = {}
    fp = os.path.join(INTEL, "intel_pool_lift.csv.gz")
    if os.path.exists(fp):
        lf = pd.read_csv(fp, dtype={"code": str, "d": str})
        lf["d"] = lf["d"].astype(str).str.slice(0, 10)
        lf["dord"] = lf["d"].map(_ord)
        lf["fr"] = pd.to_numeric(lf["fr"], errors="coerce").fillna(0.0)
        for code, g in lf.groupby("code"):
            g = g.sort_values("dord")
            lift_map[code.zfill(6)] = (g["dord"].to_numpy(), g["fr"].to_numpy())

    lhb_map = {}
    fp = os.path.join(INTEL, "intel_features.csv.gz")
    if os.path.exists(fp):
        ef = pd.read_csv(fp, dtype={"code": str, "date": str})
        ef["date"] = ef["date"].astype(str).str.slice(0, 10)
        ef["dord"] = ef["date"].map(_ord)
        ef["inc"] = pd.to_numeric(ef["inst_net_cap"], errors="coerce").fillna(0.0)
        for code, g in ef.groupby("code"):
            g = g.sort_values("dord")
            lhb_map[code.zfill(6)] = (g["dord"].to_numpy(), g["inc"].to_numpy())
    return lift_map, lhb_map


def build_blk_map():
    """大宗交易表 → 每 code: (dord[], notional[], pref[])。另返 float_shares 字典。
    pref 为 notional 前缀和，供按日窗口快速累加（避免 O(days×events)）。"""
    blk_map = {}
    fl = {}
    fp = os.path.join(INTEL, "intel_pool_events.csv.gz")
    ff = os.path.join(ROOT, "backtest_cache", "float_shares.csv")
    if os.path.exists(fp):
        ev = pd.read_csv(fp, dtype={"code": str, "src": str, "d": str})
        ev = ev[ev["src"] == "blk"]
        ev["d"] = ev["d"].astype(str).str.slice(0, 10)
        ev["dord"] = ev["d"].map(_ord)
        ev["notional"] = pd.to_numeric(ev["notional"], errors="coerce").fillna(0.0)
        for code, g in ev.groupby("code"):
            g = g.sort_values("dord")
            dord = g["dord"].to_numpy()
            nt = g["notional"].to_numpy(dtype=float)
            pref = np.concatenate([[0.0], np.cumsum(nt)])
            blk_map[code.zfill(6)] = (dord, nt, pref)
    if os.path.exists(ff):
        import csv as _csv
        with open(ff, encoding="utf-8") as f:
            r = _csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) >= 2:
                    try:
                        fl[str(row[0]).strip().zfill(6)] = float(row[1])
                    except Exception:
                        pass
    return blk_map, fl


def blk_active_for(td_ord, close, blk_entry, float_shares):
    """逐交易日判定：近 BLOCK_HORIZON_DAYS 日大宗成交占流通市值比 >= BLOCK_RATIO_FULL。
    float_shares 为静态流通股本(股)，cap = float_shares × close[i]（历史浮值略高估→保守）。
    返回 bool 数组。"""
    if blk_entry is None or float_shares is None or float_shares <= 0:
        return np.zeros(len(td_ord), dtype=bool)
    dord, nt, pref = blk_entry
    if len(dord) == 0:
        return np.zeros(len(td_ord), dtype=bool)
    out = np.zeros(len(td_ord), dtype=bool)
    for i in range(len(td_ord)):
        d0 = td_ord[i]
        try:
            y, m, day = d0 // 10000, (d0 % 10000) // 100, d0 % 100
            lo_dt = _dt.date(y, m, day) - _dt.timedelta(days=BLOCK_HORIZON_DAYS)
            lo_ord = _ord(lo_dt.isoformat())
        except Exception:
            lo_ord = d0 - BLOCK_HORIZON_DAYS
        end_idx = np.searchsorted(dord, d0, side="right")
        lo_idx = np.searchsorted(dord, lo_ord, side="left")
        s = pref[end_idx] - pref[lo_idx]
        cap = float_shares * close[i]
        if cap > 0 and s / cap >= BLOCK_RATIO_FULL:
            out[i] = True
    return out


def unlock_active_for(td_ord, lift_entry):
    """td_ord: 该 code 交易日 ordinal 数组; lift_entry: (dord[], fr[])。返回 bool 数组。"""
    if lift_entry is None:
        return np.zeros(len(td_ord), dtype=bool)
    dord, fr = lift_entry
    out = np.zeros(len(td_ord), dtype=bool)
    hlim = _ord(WIN[1])
    for d, f in zip(dord, fr):
        if f < 0.05:
            continue
        lo = d - UNLOCK_HORIZON_DAYS * 1  # 用日历日近似：解禁日前 90 天
        # 转 ordinal 近似（每月~30天）
        lo_ord = d - UNLOCK_HORIZON_DAYS * 1
        # 用精确日历：d 已是 YYYYMMDD，减 90 天需日期运算
        y, m, day = d // 10000, (d % 10000) // 100, d % 100
        try:
            lo_dt = _dt.date(y, m, day) - _dt.timedelta(days=UNLOCK_HORIZON_DAYS)
            lo_ord = _ord(lo_dt.isoformat())
        except Exception:
            lo_ord = d - UNLOCK_HORIZON_DAYS
        li = np.searchsorted(td_ord, lo_ord, side="left")
        hi = np.searchsorted(td_ord, d, side="right")
        if hi > li:
            out[li:hi] = True
    return out


def inst_sell_active_for(td_ord, lhb_entry):
    if lhb_entry is None:
        return np.zeros(len(td_ord), dtype=bool)
    lhb_dord, inc = lhb_entry
    if len(lhb_dord) == 0:
        return np.zeros(len(td_ord), dtype=bool)
    idx = np.searchsorted(lhb_dord, td_ord, side="right") - 1
    valid = idx >= 0
    cur = np.where(valid, inc[np.clip(idx, 0, len(inc) - 1)], 0.0)
    return (cur < 0) & valid


def run(verbose=True):
    lift_map, lhb_map = build_signal_maps()
    blk_map, fl = build_blk_map()
    low = {}    # date -> [returns]  (低卖压)
    high = {}   # date -> [returns]  (高卖压: 解禁∪机构净卖∪大宗)
    blow = {}   # date -> [returns]  (无大宗)
    bhigh = {}  # date -> [returns]  (大宗变现单独隔离)
    n_codes = 0
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
        dord = np.array([_ord(d) for d in dcol])
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
        n = len(close)
        # T→T+5 前向收益
        fwd = np.full(n, np.nan)
        ok5 = (np.arange(n) + 5) < n
        fwd[ok5] = close[np.arange(n)[ok5] + 5] / close[np.arange(n)[ok5]] - 1.0
        buyable = np.isfinite(ret) & (ret < zt_thr)
        # 卖压分类
        ua = unlock_active_for(dord, lift_map.get(code))
        ia = inst_sell_active_for(dord, lhb_map.get(code))
        ba = blk_active_for(dord, close, blk_map.get(code), fl.get(code))
        # 主信号只用已验证的 解禁∪机构净卖（大宗 OOS REJECT，不进规避信号）
        hi_p = ua | ia
        exp_w = np.array([is_expiry_week(d) for d in dcol])

        for i in range(n):
            if not (ok5[i] and buyable[i] and dord[i] >= _ord(WIN[0]) and dord[i] <= _ord(WIN[1])):
                continue
            d = dcol[i]
            r = fwd[i]
            if not np.isfinite(r):
                continue
            if hi_p[i]:
                high.setdefault(d, []).append(r)
            else:
                low.setdefault(d, []).append(r)
            # 大宗单独隔离
            if ba[i]:
                bhigh.setdefault(d, []).append(r)
            else:
                blow.setdefault(d, []).append(r)
        n_codes += 1

    # 聚合为日度等权均值
    days = sorted(set(low) | set(high))
    excess = []          # LOW - HIGH (合并卖压)
    excess_avoid = []    # LOW - ALL
    excess_exp = []      # 交割周内 LOW-HIGH
    excess_non = []      # 非交割周 LOW-HIGH
    n_days = 0
    for d in days:
        lr = low.get(d, [])
        hr = high.get(d, [])
        if not lr or not hr:
            continue
        ml = float(np.mean(lr))
        mh = float(np.mean(hr))
        excess.append(ml - mh)
        ma = float(np.mean(lr + hr)) if (lr or hr) else np.nan
        if np.isfinite(ma):
            excess_avoid.append(ml - ma)
        if is_expiry_week(d):
            excess_exp.append(ml - mh)
        else:
            excess_non.append(ml - mh)
        n_days += 1

    # 大宗单独隔离序列
    bdays = sorted(set(blow) | set(bhigh))
    excess_block = []
    n_bdays = 0
    for d in bdays:
        blr = blow.get(d, [])
        bhr = bhigh.get(d, [])
        if not blr or not bhr:
            continue
        excess_block.append(float(np.mean(blr)) - float(np.mean(bhr)))
        n_bdays += 1

    res = {}
    if excess:
        ex = np.array(excess)
        res = {
            "n_days": n_days,
            "n_low_obs": sum(len(v) for v in low.values()),
            "n_high_obs": sum(len(v) for v in high.values()),
            "mean_excess_bp": round(float(ex.mean()) * 1e4, 2),
            "nw_t": round(nw_t_from_raw(ex), 3) if nw_t_from_raw(ex) is not None else None,
            "mean_avoid_bp": round(float(np.mean(excess_avoid)) * 1e4, 2) if excess_avoid else None,
            "expiry_week_mean_bp": round(float(np.mean(excess_exp)) * 1e4, 2) if excess_exp else None,
            "expiry_week_n_days": len(excess_exp),
            "non_expiry_mean_bp": round(float(np.mean(excess_non)) * 1e4, 2) if excess_non else None,
            "non_expiry_n_days": len(excess_non),
        }
    else:
        res = {"n_days": 0, "verdict": "NO_DATA"}

    # 大宗单独隔离结果
    res_block = {}
    if excess_block:
        eb = np.array(excess_block)
        res_block = {
            "n_days": n_bdays,
            "n_low_obs": sum(len(v) for v in blow.values()),
            "n_high_obs": sum(len(v) for v in bhigh.values()),
            "mean_excess_bp": round(float(eb.mean()) * 1e4, 2),
            "nw_t": round(nw_t_from_raw(eb), 3) if nw_t_from_raw(eb) is not None else None,
        }
    else:
        res_block = {"n_days": 0, "verdict": "NO_DATA"}

    # 裁定（WATCH 口径：paired excess>0 且 NW t>=2.0 才有用）
    if res.get("n_days"):
        t = res.get("nw_t")
        me = res.get("mean_excess_bp", 0)
        if me > 0 and t is not None and t >= 2.0:
            res["verdict"] = "WATCH_KEEP(已验证卖压(解禁∪机构净卖)确实更亏，规避有效；仍须 Purged CV + 成本后显著才 PROMOTE)"
        elif me > 0:
            res["verdict"] = "WATCH_WEAK(方向为正但 t<2.0，证据不足)"
        else:
            res["verdict"] = "REJECT(合并高卖压未显著更亏 → 仅展示，不进 buy_score)"

    if res_block.get("n_days"):
        t = res_block.get("nw_t")
        me = res_block.get("mean_excess_bp", 0)
        if me > 0 and t is not None and t >= 2.0:
            res_block["verdict"] = "WATCH_KEEP(大宗变现单独隔离亦显著更亏 → 新规避信号有效)"
        elif me > 0:
            res_block["verdict"] = "WATCH_WEAK(大宗方向为正但 t<2.0)"
        else:
            res_block["verdict"] = "REJECT(大宗变现未显著更亏)"
    res["block_only"] = res_block
    if verbose:
        print("=" * 78)
        print("[short_pressure_probe] 卖压预警 OOS 前检定（按日配对超额，LOW−HIGH）")
        print("=" * 78)
        print("  窗口: %s ~ %s | 扫描代码 %d 只" % (WIN[0], WIN[1], n_codes))
        if res.get("n_days"):
            print("  【已验证卖压: 解禁∪机构净卖（大宗 OOS REJECT，不计入）】")
            print("  结算交易日: %d | LOW观测 %d | HIGH观测 %d"
                  % (res["n_days"], res["n_low_obs"], res["n_high_obs"]))
            print("  日均配对超额(LOW−HIGH): %+.2fbp ｜ NW t=%s"
                  % (res["mean_excess_bp"], res["nw_t"]))
            print("  规避 HIGH 的增量(LOW−ALL): %+.2fbp" % (res["mean_avoid_bp"]))
            print("  交割周内(LOW−HIGH): %+.2fbp (n=%d) ｜ 非交割周: %+.2fbp (n=%d)"
                  % (res["expiry_week_mean_bp"], res["expiry_week_n_days"],
                     res["non_expiry_mean_bp"], res["non_expiry_n_days"]))
            print("  裁定: %s" % res["verdict"])
        else:
            print("  无可用样本 → %s" % res.get("verdict"))
        rb = res.get("block_only", {})
        if rb.get("n_days"):
            print("  【大宗变现单独隔离(block_only)】")
            print("  结算交易日: %d | LOW观测 %d | HIGH观测 %d"
                  % (rb["n_days"], rb["n_low_obs"], rb["n_high_obs"]))
            print("  日均配对超额(无大宗−有大宗): %+.2fbp ｜ NW t=%s"
                  % (rb["mean_excess_bp"], rb["nw_t"]))
            print("  裁定: %s" % rb["verdict"])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run(verbose=not a.json)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
