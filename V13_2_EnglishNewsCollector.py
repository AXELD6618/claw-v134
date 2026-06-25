#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 英文财经新闻采集器 (English News Collector)
支持路透、彭博、金融时报等英文源

功能:
1. 采集英文财经新闻（Reuters/Bloomberg/FT/CNBC等）
2. 英文→中文翻译（使用LLM或翻译API）
3. 存储到sentiments_db.db（source='english_news'）
4. 与中文舆情统一分析

数据源:
- Reuters: https://www.reuters.com
- Bloomberg: https://www.bloomberg.com
- Financial Times: https://www.ft.com
- CNBC: https://www.cnbc.com
- Wall Street Journal: https://www.wsj.com
- MarketWatch: https://www.marketwatch.com

采集方式:
- RSS Feed解析（优先）
- WebSearch API搜索
- 网页爬虫（备用）

翻译方式:
- 优先: WorkBuddy内置LLM翻译（更准确，保留金融术语）
- 备用: 翻译API（Google/DeepL）

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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/english_news_collector.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'

# 英文数据源配置
EN_SOURCES = {
    'reuters': {
        'name': 'Reuters',
        'url': 'https://www.reuters.com',
        'rss': 'https://www.reuters.com/rssfeed',
        'priority': 'high',
        'enabled': True,
    },
    'bloomberg': {
        'name': 'Bloomberg',
        'url': 'https://www.bloomberg.com',
        'priority': 'high',
        'enabled': True,
    },
    'ft': {
        'name': 'Financial Times',
        'url': 'https://www.ft.com',
        'priority': 'medium',
        'enabled': True,
    },
    'cnbc': {
        'name': 'CNBC',
        'url': 'https://www.cnbc.com',
        'rss': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
        'priority': 'high',
        'enabled': True,
    },
    'wsj': {
        'name': 'Wall Street Journal',
        'url': 'https://www.wsj.com',
        'priority': 'medium',
        'enabled': False,  # 需要订阅
    },
    'marketwatch': {
        'name': 'MarketWatch',
        'url': 'https://www.marketwatch.com',
        'priority': 'medium',
        'enabled': True,
    },
}

# 英文关键词（与中文关键词对应）
EN_KEYWORDS = [
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

# 翻译缓存（避免重复翻译）
TRANSLATION_CACHE = {}


# ── 数据库操作 ────────────────────────────────────────────────────
def save_english_news(news_item: Dict, db_path: str = DB_PATH) -> Tuple[bool, int]:
    """
    保存英文新闻到数据库
    
    参数:
    - news_item: {
        'title_en': '...',
        'content_en': '...',
        'title_cn': '...',  # 翻译后
        'content_cn': '...',  # 翻译后
        'url': '...',
        'publish_time': '...',
        'source_name': 'reuters',
        'original_url': '...',
    }
    
    返回: (success, news_raw_id)
    """
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        # 计算hash（基于标题+URL）
        import hashlib
        hash_str = hashlib.md5(
            (news_item.get('title_en', '') + news_item.get('url', '')).encode('utf-8')
        ).hexdigest()
        
        # 检查是否已存在
        cur.execute('SELECT id FROM news_raw WHERE hash = ?', (hash_str,))
        if cur.fetchone():
            logger.debug(f"英文新闻已存在: {news_item.get('title_en', '')[:30]}...")
            conn.close()
            return False, 0
            
        # 插入
        cur.execute('''
            INSERT INTO news_raw
            (source, source_id, title, content, url, 
             publish_time, crawl_time, raw_data, hash)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?)
        ''', (
            'english_news',
            news_item.get('source_name', 'unknown'),
            news_item.get('title_cn', news_item.get('title_en', '')),  # 使用中文标题
            news_item.get('content_cn', news_item.get('content_en', '')),  # 使用中文内容
            news_item.get('url', ''),
            news_item.get('publish_time', datetime.now().isoformat()),
            json.dumps(news_item, ensure_ascii=False),  # 原始数据
            hash_str,
        ))
        
        news_raw_id = cur.lastrowid
        conn.commit()
        
        logger.info(f"✅ 英文新闻已保存: {news_item.get('title_cn', '')[:30]}... (ID={news_raw_id})")
        
        return True, news_raw_id
        
    except Exception as e:
        logger.error(f"❌ 保存英文新闻失败: {e}")
        conn.rollback()
        return False, 0
        
    finally:
        conn.close()


def batch_save_english_news(news_list: List[Dict], db_path: str = DB_PATH) -> Tuple[int, int]:
    """批量保存英文新闻，返回(成功数, 新增数)"""
    success_count = 0
    new_count = 0
    
    for item in news_list:
        success, _ = save_english_news(item, db_path)
        if success:
            success_count += 1
            new_count += 1
            
    return success_count, new_count


# ── 翻译器 ─────────────────────────────────────────────────────
def translate_with_llm(text_en: str, text_type: str = 'title') -> str:
    """
    使用WorkBuddy内置LLM翻译英文→中文
    
    参数:
    - text_en: 英文文本
    - text_type: 'title' or 'content'
    
    返回: 中文翻译
    
    注意: 此函数需要在WorkBuddy自动化任务中调用，
    因为只有在自动化任务的prompt中才能访问LLM。
    
    在Python代码中，此函数会：
    1. 将待翻译文本写入临时文件
    2. 触发自动化任务（该任务会读取文件并调用LLM翻译）
    3. 等待翻译结果文件
    
    或者，可以直接在当前Python进程中通过
    WorkBuddy提供的Python SDK调用LLM（如果有的话）。
    """
    # TODO: 实现WorkBuddy LLM翻译接口
    
    # 检查缓存
    cache_key = f"{text_type}:{text_en[:50]}"
    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key]
    
    logger.warning("⚠️ LLM翻译接口尚未完全实现，返回原文")
    
    # 模拟翻译（实际应调用LLM）
    # 这里简单返回原文，实际应调用LLM
    translated = text_en  # TODO: 调用LLM翻译
    
    # 存入缓存
    TRANSLATION_CACHE[cache_key] = translated
    
    return translated


