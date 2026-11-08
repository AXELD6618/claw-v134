# -*- coding: utf-8 -*-
"""
run_feature_eligibility_pit_st.py
==================================================================
用「真实 ST 剔除 + Point-in-Time(PIT) 正确口径」重跑特征资格矩阵，
验证旧矩阵 0/12 REJECT 是否稳健（排除 ST 污染 / 前视泄露造成的假阴性）。

方法学完全复刻 feature_eligibility_matrix.md：
  - Universe: demon_universe.jsonl 的 (code,date)；剔除名称含 ST/退 的 code。
  - Split: TRAIN = date <= 2025-06-30 ; OOS = date > 2025-06-30。
  - OOS 十分位分箱；high / low 两个方向都测，取 PF（最优十分位均值 ret_T5）更好的方向。
  - hit_mult = 最优十分位 CONT_T1 命中率 / 全样本基线 CONT_T1。
  - PF_t5_vs_random = 最优十分位均值 ret_T5 / 1.259（全市场随机 T+5 累计）。
  - PF_t5_vs_bottom = 最优十分位均值 ret_T5 / 反向极端十分位均值 ret_T5。
  - t_stat = (best - opposite 均值 r5) / 不等方差 SE。
  - sr_per_period = best 十分位 mean(r5)/std(r5)。
  - gate(): deflated-Sharpe 多重检验，promote iff hit>=1.15 AND PF>=1.30 AND t>=2.0 AND DSR>=0.95。

PIT 修正：
  - chip_scr: 用 cyq_engine.cyq_scr 在历史截断到 T 日的 daily 上逐点计算（替代旧的全局
    scr_latest 快照，消除前视）。
  - turnover/vol_ratio/above_ma20/ma20_slope: 本就是 as-of T 价格特征，直接重算（仅 ST 剔除）。
  - retail_ratio/holders_num_log: as-of T 股东户数（RPT_F10_EH_HOLDERNUM 历史季频，取
    NOTICE_DATE <= T 最近一季，lag≈1 季），PIT 安全，仅 ST 剔除后重算。
  - inst_org_ratio/inst_n_org/youzi_yl_net/youzi_lhb_cnt_5d/news_hit: 原源 smartmoney.context /
    龙虎榜快照 / intel_board 均为「当前快照」，无法在完整 demon 历史区间(~2024-07~2026-10)内
    可靠 PIT 化 -> 显式标 EXCLUDED（不伪造）。

铁律：不改 buy_score / FACTOR_FREEZE；缺失标 NA；禁伪造。
"""
from __future__ import annotations
import json, csv, glob, os, sys, bisect, gzip
import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
BC = os.path.join(PROJ, "backtest_cache")
DAILY = os.path.join(BC, "daily")
FLOAT_CSV = os.path.join(BC, "float_shares.csv")
UNIV_CSV = os.path.join(BC, "universe.csv")
DEMON = os.path.join(BC, "demon_universe.jsonl")
HOLDERNUM_DIR = os.path.join(BC, "intel", "holdernum")

SPLIT = "2025-06-30"
RANDOM_T5 = 1.259          # 全市场随机 T+5 累计收益（固定基准）
N_TRIALS = 33
FORCE_RELOAD = "--reload" in sys.argv
ROW_CACHE = os.path.join(BC, "_pit_st_rows.pkl")

sys.path.insert(0, os.path.join(PROJ, "research"))
from cyq_engine import cyq_scr                       # PIT 筹码集中度
from multiple_testing import adjudicate, white_reality_check

# ---------- 1. 载入 demon + ST 集合 ----------
demon = [json.loads(l) for l in open(DEMON, encoding="utf-8")]
st_codes = set()
with open(UNIV_CSV, encoding="utf-8") as f:
    for row in csv.DictReader(f):
        nm = (row.get("名称") or "").strip()
        if ("ST" in nm) or ("退" in nm):
            st_codes.add(row["代码"].strip())

n_before = len(demon)
demon_st = [r for r in demon if r["code"] in st_codes]
demon_nonst = [r for r in demon if r["code"] not in st_codes]
n_excluded = len(demon_st)
n_after = len(demon_nonst)
print(f"[ST] 剔除前 {n_before} -> 剔除 ST/退 {n_excluded} 行 -> 保留 {n_after} 行", flush=True)

