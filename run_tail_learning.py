#!/usr/bin/env python3
"""
V13.0 毕方灵犀·天眼 · T+1 尾盘复盘学习回路
============================================
设计目标：
  1. 每日盘后(15:05)自动触发，读取前日尾盘选股结果
  2. 查询当日全市场涨停股，交叉验证命中率
  3. 识别漏选(涨停但未被选出的股票)，分析漏选原因
  4. 自动微调M46贝叶斯先验、7权重融合系数、评分阈值
  5. 学习结果写入SQLite + JSON，生成学习日报
  6. 逼近100%尾盘选股成功率

使用方式：
  python run_tail_learning.py [--date 2026-06-23] [--dry-run] [--no-tune]
"""

import json, math, os, sys, sqlite3, time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict


# ═══════════════════════════════════════════════
# 核心配置
# ═══════════════════════════════════════════════

MAX_RUNTIME_SEC = 600        # 10分钟硬上限
MIN_SAMPLE_DAYS = 5          # 最少累积5天数据才开始调参
TUNE_STEP_PRIOR = 0.02       # M46先验微调步长
TUNE_STEP_WEIGHT = 0.01      # 权重微调步长
TUNE_STEP_THRESHOLD = 1.0    # 阈值微调步长
MAX_WEIGHT_SHIFT = 0.05      # 单次权重最大偏移
MAX_PRIOR_SHIFT = 0.10       # 单次先验最大偏移
MAX_THRESHOLD_SHIFT = 5.0    # 单次阈值最大偏移

# 参数快照：记录每次调参前后的值
PARAMS_DEFAULTS = {
    "m46_prior": 0.25,
    "min_score_threshold": 42.0,
    "weights": {
        "tech": 0.20, "capital": 0.18, "sentiment": 0.15,
        "fundamental": 0.15, "industry": 0.12, "event": 0.10, "game": 0.10
    }
}

# 权重维度中文名
WEIGHT_NAMES = {
    "tech": "技术面", "capital": "资金面", "sentiment": "情绪面",
    "fundamental": "基本面", "industry": "行业面", "event": "事件面", "game": "博弈面"
}


# ═══════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════

@dataclass
class LimitUpStock:
    """涨停股记录"""
    code: str
    name: str
    industry: str
    limit_up_time: str       # 涨停时间 "09:35" / "14:55" 等
    consecutive_days: int    # 连板天数
    board_type: str          # "首板" / "二板" / "三板+" / "一字板"
    turnover_pct: float      # 换手率%
    limit_up_reason: str     # 涨停原因

@dataclass
class LearningRecord:
    """单日学习记录"""
    learn_date: str          # 学习日期 (T+1)
    pick_date: str           # 选股日期 (T日)
    total_picks: int         # 尾盘选出总数
    picks_limit_up: int      # 选股中涨停数 (命中)
    picks_limit_up_list: List[str]  # 命中股票代码列表
    market_limit_up: int     # 全市场涨停总数
    missed_limit_up: int     # 漏选涨停股数
    missed_list: List[str]   # 漏选股票代码列表
    precision: float         # 命中率 = 选股涨停数/选股总数
    recall: float            # 召回率 = 选股涨停数/全市场涨停数
    f1_score: float          # F1
    params_snapshot: Dict    # 当前参数值
    tune_applied: bool       # 是否应用了调参
    tune_changes: Dict       # 调参变更详情


# ═══════════════════════════════════════════════
# 数据层：读取前日选股 + 查询当日涨停
# ═══════════════════════════════════════════════

def find_prev_trading_day(ref_date: Optional[date] = None) -> date:
    """找到上一个交易日（跳过周末；暂不处理节假日）"""
    d = ref_date or date.today()
    d = d - timedelta(days=1)
    while d.weekday() >= 5:  # 周六=5, 周日=6
        d = d - timedelta(days=1)
    return d

def load_tail_picks(pick_date: str, data_dir: str = None) -> Optional[Dict]:
    """加载指定日期的尾盘选股结果"""
    if data_dir is None:
        data_dir = os.path.join(os.path.dirname(__file__) or ".", "data")
    fpath = os.path.join(data_dir, f"tail_market_{pick_date}.json")
    if not os.path.exists(fpath):
        return None
    with open(fpath, "r", encoding="utf-8") as f:
        return json.load(f)

