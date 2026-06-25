#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.0 尾盘终极选股引擎 — TailMarket Ultimate                       ║
║  =============================================                       ║
║  融合 T-1尾盘策略 × 20+选股战法 × V13.0贝叶斯引擎                   ║
║                                                                      ║
║  战略目标：涨停命中率 99% | 盈亏比 10.0 | 诱多踩雷率 0.1%           ║
║                                                                      ║
║  知识来源：                                                          ║
║  ├── t1_tail.md: T-1尾盘潜伏·舆情先行技术确认                        ║
║  ├── select_stock_kb.md: 20+战法(老鸭头/2560/擒龙/二板定龙头…)      ║
║  ├── V13_0_M46_BayesianEngine.py: 贝叶斯概率引擎                     ║
║  ├── V13_0_M51_IntentInference.py: 主力意图推断(噪音过滤)            ║
║  ├── V13_0_7WeightFusion.py: 7权重融合V2                             ║
║  └── V13_0_M54_PositionEngine.py: 仓位决策引擎                       ║
║                                                                      ║
║  架构：四层递进筛选 → 十二维共振确认 → 贝叶斯终极评分                ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import math
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 终极目标KPI配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class UltimateKPI:
    """终极KPI——这是引擎存在的唯一理由"""
    TARGET_HIT_RATE: float = 0.99          # 涨停命中率 99%
    TARGET_PLR: float = 10.0               # 盈亏比 10.0
    TARGET_TRAP_RATE: float = 0.001        # 诱多踩雷率 0.1%

    # 各模块对目标的贡献分配
    CONTRIBUTION = {
        'pattern_detection': 0.30,          # 技术形态检测贡献30%
        't1_sentiment_screening': 0.25,     # T-1舆情筛选贡献25%
        'trap_detection': 0.20,             # 诱多排雷贡献20%
        'bayesian_engine': 0.15,            # 贝叶斯概率引擎贡献15%
        'position_engine': 0.10,            # 仓位决策贡献10%
    }


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 形态检测核心引擎 (PatternDetector)
# ═══════════════════════════════════════════════════════════════

class SignalStrength(Enum):
    """信号强度分级"""
    ULTRA_STRONG = ('超强', 0.90, 0.35)     # 几乎确定信号
    STRONG = ('强', 0.75, 0.25)
    MODERATE = ('中', 0.55, 0.18)
    WEAK = ('弱', 0.35, 0.10)
    NOISE = ('噪音', 0.00, 0.00)


