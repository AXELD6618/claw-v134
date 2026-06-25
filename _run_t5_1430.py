# -*- coding: utf-8 -*-
"""
V13.4 T5 终极圣杯 14:30 全市场扫描引擎 (Skill: v134-pipeline-t5)
执行4级降级: V13.4 → V13.2_1430_Deploy → V13.2_OrchestratorV2 → V13.0_Orchestrator → FALLBACK
"""
import json
import os
import sys
import time
from pathlib import Path

CWD = Path("E:/WorkBuddy_dot_workbuddy/Claw")
CACHE = CWD / "data" / "fullmarket_cache"
OUTPUTS = CWD / "outputs"

# === 第1步: 验证缓存 ===
print("=" * 70)
print("【第1步】验证三路Screener + 指数缓存")
print("=" * 70)

screener_files = [
    ("跌幅排序_排除ST", CACHE / "screener_t5_falling_1430.json"),
    ("放量下跌_排除ST", CACHE / "screener_t5_volume_1430.json"),
    ("换手率排序_跌幅>1%", CACHE / "screener_t5_turnover_1430.json"),
]
for name, fp in screener_files:
    if fp.exists():
        sz = fp.stat().st_size
        print(f"  [OK] {name}: {fp.name} ({sz:,}B)")

if (CACHE / "index_1430.json").exists():
    idx = json.load(open(CACHE / "index_1430.json", "r", encoding="utf-8"))
    print(f"  [OK] 指数: 上证{idx['000001']['now']:.2f}({idx['000001']['chg_pct']:+.2f}%) "
          f"创业板{idx['399006']['now']:.2f}({idx['399006']['chg_pct']:+.2f}%)")

# === 第2步: 合并screener为统一List[Dict] (供V13.4 ingest) ===
print()
print("=" * 70)
print("【第2步】合并三路Screener → List[Dict]")
print("=" * 70)

merged = []
for _, fp in screener_files:
    if fp.exists():
        d = json.load(open(fp, "r", encoding="utf-8"))
        for r in d.get("data", []):
            merged.append({
                "code": r.get("sec_code", r.get("code", "")),
                "name": r.get("sec_name", r.get("name", "")),
                "price": float(r.get("now_price", r.get("price", 0)) or 0),
                "change_pct": float(r.get("chg", r.get("change_pct", 0)) or 0),
                "turnover_rate": float(r.get("hsl", r.get("turnover_rate", 0)) or 0),
                "amplitude": float(r.get("amplitude", 0) or 0),
            })

# 去重
seen = set()
dedup = []
for r in merged:
    if r["code"] in seen:
        continue
    seen.add(r["code"])
    dedup.append(r)

with open(CACHE / "screener_t5_1430.json", "w", encoding="utf-8") as f:
    json.dump({"data": dedup, "merged_count": len(dedup), "period": "14:30", "date": "20260625"},
              f, ensure_ascii=False, indent=2)
print(f"  [OK] 合并去重: {len(dedup)}只 → data/fullmarket_cache/screener_t5_1430.json")

# === 第3步: 全市场扫描 (4级降级) ===
print()
print("=" * 70)
print("【第3步】V13.4 全市场扫描引擎 (4级降级)")
print("=" * 70)

sys.path.insert(0, str(CWD))
result = None
method_used = None


def try_v134():
    """方案1: V13.4 FullMarketMonitor"""
    global result, method_used
    try:
        from V13_4_FullMarketMonitor import get_global_scanner, save_scanner_state
        scanner = get_global_scanner()
        mkt_idx = json.load(open(CACHE / "index_1430.json", "r", encoding="utf-8"))
        dashboard_path, summary = scanner.run_full_scan(
            period="14:30",
            screener_results=dedup,  # List[Dict]
            market_index=mkt_idx,
        )
        save_scanner_state(scanner)
        holy_grails = scanner.detect_holy_grail_signals()
        result = (dashboard_path, summary, holy_grails)
        method_used = "V13.4_FullMarketMonitor"
        print(f"  ✅ {method_used} 成功")
        return True
    except Exception as e:
        print(f"  ❌ V13.4: {type(e).__name__}: {e}")
        return False


