#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 微信文件传输助手收集器 (WeChat Collector)
手动导出 + 自动分析方案

功能:
1. 读取用户手动导出的微信文件传输助手内容
2. 解析文字、链接、文件引用
3. 提取有价值的信息（舆情、知识、数据）
4. 存储到sentiment_db.db 或知识库
5. 与现有舆情系统联动

使用方式:
1. 用户手动复制微信文件传输助手内容，保存到文本文件
2. 系统定期读取该文件，分析新内容
3. 或者：用户手动触发分析（通过对话粘贴内容）

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
import re

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/wechat_collector.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# 微信导出文件配置
WECHAT_EXPORT_CONFIG = {
    'export_file_path': 'data/wechat_export.txt',  # 用户手动保存的文件路径
    'auto_check_interval': 3600,  # 自动检查间隔（秒），默认1小时
    'encoding': 'utf-8',
    'backup_enabled': True,
    'backup_dir': 'data/wechat_backups',
}

# 内容类型识别规则
CONTENT_PATTERNS = {
    'url': r'https?://[^\s]+',
    'wechat_article': r'https://mp.weixin.qq.com/[^\s]+',
    'video_number': r'https://channels.weixin.qq.com/[^\s]+',
    'file_reference': r'【文件】(.*?)\.(\w+)',
    'image_reference': r'【图片】',
    'stock_code': r'[0-9]{6}\.(SH|SZ)',
    'stock_name': r'[\u4e00-\u9fa5]{2,4}股份?',
}