def get_limit_up_stocks_synthetic() -> List[LimitUpStock]:
    """
    合成涨停数据（用于测试/离线验证）
    实际运行时由TDX MCP注入真实数据
    """
    synthetic = [
        # 常见涨停场景模拟：包含一些能被尾盘系统选出的，一些不能的
        LimitUpStock("002230", "科大讯飞", "AI", "14:35", 1, "首板", 5.2, "AI大模型政策利好"),
        LimitUpStock("688256", "寒武纪", "AI芯片", "10:05", 1, "首板", 8.5, "算力芯片国产替代"),
        # 以下为漏选候选（特征与系统偏好不匹配的涨停股）
        LimitUpStock("000001", "平安银行", "银行", "09:50", 1, "首板", 0.3, "银行板块普涨"),
        LimitUpStock("600000", "浦发银行", "银行", "10:20", 1, "首板", 0.2, "银行板块普涨"),
        LimitUpStock("001234", "N泰鸿", "次新", "09:25", 1, "一字板", 0.1, "新股上市"),
        LimitUpStock("300999", "金龙鱼", "食品", "13:10", 1, "首板", 1.5, "食品涨价预期"),
        LimitUpStock("601728", "中国电信", "通信", "14:50", 1, "首板", 0.8, "5G消息刺激"),
        LimitUpStock("600519", "贵州茅台", "白酒", "10:45", 1, "首板", 0.5, "提价预期"),
        LimitUpStock("002475", "立讯精密", "消费电子", "09:35", 2, "二板", 6.8, "苹果概念"),
        LimitUpStock("300274", "阳光电源", "光伏", "14:20", 3, "三板+", 12.3, "光伏出口超预期"),
    ]
    return synthetic

def try_load_real_limit_up(cache_dir: str = None) -> Optional[List[LimitUpStock]]:
    """尝试从TDX缓存文件加载真实涨停数据"""
    if cache_dir is None:
        cache_dir = os.path.join(os.path.dirname(__file__) or ".", "data")
    cache_path = os.path.join(cache_dir, "tdx_limit_up_today.json")
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stocks = data.get("limit_up_stocks", data.get("stocks", []))
        return [
            LimitUpStock(
                code=s.get("code", ""),
                name=s.get("name", "?"),
                industry=s.get("industry", "未知"),
                limit_up_time=s.get("time", "?"),
                consecutive_days=s.get("consecutive", 1),
                board_type=s.get("board_type", "首板"),
                turnover_pct=s.get("turnover", 0),
                limit_up_reason=s.get("reason", "")
            ) for s in stocks
        ]
    except Exception:
        return None


# ═══════════════════════════════════════════════
# 核心引擎：交叉验证 + 漏选分析
# ═══════════════════════════════════════════════

def cross_validate(picks_data: Dict, limit_up_stocks: List[LimitUpStock]) -> Dict:
    """
    交叉验证前日选股 vs 当日涨停。
    返回详细分析结果。
    """
    pick_codes = set()
    pick_map = {}
    buy_list = picks_data.get("buy_list", [])
    for b in buy_list:
        code = b.get("code", "")
        if code:
            pick_codes.add(code)
            pick_map[code] = b

    all_limit_up = {s.code: s for s in limit_up_stocks}
    lu_codes = set(all_limit_up.keys())

    # 命中：选股中涨停的
    hit_codes = pick_codes & lu_codes
    # 漏选：涨停但未被选出的
    missed_codes = lu_codes - pick_codes

    # 分析漏选原因
    missed_analysis = []
    for code in missed_codes:
        s = all_limit_up[code]
        reason = analyze_miss_reason(s, picks_data)
        missed_analysis.append({
            "code": code, "name": s.name, "industry": s.industry,
            "limit_up_time": s.limit_up_time, "board_type": s.board_type,
            "miss_reason": reason
        })

    # 分析命中股票的特征（共性提取）
    hit_features = []
    for code in hit_codes:
        if code in pick_map:
            p = pick_map[code]
            lu = all_limit_up[code]
            hit_features.append({
                "code": code, "name": p.get("name", code),
                "fusion_score": p.get("fusion_score", 0),
                "m46_prob": p.get("m46_prob", 0),
                "change_pct_pick": p.get("change_pct", 0),
                "limit_up_time": lu.limit_up_time,
                "board_type": lu.board_type,
                "breakdown": p.get("breakdown", {})
            })

    total_picks = len(pick_codes)
    total_lu = len(lu_codes)
    hit_count = len(hit_codes)

    precision = hit_count / total_picks if total_picks > 0 else 0
    recall = hit_count / total_lu if total_lu > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    return {
        "total_picks": total_picks,
        "total_limit_up": total_lu,
        "hit_count": hit_count,
        "missed_count": len(missed_codes),
        "hit_codes": sorted(hit_codes),
        "missed_codes": sorted(missed_codes),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "hit_analysis": hit_features,
        "missed_analysis": missed_analysis,
    }

