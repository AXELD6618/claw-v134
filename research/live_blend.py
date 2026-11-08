# -*- coding: utf-8 -*-
"""
research/live_blend.py — 先验 × 真实样本 的 log-odds 收缩合成
================================================================

为什么用 log-odds 而不是特征空间拼接：
  先验（可回溯日线代理）与真实 Live 样本（资金流/舆情/事件）**特征空间不一致**，
  直接在特征层合并会造出一个两边都不准的怪物。
  在**概率空间**做 log-odds 加权最干净，且天然是贝叶斯更新形式：

      z_final = (1 - w) * logit(p_prior) + w * logit(p_live)
      p_final = sigmoid(z_final)
      w       = n_live / (n_live + K)          # K = 先验等效样本量（默认 80）

  n_live = 0   → w=0，完全用先验（系统不空转）
  n_live = 80  → w=0.5，先验与实证各半
  n_live = 240 → w=0.75，实证主导
  n_live → ∞   → w→1，先验退场

这不是"拍脑袋混合"，是带明确等效样本量的收缩估计（shrinkage），
K 就是"先验值多少条真实样本"的先验信念。K=80 与 retrain_live.MIN_SAMPLES 对齐。

run:
  .venv/Scripts/python.exe -m research.live_blend --selftest
  .venv/Scripts/python.exe -m research.live_blend --score '{"ret5":0.03,...}'  # 用先验打分
"""
from __future__ import annotations
import os, sys, json, math, argparse
import numpy as np

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIOR_PATH = os.path.join(PROJ, "backtest_cache", "signal_model_live_prior.json")
LIVE_PATH = os.path.join(PROJ, "backtest_cache", "signal_model_live.json")

K_DEFAULT = 80.0
_EPS = 1e-6


def _clip(p):
    return min(max(float(p), _EPS), 1.0 - _EPS)


def logit(p):
    p = _clip(p)
    return math.log(p / (1.0 - p))


def sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _raw_blend_weight(n_live, k=K_DEFAULT):
    """纯数学收缩权重 w = n/(n+k)，不含任何部署闸门判断（自检/公式验证用）。"""
    n = max(float(n_live), 0.0)
    return n / (n + float(k))


def blend_weight(n_live, k=K_DEFAULT):
    """有效 live 融合权重 = 部署闸门 × 收缩公式。

    闸门唯一判定源是 signal_model._live_deploy_gate()（见 signal_model.py:400），
    本模块不重新实现任何阈值，只复用结论：
      闸门未通过 → 强制 0（与推理侧 signal_model._live_blend_weight() 一致，全权重给先验）
      闸门通过   → n/(n+k)，n 为非负真实样本数（有效样本，建议用已贴标条数）
    """
    if not live_deploy_status().get("gate_pass"):
        return 0.0
    return _raw_blend_weight(n_live, k)


def blend(p_prior, p_live, n_live, k=K_DEFAULT):
    """log-odds 收缩合成。p_live 为 None 或 n_live=0 时退化为纯先验。

    返回 dict: {p, w, mode, note}
      mode: "prior_only" / "blended" / "live_only"
    """
    if p_prior is None and p_live is None:
        return {"p": 0.5, "w": 0.0, "mode": "none", "note": "先验与实证均缺失，退化为中性 0.5"}
    if p_prior is None:
        return {"p": _clip(p_live), "w": 1.0, "mode": "live_only",
                "note": "无先验，纯实证"}
    w = blend_weight(n_live, k)
    if p_live is None or w <= 0.0:
        return {"p": _clip(p_prior), "w": 0.0, "mode": "prior_only",
                "note": f"真实样本 n={int(n_live)}，w={w:.3f}，纯先验"}
    z = (1.0 - w) * logit(p_prior) + w * logit(p_live)
    return {"p": sigmoid(z), "w": w, "mode": "blended",
            "note": f"先验 {p_prior:.4f} × 实证 {p_live:.4f} → n={int(n_live)}, w={w:.3f}"}


def load_prior(path=PRIOR_PATH):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def score_prior(model, feats, feature_names=None):
    """用先验模型给一条特征打分。feats 为 dict 或 list。缺失特征用训练集中位数填补。"""
    if model is None:
        return None
    names = feature_names or model["features"]
    med = model.get("impute_median") or [0.0] * len(names)
    if isinstance(feats, dict):
        x = np.array([feats.get(n, np.nan) for n in names], dtype=float)
    else:
        x = np.array(feats, dtype=float)
        if len(x) != len(names):
            raise ValueError(f"特征数不匹配: 给 {len(x)}，需 {len(names)}")
    m = np.isnan(x)
    if m.any():
        x[m] = np.array(med, dtype=float)[m]
    mu = np.array(model["means"], dtype=float)
    sd = np.array(model["stds"], dtype=float)
    sd[sd == 0] = 1.0
    w = np.array(model["w"], dtype=float)
    b = float(model["b"])
    z = float(((x - mu) / sd) @ w + b)
    return sigmoid(z)


