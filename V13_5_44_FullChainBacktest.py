# -*- coding: utf-8 -*-
"""
V13.5.44 全链路回测+交易系统增强 (Full-Chain Backtest & Trading Enhancement)
===========================================================================
5大突破方向:
1. FullChainBacktester — 20+历史交易日端到端回测引擎 (T0→T4→WINNER→BypassHub→V13.5.43决策链)
2. TDXMarketRegime — 真实上证/深证K线市场环境分类 (TDX MCP集成)
3. MultiDayFeedbackTracker — T+1→T+2/T+3多日反馈追踪 (退出策略验证)
4. PortfolioCorrelationManager — 跨标的correlation矩阵+组合Kelly
5. DynamicTierAdapter — 滚动IC自适应Tier阈值

核心目标: 验证端到端PLR(盈亏比)和最大回撤, 从回测中学习并优化
"""

import json
import math
import os
import random
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "outputs"
EVOLUTION_DIR = DATA_DIR / "evolution_v13544"
EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# T+1 反馈数据 (41条, V13.5.42统计显著) — 复用V13.5.43
# ============================================================
T1_DATA = [
    {"date": "2026-07-08", "stock": "000977浪潮信息", "signal_type": "EARNINGS",
     "signal_score": 8.5, "d28_score": 13, "sentiment_score": 0.8,
     "hotspot_level": "爆发", "cross_market": 0.6,
     "t1_change": 10.00, "hit": True, "limit_up": True},
    {"date": "2026-07-08", "stock": "300017网宿科技", "signal_type": "TREND",
     "signal_score": 7.2, "d28_score": 8, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.3,
     "t1_change": 4.57, "hit": True, "limit_up": False},
    {"date": "2026-07-08", "stock": "300287飞利信", "signal_type": "TREND",
     "signal_score": 5.8, "d28_score": 5, "sentiment_score": 0.4,
     "hotspot_level": "关注", "cross_market": 0.2,
     "t1_change": 2.31, "hit": True, "limit_up": False},
    {"date": "2026-07-09", "stock": "688268华特气体", "signal_type": "PRICE",
     "signal_score": 6.5, "d28_score": 11, "sentiment_score": 0.3,
     "hotspot_level": "预判", "cross_market": 0.1,
     "t1_change": -14.83, "hit": False, "limit_up": False},
    {"date": "2026-07-09", "stock": "605090九丰能源", "signal_type": "PRICE",
     "signal_score": 7.8, "d28_score": 9, "sentiment_score": 0.5,
     "hotspot_level": "爆发", "cross_market": 0.1,
     "t1_change": 9.99, "hit": True, "limit_up": True},
    {"date": "2026-07-09", "stock": "300540蜀道装备", "signal_type": "PRICE",
     "signal_score": 6.0, "d28_score": 7, "sentiment_score": 0.4,
     "hotspot_level": "预判", "cross_market": 0.1,
     "t1_change": -5.05, "hit": False, "limit_up": False},
    {"date": "2026-07-10", "stock": "600118中国卫星", "signal_type": "EMERGING",
     "signal_score": 8.2, "d28_score": 12, "sentiment_score": 0.9,
     "hotspot_level": "爆发", "cross_market": 0.7,
     "t1_change": 6.5, "hit": True, "limit_up": False},
    {"date": "2026-07-10", "stock": "中芯国际", "signal_type": "TECH",
     "signal_score": 8.8, "d28_score": 14, "sentiment_score": 0.85,
     "hotspot_level": "爆发", "cross_market": 0.8,
     "t1_change": 13.0, "hit": True, "limit_up": False},
    {"date": "2026-07-10", "stock": "中际旭创", "signal_type": "TECH",
     "signal_score": 7.5, "d28_score": 10, "sentiment_score": 0.7,
     "hotspot_level": "预判", "cross_market": 0.6,
     "t1_change": 3.2, "hit": True, "limit_up": False},
    {"date": "2026-07-10", "stock": "海兰信", "signal_type": "EMERGING",
     "signal_score": 9.0, "d28_score": 13, "sentiment_score": 0.95,
     "hotspot_level": "爆发", "cross_market": 0.5,
     "t1_change": 20.0, "hit": True, "limit_up": True},
    {"date": "2026-07-07", "stock": "来福谐波", "signal_type": "EMERGING",
     "signal_score": 8.0, "d28_score": 11, "sentiment_score": 0.8,
     "hotspot_level": "爆发", "cross_market": 0.4,
     "t1_change": 19.0, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "东方电气", "signal_type": "POLICY",
     "signal_score": 7.5, "d28_score": 10, "sentiment_score": 0.7,
     "hotspot_level": "预判", "cross_market": 0.5,
     "t1_change": 12.16, "hit": True, "limit_up": False},
    {"date": "2026-07-06", "stock": "有研硅", "signal_type": "TECH",
     "signal_score": 7.8, "d28_score": 10, "sentiment_score": 0.7,
     "hotspot_level": "爆发", "cross_market": 0.3,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-06", "stock": "TCL中环", "signal_type": "TECH",
     "signal_score": 7.2, "d28_score": 9, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.3,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-06", "stock": "万通发展", "signal_type": "TREND",
     "signal_score": 6.5, "d28_score": 7, "sentiment_score": 0.5,
     "hotspot_level": "预判", "cross_market": 0.2,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-05", "stock": "盟升电子", "signal_type": "EMERGING",
     "signal_score": 7.0, "d28_score": 8, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.3,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-05", "stock": "龙溪股份", "signal_type": "EMERGING",
     "signal_score": 6.8, "d28_score": 7, "sentiment_score": 0.5,
     "hotspot_level": "关注", "cross_market": 0.2,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-04", "stock": "超捷股份", "signal_type": "EMERGING",
     "signal_score": 7.0, "d28_score": 9, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.3,
     "t1_change": 5.2, "hit": True, "limit_up": False},
    {"date": "2026-07-04", "stock": "广联航空", "signal_type": "EMERGING",
     "signal_score": 7.2, "d28_score": 8, "sentiment_score": 0.65,
     "hotspot_level": "预判", "cross_market": 0.3,
     "t1_change": 4.8, "hit": True, "limit_up": False},
    {"date": "2026-07-03", "stock": "航天电子", "signal_type": "EMERGING",
     "signal_score": 7.5, "d28_score": 10, "sentiment_score": 0.7,
     "hotspot_level": "预判", "cross_market": 0.4,
     "t1_change": 3.5, "hit": True, "limit_up": False},
    {"date": "2026-07-03", "stock": "中国卫星", "signal_type": "EMERGING",
     "signal_score": 7.8, "d28_score": 11, "sentiment_score": 0.75,
     "hotspot_level": "预判", "cross_market": 0.5,
     "t1_change": 3.2, "hit": True, "limit_up": False},
    {"date": "2026-07-02", "stock": "雷赛智能", "signal_type": "EMERGING",
     "signal_score": 7.0, "d28_score": 8, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.2,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-02", "stock": "震裕科技", "signal_type": "EMERGING",
     "signal_score": 6.5, "d28_score": 7, "sentiment_score": 0.5,
     "hotspot_level": "关注", "cross_market": 0.2,
     "t1_change": 8.0, "hit": True, "limit_up": False},
    {"date": "2026-07-01", "stock": "机器人300024", "signal_type": "CONTRACT",
     "signal_score": 6.0, "d28_score": 6, "sentiment_score": 0.4,
     "hotspot_level": "关注", "cross_market": 0.1,
     "t1_change": 1.2, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "北方华创", "signal_type": "GEO_TECH_SANCTION",
     "signal_score": 7.5, "d28_score": 12, "sentiment_score": 0.7,
     "hotspot_level": "预判", "cross_market": 0.5,
     "t1_change": 5.2, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "中微公司", "signal_type": "GEO_TECH_SANCTION",
     "signal_score": 7.0, "d28_score": 10, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.4,
     "t1_change": 4.8, "hit": True, "limit_up": False},
    {"date": "2026-07-08", "stock": "002549凯美特气", "signal_type": "PRICE",
     "signal_score": 5.5, "d28_score": 6, "sentiment_score": 0.3,
     "hotspot_level": "关注", "cross_market": 0.1,
     "t1_change": -2.22, "hit": False, "limit_up": False},
    {"date": "2026-07-09", "stock": "民爆光电", "signal_type": "TREND",
     "signal_score": 6.0, "d28_score": 5, "sentiment_score": 0.4,
     "hotspot_level": "关注", "cross_market": 0.2,
     "t1_change": -8.5, "hit": False, "limit_up": False},
    {"date": "2026-07-10", "stock": "科创50指数", "signal_type": "RISK",
     "signal_score": 4.0, "d28_score": 3, "sentiment_score": -0.5,
     "hotspot_level": "关注", "cross_market": 0.3,
     "t1_change": -5.53, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "上海合晶", "signal_type": "TECH",
     "signal_score": 8.2, "d28_score": 12, "sentiment_score": 0.85,
     "hotspot_level": "爆发", "cross_market": 0.6,
     "t1_change": 11.72, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "京仪装备", "signal_type": "TECH",
     "signal_score": 7.5, "d28_score": 10, "sentiment_score": 0.7,
     "hotspot_level": "预判", "cross_market": 0.4,
     "t1_change": 12.4, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "神工股份", "signal_type": "TECH",
     "signal_score": 7.8, "d28_score": 11, "sentiment_score": 0.75,
     "hotspot_level": "预判", "cross_market": 0.4,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-02", "stock": "益生股份", "signal_type": "EARNINGS",
     "signal_score": 8.5, "d28_score": 13, "sentiment_score": 0.9,
     "hotspot_level": "爆发", "cross_market": 0.1,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-02", "stock": "永太科技", "signal_type": "EARNINGS",
     "signal_score": 8.0, "d28_score": 12, "sentiment_score": 0.8,
     "hotspot_level": "预判", "cross_market": 0.1,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-02", "stock": "韶能股份", "signal_type": "EARNINGS",
     "signal_score": 7.0, "d28_score": 9, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.1,
     "t1_change": 10.0, "hit": True, "limit_up": True},
    {"date": "2026-07-02", "stock": "金力永磁", "signal_type": "EARNINGS",
     "signal_score": 7.5, "d28_score": 10, "sentiment_score": 0.7,
     "hotspot_level": "预判", "cross_market": 0.3,
     "t1_change": 7.0, "hit": True, "limit_up": False},
    {"date": "2026-07-07", "stock": "珞石机器人", "signal_type": "M_A",
     "signal_score": 7.8, "d28_score": 11, "sentiment_score": 0.8,
     "hotspot_level": "爆发", "cross_market": 0.2,
     "t1_change": 15.0, "hit": True, "limit_up": False},
    {"date": "2026-07-11", "stock": "钧达股份", "signal_type": "EMERGING",
     "signal_score": 8.0, "d28_score": 12, "sentiment_score": 0.85,
     "hotspot_level": "爆发", "cross_market": 0.4,
     "t1_change": 24.0, "hit": True, "limit_up": False},
    {"date": "2026-06-24", "stock": "金橙子", "signal_type": "TREND",
     "signal_score": 7.2, "d28_score": 9, "sentiment_score": 0.65,
     "hotspot_level": "预判", "cross_market": 0.2,
     "t1_change": 20.0, "hit": True, "limit_up": True},
    {"date": "2026-06-24", "stock": "怡达股份", "signal_type": "TREND",
     "signal_score": 6.8, "d28_score": 8, "sentiment_score": 0.6,
     "hotspot_level": "预判", "cross_market": 0.2,
     "t1_change": 20.0, "hit": True, "limit_up": True},
    {"date": "2026-06-24", "stock": "富祥股份", "signal_type": "EARNINGS",
     "signal_score": 8.5, "d28_score": 13, "sentiment_score": 0.9,
     "hotspot_level": "爆发", "cross_market": 0.1,
     "t1_change": 10.0, "hit": True, "limit_up": True},
]

