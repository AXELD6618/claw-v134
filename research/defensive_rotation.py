#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
defensive_rotation.py — 防御轮动(银行/红利/低波) advisory track

=====================================================================
病根（2026-10-29 诊断，已用真实产物核实）
=====================================================================
选股引擎 unified_tail_pick(连板/持有5/反转/突破/弹簧/事件催化) + 发现层
p1_discovery(涨停/放量突破/冲高回落/超跌/弱转强) 是**纯动量/涨停/连板型**。
银行(低 beta、低日波动、极少涨停、不"放量突破")在任何动量阈值下分数≈0；
且监控池 intraday_universe 是"连板台账+持仓+T1+T2+发现层"的动量精选池，
银行不在任何一路 → 结构性三重锁死：
  (a) 引擎无价值/红利/低波/防御维度 → 银行动量特征分≈0；
  (b) 监控池动量精选 → 银行根本不进候选漏斗；
  (c) 无 regime/risk-off 模块(market_regime.py 不存在) → 系统识别不了
      "科技大跌→防御资金轮动银行"的窗口。
→ 近期 delivered_14h50 / edge_brief / evening_review 全部零银行股。

本模块把用户给定的**银行优先级 + 3 个观察信号**提炼为可运行逻辑，作为
**advisory（非 buy_score）** 在盘前/盘中推送，使防御窗口下银行被"看见"。
因子冻结纪律：未过 OOS 绝不进 buy_score / core_factor_model（n_factors 维持 0）。

=====================================================================
输入：银行优先级 + 3 观察信号（用户 2026-10-29 给定）
=====================================================================
优先级：招商(技术最健康+基本面最稳) > 杭州(股息+弹性+差异化)
        > 工商(底仓+资金首选) > 江苏(资金最强但KDJ超买)
        > 农业(分红+营收加速) > 浦发(最低PB但缺催化)
信号① 板块持续性：今日银行上涨覆盖面广(40家)，验证明日是否维持高占比上涨
信号② 财富号·兴业银行三季报明日披露：Q3单季改善超预期→带动股份行修复；
        不及预期→拖累板块情绪
信号③ 风格切换风险：今日科技大跌(电子-5.47%/通信-7.56%)，明日科技止跌反弹
        →防御资金可能流出银行

用法：
  .venv/Scripts/python.exe research/defensive_rotation.py            # 跑 OOS + 演示 emit
  .venv/Scripts/python.exe research/defensive_rotation.py --smoke    # 抽 600 只做 OOS 快速版
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

RANDOM_T1 = 1.088
RANDOM_T5 = 1.259

# ============================================================
# 银行优先级（用户给定） + 定性标签
# ============================================================
# rank: 基础优先级(1最高)；kind: 股份行/国有行/城商行
BANKS = [
    {"code": "600036", "name": "招商银行", "rank": 1, "kind": "股份行",
     "tags": "技术最健康+基本面最稳", "note": "优先核心，技术与基本面双优"},
    {"code": "600926", "name": "杭州银行", "rank": 2, "kind": "城商行",
     "tags": "股息+弹性+差异化", "note": "区域差异化+弹性，股息有支撑"},
    {"code": "601398", "name": "工商银行", "rank": 3, "kind": "国有行",
     "tags": "底仓+资金首选", "note": "底仓首选，大额资金容量最佳"},
    {"code": "600919", "name": "江苏银行", "rank": 4, "kind": "城商行",
     "tags": "资金最强但KDJ超买", "note": "资金面最强，但KDJ超买→追高谨慎·动态降权"},
    {"code": "601288", "name": "农业银行", "rank": 5, "kind": "国有行",
     "tags": "分红+营收加速", "note": "高分红+营收增速改善"},
    {"code": "600000", "name": "浦发银行", "rank": 6, "kind": "股份行",
     "tags": "最低PB但缺催化", "note": "估值最低(PB)，但缺事件催化→需确认驱动"},
]
BY_CODE = {b["code"]: b for b in BANKS}


