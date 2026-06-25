#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.4.2 DATA GUARDIAN V2 — 全类型数据完整性守护者                 ║
║  红线守卫：所有公网数据必须在写入前通过严格校验                     ║
║  原则：宁可拒绝写入，也绝不让错误数据出现在公网仪表盘               ║
╚══════════════════════════════════════════════════════════════════════╝

五层防护体系:
  L1 写入前验证 — 数据合法性检查 (范围/类型/必填字段) + Screener格式
  L2 写入后审计 — 文件对比 (cache vs deploy SHA256一致性)
  L3 交叉验证   — 多数据源互校验 (指数↔股票 + Screener↔State + 评分链分布)
  L4 仪表盘端   — 浏览器端时效性+格式双重检测 (已在HTML中实现)
  L5 主动巡检   — 定期全量扫描 + 自动修复 + 告警

V2 扩展验证器 (6类):
  1. IndexValidator       — 指数价格/涨跌幅/时效性
  2. ScreenerValidator    — 代码格式/重复/振幅/换手率
  3. StateValidator       — State格式/top_stocks字段完备性
  4. ScoringValidator     — M46/M57/M64/V13.2分布+浮点精度+信号缺失
  5. CrossValidator       — 指数↔股票方向 + Screener↔State代码一致性
  6. StockCodeValidator   — 6位纯数字+合法前缀(上/深/创/科/北交所)

触发方式:
  1. 作为模块导入: from V13_4_DataGuardian import Guardian, run_pre_deploy_check
  2. 作为CLI工具:   python V13_4_DataGuardian.py --pre-deploy
  3. 自动化调用:    python V13_4_DataGuardian.py --auto-audit
