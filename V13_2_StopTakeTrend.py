#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 止损止盈+趋势持有规则引擎 — StopTakeTrendEngine              ║
║  ================================================================       ║
║  盈亏比PLR从3.0提升至10.0的核心模块                                  ║
║                                                                      ║
║  核心功能：                                                           ║
║  ├── 动态止损: ATR-based + 时间衰减 + 日内最大回撤                    ║
║  ├── 动态止盈: 移动止盈(Trailing Stop) + 分批止盈                     ║
║  ├── 趋势持有: T+1/T+2续涨判定 + 持有期管理                         ║
║  ├── PLR计算: 盈亏比实时追踪                                        ║
║  └── 奖惩联动: 正确持有=奖励加成，过早止盈=惩罚                      ║
║                                                                      ║
║  使用方式：                                                           ║
║  engine = StopTakeTrendEngine(atr_period=14, risk_atr_multiplier=2.0) ║
║  decision = engine.evaluate_position(                                   ║
║      entry_price=10.0, current_price=10.5, high_since_entry=10.8,    ║
║      entry_time=datetime.now(), current_time=datetime.now(),             ║
║      atr=0.3, trend_signal='continuation',                            ║
║      t1_change_pct=5.0, t2_change_pct=3.0,                          ║
║  )                                                                   ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 枚举与数据结构
# ═══════════════════════════════════════════════════════════════

class PositionAction(Enum):
    """持仓操作"""
    HOLD = "持有"           # 继续持有
    STOP_LOSS = "止损"      # 触发止损
    TAKE_PROFIT = "止盈"    # 触发止盈
    TRAILING_STOP = "移动止损"  # 移动止盈触发
    PARTIAL_EXIT = "分批减仓"  # 分批止盈
    FULL_EXIT = "全仓退出"   # 趋势结束全退


class TrendDirection(Enum):
    """趋势方向"""
    UP = "上涨"
    DOWN = "下跌"
    SIDEWAYS = "横盘"
    UNKNOWN = "未知"


@dataclass
class StopTakeConfig:
    """止损止盈配置"""
    # 止损规则
    stop_loss_atr_mult: float = 2.0       # 止损ATR倍数（2.0=2倍ATR）
    max_intraday_drawdown_pct: float = 0.03  # 日内最大回撤3%
    time_decay_stop_hours: float = 2.0      # 持有2小时无盈利→止损
    max_loss_pct: float = 0.05             # 硬止损5%

    # 止盈规则
    take_profit_atr_mult: float = 4.0      # 止盈ATR倍数（4.0=4倍ATR）
    trailing_stop_activation_pct: float = 0.03  # 盈利≥3%后激活移动止盈
    trailing_stop_pct: float = 0.015        # 移动止盈回撤1.5%
    partial_exit_profit_pct: List[float] = field(default_factory=lambda: [0.05, 0.08, 0.13])  # 5%/8%/13%分批减仓
    partial_exit_ratio: List[float] = field(default_factory=lambda: [0.3, 0.3, 0.4])  # 对应减仓比例

    # 趋势持有规则
    trend_continuation_bars: int = 3       # 连续3根K线确认趋势延续
    max_hold_bars: int = 120              # 最大持有120根1分钟K线（2小时）
    hold_past_15min: bool = True           # 允许持有超过15:00（T+1）
    t2_continuation_threshold_pct: float = 2.0  # T+2续涨≥2%判定为趋势启动

    # PLR目标
    target_plr: float = 10.0              # 目标盈亏比10.0
    risk_reward_ratio: float = 3.0         # 风险回报比1:3