def analyze_miss_reason(lu: LimitUpStock, picks_data: Dict) -> str:
    """分析一只涨停股为什么没有被选出"""
    reasons = []

    # 一字板 → 尾盘已封死无法买入
    if lu.board_type == "一字板":
        reasons.append("一字板封死(尾盘不可买入)")
    # 次新股 → 不在系统默认监控池
    if "次新" in lu.industry or "N" in lu.name[:2]:
        reasons.append("次新股(不在默认监控池)")
    # 银行/大金融 → 低波动系统不偏好
    if lu.industry in ("银行", "保险", "券商"):
        reasons.append("大金融板块(低波动/系统低偏好)")
    # 换手率极低 → 流动性不足
    if lu.turnover_pct < 0.5:
        reasons.append(f"换手率过低({lu.turnover_pct:.1f}%)")
    # 尾盘涨停 → 与系统14:30时间窗口冲突
    if lu.limit_up_time >= "14:30":
        reasons.append("尾盘涨停(14:30后才封板)")
    # 连板股 → 高风险已被L3过滤
    if lu.consecutive_days >= 2:
        reasons.append(f"连板股({lu.consecutive_days}板/L3过滤)")

    if not reasons:
        reasons.append("监控池外(需扩展monitor范围)")

    return "; ".join(reasons)


# ═══════════════════════════════════════════════
# 自适应调参引擎
# ═══════════════════════════════════════════════

def load_current_params(db_path: str) -> Dict:
    """从SQLite加载当前参数值"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT name, value FROM learning_params ORDER BY name")
    rows = cur.fetchall()
    conn.close()

    params = dict(PARAMS_DEFAULTS)
    for row in rows:
        try:
            val = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            try:
                val = float(row["value"])
            except (ValueError, TypeError):
                val = row["value"]
        params[row["name"]] = val
    return params

def save_params(db_path: str, params: Dict):
    """保存参数到SQLite"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS learning_params (
            name TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    now = datetime.now().isoformat()
    for k, v in params.items():
        val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
        cur.execute("""
            INSERT OR REPLACE INTO learning_params (name, value, updated_at)
            VALUES (?, ?, ?)
        """, (k, val_str, now))
    conn.commit()
    conn.close()

