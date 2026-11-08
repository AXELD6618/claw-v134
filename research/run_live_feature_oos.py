# -*- coding: utf-8 -*-
"""
run_live_feature_oos.py
==================================================================
在「live 累积样本」(live_samples.jsonl, 标签 y_gap = 次日高开) 上做特征化实证：
把 12 项硬契约字段的可计算子集（7 个 PIT-clean 微观特征 + 9 个已在 live 模型里的
主力资金类控制特征）过同一道 factor_freeze_gate，与历史 PIT+ST 矩阵(0/7) 交叉验证。

目标口径：live_gapup —— T 日尾盘买，T+1 高开(gap>0)即正例。
  - 连续收益 = gap = next_open/close_T - 1（买 T 收、卖 T+1 开）
  - 命中基线 = 全样本 mean(y_gap)  （live ≈ 0.177）
  - hit_mult  = 最优十分位 gap_up 率 / 基线
  - PF_vs_bottom = 最优十分位 mean(gap) / 反向极端十分位 mean(gap)
  - PF_vs_random = 最优十分位 mean(gap) / 全样本 mean(gap)
  - t = Welch(best, opp on gap)；sr = best_mean/std(best)
  - gate(): deflated-Sharpe, promote iff hit>=1.15 AND PF_vs_bottom>=1.30 AND t>=2.0 AND DSR>=0.95
  - 经济护栏(同历史): promote = gate AND (PF_vs_random>1.0) AND (|opp_mean_gap|>0.003)

PIT 修正（与历史矩阵完全一致）：
  - chip_scr: cyq_engine.cyq_scr 在 daily 截断到 T 后逐点算（position=idx，前视消除）
  - turnover/vol_ratio/above_ma20/ma20_slope: as-of T 价格特征重算
  - retail_ratio/holders_num_log: get_holder(code,T)，取 HOLD_NOTICE_DATE <= T 最近一季
  - 机构/游资/舆情(smartmoney/intel_board) 当前快照 → 标 EXCLUDED（非 PIT，需 intel_hub 回填）

铁律：不改 buy_score / FACTOR_FREEZE；缺失标 NA；禁伪造。mining_frozen=True。

样本体量警告：live 仅 175 标注 / 31 正例 / 4 个交易日 → 非有效时序 OOS，结论仅供方向性参考，
不可部署；须继续 live_capture_round 累积至 retrain_live 闸门(n>=80 且正例>=25)。
"""
from __future__ import annotations
import json, csv, os, sys, bisect, gzip, glob
import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
BC = os.path.join(PROJ, "backtest_cache")
DAILY = os.path.join(BC, "daily")
FLOAT_CSV = os.path.join(BC, "float_shares.csv")
HOLDERNUM_DIR = os.path.join(BC, "intel", "holdernum")
LIVE = os.path.join(BC, "live_samples.jsonl")
N_TRIALS = 33
DSR_MIN = 0.95
RANDOM_T5 = 1.259   # 历史随机 T+5 基准，仅作 T+5 交叉检验

sys.path.insert(0, os.path.join(PROJ, "research"))
from cyq_engine import cyq_scr
from multiple_testing import adjudicate, white_reality_check

# ---------- 载入 live 样本 ----------
rows = [json.loads(l) for l in open(LIVE, encoding="utf-8") if l.strip()]
lab = [r for r in rows if ("y_gap" in r) and (r.get("y_gap") is not None)]
print(f"[live] 总行 {len(rows)} / 标注 {len(lab)} / 正例 {sum(1 for r in lab if r['y_gap']==1)}", flush=True)

# ---------- float 股本 ----------
float_map = {}
with open(FLOAT_CSV, encoding="utf-8") as f:
    for row in csv.reader(f):
        if len(row) < 2: continue
        try: float_map[row[0].strip()] = float(row[1])
        except: pass

