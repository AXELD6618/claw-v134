#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 微信实时同步监听器 (集成版)
集成所有新功能：图片OCR/文件解析/公众号文章解析/视频号链接解析/LLM分析

新功能:
1. 图片OCR识别 (V13_2_ImageOCR)
2. 文件内容解析 (V13_2_FileParser)
3. 公众号文章链接解析 (V13_2_MPArticleParser)
4. 视频号链接解析 (V13_2_VideoLinkParser)
5. LLM深度分析 (V13_2_SentimentLLM)

版本: V13.2 集成版
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import json
import re

# 导入新功能模块
try:
    from V13_2_ImageOCR import ImageOCRRecognizer
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False
    logging.warning("⚠️ V13_2_ImageOCR未安装")

try:
    from V13_2_FileParser import FileContentParser
    PARSER_AVAILABLE = True
except ImportError:
    PARSER_AVAILABLE = False
    logging.warning("⚠️ V13_2_FileParser未安装")

try:
    from V13_2_MPArticleParser import MPArticleParser
    MP_PARSER_AVAILABLE = True
except ImportError:
    MP_PARSER_AVAILABLE = False
    logging.warning("⚠️ V13_2_MPArticleParser未安装")

try:
    from V13_2_VideoLinkParser import VideoLinkParser
    VIDEO_PARSER_AVAILABLE = True
except ImportError:
    VIDEO_PARSER_AVAILABLE = False
    logging.warning("⚠️ V13_2_VideoLinkParser未安装")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/wechat_sync_integrated.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('WeChatRealtimeSyncIntegrated')

