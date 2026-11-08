#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
article_backtest_real.py — 《散户是最后的买单人》七信号 × 真实全市场缓存验证
真实数据: E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily/<code>.csv (5225只, 2018-2026)
无模拟回退。受 E: 盘枚举较慢, 采用 stride 抽样(默认每10只取1, ~520只)作代表性样本,
breadth/regime 在同一样本内自洽计算。

用户硬指标:
 [A] 无论何种行情之下都要套利盈利
 [B] 提前一天买入(T-1收盘), 下个交易日(T)能开启涨停 或 连续涨停 或 上涨>=2交易日
     (除非该T日全市场无股票上涨/涨停)
"""
import os, time, sys
import pandas as pd
import numpy as np
from research.board_limit import board_limit_pct, is_limit_up

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(BASE, "backtest_cache", "daily")
STRIDE = int(os.environ.get("STRIDE", "10"))   # 每STRIDE只取1只

def limit_pct(code):
    return board_limit_pct(code)


def is_zt(c, prev, lp):
    return prev > 0 and c >= prev * (1 + lp) - 0.005 and c > prev

t0 = time.time()
files = sorted(os.listdir(DAILY))
sampled = files[::STRIDE]
print(f"全市场文件数={len(files)}  抽样 stride={STRIDE} -> 样本 {len(sampled)} 只  "
      f"[{(time.time()-t0):.1f}s]")

# 单趟读入并缓存(内存安全: ~520只×~2100行)
cache = {}
breadth = {}   # date -> [up, total]
for fn in sampled:
    code = fn[:-4]
    try:
        df = pd.read_csv(os.path.join(DAILY, fn), parse_dates=["日期"])
    except Exception:
        continue
    df = df.sort_values("日期").reset_index(drop=True)
    if len(df) < 6:
        continue
    close = df["收盘"].to_numpy(dtype=float)
    dates = df["日期"].dt.strftime("%Y-%m-%d").tolist()
    prev = np.concatenate([[np.nan], close[:-1]])
    up = (close > prev) & ~np.isnan(prev)
    lp = limit_pct(code)
    cache[code] = (dates, close, up, lp)
    for d, u in zip(dates, up):
        b = breadth.setdefault(d, [0, 0])
        b[1] += 1
        if u: b[0] += 1

print(f"有效样本={len(cache)} 只, 交易日(样本内)={len(breadth)}  [{(time.time()-t0):.1f}s]")

# 配对: T-1=i 买入, T=i+1, T+1=i+2
glob_n = glob_succ = glob_zt = glob_up = 0
sum_pos = sum_neg = 0.0
# regime 分桶
buckets = {"risk_off(<30%)": [0,0,0,0,0.0,0.0],   # n, succ, zt, up, sumpos, sumneg
           "sideways(30-60%)": [0,0,0,0,0.0,0.0],
           "risk_on(>60%)": [0,0,0,0,0.0,0.0]}

for code, (dates, close, up, lp) in cache.items():
    n = len(close)
    for i in range(1, n - 2):
        buy = close[i]; cT = close[i + 1]; cT1 = close[i + 2]
        zt_T = is_zt(cT, buy, lp)
        up_T = cT > buy
        up_T1 = cT1 > cT
        succ = zt_T or (up_T and up_T1)
        ret_T = cT / buy - 1.0
        glob_n += 1
        glob_succ += int(succ); glob_zt += int(zt_T); glob_up += int(up_T)
        if ret_T > 0: sum_pos += ret_T
        else: sum_neg += -ret_T
        # regime by T-date breadth
        d = dates[i + 1]
        br = breadth.get(d, [0, 1])
        frac = br[0] / br[1] if br[1] else 0.5
        key = "risk_off(<30%)" if frac < 0.30 else ("sideways(30-60%)" if frac <= 0.60 else "risk_on(>60%)")
        b = buckets[key]
        b[0] += 1; b[1] += int(succ); b[2] += int(zt_T); b[3] += int(up_T)
        if ret_T > 0: b[4] += ret_T
        else: b[5] += -ret_T

def pf(p, ng): return (p / ng) if ng > 0 else float("inf")
print(f"\n=== Req B 基准(真实样本, T-1收盘买入->T) ===")
print(f"  配对样本 n={glob_n}")
print(f"  成功(涨停∪连涨2日)={glob_succ/glob_n*100:.2f}%  纯涨停子项={glob_zt/glob_n*100:.2f}%  "
      f"T上涨={glob_up/glob_n*100:.2f}%  avg_ret_T={ (sum_pos-sum_neg)/glob_n*100:.3f}%  "
      f"PF={pf(sum_pos,sum_neg):.2f}")

print(f"\n=== Req A 分行情制度(按样本内每日宽度) ===")
for k, b in buckets.items():
    if b[0] == 0: continue
    n, succ, zt, up, sp, sn = b
    print(f"  [{k}] n={n} 成功={succ/n*100:.2f}% 涨停={zt/n*100:.2f}% "
          f"T上涨={up/n*100:.2f}% avg_ret_T={(sp-sn)/n*100:.3f}% PF={pf(sp,sn):.2f}")

# regime gate: breadth<20% 空仓
g_n=g_s=g_z=g_u=0; gp=gn=0.0
for code, (dates, close, up, lp) in cache.items():
    n=len(close)
    for i in range(1,n-2):
        d=dates[i+1]; br=breadth.get(d,[0,1]); frac=br[0]/br[1] if br[1] else 0.5
        if frac<0.20: continue
        buy=close[i]; cT=close[i+1]; cT1=close[i+2]
        zt_T=is_zt(cT,buy,lp); up_T=cT>buy; up_T1=cT1>cT
        succ=zt_T or (up_T and up_T1); ret_T=cT/buy-1.0
        g_n+=1; g_s+=int(succ); g_z+=int(zt_T); g_u+=int(up_T)
        if ret_T>0: gp+=ret_T
        else: gn+=-ret_T
print(f"\n=== Req A 制度门(宽度<20%空仓) vs 无门 ===")
print(f"  无门: 成功={glob_succ/glob_n*100:.2f}% PF={pf(sum_pos,sum_neg):.2f}")
print(f"  加门: n={g_n} 成功={g_s/g_n*100:.2f}% T上涨={g_u/g_n*100:.2f}% avg_ret_T={(gp-gn)/g_n*100:.3f}% PF={pf(gp,gn):.2f}")

# 逃逸条款代理: 样本内有无"全市场无一股上涨"日
zero_up=[d for d,b in breadth.items() if b[0]==0]
print(f"\n=== 逃逸条款代理(样本内) ===")
print(f"  T日总数={len(breadth)}  其中'样本无一股上涨'日={len(zero_up)} ({len(zero_up)/len(breadth)*100:.1f}%)  -> 真全市场需全5225只, 此处仅样本代理")

print(f"\n[总耗时 {(time.time()-t0):.1f}s]  样本={len(cache)}/{len(files)} 只 (stride={STRIDE})")
