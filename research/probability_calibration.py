# -*- coding: utf-8 -*-
"""
research/probability_calibration.py — 概率校准验证（让"贝叶斯概率"名副其实）

主打的"概率"只有当其数值可信时才有意义。AUC 只衡量排序，不保证概率校准。
本模块提供 Brier / 可靠性曲线 / ECE / MCE / 单调性，并可对预测文件出报告。
用法：
  python -m research.probability_calibration --selftest
  python -m research.probability_calibration --check preds.csv      # 列: y_true,pred
  python -m research.probability_calibration --check preds.json     # [{"y_true":1,"pred":0.7},...]
判据：Brier 越低越好；可靠性曲线应贴近对角线（|frac-c| 小）；ECE<0.05 且曲线单调=可信。
"""
from __future__ import annotations
import json
import numpy as np


def brier_score(y, p):
    y = np.asarray(y, float); p = np.asarray(p, float)
    return float(np.mean((p - y) ** 2))


def reliability_curve(y, p, n_bins=10, min_count=0):
    """可靠性曲线。min_count>0 时剔除样本数不足的分箱（避免单样本分箱制造假 MCE/假非单调）。"""
    y = np.asarray(y, float); p = np.asarray(p, float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    which = np.clip(np.digitize(p, edges) - 1, 0, n_bins - 1)
    centers, frac, cnt = [], [], []
    for b in range(n_bins):
        m = which == b
        c = int(m.sum())
        if c > 0 and c >= min_count:
            centers.append(float(p[m].mean()))
            frac.append(float(y[m].mean()))
            cnt.append(c)
    return np.array(centers), np.array(frac), np.array(cnt)


def expected_calibration_error(y, p, n_bins=10, min_count=0):
    c, f, n = reliability_curve(y, p, n_bins, min_count)
    tot = n.sum()
    if tot == 0:
        return 0.0
    return float(np.sum(n / tot * np.abs(f - c)))


def max_calibration_error(y, p, n_bins=10, min_count=0):
    c, f, n = reliability_curve(y, p, n_bins, min_count)
    if len(c) == 0:
        return 0.0
    return float(np.max(np.abs(f - c)))


def is_monotonic(y, p, n_bins=10, tol=1e-9, min_count=0):
    _, f, _ = reliability_curve(y, p, n_bins, min_count)
    if len(f) < 2:
        return True
    return bool(np.all(np.diff(f) >= -tol))


def report(y, p, n_bins=10, min_count=0):
    c, f, n = reliability_curve(y, p, n_bins, min_count)
    # 稳健口径：分箱样本>=30 才参与 MCE/单调性判定（统计学上单个样本的分箱无信息量）
    auto_min = max(30, int(0.001 * len(y)))
    c_r, f_r, n_r = reliability_curve(y, p, n_bins, auto_min)
    mce_robust = float(np.max(np.abs(f_r - c_r))) if len(c_r) else 0.0
    mono_robust = bool(np.all(np.diff(f_r) >= -1e-9)) if len(f_r) >= 2 else True
    return {
        "n": int(len(y)),
        "base_rate": float(np.mean(y)),
        "brier": brier_score(y, p),
        "ece": expected_calibration_error(y, p, n_bins, min_count),
        "mce": max_calibration_error(y, p, n_bins, min_count),
        "mce_robust": mce_robust,
        "mce_min_count": auto_min,
        "monotonic": is_monotonic(y, p, n_bins, min_count=min_count),
        "monotonic_robust": mono_robust,
        "bins_total": int(len(c)),
        "bins_robust": int(len(c_r)),
        "reliability": [{"pred_mean": round(float(a), 4), "frac_pos": round(float(b), 4), "count": int(k)}
                        for a, b, k in zip(c, f, n)],
    }


def _load(path):
    if path.endswith(".json"):
        d = json.load(open(path, encoding="utf-8"))
        y = [r["y_true"] for r in d]; p = [r["pred"] for r in d]
    else:
        import csv
        y, p = [], []
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                y.append(float(row["y_true"])); p.append(float(row["pred"]))
    return np.array(y), np.array(p)


def _selftest():
    rng = np.random.default_rng(7)
    n = 20000
    p = rng.uniform(0, 1, n)
    # 完美校准：y ~ Bernoulli(p)
    y_cal = (rng.uniform(0, 1, n) < p).astype(float)
    # 过度自信：把 p 挤压到两端
    p_over = np.clip((p - 0.5) * 1.8 + 0.5, 0.001, 0.999)
    y_over = (rng.uniform(0, 1, n) < p).astype(float)
    rep_cal = report(y_cal, p)
    rep_over = report(y_over, p_over)
    print("[selftest] 校准良好: brier=%.4f ece=%.4f mce_raw=%.4f mce_robust=%.4f mono_robust=%s"
          % (rep_cal["brier"], rep_cal["ece"], rep_cal["mce"], rep_cal["mce_robust"], rep_cal["monotonic_robust"]))
    print("[selftest] 过度自信: brier=%.4f ece=%.4f mce_raw=%.4f mce_robust=%.4f mono_robust=%s"
          % (rep_over["brier"], rep_over["ece"], rep_over["mce"], rep_over["mce_robust"], rep_over["monotonic_robust"]))
    assert rep_cal["ece"] < rep_over["ece"], "校准良好的 ECE 应更小"
    # 稳健口径：单样本分箱不应主导 MCE
    y_tiny = np.array([1.0, 0.0, 1.0, 0.0]); p_tiny = np.array([0.05, 0.3, 0.6, 0.9])
    r_tiny = report(np.concatenate([y_cal, y_tiny]), np.concatenate([p, p_tiny]))
    assert r_tiny["mce_robust"] <= r_tiny["mce"] + 1e-12, "稳健 MCE 不应大于原始 MCE"
    print("[selftest] PASS — 模块能区分校准好坏，且具备剔除小分箱的稳健口径")


# ============================================================================
# 校准层（2026-11-03 新增）：把 raw 概率映射为校准概率，可直接存模型 JSON 应用
# 目的：让 live 模型通过 ECE 部署闸门（≤0.05），而非改 AUC / 改圣杯目标。
# ============================================================================
def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1.0 - 1e-6)
    return np.log(p / (1.0 - p))


