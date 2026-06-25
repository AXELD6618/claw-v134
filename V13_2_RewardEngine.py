#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 圣杯奖惩引擎 — RewardEngine                                    ║
║  ================================================================    ║
║  核心使命：T日尾盘选出未涨停股票 → T+1上涨/涨停 → 启动连续上涨趋势    ║
║                                                                      ║
║  奖励机制：                                                           ║
║  ├── S级（圣杯命中）: 选出 → T+1涨停 → T+2继续涨 = +100分            ║
║  ├── A级（精准命中）: 选出 → T+1涨停 = +50分                         ║
║  ├── B级（方向正确）: 选出 → T+1涨≥5% = +20分                        ║
║  ├── C级（微涨）:     选出 → T+1涨0~5% = +5分                        ║
║  ├── 惩罚P1（误判）: 选出 → T+1跌≥5% = -20分                         ║
║  ├── 惩罚P2（严重误判）: 选出 → T+1跌停 = -50分                      ║
║  └── 漏选惩罚: T+1有未涨停→上涨股但系统未选出 = 每只-5~-15分          ║
║                                                                      ║
║  市场豁免：                                                           ║
║  ├── T+1全市场无一只上涨/涨停股 → 免除漏选惩罚                        ║
║  ├── T+1全市场上涨股<3只 → 减免50%漏选惩罚                           ║
║  └── T+1全市场上涨股≥50只但系统命中0 → 额外-50分（严重失职）         ║
║                                                                      ║
║  持久化：SQLite reward_records + reward_summary + reward_trend       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
import sqlite3
import time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple, Set
from collections import defaultdict
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 常量
# ═══════════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'holy_grail.db')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# 奖励分值表
REWARD_SCORES = {
    'S_TREND_START':  100,   # 选出→T+1涨停→T+2续涨（圣杯级）
    'A_LIMIT_UP':      50,   # 选出→T+1涨停
    'B_BIG_RISE':      20,   # 选出→T+1涨≥5%
    'C_SMALL_RISE':     5,   # 选出→T+1涨0~5%
    'D_FLAT':           0,   # 选出→T+1持平(-1%~1%)
    'P1_DROP':        -20,   # 选出→T+1跌≥5%
    'P2_LIMIT_DOWN':  -50,   # 选出→T+1跌停
    'MISS_PER_STOCK':  -5,   # 每只漏选（基础惩罚）
    'MISS_BIG_RISE':  -15,   # 漏选大涨股(≥5%)
    'MISS_LIMIT_UP':  -25,   # 漏选涨停股
    'ZERO_CATCH':     -50,   # 全市场≥50只涨但命中0
    'PERFECT_DAY':     50,   # 所有上涨股全部命中+无误判
}

# 涨跌幅阈值
LIMIT_UP_PCT = 9.8       # 涨停判定（主板10%/创业板科创20%/北交所30%取最低）
LIMIT_DOWN_PCT = -9.8    # 跌停
BIG_RISE_PCT = 5.0       # 大涨
SMALL_RISE_PCT = 0.1     # 微涨
BIG_DROP_PCT = -5.0      # 大跌
FLAT_RANGE = 1.0         # 持平范围

# 市场豁免阈值
MARKET_ZERO_RISE_EXEMPT = 0     # 0只上涨=完全豁免
MARKET_FEW_RISE_THRESHOLD = 3   # <3只上涨=减免50%
MARK_MANY_RISE_THRESHOLD = 50   # ≥50只上涨但0命中=额外惩罚


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 数据结构
# ═══════════════════════════════════════════════════════════════

class RewardTier(Enum):
    """奖励等级"""
    S = "S_TREND_START"       # 圣杯级：涨停+连续上涨
    A = "A_LIMIT_UP"          # 精准级：涨停
    B = "B_BIG_RISE"          # 优良级：大涨
    C = "C_SMALL_RISE"        # 合格级：微涨
    D = "D_FLAT"              # 持平
    P1 = "P1_DROP"            # 误判：下跌
    P2 = "P2_LIMIT_DOWN"      # 严重误判：跌停
    MISS = "MISS"             # 漏选
    ZERO = "ZERO_CATCH"       # 零命中
    PERFECT = "PERFECT_DAY"   # 完美日


@dataclass
class StockPick:
    """T日尾盘选股记录"""
    code: str
    name: str
    industry: str
    pick_date: str               # T日 (YYYY-MM-DD)
    pick_score: float            # V13.2综合评分
    pick_recommendation: str     # BUY/WATCH/HOLD
    t_close_price: float         # T日收盘价
    t_change_pct: float          # T日涨跌幅
    t_was_limit_up: bool         # T日是否已涨停（应为False）
    t_volatility: float = 0.0    # T日波动率
    t_turnover: float = 0.0      # T日换手率
    t_volume_ratio: float = 0.0  # T日量比
    board_type: str = ""         # 板别
    m46_confidence: float = 0.0  # M46置信度
    m57_alpha: float = 0.0       # M57 Alpha值


@dataclass
class T1Outcome:
    """T+1日实际结果"""
    code: str
    name: str
    t1_date: str                 # T+1日
    t1_open: float               # T+1开盘价
    t1_close: float              # T+1收盘价
    t1_high: float               # T+1最高价
    t1_low: float                # T+1最低价
    t1_change_pct: float         # T+1涨跌幅 (vs T日收盘)
    t1_was_limit_up: bool        # T+1是否涨停
    t1_was_limit_down: bool      # T+1是否跌停
    t1_volume: float = 0.0       # T+1成交量
    t1_turnover: float = 0.0     # T+1换手率
    # T+2延续数据（可选，用于趋势判定）
    t2_change_pct: Optional[float] = None   # T+2涨跌幅
    t2_was_limit_up: Optional[bool] = None  # T+2是否涨停
    t3_change_pct: Optional[float] = None   # T+3涨跌幅（3日趋势）