# ---------- holdernum PIT 查表 ----------
holder = {}
for fp in sorted(glob.glob(os.path.join(HOLDERNUM_DIR, "*.csv.gz"))):
    df = pd.read_csv(gzip.open(fp, "rt", encoding="utf-8"), dtype={"code": str})
    for _, r in df.iterrows():
        c = str(r.get("code") or "").strip()
        if not c or c == "nan": continue
        nd = str(r.get("HOLD_NOTICE_DATE") or "")[:10]
        if len(nd) != 10: continue
        try:
            ratio = float(r.get("HOLDER_NUM_RATIO")) if pd.notna(r.get("HOLDER_NUM_RATIO")) else np.nan
            num = float(r.get("HOLDER_NUM")) if pd.notna(r.get("HOLDER_NUM")) else np.nan
        except Exception:
            continue
        holder.setdefault(c, []).append((nd, ratio, num))
for c in holder:
    holder[c].sort(key=lambda x: x[0])

def get_holder(code, T_str):
    lst = holder.get(code)
    if not lst: return (np.nan, np.nan)
    i = bisect.bisect_right([x[0] for x in lst], T_str) - 1
    if i < 0: return (np.nan, np.nan)
    return (lst[i][1], lst[i][2])

# ---------- daily 缓存 ----------
_daily_cache = {}
def load_daily(code):
    if code in _daily_cache: return _daily_cache[code]
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p):
        _daily_cache[code] = None; return None
    ds, cl, hi, lo, vo = [], [], [], [], []
    with open(p, encoding="utf-8") as f:
        rd = csv.reader(f); next(rd, None)
        for row in rd:
            if len(row) < 6: continue
            try:
                ds.append(row[0]); cl.append(float(row[2])); hi.append(float(row[3]))
                lo.append(float(row[4])); vo.append(float(row[5]))
            except: pass
    if len(cl) < 30:
        _daily_cache[code] = None; return None
    d = dict(date=ds, close=np.array(cl), high=np.array(hi), low=np.array(lo), vol=np.array(vo))
    _daily_cache[code] = d; return d

# ---------- 逐样本构造特征 ----------
recs = []
for r in lab:
    code = str(r["code"]); T = r["date"]
    gap = float(r.get("gap") or 0.0); ygap = int(r["y_gap"])
    d = load_daily(code)
    feat = dict(code=code, date=T, gap=gap, y_gap=ygap)
    # 控制特征（已在 live 模型）
    for k in ["main_net_pct","ddx","main_net_3d","main_net_5d","main_net_10d","hy_zf","fengdan_amt","hy_zt_count","sentiment"]:
        v = r.get(k)
        feat[k] = float(v) if (v is not None and not (isinstance(v,float) and np.isnan(v))) else np.nan
    # PIT 微观特征
    for k in ["turnover","vol_ratio","above_ma20","ma20_slope","chip_scr","retail_ratio","holders_num_log","ret_T5"]:
        feat[k] = np.nan
    if d is not None:
        close, high, low, vol = d["close"], d["high"], d["low"], d["vol"]
        dates = d["date"]; n = len(close); dmap = {dt:i for i,dt in enumerate(dates)}
        idx = dmap.get(T)
        if idx is not None and idx >= 30 and idx + 1 < n:
            cs = np.cumsum(close); ma20 = np.full(n, np.nan)
            for i in range(n):
                ma20[i] = (cs[i]-cs[max(0,i-20)+1])/(i-max(0,i-20)) if i>=19 else cs[i+1]/(i+1)
            vol5 = np.full(n, np.nan)
            for i in range(n):
                if i >= 5: vol5[i] = vol[i-5:i].mean()
            fs = float_map.get(code)
            feat["turnover"] = (vol[idx]*100.0/fs*100.0) if (fs and fs>0) else np.nan
            feat["vol_ratio"] = vol[idx]/vol5[idx] if (not np.isnan(vol5[idx]) and vol5[idx]>0) else np.nan
            feat["above_ma20"] = (1.0 if (not np.isnan(ma20[idx]) and close[idx]>ma20[idx])
                                   else (0.0 if not np.isnan(ma20[idx]) else np.nan))
            if idx>=5 and not np.isnan(ma20[idx]) and not np.isnan(ma20[idx-5]) and ma20[idx-5]>0:
                feat["ma20_slope"] = (ma20[idx]-ma20[idx-5])/ma20[idx-5]*100.0
            scr = cyq_scr(d, window=250)
            if idx < len(scr) and scr[idx] is not None:
                feat["chip_scr"] = float(scr[idx])
            if idx + 5 < n:
                feat["ret_T5"] = close[idx+5]/close[idx]-1.0
        ratio, num = get_holder(code, T)
        feat["retail_ratio"] = ratio if not np.isnan(ratio) else np.nan
        feat["holders_num_log"] = np.log(num) if (not np.isnan(num) and num>0) else np.nan
    recs.append(feat)

