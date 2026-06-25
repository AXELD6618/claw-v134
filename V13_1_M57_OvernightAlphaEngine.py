#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.1 M57 隔夜Alpha因子引擎 — OvernightAlphaEngine                  ║
║  =========================================================          ║
║  圣杯目标：捕获T日收盘→T+1日开盘/涨跌之间的Alpha                     ║
║                                                                      ║
║  知识融合：                                                          ║
║  ├── AurumQ-RL: 事件信号exp-decay编码（τ=5d）+ Main-Wave奖励         ║
║  ├── AurumQ-RL: Cross-sectional因子标准化 + SHAP因子重要性           ║
║  ├── 中信建投: 隔夜动量因子研究（2024）                             ║
║  ├── 华泰金工: T+0→T+1 Alpha因子库（2025）                         ║
║  └── 62%胜率数据拆解: 20MA+量能+不跳水+板块红+非高位                ║
║                                                                      ║
║  12大隔夜Alpha因子：                                                 ║
║  1. 尾盘相对强度    (TailRS)        - 最后30分钟vs全天涨幅          ║
║  2. 尾盘量能结构    (TailVolStruct)  - 尾盘量/全天量比              ║
║  3. 隔夜动量        (OvernightMom)   - 近N日隔夜收益模式           ║
║  4. 日内反转强度    (IntradayRev)    - 日内低位→尾盘强力收回        ║
║  5. 集合竞价微结构  (AuctionSig)     - 14:57集合竞价信号           ║
║  6. 板块Alpha       (SectorAlpha)    - 个股vs板块超额收益           ║
║  7. 连板预期        (StreakExp)      - 涨停连板次日溢价             ║
║  8. 资金流入加速度  (FlowAccel)      - 尾盘资金流速变化             ║
║  9. 跳空回补概率    (GapFillProb)    - 当日跳空后回补历史概率      ║
║  10. 事件衰减因子   (EventDecay)     - 近期催化剂的衰减加权         ║
║  11. 龙虎榜效应     (LHBEffect)      - 龙虎榜席位次日影响           ║
║  12. 市场情绪传导   (SentimentTrans)  - 大盘情绪→个股联动          ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Callable
from collections import deque


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class OvernightFactors:
    """12大隔夜Alpha因子"""
    code: str = ''
    date: str = ''

    # 因子原始值（标准化前）
    tail_rs: float = 0.0              # 1. 尾盘相对强度
    tail_vol_struct: float = 0.0      # 2. 尾盘量能结构
    overnight_mom: float = 0.0        # 3. 隔夜动量
    intraday_rev: float = 0.0         # 4. 日内反转强度
    auction_sig: float = 0.0          # 5. 集合竞价信号
    sector_alpha: float = 0.0         # 6. 板块Alpha
    streak_exp: float = 0.0           # 7. 连板预期
    flow_accel: float = 0.0           # 8. 资金流入加速度
    gap_fill_prob: float = 0.0        # 9. 跳空回补概率
    event_decay: float = 0.0          # 10. 事件衰减
    lhb_effect: float = 0.0           # 11. 龙虎榜效应
    sentiment_trans: float = 0.0      # 12. 市场情绪传导

    # 融合结果
    composite_score: float = 0.0      # 综合Alpha评分
    t1_return_forecast: float = 0.0   # T+1预期收益


@dataclass
class EventSignal:
    """事件信号（用于衰减编码）"""
    event_type: str
    date: str
    strength: float      # 0~1 事件强度
    direction: int       # +1利好 / -1利空
    half_life: int = 5   # 半衰期（天）


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 单因子计算器
# ═══════════════════════════════════════════════════════════════

