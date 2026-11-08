# -*- coding: utf-8 -*-
"""
nextday_zt_miner.py  --  首板 initiation miner (T-1 非涨停 -> T 涨停/连涨)
真实系统: E:/WorkBuddy_dot_workbuddy/Claw  (5225 只日线缓存)
方法论: 按日配对超额 + Newey-West t + 时间分段稳定性 (Purged-aware)

Label (用户硬指标):
  zt_T    = T 日是否涨停 (交易所口径)
  success = zt_T  OR (T 涨 AND T+1 涨)  即 "涨停 或 连续上涨两日以上"

Universe (用户硬约束):
  T-1 当日必须 NON-涨停 (可买入的非涨停标的)

Features @ T-1 close (无前视):
  volr20/volr5  量比(对20/5日均量)
  ret1/ret5/ret20/ret60  动量
  pos60         60日价位分位(强势)
  nearlim       (涨停价-close)/prev 越小=越贴近涨停(摸板)
  broke         当日曾触涨停但未封(烂板/开板) -> 次日溢价强信号
  amp20         20日均振幅
  consol        低波动横盘后放量突破 (r31 精神)
  upstreak      连续上涨天数
  gap1          昨收->今开跳空
"""
import sys, glob, os, time, json
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

BASE = r"E:\WorkBuddy_dot_workbuddy\Claw"
DAILY = os.path.join(BASE, "backtest_cache", "daily")
STRIDE = int(sys.argv[1]) if len(sys.argv) > 1 else 3
TOPK = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05  # 每日选前 5%

# ---------------- 涨停口径 (交易所) ----------------
def limit_pct(code: int) -> float:
    return board_limit_pct(code)


def is_zt(close, prev, lp):
    return (close >= round(prev * (1 + lp), 2) - 0.005) & prev.notna()

# ---------------- Newey-West mean t ----------------
def nw_mean_t(y, lags=None):
    y = np.asarray(y, float)
    n = len(y)
    if n < 3:
        return 0.0
    resid = y - y.mean()
    if lags is None:
        lags = int(np.floor(n ** 0.25))
    lags = min(lags, n - 1)
    S = np.mean(resid ** 2)
    for l in range(1, lags + 1):
        w = 1 - l / (lags + 1)
        g = np.mean(resid[l:] * resid[:-l])
        S += 2 * w * g
    var = S / n
    se = np.sqrt(var) if var > 0 else 0.0
    return (y.mean() / se) if se > 0 else 0.0

# ---------------- 单股处理 ----------------
BAD = []
def process_file(path):
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as e:
        BAD.append((os.path.basename(path), f"read:{e}"))
        return None
    if len(df) < 60:
        return None
    req = ("日期", "开盘", "收盘", "最高", "最低", "成交量")
    if not all(c in df.columns for c in req):
        BAD.append((os.path.basename(path), f"cols:{list(df.columns)}"))
        return None
    try:
        df = df.sort_values("日期").reset_index(drop=True)
        o = df["开盘"].astype(float); c = df["收盘"].astype(float)
        h = df["最高"].astype(float); l = df["最低"].astype(float); v = df["成交量"].astype(float)
        codekey = int(df.iloc[0]["code"]) if "code" in df.columns else int(os.path.basename(path).split(".")[0])
        lp = limit_pct(codekey)
        prev = c.shift(1)
        zt = is_zt(c, prev, lp)
        ret = c / prev - 1
        vol_ma20 = v.rolling(20).mean()
        vol_ma5 = v.rolling(5).mean()
        f_volr20 = v / vol_ma20
        f_volr5 = v / vol_ma5
        f_ret1 = ret
        f_ret5 = c / c.shift(5) - 1
        f_ret20 = c / c.shift(20) - 1
        f_ret60 = c / c.shift(60) - 1
        lo60 = c.rolling(60).min(); hi60 = c.rolling(60).max()
        f_pos60 = (c - lo60) / (hi60 - lo60)
        limit_price = prev * (1 + lp)
        f_nearlim = (limit_price - c) / prev
        f_broke = (h >= limit_price - 0.005) & (~zt)
        amp = (h - l) / prev
        f_amp20 = amp.rolling(20).mean()
        f_consol = (ret.rolling(20).std() < 0.015) & (ret > 0.03)
        up = ret > 0
        grp = (~up).cumsum()
        f_upstreak = up.groupby(grp).cumsum()
        f_gap1 = (o - c.shift(1)) / c.shift(1)

        rows = []
        n = len(df)
        for i in range(2, n - 1):
            if zt.iloc[i - 1]:           # T-1 必须非涨停 (用户硬约束)
                continue
            if pd.isna(prev.iloc[i - 1]) or vol_ma20.iloc[i - 1] == 0:
                continue
            ret_i = ret.iloc[i]
            ret_i1 = ret.iloc[i + 1] if i + 1 < n else np.nan
            zt_i = bool(zt.iloc[i])
            success = zt_i or (pd.notna(ret_i) and ret_i > 0 and pd.notna(ret_i1) and ret_i1 > 0)
            rows.append({
                "code": codekey, "date": df["日期"].iloc[i],
                "volr20": f_volr20.iloc[i - 1], "volr5": f_volr5.iloc[i - 1],
                "ret1": f_ret1.iloc[i - 1], "ret5": f_ret5.iloc[i - 1],
                "ret20": f_ret20.iloc[i - 1], "ret60": f_ret60.iloc[i - 1],
                "pos60": f_pos60.iloc[i - 1], "nearlim": f_nearlim.iloc[i - 1],
                "broke": float(f_broke.iloc[i - 1]), "amp20": f_amp20.iloc[i - 1],
                "consol": float(f_consol.iloc[i - 1]), "upstreak": f_upstreak.iloc[i - 1],
                "gap1": f_gap1.iloc[i - 1],
                "zt": zt_i, "success": success,
                "ret_T": ret_i, "ret_T1": ret_i1,
            })
        return pd.DataFrame(rows) if rows else None
    except Exception as e:
        BAD.append((os.path.basename(path), f"proc:{e}"))
        return None