@dataclass
class PatternSignal:
    """单个形态检测信号"""
    pattern_name: str           # 形态名称
    detected: bool              # 是否检测到
    strength: float             # 信号强度 0~1
    confidence: str             # 置信度
    score: float                # 归一化评分
    details: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class PatternDetector:
    """
    20+战法形态检测器
    将 select_stock_kb.md 中的所有战法转化为可计算信号
    """

    def __init__(self):
        self.detection_log: List[dict] = []

    # ─────────────────────────────────────────────────
    # 1.1 老鸭头形态检测 (Old Duck Head)
    # ─────────────────────────────────────────────────

    def detect_old_duck_head(
        self,
        ma5: List[float], ma10: List[float], ma60: List[float],
        volumes: List[float], prices: List[float],
        macd_dif: List[float] = None, macd_dea: List[float] = None,
    ) -> PatternSignal:
        """
        老鸭头形态检测

        条件：
        1. 60MA向上（中期趋势向好）
        2. 5/10MA放量上穿60MA（鸭颈形成）
        3. 股价回调但未跌破60MA（鸭头形成）
        4. 5/10MA再次金叉向上（鸭嘴形成）
        5. MACD在0轴上方金叉
        6. 回调幅度15%-25%，回调时间5-15天（最佳8-12天）
        """
        details = []
        warnings = []
        detected = False
        score = 0.0

        if len(ma60) < 30 or len(ma5) < 20:
            return PatternSignal('老鸭头', False, 0, '噪音', 0, ['数据不足'])

        # 条件1: 60MA向上
        ma60_rising = ma60[-1] > ma60[-10]
        if not ma60_rising:
            return PatternSignal('老鸭头', False, 0, '弱', 0,
                                ['60MA未上行·形态基础不成立'])
        details.append('✅ 60MA上行·中期趋势确认')

        # 条件2: 5/10MA曾上穿60MA（鸭颈）
        crossed_60 = False
        cross_idx = -1
        for i in range(len(ma5) - 1, max(0, len(ma5) - 30), -1):
            if ma5[i] > ma60[i] and ma10[i] > ma60[i]:
                if i > 0 and (ma5[i-1] <= ma60[i-1] or ma10[i-1] <= ma60[i-1]):
                    crossed_60 = True
                    cross_idx = i
                    break
        if not crossed_60:
            return PatternSignal('老鸭头', False, 0, '弱', 0,
                                ['5/10MA未上穿60MA·无鸭颈'])
        details.append(f'✅ 鸭颈形成·5/10MA上穿60MA (T-{len(ma5)-cross_idx})')
        score += 0.15

        # 条件3: 股价回调未破60MA（鸭头）
        post_cross_prices = prices[cross_idx:]
        if len(post_cross_prices) < 5:
            return PatternSignal('老鸭头', False, score, '弱', score,
                                ['鸭颈后数据不足'])

        min_price_after = min(post_cross_prices)
        if min_price_after <= ma60[cross_idx]:
            warnings.append('⚠️ 回调跌破60MA·形态可能走坏')
            score *= 0.5
        else:
            details.append('✅ 回调未破60MA·鸭头完整')
            score += 0.15

        # 条件4: 5/10MA再次金叉（鸭嘴）
        duck_mouth = False
        for i in range(len(ma5) - 1, max(cross_idx + 5, 0), -1):
            if ma5[i] > ma10[i] and i > 0 and ma5[i-1] <= ma10[i-1]:
                duck_mouth = True
                break
        if duck_mouth:
            details.append('✅ 鸭嘴形成·5/10MA二次金叉')
            score += 0.20
        else:
            details.append('⏳ 等待鸭嘴形成·5/10MA二次金叉')

        # 条件5: MACD在0轴上方金叉
        if macd_dif and macd_dea and len(macd_dif) > 5:
            if macd_dif[-1] > 0 and macd_dea[-1] > 0:
                if macd_dif[-1] > macd_dea[-1]:
                    details.append('✅ MACD零轴上方金叉')
                    score += 0.15
                else:
                    details.append('⏳ MACD零轴上方但未金叉')
            else:
                details.append('⏳ MACD尚未升至零轴上方')

        # 条件6: 回调幅度和时间
        if len(post_cross_prices) > 10:
            max_price = max(post_cross_prices[:5])  # 鸭颈附近高点
            pullback = (max_price - min_price_after) / max_price
            pullback_days = post_cross_prices.index(min_price_after)

            if 0.15 <= pullback <= 0.25:
                details.append(f'✅ 回调{pullback:.0%}·黄金区间15-25%')
                score += 0.10
            else:
                details.append(f'⚠️ 回调{pullback:.0%}·偏离黄金区间')

            if 8 <= pullback_days <= 12:
                details.append(f'✅ 回调{pullback_days}天·最佳8-12天')
                score += 0.05

        # 成交量确认：鸭颈放量+鸭头缩量+鸭嘴温和放量
        if len(volumes) > 20:
            vol_before_cross = sum(volumes[max(0, cross_idx-5):cross_idx]) / 5
            vol_during_pullback = sum(volumes[cross_idx:cross_idx+5]) / 5
            vol_recent = sum(volumes[-5:]) / 5

            if vol_before_cross > 0:
                if vol_during_pullback < vol_before_cross * 0.7:
                    details.append('✅ 鸭头缩量·洗盘确认')
                    score += 0.10
                if vol_recent > vol_during_pullback * 1.2:
                    details.append('✅ 鸭嘴温和放量·主力重新建仓')
                    score += 0.10

        detected = score >= 0.50
        confidence = '高' if score >= 0.70 else '中' if score >= 0.50 else '低'

        return PatternSignal('老鸭头', detected,
                            min(score, 0.95), confidence, score,
                            details, warnings)

    # ─────────────────────────────────────────────────
    # 1.2 2560战法检测
    # ─────────────────────────────────────────────────

    def detect_2560(
        self,
        ma25: List[float], vol_ma5: List[float], vol_ma60: List[float],
        prices: List[float], volumes: List[float],
    ) -> PatternSignal:
        """
        2560战法检测

        条件：
        1. 25MA向上或走平
        2. 5日均量线上穿60日均量线（量能金叉）
        3. 做量型：股价回踩25MA + 量能黏合
        4. 缩量型：股价回踩25MA + 5日量能>60日量能 + 单日缩量
        """
        details = []
        score = 0.0

        if len(ma25) < 10 or len(vol_ma5) < 5:
            return PatternSignal('2560战法', False, 0, '噪音', 0, ['数据不足'])

        # 条件1: 25MA趋势
        ma25_trend = (ma25[-1] - ma25[-10]) / ma25[-10] if ma25[-10] > 0 else 0
        if ma25_trend >= -0.01:  # 向上或走平(允许1%以内的走平)
            details.append('✅ 25MA向上/走平·趋势OK')
            score += 0.20
        else:
            return PatternSignal('2560战法', False, 0, '弱', 0,
                                ['25MA下行·2560战法前提不满足'])

        # 条件2: 量能金叉（5日均量 > 60日均量）
        vol_golden_cross = vol_ma5[-1] > vol_ma60[-1]
        vol_just_crossed = (
            vol_ma5[-1] > vol_ma60[-1] and
            len(vol_ma5) > 2 and vol_ma5[-2] <= vol_ma60[-2]
        )

        if vol_golden_cross:
            details.append('✅ 5日量能>60日量能·增量资金入场')
            score += 0.25
            if vol_just_crossed:
                details.append('🔥 量能刚金叉·最佳时机')
                score += 0.10
        else:
            details.append('⚠️ 5日量能<60日量能·量能不足')

        # 条件3: 股价与25MA的关系
        current_price = prices[-1]
        distance_to_ma25 = (current_price - ma25[-1]) / ma25[-1]

        # 做量型：回踩25MA
        if abs(distance_to_ma25) <= 0.03:
            details.append(f'✅ 股价贴近25MA({distance_to_ma25:+.1%})·做量型机会')
            score += 0.20
        elif distance_to_ma25 < -0.05:
            details.append('⚠️ 股价跌破25MA超5%·风险信号')
            score *= 0.6

        # 缩量型：回踩+缩量
        recent_vol = volumes[-1] if volumes else 0
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else recent_vol
        if recent_vol < avg_vol * 0.7 and vol_golden_cross:
            details.append('✅ 缩量回踩+量能在线·黑马机会')
            score += 0.15

        # 排除ST/高位
        if len(prices) > 60:
            year_low = min(prices[-60:])
            if (current_price - year_low) / year_low > 0.50:
                details.append('⚠️ 距一年低点涨幅>50%·谨慎追高')

        detected = score >= 0.45
        confidence = '高' if score >= 0.70 else '中' if score >= 0.45 else '低'

        return PatternSignal('2560战法', detected,
                            min(score, 0.90), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.3 底量超顶量检测
    # ─────────────────────────────────────────────────

    def detect_bottom_volume_exceeds_top(
        self,
        prices: List[float], volumes: List[float],
    ) -> PatternSignal:
        """
        底量超顶量检测

        条件：
        1. 前期高位出现最大成交量（顶量）
        2. 股价下跌见底过程中出现显著放量（底量）
        3. 底量 > 顶量
        4. 股价突破底量对应K线最高价时确认买点
        """
        details = []
        score = 0.0

        if len(prices) < 60 or len(volumes) < 60:
            return PatternSignal('底量超顶量', False, 0, '噪音', 0, ['数据不足(需60日)'])

        # 找顶量（股价高位区间的最大成交量）
        prices_60 = prices[-60:]
        volumes_60 = volumes[-60:]
        high_price_idx = prices_60.index(max(prices_60[:40]))  # 前40天的高点
        top_volume = max(volumes_60[:high_price_idx+1])
        top_volume_idx = volumes_60[:high_price_idx+1].index(top_volume)

        # 找底量（股价下跌后的最大成交量）
        bottom_start = max(high_price_idx, top_volume_idx) + 5
        if bottom_start >= len(volumes_60) - 10:
            return PatternSignal('底量超顶量', False, 0, '低', 0, ['尚未出现底部放量'])

        bottom_volumes = volumes_60[bottom_start:]
        bottom_volume = max(bottom_volumes)
        bottom_volume_idx = bottom_start + bottom_volumes.index(bottom_volume)

        # 判断底量是否超过顶量
        if bottom_volume > top_volume:
            details.append(f'✅ 底量({bottom_volume:.0f})>顶量({top_volume:.0f})·主力低位吸筹')
            score += 0.40

            # 确认：股价突破底量K线最高价
            breakout_price = prices_60[bottom_volume_idx]
            current_price = prices[-1]
            if current_price > breakout_price:
                details.append(f'✅ 突破底量K线高点{breakout_price:.2f}·买点确认')
                score += 0.30
            else:
                details.append(f'⏳ 等待突破{breakout_price:.2f}确认买点')
                score += 0.10
        else:
            return PatternSignal('底量超顶量', False, 0, '低', 0,
                                ['底量未超顶量'])

        # 股价位置确认
        current_price = prices[-1]
        high_price = max(prices_60)
        decline = (high_price - current_price) / high_price
        if decline > 0.30:
            details.append(f'✅ 从高点回落{decline:.0%}·深度调整')
            score += 0.15

        detected = score >= 0.55
        confidence = '高' if score >= 0.75 else '中' if score >= 0.55 else '低'

        return PatternSignal('底量超顶量', detected,
                            min(score, 0.90), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.4 擒龙战法检测
    # ─────────────────────────────────────────────────

    def detect_dragon_catch(
        self,
        ma5: List[float], ma10: List[float], ma20: List[float],
        prices: List[float], volumes: List[float],
        volume_ratio: float = 1.0, turnover_rate: float = 0,
        big_order_net: float = 0,
    ) -> PatternSignal:
        """
        擒龙战法检测（三步走）

        步骤1：趋势筛选（均线多头+突破平台）
        步骤2：主力确认（资金流向+活跃度）
        步骤3：量能起爆（量比>1.5 + 换手率3-20%）
        """
        details = []
        score = 0.0

        if len(ma5) < 20:
            return PatternSignal('擒龙战法', False, 0, '噪音', 0, ['数据不足'])

        # 步骤1: 趋势筛选
        # 均线多头排列
        if ma5[-1] > ma10[-1] > ma20[-1]:
            details.append('✅ 均线多头排列(5>10>20)')
            score += 0.15
        elif ma5[-1] > ma20[-1]:
            details.append('⚠️ 部分均线多头(5>20但10日未确认)')
            score += 0.08

        # 突破平台（近10日横盘后突破）
        if len(prices) >= 15:
            recent_10 = prices[-15:-5]
            price_range_10 = (max(recent_10) - min(recent_10)) / min(recent_10)
            if price_range_10 < 0.05 and prices[-1] > max(recent_10):
                details.append('✅ 突破10日横盘平台')
                score += 0.20

        # 步骤2: 主力确认
        # 近3日大单净流入
        if big_order_net > 0:
            details.append(f'✅ 主力净流入·大单占比{big_order_net:.0%}')
            score += 0.15

        # 步骤3: 量能起爆
        if volume_ratio > 1.5:
            details.append(f'✅ 量比{volume_ratio:.1f}·资金加速流入')
            bonus = min((volume_ratio - 1.0) * 0.1, 0.15)
            score += 0.10 + bonus

        if 0.03 <= turnover_rate <= 0.20:
            details.append(f'✅ 换手率{turnover_rate:.1%}·活跃区间')
            score += 0.10

        # 量价配合
        if len(volumes) >= 10 and len(prices) >= 10:
            up_vol_avg = sum(volumes[-10:][i] for i in range(10) if prices[-10:][i] > prices[-11:][i]) / max(1, sum(1 for i in range(10) if prices[-10:][i] > prices[-11:][i]))
            down_vol_avg = sum(volumes[-10:][i] for i in range(10) if prices[-10:][i] <= prices[-11:][i]) / max(1, sum(1 for i in range(10) if prices[-10:][i] <= prices[-11:][i]))
            if up_vol_avg > down_vol_avg * 1.2:
                details.append('✅ 上涨放量·回调缩量·量价健康')
                score += 0.10

        detected = score >= 0.40
        confidence = '高' if score >= 0.65 else '中' if score >= 0.40 else '低'

        return PatternSignal('擒龙战法', detected,
                            min(score, 0.90), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.5 二板定龙头检测
    # ─────────────────────────────────────────────────

    def detect_two_board_leader(
        self,
        code: str, name: str,
        first_board: dict = None,      # 首板数据
        second_board: dict = None,      # 二板数据
        sector_stocks: List[dict] = None,  # 同板块股票
    ) -> PatternSignal:
        """
        二板定龙头检测

        首板筛选条件：
        - 题材主线，板块同步涨停≥3只，板块涨幅>3%
        - 流通市值30-100亿，股价<25元
        - 首板成交量1.5-3倍均量，换手率8-20%(小盘)/5-15%(中盘)
        - 封板时间10:30前，非尾盘偷袭

        二板确认：
        - 二板放量(较首板放大30-80%)
        - 竞价高开3-7%，竞价量占首板10-20%
        - 板块效应：带动板块上涨
        """
        details = []
        score = 0.0

        # 需要首板数据
        if first_board is None:
            return PatternSignal('二板定龙头', False, 0, '噪音', 0,
                                ['缺少首板数据'])

        # ── 首板条件验证 ──
        fb_details = []

        # 市值
        mcap = first_board.get('market_cap', 0) / 1e8  # 转为亿
        if 30 <= mcap <= 100:
            fb_details.append(f'✅ 流通市值{mcap:.0f}亿·黄金区间30-100亿')
            score += 0.10
        elif mcap < 30:
            fb_details.append(f'⚠️ 流通市值{mcap:.0f}亿·偏小易被量化操控')
        elif mcap <= 200:
            fb_details.append(f'⚠️ 流通市值{mcap:.0f}亿·偏大')
            score += 0.05

        # 股价
        price = first_board.get('price', 0)
        if price <= 25:
            fb_details.append(f'✅ 股价{price:.1f}·低价启动')
            score += 0.05

        # 首板量能
        fb_vol_ratio = first_board.get('volume_ratio', 1.0)
        if 1.5 <= fb_vol_ratio <= 3.0:
            fb_details.append(f'✅ 首板量比{fb_vol_ratio:.1f}·放量适度')
            score += 0.10
        elif fb_vol_ratio > 3.0:
            fb_details.append(f'⚠️ 首板量比{fb_vol_ratio:.1f}·过度放量')

        # 换手率
        fb_turnover = first_board.get('turnover_rate', 0)
        if mcap <= 50:
            turnover_ok = 0.08 <= fb_turnover <= 0.20
        else:
            turnover_ok = 0.05 <= fb_turnover <= 0.15
        if turnover_ok:
            fb_details.append(f'✅ 首板换手{fb_turnover:.0%}·筹码交换充分')
            score += 0.05
        else:
            fb_details.append(f'⚠️ 首板换手{fb_turnover:.0%}·偏离合理区间')

        # 封板时间
        fb_lock_time = first_board.get('lock_time', '14:00')
        if fb_lock_time <= '10:30':
            fb_details.append(f'✅ 封板时间{fb_lock_time}·早盘强势')
            score += 0.10
        elif fb_lock_time >= '14:30':
            fb_details.append(f'⚠️ 尾盘偷袭封板({fb_lock_time})·可疑')
            score -= 0.10

        # 排除一字板
        if first_board.get('is_one_word', False):
            return PatternSignal('二板定龙头', False, 0, '弱', 0,
                                ['首板为一字板·无参与价值'])

        # ── 板块效应 ──
        if sector_stocks:
            sector_limit_up = sum(1 for s in sector_stocks if s.get('is_limit_up', False))
            if sector_limit_up >= 3:
                fb_details.append(f'✅ 板块{sector_limit_up}只涨停·龙头效应')
                score += 0.10

        details.extend(fb_details)

        # ── 二板确认（如有）──
        if second_board:
            sb_details = []
            sb_vol = second_board.get('volume', 0)
            fb_vol = first_board.get('volume', 0)
            if fb_vol > 0:
                vol_change = (sb_vol - fb_vol) / fb_vol
                if 0.30 <= vol_change <= 0.80:
                    sb_details.append(f'✅ 二板放量{vol_change:.0%}·分歧转一致')
                    score += 0.20
                elif vol_change > 0.80:
                    sb_details.append(f'⚠️ 二板过度放量{vol_change:.0%}')

            # 竞价
            open_premium = second_board.get('open_premium', 0)
            if 0.03 <= open_premium <= 0.07:
                sb_details.append(f'✅ 竞价高开{open_premium:.1%}·黄金区间')
                score += 0.10

            details.extend(sb_details)

        detected = score >= 0.40
        confidence = '高' if score >= 0.65 else '中' if score >= 0.40 else '低'

        return PatternSignal('二板定龙头', detected,
                            min(score, 0.95), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.6 主力信号检测
    # ─────────────────────────────────────────────────

    def detect_main_force_signal(
        self,
        prices: List[float], volumes: List[float],
        is_bottom_volume_breakout: bool = False,
        is_shrinking_consolidation: bool = False,
        volume_doubled: bool = False,
        ma5: List[float] = None, ma10: List[float] = None, ma20: List[float] = None,
        against_market: bool = False, bad_news_rise: bool = False,
    ) -> PatternSignal:
        """
        主力信号综合检测

        条件：
        1. 底部放量阴线/阳线（倍量突破）
        2. 缩量不跌后倍量阳线跟进
        3. 量价关系健康（阳线放量阴线缩量）
        4. 缩量突破压力位（主力控盘）
        5. 均线多头排列
        6. 逆势独立行情
        7. 利空不跌反涨
        """
        details = []
        score = 0.0

        # 条件1: 底部放量突破
        if is_bottom_volume_breakout:
            details.append('✅ 底部倍量突破·主力进场信号')
            score += 0.25

        # 条件2: 缩量不跌+倍量跟进
        if is_shrinking_consolidation and volume_doubled:
            details.append('✅ 缩量不跌→倍量跟进·拉升启动')
            score += 0.30
        elif is_shrinking_consolidation:
            details.append('⏳ 缩量不跌·等待倍量确认')
            score += 0.10

        # 条件3: 量价关系
        if len(prices) >= 10 and len(volumes) >= 10:
            up_days = sum(1 for i in range(-10, 0) if prices[i] > prices[i-1])
            down_days = 10 - up_days
            up_vol = sum(volumes[i] for i in range(-10, 0) if prices[i] > prices[i-1])
            down_vol = sum(volumes[i] for i in range(-10, 0) if prices[i] <= prices[i-1])

            if up_days > 0 and down_days > 0:
                avg_up_vol = up_vol / up_days
                avg_down_vol = down_vol / down_days
                if avg_up_vol > avg_down_vol * 1.3:
                    details.append('✅ 阳线放量阴线缩量·量价健康')
                    score += 0.10

        # 条件4: 均线多头
        if ma5 and ma10 and ma20:
            if ma5[-1] > ma10[-1] > ma20[-1]:
                details.append('✅ 均线多头排列(5>10>20)')
                score += 0.10
            elif ma5[-1] > ma20[-1]:
                score += 0.05

        # 条件5: 逆势行情
        if against_market:
            details.append('✅ 逆势抗跌·主力护盘明显')
            score += 0.10

        # 条件6: 利空不跌
        if bad_news_rise:
            details.append('✅ 利空不跌反涨·主力高度控盘')
            score += 0.15

        if score == 0:
            return PatternSignal('主力信号', False, 0, '噪音', 0, ['无明显主力信号'])

        detected = score >= 0.25
        confidence = '高' if score >= 0.60 else '中' if score >= 0.35 else '低'

        return PatternSignal('主力信号', detected,
                            min(score, 0.90), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.7 主升浪买点检测
    # ─────────────────────────────────────────────────

    def detect_main_rising_wave(
        self,
        prices: List[float], volumes: List[float],
        ma5: List[float] = None, ma10: List[float] = None,
        ma20: List[float] = None, ma120: List[float] = None,
    ) -> PatternSignal:
        """
        主升浪买点检测

        买点类型：
        1. 平台/箱体突破：放量突破横盘区间
        2. 均线突破：放量站上牛熊分界线(120/250MA)
        3. 回踩确认：突破后回踩原压力位(现支撑位)
        4. 三倍量突破：成交量放大到前5日均量3倍以上
        5. 上涨放量回调缩量
        """
        details = []
        score = 0.0

        if len(prices) < 30:
            return PatternSignal('主升浪买点', False, 0, '噪音', 0, ['数据不足'])

        current_price = prices[-1]

        # 买点1: 平台突破
        if len(prices) >= 20:
            platform_prices = prices[-20:-5]
            platform_high = max(platform_prices)
            platform_low = min(platform_prices)
            platform_range = (platform_high - platform_low) / platform_low

            if platform_range < 0.08 and current_price > platform_high * 1.02:
                recent_vol = sum(volumes[-3:]) / 3
                avg_vol = sum(volumes[-20:]) / 20
                if recent_vol > avg_vol * 1.5:
                    details.append(f'✅ 平台突破·振幅{platform_range:.1%}·放量确认')
                    score += 0.25
                else:
                    details.append('⚠️ 平台突破但量能不足')

        # 买点2: 均线突破（120日牛熊线）
        if ma120:
            if prices[-2] <= ma120[-2] and current_price > ma120[-1]:
                details.append('✅ 突破120日牛熊分界线')
                score += 0.20

        # 买点3: 回踩确认
        if ma10 and ma20:
            if (abs(current_price - ma10[-1]) / ma10[-1] < 0.02 or
                abs(current_price - ma20[-1]) / ma20[-1] < 0.02):
                details.append('✅ 股价回踩均线·二次确认买点')
                score += 0.15

        # 买点4: 三倍量突破
        if len(volumes) >= 6:
            vol_5ma = sum(volumes[-6:-1]) / 5
            if volumes[-1] > vol_5ma * 3.0:
                details.append('✅ 三倍量突破·主力大规模进场')
                score += 0.30

        # 买点5: 上涨放量回调缩量
        if len(prices) >= 10:
            up_vol = []
            down_vol = []
            for i in range(-10, 0):
                if prices[i] > prices[i-1]:
                    up_vol.append(volumes[i])
                else:
                    down_vol.append(volumes[i])
            if up_vol and down_vol:
                if sum(up_vol)/len(up_vol) > sum(down_vol)/len(down_vol) * 1.5:
                    details.append('✅ 上涨放量·回调缩量·主升浪特征')
                    score += 0.15

        # 避免高位追涨
        if len(prices) >= 60:
            low_60 = min(prices[-60:])
            if (current_price - low_60) / low_60 > 0.50:
                details.append('⚠️ 距60日低点涨幅>50%·警惕诱多')

        detected = score >= 0.25
        confidence = '高' if score >= 0.55 else '中' if score >= 0.30 else '低'

        return PatternSignal('主升浪买点', detected,
                            min(score, 0.88), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.8 筹码擒龙检测
    # ─────────────────────────────────────────────────

    def detect_chip_dragon(
        self,
        winner_ratio: float = 0,        # 获利比例
        chip_concentration: float = 0,  # 筹码集中度
    ) -> PatternSignal:
        """
        筹码擒龙检测

        条件：
        1. 获利比例(winner) EMA20 > 筹码警戒线
        2. 低位单峰密集
        3. 共振信号：均线+异动+主力
        """
        details = []
        score = 0.0

        if winner_ratio > 80:
            details.append('⚠️ 获利盘>80%·高位风险')
            score += 0.05
        elif winner_ratio > 50:
            details.append(f'✅ 获利盘{winner_ratio:.0f}%·主力锁仓')
            score += 0.15
        elif winner_ratio > 20:
            details.append(f'⏳ 获利盘{winner_ratio:.0f}%·底部区域')
            score += 0.10

        if chip_concentration > 0.7:
            details.append('✅ 筹码高度集中·主力控盘')
            score += 0.20
        elif chip_concentration > 0.5:
            details.append('⏳ 筹码中度集中')
            score += 0.10

        detected = score >= 0.15
        confidence = '高' if score >= 0.30 else '中' if score >= 0.20 else '低'

        return PatternSignal('筹码擒龙', detected,
                            min(score, 0.80), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.9 月线MACD擒牛检测
    # ─────────────────────────────────────────────────

    def detect_monthly_macd_bull(
        self,
        monthly_macd_dif: List[float] = None,
        monthly_macd_dea: List[float] = None,
        monthly_prices: List[float] = None,
        monthly_ma5: List[float] = None,
        monthly_ma10: List[float] = None,
        monthly_ma20: List[float] = None,
        is_st: bool = False,
    ) -> PatternSignal:
        """
        月线MACD擒牛战法检测

        三个核心条件：
        1. 非ST股
        2. 月线MACD底背离
        3. MACD金叉（允许中间一次死叉后再金叉）

        买点：周线回踩20周线不破 + 日线MACD零轴上死叉后再金叉
        """
        details = []
        score = 0.0

        if is_st:
            return PatternSignal('月线MACD擒牛', False, 0, '弱', 0, ['ST股·排除'])

        if not monthly_macd_dif or not monthly_macd_dea:
            return PatternSignal('月线MACD擒牛', False, 0, '噪音', 0, ['缺少月线MACD数据'])

        # 条件1: 底背离（价格新低但MACD未新低）
        if monthly_prices and len(monthly_prices) >= 6 and len(monthly_macd_dif) >= 6:
            price_low_1 = min(monthly_prices[-6:-3])
            price_low_2 = min(monthly_prices[-3:])
            macd_low_1 = min(monthly_macd_dif[-6:-3])
            macd_low_2 = min(monthly_macd_dif[-3:])

            if price_low_2 < price_low_1 and macd_low_2 > macd_low_1:
                details.append('✅ 月线MACD底背离·长线资金吸筹')
                score += 0.35

        # 条件2: MACD金叉
        if len(monthly_macd_dif) >= 2:
            if monthly_macd_dif[-1] > monthly_macd_dea[-1]:
                if monthly_macd_dif[-2] <= monthly_macd_dea[-2]:
                    details.append('✅ 月线MACD金叉·主升浪信号')
                    score += 0.25

        # 条件3: 月线均线多头
        if monthly_ma5 and monthly_ma10 and monthly_ma20:
            if monthly_ma5[-1] > monthly_ma10[-1] > monthly_ma20[-1]:
                details.append('✅ 月线均线多头排列')
                score += 0.15

        # 倍量阳线
        if monthly_prices and len(monthly_prices) >= 2:
            if monthly_prices[-1] > monthly_prices[-2] * 1.05:
                details.append('✅ 月线倍量阳线·主力启动迹象')
                score += 0.10

        detected = score >= 0.30
        confidence = '高' if score >= 0.55 else '中' if score >= 0.30 else '低'

        return PatternSignal('月线MACD擒牛', detected,
                            min(score, 0.85), confidence, score,
                            details)

    # ─────────────────────────────────────────────────
    # 1.10 暗盘资金背离检测
    # ─────────────────────────────────────────────────

    def detect_dark_pool_divergence(
        self,
        open_fund_flow: float = 0,      # 明盘资金(正=流入)
        dark_pool_flow: float = 0,      # 暗盘资金(正=流入)
        price_position: str = '中位',    # 股价位置: 低位/中位/高位
    ) -> PatternSignal:
        """
        暗盘资金检测

        信号：
        - 明暗同向流入 → 主力进攻意愿强
        - 明盘流出暗盘流入 → 主力借回调吸筹
        - 明盘流入暗盘流出 → 主力拉高出货
        - 低位+暗盘持续流入 → 主力吸筹
        - 高位+暗盘流出 → 警惕出货
        """
        details = []
        score = 0.0

        if open_fund_flow > 0 and dark_pool_flow > 0:
            details.append('✅ 明暗同向流入·主力进攻')
            score += 0.30
        elif open_fund_flow < 0 and dark_pool_flow > 0:
            if price_position == '低位':
                details.append('✅ 明出暗进·低位吸筹·黄金信号')
                score += 0.35
            else:
                details.append('⏳ 明出暗进·关注后续确认')
                score += 0.15
        elif open_fund_flow > 0 and dark_pool_flow < 0:
            if price_position == '高位':
                details.append('⚠️ 明进暗出·高位出货·危险信号')
                score -= 0.20
            else:
                details.append('⚠️ 明进暗出·谨慎')
                score += 0.05

        # 位置修正
        if price_position == '低位':
            score += 0.05
        elif price_position == '高位':
            score -= 0.10

        detected = score >= 0.20
        confidence = '高' if score >= 0.35 else '中' if score >= 0.20 else '低'

        return PatternSignal('暗盘资金', detected, min(score, 0.80),
                            confidence, score, details)

    # ─────────────────────────────────────────────────
    # 1.11 开盘溢价率分析
    # ─────────────────────────────────────────────────

    def detect_opening_premium(
        self,
        prev_close: float, open_price: float,
        sentiment_context: str = 'neutral',
    ) -> PatternSignal:
        """
        开盘溢价率分析

        公式：开盘溢价率 = (开盘价 - 前日收盘) / 前日收盘
        信号：
        - +3%~+7%: 黄金竞价区间，资金抢筹
        - >+7%: 过度高开，追高风险
        - <0%: 低开，资金出逃
        """
        if prev_close <= 0:
            return PatternSignal('开盘溢价率', False, 0, '噪音', 0, ['无数据'])

        premium = (open_price - prev_close) / prev_close
        details = []
        score = 0.0

        if 0.03 <= premium <= 0.07:
            details.append(f'✅ 高开{premium:.1%}·黄金竞价区间')
            score += 0.25
        elif premium > 0.07:
            details.append(f'⚠️ 高开{premium:.1%}·过度高开追高风险')
            score += 0.10
        elif premium > 0:
            details.append(f'✅ 高开{premium:.1%}·情绪偏暖')
            score += 0.15
        elif premium < -0.03:
            details.append(f'⚠️ 低开{premium:.1%}·资金出逃信号')
            score -= 0.10
        else:
            details.append(f'平开{premium:.1%}')

        detected = score >= 0.15
        return PatternSignal('开盘溢价率', detected, min(score, 0.60),
                            '中' if score >= 0.20 else '低', score, details)

    # ─────────────────────────────────────────────────
    # 1.12 委比/量比综合评估
    # ─────────────────────────────────────────────────

    def detect_bid_ask_volume_ratio(
        self,
        bid_ask_ratio: float = 0,       # 委比 (-100~+100)
        volume_ratio: float = 1.0,       # 量比
        price_position: str = '中位',
    ) -> PatternSignal:
        """
        委比/量比综合评估

        委比 = (委买-委卖)/(委买+委卖) × 100%
        量比 = 当前成交量 / 过去5日均量

        组合解读：
        - 委比正+量比>1: 真强势（买盘强劲+资金真实流入）
        - 委比高+量比低迷: 假强势（挂单多但成交少→诱多）
        - 低位放量+委比由负转正: 主力吸筹
        - 高位放量+委比持续为正: 警惕出货
        """
        details = []
        score = 0.0

        # 委比分析
        if bid_ask_ratio > 30:
            details.append('委比>30%·买方意愿强劲')
            score += 0.10
        elif bid_ask_ratio < -30:
            details.append('委比<-30%·卖方压力大')
            score -= 0.10

        # 量比分析
        if 1.5 <= volume_ratio <= 2.5:
            details.append(f'量比{volume_ratio:.1f}·温和放量')
            score += 0.10
        elif 2.5 <= volume_ratio <= 5:
            details.append(f'量比{volume_ratio:.1f}·明显放量')
            score += 0.15
        elif volume_ratio > 5:
            details.append(f'量比{volume_ratio:.1f}·剧烈放量·警惕')
            if price_position == '高位':
                score -= 0.10

        # 组合判断
        if bid_ask_ratio > 20 and volume_ratio > 1.5:
            details.append('✅ 委比+量比双强·真强势')
            score += 0.15
        elif bid_ask_ratio > 40 and volume_ratio < 1.0:
            details.append('⚠️ 委比高量比低·假强势·疑诱多')
            score -= 0.15

        # 位置修正
        if price_position == '低位' and volume_ratio > 1.5 and bid_ask_ratio > -10:
            details.append('✅ 低位放量·主力吸筹特征')
            score += 0.10
        if price_position == '高位' and volume_ratio > 3:
            details.append('⚠️ 高位巨量·警惕出货')

        detected = score >= 0.10
        return PatternSignal('委比量比', detected, min(score, 0.55),
                            '中' if score >= 0.20 else '低', score, details)

    # ─────────────────────────────────────────────────
    # 1.13 分时图黄白线分析
    # ─────────────────────────────────────────────────

    def detect_intraday_yellow_white(
        self,
        white_above_yellow: bool = True,    # 白线(实时价)是否在黄线(均价)上方
        white_yellow_distance: float = 0,    # 白黄线距离
        tail_divergence: bool = False,        # 尾盘白黄背离
    ) -> PatternSignal:
        """
        分时图黄白线分析

        个股分时图：
        - 白线=实时成交价，黄线=当日均价
        - 白线稳定运行在黄线上方 → 强势
        - 白线运行在黄线下方 → 弱势

        大盘分时图：
        - 黄线在白线上方 → 中小盘活跃（赚钱效应好）
        """
        details = []
        score = 0.0

        if white_above_yellow:
            if white_yellow_distance > 0.02:
                details.append(f'✅ 白线>黄线{white_yellow_distance:.1%}·强势运行')
                score += 0.20
            else:
                details.append('✅ 白线在黄线上方·偏强')
                score += 0.10
        else:
            details.append('⚠️ 白线在黄线下方·弱势')
            score -= 0.05

        if tail_divergence:
            details.append('⚠️ 尾盘白黄背离·注意风险')
            score -= 0.10

        detected = score >= 0.10
        return PatternSignal('分时图黄白线', detected, min(score, 0.40),
                            '中' if score >= 0.15 else '低', score, details)

    # ─────────────────────────────────────────────────
    # 1.14 时间窗口分析
    # ─────────────────────────────────────────────────

    def detect_time_window(
        self,
        current_month: int = None,
        fib_day: int = None,
    ) -> PatternSignal:
        """
        时间窗口分析

        A股三大黄金窗口：
        - 7-9月：中报业绩行情
        - 2-3月：春季躁动行情
        - 11-1月：跨年行情

        斐波那契周期：第13/21/34/55天可能是变盘点
        """
        if current_month is None:
            current_month = datetime.now().month

        details = []
        score = 0.0

        # 黄金窗口
        gold_windows = {
            (7, 8, 9): '中报行情·业绩驱动',
            (2, 3): '春季躁动·情绪活跃',
            (11, 12, 1): '跨年行情·资金布局',
        }

        for months, desc in gold_windows.items():
            if current_month in months:
                details.append(f'✅ {desc}')
                score += 0.10
                break

        # 斐波那契周期
        if fib_day in [13, 21, 34, 55]:
            details.append(f'⏳ 斐波那契第{fib_day}天·潜在变盘点')
            score += 0.05

        detected = score >= 0.05
        return PatternSignal('时间窗口', detected, min(score, 0.20),
                            '低', score, details)

    # ─────────────────────────────────────────────────
    # 1.15 出货阶段检测（反向信号）
    # ─────────────────────────────────────────────────

    def detect_distribution_phase(
        self,
        prices: List[float], volumes: List[float],
        cumulative_gain: float = 0,
        ma5: List[float] = None, ma10: List[float] = None, ma20: List[float] = None,
    ) -> PatternSignal:
        """
        出货阶段检测

        信号：
        1. 累计涨幅>50% + 高位滞涨
        2. 高位放量滞涨（量增价不涨）
        3. 频繁长上影/大阴线
        4. 价涨量缩（量价背离）
        """
        details = []
        score = 0.0

        # 位置判断
        if cumulative_gain > 0.50:
            details.append(f'⚠️ 累计涨幅{cumulative_gain:.0%}·高风险区')

        # 高位放量滞涨
        if len(prices) >= 5 and len(volumes) >= 10:
            price_change_5d = (prices[-1] - prices[-5]) / prices[-5]
            avg_vol_5d = sum(volumes[-5:]) / 5
            avg_vol_20d = sum(volumes[-20:]) / 20

            if abs(price_change_5d) < 0.02 and avg_vol_5d > avg_vol_20d * 1.5:
                if cumulative_gain > 0.30:
                    details.append('🚨 高位放量滞涨·疑似出货')
                    score += 0.40  # 出货信号强度为正(用于排雷)

        # 长上影/大阴线
        if len(prices) >= 3:
            upper_shadows = 0
            big_red = 0
            for i in range(-3, 0):
                daily_range = abs(prices[i] - prices[i-1]) / prices[i-1]
                if daily_range > 0.03 and prices[i] < prices[i-1]:
                    big_red += 1
            if big_red >= 2:
                if cumulative_gain > 0.30:
                    details.append('🚨 连续大阴线·出货特征')
                    score += 0.30

        # 量价背离
        if len(prices) >= 5:
            if prices[-1] > prices[-5] * 1.02 and volumes[-1] < sum(volumes[-6:-1]) / 5:
                if cumulative_gain > 0.30:
                    details.append('🚨 价涨量缩·量价背离出货')
                    score += 0.25

        detected = score >= 0.25
        return PatternSignal('出货检测', detected, min(score, 0.90),
                            '高' if score >= 0.50 else '中' if score >= 0.25 else '低',
                            score, details)

    # ─────────────────────────────────────────────────
    # 1.12 综合形态打分（主入口）
    # ─────────────────────────────────────────────────

    def comprehensive_pattern_score(
        self,
        stock_data: dict,
    ) -> dict:
        """
        运行所有形态检测器，返回综合评分

        stock_data 需要包含足够的K线数据：
        - prices: List[float] 收盘价序列(至少60日)
        - volumes: List[float] 成交量序列
        - ma5/ma10/ma20/ma25/ma60/ma120: 各周期均线
        - 其他辅助数据
        """
        result = {
            'code': stock_data.get('code', ''),
            'name': stock_data.get('name', ''),
            'patterns': {},
            'total_score': 0.0,
            'strong_signals': 0,
            'moderate_signals': 0,
            'weak_signals': 0,
            'trap_warnings': 0,
            'composite_rating': '待定',
        }

        prices = stock_data.get('prices', [])
        volumes = stock_data.get('volumes', [])
        ma5 = stock_data.get('ma5', [])
        ma10 = stock_data.get('ma10', [])
        ma20 = stock_data.get('ma20', [])
        ma25 = stock_data.get('ma25', [])
        ma60 = stock_data.get('ma60', [])
        ma120 = stock_data.get('ma120', [])

        # ── 强信号检测（核心形态）──
        duck_head = self.detect_old_duck_head(
            ma5, ma10, ma60, volumes, prices,
            stock_data.get('macd_dif', []), stock_data.get('macd_dea', [])
        )
        result['patterns']['老鸭头'] = self._signal_to_dict(duck_head)

        dragon_catch = self.detect_dragon_catch(
            ma5, ma10, ma20, prices, volumes,
            stock_data.get('volume_ratio', 1.0),
            stock_data.get('turnover_rate', 0),
            stock_data.get('big_order_net', 0),
        )
        result['patterns']['擒龙战法'] = self._signal_to_dict(dragon_catch)

        main_force = self.detect_main_force_signal(
            prices, volumes,
            stock_data.get('is_bottom_breakout', False),
            stock_data.get('is_shrinking_consolidation', False),
            stock_data.get('volume_doubled', False),
            ma5, ma10, ma20,
            stock_data.get('against_market', False),
            stock_data.get('bad_news_rise', False),
        )
        result['patterns']['主力信号'] = self._signal_to_dict(main_force)

        rising_wave = self.detect_main_rising_wave(
            prices, volumes, ma5, ma10, ma20, ma120,
        )
        result['patterns']['主升浪买点'] = self._signal_to_dict(rising_wave)

        # ── 中等信号检测 ──
        strategy_2560 = self.detect_2560(ma25,
            stock_data.get('vol_ma5', []), stock_data.get('vol_ma60', []),
            prices, volumes)
        result['patterns']['2560战法'] = self._signal_to_dict(strategy_2560)

        bvet = self.detect_bottom_volume_exceeds_top(prices, volumes)
        result['patterns']['底量超顶量'] = self._signal_to_dict(bvet)

        two_board = self.detect_two_board_leader(
            stock_data.get('code', ''), stock_data.get('name', ''),
            stock_data.get('first_board'), stock_data.get('second_board'),
            stock_data.get('sector_stocks'),
        )
        result['patterns']['二板定龙头'] = self._signal_to_dict(two_board)

        chip_dragon = self.detect_chip_dragon(
            stock_data.get('winner_ratio', 0),
            stock_data.get('chip_concentration', 0),
        )
        result['patterns']['筹码擒龙'] = self._signal_to_dict(chip_dragon)

        monthly_macd = self.detect_monthly_macd_bull(
            stock_data.get('monthly_macd_dif'),
            stock_data.get('monthly_macd_dea'),
            stock_data.get('monthly_prices'),
            stock_data.get('monthly_ma5'),
            stock_data.get('monthly_ma10'),
            stock_data.get('monthly_ma20'),
            stock_data.get('is_st', False),
        )
        result['patterns']['月线MACD擒牛'] = self._signal_to_dict(monthly_macd)

        dark_pool = self.detect_dark_pool_divergence(
            stock_data.get('open_fund_flow', 0),
            stock_data.get('dark_pool_flow', 0),
            stock_data.get('price_position', '中位'),
        )
        result['patterns']['暗盘资金'] = self._signal_to_dict(dark_pool)

        # ── 辅助检测 ──
        opening = self.detect_opening_premium(
            stock_data.get('prev_close', 0), stock_data.get('open_price', 0),
            stock_data.get('sentiment_context', 'neutral'),
        )
        result['patterns']['开盘溢价率'] = self._signal_to_dict(opening)

        bid_ask = self.detect_bid_ask_volume_ratio(
            stock_data.get('bid_ask_ratio', 0),
            stock_data.get('volume_ratio', 1.0),
            stock_data.get('price_position', '中位'),
        )
        result['patterns']['委比量比'] = self._signal_to_dict(bid_ask)

        intraday = self.detect_intraday_yellow_white(
            stock_data.get('white_above_yellow', True),
            stock_data.get('white_yellow_distance', 0),
            stock_data.get('tail_divergence', False),
        )
        result['patterns']['分时图黄白线'] = self._signal_to_dict(intraday)

        time_window = self.detect_time_window(
            stock_data.get('current_month'),
            stock_data.get('fib_day'),
        )
        result['patterns']['时间窗口'] = self._signal_to_dict(time_window)

        # ── 出货检测（排雷）──
        distribution = self.detect_distribution_phase(
            prices, volumes,
            stock_data.get('cumulative_gain', 0),
            ma5, ma10, ma20,
        )
        result['patterns']['出货检测'] = self._signal_to_dict(distribution)

        # ── 综合评分计算 ──
        # 权重分配：强信号×3 + 中信号×2 + 弱信号×1
        pattern_weights = {
            '老鸭头': 3.0, '擒龙战法': 2.5, '主力信号': 2.5, '主升浪买点': 2.5,
            '2560战法': 2.0, '底量超顶量': 2.0, '二板定龙头': 2.5,
            '筹码擒龙': 1.5, '月线MACD擒牛': 2.0, '暗盘资金': 1.5,
            '开盘溢价率': 1.0, '委比量比': 1.0, '分时图黄白线': 0.8, '时间窗口': 0.5,
        }

        total_weight = 0
        weighted_score = 0
        strong_count = 0
        moderate_count = 0
        weak_count = 0

        for pattern_name, signal in result['patterns'].items():
            if pattern_name == '出货检测':
                if signal['detected']:
                    result['trap_warnings'] += 1
                continue

            w = pattern_weights.get(pattern_name, 1.0)
            if signal['detected']:
                weighted_score += signal['score'] * w
                total_weight += w

                if signal['confidence'] == '高':
                    strong_count += 1
                elif signal['confidence'] == '中':
                    moderate_count += 1
                else:
                    weak_count += 1

        result['strong_signals'] = strong_count
        result['moderate_signals'] = moderate_count
        result['weak_signals'] = weak_count

        if total_weight > 0:
            result['total_score'] = round(weighted_score / total_weight, 4)
        else:
            result['total_score'] = 0.0

        # 综合评级
        if result['trap_warnings'] >= 1:
            result['composite_rating'] = '危险·出货信号'
            result['passed'] = False
        elif result['total_score'] >= 0.70 and strong_count >= 2:
            result['composite_rating'] = '🔥 极强·多信号共振'
            result['passed'] = True
        elif result['total_score'] >= 0.55:
            result['composite_rating'] = '✅ 强势·核心信号'
            result['passed'] = True
        elif result['total_score'] >= 0.35:
            result['composite_rating'] = '⏳ 中等·待确认'
            result['passed'] = True
        elif result['total_score'] >= 0.15:
            result['composite_rating'] = '👀 弱势·关注'
            result['passed'] = True
        else:
            result['composite_rating'] = '❌ 无效·无信号'
            result['passed'] = False

        return result

    @staticmethod
    def _signal_to_dict(signal: PatternSignal) -> dict:
        return {
            'pattern_name': signal.pattern_name,
            'detected': signal.detected,
            'strength': round(signal.strength, 4),
            'confidence': signal.confidence,
            'score': round(signal.score, 4),
            'details': signal.details,
            'warnings': signal.warnings,
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 2: T-1 尾盘筛选器 (T1TailScreener)
# ═══════════════════════════════════════════════════════════════

@dataclass
class T1TailConfig:
    """T-1尾盘筛选参数——来自t1_tail.md"""
    # 技术初筛
    GAIN_RANGE: Tuple[float, float] = (0.02, 0.06)      # 涨幅2%-6%（匹配TDX筛选器"涨幅2到6"）
    TURNOVER_RANGE: Tuple[float, float] = (0.03, 0.12)   # 换手率3%-12%（匹配TDX筛选器"换手率3到10"）
    MARKET_CAP_RANGE: Tuple[float, float] = (1e9, 1e11)  # 流通市值10亿-1000亿（拓宽实盘覆盖）
    VOLUME_RATIO_RANGE: Tuple[float, float] = (1.2, 2.5) # 量比1.2-2.5

    # 尾盘特征
    TAIL_VOLUME_RATIO: float = 0.25        # 尾盘30分钟成交量占全天≥25%
    TAIL_START: str = '14:30'              # 尾盘开始时间
    TAIL_END: str = '14:57'               # 尾盘结束时间(集合竞价前3分钟)
    BUY_WINDOW: str = '14:50-14:57'       # 最佳买入窗口

    # 均价线要求
    ABOVE_AVG_LINE: bool = True            # 全天运行在分时均价线上方
    TAIL_GENTLE_VOLUME: bool = True        # 尾盘温和放量(不是急拉)

    # 大盘环境
    MAX_MARKET_DECLINE: float = -0.005     # 大盘单边下跌>0.5%时降低标准
    NORTHBOUND_OUTFLOW_THRESHOLD: float = -5e8  # 北向流出超5亿暂停

    # 风控
    SINGLE_MAX_POSITION: float = 0.20      # 单票最大仓位20%
    HARD_STOP_LOSS: float = -0.03          # 刚性止损-3%


class T1TailScreener:
    """T-1尾盘筛选器"""

    def __init__(self, config: T1TailConfig = None):
        self.config = config or T1TailConfig()

    def screen_t1_basic(self, stock: dict) -> dict:
        """
        T-1技术初筛

        返回筛选结果和通过/不通过的原因
        """
        checks = []
        passed = True

        # 涨幅检查
        gain = stock.get('daily_change_pct', 0)
        gain_ok = self.config.GAIN_RANGE[0] <= gain <= self.config.GAIN_RANGE[1]
        checks.append({
            'item': '涨幅',
            'value': f'{gain:+.2%}',
            'threshold': f'{self.config.GAIN_RANGE[0]:.0%}-{self.config.GAIN_RANGE[1]:.0%}',
            'passed': gain_ok,
            'weight': 0.20,
        })
        if not gain_ok:
            passed = False

        # 换手率检查
        turnover = stock.get('turnover_rate', 0)
        turnover_ok = self.config.TURNOVER_RANGE[0] <= turnover <= self.config.TURNOVER_RANGE[1]
        checks.append({
            'item': '换手率',
            'value': f'{turnover:.1%}',
            'threshold': f'{self.config.TURNOVER_RANGE[0]:.0%}-{self.config.TURNOVER_RANGE[1]:.0%}',
            'passed': turnover_ok,
            'weight': 0.15,
        })
        if not turnover_ok:
            passed = False

        # 量比检查
        vol_ratio = stock.get('volume_ratio', 1.0)
        vol_ok = self.config.VOLUME_RATIO_RANGE[0] <= vol_ratio <= self.config.VOLUME_RATIO_RANGE[1]
        checks.append({
            'item': '量比',
            'value': f'{vol_ratio:.1f}',
            'threshold': f'{self.config.VOLUME_RATIO_RANGE[0]:.1f}-{self.config.VOLUME_RATIO_RANGE[1]:.1f}',
            'passed': vol_ok,
            'weight': 0.15,
        })
        if not vol_ok:
            passed = False

        # 市值检查
        mcap = stock.get('market_cap', 0)
        mcap_ok = self.config.MARKET_CAP_RANGE[0] <= mcap <= self.config.MARKET_CAP_RANGE[1]
        checks.append({
            'item': '流通市值',
            'value': f'{mcap/1e8:.0f}亿',
            'threshold': f'{self.config.MARKET_CAP_RANGE[0]/1e8:.0f}亿-{self.config.MARKET_CAP_RANGE[1]/1e8:.0f}亿',
            'passed': mcap_ok,
            'weight': 0.10,
        })

        # 均线多头
        ma5 = stock.get('ma5', [0])[-1] if stock.get('ma5') else 0
        ma10 = stock.get('ma10', [0])[-1] if stock.get('ma10') else 0
        ma20 = stock.get('ma20', [0])[-1] if stock.get('ma20') else 0
        ma_bullish = ma5 > ma10 > ma20 if (ma5 and ma10 and ma20) else False
        checks.append({
            'item': '均线多头',
            'value': '是' if ma_bullish else '否',
            'threshold': '5>10>20',
            'passed': ma_bullish,
            'weight': 0.10,
        })

        # 尾盘30分钟放量
        tail_vol_ratio = stock.get('tail_volume_ratio', 0)
        tail_ok = tail_vol_ratio >= self.config.TAIL_VOLUME_RATIO
        checks.append({
            'item': '尾盘放量',
            'value': f'{tail_vol_ratio:.0%}',
            'threshold': f'≥{self.config.TAIL_VOLUME_RATIO:.0%}',
            'passed': tail_ok,
            'weight': 0.20,
        })
        if not tail_ok:
            passed = False

        # 分时均价线上方
        above_avg = stock.get('above_avg_line', False)
        checks.append({
            'item': '均价线上方',
            'value': '是' if above_avg else '否',
            'threshold': '全天运行在上方',
            'passed': above_avg,
            'weight': 0.10,
        })

        # 计算T-1初筛得分
        t1_score = sum(c['weight'] for c in checks if c['passed'])

        return {
            'passed': passed,
            't1_score': round(t1_score, 2),
            'checks': checks,
            'buy_window': self.config.BUY_WINDOW if passed else None,
            'suggested_position': round(self.config.SINGLE_MAX_POSITION * t1_score, 2),
        }

    def assess_market_environment(self, market_data: dict) -> dict:
        """
        评估大盘环境，决定是否适合尾盘操作
        """
        decline = market_data.get('market_decline', 0)
        northbound = market_data.get('northbound_net', 0)
        tail_drop = market_data.get('tail_drop', 0)
        up_down_ratio = market_data.get('up_down_ratio', 1.0)

        issues = []
        env_score = 1.0

        if decline < self.config.MAX_MARKET_DECLINE:
            issues.append(f'大盘单边下跌{decline:.1%}')
            env_score *= 0.5
        if northbound < self.config.NORTHBOUND_OUTFLOW_THRESHOLD:
            issues.append(f'北向大幅流出{northbound/1e8:.0f}亿')
            env_score *= 0.6
        if tail_drop < -0.005:
            issues.append(f'尾盘跳水{tail_drop:.1%}')
            env_score *= 0.4
        if up_down_ratio < 0.5:
            issues.append('涨跌比过差')
            env_score *= 0.7

        suitable = len(issues) == 0

        return {
            'suitable': suitable,
            'env_score': round(env_score, 2),
            'issues': issues,
            'recommendation': '✅ 环境OK·正常选股' if suitable else
                              '⚠️ 环境偏差·降仓/空仓' if len(issues) <= 2 else
                              '🚫 环境恶劣·建议空仓',
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 诱多排雷引擎 (TrapDetector)
# ═══════════════════════════════════════════════════════════════

class TrapDetector:
    """
    诱多排雷引擎
    目标：踩雷率 5% → 0.1%
    """

    # 排雷维度
    TRAP_DIMENSIONS = {
        'technical_trap': '技术诱多',       # 尾盘急拉但无量
        'sentiment_trap': '舆情诱多',       # 舆情炒作但无实质
        'capital_trap': '资金诱多',         # 对倒+一日游
        'valuation_trap': '估值陷阱',       # 高位高PE无业绩支撑
        'event_trap': '事件地雷',          # 减持/解禁/监管
        'sector_trap': '板块陷阱',         # 龙头独涨无跟风
    }

    def detect_tail_rush_trap(self, stock: dict) -> dict:
        """
        检测尾盘急拉诱多
        特征：尾盘突然拉升但全天成交量低迷
        """
        tail_vol_pct = stock.get('tail_volume_ratio', 0)
        daily_vol_ratio = stock.get('volume_ratio', 1.0)
        tail_gain = stock.get('tail_gain', 0)
        full_day_gain = stock.get('daily_change_pct', 0)

        trap_score = 0
        reasons = []

        # 尾盘急拉但全天量能不足
        if tail_gain > 0.03 and tail_vol_pct > 0.30 and daily_vol_ratio < 1.2:
            trap_score += 0.30
            reasons.append('尾盘急拉+全天缩量·典型诱多')

        # 尾盘拉全天涨幅但早盘低迷
        if tail_gain > 0.02 and full_day_gain < 0.05 and tail_gain / max(full_day_gain, 0.001) > 0.6:
            trap_score += 0.20
            reasons.append('涨幅集中在尾盘·非自然上涨')

        return {
            'type': 'technical_trap',
            'trap_score': min(trap_score, 1.0),
            'is_trap': trap_score >= 0.30,
            'reasons': reasons,
        }

    def detect_sentiment_trap(self, stock: dict) -> dict:
        """
        检测舆情诱多
        特征：舆情热度高但无资金跟进
        """
        sentiment_score = stock.get('sentiment_score', 0.5)
        capital_score = stock.get('capital_score', 0.5)
        big_order_ratio = stock.get('big_order_ratio', 0)

        trap_score = 0
        reasons = []

        # 舆情热但资金冷
        if sentiment_score > 0.70 and capital_score < 0.40:
            trap_score += 0.35
            reasons.append('舆情高热但资金冷淡·疑似炒作')

        # 舆情高但大单占比低
        if sentiment_score > 0.60 and big_order_ratio < 0.15:
            trap_score += 0.25
            reasons.append('散户情绪高涨但无主力参与')

        return {
            'type': 'sentiment_trap',
            'trap_score': min(trap_score, 1.0),
            'is_trap': trap_score >= 0.30,
            'reasons': reasons,
        }

    def detect_event_landmine(self, stock: dict) -> dict:
        """
        检测事件地雷
        减持/解禁/监管问询/ST风险
        """
        trap_score = 0
        reasons = []

        events = {
            '减持': stock.get('has_reduction', False),
            '解禁': stock.get('has_unlock', False),
            '监管函': stock.get('has_regulatory_warning', False),
            'ST风险': stock.get('st_risk', False),
            '业绩暴雷': stock.get('earnings_cliff', False),
        }

        for event, triggered in events.items():
            if triggered:
                trap_score += 0.25
                reasons.append(f'事件地雷:{event}')

        return {
            'type': 'event_trap',
            'trap_score': min(trap_score, 1.0),
            'is_trap': trap_score >= 0.25,
            'reasons': reasons,
        }

    def detect_valuation_trap(self, stock: dict) -> dict:
        """检测估值陷阱"""
        pe = stock.get('pe', 0)
        sector_pe = stock.get('sector_pe', 0)
        cumulative_gain = stock.get('cumulative_gain', 0)
        earnings_growth = stock.get('earnings_growth', 0)

        trap_score = 0
        reasons = []

        # 高PE无增长
        if pe > 80 and earnings_growth < 0.10:
            trap_score += 0.30
            reasons.append(f'PE={pe:.0f}但增长仅{earnings_growth:.0%}·估值泡沫')

        # PE远超行业
        if sector_pe > 0 and pe > sector_pe * 2:
            trap_score += 0.20
            reasons.append(f'PE是行业{pe/sector_pe:.1f}倍')

        # 短期涨幅过大
        if cumulative_gain > 0.60:
            trap_score += 0.15
            reasons.append(f'短期涨幅{cumulative_gain:.0%}')

        return {
            'type': 'valuation_trap',
            'trap_score': min(trap_score, 1.0),
            'is_trap': trap_score >= 0.30,
            'reasons': reasons,
        }

    def comprehensive_trap_check(self, stock: dict) -> dict:
        """
        综合排雷检查
        返回排雷报告和是否安全
        """
        checks = [
            self.detect_tail_rush_trap(stock),
            self.detect_sentiment_trap(stock),
            self.detect_event_landmine(stock),
            self.detect_valuation_trap(stock),
        ]

        total_trap_score = sum(c['trap_score'] for c in checks)
        active_traps = [c for c in checks if c['is_trap']]
        all_reasons = []
        for c in active_traps:
            all_reasons.extend(c['reasons'])

        # 安全判定：总排雷分<0.3，且无单维度触发
        is_safe = total_trap_score < 0.30 and len(active_traps) == 0
        risk_level = '安全' if total_trap_score < 0.15 else \
                     '观察' if total_trap_score < 0.30 else \
                     '警告' if total_trap_score < 0.50 else \
                     '危险' if total_trap_score < 0.70 else '黑名单'

        return {
            'passed': is_safe,  # 与Orchestrator兼容
            'is_safe': is_safe,
            'risk_level': risk_level,
            'total_trap_score': round(total_trap_score, 4),
            'active_traps': len(active_traps),
            'trap_reasons': all_reasons,
            'trap_details': {c['type']: {
                'score': c['trap_score'],
                'is_trap': c['is_trap'],
                'reasons': c['reasons'],
            } for c in checks},
            'target_trap_rate': 0.001,  # 目标0.1%
            'estimated_trap_rate': round(min(total_trap_score / 2, 0.20), 4),
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 尾盘终极选股主引擎 (TailMarketUltimate)
# ═══════════════════════════════════════════════════════════════

class TailMarketUltimate:
    """
    尾盘终极选股引擎

    四层递进筛选：
    Layer 1: T-1技术初筛（涨幅/换手/量比/市值/尾盘特征）
    Layer 2: 20+形态共振检测（老鸭头/2560/擒龙/二板/主升浪…）
    Layer 3: 舆情+情报确认（T-1尾盘情报确认）
    Layer 4: 十二维共振终审（7权重+贝叶斯+M51+M54）

    输出：终极选股决策
    """

    def __init__(self):
        self.pattern_detector = PatternDetector()
        self.t1_screener = T1TailScreener()
        self.trap_detector = TrapDetector()
        self.kpi = UltimateKPI()
        self.decision_history: List[dict] = []

    # ─────────────────────────────────────────────────
    # Layer 1: T-1初筛
    # ─────────────────────────────────────────────────

    def layer1_t1_screen(self, stock: dict, market_env: dict = None) -> dict:
        """
        T-1初筛 + 大盘环境评估
        """
        t1_result = self.t1_screener.screen_t1_basic(stock)

        if market_env:
            env_result = self.t1_screener.assess_market_environment(market_env)
        else:
            env_result = {'suitable': True, 'env_score': 1.0, 'issues': []}

        # 环境修正：大盘差时降低仓位
        t1_result['adjusted_score'] = round(t1_result['t1_score'] * env_result['env_score'], 2)
        t1_result['environment'] = env_result
        t1_result['layer1_passed'] = t1_result['passed'] and env_result['suitable']

        return t1_result

    # ─────────────────────────────────────────────────
    # Layer 2: 形态共振检测
    # ─────────────────────────────────────────────────

    def layer2_pattern_resonance(self, stock: dict) -> dict:
        """
        运行所有形态检测器
        """
        pattern_result = self.pattern_detector.comprehensive_pattern_score(stock)
        return pattern_result

    # ─────────────────────────────────────────────────
    # Layer 3: 诱多排雷
    # ─────────────────────────────────────────────────

    def layer3_trap_detection(self, stock: dict) -> dict:
        """
        综合排雷检查
        """
        trap_result = self.trap_detector.comprehensive_trap_check(stock)
        return trap_result

    # ─────────────────────────────────────────────────
    # Layer 4: 十二维共振终审
    # ─────────────────────────────────────────────────

    def layer4_ultimate_verdict(
        self,
        stock: dict,
        l1_result: dict,
        l2_result: dict,
        l3_result: dict,
    ) -> dict:
        """
        十二维共振终审

        将四层筛选结果融合为最终判决
        维度：
        D1: T-1技术评分 (l1)
        D2: 老鸭头信号 (l2)
        D3: 擒龙战法信号 (l2)
        D4: 主力信号 (l2)
        D5: 主升浪买点 (l2)
        D6: 2560战法 (l2)
        D7: 底量超顶量 (l2)
        D8: 二板定龙头 (l2)
        D9: 暗盘资金 (l2)
        D10: 月线MACD (l2)
        D11: 排雷分 (l3, 负向)
        D12: 7权重融合分 (external)
        """
        # 提取各维度得分
        patterns = l2_result.get('patterns', {})

        d1 = l1_result.get('adjusted_score', 0)  # T-1初筛
        d2 = patterns.get('老鸭头', {}).get('score', 0) * 3.0  # 强形态×3
        d3 = patterns.get('擒龙战法', {}).get('score', 0) * 2.5
        d4 = patterns.get('主力信号', {}).get('score', 0) * 2.5
        d5 = patterns.get('主升浪买点', {}).get('score', 0) * 2.5
        d6 = patterns.get('2560战法', {}).get('score', 0) * 2.0
        d7 = patterns.get('底量超顶量', {}).get('score', 0) * 2.0
        d8 = patterns.get('二板定龙头', {}).get('score', 0) * 2.5
        d9 = patterns.get('暗盘资金', {}).get('score', 0) * 1.5
        d10 = patterns.get('月线MACD擒牛', {}).get('score', 0) * 2.0
        d11 = -l3_result.get('total_trap_score', 0) * 5.0  # 排雷负向×5
        d12 = stock.get('seven_weight_score', 0.5) * 2.0  # 外部7权重

        # D13: T+1反转突破加分（尾盘选股核心场景：下跌趋势中首日放量反弹）
        d13 = self._compute_reversal_breakout_score(stock, l2_result)

        dimensions = {
            'D1_T1初筛': d1,
            'D2_老鸭头': d2,
            'D3_擒龙战法': d3,
            'D4_主力信号': d4,
            'D5_主升浪买点': d5,
            'D6_2560战法': d6,
            'D7_底量超顶量': d7,
            'D8_二板定龙头': d8,
            'D9_暗盘资金': d9,
            'D10_月线MACD': d10,
            'D11_排雷(负向)': d11,
            'D12_7权重融合': d12,
            'D13_反转突破': d13,
        }

        # 加权求和（重新分配权重，D13占8%）
        weights = {
            'D1_T1初筛': 0.10, 'D2_老鸭头': 0.10, 'D3_擒龙战法': 0.09,
            'D4_主力信号': 0.09, 'D5_主升浪买点': 0.09, 'D6_2560战法': 0.07,
            'D7_底量超顶量': 0.07, 'D8_二板定龙头': 0.07,
            'D9_暗盘资金': 0.05, 'D10_月线MACD': 0.05,
            'D11_排雷(负向)': 0.06, 'D12_7权重融合': 0.07,
            'D13_反转突破': 0.08,
        }

        total = sum(dimensions[k] * weights[k] for k in dimensions)
        total = max(0.0, min(1.0, total))  # 裁剪到0~1

        # 共振计数
        resonance_count = sum(1 for k, v in dimensions.items()
                            if v > 0.3 and k != 'D11_排雷(负向)')

        # 最终判决
        if l3_result.get('risk_level') == '黑名单':
            verdict = '🚫 黑名单·禁止买入'
            action = 'reject'
        elif l3_result.get('risk_level') == '危险':
            verdict = '⚠️ 危险·不建议参与'
            action = 'reject'
        elif total >= 0.80 and resonance_count >= 6:
            verdict = '🔥🔥🔥 超级信号·重仓出击'
            action = 'buy_heavy'
            suggested_position = 0.20
        elif total >= 0.65 and resonance_count >= 4:
            verdict = '🔥🔥 强信号·标准买入'
            action = 'buy_standard'
            suggested_position = 0.15
        elif total >= 0.50 and resonance_count >= 3:
            verdict = '🔥 中信号·轻仓试探'
            action = 'buy_light'
            suggested_position = 0.10
        elif total >= 0.35 and l3_result.get('is_safe'):
            verdict = '⏳ 弱信号·关注'
            action = 'watch'
            suggested_position = 0.05
        else:
            verdict = '❌ 不满足条件'
            action = 'pass'
            suggested_position = 0.0

        # 核心信号检测
        core_signals = []
        if patterns.get('老鸭头', {}).get('detected'):
            core_signals.append('老鸭头')
        if patterns.get('主力信号', {}).get('detected'):
            core_signals.append('主力进场')
        if patterns.get('二板定龙头', {}).get('detected'):
            core_signals.append('二板龙头')
        if patterns.get('底量超顶量', {}).get('detected'):
            core_signals.append('底量超顶量')

        return {
            'verdict': verdict,
            'action': action,
            'total_score': round(total, 4),
            'resonance_count': resonance_count,
            'dimensions': {k: round(v, 4) for k, v in dimensions.items()},
            'suggested_position': suggested_position,
            'core_signals': core_signals,
            # 目标KPI对照
            'kpi_target': {
                'hit_rate': self.kpi.TARGET_HIT_RATE,
                'plr': self.kpi.TARGET_PLR,
                'trap_rate': self.kpi.TARGET_TRAP_RATE,
            },
            'estimated_performance': self._estimate_performance(
                total, resonance_count, l3_result),
        }

    def _compute_reversal_breakout_score(self, stock: dict, l2_result: dict) -> float:
        """
        D13: T+1反转突破评分

        尾盘选股核心场景：下跌趋势中首日放量反弹。
        检测信号：
        1. 价格在MA20下方但当日收阳（下跌趋势中反弹）
        2. 日涨幅≥3%且换手率达标
        3. 形态共振中有反转类信号（底量超顶量/主升浪买点/擒龙战法）
        4. 量比放大（资金进场）

        返回0~2.0的评分（与其他维度同尺度）
        """
        score = 0.0

        current_price = stock.get('current_price', 0)
        daily_change = stock.get('daily_change_pct', 0)  # fraction
        turnover = stock.get('turnover_rate', 0)  # fraction
        volume_ratio = stock.get('volume_ratio', 1.0)
        prices = stock.get('prices', [])
        ma20 = stock.get('ma20', [])

        # 条件1: 下跌趋势中反弹（价格<MA20但当日收涨）
        below_ma20 = False
        if ma20 and len(ma20) > 0 and current_price > 0:
            latest_ma20 = ma20[-1] if isinstance(ma20, list) else ma20
            if latest_ma20 > 0:
                below_ma20 = current_price < latest_ma20

        is_bounce = below_ma20 and daily_change > 0
        if is_bounce:
            score += 0.6  # 反转场景基础分

        # 条件2: 涨幅强度
        if daily_change >= 0.05:
            score += 0.5  # 强势反弹≥5%
        elif daily_change >= 0.03:
            score += 0.3  # 中等反弹3-5%
        elif daily_change >= 0.01:
            score += 0.15  # 弱反弹1-3%

        # 条件3: 换手率确认
        if turnover >= 0.05:
            score += 0.3  # 换手≥5%
        elif turnover >= 0.03:
            score += 0.15

        # 条件4: 量比放大
        if volume_ratio >= 2.0:
            score += 0.3
        elif volume_ratio >= 1.5:
            score += 0.15

        # 条件5: 反转类形态共振
        patterns = l2_result.get('patterns', {})
        reversal_patterns = ['底量超顶量', '主升浪买点', '擒龙战法', '分时图黄白线']
        reversal_count = sum(1 for p in reversal_patterns
                           if patterns.get(p, {}).get('detected'))
        score += min(reversal_count * 0.15, 0.3)

        # 裁剪到0~2.0（与维度尺度一致，其他维度最大~2.5-3.0）
        return round(min(2.0, max(0.0, score)), 4)

    def _estimate_performance(self, total_score: float,
                              resonance_count: int,
                              trap_result: dict) -> dict:
        """
        基于当前信号估算命中率和盈亏比
        """
        # 命中率估算公式（四层递进后的理论命中率）
        base_hit = 0.30
        pattern_bonus = min(resonance_count * 0.08, 0.40)
        score_bonus = total_score * 0.30
        trap_penalty = max(0, 1.0 - trap_result['total_trap_score']) * 0.10

        estimated_hit = base_hit + pattern_bonus + score_bonus + trap_penalty
        estimated_hit = min(estimated_hit, 0.95)  # 理论上限95%（保留不确定性）

        # 盈亏比估算
        base_plr = 2.0
        resonance_plr_bonus = min(resonance_count * 1.2, 6.0)
        estimated_plr = base_plr + resonance_plr_bonus

        # 踩雷率估算
        estimated_trap = max(0.001, trap_result['total_trap_score'] / 3)

        return {
            'estimated_hit_rate': round(estimated_hit, 3),
            'estimated_plr': round(estimated_plr, 1),
            'estimated_trap_rate': round(estimated_trap, 4),
        }

    # ─────────────────────────────────────────────────
    # 主流程：四层递进
    # ─────────────────────────────────────────────────

    def run_full_pipeline(
        self,
        stock: dict,
        market_env: dict = None,
    ) -> dict:
        """
        执行完整的四层递进选股流程

        输入 stock:
        {
            'code': str, 'name': str,
            'daily_change_pct': float,  # 当日涨跌幅
            'turnover_rate': float,     # 换手率
            'volume_ratio': float,      # 量比
            'market_cap': float,        # 流通市值
            'tail_volume_ratio': float, # 尾盘30分钟量占比
            'above_avg_line': bool,     # 是否在均价线上方
            # K线数据（用于形态检测）
            'prices': List[float], 'volumes': List[float],
            'ma5': List[float], 'ma10': List[float], 'ma20': List[float],
            'ma25': List[float], 'ma60': List[float], 'ma120': List[float],
            'macd_dif': List[float], 'macd_dea': List[float],
            # 辅助数据
            'vol_ma5': List[float], 'vol_ma60': List[float],
            'big_order_ratio': float, 'big_order_net': float,
            'sentiment_score': float, 'capital_score': float,
            'winner_ratio': float, 'chip_concentration': float,
            'open_fund_flow': float, 'dark_pool_flow': float,
            'price_position': str,  # 低位/中位/高位
            'cumulative_gain': float,
            'seven_weight_score': float,
            # 排雷数据
            'has_reduction': bool, 'has_unlock': bool,
            'has_regulatory_warning': bool, 'st_risk': bool,
            'earnings_cliff': bool,
            'pe': float, 'sector_pe': float, 'earnings_growth': float,
        }
        """
        code = stock.get('code', '')
        name = stock.get('name', '')

        pipeline_result = {
            'code': code,
            'name': name,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'pipeline': {},
            # 传递关键行情字段（供报告展示）
            'current_price': stock.get('current_price', 0),
            'daily_change_pct': stock.get('daily_change_pct', 0),
            'turnover_rate': stock.get('turnover_rate', 0),
            'market_cap': stock.get('market_cap', 0),
            'volume_ratio': stock.get('volume_ratio', 1.0),
        }

        # ── 第1层：T-1初筛 ──
        l1 = self.layer1_t1_screen(stock, market_env)
        pipeline_result['pipeline']['L1_T1初筛'] = l1

        if not l1['passed']:
            pipeline_result['verdict'] = '❌ T-1初筛未通过'
            pipeline_result['action'] = 'pass'
            pipeline_result['total_score'] = 0
            return pipeline_result

        # ── 第2层：形态共振 ──
        l2 = self.layer2_pattern_resonance(stock)
        pipeline_result['pipeline']['L2_形态共振'] = l2

        # ── 第3层：诱多排雷 ──
        l3 = self.layer3_trap_detection(stock)
        pipeline_result['pipeline']['L3_排雷检测'] = l3

        if l3['risk_level'] == '黑名单':
            pipeline_result['verdict'] = '🚫 黑名单·触发财务/事件地雷'
            pipeline_result['action'] = 'reject'
            pipeline_result['total_score'] = 0
            return pipeline_result

        # ── 第4层：十二维共振终审 ──
        l4 = self.layer4_ultimate_verdict(stock, l1, l2, l3)
        pipeline_result['pipeline']['L4_终极判决'] = l4

        pipeline_result['verdict'] = l4['verdict']
        pipeline_result['action'] = l4['action']
        pipeline_result['total_score'] = l4['total_score']
        pipeline_result['estimated_hit_rate'] = l4['estimated_performance']['estimated_hit_rate']
        pipeline_result['estimated_plr'] = l4['estimated_performance']['estimated_plr']
        pipeline_result['estimated_trap_rate'] = l4['estimated_performance']['estimated_trap_rate']
        pipeline_result['suggested_position'] = l4['suggested_position']
        pipeline_result['core_signals'] = l4['core_signals']
        pipeline_result['resonance_count'] = l4['resonance_count']

        # 记录决策
        self.decision_history.append({
            'code': code, 'name': name,
            'timestamp': pipeline_result['timestamp'],
            'verdict': pipeline_result['verdict'],
            'action': pipeline_result['action'],
            'total_score': pipeline_result['total_score'],
        })

        return pipeline_result

    def batch_run(self, stocks: List[dict],
                  market_env: dict = None) -> List[dict]:
        """
        批量运行选股流程
        """
        results = []
        for stock in stocks:
            result = self.run_full_pipeline(stock, market_env)
            results.append(result)

        # 按总分排序
        results.sort(key=lambda x: x.get('total_score', 0), reverse=True)

        # 生成统计
        passed = sum(1 for r in results if r['action'] not in ('pass', 'reject'))
        buy_signals = sum(1 for r in results if r['action'].startswith('buy'))

        return results

    def generate_summary_report(self, results: List[dict]) -> dict:
        """生成总结报告"""
        total = len(results)
        buy_heavy = sum(1 for r in results if r['action'] == 'buy_heavy')
        buy_standard = sum(1 for r in results if r['action'] == 'buy_standard')
        buy_light = sum(1 for r in results if r['action'] == 'buy_light')
        watch = sum(1 for r in results if r['action'] == 'watch')
        rejected = sum(1 for r in results if r['action'] == 'reject')
        passed = sum(1 for r in results if r['action'] == 'pass')

        top_picks = [r for r in results if r['action'] in ('buy_heavy', 'buy_standard')][:10]

        return {
            'total_scanned': total,
            'buy_signals': {
                'heavy': buy_heavy,
                'standard': buy_standard,
                'light': buy_light,
                'total': buy_heavy + buy_standard + buy_light,
            },
            'watch': watch,
            'rejected': rejected,
            'passed': passed,
            'top_picks': [{
                'code': r['code'], 'name': r['name'],
                'score': r['total_score'], 'action': r['action'],
                'estimated_hit_rate': r.get('estimated_hit_rate', 0),
                'estimated_plr': r.get('estimated_plr', 0),
                'core_signals': r.get('core_signals', []),
            } for r in top_picks],
            'kpi_targets': {
                'hit_rate': 0.99, 'plr': 10.0, 'trap_rate': 0.001,
            },
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 引擎入口 & 自检
# ═══════════════════════════════════════════════════════════════

def run_ultimate_self_test():
    """
    终极引擎自检测试
    验证所有20+形态检测器和四层递进流程
    """
    print("=" * 70)
    print("║  V13.0 尾盘终极选股引擎 — 自检测试")
    print("=" * 70)

    engine = TailMarketUltimate()
    detector = engine.pattern_detector

    # 模拟K线数据
    nprices = [25.0 + i * 0.15 + math.sin(i/5) * 0.5 for i in range(80)]
    nvolumes = [10000000 * (0.8 + 0.4 * abs(math.sin(i/8))) for i in range(80)]
    # 最后几天放量
    for i in range(-5, 0):
        nvolumes[i] *= 1.5

    # 计算均线
    def calc_ma(data, period):
        result = []
        for i in range(len(data)):
            if i < period - 1:
                result.append(data[i])
            else:
                result.append(sum(data[i-period+1:i+1]) / period)
        return result

    ma5 = calc_ma(nprices, 5)
    ma10 = calc_ma(nprices, 10)
    ma20 = calc_ma(nprices, 20)
    ma25 = calc_ma(nprices, 25)
    ma60 = calc_ma(nprices, 60)
    ma120 = calc_ma(nprices, 120)
    vol_ma5 = calc_ma(nvolumes, 5)
    vol_ma60 = calc_ma(nvolumes, 60)

    test_stock = {
        'code': '002371', 'name': '北方华创(测试)',
        'daily_change_pct': 0.042,     # 4.2%涨幅
        'turnover_rate': 0.07,          # 7%换手
        'volume_ratio': 1.8,            # 量比1.8
        'market_cap': 12_000_000_000,      # 120亿（50-200亿范围内）
        'tail_volume_ratio': 0.28,          # 尾盘28%
        'above_avg_line': True,
        'prices': nprices,
        'volumes': nvolumes,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
        'ma25': ma25, 'ma60': ma60, 'ma120': ma120,
        'macd_dif': [0.1 + i*0.02 for i in range(80)],
        'macd_dea': [0.05 + i*0.015 for i in range(80)],
        'vol_ma5': vol_ma5, 'vol_ma60': vol_ma60,
        'big_order_ratio': 0.35, 'big_order_net': 0.15,
        'sentiment_score': 0.72, 'capital_score': 0.65,
        'winner_ratio': 65, 'chip_concentration': 0.62,
        'open_fund_flow': 50_000_000, 'dark_pool_flow': 30_000_000,
        'price_position': '中位', 'cumulative_gain': 0.18,
        'seven_weight_score': 0.72,
        'has_reduction': False, 'has_unlock': False,
        'has_regulatory_warning': False, 'st_risk': False,
        'earnings_cliff': False,
        'pe': 45, 'sector_pe': 55, 'earnings_growth': 0.25,
        'prev_close': 31.0, 'open_price': 32.2,
        'bid_ask_ratio': 25, 'white_above_yellow': True,
        'white_yellow_distance': 0.015, 'tail_divergence': False,
        'current_month': 6, 'fib_day': 21,
        'sentiment_context': 'neutral',
    }

    # ── 形态检测自测 ──
    print("\n📊 形态检测器自测:")
    print("-" * 50)

    results = {}

    duck = detector.detect_old_duck_head(
        ma5, ma10, ma60, nvolumes, nprices,
        test_stock['macd_dif'], test_stock['macd_dea']
    )
    results['老鸭头'] = duck
    print(f"  老鸭头: detected={duck.detected}, score={duck.score:.3f}, conf={duck.confidence}")

    bvet = detector.detect_bottom_volume_exceeds_top(nprices, nvolumes)
    results['底量超顶量'] = bvet
    print(f"  底量超顶量: detected={bvet.detected}, score={bvet.score:.3f}")

    dragon = detector.detect_dragon_catch(
        ma5, ma10, ma20, nprices, nvolumes,
        test_stock['volume_ratio'], test_stock['turnover_rate']
    )
    results['擒龙战法'] = dragon
    print(f"  擒龙战法: detected={dragon.detected}, score={dragon.score:.3f}")

    mf = detector.detect_main_force_signal(nprices, nvolumes,
        is_bottom_volume_breakout=True, ma5=ma5, ma10=ma10, ma20=ma20)
    results['主力信号'] = mf
    print(f"  主力信号: detected={mf.detected}, score={mf.score:.3f}")

    mrw = detector.detect_main_rising_wave(nprices, nvolumes, ma5, ma10, ma20, ma120)
    results['主升浪买点'] = mrw
    print(f"  主升浪买点: detected={mrw.detected}, score={mrw.score:.3f}")

    strat_2560 = detector.detect_2560(ma25, vol_ma5, vol_ma60, nprices, nvolumes)
    results['2560战法'] = strat_2560
    print(f"  2560战法: detected={strat_2560.detected}, score={strat_2560.score:.3f}")

    dist = detector.detect_distribution_phase(nprices, nvolumes, 0.18)
    results['出货检测'] = dist
    print(f"  出货检测: detected={dist.detected}, score={dist.score:.3f}")

    # ── 综合形态评分 ──
    print("\n📊 综合形态评分:")
    print("-" * 50)
    comp = detector.comprehensive_pattern_score(test_stock)
    print(f"  评级: {comp['composite_rating']}")
    print(f"  总分: {comp['total_score']:.3f}")
    print(f"  强信号: {comp['strong_signals']} | 中信号: {comp['moderate_signals']} | 弱信号: {comp['weak_signals']}")

    # ── 四层递进测试 ──
    print("\n📊 四层递进流程测试:")
    print("-" * 50)

    pipeline = engine.run_full_pipeline(test_stock)
    print(f"  L1通过: {pipeline['pipeline']['L1_T1初筛']['passed']}")
    print(f"  L2评级: {pipeline['pipeline']['L2_形态共振']['composite_rating']}")
    print(f"  L3安全: {pipeline['pipeline']['L3_排雷检测']['is_safe']}")
    print(f"  L4判决: {pipeline['pipeline']['L4_终极判决']['verdict']}")
    print(f"  最终行动: {pipeline['action']}")
    print(f"  总分: {pipeline['total_score']:.4f}")
    print(f"  共振数: {pipeline.get('resonance_count', 0)}")

    # ── 性能估算 ──
    print("\n🎯 性能估算 vs KPI目标:")
    print("-" * 50)
    est = pipeline['pipeline']['L4_终极判决']['estimated_performance']
    print(f"  命中率估算: {est['estimated_hit_rate']:.1%}  目标: 99%")
    print(f"  盈亏比估算: {est['estimated_plr']:.1f}      目标: 10.0")
    print(f"  踩雷率估算: {est['estimated_trap_rate']:.2%}   目标: 0.1%")

    # ── 知识库模式覆盖检查 ──
    print("\n📚 知识库覆盖检查:")
    print("-" * 50)
    kb_patterns = [
        '老鸭头', '建仓/拉升/出货阶段', '2560战法', '底量超顶量',
        '开盘溢价率', '擒龙战法', '委比/量比', '分时图黄白线',
        '时间窗口', '筹码擒龙', '趋势线/均线', '月线MACD擒牛',
        '主力信号', '二板定龙头', '暗盘资金', '主升浪买点',
        '交易悟道(心态)', 'T-1尾盘潜伏', '舆情情报体系',
    ]
    implemented = [
        '老鸭头', '出货检测', '2560战法', '底量超顶量',
        '擒龙战法', '筹码擒龙', '月线MACD擒牛',
        '主力信号', '二板定龙头', '暗盘资金', '主升浪买点',
        'T-1初筛', '排雷引擎', '开盘溢价率', '委比/量比',
        '分时图黄白线', '时间窗口', '舆情情报', '均线',
    ]
    for kbp in kb_patterns:
        status = '✅' if any(imp in kbp for imp in implemented) else '⏳'
        print(f"  {status} {kbp}")

    print(f"\n{'='*70}")
    print(f"✅ V13.0 尾盘终极选股引擎自检完成")
    print(f"🎯 目标: 命中率99% | 盈亏比10.0 | 踩雷率0.1%")
    print(f"{'='*70}")

    return pipeline


if __name__ == '__main__':
    result = run_ultimate_self_test()