class WeChatRealtimeSyncIntegrated:
    """微信实时同步监听器（集成版）"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        self.wx = None  # wxauto实例
        self.is_running = False
        self.last_msg_id = 0
        
        # 新功能模块
        self.ocr_recognizer = None
        self.file_parser = None
        self.mp_parser = None
        self.video_parser = None
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        
        # 初始化新功能模块
        self._init_new_modules()
        
        # 初始化数据库
        self._init_db()
        
        logger.info("✅ 微信实时同步监听器（集成版）初始化完成")
        logger.info(f"   图片OCR: {'✅ 可用' if OCR_AVAILABLE else '❌ 不可用'}")
        logger.info(f"   文件解析: {'✅ 可用' if PARSER_AVAILABLE else '❌ 不可用'}")
        logger.info(f"   公众号解析: {'✅ 可用' if MP_PARSER_AVAILABLE else '❌ 不可用'}")
        logger.info(f"   视频号解析: {'✅ 可用' if VIDEO_PARSER_AVAILABLE else '❌ 不可用'}")
    
    def _init_new_modules(self):
        """初始化新功能模块"""
        # 图片OCR识别器
        if OCR_AVAILABLE:
            try:
                self.ocr_recognizer = ImageOCRRecognizer(db_path=self.db_path)
                logger.info("✅ 图片OCR识别器初始化完成")
            except Exception as e:
                logger.error(f"❌ 图片OCR识别器初始化失败: {e}")
        
        # 文件内容解析器
        if PARSER_AVAILABLE:
            try:
                self.file_parser = FileContentParser(db_path=self.db_path)
                logger.info("✅ 文件内容解析器初始化完成")
            except Exception as e:
                logger.error(f"❌ 文件内容解析器初始化失败: {e}")
        
        # 公众号文章解析器
        if MP_PARSER_AVAILABLE:
            try:
                self.mp_parser = MPArticleParser(db_path=self.db_path)
                logger.info("✅ 公众号文章解析器初始化完成")
            except Exception as e:
                logger.error(f"❌ 公众号文章解析器初始化失败: {e}")
        
        # 视频号链接解析器
        if VIDEO_PARSER_AVAILABLE:
            try:
                self.video_parser = VideoLinkParser(db_path=self.db_path)
                logger.info("✅ 视频号链接解析器初始化完成")
            except Exception as e:
                logger.error(f"❌ 视频号链接解析器初始化失败: {e}")
    
    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建微信消息表（扩展版）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wechat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT UNIQUE,
                    msg_type TEXT,
                    content TEXT,
                    file_path TEXT,
                    link_url TEXT,
                    ocr_text TEXT,  -- 图片OCR识别结果
                    file_parsed_text TEXT,  -- 文件解析结果
                    article_summary TEXT,  -- 公众号文章摘要
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
    
    def start_listening(self):
        """开始监听"""
        logger.info("🚀 微信文件传输助手实时监听已启动（集成版）...")
        
        # TODO: 连接微信（wxauto）
        # self.wx = WeChat()
        
        self.is_running = True
        
        try:
            while self.is_running:
                # 获取文件传输助手消息
                messages = self._fetch_filehelper_messages()
                
                if messages:
                    logger.info(f"📬 收到 {len(messages)} 条新消息")
                    
                    # 处理每条消息
                    for msg in messages:
                        try:
                            self._process_message_integrated(msg)
                        except Exception as e:
                            logger.error(f"❌ 消息处理失败: {e}")
                            continue
                
                # 等待5秒再检查
                time.sleep(5)
                
        except KeyboardInterrupt:
            logger.info("⏸️ 监听已手动停止")
        except Exception as e:
            logger.error(f"❌ 监听过程出错: {e}")
        finally:
            self.is_running = False
    
    def _fetch_filehelper_messages(self) -> List[Dict]:
        """获取文件传输助手消息（模拟）"""
        # TODO: 使用wxauto真实获取
        # 当前：返回模拟消息
        
        import random
        if random.random() < 0.15:  # 15%概率收到新消息
            mock_msgs = [
                {
                    'id': int(time.time() * 1000),
                    'sender': '文件传输助手',
                    'type': 'text',
                    'content': '测试消息：AI板块有利好政策出台，601919中远海控涨停',
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                },
                {
                    'id': int(time.time() * 1000) + 1,
                    'sender': '文件传输助手',
                    'type': 'image',
                    'content': 'data/wechat_images/test_image.jpg',
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                },
                {
                    'id': int(time.time() * 1000) + 2,
                    'sender': '文件传输助手',
                    'type': 'file',
                    'content': 'data/wechat_files/test.pdf',
                    'file_name': '财报.pdf',
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                },
                {
                    'id': int(time.time() * 1000) + 3,
                    'sender': '文件传输助手',
                    'type': 'link',
                    'content': 'https://mp.weixin.qq.com/s/example',
                    'title': '财经文章：美联储降息预期升温',
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            ]
            return mock_msgs
        return []
    
    def _process_message_integrated(self, msg: Dict):
        """
        处理单条消息（集成版）
        
        新功能：
        1. 图片 → OCR识别
        2. 文件 → 内容解析
        3. 链接 → 公众号文章/视频号解析
        4. 所有消息 → LLM深度分析
        """
        msg_id = str(msg.get('id', int(time.time() * 1000)))
        msg_type = msg.get('type', 'text')
        content = msg.get('content', '')
        
        logger.info(f"📝 处理消息 [{msg_type}]: {content[:50]}...")
        
        # 1. 保存到数据库
        self._save_message(msg_id, msg_type, content, msg)
        
        # 2. 根据消息类型调用相应模块
        ocr_text = None
        file_parsed_text = None
        article_summary = None
        
        if msg_type == 'image' and self.ocr_recognizer:
            # 图片OCR识别
            logger.info(f"🖼️ 开始图片OCR识别...")
            ocr_result = self.ocr_recognizer.recognize_image(content)
            ocr_text = ocr_result.get('ocr_text', '')
            logger.info(f"   OCR识别完成: {len(ocr_text)} 字符")
        
        elif msg_type == 'file' and self.file_parser:
            # 文件内容解析
            logger.info(f"📄 开始文件内容解析...")
            parse_result = self.file_parser.parse_file(content)
            file_parsed_text = parse_result.get('parsed_text', '')
            logger.info(f"   文件解析完成: {len(file_parsed_text)} 字符")
        
        elif msg_type == 'link':
            # 链接解析
            url = content
            
            if self.mp_parser and self.mp_parser._is_mp_article_url(url):
                # 公众号文章解析
                logger.info(f"📰 开始公众号文章解析...")
                article_result = self.mp_parser.parse_link(url)
                article_summary = article_result.get('content', '')[:500]
                logger.info(f"   文章解析完成: {len(article_summary)} 字符")
            
            elif self.video_parser and self.video_parser._is_video_link(url):
                # 视频号链接解析
                logger.info(f"🎬 开始视频号链接解析...")
                video_result = self.video_parser.parse_link(url)
                article_summary = video_result.get('description', '')[:500]
                logger.info(f"   视频解析完成: {len(article_summary)} 字符")
        
        # 3. 更新数据库（OCR/解析结果）
        self._update_message_analysis(msg_id, ocr_text, file_parsed_text, article_summary)
        
        # 4. LLM深度分析（TODO：通过自动化任务调用）
        logger.info(f"🤖 消息已就绪，等待LLM深度分析...")
        
        logger.info(f"✅ 消息处理完成: {msg_id}")
    
    def _save_message(self, msg_id: str, msg_type: str, content: str, msg: Dict):
        """保存消息到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO wechat_messages
                (msg_id, msg_type, content, processed)
                VALUES (?, ?, ?, ?)
            """, (msg_id, msg_type, content, 1))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ 保存消息失败: {e}")
    
    def _update_message_analysis(self, msg_id: str, ocr_text: str, file_parsed_text: str, article_summary: str):
        """更新消息分析结果"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            update_fields = []
            update_values = []
            
            if ocr_text:
                update_fields.append("ocr_text = ?")
                update_values.append(ocr_text)
            
            if file_parsed_text:
                update_fields.append("file_parsed_text = ?")
                update_values.append(file_parsed_text)
            
            if article_summary:
                update_fields.append("article_summary = ?")
                update_values.append(article_summary)
            
            if update_fields:
                sql = f"UPDATE wechat_messages SET {', '.join(update_fields)} WHERE msg_id = ?"
                update_values.append(msg_id)
                
                cursor.execute(sql, update_values)
                conn.commit()
            
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ 更新消息分析结果失败: {e}")


def main():
    """主函数"""
    logger.info("🚀 启动微信实时同步监听器（集成版）...")
    
    # 创建监听器
    listener = WeChatRealtimeSyncIntegrated()
    
    # 开始监听
    try:
        listener.start_listening()
    except KeyboardInterrupt:
        logger.info("⏸️ 监听已停止")
    finally:
        logger.info("✅ 监听器已关闭")


if __name__ == '__main__':
    main()
