#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日内择时 ML 自我审计 (主理人审查视角, 找出上一轮未点名的缺陷并量化).

审计项:
  D3 cross-stock Leave-One-Out 泛化 (留1票用其余训, 测未见票) -> 真泛化检验
  D4 剔除涨停封板样本后 pooled walk-forward 重算 -> 检验 AUC 是否只"学会封板"
  D6 分时段 AUC (上午/下午) -> 检验信号是否只靠"时段先验"
  (D1 实盘可执行性错配 / D2 单日不可靠 / D5 冷启动 在报告中定性, 不在本脚本跑)

依赖: p1_intraday_model (numpy-only).
"""
import os, sys, json, glob, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p1_intraday_model as m

INTRADAY = m.INTRADAY
H = m.H


def sec_of(dt):
    t = dt.split(" ")[1]
    h, mm, s = t.split(":")
    return int(h) * 3600 + int(mm) * 60 + int(s)


def read_one(csvp):
    code = os.path.basename(csvp).split("_")[0]
    rows = np.genfromtxt(csvp, delimiter=",", skip_header=1,
                         dtype=None, encoding="utf-8")
    dts = [r[0] for r in rows]
    O = np.array([float(r[1]) for r in rows])
    Hh = np.array([float(r[2]) for r in rows])
    L = np.array([float(r[3]) for r in rows])
    C = np.array([float(r[4]) for r in rows])
    V = np.array([float(r[5]) for r in rows])
    A = np.array([float(r[6]) for r in rows])
    n = len(C)
    sec = np.array([sec_of(d) for d in dts])
    ret1 = np.zeros(n); ret1[1:] = C[1:] / C[:-1] - 1
    feat = np.zeros((n, 9))
    for t in range(n):
        lo = max(0, t - 20)
        vw = V[lo:t + 1]; rw = ret1[lo:t + 1]
        vm = vw.mean() if len(vw) else 0.0
        rm = rw.mean() if len(rw) else 0.0
        rs = rw.std() if len(rw) > 1 else 0.0
        feat[t, 0] = ret1[t]
        feat[t, 1] = C[t] / C[t - 5] - 1 if t >= 5 else 0.0
        feat[t, 2] = rs
        feat[t, 3] = C[t] / C[t - 20] - 1 if t >= 20 else 0.0
        feat[t, 4] = (sec[t] - 34200) / (54000 - 34200)
        feat[t, 5] = math.log(V[t] + 1.0)
        feat[t, 6] = V[t] / (vm + 1e-9)
        feat[t, 7] = A[t] / (A[lo:t + 1].mean() + 1e-9) if t > lo else 1.0
        feat[t, 8] = (Hh[t] - L[t]) / (C[t] + 1e-9)
    y = np.zeros(n); yret = np.zeros(n)
    y[:n - H] = (C[H:n] > C[:n - H]).astype(float)
    yret[:n - H] = C[H:n] / C[:n - H] - 1.0
    mask = (np.arange(n) >= 20) & (np.arange(n) < n - H)
    amp = (Hh - L) / (C + 1e-9)
    seal = np.zeros(n, bool)
    seal |= np.isclose(amp, 0, atol=1e-5)                 # 一字板
    seal |= (np.isclose(C, Hh, atol=1e-6)) & (np.abs(ret1) < 2e-4)  # 收最高且不动
    is_am = sec < 41400
    return dict(code=code, X=feat[mask], y=y[mask], yret=yret[mask],
                seal=seal[mask], is_am=is_am[mask],
                dts=[dts[i] for i in range(n) if mask[i]])


def rolling_gate(pte, yte, yrete, ptr, MINH=30, COST=0.0015):
    tu0, td0 = float(np.quantile(ptr, 0.8)), float(np.quantile(ptr, 0.2))
    ei = []
    for i in range(len(pte)):
        if i >= MINH:
            tu, td = float(np.quantile(pte[:i], 0.8)), float(np.quantile(pte[:i], 0.2))
        else:
            tu, td = tu0, td0
        if pte[i] >= tu:
            ei.append(i)
    ei = np.array(ei)
    eh = float(yte[ei].mean()) if len(ei) else float("nan")
    ng = float(yrete[ei].mean() - COST) if len(ei) else float("nan")
    return eh, float(ng - yrete.mean() + COST), len(ei)


def wf(X, y, yret, dts, drop_tod=False):
    """时间前70%/后30% walk-forward. 返回 test AUC / 正类率 / ENTER命中 / 净值改善 / 分时段AUC."""
    order = np.argsort(dts)
    k = int(len(order) * 0.7)
    tr, te = order[:k], order[k:]
    Xtr, Xte = X[tr], X[te]
    if drop_tod:
        Xtr = Xtr.copy(); Xte = Xte.copy(); Xtr[:, 4] = 0.0; Xte[:, 4] = 0.0
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    W1, b1, W2, b2 = m.mlp_train((Xtr - mu) / sd, y[tr], seed=0)
    pte = m.mlp_pred((Xte - mu) / sd, W1, b1, W2, b2)
    ptr = m.mlp_pred((Xtr - mu) / sd, W1, b1, W2, b2)
    auc = m.auc_score(pte, y[te])
    eh, _, _ = rolling_gate(pte, y[te], yret[te], ptr)
    isam = np.array([dts[i] for i in te])
    # is_am 需要原始 bool, 这里 dts 是字符串, 用 sec 判断
    secs = np.array([sec_of(d) for d in isam])
    am = secs < 41400
    auc_am = m.auc_score(pte[am], y[te][am]) if am.sum() and (~am).sum() else float("nan")
    auc_pm = m.auc_score(pte[~am], y[te][~am]) if (~am).sum() and am.sum() else float("nan")
    return dict(auc=float(auc), pos=float(y[te].mean()), enter_hit=float(eh),
                auc_am=float(auc_am), auc_pm=float(auc_pm))


def main():
    csvs = sorted(glob.glob(os.path.join(INTRADAY, "*.csv")))
    csvs = [c for c in csvs if "_raw" not in os.path.basename(c)]
    per = [read_one(c) for c in csvs]
    codes = [p["code"] for p in per]
    day = per[0]["dts"][0][:8]
    print("=== 审计对象: %s (单日 %s, 共 %d 票) ===" % (codes, day, len(per)))

    # ---------- D3: cross-stock LOO ----------
    print("\n[D3] cross-stock Leave-One-Out 泛化 (留1票, 用其余训, 测未见票)")
    loo_auc, loo_eh, loo_auc_notod = [], [], []
    for i in range(len(per)):
        trX = np.vstack([per[j]["X"] for j in range(len(per)) if j != i])
        trY = np.concatenate([per[j]["y"] for j in range(len(per)) if j != i])
        te = per[i]
        mu, sd = trX.mean(0), trX.std(0) + 1e-9
        W1, b1, W2, b2 = m.mlp_train((trX - mu) / sd, trY, seed=0)
        pte = m.mlp_pred((te["X"] - mu) / sd, W1, b1, W2, b2)
        ptr = m.mlp_pred((trX - mu) / sd, W1, b1, W2, b2)
        auc = m.auc_score(pte, te["y"])
        eh, _, _ = rolling_gate(pte, te["y"], te["yret"], ptr)
        # 剔除 time-of-day 后 LOO AUC (检验是否时间泄漏)
        pte_nt = m.mlp_pred((te["X"] - mu) / sd, W1, b1, W2, b2)  # 同模型; 需重训去tod
        W1b, b1b, W2b, b2b = m.mlp_train((trX - mu) / sd, trY, seed=0)
        Xte_nt = te["X"].copy(); Xte_nt[:, 4] = 0.0
        pte_nt = m.mlp_pred((Xte_nt - mu) / sd, W1b, b1b, W2b, b2b)
        auc_nt = m.auc_score(pte_nt, te["y"])
        loo_auc.append(auc); loo_eh.append(eh); loo_auc_notod.append(auc_nt)
        print("  留 %s: AUC=%.3f (去tod=%.3f) ENTER命中=%.3f (基线0.165)" %
              (te["code"], auc, auc_nt, eh))
    loo_auc = np.array(loo_auc); loo_eh = np.array(loo_eh)
    print("  LOO AUC mean=%.3f  min=%.3f  (>0.5: %d/%d)" %
          (loo_auc.mean(), loo_auc.min(), int((loo_auc > 0.5).sum()), len(loo_auc)))
    print("  去tod后 LOO AUC mean=%.3f (若塌方=时间泄漏; 若仍>0.5=真微结构)" %
          np.array(loo_auc_notod).mean())

    # ---------- D4 + D6: pooled walk-forward (全样本 vs 剔除封板) ----------
    allX = np.vstack([p["X"] for p in per])
    allY = np.concatenate([p["y"] for p in per])
    allyret = np.concatenate([p["yret"] for p in per])
    allseal = np.concatenate([p["seal"] for p in per])
    alldts = []
    for p in per:
        alldts += p["dts"]
    print("\n[D4] 封板样本占比=%.1f%%" % (100 * allseal.mean()))
    r_full = wf(allX, allY, allyret, alldts)
    keep = ~allseal
    r_noseal = wf(allX[keep], allY[keep], allyret[keep],
                  [alldts[i] for i in range(len(alldts)) if keep[i]])
    print("  全样本   : AUC=%.3f  ENTER命中=%.3f  上午AUC=%.3f 下午AUC=%.3f" %
          (r_full["auc"], r_full["enter_hit"], r_full["auc_am"], r_full["auc_pm"]))
    print("  剔除封板 : AUC=%.3f  ENTER命中=%.3f  上午AUC=%.3f 下午AUC=%.3f" %
          (r_noseal["auc"], r_noseal["enter_hit"], r_noseal["auc_am"], r_noseal["auc_pm"]))
    print("  -> 剔除封板后 AUC %s (若仍>0.5 = 非仅'学会封板')" %
          ("保持" if r_noseal["auc"] > 0.5 else "塌陷"))

    # ---------- 综合诚实判定 ----------
    print("\n" + "=" * 56)
    print("综合审计结论 (诚实降级):")
    print("  D3 未见票泛化: LOO AUC mean=%.3f (>0.5 即信号跨票成立)" % loo_auc.mean())
    print("  D4 非封板假象: 剔除封板 AUC=%.3f" % r_noseal["auc"])
    print("  D6 时段独立: 上午AUC=%.3f 下午AUC=%.3f (两者>0.5=非仅时段先验)" %
          (r_full["auc_am"], r_full["auc_pm"]))
    print("=" * 56)
    out = {
        "audit_day": day, "stocks": codes, "n_pooled": int(len(allY)),
        "seal_ratio": round(float(allseal.mean()), 4),
        "loo_auc_mean": round(float(loo_auc.mean()), 4),
        "loo_auc_min": round(float(loo_auc.min()), 4),
        "loo_auc_notod_mean": round(float(np.array(loo_auc_notod).mean()), 4),
        "loo_enter_hit_mean": round(float(loo_eh.mean()), 4),
        "full_auc": round(r_full["auc"], 4),
        "full_auc_am": round(r_full["auc_am"], 4),
        "full_auc_pm": round(r_full["auc_pm"], 4),
        "noseal_auc": round(r_noseal["auc"], 4),
        "noseal_enter_hit": round(r_noseal["enter_hit"], 4),
        "verdict_note": "单日 cross-stock 泛化初步探测; 不足以判定实盘 edge (需>=20交易日 cross-day); H=5 纯日内标签在 T+1 市场不可直接实盘",
    }
    with open(os.path.join(m.ROOT, "reports", "intraday_audit.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[saved] reports/intraday_audit.json")


if __name__ == "__main__":
    main()
