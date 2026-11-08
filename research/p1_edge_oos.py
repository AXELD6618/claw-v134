#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""系统基石 edge 样本外(walk-forward)验证 — 主理人独立验收视角.

对"唯二真 edge"做严格时间切分 OOS:
  - 连板惯性 (LB): >=2连板(T-1,T涨停) -> T+1 表现
  - 断层反包 (DC): 昨日涨停+今日未涨停 -> T+1 表现
训练期 2018-2024 估基线, 测试期 2025-2026 验证(样本外).

关键检验:
  1. 样本外 T+1 涨停率 vs 市场基线(全体次日涨停率) -> lift + Welch p
  2. 样本外 T+1 收益率均值/中位数/胜率
  3. 断层反包 剔除 T+1 涨停日后的条件收益均值 -> edge 是否全靠涨停日硬撑
数据: backtest_cache/daily (5330 只, 2018-2026, 完整)
"""
import os, sys, glob, json, math
import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
from research.board_limit import board_limit_pct  # 涨跌停幅度单一真相源（2026-11-07 收口）

DAILY = os.path.join(PROJ, "backtest_cache", "daily")
LIM = 0.095          # legacy 涨停阈值(距昨收 >=9.5%) —— 不区分板块，已证污染连板样本
CUT = "2025-01-01"   # 训练/测试分界
# 2026-11-07 收口：默认 canonical（板块口径，与 research.board_limit.is_limit_up 一致）；
# legacy 仅用于复现旧 7259/0.345089 数字。可用环境变量 P1_ZT_MODE 或 --zt-mode 覆盖。
ZT_MODE = os.environ.get("P1_ZT_MODE", "canonical")


def _code_of(fn):
    """日线缓存文件名即股票代码（如 689009.csv）。"""
    b = os.path.basename(fn)
    if "." in b:
        b = b[:b.index(".")]
    return b


def _zt_array(code, c, mode):
    """涨停布尔数组（长度 len(c)，zt[k]=第 k 日涨停，zt[0]=False，与 legacy 索引对齐）。

    legacy=距昨收>=9.5%（旧口径）；canonical=板块口径 is_limit_up。
    canonical 用 ROUND_HALF_UP 到 2 位（floor(x*100+0.5)/100），与 is_limit_up 的
    Decimal ROUND_HALF_UP 语义一致，规避 np.round 的 half-to-even 歧义（已抽样 0 分歧）。
    """
    n = len(c)
    zt = np.zeros(n, dtype=bool)
    if mode == "legacy":
        pct = np.zeros(n); pct[1:] = c[1:] / c[:-1] - 1
        return pct >= LIM
    lim = board_limit_pct(code)
    prev = c[:-1]; cur = c[1:]   # 第 k 日(k>=1): prev=c[k-1], cur=c[k]
    lim_px = np.floor(prev * (1.0 + lim) * 100 + 0.5) / 100.0
    zt[1:] = cur >= (lim_px - 0.005)
    return zt


def proc(fn, mode=None):
    mode = mode or ZT_MODE
    try:
        df = pd.read_csv(fn, usecols=[0, 2])
    except Exception:
        return None
    if df.shape[0] < 5:
        return None
    dates = df.iloc[:, 0].astype(str).values
    c = df.iloc[:, 1].astype(float).values
    n = len(c)
    code = _code_of(fn)
    zt = _zt_array(code, c, mode)
    sig = []   # (kind, date, t1_ret, t1_zt)
    base_num = int(zt[1:].sum()); base_den = n - 1
    for i in range(2, n - 1):
        if zt[i] and zt[i - 1]:                       # 连板惯性
            sig.append(("LB", dates[i], c[i + 1] / c[i] - 1, bool(zt[i + 1])))
        elif zt[i - 1] and not zt[i]:                 # 断层反包
            sig.append(("DC", dates[i], c[i + 1] / c[i] - 1, bool(zt[i + 1])))
    return sig, base_num, base_den


def welch_p(rate, n, base):
    if n < 2:
        return None
    se = math.sqrt(rate * (1 - rate) / n + base * (1 - base) / max(1, n))
    if se == 0:
        return 0.0
    z = (rate - base) / se
    from scipy import stats as st
    return float(1 - st.norm.cdf(z))


def summarize(sigs, cut):
    tr = [s for s in sigs if s[1] < cut]
    te = [s for s in sigs if s[1] >= cut]
    out = {}
    for kind, name in (("LB", "连板惯性"), ("DC", "断层反包")):
        for tag, grp in (("train", tr), ("test", te)):
            g = [s for s in grp if s[0] == kind]
            if not g:
                out[f"{kind}_{tag}"] = None; continue
            t1zt = np.array([s[3] for s in g], float)
            t1r = np.array([s[2] for s in g], float)
            ex = t1r[~(t1zt.astype(bool))] if (~t1zt.astype(bool)).any() else np.array([])
            out[f"{kind}_{tag}"] = dict(
                n=int(len(g)),
                t1_zt_rate=float(t1zt.mean()),
                t1_ret_mean=float(t1r.mean()),
                t1_ret_median=float(np.median(t1r)),
                win_rate=float((t1r > 0).mean()),
                t1_ret_ex_zt_mean=float(ex.mean()) if len(ex) else None,
            )
    return out, tr, te


def main():
    import argparse
    ap = argparse.ArgumentParser(description="连板/断层 OOS 验证（2026-11-07 收口到板块口径）")
    ap.add_argument("--zt-mode", choices=["legacy", "canonical"], default=ZT_MODE,
                    help="涨停判定口径: legacy=旧 0.095 固定阈值(可复现 7259/0.345089); "
                         "canonical=板块口径 is_limit_up(默认)")
    ap.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = ap.parse_args()
    mode = args.zt_mode

    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    print(f"[scan] {len(files)} 只日线, 区间 2018-2026, 分界={CUT}, zt_mode={mode}")
    all_sig = []; bnum = 0; bden = 0
    for k, fn in enumerate(files):
        r = proc(fn, mode=mode)
        if r is None:
            continue
        sig, bn, bd = r
        all_sig += sig; bnum += bn; bden += bd
        if (k + 1) % 1000 == 0:
            print(f"  ...{k+1}/{len(files)}")
    base = bnum / max(1, bden)
    print(f"[base] 市场全体次日涨停率 = {base:.4f} (n={bden})")
    summ, tr, te = summarize(all_sig, CUT)
    print(f"[signals] LB={sum(1 for s in all_sig if s[0]=='LB')} DC={sum(1 for s in all_sig if s[0]=='DC')}  train={len(tr)} test={len(te)}")

    def line(kind, name):
        t = summ.get(f"{kind}_train"); e = summ.get(f"{kind}_test")
        if not e:
            print(f"  {name}: 测试期样本不足"); return
        p = welch_p(e["t1_zt_rate"], e["n"], base)
        lift = e["t1_zt_rate"] / base if base else float("nan")
        print(f"  {name} [测试期 2025-2026, 样本外]:")
        print(f"    n={e['n']}  T+1涨停率={e['t1_zt_rate']:.4f} (基线{base:.4f}, lift={lift:.2f}x, Welch p={p:.2e})")
        print(f"    T+1收益均值={e['t1_ret_mean']:+.4f} 中位数={e['t1_ret_median']:+.4f} 胜率={e['win_rate']:.3f}")
        if e.get("t1_ret_ex_zt_mean") is not None:
            print(f"    剔除T+1涨停日后条件均值={e['t1_ret_ex_zt_mean']:+.4f}  (验证edge是否全靠涨停日硬撑)")
        if t:
            pt = welch_p(t["t1_zt_rate"], t["n"], base)
            print(f"    [对照 训练期] n={t['n']} T+1涨停率={t['t1_zt_rate']:.4f} lift={t['t1_zt_rate']/base:.2f}x p={pt:.2e} 收益均值={t['t1_ret_mean']:+.4f}")

    print("=" * 60)
    print("样本外 OOS 验证结论 (2025-2026 为从未参与基线估计的测试集):")
    line("LB", "连板惯性")
    line("DC", "断层反包")
    print("=" * 60)
    out = {"base_t1_zt_rate": round(base, 4), "cut": CUT,
           "n_files": len(files), "n_LB": sum(1 for s in all_sig if s[0] == "LB"),
           "n_DC": sum(1 for s in all_sig if s[0] == "DC"),
           "summary": {k: v for k, v in summ.items() if v is not None},
           "welch": {kind: welch_p(summ[f"{kind}_test"]["t1_zt_rate"], summ[f"{kind}_test"]["n"], base)
                     for kind in ("LB", "DC") if summ.get(f"{kind}_test")}}
    out_path = args.out or os.path.join(PROJ, "reports", "edge_oos_result.json")
    out["zt_mode"] = mode
    json.dump(out, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[saved] {out_path} (zt_mode={mode})")


if __name__ == "__main__":
    main()