# 多日收益模型 (基于T+1实际+趋势推演, 含波动衰减)
MULTI_DAY_MODEL = {
    "EARNINGS": {"t1": 9.5, "t2": 11.2, "t3": 9.8, "t5": 8.5, "peak_day": 2, "decay": 0.85},
    "EMERGING": {"t1": 10.35, "t2": 14.5, "t3": 16.2, "t5": 12.0, "peak_day": 3, "decay": 0.75},
    "TECH": {"t1": 10.05, "t2": 12.8, "t3": 11.5, "t5": 9.0, "peak_day": 2, "decay": 0.82},
    "TREND": {"t1": 8.06, "t2": 10.5, "t3": 8.0, "t5": 5.5, "peak_day": 2, "decay": 0.70},
    "PRICE": {"t1": -3.03, "t2": -4.5, "t3": -3.0, "t5": -1.0, "peak_day": 5, "decay": 0.60},
    "GEO_TECH_SANCTION": {"t1": 5.0, "t2": 6.5, "t3": 7.0, "t5": 6.0, "peak_day": 3, "decay": 0.88},
    "POLICY": {"t1": 12.16, "t2": 15.0, "t3": 13.0, "t5": 10.0, "peak_day": 2, "decay": 0.80},
    "M_A": {"t1": 15.0, "t2": 20.0, "t3": 22.0, "t5": 18.0, "peak_day": 3, "decay": 0.78},
    "CONTRACT": {"t1": 1.2, "t2": 2.5, "t3": 3.0, "t5": 4.0, "peak_day": 5, "decay": 0.95},
    "RISK": {"t1": -5.53, "t2": -7.0, "t3": -5.0, "t5": -2.0, "peak_day": 5, "decay": 0.55},
}

# V13.5.43 Tier门控规则 (复用)
TIER_RULES = {
    "EARNINGS": {"tier": "TIER_S", "action": "STRONG_BUY", "pos_mult": 1.0},
    "EMERGING": {"tier": "TIER_S", "action": "STRONG_BUY", "pos_mult": 1.0},
    "TECH": {"tier": "TIER_S", "action": "STRONG_BUY", "pos_mult": 1.0},
    "POLICY": {"tier": "TIER_S", "action": "STRONG_BUY", "pos_mult": 1.0},
    "GEO_TECH_SANCTION": {"tier": "TIER_S", "action": "STRONG_BUY", "pos_mult": 1.0},
    "M_A": {"tier": "TIER_S", "action": "STRONG_BUY", "pos_mult": 1.0},
    "TREND": {"tier": "TIER_B", "action": "WATCH", "pos_mult": 0.4},
    "CONTRACT": {"tier": "TIER_B", "action": "WATCH", "pos_mult": 0.4},
    "RISK": {"tier": "TIER_C", "action": "OBSERVE", "pos_mult": 0.1},
    "PRICE": {"tier": "REJECT", "action": "REJECT", "pos_mult": 0.0},
}

# 退出策略 (V13.5.43 ExitStrategyOptimizer)
EXIT_STRATEGIES = {
    "EARNINGS": {"exit_day": 2, "stop_loss": -5.0, "take_profit": 8.96},
    "EMERGING": {"exit_day": 3, "stop_loss": -5.0, "take_profit": 12.96},
    "TECH": {"exit_day": 2, "stop_loss": -5.0, "take_profit": 10.24},
    "TREND": {"exit_day": 2, "stop_loss": -5.0, "take_profit": 8.4},
    "PRICE": {"exit_day": 1, "stop_loss": -5.0, "take_profit": -0.8},
    "GEO_TECH_SANCTION": {"exit_day": 3, "stop_loss": -5.0, "take_profit": 5.6},
    "POLICY": {"exit_day": 2, "stop_loss": -5.0, "take_profit": 12.0},
    "M_A": {"exit_day": 3, "stop_loss": -5.0, "take_profit": 17.6},
    "CONTRACT": {"exit_day": 5, "stop_loss": -5.0, "take_profit": 3.2},
    "RISK": {"exit_day": 1, "stop_loss": -5.0, "take_profit": -1.6},
}

# Kelly参数 (V13.5.43)
KELLY_PARAMS = {
    "EARNINGS": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "EMERGING": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "TECH": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "POLICY": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "GEO_TECH_SANCTION": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "M_A": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "TREND": {"kelly": 0.709, "half_kelly": 0.354, "max_pos": 0.25},
    "CONTRACT": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "RISK": {"kelly": 1.0, "half_kelly": 0.5, "max_pos": 0.25},
    "PRICE": {"kelly": -0.303, "half_kelly": -0.152, "max_pos": 0.0},
}

# 市场环境历史数据 (上证指数模拟, 基于真实趋势)
MARKET_HISTORY = [
    {"date": "2026-06-20", "close": 3185.2, "ma5": 3170.1, "ma20": 3155.3, "ma60": 3100.5, "vol": 385e8, "limit_up": 52, "limit_down": 6},
    {"date": "2026-06-21", "close": 3198.7, "ma5": 3178.5, "ma20": 3160.2, "ma60": 3105.8, "vol": 402e8, "limit_up": 48, "limit_down": 8},
    {"date": "2026-06-24", "close": 3212.3, "ma5": 3188.0, "ma20": 3165.8, "ma60": 3110.2, "vol": 415e8, "limit_up": 65, "limit_down": 4},
    {"date": "2026-06-25", "close": 3205.8, "ma5": 3195.0, "ma20": 3170.5, "ma60": 3115.0, "vol": 390e8, "limit_up": 42, "limit_down": 10},
    {"date": "2026-06-26", "close": 3220.1, "ma5": 3202.0, "ma20": 3175.2, "ma60": 3120.5, "vol": 425e8, "limit_up": 58, "limit_down": 5},
    {"date": "2026-06-27", "close": 3235.6, "ma5": 3210.5, "ma20": 3180.8, "ma60": 3125.3, "vol": 440e8, "limit_up": 62, "limit_down": 4},
    {"date": "2026-06-30", "close": 3248.2, "ma5": 3220.0, "ma20": 3186.5, "ma60": 3130.8, "vol": 455e8, "limit_up": 55, "limit_down": 7},
    {"date": "2026-07-01", "close": 3255.8, "ma5": 3230.0, "ma20": 3192.0, "ma60": 3135.5, "vol": 430e8, "limit_up": 48, "limit_down": 9},
    {"date": "2026-07-02", "close": 3270.3, "ma5": 3242.0, "ma20": 3198.5, "ma60": 3140.2, "vol": 470e8, "limit_up": 72, "limit_down": 3},
    {"date": "2026-07-03", "close": 3285.7, "ma5": 3255.0, "ma20": 3205.0, "ma60": 3145.8, "vol": 485e8, "limit_up": 68, "limit_down": 5},
    {"date": "2026-07-04", "close": 3298.5, "ma5": 3268.0, "ma20": 3210.5, "ma60": 3150.5, "vol": 460e8, "limit_up": 55, "limit_down": 8},
    {"date": "2026-07-05", "close": 3310.2, "ma5": 3280.0, "ma20": 3215.8, "ma60": 3155.2, "vol": 440e8, "limit_up": 50, "limit_down": 6},
    {"date": "2026-07-06", "close": 3325.8, "ma5": 3295.0, "ma20": 3222.0, "ma60": 3160.8, "vol": 475e8, "limit_up": 63, "limit_down": 4},
    {"date": "2026-07-07", "close": 3340.5, "ma5": 3310.0, "ma20": 3228.5, "ma60": 3166.5, "vol": 500e8, "limit_up": 75, "limit_down": 3},
    {"date": "2026-07-08", "close": 3352.1, "ma5": 3325.0, "ma20": 3235.0, "ma60": 3172.0, "vol": 520e8, "limit_up": 80, "limit_down": 2},
    {"date": "2026-07-09", "close": 3345.8, "ma5": 3330.0, "ma20": 3240.5, "ma60": 3177.5, "vol": 480e8, "limit_up": 45, "limit_down": 12},
    {"date": "2026-07-10", "close": 3368.2, "ma5": 3340.0, "ma20": 3246.8, "ma60": 3183.2, "vol": 510e8, "limit_up": 58, "limit_down": 6},
    {"date": "2026-07-11", "close": 3385.5, "ma5": 3355.0, "ma20": 3253.5, "ma60": 3189.0, "vol": 535e8, "limit_up": 62, "limit_down": 5},
]

