# -*- coding: utf-8 -*-
"""
research/oos_lhb_signal.py — 龙虎榜(聪明钱) × R31 构造突破 组合 OOS 测评（圣杯对齐门）
================================================================================
新维度：日线 OHLC 之外、真实可得的特征组合——
  (A) 龙虎榜"聪明钱"标签：T-1 上榜（被机构/游资交易）且被买上 → 强方向性（抬升次日涨停率）
  (B) R31 构造突破形态：T 日横盘后放量/站上MA20 突破（已在 oos_eval_harness 证有正 alpha）

假设：龙虎榜信号是"彩票型"（高涨停率但负期望），R31 是"正 alpha 但低涨停率"。
      二者交集（聪明钱埋伏 + 技术上未耗尽、仍构造中）可能同时获得方向性 + 正期望。

数据：backtest_cache/f10_cache/lhb_*.json + backtest_cache/daily/*.csv（与门 B 同口径）
口径：point-in-time；可买宇宙 = T 未涨停/未跌停；标签 = T+1 涨停(f1>=LIMIT)
      基准 = 同窗口可买宇宙次日涨停率（period-matched）；同日市场 = 全市场等权均值
门：adjudicate_grail（lift≥1.5 ∧ 超额vs市>0 ∧ t_vs市≥2.0 ∧ t_vs空≥2.0 ∧ PurgedCV≥0.60 ∧ n≥200）
绝不降任何闸门；仅诚实测评，不动生产门 B / 纪律红线。
"""
from __future__ import annotations
import json, math, time
import numpy as np
import pandas as pd
import oospf_validator as V

LHB_DIR = V.CACHE / "f10_cache"
DAILY = V.DAILY
LIMIT = V.LIMIT
MIN_LEN = V.MIN_LEN
CONS_STD = V.CONS_STD


def rolling_std(a, w):
    s = pd.Series(a, dtype=float)
    return s.rolling(w, min_periods=w).std().to_numpy()


