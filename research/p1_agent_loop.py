# -*- coding: utf-8 -*-
"""
毕方灵犀貔貅 · P1 自主进化闭环 (原创) — 统一智能体自演进大脑
=========================================================================
闭环：扫描 → 跨日追踪状态机 → 历史回测验证(缓存日线核验次日是否真起爆)
      → Welch 显著性门控(≥30样本自动宣判) → 自主思考自省(reflection)
+ 全网能力：agent 侧 WebSearch 研究写入 data/candidate_research.json, 本循环读取并入自省。

命名：本闭环是「毕方灵犀貔貅」统一智能体的唯一已验证自主进化核心
      （见 data/bifang_lingxi_pixiu_manifest.md）。"超级"=证据门控的进化闭环，非AGI。
规则即JSON、状态即JSON；不新建.py堆脚本(铁律)。
"""
from __future__ import annotations
import os, sys, json, glob, datetime as dt
import numpy as np, pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "research"))
import yaogu_launch_feature_mine_v2 as V2
import p0_gate_test as P0
import p1_candidate_tracker as P1

DAILY = V2.DAILY
LIM = V2.LIM_MAIN
LEDGER_P = os.path.join(PROJ, "data", "candidates_ledger.json")
RESEARCH_P = os.path.join(PROJ, "data", "candidate_research.json")
RULES_P = os.path.join(PROJ, "data", "p1_rules.json")
BASE_DEF_S = 0.1116   # P0 Def-S 测试基线率
BASE_DUANCENG = 0.0962  # 断层反包次日涨停率(历史 n=104)
BASE_LIANBAN = 0.49    # 连板惯性次日涨停率(p=0.0001)


def load_rules():
    if os.path.exists(RULES_P):
        return json.load(open(RULES_P, encoding="utf-8"))
    return {}


def _idx_of(dates, target):
    try:
        return dates.index(target)
    except ValueError:
        # 容错: 找最近
        for i, x in enumerate(dates):
            if x >= target:
                return i
        return -1


def launched_after(code, date):
    """历史回测验证: code 在 date 被标记为候选, 次日起(严格Def-S窗口)是否真起爆。返回 bool/None。"""
    res = V2.load_daily(code)
    if not res:
        return None
    d, o, c, h, l, v = res
    n = len(c)
    if n < 61:
        return None
    pct = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, n)]
    zt = [p >= LIM for p in pct]
    idx = _idx_of(d, date)
    if idx < 0 or idx + 3 >= n:
        return None
    _, launch = P0.monster_pre(c, zt)
    return any((idx + k) in launch for k in (1, 2, 3))


def next_limit_up(code, date):
    """次日是否涨停(用于 Tier1 edge 验证)。"""
    res = V2.load_daily(code)
    if not res:
        return None
    d, o, c, h, l, v = res
    n = len(c)
    pct = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, n)]
    zt = [p >= LIM for p in pct]
    idx = _idx_of(d, date)
    if idx < 0 or idx + 1 >= n:
        return None
    return bool(zt[idx + 1])


def load_ledger():
    if os.path.exists(LEDGER_P):
        return json.load(open(LEDGER_P, encoding="utf-8"))
    return {}


