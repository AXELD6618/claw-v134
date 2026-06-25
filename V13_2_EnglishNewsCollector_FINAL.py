#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 英文财经新闻采集器 (English News Collector) - 修正版
支持Reuters/CNBC/FT/Bloomberg等英文财经媒体
使用Python标准库实现，无需安装feedparser

功能:
1. 采集英文RSS新闻
2. 英文→中文翻译（规则词典 + LLM备用）
3. 存储到sentiment_db.db
4. 与中文舆情统一分析

数据源:
- CNBC: https://www.cnbc.com/id/100003114/device/rss/rss.html (✅ 可用)
- AI Model News: https://www.anthropic.com/news/rss.xml (✅ 备用)
- Google News: https://news.google.com/rss/search?q=finance&hl=en-US&gl=US&ceid=US:en (✅ 备用)
- Financial Content: https://www.financialcontent.com/rss/stock.xml (✅ 备用)

翻译方案:
- 优先: 规则词典（50+金融术语）
- 备用: LLM翻译（TODO: 通过自动化任务调用）

版本: V13.2 FIXED
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
import requests
import xml.etree.ElementTree as ET

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/english_news.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 英文RSS源配置（已测试可用）
ENGLISH_RSS_FEEDS = {
    'cnbc': {
        'url': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
        'enabled': True,
        'priority': 'high',
    },
    'google_news_finance': {
        'url': 'https://news.google.com/rss/search?q=finance+stock+market&hl=en-US&gl=US&ceid=US:en',
        'enabled': True,
        'priority': 'high',
    },
    'yahoo_finance': {
        'url': 'https://finance.yahoo.com/news/rssindex',
        'enabled': True,
        'priority': 'medium',
    },
    'investing_com': {
        'url': 'https://www.investing.com/rss/news.rss',
        'enabled': True,
        'priority': 'medium',
    },
}

# 金融术语词典（英文→中文）
FINANCIAL_TERMS = {
    # 央行与政策
    'Fed': '美联储',
    'Federal Reserve': '美联储',
    'ECB': '欧洲央行',
    'Bank of Japan': '日本央行',
    'PBOC': '中国人民银行',
    'rate cut': '降息',
    'rate hike': '加息',
    'interest rate': '利率',
    'monetary policy': '货币政策',
    'quantitative easing': '量化宽松',
    'QT': '量化紧缩',
    
    # 市场情绪
    'bullish': '看涨',
    'bearish': '看跌',
    'rally': '反弹',
    'sell-off': '抛售',
    'correction': '回调',
    'crash': '崩盘',
    'volatile': '波动',
    'momentum': '动能',
    
    # 经济指标
    'recession': '衰退',
    'inflation': '通胀',
    'deflation': '通缩',
    'GDP': '国内生产总值',
    'unemployment': '失业',
    'CPI': '消费者价格指数',
    'PPI': '生产者价格指数',
    
    # 资产类别
    'stock': '股票',
    'bond': '债券',
    'commodity': '大宗商品',
    'oil': '石油',
    'gold': '黄金',
    'forex': '外汇',
    'crypto': '加密货币',
    'bitcoin': '比特币',
    
    # 行业
    'technology': '科技',
    'tech': '科技',
    'AI': '人工智能',
    'artificial intelligence': '人工智能',
    'semiconductor': '半导体',
    'healthcare': '医疗',
    'finance': '金融',
    'energy': '能源',
    'manufacturing': '制造业',
    
    # 公司行为
    'earnings': '财报',
    'revenue': '营收',
    'profit': '利润',
    'loss': '亏损',
    'IPO': '首次公开募股',
    'merger': '合并',
    'acquisition': '收购',
    'buyback': '回购',
    'dividend': '分红',
    
    # 地理
    'US': '美国',
    'China': '中国',
    'Europe': '欧洲',
    'Japan': '日本',
    'emerging market': '新兴市场',
}

