#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.1 M56 尾盘30分钟黄金半小时异动引擎 — Tail30MinEngine           ║
║  ============================================================       ║
║  圣杯目标：T+1次日上涨/涨停 · 尾盘信号是最直接的T→T+1桥梁          ║
║                                                                      ║
║  知识融合：                                                          ║
║  ├── 知乎「尾盘30分钟6大核心技巧」（2026）                          ║
║  ├── 东方财富「14:30-15:00黄金半小时」（2026）                      ║
║  ├── 什么值得买「62%胜率尾盘策略数据拆解」（2026）                  ║
║  ├── 百家号「次日涨停逃不过的规律」（2026）                         ║
║  ├── 通达信量化1号「94.65%高冲红率」框架（2025）                   ║
║  └── AurumQ-RL「事件信号exp-decay编码」思路                         ║
║                                                                      ║
║  核心创新：                                                          ║
║  1. 尾盘四模式分类（放量拉升/跳水/缩量横盘/无量偷袭）              ║
║  2. 板块联动共振检测（单票异动需板块确认）                         ║
║  3. 对倒行为深度识别（大单对倒 vs 真实抢筹）                       ║
║  4. 次日高开概率密度估计（贝叶斯后验）                             ║
║  5. 量价共振强度量化评分（T+1上涨概率映射）                        ║
║  6. 6大闸门基础过滤（20MA/非高位/板块红/不跳水/量能达标/无对倒）  ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import math
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 数据结构定义
# ═══════════════════════════════════════════════════════════════

class TailPattern(Enum):
    """尾盘模式分类"""
    SURGE = ('放量拉升', 0.85, '资金尾盘抢筹，次日高开概率最高')
    DIVE = ('放量跳水', 0.10, '资金提前出逃，次日承压概率高')
    SIDEWAYS_SHRINK = ('缩量横盘', 0.45, '惜售信号，需板块确认')
    FAKE_PUMP = ('无量偷袭', 0.20, '主力对倒假象，次日大概率回落')
    NORMAL = ('普通收盘', 0.35, '无显著异动')


class SignalGrade(Enum):
    """信号等级"""
    S_PLATINUM = ('铂金', 0.90, '量价共振+板块确认+技术形态完美')
    S_GOLD = ('黄金', 0.75, '量价共振+板块确认')
    S_SILVER = ('白银', 0.55, '量价异动但板块未共振')
    S_BRONZE = ('黄铜', 0.35, '信号存在但需更多确认')
    S_TRASH = ('垃圾', 0.00, '诱多/对倒/跳水信号')


@dataclass
class Tail30MinSignal:
    """尾盘30分钟信号结果"""
    code: str
    name: str = ''
    pattern: TailPattern = TailPattern.NORMAL
    grade: SignalGrade = SignalGrade.S_TRASH

    # 核心量化指标
    surge_score: float = 0.0          # 放量拉升评分 0~1
    volume_ratio: float = 1.0         # 尾盘量比（vs前30分钟均值）
    price_slope: float = 0.0          # 价格变化率（%/30min）
    angle_deg: float = 0.0            # 拉升角度
    sector_resonance: float = 0.0     # 板块共振度 0~1
    wash_trade_risk: float = 0.0      # 对倒风险 0~1（越高越危险）

    # 次日预判
    gap_up_prob: float = 0.0          # 次日高开概率
    expected_return: float = 0.0      # 预期收益率
    limit_up_prob: float = 0.0        # 涨停概率
    risk_score: float = 0.0           # 综合风险评分

    # 6大门槛
    above_20ma: bool = False
    not_high_position: bool = False
    sector_red: bool = False
    no_dive: bool = True
    volume_ok: bool = False
    no_wash_trade: bool = True

    # 详情
    details: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw_data: Dict = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 尾盘模式分类器
# ═══════════════════════════════════════════════════════════════

