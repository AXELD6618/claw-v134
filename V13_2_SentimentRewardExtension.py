#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 舆情奖惩扩展模块 (Sentiment Reward Extension)
为圣杯奖惩引擎增加舆情相关的奖惩机制

扩展 V13_2_RewardEngine.py:
- 继承 RewardEngine，添加舆情奖惩方法
- 新增舆情相关奖惩类型
- 与 sentiment_db.db 联动

新增奖惩类型:
1. 舆情发现奖励 (+10~+50分)
   - 提前N小时发现重要舆情 (提前量×10分，上限+50)
   - 重要舆情（importance_score>80）额外+20分
   
2. 舆情漏检惩罚 (-10~-30分)
   - 重要舆情（importance_score>70）未被系统发现
   - 惩罚 = - (importance_score / 10) 分，上限-30
   
3. 舆情解读奖励 (+20~+100分)
   - 正确解读舆情并指导交易成功
   - 奖励 = 交易盈利% × 10分，上限+100
   
4. 舆情误判惩罚 (-20~-50分)
   - 错误解读舆情导致亏损
   - 惩罚 = 交易亏损% × 10分，下限-50
   
5. 舆情响应速度奖励 (+5~+20分)
   - 快速响应突发舆情（1小时内）
   - 奖励 = (24 - 响应小时数) × 1分，上限+20

数据库变更:
- reward_records 表添加 column: reward_category (默认'stock_pick')
- 新增表 sentiment_reward_records 记录舆情奖惩明细

版本: V13.2
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# 导入原有的奖惩引擎
sys.path.insert(0, os.path.dirname(__file__))
from V13_2_RewardEngine import (
    RewardEngine, RewardCalculator, RewardRecord, DailyRewardSummary,
    RewardTier, REWARD_SCORES, DB_PATH as BASE_DB_PATH
)

# ── 配置 ─────────────────────────────────────────────────────
SENTIMENT_DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'sentiment_db.db')
SENTIMENT_REWARD_CATEGORY = 'sentiment'  # 舆情奖惩类别

# 舆情奖惩分值
SENTIMENT_REWARD_SCORES = {
    'DISCOVERY':      10,  # 基础发现奖励
    'DISCOVERY_BONUS': 20,  # 重要舆情额外奖励
    'DISCOVERY_HOUR_MULTIPLIER': 1,  # 提前每小时+1分
    'MISS_PENALTY_BASE':     -10,  # 基础漏检惩罚
    'CORRECT_INTERPRETATION': 50,  # 正确解读基础奖励
    'WRONG_INTERPRETATION':  -30,  # 错误解读基础惩罚
    'FAST_RESPONSE':   5,   # 快速响应基础奖励
    'FAST_RESPONSE_HOUR_MULTIPLIER': 1,  # 提前每小时+1分
}

# ── 日志配置 ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_reward.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 数据结构 ──────────────────────────────────────────────────
@dataclass
class SentimentRewardRecord:
    """舆情奖惩记录"""
    news_id: int
    news_title: str
    reward_type: str  # 'discovery'/'miss'/'correct_interpretation'/'wrong_interpretation'/'fast_response'
    score: float
    reason: str
    discovery_hours_ahead: float = 0.0  # 提前发现小时数
    response_hours: float = 0.0  # 响应耗时（小时）
    trading_outcome: str = ''  # 'success'/'failure'/'pending'
    related_stock_code: str = ''
    related_stock_name: str = ''
    created_time: str = ''
    
    def __post_init__(self):
        if not self.created_time:
            self.created_time = datetime.now().isoformat()

