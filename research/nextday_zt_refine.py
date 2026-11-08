# -*- coding: utf-8 -*-
"""nextday_zt_refine.py -- 在缓存事件上快速迭代: 交易友好型复合 + 制度门 + 机构/游资增强"""
import sys, os, time, json
import numpy as np
import pandas as pd

BASE = r"E:\WorkBuddy_dot_workbuddy\Claw"
CACHE = os.path.join(BASE, "research", "nextday_zt_events.csv")
TOPK = 0.05

def nw_mean_t(y, lags=None):
    y = np.asarray(y, float); n = len(y)
    if n < 3: return 0.0
    resid = y - y.mean()
    if lags is None: lags = int(np.floor(n ** 0.25))
    lags = min(lags, n - 1)
    S = np.mean(resid ** 2)
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        g = np.mean(resid[l:] * resid[:-l]); S += 2 * w * g
    var = S / n; se = np.sqrt(var) if var > 0 else 0.0
    return (y.mean() / se) if se > 0 else 0.0

print("loading cache...", flush=True)
t0 = time.time()
ev = pd.read_csv(CACHE, parse_dates=["date"])
print(f"  events={len(ev):,} ({time.time()-t0:.1f}s)")
base_zt = ev["zt"].mean(); base_succ = ev["success"].mean(); base_ret = ev["ret_T"].mean()
print(f"base zt={base_zt:.3%} succ={base_succ:.3%} ret_T={base_ret:+.4%}")

# 逐日横截面 z
FEAT = ["volr20","volr5","ret1","ret5","ret20","ret60","pos60","nearlim","broke","amp20","consol","upstreak","gap1"]
z = ev.copy()
for f in FEAT:
    z[f] = ev.groupby("date")[f].transform(lambda x: (x - x.mean()) / (x.std(ddof=0) if x.std(ddof=0) > 0 else 1))
z[FEAT] = z[FEAT].fillna(0.0)

# 候选复合
def comp(cols_sign):
    s = pd.Series(0.0, index=z.index)
    for c, sg in cols_sign.items():
        s = s + sg * z[c]
    return s

variants = {
    "V_all":      {**{f: 1 for f in FEAT}, "nearlim": -1},
    "V_trade":    {"gap1":1,"upstreak":1,"pos60":1,"nearlim":1,"broke":1,"consol":1},
    "V_trade2":   {"gap1":1,"upstreak":1,"pos60":0.5},
    "V_gap_up":   {"gap1":1,"upstreak":1},
}

# 制度门: 日内宽度(当日上涨占比) proxy
breadth = ev.groupby("date")["ret_T"].transform(lambda x: (x > 0).mean())
ev["breadth"] = breadth
THR = 0.40
ev["tradeable"] = ev["breadth"] >= THR

def metrics(mask, trade_gate=False, label=""):
    if trade_gate:
        sub = ev[mask & ev["tradeable"]]
        buy = ev[ev["tradeable"]]
    else:
        sub = ev[mask]; buy = ev
    g_buy = buy.groupby("date")["ret_T"].mean()
    g_sel = sub.groupby("date")["ret_T"].mean()
    excess = (g_sel - g_buy.reindex(g_sel.index)).dropna()
    t = nw_mean_t(excess.values)
    r = sub["ret_T"].dropna()
    gains = r[r > 0].sum(); losses = -r[r < 0].sum()
    pf = gains / losses if losses > 0 else float("inf")
    return dict(n=int(len(sub)), sel_zt=sub["zt"].mean(), lift=sub["zt"].mean()/base_zt,
                sel_succ=sub["success"].mean(), excess=excess.mean(), nwt=t, pf=pf)

print("\n=== 复合变体 (无制度门) ===")
for name, cs in variants.items():
    ev["score"] = comp(cs).values
    ev["rk"] = ev.groupby("date")["score"].rank(ascending=False, method="first")
    dn = ev.groupby("date")["score"].transform("size")
    m = metrics(ev["rk"] <= TOPK * dn)
    print(f"  {name:9s} sel_zt={m['sel_zt']:.3%} lift={m['lift']:.2f}x sel_succ={m['sel_succ']:.3%} "
          f"excess={m['excess']:+.4%} NW-t={m['nwt']:.2f} PF={m['pf']:.2f}")

print("\n=== 复合变体 (启用制度门 breadth>=0.40) ===")
for name, cs in variants.items():
    ev["score"] = comp(cs).values
    ev["rk"] = ev.groupby("date")["score"].rank(ascending=False, method="first")
    dn = ev.groupby("date")["score"].transform("size")
    m = metrics(ev["rk"] <= TOPK * dn, trade_gate=True)
    print(f"  {name:9s} sel_zt={m['sel_zt']:.3%} lift={m['lift']:.2f}x sel_succ={m['sel_succ']:.3%} "
          f"excess={m['excess']:+.4%} NW-t={m['nwt']:.2f} PF={m['pf']:.2f} n={m['n']:,}")

# === 机构/游资增强 (intel_features, as-of join by code) ===
print("\n=== 机构/游资增强 (intel as-of join, +regime gate + 2d horizon) ===")
intel = pd.read_csv(os.path.join(BASE, "backtest_cache", "intel", "intel_features.csv.gz"))
intel = intel[["code","date","lhb_net","inst_net","lhb_net_ratio","inst_net_cap"]].copy()
intel["date"] = pd.to_datetime(intel["date"])
intel = intel.dropna(subset=["lhb_net","inst_net"], how="all").sort_values(["date","code"]).reset_index(drop=True)
intel = intel.rename(columns={"date":"intel_date"})

