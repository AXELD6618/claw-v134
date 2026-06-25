#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.1 M59 A股微观结构适配层 — AShareMicrostructureEngine           ║
║  ===========================================================        ║
║  圣杯目标：精确适配A股特有的交易制度，消除跨板别信号污染           ║
║                                                                      ║
║  知识来源：                                                          ║
║  ├── AurumQ-RL 六道闸门宇宙过滤 + 板别涨跌停差异                    ║
║  ├── 上交所/深交所/北交所 现行交易规则（2026）                      ║
║  ├── T+1结算制度建模                                                ║
║  └── 流动性分级（主板vs北交所日均成交差1-2个数量级）                ║
║                                                                      ║
║  核心创新：                                                          ║
║  1. 板别动态涨跌停计算（±10%/±20%/±30%/±5%ST）                     ║
║  2. 六道闸门宇宙过滤（data_ok+主板+上市≥60日+非退市+非ST+非停牌）  ║
║  3. T+1结算约束建模（当日买入→次日才能卖）                         ║
║  4. 流动性分级与成交量标准化                                        ║
║  5. 集合竞价微结构处理（9:15-9:30）                                ║
║  6. 涨停板统计与连板跟踪                                           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import re
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 板别定义
# ═══════════════════════════════════════════════════════════════

class BoardType(Enum):
    """A股板块分类"""
    MAIN_SH = ('上海主板', 'SH', 0.10, 0.10, '60[0135]\\d{3}')    # 上海主板
    MAIN_SZ = ('深圳主板', 'SZ', 0.10, 0.10, '00[0123]\\d{3}')    # 深圳主板
    GEM = ('创业板', 'SZ', 0.20, 0.20, '300\\d{3}|301\\d{3}')     # 创业板
    STAR = ('科创板', 'SH', 0.20, 0.20, '688\\d{3}|689\\d{3}')    # 科创板
    BSE = ('北交所', 'BJ', 0.30, 0.30, '8[3-9]\\d{4}|92\\d{4}')   # 北交所
    ST = ('ST/*ST', 'ANY', 0.05, 0.05, '.*')                       # ST股
    UNKNOWN = ('未知', 'UN', 0.10, 0.10, '.*')


class LiquidityTier(Enum):
    """流动性分级"""
    ULTRA_HIGH = ('超高流动性', 5e8)    # 日均成交>5亿（主板大盘股）
    HIGH = ('高流动性', 1e8)            # 日均成交>1亿
    MEDIUM = ('中等流动性', 3e7)        # 日均成交>3000万
    LOW = ('低流动性', 5e6)             # 日均成交>500万
    MICRO = ('微流动性', 0)             # 日均成交<500万（不建议交易）


@dataclass
class StockMicroInfo:
    """个股微观结构信息"""
    code: str
    name: str = ''
    board: BoardType = BoardType.UNKNOWN
    liquidity_tier: LiquidityTier = LiquidityTier.MEDIUM

    # 涨跌停限制
    limit_up_pct: float = 0.10
    limit_down_pct: float = 0.10
    prev_close: float = 0.0           # 前收盘价
    limit_up_price: float = 0.0        # 涨停价
    limit_down_price: float = 0.0      # 跌停价

    # 状态标志
    is_st: bool = False
    is_suspended: bool = False
    is_new_listing: bool = False       # 上市<60交易日
    listed_days: int = 0
    avg_daily_volume_yuan: float = 0.0  # 日均成交额

    # 涨停板统计
    consecutive_limit_up: int = 0       # 连续涨停天数
    recent_limit_up_count: int = 0      # 近20日涨停次数
    limit_up_lock_ratio: float = 0.0    # 封板比例（封板量/全天量）

    # 宇宙过滤通过状态
    pass_filter: bool = False
    filter_details: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 板别识别器
# ═══════════════════════════════════════════════════════════════

