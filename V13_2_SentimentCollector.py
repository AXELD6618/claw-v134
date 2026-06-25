#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 舆情采集器 (Sentiment Collector)
24小时自主在线发掘甄别收集分析运用

功能:
1. 多源舆情采集 (官媒/财经媒体/社交媒体/公告/TDX问小达/英文财经)
2. 数据清洗与去重
3. 存储到SQLite (sentiment_db.db)
4. 与奖惩引擎联动接口
5. 24小时定时采集调度

数据源:
- TDX问小达: wenda_news_query / wenda_notice_query / wenda_report_query
- WebSearch: 实时搜索最新财经新闻
- 官媒RSS: 新华社 / 人民日报
- 财经媒体: 财联社 / 东方财富 / 同花顺
- 社交媒体: 微博财经 / 雪球热点
- 公告系统: 上交所 / 深交所
- 英文财经: Reuters / Bloomberg / FT / CNBC (V13.2新增)

采集频率:
- 交易时段 (09:00-15:30): 每15分钟
- 非交易时段: 每小时
- 突发事件: 实时告警

奖惩联动:
- 提前N小时发现重要舆情 → +分数
- 重要舆情未被发现 → -分数
- 正确解读并指导交易 → +分数
- 错误解读导致亏损 → -分数

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
import hashlib

# 导入英文采集器 (V13.2新增)
try:
    from V13_2_EnglishNewsCollector import EnglishNewsCollector, EN_KEYWORDS
    ENGLISH_COLLECTOR_AVAILABLE = True
except ImportError:
    ENGLISH_COLLECTOR_AVAILABLE = False
    logger.warning("⚠️ 英文采集器导入失败，英文源将不可用")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_collector.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ───────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'
COLLECTION_INTERVAL_TRADING = 15 * 60  # 交易时段：15分钟
COLLECTION_INTERVAL_NON_TRADING = 60 * 60  # 非交易时段：1小时

# 数据源配置
DATA_SOURCES = {
    'tdx_wenda': {
        'enabled': True,
        'priority': 'high',
        'update_interval': 900,  # 15分钟
    },
    'web_search': {
        'enabled': True,
        'priority': 'high',
        'update_interval': 1800,  # 30分钟
    },
    'official_media': {
        'enabled': True,
        'priority': 'medium',
        'update_interval': 3600,  # 1小时
    },
    'financial_media': {
        'enabled': True,
        'priority': 'high',
        'update_interval': 1800,  # 30分钟
    },
    'social_media': {
        'enabled': True,
        'priority': 'low',
        'update_interval': 3600,  # 1小时
    },
    'announcements': {
        'enabled': True,
        'priority': 'high',
        'update_interval': 1800,  # 30分钟
    },
    'english_news': {   # 英文财经新闻
        'enabled': True,
        'priority': 'high',
        'update_interval': 1800,  # 30分钟
    },
}

# 关键词监控列表（中文）
KEYWORDS_CN = [
    # 地缘政治
    '霍尔木兹', '海峡', '伊朗', '美国', '石油', '航运',
    '中美', '贸易', '制裁', '关税',
    # 政策
    '央行', '降准', '降息', '财政政策', '基建',
    '新能源', '碳中和', '半导体', '国产替代',
    # 行业
    'AI', '算力', '机器人', '无人机', '商业航天',
    '锂电池', '光伏', '风电', '氢能',
    # 财报
    '业绩', '预增', '预减', '扭亏', '超预期',
    # 并购重组
    '并购', '重组', '借壳', '注入',
]

# 关键词监控列表（英文）
KEYWORDS_EN = [
    # Geopolitics
    'Hormuz', 'strait', 'Iran', 'US', 'oil', 'shipping',
    'China US', 'trade', 'sanctions', 'tariff',
    # Policy
    'Fed', 'rate cut', 'QE', 'fiscal', 'infrastructure',
    'stimulus', 'monetary', 'PBOC',
    # Industry
    'AI', 'semiconductor', 'chip', 'GPU', 'datacenter',
    'lithium', 'photovoltaic', 'wind power', 'hydrogen',
    # Earnings
    'earnings', 'profit', 'revenue', 'guidance', 'beat',
    # M&A
    'merger', 'acquisition', 'takeover', 'buyout',
    # Macro
    'GDP', 'CPI', 'inflation', 'recession', 'PMI',
    # Commodities
    'crude', 'copper', 'gold', 'iron ore',
]

