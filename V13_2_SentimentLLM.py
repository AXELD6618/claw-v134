#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 LLM深度情感分析器 (Sentiment LLM Analyzer)
使用WorkBuddy内置LLM对关键舆情进行深度解读

功能:
1. 调用WorkBuddy LLM API进行深度情感分析
2. 生成投资建议和风险提示
3. 与规则分析器结果融合（LLM权重30% + 规则权重70%）
4. 批量处理未LLM分析的舆情
5. 提供LLM分析质量评估

分析维度:
- 情感倾向: 强烈看多/看多/中性/看空/强烈看空
- 影响强度: 0-100分
- 逻辑链条: 事件→影响→传导路径
- 受益/受损标的: 具体股票代码+名称
- 风险提示: 估值泡沫/预期透支/政策风险
- 持仓建议: 买入/持有/卖出/观望

数据源:
- 输入: sentiments_db.db 中的 news_processed 表
- 输出: sentiments_db.db 中的 llm_analysis 表

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
from dataclasses import dataclass, field

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_llm.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'
LLM_WEIGHT = 0.30       # LLM分析权重
RULE_WEIGHT = 0.70       # 规则分析权重
MIN_IMPORTANCE_FOR_LLM = 60.0  # 重要性阈值，超过才调用LLM
LLM_MODEL = 'workbuddy-builtin'  # WorkBuddy内置LLM

@dataclass
class LLMAnalysisResult:
    """LLM分析结果"""
    news_id: int
    sentiment_tendency: str  # 'strong_bullish'/'bullish'/'neutral'/'bearish'/'strong_bearish'
    sentiment_score_llm: float  # -1.0 ~ +1.0
    impact_score_llm: float    # 0-100
    logic_chain: str           # 逻辑链条
    beneficiary_stocks: List[Dict]  # [{"code": "601919", "name": "中远海控", "weight": 0.9}]
    risk_warnings: List[str]
    trading_advice: str        # 'buy'/'hold'/'sell'/'watch'
    advice_reason: str
    confidence: float          # 0-1 LLM置信度
    summary: str               # LLM生成的摘要
    raw_response: str = ''    # LLM原始响应
    analysis_time: str = ''
    
    def __post_init__(self):
        if not self.analysis_time:
            self.analysis_time = datetime.now().isoformat()


# ── 数据库操作 ─────────────────────────────────────────────────
def init_llm_table(db_path: str = DB_PATH):
    """初始化LLM分析表"""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # LLM分析结果表
    cur.execute('''
        CREATE TABLE IF NOT EXISTS llm_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER NOT NULL,
            sentiment_tendency TEXT,
            sentiment_score_llm REAL,
            impact_score_llm REAL,
            logic_chain TEXT,
            beneficiary_stocks TEXT,  -- JSON
            risk_warnings TEXT,       -- JSON
            trading_advice TEXT,
            advice_reason TEXT,
            confidence REAL,
            summary TEXT,
            raw_response TEXT,
            analysis_time TEXT,
            used_prompt_tokens INTEGER,
            used_completion_tokens INTEGER,
            Fusion_score_updated INTEGER DEFAULT 0,  -- 是否已更新融合评分
            FOREIGN KEY (news_id) REFERENCES news_processed(id)
        )
    ''')
    
    # 添加LLM字段到news_processed表（如果不存在）
    try:
        cur.execute('ALTER TABLE news_processed ADD COLUMN llm_analyzed INTEGER DEFAULT 0')
        cur.execute('ALTER TABLE news_processed ADD COLUMN llm_sentiment_score REAL DEFAULT 0.0')
        cur.execute('ALTER TABLE news_processed ADD COLUMN llm_impact_score REAL DEFAULT 0.0')
        cur.execute('ALTER TABLE news_processed ADD COLUMN llm_trading_advice TEXT')
    except:
        pass  # 字段已存在
    
    conn.commit()
    conn.close()
    logger.info(f"✅ LLM分析表初始化完成")


