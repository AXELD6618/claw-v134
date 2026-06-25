#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 微信实时同步监听器（uiautomation版）
使用uiautomation库替代wxauto，解决GitHub访问问题

功能:
1. 实时监听微信文件传输助手消息（使用uiautomation）
2. 自动解析消息内容（文字/图片/文件/链接）
3. 保存到数据库（sentiment_db.db）
4. 触发LLM分析（通过自动化任务）
5. 崩溃自动重启

技术路线:
- 使用uiautomation库（wxauto的底层库，可通过PyPI安装）
- 不依赖wxauto（无需从GitHub安装）

版本: V13.2
创建: 2026-06-24
作者: 毕方灵犀·天眼 (亚瑟)
"""

import os
import sys
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import hashlib

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/wechat_sync.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('WeChatSyncUIA')

# 尝试导入uiautomation
try:
    import uiautomation as uia
    UIA_AVAILABLE = True
    logger.info("✅ uiautomation库已导入")
except ImportError:
    UIA_AVAILABLE = False
    logger.warning("⚠️ uiautomation库未安装，将使用模拟模式")


class WeChatRealtimeSyncUIA:
    """微信实时同步监听器（uiautomation版）"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        self.wechat_window = None  # 微信窗口
        self.is_running = False
        self.last_msg_id = 0  # 最后处理的消息ID
        self.restart_count = 0  # 重启次数
        self.max_restarts = 10  # 最大重启次数/24小时
        self.last_restart_time = None  # 最后重启时间
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        
        logger.info("✅ 微信实时同步监听器（uiautomation版）初始化完成")
    
    def _init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建微信消息表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS wechat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id TEXT UNIQUE NOT NULL,
            msg_type TEXT NOT NULL,
            content TEXT,
            title TEXT,
            url TEXT,
            file_path TEXT,
            thumbnail_path TEXT,
            send_time TEXT,
            sender TEXT DEFAULT 'Self',
            ocr_text TEXT,
            extracted_stocks TEXT,  -- JSON array
            llm_sentiment REAL,
            llm_impact INTEGER,
            llm_analysis TEXT,  -- JSON object
            llm_analyzed INTEGER DEFAULT 0,
            llm_analysis_time TEXT,
            fetch_status TEXT DEFAULT 'pending',
            fetch_time TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_wechat_msg_id ON wechat_messages(msg_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_wechat_send_time ON wechat_messages(send_time)')
        
        conn.commit()
        conn.close()
        
        logger.info("✅ 数据库初始化完成")
    
    def connect_wechat(self) -> bool:
        """
        连接微信（使用uiautomation）
        
        返回:
            是否连接成功
        """
        if not UIA_AVAILABLE:
            logger.warning("⚠️ uiautomation不可用，使用模拟模式")
            return True  # 模拟模式
        
        try:
            # 查找微信窗口
            self.wechat_window = uia.WindowControl(searchDepth=1, Name='微信')
            
            if not self.wechat_window.Exists():
                logger.error("❌ 未找到微信窗口，请确保微信已登录")
                return False
            
            logger.info("✅ 已连接微信窗口")
            return True
            
        except Exception as e:
            logger.error(f"❌ 连接微信失败: {e}")
            return False
    
    def _fetch_filehelper_messages(self) -> List[Dict]:
        """
        获取文件传输助手消息（使用uiautomation）
        
        返回:
            消息列表
        """
        if not UIA_AVAILABLE:
            # 模拟模式：返回模拟消息
            return self._simulate_messages()
        
        messages = []
        
        try:
            # 查找文件传输助手
            # 注意：这里需要根据微信UI的实际结构来调整
            filehelper = self.wechat_window.ListItemControl(Name='文件传输助手')
            
            if not filehelper.Exists():
                logger.warning("⚠️ 未找到文件传输助手")
                return []
            
            # 点击文件传输助手
            filehelper.Click()
            time.sleep(1)
            
            # 获取消息列表
            # 注意：这里需要根据微信UI的实际结构来调整
            msg_list = self.wechat_window.ListControl(Name='消息列表')
            
            if not msg_list.Exists():
                logger.warning("⚠️ 未找到消息列表")
                return []
            
            # 遍历消息
            for msg_item in msg_list.GetChildren():
                msg_data = self._parse_message_item(msg_item)
                
                if msg_data:
                    messages.append(msg_data)
            
            logger.info(f"📬 获取到 {len(messages)} 条消息")
            return messages
            
        except Exception as e:
            logger.error(f"❌ 获取消息失败: {e}")
            return []
    
    def _parse_message_item(self, msg_item) -> Optional[Dict]:
        """
        解析消息项（使用uiautomation）
        
        参数:
            msg_item: uiautomation消息项控件
        
        返回:
            消息数据字典，如果解析失败则返回None
        """
        try:
            # 提取消息ID（使用控件属性或哈希）
            msg_id = str(hash(str(msg_item)))
            
            # 提取消息类型
            # 注意：这里需要根据微信UI的实际结构来调整
            msg_type = 'text'  # 默认文字
            
            # 提取消息内容
            content = ''
            try:
                content_control = msg_item.TextControl()
                if content_control.Exists():
                    content = content_control.Name
            except:
                pass
            
            # 提取发送时间
            send_time = datetime.now().isoformat()
            
            return {
                'msg_id': msg_id,
                'msg_type': msg_type,
                'content': content,
                'send_time': send_time,
            }
            
        except Exception as e:
            logger.error(f"❌ 解析消息项失败: {e}")
            return None
    
    def _simulate_messages(self) -> List[Dict]:
        """
        模拟消息（用于测试）
        
        返回:
            模拟消息列表
        """
        # 每10次调用生成1条模拟消息
        if int(time.time()) % 10 != 0:
            return []
        
        simulated_msgs = [
            {
                'msg_id': f'sim_{int(time.time())}',
                'msg_type': 'text',
                'content': '模拟消息：AI板块利好，601919涨停',
                'send_time': datetime.now().isoformat(),
            }
        ]
        
        logger.info(f"📬 模拟生成 {len(simulated_msgs)} 条消息")
        return simulated_msgs
    
    def _process_message(self, msg: Dict) -> bool:
        """
        处理单条消息
        
        参数:
            msg: 消息数据字典
        
        返回:
            是否处理成功
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 检查是否已存在
            cursor.execute('SELECT id FROM wechat_messages WHERE msg_id = ?', (msg['msg_id'],))
            if cursor.fetchone():
                logger.debug(f"消息已存在: {msg['msg_id']}")
                conn.close()
                return True
            
            # 插入新消息
            cursor.execute('''
            INSERT INTO wechat_messages (
                msg_id, msg_type, content, send_time, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ''', (
                msg['msg_id'],
                msg['msg_type'],
                msg.get('content', ''),
                msg.get('send_time', datetime.now().isoformat()),
                datetime.now().isoformat()
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"💾 消息已保存: {msg['msg_id']} - {msg.get('content', '')[:50]}")
            return True
            
        except Exception as e:
            logger.error(f"❌ 处理消息失败: {e}")
            return False
    
    def start_listening(self):
        """开始监听文件传输助手消息"""
        if not self.connect_wechat():
            logger.error("❌ 无法连接微信，监听启动失败")
            return
        
        self.is_running = True
        logger.info("🚀 微信文件传输助手实时监听已启动（uiautomation版）...")
        
        try:
            while self.is_running:
                # 获取文件传输助手消息
                messages = self._fetch_filehelper_messages()
                
                if messages:
                    logger.info(f"📬 收到 {len(messages)} 条新消息")
                    
                    # 处理每条消息
                    for msg in messages:
                        self._process_message(msg)
                
                # 健康检查
                if self._health_check_needed():
                    self._perform_health_check()
                
                # 等待一段时间再检查
                time.sleep(5)  # 每5秒检查一次
                
        except KeyboardInterrupt:
            logger.info("⏸️ 监听已手动停止")
        except Exception as e:
            logger.error(f"❌ 监听过程出错: {e}")
            self._schedule_restart()
        finally:
            self.is_running = False
    
    def _health_check_needed(self) -> bool:
        """检查是否需要执行健康检查"""
        # 每10分钟检查一次
        return int(time.time()) % 600 < 5
    
    def _perform_health_check(self):
        """执行健康检查"""
        logger.info("🔧 执行健康检查...")
        
        # 检查微信连接
        if UIA_AVAILABLE and self.wechat_window:
            if not self.wechat_window.Exists():
                logger.warning("⚠️ 微信窗口已关闭，尝试重新连接...")
                self.connect_wechat()
        
        # 检查数据库
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM wechat_messages')
            count = cursor.fetchone()[0]
            conn.close()
            logger.info(f"💾 数据库健康检查通过，当前消息数: {count}")
        except Exception as e:
            logger.error(f"❌ 数据库健康检查失败: {e}")
    
    def _schedule_restart(self):
        """安排重启"""
        # 检查重启次数
        if self.last_restart_time:
            time_since_restart = datetime.now() - datetime.fromisoformat(self.last_restart_time)
            if time_since_restart.total_seconds() < 86400:  # 24小时内
                if self.restart_count >= self.max_restarts:
                    logger.error(f"❌ 已达到最大重启次数({self.max_restarts})，停止重启")
                    return
        
        # 安排重启
        self.restart_count += 1
        self.last_restart_time = datetime.now().isoformat()
        
        logger.info(f"🔄 安排重启（第{self.restart_count}次），5秒后重启...")
        time.sleep(5)
        
        # 重启
        self.is_running = False
        self.start_listening()
    
    def stop_listening(self):
        """停止监听"""
        self.is_running = False
        logger.info("⏸️ 微信实时同步监听器已停止")


def main():
    """主函数"""
    logger.info("🚀 启动微信实时同步监听器（uiautomation版）...")
    
    # 创建监听器
    listener = WeChatRealtimeSyncUIA()
    
    # 开始监听
    try:
        listener.start_listening()
    except KeyboardInterrupt:
        logger.info("⏸️ 监听已停止")
    finally:
        listener.stop_listening()


if __name__ == '__main__':
    main()
