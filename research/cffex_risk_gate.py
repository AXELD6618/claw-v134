# -*- coding: utf-8 -*-
"""
cffex_risk_gate.py — CFFEX 中信净空 regime 门控（服务圣杯管线 risk-off 收紧）

设计纪律（与系统核心原则一致：生产者不得自证 / 缺失即 pass-through / 不伪造）：
  - 门控只"收紧"，不"放行"。当 CFFEX 数据缺失/不足时，返回 level="normal"（pass-through），
    绝不让门控本身成为圣杯管线的单点故障。
  - 所有判定阈值为显式数字契约（见下方 CONSTANTS），改动须报 旧值→新值→影响面。
  - 升贴水(basis)共振项依赖现货数据，本门控不实现，标 TODO（见 cffex_position_ingest）。

门控信号（数据层信号由 cffex_position_ingest.build_snapshot 提供）：
  1) 中信净空历史分位（percentile）：跨品种取最大分位，越高越偏空。
     - >= PERCENTILE_RISK_OFF(0.85) → 极端偏空
     - >= PERCENTILE_CAUTION(0.70) → 警惕
  2) 单日加空冲击（citic net_short_var）：任一品种单日加空绝对值 >= CONT_ADD_ABS(5000手)
     → 视为激进同向加空，将 caution 升级为 risk_off（硬 spike 优先）。
  3) 中信独加（resonance.citic_dominant=True）：个体行为而非全市场共识，本身中性，
     仅作为"分位边缘时"的加权项，不单独触发（避免误伤）。

输出：evaluate() -> {
    "level": "normal" | "caution" | "risk_off",
    "risk_off": bool,                       # level == "risk_off"
    "reasons": [str, ...],
    "detail": {...},
    "data_state": "OK" | "NO_SNAPSHOT" | "INSUFFICIENT_HISTORY",
}

管线接入契约（unified_tail_pick 调用方式，guarded pass-through）：
    from research.cffex_risk_gate import evaluate
    gate = evaluate(snapshot_path=CFFEX_SNAPSHOT_P)   # 缺失→normal
    if gate["level"] == "risk_off":
        cap = int(cap * RISK_OFF_CAP_MULT)            # 收紧仓位上限
        # 或抬高入选阈值 / 跳过边际信号
    # caution / normal → 不动作
"""

import os
import json

# ───────────── 数字契约（阈值，改动须报旧值→新值→影响面） ─────────────
PERCENTILE_RISK_OFF = 0.85   # 中信净空分位 >= 此值 → 极端偏空
PERCENTILE_CAUTION = 0.70    # 中信净空分位 >= 此值 → 警惕
CONT_ADD_ABS = 5000          # 单品种单日加空绝对值(手) >= 此值 → 硬 spike 升级
RISK_OFF_CAP_MULT = 0.6      # risk_off 时仓位上限乘数（收紧到 60%）


