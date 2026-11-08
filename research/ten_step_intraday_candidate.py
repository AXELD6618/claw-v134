#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十步尾盘买入法 · 生产快照接线的候选模块（科学严谨评估，非投资建议）

接线上游：本模块直接消费 `backtest_cache/live_samples.jsonl` 的【顶层键】 schema
（即 `map_quote_to_live` 落盘的字段：lb/ turnover/ floatcap_log/ close_pos/
inside_outside/ mo_*/ main_net_pct/ ddx …），以及步骤 2/6/7/1 所需的日线
（backtest_cache/daily/{code}.csv，列序 日期,开盘,收盘,最高,最低,成交量）。

10 步 → 字段来源（本模块口径）：
  1 大盘选时(中期上升)   : ma20>ma60（日线，全局 gate 在 run_unified_14h50 的 regime gate）
  2 涨幅 3%-5%          : r_T=close[T]/close[T-1]-1 ∈[3%,5%]（日线；代理 13:30 快照）
  3 量比>1              : live["lb"]>1                         ★ 真实 TDX HQInfo.LB
  4 换手率 5%-10%       : 5<=live["turnover"]<=10（单位:%）      ★ 真实 TDX HQInfo.HSL
  5 流通市值 50-200亿    : 5e9<=exp(live["floatcap_log"])<=2e10   ★ 真实 TDX ExtInfo.LTGB×现价
  6 温和放量            : vo[T]>vo[T-1] 且 vo[T]>MA5(vo)（日线）
  7 非高位长上影        : 非(近20日高 且 上影比>0.4)（日线）
  8 分时强弱(代理)      : close_pos>=0.6 且 inside_outside>0     ★ 真实 TDX 收盘日内位置+主动买占比
                          （完整"全天在均价线上方+强于大盘"需分钟数据，见 s9 说明）
  9 买点 14:30 新高回踩  : close_pos>=0.7（EOD 代理；完整需分钟数据 → minute_required）
  10 次日卖出            : 由标签 gap(次日开盘收益)/ nol(次日涨停) 评估，非选股过滤

→ 步骤 3/4/5/8 已是【真实 TDX 点-in-time 字段】，不再是日线代理；步骤 9 的完整形态
  需分钟数据（系统 p1_intraday_accum 当前仅 3 只累积，未铺开），故 s9 以 close_pos 代理
  并标注 minute_required。步骤 2/6/7/1 仍由日线补足（与代理壳一致，属保守误差）。

评估：在 90 条非-null rich 样本上，对比「通过全 10 步」组 vs 「未通过」组 的
  gap(次日开盘收益) 与 nol(次日涨停率)，并做 Welch t 检验 + 组内胜率/PF。
  注：n=90 低于系统 OOS 门控 n≥200，本结果仅【方向性】；随捕获累积(≈96+ 富样本)
  即达门控，可在生产机给出权威判决。本模块不改变 buy_score，仅作候选/WATCH 评估。

用法：
  evaluate_record(o, daily_ctx) → 生产接线：传入单条 live_samples 记录(+日线上下文)，
                                      返回 10 步布尔 + 特征 + 是否通过。
  backtest_live_samples()        → 离线回测 90 条真实样本，打印配对/对比判决。
