#!/usr/bin/env python3
"""
V13.4 Self-Healing Dispatch Script
====================================
Uses GitHub API directly (not gh CLI) to dispatch workflows.
More reliable in GitHub Actions environment.

Usage: python self_heal_dispatch.py
"""

import os, sys, json, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

# GitHub API config
REPO = "AXELD6618/claw-v134"
API_BASE = f"https://api.github.com/repos/{REPO}/actions/workflows"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))

# Step to workflow file mapping
STEP_WF_MAP = {
    "T0": "v134-t0-screener.yml",
    "T1": "v134-t1-midday.yml",
    "T3": "v134-t3-deep.yml",
    "T4": "v134-t4-final.yml",
    "T5": "v134-t5-holy-grail.yml",
    "NIGHT": "v134-night-analysis.yml",
    "BATTLE": "v134-battle-plan.yml",
}

def log(msg, level="INFO"):
    """Output log message."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}", flush=True)

def dispatch_workflow(workflow_file, ref="master"):
    """Dispatch a workflow using GitHub API directly."""
    url = f"{API_BASE}/{workflow_file}/dispatches"
    
    data = json.dumps({"ref": ref}).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            if status in (200, 201, 204):
                log(f"✅ Dispatched {workflow_file}", "OK")
                return True
            else:
                body = resp.read().decode("utf-8")
                log(f"❌ Failed to dispatch {workflow_file}: HTTP {status} - {body[:200]}", "ERROR")
                return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        log(f"❌ HTTP Error dispatching {workflow_file}: {e.code} - {body[:200]}", "ERROR")
        return False
    except Exception as e:
        log(f"❌ Error dispatching {workflow_file}: {e}", "ERROR")
        return False

def main():
    log("=== V13.4 Self-Healing Dispatch ===")
    
    # Check if GitHub token is available
    if not GITHUB_TOKEN:
        log("ERROR: No GITHUB_TOKEN available. Set GITHUB_TOKEN env.", "ERROR")
        sys.exit(1)
    
    # Read dispatch queue
    queue_file = Path("cloud_state/guardian_dispatch_queue.json")
    if not queue_file.exists():
        log("✅ No dispatch queue found — all steps OK")
        sys.exit(0)
    
    try:
        with open(queue_file, "r", encoding="utf-8") as f:
            queue = json.load(f)
    except Exception as e:
        log(f"ERROR: Failed to read dispatch queue: {e}", "ERROR")
        sys.exit(1)
    
    steps_to_dispatch = queue.get("steps_to_dispatch", [])
    if not steps_to_dispatch:
        log("✅ No steps to dispatch")
        sys.exit(0)
    
    log(f"Found {len(steps_to_dispatch)} steps to dispatch: {steps_to_dispatch}")
    
    # Dispatch each step
    success_count = 0
    for step in steps_to_dispatch:
        wf_file = STEP_WF_MAP.get(step)
        if not wf_file:
            log(f"WARN: No workflow file mapping for step {step}", "WARN")
            continue
        
        log(f"Dispatching {step} ({wf_file})...")
        if dispatch_workflow(wf_file):
            success_count += 1
        
        # Small delay between dispatches
        import time
        time.sleep(3)
    
    log(f"=== Dispatch Summary ===")
    log(f"Total: {len(steps_to_dispatch)}, Success: {success_count}, Failed: {len(steps_to_dispatch) - success_count}")
    
    # Write result
    result = {
        "timestamp": datetime.now().isoformat(),
        "steps_dispatched": steps_to_dispatch,
        "success_count": success_count,
        "failed_count": len(steps_to_dispatch) - success_count,
    }
    
    result_file = Path("cloud_outputs/dispatch_result.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    log(f"Result written to {result_file}")
    
    # Exit with error if any dispatch failed
    sys.exit(0 if success_count == len(steps_to_dispatch) else 1)

if __name__ == "__main__":
    main()