df = pd.DataFrame(recs)
print(f"[build] 有效特征行: {len(df)}", flush=True)

base_gapup = float(df["y_gap"].mean())
mean_all_gap = float(df["gap"].mean())
print(f"[baseline] gap_up 率 = {base_gapup:.4f}  (n={int(df['y_gap'].notna().sum())}) ; mean_gap = {mean_all_gap:.5f}", flush=True)

MICRO = ["turnover","vol_ratio","above_ma20","ma20_slope","chip_scr","retail_ratio","holders_num_log"]
CONTROL = ["main_net_pct","ddx","main_net_3d","main_net_5d","main_net_10d","hy_zf","fengdan_amt","hy_zt_count","sentiment"]

def feat_stats(feat, ret_col="ret_T5"):
    """ret_col: 'ret_T5'（历史可比，随机基准 1.259）或 'gap'（策略相关，基线=全样本 mean_gap）。
    hit 始终用 y_gap 高开率。"""
    sub = df[df[feat].notna() & df[ret_col].notna()].copy()
    if len(sub) < 30:
        return None
    f = sub[feat].values.astype(float)
    ret = sub[ret_col].values.astype(float)
    uniq = np.unique(f[~np.isnan(f)])
    if len(uniq) <= 2:
        # 低基数（如 above_ma20 二值）：直接按特征值分组，对比两组
        sub["dec"] = np.where(np.isnan(f), -1, f)
        g = sub.groupby("dec")
        gmean = g[ret_col].mean(); gfeat = g[feat].mean()
        valid = [d for d in gmean.index if d >= 0]
        if len(valid) < 2:
            return None
        hi_dec = max(valid, key=lambda d: gmean.loc[d]); lo_dec = min(valid, key=lambda d: gmean.loc[d])
        mh, ml = float(gmean.loc[hi_dec]), float(gmean.loc[lo_dec])
        direction, best_dec = ("high", hi_dec) if mh >= ml else ("low", lo_dec)
        opp_dec = lo_dec if direction == "high" else hi_dec
        best = sub[sub["dec"]==best_dec][ret_col]; opp = sub[sub["dec"]==opp_dec][ret_col]
    else:
        try:
            dec = pd.qcut(f, 10, labels=False, duplicates="drop")
        except Exception:
            dec = pd.cut(f, 10, labels=False)
        sub["dec"] = np.asarray(dec)
        g = sub.groupby("dec")
        gmean = g[ret_col].mean(); gfeat = g[feat].mean()
        if len(gmean) < 2:
            return None
        hi_dec = gfeat.idxmax(); lo_dec = gfeat.idxmin()
        mh, ml = float(gmean.loc[hi_dec]), float(gmean.loc[lo_dec])
        direction, best_dec = ("high", hi_dec) if mh >= ml else ("low", lo_dec)
        opp_dec = lo_dec if direction == "high" else hi_dec
        best = sub[sub["dec"]==best_dec][ret_col]; opp = sub[sub["dec"]==opp_dec][ret_col]
    bm = float(best.mean()); om = float(opp.mean())
    best_std = float(best.std(ddof=1)) if len(best)>1 else 0.0
    n_b, n_o = len(best), len(opp)
    se = ((best.var(ddof=1)/n_b if n_b>1 else 0)+(opp.var(ddof=1)/n_o if n_o>1 else 0))**0.5
    t = (bm-om)/se if se>0 else 0.0
    ctr = sub[sub["dec"]==best_dec]["y_gap"]
    hit = float(ctr.mean())/base_gapup if (base_gapup>0 and ctr.notna().sum()>0) else np.nan
    pf_bot = bm/om if om!=0 else np.nan
    rand_bench = RANDOM_T5 if ret_col=="ret_T5" else mean_all_gap
    pf_rand = bm/rand_bench if (rand_bench not in (0,0.0,None) and not np.isnan(rand_bench)) else np.nan
    sr = bm/best_std if best_std>0 else 0.0
    lb, lo2 = list(best.values), list(opp.values); L = min(len(lb),len(lo2))
    Rmat = np.array([lb[:L], lo2[:L]]).T
    try:
        wrc = white_reality_check(Rmat, benchmark=0.0, n_boot=300, block=5, seed=42)
        white_p = float(wrc["p_value"])
    except Exception:
        white_p = 1.0
    gres = adjudicate(n_trials=N_TRIALS, n_obs=n_b,
                      hit_multiple=(hit if not np.isnan(hit) else 0.0),
                      pf=(pf_bot if not np.isnan(pf_bot) else 0.0),
                      t_stat=t, sr_per_period=sr, dsr_min=DSR_MIN)
    # 经济护栏（同历史）：pf_rand>1.0 且 |opp_mean|>0.003（非退化分母）
    econ_valid = (not np.isnan(pf_rand)) and (pf_rand>1.0) and (abs(om)>0.003)
    promote = bool(gres["promote"]) and bool(econ_valid)
    return dict(feature=feat, direction=direction, coverage_n=int(len(sub)),
                hit_mult=round(hit,4), pf_vs_bottom=round(pf_bot,4) if not np.isnan(pf_bot) else None,
                pf_vs_random=round(pf_rand,4) if not np.isnan(pf_rand) else None,
                t_stat=round(t,3), dsr=round(gres["dsr"],4), white_rc=round(white_p,4),
                gate_promote=bool(gres["promote"]), econ_valid=bool(econ_valid),
                promote=promote, n_best=n_b, n_opp=n_o, sr=round(sr,4),
                best_mean_ret=round(bm,5), opp_mean_ret=round(om,5),
                base_gapup=round(base_gapup,4), ret_col=ret_col,
                reasons=gres["reasons"] + (["econ:pf_rand<=1.0"] if (not np.isnan(pf_rand) and pf_rand<=1.0) else []))