@dataclass
class RewardRecord:
    """单只股票的奖惩记录"""
    code: str
    name: str
    pick_date: str
    t1_date: str
    tier: str                    # RewardTier枚举值
    score: float                 # 奖惩分值
    t_change_pct: float          # T日涨跌幅
    t1_change_pct: float         # T+1涨跌幅
    t2_change_pct: Optional[float]   # T+2涨跌幅
    trend_started: bool          # 是否启动了连续上涨趋势
    was_picked: bool             # 是否被系统选出
    was_missed: bool             # 是否为漏选
    reason: str                  # 奖惩原因说明
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DailyRewardSummary:
    """每日奖惩汇总"""
    date: str                    # T+1日期
    pick_date: str               # T日选股日期
    total_picks: int             # 选出总数
    total_hits: int              # 命中数（T+1上涨）
    total_limit_up_hits: int     # 涨停命中数
    total_misses: int            # 漏选数
    total_market_rising: int     # 全市场上涨股数
    total_market_limit_up: int   # 全市场涨停股数
    
    # 奖惩分值
    pick_reward: float           # 选股奖励（命中）
    pick_penalty: float          # 选股惩罚（误判）
    miss_penalty: float          # 漏选惩罚
    bonus_penalty: float         # 额外惩罚（零命中等）
    perfect_bonus: float         # 完美日奖励
    
    daily_total: float           # 每日总得分
    cumulative_total: float      # 累计总得分
    
    # 质量指标
    precision: float             # 命中率 = 命中/选出
    recall: float                # 召回率 = 命中/全市场可选上涨股
    f1_score: float              # F1
    trend_start_rate: float      # 趋势启动率 = 趋势启动/命中
    
    # 市场环境
    market_exempt: bool          # 是否市场豁免
    exempt_reason: str           # 豁免原因
    
    tier_distribution: Dict[str, int] = field(default_factory=dict)  # 各等级分布


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 奖惩计算核心
# ═══════════════════════════════════════════════════════════════

class RewardCalculator:
    """奖惩计算器"""

    @staticmethod
    def classify_pick_outcome(pick: StockPick, outcome: T1Outcome) -> Tuple[RewardTier, float, str]:
        """
        分类选股结果，返回(等级, 分值, 原因)
        """
        t1_chg = outcome.t1_change_pct
        t2_chg = outcome.t2_change_pct

        # 检查趋势启动：T+1涨停且T+2继续涨
        if outcome.t1_was_limit_up and t2_chg is not None and t2_chg > 0:
            return (
                RewardTier.S,
                REWARD_SCORES['S_TREND_START'],
                f"圣杯命中! T+1涨停({t1_chg:+.2f}%) → T+2续涨({t2_chg:+.2f}%)，连续上涨趋势启动"
            )

        # T+1涨停
        if outcome.t1_was_limit_up:
            trend_note = ""
            if t2_chg is not None:
                if t2_chg > 0:
                    trend_note = f"，T+2续涨({t2_chg:+.2f}%)"
                else:
                    trend_note = f"，但T+2回落({t2_chg:+.2f}%)"
            return (
                RewardTier.A,
                REWARD_SCORES['A_LIMIT_UP'],
                f"精准命中! T+1涨停({t1_chg:+.2f}%){trend_note}"
            )

        # T+1大涨(≥5%)
        if t1_chg >= BIG_RISE_PCT:
            return (
                RewardTier.B,
                REWARD_SCORES['B_BIG_RISE'],
                f"方向正确! T+1大涨({t1_chg:+.2f}%)"
            )

        # T+1微涨
        if t1_chg > SMALL_RISE_PCT:
            return (
                RewardTier.C,
                REWARD_SCORES['C_SMALL_RISE'],
                f"微涨({t1_chg:+.2f}%)，方向正确但力度不足"
            )

        # T+1持平
        if abs(t1_chg) <= FLAT_RANGE:
            return (
                RewardTier.D,
                REWARD_SCORES['D_FLAT'],
                f"持平({t1_chg:+.2f}%)，无方向性收益"
            )

        # T+1跌停
        if outcome.t1_was_limit_down:
            return (
                RewardTier.P2,
                REWARD_SCORES['P2_LIMIT_DOWN'],
                f"严重误判! T+1跌停({t1_chg:+.2f}%)"
            )

        # T+1大跌
        if t1_chg <= BIG_DROP_PCT:
            return (
                RewardTier.P1,
                REWARD_SCORES['P1_DROP'],
                f"误判! T+1大跌({t1_chg:+.2f}%)"
            )

        # T+1小跌
        return (
            RewardTier.P1,
            REWARD_SCORES['P1_DROP'] * 0.5,  # 小跌减半惩罚
            f"小跌({t1_chg:+.2f}%)，方向错误"
        )

    @staticmethod
    def classify_miss(outcome: T1Outcome, was_t_limit_up: bool) -> Tuple[RewardTier, float, str]:
        """
        分类漏选结果，返回(等级, 分值, 原因)
        was_t_limit_up: 该股票T日是否已涨停（如果已涨停则不算漏选）
        """
        if was_t_limit_up:
            return (RewardTier.MISS, 0, "T日已涨停，非可选标的，不计漏选")

        t1_chg = outcome.t1_change_pct

        if outcome.t1_was_limit_up:
            return (
                RewardTier.MISS,
                REWARD_SCORES['MISS_LIMIT_UP'],
                f"漏选涨停股! T+1涨停({t1_chg:+.2f}%)，T日尾盘未涨停但系统未选出"
            )

        if t1_chg >= BIG_RISE_PCT:
            return (
                RewardTier.MISS,
                REWARD_SCORES['MISS_BIG_RISE'],
                f"漏选大涨股! T+1涨{t1_chg:+.2f}%，T日尾盘未涨停但系统未选出"
            )

        if t1_chg > SMALL_RISE_PCT:
            return (
                RewardTier.MISS,
                REWARD_SCORES['MISS_PER_STOCK'],
                f"漏选上涨股: T+1涨{t1_chg:+.2f}%"
            )

        return (RewardTier.MISS, 0, "T+1未上涨，不计漏选")

    @staticmethod
    def check_market_exemption(
        market_rising_count: int,
        market_limit_up_count: int
    ) -> Tuple[bool, float, str]:
        """
        检查市场豁免条件
        返回 (是否豁免, 豁免比例, 原因)
        """
        total_rising = market_rising_count + market_limit_up_count

        if total_rising == 0:
            return (True, 1.0, "T+1全市场无一只上涨/涨停股，完全豁免漏选惩罚")

        if total_rising < MARKET_FEW_RISE_THRESHOLD:
            return (True, 0.5, f"T+1全市场仅{total_rising}只上涨股，减免50%漏选惩罚")

        return (False, 0.0, "")

    @staticmethod
    def check_zero_catch(
        total_hits: int,
        market_rising_count: int
    ) -> Tuple[bool, float, str]:
        """
        检查零命中惩罚
        """
        if total_hits == 0 and market_rising_count >= MARK_MANY_RISE_THRESHOLD:
            return (
                True,
                REWARD_SCORES['ZERO_CATCH'],
                f"严重失职! 全市场{market_rising_count}只上涨股但系统命中0只"
            )
        return (False, 0, "")

    @staticmethod
    def check_perfect_day(
        total_picks: int,
        total_hits: int,
        total_misses: int,
        total_penalty_picks: int
    ) -> Tuple[bool, float, str]:
        """
        检查完美日奖励
        条件：选出≥3只，全部命中，无漏选，无误判
        """
        if (total_picks >= 3 and
            total_hits == total_picks and
            total_misses == 0 and
            total_penalty_picks == 0):
            return (
                True,
                REWARD_SCORES['PERFECT_DAY'],
                f"完美日! {total_picks}只全部命中，零漏选零误判"
            )
        return (False, 0, "")


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 奖惩引擎主类
# ═══════════════════════════════════════════════════════════════

