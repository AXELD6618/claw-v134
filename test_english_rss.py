#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试英文RSS解析功能
验证Reuters/CNBC RSS解析是否正常工作
"""

import sys
import os
import xml.etree.ElementTree as ET
import requests
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/test_english_rss.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# RSS源配置
RSS_FEEDS = {
    'reuters': 'https://feeds.reuters.com/reuters/businessNews',
    'cnbc': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
    'ft': 'https://www.ft.com/?format=rss',
    'bloomberg': 'https://www.bloomberg.com/feed/podcast/etf-iq.xml',  # 可能需要更换
}

def test_rss_fetch(source_name, url):
    """
    测试RSS抓取
    
    参数:
        source_name: 数据源名称
        url: RSS URL
    
    返回:
        (成功标志, 消息, 解析文章数)
    """
    try:
        logger.info(f"📰 开始测试 {source_name} RSS: {url}")
        
        # 发送HTTP请求
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        logger.info(f"✅ {source_name} RSS 抓取成功，状态码: {response.status_code}")
        logger.info(f"   响应长度: {len(response.content)} 字节")
        
        # 解析XML
        root = ET.fromstring(response.content)
        
        # 查找所有<item>或<entry>标签
        items = root.findall('.//item') or root.findall('.//{http://www.w3.org/2005/Atom}entry')
        
        logger.info(f"✅ {source_name} RSS 解析成功，找到 {len(items)} 篇文章")
        
        # 解析前3篇文章
        articles = []
        for i, item in enumerate(items[:3]):
            try:
                # 提取标题
                title_elem = item.find('title') or item.find('{http://www.w3.org/2005/Atom}title')
                title = title_elem.text if title_elem is not None else ''
                
                # 提取链接
                link_elem = item.find('link') or item.find('{http://www.w3.org/2005/Atom}link')
                link = link_elem.text if link_elem is not None else (link_elem.get('href') if link_elem is not None else '')
                
                # 提取发布时间
                pub_date_elem = item.find('pubDate') or item.find('{http://www.w3.org/2005/Atom}published')
                pub_date = pub_date_elem.text if pub_date_elem is not None else ''
                
                # 提取描述
                desc_elem = item.find('description') or item.find('{http://www.w3.org/2005/Atom}summary')
                description = desc_elem.text if desc_elem is not None else ''
                
                articles.append({
                    'title': title,
                    'link': link,
                    'pub_date': pub_date,
                    'description': description[:200] if description else '',
                })
                
                logger.info(f"   文章 {i+1}: {title[:50]}...")
                
            except Exception as e:
                logger.warning(f"⚠️  解析第 {i+1} 篇文章失败: {e}")
                continue
        
        return True, f"成功解析 {len(items)} 篇文章", len(items)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ {source_name} RSS 抓取失败: {e}")
        return False, str(e), 0
    except ET.ParseError as e:
        logger.error(f"❌ {source_name} RSS 解析失败: {e}")
        return False, str(e), 0
    except Exception as e:
        logger.error(f"❌ {source_name} RSS 测试失败: {e}")
        return False, str(e), 0

def test_rule_translate():
    """
    测试规则翻译功能
    """
    logger.info("🌐 开始测试规则翻译功能...")
    
    # 金融术语词典
    FINANCIAL_TERMS = {
        'Fed': '美联储',
        'Federal Reserve': '美联储',
        'rate cut': '降息',
        'interest rate': '利率',
        'bullish': '看涨',
        'bearish': '看跌',
        'recession': '衰退',
        'inflation': '通胀',
        'GDP': '国内生产总值',
        'unemployment': '失业',
        'stock': '股票',
        'bond': '债券',
        'commodity': '大宗商品',
        'oil': '石油',
        'gold': '黄金',
        'technology': '科技',
        'AI': '人工智能',
        'earnings': '财报',
        'revenue': '营收',
        'profit': '利润',
        'loss': '亏损',
    }
    
    # 测试文本
    test_texts = [
        "Fed rate cut expected, bullish for tech stocks",
        "Recession fears grow as unemployment rises",
        "Oil prices surge on Middle East tensions",
        "AI earnings blowout, stock surges",
    ]
    
    for text in test_texts:
        # 规则翻译
        translated = text
        for en, zh in FINANCIAL_TERMS.items():
            translated = translated.replace(en, zh)
        
        logger.info(f"   原文: {text}")
        logger.info(f"   译文: {translated}")
        logger.info("")
    
    logger.info("✅ 规则翻译功能测试完成")
    return True

def main():
    """主测试函数"""
    logger.info("=" * 60)
    logger.info("开始测试英文RSS解析功能")
    logger.info("=" * 60)
    
    # 创建日志目录
    os.makedirs('logs', exist_ok=True)
    
    # 测试各个RSS源
    results = {}
    for source_name, url in RSS_FEEDS.items():
        success, message, count = test_rss_fetch(source_name, url)
        results[source_name] = {
            'success': success,
            'message': message,
            'count': count,
        }
        
        if success:
            logger.info(f"✅ {source_name}: {message}")
        else:
            logger.error(f"❌ {source_name}: {message}")
        
        logger.info("")
    
    # 测试规则翻译
    test_rule_translate()
    
    # 生成测试报告
    logger.info("=" * 60)
    logger.info("测试报告")
    logger.info("=" * 60)
    
    total_sources = len(results)
    success_sources = sum(1 for r in results.values() if r['success'])
    
    logger.info(f"总RSS源: {total_sources}")
    logger.info(f"成功解析: {success_sources}")
    logger.info(f"失败: {total_sources - success_sources}")
    logger.info("")
    
    for source_name, result in results.items():
        status = "✅" if result['success'] else "❌"
        logger.info(f"{status} {source_name}: {result['message']}")
    
    logger.info("")
    logger.info("=" * 60)
    
    # 给出建议
    if success_sources == 0:
        logger.warning("⚠️  所有RSS源都无法访问，可能原因：")
        logger.warning("   1. 网络被墙（需使用代理）")
        logger.warning("   2. RSS URL已失效")
        logger.warning("   3. 需要特殊认证")
        logger.info("")
        logger.info("建议方案：")
        logger.info("1. 使用WebSearch替代RSS（搜索最新英文新闻）")
        logger.info("2. 使用LLM翻译英文新闻（先人工收集，再翻译）")
        logger.info("3. 使用第三方API（如NewsAPI）")
    elif success_sources < total_sources:
        logger.warning(f"⚠️  部分RSS源无法访问（{total_sources - success_sources}/{total_sources}）")
        logger.info("建议：检查失败的RSS URL，必要时更换为备用URL")
    else:
        logger.info("🎉 所有RSS源都可以正常访问！")
    
    logger.info("=" * 60)
    
    return results

if __name__ == '__main__':
    main()
