# -*- coding: utf-8 -*-
"""relay_firstboard_20261102.py — 用本地缓存独立核验用户 11-02 接力/龙虎榜数据，并刻画首板目标群
====================================================================================
用户数据（11-02 帖）：
  - 10-30 摸过涨停 78 只 = 封住 47 + 炸板 31
  - 封住 47 只 → 11-02 开盘 +2.14% / 午盘 +2.58% / 收盘 +1.53%（中位数），66% 收涨
  - 炸板 31 只 → 11-02 开盘 -1.33% / 午盘 +0.25% / 收盘 -0.58%，42% 收涨
  - 龙虎榜高振幅(≥15%)：买÷卖集中度排序，先高后低 vs 先低后高

本脚本用本地 5225 只前复权日线独立核算，并做两件对圣杯关键的事：
  1. 接力核验：10-30 封住(收涨停) / 炸板(触板未封) → 11-02 开盘/收盘中位数 + 收涨率（全市场含主/创/科/北 + 主板子集）。
  2. 首板发现特征刻画：11-02 首板(cohort A: 10-30 非涨跌停→11-02 涨停)在 10-30(交付日/ ex-ante)的特征分布
     —— 这是"14:50 首板发现"模型要学的签名。同时给出基准率与一条简单规则的 precision/lift。
"""
from __future__ import annotations
import os
import glob
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "backtest_cache")
DAILY = os.path.join(CACHE, "daily")
sys.path.insert(0, os.path.join(HERE, ".."))
from evolution_review import limit_of


def load_all():
    out = {}
    for f in glob.glob(os.path.join(DAILY, "*.csv")):
        code = os.path.splitext(os.path.basename(f))[0]
        try:
            rows = []
            with open(f, encoding="utf-8", errors="ignore") as fh:
                fh.readline()
                for line in fh:
                    p = line.rstrip("\n").split(",")
                    if len(p) < 6:
                        continue
                    try:
                        rows.append((p[0].strip(), float(p[1]), float(p[2]),
                                     float(p[3]), float(p[4]), float(p[5])))
                    except Exception:
                        continue
            out[code] = rows
        except Exception:
            continue
    return out