class RewardEngine:
    """
    圣杯奖惩引擎
    
    使用方式：
        engine = RewardEngine()
        
        # T日选股
        picks = [StockPick(...), ...]
        engine.record_picks(picks, pick_date='2026-06-24')
        
        # T+1验证（需要TDX获取T+1实际数据）
        outcomes = [T1Outcome(...), ...]  # 全市场T+1结果
        summary = engine.evaluate(picks, outcomes, market_rising=50, market_limit_up=10)
        
        # 查看奖惩报告
        print(engine.format_report(summary))
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        self._ensure_db()
        self.calc = RewardCalculator()

    def _ensure_db(self):
        """创建SQLite表"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 奖惩明细表
        c.execute("""
            CREATE TABLE IF NOT EXISTS reward_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT,
                pick_date TEXT NOT NULL,
                t1_date TEXT NOT NULL,
                tier TEXT NOT NULL,
                score REAL NOT NULL,
                t_change_pct REAL,
                t1_change_pct REAL,
                t2_change_pct REAL,
                trend_started INTEGER DEFAULT 0,
                was_picked INTEGER DEFAULT 1,
                was_missed INTEGER DEFAULT 0,
                reason TEXT,
                detail TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 每日汇总表
        c.execute("""
            CREATE TABLE IF NOT EXISTS reward_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                t1_date TEXT NOT NULL UNIQUE,
                pick_date TEXT NOT NULL,
                total_picks INTEGER,
                total_hits INTEGER,
                total_limit_up_hits INTEGER,
                total_misses INTEGER,
                total_market_rising INTEGER,
                total_market_limit_up INTEGER,
                pick_reward REAL,
                pick_penalty REAL,
                miss_penalty REAL,
                bonus_penalty REAL,
                perfect_bonus REAL,
                daily_total REAL,
                cumulative_total REAL,
                precision REAL,
                recall REAL,
                f1_score REAL,
                trend_start_rate REAL,
                market_exempt INTEGER DEFAULT 0,
                exempt_reason TEXT,
                tier_distribution TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 奖惩趋势表（7日滚动）
        c.execute("""
            CREATE TABLE IF NOT EXISTS reward_trend (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                end_date TEXT NOT NULL,
                window_days INTEGER DEFAULT 7,
                avg_daily_score REAL,
                total_score REAL,
                best_day_score REAL,
                worst_day_score REAL,
                total_hits INTEGER,
                total_misses INTEGER,
                avg_precision REAL,
                avg_recall REAL,
                trend_direction TEXT,
                improving_streak INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()

    # ── 记录T日选股 ──

    def record_picks(self, picks: List[StockPick], pick_date: str):
        """记录T日尾盘选股结果"""
        # 验证：T日选出的股票不应是已涨停的
        invalid = [p for p in picks if p.t_was_limit_up]
        if invalid:
            print(f"[警告] {len(invalid)}只股票T日已涨停，不应作为尾盘选股标的:")
            for p in invalid:
                print(f"  - {p.code} {p.name} T日涨幅={p.t_change_pct:+.2f}%")

        # 保存到JSON（等待T+1验证）
        picks_file = os.path.join(DATA_DIR, f'picks_{pick_date}.json')
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(picks_file, 'w', encoding='utf-8') as f:
            json.dump([asdict(p) for p in picks], f, ensure_ascii=False, indent=2)
        print(f"[奖惩引擎] T日选股已记录: {len(picks)}只 → {picks_file}")

    def load_picks(self, pick_date: str) -> List[StockPick]:
        """加载T日选股记录"""
        picks_file = os.path.join(DATA_DIR, f'picks_{pick_date}.json')
        if not os.path.exists(picks_file):
            return []
        with open(picks_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return [StockPick(**d) for d in data]

    # ── T+1评估 ──

    def evaluate(
        self,
        picks: List[StockPick],
        all_outcomes: List[T1Outcome],
        market_rising_count: int = 0,
        market_limit_up_count: int = 0,
        t1_date: str = None
    ) -> DailyRewardSummary:
        """
        执行T+1奖惩评估
        
        参数:
            picks: T日选出的股票列表
            all_outcomes: 全市场T+1实际结果（含选出和未选出的）
            market_rising_count: 全市场T+1上涨股数（不含涨停）
            market_limit_up_count: 全市场T+1涨停股数
            t1_date: T+1日期
        """
        t1_date = t1_date or datetime.now().strftime('%Y-%m-%d')
        pick_date = picks[0].pick_date if picks else t1_date

        # 按code索引
        outcome_map = {o.code: o for o in all_outcomes}
        pick_codes = {p.code for p in picks}

        # 分类所有结果
        rewards: List[RewardRecord] = []

        # 1. 评估选出的股票
        hit_count = 0
        limit_up_hits = 0
        penalty_picks = 0
        trend_starts = 0
        tier_dist = defaultdict(int)

        for pick in picks:
            outcome = outcome_map.get(pick.code)
            if outcome is None:
                # 无T+1数据，跳过
                continue

            tier, score, reason = self.calc.classify_pick_outcome(pick, outcome)
            trend_started = (
                outcome.t1_was_limit_up and
                outcome.t2_change_pct is not None and
                outcome.t2_change_pct > 0
            )

            if tier in (RewardTier.S, RewardTier.A, RewardTier.B, RewardTier.C):
                hit_count += 1
                if tier in (RewardTier.S, RewardTier.A):
                    limit_up_hits += 1
                if trend_started:
                    trend_starts += 1
            elif tier in (RewardTier.P1, RewardTier.P2):
                penalty_picks += 1

            tier_dist[tier.name] += 1
            rewards.append(RewardRecord(
                code=pick.code,
                name=pick.name,
                pick_date=pick_date,
                t1_date=t1_date,
                tier=tier.name,
                score=score,
                t_change_pct=pick.t_change_pct,
                t1_change_pct=outcome.t1_change_pct,
                t2_change_pct=outcome.t2_change_pct,
                trend_started=trend_started,
                was_picked=True,
                was_missed=False,
                reason=reason,
                detail={
                    'pick_score': pick.pick_score,
                    'm46_confidence': pick.m46_confidence,
                    'm57_alpha': pick.m57_alpha,
                    't1_high': outcome.t1_high,
                    't1_low': outcome.t1_low,
                }
            ))

        # 2. 评估漏选的股票
        miss_count = 0
        miss_penalty_total = 0.0

        # 市场豁免检查
        is_exempt, exempt_ratio, exempt_reason = self.calc.check_market_exemption(
            market_rising_count, market_limit_up_count
        )

        for outcome in all_outcomes:
            if outcome.code in pick_codes:
                continue  # 已评估

            # 检查T日是否已涨停（需要从outcomes中获取T日信息）
            # 这里简化：如果T+1涨幅很高但T日未涨停（通过T+1涨幅推断）
            # 实际中需要T日数据，这里用outcome中的信息
            t_was_limit_up = False  # 默认未涨停（实际应从T日数据获取）

            tier, score, reason = self.calc.classify_miss(outcome, t_was_limit_up)

            if tier == RewardTier.MISS and score == 0:
                continue  # 非上涨股，不计漏选

            miss_count += 1
            if is_exempt:
                score *= (1 - exempt_ratio)  # 豁免减免

            miss_penalty_total += score
            tier_dist[tier.name] += 1
            rewards.append(RewardRecord(
                code=outcome.code,
                name=outcome.name,
                pick_date=pick_date,
                t1_date=t1_date,
                tier=tier.name,
                score=score,
                t_change_pct=0,  # 未知
                t1_change_pct=outcome.t1_change_pct,
                t2_change_pct=outcome.t2_change_pct,
                trend_started=False,
                was_picked=False,
                was_missed=True,
                reason=reason,
                detail={
                    't1_high': outcome.t1_high,
                    't1_low': outcome.t1_low,
                }
            ))

        # 3. 计算汇总
        pick_reward = sum(r.score for r in rewards if r.was_picked and r.score > 0)
        pick_penalty = sum(abs(r.score) for r in rewards if r.was_picked and r.score < 0)
        miss_penalty = abs(miss_penalty_total)

        # 零命中检查
        zero_catch, zero_score, zero_reason = self.calc.check_zero_catch(
            hit_count, market_rising_count + market_limit_up_count
        )
        bonus_penalty = abs(zero_score) if zero_catch else 0

        # 完美日检查
        perfect, perfect_score, perfect_reason = self.calc.check_perfect_day(
            len(picks), hit_count, miss_count, penalty_picks
        )
        perfect_bonus = perfect_score if perfect else 0

        daily_total = pick_reward - pick_penalty - miss_penalty - bonus_penalty + perfect_bonus

        # 质量指标
        total_picks_valid = len([r for r in rewards if r.was_picked])
        precision = hit_count / total_picks_valid if total_picks_valid > 0 else 0
        available_rising = market_rising_count + market_limit_up_count
        recall = limit_up_hits / available_rising if available_rising > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        trend_rate = trend_starts / hit_count if hit_count > 0 else 0

        # 累计得分
        cumulative = self._get_cumulative_score() + daily_total

        summary = DailyRewardSummary(
            date=t1_date,
            pick_date=pick_date,
            total_picks=total_picks_valid,
            total_hits=hit_count,
            total_limit_up_hits=limit_up_hits,
            total_misses=miss_count,
            total_market_rising=market_rising_count,
            total_market_limit_up=market_limit_up_count,
            pick_reward=round(pick_reward, 2),
            pick_penalty=round(pick_penalty, 2),
            miss_penalty=round(miss_penalty, 2),
            bonus_penalty=round(bonus_penalty, 2),
            perfect_bonus=round(perfect_bonus, 2),
            daily_total=round(daily_total, 2),
            cumulative_total=round(cumulative, 2),
            precision=round(precision, 4),
            recall=round(recall, 4),
            f1_score=round(f1, 4),
            trend_start_rate=round(trend_rate, 4),
            market_exempt=is_exempt,
            exempt_reason=exempt_reason,
            tier_distribution=dict(tier_dist),
        )

        # 持久化
        self._save_rewards(rewards, summary)

        return summary

    def _get_cumulative_score(self) -> float:
        """获取历史累计得分"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT MAX(cumulative_total) FROM reward_summary")
        row = c.fetchone()
        conn.close()
        return row[0] if row and row[0] is not None else 0.0

    def _save_rewards(self, rewards: List[RewardRecord], summary: DailyRewardSummary):
        """保存奖惩记录到SQLite"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 保存明细
        for r in rewards:
            c.execute("""
                INSERT INTO reward_records
                (code, name, pick_date, t1_date, tier, score,
                 t_change_pct, t1_change_pct, t2_change_pct,
                 trend_started, was_picked, was_missed, reason, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r.code, r.name, r.pick_date, r.t1_date, r.tier, r.score,
                r.t_change_pct, r.t1_change_pct, r.t2_change_pct,
                int(r.trend_started), int(r.was_picked), int(r.was_missed),
                r.reason, json.dumps(r.detail, ensure_ascii=False)
            ))

        # 保存汇总（UPSERT）
        c.execute("""
            INSERT OR REPLACE INTO reward_summary
            (t1_date, pick_date, total_picks, total_hits, total_limit_up_hits,
             total_misses, total_market_rising, total_market_limit_up,
             pick_reward, pick_penalty, miss_penalty, bonus_penalty, perfect_bonus,
             daily_total, cumulative_total, precision, recall, f1_score,
             trend_start_rate, market_exempt, exempt_reason, tier_distribution)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary.date, summary.pick_date,
            summary.total_picks, summary.total_hits, summary.total_limit_up_hits,
            summary.total_misses, summary.total_market_rising, summary.total_market_limit_up,
            summary.pick_reward, summary.pick_penalty, summary.miss_penalty,
            summary.bonus_penalty, summary.perfect_bonus,
            summary.daily_total, summary.cumulative_total,
            summary.precision, summary.recall, summary.f1_score,
            summary.trend_start_rate,
            int(summary.market_exempt), summary.exempt_reason,
            json.dumps(summary.tier_distribution, ensure_ascii=False)
        ))

        conn.commit()
        conn.close()

    # ── 趋势分析 ──

    def compute_trend(self, window_days: int = 7) -> Dict:
        """计算滚动趋势"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            SELECT t1_date, daily_total, precision, recall,
                   total_hits, total_misses
            FROM reward_summary
            ORDER BY t1_date DESC
            LIMIT ?
        """, (window_days,))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return {'window_days': window_days, 'status': 'no_data'}

        scores = [r[1] or 0 for r in rows]
        precisions = [r[2] or 0 for r in rows]
        recalls = [r[3] or 0 for r in rows]
        total_hits = sum(r[4] or 0 for r in rows)
        total_misses = sum(r[5] or 0 for r in rows)

        avg_score = sum(scores) / len(scores)
        total_score = sum(scores)

        # 趋势方向
        if len(scores) >= 3:
            recent_avg = sum(scores[:3]) / 3
            older_avg = sum(scores[3:]) / max(len(scores) - 3, 1)
            if recent_avg > older_avg * 1.1:
                direction = "IMPROVING"
            elif recent_avg < older_avg * 0.9:
                direction = "DECLINING"
            else:
                direction = "STAGNANT"
        else:
            direction = "INSUFFICIENT_DATA"

        # 改进连续数
        improving_streak = 0
        for i in range(len(scores) - 1):
            if scores[i] > scores[i + 1]:
                improving_streak += 1
            else:
                break

        trend = {
            'window_days': window_days,
            'end_date': rows[0][0],
            'start_date': rows[-1][0],
            'avg_daily_score': round(avg_score, 2),
            'total_score': round(total_score, 2),
            'best_day_score': round(max(scores), 2),
            'worst_day_score': round(min(scores), 2),
            'total_hits': total_hits,
            'total_misses': total_misses,
            'avg_precision': round(sum(precisions) / len(precisions), 4),
            'avg_recall': round(sum(recalls) / len(recalls), 4),
            'trend_direction': direction,
            'improving_streak': improving_streak,
            'daily_scores': list(reversed(scores)),
            'daily_dates': list(reversed([r[0] for r in rows])),
        }

        # 保存趋势
        self._save_trend(trend)
        return trend

    def _save_trend(self, trend: Dict):
        """保存趋势记录"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            INSERT INTO reward_trend
            (end_date, window_days, avg_daily_score, total_score,
             best_day_score, worst_day_score, total_hits, total_misses,
             avg_precision, avg_recall, trend_direction, improving_streak)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trend['end_date'], trend['window_days'],
            trend['avg_daily_score'], trend['total_score'],
            trend['best_day_score'], trend['worst_day_score'],
            trend['total_hits'], trend['total_misses'],
            trend['avg_precision'], trend['avg_recall'],
            trend['trend_direction'], trend['improving_streak']
        ))
        conn.commit()
        conn.close()

    # ── 报告生成 ──

    def format_report(self, summary: DailyRewardSummary) -> str:
        """格式化每日奖惩报告"""
        lines = []
        lines.append("=" * 70)
        lines.append("  V13.2 圣杯奖惩报告")
        lines.append(f"  T日选股: {summary.pick_date} | T+1验证: {summary.date}")
        lines.append("=" * 70)

        # 核心指标
        lines.append("")
        lines.append("【核心指标】")
        lines.append(f"  每日得分:     {summary.daily_total:+.1f} 分")
        lines.append(f"  累计得分:     {summary.cumulative_total:+.1f} 分")
        lines.append(f"  命中率:       {summary.precision*100:.1f}% ({summary.total_hits}/{summary.total_picks})")
        lines.append(f"  召回率:       {summary.recall*100:.1f}% (命中/全市场{summary.total_market_rising + summary.total_market_limit_up})")
        lines.append(f"  F1分数:       {summary.f1_score:.4f}")
        lines.append(f"  趋势启动率:   {summary.trend_start_rate*100:.1f}% ({summary.total_hits}命中中)")
        lines.append(f"  涨停命中:     {summary.total_limit_up_hits} 只")

        # 奖惩明细
        lines.append("")
        lines.append("【奖惩明细】")
        lines.append(f"  + 选股奖励:   +{summary.pick_reward:.1f} 分 (命中)")
        lines.append(f"  - 选股惩罚:   -{summary.pick_penalty:.1f} 分 (误判)")
        lines.append(f"  - 漏选惩罚:   -{summary.miss_penalty:.1f} 分 ({summary.total_misses}只漏选)")
        if summary.bonus_penalty > 0:
            lines.append(f"  - 额外惩罚:   -{summary.bonus_penalty:.1f} 分 (零命中)")
        if summary.perfect_bonus > 0:
            lines.append(f"  + 完美奖励:   +{summary.perfect_bonus:.1f} 分 (完美日!)")

        # 市场环境
        if summary.market_exempt:
            lines.append("")
            lines.append(f"【市场豁免】 {summary.exempt_reason}")

        # 等级分布
        lines.append("")
        lines.append("【等级分布】")
        tier_names = {
            'S': 'S级-圣杯(涨停+续涨)',
            'A': 'A级-精准(涨停)',
            'B': 'B级-优良(大涨≥5%)',
            'C': 'C级-合格(微涨)',
            'D': 'D级-持平',
            'P1': 'P1-误判(下跌)',
            'P2': 'P2-严重(跌停)',
            'MISS': '漏选',
        }
        for tier, name in tier_names.items():
            count = summary.tier_distribution.get(tier, 0)
            if count > 0:
                lines.append(f"  {name}: {count} 只")

        lines.append("")
        lines.append("=" * 70)

        return '\n'.join(lines)

    def format_trend_report(self, trend: Dict) -> str:
        """格式化趋势报告"""
        lines = []
        lines.append("=" * 70)
        lines.append(f"  V13.2 奖惩趋势分析 ({trend['window_days']}日滚动)")
        lines.append("=" * 70)

        lines.append("")
        lines.append(f"  区间: {trend.get('start_date', '?')} ~ {trend.get('end_date', '?')}")
        lines.append(f"  总得分:       {trend['total_score']:+.1f} 分")
        lines.append(f"  日均得分:     {trend['avg_daily_score']:+.1f} 分")
        lines.append(f"  最佳日:       {trend['best_day_score']:+.1f} 分")
        lines.append(f"  最差日:       {trend['worst_day_score']:+.1f} 分")
        lines.append(f"  总命中:       {trend['total_hits']} 只")
        lines.append(f"  总漏选:       {trend['total_misses']} 只")
        lines.append(f"  平均命中率:   {trend['avg_precision']*100:.1f}%")
        lines.append(f"  平均召回率:   {trend['avg_recall']*100:.1f}%")

        # 趋势方向
        dir_map = {
            'IMPROVING': 'UPTRAJ 改善中',
            'DECLINING': 'DOWNTRAJ 退步中',
            'STAGNANT': 'STAGNANT 停滞',
            'INSUFFICIENT_DATA': 'DATA 不足',
        }
        direction = trend.get('trend_direction', 'INSUFFICIENT_DATA')
        lines.append(f"  趋势方向:     {dir_map.get(direction, direction)}")
        if trend.get('improving_streak', 0) > 0:
            lines.append(f"  改善连续:     {trend['improving_streak']} 天")

        # 每日得分迷你图
        if 'daily_scores' in trend and trend['daily_scores']:
            lines.append("")
            lines.append("【每日得分】")
            scores = trend['daily_scores']
            dates = trend.get('daily_dates', [''] * len(scores))
            max_abs = max(abs(s) for s in scores) if scores else 1
            for i, (s, d) in enumerate(zip(scores, dates)):
                bar_len = int(abs(s) / max_abs * 30) if max_abs > 0 else 0
                bar = ('█' * bar_len) if s >= 0 else ('░' * bar_len)
                sign = '+' if s >= 0 else ''
                lines.append(f"  {d} |{bar:>30s}| {sign}{s:.1f}")

        lines.append("")
        lines.append("=" * 70)
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 4: TDX T+1数据获取指令生成器
# ═══════════════════════════════════════════════════════════════