# ---------- 2. float 股本 ----------
float_map = {}
with open(FLOAT_CSV, encoding="utf-8") as f:
    for row in csv.reader(f):
        if len(row) < 2: continue
        try: float_map[row[0].strip()] = float(row[1])
        except: pass

# ---------- 3. holdernum 历史季频 -> PIT 查表 ----------
holder = {}   # code -> list of (notice_str, ratio, num)
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
    notices = [x[0] for x in lst]
    i = bisect.bisect_right(notices, T_str) - 1
    if i < 0: return (np.nan, np.nan)
    return (lst[i][1], lst[i][2])

# ---------- 4. 逐 code 载入 daily，计算 as-of-T 特征 + PIT chip_scr ----------
recs = []
if (not FORCE_RELOAD) and os.path.exists(ROW_CACHE):
    import pickle
    recs = pickle.load(open(ROW_CACHE, "rb"))
    print(f"[cache] 载入已缓存样本行 {len(recs)}", flush=True)
else:
    by_code = {}
    for r in demon_nonst:
        by_code.setdefault(r["code"], []).append(r)

def load_daily(code):
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p): return None
    ds, cl, hi, lo, vo = [], [], [], [], []
    with open(p, encoding="utf-8") as f:
        rd = csv.reader(f); next(rd, None)
        for row in rd:
            if len(row) < 6: continue
            try:
                ds.append(row[0]); cl.append(float(row[2])); hi.append(float(row[3]))
                lo.append(float(row[4])); vo.append(float(row[5]))
            except: pass
    if len(cl) < 30: return None
    return dict(date=ds, close=np.array(cl), high=np.array(hi),
                low=np.array(lo), vol=np.array(vo))

codes_done = 0
for code, rows in by_code.items():
    d = load_daily(code)
    if d is None:
        codes_done += 1; continue
    close, high, low, vol = d["close"], d["high"], d["low"], d["vol"]
    dates = d["date"]; n = len(close)
    dmap = {dt: i for i, dt in enumerate(dates)}
    cs = np.cumsum(close)
    ma20 = np.full(n, np.nan)
    for i in range(n):
        if i >= 19:
            ma20[i] = (cs[i] - cs[i-20]) / 20.0
        else:
            ma20[i] = cs[i+1] / (i+1)
    vol5 = np.full(n, np.nan)
    for i in range(n):
        if i >= 5:
            vol5[i] = vol[i-5:i].mean()
    scr = cyq_scr(d, window=250)
    fs = float_map.get(code)
    for r in rows:
        T = r["date"]; idx = dmap.get(T)
        if idx is None or idx < 30 or idx + 5 >= n:
            continue
        lp = float(r.get("limit_pct") or 10.0) / 100.0
        r5 = close[idx+5] / close[idx] - 1.0
        cont_t1 = 1.0 if (close[idx+1] - close[idx]) / close[idx] >= (lp - 0.001) else 0.0
        turnover = (vol[idx] * 100.0 / fs * 100.0) if (fs and fs > 0) else np.nan
        vol_ratio = vol[idx] / vol5[idx] if (not np.isnan(vol5[idx]) and vol5[idx] > 0) else np.nan
        above_ma20 = 1.0 if (not np.isnan(ma20[idx]) and close[idx] > ma20[idx]) else np.nan
        slope = (ma20[idx] - ma20[idx-5]) / ma20[idx-5] * 100.0 if (idx >= 5 and not np.isnan(ma20[idx]) and not np.isnan(ma20[idx-5]) and ma20[idx-5] > 0) else np.nan
        cs_val = scr[idx] if (idx < len(scr) and scr[idx] is not None) else np.nan
        ratio, num = get_holder(code, T)
        retail_ratio = ratio if (not np.isnan(ratio)) else np.nan
        holders_num_log = np.log(num) if (not np.isnan(num) and num > 0) else np.nan
        recs.append(dict(
            code=code, date=T, split=("OOS" if T > SPLIT else "TRAIN"),
            ret_T5=r5, cont_t1=cont_t1,
            turnover=turnover, vol_ratio=vol_ratio, above_ma20=above_ma20,
            ma20_slope=slope, chip_scr=cs_val,
            retail_ratio=retail_ratio, holders_num_log=holders_num_log,
        ))
    codes_done += 1
    if codes_done % 200 == 0:
        print(f"  processed {codes_done}/{len(by_code)} codes, rows={len(recs)}", flush=True)