class SingleFactorComputer:
    """12个隔夜因子的独立计算"""

    # ── 1. 尾盘相对强度 ──
    @staticmethod
    def compute_tail_rs(
        intraday_change_pct: float,    # 全天涨幅
        tail_30min_change_pct: float,  # 尾盘30分钟涨幅
    ) -> float:
        """
        尾盘相对强度 = 尾盘涨幅 - 全天涨幅
        正数=尾盘走强（抢筹），负数=尾盘走弱（出货）
        """
        return round(tail_30min_change_pct - intraday_change_pct, 4)

    # ── 2. 尾盘量能结构 ──
    @staticmethod
    def compute_tail_vol_struct(
        tail_30min_volume: float,      # 尾盘30分钟成交量
        total_day_volume: float,       # 全天成交量
        avg_tail_ratio: float = 0.15,  # 历史平均尾盘量占比
    ) -> float:
        """
        尾盘量能结构 = (尾盘量占比 / 历史均值) - 1
        >0 = 尾盘异常放量, <0 = 尾盘缩量
        """
        if total_day_volume == 0 or avg_tail_ratio == 0:
            return 0.0
        actual_ratio = tail_30min_volume / total_day_volume
        return round(actual_ratio / avg_tail_ratio - 1.0, 4)

    # ── 3. 隔夜动量 ──
    @staticmethod
    def compute_overnight_mom(
        overnight_returns: List[float],  # 近N日隔夜收益（T-1收盘→T开盘）
        lookback: int = 10,
    ) -> float:
        """
        隔夜动量 = 近N日隔夜收益的EMA
        正数=近期隔夜偏涨，负数=近期隔夜偏跌
        tau=3天指数衰减
        """
        if not overnight_returns:
            return 0.0
        tau = 3.0
        weights = [math.exp(-i / tau) for i in range(len(overnight_returns))]
        total_w = sum(weights)
        if total_w == 0:
            return 0.0
        ema = sum(r * w for r, w in zip(overnight_returns, weights)) / total_w
        return round(ema, 4)

    # ── 4. 日内反转强度 ──
    @staticmethod
    def compute_intraday_rev(
        day_low_pct: float,            # 日内最低点相对昨收(%)
        day_close_pct: float,          # 收盘相对昨收(%)
        tail_30min_change_pct: float,  # 尾盘30分钟涨幅(%)
    ) -> float:
        """
        日内反转强度 = (收盘-最低) + 尾盘涨幅×放大系数
        日内从最低点强力收回+尾盘加速 = 强反转信号
        """
        recovery = day_close_pct - day_low_pct  # 从低点回升幅度
        # 尾盘加速部分额外加分
        accel_bonus = max(0, tail_30min_change_pct) * 0.5
        return round(recovery + accel_bonus, 4)

    # ── 5. 集合竞价微结构 ──
    @staticmethod
    def compute_auction_sig(
        auction_price_change_pct: float,    # 14:57→15:00价格变化(%)
        auction_volume_ratio: float,        # 集合竞价的量占尾盘量的比例
        price_before_auction_pct: float,    # 14:56价格相对昨收(%)
    ) -> float:
        """
        集合竞价信号强度
        正向=竞价拉高（抢筹），负向=竞价压低（出货）
        """
        # 竞价拉升 且 竞价前处于弱势 → 强烈的尾盘偷袭信号
        if auction_price_change_pct > 0.3 and price_before_auction_pct < 0:
            return round(auction_price_change_pct * auction_volume_ratio * 2.0, 4)
        # 竞价拉升 且 竞价前已经强势 → 正常延续
        if auction_price_change_pct > 0:
            return round(auction_price_change_pct * auction_volume_ratio, 4)
        # 竞价压低
        return round(auction_price_change_pct * auction_volume_ratio * 1.5, 4)

    # ── 6. 板块Alpha ──
    @staticmethod
    def compute_sector_alpha(
        stock_intraday_change: float,    # 个股全天涨幅(%)
        sector_intraday_change: float,   # 板块全天涨幅(%)
        sector_std: float = 1.5,         # 板块内标准差
    ) -> float:
        """
        板块Alpha = (个股涨幅 - 板块涨幅) / 板块标准差
        正数=个股显著强于板块（独立逻辑）
        """
        if sector_std == 0:
            return 0.0
        return round((stock_intraday_change - sector_intraday_change) / sector_std, 4)

    # ── 7. 连板预期 ──
    @staticmethod
    def compute_streak_exp(
        consecutive_limit_up: int,       # 当前连板天数
        daily_limit_pct: float = 0.10,  # 涨停幅度
        board_tier: str = 'main',       # 'main'/'gem'/'star'/'bse'
    ) -> float:
        """
        连板预期收益
        基于历史统计：第N板次日平均溢价
        """
        # 不同板的连板溢价衰减曲线
        decay_curves = {
            'main': [5.0, 3.5, 2.0, 1.0, 0.5, 0.2, 0.0],   # 主板
            'gem':  [7.0, 5.0, 3.0, 1.5, 0.8, 0.3, 0.0],   # 创业板20cm
            'star': [8.0, 6.0, 4.0, 2.0, 1.0, 0.5, 0.0],   # 科创板20cm
            'bse':  [12.0, 9.0, 5.0, 2.0, 1.0, 0.5, 0.0],  # 北交所30cm
        }
        curve = decay_curves.get(board_tier, decay_curves['main'])
        idx = min(consecutive_limit_up, len(curve) - 1)
        # 首板溢价最高，之后递减
        return round(curve[idx] / 100.0, 4)  # 转换为小数

    # ── 8. 资金流入加速度 ──
    @staticmethod
    def compute_flow_accel(
        tail_volumes: List[float],     # 尾盘30分钟每1分钟量
        tail_prices: List[float],      # 尾盘30分钟每1分钟价格
    ) -> float:
        """
        资金流入加速度 = 尾盘后1/3时段 vs 前2/3时段的量价综合变化率

        核心逻辑：尾盘后半段资金加速流入 + 价格加速上涨 = 强信号
        """
        if len(tail_volumes) < 6 or len(tail_prices) < 6:
            return 0.0

        n = len(tail_volumes)
        split = n * 2 // 3

        # 前2/3
        early_vol = sum(tail_volumes[:split]) / split
        early_price_change = (tail_prices[split - 1] / tail_prices[0] - 1) * 100

        # 后1/3
        late_vol = sum(tail_volumes[split:]) / (n - split)
        late_price_change = (tail_prices[-1] / tail_prices[split] - 1) * 100

        # 量加速度
        vol_accel = (late_vol / early_vol - 1) if early_vol > 0 else 0

        # 价加速度
        price_accel = late_price_change - early_price_change

        # 综合：量价齐加速 = 最强信号
        return round(vol_accel * 0.4 + price_accel * 0.6, 4)

    # ── 9. 跳空回补概率 ──
    @staticmethod
    def compute_gap_fill_prob(
        today_gap_pct: float,           # 今日开盘跳空幅度（%）
        gap_direction: int,             # +1向上跳空 / -1向下跳空
        historical_fill_rate: float,    # 历史回补率（该股过去类似跳空的回补概率）
    ) -> float:
        """
        跳空回补概率估计
        近0=大概率回补（信号弱），近1=小概率回补（信号强）
        向下跳空且大概率回补 = 潜在T+1反弹机会
        向上跳空且大概率不回补 = T+1延续上涨机会
        """
        if abs(today_gap_pct) < 0.5:
            return 0.5  # 无明显跳空

        # 向上跳空：不回补概率越高越好（延续性强）
        if gap_direction > 0:
            return round(1.0 - historical_fill_rate, 4)

        # 向下跳空：回补概率越高越好（反弹预期）
        return round(historical_fill_rate, 4)

    # ── 10. 事件衰减因子（AurumQ-RL exp-decay τ=5d）──
    @staticmethod
    def compute_event_decay(
        events: List[EventSignal],
        current_date: str,
    ) -> float:
        """
        事件信号exp-decay编码

        formula: Σ strength × direction × exp(-(t_now - t_event) / half_life)
        """
        if not events:
            return 0.0

        try:
            now = datetime.strptime(current_date, '%Y-%m-%d')
        except (ValueError, TypeError):
            return 0.0

        total_signal = 0.0
        for evt in events:
            try:
                evt_date = datetime.strptime(evt.date, '%Y-%m-%d')
            except (ValueError, TypeError):
                continue
            days_diff = (now - evt_date).days
            if days_diff < 0:  # 未来事件不考虑
                continue
            if days_diff > 30:  # 超过30天忽略
                continue
            decay = math.exp(-days_diff / evt.half_life)
            total_signal += evt.strength * evt.direction * decay

        # tanh归一化到[-1, 1]
        return round(math.tanh(total_signal / 2.0), 4)

    # ── 11. 龙虎榜效应 ──
    @staticmethod
    def compute_lhb_effect(
        lhb_buy_amount: float,           # 龙虎榜买入总额
        lhb_sell_amount: float,          # 龙虎榜卖出总额
        total_turnover: float,           # 当日总成交额
        is_bullish_seat: bool = False,   # 是否有知名游资/机构席位
        days_since_lhb: int = 0,         # 距龙虎榜日数（0=当日上榜）
    ) -> float:
        """
        龙虎榜次日效应

        正向=净买入多且有知名席位，负向=净卖出
        exp-decay 衰减τ=3天
        """
        if total_turnover == 0:
            return 0.0

        # 买卖净额占成交比
        net_ratio = (lhb_buy_amount - lhb_sell_amount) / total_turnover

        # 知名席位加分
        seat_bonus = 0.3 if is_bullish_seat else 0.0

        # 时间衰减
        decay = math.exp(-days_since_lhb / 3.0)

        return round((net_ratio + seat_bonus) * decay, 4)

    # ── 12. 市场情绪传导 ──
    @staticmethod
    def compute_sentiment_trans(
        market_tail_change_pct: float,   # 大盘尾盘变化(%)
        stock_beta: float,               # 个股Beta
        market_sentiment_idx: float,     # 市场情绪指数（-1恐慌 ~ +1贪婪）
        vix_proxy: float = 20.0,         # 恐慌指数代理（波动率越高越恐慌）
    ) -> float:
        """
        市场情绪→个股传导

        大盘尾盘走强 × Beta = 个股隔夜溢价
        但在高VIX环境下，隔夜传导减弱
        """
        base_transmission = market_tail_change_pct * stock_beta
        # 高波动时传导系数降低
        vix_discount = 20.0 / max(vix_proxy, 10.0)
        return round(base_transmission * vix_discount * 0.01, 4)


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 截面标准化器
# ═══════════════════════════════════════════════════════════════