def fit_platt(y, p, max_iter=100, tol=1e-7):
    """Platt scaling：在 logit(p) 上拟合逻辑回归 A*logit(p)+B → 校准概率。
    对逻辑/线性模型最稳，小样本不抖动。返回 (A, B)。"""
    p = np.asarray(p, float); y = np.asarray(y, float)
    z = _logit(p)
    A, B = 1.0, 0.0
    for _ in range(max_iter):
        eta = A * z + B
        pr = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(pr * (1.0 - pr), 1e-12, None)
        gA = np.sum((y - pr) * z); gB = np.sum(y - pr)
        hA = -np.sum(w * z * z) - 1e-6; hB = -np.sum(w) - 1e-6
        dA = gA / hA; dB = gB / hB
        A += dA; B += dB
        if abs(dA) < tol and abs(dB) < tol:
            break
    return float(A), float(B)


def apply_platt(p, A, B):
    p = np.asarray(p, float)
    return 1.0 / (1.0 + np.exp(-(A * _logit(p) + B)))


def fit_isotonic(y, p):
    """Isotonic regression 校准（分段常数）；需 sklearn。返回阈值 knots。"""
    from sklearn.isotonic import IsotonicRegression
    ir = IsotonicRegression(out_of_bounds="clip", y_min=1e-6, y_max=1.0 - 1e-6)
    ir.fit(np.asarray(p, float), np.asarray(y, float))
    return {"x": [float(v) for v in ir.X_thresholds_],
            "y": [float(v) for v in ir.y_thresholds_]}


def apply_isotonic(p, x, y):
    p = np.asarray(p, float)
    idx = np.clip(np.searchsorted(x, p), 0, len(y) - 1)
    return np.asarray(y, float)[idx]


def apply_calibrator(p, cal_map):
    """对 raw 概率 p 施加校准层。cal_map 由 fit_calibrator 产出；空则返回原值。"""
    if not cal_map:
        return np.asarray(p, float)
    t = cal_map.get("type")
    if t == "platt":
        return apply_platt(p, cal_map["A"], cal_map["B"])
    if t == "isotonic":
        return apply_isotonic(p, cal_map["x"], cal_map["y"])
    return np.asarray(p, float)


def fit_calibrator(y_train, p_train, y_val, p_val, min_count=0):
    """在训练集拟合 Platt + Isotonic，按验证集 ECE 择优返回 cal_map + 度量。
    仅当择优后 ECE 明显优于未校准才启用（否则返回 {}，保持原样）。"""
    p_tr = np.asarray(p_train, float); y_tr = np.asarray(y_train, float)
    p_te = np.asarray(p_val, float); y_te = np.asarray(y_val, float)
    base = report(y_te, p_te, min_count=min_count)
    base_ece = base["ece"]
    cands = []
    try:
        A, B = fit_platt(y_tr, p_tr)
        pe = apply_platt(p_te, A, B)
        cands.append(("platt", {"type": "platt", "A": A, "B": B}, report(y_te, pe, min_count=min_count)))
    except Exception as e:
        print(f"[calib-fit] Platt 失败: {type(e).__name__}: {e}")
    try:
        iso = fit_isotonic(y_tr, p_tr)
        ie = apply_isotonic(p_te, iso["x"], iso["y"])
        cands.append(("isotonic", {"type": "isotonic", **iso}, report(y_te, ie, min_count=min_count)))
    except Exception as e:
        print(f"[calib-fit] Isotonic 失败(可能无 sklearn): {type(e).__name__}: {e}")
    best = None
    for name, cmap, r in cands:
        mce = r.get("mce_robust", r["mce"])
        print(f"[calib-fit] {name}: ECE={r['ece']:.5f} MCE={mce:.5f} "
              f"mono={r.get('monotonic_robust', r['monotonic'])}")
        if best is None or r["ece"] < best[2]["ece"]:
            best = (name, cmap, r, mce)
    if best is None or best[2]["ece"] >= base_ece - 1e-4:
        print(f"[calib-fit] 校准无改善（base ECE={base_ece:.5f}），不启用校准层")
        return {}
    name, cmap, r, mce = best
    cmap["val_ece"] = r["ece"]; cmap["val_mce"] = mce
    cmap["val_monotonic"] = bool(r.get("monotonic_robust", r["monotonic"]))
    print(f"[calib-fit] 选用 {name}：校准后 ECE={r['ece']:.5f}（base={base_ece:.5f}）")
    return cmap


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    elif "--check" in sys.argv:
        path = sys.argv[sys.argv.index("--check") + 1]
        y, p = _load(path)
        print(json.dumps(report(y, p), ensure_ascii=False, indent=2))
    else:
        print("usage: python -m research.probability_calibration --selftest | --check <file>")
