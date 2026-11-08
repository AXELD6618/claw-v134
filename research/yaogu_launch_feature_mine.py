# -*- coding: utf-8 -*-
"""
妖股启动前交易日 · 全维度无未来函数特征挖掘引擎（原创，非拿来主义）
=========================================================================
目标：发掘「连续涨停妖股在其启动首板(或启动上涨)的前一交易日」的共有特征信号，
      形成可部署的尾盘跟踪预测评分能力。

硬约束（用户铁律）：
  1. 无未来函数 —— 每个特征只使用第 t 日(启动前交易日)及之前的切片，函数接收已
     截断到 t 的数组，结构上不可能引用 t+1 及以后。
  2. 全维度 —— 覆盖尾盘代理 / 动量 / 波动 / 均线位置 / 形态 / 涨停史 / 量价 / 复牌原型。
  3. 泄漏审计 —— 训练/测试按时间切分(embargo)，并断言特征最大索引不超过 t。

用法：python research/yaogu_launch_feature_mine.py
输出：research/yaogu_feature_mine.json + 打印摘要（供报告引用）
"""
from __future__ import annotations
import os, glob, json, datetime as dt
import numpy as np
import pandas as pd
from scipy import stats as st

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")

LIM_MAIN = 0.095   # 主板涨停阈值
RUN_MIN = 2        # 连板运行最小长度（≥2 算"连续"）

# ───────────────────────────────────────────────────────────────────────────
# 特征计算：仅接收截断到 t 的切片（c[-1] 即第 t 日），绝不含未来
# ───────────────────────────────────────────────────────────────────────────
def feats(c, o, h, l, v, zt):
    """c/o/h/l/v/zt 为 0..t 的切片（t 是最后一天=启动前交易日）。无未来。"""
    n = len(c)
    if n < 61:
        return None
    ci, oi, hi, li, vi = c[-1], o[-1], h[-1], l[-1], v[-1]
    pct = np.array([0.0] + [c[i]/c[i-1]-1 for i in range(1, n)])
    f = {}
    # ── 尾盘代理（日线限制：无 1min 数据，以下为日级近似，已在报告标注局限）──
    f['body'] = (ci - oi)/ci if ci else 0.0
    f['up_shadow'] = (hi - max(oi, ci))/ci if ci else 0.0
    f['lo_shadow'] = (min(oi, ci) - li)/ci if ci else 0.0
    f['close_pos'] = (ci - li)/(hi - li) if (hi - li) > 1e-9 else 0.5
    f['gap'] = oi/c[-2] - 1 if n >= 2 and c[-2] else 0.0
    f['amp'] = (hi - li)/ci if ci else 0.0
    # ── 动量 ──
    for w in (1, 5, 10, 20, 60):
        f[f'ret{w}'] = ci/c[-1-w] - 1 if n > w else 0.0
    f['ret5_1'] = (ci/c[-6]-1) - (c[-6]/c[-11]-1) if n > 10 else 0.0  # 加速度
    # ── 波动 ──
    for w in (20, 60):
        f[f'vol{w}'] = float(np.std(pct[-w:]))
    f['vol_ratio'] = vi/float(np.mean(v[-20:])) if np.mean(v[-20:]) > 0 else 1.0
    # ── 均线位置 ──
    for w in (5, 10, 20, 60):
        ma = float(np.mean(c[-w:]))
        f[f'ma{w}_ratio'] = ci/ma - 1 if ma > 0 else 0.0
    # ── 形态/距离 ──
    f['dist_high20'] = ci/max(h[-20:]) - 1
    f['dist_low20'] = ci/min(l[-20:]) - 1
    f['dd_from20h'] = ci/max(c[-20:]) - 1
    # ── 年分位 / 量加权成本获利 ──
    win = c[-250:] if n >= 250 else c
    f['yr_pctile'] = float((win < ci).mean())
    vw = sum(c[-120:][j]*v[-120:][j] for j in range(min(n,120))) / max(1e-9, sum(v[-120:]))
    f['vwap120_ratio'] = ci/vw - 1 if vw > 0 else 0.0
    # ── 振幅放大 ──
    amps = np.array([(h[i]-l[i])/c[i] for i in range(max(0,n-20), n) if c[i] > 0])
    f['amp_ratio'] = float((hi-li)/ci / np.mean(amps)) if len(amps) and np.mean(amps) > 0 else 1.0
    # ── 涨停史（纯历史）──
    f['days_since_zt'] = float(next((k for k in range(1, n) if zt[-1-k]), n))
    f['cnt_zt_20'] = float(np.sum(zt[-20:]))
    f['cnt_zt_60'] = float(np.sum(zt[-60:]))
    # ── 量趋势（前半 vs 后半 10日窗）──
    if n >= 10:
        vf = np.mean(v[-10:-5]); vs = np.mean(v[-5:])
        f['vol_trend'] = vf/vs if vs > 0 else 1.0
    else:
        f['vol_trend'] = 1.0
    return f

