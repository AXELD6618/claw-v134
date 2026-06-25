#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 公众号文章链接解析器 (MP Article Parser)
解析微信文件传输助手中的公众号文章链接

功能:
1. 识别公众号文章链接（mp.weixin.qq.com）
2. 使用WebFetch/requests获取文章内容
3. 解析文章标题/作者/发布时间/正文
4. 提取文章中的股票代码/板块名称/投资观点
5. 保存到舆情数据库
6. 触发舆情分析

技术路线:
- 链接识别：正则表达式
- 内容获取：WebFetch（优先）/ requests（备用）
- 内容解析：BeautifulSoup（HTML解析）
- 兜底：仅保存链接和标题

版本: V13.2
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import json
import re
import requests
from bs4 import BeautifulSoup

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/mp_article_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('MPArticleParser')

class MPArticleParser:
    """公众号文章链接解析器"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        
        # 解析器配置
        self.use_webfetch = True  # 是否使用WebFetch（优先）
        self.use_requests = True  # 是否使用requests（备用）
        self.timeout = 10  # 请求超时时间（秒）
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        
        logger.info("✅ 公众号文章链接解析器初始化完成")
    
    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建公众号文章表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mp_articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    title TEXT,
                    author TEXT,
                    publish_time TEXT,
                    content TEXT,
                    extracted_stocks TEXT,  -- JSON array
                    extracted_keywords TEXT,  -- JSON array
                    sentiment_score REAL,
                    importance INTEGER,
                    processed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()
            conn.close()
            logger.info("✅ 数据库表初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 数据库初始化失败: {e}")
            raise
    
    def parse_link(self, url: str) -> Dict:
        """
        解析公众号文章链接
        
        参数:
            url: 公众号文章链接
            
        返回:
            Dict: {
                'title': str,  # 文章标题
                'author': str,  # 作者/公众号名称
                'publish_time': str,  # 发布时间
                'content': str,  # 文章正文
                'extracted_stocks': List[str],  # 提取的股票代码
                'extracted_keywords': List[str],  # 提取的关键词
                'sentiment_score': float,  # 情感评分
                'importance': int,  # 重要性（1-100）
            }
        """
        logger.info(f"🔗 开始解析公众号文章: {url}")
        
        if not self._is_mp_article_url(url):
            logger.warning(f"⚠️ 不是公众号文章链接: {url}")
            return {
                'title': '',
                'author': '',
                'publish_time': '',
                'content': '',
                'extracted_stocks': [],
                'extracted_keywords': [],
                'sentiment_score': 0.0,
                'importance': 0,
            }
        
        # 获取文章内容
        html_content = self._fetch_article_content(url)
        
        if not html_content:
            logger.error(f"❌ 获取文章内容失败: {url}")
            return {
                'title': '',
                'author': '',
                'publish_time': '',
                'content': '',
                'extracted_stocks': [],
                'extracted_keywords': [],
                'sentiment_score': 0.0,
                'importance': 0,
            }
        
        # 解析HTML内容
        result = self._parse_html_content(html_content, url)
        
        # 保存到数据库
        self._save_article(url, result)
        
        logger.info(f"✅ 文章解析完成: {result['title'][:50]}")
        return result
    
    def _is_mp_article_url(self, url: str) -> bool:
        """判断是否为公众号文章链接"""
        pattern = r'https?://mp\.weixin\.qq\.com/s\?'
        return re.match(pattern, url) is not None
    
    def _fetch_article_content(self, url: str) -> Optional[str]:
        """
        获取文章内容（HTML）
        
        优先使用WebFetch，备用requests
        """
        logger.info("📡 获取文章内容...")
        
        # 优先：WebFetch（需要通过自动化任务调用）
        if self.use_webfetch:
            logger.info("🌐 使用WebFetch获取内容...")
            # TODO: 通过自动化任务调用WebFetch
            # 当前：返回None，使用requests备用
            logger.warning("⚠️ WebFetch需要通过自动化任务调用，使用requests备用")
        
        # 备用：requests
        if self.use_requests:
            logger.info("🔧 使用requests获取内容...")
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = requests.get(url, headers=headers, timeout=self.timeout)
                response.encoding = 'utf-8'
                
                if response.status_code == 200:
                    logger.info(f"✅ 文章内容获取成功: {len(response.text)} 字符")
                    return response.text
                else:
                    logger.error(f"❌ 获取文章内容失败: HTTP {response.status_code}")
                    return None
                    
            except Exception as e:
                logger.error(f"❌ 获取文章内容失败: {e}")
                return None
        
        return None
    
    def _parse_html_content(self, html_content: str, url: str) -> Dict:
        """解析HTML内容，提取文章信息"""
        logger.info("📝 解析HTML内容...")
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 提取标题
            title = ""
            title_tag = soup.find('h1', class_='rich_media_title')
            if title_tag:
                title = title_tag.get_text(strip=True)
            
            # 提取作者/公众号名称
            author = ""
            author_tag = soup.find('a', class_='rich_media_meta_link')
            if author_tag:
                author = author_tag.get_text(strip=True)
            
            # 提取发布时间
            publish_time = ""
            time_tag = soup.find('em', id='publish_time')
            if time_tag:
                publish_time = time_tag.get_text(strip=True)
            
            # 提取正文
            content = ""
            content_tag = soup.find('div', class_='rich_media_content')
            if content_tag:
                # 去除HTML标签，保留文字
                content = content_tag.get_text(separator='\n', strip=True)
            
            # 提取股票代码和关键词
            extracted_stocks = self._extract_stock_codes(content)
            extracted_keywords = self._extract_keywords(content)
            
            # 计算情感评分和重要性
            sentiment_score = self._calculate_sentiment_score(content)
            importance = self._calculate_importance(content, author)
            
            result = {
                'title': title,
                'author': author,
                'publish_time': publish_time,
                'content': content,
                'extracted_stocks': extracted_stocks,
                'extracted_keywords': extracted_keywords,
                'sentiment_score': sentiment_score,
                'importance': importance,
            }
            
            logger.info(f"✅ HTML解析完成: 标题={title[:30]}, 正文={len(content)}字符")
            return result
            
        except Exception as e:
            logger.error(f"❌ HTML解析失败: {e}")
            return {
                'title': '',
                'author': '',
                'publish_time': '',
                'content': '',
                'extracted_stocks': [],
                'extracted_keywords': [],
                'sentiment_score': 0.0,
                'importance': 0,
            }
    
    def _extract_stock_codes(self, text: str) -> List[str]:
        """从文本中提取股票代码（6位数字）"""
        pattern = r'\b\d{6}\b'
        stocks = re.findall(pattern, text)
        
        # 去重
        stocks = list(set(stocks))
        
        # 验证是否为有效股票代码
        valid_stocks = []
        for stock in stocks:
            if re.match(r'^(600|601|603|000|002|300)\d{3}$', stock):
                valid_stocks.append(stock)
        
        return valid_stocks
    
    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词"""
        keywords = []
        keyword_list = [
            '涨停', '跌停', '上涨', '下跌', '买入', '卖出', '持有',
            '航运', '石油', 'AI', '算力', '机器人', '无人机',
            '利好', '利空', '超预期', '不及预期',
            '财报', '研报', '公告', '业绩', '预增', '预减',
        ]
        
        for keyword in keyword_list:
            if keyword in text:
                keywords.append(keyword)
        
        return keywords
    
    def _calculate_sentiment_score(self, text: str) -> float:
        """计算情感评分（-1.0到+1.0）"""
        # 简单规则：利好词汇+1，利空词汇-1
        bullish_words = ['涨停', '上涨', '利好', '超预期', '买入']
        bearish_words = ['跌停', '下跌', '利空', '不及预期', '卖出']
        
        bullish_count = sum(1 for word in bullish_words if word in text)
        bearish_count = sum(1 for word in bearish_words if word in text)
        
        if bullish_count + bearish_count == 0:
            return 0.0
        
        score = (bullish_count - bearish_count) / (bullish_count + bearish_count)
        return max(-1.0, min(1.0, score))
    
    def _calculate_importance(self, text: str, author: str) -> int:
        """计算重要性（1-100）"""
        importance = 50  # 基础分
        
        # 作者知名度（模拟）
        famous_authors = ['券商研报', '财经日报', '投资日报']
        if any(fa in author for fa in famous_authors):
            importance += 20
        
        # 内容长度
        if len(text) > 5000:
            importance += 10
        
        # 包含股票代码
        stocks = self._extract_stock_codes(text)
        if len(stocks) > 0:
            importance += 10
        
        return min(100, max(1, importance))
    
    def _save_article(self, url: str, result: Dict):
        """保存文章到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO mp_articles
                (url, title, author, publish_time, content, extracted_stocks, extracted_keywords, sentiment_score, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url,
                result['title'],
                result['author'],
                result['publish_time'],
                result['content'],
                json.dumps(result['extracted_stocks'], ensure_ascii=False),
                json.dumps(result['extracted_keywords'], ensure_ascii=False),
                result['sentiment_score'],
                result['importance'],
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"✅ 文章已保存: {url}")
            
        except Exception as e:
            logger.error(f"❌ 保存文章失败: {e}")
    
    def batch_parse(self, urls: List[str]) -> List[Dict]:
        """
        批量解析公众号文章
        
        参数:
            urls: 文章链接列表
            
        返回:
            List[Dict]: 解析结果列表
        """
        logger.info(f"📂 开始批量解析文章: {len(urls)} 篇")
        
        results = []
        
        for url in urls:
            result = self.parse_link(url)
            results.append(result)
        
        logger.info(f"✅ 批量解析完成: {len(results)} 篇文章")
        return results
    
    def generate_webfetch_prompt(self, url: str) -> str:
        """
        生成WebFetch提示词
        
        用于自动化任务中调用WebFetch
        """
        prompt = f"""