def get_unanalyzed_news(db_path: str = DB_PATH, 
                          min_importance: float = MIN_IMPORTANCE_FOR_LLM,
                          limit: int = 20) -> List[Dict]:
    """获取未LLM分析的重要新闻"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    cur.execute('''
        SELECT p.*, r.title, r.content, r.source, r.publish_time
        FROM news_processed p
        JOIN news_raw r ON r.id = p.raw_id
        WHERE p.importance_score >= ?
        AND (p.llm_analyzed IS NULL OR p.llm_analyzed = 0)
        ORDER BY p.importance_score DESC
        LIMIT ?
    ''', (min_importance, limit))
    
    rows = cur.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]


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
        
        # 更新news_processed表
        cur.execute('''
            UPDATE news_processed
            SET llm_analyzed = 1,
                llm_sentiment_score = ?,
                llm_impact_score = ?,
                llm_trading_advice = ?
            WHERE id = (SELECT id FROM news_processed WHERE raw_id = ?)
        ''', (
            result.sentiment_score_llm,
            result.impact_score_llm,
            result.trading_advice,
            result.news_id
        ))
        
        conn.commit()
        logger.info(f"✅ LLM分析已保存: news_id={result.news_id} | 建议={result.trading_advice}")
        
    except Exception as e:
        logger.error(f"❌ 保存LLM分析失败: {e}")
        conn.rollback()
        
    finally:
        conn.close()


def fusion_with_llm(news_id: int, 
                     rule_sentiment: float, 
                     rule_impact: float,
                     llm_sentiment: float,
                     llm_impact: float) -> Tuple[float, float]:
    """
    融合规则分析和LLM分析结果
    
    返回: (fused_sentiment, fused_impact)
    """
    fused_sentiment = rule_sentiment * RULE_WEIGHT + llm_sentiment * LLM_WEIGHT
    fused_impact = rule_impact * RULE_WEIGHT + llm_impact * LLM_WEIGHT
    
    logger.info(
        f"🎯 评分融合: news_id={news_id} | "
        f"规则情感={rule_sentiment:+.2f} LLM情感={llm_sentiment:+.2f} → "
        f"融合={fused_sentiment:+.2f} | "
        f"规则影响={rule_impact:.0f} LLM影响={llm_impact:.0f} → "
        f"融合={fused_impact:.0f}"
    )
    
    return fused_sentiment, fused_impact


# ── LLM提示词模板 ─────────────────────────────────────────────
def build_llm_prompt(title: str, content: str, source: str = '', 
                     publish_time: str = '') -> str:
    """
    构建LLM分析提示词
    
    返回: 完整的prompt字符串
    """
    prompt = f"""# A股舆情深度分析任务

你是一位顶级的A股首席策略分析师，需要对以下财经舆情进行深度解读，并给出精准的投资建议。

## 舆情信息
- 标题: {title}
- 来源: {source}
- 发布时间: {publish_time}
- 正文:
{content[:2000]}  # 限制长度

## 分析要求

请从以下维度进行深度分析，并以**严格JSON格式**输出结果：

### 1. 情感倾向 (sentiment_tendency)
- "strong_bullish": 强烈看多（重大利好，影响持久）
- "bullish": 看多（明显利好）
- "neutral": 中性（影响有限或方向不明）
- "bearish": 看空（明显利空）
- "strong_bearish": 强烈看空（重大利空）

### 2. 情感得分 (sentiment_score)
- 范围: -1.0（极度利空）~ +1.0（极度利好）
- 精确到小数点后2位

### 3. 影响强度 (impact_score)
- 范围: 0-100分
- 评估该舆情对A股相关板块/个股的影响程度
- 90+: 重磅利好/利空，可能引发板块性行情
- 70-90: 明显利好/利空，相关个股会有显著反应
- 50-70: 中等影响，短期有交易性机会
- 30-50: 轻微影响，需要结合其他因子
- <30: 影响微弱

### 4. 逻辑链条 (logic_chain)
用1-2句话说明：该事件 → 影响哪些环节 → 传导路径 → 最终影响

