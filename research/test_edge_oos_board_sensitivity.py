# -*- coding: utf-8 -*-
"""独立验收：连板惯性 OOS 真 edge 对「涨跌停判定口径」的敏感性复核。

背景（证据链先于结论）：
  reports/edge_oos_result.json 的 LB_test n=7102 / T+1涨停率 34.55% / 基线 1.92%
  由 research/p1_edge_oos.py 产出，而该脚本用固定 LIM=0.095 判涨停，不区分板块：
  双创 20% 板、北交所 30% 板的 +10% 普通大涨日会被误记为涨停 → 连板样本被污染。

本脚本不修改任何生产文件，只做对照实验：
  A) legacy  : pct >= 0.095（原 OOS 口径）
  B) canonical: research.board_limit.is_limit_up 同口径（板块幅度 + ROUND_HALF_UP 涨停价）
对同一批 backtest_cache/daily/*.csv，各自重算基线、LB 连板信号与 2025-2026 样本外指标。

向量化实现必须与 canonical 标量函数交叉验证（生产者不得自证）：
随机抽 20000 个真实 (code, prev_close, close) 三元组，逐一调用 is_limit_up 比对。

运行：.venv/Scripts/python.exe research/test_edge_oos_board_sensitivity.py
输出：reports/edge_oos_board_sensitivity.json
"""
import glob
import json
import math
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from research.board_limit import board_limit_pct, is_limit_up  # 单一真相源

DAILY = os.path.join(ROOT, "backtest_cache", "daily")
REPORT = os.path.join(ROOT, "reports", "edge_oos_board_sensitivity.json")
CUT = "2025-01-01"
LEGACY_LIM = 0.095
SEED = 20261107
N_CROSSCHECK = 20000


def _zt_legacy(c):
    """原 p1_edge_oos.py: pct[1:] = c[1:]/c[:-1]-1, zt[0]=False。"""
    n = len(c)
    zt = np.zeros(n, dtype=bool)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = c[1:] / c[:-1] - 1.0
    zt[1:] = pct >= LEGACY_LIM
    return zt


def _zt_canonical(code, c):
    """与 is_limit_up 同口径的向量化：round(prev*(1+lim),2) ROUND_HALF_UP + 0.005 容差。"""
    n = len(c)
    zt = np.zeros(n, dtype=bool)
    lim = board_limit_pct(code)
    prev, cur = c[:-1], c[1:]
    lim_px = np.floor(prev * (1.0 + lim) * 100.0 + 0.5) / 100.0
    zt[1:] = (prev > 0) & (cur >= lim_px - 0.005)
    return zt


def _vec_single(code, prev, cur):
    """单样本版，用于与 canonical 标量函数交叉验证。"""
    lim = board_limit_pct(code)
    lim_px = math.floor(prev * (1.0 + lim) * 100.0 + 0.5) / 100.0
    return bool(prev > 0 and cur >= lim_px - 0.005)


def _board_of(code):
    lim = board_limit_pct(code)
    if lim == 0.20:
        return "双创/CDR(20%)"
    if lim == 0.30:
        return "北交所(30%)"
    if str(code).startswith("900"):
        return "沪B(10%)"
    return "主板(10%)"


def _welch_p(rate, n, base):
    if n < 2:
        return None
    se = math.sqrt(rate * (1 - rate) / n + base * (1 - base) / max(1, n))
    if se == 0:
        return 0.0
    from scipy import stats as st
    return float(1 - st.norm.cdf((rate - base) / se))


def _summarize(sigs, base, cut=CUT):
    out = {}
    for tag, grp in (("train", [s for s in sigs if s[0] < cut]),
                     ("test", [s for s in sigs if s[0] >= cut])):
        if not grp:
            out[tag] = None
            continue
        t1zt = np.array([s[2] for s in grp], dtype=float)
        t1r = np.array([s[1] for s in grp], dtype=float)
        ex = t1r[~(t1zt.astype(bool))]
        rate = float(t1zt.mean())
        out[tag] = {
            "n": int(len(grp)),
            "t1_zt_rate": round(rate, 6),
            "t1_ret_mean": round(float(t1r.mean()), 6),
            "t1_ret_median": round(float(np.median(t1r)), 6),
            "win_rate": round(float((t1r > 0).mean()), 6),
            "t1_ret_ex_zt_mean": round(float(ex.mean()), 6) if len(ex) else None,
            "lift_vs_base": round(rate / base, 3) if base else None,
            "welch_p": _welch_p(rate, len(grp), base),
        }
    return out


def verify_saved():
    """轻量复核：不重扫 5225 个 csv，只对已落盘的报告做验收断言。"""
    d = json.load(open(REPORT, encoding="utf-8"))
    assert d["crosscheck"]["mismatch"] == 0, "向量化判定与 canonical 不一致，报告作废"
    assert d["crosscheck"]["sample_size"] >= N_CROSSCHECK, "交叉验证抽样量不足"
    dual = d["board_misclassification"].get("双创/CDR(20%)", {})
    assert dual.get("legacy_only", 0) > 0, "未观测到双创误判，对照实验失去意义"
    assert d["n_LB_all"]["legacy"] != d["n_LB_all"]["canonical"], "两种口径样本数相同，疑似未生效"
    print("[verify-only] mismatch=0, 抽样=%d, 双创误判=%d, LB样本 %d→%d —— PASS"
          % (d["crosscheck"]["sample_size"], dual["legacy_only"],
             d["n_LB_all"]["legacy"], d["n_LB_all"]["canonical"]))
    return 0


