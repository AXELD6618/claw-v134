# -*- coding: utf-8 -*-
"""
research/nw_stats.py — OOS 因子验证的 HAC 标准误估计量（独立模块，无项目内依赖）
================================================================================
背景：H=5 持有期的前向收益逐日重叠（day t 与 day t+1 共享 4/5 个前向日），
      日度配对超额序列呈 MA(H-1)=MA(4) 自相关；i.i.d. `std/sqrt(n)` 会系统性
      低估标准误、放大 t，可把无 edge 的因子误判过 t>=2.0 门槛。
本模块只修「估计量」，不含任何门槛/政策：
  - nw_tstat              : Newey-West HAC（Bartlett 核）均值 t，默认 lag=4
  - stationary_bootstrap_t: Politis-Romano(1994) 平稳块自助，主链路不用，
                            仅供交叉校验（中心化口径同 research/multiple_testing.py
                            :97 white_reality_check 的 (fb - fbar) 中心化）。
仅依赖 numpy + math。运行 `python -m research.nw_stats` 执行自检。
"""
from __future__ import annotations
import math
import numpy as np

__all__ = ["nw_tstat", "stationary_bootstrap_t"]

DEFAULT_LAG = 4            # H-1：H=5 重叠 → MA(4)
_DEFAULT_BLOCK_MEAN = 5.0  # 默认 H=5，保持模块自包含（不依赖 oospf_validator）
_NAN = float("nan")


def nw_tstat(x, lag=None):
    """对按时间排序的日度序列 x 返回 (t_hac, se_hac)。

    Newey-West HAC 均值 t：
        gamma_j = sum_{t=j+1..n} (x_t - mu)(x_{t-j} - mu) / n
        S       = gamma_0 + sum_{j=1..lag} 2 * (1 - j/(lag+1)) * gamma_j   (Bartlett)
        se_hac  = sqrt(S / n);  t_hac = mu / se_hac
    lag 默认 4（= H-1）。退化输入（n < max(5, lag+2)、S<=0、非有限）→ (nan, nan)。
    """
    if lag is None:
        lag = DEFAULT_LAG
    lag = int(lag)
    try:
        a = np.asarray(x, dtype=float)
    except (TypeError, ValueError):
        return _NAN, _NAN
    a = a[np.isfinite(a)]
    n = a.size
    if n < max(5, lag + 2):
        return _NAN, _NAN
    mu = float(a.mean())
    xc = a - mu
    S = float(np.dot(xc, xc)) / n                      # gamma_0
    for j in range(1, min(lag, n - 1) + 1):
        gj = float(np.dot(xc[j:], xc[:-j]) / n)
        S += 2.0 * (1.0 - j / (lag + 1.0)) * gj       # Bartlett 权重
    if not math.isfinite(S) or S <= 0:
        return _NAN, _NAN
    se = math.sqrt(S / n)
    if se <= 0 or not math.isfinite(se):
        return _NAN, _NAN
    t = mu / se
    if not math.isfinite(t):
        return _NAN, _NAN
    return t, se


def stationary_bootstrap_t(x, n_boot=2000, block_mean=None, seed=20260701):
    """Politis-Romano(1994) 平稳块自助 → (t_boot, p_two_sided, se_boot)。

    几何分布块长（均值 block_mean，默认 5.0=H），循环拼接长度 n 的重采样序列；
    中心化 z=(means-mu)/se_boot 构造 H0 分布（口径同
    research/multiple_testing.py:97 white_reality_check 的 (fb-fbar) 中心化）。
    主链路不使用；仅供 T7 交叉校验。退化输入 → (nan, nan, nan)。
    """
    if block_mean is None:
        block_mean = _DEFAULT_BLOCK_MEAN
    try:
        a = np.asarray(x, dtype=float)
    except (TypeError, ValueError):
        return _NAN, _NAN, _NAN
    a = a[np.isfinite(a)]
    n = a.size
    if n < 5 or block_mean <= 0:
        return _NAN, _NAN, _NAN
    p = 1.0 / float(block_mean)
    mu = float(a.mean())
    n_boot = int(n_boot)
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        i = int(rng.integers(0, n))
        acc = 0.0
        for _ in range(n):
            acc += a[i]
            if rng.random() < p:                       # 以概率 p 重开一块
                i = int(rng.integers(0, n))
            else:
                i = (i + 1) % n                        # 否则沿用下一观测（环形）
        means[b] = acc / n
    se_boot = float(means.std(ddof=1))
    if se_boot <= 0 or not math.isfinite(se_boot):
        return _NAN, _NAN, _NAN
    t_boot = mu / se_boot
    z = (means - mu) / se_boot                        # 中心化 → H0 分布
    p_two = float(np.mean(np.abs(z) >= abs(t_boot)))
    return t_boot, p_two, se_boot


def _selftest():
    rng = np.random.default_rng(0)
    # (1) 白噪声：HAC 不应凭空放大 se
    w = rng.normal(0.0, 0.01, 513)
    t_w, se_w = nw_tstat(w, 4)
    se_iid = w.std(ddof=1) / math.sqrt(w.size)
    assert 0.8 < se_w / se_iid < 1.3, "白噪声 HAC/IID se 比值异常"
    # (2) H=5 重叠 MA(4)：HAC t 必须显著小于 naive iid t，且落到 2.0 以下
    eps = rng.normal(0.0, 0.01, 513 + 4)
    eps -= eps.mean()
    ov = np.array([eps[t:t + 5].mean() for t in range(513)])
    ov -= ov.mean()
    sd = ov.std(ddof=1)
    ov = ov + 3.0 * sd / math.sqrt(ov.size)           # 反推：令 naive iid t = 3.0
    t_naive = ov.mean() / (ov.std(ddof=1) / math.sqrt(ov.size))
    t_hac, se_hac = nw_tstat(ov, 4)
    assert t_hac < t_naive and t_hac < 2.0, "重叠数据 HAC t 未压到 naive 之下"
    assert se_hac / (ov.std(ddof=1) / math.sqrt(ov.size)) > 1.5, "HAC se 未放大"
    # (3) 退化输入：n<5 / 常数 / 全 NaN / 含 inf → (nan, nan)
    for bad in ([1.0, 2.0], np.full(10, 0.5), np.full(10, np.nan),
                [1, 2, np.inf, 3, 4, 5]):
        r = nw_tstat(bad, 4)
        assert all(math.isnan(v) for v in r), "退化输入未返回 (nan,nan)"
    # (4) 平稳块自助：返回有限值，且与 HAC 同量级
    tb, pb, sb = stationary_bootstrap_t(ov)
    assert all(math.isfinite(v) for v in (tb, pb, sb)), "bootstrap 返回非有限"
    print("[nw_stats] selftest: nw_tstat(white)=%.3f  nw_tstat(overlap)=%.3f (naive=%.3f)"
          % (t_w, t_hac, t_naive))
    print("[nw_stats] selftest: stationary_bootstrap_t t=%.3f p_two=%.3f se=%.6f"
          % (tb, pb, sb))
    print("PASS")


if __name__ == "__main__":
    _selftest()