# ---------------- 主流程 ----------------
t0 = time.time()
files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
files = files[::STRIDE]
print(f"[load] files={len(files)} stride={STRIDE}")
parts = []
for k, f in enumerate(files):
    r = process_file(f)
    if r is not None:
        parts.append(r)
    if (k + 1) % 500 == 0:
        print(f"  ...{k+1}/{len(files)}")
ev = pd.concat(parts, ignore_index=True)
ev["date"] = pd.to_datetime(ev["date"])
print(f"[load] events={len(ev):,}  codes={ev['code'].nunique()}  date {ev['date'].min().date()}->{ev['date'].max().date()}  ({time.time()-t0:.1f}s)")
print(f"[load] skipped {len(BAD)} bad files; e.g. {BAD[:3]}")

# 基线
base_zt = ev["zt"].mean()
base_succ = ev["success"].mean()
print(f"[base] zt_rate={base_zt:.4%}  success_rate={base_succ:.4%}")

# ---------------- 特征方向与打分 ----------------
FEATURES = ["volr20","volr5","ret1","ret5","ret20","ret60","pos60","nearlim",
            "broke","amp20","consol","upstreak","gap1"]
# 先验方向(领域逻辑, 非拟合): nearlim 越小越好 -> 符号取负
SIGN = {"volr20":+1,"volr5":+1,"ret1":+1,"ret5":+1,"ret20":+1,"ret60":+1,
        "pos60":+1,"nearlim":-1,"broke":+1,"amp20":+1,"consol":+1,"upstreak":+1,"gap1":+1}

# 逐日横截面 z-score (无时间泄漏)
z = ev.copy()
for fcol in FEATURES:
    z[fcol] = ev.groupby("date")[fcol].transform(lambda x: (x - x.mean()) / (x.std(ddof=0) if x.std(ddof=0) > 0 else 1))
z[FEATURES] = z[FEATURES].fillna(0.0)
z["score"] = sum(SIGN[fcol] * z[fcol] for fcol in FEATURES)
ev["score"] = z["score"].values

# ---------------- 单变量诊断 (top  Decile zt 率 + 超额贡献) ----------------
print("\n[univariate] feature -> top-decile zt/succ rate + mean ret_T (excess vs base ret)")
base_ret = ev["ret_T"].mean()
for fcol in FEATURES:
    q = ev[fcol].quantile(0.9)
    top = ev[ev[fcol] >= q]
    print(f"  {fcol:9s} zt={top['zt'].mean():.3%} succ={top['success'].mean():.3%} "
          f"ret_T={top['ret_T'].mean():+.4%} (excess {top['ret_T'].mean()-base_ret:+.4%}) n={len(top):,}")

# ---------------- 每日选前 TOPK ----------------
ev["rank_in_day"] = ev.groupby("date")["score"].rank(ascending=False, method="first")
day_n = ev.groupby("date")["score"].transform("size")
ev["sel"] = ev["rank_in_day"] <= (TOPK * day_n)

