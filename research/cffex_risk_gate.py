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

# ───────────── 路径单一真相源（与 cffex_position_ingest.SNAPSHOT_P 同源） ─────────────
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "backtest_cache")
# 快照 canonical 路径。供管线接入契约(unified_tail_pick)与文档示例统一引用，
# 避免各模块散落硬编码同一路径字符串造成漂移/phantom 常量。
CFFEX_SNAPSHOT_P = os.path.join(CACHE, "cffex_regime_snapshot.json")

# ───────────── 数字契约（阈值，改动须报旧值→新值→影响面） ─────────────
PERCENTILE_RISK_OFF = 0.85   # 中信净空分位 >= 此值 → 极端偏空
PERCENTILE_CAUTION = 0.70    # 中信净空分位 >= 此值 → 警惕
CONT_ADD_ABS = 5000          # 单品种单日加空绝对值(手) >= 此值 → 硬 spike 升级
RISK_OFF_CAP_MULT = 0.6      # risk_off 时仓位上限乘数（收紧到 60%）

# ───────────── 板块拥挤度 PROXY 边缘加权（第二个边缘因子，与 spot 共振同层） ─────────────
# 板块级筹码抱团 = 风险上下文：本门控接入 sector_chip_crowding.json（research/sector_chip_crowding.py
# 产出的 PROXY，非真实公募持仓）。仅作边缘加权：只紧不松 / 绝不独立触发 / 缺失 pass-through。
# 以下为保守默认，后期可据 OOS 回测调参（改动须报 旧值→新值→影响面）。
SECTOR_CROWD_SHARE_MIN = 0.20      # crowded_share >= 此值（且 level==caution）→ 升级 risk_off
SECTOR_CROWD_CROWD_THRESH = 0.15   # 单题材拥挤分阈值（判定该题材是否算"拥挤"计入分子）
SECTOR_CROWD_SNAPSHOT_P = os.path.join(CACHE, "sector_chip_crowding.json")


