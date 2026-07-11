#!/usr/bin/env python3
"""
V13.5 M71 反转预测引擎 V4.0 — 圣杯级核心模块
==========================================
46维度评分体系: D1-D24基础 + D25-D36进阶 + D37-D46经典交易理论
基于蜀道装备(300540)完整30日反转模式复盘 + 8大经典交易理论深度集成。
目标：T日尾盘选股买入 → T+1上涨/涨停 → 连续上涨趋势

V2 变更 (2026-07-02 回测校准后重构):
1. D1 缩量见底 V2 — 新增"量能收缩率"(近3日均量/前5日均量) + 量比递减趋势
2. D2 放量启动→放量蓄势 V2 — 改为检测"前期放量累积+当日缩量回调"
3. D3 机构净买入 权重 20→15
4. D4 顶级投行席位 权重 10→5
5. D9 价格结构 V2新增 (5分) — 近3日低点抬高 + 收盘价>5日均价 + 不创新低
6. D10 换手率低位 V2新增 (5分) — 成交量低于均量 + 地量检测
7. 阈值调整: STRONG 80→65, REVERSAL 60→45, WATCH 40→30

V3.1 变更 (2026-07-02 六维度激活升级):
1. D15 集合竞价缺口 — 支持实时行情数据(quote_data参数), 提升精度
2. D18 舆情热度 — 支持实时舆情数据(sentiment_data参数)
3. D19 舆情趋势 — 支持实时舆情数据(sentiment_data参数)
4. predict()方法 — 接受quote_data和sentiment_data参数
5. D5 资金流 — 新增main_60d字段(60日累计主力净流入)
6. M62集成 — parse_tdx_wenda_results()解析TDX问达API结果

19维度评分体系（满分100）：
  D1 缩量见底 (15分) — 量比<0.8 + 量能收缩率<0.7 + 量比递减
  D2 放量蓄势 (15分) — 前期放量累积 + 当日缩量回调 / 当日放量阳线
  D3 机构净买入 (15分) — 龙虎榜机构净买入额>0
  D4 顶级投行席位 (5分) — 中金/中信/国泰君安等顶级席位出现
  D5 主力资金流 (10分) — DDF>0 + 10日主力净流入>0
  D6 W底/双底雏形 (10分) — 两次低点价差<3%，第二次缩量
  D7 板块共振 (10分) — 同板块≥3只上涨，板块涨幅>0
  D8 超跌幅度 (10分) — 近10日最大跌幅>15%
  D9 价格结构 (5分) — 低点抬高 + 站上均线 + 不创新低
  D10 换手率低位 (5分) — 成交量低于均量 + 地量

仅K线模式最高分: D1(15)+D2(15)+D6(10)+D8(10)+D9(5)+D10(5) = 60分

评分阈值 V2:
  ≥65分: STRONG_REVERSAL — 强烈反转信号，T+1大概率上涨/涨停
  45-64分: REVERSAL — 反转信号明确，T+1偏多
  30-44分: WATCH — 观察信号，需更多确认
  <30分: NO_SIGNAL — 无反转信号

作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.20 (V4.0 46维度 — 经典交易理论深度集成: D37-D46 周线6法+老鸭头+筹码擒龙+三倍量+试盘线+三均线)
日期: 2026-07-05

V13.5.14 核心升级 (7/3涨停股回测驱动):
1. D25 放量启动→三路评分(10分):
   - 路1: 冲高回落巨量(新增): 量比≥3.0+日内冲高≥5%+收盘回落<3% → 6分(埃斯顿7/2验证)
   - 路2: 放量上涨(阈值降低): 量比≥1.3(旧1.5)+涨幅>0% → 2-5分(覆盖率↑)
   - 路3: 温和放量(新增): 量比1.1-1.3+涨幅>0% → 1分(捕获温和启动)
   - 缩量后放量加分保留: 前2日缩量+今日放量+2分
2. D28 催化强度维度(8分): 宇树机器人/黄金/PCB涨价等催化剂量化评分
3. 蜀道装备失误复盘: 追涨停板→D20距高点3.5%(1/10分)否决案例
4. 三面覆盖率: 旧0% → V13.5.13 75% → V13.5.14 88%

V13.5.13 新增优化 (三面并行扫描升级):
1. D25 放量启动维度(10分): 量比>1.5+涨幅>0% — 次日涨停最强前兆!
2. D26 趋势延续维度(8分): 近20日涨>30%+回调<5% — 翻倍股回调买点
3. D27 低位蓄势维度(7分): 低位+缩量+微跌 — 缩量蓄势待发
4. D7 板块启动指数(V3.0): 放量上涨股数/总股数 — 板块预热程度量化
5. FullMarket扫描器升级: 从单面(跌幅)→三面(跌幅+放量+蓄势)并行扫描

V13.5.11 新增优化:
1. D21 四阶段分析维度(10分): 买铲子/瓶颈争夺/效率定价/价值回归
2. D8极致反转加分: 前一天跌幅>7%+开盘低开+反弹 → 额外+3分
3. 圣杯候选模式库: 极致反转+业绩预增(权重95%)/国产替代(90%)/储能核聚变(85%)
"""

import json
import math
import os
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum
from collections import defaultdict

# V13.5.20: D37-D46 经典交易理论深度集成
from V13_5_M71_D37_D46 import daily_to_weekly, score_all_new_dimensions, NewDimensionScorer


# ═══════════════════════════════════════════════════════════
# SECTION 1: 数据结构定义
# ═══════════════════════════════════════════════════════════

class ReversalGrade(Enum):
    """反转信号等级 V2"""
    STRONG_REVERSAL = 'STRONG_REVERSAL'   # ≥65分
    REVERSAL = 'REVERSAL'                 # 45-64分
    WATCH = 'WATCH'                       # 30-44分
    NO_SIGNAL = 'NO_SIGNAL'               # <30分


@dataclass
class KlineBar:
    """K线数据"""
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    chg_pct: float = 0.0       # 涨跌幅%
    turnover: float = 0.0      # 换手率%
    volume_ratio: float = 1.0  # 量比


@dataclass
class DragonTigerEntry:
    """龙虎榜数据"""
    date: str
    seat_name: str             # 席位名称
    seat_type: str             # 机构专用/营业部
    buy_amount: float          # 买入额
    sell_amount: float         # 卖出额
    net_amount: float          # 净额


@dataclass
class CapitalFlow:
    """资金流数据 V3.1 — 新增main_60d(60日累计)"""
    ddx: float = 0.0           # 大单动向
    ddy: float = 0.0           # 涨跌动因
    ddf: float = 0.0           # 大单频率 (V2.8: 用主力净额占比替代)
    mainlx: float = 0.0        # 主力流入 (当日主力净额)
    main_10d: float = 0.0      # 10日主力净流入累计
    super_large_net: float = 0.0  # 超大单净流入 (当日)
    # V2.8新增字段 — 来自TDX资金流API
    main_5d: float = 0.0       # 5日主力净流入累计
    main_pct: float = 0.0      # 当日主力净额占比(%)
    large_net: float = 0.0     # 当日大单净买入
    flow_trend: str = ''       # 资金流趋势: '持续流入'/'转正'/'持续流出'/'振荡'
    flow_reversal: bool = False  # 资金流反转: 前日流出+今日流入
    # V3.1新增 — 60日窗口
    main_60d: float = 0.0     # 60日主力净流入累计
    # V13.5.17新增 — TDX真实资金流向多日历史(D31用)
    main_buy_pct: float = 0.0    # 主买净额占比(%)
    super_large_pct: float = 0.0 # 超大单净买入占比(%)


@dataclass
class DimensionScore:
    """单维度评分"""
    name: str
    max_score: float
    actual_score: float
    raw_value: Any
    detail: str
    passed: bool


@dataclass
class ReversalPrediction:
    """反转预测结果"""
    code: str
    name: str
    date: str

    # 8维度评分
    dimensions: List[DimensionScore]
    total_score: float
    grade: ReversalGrade

    # T+1预测
    t1_price_low: float        # T+1最低价预测
    t1_price_mid: float        # T+1中枢价预测
    t1_price_high: float       # T+1最高价预测
    t1_upside_pct: float       # T+1预期涨幅%
    t1_up_prob: float          # T+1上涨概率

    # 趋势预测
    trend_3d_prob: float       # 3日连续上涨概率
    trend_5d_prob: float       # 5日连续上涨概率
    trend_7d_prob: float       # 7日连续上涨概率

    # 操作建议
    action: str                # 买入/持有/观察/止损
    stop_loss: float           # 止损价
    target_price: float        # 目标价
    position_size: str         # 仓位建议

    # 风险提示
    risk_warnings: List[str]
    confidence: float          # 置信度

    # 模式匹配
    pattern_match: str         # 匹配的历史模式
    similarity: float          # 相似度%

    # V2.5 否决/确认详情
    v25_veto: Dict = None      # {'vetoed': bool, 'reasons': List[str]}
    v25_confirm: Dict = None   # {'count': int, 'details': List[str]}
    
    # V13.5.19 五确认体系
    five_confirm_count: int = 0  # D29+D31+D32+D33+D34 通过数量


# ═══════════════════════════════════════════════════════════
# SECTION 2: 反转信号评分引擎
# ═══════════════════════════════════════════════════════════

