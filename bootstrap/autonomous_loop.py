"""
autonomous_loop.py
==================
毕方灵犀貔貅助手自主进化守护进程

目标：在后台持续运行，监控整体系统健康、追踪 T+1 绩效、
      根据失败案例生成改进假设、触发回测实验、写入 Cognee 记忆，
      并定期将代码与数据同步到 GitHub。

运行方式：
    python bootstrap/autonomous_loop.py
或  
    start bootstrap/start_holy_grail_workstation.bat

循环间隔：15 分钟（生产可调整为 60 分钟）
"""
import os
import sys
import json
import time
import asyncio
import ctypes
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# 强制使用 managed venv 的包路径
VENV_SITE = Path("E:/WorkBuddy_dot_workbuddy/binaries/python/envs/default/Lib/site-packages")
if str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

PROJECT_ROOT = Path("E:/WorkBuddy_dot_workbuddy/Claw")
LOG_FILE = PROJECT_ROOT / "logs" / "autonomous_loop.log"
DB_PATH = PROJECT_ROOT / "data" / "holy_grail.db"
MEMORY_DIR = PROJECT_ROOT / ".workbuddy" / "memory"
STATE_FILE = PROJECT_ROOT / ".workbuddy" / "autonomous_loop_state.json"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def log(msg: str):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


class HealthMonitor:
    """监控 Ollama、Cognee、TDX、磁盘空间。"""

    def __init__(self):
        self.report = {}

    def check_ollama(self) -> bool:
        try:
            req = urllib.request.Request(
                "http://localhost:11434/api/tags",
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                self.report["ollama"] = {"ok": True, "models": models}
                return True
        except Exception as e:
            self.report["ollama"] = {"ok": False, "error": str(e)}
            return False

    def check_tdx(self) -> bool:
        """检查 TDX 数据源可用性：进程检测 + 数据库最新数据日期。"""
        try:
            import subprocess
            # 1. 检查通达信客户端进程是否运行
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq tdxw.exe", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=5, encoding="gbk", errors="ignore"
            )
            tdx_running = "tdxw.exe" in (result.stdout or "").lower()

            # 2. 检查数据库最新信号日期（格式归一化后比较，避免 '20260702' vs '2026-10-12' 字符串误判）
            latest_date = None
            if DB_PATH.exists():
                conn = sqlite3.connect(str(DB_PATH))
                cur = conn.cursor()
                try:
                    cur.execute("SELECT MAX(signal_date) FROM daily_signals")
                    row = cur.fetchone()
                    latest_date = row[0] if row else None
                except Exception:
                    pass
                finally:
                    conn.close()

            def _norm(d):
                if not d:
                    return None
                return str(d).replace("-", "").strip()[:8]

            today_y = datetime.now().strftime("%Y%m%d")
            latest_norm = _norm(latest_date)
            data_fresh = latest_norm is not None and latest_norm >= today_y

            ok = tdx_running or data_fresh
            self.report["tdx"] = {
                "ok": ok,
                "tdx_process": tdx_running,
                "latest_data_date": latest_date,
                "data_fresh": data_fresh,
            }
            if not data_fresh:
                log(f"[Health] ⚠️ TDX 数据陈旧（最新 {latest_date}，今日 {today_y}），需刷新数据源")
            return ok
        except Exception as e:
            self.report["tdx"] = {"ok": False, "error": str(e)}
            return False

    def check_disk(self, min_gb: float = 10.0) -> bool:
        try:
            import shutil
            total, used, free = shutil.disk_usage("E:/")
            free_gb = free / (1024**3)
            ok = free_gb >= min_gb
            self.report["disk"] = {"ok": ok, "free_gb": round(free_gb, 2)}
            return ok
        except Exception as e:
            self.report["disk"] = {"ok": False, "error": str(e)}
            return False

    def run(self) -> Dict[str, Any]:
        self.check_ollama()
        self.check_tdx()
        self.check_disk()
        return self.report