# 合并关键词
KEYWORDS = KEYWORDS_CN + [kw.lower() for kw in KEYWORDS_EN]

# ── 数据库初始化 ─────────────────────────────────────────────────
def init_database(db_path: str = DB_PATH):
    """初始化舆情数据库"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # 原始新闻表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS news_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_id TEXT,
            title TEXT NOT NULL,
            content TEXT,
            url TEXT,
            publish_time TEXT,
            crawl_time TEXT DEFAULT CURRENT_TIMESTAMP,
            raw_data TEXT,
            hash TEXT UNIQUE,
            UNIQUE(source, source_id)
        )
    ''')
    
    # 处理后新闻表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS news_processed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_id INTEGER,
            title TEXT NOT NULL,
            summary TEXT,
            entities TEXT,  # JSON: {"stocks": [], "sectors": [], "concepts": []}
            sentiment_score REAL,  # -1.0 (极度负面) ~ +1.0 (极度正面)
            sentiment_label TEXT,  # 'positive'/'negative'/'neutral'
            impact_score REAL,  # 0-100 影响程度
            importance_score REAL,  # 0-100 重要程度
            event_type TEXT,  # 'policy'/'earnings'/'m&a'/'geopolitical'/...
            related_stocks TEXT,  # JSON: ["600519", "000001"]
            related_sectors TEXT,  # JSON: ["航运", "石油"]
            processed_time TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (raw_id) REFERENCES news_raw(id)
        )
    ''')
    
    # 交易信号表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS trading_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            signal_type TEXT,  # 'buy'/'sell'/'hold'
            signal_strength REAL,  # 0-1 信号强度
            confidence REAL,  # 0-1 置信度
            target_stocks TEXT,  # JSON: [{"code": "600519", "name": "贵州茅台", "weight": 0.8}]
            reasoning TEXT,
            expected_impact_days INTEGER,  # 预期影响天数
            created_time TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'active',  # 'active'/'expired'/'executed'
            actual_outcome TEXT,  # 'success'/'failure'/'pending'
            FOREIGN KEY (news_id) REFERENCES news_processed(id)
        )
    ''')
    
    # 采集日志表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS collection_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            collect_time TEXT DEFAULT CURRENT_TIMESTAMP,
            items_collected INTEGER DEFAULT 0,
            items_new INTEGER DEFAULT 0,
            items_updated INTEGER DEFAULT 0,
            errors TEXT,
            duration_ms INTEGER
        )
    ''')
    
    # 奖惩联动表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS reward_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,  # 'discovery'/'miss'/'correct_interpretation'/'wrong_interpretation'
            news_id INTEGER,
            score_change REAL,
            reason TEXT,
            created_time TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (news_id) REFERENCES news_processed(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info(f"✅ 数据库初始化完成: {db_path}")

# ── 工具函数 ───────────────────────────────────────────────────
def compute_hash(title: str, content: str = '') -> str:
    """计算新闻哈希值（用于去重）"""
    text = (title or '') + (content or '')[:500]
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def is_trading_time() -> bool:
    """判断当前是否为交易时段 (09:00-15:30, 周一至周五)"""
    now = datetime.now()
    
    # 周末
    if now.weekday() >= 5:
        return False
    
    # 时间段
    current_time = now.time()
    morning_start = datetime.strptime('09:00', '%H:%M').time()
    evening_end = datetime.strptime('15:30', '%H:%M').time()
    
    return morning_start <= current_time <= evening_end

def get_collection_interval() -> int:
    """获取当前采集间隔（秒）"""
    if is_trading_time():
        return COLLECTION_INTERVAL_TRADING
    else:
        return COLLECTION_INTERVAL_NON_TRADING

# ── TDX问小达采集器 ──────────────────────────────────────────
class TDXWendaCollector:
    """TDX问小达数据采集器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.source_name = 'tdx_wenda'
        
    def collect_news(self, keywords: List[str] = None, top_k: int = 20) -> Tuple[int, int]:
        """
        采集问小达新闻
        
        返回: (采集总数, 新增数量)
        """
        if keywords is None:
            keywords = KEYWORDS[:10]  # 限制关键词数量
            
        total_collected = 0
        total_new = 0
        
        # 注意：这里需要调用TDX MCP工具
        # 由于无法在代码中直接调用MCP工具，这里提供接口框架
        # 实际采集需要通过自动化任务调用MCP工具
        
        logger.info(f"TDX问小达采集器: 关键词={keywords[:5]}...")
        
        # 模拟采集（实际应通过MCP调用）
        # TODO: 集成真实的TDX MCP调用
        
        return total_collected, total_new
    
    def collect_notices(self, keywords: List[str] = None, top_k: int = 20) -> Tuple[int, int]:
        """采集问小达公告"""
        logger.info("TDX问小达公告采集器: 开始采集...")
        # TODO: 集成真实的TDX MCP调用
        return 0, 0
    
    def collect_reports(self, keywords: List[str] = None, top_k: int = 20) -> Tuple[int, int]:
        """采集问小达研报"""
        logger.info("TDX问小达研报采集器: 开始采集...")
        # TODO: 集成真实的TDX MCP调用
        return 0, 0