class CrossSectionNormalizer:
    """
    截面标准化（AurumQ-RL风格）
    将全市场因子值转为z-score/百分位排名
    """

    @staticmethod
    def zscore(values: List[float]) -> List[float]:
        """Z-score标准化"""
        if len(values) < 2:
            return values
        mean = sum(values) / len(values)
        var = sum((v - mean) ** 2 for v in values) / len(values)
        std = math.sqrt(var)
        if std == 0:
            return [0.0] * len(values)
        return [round((v - mean) / std, 4) for v in values]

    @staticmethod
    def percentile_rank(values: List[float]) -> List[float]:
        """百分位排名 0~1"""
        if not values:
            return []
        n = len(values)
        # 排序并分配排名
        sorted_pairs = sorted(enumerate(values), key=lambda x: x[1])
        ranks = [0.0] * n
        for rank, (idx, _) in enumerate(sorted_pairs):
            ranks[idx] = rank / (n - 1) if n > 1 else 0.5
        return [round(r, 4) for r in ranks]

    @staticmethod
    def winsorize(values: List[float], limits: float = 0.02) -> List[float]:
        """缩尾处理（trim极端值）"""
        if len(values) < 10:
            return values
        sorted_vals = sorted(values)
        lower = sorted_vals[int(len(values) * limits)]
        upper = sorted_vals[int(len(values) * (1 - limits))]
        return [min(max(v, lower), upper) for v in values]


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 因子融合引擎
# ═══════════════════════════════════════════════════════════════

