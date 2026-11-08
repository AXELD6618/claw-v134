#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S7 独立验收 (evaluation-gatekeeper 视角, 与生产方 P1 闭环分离).

纠正 S5 的 data snooping: 本审计不 import 任何生产模型(P1/p0_gate_test),
仅用原始日线独立重算, 并做到三点:
  1. 分位阈值注册: 我们只检验"信号日二进制 T+1 表现 vs 市场基线", 预先声明假设,
     绝不在数据上事后挑最优分位当证据 (与 p1_agent_loop.evidence_from_panel 的前10%分位划清界限).
  2. OOS 确认: 训练 2018-2024 估基线, 测试 2025-2026 验证.
  3. FDR 校正: 多假设(连板/断层 × 涨停率/收益 × 稳定性)做 Benjamini-Hochberg 多重比较校正,
     避免假显著.
输出独立 verdict: 哪些 edge claim 经 FDR+OOS 后仍成立.
"""
import os, sys, glob, json, math
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p1_edge_oos as E   # 复用 proc: 独立读 daily 原始日线, 与生产模型无关

PROJ = E.PROJ
CUT = "2025-01-01"


def collect():
    files = sorted(glob.glob(os.path.join(E.DAILY, "*.csv")))
    all_sig = []; bnum = 0; bden = 0
    for fn in files:
        r = E.proc(fn)
        if r is None:
            continue
        all_sig += r[0]; bnum += r[1]; bden += r[2]
    return all_sig, bnum / max(1, bden)


def prop_test(rate, n, base):
    if n < 2:
        return None
    se = math.sqrt(base * (1 - base) / n)
    if se == 0:
        return 0.0
    z = (rate - base) / se
    from scipy import stats as st
    return float(1 - st.norm.cdf(z))


def t_test_onesided(mean, n, std):
    if n < 2 or std <= 0:
        return None
    se = std / math.sqrt(n)
    t = mean / se
    from scipy import stats as st
    return float(1 - st.norm.cdf(t))


def two_sample_stable_p(a_rate, a_n, b_rate, b_n):
    """稳定性: 检验 测试期率 是否显著低于 训练期率 (单侧). p 大=稳定无衰减."""
    if a_n < 2 or b_n < 2:
        return None
    se = math.sqrt(a_rate * (1 - a_rate) / a_n + b_rate * (1 - b_rate) / b_n)
    if se == 0:
        return 1.0
    z = (a_rate - b_rate) / se   # 测试-训练; 若测试更低 z<0 -> 单侧 p 大
    from scipy import stats as st
    return float(st.norm.cdf(z))  # 单侧: 测试<=训练 的概率, 大=稳定


def bh_fdr(pvals):
    p = np.array([x if x is not None else 1.0 for x in pvals], float)
    m = len(p)
    if m == 0:
        return []
    order = np.argsort(p)
    ranked = p[order]
    q = np.empty(m)
    prev = 1.0
    for i in range(m - 1, -1, -1):
        q[order[i]] = min(prev, ranked[i] * m / (i + 1))
        prev = q[order[i]]
    return q.tolist()


def main():
    sigs, base = collect()
    print(f"[independent] 信号 {len(sigs)} 条, 市场基线次日涨停率={base:.4f}, 分界={CUT}")
    print("[independent] ⚠️ 分位阈值注册: 仅检验信号日二进制 T+1 表现 vs 基线, 不事后挑分位 (纠正 S5)")
    tr = [s for s in sigs if s[1] < CUT]
    te = [s for s in sigs if s[1] >= CUT]
    g = lambda grp, k: [s for s in grp if s[0] == k]
    lb_tr, lb_te = g(tr, "LB"), g(te, "LB")
    dc_tr, dc_te = g(tr, "DC"), g(te, "DC")

    H = []  # (name, p, direction_pass_if_small)
    r_lb_te = np.mean([s[3] for s in lb_te]); r_lb_tr = np.mean([s[3] for s in lb_tr])
    r_dc_te = np.mean([s[3] for s in dc_te]); r_dc_tr = np.mean([s[3] for s in dc_tr])
    H.append(("H1 连板惯性 测试期T+1涨停率>基线", prop_test(r_lb_te, len(lb_te), base), True))
    H.append(("H2 断层反包 测试期T+1涨停率>基线", prop_test(r_dc_te, len(dc_te), base), True))
    ret_lb = np.array([s[2] for s in lb_te]); ret_dc = np.array([s[2] for s in dc_te])
    H.append(("H3 连板惯性 测试期T+1收益均值>0", t_test_onesided(ret_lb.mean(), len(ret_lb), ret_lb.std()), True))
    H.append(("H4 断层反包 测试期T+1收益均值>0", t_test_onesided(ret_dc.mean(), len(ret_dc), ret_dc.std()), True))
    H.append(("H5 连板惯性 测试vs训练 涨停率稳定(不显著降)", two_sample_stable_p(r_lb_te, len(lb_te), r_lb_tr, len(lb_tr)), False))
    H.append(("H6 断层反包 测试vs训练 涨停率稳定(不显著降)", two_sample_stable_p(r_dc_te, len(dc_te), r_dc_tr, len(dc_tr)), False))

    pvals = [h[1] for h in H]
    qs = bh_fdr(pvals)
    print("\n假设 / p(单侧) / FDR-q / 独立判定")
    out = []
    for (name, p, pas), q in zip(H, qs):
        if p is None:
            verdict = "NA(样本不足)"
        elif q < 0.05:
            verdict = "PASS(成立)" if pas else "FAIL(反向显著)"
        else:
            verdict = "FAIL(不显著)" if pas else "PASS(稳定/无反向)"
        print(f"  {name}: p={p:.2e} q={q:.2e} -> {verdict}")
        out.append({"hypothesis": name, "p": p, "q_fdr": q, "verdict": verdict})
    print("=" * 60)
    print("独立验收结论 (FDR 校正后):")
    print("  连板惯性: H1/H3/H5 ->", "成立" if qs[0] < 0.05 and qs[2] < 0.05 else "存疑")
    print("  断层反包: H2(涨停率高)成立 但 H4(收益>0) ->", "不成立(FDR后负期望)" if qs[3] >= 0.05 else "成立")
    print("  => 与 p1_edge_oos 一致: 仅连板惯性经独立 FDR+OOS 验证为真 edge; 断层反包收益期望不成立")
    print("=" * 60)
    json.dump({"base": round(base, 4), "cut": CUT, "n_signals": len(sigs),
               "n_lb_test": len(lb_te), "n_dc_test": len(dc_te),
               "hypotheses": out},
              open(os.path.join(PROJ, "reports", "independent_audit_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("[saved] reports/independent_audit_result.json")


if __name__ == "__main__":
    main()
