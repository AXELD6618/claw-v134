#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 舆情交易决策器 (Sentiment Trader)
将舆情分析结果转化为交易信号，与圣杯系统深度融合

功能:
1. 舆情信号 → 交易信号 (BUY/SELL/HOLD)
2. 信号强度评估 (基于情感得分×影响范围×及时性)
3. 与V13.2圣杯评分系统融合 (舆情得分×20% + M46×40% + M57×40%)
4. 生成舆情驱动的交易建议
5. 回测舆情信号的有效性

信号生成逻辑:
- sentiment_score > 0.5 AND impact_score > 70 → STRONG_BUY
- sentiment_score > 0.3 AND impact_score > 50 → BUY
- sentiment_score > 0.0 AND impact_score > 30 → WATCH
- sentiment_score < -0.5 AND impact_score > 70 → STRONG_SELL
- sentiment_score < -0.3 AND impact_score > 50 → SELL
- 其他 → HOLD

融合评分:
  final_score = m46_confidence * 0.35 
                + m57_alpha * 0.35 
                + sentiment_score_normalized * 0.20 
                + data_quality * 0.10

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
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_trader.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'
HOLY_GRAIL_DB = 'data/holy_grail.db'

@dataclass
class SentimentSignal:
    """舆情交易信号"""
    news_id: int
    signal_type: str  # 'buy'/'sell'/'hold'
    signal_strength: float  # 0-1 信号强度
    confidence: float  # 0-1 置信度
    target_stocks: List[Dict]  # [{"code": "600519", "name": "贵州茅台", "weight": 0.8}]
    reasoning: str
    expected_impact_days: int
    sentiment_score: float
    impact_score: float
    importance_score: float
    event_type: str
    created_time: str = ''
    
    def __post_init__(self):
        if not self.created_time:
            self.created_time = datetime.now().isoformat()

@dataclass
class FusionScore:
    """融合评分结果"""
    stock_code: str
    stock_name: str
    m46_confidence: float
    m57_alpha: float
    sentiment_score: float
    sentiment_normalized: float  # 归一化到0-1
    data_quality: float
    final_score: float
    recommendation: str  # 'STRONG_BUY'/'BUY'/'WATCH'/'HOLD'/'REJECT'
    sentiment_signal: Optional[SentimentSignal] = None
    
# ── 数据库操作 ────────────────────────────────────────────────────
def load_sentiment_signals(db_path: str = DB_PATH, 
                           min_importance: float = 50.0,
                           limit: int = 20) -> List[Dict]:
    """加载活跃的舆情信号"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    since = (datetime.now() - timedelta(days=3)).isoformat()
    
    cur.execute('''
        SELECT s.*, p.title, p.sentiment_score, p.impact_score, 
               p.importance_score, p.event_type, p.related_stocks,
               p.related_sectors, r.publish_time, r.source
        FROM trading_signals s
        JOIN news_processed p ON p.id = s.news_id
        JOIN news_raw r ON r.id = p.raw_id
        WHERE s.status = 'active'
        AND p.importance_score >= ?
        AND r.publish_time >= ?
        ORDER BY p.importance_score DESC, p.sentiment_score DESC
        LIMIT ?
    ''', (min_importance, since, limit))
    
    rows = cur.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def save_trading_signal(signal: SentimentSignal, db_path: str = DB_PATH):
    """保存交易信号"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        cur.execute('''
            INSERT OR REPLACE INTO trading_signals
            (news_id, signal_type, signal_strength, confidence,
             target_stocks, reasoning, expected_impact_days)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            signal.news_id,
            signal.signal_type,
            signal.signal_strength,
            signal.confidence,
            json.dumps(signal.target_stocks, ensure_ascii=False),
            signal.reasoning,
            signal.expected_impact_days
        ))
        
        conn.commit()
        logger.info(f"✅ 已保存交易信号: {signal.signal_type} | 强度={signal.signal_strength:.2f}")
        
    except Exception as e:
        logger.error(f"❌ 保存失败: {e}")
        conn.rollback()
        
    finally:
        conn.close()

