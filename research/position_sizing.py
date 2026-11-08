# -*- coding: utf-8 -*-
"""
research/position_sizing.py — Kelly 仓位 + 组合风控（有 edge 后再启用）

现状：tail_pick 用固定 18.5% 仓位。本模块提供"有验证 edge 后"的仓位与组合风险工具：
  - kelly_fraction: 分数 Kelly（默认半 Kelly，带上限），无 edge 时返回 0
  - vol_target_size: 波动率目标仓位
  - portfolio_vol: 组合波动 w^T Σ w
  - max_drawdown: 净值最大回撤
  - corr_cap_weights: 相关性惩罚的仓位上限
run: python -m research.position_sizing --selftest
"""
from __future__ import annotations
import numpy as np


def kelly_fraction(win_prob, payoff=1.0, cap=1.0, frac=0.5):
    """f* = p - (1-p)/b；取分数 Kelly 并夹在 [0, cap]。无正 edge 时自然为 0。"""
    p = float(win_prob); b = float(payoff)
    if b <= 0:
        return 0.0
    f = p - (1 - p) / b
    if f <= 0:
        return 0.0
    return float(min(f * frac, cap))


def vol_target_size(target_vol, asset_vol, cap=1.0):
    if asset_vol <= 0:
        return 0.0
    return float(min(target_vol / asset_vol, cap))


def portfolio_vol(weights, cov):
    w = np.asarray(weights, float); c = np.asarray(cov, float)
    return float(np.sqrt(max(w @ c @ w, 0.0)))


def max_drawdown(equity):
    e = np.asarray(equity, float)
    peak = np.maximum.accumulate(e)
    dd = (e / peak) - 1.0
    return float(dd.min())


def corr_cap_weights(weights, corr, avg_corr_threshold=0.6):
    """相关性过高时按比例压缩总仓位（组合分散度不足的风控钩子）。"""
    w = np.asarray(weights, float); c = np.asarray(corr, float)
    n = len(w)
    if n < 2:
        return w
    iu = np.triu_indices(n, 1)
    avg_corr = float(c[iu].mean())
    if avg_corr > avg_corr_threshold and avg_corr > 0:
        w = w * (avg_corr_threshold / avg_corr)
    return w


def calibration_gate(cal_report, ece_max=0.05, mce_max=0.10, require_monotonic=True):
    """Kelly 前置门：概率数值必须经得起校准检验，否则禁止把它当"真实概率"下注。

    cal_report 可来自 research.probability_calibration.report()（含 ece/mce_robust/monotonic_robust）。
    返回 (passed: bool, reasons: list[str])。
    """
    reasons = []
    if not isinstance(cal_report, dict):
        return False, ["无校准报告"]
    ece = cal_report.get("ece")
    mce = cal_report.get("mce_robust", cal_report.get("mce"))
    mono = cal_report.get("monotonic_robust", cal_report.get("monotonic"))
    if ece is None:
        return False, ["缺 ECE"]
    if ece > ece_max:
        reasons.append(f"ECE {ece:.4f} > {ece_max}")
    if mce is not None and mce > mce_max:
        reasons.append(f"MCE {mce:.4f} > {mce_max}")
    if require_monotonic and mono is not True:
        reasons.append("可靠性曲线非单调")
    return (len(reasons) == 0), reasons


def safe_kelly(win_prob, payoff=1.0, cap=1.0, frac=0.5, cal_report=None,
               fixed_fallback=0.185):
    """只有在校准门通过时才允许 Kelly；否则退回固定仓位（tail_pick 现用的 18.5%）。

    返回 dict: {size, mode, note}
      mode = "kelly"          → 采用分数 Kelly 结果
      mode = "kelly_zero"     → 校准通过但模型无正 edge，Kelly 自然为 0（应空仓）
      mode = "fixed_fallback" → 校准未过，禁止用概率下注，退回固定仓位
    """
    passed, reasons = calibration_gate(cal_report) if cal_report is not None else (False, ["未提供校准报告"])
    if not passed:
        return {"size": float(fixed_fallback), "mode": "fixed_fallback",
                "note": "校准门未通过(" + "; ".join(reasons) + ")，概率禁止用于仓位，退回固定仓位"}
    f = kelly_fraction(win_prob, payoff, cap, frac)
    if f <= 0:
        return {"size": 0.0, "mode": "kelly_zero",
                "note": f"校准通过但 Kelly={f:.4f}，无正 edge，应空仓"}
    return {"size": f, "mode": "kelly", "note": f"分数 Kelly={f:.4f}"}


def _selftest():
    print("[selftest] Kelly p=0.55,b=1 ->", round(kelly_fraction(0.55, 1.0), 4), "(应为0.05)")
    print("[selftest] Kelly p=0.45,b=1 ->", round(kelly_fraction(0.45, 1.0), 4), "(无edge应为0)")
    assert kelly_fraction(0.45, 1.0) == 0.0
    cov = np.array([[0.04, 0.01], [0.01, 0.09]])
    print("[selftest] portfolio_vol 50/50 ->", round(portfolio_vol([0.5, 0.5], cov), 4))
    eq = [1, 1.1, 1.05, 1.2, 0.9, 1.0]
    print("[selftest] max_drawdown ->", round(max_drawdown(eq), 4), "(应为-0.25)")

    # 校准门：诚实场景 —— 当前 A 标签真实校准指标（ECE 0.0057 好，但 MCE 0.35 + 非单调）
    bad = {"ece": 0.0057, "mce_robust": 0.3525, "monotonic_robust": False}
    ok_bad, why = calibration_gate(bad)
    print("[selftest] 校准门(真实y_highopen指标) passed=%s reasons=%s" % (ok_bad, why))
    assert not ok_bad, "MCE 0.35/非单调 必须被拒"
    r1 = safe_kelly(0.55, 1.0, cal_report=bad)
    print("[selftest] safe_kelly 校准未过 ->", r1["mode"], round(r1["size"], 4))
    assert r1["mode"] == "fixed_fallback"
    # 校准通过且 p>0.5 -> kelly
    good = {"ece": 0.01, "mce_robust": 0.03, "monotonic_robust": True}
    r2 = safe_kelly(0.60, 1.0, cal_report=good)
    print("[selftest] safe_kelly 校准通过 p=0.60 ->", r2["mode"], round(r2["size"], 4))
    assert r2["mode"] == "kelly"
    # 校准通过但无 edge -> 0
    r3 = safe_kelly(0.45, 1.0, cal_report=good)
    print("[selftest] safe_kelly 校准通过 p=0.45 ->", r3["mode"], round(r3["size"], 4))
    assert r3["mode"] == "kelly_zero"
    print("[selftest] PASS")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python -m research.position_sizing --selftest")
