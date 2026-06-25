"""
V13.4 Cloud Pipeline — Unified Entry Point for GitHub Actions
Called by GitHub Actions workflows with --step argument.

Steps:
  T0 (10:30 CST): Full market screener → candidate pool
  T1 (11:30 CST): Midday refresh → update candidates
  T3 (14:00 CST): Deep dive → narrow to 50-80
  T4 (14:15 CST): Final confirmation → 15-30
  T5 (14:30 CST): Holy Grail execution
  NIGHT (20:00 CST): Night analysis
  BATTLE (22:00 CST): Battle plan
  GUARDIAN: Health check

Architecture:
  Each step reads state from previous step, processes, writes output.
  Each step is independent and can run in isolation.
  All output goes to cloud_outputs/ directory.
"""
import os, sys, json, time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

CLOUD_ROOT = Path(__file__).parent
OUTPUT_DIR = CLOUD_ROOT / "cloud_outputs"
CACHE_DIR = CLOUD_ROOT / "cloud_cache"
STATE_DIR = CLOUD_ROOT / "cloud_state"

for d in [OUTPUT_DIR, CACHE_DIR, STATE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


def get_today():
    return datetime.now().strftime("%Y%m%d")


def write_step_result(step: str, data: Dict):
    """Write step result to state file."""
    path = STATE_DIR / f"step_{step}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"step": step, "timestamp": datetime.now().isoformat(), "data": data}, f, ensure_ascii=False)
    print(f"[STATE] {step} → {path}")


def read_step_result(step: str) -> Dict:
    """Read previous step result."""
    path = STATE_DIR / f"step_{step}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def run_step_T0():
    """10:30 CST — Full market screener, establish candidate pool."""
    print("[T0] Full market screener...")
    try:
        from cloud_data_fetcher import fetch_full_pipeline_data, export_candidate_pool
        data = fetch_full_pipeline_data()
        pool = export_candidate_pool(data, min_score=4, top_n=150)
        result = {
            "total_stocks": data["snapshot"]["total_count"],
            "candidates": len(pool),
            "indices": len(data["snapshot"].get("indices", [])),
            "top_sectors": [s.get("name", "?") for s in data.get("sectors", {}).get("sectors", [])[:5]],
        }
        write_step_result("T0", result)
        return result
    except Exception as e:
        print(f"[T0 ERROR] {e}")
        write_step_result("T0", {"error": str(e)})
        return {"error": str(e)}


def run_step_T1():
    """11:30 CST — Midday refresh, update candidates."""
    print("[T1] Midday refresh...")
    try:
        from cloud_data_fetcher import fetch_market_snapshot, export_candidate_pool, compute_stock_scores
        snapshot = fetch_market_snapshot()
        stocks = compute_stock_scores(snapshot.get("stocks", []))
        data = {"snapshot": snapshot, "candidates": [s for s in stocks if s.get("cloud_score", 0) >= 4]}
        pool = export_candidate_pool(data, min_score=4, top_n=120)
        result = {
            "total_stocks": snapshot["total_count"],
            "new_candidates": len(pool),
            "top_movers": [{"code": s.get("code"), "name": s.get("name"), "pct": s.get("pct_chg")} for s in pool[:5]],
        }
        write_step_result("T1", result)
        return result
    except Exception as e:
        print(f"[T1 ERROR] {e}")
        write_step_result("T1", {"error": str(e)})
        return {"error": str(e)}


def run_step_T3():
    """14:00 CST — Deep dive, narrow to 50-80 core candidates."""
    print("[T3] Deep dive...")
    try:
        from cloud_data_fetcher import fetch_market_snapshot, export_candidate_pool, compute_stock_scores
        snapshot = fetch_market_snapshot()
        stocks = compute_stock_scores(snapshot.get("stocks", []))
        # Tighter filter: higher score, more active
        candidates = [s for s in stocks if s.get("cloud_score", 0) >= 5]
        pool = export_candidate_pool({"snapshot": snapshot, "candidates": candidates}, min_score=5, top_n=80)
        result = {
            "candidates": len(pool),
            "avg_pct": round(sum(s.get("pct_chg", 0) for s in pool) / max(len(pool), 1), 2),
            "top5": [{"code": s.get("code"), "name": s.get("name"), "score": s.get("cloud_score"), "pct": s.get("pct_chg")} for s in pool[:5]],
        }
        write_step_result("T3", result)
        return result
    except Exception as e:
        print(f"[T3 ERROR] {e}")
        write_step_result("T3", {"error": str(e)})
        return {"error": str(e)}


def run_step_T4():
    """14:15 CST — Final confirmation, 15-30 holy grail candidates."""
    print("[T4] Final confirmation...")
    try:
        from cloud_data_fetcher import fetch_market_snapshot, export_candidate_pool, compute_stock_scores
        snapshot = fetch_market_snapshot()
        stocks = compute_stock_scores(snapshot.get("stocks", []))
        candidates = [s for s in stocks if s.get("cloud_score", 0) >= 6]
        pool = export_candidate_pool({"snapshot": snapshot, "candidates": candidates}, min_score=6, top_n=30)
        result = {
            "final_candidates": len(pool),
            "list": [{"code": s.get("code"), "name": s.get("name"), "score": s.get("cloud_score"), "pct": s.get("pct_chg"), "turnover": s.get("turnover")} for s in pool],
        }
        write_step_result("T4", result)
        return result
    except Exception as e:
        print(f"[T4 ERROR] {e}")
        write_step_result("T4", {"error": str(e)})
        return {"error": str(e)}


def run_step_T5():
    """14:30 CST — HOLY GRAIL execution."""
    print("[T5] ⚡ HOLY GRAIL ⚡")
    try:
        from cloud_holy_grail import HolyGrailEngine
        engine = HolyGrailEngine()
        result = engine.execute()
        write_step_result("T5", result)
        return result
    except Exception as e:
        print(f"[T5 ERROR] {e}")
        write_step_result("T5", {"error": str(e)})
        return {"error": str(e)}


def run_step_NIGHT():
    """20:00 CST — Night analysis: review, backtest, calibrate."""
    print("[NIGHT] Night analysis...")
    results = {
        "timestamp": datetime.now().isoformat(),
        "steps_ran": [],
    }

    # Review today's T5 results
    t5 = read_step_result("T5")
    if t5:
        results["t5_result"] = t5.get("data", {})

    # Summarize pipeline
    for step in ["T0", "T1", "T3", "T4"]:
        s = read_step_result(step)
        if s:
            results["steps_ran"].append(step)

    # Write night summary
    night_path = OUTPUT_DIR / f"night_summary_{get_today()}.json"
    with open(night_path, "w") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    write_step_result("NIGHT", {"steps_completed": len(results["steps_ran"]), "summary_file": str(night_path)})
    return results


def run_step_BATTLE():
    """22:00 CST — Tomorrow battle plan."""
    print("[BATTLE] Battle plan...")
    plan = {
        "timestamp": datetime.now().isoformat(),
        "date": get_today(),
        "tomorrow_outlook": {},
        "watchlist": [],
        "war_chest_allocation": {},
    }

    # Load holy grail signals
    signals_file = OUTPUT_DIR / "holy_grail_signals.json"
    if signals_file.exists():
        with open(signals_file, "r") as f:
            hg = json.load(f)
        signals = hg.get("signals", [])
        plan["holy_grail_count"] = len(signals)
        plan["watchlist"] = [{"code": s.get("code"), "name": s.get("name"), "score": s.get("hg_score", 0)} for s in signals[:10] if s.get("hg_score", 0) > 0]

    # Summary of all steps
    for step in ["T0", "T1", "T3", "T4", "T5", "NIGHT"]:
        s = read_step_result(step)
        if s:
            plan[f"{step}_status"] = "completed"

    battle_path = OUTPUT_DIR / f"battle_plan_{get_today()}.json"
    with open(battle_path, "w") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    write_step_result("BATTLE", {"plan_file": str(battle_path), "watchlist_count": len(plan["watchlist"])})
    return plan


def run_step_GUARDIAN():
    """Health guardian — check pipeline health."""
    print("[GUARDIAN] Health check...")
    health = {
        "timestamp": datetime.now().isoformat(),
        "status": "OK",
        "checks": {},
    }

    # Check each step
    steps = ["T0", "T1", "T3", "T4", "T5", "NIGHT", "BATTLE"]
    for step in steps:
        state = read_step_result(step)
        health["checks"][step] = "completed" if state else "pending"

    # Check disk
    import shutil
    disk = shutil.disk_usage(CLOUD_ROOT)
    health["disk_free_gb"] = round(disk.free / (1024**3), 1)

    # Check connectivity
    try:
        import urllib.request
        urllib.request.urlopen("https://www.baidu.com", timeout=5)
        health["network"] = "OK"
    except Exception:
        health["network"] = "DOWN"

    # Determine overall status
    completed = sum(1 for v in health["checks"].values() if v == "completed")
    health["completion_pct"] = round(completed / len(steps) * 100)

    if health["completion_pct"] < 50:
        health["status"] = "DEGRADED"
    if health["completion_pct"] < 20:
        health["status"] = "CRITICAL"

    health_path = OUTPUT_DIR / "guardian_health.json"
    with open(health_path, "w") as f:
        json.dump(health, f, ensure_ascii=False, indent=2)

    write_step_result("GUARDIAN", health)
    print(f"[GUARDIAN] Status: {health['status']} | Completion: {health['completion_pct']}% | Disk: {health['disk_free_gb']}GB")
    return health


# === Step Dispatch ===
STEP_HANDLERS = {
    "T0": run_step_T0,
    "T1": run_step_T1,
    "T3": run_step_T3,
    "T4": run_step_T4,
    "T5": run_step_T5,
    "NIGHT": run_step_NIGHT,
    "BATTLE": run_step_BATTLE,
    "GUARDIAN": run_step_GUARDIAN,
}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="V13.4 Cloud Pipeline")
    ap.add_argument("--step", required=True, choices=list(STEP_HANDLERS.keys()), help="Pipeline step to execute")
    ap.add_argument("--full-day", action="store_true", help="Run full trading day pipeline (T0→T1→T3→T4→T5)")
    args = ap.parse_args()

    t_start = time.time()
    print(f"\n{'='*60}")
    print(f"  V13.4 CLOUD PIPELINE — Step={args.step} @ {datetime.now()}")
    print(f"{'='*60}\n")

    if args.full_day:
        steps = ["T0", "T1", "T3", "T4", "T5"]
        for step in steps:
            print(f"\n--- {step} ---")
            handler = STEP_HANDLERS.get(step)
            if handler:
                result = handler()
                print(f"  {step}: {json.dumps(result, ensure_ascii=False, default=str)[:200]}")
    else:
        handler = STEP_HANDLERS.get(args.step)
        if handler:
            result = handler()
            print(f"\n  Result: {json.dumps(result, ensure_ascii=False, default=str)[:300]}")
        else:
            print(f"[ERROR] Unknown step: {args.step}")
            sys.exit(1)

    print(f"\n[DONE] Elapsed: {time.time()-t_start:.1f}s")
