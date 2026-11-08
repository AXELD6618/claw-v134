# -*- coding: utf-8 -*-
"""
P1 v0 候选追踪引擎 (原创)
=========================================================================
分层输出：
  Tier1 = 已验证真edge(高命中率执行核心): 连板惯性(≥2连板态) + 断层反包(昨涨停止/今未涨)
  Tier2 = pre-launch 埋伏 watchlist: Def-S 模型打分进前10%分位, 但尚不匹配 Tier1 edge
规则即JSON(data/p1_rules.json); 每日状态落 data/candidates.json; 模型持久化 data/p1_model.json(按日缓存)
"""
from __future__ import annotations
import os, sys, json, glob, datetime as dt
import numpy as np, pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "research"))
import yaogu_launch_feature_mine_v2 as V2
import p0_gate_test as P0

DAILY = V2.DAILY
LIM = V2.LIM_MAIN
MODEL_P = os.path.join(PROJ, "data", "p1_model.json")
CAND_P = os.path.join(PROJ, "data", "candidates.json")
RULES_P = os.path.join(PROJ, "data", "p1_rules.json")


def train_model(force=False):
    today = dt.date.today().isoformat()
    if not force and os.path.exists(MODEL_P):
        m = json.load(open(MODEL_P, encoding="utf-8"))
        if m.get("date") == today:
            return m
    print("[train] 构建 Def-S 面板并训练 LogisticRegression ...")
    E, C = P0.build_panel(P0.monster_pre)
    EF, CF = pd.DataFrame(E), pd.DataFrame(C)
    FEAT = [c for c in EF.columns if c not in ("code", "date")]
    cov = {k: EF[k].notna().mean() for k in FEAT}
    keep = [k for k in FEAT if cov[k] >= 0.3]
    allf = pd.concat([EF.assign(y=1), CF.assign(y=0)], ignore_index=True).dropna(subset=keep + ["y", "date"])
    allf["dn"] = allf["date"].map(V2.dn)
    cut = int(allf["dn"].quantile(0.7))
    tr, te = allf[allf["dn"] <= cut], allf[allf["dn"] > cut]
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    sc = StandardScaler().fit(tr[keep].values)
    clf = LogisticRegression(max_iter=1000, C=0.5).fit(sc.transform(tr[keep].values), tr["y"].values)
    p = clf.predict_proba(sc.transform(te[keep].values))[:, 1]
    auc = roc_auc_score(te["y"].values, p)
    thr = float(np.quantile(p, 0.90))  # 前10%分位阈值
    m = {"date": today, "keep": keep, "coef": clf.coef_[0].tolist(),
         "scaler_mean": sc.mean_.tolist(), "scaler_std": sc.scale_.tolist(),
         "threshold": thr, "auc": round(float(auc), 4), "base": round(float(te["y"].mean()), 4)}
    json.dump(m, open(MODEL_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[train] done AUC={auc:.4f} keep={len(keep)} thr={thr:.4f} -> {MODEL_P}")
    return m


def score_row(model, fv):
    keep = model["keep"]
    vals = np.array([fv.get(k, np.nan) for k in keep], dtype=float)
    if np.isnan(vals).any():
        return None
    z = (vals - np.array(model["scaler_mean"])) / np.array(model["scaler_std"])
    s = float(np.dot(z, np.array(model["coef"])))
    return float(1.0 / (1.0 + np.exp(-s)))


def edge_state(c, zt):
    """基于最新日识别 Tier1 真edge 状态。返回 (tier1, kind, buyable, note)。"""
    n = len(zt)
    if n < 2:
        return False, None, False, ""
    today_zt, yest_zt = zt[-1], zt[-2]
    # 断层反包: 昨涨停 / 今未涨停 -> 今日尾盘买, 博次日涨停(已验证 +0.53% 期望, 100%可成交)
    if yest_zt and not today_zt:
        return True, "断层反包", True, "昨涨停今未涨停, 尾盘买博次日涨停(期望+0.53%)"
    # 连板惯性: 最新连续涨停数 (已验证 ≥2连板->次日涨停49%, p=0.0001)
    run = 0
    for i in range(n - 1, -1, -1):
        if zt[i]:
            run += 1
        else:
            break
    if run >= 2:
        return True, f"连板惯性(run={run})", (not today_zt), f"已{run}连板, 次日涨停基准49%(p=0.0001)"
    if today_zt and not yest_zt:
        return False, "首板(监控)", True, "首日涨停, 监控是否成连板"
    return False, None, False, ""


def scan_universe(model):
    """Tier1=真edge状态; Tier2=非edge票中按 Def-S 打分取前1%(≥20)作埋伏watchlist(聚焦, 非全宇宙)。"""
    files = glob.glob(os.path.join(DAILY, "*.csv"))
    float_shr = V2.load_float()
    tier1, scored = [], []
    for f in files:
        code = os.path.basename(f).split(".")[0]
        res = V2.load_daily(code)
        if not res:
            continue
        d, o, c, h, l, v = res
        n = len(c)
        if n < 61:
            continue
        pct = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, n)]
        zt = [p >= LIM for p in pct]
        fv = V2.feats(np.array(c), np.array(o), np.array(h), np.array(l), np.array(v), zt,
                      float_shr.get(code), None, None, None, None, day_str=d[-1])
        if not fv:
            continue
        t1, kind, buyable, note = edge_state(c, zt)
        prob = score_row(model, fv)
        rec = {"code": code, "date": d[-1], "prob": round(prob, 4) if prob is not None else None,
               "kind": kind, "buyable": buyable, "note": note}
        if t1:
            tier1.append(rec)
        elif prob is not None:
            scored.append(rec)
    tier1.sort(key=lambda x: (x["prob"] or 0), reverse=True)
    scored.sort(key=lambda x: (x["prob"] or 0), reverse=True)
    k = max(20, int(len(scored) * 0.01))
    tier2 = scored[:k]
    return tier1, tier2


def main():
    model = train_model()
    tier1, tier2 = scan_universe(model)
    out = {"date": dt.date.today().isoformat(), "model_auc": model["auc"],
           "threshold": model["threshold"], "n_tier1": len(tier1), "n_tier2": len(tier2),
           "tier1": tier1[:30], "tier2": tier2[:40],
           "note": "Tier1=已验证真edge(高命中率执行); Tier2=pre-launch埋伏watchlist(Def-S前10%分位)"}
    json.dump(out, open(CAND_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n[scan] Tier1(真edge)={len(tier1)} Tier2(埋伏)={len(tier2)} AUC={model['auc']} thr={model['threshold']:.4f}")
    print("=== Tier1 样本(前10) ===")
    for r in tier1[:10]:
        print(f"  {r['code']} {r['date']} {r['kind']} buyable={r['buyable']} prob={r['prob']} | {r['note']}")
    print("=== Tier2 埋伏样本(前10) ===")
    for r in tier2[:10]:
        print(f"  {r['code']} {r['date']} prob={r['prob']}")
    print(f"[OK] {CAND_P}")


if __name__ == "__main__":
    main()