def auto_tune(cross_result: Dict, current_params: Dict,
              all_learning_history: List[Dict]) -> Tuple[Dict, Dict]:
    """
    根据交叉验证结果自动调参。
    
    策略：
    - 召回率低(漏选多) → 降低阈值/提升权重来扩大选股面
    - 精确率低(选的多但涨停少) → 提升阈值/收紧权重
    - F1导向：同时优化精确率和召回率
    
    调参边界：
    - M46先验: [0.15, 0.40]
    - 各权重: [0.05, 0.30]
    - 评分阈值: [35.0, 55.0]
    """
    if len(all_learning_history) < MIN_SAMPLE_DAYS:
        return current_params, {"reason": f"样本不足(需≥{MIN_SAMPLE_DAYS}天)", "applied": False}

    precision = cross_result["precision"]
    recall = cross_result["recall"]
    f1 = cross_result["f1_score"]
    missed_count = cross_result["missed_count"]
    hit_count = cross_result["hit_count"]

    new_params = {
        "m46_prior": current_params.get("m46_prior", PARAMS_DEFAULTS["m46_prior"]),
        "min_score_threshold": current_params.get("min_score_threshold", PARAMS_DEFAULTS["min_score_threshold"]),
        "weights": dict(current_params.get("weights", PARAMS_DEFAULTS["weights"])),
    }

    changes = {"reason": "", "applied": True, "details": []}

    # ── 策略1: 低召回率 → 降低阈值 + 提升资金面/情绪面权重 ──
    if recall < 0.30 and missed_count >= 5:
        old_thresh = new_params["min_score_threshold"]
        shift = min(TUNE_STEP_THRESHOLD * min(missed_count, 5), MAX_THRESHOLD_SHIFT)
        new_params["min_score_threshold"] = max(35.0, old_thresh - shift)
        changes["details"].append(
            f"低召回率({recall:.1%})|漏选{missed_count}只 → 阈值 {old_thresh:.1f}→{new_params['min_score_threshold']:.1f}"
        )

        # 提升资金面权重（尾盘资金是关键信号）
        w = new_params["weights"]
        w["capital"] = min(0.30, w["capital"] + TUNE_STEP_WEIGHT * 2)
        w["sentiment"] = min(0.25, w["sentiment"] + TUNE_STEP_WEIGHT)
        # 从事件面/博弈面挪空间
        w["event"] = max(0.05, w["event"] - TUNE_STEP_WEIGHT)
        w["game"] = max(0.05, w["game"] - TUNE_STEP_WEIGHT * 2)
        changes["details"].append("提升资金面+情绪面权重，降低事件面+博弈面")

    # ── 策略2: 低精确率 → 提升阈值 + 提升技术面/基本面权重 ──
    if precision < 0.15 and hit_count < 3:
        old_thresh = new_params["min_score_threshold"]
        shift = min(TUNE_STEP_THRESHOLD * 2, MAX_THRESHOLD_SHIFT)
        new_params["min_score_threshold"] = min(55.0, old_thresh + shift)
        changes["details"].append(
            f"低精确率({precision:.1%})|仅命中{hit_count}只 → 阈值 {old_thresh:.1f}→{new_params['min_score_threshold']:.1f}"
        )

        w = new_params["weights"]
        w["tech"] = min(0.30, w["tech"] + TUNE_STEP_WEIGHT * 2)
        w["fundamental"] = min(0.25, w["fundamental"] + TUNE_STEP_WEIGHT)
        w["capital"] = max(0.10, w["capital"] - TUNE_STEP_WEIGHT)
        w["sentiment"] = max(0.08, w["sentiment"] - TUNE_STEP_WEIGHT * 2)
        changes["details"].append("提升技术面+基本面权重，降低资金面+情绪面")

    # ── 策略3: M46贝叶斯先验校准 ──
    # 先验应趋向于实际命中率的滑动窗口均值
    recent_precisions = [h.get("precision", 0) for h in all_learning_history[-MIN_SAMPLE_DAYS:]]
    avg_precision = sum(recent_precisions) / len(recent_precisions) if recent_precisions else precision
    target_prior = avg_precision * 0.7 + current_params.get("m46_prior", 0.25) * 0.3
    new_prior = max(0.15, min(0.40, target_prior))
    old_prior = new_params["m46_prior"]
    if abs(new_prior - old_prior) > TUNE_STEP_PRIOR * 0.5:
        new_params["m46_prior"] = round(new_prior, 4)
        changes["details"].append(
            f"M46先验校准 {old_prior:.4f}→{new_prior:.4f} (近{MIN_SAMPLE_DAYS}日均精确率={avg_precision:.2%})"
        )

    # ── 策略4: 漏选行业分析 → 调整行业权重 ──
    missed_industries = defaultdict(int)
    for m in cross_result.get("missed_analysis", []):
        missed_industries[m["industry"]] += 1
    if missed_industries:
        top_missed = max(missed_industries, key=missed_industries.get)
        if missed_industries[top_missed] >= 3:
            # 某个行业大量漏选 → 略微提升行业面权重
            w = new_params["weights"]
            w["industry"] = min(0.25, w["industry"] + TUNE_STEP_WEIGHT)
            changes["details"].append(f"行业漏选({top_missed}漏{missed_industries[top_missed]}只) → 提升行业面权重至{w['industry']:.2f}")

    # ── 权重归一化 ──
    w = new_params["weights"]
    total_w = sum(w.values())
    if abs(total_w - 1.0) > 0.001:
        for k in w:
            w[k] = round(w[k] / total_w, 4)
        # 修正浮点误差
        diff = 1.0 - sum(w.values())
        if abs(diff) > 0.001:
            max_k = max(w, key=w.get)
            w[max_k] = round(w[max_k] + diff, 4)

    if not changes["details"]:
        changes["reason"] = f"无需调参(F1={f1:.3f}在可接受范围)"
        changes["applied"] = False
        return current_params, changes

    changes["reason"] = f"基于F1={f1:.3f}(P={precision:.3f}/R={recall:.3f})自动优化"
    return new_params, changes


