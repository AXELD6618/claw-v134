#!/usr/bin/env python3
"""
V13.0 M46 贝叶斯概率引擎（命中率提升增强版）
=============================================
Phase 2 能力跃升：命中率 38% → 45%

核心增强：
1. 行业贝叶斯先验——根据不同行业板块赋予差异化先验概率
2. 动量自适应衰减——根据近期市场节奏动态调整动量因子权重
3. 信号二次确认——M48(尾盘)+M49(VPRI)+M51(主力)三方共振加权
4. Sigmoid映射参数优化——k和x₀根据市场状态自适应调整

命中率提升路径：
- 先验概率引入 → +2pp
- 动量自适应衰减 → +2pp
- 三方共振二次确认 → +2pp
- Sigmoid参数自适应 → +1pp
─────────────────────────
总计预期：38% → 45% (+7pp)
"""

import json
import math
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


# ═══════════════════════════════════════════════
# 行业贝叶斯先验配置
# ═══════════════════════════════════════════════

class IndustryPrior(Enum):
    """行业先验概率——基于历史涨停/大涨统计"""
    SEMICONDUCTOR = ('半导体设备/材料', 0.35, 0.12)       # (名称, 先验成功概率, 波动率)
    AI_COMPUTING = ('AI算力/服务器', 0.32, 0.14)
    QUANTUM_PHOTONICS = ('量子/光子', 0.28, 0.18)
    SPECIAL_TRANSFORMER = ('特高压/电力设备', 0.30, 0.11)
    NEW_ENERGY = ('新能源/储能', 0.25, 0.15)
    ROBOTICS = ('人形机器人', 0.33, 0.16)
    PHARMA = ('医药/创新药', 0.22, 0.13)
    CONSUMER = ('消费/食品饮料', 0.20, 0.10)
    FINANCE = ('金融/券商', 0.18, 0.12)
    MATERIAL = ('有色/化工', 0.24, 0.14)
    DEFENSE = ('军工/航天', 0.26, 0.15)
    AUTOMOBILE = ('汽车/零部件', 0.23, 0.13)
    DEFAULT = ('通用', 0.25, 0.15)

    @classmethod
    def get_prior(cls, industry: str) -> Tuple[float, float]:
        """获取先验概率和波动率"""
        for member in cls:
            if member.value[0] in industry or industry in member.value[0]:
                return member.value[1], member.value[2]
        return cls.DEFAULT.value[1], cls.DEFAULT.value[2]


# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

@dataclass
class M46Config:
    """M46贝叶斯概率引擎配置"""

    # Sigmoid映射参数
    sigmoid_k: float = 8.0           # 陡峭度（越大越激进）
    sigmoid_x0: float = 0.55         # 偏移点（评分中心）
    sigmoid_k_range: Tuple[float, float] = (4.0, 12.0)  # k的允许范围
    sigmoid_x0_range: Tuple[float, float] = (0.45, 0.65)  # x0的允许范围

    # 先验权重
    prior_weight: float = 0.20        # 行业先验在最终概率中的权重
    prior_weight_range: Tuple[float, float] = (0.10, 0.30)

    # 动量自适应衰减
    momentum_window: int = 20          # 动量计算窗口（交易日）
    momentum_decay_factor: float = 0.92  # 动量衰减因子（<1表示衰减）

    # 三方共振
    resonance_threshold: float = 0.60  # 共振阈值（三方信号都>阈值才算共振）
    resonance_bonus: float = 0.10      # 共振加权加分

    # 置信度分类
    high_confidence: float = 0.70
    medium_confidence: float = 0.45
    # < 0.45 为低置信度

    # 信号二次确认
    confirmation_weight: float = 0.15  # 二次确认在总分中的权重


