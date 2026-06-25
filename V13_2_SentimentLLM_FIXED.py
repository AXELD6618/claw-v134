#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 LLM深度情感分析器 (Sentiment LLM Analyzer) - 修正版
集成自动化任务调用LLM

关键修改:
1. 添加 generate_automation_task_prompt() 方法（生成自动化任务提示词）
2. 添加 process_llm_result_file() 方法（处理LLM分析结果文件）
3. 修改 call_workbuddy_llm() 方法（写入提示词文件，供自动化任务读取）

使用方式:
- 自动化任务会读取提示词文件 → 调用LLM → 将结果写入结果文件
- Python代码读取结果文件 → 解析 → 存入数据库

版本: V13.2-FIXED
创建: 2026-06-24
"""

import os
import sys
import json
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_llm_fixed.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'
LLM_PROMPT_FILE = 'data/llm_prompt.txt'   # LLM分析提示词文件（供自动化任务读取）
LLM_RESULT_FILE = 'data/llm_result.json'  # LLM分析结果文件（由自动化任务写入）
LLM_WEIGHT = 0.30
RULE_WEIGHT = 0.70
MIN_IMPORTANCE_FOR_LLM = 60.0


@dataclass
class LLMAnalysisResult:
    """LLM分析结果"""
    news_id: int
    sentiment_tendency: str
    sentiment_score_llm: float
    impact_score_llm: float
    logic_chain: str
    beneficiary_stocks: List[Dict]
    risk_warnings: List[str]
    trading_advice: str
    advice_reason: str
    confidence: float
    summary: str
    raw_response: str = ''
    analysis_time: str = ''
    
    def __post_init__(self):
        if not self.analysis_time:
            self.analysis_time = datetime.now().isoformat()


# ── 关键修改1: 生成自动化任务提示词 ─────────────────────────────
def generate_automation_task_prompt(news_list: List[Dict]) -> str:
    """
    生成自动化任务的提示词（供自动化任务调用LLM分析舆情）
    
    参数:
    - news_list: 未LLM分析的重要新闻列表
    
    返回: 自动化任务的prompt字符串
    
    使用方式:
    1. Python代码调用此函数生成prompt
    2. 将prompt写入文件（或直接作为自动化任务的prompt）
    3. 自动化任务执行时，LLM会分析舆情
    4. 将LLM分析结果写入 LLM_RESULT_FILE
    """
    if not news_list:
        return "没有需要LLM分析的舆情。"
    
    prompt = f"""# A股舆情LLM深度分析任务

你是一位顶级的A股首席策略分析师，需要对以下 {len(news_list)} 条财经舆情进行深度解读。

## 待分析舆情列表

"""
    
    for idx, news in enumerate(news_list):
        prompt += f"""
### 舆情 {idx+1}
- ID: {news['id']}
- 标题: {news.get('title', '')}
- 来源: {news.get('source', '')}
- 发布时间: {news.get('publish_time', '')}
- 内容摘要: {news.get('content', '')[:500]}

"""
    
    prompt += """
## 分析要求

对每条舆情，请从以下维度进行深度分析，并以**严格JSON格式**输出结果：

### 输出格式

对每条舆情，输出一个JSON对象（用---分隔）：

