#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
reversal7 全市场 OOS 验证器（"次日盘中高点卖"版本）
========================================================================
数据: backtest_cache/daily/*.csv（~5225只，2018→今，前复权）
      字段: 日期,开盘,收盘,最高,最低,成交量

信号: T 日 下跌 & 未跌停 & 连跌≥7交易日（交易日历缺口>7天则断 streak）
入场: T 日收盘（尾盘买入代理）
退出(三种口径, 均持有期=1日):
  ideal_high : T+1 盘中最高卖  ret = high[T+1]/close[T]-1   ← 用户要的"次日盘中高点卖"
  capture50  : 0.5*(open+high)[T+1]/close[T]-1              ← 半程捕捉(更现实)
  close      : T+1 收盘卖    ret = close[T+1]/close[T]-1    ← 隔夜持有(对照)

PF 口径(与 oospf_validator 完全一致):
  日度等权组合: 有信号日=当日信号股等权收益, 无信号日=0(持币)
  PF = Σ盈利日 / |Σ亏损日| ; 命中率=P(日收益>0); 事件池 t 检验
  基线(全市场随机 t1-PF) 本数据集自算, 并与已知 1.088 对照。

铁律裁定(用户给定): 绑定持有期 PF 基线 1.088 / 理想 1.259。
  PASS_BASE  = PF(ideal_high) ≥ 1.088
  PASS_IDEAL = PF(ideal_high) ≥ 1.259
  另附正式 PROMOTE 门槛: n_test≥200 & 命中倍数≥1.15 & PF≥1.30 & 均值间隙>20bp & t≥2.0
"""
import os, json, math, time, glob
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(BASE, "..", "backtest_cache", "daily")
TEST_START_I = 20240701
TRAIN_END_I = 20240630
MIN_LEN = 60
GAP_RESET = 7          # 相邻行日期差>7天 → 断连跌streak
STREAK_MIN = 7
TP = 0.015          # 次日盘中 scalp 止盈 +1.5%
SL = 0.020          # 次日盘中 scalp 止损 -2.0%


def thr_of(code):
    return 19.8 if code.startswith(("300", "301", "688", "689")) else 9.8


def scalp_ret(cT, hi, lo, cl):
    """真实可达盘中 scalp：次日挂 +TP 止盈 / -SL 止损，触不到则收盘走。"""
    target = cT * (1 + TP)
    stop = cT * (1 - SL)
    if hi >= target:
        return TP
    if lo <= stop:
        return -SL
    return cl / cT - 1.0


def load_close(code, path):
    try:
        # 只取需要的列, C 引擎快
        d = np.genfromtxt(path, delimiter=",", skip_header=1,
                          usecols=(0, 1, 2, 3, 4), dtype=None,
                          encoding="utf-8")
    except Exception:
        return None
    if d is None or len(d) < MIN_LEN:
        return None
    # d: (date:str, open, close, high, low) 注意列序 日期,开盘,收盘,最高,最低
    dates = np.array([str(x[0]).replace("-", "")[:8] for x in d], dtype=np.int64)
    openp = np.array([float(x[1]) for x in d])
    close = np.array([float(x[2]) for x in d])
    high = np.array([float(x[3]) for x in d])
    low = np.array([float(x[4]) for x in d])
    return dates, openp, close, high, low


def compute(path, code):
    ld = load_close(code, path)
    if ld is None:
        return []
    dates, openp, close, high, low = ld
    n = len(close)
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    # 连跌 streak（遇上涨日或交易日历缺口>7天则断）
    streak = np.zeros(n, dtype=np.int64)
    for i in range(1, n):
        gap_ok = (dates[i] - dates[i - 1]) <= GAP_RESET
        if ret[i] < 0 and gap_ok:
            streak[i] = streak[i - 1] + 1
        else:
            streak[i] = 0
    out = []
    for i in range(1, n - 1):
        if streak[i] < STREAK_MIN:
            continue
        if not (ret[i] < 0):
            continue
        if ret[i] <= -thr_of(code) / 100.0 * 0.999:   # T 当日跌停 → 排除
            continue
        j = i + 1
        if dates[j] - dates[i] > GAP_RESET:
            continue
        cT = close[i]
        if cT <= 0 or openp[j] <= 0 or high[j] <= 0 or close[j] <= 0:
            continue
        ideal = high[j] / cT - 1.0
        cap50 = (0.5 * openp[j] + 0.5 * high[j]) / cT - 1.0
        cl = close[j] / cT - 1.0
        sc = scalp_ret(cT, high[j], low[j], close[j])
        di = int(dates[i])
        out.append((di, ideal, cap50, cl, sc))
    return out


def pf_from_daily(daily_rets):
    """daily_rets: list of (date, ret). 无信号日=0。返回 PF/命中率/序列。"""
    from collections import defaultdict
    by = defaultdict(list)
    for d, r in daily_rets:
        by[d].append(r)
    days = sorted(by)
    ser = np.array([float(np.mean(by[d])) for d in days])
    pos = ser[ser > 0]
    neg = ser[ser < 0]
    pf = (pos.sum() / abs(neg.sum())) if neg.size and abs(neg.sum()) > 0 else float("inf")
    hit = float((ser > 0).mean()) if ser.size else float("nan")
    return pf, hit, ser


def summary(events, label):
    arr = np.array(events, dtype=float)
    n = arr.size
    if n < 2:
        return dict(n=n, mean=float("nan"), hit=float("nan"), pf=float("nan"),
                    t=float("nan"))
    mean = arr.mean()
    std = arr.std()
    t = mean / (std / math.sqrt(n)) if std > 0 else float("nan")
    hit = float((arr > 0).mean())
    pos = arr[arr > 0]
    neg = arr[arr < 0]
    pf = (pos.sum() / abs(neg.sum())) if neg.size and abs(neg.sum()) > 0 else float("inf")
    return dict(n=n, mean=mean * 100, hit=hit * 100, pf=pf, t=t,
                worst=arr.min() * 100, p5=np.percentile(arr, 5) * 100)


def main():
    t0 = time.time()
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    files = [f for f in files if not f.endswith(".bak")]
    print(f"[init] {len(files)} 只日线")

    sig_ideal_test, sig_cap_test, sig_close_test, sig_scalp_test = [], [], [], []
    sig_ideal_train, sig_cap_train, sig_close_train, sig_scalp_train = [], [], [], []
    bench_test, bench_train = [], []   # 全市场随机(t1): 任意T→T+1收盘
    bench_scalp_test, bench_scalp_train = [], []
    fi = 0
    for f in files:
        code = os.path.basename(f)[:6]
        rows = compute(f, code)
        fi += 1
        for di, ideal, cap, cl, sc in rows:
            if di >= TEST_START_I:
                sig_ideal_test.append((di, ideal)); sig_cap_test.append((di, cap))
                sig_close_test.append((di, cl)); sig_scalp_test.append((di, sc))
            elif di <= TRAIN_END_I:
                sig_ideal_train.append((di, ideal)); sig_cap_train.append((di, cap))
                sig_close_train.append((di, cl)); sig_scalp_train.append((di, sc))
        # 基准: 该票所有有效 T→T+1（收盘收益 + 同规则 scalp）
        ld = load_close(code, f)
        if ld is None:
            continue
        dates, openp, close, high, low = ld
        n = len(close)
        for i in range(1, n):
            cT = close[i - 1]
            if cT <= 0 or openp[i] <= 0 or high[i] <= 0 or low[i] <= 0 or close[i] <= 0:
                continue
            di = int(dates[i])
            cl = close[i] / cT - 1.0
            sc = scalp_ret(cT, high[i], low[i], close[i])
            if di >= TEST_START_I:
                bench_test.append((di, cl)); bench_scalp_test.append((di, sc))
            elif di <= TRAIN_END_I:
                bench_train.append((di, cl)); bench_scalp_train.append((di, sc))
        if fi % 1000 == 0:
            print(f"  [{fi}/{len(files)}] sig_test={len(sig_ideal_test)}")

    print(f"[load] 用时 {time.time()-t0:.0f}s")

    # 基准 PF (t1)
    bpf_t, bhit_t, _ = pf_from_daily(bench_test)
    bpf_r, bhit_r, _ = pf_from_daily(bench_train)
    print("\n=== 全市场随机基线 (t1, 任意T→T+1收盘, 等权日度) ===")
    print(f"  TEST n={len(bench_test)} PF={bpf_t:.4f} 命中={bhit_t*100:.1f}%  "
          f"(已知基线 1.088, 本数据集自算应接近)")
    print(f"  TRAIN n={len(bench_train)} PF={bpf_r:.4f} 命中={bhit_r*100:.1f}%")

    print("\n=== 连跌≥7日 · 次日盘中高点卖 (TEST ≥ 2024-07-01) ===")
    base_pf = 1.088
    for name, ev in [("ideal_high(盘中最高卖·理想化不可达)", sig_ideal_test),
                     ("capture50(半程捕捉·理想化)", sig_cap_test),
                     ("scalp(+1.5%止盈/-2%止损·真实可达)", sig_scalp_test),
                     ("close(隔夜收盘卖·对照)", sig_close_test)]:
        s = summary([r for _, r in ev], name)
        pf_d, hit_d, _ = pf_from_daily(ev)
        verdict = ""
        if "scalp" in name:
            verdict = (" PASS_BASE(≥1.088)" if pf_d >= 1.088 else " 未过基线") + \
                      (" + PASS_IDEAL(≥1.259)" if pf_d >= 1.259 else "")
        print(f"\n  [{name}] n={s['n']} 事件均值={s['mean']:+.3f}% 命中={s['hit']:.1f}% "
              f"日度PF={pf_d:.4f}{verdict}")
        print(f"     t={s['t']:.2f}  最差={s['worst']:+.2f}%  左尾5%={s['p5']:+.2f}%  "
              f"vs基线PF {base_pf} → 倍数 {pf_d/base_pf:.3f}")

    # 公平对照: 同一条 scalp 规则套在全市场随机上
    bp_s, bh_s, _ = pf_from_daily(bench_scalp_test)
    bp_c, bh_c, _ = pf_from_daily(bench_test)
    print(f"\n=== 同规则 scalp 公平对照 (全市场随机, +1.5%/-2%) ===")
    print(f"  随机 scalp PF={bp_s:.4f} 命中={bh_s*100:.1f}%  | 随机收盘 PF={bp_c:.4f} 命中={bh_c*100:.1f}%")
    ss = summary([r for _, r in sig_scalp_test], "sig_scalp")
    pf_s, _, _ = pf_from_daily(sig_scalp_test)
    print(f"  信号 scalp PF={pf_s:.4f} 命中={ss['hit']:.1f}%  → PF比(信号/随机)={pf_s/bp_s:.3f}")

    print("\n=== TRAIN(≤2024-06-30) 对照漂移 ===")
    for name, ev in [("ideal_high", sig_ideal_train), ("capture50", sig_cap_train),
                     ("scalp", sig_scalp_train), ("close", sig_close_train)]:
        s = summary([r for _, r in ev], name)
        pf_d, _, _ = pf_from_daily(ev)
        print(f"  [{name}] n={s['n']} 均值={s['mean']:+.3f}% 日度PF={pf_d:.4f}")

    print(f"\n[done] t={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