class T1DataFetcher:
    """
    生成T+1验证所需的TDX MCP调用指令
    
    T+1验证需要以下数据：
    1. 全市场T+1涨停股列表 → tdx_screener "涨停"
    2. 全市场T+1涨幅≥5%的股票 → tdx_screener "涨幅5%"
    3. 每只选出股票的T+1日K线 → tdx_kline period=4 wantNum=3
    4. 每只选出股票的T+1实时行情 → tdx_quotes
    """

    @staticmethod
    def generate_fetch_instructions(
        picks: List[StockPick],
        t1_date: str = None
    ) -> str:
        """生成Agent TDX数据获取指令"""
        t1_date = t1_date or datetime.now().strftime('%Y-%m-%d')

        lines = []
        lines.append("# T+1验证数据获取指令")
        lines.append(f"# T+1日期: {t1_date}")
        lines.append(f"# 需验证选股: {len(picks)}只")
        lines.append("")

        # Step 1: 全市场涨停股
        lines.append("## Step 1: 获取全市场T+1涨停股")
        lines.append('调用: tdx_screener(query="涨停", market="A股")')
        lines.append("目的: 获取T+1全市场涨停股列表，用于漏选检查")
        lines.append("")

        # Step 2: 全市场大涨股
        lines.append("## Step 2: 获取全市场T+1涨幅≥5%的股票")
        lines.append('调用: tdx_screener(query="涨幅大于5%", market="A股")')
        lines.append("目的: 获取T+1全市场大涨股列表，用于漏选检查")
        lines.append("")

        # Step 3: 每只选出股票的T+1日K线
        lines.append("## Step 3: 获取选出股票的T+1日K线")
        for pick in picks:
            setcode = '1' if pick.code.startswith('6') else ('0' if pick.code[0] in '03' else '2')
            lines.append(f'调用: tdx_kline(code="{pick.code}", setcode="{setcode}", period="4", wantNum="5")')
            lines.append(f'  # {pick.name}({pick.code}) — 需要最近5根日K(T-1/T/T+1/T+2/T+3)')
        lines.append("")

        # Step 4: 每只选出股票的T+1实时行情
        lines.append("## Step 4: 获取选出股票的T+1实时行情")
        for pick in picks:
            setcode = '1' if pick.code.startswith('6') else ('0' if pick.code[0] in '03' else '2')
            lines.append(f'调用: tdx_quotes(code="{pick.code}", setcode="{setcode}")')
            lines.append(f'  # {pick.name}({pick.code}) — 获取T+1收盘价/开盘价/最高/最低')
        lines.append("")

        # 构建缓存格式
        lines.append("## Step 5: 构建T+1缓存JSON")
        lines.append("将以上数据写入 data/t1_outcomes_{}.json:".format(t1_date))
        lines.append("```json")
        lines.append("""{
  "t1_date": "DATE",
  "screener_limit_up": {
    "count": N,
    "stocks": [{"code":"...", "name":"...", "change_pct": 10.0, ...}, ...]
  },
  "screener_big_rise": {
    "count": N,
    "stocks": [{"code":"...", "name":"...", "change_pct": 5.5, ...}, ...]
  },
  "stock_outcomes": {
    "600519": {
      "code": "600519",
      "name": "贵州茅台",
      "t1_open": 1700.0,
      "t1_close": 1720.0,
      "t1_high": 1735.0,
      "t1_low": 1695.0,
      "t1_change_pct": 1.5,
      "t1_was_limit_up": false,
      "t1_was_limit_down": false,
      "t1_volume": 12345600,
      "t1_turnover": 0.8,
      "t2_change_pct": 0.5,
      "t2_was_limit_up": false,
      "t3_change_pct": -0.2
    }
  }
}
```""")
        lines.append("")
        lines.append("## Step 6: 运行奖惩引擎")
        lines.append("```bash")
        lines.append(f'cd "E:/WorkBuddy_dot_workbuddy/Claw" && python V13_2_RewardEngine.py --evaluate --t1-file data/t1_outcomes_{t1_date}.json --pick-date ' + picks[0].pick_date if picks else '')
        lines.append("```")

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description='V13.2 圣杯奖惩引擎')
    parser.add_argument('--evaluate', action='store_true', help='执行T+1评估')
    parser.add_argument('--t1-file', type=str, help='T+1结果JSON文件路径')
    parser.add_argument('--pick-date', type=str, help='T日选股日期')
    parser.add_argument('--trend', action='store_true', help='显示趋势报告')
    parser.add_argument('--history', type=int, default=7, help='历史趋势天数')
    parser.add_argument('--demo', action='store_true', help='演示模式（合成数据）')
    args = parser.parse_args()

    engine = RewardEngine()

    if args.demo:
        _run_demo(engine)
    elif args.evaluate:
        _run_evaluate(engine, args)
    elif args.trend:
        trend = engine.compute_trend(args.history)
        print(engine.format_trend_report(trend))
    else:
        parser.print_help()


