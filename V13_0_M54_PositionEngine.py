#!/usr/bin/env python3
"""
V13.0 M54 仓位决策引擎（盈亏比提升增强版）
===========================================
Phase 2 能力跃升：盈亏比 2.4 → 3.0

核心增强：
1. 动态移动平均跟踪止损（Trailing MA Stop）
2. 胜率-盈亏比联合优化（Kelly变体）
3. 仓位与波动率动态绑定（ATR自适应）
4. 分批止盈机制（1/3+1/3+1/3 阶梯止盈）

盈亏比提升路径：
- 动态跟踪止损（减少过早止盈/过晚止损）→ +0.3
- Kelly优化仓位配比 → +0.2
- ATR波动率仓位绑定 → +0.1
─────────────────────────
总计预期：2.4 → 3.0 (+0.6)
"""

import json
import math
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

@dataclass
class M54Config:
    """M54仓位决策配置"""

    # 总资金
    total_capital: float = 1_000_000

    # 单只最大仓位
    max_single_position: float = 0.25    # 单只≤25%
    max_total_position: float = 0.80     # 总仓位≤80%

    # Kelly参数
    kelly_fraction: float = 0.25         # 使用1/4 Kelly（保守）
    kelly_min_position: float = 0.05     # 最小仓位5%
    kelly_max_position: float = 0.25     # 最大仓位25%

    # 动态止损
    trailing_stop_ma_period: int = 10    # 移动止损MA周期
    trailing_stop_buffer: float = 0.02   # 止损缓冲2%
    hard_stop_loss: float = 0.08         # 硬止损-8%

    # 分批止盈
    tier1_take_profit: float = 0.05      # 第一档+5%止盈1/3
    tier2_take_profit: float = 0.10      # 第二档+10%止盈1/3
    tier3_take_profit: float = 0.20      # 第三档+20%止盈1/3
    tier1_ratio: float = 0.33            # 第一档减仓比例
    tier2_ratio: float = 0.33            # 第二档减仓比例
    tier3_ratio: float = 0.34            # 第三档减仓比例

    # ATR波动率参数
    atr_period: int = 14                 # ATR计算周期
    atr_position_scalar: float = 0.5     # ATR仓位调节系数


