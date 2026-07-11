#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.20 降级/容错检查脚本
==========================
按用户指定10项检查清单执行：
1. checkpoint.json 状态
2. failed步骤 → 数据完整性验证 + 备份恢复 / needs_manual
3. T0/T3/T4步骤文件 JSON 有效性
4. 46维度评分文件完整性 (D1-D46)
5. 五确认体系标注完整性 (D29+D31+D32+D33+D34)
6. fundamental_data 缓存 (tdx_indicator_select)
7. tdx_ai_listening 舆情缓存
8. wenda_notice_query 公告催化缓存
9. 蜀道模式扫描缓存 (shudao_screener_YYYYMMDD.json)
10. D37-D46 周线/形态数据完整性 (tdx_kline wantNum>=200)

输出：outputs/V13520_degradation_check_YYYYMMDD.json + alert.json
"""

import os
import sys
import json
import sqlite3
import shutil
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Tuple

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "fullmarket_cache"
PIPELINE_DIR = DATA_DIR / "pipeline"
OUTPUTS_DIR = ROOT / "outputs"
DB_PATH = DATA_DIR / "holy_grail.db"
BACKUP_DIR = DATA_DIR / "backups"

TODAY = datetime.now().strftime("%Y%m%d")
TODAY_ISO = datetime.now().strftime("%Y-%m-%d")
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 检查报告
report = {
    "version": "V13.5.20",
    "check_type": "degradation_fault_tolerance",
    "check_time": NOW,
    "overall_status": "HEALTHY",  # HEALTHY / DEGRADED / CRITICAL
    "checks": {},
    "issues": [],
    "healing_actions": [],
    "needs_manual": [],
    "alert": None,
}


def log(msg: str, level: str = "INFO"):
    icons = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERROR": "❌", "PROGRESS": "🔄"}
    icon = icons.get(level, "ℹ️")
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {icon} {msg}")


def add_issue(check: str, severity: str, message: str, needs_manual: bool = False,
              auto_resolve: str = None, suggestion: str = None):
    issue = {
        "id": f"{check}_{len(report['issues']) + 1}",
        "check": check,
        "severity": severity,
        "message": message,
        "needs_manual": needs_manual,
    }
    if auto_resolve:
        issue["auto_resolve_on"] = auto_resolve
    if suggestion:
        issue["suggestion"] = suggestion
    report["issues"].append(issue)
    if needs_manual:
        report["needs_manual"].append(issue)
        report["overall_status"] = "CRITICAL"
    elif severity == "CRITICAL" and report["overall_status"] != "CRITICAL":
        # 非人工介入的 CRITICAL（如数据损坏但可自动恢复）先降级为 DEGRADED，
        # 最终报告只在 needs_manual>0 时标 CRITICAL
        report["overall_status"] = "DEGRADED"
    elif severity == "WARNING" and report["overall_status"] == "HEALTHY":
        report["overall_status"] = "DEGRADED"
    return issue


def add_healing(action: str):
    report["healing_actions"].append({"time": datetime.now().isoformat(), "action": action})
    log(action, "OK")


def load_json(path: Path) -> Tuple[bool, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return True, json.load(f)
    except Exception as e:
        return False, str(e)


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def market_trading_today() -> bool:
    wd = datetime.now().weekday()
    return wd < 5  # 周一到周五


# ═══════════════════════════════════════════════════════════
# 1. checkpoint.json 状态
# ═══════════════════════════════════════════════════════════
def check_checkpoint():
    check = "checkpoint"
    log("[1/10] 读取 data/pipeline/checkpoint.json ...", "PROGRESS")
    cp_path = PIPELINE_DIR / "checkpoint.json"
    result = {"file_exists": cp_path.exists()}

    if not cp_path.exists():
        add_issue(check, "CRITICAL", "checkpoint.json 不存在", needs_manual=True,
                  suggestion="手动创建 checkpoint.json 或从备份恢复")
        result["status"] = "CRITICAL"
        report["checks"][check] = result
        return

    ok, data = load_json(cp_path)
    if not ok:
        add_issue(check, "CRITICAL", f"checkpoint.json 解析失败: {data}", needs_manual=True)
        result["status"] = "CRITICAL"
        report["checks"][check] = result
        return

    result["status"] = "OK"
    result["check_type"] = data.get("check_type", "UNKNOWN")
    result["check_timestamp"] = data.get("check_timestamp", "UNKNOWN")
    result["overall_health"] = data.get("overall_health", "UNKNOWN")
    result["needs_manual_count"] = data.get("needs_manual_count", 0)

    steps = data.get("steps_checked", {})
    failed_steps = []
    stale_steps = []
    for step, info in steps.items():
        status = str(info.get("status", "")).lower()
        if "fail" in status or "critical" in status or info.get("needs_rebuild"):
            failed_steps.append({"step": step, "status": info.get("status"), "note": info.get("note", "")})
        elif "stale" in status and "healthy" not in status:
            stale_steps.append({"step": step, "status": info.get("status"), "note": info.get("note", "")})

    result["failed_steps"] = failed_steps
    result["stale_steps"] = stale_steps
    result["failed_count"] = len(failed_steps)
    result["stale_count"] = len(stale_steps)

    if failed_steps:
        add_issue(check, "WARNING",
                  f"checkpoint 发现 {len(failed_steps)} 个失败/需重建步骤: " +
                  ", ".join([f"{x['step']}({x['status']})" for x in failed_steps]),
                  suggestion="进入后续数据完整性验证与恢复流程")
    elif stale_steps:
        add_issue(check, "NORMAL",
                  f"checkpoint 发现 {len(stale_steps)} 个 stale 步骤（周末/非交易时段属正常）: " +
                  ", ".join([f"{x['step']}({x['status']})" for x in stale_steps]),
                  suggestion="交易时段会自动刷新")
    else:
        log("checkpoint 状态正常", "OK")

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 2. failed步骤 → 数据完整性 + 缓存检查 + 备份恢复
# ═══════════════════════════════════════════════════════════
def check_failed_steps_recovery():
    check = "failed_steps_recovery"
    log("[2/10] 验证数据完整性 + TDX/蜀道/D37-D46 缓存检查 + 备份恢复 ...", "PROGRESS")
    result = {"tdx_enhanced_files": [], "shudao_files": [], "d37d46_module_ok": False,
              "restored": [], "needs_manual": [], "status": "OK"}

    # 2.1 TDX Enhanced Feeder 输出缓存
    tdx_files = sorted(CACHE_DIR.glob("tdx_enhanced_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result["tdx_enhanced_files"] = [f.name for f in tdx_files[:5]]
    latest_tdx = tdx_files[0] if tdx_files else None
    if latest_tdx:
        ok, data = load_json(latest_tdx)
        if ok:
            quote_keys = list(data.get("quote_data", {}).keys())
            hist_keys = list(data.get("capital_flow_history", {}).keys())
            result["latest_tdx"] = {
                "file": latest_tdx.name,
                "quote_stocks": len(quote_keys),
                "history_stocks": len(hist_keys),
                "mtime": datetime.fromtimestamp(latest_tdx.stat().st_mtime).isoformat(),
            }
            if len(quote_keys) < 10:
                add_issue(check, "WARNING",
                          f"TDX EnhancedFeeder 最新缓存 {latest_tdx.name} 仅 {len(quote_keys)} 只报价数据，可能未完整采集",
                          suggestion="检查 TDX MCP 连接或手动触发 tdx_quotes 批量获取")
        else:
            add_issue(check, "CRITICAL", f"TDX EnhancedFeeder 缓存 {latest_tdx.name} JSON 损坏: {data}")
    else:
        add_issue(check, "WARNING", "未找到 tdx_enhanced_*.json 缓存文件")

    # 2.2 蜀道扫描器输出缓存
    shudao_files = sorted(DATA_DIR.glob("shudao_screener_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result["shudao_files"] = [f.name for f in shudao_files[:5]]
    if shudao_files:
        latest_sd = shudao_files[0]
        ok, data = load_json(latest_sd)
        if ok:
            candidates = data if isinstance(data, list) else data.get("candidates", [])
            result["latest_shudao"] = {
                "file": latest_sd.name,
                "candidates": len(candidates),
                "mtime": datetime.fromtimestamp(latest_sd.stat().st_mtime).isoformat(),
            }
            if len(candidates) == 0:
                add_issue(check, "WARNING", f"蜀道扫描器 {latest_sd.name} 候选为空",
                          suggestion="检查 V13_5_ShuDao_Screener.py 输入数据")
        else:
            add_issue(check, "CRITICAL", f"蜀道扫描器缓存 {latest_sd.name} JSON 损坏: {data}")
    else:
        add_issue(check, "WARNING", "未找到 shudao_screener_YYYYMMDD.json 输出缓存",
                  suggestion="运行 V13_5_ShuDao_Screener.py 生成扫描结果")

    # 2.3 V13_5_M71_D37_D46.py 可导入性
    d37_path = ROOT / "V13_5_M71_D37_D46.py"
    if not d37_path.exists():
        add_issue(check, "CRITICAL", "V13_5_M71_D37_D46.py 模块文件缺失", needs_manual=True,
                  suggestion="从 Git 历史或备份恢复该模块")
        result["d37d46_module_ok"] = False
    else:
        try:
            spec = importlib.util.spec_from_file_location("V13_5_M71_D37_D46", d37_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules["V13_5_M71_D37_D46"] = mod
                spec.loader.exec_module(mod)
                scorer = getattr(mod, "NewDimensionScorer", None)
                methods = [m for m in dir(scorer) if m.startswith("score_")] if scorer else []
                result["d37d46_module_ok"] = True
                result["d37d46_methods"] = methods
                result["d37d46_method_count"] = len(methods)
                if len(methods) < 10:
                    add_issue(check, "WARNING",
                              f"D37-D46 模块方法不完整: {len(methods)}/10 ({methods})")
                else:
                    log("D37-D46 模块导入成功，10个评分方法齐全", "OK")
            else:
                raise ImportError("spec loader is None")
        except Exception as e:
            add_issue(check, "CRITICAL", f"V13_5_M71_D37_D46.py 导入失败: {e}", needs_manual=True)
            result["d37d46_module_ok"] = False

    # 2.4 备份恢复逻辑：对缺失/损坏的 step_T*.json 从 backup 恢复
    for step in ["T0", "T3", "T4"]:
        step_path = PIPELINE_DIR / f"step_{step}.json"
        if not step_path.exists():
            restored = restore_step_from_backup(step)
            if restored:
                result["restored"].append(step)
                add_healing(f"step_{step}.json 从备份恢复 → {restored}")
            else:
                result["needs_manual"].append(step)
                add_issue(check, "CRITICAL", f"step_{step}.json 缺失且无法从备份恢复", needs_manual=True)

    # 评估状态
    if result["needs_manual"]:
        result["status"] = "CRITICAL"
    elif result.get("restored") or report["issues"]:
        result["status"] = "DEGRADED"
    else:
        result["status"] = "OK"

    report["checks"][check] = result


def restore_step_from_backup(step: str) -> str:
    """从 backup 目录或 state 文件恢复 step 文件。返回恢复来源或空。"""
    # 优先从 backups 目录
    backups = sorted(BACKUP_DIR.glob(f"step_{step}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if backups:
        try:
            shutil.copy2(backups[0], PIPELINE_DIR / f"step_{step}.json")
            return str(backups[0])
        except Exception:
            pass

    # 尝试从最新 state 文件重建
    state_files = sorted(CACHE_DIR.glob("state_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest_state = None
    for sf in state_files:
        if "before" in sf.name or "expanded" in sf.name or "latest" in sf.name:
            continue
        latest_state = sf
        break
    if latest_state:
        try:
            ok, data = load_json(latest_state)
            if ok:
                rebuilt = rebuild_step_from_state(step, data, latest_state)
                if rebuilt:
                    save_json(PIPELINE_DIR / f"step_{step}.json", rebuilt)
                    return f"rebuilt_from_{latest_state.name}"
        except Exception:
            pass
    return ""


def rebuild_step_from_state(step: str, state: Any, state_path: Path) -> Dict:
    date = state_path.stem.replace("state_", "")[:8]
    ts = datetime.now().isoformat()
    if step == "T0":
        return {
            "step": "T0", "timestamp": ts, "date": date, "status": "rebuilt_from_state_backup",
            "summary": {"total_candidates": len(state.get("summary", {})), "lane1_count": 0, "lane3_count": 0},
            "screener_file": "data/fullmarket_cache/screener_t0_1030.json", "screener_status": "RESTORED"
        }
    elif step == "T3":
        return {
            "step": "T3", "timestamp": ts, "date": date, "status": "rebuilt_from_state_backup",
            "total_candidates": len(state.get("summary", {})), "holy_grail_count": state.get("holy_grail_count", 0),
            "screener_file": "data/fullmarket_cache/screener_t3_1400.json", "screener_status": "RESTORED"
        }
    elif step == "T4":
        return {
            "step": "T4", "timestamp": ts, "date": date, "status": "rebuilt_from_state_backup",
            "total_scanned": len(state.get("summary", {})), "holy_grail_count": state.get("holy_grail_count", 0),
            "screener_file": "data/fullmarket_cache/screener_t4_1415.json", "screener_status": "RESTORED"
        }
    return {}


# ═══════════════════════════════════════════════════════════
# 3. T0/T3/T4 步骤文件存在且 JSON 有效
# ═══════════════════════════════════════════════════════════
def check_step_files():
    check = "step_files"
    log("[3/10] 检查 T0/T3/T4 步骤文件 ...", "PROGRESS")
    result = {"files": {}, "status": "OK"}
    today = TODAY

    for step in ["T0", "T3", "T4"]:
        path = PIPELINE_DIR / f"step_{step}.json"
        file_ok = path.exists()
        json_ok, data = (False, "file_missing") if not file_ok else load_json(path)
        info = {"exists": file_ok, "valid_json": json_ok, "path": str(path)}

        if file_ok and json_ok:
            info["date"] = data.get("date", "UNKNOWN")
            info["status"] = data.get("status", "UNKNOWN")
            info["timestamp"] = data.get("timestamp", "UNKNOWN")
            if info["date"] != today:
                # 交易日 stale 也是可自动刷新的，标 WARNING/DEGRADED
                add_issue(check, "WARNING",
                          f"step_{step}.json 日期不是今天: {info['date']} (今天 {today})",
                          auto_resolve=f"{TODAY_ISO} 对应交易时段自动刷新" if market_trading_today() else "下一交易日自动刷新",
                          suggestion=f"运行 {step} 时段扫描脚本刷新")
        else:
            sev = "CRITICAL" if step in ["T3", "T4"] else "WARNING"
            add_issue(check, sev, f"step_{step}.json {'不存在' if not file_ok else 'JSON无效'}",
                      needs_manual=(not file_ok or not json_ok))

        result["files"][step] = info

    # 汇总
    all_ok = all(v["valid_json"] for v in result["files"].values())
    all_today = all(v.get("date") == today for v in result["files"].values() if v.get("date"))
    result["all_valid"] = all_ok
    result["all_today"] = all_today
    result["status"] = "OK" if (all_ok and all_today) else ("DEGRADED" if all_ok else "CRITICAL")
    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 4. 46维度评分文件完整性 (D1-D46)
# ═══════════════════════════════════════════════════════════
def check_46_dimensions():
    check = "46_dimensions"
    log("[4/10] 检查 46 维度评分文件完整性 ...", "PROGRESS")
    result = {"status": "OK"}

    # 4.1 数据库 m71_reversal_predictions 表
    db_ok = DB_PATH.exists()
    result["db_exists"] = db_ok
    if db_ok:
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), MAX(created_at) FROM m71_reversal_predictions")
            count, max_dt = cursor.fetchone()
            result["db_predictions"] = count
            result["latest_prediction"] = max_dt

            cursor.execute("SELECT dimensions_json FROM m71_reversal_predictions WHERE dimensions_json IS NOT NULL ORDER BY created_at DESC LIMIT 3")
            sample_dims = []
            dim_names = set()
            for row in cursor.fetchall():
                try:
                    dims = json.loads(row[0])
                    if isinstance(dims, list):
                        names = [d.get("name") for d in dims]
                        sample_dims.append(names)
                        dim_names.update(names)
                    elif isinstance(dims, dict):
                        sample_dims.append(list(dims.keys()))
                        dim_names.update(dims.keys())
                except Exception:
                    pass
            conn.close()
            result["sample_dimension_names"] = sample_dims
            result["db_dimension_name_count"] = len(dim_names)

            if count == 0:
                add_issue(check, "CRITICAL", "m71_reversal_predictions 表为空")
                result["status"] = "CRITICAL"
            else:
                log(f"m71_reversal_predictions: {count} 条预测, 维度名 {len(dim_names)} 个", "OK")
        except Exception as e:
            add_issue(check, "CRITICAL", f"读取 m71_reversal_predictions 失败: {e}")
            result["status"] = "CRITICAL"
    else:
        add_issue(check, "CRITICAL", f"数据库不存在: {DB_PATH}")
        result["status"] = "CRITICAL"

    # 4.2 T4 实时评分文件（通常包含 D29-D46 实时/经典维度）
    t4_files = sorted(CACHE_DIR.glob("t4_v13520_full_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if t4_files:
        latest_t4 = t4_files[0]
        ok, data = load_json(latest_t4)
        if ok:
            all_scored = data.get("all_scored", [])
            d_keys = set()
            for s in all_scored[:5]:
                d_keys.update([k for k in s.keys() if k.startswith("d") and k[1:].isdigit()])
            result["latest_t4_file"] = latest_t4.name
            result["t4_scored_count"] = len(all_scored)
            result["t4_d_keys_present"] = sorted(d_keys)
            result["t4_d_keys_count"] = len(d_keys)

            # T4 文件只应包含 D29-D46；D1-D28 来自 m71_reversal_predictions.dimensions_json
            expected_t4 = [f"d{i}" for i in range(29, 47)]
            missing_t4 = [k for k in expected_t4 if k not in d_keys]
            result["t4_d_keys_missing"] = missing_t4
            if missing_t4:
                add_issue(check, "WARNING",
                          f"T4 评分文件 {latest_t4.name} 缺少实时维度: {missing_t4}",
                          suggestion="重新运行 T4 全市场 46 维度评分")
            else:
                log(f"T4 评分文件 {latest_t4.name} 包含 D29-D46 全部实时维度", "OK")
        else:
            add_issue(check, "CRITICAL", f"T4 评分文件 {latest_t4.name} JSON 损坏: {data}")
    else:
        add_issue(check, "WARNING", "未找到 t4_v13520_full_*.json 46维度评分文件")

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 5. 五确认体系标注完整性 (D29+D31+D32+D33+D34)
# ═══════════════════════════════════════════════════════════
def check_five_confirmation():
    check = "five_confirmation"
    log("[5/10] 检查五确认体系标注完整性 ...", "PROGRESS")
    result = {"status": "OK"}

    if not DB_PATH.exists():
        add_issue(check, "CRITICAL", "数据库不存在，无法检查五确认体系")
        result["status"] = "CRITICAL"
        report["checks"][check] = result
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # 检查 four_confirm_snapshots 表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='four_confirm_snapshots'")
        has_table = cursor.fetchone() is not None
        result["table_exists"] = has_table

        if not has_table:
            add_issue(check, "CRITICAL", "four_confirm_snapshots 表不存在",
                      suggestion="运行 M71 五确认初始化脚本")
            result["status"] = "CRITICAL"
            conn.close()
            report["checks"][check] = result
            return

        cursor.execute("SELECT COUNT(*), MAX(created_at) FROM four_confirm_snapshots")
        count, max_dt = cursor.fetchone()
        result["snapshot_count"] = count
        result["latest_snapshot"] = max_dt

        # 探测实际列名（兼容 V13.5.19 之前的 four_confirm 与之后的 five_confirm 命名）
        cursor.execute("PRAGMA table_info(four_confirm_snapshots)")
        columns = [r[1] for r in cursor.fetchall()]
        result["columns"] = columns
        d29_col = next((c for c in columns if "d29" in c.lower() and "score" in c.lower()), None)
        d31_col = next((c for c in columns if "d31" in c.lower() and "score" in c.lower()), None)
        d32_col = next((c for c in columns if "d32" in c.lower() and "score" in c.lower()), None)
        d33_col = next((c for c in columns if "d33" in c.lower() and "score" in c.lower()), None)
        d34_col = next((c for c in columns if "d34" in c.lower() and "score" in c.lower()), None)
        result["d29_col"] = d29_col
        result["d31_col"] = d31_col
        result["d32_col"] = d32_col
        result["d33_col"] = d33_col
        result["d34_col"] = d34_col

        if count == 0:
            add_issue(check, "WARNING", "four_confirm_snapshots 表为空",
                      suggestion="运行 T4 五确认评分生成快照")
            result["status"] = "WARNING"
            conn.close()
            report["checks"][check] = result
            return

        # 检查最新记录是否包含已存在的4/5个维度
        select_cols = [c for c in [d29_col, d31_col, d32_col, d33_col, d34_col] if c]
        level_col = next((c for c in columns if "verdict" in c.lower() or "level" in c.lower()), None)
        level_col = level_col or columns[-1]
        query = f"SELECT {', '.join(select_cols)}, {level_col} FROM four_confirm_snapshots ORDER BY created_at DESC LIMIT 5"
        cursor.execute(query)
        rows = cursor.fetchall()
        complete = 0
        levels = {}
        for r in rows:
            scores = r[:len(select_cols)]
            level = r[-1]
            if all(s is not None for s in scores):
                complete += 1
            levels[level] = levels.get(level, 0) + 1
        result["recent_complete"] = f"{complete}/{len(rows)}"
        result["confirm_level_distribution"] = levels
        result["available_confirm_dimensions"] = len(select_cols)

        if len(select_cols) < 5:
            add_issue(check, "WARNING",
                      f"four_confirm_snapshots 表缺少 D34 列，当前仅 {len(select_cols)}/5 个维度（{select_cols}）。V13.5.19 五确认体系尚未迁移到此表。",
                      suggestion="检查 T4 流程是否将 D34 拆单识别写入 four_confirm_snapshots，或从 T4 评分文件直接读取 D34")
        elif complete < len(rows):
            add_issue(check, "WARNING",
                      f"五确认快照最近 {len(rows)} 条中 {len(rows) - complete} 条维度不完整",
                      suggestion="检查 TDX 实时数据获取（D31/D32/D33/D34 依赖实时行情）")
        else:
            log(f"五确认体系: {count} 条快照，{len(select_cols)} 个维度列，最近 {complete}/{len(rows)} 条完整", "OK")

        conn.close()
    except Exception as e:
        add_issue(check, "CRITICAL", f"检查五确认表失败: {e}")
        result["status"] = "CRITICAL"

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 6. fundamental_data 缓存 (tdx_indicator_select)
# ═══════════════════════════════════════════════════════════
def check_fundamental_cache():
    check = "fundamental_cache"
    log("[6/10] 检查 fundamental_data 缓存 (tdx_indicator_select) ...", "PROGRESS")
    result = {"status": "OK"}

    fd_dir = DATA_DIR / "fundamental_data"
    result["dir_exists"] = fd_dir.exists()
    if fd_dir.exists():
        files = sorted(fd_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result["file_count"] = len(files)
        result["latest_files"] = [f.name for f in files[:5]]
        if files:
            latest = files[0]
            ok, data = load_json(latest)
            result["latest_file"] = latest.name
            result["latest_valid_json"] = ok
            if not ok:
                add_issue(check, "WARNING", f"fundamental_data 缓存 {latest.name} JSON 损坏: {data}")
    else:
        result["note"] = "tdx_indicator_select 是 TDX MCP 运行时工具，未配置持久化缓存目录"

    # 同时检查是否有通过 tdx_api_data 获取的基本面数据痕迹
    tdx_enhanced = CACHE_DIR.glob("tdx_enhanced_*.json")
    latest_te = sorted(tdx_enhanced, key=lambda p: p.stat().st_mtime, reverse=True)
    if latest_te:
        ok, data = load_json(latest_te[0])
        if ok:
            result["tdx_enhanced_quote_fields"] = sorted(list(data.get("quote_data", {}).values())[0].keys()) if data.get("quote_data") else []

    # 是否有 indicator 缓存？
    indicator_dir = CACHE_DIR / "indicator"
    if indicator_dir.exists():
        ind_files = list(indicator_dir.glob("*.json"))
        result["indicator_cache_count"] = len(ind_files)
    else:
        result["indicator_cache_count"] = 0

    if not fd_dir.exists() and result.get("indicator_cache_count", 0) == 0:
        add_issue(check, "NORMAL",
                  "fundamental_data 缓存目录不存在且无 indicator 缓存。tdx_indicator_select 为运行时工具，当前通过 TDX MCP 实时获取，非阻塞。",
                  suggestion="如需本地缓存可配置 fundamental_data 目录")

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 7. tdx_ai_listening 舆情缓存
# ═══════════════════════════════════════════════════════════
def check_ai_listening_cache():
    check = "ai_listening_cache"
    log("[7/10] 检查 tdx_ai_listening 舆情缓存 ...", "PROGRESS")
    result = {"status": "OK"}

    # 本地缓存目录
    ai_dir = DATA_DIR / "tdx_ai_listening"
    result["local_dir_exists"] = ai_dir.exists()
    if ai_dir.exists():
        files = sorted(ai_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result["local_file_count"] = len(files)
        result["latest_local_files"] = [f.name for f in files[:5]]
    else:
        result["local_note"] = "未配置本地持久化目录"

    # fullmarket_cache 中 ai_listening_YYYYMMDD.json
    ai_files = sorted(CACHE_DIR.glob("ai_listening_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result["cache_files"] = [f.name for f in ai_files[:5]]
    if ai_files:
        latest = ai_files[0]
        ok, data = load_json(latest)
        result["latest_cache_file"] = latest.name
        result["latest_valid_json"] = ok
        if ok:
            # 统计条目数
            if isinstance(data, dict):
                result["entry_count"] = len(data)
            elif isinstance(data, list):
                result["entry_count"] = len(data)
            else:
                result["entry_count"] = 0
            mtime = datetime.fromtimestamp(latest.stat().st_mtime)
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            result["age_hours"] = round(age_hours, 2)
            if age_hours > 24:
                add_issue(check, "WARNING",
                          f"tdx_ai_listening 缓存 {latest.name} 已 {age_hours:.1f}h 未更新",
                          auto_resolve="下一交易时段自动刷新")
            else:
                log(f"tdx_ai_listening 缓存正常: {latest.name}, {result.get('entry_count', 0)} 条", "OK")
        else:
            add_issue(check, "CRITICAL", f"tdx_ai_listening 缓存 {latest.name} JSON 损坏: {data}")
    else:
        add_issue(check, "NORMAL",
                  "未找到 ai_listening_*.json 本地缓存。tdx_ai_listening 为 TDX MCP 运行时工具，舆情数据通常存入 DB market_insights 表。",
                  suggestion="检查 holy_grail.db market_insights 表是否有记录")

    # DB 检查
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='market_insights'")
            has_table = cursor.fetchone() is not None
            result["db_market_insights_exists"] = has_table
            if has_table:
                cursor.execute("SELECT COUNT(*), MAX(created_at) FROM market_insights")
                cnt, max_dt = cursor.fetchone()
                result["market_insights_count"] = cnt
                result["market_insights_latest"] = max_dt
                if cnt == 0:
                    add_issue(check, "WARNING", "market_insights 表为空",
                              suggestion="手动触发 tdx_ai_listening 采集")
            conn.close()
        except Exception as e:
            add_issue(check, "WARNING", f"读取 market_insights 表失败: {e}")

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 8. wenda_notice_query 公告催化缓存
# ═══════════════════════════════════════════════════════════
def check_wenda_notice_cache():
    check = "wenda_notice_cache"
    log("[8/10] 检查 wenda_notice_query 公告催化缓存 ...", "PROGRESS")
    result = {"status": "OK"}

    # 本地缓存目录
    notice_dir = DATA_DIR / "wenda_notice"
    result["local_dir_exists"] = notice_dir.exists()
    if notice_dir.exists():
        files = sorted(notice_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result["local_file_count"] = len(files)
        result["latest_local_files"] = [f.name for f in files[:5]]
    else:
        result["local_note"] = "未配置本地持久化目录"

    # fullmarket_cache 中 notices_YYYYMMDD.json
    notice_files = sorted(CACHE_DIR.glob("notices_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result["cache_files"] = [f.name for f in notice_files[:5]]
    if notice_files:
        latest = notice_files[0]
        ok, data = load_json(latest)
        result["latest_cache_file"] = latest.name
        result["latest_valid_json"] = ok
        if ok:
            if isinstance(data, dict):
                result["entry_count"] = len(data)
            elif isinstance(data, list):
                result["entry_count"] = len(data)
            else:
                result["entry_count"] = 0
            mtime = datetime.fromtimestamp(latest.stat().st_mtime)
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            result["age_hours"] = round(age_hours, 2)
            if age_hours > 24:
                add_issue(check, "WARNING",
                          f"wenda_notice 缓存 {latest.name} 已 {age_hours:.1f}h 未更新",
                          auto_resolve="下一交易时段自动刷新")
            else:
                log(f"wenda_notice 缓存正常: {latest.name}, {result.get('entry_count', 0)} 条", "OK")
        else:
            add_issue(check, "CRITICAL", f"wenda_notice 缓存 {latest.name} JSON 损坏: {data}")
    else:
        add_issue(check, "NORMAL",
                  "未找到 notices_*.json 本地缓存。wenda_notice_query 为 TDX MCP 运行时工具，公告数据通常存入 DB catalyst_snapshots 表。",
                  suggestion="检查 holy_grail.db catalyst_snapshots 表")

    # DB 检查
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='catalyst_snapshots'")
            has_table = cursor.fetchone() is not None
            result["db_catalyst_snapshots_exists"] = has_table
            if has_table:
                cursor.execute("SELECT COUNT(*), MAX(created_at) FROM catalyst_snapshots")
                cnt, max_dt = cursor.fetchone()
                result["catalyst_snapshots_count"] = cnt
                result["catalyst_snapshots_latest"] = max_dt
                if cnt == 0:
                    add_issue(check, "WARNING", "catalyst_snapshots 表为空",
                              suggestion="手动触发 wenda_notice_query 采集")
            conn.close()
        except Exception as e:
            add_issue(check, "WARNING", f"读取 catalyst_snapshots 表失败: {e}")

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 9. 蜀道模式扫描缓存
# ═══════════════════════════════════════════════════════════
def check_shudao_screener():
    check = "shudao_screener"
    log("[9/10] 检查蜀道模式扫描缓存 ...", "PROGRESS")
    result = {"status": "OK"}

    screener_files = sorted(DATA_DIR.glob("shudao_screener_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result["files"] = [f.name for f in screener_files[:5]]

    if not screener_files:
        add_issue(check, "WARNING",
                  "未生成 shudao_screener_YYYYMMDD.json 缓存",
                  suggestion="运行 V13_5_ShuDao_Screener.py 生成")
        result["status"] = "WARNING"
    else:
        latest = screener_files[0]
        ok, data = load_json(latest)
        result["latest_file"] = latest.name
        result["latest_valid_json"] = ok
        if ok:
            candidates = data if isinstance(data, list) else data.get("candidates", [])
            result["candidate_count"] = len(candidates)
            d34_count = sum(1 for c in candidates if c.get("d34_score", 0) >= 3)
            result["d34_positive_count"] = d34_count
            mtime = datetime.fromtimestamp(latest.stat().st_mtime)
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            result["age_hours"] = round(age_hours, 2)
            if age_hours > 48:
                add_issue(check, "WARNING",
                          f"蜀道扫描缓存 {latest.name} 已 {age_hours:.1f}h 未更新",
                          auto_resolve="下一交易日运行 V13_5_ShuDao_Screener.py")
            else:
                log(f"蜀道扫描缓存: {latest.name}, {len(candidates)} 候选, D34≥3: {d34_count}", "OK")
        else:
            add_issue(check, "CRITICAL", f"蜀道扫描缓存 {latest.name} JSON 损坏: {data}")
            result["status"] = "CRITICAL"

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 10. D37-D46 周线/形态数据完整性 (tdx_kline wantNum>=200)
# ═══════════════════════════════════════════════════════════
def check_d37_d46_data():
    check = "d37_d46_data"
    log("[10/10] 检查 D37-D46 周线/形态数据完整性 ...", "PROGRESS")
    result = {"status": "OK"}

    # 10.1 检查 weekly_kline 缓存
    weekly_files = sorted(CACHE_DIR.glob("weekly_kline_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    result["weekly_kline_files"] = [f.name for f in weekly_files[:5]]

    if weekly_files:
        latest_wk = weekly_files[0]
        ok, data = load_json(latest_wk)
        result["latest_weekly_file"] = latest_wk.name
        result["latest_weekly_valid_json"] = ok
        if ok:
            # 支持两种格式：字符串 "cached" 或 dict {"wantNum": 200, ...}
            stocks_with_200 = []
            stocks_cached = []
            raw_kline_entries = 0
            for k, v in data.items():
                if k in ("note", "meta", "timestamp"):
                    continue
                if isinstance(v, dict):
                    if v.get("wantNum", 0) >= 200 or v.get("count", 0) >= 200:
                        stocks_with_200.append(k)
                    if v.get("status") == "cached":
                        stocks_cached.append(k)
                    if "data" in v or "kline" in v or "ohlc" in v:
                        raw_kline_entries += 1
                elif v == "cached":
                    stocks_cached.append(k)
            result["stocks_with_wantNum_200"] = len(stocks_with_200)
            result["stocks_cached_flag"] = len(stocks_cached)
            result["raw_kline_entries"] = raw_kline_entries
            result["total_weekly_entries"] = len([k for k in data.keys() if k not in ("note", "meta", "timestamp")])
            if len(stocks_with_200) == 0 and len(stocks_cached) == 0 and raw_kline_entries == 0:
                add_issue(check, "WARNING",
                          f"weekly_kline 缓存 {latest_wk.name} 未包含 wantNum>=200 的股票周线数据",
                          suggestion="运行批量 tdx_kline(P=5, wantNum>=200) 采集")
            else:
                log(f"weekly_kline 缓存: {latest_wk.name}, wantNum≥200: {len(stocks_with_200)}, cached: {len(stocks_cached)}, raw: {raw_kline_entries}", "OK")
        else:
            add_issue(check, "CRITICAL", f"weekly_kline 缓存 {latest_wk.name} JSON 损坏: {data}")
    else:
        add_issue(check, "WARNING",
                  "未找到 weekly_kline_*.json 缓存",
                  suggestion="运行批量周线采集脚本")

    # 10.2 检查 T4 评分中 D37-D46 是否非全 0
    t4_files = sorted(CACHE_DIR.glob("t4_v13520_full_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if t4_files:
        latest_t4 = t4_files[0]
        ok, data = load_json(latest_t4)
        if ok:
            all_scored = data.get("all_scored", [])
            classic_keys = [f"d{i}" for i in range(37, 47)]
            non_zero_count = 0
            sample_non_zero = []
            for s in all_scored:
                total = sum(s.get(k, 0) for k in classic_keys)
                if total != 0:
                    non_zero_count += 1
                    if len(sample_non_zero) < 3:
                        sample_non_zero.append({"code": s.get("code"), "classic_total": total})
            result["t4_classic_non_zero_count"] = non_zero_count
            result["t4_classic_non_zero_samples"] = sample_non_zero
            if non_zero_count == 0:
                add_issue(check, "WARNING",
                          f"T4 评分文件 {latest_t4.name} 中 D37-D46 全为 0，经典交易理论维度未生效",
                          suggestion="检查 V13_5_M71_D37_D46.py 是否在 T4 流程中被调用，以及 tdx_kline 周线数据是否充足")
            else:
                log(f"D37-D46 在 T4 中已生效: {non_zero_count}/{len(all_scored)} 只股票非零", "OK")

    # 10.3 检查 tdx_kline 原始K线目录
    kline_dirs = [d for d in CACHE_DIR.iterdir() if d.is_dir() and "kline" in d.name.lower()]
    result["kline_cache_dirs"] = [d.name for d in kline_dirs[:10]]
    if not kline_dirs:
        add_issue(check, "WARNING",
                  "未找到任何 tdx_kline 本地缓存目录",
                  suggestion="运行 tdx_kline 批量采集脚本")

    report["checks"][check] = result


# ═══════════════════════════════════════════════════════════
# 生成 alert.json 与最终报告
# ═══════════════════════════════════════════════════════════
def generate_outputs():
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    # 最终状态：只有 needs_manual>0 才整体 CRITICAL
    if report["needs_manual"]:
        report["overall_status"] = "CRITICAL"
    elif report["overall_status"] == "CRITICAL":
        report["overall_status"] = "DEGRADED"

    # needs_manual 汇总
    if report["needs_manual"]:
        report["alert"] = {
            "level": "CRITICAL",
            "timestamp": datetime.now().isoformat(),
            "title": "V13.5.20 降级/容错检查 - 需人工介入",
            "needs_manual_count": len(report["needs_manual"]),
            "items": report["needs_manual"],
        }
    else:
        report["alert"] = {
            "level": report["overall_status"],
            "timestamp": datetime.now().isoformat(),
            "title": f"V13.5.20 降级/容错检查 - {report['overall_status']}",
            "needs_manual_count": 0,
            "items": [],
        }

    # 写入 alert.json
    alert_path = OUTPUTS_DIR / "alert.json"
    save_json(alert_path, report["alert"])
    log(f"alert.json 已写入: {alert_path}", "OK")

    # 写入完整报告
    report_path = OUTPUTS_DIR / f"V13520_degradation_check_{TODAY}.json"
    save_json(report_path, report)
    log(f"完整检查报告: {report_path}", "OK")

    # 同时更新 checkpoint.json（如果当前是 healthier 状态）
    cp_path = PIPELINE_DIR / "checkpoint.json"
    try:
        cp_data = {
            "check_type": "V13.5.20_degradation_fault_tolerance",
            "check_timestamp": datetime.now().isoformat(),
            "market_status": "TRADING" if market_trading_today() else "NON_TRADING",
            "overall_health": report["overall_status"],
            "needs_manual_count": len(report["needs_manual"]),
            "issues_count": len(report["issues"]),
            "healing_actions": len(report["healing_actions"]),
            "checks_summary": {k: v.get("status", "UNKNOWN") for k, v in report["checks"].items()},
        }
        save_json(cp_path, cp_data)
        log(f"checkpoint.json 已更新", "OK")
    except Exception as e:
        log(f"更新 checkpoint.json 失败: {e}", "WARN")

    return report_path, alert_path


def main():
    log("=" * 70)
    log("V13.5.20 降级/容错检查启动")
    log("=" * 70)

    check_checkpoint()
    check_failed_steps_recovery()
    check_step_files()
    check_46_dimensions()
    check_five_confirmation()
    check_fundamental_cache()
    check_ai_listening_cache()
    check_wenda_notice_cache()
    check_shudao_screener()
    check_d37_d46_data()

    report_path, alert_path = generate_outputs()

    log("=" * 70)
    log(f"检查完成 — 总体状态: {report['overall_status']}")
    log(f"问题数: {len(report['issues'])}, 需人工: {len(report['needs_manual'])}, 自愈: {len(report['healing_actions'])}")
    log("=" * 70)

    return report_path, alert_path


if __name__ == "__main__":
    main()
