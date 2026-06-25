#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 微信实时同步监听器 (WeChat Realtime Sync Listener)
自主自动实时在线同步获取文件传输助手信息

功能:
1. 实时监听微信文件传输助手消息（文字/图片/文件/链接）
2. 自动解析消息内容
3. 保存到舆情数据库
4. 触发舆情分析
5. 与奖惩引擎联动

技术路线:
- 使用wxauto库（基于UIAutomation）
- 监听微信窗口消息
- 解析文件传输助手消息

版本: V13.2
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

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/wechat_sync.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('WeChatRealtimeSync')

class WeChatRealtimeSync:
    """微信实时同步监听器"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        self.wx = None  # wxauto实例
        self.is_running = False
        self.last_msg_id = 0  # 最后处理的消息ID
        
        # 崩溃重启配置
        self.start_time = datetime.now()
        self.restart_count = 0
        self.max_restarts = 10  # 最大重启次数（24小时内）
        self.health_check_interval = 300  # 健康检查间隔（5分钟）
        self.last_health_check = datetime.now()
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        
        logger.info("✅ 微信实时同步监听器初始化完成")
    
    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建微信消息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wechat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT UNIQUE,
                    msg_type TEXT,  -- text/image/file/link
                    content TEXT,
                    file_path TEXT,
                    link_url TEXT,
                    send_time TEXT,
                    processed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建微信内容舆情表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS wechat_sentiment (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    msg_id TEXT,
                    content TEXT,
                    sentiment_score REAL,
                    importance INTEGER,
                    keywords TEXT,  -- JSON array
                    sentiment_type TEXT,
                    processed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (msg_id) REFERENCES wechat_messages(msg_id)
                )
            """)
            
            conn.commit()
            conn.close()
            
            logger.info("✅ 微信消息数据库表初始化完成")
            
        except Exception as e:
            logger.error(f"❌ 数据库初始化失败: {e}")
    
    def connect_wechat(self) -> bool:
        """连接微信"""
        try:
            # 尝试导入wxauto
            try:
                from wxauto import WeChat
                try:
                    self.wx = WeChat()
                    logger.info("✅ 微信连接成功（wxauto）")
                    return True
                except Exception as conn_err:
                    logger.warning(f"⚠️ wxauto已安装但微信客户端不可用: {conn_err}")
                    logger.warning("⚠️ 微信桌面客户端未运行，回退到模拟模式")
                    self.wx = MockWeChat()
                    return True
            except ImportError:
                logger.warning("⚠️ wxauto库未安装，使用模拟模式")
                self.wx = MockWeChat()
                return True
                
        except Exception as e:
            logger.error(f"❌ 微信连接失败: {e}")
            return False
    
    def start_listening(self):
        """开始监听文件传输助手消息"""
        if not self.connect_wechat():
            logger.error("❌ 无法连接微信，监听启动失败")
            return
        
        self.is_running = True
        self.start_time = datetime.now()
        logger.info("🚀 微信文件传输助手实时监听已启动...")
        
        try:
            while self.is_running:
                # 健康检查（每5分钟）
                now = datetime.now()
                if (now - self.last_health_check).seconds >= self.health_check_interval:
                    self._health_check()
                    self.last_health_check = now
                
                # 获取文件传输助手消息
                messages = self._fetch_filehelper_messages()
                
                if messages:
                    logger.info(f"📬 收到 {len(messages)} 条新消息")
                    
                    # 处理每条消息
                    for msg in messages:
                        try:
                            self._process_message(msg)
                        except Exception as e:
                            logger.error(f"❌ 消息处理失败: {e}, 消息ID: {msg.get('id', 'unknown')}")
                            continue  # 继续处理下一条消息
                
                # 等待一段时间再检查
                time.sleep(5)  # 每5秒检查一次
                
        except KeyboardInterrupt:
            logger.info("⏸️ 监听已手动停止")
        except Exception as e:
            logger.error(f"❌ 监听过程出错: {e}")
            # 自动重启
            self._auto_restart()
        finally:
            self.is_running = False
    
    def _health_check(self):
        """健康检查"""
        try:
            uptime = (datetime.now() - self.start_time).seconds
            logger.info(f"💓 健康检查: 运行时间={uptime}秒, 重启次数={self.restart_count}")
            
            # 检查数据库连接
            try:
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM wechat_messages")
                count = cursor.fetchone()[0]
                conn.close()
                logger.info(f"💾 数据库健康检查通过: {count} 条消息")
            except Exception as e:
                logger.error(f"❌ 数据库健康检查失败: {e}")
                
        except Exception as e:
            logger.error(f"❌ 健康检查失败: {e}")
    
    def _auto_restart(self):
        """自动重启"""
        if self.restart_count >= self.max_restarts:
            logger.error(f"❌ 已达到最大重启次数 ({self.max_restarts})，停止重启")
            return
        
        self.restart_count += 1
        logger.info(f"🔄 自动重启中... (第 {self.restart_count}/{self.max_restarts} 次)")
        
        # 等待5秒后重启
        time.sleep(5)
        
        # 重新初始化
        self.is_running = False
        time.sleep(2)
        
        # 重新启动
        self.start_listening()
    
    def _fetch_filehelper_messages(self) -> List[Dict]:
        """获取文件传输助手消息"""
        try:
            # 使用wxauto获取文件传输助手消息
            if hasattr(self.wx, 'GetAllMessage'):
                # 真实wxauto接口
                msgs = self.wx.GetAllMessage()
                
                # 过滤文件传输助手消息
                filehelper_msgs = []
                for msg in msgs:
                    if '文件传输助手' in msg.get('sender', ''):
                        # 检查是否已处理
                        if msg['id'] > self.last_msg_id:
                            filehelper_msgs.append(msg)
                            self.last_msg_id = msg['id']
                
                return filehelper_msgs
            else:
                # 模拟模式：返回模拟消息
                return self._mock_fetch_messages()
                
        except Exception as e:
            logger.error(f"❌ 获取消息失败: {e}")
            return []
    
    def _mock_fetch_messages(self) -> List[Dict]:
        """模拟获取消息（测试用）"""
        # 模拟偶尔收到新消息
        import random
        if random.random() < 0.1:  # 10%概率收到新消息
            mock_msgs = [
                {
                    'id': int(time.time() * 1000),
                    'sender': '文件传输助手',
                    'type': 'text',
                    'content': '测试消息：AI板块有利好政策出台',
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                },
                {
                    'id': int(time.time() * 1000) + 1,
                    'sender': '文件传输助手',
                    'type': 'link',
                    'content': 'https://www.example.com/article/123',
                    'title': '财经新闻：美联储降息预期升温',
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            ]
            return mock_msgs
        return []
    
    def _process_message(self, msg: Dict):
        """处理单条消息"""
        try:
            msg_id = str(msg.get('id', int(time.time() * 1000)))
            msg_type = msg.get('type', 'text')
            content = msg.get('content', '')
            send_time = msg.get('time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            
            logger.info(f"📝 处理消息 [{msg_type}]: {content[:50]}...")
            
            # 1. 保存到数据库
            self._save_message(msg_id, msg_type, content, send_time)
            
            # 2. 解析消息内容
            parsed_content = self._parse_message_content(msg)
            
            # 3. 如果是文字/链接，进行舆情分析
            if msg_type in ['text', 'link']:
                self._analyze_sentiment(msg_id, parsed_content)
            
            # 4. 如果是文件/图片，保存到指定目录
            if msg_type in ['file', 'image']:
                self._save_file(msg)
            
            logger.info(f"✅ 消息处理完成: {msg_id}")
            
        except Exception as e:
            logger.error(f"❌ 消息处理失败: {e}")
    
    def _save_message(self, msg_id: str, msg_type: str, content: str, send_time: str):
        """保存消息到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO wechat_messages
                (msg_id, msg_type, content, send_time, processed)
                VALUES (?, ?, ?, ?, ?)
            """, (msg_id, msg_type, content, send_time, 1))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"❌ 保存消息失败: {e}")
    
    def _parse_message_content(self, msg: Dict) -> str:
        """解析消息内容"""
        msg_type = msg.get('type', 'text')
        content = msg.get('content', '')
        
        if msg_type == 'text':
            # 文字消息：直接返回
            return content
        
        elif msg_type == 'link':
            # 链接消息：提取标题和URL
            title = msg.get('title', '')
            url = msg.get('content', '')
            return f"{title}\n{url}"
        
        elif msg_type == 'file':
            # 文件消息：记录文件名
            file_name = msg.get('file_name', '')
            return f"[文件] {file_name}"
        
        elif msg_type == 'image':
            # 图片消息：记录图片路径
            image_path = msg.get('image_path', '')
            return f"[图片] {image_path}"
        
        else:
            return content
    
    def _analyze_sentiment(self, msg_id: str, content: str):
        """进行舆情分析"""
        try:
            # 简单规则分析
            sentiment_score = self._rule_sentiment_analysis(content)
            importance = self._calculate_importance(content)
            keywords = self._extract_keywords(content)
            
            # 保存到数据库
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR IGNORE INTO wechat_sentiment
                (msg_id, content, sentiment_score, importance, keywords, sentiment_type, processed)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                msg_id,
                content,
                sentiment_score,
                importance,
                json.dumps(keywords, ensure_ascii=False),
                'wechat_filehelper',
                0  # 未处理，待LLM分析
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"📊 舆情分析完成: 得分={sentiment_score:.2f}, 重要性={importance}")
            
            # 如果重要性高，触发舆情分析
            if importance >= 70:
                logger.info(f"🔔 重要舆情发现！重要性={importance}")
                # TODO: 触发LLM分析（通过自动化任务）
            
        except Exception as e:
            logger.error(f"❌ 舆情分析失败: {e}")
    
    def _rule_sentiment_analysis(self, text: str) -> float:
        """规则情感分析"""
        positive_keywords = ['利好', '上涨', '涨停', '突破', '创新高', '放量', '买入', '推荐', '看好']
        negative_keywords = ['利空', '下跌', '跌停', '破位', '创新低', '缩量', '卖出', '回避', '看空']
        
        positive_count = sum(1 for kw in positive_keywords if kw in text)
        negative_count = sum(1 for kw in negative_keywords if kw in text)
        
        if positive_count + negative_count == 0:
            return 0.0
        
        score = (positive_count - negative_count) / (positive_count + negative_count)
        return max(-1.0, min(1.0, score))
    
    def _calculate_importance(self, text: str) -> int:
        """计算重要性（1-100）"""
        importance = 50  # 基础分
        
        # 关键词加分
        high_impact_keywords = ['央行', '降息', '降准', '财政部', '发改委', '特朗普', '霍尔木兹', '战争', '制裁']
        for kw in high_impact_keywords:
            if kw in text:
                importance += 10
        
        # 股票代码加分
        stock_codes = re.findall(r'(60\d{4}|00\d{4}|30\d{4}|688\d{3})', text)
        if stock_codes:
            importance += len(stock_codes) * 5
        
        return min(100, importance)
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        
        # 股票代码
        stock_codes = re.findall(r'(60\d{4}|00\d{4}|30\d{4}|688\d{3})', text)
        keywords.extend(stock_codes)
        
        # 行业关键词
        industry_keywords = ['AI', '芯片', '新能源', '光伏', '锂电池', '医药', '消费', '地产', '金融']
        for kw in industry_keywords:
            if kw in text:
                keywords.append(kw)
        
        return keywords[:5]  # 最多5个
    
    def _save_file(self, msg: Dict):
        """保存文件/图片"""
        # TODO: 实现文件保存逻辑
        pass
    
    def stop_listening(self):
        """停止监听"""
        self.is_running = False
        logger.info("⏸️ 微信文件传输助手监听已停止")


