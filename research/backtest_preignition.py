#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""预点火(连续涨停启动前一日)技术性 edge 回测 —— 全向量化版（cumsum 滚动）。

标签(严格 point-in-time)：
  T-1 = 候选日(尾盘可买，必须非涨停、非跌停)。
  ignite = ZT[T] & ZT[T+1]   (T 日开启连续 >=2 涨停)
  fwd_ret = c[T+1]/c[T-1]-1  (T-1 尾盘买入、T+1 收盘卖)
门控：paired_excess = mean(信号股) - mean(全可买子集)；Welch t；n>=200/t>=2.0/PF>=1.30/DSR>=0.95。
"""
import os, glob, json, numpy as np
from research.board_limit import board_limit_pct, is_limit_up

D = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backtest_cache", "daily")

def lp_for(code):
    return board_limit_pct(code)

def load_all():
    files = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(D, "*.csv"))]
    files = [f for f in files if f[:1].isdigit()]
    data = {}
    for code in files:
        try:
            a = np.loadtxt(os.path.join(D, code + ".csv"), delimiter=",", dtype=str, skiprows=1)
        except Exception:
            continue
        if a.ndim != 2 or a.shape[0] < 120:
            continue
        try:
            o = a[:, 1].astype(float); c = a[:, 2].astype(float)
            h = a[:, 3].astype(float); l = a[:, 4].astype(float); v = a[:, 5].astype(float)
        except Exception:
            continue
        data[code] = (o, c, h, l, v)
    return data

def rolling_mean(x, w):
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    for i in range(w - 1, len(x)):
        out[i] = (cs[i + 1] - cs[i - w + 1]) / w
    return out

def main():
    data = load_all()
    print("载入股票数: %d" % len(data))
    cols = {"fwd": [], "ign": [], "volr": [], "ret5": [], "ret20": [], "ma": [], "cpos": [], "gap": []}
    n_total = 0
    for code, (o, c, h, l, v) in data.items():
        n = len(c)
        if n < 123:
            continue
        lp = lp_for(code)
        prev = c[:-1]
        zt = c[1:] >= (np.round(prev * (1.0 + lp), 2) - 0.005)
        dt = c[1:] <= (np.round(prev * (1.0 - lp), 2) + 0.005)
        # 对齐到 c 索引：zt[i] 表示 c[i+1] 是否涨停；dt 同理
        zt_c = np.concatenate([[False], zt])   # 长度 n，zt_c[i]=c[i] 是否涨停(基于 c[i-1]), i>=1
        dt_c = np.concatenate([[False], dt])
        # 候选 T-1 = 索引 i，需 i>=1, i+2<=n-1
        # 非涨停非跌停
        norm = (~zt_c) & (~dt_c)
        # 需 T(i+1),T+1(i+2) 存在 → i <= n-3
        valid = norm & (np.arange(n) >= 1) & (np.arange(n) <= n - 3)
        idx = np.where(valid)[0]
        if len(idx) == 0:
            continue
        ig = (zt_c[idx + 1] & zt_c[idx + 2]).astype(float)
        fwd = c[idx + 1] / c[idx] - 1.0
        # 特征
        rm5 = rolling_mean(v, 5)
        volr = v / np.where(rm5 > 0, rm5, np.nan)
        ret5 = c / np.roll(c, 5) - 1.0
        ret20 = c / np.roll(c, 20) - 1.0
        ma5 = rolling_mean(c, 5); ma20 = rolling_mean(c, 20)
        ma = ma5 / ma20 - 1.0
        cpos = (c - l) / (h - l + 1e-9)
        gap = o / np.roll(c, 1) - 1.0
        cols["fwd"].append(fwd); cols["ign"].append(ig)
        cols["volr"].append(volr[idx]); cols["ret5"].append(ret5[idx])
        cols["ret20"].append(ret20[idx]); cols["ma"].append(ma[idx])
        cols["cpos"].append(cpos[idx]); cols["gap"].append(gap[idx])
        n_total += len(idx)
    for k in cols:
        cols[k] = np.concatenate(cols[k]) if cols[k] else np.array([])
    print("候选(T-1 非涨停非跌停)样本数: %d" % n_total)
    base = cols["ign"].mean()
    print("基准: 任意普通日 T-1 之后开启>=2连板比例 = %.4f%%  (n=%d)" % (base * 100, n_total))
    print("全样本 fwd均值=%.4f%%  点火样本 fwd均值=%.4f%%" % (
        cols["fwd"].mean() * 100, cols["fwd"][cols["ign"] > 0].mean() * 100))

    recs = np.column_stack([cols["fwd"], cols["ign"], cols["volr"], cols["ret5"],
                            cols["ret20"], cols["ma"], cols["cpos"], cols["gap"]])

    def paired_excess(mask):
        if mask.sum() < 200:
            return None
        sig = recs[mask]; pool = recs
        ms = sig[:, 0].mean(); mp = pool[:, 0].mean()
        s1 = sig[:, 0].std(ddof=1); s2 = pool[:, 0].std(ddof=1)
        se = np.sqrt(s1**2 / len(sig) + s2**2 / len(pool))
        t = (ms - mp) / se if se > 0 else 0.0
        return int(mask.sum()), ms * 100, mp * 100, (ms - mp) * 100, t

    filters = {
        "volr>2": recs[:, 2] > 2.0, "volr>1.5": recs[:, 2] > 1.5,
        "ret5>5%": recs[:, 3] > 0.05, "ret5>3%": recs[:, 3] > 0.03,
        "ret20>10%": recs[:, 4] > 0.10, "ma>0": recs[:, 5] > 0.0,
        "cpos>0.8": recs[:, 6] > 0.8, "gap>2%": recs[:, 7] > 0.02,
        "volr>1.5&ret5>3%": (recs[:, 2] > 1.5) & (recs[:, 3] > 0.03),
        "volr>2&ret5>5%": (recs[:, 2] > 2.0) & (recs[:, 3] > 0.05),
    }
    print("\n%-22s %8s %9s %9s %9s %7s %6s %6s %6s" % ("filter", "n", "sigRet%", "pool%", "exBP", "t", "PF", "DSR", "PASS"))
    results = []
    for name, m in filters.items():
        pe = paired_excess(m)
        if pe is None:
            print("%-22s %8s %9s %9s %9s %7s %6s %6s %6s" % (name, "<200", "-", "-", "-", "-", "-", "-", "NS"))
            continue
        nn, ms, mp, ex, t = pe
        pf = ms / mp if mp != 0 else float("nan"); dsr = (recs[m, 0] > 0).mean()
        pas = bool(nn >= 200 and t >= 2.0 and pf >= 1.30 and dsr >= 0.95)
        results.append((name, nn, ms, mp, ex, t, pf, dsr, pas))
        print("%-22s %8d %9.3f %9.3f %9.2f %7.2f %6.2f %6.3f %6s" % (name, nn, ms, mp, ex, t, pf, dsr, "PASS" if pas else ""))
    overall = bool(any(r[8] for r in results))
    print("\n=== 结论 ===")
    print("技术性滤波器通过 OOS 门？ %s" % ("是→可接回生产" if overall else "否→REJECT：纯技术不可预测,必须走催化/事件层"))
    json.dump({"base_rate": float(base), "n": int(n_total),
               "results": [{"name": r[0], "n": r[1], "sig_ret": r[2], "pool_ret": r[3],
                            "excess_bp": r[4], "t": r[5], "pf": r[6], "dsr": r[7], "pass": r[8]} for r in results],
               "overall_pass": bool(overall)},
              open(os.path.join(os.path.dirname(__file__), "preignition_result.json"), "w"), ensure_ascii=False, indent=2)
    return overall

if __name__ == "__main__":
    main()
