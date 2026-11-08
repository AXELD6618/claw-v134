#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
w2s_breakout.py — 盘中「弱转强」(weak→strong, w2s) 新买点范式

背景（用户授权突破「T 日尾盘买」结构性不可达）：
  「浅跌突然板」(T-1 浅跌无冲高 → T 日直接涨停) 在「T 日尾盘买」范式下买不进（尾盘已封板）。
  突破口：在 T 日 *盘中* 检测到「弱→强」且未封板时买入（买点可达）。

本模块职责（研究+工程，严禁改 buy_score / unified_tail_pick / core_factor_model）：
  A. w2s_oos_backtest(...)    —— 日线代理 OOS 回测（诚实边界：日线收盘≈转折点代理买点；
                                  封板股日线收盘=封板价，故封板股买点日线不可代表，需实时验证）。
  B. w2s_signal_live(quote)    —— 实时盘口信号判定（字段对齐 research/p1_discovery.discovery_filter.intraday_w2s）。
  C. w2s_profile(code, quote)  —— 信号档案（买点参考价 + OOS 结论，绝不伪造）。

定义（与 p1_discovery.intraday_w2s 严格对齐；pct 用百分比单位，open_pct 内部转百分比）：
  弱转强 = 开盘弱/平(open<=prev_close*1.005 或盘中曾≤0)
        AND 收/现强(>=prev_close*1.07, 即 +7%)
        AND 未封板(close/price < 涨停阈值, 主板 10%/双创 20%/北交 30%, 减 0.005 容差)
        AND 放量(volr = vol/近5日均量 >= 1.5)

两种 OOS 口径（mode）：
  "open_weak" —— 严格按 team-lead 步骤A：T 日开盘弱/平(open<=prev_close*1.005) → 收强未封板。
  "t1_weak"   —— 放松以贴合用户真实目标（捕「浅跌突然板」可达窗口）：T-1 浅跌无冲高
                 (T-1 收跌[-4%,-0.5%] 且 T-1 最高<+5%) → T 日收强未封板。开盘不强求弱。
  注：603897/603006/603612 三只样本 T 日均 *跳空强开(+7~+10%)且封板*，既不「开盘弱」也未「未封板」，
      故两种日线口径都 *不* 把其 T 日代入原方程；其可达买点只存在于盘中封板前窗口，须实时验证（见报告）。

用法：
  .venv/Scripts/python.exe research/w2s_breakout.py            # 跑全样本 OOS（两种口径）+ 样本推断
  .venv/Scripts/python.exe research/w2s_breakout.py --smoke   # 抽 500 只 smoke