# ── 数据库扩展 ───────────────────────────────────────────────
def extend_reward_database(base_db_path: str = BASE_DB_PATH):
    """扩展原有奖惩数据库，添加舆情相关字段"""
    conn = sqlite3.connect(base_db_path)
    cur = conn.cursor()
    
    try:
        # 检查 reward_records 表是否有 reward_category 字段
        cur.execute("PRAGMA table_info(reward_records)")
        columns = [row[1] for row in cur.fetchall()]
        
        if 'reward_category' not in columns:
            cur.execute("ALTER TABLE reward_records ADD COLUMN reward_category TEXT DEFAULT 'stock_pick'")
            logger.info("✅ 已添加 reward_category 字段到 reward_records 表")
            
        if 'news_id' not in columns:
            cur.execute("ALTER TABLE reward_records ADD COLUMN news_id INTEGER DEFAULT NULL")
            logger.info("✅ 已添加 news_id 字段到 reward_records 表")
            
        if 'discovery_hours_ahead' not in columns:
            cur.execute("ALTER TABLE reward_records ADD COLUMN discovery_hours_ahead REAL DEFAULT 0.0")
            logger.info("✅ 已添加 discovery_hours_ahead 字段到 reward_records 表")
            
        if 'trading_outcome' not in columns:
            cur.execute("ALTER TABLE reward_records ADD COLUMN trading_outcome TEXT DEFAULT ''")
            logger.info("✅ 已添加 trading_outcome 字段到 reward_records 表")
            
        conn.commit()
        logger.info(f"✅ 数据库扩展完成: {base_db_path}")
        
    except Exception as e:
        logger.error(f"❌ 数据库扩展失败: {e}")
        conn.rollback()
        
    finally:
        conn.close()