print(f"[load] 有效样本行: {len(recs)}", flush=True)
df = pd.DataFrame(recs)

# ---------- 5. 基线 CONT_T1 ----------
base_ct1 = float(df["cont_t1"].mean())
print(f"[baseline] CONT_T1 = {base_ct1:.4f}  (n={int(df['cont_t1'].notna().sum())})", flush=True)

# ---------- 6. 逐特征统计 ----------
FEATURES = ["turnover", "vol_ratio", "above_ma20", "ma20_slope", "chip_scr",
            "retail_ratio", "holders_num_log"]

def feat_stats(feat):
    sub = df[(df["split"] == "OOS") & df[feat].notna() & df["ret_T5"].notna()].copy()
    if len(sub) < 100:
        return None
    f = sub[feat].values.astype(float)
    r = sub["ret_T5"].values.astype(float)
    try:
        dec = pd.qcut(f, 10, labels=False, duplicates="drop")
    except Exception:
        dec = pd.cut(f, 10, labels=False)
    sub["dec"] = np.asarray(dec)
    g = sub.groupby("dec")
    gmean = g["ret_T5"].mean()
    gfeat = g[feat].mean()
    hi_dec = gfeat.idxmax(); mh = float(gmean.loc[hi_dec])
    lo_dec = gfeat.idxmin(); ml = float(gmean.loc[lo_dec])
    if mh >= ml:
        direction, best_dec = "high", hi_dec
    else:
        direction, best_dec = "low", lo_dec
    opp_dec = lo_dec if direction == "high" else hi_dec
    best = sub[sub["dec"] == best_dec]["ret_T5"]
    opp = sub[sub["dec"] == opp_dec]["ret_T5"]
    bm = float(best.mean()); om = float(opp.mean())
    best_std = float(best.std(ddof=1)) if len(best) > 1 else 0.0
    n_b, n_o = len(best), len(opp)
    vb = best.var(ddof=1) if n_b > 1 else 0.0
    vo = opp.var(ddof=1) if n_o > 1 else 0.0
    se = (vb / n_b + vo / n_o) ** 0.5
    t = (bm - om) / se if se > 0 else 0.0
    ctr = sub[sub["dec"] == best_dec]["cont_t1"]
    hit = float(ctr.mean()) / base_ct1 if (base_ct1 > 0 and ctr.notna().sum() > 0) else np.nan
    pf_rand = bm / RANDOM_T5
    pf_bot = bm / om if om != 0 else np.nan
    sr = bm / best_std if best_std > 0 else 0.0
    lb, lo2 = list(best.values), list(opp.values)
    L = min(len(lb), len(lo2))
    Rmat = np.array([lb[:L], lo2[:L]]).T
    try:
        wrc = white_reality_check(Rmat, benchmark=0.0, n_boot=300, block=5, seed=42)
        white_p = float(wrc["p_value"])
    except Exception:
        white_p = 1.0
    gres = adjudicate(n_trials=N_TRIALS, n_obs=n_b,
                     hit_multiple=(hit if not np.isnan(hit) else 0.0),
                     pf=pf_rand, t_stat=t, sr_per_period=sr, dsr_min=0.95)
    hi_hit = float(sub[sub["dec"] == hi_dec]["cont_t1"].mean()) / base_ct1 if base_ct1 > 0 else np.nan
    lo_hit = float(sub[sub["dec"] == lo_dec]["cont_t1"].mean()) / base_ct1 if base_ct1 > 0 else np.nan
    return dict(feature=feat, direction=direction, coverage_n=int(len(sub)),
                hit_multiple=round(hit, 4), pf_t5_vs_bottom=round(pf_bot, 4),
                pf_t5_vs_random=round(pf_rand, 4), t_stat=round(t, 3),
                dsr=round(gres["dsr"], 4), white_rc=round(white_p, 4),
                promote=bool(gres["promote"]),
                n_best=n_b, n_opp=n_o, sr=round(sr, 4),
                hi_pf=round(mh, 4), hi_hit=round(hi_hit, 4),
                lo_pf=round(ml, 4), lo_hit=round(lo_hit, 4),
                reasons=gres["reasons"], best_mean_r5=round(bm, 4),
                opp_mean_r5=round(om, 4))