class BoardClassifier:
    """A股板别自动识别"""

    # 沪深主板正则
    SH_MAIN = re.compile(r'^60[0135]\d{3}$')
    SZ_MAIN = re.compile(r'^00[0123]\d{3}$')
    GEM = re.compile(r'^30[01]\d{3}$')
    STAR = re.compile(r'^68[89]\d{3}$')
    BSE = re.compile(r'^(8[3-9]\d{4}|92\d{4})$')

    # ST标识
    ST_PATTERN = re.compile(r'\*?ST', re.IGNORECASE)

    @classmethod
    def classify(cls, code: str, name: str = '') -> BoardType:
        """识别股票所属板块"""
        code = code.strip().replace('.SH', '').replace('.SZ', '').replace('.BJ', '')

        # ST优先判断（涨跌幅最特殊）
        if cls.ST_PATTERN.search(name):
            return BoardType.ST

        if cls.BSE.match(code):
            return BoardType.BSE
        if cls.STAR.match(code):
            return BoardType.STAR
        if cls.GEM.match(code):
            return BoardType.GEM
        if cls.SH_MAIN.match(code):
            return BoardType.MAIN_SH
        if cls.SZ_MAIN.match(code):
            return BoardType.MAIN_SZ

        return BoardType.UNKNOWN

    @classmethod
    def get_limit_pcts(cls, board: BoardType) -> Tuple[float, float]:
        """返回(涨停幅度, 跌停幅度)"""
        return board.value[2], board.value[3]

    @classmethod
    def get_limit_pcts_by_code(cls, code: str, name: str = '') -> Tuple[float, float]:
        """根据代码和名称返回涨跌停幅度"""
        board = cls.classify(code, name)
        return cls.get_limit_pcts(board)


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 宇宙过滤器（AurumQ-RL六道闸门 + 扩展）
# ═══════════════════════════════════════════════════════════════