def update_signal_outcome(signal_id: int, outcome: str, db_path: str = DB_PATH):
    """更新信号实际结果 (供奖惩引擎调用)"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        cur.execute('''
            UPDATE trading_signals
            SET status = 'executed', actual_outcome = ?
            WHERE id = ?
        ''', (outcome, signal_id))
        
        conn.commit()
        logger.info(f"✅ 已更新信号结果: id={signal_id} outcome={outcome}")
        
    except Exception as e:
        logger.error(f"❌ 更新失败: {e}")
        conn.rollback()
        
    finally:
        conn.close()

# ── 信号生成器 ────────────────────────────────────────────────────
class SentimentSignalGenerator:
    """舆情信号生成器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info("✅ 舆情信号生成器初始化完成")
        
    def generate_signals(self, news_list: List[Dict]) -> List[SentimentSignal]:
        """
        从处理后的新闻生成交易信号
        
        返回: SentimentSignal列表
        """
        signals = []
        
        for news in news_list:
            try:
                signal = self._generate_signal_for_news(news)
                if signal:
                    signals.append(signal)
                    save_trading_signal(signal, self.db_path)
            except Exception as e:
                logger.error(f"❌ 信号生成失败: {news.get('title', '')} | 错误: {e}")
                
        logger.info(f"✅ 信号生成完成: 共{len(signals)}个信号")
        return signals
    
    def _generate_signal_for_news(self, news: Dict) -> Optional[SentimentSignal]:
        """为单条新闻生成交易信号"""
        sentiment_score = news.get('sentiment_score', 0.0)
        impact_score = news.get('impact_score', 0.0)
        importance_score = news.get('importance_score', 0.0)
        event_type = news.get('event_type', 'unknown')
        
        # 1. 判断信号类型
        if sentiment_score > 0.5 and impact_score > 70:
            signal_type = 'buy'
            signal_strength = min((sentiment_score + impact_score/100) / 2, 1.0)
        elif sentiment_score > 0.3 and impact_score > 50:
            signal_type = 'buy'
            signal_strength = min((sentiment_score + impact_score/100) / 2, 0.8)
        elif sentiment_score > 0.0 and impact_score > 30:
            signal_type = 'hold'
            signal_strength = 0.3
        elif sentiment_score < -0.5 and impact_score > 70:
            signal_type = 'sell'
            signal_strength = min((abs(sentiment_score) + impact_score/100) / 2, 1.0)
        elif sentiment_score < -0.3 and impact_score > 50:
            signal_type = 'sell'
            signal_strength = min((abs(sentiment_score) + impact_score/100) / 2, 0.8)
        else:
            signal_type = 'hold'
            signal_strength = 0.1
            
        # 2. 计算置信度
        confidence = (
            abs(sentiment_score) * 0.4 +
            (importance_score / 100) * 0.4 +
            min(impact_score / 100, 1.0) * 0.2
        )
        confidence = min(confidence, 1.0)
        
        # 3. 识别目标股票
        target_stocks = self._identify_target_stocks(news)
        
        # 4. 预期影响天数
        expected_days = self._estimate_impact_duration(news)
        
        # 5. 推理原因
        reasoning = self._generate_reasoning(news, signal_type, signal_strength)
        
        return SentimentSignal(
            news_id=news['id'],
            signal_type=signal_type,
            signal_strength=signal_strength,
            confidence=confidence,
            target_stocks=target_stocks,
            reasoning=reasoning,
            expected_impact_days=expected_days,
            sentiment_score=sentiment_score,
            impact_score=impact_score,
            importance_score=importance_score,
            event_type=event_type,
        )
    
    def _identify_target_stocks(self, news: Dict) -> List[Dict]:
        """识别目标股票"""
        # 方法1: 从数据库读取
        related_stocks_str = news.get('related_stocks', '[]')
        try:
            related_stocks = json.loads(related_stocks_str)
            if related_stocks:
                return related_stocks
        except:
            pass
        
        # 方法2: 从板块推断
        related_sectors_str = news.get('related_sectors', '[]')
        try:
            related_sectors = json.loads(related_sectors_str)
            # TODO: 根据板块查询代表性股票
            return []
        except:
            return []
    
    def _estimate_impact_duration(self, news: Dict) -> int:
        """估计影响持续时间"""
        event_type = news.get('event_type', 'unknown')
        
        duration_map = {
            'geopolitical': 30,  # 地缘政治影响持久
            'policy': 14,  # 政策影响2周+
            'earnings': 3,  # 财报影响3天左右
            'm&a': 7,  # 并购影响1周+
            'industry': 14,  # 行业动态影响2周+
            'macro': 7,  # 宏观数据影响1周+
            'company': 5,  # 公司公告影响5天左右
        }
        
        return duration_map.get(event_type, 3)
    
    def _generate_reasoning(self, news: Dict, signal_type: str, strength: float) -> str:
        """生成推理原因"""
        title = news.get('title', '')
        sentiment_score = news.get('sentiment_score', 0.0)
        impact_score = news.get('impact_score', 0.0)
        event_type = news.get('event_type', 'unknown')
        
        if signal_type == 'buy':
            return (
                f"舆情利好: {title[:30]}... | "
                f"情感得分={sentiment_score:+.2f} | "
                f"影响程度={impact_score:.0f}分 | "
                f"事件类型={event_type} | "
                f"信号强度={strength:.2f}"
            )
        elif signal_type == 'sell':
            return (
                f"舆情利空: {title[:30]}... | "
                f"情感得分={sentiment_score:+.2f} | "
                f"影响程度={impact_score:.0f}分 | "
                f"事件类型={event_type} | "
                f"信号强度={strength:.2f}"
            )
        else:
            return f"舆情观察: {title[:30]}... | 情感得分={sentiment_score:+.2f}"
    