class M46BayesianEngine:
    """M46 贝叶斯概率引擎 V13.0（增强版）"""

    def __init__(self, config: M46Config = None):
        self.config = config or M46Config()
        self.calibration_history: List[dict] = []
        self.market_state: dict = {}  # 市场状态缓存
        self.momentum_cache: Dict[str, float] = {}  # 个股东量缓存

    # ═══════════════════════════════════════════════
    # 核心：Sigmoid映射 → 贝叶斯后验概率
    # ═══════════════════════════════════════════════

    def sigmoid_map(self, score: float, k: float = None, x0: float = None) -> float:
        """
        Sigmoid映射：原始评分 → 概率
        P = 1 / (1 + e^(-k(S - x₀)))
        """
        k = k or self.config.sigmoid_k
        x0 = x0 or self.config.sigmoid_x0
        return 1.0 / (1.0 + math.exp(-k * (score - x0)))

    def update_sigmoid_params(self, market_volatility: float, market_trend: float):
        """
        根据市场状态自适应调整Sigmoid参数
        - 高波动市场：降低k（更保守）
        - 趋势向上：升高x₀（提高门槛）
        - 趋势向下：降低x₀（放宽门槛，捕捉超跌反弹）
        """
        k = self.config.sigmoid_k
        x0 = self.config.sigmoid_x0

        # 波动率调节k
        if market_volatility > 0.03:   # 日波动>3%，高波动
            k = max(self.config.sigmoid_k_range[0], k - 0.5)
        elif market_volatility < 0.015:  # 日波动<1.5%，低波动
            k = min(self.config.sigmoid_k_range[1], k + 0.5)

        # 趋势调节x₀
        if market_trend > 0.02:   # 强势上涨
            x0 = min(self.config.sigmoid_x0_range[1], x0 + 0.02)
        elif market_trend < -0.02:  # 弱势下跌
            x0 = max(self.config.sigmoid_x0_range[0], x0 - 0.02)

        return k, x0

    # ═══════════════════════════════════════════════
    # 增强1：行业贝叶斯先验
    # ═══════════════════════════════════════════════

    def get_industry_prior(self, industry: str, sub_sector: str = '') -> float:
        """
        获取行业先验成功概率
        使用贝叶斯框架：后验 ∝ 似然 × 先验
        """
        prior_prob, prior_vol = IndustryPrior.get_prior(industry)

        # 子赛道微调
        sector_adjustments = {
            '先进封装': 0.04,    # 当前热点，调高先验
            'DrMOS': 0.03,
            '人形机器人关节': 0.03,
            '液冷': 0.02,
            'PCB': 0.01,
        }

        for keyword, adj in sector_adjustments.items():
            if keyword in sub_sector or keyword in industry:
                prior_prob += adj
                break

        return min(prior_prob, 0.50)  # 上限50%

    def bayesian_update(
        self,
        prior_prob: float,
        likelihood: float,
        prior_weight: float = None,
    ) -> float:
        """
        贝叶斯更新：先验 × 似然 → 后验
        posterior = (prior^α × likelihood^(1-α)) / normalization
        简化为加权平均
        """
        w = prior_weight or self.config.prior_weight
        posterior = w * prior_prob + (1.0 - w) * likelihood
        return posterior

    # ═══════════════════════════════════════════════
    # 增强2：动量自适应衰减
    # ═══════════════════════════════════════════════

    def compute_momentum_decay(self, code: str, price_series: List[float]) -> float:
        """
        计算动量自适应衰减系数
        近期动量越强 → 衰减越大（防止追高）
        计算公式：衰减 = 1 - |20日动量| × factor
        """
        if len(price_series) < self.config.momentum_window:
            return 1.0  # 数据不足，不衰减

        # 20日动量
        momentum = (price_series[-1] - price_series[0]) / price_series[0]

        # 绝对动量越大，衰减越大
        abs_momentum = abs(momentum)

        if abs_momentum > 0.30:  # 30%以上涨幅，强衰减
            decay = 0.75
        elif abs_momentum > 0.20:
            decay = 0.82
        elif abs_momentum > 0.10:
            decay = 0.90
        else:
            decay = 1.0  # 低动量，不衰减

        self.momentum_cache[code] = decay
        return decay

    # ═══════════════════════════════════════════════
    # 增强3：三方共振二次确认
    # ═══════════════════════════════════════════════

    def check_resonance(
        self,
        m48_signal: float,   # M48尾盘特征信号(0~1)
        m49_signal: float,   # M49 VPRI量价共振(0~1)
        m51_signal: float,   # M51主力意图(0~1)
    ) -> Tuple[bool, float]:
        """
        三方共振检查
        返回：(是否共振, 共振强度)
        """
        signals = [m48_signal, m49_signal, m51_signal]
        threshold = self.config.resonance_threshold

        # 统计超过阈值的信号数
        above_threshold = sum(1 for s in signals if s >= threshold)

        if above_threshold >= 3:
            # 三方共振 → 强信号
            resonance_strength = sum(signals) / 3
            return True, min(resonance_strength + self.config.resonance_bonus, 1.0)
        elif above_threshold >= 2:
            # 两方共振 → 中等信号
            resonance_strength = sum(signals) / 3
            return True, resonance_strength
        else:
            # 无共振或单信号 → 信号质量低
            return False, sum(signals) / 3 * 0.7  # 惩罚

    # ═══════════════════════════════════════════════
    # 综合评分主流程
    # ═══════════════════════════════════════════════

    def compute_probability(
        self,
        code: str,
        name: str,
        industry: str,
        sub_sector: str,
        seven_weight_score: float,    # 7权重融合V2评分(0~1)
        m48_signal: float = 0.5,      # M48尾盘特征
        m49_signal: float = 0.5,      # M49 VPRI量价共振
        m51_signal: float = 0.5,      # M51主力意图(已过滤)
        price_series: List[float] = None,
        market_volatility: float = 0.02,
        market_trend: float = 0.0,
    ) -> dict:
        """
        综合贝叶斯概率计算

        返回完整概率分析结果，兼容 V13.0 data.json
        """

        # ── Step 1: Sigmoid映射评分 → 概率 ──
        k, x0 = self.update_sigmoid_params(market_volatility, market_trend)
        base_prob = self.sigmoid_map(seven_weight_score, k, x0)

        # ── Step 2: 行业贝叶斯先验 ──
        prior = self.get_industry_prior(industry, sub_sector)
        posterior_prob = self.bayesian_update(prior, base_prob)

        # ── Step 3: 动量自适应衰减 ──
        momentum_decay = 1.0
        if price_series:
            momentum_decay = self.compute_momentum_decay(code, price_series)

        # ── Step 4: 三方共振确认 ──
        is_resonance, resonance_strength = self.check_resonance(m48_signal, m49_signal, m51_signal)

        # ── Step 5: 最终概率合成 ──
        # 权重分配：7权重评分55% + 先验20% + 共振确认15% + 动量调制10%
        final_prob = (
            0.55 * posterior_prob +
            0.20 * prior +
            0.15 * resonance_strength +
            0.10 * momentum_decay * base_prob
        ) / (0.55 + 0.20 + 0.15 + 0.10)  # 归一化

        final_prob *= momentum_decay  # 再乘动量衰减

        # 限制在有效范围内
        final_prob = max(0.01, min(0.99, final_prob))

        # ── Step 6: 置信度分类 ──
        if final_prob >= self.config.high_confidence:
            confidence = '高'
        elif final_prob >= self.config.medium_confidence:
            confidence = '中'
        else:
            confidence = '低'

        return {
            'code': code,
            'name': name,
            'industry': industry,
            'sub_sector': sub_sector,
            'base_probability': round(base_prob, 4),
            'industry_prior': round(prior, 4),
            'posterior_probability': round(posterior_prob, 4),
            'momentum_decay': round(momentum_decay, 4),
            'is_resonance': is_resonance,
            'resonance_strength': round(resonance_strength, 4),
            'final_probability': round(final_prob, 4),
            'confidence': confidence,
            'sigmoid_params': {'k': round(k, 2), 'x0': round(x0, 2)},
            'signal_valid': final_prob >= self.config.medium_confidence,  # P≥0.45为有效信号
        }

    def batch_compute(
        self,
        stocks: List[dict],
        market_volatility: float = 0.02,
        market_trend: float = 0.0,
    ) -> List[dict]:
        """
        批量计算多只股票概率
        stocks: [{'code', 'name', 'industry', 'sub_sector', 'seven_weight_score', ...}]
        """
        results = []
        for stock in stocks:
            result = self.compute_probability(
                code=stock.get('code', ''),
                name=stock.get('name', ''),
                industry=stock.get('industry', '通用'),
                sub_sector=stock.get('sub_sector', ''),
                seven_weight_score=stock.get('seven_weight_score', 0.5),
                m48_signal=stock.get('m48_signal', 0.5),
                m49_signal=stock.get('m49_signal', 0.5),
                m51_signal=stock.get('m51_signal', 0.5),
                price_series=stock.get('price_series'),
                market_volatility=market_volatility,
                market_trend=market_trend,
            )
            results.append(result)

        # 按最终概率排序
        results.sort(key=lambda x: x['final_probability'], reverse=True)
        return results

    def get_hit_rate_estimate(self, results: List[dict], confidence_filter: str = None) -> float:
        """
        估算命中率
        基于历史校准数据+当前置信度分档估算
        """
        if confidence_filter:
            results = [r for r in results if r['confidence'] == confidence_filter]

        if not results:
            return 0.0

        # 置信度档位 → 历史命中率映射（来自M55校准）
        confidence_hit_rate = {
            '高': 0.55,   # 高置信度历史命中率约55%
            '中': 0.35,   # 中置信度历史命中率约35%
            '低': 0.18,   # 低置信度历史命中率约18%
        }

        total = 0.0
        for r in results:
            cr = confidence_hit_rate.get(r['confidence'], 0.25)
            total += cr * r['final_probability']

        return round(total / len(results), 4)


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def run_m46_probability(stocks_data: List[dict],
                        market_volatility: float = 0.02,
                        market_trend: float = 0.0) -> dict:
    """
    M46贝叶斯概率计算快捷入口
    """
    engine = M46BayesianEngine()
    results = engine.batch_compute(stocks_data, market_volatility, market_trend)

    # 统计
    high_count = sum(1 for r in results if r['confidence'] == '高')
    mid_count = sum(1 for r in results if r['confidence'] == '中')
    low_count = sum(1 for r in results if r['confidence'] == '低')
    valid_count = sum(1 for r in results if r['signal_valid'])
    estimated_hit_rate = engine.get_hit_rate_estimate(results)

    return {
        'results': results,
        'summary': {
            'total': len(results),
            'high_confidence': high_count,
            'medium_confidence': mid_count,
            'low_confidence': low_count,
            'valid_signals': valid_count,
            'target_hit_rate': 0.45,
            'estimated_hit_rate': estimated_hit_rate,
            'hit_rate_gap': round(0.45 - estimated_hit_rate, 4) if estimated_hit_rate < 0.45 else 0,
        },
    }


