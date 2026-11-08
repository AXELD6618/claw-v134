#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · Layer C — TDX cyq 过滤 (CYS / SCR)
==================================================================
输入: backtest_cache/spring_soe_layerAB.json  (73 只 中小盘安静国资)
引擎: research/cyq_engine.py  (本地 cyq: CYS=成本均线偏离, SCR=滚动筹码分布离散度)
      - 输入为 TDX 同源前复权日线 + 流通股本, 与 TDX cyq 同口径
      - CYS 已与 live TDX 读数校准 (601158: local 0.61 vs live 0.42, 同处 [-5,+5])
      - SCR 用历史分位判定 "偏低带", 分位方向与 live TDX 一致 (601158 二者均判 SCR 高位=FAIL)

过滤条件:
  CYS_latest  ∈ [-5, +5]          # 价格压在 13 日成本均线附近, 未拉伸
  SCR_pctl    ≤ SCR_LOW_BAND      # 当前筹码离散度处历史低位 = 弹簧压到底

输出: backtest_cache/spring_soe_layerC.json  (最终弹簧池, 按 SCR 分位升序)
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cyq_engine import analyze

OUT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"
CYS_LO, CYS_HI = -5.0, 5.0
SCR_LOW_BAND = 0.20      # 历史偏低带 = 后 20% 分位
WINDOW = 250

def main():
    ab = json.load(open(os.path.join(OUT, "spring_soe_layerAB.json"), encoding="utf-8"))
    results = []
    skipped = []
    for rec in ab:
        code = rec["code"]
        try:
            r = analyze(code, window=WINDOW)
        except Exception as e:
            skipped.append((code, "analyze_err:%s" % e)); continue
        if r is None:
            skipped.append((code, "no_data")); continue
        s = r["scr_series"]
        svals = [x for x in s if x is not None]
        if len(svals) < 60:
            skipped.append((code, "short_hist:%d" % len(svals))); continue
        cur_scr = s[-1]
        pctl = sum(1 for x in svals if x <= cur_scr) / len(svals)
        cys = r["cys_latest"]
        rec_out = dict(rec)
        rec_out["cys"] = round(cys, 3) if cys is not None else None
        rec_out["scr"] = round(cur_scr, 3)
        rec_out["scr_pctl"] = round(pctl, 3)
        rec_out["scr_min"] = round(min(svals), 3)
        rec_out["scr_max"] = round(max(svals), 3)
        # 判定
        cys_ok = (cys is not None) and (CYS_LO <= cys <= CYS_HI)
        scr_ok = pctl <= SCR_LOW_BAND
        rec_out["cys_ok"] = cys_ok
        rec_out["scr_ok"] = scr_ok
        rec_out["pass"] = cys_ok and scr_ok
        results.append(rec_out)

    passed = [x for x in results if x["pass"]]
    passed.sort(key=lambda x: x["scr_pctl"])
    print("LayerAB 输入: %d" % len(ab))
    print("成功计算: %d   跳过: %d" % (len(results), len(skipped)))
    print("CYS 通过: %d   SCR 偏低带通过: %d" % (
        sum(1 for x in results if x["cys_ok"]), sum(1 for x in results if x["scr_ok"])))
    print("★ 最终弹簧池 (CYS∩SCR): %d 只" % len(passed))

    json.dump(results, open(os.path.join(OUT, "spring_soe_layerC.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("\n=== 最终弹簧池 (前 20, 按 SCR 分位升序) ===")
    for m in passed[:20]:
        la = m["layerA"]
        print("%s %s  换手%.3f%%  CYS=%s  SCR=%s 分位%.2f  市值%.1f亿  %s" % (
            m["code"], m["sec_name"], la["turnover"], m["cys"], m["scr"], m["scr_pctl"],
            (m["mktcap"] or 0)/1e8, m["controller"]))
    if skipped:
        print("\n跳过:", skipped)

if __name__ == "__main__":
    main()