class PerformanceTracker:
    """从 holy_grail.db 读取 T+1 绩效，识别失败模式。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    def _query(self, sql: str, params: tuple = ()) -> List[tuple]:
        if not self.db_path.exists():
            return []
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            return cur.fetchall()
        finally:
            conn.close()

    def last_n_days_summary(self, n_days: int = 5) -> Dict[str, Any]:
        since = (datetime.now() - timedelta(days=n_days)).strftime("%Y-%m-%d")
        rows = self._query(
            """
            SELECT signal_date, COUNT(*) as total,
                   SUM(CASE WHEN t1_change > 0 THEN 1 ELSE 0 END) as wins,
                   AVG(t1_change) as avg_return,
                   MAX(t1_change) as best,
                   MIN(t1_change) as worst
            FROM daily_signals
            WHERE signal_date >= ? AND t1_change IS NOT NULL
            GROUP BY signal_date
            ORDER BY signal_date DESC
            """,
            (since,),
        )
        summary = []
        for r in rows:
            summary.append({
                "date": r[0],
                "total": r[1],
                "wins": r[2],
                "avg_return": round(r[3] or 0, 4),
                "best": round(r[4] or 0, 4),
                "worst": round(r[5] or 0, 4),
            })
        return {"since": since, "daily": summary}

    def recent_failures(self, n_days: int = 5, limit: int = 20) -> List[Dict[str, Any]]:
        since = (datetime.now() - timedelta(days=n_days)).strftime("%Y-%m-%d")
        rows = self._query(
            """
            SELECT code, name, signal_date, v132_score, t1_change, screener_rank
            FROM daily_signals
            WHERE signal_date >= ? AND t1_change IS NOT NULL AND t1_change < -3.0
            ORDER BY t1_change ASC
            LIMIT ?
            """,
            (since, limit),
        )
        return [
            {
                "code": r[0], "name": r[1], "signal_date": r[2],
                "score": r[3], "t1_return": r[4], "rank": r[5]
            }
            for r in rows
        ]


class HypothesisGenerator:
    """根据失败案例生成可验证的改进假设。"""

    def __init__(self, tracker: PerformanceTracker):
        self.tracker = tracker

    def generate(self) -> List[Dict[str, Any]]:
        failures = self.tracker.recent_failures(n_days=10, limit=10)
        if not failures:
            return []
        hypotheses = []
        # 按信号日期分组失败案例
        date_counts = {}
        for f in failures:
            d = f.get("signal_date", "未知")
            date_counts[d] = date_counts.get(d, 0) + 1
        if date_counts:
            worst_date = max(date_counts, key=date_counts.get)
            hypotheses.append({
                "id": f"H-{datetime.now().strftime('%Y%m%d')}-01",
                "type": "date_cluster",
                "hypothesis": f"近期失败集中在 {worst_date}（{date_counts[worst_date]}个失败案例），建议检查当日 MEG 门控是否正确拦截。",
                "metric": "该日信号 T+1 平均收益",
                "action": "检查 V13_5_22_MEG.py 在该日的 DEFCON 等级是否匹配实际市场",
                "source_failures": [f["code"] for f in failures if f.get("signal_date") == worst_date]
            })
        low_score_failures = [f for f in failures if (f.get("score") or 0) < 65]
        if low_score_failures:
            hypotheses.append({
                "id": f"H-{datetime.now().strftime('%Y%m%d')}-02",
                "type": "score_threshold",
                "hypothesis": "评分较低的信号失败率显著，建议提高 HARD FILTER 的最低分门槛。",
                "metric": "score >= 65 信号的 T+1 胜率",
                "action": "调整 T4_HardFilter 中 F2 或增加 F5 分数门槛",
                "source_failures": [f["code"] for f in low_score_failures]
            })
        return hypotheses


class MarketDataFetcher:
    """交易时段获取实时行情（东方财富 API），非交易时段跳过。"""

    def __init__(self):
        self.report = {}

    def is_market_open(self) -> bool:
        """判断当前是否在 A 股交易时段（9:15-11:35, 12:55-15:05）。"""
        if datetime.now().weekday() >= 5:
            return False
        hm = datetime.now().hour * 100 + datetime.now().minute
        return (915 <= hm <= 1135) or (1255 <= hm <= 1505)

    def fetch_snapshot(self) -> Optional[Dict[str, Any]]:
        """获取全市场快照。"""
        if not self.is_market_open():
            self.report = {"skipped": True, "reason": "market closed"}
            return None
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from cloud_data_fetcher import fetch_market_snapshot, fetch_sector_heat
            snapshot = fetch_market_snapshot()
            sectors = fetch_sector_heat()
            self.report = {
                "ok": True,
                "total_stocks": snapshot.get("total", 0) if snapshot else 0,
                "limit_up": len(snapshot.get("limit_up", [])) if snapshot else 0,
                "limit_down": len(snapshot.get("limit_down", [])) if snapshot else 0,
                "sectors_tracked": len(sectors) if sectors else 0,
                "timestamp": datetime.now().isoformat(),
            }
            return {"snapshot": snapshot, "sectors": sectors}
        except Exception as e:
            self.report = {"ok": False, "error": str(e)}
            log(f"[MarketData] Fetch error: {e}")
            return None


class MemoryUpdater:
    """将高价值决策写入 Cognee，失败时降级到 SQLite。"""

    def __init__(self):
        self.fallback_db = PROJECT_ROOT / ".workbuddy" / "cognee_fallback.db"
        self._cognee_unavailable = False  # 首次失败后置位，避免每个循环重复尝试
        self._init_fallback_db()

    def _init_fallback_db(self):
        conn = sqlite3.connect(str(self.fallback_db))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS autonomous_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT,
                memory_type TEXT,
                payload TEXT
            )"""
        )
        conn.commit()
        conn.close()

    async def write_cognee(self, text: str, memory_type: str = "observation"):
        """写入 Cognee 记忆，失败/超时时降级到 SQLite。"""
        if os.environ.get("SKIP_COGNEE", "0") == "1" or self._cognee_unavailable:
            self.write_fallback(text, memory_type)
            return
        try:
            from V13_5_24_CogneeMemory import _ensure_cognee_env
            _ensure_cognee_env()
            import cognee
            await cognee.add(text, dataset_name="claw_autonomous")
            # cognify 可能很慢（7B CPU），用 90 秒超时
            try:
                await asyncio.wait_for(cognee.cognify(), timeout=90.0)
                log(f"[MemoryUpdater] Cognee write ok: {memory_type}")
            except asyncio.TimeoutError:
                log(f"[MemoryUpdater] Cognee cognify timeout (90s), fallback to SQLite")
                self.write_fallback(text, memory_type)
        except Exception as e:
            self._cognee_unavailable = True
            log(f"[MemoryUpdater] Cognee unavailable ({e}), 后续循环直连 SQLite 降级")
            self.write_fallback(text, memory_type)

    def write_fallback(self, text: str, memory_type: str):
        conn = sqlite3.connect(str(self.fallback_db))
        conn.execute(
            "INSERT INTO autonomous_memory (created_at, memory_type, payload) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), memory_type, text)
        )
        conn.commit()
        conn.close()


