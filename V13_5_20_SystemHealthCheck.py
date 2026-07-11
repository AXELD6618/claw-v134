#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.20 系统健康巡检 + 自愈脚本
================================
9项巡检 + 自动修复，生成 Markdown 报告。

作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.20
日期: 2026-07-06
"""

import os
import sys
import json
import sqlite3
import shutil
import subprocess
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Any

# ────────────────────────────────────────────────
# 配置
# ────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUTS_DIR = ROOT / "outputs"
CACHE_DIR = DATA_DIR / "fullmarket_cache"
DB_PATH = DATA_DIR / "holy_grail.db"
REPORT_DIR = OUTPUTS_DIR
TODAY = datetime.now().strftime("%Y%m%d")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 巡检报告结构
report = {
    "version": "V13.5.20",
    "check_time": NOW,
    "overall_status": "GOOD",  # GOOD / NORMAL / WARNING / CRITICAL
    "checks": {},
    "issues": [],
    "healing_actions": [],
    "recommendations": [],
}


def log(msg: str, level: str = "INFO"):
    icons = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌", "PROGRESS": "🔄"}
    icon = icons.get(level, "ℹ️")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {icon} {msg}")


def add_issue(check_name: str, severity: str, message: str):
    report["issues"].append({
        "check": check_name,
        "severity": severity,
        "message": message,
    })
    if severity == "CRITICAL":
        report["overall_status"] = "CRITICAL"
    elif severity == "WARNING" and report["overall_status"] not in ("CRITICAL",):
        report["overall_status"] = "WARNING"
    elif severity == "NORMAL" and report["overall_status"] == "GOOD":
        report["overall_status"] = "NORMAL"


def add_healing(action: str):
    report["healing_actions"].append(action)


def add_recommendation(rec: str):
    report["recommendations"].append(rec)


def status_icon(status: str) -> str:
    return {"GOOD": "🟢", "NORMAL": "🟡", "WARNING": "🟠", "CRITICAL": "🔴"}.get(status, "⚪")


# ═══════════════════════════════════════════════════════════
# 1. 管道状态检查
# ═══════════════════════════════════════════════════════════
def check_pipelines():
    check_name = "管道状态"
    log("检查管道状态...", "PROGRESS")
    try:
        # 尝试从 workbuddy.db 读取自动化状态
        wb_db = Path(os.path.expanduser("~/.workbuddy/workbuddy.db"))
        result = {"status": "GOOD", "active": 0, "paused": 0, "deleted": 0}
        details = []

        if wb_db.exists():
            conn = sqlite3.connect(wb_db)
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, status FROM automations")
            for row in cursor.fetchall():
                aid, name, status = row
                if status == "ACTIVE":
                    result["active"] += 1
                elif status == "PAUSED":
                    result["paused"] += 1
                elif status == "DELETED":
                    result["deleted"] += 1
                details.append({"id": aid[:20], "name": name[:60], "status": status})
            conn.close()
            result["total"] = result["active"] + result["paused"] + result["deleted"]
            result["details"] = details[:15]
        else:
            add_issue(check_name, "WARNING", f"workbuddy.db 不存在: {wb_db}")
            result["status"] = "WARNING"

        expected_active = 22
        # DB 中保留历史软删除/归档记录, PAUSED/DELETED 数量会高于活动视图
        if result["active"] != expected_active:
            add_issue(check_name, "WARNING",
                      f"ACTIVE 自动化数量不匹配: 当前 {result['active']} (预期 {expected_active}); "
                      f"DB总记录 {result['total']} ({result['paused']}P/{result['deleted']}D 含历史归档)")
            result["status"] = "WARNING"
        else:
            log(f"管道状态正常: {result['active']} ACTIVE / DB总计 {result['total']} (含历史归档 {result['paused']}P/{result['deleted']}D)", "SUCCESS")

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 2. 圣杯信号文件检查
# ═══════════════════════════════════════════════════════════
def check_holy_grail_signals():
    check_name = "圣杯信号"
    log("检查圣杯信号文件...", "PROGRESS")
    try:
        # 检查最新 state 文件
        state_files = sorted(CACHE_DIR.glob("state_*.json"))
        latest_state = None
        latest_mtime = 0
        for sf in state_files:
            if "before" in sf.name or "expanded" in sf.name or "latest" in sf.name:
                continue
            mtime = sf.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_state = sf

        result = {"status": "GOOD"}

        if not latest_state:
            add_issue(check_name, "CRITICAL", "未找到任何 state_YYYYMMDD.json 圣杯信号文件")
            result["status"] = "CRITICAL"
        else:
            age_hours = (datetime.now() - datetime.fromtimestamp(latest_mtime)).total_seconds() / 3600
            result["latest_file"] = str(latest_state)
            result["age_hours"] = round(age_hours, 2)
            try:
                with open(latest_state, "r", encoding="utf-8") as f:
                    data = json.load(f)
                result["file_size_kb"] = round(latest_state.stat().st_size / 1024, 2)
                if isinstance(data, dict):
                    if "summary" in data:
                        result["top_candidates"] = len(data["summary"])
                        result["holy_grail_count"] = data.get("holy_grail_count", 0)
                        result["total_stocks"] = data.get("total_stocks", 0)
                    else:
                        result["top_candidates"] = len(data)
                    version = data.get("version", data.get("v", "UNKNOWN"))
                else:
                    result["top_candidates"] = len(data)
                    version = "UNKNOWN"
                result["version"] = version

                if age_hours > 72:
                    add_issue(check_name, "CRITICAL",
                              f"圣杯信号严重过期: {latest_state.name} 已 {age_hours:.1f} 小时未更新 (版本 {version})")
                    result["status"] = "CRITICAL"
                elif age_hours > 24:
                    add_issue(check_name, "WARNING",
                              f"圣杯信号较旧: {latest_state.name} 已 {age_hours:.1f} 小时未更新")
                    result["status"] = "WARNING"
                else:
                    log(f"圣杯信号正常: {latest_state.name}, {age_hours:.1f}h, {result['top_candidates']} 候选", "SUCCESS")
            except Exception as e:
                add_issue(check_name, "CRITICAL", f"读取最新信号文件失败: {e}")
                result["status"] = "CRITICAL"

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 3. 磁盘空间检查
# ═══════════════════════════════════════════════════════════
def check_disk_space():
    check_name = "磁盘空间"
    log("检查磁盘空间...", "PROGRESS")
    try:
        result = {}
        for drive in ["C:", "E:"]:
            if os.path.exists(drive):
                total, used, free = shutil.disk_usage(drive)
                pct_free = free / total * 100
                result[drive] = {
                    "total_gb": round(total / (1024 ** 3), 2),
                    "free_gb": round(free / (1024 ** 3), 2),
                    "free_pct": round(pct_free, 2),
                }
                if pct_free < 10:
                    add_issue(check_name, "CRITICAL", f"{drive} 仅剩 {pct_free:.1f}% 空间 ({free / (1024 ** 3):.1f} GB)")
                elif pct_free < 20:
                    add_issue(check_name, "WARNING", f"{drive} 空间偏少: {pct_free:.1f}%")

        c_pct = result.get("C:", {}).get("free_pct", 100)
        e_pct = result.get("E:", {}).get("free_pct", 100)
        if c_pct >= 10 and e_pct >= 10:
            log(f"磁盘空间正常: C:{c_pct}% / E:{e_pct}%", "SUCCESS")
            result["status"] = "GOOD"
        else:
            result["status"] = report["checks"].get(check_name, {}).get("status", "WARNING")

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 4. 模块可用性检查
# ═══════════════════════════════════════════════════════════
def check_modules():
    check_name = "模块可用性"
    log("检查核心模块可导入性...", "PROGRESS")

    modules_to_check = [
        ("V13_5_M71_ReversalPredictor", "M71"),
        ("V13_4_M55_DailyCalibration_1535", "M55"),
        ("V13_3_M70_LightGBM", "M70"),
        ("V13_2_M46_Normalized", "M46"),
        ("V13_5_TDX_EnhancedFeeder", "TDX增强馈送"),
        ("V13_5_ShuDao_Screener", "蜀道扫描器"),
        ("V13_5_M71_D37_D46", "D37-D46经典理论"),
    ]

    result = {"modules": [], "status": "GOOD"}
    failed = []
    for module_name, alias in modules_to_check:
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, ROOT / f"{module_name}.py"
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"找不到模块文件 {module_name}.py")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            result["modules"].append({"name": alias, "file": f"{module_name}.py", "status": "OK"})
            log(f"模块导入成功: {alias}", "SUCCESS")
        except Exception as e:
            failed.append((alias, str(e)))
            result["modules"].append({"name": alias, "file": f"{module_name}.py", "status": "FAIL", "error": str(e)})
            log(f"模块导入失败: {alias} - {e}", "ERROR")

    if failed:
        add_issue(check_name, "CRITICAL" if len(failed) >= 3 else "WARNING",
                  f"{len(failed)} 个核心模块导入失败: " + ", ".join([f[0] for f in failed]))
        result["status"] = "CRITICAL" if len(failed) >= 3 else "WARNING"
    else:
        result["status"] = "GOOD"

    report["checks"][check_name] = result


# ═══════════════════════════════════════════════════════════
# 5. TDX 连通性检查 (依赖 MCP, 这里只检查本地适配器)
# ═══════════════════════════════════════════════════════════
def check_tdx_connectivity():
    check_name = "TDX连通性"
    log("检查 TDX 连接器本地状态...", "PROGRESS")
    try:
        # 检查本地 TdxClaw 技能文件
        tdx_skill_file = ROOT / "V13_5_TdxClaw_Skills_AllInOne.py"
        feeder_file = ROOT / "V13_5_TDX_EnhancedFeeder.py"
        result = {
            "tdx_skill_exists": tdx_skill_file.exists(),
            "feeder_exists": feeder_file.exists(),
            "status": "GOOD",
        }

        # 尝试导入并列出 TDX_14_TOOLS
        try:
            spec = importlib.util.spec_from_file_location("V13_5_TDX_EnhancedFeeder", feeder_file)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["V13_5_TDX_EnhancedFeeder"] = mod
                spec.loader.exec_module(mod)
                tools = getattr(mod, "TDX_14_TOOLS", {})
                result["tdx_14_tools_defined"] = len(tools)
                result["tool_names"] = list(tools.keys())
                if len(tools) != 14:
                    add_issue(check_name, "WARNING",
                              f"TDX_14_TOOLS 定义数量异常: {len(tools)} (预期 14)")
                    result["status"] = "WARNING"
        except Exception as e:
            add_issue(check_name, "WARNING", f"读取 TDX_14_TOOLS 失败: {e}")
            result["status"] = "WARNING"

        if result["status"] == "GOOD":
            log(f"TDX 本地连接器正常: {result.get('tdx_14_tools_defined', 0)} 工具定义", "SUCCESS")

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 6. GitHub 连通性检查
# ═══════════════════════════════════════════════════════════
def check_github_connectivity():
    check_name = "GitHub连通性"
    log("检查 GitHub PAT 有效性...", "PROGRESS")
    try:
        token = None
        env_file = ROOT / ".env"
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GITHUB_TOKEN="):
                        token = line.strip().split("=", 1)[1]
                        break

        result = {"token_found": bool(token), "status": "GOOD"}

        if not token:
            add_issue(check_name, "WARNING", "未找到 .env 中的 GITHUB_TOKEN")
            result["status"] = "WARNING"
        else:
            # 使用 curl 测试 GitHub API
            cmd = [
                "curl", "-s", "-o", "NUL", "-w", "%{http_code}",
                "-H", f"Authorization: token {token}",
                "https://api.github.com/user"
            ]
            try:
                # Windows 下 NUL, Linux 下 /dev/null
                output = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                http_code = output.stdout.strip()
                result["http_code"] = http_code
                if http_code == "200":
                    log("GitHub PAT 有效", "SUCCESS")
                elif http_code == "401":
                    add_issue(check_name, "CRITICAL", "GitHub PAT 已失效/未授权 (HTTP 401)")
                    result["status"] = "CRITICAL"
                else:
                    add_issue(check_name, "WARNING", f"GitHub API 返回异常状态码: {http_code}")
                    result["status"] = "WARNING"
            except Exception as e:
                add_issue(check_name, "WARNING", f"GitHub 连通性测试失败: {e}")
                result["status"] = "WARNING"

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 7. 五确认体系检查
# ═══════════════════════════════════════════════════════════
def check_five_confirmation():
    check_name = "五确认体系"
    log("检查五确认体系数据库表...", "PROGRESS")
    try:
        result = {"db_exists": DB_PATH.exists(), "status": "GOOD"}
        if not DB_PATH.exists():
            add_issue(check_name, "CRITICAL", f"数据库不存在: {DB_PATH}")
            result["status"] = "CRITICAL"
        else:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]
            result["tables"] = tables

            required_tables = ["four_confirm_snapshots", "daily_signals", "reward_records", "evolution_params", "evolution_records"]
            missing = [t for t in required_tables if t not in tables]
            if missing:
                add_issue(check_name, "CRITICAL", f"五确认相关表缺失: {missing}")
                result["status"] = "CRITICAL"
            else:
                # 检查 four_confirm_snapshots 最新记录 (V13.5.19升级后的五确认体系)
                cursor.execute("SELECT COUNT(*), MAX(created_at) FROM four_confirm_snapshots")
                count, max_dt = cursor.fetchone()
                result["snapshot_count"] = count
                result["latest_snapshot"] = max_dt
                if count == 0:
                    add_issue(check_name, "WARNING", "four_confirm_snapshots 表为空")
                    result["status"] = "WARNING"
                elif max_dt:
                    latest_dt = datetime.fromisoformat(max_dt.replace("Z", "+00:00").replace(" ", "T"))
                    age_hours = (datetime.now() - latest_dt).total_seconds() / 3600
                    result["snapshot_age_hours"] = round(age_hours, 2)
                    if age_hours > 48:
                        add_issue(check_name, "WARNING", f"五确认快照已 {age_hours:.1f}h 未更新")
                        result["status"] = "WARNING"
                    else:
                        log(f"五确认体系正常: {count} 条快照, 最新 {max_dt}", "SUCCESS")
                conn.close()

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 8. 蜀道模式检查
# ═══════════════════════════════════════════════════════════
def check_shudao_screener():
    check_name = "蜀道模式"
    log("检查蜀道模式扫描器输出...", "PROGRESS")
    try:
        screener_files = sorted(DATA_DIR.glob("shudao_screener_*.json"))
        result = {"status": "GOOD"}
        if not screener_files:
            add_issue(check_name, "WARNING", "未找到 shudao_screener_YYYYMMDD.json 输出文件")
            result["status"] = "WARNING"
            result["d34_candidates"] = 0
        else:
            latest = screener_files[-1]
            result["latest_file"] = str(latest)
            try:
                with open(latest, "r", encoding="utf-8") as f:
                    data = json.load(f)
                candidates = data if isinstance(data, list) else data.get("candidates", [])
                d34_count = sum(1 for c in candidates if c.get("d34_score", 0) >= 3)
                result["total_candidates"] = len(candidates)
                result["d34_candidates"] = d34_count
                age_hours = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).total_seconds() / 3600
                result["age_hours"] = round(age_hours, 2)
                if age_hours > 48:
                    add_issue(check_name, "WARNING", f"蜀道扫描器输出过期: {latest.name} 已 {age_hours:.1f}h 未更新")
                    result["status"] = "WARNING"
                else:
                    log(f"蜀道模式正常: {latest.name}, {len(candidates)} 候选, D34≥3: {d34_count}", "SUCCESS")
            except Exception as e:
                add_issue(check_name, "CRITICAL", f"读取蜀道扫描器输出失败: {e}")
                result["status"] = "CRITICAL"

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 9. D37-D46 数据可用性检查
# ═══════════════════════════════════════════════════════════
def check_d37_d46():
    check_name = "D37-D46"
    log("检查 D37-D46 经典交易理论模块...", "PROGRESS")
    try:
        module_file = ROOT / "V13_5_M71_D37_D46.py"
        result = {"module_exists": module_file.exists(), "status": "GOOD"}
        if not module_file.exists():
            add_issue(check_name, "CRITICAL", "V13_5_M71_D37_D46.py 模块不存在")
            result["status"] = "CRITICAL"
        else:
            # 检查模块中是否定义了 D37-D46 维度
            try:
                spec = importlib.util.spec_from_file_location("V13_5_M71_D37_D46", module_file)
                if spec and spec.loader:
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules["V13_5_M71_D37_D46"] = mod
                    spec.loader.exec_module(mod)
                    dims = []
                    # 检查批量评分入口
                    has_batch = hasattr(mod, "score_all_new_dimensions")
                    result["has_batch_scorer"] = has_batch
                    # 检查 NewDimensionScorer 类及10个评分方法
                    scorer_cls = getattr(mod, "NewDimensionScorer", None)
                    if scorer_cls:
                        expected_methods = [
                            "score_weekly_ma_alignment",      # D37
                            "score_weekly_platform_breakout", # D38
                            "score_weekly_macd_golden_cross", # D39
                            "score_weekly_pullback_support",  # D40
                            "score_weekly_macd_divergence",   # D41
                            "score_duck_head_pattern",        # D42
                            "score_chip_dragon",              # D43
                            "score_triple_volume_breakout",   # D44
                            "score_trial_line",               # D45
                            "score_three_ma_warfare",         # D46
                        ]
                        for m in expected_methods:
                            if hasattr(scorer_cls, m):
                                dims.append(m)
                    result["dimensions_defined"] = dims
                    if len(dims) < 10:
                        add_issue(check_name, "WARNING",
                                  f"D37-D46 维度定义不完整: 仅 {len(dims)}/10 ({dims})")
                        result["status"] = "WARNING"
                    else:
                        log(f"D37-D46 模块正常: {len(dims)} 维度已定义", "SUCCESS")
            except Exception as e:
                add_issue(check_name, "CRITICAL", f"加载 D37-D46 模块失败: {e}")
                result["status"] = "CRITICAL"

        report["checks"][check_name] = result
    except Exception as e:
        add_issue(check_name, "CRITICAL", f"检查失败: {e}")
        report["checks"][check_name] = {"status": "CRITICAL", "error": str(e)}


# ═══════════════════════════════════════════════════════════
# 自愈逻辑
# ═══════════════════════════════════════════════════════════
def self_heal():
    log("评估自愈动作...", "PROGRESS")
    # 1. 若五确认表缺失，尝试创建 (兼容 V13.5.19 四确认/五确认命名)
    if "four_confirm_snapshots" not in report["checks"].get("五确认体系", {}).get("tables", []):
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS four_confirm_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    d29_score REAL,
                    d31_score REAL,
                    d32_score REAL,
                    d33_score REAL,
                    d34_score REAL,
                    confirm_level TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
            add_healing("已创建 four_confirm_snapshots 表")
        except Exception as e:
            add_healing(f"创建 four_confirm_snapshots 表失败: {e}")

    # 2. 若 state_latest.json 不存在但最新 state_YYYYMMDD.json 存在，创建软链接/副本
    latest_state = None
    latest_mtime = 0
    for sf in CACHE_DIR.glob("state_*.json"):
        if "before" in sf.name or "expanded" in sf.name or "latest" in sf.name:
            continue
        mtime = sf.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest_state = sf
    state_latest = CACHE_DIR / "state_latest.json"
    if latest_state and (not state_latest.exists() or state_latest.stat().st_mtime < latest_mtime):
        try:
            shutil.copy2(latest_state, state_latest)
            add_healing(f"已更新 state_latest.json → {latest_state.name}")
        except Exception as e:
            add_healing(f"更新 state_latest.json 失败: {e}")

    # 3. 清理过期备份文件 (保留最近5个)
    try:
        backups = sorted(CACHE_DIR.glob("state_*_before_*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        for old in backups[5:]:
            old.unlink()
        if len(backups) > 5:
            add_healing(f"已清理 {len(backups) - 5} 个过期 state 备份")
    except Exception as e:
        add_healing(f"清理过期备份失败: {e}")


# ═══════════════════════════════════════════════════════════
# 生成报告
# ═══════════════════════════════════════════════════════════
def generate_report():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORT_DIR / f"V13520_health_check_{TODAY}.md"

    lines = []
    lines.append(f"# V13.5.20 系统健康巡检报告\n")
    lines.append(f"- **巡检时间**: {NOW}\n")
    lines.append(f"- **系统版本**: {report['version']}\n")
    lines.append(f"- **总体状态**: {status_icon(report['overall_status'])} {report['overall_status']}\n")
    lines.append(f"- **巡检项数**: 9\n\n")

    lines.append("## 巡检结果汇总\n\n")
    lines.append("| 序号 | 巡检项 | 状态 | 关键指标 |\n")
    lines.append("|------|--------|------|----------|\n")
    order = [
        ("1", "管道状态"),
        ("2", "圣杯信号"),
        ("3", "磁盘空间"),
        ("4", "模块可用性"),
        ("5", "TDX连通性"),
        ("6", "GitHub连通性"),
        ("7", "五确认体系"),
        ("8", "蜀道模式"),
        ("9", "D37-D46"),
    ]
    for idx, name in order:
        data = report["checks"].get(name, {})
        status = data.get("status", "UNKNOWN")
        if name == "管道状态":
            metric = f"{data.get('active', 0)}A/{data.get('paused', 0)}P/{data.get('deleted', 0)}D"
        elif name == "圣杯信号":
            metric = f"{data.get('age_hours', '-')}h / {data.get('top_candidates', '-')} 候选"
        elif name == "磁盘空间":
            metric = f"C:{data.get('C:', {}).get('free_pct', '-')}%, E:{data.get('E:', {}).get('free_pct', '-')}%"
        elif name == "模块可用性":
            mods = data.get("modules", [])
            ok = sum(1 for m in mods if m.get("status") == "OK")
            metric = f"{ok}/{len(mods)} OK"
        elif name == "TDX连通性":
            metric = f"{data.get('tdx_14_tools_defined', '-')} 工具定义"
        elif name == "GitHub连通性":
            metric = f"Token: {'有' if data.get('token_found') else '无'}, HTTP: {data.get('http_code', 'N/A')}"
        elif name == "五确认体系":
            metric = f"{data.get('snapshot_count', '-')} 条 / {data.get('snapshot_age_hours', '-')}h"
        elif name == "蜀道模式":
            metric = f"{data.get('d34_candidates', '-')} D34候选 / {data.get('age_hours', '-')}h"
        elif name == "D37-D46":
            metric = f"{len(data.get('dimensions_defined', []))}/10 维度"
        else:
            metric = "-"
        lines.append(f"| {idx} | {name} | {status_icon(status)} {status} | {metric} |\n")

    lines.append("\n## 详细检查结果\n")
    for idx, name in order:
        lines.append(f"\n### {idx}. {name}\n")
        lines.append(f"```json\n{json.dumps(report['checks'].get(name, {}), ensure_ascii=False, indent=2)}\n```\n")

    if report["issues"]:
        lines.append("\n## 发现的问题\n\n")
        for issue in report["issues"]:
            lines.append(f"- {status_icon(issue['severity'])} **[{issue['severity']}]** {issue['check']}: {issue['message']}\n")
    else:
        lines.append("\n## 发现的问题\n\n未发现异常。\n")

    if report["healing_actions"]:
        lines.append("\n## 自愈执行记录\n\n")
        for action in report["healing_actions"]:
            lines.append(f"- {action}\n")
    else:
        lines.append("\n## 自愈执行记录\n\n无需自愈。\n")

    if report["recommendations"]:
        lines.append("\n## 建议\n\n")
        for rec in report["recommendations"]:
            lines.append(f"- {rec}\n")

    with open(report_file, "w", encoding="utf-8") as f:
        f.writelines(lines)

    # 同时保存 JSON
    json_file = REPORT_DIR / f"V13520_health_check_{TODAY}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log(f"报告已生成: {report_file}", "SUCCESS")
    return report_file, json_file


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════
def main():
    log("=" * 60)
    log("V13.5.20 系统健康巡检启动")
    log("=" * 60)

    check_pipelines()
    check_holy_grail_signals()
    check_disk_space()
    check_modules()
    check_tdx_connectivity()
    check_github_connectivity()
    check_five_confirmation()
    check_shudao_screener()
    check_d37_d46()

    self_heal()

    # 综合建议
    if report["overall_status"] in ("WARNING", "CRITICAL"):
        add_recommendation("请优先处理 CRITICAL/WARNING 级别问题，确保开盘前系统可用。")
    if report["checks"].get("圣杯信号", {}).get("age_hours", 0) > 24:
        add_recommendation("建议手动触发 08:30 盘前快照或 T0 10:30 全市场初筛以刷新信号。")
    if report["checks"].get("GitHub连通性", {}).get("http_code") != "200":
        add_recommendation("请检查 .env 中的 GITHUB_TOKEN 是否过期，必要时重新生成 PAT。")

    report_file, json_file = generate_report()

    log("=" * 60)
    log(f"巡检完成 — 总体状态: {report['overall_status']}")
    log("=" * 60)
    return report_file, json_file


if __name__ == "__main__":
    main()
