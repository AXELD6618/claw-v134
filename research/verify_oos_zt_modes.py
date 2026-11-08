#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""OOS 涨停判定口径对照（单遍读取，同时算 legacy 与 canonical）。

目的（2026-11-07）：
  验证 p1_edge_oos / p1_execution_oos 收口到板块口径后，edge 与执行分层的关键数字如何变化。
  一次扫描所有 daily CSV，对每只票计算两种 zt 口径，分别累积：
    - edge: LB/DC 样本外(T>=2025) 涨停率/收益/胜率/剔除涨停后条件均值
    - execution: 连板长度分层(2/3/4+) 的 T+1 涨停率/收益/胜率，以及持有期/trailing

交叉校验：legacy 模式 edge LB_test 必须等于已知真值 n=7259 / t1_zt=0.345089
          （来自 prior-turn 原脚本重跑，同一份数据快照）。不一致即 harness 自身有 bug。
zt 语义直接复用 research.p1_edge_oos._zt_array（与脚本内判定逐字一致）。
"""
import os, sys, glob, json
import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
from research.p1_edge_oos import _code_of, _zt_array

DAILY = os.path.join(PROJ, "backtest_cache", "daily")
CUT = "2025-01-01"


def main():
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    print(f"[scan] {len(files)} 只日线, 分界={CUT}")

    # 逐模式累积容器
    base = {m: [0, 0] for m in ("legacy", "canonical")}      # [num, den]
    edge = {m: dict(n_LB=0, n_DC=0, LB_zt=0.0, LB_ret=0.0, LB_win=0,
                   LB_exzt_ret=0.0, LB_exzt_n=0,
                   DC_zt=0.0, DC_ret=0.0, DC_win=0,
                   DC_exzt_ret=0.0, DC_exzt_n=0) for m in ("legacy", "canonical")}
    exall = {m: [] for m in ("legacy", "canonical")}          # (L,t1,t2,t3,dd,t1zt)

    for k, fn in enumerate(files):
        try:
            df = pd.read_csv(fn, usecols=[0, 2, 3, 4])
        except Exception:
            continue
        if df.shape[0] < 6:
            continue
        dates = df.iloc[:, 0].astype(str).values
        c = df.iloc[:, 1].astype(float).values
        h = df.iloc[:, 2].astype(float).values
        l = df.iloc[:, 3].astype(float).values
        n = len(c)
        code = _code_of(fn)
        for m in ("legacy", "canonical"):
            zt = _zt_array(code, c, m)
            base[m][0] += int(zt[1:].sum()); base[m][1] += (n - 1)
            # edge LB/DC（测试期）
            for i in range(2, n - 1):
                if dates[i] < CUT:
                    continue
                t1 = c[i + 1] / c[i] - 1
                t1zt = bool(zt[i + 1])
                if zt[i] and zt[i - 1]:
                    e = edge[m]; e["n_LB"] += 1
                    e["LB_zt"] += t1zt; e["LB_ret"] += t1; e["LB_win"] += (t1 > 0)
                    if not t1zt:
                        e["LB_exzt_ret"] += t1; e["LB_exzt_n"] += 1
                elif zt[i - 1] and not zt[i]:
                    e = edge[m]; e["n_DC"] += 1
                    e["DC_zt"] += t1zt; e["DC_ret"] += t1; e["DC_win"] += (t1 > 0)
                    if not t1zt:
                        e["DC_exzt_ret"] += t1; e["DC_exzt_n"] += 1
            # execution 连板分层
            for i in range(2, n - 3):
                if not (zt[i] and zt[i - 1]):
                    continue
                L = 1; j = i
                while j - 1 >= 0 and zt[j - 1]:
                    L += 1; j -= 1
                t1 = c[i + 1] / c[i] - 1
                t2 = c[i + 2] / c[i] - 1 if i + 2 < n else None
                t3 = c[i + 3] / c[i] - 1 if i + 3 < n else None
                dd = (l[i + 1] - h[i + 1]) / h[i + 1] if i + 1 < n else 0.0
                exall[m].append((L, t1, t2, t3, dd, bool(zt[i + 1])))
        if (k + 1) % 1000 == 0:
            print(f"  ...{k+1}/{len(files)}")

    result = {"cut": CUT, "n_files": len(files), "modes": {}}
    for m in ("legacy", "canonical"):
        bnum, bden = base[m]
        base_rate = bnum / max(1, bden)
        e = edge[m]
        ex = exall[m]
        # 分层
        layers = {2: [], 3: [], 4: []}
        for s in ex:
            L = s[0]; key = L if L <= 3 else 4
            layers.setdefault(key, []).append(s)
        layer_stat = {}
        for L in sorted(layers):
            g = layers[L]
            if not g:
                continue
            t1 = np.array([x[1] for x in g]); zt = np.array([x[5] for x in g])
            layer_stat[L] = dict(n=len(g), t1_zt=float(zt.mean()),
                                 ret_mean=float(t1.mean()), win=float((t1 > 0).mean()))
        hold_stat = {}
        for kk in (1, 2, 3):
            col = [s[1 + kk - 1] for s in ex if s[1 + kk - 1] is not None]
            if col:
                arr = np.array(col)
                hold_stat[kk] = dict(n=len(arr), ret_mean=float(arr.mean()),
                                     win=float((arr > 0).mean()))
        trail_stat = {}
        hold_close = np.array([s[1] for s in ex])
        for tp in (0.03, 0.05, 0.07):
            sell = np.where(np.array([s[4] for s in ex]) < -tp,
                            np.array([s[4] for s in ex]), hold_close)
            trail_stat[tp] = float(sell.mean())

        lb = e["n_LB"]
        lb_zt = e["LB_zt"] / lb if lb else 0.0
        lb_ret = e["LB_ret"] / lb if lb else 0.0
        lb_win = e["LB_win"] / lb if lb else 0.0
        lb_ex = e["LB_exzt_ret"] / e["LB_exzt_n"] if e["LB_exzt_n"] else None

        result["modes"][m] = dict(
            base_t1_zt=round(base_rate, 6),
            LB_test=dict(n=lb, t1_zt_rate=round(lb_zt, 6), lift_vs_base=round(lb_zt / base_rate, 3) if base_rate else None,
                         t1_ret_mean=round(lb_ret, 6), win_rate=round(lb_win, 6),
                         t1_ret_ex_zt_mean=(round(lb_ex, 6) if lb_ex is not None else None)),
            DC_test=dict(n=e["n_DC"],
                         t1_zt_rate=round(e["DC_zt"] / e["n_DC"], 6) if e["n_DC"] else 0.0,
                         t1_ret_mean=round(e["DC_ret"] / e["n_DC"], 6) if e["n_DC"] else 0.0),
            layer_stat=layer_stat, hold_stat=hold_stat, trail_stat=trail_stat,
            best_L=max(layer_stat, key=lambda k: layer_stat[k]["ret_mean"]) if layer_stat else 3,
            best_hold=max(hold_stat, key=lambda k: hold_stat[k]["ret_mean"]) if hold_stat else 1,
            best_trail=max(trail_stat, key=lambda k: trail_stat[k]) if trail_stat else 0.05,
        )
        print(f"\n===== {m} =====  base={base_rate:.4f}  LB_test n={lb} zt={lb_zt:.4f} "
              f"lift={lb_zt/base_rate:.2f}x ret={lb_ret:+.4f} win={lb_win:.3f} exzt={lb_ex}")
        print(f"  DC_test n={e['n_DC']} zt={e['DC_zt']/max(1,e['n_DC']):.4f}")
        print(f"  layers:", {L: (layer_stat[L]["n"], round(layer_stat[L]["t1_zt"],3), round(layer_stat[L]["ret_mean"],4))
                           for L in layer_stat})

    # 交叉校验：legacy edge LB_test 必须 = 已知真值
    lb_legacy = result["modes"]["legacy"]["LB_test"]
    ref_n, ref_zt = 7259, 0.345089
    chk = (lb_legacy["n"] == ref_n) and (abs(lb_legacy["t1_zt_rate"] - ref_zt) < 1e-6)
    print(f"\n[交叉校验] legacy edge LB_test n={lb_legacy['n']} zt={lb_legacy['t1_zt_rate']:.6f}"
          f"  期望 {ref_n}/{ref_zt}  -> {'PASS' if chk else 'FAIL!!!'}")

    outp = os.path.join(PROJ, "reports", "oos_zt_mode_compare.json")
    result["crosscheck_legacy_edge"] = dict(pass_=bool(chk), ref_n=ref_n, ref_zt=ref_zt,
                                           got_n=lb_legacy["n"], got_zt=lb_legacy["t1_zt_rate"])
    json.dump(result, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[saved] {outp}")


if __name__ == "__main__":
    main()
