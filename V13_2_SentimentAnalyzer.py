#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 舆情分析器 (Sentiment Analyzer)
对采集的舆情进行智能分析，生成结构化 output

功能:
1. 情感分析 (正面/负面/中性，使用LLM)
2. 影响评估 (对相关板块/个股的影响程度)
3. 事件分类 (政策/财报/并购/地缘政治/行业动态等)
4. 热度分析 (舆情传播趋势)
5. 去重与聚合 (同一事件的多源报道合并)
6. 重要性评分 (0-100分)

输入: sentiments_db.db 中的 news_raw 表
输出: sentiments_db.db 中的 news_processed 表 + 结构化JSON报告

情感分析策略:
- 使用LLM对标题+正文进行情感打分 (-1.0 ~ +1.0)
- 关键词加权: 涨/跌/利好/利空 等
- 板块关联: 识别受影响的板块和个股

事件分类:
- policy: 政策类 (降准、降息、产业政策支持)
- earnings: 财报类 (业绩预增、预减、超预期)
- m_a: 并购重组类 (并购、重组、借壳、注入)
- geopolitical: 地缘政治类 (国际冲突、贸易战、资源封锁)
- industry: 行业动态类 (技术突破、产能变化、供需失衡)
- macro: 宏观经济类 (GDP、CPI、PMI、利率)
- company: 公司公告类 (股权激励、增持减持、重大合同)

影响评估:
- 影响范围: 全市场 / 板块 / 个股
- 影响方向: 利好 / 利空 / 中性
- 影响程度: 0-100分
- 持续时间: 短期(1-3天) / 中期(1-2周) / 长期(1月+)

版本: V13.2
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import json
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_analyzer.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'

# 情感关键词
SENTIMENT_KEYWORDS = {
    'positive': [
        '涨', '利好', '突破', '增长', '超预期', '预增', '扭亏',
        '上涨', '走强', '领跑', '龙头', '爆发', '机会',
        '宽松', '降准', '降息', '支持', '扶持', '改革',
    ],
    'negative': [
        '跌', '利空', '下滑', '亏损', '暴雷', '退市', '预警',
        '下跌', '走弱', '承压', '风险', '泡沫', '破裂',
        '收紧', '加税', '制裁', '封锁', '冲突', '战争',
    ],
}

# 板块关键词映射
SECTOR_KEYWORDS = {
    '航运': ['航运', '船舶', '海运', '港口', '霍尔木兹', '海峡'],
    '石油': ['石油', '原油', '油气', '开采', '炼油', '石化'],
    '新能源': ['新能源', '光伏', '风电', '储能', '锂电池', '氢能'],
    'AI算力': ['AI', '算力', '芯片', 'GPU', '服务器', '数据中心'],
    '机器人': ['机器人', '人形', '减速器', '伺服', '控制器'],
    '军工': ['军工', '国防', '导弹', '战机', '航母', '卫星'],
    '消费': ['消费', '白酒', '家电', '汽车', '零售', '旅游'],
    '医药': ['医药', '生物', '疫苗', '创新药', '医疗器械'],
    '金融': ['金融', '银行', '保险', '证券', '信托'],
    '地产': ['地产', '房地产', '楼市', '房价', '建筑'],
}

# 事件类型关键词
EVENT_TYPE_KEYWORDS = {
    'policy': ['央行', '降准', '降息', '财政政策', '产业政策', '支持', '扶持'],
    'earnings': ['业绩', '预增', '预减', '扭亏', '超预期', '财报', '季报'],
    'm&a': ['并购', '重组', '借壳', '注入', '股权转让', '要约收购'],
    'geopolitical': ['伊朗', '美国', '中国', '贸易战', '制裁', '封锁', '冲突', '战争', '海峡'],
    'industry': ['技术突破', '产能', '供需', '价格', '涨价', '跌价'],
    'macro': ['GDP', 'CPI', 'PPI', 'PMI', '利率', '汇率', '通胀'],
    'company': ['公告', '股权激励', '增持', '减持', '重大合同', '中标'],
}

