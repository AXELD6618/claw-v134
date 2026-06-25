#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 统一多源知识管道 (Unified Multi-Source Knowledge Pipeline)
================================================================

自主自动采集、分析、学习、应用市场和行业知识，持续提升系统选股能力。

数据源架构:
  源1: TDX 问达 (中文新闻/研报/公告) — 通过MCP工具调用
  源2: 英文财经新闻 (CNBC/Bloomberg/Reuters) — EnglishNewsCollector
  源3: WebSearch (中文财经新闻) — WebSearch工具
  源4: 微信文件传输助手 (待修复) — WeChatRealtimeSync

分析闭环:
  采集 → 去重 → 评分 → 情感分析 → 提取信号 → 知识库更新 → 因子增强 → 奖惩

版本: V13.2 KP-1
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import json
import sqlite3
import logging
import time
import hashlib
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Any
from collections import defaultdict

# Setup paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Configure logging
os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/knowledge_pipeline.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('KnowledgePipeline')


class KnowledgePipeline:
    """统一多源知识管道"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        self.stats = {
            'tdx_news': 0,
            'english_news': 0,
            'web_search': 0,
            'wechat': 0,
            'total_analyzed': 0,
            'signals_generated': 0,
            'knowledge_updates': 0,
        }
        self._init_db()
        logger.info("[KnowledgePipeline] 统一知识管道初始化完成")
    
    # ============================================================
    # 数据库初始化
    # ============================================================
    def _init_db(self):
        """初始化知识管道数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 统一知识条目表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,          -- tdx_news/english_news/web_search/wechat
                    source_type TEXT,              -- news/report/notice/macro/social
                    title TEXT,
                    content TEXT,
                    summary TEXT,                  -- AI摘要
                    url TEXT,
                    published_at TEXT,
                    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    content_hash TEXT UNIQUE,      -- 去重用
                    sentiment_score REAL DEFAULT 0,
                    importance_score INTEGER DEFAULT 50,
                    relevance_score REAL DEFAULT 0,-- 与持仓/监控池相关性
                    keywords TEXT,                 -- JSON数组
                    tickers TEXT,                  -- JSON数组,涉及的股票代码
                    sectors TEXT,                  -- JSON数组,涉及的行业
                    processed INTEGER DEFAULT 0,   -- 0=新/1=已分析/2=已学习/3=已应用
                    applied_to TEXT,               -- JSON,应用到哪些因子/模块
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 知识学习记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS knowledge_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    knowledge_id INTEGER,
                    learning_type TEXT,           -- sentiment/pattern/factor/macro
                    insight TEXT,                  -- 学到的洞察
                    action_taken TEXT,            -- 采取的行动
                    factor_impact TEXT,            -- JSON,因子影响
                    confidence REAL DEFAULT 0.5,
                    verified INTEGER DEFAULT 0,   -- 0=未验证/1=已验证
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (knowledge_id) REFERENCES knowledge_items(id)
                )
            """)
            
            # 每日知识摘要表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_knowledge_summary (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    summary_date TEXT UNIQUE,
                    total_items INTEGER DEFAULT 0,
                    positive_count INTEGER DEFAULT 0,
                    negative_count INTEGER DEFAULT 0,
                    top_sectors TEXT,              -- JSON
                    top_tickers TEXT,              -- JSON
                    key_insights TEXT,             -- JSON
                    market_sentiment REAL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()
            logger.info("[KnowledgePipeline] 数据库表初始化完成")
        except Exception as e:
            logger.error(f"[KnowledgePipeline] 数据库初始化失败: {e}")
    
    # ============================================================
    # 数据采集接口
    # ============================================================
    
    def ingest_tdx_news(self, news_items: List[Dict]) -> int:
        """
        接入TDX问达新闻数据
        
        Args:
            news_items: [{'title':..., 'content':..., 'published_at':..., 'source':..., 'url':...}, ...]
        
        Returns:
            新增条目数
        """
        count = 0
        for item in news_items:
            if self._save_knowledge_item(
                source='tdx_news',
                source_type=item.get('type', 'news'),
                title=item.get('title', ''),
                content=item.get('content', ''),
                summary=item.get('summary', ''),
                url=item.get('url', ''),
                published_at=item.get('published_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            ):
                count += 1
        
        self.stats['tdx_news'] += count
        logger.info(f"[KnowledgePipeline] TDX新闻接入: {count}条")
        return count
    
    def ingest_english_news(self, news_items: List[Dict]) -> int:
        """接入英文新闻数据"""
        count = 0
        for item in news_items:
            if self._save_knowledge_item(
                source='english_news',
                source_type='news',
                title=item.get('title', ''),
                content=item.get('content', ''),
                summary=item.get('summary', ''),
                url=item.get('url', ''),
                published_at=item.get('published_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            ):
                count += 1
        
        self.stats['english_news'] += count
        logger.info(f"[KnowledgePipeline] 英文新闻接入: {count}条")
        return count
    
    def ingest_web_search(self, search_results: List[Dict]) -> int:
        """接入WebSearch结果"""
        count = 0
        for item in search_results:
            if self._save_knowledge_item(
                source='web_search',
                source_type=item.get('type', 'news'),
                title=item.get('title', ''),
                content=item.get('content', item.get('snippet', '')),
                url=item.get('url', ''),
                published_at=item.get('published_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            ):
                count += 1
        
        self.stats['web_search'] += count
        logger.info(f"[KnowledgePipeline] WebSearch接入: {count}条")
        return count
    
    def ingest_wechat_file(self, file_items: List[Dict]) -> int:
        """
        接入微信文件传输助手数据
        
        Args:
            file_items: [{'title':..., 'content':..., 'published_at':..., 'type':...}, ...]
        
        Returns:
            新增条目数
        """
        count = 0
        for item in file_items:
            if self._save_knowledge_item(
                source='wechat_file',
                source_type=item.get('type', 'file'),
                title=item.get('title', ''),
                content=item.get('content', ''),
                published_at=item.get('published_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            ):
                count += 1
        
        self.stats['wechat'] += count
        logger.info(f"[KnowledgePipeline] 微信文件接入: {count}条")
        return count
    
    def _save_knowledge_item(self, **kwargs) -> bool:
        """保存单条知识条目（带去重）"""
        try:
            title = kwargs.get('title', '')
            url = kwargs.get('url', '')
            content = kwargs.get('content', '')
            
            # 生成内容哈希（去重）
            content_hash = hashlib.md5(
                f"{title}{url}{content[:200]}".encode('utf-8')
            ).hexdigest()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 检查是否已存在
            cursor.execute(
                "SELECT id FROM knowledge_items WHERE content_hash = ?",
                (content_hash,)
            )
            if cursor.fetchone():
                conn.close()
                return False  # 已存在，跳过
            
            cursor.execute("""
                INSERT INTO knowledge_items
                (source, source_type, title, content, url, published_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                kwargs.get('source', 'unknown'),
                kwargs.get('source_type', 'news'),
                title,
                content,
                url,
                kwargs.get('published_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                content_hash,
            ))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"[KnowledgePipeline] 保存知识条目失败: {e}")
            return False
    
    # ============================================================
    # 分析引擎
    # ============================================================
    
    def analyze_pending_items(self, limit: int = 50) -> int:
        """
        分析所有未处理的知识条目
        
        Returns:
            分析的条目数
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM knowledge_items WHERE processed = 0 ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        items = cursor.fetchall()
        
        analyzed = 0
        for item in items:
            try:
                item_id = item['id']
                content = item['content'] or item['title'] or ''
                
                # 1. 情感分析
                sentiment = self._analyze_sentiment(content)
                
                # 2. 重要性评分
                importance = self._calculate_importance(content)
                
                # 3. 提取关键词和股票代码
                keywords = self._extract_keywords(content)
                tickers = self._extract_tickers(content)
                sectors = self._extract_sectors(content)
                
                # 4. 相关性评分（与持仓/监控池）
                relevance = self._calculate_relevance(tickers, sectors)
                
                # 5. 更新数据库
                cursor.execute("""
                    UPDATE knowledge_items SET
                        sentiment_score = ?,
                        importance_score = ?,
                        relevance_score = ?,
                        keywords = ?,
                        tickers = ?,
                        sectors = ?,
                        processed = 1
                    WHERE id = ?
                """, (
                    sentiment,
                    importance,
                    relevance,
                    json.dumps(keywords, ensure_ascii=False),
                    json.dumps(tickers, ensure_ascii=False),
                    json.dumps(sectors, ensure_ascii=False),
                    item_id,
                ))
                
                analyzed += 1
                
                # 6. 高重要性条目触发学习
                if importance >= 70 and relevance >= 30:
                    self._trigger_learning(item_id, sentiment, importance, keywords, tickers, sectors)
                
            except Exception as e:
                logger.error(f"[KnowledgePipeline] 分析条目 {item['id']} 失败: {e}")
                continue
        
        conn.commit()
        conn.close()
        
        self.stats['total_analyzed'] += analyzed
        logger.info(f"[KnowledgePipeline] 分析完成: {analyzed}条")
        return analyzed
    
    def _analyze_sentiment(self, text: str) -> float:
        """
        情感分析（增强版规则引擎）
        
        Returns:
            -1.0 (极度利空) ~ 1.0 (极度利好)
        """
        # 强力利好关键词（权重3）
        strong_positive = [
            '涨停', '连续涨停', '一字板', '翻倍', '重大突破',
            '超预期', '业绩暴增', '订单爆发', '政策重磅利好',
            '国家级', '战略支持', '行业龙头', '全球领先',
            '技术突破', '量产', '放量突破', '主升浪',
            'blockbuster', 'breakthrough', 'record high',
            'beat estimates', 'surge', 'rally'
        ]
        
        # 普通利好关键词（权重2）
        positive = [
            '利好', '上涨', '买入', '推荐', '看好', '增持',
            '增长', '盈利', '扩张', '中标', '签约',
            '创新高', '放量', '资金流入', '机构加仓',
            'upgrade', 'buy', 'outperform', 'growth',
            'profit', 'expansion', 'partnership'
        ]
        
        # 强力利空关键词（权重-3）
        strong_negative = [
            '跌停', '连续跌停', '暴雷', '退市', '破产',
            '财务造假', 'ST', '*ST', '监管处罚', '立案调查',
            '巨亏', '崩盘', '踩踏', '流动性危机',
            'crash', 'bankruptcy', 'fraud', 'delisting',
            'plunge', 'collapse', 'investigation'
        ]
        
        # 普通利空关键词（权重-2）
        negative = [
            '利空', '下跌', '卖出', '回避', '看空', '减持',
            '下滑', '亏损', '违约', '诉讼', '仲裁',
            '破位', '缩量', '资金流出', '机构减仓',
            'downgrade', 'sell', 'underperform', 'decline',
            'loss', 'lawsuit', 'default'
        ]
        
        score = 0
        total_weight = 0
        
        for kw in strong_positive:
            if kw.lower() in text.lower():
                score += 3
                total_weight += 3
        for kw in positive:
            if kw.lower() in text.lower():
                score += 2
                total_weight += 2
        for kw in strong_negative:
            if kw.lower() in text.lower():
                score -= 3
                total_weight += 3
        for kw in negative:
            if kw.lower() in text.lower():
                score -= 2
                total_weight += 2
        
        if total_weight == 0:
            return 0.0
        
        return max(-1.0, min(1.0, score / total_weight))
    
    def _calculate_importance(self, text: str) -> int:
        """计算重要性（0-100）"""
        importance = 30  # 基础分
        
        # 高层级关键词（+20）
        high_impact = [
            '央行', '降息', '降准', 'LPR', 'MLF', '逆回购',
            '国务院', '发改委', '工信部', '证监会', '财政部',
            '美联储', '加息', '缩表', '非农',
            '战争', '制裁', '关税', '贸易战',
            '重大资产重组', '借壳', 'IPO',
            'Trump', 'Biden', 'Xi', 'Putin',
            'recession', 'inflation', 'FOMC'
        ]
        for kw in high_impact:
            if kw.lower() in text.lower():
                importance += 15
        
        # 市场关注关键词（+10）
        market_focus = [
            '涨停', '跌停', '龙虎榜', '机构', '北向资金',
            '增持', '减持', '回购', '分红', '业绩预告',
            'earnings', 'guidance', 'analyst'
        ]
        for kw in market_focus:
            if kw.lower() in text.lower():
                importance += 8
        
        # 行业关键词（+5）
        industry = [
            'AI', '芯片', '半导体', '新能源', '光伏', '锂电池',
            '机器人', '算力', '数据', '医药', '军工',
            'AI chip', 'semiconductor', 'EV', 'solar'
        ]
        found_industries = sum(1 for kw in industry if kw.lower() in text.lower())
        importance += found_industries * 5
        
        # 股票代码（+5 per code, max +20）
        stock_codes = re.findall(r'(60\d{4}|00\d{4}|30\d{4}|688\d{3}|8\d{5})', text)
        importance += min(20, len(stock_codes) * 5)
        
        return min(100, importance)
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        
        # 金融热词库
        hot_words = [
            '降息', '降准', '加息', '通胀', 'GDP', 'PMI', 'CPI',
            'A股', '上证', '深证', '创业板', '科创板', '北交所',
            '涨停', '跌停', '涨停板', '跌停板', '连板',
            'AI', '人工智能', '芯片', '半导体', '光刻机',
            '新能源', '光伏', '锂电池', '储能', '氢能',
            '机器人', '人形机器人', '具身智能',
            '算力', '数据中心', '液冷', '光模块',
            '低空经济', '飞行汽车', '商业航天',
            '医药', '创新药', '医疗器械',
            '消费', '白酒', '食品', '家电',
            '地产', '基建', '建材',
            '券商', '银行', '保险',
            '期货', '期权', 'ETF',
            '北向资金', '主力资金', '游资',
            '龙虎榜', '机构席位',
        ]
        
        for word in hot_words:
            if word in text:
                keywords.append(word)
        
        # 去重并限制数量
        seen = set()
        unique_keywords = []
        for kw in keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)
        
        return unique_keywords[:10]
    
    def _extract_tickers(self, text: str) -> List[str]:
        """提取股票代码"""
        patterns = [
            r'(60\d{4})',      # 上海主板
            r'(00\d{4})',      # 深圳主板
            r'(30\d{4})',      # 创业板
            r'(688\d{3})',     # 科创板
            r'(8\d{5})',       # 北交所
            r'(4\d{5})',       # 新三板
        ]
        
        tickers = []
        for pattern in patterns:
            found = re.findall(pattern, text)
            tickers.extend(found)
        
        return list(set(tickers))[:20]
    
    def _extract_sectors(self, text: str) -> List[str]:
        """提取行业分类"""
        sector_map = {
            'AI算力': ['AI', '算力', 'GPU', '数据中心', '液冷', '光模块', 'CPO'],
            '半导体': ['芯片', '半导体', '光刻', '晶圆', '封装', 'EDA', 'HBM'],
            '新能源': ['光伏', '锂电', '储能', '氢能', '钠离子', '固态电池'],
            '机器人': ['机器人', '人形', '具身智能', '自动化', '减速器'],
            '医药': ['医药', '创新药', '医疗器械', 'CXO', '基因'],
            '消费': ['白酒', '食品', '家电', '旅游', '免税', '电商'],
            '军工': ['军工', '航天', '导弹', '雷达', '舰船'],
            '金融': ['券商', '银行', '保险', '期货'],
            '地产': ['地产', '房地产', '基建', '建材'],
            '汽车': ['汽车', '新能源车', '自动驾驶', '智能座舱'],
            '电力设备': ['特高压', '变压器', 'GIS', '开关', '电力'],
        }
        
        found_sectors = []
        for sector, keywords in sector_map.items():
            for kw in keywords:
                if kw in text:
                    found_sectors.append(sector)
                    break
        
        return list(set(found_sectors))
    
    def _calculate_relevance(self, tickers: List[str], sectors: List[str]) -> float:
        """计算与持仓/监控池的相关性"""
        # 加载监控池股票
        universe_tickers = self._load_universe_tickers()
        
        if not tickers and not sectors:
            return 10.0  # 基础相关性
        
        relevance = 10.0
        
        # 股票直接匹配
        matched_tickers = [t for t in tickers if t in universe_tickers]
        relevance += len(matched_tickers) * 20
        
        # 行业匹配（简化）
        relevance += len(sectors) * 10
        
        return min(100.0, relevance)
    
    def _load_universe_tickers(self) -> set:
        """加载监控池股票代码"""
        tickers = set()
        try:
            # 从动态池模块加载
            dynamic_pool_path = os.path.join(BASE_DIR, 'V13_1_P0_DynamicPool.py')
            if os.path.exists(dynamic_pool_path):
                with open(dynamic_pool_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    found = re.findall(r'(60\d{4}|00\d{4}|30\d{4}|688\d{3})', content)
                    tickers.update(found)
        except:
            pass
        
        return tickers if tickers else set()
    
    # ============================================================
    # 学习与知识应用
    # ============================================================
    
    def _trigger_learning(self, item_id: int, sentiment: float, importance: int,
                          keywords: List[str], tickers: List[str], sectors: List[str]):
        """触发知识学习流程"""
        try:
            insight = self._derive_insight(sentiment, importance, keywords, tickers, sectors)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO knowledge_learning
                (knowledge_id, learning_type, insight, action_taken, confidence)
                VALUES (?, ?, ?, ?, ?)
            """, (item_id, 'sentiment', insight, 'recorded', 0.6))
            
            conn.commit()
            conn.close()
            
            self.stats['knowledge_updates'] += 1
            
        except Exception as e:
            logger.error(f"[KnowledgePipeline] 触发学习失败: {e}")
    
    def _derive_insight(self, sentiment: float, importance: int,
                        keywords: List[str], tickers: List[str], sectors: List[str]) -> str:
        """从知识条目推导洞察"""
        insights = []
        
        if sentiment > 0.5 and importance >= 70:
            insights.append(f"利好信号: 情感={sentiment:.2f}, 重要性={importance}")
            if sectors:
                insights.append(f"影响行业: {', '.join(sectors[:3])}")
            if tickers:
                insights.append(f"相关股票: {', '.join(tickers[:5])}")
        
        elif sentiment < -0.3 and importance >= 60:
            insights.append(f"风险信号: 情感={sentiment:.2f}, 重要性={importance}")
        
        return ' | '.join(insights) if insights else "一般信息"
    
    # ============================================================
    # 每日摘要
    # ============================================================
    
    def generate_daily_summary(self) -> Dict:
        """生成每日知识摘要"""
        today = datetime.now().strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 统计今天的数据
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                AVG(sentiment_score) as avg_sentiment,
                SUM(CASE WHEN sentiment_score > 0.2 THEN 1 ELSE 0 END) as positive,
                SUM(CASE WHEN sentiment_score < -0.2 THEN 1 ELSE 0 END) as negative
            FROM knowledge_items
            WHERE date(collected_at) = date('now')
        """)
        row = cursor.fetchone()
        
        total = row['total'] if row['total'] else 0
        positive = row['positive'] if row['positive'] else 0
        negative = row['negative'] if row['negative'] else 0
        avg_sentiment = row['avg_sentiment'] if row['avg_sentiment'] else 0
        
        # 获取高重要性条目
        cursor.execute("""
            SELECT title, sentiment_score, importance_score, tickers, sectors
            FROM knowledge_items
            WHERE date(collected_at) = date('now') AND importance_score >= 70
            ORDER BY importance_score DESC
            LIMIT 10
        """)
        top_items = [dict(r) for r in cursor.fetchall()]
        
        # 保存摘要
        cursor.execute("""
            INSERT OR REPLACE INTO daily_knowledge_summary
            (summary_date, total_items, positive_count, negative_count,
             top_tickers, key_insights, market_sentiment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            total,
            positive,
            negative,
            json.dumps(self._aggregate_tickers(top_items), ensure_ascii=False),
            json.dumps([i['title'][:100] for i in top_items[:5]], ensure_ascii=False),
            avg_sentiment or 0,
        ))
        
        conn.commit()
        conn.close()
        
        summary = {
            'date': today,
            'total_items': total,
            'positive': positive,
            'negative': negative,
            'neutral': total - positive - negative,
            'avg_sentiment': avg_sentiment or 0,
            'top_items': top_items,
            'stats': self.stats,
        }
        
        logger.info(f"[KnowledgePipeline] 每日摘要: 总计{total}条, 利好{positive}, 利空{negative}")
        return summary
    
    def _aggregate_tickers(self, items: List[Dict]) -> List[str]:
        """聚合所有出现的股票代码"""
        all_tickers = []
        for item in items:
            tickers_str = item.get('tickers', '[]')
            try:
                tickers = json.loads(tickers_str)
                all_tickers.extend(tickers)
            except:
                pass
        return list(set(all_tickers))[:20]
    
    # ============================================================
    # 报告生成
    # ============================================================
    
    def print_report(self):
        """输出管道报告"""
        report = f"""
{'='*60}
  V13.2 知识管道运行报告
  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*60}
📊 数据采集:
  ├─ TDX中文新闻:    {self.stats['tdx_news']:>5} 条
  ├─ 英文财经新闻:    {self.stats['english_news']:>5} 条
  ├─ WebSearch:       {self.stats['web_search']:>5} 条
  └─ 微信:           {self.stats['wechat']:>5} 条

🔬 分析处理:
  └─ 已分析条目:      {self.stats['total_analyzed']:>5} 条

💡 知识学习:
  ├─ 生成信号:        {self.stats['signals_generated']:>5} 个
  └─ 知识更新:        {self.stats['knowledge_updates']:>5} 条
{'='*60}
"""
        print(report)
        logger.info(report)
        return report


# ============================================================
# 主入口
# ============================================================
def main():
    """运行知识管道"""
    logger.info("=" * 60)
    logger.info("[KnowledgePipeline] 启动统一多源知识管道")
    logger.info("=" * 60)
    
    pipeline = KnowledgePipeline()
    
    # 1. 分析未处理条目
    analyzed = pipeline.analyze_pending_items(limit=100)
    
    # 2. 生成每日摘要
    summary = pipeline.generate_daily_summary()
    
    # 3. 打印报告
    pipeline.print_report()
    
    logger.info("[KnowledgePipeline] 管道运行完成")
    return summary


if __name__ == '__main__':
    main()