results = []
for feat in FEATURES:
    s = feat_stats(feat)
    if s is None:
        print(f"  !! {feat} 样本不足，跳过", flush=True); continue
    results.append(s)
    print(f"  {feat:16s} dir={s['direction']:5s} hit={s['hit_multiple']:.3f} "
          f"pfR={s['pf_t5_vs_random']:.3f} t={s['t_stat']:.2f} dsr={s['dsr']:.3f} "
          f"promote={s['promote']}", flush=True)

EXCLUDED = {
    "inst_org_ratio":    "smartmoney.context 机构占比（当前快照，非 as-of T）；无法在完整 demon 历史(~2024-07~2026-10)内 PIT 化",
    "inst_n_org":        "smartmoney.context 机构家数（当前快照，非 as-of T）；无法 PIT 化",
    "youzi_yl_net":      "smartmoney.context / 龙虎榜游资净买（当前快照，且 LHB 历史仅 ~2026-09~10 局部窗口）；无法全样本 PIT 化",
    "youzi_lhb_cnt_5d":  "龙虎榜上榜次数（快照口径，LHB 历史覆盖不足）；无法 PIT 化",
    "news_hit":          "intel_board 当前新闻命中快照（非 as-of T）；无法 PIT 化",
}

def row_to_out(s):
    return dict(feature=s["feature"], direction=s["direction"], coverage_n=s["coverage_n"],
                hit_mult=s["hit_multiple"], PF_t5_vs_random=s["pf_t5_vs_random"],
                t=s["t_stat"], DSR=s["dsr"], WhiteRC=s["white_rc"], promote=s["promote"],
                pit_safe="yes", st_excluded=n_excluded,
                note=(f"PIT-ST重算 | dir={s['direction']} best_mean_r5={s['best_mean_r5']} "
                      f"opp_mean_r5={s['opp_mean_r5']} sr={s['sr']} n_best={s['n_best']} | "
                      f"high: pf={s['hi_pf']} hit={s['hi_hit']} ; low: pf={s['lo_pf']} hit={s['lo_hit']} | "
                      f"DSR reasons={' '.join(s['reasons'])}"))

out_features = [row_to_out(s) for s in results]
for feat, why in EXCLUDED.items():
    out_features.append(dict(feature=feat, direction="n/a", coverage_n=0,
        hit_mult=None, PF_t5_vs_random=None, t=None, DSR=None, WhiteRC=None, promote=False,
        pit_safe="no", st_excluded=n_excluded,
        note="EXCLUDED (non-PIT): " + why))

promoted = [o["feature"] for o in out_features if o["promote"]]
meta = dict(
    split=SPLIT, random_t5=RANDOM_T5, n_trials=N_TRIALS,
    baseline_cont_t1=round(base_ct1, 6), baseline_cont_t1_n=int(df["cont_t1"].notna().sum()),
    demon_n_before=n_before, st_excluded_n=n_excluded, demon_n_after=n_after,
    oos_rows=int((df["split"] == "OOS").sum()),
    method=("single chronological TRAIN/OOS split; deciles on OOS; both directions tested; "
            "gate=deflated-Sharpe>=0.95 with hit>=1.15/pf>=1.30/t>=2.0; "
            "ST excluded by name(ST/退); chip_scr PIT via cyq_engine on T-truncated history; "
            "as-of-T price/holdernum recomputed on ST-excluded sample; "
            "5 snapshot features marked EXCLUDED (not PIT-able)"),
    promoted=len(promoted),
)
out = dict(meta=meta, features=out_features)
with open(os.path.join(BC, "feature_eligibility_matrix_pit_st.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"\n[RESULT] promote = {len(promoted)} / 12  ({promoted or 'NONE'})", flush=True)
print(f"[RESULT] ST excluded = {n_excluded}; baseline CONT_T1 = {base_ct1:.4f}", flush=True)
json.dump(dict(meta=meta, results=results, excluded=EXCLUDED, promoted=promoted),
          open(os.path.join(BC, "_pit_st_intermediate.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