class EnglishNewsCollectorFixed:
    """英文财经新闻采集器（修正版）"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        self.init_db()
        logger.info("✅ 英文新闻采集器（修正版）初始化完成")
    
    def init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建英文新闻表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS english_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            title_zh TEXT,
            content TEXT,
            content_zh TEXT,
            source TEXT NOT NULL,
            url TEXT UNIQUE,
            publish_time TEXT,
            collect_time TEXT DEFAULT CURRENT_TIMESTAMP,
            title_hash TEXT UNIQUE,
            content_hash TEXT,
            sentiment_score REAL DEFAULT 0,
            importance INTEGER DEFAULT 50,
            related_symbols TEXT,
            translated INTEGER DEFAULT 0,
            llm_translated INTEGER DEFAULT 0,
            raw_data TEXT
        )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ 英文新闻表初始化完成")
    
    def collect_cnbc(self) -> Tuple[int, int]:
        """
        采集CNBC新闻（已测试可用）
        
        返回: (采集数, 新增数)
        """
        logger.info("📰 开始采集CNBC新闻...")
        
        url = ENGLISH_RSS_FEEDS['cnbc']['url']
        
        try:
            # 发送HTTP请求
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # 解析XML
            root = ET.fromstring(response.content)
            
            # 查找所有<item>标签
            items = root.findall('.//item')
            
            collected = 0
            new_count = 0
            
            for item in items:
                try:
                    # 提取标题
                    title_elem = item.find('title')
                    title = title_elem.text if title_elem is not None else ''
                    
                    # 提取链接
                    link_elem = item.find('link')
                    link = link_elem.text if link_elem is not None else ''
                    
                    # 提取发布时间
                    pub_date_elem = item.find('pubDate')
                    pub_date = pub_date_elem.text if pub_date_elem is not None else ''
                    
                    # 提取描述
                    desc_elem = item.find('description')
                    description = desc_elem.text if desc_elem is not None else ''
                    
                    # 计算哈希
                    title_hash = hashlib.md5(title.encode('utf-8')).hexdigest()
                    
                    # 存储到数据库
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    
                    try:
                        cursor.execute("""
                        INSERT INTO english_news 
                        (title, content, source, url, publish_time, title_hash, raw_data)
                        VALUES (?, ?, 'cnbc', ?, ?, ?, ?)
                        """, (title, description, link, pub_date, title_hash, json.dumps({
                            'title': title,
                            'link': link,
                            'pub_date': pub_date,
                            'description': description,
                        })))
                        
                        conn.commit()
                        new_count += 1
                        logger.debug(f"✅ 新增CNBC新闻: {title[:50]}...")
                        
                    except sqlite3.IntegrityError:
                        # 已存在，跳过
                        pass
                    finally:
                        conn.close()
                    
                    collected += 1
                    
                except Exception as e:
                    logger.warning(f"⚠️  解析CNBC文章失败: {e}")
                    continue
            
            logger.info(f"✅ CNBC采集完成: 采集{collected}条，新增{new_count}条")
            return collected, new_count
            
        except Exception as e:
            logger.error(f"❌ CNBC采集失败: {e}")
            return 0, 0
    
    def collect_google_news(self, query: str = 'finance') -> Tuple[int, int]:
        """
        采集Google News新闻
        
        参数:
            query: 搜索关键词
        
        返回: (采集数, 新增数)
        """
        logger.info(f"📰 开始采集Google News新闻 (query={query})...")
        
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            root = ET.fromstring(response.content)
            items = root.findall('.//item')
            
            collected = 0
            new_count = 0
            
            for item in items:
                try:
                    title_elem = item.find('title')
                    title = title_elem.text if title_elem is not None else ''
                    
                    link_elem = item.find('link')
                    link = link_elem.text if link_elem is not None else ''
                    
                    pub_date_elem = item.find('pubDate')
                    pub_date = pub_date_elem.text if pub_date_elem is not None else ''
                    
                    desc_elem = item.find('description')
                    description = desc_elem.text if desc_elem is not None else ''
                    
                    title_hash = hashlib.md5(title.encode('utf-8')).hexdigest()
                    
                    conn = sqlite3.connect(self.db_path)
                    cursor = conn.cursor()
                    
                    try:
                        cursor.execute("""
                        INSERT INTO english_news 
                        (title, content, source, url, publish_time, title_hash, raw_data)
                        VALUES (?, ?, 'google_news', ?, ?, ?, ?)
                        """, (title, description, link, pub_date, title_hash, json.dumps({
                            'title': title,
                            'link': link,
                            'pub_date': pub_date,
                            'description': description,
                            'query': query,
                        })))
                        
                        conn.commit()
                        new_count += 1
                        
                    except sqlite3.IntegrityError:
                        pass
                    finally:
                        conn.close()
                    
                    collected += 1
                    
                except Exception as e:
                    logger.warning(f"⚠️  解析Google News文章失败: {e}")
                    continue
            
            logger.info(f"✅ Google News采集完成: 采集{collected}条，新增{new_count}条")
            return collected, new_count
            
        except Exception as e:
            logger.error(f"❌ Google News采集失败: {e}")
            return 0, 0
    
    def rule_translate(self, text: str) -> str:
        """
        规则翻译（英文→中文）
        使用金融术语词典进行关键词替换
        
        参数:
            text: 英文文本
        
        返回:
            翻译后的文本（部分翻译）
        """
        if not text:
            return ''
        
        # 替换金融术语
        translated = text
        for en, zh in FINANCIAL_TERMS.items():
            # 不区分大小写替换
            import re
            pattern = re.compile(re.escape(en), re.IGNORECASE)
            translated = pattern.sub(zh, translated)
        
        return translated
    
    def batch_translate(self, limit: int = 20):
        """
        批量翻译未翻译的英文新闻
        
        参数:
            limit: 每次翻译的最大数量
        """
        logger.info(f"🌐 开始批量翻译英文新闻 (limit={limit})...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 查询未翻译的新闻
        cursor.execute("""
        SELECT id, title, content 
        FROM english_news 
        WHERE translated = 0 
        LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        
        translated_count = 0
        
        for row in rows:
            news_id, title, content = row
            
            # 规则翻译
            title_zh = self.rule_translate(title)
            content_zh = self.rule_translate(content) if content else ''
            
            # 更新数据库
            cursor.execute("""
            UPDATE english_news 
            SET title_zh = ?, content_zh = ?, translated = 1
            WHERE id = ?
            """, (title_zh, content_zh, news_id))
            
            translated_count += 1
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ 批量翻译完成: 翻译{translated_count}条新闻")
        return translated_count
    
    def collect_all(self) -> Tuple[int, int]:
        """
        采集所有可用的英文新闻源
        
        返回: (总采集数, 总新增数)
        """
        logger.info("🚀 开始采集所有英文新闻源...")
        
        total_collected = 0
        total_new = 0
        
        # 采集CNBC
        if ENGLISH_RSS_FEEDS['cnbc']['enabled']:
            c, n = self.collect_cnbc()
            total_collected += c
            total_new += n
        
        # 采集Google News
        if ENGLISH_RSS_FEEDS['google_news_finance']['enabled']:
            c, n = self.collect_google_news('finance OR stock OR market')
            total_collected += c
            total_new += n
        
        # 批量翻译
        self.batch_translate()
        
        # V13.2 KP: Feed into KnowledgePipeline
        if total_new > 0:
            try:
                self._feed_knowledge_pipeline()
            except Exception as e:
                logger.warning(f"知识管道接入失败: {e}")
        
        logger.info(f"🎉 所有英文新闻源采集完成: 总采集{total_collected}条，总新增{total_new}条")
        return total_collected, total_new
    
    def _feed_knowledge_pipeline(self):
        """将新增的英文新闻送入知识管道"""
        try:
            from V13_2_KnowledgePipeline import KnowledgePipeline
            kp = KnowledgePipeline()
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, title, content_zh, summary, source, url, publish_time
                FROM english_news WHERE processed_kp = 0 OR processed_kp IS NULL
                LIMIT 50
            """)
            rows = cursor.fetchall()
            conn.close()
            
            if rows:
                items = []
                for row in rows:
                    items.append({
                        'title': row[1] or '',
                        'content': row[3] or row[2] or '',
                        'summary': row[3] or '',
                        'url': row[5] or '',
                        'published_at': row[6] or '',
                        'type': 'news',
                    })
                
                count = kp.ingest_english_news(items)
                logger.info(f"知识管道: 英文新闻接入 {count} 条")
                
                # 标记已处理
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE english_news SET processed_kp = 1
                    WHERE processed_kp = 0 OR processed_kp IS NULL
                """)
                conn.commit()
                conn.close()
                
                # 运行分析
                kp.analyze_pending_items(limit=50)
                kp.generate_daily_summary()
        except Exception as e:
            logger.debug(f"知识管道接入跳过（可能尚未初始化）: {e}")


def test_english_news_collector():
    """测试英文新闻采集器"""
    print("=" * 60)
    print("测试英文新闻采集器（修正版）")
    print("=" * 60)
    
    # 创建日志目录
    os.makedirs('logs', exist_ok=True)
    
    # 初始化采集器
    collector = EnglishNewsCollectorFixed()
    
    # 采集所有英文新闻
    collected, new = collector.collect_all()
    
    print(f"\n✅ 测试完成: 采集{collected}条，新增{new}条")
    print("=" * 60)
    
    return collected, new


if __name__ == '__main__':
    test_english_news_collector()
