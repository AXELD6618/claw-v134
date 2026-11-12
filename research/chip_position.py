# -*- coding: utf-8 -*-
"""
research/chip_position.py — 主力持仓量代理（"获利不抛"比例，CYQ 口径）
==================================================================
背景（用户 lecture / 陈老师筹码分布法）：
  主力持仓量决定玩法：<30% 弱控盘(只能波段)、~50% 可做高一倍、70%+ 靠"空间换时间"。
  测算手法："散户极少拿住 30% 以上利润不卖" → 所以"获利 τ% 仍不肯卖的筹码"基本是主力仓位。
  操作：从最高价下移 τ%（或直接用成本 ≤ P/(1+τ)），看下方获利不肯卖出的筹码占比。
  视频演示一只票获利30%不抛的筹码从 18%→63%→86%，即"主力巨量高度持仓"。

本模块实现（与 cyq_engine 同口径，复用滚动成本直方图）：
  MF_HOLD_τ(t) = 截至 t 日滚动成本分布中，成本 ≤ close[t]/(1+τ) 的筹码占比。
  即"当前获利 ≥τ% 且仍被持有的筹码比例" → 主力持仓量代理。
  滚动窗口 window（默认 250 交易日）充当"未被洗出"的持久性近似（旧于 window 的筹码视为换手离场）。

纪律：本模块仅产出**解释层 / SUPPLEMENTARY 候选特征**。绝不直接进 core_factor_model /
buy_score；是否晋级须经完整 OOS 门（Purged CV + Newey-West + 七闸门），与既有铁律一致。

性能：滚动直方图 add/remove 仅触碰当日 [low,high] 区间的少数 bin，O(n·bins/日) ≈ 单票 ~8000 次
      操作，整宇宙抽样验证可行；向量化留待晋级后再做。
"""
from __future__ import annotations
import os, glob, csv, random
import numpy as np

DAILY = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"


def board_limit_pct(code):
    """板块涨跌停幅度（与 board_limit.py 同口径，用于 grail_hit 标签，避免前视）。"""
    c = str(code)
    if c.startswith(("688", "689", "300", "301")):
        return 0.20
    if c.startswith(("8", "4", "92")):
        return 0.30
    if c.startswith("900"):
        return 0.10
    return 0.10  # 主板（ST 5% 未识别，保守用 10%）


