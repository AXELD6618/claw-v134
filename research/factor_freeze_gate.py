# -*- coding: utf-8 -*-
"""
research/factor_freeze_gate.py — 冻结因子挖掘后的"准入门"

政策：新因子/新战法在进入 core_factor_model 前，除 oospf 的 Top-K/PF 外，
还必须通过多重检验（deflated Sharpe >= 0.95，考虑到累计搜索次数 n_trials）。
用法：
  python -m research.factor_freeze_gate --demo
  from research.factor_freeze_gate import gate
  gate(hit_multiple=1.2, pf=1.4, t_stat=2.5, sr_per_period=0.01, n_obs=1000, n_trials=33)
"""
from __future__ import annotations
from .multiple_testing import adjudicate, deflated_sharpe_ratio

FREEZE_NOTE = ("因子挖掘已冻结：不再新增手写因子进入 buy_score。新战法须先过 OOS Top-K/PF "
               "且 deflated Sharpe>=0.95（按累计搜索次数校正），否则一律 REJECT。")


def gate(hit_multiple, pf, t_stat, sr_per_period, n_obs, n_trials=33, dsr_min=0.95):
    res = adjudicate(n_trials=n_trials, n_obs=n_obs, hit_multiple=hit_multiple, pf=pf,
                     t_stat=t_stat, sr_per_period=sr_per_period, dsr_min=dsr_min)
    res["dsr"] = round(res["dsr"], 4)
    res["note"] = FREEZE_NOTE
    res["n_trials"] = n_trials
    return res


def _demo():
    # 一个"看起来不错"的候选：命中1.20/PF1.40/t=2.5，但累计搜索33次
    print("[demo] 候选1:", gate(1.20, 1.40, 2.5, sr_per_period=0.012, n_obs=1200, n_trials=33))
    print("[demo] 候选2(强):", gate(1.35, 1.70, 3.5, sr_per_period=0.020, n_obs=3000, n_trials=33))
    print("[demo] DSR 说明: 累计搜索越多，同样的 Sharpe 越难通过（多重检验惩罚）。")


if __name__ == "__main__":
    import sys
    _demo()
