#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 图片OCR识别器 (Image OCR Recognizer)
使用OCR识别微信图片中的文字内容

功能:
1. 使用pillow加载图片
2. 使用pytesseract进行OCR识别（备用：调用WorkBuddy LLM Vision）
3. 解析OCR结果，提取文字内容
4. 识别图片中的股票代码/板块名称
5. 保存到舆情数据库

技术路线:
- 首选：调用WorkBuddy LLM Vision（图像理解）
- 备用：pytesseract OCR（需要安装Tesseract）
- 兜底：规则解析（简单的文字提取）

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
import base64
from pathlib import Path

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/image_ocr.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('ImageOCR')

class ImageOCRRecognizer:
    """图片OCR识别器"""
    
    def __init__(self, db_path: str = 'data/sentiment_db.db'):
        self.db_path = db_path
        
        # OCR引擎配置
        self.ocr_engine = 'llm_vision'  # llm_vision / tesseract / rule
        self.llm_available = False  # LLM Vision是否可用
        
        # 确保目录存在
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs('logs', exist_ok=True)
        
        # 初始化数据库
        self._init_db()
        
        logger.info("✅ 图片OCR识别器初始化完成")
    
    def _init_db(self):
        """初始化数据库表"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 创建OCR结果表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS image_ocr_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    image_path TEXT UNIQUE,
                    ocr_engine TEXT,
                    ocr_text TEXT,
                    extracted_stocks TEXT,  -- JSON array
                    extracted_keywords TEXT,  -- JSON array
                    confidence REAL,
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
    
    def recognize_image(self, image_path: str) -> Dict:
        """
        识别图片中的文字
        
        参数:
            image_path: 图片文件路径
            
        返回:
            Dict: {
                'ocr_text': str,  # 识别出的文字
                'extracted_stocks': List[str],  # 提取的股票代码
                'extracted_keywords': List[str],  # 提取的关键词
                'confidence': float,  # 置信度
                'ocr_engine': str,  # 使用的OCR引擎
            }
        """
        logger.info(f"📷 开始识别图片: {image_path}")
        
        if not os.path.exists(image_path):
            logger.error(f"❌ 图片文件不存在: {image_path}")
            return {
                'ocr_text': '',
                'extracted_stocks': [],
                'extracted_keywords': [],
                'confidence': 0.0,
                'ocr_engine': 'none',
            }
        
        # 根据配置的引擎选择识别方法
        if self.ocr_engine == 'llm_vision' and self.llm_available:
            result = self._recognize_with_llm_vision(image_path)
        elif self.ocr_engine == 'tesseract':
            result = self._recognize_with_tesseract(image_path)
        else:
            result = self._recognize_with_rule(image_path)
        
        # 保存到数据库
        self._save_ocr_result(image_path, result)
        
        logger.info(f"✅ 图片识别完成: {len(result['ocr_text'])} 字符, 置信度={result['confidence']:.2f}")
        return result
    
    def _recognize_with_llm_vision(self, image_path: str) -> Dict:
        """
        使用LLM Vision识别图片（最佳方案）
        
        注意：当前返回模拟结果，需通过自动化任务调用LLM Vision
        """
        logger.info("🤖 使用LLM Vision识别图片...")
        
        # TODO: 通过自动化任务调用LLM Vision
        # 当前：返回模拟结果
        
        # 模拟LLM Vision识别结果
        mock_result = {
            'ocr_text': '模拟LLM Vision识别结果：图片中包含股票代码601919（中远海控），板块名称航运概念。',
            'extracted_stocks': ['601919'],
            'extracted_keywords': ['中远海控', '航运概念', '涨停'],
            'confidence': 0.95,
            'ocr_engine': 'llm_vision',
        }
        
        logger.warning("⚠️ LLM Vision识别返回模拟结果，需通过自动化任务调用真实LLM")
        return mock_result
    
    def _recognize_with_tesseract(self, image_path: str) -> Dict:
        """
        使用Tesseract OCR识别图片
        
        需要安装:
        1. Tesseract OCR: https://github.com/tesseract-ocr/tesseract
        2. pytesseract: pip install pytesseract
        """
        logger.info("🔧 使用Tesseract OCR识别图片...")
        
        try:
            import pytesseract
            from PIL import Image
            
            # 打开图片
            img = Image.open(image_path)
            
            # OCR识别（中文+英文）
            ocr_text = pytesseract.image_to_string(img, lang='chi_sim+eng')
            
            # 提取股票代码和关键词
            extracted_stocks = self._extract_stock_codes(ocr_text)
            extracted_keywords = self._extract_keywords(ocr_text)
            
            result = {
                'ocr_text': ocr_text,
                'extracted_stocks': extracted_stocks,
                'extracted_keywords': extracted_keywords,
                'confidence': 0.8,  # Tesseract默认置信度
                'ocr_engine': 'tesseract',
            }
            
            logger.info(f"✅ Tesseract OCR识别完成: {len(ocr_text)} 字符")
            return result
            
        except ImportError:
            logger.warning("⚠️ pytesseract未安装，降级到规则识别")
            return self._recognize_with_rule(image_path)
        except Exception as e:
            logger.error(f"❌ Tesseract OCR识别失败: {e}")
            return self._recognize_with_rule(image_path)
    
    def _recognize_with_rule(self, image_path: str) -> Dict:
        """
        使用规则识别图片（兜底方案）
        
        仅提取图片文件名中的信息（如果文件名包含股票代码/关键词）
        """
        logger.info("📝 使用规则识别图片（兜底方案）...")
        
        # 从文件名提取信息
        filename = os.path.basename(image_path)
        
        # 提取股票代码（6位数字）
        stock_pattern = r'\b\d{6}\b'
        extracted_stocks = re.findall(stock_pattern, filename)
        
        # 提取关键词（简单规则）
        keywords = []
        keyword_list = ['涨停', '跌停', '上涨', '下跌', '买入', '卖出', '持有']
        for keyword in keyword_list:
            if keyword in filename:
                keywords.append(keyword)
        
        result = {
            'ocr_text': f'【规则识别】文件名: {filename}',
            'extracted_stocks': extracted_stocks,
            'extracted_keywords': keywords,
            'confidence': 0.3,  # 规则识别置信度较低
            'ocr_engine': 'rule',
        }
        
        logger.warning("⚠️ 规则识别仅提取文件名信息，准确度较低")
        return result
    
    def _extract_stock_codes(self, text: str) -> List[str]:
        """从文本中提取股票代码（6位数字）"""
        pattern = r'\b\d{6}\b'
        stocks = re.findall(pattern, text)
        
        # 去重
        stocks = list(set(stocks))
        
        # 验证是否为有效股票代码（简单规则：600xxx/601xxx/603xxx/000xxx/002xxx/300xxx）
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
        ]
        
        for keyword in keyword_list:
            if keyword in text:
                keywords.append(keyword)
        
        return keywords
    
    def _save_ocr_result(self, image_path: str, result: Dict):
        """保存OCR结果到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT OR REPLACE INTO image_ocr_results
                (image_path, ocr_engine, ocr_text, extracted_stocks, extracted_keywords, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                image_path,
                result['ocr_engine'],
                result['ocr_text'],
                json.dumps(result['extracted_stocks'], ensure_ascii=False),
                json.dumps(result['extracted_keywords'], ensure_ascii=False),
                result['confidence'],
            ))
            
            conn.commit()
            conn.close()
            
            logger.info(f"✅ OCR结果已保存: {image_path}")
            
        except Exception as e:
            logger.error(f"❌ 保存OCR结果失败: {e}")
    
    def batch_recognize(self, image_dir: str) -> List[Dict]:
        """
        批量识别图片
        
        参数:
            image_dir: 图片目录
            
        返回:
            List[Dict]: OCR结果列表
        """
        logger.info(f"📂 开始批量识别图片: {image_dir}")
        
        if not os.path.exists(image_dir):
            logger.error(f"❌ 目录不存在: {image_dir}")
            return []
        
        # 遍历目录中的图片文件
        image_extensions = ['.jpg', '.jpeg', '.png', '.bmp', '.gif']
        results = []
        
        for filename in os.listdir(image_dir):
            if any(filename.lower().endswith(ext) for ext in image_extensions):
                image_path = os.path.join(image_dir, filename)
                result = self.recognize_image(image_path)
                results.append(result)
        
        logger.info(f"✅ 批量识别完成: {len(results)} 张图片")
        return results
    
    def generate_llm_vision_prompt(self, image_path: str) -> str:
        """
        生成LLM Vision分析的提示词
        
        用于自动化任务中调用LLM Vision
        """
        prompt = f"""
请作为A股专业分析师，对以下图片进行深度分析：

【图片路径】
{image_path}

【分析要求】
1. 识别图片中的所有文字内容（OCR）
2. 提取图片中的股票代码/板块名称/价格信息
3. 分析图片传达的舆情信息（利好/利空/中性）
4. 评估该舆情对A股的影响程度（1-10分）
5. 给出投资建议（买入/卖出/持有/观望）

【输出格式】
严格按JSON格式输出，示例：
{{
  "ocr_text": "图片中的完整文字内容",
  "extracted_stocks": ["601919", "600018"],
  "extracted_keywords": ["航运", "涨停", "利好"],
  "sentiment": "bullish",  // bullish/bearish/neutral
  "impact_score": 8,
  "recommendation": "关注航运板块龙头股，可适当配置",
  "confidence": 0.95
}}

开始分析。
"""
        
        # 保存提示词到文件
        prompt_file = f"data/llm_vision_prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        
        logger.info(f"✅ LLM Vision提示词已生成: {prompt_file}")
        return prompt_file


def main():
    """主函数"""
    logger.info("🚀 启动图片OCR识别器...")
    
    # 创建识别器
    recognizer = ImageOCRRecognizer()
    
    # 测试：识别单张图片
    test_image = "data/wechat_images/test_image.jpg"
    
    if os.path.exists(test_image):
        result = recognizer.recognize_image(test_image)
        print(f"\n📊 识别结果:")
        print(f"  OCR文字: {result['ocr_text'][:100]}...")
        print(f"  股票代码: {result['extracted_stocks']}")
        print(f"  关键词: {result['extracted_keywords']}")
        print(f"  置信度: {result['confidence']:.2f}")
    else:
        logger.warning(f"⚠️ 测试图片不存在: {test_image}")
    
    # 生成LLM Vision提示词
    if os.path.exists(test_image):
        prompt_file = recognizer.generate_llm_vision_prompt(test_image)
        print(f"\n📝 LLM Vision提示词文件: {prompt_file}")


if __name__ == '__main__':
    main()