```
---
舆情ID: 1
```json
{
    "news_id": 1,
    "sentiment_tendency": "bullish",
    "sentiment_score": 0.75,
    "impact_score": 82.0,
    "logic_chain": "...",
    "beneficiary_stocks": [...],
    "risk_warnings": [...],
    "trading_advice": "buy",
    "advice_reason": "...",
    "confidence": 0.72,
    "summary": "..."
}
```
---

现在，请开始分析。
"""
    
    return prompt


# ── 关键修改2: 写入提示词文件 ─────────────────────────────────
def write_llm_prompt_file(news_list: List[Dict], 
                           prompt_file: str = LLM_PROMPT_FILE) -> bool:
    """
    将LLM分析提示词写入文件（供自动化任务读取）
    
    返回: 成功True/失败False
    """
    try:
        prompt = generate_automation_task_prompt(news_list)
        
        os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        
        logger.info(f"✅ LLM提示词已写入: {prompt_file} | 舆情数={len(news_list)}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 写入LLM提示词失败: {e}")
        return False


# ── 关键修改3: 处理LLM结果文件 ─────────────────────────────────
def process_llm_result_file(result_file: str = LLM_RESULT_FILE,
                             db_path: str = DB_PATH) -> int:
    """
    处理LLM分析结果文件，将结果存入数据库
    
    返回: 成功处理数量
    """
    if not os.path.exists(result_file):
        logger.warning(f"⚠️ LLM结果文件不存在: {result_file}")
        return 0
    
    try:
        with open(result_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析JSON（可能包含多个JSON对象，用---分隔）
        import re
        json_blocks = re.findall(r'```json\s*(.*?)\s*```', content, re.DOTALL)
        
        if not json_blocks:
            # 尝试直接解析整个文件
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    json_blocks = [json.dumps(item) for item in data]
                else:
                    json_blocks = [content]
            except:
                logger.error("❌ 无法从结果文件中提取JSON")
                return 0
        
        # 处理每个JSON对象
        success_count = 0
        for block in json_blocks:
            try:
                data = json.loads(block)
                
                # 构造LLMAnalysisResult
                result = LLMAnalysisResult(
                    news_id=data.get('news_id', 0),
                    sentiment_tendency=data.get('sentiment_tendency', 'neutral'),
                    sentiment_score_llm=float(data.get('sentiment_score', 0.0)),
                    impact_score_llm=float(data.get('impact_score', 0.0)),
                    logic_chain=data.get('logic_chain', ''),
                    beneficiary_stocks=data.get('beneficiary_stocks', []),
                    risk_warnings=data.get('risk_warnings', []),
                    trading_advice=data.get('trading_advice', 'watch'),
                    advice_reason=data.get('advice_reason', ''),
                    confidence=float(data.get('confidence', 0.5)),
                    summary=data.get('summary', ''),
                    raw_response=block,
                )
                
                # 保存到数据库
                save_llm_analysis(result, db_path)
                success_count += 1
                
            except Exception as e:
                logger.error(f"❌ 处理LLM结果失败: {e}")
                continue
        
        logger.info(f"✅ LLM结果处理完成: 成功={success_count}/{len(json_blocks)}")
        
        # 删除结果文件（避免重复处理）
        os.remove(result_file)
        
        return success_count
        
    except Exception as e:
        logger.error(f"❌ 处理LLM结果文件失败: {e}")
        return 0


# ── 数据库操作（简化版） ──────────────────────────────────────
def init_llm_table(db_path: str = DB_PATH):
    """初始化LLM分析表"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS llm_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER NOT NULL,
            sentiment_tendency TEXT,
            sentiment_score_llm REAL,
            impact_score_llm REAL,
            logic_chain TEXT,
            beneficiary_stocks TEXT,
            risk_warnings TEXT,
            trading_advice TEXT,
            advice_reason TEXT,
            confidence REAL,
            summary TEXT,
            raw_response TEXT,
            analysis_time TEXT,
            FOREIGN KEY (news_id) REFERENCES news_processed(id)
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info(f"✅ LLM分析表初始化完成")


def save_llm_analysis(result: LLMAnalysisResult, db_path: str = DB_PATH):
    """保存LLM分析结果"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    try:
        cur.execute('''
            INSERT OR REPLACE INTO llm_analysis
            (news_id, sentiment_tendency, sentiment_score_llm, impact_score_llm,
             logic_chain, beneficiary_stocks, risk_warnings, trading_advice,
             advice_reason, confidence, summary, raw_response, analysis_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result.news_id,
            result.sentiment_tendency,
            result.sentiment_score_llm,
            result.impact_score_llm,
            result.logic_chain,
            json.dumps(result.beneficiary_stocks, ensure_ascii=False),
            json.dumps(result.risk_warnings, ensure_ascii=False),
            result.trading_advice,
            result.advice_reason,
            result.confidence,
            result.summary,
            result.raw_response,
            result.analysis_time
        ))
        
        conn.commit()
        logger.info(f"✅ LLM分析已保存: news_id={result.news_id}")
        
    except Exception as e:
        logger.error(f"❌ 保存LLM分析失败: {e}")
        conn.rollback()
    finally:
        conn.close()


# ── 主分析器类 ───────────────────────────────────────────────
class SentimentLLMAnalyzerFixed:
    """LLM深度情感分析器（修正版）"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        init_llm_table(db_path)
        logger.info(f"✅ LLM分析器（修正版）初始化完成")
        
    def prepare_llm_analysis(self, min_importance: float = MIN_IMPORTANCE_FOR_LLM,
                               limit: int = 20) -> bool:
        """
        准备LLM分析：生成提示词文件（供自动化任务读取）
        
        返回: 成功True/失败False
        """
        # 获取未分析的新闻
        news_list = self._get_unanalyzed_news(min_importance, limit)
        
        if not news_list:
            logger.info("没有未LLM分析的重要新闻")
            return False
        
        # 生成提示词文件
        success = write_llm_prompt_file(news_list)
        
        if success:
            logger.info(f"🤖 已生成LLM分析提示词文件，请执行自动化任务进行LLM分析")
            logger.info(f"   提示词文件: {LLM_PROMPT_FILE}")
            logger.info(f"   待分析舆情数: {len(news_list)}")
        
        return success
    
    def process_llm_results(self) -> int:
        """
        处理LLM分析结果（由自动化任务生成的结果文件）
        
        返回: 成功处理数量
        """
        return process_llm_result_file(self.db_path)
    
    def _get_unanalyzed_news(self, min_importance: float = MIN_IMPORTANCE_FOR_LLM,
                              limit: int = 20) -> List[Dict]:
        """获取未LLM分析的重要新闻（模拟）"""
        # 模拟数据（实际应从数据库读取）
        return [
            {
                'id': 1,
                'title': '霍尔木兹海峡恢复通航',
                'content': '据新华社消息...',
                'source': '新华社',
                'publish_time': datetime.now().isoformat(),
            }
        ]


# ── 测试函数 ────────────────────────────────────────────────
if __name__ == '__main__':
    print("🚀 V13.2 LLM深度情感分析器 (修正版) 测试")
    print("="*60)
    
    analyzer = SentimentLLMAnalyzerFixed()
    
    # 测试1: 准备LLM分析（生成提示词文件）
    print("\n🧪 测试1: 准备LLM分析...")
    analyzer.prepare_llm_analysis()
    
    # 测试2: 模拟LLM分析结果（写入结果文件）
    print("\n🧪 测试2: 模拟LLM分析结果...")
    mock_result = {
        "news_id": 1,
        "sentiment_tendency": "bullish",
        "sentiment_score": 0.75,
        "impact_score": 82.0,
        "logic_chain": "霍尔木兹通航 → 航运成本下降 → 油价承压",
        "beneficiary_stocks": [],
        "risk_warnings": ["地缘政治风险未完全解除"],
        "trading_advice": "watch",
        "advice_reason": "短期油价承压但幅度有限",
        "confidence": 0.72,
        "summary": "霍尔木兹通航短期利空石油板块"
    }
    
    with open(LLM_RESULT_FILE, 'w', encoding='utf-8') as f:
        f.write(f"```json\n{json.dumps(mock_result, ensure_ascii=False)}\n```")
    
    print(f"  模拟结果已写入: {LLM_RESULT_FILE}")
    
    # 测试3: 处理LLM分析结果
    print("\n🧪 测试3: 处理LLM分析结果...")
    count = analyzer.process_llm_results()
    print(f"  成功处理: {count} 条")
    
    print("\n✅ 测试完成")
