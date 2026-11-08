# -*- coding: utf-8 -*-
"""
research/multiple_testing.py — 多重检验校正（让 OOS 不再"看起来严格"）

32 轮因子搜索 = 32 次检验，单看"命中倍数1.15"大概率仍在噪声带内。本模块提供：
  - bonferroni / holm：经典族错误率控制
  - deflated_sharpe_ratio：Bailey & López de Prado (2014)，给定搜索次数 N 后的"真实 Sharpe>0"概率
  - white_reality_check：White (2000) 数据窥探检验，block bootstrap 最大统计量 p 值
纯 numpy + 标准库，无 scipy 依赖。run: python -m research.multiple_testing --selftest
"""
from __future__ import annotations
import math, statistics
import numpy as np

_ND = statistics.NormalDist()


def _ppf(p):
    return _ND.inv_cdf(min(max(p, 1e-12), 1 - 1e-12))


def _cdf(x):
    return _ND.cdf(x)


def bonferroni(pvalues, alpha=0.05):
    m = len(pvalues)
    thr = alpha / m if m else alpha
    return {"method": "bonferroni", "m": m, "threshold": thr,
            "reject": [p <= thr for p in pvalues]}


def holm(pvalues, alpha=0.05):
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    reject = [False] * m
    thresh = [None] * m
    for rank, i in enumerate(order):
        thr = alpha / (m - rank)
        thresh[i] = thr
        if pvalues[i] <= thr:
            reject[i] = True
        else:
            break
    return {"method": "holm", "m": m, "thresholds": thresh, "reject": reject}


def deflated_sharpe_ratio(sr, n_trials, n_obs, skew=0.0, kurt=3.0):
    """给定搜索 N 次后，观测 Sharpe 的"真实 SR>0"概率（0.95+ 才算扛过多重检验）。

    sr: 每期 Sharpe（如日频）；n_trials: 累计搜索/因子数；n_obs: 样本期数。
    """
    if n_obs < 2 or n_trials < 1:
        return 0.0
    g = 0.5772156649  # Euler-Mascheroni
    exp_max = (1 - g) * _ppf(1 - 1.0 / n_trials) + g * _ppf(1 - 1.0 / (n_trials * math.e))
    var_sr = max(1 - skew * sr + (kurt - 1) / 4.0 * sr * sr, 0.0) / (n_obs - 1)
    sr0 = exp_max * math.sqrt(var_sr)
    denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4.0 * sr * sr, 1e-12))
    return float(_cdf((sr - sr0) * math.sqrt(n_obs - 1) / denom))


def _circular_block_indices(T, block, rng):
    idx = np.empty(T, dtype=int)
    i = 0
    while i < T:
        start = rng.integers(0, T)
        for j in range(block):
            if i >= T:
                break
            idx[i] = (start + j) % T
            i += 1
    return idx


def white_reality_check(R, benchmark=0.0, n_boot=1000, block=5, seed=0):
    """White (2000) Reality Check：H0 = 所有 K 个策略/因子都没有超额表现。

    R: T x K 的超额收益矩阵（每列一个策略/因子）；benchmark 为额外扣减的基线收益。
    做法：f = R - benchmark；观察统计量 v_obs = max_k mean(f_k)；
          block bootstrap 重采样后**中心化**再取 max，得到 H0 下的 max 统计量分布；
          p = P(v* >= v_obs)。p 越小越说明"最好的那个确实不是数据窥探产物"。
    注意：bootstrap 必须中心化 (fb - fbar)，否则分布不以 0 为中心，p 值会系统性偏高（假的"通过"）。
    """
    R = np.asarray(R, dtype=float)
    if R.ndim == 1:
        R = R.reshape(-1, 1)
    T, K = R.shape
    f = R - benchmark
    fbar = f.mean(axis=0)
    v_obs = float(max(fbar.max(), 0.0))
    rng = np.random.default_rng(seed)
    reps = np.empty(n_boot)
    for b in range(n_boot):
        idx = _circular_block_indices(T, block, rng)
        fb = f[idx].mean(axis=0)
        reps[b] = float((fb - fbar).max())
    p = float((reps >= v_obs).mean())
    return {"method": "white_reality_check", "v_stat": v_obs, "p_value": p,
            "n_boot": n_boot, "K": int(K), "T": int(T)}


def adjudicate(n_trials, n_obs, hit_multiple, pf, t_stat, sr_per_period,
               dsr_min=0.95, min_hit=1.15, min_pf=1.30, min_t=2.0):
    """综合裁决：命中倍数+PF+t 之外，叠加 deflated Sharpe 多重检验门槛。"""
    dsr = deflated_sharpe_ratio(sr_per_period, n_trials=n_trials, n_obs=n_obs)
    ok = (hit_multiple >= min_hit and pf >= min_pf and t_stat >= min_t and dsr >= dsr_min)
    reasons = []
    if hit_multiple < min_hit:
        reasons.append(f"hit {hit_multiple:.2f}<{min_hit}")
    if pf < min_pf:
        reasons.append(f"pf {pf:.2f}<{min_pf}")
    if t_stat < min_t:
        reasons.append(f"t {t_stat:.1f}<{min_t}")
    if dsr < dsr_min:
        reasons.append(f"DSR {dsr:.2f}<{dsr_min}(多重检验未过)")
    return {"promote": bool(ok), "dsr": dsr, "reasons": reasons}


def _selftest():
    print("[selftest] bonferroni/holm on 3 p-values")
    ps = [0.001, 0.02, 0.2]
    print("  bonferroni:", bonferroni(ps))
    print("  holm:", holm(ps))
    print("[selftest] deflated_sharpe: strong vs weak")
    print("  strong sr=0.08 n_obs=1000 n_trials=10 ->", round(deflated_sharpe_ratio(0.08, 10, 1000), 3))
    print("  weak   sr=0.02 n_obs=1000 n_trials=300 ->", round(deflated_sharpe_ratio(0.02, 300, 1000), 3))
    rng = np.random.default_rng(1)
    R = rng.normal(0, 0.01, size=(500, 20))  # 20 个纯噪声策略
    rc_rand = white_reality_check(R, n_boot=500)
    print("[selftest] white RC (纯噪声, 期望 p 高):", rc_rand)
    R2 = rng.normal(0.0020, 0.01, size=(500, 20))  # 20 个带真实漂移的策略
    rc_drift = white_reality_check(R2, n_boot=500)
    print("[selftest] white RC (强漂移, 期望 p 低):", rc_drift)
    assert rc_drift["p_value"] < rc_rand["p_value"], "真实漂移的 p 值必须小于纯噪声的 p 值"
    R3 = np.zeros((500, 1)); R3[:, 0] = rng.normal(0, 0.01, 500)
    rc_null = white_reality_check(R3, n_boot=500)
    print("[selftest] white RC (单列零均值, 期望 p≈均匀):", rc_null)
    print("[selftest] adjudicate example:", adjudicate(300, 1000, 1.10, 1.20, 1.5, 0.02))
    print("[selftest] PASS — White RC 中心化口径正确（漂移可被检出）")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python -m research.multiple_testing --selftest")