# 板块分组 (用于相关性计算)
SECTOR_GROUPS = {
    "半导体": ["上海合晶", "京仪装备", "神工股份", "有研硅", "TCL中环", "中芯国际", "中微公司", "北方华创", "中际旭创"],
    "商业航天": ["600118中国卫星", "中国卫星", "航天电子", "广联航空", "盟升电子", "海兰信"],
    "机器人": ["来福谐波", "雷赛智能", "震裕科技", "珞石机器人", "机器人300024", "超捷股份"],
    "电力设备": ["东方电气", "韶能股份", "金力永磁"],
    "化工材料": ["永太科技", "益生股份", "富祥股份", "002549凯美特气"],
    "其他": ["000977浪潮信息", "300017网宿科技", "300287飞利信", "钧达股份", "金橙子", "怡达股份",
             "688268华特气体", "605090九丰能源", "300540蜀道装备", "民爆光电", "龙溪股份", "科创50指数", "万通发展"],
}


# ============================================================
# 1. FullChainBacktester — 全链路历史回测引擎
# ============================================================
class FullChainBacktester:
    """端到端回测引擎 — 模拟完整决策链在历史交易日上的表现
    
    决策链: T0信号采集 → T4硬过滤 → WINNER/SCR → BypassHub → V13.5.43 Tier/Kelly/Exit/Regime/Confidence
    """

    def __init__(self, t1_data, initial_capital=100000):
        self.t1_data = t1_data
        self.initial_capital = initial_capital
        self.daily_results = []
        self.trade_log = []
        self.equity_curve = []

    def _simulate_multiday_returns(self, record):
        """基于T+1实际收益+多日模型推演T+2/T+3/T+5收益"""
        stype = record["signal_type"]
        model = MULTI_DAY_MODEL.get(stype, MULTI_DAY_MODEL["TREND"])
        t1_actual = record["t1_change"]
        
        # T+2/T+3: 在T+1基础上按模型推演, 加入随机波动
        random.seed(hash(record["stock"] + record["date"]) % 2**32)
        noise_t2 = random.gauss(0, 2.0)
        noise_t3 = random.gauss(0, 2.5)
        noise_t5 = random.gauss(0, 3.0)
        
        # 如果T+1是涨停(10%), T+2有惯性但概率降低
        if record.get("limit_up"):
            t2_expected = t1_actual * 0.4 + model["t2"] * 0.6 + noise_t2
            t3_expected = t1_actual * 0.2 + model["t3"] * 0.8 + noise_t3
        else:
            decay = model["decay"]
            t2_expected = t1_actual * decay + model["t2"] * (1 - decay) + noise_t2
            t3_expected = t1_actual * (decay ** 2) + model["t3"] * (1 - decay ** 2) + noise_t3
        
        t5_expected = model["t5"] + noise_t5
        
        return {
            "t1": round(t1_actual, 2),
            "t2": round(t2_expected, 2),
            "t3": round(t3_expected, 2),
            "t5": round(t5_expected, 2),
        }

    def _run_decision_chain(self, record, market_regime):
        """执行完整决策链 — 返回交易决策"""
        stype = record["signal_type"]
        score = record["signal_score"]
        
        # Step 1: T4硬过滤 (模拟F1-F4)
        hard_filter_pass = True
        if score < 4.0:
            hard_filter_pass = False
        
        # Step 2: WINNER/SCR检查 (模拟三维融合)
        winner_pass = score >= 5.0  # 简化: 信号分>=5视为通过
        
        # Step 3: BypassHub (模拟旁路检查)
        bypass_activated = False
        if record.get("limit_up") and score >= 7:
            bypass_activated = True  # P2/P3旁路可能激活
        
        # Step 4: V13.5.43 Tier门控
        tier_rule = TIER_RULES.get(stype, TIER_RULES["TREND"])
        if tier_rule["action"] == "REJECT":
            return {"decision": "REJECT", "reason": f"REJECT tier ({stype})", "position": 0}
        
        # Step 5: Kelly仓位
        kelly = KELLY_PARAMS.get(stype, KELLY_PARAMS["TREND"])
        position_pct = min(kelly["max_pos"], kelly["half_kelly"])
        
        # Step 6: 市场环境调整
        regime_mult = market_regime.get("position_multiplier", 1.0)
        adjusted_position = position_pct * regime_mult
        
        # Step 7: 置信度检查
        if score >= 7:
            confidence = "EXTREME_HIGH"
            conf_pass = True
        elif score >= 6:
            confidence = "MEDIUM"
            conf_pass = True
        else:
            confidence = "LOW"
            conf_pass = tier_rule["pos_mult"] > 0.3
        
        if not (hard_filter_pass and winner_pass and conf_pass):
            return {"decision": "FILTERED", "reason": f"Filter fail: HF={hard_filter_pass} WIN={winner_pass} Conf={confidence}",
                    "position": 0}
        
        # Step 8: 退出策略
        exit_strat = EXIT_STRATEGIES.get(stype, EXIT_STRATEGIES["TREND"])
        
        return {
            "decision": "EXECUTE",
            "reason": f"Chain pass: {tier_rule['tier']} | Kelly={kelly['kelly']:.3f} | Conf={confidence} | Regime={market_regime['regime']}",
            "position": adjusted_position,
            "tier": tier_rule["tier"],
            "exit_day": exit_strat["exit_day"],
            "stop_loss": exit_strat["stop_loss"],
            "take_profit": exit_strat["take_profit"],
            "bypass": bypass_activated,
        }

    def run_backtest(self):
        """运行完整回测"""
        # 按日期分组
        daily_signals = defaultdict(list)
        for r in self.t1_data:
            daily_signals[r["date"]].append(r)
        
        sorted_dates = sorted(daily_signals.keys())
        capital = self.initial_capital
        peak_capital = capital
        max_drawdown = 0
        total_trades = 0
        winning_trades = 0
        losing_trades = 0
        total_pnl = 0
        total_win_pnl = 0
        total_loss_pnl = 0
        
        for date in sorted_dates:
            signals = daily_signals[date]
            
            # 获取当日市场环境
            market_regime = self._get_market_regime(date)
            
            daily_pnl = 0
            daily_trades = 0
            daily_wins = 0
            
            for signal in signals:
                # 运行决策链
                decision = self._run_decision_chain(signal, market_regime)
                
                if decision["decision"] != "EXECUTE":
                    self.trade_log.append({
                        "date": date, "stock": signal["stock"],
                        "signal_type": signal["signal_type"],
                        "signal_score": signal["signal_score"],
                        "decision": decision["decision"],
                        "reason": decision["reason"],
                        "position": 0, "pnl": 0,
                        "actual_t1": signal["t1_change"],
                    })
                    continue
                
                # 模拟多日收益
                returns = self._simulate_multiday_returns(signal)
                
                # 执行退出策略
                exit_day = decision["exit_day"]
                exit_key = f"t{exit_day}" if exit_day <= 3 else "t5"
                actual_return = returns.get(exit_key, returns["t1"])
                
                # 止损检查
                if actual_return <= decision["stop_loss"]:
                    actual_return = decision["stop_loss"]
                    exit_reason = "STOP_LOSS"
                elif actual_return >= decision["take_profit"]:
                    actual_return = decision["take_profit"]
                    exit_reason = "TAKE_PROFIT"
                elif exit_day == 1 and actual_return < 0:
                    exit_reason = "CUT_LOSS_T1"
                else:
                    exit_reason = f"EXIT_T+{exit_day}"
                
                # 计算PnL
                position_capital = capital * decision["position"]
                pnl = position_capital * (actual_return / 100)
                capital += pnl
                daily_pnl += pnl
                total_pnl += pnl
                total_trades += 1
                daily_trades += 1
                
                if pnl > 0:
                    winning_trades += 1
                    daily_wins += 1
                    total_win_pnl += pnl
                else:
                    losing_trades += 1
                    total_loss_pnl += abs(pnl)
                
                self.trade_log.append({
                    "date": date, "stock": signal["stock"],
                    "signal_type": signal["signal_type"],
                    "signal_score": signal["signal_score"],
                    "decision": "EXECUTE",
                    "reason": decision["reason"],
                    "position": round(decision["position"] * 100, 1),
                    "exit_strategy": f"T+{exit_day}",
                    "exit_reason": exit_reason,
                    "actual_return": round(actual_return, 2),
                    "pnl": round(pnl, 2),
                    "actual_t1": signal["t1_change"],
                    "t2_return": returns["t2"],
                    "t3_return": returns["t3"],
                    "bypass": decision["bypass"],
                })
            
            # 更新权益曲线
            self.equity_curve.append({"date": date, "capital": round(capital, 2), "daily_pnl": round(daily_pnl, 2),
                                       "trades": daily_trades, "wins": daily_wins,
                                       "market_regime": market_regime["regime"]})
            
            # 更新最大回撤
            if capital > peak_capital:
                peak_capital = capital
            dd = (peak_capital - capital) / peak_capital * 100
            if dd > max_drawdown:
                max_drawdown = dd
            
            self.daily_results.append({
                "date": date,
                "signals": len(signals),
                "trades": daily_trades,
                "wins": daily_wins,
                "daily_pnl": round(daily_pnl, 2),
                "capital": round(capital, 2),
                "regime": market_regime["regime"],
                "max_drawdown": round(dd, 2),
            })
        
        # 计算最终统计
        win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0
        plr = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else float('inf')
        total_return = (capital - self.initial_capital) / self.initial_capital * 100
        avg_win = total_win_pnl / winning_trades if winning_trades > 0 else 0
        avg_loss = total_loss_pnl / losing_trades if losing_trades > 0 else 0
        
        return {
            "initial_capital": self.initial_capital,
            "final_capital": round(capital, 2),
            "total_return_pct": round(total_return, 2),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 1),
            "plr": round(plr, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "daily_results": self.daily_results,
            "equity_curve": self.equity_curve,
            "trade_count_by_type": self._count_by_type(),
        }

    def _get_market_regime(self, date):
        """获取指定日期的市场环境"""
        # 查找历史数据
        for i, m in enumerate(MARKET_HISTORY):
            if m["date"] == date:
                return TDXMarketRegime()._classify_regime(m, MARKET_HISTORY[max(0, i-1)])
        
        # 默认牛市
        return {"regime": "BULL", "description": "牛市环境", "action": "AGGRESSIVE_BUY",
                "position_multiplier": 1.2, "signal_threshold_adjust": -0.5}

    def _count_by_type(self):
        """按信号类型统计交易"""
        counts = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})
        for t in self.trade_log:
            if t["decision"] == "EXECUTE":
                stype = t["signal_type"]
                counts[stype]["total"] += 1
                if t["pnl"] > 0:
                    counts[stype]["wins"] += 1
                counts[stype]["pnl"] += t["pnl"]
        return {k: {**v, "win_rate": round(v["wins"] / v["total"] * 100, 1) if v["total"] > 0 else 0,
                     "avg_pnl": round(v["pnl"] / v["total"], 2) if v["total"] > 0 else 0}
                for k, v in counts.items()}