def _run_demo(engine: RewardEngine):
    """演示模式：合成数据展示奖惩引擎"""
    print("\n[演示模式] 使用合成数据展示奖惩引擎工作流程\n")

    pick_date = '2026-06-24'
    t1_date = '2026-06-25'

    # 合成T日选股（5只未涨停股票）
    picks = [
        StockPick('600519', '贵州茅台', '食品饮料', pick_date, 0.72, 'BUY', 1680, 2.5, False,
                  t_volatility=1.2, t_turnover=0.5, t_volume_ratio=1.5, board_type='上海主板',
                  m46_confidence=0.68, m57_alpha=0.85),
        StockPick('300750', '宁德时代', '新能源', pick_date, 0.65, 'BUY', 210, 4.2, False,
                  t_volatility=2.8, t_turnover=1.8, t_volume_ratio=2.1, board_type='创业板',
                  m46_confidence=0.71, m57_alpha=0.72),
        StockPick('002230', '科大讯飞', 'AI算力', pick_date, 0.58, 'WATCH', 45, 3.8, False,
                  t_volatility=3.5, t_turnover=2.5, t_volume_ratio=1.8, board_type='深圳主板',
                  m46_confidence=0.55, m57_alpha=0.45),
        StockPick('688256', '寒武纪', 'AI芯片', pick_date, 0.62, 'BUY', 250, 6.5, False,
                  t_volatility=4.2, t_turnover=3.2, t_volume_ratio=2.5, board_type='科创板',
                  m46_confidence=0.66, m57_alpha=0.78),
        StockPick('300059', '东方财富', '券商', pick_date, 0.45, 'HOLD', 14, 1.2, False,
                  t_volatility=1.8, t_turnover=0.8, t_volume_ratio=1.0, board_type='创业板',
                  m46_confidence=0.42, m57_alpha=0.20),
    ]

    # 记录选股
    engine.record_picks(picks, pick_date)

    # 合成T+1结果（含选出和未选出的）
    outcomes = [
        # 选出的股票
        T1Outcome('600519', '贵州茅台', t1_date, 1685, 1720, 1735, 1695, 2.38, False, False,
                  t1_volume=15000000, t1_turnover=0.6, t2_change_pct=0.8, t3_change_pct=1.2),
        T1Outcome('300750', '宁德时代', t1_date, 215, 231, 233, 212, 10.0, True, False,
                  t1_volume=25000000, t1_turnover=2.0, t2_change_pct=3.5, t3_change_pct=1.8),
        T1Outcome('002230', '科大讯飞', t1_date, 45.5, 44.2, 46.0, 43.8, -1.78, False, False,
                  t1_volume=8000000, t1_turnover=2.2),
        T1Outcome('688256', '寒武纪', t1_date, 255, 275, 278, 252, 10.0, True, False,
                  t1_volume=12000000, t1_turnover=3.5, t2_change_pct=5.2, t3_change_pct=2.1),
        T1Outcome('300059', '东方财富', t1_date, 14.1, 13.8, 14.3, 13.6, -1.43, False, False,
                  t1_volume=5000000, t1_turnover=0.7),
        # 未选出的上涨股（漏选）
        T1Outcome('000725', '京东方A', t1_date, 4.2, 4.45, 4.48, 4.15, 5.95, False, False),
        T1Outcome('601012', '隆基绿能', t1_date, 18.5, 19.8, 20.0, 18.3, 7.03, False, False),
        T1Outcome('002594', '比亚迪', t1_date, 250, 265, 268, 248, 6.0, False, False),
        T1Outcome('300308', '中际旭创', t1_date, 160, 176, 178, 159, 10.0, True, False),
        T1Outcome('601899', '紫金矿业', t1_date, 12.5, 13.25, 13.4, 12.4, 6.0, False, False),
    ]

    # 评估
    summary = engine.evaluate(
        picks=picks,
        all_outcomes=outcomes,
        market_rising_count=8,   # 8只大涨(非涨停)
        market_limit_up_count=3,  # 3只涨停
        t1_date=t1_date
    )

    # 打印报告
    print(engine.format_report(summary))

    # 趋势
    print("\n")
    trend = engine.compute_trend(window_days=7)
    print(engine.format_trend_report(trend))

    # 打印TDX获取指令
    print("\n")
    fetcher = T1DataFetcher()
    print(fetcher.generate_fetch_instructions(picks, t1_date))