### 5. 受益/受损标的 (beneficiary_stocks)
识别最可能受益或受损的A股标的，每个标的包含：
- "code": 6位股票代码
- "name": 股票名称
- "weight": 受益程度 0.0-1.0
- "reason": 为什么受益/受损（简短）

### 6. 风险提示 (risk_warnings)
列出2-3条可能的风险因素，例如：
- 估值已高，预期透支
- 政策落地不及预期
- 市场情绪退潮后回调风险

### 7. 交易建议 (trading_advice)
- "strong_buy": 强烈买入（确定性高，空间大）
- "buy": 买入（有确定性，但需择时）
- "hold": 持有（已持仓可持有，暂不追高）
- "watch": 观望（等待回调或更多确认信号）
- "sell": 卖出（利空明确，规避风险）

### 8. 建议理由 (advice_reason)
简述给出该交易建议的核心逻辑（1-2句话）

### 9. 置信度 (confidence)
你对本次分析的置信度 0.0-1.0
- 0.9+: 信息充分，逻辑清晰，确定性高
- 0.7-0.9: 信息较充分，有一定确定性
- 0.5-0.7: 信息有限，不确定性较大
- <0.5: 信息不足，建议谨慎

### 10. 摘要 (summary)
用1句话概括本舆情的核心要点和投资启示

## 输出格式

**必须严格按照以下JSON格式输出**，不要输出任何解释性文字：

```json
{{
    "sentiment_tendency": "bullish",
    "sentiment_score": 0.75,
    "impact_score": 82.0,
    "logic_chain": "霍尔木兹海峡恢复通航 → 航运成本下降 → 原油运输效率提升 → 炼化企业成本降低 → 石油石化板块短期承压",
    "beneficiary_stocks": [
        {{"code": "601857", "name": "中国石油", "weight": 0.3, "reason": "海峡通航增加原油供应，油价承压}},
        {{"code": "600028", "name": "中国石化", "weight": 0.4, "reason": "炼化成本下降，毛利改善"}}
    ],
    "risk_warnings": [
        "OPEC+可能减产对冲通航影响",
        "通航协议执行力度存疑",
        "地缘风险未完全解除"
    ],
    "trading_advice": "watch",
    "advice_reason": "短期油价承压但幅度有限，建议观望等待情绪稳定后再介入",
    "confidence": 0.72,
    "summary": "霍尔木兹通航短期利空石油板块，但影响有限，建议观望"
}}
```

