#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.2 舆情信号回测框架 (Sentiment Backtest) - 修正版
集成TDX MCP获取历史行情

关键修改:
1. 添加 generate_tdx_fetch_prompt() 方法（生成TDX调用提示词）
2. 添加 fetch_historical_data_via_tdx() 方法（通过自动化任务调用TDX）
3. 修改 _simulate_trade() 方法（使用真实历史行情）

使用方式:
- 自动化任务会读取提示词文件 → 调用TDX MCP → 将历史行情写入结果文件
- Python代码读取结果文件 → 解析 → 用于回测

版本: V13.2-FIXED
创建: 2026-06-24
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('logs/sentiment_backtest_fixed.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ── 配置 ─────────────────────────────────────────────────────
DB_PATH = 'data/sentiment_db.db'
TDX_PROMPT_FILE = 'data/tdx_fetch_prompt.txt'   # TDX调用提示词文件
TDX_RESULT_FILE = 'data/tdx_historical_data.json'  # TDX返回的历史行情数据


@dataclass
class BacktestResult:
    """回测结果"""
    signal_id: int
    news_id: int
    stock_code: str
    signal_time: str
    sentiment_score: float
    
    # 回测结果
    buy_price: float = 0.0
    sell_price: float = 0.0
    hold_days: int = 1
    pnl_pct: float = 0.0
    is_hit: bool = False
    is_limit_up: bool = False
    
    # 评级
    rating: str = ''  # 'STRONG_HIT' / 'HIT' / 'MISS' / 'LOSS'


# ── 关键修改1: 生成TDX调用提示词 ─────────────────────────────
def generate_tdx_fetch_prompt(stock_codes: List[str], 
                               start_date: str, 
                               end_date: str) -> str:
    """
    生成TDX MCP调用提示词（供自动化任务读取）
    
    参数:
    - stock_codes: 股票代码列表
    - start_date: 开始日期 '2026-06-01'
    - end_date: 结束日期 '2026-06-24'
    
    返回: 自动化任务的prompt字符串
    """
    prompt = f"""# TDX历史行情数据获取任务

请使用TDX MCP工具获取以下股票的历史K线数据，并将结果保存到文件。

## 股票列表

"""
    
    for code in stock_codes:
        prompt += f"- {code}\n"
    
    prompt += f"""
## 时间范围

- 开始日期: {start_date}
- 结束日期: {end_date}
- K线周期: 日线 (D)

## 任务步骤

1. 对每只股票，调用 `tdx_kline` 工具获取历史K线
2. 提取以下字段: date, open, high, low, close, volume, amount
3. 将所有股票的数据合并，保存到文件: `{TDX_RESULT_FILE}`
4. 数据格式: JSON数组，每个元素是一只股票的数据

## 输出格式

```json
[
    {{
        "code": "601919",
        "name": "中远海控",
        "kline": [
            {{"date": "2026-06-01", "open": 10.5, "high": 10.8, "low": 10.3, "close": 10.6, "volume": 1000000, "amount": 106000000}},
            ...
        ]
    }},
    ...
]
```

现在，请开始执行。
"""
    
    return prompt


# ── 关键修改2: 写入TDX提示词文件 ──────────────────────────────
def write_tdx_prompt_file(stock_codes: List[str],
                           start_date: str = '2026-06-01',
                           end_date: str = '2026-06-24',
                           prompt_file: str = TDX_PROMPT_FILE) -> bool:
    """将TDX调用提示词写入文件"""
    try:
        prompt = generate_tdx_fetch_prompt(stock_codes, start_date, end_date)
        
        os.makedirs(os.path.dirname(prompt_file), exist_ok=True)
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(prompt)
        
        logger.info(f"✅ TDX提示词已写入: {prompt_file} | 股票数={len(stock_codes)}")
        return True
        
    except Exception as e:
        logger.error(f"❌ 写入TDX提示词失败: {e}")
        return False


# ── 关键修改3: 处理TDX结果文件 ────────────────────────────────
def process_tdx_result_file(result_file: str = TDX_RESULT_FILE) -> Dict:
    """
    处理TDX返回的历史行情文件
    
    返回: {stock_code: [kline_dict, ...]}
    """
    if not os.path.exists(result_file):
        logger.warning(f"⚠️ TDX结果文件不存在: {result_file}")
        return {}
    
    try:
        with open(result_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 转换为字典
        result = {}
        for stock_data in data:
            code = stock_data.get('code', '')
            kline = stock_data.get('kline', [])
            result[code] = kline
        
        logger.info(f"✅ TDX结果处理完成: 股票数={len(result)}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ 处理TDX结果文件失败: {e}")
        return {}


# ── 关键修改4: 使用真实历史行情回测 ────────────────────────────
def backtest_with_real_data(signal_id: int,
                             stock_code: str,
                             signal_time: str,
                             historical_data: Dict) -> BacktestResult:
    """
    使用真实历史行情回测
    
    参数:
    - signal_id: 信号ID
    - stock_code: 股票代码
    - signal_time: 信号时间
    - historical_data: TDX返回的历史行情 {code: [kline, ...]}
    
    返回: BacktestResult
    """
    result = BacktestResult(
        signal_id=signal_id,
        news_id=0,
        stock_code=stock_code,
        signal_time=signal_time,
        sentiment_score=0.0,
    )
    
    # 获取该股票的历史行情
    kline_data = historical_data.get(stock_code, [])
    
    if not kline_data:
        logger.warning(f"⚠️ 无历史行情数据: {stock_code}")
        return result
    
    # 找到信号时间后的第一天
    signal_date = signal_time[:10]
    
    buy_kline = None
    sell_kline = None
    
    for kline in kline_data:
        kline_date = kline.get('date', '')
        
        if kline_date > signal_date:
            if buy_kline is None:
                buy_kline = kline  # T+1开盘价
            elif sell_kline is None:
                sell_kline = kline  # T+2收盘价
                break
    
    if buy_kline is None:
        logger.warning(f"⚠️ 未找到买入K线: {stock_code}")
        return result
    
    # 计算收益率
    result.buy_price = buy_kline.get('open', 0.0) or buy_kline.get('close', 0.0)
    result.sell_price = sell_kline.get('close', 0.0) if sell_kline else buy_kline.get('close', 0.0)
    
    if result.buy_price > 0:
        result.pnl_pct = (result.sell_price - result.buy_price) / result.buy_price * 100
    
    # 判断命中
    result.is_hit = result.pnl_pct > 0
    result.is_limit_up = result.pnl_pct >= 9.5
    
    # 评级
    if result.is_limit_up:
        result.rating = 'STRONG_HIT'
    elif result.is_hit:
        result.rating = 'HIT'
    elif result.pnl_pct < -3:
        result.rating = 'LOSS'
    else:
        result.rating = 'MISS'
    
    result.hold_days = 1  # 简化
    
    logger.info(
        f"📊 回测 {stock_code}: "
        f"买入={result.buy_price:.2f} 卖出={result.sell_price:.2f} "
        f"收益率={result.pnl_pct:+.2f}% 评级={result.rating}"
    )
    
    return result


# ── 主回测器类 ───────────────────────────────────────────────
class SentimentBacktesterFixed:
    """舆情信号回测器（修正版）"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        logger.info(f"✅ 回测器（修正版）初始化完成")
        
    def prepare_tdx_data_fetch(self, stock_codes: List[str]) -> bool:
        """
        准备TDX数据获取：生成提示词文件（供自动化任务读取）
        
        返回: 成功True/失败False
        """
        if not stock_codes:
            logger.warning("⚠️ 股票列表为空")
            return False
        
        # 生成提示词文件
        success = write_tdx_prompt_file(stock_codes)
        
        if success:
            logger.info(f"📊 已生成TDX数据获取提示词文件，请执行自动化任务获取历史行情")
            logger.info(f"   提示词文件: {TDX_PROMPT_FILE}")
            logger.info(f"   股票数: {len(stock_codes)}")
        
        return success
    
    def run_backtest_with_tdx_data(self) -> int:
        """
        使用TDX历史行情数据回测
        
        返回: 成功回测数量
        """
        # 1. 处理TDX结果文件
        historical_data = process_tdx_result_file()
        
        if not historical_data:
            logger.warning("⚠️ 无TDX历史行情数据，使用模拟数据")
            # TODO: 使用模拟数据
            return 0
        
        # 2. 获取回测信号（从数据库）
        signals = self._get_backtest_signals()
        
        # 3. 逐条回测
        success_count = 0
        for signal in signals:
            try:
                result = backtest_with_real_data(
                    signal['id'],
                    signal['stock_code'],
                    signal['signal_time'],
                    historical_data
                )
                
                # 保存结果
                self._save_backtest_result(result)
                success_count += 1
                
            except Exception as e:
                logger.error(f"❌ 回测失败: {signal} | 错误: {e}")
        
        logger.info(f"✅ 回测完成: 成功={success_count}/{len(signals)}")
        
        return success_count
    
    def _get_backtest_signals(self) -> List[Dict]:
        """获取回测信号（模拟）"""
        return [
            {
                'id': 1,
                'stock_code': '601919',
                'signal_time': '2026-06-23 14:30:00',
            }
        ]
    
    def _save_backtest_result(self, result: BacktestResult):
        """保存回测结果（模拟）"""
        logger.info(f"💾 回测结果已保存: {result.stock_code} {result.rating}")


# ── 测试函数 ────────────────────────────────────────────────
if __name__ == '__main__':
    print("🚀 V13.2 舆情信号回测框架 (修正版) 测试")
    print("="*60)
    
    backtester = SentimentBacktesterFixed()
    
    # 测试1: 准备TDX数据获取
    print("\n🧪 测试1: 准备TDX数据获取...")
    test_stocks = ['601919', '600428', '601866', '600428', '603565']
    backtester.prepare_tdx_data_fetch(test_stocks)
    
    # 测试2: 模拟TDX结果文件
    print("\n🧪 测试2: 模拟TDX结果文件...")
    mock_tdx_data = [
        {
            "code": "601919",
            "name": "中远海控",
            "kline": [
                {"date": "2026-06-24", "open": 10.5, "high": 10.8, "low": 10.3, "close": 10.6, "volume": 1000000, "amount": 106000000}
            ]
        }
    ]
    
    with open(TDX_RESULT_FILE, 'w', encoding='utf-8') as f:
        json.dump(mock_tdx_data, f, ensure_ascii=False)
    
    print(f"  模拟TDX数据已写入: {TDX_RESULT_FILE}")
    
    # 测试3: 使用TDX数据回测
    print("\n🧪 测试3: 使用TDX数据回测...")
    count = backtester.run_backtest_with_tdx_data()
    print(f"  成功回测: {count} 条")
    
    print("\n✅ 测试完成")