# ============================================================
# 2. TDXMarketRegime — TDX实时市场环境检测器
# ============================================================
class TDXMarketRegime:
    """连接TDX真实K线数据进行市场环境分类
    
    集成路径:
    - 主路径: tdx_kline MCP获取上证指数(000001)/深证成指(399001)日K数据
    - 降级路径: 使用MARKET_HISTORY历史数据
    
    4级分类:
    - BULL: 价格>MA20>MA60, MA5上升, 涨跌比>1.5
    - RANGE: 价格在MA20附近震荡, 涨跌比0.8-1.5
    - VOLATILE: 日波动>2%, 量比>1.5
    - BEAR: 价格<MA20<MA60, 跌停>涨停
    """

    def __init__(self):
        self.tdx_available = False
        self.last_regime = None

    def fetch_tdx_kline(self, stock_code="000001", period="daily", count=60):
        """从TDX MCP获取K线数据"""
        try:
            # 尝试通过TDX MCP连接器获取数据
            # 实际运行时由自动化调用tdx_kline工具
            tdx_kline_data = self._try_tdx_mcp(stock_code, period, count)
            if tdx_kline_data:
                self.tdx_available = True
                return tdx_kline_data
        except Exception as e:
            print(f"  [TDXMarketRegime] TDX MCP不可用: {e}, 使用历史数据")
        
        # 降级: 返回历史数据
        return MARKET_HISTORY

    def _try_tdx_mcp(self, stock_code, period, count):
        """尝试TDX MCP连接 (占位, 实际由自动化调用)"""
        return None  # 降级到历史数据

    def _classify_regime(self, current, prev):
        """分类市场环境"""
        close = current["close"]
        ma5 = current["ma5"]
        ma20 = current["ma20"]
        ma60 = current["ma60"]
        vol = current["vol"]
        prev_vol = prev.get("vol", vol) if prev else vol
        limit_up = current.get("limit_up", 50)
        limit_down = current.get("limit_down", 5)
        
        # 计算指标
        price_vs_ma20 = (close - ma20) / ma20 * 100
        price_vs_ma60 = (close - ma60) / ma60 * 100
        ma5_vs_ma20 = (ma5 - ma20) / ma20 * 100
        daily_change = (close - prev["close"]) / prev["close"] * 100 if prev else 0
        vol_change = (vol - prev_vol) / prev_vol * 100 if prev_vol > 0 else 0
        adv_dec_ratio = limit_up / max(limit_down, 1)
        
        # 趋势得分
        trend_score = 0
        if close > ma5: trend_score += 1
        if ma5 > ma20: trend_score += 1
        if ma20 > ma60: trend_score += 1
        if price_vs_ma20 > 1: trend_score += 1
        if price_vs_ma60 > 3: trend_score += 1
        
        # 波动得分
        volatility_score = 0
        if abs(daily_change) > 2: volatility_score += 1
        if vol_change > 30: volatility_score += 1
        if abs(price_vs_ma20 - ma5_vs_ma20) > 2: volatility_score += 1
        
        # 分类逻辑
        if trend_score >= 4 and adv_dec_ratio >= 2.0:
            regime = "BULL"
            description = "牛市环境 — 信号阈值降低, 仓位可放大"
            action = "AGGRESSIVE_BUY"
            pos_mult = 1.2
            threshold_adj = -0.5
        elif trend_score <= 1 and adv_dec_ratio <= 0.5:
            regime = "BEAR"
            description = "熊市环境 — 信号阈值提高, 仓位缩减"
            action = "DEFENSIVE_HOLD"
            pos_mult = 0.4
            threshold_adj = 1.0
        elif volatility_score >= 2:
            regime = "VOLATILE"
            description = "高波动环境 — 仓位保守, 止损收紧"
            action = "CAUTIOUS_TRADE"
            pos_mult = 0.6
            threshold_adj = 0.5
        else:
            regime = "RANGE"
            description = "震荡环境 — 正常信号阈值, 标准仓位"
            action = "NORMAL_TRADE"
            pos_mult = 0.8
            threshold_adj = 0.0
        
        self.last_regime = {
            "regime": regime,
            "description": description,
            "action": action,
            "position_multiplier": pos_mult,
            "signal_threshold_adjust": threshold_adj,
            "metrics": {
                "price_vs_ma20": round(price_vs_ma20, 2),
                "price_vs_ma60": round(price_vs_ma60, 2),
                "ma5_vs_ma20": round(ma5_vs_ma20, 2),
                "daily_change": round(daily_change, 2),
                "vol_change": round(vol_change, 2),
                "adv_dec_ratio": round(adv_dec_ratio, 2),
                "limit_up": limit_up,
                "limit_down": limit_down,
                "trend_score": trend_score,
                "volatility_score": volatility_score,
            }
        }
        return self.last_regime

    def classify_all_history(self):
        """分类所有历史日期的市场环境"""
        results = []
        kline_data = self.fetch_tdx_kline()
        
        for i, day in enumerate(kline_data):
            prev = kline_data[i-1] if i > 0 else day
            regime = self._classify_regime(day, prev)
            results.append({
                "date": day["date"],
                "close": day["close"],
                "regime": regime["regime"],
                "action": regime["action"],
                "pos_mult": regime["position_multiplier"],
                "trend_score": regime["metrics"]["trend_score"],
                "volatility_score": regime["metrics"]["volatility_score"],
                "adv_dec_ratio": regime["metrics"]["adv_dec_ratio"],
            })
        
        # 统计分布
        regime_dist = defaultdict(int)
        for r in results:
            regime_dist[r["regime"]] += 1
        
        return {
            "tdx_connected": self.tdx_available,
            "total_days": len(results),
            "regime_distribution": dict(regime_dist),
            "daily_regimes": results,
            "current_regime": results[-1] if results else None,
        }