class GitSync:
    """自动 Git 同步。"""

    def sync(self):
        import subprocess
        try:
            subprocess.run(["git", "add", "-A"], cwd=str(PROJECT_ROOT), check=False, capture_output=True)
            result = subprocess.run(
                ["git", "commit", "-m", f"auto-sync: {datetime.now().isoformat()}"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False
            )
            if result.returncode == 0:
                subprocess.run(["git", "push", "origin", "main"], cwd=str(PROJECT_ROOT), check=False, capture_output=True)
                log("[GitSync] Synced to GitHub.")
            else:
                log("[GitSync] Nothing to commit or commit failed.")
        except Exception as e:
            log(f"[GitSync] Error: {e}")


class AutonomousLoop:
    """主循环。"""

    # 交易节点时间表
    TRADING_SLOTS = {
        "08:30": "盘前MEG预评",
        "10:30": "T0开盘检",
        "11:30": "T1午盘检",
        "14:00": "T3预检",
        "14:15": "T4选股(核心)",
        "14:30": "T5圣杯执行",
        "15:05": "收盘归档",
        "15:35": "M55调仓",
        "20:00": "夜间T+1验证",
        "22:00": "BATTLE作战计划",
    }

    # 节点名称 → cloud_pipeline 步骤映射（本地真实执行，不再 monitor-only）
    SLOT_TO_STEP = {
        "T0": "T0",
        "T1": "T1",
        "T3": "T3",
        "T4": "T4",
        "T5": "T5",
        "夜间": "NIGHT",
        "BATTLE": "BATTLE",
    }

    def __init__(self, interval_minutes: int = 15):
        self.interval = interval_minutes * 60
        self.health = HealthMonitor()
        self.tracker = PerformanceTracker(DB_PATH)
        self.hypo_gen = HypothesisGenerator(self.tracker)
        self.memory = MemoryUpdater()
        self.market = MarketDataFetcher()
        self.git = GitSync()
        self.state = self._load_state()
        self._triggered_slots = set()  # 当天已触发的节点

    def _is_trading_day(self) -> bool:
        """简单判断：周末非交易日。节假日需额外日历。"""
        return datetime.now().weekday() < 5  # 0=Mon ... 4=Fri

    def _current_slot(self) -> Optional[str]:
        """返回当前应该触发的交易节点名称，或 None。"""
        if not self._is_trading_day():
            return None
        now_hm = datetime.now().strftime("%H:%M")
        for slot_time, slot_name in self.TRADING_SLOTS.items():
            slot_h, slot_m = int(slot_time[:2]), int(slot_time[3:])
            now_h, now_m = datetime.now().hour, datetime.now().minute
            # 在 slot 时间 ±10 分钟内触发
            if now_h == slot_h and abs(now_m - slot_m) <= 10:
                today_key = f"{datetime.now().strftime('%Y%m%d')}_{slot_time}"
                if today_key not in self._triggered_slots:
                    self._triggered_slots.add(today_key)
                    return slot_name
        return None

    def _check_slot_output(self, slot_name: str) -> bool:
        """检查交易节点输出文件是否已存在。"""
        today = datetime.now().strftime("%Y%m%d")
        outputs_dir = PROJECT_ROOT / "outputs"
        if slot_name and "T4" in slot_name:
            # T4 输出：battle_plan 或 closing_archive
            return any(
                (outputs_dir / f).exists()
                for f in [f"battle_plan_{today}.html", f"closing_archive_{today}.py"]
            )
        elif slot_name and "夜间" in slot_name:
            # 20:00 夜间：检查 m71_t1_validation 是否有今天数据
            if DB_PATH.exists():
                conn = sqlite3.connect(str(DB_PATH))
                cur = conn.cursor()
                try:
                    cur.execute(
                        "SELECT COUNT(*) FROM m71_t1_validation WHERE signal_date = ?",
                        (datetime.now().strftime("%Y-%m-%d"),),
                    )
                    return cur.fetchone()[0] > 0
                except Exception:
                    return False
                finally:
                    conn.close()
        return False

    def _trigger_slot(self, slot_name: str):
        """触发交易节点：本地真实执行 cloud_pipeline 步骤（不再 monitor-only）。"""
        import subprocess
        today = datetime.now().strftime("%Y%m%d")

        # 映射节点 → cloud_pipeline 步骤
        step = None
        for key, val in self.SLOT_TO_STEP.items():
            if key in slot_name:
                step = val
                break

        # 盘前/收盘/M55 等非 cloud 节点：仅记录，不触发 cloud 步骤
        if step is None:
            log(f"[Slot] {slot_name} — 本地观察节点（不触发 cloud 步骤）")
            return

        # 若该步骤今日已成功运行，跳过（避免重复执行）
        step_file = PROJECT_ROOT / "cloud_state" / f"step_{step}.json"
        if step_file.exists():
            try:
                sd = json.loads(step_file.read_text(encoding="utf-8"))
                if sd.get("timestamp", "").startswith(today) and "error" not in sd.get("data", {}):
                    log(f"[Slot] {slot_name} → {step} 今日已成功执行，跳过")
                    return
            except Exception:
                pass

        log(f"[Slot] {slot_name} → 本地执行 cloud_pipeline --step={step}")
        try:
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "cloud_pipeline.py"), f"--step={step}"],
                cwd=str(PROJECT_ROOT),
                capture_output=True, text=True, timeout=600,
            )
            out = (result.stdout or "")[-800:]
            if result.returncode == 0:
                log(f"[Slot] ✅ {step} 执行成功\n{out}")
            else:
                log(f"[Slot] ❌ {step} 返回 {result.returncode}\n{out}\n{(result.stderr or '')[-400:]}")
        except subprocess.TimeoutExpired:
            log(f"[Slot] ⏱️ {step} 执行超时（>600s）")
        except Exception as e:
            log(f"[Slot] ❌ {step} 执行异常: {e}")

    def _run_grail_oos_nightly(self):
        """圣杯前向 OOS 闭环 nightly 标注+复核（运行时自提升链路末端）。

        由 T5 实推 emit 写入前台台账(backtest_cache/grail_hit_forward_ledger.jsonl)；
        本步 label_all 回填 T+1..T+3 真实标签(数据到齐后), evaluate 复核晋升
        (n>=200 后裁定 SUPPLEMENTARY_LIVE_CONFIRMED, 闸门一字未降), 且 --apply-confirm
        在达晋升时写回验证边 manifest(live_confirmed + live_metrics, 运行时自提升)；
        未达晋升则诚实不写回。仅依赖本地全量日线缓存；CI 无数据则空转(台账无新记录可标)。
        """
        import subprocess
        log("[GrailOOS] 启动 nightly 标注+复核")
        try:
            result = subprocess.run(
                [sys.executable, "-m", "research.grail_hit_forward_oos", "--label-all", "--report", "--apply-confirm"],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=300,
            )
            out = (result.stdout or "")[-1500:]
            if result.returncode == 0:
                log(f"[GrailOOS] ✅ 标注+复核完成\n{out}")
            else:
                log(f"[GrailOOS] ❌ 返回 {result.returncode}\n{out}\n{(result.stderr or '')[-400:]}")
        except subprocess.TimeoutExpired:
            log("[GrailOOS] ⏱️ 超时(>300s)")
        except Exception as e:
            log(f"[GrailOOS] ❌ 异常: {e}")

    def _maybe_daily_grail_oos(self):
        """每日 ~21:00 触发一次圣杯前向 OOS 标注+复核(含周末/节假日, 不受交易日限制)。
        用 state 的 grail_oos_last 保证每日仅一次。"""
        now = datetime.now()
        # 每日 21:00 后首次迭代触发一次（不再限定 21:00±10 分钟窄窗:
        # 循环颗粒度为 15 分钟, 窄窗可能被完整跳过导致 nightly 标注永不触发）。
        # 由下方 grail_oos_last 当日守卫保证每日仅跑一次。
        if now.hour < 21:
            return
        today_key = f"grail_oos_{now.strftime('%Y%m%d')}"
        if self.state.get("grail_oos_last") == today_key:
            return
        self.state["grail_oos_last"] = today_key
        self._run_grail_oos_nightly()

    def _load_state(self) -> Dict[str, Any]:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {"last_run": None, "runs": 0, "hypotheses": []}

    def _save_state(self):
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)  # 原子替换, 防止写一半崩溃损坏 state

    async def _run_once(self):
        log("=" * 60)
        log("[Loop] Starting autonomous iteration.")
        self.state["runs"] += 1
        self.state["last_run"] = datetime.now().isoformat()

        # 1. 健康检查
        health_report = self.health.run()
        log(f"[Health] {json.dumps(health_report, ensure_ascii=False)}")
        if not health_report.get("ollama", {}).get("ok"):
            log("[Health] Ollama not healthy. Starting Ollama from E drive...")
            import subprocess
            subprocess.Popen(["bootstrap\\start_ollama_from_e.bat"], cwd=str(PROJECT_ROOT), shell=True)
            await asyncio.sleep(10)

        # 1.5 交易节点触发检查
        slot = self._current_slot()
        if slot:
            log(f"[Slot] Triggering: {slot}")
            self._trigger_slot(slot)

        # 1.6 圣杯前向 OOS 每日标注+复核（~21:00, 含周末；emit 由 T5 实推写入台账）
        self._maybe_daily_grail_oos()

        # 1.8 实时行情获取（交易时段）
        market_data = self.market.fetch_snapshot()
        if self.market.report:
            log(f"[MarketData] {json.dumps(self.market.report, ensure_ascii=False)}")

        # 2. T+1 绩效追踪
        perf = self.tracker.last_n_days_summary(n_days=7)
        failures = self.tracker.recent_failures(n_days=7, limit=10)
        log(f"[Performance] {json.dumps(perf, ensure_ascii=False)}")
        log(f"[Performance] Recent failures: {len(failures)}")

        # 3. 写入记忆
        market_summary = ""
        if market_data and self.market.report.get("ok"):
            market_summary = (
                f"实时行情：{self.market.report.get('total_stocks', 0)}只股票，"
                f"涨停{self.market.report.get('limit_up', 0)}只，"
                f"跌停{self.market.report.get('limit_down', 0)}只。"
            )
        memory_text = (
            f"{market_summary} T+1绩效追踪：最近7天 {json.dumps(perf, ensure_ascii=False)}。 "
            f"失败案例：{json.dumps(failures[:3], ensure_ascii=False)}。"
        )
        await self.memory.write_cognee(memory_text, memory_type="performance_review")

        # 4. 生成假设
        hypotheses = self.hypo_gen.generate()
        if hypotheses:
            for h in hypotheses:
                log(f"[Hypothesis] {h['id']}: {h['hypothesis']}")
                await self.memory.write_cognee(
                    f"改进假设 {h['id']}：{h['hypothesis']} 指标：{h['metric']} 行动：{h['action']}",
                    memory_type="hypothesis"
                )
            self.state["hypotheses"].extend(hypotheses)

        # 5. Git 同步（每4次循环同步一次）
        if self.state["runs"] % 4 == 0:
            self.git.sync()

        self._save_state()
        log("[Loop] Iteration complete.")

    async def run_forever(self):
        log("[Loop] Autonomous loop started. Press Ctrl+C to stop.")
        while True:
            try:
                await self._run_once()
            except Exception as e:
                log(f"[Loop] ERROR: {e}")
            log(f"[Loop] Sleeping {self.interval} seconds...")
            await asyncio.sleep(self.interval)