def metrics(mask, label=""):
    sub = ev[mask]
    # 逐日配对超额
    g_buy = ev.groupby("date")["ret_T"].mean()
    g_sel = sub.groupby("date")["ret_T"].mean()
    excess = (g_sel - g_buy.reindex(g_sel.index)).dropna()
    t = nw_mean_t(excess.values)
    r = sub["ret_T"].dropna()
    gains = r[r > 0].sum(); losses = -r[r < 0].sum()
    pf = (gains / losses) if losses > 0 else float("inf")
    return {
        "n": int(len(sub)), "base_zt": base_zt, "sel_zt": sub["zt"].mean(),
        "lift_zt": sub["zt"].mean() / base_zt if base_zt > 0 else 0,
        "base_succ": base_succ, "sel_succ": sub["success"].mean(),
        "excess_mean": excess.mean(), "nw_t": t, "pf": pf,
    }

m_all = metrics(ev["sel"], "ALL")
print(f"\n[SELECT top {TOPK:.0%}] n={m_all['n']:,}  sel_zt={m_all['sel_zt']:.3%} (base {m_all['base_zt']:.3%}, lift {m_all['lift_zt']:.2f}x)")
print(f"  sel_succ={m_all['sel_succ']:.3%} (base {m_all['base_succ']:.3%})  excess={m_all['excess_mean']:+.4%}/d  NW-t={m_all['nw_t']:.2f}  PF={m_all['pf']:.2f}")

# ---------------- 时间分段稳定性 (Purged-aware) ----------------
print("\n[stability by year]")
for yr in range(2019, 2027):
    m = metrics((ev["sel"]) & (ev["date"].dt.year == yr))
    print(f"  {yr}: n={m['n']:,} sel_zt={m['sel_zt']:.3%} lift={m['lift_zt']:.2f}x excess={m['excess_mean']:+.4%} NW-t={m['nw_t']:.2f} PF={m['pf']:.2f}")

# 5 段时间折 (walk-forward 风格, 仅报告后段 OOS)
print("\n[5-fold time split OOS]")
cuts = pd.qcut(ev["date"].rank(method="first"), 5, labels=False)
for k in range(5):
    m = metrics((ev["sel"]) & (cuts == k))
    print(f"  fold{k} ({(ev[cuts==k]['date'].min().date())}~{(ev[cuts==k]['date'].max().date())}): n={m['n']:,} sel_zt={m['sel_zt']:.3%} lift={m['lift_zt']:.2f}x excess={m['excess_mean']:+.4%} NW-t={m['nw_t']:.2f} PF={m['pf']:.2f}")

# ---------------- 后验: 2024-2026 纯样本 (无权重前视) ----------------
m_oos = metrics((ev["sel"]) & (ev["date"] >= "2024-01-01"))
print(f"\n[POST-2024 OOS] n={m_oos['n']:,} sel_zt={m_oos['sel_zt']:.3%} lift={m_oos['lift_zt']:.2f}x excess={m_oos['excess_mean']:+.4%} NW-t={m_oos['nw_t']:.2f} PF={m_oos['pf']:.2f}")

# ---------------- 阈值灵敏度 ----------------
print("\n[sensitivity TOPK]")
for tk in (0.01, 0.02, 0.05, 0.10, 0.20):
    rk = ev.groupby("date")["score"].rank(ascending=False, method="first")
    dn = ev.groupby("date")["score"].transform("size")
    mm = metrics(rk <= tk * dn)
    print(f"  top{tk:.0%}: sel_zt={mm['sel_zt']:.3%} lift={mm['lift_zt']:.2f}x excess={mm['excess_mean']:+.4%} NW-t={mm['nw_t']:.2f} PF={mm['pf']:.2f}")

# ---------------- 结果落盘 ----------------
out = {
    "stride": STRIDE, "n_events": int(len(ev)), "n_codes": int(ev["code"].nunique()),
    "date_min": str(ev["date"].min().date()), "date_max": str(ev["date"].max().date()),
    "base_zt": base_zt, "base_succ": base_succ,
    "select_topk": TOPK, "metrics_all": m_all,
    "metrics_post2024": m_oos,
}
with open(os.path.join(BASE, "research", "nextday_zt_miner_result.json"), "w") as fp:
    json.dump(out, fp, indent=2, default=str)
ev.to_csv(os.path.join(BASE, "research", "nextday_zt_events.csv"), index=False)
print("\n[done] wrote research/nextday_zt_miner_result.json + nextday_zt_events.csv")