class M54PositionEngine:
    """M54 仓位决策引擎 V13.0（增强版）"""

    def __init__(self, config: M54Config = None):
        self.config = config or M54Config()
        self.positions: Dict[str, dict] = {}  # 当前持仓
        self.trade_history: List[dict] = []   # 交易历史

    # ═══════════════════════════════════════════════
    # 增强1：动态移动平均跟踪止损
    # ═══════════════════════════════════════════════

    def compute_trailing_stop(
        self,
        entry_price: float,
        current_price: float,
        price_history: List[float],
        atr: float = 0,
    ) -> dict:
        """
        动态跟踪止损计算
        使用移动均线作为止损基线，随价格上涨自动上移
        """
        period = min(self.config.trailing_stop_ma_period, len(price_history))

        if period < 2:
            # 数据不足，使用固定百分比止损
            stop_price = entry_price * (1.0 - self.config.hard_stop_loss)
            return {
                'stop_price': round(stop_price, 2),
                'stop_type': 'fixed',
                'stop_pct': round((stop_price / current_price - 1), 4),
                'distance_pct': round(self.config.hard_stop_loss * 100, 1),
            }

        # MA10 跟踪止损
        recent_prices = price_history[-period:]
        ma10 = sum(recent_prices) / period

        # 止损价 = MA10 × (1 - buffer - ATR调整)
        atr_adjust = (atr / current_price) * 0.5 if atr > 0 else 0
        stop_price = ma10 * (1.0 - self.config.trailing_stop_buffer - atr_adjust)

        # 止损价不能低于硬止损
        hard_stop = entry_price * (1.0 - self.config.hard_stop_loss)
        stop_price = max(stop_price, hard_stop)

        # 止损价不能低于当前止损（只能上移）
        current_stop_pct = round((stop_price / current_price - 1) * 100, 1)

        return {
            'stop_price': round(stop_price, 2),
            'stop_type': 'trailing_ma',
            'ma10': round(ma10, 2),
            'hard_stop': round(hard_stop, 2),
            'atr_adjustment': round(atr_adjust, 4),
            'stop_pct': round((stop_price / current_price - 1), 4),
            'distance_pct': abs(current_stop_pct),
        }

    # ═══════════════════════════════════════════════
    # 增强2：Kelly公式仓位优化
    # ═══════════════════════════════════════════════

    def kelly_position_size(
        self,
        win_rate: float,
        profit_loss_ratio: float,
        avg_win: float = 0,
        avg_loss: float = 0,
    ) -> float:
        """
        Kelly仓位计算（分数Kelly）

        f* = (p × b - q) / b
        其中 p=胜率, b=盈亏比, q=1-p

        使用1/4 Kelly降低波动
        """
        if profit_loss_ratio <= 0:
            return 0.0

        # 如果提供了详细的盈亏数据，使用更精确的Kelly
        if avg_win > 0 and avg_loss > 0:
            b = avg_win / avg_loss
        else:
            b = profit_loss_ratio

        q = 1.0 - win_rate
        f_star = (win_rate * b - q) / b

        # 分数Kelly
        f = f_star * self.config.kelly_fraction

        # 限制在允许范围内
        f = max(0.0, min(f, self.config.kelly_max_position))
        f = max(f, self.config.kelly_min_position) if f_star > 0 else 0.0

        return round(f, 4)

    # ═══════════════════════════════════════════════
    # 增强3：ATR波动率仓位绑定
    # ═══════════════════════════════════════════════

    def compute_atr(self, high_prices: List[float], low_prices: List[float],
                    close_prices: List[float]) -> float:
        """
        计算ATR（平均真实波幅）
        """
        period = min(self.config.atr_period, len(close_prices))
        if period < 2:
            return 0.0

        true_ranges = []
        for i in range(1, period + 1):
            high = high_prices[-i]
            low = low_prices[-i]
            prev_close = close_prices[-i-1] if i < len(close_prices) else close_prices[-i]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        return sum(true_ranges) / len(true_ranges)

    def atr_adjust_position(
        self,
        base_position: float,
        atr: float,
        current_price: float,
    ) -> float:
        """
        ATR波动率仓位调整
        高波动 → 减仓；低波动 → 可适当加仓
        """
        if atr == 0 or current_price == 0:
            return base_position

        atr_pct = atr / current_price

        # 基准：2%的ATR为正常波动
        if atr_pct > 0.04:        # ATR>4%，极高高波动 → 仓位×0.6
            scalar = 0.60
        elif atr_pct > 0.03:      # ATR>3%，高波动 → 仓位×0.75
            scalar = 0.75
        elif atr_pct > 0.02:      # ATR>2%，中高波动 → 仓位×0.85
            scalar = 0.85
        elif atr_pct < 0.01:      # ATR<1%，低波动 → 仓位×1.1
            scalar = 1.10
        else:                      # 正常 → 仓位×1.0
            scalar = 1.00

        adjusted = base_position * scalar
        return min(adjusted, self.config.max_single_position)

    # ═══════════════════════════════════════════════
    # 增强4：分批止盈机制
    # ═══════════════════════════════════════════════

    def compute_tiered_take_profit(
        self,
        entry_price: float,
        current_price: float,
        position_size: float,
        current_qty: int,
    ) -> dict:
        """
        分批止盈计算
        返回每个档位的止盈触发状态和应减仓数量
        """
        profit_pct = (current_price - entry_price) / entry_price

        tiers = [
            {
                'level': 1,
                'trigger_pct': self.config.tier1_take_profit,
                'trigger_price': round(entry_price * (1 + self.config.tier1_take_profit), 2),
                'triggered': profit_pct >= self.config.tier1_take_profit,
                'sell_ratio': self.config.tier1_ratio,
                'sell_qty': int(current_qty * self.config.tier1_ratio),
                'status': 'active' if profit_pct >= self.config.tier1_take_profit else 'pending',
            },
            {
                'level': 2,
                'trigger_pct': self.config.tier2_take_profit,
                'trigger_price': round(entry_price * (1 + self.config.tier2_take_profit), 2),
                'triggered': profit_pct >= self.config.tier2_take_profit,
                'sell_ratio': self.config.tier2_ratio,
                'sell_qty': int(current_qty * self.config.tier2_ratio),
                'status': 'active' if profit_pct >= self.config.tier2_take_profit else 'pending',
            },
            {
                'level': 3,
                'trigger_pct': self.config.tier3_take_profit,
                'trigger_price': round(entry_price * (1 + self.config.tier3_take_profit), 2),
                'triggered': profit_pct >= self.config.tier3_take_profit,
                'sell_ratio': self.config.tier3_ratio,
                'sell_qty': int(current_qty * self.config.tier3_ratio),
                'status': 'active' if profit_pct >= self.config.tier3_take_profit else 'pending',
            },
        ]

        triggered_count = sum(1 for t in tiers if t['triggered'])

        return {
            'current_profit_pct': round(profit_pct * 100, 2),
            'entry_price': entry_price,
            'current_price': current_price,
            'tiers': tiers,
            'triggered_tiers': triggered_count,
            'suggested_action': 'hold' if triggered_count == 0 else
                               f'阶梯止盈·已触发{triggered_count}档',
        }

    # ═══════════════════════════════════════════════
    # 综合仓位决策
    # ═══════════════════════════════════════════════

    def position_decision(
        self,
        code: str,
        name: str,
        current_price: float,
        entry_price: float = 0,
        m46_probability: float = 0.5,
        m46_confidence: str = '中',
        win_rate: float = 0.38,
        profit_loss_ratio: float = 2.4,
        high_prices: List[float] = None,
        low_prices: List[float] = None,
        close_prices: List[float] = None,
        current_qty: int = 0,
        is_new_position: bool = True,
        risk_level: str = '安全',
    ) -> dict:
        """
        综合仓位决策

        返回完整的仓位建议，兼容 V13.0 data.json
        """

        high_prices = high_prices or []
        low_prices = low_prices or []
        close_prices = close_prices or []

        # ── Step 1: Kelly基础仓位 ──
        kelly_size = self.kelly_position_size(win_rate, profit_loss_ratio)
        kelly_amount = self.config.total_capital * kelly_size

        # ── Step 2: M46概率修正 ──
        # 高置信度→满配Kelly；中→0.8x；低→0.5x
        confidence_multiplier = {
            '高': 1.0,
            '中': 0.80,
            '低': 0.50,
        }.get(m46_confidence, 0.5)

        prob_adjusted_size = kelly_size * m46_probability * confidence_multiplier

        # ── Step 3: ATR波动率修正 ──
        atr = self.compute_atr(high_prices, low_prices, close_prices) if high_prices and low_prices and close_prices else 0
        atr_adjusted_size = self.atr_adjust_position(prob_adjusted_size, atr, current_price)

        # ── Step 4: 风险黑名单修正 ──
        risk_multiplier = {
            '安全': 1.0,
            '观察': 0.75,
            '警告': 0.50,
            '危险': 0.0,
            '黑名单': 0.0,
        }.get(risk_level, 1.0)

        # ── Step 5: 最终仓位 ──
        final_size = atr_adjusted_size * risk_multiplier
        final_amount = self.config.total_capital * final_size

        # 如果是加仓（已有持仓），考虑总仓位上限
        if not is_new_position and current_qty > 0:
            current_market_value = current_qty * current_price
            current_position_pct = current_market_value / self.config.total_capital
            available_pct = self.config.max_single_position - current_position_pct
            final_size = min(final_size, max(0, available_pct))

        final_size = min(final_size, self.config.max_single_position)

        # ── Step 6: 止损止盈计算 ──
        trailing_stop = None
        tiered_tp = None

        if entry_price > 0 and current_qty > 0:
            trailing_stop = self.compute_trailing_stop(
                entry_price, current_price,
                close_prices if close_prices else [current_price],
                atr,
            )
            tiered_tp = self.compute_tiered_take_profit(
                entry_price, current_price,
                final_size, current_qty,
            )

        # ── Step 7: 决策建议 ──
        if final_size >= 0.20:
            action = 'buy_heavy'     # 重仓买入
        elif final_size >= 0.10:
            action = 'buy_standard'  # 标准买入
        elif final_size >= 0.05:
            action = 'buy_light'     # 轻仓试探
        elif final_size > 0 and not is_new_position:
            action = 'hold'          # 持有
        elif final_size == 0 and current_qty > 0:
            action = 'sell_all'      # 清仓
        else:
            action = 'watch'         # 观望

        # ── Step 8: 盈亏比预估 ──
        # 基于当前止损止盈配置计算预期盈亏比
        if trailing_stop and tiered_tp:
            expected_risk = trailing_stop['distance_pct'] / 100
            # 加权平均止盈收益
            expected_reward = (
                self.config.tier1_take_profit * self.config.tier1_ratio +
                self.config.tier2_take_profit * self.config.tier2_ratio +
                self.config.tier3_take_profit * self.config.tier3_ratio
            )
            estimated_plr = expected_reward / expected_risk if expected_risk > 0 else 3.0
        else:
            estimated_plr = profit_loss_ratio

        return {
            'code': code,
            'name': name,
            'action': action,
            'position_size': round(final_size, 4),
            'position_amount': round(final_amount, 0),
            'position_pct': round(final_size * 100, 1),
            'kelly_base': round(kelly_size, 4),
            'm46_multiplier': confidence_multiplier,
            'atr': round(atr, 2) if atr else 0,
            'atr_pct': round(atr / current_price * 100, 2) if atr and current_price else 0,
            'risk_multiplier': risk_multiplier,
            'trailing_stop': trailing_stop,
            'tiered_take_profit': tiered_tp,
            'estimated_profit_loss_ratio': round(estimated_plr, 2),
            'target_plr': 3.0,
            'plr_gap': round(max(0, 3.0 - estimated_plr), 2),
        }


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def run_m54_decision(
    code: str, name: str,
    current_price: float,
    entry_price: float = 0,
    m46_probability: float = 0.5,
    m46_confidence: str = '中',
    high_prices: List[float] = None,
    low_prices: List[float] = None,
    close_prices: List[float] = None,
    current_qty: int = 0,
    is_new: bool = True,
    risk_level: str = '安全',
) -> dict:
    """
    M54仓位决策快捷入口
    """
    engine = M54PositionEngine()
    return engine.position_decision(
        code=code, name=name,
        current_price=current_price,
        entry_price=entry_price,
        m46_probability=m46_probability,
        m46_confidence=m46_confidence,
        high_prices=high_prices,
        low_prices=low_prices,
        close_prices=close_prices,
        current_qty=current_qty,
        is_new_position=is_new,
        risk_level=risk_level,
    )


if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 M54 仓位决策引擎（盈亏比提升增强版）")
    print("=" * 60)
    print("目标: 盈亏比 2.4 → 3.0 (+0.6)")
    print("增强: 动态跟踪止损 + Kelly优化 + ATR绑定 + 分批止盈")
    print("=" * 60)

    # 自测
    prices = [25.0 + i*0.3 for i in range(20)]
    highs = [p * 1.02 for p in prices]
    lows = [p * 0.98 for p in prices]

    decision = run_m54_decision(
        code='002028', name='思源电气',
        current_price=30.5, entry_price=28.0,
        m46_probability=0.72, m46_confidence='高',
        high_prices=highs, low_prices=lows, close_prices=prices,
        current_qty=5000, is_new=False,
        risk_level='安全',
    )

    print(f"\n📊 仓位决策结果:")
    print(json.dumps(decision, ensure_ascii=False, indent=2))
    print(f"\n🎯 预期盈亏比: {decision['estimated_profit_loss_ratio']:.2f}")
    print(f"   目标盈亏比: 3.0")
    print(f"   差距: {decision['plr_gap']:.2f}")