_SINGLE_INSTANCE_HANDLE = None


def _acquire_single_instance() -> bool:
    """确保全局仅一个活跃循环（防止 launcher/自身双 spawn 导致多个循环共享 state）。

    使用 Windows 命名互斥体(CreateMutexW, 全局命名空间), 由 OS 跨进程强制唯一持有:
    若互斥体已存在(另一实例创建) -> 本进程退出(False)。进程退出/崩溃时 OS 自动释放，
    无陈旧锁问题。ctypes 失败则降级放行(不阻塞循环)。
    """
    global _SINGLE_INSTANCE_HANDLE
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        mutex_name = "Global\\BifangAutonomousLoopSingleton"
        h = kernel32.CreateMutexW(None, False, mutex_name)
        if not h:
            return True  # 创建失败 -> 降级放行
        if ctypes.get_last_error() == 0xB7:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(h)
            return False  # 另一实例已持有互斥体 -> 退出
        _SINGLE_INSTANCE_HANDLE = h  # 持有直到进程退出(OS 自动释放)
        return True
    except Exception:
        return True


if __name__ == "__main__":
    if not _acquire_single_instance():
        print("[Loop] 另一实例已在运行, 本进程退出(避免重复循环共享 state 文件)。")
        raise SystemExit(0)
    import atexit, ctypes as _ct
    atexit.register(
        lambda: _ct.WinDLL("kernel32", use_last_error=True).CloseHandle(_SINGLE_INSTANCE_HANDLE)
        if _SINGLE_INSTANCE_HANDLE else None
    )
    try:
        loop = AutonomousLoop(interval_minutes=15)
        asyncio.run(loop.run_forever())
    except KeyboardInterrupt:
        log("[Loop] Stopped by user.")
    except Exception as e:
        log(f"[Loop] Fatal error: {e}")