@dataclass
class PositionState:
    """持仓状态"""
    code: str
    name: str
    entry_price: float              # 入场价
    entry_time: datetime            # 入场时间
    current_price: float           # 当前价
    high_since_entry: float       # 入场后最高价
    low_since_entry: float        # 入场后最低价
    atr: float                    # 当前ATR值
    position_size: float = 1.0   # 仓位比例（1.0=全仓）
    unrealized_pnl_pct: float = 0.0  # 未实现盈亏%
    max_profit_pct: float = 0.0  # 最大盈利%
    max_drawdown_pct: float = 0.0  # 最大回撤%
    bars_held: int = 0            # 已持有K线数
    partial_exit_count: int = 0    # 已分批减仓次数

    # T+1/T+2趋势
    t1_change_pct: float = 0.0   # T+1涨跌幅%
    t2_change_pct: float = 0.0   # T+2涨跌幅%
    trend_started: bool = False    # 趋势是否启动


@dataclass
class PositionDecision:
    """持仓操作决策"""
    action: PositionAction
    reason: str
    exit_price: Optional[float] = None
    exit_ratio: float = 0.0       # 减仓比例（0=不操作，1.0=全仓）
    hold_reason: Optional[str] = None
    plr_contribution: float = 0.0  # 对PLR的贡献
    reward_bonus: float = 0.0     # 奖惩引擎加成