class FactorFusion:
    """
    12因子融合 → T+1预期收益

    权重来源：SHAP分析 + 回测IC优化 + M55动态校准
    """

    # 基础权重（基于IC值排序）
    BASE_WEIGHTS = {
        'tail_rs': 0.15,           # 尾盘相对强度（最重要）
        'tail_vol_struct': 0.12,   # 尾盘量能结构
        'intraday_rev': 0.12,      # 日内反转
        'flow_accel': 0.10,        # 资金加速度
        'auction_sig': 0.08,       # 集合竞价
        'sector_alpha': 0.08,      # 板块Alpha
        'overnight_mom': 0.07,     # 隔夜动量
        'streak_exp': 0.07,        # 连板预期
        'event_decay': 0.06,       # 事件衰减
        'sentiment_trans': 0.05,   # 情绪传导
        'lhb_effect': 0.05,        # 龙虎榜
        'gap_fill_prob': 0.05,     # 跳空回补
    }

    # 截面归一化后因子std≈1，加权融合后composite std≈0.29
    # 需放大3.5x使tanh输入分布在[-2, 2]范围，保留高区分度
    CROSS_SECTION_SCALE = 3.5

    @classmethod
    def fuse(
        cls,
        factors: OvernightFactors,
        custom_weights: Dict[str, float] = None,
    ) -> Tuple[float, float]:
        """
        因子加权融合

        Returns:
            (composite_score, t1_return_forecast)
        """
        weights = custom_weights or cls.BASE_WEIGHTS

        factor_dict = {
            'tail_rs': factors.tail_rs,
            'tail_vol_struct': factors.tail_vol_struct,
            'overnight_mom': factors.overnight_mom,
            'intraday_rev': factors.intraday_rev,
            'auction_sig': factors.auction_sig,
            'sector_alpha': factors.sector_alpha,
            'streak_exp': factors.streak_exp,
            'flow_accel': factors.flow_accel,
            'gap_fill_prob': factors.gap_fill_prob,
            'event_decay': factors.event_decay,
            'lhb_effect': factors.lhb_effect,
            'sentiment_trans': factors.sentiment_trans,
        }

        # 加权求和
        composite = sum(
            factor_dict.get(k, 0.0) * weights.get(k, 0.0)
            for k in weights
        )

        # 截面归一化后因子std≈1，composite std≈0.29
        # 放大3.5x使tanh输入分布在[-2, 2]范围，保留高区分度
        composite = composite * cls.CROSS_SECTION_SCALE

        # 使用tanh软归一化到[-1, 1]，保留高分区间的区分度
        import math
        composite = math.tanh(composite)

        # T+1预期收益映射（基于历史IC×当前因子值）
        # 假设IC=0.05，因子标准差≈1，则预期收益 ≈ IC * composite * σ(returns)
        avg_daily_return_std = 3.0  # A股日均收益标准差约3%
        t1_forecast = composite * 0.05 * avg_daily_return_std

        return round(composite, 4), round(t1_forecast, 4)


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 主引擎 — OvernightAlphaEngine
# ═══════════════════════════════════════════════════════════════