class TailPatternClassifier:
    """
    尾盘30分钟模式分类

    核心逻辑（来源：知乎6大核心技巧 + 东方财富黄金半小时）：
    1. 放量拉升 = 量比≥2.0 + 涨幅≥1.5% + 40-55°平滑拉升
    2. 放量跳水 = 量比≥2.0 + 跌幅≥1.5% + 跌破关键均线
    3. 缩量横盘 = 量比≤0.6 + 振幅≤0.5% 
    4. 无量偷袭 = 最后一笔大单拉升但总量比不高（对倒嫌疑）
    """

    # 阈值配置（可通过M55校准）
    # 注：角度阈值已针对1分钟K线校准（原30°阈值适用于5秒/15秒bar）
    SURGE_VOL_RATIO_MIN = 1.8         # 放量拉升最小量比
    SURGE_PRICE_CHANGE_MIN = 1.0      # 放量拉升最小涨幅(%)
    SURGE_ANGLE_MIN = 3.0             # 拉升最小角度（1分钟bar校准）
    SURGE_ANGLE_MAX = 45.0            # 拉升最大角度（太陡=偷袭）

    DIVE_VOL_RATIO_MIN = 1.5          # 跳水最小量比
    DIVE_PRICE_CHANGE_MAX = -1.0      # 跳水最大跌幅(%)

    SIDEWAYS_VOL_RATIO_MAX = 0.6      # 缩量横盘最大量比
    SIDEWAYS_AMPLITUDE_MAX = 0.5      # 缩量横盘最大振幅(%)

    FAKE_VOL_RATIO_MIN = 0.8          # 无量偷袭最低量比
    FAKE_LAST_BAR_RATIO = 2.5         # 最后一笔量比异常阈值

    @staticmethod
    def classify(
        tail_prices: List[float],      # 尾盘30分钟价格序列（1分钟K线）
        tail_volumes: List[float],     # 尾盘30分钟量序列
        prev_30_avg_volume: float,     # 前30分钟（14:00-14:30）平均量
        support_level: float = None,   # 关键支撑位（如20MA）
        sector_change: float = 0.0,    # 板块同期涨跌幅
    ) -> Tuple[TailPattern, Dict[str, float]]:
        """
        返回：(模式, 量化指标字典)
        """
        if len(tail_prices) < 5 or len(tail_volumes) < 5:
            return TailPattern.NORMAL, {}

        # 计算核心指标
        price_change_pct = (tail_prices[-1] / tail_prices[0] - 1) * 100
        avg_volume = sum(tail_volumes) / len(tail_volumes) if tail_volumes else 0
        volume_ratio = avg_volume / prev_30_avg_volume if prev_30_avg_volume > 0 else 1.0

        # 计算角度（线性回归斜率）
        n = len(tail_prices)
        x_mean = (n - 1) / 2
        y_mean = sum(tail_prices) / n
        slope = sum((i - x_mean) * (tail_prices[i] - y_mean) for i in range(n)) / \
                sum((i - x_mean) ** 2 for i in range(n)) if n > 1 else 0
        angle_deg = math.degrees(math.atan(slope / tail_prices[0] * 100))

        # 计算价格走势平滑度（R²）
        y_pred = [tail_prices[0] + slope * i for i in range(n)]
        ss_res = sum((tail_prices[i] - y_pred[i]) ** 2 for i in range(n))
        ss_tot = sum((tail_prices[i] - y_mean) ** 2 for i in range(n))
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        # 最后一根K线的量占比
        last_bar_vol_ratio = tail_volumes[-1] / avg_volume if avg_volume > 0 else 1.0

        # 振幅
        amplitude = (max(tail_prices) / min(tail_prices) - 1) * 100

        metrics = {
            'price_change_pct': round(price_change_pct, 3),
            'volume_ratio': round(volume_ratio, 3),
            'angle_deg': round(angle_deg, 2),
            'smoothness_r2': round(r_squared, 3),
            'amplitude': round(amplitude, 3),
            'last_bar_vol_ratio': round(last_bar_vol_ratio, 3),
        }

        # ── 分类逻辑 ──

        # 1. 放量跳水
        if (volume_ratio >= TailPatternClassifier.DIVE_VOL_RATIO_MIN and
            price_change_pct <= TailPatternClassifier.DIVE_PRICE_CHANGE_MAX):
            if support_level and tail_prices[-1] < support_level:
                return TailPattern.DIVE, metrics
            if price_change_pct <= -2.0:  # 大跌2%+
                return TailPattern.DIVE, metrics

        # 2. 放量拉升
        if (volume_ratio >= TailPatternClassifier.SURGE_VOL_RATIO_MIN and
            price_change_pct >= TailPatternClassifier.SURGE_PRICE_CHANGE_MIN):
            # 角度在合理范围内 = 真实拉升
            if TailPatternClassifier.SURGE_ANGLE_MIN <= angle_deg <= TailPatternClassifier.SURGE_ANGLE_MAX:
                return TailPattern.SURGE, metrics
            # 角度太陡 = 可能偷袭，需进一步辨别
            elif angle_deg > TailPatternClassifier.SURGE_ANGLE_MAX:
                # 如果量比也大且平滑，可能是强势抢筹
                if volume_ratio >= 3.0 and r_squared >= 0.85:
                    return TailPattern.SURGE, metrics
                else:
                    return TailPattern.FAKE_PUMP, metrics

        # 3. 缩量横盘
        if (volume_ratio <= TailPatternClassifier.SIDEWAYS_VOL_RATIO_MAX and
            amplitude <= TailPatternClassifier.SIDEWAYS_AMPLITUDE_MAX):
            return TailPattern.SIDEWAYS_SHRINK, metrics

        # 4. 无量偷袭（最后一笔异常放量但整体量比不高）
        if (last_bar_vol_ratio >= TailPatternClassifier.FAKE_LAST_BAR_RATIO and
            volume_ratio < TailPatternClassifier.FAKE_VOL_RATIO_MIN and
            price_change_pct > 0):
            return TailPattern.FAKE_PUMP, metrics

        return TailPattern.NORMAL, metrics


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 对倒行为识别器
# ═══════════════════════════════════════════════════════════════