# ============================================================
# 技术计算：KDJ(9,3,3) / 均线位置（从日线，列序 日期,开盘,收盘,最高,最低,成交量）
# ============================================================
def _load_ohlcv(code, daily_dir=None):
    if daily_dir is None:
        daily_dir = os.path.join(ROOT, "backtest_cache", "daily")
    fp = os.path.join(daily_dir, "%s.csv" % code)
    if not os.path.exists(fp):
        return None
    ds, o, c, h, l, v = [], [], [], [], [], []
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            next(f, None)
            for line in f:
                p = line.split(",")
                if len(p) < 6:
                    continue
                try:
                    ds.append(p[0].strip()); o.append(float(p[1])); c.append(float(p[2]))
                    h.append(float(p[3])); l.append(float(p[4])); v.append(float(p[5]))
                except Exception:
                    continue
    except Exception:
        return None
    if len(c) < 10:
        return None
    return {"date": ds, "open": np.array(o), "close": np.array(c),
            "high": np.array(h), "low": np.array(l), "vol": np.array(v)}


def compute_kdj(code, daily_dir=None, n=9):
    """返回最新 (K,D,J)；数据不足返回 None。"""
    d = _load_ohlcv(code, daily_dir)
    if not d:
        return None
    high = d["high"]; low = d["low"]; close = d["close"]
    N = len(close)
    if N < n + 3:
        return None
    rsv = np.full(N, np.nan)
    for i in range(n - 1, N):
        hh = high[i - n + 1:i + 1].max()
        ll = low[i - n + 1:i + 1].min()
        rsv[i] = (close[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
    K = np.full(N, 50.0); D = np.full(N, 50.0)
    for i in range(1, N):
        if np.isnan(rsv[i]):
            K[i] = K[i - 1]; D[i] = D[i - 1]; continue
        K[i] = (2.0 / 3.0) * K[i - 1] + (1.0 / 3.0) * rsv[i]
        D[i] = (2.0 / 3.0) * D[i - 1] + (1.0 / 3.0) * K[i]
    J = 3 * K - 2 * D
    return float(K[-1]), float(D[-1]), float(J[-1])


def tech_health(code, daily_dir=None):
    """简易技术健康度：收盘站上 MA20 且 MA20 上行 → 健康。返回 (above_ma20, ma20_up)。"""
    d = _load_ohlcv(code, daily_dir)
    if not d:
        return None, None
    c = d["close"]; N = len(c)
    if N < 20:
        return None, None
    ma20 = c[-20:].mean(); ma60 = c[-60:].mean() if N >= 60 else ma20
    return bool(c[-1] > ma20), bool(ma20 > ma60)


# ============================================================
# regime / risk-off 判定（无 market_regime.py，用 macro_series + 信号）
# ============================================================
def _load_macro():
    p = os.path.join(ROOT, "backtest_cache", "macro_series.json")
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def latest_regime():
    m = _load_macro()
    if not m:
        return None
    ks = sorted(m.keys())
    return m[ks[-1]].get("regime") if ks else None


def rotation_active(state, fallback_regime=None):
    """防御轮动是否触发（risk-off 门控）。

    state 字段（均可缺省，缺省时用 macro 最近 regime 兜底）：
      tech_drop_pct     : 今日科技板块涨跌(%)；<=-3 → 风险偏好下行
      bank_advancers    : 今日银行上涨家数
      bank_total        : 今日银行总数（覆盖面 = advancers/total）
      xingye_earnings_tomorrow : bool 兴业明日披露
      force             : 强制触发（调试/演示）
    返回 (active:bool, reason:str)
    """
    tech_drop = state.get("tech_drop_pct")
    adv = state.get("bank_advancers"); tot = state.get("bank_total")
    breadth = (adv / tot) if (adv and tot) else None
    reasons = []
    if state.get("force"):
        reasons.append("强制触发(演示)")
    if tech_drop is not None and tech_drop <= -3.0:
        reasons.append("科技板块大跌 %.2f%%→防御资金轮动" % tech_drop)
    if breadth is not None and breadth >= 0.6:
        reasons.append("银行上涨覆盖面 %.0f%%→板块持续性确认" % (breadth * 100))
    if not reasons:
        # 兜底：macro 最近 regime 为 bear/volatile
        reg = fallback_regime if fallback_regime is not None else latest_regime()
        if reg in ("bear", "volatile"):
            reasons.append("宏观regime=%s(风险偏好下行)" % reg)
    return (len(reasons) > 0, "；".join(reasons) if reasons else "无 risk-off 信号→不触发")


# ============================================================
# 动态评分（用户优先级 + KDJ超买降权 + 兴业/科技信号调制）
# ============================================================
def score_banks(state, daily_dir=None):
    """返回排序后的银行 advisory 列表（含动态降权与理由）。"""
    rows = []
    for b in BANKS:
        code = b["code"]
        kdj = compute_kdj(code, daily_dir)
        ab, up = tech_health(code, daily_dir)
        eff_rank = b["rank"]
        reasons = [b["tags"]]
        # —— KDJ 超买处理：仅 J>100 真正超买才降权（用户明确点名江苏）；
        #    K>80 仅提示不降权，尊重用户给定优先级（招商=技术最健康）——
        if kdj and kdj[2] > 100:
            eff_rank = max(eff_rank, 5.5)  # 降到农业(5)之后、浦发(6)之前
            reasons.append("KDJ超买(J=%.1f)→追高谨慎·降权" % kdj[2])
        elif kdj and kdj[0] > 80:
            reasons.append("KDJ偏高(K=%.1f)·不追高" % kdj[0])
        if ab:
            reasons.append("站上MA20技术健康" if up else "站上MA20")
        else:
            reasons.append("未站上MA20")
        rows.append({
            "code": code, "name": b["name"], "kind": b["kind"],
            "base_rank": b["rank"], "eff_rank": eff_rank,
            "kdj": [round(x, 1) for x in kdj] if kdj else None,
            "above_ma20": ab, "ma20_up": up,
            "note": b["note"], "reasons": reasons,
        })
    # —— 信号② 兴业明日披露：股份行情绪调制 ——
    if state.get("xingye_earnings_tomorrow"):
        xexp = state.get("xingye_expected", "unk")  # pos/neg/unk
        for r in rows:
            if r["kind"] == "股份行":
                if xexp == "pos":
                    r["eff_rank"] -= 0.3; r["reasons"].append("兴业Q3望超预期→股份行修复受益")
                elif xexp == "neg":
                    r["eff_rank"] += 0.3; r["reasons"].append("兴业Q3或不及预期→股份行情绪承压")
                else:
                    r["reasons"].append("兴业明日披露→股份行待验证")
    # —— 信号③ 科技反弹风险：整体谨慎 ——
    if state.get("tech_rebound_risk"):
        for r in rows:
            r["reasons"].append("⚠科技明日若反弹→防御资金或流出·轮动可逆转")
    rows.sort(key=lambda x: x["eff_rank"])
    for i, r in enumerate(rows, 1):
        r["pick_rank"] = i
    return rows


def emit_advisory(state, daily_dir=None):
    """selection_push 调用入口：返回 {active, reason, banks[], disclaimer}。"""
    active, reason = rotation_active(state)
    if not active:
        return {"active": False, "reason": reason, "banks": [], "disclaimer": None}
    banks = score_banks(state, daily_dir)
    disclaimer = ("防御轮动·盘中参考·advisory·非14:50尾盘决策·仅供参考不替代尾盘主推；"
                  "银行低波动低弹性，非涨停型机会，须结合仓位与止损。封板/破位即失效。")
    return {"active": True, "reason": reason, "banks": banks, "disclaimer": disclaimer}


# ============================================================
# OOS 验证：risk-off 日持有银行篮子 vs 可买子集（配对超额 + NW-t）
# ============================================================
def _nw_t(raw):
    raw = np.asarray(raw, dtype=float); n = raw.size
    if n < 5:
        return None
    x = raw - raw.mean()
    q = min(int(4 * (n / 100.0) ** 0.25), n - 1); q = max(q, 1)
    g0 = float(np.dot(x, x)) / n; var = g0
    for lag in range(1, q + 1):
        g = float(np.dot(x[lag:], x[:-lag])) / n
        var += 2.0 * (1.0 - lag / (q + 1.0)) * g
    if var <= 0:
        return None
    se = (var / n) ** 0.5
    return float(raw.mean() / se) if se > 0 else None


def _limit_pct(code):
    return board_limit_pct(code)



def defensive_rotation_oos(daily_dir=None, start="2023-11-22", end="2026-10-20",
                            limit_files=None, verbose=True):
    """在 macro regime=bear/volatile 的 risk-off 日，持有 6 只银行篮子，
    算 T→T+1/T+5 前向收益，配对可买子集(当日未封板全市场)均值，做 NW-t。
    返回 dict（n_signal_days / mean_r1/r5 / base / excess / t / pf_t1/t5 / gate）。"""
    if daily_dir is None:
        daily_dir = os.path.join(ROOT, "backtest_cache", "daily")
    macro = _load_macro()
    # risk-off 日集合
    ro_set = set()
    for k, v in macro.items():
        if isinstance(v, dict) and v.get("regime") in ("bear", "volatile"):
            d = "%s-%s-%s" % (k[:4], k[4:6], k[6:8])
            if start <= d <= end:
                ro_set.add(d)
    if verbose:
        print("[def_rot] risk-off 日(bear/volatile): %d 天" % len(ro_set))

    files = sorted(glob.glob(os.path.join(daily_dir, "*.csv")))
    if limit_files:
        files = files[:limit_files]

    # 日历
    dates = set()
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                next(f, None)
                for line in f:
                    d = line.split(",", 1)[0].strip()
                    if len(d) == 10 and d[4] == "-":
                        dates.add(d)
        except Exception:
            continue
    dates = sorted(dates)
    idx = {d: i for i, d in enumerate(dates)}
    NC = len(dates)

    bank_codes = [b["code"] for b in BANKS]
    sig_d_r1, sig_v_r1, sig_d_r5, sig_v_r5 = [], [], [], []
    buy_d_r1, buy_v_r1, buy_d_r5, buy_v_r5 = [], [], [], []
    n_sig_days = 0
    n_signal = 0

    for fp in files:
        parsed = _parse_file(fp)
        if not parsed:
            continue
        ds, OA, CA, HA, LA, VA = parsed
        code = os.path.basename(fp)[:-4]
        lp = _limit_pct(code)
        o_arr = np.full(NC, np.nan); c_arr = np.full(NC, np.nan)
        for i in range(len(ds)):
            j = idx.get(ds[i])
            if j is None:
                continue
            o_arr[j] = OA[i]; c_arr[j] = CA[i]
        is_bank = code in bank_codes
        for i in range(len(ds)):
            j = idx.get(ds[i])
            if j is None or j < 5 or j >= NC - 5:
                continue
            d = ds[i]
            if d not in ro_set:
                continue
            pc = c_arr[j - 1]
            if not np.isfinite(pc) or pc <= 0:
                continue
            close = c_arr[j]
            if not np.isfinite(close) or close <= 0:
                continue
            zlim = round(pc * (1 + lp), 2) - 0.005
            sealed = close >= zlim
            j1 = j + 1; j5 = j + 5
            c1 = c_arr[j1]; c5 = c_arr[j5]
            r1 = (c1 / close - 1.0) if (np.isfinite(c1) and c1 > 0) else np.nan
            r5 = (c5 / close - 1.0) if (np.isfinite(c5) and c5 > 0) else np.nan
            # 基线：当日未封板全市场
            if (not sealed) and np.isfinite(r1):
                buy_d_r1.append(j); buy_v_r1.append(r1)
            if (not sealed) and np.isfinite(r5):
                buy_d_r5.append(j); buy_v_r5.append(r5)
            # 信号：银行 + 未封板 + 有前向收益
            if is_bank and (not sealed):
                n_signal += 1
                if np.isfinite(r1):
                    sig_d_r1.append(j); sig_v_r1.append(r1)
                if np.isfinite(r5):
                    sig_d_r5.append(j); sig_v_r5.append(r5)
        # 粗略统计风险日覆盖
    # 统计信号覆盖的风险日数
    n_sig_days = len(set(sig_d_r1) | set(sig_d_r5))

    sig_r1 = np.array(sig_v_r1) if sig_v_r1 else np.array([np.nan])
    sig_r5 = np.array(sig_v_r5) if sig_v_r5 else np.array([np.nan])
    buy_r1 = np.array(buy_v_r1) if buy_v_r1 else np.array([np.nan])
    buy_r5 = np.array(buy_v_r5) if buy_v_r5 else np.array([np.nan])
    mean_r1 = float(np.nanmean(sig_r1)); base_r1 = float(np.nanmean(buy_r1))
    mean_r5 = float(np.nanmean(sig_r5)); base_r5 = float(np.nanmean(buy_r5))
    pf_t1 = (1 + mean_r1) / (1 + base_r1)
    pf_t5 = (1 + mean_r5) / (1 + base_r5)

    # 配对超额（按日）
    def _daymap(dd, vv):
        m = {}
        if not dd:
            return m
        sd = np.array(dd); sv = np.array(vv)
        s = np.bincount(sd, weights=sv, minlength=NC)
        c = np.bincount(sd, minlength=NC)
        for k in set(sd.tolist()):
            m[k] = s[k] / c[k] if c[k] > 0 else np.nan
        return m
    sm1 = _daymap(sig_d_r1, sig_v_r1); bm1 = _daymap(buy_d_r1, buy_v_r1)
    sm5 = _daymap(sig_d_r5, sig_v_r5); bm5 = _daymap(buy_d_r5, buy_v_r5)
    ex1 = np.array([sm1[k] - bm1[k] for k in (set(sm1) & set(bm1))])
    ex5 = np.array([sm5[k] - bm5[k] for k in (set(sm5) & set(bm5))])
    t_r1 = _nw_t(ex1) if len(ex1) >= 5 else None
    t_r5 = _nw_t(ex5) if len(ex5) >= 5 else None
    gate = {
        "n_test_ge_200": n_signal >= 200,
        "hit_multiple_ge_1.15": False,  # 命中倍数 OOS 另算，这里用 PF+t 主判
        "pf_t5_ge_1.259": pf_t5 >= 1.259,
        "t_ge_2.0": (t_r5 is not None and t_r5 >= 2.0),
    }
    res = {
        "window": "%s~%s" % (start, end),
        "n_riskoff_days": len(ro_set),
        "n_signal_obs": n_signal,
        "n_signal_days": n_sig_days,
        "mean_r1": round(mean_r1, 5), "base_r1": round(base_r1, 5),
        "mean_r5": round(mean_r5, 5), "base_r5": round(base_r5, 5),
        "excess_r1": round(float(ex1.mean()), 5) if len(ex1) else None,
        "excess_r5": round(float(ex5.mean()), 5) if len(ex5) else None,
        "t_r1": round(t_r1, 3) if t_r1 is not None else None,
        "t_r5": round(t_r5, 3) if t_r5 is not None else None,
        "pf_t1": round(pf_t1, 4), "pf_t5": round(pf_t5, 4),
        "random_baseline": {"pf_t1": RANDOM_T1, "pf_t5": RANDOM_T5},
        "gate": gate,
        "gate_pass": all(gate.values()),
    }
    return res


def _parse_file(fp):
    ds, o, c, h, l, v = [], [], [], [], [], []
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            next(f, None)
            for line in f:
                p = line.split(",")
                if len(p) < 6:
                    continue
                try:
                    ds.append(p[0].strip()); o.append(float(p[1])); c.append(float(p[2]))
                    h.append(float(p[3])); l.append(float(p[4])); v.append(float(p[5]))
                except Exception:
                    continue
    except Exception:
        return None
    if len(c) < 6:
        return None
    return ds, np.array(o), np.array(c), np.array(h), np.array(l), np.array(v)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="抽 600 只做 OOS 快速版")
    ap.add_argument("--state", default=None, help="state json 路径(演示 emit)")
    args = ap.parse_args()

    print("="*60)
    print("防御轮动 OOS（risk-off 日持有银行篮子 vs 可买子集）")
    oos = defensive_rotation_oos(limit_files=600 if args.smoke else None)
    print(json.dumps(oos, ensure_ascii=False, indent=2))

    print("="*60)
    print("演示 emit_advisory（用用户 2026-10-29 信号）")
    demo_state = {
        "tech_drop_pct": -6.0,        # 电子-5.47/通信-7.56 近似
        "bank_advancers": 40, "bank_total": 42,
        "xingye_earnings_tomorrow": True, "xingye_expected": "unk",
        "tech_rebound_risk": True,
    }
    adv = emit_advisory(demo_state)
    print(json.dumps(adv, ensure_ascii=False, indent=2))