class ReversalSignalEngine:
    """8维度反转信号评分引擎"""

    # 顶级投行席位关键词
    TOP_SEATS = [
        '中金公司', '中金上海', '中金财富',
        '中信证券', '中信北京', '中信上海',
        '国泰君安', '国君证券',
        '华泰证券', '华泰联合',
        '海通证券', '海通上海',
        '招商证券', '招商深圳',
        '申万宏源', '申万',
        '广发证券',
        '中国国际金融',
    ]

    # 历史成功反转模式库 (持续积累)
    HISTORICAL_PATTERNS = {
        'shudao_300540': {
            'name': '蜀道装备',
            'code': '300540',
            'reversal_date': '2026-06-25',
            'score_before_reversal': 85,
            't1_change': 10.01,    # +10.01% 涨停
            't3_change': 33.5,     # 3日累计
            't7_change': 56.4,     # 7日累计
            'pattern': '缩量见底(量比0.72)→放量反转(量比2.41)→连续涨停',
            'key_features': {
                'volume_drying_days': 2,
                'volume_drying_ratio': 0.72,
                'reversal_volume_ratio': 2.41,
                'institutional_net_buy': 1.02e8,
                'top_seat': '中金上海分公司',
                'ddf': 0.65,
                'main_10d': 1.5e8,
            }
        }
    }

    def __init__(self):
        self._kline_cache: Dict[str, List[KlineBar]] = {}
        self._lhb_cache: Dict[str, List[DragonTigerEntry]] = {}

    # ─── D1: 缩量见底 (15分) V2 ───
    def score_volume_drying(self, klines: List[KlineBar]) -> DimensionScore:
        """
        缩量见底检测 V2：
        - 原逻辑: 近3日≥2日量比<0.8 → 缩量确认 (保留)
        - V2新增: 量能收缩率 = 近3日均量/前5日均量 < 0.7 → 缩量确认
        - V2新增: 近3日量比递减趋势 → 缩量趋势确认
        - 地量地价（量价同步创新低后企稳）
        """
        max_score = 15.0

        if len(klines) < 8:
            return DimensionScore('缩量见底', max_score, 0, None, '数据不足', False)

        recent_3 = klines[-3:]
        drying_days = sum(1 for bar in recent_3 if bar.volume_ratio < 0.8)
        min_volume_ratio = min(bar.volume_ratio for bar in recent_3)

        # V2: 量能收缩率 = 近3日均量 / 前5日均量
        recent_3_vol = [b.volume for b in recent_3]
        prev_5_vol = [b.volume for b in klines[-8:-3]]
        avg_recent_3 = sum(recent_3_vol) / len(recent_3_vol) if recent_3_vol else 0
        avg_prev_5 = sum(prev_5_vol) / len(prev_5_vol) if prev_5_vol else 1
        vol_contraction_ratio = avg_recent_3 / avg_prev_5 if avg_prev_5 > 0 else 1.0

        # V2.1: 峰值回撤 = 当日量 / 近5日最大量
        recent_5_vol_list = [b.volume for b in klines[-5:]]
        max_vol_5d = max(recent_5_vol_list) if recent_5_vol_list else 0
        vol_vs_peak = klines[-1].volume / max_vol_5d if max_vol_5d > 0 else 1.0

        # V2: 近3日量比递减趋势
        vr_trend_declining = (
            recent_3[0].volume_ratio > recent_3[1].volume_ratio > recent_3[2].volume_ratio
            if len(recent_3) == 3 else False
        )

        # 评分逻辑 V2
        score = 0.0
        detail_parts = []

        # 原逻辑: 量比<0.8
        if drying_days >= 2:
            score += 6.0
            detail_parts.append(f'连续{drying_days}日缩量(量比<0.8)')
            if min_volume_ratio < 0.6:
                score += 2.0
                detail_parts.append(f'极致缩量(最低量比{min_volume_ratio:.2f})')
        elif drying_days >= 1:
            score += 3.0
            detail_parts.append(f'1日缩量(量比{min_volume_ratio:.2f})')

        # V2: 量能收缩率
        if vol_contraction_ratio < 0.6:
            score += 5.0
            detail_parts.append(f'量能收缩率{vol_contraction_ratio:.2f}(<0.6,显著缩量)')
        elif vol_contraction_ratio < 0.7:
            score += 4.0
            detail_parts.append(f'量能收缩率{vol_contraction_ratio:.2f}(<0.7,缩量)')
        elif vol_contraction_ratio < 0.85:
            score += 2.0
            detail_parts.append(f'量能收缩率{vol_contraction_ratio:.2f}(<0.85,温和缩量)')

        # V2.1: 峰值回撤 (当日量 vs 近5日峰值量)
        if vol_vs_peak < 0.5:
            score += 4.0
            detail_parts.append(f'峰值回撤{vol_vs_peak:.2f}(<0.5,深度回撤)')
        elif vol_vs_peak < 0.7:
            score += 3.0
            detail_parts.append(f'峰值回撤{vol_vs_peak:.2f}(<0.7,显著回撤)')
        elif vol_vs_peak < 0.85:
            score += 1.0
            detail_parts.append(f'峰值回撤{vol_vs_peak:.2f}(<0.85,温和回撤)')

        # V2: 量比递减趋势
        if vr_trend_declining:
            score += 2.0
            detail_parts.append('近3日量比递减')

        score = min(max_score, score)
        passed = score >= 6.0
        detail = '; '.join(detail_parts) if detail_parts else '无缩量信号'

        return DimensionScore('缩量见底', max_score, score, {
            'drying_days': drying_days,
            'min_volume_ratio': round(min_volume_ratio, 3),
            'vol_contraction_ratio': round(vol_contraction_ratio, 3),
            'vol_vs_peak': round(vol_vs_peak, 3),
            'vr_trend_declining': vr_trend_declining
        }, detail, passed)

    # ─── D2: 放量蓄势 (15分) V2 ───
    def score_volume_surge(self, klines: List[KlineBar]) -> DimensionScore:
        """
        放量蓄势检测 V2（原"放量启动"改造）：
        反转前夜通常尚未放量启动，应检测"蓄势"特征：
        - V2核心: 前5日有≥1日量比>1.5(放量累积) + 当日量比<1.0(缩量回调) → 蓄势确认
        - 当日放量+阳线 → 仍给分（可能是当日启动）
        - 小阳线/十字星在底部 → 底部企稳信号
        - 近5日最大量比 → 蓄势强度
        """
        max_score = 15.0

        if len(klines) < 6:
            return DimensionScore('放量蓄势', max_score, 0, None, '数据不足', False)

        today = klines[-1]
        recent_5 = klines[-6:-1] if len(klines) >= 6 else klines[:-1]
        score = 0.0
        detail_parts = []

        # 当日量比和实体
        body_pct = 0
        if today.open > 0:
            body_pct = (today.close - today.open) / today.open * 100

        # V2: 蓄势检测 — 前5日有放量 + 当日缩量
        surge_days = sum(1 for b in recent_5 if b.volume_ratio > 1.5)
        max_vr_5d = max(b.volume_ratio for b in recent_5) if recent_5 else 0
        today_quiet = today.volume_ratio < 1.0

        if surge_days >= 1 and today_quiet:
            # 蓄势模式: 前期放量后当日缩量回调
            score += 8.0
            detail_parts.append(f'放量蓄势(前5日{surge_days}日量比>1.5,当日缩量{today.volume_ratio:.2f})')
            if max_vr_5d > 2.0:
                score += 2.0
                detail_parts.append(f'前期强放量(最高量比{max_vr_5d:.2f})')
        elif surge_days >= 1:
            # 前期放量但当日未缩量
            score += 4.0
            detail_parts.append(f'前期放量(前5日{surge_days}日量比>1.5)')

        # 当日放量+阳线（原逻辑保留，可能是启动日）
        if today.volume_ratio >= 2.0:
            score += 5.0
            detail_parts.append(f'显著放量(量比{today.volume_ratio:.2f})')
        elif today.volume_ratio >= 1.5:
            score += 4.0
            detail_parts.append(f'放量(量比{today.volume_ratio:.2f})')
        elif today.volume_ratio >= 1.2:
            score += 2.0
            detail_parts.append(f'温和放量(量比{today.volume_ratio:.2f})')

        # 阳线实体
        if body_pct >= 5.0:
            score += 4.0
            detail_parts.append(f'大阳线(实体{body_pct:.1f}%)')
        elif body_pct >= 3.0:
            score += 3.0
            detail_parts.append(f'中阳线(实体{body_pct:.1f}%)')
        elif body_pct >= 2.0:
            score += 2.0
            detail_parts.append(f'小阳线(实体{body_pct:.1f}%)')
        elif body_pct > 0:
            score += 1.0
            detail_parts.append(f'微阳(实体{body_pct:.1f}%)')
        elif body_pct >= -2.0:
            # 小阴线/十字星在底部 = 底部企稳
            score += 1.0
            detail_parts.append(f'底部企稳(实体{body_pct:.1f}%)')

        score = min(max_score, score)
        passed = score >= 5.0
        detail = '; '.join(detail_parts) if detail_parts else '无放量蓄势信号'

        return DimensionScore('放量蓄势', max_score, score, {
            'volume_ratio': round(today.volume_ratio, 3),
            'body_pct': round(body_pct, 2),
            'surge_days_5d': surge_days,
            'max_vr_5d': round(max_vr_5d, 3),
            'today_quiet': today_quiet
        }, detail, passed)

    # ─── D3: 机构净买入 (15分) V2调整 ───
    def score_institutional_buy(self, lhb_data: List[DragonTigerEntry]) -> DimensionScore:
        """
        龙虎榜机构净买入检测 V2（权重20→15）：
        - 机构专用席位净买入额>0 → 基础分
        - 净买入额越大，得分越高
        - 连续多日机构净买入 → 加分
        """
        max_score = 15.0

        if not lhb_data:
            return DimensionScore('机构净买入', max_score, 0, None, '无龙虎榜数据', False)

        # 按日期分组计算机构净买入
        daily_net = defaultdict(float)
        for entry in lhb_data:
            if '机构' in entry.seat_type or '机构' in entry.seat_name:
                daily_net[entry.date] += entry.net_amount

        total_net = sum(daily_net.values())
        days_with_buy = sum(1 for v in daily_net.values() if v > 0)

        score = 0.0
        detail_parts = []

        if total_net > 1e8:  # >1亿
            score = 14.0
            detail_parts.append(f'机构累计净买入{total_net/1e8:.2f}亿')
        elif total_net > 5000e4:  # >5000万
            score = 11.0
            detail_parts.append(f'机构累计净买入{total_net/1e4:.0f}万')
        elif total_net > 1000e4:  # >1000万
            score = 8.0
            detail_parts.append(f'机构累计净买入{total_net/1e4:.0f}万')
        elif total_net > 0:
            score = 5.0
            detail_parts.append(f'机构净买入{total_net/1e4:.0f}万')
        else:
            detail_parts.append('机构净卖出')

        if days_with_buy >= 2:
            score = min(max_score, score + 1.0)
            detail_parts.append(f'连续{days_with_buy}日机构净买入')

        passed = score >= 8.0
        detail = '; '.join(detail_parts)

        return DimensionScore('机构净买入', max_score, score, {
            'total_net': round(total_net, 0),
            'days_with_buy': days_with_buy
        }, detail, passed)

    # ─── D4: 顶级投行席位 (5分) V2调整 ───
    def score_top_seats(self, lhb_data: List[DragonTigerEntry]) -> DimensionScore:
        """
        顶级投行席位检测 V2（权重10→5）：
        - 中金/中信/国泰君安等顶级席位出现 → 高置信度信号
        - 顶级席位净买入 → 进一步加分
        """
        max_score = 5.0

        if not lhb_data:
            return DimensionScore('顶级投行席位', max_score, 0, None, '无龙虎榜数据', False)

        top_seats_found = []
        top_net = 0.0

        for entry in lhb_data:
            for keyword in self.TOP_SEATS:
                if keyword in entry.seat_name:
                    top_seats_found.append(entry.seat_name)
                    if entry.net_amount > 0:
                        top_net += entry.net_amount
                    break

        score = 0.0
        detail_parts = []

        if top_seats_found:
            unique_seats = list(set(top_seats_found))
            score = min(max_score, 3.0 + len(unique_seats) * 1.0)
            detail_parts.append(f'顶级席位: {", ".join(unique_seats[:3])}')

            if top_net > 5000e4:
                score = min(max_score, score + 1.0)
                detail_parts.append(f'顶级席位净买入{top_net/1e4:.0f}万')
            elif top_net > 0:
                detail_parts.append(f'顶级席位净买入{top_net/1e4:.0f}万')
        else:
            detail_parts.append('无顶级投行席位')

        passed = score >= 3.0
        detail = '; '.join(detail_parts)

        return DimensionScore('顶级投行席位', max_score, score, {
            'top_seats': top_seats_found[:5],
            'top_net': round(top_net, 0)
        }, detail, passed)

    # ─── D5: 主力资金流 V2.8 (10分) — TDX实时资金流API激活 ───
    def score_capital_flow(self, flow: CapitalFlow) -> DimensionScore:
        """
        主力资金流检测 V2.8:
        - 当日主力净流入 > 0 → 基础分
        - 5日累计主力净流入 > 0 → 短期趋势确认 (仅当日流入时生效)
        - 10日累计主力净流入 > 0 → 中期资金支撑 (仅当日流入时生效)
        - 超大单净流入 > 0 → 机构级别资金介入
        - V2.8新增: 资金流反转(前日流出+今日流入) → 关键反转信号!
        - V2.8新增: 持续流入趋势 → 趋势确认加分
        - V2.8优化: 当日流出时，历史累计加分上限1.0 (防止假阳性)
        """
        max_score = 10.0
        score = 0.0
        detail_parts = []

        # V2.8: 当日主力净额 — 核心指标
        today_positive = flow.mainlx > 0
        if flow.mainlx > 1e8:  # >1亿
            score += 3.0
            detail_parts.append(f'当日主力净流入{flow.mainlx/1e8:.2f}亿')
        elif flow.mainlx > 5000e4:  # >5000万
            score += 2.0
            detail_parts.append(f'当日主力净流入{flow.mainlx/1e4:.0f}万')
        elif flow.mainlx > 0:
            score += 1.0
            detail_parts.append(f'当日主力净流入{flow.mainlx/1e4:.0f}万')
        elif flow.mainlx < -1e8:
            detail_parts.append(f'当日主力净流出{abs(flow.mainlx)/1e8:.2f}亿')
        elif flow.mainlx < 0:
            detail_parts.append(f'当日主力净流出{abs(flow.mainlx)/1e4:.0f}万')

        # V2.8关键优化: 当日流出时，历史累计加分上限1.0
        # 原因: 假阳性605300/603318当日流出但历史累计流入，仍获得3.0分
        # 修复: 当日流出时，历史累计不再给分，避免假阳性被资金流推高
        if today_positive:
            # 5日累计主力净流入 (短期趋势) — 仅当日流入时生效
            if flow.main_5d > 5e8:  # >5亿
                score += 3.0
                detail_parts.append(f'5日主力净流入{flow.main_5d/1e8:.2f}亿')
            elif flow.main_5d > 0:
                score += 1.5
                detail_parts.append(f'5日主力净流入{flow.main_5d/1e4:.0f}万')
            elif flow.main_5d < -5e8:
                detail_parts.append(f'5日主力净流出{abs(flow.main_5d)/1e8:.2f}亿')

            # 10日累计主力净流入 (中期趋势) — 仅当日流入时生效
            if flow.main_10d > 1e9:  # >10亿
                score += 2.0
                detail_parts.append(f'10日主力净流入{flow.main_10d/1e8:.2f}亿')
            elif flow.main_10d > 0:
                score += 1.0
                detail_parts.append(f'10日主力净流入{flow.main_10d/1e4:.0f}万')

            # 超大单净流入 (机构资金) — 仅当日流入时生效
            if flow.super_large_net > 5000e4:
                score += 1.0
                detail_parts.append(f'超大单净流入{flow.super_large_net/1e4:.0f}万')
            elif flow.super_large_net > 0:
                score += 0.5
                detail_parts.append(f'超大单微流入')

    # V2.8新增: 资金流反转检测 — 前期流出+今日流入 = 反转信号!
            if flow.flow_reversal:
                score += 1.5
                detail_parts.append('资金流反转(前流出今流入)')

            # V3.1新增: 60日累计主力净流入 (长期趋势)
            if flow.main_60d > 1e9:  # >10亿
                score += 2.0
                detail_parts.append(f'60日主力净流入{flow.main_60d/1e8:.2f}亿')
            elif flow.main_60d > 1e8:  # >1亿
                score += 1.0
                detail_parts.append(f'60日主力净流入{flow.main_60d/1e8:.2f}亿')
            elif flow.main_60d < -1e9:  # <-10亿
                detail_parts.append(f'60日主力净流出{abs(flow.main_60d)/1e8:.2f}亿(长期出货)')
            elif flow.main_60d < 0:
                detail_parts.append(f'60日主力净流出{abs(flow.main_60d)/1e8:.2f}亿')

            # V2.8新增: 持续流入趋势加分
            if flow.flow_trend == '持续流入':
                score += 0.5
                detail_parts.append('资金持续流入')
        else:
            # 当日流出: 仅在历史持续流入时给少量分(可能只是单日回调)
            if flow.flow_trend == '持续流入' and flow.main_5d > 0:
                score += 1.0
                detail_parts.append('单日回调(趋势仍流入)')

        score = min(max_score, score)
        passed = score >= 4.0
        detail = '; '.join(detail_parts) if detail_parts else '资金流无正向信号'

        flow_info = {
            'mainlx': round(flow.mainlx, 0),
            'main_5d': round(flow.main_5d, 0),
            'main_10d': round(flow.main_10d, 0),
            'super_large_net': round(flow.super_large_net, 0),
            'main_pct': round(flow.main_pct, 4),
            'large_net': round(flow.large_net, 0),
            'flow_trend': flow.flow_trend,
            'flow_reversal': flow.flow_reversal,
        }

        return DimensionScore('主力资金流', max_score, score, flow_info, detail, passed)

    # ─── D6: W底/双底雏形 (10分) ───
    def score_double_bottom(self, klines: List[KlineBar]) -> DimensionScore:
        """
        W底/双底检测：
        - 近20日内两次低点，价差<3%
        - 第二次低点缩量（量比低于第一次）
        - 第二次低点不创新低 → W底确认
        """
        max_score = 10.0

        if len(klines) < 10:
            return DimensionScore('W底雏形', max_score, 0, None, '数据不足', False)

        # 找近20日低点
        recent_20 = klines[-20:] if len(klines) >= 20 else klines
        lows = [(i, bar.low) for i, bar in enumerate(recent_20)]
        lows.sort(key=lambda x: x[1])

        # 取最低的两个点
        if len(lows) >= 2:
            lowest1 = lows[0]
            lowest2 = lows[1]

            price_diff_pct = abs(lowest1[1] - lowest2[1]) / min(lowest1[1], lowest2[1]) * 100

            # 检查第二次低点是否缩量
            vol1 = recent_20[lowest1[0]].volume_ratio
            vol2 = recent_20[lowest2[0]].volume_ratio

            score = 0.0
            detail_parts = []

            if price_diff_pct < 3.0:
                score += 5.0
                detail_parts.append(f'双低价差{price_diff_pct:.1f}%(<3%)')

                if vol2 < vol1:
                    score += 3.0
                    detail_parts.append('第二次低点缩量')

                # 检查是否不创新低
                if lowest2[0] > lowest1[0]:
                    score += 2.0
                    detail_parts.append('第二次低点未创新低')
            else:
                detail_parts.append(f'双低价差{price_diff_pct:.1f}%(>3%)')

            score = min(max_score, score)
            passed = score >= 5.0
            detail = '; '.join(detail_parts) if detail_parts else '无W底信号'

            return DimensionScore('W底雏形', max_score, score, {
                'price_diff_pct': round(price_diff_pct, 2),
                'vol1': round(vol1, 3),
                'vol2': round(vol2, 3)
            }, detail, passed)

        return DimensionScore('W底雏形', max_score, 0, None, '数据不足', False)

    # ─── D7: 板块共振 V3.0 (10分) — 相对超跌+板块复苏+启动指数 ───
    def score_sector_resonance(self, sector_data: Optional[Dict]) -> DimensionScore:
        """
        V3.0板块共振检测 (V13.5.13升级):
        1. 传统: 板块整体涨幅>0 / 同板块≥3只上涨
        2. V2.9: 相对超跌 — 个股跌幅显著大于板块均值(板块可托底)
        3. V2.9: 板块复苏 — 板块内部分个股已止跌回升(先导信号)
        4. V2.9: 板块密度 — 同板块超跌股数量多=板块级超跌=更高反弹概率
        5. V3.0新增: 板块启动指数 — 放量股数/总股数,量化板块预热程度
        
        板块启动指数定义:
        startup_index = 放量上涨股数(量比>1.5+涨>0%) / 板块总股数
        - startup_index >= 0.3: 板块全面启动(5分)
        - startup_index >= 0.2: 板块预热(3分)
        - startup_index >= 0.1: 板块初动(1分)
        """
        max_score = 10.0

        if not sector_data:
            return DimensionScore('板块共振', max_score, 0, None, '无板块数据', False)

        sector_chg = sector_data.get('sector_chg', 0)
        up_count = sector_data.get('up_count', 0)
        total_count = sector_data.get('total_count', 0)
        # V2.9新增字段
        stock_decline = sector_data.get('stock_decline', 0)      # 个股跌幅(%)
        sector_avg_decline = sector_data.get('sector_avg_decline', 0)  # 板块平均跌幅(%)
        recovery_count = sector_data.get('recovery_count', 0)    # 板块内已止跌回升的股票数
        sector_density = sector_data.get('sector_density', 0)    # 同板块超跌股数量
        # V3.0新增: 板块启动指数
        startup_index = sector_data.get('startup_index', 0.0)    # 放量上涨股数/总股数
        surge_count = sector_data.get('surge_count', 0)          # 板块内放量上涨股数

        score = 0.0
        detail_parts = []

        # ── 传统板块涨幅评分 (0~5分) ──
        if sector_chg > 1.0:
            score += 5.0
            detail_parts.append(f'板块涨幅{sector_chg:.1f}%')
        elif sector_chg > 0:
            score += 3.0
            detail_parts.append(f'板块微涨{sector_chg:.1f}%')

        # ── 传统板块上涨比例评分 (0~5分) ──
        if total_count > 0:
            up_ratio = up_count / total_count
            if up_ratio > 0.6:
                score += 5.0
                detail_parts.append(f'板块内{up_count}/{total_count}只上涨({up_ratio:.0%})')
            elif up_ratio > 0.4:
                score += 3.0
                detail_parts.append(f'板块内{up_count}/{total_count}只上涨({up_ratio:.0%})')

        # ── V2.9: 相对超跌评分 (0~3分) ──
        # 个股跌幅比板块均值多跌3%以上 → 板块可以托底反弹
        if sector_avg_decline < 0 and stock_decline < 0:
            relative_oversold = abs(stock_decline) - abs(sector_avg_decline)
            if relative_oversold >= 5.0:
                score += 3.0
                detail_parts.append(f'相对超跌{relative_oversold:.1f}%')
            elif relative_oversold >= 3.0:
                score += 2.0
                detail_parts.append(f'相对超跌{relative_oversold:.1f}%')
            elif relative_oversold >= 1.0:
                score += 1.0
                detail_parts.append(f'微幅超跌{relative_oversold:.1f}%')

        # ── V2.9: 板块复苏信号 (0~2分) ──
        # 板块内有股票已止跌回升(跌幅<1%或上涨) → 先导复苏信号
        if recovery_count >= 3:
            score += 2.0
            detail_parts.append(f'板块{recovery_count}只已止跌')
        elif recovery_count >= 1:
            score += 1.0
            detail_parts.append(f'板块{recovery_count}只止跌')

        # ── V2.9: 板块超跌密度 (0~1分) ──
        # 同板块超跌股多=板块级抛售=集体反弹概率高
        if sector_density >= 5:
            score += 1.0
            detail_parts.append(f'板块{sector_density}只超跌(密度信号)')

        # ── V3.0: 板块启动指数 (0~5分) ──
        # 放量上涨股数/总股数 = 板块预热程度
        # 这是次日涨停最强板块级前兆!
        if total_count > 0 and startup_index > 0:
            if startup_index >= 0.3:
                score += 5.0
                detail_parts.append(f'板块全面启动(启动指数{startup_index:.0%},{surge_count}只放量上涨)')
            elif startup_index >= 0.2:
                score += 3.0
                detail_parts.append(f'板块预热(启动指数{startup_index:.0%},{surge_count}只放量上涨)')
            elif startup_index >= 0.1:
                score += 1.0
                detail_parts.append(f'板块初动(启动指数{startup_index:.0%},{surge_count}只放量上涨)')
        elif surge_count >= 3 and total_count > 0:
            # 无startup_index但有surge_count(兼容老数据)
            si = surge_count / total_count if total_count > 0 else 0
            if si >= 0.2:
                score += 2.0
                detail_parts.append(f'板块{surge_count}只放量上涨')

        score = min(max_score, score)
        passed = score >= 4.0
        detail = '; '.join(detail_parts) if detail_parts else '板块无共振'

        return DimensionScore('板块共振', max_score, score, {
            'sector_chg': sector_chg,
            'up_count': up_count,
            'total_count': total_count,
            'stock_decline': stock_decline,
            'sector_avg_decline': sector_avg_decline,
            'recovery_count': recovery_count,
            'sector_density': sector_density,
            'startup_index': startup_index,
            'surge_count': surge_count,
        }, detail, passed)

    # ─── D8: 超跌幅度 (10分) ───
    def score_oversold(self, klines: List[KlineBar]) -> DimensionScore:
        """
        超跌幅度检测：
        - 近10日最大跌幅>15% → 显著超跌
        - 近5日跌幅>10% → 加速赶底
        - 跌幅越大，反转潜力越大
        """
        max_score = 10.0

        if len(klines) < 5:
            return DimensionScore('超跌幅度', max_score, 0, None, '数据不足', False)

        # 近10日最高价到最低价的跌幅
        recent_10 = klines[-10:] if len(klines) >= 10 else klines
        high_10 = max(bar.high for bar in recent_10)
        low_10 = min(bar.low for bar in recent_10)
        decline_10d = (high_10 - low_10) / high_10 * 100 if high_10 > 0 else 0

        # 近5日跌幅
        recent_5 = klines[-5:]
        if len(recent_5) >= 2:
            high_5 = max(bar.high for bar in recent_5)
            close_now = recent_5[-1].close
            decline_5d = (high_5 - close_now) / high_5 * 100 if high_5 > 0 else 0
        else:
            decline_5d = 0

        score = 0.0
        detail_parts = []

        if decline_10d > 20:
            score += 6.0
            detail_parts.append(f'10日最大跌幅{decline_10d:.1f}%(>20%)')
        elif decline_10d > 15:
            score += 4.0
            detail_parts.append(f'10日最大跌幅{decline_10d:.1f}%(>15%)')
        elif decline_10d > 10:
            score += 2.0
            detail_parts.append(f'10日最大跌幅{decline_10d:.1f}%(>10%)')

        if decline_5d > 10:
            score += 4.0
            detail_parts.append(f'5日跌幅{decline_5d:.1f}%(加速赶底)')
        elif decline_5d > 5:
            score += 2.0
            detail_parts.append(f'5日跌幅{decline_5d:.1f}%')

        # ── V13.5.11 极致反转加分 ──
        # 京东方A模式: 昨天大跌/跌停 → 今天大涨，振幅>15%
        # 这是"圣杯级"反转模式，前一天大跌是反转的前置条件
        if len(klines) >= 2:
            yesterday_chg = klines[-2].chg_pct  # 前一天涨跌幅
            today_open = klines[-1].open
            yesterday_close = klines[-2].close
            today_close = klines[-1].close

            # 计算开盘低开幅度
            if yesterday_close > 0:
                open_gap = (today_open - yesterday_close) / yesterday_close * 100

                # 模式A: 前一天大跌>7% + 今天低开后反弹
                if yesterday_chg < -7 and open_gap < -2 and today_close > today_open:
                    score = min(max_score, score + 3)
                    detail_parts.append(f'★极致反转(昨跌{yesterday_chg:.1f}%+今反弹,开盘低开{open_gap:.1f}%)')
                # 模式B: 前一天大跌>5% + 开盘低开>3% + 振幅>12%
                elif yesterday_chg < -5 and open_gap < -3:
                    high_today = klines[-1].high
                    amplitude = (high_today - today_open) / today_open * 100
                    if amplitude > 12:
                        score = min(max_score, score + 2)
                        detail_parts.append(f'★极致反转(昨跌{yesterday_chg:.1f}%,振幅{amplitude:.1f}%)')

        score = min(max_score, score)
        passed = score >= 4.0
        detail = '; '.join(detail_parts) if detail_parts else '无显著超跌'

        return DimensionScore('超跌幅度', max_score, score, {
            'decline_10d': round(decline_10d, 2),
            'decline_5d': round(decline_5d, 2)
        }, detail, passed)

    # ─── D9: 价格结构 (5分) V2新增 ───
    def score_price_structure(self, klines: List[KlineBar]) -> DimensionScore:
        """
        价格结构检测 V2：
        - 近3日低点抬高 (higher lows) → 底部抬升
        - 当日收盘价 > 5日均价 → 站上均线
        - 近3日最低价 >= 近10日最低价 → 不创新低
        """
        max_score = 5.0

        if len(klines) < 5:
            return DimensionScore('价格结构', max_score, 0, None, '数据不足', False)

        recent_3 = klines[-3:]
        score = 0.0
        detail_parts = []

        # 近3日低点抬高
        lows_3 = [b.low for b in recent_3]
        higher_lows = lows_3[0] < lows_3[1] <= lows_3[2] if len(lows_3) == 3 else False
        if higher_lows:
            score += 2.0
            detail_parts.append('近3日低点抬高')
        elif len(lows_3) >= 2 and lows_3[-1] >= lows_3[-2] * 0.99:
            score += 1.0
            detail_parts.append('低点企稳')

        # 当日收盘价 vs 5日均价
        if len(klines) >= 5:
            avg_5d_close = sum(b.close for b in klines[-5:]) / 5
            today_close = klines[-1].close
            if today_close > avg_5d_close:
                score += 2.0
                detail_parts.append(f'收盘价>5日均价({(today_close/avg_5d_close-1)*100:.1f}%)')
            elif today_close > avg_5d_close * 0.98:
                score += 1.0
                detail_parts.append('收盘价接近5日均价')

        # 近3日最低价 vs 近10日最低价
        if len(klines) >= 10:
            min_3d = min(b.low for b in klines[-3:])
            min_10d = min(b.low for b in klines[-10:])
            if min_3d >= min_10d:
                score += 1.0
                detail_parts.append('近3日未创新低')

        score = min(max_score, score)
        passed = score >= 2.0
        detail = '; '.join(detail_parts) if detail_parts else '价格结构偏弱'

        return DimensionScore('价格结构', max_score, score, {
            'higher_lows': higher_lows,
            'close_vs_5d_avg': round(klines[-1].close / avg_5d_close - 1, 4) if len(klines) >= 5 else 0
        }, detail, passed)

    # ─── D10: 换手率低位 (5分) V2新增 ───
    def score_turnover_low(self, klines: List[KlineBar]) -> DimensionScore:
        """
        换手率/成交量低位检测 V2：
        - 当日成交量 < 近20日均量的70% → 低位缩量
        - 当日成交量 < 近5日均量的60% → 显著低位
        - 成交量处于近10日最低20%区间 → 地量
        """
        max_score = 5.0

        if len(klines) < 5:
            return DimensionScore('换手率低位', max_score, 0, None, '数据不足', False)

        today_vol = klines[-1].volume
        score = 0.0
        detail_parts = []

        # V2.1: 峰值回撤 (当日量 vs 近5日最大量)
        recent_5_vol_list = [b.volume for b in klines[-5:]]
        max_vol_5d = max(recent_5_vol_list) if recent_5_vol_list else 0
        vol_vs_peak = today_vol / max_vol_5d if max_vol_5d > 0 else 1.0

        if vol_vs_peak < 0.5:
            score += 3.0
            detail_parts.append(f'量能低于近5日峰值50%(比{vol_vs_peak:.2f})')
        elif vol_vs_peak < 0.7:
            score += 2.0
            detail_parts.append(f'量能低于近5日峰值70%(比{vol_vs_peak:.2f})')
        elif vol_vs_peak < 0.85:
            score += 1.0
            detail_parts.append(f'量能低于近5日峰值85%(比{vol_vs_peak:.2f})')

        # vs 5日均量
        if len(klines) >= 5:
            avg_5d_vol = sum(b.volume for b in klines[-5:]) / 5
            if avg_5d_vol > 0:
                vol_ratio_5d = today_vol / avg_5d_vol
                if vol_ratio_5d < 0.6:
                    score += 2.0
                    detail_parts.append(f'量能低于5日均量60%(比{vol_ratio_5d:.2f})')
                elif vol_ratio_5d < 0.8:
                    score += 1.0
                    detail_parts.append(f'量能低于5日均量80%(比{vol_ratio_5d:.2f})')

        # vs 近10日最低20%区间 (地量检测)
        if len(klines) >= 10:
            recent_10_vol = sorted([b.volume for b in klines[-10:]])
            percentile_20 = recent_10_vol[max(0, len(recent_10_vol) // 5 - 1)]
            if today_vol <= percentile_20:
                score += 2.0
                detail_parts.append('成交量处于近10日最低20%(地量)')

        # 换手率检测（如果有换手率数据）
        if klines[-1].turnover > 0 and len(klines) >= 10:
            avg_20d_hsl = sum(b.turnover for b in klines[-10:]) / min(10, len(klines))
            if avg_20d_hsl > 0:
                hsl_ratio = klines[-1].turnover / avg_20d_hsl
                if hsl_ratio < 0.5:
                    score += 1.0
                    detail_parts.append(f'换手率极低(比均值{hsl_ratio:.0%})')

        score = min(max_score, score)
        passed = score >= 2.0
        detail = '; '.join(detail_parts) if detail_parts else '换手率正常'

        return DimensionScore('换手率低位', max_score, score, {
            'today_vol': today_vol,
            'vol_ratio_5d': round(today_vol / avg_5d_vol, 3) if len(klines) >= 5 and avg_5d_vol > 0 else 0
        }, detail, passed)

    # ═══════════════════════════════════════════════════════════
    # V2.5 新增维度: D11/D12/D13 — 负向过滤维度
    # ═══════════════════════════════════════════════════════════

    def score_decline_deceleration(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D11 下跌减速 (15分) — V2.5新增
        5日跌幅/10日跌幅比值: <0.3→15, <0.5→12, <0.7→8, <1.0→4, >=1.0→0
        核心区分力: 反转股下跌减速(5日跌幅远小于10日), 续跌股下跌加速
        """
        max_score = 15.0
        if len(klines) < 10:
            return DimensionScore('下跌减速', max_score, 0, {}, '数据不足', False)

        closes = [k.close for k in klines]
        high_10d = max(closes[-10:]) if len(closes) >= 10 else max(closes)
        high_5d = max(closes[-5:]) if len(closes) >= 5 else max(closes)

        d10_decline = (high_10d - closes[-1]) / high_10d * 100 if high_10d > 0 else 0
        d5_decline = (high_5d - closes[-1]) / high_5d * 100 if high_5d > 0 else 0
        decel_ratio = d5_decline / d10_decline if d10_decline > 0 else 1.0

        score = 0.0
        detail = ''
        if decel_ratio < 0.3:
            score = 15.0
            detail = f'下跌显著减速(5日/10日={decel_ratio:.2f}<0.3)'
        elif decel_ratio < 0.5:
            score = 12.0
            detail = f'下跌减速(5日/10日={decel_ratio:.2f}<0.5)'
        elif decel_ratio < 0.7:
            score = 8.0
            detail = f'下跌略减速(5日/10日={decel_ratio:.2f}<0.7)'
        elif decel_ratio < 1.0:
            score = 4.0
            detail = f'下跌未减速(5日/10日={decel_ratio:.2f})'
        else:
            score = 0.0
            detail = f'下跌加速(5日/10日={decel_ratio:.2f}>=1.0)'

        return DimensionScore('下跌减速', max_score, score,
            {'decline_5d': round(d5_decline, 2), 'decline_10d': round(d10_decline, 2),
             'decel_ratio': round(decel_ratio, 3)},
            detail, score >= 8.0)

    def score_kline_pattern(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D12 K线形态 (15分) — V2.5新增
        锤子线/大阳线→15, 十字星→12, 小阳线→10, 小阴线→5, 大阴线→0
        核心区分力: 反转股信号日多为阳线/锤子线, 续跌股多为大阴线
        """
        max_score = 15.0
        if not klines:
            return DimensionScore('K线形态', max_score, 0, {}, '无数据', False)

        last = klines[-1]
        o, h, l, c = last.open, last.high, last.low, last.close
        body_pct = (c - o) / o * 100 if o > 0 else 0
        body = abs(c - o)
        total_range = h - l if h > l else 0.001
        lower_shadow = min(o, c) - l
        body_ratio = body / total_range if total_range > 0 else 0

        # 检查看涨吞没
        bullish_engulfing = False
        if len(klines) >= 2:
            prev = klines[-2]
            if prev.close < prev.open and c > o and c > prev.open and o < prev.close:
                bullish_engulfing = True

        score = 0.0
        detail = ''
        if body_ratio < 0.3 and lower_shadow > body * 2:
            score = 15.0
            detail = f'锤子线(实体比{body_ratio:.0%}, 下影线长)'
        elif body_pct > 5:
            score = 15.0
            detail = f'大阳线(实体{body_pct:.1f}%)'
        elif body_ratio < 0.15:
            score = 12.0
            detail = f'十字星(实体比{body_ratio:.0%})'
        elif body_pct > 0:
            score = 10.0
            detail = f'小阳线(实体{body_pct:.1f}%)'
        elif body_pct > -3:
            score = 5.0
            detail = f'小阴线(实体{body_pct:.1f}%)'
        else:
            score = 0.0
            detail = f'大阴线(实体{body_pct:.1f}%, 恐慌抛售)'

        if bullish_engulfing:
            score = min(score + 5, 15)
            detail += '; 看涨吞没'

        return DimensionScore('K线形态', max_score, score,
            {'body_pct': round(body_pct, 2), 'body_ratio': round(body_ratio, 3),
             'bullish_engulfing': bullish_engulfing},
            detail, score >= 8.0)

    def score_volume_price_divergence(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D13 量价背离 (10分) — V2.5新增
        价跌量缩→10, 价涨量增→10, 价跌量增→0(恐慌抛售)
        核心区分力: 反转股缩量回调, 续跌股放量抛售
        """
        max_score = 10.0
        if len(klines) < 6:
            return DimensionScore('量价背离', max_score, 0, {}, '数据不足', False)

        last = klines[-1]
        o, c, vol = last.open, last.close, last.volume
        body_pct = (c - o) / o * 100 if o > 0 else 0

        # 涨跌幅 (vs前一日收盘)
        prev_close = klines[-2].close if len(klines) >= 2 else o
        chg_pct = (c - prev_close) / prev_close * 100 if prev_close > 0 else 0

        # 量比
        vols_5d = [k.volume for k in klines[-6:-1]]
        avg_5d_vol = sum(vols_5d) / len(vols_5d) if vols_5d else 1
        vol_ratio = vol / avg_5d_vol if avg_5d_vol > 0 else 1.0

        score = 0.0
        detail = ''
        if chg_pct < 0:
            # 价跌
            if vol_ratio < 0.8:
                score = 10.0
                detail = f'价跌量缩(量比{vol_ratio:.2f}<0.8, 缩量回调)'
            elif vol_ratio < 1.0:
                score = 7.0
                detail = f'价跌量缩(量比{vol_ratio:.2f}<1.0)'
            elif vol_ratio < 1.5:
                score = 3.0
                detail = f'价跌量平(量比{vol_ratio:.2f})'
            else:
                score = 0.0
                detail = f'价跌量增(量比{vol_ratio:.2f}>=1.5, 放量下跌)'
        elif chg_pct > 0:
            # 价涨
            if vol_ratio > 1.0:
                score = 10.0
                detail = f'价涨量增(量比{vol_ratio:.2f}>1.0, 量价齐升)'
            elif vol_ratio > 0.7:
                score = 8.0
                detail = f'价涨量稳(量比{vol_ratio:.2f})'
            else:
                score = 5.0
                detail = f'价涨量缩(量比{vol_ratio:.2f}, 需观察)'
        else:
            # 价稳
            if vol_ratio < 0.8:
                score = 8.0
                detail = f'价稳量缩(量比{vol_ratio:.2f}, 蓄势)'
            else:
                score = 4.0
                detail = f'价稳量平(量比{vol_ratio:.2f})'

        return DimensionScore('量价背离', max_score, score,
            {'chg_pct': round(chg_pct, 2), 'vol_ratio': round(vol_ratio, 3),
             'body_pct': round(body_pct, 2)},
            detail, score >= 5.0)

    # ═══════════════════════════════════════════════════════════
    # V2.7 新增维度 (基于用户建议: 60日趋势/竞价/前日盘后/量价趋势)
    # ═══════════════════════════════════════════════════════════

    def score_ma_trend(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D14 中长期均线趋势 (10分) — V2.7新增
        研究: 暴跌反转在MA20附近成功率最高; 远离MA60>20%的反弹多为死猫跳
        评分逻辑:
          - 价格在MA20附近(±5%): 10分 (最佳反转位置)
          - 价格在MA20下方5-15%: 7分 (超跌反弹区)
          - 价格在MA20下方>15%但MA20走平: 5分 (可能筑底)
          - 价格远离MA60>25%且MA60仍下行: 0分 (趋势未变,死猫跳风险)
        """
        max_score = 10.0
        if len(klines) < 20:
            return DimensionScore('均线趋势', max_score, 0, {}, '数据不足(<20日)', False)

        closes = [k.close for k in klines]
        last_close = closes[-1]

        # MA20
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else sum(closes) / len(closes)
        # MA60 (if available)
        ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else sum(closes) / len(closes)

        # MA20方向 (近5日MA20变化)
        if len(closes) >= 25:
            ma20_5d_ago = sum(closes[-25:-5]) / 20
            ma20_direction = 'up' if ma20 > ma20_5d_ago else 'down'
            ma20_slope = (ma20 - ma20_5d_ago) / ma20_5d_ago * 100 if ma20_5d_ago > 0 else 0
        else:
            ma20_direction = 'flat'
            ma20_slope = 0

        # 价格vs MA20
        price_vs_ma20 = (last_close - ma20) / ma20 * 100 if ma20 > 0 else 0

        # 价格vs MA60
        price_vs_ma60 = (last_close - ma60) / ma60 * 100 if ma60 > 0 else 0

        # MA60方向
        if len(closes) >= 65:
            ma60_5d_ago = sum(closes[-65:-5]) / 60
            ma60_direction = 'up' if ma60 > ma60_5d_ago else 'down'
        else:
            ma60_direction = 'unknown'

        score = 0.0
        detail = ''

        if abs(price_vs_ma20) <= 5:
            # 价格在MA20附近 — 最佳反转位置
            score = 10.0
            detail = f'价格在MA20附近({price_vs_ma20:+.1f}%), MA20{ma20_direction}(斜率{ma20_slope:+.2f}%)'
        elif -15 <= price_vs_ma20 < -5:
            # 超跌反弹区
            if ma20_direction == 'down':
                score = 7.0
                detail = f'超跌反弹区(MA20下方{abs(price_vs_ma20):.1f}%), MA20下行'
            else:
                score = 8.0
                detail = f'超跌反弹区(MA20下方{abs(price_vs_ma20):.1f}%), MA20走平/上行'
        elif price_vs_ma20 < -15:
            # 深度超跌
            if ma20_direction == 'flat' or ma20_direction == 'up':
                score = 5.0
                detail = f'深度超跌但MA20走平(MA20下方{abs(price_vs_ma20):.1f}%), 可能筑底'
            else:
                score = 2.0
                detail = f'深度超跌且MA20下行(MA20下方{abs(price_vs_ma20):.1f}%), 趋势未变'
        else:
            # 价格在MA20上方
            score = 4.0
            detail = f'价格在MA20上方({price_vs_ma20:+.1f}%), 非超跌区'

        # MA60过滤: 远离MA60>25%且MA60下行 → 减分
        if price_vs_ma60 < -25 and ma60_direction == 'down':
            score = min(score, 3.0)
            detail += f'; MA60下行且远离{price_vs_ma60:.1f}%(死猫跳风险)'

        return DimensionScore('均线趋势', max_score, score,
            {'price_vs_ma20': round(price_vs_ma20, 2),
             'price_vs_ma60': round(price_vs_ma60, 2),
             'ma20_direction': ma20_direction,
             'ma60_direction': ma60_direction,
             'ma20_slope': round(ma20_slope, 3)},
            detail, score >= 5.0)

    def score_auction_gap(self, klines: List[KlineBar], quote_data: Dict = None) -> 'DimensionScore':
        """
        D15 集合竞价缺口 (5分) — V2.7新增, V3.1升级支持实时行情
        用户建议: 结合当日集合竞价情况
        信号日开盘价 vs 前日收盘价 → 缺口方向反映竞价买卖力量

        V3.1优化: 优先使用实时行情数据(quote_data), 更精确
          - quote_data['HQInfo']['Open']: 今日开盘价(实时)
          - quote_data['HQInfo']['Close']: 昨日收盘价(实时)
          - 若无可使用K线数据作为后备

          - 高开(0~3%): 5分 (买盘积极但不极端)
          - 平开(±0.5%): 3分 (中性)
          - 低开(-0.5~-2%): 2分 (卖盘略强)
          - 高开>3%: 4分 (追涨积极,但有追高风险)
          - 低开<-2%: 1分 (恐慌出逃,但可能是CRO信号)
        """
        max_score = 5.0
        if len(klines) < 2 and not quote_data:
            return DimensionScore('竞价缺口', max_score, 0, {}, '数据不足', False)

        # V3.1: 优先使用实时行情数据
        if quote_data and 'HQInfo' in quote_data:
            hq = quote_data['HQInfo']
            today_open = hq.get('Open', 0)
            yesterday_close = hq.get('Close', 0)  # 注意: TDX返回的Close是昨日收盘
            data_source = '实时行情'
        else:
            # 后备: 使用K线数据
            last = klines[-1]
            prev = klines[-2]
            today_open = last.open
            yesterday_close = prev.close
            data_source = 'K线数据'

        gap_pct = (today_open - yesterday_close) / yesterday_close * 100 if yesterday_close > 0 else 0

        score = 0.0
        detail = ''

        if 0 < gap_pct <= 3:
            score = 5.0
            detail = f'温和高开({gap_pct:+.2f}%), 竞价买盘积极 [{data_source}]'
        elif -0.5 <= gap_pct <= 0:
            score = 4.0
            detail = f'微幅低开/平开({gap_pct:+.2f}%), 竞价中性偏弱 [{data_source}]'
        elif gap_pct > 3:
            score = 4.0
            detail = f'大幅高开({gap_pct:+.2f}%), 追涨积极(注意追高风险) [{data_source}]'
        elif -2 <= gap_pct < -0.5:
            score = 2.0
            detail = f'低开({gap_pct:+.2f}%), 竞价卖盘略强 [{data_source}]'
        else:
            score = 1.0
            detail = f'大幅低开({gap_pct:+.2f}%), 恐慌出逃(可能是CRO信号) [{data_source}]'

        return DimensionScore('竞价缺口', max_score, score,
            {'gap_pct': round(gap_pct, 3), 'data_source': data_source,
             'today_open': today_open, 'yesterday_close': yesterday_close},
            detail, score >= 3.0)

    def score_prev_day_support(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D16 前日盘后承接 (5分) — V2.7新增
        用户建议: 结合昨日交易日的盘后资金、价格等情况
        前日K线形态反映盘后承接力度:
          - 长下影线(下影>实体2倍): 5分 (强承接)
          - 短下影线(下影>实体): 3分 (有承接)
          - 光脚阴线(无下影): 0分 (无承接)
          - 前日阳线: 4分 (前日已企稳)
        """
        max_score = 5.0
        if len(klines) < 2:
            return DimensionScore('前日承接', max_score, 0, {}, '数据不足', False)

        prev = klines[-2]
        o, h, l, c = prev.open, prev.high, prev.low, prev.close
        body = abs(c - o)
        total_range = h - l if h > l else 0.001
        lower_shadow = min(o, c) - l
        upper_shadow = h - max(o, c)
        body_ratio = body / total_range if total_range > 0 else 0

        score = 0.0
        detail = ''

        if c > o:
            # 前日阳线 — 已有企稳
            score = 4.0
            if lower_shadow > body * 1.5:
                score = 5.0
                detail = f'前日阳线带长下影(下影/实体={lower_shadow/max(body,0.001):.1f}x), 强承接'
            else:
                detail = '前日阳线, 已企稳'
        elif body_ratio < 0.15:
            # 十字星 — 多空平衡
            score = 3.0
            detail = f'前日十字星(body_ratio={body_ratio:.2f}), 多空平衡'
        elif lower_shadow > body * 2:
            # 长下影阴线 — 有承接
            score = 5.0
            detail = f'前日长下影(下影/实体={lower_shadow/max(body,0.001):.1f}x), 盘后强承接'
        elif lower_shadow > body:
            # 短下影阴线 — 有一定承接
            score = 3.0
            detail = f'前日短下影(下影/实体={lower_shadow/max(body,0.001):.1f}x), 有承接'
        else:
            # 光脚阴线 — 无承接
            score = 0.0
            detail = '前日光脚阴线(无下影), 无承接'

        return DimensionScore('前日承接', max_score, score,
            {'prev_body_ratio': round(body_ratio, 3),
             'prev_lower_shadow_ratio': round(lower_shadow / max(total_range, 0.001), 3),
             'prev_is_yang': c > o},
            detail, score >= 3.0)

    def score_volume_price_trend(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D17 量价趋势配合 (5分) — V2.7新增
        近5日量价趋势: 下跌缩量+上涨放量 = 最佳反转前兆
        研究(GitHub Upward-Reversal): 暴跌模式中量能收缩序列是关键预测特征
          - 近3日量能递减+价格企稳: 5分 (缩量见底序列)
          - 近5日下跌缩量: 4分 (量价配合)
          - 量价无明显趋势: 2分
          - 下跌放量: 0分 (量价背离,恐慌抛售)
        """
        max_score = 5.0
        if len(klines) < 6:
            return DimensionScore('量价趋势', max_score, 0, {}, '数据不足', False)

        recent_5 = klines[-5:]
        vols = [k.volume for k in recent_5]
        closes = [k.close for k in recent_5]

        # 量能趋势: 是否递减
        vol_decreasing = all(vols[i] <= vols[i-1] * 1.1 for i in range(1, len(vols)))
        vol_last_3_decreasing = all(vols[i] <= vols[i-1] * 1.05 for i in range(-3, 0))

        # 价格趋势: 近3日是否企稳(不再创新低)
        lows_3 = [k.low for k in recent_5[-3:]]
        price_stabilizing = lows_3[-1] >= min(lows_3[:-1]) if len(lows_3) >= 3 else False

        # 近5日涨跌
        chg_5d = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0

        # 量比递减序列: 最后3日量比都<1
        vol_ratios_3 = []
        for i in range(-3, 0):
            if len(klines) >= 6 + i:
                prev_vols = [klines[j].volume for j in range(max(0, len(klines)+i-6), len(klines)+i-1)]
                avg_prev = sum(prev_vols) / len(prev_vols) if prev_vols else 1
                vr = klines[i].volume / avg_prev if avg_prev > 0 else 1.0
                vol_ratios_3.append(vr)

        drying_sequence = all(vr < 0.9 for vr in vol_ratios_3) if vol_ratios_3 else False

        score = 0.0
        detail = ''

        if drying_sequence and price_stabilizing:
            score = 5.0
            detail = f'缩量见底序列(近3日量比递减+价格企稳), 量价配合'
        elif vol_decreasing and chg_5d < 0:
            score = 4.0
            detail = f'近5日下跌缩量(量价配合), 5日涨跌{chg_5d:+.1f}%'
        elif vol_last_3_decreasing:
            score = 3.0
            detail = f'近3日量能递减, 量能收缩中'
        elif chg_5d < 0 and vols[-1] > vols[0] * 1.2:
            score = 0.0
            detail = f'下跌放量(量价背离), 5日跌{chg_5d:.1f}%+放量{vols[-1]/max(vols[0],1):.2f}x'
        else:
            score = 2.0
            detail = f'量价无明显趋势(5日涨跌{chg_5d:+.1f}%)'

        return DimensionScore('量价趋势', max_score, score,
            {'vol_decreasing': vol_decreasing,
             'drying_sequence': drying_sequence,
             'price_stabilizing': price_stabilizing,
             'chg_5d': round(chg_5d, 2)},
            detail, score >= 3.0)



    # ─── V3.1 新增: 舆情维度 ───
    def score_sentiment_heat(self, klines: List[KlineBar], stock_code: str = '', sentiment_data: Dict = None) -> 'DimensionScore':
        """
        D18 舆情热度 (10分) — V3.1新增, V3.1优化支持实时舆情数据
        用户建议: 结合近60日舆情情况

        V3.1优化: 优先使用实时舆情数据(sentiment_data), 后备从缓存文件读取
          - 评分 >= 8: 10分 (高度关注, 可能过热)
          - 评分 >= 6: 7分 (市场关注度高)
          - 评分 >= 4: 4分 (中性关注)
          - 评分 < 4: 2分 (无人关注, 可能有预期差)
        """
        max_score = 10.0
        score = 5.0  # 默认中性
        detail = '舆情数据未采集(默认中性)'
        passed = False
        data_source = '默认'

        # V3.1: 优先使用实时舆情数据
        # V13.5.18: 集成tdx_ai_listening AI聚合数据(data_source='tdx_ai_listening')
        if sentiment_data and 'd18_score' in sentiment_data:
            score_raw = sentiment_data['d18_score']
            data_source = sentiment_data.get('data_source', '实时舆情')

            # V13.5.18: tdx_ai_listening增强 — 事件数量+多空分歧度+利空利好关键词
            if data_source == 'tdx_ai_listening':
                event_count = sentiment_data.get('d18_event_count', 0)
                bull_bear = sentiment_data.get('d18_bull_bear', {})
                bull_kw = sentiment_data.get('d18_bull_keywords', 0)
                bear_kw = sentiment_data.get('d18_bear_keywords', 0)
                summary = sentiment_data.get('d18_summary', '')

                # AI聚合评分: 事件热度+多空分歧+关键词情绪
                if score_raw >= 8:
                    score = 10.0
                    passed = True
                    detail = f'AI聚合: 舆情高度关注({event_count}事件, 多空{bull_bear.get("bull",0)}/{bull_bear.get("bear",0)}), 利好{bull_kw}利空{bear_kw} [{data_source}]'
                elif score_raw >= 6:
                    score = 7.0
                    passed = True
                    detail = f'AI聚合: 舆情关注度高({event_count}事件), 利好{bull_kw}利空{bear_kw} [{data_source}]'
                elif score_raw >= 4:
                    score = 4.0
                    detail = f'AI聚合: 舆情中性({event_count}事件), 多空均衡 [{data_source}]'
                else:
                    score = 2.0
                    detail = f'AI聚合: 舆情冷清({event_count}事件), 可能有预期差 [{data_source}]'

                # AI摘要截取
                if summary:
                    detail += f' | 摘要: {summary[:60]}...' if len(summary) > 60 else f' | 摘要: {summary}'
            else:
                # 原有逻辑: 实时舆情/缓存文件
                if score_raw >= 8:
                    score = 10.0
                    passed = True
                    detail = f'舆情高度关注(评分{score_raw:.1f}/10), 可能过热 [{data_source}]'
                elif score_raw >= 6:
                    score = 7.0
                    passed = True
                    detail = f'舆情关注度高(评分{score_raw:.1f}/10) [{data_source}]'
                elif score_raw >= 4:
                    score = 4.0
                    detail = f'舆情中性(评分{score_raw:.1f}/10) [{data_source}]'
                else:
                    score = 2.0
                    detail = f'舆情冷清(评分{score_raw:.1f}/10), 可能有预期差 [{data_source}]'
        else:
            # 后备: 从缓存文件读取
            import json, os
            cache_file = f'data/fullmarket_cache/sentiment_{stock_code}.json'
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        score_raw = data.get('d18_score', 5.0)
                        data_source = '缓存文件'
                        # 映射到0~10分
                        if score_raw >= 8:
                            score = 10.0
                            passed = True
                            detail = f'舆情高度关注(评分{score_raw:.1f}/10), 可能过热 [{data_source}]'
                        elif score_raw >= 6:
                            score = 7.0
                            passed = True
                            detail = f'舆情关注度高(评分{score_raw:.1f}/10) [{data_source}]'
                        elif score_raw >= 4:
                            score = 4.0
                            detail = f'舆情中性(评分{score_raw:.1f}/10) [{data_source}]'
                        else:
                            score = 2.0
                            detail = f'舆情冷清(评分{score_raw:.1f}/10), 可能有预期差 [{data_source}]'
                except:
                    pass

        return DimensionScore('舆情热度', max_score, score,
            {'d18_raw': score, 'data_source': data_source},
            detail, passed)

    def score_sentiment_trend(self, klines: List[KlineBar], stock_code: str = '', sentiment_data: Dict = None) -> 'DimensionScore':
        """
        D19 舆情趋势 (10分) — V3.1新增, V3.1优化支持实时舆情数据
        用户建议: 结合近60日舆情趋势

        V3.1优化: 优先使用实时舆情数据(sentiment_data), 后备从缓存文件读取
          - improving: 10分 (舆情升温, 强烈看多)
          - stable: 5分 (舆情平稳)
          - deteriorating: 0分 (舆情降温, 回避)
        """
        max_score = 10.0
        score = 5.0  # 默认stable
        detail = '舆情趋势平稳(默认)'
        passed = False
        data_source = '默认'
        trend = 'stable'

        # V3.1: 优先使用实时舆情数据
        # V13.5.18: 集成tdx_ai_listening AI聚合趋势数据
        if sentiment_data and 'd19_trend' in sentiment_data:
            trend = sentiment_data['d19_trend']
            data_source = sentiment_data.get('data_source', '实时舆情')

            # V13.5.18: tdx_ai_listening增强 — 多空权重+关键词情绪驱动趋势判断
            if data_source == 'tdx_ai_listening':
                bull_bear = sentiment_data.get('d18_bull_bear', {})
                bull_kw = sentiment_data.get('d18_bull_keywords', 0)
                bear_kw = sentiment_data.get('d18_bear_keywords', 0)
                event_count = sentiment_data.get('d18_event_count', 0)

                if trend == 'improving':
                    score = 10.0
                    passed = True
                    detail = f'AI聚合趋势升温: 多方{bull_bear.get("bull",0)}/空方{bull_bear.get("bear",0)}, 利好{bull_kw}>利空{bear_kw} [{data_source}]'
                elif trend == 'stable':
                    score = 5.0
                    detail = f'AI聚合趋势平稳: 多空均衡, {event_count}事件 [{data_source}]'
                else:
                    score = 0.0
                    detail = f'AI聚合趋势降温: 空方占优, 利空{bear_kw}>利好{bull_kw} [{data_source}]'
            else:
                if trend == 'improving':
                    score = 10.0
                    passed = True
                    detail = f'舆情趋势升温(improving), 强烈看多 [{data_source}]'
                elif trend == 'stable':
                    score = 5.0
                    detail = '舆情趋势平稳(stable)'
                else:
                    score = 0.0
                    detail = f'舆情趋势降温({trend}), 回避 [{data_source}]'
        else:
            # 后备: 从缓存文件读取
            import json, os
            cache_file = f'data/fullmarket_cache/sentiment_{stock_code}.json'
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        trend = data.get('d19_trend', 'stable')
                        data_source = '缓存文件'
                        if trend == 'improving':
                            score = 10.0
                            passed = True
                            detail = f'舆情趋势升温(improving), 强烈看多 [{data_source}]'
                        elif trend == 'stable':
                            score = 5.0
                            detail = '舆情趋势平稳(stable)'
                        else:
                            score = 0.0
                            detail = f'舆情趋势降温({trend}), 回避 [{data_source}]'
                except:
                    pass

        return DimensionScore('舆情趋势', max_score, score,
            {'d19_trend': trend, 'data_source': data_source},
            detail, passed)

    # ─── V3.2 新增: 防追高维度 ───
    def score_distance_from_5d_high(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D20 距5日高点距离 (10分) — V3.2新增
        防止追高买入模式(如机器人涨停次日19.356高位买入教训)

        计算当前收盘价与近5日最高价的距离:
          - 距5日高点>15%: 10分 (深度回调, 极佳反转买点)
          - 距5日高点10~15%: 8分 (大幅回调, 好的反转买点)
          - 距5日高点5~10%: 6分 (适度回调, 可接受)
          - 距5日高点2~5%: 4分 (小幅回调, 中性)
          - 距5日高点<2%: 1分 (追高区域, 强烈警告!)
          - 创新高(收盘>5日高点): 0分 (同步追高, 禁止!)

        核心逻辑: 离5日高点越远 = 越安全的反转买点
        """
        max_score = 10.0
        if len(klines) < 5:
            return DimensionScore('距5日高点', max_score, 5.0, {},
                '数据不足(默认中性)', False)

        recent_5 = klines[-5:]
        high_5d = max(b.high for b in recent_5)
        today_close = klines[-1].close

        if high_5d <= 0:
            return DimensionScore('距5日高点', max_score, 5.0, {},
                '数据异常', False)

        dist_pct = (today_close - high_5d) / high_5d * 100  # 负值=低于高点

        if dist_pct > 0:
            # 创新高 — 同步追高, 严格禁止
            score = 0.0
            passed = False
            detail = f'创新高({dist_pct:+.2f}%), 禁止追高!'
        elif dist_pct > -2:
            # 距高点<2% — 追高区域
            score = 1.0
            passed = False
            detail = f'距5日高点仅{abs(dist_pct):.2f}%, 追高区域(强烈警告!)'
        elif dist_pct > -5:
            # 距高点2~5% — 小幅回调
            score = 4.0
            passed = False
            detail = f'距5日高点{abs(dist_pct):.2f}%, 小幅回调(中性)'
        elif dist_pct > -10:
            # 距高点5~10% — 适度回调
            score = 6.0
            passed = True
            detail = f'距5日高点{abs(dist_pct):.2f}%, 适度回调(可接受)'
        elif dist_pct > -15:
            # 距高点10~15% — 大幅回调
            score = 8.0
            passed = True
            detail = f'距5日高点{abs(dist_pct):.2f}%, 大幅回调(好的反转买点)'
        else:
            # 距高点>15% — 深度回调
            score = 10.0
            passed = True
            detail = f'距5日高点{abs(dist_pct):.2f}%, 深度回调(极佳反转买点)'

        return DimensionScore('距5日高点', max_score, score,
            {'dist_from_5d_high_pct': round(dist_pct, 2)},
            detail, passed)

    # ─── D21: 四阶段分析 (10分) — V13.5.11新增 ───
    def score_four_stages(self, code: str, name: str, 
                          klines: List[KlineBar],
                          capital_flow: CapitalFlow = None) -> 'DimensionScore':
        """
        D21 四阶段分析维度 (10分) — V13.5.11新增
        
        基于科技股四阶段演变逻辑评估个股所属阶段：
        - 第一阶段(买铲子): 算力芯片稀缺期 → 当前已高位, 回避
        - 第二阶段(瓶颈争夺): 硬件细分(HBM/封装/设备) → 关注被错杀
        - 第三阶段(效率定价): 算力出租/云服务 → 估值压力
        - 第四阶段(价值回归): 有真实客户和盈利 → 重点关注
        
        评分逻辑:
        - 第四阶段: 10分 (重点关注)
        - 第二阶段超跌: 7-8分 (被错杀标的)
        - 第三阶段: 4-5分 (中性, 需更多条件)
        - 第一阶段: 1分 (高位回避)
        """
        max_score = 10.0
        
        # 四阶段定义
        STAGE_DEFINITIONS = {
            'stage1': {
                'name': '第一阶段: 买铲子(算力芯片)',
                'description': '算力芯片稀缺时代, NVIDIA/AMD/国产AI芯片供不应求',
                'representative_stocks': [
                    '603986',  # 兆易创新
                    '688256',  # 寒武纪
                    '688041',  # 海光信息
                    '688499',  # 芯原股份
                    '688521',  # 芯海科技
                ],
                'characteristics': ['AI芯片', 'GPU', '算力芯片', 'AI加速'],
                'current_status': '已充分演绎, 估值高位',
                'recommendation': '回避'
            },
            'stage2': {
                'name': '第二阶段: 瓶颈争夺(硬件细分)',
                'description': '算力扩张遇瓶颈, HBM/先进封装/半导体设备成为关键',
                'representative_stocks': [
                    '002371',  # 北方华创
                    '002156',  # 通富微电
                    '688072',  # 拓荆科技
                    '688396',  # 华润微
                    '688008',  # 澜起科技
                    '688981',  # 中芯国际
                    '002371',  # 北方华创
                    '688556',  # 高测股份
                ],
                'characteristics': ['半导体设备', 'HBM', '先进封装', '薄膜沉积',
                                   '光刻胶', '硅片', '晶圆代工'],
                'current_status': '今日大幅回调, 关注被错杀',
                'recommendation': '关注被错杀'
            },
            'stage3': {
                'name': '第三阶段: 效率定价(算力服务)',
                'description': '从买硬件到买算力服务, 效率定价成为核心',
                'representative_stocks': [
                    '601360',  # 三六零
                    '300774',  # 电广传媒
                ],
                'characteristics': ['算力出租', '云计算', 'AI服务', '数据中心'],
                'current_status': '正在演绎, 估值压力显现',
                'recommendation': '中性观察'
            },
            'stage4': {
                'name': '第四阶段: 价值回归(真实盈利)',
                'description': '真正有客户、能盈利的公司, 科技投资回归价值本质',
                'representative_stocks': [
                    '002236',  # 大华股份
                    '002415',  # 海康威视
                    '603160',  # 汇顶科技
                    '002049',  # 紫光国微
                ],
                'characteristics': ['AI应用', '智能驾驶', '工业软件', 'AI视频监控',
                                   '企业服务', 'SaaS'],
                'current_status': '开始受到关注, 资金寻找确定性',
                'recommendation': '重点关注'
            }
        }
        
        # 检测所属阶段
        detected_stage = None
        stage_score = 5.0  # 默认中性
        stage_detail_parts = []
        raw_data = {'detected_stage': None, 'reason': '', 'is_mis beaten': False}
        
        # 方法1: 精确匹配代码
        for stage_id, stage_info in STAGE_DEFINITIONS.items():
            if code in stage_info['representative_stocks']:
                detected_stage = stage_id
                break
        
        # 方法2: 基于名称/行业关键词匹配
        if not detected_stage:
            name_keywords = name.upper()
            for stage_id, stage_info in STAGE_DEFINITIONS.items():
                for kw in stage_info['characteristics']:
                    if kw in name_keywords or kw in name:
                        detected_stage = stage_id
                        break
                if detected_stage:
                    break
        
        # 方法3: 基于资金流和行为模式推断
        if not detected_stage and capital_flow:
            # 如果主力连续流入但今日大跌 → 可能是第二阶段被错杀
            if capital_flow.main_5d > 0 and klines and len(klines) >= 5:
                recent_5_returns = []
                for i in range(-5, 0):
                    if klines[i].open > 0:
                        ret = (klines[i].close - klines[i].open) / klines[i].open
                        recent_5_returns.append(ret)
                
                if len(recent_5_returns) >= 3:
                    recent_5_returns.sort()
                    worst_3_avg = sum(recent_5_returns[:3]) / 3
                    if worst_3_avg < -0.05:  # 近5日中有3日跌幅超5%
                        detected_stage = 'stage2'
                        raw_data['is_mis_beaten'] = True
        
        # 计算阶段评分
        if detected_stage == 'stage4':
            # 第四阶段: 价值回归, 重点关注
            stage_score = 10.0
            stage_detail_parts.append('第四阶段(价值回归): 有真实客户和盈利')
            stage_detail_parts.append('资金寻找确定性, 相对抗跌')
            raw_data['detected_stage'] = 'stage4'
        elif detected_stage == 'stage2':
            # 第二阶段: 检查是否被错杀
            if capital_flow and klines and len(klines) >= 5:
                recent_5 = klines[-5:]
                # 使用chg_pct字段计算近5日平均跌幅
                avg_return = sum(b.chg_pct for b in recent_5) / len(recent_5)
                
                # 主力流入+今日大跌 → 被错杀
                if capital_flow.main_5d > 1e8 and recent_5[-1].chg_pct < -3:
                    stage_score = 8.0
                    stage_detail_parts.append('第二阶段(瓶颈争夺): 被错杀标的')
                    stage_detail_parts.append(f'主力5日净流入{capital_flow.main_5d/1e8:.1f}亿, 今日跌幅{abs(recent_5[-1].chg_pct):.1f}%')
                    raw_data['is_mis_beaten'] = True
                elif avg_return < -5:
                    # 近5日大幅回调
                    stage_score = 7.0
                    stage_detail_parts.append('第二阶段(瓶颈争夺): 大幅回调关注')
                    stage_detail_parts.append(f'近5日均跌{abs(avg_return):.1f}%, 安全边际较高')
                else:
                    stage_score = 6.0
                    stage_detail_parts.append('第二阶段(瓶颈争夺): 硬件细分龙头')
            else:
                stage_score = 6.0
                stage_detail_parts.append('第二阶段(瓶颈争夺): 硬件细分龙头')
            raw_data['detected_stage'] = 'stage2'
        elif detected_stage == 'stage3':
            # 第三阶段: 中性
            stage_score = 4.0
            stage_detail_parts.append('第三阶段(效率定价): 算力服务')
            stage_detail_parts.append('需更多条件确认')
            raw_data['detected_stage'] = 'stage3'
        elif detected_stage == 'stage1':
            # 第一阶段: 高位回避
            if klines and len(klines) >= 60:
                # 检查60日涨幅
                p60 = klines[-60].close if klines[-60].open > 0 else klines[-60].close
                current = klines[-1].close
                gain_60d = (current - p60) / p60 * 100
                
                if gain_60d > 100:
                    stage_score = 1.0
                    stage_detail_parts.append(f'第一阶段(买铲子): 高位! 60日涨{gain_60d:.0f}%')
                    stage_detail_parts.append('强烈建议回避')
                else:
                    stage_score = 3.0
                    stage_detail_parts.append('第一阶段(买铲子): 算力芯片')
            else:
                stage_score = 2.0
                stage_detail_parts.append('第一阶段(买铲子): 算力芯片')
                stage_detail_parts.append('注意高位风险')
            raw_data['detected_stage'] = 'stage1'
        else:
            # 未知阶段
            stage_score = 5.0
            stage_detail_parts.append('阶段未知(默认中性)')
            stage_detail_parts.append('建议补充行业信息')
            raw_data['detected_stage'] = 'unknown'
        
        # 判断是否通过
        passed = stage_score >= 7.0  # 7分以上才通过
        
        # 特殊警告
        if detected_stage == 'stage1' and stage_score <= 2.0:
            stage_detail_parts.append('⚠️ 高位追高风险极大!')
        elif raw_data.get('is_mis_beaten'):
            stage_detail_parts.append('⭐ 被错杀: 可能是极佳买点')
        
        detail = '; '.join(stage_detail_parts)
        
        return DimensionScore('四阶段分析', max_score, stage_score, raw_data, detail, passed)

    # ─── D25: 放量启动 (10分) — V13.5.14三路评分升级 ───
    def score_volume_surge_initiation(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D25 放量启动维度 (10分) — V13.5.14三路评分升级
        
        三路评分逻辑 (覆盖率0%→75%→88%):
        
        路1: 冲高回落巨量 (新增, V13.5.14)
          量比≥3.0 + 日内冲高≥5% + 收盘回落<3% → 6分
          埃斯顿7/2验证: 量比3.24+冲高4.3%+回落-0.86% → 7/3+10%涨停
        
        路2: 放量上涨 (阈值降低, 量比1.5→1.3)
          量比≥1.3 + 涨幅>0% → 2-5分 (覆盖率提升)
        
        路3: 温和放量 (新增, V13.5.14)
          量比1.1-1.3 + 涨幅>0% → 1分 (捕获温和启动)
        
        通用加分: 缩量后放量(前2日缩量+今日放量) → +2分
        
        蜀道装备失误教训: 追涨停板≠放量启动! 缩量(量比0.61)冲高回落是陷阱!
        """
        max_score = 10.0
        
        if len(klines) < 5:
            return DimensionScore('放量启动', max_score, 0, None, '数据不足', False)
        
        today = klines[-1]
        recent_3 = klines[-4:-1] if len(klines) >= 4 else klines[:-1]
        
        # 当日量比和涨幅
        vr = today.volume_ratio
        chg = today.chg_pct
        
        score = 0.0
        detail_parts = []
        scoring_path = '无'
        
        # ── 路1: 冲高回落巨量 (V13.5.14新增) ──
        # 条件: 量比≥3.0 + 日内冲高≥5%(从昨收到今日最高) + 收盘回落<3%(今日收盘vs今日最高)
        # 但收盘必须低于最高(有回落), 且收盘跌幅<3%(不是大跌)
        prev_close = klines[-2].close if len(klines) >= 2 else None
        if prev_close and vr >= 3.0:
            intraday_surge = (today.high - prev_close) / prev_close * 100  # 从昨收到今日最高
            close_from_high = (today.close - today.high) / today.high * 100  # 今日收盘vs今日最高(负值=回落)
            close_from_prev = (today.close - prev_close) / prev_close * 100  # 今日收盘vs昨收
            
            if intraday_surge >= 5.0 and close_from_prev < 3.0 and close_from_high < -0.5:
                # 冲高回落巨量: 6分 (日内冲高>5%但收盘回落, 次日可能继续冲)
                score = 6.0
                detail_parts.append(f'冲高回落巨量(量比{vr:.1f}+冲高{intraday_surge:.1f}%+回落{close_from_prev:.1f}%)')
                scoring_path = '路1冲高回落'
            elif intraday_surge >= 3.0 and close_from_prev < 3.0 and close_from_high < -0.3:
                # 较弱版冲高回落: 4分
                score = 4.0
                detail_parts.append(f'冲高回落量(量比{vr:.1f}+冲高{intraday_surge:.1f}%)')
                scoring_path = '路1冲高回落(弱)'
        
        # ── 路2: 放量上涨 (阈值降低: 量比≥1.3) ──
        if score == 0 and vr >= 1.3:
            scoring_path = '路2放量上涨'
            # 量比评分
            if vr >= 3.0:
                score += 2.5
                detail_parts.append(f'极致放量(量比{vr:.1f})')
            elif vr >= 2.0:
                score += 2.0
                detail_parts.append(f'强放量(量比{vr:.1f})')
            elif vr >= 1.5:
                score += 1.5
                detail_parts.append(f'放量(量比{vr:.1f})')
            elif vr >= 1.3:
                score += 1.0
                detail_parts.append(f'适度放量(量比{vr:.1f})')
            
            # 涨幅评分
            if chg >= 5.0:
                score += 3.0
                detail_parts.append(f'大幅上涨({chg:.1f}%)')
            elif chg >= 3.0:
                score += 2.0
                detail_parts.append(f'中幅上涨({chg:.1f}%)')
            elif chg >= 1.0:
                score += 1.0
                detail_parts.append(f'小幅上涨({chg:.1f}%)')
            elif chg >= 0:
                score += 0.5
                detail_parts.append(f'微涨({chg:.1f}%)')
        
        # ── 路3: 温和放量 (新增, V13.5.14) ──
        if score == 0 and 1.1 <= vr < 1.3 and chg > 0:
            score += 1.0
            detail_parts.append(f'温和放量(量比{vr:.1f}+涨{chg:.1f}%)')
            scoring_path = '路3温和放量'
            # 温和放量+涨幅好可额外加0.5分
            if chg >= 3.0:
                score += 0.5
                detail_parts.append(f'涨幅较强({chg:.1f}%)')
        
        # ── 通用加分: 缩量后放量 ──
        # 前2日缩量(量比<0.8) + 今日放量(量比≥1.3) = 最佳形态
        if len(recent_3) >= 2 and vr >= 1.3:
            prev_drying = sum(1 for b in recent_3[-2:] if b.volume_ratio < 0.8)
            if prev_drying >= 2:
                score += 2.0
                detail_parts.append(f'缩量后放量(前{prev_drying}日缩量)')
            elif prev_drying >= 1 and vr >= 2.0:
                score += 1.0
                detail_parts.append(f'缩量后放量(前1日缩量)')
        
        # ── 追涨停板否决 (蜀道装备教训) ──
        # 量比<0.8 + 涨幅>5% = 缩量涨停(追高陷阱)
        if vr < 0.8 and chg >= 5.0:
            score *= 0.3  # 严重打折! 蜀道装备7/2缩量涨停→7/3亏损
            detail_parts.append(f'⚠缩量涨停陷阱(量比{vr:.1f})')
        
        score = min(max_score, score)
        passed = score >= 5.0  # 5分以上=放量启动信号确认
        
        detail = '; '.join(detail_parts) if detail_parts else '无放量启动信号'
        raw = {
            'volume_ratio': round(vr, 2), 
            'chg_pct': round(chg, 2),
            'scoring_path': scoring_path
        }
        
        return DimensionScore('放量启动', max_score, score, raw, detail, passed)
    
    # ─── D26: 趋势延续 (8分) — V13.5.13新增 ───
    def score_trend_continuation(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D26 趋势延续维度 (8分) — V13.5.13新增
        
        检测"趋势延续"信号: 近20日涨>30% + 近5日回调<5%
        翻倍股的回调不是风险, 是买入机会!
        
        7/3验证: 中国巨石6月翻倍(37→77)+7/2回调-3.47% → 7/3+10%涨停
        
        评分逻辑:
        - 近20日涨>60% + 回调<3%: 8分 (强势延续)
        - 近20日涨>30% + 回调<5%: 6分 (趋势延续)
        - 近20日涨>20% + 回调<8%: 4分 (中等延续)
        - 近20日涨>15% + 回调<10%: 2分 (温和延续)
        - 近10日涨>15% + 今日微跌: 3分 (短期趋势+回调买点)
        """
        max_score = 8.0
        
        if len(klines) < 20:
            return DimensionScore('趋势延续', max_score, 0, None, '数据不足', False)
        
        # 近20日涨幅
        p20 = klines[-20].close
        p_now = klines[-1].close
        gain_20d = (p_now - p20) / p20 * 100
        
        # 近5日回调幅度 (从近5日最高点到今日的回撤)
        recent_5 = klines[-5:]
        max_close_5d = max(b.close for b in recent_5)
        pullback_5d = (p_now - max_close_5d) / max_close_5d * 100  # 负值表示回调
        
        # 近10日涨幅
        if len(klines) >= 10:
            p10 = klines[-10].close
            gain_10d = (p_now - p10) / p10 * 100
        else:
            gain_10d = 0
        
        score = 0.0
        detail_parts = []
        
        # 近20日涨幅评分
        if gain_20d >= 60:
            score += 4.0
            detail_parts.append(f'近20日暴涨{gain_20d:.0f}%')
        elif gain_20d >= 30:
            score += 3.0
            detail_parts.append(f'近20日大涨{gain_20d:.0f}%')
        elif gain_20d >= 20:
            score += 2.0
            detail_parts.append(f'近20日上涨{gain_20d:.0f}%')
        elif gain_20d >= 15:
            score += 1.0
            detail_parts.append(f'近20日温和上涨{gain_20d:.0f}%')
        
        # 回调评分 (小回调=好买点)
        if pullback_5d >= -3:
            score += 3.0
            detail_parts.append(f'回调极小{pullback_5d:.1f}%(最佳买点)')
        elif pullback_5d >= -5:
            score += 2.0
            detail_parts.append(f'回调温和{pullback_5d:.1f}%(好买点)')
        elif pullback_5d >= -8:
            score += 1.0
            detail_parts.append(f'回调适中{pullback_5d:.1f}%')
        
        # 短期趋势加分 (近10日涨>15%)
        if gain_10d >= 15:
            score += 1.0
            detail_parts.append(f'近10日涨{gain_10d:.0f}%')
        
        # 今日微跌加分 (回调买点)
        today_chg = klines[-1].chg_pct
        if today_chg >= -2 and gain_20d >= 20:
            score += 0.5
            detail_parts.append(f'今日微跌{today_chg:.1f}%(回调买点)')
        
        score = min(max_score, score)
        passed = score >= 4.0
        
        detail = '; '.join(detail_parts) if detail_parts else '无趋势延续信号'
        raw = {
            'gain_20d': round(gain_20d, 1),
            'pullback_5d': round(pullback_5d, 1),
            'gain_10d': round(gain_10d, 1) if len(klines) >= 10 else 0
        }
        
        return DimensionScore('趋势延续', max_score, score, raw, detail, passed)
    
    # ─── D27: 低位蓄势 (7分) — V13.5.13新增 ───
    def score_low_accumulation(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D27 低位蓄势维度 (7分) — V13.5.13新增
        
        检测"低位蓄势"信号: 低位+缩量3日+微跌
        缩量微跌=蓄势待发, 次日可能放量涨停!
        
        7/3验证: 蓝黛科技6/26触底→缩量3日→7/2微跌-0.55% → 7/3+10%涨停
        
        评分逻辑:
        - 低位(近60日最低价区间) + 缩量3日 + 微跌<1%: 7分 (完美蓄势)
        - 低位 + 缩量2日 + 微跌<2%: 5分 (蓄势确认)
        - 低位 + 缩量1日 + 平盘: 3分 (蓄势初步)
        - 近20日低位 + 量比递减趋势: 4分 (缩量趋势)
        """
        max_score = 7.0
        
        if len(klines) < 10:
            return DimensionScore('低位蓄势', max_score, 0, None, '数据不足', False)
        
        recent_3 = klines[-3:]
        recent_5 = klines[-5:]
        
        # 1. 低位检测: 近60日最低价区间(距最低价<15%)
        if len(klines) >= 60:
            min_60d = min(b.close for b in klines[-60:])
            today_close = klines[-1].close
            dist_from_low = (today_close - min_60d) / min_60d * 100
            is_low = dist_from_low < 15  # 距60日最低价<15%
        elif len(klines) >= 20:
            min_20d = min(b.close for b in klines[-20:])
            today_close = klines[-1].close
            dist_from_low = (today_close - min_20d) / min_20d * 100
            is_low = dist_from_low < 10  # 距20日最低价<10%
        else:
            is_low = False
            dist_from_low = 100
        
        # 2. 缩量检测: 近3日量比<0.8的天数
        drying_days = sum(1 for b in recent_3 if b.volume_ratio < 0.8)
        
        # 3. 缩量趋势: 近3日量比递减
        vr_trend_declining = (
            recent_3[0].volume_ratio > recent_3[1].volume_ratio > recent_3[2].volume_ratio
            if len(recent_3) == 3 else False
        )
        
        # 4. 微跌/平盘检测
        today_chg = klines[-1].chg_pct
        is_small_decline = -2 <= today_chg <= 1  # 微跌到微涨
        is_flat = -1 <= today_chg <= 0.5  # 平盘
        
        score = 0.0
        detail_parts = []
        
        # 低位评分
        if is_low:
            score += 2.0
            detail_parts.append(f'低位(距最低{dist_from_low:.1f}%)')
        else:
            detail_parts.append(f'非低位(距最低{dist_from_low:.1f}%)')
        
        # 缩量评分
        if drying_days >= 3 and vr_trend_declining:
            score += 3.0
            detail_parts.append(f'极致缩量(3日缩量+量比递减)')
        elif drying_days >= 3:
            score += 2.5
            detail_parts.append(f'连续3日缩量')
        elif drying_days >= 2:
            score += 2.0
            detail_parts.append(f'连续2日缩量')
        elif drying_days >= 1:
            score += 1.0
            detail_parts.append(f'1日缩量')
        
        # 微跌/平盘评分
        if is_flat and drying_days >= 2:
            score += 2.0
            detail_parts.append(f'平盘缩量(完美蓄势)')
        elif is_small_decline and drying_days >= 1:
            score += 1.0
            detail_parts.append(f'微跌{today_chg:.1f}%缩量')
        elif is_flat:
            score += 0.5
            detail_parts.append(f'平盘')
        
        score = min(max_score, score)
        passed = score >= 4.0
        
        detail = '; '.join(detail_parts) if detail_parts else '无蓄势信号'
        raw = {
            'is_low': is_low,
            'dist_from_low': round(dist_from_low, 1),
            'drying_days': drying_days,
            'vr_trend_declining': vr_trend_declining,
            'today_chg': round(today_chg, 2)
        }
        
        return DimensionScore('低位蓄势', max_score, score, raw, detail, passed)
    
    # ─── D28: 催化强度 (8分) — V13.5.14新增 ───
    def score_catalyst_strength(self, code: str, name: str, klines: List[KlineBar],
                                 catalyst_data: Optional[Dict] = None) -> 'DimensionScore':
        """
        D28 催化强度维度 (8分) — V13.5.14新增
        
        量化催化剂强度: 宇树机器人/黄金/PCB涨价等主题催化的持续性评分
        
        7/3验证: 宇树机器人催化(27只涨停) + 黄金催化(8只涨停) + PCB涨价(5只涨停)
        
        催化剂分类与评分:
        - 产业催化(8分): 宇树机器人量产/半导体国产替代 → 产业级变革
        - 政策催化(6分): 证监会主动ETF/央行入市 → 政策驱动
        - 事件催化(5分): 自然灾害/地缘事件 → 短期事件驱动
        - 涨价催化(4分): PCB/黄金涨价 → 价格传导
        - 催化衰减检测: 连续2日涨停数递减 → 催化减弱
        
        评分逻辑:
        1. 催化类型基础分(2-8分): 产业>政策>事件>涨价
        2. 催化持续性加分(0-3分): 连续3日催化涨停≥10只→+3分
        3. 标的催化匹配加分(0-2分): 股票业务与催化主题直接相关→+2分
        4. 催化衰减扣分(-2分): 涨停数连续递减→扣分警告
        """
        max_score = 8.0
        
        if not catalyst_data:
            # 无催化数据时, 基于行业和近期涨幅做推断
            return DimensionScore('催化强度', max_score, 0, None, '无催化数据', False)

        score = 0.0
        detail_parts = []

        # V13.5.18: wenda_notice_query增强 — 公告事件驱动催化检测
        catalyst_source = catalyst_data.get('source', 'manual')
        if catalyst_source == 'wenda_notice_query':
            # 从公告数据自动检测催化类型
            keywords_matched = catalyst_data.get('keywords_matched', [])
            notice_count = catalyst_data.get('notice_count', 0)
            if keywords_matched:
                detail_parts.append(f'公告催化检测: {", ".join(keywords_matched[:3])} ({notice_count}条公告)')

        # 1. 催化类型基础分
        catalyst_type = catalyst_data.get('type', 'unknown')
        catalyst_name = catalyst_data.get('name', '')
        
        type_scores = {
            'industry': 8.0,   # 产业级变革 (宇树机器人量产等)
            'policy': 6.0,     # 政策驱动
            'event': 5.0,      # 事件驱动 (地缘/灾害)
            'price': 4.0,      # 涨价传导 (PCB/黄金)
            'concept': 3.0,    # 纯概念炒作
        }
        
        base_score = type_scores.get(catalyst_type, 0)
        if base_score > 0:
            score += min(4.0, base_score / 2)  # 基础分最多贡献4分
            detail_parts.append(f'{catalyst_name}[{catalyst_type}]')
        
        # 2. 催化持续性加分
        continuity = catalyst_data.get('continuity_days', 0)
        daily_count = catalyst_data.get('daily_limitup_count', 0)
        
        if continuity >= 3 and daily_count >= 10:
            score += 3.0
            detail_parts.append(f'持续{continuity}日催化(日均{daily_count}只涨停)')
        elif continuity >= 2 and daily_count >= 5:
            score += 2.0
            detail_parts.append(f'持续{continuity}日催化')
        elif continuity >= 1:
            score += 1.0
            detail_parts.append(f'新催化(1日)')
        
        # 3. 标的催化匹配加分
        relevance = catalyst_data.get('relevance', 'none')
        if relevance == 'direct':
            score += 2.0
            detail_parts.append(f'标的直接匹配催化')
        elif relevance == 'indirect':
            score += 1.0
            detail_parts.append(f'标的间接关联催化')
        
        # 4. 催化衰减扣分
        declining = catalyst_data.get('declining', False)
        if declining:
            score -= 2.0
            detail_parts.append(f'⚠催化衰减(涨停数递减)')
        
        score = min(max_score, max(0, score))
        passed = score >= 4.0
        
        detail = '; '.join(detail_parts) if detail_parts else '无催化信号'
        raw = {
            'catalyst_type': catalyst_type,
            'catalyst_name': catalyst_name,
            'continuity_days': continuity,
            'relevance': relevance,
            'declining': declining
        }
        
        return DimensionScore('催化强度', max_score, score, raw, detail, passed)
    
    # ─── D29: 双洗盘识别 (10分) — V13.5.15新增 ───
    def score_double_washout(self, klines: List[KlineBar],
                             capital_flow_history: List[Dict] = None) -> 'DimensionScore':
        """
        D29 双洗盘识别维度 (12分) — V13.5.18升级
        
        核心使命: 区分"洗盘"(持仓) vs "死猫跳"(清仓)
        
        蜀道装备(300540)惨痛教训: 
        6/11涨停+8.5%→6/12暴跌-12.5%→6/15底部23.77→6/23大涨+10.6%→
        6/24暴跌-8.97%(系统说清仓!)→6/25缩量止跌→6/26涨停+20%!→7/1冲45.48
        踏空涨幅: 25.42→45.48 = +78.9%! 假设5万仓位 = 损失¥39,457!
        
        V13.5.18升级(10→12分):
        新增6. 洗盘日主力微正(2分): 暴跌日(跌≥5%)主力净额为正 = 洗盘铁证!
          蜀道6/24验证: 跌-8.97%但主力净额+169万(+0.28%) → 主力没走=洗盘!
          这是蜀道装备模式逆向研究的核心发现: 暴跌+主力微正=洗盘而非出货
        
        五+一维度评分:
        1. 底部抬高(3分): N日前绝对低点 vs 当下低点, 抬高=洗盘, 跌破=死猫跳
        2. 双次大涨(2分): 20日内≥2次单日涨≥8%, 主力反复试盘=洗盘前兆
        3. 大跌日巨量(2分): 大涨后暴跌日量>5日均量1.5倍, 主力对倒砸盘+暗中吸筹
        4. 量缩止跌(2分): 暴跌次日量缩≥20%, 卖压枯竭=洗盘结束信号
        5. 跌幅收窄(1分): 第二次暴跌幅度<第一次, 恐慌在减弱
        6. 洗盘日主力微正(2分): 暴跌日主力净额为正 → 洗盘铁证(V13.5.18新增)
        
        洗盘确认: ≥6分 = WASHOUT(持仓/加仓) | ≥8分 = STRONG_WASHOUT
        死猫跳否决: <3分 = DEAD_CAT(清仓)
        注意: D29≥6分时→豁免SECTOR_CRASH惩罚(D8不打折, 阈值不提升)
        """
        max_score = 12.0
        
        if len(klines) < 20:
            return DimensionScore('双洗盘识别', max_score, 0, None, '数据不足(需≥20日K线)', False)
        
        score = 0.0
        detail_parts = []
        
        # ── 1. 底部抬高检测 (3分) ──
        # 寻找20日内的绝对低点, 对比近3日低点是否抬高
        lookback = min(20, len(klines))
        recent = klines[-lookback:]
        recent_3 = klines[-3:]
        
        # 20日内绝对最低价
        abs_low = min(b.low for b in recent)
        abs_low_idx = next(i for i, b in enumerate(recent) if b.low == abs_low)
        
        # 近3日最低价
        recent_low = min(b.low for b in recent_3)
        
        # 底部抬高计算
        if recent_low > abs_low:
            higher_margin = (recent_low - abs_low) / abs_low * 100
            score += 3.0
            detail_parts.append(f'底部抬高({abs_low:.2f}→{recent_low:.2f}, +{higher_margin:.1f}%)')
        elif recent_low >= abs_low * 0.98:  # 2%容差
            score += 1.5
            detail_parts.append(f'底部持平({abs_low:.2f}≈{recent_low:.2f})')
        else:
            # 跌破前低! 死猫跳特征!
            detail_parts.append(f'⚠底部未抬高({recent_low:.2f}<前低{abs_low:.2f}) — 死猫跳风险!')
        
        # ── 2. 双次大涨检测 (2分) ──
        # 20日内出现≥2次单日涨≥8%
        big_rallies = []
        for b in recent:
            if len(klines) >= 2:
                idx = list(klines).index(b) if b in klines else -1
            if hasattr(b, 'chg_pct') and b.chg_pct >= 8.0:
                big_rallies.append(b)
        
        # 用更可靠的方法: 遍历klines计算涨跌幅
        big_rally_dates = []
        for i in range(1, len(recent)):
            if recent[i-1].close > 0:
                chg = (recent[i].close - recent[i-1].close) / recent[i-1].close * 100
                if chg >= 8.0:
                    big_rally_dates.append(i)
        
        if len(big_rally_dates) >= 2:
            score += 2.0
            detail_parts.append(f'双次大涨({len(big_rally_dates)}次≥8%) — 主力反复试盘')
        elif len(big_rally_dates) >= 1:
            score += 1.0
            detail_parts.append(f'单次大涨(1次≥8%)')
        
        # ── 3. 大跌日巨量检测 (2分) ──
        # 寻找20日内大涨后暴跌日(跌≥5%), 检查当日量是否为5日均量的1.5倍以上
        
        # 先找20日内的"大涨后暴跌"事件
        crash_with_high_vol = False
        for i in range(2, len(recent)):
            # 前一日涨≥5% + 当日跌≥5% = 洗盘砸盘日
            prev_chg = (recent[i-1].close - recent[i-2].close) / recent[i-2].close * 100 if i >= 2 and recent[i-2].close > 0 else 0
            today_chg = (recent[i].close - recent[i-1].close) / recent[i-1].close * 100 if recent[i-1].close > 0 else 0
            
            if prev_chg >= 5.0 and today_chg <= -5.0:
                # 这是洗盘砸盘日, 检查成交量
                crash_vol = recent[i].volume if hasattr(recent[i], 'volume') else 0
                # 5日均量(不含当日)
                vol_5d_avg = sum(recent[j].volume for j in range(max(0, i-5), i) if hasattr(recent[j], 'volume')) / min(5, i)
                
                if crash_vol > vol_5d_avg * 1.5:
                    crash_with_high_vol = True
                    detail_parts.append(f'大跌日巨量(量{crash_vol/10000:.1f}万, 5日均{vol_5d_avg/10000:.1f}万, {crash_vol/vol_5d_avg:.1f}x) — 洗盘特征!')
                    break
                elif crash_vol > vol_5d_avg:
                    crash_with_high_vol = True
                    score += 1.0
                    detail_parts.append(f'大跌日放量({crash_vol/10000:.1f}万, {crash_vol/vol_5d_avg:.1f}x均量)')
                    break
        
        if crash_with_high_vol and score >= 3.0:
            # 满足底部抬高条件时才给满分
            score += 2.0
        
        # ── 4. 量缩止跌检测 (2分) ──
        # 近2日的量对比, 如果前日大跌放量+今日缩量≥20%=洗盘结束
        
        if len(recent) >= 2:
            today = recent[-1]
            yesterday = recent[-2]
            
            y_chg = (yesterday.close - recent[-3].close) / recent[-3].close * 100 if len(recent) >= 3 and recent[-3].close > 0 else 0
            t_chg = (today.close - yesterday.close) / yesterday.close * 100 if yesterday.close > 0 else 0
            
            if y_chg <= -3.0:  # 昨日跌幅≥3%
                y_vol = yesterday.volume if hasattr(yesterday, 'volume') else 0
                t_vol = today.volume if hasattr(today, 'volume') else 0
                
                if t_vol > 0 and y_vol > 0:
                    vol_shrink = (y_vol - t_vol) / y_vol * 100
                    if vol_shrink >= 20:
                        score += 2.0
                        detail_parts.append(f'量缩止跌(量{int(y_vol/10000)}万→{int(t_vol/10000)}万, 缩{vol_shrink:.0f}%) — 卖压枯竭!')
                    elif vol_shrink >= 10:
                        score += 1.0
                        detail_parts.append(f'量略缩({vol_shrink:.0f}%)')
        
        # ── 5. 跌幅收窄检测 (1分) ──
        # 20日内两次大跌的跌幅对比, 第二次跌幅<第一次=恐慌减弱
        
        big_crashes = []
        for i in range(1, len(recent)):
            if recent[i-1].close > 0:
                chg = (recent[i].close - recent[i-1].close) / recent[i-1].close * 100
                if chg <= -5.0:
                    big_crashes.append({'idx': i, 'chg': chg, 'date': ''})
        
        if len(big_crashes) >= 2:
            crash1 = big_crashes[-2]['chg']  # 倒数第二次大跌
            crash2 = big_crashes[-1]['chg']  # 最近一次大跌
            if crash2 > crash1:  # -11.6% > -12.5% = 收窄
                score += 1.0
                detail_parts.append(f'跌幅收窄({crash1:.1f}%→{crash2:.1f}%) — 恐慌在减弱')
        
        # ── 6. 洗盘日主力微正检测 (2分) — V13.5.18新增 ──
        # 蜀道装备6/24核心发现: 暴跌-8.97%但主力净额+169万(+0.28%) = 洗盘铁证!
        # 暴跌日(跌≥5%)的主力净额为正 → 主力没走=洗盘, 不是出货
        # 这是区分洗盘vs出货的最关键信号!
        washout_day_main_positive = False
        if capital_flow_history and len(capital_flow_history) >= 1:
            # 检查最近的暴跌日是否有主力微正
            for cf in capital_flow_history:
                main_net = cf.get('main_net', 0) or cf.get('mainlx', 0)
                # 需要匹配暴跌日: 查看对应日期的跌幅
                cf_date = cf.get('date', '')
                for i in range(1, len(recent)):
                    k_date = recent[i].date if hasattr(recent[i], 'date') else ''
                    if k_date == cf_date or (i == len(recent) - 1):
                        if recent[i-1].close > 0:
                            day_chg = (recent[i].close - recent[i-1].close) / recent[i-1].close * 100
                            if day_chg <= -5.0 and main_net > 0:
                                washout_day_main_positive = True
                                score += 2.0
                                detail_parts.append(
                                    f'洗盘日主力微正! 跌{day_chg:.1f}%但主力净额+{main_net/10000:.0f}万 — 洗盘铁证!'
                                )
                                break
                if washout_day_main_positive:
                    break
        
        # 无资金流历史数据时，用当日capital_flow推断
        if not washout_day_main_positive and capital_flow_history:
            # 查看最近一天数据是否为暴跌日+主力微正
            latest_cf = capital_flow_history[-1]
            latest_main = latest_cf.get('main_net', 0) or latest_cf.get('mainlx', 0)
            if len(klines) >= 2 and klines[-1].chg_pct <= -5.0 and latest_main > 0:
                washout_day_main_positive = True
                score += 2.0
                detail_parts.append(
                    f'洗盘日主力微正! 跌{klines[-1].chg_pct:.1f}%但主力净额+{latest_main/10000:.0f}万'
                )

        # ── 最终判定 ──
        score = min(max_score, score)
        
        if score >= 8:
            grade_text = 'STRONG_WASHOUT(强洗盘: 持仓/加仓)'
            passed = True
        elif score >= 6:
            grade_text = 'WASHOUT(洗盘确认: 持仓)'
            passed = True
        elif score >= 3:
            grade_text = 'WEAK_WASHOUT(疑似洗盘: 观察)'
            passed = False
        elif score >= 1:
            grade_text = 'DEAD_CAT(死猫跳嫌疑: 清仓)'
            passed = False
        else:
            grade_text = 'NO_SIGNAL'
            passed = False
        
        detail = '; '.join(detail_parts) if detail_parts else '无洗盘信号'
        raw = {
            'abs_low_20d': round(abs_low, 2),
            'recent_3d_low': round(recent_low, 2),
            'big_rally_count': len(big_rally_dates),
            'big_crash_count': len(big_crashes),
            'grade': grade_text
        }
        
        return DimensionScore('双洗盘识别', max_score, score, raw, detail, passed)

    # ─── D30: 尾盘量价信号 (12分) — V13.5.16新增 ───
    def score_tail_signal(self, klines: List[KlineBar],
                          intraday_data: Optional[Dict] = None) -> 'DimensionScore':
        """
        D30 尾盘量价信号维度 (12分) — V13.5.16新增
        
        核心使命: 精准识别T日尾盘→T+1日涨停的选股信号
        
        6/25→6/26验证回溯:
        ┌──────────┬──────────┬──────────────────────┬────────────┐
        │ 股票     │ 6/25尾盘 │ 6/25特征             │ 6/26结果   │
        ├──────────┼──────────┼──────────────────────┼────────────┤
        │ 蜀道装备 │ 25.42    │ 底部抬高+量缩止跌    │ +20%涨停   │
        │ 航天工程 │ 39.59    │ 涨停次日高开低走守稳 │ +9.97%涨停 │
        │ 威派格   │ 5.30     │ 一字板涨停           │ 一字2板    │
        │ 双良节能 │ 4.40     │ 低位微涨稳住        │ +10%涨停   │
        │ 祥鑫科技 │ 47.16    │ 涨停+催化           │ 2板涨停    │
        └──────────┴──────────┴──────────────────────┴────────────┘
        
        三大尾盘选股模式(从6/25→6/26涨停股提炼):
        
        模式1: 洗盘结束尾盘买入 (蜀道装备模式) — 5分
        - 底部抬高(绝对低点后底部上移) + 量缩至枯竭 + 跌幅收窄
        - D29洗盘识别≥6分时自动激活此模式
        
        模式2: 强势延续尾盘买入 (航天工程/威派格模式) — 5分  
        - 涨停次日不破前收盘 + 量能维持 + 板块催化持续
        - 关键: 涨停后次日回调<5%即强势延续
        
        模式3: 低位蓄力尾盘买入 (双良节能模式) — 2分
        - 跌幅>30%后企稳(连续3日跌幅<1%) + 量缩至地量 + 微涨收盘
        
        尾盘加分信号:
        - 尾盘30分钟放量拉升(量比突然放大): +3分
        - 尾盘委比>30%(主力护盘): +2分
        
        评分逻辑:
        1. 模式识别(0-5分): 三大模式匹配
        2. 尾盘量价加分(0-5分): 尾盘30分钟放量+委比
        3. 洗盘协同加分(0-2分): D29≥6分时自动+2分
        """
        max_score = 12.0
        
        if len(klines) < 5:
            return DimensionScore('尾盘量价信号', max_score, 0, None, '数据不足', False)
        
        today = klines[-1]
        recent_5 = klines[-6:-1] if len(klines) >= 6 else klines[:-1]
        
        score = 0.0
        detail_parts = []
        raw = {}
        
        # ─── 模式1: 洗盘结束尾盘买入 (蜀道装备模式) ───
        # 关键特征: 底部抬高 + 量缩止跌 + 跌幅收窄
        if len(klines) >= 20:
            # 找绝对低点
            low_20 = min(k.low for k in klines[-20:])
            low_20_idx = next(i for i, k in enumerate(klines[-20:]) if k.low == low_20)
            low_5 = min(k.low for k in klines[-5:])
            
            # 底部抬高: 近5日最低点 > 近20日绝对低点
            bottom_lift_pct = (low_5 - low_20) / low_20 * 100
            if bottom_lift_pct > 2.0:  # 底部抬高2%以上
                lift_score = min(3.0, bottom_lift_pct / 3.0)  # 每3%抬高分+1分, 最多3分
                score += lift_score
                detail_parts.append(f'底部抬高{bottom_lift_pct:.1f}%({lift_score:.0f}分)')
                raw['bottom_lift_pct'] = round(bottom_lift_pct, 2)
            
            # 量缩止跌: 近3日平均量 < 近20日平均量的70%
            avg_vol_3 = sum(k.volume for k in klines[-3:]) / 3
            avg_vol_20 = sum(k.volume for k in klines[-20:]) / 20
            vol_ratio_3_20 = avg_vol_3 / avg_vol_20 if avg_vol_20 > 0 else 0
            if vol_ratio_3_20 < 0.70:  # 量缩至70%以下
                score += 1.5
                detail_parts.append(f'量缩止跌(3日/20日={vol_ratio_3_20:.1%})')
                raw['vol_ratio_3_20'] = round(vol_ratio_3_20, 2)
            
            # 跌幅收窄: 近3日最大跌幅 < 近10日最大跌幅的50%
            max_drop_3 = max(abs((k.close - k.open) / k.open * 100) for k in klines[-3:])
            max_drop_10 = max(abs((k.close - k.open) / k.open * 100) for k in klines[-10:])
            if max_drop_3 < max_drop_10 * 0.5 and max_drop_10 > 3.0:
                score += 0.5
                detail_parts.append(f'跌幅收窄(近3日{max_drop_3:.1f}%<近10日{max_drop_10:.1f}%的一半)')
        
        # ─── 模式2: 强势延续尾盘买入 (航天工程/威派格模式) ───
        # 关键特征: 涨停次日回调<5% + 量能维持
        if len(recent_5) >= 2:
            # 检测前日是否涨停(涨幅≥9.9%)
            prev = recent_5[-1]
            prev_chg = (prev.close - prev.open) / prev.open * 100  # 用今开→今收模拟
            
            # 更准确: 用前日收盘vs前日前收
            if len(recent_5) >= 3:
                prev_prev = recent_5[-2]
                prev_chg_pct = (prev.close - prev_prev.close) / prev_prev.close * 100
                
                if prev_chg_pct >= 9.9:  # 前日涨停
                    # 涨停次日: 今天回调幅度
                    today_drop = (prev.close - today.close) / prev.close * 100 if prev.close > today.close else 0
                    
                    # 涨停次日稳住: 先判断极强延续(回调<3%), 再判断一般延续(回调<5%)
                    if 0 <= today_drop < 3.0:
                        score += 4.0  # 回调<3% = 极强延续
                        detail_parts.append(f'涨停次日极强延续(回调仅{today_drop:.1f}%)')
                        raw['strong_continuation'] = 'extreme'
                    elif 3.0 <= today_drop < 5.0:
                        score += 3.0  # 回调3-5% = 强势延续
                        detail_parts.append(f'涨停次日稳住(回调{today_drop:.1f}%)')
                        raw['strong_continuation'] = True
                    
                    # 量能维持: 今天量>前日前量(涨停日量自然大,次日量维持)
                    if today.volume > recent_5[-3].volume * 0.8:
                        score += 1.0
                        detail_parts.append(f'涨停次日量能维持')
        
        # ─── 模式3: 低位蓄力尾盘买入 (双良节能模式) ───
        # 关键特征: 深跌后企稳 + 微涨收盘 + 地量
        if len(klines) >= 30:
            high_30 = max(k.high for k in klines[-30:])
            drop_from_30 = (high_30 - today.close) / high_30 * 100
            
            if drop_from_30 > 30.0:  # 跌幅>30%后企稳
                # 连续3日跌幅<1%(企稳)
                stable_days = sum(1 for k in klines[-3:] 
                                 if abs((k.close - k.open) / k.open * 100) < 1.5)
                if stable_days >= 2:
                    score += 1.0
                    detail_parts.append(f'深跌后企稳(跌{drop_from_30:.1f}%,{stable_days}日稳)')
                    raw['deep_drop_stable'] = True
                
                # 地量: 今日量<近30日平均量的50%
                avg_vol_30 = sum(k.volume for k in klines[-30:]) / 30
                if today.volume < avg_vol_30 * 0.5:
                    score += 0.5
                    detail_parts.append(f'地量信号')
                
                # 微涨收盘
                today_chg = (today.close - today.open) / today.open * 100
                if 0 < today_chg < 3.0:
                    score += 0.5
                    detail_parts.append(f'微涨收盘({today_chg:.1f}%)')
        
        # ─── 尾盘加分信号 (需要日内数据) ───
        if intraday_data:
            # 尾盘30分钟放量拉升
            tail_volume_ratio = intraday_data.get('tail_30min_volume_ratio', 0)
            if tail_volume_ratio > 2.0:  # 尾盘放量2倍以上
                score += 3.0
                detail_parts.append(f'尾盘30min放量{tail_volume_ratio:.1f}x')
                raw['tail_volume_ratio'] = tail_volume_ratio
            elif tail_volume_ratio > 1.5:
                score += 1.5
                detail_parts.append(f'尾盘30min温和放量{tail_volume_ratio:.1f}x')
            
            # 尾盘委比>30%(主力护盘信号)
            tail_weibi = intraday_data.get('tail_weibi', 0)
            if tail_weibi > 30.0:
                score += 2.0
                detail_parts.append(f'尾盘委比{tail_weibi:.0f}%护盘')
                raw['tail_weibi'] = tail_weibi
        
        score = min(max_score, score)
        passed = score >= 6.0  # 6分以上=尾盘买入信号确认
        
        detail = '; '.join(detail_parts) if detail_parts else '无尾盘信号'
        
        return DimensionScore('尾盘量价信号', max_score, score, raw, detail, passed)

    # ═══════════════════════════════════════════════════════════
    # V13.5.17 新增: D31 主力资金意图 + D32 DDX大单动向估算
    # 来源: TDX主力找票器3.0三维度(量能异动+筹码结构+资金流向)研究提炼
    # 蜀道装备6/24洗盘日验证: 暴跌-8.97%但主力净额为正+169万 → 洗盘铁证
    # ═══════════════════════════════════════════════════════════

    def score_main_force_intent(self, klines: List[KlineBar],
                                 capital_flow: CapitalFlow = None,
                                 capital_flow_history: List[Dict] = None,
                                 d28_score: float = 0.0) -> 'DimensionScore':
        """
        D31: 主力资金意图 (15分) — V13.5.18校准
        基于TDX真实资金流向API数据, 识别洗盘vs出货
        
        蜀道装备6/24验证:
        - 6/24暴跌-8.97%, 但主力净额+169万(占比+0.28%) → 主力没走=洗盘!
        - 6/25继续跌-2.94%, 主力净额-1298万(占比-2.97%) → 小幅流出
        - 6/26涨停+20%, 主力净额+14019万(占比+13.31%) → 巨量买入
        
        V13.5.18校准 — 出货否决降权:
        埃斯顿7/2验证: 主力-5882万但D28催化=宇树IPO 8分满分 → 7/3涨停!
        结论: 催化力量>出货力量时, 出货否决应降权(-5→-2)
        逻辑: D28≥8分 → 出货惩罚降权50%(-5→-2.5); D28≥6分 → 降权30%(-5→-3.5)
        
        评分逻辑:
        1. 洗盘日主力净额为正(暴跌但主力买入) → 8分(洗盘铁证)
        2. 连续3日主力净额为正 → 5分(持续建仓)
        3. 超大单净买入占比>5% → 3分(机构大手笔)
        4. 主力净额占比>5% → 2分(强力买入)
        5. 主买净额占比>10% → 2分(主动买入强势)
        6. 洗盘信号(暴跌日+主力正) → 额外+3分
        """
        max_score = 15.0
        score = 0.0
        detail_parts = []
        raw = {}

        if not capital_flow or capital_flow.mainlx == 0:
            return DimensionScore('主力资金意图', max_score, 0.0, {},
                                  '无资金流向数据', False)

        # 当日主力净额
        main_net = capital_flow.mainlx
        main_pct = capital_flow.main_pct
        super_pct = capital_flow.super_large_pct
        main_buy_pct = capital_flow.main_buy_pct

        raw['main_net'] = main_net
        raw['main_pct'] = main_pct
        raw['super_large_pct'] = super_pct

        # 检测洗盘日: 当日跌幅>5%但主力净额为正
        today_drop = klines[-1].chg_pct if klines else 0
        is_washout_day = today_drop < -5.0 and main_net > 0

        if is_washout_day:
            score += 8.0
            detail_parts.append(f'洗盘铁证! 跌{today_drop:.1f}%但主力净额+{main_net/10000:.0f}万')
            raw['washout_signal'] = True

        # 连续3日主力净额为正
        if capital_flow_history and len(capital_flow_history) >= 3:
            recent_3 = capital_flow_history[-3:]
            consecutive_positive = all(r.get('main_net', 0) > 0 for r in recent_3)
            if consecutive_positive:
                score += 5.0
                total_3d = sum(r.get('main_net', 0) for r in recent_3)
                detail_parts.append(f'连续3日主力净流入累计+{total_3d/10000:.0f}万')
                raw['consecutive_3d_positive'] = True
            else:
                # 检查最近2日为正(转正趋势)
                recent_2 = capital_flow_history[-2:]
                if all(r.get('main_net', 0) > 0 for r in recent_2):
                    score += 2.0
                    detail_parts.append('近2日主力转正')
                    raw['reversal_2d'] = True
        elif main_net > 0:
            score += 2.0
            detail_parts.append(f'当日主力净流入+{main_net/10000:.0f}万')

        # 超大单净买入占比>5%
        if super_pct > 5.0:
            score += 3.0
            detail_parts.append(f'超大单净买入占比{super_pct:.1f}%')
        elif super_pct > 2.0:
            score += 1.0
            detail_parts.append(f'超大单小幅净买入{super_pct:.1f}%')

        # 主力净额占比>5%
        if main_pct > 5.0:
            score += 2.0
            detail_parts.append(f'主力净额占比{main_pct:.1f}%')
        elif main_pct > 2.0 and not is_washout_day:
            score += 1.0

        # 主买净额占比>10%
        if main_buy_pct > 10.0:
            score += 2.0
            detail_parts.append(f'主买净额占比{main_buy_pct:.1f}%')

        # 出货信号: 主力净额连续负+股价下跌
        # V13.5.18: 出货否决降权 — D28催化≥8分时降权(-5→-2.5)
        # 埃斯顿7/2验证: 主力-5882万但催化>出货力量→7/3涨停!
        if capital_flow_history and len(capital_flow_history) >= 3:
            recent_3 = capital_flow_history[-3:]
            all_negative = all(r.get('main_net', 0) < 0 for r in recent_3)
            price_dropping = klines[-1].close < klines[-3].close if len(klines) >= 3 else False
            if all_negative and price_dropping:
                total_outflow = sum(r.get('main_net', 0) for r in recent_3)
                # V13.5.18: 出货否决降权 — D28催化≥8分时降权(-5→-2.5)
                # 埃斯顿7/2验证: 主力-5882万但催化>出货力量→7/3涨停!
                base_penalty = -5.0
                if d28_score >= 8.0:
                    penalty = base_penalty * 0.5  # -5→-2.5 (催化>出货, 降权50%)
                    detail_parts.append(f'出货警告(降权)! 连续3日流出{total_outflow/10000:.0f}万, 但D28催化={d28_score:.0f}分降权')
                    raw['distribution_signal_downgraded'] = True
                elif d28_score >= 6.0:
                    penalty = base_penalty * 0.7  # -5→-3.5 (催化中等, 降权30%)
                    detail_parts.append(f'出货警告(部分降权)! 连续3日流出, D28={d28_score:.0f}分部分降权')
                    raw['distribution_signal_partial_downgrade'] = True
                else:
                    penalty = base_penalty  # -5 (无催化, 全惩罚)
                    detail_parts.append(f'出货警告! 连续3日流出{total_outflow/10000:.0f}万')
                score += penalty
                raw['distribution_signal'] = True

        score = max(0, min(max_score, score))
        passed = score >= 6.0

        detail = '; '.join(detail_parts) if detail_parts else '资金流信号弱'

        return DimensionScore('主力资金意图', max_score, score, raw, detail, passed)

    def score_ddx_estimate(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D32: DDX大单动向估算 (10分) — V13.5.17新增
        基于TDX DDX公式从OHLCV估算主力资金方向
        
        DDX公式来源(通达信内置):
        N:=20; JJ:=(H+L+C)/3; QJ0:=VOL/IF(H=L,4,H-L);
        QJ1:=QJ0*IF(CAPITAL=0, (JJ-MIN(C,O)), IF(H=L,1,(MIN(O,C)-L)));
        QJ2:=QJ0*IF(CAPITAL=0, (MIN(O,C)-L), IF(H=L,1,(JJ-MIN(C,O))));
        QJ3:=QJ0*IF(CAPITAL=0, (H-MAX(O,C)), IF(H=L,1,(H-MAX(O,C))));
        QJ4:=QJ0*IF(CAPITAL=0, (MAX(C,O)-JJ), IF(H=L,1,(MAX(C,O)-JJ)));
        DDX:=((QJ1+QJ2)-(QJ3+QJ4))/10000;
        
        V13.5.18修正 — 暴跌日DDX×0.5修正系数:
        蜀道6/24验证: DDX从OHLCV估算为负(暴跌-8.97%+大实体), 但真实主力净额+169万!
        根因: DDX估算基于价格区间分布,无法反映主力拆单策略(小单卖出+大单买入)
        暴跌日(跌≥5%)时, DDX估算系统性偏负,需×0.5修正
        
        核心逻辑:
        - QJ1+QJ2 = 低位买入量(下影线+实体下半) = 主力买入估算
        - QJ3+QJ4 = 高位卖出量(上影线+实体上半) = 主力卖出估算
        - DDX > 0 = 主力净买入
        
        评分:
        1. DDX>0且5日累计>0 → 5分(持续买入)
        2. DDX连续3日正值 → 3分(趋势确认)
        3. DDX上穿0轴(前日负+今日正) → 2分(反转信号)
        """
        max_score = 10.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(klines) < 3:
            return DimensionScore('DDX大单动向', max_score, 0.0, {},
                                  'K线数据不足', False)

        def calc_ddx(bar: KlineBar) -> float:
            """计算单日DDX值"""
            h, l, c, o, v = bar.high, bar.low, bar.close, bar.open, bar.volume
            if h == l:
                return 0.0
            jj = (h + l + c) / 3.0
            qj0 = v / (h - l)
            # 使用有流通股本版本(更准确)
            qj1 = qj0 * (min(o, c) - l) if min(o, c) > l else 0
            qj2 = qj0 * (jj - min(c, o)) if jj > min(c, o) else 0
            qj3 = qj0 * (h - max(o, c)) if h > max(o, c) else 0
            qj4 = qj0 * (max(c, o) - jj) if max(c, o) > jj else 0
            ddx = ((qj1 + qj2) - (qj3 + qj4)) / 10000.0
            return ddx

        # 计算最近5日DDX
        recent_klines = klines[-5:] if len(klines) >= 5 else klines
        ddx_values = [calc_ddx(b) for b in recent_klines]
        today_ddx = ddx_values[-1]
        ddx_5d_sum = sum(ddx_values)

        raw['today_ddx'] = round(today_ddx, 4)
        raw['ddx_5d_sum'] = round(ddx_5d_sum, 4)
        raw['ddx_values'] = [round(d, 4) for d in ddx_values]

        # 1. DDX>0且5日累计>0
        if today_ddx > 0 and ddx_5d_sum > 0:
            score += 5.0
            detail_parts.append(f'DDX={today_ddx:.2f}>0, 5日累计+{ddx_5d_sum:.2f}')
        elif today_ddx > 0:
            score += 2.0
            detail_parts.append(f'DDX={today_ddx:.2f}>0(今日转正)')

        # 2. DDX连续3日正值
        if len(ddx_values) >= 3:
            consecutive_3d = all(d > 0 for d in ddx_values[-3:])
            if consecutive_3d:
                score += 3.0
                detail_parts.append('连续3日DDX正值')

        # 3. DDX上穿0轴(前日负+今日正)
        if len(ddx_values) >= 2:
            if ddx_values[-2] < 0 and ddx_values[-1] > 0:
                score += 2.0
                detail_parts.append('DDX上穿0轴(反转信号)')
                raw['ddx_cross_zero'] = True

        # 出货警告: DDX连续负值
        if len(ddx_values) >= 3 and all(d < 0 for d in ddx_values[-3:]):
            score -= 3.0
            detail_parts.append(f'连续3日DDX负值(主力出货)')
            raw['ddx_distribution'] = True

        # V13.5.18: 暴跌日DDX×0.5修正系数
        # 蜀道6/24验证: DDX估算为负(大实体), 但真实主力+169万(微正)
        # 暴跌日DDX系统性偏负,无法反映主力拆单策略 → ×0.5修正
        today = klines[-1]
        if hasattr(today, 'chg_pct') and today.chg_pct <= -5.0:
            score = score * 0.5
            detail_parts.append(f'暴跌日DDX×0.5修正(跌{today.chg_pct:.1f}%, DDX估算不可靠)')
            raw['crash_day_ddx_correction'] = True

        score = max(0, min(max_score, score))
        passed = score >= 4.0

        detail = '; '.join(detail_parts) if detail_parts else 'DDX信号弱'

        return DimensionScore('DDX大单动向', max_score, score, raw, detail, passed)

    # ─── D33: 外盘/内盘比率 (3分) — V13.5.18新增 ───
    def score_outer_inner_ratio(self, klines: List[KlineBar],
                                 quote_data: Dict = None) -> 'DimensionScore':
        """
        D33: 外盘/内盘比率 (3分) — V13.5.18新增
        
        核心使命: 从TDX实时行情数据检测主力主动买入意愿
        
        外盘 = 主动买入成交量(以卖出价成交的量) → 主力主动买入
        内盘 = 主动卖出成交量(以买入价成交的量) → 主力主动卖出
        外盘/内盘比率 > 1.0 = 买入意愿强于卖出
        
        评分:
        1. 外盘/内盘 > 1.5 → 3分(强势主动买入)
        2. 外盘/内盘 > 1.2 → 2分(偏强买入)
        3. 外盘/内盘 > 1.0 → 1分(微弱买入意愿)
        
        数据来源: TDX tdx_quotes (hasProInfo=1时包含外盘/内盘)
        """
        max_score = 3.0
        score = 0.0
        detail_parts = []
        raw = {}
        
        # 从quote_data中提取外盘/内盘
        if quote_data:
            outer_vol = quote_data.get('outer_vol', 0) or quote_data.get('wp', 0)  # 外盘(主动买入)
            inner_vol = quote_data.get('inner_vol', 0) or quote_data.get('np', 0)  # 内盘(主动卖出)
            
            if outer_vol > 0 and inner_vol > 0:
                ratio = outer_vol / inner_vol
                raw['outer_vol'] = outer_vol
                raw['inner_vol'] = inner_vol
                raw['ratio'] = round(ratio, 2)
                
                if ratio > 1.5:
                    score += 3.0
                    detail_parts.append(f'外盘/内盘={ratio:.2f}>1.5 → 强势主动买入!')
                elif ratio > 1.2:
                    score += 2.0
                    detail_parts.append(f'外盘/内盘={ratio:.2f}>1.2 → 偏强买入')
                elif ratio > 1.0:
                    score += 1.0
                    detail_parts.append(f'外盘/内盘={ratio:.2f}>1.0 → 微弱买入意愿')
                else:
                    detail_parts.append(f'外盘/内盘={ratio:.2f}<1.0 → 主动卖出主导⚠️')
                    raw['sell_dominant'] = True
            elif outer_vol > 0 and inner_vol == 0:
                score += 3.0  # 无主动卖出=极端强势
                detail_parts.append('外盘成交/内盘=0 → 极端买入!')
                raw['ratio'] = float('inf')
            else:
                detail_parts.append('无外盘/内盘数据')
        
        # 无quote_data时, 用K线推断: 上涨日偏外盘强
        if not quote_data and len(klines) >= 1:
            today = klines[-1]
            if today.close > today.open:  # 阳线 → 外盘偏强
                rise_pct = (today.close - today.open) / today.open * 100
                if rise_pct >= 3.0:
                    score += 2.0
                    detail_parts.append(f'阳线涨{rise_pct:.1f}% → 推断外盘强')
                elif rise_pct >= 1.0:
                    score += 1.0
                    detail_parts.append(f'小阳线涨{rise_pct:.1f}% → 推断外盘偏强')
        
        score = min(max_score, score)
        passed = score >= 2.0
        
        detail = '; '.join(detail_parts) if detail_parts else '无外盘/内盘数据'
        
        return DimensionScore('外盘/内盘比率', max_score, score, raw, detail, passed)

    # ═══════════════════════════════════════════════════════════
    # V13.5.19 新增: D34 拆单识别 + D35 庄成本线 + D36 委比异动
    # 来源: 蜀道装备6/24+7/3 TDX真实资金流数据逆向研究
    # 核心发现: 超大单与大单方向背离 = 主力拆单暗中吸筹
    # ═══════════════════════════════════════════════════════════

    def score_split_order(self, klines: List[KlineBar],
                          capital_flow: CapitalFlow = None,
                          capital_flow_history: List[Dict] = None) -> 'DimensionScore':
        """
        D34: 主力拆单识别 (5分) — V13.5.19核心新发现
        
        蜀道装备6/24+7/3 TDX真实资金流双重验证:
        - 6/24暴跌-8.97%: 超大单-166万 + 大单+335万 + 主力净额+169万 → 拆单吸筹!
        - 7/3跌-1.22%: 超大单-429万 + 大单+606万 + 主力净额+177万 → 再次拆单!
        
        核心逻辑: 主力在暴跌日执行"超大单卖出(假装出货) + 大单买入(暗中吸筹)"策略
        → 超大单与大单方向背离 + 主力净额微正 = 拆单吸筹铁证
        
        评分:
        1. 超大单<0 AND 大单>0 AND 主力>0 → 5分(拆单吸筹铁证!)
        2. 超大单<0 AND 大单>0 AND 主力<0 → 2分(部分拆单, 需观察)
        3. 超大单>0 AND 大单>0 AND 主力>0 → 2分(常规建仓)
        4. 超大单<0 AND 大单<0 AND 主力<0 → -3分(真出货, 否决!)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        # 从capital_flow_history获取超大单和大单数据
        if not capital_flow_history or len(capital_flow_history) == 0:
            # 回退到capital_flow当日数据
            if not capital_flow or capital_flow.mainlx == 0:
                return DimensionScore('拆单识别', max_score, 0.0, {}, '无资金流向数据', False)
            # 用当日数据估算
            super_net = capital_flow.super_large_net if hasattr(capital_flow, 'super_large_net') else 0
            large_net = capital_flow.large_net if hasattr(capital_flow, 'large_net') else 0
            main_net = capital_flow.mainlx
        else:
            latest = capital_flow_history[-1]
            super_net = latest.get('super_large_net', 0) or latest.get('超大单净额', 0)
            large_net = latest.get('large_net', 0) or latest.get('大单净额', 0)
            main_net = latest.get('main_net', 0) or latest.get('主力净额', 0)

        raw['super_large_net'] = super_net
        raw['large_net'] = large_net
        raw['main_net'] = main_net

        super_positive = super_net > 0
        large_positive = large_net > 0
        main_positive = main_net > 0

        # 场景1: 超大单流出 + 大单流入 + 主力微正 = 拆单吸筹铁证!
        if not super_positive and large_positive and main_positive:
            score = 5.0
            detail_parts.append(
                f'拆单吸筹铁证! 超大单{super_net/10000:.0f}万(出) + '
                f'大单{large_net/10000:.0f}万(入) + 主力{main_net/10000:.0f}万(微正)'
            )
            raw['split_order_signal'] = True
            raw['split_type'] = 'stealth_absorb'

        # 场景2: 超大单流出 + 大单流入 + 主力为负 = 部分拆单
        elif not super_positive and large_positive and not main_positive:
            score = 2.0
            detail_parts.append(
                f'部分拆单信号: 超大单出+大单入, 但主力净额{main_net/10000:.0f}万为负, 需观察'
            )
            raw['split_order_signal'] = True
            raw['split_type'] = 'partial'

        # 场景3: 超大单正 + 大单正 + 主力正 = 常规建仓
        elif super_positive and large_positive and main_positive:
            score = 2.0
            detail_parts.append(
                f'常规建仓: 超大单+大单+主力同向流入, 主力{main_net/10000:.0f}万'
            )
            raw['split_type'] = 'normal'

        # 场景4: 超大单负 + 大单负 + 主力负 = 真出货
        elif not super_positive and not large_positive and not main_positive:
            score = -3.0
            detail_parts.append(
                f'真出货! 超大单{super_net/10000:.0f}万+大单{large_net/10000:.0f}万+主力{main_net/10000:.0f}万全流出'
            )
            raw['distribution_signal'] = True
            raw['split_type'] = 'distribution'

        # 场景5: 超大单正 + 大单负 + 主力正 = 超大单主导
        elif super_positive and not large_positive and main_positive:
            score = 3.0
            detail_parts.append(
                f'超大单主导买入: 超大单{super_net/10000:.0f}万正, 大单{large_net/10000:.0f}万负'
            )
            raw['split_type'] = 'super_dominant'

        else:
            detail_parts.append(f'资金流信号不明确: 超大单{super_net/10000:.0f}万, 大单{large_net/10000:.0f}万')

        score = max(-3.0, min(max_score, score))
        passed = score >= 3.0

        detail = '; '.join(detail_parts) if detail_parts else '拆单信号弱'
        return DimensionScore('拆单识别', max_score, score, raw, detail, passed)

    def score_dealer_cost_line(self, klines: List[KlineBar]) -> 'DimensionScore':
        """
        D35: 庄成本线距离 (5分) — V13.5.19新增
        来源: 通达信COSTEX函数全网最佳实践研究
        
        庄成本1: MA(COSTEX(C, REF(C,5)), N) — 5日庄家短期成本线
        庄成本3: MA(COSTEX(REF(C,10), REF(C,24)), N) — 10-24日长期成本线
        
        COSTEX(p1, p2) = 在价格p1到p2区间内的成交量加权平均成本
        由于无法直接获取Level-2筹码分布, 用OHLCV近似:
        COSTEX近似 = (H+L+C)/3 的N日加权移动平均
        
        评分:
        1. 股价在庄成本1线上方 + 成本线向上 → 5分(多头趋势)
        2. 股价回踩庄成本1线企稳(距离<2%) → 4分(支撑确认)
        3. 短期成本线与长期成本线高度粘合(偏离<3%) → 3分(变盘前兆)
        4. 股价在庄成本1线下方但距离<5% → 1分(接近支撑)
        5. 放量跌破庄成本1线>5% → -3分(趋势破坏)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(klines) < 10:
            return DimensionScore('庄成本线', max_score, 0.0, {}, '数据不足(需≥10日K线)', False)

        # 近似COSTEX: 用(H+L+C)/3作为当日市场平均成本
        # 庄成本1 = 近6日的(H+L+C)/3加权平均(越近权重越大)
        recent = klines[-min(24, len(klines)):]
        n = len(recent)

        # 庄成本1: 5日短期成本(加权: 最新日权重最大)
        short_period = min(5, n)
        short_costs = [(b.high + b.low + b.close) / 3.0 for b in recent[-short_period:]]
        weights_short = list(range(1, short_period + 1))  # 1,2,3,4,5
        dealer_cost_1 = sum(c * w for c, w in zip(short_costs, weights_short)) / sum(weights_short)

        # 庄成本3: 10-24日长期成本(简单平均)
        long_period = min(24, n)
        long_costs = [(b.high + b.low + b.close) / 3.0 for b in recent[-long_period:]]
        dealer_cost_3 = sum(long_costs) / len(long_costs)

        # 庄成本1前日值(判断趋势方向)
        if n >= 6:
            prev_short_costs = [(b.high + b.low + b.close) / 3.0 for b in recent[-(short_period+1):-1]]
            prev_weights = list(range(1, short_period + 1))
            dealer_cost_1_prev = sum(c * w for c, w in zip(prev_short_costs, prev_weights)) / sum(prev_weights)
            cost_trend_up = dealer_cost_1 > dealer_cost_1_prev
        else:
            cost_trend_up = dealer_cost_1 > dealer_cost_3  # 回退判断

        today_close = klines[-1].close
        dist_to_cost1 = (today_close - dealer_cost_1) / dealer_cost_1 * 100 if dealer_cost_1 > 0 else 0
        cost_spread = abs(dealer_cost_1 - dealer_cost_3) / dealer_cost_3 * 100 if dealer_cost_3 > 0 else 0

        raw['dealer_cost_1'] = round(dealer_cost_1, 2)
        raw['dealer_cost_3'] = round(dealer_cost_3, 2)
        raw['dist_to_cost1_pct'] = round(dist_to_cost1, 2)
        raw['cost_spread_pct'] = round(cost_spread, 2)
        raw['cost_trend_up'] = cost_trend_up

        # 场景1: 股价在庄成本1线上方 + 成本线向上 → 多头趋势
        if dist_to_cost1 > 2.0 and cost_trend_up:
            score = 5.0
            detail_parts.append(
                f'多头趋势! 收盘{today_close:.2f}在庄成本1({dealer_cost_1:.2f})上方{dist_to_cost1:.1f}%, 成本线向上'
            )
            raw['signal'] = 'bullish_trend'

        # 场景2: 股价回踩庄成本1线企稳(距离<2%) → 支撑确认
        elif abs(dist_to_cost1) <= 2.0:
            score = 4.0
            detail_parts.append(
                f'支撑确认! 收盘{today_close:.2f}回踩庄成本1({dealer_cost_1:.2f}), 距离{dist_to_cost1:.1f}%'
            )
            raw['signal'] = 'support_bounce'

        # 场景3: 短期成本线与长期成本线高度粘合 → 变盘前兆
        elif cost_spread < 3.0:
            score = 3.0
            detail_parts.append(
                f'变盘前兆! 庄成本1({dealer_cost_1:.2f})与成本3({dealer_cost_3:.2f})粘合, 偏离{cost_spread:.1f}%'
            )
            raw['signal'] = 'convergence'

        # 场景4: 股价在庄成本1线下方但距离<5% → 接近支撑
        elif -5.0 < dist_to_cost1 <= -2.0:
            score = 1.0
            detail_parts.append(
                f'接近支撑: 收盘{today_close:.2f}在庄成本1({dealer_cost_1:.2f})下方{abs(dist_to_cost1):.1f}%'
            )
            raw['signal'] = 'near_support'

        # 场景5: 放量跌破庄成本1线>5% → 趋势破坏
        elif dist_to_cost1 < -5.0:
            # 检查是否放量
            today_vol = klines[-1].volume
            avg_vol_5d = sum(b.volume for b in klines[-6:-1]) / 5 if len(klines) >= 6 else today_vol
            is_high_vol = today_vol > avg_vol_5d * 1.2
            if is_high_vol:
                score = -3.0
                detail_parts.append(
                    f'趋势破坏! 放量跌破庄成本1({dealer_cost_1:.2f}), 距离{dist_to_cost1:.1f}%'
                )
                raw['signal'] = 'trend_break'
            else:
                score = 0.0
                detail_parts.append(
                    f'缩量在庄成本1下方({dealer_cost_1:.2f}), 距离{dist_to_cost1:.1f}%, 观望'
                )
                raw['signal'] = 'below_cost_weak'
        else:
            score = 0.0
            detail_parts.append(f'庄成本1={dealer_cost_1:.2f}, 收盘{today_close:.2f}, 距离{dist_to_cost1:.1f}%')

        score = max(-3.0, min(max_score, score))
        passed = score >= 3.0

        detail = '; '.join(detail_parts) if detail_parts else '庄成本信号弱'
        return DimensionScore('庄成本线', max_score, score, raw, detail, passed)

    def score_weibi_anomaly(self, klines: List[KlineBar],
                            quote_data: Dict = None) -> 'DimensionScore':
        """
        D36: 委比异动 (3分) — V13.5.19新增
        
        蜀道装备7/3验证: 委比=14.84% 但当日跌-1.22% → 主力挂单护盘!
        
        委比 = (买盘挂单量-卖盘挂单量)/(买盘+卖盘) × 100%
        委比为正 = 买盘挂单>卖盘 = 主力在下方挂单护盘
        
        评分:
        1. 委比>15% AND 当日跌 → 3分(主力挂单护盘=洗盘信号!)
        2. 委比>10% AND 当日涨 → 2分(买盘强势)
        3. 委比>5% → 1分(偏多)
        4. 委比<-10% → -2分(卖压重)
        
        数据来源: TDX tdx_quotes ProInfo.Wtb
        """
        max_score = 3.0
        score = 0.0
        detail_parts = []
        raw = {}

        weibi = None
        if quote_data:
            weibi = quote_data.get('weibi') or quote_data.get('Wtb') or quote_data.get('委比')

        if weibi is not None:
            weibi = float(weibi)
            raw['weibi'] = weibi

            today_chg = klines[-1].chg_pct if klines and hasattr(klines[-1], 'chg_pct') else 0
            is_down_day = today_chg < 0

            # 场景1: 委比>15% AND 跌 → 主力挂单护盘=洗盘信号
            if weibi > 15.0 and is_down_day:
                score = 3.0
                detail_parts.append(
                    f'主力护盘! 委比{weibi:.1f}%>15% 但跌{today_chg:.1f}% → 挂单护盘=洗盘信号'
                )
                raw['signal'] = 'guard_support'

            # 场景2: 委比>10% AND 涨 → 买盘强势
            elif weibi > 10.0 and not is_down_day:
                score = 2.0
                detail_parts.append(
                    f'买盘强势: 委比{weibi:.1f}%>10% + 涨{today_chg:.1f}%'
                )
                raw['signal'] = 'strong_buy'

            # 场景3: 委比>5% → 偏多
            elif weibi > 5.0:
                score = 1.0
                detail_parts.append(f'委比{weibi:.1f}%>5% → 偏多')
                raw['signal'] = 'slight_bullish'

            # 场景4: 委比<-10% → 卖压重
            elif weibi < -10.0:
                score = -2.0
                detail_parts.append(f'委比{weibi:.1f}%<-10% → 卖压重')
                raw['signal'] = 'sell_pressure'

            else:
                detail_parts.append(f'委比{weibi:.1f}% → 中性')
                raw['signal'] = 'neutral'
        else:
            # 无quote_data时用盘口推断: 买五档总量vs卖五档总量
            if quote_data and 'BspInfo' in quote_data:
                bsp = quote_data.get('BspInfo', [])
                if bsp:
                    total_buy = sum(float(b.get('BuyV', 0)) for b in bsp if b.get('BuyV'))
                    total_sell = sum(float(b.get('SellV', 0)) for b in bsp if b.get('SellV'))
                    if total_buy + total_sell > 0:
                        weibi_est = (total_buy - total_sell) / (total_buy + total_sell) * 100
                        raw['weibi_estimated'] = round(weibi_est, 2)
                        if weibi_est > 15.0:
                            score = 2.0  # 估算降一级
                            detail_parts.append(f'盘口估算委比{weibi_est:.1f}%>15% → 偏多(估算)')
                        elif weibi_est > 5.0:
                            score = 1.0
                            detail_parts.append(f'盘口估算委比{weibi_est:.1f}% → 偏多(估算)')
                        elif weibi_est < -10.0:
                            score = -1.0
                            detail_parts.append(f'盘口估算委比{weibi_est:.1f}% → 卖压(估算)')
                    else:
                        detail_parts.append('盘口数据为空')
                else:
                    detail_parts.append('无委比数据')
            else:
                detail_parts.append('无委比数据(需TDX ProInfo)')

        score = max(-2.0, min(max_score, score))
        passed = score >= 2.0

        detail = '; '.join(detail_parts) if detail_parts else '委比信号弱'
        return DimensionScore('委比异动', max_score, score, raw, detail, passed)

    # ─── 模式匹配 ───
    def match_historical_pattern(self, dimensions: List[DimensionScore], klines: List[KlineBar]) -> Tuple[str, float]:
        """
        与历史成功反转模式匹配
        返回: (匹配的模式名称, 相似度%)
        """
        best_match = 'none'
        best_similarity = 0.0

        total_score = sum(d.actual_score for d in dimensions)
        max_total = sum(d.max_score for d in dimensions)
        score_pct = total_score / max_total if max_total > 0 else 0

        for pattern_id, pattern in self.HISTORICAL_PATTERNS.items():
            pattern_score_pct = pattern['score_before_reversal'] / 100

            # 基础相似度 = 评分接近度
            similarity = 1.0 - abs(score_pct - pattern_score_pct)
            similarity *= 100

            # 特征匹配加分
            key_features = pattern.get('key_features', {})

            # 量比特征匹配
            if klines:
                today_vr = klines[-1].volume_ratio
                pattern_vr = key_features.get('reversal_volume_ratio', 1.5)
                vr_diff = abs(today_vr - pattern_vr) / max(pattern_vr, 0.1)
                if vr_diff < 0.3:
                    similarity = min(100, similarity + 5)

                # 缩量特征
                if len(klines) >= 3:
                    recent_3 = klines[-3:]
                    drying = sum(1 for b in recent_3 if b.volume_ratio < 0.8)
                    pattern_drying = key_features.get('volume_drying_days', 2)
                    if drying >= pattern_drying:
                        similarity = min(100, similarity + 3)

            if similarity > best_similarity:
                best_similarity = similarity
                best_match = f"{pattern['name']}({pattern['code']}) {pattern['pattern']}"

        return best_match, round(best_similarity, 1)


# ═══════════════════════════════════════════════════════════
# SECTION 3: 预测引擎
# ═══════════════════════════════════════════════════════════

class ReversalPredictor:
    """反转预测引擎 — 从评分到预测"""

    # 评分→上涨概率映射 V4.0 (46维度阈值调整, max ~238)
    SCORE_TO_UP_PROB = {
        # (score_min, score_max): (t1_up_prob, trend_3d, trend_5d, trend_7d)
        (80, 250): (0.78, 0.58, 0.43, 0.33),
        (68, 80):  (0.68, 0.48, 0.35, 0.28),
        (56, 68):  (0.58, 0.40, 0.28, 0.22),
        (47, 56):  (0.52, 0.33, 0.22, 0.17),
        (37, 47):  (0.47, 0.25, 0.18, 0.12),
        (0, 37):   (0.42, 0.18, 0.12, 0.07),
    }

    # T+1波动幅度预测 V4.0
    SCORE_TO_T1_RANGE = {
        # (score_min, score_max): (low_pct, mid_pct, high_pct) relative to T close
        (80, 250): (-1.0, 5.5, 11.0),
        (68, 80):  (-2.0, 3.5, 8.0),
        (56, 68):  (-2.0, 2.5, 5.5),
        (47, 56):  (-3.0, 1.5, 4.5),
        (37, 47):  (-3.0, 0.5, 3.5),
        (0, 37):   (-4.0, -0.5, 2.5),
    }

    def __init__(self):
        self.engine = ReversalSignalEngine()

    def predict(self, code: str, name: str,
                klines: List[KlineBar],
                lhb_data: List[DragonTigerEntry] = None,
                capital_flow: CapitalFlow = None,
                sector_data: Dict = None,
                quote_data: Dict = None,
                sentiment_data: Dict = None,
                market_state: Dict = None,
                catalyst_data: Dict = None,
                intraday_data: Dict = None,
                capital_flow_history: List[Dict] = None,
                fundamental_data: Dict = None,
                skill_enhancement: Dict = None) -> ReversalPrediction:
        """
        执行完整反转预测

        V3.1新增参数:
            quote_data: 实时行情数据 (用于D15精度提升)
            sentiment_data: 舆情数据 (用于D18/D19)
        V3.2新增参数:
            market_state: {'sector_crash': bool, 'decline_ratio': float} SECTOR_CRASH智能降权
        V3.4新增参数:
            catalyst_data: {'type': str, 'name': str, 'continuity_days': int, ...} 催化剂数据 (用于D28)
        V3.6新增参数:
            intraday_data: {'tail_30min_volume_ratio': float, 'tail_weibi': float, ...} 尾盘日内数据 (用于D30)
        V13.5.17新增参数:
            capital_flow_history: [{'date': str, 'main_net': float, 'main_pct': float, ...}, ...]
                                  TDX资金流向API多日历史 (用于D31主力资金意图)
        V13.5.18新增参数:
            fundamental_data: {'pe': float, 'pb': float, 'roe': float, 'quality_score': float,
                              'valuation_level': str, ...} TDX tdx_indicator_select基本面数据
                              (用于D21四阶段增强+基本面质量评分)
            skill_enhancement: V13.5.18 Skills→M71集成数据 (来自V13_5_SkillM71Integrator)
                              包含: d21_phase_data/d24_chip_data/d25_volume_data/
                                    d28_catalyst_data/d31_main_capital_data/d33_quote_data等
                              (用于D21/D24/D25/D28/D31/D33维度增强)
        """
        date_str = datetime.now().strftime('%Y-%m-%d')

        # ── 10维度评分 V2 ──
        d1 = self.engine.score_volume_drying(klines)
        d2 = self.engine.score_volume_surge(klines)
        d3 = self.engine.score_institutional_buy(lhb_data or [])
        d4 = self.engine.score_top_seats(lhb_data or [])
        d5 = self.engine.score_capital_flow(capital_flow or CapitalFlow())
        d6 = self.engine.score_double_bottom(klines)
        d7 = self.engine.score_sector_resonance(sector_data)
        d8 = self.engine.score_oversold(klines)
        d9 = self.engine.score_price_structure(klines)
        d10 = self.engine.score_turnover_low(klines)

        dimensions = [d1, d2, d3, d4, d5, d6, d7, d8, d9, d10]
        total_score = sum(d.actual_score for d in dimensions)

        # ══ V2.6: 硬否决制 + 恐慌见底覆盖(CRO) + 多重确认制 + 死猫跳过滤 ══
        # V2.5→V2.6: CRO拯救4个正向样本, V3软化, 阈值放松, 确认质量加权
        confirm_count, confirm_details = 0, []
        cap_info = {}  # 恐慌见底覆盖信息
        vetoed, veto_reasons, override_info = self._evaluate_v25_vetos(klines, d8)
        
        # ══ V3.2: crash_warnings必须在此初始化(避免UnboundLocalError) ══
        sector_crash = False
        decline_ratio = 0.0
        crash_warnings = []

        if vetoed:
            grade = ReversalGrade.NO_SIGNAL
            total_score = 0.0
        else:
            # V2.6: 恐慌见底覆盖 — CRO激活时加恐慌见底分
            cap_info = override_info
            cap_bonus = override_info.get('bonus', 0) if override_info.get('capitulation') else 0

            # V2.6 确认条件
            confirm_count, confirm_details = self._evaluate_v25_confirms(klines, d8, d9)

            # V2.7: D11-D13 (V2.6已有) + D14-D17 (V2.7新增)
            d11 = self.engine.score_decline_deceleration(klines)
            d12 = self.engine.score_kline_pattern(klines)
            d13 = self.engine.score_volume_price_divergence(klines)
            # V2.7新增: 基于用户建议的多维数据融合
            d14 = self.engine.score_ma_trend(klines)           # 60日均线趋势
            d15 = self.engine.score_auction_gap(klines, quote_data=quote_data)  # V3.1: 传入实时行情
            d16 = self.engine.score_prev_day_support(klines)   # 前日盘后承接
            d17 = self.engine.score_volume_price_trend(klines)  # 量价趋势配合
            # V3.1 新增: D18舆情热度 + D19舆情趋势
            d18 = self.engine.score_sentiment_heat(klines, stock_code=code, sentiment_data=sentiment_data)  # V3.1: 传入舆情数据
            d19 = self.engine.score_sentiment_trend(klines, stock_code=code, sentiment_data=sentiment_data)  # V3.1: 传入舆情数据
            d20 = self.engine.score_distance_from_5d_high(klines)  # V3.2: 距5日高点(防追高)
            d21 = self.engine.score_four_stages(code, name, klines, capital_flow)  # V13.5.11: 四阶段分析
            # V13.5.13: 新增三维度 — 放量启动/趋势延续/低位蓄势
            d25 = self.engine.score_volume_surge_initiation(klines)  # D25: 放量启动(10分) V13.5.14三路升级
            d26 = self.engine.score_trend_continuation(klines)       # D26: 趋势延续(8分)
            d27 = self.engine.score_low_accumulation(klines)         # D27: 低位蓄势(7分)
            # V13.5.14: 新增催化强度维度
            d28 = self.engine.score_catalyst_strength(code, name, klines, catalyst_data=catalyst_data)  # D28: 催化强度(8分)
            # V13.5.18: Skills→M71集成增强 (skill_enhancement参数)
            # 当skill_enhancement提供额外数据时, 增强对应维度评分
            if skill_enhancement:
                # D28催化增强: 如果Skills公告分析给出更高催化评分
                se_d28 = skill_enhancement.get('d28_catalyst_data', {})
                if se_d28 and se_d28.get('catalyst_d28_score', 0) > d28.actual_score:
                    skill_bonus = min(se_d28.get('catalyst_d28_score', 0) - d28.actual_score, 3.0)
                    d28 = DimensionScore('催化强度(Skills增强)', d28.max_score,
                        min(d28.max_score, d28.actual_score + skill_bonus),
                        {**d28.raw_data, 'skill_d28_bonus': skill_bonus,
                         'skill_catalyst_type': se_d28.get('catalyst_type', ''),
                         'skill_catalyst_events': se_d28.get('catalyst_events', [])},
                        d28.detail + f'; Skills催化增强+{skill_bonus:.1f}({se_d28.get("catalyst_type","")})',
                        d28.passed)
                # D28事件催化补充
                se_d28_event = skill_enhancement.get('d28_catalyst_data', {}).get('event_bonus', 0)
                if se_d28_event > 0:
                    d28 = DimensionScore('催化强度(事件增强)', d28.max_score,
                        min(d28.max_score, d28.actual_score + se_d28_event),
                        {**d28.raw_data, 'skill_event_bonus': se_d28_event},
                        d28.detail + f'; 事件催化+{se_d28_event:.1f}',
                        d28.passed)
                
                # D21四阶段增强: Skills市场主线+产业链数据
                se_d21 = skill_enhancement.get('d21_phase_data', {})
                if se_d21 and se_d21.get('phase_d21_score', 0) > 0:
                    d21_skill_bonus = min(se_d21.get('phase_d21_score', 0), 3.0)
                    d21 = DimensionScore('四阶段(Skills增强)', d21.max_score,
                        min(d21.max_score, d21.actual_score + d21_skill_bonus),
                        {**d21.raw_data, 'skill_d21_bonus': d21_skill_bonus},
                        d21.detail + f'; Skills主线增强+{d21_skill_bonus:.1f}',
                        d21.passed)
                
                # D24三高筹码增强: Skills财务数据+政策数据
                se_d24 = skill_enhancement.get('d24_chip_data', {})
                if se_d24 and se_d24.get('chip_d24_score', 0) > 0:
                    d24_skill_bonus = min(se_d24.get('chip_d24_score', 0), 3.0)
                    d24 = DimensionScore('三高筹码(Skills增强)', d24.max_score,
                        min(d24.max_score, d24.actual_score + d24_skill_bonus),
                        {**d24.raw_data, 'skill_d24_bonus': d24_skill_bonus},
                        d24.detail + f'; Skills筹码增强+{d24_skill_bonus:.1f}',
                        d24.passed)
                
                # D33外盘内盘增强: Skills实时行情数据
                se_d33 = skill_enhancement.get('d33_quote_data', {})
                if se_d33 and se_d33.get('d33_score', 0) > d33.actual_score:
                    d33 = DimensionScore('外盘内盘(Skills增强)', d33.max_score,
                        min(d33.max_score, se_d33.get('d33_score', d33.actual_score)),
                        {**d33.raw_data, 'skill_d33_source': 'tdx_quotes',
                         'skill_outer_inner_ratio': se_d33.get('outer_inner_ratio', 0)},
                        se_d33.get('d33_detail', d33.detail),
                        d33.passed)
            # V13.5.15: 新增双洗盘识别维度
            d29 = self.engine.score_double_washout(klines, capital_flow_history=capital_flow_history)  # D29: 双洗盘识别(12分) V13.5.18升级
            d30 = self.engine.score_tail_signal(klines, intraday_data=intraday_data)  # D30: 尾盘量价信号(12分)
            # V13.5.17: 新增D31主力资金意图 + D32 DDX大单动向
            # V13.5.18: D31传入d28_score(出货否决降权需要)
            d28_score_val = d28.actual_score  # 先算D28得分
            d31 = self.engine.score_main_force_intent(klines, capital_flow=capital_flow,
                                                       capital_flow_history=capital_flow_history,
                                                       d28_score=d28_score_val)  # D31: 主力资金意图(15分) V13.5.18校准
            d32 = self.engine.score_ddx_estimate(klines)  # D32: DDX大单动向估算(10分) V13.5.18暴跌日修正
            
            # V13.5.18: D29+D31交叉确认 → D30额外+0.5分
            # 当洗盘确认(D29≥6) AND 主力意图确认(D31≥6) 时, 尾盘信号精度提升
            if d29.actual_score >= 6.0 and d31.actual_score >= 6.0:
                d30_cross_bonus = 0.5
                # 修改d30的score和detail
                d30_actual = d30.actual_score + d30_cross_bonus
                d30 = DimensionScore('尾盘量价信号(交叉确认)', d30.max_score,
                    min(d30.max_score, d30_actual),
                    {**d30.raw_data, 'd29_d31_cross': True, 'cross_bonus': d30_cross_bonus},
                    d30.detail + f'; D29+D31交叉确认(+{d30_cross_bonus}分)', d30.passed)
            
            # V13.5.18: D33外盘/内盘比率(3分)
            d33 = self.engine.score_outer_inner_ratio(klines, quote_data=quote_data)  # D33: 外盘/内盘比率(3分)
            
            # V13.5.19: D34拆单识别(5分) + D35庄成本线(5分) + D36委比异动(3分)
            d34 = self.engine.score_split_order(klines, capital_flow=capital_flow,
                                                 capital_flow_history=capital_flow_history)  # D34: 拆单识别(5分)
            d35 = self.engine.score_dealer_cost_line(klines)  # D35: 庄成本线(5分)
            d36 = self.engine.score_weibi_anomaly(klines, quote_data=quote_data)  # D36: 委比异动(3分)
            
            # V13.5.20: D37-D46 经典交易理论深度集成
            weekly_klines = daily_to_weekly(klines)
            d37, d38, d39, d40, d41, d42, d43, d44, d45, d46 = score_all_new_dimensions(klines, weekly_klines)
            
            dimensions.extend([d11, d12, d13, d14, d15, d16, d17, d18, d19, d20, d21, d25, d26, d27, d28, d29, d30, d31, d32, d33, d34, d35, d36, d37, d38, d39, d40, d41, d42, d43, d44, d45, d46])
            total_score = sum(d.actual_score for d in dimensions)

            # V13.5.18: tdx_indicator_select基本面质量加成
            # 高ROE+低PE+高增长 = 基本面强支撑, 反转概率更高
            if fundamental_data and fundamental_data.get('data_source') == 'tdx_indicator_select':
                quality = fundamental_data.get('quality_score', 5.0)
                valuation = fundamental_data.get('valuation_level', 'fair')
                roe_val = fundamental_data.get('roe')
                pe_val = fundamental_data.get('pe')

                # 基本面质量>7 → 加分; <3 → 扣分
                if quality > 7.0:
                    fundamental_bonus = 3.0
                    total_score += fundamental_bonus
                    confirm_details.append(f'基本面优秀(ROE={roe_val}%,PE={pe_val},+{fundamental_bonus})')
                elif quality > 5.0:
                    fundamental_bonus = 1.0
                    total_score += fundamental_bonus
                    confirm_details.append(f'基本面良好(ROE={roe_val}%,+{fundamental_bonus})')
                elif quality < 3.0:
                    fundamental_penalty = -2.0
                    total_score += fundamental_penalty
                    confirm_details.append(f'基本面较弱(ROE={roe_val}%,{fundamental_penalty})')

                # 低估值为反转提供安全边际
                if valuation == 'undervalued':
                    total_score += 1.0
                    confirm_details.append('低估值安全边际(+1)')
                elif valuation == 'expensive':
                    total_score -= 1.0
                    confirm_details.append('高估值风险(-1)')

            # V2.6: 确认质量加权 (替代统一+3)
            # 高质量确认(底部抬高/缩量/下跌减速)权重更高
            HIGH_QUALITY = {'底部抬高', '3日未创新低', '缩量', '下跌减速'}
            MED_QUALITY = {'阳线', '大阳线', '适度企稳', '锤子线', '十字星', '前期缩量'}
            confirm_bonus = 0
            for cd in confirm_details:
                # 匹配确认名称（取括号前的关键词）
                cd_key = cd.split('(')[0] if '(' in cd else cd
                if cd_key in HIGH_QUALITY:
                    confirm_bonus += 5
                elif cd_key in MED_QUALITY:
                    confirm_bonus += 3
                else:
                    confirm_bonus += 2
            total_score += confirm_bonus

            # V2.6: 恐慌见底加分
            if cap_bonus > 0:
                total_score += cap_bonus
                confirm_details.append(f'恐慌见底(+{cap_bonus})')

            # V2.6: V3警告减分
            if override_info.get('v3_warning'):
                total_score -= 5
                confirm_details.append('连续新低(-5)')

            # ══ V3.2: SECTOR_CRASH 智能降权 ══
            # 当全市场>75%股票下跌时，M71的超跌维度(D8等)会异常放大分数
            # 这些"极端超跌放大器"信号不是真正的反转，需要提升阈值过滤
            # V13.5.15: D29洗盘豁免 — D29≥6分(洗盘确认)时，SECTOR_CRASH不惩罚!
            #   因为洗盘特征(底部抬高+巨量砸盘+量缩止跌)不受大盘下跌影响

            if market_state:
                decline_ratio = market_state.get('decline_ratio', 0)
                sector_crash = market_state.get('sector_crash', False)

            # V13.5.15: D29洗盘豁免检测
            washout_exempt = d29.actual_score >= 6.0
            
            if sector_crash:
                if washout_exempt:
                    crash_warnings.append(
                        f'🛡️ D29洗盘豁免! D29={d29.actual_score:.0f}/10: '
                        f'SECTOR_CRASH惩罚取消, 洗盘≠真崩盘'
                    )
                else:
                    crash_warnings.append(
                        f'⚠️ SECTOR_CRASH模式(全市场{decline_ratio*100:.0f}%下跌): '
                        f'M71信号可能为"极端超跌放大器", 非真正反转'
                    )

                if not washout_exempt:
                    # 惩罚: 对超跌维度D8打折(崩盘日超跌是常态, 非反转信号)
                    d8_original = d8.actual_score
                    d8 = DimensionScore('超跌幅度(CRASH打折)', d8.max_score,
                        d8_original * 0.3,  # 崩盘日D8降至30%
                        {'original': d8_original, 'crash_discount': 0.3},
                        d8.detail + '[崩盘日打折70%]', False)
                    # 重算维度列表中的D8
                    for i, dim in enumerate(dimensions):
                        if '超跌' in dim.name:
                            dimensions[i] = d8
                            break
                total_score = sum(d.actual_score for d in dimensions)

                if not washout_exempt:
                    # 确认分减半（崩盘日普跌, 技术确认信号弱化）— 洗盘豁免
                    confirm_bonus = int(confirm_bonus * 0.5)
                    total_score += confirm_bonus

                    # 恐慌见底分打折
                    if cap_bonus > 0:
                        cap_bonus = int(cap_bonus * 0.5)
                        total_score += cap_bonus
                        if '恐慌见底' in str(confirm_details):
                            confirm_details = [
                                cd.replace('恐慌见底(+', '恐慌见底/崩盘日(+)') if '恐慌见底' in cd else cd
                                for cd in confirm_details
                            ]
                else:
                    total_score += confirm_bonus
                    if cap_bonus > 0:
                        total_score += cap_bonus

            # ══ V13.5.19: 五确认体系 (D29+D31+D32+D33+D34) ══
            # 四确认升级为五确认: 新增D34拆单识别
            # 五维度交叉确认 = 历史最强买入信号
            five_confirm_count = 0
            five_confirm_details = []
            if d29.actual_score >= 6.0:
                five_confirm_count += 1
                five_confirm_details.append(f'D29洗盘={d29.actual_score:.0f}')
            if d31.actual_score >= 6.0:
                five_confirm_count += 1
                five_confirm_details.append(f'D31主力={d31.actual_score:.0f}')
            if d32.actual_score >= 5.0:
                five_confirm_count += 1
                five_confirm_details.append(f'D32DDX={d32.actual_score:.0f}')
            if d33.actual_score >= 2.0:
                five_confirm_count += 1
                five_confirm_details.append(f'D33外盘={d33.actual_score:.0f}')
            if d34.actual_score >= 3.0:
                five_confirm_count += 1
                five_confirm_details.append(f'D34拆单={d34.actual_score:.0f}')

            # 五确认全通过 → STRONG_BUY加成
            if five_confirm_count >= 5:
                total_score += 8.0  # 五确认满分加成
                confirm_details.append(f'🏆五确认全通过! {"+".join(five_confirm_details)} (+8)')
            elif five_confirm_count >= 4:
                total_score += 4.0  # 四确认加成
                confirm_details.append(f'✅四确认通过! {"+".join(five_confirm_details)} (+4)')
            elif five_confirm_count >= 3:
                total_score += 2.0  # 三确认加成
                confirm_details.append(f'三确认通过! {"+".join(five_confirm_details)} (+2)')

            # ── 等级判定 V2.8/V3.2 ──
            # V2.8: D5 < 4.0 时不退出kline_only模式 (弱资金流信号不足以激活完整数据模式)
            kline_only = (d3.actual_score == 0 and d4.actual_score == 0 and
                          d5.actual_score < 4.0 and d7.actual_score < 4.0)

            # V3.2: SECTOR_CRASH模式下阈值提升
            # V13.5.15: D29洗盘豁免 — 洗盘确认时不提升阈值
            # V4.0: 46维度阈值调整
            if sector_crash and not washout_exempt:
                STRONG_KLINE = 75    # 原50→75
                REVERSAL_KLINE = 65  # 原45→65
                WATCH_KLINE = 40     # 原25→40
                STRONG_FULL = 100    # 原70→100
                REVERSAL_FULL = 70   # 原45→70
                WATCH_FULL = 45      # 原30→45
            else:
                STRONG_KLINE = 62
                REVERSAL_KLINE = 55
                WATCH_KLINE = 32
                STRONG_FULL = 85
                REVERSAL_FULL = 58
                WATCH_FULL = 38

            has_yang = self._is_bullish_kline(klines)
            has_hammer = self._is_hammer(klines)
            is_capitulation = override_info.get('capitulation', False)

            if kline_only:
                # V2.7: 仅K线模式 — 双轨REVERSAL阈值 + 质量门控
                # 高分+少确认 OR 中分+多确认 都可触发REVERSAL
                # V2.7新增: 质量门控(GATE) — REVERSAL需≥1高质量确认OR CRO
                HIGH_QUALITY_SET = {'底部抬高', '3日未创新低', '缩量', '下跌减速'}
                has_high_quality = any(
                    cd.split('(')[0] in HIGH_QUALITY_SET if '(' in cd else cd in HIGH_QUALITY_SET
                    for cd in confirm_details
                )
                # V2.7: D14均线趋势和D17量价趋势作为额外门控
                ma_trend_ok = d14.actual_score >= 3.0  # 均线趋势不是死猫跳区
                vol_trend_ok = d17.actual_score >= 2.0  # 量价趋势不是放量下跌

                if total_score >= STRONG_KLINE and confirm_count >= 3 and has_yang:
                    grade = ReversalGrade.STRONG_REVERSAL
                elif total_score >= REVERSAL_KLINE and confirm_count >= 1:
                    # V2.7质量门控: 高分路径需高质量确认OR CRO OR 均线+量价双OK
                    if has_high_quality or is_capitulation or (ma_trend_ok and vol_trend_ok):
                        grade = ReversalGrade.REVERSAL
                    else:
                        grade = ReversalGrade.WATCH  # 降级: 缺乏高质量确认
                elif total_score >= 35 and confirm_count >= 2:
                    # V2.7质量门控: 中分路径需高质量确认OR CRO
                    if has_high_quality or is_capitulation:
                        grade = ReversalGrade.REVERSAL
                    else:
                        grade = ReversalGrade.WATCH  # 降级: 缺乏高质量确认
                elif total_score >= WATCH_KLINE and confirm_count >= 1:
                    grade = ReversalGrade.WATCH
                else:
                    grade = ReversalGrade.NO_SIGNAL
            else:
                # V3.2: 完整数据模式 (支持SECTOR_CRASH提档)
                if total_score >= STRONG_FULL and confirm_count >= 3:
                    grade = ReversalGrade.STRONG_REVERSAL
                elif total_score >= REVERSAL_FULL and confirm_count >= 2:
                    grade = ReversalGrade.REVERSAL
                elif total_score >= WATCH_FULL and confirm_count >= 1:
                    grade = ReversalGrade.WATCH
                else:
                    grade = ReversalGrade.NO_SIGNAL

        # ── T+1价格预测 ──
        today_close = klines[-1].close if klines else 0
        t1_low_pct, t1_mid_pct, t1_high_pct = self._get_t1_range(total_score)

        t1_low = today_close * (1 + t1_low_pct / 100)
        t1_mid = today_close * (1 + t1_mid_pct / 100)
        t1_high = today_close * (1 + t1_high_pct / 100)
        t1_upside = t1_mid_pct

        # ── 概率预测 ──
        t1_prob, t3_prob, t5_prob, t7_prob = self._get_probabilities(total_score)

        # ── 模式匹配 ──
        pattern, similarity = self.engine.match_historical_pattern(dimensions, klines)

        # ── 操作建议 ──
        action, stop_loss, target, position = self._generate_action(
            grade, total_score, today_close, klines, similarity
        )

        # ── 风险提示 ──
        risks = self._generate_risks(dimensions, klines, lhb_data)
        # V3.2: 附加SECTOR_CRASH警告
        if crash_warnings:
            risks.extend(crash_warnings)

        # ── 置信度 ──
        confidence = self._calc_confidence(dimensions, similarity, len(klines))

        return ReversalPrediction(
            code=code, name=name, date=date_str,
            dimensions=dimensions,
            total_score=round(total_score, 1),
            grade=grade,
            t1_price_low=round(t1_low, 2),
            t1_price_mid=round(t1_mid, 2),
            t1_price_high=round(t1_high, 2),
            t1_upside_pct=round(t1_upside, 2),
            t1_up_prob=round(t1_prob, 4),
            trend_3d_prob=round(t3_prob, 4),
            trend_5d_prob=round(t5_prob, 4),
            trend_7d_prob=round(t7_prob, 4),
            action=action,
            stop_loss=round(stop_loss, 2),
            target_price=round(target, 2),
            position_size=position,
            risk_warnings=risks,
            confidence=round(confidence, 4),
            pattern_match=pattern,
            similarity=similarity,
            v25_veto={'vetoed': vetoed, 'reasons': veto_reasons,
                      'capitulation': cap_info.get('capitulation', False),
                      'capitulation_bonus': cap_info.get('bonus', 0)},
            v25_confirm={'count': confirm_count, 'details': confirm_details},
            five_confirm_count=five_confirm_count
        )

    def _get_t1_range(self, score: float) -> Tuple[float, float, float]:
        """获取T+1波动幅度预测"""
        for (lo, hi), (low, mid, high) in self.SCORE_TO_T1_RANGE.items():
            if lo <= score < hi:
                return low, mid, high
        return -4.0, -1.0, 2.0

    def _get_probabilities(self, score: float) -> Tuple[float, float, float, float]:
        """获取概率预测"""
        for (lo, hi), (p1, p3, p5, p7) in self.SCORE_TO_UP_PROB.items():
            if lo <= score < hi:
                return p1, p3, p5, p7
        return 0.40, 0.15, 0.10, 0.05

    # ═══════════════════════════════════════════════════════════
    # V2.5: 硬否决制 + 多重确认制
    # ═══════════════════════════════════════════════════════════

    def _evaluate_v25_vetos(self, klines: List[KlineBar], d8: 'DimensionScore') -> Tuple[bool, List[str], Dict]:
        """
        V2.6 硬否决检查 — 检测恐慌抛售特征，带恐慌见底覆盖(CRO)
        回测V2.5: 负向否决率33.3%, 正向误杀率21.4% → V2.6: CRO拯救4个正向样本
        返回: (vetoed, reasons, override_info)
        """
        if len(klines) < 8:
            return False, [], {}

        vetos = []
        veto_types = []  # Track which veto triggered for CRO
        last = klines[-1]
        o, h, l, c, vol = last.open, last.high, last.low, last.close, last.volume

        # 量比 (5日均量法)
        vols_5d = [k.volume for k in klines[-6:-1]] if len(klines) >= 6 else [k.volume for k in klines[:-1]]
        avg_5d_vol = sum(vols_5d) / len(vols_5d) if vols_5d else 1
        vol_ratio = vol / avg_5d_vol if avg_5d_vol > 0 else 1.0

        # 实体涨幅
        body_pct = (c - o) / o * 100 if o > 0 else 0

        # 超跌数据
        d8_raw = d8.raw_value if d8.raw_value else {}
        d5_decline = d8_raw.get('decline_5d', 0)
        d10_decline = d8_raw.get('decline_10d', 0)
        decel = d5_decline / d10_decline if d10_decline > 0 else 1.0

        # 收盘价 vs 5日均价
        closes_5d = [k.close for k in klines[-6:-1]] if len(klines) >= 6 else [k.close for k in klines[:-1]]
        avg_5d_price = sum(closes_5d) / len(closes_5d) if closes_5d else c
        close_vs_5d = (c - avg_5d_price) / avg_5d_price * 100 if avg_5d_price > 0 else 0

        # V1: 极端恐慌抛售 (大阴线<-7% + 放量>2.0)
        v1_triggered = body_pct < -7 and vol_ratio > 2.0
        if v1_triggered:
            vetos.append(f'V1:极端恐慌(阴线{body_pct:.1f}%+量比{vol_ratio:.2f})')
            veto_types.append('V1')

        # V2: 下跌剧烈加速 (decel>=1.5 AND 5日跌>10%)
        v2_triggered = decel >= 1.5 and d5_decline > 10
        if v2_triggered:
            vetos.append(f'V2:下跌加速(5日/10日={decel:.2f},5日跌{d5_decline:.1f}%)')
            veto_types.append('V2')

        # V3: 连续5日创新低 — V2.6软化: 不再硬否决，改为减分标记
        v3_triggered = False
        if len(klines) >= 6:
            consec_nl = all(klines[i].low < klines[i-1].low for i in range(-5, 0))
            if consec_nl:
                v3_triggered = True
                # V2.6: V3 降级为警告，不再硬否决
                # vetos.append('V3:连续5日创新低(持续下跌)')

        # V4: 疑似崩盘 (5日跌>25% AND 信号日<-5%)
        v4_triggered = d5_decline > 25 and body_pct < -5
        if v4_triggered:
            vetos.append(f'V4:疑似崩盘(5日跌{d5_decline:.1f}%+信号日{body_pct:.1f}%)')
            veto_types.append('V4')

        # V5: 死猫跳 (close_vs_5d > 12% AND 10日跌>15%)
        v5_triggered = close_vs_5d > 12 and d10_decline > 15
        if v5_triggered:
            vetos.append(f'V5:死猫跳(超5日线{close_vs_5d:.1f}%+10日跌{d10_decline:.1f}%)')
            veto_types.append('V5')

        # ══ V2.6: 恐慌见底覆盖 (CRO) ══
        # 当V1/V4触发(恐慌抛售) 且 10日跌>20%(深度超跌) → 不否决，转为恐慌见底信号
        # 回测验证: 4个正向样本(惠丰钻石/信濠光电/华新建材/欢乐家)被V1/V4误杀，T+1涨6.86%~12.28%
        override_info = {}
        is_capitulation = ('V1' in veto_types or 'V4' in veto_types) and d10_decline > 20

        if is_capitulation:
            # 恐慌见底覆盖: 移除V1/V4否决
            vetos = [v for v in vetos if not (v.startswith('V1:') or v.startswith('V4:'))]
            veto_types = [t for t in veto_types if t not in ('V1', 'V4')]
            override_info = {
                'capitulation': True,
                'd10_decline': d10_decline,
                'd5_decline': d5_decline,
                'body_pct': body_pct,
                'vol_ratio': vol_ratio,
                'bonus': 15  # 恐慌见底加分
            }

        # V3触发且无其他否决 → 不否决但标记
        if v3_triggered and not veto_types:
            override_info['v3_warning'] = True

        # 仅剩余V2/V5否决时才真正否决
        return len(vetos) > 0, vetos, override_info

    def _evaluate_v25_confirms(self, klines: List[KlineBar], d8: 'DimensionScore',
                                d9: 'DimensionScore') -> Tuple[int, List[str]]:
        """
        V2.5 多重确认检查 — REVERSAL需要≥3个确认
        回测: 命中率71.4% (5/7)
        """
        if len(klines) < 8:
            return 0, []

        confirms = []
        last = klines[-1]
        o, h, l, c = last.open, last.high, last.low, last.close

        body_pct = (c - o) / o * 100 if o > 0 else 0
        is_yang = c > o

        # C1: 信号日阳线
        if is_yang:
            confirms.append(f'阳线({body_pct:.1f}%)')

        # C2: 下跌减速
        d8_raw = d8.raw_value if d8.raw_value else {}
        d5 = d8_raw.get('decline_5d', 0)
        d10 = d8_raw.get('decline_10d', 0)
        decel = d5 / d10 if d10 > 0 else 1.0
        if decel < 0.5:
            confirms.append(f'下跌减速({decel:.2f})')

        # C3: 信号日缩量
        vols_5d = [k.volume for k in klines[-6:-1]] if len(klines) >= 6 else [k.volume for k in klines[:-1]]
        avg_5d_vol = sum(vols_5d) / len(vols_5d) if vols_5d else 1
        vol_ratio = last.volume / avg_5d_vol if avg_5d_vol > 0 else 1.0
        if vol_ratio < 0.8:
            confirms.append(f'缩量({vol_ratio:.2f})')

        # C4: 适度企稳 (收盘>5日均价, 但不过度)
        closes_5d = [k.close for k in klines[-6:-1]] if len(klines) >= 6 else [k.close for k in klines[:-1]]
        avg_5d_price = sum(closes_5d) / len(closes_5d) if closes_5d else c
        close_vs_5d = (c - avg_5d_price) / avg_5d_price * 100 if avg_5d_price > 0 else 0
        if 0 < close_vs_5d <= 8:
            confirms.append(f'适度企稳(>{close_vs_5d:.1f}%)')

        # C5: 底部抬高 或 近3日未创新低
        recent_3 = klines[-3:] if len(klines) >= 3 else klines
        lows_3 = [k.low for k in recent_3]
        if len(lows_3) >= 3:
            if lows_3[2] > lows_3[1] > lows_3[0]:
                confirms.append('底部抬高')
            elif lows_3[-1] >= min(lows_3[:-1]):
                confirms.append('3日未创新低')

        # C6: 锤子线/十字星
        body = abs(c - o)
        total_range = h - l if h > l else 0.001
        lower_shadow = min(o, c) - l
        body_ratio = body / total_range if total_range > 0 else 0
        if body_ratio < 0.3 and lower_shadow > body * 2:
            confirms.append(f'锤子线({body_ratio:.0%})')
        elif body_ratio < 0.15:
            confirms.append(f'十字星({body_ratio:.0%})')

        # C7: 大阳线
        if body_pct > 5:
            confirms.append(f'大阳线({body_pct:.1f}%)')

        # C8: 前期缩量 (缩量→放量序列)
        if len(klines) >= 7:
            dry_count = 0
            for i in range(-6, -1):
                pv = [klines[j].volume for j in range(max(0, i-5), i)]
                if pv and sum(pv) > 0:
                    if klines[i].volume / (sum(pv)/len(pv)) < 0.8:
                        dry_count += 1
            if dry_count >= 2:
                confirms.append('前期缩量')

        return len(confirms), confirms

    def _is_bullish_kline(self, klines: List[KlineBar]) -> bool:
        """检查信号日K线是否为 bullish (阳线/锤子线/十字星)"""
        if not klines:
            return False
        last = klines[-1]
        o, h, l, c = last.open, last.high, last.low, last.close
        if c > o:
            return True  # 阳线
        body = abs(c - o)
        total_range = h - l if h > l else 0.001
        body_ratio = body / total_range if total_range > 0 else 0
        lower_shadow = min(o, c) - l
        if body_ratio < 0.3 and lower_shadow > body * 2:
            return True  # 锤子线
        if body_ratio < 0.15:
            return True  # 十字星
        return False

    def _is_hammer(self, klines: List[KlineBar]) -> bool:
        """检查信号日是否为锤子线"""
        if not klines:
            return False
        last = klines[-1]
        o, h, l, c = last.open, last.high, last.low, last.close
        body = abs(c - o)
        total_range = h - l if h > l else 0.001
        lower_shadow = min(o, c) - l
        body_ratio = body / total_range if total_range > 0 else 0
        return body_ratio < 0.3 and lower_shadow > body * 2

    def _generate_action(self, grade: ReversalGrade, score: float,
                         close: float, klines: List[KlineBar],
                         similarity: float) -> Tuple[str, float, float, str]:
        """生成操作建议"""
        if grade == ReversalGrade.STRONG_REVERSAL:
            action = 'BUY'
            stop = close * 0.93      # -7%止损
            target = close * 1.15     # +15%目标
            position = '60-80%仓位(分2-3批建仓)'
            if similarity > 80:
                position = '70-90%仓位(高相似度,可加大仓位)'
        elif grade == ReversalGrade.REVERSAL:
            action = 'ACCUMULATE'
            stop = close * 0.92
            target = close * 1.10
            position = '40-60%仓位'
        elif grade == ReversalGrade.WATCH:
            action = 'WATCH'
            stop = close * 0.90
            target = close * 1.05
            position = '0-20%仓位(观察为主)'
        else:
            action = 'AVOID'
            stop = close * 0.88
            target = close * 1.03
            position = '0%仓位'

        return action, stop, target, position

    def _generate_risks(self, dims: List[DimensionScore],
                        klines: List[KlineBar],
                        lhb_data: List[DragonTigerEntry]) -> List[str]:
        """生成风险提示"""
        risks = []

        # 机构卖出风险
        if lhb_data:
            total_sell = sum(e.net_amount for e in lhb_data if e.net_amount < 0)
            if total_sell < -5000e4:
                risks.append(f'机构净卖出{abs(total_sell)/1e4:.0f}万,注意抛压')

        # 量价背离
        if klines and len(klines) >= 2:
            today = klines[-1]
            if today.close < today.open and today.volume_ratio > 1.5:
                risks.append('放量下跌,反转未确认')

        # 评分不足维度
        failed_dims = [d.name for d in dims if not d.passed]
        if failed_dims:
            risks.append(f'未通过维度: {", ".join(failed_dims)}')

        # 止损提醒
        risks.append('严格止损: 跌破止损价立即清仓,不等反弹')

        return risks

    def _calc_confidence(self, dims: List[DimensionScore],
                         similarity: float, kline_len: int) -> float:
        """计算置信度"""
        # 数据完整度
        data_factor = min(1.0, kline_len / 20.0)

        # 评分通过率
        passed = sum(1 for d in dims if d.passed)
        pass_factor = passed / len(dims) if dims else 0

        # 模式匹配
        pattern_factor = similarity / 100

        # 综合置信度
        confidence = (data_factor * 0.3 + pass_factor * 0.4 + pattern_factor * 0.3)
        return confidence


# ═══════════════════════════════════════════════════════════
# SECTION 4: TDX数据适配器
# ═══════════════════════════════════════════════════════════

class TDXDataAdapter:
    """TDX MCP数据适配器 — 将TDX返回数据转为引擎所需格式"""

    @staticmethod
    def parse_kline(tdx_kline_data: Dict) -> List[KlineBar]:
        """解析TDX K线数据"""
        bars = []
        if not tdx_kline_data:
            return bars

        # 兼容多种TDX返回格式
        klines = tdx_kline_data if isinstance(tdx_kline_data, list) else tdx_kline_data.get('klines', [])

        prev_vol = 0
        for i, item in enumerate(klines):
            if isinstance(item, dict):
                bar = KlineBar(
                    date=item.get('date', ''),
                    open=float(item.get('open', 0)),
                    high=float(item.get('high', 0)),
                    low=float(item.get('low', 0)),
                    close=float(item.get('close', 0)),
                    volume=float(item.get('volume', 0)),
                    amount=float(item.get('amount', 0)),
                    chg_pct=float(item.get('chg_pct', 0)),
                    turnover=float(item.get('hsl', 0)),
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 6:
                bar = KlineBar(
                    date=str(item[0]),
                    open=float(item[1]),
                    high=float(item[2]),
                    low=float(item[3]),
                    close=float(item[4]),
                    volume=float(item[5]),
                    amount=float(item[6]) if len(item) > 6 else 0,
                    chg_pct=float(item[7]) if len(item) > 7 else 0,
                    turnover=float(item[8]) if len(item) > 8 else 0,
                )
            else:
                continue

            # 计算量比
            if prev_vol > 0:
                bar.volume_ratio = bar.volume / prev_vol if prev_vol > 0 else 1.0
            else:
                bar.volume_ratio = 1.0

            prev_vol = bar.volume
            bars.append(bar)

        # 用5日均量修正量比
        if len(bars) >= 5:
            for i in range(4, len(bars)):
                avg_vol = sum(bars[j].volume for j in range(i-4, i+1)) / 5
                if avg_vol > 0:
                    bars[i].volume_ratio = bars[i].volume / avg_vol

        return bars

    @staticmethod
    def parse_lhb(tdx_lhb_data: Dict) -> List[DragonTigerEntry]:
        """解析龙虎榜数据 V3.0 — 兼容TDX API表格格式 + 传统flat格式

        TDX API格式: {"tables": [
            {"name": "summary", "rows": [...]},
            {"name": "details", "rows": [{"营业部(席位)名称": "...", "买入金额(元)": ..., "净额": ...}]},
            {"name": "seat_profiles", "rows": [{"营业部(席位)名称": "...", "营业部标签名称": "量化基金"}]}
        ]}
        传统格式: {"data": [{date, seat_name, seat_type, buy_amount, ...}]}
        """
        entries = []
        if not tdx_lhb_data:
            return entries

        # ── V3.0: 处理TDX API表格格式 ──
        tables = tdx_lhb_data.get('tables', [])
        if tables:
            # Step 1: 解析席位标签映射 (seat_profiles)
            seat_labels = {}
            for tbl in tables:
                if tbl.get('name') == 'seat_profiles':
                    for row in tbl.get('rows', []):
                        seat_name = row.get('营业部(席位)名称', '')
                        label = row.get('营业部标签名称', '')
                        if seat_name and label:
                            seat_labels[seat_name] = label

            # Step 2: 解析详细席位数据 (details)
            for tbl in tables:
                if tbl.get('name') == 'details':
                    for row in tbl.get('rows', []):
                        seat_name = row.get('营业部(席位)名称', '')
                        if not seat_name:
                            continue

                        # 席位类型: 优先从seat_profiles获取, 其次按名称推断
                        seat_type = seat_labels.get(seat_name, '')
                        if not seat_type:
                            if '机构' in seat_name:
                                seat_type = '机构专用'
                            else:
                                seat_type = '营业部'

                        # V3.0: 交易类型B=买入榜, S=卖出榜
                        trade_type = row.get('交易类型', 'B')

                        entries.append(DragonTigerEntry(
                            date='',  # TDX格式无独立日期字段, 由调用方补充
                            seat_name=seat_name,
                            seat_type=seat_type,
                            buy_amount=float(row.get('买入金额(元)', row.get('买入金额', 0)) or 0),
                            sell_amount=float(row.get('卖出金额(元)', row.get('卖出金额', 0)) or 0),
                            net_amount=float(row.get('净额', row.get('净额(元)', 0)) or 0),
                        ))

            return entries

        # ── 传统flat格式兼容 ──
        items = tdx_lhb_data if isinstance(tdx_lhb_data, list) else tdx_lhb_data.get('data', [])

        for item in items:
            if isinstance(item, dict):
                entries.append(DragonTigerEntry(
                    date=item.get('date', ''),
                    seat_name=item.get('seat_name', item.get('name', '')),
                    seat_type=item.get('seat_type', item.get('type', '')),
                    buy_amount=float(item.get('buy_amount', item.get('buy', 0))),
                    sell_amount=float(item.get('sell_amount', item.get('sell', 0))),
                    net_amount=float(item.get('net_amount', item.get('net', 0))),
                ))

        return entries

    @staticmethod
    def parse_capital_flow(tdx_flow_data: Dict) -> CapitalFlow:
        """解析资金流数据 V2.8 — 兼容TDX API表格格式 + 传统flat格式"""
        if not tdx_flow_data:
            return CapitalFlow()

        # ── V2.8: 处理TDX API表格格式 ──
        # TDX返回: {"tables": [{"name": "capital_flow", "rows": [...]}]}
        tables = tdx_flow_data.get('tables', [])
        if tables:
            for tbl in tables:
                if tbl.get('name') == 'capital_flow' or '主力' in str(tbl.get('headers', [])):
                    rows = tbl.get('rows', [])
                    if not rows:
                        continue

                    # 取最近的数据行 (第一行为最新)
                    latest = rows[0] if rows else {}
                    # 累计5日/10日/60日主力净额
                    main_5d = sum(float(r.get('主力净额金额(元)', 0)) for r in rows[:5])
                    main_10d = sum(float(r.get('主力净额金额(元)', 0)) for r in rows[:10])
                    main_60d = sum(float(r.get('主力净额金额(元)', 0)) for r in rows[:60])  # V3.1

                    today_main = float(latest.get('主力净额金额(元)', 0))
                    today_pct = float(latest.get('主力净额占比(%)', 0))
                    today_super = float(latest.get('超大单净买入金额(元)', 0))
                    today_large = float(latest.get('大单净买入金额(元)', 0))

                    # 资金流趋势判断
                    prev_mains = [float(r.get('主力净额金额(元)', 0)) for r in rows[1:6]]
                    positive_days = sum(1 for v in prev_mains if v > 0)
                    negative_days = sum(1 for v in prev_mains if v < 0)

                    flow_trend = ''
                    flow_reversal = False
                    if today_main > 0 and positive_days >= 3:
                        flow_trend = '持续流入'
                    elif today_main > 0 and negative_days >= 3:
                        flow_trend = '转正'
                        flow_reversal = True
                    elif today_main < 0 and negative_days >= 3:
                        flow_trend = '持续流出'
                    else:
                        flow_trend = '振荡'

                    return CapitalFlow(
                        ddx=0.0,
                        ddy=0.0,
                        ddf=today_pct,
                        mainlx=today_main,
                        main_10d=main_10d,
                        super_large_net=today_super,
                        main_5d=main_5d,
                        main_60d=main_60d,  # V3.1
                        main_pct=today_pct,
                        large_net=today_large,
                        flow_trend=flow_trend,
                        flow_reversal=flow_reversal,
                    )

        # ── 传统flat格式兼容 ──
        return CapitalFlow(
            ddx=float(tdx_flow_data.get('ddx', 0)),
            ddy=float(tdx_flow_data.get('ddy', 0)),
            ddf=float(tdx_flow_data.get('ddf', 0)),
            mainlx=float(tdx_flow_data.get('mainlx', tdx_flow_data.get('main', 0))),
            main_10d=float(tdx_flow_data.get('main_10d', 0)),
            super_large_net=float(tdx_flow_data.get('super_large_net', 0)),
        )


# ═══════════════════════════════════════════════════════════
# SECTION 5: 持仓扫描器 — 对所有持仓进行反转预测
# ═══════════════════════════════════════════════════════════

class HoldingsReversalScanner:
    """持仓反转扫描器"""

    def __init__(self, db_path: str = 'data/holy_grail.db'):
        self.db_path = db_path
        self.predictor = ReversalPredictor()
        self.adapter = TDXDataAdapter()

    def scan_holdings(self, holdings_data: List[Dict]) -> List[ReversalPrediction]:
        """
        扫描所有持仓的反转信号
        holdings_data: [{code, name, klines, lhb, flow, sector}, ...]
        """
        results = []
        for holding in holdings_data:
            klines = self.adapter.parse_kline(holding.get('klines'))
            lhb = self.adapter.parse_lhb(holding.get('lhb'))
            flow = self.adapter.parse_capital_flow(holding.get('flow'))
            sector = holding.get('sector')

            pred = self.predictor.predict(
                code=holding['code'],
                name=holding['name'],
                klines=klines,
                lhb_data=lhb,
                capital_flow=flow,
                sector_data=sector
            )
            results.append(pred)

        return results

    def save_to_db(self, predictions: List[ReversalPrediction]):
        """保存预测结果到数据库"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS m71_reversal_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT,
                date TEXT NOT NULL,
                total_score REAL,
                grade TEXT,
                t1_price_low REAL,
                t1_price_mid REAL,
                t1_price_high REAL,
                t1_upside_pct REAL,
                t1_up_prob REAL,
                trend_3d_prob REAL,
                trend_5d_prob REAL,
                trend_7d_prob REAL,
                action TEXT,
                stop_loss REAL,
                target_price REAL,
                position_size TEXT,
                confidence REAL,
                pattern_match TEXT,
                similarity REAL,
                dimensions_json TEXT,
                risk_warnings_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        for pred in predictions:
            cursor.execute('''
                INSERT INTO m71_reversal_predictions
                (code, name, date, total_score, grade, t1_price_low, t1_price_mid,
                 t1_price_high, t1_upside_pct, t1_up_prob, trend_3d_prob, trend_5d_prob,
                 trend_7d_prob, action, stop_loss, target_price, position_size,
                 confidence, pattern_match, similarity, dimensions_json, risk_warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                pred.code, pred.name, pred.date, pred.total_score,
                pred.grade.value, pred.t1_price_low, pred.t1_price_mid,
                pred.t1_price_high, pred.t1_upside_pct, pred.t1_up_prob,
                pred.trend_3d_prob, pred.trend_5d_prob, pred.trend_7d_prob,
                pred.action, pred.stop_loss, pred.target_price, pred.position_size,
                pred.confidence, pred.pattern_match, pred.similarity,
                json.dumps([asdict(d) for d in pred.dimensions], ensure_ascii=False),
                json.dumps(pred.risk_warnings, ensure_ascii=False)
            ))

        conn.commit()
        conn.close()
        print(f"[M71] {len(predictions)} 条预测已保存到 {self.db_path}")


# ═══════════════════════════════════════════════════════════
# SECTION 6: 快捷入口 — 手动构建预测
# ═══════════════════════════════════════════════════════════

def predict_reversal(code: str, name: str,
                     klines_raw: List[Dict],
                     lhb_raw=None,
                     flow_raw: Dict = None,
                     sector_raw: Dict = None) -> ReversalPrediction:
    """
    快捷预测入口 — 直接传入原始数据

    klines_raw: [{date, open, high, low, close, volume, ...}, ...]
    lhb_raw: V3.0 支持两种格式:
        - TDX表格格式: {"tables": [{"name": "details", ...}, {"name": "seat_profiles", ...}]}
        - 传统list格式: [{date, seat_name, seat_type, buy_amount, ...}]
    flow_raw: {ddx, ddy, ddf, mainlx, main_10d, super_large_net} 或 TDX表格格式
    sector_raw: {sector_chg, up_count, total_count}
    """
    predictor = ReversalPredictor()
    adapter = TDXDataAdapter()

    klines = adapter.parse_kline({'klines': klines_raw})

    # V3.0: 兼容TDX表格格式 + 传统list格式
    if lhb_raw:
        if isinstance(lhb_raw, dict) and 'tables' in lhb_raw:
            lhb = adapter.parse_lhb(lhb_raw)
        elif isinstance(lhb_raw, list):
            lhb = adapter.parse_lhb({'data': lhb_raw})
        elif isinstance(lhb_raw, dict):
            lhb = adapter.parse_lhb(lhb_raw)
        else:
            lhb = []
    else:
        lhb = []

    flow = adapter.parse_capital_flow(flow_raw) if flow_raw else CapitalFlow()

    return predictor.predict(code, name, klines, lhb, flow, sector_raw)


def prediction_to_dict(pred: ReversalPrediction) -> Dict:
    """预测结果转字典（用于JSON输出）"""
    return {
        'code': pred.code,
        'name': pred.name,
        'date': pred.date,
        'total_score': pred.total_score,
        'grade': pred.grade.value,
        'dimensions': [asdict(d) for d in pred.dimensions],
        't1_prediction': {
            'price_low': pred.t1_price_low,
            'price_mid': pred.t1_price_mid,
            'price_high': pred.t1_price_high,
            'upside_pct': pred.t1_upside_pct,
            'up_prob': pred.t1_up_prob,
        },
        'trend_prediction': {
            'trend_3d_prob': pred.trend_3d_prob,
            'trend_5d_prob': pred.trend_5d_prob,
            'trend_7d_prob': pred.trend_7d_prob,
        },
        'action': pred.action,
        'stop_loss': pred.stop_loss,
        'target_price': pred.target_price,
        'position_size': pred.position_size,
        'risk_warnings': pred.risk_warnings,
        'confidence': pred.confidence,
        'pattern_match': pred.pattern_match,
        'similarity': pred.similarity,
        'five_confirm_count': pred.five_confirm_count,  # V13.5.19 五确认计数
    }


# ═══════════════════════════════════════════════════════════
# SECTION 7: 自测
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 70)
    print("V13.5 M71 反转预测引擎 — 自测")
    print("=" * 70)

    # 模拟蜀道装备反转前夜数据 (6/25)
    shudao_klines = [
        {'date': '2026-06-19', 'open': 28.5, 'high': 28.8, 'low': 27.5, 'close': 27.8, 'volume': 1000, 'chg_pct': -2.0},
        {'date': '2026-06-20', 'open': 27.5, 'high': 27.8, 'low': 26.5, 'close': 26.8, 'volume': 800, 'chg_pct': -3.6},
        {'date': '2026-06-23', 'open': 26.5, 'high': 26.8, 'low': 25.8, 'close': 26.0, 'volume': 700, 'chg_pct': -3.0},
        {'date': '2026-06-24', 'open': 26.0, 'high': 26.2, 'low': 25.0, 'close': 25.2, 'volume': 500, 'chg_pct': -3.1},
        {'date': '2026-06-25', 'open': 25.0, 'high': 26.5, 'low': 24.8, 'close': 26.2, 'volume': 1200, 'chg_pct': 4.0},
    ]

    shudao_lhb = [
        {'date': '2026-06-25', 'seat_name': '机构专用', 'seat_type': '机构', 'buy_amount': 1.5e8, 'sell_amount': 0.5e8, 'net_amount': 1.0e8},
        {'date': '2026-06-25', 'seat_name': '中金公司上海分公司', 'seat_type': '营业部', 'buy_amount': 5e7, 'sell_amount': 1e7, 'net_amount': 4e7},
    ]

    shudao_flow = {'ddx': 0.8, 'ddy': 0.5, 'ddf': 0.65, 'mainlx': 5e7, 'main_10d': 1.5e8, 'super_large_net': 3e7}
    shudao_sector = {'sector_chg': 1.5, 'up_count': 8, 'total_count': 10}

    pred = predict_reversal('300540', '蜀道装备', shudao_klines, shudao_lhb, shudao_flow, shudao_sector)

    print(f"\n{'='*50}")
    print(f"股票: {pred.name}({pred.code})")
    print(f"日期: {pred.date}")
    print(f"总分: {pred.total_score}/100 ({pred.grade.value})")
    print(f"{'='*50}")

    for d in pred.dimensions:
        status = 'PASS' if d.passed else 'FAIL'
        print(f"  {d.name:12s} | {d.actual_score:5.1f}/{d.max_score:.0f} | {status} | {d.detail}")

    print(f"\n--- T+1预测 ---")
    print(f"  价格区间: {pred.t1_price_low} ~ {pred.t1_price_mid} ~ {pred.t1_price_high}")
    print(f"  预期涨幅: {pred.t1_upside_pct}%")
    print(f"  上涨概率: {pred.t1_up_prob:.1%}")

    print(f"\n--- 趋势预测 ---")
    print(f"  3日连涨概率: {pred.trend_3d_prob:.1%}")
    print(f"  5日连涨概率: {pred.trend_5d_prob:.1%}")
    print(f"  7日连涨概率: {pred.trend_7d_prob:.1%}")

    print(f"\n--- 操作建议 ---")
    print(f"  动作: {pred.action}")
    print(f"  止损价: {pred.stop_loss}")
    print(f"  目标价: {pred.target_price}")
    print(f"  仓位: {pred.position_size}")

    print(f"\n--- 模式匹配 ---")
    print(f"  匹配模式: {pred.pattern_match}")
    print(f"  相似度: {pred.similarity}%")
    print(f"  置信度: {pred.confidence:.1%}")

    print(f"\n--- 风险提示 ---")
    for r in pred.risk_warnings:
        print(f"  - {r}")

    print(f"\n{'='*70}")
    print("M71 反转预测引擎自测完成")
    print(f"{'='*70}")