ev_s = ev.drop_duplicates(subset=["code","date"]).sort_values(["date","code"]).reset_index(drop=True)
intel = intel.drop_duplicates(subset=["code","intel_date"]).sort_values(["intel_date","code"]).reset_index(drop=True)
res = pd.merge_asof(ev_s, intel, left_on="date", right_on="intel_date", by="code", direction="backward")
res["has_intel"] = res["lhb_net"].notna()
res["fresh_intel"] = (res["date"] - res["intel_date"]).dt.days <= 5
print(f"  as-of intel coverage: {res['has_intel'].mean():.1%} of events ({res['has_intel'].sum():,})")
res["ret2"] = (1+res["ret_T"].fillna(0))*(1+res["ret_T1"].fillna(0)) - 1
for f in FEAT:
    res[f+"z"] = res.groupby("date")[f].transform(lambda x:(x-x.mean())/(x.std(ddof=0) if x.std(ddof=0)>0 else 1))
intel_cols = [c for c in ["lhb_net","inst_net","lhb_net_ratio","inst_net_cap"] if c in res.columns]
print("  intel cols present:", intel_cols, "| all cols sample:", [c for c in res.columns if 'net' in c or 'inst' in c])
for c in intel_cols:
    res[c+"z"] = res.groupby("date")[c].transform(lambda x:(x-x.mean())/(x.std(ddof=0) if x.std(ddof=0)>0 else 1))
res["intel_sig"] = res[[c+"z" for c in ("lhb_net","inst_net") if (c+"z") in res.columns]].fillna(0).sum(axis=1)
base_gap = {"gap1":1,"upstreak":1,"pos60":0.5}

def eval_score(score_series, gate=False):
    res["score"] = score_series.values
    res["rk"] = res.groupby("date")["score"].rank(ascending=False, method="first")
    dn = res.groupby("date")["score"].transform("size")
    mask = res["rk"] <= TOPK*dn
    if gate:
        mask = mask & (res["breadth"] >= 0.40)
    sub = res[mask]
    buy = res[res["breadth"]>=0.40] if gate else res
    g_buy = buy.groupby("date")["ret_T"].mean()
    g_sel = sub.groupby("date")["ret_T"].mean()
    ex1 = (g_sel - g_buy.reindex(g_sel.index)).dropna()
    t1 = nw_mean_t(ex1.values)
    r = sub["ret_T"].dropna(); gl=r[r>0].sum(); ls=-r[r<0].sum(); pf1=gl/ls if ls>0 else float("inf")
    g_buy2 = buy.groupby("date")["ret2"].mean()
    g_sel2 = sub.groupby("date")["ret2"].mean()
    ex2 = (g_sel2 - g_buy2.reindex(g_sel2.index)).dropna()
    t2 = nw_mean_t(ex2.values)
    return len(sub), sub["zt"].mean(), sub["zt"].mean()/base_zt, ex1.mean(), t1, pf1, ex2.mean(), t2

for tag, use in (("V_trade2", False), ("V_trade2+intel", True)):
    s = sum(sg*res[c+"z"] for c,sg in base_gap.items())
    if use: s = s + 0.5*res["intel_sig"]
    n, zt, lift, e1, t1, pf, e2, t2 = eval_score(s, gate=True)
    print(f"  [{tag} +regime] n_sel={n:,} sel_zt={zt:.3%} lift={lift:.2f}x "
          f"| 1d ex={e1:+.4%} NW-t={t1:.2f} PF={pf:.2f} | 2d ex={e2:+.4%} NW-t={t2:.2f}")
    n, zt, lift, e1, t1, pf, e2, t2 = eval_score(s, gate=False)
    print(f"  [{tag} no-gate ] n_sel={n:,} sel_zt={zt:.3%} lift={lift:.2f}x "
          f"| 1d ex={e1:+.4%} NW-t={t1:.2f} PF={pf:.2f} | 2d ex={e2:+.4%} NW-t={t2:.2f}")

print("\n=== 质量门控实验 (fresh intel + 剔除炸板近端) ===")
# 基础分: gap1+upstreak+pos60, 已算在 res 的 z 列
def score_quality(fresh_only, exclude_nearlim):
    s = res["gap1z"] + res["upstreakz"] + 0.5*res["pos60z"]
    if fresh_only:
        s = s + 0.5*res["intel_sig"]*res["fresh_intel"]
    return s

for tag, fresh, excl in (("base+regime", False, False),
                         ("fresh_intel+regime", True, False),
                         ("excl_nearlim+regime", False, True),
                         ("fresh+excl+regime", True, True)):
    s = score_quality(fresh, excl)
    res["score"] = s.values
    res["rk"] = res.groupby("date")["score"].rank(ascending=False, method="first")
    dn = res.groupby("date")["score"].transform("size")
    mask = res["rk"] <= TOPK*dn
    if excl:
        # 剔除最贴近涨停(炸板风险最高)的 10%
        mask = mask & (res["nearlim"] > res["nearlim"].groupby(res["date"]).transform(lambda x: x.quantile(0.10)))
    mask = mask & (res["breadth"] >= 0.40)
    sub = res[mask]
    buy = res[res["breadth"] >= 0.40]
    g_buy = buy.groupby("date")["ret_T"].mean()
    g_sel = sub.groupby("date")["ret_T"].mean()
    ex = (g_sel - g_buy.reindex(g_sel.index)).dropna()
    t = nw_mean_t(ex.values)
    r = sub["ret_T"].dropna(); gl=r[r>0].sum(); ls=-r[r<0].sum(); pf=gl/ls if ls>0 else float("inf")
    print(f"  [{tag}] n_sel={len(sub):,} sel_zt={sub['zt'].mean():.3%} lift={sub['zt'].mean()/base_zt:.2f}x "
          f"sel_succ={sub['success'].mean():.3%} | 1d ex={ex.mean():+.4%} NW-t={t:.2f} PF={pf:.2f}")

print("\n[done refine]")