# ── WebSearch采集器 ────────────────────────────────────────────
class WebSearchCollector:
    """WebSearch采集器"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.source_name = 'web_search'
        
    def collect_news(self, query: str, max_results: int = 20) -> Tuple[int, int]:
        """
        通过WebSearch采集新闻
        
        返回: (采集总数, 新增数量)
        """
        logger.info(f"WebSearch采集: query={query[:50]}...")
        
        # TODO: 集成真实的WebSearch调用
        # 需要通过自动化任务调用WebSearch工具
        
        return 0, 0
    
    def batch_collect(self, queries: List[str], max_per_query: int = 10) -> Tuple[int, int]:
        """批量采集"""
        total_collected = 0
        total_new = 0
        
        for query in queries:
            c, n = self.collect_news(query, max_per_query)
            total_collected += c
            total_new += n
            
        return total_collected, total_new

# ── 官媒RSS采集器 ────────────────────────────────────────────
class OfficialMediaCollector:
    """官媒RSS采集器"""
    
    RSS_FEEDS = {
        'xinhua': 'http://www.xinhuanet.com/politics/news_politics.xml',
        'people': 'http://www.people.com.cn/rss/politics.xml',
    }
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.source_name = 'official_media'
        
    def collect_rss(self, feed_name: str, feed_url: str) -> Tuple[int, int]:
        """采集RSS feed"""
        logger.info(f"官媒RSS采集: {feed_name}...")
        # TODO: 集成RSS解析库 (feedparser)
        return 0, 0
    
    def collect_all(self) -> Tuple[int, int]:
        """采集所有官媒RSS"""
        total_collected = 0
        total_new = 0
        
        for name, url in self.RSS_FEEDS.items():
            c, n = self.collect_rss(name, url)
            total_collected += c
            total_new += n
            
        return total_collected, total_new

# ── 财经媒体采集器 ────────────────────────────────────────────
class FinancialMediaCollector:
    """财经媒体采集器"""
    
    SOURCES = {
        'cailian': 'https://www.cls.cn',
        'eastmoney': 'https://finance.eastmoney.com',
        '10jqka': 'https://news.10jqka.com.cn',
    }
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.source_name = 'financial_media'
        
    def collect(self, source_name: str, source_url: str) -> Tuple[int, int]:
        """采集单个财经媒体源"""
        logger.info(f"财经媒体采集: {source_name}...")
        # TODO: 集成网页爬虫
        return 0, 0
    
    def collect_all(self) -> Tuple[int, int]:
        """采集所有财经媒体"""
        total_collected = 0
        total_new = 0
        
        for name, url in self.SOURCES.items():
            c, n = self.collect(name, url)
            total_collected += c
            total_new += n
            
        return total_collected, total_new

# ── 社交媒体采集器 ────────────────────────────────────────────
class SocialMediaCollector:
    """社交媒体采集器"""
    
    SOURCES = {
        'weibo_finance': '微博财经',
        'xueqiu': '雪球',
    }
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.source_name = 'social_media'
        
    def collect_weibo(self, keyword: str) -> Tuple[int, int]:
        """采集微博"""
        logger.info(f"微博采集: keyword={keyword}...")
        # TODO: 集成微博API或爬虫
        return 0, 0
    
    def collect_xueqiu(self, keyword: str) -> Tuple[int, int]:
        """采集雪球"""
        logger.info(f"雪球采集: keyword={keyword}...")
        # TODO: 集成雪球API或爬虫
        return 0, 0
    
    def collect_all_keywords(self, keywords: List[str]) -> Tuple[int, int]:
        """采集所有关键词"""
        total_collected = 0
        total_new = 0
        
        for keyword in keywords[:5]:  # 限制数量
            c1, n1 = self.collect_weibo(keyword)
            c2, n2 = self.collect_xueqiu(keyword)
            total_collected += c1 + c2
            total_new += n1 + n2
            
        return total_collected, total_new

# ── 公告系统采集器 ────────────────────────────────────────────
class AnnouncementCollector:
    """公告系统采集器"""
    
    SOURCES = {
        'sse': 'http://www.sse.com.cn',  # 上交所
        'szse': 'http://www.szse.cn',  # 深交所
    }
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.source_name = 'announcements'
        
    def collect_sse(self) -> Tuple[int, int]:
        """采集上交所公告"""
        logger.info("上交所公告采集...")
        # TODO: 集成上交所API
        return 0, 0
    
    def collect_szse(self) -> Tuple[int, int]:
        """采集深交所公告"""
        logger.info("深交所公告采集...")
        # TODO: 集成深交所API
        return 0, 0
    
    def collect_all(self) -> Tuple[int, int]:
        """采集所有公告"""
        total_collected = 0
        total_new = 0
        
        c1, n1 = self.collect_sse()
        c2, n2 = self.collect_szse()
        total_collected += c1 + c2
        total_new += n1 + n2
            
        return total_collected, total_new

# ── 主采集协调器 ──────────────────────────────────────────────
class SentimentCollector:
    """舆情采集协调器（主类）"""
    
    def __init__(self, db_path: str = DB_PATH, enable_all_sources: bool = True):
        self.db_path = db_path
        init_database(db_path)
        
        # 初始化各采集器
        self.collectors = {}
        
        if DATA_SOURCES['tdx_wenda']['enabled']:
            self.collectors['tdx_wenda'] = TDXWendaCollector(db_path)
            
        if DATA_SOURCES['web_search']['enabled']:
            self.collectors['web_search'] = WebSearchCollector(db_path)
            
        if DATA_SOURCES['official_media']['enabled']:
            self.collectors['official_media'] = OfficialMediaCollector(db_path)
            
        if DATA_SOURCES['financial_media']['enabled']:
            self.collectors['financial_media'] = FinancialMediaCollector(db_path)
            
        if DATA_SOURCES['social_media']['enabled']:
            self.collectors['social_media'] = SocialMediaCollector(db_path)
            
        if DATA_SOURCES['announcements']['enabled']:
            self.collectors['announcements'] = AnnouncementCollector(db_path)
            
        # 英文财经新闻采集器 (V13.2新增)
        if ENGLISH_COLLECTOR_AVAILABLE and DATA_SOURCES['english_news']['enabled']:
            self.collectors['english_news'] = EnglishNewsCollector(db_path)
            logger.info(f"  ✅ 英文采集器已加载")
            
        logger.info(f"✅ 舆情采集器初始化完成，已加载 {len(self.collectors)} 个数据源")
        
    def collect_all(self, keywords: List[str] = None) -> Dict:
        """
        采集所有数据源
        
        返回: 采集统计字典
        """
        if keywords is None:
            keywords = KEYWORDS
            
        stats = {
            'total_collected': 0,
            'total_new': 0,
            'by_source': {},
            'errors': [],
            'start_time': datetime.now().isoformat(),
        }
        
        logger.info(f"🚀 开始全源采集... 关键词数量={len(keywords)}")
        
        for source_name, collector in self.collectors.items():
            try:
                start_time = time.time()
                
                if source_name == 'tdx_wenda':
                    c, n = collector.collect_news(keywords)
                    c2, n2 = collector.collect_notices(keywords)
                    c3, n3 = collector.collect_reports(keywords)
                    c += c2 + c3
                    n += n2 + n3
                    
                elif source_name == 'web_search':
                    # 构造查询
                    queries = [f"{kw} A股" for kw in keywords[:5]]
                    c, n = collector.batch_collect(queries)
                    
                elif source_name == 'official_media':
                    c, n = collector.collect_all()
                    
                elif source_name == 'financial_media':
                    c, n = collector.collect_all()
                    
                elif source_name == 'social_media':
                    c, n = collector.collect_all_keywords(keywords[:10])
                    
                elif source_name == 'announcements':
                    c, n = collector.collect_all()
                    
                elif source_name == 'english_news':
                    # 英文财经新闻采集 (V13.2新增)
                    en_keywords = []
                    # 合并中英文关键词
                    en_keywords.extend(KEYWORDS_CN[:5])
                    en_keywords.extend(KEYWORDS_EN[:5])
                    c, n = collector.collect_all(keywords=en_keywords, max_per_source=10)
                    
                else:
                    c, n = 0, 0
                    
                duration_ms = int((time.time() - start_time) * 1000)
                
                stats['by_source'][source_name] = {
                    'collected': c,
                    'new': n,
                    'duration_ms': duration_ms,
                }
                stats['total_collected'] += c
                stats['total_new'] += n
                
                # 记录日志
                self._log_collection(source_name, c, n, None, duration_ms)
                
                logger.info(f"  {source_name}: 采集={c} 新增={n} 耗时={duration_ms}ms")
                
            except Exception as e:
                error_msg = f"{source_name} 采集失败: {e}"
                logger.error(error_msg)
                stats['errors'].append(error_msg)
                self._log_collection(source_name, 0, 0, error_msg, 0)
                
        stats['end_time'] = datetime.now().isoformat()
        stats['total_sources'] = len(self.collectors)
        
        logger.info(f"✅ 全源采集完成: 总计采集={stats['total_collected']} 新增={stats['total_new']}")
        
        return stats
    
    def collect_source(self, source_name: str, **kwargs) -> Tuple[int, int]:
        """采集单个数据源"""
        if source_name not in self.collectors:
            logger.error(f"数据源不存在: {source_name}")
            return 0, 0
            
        collector = self.collectors[source_name]
        
        if source_name == 'tdx_wenda':
            return collector.collect_news(kwargs.get('keywords', KEYWORDS))
        elif source_name == 'web_search':
            return collector.collect_news(kwargs.get('query', ''))
        elif source_name == 'official_media':
            return collector.collect_all()
        elif source_name == 'financial_media':
            return collector.collect_all()
        elif source_name == 'social_media':
            return collector.collect_all_keywords(kwargs.get('keywords', KEYWORDS))
        elif source_name == 'announcements':
            return collector.collect_all()
        else:
            return 0, 0
    
    def _log_collection(self, source: str, collected: int, new: int, 
                       error: Optional[str], duration_ms: int):
        """记录采集日志"""
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO collection_logs 
                (source, items_collected, items_new, errors, duration_ms)
                VALUES (?, ?, ?, ?, ?)
            ''', (source, collected, new, error, duration_ms))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"记录采集日志失败: {e}")
    
    def get_collection_stats(self, days: int = 1) -> Dict:
        """获取采集统计"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(days=days)).isoformat()
        
        # 按数据源统计
        cur.execute('''
            SELECT source, 
                   SUM(items_collected) as total_collected,
                   SUM(items_new) as total_new,
                   COUNT(*) as collect_count
            FROM collection_logs
            WHERE collect_time >= ?
            GROUP BY source
        ''', (since,))
        
        by_source = {}
        for row in cur.fetchall():
            by_source[row['source']] = dict(row)
            
        # 总时间
        cur.execute('''
            SELECT SUM(items_collected) as total_collected,
                   SUM(items_new) as total_new,
                   COUNT(*) as total_collects
            FROM collection_logs
            WHERE collect_time >= ?
        ''', (since,))
        
        total = cur.fetchone()
        
        conn.close()
        
        return {
            'period_days': days,
            'total': dict(total) if total else {},
            'by_source': by_source,
        }