# ── 数据库操作 ─────────────────────────────────────────────────
def get_unprocessed_news(db_path: str = DB_PATH, limit: int = 50) -> List[Dict]:
    """获取未处理的新闻"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('''
        SELECT r.* 
        FROM news_raw r
        LEFT JOIN news_processed p ON p.raw_id = r.id
        WHERE p.id IS NULL
        ORDER BY r.crawl_time DESC
        LIMIT ?
    ''', (limit,))
    
    rows = cur.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]

def save_processed_news(news: Dict, db_path: str = DB_PATH):
    """保存处理后的新闻"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        cur.execute('''
            INSERT OR REPLACE INTO news_processed
            (raw_id, title, summary, entities, sentiment_score, sentiment_label,
             impact_score, importance_score, event_type, related_stocks,
             related_sectors)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            news['raw_id'],
            news['title'],
            news.get('summary', ''),
            json.dumps(news.get('entities', {}), ensure_ascii=False),
            news.get('sentiment_score', 0.0),
            news.get('sentiment_label', 'neutral'),
            news.get('impact_score', 0.0),
            news.get('importance_score', 0.0),
            news.get('event_type', 'unknown'),
            json.dumps(news.get('related_stocks', []), ensure_ascii=False),
            json.dumps(news.get('related_sectors', []), ensure_ascii=False),
        ))
        
        conn.commit()
        logger.info(f"✅ 已保存处理后新闻: {news['title'][:30]}...")
        
    except Exception as e:
        logger.error(f"❌ 保存失败: {e}")
        conn.rollback()
        
    finally:
        conn.close()

# ── 情感分析 ─────────────────────────────────────────────────────
def analyze_sentiment(title: str, content: str = '') -> Tuple[float, str]:
    """
    情感分析
    
    返回: (sentiment_score, sentiment_label)
    - sentiment_score: -1.0 (极度负面) ~ +1.0 (极度正面)
    - sentiment_label: 'positive' / 'negative' / 'neutral'
    """
    text = (title or '') + ' ' + (content or '')[:500]  # 限制长度
    
    # 1. 关键词匹配 (简单版本)
    pos_count = sum(1 for kw in SENTIMENT_KEYWORDS['positive'] if kw in text)
    neg_count = sum(1 for kw in SENTIMENT_KEYWORDS['negative'] if kw in text)
    
    # 2. 计算得分
    total = pos_count + neg_count
    if total == 0:
        score = 0.0
    else:
        score = (pos_count - neg_count) / total
    
    # 3. 标签
    if score > 0.3:
        label = 'positive'
    elif score < -0.3:
        label = 'negative'
    else:
        label = 'neutral'
    
    # 4. 增强：使用LLM (如果可用)
    # TODO: 集成LLM进行更精确的情感分析
    
    logger.debug(f"情感分析: score={score:.2f} label={label} 文本={text[:50]}...")
    
    return score, label

# ── 影响评估 ─────────────────────────────────────────────────────
def assess_impact(title: str, content: str = '', sentiment_score: float = 0.0) -> Tuple[float, str, str, int]:
    """
    影响评估
    
    返回: (impact_score, impact_direction, impact_scope, duration_days)
    - impact_score: 0-100 影响程度
    - impact_direction: 'bullish' / 'bearish' / 'neutral'
    - impact_scope: 'market' / 'sector' / 'stock'
    - duration_days: 预计影响天数
    """
    text = (title or '') + ' ' + (content or '')[:500]
    
    # 1. 影响方向
    if sentiment_score > 0.2:
        direction = 'bullish'
    elif sentiment_score < -0.2:
        direction = 'bearish'
    else:
        direction = 'neutral'
    
    # 2. 影响范围
    # 检查是否提到具体股票代码
    import re
    stock_codes = re.findall(r'[0-9]{6}', text)
    
    if stock_codes:
        scope = 'stock'
    elif any(kw in text for kw in ['板块', '行业', '产业']):
        scope = 'sector'
    else:
        scope = 'market'
    
    # 3. 影响程度 (0-100)
    score = 0.0
    
    # 基础分：情感强度
    score += abs(sentiment_score) * 30
    
    # 关键词加分
    high_impact_kws = ['央行', '降准', '降息', '战', '制裁', '封锁', '突破', '爆发']
    for kw in high_impact_kws:
        if kw in text:
            score += 10
            
    # 限制最大值
    score = min(score, 100.0)
    
    # 4. 持续时间
    if any(kw in text for kw in ['长期', '战略', '规划']):
        duration = 30  # 1月+
    elif any(kw in text for kw in ['中期', '趋势']):
        duration = 14  # 1-2周
    else:
        duration = 3   # 1-3天
    
    logger.debug(f"影响评估: score={score:.1f} direction={direction} scope={scope} duration={duration}天")
    
    return score, direction, scope, duration

