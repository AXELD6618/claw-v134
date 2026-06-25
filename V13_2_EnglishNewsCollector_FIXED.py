#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 英文财经新闻采集器 (English News Collector) - 修正版
实现RSS解析（标准库xml）+ 规则翻译

功能:
1. 采集英文财经新闻（Reuters/Bloomberg/FT/CNBC等）
2. 英文→中文翻译（规则词典 + LLM备用）
3. 存储到sentiments_db.db
4. 与中文舆情统一分析

RSS解析: 使用Python标准库 xml.etree.ElementTree（无需安装feedparser）
规则翻译: 金融术语词典映射（作为LLM翻译的备用方案）

版本: V13.2-FIXED
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
from dataclasses import dataclass

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/english_news_fixed.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'

# 英文数据源RSS URL
EN_RSS_SOURCES = {
    'reuters': {
        'name': 'Reuters',
        'rss': 'https://www.reuters.com/rssfeed',
        'enabled': True,
    },
    'cnbc': {
        'name': 'CNBC',
        'rss': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
        'enabled': True,
    },
    'bloomberg': {
        'name': 'Bloomberg',
        'rss': 'https://www.bloomberg.com/feed/podcast/bloomberg-best',
        'enabled': False,  # 需要解析网页
    },
}

# 金融术语翻译词典（规则翻译用）
FINANCIAL_TERMS_DICT = {
    # 市场术语
    'bullish': '看涨',
    'bearish': '看跌',
    'neutral': '中性',
    'volatile': '波动',
    'surge': '飙升',
    'plunge': '暴跌',
    'rally': '反弹',
    'correction': '回调',
    'recovery': '复苏',
    'recession': '衰退',
    
    # 政策术语
    'Fed': '美联储',
    'rate cut': '降息',
    'rate hike': '加息',
    'QE': '量化宽松',
    'fiscal stimulus': '财政刺激',
    'monetary policy': '货币政策',
    'PBOC': '中国人民银行',
    'ECB': '欧洲央行',
    
    # 行业术语
    'semiconductor': '半导体',
    'chip': '芯片',
    'GPU': '图形处理器',
    'AI': '人工智能',
    'datacenter': '数据中心',
    'lithium': '锂',
    'photovoltaic': '光伏',
    'wind power': '风电',
    'hydrogen': '氢能',
    'electric vehicle': '电动汽车',
    'EV': '电动汽车',
    
    # 财报术语
    'earnings': '财报',
    'profit': '利润',
    'revenue': '营收',
    'guidance': '指引',
    'beat': '超预期',
    'miss': '不及预期',
    'outlook': '展望',
    
    # 大宗商品
    'crude oil': '原油',
    'copper': '铜',
    'gold': '黄金',
    'iron ore': '铁矿石',
    'natural gas': '天然气',
    
    # 地缘政治
    'Hormuz': '霍尔木兹',
    'strait': '海峡',
    'Iran': '伊朗',
    'sanctions': '制裁',
    'tariff': '关税',
    'trade war': '贸易战',
}

# 翻译缓存
TRANSLATION_CACHE = {}


# ── 规则翻译器 ───────────────────────────────────────────────
def rule_translate(text_en: str) -> str:
    """
    基于词典的规则翻译（英文→中文）
    
    参数:
    - text_en: 英文文本
    
    返回: 中文翻译（基于词典替换）
    
    注意: 这不是真正的翻译，只是关键词替换。
          用于LLM翻译不可用时的备用方案。
    """
    if not text_en:
        return ''
    
    # 检查缓存
    cache_key = text_en[:50]
    if cache_key in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[cache_key]
    
    text_cn = text_en
    
    # 替换金融术语
    for en_term, cn_term in FINANCIAL_TERMS_DICT.items():
        # 不区分大小写替换
        import re
        pattern = re.compile(re.escape(en_term), re.IGNORECASE)
        text_cn = pattern.sub(cn_term, text_cn)
    
    # 存入缓存
    TRANSLATION_CACHE[cache_key] = text_cn
    
    return text_cn