def create_sentiment_reward_table(sentiment_db_path: str = SENTIMENT_DB_PATH):
    """在 sentiment_db.db 中创建舆情奖惩记录表"""
    os.makedirs(os.path.dirname(sentiment_db_path), exist_ok=True)
    
    conn = sqlite3.connect(sentiment_db_path)
    cur = conn.cursor()
    
    try:
        # 舆情奖惩明细表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS sentiment_reward_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id INTEGER NOT NULL,
                news_title TEXT,
                reward_type TEXT NOT NULL,
                score REAL NOT NULL,
                reason TEXT,
                discovery_hours_ahead REAL DEFAULT 0.0,
                response_hours REAL DEFAULT 0.0,
                trading_outcome TEXT DEFAULT '',
                related_stock_code TEXT DEFAULT '',
                related_stock_name TEXT DEFAULT '',
                created_time TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (news_id) REFERENCES news_processed(id)
            )
        ''')
        
        # 舆情奖惩每日汇总表
        cur.execute('''
            CREATE TABLE IF NOT EXISTS sentiment_reward_daily (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_discovery_rewards REAL DEFAULT 0,
                total_miss_penalties REAL DEFAULT 0,
                total_interpretation_rewards REAL DEFAULT 0,
                total_wrong_penalties REAL DEFAULT 0,
                total_fast_response_rewards REAL DEFAULT 0,
                net_sentiment_score REAL DEFAULT 0,
                news_count INTEGER DEFAULT 0,
                created_time TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        logger.info(f"✅ 舆情奖惩表创建完成: {sentiment_db_path}")
        
    except Exception as e:
        logger.error(f"❌ 创建表失败: {e}")
        conn.rollback()
        
    finally:
        conn.close()

# ── 舆情奖惩计算器 ──────────────────────────────────────────
class SentimentRewardCalculator:
    """舆情奖惩计算器"""
    
    def __init__(self):
        self.scores = SENTIMENT_REWARD_SCORES
        logger.info("✅ 舆情奖惩计算器初始化完成")
        
    def compute_discovery_reward(
        self,
        news_importance_score: float,
        hours_ahead: float = 0.0
    ) -> Tuple[float, str]:
        """
        计算舆情发现奖励
        
        参数:
            news_importance_score: 新闻重要性评分 (0-100)
            hours_ahead: 提前发现小时数（0=同时发现，正=提前）
            
        返回: (score, reason)
        """
        score = 0.0
        reasons = []
        
        # 1. 基础发现奖励
        if news_importance_score >= 50:
            score += self.scores['DISCOVERY']
            reasons.append(f"发现重要性{news_importance_score:.0f}分舆情+{self.scores['DISCOVERY']}分")
            
        # 2. 重要舆情额外奖励
        if news_importance_score >= 80:
            bonus = self.scores['DISCOVERY_BONUS']
            score += bonus
            reasons.append(f"重要舆情(≥80分)额外+{bonus}分")
            
        # 3. 提前发现奖励
        if hours_ahead > 0:
            time_bonus = min(
                hours_ahead * self.scores['DISCOVERY_HOUR_MULTIPLIER'],
                40.0  # 上限+40
            )
            score += time_bonus
            reasons.append(f"提前{hours_ahead:.1f}小时发现+{time_bonus:.0f}分")
            
        # 4. 上限
        score = min(score, 100.0)
        
        return score, '; '.join(reasons)
    
    def compute_miss_penalty(
        self,
        news_importance_score: float,
        hours_delay: float = 0.0
    ) -> Tuple[float, str]:
        """
        计算舆情漏检惩罚
        
        参数:
            news_importance_score: 新闻重要性评分 (0-100)
            hours_delay: 延迟发现小时数（正=延迟）
            
        返回: (score, reason)  # score为负数
        """
        # 只惩罚重要舆情
        if news_importance_score < 50:
            return 0.0, "舆情不重要，不惩罚"
            
        score = self.scores['MISS_PENALTY_BASE']
        reasons = []
        
        # 按重要性加大惩罚
        importance_penalty = - (news_importance_score / 10)
        score += importance_penalty
        reasons.append(f"重要性{news_importance_score:.0f}分，惩罚{importance_penalty:.0f}分")
        
        # 延迟发现加大惩罚
        if hours_delay > 2:
            delay_penalty = - min(hours_delay * 0.5, 10.0)
            score += delay_penalty
            reasons.append(f"延迟{hours_delay:.1f}小时发现，额外惩罚{delay_penalty:.0f}分")
            
        # 下限
        score = max(score, -50.0)
        
        return score, '; '.join(reasons)
    
    def compute_interpretation_reward(
        self,
        news_id: int,
        trading_profit_pct: float = 0.0,
        was_correct: bool = True
    ) -> Tuple[float, str]:
        """
        计算舆情解读奖励
        
        参数:
            news_id: 新闻ID
            trading_profit_pct: 交易盈利百分比（正=盈利，负=亏损）
            was_correct: 解读是否正确
            
        返回: (score, reason)
        """
        if not was_correct:
            # 错误解读，交给 compute_wrong_penalty
            return self.compute_wrong_penalty(news_id, trading_profit_pct)
            
        score = self.scores['CORRECT_INTERPRETATION']
        reasons = [f"正确解读舆情+{score}分"]
        
        # 交易盈利加成
        if trading_profit_pct > 0:
            profit_bonus = min(trading_profit_pct * 10, 50.0)
            score += profit_bonus
            reasons.append(f"交易盈利{trading_profit_pct:+.1f}%，加成+{profit_bonus:.0f}分")
            
        # 上限
        score = min(score, 150.0)
        
        return score, '; '.join(reasons)
    
    def compute_wrong_penalty(
        self,
        news_id: int,
        trading_loss_pct: float = 0.0
    ) -> Tuple[float, str]:
        """
        计算舆情误判惩罚
        
        参数:
            news_id: 新闻ID
            trading_loss_pct: 交易亏损百分比（负=亏损）
            
        返回: (score, reason)  # score为负数
        """
        score = self.scores['WRONG_INTERPRETATION']
        reasons = [f"错误解读舆情{score}分"]
        
        # 交易亏损加成
        if trading_loss_pct < 0:
            loss_penalty = max(trading_loss_pct * 10, -50.0)
            score += loss_penalty
            reasons.append(f"交易亏损{trading_loss_pct:+.1f}%，额外惩罚{loss_penalty:.0f}分")
            
        # 下限
        score = max(score, -100.0)
        
        return score, '; '.join(reasons)
    
    def compute_fast_response_reward(
        self,
        response_hours: float = 1.0
    ) -> Tuple[float, str]:
        """
        计算快速响应奖励
        
        参数:
            response_hours: 响应耗时（小时）
            
        返回: (score, reason)
        """
        if response_hours > 24:
            return 0.0, "响应超过24小时，无奖励"
            
        score = self.scores['FAST_RESPONSE']
        reasons = [f"快速响应基础+{score}分"]
        
        # 越快越好
        if response_hours <= 1:
            bonus = self.scores['FAST_RESPONSE_HOUR_MULTIPLIER'] * 15
            score += bonus
            reasons.append(f"1小时内响应，加成+{bonus:.0f}分")
        elif response_hours <= 6:
            bonus = self.scores['FAST_RESPONSE_HOUR_MULTIPLIER'] * 10
            score += bonus
            reasons.append(f"6小时内响应，加成+{bonus:.0f}分")
        elif response_hours <= 12:
            bonus = self.scores['FAST_RESPONSE_HOUR_MULTIPLIER'] * 5
            score += bonus
            reasons.append(f"12小时内响应，加成+{bonus:.0f}分")
            
        # 上限
        score = min(score, 50.0)
        
        return score, '; '.join(reasons)

# ── 舆情奖惩引擎（主类） ────────────────────────────────
class SentimentRewardEngine:
    """舆情奖惩引擎"""
    
    def __init__(
        self,
        base_db_path: str = None,
        sentiment_db_path: str = None
    ):
        self.base_db_path = base_db_path or BASE_DB_PATH
        self.sentiment_db_path = sentiment_db_path or SENTIMENT_DB_PATH
        
        # 扩展数据库
        extend_reward_database(self.base_db_path)
        create_sentiment_reward_table(self.sentiment_db_path)
        
        # 计算器
        self.calc = SentimentRewardCalculator()
        
        # 加载原有奖惩引擎（用于获取累计得分）
        self.base_engine = RewardEngine(db_path=self.base_db_path)
        
        logger.info(f"✅ 舆情奖惩引擎初始化完成")
        logger.info(f"   基础数据库: {self.base_db_path}")
        logger.info(f"   舆情数据库: {self.sentiment_db_path}")
        
    def evaluate_sentiment_discovery(
        self,
        news_id: int,
        news_title: str,
        news_importance_score: float,
        discovery_time: str,  # ISO格式时间
        publish_time: str     # ISO格式时间
    ) -> Tuple[float, str]:
        """
        评估舆情发现（由SentimentCollector调用）
        
        参数:
            news_id: 新闻ID
            news_title: 新闻标题
            news_importance_score: 重要性评分
            discovery_time: 发现时间
            publish_time: 发布时间
            
        返回: (score, reason)
        """
        # 计算提前发现小时数
        try:
            pub_dt = datetime.fromisoformat(publish_time.replace('Z', '+00:00'))
            disc_dt = datetime.fromisoformat(discovery_time.replace('Z', '+00:00'))
            hours_ahead = (disc_dt - pub_dt).total_seconds() / 3600
        except:
            hours_ahead = 0.0
            
        # 计算奖励
        score, reason = self.calc.compute_discovery_reward(
            news_importance_score, hours_ahead
        )
        
        if score > 0:
            # 保存奖励记录
            self._save_sentiment_reward(
                SentimentRewardRecord(
                    news_id=news_id,
                    news_title=news_title,
                    reward_type='discovery',
                    score=score,
                    reason=reason,
                    discovery_hours_ahead=hours_ahead,
                )
            )
            
            # 同时保存到基础奖惩表（作为扩展字段）
            self._save_to_base_reward_table(
                news_id=news_id,
                score=score,
                reason=reason,
                reward_category=SENTIMENT_REWARD_CATEGORY,
                discovery_hours_ahead=hours_ahead,
            )
            
            logger.info(f"✅ 舆情发现奖励: +{score:.0f}分 | {news_title[:30]}...")
            
        return score, reason
    
    def evaluate_sentiment_miss(
        self,
        news_id: int,
        news_title: str,
        news_importance_score: float,
        discovery_time: str,
        publish_time: str
    ) -> Tuple[float, str]:
        """
        评估舆情漏检（由SentimentAnalyzer调用）
        
        返回: (score, reason)  # score为负数
        """
        # 计算延迟发现小时数
        try:
            pub_dt = datetime.fromisoformat(publish_time.replace('Z', '+00:00'))
            disc_dt = datetime.fromisoformat(discovery_time.replace('Z', '+00:00'))
            hours_delay = (disc_dt - pub_dt).total_seconds() / 3600
            hours_delay = max(hours_delay, 0)  # 确保非负
        except:
            hours_delay = 0.0
            
        # 计算惩罚
        score, reason = self.calc.compute_miss_penalty(
            news_importance_score, hours_delay
        )
        
        if score < 0:
            # 保存惩罚记录
            self._save_sentiment_reward(
                SentimentRewardRecord(
                    news_id=news_id,
                    news_title=news_title,
                    reward_type='miss',
                    score=score,
                    reason=reason,
                    discovery_hours_ahead=-hours_delay,  # 负值表示延迟
                )
            )
            
            # 同时保存到基础奖惩表
            self._save_to_base_reward_table(
                news_id=news_id,
                score=score,
                reason=reason,
                reward_category=SENTIMENT_REWARD_CATEGORY,
            )
            
            logger.info(f"✅ 舆情漏检惩罚: {score:.0f}分 | {news_title[:30]}...")
            
        return score, reason
    
    def evaluate_interpretation(
        self,
        news_id: int,
        news_title: str,
        trading_profit_pct: float = 0.0,
        was_correct: bool = True,
        related_stock_code: str = '',
        related_stock_name: str = ''
    ) -> Tuple[float, str]:
        """
        评估舆情解读（由交易结果回调调用）
        
        返回: (score, reason)
        """
        if was_correct:
            score, reason = self.calc.compute_interpretation_reward(
                news_id, trading_profit_pct, was_correct
            )
            reward_type = 'correct_interpretation'
        else:
            score, reason = self.calc.compute_wrong_penalty(
                news_id, trading_profit_pct
            )
            reward_type = 'wrong_interpretation'
            
        # 保存记录
        self._save_sentiment_reward(
            SentimentRewardRecord(
                news_id=news_id,
                news_title=news_title,
                reward_type=reward_type,
                score=score,
                reason=reason,
                trading_outcome='success' if was_correct else 'failure',
                related_stock_code=related_stock_code,
                related_stock_name=related_stock_name,
            )
        )
        
        # 同时保存到基础奖惩表
        self._save_to_base_reward_table(
            news_id=news_id,
            score=score,
            reason=reason,
            reward_category=SENTIMENT_REWARD_CATEGORY,
            trading_outcome='success' if was_correct else 'failure',
        )
        
        logger.info(f"{'✅' if score > 0 else '⚠️'} 舆情解读{'奖励' if score > 0 else '惩罚'}: {score:+.0f}分 | {news_title[:30]}...")
        
        return score, reason
    
    def evaluate_fast_response(
        self,
        news_id: int,
        news_title: str,
        response_time: str,  # ISO格式时间
        discovery_time: str   # ISO格式时间
    ) -> Tuple[float, str]:
        """
        评估快速响应（由SentimentTrader调用）
        
        返回: (score, reason)
        """
        # 计算响应耗时
        try:
            disc_dt = datetime.fromisoformat(discovery_time.replace('Z', '+00:00'))
            resp_dt = datetime.fromisoformat(response_time.replace('Z', '+00:00'))
            response_hours = (resp_dt - disc_dt).total_seconds() / 3600
        except:
            response_hours = 1.0
            
        # 计算奖励
        score, reason = self.calc.compute_fast_response_reward(response_hours)
        
        if score > 0:
            # 保存记录
            self._save_sentiment_reward(
                SentimentRewardRecord(
                    news_id=news_id,
                    news_title=news_title,
                    reward_type='fast_response',
                    score=score,
                    reason=reason,
                    response_hours=response_hours,
                )
            )
            
            # 同时保存到基础奖惩表
            self._save_to_base_reward_table(
                news_id=news_id,
                score=score,
                reason=reason,
                reward_category=SENTIMENT_REWARD_CATEGORY,
            )
            
            logger.info(f"✅ 快速响应奖励: +{score:.0f}分 | 响应耗时{response_hours:.1f}小时")
            
        return score, reason
    
    def _save_sentiment_reward(self, record: SentimentRewardRecord):
        """保存舆情奖惩记录到 sentiment_db.db"""
        conn = sqlite3.connect(self.sentiment_db_path)
        cur = conn.cursor()
        
        try:
            cur.execute('''
                INSERT INTO sentiment_reward_records
                (news_id, news_title, reward_type, score, reason,
                 discovery_hours_ahead, response_hours, trading_outcome,
                 related_stock_code, related_stock_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                record.news_id,
                record.news_title,
                record.reward_type,
                record.score,
                record.reason,
                record.discovery_hours_ahead,
                record.response_hours,
                record.trading_outcome,
                record.related_stock_code,
                record.related_stock_name,
            ))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"❌ 保存舆情奖惩记录失败: {e}")
            conn.rollback()
            
        finally:
            conn.close()
            
    def _save_to_base_reward_table(
        self,
        news_id: int,
        score: float,
        reason: str,
        reward_category: str = SENTIMENT_REWARD_CATEGORY,
        discovery_hours_ahead: float = 0.0,
        trading_outcome: str = '',
        detail: str = ''
    ):
        """保存舆情奖惩记录到基础奖惩表（作为扩展记录）"""
        conn = sqlite3.connect(self.base_db_path)
        cur = conn.cursor()
        
        try:
            # 注意：这里保存到 reward_records 表，但 code/name 字段用特殊值
            cur.execute('''
                INSERT INTO reward_records
                (code, name, pick_date, t1_date, tier, score,
                 reason, reward_category, news_id, discovery_hours_ahead,
                 trading_outcome, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                f'NEWS_{news_id}',  # 特殊code
                f'{reason[:50]}',  # 特殊name
                datetime.now().strftime('%Y-%m-%d'),  # pick_date
                datetime.now().strftime('%Y-%m-%d'),  # t1_date
                f'SENTIMENT_{reward_category.upper()}',  # tier
                score,
                reason,
                reward_category,
                news_id,
                discovery_hours_ahead,
                trading_outcome,
                detail,
                datetime.now().isoformat(),
            ))
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"❌ 保存到基础奖惩表失败: {e}")
            conn.rollback()
            
        finally:
            conn.close()
            
    def get_sentiment_reward_stats(self, days: int = 7) -> Dict:
        """获取舆情奖惩统计"""
        conn = sqlite3.connect(self.sentiment_db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        # 按类型统计
        cur.execute('''
            SELECT 
                reward_type,
                COUNT(*) as count,
                SUM(score) as total_score,
                AVG(score) as avg_score
            FROM sentiment_reward_records
            WHERE created_time >= ?
            GROUP BY reward_type
        ''', (since,))
        
        by_type = {}
        for row in cur.fetchall():
            by_type[row['reward_type']] = dict(row)
            
        # 总计
        cur.execute('''
            SELECT 
                COUNT(*) as total_count,
                SUM(score) as net_score,
                SUM(CASE WHEN score > 0 THEN score ELSE 0 END) as total_rewards,
                SUM(CASE WHEN score < 0 THEN ABS(score) ELSE 0 END) as total_penalties
            FROM sentiment_reward_records
            WHERE created_time >= ?
        ''', (since,))
        
        total = cur.fetchone()
        
        conn.close()
        
        return {
            'period_days': days,
            'total': dict(total) if total else {},
            'by_type': by_type,
        }
    
    def generate_sentiment_reward_report(self, days: int = 7) -> str:
        """生成舆情奖惩报告"""
        stats = self.get_sentiment_reward_stats(days=days)
        
        lines = []
        lines.append("=" * 70)
        lines.append(f"  舆情奖惩报告 (过去 {days} 天)")
        lines.append("=" * 70)
        
        if stats['total']:
            t = stats['total']
            lines.append(f"  总记录数:  {t.get('total_count', 0)} 条")
            lines.append(f"  净得分:    {t.get('net_score', 0):+.1f} 分")
            lines.append(f"  总奖励:    +{t.get('total_rewards', 0):.1f} 分")
            lines.append(f"  总惩罚:    -{t.get('total_penalties', 0):.1f} 分")
        else:
            lines.append("  无舆情奖惩记录")
            
        if stats['by_type']:
            lines.append("")
            lines.append("  按类型:")
            type_names = {
                'discovery': '舆情发现',
                'miss': '舆情漏检',
                'correct_interpretation': '正确解读',
                'wrong_interpretation': '错误解读',
                'fast_response': '快速响应',
            }
            for typ, data in stats['by_type'].items():
                name = type_names.get(typ, typ)
                lines.append(f"    {name:10s}: {data['count']:3d}次  总分={data['total_score']:+.1f}  平均={data['avg_score']:+.1f}")
                
        lines.append("=" * 70)
        return '\n'.join(lines)

# ── 命令行接口 ────────────────────────────────────────────────
def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='V13.2 舆情奖惩扩展')
    parser.add_argument('--discover', action='store_true', help='模拟舆情发现')
    parser.add_argument('--miss', action='store_true', help='模拟舆情漏检')
    parser.add_argument('--interpret', type=float, default=5.0, help='模拟舆情解读 (交易盈利%%)')
    parser.add_argument('--wrong', action='store_true', help='模拟舆情误判')
    parser.add_argument('--respond', type=float, default=1.0, help='模拟快速响应 (响应小时数)')
    parser.add_argument('--report', action='store_true', help='生成舆情奖惩报告')
    parser.add_argument('--days', type=int, default=7, help='统计天数')
    parser.add_argument('--base-db', type=str, default=None, help='基础数据库路径')
    parser.add_argument('--sentiment-db', type=str, default=None, help='舆情数据库路径')
    
    args = parser.parse_args()
    
    # 初始化引擎
    engine = SentimentRewardEngine(
        base_db_path=args.base_db,
        sentiment_db_path=args.sentiment_db
    )
    
    if args.discover:
        # 模拟发现
        score, reason = engine.evaluate_sentiment_discovery(
            news_id=1,
            news_title='模拟重要舆情新闻',
            news_importance_score=85.0,
            discovery_time=datetime.now().isoformat(),
            publish_time=(datetime.now() - timedelta(hours=2)).isoformat(),
        )
        print(f"\n发现奖励: {score:+.0f}分")
        print(f"原因: {reason}\n")
        
    elif args.miss:
        # 模拟漏检
        score, reason = engine.evaluate_sentiment_miss(
            news_id=2,
            news_title='模拟漏检的重要舆情',
            news_importance_score=75.0,
            discovery_time=datetime.now().isoformat(),
            publish_time=(datetime.now() - timedelta(hours=5)).isoformat(),
        )
        print(f"\n漏检惩罚: {score:+.0f}分")
        print(f"原因: {reason}\n")
        
    elif args.interpret:
        # 模拟解读
        score, reason = engine.evaluate_interpretation(
            news_id=1,
            news_title='模拟重要舆情新闻',
            trading_profit_pct=args.interpret,
            was_correct=True,
        )
        print(f"\n解读奖励: {score:+.0f}分")
        print(f"原因: {reason}\n")
        
    elif args.wrong:
        # 模拟误判
        score, reason = engine.evaluate_interpretation(
            news_id=2,
            news_title='模拟漏检的重要舆情',
            trading_profit_pct=-3.0,
            was_correct=False,
        )
        print(f"\n误判惩罚: {score:+.0f}分")
        print(f"原因: {reason}\n")
        
    elif args.respond:
        # 模拟快速响应
        score, reason = engine.evaluate_fast_response(
            news_id=1,
            news_title='模拟重要舆情新闻',
            response_time=datetime.now().isoformat(),
            discovery_time=(datetime.now() - timedelta(hours=args.respond)).isoformat(),
        )
        print(f"\n快速响应奖励: {score:+.0f}分")
        print(f"原因: {reason}\n")
        
    elif args.report:
        # 生成报告
        print(engine.generate_sentiment_reward_report(days=args.days))
        
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