# ── 事件分类 ─────────────────────────────────────────────────────
def classify_event(title: str, content: str = '') -> str:
    """
    事件分类
    
    返回: 事件类型字符串
    - 'policy' / 'earnings' / 'm&a' / 'geopolitical' / 'industry' / 'macro' / 'company' / 'unknown'
    """
    text = (title or '') + ' ' + (content or '')[:500]
    
    # 计算每个类型的匹配分数
    scores = {}
    for event_type, keywords in EVENT_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > 0:
            scores[event_type] = score
    
    if not scores:
        return 'unknown'
    
    # 返回分数最高的类型
    return max(scores, key=scores.get)

# ── 板块识别 ─────────────────────────────────────────────────────
def identify_sectors(title: str, content: str = '') -> List[str]:
    """
    识别受影响的板块
    
    返回: 板块名称列表
    """
    text = (title or '') + ' ' + (content or '')[:500]
    
    matched_sectors = []
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matched_sectors.append(sector)
    
    return matched_sectors

# ── 重要性评分 ────────────────────────────────────────────────────
def compute_importance_score(news: Dict) -> float:
    """
    计算重要性评分 (0-100)
    
    考虑因素:
    - 情感强度 (30%)
    - 影响程度 (30%)
    - 事件类型 (20%)
    - 来源可信度 (10%)
    - 时效性 (10%)
    """
    sentiment_score = abs(news.get('sentiment_score', 0.0))
    impact_score = news.get('impact_score', 0.0) / 100.0
    event_type = news.get('event_type', 'unknown')
    source = news.get('source', '')
    
    # 事件类型权重
    event_weights = {
        'geopolitical': 1.0,  # 地缘政治最重要
        'policy': 0.9,
        'earnings': 0.8,
        'm&a': 0.8,
        'macro': 0.7,
        'industry': 0.6,
        'company': 0.5,
        'unknown': 0.3,
    }
    event_weight = event_weights.get(event_type, 0.3)
    
    # 来源可信度
    trusted_sources = ['xinhua', 'people', 'cctv', 'cailian', 'eastmoney']
    source_trust = 1.0 if any(s in source.lower() for s in trusted_sources) else 0.5
    
    # 时效性 (发布时间在24小时内?)
    publish_time = news.get('publish_time', '')
    try:
        if publish_time:
            pub_dt = datetime.fromisoformat(publish_time.replace('Z', '+00:00'))
            hours_ago = (datetime.now() - pub_dt).total_seconds() / 3600
            if hours_ago <= 24:
                timeliness = 1.0
            elif hours_ago <= 72:
                timeliness = 0.5
            else:
                timeliness = 0.1
        else:
            timeliness = 0.5
    except:
        timeliness = 0.5
    
    # 综合评分
    score = (
        sentiment_score * 0.3 +
        impact_score * 0.3 +
        event_weight * 0.2 +
        source_trust * 0.1 +
        timeliness * 0.1
    ) * 100
    
    return min(score, 100.0)

# ── 去重与聚合 ───────────────────────────────────────────────────
def deduplicate_and_merge(news_list: List[Dict], similarity_threshold: float = 0.8) -> List[Dict]:
    """
    去重与聚合：将同一事件的多篇报道合并
    
    返回: 聚合后的新闻列表
    """
    # TODO: 实现基于标题相似度的去重
    # 简单版本：相同source_id的合并
    
    merged = {}
    for news in news_list:
        key = news.get('title', '')[:30]  # 前30字符作为key
        
        if key in merged:
            # 合并
            merged[key]['sources'].append(news.get('source', ''))
            # 保留情感得分更高的
            if abs(news.get('sentiment_score', 0)) > abs(merged[key].get('sentiment_score', 0)):
                merged[key]['sentiment_score'] = news.get('sentiment_score', 0)
        else:
            news['sources'] = [news.get('source', '')]
            merged[key] = news
    
    return list(merged.values())

