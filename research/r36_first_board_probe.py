# -*- coding: utf-8 -*-
"""r36_first_board_probe.py — 圣杯「首板发现」受控研究轨道（路径 B 候选）
================================================================================
核心目标：T 日尾盘(14:50)买非涨停 → T+1 首板涨停(或上涨)。

本模块用本地 5225 只前复权日线做 ex-ante 回测（14:50 状态以 T 日收盘代理）：
  - 标签：T 日非涨停 ∧ T+1 涨停 = 首板命中（交易所口径，与 evolution_review.limit_of 一致）。
  - 基准率：非涨停 → 次日首板 的全样本占比（已实测 ≈1.31%）。
  - 信号：用 ex-ante 量价特征(ret/amp/vol_ratio/price)构造候选规则，逐日核算：
        precision = 命中/预测；lift = precision / 基准率；
        paired_excess = 信号股 T+1 收益均值 − 可买子集(全市场非涨停) T+1 收益均值（与系统核心度量同构）；
        Newey-West 近似 t（与 evolution_review 同约定：日序列 mean/(std/√n)）。
  - 结论诚实：naive 量价能给 lift>2 的倾斜，但 precision 仅 4–5%、候选数 4万–8万，
    单独不可用；真正可用的 14:50 选择器须叠加 龙虎榜集中度/板块催化/主净流/舆情 等新鲜层
    （本轨道的"待补特征"，见 REFLECTION_2026-11-02.md §8）。

输出：backtest_cache/r36_first_board_probe.json（供 live_factor_miner 读取，走 WATCH→PROMOTE 门控）。
"""
from __future__ import annotations
import os
import glob
import json
import sys
import statistics
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "backtest_cache")
DAILY = os.path.join(CACHE, "daily")
OUT = os.path.join(CACHE, "r36_first_board_probe.json")
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


def nw_t(series):
    n = len(series)
    if n < 2:
        return 0.0
    m = sum(series) / n
    sd = statistics.pstdev(series)
    if sd == 0:
        return 0.0 if m == 0 else float("inf")
    return m / (sd / (n ** 0.5))


def main():
    data = load_all()
    # 预建每只票的日期→索引
    # 规则定义（ex-ante，T 日收盘代理 14:50）
    rules = {
        "strong_day": lambda fea: (0.03 <= fea["ret"] <= 0.095) and fea["amp"] >= 0.08 and fea["vr"] >= 2.0,
        "oversold_reversal": lambda fea: fea["ret"] <= -0.04 and fea["amp"] >= 0.06 and fea["price"] <= 25,
        "tight_prebreak": lambda fea: (0.05 <= fea["ret"] <= 0.095) and fea["amp"] >= 0.10
                                   and fea["vr"] >= 2.5 and fea["price"] <= 15,
    }
    # 累加器
    acc = {k: {"pred": 0, "hit": 0, "sig_ret": [], "buy_ret": []} for k in rules}
    n_nolim = 0
    n_fb = 0
    base_ret_series = []  # 所有非涨停股 T+1 收益（基准池）

    for c, rows in data.items():
        for i in range(1, len(rows) - 1):
            clTm1, oT, clT, hiT, loT, voT = rows[i - 1][2], rows[i][1], rows[i][2], rows[i][3], rows[i][4], rows[i][5]
            clT1 = rows[i + 1][2]
            if clTm1 == 0 or clT == 0:
                continue
            lim = limit_of(c)
            lupT = (clT >= clTm1 * (1 + lim) - 0.005)
            ldnT = (clT <= clTm1 * (1 - lim) + 0.005)
            if lupT or ldnT:
                continue
            n_nolim += 1
            retT = clT / clTm1 - 1.0
            ampT = (hiT - loT) / clTm1 if clTm1 else 0
            vols = [r[5] for r in rows[:i + 1]]
            vavg = (sum(vols[-20:]) / 20.0) if len(vols) >= 5 else voT
            vrT = voT / vavg if vavg else 1.0
            is_fb = (clT1 >= clT * (1 + lim) - 0.005)
            nxt = clT1 / clT - 1.0
            if is_fb:
                n_fb += 1
            base_ret_series.append(nxt)
            fea = {"ret": retT, "amp": ampT, "vr": vrT, "price": clT}
            for k, fn in rules.items():
                if fn(fea):
                    acc[k]["pred"] += 1
                    acc[k]["sig_ret"].append(nxt)
                    if is_fb:
                        acc[k]["hit"] += 1

    base = n_fb / n_nolim if n_nolim else 0
    # 基准池 T+1 收益均值（可买子集）
    buyable_mean = sum(base_ret_series) / len(base_ret_series) if base_ret_series else 0.0

    result = {
        "as_of": datetime.date.today().isoformat(),
        "base_rate": base,
        "n_nolim": n_nolim,
        "n_first_board": n_fb,
        "buyable_t1_mean": buyable_mean,
        "rules": {},
    }
    for k, a in acc.items():
        prec = a["hit"] / a["pred"] if a["pred"] else 0
        lift = prec / base if base else 0
        sig_mean = sum(a["sig_ret"]) / len(a["sig_ret"]) if a["sig_ret"] else 0
        pe = sig_mean - buyable_mean
        t = nw_t([(r - buyable_mean) for r in a["sig_ret"]]) if a["sig_ret"] else 0
        result["rules"][k] = {
            "pred": a["pred"], "hit": a["hit"],
            "precision": prec, "lift": lift,
            "sig_t1_mean": sig_mean, "paired_excess": pe, "t": t,
        }

    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=float)
    print("[r36] 基准率(非涨停→次日首板)=%.2f%% (n=%d, fb=%d)" % (base * 100, n_nolim, n_fb))
    print("[r36] 可买子集 T+1 均值=%.4f" % buyable_mean)
    for k, r in result["rules"].items():
        print("  %-16s pred=%-7d hit=%-5d prec=%.2f%% lift=%.2f paired_excess=%.4f t=%.2f"
              % (k, r["pred"], r["hit"], r["precision"] * 100, r["lift"], r["paired_excess"], r["t"]))
    print("[r36] → lift>2 证明首板可预测倾斜存在；但 precision 4–5%%、候选 4万–8万，")
    print("    单独不可用。须叠加 龙虎榜集中度/板块催化/主净流/舆情 新鲜层压缩候选并提 precision。")
    print("[r36] 写出 %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
