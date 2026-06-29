"""
V13.4 Watchdog — Autonomous Monitoring & Alert System

Tracks consecutive failures for each pipeline step.
Triggers alerts when failure threshold is exceeded.
Automatically resets on successful execution.

Features:
1. Consecutive failure tracking per step
2. Auto-reset on success
3. Alert dispatch via GitHub Actions (PushPlus WeChat)
4. Integrated with Guardian for health checks
"""

import json
from pathlib import Path
from datetime import datetime, timedelta

STATE_DIR = Path(__file__).parent / "cloud_state"
WATCHDOG_STATE_FILE = STATE_DIR / "watchdog_state.json"
ALERT_THRESHOLD = 3  # Alert after 3 consecutive failures
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _load_watchdog_state() -> dict:
    """Load watchdog state from disk."""
    if WATCHDOG_STATE_FILE.exists():
        with open(WATCHDOG_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"steps": {}, "last_alerts": {}}


def _save_watchdog_state(state: dict):
    """Save watchdog state to disk."""
    with open(WATCHDOG_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def record_success(step: str):
    """Record a successful execution for a step. Resets consecutive failure count."""
    state = _load_watchdog_state()
    if step not in state["steps"]:
        state["steps"][step] = {"consecutive_failures": 0, "last_failure": None, "last_success": None}

    step_state = state["steps"][step]
    step_state["consecutive_failures"] = 0  # Reset on success
    step_state["last_success"] = datetime.now().isoformat()
    step_state["last_status"] = "ok"

    _save_watchdog_state(state)
    print(f"[WATCHDOG] ✅ {step} success recorded, failure count reset")


def record_failure(step: str, error: str):
    """Record a failed execution for a step. Increments consecutive failure count."""
    state = _load_watchdog_state()
    if step not in state["steps"]:
        state["steps"][step] = {"consecutive_failures": 0, "last_failure": None, "last_success": None}

    step_state = state["steps"][step]
    step_state["consecutive_failures"] = step_state.get("consecutive_failures", 0) + 1
    step_state["last_failure"] = datetime.now().isoformat()
    step_state["last_error"] = error[:200]  # Truncate error
    step_state["last_status"] = "failed"

    _save_watchdog_state(state)
    print(f"[WATCHDOG] ❌ {step} failure recorded (consecutive: {step_state['consecutive_failures']})")

    # Check if alert threshold reached
    if step_state["consecutive_failures"] >= ALERT_THRESHOLD:
        return True  # Signal that alert should be sent
    return False


def check_alerts() -> dict:
    """Check all steps for alert conditions. Returns alert info."""
    state = _load_watchdog_state()
    alerts = {
        "timestamp": datetime.now().isoformat(),
        "alerts": [],
        "all_ok": True,
    }

    for step, step_state in state.get("steps", {}).items():
        consecutive = step_state.get("consecutive_failures", 0)
        if consecutive >= ALERT_THRESHOLD:
            alert = {
                "step": step,
                "consecutive_failures": consecutive,
                "last_failure": step_state.get("last_failure"),
                "last_error": step_state.get("last_error"),
                "severity": "CRITICAL" if consecutive >= 5 else "WARNING",
            }
            alerts["alerts"].append(alert)
            alerts["all_ok"] = False

    return alerts


def send_alert(step: str, alert_info: dict) -> bool:
    """Send alert notification via PushPlus (WeChat)."""
    try:
        from cloud_notify import send_pushplus_message

        title = f"🚨 V13.4 系统告警: {step} 连续失败"
        content = f"""
# V13.4 云端系统告警

## 步骤: {step}
## 连续失败次数: {alert_info.get('consecutive_failures', '?')}
## 最后失败时间: {alert_info.get('last_failure', '?')}
## 最后错误: {alert_info.get('last_error', '?')}

## 建议操作:
1. 检查 GitHub Actions 日志
2. 检查网络连接
3. 检查数据源 API 状态
4. 如需手动干预，请查看 claw-v134 仓库

## 自动修复:
系统将自动重试，Guardian 会尝试自动修复。
        """.strip()

        send_pushplus_message(title, content, msg_type="markdown")
        print(f"[WATCHDOG] 📢 Alert sent for {step}")
        return True
    except Exception as e:
        print(f"[WATCHDOG] ❌ Failed to send alert: {e}")
        return False


def run_watchdog_check() -> dict:
    """Main watchdog check: called by Guardian or independently."""
    print("\n[WATCHDOG] Running watchdog check...")
    alerts = check_alerts()

    if alerts["all_ok"]:
        print("[WATCHDOG] ✅ All steps OK, no alerts needed")
        return {"status": "ok", "alerts_sent": 0}

    # Send alerts for each triggered alert
    alerts_sent = 0
    state = _load_watchdog_state()

    for alert in alerts["alerts"]:
        step = alert["step"]
        step_state = state["steps"].get(step, {})

        # Check if we already alerted recently (avoid spam)
        last_alert = state.get("last_alerts", {}).get(step)
        should_alert = True
        if last_alert:
            try:
                last_time = datetime.fromisoformat(last_alert)
                if datetime.now() - last_time < timedelta(hours=1):
                    should_alert = False  # Already alerted in last hour
                    print(f"[WATCHDOG] ⏭ {step} alert suppressed (sent within 1h)")
            except Exception:
                pass

        if should_alert:
            if send_alert(step, step_state):
                alerts_sent += 1
                # Record alert time
                state.setdefault("last_alerts", {})[step] = datetime.now().isoformat()
                _save_watchdog_state(state)

    print(f"[WATCHDOG] Complete: {alerts_sent} alerts sent")
    return {"status": "alerts_sent", "alerts_sent": alerts_sent, "triggered": len(alerts["alerts"])}