def load_snapshot(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def evaluate(snapshot=None, snapshot_path=None):
    """评估 CFFEX regime。返回上文结构。数据缺失一律 normal(pass-through)。"""
    if snapshot is None:
        snapshot = load_snapshot(snapshot_path)
    if not snapshot or not isinstance(snapshot, dict):
        return _mk("normal", False, ["NO_SNAPSHOT: 无快照，pass-through"],
                   {}, "NO_SNAPSHOT")

    pct = snapshot.get("percentile") or {}
    citic = snapshot.get("citic_by_product") or {}
    resonance = snapshot.get("resonance") or {}

    # 1) 分位：跨品种取最大
    valid_pct = [v for v in pct.values() if isinstance(v, (int, float))]
    max_pct = max(valid_pct) if valid_pct else None

    # 2) 单日加空冲击
    add_spikes = [p for p, c in citic.items()
                  if isinstance(c, dict) and abs(c.get("net_short_var") or 0) >= CONT_ADD_ABS]

    # 3) 中信独加（边缘加权）
    citic_dominant = resonance.get("citic_dominant")

    reasons = []
    level = "normal"

    if max_pct is None:
        data_state = "INSUFFICIENT_HISTORY"
        reasons.append("分位缺失(历史不足)，无法判定极值，pass-through")
    else:
        data_state = "OK"
        if max_pct >= PERCENTILE_RISK_OFF:
            level = "risk_off"
            reasons.append("中信净空分位=%.2f >= %.2f 极端偏空" %
                           (max_pct, PERCENTILE_RISK_OFF))
        elif max_pct >= PERCENTILE_CAUTION:
            level = "caution"
            reasons.append("中信净空分位=%.2f >= %.2f 警惕" %
                           (max_pct, PERCENTILE_CAUTION))

    # 硬 spike：任何品种单日加空 >= 阈值 → 升级到 risk_off
    if add_spikes:
        reasons.append("单日加空冲击(>=%d手): %s → 升级 risk_off" %
                       (CONT_ADD_ABS, ",".join(add_spikes)))
        level = "risk_off"

    # 中信独加在边缘时加权（仅当已在 caution/risk_off 边缘，强化而非独立触发）
    if citic_dominant and level in ("caution", "risk_off"):
        reasons.append("中信独加(个体行为非全市场共识)，维持警惕")

    if level == "normal":
        reasons.append("中信净空未达警惕阈值，normal pass-through")

    return _mk(level, level == "risk_off", reasons, {
        "max_percentile": max_pct,
        "add_spikes": add_spikes,
        "citic_dominant": citic_dominant,
        "citic_aggregate_net_short": (snapshot.get("citic_aggregate") or {}).get("net_short"),
    }, data_state)


def _mk(level, risk_off, reasons, detail, data_state):
    return {
        "level": level,
        "risk_off": risk_off,
        "reasons": reasons,
        "detail": detail,
        "data_state": data_state,
    }


def _selftest():
    # 1) 缺失快照 → normal
    r0 = evaluate(snapshot_path="/nonexistent/path.json")
    assert r0["level"] == "normal" and r0["data_state"] == "NO_SNAPSHOT", r0

    # 2) 正常：分位低
    snap_ok = {
        "percentile": {"IF": 0.40, "IC": 0.35},
        "citic_by_product": {"IF": {"net_short_var": 200}, "IC": {"net_short_var": -100}},
        "resonance": {"citic_dominant": False},
        "citic_aggregate": {"net_short": 20000},
    }
    r1 = evaluate(snapshot=snap_ok)
    assert r1["level"] == "normal", r1

    # 3) 极端偏空：分位高
    snap_risk = {
        "percentile": {"IF": 0.92, "IC": 0.88},
        "citic_by_product": {"IF": {"net_short_var": 300}, "IC": {"net_short_var": 100}},
        "resonance": {"citic_dominant": True},
        "citic_aggregate": {"net_short": 60000},
    }
    r2 = evaluate(snapshot=snap_risk)
    assert r2["level"] == "risk_off" and r2["risk_off"], r2

    # 4) 硬 spike 升级：分位仅 caution，但单日加空 >= 5000
    snap_spike = {
        "percentile": {"IF": 0.72},
        "citic_by_product": {"IF": {"net_short_var": 6200}},
        "resonance": {"citic_dominant": False},
        "citic_aggregate": {"net_short": 30000},
    }
    r3 = evaluate(snapshot=snap_spike)
    assert r3["level"] == "risk_off", r3

    # 5) 警惕边界
    snap_cau = {
        "percentile": {"IF": 0.75},
        "citic_by_product": {"IF": {"net_short_var": 300}},
        "resonance": {"citic_dominant": False},
        "citic_aggregate": {"net_short": 25000},
    }
    r4 = evaluate(snapshot=snap_cau)
    assert r4["level"] == "caution", r4

    print("cffex_risk_gate selftest PASS")
    print("  NO_SNAPSHOT->%s | 正常->%s | 极端->%s | 硬spike->%s | 警惕->%s"
          % (r0["level"], r1["level"], r2["level"], r3["level"], r4["level"]))
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
