# -*- coding: utf-8 -*-
"""
grail_validation.py — 圣杯可证伪验证(2026-11-03)
================================================
对多历史交易日，比较各离线信号在「T日非涨停」候选上的 T+1 真实表现：
  - lim_up_open  : T+1 开盘价 >= round(T收盘*(1+限幅),2)-0.005  （开盘涨停）
  - lim_up_close : T+1 收盘价 >= 同上                          （收盘涨停 = 严格 grail_hit）
  - gap_up_open  : T+1 开盘价 > T 收盘价                        （高开）
  - mean T+1 收益 : (T+1收盘 / T收盘) - 1
信号：gapup(次日高开模型) / nol(次日开盘涨停模型) / main(主触板模型) / random(随机非涨停基线)
方法：每个 code×date 只算一次 features(compute_features asof=date)，再套用三个模型权重向量。
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
import numpy as np
import signal_model as sm

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtest_cache", "daily")
N = 20
NDATES = 24
SEED = 0


def fwd(fdict, feats, means, stds, w, b):
    if fdict is None:
        return None
    x = np.array([fdict.get(f, np.nan) for f in feats], dtype=float)
    if np.any(np.isnan(x)):
        return None
    z = (x - means) / stds
    return float(1.0 / (1.0 + np.exp(-(z @ w + b))))


def main():
    signals = {
        "gapup": (sm.GAPUP_FEATURES, sm.GAPUP_MEANS, sm.GAPUP_STDS, sm.GAPUP_W, sm.GAPUP_B),
        "nol":   (sm.NOL_FEATURES, sm.NOL_MEANS, sm.NOL_STDS, sm.NOL_W, sm.NOL_B),
        "main":  (sm.FEATURES, sm.MEANS, sm.STDS, sm.W, sm.B),
    }
    priors = {"gapup": 0.383, "nol": sm.NOL_BASE_RATE, "main": None}

    base = sm._load_bars_arr("000001")
    if base is None:
        print("no base bars"); return
    trading = [str(x) for x in base[0]]
    pairs = [(trading[i], trading[i + 1]) for i in range(len(trading) - 2, -1, -1)]  # 最近在前
    test_dates = pairs[:NDATES][::-1]   # 取最近 NDATES 个交易日对，按时间升序
    ndates = len(test_dates)

    codes = [f[:-4] for f in os.listdir(D) if f.endswith(".csv")]
    agg = {k: {"ho": 0, "hc": 0, "go": 0, "ret": []} for k in signals}
    agg["random"] = {"ho": 0, "hc": 0, "go": 0, "ret": []}

    for (d, nd) in test_dates:
        cands = []
        for code in codes:
            b = sm._load_bars_arr(code)
            if b is None:
                continue
            dates, o, c, h, l, v = b
            i_d = int(np.where(dates == d)[0][0]) if (dates == d).any() else -1
            i_nd = int(np.where(dates == nd)[0][0]) if (dates == nd).any() else -1
            if i_d < 1 or i_nd < 0:
                continue
            c_d = c[i_d]; pc_d = c[i_d - 1]
            if not pc_d:
                continue
            lim = sm.board_limit(code)
            at_limit_d = c_d >= pc_d * (1 + lim) - 0.005
            if at_limit_d:
                continue
            o_nd = o[i_nd]; c_nd = c[i_nd]
            thr = round(pc_d * (1 + lim), 2) - 0.005
            lim_up_open = o_nd >= thr
            lim_up_close = c_nd >= thr
            gap_up_open = o_nd > c_d
            ret_t1 = c_nd / c_d - 1.0
            fdict = sm.compute_features(code, asof=d)
            lp = sm._limit_prox(code, asof=d)
            full = dict(fdict) if fdict else None
            if full is not None and lp is not None:
                full["limit_prox"] = lp
            scores = {}
            for sk, (fe, me, st, w, bb) in signals.items():
                scores[sk] = fwd(full, fe, me, st, w, bb)
            cands.append((code, scores, (lim_up_open, lim_up_close, gap_up_open, ret_t1)))
        for sk in signals:
            prior = priors[sk]
            pool = [x for x in cands if x[1].get(sk) is not None and (prior is None or x[1][sk] > prior)]
            pool.sort(key=lambda x: x[1][sk], reverse=True)
            for x in pool[:N]:
                lo, lc, go, rt = x[2]
                a = agg[sk]; a["ho"] += int(lo); a["hc"] += int(lc); a["go"] += int(go); a["ret"].append(rt)
        if len(cands) >= N:
            rng = np.random.default_rng(SEED)
            for _ in range(20):
                sel = rng.choice(len(cands), size=N, replace=False)
                for j in sel:
                    lo, lc, go, rt = cands[j][2]
                    a = agg["random"]; a["ho"] += int(lo); a["hc"] += int(lc); a["go"] += int(go); a["ret"].append(rt)

    print("交易日=%d  N(每信号/日)=%d  随机抽样=20次" % (ndates, N))
    print("%-8s %9s %10s %10s %10s %11s" % ("signal", "n", "开盘涨停%", "收盘涨停%", "高开%", "均T1收益%"))
    for k in list(signals) + ["random"]:
        a = agg[k]; tot = len(a["ret"]) or 1
        print("%-8s %9d %9.3f %9.3f %9.3f %10.3f" % (
            k, tot, 100 * a["ho"] / tot, 100 * a["hc"] / tot, 100 * a["go"] / tot,
            100 * float(np.mean(a["ret"]))))


if __name__ == "__main__":
    t0 = time.time()
    main()
    print("elapsed %.1fs" % (time.time() - t0))