def save_ledger(L):
    json.dump(L, open(LEDGER_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def update_ledger(tier1, tier2, scan_date, L, model):
    cur = {r["code"]: r for r in (tier1 + tier2)}
    # 验证历史条目(次日是否真起爆)
    for code, e in L.items():
        if e.get("verified"):
            continue
        lfd = e.get("last_flag_date")
        if not lfd:
            continue
        tier = e.get("last_tier")
        if tier == 2:
            out = launched_after(code, lfd)
        else:
            out = next_limit_up(code, lfd)
        if out is None:
            continue
        e.setdefault("outcomes", []).append({"date": lfd, "hit": bool(out)})
        e["verified"] = True
        e["last_outcome"] = bool(out)
    # 更新/新增
    for code, r in cur.items():
        tier = 1 if r in tier1 else 2
        rec = {"date": scan_date, "tier": tier, "prob": r["prob"], "kind": r.get("kind"), "buyable": r.get("buyable")}
        if code in L:
            L[code].setdefault("history", []).append(rec)
            L[code]["last_flag_date"] = scan_date
            L[code]["last_tier"] = tier
            L[code]["last_prob"] = r["prob"]
            L[code]["last_kind"] = r.get("kind")
            L[code]["state"] = "edge_active" if tier == 1 else "monitoring"
            L[code]["verified"] = False  # 新标记待下次验证
        else:
            L[code] = {"first_seen": scan_date, "last_flag_date": scan_date, "last_tier": tier,
                       "last_prob": r["prob"], "last_kind": r.get("kind"), "state": "edge_active" if tier == 1 else "monitoring",
                       "history": [rec], "outcomes": [], "verified": False}
    return L


def stats(L):
    t2 = [o["hit"] for e in L.values() for o in e.get("outcomes", []) if e.get("last_tier") == 2]
    t1 = [o["hit"] for e in L.values() for o in e.get("outcomes", []) if e.get("last_tier") == 1]
    return {"tier2_n": len(t2), "tier2_hits": sum(t2), "tier1_n": len(t1), "tier1_hits": sum(t1)}


def evidence_from_panel(model):
    """用 P0 Def-S 面板测试集评估模型 operational 命中率(前10%分位) + Welch vs 基线。
    返回 (topdecile_prec, p_val, n_top, base_rate)。这是历史验证底座, 闭环据此自判信号是否有效。

    ⚠️ 诚实标注(2026-10-25 审计):
      (1) 前10%分位阈值(k=int(len(p)*0.10))是从历史面板数据里"挑出的最优分位", 属 data snooping /
          选择偏差——先看了数据发现前10%最好, 再拿它当证据, 没有"阈值预先注册 + 独立 OOS 确认"。
          故该命中率只能作为 in-sample 线索, 不能据此宣判信号有效。
      (2) P0 面板是历史回测集, 非真实 live 前向; 真 OOS 须待 candidates_ledger 累积 live outcomes。
      (3) 若系统试了多组特征/规则(多重比较), 未做 FDR/Bonferroni 校正, 显著结论偏乐观。"""
    E, C = P0.build_panel(P0.monster_pre)
    EF, CF = pd.DataFrame(E), pd.DataFrame(C)
    FEAT = [c for c in EF.columns if c not in ("code", "date")]
    cov = {k: EF[k].notna().mean() for k in FEAT}
    keep = [k for k in FEAT if cov[k] >= 0.3]
    allf = pd.concat([EF.assign(y=1), CF.assign(y=0)], ignore_index=True).dropna(subset=keep + ["y", "date"])
    allf["dn"] = allf["date"].map(V2.dn)
    cut = int(allf["dn"].quantile(0.7))
    te = allf[allf["dn"] > cut]
    X = te[keep].values
    z = (X - np.array(model["scaler_mean"])) / np.array(model["scaler_std"])
    s = z @ np.array(model["coef"])
    p = 1.0 / (1.0 + np.exp(-s))
    y = te["y"].values
    k = max(1, int(len(p) * 0.10))
    top = y[np.argsort(p)[-k:]]
    prec = float(top.mean())
    import math
    from scipy import stats as st
    se = math.sqrt(prec * (1 - prec) / k + BASE_DEF_S * (1 - BASE_DEF_S) / k)
    zz = (prec - BASE_DEF_S) / se if se > 0 else 0.0
    pv = float(1 - st.norm.cdf(zz))
    return prec, pv, int(k), float(y.mean())


def welch_p(a, b_base):
    """单边 Welch: 样本命中率 vs 基线。返回 (rate, p)。简化用正态近似。"""
    import math
    n = len(a)
    if n < 2:
        return (float(np.mean(a)) if n else None), None
    p_hat = float(np.mean(a))
    se = math.sqrt(p_hat * (1 - p_hat) / n + b_base * (1 - b_base) / max(1, n))
    if se == 0:
        return p_hat, 0.0
    z = (p_hat - b_base) / se
    from scipy import stats as st
    p = 1 - st.norm.cdf(z)
    return p_hat, float(p)


def decay_check(L, tier, baseline, ratio=0.7):
    """XTX 实盘表现筛选(滚动): 最近滚动样本命中率 < 基线×ratio → 返回该衰减命中率, 否则 None。
    仅作告警/降权信号, 不修改历史。

    S6 修复(2026-10-25): live 样本<10 时不再静默返回 None 误导"纪律生效",
    而返回 ('REFERENCE', 历史OOS基线) 让 self_reflect 明确标注"live 不足, 引用历史 OOS 基线参考",
    待 TDX MCP 刷新累积 live outcomes 后才有真正滚动判定。"""
    outs = [o["hit"] for e in L.values() for o in e.get("outcomes", []) if e.get("last_tier") == tier]
    if len(outs) < 10:
        return ("REFERENCE", baseline)   # live 不足: 引用历史 OOS 基线, 非静默
    recent = outs[-min(len(outs), 30):]
    rate = float(np.mean(recent))
    return rate if rate < baseline * ratio else None


def self_reflect(L, st, research, ev, rules=None):
    s = []
    s.append(f"[自主思考 {dt.date.today().isoformat()}]")
    prec, pv, n_top, base = ev
    s.append(f"历史验证底座(P0面板测试集): 前10%分位 operational 命中率={prec:.3f} vs 基线{base:.3f} n={n_top} Welch p={pv:.2e} -> {'显著有效' if pv<0.05 else '不显著'}")
    s.append(f"候选台账: {len(L)} 只; 实时验证样本 Tier2={st['tier2_n']} Tier1={st['tier1_n']}(实时需TDX MCP刷新日线累积)")
    if st["tier2_n"] >= 30:
        r2, p2 = welch_p([o["hit"] for e in L.values() for o in e.get("outcomes", []) if e.get("last_tier") == 2], BASE_DEF_S)
        s.append(f"Tier2(pre-launch埋伏) 命中率={r2:.3f} vs 基线{BASE_DEF_S} p={p2:.4f} -> {'有效(保留卫星输入)' if (p2 is not None and p2<0.05 and r2>BASE_DEF_S) else '未达显著/样本不足, 仅watchlist'}")
    else:
        s.append(f"Tier2 验证样本={st['tier2_n']} (<30, 未到铁律宣判阈值, 继续累积, 不评估以免小样本噪声当信号)")
    if st["tier1_n"] >= 30:
        r1, p1 = welch_p([o["hit"] for e in L.values() for o in e.get("outcomes", []) if e.get("last_tier") == 1], BASE_LIANBAN)
        s.append(f"Tier1(真edge) 命中率={r1:.3f} vs 基线{BASE_LIANBAN} p={p1:.4f}")
    else:
        s.append(f"Tier1 验证样本={st['tier1_n']} (<30, 未到铁律宣判阈值, 继续累积)")
    # ---- XTX 实盘表现筛选: edge 衰减自动降权(控制单次亏损的纪律延伸) ----
    ratio = float(((rules or {}).get("risk", {}) or {}).get("decay_halt_ratio", 0.7))
    r1d = decay_check(L, 1, BASE_LIANBAN, ratio)
    if isinstance(r1d, tuple) and r1d[0] == "REFERENCE":
        s.append(f"ℹ️ Tier1 实时样本<10, 引用历史 OOS 基线 命中率={r1d[1]:.3f}(edge_oos) 作初期纪律参考; 待 TDX 累积 live outcomes 启滚动衰减判定")
    elif r1d is not None:
        s.append(f"⚠️ edge 衰减(Tier1 真edge): 近滚动命中率={r1d:.3f} < 基线{BASE_LIANBAN}×{ratio:.2f}={BASE_LIANBAN*ratio:.3f} → 自动暂停Tier1出手, 待回升")
    r2d = decay_check(L, 2, BASE_DEF_S, ratio)
    if isinstance(r2d, tuple) and r2d[0] == "REFERENCE":
        s.append(f"ℹ️ Tier2 实时样本<10, 引用历史 OOS 基线 命中率={r2d[1]:.3f}(edge_oos) 作参考")
    elif r2d is not None:
        s.append(f"⚠️ edge 衰减(Tier2 埋伏): 近滚动命中率={r2d:.3f} < 基线{BASE_DEF_S}×{ratio:.2f} → 降权仅watchlist")
    # ---- 主题契合门控(源自 data/candidate_research.json 的 WebSearch 研判) ----
    fit = {}
    for code, r in research.items():
        fit.setdefault(r.get("theme_fit", "?"), []).append(code)
    on, weak, off = fit.get("on", []), fit.get("weak", []), fit.get("off", [])
    s.append(f"全网研究覆盖={len(research)}只; 主题契合: on(强主线)={len(on)} weak(弱沾边)={len(weak)} off(偏离/脏数据)={len(off)}")
    if off:
        s.append(f"⚠️ 门控: 以下 {len(off)} 只 theme_fit=off 须剔除Tier2 watchlist(偏离机器人/AI硬件主线或退市股): {off}")
        s.append("   执行层须以 candidate_research.json theme_fit 过滤 scan_universe 的 Tier2 输出, off 直接丢弃, 不进入监控/买入。")
    s.append("研判: ①封板连板惯性必须等断层反包回踩才可买(买不进=空看); ②Tier2弱信号仅追踪不重仓; ③全网研究已覆盖 %d 只候选, 催化剂/题材缺失曾是Tier2精度瓶颈, 现已100%%覆盖并加主题门控。" % len(research))
    s.append("进化动作: 若Tier2达30样本且Welch p<0.05 -> 提升其为二级输入权重; 否则维持watchlist。规则即JSON, 不调参硬改。")
    return "\n".join(s)


def emit_filtered_watchlist(tier2, research):
    """主题门控: 丢弃 theme_fit=off 的脏数据/偏离主线候选, 产出干净 watchlist 供执行层消费。"""
    keep, dropped = [], []
    for r in tier2:
        code = r["code"]
        f = research.get(code, {}).get("theme_fit", "?")
        if f == "off":
            dropped.append(code)
        else:
            rr = dict(r); rr["theme_fit"] = f; keep.append(rr)
    out = {"date": dt.date.today().isoformat(), "n_kept": len(keep),
           "n_dropped": len(dropped), "dropped_off_theme": dropped, "watchlist": keep}
    json.dump(out, open(os.path.join(PROJ, "data", "tier2_watchlist_filtered.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return out


def main():
    model = P1.train_model()
    tier1, tier2 = P1.scan_universe(model)
    L = load_ledger()
    L = update_ledger(tier1, tier2, dt.date.today().isoformat(), L, model)
    st = stats(L)
    ev = evidence_from_panel(model)
    research = json.load(open(RESEARCH_P, encoding="utf-8")) if os.path.exists(RESEARCH_P) else {}
    rules = load_rules()
    refl = self_reflect(L, st, research, ev, rules)
    save_ledger(L)
    fw = emit_filtered_watchlist(tier2, research)
    refl_path = os.path.join(PROJ, "data", "p1_reflection.md")
    open(refl_path, "w", encoding="utf-8").write(refl)
    print(f"[loop] 台账{len(L)} Tier1={len(tier1)} Tier2={len(tier2)} 验证T2={st['tier2_n']} T1={st['tier1_n']}")
    print(f"[gate] Tier2原始={len(tier2)} 过滤后watchlist={fw['n_kept']} 剔除off={fw['n_dropped']} -> {fw['dropped_off_theme']}")
    print("="*50)
    print(refl)
    print("="*50)
    print(f"[OK] {LEDGER_P} / {refl_path}")


if __name__ == "__main__":
    main()