# ── 评分融合器 ────────────────────────────────────────────────────
class ScoreFusionEngine:
    """评分融合引擎：将舆情得分与圣杯评分融合"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info("✅ 评分融合引擎初始化完成")
        
    def compute_fusion_score(self, 
                             stock_code: str,
                             stock_name: str,
                             m46_confidence: float,
                             m57_alpha: float,
                             sentiment_score: float,
                             data_quality: float = 0.8) -> FusionScore:
        """
        计算融合评分
        
        公式:
          final_score = m46_confidence * 0.35 
                        + m57_alpha * 0.35 
                        + sentiment_normalized * 0.20 
                        + data_quality * 0.10
        
        返回: FusionScore对象
        """
        # 1. 归一化舆情得分 (从-1~+1 映射到 0~1)
        sentiment_normalized = (sentiment_score + 1.0) / 2.0
        sentiment_normalized = max(0.0, min(1.0, sentiment_normalized))
        
        # 2. 计算融合评分
        final_score = (
            m46_confidence * 0.35 +
            m57_alpha * 0.35 +
            sentiment_normalized * 0.20 +
            data_quality * 0.10
        )
        final_score = max(0.0, min(1.0, final_score))
        
        # 3. 生成推荐
        recommendation = self._make_recommendation(final_score)
        
        logger.info(
            f"🎯 融合评分: {stock_name}({stock_code}) | "
            f"M46={m46_confidence:.3f} M57={m57_alpha:.3f} "
            f"情绪={sentiment_score:+.2f}(归一化={sentiment_normalized:.3f}) | "
            f"最终={final_score:.3f} → {recommendation}"
        )
        
        return FusionScore(
            stock_code=stock_code,
            stock_name=stock_name,
            m46_confidence=m46_confidence,
            m57_alpha=m57_alpha,
            sentiment_score=sentiment_score,
            sentiment_normalized=sentiment_normalized,
            data_quality=data_quality,
            final_score=final_score,
            recommendation=recommendation,
        )
    
    def _make_recommendation(self, final_score: float) -> str:
        """根据融合评分生成推荐"""
        if final_score >= 0.75:
            return 'STRONG_BUY'
        elif final_score >= 0.60:
            return 'BUY'
        elif final_score >= 0.45:
            return 'WATCH'
        elif final_score >= 0.30:
            return 'HOLD'
        else:
            return 'REJECT'
    
    def fuse_batch(self, 
                   stocks_data: List[Dict]) -> List[FusionScore]:
        """
        批量融合评分
        
        stocks_data: [
            {
                'code': '600519',
                'name': '贵州茅台',
                'm46_confidence': 0.75,
                'm57_alpha': 0.68,
                'sentiment_score': 0.35,  # 可选，若无则=0
                'data_quality': 0.85,
            },
            ...
        ]
        """
        results = []
        
        for stock in stocks_data:
            fusion = self.compute_fusion_score(
                stock_code=stock['code'],
                stock_name=stock['name'],
                m46_confidence=stock.get('m46_confidence', 0.5),
                m57_alpha=stock.get('m57_alpha', 0.5),
                sentiment_score=stock.get('sentiment_score', 0.0),
                data_quality=stock.get('data_quality', 0.8)
            )
            results.append(fusion)
            
        logger.info(f"✅ 批量融合完成: {len(results)}只股票")
        return results

# ── 回测器 ────────────────────────────────────────────────────
class SentimentBacktester:
    """舆情信号回测器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info("✅ 舆情回测器初始化完成")
        
    def backtest_signals(self, days: int = 30) -> Dict:
        """回测历史信号"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        # 获取有结果的信号
        cur.execute('''
            SELECT s.*, p.title, p.sentiment_score, p.impact_score
            FROM trading_signals s
            JOIN news_processed p ON p.id = s.news_id
            WHERE s.status = 'executed'
            AND s.actual_outcome IS NOT NULL
            AND s.created_time >= ?
        ''', (since,))
        
        signals = cur.fetchall()
        conn.close()
        
        if not signals:
            return {'total': 0, 'message': '没有已执行的信号可回测'}
        
        # 统计
        total = len(signals)
        success = sum(1 for s in signals if s['actual_outcome'] == 'success')
        failure = sum(1 for s in signals if s['actual_outcome'] == 'failure')
        
        hit_rate = success / total if total > 0 else 0.0
        
        return {
            'total_signals': total,
            'success': success,
            'failure': failure,
            'hit_rate': hit_rate,
            'period_days': days,
        }

# ── 主决策器类 ────────────────────────────────────────────────────
class SentimentTrader:
    """舆情交易决策器（主类）"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        
        # 初始化子模块
        self.signal_generator = SentimentSignalGenerator(db_path)
        self.fusion_engine = ScoreFusionEngine(db_path)
        self.backtester = SentimentBacktester(db_path)
        
        logger.info(f"✅ 舆情交易决策器初始化完成")
        
    def process_latest_news(self, limit: int = 20) -> List[SentimentSignal]:
        """处理最新新闻并生成交易信号"""
        # 1. 加载未处理的处理后新闻
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(hours=24)).isoformat()
        
        cur.execute('''
            SELECT p.*, r.title, r.publish_time, r.source
            FROM news_processed p
            JOIN news_raw r ON r.id = p.raw_id
            LEFT JOIN trading_signals s ON s.news_id = p.id
            WHERE s.id IS NULL  -- 尚未生成信号
            AND p.importance_score >= 50  -- 只看重要新闻
            AND r.publish_time >= ?
            ORDER BY p.importance_score DESC
            LIMIT ?
        ''', (since, limit))
        
        news_list = [dict(row) for row in cur.fetchall()]
        conn.close()
        
        if not news_list:
            logger.info("没有未处理的重要新闻")
            return []
        
        logger.info(f"📊 开始处理 {len(news_list)} 条重要新闻...")
        
        # 2. 生成信号
        signals = self.signal_generator.generate_signals(news_list)
        
        return signals
    
    def analyze_stock_with_sentiment(self, 
                                    stock_code: str,
                                    stock_name: str,
                                    m46_confidence: float,
                                    m57_alpha: float,
                                    data_quality: float = 0.8) -> FusionScore:
        """结合舆情分析单只股票"""
        # 1. 获取该股票相关的最新舆情
        sentiment_score = self._get_latest_sentiment_for_stock(stock_code)
        
        # 2. 计算融合评分
        fusion = self.fusion_engine.compute_fusion_score(
            stock_code=stock_code,
            stock_name=stock_name,
            m46_confidence=m46_confidence,
            m57_alpha=m57_alpha,
            sentiment_score=sentiment_score,
            data_quality=data_quality,
        )
        
        return fusion
    
    def _get_latest_sentiment_for_stock(self, stock_code: str) -> float:
        """获取股票的最新舆情得分"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(hours=24)).isoformat()
        
        # 查找与该股票相关的新闻
        cur.execute('''
            SELECT AVG(p.sentiment_score) as avg_sentiment
            FROM news_processed p
            JOIN news_raw r ON r.id = p.raw_id
            WHERE p.related_stocks LIKE ?
            AND r.publish_time >= ?
        ''', (f'%{stock_code}%', since))
        
        row = cur.fetchone()
        conn.close()
        
        if row and row['avg_sentiment'] is not None:
            return row['avg_sentiment']
        
        return 0.0  # 无舆情
        
    def backtest(self, days: int = 30) -> Dict:
        """回测"""
        return self.backtester.backtest_signals(days=days)
    
    def generate_trading_advice(self, 
                               min_strength: float = 0.5,
                               limit: int = 10) -> List[Dict]:
        """生成交易建议"""
        # 1. 加载活跃信号
        signals_data = load_sentiment_signals(
            db_path=self.db_path,
            min_importance=50.0,
            limit=limit
        )
        
        if not signals_data:
            return []
        
        # 2. 过滤+排序
        advice_list = []
        for s in signals_data:
            if s['signal_strength'] < min_strength:
                continue
                
            advice = {
                'signal_id': s['id'],
                'news_title': s['title'],
                'signal_type': s['signal_type'],
                'signal_strength': s['signal_strength'],
                'confidence': s['confidence'],
                'target_stocks': json.loads(s.get('target_stocks', '[]')),
                'reasoning': s['reasoning'],
                'expected_days': s['expected_impact_days'],
                'sentiment_score': s['sentiment_score'],
                'importance_score': s['importance_score'],
            }
            advice_list.append(advice)
            
        # 按强度排序
        advice_list.sort(key=lambda x: x['signal_strength'], reverse=True)
        
        return advice_list

# ── 命令行接口 ────────────────────────────────────────────────────
def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='V13.2 舆情交易决策器')
    parser.add_argument('--process-latest', action='store_true', help='处理最新新闻并生成信号')
    parser.add_argument('--limit', type=int, default=20, help='处理数量限制')
    parser.add_argument('--analyze-stock', type=str, help='分析单只股票 (格式: code,name)')
    parser.add_argument('--m46', type=float, default=0.5, help='M46置信度')
    parser.add_argument('--m57', type=float, default=0.5, help='M57 Alpha得分')
    parser.add_argument('--backtest', action='store_true', help='回测舆情信号')
    parser.add_argument('--days', type=int, default=30, help='回测天数')
    parser.add_argument('--advice', action='store_true', help='生成交易建议')
    parser.add_argument('--db-path', type=str, default=DB_PATH, help='数据库路径')
    
    args = parser.parse_args()
    
    # 初始化决策器
    trader = SentimentTrader(db_path=args.db_path)
    
    if args.process_latest:
        # 处理最新新闻
        signals = trader.process_latest_news(limit=args.limit)
        
        print(f"\n{'=' * 70}")
        print(f"  舆情信号生成报告")
        print(f"{'=' * 70}")
        print(f"  处理数量: {len(signals)} 个信号")
        
        if signals:
            print(f"\n  信号列表:")
            for i, sig in enumerate(signals[:10], 1):
                print(f"    {i}. [{sig.signal_type.upper()}] {sig.reasoning[:60]}...")
                print(f"       强度={sig.signal_strength:.2f} 置信度={sig.confidence:.2f} 预期天数={sig.expected_impact_days}")
                
        print(f"\n{'=' * 70}\n")
        
    elif args.analyze_stock:
        # 分析单只股票
        parts = args.analyze_stock.split(',')
        if len(parts) != 2:
            print("错误: 格式应为 code,name")
            sys.exit(1)
            
        code, name = parts[0].strip(), parts[1].strip()
        
        fusion = trader.analyze_stock_with_sentiment(
            stock_code=code,
            stock_name=name,
            m46_confidence=args.m46,
            m57_alpha=args.m57,
        )
        
        print(f"\n{'=' * 70}")
        print(f"  股票舆情融合分析: {name}({code})")
        print(f"{'=' * 70}")
        print(f"  M46贝叶斯置信度: {fusion.m46_confidence:.3f}")
        print(f"  M57隔夜Alpha:    {fusion.m57_alpha:.3f}")
        print(f"  舆情得分:          {fusion.sentiment_score:+.2f} (归一化={fusion.sentiment_normalized:.3f})")
        print(f"  数据质量:          {fusion.data_quality:.2f}")
        print(f"  {'─' * 50}")
        print(f"  融合评分:          {fusion.final_score:.3f}")
        print(f"  推荐:              {fusion.recommendation}")
        print(f"{'=' * 70}\n")
        
    elif args.backtest:
        # 回测
        result = trader.backtest(days=args.days)
        
        print(f"\n{'=' * 70}")
        print(f"  舆情信号回测报告 (过去{args.days}天)")
        print(f"{'=' * 70}")
        
        if result.get('total', 0) == 0:
            print(f"  没有已执行的信号可回测")
        else:
            print(f"  总信号数: {result['total_signals']}")
            print(f"  成功:     {result['success']}")
            print(f"  失败:     {result['failure']}")
            print(f"  命中率:   {result['hit_rate']*100:.1f}%")
            
        print(f"\n{'=' * 70}\n")
        
    elif args.advice:
        # 生成交易建议
        advice = trader.generate_trading_advice()
        
        print(f"\n{'=' * 70}")
        print(f"  舆情驱动交易建议")
        print(f"{'=' * 70}")
        
        if not advice:
            print(f"  没有活跃的交易信号")
        else:
            for i, adv in enumerate(advice, 1):
                print(f"\n{i}. [{adv['signal_type'].upper()}] 强度={adv['signal_strength']:.2f} 置信度={adv['confidence']:.2f}")
                print(f"   新闻: {adv['news_title'][:50]}...")
                print(f"   推理: {adv['reasoning'][:80]}")
                if adv['target_stocks']:
                    stocks = ', '.join([f"{s['name']}({s['code']})" for s in adv['target_stocks'][:3]])
                    print(f"   目标股票: {stocks}")
                print(f"   预期影响: {adv['expected_days']}天")
                
        print(f"\n{'=' * 70}\n")
        
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
