"""
V13.4 Cloud Monitor — Real-time Trading Hours Daemon
=====================================================
Runs on VPS (Docker) or locally. During A-share trading hours:
  - Fetches market data every 60 seconds
  - Runs screening pipeline at key timestamps (T0-T5)
  - Pushes signals via PushPlus
  - Writes status to cloud_state/ for dashboard

Outside trading hours: sleeps to save resources.

Trading hours (CST = UTC+8):
  Pre-market:  09:15 - 09:30 (集合竞价)
  Morning:     09:30 - 11:30
  Afternoon:   13:00 - 15:00
  Key screens: 10:30(T0), 11:30(T1), 14:00(T3), 14:15(T4), 14:30(T5)
"""
import os, sys, json, time, signal, subprocess, logging
from datetime import datetime, timedelta
from pathlib import Path

# === Config ===
CLOUD_ROOT = Path(__file__).parent
LOG_DIR = CLOUD_ROOT / "logs"
STATE_DIR = CLOUD_ROOT / "cloud_state"
LOG_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"monitor_{datetime.now().strftime('%Y%m%d')}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("V134Monitor")

# Trading schedule (CST hours)
SCREENING_SCHEDULE = {
    "10:30": ("T0", "全市场筛选"),
    "11:30": ("T1", "午盘刷新"),
    "14:00": ("T3", "深度分析"),
    "14:15": ("T4", "最终确认"),
    "14:30": ("T5", "圣杯选股"),
    "20:00": ("NIGHT", "夜间分析"),
    "22:00": ("BATTLE", "作战计划"),
}

# Data refresh interval (seconds) during trading hours
DATA_REFRESH_INTERVAL = 60

# === Trading day check ===

# Simplified holiday list (update annually)
HOLIDAYS_2026 = {
    # New Year
    "2026-01-01", "2026-01-02",
    # Spring Festival (approximate)
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20",
    "2026-02-23", "2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27",
    # Qingming
    "2026-04-06", "2026-04-07", "2026-04-08",
    # Labour Day
    "2026-05-01", "2026-05-04", "2026-05-05",
    # Dragon Boat
    "2026-06-19", "2026-06-22",
    # Mid-Autumn + National Day
    "2026-09-25", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06",
    "2026-10-07", "2026-10-08",
}


def is_trading_day() -> bool:
    """Check if today is a trading day (Mon-Fri, not holiday)."""
    now = datetime.now()
    # Weekend check
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    # Holiday check
    date_str = now.strftime("%Y-%m-%d")
    if date_str in HOLIDAYS_2026:
        return False
    return True


def is_trading_hours() -> bool:
    """Check if current time is within trading hours."""
    if not is_trading_day():
        return False
    now = datetime.now()
    hour_min = now.hour * 100 + now.minute
    # Morning: 09:15 - 11:30
    if 915 <= hour_min <= 1130:
        return True
    # Afternoon: 13:00 - 15:00
    if 1300 <= hour_min <= 1500:
        return True
    return False


def get_now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


# === Screening execution ===

def run_screening_step(step: str, name: str):
    """Run a pipeline screening step."""
    log.info(f"{'='*50}")
    log.info(f"  Running {step}: {name} @ {get_now_str()}")
    log.info(f"{'='*50}")

    try:
        # Run pipeline step
        result = subprocess.run(
            [sys.executable, "cloud_pipeline.py", "--step", step],
            capture_output=True, text=True, timeout=300,
            cwd=str(CLOUD_ROOT),
        )
        log.info(f"[{step}] Pipeline exit code: {result.returncode}")
        if result.stdout:
            log.info(f"[{step}] Output: {result.stdout[-500:]}")
        if result.stderr:
            log.warning(f"[{step}] Stderr: {result.stderr[-500:]}")

        # Run notification
        notify_result = subprocess.run(
            [sys.executable, "cloud_notify.py", "--step", step],
            capture_output=True, text=True, timeout=60,
            cwd=str(CLOUD_ROOT),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        log.info(f"[{step}] Notify exit code: {notify_result.returncode}")
        if notify_result.stdout:
            log.info(f"[{step}] Notify: {notify_result.stdout[-300:]}")

    except subprocess.TimeoutExpired:
        log.error(f"[{step}] Timeout!")
    except Exception as e:
        log.error(f"[{step}] Error: {e}")


def run_guardian():
    """Run health check."""
    try:
        subprocess.run(
            [sys.executable, "cloud_pipeline.py", "--step", "GUARDIAN"],
            capture_output=True, text=True, timeout=60,
            cwd=str(CLOUD_ROOT),
        )
        log.info("[GUARDIAN] Health check done")
    except Exception as e:
        log.error(f"[GUARDIAN] Error: {e}")


def update_status():
    """Write current monitor status to state file."""
    status = {
        "timestamp": datetime.now().isoformat(),
        "trading_day": is_trading_day(),
        "trading_hours": is_trading_hours(),
        "container_mode": os.environ.get("CLOUD_MODE", "local"),
        "pid": os.getpid(),
    }
    status_file = STATE_DIR / "monitor_status.json"
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


# === Main loop ===

def main():
    log.info("=" * 60)
    log.info("  V13.4 Cloud Monitor — Started")
    log.info(f"  PID: {os.getpid()} | Mode: {os.environ.get('CLOUD_MODE', 'local')}")
    log.info(f"  Trading day: {is_trading_day()} | Trading hours: {is_trading_hours()}")
    log.info("=" * 60)

    # Graceful shutdown
    running = True

    def signal_handler(signum, frame):
        nonlocal running
        log.info(f"Received signal {signum}, shutting down gracefully...")
        running = False

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Track which screenings have run today
    today_str = datetime.now().strftime("%Y%m%d")
    completed_screens = set()

    while running:
        now = datetime.now()
        current_date = now.strftime("%Y%m%d")

        # Reset daily tracking at midnight
        if current_date != today_str:
            today_str = current_date
            completed_screens.clear()
            log.info(f"New day: {current_date}")

        # Check if it's time for a screening
        time_key = now.strftime("%H:%M")
        if time_key in SCREENING_SCHEDULE and time_key not in completed_screens:
            step, name = SCREENING_SCHEDULE[time_key]

            # Only run trading-day screens on trading days
            if step in ("T0", "T1", "T3", "T4", "T5") and not is_trading_day():
                log.info(f"[{step}] Skipped — not a trading day")
                completed_screens.add(time_key)
            else:
                run_screening_step(step, name)
                completed_screens.add(time_key)

        # Guardian health check every 30 minutes
        if now.minute % 30 == 0 and now.second < 5:
            run_guardian()

        # Update status
        update_status()

        # Sleep: 5s during trading hours, 60s otherwise
        if is_trading_hours():
            time.sleep(5)
        elif is_trading_day():
            time.sleep(30)
        else:
            # Non-trading day: check every 5 minutes
            time.sleep(300)

    log.info("V13.4 Cloud Monitor — Stopped")


if __name__ == "__main__":
    # Support one-shot mode for testing
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        step = sys.argv[2] if len(sys.argv) > 2 else "T5"
        name = SCREENING_SCHEDULE.get(datetime.now().strftime("%H:%M"), ("T5", "Test"))[1]
        run_screening_step(step, name)
    else:
        main()