if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 M46 贝叶斯概率引擎（命中率提升增强版）")
    print("=" * 60)
    print("目标: 命中率 38% → 45% (+7pp)")
    print("增强: 行业先验 + 动量自适应 + 三方共振 + Sigmoid自适应")
    print("=" * 60)

    # 自测
    test_stocks = [
        {
            'code': '002371', 'name': '北方华创',
            'industry': '半导体设备/材料', 'sub_sector': '先进封装',
            'seven_weight_score': 0.78,
            'm48_signal': 0.72, 'm49_signal': 0.68, 'm51_signal': 0.65,
            'price_series': [380 + i*2 for i in range(20)],
        },
        {
            'code': '002851', 'name': '麦格米特',
            'industry': 'AI算力/服务器', 'sub_sector': 'DrMOS',
            'seven_weight_score': 0.82,
            'm48_signal': 0.75, 'm49_signal': 0.70, 'm51_signal': 0.60,
            'price_series': [45 + i*0.5 for i in range(20)],
        },
        {
            'code': '002028', 'name': '思源电气',
            'industry': '特高压/电力设备', 'sub_sector': 'GIS/直流断路器',
            'seven_weight_score': 0.65,
            'm48_signal': 0.55, 'm49_signal': 0.50, 'm51_signal': 0.40,
            'price_series': [28 + i*0.2 for i in range(20)],
        },
    ]

    result = run_m46_probability(test_stocks)
    print(f"\n📊 M46概率计算结果:")
    print(f"摘要: {json.dumps(result['summary'], ensure_ascii=False, indent=2)}")
    for r in result['results']:
        print(f"  {r['name']}({r['code']}): P={r['final_probability']:.3f} [{r['confidence']}] "
              f"{'✅有效' if r['signal_valid'] else '❌无效'}")
