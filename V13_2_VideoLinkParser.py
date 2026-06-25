#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 视频号链接解析器 (Video Link Parser)
解析微信文件传输助手中的视频号链接

功能:
1. 识别视频号链接（微信视频号特有的URL格式）
2. 使用WebFetch/requests获取视频页面
3. 解析视频标题/作者/描述/标签
4. 提取视频描述中的股票代码/板块名称/投资观点
5. 使用LLM分析视频内容（如果有字幕/描述）
6. 保存到舆情数据库

技术路线:
- 链接识别：正则表达式
- 内容获取：WebFetch（优先）/ requests（备用）
- 内容解析：BeautifulSoup（HTML解析）
- LLM分析：通过自动化任务调用LLM

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
        logging.FileHandler('logs/video_link_parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('VideoLinkParser')

class VideoLinkParser:
    """视频号链接解析器"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        
        # 解析器配置
        self.use_webfetch = True
        self.use_requests = True
        self.timeout = 10
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        
        logger.info("✅ 视频号链接解析器初始化完成")
    
    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建视频链接表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS video_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    title TEXT,
                    author TEXT,
                    description TEXT,
                    tags TEXT,  -- JSON array
                    extracted_stocks TEXT,  -- JSON array
                    extracted_keywords TEXT,  -- JSON array
                    sentiment_score REAL,
                    importance INTEGER,
                    llm_analyzed INTEGER DEFAULT 0,
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
        解析视频号链接
        
        参数:
            url: 视频号链接
            
        返回:
            Dict: {
                'title': str,  # 视频标题
                'author': str,  # 作者/公众号名称
                'description': str,  # 视频描述
                'tags': List[str],  # 标签
                'extracted_stocks': List[str],  # 提取的股票代码
                'extracted_keywords': List[str],  # 提取的关键词
                'sentiment_score': float,  # 情感评分
                'importance': int,  # 重要性（1-100）
            }
        """
        logger.info(f"🎬 开始解析视频号链接: {url}")
        
        if not self._is_video_link(url):
            logger.warning(f"⚠️ 不是视频号链接: {url}")
            return {
                'title': '',
                'author': '',
                'description': '',
                'tags': [],
                'extracted_stocks': [],
                'extracted_keywords': [],
                'sentiment_score': 0.0,
                'importance': 0,
            }
        
        # 获取视频页面内容
        html_content = self._fetch_video_content(url)
        
        if not html_content:
            logger.error(f"❌ 获取视频内容失败: {url}")
            return {
                'title': '',
                'author': '',
                'description': '',
                'tags': [],
                'extracted_stocks': [],
                'extracted_keywords': [],
                'sentiment_score': 0.0,
                'importance': 0,
            }
        
        # 解析HTML内容
        result = self._parse_html_content(html_content, url)
        
        # 保存到数据库
        self._save_video(url, result)
        
        logger.info(f"✅ 视频链接解析完成: {result['title'][:50]}")
        return result
    
    def _is_video_link(self, url: str) -> bool:
        """判断是否为视频号链接"""
        # 微信视频号链接格式（模拟）
        patterns = [
            r'https?://weixin\.qq\.com/sph/[a-zA-Z0-9]+',
            r'https?://channels\.weixin\.qq\.com/[a-zA-Z0-9]+',
        ]
        
        for pattern in patterns:
            if re.match(pattern, url):
                return True
        
        return False
    
    def _fetch_video_content(self, url: str) -> Optional[str]:
        """
        获取视频页面内容（HTML）
        
        注意：微信视频号链接可能需要登录才能访问
        """
        logger.info("📡 获取视频页面内容...")
        
        # 优先：WebFetch（需要通过自动化任务调用）
        if self.use_webfetch:
            logger.info("🌐 使用WebFetch获取内容...")
            # TODO: 通过自动化任务调用WebFetch
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
                    logger.info(f"✅ 视频页面获取成功: {len(response.text)} 字符")
                    return response.text
                else:
                    logger.error(f"❌ 获取视频页面失败: HTTP {response.status_code}")
                    return None
                    
            except Exception as e:
                logger.error(f"❌ 获取视频页面失败: {e}")
                return None
        
        return None
    
    def _parse_html_content(self, html_content: str, url: str) -> Dict:
        """解析HTML内容，提取视频信息"""
        logger.info("📝 解析HTML内容...")
        
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 提取标题
            title = ""
            title_tag = soup.find('h1')
            if title_tag:
                title = title_tag.get_text(strip=True)
            
            # 提取作者
            author = ""
            author_tag = soup.find('a', class_='author')
            if author_tag:
                author = author_tag.get_text(strip=True)
            
            # 提取描述
            description = ""
            desc_tag = soup.find('div', class_='description')
            if desc_tag:
                description = desc_tag.get_text(separator='\n', strip=True)
            
            # 提取标签
            tags = []
            tag_tags = soup.find_all('a', class_='tag')
            for tag_tag in tag_tags:
                tags.append(tag_tag.get_text(strip=True))
            
            # 提取股票代码和关键词
            text = title + " " + description
            extracted_stocks = self._extract_stock_codes(text)
            extracted_keywords = self._extract_keywords(text)
            
            # 计算情感评分和重要性
            sentiment_score = self._calculate_sentiment_score(text)
            importance = self._calculate_importance(text, author)
            
            result = {
                'title': title,
                'author': author,
                'description': description,
                'tags': tags,
                'extracted_stocks': extracted_stocks,
                'extracted_keywords': extracted_keywords,
                'sentiment_score': sentiment_score,
                'importance': importance,
            }
            
            logger.info(f"✅ HTML解析完成: 标题={title[:30]}, 描述={len(description)}字符")
            return result
            
        except Exception as e:
            logger.error(f"❌ HTML解析失败: {e}")
            return {
                'title': '',
                'author': '',
                'description': '',
                'tags': [],
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
        famous_authors = ['财经大V', '投资专家', '券商分析师']
        if any(fa in author for fa in famous_authors):
            importance += 20
        
        # 内容长度
        if len(text) > 2000:
            importance += 10
        
        # 包含股票代码
        stocks = self._extract_stock_codes(text)
        if len(stocks) > 0:
            importance += 10
        
        return min(100, max(1, importance))
    
    def _save_video(self, url: str, result: Dict):
        """保存视频信息到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO video_links
                (url, title, author, description, tags, extracted_stocks, extracted_keywords, sentiment_score, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                url,
                result['title'],
                result['author'],
                result['description'],
                json.dumps(result['tags'], ensure_ascii=False),
                json.dumps(result['extracted_stocks'], ensure_ascii=False),
                json.dumps(result['extracted_keywords'], ensure_ascii=False),
                result['sentiment_score'],
                result['importance'],
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"✅ 视频信息已保存: {url}")
            
        except Exception as e:
            logger.error(f"❌ 保存视频信息失败: {e}")
    
    def batch_parse(self, urls: List[str]) -> List[Dict]:
        """
        批量解析视频号链接
        
        参数:
            urls: 视频链接列表
            
        返回:
            List[Dict]: 解析结果列表
        """
        logger.info(f"📂 开始批量解析视频: {len(urls)} 个")
        
        results = []
        
        for url in urls:
            result = self.parse_link(url)
            results.append(result)
        
        logger.info(f"✅ 批量解析完成: {len(results)} 个视频")
        return results
    
    def generate_llm_analysis_prompt(self, url: str, video_info: Dict) -> str:
        """
        生成LLM分析视频内容的提示词
        
        用于自动化任务中调用LLM
        """
        prompt = f"""
请作为A股专业分析师，对以下视频号内容进行深度分析：

【视频信息】
链接：{url}
标题：{video_info['title']}
作者：{video_info['author']}
描述：{video_info['description']}
标签：{', '.join(video_info['tags'])}

【分析要求】
1. 分析视频描述中的投资观点
2. 评估视频对A股相关板块/个股的影响
3. 提取视频中的投资建议（买入/卖出/持有/观望）
4. 评估视频内容的可靠性和权威性
5. 给出综合投资建议

【输出格式】
严格按JSON格式输出，示例：
{{
  "investment_view": "看好航运板块，认为中远海控有上涨空间",
  "impact_assessment": "利好航运板块，可能带动相关个股上涨",
  "recommendation": "关注航运板块龙头股，可适当配置",
  "reliability": 7,  // 1-10分
  "final_sentiment": "bullish"
}}

开始分析。
"""
        
        # 保存提示词到文件
        prompt_file = f"data/llm_video_analysis_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        
        logger.info(f"✅ LLM分析提示词已生成: {prompt_file}")
        return prompt_file


def main():
    """主函数"""
    logger.info("🚀 启动视频号链接解析器...")
    
    # 创建解析器
    parser = VideoLinkParser()
    
    # 测试：解析单个视频链接
    test_url = "https://weixin.qq.com/sph/example"
    
    # 注意：示例链接无法访问，仅测试流程
    logger.warning(f"⚠️ 测试链接为示例，无法访问: {test_url}")
    
    # 演示链接识别
    print(f"\n🔗 视频号链接识别测试:")
    print(f"  测试链接: {test_url}")
    print(f"  是否视频号链接: {parser._is_video_link(test_url)}")
    
    # 演示批量解析（空列表）
    results = parser.batch_parse([])
    print(f"\n📊 批量解析结果: {len(results)} 个视频")


if __name__ == '__main__':
    main()