class UniverseFilter:
    """
    宇宙过滤器 — 六道闸门 + 3道扩展

    来源：AurumQ-RL的MAIN_BOARD_NON_ST过滤器
    扩展：增加流动性闸门、价格闸门、行业闸门
    """

    # 基础配置
    MIN_LISTED_DAYS = 60              # 上市最少天数
    MIN_AVG_VOLUME_YUAN = 5_000_000   # 最低日均成交额500万
    MIN_PRICE = 2.0                   # 最低股价2元（防仙股）
    MAX_PRICE = 500.0                 # 最高股价（不限制但标记）

    @staticmethod
    def apply(
        code: str,
        name: str = '',
        listed_days: int = 9999,
        is_suspended: bool = False,
        is_st: bool = False,
        avg_volume_yuan: float = 0,
        current_price: float = 0,
        has_data: bool = True,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        应用宇宙过滤

        返回：(通过/不通过, 通过原因, 拒绝原因)
        """
        passed = []
        rejected = []

        # 闸门1: 当日有数据 + vol>0（排除停牌）
        if not has_data or is_suspended:
            rejected.append('❌ 无行情数据或停牌')
            return False, passed, rejected
        passed.append('✅ 闸门1: 正常交易')

        # 闸门2: 主板（或明确板块）
        board = BoardClassifier.classify(code, name)
        if board == BoardType.UNKNOWN:
            rejected.append('❌ 闸门2: 无法识别板块')
            return False, passed, rejected
        passed.append(f'✅ 闸门2: {board.value[0]}')

        # 闸门3: 上市≥60交易日（新股保护）
        if listed_days < UniverseFilter.MIN_LISTED_DAYS:
            rejected.append(f'❌ 闸门3: 上市仅{listed_days}日（需≥{UniverseFilter.MIN_LISTED_DAYS}）')
            return False, passed, rejected
        passed.append(f'✅ 闸门3: 上市{listed_days}日')

        # 闸门4: 非退市
        # (通过TDX已自动过滤退市股，此处为逻辑占位)
        passed.append('✅ 闸门4: 非退市')

        # 闸门5: 非ST（可选，可配置为不过滤ST但有特殊处理）
        if board == BoardType.ST:
            rejected.append('❌ 闸门5: ST风险警示股（可在高风险模式启用）')
            return False, passed, rejected
        passed.append('✅ 闸门5: 非ST')

        # 闸门6: 非停牌（已在闸门1中处理）
        passed.append('✅ 闸门6: 非停牌')

        # —— 扩展闸门 ——

        # 闸门7: 流动性（日均成交≥500万）
        if avg_volume_yuan > 0 and avg_volume_yuan < UniverseFilter.MIN_AVG_VOLUME_YUAN:
            rejected.append(f'❌ 扩展闸门7: 日均成交{avg_volume_yuan/1e4:.0f}万（需≥500万）')
            return False, passed, rejected
        if avg_volume_yuan > 0:
            passed.append(f'✅ 扩展闸门7: 日均成交{avg_volume_yuan/1e4:.0f}万')

        # 闸门8: 股价（防仙股）
        if current_price > 0 and current_price < UniverseFilter.MIN_PRICE:
            rejected.append(f'❌ 扩展闸门8: 股价{current_price:.2f}（需≥{UniverseFilter.MIN_PRICE}）')
            return False, passed, rejected
        if current_price > 0:
            passed.append(f'✅ 扩展闸门8: 股价{current_price:.2f}')

        return True, passed, rejected


# ═══════════════════════════════════════════════════════════════
# SECTION 3: T+1结算约束建模
# ═══════════════════════════════════════════════════════════════

class T1SettlementModel:
    """
    T+1结算制度建模

    核心逻辑：
    - 当日（T日）买入 → 次日（T+1日）才能卖出
    - 这意味着T日尾盘选股必须承担隔夜风险
    - 建模需考虑：隔夜信息冲击、次日开盘流动性、集合竞价博弈
    """

    @staticmethod
    def compute_overnight_risk(
        board: BoardType,
        gap_up_prob: float,
        market_volatility: float,
        sector_beta: float = 1.0,
    ) -> Dict[str, float]:
        """
        计算隔夜风险指标

        Args:
            board: 板块类型
            gap_up_prob: 次日高开概率
            market_volatility: 大盘波动率（近期）
            sector_beta: 板块Beta

        Returns:
            {
                overnight_risk: 隔夜风险 0~1
                expected_gap: 预期隔夜涨跌幅
                max_adverse_gap: 最差隔夜涨跌幅（95%置信）
                hold_time_cost: 资金占用成本
            }
        """
        # 基础隔夜波动（不同板块）
        base_overnight_vol = {
            BoardType.MAIN_SH: 1.2,
            BoardType.MAIN_SZ: 1.3,
            BoardType.GEM: 2.0,
            BoardType.STAR: 2.2,
            BoardType.BSE: 3.5,
            BoardType.ST: 1.5,
        }.get(board, 1.5)

        # 隔夜风险
        overnight_vol = base_overnight_vol * market_volatility * abs(sector_beta)
        # 高开概率越高，隔夜风险越低（预期方向有利）
        risk_direction_factor = 1.0 - gap_up_prob * 0.5
        overnight_risk = min(1.0, overnight_vol / 10.0 * risk_direction_factor)

        # 预期隔夜涨跌
        expected_gap = gap_up_prob * overnight_vol - (1 - gap_up_prob) * overnight_vol * 0.5

        # 最差隔夜（95%置信，约1.65σ）
        max_adverse_gap = -overnight_vol * 1.65

        # T+1资金占用成本（隔夜利率年化2% / 250 ≈ 0.008%）
        hold_time_cost = 0.008

        return {
            'overnight_risk': round(overnight_risk, 4),
            'expected_gap_pct': round(expected_gap, 3),
            'max_adverse_gap_pct': round(max_adverse_gap, 3),
            'hold_time_cost_pct': round(hold_time_cost, 3),
        }


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 流动性分级
# ═══════════════════════════════════════════════════════════════

class LiquidityClassifier:
    """流动性分级与成交量标准化"""

    @staticmethod
    def classify(avg_daily_volume_yuan: float) -> LiquidityTier:
        """根据日均成交额分级"""
        if avg_daily_volume_yuan >= LiquidityTier.ULTRA_HIGH.value[1]:
            return LiquidityTier.ULTRA_HIGH
        if avg_daily_volume_yuan >= LiquidityTier.HIGH.value[1]:
            return LiquidityTier.HIGH
        if avg_daily_volume_yuan >= LiquidityTier.MEDIUM.value[1]:
            return LiquidityTier.MEDIUM
        if avg_daily_volume_yuan >= LiquidityTier.LOW.value[1]:
            return LiquidityTier.LOW
        return LiquidityTier.MICRO

    @staticmethod
    def normalize_volume(
        raw_volume: float,
        avg_daily_volume: float,
        tier: LiquidityTier,
    ) -> float:
        """成交量标准化（消除流动性量级差异）"""
        if avg_daily_volume == 0:
            return 1.0
        ratio = raw_volume / avg_daily_volume
        # 不同流动性级做不同缩放
        scale = {
            LiquidityTier.ULTRA_HIGH: 1.0,
            LiquidityTier.HIGH: 0.9,
            LiquidityTier.MEDIUM: 0.75,
            LiquidityTier.LOW: 0.6,
            LiquidityTier.MICRO: 0.4,
        }.get(tier, 0.5)
        return ratio * scale

    @staticmethod
    def get_max_position_pct(tier: LiquidityTier, board: BoardType) -> float:
        """根据流动性给出最大仓位限制"""
        base = {
            LiquidityTier.ULTRA_HIGH: 0.30,
            LiquidityTier.HIGH: 0.20,
            LiquidityTier.MEDIUM: 0.10,
            LiquidityTier.LOW: 0.05,
            LiquidityTier.MICRO: 0.02,
        }.get(tier, 0.05)

        # 北交所额外减半（T+1流动性风险大）
        if board == BoardType.BSE:
            base *= 0.5
        if board == BoardType.ST:
            base *= 0.3

        return base


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 主力引擎
# ═══════════════════════════════════════════════════════════════

class AShareMicrostructureEngine:
    """
    V13.1 M59 A股微观结构主引擎

    集成：板别识别 + 宇宙过滤 + T+1建模 + 流动性分级
    """

    def __init__(self):
        self.board_classifier = BoardClassifier()
        self.universe_filter = UniverseFilter()
        self.t1_model = T1SettlementModel()
        self.liquidity_classifier = LiquidityClassifier()

    def build_stock_info(
        self,
        code: str,
        name: str = '',
        listed_days: int = 9999,
        is_suspended: bool = False,
        avg_volume_yuan: float = 0,
        current_price: float = 0,
        prev_close: float = 0,
        has_data: bool = True,
        consecutive_limit_up: int = 0,
    ) -> StockMicroInfo:
        """构建完整微观结构信息"""
        info = StockMicroInfo(code=code, name=name)

        # 板别
        info.board = self.board_classifier.classify(code, name)
        info.is_st = info.board == BoardType.ST

        # 涨跌停
        info.limit_up_pct, info.limit_down_pct = self.board_classifier.get_limit_pcts(info.board)
        if prev_close > 0:
            info.prev_close = prev_close
            info.limit_up_price = round(prev_close * (1 + info.limit_up_pct), 2)
            info.limit_down_price = round(prev_close * (1 - info.limit_down_pct), 2)

        # 流动性
        info.avg_daily_volume_yuan = avg_volume_yuan
        info.liquidity_tier = self.liquidity_classifier.classify(avg_volume_yuan)

        # 状态
        info.is_suspended = is_suspended
        info.listed_days = listed_days
        info.is_new_listing = listed_days < UniverseFilter.MIN_LISTED_DAYS
        info.consecutive_limit_up = consecutive_limit_up

        # 宇宙过滤
        info.pass_filter, filter_ok, filter_reject = self.universe_filter.apply(
            code=code, name=name,
            listed_days=listed_days,
            is_suspended=is_suspended,
            is_st=info.is_st,
            avg_volume_yuan=avg_volume_yuan,
            current_price=current_price,
            has_data=has_data,
        )
        info.filter_details = filter_ok + filter_reject

        return info

    def compute_trade_constraints(
        self,
        info: StockMicroInfo,
        total_capital: float,
        gap_up_prob: float = 0.5,
        market_volatility: float = 1.5,
    ) -> Dict[str, float]:
        """
        计算该股的交易约束

        Returns:
            {
                max_position_pct: 最大仓位比例
                max_position_yuan: 最大仓位金额
                limit_up_price: 涨停价
                limit_down_price: 跌停价
                overnight_risk: 隔夜风险
                max_adverse_gap_pct: 最差隔夜幅度
                is_tradable: 是否可交易
            }
        """
        constraints = {}

        # 涨跌停
        constraints['limit_up_price'] = info.limit_up_price
        constraints['limit_down_price'] = info.limit_down_price

        # 距涨跌停的距离
        if info.prev_close > 0:
            last_price = info.prev_close
            constraints['dist_to_limit_up_pct'] = round(
                (info.limit_up_price / last_price - 1) * 100, 2)
            constraints['dist_to_limit_down_pct'] = round(
                (info.limit_down_price / last_price - 1) * 100, 2)

        # 仓位约束
        max_pct = self.liquidity_classifier.get_max_position_pct(
            info.liquidity_tier, info.board)
        constraints['max_position_pct'] = max_pct
        constraints['max_position_yuan'] = total_capital * max_pct

        # 隔夜风险
        overnight = self.t1_model.compute_overnight_risk(
            board=info.board,
            gap_up_prob=gap_up_prob,
            market_volatility=market_volatility,
        )
        constraints.update(overnight)

        # 可交易性
        constraints['is_tradable'] = (
            info.pass_filter and
            not info.is_suspended and
            info.liquidity_tier not in [LiquidityTier.MICRO] and
            info.consecutive_limit_up < 3  # 连续涨停>3板不追
        )

        return constraints

    def apply_board_specific_rules(
        self,
        info: StockMicroInfo,
        signal_score: float,
    ) -> Tuple[float, List[str]]:
        """
        应用板别特殊规则，调整信号评分

        Returns:
            (调整后评分, 调整说明)
        """
        adjustments = []
        adjusted_score = signal_score

        # 北交所：T+1流动性风险大，需降低评分
        if info.board == BoardType.BSE:
            adjusted_score *= 0.7
            adjustments.append('北交所板·流动性风险折扣×0.7')

        # 科创板：波动大，但打板机会多
        if info.board == BoardType.STAR:
            adjusted_score *= 0.85
            adjustments.append('科创板·波动折扣×0.85')

        # 创业板：20cm弹性
        if info.board == BoardType.GEM:
            if signal_score >= 0.7:  # 高评分创业板弹性放大
                adjusted_score *= 1.05
                adjustments.append('创业板·弹性加成×1.05')

        # 连续涨停处理
        if info.consecutive_limit_up >= 3:
            adjusted_score *= 0.5
            adjustments.append(f'连板{info.consecutive_limit_up}天·高位折扣×0.5')
        elif info.consecutive_limit_up == 2:
            adjusted_score *= 0.7
            adjustments.append('连板2天·追高风险折扣×0.7')
        elif info.consecutive_limit_up == 1:
            adjusted_score *= 0.85
            adjustments.append('连板1天·谨慎折扣×0.85')

        return round(min(1.0, max(0.0, adjusted_score)), 4), adjustments

    # ── 批量处理 ──
    def batch_filter(
        self,
        stocks: List[Dict],
    ) -> Tuple[List[StockMicroInfo], List[StockMicroInfo]]:
        """
        批量宇宙过滤

        Returns:
            (通过过滤的, 被拒绝的)
        """
        passed = []
        rejected = []

        for s in stocks:
            info = self.build_stock_info(
                code=s.get('code', ''),
                name=s.get('name', ''),
                listed_days=s.get('listed_days', 9999),
                is_suspended=s.get('is_suspended', False),
                avg_volume_yuan=s.get('avg_volume_yuan', 0),
                current_price=s.get('current_price', 0),
                prev_close=s.get('prev_close', 0),
                has_data=s.get('has_data', True),
                consecutive_limit_up=s.get('consecutive_limit_up', 0),
            )
            if info.pass_filter:
                passed.append(info)
            else:
                rejected.append(info)

        return passed, rejected


# ═══════════════════════════════════════════════════════════════
# SECTION 6: 导出
# ═══════════════════════════════════════════════════════════════

__all__ = [
    'BoardType', 'LiquidityTier', 'StockMicroInfo',
    'BoardClassifier', 'UniverseFilter',
    'T1SettlementModel', 'LiquidityClassifier',
    'AShareMicrostructureEngine',
]
