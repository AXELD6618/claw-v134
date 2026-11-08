#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S9/S10 执行空白量化: 连板惯性买点优化(连板长度分层) + 卖点量化(持有期/trailing).

S10 买点: 连板惯性买点是 T 日尾盘(已第N板), 本质追高. 本脚本按"连续涨停长度 L"
     分层(2板/3板/4+板), 量化各层 T+1 期望, 找最优买入连板长度窗口(避免追末板博弈).
S9 卖点: 当前 p1_rules exit 段 trailing/time_stop 参数未量化. 本脚本算持有 T+1/T+2/T+3
     累计收益 + 日线级 trailing 回撤止损近似, 量化最优持有期与 trailing 阈值.

输出: 分层/持有期表 -> 写回 data/p1_rules.json (entry.max_lianban_len / exit.hold_days / exit.trailing_pct).
独立读原始日线, 与生产模型无关.
"""
import os, sys, glob, json, argparse
import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
# 2026-11-07 收口：涨停判定复用 p1_edge_oos 的板块口径助手（单一真相源），
# 不再使用硬编码 LIM=0.095。
from research.p1_edge_oos import _code_of, _zt_array, ZT_MODE

DAILY = os.path.join(PROJ, "backtest_cache", "daily")
RULES_P = os.path.join(PROJ, "data", "p1_rules.json")


def proc_ext(fn, mode=None):
    mode = mode or ZT_MODE
    try:
        df = pd.read_csv(fn, usecols=[0, 2, 3, 4])
    except Exception:
        return None
    if df.shape[0] < 6:
        return None
    dates = df.iloc[:, 0].astype(str).values
    c = df.iloc[:, 1].astype(float).values
    h = df.iloc[:, 2].astype(float).values
    l = df.iloc[:, 3].astype(float).values
    n = len(c)
    code = _code_of(fn)
    zt = _zt_array(code, c, mode)
    out = []
    for i in range(2, n - 3):
        if zt[i] and zt[i - 1]:
            L = 1; j = i
            while j - 1 >= 0 and zt[j - 1]:
                L += 1; j -= 1
            t1 = c[i + 1] / c[i] - 1
            t2 = c[i + 2] / c[i] - 1 if i + 2 < n else None
            t3 = c[i + 3] / c[i] - 1 if i + 3 < n else None
            dd = (l[i + 1] - h[i + 1]) / h[i + 1] if i + 1 < n else 0.0  # T+1日内最大回撤proxy
            out.append((dates[i], L, t1, t2, t3, dd, bool(zt[i + 1])))
    return out


def main():
    ap = argparse.ArgumentParser(description="S9/S10 执行空白量化（2026-11-07 收口到板块口径）")
    ap.add_argument("--zt-mode", choices=["legacy", "canonical"], default=ZT_MODE,
                    help="涨停判定口径: legacy=旧 0.095; canonical=板块口径 is_limit_up(默认)")
    ap.add_argument("--write", action="store_true", default=False,
                    help="写回 data/p1_rules.json（默认不写，仅评估；实盘配置判定写入即冻结，须先评审）")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    alls = []
    for fn in files:
        r = proc_ext(fn, mode=args.zt_mode)
        if r:
            alls += r
    print(f"[exec] LB 信号 {len(alls)} 条 (连板惯性), zt_mode={args.zt_mode}")

    # ---- S10 买点: 连板长度分层 ----
    print("\n[S10] 连板长度 L 分层 (买点优化, 避免追末板):")
    print(f"{'L板':>4} {'n':>7} {'T+1涨停率':>9} {'T+1收益均值':>10} {'胜率':>6}")
    layers = {2: [], 3: [], 4: []}
    for s in alls:
        L = s[1]
        key = L if L <= 3 else 4
        layers.setdefault(key, []).append(s)
    layer_stat = {}
    for L in sorted(layers):
        g = layers[L]
        if not g:
            continue
        t1 = np.array([x[2] for x in g]); zt = np.array([x[6] for x in g])
        print(f"{L:>4} {len(g):>7} {zt.mean():>9.3f} {t1.mean():>+10.4f} {(t1>0).mean():>6.3f}")
        layer_stat[L] = dict(n=len(g), t1_zt=float(zt.mean()), ret_mean=float(t1.mean()), win=float((t1>0).mean()))

    # ---- S9 卖点: 持有期 + trailing ----
    print("\n[S9] 持有期 (T日收盘买, T+k收盘卖):")
    print(f"{'持有k':>5} {'n':>7} {'累计收益均值':>11} {'胜率':>6}")
    hold_stat = {}
    for k in (1, 2, 3):
        col = [s[2 + k - 1] for s in alls if s[2 + k - 1] is not None]
        if not col:
            continue
        arr = np.array(col)
        print(f"{k:>5} {len(arr):>7} {arr.mean():>+11.4f} {(arr>0).mean():>6.3f}")
        hold_stat[k] = dict(n=len(arr), ret_mean=float(arr.mean()), win=float((arr>0).mean()))

    # trailing 近似: 若 T+1 日内回撤触 trailing_pct 则按 low 卖, 否则持有收盘卖
    print("\n[S9] trailing 回撤止损 (日线级 proxy, 触回撤按 low 卖):")
    print(f"{'trailing%':>10} {'期望收益':>10} {'vs持有收盘':>11}")
    trail_stat = {}
    hold_close = np.array([s[2] for s in alls])
    for tp in (0.03, 0.05, 0.07):
        sell = np.where(np.array([s[5] for s in alls]) < -tp,
                        np.array([s[5] for s in alls]), hold_close)  # 触回撤按 low/c 近似
        # low 卖收益 = (low[i+1]-c[i])/c[i] = dd (已近似); 不触则持有收盘 t1
        exp = sell.mean()
        trail_stat[tp] = float(exp)
        print(f"{tp:>10.2f} {exp:>+10.4f} {exp-hold_close.mean():>+11.4f}")

    # ---- 是否写回 p1_rules.json（默认 --no-write，仅评估）----
    best_L = max(layer_stat, key=lambda k: layer_stat[k]["ret_mean"]) if layer_stat else 3
    best_hold = max(hold_stat, key=lambda k: hold_stat[k]["ret_mean"]) if hold_stat else 1
    best_trail = max(trail_stat, key=lambda k: trail_stat[k]) if trail_stat else 0.05
    if not args.write:
        print("\n[no-write] 默认不写回 p1_rules.json（仅评估）。如需落地，先评审分层结论后加 --write。")
        print(f"[建议] max_lianban_len={best_L} hold_days={best_hold} trailing_pct={best_trail} (zt_mode={args.zt_mode})")
    else:
        rules = json.load(open(RULES_P, encoding="utf-8"))
        rules.setdefault("entry", {})["max_lianban_len"] = int(best_L)
        rules.setdefault("exit", {})["hold_days"] = int(best_hold)
        rules.setdefault("exit", {})["trailing_pct"] = float(best_trail)
        rules.setdefault("exit", {})["no_hard_stop"] = True
        rules["_execution_oos_note"] = ("S9/S10 量化(2026-11-07 板块口径): 连板长度最优%s板, 持有%s日, trailing%s%%; "
                                        "LB剔除涨停后条件均值约-2.3%%, 故单票风险大, 须仓位纪律" %
                                        (best_L, best_hold, best_trail))
        json.dump(rules, open(RULES_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n[exec] 已写回 p1_rules.json: max_lianban_len=%s hold_days=%s trailing_pct=%s" %
              (best_L, best_hold, best_trail))
    json.dump({"zt_mode": args.zt_mode, "layer_stat": layer_stat, "hold_stat": hold_stat, "trail_stat": trail_stat,
               "chosen": {"max_lianban_len": best_L, "hold_days": best_hold, "trailing_pct": best_trail}},
              open(os.path.join(PROJ, "reports", "execution_oos_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("[saved] reports/execution_oos_result.json")


if __name__ == "__main__":
    main()