def _run_evaluate(engine: RewardEngine, args):
    """从JSON文件执行评估"""
    if not args.t1_file or not os.path.exists(args.t1_file):
        print(f"[错误] T+1结果文件不存在: {args.t1_file}")
        return

    with open(args.t1_file, 'r', encoding='utf-8') as f:
        t1_data = json.load(f)

    pick_date = args.pick_date or t1_data.get('pick_date')
    if not pick_date:
        print("[错误] 未指定 --pick-date")
        return

    picks = engine.load_picks(pick_date)
    if not picks:
        print(f"[错误] 未找到T日选股记录: {pick_date}")
        return

    # 解析T+1结果
    outcomes = []
    for code, data in t1_data.get('stock_outcomes', {}).items():
        outcomes.append(T1Outcome(
            code=code,
            name=data.get('name', code),
            t1_date=t1_data.get('t1_date', ''),
            t1_open=data.get('t1_open', 0),
            t1_close=data.get('t1_close', 0),
            t1_high=data.get('t1_high', 0),
            t1_low=data.get('t1_low', 0),
            t1_change_pct=data.get('t1_change_pct', 0),
            t1_was_limit_up=data.get('t1_was_limit_up', False),
            t1_was_limit_down=data.get('t1_was_limit_down', False),
            t1_volume=data.get('t1_volume', 0),
            t1_turnover=data.get('t1_turnover', 0),
            t2_change_pct=data.get('t2_change_pct'),
            t2_was_limit_up=data.get('t2_was_limit_up'),
            t3_change_pct=data.get('t3_change_pct'),
        ))

    market_rising = t1_data.get('screener_big_rise', {}).get('count', 0)
    market_limit_up = t1_data.get('screener_limit_up', {}).get('count', 0)

    summary = engine.evaluate(
        picks=picks,
        all_outcomes=outcomes,
        market_rising_count=market_rising,
        market_limit_up_count=market_limit_up,
        t1_date=t1_data.get('t1_date')
    )

    print(engine.format_report(summary))

    trend = engine.compute_trend(7)
    print('\n' + engine.format_trend_report(trend))


if __name__ == '__main__':
    main()