def load_lhb():
    m = {}
    for f in sorted(LHB_DIR.glob("lhb_20*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        dt = int(d.get("date"))
        stocks = d.get("stocks", {})
        m[dt] = {c: v / 100.0 for c, v in stocks.items() if isinstance(v, (int, float))}
    return m


def main():
    t0 = time.time()
    lhb = load_lhb()
    lhb_dates = set(lhb.keys())
    w_start, w_end = min(lhb_dates), max(lhb_dates)
    print(f"[lhb] 文件 {len(lhb)}；窗口 {w_start}..{w_end}")

    # 变体：(name, kind, lhb_cond)  kind: 'lhb' / 'r31' / 'both'
    VARIANTS = [
        ("LHB_pos",        "lhb",  lambda r: r > 0),
        ("R31_base",       "r31",  None),
        ("LHB_pos+R31",    "both", lambda r: r > 0),
    ]
    ev = {k[0]: {"f1": [], "mkt": [], "date": []} for k in VARIANTS}
    base_tot = 0
    base_hit = 0
    mkt_sum = {}
    mkt_cnt = {}

    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float)
        n = len(close)
        dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        f1 = np.full(n, np.nan); f1[:-1] = close[1:] / close[:-1] - 1.0
        code = f.name[:-4]
        # R31_base 布尔（横盘后突破）
        cons = rolling_std(ret, 10); cons = np.roll(cons, 1); cons[0] = np.nan
        consol = (~np.isnan(cons)) & (cons < CONS_STD)
        r31 = consol & (ret > 0.03)
        # 同日市场累计（窗口内）
        for i in range(1, n):
            di = dates[i]
            if w_start <= di <= w_end and not np.isnan(ret[i]):
                mkt_sum[di] = mkt_sum.get(di, 0.0) + ret[i]
                mkt_cnt[di] = mkt_cnt.get(di, 0) + 1
        for i in range(1, n - 1):
            di = dates[i]
            if not (w_start <= di <= w_end):
                continue
            if np.isnan(ret[i]) or np.isnan(f1[i]):
                continue
            buyable = (ret[i] < LIMIT) and (ret[i] > -LIMIT)
            if not buyable:
                continue
            zt_next = f1[i] >= LIMIT
            base_tot += 1
            if zt_next:
                base_hit += 1
            dim1 = dates[i - 1]
            lhb_prev = lhb.get(dim1)
            r_lhb = lhb_prev.get(code) if lhb_prev else None
            mc = mkt_cnt.get(di, 0)
            mkt = (mkt_sum.get(di, 0.0) / mc) if mc > 0 else float("nan")
            for name, kind, cond in VARIANTS:
                if kind == "lhb":
                    if r_lhb is None or not cond(r_lhb):
                        continue
                elif kind == "r31":
                    if not r31[i]:
                        continue
                else:  # both
                    if r_lhb is None or not cond(r_lhb) or not r31[i]:
                        continue
                ev[name]["f1"].append(f1[i])
                ev[name]["mkt"].append(mkt)
                ev[name]["date"].append(di)
        if (fi + 1) % 1000 == 0:
            print(f"  [load] {fi+1}/{len(files)}  t={time.time()-t0:.0f}s")

    base_rate = (base_hit / base_tot) if base_tot else float("nan")
    print(f"\n[基准] 窗口可买宇宙次日涨停率 = {base_rate*100:.3f}%  (n={base_tot})")
    print("\n=== 龙虎榜 × R31 组合 OOS（圣杯对齐门）===")
    print("%-14s %8s %10s %8s %11s %12s %9s %6s %8s %-14s" %
          ("变体", "n", "次日涨停%", "lift", "信号日均值%", "超额vs市bp", "t_vs市", "t_vs空", "PurgedCV", "裁定"))
    for name, _, _ in VARIANTS:
        f1a = np.array(ev[name]["f1"], dtype=float)
        mka = np.array(ev[name]["mkt"], dtype=float)
        nn = int(f1a.size)
        sig_zt = float((f1a >= LIMIT).mean()) if nn else float("nan")
        lift = (sig_zt / base_rate) if (base_rate and base_rate > 0) else float("nan")
        mean_f1 = float(f1a.mean()) if nn else float("nan")
        ex = (f1a - mka)[~np.isnan(f1a - mka)]
        ex_mkt = float(ex.mean() * 100) if ex.size else float("nan")
        t_vm = float(ex.mean() / ex.std() * math.sqrt(ex.size)) if ex.size > 2 and ex.std() > 0 else float("nan")
        t_vc = float(f1a.mean() / f1a.std() * math.sqrt(f1a.size)) if nn > 2 and f1a.std() > 0 else float("nan")
        order = np.argsort(np.array(ev[name]["date"], dtype=int))
        f1_ord = f1a[order]
        pcv = V.purged_cv_stats(f1_ord, 1, V.PURGED_CV_N_SPLITS, 1)["pos_frac"] if f1_ord.size > 10 else float("nan")
        ok = (lift == lift and lift >= 1.5 and ex_mkt == ex_mkt and ex_mkt > 0
              and t_vm == t_vm and t_vm >= 2.0 and t_vc == t_vc and t_vc >= 2.0
              and pcv == pcv and pcv >= 0.60 and nn >= 200)
        verdict = "PROMOTE_GRAIL" if ok else "REJECT_GRAIL"
        print("%-14s %8d %10.3f %8.3f %11.3f %12s %9s %6s %8s %-14s" %
              (name, nn, sig_zt * 100, lift if lift == lift else 0, mean_f1 * 100,
               ("%.2f" % ex_mkt if ex_mkt == ex_mkt else "nan"),
               ("%.2f" % t_vm if t_vm == t_vm else "nan"),
               ("%.2f" % t_vc if t_vc == t_vc else "nan"),
               ("%.2f" % pcv if pcv == pcv else "nan"), verdict))
    print("  解读：若 'LHB_pos+R31' 同时保留 lift 与正 alpha，则证明'聪明钱+构造中'是可交易 edge；")
    print("        否则确认龙虎榜为彩票型、R31 为低命中正 alpha，二者交集仍不足以过完整门。")
    print(f"  [done] t={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