现在，请开始分析。
"""
    return prompt


# ── LLM调用接口 ───────────────────────────────────────────────
def call_workbuddy_llm(prompt: str) -> Tuple[bool, str]:
    """
    调用WorkBuddy内置LLM
    
    返回: (success, response_text)
    
    注意: 此函数需要在WorkBuddy自动化任务中调用，
    因为只有在自动化任务的prompt中才能访问LLM。
    
    在Python代码中，此函数会：
    1. 将prompt写入临时文件
    2. 触发自动化任务（该任务会读取文件并调用LLM）
    3. 等待结果文件
    
    或者，可以直接在当前Python进程中通过
    WorkBuddy提供的Python SDK调用LLM（如果有的话）。
    """
    # TODO: 实现WorkBuddy LLM调用接口
    # 当前版本：返回模拟响应，实际应通过自动化任务调用LLM
    
    logger.warning("⚠️ LLM调用接口尚未完全实现，返回模拟响应")
    
    # 模拟响应（实际应调用LLM）
    mock_response = json.dumps({{
        "sentiment_tendency": "bullish",
        "sentiment_score": 0.65,
        "impact_score": 75.0,
        "logic_chain": "模拟逻辑链条",
        "beneficiary_stocks": [],
        "risk_warnings": ["模拟风险"],
        "trading_advice": "watch",
        "advice_reason": "模拟理由",
        "confidence": 0.70,
        "summary": "模拟摘要"
    }}, ensure_ascii=False)
    
    return True, mock_response


def parse_llm_response(response_text: str) -> Optional[LLMAnalysisResult]:
    """
    解析LLM响应文本，提取JSON
    
    返回: LLMAnalysisResult对象，解析失败返回None
    """
    try:
        # 尝试提取JSON（可能包含在```json ... ```中）
        import re
        
        # 方法1: 直接解析
        try:
            data = json.loads(response_text)
        except:
            # 方法2: 提取```json ... ```中的内容
            match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
            if match:
                data = json.loads(match.group(1))
            else:
                # 方法3: 提取{{...}}中的内容
                match = re.search(r'\{\{.*\}\}', response_text, re.DOTALL)
                if match:
                    # 替换{{为{（LLM可能错误使用了双括号）
                    json_str = match.group(0).replace('{{', '{').replace('}}', '}')
                    data = json.loads(json_str)
                else:
                    raise ValueError("无法从响应中提取JSON")
        
        # 构造结果对象
        result = LLMAnalysisResult(
            news_id=0,  # 需要外部设置
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
            raw_response=response_text
        )
        
        logger.info(f"✅ LLM响应解析成功: 建议={result.trading_advice} 置信度={result.confidence:.2f}")
        return result
        
    except Exception as e:
        logger.error(f"❌ LLM响应解析失败: {e}")
        logger.debug(f"响应文本: {response_text[:500]}")
        return None


# ── 主分析器类 ───────────────────────────────────────────────
class SentimentLLMAnalyzer:
    """LLM深度情感分析器（主类）"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        init_llm_table(db_path)
        logger.info(f"✅ LLM分析器初始化完成: {db_path}")
        
    def analyze_one(self, news: Dict) -> Optional[LLMAnalysisResult]:
        """
        对单条新闻进行LLM深度分析
        
        返回: LLMAnalysisResult对象，失败返回None
        """
        news_id = news['id']
        title = news.get('title', '')
        content = news.get('content', '') or news.get('summary', '')
        source = news.get('source', '')
        publish_time = news.get('publish_time', '')
        
        # 1. 构建prompt
        prompt = build_llm_prompt(title, content, source, publish_time)
        
        logger.info(f"🤖 开始LLM分析: {title[:40]}...")
        
        # 2. 调用LLM
        success, response_text = call_workbuddy_llm(prompt)
        
        if not success:
            logger.error(f"❌ LLM调用失败: {title[:40]}")
            return None
            
        # 3. 解析响应
        result = parse_llm_response(response_text)
        
        if result is None:
            logger.error(f"❌ LLM响应解析失败: {title[:40]}")
            return None
            
        # 4. 设置news_id
        result.news_id = news_id
        
        # 5. 保存结果
        save_llm_analysis(result, self.db_path)
        
        return result
    
    def analyze_batch(self, min_importance: float = MIN_IMPORTANCE_FOR_LLM,
                      limit: int = 20) -> int:
        """
        批量分析未LLM分析的重要新闻
        
        返回: 成功分析数量
        """
        # 1. 获取未分析的新闻
        news_list = get_unanalyzed_news(
            db_path=self.db_path,
            min_importance=min_importance,
            limit=limit
        )
        
        if not news_list:
            logger.info("没有未LLM分析的重要新闻")
            return 0
            
        logger.info(f"🤖 开始批量LLM分析 {len(news_list)} 条新闻...")
        
        # 2. 逐条分析
        success_count = 0
        for news in news_list:
            try:
                result = self.analyze_one(news)
                if result:
                    success_count += 1
            except Exception as e:
                logger.error(f"❌ 分析失败: {news.get('title', '')} | 错误: {e}")
        
        logger.info(f"✅ 批量LLM分析完成: 成功={success_count}/{len(news_list)}")
        
        return success_count
    
    def fusion_score(self, news_id: int) -> Tuple[float, float]:
        """
        融合规则分析和LLM分析的评分
        
        返回: (fused_sentiment, fused_impact)
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        # 获取规则分析结果
        cur.execute('''
            SELECT sentiment_score, impact_score, 
                   llm_sentiment_score, llm_impact_score
            FROM news_processed
            WHERE raw_id = ?
        ''', (news_id,))
        
        row = cur.fetchone()
        conn.close()
        
        if not row:
            logger.warning(f"新闻不存在: news_id={news_id}")
            return 0.0, 0.0
            
        rule_sentiment = row['sentiment_score'] or 0.0
        rule_impact = row['impact_score'] or 0.0
        llm_sentiment = row['llm_sentiment_score'] or 0.0
        llm_impact = row['llm_impact_score'] or 0.0
        
        # 融合
        return fusion_with_llm(
            news_id, rule_sentiment, rule_impact,
            llm_sentiment, llm_impact
        )
    
    def get_latest_llm_analysis(self, limit: int = 10) -> List[Dict]:
        """获取最新的LLM分析结果"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        
        cur.execute('''
            SELECT l.*, p.title, p.importance_score, r.publish_time, r.source
            FROM llm_analysis l
            JOIN news_processed p ON p.id = l.news_id
            JOIN news_raw r ON r.id = p.raw_id
            ORDER BY l.analysis_time DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cur.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]


# ── 命令行接口 ───────────────────────────────────────────────
def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='V13.2 LLM深度情感分析器')
    parser.add_argument('--analyze-batch', type=int, default=20, help='批量分析未LLM分析的重要新闻')
    parser.add_argument('--min-importance', type=float, default=MIN_IMPORTANCE_FOR_LLM, help='最低重要性分数')
    parser.add_argument('--analyze-one', type=int, help='分析指定news_id的新闻')
    parser.add_argument('--fusion', type=int, help='融合指定news_id的评分')
    parser.add_argument('--latest', type=int, default=10, help='显示最新的LLM分析')
    parser.add_argument('--db-path', type=str, default=DB_PATH, help='数据库路径')
    
    args = parser.parse_args()
    
    # 初始化分析器
    analyzer = SentimentLLMAnalyzer(db_path=args.db_path)
    
    if args.analyze_batch:
        # 批量分析
        count = analyzer.analyze_batch(
            min_importance=args.min_importance,
            limit=args.analyze_batch
        )
        print(f"\n{'=' * 70}")
        print(f"  LLM批量分析完成")
        print(f"{'=' * 70}")
        print(f"  分析数量: {count} 条")
        print(f"{'=' * 70}\n")
        
    elif args.analyze_one:
        # 分析单条
        # TODO: 从数据库加载指定news_id的新闻
        print(f"分析单条新闻: news_id={args.analyze_one}")
        print("TODO: 实现此功能")
        
    elif args.fusion:
        # 融合评分
        fused_sentiment, fused_impact = analyzer.fusion_score(args.fusion)
        print(f"\n{'=' * 70}")
        print(f"  评分融合结果: news_id={args.fusion}")
        print(f"{'=' * 70}")
        print(f"  融合情感得分: {fused_sentiment:+.2f}")
        print(f"  融合影响得分: {fused_impact:.0f} 分")
        print(f"{'=' * 70}\n")
        
    elif args.latest:
        # 显示最新分析
        latest = analyzer.get_latest_llm_analysis(limit=args.latest)
        
        print(f"\n{'=' * 70}")
        print(f"  最新的 {len(latest)} 条LLM分析")
        print(f"{'=' * 70}")
        
        for i, analysis in enumerate(latest, 1):
            print(f"\n{i}. {analysis['title'][:50]}...")
            print(f"   情感: {analysis['sentiment_tendency']} ({analysis['sentiment_score_llm']:+.2f})")
            print(f"   影响: {analysis['impact_score_llm']:.0f}分")
            print(f"   建议: {analysis['trading_advice']}")
            print(f"   置信度: {analysis['confidence']:.2f}")
            print(f"   摘要: {analysis['summary'][:80]}...")
            
        print(f"\n{'=' * 70}\n")
        
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