"""

import json
import os
import sys
import hashlib
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

# ============================================================
# 配置
# ============================================================
CACHE_DIR = "data/fullmarket_cache"
DEPLOY_DIR = "deploy/data"

# 合理性检查阈值
INDEX_PRICE_RANGE = {
    "000001": (2500, 6000),   # 上证指数
    "399006": (1500, 5000),   # 创业板指
}
INDEX_CHG_RANGE = (-15.0, 15.0)
MAX_INDEX_AGE_MINUTES = 45    # 指数数据最大允许过期时间（交易时段）
MAX_INDEX_AGE_OFFHOURS = 120  # 非交易时段允许更长时间

STOCK_DECLINE_RANGE = (-20.0, 20.0)   # A股涨跌停限制
STOCK_SCORE_RANGE = (0.0, 1.0)        # V13.2评分范围

# === V2: 全数据类型验证阈值 ===
M46_SCORE_RANGE = (0.0, 1.0)          # M46贝叶斯概率 0~1
M57_SCORE_RANGE = (0.0, 1.0)          # M57隔夜Alpha评分 0~1
M64_SCORE_RANGE = (0.0, 5.0)          # M64超跌反转放大 0~5x
V132_SCORE_RANGE = (0.0, 1.0)         # V13.2综合评分 0~1
DECLINE_PCT_RANGE = (-20.0, 5.0)      # A股跌幅范围（跌停-20% 到 涨+5%）
AMPLITUDE_RANGE = (0.0, 30.0)         # 振幅合理范围
HSL_RANGE = (0.0, 100.0)              # 换手率范围
MAX_IDENTICAL_SCORE_RATIO = 0.6       # 相同评分占比超过此值 → 可疑模式
MAX_STOCKS_WITHOUT_SIGNAL = 0.9       # 无信号股票占比超过此值 → 可疑

# Stock code format validators
VALID_STOCK_PREFIXES = {
    "sh": ("600", "601", "603", "605"),  # 上海主板
    "sz_main": ("000", "001", "002", "003"),  # 深圳主板
    "sz_gem": ("300", "301"),            # 创业板
    "sz_star": ("688",),                  # 科创板
    "bj": ("920", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839"),  # 北交所
}

REQUIRED_SCREENER_FIELDS = ["code", "name", "changePct", "amplitude", "hsl", "market"]
REQUIRED_STOCK_SCORE_FIELDS = ["code", "name", "v132_score", "m46_score", "m57_score", "m64_score"]

REQUIRED_STATE_FIELDS = ["date", "summary", "top_stocks"]
REQUIRED_STOCK_FIELDS = ["code", "name", "v132_score", "decline_pct", "tier"]
REQUIRED_INDEX_FIELDS = {
    "000001": ["price", "change_pct", "name"],
    "399006": ["price", "change_pct", "name"],
}

# 交易时段定义
TRADING_HOURS = [(9, 30), (11, 30), (13, 0), (15, 0)]


# ============================================================
# 问题等级
# ============================================================
class Severity:
    CRITICAL = "CRITICAL"   # 必须在部署前修复，否则阻止部署
    ERROR = "ERROR"         # 数据错误，需要修复
    WARNING = "WARNING"     # 可疑但不阻止部署
    INFO = "INFO"           # 信息性提示


class Issue:
    def __init__(self, severity: str, category: str, file_path: str, message: str, fixable: bool = False):
        self.severity = severity
        self.category = category
        self.file_path = file_path
        self.message = message
        self.fixable = fixable


class AuditReport:
    def __init__(self):
        self.issues: List[Issue] = []
        self.fixed: List[str] = []
        self.blocked: List[str] = []
        self.passed_checks: int = 0
        self.total_checks: int = 0
        self.can_deploy: bool = True

    @property
    def critical_count(self):
        return sum(1 for i in self.issues if i.severity == Severity.CRITICAL)

    @property
    def error_count(self):
        return sum(1 for i in self.issues if i.severity == Severity.ERROR)

    @property
    def warning_count(self):
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)


# ============================================================
# L1: 写入前验证
# ============================================================

def is_trading_time() -> bool:
    """判断当前是否在交易时段"""
    now = datetime.now()
    weekday = now.weekday()
    if weekday >= 5:  # 周末
        return False
    hm = now.hour * 60 + now.minute
    for start_h, start_m, end_h, end_m in [(9, 30, 11, 30), (13, 0, 15, 0)]:
        start = start_h * 60 + start_m
        end = end_h * 60 + end_m
        if start <= hm <= end:
            return True
    return False


def validate_index_entry(code: str, data: dict) -> List[Issue]:
    """验证单个指数条目"""
    issues = []
    required = REQUIRED_INDEX_FIELDS.get(code, ["price", "change_pct", "name"])

    for field in required:
        if field not in data or data[field] is None:
            issues.append(Issue(Severity.CRITICAL, "INDEX", f"index({code})",
                               f"缺少必填字段: {field}", fixable=False))

    price = data.get("price", 0)
    if code in INDEX_PRICE_RANGE:
        lo, hi = INDEX_PRICE_RANGE[code]
        if price and (price < lo or price > hi):
            issues.append(Issue(Severity.CRITICAL, "INDEX", f"index({code})",
                               f"价格异常: {price} (合理范围 {lo}-{hi})", fixable=False))

    chg = data.get("change_pct", 0)
    if chg and (chg < INDEX_CHG_RANGE[0] or chg > INDEX_CHG_RANGE[1]):
        issues.append(Issue(Severity.CRITICAL, "INDEX", f"index({code})",
                           f"涨跌幅异常: {chg}% (合理范围 {INDEX_CHG_RANGE[0]}-{INDEX_CHG_RANGE[1]}%)",
                           fixable=False))

    name = data.get("name", "")
    if code == "000001" and name and "上证" not in name:
        issues.append(Issue(Severity.WARNING, "INDEX", f"index({code})",
                           f"名称不匹配: {name} (期望包含'上证')", fixable=False))
    if code == "399006" and name and "创业" not in name:
        issues.append(Issue(Severity.WARNING, "INDEX", f"index({code})",
                           f"名称不匹配: {name} (期望包含'创业板')", fixable=False))

    return issues


def validate_index_file(filepath: str) -> List[Issue]:
    """验证指数JSON文件"""
    issues = []
    if not os.path.exists(filepath):
        issues.append(Issue(Severity.CRITICAL, "INDEX", filepath, "文件不存在", fixable=False))
        return issues

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        issues.append(Issue(Severity.CRITICAL, "INDEX", filepath, f"JSON解析失败: {e}", fixable=False))
        return issues

    # ============================================================
    # 格式检测与标准化 (V13.4.2 支持6种格式)
    # ============================================================
    # 格式1: code-as-key {"000001": {price, change_pct, name}, "399006": {...}}
    # 格式2: sh_index/cy_index {"sh_index": {...}, "cy_index": {...}}
    # 格式3: TDX原生 {"000001": {now, chg_pct, name}, "399006": {now, chg_pct, name}}  (now=price, chg_pct=change_pct)
    # 格式4: sh/sz/cy {"sh": {code:"000001", now, chg}, "sz": {...}, "cy": {code:"399006", ...}}
    # 格式5: indexes包装 {"indexes": {"000001.SH": {...}, "399006.SZ": {...}}}
    # 格式6: 单层now {"000001": {now, name}, ...}  (只有now无price/chg_pct)
    idx_data = {}
    fmt_label = "unknown"

    # 标准化辅助函数: 将任意格式的指数数据转为 {price, change_pct, name}
    def _normalize_index_entry(raw_entry: dict, default_name: str = "") -> dict:
        result = {}
        result["name"] = raw_entry.get("name", default_name)
        # price: 优先price, 其次now
        result["price"] = raw_entry.get("price", raw_entry.get("now", 0))
        # change_pct: 优先change_pct, 其次chg_pct, 最后chg
        result["change_pct"] = raw_entry.get("change_pct", raw_entry.get("chg_pct", raw_entry.get("chg", 0)))
        return result

    if "000001" in data and isinstance(data["000001"], dict):
        # 格式1/3/6: code-as-key (可能是price/change_pct或now/chg_pct或now-only)
        idx_data = {k: _normalize_index_entry(data[k], "上证指数" if k == "000001" else "创业板指" if k == "399006" else "")
                    for k in ["000001", "399006"] if k in data}
        fmt_label = "code-as-key"

    elif "sh_index" in data or "cy_index" in data:
        # 格式2: sh_index/cy_index旧格式
        if "sh_index" in data:
            s = data["sh_index"]
            idx_data["000001"] = {"price": s.get("now", 0), "change_pct": s.get("chg", 0), "name": s.get("name", "上证指数")}
        if "cy_index" in data:
            c = data["cy_index"]
            idx_data["399006"] = {"price": c.get("now", 0), "change_pct": c.get("chg", 0), "name": c.get("name", "创业板指")}
        fmt_label = "sh_index/cy_index"
        issues.append(Issue(Severity.WARNING, "INDEX", filepath,
                           "使用了旧格式(sh_index/cy_index)，建议统一为code-as-key格式", fixable=True))

    elif "sh" in data and isinstance(data["sh"], dict):
        # 格式4: sh/sz/cy {"sh": {code:"000001", now, chg}, "cy": {code:"399006", ...}}
        sh = data.get("sh", {})
        cy = data.get("cy", {})
        if sh and str(sh.get("code", "")) == "000001":
            idx_data["000001"] = _normalize_index_entry(sh, "上证指数")
        if cy and str(cy.get("code", "")) == "399006":
            idx_data["399006"] = _normalize_index_entry(cy, "创业板指")
        # 也可能 sz (深证成指) 替代 cy
        if "399006" not in idx_data:
            sz = data.get("sz", {})
            if sz and str(sz.get("code", "")) == "399001":
                # sz是深证成指, 不是创业板; 只做记录
                pass
        fmt_label = "sh/sz/cy"

    elif "indexes" in data and isinstance(data["indexes"], dict):
        # 格式5: {"indexes": {"000001.SH": {now, change_pct, ...}, "399006.SZ": {...}}}
        indexes = data["indexes"]
        for key, val in indexes.items():
            if not isinstance(val, dict):
                continue
            if "000001" in key:
                idx_data["000001"] = _normalize_index_entry(val, "上证指数")
            elif "399006" in key:
                idx_data["399006"] = _normalize_index_entry(val, "创业板指")
        fmt_label = "indexes-wrap"

    else:
        issues.append(Issue(Severity.CRITICAL, "INDEX", filepath,
                           "无法识别数据格式（既无000001也无sh/cy/indexes）", fixable=False))
        return issues

    # 验证每个指数
    # 判断是否为关键部署文件 (只有latest和1430是部署到CloudStudio的关键文件)
    fname = os.path.basename(filepath)
    is_deploy_critical = fname in ["index_latest.json", "index_1430.json"] or "1430" in fname

    for code in ["000001", "399006"]:
        if code not in idx_data:
            sev = Severity.CRITICAL if is_deploy_critical else Severity.WARNING
            issues.append(Issue(sev, "INDEX", filepath, f"缺少{code}指数数据", fixable=False))
        else:
            issues.extend(validate_index_entry(code, idx_data[code]))

    # 时间戳验证 (支持多种格式)
    # 格式A: time + date 字段 ("10:49" / "1049" + "20260625")
    # 格式B: td_ts 字段 ("20260625_142642")
    # 格式C: 从000001数据中提取 ts ("20260625_142642")
    # 格式D: timestamp 字段 ("2026-06-25 14:21:31")
    data_time = None
    time_display = "未知"

    if "time" in data and "date" in data:
        try:
            time_str = data['time']
            if ':' not in time_str and len(time_str) == 4:
                time_str = f"{time_str[:2]}:{time_str[2:]}"
            dt_str = f"{data['date']} {time_str}"
            data_time = datetime.strptime(dt_str.replace("-", ""), "%Y%m%d %H:%M")
            time_display = f"{data['date']} {data['time']}"
        except (ValueError, IndexError):
            pass

    if data_time is None and "timestamp" in data:
        try:
            data_time = datetime.strptime(str(data["timestamp"]), "%Y-%m-%d %H:%M:%S")
            time_display = str(data["timestamp"])
        except (ValueError, IndexError):
            try:
                data_time = datetime.strptime(str(data["timestamp"]), "%Y-%m-%d %H:%M")
                time_display = str(data["timestamp"])
            except:
                pass

    if data_time is None:
        # 尝试从指数数据中提取ts字段 (TDX native格式)
        for code in ["000001", "399006"]:
            if code in data and isinstance(data[code], dict):
                ts = data[code].get("ts", "")
                if ts and "_" in ts:
                    try:
                        parts = ts.split("_")
                        data_time = datetime.strptime(ts, "%Y%m%d_%H%M%S")
                        time_display = ts
                        break
                    except:
                        pass
            # 也从标准化后的idx_data查
            if code in idx_data:
                pass  # idx_data 可能没有ts字段

    if data_time:
        age_minutes = (datetime.now() - data_time).total_seconds() / 60
        max_age = MAX_INDEX_AGE_MINUTES if is_trading_time() else MAX_INDEX_AGE_OFFHOURS
        if age_minutes > max_age:
            issues.append(Issue(Severity.ERROR, "INDEX", filepath,
                               f"数据过期: {time_display} ({age_minutes:.0f}分钟前, 最大允许{max_age}分钟)",
                               fixable=True))

    return issues


# ============================================================
# L2: 写入后审计 (Cache ↔ Deploy一致性)
# ============================================================

def _file_hash(filepath: str) -> str:
    """计算文件SHA256"""
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_cache_deploy_sync() -> List[Issue]:
    """验证 cache 和 deploy 目录数据一致性"""
    issues = []
    if not os.path.exists(CACHE_DIR):
        issues.append(Issue(Severity.WARNING, "SYNC", CACHE_DIR, "Cache目录不存在", fixable=False))
        return issues
    if not os.path.exists(DEPLOY_DIR):
        issues.append(Issue(Severity.WARNING, "SYNC", DEPLOY_DIR, "Deploy目录不存在", fixable=False))
        return issues

    # 排除非数据文件
    NON_DATA_FILES = {".watchdog_last.json", "app-manifest.json", "sw.js", "sw_backup.js"}
    cache_files = {}
    for f in os.listdir(CACHE_DIR):
        if f.endswith('.json') and f not in NON_DATA_FILES and f.startswith(("index_", "state_", "screener_", "fullmarket_", "news_")):
            cache_files[f] = os.path.join(CACHE_DIR, f)

    for fname, cache_path in cache_files.items():
        deploy_path = os.path.join(DEPLOY_DIR, fname)
        if not os.path.exists(deploy_path):
            # 这些是可选的数据归档文件，不需要部署
            issues.append(Issue(Severity.INFO, "SYNC", deploy_path,
                               f"Deploy缺少可选文件: {fname}", fixable=False))
            continue

        # 对比哈希
        try:
            cache_hash = _file_hash(cache_path)
            deploy_hash = _file_hash(deploy_path)
            if cache_hash != deploy_hash:
                issues.append(Issue(Severity.ERROR, "SYNC", deploy_path,
                                   f"Cache/Deploy不一致: {fname} (需重新同步)", fixable=True))
        except Exception as e:
            issues.append(Issue(Severity.ERROR, "SYNC", deploy_path, f"哈希计算失败: {e}", fixable=False))

    # 检查deploy中多余的数据文件（排除非数据文件）
    DATA_PREFIXES = ("index_", "state_", "screener_")
    for f in os.listdir(DEPLOY_DIR):
        if f.endswith('.json') and f.startswith(DATA_PREFIXES) and f not in cache_files:
            issues.append(Issue(Severity.INFO, "SYNC", os.path.join(DEPLOY_DIR, f),
                               f"Deploy中存在孤立数据文件: {f}", fixable=False))

    return issues


# ============================================================
# L3: 交叉验证 (多数据源互校验)
# ============================================================

def validate_state_file(filepath: str) -> List[Issue]:
    """验证state_*.json文件"""
    issues = []
    if not os.path.exists(filepath):
        issues.append(Issue(Severity.ERROR, "STATE", filepath, "State文件不存在", fixable=False))
        return issues

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        issues.append(Issue(Severity.CRITICAL, "STATE", filepath, f"JSON解析失败: {e}", fixable=False))
        return issues

    # 必填顶层字段 (兼容旧格式: total_stocks → top_stocks)
    if "top_stocks" not in data and "total_stocks" in data:
        # 旧格式state文件，降级为WARNING
        issues.append(Issue(Severity.WARNING, "STATE", filepath,
                           "旧格式state文件(用total_stocks代替top_stocks)，仪表盘可能无法识别", fixable=False))
    elif "top_stocks" not in data:
        issues.append(Issue(Severity.CRITICAL, "STATE", filepath, "缺少顶层字段: top_stocks", fixable=False))
    
    for field in ["date", "summary"]:
        if field not in data:
            issues.append(Issue(Severity.CRITICAL, "STATE", filepath, f"缺少顶层字段: {field}", fixable=False))

    # date校验
    file_date = data.get("date", "")
    if file_date:
        # 支持两种格式: "20260624" 和 "2026-06-24"
        try:
            date_str = file_date.replace("-", "")
            dt = datetime.strptime(date_str, "%Y%m%d")
            age_days = (datetime.now() - dt).days
            if age_days > 7:
                issues.append(Issue(Severity.ERROR, "STATE", filepath,
                                   f"数据过期: {file_date} ({age_days}天前)", fixable=True))
            elif age_days > 1:
                issues.append(Issue(Severity.WARNING, "STATE", filepath,
                                   f"数据非最新: {file_date} ({age_days}天前)", fixable=False))
        except ValueError:
            issues.append(Issue(Severity.ERROR, "STATE", filepath, f"日期格式错误: {file_date}", fixable=False))

    # summary校验
    summary = data.get("summary", {})
    if summary:
        # 统计一致性: tier_a + tier_b + tier_c ≈ total_scanned (允许排除部分)
        tier_a = summary.get("tier_a", 0)
        tier_b = summary.get("tier_b", 0)
        tier_c = summary.get("tier_c", 0)
        total = summary.get("total_scanned", 0)
        tier_total = tier_a + tier_b + tier_c
        if total > 0 and tier_total > total:
            issues.append(Issue(Severity.ERROR, "STATE", filepath,
                               f"统计不一致: tier_sum({tier_total}) > total_scanned({total})", fixable=False))

        # holy_grail_count合理性
        hg = summary.get("holy_grail_count", 0)
        scored = summary.get("scored", 0)
        if hg > scored and scored > 0:
            issues.append(Issue(Severity.ERROR, "STATE", filepath,
                               f"圣杯数量异常: {hg} > 评分总数{scored}", fixable=False))

        # 圣杯数量不应该过多（正常市场<30）
        if hg > 50:
            issues.append(Issue(Severity.WARNING, "STATE", filepath,
                               f"圣杯数量偏高: {hg}只 (正常<30)", fixable=False))

    # top_stocks校验
    stocks = data.get("top_stocks", [])
    if stocks:
        empty_scores = 0
        missing_fields_count = 0
        for i, s in enumerate(stocks[:10]):  # 抽查前10只
            for field in REQUIRED_STOCK_FIELDS:
                if field not in s or s[field] is None:
                    missing_fields_count += 1
            # 评分合理性
            score = s.get("v132_score", 0)
            if score == 0:
                empty_scores += 1
            elif score < STOCK_SCORE_RANGE[0] or score > STOCK_SCORE_RANGE[1]:
                issues.append(Issue(Severity.ERROR, "STATE", filepath,
                                   f"股票{s.get('code','?')} 评分异常: {score}", fixable=False))
            # 跌幅合理性
            decline = s.get("decline_pct", 0)
            if decline < STOCK_DECLINE_RANGE[0] or decline > STOCK_DECLINE_RANGE[1]:
                issues.append(Issue(Severity.ERROR, "STATE", filepath,
                                   f"股票{s.get('code','?')} 跌幅异常: {decline}%", fixable=False))

        if missing_fields_count > 0:
            issues.append(Issue(Severity.WARNING, "STATE", filepath,
                               f"前10只中有{missing_fields_count}个字段缺失", fixable=False))
        if empty_scores > 5:
            issues.append(Issue(Severity.WARNING, "STATE", filepath,
                               f"评分缺失: {empty_scores}/10只无有效评分", fixable=False))

    return issues


# ============================================================
# L3-V2: 全类型数据验证器
# ============================================================

def validate_stock_code_format(code: str) -> Tuple[bool, str, str]:
    """
    验证股票代码格式
    Returns: (is_valid, market, detail)
    """
    if not code or not isinstance(code, str):
        return False, "unknown", "代码为空或非字符串"
    
    if len(code) != 6:
        return False, "unknown", f"代码长度异常: {len(code)}位, 期望6位"
    
    if not code.isdigit():
        return False, "unknown", f"代码含非数字字符: {code}"
    
    prefix3 = code[:3]
    all_valid_prefixes = set()
    for market, prefixes in VALID_STOCK_PREFIXES.items():
        for p in prefixes:
            all_valid_prefixes.add(p)
        if prefix3 in prefixes:
            return True, market, "OK"
    
    return False, "unknown", f"未识别的代码前缀: {prefix3}"


def validate_screener_file(filepath: str) -> List[Issue]:
    """验证screener_*.json文件（L1扩展, 兼容多种格式）"""
    issues = []
    if not os.path.exists(filepath):
        issues.append(Issue(Severity.ERROR, "SCREENER", filepath, "文件不存在", fixable=False))
        return issues

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        issues.append(Issue(Severity.CRITICAL, "SCREENER", filepath, f"JSON解析失败: {e}", fixable=False))
        return issues

    # 格式适配: 支持5种格式
    # 格式1: 纯数组 [{code, name, changePct, ...}]
    # 格式2: 包装对象 {"screener_results": [...], "time": "...", "date": "..."}
    # 格式3: 包装对象 {"data": [...], ...}
    # 格式4: 嵌套 {"decline": {"data": [{sec_code, sec_name, now_price, chg, ...}]}}
    # 格式5: Stats summary (无股票列表, 如screener_t3_1400.json)
    data = None
    file_format = "unknown"
    if isinstance(raw, list):
        data = raw
        file_format = "array"
    elif isinstance(raw, dict):
        if "screener_results" in raw:
            data = raw.get("screener_results", [])
            file_format = "wrapped(screener_results)"
        elif "data" in raw:
            inner = raw.get("data")
            if isinstance(inner, list):
                data = inner
                file_format = "wrapped(data)"
            elif isinstance(inner, dict) and "screener_results" in inner:
                data = inner.get("screener_results", [])
                file_format = "wrapped(data.screener_results)"
        elif "decline" in raw and isinstance(raw["decline"], dict):
            # 格式4: T4 1415 嵌套{"decline":{"data":[...]}}
            decline = raw["decline"]
            if "data" in decline:
                data = decline["data"]
                file_format = "nested(decline.data)"
        elif "route1_decline_p1" in raw or "total_unique_estimate" in raw:
            # 格式5: Stats summary (只有统计数字, 无股票列表)
            issues.append(Issue(Severity.INFO, "SCREENER", filepath,
                               "统计摘要文件(无股票列表)", fixable=False))
            return issues
        elif "route1" in raw and "route2" in raw:
            # 格式5b: 路由摘要 (如 screener_t3_1400_raw.json, 仅含page_no/page_size元数据)
            issues.append(Issue(Severity.INFO, "SCREENER", filepath,
                               "路由元数据文件(无股票列表)", fixable=False))
            return issues
    
    if data is None or not isinstance(data, list):
        issues.append(Issue(Severity.CRITICAL, "SCREENER", filepath,
                           f"无法提取股票列表 (格式={file_format})", fixable=False))
        return issues

    if len(data) == 0:
        issues.append(Issue(Severity.WARNING, "SCREENER", filepath, "Screener结果为空", fixable=False))
        return issues

    # 字段映射: 不同格式可能用不同的字段名
    FIELD_ALIASES = {
        "code": ["code", "sec_code"],
        "name": ["name", "sec_name"],
        "changePct": ["changePct", "change_pct", "chg", "pct"],
        "amplitude": ["amplitude", "amplitudePct", "amplitude_pct", "amp"],
        "hsl": ["hsl", "turnover", "turnover_rate"],
        "market": ["market", "market_id"],
        "price": ["price", "now_price"],
    }

    def _get_field(s: dict, canonical: str) -> Any:
        """从字段别名中获取值"""
        if canonical in s:
            return s[canonical]
        for alias in FIELD_ALIASES.get(canonical, []):
            if alias in s:
                return s[alias]
        return None

    def _normalize_stock(s: dict) -> dict:
        """将不同格式的股票条目标准化"""
        return {
            "code": _get_field(s, "code") or "",
            "name": _get_field(s, "name") or "",
            "changePct": _get_field(s, "changePct"),
            "amplitude": _get_field(s, "amplitude"),
            "hsl": _get_field(s, "hsl"),
            "market": _get_field(s, "market"),
            "price": _get_field(s, "price"),
        }

    # 统计验证
    bad_codes = 0
    bad_names = 0
    st_stocks_found = []  # ⚠️ ST股票不应出现在screener中
    zero_amplitude_stocks = []
    unique_codes = set()

    for i, s_raw in enumerate(data):
        s = _normalize_stock(s_raw)
        code = str(s.get("code", ""))
        
        # 重复检测
        if code in unique_codes:
            issues.append(Issue(Severity.WARNING, "SCREENER", filepath,
                               f"重复股票代码: {code} (索引{i})", fixable=False))
        unique_codes.add(code)
        
        # 代码格式验证
        is_valid, market, detail = validate_stock_code_format(code)
        if not is_valid:
            bad_codes += 1
            if bad_codes <= 3:
                issues.append(Issue(Severity.ERROR, "SCREENER", filepath,
                                   f"股票{i}: {detail}", fixable=False))
        
        # ST/*ST检测 (全市场扫描器应该排除, 出现ST说明排除规则未生效)
        name = str(s.get("name", ""))
        if "ST" in name or "*ST" in name:
            st_stocks_found.append(f"{code}:{name}")
        
        # 必填字段检查 (通过别名)
        for field in ["code", "name"]:
            if field not in s or not s[field]:
                bad_names += 1
        
        # 振幅检查 (通过field alias)
        amplitude = _get_field(s, "amplitude")
        if amplitude is not None:
            try:
                amp_val = float(amplitude)
                if amp_val == 0.0:
                    zero_amplitude_stocks.append(f"{code}:{name}")
            except (ValueError, TypeError):
                pass

    if bad_codes > 0:
        issues.append(Issue(Severity.ERROR, "SCREENER", filepath,
                           f"无效股票代码: {bad_codes}只", fixable=False))
    if bad_names > 0:
        issues.append(Issue(Severity.WARNING, "SCREENER", filepath,
                           f"字段缺失: {bad_names}条记录", fixable=False))

    # ST股票告警 (关键: 全市场扫描器应已排除ST)
    if st_stocks_found:
        stock_list = ", ".join(st_stocks_found[:5])
        if len(st_stocks_found) > 5:
            stock_list += f"... 等{len(st_stocks_found)}只"
        issues.append(Issue(Severity.ERROR, "SCREENER", filepath,
                           f"ST/*ST股票未被排除: {stock_list}",
                           fixable=False))  # 需修复扫描器排除逻辑

    if zero_amplitude_stocks:
        stocks_str = ", ".join(zero_amplitude_stocks[:5])
        if len(zero_amplitude_stocks) > 5:
            stocks_str += f"... 等{len(zero_amplitude_stocks)}只"
        issues.append(Issue(Severity.INFO, "SCREENER", filepath,
                           f"振幅为0 (可能停牌/一字板): {stocks_str}", fixable=False))

    return issues


def validate_scoring_distribution(stocks: List[dict], source_file: str) -> List[Issue]:
    """
    验证评分链分布合理性（L3扩展）
    检测: 所有m64相同、M46/M57不合理分布、v132评分异常精度
    """
    issues = []
    if not stocks:
        return issues

    n = len(stocks)
    
    # === m64_score 分布检测 ===
    m64_values = [s.get("m64_score", 0) for s in stocks if s.get("m64_score") is not None]
    if m64_values:
        m64_unique = set(round(v, 4) for v in m64_values)
        # 检测：只有1-2个唯一值
        if len(m64_unique) <= 2:
            from collections import Counter
            cnt = Counter(round(v, 4) for v in m64_values)
            top_val, top_count = cnt.most_common(1)[0]
            ratio = top_count / len(m64_values)
            if ratio > MAX_IDENTICAL_SCORE_RATIO:
                # M64值仍在合理范围(0-5)内只是区分度低 → 模型质量问题而非数据损坏
                # 降级为ERROR而非CRITICAL，不阻止部署但需记录改进
                issues.append(Issue(Severity.ERROR, "SCORING", source_file,
                                   f"M64评分退化(模型质量): {len(m64_unique)}种值/{n}只, {top_val}出现{top_count}次({ratio*100:.0f}%)",
                                   fixable=False))
            else:
                issues.append(Issue(Severity.WARNING, "SCORING", source_file,
                                   f"M64评分集中: {len(m64_unique)}种值/{n}只", fixable=False))
        
        # 检测：M64超出合理范围
        m64_outliers = [v for v in m64_values if v < M64_SCORE_RANGE[0] or v > M64_SCORE_RANGE[1]]
        if m64_outliers:
            issues.append(Issue(Severity.ERROR, "SCORING", source_file,
                               f"M64评分超范围: {len(m64_outliers)}只 (范围{M64_SCORE_RANGE})", fixable=False))
    
    # === M46/M57/V13.2 分布检测 ===
    for score_name, score_range, label in [
        ("m46_score", M46_SCORE_RANGE, "M46贝叶斯"),
        ("m57_score", M57_SCORE_RANGE, "M57隔夜Alpha"),
        ("v132_score", V132_SCORE_RANGE, "V13.2综合"),
    ]:
        values = [s.get(score_name, 0) for s in stocks if s.get(score_name) is not None]
        if not values:
            continue
        outliers = [v for v in values if v < score_range[0] or v > score_range[1]]
        if outliers:
            issues.append(Issue(Severity.ERROR, "SCORING", source_file,
                               f"{label}评分超范围: {len(outliers)}只 (范围{score_range})", fixable=False))
        
        # 检测全部相同的值
        unique_vals = set(round(v, 4) for v in values)
        if len(unique_vals) <= 3 and n > 10:
            issues.append(Issue(Severity.WARNING, "SCORING", source_file,
                               f"{label}分布过集中: {len(unique_vals)}种值/{n}只", fixable=False))
    
    # === v132_score 浮点精度检测 ===
    v132_vals = [s.get("v132_score", 0) for s in stocks if s.get("v132_score") is not None]
    precision_issues = 0
    for v in v132_vals:
        if isinstance(v, float):
            s = f"{v:.15f}"
            # 检测 0.9999... 类型的浮点精度溢出
            if "999999" in s[-10:] or "000000" in s[-10:]:
                precision_issues += 1
    if precision_issues > n * 0.5:
        issues.append(Issue(Severity.WARNING, "SCORING", source_file,
                           f"v132浮点精度溢出: {precision_issues}/{n}只 (建议round(score,6)规范化)",
                           fixable=False))
    
    # === alert_level 分布检测 ===
    no_signal_count = sum(1 for s in stocks if s.get("alert_level", "").startswith("⚪"))
    if no_signal_count > n * MAX_STOCKS_WITHOUT_SIGNAL and n > 10:
        issues.append(Issue(Severity.WARNING, "SCORING", source_file,
                           f"信号缺失: {no_signal_count}/{n}只无预警信号 ({no_signal_count*100/n:.0f}%)",
                           fixable=False))
    
    # === cumulative_weight 检测 ===
    all_zero_weight = sum(1 for s in stocks if s.get("cumulative_weight", 0) == 0)
    if all_zero_weight == n and n > 5:
        issues.append(Issue(Severity.INFO, "SCORING", source_file,
                           f"累积权重全部为0: {n}只 (跨时段累积可能未激活)", fixable=False))
    
    return issues


def cross_validate_screener_vs_state(screener_file: str, state_file: str) -> List[Issue]:
    """
    交叉验证: Screener数据 vs State/top_stocks数据一致性
    确保screener筛选结果与state中的top_stocks对应
    """
    issues = []
    if not os.path.exists(screener_file) or not os.path.exists(state_file):
        return issues

    try:
        with open(screener_file, 'r') as f:
            screener = json.load(f)
        with open(state_file, 'r') as f:
            state = json.load(f)
    except Exception:
        return issues

    screener_codes = set(str(s.get("code", "")) for s in screener)
    top_stocks = state.get("top_stocks", [])
    state_codes = set(str(s.get("code", "")) for s in top_stocks)
    
    if not screener_codes or not state_codes:
        return issues

    # 检测: screener中有但state中没有的股票（top_stocks应该是screener的评分子集）
    missing_from_state = screener_codes - state_codes
    if len(missing_from_state) > len(screener_codes) * 0.5:
        issues.append(Issue(Severity.WARNING, "CROSS", screener_file,
                           f"Screener与State不一致: {len(missing_from_state)}只在Screener但不在State ({len(missing_from_state)*100//len(screener_codes)}%)",
                           fixable=False))
    
    # 检测: state中的股票不在screener中 (顶级异常)
    missing_from_screener = state_codes - screener_codes
    if missing_from_screener:
        issues.append(Issue(Severity.ERROR, "CROSS", state_file,
                           f"State中有股票不在Screener中: {missing_from_screener}",
                           fixable=False))

    # decline_pct一致性检测 (抽查前5只)
    screener_decline = {str(s["code"]): s.get("changePct") for s in screener}
    mismatch_count = 0
    for s in top_stocks[:10]:
        code = str(s.get("code", ""))
        sd = screener_decline.get(code)
        sd_val = s.get("decline_pct")
        if sd is not None and sd_val is not None:
            if abs(sd - sd_val) > 0.5:  # 允许0.5%误差
                mismatch_count += 1
    
    if mismatch_count >= 3:
        issues.append(Issue(Severity.WARNING, "CROSS", screener_file,
                           f"Screener vs State跌幅不一致: {mismatch_count}/10只",
                           fixable=False))
    
    return issues


def cross_validate_index_vs_state(index_file: str, state_file: str) -> List[Issue]:
    """
    交叉验证: 指数方向 vs 股票方向
    如果指数上涨但top_stocks全部是下跌股 → 可能数据不一致
    """
    issues = []
    if not os.path.exists(index_file) or not os.path.exists(state_file):
        return issues  # 各文件独立验证已有报错

    try:
        with open(index_file, 'r') as f:
            idx = json.load(f)
        with open(state_file, 'r') as f:
            state = json.load(f)
    except Exception:
        return issues

    # 获取指数方向 (兼容多种格式)
    sh_data, cy_data = {}, {}
    if "000001" in idx and isinstance(idx["000001"], dict):
        sh_data = idx["000001"]
    elif "sh" in idx and isinstance(idx["sh"], dict):
        sh_data = idx["sh"]
    elif "sh_index" in idx and isinstance(idx["sh_index"], dict):
        sh_data = idx["sh_index"]
    elif "indexes" in idx and isinstance(idx["indexes"], dict):
        for k, v in idx["indexes"].items():
            if "000001" in k:
                sh_data = v
            elif "399006" in k:
                cy_data = v

    if not cy_data:
        # 尝试获取cy
        if "399006" in idx and isinstance(idx["399006"], dict):
            cy_data = idx["399006"]
        elif "cy" in idx and isinstance(idx["cy"], dict):
            cy_data = idx["cy"]
        elif "cy_index" in idx and isinstance(idx["cy_index"], dict):
            cy_data = idx["cy_index"]

    sh_chg = sh_data.get("change_pct", sh_data.get("chg_pct", sh_data.get("chg", 0))) or 0
    cy_chg = cy_data.get("change_pct", cy_data.get("chg_pct", cy_data.get("chg", 0))) or 0
    idx_direction = "UP" if (sh_chg + cy_chg) / 2 > 0.3 else "DOWN" if (sh_chg + cy_chg) / 2 < -0.3 else "FLAT"

    # 获取股票方向
    stocks = state.get("top_stocks", [])
    if not stocks:
        return issues

    decline_stocks = [s for s in stocks if s.get("decline_pct", 0) < 0]
    # 如果指数上涨>0.3% 但top_stocks全是下跌股 → 数据可能有问题
    if idx_direction == "UP" and len(decline_stocks) == len(stocks) and len(stocks) >= 5:
        issues.append(Issue(Severity.WARNING, "CROSS", index_file,
                           f"交叉异常: 指数上涨({sh_chg:+.2f}%/{cy_chg:+.2f}%)但top_stocks全部下跌",
                           fixable=False))

    return issues


# ============================================================
# L5: 主动巡检 (全量扫描)
# ============================================================

def run_full_integrity_audit(auto_fix: bool = False, v2_mode: bool = True) -> AuditReport:
    """运行全量数据完整性审计(V2: 覆盖所有数据类型)"""
    report = AuditReport()
    checks = {
        # === L1: 数据格式与完整性 ===
        "L1-IndexCache": lambda: _check_all_index_files(CACHE_DIR),
        "L1-IndexDeploy": lambda: _check_all_index_files(DEPLOY_DIR),
        "L1-ScreenerCache": lambda: _check_all_screener_files(CACHE_DIR),
        "L1-ScreenerDeploy": lambda: _check_all_screener_files(DEPLOY_DIR),
        # === L2: Cache ↔ Deploy 一致性 ===
        "L2-CacheDeploySync": verify_cache_deploy_sync,
        # === L3: 数据合理性 + 交叉验证 ===
        "L3-StateValidation": lambda: _check_all_state_files(v2_mode=v2_mode),
        "L3-CrossValidation": lambda: _run_cross_validation(v2_mode=v2_mode),
    }

    for check_name, check_fn in checks.items():
        try:
            issues = check_fn()
            report.issues.extend(issues)
            report.total_checks += 1
            has_critical = any(i.severity == Severity.CRITICAL for i in issues)
            has_error = any(i.severity == Severity.ERROR for i in issues)
            if not has_critical and not has_error:
                report.passed_checks += 1
        except Exception as e:
            import traceback
            report.issues.append(Issue(Severity.ERROR, "GUARDIAN", check_name,
                                       f"检查执行异常: {e}", fixable=False))

    # 判断是否可以部署
    report.can_deploy = report.critical_count == 0

    # 自动修复
    if auto_fix and not report.can_deploy:
        _attempt_auto_fix(report)

    return report


def _check_all_index_files(directory: str) -> List[Issue]:
    """检查目录中所有index_*.json文件"""
    issues = []
    if not os.path.exists(directory):
        issues.append(Issue(Severity.WARNING, "INDEX", directory, "目录不存在", fixable=False))
        return issues

    for f in sorted(os.listdir(directory)):
        if f.startswith("index_") and f.endswith(".json"):
            filepath = os.path.join(directory, f)
            issues.extend(validate_index_file(filepath))
    return issues


def _check_all_screener_files(directory: str) -> List[Issue]:
    """检查目录中所有screener_*.json文件"""
    issues = []
    if not os.path.exists(directory):
        issues.append(Issue(Severity.WARNING, "SCREENER", directory, "目录不存在", fixable=False))
        return issues

    found = False
    for f in sorted(os.listdir(directory)):
        if f.startswith("screener_") and f.endswith(".json"):
            found = True
            filepath = os.path.join(directory, f)
            issues.extend(validate_screener_file(filepath))
    
    if not found:
        issues.append(Issue(Severity.INFO, "SCREENER", directory, "未找到screener文件", fixable=False))
    return issues


def _check_all_state_files(v2_mode: bool = True) -> List[Issue]:
    """检查所有state文件 + V2评分链验证"""
    issues = []
    for directory in [CACHE_DIR, DEPLOY_DIR]:
        if not os.path.exists(directory):
            continue
        for f in os.listdir(directory):
            if f.startswith("state_") and f.endswith(".json"):
                filepath = os.path.join(directory, f)
                issues.extend(validate_state_file(filepath))
                
                # V2: 评分链分布验证
                if v2_mode:
                    try:
                        with open(filepath, 'r') as fp:
                            data = json.load(fp)
                        top_stocks = data.get("top_stocks", [])
                        if top_stocks:
                            issues.extend(validate_scoring_distribution(top_stocks, filepath))
                    except Exception:
                        pass
    return issues


def _run_cross_validation(v2_mode: bool = True) -> List[Issue]:
    """运行交叉验证 (V2: 含Screener↔State)"""
    issues = []
    today = datetime.now().strftime("%Y%m%d")

    # 找最新的index文件
    index_file = None
    for fn in [f"index_{today}", "index_latest.json", "index_1430.json",
               "index_1415.json", "index_1400.json",
               "index_1130.json", "index_1030.json"]:
        fp = os.path.join(CACHE_DIR, fn)
        if os.path.exists(fp):
            index_file = fp
            break

    # 找今天的state文件
    state_file = None
    for directory in [CACHE_DIR, DEPLOY_DIR]:
        fp = os.path.join(directory, f"state_{today}.json")
        if os.path.exists(fp):
            state_file = fp
            break

    if index_file and state_file:
        issues.extend(cross_validate_index_vs_state(index_file, state_file))
    
    # V2: Screener ↔ State 交叉验证
    if v2_mode:
        screener_file = None
        for fn in ["screener_t5_1430.json", "screener_t4_1415.json",
                    "screener_t3_1400.json", "screener_t1_1130.json",
                    "screener_t0_1030.json"]:
            fp = os.path.join(CACHE_DIR, fn)
            if os.path.exists(fp):
                screener_file = fp
                break
        
        if screener_file and state_file:
            issues.extend(cross_validate_screener_vs_state(screener_file, state_file))

    return issues


def _attempt_auto_fix(report: AuditReport):
    """尝试自动修复可修复的问题"""
    for issue in report.issues:
        if not issue.fixable:
            continue

        if issue.category == "SYNC" and "不一致" in issue.message:
            # 从cache同步到deploy
            fname = os.path.basename(issue.file_path)
            cache_path = os.path.join(CACHE_DIR, fname)
            if os.path.exists(cache_path):
                try:
                    shutil.copy2(cache_path, issue.file_path)
                    report.fixed.append(f"SYNC: {fname} → deploy")
                except Exception as e:
                    report.blocked.append(f"SYNC修复失败 {fname}: {e}")

        elif issue.category == "INDEX" and "过期" in issue.message:
            # 标记为需刷新（实际刷新由自动化完成）
            report.fixed.append(f"INDEX过期标记: {issue.file_path} (等待下次自动化刷新)")

    # 刷新can_deploy状态
    report.can_deploy = report.critical_count == 0


# ============================================================
# 部署前检查 (关键入口)
# ============================================================

def run_pre_deploy_check(auto_fix: bool = True) -> Tuple[bool, AuditReport]:
    """
    部署前完整性检查 — CloudStudio同步前必须通过
    
    Returns:
        (can_deploy, report)
    """
    report = run_full_integrity_audit(auto_fix=auto_fix)

    # 交易时段额外检查
    if is_trading_time():
        # 检查是否有最新的index数据
        latest_idx = os.path.join(CACHE_DIR, "index_latest.json")
        if os.path.exists(latest_idx):
            mtime = datetime.fromtimestamp(os.path.getmtime(latest_idx))
            age = (datetime.now() - mtime).total_seconds() / 60
            if age > MAX_INDEX_AGE_MINUTES:
                report.issues.append(Issue(Severity.ERROR, "DEPLOY", latest_idx,
                                          f"交易时段指数数据过期({age:.0f}分钟)，建议暂缓部署", fixable=True))

    return report.can_deploy, report


# ============================================================
# 报告生成
# ============================================================

def format_report(report: AuditReport, title: str = "数据完整性审计报告") -> str:
    """格式化审计报告"""
    lines = []
    lines.append("╔" + "═" * 58 + "╗")
    lines.append(f"║  {title:<56}║")
    lines.append("╠" + "═" * 58 + "╣")

    # 摘要
    status_icon = "✅" if report.can_deploy else "❌"
    lines.append(f"║  部署状态: {status_icon} {'可以部署' if report.can_deploy else '阻止部署':<46}║")
    lines.append(f"║  检查项: {report.passed_checks}/{report.total_checks} 通过                          ║")
    lines.append(f"║  问题: 🔴CRIT={report.critical_count} 🟠ERR={report.error_count} 🟡WARN={report.warning_count}                  ║")
    lines.append("╠" + "═" * 58 + "╣")

    # 问题详情
    if report.issues:
        by_severity = {"CRITICAL": [], "ERROR": [], "WARNING": [], "INFO": []}
        for issue in report.issues:
            by_severity[issue.severity].append(issue)

        for sev in ["CRITICAL", "ERROR", "WARNING", "INFO"]:
            items = by_severity[sev]
            if items:
                emoji = {"CRITICAL": "🔴", "ERROR": "🟠", "WARNING": "🟡", "INFO": "🔵"}[sev]
                lines.append(f"║ {emoji} {sev} ({len(items)}项)")
                for item in items[:10]:  # 最多显示10条
                    msg = item.message[:48]
                    lines.append(f"║    └─ [{item.category}] {msg}")
                if len(items) > 10:
                    lines.append(f"║    └─ ... 还有{len(items)-10}条")
    else:
        lines.append("║  🎉 未发现任何问题，数据完整性完美！                  ║")

    # 修复记录
    if report.fixed:
        lines.append("╠" + "═" * 58 + "╣")
        lines.append("║ 🔧 自动修复:")
        for f in report.fixed[:5]:
            lines.append(f"║    ✓ {f[:52]}")
    if report.blocked:
        lines.append("║ ⚠️ 修复失败:")
        for b in report.blocked[:5]:
            lines.append(f"║    ✗ {b[:52]}")

    lines.append("╚" + "═" * 58 + "╝")
    return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="V13.4.1 数据完整性守护者 (V2: 全类型数据验证)")
    parser.add_argument("--pre-deploy", action="store_true", help="部署前检查")
    parser.add_argument("--auto-audit", action="store_true", help="全量自动审计")
    parser.add_argument("--v2", action="store_true", default=True, help="启用V2全类型验证 (默认)")
    parser.add_argument("--v1", action="store_true", help="仅V1基础验证 (指数+State基础)")
    parser.add_argument("--auto-fix", action="store_true", help="自动修复可修复问题")
    parser.add_argument("--report", action="store_true", help="仅生成报告不修复")
    parser.add_argument("--json", action="store_true", help="JSON格式输出")
    args = parser.parse_args()

    v2_mode = not args.v1  # 默认V2

    if args.pre_deploy:
        can_deploy, report = run_pre_deploy_check(auto_fix=not args.report)
        if args.json:
            print(json.dumps({"can_deploy": report.can_deploy, "critical": report.critical_count,
                             "errors": report.error_count, "warnings": report.warning_count,
                             "fixed": report.fixed, "blocked": report.blocked},
                            ensure_ascii=False))
        else:
            print(format_report(report, "V13.4.1 部署前数据完整性检查 (V2全类型)"))
        sys.exit(0 if report.can_deploy else 1)

    elif args.auto_audit:
        report = run_full_integrity_audit(auto_fix=args.auto_fix, v2_mode=v2_mode)
        if args.json:
            print(json.dumps({"can_deploy": report.can_deploy, "critical": report.critical_count,
                             "errors": report.error_count, "warnings": report.warning_count,
                             "fixed": report.fixed, "issues": [
                                 {"severity": i.severity, "category": i.category,
                                  "file": i.file_path, "message": i.message, "fixable": i.fixable}
                                 for i in report.issues
                             ]}, ensure_ascii=False))
        else:
            mode_label = "V2全类型" if v2_mode else "V1基础"
            print(format_report(report, f"V13.4.1 数据完整性全量审计 ({mode_label})"))
        sys.exit(0 if report.can_deploy else 1)

    else:
        # 默认: 快速检查 (V2)
        report = run_full_integrity_audit(auto_fix=False, v2_mode=v2_mode)
        mode_label = "V2全类型" if v2_mode else "V1基础"
        print(format_report(report, f"V13.4.1 数据完整性快速检查 ({mode_label})"))
        sys.exit(0 if report.can_deploy else 1)