class WeChatCollector:
    """微信文件传输助手收集器"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        self.export_file_path = WECHAT_EXPORT_CONFIG['export_file_path']
        self.init_db()
        logger.info("✅ 微信文件传输助手收集器初始化完成")
    
    def init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建微信内容表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS wechat_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_text TEXT NOT NULL,
            content_hash TEXT UNIQUE,
            content_type TEXT,
            source VARCHAR(50) DEFAULT 'wechat_file_transfer',
            publish_time TEXT,
            collect_time TEXT DEFAULT CURRENT_TIMESTAMP,
            urls TEXT,
            stock_codes TEXT,
            sentiment_score REAL DEFAULT 0,
            importance INTEGER DEFAULT 50,
            analyzed INTEGER DEFAULT 0,
            analysis_result TEXT,
            raw_data TEXT
        )
        """)
        
        # 创建微信链接表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS wechat_urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wechat_content_id INTEGER,
            url TEXT NOT NULL,
            url_type TEXT,
            title TEXT,
            fetch_status TEXT DEFAULT 'pending',
            fetch_time TEXT,
            content TEXT,
            FOREIGN KEY (wechat_content_id) REFERENCES wechat_contents(id)
        )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ 微信内容表初始化完成")
    
    def read_export_file(self, file_path: Optional[str] = None) -> str:
        """
        读取微信导出文件
        
        参数:
            file_path: 导出文件路径，如果为None则使用默认路径
        
        返回:
            文件内容
        """
        if file_path is None:
            file_path = self.export_file_path
        
        if not os.path.exists(file_path):
            logger.warning(f"⚠️  微信导出文件不存在: {file_path}")
            return ''
        
        try:
            with open(file_path, 'r', encoding=WECHAT_EXPORT_CONFIG['encoding']) as f:
                content = f.read()
            
            logger.info(f"✅ 读取微信导出文件成功: {file_path} ({len(content)} 字符)")
            return content
            
        except Exception as e:
            logger.error(f"❌ 读取微信导出文件失败: {e}")
            return ''
    
    def parse_content(self, text: str) -> List[Dict]:
        """
        解析微信内容，提取结构化信息
        
        参数:
            text: 微信内容文本
        
        返回:
            解析后的内容列表
        """
        logger.info("🔍 开始解析微信内容...")
        
        # 按行分割
        lines = text.split('\n')
        
        contents = []
        current_content = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # 识别时间标记（微信导出格式可能包含时间戳）
            time_match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', line)
            if time_match:
                # 保存前一个内容
                if current_content:
                    contents.append(current_content)
                
                # 开始新内容
                current_content = {
                    'content_text': line,
                    'publish_time': time_match.group(0),
                    'urls': [],
                    'stock_codes': [],
                }
            else:
                # 继续当前内容
                if current_content is None:
                    current_content = {
                        'content_text': line,
                        'publish_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'urls': [],
                        'stock_codes': [],
                    }
                else:
                    current_content['content_text'] += '\n' + line
            
            # 提取URL
            urls = re.findall(CONTENT_PATTERNS['url'], line)
            if urls and current_content:
                current_content['urls'].extend(urls)
            
            # 提取股票代码
            stock_codes = re.findall(CONTENT_PATTERNS['stock_code'], line)
            if stock_codes and current_content:
                current_content['stock_codes'].extend([code[0] for code in stock_codes])
        
        # 保存最后一个内容
        if current_content:
            contents.append(current_content)
        
        logger.info(f"✅ 解析完成: 提取{len(contents)}条内容")
        return contents
    
    def save_contents(self, contents: List[Dict]) -> int:
        """
        保存解析后的内容到数据库
        
        参数:
            contents: 解析后的内容列表
        
        返回:
            新增内容数量
        """
        logger.info(f"💾 开始保存{len(contents)}条内容到数据库...")
        
        new_count = 0
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for content in contents:
            try:
                content_text = content['content_text']
                content_hash = hashlib.md5(content_text.encode('utf-8')).hexdigest()
                
                # 识别内容类型
                content_type = self._identify_content_type(content_text)
                
                # 插入数据库
                cursor.execute("""
                INSERT INTO wechat_contents
                (content_text, content_hash, content_type, publish_time, urls, stock_codes)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    content_text,
                    content_hash,
                    content_type,
                    content.get('publish_time'),
                    json.dumps(content.get('urls', [])),
                    json.dumps(content.get('stock_codes', [])),
                ))
                
                # 保存URL到wechat_urls表
                wechat_content_id = cursor.lastrowid
                for url in content.get('urls', []):
                    url_type = self._identify_url_type(url)
                    cursor.execute("""
                    INSERT INTO wechat_urls (wechat_content_id, url, url_type)
                    VALUES (?, ?, ?)
                    """, (wechat_content_id, url, url_type))
                
                new_count += 1
                logger.debug(f"✅ 新增微信内容: {content_text[:50]}...")
                
            except sqlite3.IntegrityError:
                # 已存在，跳过
                pass
            except Exception as e:
                logger.warning(f"⚠️  保存微信内容失败: {e}")
                continue
        
        conn.commit()
        conn.close()
        
        logger.info(f"✅ 保存完成: 新增{new_count}条内容")
        return new_count
    
    def _identify_content_type(self, text: str) -> str:
        """识别内容类型"""
        if re.search(CONTENT_PATTERNS['wechat_article'], text):
            return 'wechat_article'
        elif re.search(CONTENT_PATTERNS['video_number'], text):
            return 'video_number'
        elif re.search(CONTENT_PATTERNS['file_reference'], text):
            return 'file'
        elif re.search(CONTENT_PATTERNS['image_reference'], text):
            return 'image'
        elif re.search(CONTENT_PATTERNS['url'], text):
            return 'link'
        elif re.search(CONTENT_PATTERNS['stock_code'], text):
            return 'stock_info'
        else:
            return 'text'
    
    def _identify_url_type(self, url: str) -> str:
        """识别URL类型"""
        if 'mp.weixin.qq.com' in url:
            return 'wechat_article'
        elif 'channels.weixin.qq.com' in url:
            return 'video_number'
        elif 'finance.sina.com.cn' in url or 'finance.eastmoney.com' in url:
            return 'financial_news'
        elif 'xueqiu.com' in url:
            return 'xueqiu'
        else:
            return 'general'
    
    def analyze_content(self, content_id: int) -> Dict:
        """
        分析单条微信内容（生成提示词供LLM分析）
        
        参数:
            content_id: 内容ID
        
        返回:
            分析结果（提示词）
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
        SELECT content_text, content_type, urls, stock_codes
        FROM wechat_contents
        WHERE id = ?
        """, (content_id,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {'error': 'Content not found'}
        
        content_text, content_type, urls_json, stock_codes_json = row
        urls = json.loads(urls_json) if urls_json else []
        stock_codes = json.loads(stock_codes_json) if stock_codes_json else []
        
        # 生成LLM分析提示词
        prompt = f"""
请作为A股专业分析师，对以下微信文件传输助手内容进行分析：

【内容类型】{content_type}
【内容文本】
{content_text}

【包含的链接】
{chr(10).join(['- ' + url for url in urls]) if urls else '无'}

【提到的股票代码】
{', '.join(stock_codes) if stock_codes else '无'}

【分析要求】
1. 内容摘要：用一句话概括内容要点
2. 舆情价值：该内容是否包含有价值的舆情信息（是/否）
3. 投资启示：对A股投资是否有启示（有/无）
4. 相关板块：提到的相关板块或行业
5. 相关个股：提到的具体股票（代码+名称）
6. 建议操作：基于该内容的投资建议（买入/卖出/持有/观望/无）
7. 知识价值：该内容是否包含可学习的知识或数据（是/否）
8. 知识类型：如果是知识，属于哪种类型（行业研究/公司分析/交易策略/市场观察/其他）

【输出格式】
严格按JSON格式输出。
"""
        
        return {
            'content_id': content_id,
            'llm_prompt': prompt,
            'analysis_status': 'pending',
        }
    
    def batch_analyze(self, limit: int = 10):
        """
        批量分析未分析的微信内容
        
        参数:
            limit: 每次分析的最大数量
        
        返回:
            生成的LLM提示词列表
        """
        logger.info(f"🧠 开始批量分析微信内容 (limit={limit})...")
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 查询未分析的内容
        cursor.execute("""
        SELECT id FROM wechat_contents
        WHERE analyzed = 0
        LIMIT ?
        """, (limit,))
        
        rows = cursor.fetchall()
        
        prompts = []
        
        for row in rows:
            content_id = row[0]
            analysis = self.analyze_content(content_id)
            if 'error' not in analysis:
                prompts.append(analysis)
        
        conn.close()
        
        logger.info(f"✅ 批量分析完成: 生成{len(prompts)}条LLM提示词")
        return prompts
    
    def collect_from_file(self, file_path: Optional[str] = None) -> int:
        """
        从导出文件采集内容
        
        参数:
            file_path: 导出文件路径
        
        返回:
            新增内容数量
        """
        logger.info("📂 开始从导出文件采集微信内容...")
        
        # 读取文件
        content_text = self.read_export_file(file_path)
        if not content_text:
            return 0
        
        # 解析内容
        contents = self.parse_content(content_text)
        
        # 保存到数据库
        new_count = self.save_contents(contents)
        
        # 备份文件（如果配置了）
        if WECHAT_EXPORT_CONFIG['backup_enabled']:
            self._backup_export_file(file_path)
        
        logger.info(f"🎉 采集完成: 新增{new_count}条微信内容")
        return new_count
    
    def _backup_export_file(self, file_path: Optional[str] = None):
        """备份导出文件"""
        if file_path is None:
            file_path = self.export_file_path
        
        if not os.path.exists(file_path):
            return
        
        # 创建备份目录
        backup_dir = WECHAT_EXPORT_CONFIG['backup_dir']
        os.makedirs(backup_dir, exist_ok=True)
        
        # 生成备份文件名
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = os.path.join(backup_dir, f'wechat_export_{timestamp}.txt')
        
        # 复制文件
        import shutil
        shutil.copy2(file_path, backup_file)
        
        logger.info(f"✅ 备份完成: {backup_file}")
    
    def generate_collection_guide(self):
        """生成微信内容采集指南（给用户）"""
        guide = """
# 微信文件传输助手内容采集指南

## 方式一：手动导出（推荐）

### 步骤
1. 打开微信PC版
2. 找到"文件传输助手"聊天窗口
3. 选择要导出的消息（可多选）
4. 右键 → "复制" 或 Ctrl+C
5. 打开文本编辑器（如记事本）
6. 粘贴（Ctrl+V）
7. 保存到文件：`data/wechat_export.txt`
8. 确保文件编码为 UTF-8

### 注意事项
- 每次导出后，系统会自动备份到 `data/wechat_backups/`
- 建议每天导出一次（如每晚22:00）
- 可导出文字、链接、文件引用等

## 方式二：手动粘贴到对话（快速）

### 步骤
1. 复制微信文件传输助手中的内容
2. 在WorkBuddy对话中粘贴
3. 添加指令："请分析以下微信内容：[粘贴的内容]"
4. 系统会自动分析并存储

## 方式三：自动化任务提示（未来）

系统会创建自动化任务，定期提示你导出微信内容。

## 内容类型支持

系统会自动识别以下内容类型：
- ✅ 文字消息
- ✅ 网页链接（普通链接、公众号文章、视频号）
- ✅ 文件引用
- ✅ 图片引用
- ✅ 股票代码（如 601919.SH）

## 分析能力

系统会对采集的内容进行以下分析：
1. **舆情价值评估**：是否包含影响A股的舆情
2. **投资启示提取**：对投资策略的启示
3. **知识价值评估**：是否包含可学习的知识
4. **LLM深度分析**：通过自动化任务调用LLM进行深度分析

## 与舆情系统集成

采集的微信内容会：
1. 存储到 `sentiment_db.db` 的 `wechat_contents` 表
2. 与现有舆情统一分析
3. 纳入奖惩机制（如发现重要舆情 → +分数）
4. 作为知识库，提升系统能力

---
生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        # 保存指南到文件
        guide_file = 'data/wechat_collection_guide.md'
        os.makedirs('data', exist_ok=True)
        with open(guide_file, 'w', encoding='utf-8') as f:
            f.write(guide)
        
        logger.info(f"✅ 采集指南已生成: {guide_file}")
        return guide_file


def test_wechat_collector():
    """测试微信收集器"""
    print("=" * 60)
    print("测试微信文件传输助手收集器")
    print("=" * 60)
    
    # 创建日志目录
    os.makedirs('logs', exist_ok=True)
    
    # 初始化收集器
    collector = WeChatCollector()
    
    # 生成采集指南
    guide_file = collector.generate_collection_guide()
    print(f"\n✅ 采集指南已生成: {guide_file}")
    
    # 创建测试导出文件
    test_export_content = """
2026-06-24 03:00:00
霍尔木兹海峡恢复通航，油价大跌，航运股承压。
https://mp.weixin.qq.com/s/abc123

2026-06-24 03:05:00
AI板块利好，601919.SH 中远海控涨停。
【图片】

2026-06-24 03:10:00
分享一篇研报：https://www.xueqiu.com/123456/abc
    """
    
    test_file = 'data/wechat_export_test.txt'
    os.makedirs('data', exist_ok=True)
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write(test_export_content)
    
    print(f"✅ 测试导出文件已创建: {test_file}")
    
    # 从测试文件采集
    new_count = collector.collect_from_file(test_file)
    print(f"\n✅ 测试采集完成: 新增{new_count}条内容")
    
    # 批量分析
    prompts = collector.batch_analyze(limit=5)
    print(f"\n✅ 生成{len(prompts)}条LLM分析提示词")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    
    return new_count, len(prompts)


if __name__ == '__main__':
    test_wechat_collector()