def translate_news_item(news_item: Dict) -> Dict:
    """
    翻译英文新闻条目（标题+内容）
    
    返回: 添加了title_cn和content_cn的字典
    """
    # 1. 翻译标题
    title_en = news_item.get('title_en', '')
    title_cn = translate_with_llm(title_en, text_type='title')
    
    # 2. 翻译内容（限制长度）
    content_en = news_item.get('content_en', '')[:2000]  # 限制长度
    content_cn = translate_with_llm(content_en, text_type='content')
    
    # 3. 更新字典
    news_item['title_cn'] = title_cn
    news_item['content_cn'] = content_cn
    
    logger.info(f"✅ 翻译完成: {title_en[:30]}... → {title_cn[:30]}...")
    
    return news_item


# ── 英文新闻采集器类 ─────────────────────────────────────────────────────
class EnglishNewsCollector:
    """英文财经新闻采集器（主类）"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info(f"✅ 英文新闻采集器初始化完成: {db_path}")
        
    def collect_all(self, keywords: List[str] = None, 
                     max_per_source: int = 10) -> Tuple[int, int]:
        """
        采集所有启用的英文数据源
        
        返回: (采集总数, 新增数量)
        """
        if keywords is None:
            keywords = EN_KEYWORDS[:10]
            
        total_collected = 0
        total_new = 0
        
        logger.info(f"🚀 开始采集英文新闻... 关键词数量={len(keywords)}")
        
        for source_key, source_info in EN_SOURCES.items():
            if not source_info.get('enabled', False):
                logger.debug(f"  跳过禁用源: {source_info['name']}")
                continue
                
            try:
                logger.info(f"  采集源: {source_info['name']}...")
                
                # 根据源类型调用不同的采集方法
                if source_key == 'reuters':
                    items = self._collect_reuters(keywords, max_per_source)
                elif source_key == 'bloomberg':
                    items = self._collect_bloomberg(keywords, max_per_source)
                elif source_key == 'ft':
                    items = self._collect_ft(keywords, max_per_source)
                elif source_key == 'cnbc':
                    items = self._collect_cnbc(keywords, max_per_source)
                elif source_key == 'marketwatch':
                    items = self._collect_marketwatch(keywords, max_per_source)
                else:
                    items = []
                    
                # 翻译并保存
                for item in items:
                    item = translate_news_item(item)
                    success, _ = save_english_news(item, self.db_path)
                    if success:
                        total_collected += 1
                        total_new += 1
                        
            except Exception as e:
                logger.error(f"❌ 采集失败 ({source_info['name']}): {e}")
                
        logger.info(f"✅ 英文新闻采集完成: 总计={total_collected} 新增={total_new}")
        
        return total_collected, total_new
    
    def _collect_reuters(self, keywords: List[str], 
                          max_items: int = 10) -> List[Dict]:
        """采集路透社新闻"""
        logger.info(f"  Reuters采集: 关键词={keywords[:3]}...")
        
        # TODO: 实现Reuters RSS解析或WebSearch调用
        # 示例: 使用WebSearch(query="Reuters [keyword] China market", topic="finance")
        
        return []
    
    def _collect_bloomberg(self, keywords: List[str], 
                            max_items: int = 10) -> List[Dict]:
        """采集彭博新闻"""
        logger.info(f"  Bloomberg采集: 关键词={keywords[:3]}...")
        
        # TODO: 实现Bloomberg采集
        
        return []
    
    def _collect_ft(self, keywords: List[str], 
                     max_items: int = 10) -> List[Dict]:
        """采集金融时报新闻"""
        logger.info(f"  FT采集: 关键词={keywords[:3]}...")
        
        # TODO: 实现FT采集
        
        return []
    
    def _collect_cnbc(self, keywords: List[str], 
                       max_items: int = 10) -> List[Dict]:
        """采集CNBC新闻"""
        logger.info(f"  CNBC采集: 关键词={keywords[:3]}...")
        
        # TODO: 实现CNBC RSS解析
        
        return []
    
    def _collect_marketwatch(self, keywords: List[str], 
                             max_items: int = 10) -> List[Dict]:
        """采集MarketWatch新闻"""
        logger.info(f"  MarketWatch采集: 关键词={keywords[:3]}...")
        
        # TODO: 实现MarketWatch采集
        
        return []
    
    def collect_via_websearch(self, query: str, 
                               max_results: int = 10) -> List[Dict]:
        """
        通过WebSearch采集英文新闻
        
        返回: 英文新闻条目列表（未翻译）
        
        注意: 此函数需要在WorkBuddy自动化任务中调用WebSearch工具
        """
        logger.info(f"  WebSearch采集: query={query[:50]}...")
        
        # TODO: 调用WebSearch工具
        # 示例: WebSearch(query=query, topic="finance")
        
        return []
    
    def get_latest_english_news(self, limit: int = 20) -> List[Dict]:
        """获取最新的英文来源新闻（已翻译）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        since = (datetime.now() - timedelta(hours=24)).isoformat()
        
        cur.execute('''
            SELECT * FROM news_raw
            WHERE source = 'english_news'
            AND crawl_time >= ?
            ORDER BY publish_time DESC
            LIMIT ?
        ''', (since, limit))
        
        rows = cur.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]