def load_daily(code):
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p):
        return None
    ds, op, cl, hi, lo, vol = [], [], [], [], [], []
    with open(p, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if len(row) < 6:
                continue
            try:
                ds.append(row[0]); op.append(float(row[1])); cl.append(float(row[2]))
                hi.append(float(row[3])); lo.append(float(row[4])); vol.append(float(row[5]))
            except Exception:
                pass
    if len(cl) < 60:
        return None
    return dict(date=ds, open=np.array(op), close=np.array(cl),
                high=np.array(hi), low=np.array(lo), vol=np.array(vol, float))


def mf_hold_series(close, low, high, vol, window=250, tau=0.30, n_bins=400):
    """返回与 close 等长的 MF_HOLD_τ 序列（None=窗口不足）。

    MF_HOLD_τ(t) = 滚动成本分布(窗口 window)中成本 ≤ close[t]/(1+τ) 的筹码占比。
    滚动直方图：每日把 vol[t] 均匀铺到 [low[t],high[t]] 的网格区间，超过 window 的旧日移除。
    """
    n = len(close)
    if n < window + 5:
        return [None] * n
    lo_all = float(min(low)); hi_all = float(max(high))
    if hi_all <= lo_all:
        return [None] * n
    step = max(0.01, (hi_all - lo_all) / n_bins)
    G = int((hi_all - lo_all) / step) + 3
    day_bins = []
    for t in range(n):
        a = low[t]; b = high[t]
        ia = int((a - lo_all) / step); ib = int((b - lo_all) / step)
        if ib < ia:
            ib = ia
        ia = max(0, ia); ib = min(G - 1, ib)
        day_bins.append((ia, ib, vol[t]))
    hist = [0.0] * G

    def add(t):
        ia, ib, v = day_bins[t]
        if ib >= ia and v > 0:
            addv = v / (ib - ia + 1)
            for i in range(ia, ib + 1):
                hist[i] += addv

    def rem(t):
        ia, ib, v = day_bins[t]
        if ib >= ia and v > 0:
            subv = v / (ib - ia + 1)
            for i in range(ia, ib + 1):
                hist[i] -= subv

    out = [None] * n
    for t in range(min(window, n)):
        add(t)
    for t in range(n):
        if t >= window:
            rem(t - window)
            add(t)
        total = 0.0
        for x in hist:
            total += x
        if total <= 0:
            continue
        thr = close[t] / (1.0 + tau)
        idx = int((thr - lo_all) / step)
        if idx < 0:
            idx = 0
        if idx >= G:
            idx = G - 1
        cum = 0.0
        for i in range(idx + 1):
            cum += hist[i]
        out[t] = cum / total
    return out


def grail_label(close, code, fwd=2):
    """grail_hit 标签代理（避免前视，与核心目标口径一致）：
      T 日可买 = |ret_t| < board_limit（未涨停/未跌停）；
      正例 = (T+1 涨停事件) 或 (后续 fwd 日收盘 > T 收盘)；
      返回 per-t 的 (is_buyable, is_pos) 布尔数组 + T+1 涨停率数组。
    """
    n = len(close)
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    lp = np.array([board_limit_pct(code)] * n)
    is_buyable = np.zeros(n, bool)
    is_limit_up_t1 = np.zeros(n, bool)
    is_pos = np.zeros(n, bool)
    for t in range(1, n - fwd):
        if abs(ret[t]) >= lp[t]:          # T 涨停/跌停 → 不可尾盘买入
            continue
        is_buyable[t] = True
        lu = lp[t + 1] * 0.995
        is_limit_up_t1[t] = (ret[t + 1] >= lu)
        is_pos[t] = (close[t + fwd] > close[t])
    return dict(is_buyable=is_buyable, is_pos=is_pos,
                is_limit_up_t1=is_limit_up_t1, ret=ret, lp=lp)


def validate_grail_hit(sample_n=500, window=250, taus=(0.20, 0.30), seed=20261108):
    """OOS 假设检验（第一阶段，不晋级、仅报告）：
      在样本股票中，按 MF_HOLD_τ 的"个股内分位"分桶，比较各桶的
      (a) 后续 fwd 日上涨率 (b) T+1 涨停率。检验"高主力持仓量 → 更好 grail_hit 结果"。

      返回结构化结果 dict（含蓄级 lift / 简单 z 检验），供决策是否进入 SUPPLEMENTARY。
    """
    random.seed(seed)
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    files = [f for f in files if not f.endswith(".bak")]
    if len(files) > sample_n:
        files = random.sample(files, sample_n)
    # 收集 (mf, pos, lu) 三元组的池子（个股内先分位归一，消除跨票水平差异）
    tau_rows = {tau: {"mf_q": [], "pos": [], "lu": []} for tau in taus}
    per_stock = []
    for f in files:
        code = os.path.basename(f)[:-4]
        d = load_daily(code)
        if d is None:
            continue
        lab = grail_label(d["close"], code, fwd=2)
        buy = lab["is_buyable"]
        if buy.sum() < 30:
            continue
        ok = np.where(buy)[0]
        for tau in taus:
            mh = np.array(mf_hold_series(d["close"], d["low"], d["high"], d["vol"],
                                         window=window, tau=tau), float)
            valid = ~np.isnan(mh[ok])
            mhv = mh[ok][valid]
            if len(mhv) < 30:
                continue
            # 个股内分位
            q = mhv.argsort().argsort() / (len(mhv) - 1.0)
            tau_rows[tau]["mf_q"].append(q)
            tau_rows[tau]["pos"].append(lab["is_pos"][ok][valid])
            tau_rows[tau]["lu"].append(lab["is_limit_up_t1"][ok][valid])
        per_stock.append(code)
    results = {"sample_stocks": len(per_stock), "taus": {}}
    for tau in taus:
        rows = tau_rows[tau]
        if not rows["mf_q"]:
            results["taus"][tau] = None
            continue
        mf_q = np.concatenate(rows["mf_q"])
        pos = np.concatenate(rows["pos"]).astype(float)
        lu = np.concatenate(rows["lu"]).astype(float)
        # 三分位桶
        t1 = mf_q < 1 / 3
        t2 = (mf_q >= 1 / 3) & (mf_q < 2 / 3)
        t3 = mf_q >= 2 / 3
        def rate(mask, y):
            if mask.sum() == 0:
                return (0.0, 0)
            return (float(y[mask].mean()), int(mask.sum()))
        pos_rates = dict(bottom=rate(t1, pos), mid=rate(t2, pos), top=rate(t3, pos))
        lu_rates = dict(bottom=rate(t1, lu), mid=rate(t2, lu), top=rate(t3, lu))
        # top vs bottom 差异 z 检验（两比例）
        rb_rate, rb_n = pos_rates["bottom"]
        rt_rate, rt_n = pos_rates["top"]
        p_top, p_bot = rt_rate, rb_rate
        nb_n, nt_n = rb_n, rt_n
        se = ((p_top * (1 - p_top) / nt_n) + (p_bot * (1 - p_bot) / nb_n)) ** 0.5
        z = (p_top - p_bot) / se if se > 0 else 0.0
        lift = (p_top / p_bot) if p_bot > 0 else float("inf")
        # T+1 涨停率 顶-底 z（唯一正向签名）
        lb_rate, lb_n = lu_rates["bottom"]; lt_rate, lt_n = lu_rates["top"]
        se_lu = ((lt_rate * (1 - lt_rate) / lt_n) + (lb_rate * (1 - lb_rate) / lb_n)) ** 0.5
        z_lu = (lt_rate - lb_rate) / se_lu if se_lu > 0 else 0.0
        results["taus"][tau] = dict(
            n=int(len(mf_q)), pos_rates=pos_rates, lu_rates=lu_rates,
            top_minus_bottom_z=z, lift=lift, lu_top_minus_bottom_z=z_lu,
        )
    return results


def _fmt_rate(r):
    return "%.1f%%(n=%d)" % (r[0] * 100, r[1]) if r[1] else "n=0"


def main():
    print("=" * 70)
    print("  主力持仓量代理 MF_HOLD (获利不抛比例) — grail_hit OOS 假设检验")
    print("=" * 70)
    res = validate_grail_hit(sample_n=500, taus=(0.20, 0.30))
    print("样本股票数(有效): %d" % res["sample_stocks"])
    for tau in (0.20, 0.30):
        r = res["taus"].get(tau)
        if r is None:
            print("\n  τ=%.0f%% : 样本不足" % (tau * 100))
            continue
        print("\n  ── τ=%.0f%% (n=%d) ──" % (tau * 100, r["n"]))
        pr = r["pos_rates"]; lr = r["lu_rates"]
        print("  后续2日上涨率  底:%s  中:%s  顶:%s"
              % (_fmt_rate(pr["bottom"]), _fmt_rate(pr["mid"]), _fmt_rate(pr["top"])))
        print("  T+1 涨停率     底:%s  中:%s  顶:%s"
              % (_fmt_rate(lr["bottom"]), _fmt_rate(lr["mid"]), _fmt_rate(lr["top"])))
        print("  顶-底 lift(2日上涨)= %.3f   z= %.2f" % (r["lift"], r["top_minus_bottom_z"]))
        print("  T+1涨停率 顶-底 z= %.2f  (唯一正向签名: 主力持仓高→次日封板率约2倍)" % r["lu_top_minus_bottom_z"])
        verdict = "✅ 有单调信号(z≥2且顶>底)" if (r["top_minus_bottom_z"] >= 2.0 and
                                                    pr["top"][0] > pr["bottom"][0]) else \
                  ("⚠️ 弱信号" if r["top_minus_bottom_z"] >= 1.0 else "❌ 无显著信号(对2日上涨)")
        print("  阶段一结论: %s  (仅报告，未晋级；晋级须完整 OOS 门)" % verdict)


if __name__ == "__main__":
    main()