"""
import os
import sys
import json
import glob
import argparse
import datetime

import numpy as np
from research.board_limit import board_limit_pct, is_limit_up

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

W2S_PCT = 7.0      # 收/现强阈值(%)，对齐 p1_discovery.W2S_PCT
W2S_VOLR = 1.5     # 量比阈值，对齐 p1_discovery.W2S_VOLR
ZT_PCT = 9.5       # 涨停判定阈值(%)，对齐 p1_discovery.ZT_PCT
RANDOM_T1 = 1.088  # 生产真及格线：随机基线 PF_t1
RANDOM_T5 = 1.259  # 生产真及格线：随机基线 PF_t5

# ============================================================
# 交易所涨停口径
# ============================================================
def _limit_pct(code):
    return board_limit_pct(code)



def _zlimit_close(code, prev_close):
    """涨停价（交易所 round 到分 - 0.005 容差），对应「未封板」上界。"""
    if prev_close <= 0:
        return float("inf")
    lp = _limit_pct(code)
    return round(prev_close * (1 + lp), 2) - 0.005


# ============================================================
# Newey-West t（与 research/short_pressure_probe.nw_t_from_raw 等价，内联以自包含）
# ============================================================
def _nw_t(raw):
    raw = np.asarray(raw, dtype=float)
    n = raw.size
    if n < 5:
        return None
    x = raw - raw.mean()
    q = int(4 * (n / 100.0) ** 0.25)
    q = min(q, n - 1)
    if q < 1:
        q = 1
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


# ============================================================
# 数据加载：全局交易日历（所有文件日期并集）
# ============================================================
def _load_calendar(daily_dir, start=None, end=None):
    dates = set()
    files = glob.glob(os.path.join(daily_dir, "*.csv"))
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                next(f, None)
                for line in f:
                    if not line:
                        continue
                    d = line.split(",", 1)[0].strip()
                    if len(d) == 10 and d[4] == "-":
                        dates.add(d)
        except Exception:
            continue
    cal = sorted(dates)
    if start:
        cal = [d for d in cal if d >= start]
    if end:
        cal = [d for d in cal if d <= end]
    idx = {d: i for i, d in enumerate(cal)}
    return cal, idx


def _parse_file(fp):
    """读单只日线 → (dates[], open[], close[], high[], low[], vol[])。列序=日期,开盘,收盘,最高,最低,成交量。"""
    ds, o, c, h, l, v = [], [], [], [], [], []
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            next(f, None)
            for line in f:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                try:
                    ds.append(parts[0].strip())
                    o.append(float(parts[1]))
                    c.append(float(parts[2]))
                    h.append(float(parts[3]))
                    l.append(float(parts[4]))
                    v.append(float(parts[5]))
                except Exception:
                    continue
    except Exception:
        return None
    if len(c) < 6:
        return None
    return ds, np.array(o), np.array(c), np.array(h), np.array(l), np.array(v)


# ============================================================
# OOS 主引擎
# ============================================================
def w2s_oos_backtest(daily_dir=None, start="2024-07-01", end="2026-10-28",
                     mode="open_weak", w2s_pct=0.07, volr_thr=1.5,
                     verbose=True, limit_files=None):
    """日线代理 OOS 回测。

    mode="open_weak": open<=prev_close*1.005 AND close>=prev*1.07 AND close<zlim AND volr>=1.5
    mode="t1_weak"  : T-1 浅跌无冲高 AND T 收强未封板 AND volr>=1.5
                      （T-1 收跌[-4%,-0.5%] 且 T-1 最高<+5%）

    返回 dict（含 n_signal / mean_r1/r5 / base_r1/r5 / excess / t / pf_t1/t5 / hit_multiple / gate 各项）。
    """
    if daily_dir is None:
        daily_dir = os.path.join(ROOT, "backtest_cache", "daily")
    cal, idx = _load_calendar(daily_dir, start, end)
    NC = len(cal)
    if verbose:
        print("[w2s] 交易日历 %d 天 (%s ~ %s), mode=%s" % (NC, cal[0] if cal else "-", cal[-1] if cal else "-", mode))

    files = sorted(glob.glob(os.path.join(daily_dir, "*.csv")))
    if limit_files:
        files = files[:limit_files]

    # 累积事件（day_idx, value）列表
    sig_d_r1, sig_v_r1, sig_d_r5, sig_v_r5 = [], [], [], []
    buy_d_r1, buy_v_r1, buy_d_r5, buy_v_r5 = [], [], [], []
    sig_r1_all, sig_r5_all, buy_r1_all, buy_r5_all = [], [], [], []   # 汇总均值用（池化）
    # 多维特征累积（仅在信号事件上）：(t1_weak, above_ma20, volr_tier, pull_tier) -> lists of r1/r5
    feat = {}  # key tuple -> [n, sum_r1, sum_r5, up1, n1]
    n_signal = 0

    for fi, fp in enumerate(files):
        parsed = _parse_file(fp)
        if parsed is None:
            continue
        ds, OA, CA, HA, LA, VA = parsed
        code = os.path.basename(fp)[:-4]
        lp = _limit_pct(code)
        # 对齐到全局日历
        n = len(ds)
        o_arr = np.full(NC, np.nan)
        c_arr = np.full(NC, np.nan)
        h_arr = np.full(NC, np.nan)
        v_arr = np.full(NC, np.nan)
        for i in range(n):
            j = idx.get(ds[i])
            if j is None:
                continue
            o_arr[j] = OA[i]; c_arr[j] = CA[i]; h_arr[j] = HA[i]; v_arr[j] = VA[i]

        for i in range(n):
            j = idx.get(ds[i])
            if j is None or j == 0:
                continue
            if j < 5:
                continue
            pc = c_arr[j - 1]          # prev_close（上一交易日收盘）
            if not np.isfinite(pc) or pc <= 0:
                continue
            close = c_arr[j]
            if not np.isfinite(close) or close <= 0:
                continue
            openp = o_arr[j]
            if not np.isfinite(openp) or openp <= 0:
                continue
            # 涨停价
            zlim = round(pc * (1 + lp), 2) - 0.005
            sealed = close >= zlim
            # 可买子集标记（未封板）
            buyable = (not sealed)
            # T+1 / T+5（全局日历精确）
            j1 = j + 1; j5 = j + 5
            c1 = c_arr[j1] if j1 < NC else np.nan
            c5 = c_arr[j5] if j5 < NC else np.nan
            r1 = (c1 / close - 1.0) if (j1 < NC and np.isfinite(c1) and c1 > 0) else np.nan
            r5 = (c5 / close - 1.0) if (j5 < NC and np.isfinite(c5) and c5 > 0) else np.nan

            # —— 信号判定 ——
            sig = False
            if mode == "open_weak":
                open_ok = openp <= pc * 1.005
                close_ok = close >= pc * (1 + w2s_pct)
                if open_ok and close_ok and (not sealed):
                    # 量比：近 5 日均量（前 5 个交易日）
                    vwin = v_arr[j - 5:j]
                    if np.isfinite(vwin).all() and vwin.mean() > 0:
                        volr = VA[i] / vwin.mean()
                        if volr >= volr_thr:
                            sig = True
            elif mode == "t1_weak":
                # T-1 浅跌无冲高
                pc2 = c_arr[j - 2]
                if not np.isfinite(pc2) or pc2 <= 0:
                    continue
                t1_close_pct = c_arr[j - 1] / pc2 - 1.0
                t1_high_pct = h_arr[j - 1] / pc2 - 1.0
                t1_weak = (-0.04 <= t1_close_pct <= -0.005) and (t1_high_pct < 0.05)
                close_ok = close >= pc * (1 + w2s_pct)
                if t1_weak and close_ok and (not sealed):
                    vwin = v_arr[j - 5:j]
                    if np.isfinite(vwin).all() and vwin.mean() > 0:
                        volr = VA[i] / vwin.mean()
                        if volr >= volr_thr:
                            sig = True
            if not sig:
                # 仍可买则计入基线
                if buyable:
                    if np.isfinite(r1):
                        buy_d_r1.append(j); buy_v_r1.append(r1); buy_r1_all.append(r1)
                    if np.isfinite(r5):
                        buy_d_r5.append(j); buy_v_r5.append(r5); buy_r5_all.append(r5)
                continue

            # 信号命中
            n_signal += 1
            volr = VA[i] / v_arr[j - 5:j].mean() if (np.isfinite(v_arr[j - 5:j]).all() and v_arr[j - 5:j].mean() > 0) else 0.0
            if np.isfinite(r1):
                sig_d_r1.append(j); sig_v_r1.append(r1); sig_r1_all.append(r1)
            if np.isfinite(r5):
                sig_d_r5.append(j); sig_v_r5.append(r5); sig_r5_all.append(r5)
            # 基线同步计入（信号股本身也是可买子集一员）
            if buyable:
                if np.isfinite(r1):
                    buy_d_r1.append(j); buy_v_r1.append(r1); buy_r1_all.append(r1)
                if np.isfinite(r5):
                    buy_d_r5.append(j); buy_v_r5.append(r5); buy_r5_all.append(r5)

            # —— 多维特征（仅信号事件）——
            # T-1 浅跌无冲高（对 open_weak 也记录，便于交叉分析）
            pc2 = c_arr[j - 2]
            t1_weak_f = False
            if np.isfinite(pc2) and pc2 > 0:
                t1cp = c_arr[j - 1] / pc2 - 1.0
                t1hp = h_arr[j - 1] / pc2 - 1.0
                t1_weak_f = bool((-0.04 <= t1cp <= -0.005) and (t1hp < 0.05))
            # 站上 MA20
            ma20 = np.nanmean(c_arr[j - 20:j]) if j >= 20 else np.nan
            above_ma20 = bool(np.isfinite(ma20) and close > ma20)
            # 量比档
            volr_tier = "hi" if volr >= 2.0 else "lo"
            # 盘中拉升幅度（收-开 相对 prev）
            pull = (close - openp) / pc
            pull_tier = "big" if pull >= 0.05 else "mid"
            key = (t1_weak_f, above_ma20, volr_tier, pull_tier)
            if key not in feat:
                feat[key] = [0, 0.0, 0.0, 0, 0]
            f = feat[key]
            f[0] += 1
            if np.isfinite(r1):
                f[1] += r1; f[3] += 1; f[4] += (1 if r1 > 0 else 0)
            if np.isfinite(r5):
                f[2] += r5

        if verbose and (fi % 1000 == 0):
            print("[w2s] 处理 %d/%d 文件, 当前信号数=%d" % (fi, len(files), n_signal))

    # ---- 汇总：池化均值 ----
    sig_r1 = np.array(sig_r1_all) if sig_r1_all else np.array([np.nan])
    sig_r5 = np.array(sig_r5_all) if sig_r5_all else np.array([np.nan])
    buy_r1 = np.array(buy_r1_all) if buy_r1_all else np.array([np.nan])
    buy_r5 = np.array(buy_r5_all) if buy_r5_all else np.array([np.nan])
    mean_r1 = float(np.nanmean(sig_r1)); base_r1 = float(np.nanmean(buy_r1))
    mean_r5 = float(np.nanmean(sig_r5)); base_r5 = float(np.nanmean(buy_r5))
    pf_t1 = (1.0 + mean_r1) / (1.0 + base_r1)
    pf_t5 = (1.0 + mean_r5) / (1.0 + base_r5)
    frac_up_sig = float(np.mean(sig_r1 > 0)); frac_up_base = float(np.mean(buy_r1 > 0))
    hit_multiple = frac_up_sig / frac_up_base if frac_up_base > 0 else 0.0
    # H5 命中倍数（用 r5 方向）
    frac_up_sig5 = float(np.mean(sig_r5 > 0)); frac_up_base5 = float(np.mean(buy_r5 > 0))
    hit_multiple5 = frac_up_sig5 / frac_up_base5 if frac_up_base5 > 0 else 0.0

    # ---- 配对超额（按日）+ Newey-West t（用字典索引按日聚合）----
    sig_dm1_map = {}
    if sig_d_r1:
        sd = np.array(sig_d_r1); sv = np.array(sig_v_r1)
        _s = np.bincount(sd, weights=sv, minlength=NC)
        _c = np.bincount(sd, minlength=NC)
        for k in set(sd.tolist()):
            sig_dm1_map[k] = _s[k] / _c[k] if _c[k] > 0 else np.nan
    buy_dm1_map = {}
    if buy_d_r1:
        bd = np.array(buy_d_r1); bv = np.array(buy_v_r1)
        _s = np.bincount(bd, weights=bv, minlength=NC)
        _c = np.bincount(bd, minlength=NC)
        for k in set(bd.tolist()):
            buy_dm1_map[k] = _s[k] / _c[k] if _c[k] > 0 else np.nan
    sig_dm5_map = {}
    if sig_d_r5:
        sd = np.array(sig_d_r5); sv = np.array(sig_v_r5)
        _s = np.bincount(sd, weights=sv, minlength=NC)
        _c = np.bincount(sd, minlength=NC)
        for k in set(sd.tolist()):
            sig_dm5_map[k] = _s[k] / _c[k] if _c[k] > 0 else np.nan
    buy_dm5_map = {}
    if buy_d_r5:
        bd = np.array(buy_d_r5); bv = np.array(buy_v_r5)
        _s = np.bincount(bd, weights=bv, minlength=NC)
        _c = np.bincount(bd, minlength=NC)
        for k in set(bd.tolist()):
            buy_dm5_map[k] = _s[k] / _c[k] if _c[k] > 0 else np.nan

    common_days1 = sorted(set(sig_dm1_map) & set(buy_dm1_map))
    ex1 = np.array([sig_dm1_map[k] - buy_dm1_map[k] for k in common_days1])
    common_days5 = sorted(set(sig_dm5_map) & set(buy_dm5_map))
    ex5 = np.array([sig_dm5_map[k] - buy_dm5_map[k] for k in common_days5])
    t_r1 = _nw_t(ex1) if len(ex1) >= 5 else None
    t_r5 = _nw_t(ex5) if len(ex5) >= 5 else None
    excess_r1 = float(ex1.mean()) if len(ex1) else float("nan")
    excess_r5 = float(ex5.mean()) if len(ex5) else float("nan")

    # ---- gate（H5 口径）----
    gate = {
        "n_test_ge_200": n_signal >= 200,
        "hit_multiple_ge_1.15": hit_multiple5 >= 1.15,
        "pf_sig_ge_1.30": pf_t5 >= 1.30,
        "mean_sig_gt_base_0.20": (mean_r5 - base_r5) >= 0.20,
        "t_ge_2.0": (t_r5 is not None and t_r5 >= 2.0),
    }
    passed = sum(1 for v in gate.values() if v)
    gate_pass = passed == len(gate)

    # ---- 多维最优组合 ----
    multidim = []
    for key, f in feat.items():
        n, sr1, sr5, n1, up1 = f
        mr1 = sr1 / n1 if n1 else np.nan
        mr5 = sr5 / n if n else np.nan
        pf5 = (1.0 + mr5) / (1.0 + base_r5)
        up_rate = up1 / n1 if n1 else np.nan
        multidim.append({
            "t1_weak": key[0], "above_ma20": key[1], "volr_tier": key[2], "pull_tier": key[3],
            "n": n, "mean_r1": round(mr1, 4), "mean_r5": round(mr5, 4),
            "pf_t5": round(pf5, 4), "up_rate_t1": round(up_rate, 4),
        })
    multidim.sort(key=lambda x: (x["n"], x["pf_t5"]), reverse=True)

    out = {
        "mode": mode,
        "window": "%s~%s" % (start, end),
        "n_signal": n_signal,
        "n_signal_days": len(common_days5),
        "mean_r1": round(mean_r1, 4), "base_r1": round(base_r1, 4),
        "mean_r5": round(mean_r5, 4), "base_r5": round(base_r5, 4),
        "excess_r1_pp": round(excess_r1 * 100, 2), "excess_r5_pp": round(excess_r5 * 100, 2),
        "t_r1": round(t_r1, 3) if t_r1 is not None else None,
        "t_r5": round(t_r5, 3) if t_r5 is not None else None,
        "pf_t1": round(pf_t1, 4), "pf_t5": round(pf_t5, 4),
        "random_baseline": {"pf_t1": RANDOM_T1, "pf_t5": RANDOM_T5},
        "hit_multiple_t1": round(hit_multiple, 4),
        "hit_multiple_t5": round(hit_multiple5, 4),
        "frac_up_signal_t1": round(frac_up_sig, 4), "frac_up_base_t1": round(frac_up_base, 4),
        "gate": gate, "gate_pass": gate_pass, "gate_conditions_passed": passed,
        "multidim_top": multidim[:12],
    }
    if verbose:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


# ============================================================
# 实时信号（盘口）
# ============================================================
def w2s_signal_live(quote):
    """实时盘口弱转强判定。返回 signal dict（命中）或 None（未命中/异常）。绝不抛异常。

    quote 字段（对齐 p1_discovery discovery_filter 走出的快照结构）：
      code/sec_code, name/sec_name, prev_close, open, price, pct(百分比, 如 7.5 表示 +7.5%),
      high, vol, volma5(近5日均量，可选；缺则返 None), time
    """
    try:
        code = str(quote.get("code") or quote.get("sec_code") or "")
        prev_close = float(quote.get("prev_close") or 0)
        if prev_close <= 0:
            return None
        openp = float(quote.get("open") or 0)
        price = float(quote.get("price") or 0)
        pct = float(quote.get("pct") or 0)            # 百分比
        high = float(quote.get("high") or 0)
        vol = float(quote.get("vol") or 0)
        volma5 = float(quote.get("volma5") or quote.get("vol_avg_5") or 0)
        if price <= 0:
            price = prev_close * (1 + pct / 100.0)
        open_pct = (openp - prev_close) / prev_close * 100.0
        high_pct = (high - prev_close) / prev_close * 100.0 if high > 0 else 0.0
        volr = (vol / volma5) if volma5 > 0 else 0.0
        limit_pct = _limit_pct(code) * 100.0          # 百分比阈值(如 10)
        # 条件：开盘弱/平(<=+0.5%) AND 现强(>=7%) AND 未封板(<limit-0.5) AND 放量(>=1.5)
        if (open_pct <= 0.5) and (pct >= W2S_PCT) and (pct < limit_pct - 0.5) and (volr >= W2S_VOLR):
            return {
                "code": code, "name": quote.get("name") or quote.get("sec_name") or "?",
                "signal": "w2s",
                "open_pct": round(open_pct, 2), "pct": round(pct, 2),
                "high_pct": round(high_pct, 2), "vol_ratio": round(volr, 2),
                "price": round(price, 3), "time": quote.get("time"),
                "note": "盘中弱转强买点(可达·封板前窗口·验证中·非尾盘主推)",
            }
        return None
    except Exception:
        return None


def w2s_profile(code, quote, oos_conclusion=""):
    """信号档案：买点参考价 + OOS 结论（绝不伪造）。异常返 {}。"""
    try:
        sig = w2s_signal_live(quote)
        price = sig.get("price") if sig else (float(quote.get("price") or 0) or float(quote.get("prev_close") or 0))
        return {
            "code": str(code),
            "buy_ref": round(price, 3) if price else None,
            "triggered": bool(sig),
            "note": "盘中弱转强买点(可达·验证中)",
            "oos": oos_conclusion or "见 w2s_oos_backtest 输出",
        }
    except Exception:
        return {}


# ============================================================
# 样本推断（603897/603006/603612 的 T 日 2026-10-28）
# ============================================================
def check_samples(daily_dir=None):
    if daily_dir is None:
        daily_dir = os.path.join(ROOT, "backtest_cache", "daily")
    samples = ["603897", "603006", "603612"]
    res = {}
    for code in samples:
        fp = os.path.join(daily_dir, code + ".csv")
        if not os.path.exists(fp):
            res[code] = {"error": "no file"}
            continue
        parsed = _parse_file(fp)
        if parsed is None:
            res[code] = {"error": "parse fail"}
            continue
        ds, OA, CA, HA, LA, VA = parsed
        dmap = {ds[i]: i for i in range(len(ds))}
        for Td in ("2026-10-28",):
            if Td not in dmap:
                res[code] = {"error": "no T day"}
                continue
            j = dmap[Td]
            if j < 2:
                continue
            pc = CA[j - 1]; openp = OA[j]; close = CA[j]; high = HA[j]; vol = VA[j]
            lp = _limit_pct(code)
            zlim = round(pc * (1 + lp), 2) - 0.005
            vwin = VA[j - 5:j]
            volr = vol / vwin.mean() if (len(vwin) == 5 and vwin.mean() > 0) else 0.0
            open_pct = (openp / pc - 1) * 100
            close_pct = (close / pc - 1) * 100
            low_pct = (LA[j] / pc - 1) * 100
            res[code] = {
                "T": Td, "prev_close": pc,
                "open": openp, "open_pct": round(open_pct, 2),
                "close": close, "close_pct": round(close_pct, 2),
                "low_pct": round(low_pct, 2), "volr": round(volr, 2),
                "zlim": zlim,
                "cond_open_weak": bool(openp <= pc * 1.005),
                "cond_close_strong": bool(close >= pc * 1.07),
                "cond_not_sealed": bool(close < zlim),
                "cond_volr": bool(volr >= 1.5),
                "triggers_w2s_open_weak": bool((openp <= pc * 1.005) and (close >= pc * 1.07) and (close < zlim) and (volr >= 1.5)),
                "note": "gap-up open & sealed → 日线口径不可达其盘中买点(需实时封板前轮询)",
            }
    return res


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="抽 500 只 smoke")
    ap.add_argument("--mode", default="open_weak", choices=["open_weak", "t1_weak"])
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-10-28")
    ap.add_argument("--out", default=None, help="写 JSON 路径")
    a = ap.parse_args()

    limit = 500 if a.smoke else None
    print("=" * 60)
    print("W2S OOS 主口径 (mode=%s)" % a.mode)
    print("=" * 60)
    out = w2s_oos_backtest(start=a.start, end=a.end, mode=a.mode, limit_files=limit)

    print("\n" + "=" * 60)
    print("W2S OOS 放松口径 (mode=t1_weak, 捕浅跌突然板可达窗口)")
    print("=" * 60)
    out2 = w2s_oos_backtest(start=a.start, end=a.end, mode="t1_weak", limit_files=limit)

    print("\n" + "=" * 60)
    print("样本推断 603897/603006/603612 @2026-10-28")
    print("=" * 60)
    samp = check_samples()
    print(json.dumps(samp, ensure_ascii=False, indent=2))

    payload = {"open_weak": out, "t1_weak": out2, "samples": samp,
               "generated_at": datetime.datetime.now().isoformat(timespec="seconds")}
    outp = a.out or os.path.join(ROOT, "backtest_cache", "w2s_oos.json")
    try:
        json.dump(payload, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=float)
        print("\n[OK] 写 %s" % outp)
    except Exception as e:
        print("[warn] 写 JSON 失败: %s" % e)


if __name__ == "__main__":
    main()