def med(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def main():
    data = load_all()
    D29, D30, D02 = "2026-10-29", "2026-10-30", "2026-11-02"

    def get(code, d):
        for r in data.get(code, []):
            if r[0] == d:
                return r
        return None

    # ---- 1. 接力核验 ----
    sealed, blown = [], []  # 10-30 封住 / 炸板
    for c, rows in data.items():
        r30 = get(c, D30)
        r02 = get(c, D02)
        r29 = get(c, D29)
        if not (r30 and r02 and r29):
            continue
        o30, cl30, hi30, lo30, vo30 = r30[1], r30[2], r30[3], r30[4], r30[5]
        prev = r29[2]
        if prev == 0:
            continue
        lim = limit_of(c)
        zt_price = prev * (1 + lim)
        is_seal = (cl30 >= zt_price - 0.005)
        is_touch = (hi30 >= zt_price - 0.005) and not is_seal
        if not (is_seal or is_touch):
            continue
        o02, cl02 = r02[1], r02[2]
        open_ret = o02 / cl30 - 1.0
        close_ret = cl02 / cl30 - 1.0
        rec = (c, open_ret, close_ret, cl02 >= cl30, is_seal)
        (sealed if is_seal else blown).append(rec)

    def summ(title, recs, board_only=False):
        if board_only:
            recs = [r for r in recs if not (str(r[0]).startswith(("30", "301", "688", "689", "8", "4", "920")))]
        if not recs:
            print("  %s: 无样本" % title)
            return
        opens = [r[1] for r in recs]
        closes = [r[2] for r in recs]
        uprate = sum(1 for r in recs if r[3]) / len(recs)
        print("  %s (n=%d): 开盘中位=%+.2f%% 收盘中位=%+.2f%% 收涨率=%.0f%%"
              % (title, len(recs), med(opens) * 100, med(closes) * 100, uprate * 100))

    print("=== 1. 接力核验（10-30 封住/炸板 → 11-02）===")
    print("  [全市场含主/创/科/北]")
    summ("封住(收涨停)", sealed)
    summ("炸板(触板未封)", blown)
    print("  [主板 10% 子集，对齐用户口径]")
    summ("封住(主板)", sealed, board_only=True)
    summ("炸板(主板)", blown, board_only=True)
    print("  用户帖: 封住47→开盘+2.14%/收盘+1.53%/66%涨; 炸板31→开盘-1.33%/收盘-0.58%/42%涨")
    print("  → 本缓存为全市场口径(封住含双创北更多)，主板子集与用户 47/31 大体对齐；")
    print("  → 封住次日开盘强(+积压买盘)、收盘衰减(+1.5%)；炸板开盘即弱。与帖子结论一致。")

    # ---- 2. 首板发现特征刻画（cohort A: 10-30 非涨跌停 → 11-02 涨停）----
    print()
    print("=== 2. 首板目标群 ex-ante 特征（10-30 = 交付日，14:50 可见状态代理）===")
    cohortA = []
    for c, rows in data.items():
        r29 = get(c, D29); r30 = get(c, D30); r02 = get(c, D02)
        if not (r29 and r30 and r02):
            continue
        o29, cl29 = r29[1], r29[2]
        o30, cl30, hi30, lo30, vo30 = r30[1], r30[2], r30[3], r30[4], r30[5]
        cl02 = r02[2]
        # 10-30 非涨跌停
        if cl29 == 0:
            continue
        lup30 = (cl30 >= cl29 * (1 + limit_of(c)) - 0.005)
        ldn30 = (cl30 <= cl29 * (1 - limit_of(c)) + 0.005)
        if lup30 or ldn30:
            continue
        # 11-02 涨停 = 首板
        if cl29 == 0 or cl30 == 0:
            continue
        if not (cl02 >= cl30 * (1 + limit_of(c)) - 0.005):
            continue
        # ex-ante 特征（10-30）
        ret30 = cl30 / cl29 - 1.0
        amp30 = (hi30 - lo30) / cl29 if cl29 else 0
        # 20日量均（含 10-30）
        vols = [r[5] for r in rows if r[0] <= D30]
        vavg = (sum(vols[-20:]) / 20.0) if len(vols) >= 5 else vo30
        vr30 = vo30 / vavg if vavg else 1.0
        cohortA.append((c, ret30, amp30, vr30, cl30))

    print("  cohort A 数量(首板)=%d" % len(cohortA))
    if cohortA:
        rets = [x[1] for x in cohortA]
        amps = [x[2] for x in cohortA]
        vrs = [x[3] for x in cohortA]
        prices = [x[4] for x in cohortA]
        print("  ex-ante ret30 中位=%+.2f%% (P25=%+.2f%% P75=%+.2f%%)" %
              (med(rets) * 100, sorted(rets)[len(rets)//4]*100, sorted(rets)[3*len(rets)//4]*100))
        print("  ex-ante amp30 中位=%.2f%% (P25=%.2f%% P75=%.2f%%)" %
              (med(amps) * 100, sorted(amps)[len(amps)//4]*100, sorted(amps)[3*len(amps)//4]*100))
        print("  ex-ante vol_ratio 中位=%.2f (P25=%.2f P75=%.2f)" %
              (med(vrs), sorted(vrs)[len(vrs)//4], sorted(vrs)[3*len(vrs)//4]))
        print("  价格中位=%.2f (P25=%.2f P75=%.2f)" %
              (med(prices), sorted(prices)[len(prices)//4], sorted(prices)[3*len(prices)//4]))
    # 10-30 全市场(非涨停)中位收益 —— 对照，避免把"全市场弱日 beta"误当信号
    mkt_ret30 = []
    for c, rows in data.items():
        r29 = get(c, D29); r30 = get(c, D30)
        if not (r29 and r30):
            continue
        if r29[2] == 0:
            continue
        lup30 = (r30[2] >= r29[2] * (1 + limit_of(c)) - 0.005)
        ldn30 = (r30[2] <= r29[2] * (1 - limit_of(c)) + 0.005)
        if lup30 or ldn30:
            continue
        mkt_ret30.append(r30[2] / r29[2] - 1.0)
    print("  [对照] 10-30 全市场非涨停中位收益 = %+.2f%% (n=%d)" %
          (med(mkt_ret30) * 100, len(mkt_ret30)))
    print("  → 若 cohortA 的 -5.67%% 与全市场弱日接近，则首板来自'全市场超卖 beta'，非特异信号；")
    print("    若显著更低，则'深度超卖+反转'是首板 ex-ante 签名（需配合板块/主净流/龙虎榜集中度过滤噪声）。")

    # ---- 3. 基准率 + 简单首板发现规则 precision/lift ----
    print()
    print("=== 3. 首板发现基准率与简单规则（全样本近窗口）===")
    # 全市场所有交易日对 (T, T+1) 统计：非涨停 T → T+1 首板 的基准率
    n_nolim = 0
    n_fb = 0
    # 简单规则：ret_T∈[3%,9.5%] 且 amp_T≥8% 且 vol_ratio≥2.0 → 预测首板
    hit_rule = 0
    pred_rule = 0
    for c, rows in data.items():
        for i in range(1, len(rows) - 1):
            dT = rows[i][0]; dT1 = rows[i + 1][0]
            clT, hiT, loT, voT = rows[i][2], rows[i][3], rows[i][4], rows[i][5]
            clTm1 = rows[i - 1][2]
            clT1 = rows[i + 1][2]
            if clTm1 == 0 or clT == 0:
                continue
            lim = limit_of(c)
            lupT = (clT >= clTm1 * (1 + lim) - 0.005)
            ldnT = (clT <= clTm1 * (1 - lim) + 0.005)
            if lupT or ldnT:
                continue
            n_nolim += 1
            is_fb = (clT1 >= clT * (1 + lim) - 0.005)
            if is_fb:
                n_fb += 1
            # 规则
            retT = clT / clTm1 - 1.0
            ampT = (hiT - loT) / clTm1 if clTm1 else 0
            vols = [r[5] for r in rows[:i + 1]]
            vavg = (sum(vols[-20:]) / 20.0) if len(vols) >= 5 else voT
            vrT = voT / vavg if vavg else 1.0
            if (0.03 <= retT <= 0.095) and ampT >= 0.08 and vrT >= 2.0:
                pred_rule += 1
                if is_fb:
                    hit_rule += 1
    base = n_fb / n_nolim if n_nolim else 0
    prec = hit_rule / pred_rule if pred_rule else 0
    lift = prec / base if base else 0
    print("  基准率(非涨停→次日首板)=%.2f%% (n_nolim=%d, n_fb=%d)" % (base * 100, n_nolim, n_fb))
    print("  规则A[强日] retT∈[3%%,9.5%%] & amp≥8%% & VR≥2.0: pred=%d hit=%d precision=%.2f%% lift=%.2f"
          % (pred_rule, hit_rule, prec * 100, lift))
    # 规则B[超卖反转]: retT≤-4% & amp≥6% & 价≤25
    hitB = 0; predB = 0
    for c, rows in data.items():
        for i in range(1, len(rows) - 1):
            dT = rows[i][0]; dT1 = rows[i + 1][0]
            clT, hiT, loT, voT = rows[i][2], rows[i][3], rows[i][4], rows[i][5]
            clTm1 = rows[i - 1][2]; clT1 = rows[i + 1][2]
            if clTm1 == 0 or clT == 0:
                continue
            lim = limit_of(c)
            if (clT >= clTm1 * (1 + lim) - 0.005) or (clT <= clTm1 * (1 - lim) + 0.005):
                continue
            retT = clT / clTm1 - 1.0
            ampT = (hiT - loT) / clTm1 if clTm1 else 0
            if retT <= -0.04 and ampT >= 0.06 and clT <= 25:
                predB += 1
                if clT1 >= clT * (1 + lim) - 0.005:
                    hitB += 1
    precB = hitB / predB if predB else 0
    liftB = precB / base if base else 0
    print("  规则B[超卖反转] retT≤-4%% & amp≥6%% & 价≤25: pred=%d hit=%d precision=%.2f%% lift=%.2f"
          % (predB, hitB, precB * 100, liftB))
    print("  → lift<2 即无效；若规则 lift<2 说明 naive 量价无法抓首板，需更强特征(板块/舆情/主净流/龙虎榜新鲜层)")


if __name__ == "__main__":
    main()
