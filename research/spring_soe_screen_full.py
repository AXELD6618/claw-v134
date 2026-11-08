#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · 全链路选股能力 (Orchestrator)
==================================================================
四层架构:
  Layer A  本地廉价预筛 (research/spring_soe_screen.py)
          -> 近250日 日均换手<=1.5% ∧ 横盘压缩 ∧ 无急涨
          -> backtest_cache/spring_soe_layerA.json (407 只)

  Layer B  TDX screener 国资中小盘宇宙
          -> 实际控制人=国资 ∧ 总市值<200亿  (714 只, 已落盘 spring_soe_layerB.json)
          -> 与 Layer A 相交 -> 中小盘安静国资 (73 只, spring_soe_layerAB.json)

  Layer C  TDX cyq 引擎 (research/cyq_engine.py, TDX 同源前复权日线重建)
          -> CYS∈[-5,+5]  (价格压在13日成本均线附近, 未拉伸)
          ∧  SCR 历史偏低带 (当前筹码离散度处后20%分位 = 弹簧压到底)
          -> 最终弹簧池 (8 只, spring_soe_layerC.json)

  Layer D  事件触发层 (停牌/重组预案/国资资产注入)  —— 实时监控钩子
          -> 对弹簧池做催化剂扫描; 命中即触发 (见 layerD_scan)

产物: backtest_cache/spring_soe_final.json
      (含 A/B/C 全部指标 + D 催化剂标记, 按 SCR 分位升序 = 弹簧压得最紧在前)

用法:
  python research/spring_soe_screen_full.py
  (Layer A/B 产物已落盘, 本脚本负责相交 + Layer C + 汇总 + Layer D 钩子)
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cyq_engine import analyze

OUT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"
CYS_LO, CYS_HI = -5.0, 5.0
SCR_LOW_BAND = 0.20
WINDOW = 250

# ---- Layer D 催化剂主题词表 (来自 TDX F10 关联主题真实标签) ----
# 命中即视为"弹簧已搭好引信": 停牌/重组预案/国资资产注入/国企改革等
CATALYST_THEMES = ["国企改革", "广东国资改革", "上海国资改革", "北京国资改革",
                   "浙江国资改革", "深圳国资改革", "活跃小盘国企", "双百企业",
                   "并购重组预案", "定增预案", "整体上市", "资产重组", "资产注入",
                   "停牌", "筹划重大事项", "借壳", "混改", "国有資本", "注入", "并购"]

# Layer D 实时催化剂缓存 (由 MCP tdx_api_data 拉取 F10 关联主题后落盘;
# 本地 Python 无法直连 MCP, 故由 agent 抓取并写入此缓存, 编排器读取填充)
LAYERD_CACHE = os.path.join(OUT, "spring_soe_layerD_catalysts.json")

def layerD_scan(code, sec_name, themes=None, catalyst_text=None):
    """催化剂扫描.
    优先匹配 TDX F10 关联主题标签(themes); 若有公告文本(catalyst_text)额外做关键词匹配.
    返回命中的催化剂标签列表, 无命中返回 [].
    """
    hits = []
    if themes:
        hits += [t for t in themes if t in CATALYST_THEMES]
    if catalyst_text:
        hits += [k for k in CATALYST_THEMES if k in catalyst_text]
    seen = set(); out = []
    for h in hits:
        if h not in seen:
            seen.add(h); out.append(h)
    return out