def load_snapshot(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_spot(path):
    """读现货指数快照（spot_index_resonance 产出）；缺失/损坏返回 None（pass-through）。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _load_sector_crowding(path):
    """读板块拥挤度 PROXY 快照（sector_chip_crowding.json 产出）；缺失/损坏返回 None（pass-through）。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _sector_crowded_share(snap):
    """计算板块拥挤度占比 PROXY。

    口径：crowded_share = (valid_n>=3 且 turnover_weighted_crowding_PROXY >= SECTOR_CROWD_CROWD_THRESH
    的题材数) / (valid_n>=3 的题材总数)。
    返回 (status, share)：
      status: "OK" | "UNAVAILABLE"(快照缺失/非 dict) | "NO_VALID_THEMES"(无有效题材)
      share: float 或 None（无法计算时）
    纯只读聚合，绝不伪造；题材字段缺失一律跳过。
    """
    if not snap or not isinstance(snap, dict):
        return "UNAVAILABLE", None
    themes = snap.get("themes")
    if not isinstance(themes, dict) or not themes:
        return "NO_VALID_THEMES", None
    denom = 0
    numer = 0
    for _t, v in themes.items():
        if not isinstance(v, dict):
            continue
        vn = v.get("valid_n")
        crowd = v.get("turnover_weighted_crowding_PROXY")
        if isinstance(vn, int) and vn >= 3 and isinstance(crowd, (int, float)):
            denom += 1
            if crowd >= SECTOR_CROWD_CROWD_THRESH:
                numer += 1
    if denom == 0:
        return "NO_VALID_THEMES", None
    return "OK", numer / denom


def evaluate(snapshot=None, snapshot_path=None, alert_on_missing=True,
             spot_snapshot_path=None, sector_crowding=None, sector_crowding_path=None):
    """评估 CFFEX regime。返回上文结构。数据缺失一律 normal(pass-through)。

    spot_snapshot_path：现货指数共振快照（spot_index_resonance 产出），可选。
      传入 None 时自动取 spot_index_resonance.SPOT_SNAPSHOT_P；解析失败则忽略（pass-through）。
      现货侧仅作「共振确认/阻尼」边缘项，绝不独立触发 risk_off，绝不放宽核心阈值
      （PERCENTILE_RISK_OFF/PERCENTILE_CAUTION/CONT_ADD_ABS 不变）。

    sector_crowding / sector_crowding_path：板块拥挤度 PROXY 快照（sector_chip_crowding.json 产出），
      可选。sector_crowding 为已解析 dict 时优先使用；否则取 sector_crowding_path
      （None 时默认 SECTOR_CROWD_SNAPSHOT_P）。缺失/解析失败 → pass-through（detail 记 UNAVAILABLE，
      不改变 level）。板块侧仅作「风险上下文」边缘加权：净空已处 caution 且拥挤题材占比高
      → 升级 risk_off（只紧不松）；normal 态绝不独立触发；risk_off 已触发时仅记录方向。
      绝不放宽 PERCENTILE_RISK_OFF/PERCENTILE_CAUTION/CONT_ADD_ABS/RISK_OFF_CAP_MULT 核心阈值。

    alert_on_missing=True 时，若数据缺失/不足，经共享告警通道显式上报
    （修复「门控悄悄 no-op」的跨系统短板）；self-test 传 False 避免刷屏。
    """
    if spot_snapshot_path is None:
        try:
            from spot_index_resonance import SPOT_SNAPSHOT_P as _SPOT_DEFAULT
            spot_snapshot_path = _SPOT_DEFAULT
        except Exception:
            spot_snapshot_path = None
    if snapshot is None:
        snapshot = load_snapshot(snapshot_path)
    if not snapshot or not isinstance(snapshot, dict):
        if alert_on_missing:
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from alerting import notify
                notify("D5 CFFEX 风险门控数据缺失",
                       "warn",
                       "无快照(normal pass-through)；风险层未生效。需在「毕方灵犀通达」宿主"
                       "验证 CFFEX 端点(现 404/LIVE_ENDPOINT_UNVERIFIED)后由 cffex_position_ingest 产出快照。",
                       dedup_key="d5_cffex_nosnap")
            except Exception:
                pass
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
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from alerting import notify
            notify("D5 CFFEX 风险门控历史不足",
                   "warn",
                   "中信净空分位缺失(历史不足)，门控无法判定极值，pass-through；"
                   "风险层未生效。需 cffex_position_ingest 累积真实持仓排名历史。",
                   dedup_key="d5_cffex_insufficient")
        except Exception:
            pass
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

    # ── 现货侧共振（升贴水现货端，D5 basis/TODO 落地）──
    # 仅作边缘加权：净空已处 caution(分位∈[0.70,0.85))时，现货普跌(bearish)确认 → 升级 risk_off；
    # 现货普涨(bullish) → 阻尼（净空或属套保，维持 caution 不升级）；risk_off 已触发时仅记录方向。
    # 绝不独立触发，绝不放宽 PERCENTILE_RISK_OFF/PERCENTILE_CAUTION/CONT_ADD_ABS 核心阈值
    # （与门控"只收紧不放行"纪律一致）。现货数据缺失/UNVERIFIED 时忽略（pass-through）。
    spot_regime = None
    spot_agg = None
    spot = _load_spot(spot_snapshot_path)
    if spot and spot.get("available") and spot.get("spot_regime") in ("bearish", "bullish"):
        spot_regime = spot["spot_regime"]
        spot_agg = spot.get("aggregate_pct")
        if level == "caution" and spot_regime == "bearish":
            level = "risk_off"
            reasons.append("现货四大指数普跌(%.2f%%) bearish 共振确认 → 升级 risk_off"
                           % (spot_agg * 100))
        elif level == "caution" and spot_regime == "bullish":
            reasons.append("现货普涨 bullish 阻尼：净空或属套保，维持 caution 谨慎对待")
        elif level == "risk_off" and spot_regime == "bullish":
            reasons.append("risk_off 已触发(净空极值)，但现货 bullish：净空或部分为套保，留意假信号")

    # ── 板块拥挤度 PROXY 边缘加权（第二个边缘因子，与 spot 同层）──
    # 板块级筹码抱团 = 风险上下文：仅当净空已处 caution 边缘时，拥挤题材占比高 → 升级 risk_off；
    # normal 态绝不独立触发；risk_off 已触发时仅记录方向；缺失/无效 → pass-through。
    # 绝不改动 PERCENTILE_RISK_OFF/PERCENTILE_CAUTION/CONT_ADD_ABS/RISK_OFF_CAP_MULT 核心阈值
    # （与门控"只收紧不放行"纪律一致）。
    sector_status, crowded_share = "UNAVAILABLE", None
    sector_escalated = False
    if sector_crowding is None:
        if sector_crowding_path is None:
            sector_crowding_path = SECTOR_CROWD_SNAPSHOT_P
        sector_crowding = _load_sector_crowding(sector_crowding_path)
    if sector_crowding is not None:
        sector_status, crowded_share = _sector_crowded_share(sector_crowding)
    if sector_status == "OK" and crowded_share is not None:
        if level == "caution" and crowded_share >= SECTOR_CROWD_SHARE_MIN:
            level = "risk_off"
            sector_escalated = True
            reasons.append("板块拥挤度 PROXY 占比=%.2f >= %.2f 且净空 caution → 升级 risk_off"
                           % (crowded_share, SECTOR_CROWD_SHARE_MIN))
        elif level == "risk_off":
            reasons.append("risk_off 已触发(净空极值)，板块拥挤占比=%.2f 方向记录" % crowded_share)
        # level == "normal" → 仅记录（detail），绝不独立触发

    if level == "normal":
        reasons.append("中信净空未达警惕阈值，normal pass-through")

    return _mk(level, level == "risk_off", reasons, {
        "max_percentile": max_pct,
        "add_spikes": add_spikes,
        "citic_dominant": citic_dominant,
        "spot_regime": spot_regime,
        "spot_aggregate_pct": spot_agg,
        "citic_aggregate_net_short": (snapshot.get("citic_aggregate") or {}).get("net_short"),
        "sector_crowding": sector_status,
        "sector_crowded_share": crowded_share,
        "sector_crowding_escalated": sector_escalated,
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
    r0 = evaluate(snapshot_path="/nonexistent/path.json", alert_on_missing=False)
    assert r0["level"] == "normal" and r0["data_state"] == "NO_SNAPSHOT", r0

    # 2) 正常：分位低
    snap_ok = {
        "percentile": {"IF": 0.40, "IC": 0.35},
        "citic_by_product": {"IF": {"net_short_var": 200}, "IC": {"net_short_var": -100}},
        "resonance": {"citic_dominant": False},
        "citic_aggregate": {"net_short": 20000},
    }
    r1 = evaluate(snapshot=snap_ok, sector_crowding={"themes": {}})
    assert r1["level"] == "normal", r1

    # 3) 极端偏空：分位高
    snap_risk = {
        "percentile": {"IF": 0.92, "IC": 0.88},
        "citic_by_product": {"IF": {"net_short_var": 300}, "IC": {"net_short_var": 100}},
        "resonance": {"citic_dominant": True},
        "citic_aggregate": {"net_short": 60000},
    }
    r2 = evaluate(snapshot=snap_risk, sector_crowding={"themes": {}})
    assert r2["level"] == "risk_off" and r2["risk_off"], r2

    # 4) 硬 spike 升级：分位仅 caution，但单日加空 >= 5000
    snap_spike = {
        "percentile": {"IF": 0.72},
        "citic_by_product": {"IF": {"net_short_var": 6200}},
        "resonance": {"citic_dominant": False},
        "citic_aggregate": {"net_short": 30000},
    }
    r3 = evaluate(snapshot=snap_spike, sector_crowding={"themes": {}})
    assert r3["level"] == "risk_off", r3

    # 5) 警惕边界
    snap_cau = {
        "percentile": {"IF": 0.75},
        "citic_by_product": {"IF": {"net_short_var": 300}},
        "resonance": {"citic_dominant": False},
        "citic_aggregate": {"net_short": 25000},
    }
    r4 = evaluate(snapshot=snap_cau, sector_crowding={"themes": {}})
    assert r4["level"] == "caution", r4

    # 6) 现货 bearish 共振：caution 边缘 + 现货普跌 → 升级 risk_off（边缘加权，非独立触发）
    import tempfile as _tf
    spot_bear = {"available": True, "spot_regime": "bearish", "aggregate_pct": -0.8, "source": "t"}
    tfb = _tf.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(spot_bear, tfb); tfb.close()
    r5 = evaluate(snapshot=snap_cau, spot_snapshot_path=tfb.name, sector_crowding={"themes": {}})
    os.unlink(tfb.name)
    assert r5["level"] == "risk_off", r5
    assert r5["detail"]["spot_regime"] == "bearish", r5

    # 7) 现货 bullish 阻尼：caution 边缘 + 现货普涨 → 维持 caution 不升级
    spot_bull = {"available": True, "spot_regime": "bullish", "aggregate_pct": 0.9, "source": "t"}
    tfu = _tf.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(spot_bull, tfu); tfu.close()
    r6 = evaluate(snapshot=snap_cau, spot_snapshot_path=tfu.name, sector_crowding={"themes": {}})
    os.unlink(tfu.name)
    assert r6["level"] == "caution", r6

    # ── 板块拥挤度 PROXY 边缘加权（第二个边缘因子）──
    # 8) caution 边缘 + 拥挤题材占比高 → 升级 risk_off（只紧不松，非独立触发）
    sec_high = {"themes": {
        "A": {"valid_n": 5, "turnover_weighted_crowding_PROXY": 0.30},
        "B": {"valid_n": 5, "turnover_weighted_crowding_PROXY": 0.28},
        "C": {"valid_n": 6, "turnover_weighted_crowding_PROXY": 0.26},
        "D": {"valid_n": 5, "turnover_weighted_crowding_PROXY": 0.10},
        "E": {"valid_n": 4, "turnover_weighted_crowding_PROXY": 0.09},
    }}
    # valid_n>=3 总数=5；其中 A/B/C(>=0.15)计入分子=3 → share=0.6 >= 0.20
    r7 = evaluate(snapshot=snap_cau, sector_crowding=sec_high)
    assert r7["level"] == "risk_off", r7
    assert r7["detail"]["sector_crowding_escalated"] is True, r7
    assert abs(r7["detail"]["sector_crowded_share"] - 0.6) < 1e-9, r7

    # 9) caution 边缘 + 拥挤题材占比低 → 维持 caution（不升级）
    sec_low = {"themes": {
        "A": {"valid_n": 5, "turnover_weighted_crowding_PROXY": 0.10},
        "B": {"valid_n": 5, "turnover_weighted_crowding_PROXY": 0.08},
    }}
    # valid_n>=3 总数=2；分子(>=0.15)=0 → share=0.0 < 0.20
    r8 = evaluate(snapshot=snap_cau, sector_crowding=sec_low)
    assert r8["level"] == "caution", r8
    assert r8["detail"]["sector_crowding_escalated"] is False, r8

    # 10) normal 态 + 拥挤占比高 → 绝不独立触发，维持 normal
    r9 = evaluate(snapshot=snap_ok, sector_crowding=sec_high)
    assert r9["level"] == "normal", r9
    assert r9["detail"]["sector_crowded_share"] is not None, r9
    assert r9["detail"]["sector_crowding_escalated"] is False, r9

    # 11) 板块快照缺失 → pass-through，caution 态不升级
    r10 = evaluate(snapshot=snap_cau,
                   sector_crowding_path="/nonexistent/sector_crowding_test.json")
    assert r10["level"] == "caution", r10
    assert r10["detail"]["sector_crowding"] == "UNAVAILABLE", r10
    assert r10["detail"]["sector_crowding_escalated"] is False, r10

    print("cffex_risk_gate selftest PASS")
    print("  NO_SNAPSHOT->%s | 正常->%s | 极端->%s | 硬spike->%s | 警惕->%s"
          % (r0["level"], r1["level"], r2["level"], r3["level"], r4["level"]))
    print("  共振: caution+bearish->%s | caution+bullish->%s（边缘加权，非独立触发）"
          % (r5["level"], r6["level"]))
    print("  板块PROXY: caution+高share->%s | caution+低share->%s | normal+高share->%s(不触发) | 缺失->%s(pass-through)"
          % (r7["level"], r8["level"], r9["level"], r10["level"]))
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
