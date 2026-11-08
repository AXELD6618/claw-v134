# -*- coding: utf-8 -*-
"""统一选股验证回测（只读日线，不改架构）。
合成两套预breakout信号，测 OOS 命中/PF，并回放 大业(603278,2026-10-15) / 新华传媒(600825,2026-09-04)。
REVERSAL  : T日下跌 3~8% 且未涨停 -> 次日涨停/高开/强弹（大业10-15型）
PRE_BREAKOUT: T日在涨(ret20>10%) + 近20日高(dist_high20>-6%) + 20日内有涨停基因 -> 次日涨停（新华传媒9-4延续型）
run: .venv/Scripts/python.exe research/unified_backtest.py
"""
import os, glob, json
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

DAILY = "backtest_cache/daily"
TRAIN_END = "2024-12-31"   # OOS = 2025+
LIM_HI, LIM_LO = -0.03, -0.08  # REVERSAL 跌幅窗口 3%~8%

def lim_of(code):
    return board_limit_pct(code)


def proc(f):
    code = os.path.splitext(os.path.basename(f))[0]
    try:
        df = pd.read_csv(f, usecols=["日期", "开盘", "收盘", "最高", "最低", "成交量"])
    except Exception:
        return None
    if len(df) < 60: return None
    df = df.dropna().sort_values("日期").reset_index(drop=True)
    c = df["收盘"].astype(float).values; o = df["开盘"].astype(float).values
    h = df["最高"].astype(float).values; v = df["成交量"].astype(float).values
    d = df["日期"].astype(str).str.slice(0, 10).values
    n = len(c)
    prev = np.concatenate([[np.nan], c[:-1]])
    ret1 = c / prev - 1.0
    limc = lim_of(code)
    hi20 = pd.Series(h).rolling(20).max().values
    vol20 = pd.Series(v).rolling(20).mean().values
    zt_ind = (ret1 >= limc - 0.011).astype(float)
    gene20 = pd.Series(zt_ind).rolling(20).max().fillna(0).values
    buyable = (ret1 < limc - 0.005)
    if n < 36: return None
    idx = np.arange(30, n - 5)
    if len(idx) == 0: return None
    sel = idx[buyable[idx]]
    if len(sel) == 0: return None
    c0 = c[sel]; nxt_o = o[sel + 1]; nxt_c = c[sel + 1]
    zt1 = (nxt_c >= c0 * (1 + limc) - 0.011).astype(int)
    gapup = (nxt_o / c0 - 1 > 0.01).astype(int)
    buy = ((gapup == 1) | (zt1 == 1)).astype(int)
    cum5 = (c[sel + 5] / c0 - 1 >= 0.20).astype(int)
    dist_high20 = c0 / hi20[sel] - 1
    ret20 = c0 / prev[np.maximum(sel - 19, 0)] - 1
    vol_ratio = v[sel] / vol20[sel]
    r1 = ret1[sel]
    reversal = (r1 <= LIM_HI) & (r1 > LIM_LO)
    pre_break = (ret20 > 0.10) & (dist_high20 > -0.06) & (vol_ratio > 1.3) & (gene20[sel] > 0)
    is_train = (np.array([d[i] for i in sel]) <= TRAIN_END)
    out = pd.DataFrame({
        "code": code, "date": [d[i] for i in sel],
        "r1": r1, "dist_high20": dist_high20, "ret20": ret20, "vol_ratio": vol_ratio,
        "reversal": reversal.astype(int), "pre_break": pre_break.astype(int),
        "zt1": zt1, "gapup": gapup, "buy": buy, "cum5": cum5,
        "train": is_train.astype(int),
        "nxt_open_ret": nxt_o / c0 - 1, "nxt_close_ret": nxt_c / c0 - 1,
    })
    return out

def summarize(df, mask, name):
    s = df[mask & (df.train == 0)]  # OOS
    if len(s) == 0:
        print(f"  [{name}] OOS n=0"); return
    base = df[df.train == 0]
    print(f"  [{name}] OOS n={len(s):,}  均值T+1开={s.nxt_open_ret.mean()*100:+.2f}%  收={s.nxt_close_ret.mean()*100:+.2f}%")
    for lab in ("zt1", "gapup", "buy", "cum5"):
        hr = s[lab].mean(); br = base[lab].mean()
        lift = hr / br if br > 0 else float("nan")
        print(f"      {lab:6s} 命中={hr*100:5.2f}%  基线={br*100:5.2f}%  Lift={lift:.2f}x  n={len(s):,}")

def main():
    files = glob.glob(os.path.join(DAILY, "*.csv"))
    parts = [p for p in (proc(f) for f in files) if p is not None]
    df = pd.concat(parts, ignore_index=True)
    print(f"面板: 股票={df.code.nunique()} stock-day={len(df):,}  训练={int((df.train==1).sum()):,} OOS={int((df.train==0).sum()):,}")
    print("\n=== 各信号 OOS 表现 ===")
    summarize(df, df.reversal == 1, "REVERSAL(跌3-8%)")
    summarize(df, df.pre_break == 1, "PRE_BREAKOUT(在涨近高+放量+涨停基因)")
    summarize(df, (df.reversal | df.pre_break) == 1, "合并(REVERSAL∪PRE_BREAKOUT)")
    print("\n=== 回放两只标的 ===")
    for code, day in (("603278", "2026-10-15"), ("600825", "2026-09-04")):
        r = df[(df.code == code) & (df.date == day)]
        if r.empty:
            print(f"  {code} {day}: 无记录(可能停牌/数据缺失)"); continue
        x = r.iloc[0]
        print(f"  {code} {day}: 跌幅={x.r1*100:+.2f}% 近20高={x.dist_high20*100:+.1f}% ret20={x.ret20*100:+.1f}% 量比={x.vol_ratio:.2f}")
        print(f"      REVERSAL={x.reversal}  PRE_BREAKOUT={x.pre_break}  -> 次日涨停={x.zt1} 高开={x.gapup} 5日≥20%={x.cum5}")
        print(f"      T+1开={x.nxt_open_ret*100:+.2f}% T+1收={x.nxt_close_ret*100:+.2f}% (涨停限幅约{10 if not str(code).startswith(('30','688')) else 20}%)")
    # 新华传媒 9-5+ 数据缺失提示
    print("\n注: 新华传媒(600825)日线止于2026-09-04(其后停牌/国庆无记录)，故9-4的T+1标签不可算，仅看技术形态是否被PRE_BREAKOUT命中。")

if __name__ == "__main__":
    main()