# ── 命令行接口 ─────────────────────────────────────────────────────
def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='V13.2 英文财经新闻采集器')
    parser.add_argument('--collect-all', action='store_true', help='采集所有英文源')
    parser.add_argument('--keywords', type=str, nargs='+', help='英文关键词列表')
    parser.add_argument('--max-per-source', type=int, default=10, help='每源最大采集数')
    parser.add_argument('--latest', type=int, default=20, help='显示最新的英文新闻')
    parser.add_argument('--db-path', type=str, default=DB_PATH, help='数据库路径')
    
    args = parser.parse_args()
    
    # 初始化采集器
    collector = EnglishNewsCollector(db_path=args.db_path)
    
    if args.collect_all:
        # 采集所有
        keywords = args.keywords if args.keywords else None
        collected, new = collector.collect_all(
            keywords=keywords,
            max_per_source=args.max_per_source
        )
        
        print(f"\n{'=' * 70}")
        print(f"  英文新闻采集报告")
        print(f"{'=' * 70}")
        print(f"  采集总数: {collected} 条")
        print(f"  新增数量: {new} 条")
        print(f"{'=' * 70}\n")
        
    elif args.latest:
        # 显示最新
        latest = collector.get_latest_english_news(limit=args.latest)
        
        print(f"\n{'=' * 70}")
        print(f"  最新的 {len(latest)} 条英文新闻")
        print(f"{'=' * 70}")
        
        for i, news in enumerate(latest, 1):
            print(f"\n{i}. {news.get('title', '')[:50]}...")
            print(f"   来源: {news.get('source_id', '?')}")
            print(f"   发布: {news.get('publish_time', '?')}")
            print(f"   URL: {news.get('url', '?')[:60]}...")
            
        print(f"\n{'=' * 70}\n")
        
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