# ── 主分析器类 ──────────────────────────────────────────────────
class SentimentAnalyzer:
    """舆情分析器（主类）"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info(f"✅ 舆情分析器初始化完成: {db_path}")
        
    def analyze_one(self, raw_news: Dict) -> Dict:
        """
        分析单条新闻
        
        返回: 处理后的新闻字典
        """
        title = raw_news.get('title', '')
        content = raw_news.get('content', '')
        
        # 1. 情感分析
        sentiment_score, sentiment_label = analyze_sentiment(title, content)
        
        # 2. 影响评估
        impact_score, impact_direction, impact_scope, duration_days = assess_impact(
            title, content, sentiment_score
        )
        
        # 3. 事件分类
        event_type = classify_event(title, content)
        
        # 4. 板块识别
        related_sectors = identify_sectors(title, content)
        
        # 5. 重要性评分
        processed = {
            'raw_id': raw_news['id'],
            'title': title,
            'summary': content[:200] if content else '',  # 简单摘要
            'entities': {
                'stocks': [],  # TODO: 识别股票代码
                'sectors': related_sectors,
                'concepts': [],
            },
            'sentiment_score': sentiment_score,
            'sentiment_label': sentiment_label,
            'impact_score': impact_score,
            'importance_score': 0.0,  # 稍后计算
            'event_type': event_type,
            'related_stocks': [],  # TODO: 识别相关股票
            'related_sectors': related_sectors,
        }
        
        # 6. 计算重要性评分
        processed['importance_score'] = compute_importance_score(processed)
        
        logger.info(
            f"📊 分析完成: {title[:40]}... | "
            f"情感={sentiment_score:+.2f}({sentiment_label}) | "
            f"影响={impact_score:.0f}分({impact_direction}) | "
            f"类型={event_type} | "
            f"重要性={processed['importance_score']:.0f}分"
        )
        
        return processed
    
    def analyze_batch(self, limit: int = 50) -> int:
        """
        批量分析未处理的新闻
        
        返回: 处理数量
        """
        # 1. 获取未处理的新闻
        raw_news_list = get_unprocessed_news(self.db_path, limit=limit)
        
        if not raw_news_list:
            logger.info("没有未处理的新闻")
            return 0
        
        logger.info(f"📊 开始批量分析 {len(raw_news_list)} 条新闻...")
        
        # 2. 逐条分析
        processed_count = 0
        for raw_news in raw_news_list:
            try:
                processed = self.analyze_one(raw_news)
                save_processed_news(processed, self.db_path)
                processed_count += 1
            except Exception as e:
                logger.error(f"❌ 分析失败: {raw_news.get('title', '')} | 错误: {e}")
        
        logger.info(f"✅ 批量分析完成: 处理={processed_count}/{len(raw_news_list)}")
        
        return processed_count
    
    def get_top_news(self, days: int = 1, min_importance: float = 50.0, 
                     limit: int = 20) -> List[Dict]:
        """获取最重要的新闻"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        cur.execute('''
            SELECT p.*, r.source, r.publish_time, r.url
            FROM news_processed p
            JOIN news_raw r ON r.id = p.raw_id
            WHERE p.importance_score >= ?
            AND r.publish_time >= ?
            ORDER BY p.importance_score DESC, r.publish_time DESC
            LIMIT ?
        ''', (min_importance, since, limit))
        
        rows = cur.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    def generate_report(self, days: int = 1) -> Dict:
        """生成舆情分析报告"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        # 统计
        cur.execute('''
            SELECT 
                COUNT(*) as total,
                AVG(sentiment_score) as avg_sentiment,
                SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END) as positive_count,
                SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END) as negative_count,
                SUM(CASE WHEN sentiment_label = 'neutral' THEN 1 ELSE 0 END) as neutral_count,
                AVG(impact_score) as avg_impact,
                AVG(importance_score) as avg_importance
            FROM news_processed p
            JOIN news_raw r ON r.id = p.raw_id
            WHERE r.publish_time >= ?
        ''', (since,))
        
        stats = dict(cur.fetchone())
        
        # 按事件类型统计
        cur.execute('''
            SELECT event_type, COUNT(*) as count
            FROM news_processed p
            JOIN news_raw r ON r.id = p.raw_id
            WHERE r.publish_time >= ?
            GROUP BY event_type
            ORDER BY count DESC
        ''', (since,))
        
        by_event_type = {row['event_type']: row['count'] for row in cur.fetchall()}
        
        # 按板块统计
        # TODO: 解析JSON字段并统计
        
        conn.close()
        
        return {
            'period_days': days,
            'generated_at': datetime.now().isoformat(),
            'stats': stats,
            'by_event_type': by_event_type,
            'top_news': self.get_top_news(days=days, limit=10),
        }

# ── 命令行接口 ──────────────────────────────────────────────────
def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='V13.2 舆情分析器')
    parser.add_argument('--analyze-batch', type=int, default=50, help='批量分析未处理的新闻')
    parser.add_argument('--top-news', type=int, default=20, help='显示最重要的新闻')
    parser.add_argument('--min-importance', type=float, default=50.0, help='最低重要性分数')
    parser.add_argument('--days', type=int, default=1, help='分析过去N天的新闻')
    parser.add_argument('--report', action='store_true', help='生成舆情分析报告')
    parser.add_argument('--db-path', type=str, default=DB_PATH, help='数据库路径')
    
    args = parser.parse_args()
    
    # 初始化分析器
    analyzer = SentimentAnalyzer(db_path=args.db_path)
    
    if args.analyze_batch:
        # 批量分析
        count = analyzer.analyze_batch(limit=args.analyze_batch)
        print(f"\n{'=' * 70}")
        print(f"  批量分析完成")
        print(f"{'=' * 70}")
        print(f"  处理数量: {count} 条")
        print(f"{'=' * 70}\n")
        
    elif args.top_news:
        # 显示最重要的新闻
        top_news = analyzer.get_top_news(
            days=args.days, 
            min_importance=args.min_importance,
            limit=args.top_news
        )
        
        print(f"\n{'=' * 70}")
        print(f"  过去 {args.days} 天最重要的 {len(top_news)} 条新闻")
        print(f"{'=' * 70}")
        
        for i, news in enumerate(top_news, 1):
            print(f"\n{i}. {news['title']}")
            print(f"   来源: {news.get('source', '?')}")
            print(f"   情感: {news['sentiment_score']:+.2f} ({news['sentiment_label']})")
            print(f"   影响: {news['impact_score']:.0f}分 | 重要性: {news['importance_score']:.0f}分")
            print(f"   类型: {news['event_type']} | 板块: {', '.join(news.get('related_sectors', []))}")
            print(f"   时间: {news.get('publish_time', '?')}")
            
        print(f"\n{'=' * 70}\n")
        
    elif args.report:
        # 生成报告
        report = analyzer.generate_report(days=args.days)
        
        print(f"\n{'=' * 70}")
        print(f"  舆情分析报告 (过去 {args.days} 天)")
        print(f"{'=' * 70}")
        print(f"  总数量: {report['stats']['total']} 条")
        print(f"  平均情感: {report['stats']['avg_sentiment']:.3f}")
        print(f"  正面: {report['stats']['positive_count']} | 负面: {report['stats']['negative_count']} | 中性: {report['stats']['neutral_count']}")
        print(f"  平均影响: {report['stats']['avg_impact']:.1f}分")
        print(f"  平均重要性: {report['stats']['avg_importance']:.1f}分")
        
        if report['by_event_type']:
            print(f"\n  按事件类型:")
            for event_type, count in report['by_event_type'].items():
                print(f"    {event_type:20s}: {count} 条")
                
        if report['top_news']:
            print(f"\n  Top {len(report['top_news'])} 重要新闻:")
            for i, news in enumerate(report['top_news'][:5], 1):
                print(f"    {i}. [{news['importance_score']:.0f}分] {news['title'][:50]}...")
                
        print(f"\n{'=' * 70}\n")
        
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