# ═══════════════════════════════════════════════
# SQLite 持久化
# ═══════════════════════════════════════════════

def init_learning_db(db_path: str):
    """初始化学习回路数据库表"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS learning_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            learn_date TEXT NOT NULL,
            pick_date TEXT NOT NULL,
            total_picks INTEGER DEFAULT 0,
            picks_limit_up INTEGER DEFAULT 0,
            picks_limit_up_list TEXT DEFAULT '[]',
            market_limit_up INTEGER DEFAULT 0,
            missed_limit_up INTEGER DEFAULT 0,
            missed_list TEXT DEFAULT '[]',
            precision REAL DEFAULT 0,
            recall REAL DEFAULT 0,
            f1_score REAL DEFAULT 0,
            params_snapshot TEXT DEFAULT '{}',
            tune_applied INTEGER DEFAULT 0,
            tune_changes TEXT DEFAULT '{}',
            missed_analysis TEXT DEFAULT '[]',
            hit_analysis TEXT DEFAULT '[]',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS learning_params (
            name TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS learning_trend (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            precision_7d REAL DEFAULT 0,
            recall_7d REAL DEFAULT 0,
            f1_7d REAL DEFAULT 0,
            m46_prior REAL DEFAULT 0.25,
            min_threshold REAL DEFAULT 42.0,
            total_samples INTEGER DEFAULT 0
        )
    """)
    conn.commit()

    # 初始化默认参数
    now = datetime.now().isoformat()
    for k, v in PARAMS_DEFAULTS.items():
        val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, dict) else str(v)
        cur.execute("""
            INSERT OR IGNORE INTO learning_params (name, value, updated_at)
            VALUES (?, ?, ?)
        """, (k, val_str, now))
    conn.commit()
    conn.close()

def save_learning_record(db_path: str, record: LearningRecord, cross_result: Dict):
    """保存单日学习记录"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO learning_records
        (learn_date, pick_date, total_picks, picks_limit_up, picks_limit_up_list,
         market_limit_up, missed_limit_up, missed_list,
         precision, recall, f1_score, params_snapshot, tune_applied, tune_changes,
         missed_analysis, hit_analysis)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        record.learn_date, record.pick_date,
        record.total_picks, record.picks_limit_up,
        json.dumps(record.picks_limit_up_list, ensure_ascii=False),
        record.market_limit_up, record.missed_limit_up,
        json.dumps(record.missed_list, ensure_ascii=False),
        record.precision, record.recall, record.f1_score,
        json.dumps(record.params_snapshot, ensure_ascii=False),
        1 if record.tune_applied else 0,
        json.dumps(record.tune_changes, ensure_ascii=False),
        json.dumps(cross_result.get("missed_analysis", []), ensure_ascii=False),
        json.dumps(cross_result.get("hit_analysis", []), ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

def load_learning_history(db_path: str, limit: int = 60) -> List[Dict]:
    """加载历史学习记录（最近N天）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM learning_records
        ORDER BY learn_date DESC LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()

    records = []
    for row in rows:
        r = dict(row)
        for f in ("precision", "recall", "f1_score"):
            r[f] = float(r[f]) if r[f] else 0
        records.append(r)
    return list(reversed(records))  # 按时间正序