# ── RSS解析器 ────────────────────────────────────────────────
def parse_rss(url: str, source_name: str, max_items: int = 10) -> List[Dict]:
    """
    解析RSS Feed，提取新闻条目
    
    参数:
    - url: RSS URL
    - source_name: 数据源名称
    - max_items: 最大条目数
    
    返回: 新闻条目列表 [{'title_en': ..., 'content_en': ..., 'url': ...}]
    """
    import xml.etree.ElementTree as ET
    from urllib.request import urlopen, Request
    from urllib.error import URLError
    
    items = []
    
    try:
        # 1. 获取RSS Feed
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=10) as response:
            rss_data = response.read()
        
        # 2. 解析XML
        root = ET.fromstring(rss_data)
        
        # 3. 提取新闻条目（RSS 2.0格式: <item>）
        xml_items = root.findall('.//item')
        
        logger.info(f"   {source_name} RSS解析到 {len(xml_items)} 条新闻")
        
        # 4. 处理每条新闻
        for idx, item in enumerate(xml_items[:max_items]):
            try:
                title_en = item.find('title').text if item.find('title') is not None else ''
                link = item.find('link').text if item.find('link') is not None else ''
                description = item.find('description').text if item.find('description') is not None else ''
                pub_date = item.find('pubDate').text if item.find('pubDate') is not None else ''
                
                # 规则翻译
                title_cn = rule_translate(title_en)
                content_cn = rule_translate(description[:500]) if description else ''
                
                # 构造新闻项
                news_item = {
                    'title_en': title_en,
                    'content_en': description or '',
                    'title_cn': title_cn,
                    'content_cn': content_cn,
                    'url': link or '',
                    'publish_time': pub_date or datetime.now().isoformat(),
                    'source_name': source_name,
                }
                
                items.append(news_item)
                
            except Exception as e:
                logger.error(f"❌ 处理{source_name}新闻条目失败: {e}")
                continue
        
    except URLError as e:
        logger.error(f"❌ {source_name} RSS获取失败（网络错误）: {e}")
    except ET.ParseError as e:
        logger.error(f"❌ {source_name} RSS解析失败（XML格式错误）: {e}")
    except Exception as e:
        logger.error(f"❌ {source_name} RSS解析失败: {e}")
    
    return items


# ── 数据库操作 ────────────────────────────────────────────────
def save_english_news(news_item: Dict, db_path: str = DB_PATH) -> Tuple[bool, int]:
    """保存英文新闻到数据库"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        # 计算hash
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
            news_item.get('title_cn', news_item.get('title_en', '')),
            news_item.get('content_cn', news_item.get('content_en', '')),
            news_item.get('url', ''),
            news_item.get('publish_time', datetime.now().isoformat()),
            json.dumps(news_item, ensure_ascii=False),
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


# ── 主采集器 ────────────────────────────────────────────────
def collect_all_english_news() -> Tuple[int, int]:
    """
    采集所有英文数据源的新闻
    
    返回: (总采集数, 总新增数)
    """
    total_collected = 0
    total_new = 0
    
    for source_id, source_config in EN_RSS_SOURCES.items():
        if not source_config.get('enabled', False):
            continue
            
        rss_url = source_config.get('rss', '')
        if not rss_url:
            logger.warning(f"⚠️ {source_id} 未配置RSS URL，跳过")
            continue
        
        logger.info(f"📰 开始采集 {source_config['name']} 新闻...")
        
        # 解析RSS
        items = parse_rss(rss_url, source_id, max_items=10)
        
        # 保存
        for item in items:
            success, _ = save_english_news(item)
            if success:
                total_new += 1
            total_collected += 1
        
        logger.info(f"✅ {source_config['name']} 采集完成: 采集={len(items)} 新增={sum(1 for i in items if i)}")
    
    logger.info(f"✅ 英文新闻采集全部完成: 总采集={total_collected} 总新增={total_new}")
    
    return total_collected, total_new


# ── 测试函数 ────────────────────────────────────────────────
def test_rule_translate():
    """测试规则翻译"""
    print("🧪 测试规则翻译...")
    
    test_text = "Fed rate cut expected, bullish for semiconductor stocks. AI and GPU demand surge."
    translated = rule_translate(test_text)
    
    print(f"  原文: {test_text}")
    print(f"  译文: {translated}")
    print(f"  包含中文: {'看涨' in translated and '美联储' in translated}")
    
    return translated


def test_rss_parse():
    """测试RSS解析（使用Reuters RSS）"""
    print("🧪 测试RSS解析...")
    
    items = parse_rss('https://www.reuters.com/rssfeed', 'reuters', max_items=3)
    
    print(f"  解析到 {len(items)} 条新闻")
    for i, item in enumerate(items[:3]):
        print(f"  [{i+1}] {item['title_cn'][:40]}...")
    
    return items


if __name__ == '__main__':
    print("🚀 V13.2 英文财经新闻采集器 (修正版) 测试")
    print("="*60)
    
    # 测试规则翻译
    test_rule_translate()
    print()
    
    # 测试RSS解析
    test_rss_parse()
    print()
    
    # 测试完整采集
    print("🚀 开始完整采集...")
    collect_all_english_news()
