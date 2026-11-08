# -*- coding: utf-8 -*-
"""r36_lhb_conc_test.py — Task1: 龙虎榜买卖席位集中度 vs r36 严格首板标签 (OOS)

与 r36_first_board_probe.py 完全相同的 严格标签 口径：
    T 日非涨停 ∧ T+1 涨停 = 首板命中；基准率(非涨停→次日首板) ≈1.31%。
但本脚本把特征换成 龙虎榜席位买卖集中度（来自 backtest_cache/intel/intel_features.csv.gz
中新加的 lhb_buy_conc / lhb_buy_gt_sell），且这些特征已是 lag-1 对齐
（T-1 收盘后榜单 → T 日 14:50 可用），无未来泄漏。

度量（与 r36 / evolution_review 同约定）：
    precision = 命中/预测
    lift      = precision / 基准率
    paired_excess = 信号股 T+1 收益均值 − 全市场非涨停子集 T+1 收益均值
    t         = Newey-West 近似：mean(diff)/(std(diff)/√n)
门控（与系统一致）：
    PROMOTE  若 precision≥8% 且 paired_excess_t≥2.0
    否则     REJECT（无任何正信号）/ WATCH（有正倾斜但未达 gate）

用法：
    python research/r36_lhb_conc_test.py
"""
from __future__ import annotations
import os
import glob
import gzip
import json
import sys
import datetime
import statistics

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
CACHE = os.path.join(PROJ, "backtest_cache")
DAILY = os.path.join(CACHE, "daily")
FEAT = os.path.join(CACHE, "intel", "intel_features.csv.gz")
EDGE = os.path.join(HERE, "intel_edge_test.json")
OUT = os.path.join(CACHE, "r36_lhb_conc_test.json")
sys.path.insert(0, PROJ)
from evolution_review import limit_of  # 与 r36 完全相同的涨跌停口径


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
    # ---- 特征表（lag-1 对齐）----
    feat = pd.read_csv(FEAT, compression="gzip", dtype={"code": str})
    feat["code"] = feat["code"].str.zfill(6)
    need = ["lhb_buy_conc", "lhb_buy_gt_sell"]
    missing = [c for c in need if c not in feat.columns]
    if missing:
        print(f"[ERR] intel_features.csv.gz 缺少 {missing}，先重跑 research/intel_features.py", file=sys.stderr)
        return 1
    # (code,date) -> 行
    fmap = feat.set_index(["code", "date"]).to_dict("index")

    data = load_all()

    # ---- 累加器 ----
    # 全市场（与 r36 同口径，用于基准率/可买子集均值）
    n_nolim = 0
    n_fb = 0
    base_ret = []
    # 信号组（lhb_buy_gt_sell==1 且 T 非涨停）
    sig = {"pred": 0, "hit": 0, "sig_ret": []}
    # LHB 覆盖子集（仅上榜标的，用于连续集中度排序对照）
    lhb_present = []  # (conc, nxt, is_fb)
    # 对照：LHB 覆盖窗口（2026-06 起）的非涨停基线，便于 period 对齐的 paired_excess
    era_nolim = 0
    era_fb = 0
    era_ret = []

    for c, rows in data.items():
        for i in range(1, len(rows) - 1):
            clTm1, oT, clT, hiT, loT, voT = (rows[i - 1][2], rows[i][1], rows[i][2],
                                            rows[i][3], rows[i][4], rows[i][5])
            clT1 = rows[i + 1][2]
            if clTm1 == 0 or clT == 0:
                continue
            lim = limit_of(c)
            lupT = (clT >= clTm1 * (1 + lim) - 0.005)
            ldnT = (clT <= clTm1 * (1 - lim) + 0.005)
            if lupT or ldnT:
                continue
            n_nolim += 1
            nxt = clT1 / clT - 1.0
            is_fb = (clT1 >= clT * (1 + lim) - 0.005)
            if is_fb:
                n_fb += 1
            base_ret.append(nxt)

            dT = rows[i][0]
            # LHB 覆盖窗口（榜单数据从 2026-06 起）→ 对齐配对基线
            if dT >= "2026-06-01":
                era_nolim += 1
                era_ret.append(nxt)
                if is_fb:
                    era_fb += 1

            # 信号：lag-1 特征在 date==T 可用（T-1 榜单）
            fv = fmap.get((c.zfill(6), dT))
            if fv is not None:
                conc = float(fv.get("lhb_buy_conc", 0.0))
                lhb_present.append((conc, nxt, is_fb))
                if int(round(float(fv.get("lhb_buy_gt_sell", 0)))) == 1:
                    sig["pred"] += 1
                    sig["sig_ret"].append(nxt)
                    if is_fb:
                        sig["hit"] += 1

    base = n_fb / n_nolim if n_nolim else 0.0
    buyable_mean = sum(base_ret) / len(base_ret) if base_ret else 0.0
    era_base = era_fb / era_nolim if era_nolim else 0.0
    era_mean = sum(era_ret) / len(era_ret) if era_ret else 0.0

    prec = sig["hit"] / sig["pred"] if sig["pred"] else 0.0
    lift = prec / base if base else 0.0
    sig_mean = sum(sig["sig_ret"]) / len(sig["sig_ret"]) if sig["sig_ret"] else 0.0
    pe = sig_mean - buyable_mean
    pe_era = sig_mean - era_mean
    t = nw_t([(r - buyable_mean) for r in sig["sig_ret"]]) if sig["sig_ret"] else 0.0
    t_era = nw_t([(r - era_mean) for r in sig["sig_ret"]]) if sig["sig_ret"] else 0.0

    # ---- 连续集中度排序对照：在上榜子集里按 lhb_buy_conc 取 Top-K% ----
    cont = {}
    if lhb_present:
        arr = np.array(lhb_present, dtype=float)  # (conc, nxt, is_fb)
        conc = arr[:, 0]
        nxt = arr[:, 1]
        isfb = arr[:, 2].astype(bool)
        for q in (0.50, 0.20, 0.10):
            thr = np.quantile(conc[conc > 0], 1 - q) if (conc > 0).any() else 0.0
            mask = conc >= thr
            if mask.sum() == 0:
                continue
            p = isfb[mask].mean()
            pm = nxt[mask].mean()
            pe_c = pm - buyable_mean
            cont[f"top{q}"] = {
                "n": int(mask.sum()), "precision": float(p),
                "lift": float(p / base) if base else 0.0,
                "sig_t1_mean": float(pm), "paired_excess": float(pe_c),
                "t": float(nw_t(list(nxt[mask] - buyable_mean))),
            }

    # ---- 门控（与系统一致； brutally honest）----
    # PROMOTE: precision>=8% 且 paired_excess_t>=2.0
    # 其余按边缘诚实分级：
    #   - 高 precision 但 next-day 收益边缘为负 → REJECT（命中率高却亏钱，是典型陷阱）
    #   - precision 不足但边缘为正 → WATCH（有正 alpha，需再提 precision）
    if sig["pred"] == 0:
        verdict = "REJECT (no signal days)"
    elif prec >= 0.08 and t >= 2.0:
        verdict = "PROMOTE"
    elif prec >= 0.08 and t < 2.0:
        verdict = "REJECT (high precision but non-positive edge)"
    elif t >= 2.0:
        verdict = "WATCH (positive edge, precision below gate)"
    else:
        verdict = "REJECT (low precision AND non-positive/negative edge)"

    # ---- 与既有 Loose-label REJECT 对比 ----
    loose = {}
    if os.path.exists(EDGE):
        try:
            loose = json.load(open(EDGE, encoding="utf-8"))
        except Exception:
            pass

    result = {
        "as_of": datetime.date.today().isoformat(),
        "label": "STRICT r36: T non-limit-up -> T+1 first-board limit-up",
        "base_rate": base,
        "n_nolim": n_nolim,
        "n_first_board": n_fb,
        "buyable_t1_mean": buyable_mean,
        "era_base_rate": era_base,
        "era_buyable_t1_mean": era_mean,
        "signal": {
            "pred": sig["pred"], "hit": sig["hit"],
            "precision": prec, "lift": lift,
            "sig_t1_mean": sig_mean,
            "paired_excess_vs_full": pe, "t_vs_full": t,
            "paired_excess_vs_era": pe_era, "t_vs_era": t_era,
        },
        "verdict": verdict,
        "gate": "PROMOTE iff precision>=0.08 AND t>=2.0",
        "continuous_topk_by_conc": cont,
        "loose_label_reference": {
            "base": loose.get("base"),
            "days": loose.get("days"),
            "note": "existing intel_edge_test used LOOSE label (gap-up or limit-up, base~9.88%) and REJECTED all 10 LHB/inst factors (AUC~0.50).",
        },
    }
    json.dump(result, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=float)

    print("=" * 70)
    print("[r36/lhb] 严格标签 OOS 测试 (lag-1 龙虎榜买卖集中度)")
    print("=" * 70)
    print(f"基准率(非涨停→次日首板) = {base*100:.3f}%  (n={n_nolim:,}, fb={n_fb:,})")
    print(f"可买子集 T+1 均值(全历史) = {buyable_mean:+.4f}")
    print(f"LHB窗口(2026-06起) 基准率 = {era_base*100:.3f}%  T+1均值 = {era_mean:+.4f}")
    print("-" * 70)
    print(f"信号 lhb_buy_gt_sell==1 : pred={sig['pred']:,}  hit={sig['hit']:,}")
    print(f"  precision = {prec*100:.3f}%   lift = {lift:.2f}x")
    print(f"  paired_excess(vs全历史) = {pe:+.4f}   t = {t:.2f}")
    print(f"  paired_excess(vs LHB窗口) = {pe_era:+.4f}   t = {t_era:.2f}")
    print("-" * 70)
    print(f"门控: PROMOTE iff precision>=8% 且 t>=2.0")
    print(f"→ 裁决: {verdict}")
    for q, v in cont.items():
        print(f"  连续集中度 Top{q} (n={v['n']:,}): precision={v['precision']*100:.2f}% lift={v['lift']:.2f}x "
              f"paired_excess={v['paired_excess']:+.4f} t={v['t']:.2f}")
    if loose:
        print(f"对照: 既有 loose-label 测试 base={loose.get('base'):.4f} days={loose.get('days')} → REJECT 全部10个 LHB/机构因子 (AUC~0.50)")
    print(f"写出 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