def compute_rolling_metrics(history: List[Dict], window: int = 7) -> Dict:
    """计算滚动窗口指标"""
    if not history:
        return {"precision": 0, "recall": 0, "f1": 0, "samples": 0}
    window_data = history[-window:]
    avg_p = sum(h.get("precision", 0) for h in window_data) / len(window_data)
    avg_r = sum(h.get("recall", 0) for h in window_data) / len(window_data)
    f1 = 2 * avg_p * avg_r / (avg_p + avg_r) if (avg_p + avg_r) > 0 else 0
    return {
        "precision": round(avg_p, 4),
        "recall": round(avg_r, 4),
        "f1": round(f1, 4),
        "samples": len(window_data)
    }

def save_trend(db_path: str, metrics: Dict, params: Dict):
    """保存趋势快照"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO learning_trend (date, precision_7d, recall_7d, f1_7d,
                                     m46_prior, min_threshold, total_samples)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d"),
        metrics.get("precision", 0), metrics.get("recall", 0),
        metrics.get("f1", 0),
        params.get("m46_prior", 0.25),
        params.get("min_score_threshold", 42.0),
        metrics.get("samples", 0)
    ))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════

def generate_report(cross_result: Dict, record: LearningRecord,
                    rolling: Dict, params: Dict) -> str:
    """生成学习日报文本"""
    lines = []
    lines.append("=" * 72)
    lines.append(f"  📚 毕方灵犀·天眼 V13.0 | T+1 尾盘学习日报")
    lines.append(f"  学习日期: {record.learn_date} | 选股日期: {record.pick_date}")
    lines.append(f"  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 72)

    # 核心指标
    lines.append(f"\n  🎯 核心指标:")
    lines.append(f"  {'─' * 40}")
    lines.append(f"  尾盘选股: {record.total_picks}只 → 次日涨停: {record.picks_limit_up}只")
    lines.append(f"  全市场涨停: {record.market_limit_up}只 → 漏选: {record.missed_limit_up}只")
    lines.append(f"  命中率(Precision): {record.precision:.1%}  召回率(Recall): {record.recall:.1%}")
    lines.append(f"  F1分数: {record.f1_score:.4f}")

    # 命中明细
    if cross_result["hit_analysis"]:
        lines.append(f"\n  ✅ 命中涨停 ({len(cross_result['hit_analysis'])}只):")
        for h in cross_result["hit_analysis"]:
            lines.append(f"    [{h['code']}] {h['name']} | 融合{h['fusion_score']:.1f} | "
                         f"M46={h['m46_prob']:.2f} | 选时涨幅{h['change_pct_pick']:+.1f}% | "
                         f"封板{h['limit_up_time']} | {h['board_type']}")

    # 漏选明细
    if cross_result["missed_analysis"]:
        lines.append(f"\n  ❌ 漏选涨停 ({len(cross_result['missed_analysis'])}只):")
        for m in cross_result["missed_analysis"]:
            lines.append(f"    [{m['code']}] {m['name']} | {m['industry']} | "
                         f"{m['board_type']} | 封板{m['limit_up_time']}")
            lines.append(f"      → 漏选原因: {m['miss_reason']}")

    # 滚动趋势
    lines.append(f"\n  📈 滚动趋势 (近{rolling['samples']}天):")
    lines.append(f"  {'─' * 40}")
    lines.append(f"  7日平均命中率: {rolling['precision']:.1%}")
    lines.append(f"  7日平均召回率: {rolling['recall']:.1%}")
    lines.append(f"  7日F1: {rolling['f1']:.4f}")

    # 参数变更
    lines.append(f"\n  🔧 参数状态:")
    lines.append(f"  {'─' * 40}")
    lines.append(f"  M46贝叶斯先验: {params.get('m46_prior', 0.25):.4f}")
    lines.append(f"  最低评分阈值: {params.get('min_score_threshold', 42.0):.1f}")
    w = params.get("weights", PARAMS_DEFAULTS["weights"])
    lines.append(f"  7权重融合: " + " | ".join(f"{WEIGHT_NAMES[k]}{w[k]:.2f}" for k in w))
    lines.append(f"  权重合计: {sum(w.values()):.3f}")

    if record.tune_applied:
        lines.append(f"\n  ⚡ 本次调参:")
        for d in record.tune_changes.get("details", []):
            lines.append(f"    · {d}")
    else:
        lines.append(f"\n  ℹ️ 本次未触发调参: {record.tune_changes.get('reason', 'N/A')}")

    # 改进建议
    lines.append(f"\n  💡 改进建议:")
    lines.append(f"  {'─' * 40}")
    if record.recall < 0.20:
        lines.append(f"  ⚠️ 召回率过低({record.recall:.1%})，系统选股面过窄")
        lines.append(f"     建议: 扩大监控池、降低评分阈值、增加尾盘放量因子")
    if record.precision < 0.10 and record.total_picks > 0:
        lines.append(f"  ⚠️ 命中率过低({record.precision:.1%})，信号噪音比高")
        lines.append(f"     建议: 提升技术面+基本面权重、收紧换手率过滤")
    missed_industries = defaultdict(int)
    for m in cross_result.get("missed_analysis", []):
        missed_industries[m["industry"]] += 1
    if missed_industries:
        top_missed = sorted(missed_industries.items(), key=lambda x: x[1], reverse=True)
        lines.append(f"  漏选行业TOP3: {', '.join(f'{ind}({n}只)' for ind, n in top_missed[:3])}")
        lines.append(f"     建议: 将上述行业标的纳入监控池")

    lines.append(f"\n  {'─' * 72}")
    lines.append(f"  📊 学习回路状态: {'✅ 正常运转' if rolling['samples'] > 0 else '⚠️ 数据积累中'}")
    lines.append("=" * 72)

    return "\n".join(lines)

def save_report(report_text: str, learn_date: str, output_dir: str = None):
    """保存学习报告到文件"""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(__file__) or ".", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    fpath = os.path.join(output_dir, f"learn_report_{learn_date}.txt")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(report_text)
    return fpath


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def main(ref_date_str: str = None, dry_run: bool = False, no_tune: bool = False):
    """
    主学习回路入口。
    
    Args:
        ref_date_str: 参考日期 "2026-06-23"，默认为今天
        dry_run: 仅分析不写入
        no_tune: 跳过自动调参
    """
    t0 = time.time()

    # 确定日期
    if ref_date_str:
        learn_date = date.fromisoformat(ref_date_str)
    else:
        learn_date = date.today()
    pick_date = find_prev_trading_day(learn_date)

    learn_date_str = learn_date.isoformat()
    pick_date_str = pick_date.isoformat()

    print(f"🧠 V13.0 T+1 尾盘学习回路启动")
    print(f"   学习日 T+1: {learn_date_str} | 选股日 T: {pick_date_str}")
    print(f"   {'🧪 演练模式(不写入)' if dry_run else '📝 正式运行'}")
    print(f"   {'🔒 跳过调参' if no_tune else '🔧 启用自适应调参'}")

    # 1. 加载前日选股
    picks_data = load_tail_picks(pick_date_str)
    if picks_data is None:
        print(f"\n⚠️ 未找到选股数据: data/tail_market_{pick_date_str}.json")
        print(f"   可能原因: 当日无选股输出 / 文件路径错误 / 非交易日")
        print(f"   学习回路跳过本次运行，等待下次触发。")
        return None

    buy_count = len(picks_data.get("buy_list", []))
    watch_count = len(picks_data.get("watch_list", []))
    print(f"\n📋 加载前日选股: {buy_count}买入 + {watch_count}关注 = {buy_count + watch_count}条信号")

    # 2. 获取当日涨停数据
    limit_up_stocks = try_load_real_limit_up()
    if limit_up_stocks is None:
        print("⚠️ TDX真实涨停数据不可用，使用合成数据（仅供测试验证）")
        limit_up_stocks = get_limit_up_stocks_synthetic()
    print(f"📊 当日涨停股: {len(limit_up_stocks)}只")

    # 3. 交叉验证
    cross_result = cross_validate(picks_data, limit_up_stocks)
    print(f"\n🎯 交叉验证结果:")
    print(f"   命中: {cross_result['hit_count']}/{cross_result['total_picks']} "
          f"(命中率 {cross_result['precision']:.1%})")
    print(f"   召回: {cross_result['hit_count']}/{cross_result['total_limit_up']} "
          f"(召回率 {cross_result['recall']:.1%})")
    print(f"   F1: {cross_result['f1_score']:.4f}")
    print(f"   漏选: {cross_result['missed_count']}只")

    # 4. 初始化/加载DB
    script_dir = os.path.dirname(__file__) or "."
    db_path = os.path.join(script_dir, "data", "v13_decisions.db")
    init_learning_db(db_path)

    # 5. 加载当前参数和历史
    current_params = load_current_params(db_path)
    all_history = load_learning_history(db_path)

    # 6. 自动调参
    tune_changes = {"reason": "跳过调参", "applied": False, "details": []}
    new_params = current_params
    if not no_tune:
        new_params, tune_changes = auto_tune(cross_result, current_params, all_history)
        if tune_changes["applied"]:
            if not dry_run:
                save_params(db_path, new_params)
            print(f"\n🔧 自动调参已应用:")
            for d in tune_changes.get("details", []):
                print(f"    · {d}")

    # 7. 构建学习记录
    record = LearningRecord(
        learn_date=learn_date_str,
        pick_date=pick_date_str,
        total_picks=cross_result["total_picks"],
        picks_limit_up=cross_result["hit_count"],
        picks_limit_up_list=cross_result["hit_codes"],
        market_limit_up=cross_result["total_limit_up"],
        missed_limit_up=cross_result["missed_count"],
        missed_list=cross_result["missed_codes"],
        precision=cross_result["precision"],
        recall=cross_result["recall"],
        f1_score=cross_result["f1_score"],
        params_snapshot=new_params,
        tune_applied=tune_changes["applied"],
        tune_changes=tune_changes,
    )

    # 8. 持久化
    if not dry_run:
        save_learning_record(db_path, record, cross_result)

        # 更新趋势
        updated_history = load_learning_history(db_path)
        rolling = compute_rolling_metrics(updated_history)
        save_trend(db_path, rolling, new_params)
    else:
        updated_history = all_history + [{
            "precision": cross_result["precision"],
            "recall": cross_result["recall"],
            "f1_score": cross_result["f1_score"]
        }]
        rolling = compute_rolling_metrics(updated_history)

    # 9. 生成报告
    report = generate_report(cross_result, record, rolling, new_params)
    print(report)

    if not dry_run:
        rpt_path = save_report(report, learn_date_str)
        print(f"\n📁 报告已保存: {rpt_path}")

    elapsed = time.time() - t0
    print(f"\n⏱️ 全流程耗时: {elapsed:.1f}秒 | {'✅ 在时限内' if elapsed < MAX_RUNTIME_SEC else '⚠️ 超时'}")

    # 10. 返回结构化结果（供外部调用）
    return {
        "learn_date": learn_date_str,
        "pick_date": pick_date_str,
        "cross_validation": cross_result,
        "rolling_metrics": rolling,
        "params": new_params,
        "tune_applied": tune_changes["applied"],
        "tune_changes": tune_changes,
        "elapsed_sec": round(elapsed, 1),
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V13.0 T+1 尾盘复盘学习回路")
    parser.add_argument("--date", type=str, default=None,
                        help="学习日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--dry-run", action="store_true",
                        help="演练模式：仅分析不写入DB")
    parser.add_argument("--no-tune", action="store_true",
                        help="跳过自动调参")
    args = parser.parse_args()
    main(ref_date_str=args.date, dry_run=args.dry_run, no_tune=args.no_tune)