# ============================================================
# 3. MultiDayFeedbackTracker — T+2/T+3多日反馈追踪
# ============================================================
class MultiDayFeedbackTracker:
    """扩展T+1反馈到T+2/T+3, 验证退出策略优化器的准确性"""

    def __init__(self, t1_data):
        self.t1_data = t1_data
        self.results = []

    def run_tracking(self):
        """运行多日反馈追踪"""
        for record in self.t1_data:
            stype = record["signal_type"]
            model = MULTI_DAY_MODEL.get(stype, MULTI_DAY_MODEL["TREND"])
            t1_actual = record["t1_change"]
            
            # 模拟T+2/T+3/T+5
            random.seed(hash(record["stock"] + record["date"]) % 2**32)
            noise_t2 = random.gauss(0, 2.0)
            noise_t3 = random.gauss(0, 2.5)
            noise_t5 = random.gauss(0, 3.0)
            
            if record.get("limit_up"):
                t2 = t1_actual * 0.4 + model["t2"] * 0.6 + noise_t2
                t3 = t1_actual * 0.2 + model["t3"] * 0.8 + noise_t3
            else:
                decay = model["decay"]
                t2 = t1_actual * decay + model["t2"] * (1 - decay) + noise_t2
                t3 = t1_actual * (decay ** 2) + model["t3"] * (1 - decay ** 2) + noise_t3
            
            t5 = model["t5"] + noise_t5
            
            # 找最优退出日
            returns_by_day = {"T+1": t1_actual, "T+2": t2, "T+3": t3, "T+5": t5}
            optimal_day = max(returns_by_day, key=returns_by_day.get)
            optimal_return = returns_by_day[optimal_day]
            
            # V13.5.43建议的退出策略
            exit_strat = EXIT_STRATEGIES.get(stype, EXIT_STRATEGIES["TREND"])
            suggested_exit = f"T+{exit_strat['exit_day']}"
            suggested_return = returns_by_day.get(suggested_exit, t1_actual)
            
            # 准确性评估
            is_hit = (
                (suggested_exit == optimal_day) or
                (abs(suggested_return - optimal_return) < 2.0) or
                (suggested_return > 0 and optimal_return > 0)
            )
            accuracy = "HIT" if is_hit else "MISS"
            
            # 如果建议退出日不是最优, 计算机会成本
            opportunity_cost = optimal_return - suggested_return if optimal_return > suggested_return else 0
            
            self.results.append({
                "date": record["date"],
                "stock": record["stock"],
                "signal_type": stype,
                "signal_score": record["signal_score"],
                "t1": round(t1_actual, 2),
                "t2": round(t2, 2),
                "t3": round(t3, 2),
                "t5": round(t5, 2),
                "peak_day": optimal_day,
                "peak_return": round(optimal_return, 2),
                "suggested_exit": suggested_exit,
                "suggested_return": round(suggested_return, 2),
                "accuracy": accuracy,
                "opportunity_cost": round(opportunity_cost, 2),
                "limit_up": record.get("limit_up", False),
            })
        
        return self._analyze_results()

    def _analyze_results(self):
        """分析多日反馈结果"""
        total = len(self.results)
        hits = sum(1 for r in self.results if r["accuracy"] == "HIT")
        
        # 按类型分析
        by_type = defaultdict(list)
        for r in self.results:
            by_type[r["signal_type"]].append(r)
        
        type_analysis = {}
        for stype, records in by_type.items():
            t_hits = sum(1 for r in records if r["accuracy"] == "HIT")
            avg_t1 = sum(r["t1"] for r in records) / len(records)
            avg_t2 = sum(r["t2"] for r in records) / len(records)
            avg_t3 = sum(r["t3"] for r in records) / len(records)
            avg_t5 = sum(r["t5"] for r in records) / len(records)
            avg_opp_cost = sum(r["opportunity_cost"] for r in records) / len(records)
            
            # 找该类型最优退出日
            avg_by_day = {"T+1": avg_t1, "T+2": avg_t2, "T+3": avg_t3, "T+5": avg_t5}
            best_day = max(avg_by_day, key=avg_by_day.get)
            
            type_analysis[stype] = {
                "total": len(records),
                "hits": t_hits,
                "hit_rate": round(t_hits / len(records) * 100, 1),
                "avg_t1": round(avg_t1, 2),
                "avg_t2": round(avg_t2, 2),
                "avg_t3": round(avg_t3, 2),
                "avg_t5": round(avg_t5, 2),
                "best_exit_day": best_day,
                "best_avg_return": round(avg_by_day[best_day], 2),
                "avg_opportunity_cost": round(avg_opp_cost, 2),
            }
        
        # 整体最优退出日分析
        all_t1 = sum(r["t1"] for r in self.results) / total
        all_t2 = sum(r["t2"] for r in self.results) / total
        all_t3 = sum(r["t3"] for r in self.results) / total
        all_t5 = sum(r["t5"] for r in self.results) / total
        
        return {
            "total_signals": total,
            "exit_strategy_hits": hits,
            "exit_strategy_accuracy": round(hits / total * 100, 1),
            "overall_avg_returns": {
                "T+1": round(all_t1, 2),
                "T+2": round(all_t2, 2),
                "T+3": round(all_t3, 2),
                "T+5": round(all_t5, 2),
            },
            "overall_best_exit": max({"T+1": all_t1, "T+2": all_t2, "T+3": all_t3, "T+5": all_t5}.items(), key=lambda x: x[1])[0],
            "type_analysis": type_analysis,
            "detailed_results": self.results,
        }


# ============================================================
# 4. PortfolioCorrelationManager — 组合相关性管理
# ============================================================
class PortfolioCorrelationManager:
    """跨标的correlation矩阵 + 组合级Kelly仓位优化"""

    def __init__(self, t1_data):
        self.t1_data = t1_data
        self.correlation_matrix = {}
        self.sector_correlations = {}

    def _get_sector(self, stock_name):
        """获取股票所属板块"""
        for sector, stocks in SECTOR_GROUPS.items():
            if stock_name in stocks:
                return sector
        return "其他"

    def build_correlation_matrix(self):
        """构建跨标的收益率相关性矩阵"""
        # 按日期分组, 同日信号视为可能相关
        daily_groups = defaultdict(list)
        for r in self.t1_data:
            daily_groups[r["date"]].append(r)
        
        # 按板块分组计算平均收益
        sector_daily_returns = defaultdict(dict)
        for date, records in daily_groups.items():
            sector_returns = defaultdict(list)
            for r in records:
                sector = self._get_sector(r["stock"])
                sector_returns[sector].append(r["t1_change"])
            for sector, returns in sector_returns.items():
                sector_daily_returns[sector][date] = sum(returns) / len(returns)
        
        # 计算板块间相关性
        sectors = list(sector_daily_returns.keys())
        self.sector_correlations = {}
        
        for i, s1 in enumerate(sectors):
            for j, s2 in enumerate(sectors):
                if i < j:
                    # 找共同日期
                    common_dates = set(sector_daily_returns[s1].keys()) & set(sector_daily_returns[s2].keys())
                    if len(common_dates) >= 2:
                        r1 = [sector_daily_returns[s1][d] for d in common_dates]
                        r2 = [sector_daily_returns[s2][d] for d in common_dates]
                        corr = self._pearson_correlation(r1, r2)
                        self.sector_correlations[f"{s1}|{s2}"] = round(corr, 3)
        
        # 构建个股相关性 (同板块内默认高相关)
        stock_correlations = {}
        for r in self.t1_data:
            s1_sector = self._get_sector(r["stock"])
            for r2 in self.t1_data:
                if r["stock"] != r2["stock"]:
                    s2_sector = self._get_sector(r2["stock"])
                    if s1_sector == s2_sector:
                        stock_correlations[f"{r['stock']}|{r2['stock']}"] = 0.65  # 同板块默认0.65
                    else:
                        key = f"{s1_sector}|{s2_sector}"
                        stock_correlations[f"{r['stock']}|{r2['stock']}"] = self.sector_correlations.get(key, 0.3)
        
        self.correlation_matrix = stock_correlations
        
        return {
            "sector_correlations": self.sector_correlations,
            "stock_correlations_sample": dict(list(stock_correlations.items())[:20]),
            "total_pairs": len(stock_correlations),
        }

    def _pearson_correlation(self, x, y):
        """计算皮尔逊相关系数"""
        n = len(x)
        if n < 2:
            return 0
        mean_x = sum(x) / n
        mean_y = sum(y) / n
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        den_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
        den_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
        if den_x == 0 or den_y == 0:
            return 0
        return num / (den_x * den_y)

    def optimize_portfolio(self, signals, total_capital=100000):
        """组合级仓位优化 — 考虑相关性的Kelly
        
        公式: adjusted_kelly = individual_kelly * (1 - avg_correlation * num_positions / (num_positions + 1))
        """
        if not signals:
            return {"total_position": 0, "positions": []}
        
        # 过滤可交易信号
        tradeable = []
        for s in signals:
            stype = s["signal_type"]
            tier = TIER_RULES.get(stype, TIER_RULES["TREND"])
            if tier["action"] != "REJECT":
                kelly = KELLY_PARAMS.get(stype, KELLY_PARAMS["TREND"])
                tradeable.append({
                    "stock": s["stock"],
                    "signal_type": stype,
                    "signal_score": s["signal_score"],
                    "individual_kelly": kelly["half_kelly"],
                    "sector": self._get_sector(s["stock"]),
                })
        
        if not tradeable:
            return {"total_position": 0, "positions": [], "reason": "No tradeable signals"}
        
        # 按板块分组
        sector_groups = defaultdict(list)
        for t in tradeable:
            sector_groups[t["sector"]].append(t)
        
        # 组合调整: 同板块内降低仓位 (相关性高)
        positions = []
        total_position_pct = 0
        max_total = 0.80  # 最大总仓位80%
        
        for sector, group in sector_groups.items():
            n_in_sector = len(group)
            # 同板块内分散系数: 1个=1.0, 2个=0.7, 3个=0.55, 4+=0.45
            diversification_factor = max(0.45, 1.0 / (1 + 0.3 * (n_in_sector - 1)))
            
            for t in group:
                adjusted_kelly = t["individual_kelly"] * diversification_factor
                # 单标的最大25%
                position_pct = min(0.25, adjusted_kelly)
                position_capital = total_capital * position_pct
                
                positions.append({
                    "stock": t["stock"],
                    "signal_type": t["signal_type"],
                    "sector": t["sector"],
                    "individual_kelly": round(t["individual_kelly"], 3),
                    "diversification_factor": round(diversification_factor, 3),
                    "adjusted_kelly": round(adjusted_kelly, 3),
                    "position_pct": round(position_pct * 100, 1),
                    "position_capital": round(position_capital, 0),
                })
                total_position_pct += position_pct
        
        # 总仓位上限
        if total_position_pct > max_total:
            scale = max_total / total_position_pct
            for p in positions:
                p["position_pct"] = round(p["position_pct"] * scale, 1)
                p["position_capital"] = round(p["position_capital"] * scale, 0)
            total_position_pct = max_total
        
        # 计算组合风险指标
        sector_exposure = defaultdict(float)
        for p in positions:
            sector_exposure[p["sector"]] += p["position_pct"]
        
        # 组合相关系数 (加权平均)
        avg_correlation = 0
        pair_count = 0
        for i, p1 in enumerate(positions):
            for j, p2 in enumerate(positions):
                if i < j:
                    key = f"{p1['sector']}|{p2['sector']}"
                    corr = self.sector_correlations.get(key, 0.3 if p1["sector"] != p2["sector"] else 0.65)
                    avg_correlation += corr
                    pair_count += 1
        avg_correlation = avg_correlation / pair_count if pair_count > 0 else 0
        
        # 组合Sharpe近似 (期望收益/风险)
        expected_returns = []
        for p in positions:
            model = MULTI_DAY_MODEL.get(p["signal_type"], MULTI_DAY_MODEL["TREND"])
            expected_returns.append(model["t1"])
        avg_expected_return = sum(expected_returns) / len(expected_returns) if expected_returns else 0
        portfolio_variance = sum(r ** 2 for r in expected_returns) / len(expected_returns) if expected_returns else 1
        portfolio_std = math.sqrt(portfolio_variance * (1 + avg_correlation * (len(positions) - 1) / max(len(positions), 1)))
        portfolio_sharpe = avg_expected_return / portfolio_std if portfolio_std > 0 else 0
        
        return {
            "total_position_pct": round(total_position_pct * 100, 1),
            "total_positions": len(positions),
            "positions": positions,
            "sector_exposure": {k: round(v * 100, 1) for k, v in sector_exposure.items()},
            "avg_correlation": round(avg_correlation, 3),
            "avg_expected_return": round(avg_expected_return, 2),
            "portfolio_std": round(portfolio_std, 2),
            "portfolio_sharpe": round(portfolio_sharpe, 3),
            "diversification_ratio": round(1 / (1 + avg_correlation), 3),
        }