results = []
for feat in MICRO + CONTROL:
    s = feat_stats(feat, ret_col="ret_T5")
    if s is None:
        print(f"  !! {feat} ret_T5 样本不足(<30)，尝试 gap 口径", flush=True)
        s = feat_stats(feat, ret_col="gap")
        if s is None:
            print(f"  !! {feat} 样本不足(<30)，跳过", flush=True); continue
    results.append(s)
    tag = "MICRO " if feat in MICRO else "CTRL  "
    print(f"  {tag}{feat:16s} ret={s['ret_col']:6s} dir={s['direction']:5s} hit={s['hit_mult']:.3f} "
          f"pfB={s['pf_vs_bottom']} pfR={s['pf_vs_random']} t={s['t_stat']:.2f} "
          f"dsr={s['dsr']:.3f} gate={s['gate_promote']} econ={s['econ_valid']} -> {s['promote']}", flush=True)

# gap 口径辅助（策略相关：买 T 收卖 T+1 开，收益=gap）
print("\n[gap 口径辅助 — 策略相关，基线=全样本 mean_gap]", flush=True)
for feat in MICRO:
    s = feat_stats(feat, ret_col="gap")
    if s is None:
        print(f"  !! {feat} gap 样本不足，跳过", flush=True); continue
    print(f"  {feat:16s} dir={s['direction']:5s} hit={s['hit_mult']:.3f} "
          f"best_mean_gap={s['best_mean_ret']:.4f} opp_mean_gap={s['opp_mean_ret']:.4f} "
          f"pfB={s['pf_vs_bottom']} t={s['t_stat']:.2f} dsr={s['dsr']:.3f}", flush=True)