@dataclass
class PLRCalculator:
    """盈亏比PLR计算器"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0
    total_loss: float = 0.0
    max_winning_trade: float = 0.0
    max_losing_trade: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.winning_trades / max(1, self.total_trades)

    @property
    def avg_profit(self) -> float:
        return self.total_profit / max(1, self.winning_trades)

    @property
    def avg_loss(self) -> float:
        return abs(self.total_loss) / max(1, self.losing_trades)

    @property
    def plr(self) -> float:
        """盈亏比 = 平均盈利 / 平均亏损"""
        avg_l = self.avg_loss
        if avg_l == 0:
            return float('inf')
        return self.avg_profit / avg_l

    @property
    def profit_factor(self) -> float:
        """盈利因子 = 总盈利 / |总亏损|"""
        if abs(self.total_loss) < 1e-6:
            return float('inf')
        return self.total_profit / abs(self.total_loss)

    def add_trade(self, profit_pct: float):
        """添加一笔交易"""
        self.total_trades += 1
        if profit_pct > 0:
            self.winning_trades += 1
            self.total_profit += profit_pct
            self.max_winning_trade = max(self.max_winning_trade, profit_pct)
        else:
            self.losing_trades += 1
            self.total_loss += profit_pct
            self.max_losing_trade = min(self.max_losing_trade, profit_pct)

    def to_dict(self) -> Dict:
        return {
            'total_trades': self.total_trades,
            'win_rate': round(self.win_rate, 4),
            'avg_profit_pct': round(self.avg_profit, 4),
            'avg_loss_pct': round(self.avg_loss, 4),
            'plr': round(self.plr, 4),
            'profit_factor': round(self.profit_factor, 4),
            'max_win': round(self.max_winning_trade, 4),
            'max_loss': round(self.max_losing_trade, 4),
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 动态止损引擎
# ═══════════════════════════════════════════════════════════════

class DynamicStopLoss:
    """动态止损引擎"""

    def __init__(self, config: StopTakeConfig):
        self.config = config

    def compute_stop_price(self, state: PositionState) -> float:
        """
        计算动态止损价
        多级止损取最宽松（最高止损价，给仓位更多空间）
        """
        # 1. ATR-based止损
        atr_stop = state.entry_price - self.config.stop_loss_atr_mult * state.atr

        # 2. 硬止损（最大亏损%）
        hard_stop = state.entry_price * (1 - self.config.max_loss_pct)

        # 3. 日内最大回撤止损
        if state.high_since_entry > state.entry_price:
            drawdown_stop = state.high_since_entry * (1 - self.config.max_intraday_drawdown_pct)
        else:
            drawdown_stop = float('-inf')  # 还未盈利，不激活

        # 4. 时间衰减止损（持有过久无盈利）
        hold_hours = (datetime.now() - state.entry_time).total_seconds() / 3600
        if hold_hours > self.config.time_decay_stop_hours and state.unrealized_pnl_pct < 1.0:
            time_stop = state.current_price  # 当前价附近止损
        else:
            time_stop = float('-inf')

        # 取最高止损价（最宽松）
        stop_price = max(atr_stop, hard_stop, drawdown_stop, time_stop)
        return round(stop_price, 4)

    def check_stop_triggered(self, state: PositionState) -> Optional[str]:
        """检查是否触发止损"""
        stop_price = self.compute_stop_price(state)

        if state.current_price <= stop_price:
            if state.current_price <= state.entry_price * (1 - self.config.max_loss_pct):
                return f"硬止损触发: 当前价{state.current_price:.2f} ≤ 硬止损价{state.entry_price*(1-self.config.max_loss_pct):.2f}"
            elif state.low_since_entry <= state.high_since_entry * (1 - self.config.max_intraday_drawdown_pct):
                return f"日内回撤止损: 最低{state.low_since_entry:.2f} 跌破最高{state.high_since_entry:.2f}的{self.config.max_intraday_drawdown_pct*100:.0f}%回撤线"
            else:
                return f"ATR止损触发: 当前价{state.current_price:.2f} ≤ 止损价{stop_price:.2f}"

        return None


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 动态止盈引擎
# ═══════════════════════════════════════════════════════════════

class DynamicTakeProfit:
    """动态止盈引擎"""

    def __init__(self, config: StopTakeConfig):
        self.config = config

    def compute_take_profit_price(self, state: PositionState) -> float:
        """计算止盈价（固定ATR倍数）"""
        return round(state.entry_price + self.config.take_profit_atr_mult * state.atr, 4)

    def compute_trailing_stop(self, state: PositionState) -> Optional[float]:
        """
        计算移动止盈止损价
        激活条件: 盈利≥trailing_stop_activation_pct
        止损线: high_since_entry * (1 - trailing_stop_pct)
        """
        if state.max_profit_pct < self.config.trailing_stop_activation_pct * 100:
            return None  # 未激活

        trailing_stop = state.high_since_entry * (1 - self.config.trailing_stop_pct)
        return round(trailing_stop, 4)

    def check_partial_exit(self, state: PositionState) -> Optional[Tuple[float, str]]:
        """
        检查是否触发分批减仓
        返回: (减仓比例, 原因) 或 None
        """
        for i, (profit_thresh, exit_ratio) in enumerate(
            zip(self.config.partial_exit_profit_pct, self.config.partial_exit_ratio)
        ):
            if i < state.partial_exit_count and state.unrealized_pnl_pct >= profit_thresh * 100:
                return (exit_ratio, f"分批减仓: 盈利{state.unrealized_pnl_pct:.1f}% ≥ {profit_thresh*100:.0f}%阈值")

        return None

    def check_take_profit(self, state: PositionState) -> Optional[str]:
        """检查是否触发固定止盈"""
        tp_price = self.compute_take_profit_price(state)
        if state.current_price >= tp_price:
            return f"固定止盈触发: 当前价{state.current_price:.2f} ≥ 止盈价{tp_price:.2f}"
        return None


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 趋势持有判定引擎
# ═══════════════════════════════════════════════════════════════

class TrendHoldEngine:
    """趋势持有判定引擎"""

    def __init__(self, config: StopTakeConfig):
        self.config = config

    def evaluate_trend(self, state: PositionState) -> Tuple[TrendDirection, str]:
        """
        评估当前趋势方向
        返回: (趋势方向, 判定原因)
        """
        # T+1续涨判定
        if state.t1_change_pct > 0:
            if state.t1_change_pct >= 5:
                return TrendDirection.UP, f"T+1大涨{state.t1_change_pct:.1f}%，趋势强劲"
            elif state.t1_change_pct >= 2:
                return TrendDirection.UP, f"T+1续涨{state.t1_change_pct:.1f}%，趋势延续"
            else:
                return TrendDirection.UP, f"T+1微涨{state.t1_change_pct:.1f}%"

        # T+2趋势启动判定（圣杯核心！）
        if state.trend_started:
            return TrendDirection.UP, f"趋势已启动: T+2续涨{state.t2_change_pct:.1f}%"

        # 横盘判定
        if abs(state.current_price - state.entry_price) / state.entry_price < 0.01:
            return TrendDirection.SIDEWAYS, "价格横盘，趋势不明"

        # 下跌判定
        if state.current_price < state.entry_price * 0.98:
            return TrendDirection.DOWN, f"价格跌破入场价{state.entry_price:.2f}，趋势转弱"

        return TrendDirection.UP, "默认持有"

    def should_continue_holding(self, state: PositionState) -> Tuple[bool, str]:
        """
        判断是否继续持有
        返回: (是否持有, 原因)
        """
        # 超长持有时间检查
        if state.bars_held > self.config.max_hold_bars:
            return False, f"超长持有: {state.bars_held}根K线 > 最大{self.config.max_hold_bars}根"

        # 趋势判定
        trend, reason = self.evaluate_trend(state)

        if trend == TrendDirection.UP:
            return True, f"趋势延续: {reason}"
        elif trend == TrendDirection.SIDEWAYS:
            # 横盘不超过30根K线（30分钟）
            if state.bars_held < 30:
                return True, f"横盘观望: {reason}"
            else:
                return False, f"横盘过久: 持有{state.bars_held}根K线"
        else:
            return False, f"趋势转跌: {reason}"

    def evaluate_t2_continuation(self, t2_change_pct: float) -> Tuple[bool, str]:
        """
        评估T+2是否续涨（圣杯核心判定）
        返回: (是否趋势启动, 原因)
        """
        if t2_change_pct >= self.config.t2_continuation_threshold_pct:
            return True, f"T+2续涨{t2_change_pct:.1f}% ≥ {self.config.t2_continuation_threshold_pct:.1f}%阈值，趋势启动！"
        elif t2_change_pct > 0:
            return False, f"T+2微涨{t2_change_pct:.1f}%，未达趋势启动阈值"
        else:
            return False, f"T+2下跌{t2_change_pct:.1f}%，趋势未启动"


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 主引擎 — StopTakeTrendEngine
# ═══════════════════════════════════════════════════════════════

class StopTakeTrendEngine:
    """
    止损止盈+趋势持有主引擎

    目标: PLR从3.0→10.0
    方法: 让盈利仓位跑起来（趋势持有），快速止损（动态止损）
    """

    def __init__(
        self,
        atr_period: int = 14,
        risk_atr_multiplier: float = 2.0,
        reward_atr_multiplier: float = 4.0,
        trailing_activation_pct: float = 3.0,
        config: Optional[StopTakeConfig] = None,
    ):
        self.config = config or StopTakeConfig(
            stop_loss_atr_mult=risk_atr_multiplier,
            take_profit_atr_mult=reward_atr_multiplier,
            trailing_stop_activation_pct=trailing_activation_pct / 100,
        )
        self.stop_loss_engine = DynamicStopLoss(self.config)
        self.take_profit_engine = DynamicTakeProfit(self.config)
        self.trend_engine = TrendHoldEngine(self.config)
        self.plr_calculator = PLRCalculator()

        # 持仓池
        self.positions: Dict[str, PositionState] = {}

        print(f"✅ [V13.2] 止损止盈引擎已初始化")
        print(f"   止损: {self.config.stop_loss_atr_mult}倍ATR | 硬止损: {self.config.max_loss_pct*100:.0f}%")
        print(f"   止盈: {self.config.take_profit_atr_mult}倍ATR | 移动止盈: 盈利≥{self.config.trailing_stop_activation_pct*100:.0f}%后回撤{self.config.trailing_stop_pct*100:.0f}%")
        print(f"   目标PLR: {self.config.target_plr:.1f}")

    # ── 开仓 ──────────────────────────────────────────────────────────
    def open_position(
        self,
        code: str,
        name: str,
        entry_price: float,
        entry_time: datetime,
        atr: float,
        position_size: float = 1.0,
    ) -> PositionState:
        """开仓"""
        state = PositionState(
            code=code,
            name=name,
            entry_price=entry_price,
            entry_time=entry_time,
            current_price=entry_price,
            high_since_entry=entry_price,
            low_since_entry=entry_price,
            atr=atr,
            position_size=position_size,
        )
        self.positions[code] = state
        print(f"📈 [开仓] {code} {name} @ {entry_price:.2f} | ATR={atr:.3f}")
        return state

    # ── 更新价格 ────────────────────────────────────────────────────
    def update_price(
        self,
        code: str,
        current_price: float,
        current_time: datetime,
        t1_change_pct: Optional[float] = None,
        t2_change_pct: Optional[float] = None,
    ) -> PositionState:
        """更新持仓价格，返回更新后的状态"""
        if code not in self.positions:
            raise ValueError(f"持仓{code}不存在")

        state = self.positions[code]
        state.current_price = current_price
        state.high_since_entry = max(state.high_since_entry, current_price)
        state.low_since_entry = min(state.low_since_entry, current_price)
        state.unrealized_pnl_pct = (current_price - state.entry_price) / state.entry_price * 100
        state.max_profit_pct = max(state.max_profit_pct, state.unrealized_pnl_pct)
        state.max_drawdown_pct = min(state.max_drawdown_pct, state.unrealized_pnl_pct)
        state.bars_held += 1

        if t1_change_pct is not None:
            state.t1_change_pct = t1_change_pct
        if t2_change_pct is not None:
            state.t2_change_pct = t2_change_pct
            # T+2趋势启动判定
            if t2_change_pct >= self.config.t2_continuation_threshold_pct:
                state.trend_started = True

        return state

    # ── 评估持仓 ────────────────────────────────────────────────────
    def evaluate_position(self, code: str) -> PositionDecision:
        """
        评估持仓，产出操作决策
        这是核心方法，整合止损/止盈/趋势持有判定
        """
        if code not in self.positions:
            raise ValueError(f"持仓{code}不存在")

        state = self.positions[code]

        # Step 1: 检查止损
        stop_reason = self.stop_loss_engine.check_stop_triggered(state)
        if stop_reason:
            return PositionDecision(
                action=PositionAction.STOP_LOSS,
                reason=stop_reason,
                exit_price=state.current_price,
                exit_ratio=1.0,
                plr_contribution=-abs(state.unrealized_pnl_pct),
                reward_bonus=-20.0,  # 止损惩罚
            )

        # Step 2: 检查固定止盈
        tp_reason = self.take_profit_engine.check_take_profit(state)
        if tp_reason:
            return PositionDecision(
                action=PositionAction.TAKE_PROFIT,
                reason=tp_reason,
                exit_price=state.current_price,
                exit_ratio=1.0,
                plr_contribution=state.unrealized_pnl_pct,
                reward_bonus=+30.0,  # 止盈奖励
            )

        # Step 3: 检查移动止盈
        trailing_stop = self.take_profit_engine.compute_trailing_stop(state)
        if trailing_stop is not None and state.current_price <= trailing_stop:
            return PositionDecision(
                action=PositionAction.TRAILING_STOP,
                reason=f"移动止盈触发: 当前价{state.current_price:.2f} ≤ 移动止损价{trailing_stop:.2f}",
                exit_price=state.current_price,
                exit_ratio=1.0,
                plr_contribution=state.unrealized_pnl_pct,
                reward_bonus=+20.0,
            )

        # Step 4: 检查分批减仓
        partial = self.take_profit_engine.check_partial_exit(state)
        if partial:
            exit_ratio, reason = partial
            return PositionDecision(
                action=PositionAction.PARTIAL_EXIT,
                reason=reason,
                exit_price=state.current_price,
                exit_ratio=exit_ratio,
                plr_contribution=state.unrealized_pnl_pct * exit_ratio,
                reward_bonus=+10.0 * exit_ratio,
            )

        # Step 5: 趋势持有判定
        should_hold, hold_reason = self.trend_engine.should_continue_holding(state)
        if not should_hold:
            return PositionDecision(
                action=PositionAction.FULL_EXIT,
                reason=f"趋势结束: {hold_reason}",
                exit_price=state.current_price,
                exit_ratio=1.0,
                plr_contribution=state.unrealized_pnl_pct,
                reward_bonus=+10.0,
            )

        # Step 6: 继续持有
        trend, trend_reason = self.trend_engine.evaluate_trend(state)
        return PositionDecision(
            action=PositionAction.HOLD,
            reason=f"继续持有: {trend_reason} | {hold_reason}",
            hold_reason=hold_reason,
            plr_contribution=0.0,  # 未平仓，不计入PLR
        )

    # ── 平仓 ──────────────────────────────────────────────────────────
    def close_position(self, code: str, exit_price: float, reason: str) -> Dict:
        """平仓，更新PLR计算器"""
        if code not in self.positions:
            raise ValueError(f"持仓{code}不存在")

        state = self.positions[code]
        profit_pct = (exit_price - state.entry_price) / state.entry_price * 100

        # 更新PLR
        self.plr_calculator.add_trade(profit_pct)

        result = {
            'code': code,
            'name': state.name,
            'entry_price': state.entry_price,
            'exit_price': exit_price,
            'profit_pct': round(profit_pct, 4),
            'bars_held': state.bars_held,
            'reason': reason,
            'plr_after': round(self.plr_calculator.plr, 4),
        }

        del self.positions[code]
        print(f"📉 [平仓] {code} {state.name} | 盈利={profit_pct:+.2f}% | PLR={self.plr_calculator.plr:.2f}")
        return result

    # ── 批量评估（用于回测）────────────────────────────────────────
    def backtest_batch(
        self,
        trades: List[Dict],
    ) -> Dict:
        """
        批量回测
        trades: [{'code','name','entry_price','entry_time','exit_price','exit_time','atr'}]
        """
        results = []
        for t in trades:
            state = self.open_position(
                code=t['code'],
                name=t.get('name', ''),
                entry_price=t['entry_price'],
                entry_time=t.get('entry_time', datetime.now()),
                atr=t.get('atr', 0.3),
            )
            # 简化：直接平仓
            result = self.close_position(
                code=t['code'],
                exit_price=t['exit_price'],
                reason=t.get('reason', '回测平仓'),
            )
            results.append(result)

        return {
            'trades': results,
            'plr_stats': self.plr_calculator.to_dict(),
        }

    # ── 生成持仓报告 ────────────────────────────────────────────────
    def generate_position_report(self) -> Dict:
        """生成当前持仓报告"""
        if not self.positions:
            return {'positions': [], 'plr_stats': self.plr_calculator.to_dict()}

        positions = []
        for code, state in self.positions.items():
            stop_price = self.stop_loss_engine.compute_stop_price(state)
            tp_price = self.take_profit_engine.compute_take_profit_price(state)
            trailing_stop = self.take_profit_engine.compute_trailing_stop(state)

            positions.append({
                'code': code,
                'name': state.name,
                'entry_price': state.entry_price,
                'current_price': state.current_price,
                'unrealized_pnl_pct': round(state.unrealized_pnl_pct, 4),
                'max_profit_pct': round(state.max_profit_pct, 4),
                'max_drawdown_pct': round(state.max_drawdown_pct, 4),
                'bars_held': state.bars_held,
                'stop_price': stop_price,
                'take_profit_price': tp_price,
                'trailing_stop': trailing_stop,
                'trend_started': state.trend_started,
            })

        return {
            'positions': positions,
            'position_count': len(positions),
            'plr_stats': self.plr_calculator.to_dict(),
            'config': {
                'stop_loss_atr': self.config.stop_loss_atr_mult,
                'take_profit_atr': self.config.take_profit_atr_mult,
                'trailing_activation_pct': self.config.trailing_stop_activation_pct * 100,
                'trailing_stop_pct': self.config.trailing_stop_pct * 100,
            },
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 与奖惩引擎联动
# ═══════════════════════════════════════════════════════════════

def integrate_with_reward_engine(
    position_decision: PositionDecision,
    reward_engine: Any,  # V13_2_RewardEngine.RewardEngine
) -> float:
    """
    将持仓操作决策与奖惩引擎联动
    正确的持有操作 → 奖励加成
    过早止盈/过晚止损 → 惩罚
    """
    bonus = position_decision.reward_bonus

    # 趋势启动时继续持有 → 额外奖励（圣杯命中！）
    if position_decision.action == PositionAction.HOLD and position_decision.hold_reason:
        if '趋势启动' in position_decision.hold_reason:
            bonus += 50.0  # 趋势启动额外奖励

    # 移动止盈触发 → 正确锁定利润
    if position_decision.action == PositionAction.TRAILING_STOP:
        bonus += 10.0

    # 过早止盈（盈利<3%就止盈）→ 惩罚
    if position_decision.action == PositionAction.TAKE_PROFIT and position_decision.plr_contribution < 3.0:
        bonus -= 15.0

    return bonus


# ═══════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import random

    print("=" * 70)
    print("  V13.2 止损止盈+趋势持有规则引擎 — 演示")
    print("=" * 70)

    engine = StopTakeTrendEngine(
        risk_atr_multiplier=2.0,
        reward_atr_multiplier=4.0,
        trailing_activation_pct=3.0,
    )

    # 演示1: 开仓 + 价格上涨 → 移动止盈激活
    print("\n--- 演示1: 趋势持有 + 移动止盈 ---")
    state = engine.open_position('600519', '贵州茅台', entry_price=100.0, entry_time=datetime.now(), atr=1.5)

    # 模拟价格上涨
    prices = [100.5, 101.0, 102.0, 103.0, 104.0, 103.5, 102.0]
    for p in prices:
        engine.update_price('600519', p, datetime.now())
        decision = engine.evaluate_position('600519')
        state = engine.positions.get('600519')
        stop = engine.stop_loss_engine.compute_stop_price(state) if state else 0
        tp = engine.take_profit_engine.compute_take_profit_price(state) if state else 0
        trail = engine.take_profit_engine.compute_trailing_stop(state) if state else None
        trail_str = f'{trail:.2f}' if trail is not None else '未激活'
        print(f"  价格={p:.1f} | 盈亏={state.unrealized_pnl_pct:+.1f}% | 决策={decision.action.value} | 原因={decision.reason[:30]}")
        print(f"    止损价={stop:.2f} | 止盈价={tp:.2f} | 移动止损={trail_str}")

    # 演示2: 止损触发
    print("\n--- 演示2: 止损触发 ---")
    state2 = engine.open_position('300750', '宁德时代', entry_price=200.0, entry_time=datetime.now(), atr=3.0)
    engine.update_price('300750', 196.0, datetime.now())  # 下跌2%
    decision2 = engine.evaluate_position('300750')
    print(f"  价格=196.0 | 决策={decision2.action.value} | 原因={decision2.reason}")

    # 演示3: PLR统计
    print("\n--- 演示3: PLR统计 ---")
    # 模拟10笔交易
    random.seed(42)
    trades = []
    for i in range(10):
        entry = 100.0
        # 30%概率大赚，70%概率小亏（当前PLR=3.0的情况）
        if random.random() < 0.3:
            exit = entry * (1 + random.uniform(0.05, 0.15))  # 赚5-15%
        else:
            exit = entry * (1 - random.uniform(0.01, 0.03))  # 亏1-3%
        trades.append({'code': f'demo{i}', 'name': f'演示{i}', 'entry_price': entry, 'exit_price': exit, 'atr': 1.5})

    result = engine.backtest_batch(trades)
    plr = result['plr_stats']
    print(f"  交易次数: {plr['total_trades']}")
    print(f"  胜率: {plr['win_rate']*100:.0f}%")
    print(f"  平均盈利: {plr['avg_profit_pct']:.2f}%")
    print(f"  平均亏损: {plr['avg_loss_pct']:.2f}%")
    print(f"  盈亏比PLR: {plr['plr']:.2f}  ← 目标: 10.0")
    print(f"  盈利因子: {plr['profit_factor']:.2f}")

    print("\n" + "=" * 70)
    print("  演示完成")
    print("=" * 70)