def effective_n_live(samples_path=None):
    """有效真实样本数 = live_samples.jsonl 行数。"""
    p = samples_path or os.path.join(PROJ, "backtest_cache", "live_samples.jsonl")
    if not os.path.exists(p):
        return 0
    try:
        with open(p, encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def predict(p_prior, p_live=None, samples_path=None, k=K_DEFAULT):
    """便捷入口：自动读 n_live。"""
    n = effective_n_live(samples_path)
    return blend(p_prior, p_live, n, k)


def _signal_model():
    """延迟导入 signal_model（部署闸门的唯一判定源）。不可用 → None（调用方一律按未过闸降级）。"""
    try:
        if PROJ not in sys.path:
            sys.path.insert(0, PROJ)
        import signal_model
        return signal_model
    except Exception as e:
        print(f"[WARN] live_blend: signal_model 不可用，按未过闸降级: {type(e).__name__}: {e}")
        return None


def _degraded_status(reason):
    """signal_model 不可用时的统一降级结构：gate_pass=False、live 权重 0。"""
    return {"gate_pass": False, "gate_reasons": [reason], "quality": {},
            "prior_weight": 1.0, "live_weight": 0.0, "blend_k": K_DEFAULT,
            "n_live": 0, "prior_available": False, "mode": "prior_only",
            "degraded": True, "target": None, "val_auc": None, "ece": None}


def live_deploy_status():
    """live 模型部署状态，字段全部来自 signal_model.live_blend_weights()（= 部署闸门的结论）。

    返回 dict：
      gate_pass / gate_reasons / quality  —— 闸门判定与质量指标（val_auc/ece/mce/n/pos/...）
      live_weight / prior_weight          —— 推理侧真实生效的融合权重
      n_live                              —— 过闸才非 0（与 signal_model 一致）
      target / val_auc / ece              —— 文案引用的元信息（未过闸时仅供如实披露，不得宣称已部署）
      degraded                            —— True 表示 signal_model 不可用，按未过闸处理
    """
    sm = _signal_model()
    if sm is None:
        return _degraded_status("signal_model 不可导入")
    try:
        d = sm.live_blend_weights() or {}
        q = d.get("quality") or {}
        return {
            "gate_pass": bool(d.get("gate_pass")),
            "gate_reasons": list(d.get("gate_reasons") or []),
            "quality": dict(q),
            "prior_weight": float(d.get("prior_weight") or 0.0),
            "live_weight": float(d.get("live_weight") or 0.0),
            "blend_k": d.get("blend_k", K_DEFAULT),
            "n_live": int(d.get("n_live") or 0),
            "prior_available": bool(d.get("prior_available")),
            "mode": d.get("mode", "prior_only"),
            "degraded": False,
            "target": getattr(sm, "LIVENOL_TARGET", None),
            "val_auc": q.get("val_auc"),
            "ece": q.get("ece"),
        }
    except Exception as e:
        return _degraded_status(f"live_blend_weights() 失败: {type(e).__name__}: {e}")


def health():
    """返回当前先验/实证状态，供自动化与日报使用。

    新增 gate_pass / gate_reasons / gate_quality / live_model_deployed / n_live_model：
    全部来自 signal_model 部署闸门，不按"文件存在"就宣称已部署（旧字段全部保留，向后兼容）。
    """
    prior = load_prior()
    n = effective_n_live()
    st = live_deploy_status()
    gate_pass = bool(st.get("gate_pass"))
    w = blend_weight(n)          # 闸门未过时内部已返回 0，与推理侧一致
    return {
        "prior_exists": prior is not None,
        "prior_target": (prior or {}).get("target"),
        "prior_oos_auc": (prior or {}).get("oos_auc"),
        "prior_oos_lift": (prior or {}).get("oos_lift"),
        "prior_calibration": (prior or {}).get("calibration"),
        "n_live": n,
        "blend_k": K_DEFAULT,
        "prior_weight": round(1.0 - w, 4),
        "live_weight": round(w, 4),
        "mode": ("prior_only" if w <= 0.0
                 else ("blended" if n < 4 * K_DEFAULT else "live_dominant")),
        "live_model_exists": os.path.exists(LIVE_PATH),
        # ↓ 部署闸门（唯一判定源 signal_model._live_deploy_gate）
        "gate_pass": gate_pass,
        "gate_reasons": list(st.get("gate_reasons") or []),
        "gate_quality": dict(st.get("quality") or {}),
        "live_model_deployed": gate_pass,
        "n_live_model": st.get("n_live"),
        "gate_degraded": bool(st.get("degraded")),
    }


def _selftest():
    # 1) 权重曲线（纯公式 _raw_blend_weight，与闸门无关）
    for n in (0, 20, 80, 240, 1000):
        print(f"[selftest] n={n:<5} raw_w={_raw_blend_weight(n):.4f} gate_w={blend_weight(n):.4f}")
    assert _raw_blend_weight(0) == 0.0
    assert abs(_raw_blend_weight(80) - 0.5) < 1e-12
    assert _raw_blend_weight(240) > 0.7

    # 1b) 闸门一致性：blend_weight 必须服从 signal_model 部署闸门
    st = live_deploy_status()
    print(f"[selftest] 部署闸门 gate_pass={st['gate_pass']} 原因={st['gate_reasons']}")
    if st.get("gate_pass"):
        assert abs(blend_weight(80) - 0.5) < 1e-12, "过闸时应等于纯公式值"
        assert blend_weight(240) > 0.7
    else:
        assert blend_weight(80) == 0.0 and blend_weight(10000) == 0.0, "未过闸时 live 权重必须恒为 0"
        assert health()["live_weight"] == 0.0 and health()["prior_weight"] == 1.0

    # 2) n=0 必须等于纯先验
    r = blend(0.62, 0.30, 0)
    assert r["mode"] == "prior_only" and abs(r["p"] - 0.62) < 1e-12, r

    # 3) 无先验时纯实证
    r = blend(None, 0.30, 50)
    assert r["mode"] == "live_only" and abs(r["p"] - 0.30) < 1e-9, r

    # 4) 凸组合数学性质：用纯公式权重直接验证（与闸门无关，闸门锁死时也能验公式）
    w80 = _raw_blend_weight(80)
    for pp, pl in ((0.60, 0.70), (0.60, 0.40)):
        pf = sigmoid((1 - w80) * logit(pp) + w80 * logit(pl))
        print(f"[selftest] 纯公式 w={w80:.3f}: prior {pp:.2f} live {pl:.2f} -> p={pf:.4f} "
              f"(应在 {min(pp, pl):.2f}~{max(pp, pl):.2f} 之间)")
        assert min(pp, pl) - 1e-9 <= pf <= max(pp, pl) + 1e-9, (pp, pl, pf)

    # 5) 权重随 n 单调 → 先验影响单调衰减（纯公式层面）
    p10 = sigmoid((1 - _raw_blend_weight(10)) * logit(0.70) + _raw_blend_weight(10) * logit(0.40))
    p1000 = sigmoid((1 - _raw_blend_weight(1000)) * logit(0.70) + _raw_blend_weight(1000) * logit(0.40))
    print(f"[selftest] 同一分歧下 n=10 p={p10:.4f} vs n=1000 p={p1000:.4f} (应更靠 .40)")
    assert p1000 < p10, "样本越多应越靠实证"

    # 6) blend() 必须服从闸门：未过闸 → 恒为纯先验（与推理侧一致）；过闸 → 凸组合
    r = blend(0.70, 0.40, 1000)
    if st.get("gate_pass"):
        print(f"[selftest] 已过闸：blend(0.70,0.40,n=1000) mode={r['mode']} p={r['p']:.4f}")
        assert r["mode"] == "blended" and 0.40 - 1e-9 <= r["p"] <= 0.70 + 1e-9, r
    else:
        print(f"[selftest] 未过闸：blend(0.70,0.40,n=1000) mode={r['mode']} p={r['p']:.4f} (应=先验 0.70)")
        assert r["mode"] == "prior_only" and abs(r["p"] - 0.70) < 1e-12, r

    # 7) 先验打分器
    m = {"features": ["a", "b"], "means": [0.0, 0.0], "stds": [1.0, 1.0],
         "w": [1.0, -1.0], "b": 0.0, "impute_median": [0.0, 0.0]}
    p = score_prior(m, {"a": 2.0, "b": 0.0})
    print(f"[selftest] score_prior(a=2,b=0) -> {p:.4f} (应≈0.8808)")
    assert abs(p - 1 / (1 + math.exp(-2))) < 1e-9
    p_nan = score_prior(m, {"a": 2.0})  # b 缺失 → 中位数 0
    assert abs(p_nan - p) < 1e-12, "缺失特征应用中位数填补"
    print("[selftest] PASS — 收缩合成/退化路径/打分器均正确")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--score", default=None, help="JSON 特征串，用先验打分")
    ap.add_argument("--k", type=float, default=K_DEFAULT)
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return
    if args.health:
        print(json.dumps(health(), ensure_ascii=False, indent=2))
        return
    if args.score:
        m = load_prior()
        if m is None:
            print("[error] 先验模型不存在，先跑 research.live_prior_builder")
            return
        feats = json.loads(args.score)
        p = score_prior(m, feats)
        n = effective_n_live()
        print(json.dumps({"p_prior": round(p, 6),
                          "n_live": n,
                          "blend": blend(p, None, n),},
                         ensure_ascii=False, indent=2))
        return
    print("usage: --selftest | --health | --score '<json>'")


if __name__ == "__main__":
    main()