class WashTradeDetector:
    """
    对倒/诱多行为深度识别

    识别维度：
    1. 大单对倒：成交量极大但价格几乎不动
    2. 1手买单刷单：大量1手买单制造活跃假象
    3. 尾盘脉冲后回落：最后几分钟直线拉升又回落
    4. 量价背离：量增价不增（出货信号）
    """

    @staticmethod
    def detect(
        tail_prices: List[float],
        tail_volumes: List[float],
        split_by_5min: bool = True,
    ) -> Tuple[float, List[str]]:
        """
        返回：(对倒风险分 0~1, 警告列表)
        """
        risk = 0.0
        warnings = []

        if len(tail_prices) < 10:
            return 0.5, ['数据不足·无法识别对倒']

        n = len(tail_prices)
        price_change = (tail_prices[-1] / tail_prices[0] - 1) * 100

        # 1. 量价背离检测（30分钟内）
        total_volume = sum(tail_volumes)
        # 计算归一化量价相关系数
        vol_norm = [v / (sum(tail_volumes) / n) for v in tail_volumes]
        price_norm = [p / tail_prices[0] for p in tail_prices]
        corr = WashTradeDetector._pearson_corr(vol_norm, price_norm)

        if corr < -0.3:  # 量价负相关
            risk += 0.35
            warnings.append('⚠️ 尾盘量价负相关（r={:.2f}）·量增价跌=出货'.format(corr))

        if corr < 0.1 and total_volume > sum(tail_volumes[:n//2]) * 2.5:
            risk += 0.25
            warnings.append('⚠️ 尾盘量能激增但价格几乎不动·对倒嫌疑')

        # 2. 脉冲回落检测
        # 找后1/3区间的最高点到收盘的回落
        later_start = n * 2 // 3
        later_prices = tail_prices[later_start:]
        peak = max(later_prices)
        peak_idx = later_prices.index(peak) + later_start
        decline_from_peak = (tail_prices[-1] / peak - 1) * 100

        if peak_idx >= n - 3 and decline_from_peak <= -0.8:
            risk += 0.20
            warnings.append('⚠️ 收盘前脉冲回落{:.1f}%·诱多出货'.format(abs(decline_from_peak)))

        # 3. 最后5分钟的异常
        last_5 = min(5, n)
        last_5_prices = tail_prices[-last_5:]
        last_5_vols = tail_volumes[-last_5:]
        last_5_change = (last_5_prices[-1] / last_5_prices[0] - 1) * 100
        last_5_vol_ratio = sum(last_5_vols) / (sum(tail_volumes) / n) / last_5

        # 最后一分钟量比异常高但价格不动
        if last_5_vol_ratio > 3.0 and abs(last_5_change) < 0.15:
            risk += 0.30
            warnings.append('⚠️ 收盘集合竞价放量{:.1f}x但价格微变·主力对倒制造量能'.format(last_5_vol_ratio))

        # 4. 整体量价效率比
        # 每1%涨幅消耗的成交量（效率低=对倒嫌疑）
        if abs(price_change) > 0.1:
            efficiency = abs(price_change) / (total_volume / 100000)  # 每十万量对应%涨幅
            if efficiency < 0.02 and price_change > 0:
                risk += 0.20
                warnings.append('⚠️ 量价效率极低（{:.3f}）·大单量小涨幅=对倒'.format(efficiency))

        return min(risk, 1.0), warnings

    @staticmethod
    def _pearson_corr(x: List[float], y: List[float]) -> float:
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(x[i] * y[i] for i in range(n))
        sum_x2 = sum(v ** 2 for v in x)
        sum_y2 = sum(v ** 2 for v in y)

        denom = math.sqrt(max(0.0, (n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2)))
        if denom == 0:
            return 0
        return (n * sum_xy - sum_x * sum_y) / denom


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 板块共振检测器
# ═══════════════════════════════════════════════════════════════

class SectorResonance:
    """
    板块联动共振检测
    核心逻辑：单票尾盘异动可能是噪音，需要板块确认

    共振等级：
    - 强共振（≥2只以上同板块同时放量拉升）= 真实性高
    - 弱共振（仅龙头异动）= 需谨慎
    - 无共振（孤票异动）= 高风险
    """

    @staticmethod
    def compute(
        stock_change: float,            # 本股尾盘涨幅
        sector_change: float,           # 板块同期涨幅
        sector_volume_ratio: float,      # 板块量比
        sector_up_count: int,           # 板块内上涨家数
        sector_total: int,              # 板块总家数
        sector_leader_change: float,    # 板块龙头涨幅
    ) -> Tuple[float, str]:
        """
        返回：(共振度 0~1, 共振描述)
        """
        score = 0.0
        signals = []

        # 1. 板块同向性
        if sector_change * stock_change > 0:
            score += 0.20
            signals.append('板块同向')

        # 2. 板块强度
        if abs(sector_change) > 0.5:
            score += 0.15
            signals.append('板块有效波动')

        # 3. 上涨家数占比
        up_ratio = sector_up_count / sector_total if sector_total > 0 else 0
        if up_ratio >= 0.6:
            score += 0.20
            signals.append('板块普涨')
        elif up_ratio >= 0.4:
            score += 0.10
            signals.append('板块分化')

        # 4. 龙头效应
        if sector_leader_change > 2.0:
            score += 0.15
            signals.append('龙头强势')

        # 5. 板块量能
        if sector_volume_ratio > 1.5:
            score += 0.15
            signals.append('板块放量')

        # 6. 本股vs板块相对强度（超额收益）
        relative_strength = stock_change - sector_change
        if relative_strength > 1.0:
            score += 0.10
            signals.append('个股强于板块')
        elif relative_strength > 0:
            score += 0.05
            signals.append('个股略强板块')

        # 7. 如果板块下跌但个股逆势拉升 → 加分（独立行情）
        if sector_change < 0 and stock_change > 1.0:
            score += 0.05
            signals.append('逆势拉升')

        desc = ' + '.join(signals) if signals else '无板块共振'
        return min(score, 1.0), desc


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 次日高开概率估计器
# ═══════════════════════════════════════════════════════════════

class GapUpEstimator:
    """
    次日高开概率密度估计

    基于尾盘信号 → 贝叶斯后验 → 高开概率
    来源：62%胜率研究 + 量化1号94.65%框架 + AurumQ-RL Main-Wave思路
    """

    # 基础先验概率（A股整体次日高开概率约35-40%）
    PRIOR_GAP_UP = 0.38
    PRIOR_LIMIT_UP = 0.03  # 基础涨停概率约3%

    # 似然比（条件概率比）
    LIKELIHOOD_RATIOS = {
        'surge_pattern': 2.5,       # 尾盘放量拉升 → 次日高开概率×2.5
        'above_20ma': 1.8,          # 站上20MA
        'sector_resonance': 1.6,    # 板块共振
        'not_high_pos': 1.3,        # 非高位
        'volume_surge': 1.5,        # 放量
        'smooth_rise': 1.4,         # 平滑拉升
        'no_wash_trade': 1.2,       # 无对倒
        'dive_pattern': 0.15,       # 跳水 → 大幅降低
        'fake_pump': 0.25,          # 偷袭 → 大幅降低
    }

    @staticmethod
    def estimate(
        pattern: TailPattern,
        volume_ratio: float,
        angle_deg: float,
        smoothness: float,
        sector_resonance: float,
        above_20ma: bool,
        not_high_pos: bool,
        no_wash: bool,
    ) -> Dict[str, float]:
        """
        贝叶斯更新：先验 × 似然比序列 → 后验概率
        """
        # 对数几率尺度计算（避免概率边界问题）
        def prob_to_log_odds(p):
            return math.log(p / (1 - p))

        def log_odds_to_prob(lo):
            return 1.0 / (1.0 + math.exp(-lo))

        # 初始对数几率
        log_odds = prob_to_log_odds(GapUpEstimator.PRIOR_GAP_UP)
        log_odds_limit = prob_to_log_odds(GapUpEstimator.PRIOR_LIMIT_UP)

        lr = GapUpEstimator.LIKELIHOOD_RATIOS

        # 模式信号
        if pattern == TailPattern.SURGE:
            log_odds += math.log(lr['surge_pattern'])
            log_odds_limit += math.log(lr['surge_pattern'] * 0.8)  # 涨停更苛刻
        elif pattern == TailPattern.DIVE:
            log_odds += math.log(lr['dive_pattern'])
            log_odds_limit += math.log(lr['dive_pattern'] * 0.3)
        elif pattern == TailPattern.FAKE_PUMP:
            log_odds += math.log(lr['fake_pump'])
            log_odds_limit += math.log(lr['fake_pump'] * 0.3)

        # 技术条件
        if above_20ma:
            log_odds += math.log(lr['above_20ma'])
            log_odds_limit += math.log(lr['above_20ma'] * 0.6)

        if not_high_pos:
            log_odds += math.log(lr['not_high_pos'])
            log_odds_limit += math.log(lr['not_high_pos'] * 0.7)

        if no_wash:
            log_odds += math.log(lr['no_wash_trade'])
            log_odds_limit += math.log(lr['no_wash_trade'] * 0.5)

        # 量化条件
        if sector_resonance >= 0.5:
            log_odds += math.log(lr['sector_resonance'])

        if volume_ratio >= 2.0:
            log_odds += math.log(lr['volume_surge'])

        if 30 <= angle_deg <= 55 and smoothness >= 0.7:
            log_odds += math.log(lr['smooth_rise'])

        gap_up_prob = log_odds_to_prob(log_odds)
        limit_up_prob = log_odds_to_prob(log_odds_limit)

        # 预期收益估计
        if pattern == TailPattern.SURGE:
            expected_return = gap_up_prob * 3.5 + limit_up_prob * 7.0  # 高开平均3.5% + 涨停溢价
        elif pattern == TailPattern.SIDEWAYS_SHRINK:
            expected_return = gap_up_prob * 1.5 + limit_up_prob * 5.0
        else:
            expected_return = gap_up_prob * 1.0

        # 风险调整
        if pattern == TailPattern.DIVE or pattern == TailPattern.FAKE_PUMP:
            expected_return *= 0.2

        return {
            'gap_up_prob': round(gap_up_prob, 4),
            'limit_up_prob': round(limit_up_prob, 4),
            'expected_return': round(expected_return, 3),
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 主引擎 — Tail30MinEngine
# ═══════════════════════════════════════════════════════════════

class Tail30MinEngine:
    """
    V13.1 M56 尾盘30分钟异动主引擎

    调用方式：
        engine = Tail30MinEngine()
        signal = engine.analyze(code, tail_1min_klines, sector_data, market_data)

    输入：30根1分钟K线（14:30-15:00）
    输出：Tail30MinSignal（含次日预判）
    """

    def __init__(self):
        self.classifier = TailPatternClassifier()
        self.wash_detector = WashTradeDetector()
        self.sector_resonance = SectorResonance()
        self.gap_estimator = GapUpEstimator()
        self.analysis_log: List[Dict] = []

    def analyze(
        self,
        code: str,
        name: str = '',
        tail_1min_prices: List[float] = None,
        tail_1min_volumes: List[float] = None,
        prev_30_avg_volume: float = 0.0,
        ma20_price: float = None,
        high_20d_price: float = None,
        sector_data: Dict = None,
        market_data: Dict = None,
    ) -> Tail30MinSignal:
        """
        完整尾盘分析

        Args:
            code: 股票代码
            name: 股票名称
            tail_1min_prices: 14:30-15:00 每分钟收盘价（30根）
            tail_1min_volumes: 14:30-15:00 每分钟成交量（30根）
            prev_30_avg_volume: 14:00-14:30平均每分钟成交量
            ma20_price: 20日均线价格
            high_20d_price: 20日最高价
            sector_data: 板块数据 {change, volume_ratio, up_count, total, leader_change}
            market_data: 大盘数据 {change, volume_ratio}
        """
        signal = Tail30MinSignal(code=code, name=name)
        sector_data = sector_data or {}
        market_data = market_data or {}

        if not tail_1min_prices or len(tail_1min_prices) < 5:
            signal.warnings.append('数据不足·需至少5根1分钟K线')
            signal.grade = SignalGrade.S_TRASH
            return signal

        # ── A. 模式分类 ──
        support = ma20_price
        pattern, metrics = self.classifier.classify(
            tail_1min_prices, tail_1min_volumes,
            prev_30_avg_volume, support,
            sector_data.get('change', 0)
        )
        signal.pattern = pattern
        signal.volume_ratio = metrics.get('volume_ratio', 1.0)
        signal.price_slope = metrics.get('price_change_pct', 0.0)
        signal.angle_deg = metrics.get('angle_deg', 0.0)
        signal.raw_data['pattern_metrics'] = metrics

        # ── B. 对倒检测 ──
        wash_risk, wash_warnings = self.wash_detector.detect(
            tail_1min_prices, tail_1min_volumes
        )
        signal.wash_trade_risk = wash_risk
        signal.warnings.extend(wash_warnings)

        # ── C. 板块共振 ──
        if sector_data:
            resonance, resonance_desc = self.sector_resonance.compute(
                stock_change=signal.price_slope,
                sector_change=sector_data.get('change', 0),
                sector_volume_ratio=sector_data.get('volume_ratio', 1.0),
                sector_up_count=sector_data.get('up_count', 0),
                sector_total=sector_data.get('total', 1),
                sector_leader_change=sector_data.get('leader_change', 0),
            )
            signal.sector_resonance = resonance
            signal.details.append(f'板块共振: {resonance_desc} ({resonance:.2f})')

        # ── D. 6大门槛过滤 ──
        current_price = tail_1min_prices[-1]

        # 1. 20MA之上
        if ma20_price and current_price > ma20_price:
            signal.above_20ma = True
            signal.details.append('✅ 站上20MA')
        else:
            signal.warnings.append('⚠️ 未站上20MA·趋势偏弱')

        # 2. 非高位（距20日高>5%）
        if high_20d_price:
            dist_from_high = (high_20d_price / current_price - 1) * 100
            if dist_from_high > 5.0:
                signal.not_high_position = True
                signal.details.append(f'✅ 非高位（距20日高{dist_from_high:.1f}%）')
            else:
                signal.warnings.append(f'⚠️ 接近20日高（仅{dist_from_high:.1f}%）')

        # 3. 板块红盘
        if sector_data.get('change', 0) > 0:
            signal.sector_red = True

        # 4. 不跳水
        if pattern != TailPattern.DIVE:
            signal.no_dive = True

        # 5. 量能达标
        if signal.volume_ratio >= 1.5:
            signal.volume_ok = True

        # 6. 无对倒
        if wash_risk < 0.35:
            signal.no_wash_trade = True
        else:
            signal.warnings.append('⚠️ 对倒风险较高')

        # ── E. 次日高开概率估计 ──
        prob_est = self.gap_estimator.estimate(
            pattern=pattern,
            volume_ratio=signal.volume_ratio,
            angle_deg=signal.angle_deg,
            smoothness=metrics.get('smoothness_r2', 0),
            sector_resonance=signal.sector_resonance,
            above_20ma=signal.above_20ma,
            not_high_pos=signal.not_high_position,
            no_wash=signal.no_wash_trade,
        )
        signal.gap_up_prob = prob_est['gap_up_prob']
        signal.limit_up_prob = prob_est['limit_up_prob']
        signal.expected_return = prob_est['expected_return']

        # ── F. 综合评分 ──
        signal.surge_score = self._compute_surge_score(
            pattern, signal.volume_ratio, signal.angle_deg,
            metrics.get('smoothness_r2', 0),
            signal.sector_resonance, wash_risk
        )

        # ── G. 信号定级 ──
        signal.grade = self._grade_signal(signal)

        # ── H. 风险评分 ──
        signal.risk_score = self._compute_risk(signal)

        self.analysis_log.append({
            'code': code, 'name': name,
            'pattern': signal.pattern.value[0],
            'grade': signal.grade.value[0],
            'gap_up_prob': signal.gap_up_prob,
            'expected_return': signal.expected_return,
            'timestamp': datetime.now().isoformat(),
        })

        return signal

    def _compute_surge_score(
        self,
        pattern: TailPattern,
        vol_ratio: float,
        angle: float,
        smoothness: float,
        resonance: float,
        wash_risk: float,
    ) -> float:
        """计算放量拉升综合评分 0~1"""
        if pattern == TailPattern.DIVE:
            return 0.0
        if pattern == TailPattern.FAKE_PUMP:
            return 0.1

        score = 0.0

        # 模式基础分
        if pattern == TailPattern.SURGE:
            score = 0.55
        elif pattern == TailPattern.SIDEWAYS_SHRINK:
            score = 0.30
        elif pattern == TailPattern.NORMAL:
            score = 0.15

        # 量比加分
        if vol_ratio >= 3.0:
            score += 0.15
        elif vol_ratio >= 2.0:
            score += 0.10
        elif vol_ratio >= 1.5:
            score += 0.05

        # 角度加分（1分钟bar校准）
        if 3 <= angle <= 45:
            score += 0.10
        elif 1 <= angle < 3:
            score += 0.05

        # 平滑度加分
        if smoothness >= 0.85:
            score += 0.10
        elif smoothness >= 0.65:
            score += 0.05

        # 板块共振加分
        score += resonance * 0.10

        # 对倒扣分
        score -= wash_risk * 0.20

        return max(0.0, min(1.0, score))

    def _grade_signal(self, signal: Tail30MinSignal) -> SignalGrade:
        """信号等级评定"""
        # 垃圾信号：跳水/偷袭/对倒严重
        if signal.pattern == TailPattern.DIVE:
            return SignalGrade.S_TRASH
        if signal.pattern == TailPattern.FAKE_PUMP and signal.wash_trade_risk > 0.4:
            return SignalGrade.S_TRASH
        if signal.wash_trade_risk > 0.7:
            return SignalGrade.S_TRASH

        # 计数通关的门槛
        gates = [signal.above_20ma, signal.not_high_position,
                 signal.sector_red, signal.no_dive, signal.volume_ok, signal.no_wash_trade]
        passed = sum(gates)

        # 铂金：放量拉升 + 6门全过 + 板块共振
        if (signal.pattern == TailPattern.SURGE and
            passed >= 6 and signal.sector_resonance >= 0.5):
            return SignalGrade.S_PLATINUM

        # 黄金：放量拉升/缩量横盘 + 5门+
        if signal.pattern in [TailPattern.SURGE, TailPattern.SIDEWAYS_SHRINK] and passed >= 5:
            return SignalGrade.S_GOLD

        # 白银：有异动但缺确认
        if signal.pattern != TailPattern.NORMAL and passed >= 3:
            return SignalGrade.S_SILVER

        # 黄铜：弱信号
        if passed >= 2:
            return SignalGrade.S_BRONZE

        return SignalGrade.S_TRASH

    def _compute_risk(self, signal: Tail30MinSignal) -> float:
        """综合风险评分 0~1"""
        risk = 0.0
        risk += signal.wash_trade_risk * 0.35
        risk += (1 - signal.sector_resonance) * 0.20
        risk += (0 if signal.above_20ma else 0.10)
        risk += (0 if signal.not_high_position else 0.15)
        risk += (0 if signal.no_dive else 0.10)
        risk += (0 if signal.volume_ok else 0.10)
        return min(1.0, risk)

    # ── 批量分析 ──
    def batch_analyze(
        self,
        stocks_data: List[Dict],
        sector_map: Dict[str, Dict] = None,
    ) -> List[Tail30MinSignal]:
        """批量尾盘分析"""
        results = []
        for stock in stocks_data:
            sector_info = None
            if sector_map and stock.get('sector'):
                sector_info = sector_map.get(stock['sector'])
            signal = self.analyze(
                code=stock.get('code', ''),
                name=stock.get('name', ''),
                tail_1min_prices=stock.get('tail_prices'),
                tail_1min_volumes=stock.get('tail_volumes'),
                prev_30_avg_volume=stock.get('prev_30_avg_vol', 0),
                ma20_price=stock.get('ma20'),
                high_20d_price=stock.get('high_20d'),
                sector_data=sector_info,
                market_data=stock.get('market_data'),
            )
            results.append(signal)
        return results

    # ── 报告生成 ──
    def generate_report(self, signals: List[Tail30MinSignal]) -> str:
        """生成尾盘分析报告"""
        lines = [
            '=' * 60,
            f'V13.1 M56 尾盘30分钟异动分析报告',
            f'分析时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'分析股票数：{len(signals)}',
            '=' * 60,
        ]

        # 按等级排序
        grade_order = {SignalGrade.S_PLATINUM: 0, SignalGrade.S_GOLD: 1,
                       SignalGrade.S_SILVER: 2, SignalGrade.S_BRONZE: 3,
                       SignalGrade.S_TRASH: 4}
        sorted_signals = sorted(signals, key=lambda s: grade_order.get(s.grade, 99))

        for s in sorted_signals:
            if s.grade == SignalGrade.S_TRASH and s.pattern == TailPattern.NORMAL:
                continue  # 跳过无信号

            lines.append(f"\n{'─' * 50}")
            lines.append(f"【{s.code} {s.name}】 等级：{s.grade.value[0]} | 模式：{s.pattern.value[0]}")
            lines.append(f"  放量评分：{s.surge_score:.2f} | 量比：{s.volume_ratio:.2f}x")
            lines.append(f"  板块共振：{s.sector_resonance:.2f} | 对倒风险：{s.wash_trade_risk:.2f}")
            lines.append(f"  次日高开概率：{s.gap_up_prob:.1%} | 涨停概率：{s.limit_up_prob:.1%}")
            lines.append(f"  预期收益：{s.expected_return:+.2f}% | 风险评分：{s.risk_score:.2f}")
            if s.details:
                for d in s.details:
                    lines.append(f"  {d}")
            if s.warnings:
                for w in s.warnings:
                    lines.append(f"  {w}")

        lines.append(f"\n{'─' * 50}")
        lines.append(f"铂金：{sum(1 for s in sorted_signals if s.grade == SignalGrade.S_PLATINUM)}")
        lines.append(f"黄金：{sum(1 for s in sorted_signals if s.grade == SignalGrade.S_GOLD)}")
        lines.append(f"白银：{sum(1 for s in sorted_signals if s.grade == SignalGrade.S_SILVER)}")
        lines.append(f"黄铜：{sum(1 for s in sorted_signals if s.grade == SignalGrade.S_BRONZE)}")
        lines.append('=' * 60)

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 6: 导出函数
# ═══════════════════════════════════════════════════════════════

__all__ = [
    'TailPattern', 'SignalGrade', 'Tail30MinSignal',
    'TailPatternClassifier', 'WashTradeDetector',
    'SectorResonance', 'GapUpEstimator',
    'Tail30MinEngine',
]