# ── 命令行接口 ───────────────────────────────────────────────
def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='V13.2 舆情采集器')
    parser.add_argument('--collect-all', action='store_true', help='采集所有数据源')
    parser.add_argument('--collect-source', type=str, help='采集指定数据源')
    parser.add_argument('--keywords', type=str, nargs='+', help='关键词列表')
    parser.add_argument('--stats', action='store_true', help='显示采集统计')
    parser.add_argument('--db-path', type=str, default=DB_PATH, help='数据库路径')
    parser.add_argument('--daemon', action='store_true', help='守护进程模式（24小时运行）')
    
    args = parser.parse_args()
    
    # 初始化采集器
    collector = SentimentCollector(db_path=args.db_path)
    
    if args.collect_all:
        # 采集所有
        keywords = args.keywords if args.keywords else None
        stats = collector.collect_all(keywords=keywords)
        
        print(f"\n{'=' * 70}")
        print(f"  舆情采集报告")
        print(f"{'=' * 70}")
        print(f"  采集时间: {stats['start_time']} → {stats['end_time']}")
        print(f"  数据源数量: {stats['total_sources']}")
        print(f"  总计采集: {stats['total_collected']} 条")
        print(f"  新增数量: {stats['total_new']} 条")
        
        if stats['by_source']:
            print(f"\n  按数据源统计:")
            for source, s in stats['by_source'].items():
                print(f"    {source:25s} 采集={s['collected']:4d}  新增={s['new']:4d}  耗时={s['duration_ms']:6d}ms")
                
        if stats['errors']:
            print(f"\n  ⚠️  错误 ({len(stats['errors'])}个):")
            for err in stats['errors']:
                print(f"    - {err[:80]}")
                
        print(f"{'=' * 70}\n")
        
    elif args.collect_source:
        # 采集单个数据源
        collected, new = collector.collect_source(args.collect_source, keywords=args.keywords)
        print(f"数据源: {args.collect_source}")
        print(f"采集数量: {collected}")
        print(f"新增数量: {new}")
        
    elif args.stats:
        # 显示统计
        for days in [1, 7, 30]:
            stats = collector.get_collection_stats(days=days)
            print(f"\n📊 过去 {days} 天采集统计:")
            if stats['total']:
                print(f"  总计采集: {stats['total'].get('total_collected', 0)} 条")
                print(f"  新增数量: {stats['total'].get('total_new', 0)} 条")
                print(f"  采集次数: {stats['total'].get('total_collects', 0)} 次")
                
            if stats['by_source']:
                print(f"  按数据源:")
                for source, s in stats['by_source'].items():
                    print(f"    {source:25s} 采集={s.get('total_collected', 0):4d}  新增={s.get('total_new', 0):4d}")
                    
    elif args.daemon:
        # 守护进程模式
        print(f"🤖 舆情采集器守护进程启动 (PID={os.getpid()})")
        print(f"  交易时段采集间隔: {COLLECTION_INTERVAL_TRADING / 60} 分钟")
        print(f"  非交易时段采集间隔: {COLLECTION_INTERVAL_NON_TRADING / 60} 分钟")
        print(f"  按 Ctrl+C 停止\n")
        
        try:
            while True:
                # 采集
                stats = collector.collect_all()
                
                # 计算下次采集时间
                interval = get_collection_interval()
                next_time = datetime.now() + timedelta(seconds=interval)
                
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 采集完成 新增={stats['total_new']}条 | 下次采集: {next_time.strftime('%H:%M:%S')}")
                
                # 休眠
                time.sleep(interval)
                
        except KeyboardInterrupt:
            print("\n🛑 守护进程已停止")
            
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