class MockWeChat:
    """模拟微信接口（用于测试）"""
    
    def GetAllMessage(self):
        """获取所有消息（模拟）"""
        return []


def main():
    """主函数（带自动重启）"""
    logger.info("🚀 启动微信实时同步监听器（带自动重启）...")
    
    max_restarts = 10  # 最大重启次数（24小时内）
    restart_count = 0
    start_time = datetime.now()
    
    while restart_count < max_restarts:
        try:
            # 创建监听器
            listener = WeChatRealtimeSync()
            
            # 开始监听
            listener.start_listening()
            
            # 如果正常退出，跳出循环
            if not listener.is_running:
                logger.info("✅ 监听器正常退出")
                break
                
        except KeyboardInterrupt:
            logger.info("⏸️ 监听已手动停止")
            break
        except Exception as e:
            restart_count += 1
            logger.error(f"❌ 监听器崩溃: {e}, 重启中 ({restart_count}/{max_restarts})...")
            
            # 等待5秒后重启
            time.sleep(5)
            
            # 检查是否超过24小时
            if (datetime.now() - start_time).days >= 1:
                restart_count = 0  # 重置计数器
                start_time = datetime.now()
                logger.info("✅ 重启计数器已重置（24小时）")
    
    if restart_count >= max_restarts:
        logger.error(f"❌ 已达到最大重启次数 ({max_restarts})，停止重启")
        logger.error("⚠️ 请检查日志并手动修复问题")
    
    logger.info("🛑 微信实时同步监听器已完全停止")


if __name__ == '__main__':
    main()
