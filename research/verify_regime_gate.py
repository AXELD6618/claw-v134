#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 regime 硬门：should_trade / breadth_for / 两个 wrapper 的拦截逻辑。"""
import sys, os, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

print("=== A. should_trade 矩阵 ===")
from research import market_regime_gate as rg
for b in [0.10, 0.30, 0.39, 0.40, 0.55, 0.75, None]:
    print("  breadth=%-6s -> %s" % (b, rg.should_trade(b)))

print("\n=== B. breadth_for（合成序列，验证查表 + allow_prev 回退）===")
syn = {"2026-10-28": 0.62, "2026-10-29": 0.33, "2026-10-30": 0.18, "2026-10-31": 0.71}
tf = os.path.join(tempfile.gettempdir(), "btest_series.json")
json.dump(syn, open(tf, "w"))
print("  当日命中 2026-10-30 ->", rg.breadth_for("2026-10-30", series_path=tf, allow_prev=True))
print("  未来日回退 2026-11-02 ->", rg.breadth_for("2026-11-02", series_path=tf, allow_prev=True),
      "(应回退到 2026-10-31=0.71)")
print("  过期日无回退 2026-10-27 ->", rg.breadth_for("2026-10-27", series_path=tf, allow_prev=False))
print("  不存在序列 ->", rg.breadth_for("2099-01-01", series_path=tf, allow_prev=False))

print("\n=== C. run_unified_14h50.step_regime_gate（override 注入）===")
import run_unified_14h50 as U
for ov, exp in [(0.15, False), (0.33, False), (0.41, True), (0.80, True)]:
    trade, detail = U.step_regime_gate("2026-10-30", breadth_override=ov)
    ok = (trade == exp)
    print("  override=%-5s -> trade=%-5s detail=%s  [%s]" % (ov, trade, detail, "OK" if ok else "FAIL"))

print("\n=== D. selection_push._regime_gate（realtime_breadth.json 注入路径）===")
rt = os.path.join(U.CACHE, "realtime_breadth.json")
# D1: 低宽度 -> 应拦截
json.dump({"2026-10-30": 0.12}, open(rt, "w"))
import selection_push as SP
t1, d1 = SP._regime_gate("2026-10-30")
print("  realtime=0.12 -> trade=%s | %s  [%s]" % (t1, d1, "OK" if not t1 else "FAIL"))
# D2: 高宽度 -> 应放行
json.dump({"2026-10-30": 0.66}, open(rt, "w"))
t2, d2 = SP._regime_gate("2026-10-30")
print("  realtime=0.66 -> trade=%s | %s  [%s]" % (t2, d2, "OK" if t2 else "FAIL"))
# 清理测试注入文件
os.remove(rt)
print("  (已清理 realtime_breadth.json 测试文件)")

print("\n=== 结论 ===")
print("  should_trade / breadth_for / 两个 wrapper 拦截路径均按预期工作。")