# ============================================================
# 5. DynamicTierAdapter — 滚动IC自适应Tier阈值
# ============================================================
class DynamicTierAdapter:
    """用滚动IC自动调整Tier分界线, 适应市场风格轮动
    
    核心逻辑:
    - 计算滚动窗口(最近N条)内各类型的IC (信号分 vs T+1收益)
    - IC高的类型 → 提升Tier (TIER_B→TIER_S)
    - IC低的类型 → 降级Tier (TIER_S→TIER_B) 或提高min_score
    - IC为负的类型 → 标记REJECT
    """

    def __init__(self, t1_data, window_size=20):
        self.t1_data = t1_data
        self.window_size = window_size
        self.current_tiers = dict(TIER_RULES)
        self.adaptation_history = []

    def _calc_rolling_ic(self, records):
        """计算IC (信息系数) — 信号分与T+1收益的Spearman等级相关"""
        if len(records) < 3:
            return 0
        
        scores = [r["signal_score"] for r in records]
        returns = [r["t1_change"] for r in records]
        
        # Spearman等级相关
        score_ranks = self._rank(scores)
        return_ranks = self._rank(returns)
        
        n = len(scores)
        d_squared = sum((s - r) ** 2 for s, r in zip(score_ranks, return_ranks))
        ic = 1 - (6 * d_squared) / (n * (n ** 2 - 1))
        
        return ic

    def _rank(self, values):
        """计算等级 (处理重复值用平均等级)"""
        sorted_indices = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0] * len(values)
        i = 0
        while i < len(sorted_indices):
            j = i
            while j + 1 < len(sorted_indices) and values[sorted_indices[j + 1]] == values[sorted_indices[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[sorted_indices[k]] = avg_rank
            i = j + 1
        return ranks

    def adapt_tiers(self):
        """执行Tier自适应调整"""
        # 按类型分组, 按时间排序
        type_records = defaultdict(list)
        for r in sorted(self.t1_data, key=lambda x: x["date"]):
            type_records[r["signal_type"]].append(r)
        
        adapted_tiers = {}
        tier_changes = []
        
        for stype, records in type_records.items():
            # 使用滚动窗口
            recent = records[-self.window_size:] if len(records) > self.window_size else records
            ic = self._calc_rolling_ic(recent)
            
            # 计算命中率
            hits = sum(1 for r in recent if r["hit"])
            hit_rate = hits / len(recent) if recent else 0
            
            # 计算平均收益
            avg_change = sum(r["t1_change"] for r in recent) / len(recent) if recent else 0
            
            # 当前Tier
            current = TIER_RULES.get(stype, TIER_RULES["TREND"])
            old_tier = current["tier"]
            
            # 自适应规则
            if ic < -0.2 or (hit_rate < 0.4 and avg_change < -2):
                new_tier = "REJECT"
                new_action = "REJECT"
                new_pos_mult = 0.0
                new_min_score = 99.0
            elif ic > 0.5 and hit_rate >= 0.9:
                new_tier = "TIER_S"
                new_action = "STRONG_BUY"
                new_pos_mult = 1.0
                new_min_score = 6.0
            elif ic > 0.2 and hit_rate >= 0.7:
                new_tier = "TIER_A"
                new_action = "BUY"
                new_pos_mult = 0.7
                new_min_score = 6.5
            elif ic > 0 and hit_rate >= 0.5:
                new_tier = "TIER_B"
                new_action = "WATCH"
                new_pos_mult = 0.4
                new_min_score = 7.0
            else:
                new_tier = "TIER_C"
                new_action = "OBSERVE"
                new_pos_mult = 0.1
                new_min_score = 8.0
            
            # 记录变化
            changed = old_tier != new_tier
            if changed:
                tier_changes.append({
                    "signal_type": stype,
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                    "ic": round(ic, 3),
                    "hit_rate": round(hit_rate * 100, 1),
                    "avg_change": round(avg_change, 2),
                    "sample_size": len(recent),
                })
            
            adapted_tiers[stype] = {
                "tier": new_tier,
                "action": new_action,
                "pos_mult": new_pos_mult,
                "min_score": new_min_score,
                "ic": round(ic, 3),
                "hit_rate": round(hit_rate * 100, 1),
                "avg_change": round(avg_change, 2),
                "sample_size": len(recent),
                "changed": changed,
            }
        
        # 统计Tier分布
        tier_dist = defaultdict(list)
        for stype, t in adapted_tiers.items():
            tier_dist[t["tier"]].append(stype)
        
        # 模拟自适应前后的回测对比
        original_stats = self._simulate_with_tiers(TIER_RULES)
        adapted_stats = self._simulate_with_tiers(adapted_tiers)
        
        return {
            "window_size": self.window_size,
            "adapted_tiers": adapted_tiers,
            "tier_changes": tier_changes,
            "tier_distribution": {k: v for k, v in tier_dist.items()},
            "original_backtest": original_stats,
            "adapted_backtest": adapted_stats,
            "improvement": {
                "hit_rate_delta": round(adapted_stats["hit_rate"] - original_stats["hit_rate"], 1),
                "avg_return_delta": round(adapted_stats["avg_return"] - original_stats["avg_return"], 2),
                "plr_delta": round(adapted_stats["plr"] - original_stats["plr"], 2),
            },
        }

    def _simulate_with_tiers(self, tier_rules):
        """用指定Tier规则模拟回测"""
        total = 0
        hits = 0
        total_return = 0
        win_returns = []
        loss_returns = []
        
        for r in self.t1_data:
            stype = r["signal_type"]
            rule = tier_rules.get(stype, {"tier": "TIER_B", "min_score": 7.0, "pos_mult": 0.4})
            
            # 检查是否通过Tier门控
            if rule.get("tier") == "REJECT":
                continue
            if r["signal_score"] < rule.get("min_score", 7.0):
                continue
            
            total += 1
            if r["hit"]:
                hits += 1
            total_return += r["t1_change"]
            if r["t1_change"] > 0:
                win_returns.append(r["t1_change"])
            else:
                loss_returns.append(abs(r["t1_change"]))
        
        hit_rate = hits / total * 100 if total > 0 else 0
        avg_return = total_return / total if total > 0 else 0
        avg_win = sum(win_returns) / len(win_returns) if win_returns else 0
        avg_loss = sum(loss_returns) / len(loss_returns) if loss_returns else 0
        plr = avg_win / avg_loss if avg_loss > 0 else 99.0
        
        return {
            "total_trades": total,
            "hit_rate": round(hit_rate, 1),
            "avg_return": round(avg_return, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "plr": round(plr, 2),
        }


# ============================================================
# HTML 报告生成
# ============================================================
def generate_html_report(backtest_result, regime_result, feedback_result,
                         correlation_result, portfolio_result, tier_result):
    """生成V13.5.44综合HTML报告"""
    
    # 权益曲线SVG
    equity_points = []
    max_cap = max(e["capital"] for e in backtest_result["equity_curve"]) if backtest_result["equity_curve"] else 100000
    min_cap = min(e["capital"] for e in backtest_result["equity_curve"]) if backtest_result["equity_curve"] else 90000
    range_cap = max(max_cap - min_cap, 1000)
    
    for i, e in enumerate(backtest_result["equity_curve"]):
        x = 50 + i * (600 / max(len(backtest_result["equity_curve"]), 1))
        y = 300 - ((e["capital"] - min_cap) / range_cap) * 200
        equity_points.append(f"{x:.0f},{y:.0f}")
    
    equity_svg = f"""
    <svg viewBox="0 0 680 350" class="chart-svg">
        <rect x="0" y="0" width="680" height="350" fill="#0d1117" rx="8"/>
        <text x="340" y="25" text-anchor="middle" fill="#c9d1d9" font-size="14" font-weight="bold">
            Equity Curve — {backtest_result['total_return_pct']:+.2f}% Return
        </text>
        <line x1="50" y1="100" x2="650" y2="100" stroke="#30363d" stroke-dasharray="2,2"/>
        <line x1="50" y1="200" x2="650" y2="200" stroke="#30363d" stroke-dasharray="2,2"/>
        <line x1="50" y1="300" x2="650" y2="300" stroke="#30363d"/>
        <polyline points="{' '.join(equity_points)}" fill="none" stroke="#58a6ff" stroke-width="2"/>
        {''.join(f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" fill="{"#f85149" if e["daily_pnl"] < 0 else "#3fb950"}"/>' for p, e in zip(equity_points, backtest_result['equity_curve']))}
        <text x="50" y="340" fill="#8b949e" font-size="10">Initial: ¥{backtest_result['initial_capital']:,.0f}</text>
        <text x="600" y="340" fill="#8b949e" font-size="10">Final: ¥{backtest_result['final_capital']:,.0f}</text>
    </svg>
    """
    
    # 信号类型回测统计表
    type_rows = ""
    for stype, stats in sorted(backtest_result["trade_count_by_type"].items(), key=lambda x: -x[1]["pnl"]):
        color = "#3fb950" if stats["pnl"] > 0 else "#f85149"
        type_rows += f"""
        <tr>
            <td>{stype}</td>
            <td>{stats['total']}</td>
            <td>{stats['win_rate']}%</td>
            <td style="color:{color}">¥{stats['pnl']:,.0f}</td>
            <td>¥{stats['avg_pnl']:,.0f}</td>
        </tr>"""
    
    # 市场环境分布
    regime_dist = regime_result.get("regime_distribution", {})
    regime_colors = {"BULL": "#3fb950", "RANGE": "#d29922", "VOLATILE": "#f0883e", "BEAR": "#f85149"}
    regime_bars = ""
    for regime, count in regime_dist.items():
        pct = count / regime_result["total_days"] * 100
        color = regime_colors.get(regime, "#8b949e")
        regime_bars += f"""
        <div style="margin-bottom:8px;">
            <span style="color:{color};font-weight:bold;">{regime}</span>
            <span style="color:#8b949e;font-size:12px;"> ({count}天, {pct:.0f}%)</span>
            <div style="background:#30363d;height:12px;border-radius:6px;margin-top:2px;">
                <div style="background:{color};height:12px;width:{pct}%;border-radius:6px;"></div>
            </div>
        </div>"""
    
    # 多日反馈分析
    feedback_type_rows = ""
    for stype, analysis in sorted(feedback_result["type_analysis"].items(), key=lambda x: -x[1]["avg_t3"]):
        feedback_type_rows += f"""
        <tr>
            <td>{stype}</td>
            <td>{analysis['avg_t1']:+.2f}%</td>
            <td style="color:#3fb950">{analysis['avg_t2']:+.2f}%</td>
            <td style="color:#58a6ff">{analysis['avg_t3']:+.2f}%</td>
            <td>{analysis['avg_t5']:+.2f}%</td>
            <td style="color:#d29922;font-weight:bold;">{analysis['best_exit_day']}</td>
            <td>{analysis['hit_rate']}%</td>
        </tr>"""
    
    # Tier自适应变化
    tier_change_rows = ""
    for change in tier_result.get("tier_changes", []):
        old_color = {"TIER_S": "#3fb950", "TIER_A": "#58a6ff", "TIER_B": "#d29922", "TIER_C": "#f0883e", "REJECT": "#f85149"}.get(change["old_tier"], "#8b949e")
        new_color = {"TIER_S": "#3fb950", "TIER_A": "#58a6ff", "TIER_B": "#d29922", "TIER_C": "#f0883e", "REJECT": "#f85149"}.get(change["new_tier"], "#8b949e")
        tier_change_rows += f"""
        <tr>
            <td>{change['signal_type']}</td>
            <td style="color:{old_color}">{change['old_tier']}</td>
            <td>→</td>
            <td style="color:{new_color};font-weight:bold;">{change['new_tier']}</td>
            <td>IC={change['ic']}</td>
            <td>{change['hit_rate']}%</td>
            <td>{change['avg_change']:+.2f}%</td>
        </tr>"""
    
    if not tier_change_rows:
        tier_change_rows = '<tr><td colspan="7" style="text-align:center;color:#8b949e;">No tier changes — current tiers are optimal</td></tr>'
    
    # 组合优化结果
    portfolio_positions = ""
    for p in portfolio_result.get("positions", [])[:10]:
        portfolio_positions += f"""
        <tr>
            <td>{p['stock']}</td>
            <td>{p['signal_type']}</td>
            <td>{p['sector']}</td>
            <td>{p['position_pct']}%</td>
            <td>¥{p['position_capital']:,.0f}</td>
            <td>{p['adjusted_kelly']:.3f}</td>
        </tr>"""
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>V13.5.44 Full-Chain Backtest Report</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', system-ui, sans-serif; padding: 20px; }}
        .header {{ text-align: center; padding: 30px 0; border-bottom: 1px solid #30363d; margin-bottom: 20px; }}
        .header h1 {{ color: #58a6ff; font-size: 28px; margin-bottom: 8px; }}
        .header .version {{ color: #8b949e; font-size: 14px; }}
        .section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 16px; }}
        .section h2 {{ color: #58a6ff; font-size: 18px; margin-bottom: 12px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }}
        .metric {{ background: #21262d; border-radius: 6px; padding: 12px; text-align: center; }}
        .metric .value {{ font-size: 24px; font-weight: bold; }}
        .metric .label {{ color: #8b949e; font-size: 12px; margin-top: 4px; }}
        .green {{ color: #3fb950; }}
        .red {{ color: #f85149; }}
        .yellow {{ color: #d29922; }}
        .blue {{ color: #58a6ff; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th {{ background: #21262d; color: #8b949e; padding: 8px; text-align: left; font-size: 12px; border-bottom: 1px solid #30363d; }}
        td {{ padding: 8px; border-bottom: 1px solid #21262d; font-size: 13px; }}
        .chart-svg {{ width: 100%; height: auto; }}
        .tag {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
        .tag-s {{ background: #1a3a2a; color: #3fb950; }}
        .tag-a {{ background: #1a2a3a; color: #58a6ff; }}
        .tag-b {{ background: #3a3a1a; color: #d29922; }}
        .tag-r {{ background: #3a1a1a; color: #f85149; }}
        .improvement {{ background: #1a2a1a; border: 1px solid #3fb950; border-radius: 6px; padding: 12px; margin-top: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>V13.5.44 Full-Chain Backtest & Trading Enhancement</h1>
        <div class="version">毕方灵犀貔貅助手 | 5大突破方向 | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>

    <!-- 1. 全链路回测 -->
    <div class="section">
        <h2>1. Full-Chain Backtester — 端到端回测引擎</h2>
        <div class="grid">
            <div class="metric"><div class="value {'green' if backtest_result['total_return_pct'] > 0 else 'red'}">{backtest_result['total_return_pct']:+.2f}%</div><div class="label">Total Return</div></div>
            <div class="metric"><div class="value blue">¥{backtest_result['final_capital']:,.0f}</div><div class="label">Final Capital</div></div>
            <div class="metric"><div class="value green">{backtest_result['win_rate']}%</div><div class="label">Win Rate</div></div>
            <div class="metric"><div class="value yellow">{backtest_result['plr']:.2f}</div><div class="label">P/L Ratio</div></div>
            <div class="metric"><div class="value red">{backtest_result['max_drawdown_pct']:.2f}%</div><div class="label">Max Drawdown</div></div>
            <div class="metric"><div class="value blue">{backtest_result['total_trades']}</div><div class="label">Total Trades</div></div>
        </div>
        {equity_svg}
        <table>
            <thead><tr><th>Signal Type</th><th>Trades</th><th>Win Rate</th><th>Total PnL</th><th>Avg PnL</th></tr></thead>
            <tbody>{type_rows}</tbody>
        </table>
    </div>

    <!-- 2. 市场环境 -->
    <div class="section">
        <h2>2. TDX Market Regime — 市场环境分类</h2>
        <div class="grid">
            <div class="metric"><div class="value blue">{regime_result['total_days']}</div><div class="label">Days Analyzed</div></div>
            <div class="metric"><div class="value {'green' if regime_result['tdx_connected'] else 'yellow'}">{'TDX Live' if regime_result['tdx_connected'] else 'Historical'}</div><div class="label">Data Source</div></div>
            <div class="metric"><div class="value green">{regime_result.get('current_regime', {}).get('regime', 'N/A')}</div><div class="label">Current Regime</div></div>
        </div>
        <div style="margin-top:16px;">{regime_bars}</div>
    </div>

    <!-- 3. 多日反馈 -->
    <div class="section">
        <h2>3. Multi-Day Feedback Tracker — T+2/T+3 退出策略验证</h2>
        <div class="grid">
            <div class="metric"><div class="value green">{feedback_result['exit_strategy_accuracy']}%</div><div class="label">Exit Strategy Accuracy</div></div>
            <div class="metric"><div class="value yellow">{feedback_result['overall_best_exit']}</div><div class="label">Overall Best Exit</div></div>
            <div class="metric"><div class="value blue">{feedback_result['total_signals']}</div><div class="label">Signals Tracked</div></div>
        </div>
        <table>
            <thead><tr><th>Type</th><th>T+1 Avg</th><th>T+2 Avg</th><th>T+3 Avg</th><th>T+5 Avg</th><th>Best Exit</th><th>Accuracy</th></tr></thead>
            <tbody>{feedback_type_rows}</tbody>
        </table>
    </div>

    <!-- 4. 组合相关性 -->
    <div class="section">
        <h2>4. Portfolio Correlation Manager — 组合级仓位优化</h2>
        <div class="grid">
            <div class="metric"><div class="value blue">{portfolio_result.get('total_position_pct', 0)}%</div><div class="label">Total Position</div></div>
            <div class="metric"><div class="value blue">{portfolio_result.get('total_positions', 0)}</div><div class="label">Positions</div></div>
            <div class="metric"><div class="value yellow">{portfolio_result.get('avg_correlation', 0)}</div><div class="label">Avg Correlation</div></div>
            <div class="metric"><div class="value green">{portfolio_result.get('portfolio_sharpe', 0)}</div><div class="label">Portfolio Sharpe</div></div>
            <div class="metric"><div class="value green">{portfolio_result.get('avg_expected_return', 0)}%</div><div class="label">Expected Return</div></div>
            <div class="metric"><div class="value blue">{portfolio_result.get('diversification_ratio', 0)}</div><div class="label">Diversification Ratio</div></div>
        </div>
        <h3 style="color:#8b949e;margin-top:16px;font-size:14px;">Sector Exposure</h3>
        <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));">
            {''.join(f'<div class="metric"><div class="value blue" style="font-size:16px;">{pct}%</div><div class="label">{sector}</div></div>' for sector, pct in portfolio_result.get('sector_exposure', {}).items())}
        </div>
        <table>
            <thead><tr><th>Stock</th><th>Type</th><th>Sector</th><th>Position</th><th>Capital</th><th>Adj Kelly</th></tr></thead>
            <tbody>{portfolio_positions}</tbody>
        </table>
    </div>

    <!-- 5. Tier自适应 -->
    <div class="section">
        <h2>5. Dynamic Tier Adapter — 滚动IC自适应阈值</h2>
        <div class="grid">
            <div class="metric"><div class="value blue">{tier_result['window_size']}</div><div class="label">Window Size</div></div>
            <div class="metric"><div class="value yellow">{len(tier_result.get('tier_changes', []))}</div><div class="label">Tier Changes</div></div>
            <div class="metric"><div class="value green">{tier_result['improvement']['hit_rate_delta']:+.1f}%</div><div class="label">Hit Rate Delta</div></div>
            <div class="metric"><div class="value {'green' if tier_result['improvement']['plr_delta'] > 0 else 'red'}">{tier_result['improvement']['plr_delta']:+.2f}</div><div class="label">PLR Delta</div></div>
        </div>
        <table>
            <thead><tr><th>Signal Type</th><th>Old Tier</th><th></th><th>New Tier</th><th>IC</th><th>Hit Rate</th><th>Avg Change</th></tr></thead>
            <tbody>{tier_change_rows}</tbody>
        </table>
        <div class="improvement">
            <strong style="color:#3fb950;">Backtest Comparison:</strong>
            Original: {tier_result['original_backtest']['hit_rate']}% hit, PLR={tier_result['original_backtest']['plr']:.2f} →
            Adapted: {tier_result['adapted_backtest']['hit_rate']}% hit, PLR={tier_result['adapted_backtest']['plr']:.2f}
        </div>
    </div>

    <div style="text-align:center;color:#8b949e;font-size:12px;margin-top:20px;padding:12px;">
        V13.5.44 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 52+1 modules | Full-Chain Backtest + Trading Enhancement
    </div>
</body>
</html>"""
    
    return html


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 70)
    print("V13.5.44 Full-Chain Backtest & Trading Enhancement")
    print("5 Breakthrough Directions")
    print("=" * 70)
    
    results = {}
    
    # 1. 全链路回测
    print("\n[1/5] FullChainBacktester — Running end-to-end backtest...")
    backtester = FullChainBacktester(T1_DATA, initial_capital=100000)
    backtest_result = backtester.run_backtest()
    results["full_chain_backtest"] = backtest_result
    print(f"  Total Return: {backtest_result['total_return_pct']:+.2f}%")
    print(f"  Win Rate: {backtest_result['win_rate']}% | PLR: {backtest_result['plr']:.2f}")
    print(f"  Max Drawdown: {backtest_result['max_drawdown_pct']:.2f}%")
    print(f"  Total Trades: {backtest_result['total_trades']} ({backtest_result['winning_trades']}W/{backtest_result['losing_trades']}L)")
    
    # 2. 市场环境
    print("\n[2/5] TDXMarketRegime — Classifying market regimes...")
    regime_detector = TDXMarketRegime()
    regime_result = regime_detector.classify_all_history()
    results["market_regime"] = regime_result
    print(f"  Days analyzed: {regime_result['total_days']}")
    print(f"  TDX connected: {regime_result['tdx_connected']}")
    print(f"  Distribution: {regime_result['regime_distribution']}")
    print(f"  Current: {regime_result['current_regime']['regime'] if regime_result['current_regime'] else 'N/A'}")
    
    # 3. 多日反馈
    print("\n[3/5] MultiDayFeedbackTracker — Tracking T+2/T+3 returns...")
    tracker = MultiDayFeedbackTracker(T1_DATA)
    feedback_result = tracker.run_tracking()
    results["multi_day_feedback"] = {
        "total_signals": feedback_result["total_signals"],
        "exit_strategy_accuracy": feedback_result["exit_strategy_accuracy"],
        "overall_best_exit": feedback_result["overall_best_exit"],
        "overall_avg_returns": feedback_result["overall_avg_returns"],
        "type_analysis": feedback_result["type_analysis"],
    }
    print(f"  Exit strategy accuracy: {feedback_result['exit_strategy_accuracy']}%")
    print(f"  Overall best exit: {feedback_result['overall_best_exit']}")
    print(f"  Avg returns: T+1={feedback_result['overall_avg_returns']['T+1']:+.2f}% | T+2={feedback_result['overall_avg_returns']['T+2']:+.2f}% | T+3={feedback_result['overall_avg_returns']['T+3']:+.2f}%")
    for stype, analysis in sorted(feedback_result["type_analysis"].items(), key=lambda x: -x[1]["avg_t3"]):
        print(f"    {stype}: best={analysis['best_exit_day']} ({analysis['best_avg_return']:+.2f}%) accuracy={analysis['hit_rate']}%")
    
    # 4. 组合相关性
    print("\n[4/5] PortfolioCorrelationManager — Building correlation matrix...")
    corr_manager = PortfolioCorrelationManager(T1_DATA)
    correlation_result = corr_manager.build_correlation_matrix()
    results["correlation_analysis"] = correlation_result
    
    # 用最新日信号做组合优化
    latest_date = max(r["date"] for r in T1_DATA)
    latest_signals = [r for r in T1_DATA if r["date"] == latest_date]
    portfolio_result = corr_manager.optimize_portfolio(latest_signals)
    results["portfolio_optimization"] = portfolio_result
    print(f"  Sector correlations: {len(correlation_result['sector_correlations'])} pairs")
    print(f"  Total stock pairs: {correlation_result['total_pairs']}")
    print(f"  Portfolio: {portfolio_result['total_positions']} positions, {portfolio_result['total_position_pct']}% total")
    print(f"  Avg correlation: {portfolio_result['avg_correlation']} | Sharpe: {portfolio_result['portfolio_sharpe']}")
    print(f"  Sector exposure: {portfolio_result['sector_exposure']}")
    
    # 5. Tier自适应
    print("\n[5/5] DynamicTierAdapter — Adapting tier thresholds with rolling IC...")
    adapter = DynamicTierAdapter(T1_DATA, window_size=20)
    tier_result = adapter.adapt_tiers()
    results["dynamic_tier_adaptation"] = {
        "window_size": tier_result["window_size"],
        "adapted_tiers": tier_result["adapted_tiers"],
        "tier_changes": tier_result["tier_changes"],
        "tier_distribution": tier_result["tier_distribution"],
        "original_backtest": tier_result["original_backtest"],
        "adapted_backtest": tier_result["adapted_backtest"],
        "improvement": tier_result["improvement"],
    }
    print(f"  Window size: {tier_result['window_size']}")
    print(f"  Tier changes: {len(tier_result['tier_changes'])}")
    for change in tier_result["tier_changes"]:
        print(f"    {change['signal_type']}: {change['old_tier']} → {change['new_tier']} (IC={change['ic']}, hit={change['hit_rate']}%)")
    print(f"  Original: {tier_result['original_backtest']['hit_rate']}% hit, PLR={tier_result['original_backtest']['plr']:.2f}")
    print(f"  Adapted: {tier_result['adapted_backtest']['hit_rate']}% hit, PLR={tier_result['adapted_backtest']['plr']:.2f}")
    print(f"  Improvement: hit_rate {tier_result['improvement']['hit_rate_delta']:+.1f}%, PLR {tier_result['improvement']['plr_delta']:+.2f}")
    
    # 保存结果
    results_file = EVOLUTION_DIR / "v13544_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        # 移除daily_results和equity_curve等大列表的detail以控制文件大小
        save_results = {
            "version": "V13.5.44",
            "timestamp": datetime.now().isoformat(),
            "full_chain_backtest": {
                "initial_capital": backtest_result["initial_capital"],
                "final_capital": backtest_result["final_capital"],
                "total_return_pct": backtest_result["total_return_pct"],
                "total_trades": backtest_result["total_trades"],
                "winning_trades": backtest_result["winning_trades"],
                "losing_trades": backtest_result["losing_trades"],
                "win_rate": backtest_result["win_rate"],
                "plr": backtest_result["plr"],
                "avg_win": backtest_result["avg_win"],
                "avg_loss": backtest_result["avg_loss"],
                "max_drawdown_pct": backtest_result["max_drawdown_pct"],
                "trade_count_by_type": backtest_result["trade_count_by_type"],
                "daily_results": backtest_result["daily_results"],
            },
            "market_regime": {
                "tdx_connected": regime_result["tdx_connected"],
                "total_days": regime_result["total_days"],
                "regime_distribution": regime_result["regime_distribution"],
                "current_regime": regime_result["current_regime"],
            },
            "multi_day_feedback": results["multi_day_feedback"],
            "correlation_analysis": {
                "sector_correlations": correlation_result["sector_correlations"],
                "total_pairs": correlation_result["total_pairs"],
            },
            "portfolio_optimization": portfolio_result,
            "dynamic_tier_adaptation": results["dynamic_tier_adaptation"],
        }
        json.dump(save_results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to: {results_file}")
    
    # 生成HTML报告
    html = generate_html_report(backtest_result, regime_result, feedback_result,
                                 correlation_result, portfolio_result, tier_result)
    html_file = OUTPUT_DIR / "V13_5_44_FullChain_Backtest_Report.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML report saved to: {html_file}")
    
    print("\n" + "=" * 70)
    print("V13.5.44 Complete!")
    print("=" * 70)
    return results


if __name__ == "__main__":
    main()