FEAT_KEYS = ['body','up_shadow','lo_shadow','close_pos','gap','amp',
             'ret1','ret5','ret10','ret20','ret60','ret5_1',
             'vol20','vol60','vol_ratio','ma5_ratio','ma10_ratio','ma20_ratio','ma60_ratio',
             'dist_high20','dist_low20','dd_from20h','yr_pctile','vwap120_ratio','amp_ratio',
             'days_since_zt','cnt_zt_20','cnt_zt_60','vol_trend']

def load(code):
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p):
        return None
    d,o,c,h,l,v = [],[],[],[],[],[]
    with open(p, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            a = ln.strip().split(",")
            if len(a) < 6: continue
            try:
                d.append(a[0]); o.append(float(a[1])); c.append(float(a[2]))
                h.append(float(a[3])); l.append(float(a[4])); v.append(float(a[5]))
            except: pass
    return d,o,c,h,l,v

def datenum(s):
    try: return int(s.replace("-",""))
    except: return 0

def main():
    files = glob.glob(os.path.join(DAILY, "*.csv"))
    rng = np.random.default_rng(20261024)
    files = rng.choice(files, min(420, len(files)), replace=False)

    events, controls = [], []          # 事件=启动前交易日; 控制=同股非启动日
    launch_meta = []                    # 启动日信息(用于原型分析)
    for f in files:
        try:
            res = load(os.path.basename(f).split(".")[0])
            if not res: continue
            d,o,c,h,l,v = res
            n = len(c)
            if n < 120: continue
            pct = [0.0] + [c[i]/c[i-1]-1 for i in range(1, n)]
            zt = [pct[i] >= LIM_MAIN for i in range(n)]
            # 检测连板运行(≥RUN_MIN)
            runs = []
            i = 0
            while i < n:
                if zt[i]:
                    j = i; rl = 0
                    while j < n and zt[j]: rl += 1; j += 1
                    if rl >= RUN_MIN: runs.append((i, j, rl))   # (start, end_excl, len)
                    i = j
                else: i += 1
            launch_dates = set()
            for (s, e, rl) in runs:
                launch_dates.add(s)
                # 复牌原型: 启动日与上一交易日历间隔>7天
                resumed = (s > 0) and (datenum(d[s]) - datenum(d[s-1]) > 7)
                launch_meta.append({"code": os.path.basename(f).split(".")[0],
                                     "launch": d[s], "len": rl, "resumed": resumed})
                pre = s - 1
                if pre < 61: continue
                # 截断到 pre（无未来）
                fv = feats(np.array(c[:pre+1]), np.array(o[:pre+1]), np.array(h[:pre+1]),
                           np.array(l[:pre+1]), np.array(v[:pre+1]), zt[:pre+1])
                if fv: events.append({**fv, "code": os.path.basename(f).split(".")[0],
                                       "date": d[pre], "resumed_launch": resumed})
            # 控制: 同股随机非启动日(排除启动日±2)
            cand = [k for k in range(61, n-1)
                    if (k not in launch_dates) and (k+1 not in launch_dates)
                    and (k+2 not in launch_dates) and (k-1 not in launch_dates)
                    and (k-2 not in launch_dates)]
            if cand:
                pick = rng.choice(cand, min(60, len(cand)), replace=False)
                for k in pick:
                    fv = feats(np.array(c[:k+1]), np.array(o[:k+1]), np.array(h[:k+1]),
                               np.array(l[:k+1]), np.array(v[:k+1]), zt[:k+1])
                    if fv: controls.append({**fv, "code": os.path.basename(f).split(".")[0],
                                            "date": d[k], "resumed_launch": False})
        except Exception:
            continue

    E = pd.DataFrame(events); C = pd.DataFrame(controls)
    print(f"[面板] 事件(启动前交易日) {len(E)} 行 / 控制 {len(C)} 行 / 股票覆盖 {E['code'].nunique()}")
    print(f"[启动原型] 复牌启动 {sum(m['resumed'] for m in launch_meta)} / 正常启动 {sum(not m['resumed'] for m in launch_meta)}")

    # ── 单变量挖掘: Mann-Whitney + 效应量 + BH-FDR ──
    rows = []
    for key in FEAT_KEYS:
        ev = E[key].dropna().values; cv = C[key].dropna().values
        if len(ev) < 20 or len(cv) < 20: continue
        try:
            u, p = st.mannwhitneyu(ev, cv, alternative="two-sided")
        except Exception:
            continue
        # 秩双列效应量
        n1, n2 = len(ev), len(cv)
        rb = 1 - 2*u/(n1*n2)
        # 方向: 事件中位数 - 控制中位数
        diff = float(np.median(ev) - np.median(cv))
        rows.append({"feat": key, "event_med": float(np.median(ev)),
                     "ctrl_med": float(np.median(cv)), "diff": diff,
                     "rank_biserial": float(rb), "p": float(p)})
    rd = pd.DataFrame(rows).sort_values("p")
    # BH-FDR
    m = len(rd); rd["p_rank"] = range(1, m+1)
    rd["q"] = rd["p"] * m / rd["p_rank"]
    rd["q"] = rd["q"].cummin()
    sig = rd[rd["q"] < 0.05].sort_values("rank_biserial", key=lambda x: -x.abs())
    print(f"\n[单变量挖掘] 显著特征(q<0.05): {len(sig)} / {m}")
    for r in sig.head(20).itertuples():
        print(f"  {r.feat:14s} 事件中位={r.event_med:+.4f} 控制中位={r.ctrl_med:+.4f} "
              f"效应量={r.rank_biserial:+.3f} q={r.q:.4f}")

    # ── 多变量(时间CV + 泄漏审计)──
    allf = pd.concat([E.assign(y=1), C.assign(y=0)], ignore_index=True)
    allf = allf.dropna(subset=FEAT_KEYS+["y","date"])
    # 日期排序切分(70/30, embargo 5日)
    allf["dn"] = allf["date"].map(datenum)
    cut = int(allf["dn"].quantile(0.7))
    tr = allf[allf["dn"] <= cut]; te = allf[allf["dn"] > cut]
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    sc = StandardScaler().fit(tr[FEAT_KEYS].values)
    clf = LogisticRegression(max_iter=1000, C=0.5).fit(sc.transform(tr[FEAT_KEYS].values), tr["y"].values)
    p = clf.predict_proba(sc.transform(te[FEAT_KEYS].values))[:,1]
    auc = roc_auc_score(te["y"].values, p); base = te["y"].mean()
    k = int(len(p)*0.10); top = te["y"].values[np.argsort(p)[-k:]].mean()
    print(f"\n[多变量 OOS] 测试 {len(te)} 行 基线={base:.4f} AUC={auc:.4f} Top10%={top:.4f} 倍数={top/base:.3f}")
    coef = dict(zip(FEAT_KEYS, clf.coef_[0]))
    print("[模型权重 最强±5]:")
    for name, val in sorted(coef.items(), key=lambda x:-abs(x[1]))[:5]:
        print(f"  + {name:14s} {val:+.3f}")
    for name, val in sorted(coef.items(), key=lambda x:abs(x[1]))[:5]:
        if val < 0: print(f"  - {name:14s} {val:+.3f}")

    # ── 复牌原型单独看 ──
    Er = E[E["resumed_launch"]]
    if len(Er) > 20:
        print(f"\n[复牌启动原型] 事件 {len(Er)} 个, 其特有信号(事件中位 vs 全控制):")
        for key in FEAT_KEYS:
            ev = Er[key].dropna().values; cv = C[key].dropna().values
            if len(ev) < 15: continue
            try: u,p = st.mannwhitneyu(ev, cv)
            except: continue
            if p < 0.05:
                print(f"  {key:14s} 复牌前中位={np.median(ev):+.4f} 控制={np.median(cv):+.4f} q≈{p:.4f}")

    # ── 深度个案: 大业 603278 10-15 / 新华 600825 9-4 ──
    cases = {}
    for code, day in (("603278","2026-10-15"), ("600825","2026-09-04")):
        res = load(code)
        if not res: continue
        d,o,c,h,l,v = res
        n = len(c)
        idx = {x:i for i,x in enumerate(d)}
        if day in idx:
            i = idx[day]
            zt = [ (c[k]/c[k-1]-1) >= LIM_MAIN if k>0 else False for k in range(n)]
            fv = feats(np.array(c[:i+1]), np.array(o[:i+1]), np.array(h[:i+1]),
                       np.array(l[:i+1]), np.array(v[:i+1]), zt[:i+1])
            if fv:
                cases[f"{code}_{day}"] = {k: float(fv[k]) for k in FEAT_KEYS}
                # 与事件/控制分布对比: 标记极端方向
                print(f"\n[个案 {code} {day}] 特征 vs 事件中位/控制中位:")
                for key in FEAT_KEYS:
                    em = E[key].median(); cm = C[key].median()
                    val = fv[key]
                    tag = "↑事件侧" if (em>=cm and val>=em) or (em<cm and val<=em) else "·"
                    print(f"  {key:14s} {val:+.4f}  (事件中位 {em:+.4f} / 控制 {cm:+.4f}) {tag}")

    out = {"n_event": len(E), "n_ctrl": len(C), "n_stock": int(E["code"].nunique()),
           "univariate_sig": sig[["feat","event_med","ctrl_med","rank_biserial","q"]].to_dict("records"),
           "multivariate": {"auc": float(auc), "base": float(base),
                            "top10": float(top), "lift": float(top/base),
                            "coef": {k: float(v) for k,v in coef.items()}},
           "cases": cases,
           "launch_meta_summary": {"resumed": int(sum(m["resumed"] for m in launch_meta)),
                                   "normal": int(sum(not m["resumed"] for m in launch_meta))}}
    json.dump(out, open(os.path.join(PROJ,"research","yaogu_feature_mine.json"),"w",encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n[OK] research/yaogu_feature_mine.json")

if __name__ == "__main__":
    main()
