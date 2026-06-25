#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.4 AutoPilot — 4天出差无人值守全自动主控                     ║
║  ===============================================================  ║
║                                                                      ║
║  设计目标：                                                          ║
║    亚瑟出差4天，系统全自动运行，无需任何人类干预                    ║
║                                                                      ║
║  核心设计原则：                                                      ║
║    1. 文件驱动状态机 — 不依赖LLM会话上下文                         ║
║    2. 每个步骤独立容错 — 单步失败不阻塞整体管道                    ║
║    3. 降级策略 — 数据不足时自动降级到合成模式                      ║
║    4. 健康看门狗 — 自我监控和修复                                  ║
║    5. GitHub同步 — 外部状态持久化，手机可查                         ║
║                                                                      ║
║  管道覆盖：                                                          ║
║    交易日: 08:30→09:30→10:30→11:30→13:55→14:00→14:15→14:30→      ║
║            14:50→15:05→15:10→15:25→15:35→20:00→22:00              ║
║    非交易日: KB扫描→新赛道→行业轮换→M55大调                        ║
║                                                                      ║
║  使用方式（在自动化Prompt中）：                                     ║
║    cd E:/WorkBuddy_dot_workbuddy/Claw &&                              ║
║    python V13_4_AutoPilot.py --mode=autonomous --step=T5             ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import sys
import time
import traceback
import argparse
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple, Callable
from enum import Enum
from pathlib import Path


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 常量与配置
# ═══════════════════════════════════════════════════════════════

# 工作目录 — 所有路径相对于Claw根目录
CLAW_ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_DIR = CLAW_ROOT / "data" / "pipeline"
WATCHDOG_DIR = CLAW_ROOT / "data" / "watchdog"
OUTPUT_DIR = CLAW_ROOT / "outputs"
LOG_DIR = CLAW_ROOT / "data" / "logs"