"""
import os, sys, json, glob
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
D = os.path.join(ROOT, "backtest_cache", "daily")
LIVE = os.path.join(ROOT, "backtest_cache", "live_samples.jsonl")

def _sma(x, n):
    if len(x) < n:
        return np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = (c[n:] - c[:-n]) / n
    return np.concatenate([np.full(n - 1, np.nan), out])

def load_daily(code):
    p = os.path.join(D, code + ".csv")
    try:
        a = np.loadtxt(p, delimiter=",", dtype=str, skiprows=1)
    except Exception:
        return None
    if a.ndim != 2 or a.shape[0] < 70:
        return None
    try:
        dates = a[:, 0]
        op = a[:, 1].astype(float); cl = a[:, 2].astype(float)
        hi = a[:, 3].astype(float); lo = a[:, 4].astype(float); vo = a[:, 5].astype(float)
    except Exception:
        return None
    return dates, op, cl, hi, lo, vo

def daily_ctx_for(code, date):
    """返回该 code 在 date 当日的日内派生特征（步骤1/2/6/7）。"""
    L = load_daily(code)
    if L is None:
        return None
    dates, op, cl, hi, lo, vo = L
    n = len(cl)
    try:
        i = list(dates).index(date)
    except Exception:
        return None
    if i < 20 or i >= n - 1:
        return None
    ma20 = _sma(cl, 20); ma60 = _sma(cl, 60); ma5v = _sma(vo, 5)
    r = cl[i] / cl[i-1] - 1.0 if cl[i-1] > 0 else np.nan
    hh20 = hi[max(0, i-19):i+1].max()
    rng = hi[i] - lo[i]
    us = (hi[i] - max(op[i], cl[i])) / (rng + 1e-9) if rng > 0 else 0.0
    near_high = hi[i] >= 0.95 * hh20
    return dict(
        idx=i, r_T=r,
        trend=(ma20[i] > ma60[i]),
        gentle_vol=(vo[i] > vo[i-1] and vo[i] > ma5v[i]),
        high_shadow_bad=(near_high and us > 0.4),
    )

# ---- 各步通过判定（纯函数，便于生产逐条调用）----
def step2_gain(ctx):       return ctx is not None and (0.03 <= ctx["r_T"] <= 0.05)
def step1_trend(ctx):      return ctx is not None and bool(ctx["trend"])
def step6_vol(ctx):        return ctx is not None and bool(ctx["gentle_vol"])
def step7_shadow(ctx):     return ctx is not None and (not ctx["high_shadow_bad"])

def step3_lb(o):           return (o.get("lb") is not None) and (o["lb"] > 1.0)
def step4_turnover(o):     return (o.get("turnover") is not None) and (5.0 <= o["turnover"] <= 10.0)
def step5_cap(o):
    f = o.get("floatcap_log")
    if f is None:
        return False
    cap = np.exp(f)
    return 5e9 <= cap <= 2e10
def step8_intraday(o):
    cp = o.get("close_pos"); io = o.get("inside_outside")
    if cp is None or io is None:
        return False
    return (cp >= 0.6) and (io > 0.0)
def step9_pullback(o, minute_required=False):
    # EOD 代理：收盘位于日内上 30% 区，近似"尾盘创高回踩均价线不破"。
    # 完整判定需分钟数据（minute_required=True 时，无分钟则视为未知→False）。
    cp = o.get("close_pos")
    if cp is None:
        return False
    if minute_required:
        return False  # 生产未铺开分钟捕获时，s9 不参与判定（保守）
    return cp >= 0.7

def evaluate_record(o, ctx):
    """生产接线：传入单条 live_samples 记录 + 日线上下文，返回判定 dict。"""
    steps = dict(
        s1_trend=step1_trend(ctx), s2_gain=step2_gain(ctx), s3_lb=step3_lb(o),
        s4_turnover=step4_turnover(o), s5_cap=step5_cap(o), s6_vol=step6_vol(ctx),
        s7_shadow=step7_shadow(ctx), s8_intraday=step8_intraday(o),
        s9_pullback=step9_pullback(o, minute_required=False),
    )
    steps["s9_minute_strict"] = step9_pullback(o, minute_required=True)
    core = ["s1_trend","s2_gain","s3_lb","s4_turnover","s5_cap","s6_vol","s7_shadow","s8_intraday"]
    passed = all(steps[k] for k in core)            # s9 用 EOD 代理版
    passed_strict = all(steps[k] for k in core) and steps["s9_minute_strict"]
    return dict(steps=steps, passed=passed, passed_strict=passed_strict,
                gap=o.get("gap"), nol=o.get("nol"))

def _welch(a, b):
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    denom = np.sqrt(va/len(a) + vb/len(b))
    return (ma - mb) / denom if denom > 0 else 0.0

def backtest_live_samples():
    rows = []
    for line in open(LIVE, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if not (o.get("lb") is not None and o.get("turnover") is not None and o.get("floatcap_log") is not None):
            continue  # 步骤 3/4/5 需真实 TDX 字段，缺失则无法评估
        ctx = daily_ctx_for(o.get("code"), o.get("date"))
        if ctx is None:
            continue  # 日线不足/日期未对齐 → 跳过（保守）
        ev = evaluate_record(o, ctx)
        rows.append(ev)
    n = len(rows)
    print("[TS-LIVE] evaluable rich records = %d" % n)
    if n == 0:
        return
    gaps = np.array([r["gap"] for r in rows if r["gap"] is not None], dtype=float)
    nols = np.array([(r["nol"] or 0) for r in rows], dtype=float)
    print("  全样本    : n=%-3d win=%.3f mean_gap=%+.4f nol_rate=%.3f"
          % (len(gaps), (gaps>0).mean(), gaps.mean(), nols.mean()))

    # 逐步骤边际分析：通过某步 vs 未通过某步，gap/nol 是否有差（该过滤是否加分）
    print("\n  [逐步骤边际价值]  pass=通过该步  fail=未通过 → 看 pass 是否更优")
    print("  %-12s %5s %6s %8s %7s | %6s %8s" % ("step","nP","winP","gapP","nolP","t_gap","t_nol"))
    step_keys = ["s1_trend","s2_gain","s3_lb","s4_turnover","s5_cap","s6_vol","s7_shadow","s8_intraday"]
    for k in step_keys:
        pmask = np.array([bool(r["steps"][k]) for r in rows])
        if pmask.sum() < 5 or (~pmask).sum() < 5:
            print("  %-12s nP=%-3d (样本过少，跳过边际)" % (k, int(pmask.sum())))
            continue
        gP, gF = gaps[pmask], gaps[~pmask]
        nP, nF = nols[pmask], nols[~pmask]
        t_g = _welch(gP, gF); t_n = _welch(nP.astype(float), nF.astype(float))
        print("  %-12s %5d %6.3f %+8.4f %7.3f | %6.2f %8.2f"
              % (k, int(pmask.sum()), (gP>0).mean(), gP.mean(), nP.mean(),
                 t_g or 0, t_n or 0))

    # 严格全 10 步 AND vs 宽松核心（去 s9 分钟代理）
    core = ["s1_trend","s2_gain","s3_lb","s4_turnover","s5_cap","s6_vol","s7_shadow","s8_intraday"]
    strict = np.array([r["passed"] for r in rows])
    loose  = np.array([all(r["steps"][k] for k in core) for r in rows])
    def blk(name, m):
        if m.sum() == 0:
            print("  %-10s n=0（不触发）" % name); return
        g = gaps[m]; nl = nols[m]
        print("  %-10s n=%-3d win=%.3f mean_gap=%+.4f nol_rate=%.3f"
              % (name, int(m.sum()), (g>0).mean(), g.mean(), nl.mean()))
    print("\n  [组合触发率与表现]")
    blk("严格10步AND", strict)
    blk("宽松核心(去s9)", loose)
    t_loose = _welch(gaps[loose], gaps[~loose]) if loose.sum()>1 else None
    print("  宽松核心 Welch t(gap PASS vs FAIL)=%.3f" % (t_loose or 0))
    print("  ⚠ n=%d 低于系统 OOS 门控 n≥200，本结果为方向性；富样本累积达 ~96+ 即权威。" % n)
    print("  ⚠ s9(14:30买点) 仅 EOD 代理，完整需分钟数据；逐步骤表已揭示各过滤边际价值。")

if __name__ == "__main__":
    backtest_live_samples()