def main():
    la = json.load(open(os.path.join(OUT, "spring_soe_layerA.json"), encoding="utf-8"))
    lb = json.load(open(os.path.join(OUT, "spring_soe_layerB.json"), encoding="utf-8"))
    lab = json.load(open(os.path.join(OUT, "spring_soe_layerAB.json"), encoding="utf-8"))

    la_codes = {x["code"] for x in la}
    lb_by_code = {x["code"]: x for x in lb}

    # ---- Layer D: 载入实时催化剂缓存 (由 MCP tdx_api_data 抓取 F10 关联主题落盘) ----
    d_cache = {}
    if os.path.exists(LAYERD_CACHE):
        try:
            for r in json.load(open(LAYERD_CACHE, encoding="utf-8")):
                d_cache[r["code"]] = r
        except Exception:
            d_cache = {}
    LAYERD_DATE = (list(d_cache.values())[0].get("fetched_date", "?")) if d_cache else "未接入"

    # ---- Layer C: 对 73 只相交池跑 cyq ----
    cands = []
    for rec in lab:
        code = rec["code"]
        try:
            r = analyze(code, window=WINDOW)
        except Exception as e:
            continue
        if r is None:
            continue
        s = r["scr_series"]
        svals = [x for x in s if x is not None]
        if len(svals) < 60:
            continue
        cur_scr = s[-1]
        pctl = sum(1 for x in svals if x <= cur_scr) / len(svals)
        cys = r["cys_latest"]
        cys_ok = (cys is not None) and (CYS_LO <= cys <= CYS_HI)
        scr_ok = pctl <= SCR_LOW_BAND
        m = dict(rec)
        m.update({
            "cys": round(cys, 3) if cys is not None else None,
            "scr": round(cur_scr, 3),
            "scr_pctl": round(pctl, 3),
            "scr_min": round(min(svals), 3),
            "scr_max": round(max(svals), 3),
            "cys_ok": cys_ok, "scr_ok": scr_ok,
            "pass": cys_ok and scr_ok,
        })
        cands.append(m)

    passed = [x for x in cands if x["pass"]]
    passed.sort(key=lambda x: x["scr_pctl"])

    # ---- Layer D: 读取实时催化剂缓存 (TDX F10 关联主题), 命中即标记 ----
    for m in passed:
        rec = d_cache.get(m["code"])
        themes = rec.get("themes") if rec else None
        m["catalyst"] = layerD_scan(m["code"], m["sec_name"], themes=themes)
        if rec and rec.get("risk_flags"):
            m["risk_flags"] = rec["risk_flags"]

    final = passed
    json.dump(final, open(os.path.join(OUT, "spring_soe_final.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("="*70)
    print("弹簧压到底 · 全链路选股结果")
    print("="*70)
    print("LayerA 安静压缩池      : %d 只" % len(la))
    print("LayerB 国资中小盘宇宙  : %d 只 (TDX screener 落盘)" % len(lb))
    print("相交(中小盘安静国资)   : %d 只" % len(lab))
    print("LayerC(CYS∩SCR偏低带) : %d 只  ★ 最终弹簧池" % len(final))
    print("-"*70)
    print("%-7s %-8s %8s %7s %7s %7s %8s  %s" %
          ("代码","名称","换手%","CYS","SCR","SCR分位","市值(亿)","实控人"))
    for m in final:
        la_m = m["layerA"]
        print("%-7s %-8s %7.3f%% %7.2f %7.2f %7.2f %8.1f  %s" % (
            m["code"], m["sec_name"], la_m["turnover"], m["cys"], m["scr"],
            m["scr_pctl"], (m["mktcap"] or 0)/1e8, m["controller"]))
    print("-"*70)
    print("-"*70)
    print("LayerD 实时催化剂 (TDX F10 关联主题, 拉取日 %s):" % LAYERD_DATE)
    for m in final:
        cat = m.get("catalyst") or []
        if cat:
            print("  %-7s %-8s -> ★ 引信已搭: %s" % (m["code"], m["sec_name"], ", ".join(cat)))
        else:
            print("  %-7s %-8s -> · 观察池 (暂无催化剂标签)" % (m["code"], m["sec_name"]))
        if m.get("risk_flags"):
            print("            ⚠ 风险标记: %s" % ", ".join(m["risk_flags"]))
    print("="*70)

if __name__ == "__main__":
    main()