请使用WebFetch工具获取以下公众号文章内容，并解析：

【文章链接】
{url}

【解析要求】
1. 提取文章标题
2. 提取作者/公众号名称
3. 提取发布时间
4. 提取文章正文（去除HTML标签）
5. 识别文章中的股票代码（6位数字）
6. 提取关键词（利好/利空/超预期等）
7. 评估文章情感倾向（利好/利空/中性）
8. 评估文章重要性（1-100分）

【输出格式】
严格按JSON格式输出，示例：
{{
  "title": "文章标题",
  "author": "公众号名称",
  "publish_time": "2026-06-24 14:30",
  "content": "文章正文内容...",
  "extracted_stocks": ["601919", "600018"],
  "extracted_keywords": ["航运", "涨停", "利好"],
  "sentiment": "bullish",
  "importance": 85
}}

开始解析。
"""
        
        # 保存提示词到文件
        prompt_file = f"data/webfetch_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        
        logger.info(f"✅ WebFetch提示词已生成: {prompt_file}")
        return prompt_file


def main():
    """主函数"""
    logger.info("🚀 启动公众号文章链接解析器...")
    
    # 创建解析器
    parser = MPArticleParser()
    
    # 测试：解析单篇文章
    test_url = "https://mp.weixin.qq.com/s/example"
    
    # 注意：示例链接无法访问，仅测试流程
    logger.warning(f"⚠️ 测试链接为示例，无法访问: {test_url}")
    
    # 演示链接识别
    print(f"\n🔗 链接识别测试:")
    print(f"  测试链接: {test_url}")
    print(f"  是否公众号文章: {parser._is_mp_article_url(test_url)}")
    
    # 演示批量解析（空列表）
    results = parser.batch_parse([])
    print(f"\n📊 批量解析结果: {len(results)} 篇文章")


if __name__ == '__main__':
    main()