EXCLUDED = {
    "inst_org_ratio":    "smartmoney.context 机构占比（当前快照，非 as-of T）；live 样本未含，需 intel_hub 回填且 PIT 化",
    "inst_n_org":        "smartmoney.context 机构家数（当前快照，非 as-of T）；无法 PIT 化",
    "youzi_yl_net":      "smartmoney.context / 龙虎榜游资净买（当前快照）；live 样本未含",
    "youzi_lhb_cnt_5d":  "龙虎榜上榜次数（快照口径，LHB 历史覆盖不足）；无法 PIT 化",
    "news_hit":          "intel_board 当前新闻命中快照（非 as-of T）；无法 PIT 化",
}

promoted = [r["feature"] for r in results if r["promote"]]
meta = dict(
    source="live_samples.jsonl (live_gapup label y_gap)",
    n_labeled=int(len(lab)), n_positive=int(sum(1 for r in lab if r["y_gap"]==1)),
    dates=sorted(set(r["date"] for r in lab)),
    baseline_gapup=round(base_gapup,6), mean_all_gap=round(mean_all_gap,6),
    random_t5=RANDOM_T5, n_trials=N_TRIALS, dsr_min=DSR_MIN,
    method=("live 标注样本直接评估（非时序 OOS split）；10 十分位，两方向测，取 PF_vs_bottom 更优方向；"
            "gate=deflated-Sharpe>=0.95 且 hit>=1.15 且 PF_vs_bottom>=1.30 且 t>=2.0；"
            "最终 promote=gate 且经济护栏(PF_vs_random>1.0 且 |opp_mean_ret|>0.003)；"
            "chip_scr PIT 截断 daily@T；holdernum 取 HOLD_NOTICE_DATE<=T 最近季；"
            "5 snapshot 特征 EXCLUDED(非 PIT)；mining_frozen=True"),
    small_sample_warning=("live 仅 175 标注/31 正例/4 交易日，n_obs(最优十分位~17) 远小于 DSR 门控需求，"
                          "DSR≈0，gate 必拒；结论方向性参考，不可部署；须继续 live_capture_round 累积至 "
                          "retrain_live 闸门(n>=80 且正例>=25)"),
    promoted=len(promoted),
)
out_features = []
for r in results:
    grp = "micro_pit" if r["feature"] in MICRO else "control_mainforce"
    out_features.append(dict(group=grp, feature=r["feature"], direction=r["direction"],
        coverage_n=r["coverage_n"], hit_mult=r["hit_mult"], pf_vs_bottom=r["pf_vs_bottom"],
        pf_vs_random=r["pf_vs_random"], t=r["t_stat"], DSR=r["dsr"], WhiteRC=r["white_rc"],
        gate_promote=r["gate_promote"], econ_valid=r["econ_valid"], promote=r["promote"],
        n_best=r["n_best"], n_opp=r["n_opp"], sr=r["sr"], best_mean_ret=r["best_mean_ret"],
        opp_mean_ret=r["opp_mean_ret"], reasons=r["reasons"]))
for feat, why in EXCLUDED.items():
    out_features.append(dict(group="excluded_non_pit", feature=feat, direction="n/a",
        coverage_n=0, hit_mult=None, pf_vs_bottom=None, pf_vs_random=None, t=None, DSR=None,
        WhiteRC=None, gate_promote=False, econ_valid=False, promote=False,
        n_best=0, n_opp=0, sr=None, best_mean_ret=None, opp_mean_ret=None,
        reasons=["EXCLUDED(non-PIT)"], note=why))
out = dict(meta=meta, features=out_features)
with open(os.path.join(BC, "live_feature_eligibility.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"\n[RESULT] live promote = {len(promoted)} / 12  ({promoted or 'NONE'})", flush=True)
print(f"[RESULT] 已评估 {len(results)} 特征（7 micro + {len(results)-7} control），5 EXCLUDED", flush=True)
