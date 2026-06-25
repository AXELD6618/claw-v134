#!/usr/bin/env python3
"""
V13.0 7权重融合V2引擎
======================
替代V12.8的5权重体系，全面升级为七维共振

7权重定义：
W1 催化因子(Catalyst)    20% - M42 LLM事件+政策驱动
W2 政策因子(Policy)      10% - 产业政策+监管动态
W3 板块因子(Sector)     15% - M50板块联动+龙头效应
W4 动量因子(Momentum)    20% - M49 VPRI量价共振+技术形态
W5 资金因子(Capital)     15% - M51主力意图+龙虎榜+北向资金
W6 舆情因子(Sentiment)   10% - M16舆情+M42 LLM事件
W7 技术因子(Technical)   10% - M48尾盘特征+M52情绪修正

权重自适应：根据市场状态(M52情绪周期)和M55校准结果动态调整
"""

import json
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class WeightConfig:
    """权重配置"""
    # 默认权重（总和=1.0）
    W_CATALYST: float = 0.20
    W_POLICY: float = 0.10
    W_SECTOR: float = 0.15
    W_MOMENTUM: float = 0.20
    W_CAPITAL: float = 0.15
    W_SENTIMENT: float = 0.10
    W_TECHNICAL: float = 0.10

    @property
    def weights(self) -> dict:
        return {
            'W1_催化': self.W_CATALYST,
            'W2_政策': self.W_POLICY,
            'W3_板块': self.W_SECTOR,
            'W4_动量': self.W_MOMENTUM,
            'W5_资金': self.W_CAPITAL,
            'W6_舆情': self.W_SENTIMENT,
            'W7_技术': self.W_TECHNICAL,
        }

    def set_weights(self, new_weights: dict):
        """批量设置权重（自动归一化）"""
        for key, value in new_weights.items():
            setattr(self, key, value)
        self._normalize()

    def _normalize(self):
        """归一化确保总和=1.0"""
        total = sum(self.weights.values())
        if total > 0 and abs(total - 1.0) > 0.001:
            scale = 1.0 / total
            for key in self.weights:
                setattr(self, key, round(getattr(self, key) * scale, 4))


class SevenWeightFusionV2:
    """7权重融合V2引擎"""

    def __init__(self, config: WeightConfig = None):
        self.config = config or WeightConfig()
        self.market_sentiment = 'neutral'  # panic/fear/neutral/greed/extreme

    def adjust_weights_by_sentiment(self, sentiment_stage: str):
        """
        根据M52情绪周期调整权重
        """
        self.market_sentiment = sentiment_stage

        adjustments = {
            '恐慌': {
                'W5_资金': 0.05,   # 恐慌期加重资金因子（寻找护盘/抄底资金）
                'W4_动量': -0.05,  # 动量失效
                'W7_技术': 0.03,   # 技术面超卖信号更重要
                'W1_催化': -0.03,
            },
            '犹豫': {
                'W4_动量': -0.03,  # 动量减弱
                'W6_舆情': 0.03,   # 舆情更敏感
            },
            '贪婪': {
                'W4_动量': 0.05,   # 动量强化
                'W5_资金': -0.03,  # 资金过热
                'W7_技术': -0.02,
            },
            '极端': {
                'W5_资金': -0.08,  # 极度减仓
                'W3_板块': -0.05,
                'W7_技术': 0.05,   # 密切关注技术拐点
                'W6_舆情': 0.08,   # 极端情绪时舆情权重最高
            },
        }

        adj = adjustments.get(sentiment_stage, {})
        for key, delta in adj.items():
            current = getattr(self.config, key, 0)
            setattr(self.config, key, max(0.05, min(0.35, current + delta)))

        self.config._normalize()

    def compute_score(
        self,
        catalyst_score: float,     # W1: M42 LLM事件+政策
        policy_score: float,       # W2: 产业政策
        sector_score: float,       # W3: M50板块联动
        momentum_score: float,     # W4: M49 VPRI+技术形态
        capital_score: float,      # W5: M51主力+龙虎榜+北向
        sentiment_score: float,    # W6: M16舆情+M42 LLM
        technical_score: float,    # W7: M48尾盘+M52情绪
        m52_sentiment_coeff: float = 1.0,  # M52情绪系数(0.5~1.0)
    ) -> dict:
        """
        计算7权重融合评分

        返回：综合评分+各维度分解
        """

        total = (
            self.config.W_CATALYST * catalyst_score +
            self.config.W_POLICY * policy_score +
            self.config.W_SECTOR * sector_score +
            self.config.W_MOMENTUM * momentum_score +
            self.config.W_CAPITAL * capital_score +
            self.config.W_SENTIMENT * sentiment_score +
            self.config.W_TECHNICAL * technical_score
        )

        # M52情绪周期修正
        total *= m52_sentiment_coeff
        total = min(total, 1.0)

        return {
            'total_score': round(total, 4),
            'weights': self.config.weights,
            'dimensions': {
                'W1_催化': round(self.config.W_CATALYST * catalyst_score, 4),
                'W2_政策': round(self.config.W_POLICY * policy_score, 4),
                'W3_板块': round(self.config.W_SECTOR * sector_score, 4),
                'W4_动量': round(self.config.W_MOMENTUM * momentum_score, 4),
                'W5_资金': round(self.config.W_CAPITAL * capital_score, 4),
                'W6_舆情': round(self.config.W_SENTIMENT * sentiment_score, 4),
                'W7_技术': round(self.config.W_TECHNICAL * technical_score, 4),
            },
            'm52_coefficient': m52_sentiment_coeff,
            'market_sentiment': self.market_sentiment,
        }

    def batch_compute(self, stocks: List[dict], sentiment_coeff: float = 1.0) -> List[dict]:
        """批量计算"""
        results = []
        for s in stocks:
            score = self.compute_score(
                catalyst_score=s.get('catalyst', 0.5),
                policy_score=s.get('policy', 0.5),
                sector_score=s.get('sector', 0.5),
                momentum_score=s.get('momentum', 0.5),
                capital_score=s.get('capital', 0.5),
                sentiment_score=s.get('sentiment', 0.5),
                technical_score=s.get('technical', 0.5),
                m52_sentiment_coeff=sentiment_coeff,
            )
            results.append({
                'code': s.get('code', ''),
                'name': s.get('name', ''),
                **score,
            })

        results.sort(key=lambda x: x['total_score'], reverse=True)
        return results


if __name__ == '__main__':
    engine = SevenWeightFusionV2()
    print("V13.0 7权重融合V2引擎就绪 ✅")
    print(f"默认权重: {json.dumps(engine.config.weights, ensure_ascii=False, indent=2)}")
    engine.adjust_weights_by_sentiment('贪婪')
    print(f"贪婪调整: {json.dumps(engine.config.weights, ensure_ascii=False, indent=2)}")
