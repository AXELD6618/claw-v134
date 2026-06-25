"""
V13.4 Local Thin Client
Runs on WorkBuddy local machine — pulls cloud results from GitHub, displays dashboard.
No heavy computation — just sync + display + alert.

Modes:
  --sync: Pull latest cloud results from GitHub repo
  --dashboard: Display current pipeline status
  --daemon: Watch mode — pull every 5 minutes, alert on new signals
  --status: Quick health check of cloud pipeline
"""
import os, sys, json, time, subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

CLOUD_OUTPUT = Path(__file__).parent / "cloud_outputs"
CLOUD_STATE = Path(__file__).parent / "cloud_state"


def git_pull() -> bool:
    """Pull latest from GitHub (thin client — just sync)."""
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "master"],
            cwd=Path(__file__).parent,
            capture_output=True, text=True, timeout=30
        )
        if "Already up to date" in result.stdout:
            return True
        if result.returncode == 0:
            print("[SYNC] Pulled latest cloud results")
            return True
        print(f"[SYNC] Pull failed: {result.stderr[:200]}")
        return False
    except Exception as e:
        print(f"[SYNC] Git error: {e}")
        return False


def read_cloud_file(name: str) -> Optional[Dict]:
    """Read a cloud output file."""
    path = CLOUD_OUTPUT / name
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def read_state(step: str) -> Optional[Dict]:
    """Read a pipeline step state."""
    path = CLOUD_STATE / f"step_{step}.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def show_dashboard():
    """Display current pipeline status dashboard."""
    print("\n" + "=" * 60)
    print("  🦅 毕方灵犀 V13.4 Cloud Dashboard")
    print("=" * 60)

    # Pipeline status
    steps_order = ["T0", "T1", "T3", "T4", "T5", "NIGHT", "BATTLE"]
    print(f"\n  Pipeline Status @ {datetime.now().strftime('%H:%M:%S')}")
    print("  " + "-" * 40)

    all_ok = True
    for step in steps_order:
        state = read_state(step)
        if state:
            ts = state.get("timestamp", "?")[:19]
            data = state.get("data", {})
            status = "✅"
            if "error" in str(data):
                status = "❌"
                all_ok = False
            print(f"  {status} {step}: {ts}")
        else:
            print(f"  ⬜ {step}: PENDING")
            all_ok = False

    # Holy Grail signals
    hg = read_cloud_file("holy_grail_signals.json")
    if hg:
        print(f"\n  ⚡ Holy Grail Signals ({hg.get('date', '?')})")
        print("  " + "-" * 40)
        signals = hg.get("signals", [])
        level = hg.get("degradation_level", "?")
        print(f"  Level: {level} | Signals: {len(signals)}")
        for i, s in enumerate(signals[:10]):
            code = s.get("code", "?")
            name = s.get("name", "?")
            score = s.get("hg_score", 0)
            if score < 0:
                print(f"  ⚠️  SYSTEM_DOWN — no data available")
                break
            pct = s.get("pct_chg", "?")
            print(f"  #{i+1} {code} {name} | Score:{score:.3f} | {pct}%")

    # Guardian health
    health = read_cloud_file("guardian_health.json")
    if health:
        print(f"\n  🛡️ System Health: {health.get('status', '?')}")
        print("  " + "-" * 40)
        print(f"  Completion: {health.get('completion_pct', 0)}%")
        print(f"  Network: {health.get('network', '?')}")
        print(f"  Disk Free: {health.get('disk_free_gb', '?')} GB")

    # Battle plan
    battle = read_state("BATTLE")
    if battle:
        data = battle.get("data", {})
        watchlist = data.get("watchlist", [])
        if watchlist:
            print(f"\n  ⚔️ Tomorrow Watchlist ({len(watchlist)} stocks)")
            print("  " + "-" * 40)
            for s in watchlist[:8]:
                print(f"  {s.get('code')} {s.get('name')} | Score:{s.get('score',0):.3f}")

    print("\n" + "=" * 60)

    if all_ok:
        print("  ✅ All pipeline steps completed")
    else:
        print("  ⚠️  Some steps pending — check cloud_outputs/")

    print("=" * 60 + "\n")


def check_health() -> Dict:
    """Quick health check."""
    health = {
        "timestamp": datetime.now().isoformat(),
        "pipeline_complete": False,
        "holy_grail_available": False,
        "steps_completed": 0,
        "steps_total": 7,
    }

    for step in ["T0", "T1", "T3", "T4", "T5", "NIGHT", "BATTLE"]:
        state = read_state(step)
        if state:
            health["steps_completed"] += 1

    if read_cloud_file("holy_grail_signals.json"):
        health["holy_grail_available"] = True

    health["pipeline_complete"] = health["steps_completed"] == health["steps_total"]
    return health


def daemon_mode(interval: int = 300):
    """Watch mode — pull every N seconds, alert on new signals."""
    print(f"[DAEMON] Starting thin client daemon (interval={interval}s)")
    last_signals_checksum = ""

    while True:
        try:
            git_pull()
            hg = read_cloud_file("holy_grail_signals.json")

            if hg:
                checksum = hg.get("checksum", "")
                if checksum != last_signals_checksum and checksum:
                    last_signals_checksum = checksum
                    signals = hg.get("signals", [])
                    valid = [s for s in signals if s.get("hg_score", 0) > 0]
                    if valid:
                        print(f"\n⚡ NEW HOLY GRAIL SIGNALS! {len(valid)} stocks @ {hg.get('timestamp','?')[:19]}")
                        for s in valid[:5]:
                            print(f"  {s.get('code')} {s.get('name')} Score:{s.get('hg_score',0):.3f}")

            # Quick status
            health = check_health()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Steps: {health['steps_completed']}/{health['steps_total']} | HG: {'✅' if health['holy_grail_available'] else '⏳'}")

        except Exception as e:
            print(f"[DAEMON ERROR] {e}")

        time.sleep(interval)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="V13.4 Local Thin Client")
    ap.add_argument("--mode", choices=["sync", "dashboard", "daemon", "status"], default="dashboard")
    ap.add_argument("--interval", type=int, default=300, help="Daemon pull interval (seconds)")
    args = ap.parse_args()

    if args.mode == "sync":
        print("[SYNC] Pulling from GitHub...")
        git_pull()
        show_dashboard()

    elif args.mode == "dashboard":
        show_dashboard()

    elif args.mode == "daemon":
        daemon_mode(args.interval)

    elif args.mode == "status":
        health = check_health()
        print(json.dumps(health, indent=2, ensure_ascii=False))