class OvernightAlphaEngine:
    """
    V13.1 M57 隔夜Alpha因子主引擎

    调用方式：
        engine = OvernightAlphaEngine()
        factors = engine.compute_all_factors(stock_data)
        score, forecast = engine.evaluate(factors)
    """

    def __init__(self):
        self.computer = SingleFactorComputer()
        self.normalizer = CrossSectionNormalizer()
        self.fusion = FactorFusion()

    def compute_all_factors(
        self,
        code: str,
        date: str = '',
        # 价格数据
        intraday_change_pct: float = 0.0,
        tail_30min_change_pct: float = 0.0,
        day_low_pct: float = 0.0,
        day_close_pct: float = 0.0,
        today_gap_pct: float = 0.0,
        gap_direction: int = 0,
        # 量能数据
        tail_30min_volume: float = 0.0,
        total_day_volume: float = 0.0,
        avg_tail_ratio: float = 0.15,
        tail_volumes: List[float] = None,
        tail_prices: List[float] = None,
        # 竞价数据
        auction_price_change_pct: float = 0.0,
        auction_volume_ratio: float = 0.0,
        price_before_auction_pct: float = 0.0,
        # 板块数据
        sector_intraday_change: float = 0.0,
        sector_std: float = 1.5,
        # 历史数据
        overnight_returns: List[float] = None,
        historical_fill_rate: float = 0.5,
        # 连板
        consecutive_limit_up: int = 0,
        board_tier: str = 'main',
        # 事件
        events: List[EventSignal] = None,
        # 龙虎榜
        lhb_buy_amount: float = 0.0,
        lhb_sell_amount: float = 0.0,
        total_turnover: float = 0.0,
        is_bullish_seat: bool = False,
        days_since_lhb: int = 0,
        # 市场
        market_tail_change_pct: float = 0.0,
        stock_beta: float = 1.0,
        market_sentiment_idx: float = 0.0,
        vix_proxy: float = 20.0,
    ) -> OvernightFactors:
        """计算全部12个隔夜Alpha因子"""

        f = OvernightFactors(code=code, date=date)

        # 1. 尾盘相对强度
        f.tail_rs = self.computer.compute_tail_rs(
            intraday_change_pct, tail_30min_change_pct)

        # 2. 尾盘量能结构
        f.tail_vol_struct = self.computer.compute_tail_vol_struct(
            tail_30min_volume, total_day_volume, avg_tail_ratio)

        # 3. 隔夜动量
        f.overnight_mom = self.computer.compute_overnight_mom(
            overnight_returns or [])

        # 4. 日内反转强度
        f.intraday_rev = self.computer.compute_intraday_rev(
            day_low_pct, day_close_pct, tail_30min_change_pct)

        # 5. 集合竞价信号
        f.auction_sig = self.computer.compute_auction_sig(
            auction_price_change_pct, auction_volume_ratio,
            price_before_auction_pct)

        # 6. 板块Alpha
        f.sector_alpha = self.computer.compute_sector_alpha(
            intraday_change_pct, sector_intraday_change, sector_std)

        # 7. 连板预期
        f.streak_exp = self.computer.compute_streak_exp(
            consecutive_limit_up, 0.10, board_tier)

        # 8. 资金流入加速度
        f.flow_accel = self.computer.compute_flow_accel(
            tail_volumes or [], tail_prices or [])

        # 9. 跳空回补概率
        f.gap_fill_prob = self.computer.compute_gap_fill_prob(
            today_gap_pct, gap_direction, historical_fill_rate)

        # 10. 事件衰减
        f.event_decay = self.computer.compute_event_decay(
            events or [], date)

        # 11. 龙虎榜效应
        f.lhb_effect = self.computer.compute_lhb_effect(
            lhb_buy_amount, lhb_sell_amount, total_turnover,
            is_bullish_seat, days_since_lhb)

        # 12. 市场情绪传导
        f.sentiment_trans = self.computer.compute_sentiment_trans(
            market_tail_change_pct, stock_beta,
            market_sentiment_idx, vix_proxy)

        return f

    def cross_sectional_normalize(
        self,
        factor_list: List[OvernightFactors],
    ) -> List[OvernightFactors]:
        """对一组股票的因子值做截面标准化"""
        if len(factor_list) < 2:
            return factor_list

        factor_names = [
            'tail_rs', 'tail_vol_struct', 'overnight_mom',
            'intraday_rev', 'auction_sig', 'sector_alpha',
            'streak_exp', 'flow_accel', 'gap_fill_prob',
            'event_decay', 'lhb_effect', 'sentiment_trans',
        ]

        for fname in factor_names:
            raw_values = [getattr(f, fname) for f in factor_list]
            # Winsorize + Z-score
            winsorized = self.normalizer.winsorize(raw_values)
            normalized = self.normalizer.zscore(winsorized)
            for i, f in enumerate(factor_list):
                setattr(f, fname, normalized[i])

        return factor_list

    def evaluate(
        self,
        factors: OvernightFactors,
        custom_weights: Dict[str, float] = None,
    ) -> OvernightFactors:
        """评估并融合因子"""
        score, forecast = self.fusion.fuse(factors, custom_weights)
        factors.composite_score = score
        factors.t1_return_forecast = forecast
        return factors

    def batch_evaluate(
        self,
        factor_list: List[OvernightFactors],
        custom_weights: Dict[str, float] = None,
    ) -> List[OvernightFactors]:
        """批量评估"""
        # 先截面标准化
        normalized = self.cross_sectional_normalize(factor_list)
        # 再逐个融合
        for f in normalized:
            self.evaluate(f, custom_weights)
        return normalized

    # ── 报告生成 ──
    def generate_report(
        self,
        factor_list: List[OvernightFactors],
        top_n: int = 20,
    ) -> str:
        """生成Alpha因子报告"""
        sorted_factors = sorted(
            factor_list,
            key=lambda f: f.composite_score,
            reverse=True
        )

        lines = [
            '=' * 70,
            f'V13.1 M57 隔夜Alpha因子分析报告',
            f'分析时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'覆盖股票数：{len(factor_list)}',
            '=' * 70,
            f'\n{"─" * 60}',
            f'Top {min(top_n, len(sorted_factors))} T+1预期收益排名',
            f'{"─" * 60}',
        ]

        for rank, f in enumerate(sorted_factors[:top_n], 1):
            lines.append(
                f'\n#{rank} {f.code} | Alpha评分：{f.composite_score:.4f} | '
                f'T+1预期：{f.t1_return_forecast:+.3f}%'
            )
            lines.append(f'  尾盘RS：{f.tail_rs:+.3f} | 量能结构：{f.tail_vol_struct:+.3f} | '
                         f'日内反转：{f.intraday_rev:+.3f}')
            lines.append(f'  资金加速：{f.flow_accel:+.3f} | 竞价信号：{f.auction_sig:+.3f} | '
                         f'板块Alpha：{f.sector_alpha:+.3f}')
            lines.append(f'  事件衰减：{f.event_decay:+.3f} | 连板预期：{f.streak_exp:+.3f}')

        # 因子有效性汇总
        lines.append(f'\n{"─" * 60}')
        lines.append('因子方向统计（全样本）')
        lines.append(f'{"─" * 60}')
        factor_names = ['tail_rs', 'tail_vol_struct', 'intraday_rev',
                        'flow_accel', 'auction_sig', 'sector_alpha',
                        'overnight_mom', 'streak_exp', 'gap_fill_prob',
                        'event_decay', 'lhb_effect', 'sentiment_trans']
        for fname in factor_names:
            vals = [getattr(f, fname) for f in factor_list]
            pos_count = sum(1 for v in vals if v > 0)
            mean_val = sum(vals) / len(vals) if vals else 0
            lines.append(f'  {fname:20s}: 正向占比 {pos_count/len(vals)*100:5.1f}% | '
                         f'均值 {mean_val:+.4f}')

        lines.append('=' * 70)
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 导出
# ═══════════════════════════════════════════════════════════════

__all__ = [
    'OvernightFactors', 'EventSignal',
    'SingleFactorComputer', 'CrossSectionNormalizer',
    'FactorFusion', 'OvernightAlphaEngine',
]