def try_v132_1430():
    """方案2: V13.2 1430_Deploy"""
    global result, method_used
    try:
        sys.path.insert(0, str(CWD))
        os.chdir(CWD)
        # V13.2_1430_Deploy 是 main() 入口, 改用批处理
        import subprocess
        out = subprocess.run(
            [sys.executable, "V13_2_1430_Deploy.py", "--synthetic"],
            capture_output=True, text=True, timeout=60, cwd=str(CWD)
        )
        if out.returncode == 0:
            method_used = "V13.2_1430_Deploy"
            result = (None, {"synthetic_run": True, "stdout": out.stdout[-500:]}, [])
            print(f"  ✅ {method_used} 成功 (synthetic)")
            return True
        return False
    except Exception as e:
        print(f"  ❌ V13.2: {type(e).__name__}: {e}")
        return False


def try_v132_orch():
    """方案3: V13.2 OrchestratorV2"""
    global result, method_used
    try:
        os.chdir(CWD)
        import subprocess
        out = subprocess.run(
            [sys.executable, "V13_2_OrchestratorV2.py"],
            capture_output=True, text=True, timeout=60, cwd=str(CWD)
        )
        if out.returncode == 0:
            method_used = "V13.2_OrchestratorV2"
            result = (None, {"stdout": out.stdout[-500:]}, [])
            print(f"  ✅ {method_used} 成功")
            return True
        return False
    except Exception as e:
        print(f"  ❌ V13.2 Orch: {type(e).__name__}: {e}")
        return False


def try_v130_orch():
    """方案4: V13.0 Orchestrator"""
    global result, method_used
    try:
        os.chdir(CWD)
        import subprocess
        out = subprocess.run(
            [sys.executable, "V13_0_Orchestrator.py"],
            capture_output=True, text=True, timeout=60, cwd=str(CWD)
        )
        if out.returncode == 0:
            method_used = "V13.0_Orchestrator"
            result = (None, {"stdout": out.stdout[-500:]}, [])
            print(f"  ✅ {method_used} 成功")
            return True
        return False
    except Exception as e:
        print(f"  ❌ V13.0: {type(e).__name__}: {e}")
        return False


for fn in [try_v134, try_v132_1430, try_v132_orch, try_v130_orch]:
    if fn():
        break

# === 第4步: 终极降级 - TDX MCP手工选股 ===
if not result:
    print()
    print("  [FALLBACK] 所有Python模块未就绪, 使用TDX MCP数据手工构造结果")
    method_used = "FALLBACK_TDX_MCP"
    cands = sorted(dedup, key=lambda x: x["change_pct"])[:20]
    result = (None, {"total_candidates": len(dedup), "source": "tdx_screener_merge"}, cands)

# === 第5步: 输出汇总 ===
print()
print("=" * 70)
print(f"【第4步】T5 14:30 圣杯信号汇总 — {method_used}")
print("=" * 70)

dashboard_path, summary, holy_grails = result
if method_used == "V13.4_FullMarketMonitor":
    print(f"  全市场扫描: {summary.get('total_scanned', 0)}只 (排除{summary.get('excluded', 0)})")
    print(f"  三档: A={summary.get('tier_a', 0)} B={summary.get('tier_b', 0)} C={summary.get('tier_c', 0)}")
    print(f"  圣杯: {summary.get('holy_grail_count', 0)}只")
    print(f"  仪表盘: {dashboard_path}")
    if holy_grails:
        print(f"\n  ⚡ 圣杯TOP10:")
        for hg in holy_grails[:10]:
            print(f"    [{hg.tier}档] {hg.code} {hg.name} v132={hg.v132_score:.4f} 跌幅={hg.decline_pct:.2f}%")
elif method_used == "FALLBACK_TDX_MCP":
    print(f"  候选池(去重): {summary.get('total_candidates', 0)}只")
    for c in holy_grails[:20]:
        print(f"    {c['code']} {c['name']:<10} 价:{c['price']:<8} 跌幅:{c['change_pct']:.2f}%")

print()
print("=" * 70)
print("✅ T5 14:30 终极圣杯执行完毕")
print("=" * 70)