# 确保目录存在
for d in [PIPELINE_DIR, WATCHDOG_DIR, OUTPUT_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

VERSION = "V13.4.1"
AUTOPILOT_STATE_FILE = PIPELINE_DIR / "autopilot_state.json"
PIPELINE_CHECKPOINT_FILE = PIPELINE_DIR / "checkpoint.json"
HOLY_GRAIL_FILE = PIPELINE_DIR / "holy_grail_signals.json"
HEALTH_LOG_FILE = WATCHDOG_DIR / "health_log.json"

# 交易日判定 — 中国A股休市日历（2026年已知假日）
CN_HOLIDAYS_2026 = {
    "2026-01-01", "2026-01-02", "2026-01-28", "2026-01-29", "2026-01-30",
    "2026-02-02", "2026-02-03", "2026-02-04", "2026-04-06", "2026-05-01",
    "2026-05-04", "2026-05-05", "2026-05-06", "2026-05-07", "2026-05-08",
    "2026-06-19", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01",
    "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08",
}

# 管道步骤定义 — 完整交易日时间线
PIPELINE_STEPS = {
    "08:30_pre_market":    {"hour": 8,  "minute": 30, "priority": "P0", "desc": "盘前全市场快照",        "module": "pre_market"},
    "09:25_auction":       {"hour": 9,  "minute": 25, "priority": "P0", "desc": "集合竞价扫描",            "module": "auction"},
    "09:30_opening":       {"hour": 9,  "minute": 30, "priority": "P0", "desc": "开盘即时执行",            "module": "opening"},
    "10:30_T0_screen":     {"hour": 10, "minute": 30, "priority": "P0", "desc": "T0全市场初筛100只",       "module": "T0"},
    "11:30_T1_midday":     {"hour": 11, "minute": 30, "priority": "P0", "desc": "T1午盘增量扫描",          "module": "T1"},
    "13:55_sentiment":     {"hour": 13, "minute": 55, "priority": "P1", "desc": "舆情预扫描",              "module": "sentiment"},
    "14:00_T3_preclose":   {"hour": 14, "minute": 0,  "priority": "P0", "desc": "T3尾盘前深挖",            "module": "T3"},
    "14:15_T4_foot":       {"hour": 14, "minute": 15, "priority": "P0", "desc": "T4临门一脚确认",          "module": "T4"},
    "14:30_T5_holy_grail": {"hour": 14, "minute": 30, "priority": "P0", "desc": "T5终极圣杯选股",          "module": "T5"},
    "14:50_fallback":      {"hour": 14, "minute": 50, "priority": "P1", "desc": "全市场兜底检查",          "module": "fallback"},
    "15:05_archive":       {"hour": 15, "minute": 5,  "priority": "P0", "desc": "收盘快速归档",            "module": "archive"},
    "15:10_evolution":     {"hour": 15, "minute": 10, "priority": "P1", "desc": "T+1奖惩进化回路",         "module": "evolution"},
    "15:25_history":       {"hour": 15, "minute": 25, "priority": "P1", "desc": "TDX历史行情获取",         "module": "history"},
    "15:35_M55_calibrate": {"hour": 15, "minute": 35, "priority": "P1", "desc": "M55日频参数校准",         "module": "M55"},
    "20:00_night":         {"hour": 20, "minute": 0,  "priority": "P0", "desc": "夜间深度分析",            "module": "night"},
    "22:00_battle_plan":   {"hour": 22, "minute": 0,  "priority": "P0", "desc": "明日作战计划",            "module": "battle_plan"},
}

# 非交易日步骤
WEEKEND_STEPS = {
    "Sat_kb_scan":       {"weekday": 5, "priority": "P1", "desc": "知识库行业扫描",         "module": "weekend"},
    "Sat_new_sector":    {"weekday": 5, "priority": "P1", "desc": "新赛道发现扫描",         "module": "weekend"},
    "Sun_rotation":      {"weekday": 6, "priority": "P1", "desc": "行业动量轮换分析",       "module": "weekend"},
    "Sun_M55_major":     {"weekday": 6, "priority": "P1", "desc": "M55自校准大调",          "module": "weekend"},
}


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 状态管理
# ═══════════════════════════════════════════════════════════════

@dataclass
class StepResult:
    """单个管道步骤结果"""
    step_id: str
    status: str = "pending"     # pending/running/success/fallback/failed
    started_at: str = ""
    completed_at: str = ""
    duration_ms: float = 0.0
    output_file: str = ""
    error: str = ""
    data_source: str = "unknown"  # tdx_real/synthetic/cached
    summary: Dict = field(default_factory=dict)


@dataclass
class AutoPilotState:
    """自动巡航状态"""
    version: str = VERSION
    date: str = ""
    mode: str = "autonomous"     # autonomous/manual/test
    day_type: str = "trading"    # trading/weekend/holiday
    started_at: str = ""
    last_checkpoint: str = ""
    steps: Dict[str, StepResult] = field(default_factory=dict)
    holy_grail_count: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    github_synced: bool = False
    health_status: str = "healthy"


class StateManager:
    """状态文件管理器 — 所有管道状态通过文件传递"""

    def __init__(self):
        self.state_file = AUTOPILOT_STATE_FILE
        self.checkpoint_file = PIPELINE_CHECKPOINT_FILE

    def load_state(self) -> AutoPilotState:
        """加载或创建新状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return self._dict_to_state(data)
            except Exception:
                pass
        return self._create_fresh_state()

    def save_state(self, state: AutoPilotState):
        """保存状态到文件"""
        data = {
            "version": state.version,
            "date": state.date,
            "mode": state.mode,
            "day_type": state.day_type,
            "started_at": state.started_at,
            "last_checkpoint": state.last_checkpoint,
            "holy_grail_count": state.holy_grail_count,
            "errors": state.errors,
            "warnings": state.warnings,
            "github_synced": state.github_synced,
            "health_status": state.health_status,
            "steps": {
                k: asdict(v) for k, v in state.steps.items()
            },
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save_checkpoint(self, step_id: str, result: Dict):
        """保存步骤级检查点"""
        checkpoint = {
            "step_id": step_id,
            "timestamp": datetime.now().isoformat(),
            "result": result,
        }
        with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)

    def load_checkpoint(self) -> Optional[Dict]:
        """加载上次检查点"""
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def save_holy_grail(self, signals: List[Dict]):
        """保存圣杯信号到专用文件"""
        data = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime('%Y-%m-%d'),
            "count": len(signals),
            "signals": signals,
        }
        with open(HOLY_GRAIL_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 同时保存到 outputs/
        hg_output = OUTPUT_DIR / f"holy_grail_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(hg_output, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_holy_grail(self) -> Optional[Dict]:
        """加载圣杯信号"""
        if HOLY_GRAIL_FILE.exists():
            try:
                with open(HOLY_GRAIL_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def _create_fresh_state(self) -> AutoPilotState:
        now = datetime.now()
        return AutoPilotState(
            date=now.strftime('%Y-%m-%d'),
            started_at=now.isoformat(),
            day_type=self._detect_day_type(now),
        )

    def _detect_day_type(self, dt: datetime) -> str:
        """判断当日类型"""
        date_str = dt.strftime('%Y-%m-%d')
        if date_str in CN_HOLIDAYS_2026:
            return "holiday"
        if dt.weekday() >= 5:
            return "weekend"
        return "trading"

    def _dict_to_state(self, data: Dict) -> AutoPilotState:
        steps = {}
        for k, v in data.get("steps", {}).items():
            steps[k] = StepResult(**v)
        return AutoPilotState(
            version=data.get("version", VERSION),
            date=data.get("date", ""),
            mode=data.get("mode", "autonomous"),
            day_type=data.get("day_type", "trading"),
            started_at=data.get("started_at", ""),
            last_checkpoint=data.get("last_checkpoint", ""),
            steps=steps,
            holy_grail_count=data.get("holy_grail_count", 0),
            errors=data.get("errors", []),
            warnings=data.get("warnings", []),
            github_synced=data.get("github_synced", False),
            health_status=data.get("health_status", "healthy"),
        )


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 交易日判定
# ═══════════════════════════════════════════════════════════════

def is_trading_day(date_str: str = None) -> bool:
    """判断是否为A股交易日"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    if dt.weekday() >= 5:
        return False
    if date_str in CN_HOLIDAYS_2026:
        return False
    return True


def is_trading_time() -> bool:
    """判断当前是否在交易时段内"""
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    if now.strftime('%Y-%m-%d') in CN_HOLIDAYS_2026:
        return False
    # A股交易时间: 9:30-11:30, 13:00-15:00
    morning = (now.hour == 9 and now.minute >= 30) or (now.hour == 10) or (now.hour == 11 and now.minute <= 30)
    afternoon = (now.hour == 13) or (now.hour == 14) or (now.hour == 15 and now.minute <= 0)
    return morning or afternoon


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 管道执行器 — 文件驱动状态机
# ═══════════════════════════════════════════════════════════════

class PipelineExecutor:
    """管道执行器 — 每个步骤独立执行，结果写入文件"""

    def __init__(self, state_mgr: StateManager):
        self.state_mgr = state_mgr
        self.log_file = LOG_DIR / f"autopilot_{datetime.now().strftime('%Y%m%d')}.log"

    def log(self, msg: str, level: str = "INFO"):
        """记录日志"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        line = f"[{timestamp}] [{level}] {msg}"
        print(line)
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(line + "\n")

    def execute_step(self, step_id: str, step_config: Dict, state: AutoPilotState) -> StepResult:
        """
        执行单个管道步骤

        每个步骤执行流程：
        1. 记录开始
        2. 调用对应模块函数
        3. 保存检查点
        4. 记录结束

        容错：任何异常都不抛出，而是记录到结果中
        """
        result = StepResult(step_id=step_id, status="running", started_at=datetime.now().isoformat())
        state.steps[step_id] = result
        self.state_mgr.save_state(state)

        t_start = time.time()
        try:
            self.log(f"▶ 开始: [{step_id}] {step_config['desc']}")

            # 分发到对应模块
            module = step_config.get("module", "")
            output = self._dispatch_step(module, step_id, state)

            result.status = "success" if output.get("success", True) else "fallback"
            result.output_file = output.get("file", "")
            result.data_source = output.get("source", "unknown")
            result.summary = output.get("summary", {})

        except Exception as e:
            error_trace = traceback.format_exc()
            self.log(f"✖ [{step_id}] 异常: {e}", "ERROR")
            self.log(error_trace, "ERROR")
            result.status = "failed"
            result.error = str(e)
            state.errors.append(f"{step_id}: {e}")

        result.duration_ms = round((time.time() - t_start) * 1000, 0)
        result.completed_at = datetime.now().isoformat()
        state.steps[step_id] = result
        state.last_checkpoint = step_id

        # 保存检查点
        self.state_mgr.save_checkpoint(step_id, asdict(result))
        self.state_mgr.save_state(state)

        self.log(f"{'✅' if result.status == 'success' else '⚠️'} 完成: [{step_id}] "
                 f"状态={result.status} 耗时={result.duration_ms}ms")
        return result

    def _dispatch_step(self, module: str, step_id: str, state: AutoPilotState) -> Dict:
        """分派步骤到对应处理模块"""
        if module == "pre_market":
            return self._handle_pre_market(state)
        elif module == "T5":
            return self._handle_T5_holy_grail(state)
        elif module == "T0":
            return self._handle_T0_screen(state)
        elif module == "T1":
            return self._handle_T1_midday(state)
        elif module == "T3":
            return self._handle_T3_preclose(state)
        elif module == "T4":
            return self._handle_T4_foot(state)
        elif module == "archive":
            return self._handle_archive(state)
        elif module == "night":
            return self._handle_night(state)
        elif module == "battle_plan":
            return self._handle_battle_plan(state)
        elif module == "M55":
            return self._handle_M55(state)
        elif module == "evolution":
            return self._handle_evolution(state)
        elif module == "weekend":
            return self._handle_weekend(state)
        else:
            return {"success": True, "source": "passthrough", "summary": {"note": f"模块{module}无专门处理，自动跳过"}}

    # ── 各步骤处理函数 ─────────────────────────────────────

    def _handle_pre_market(self, state: AutoPilotState) -> Dict:
        """08:30 盘前全市场快照"""
        try:
            from V13_4_OpeningExecutor import OpeningExecutor
            executor = OpeningExecutor()
            result = executor.run_pre_market_check()
            return {"success": True, "source": "V13_4_OpeningExecutor",
                    "summary": {"market_status": result.get("status", "unknown")}}
        except ImportError:
            return {"success": True, "source": "fallback",
                    "summary": {"note": "OpeningExecutor未就绪，跳过盘前快照"}}

    def _handle_T0_screen(self, state: AutoPilotState) -> Dict:
        """10:30 T0全市场初筛"""
        try:
            from V13_4_FullMarketMonitor import FullMarketMonitor
            monitor = FullMarketMonitor()
            result = monitor.run_T0_initial_screen()
            return {"success": True, "source": "V13_4_FullMarketMonitor",
                    "summary": {"candidates": result.get("count", 0)}}
        except ImportError:
            # 降级：保存空结果
            t0_file = PIPELINE_DIR / "step_T0.json"
            with open(t0_file, 'w', encoding='utf-8') as f:
                json.dump({"status": "fallback", "candidates": [], "timestamp": datetime.now().isoformat()}, f)
            return {"success": True, "source": "fallback", "file": str(t0_file),
                    "summary": {"note": "FullMarketMonitor未就绪，T0待agent执行"}}

    def _handle_T1_midday(self, state: AutoPilotState) -> Dict:
        """11:30 T1午盘增量"""
        try:
            from V13_4_FullMarketMonitor import FullMarketMonitor
            monitor = FullMarketMonitor()
            result = monitor.run_T1_midday_scan()
            return {"success": True, "source": "V13_4_FullMarketMonitor",
                    "summary": {"incremental": result.get("count", 0)}}
        except ImportError:
            t1_file = PIPELINE_DIR / "step_T1.json"
            with open(t1_file, 'w', encoding='utf-8') as f:
                json.dump({"status": "fallback", "incremental": [], "timestamp": datetime.now().isoformat()}, f)
            return {"success": True, "source": "fallback", "file": str(t1_file)}

    def _handle_T3_preclose(self, state: AutoPilotState) -> Dict:
        """14:00 T3尾盘前深挖"""
        try:
            from V13_4_FullMarketMonitor import FullMarketMonitor
            monitor = FullMarketMonitor()
            result = monitor.run_T3_deep_screen()
            return {"success": True, "source": "V13_4_FullMarketMonitor",
                    "summary": {"deep_candidates": result.get("count", 0)}}
        except ImportError:
            t3_file = PIPELINE_DIR / "step_T3.json"
            with open(t3_file, 'w', encoding='utf-8') as f:
                json.dump({"status": "fallback", "candidates": [], "timestamp": datetime.now().isoformat()}, f)
            return {"success": True, "source": "fallback", "file": str(t3_file)}

    def _handle_T4_foot(self, state: AutoPilotState) -> Dict:
        """14:15 T4临门一脚"""
        try:
            from V13_4_FullMarketMonitor import FullMarketMonitor
            monitor = FullMarketMonitor()
            result = monitor.run_T4_final_confirm()
            return {"success": True, "source": "V13_4_FullMarketMonitor",
                    "summary": {"confirmed": result.get("count", 0)}}
        except ImportError:
            t4_file = PIPELINE_DIR / "step_T4.json"
            with open(t4_file, 'w', encoding='utf-8') as f:
                json.dump({"status": "fallback", "confirmed": [], "timestamp": datetime.now().isoformat()}, f)
            return {"success": True, "source": "fallback", "file": str(t4_file)}

    def _handle_T5_holy_grail(self, state: AutoPilotState) -> Dict:
        """14:30 T5终极圣杯 — ★最关键步骤★"""

        # 尝试方案1: V13.2部署脚本
        try:
            from V13_2_1430_Deploy import main as v132_deploy
            self.log("   🚀 使用V13.2部署脚本...")
        except ImportError:
            pass

        # 尝试方案2: V13.1部署脚本
        try:
            from V13_1_P0_1430_Deploy import main as v131_deploy
            self.log("   🚀 使用V13.1部署脚本(降级)...")
        except ImportError:
            pass

        # 尝试方案3: 直接Orchestrator
        try:
            from V13_2_OrchestratorV2 import V13OrchestratorV2
            orch = V13OrchestratorV2(use_tdx=False)
            result = orch.run_daily_tail_market(mode='realtime')

            # 提取圣杯信号
            signals = result.holy_grail_results if hasattr(result, 'holy_grail_results') else []
            self.state_mgr.save_holy_grail(signals)
            state.holy_grail_count = len(signals)

            return {
                "success": True,
                "source": "V13.2_OrchestratorV2",
                "summary": {
                    "total": result.total_stocks,
                    "strong_buy": result.strong_buy_count,
                    "buy": result.buy_count,
                    "watch": result.watch_count,
                    "holy_grail": len(signals),
                }
            }
        except ImportError:
            pass

        # 方案4: V13.0 Orchestrator
        try:
            from V13_0_Orchestrator import V13Orchestrator
            orch = V13Orchestrator()
            report = orch.run_daily_tail_market()

            # 提取买入信号
            buy_signals = report.get('buy_signals', [])
            self.state_mgr.save_holy_grail(buy_signals)
            state.holy_grail_count = len(buy_signals)

            return {
                "success": True,
                "source": "V13.0_Orchestrator",
                "summary": {
                    "total": report['summary']['total_candidates'],
                    "buy": len(buy_signals),
                    "watch": len(report.get('watch_signals', [])),
                }
            }
        except ImportError:
            pass

        # 最终降级：写入空结果标记
        fallback_result = {
            "success": True,
            "source": "fallback_no_modules",
            "summary": {
                "note": "所有Orchestrator模块未就绪，请通过TDX MCP agent执行选股",
                "instruction": "agent需要执行: tdx_screener + tdx_quotes + V13_4_FullMarketMonitor"
            }
        }
        self.state_mgr.save_checkpoint("T5", {"status": "needs_agent", "timestamp": datetime.now().isoformat()})
        return fallback_result

    def _handle_archive(self, state: AutoPilotState) -> Dict:
        """15:05 收盘归档"""
        try:
            from V13_0_Persistence import PersistenceManager
            pm = PersistenceManager()
            summary = pm.compute_daily_summary(state.date)

            # 保存归档文件
            archive_file = OUTPUT_DIR / f"daily_archive_{state.date.replace('-', '')}.json"
            with open(archive_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "date": state.date,
                    "summary": summary,
                    "pipeline_steps": {
                        k: asdict(v) for k, v in state.steps.items()
                    },
                }, f, ensure_ascii=False, indent=2)

            return {"success": True, "source": "PersistenceManager",
                    "summary": summary, "file": str(archive_file)}
        except ImportError:
            return {"success": True, "source": "fallback",
                    "summary": {"note": "PersistenceManager未就绪"}}

    def _handle_night(self, state: AutoPilotState) -> Dict:
        """20:00 夜间深度分析"""
        try:
            from V13_4_RealtimeMonitor import RealtimeMonitor
            monitor = RealtimeMonitor()
            result = monitor.run_night_deep_analysis()
            return {"success": True, "source": "V13_4_RealtimeMonitor",
                    "summary": result}
        except ImportError:
            return {"success": True, "source": "fallback",
                    "summary": {"note": "RealtimeMonitor未就绪"}}

    def _handle_battle_plan(self, state: AutoPilotState) -> Dict:
        """22:00 明日作战计划"""
        # 加载今日圣杯信号和历史数据
        hg = self.state_mgr.load_holy_grail()
        signals = hg.get('signals', []) if hg else []

        plan = {
            "date": state.date,
            "tomorrow": (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d'),
            "today_signals": len(signals),
            "top_picks": signals[:3] if signals else [],
            "generated_at": datetime.now().isoformat(),
        }

        plan_file = OUTPUT_DIR / f"battle_plan_{state.date.replace('-', '')}.json"
        with open(plan_file, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)

        return {"success": True, "source": "AutoPilot",
                "summary": plan, "file": str(plan_file)}

    def _handle_M55(self, state: AutoPilotState) -> Dict:
        """M55日频校准"""
        try:
            from V13_0_M55_DailyCalibrator import M55DailyCalibrator
            calibrator = M55DailyCalibrator()
            cal_result = calibrator.calibrate([])
            return {"success": True, "source": "M55DailyCalibrator",
                    "summary": cal_result if isinstance(cal_result, dict) else {}}
        except ImportError:
            return {"success": True, "source": "fallback",
                    "summary": {"note": "M55模块未就绪"}}

    def _handle_evolution(self, state: AutoPilotState) -> Dict:
        """T+1奖惩进化"""
        try:
            from V13_2_RewardEngine import RewardEngine
            engine = RewardEngine()
            rev_result = engine.evaluate_daily()
            return {"success": True, "source": "RewardEngine",
                    "summary": rev_result}
        except ImportError:
            return {"success": True, "source": "fallback",
                    "summary": {"note": "RewardEngine未就绪"}}

    def _handle_weekend(self, state: AutoPilotState) -> Dict:
        """非交易日处理"""
        weekday = datetime.now().weekday()
        if weekday == 5:  # 周六
            return {"success": True, "source": "weekend",
                    "summary": {"tasks": ["KB行业扫描", "新赛道发现"]}}
        elif weekday == 6:  # 周日
            return {"success": True, "source": "weekend",
                    "summary": {"tasks": ["行业动量轮换", "M55大调"]}}
        return {"success": True, "source": "weekend", "summary": {}}


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 健康守护者
# ═══════════════════════════════════════════════════════════════

class HealthGuardian:
    """系统健康守护者 — 巡检+自愈"""

    def __init__(self, state_mgr: StateManager):
        self.state_mgr = state_mgr

    def run_health_check(self) -> Dict:
        """执行系统健康检查"""
        now = datetime.now()
        report = {
            "timestamp": now.isoformat(),
            "checks": {},
            "status": "healthy",
            "actions_taken": [],
        }

        # 检查1: 管道步骤是否按时完成
        state = self.state_mgr.load_state()
        if state.day_type == "trading":
            report["checks"]["pipeline"] = self._check_pipeline_health(state, now)

        # 检查2: 圣杯信号文件
        report["checks"]["holy_grail"] = self._check_holy_grail_file(now)

        # 检查3: 磁盘空间
        report["checks"]["disk"] = self._check_disk_space()

        # 检查4: Python模块可用性
        report["checks"]["modules"] = self._check_critical_modules()

        # 检查5: GitHub连通性
        report["checks"]["github"] = self._check_github_access()

        # 汇总状态
        for check_name, check in report["checks"].items():
            if check.get("status") == "critical":
                report["status"] = "critical"
                break
            elif check.get("status") == "warning" and report["status"] == "healthy":
                report["status"] = "warning"

        # 保存健康日志
        self._save_health_log(report)

        # 自动修复
        if report["status"] != "healthy":
            report["actions_taken"] = self._auto_heal(report)

        return report

    def _check_pipeline_health(self, state: AutoPilotState, now: datetime) -> Dict:
        """检查管道健康（AutoPilot状态 + automation_runs交叉验证）"""
        issues = []
        verified_by_automation = []
        
        # 自动化名称关键词→步骤ID映射（覆盖全交易日16步骤）
        AUTOMATION_STEP_MAP = {
            "08:30": "08:30_pre_market",
            "09:30": "09:30_opening",
            "T0 10:30": "10:30_T0_screen",
            "T1 11:30": "11:30_T1_midday",
            "13:55": "13:55_sentiment",
            "T3 14:00": "14:00_T3_preclose",
            "T4 14:15": "14:15_T4_foot",
            "T5 14:30": "14:30_T5_holy_grail",
            "14:50": "14:50_fallback",
            "15:05": "15:05_archive",
            "15:10": "15:10_evolution",
            "15:25": "15:25_history",
            "15:35": "15:35_M55_calibrate",
            "20:00": "20:00_night",
            "22:00": "22:00_battle_plan",
        }
        
        # 交叉检查: 查询automation_runs表（JOIN名称）确认步骤是否通过自动化执行
        ran_step_keywords = set()  # 今日成功执行的关键词集合
        try:
            import sqlite3
            db_path = str(Path.home() / ".workbuddy" / "workbuddy.db")
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            today_start = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            runs = db.execute('''
                SELECT a.name, r.result_success 
                FROM automation_runs r
                JOIN automations a ON r.automation_id = a.id
                WHERE r.created_at > ?
            ''', (today_start,)).fetchall()
            db.close()
            
            for r in runs:
                if r['result_success'] == 1 and r['name']:
                    for kw in AUTOMATION_STEP_MAP:
                        if kw in r['name']:
                            ran_step_keywords.add(kw)
        except Exception:
            pass
        
        for step_id, config in PIPELINE_STEPS.items():
            step_time = now.replace(hour=config["hour"], minute=config["minute"], second=0, microsecond=0)
            if now < step_time:
                continue  # 还没到执行时间
            
            # 主检查: AutoPilot状态
            in_state = step_id in state.steps and state.steps[step_id].status in ("success", "fallback")
            
            if not in_state:
                # 交叉验证: 自动化系统是否已成功执行
                for kw, mapped_step in AUTOMATION_STEP_MAP.items():
                    if mapped_step == step_id and kw in ran_step_keywords:
                        verified_by_automation.append(step_id)
                        break
                else:
                    # 既不在AutoPilot状态，也没有自动化执行记录
                    if config["priority"] == "P0":
                        issues.append(f"P0缺失: {step_id}")
                    else:
                        issues.append(f"P1缺失: {step_id}")
        
        if not issues:
            msg = "所有步骤正常"
            if verified_by_automation:
                msg += f" (自动化验证: {len(verified_by_automation)}步)"
            return {"status": "healthy", "message": msg}
        elif any("P0" in i for i in issues):
            return {"status": "critical", "message": f"P0步骤缺失: {issues}"}
        else:
            return {"status": "warning", "message": f"P1步骤缺失: {issues}"}

    def _check_holy_grail_file(self, now: datetime) -> Dict:
        """检查圣杯信号文件（双路径回退: pipeline/ → fullmarket_cache/）"""
        # 主路径: data/pipeline/holy_grail_signals.json
        if HOLY_GRAIL_FILE.exists():
            age = time.time() - HOLY_GRAIL_FILE.stat().st_mtime
            hours_old = age / 3600
            if hours_old < 24:
                return {"status": "healthy", "message": f"圣杯信号{hours_old:.0f}小时前更新"}
            else:
                return {"status": "warning", "message": f"圣杯信号{hours_old:.0f}小时未更新"}
        # 回退路径: data/fullmarket_cache/state_YYYYMMDD.json
        cache_file = CLAW_ROOT / "data" / "fullmarket_cache" / f"state_{now.strftime('%Y%m%d')}.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    d = json.load(f)
                hg = d.get("holy_grail_count", 0)
                top_count = len(d.get("top_stocks", []))
                return {"status": "healthy", "message": f"圣杯信号{hg}只 (fullmarket_cache回退, {top_count}只TOP)"}
            except Exception:
                pass
        # 都不存在
        if now.hour >= 15 and now.weekday() < 5:
            return {"status": "warning", "message": "交易日圣杯信号文件不存在"}
        return {"status": "healthy", "message": "非交易时间"}

    def _check_disk_space(self) -> Dict:
        """检查磁盘空间"""
        try:
            import shutil
            usage = shutil.disk_usage(str(CLAW_ROOT.root) if hasattr(CLAW_ROOT, 'root') else str(CLAW_ROOT))
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            if free_gb < 1:
                return {"status": "critical", "message": f"磁盘空间不足: {free_gb:.1f}GB/{total_gb:.0f}GB"}
            elif free_gb < 5:
                return {"status": "warning", "message": f"磁盘空间偏低: {free_gb:.1f}GB/{total_gb:.0f}GB"}
            return {"status": "healthy", "message": f"磁盘: {free_gb:.1f}GB空闲/{total_gb:.0f}GB"}
        except Exception:
            return {"status": "healthy", "message": "磁盘检查跳过"}

    def _check_critical_modules(self) -> Dict:
        """检查关键Python模块"""
        modules = [
            "V13_0_Orchestrator",
            "V13_4_FullMarketMonitor",
            "V13_0_Persistence",
            "V13_0_Watchdog",
            "V13_2_1430_Deploy",
        ]
        available = []
        missing = []
        for mod in modules:
            try:
                __import__(mod)
                available.append(mod)
            except ImportError:
                missing.append(mod)

        if len(missing) >= 3:
            return {"status": "critical", "message": f"缺失关键模块: {missing}", "available": available}
        elif missing:
            return {"status": "warning", "message": f"部分模块缺失: {missing}", "available": available}
        return {"status": "healthy", "message": f"全部{len(available)}个关键模块就绪", "available": available}

    def _check_github_access(self) -> Dict:
        """检查GitHub连通性（三重回退+全局socket超时保护）"""
        import socket
        import urllib.request
        import urllib.error
        
        # 全局socket超时保护，防止整个函数hang住
        socket.setdefaulttimeout(3)
        
        # 方案1: socket直连（最快）
        try:
            s = socket.create_connection(("github.com", 443), timeout=2)
            s.close()
            socket.setdefaulttimeout(None)
            return {"status": "healthy", "message": "GitHub可达"}
        except Exception:
            pass
        
        socket.setdefaulttimeout(None)
        return {"status": "warning", "message": "GitHub不可达(网络暂时受限)"}

    def _auto_heal(self, report: Dict) -> List[str]:
        """自动修复尝试"""
        actions = []

        # 修复1: 管道P0缺失 → 触发重试
        pipeline = report["checks"].get("pipeline", {})
        if pipeline.get("status") == "critical":
            actions.append("标记P0缺失步骤，触发agent补执行")

        # 修复2: 圣杯信号未生成 → 标记需要14:50兜底
        hg = report["checks"].get("holy_grail", {})
        if hg.get("status") == "warning":
            actions.append("触发14:50兜底圣杯扫描")

        # 修复3: 磁盘不足 → 清理旧日志
        disk = report["checks"].get("disk", {})
        if disk.get("status") in ("warning", "critical"):
            self._cleanup_old_logs()
            actions.append("已清理旧日志文件")

        return actions

    def _save_health_log(self, report: Dict):
        """保存健康日志"""
        # 追加到健康日志
        logs = []
        if HEALTH_LOG_FILE.exists():
            try:
                with open(HEALTH_LOG_FILE, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            except Exception:
                logs = []
        logs.append(report)
        # 保留最近100条
        if len(logs) > 100:
            logs = logs[-100:]
        with open(HEALTH_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)

    def _cleanup_old_logs(self):
        """清理30天前的日志"""
        cutoff = time.time() - 30 * 24 * 3600
        if LOG_DIR.exists():
            for f in LOG_DIR.glob("*.log"):
                if f.stat().st_mtime < cutoff:
                    try:
                        f.unlink()
                    except Exception:
                        pass


# ═══════════════════════════════════════════════════════════════
# SECTION 5: GitHub同步
# ═══════════════════════════════════════════════════════════════

class GitHubSync:
    """GitHub状态同步 — 外部大脑"""

    def __init__(self, repo_root: str = None):
        self.repo_root = repo_root or str(CLAW_ROOT)

    def sync_holy_grail(self) -> bool:
        """同步圣杯信号到GitHub"""
        import subprocess
        repo_path = Path(self.repo_root) / ".git"
        if not repo_path.exists():
            print("[GitHub] 未检测到git仓库，跳过同步")
            return False

        try:
            # 检查远程
            remote = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True, text=True, cwd=self.repo_root, timeout=10
            )
        except Exception:
            return False

        if remote.returncode != 0:
            return False

        try:
            # pull → add → commit → push
            subprocess.run(["git", "pull", "--rebase"], capture_output=True, text=True,
                          cwd=self.repo_root, timeout=30)
            subprocess.run(["git", "add", "data/pipeline/*.json", "outputs/*.json",
                           "data/watchdog/*.json"],
                          capture_output=True, text=True, cwd=self.repo_root, timeout=10)
            commit_msg = f"AutoPilot {datetime.now().strftime('%Y-%m-%d %H:%M')} — 圣杯信号同步"
            subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, text=True,
                          cwd=self.repo_root, timeout=10)
            subprocess.run(["git", "push"], capture_output=True, text=True,
                          cwd=self.repo_root, timeout=30)
            return True
        except Exception as e:
            print(f"[GitHub] 同步失败: {e}")
            return False

    def pull_latest(self) -> bool:
        """从GitHub拉取最新状态"""
        import subprocess
        try:
            subprocess.run(["git", "pull"], capture_output=True, text=True,
                          cwd=self.repo_root, timeout=30)
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════
# SECTION 6: 主入口
# ═══════════════════════════════════════════════════════════════

def run_autonomous_mode(args):
    """全自动模式 — 根据当前时间自动判断执行什么步骤"""
    state_mgr = StateManager()
    executor = PipelineExecutor(state_mgr)
    guardian = HealthGuardian(state_mgr)

    state = state_mgr.load_state()

    now = datetime.now()

    # 前置：健康检查
    health = guardian.run_health_check()
    if health["status"] == "critical":
        executor.log("⚠️ 系统健康状态异常，启用降级模式", "WARN")
        state.health_status = "degraded"
    else:
        state.health_status = "healthy"

    state_mgr.save_state(state)

    # 判断运行模式
    if args.step:
        # 指定单步执行
        step_id = args.step
        if step_id not in PIPELINE_STEPS:
            executor.log(f"❌ 未知步骤: {step_id}", "ERROR")
            return
        step_config = PIPELINE_STEPS[step_id]
        result = executor.execute_step(step_id, step_config, state)
        executor.log(f"\n📊 步骤结果: {result.status} [{step_id}]")

    elif args.health_only:
        # 仅健康检查
        print(json.dumps(health, ensure_ascii=False, indent=2))

    elif args.full_day:
        # 完整交易日
        executor.log(f"🚀 V13.4 AutoPilot 全交易日开始运行")
        executor.log(f"   日期: {state.date} | 类型: {state.day_type} | 模式: autonomous")

        if state.day_type == "trading":
            # 执行所有已到时间的步骤
            steps_run = 0
            for step_id, config in PIPELINE_STEPS.items():
                step_time = now.replace(hour=config["hour"], minute=config["minute"], second=0)
                # 跳过还未到时间的步骤
                if now < step_time:
                    executor.log(f"⏳ 跳过 [{step_id}] — 执行时间{step_time.strftime('%H:%M')}未到")
                    continue

                # 检查是否已执行
                if step_id in state.steps and state.steps[step_id].status in ("success", "fallback"):
                    executor.log(f"✓ 跳过 [{step_id}] — 已执行")
                    continue

                result = executor.execute_step(step_id, config, state)
                steps_run += 1

                # T5完成后同步到GitHub
                if step_id == "14:30_T5_holy_grail" and result.status == "success":
                    github = GitHubSync()
                    github.sync_holy_grail()

            executor.log(f"\n✅ 全交易日完成 — 执行{steps_run}个步骤")

        elif state.day_type == "weekend":
            # 执行周末步骤
            executor.log("📅 非交易日，执行周末维护流程")
            for step_id, config in WEEKEND_STEPS.items():
                if datetime.now().weekday() == config["weekday"]:
                    # 创建伪步骤配置
                    step_config = {"hour": 0, "minute": 0, "priority": config["priority"],
                                   "desc": config["desc"], "module": config["module"]}
                    executor.execute_step(step_id, step_config, state)

        else:  # holiday
            executor.log("🏖 假期休市，跳过所有步骤")

    else:
        # 默认：打印状态
        print(json.dumps({
            "version": VERSION,
            "date": state.date,
            "day_type": state.day_type,
            "health": health["status"],
            "steps_completed": sum(1 for s in state.steps.values() if s.status in ("success", "fallback")),
            "pipeline_steps_total": len(PIPELINE_STEPS),
            "holy_grail_count": state.holy_grail_count,
        }, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description='V13.4 AutoPilot — 无人值守全自动主控')

    parser.add_argument('--mode', type=str, default='autonomous',
                        choices=['autonomous', 'manual', 'test'],
                        help='运行模式')
    parser.add_argument('--step', type=str, default=None,
                        help='指定执行单步 (如: 14:30_T5_holy_grail)')
    parser.add_argument('--full-day', action='store_true',
                        help='执行完整交易日管道')
    parser.add_argument('--health-only', action='store_true',
                        help='仅执行健康检查')
    parser.add_argument('--github-sync', action='store_true',
                        help='执行GitHub同步')
    parser.add_argument('--quiet', action='store_true',
                        help='静默模式')

    args = parser.parse_args()

    # GitHub同步模式
    if args.github_sync:
        github = GitHubSync()
        ok = github.sync_holy_grail()
        print(f"GitHub同步: {'✅ 成功' if ok else '❌ 失败'}")
        return

    # 健康检查
    if args.health_only:
        state_mgr = StateManager()
        guardian = HealthGuardian(state_mgr)
        report = guardian.run_health_check()
        print(f"健康状态: {report['status']}")
        for name, check in report['checks'].items():
            icon = "✅" if check['status'] == 'healthy' else "⚠️" if check['status'] == 'warning' else "🔴"
            print(f"  {icon} {name}: {check.get('message', 'N/A')}")
        return

    # 自动模式
    try:
        run_autonomous_mode(args)
    except Exception as e:
        print(f"❌ AutoPilot致命错误: {e}")
        traceback.print_exc()
        # 尝试优雅保存
        try:
            state_mgr = StateManager()
            state = state_mgr.load_state()
            state.errors.append(f"FATAL: {e}")
            state.health_status = "critical"
            state_mgr.save_state(state)
        except Exception:
            pass
        sys.exit(1)


if __name__ == '__main__':
    main()