def main():
    if "--verify-only" in sys.argv:
        return verify_saved()
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    print("[scan] daily csv 文件数 = %d, 训练/测试分界 = %s" % (len(files), CUT))
    sig = {"legacy": [], "canonical": []}
    base = {"legacy": [0, 0], "canonical": [0, 0]}   # [涨停数, 样本数]
    mis = {}          # 按板块统计 legacy 多判 / 漏判
    triples = []      # 交叉验证抽样池
    rng = np.random.default_rng(SEED)

    for k, fn in enumerate(files):
        code = os.path.basename(fn)[:-4]
        try:
            df = pd.read_csv(fn, usecols=[0, 2])
        except Exception:
            continue
        if df.shape[0] < 5:
            continue
        dates = df.iloc[:, 0].astype(str).values
        c = df.iloc[:, 1].astype(float).values
        n = len(c)
        zl, zc = _zt_legacy(c), _zt_canonical(code, c)

        bd = _board_of(code)
        rec = mis.setdefault(bd, {"bars": 0, "legacy_only": 0, "canonical_only": 0})
        rec["bars"] += n - 1
        rec["legacy_only"] += int(np.sum(zl[1:] & ~zc[1:]))
        rec["canonical_only"] += int(np.sum(zc[1:] & ~zl[1:]))

        for key, zt in (("legacy", zl), ("canonical", zc)):
            base[key][0] += int(zt[1:].sum())
            base[key][1] += n - 1
            for i in range(2, n - 1):
                if zt[i] and zt[i - 1]:
                    sig[key].append((dates[i], float(c[i + 1] / c[i] - 1), bool(zt[i + 1])))

        # 抽样池：从真实 (prev, close) 对里随机取，供标量 canonical 交叉验证
        if n > 3:
            idx = rng.choice(np.arange(1, n), size=min(6, n - 1), replace=False)
            for i in idx:
                triples.append((code, float(c[i - 1]), float(c[i])))
        if (k + 1) % 1500 == 0:
            print("  ...%d/%d" % (k + 1, len(files)))

    # 独立交叉验证：向量化判定必须逐样本等于 canonical 标量函数
    sample = rng.choice(len(triples), size=min(N_CROSSCHECK, len(triples)), replace=False)
    mismatch = 0
    for j in sample:
        code, prev, cur = triples[int(j)]
        if _vec_single(code, prev, cur) != bool(is_limit_up(code, prev, cur)):
            mismatch += 1
    print("[crosscheck] 抽样 %d 个真实三元组，向量化 vs is_limit_up 不一致 = %d"
          % (len(sample), mismatch))

    bases = {k: (v[0] / max(1, v[1])) for k, v in base.items()}
    result = {
        "source": "backtest_cache/daily/*.csv",
        "n_files": len(files),
        "cut": CUT,
        "legacy_lim": LEGACY_LIM,
        "crosscheck": {"sample_size": int(len(sample)), "mismatch": mismatch,
                       "seed": SEED},
        "base_t1_zt_rate": {k: round(v, 6) for k, v in bases.items()},
        "base_counts": {k: {"zt": v[0], "bars": v[1]} for k, v in base.items()},
        "LB": {k: _summarize(v, bases[k]) for k, v in sig.items()},
        "n_LB_all": {k: len(v) for k, v in sig.items()},
        "board_misclassification": mis,
    }
    # 独立验收断言：不成立就不写报告、非零退出（生产者不得自证）
    assert mismatch == 0, "向量化判定与 is_limit_up 不一致，本次对照实验作废"
    assert mis.get("双创/CDR(20%)", {}).get("legacy_only", 0) > 0, "未观测到双创误判"
    assert len(sig["legacy"]) != len(sig["canonical"]), "两口径样本数相同，判定未生效"
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    json.dump(result, open(REPORT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n=== 连板惯性 LB · 口径敏感性对照 ===")
    print("基线(全体次日涨停率): legacy=%.4f  canonical=%.4f"
          % (bases["legacy"], bases["canonical"]))
    print("%-10s %8s %10s %10s %10s %10s %10s" %
          ("口径", "n(test)", "T+1涨停率", "收益均值", "胜率", "剔除涨停", "lift"))
    for k in ("legacy", "canonical"):
        t = result["LB"][k]["test"]
        print("%-10s %8d %10.4f %+10.4f %10.4f %+10.4f %10.2f" %
              (k, t["n"], t["t1_zt_rate"], t["t1_ret_mean"], t["win_rate"],
               t["t1_ret_ex_zt_mean"] if t["t1_ret_ex_zt_mean"] is not None else float("nan"),
               t["lift_vs_base"]))
    print("\n=== 旧口径误判分布（按板块，bars=可比日数）===")
    for bd, r in sorted(mis.items()):
        print("  %-18s bars=%8d  legacy多判=%7d  canonical独有=%6d"
              % (bd, r["bars"], r["legacy_only"], r["canonical_only"]))
    print("\n[ saved ] %s" % REPORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
