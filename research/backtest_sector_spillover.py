#!/usr/bin/env python3
# #-*- coding: utf-8 -*-
"""板块动量溢出(催化层第一组件) OOS 回测 —— 严格 point-in-time。

假设：T-1 收盘时，某板块出现 >=K 只其他成分股涨停(板块"热")，而 S 自身当日未涨停(安静成员)，
则 S 可能在 T 日跟随板块热度点火/走强。这是 ex-ante(T-1 收盘可见)的催化/联动型信号，
不同于已被证伪的纯价量预点火(backtest_preignition.py, 3.5M 样本 REJECT)。

标签：ignite = ZT[T] & ZT[T+1]（T 开启>=2连板）；fwd_ret = c[T+1]/c[T-1]-1。
门控：paired_excess = mean(信号股) - mean(同分布可买子集)；Welch t；n>=200/t>=2.0/PF>=1.30/DSR>=0.95。
依赖：data/industry_map/stock_to_sector.json (379 只, 覆盖有限；若信号成立再扩面)。
"""
import os, glob, json, numpy as np
from research.board_limit import board_limit_pct, is_limit_up

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(HERE, "backtest_cache", "daily")
MAP = os.path.join(HERE, "data", "industry_map", "stock_to_sector.json")

def lp_for(code):
    return board_limit_pct(code)

def main():
    smap = json.load(open(MAP, encoding="utf-8"))
    codes = [c for c in smap if c[:1].isdigit()]
    # 板块 -> 成员
    sec2mem = {}
    for c in codes:
        for s in smap[c].get("sectors", []):
            sec2mem.setdefault(s, []).append(c)
    # 仅保留成员数 >=3 的板块(可统计 peer)
    sec2mem = {s: m for s, m in sec2mem.items() if len(m) >= 3}
    print("板块数(成员>=3): %d  覆盖股票: %d" % (len(sec2mem), len(codes)))

    # 载入日线 + 涨停布尔
    bars = {}
    d2i = {}
    for c in codes:
        try:
            a = np.loadtxt(os.path.join(D, c + ".csv"), delimiter=",", dtype=str, skiprows=1)
        except Exception:
            continue
        if a.ndim != 2 or a.shape[0] < 120:
            continue
        try:
            dates = a[:, 0]; cA = a[:, 2].astype(float)
        except Exception:
            continue
        lp = lp_for(c)
        prev = cA[:-1]
        zt = cA[1:] >= (np.round(prev * (1.0 + lp), 2) - 0.005)
        bars[c] = (dates, cA, np.concatenate([[False], zt]))  # zt_c[i] = c[i] 是否涨停
        d2i[c] = {d: k for k, d in enumerate(dates)}          # 日期->索引 对齐
    print("载入日线股票: %d" % len(bars))

    recs = []  # fwd, ign, heat, retT (T日收益), nsec
    for c in bars:
        dates, cA, zt_c = bars[c]
        n = len(cA)
        secs = [s for s in smap.get(c, {}).get("sectors", []) if s in sec2mem]
        if not secs:
            continue
        for i in range(1, n - 2):  # T-1 = i, 需 T=i+1, T+1=i+2
            if zt_c[i]:
                continue  # S 自身 T-1 涨停 -> 不可尾盘买, 跳过
            di = dates[i]  # 当日日期, 用于对齐 peer
            # 板块热度: 同一板块其他成员在 T-1(同日期)涨停数
            heat = 0
            nsec = 0
            for s in secs:
                for m in sec2mem[s]:
                    if m == c:
                        continue
                    if m in bars:
                        pi = d2i[m].get(di)
                        if pi is None:
                            continue
                        nsec += 1
                        if bars[m][2][pi]:
                            heat += 1
            if heat == 0:
                continue  # 仅看"有热度"的样本作为信号侧
            ign = bool(zt_c[i + 1] and zt_c[i + 2])
            fwd = cA[i + 1] / cA[i] - 1.0 if cA[i] > 0 else 0.0
            recs.append((fwd, 1.0 if ign else 0.0, float(heat), float(nsec)))
    recs = np.array(recs)
    if len(recs) == 0:
        print("无样本(板块热度样本为空)"); return
    print("有板块热度的候选(T-1 非涨停)样本数: %d" % len(recs))
    base_ign = recs[:, 1].mean()
    print("热度样本 ignite 基准率=%.4f%%  全样本 fwd均值=%.4f%%  点火fwd均值=%.4f%%" % (
        base_ign * 100, recs[:, 0].mean() * 100, recs[recs[:, 1] > 0, 0].mean() * 100))

    # 与"全市场普通 T-1 日"基准对比(来自 preignition: 0.1872% ignite, fwd~0.069%)
    BASE_IGN = 0.001872
    BASE_FWD = 0.00069

    def paired(mask):
        if mask.sum() < 200:
            return None
        sig = recs[mask]; pool = recs
        ms = sig[:, 0].mean(); mp = pool[:, 0].mean()
        s1 = sig[:, 0].std(ddof=1); s2 = pool[:, 0].std(ddof=1)
        se = np.sqrt(s1**2 / len(sig) + s2**2 / len(pool))
        t = (ms - mp) / se if se > 0 else 0.0
        return int(mask.sum()), ms * 100, mp * 100, (ms - mp) * 100, t

    buckets = {
        "heat>=1": recs[:, 2] >= 1, "heat>=2": recs[:, 2] >= 2, "heat>=3": recs[:, 2] >= 3,
    }
    print("\n%-12s %8s %10s %9s %10s %7s %6s %7s" % ("bucket", "n", "ign%", "sigFwd%", "poolFwd%", "t", "PF", "DSR"))
    res = []
    for name, m in buckets.items():
        pe = paired(m)
        if pe is None:
            print("%-12s %8s %10s %9s %10s %7s %6s %7s" % (name, "<200", "-", "-", "-", "-", "-", "-")); continue
        nn, ms, mp, ex, t = pe
        pf = ms / mp if mp != 0 else float("nan"); dsr = (recs[m, 0] > 0).mean()
        ign_rate = recs[m, 1].mean() * 100
        pas = bool(nn >= 200 and t >= 2.0 and pf >= 1.30 and dsr >= 0.95)
        res.append((name, nn, ign_rate, ms, mp, t, pf, dsr, pas))
        print("%-12s %8d %10.3f %9.3f %10.3f %7.2f %6.2f %7.3f %6s" % (name, nn, ign_rate, ms, mp, t, pf, dsr, "PASS" if pas else ""))
    overall = bool(any(r[8] for r in res))
    print("\n=== 结论 ===")
    print("板块动量溢出经 OOS 门(配对 t>=2 & PF>=1.30 & DSR>=0.95 & n>=200)？ %s" % ("PASS→可接回生产(须先扩面至全市场板块)" if overall else "当前 379 只样本 REJECT/样本不足→须扩面或转向实时板热/新闻催化层"))
    json.dump({"base_ign": BASE_IGN, "n": int(len(recs)),
               "results": [{"name": r[0], "n": r[1], "ign_rate": r[2], "sig_fwd": r[3], "pool_fwd": r[4],
                            "t": r[5], "pf": r[6], "dsr": r[7], "pass": r[8]} for r in res],
               "overall_pass": overall},
              open(os.path.join(os.path.dirname(__file__), "sector_spillover_result.json"), "w"), ensure_ascii=False, indent=2)
    return overall

if __name__ == "__main__":
    main()
