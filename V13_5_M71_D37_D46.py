#!/usr/bin/env python3
"""
V13.5.20: 经典交易理论深度集成 — D37-D46 新维度
==============================================
融合8大经典交易方法 + 全网最佳实战经验 + TDX数据源验证
将趋势跟随/周线6法/老鸭头/筹码擒龙/三倍量/试盘线/三均线/主升浪 量化编码

数据源映射:
  tdx_kline(period=4/5) → 日线/周线K线
  tdx_quotes → 委比/外盘内盘/量比
  tdx_api_data(zjlx) → 主力净额

10个新维度总满分: 48分 (D37-D46)
V13.5.20总维度: 46维 (D1-D36 + D37-D46)

作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.20
日期: 2026-07-05
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict


# ────────────────────────────────────────────────
# 复刻M71的数据结构（不依赖import）
# ────────────────────────────────────────────────

@dataclass
class KlineBar:
    """K线柱"""
    date: str = ""
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    chg_pct: float = 0.0
    volume_ratio: float = 1.0
    amplitude: float = 0.0

@dataclass
class DimensionScore:
    """维度评分"""
    name: str = ""
    max_score: float = 0.0
    actual_score: float = 0.0
    raw_data: dict = field(default_factory=dict)
    detail: str = ""
    passed: bool = False


# ────────────────────────────────────────────────
# 工具函数
# ────────────────────────────────────────────────

def ema(data: List[float], period: int) -> List[float]:
    """指数移动平均"""
    if len(data) < period:
        return [sum(data) / len(data)] * len(data)
    result = [0.0] * len(data)
    multiplier = 2.0 / (period + 1)
    result[period - 1] = sum(data[:period]) / period
    for i in range(period, len(data)):
        result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]
    for i in range(period - 1):
        result[i] = result[period - 1]
    return result

def sma(data: List[float], period: int) -> List[float]:
    """简单移动平均"""
    if len(data) < period:
        return [sum(data) / len(data)] * len(data)
    result = [0.0] * len(data)
    for i in range(len(data)):
        if i < period - 1:
            result[i] = sum(data[:i + 1]) / (i + 1)
        else:
            result[i] = sum(data[i - period + 1:i + 1]) / period
    return result

def compute_macd(closes: List[float], fast=12, slow=26, signal=9):
    """
    计算MACD
    返回: (dif_list, dea_list, hist_list)
    """
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = sma(dif, signal)
    hist = [2 * (dif[i] - dea[i]) for i in range(len(dif))]
    return dif, dea, hist

def daily_to_weekly(daily_klines: List[KlineBar]) -> List[KlineBar]:
    """
    将日K线转换为周K线
    以每周末(周五)为界聚合
    """
    if not daily_klines:
        return []

    weekly = []
    week_start = None
    week_data = []

    for k in daily_klines:
        if not k.date or len(k.date) < 8:
            continue
        try:
            dt = datetime.strptime(k.date, '%Y%m%d')
        except ValueError:
            continue

        # 获取周标识(年+周号)
        iso = dt.isocalendar()
        week_id = f"{iso[0]}{iso[1]:02d}"

        if week_start != week_id:
            if week_data:
                weekly.append(_aggregate_week(week_data))
            week_start = week_id
            week_data = [k]
        else:
            week_data.append(k)

    if week_data:
        weekly.append(_aggregate_week(week_data))

    return weekly

def _aggregate_week(week_bars: List[KlineBar]) -> KlineBar:
    """聚合一周的日K线为一根周K线"""
    if len(week_bars) == 1:
        return week_bars[0]

    opens = [b.open for b in week_bars]
    highs = [b.high for b in week_bars]
    lows = [b.low for b in week_bars]
    closes = [b.close for b in week_bars]
    volumes = [b.volume for b in week_bars]
    amounts = [b.amount for b in week_bars]

    first_open = opens[0]
    last_close = closes[-1]
    week_chg_pct = (last_close - week_bars[0].close) / week_bars[0].close * 100 if week_bars[0].close else 0

    return KlineBar(
        date=week_bars[-1].date,
        open=first_open,
        high=max(highs),
        low=min(lows),
        close=last_close,
        volume=sum(volumes),
        amount=sum(amounts),
        chg_pct=week_chg_pct,
        volume_ratio=sum(volumes) / max(sum(volumes[:len(volumes)//2]), 1) if len(volumes) > 2 else 1.0,
        amplitude=(max(highs) - min(lows)) / week_bars[0].close * 100 if week_bars[0].close else 0
    )


# ═══════════════════════════════════════════════════════════
# D37-D46 新维度评分引擎
# ═══════════════════════════════════════════════════════════

class NewDimensionScorer:
    """D37-D46: 经典交易理论新维度评分器"""

    def __init__(self):
        pass

    # ───────────────────────────────────
    # D37: 周线多头排列 (5分)
    # ───────────────────────────────────
    def score_weekly_ma_alignment(self, weekly_klines: List[KlineBar]) -> DimensionScore:
        """
        D37: 周线多头排列 (5分)
        
        来源: 趋势交易 - 周线均线多头排列
        逻辑: 5周MA > 10周MA > 20周MA → 中期上升趋势确认
              价格站在均线上方说明多方控盘
        
        评分:
        1. 5W>10W>20W 且价格>5W → 5分(强多头)
        2. 5W>10W 但10W<20W(修复中) → 3分(转多)
        3. 仅价格>20W → 1分(弱多)
        
        数据: tdx_kline period=5 (周K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(weekly_klines) < 20:
            return DimensionScore('D37周线多头', max_score, 0.0, {}, '周K线不足20根', False)

        closes = [k.close for k in weekly_klines]
        ma5 = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)

        cur_close = closes[-1]
        cur_ma5 = ma5[-1]
        cur_ma10 = ma10[-1]
        cur_ma20 = ma20[-1]

        raw['ma5'] = round(cur_ma5, 2)
        raw['ma10'] = round(cur_ma10, 2)
        raw['ma20'] = round(cur_ma20, 2)
        raw['close'] = round(cur_close, 2)

        # 多头排列: 5W > 10W > 20W
        ma_bull_aligned = cur_ma5 > cur_ma10 > cur_ma20
        price_above_5w = cur_close > cur_ma5
        price_above_20w = cur_close > cur_ma20
        ma5_slope = (cur_ma5 - ma5[-5]) / ma5[-5] * 100 if len(ma5) >= 5 and ma5[-5] > 0 else 0
        ma10_slope = (cur_ma10 - ma10[-5]) / ma10[-5] * 100 if len(ma10) >= 5 and ma10[-5] > 0 else 0

        if ma_bull_aligned and price_above_5w:
            score = 5.0
            detail_parts.append(
                f'周线强多头! 5W={cur_ma5:.1f}>10W={cur_ma10:.1f}>20W={cur_ma20:.1f} 价在线上'
            )
            raw['signal'] = 'strong_bull'
        elif cur_ma5 > cur_ma10 and price_above_20w:
            score = 3.0
            detail_parts.append(
                f'周线转多: 5W({cur_ma5:.1f})>10W({cur_ma10:.1f}) 价站上20W'
            )
            raw['signal'] = 'turning_bull'
        elif price_above_20w and ma5_slope > 0:
            score = 1.0
            detail_parts.append(f'周线弱多: 价站上20W MA5向上({ma5_slope:.1f}%)')
            raw['signal'] = 'weak_bull'
        else:
            detail_parts.append(
                f'周线非多头: MA5={cur_ma5:.1f} MA10={cur_ma10:.1f} MA20={cur_ma20:.1f}'
            )
            raw['signal'] = 'no_bull'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D37周线多头', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D38: 周线平台突破 (5分)
    # ───────────────────────────────────
    def score_weekly_platform_breakout(self, weekly_klines: List[KlineBar]) -> DimensionScore:
        """
        D38: 周线平台突破 (5分)
        
        来源: 周线选股 - 周线平台突破
        逻辑: 股价在周线上横盘震荡=主力吸筹，放量突破平台=拉升开始
        
        评分:
        1. 平台形成(5-15周窄幅震荡)+本周放量突破上轨 → 5分
        2. 接近突破(本周收在平台高点附近+放量) → 3分
        3. 平台内运行 → 1分
        
        数据: tdx_kline period=5 (周K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(weekly_klines) < 15:
            return DimensionScore('D38平台突破', max_score, 0.0, {}, '周K线不足15根', False)

        # 取最近15周数据检测平台
        recent = weekly_klines[-15:]
        highs = [k.high for k in recent]
        lows = [k.low for k in recent]
        closes = [k.close for k in recent]
        volumes = [k.volume for k in recent]

        # 平台范围: 去除极端值的区间
        sorted_highs = sorted(highs)
        sorted_lows = sorted(lows)
        # 用80百分位的高和20百分位的低作为平台区间
        platform_high = sorted_highs[int(len(sorted_highs) * 0.85)]
        platform_low = sorted_lows[int(len(sorted_lows) * 0.15)]
        platform_range = (platform_high - platform_low) / platform_low * 100 if platform_low > 0 else 100

        # 平台判断: 区间<25%视为窄幅震荡
        is_platform = platform_range < 25.0
        raw['platform_high'] = round(platform_high, 2)
        raw['platform_low'] = round(platform_low, 2)
        raw['platform_range_pct'] = round(platform_range, 1)

        # 本周数据
        this_week = recent[-1]
        prev_week_vol = volumes[-2] if len(volumes) >= 2 else volumes[-1]
        vol_expand = this_week.volume > prev_week_vol * 1.5
        break_above = this_week.close > platform_high

        if is_platform and break_above and vol_expand:
            score = 5.0
            detail_parts.append(
                f'周线平台突破! 平台区间{platform_range:.1f}% '
                f'本周放量突破上轨{platform_high:.2f}'
            )
            raw['signal'] = 'platform_breakout'
        elif is_platform and (break_above or vol_expand):
            score = 3.0
            detail_parts.append(
                f'周线近突破: 平台区间{platform_range:.1f}% '
                f'{"突破上轨" if break_above else "放量" if vol_expand else "蓄势中"}'
            )
            raw['signal'] = 'near_breakout'
        elif is_platform:
            score = 1.0
            detail_parts.append(f'周线平台盘整: 区间{platform_range:.1f}% 待突破')
            raw['signal'] = 'in_platform'
        else:
            detail_parts.append(f'周线非平台: 振幅{platform_range:.1f}%')
            raw['signal'] = 'no_platform'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D38平台突破', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D39: 周线MACD金叉 (3分)
    # ───────────────────────────────────
    def score_weekly_macd_golden_cross(self, weekly_klines: List[KlineBar]) -> DimensionScore:
        """
        D39: 周线MACD金叉 (3分)
        
        来源: 周线选股 - 周线MACD金叉
        逻辑: MACD金叉是中期多空转换信号，零轴上金叉可靠性更高
        
        评分:
        1. 本周金叉 + 零轴上方 → 3分
        2. 本周金叉 + 零轴下方 → 2分
        3. DIF已在DEA上方(已金叉) → 1分
        
        数据: tdx_kline period=5 (周K)
        """
        max_score = 3.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(weekly_klines) < 35:
            return DimensionScore('D39周线MACD', max_score, 0.0, {}, '周K线不足35根(需MACD计算)', False)

        closes = [k.close for k in weekly_klines]
        dif, dea, hist = compute_macd(closes)

        cur_dif = dif[-1]
        cur_dea = dea[-1]
        prev_dif = dif[-2]
        prev_dea = dea[-2]
        cur_hist = hist[-1]
        prev_hist = hist[-2]

        raw['dif'] = round(cur_dif, 2)
        raw['dea'] = round(cur_dea, 2)
        raw['hist'] = round(cur_hist, 2)

        # 金叉检测: 前一日DIF<DEA 且 今DIF>DEA
        golden_cross_today = prev_dif <= prev_dea and cur_dif > cur_dea
        already_bull = cur_dif > cur_dea
        above_zero = cur_dif > 0 and cur_dea > 0

        if golden_cross_today and above_zero:
            score = 3.0
            detail_parts.append(
                f'周线MACD零轴上金叉! DIF={cur_dif:.2f} DEA={cur_dea:.2f} → 强多信号'
            )
            raw['signal'] = 'golden_cross_above_zero'
        elif golden_cross_today:
            score = 2.0
            detail_parts.append(
                f'周线MACD金叉(零轴下): DIF={cur_dif:.2f} DEA={cur_dea:.2f}'
            )
            raw['signal'] = 'golden_cross_below'
        elif already_bull:
            # 检测hist柱是否在变长（动能增强）
            hist_expanding = cur_hist > prev_hist
            score = 1.5 if hist_expanding else 1.0
            detail_parts.append(
                f'周线MACD多头: DIF={cur_dif:.2f}>DEA={cur_dea:.2f} '
                f'{"动能增强" if hist_expanding else "动能减弱"}'
            )
            raw['signal'] = 'already_bull'
        else:
            detail_parts.append(f'周线MACD空头: DIF={cur_dif:.2f}<DEA={cur_dea:.2f}')
            raw['signal'] = 'bear'

        score = min(max_score, score)
        passed = score >= 2.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D39周线MACD', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D40: 周线回踩支撑 (5分)
    # ───────────────────────────────────
    def score_weekly_pullback_support(self, weekly_klines: List[KlineBar]) -> DimensionScore:
        """
        D40: 周线回踩支撑 (5分)
        
        来源: 周线选股 - 周线回踩支撑
        逻辑: 上升趋势中回调至20周均线+缩量 = 主力洗盘后的买入良机
        
        评分:
        1. 回踩20W(<3%) + 缩量到前期一半 + 止跌信号 → 5分
        2. 回踩5W/10W + 缩量 → 3分
        3. 接近20W但有支撑迹象 → 1分
        
        数据: tdx_kline period=5 (周K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(weekly_klines) < 22:
            return DimensionScore('D40回踩支撑', max_score, 0.0, {}, '周K线不足22根', False)

        closes = [k.close for k in weekly_klines]
        volumes = [k.volume for k in weekly_klines]
        ma5 = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)

        cur_close = closes[-1]
        cur_ma5 = ma5[-1]
        cur_ma10 = ma10[-1]
        cur_ma20 = ma20[-1]
        cur_vol = volumes[-1]
        prev_vol = volumes[-2]
        avg_vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else cur_vol

        # 检测趋势: 20W趋势向上（至少过去4周ma20递增）
        ma20_trend_up = all(ma20[-i] >= ma20[-i-1] for i in range(1, 5))

        # 距离20W的距离
        dist_to_20w = abs(cur_close - cur_ma20) / cur_ma20 * 100 if cur_ma20 > 0 else 100

        raw['ma5'] = round(cur_ma5, 2)
        raw['ma10'] = round(cur_ma10, 2)
        raw['ma20'] = round(cur_ma20, 2)
        raw['dist_to_20w'] = round(dist_to_20w, 1)
        raw['ma20_trend_up'] = ma20_trend_up

        # 缩量判断
        vol_shrink = cur_vol < avg_vol_5 * 0.7

        # 止跌信号: 本周收阳或十字星
        this_week = weekly_klines[-1]
        is_bullish = this_week.close > this_week.open
        is_doji = abs(this_week.close - this_week.open) / (this_week.high - this_week.low) < 0.3 if this_week.high != this_week.low else False
        stop_signal = is_bullish or is_doji

        if ma20_trend_up and dist_to_20w < 3.0 and vol_shrink and stop_signal:
            score = 5.0
            detail_parts.append(
                f'周线回踩20W支撑! 距20W={dist_to_20w:.1f}% 缩量+止跌 → 绝佳买点'
            )
            raw['signal'] = 'ideal_pullback'
        elif ma20_trend_up and dist_to_20w < 5.0 and vol_shrink:
            score = 3.0
            detail_parts.append(
                f'周线回踩均线: 距20W={dist_to_20w:.1f}% 缩量'
            )
            raw['signal'] = 'pullback'
        elif ma20_trend_up and dist_to_20w < 8.0:
            score = 1.0
            detail_parts.append(f'周线接近20W支撑: 距{dist_to_20w:.1f}%')
            raw['signal'] = 'near_support'
        else:
            detail_parts.append(
                f'周线离20W较远: {dist_to_20w:.1f}% '
                f'{"趋势向上" if ma20_trend_up else "趋势不明"}'
            )
            raw['signal'] = 'no_support'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D40回踩支撑', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D41: 周线MACD底背离 (5分)
    # ───────────────────────────────────
    def score_weekly_macd_divergence(self, weekly_klines: List[KlineBar]) -> DimensionScore:
        """
        D41: 周线MACD底背离 (5分)
        
        来源: 周线选股 - 周线MACD底背离
        逻辑: 股价创新低但MACD不创新低 = 下跌动能衰竭 = 反转前兆
        
        评分:
        1. 底背离清晰(两个波段比价) + 金叉确认 → 5分
        2. 疑似底背离 + DIF回升 → 3分
        3. DIF有回升迹象 → 1分
        
        数据: tdx_kline period=5 (周K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(weekly_klines) < 40:
            return DimensionScore('D41周线背离', max_score, 0.0, {}, '周K线不足40根', False)

        closes = [k.close for k in weekly_klines]
        lows = [k.low for k in weekly_klines]
        dif, dea, hist = compute_macd(closes)

        # 找最近两个波段低点: 各取最近15-30周范围
        # 波段1: [-30, -20] 范围
        # 波段2: [-15, -1] 范围
        range1_lows = lows[-30:-18] if len(lows) >= 30 else lows[:15]
        range1_dif = dif[-30:-18] if len(dif) >= 30 else dif[:15]
        range2_lows = lows[-12:-1]
        range2_dif = dif[-12:-1]

        if not range1_lows or not range2_lows:
            return DimensionScore('D41周线背离', max_score, 0.0, {}, '数据不足', False)

        # 找到两个波段的最低点和对应的DIF
        range1_min_low = min(range1_lows)
        range1_min_dif = min(range1_dif)
        range2_min_low = min(range2_lows)
        range2_min_dif = min(range2_dif)

        # 底背离: 价格新低但DIF更高
        price_new_low = range2_min_low < range1_min_low * 0.98  # 跌幅>2%为新低
        dif_higher = range2_min_dif > range1_min_dif * 1.05    # DIF高>5%

        raw['range1_low'] = round(range1_min_low, 2)
        raw['range2_low'] = round(range2_min_low, 2)
        raw['range1_dif'] = round(range1_min_dif, 2)
        raw['range2_dif'] = round(range2_min_dif, 2)

        # DIF回归检测
        dif_rising = dif[-1] > dif[-5] and dif[-1] > dif[-3]

        if price_new_low and dif_higher:
            score = 5.0
            detail_parts.append(
                f'周线MACD底背离! 价新低{range2_min_low:.1f}<{range1_min_low:.1f} '
                f'但DIF抬高{range2_min_dif:.2f}>{range1_min_dif:.2f}'
            )
            raw['signal'] = 'confirmed_divergence'
        elif dif_rising and dif[-1] < 0:
            score = 3.0
            detail_parts.append(
                f'周线潜在底背离: DIF回升中({dif[-1]:.2f}) 待金叉确认'
            )
            raw['signal'] = 'potential_divergence'
        elif dif_rising:
            score = 1.0
            detail_parts.append(f'周线DIF回升: {dif[-1]:.2f} → 偏多')
            raw['signal'] = 'dif_rising'
        else:
            detail_parts.append(f'周线无背离')
            raw['signal'] = 'no_divergence'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D41周线背离', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D42: 老鸭头形态 (5分)
    # ───────────────────────────────────
    def score_duck_head_pattern(self, daily_klines: List[KlineBar]) -> DimensionScore:
        """
        D42: 老鸭头形态 (5分)
        
        来源: 经典形态学 - 老鸭头
        逻辑: 
          鸭颈: 5/10MA放量上穿60MA (起涨)
          鸭鼻孔: 5/10MA死叉快速回金叉+缩量 (洗盘)
          鸭嘴: 5/10MA再次金叉+站上60MA (拉升启动)
        
        评分:
        1. 完整鸭颈+鸭鼻+鸭嘴 → 5分
        2. 鸭颈+鸭鼻形成中 → 3分
        3. 仅鸭颈 → 1分
        
        数据: tdx_kline period=4 (日K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(daily_klines) < 70:
            return DimensionScore('D42老鸭头', max_score, 0.0, {}, '日K线不足70根', False)

        closes = [k.close for k in daily_klines]
        volumes = [k.volume for k in daily_klines]

        ma5 = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma60 = sma(closes, 60)

        cur_ma5 = ma5[-1]
        cur_ma10 = ma10[-1]
        cur_ma60 = ma60[-1]
        cur_price = closes[-1]

        raw['ma5'] = round(cur_ma5, 2)
        raw['ma10'] = round(cur_ma10, 2)
        raw['ma60'] = round(cur_ma60, 2)

        # ── 鸭颈检测: 5/10MA在最近30日内放量上穿60MA ──
        duck_neck = False
        neck_idx = -1
        for i in range(-30, -5):
            if (ma5[i] > ma60[i] and ma5[i-1] <= ma60[i-1] and
                ma10[i] > ma60[i] and ma10[i-1] <= ma60[i-1]):
                if volumes[i] > volumes[i-1] * 1.5:
                    duck_neck = True
                    neck_idx = i
                    break

        # ── 鸭鼻检测: 鸭颈之后5/10MA死叉后5日内快速回金叉+缩量 ──
        duck_nose = False
        if duck_neck and neck_idx < -15:
            nose_start = neck_idx + 3  # 鸭颈后3日开始查
            for i in range(nose_start, -5):
                # 检测: 死叉→5日内金叉
                if ma5[i] < ma10[i] and ma5[i-1] >= ma10[i-1]:  # 死叉日
                    # 此后5日检查金叉
                    for j in range(i+1, min(i+8, -1)):
                        if ma5[j] > ma10[j] and ma5[j-1] <= ma10[j-1]:  # 金叉
                            nose_vol = volumes[i:j+1]
                            avg_nose_vol = sum(nose_vol) / len(nose_vol)
                            pre_vol = volumes[i-10:i]
                            avg_pre_vol = sum(pre_vol) / len(pre_vol) if pre_vol else avg_nose_vol * 2
                            if avg_nose_vol < avg_pre_vol * 0.7:  # 缩量>30%
                                duck_nose = True
                                nose_idx = j
                                break
                    break

        # ── 鸭嘴检测: 5/10MA再次金叉 + 站上60MA ──
        duck_mouth = False
        if duck_nose:
            # 当前5/10MA都在60MA上方
            mouth_formed = cur_ma5 > cur_ma60 and cur_ma10 > cur_ma60
            # 检测近期5/10MA是否刚金叉
            recent_golden = False
            for i in range(-10, -1):
                if ma5[i] > ma10[i] and ma5[i-1] <= ma10[i-1]:
                    recent_golden = True
                    break
            duck_mouth = mouth_formed and recent_golden

        if duck_neck and duck_nose and duck_mouth:
            score = 5.0
            detail_parts.append('老鸭头完整形态! 鸭颈→鸭鼻(洗盘)→鸭嘴(再拉升) 信号明确!')
            raw['signal'] = 'full_duck_head'
        elif duck_neck and duck_nose:
            score = 3.0
            detail_parts.append('老鸭头进行中: 鸭颈+鸭鼻已形成 等待鸭嘴金叉')
            raw['signal'] = 'duck_neck_nose'
        elif duck_neck:
            score = 1.0
            detail_parts.append('鸭颈形成(5/10MA放量上穿60MA)')
            raw['signal'] = 'duck_neck_only'
        else:
            detail_parts.append('未检测到老鸭头形态')
            raw['signal'] = 'no_duck_head'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D42老鸭头', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D43: 筹码擒龙 (5分)
    # ───────────────────────────────────
    def score_chip_dragon(self, daily_klines: List[KlineBar]) -> DimensionScore:
        """
        D43: 筹码擒龙 (5分)
        
        来源: 筹码擒龙指标(融合版)
        逻辑: 
          - 主力成本线近似=EMA((H+L+C)/3, 34)，代表50%筹码成本
          - 量能饱和度=当前成交额/历史N日最大成交额
          - 趋势=MA5>MA10>MA20 多头排列
        三条件共振=主力开始控盘拉升
        
        评分:
        1. 站上成本线 + 量能饱和≥0.9 + 多头排列 → 5分
        2. 站上成本线 + 多头排列 → 3分
        3. 仅站上成本线 → 1分
        
        数据: tdx_kline (日K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(daily_klines) < 35:
            return DimensionScore('D43筹码擒龙', max_score, 0.0, {}, '日K线不足35根', False)

        closes = [k.close for k in daily_klines]
        highs = [k.high for k in daily_klines]
        lows = [k.low for k in daily_klines]
        volumes = [k.volume for k in daily_klines]
        amounts = [k.amount for k in daily_klines]

        # 主力成本线: (H+L+C)/3的34日EMA
        typical_prices = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(daily_klines))]
        zhuang_cost = ema(typical_prices, 34)

        cur_cost = zhuang_cost[-1]
        cur_price = closes[-1]
        price_above_cost = cur_price > cur_cost
        dist_to_cost = (cur_price - cur_cost) / cur_cost * 100 if cur_cost > 0 else 0

        raw['cost_line'] = round(cur_cost, 2)
        raw['dist_to_cost'] = round(dist_to_cost, 1)

        # 量能饱和度: 近5日成交额 / 近34日最大成交额
        recent_amounts = amounts[-5:]
        max_amount_34 = max(amounts[-34:]) if len(amounts) >= 34 else max(amounts)
        avg_recent_amount = sum(recent_amounts) / len(recent_amounts)
        saturation = avg_recent_amount / max_amount_34 if max_amount_34 > 0 else 0

        raw['saturation'] = round(saturation, 2)

        # 多头排列
        ma5 = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)
        bull_aligned = ma5[-1] > ma10[-1] > ma20[-1]
        price_above_ma = cur_price > ma5[-1]

        if price_above_cost and saturation >= 0.9 and bull_aligned and price_above_ma:
            score = 5.0
            detail_parts.append(
                f'筹码擒龙! 站上成本线{dist_to_cost:.1f}% 量能饱和{saturation:.1%} 多头排列 → 主力控盘'
            )
            raw['signal'] = 'dragon_launch'
        elif price_above_cost and bull_aligned and price_above_ma:
            score = 3.0
            detail_parts.append(
                f'筹码偏多: 站上成本线{dist_to_cost:.1f}% 多头排列 待量能放大'
            )
            raw['signal'] = 'chip_bull'
        elif price_above_cost:
            score = 1.0
            detail_parts.append(f'站上主力成本线: +{dist_to_cost:.1f}%')
            raw['signal'] = 'above_cost'
        elif dist_to_cost > -3.0:
            score = 0.5
            detail_parts.append(f'接近成本线: {dist_to_cost:.1f}% 可能支撑')
            raw['signal'] = 'near_cost'
        else:
            detail_parts.append(f'低于成本线: {dist_to_cost:.1f}%')
            raw['signal'] = 'below_cost'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D43筹码擒龙', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D44: 三倍量突破 (5分)
    # ───────────────────────────────────
    def score_triple_volume_breakout(self, daily_klines: List[KlineBar]) -> DimensionScore:
        """
        D44: 三倍量突破 (5分)
        
        来源: 三倍量选股指标
        逻辑: 当日成交量≥前日3倍 + 收阳 + 20MA向上 = 主力大资金进场
        
        评分:
        1. 三倍量 + 收阳 + 20MA向上 + 低位(回调3-25%) → 5分
        2. 三倍量 + 收阳 + 20MA向上 → 3分
        3. 倍量(≥2倍) + 收阳 → 1分
        
        数据: tdx_kline (日K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(daily_klines) < 22:
            return DimensionScore('D44三倍量', max_score, 0.0, {}, '日K线不足22根', False)

        today = daily_klines[-1]
        yesterday = daily_klines[-2]
        closes = [k.close for k in daily_klines]
        volumes = [k.volume for k in daily_klines]

        vol_ratio = today.volume / yesterday.volume if yesterday.volume > 0 else 1.0
        is_yang = today.close > today.open

        ma20 = sma(closes, 20)
        ma20_up = ma20[-1] > ma20[-5] if len(ma20) >= 5 else False

        # 检测位置: 从近期高点的回撤幅度
        recent_high = max(k.high for k in daily_klines[-20:])
        pullback_pct = (recent_high - today.close) / recent_high * 100 if recent_high > 0 else 0
        is_low_position = 3.0 <= pullback_pct <= 25.0

        raw['vol_ratio'] = round(vol_ratio, 1)
        raw['is_yang'] = is_yang
        raw['ma20_up'] = ma20_up
        raw['pullback_pct'] = round(pullback_pct, 1)

        # 三倍量
        is_triple = vol_ratio >= 3.0
        is_double = vol_ratio >= 2.0

        if is_triple and is_yang and ma20_up and is_low_position:
            score = 5.0
            detail_parts.append(
                f'三倍量突破! 量比{vol_ratio:.1f}倍 阳线+20MA向上+回调{pullback_pct:.1f}% → 主力进场!'
            )
            raw['signal'] = 'triple_breakout'
        elif is_triple and is_yang and ma20_up:
            score = 3.0
            detail_parts.append(
                f'三倍量: 量比{vol_ratio:.1f}倍 阳线+20MA向上 待确认位置'
            )
            raw['signal'] = 'triple_volume'
        elif is_double and is_yang:
            score = 1.0
            detail_parts.append(f'倍量: 量比{vol_ratio:.1f}倍 阳线 → 关注')
            raw['signal'] = 'double_volume'
        elif is_yang:
            score = 0.5
            detail_parts.append(f'量比{vol_ratio:.1f}倍 阳线 → 力度一般')
            raw['signal'] = 'normal_yang'
        else:
            detail_parts.append(f'非三倍量: 量比{vol_ratio:.1f}倍')
            raw['signal'] = 'no_signal'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D44三倍量', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D45: 试盘线 (5分)
    # ───────────────────────────────────
    def score_trial_line(self, daily_klines: List[KlineBar]) -> DimensionScore:
        """
        D45: 试盘线 (5分)
        
        来源: 试盘线选股
        逻辑: 主力拉升前测试抛压
          - 长上影线(上影≥实体2倍, 实体≤上影1/3)
          - 当日放量(≥前5日均量1.5倍)
          - 次日快速缩量(缩至30-50%) = 抛压轻!
          - 位置: 低位/横盘区(涨幅<30%)
        
        评分:
        1. 完整试盘形态 + 缩量确认 + 低位 → 5分
        2. 长上影+放量 → 3分
        3. 上影较长 → 1分
        
        数据: tdx_kline (日K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(daily_klines) < 7:
            return DimensionScore('D45试盘线', max_score, 0.0, {}, '日K线不足7根', False)

        today = daily_klines[-1]
        yesterday = daily_klines[-2]
        volumes = [k.volume for k in daily_klines]

        # 上影线分析
        body_top = max(today.open, today.close)
        body_bottom = min(today.open, today.close)
        body_len = body_top - body_bottom
        upper_shadow = today.high - body_top
        lower_shadow = body_bottom - today.low

        is_long_upper = upper_shadow >= body_len * 2 and body_len > 0
        is_small_body = body_len <= upper_shadow * 0.35 if upper_shadow > 0 else False

        raw['upper_shadow'] = round(upper_shadow, 2)
        raw['body_len'] = round(body_len, 2)
        raw['upper_ratio'] = round(upper_shadow / body_len, 1) if body_len > 0 else 999

        # 量能检测
        avg_vol_5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else today.volume
        vol_surge_on_trial = today.volume >= avg_vol_5 * 1.5

        # 次日缩量
        post_shrink = yesterday.volume < today.volume * 0.5 if len(volumes) >= 2 else False

        # 位置检测: 近60日涨幅<30%
        if len(daily_klines) >= 60:
            low_60d = min(k.low for k in daily_klines[-60:])
            rise_from_low = (today.close - low_60d) / low_60d * 100 if low_60d > 0 else 999
            is_low_area = rise_from_low < 30.0
        else:
            is_low_area = True

        raw['vol_surge'] = vol_surge_on_trial
        raw['post_shrink'] = post_shrink
        raw['is_low_area'] = is_low_area

        if is_long_upper and is_small_body and vol_surge_on_trial and is_low_area:
            if post_shrink:
                score = 5.0
                detail_parts.append(
                    f'试盘线确认! 长上影(上影/实体={raw["upper_ratio"]:.1f}) '
                    f'放量+次日缩量+低位 → 抛压轻 即将拉升!'
                )
                raw['signal'] = 'trial_confirmed'
            else:
                score = 3.5
                detail_parts.append(
                    f'试盘线: 长上影+放量+低位 → 等待缩量确认'
                )
                raw['signal'] = 'trial_pending'
        elif is_long_upper and vol_surge_on_trial:
            score = 2.0
            detail_parts.append(f'疑似试盘: 长上影+放量')
            raw['signal'] = 'possible_trial'
        elif is_long_upper:
            score = 1.0
            detail_parts.append(f'上影较长: 上影/实体={raw["upper_ratio"]:.1f}')
            raw['signal'] = 'long_upper'
        else:
            detail_parts.append('非试盘线形态')
            raw['signal'] = 'no_trial'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D45试盘线', max_score, score, raw, detail, passed)


    # ───────────────────────────────────
    # D46: 三均线战法 (5分)
    # ───────────────────────────────────
    def score_three_ma_warfare(self, daily_klines: List[KlineBar]) -> DimensionScore:
        """
        D46: 三均线战法 (5分)
        
        来源: 三根均线战法(25MA + 5VMA + 60VMA)
        逻辑:
          起爆点: 股价放量突破25MA + 5VMA上穿60VMA → 主升浪启动
          规避: 5VMA在60VMA下方=量能不足(全是坑)
          回踩: 股价回踩25MA + 量线双回踩 → 波段起涨
          起飞: 量缩极致 + 价稳25MA上方 → 主升浪前洗盘
        
        评分:
        1. 起爆点(放量突破25MA+量线金叉) → 5分
        2. 回踩企稳(价踩25MA+量线回踩60VMA) → 3分
        3. 价站25MA上+量线在60VMA上 → 1分
        
        数据: tdx_kline (日K)
        """
        max_score = 5.0
        score = 0.0
        detail_parts = []
        raw = {}

        if len(daily_klines) < 65:
            return DimensionScore('D46三均线', max_score, 0.0, {}, '日K线不足65根', False)

        closes = [k.close for k in daily_klines]
        volumes = [k.volume for k in daily_klines]

        # 价格25日均线
        ma25 = sma(closes, 25)
        cur_ma25 = ma25[-1]

        # 量能线: 5日均量和60日均量
        vma5 = sma(volumes, 5)
        vma60 = sma(volumes, 60)

        cur_vma5 = vma5[-1]
        cur_vma60 = vma60[-1]
        prev_vma5 = vma5[-2]
        prev_vma60 = vma60[-2]

        cur_price = closes[-1]
        cur_vol = volumes[-1]

        raw['ma25'] = round(cur_ma25, 2)
        raw['vma5'] = round(cur_vma5, 1)
        raw['vma60'] = round(cur_vma60, 1)
        raw['price_above_25'] = cur_price > cur_ma25

        # ── 场景1: 起爆点 — 价放量突破25MA + 5VMA金叉60VMA ──
        price_broke_25 = cur_price > cur_ma25 and daily_klines[-2].close <= ma25[-2]
        vol_golden_cross = cur_vma5 > cur_vma60 and prev_vma5 <= prev_vma60
        vol_surge = cur_vol > volumes[-2] * 1.5

        if price_broke_25 and vol_golden_cross:
            score = 5.0
            detail_parts.append(
                f'三均线起爆点! 价放量突破25MA + 5VMA({cur_vma5:.0f})金叉60VMA({cur_vma60:.0f}) → 主升浪!'
            )
            raw['signal'] = 'ignition'
        elif price_broke_25:
            score = 4.0
            detail_parts.append(f'三均线突破25MA: 待量线金叉确认')
            raw['signal'] = 'price_break'

        # ── 场景2: 回踩企稳 — 价回调至25MA附近 + 量线回踩60VMA ──
        elif abs(cur_price - cur_ma25) / cur_ma25 * 100 < 3.0 and cur_vma5 < cur_vma60 * 1.1 and cur_vma5 > cur_vma60 * 0.9:
            score = 3.0
            detail_parts.append(
                f'三均线回踩企稳: 价距25MA<3% 量线粘合 → 波段起涨点'
            )
            raw['signal'] = 'pullback_stable'

        # ── 场景3: 量在水下=全是坑 ──
        elif cur_vma5 < cur_vma60 * 0.8:
            score = 0.0
            detail_parts.append(
                f'三均线规避: 5VMA({cur_vma5:.0f})远低于60VMA({cur_vma60:.0f}) → 量能不足'
            )
            raw['signal'] = 'under_water'

        # ── 场景4: 缩量极致假跌 → 起飞前兆 ──
        elif cur_price > cur_ma25 and cur_vol < vma5[-2] * 0.5 and cur_vma5 > cur_vma60:
            score = 4.0
            detail_parts.append(
                f'三均线起飞前兆! 缩量极致(量{vma5[-2]*0.5:.0f}) '
                f'价稳25MA上方 → 主升前洗盘!'
            )
            raw['signal'] = 'pre_takeoff'

        # ── 场景5: 价在25MA上 + 量线多头 ──
        elif cur_price > cur_ma25 and cur_vma5 > cur_vma60:
            score = 1.0
            detail_parts.append(f'三均线偏多: 价站25MA+量线多头')
            raw['signal'] = 'bull_setup'

        else:
            detail_parts.append(
                f'三均线中性: 价{"上" if cur_price > cur_ma25 else "下"}25MA '
                f'量线{"多头" if cur_vma5 > cur_vma60 else "空头"}'
            )
            raw['signal'] = 'neutral'

        score = min(max_score, score)
        passed = score >= 3.0
        detail = '; '.join(detail_parts)
        return DimensionScore('D46三均线', max_score, score, raw, detail, passed)


# ═══════════════════════════════════════════════════════════
# 批量评分入口
# ═══════════════════════════════════════════════════════════

def score_all_new_dimensions(daily_klines: List[KlineBar],
                             weekly_klines: List[KlineBar] = None) -> List[DimensionScore]:
    """
    批量计算D37-D46所有新维度评分
    返回10个DimensionScore的列表
    
    用法:
        from V13_5_M71_D37_D46 import score_all_new_dimensions, daily_to_weekly
        weekly = daily_to_weekly(daily_klines)
        new_scores = score_all_new_dimensions(daily_klines, weekly)
    """
    if weekly_klines is None:
        weekly_klines = daily_to_weekly(daily_klines)

    scorer = NewDimensionScorer()
    
    scores = [
        scorer.score_weekly_ma_alignment(weekly_klines),       # D37
        scorer.score_weekly_platform_breakout(weekly_klines),   # D38
        scorer.score_weekly_macd_golden_cross(weekly_klines),   # D39
        scorer.score_weekly_pullback_support(weekly_klines),    # D40
        scorer.score_weekly_macd_divergence(weekly_klines),     # D41
        scorer.score_duck_head_pattern(daily_klines),           # D42
        scorer.score_chip_dragon(daily_klines),                 # D43
        scorer.score_triple_volume_breakout(daily_klines),      # D44
        scorer.score_trial_line(daily_klines),                  # D45
        scorer.score_three_ma_warfare(daily_klines),            # D46
    ]
    
    return scores


# ────────────────────────────────────────────────
# CLI验证入口
# ────────────────────────────────────────────────
if __name__ == '__main__':
    """
    验证D37-D46在蜀道装备(300540)周线数据上的表现
    """
    print("=" * 60)
    print("V13.5.20 D37-D46 新维度验证")
    print("=" * 60)
    
    # 模拟蜀道装备6/25附近数据 (实际应调TDX)
    # 这里用模拟kline验证维度逻辑
    
    import random
    random.seed(42)
    
    # 模拟90根日K (上涨趋势)
    mock_daily = []
    base_price = 25.0
    for i in range(90):
        chg = random.gauss(0, 0.02)
        close = base_price * (1 + max(min(chg, 0.05), -0.05))
        open_p = close * (1 + random.gauss(0, 0.005))
        high = max(open_p, close) * (1 + abs(random.gauss(0, 0.01)))
        low = min(open_p, close) * (1 - abs(random.gauss(0, 0.01)))
        vol = random.randint(50000, 200000)
        bar = KlineBar(
            date=f"2026{(i//30)+4:02d}{(i%30)+1:02d}",
            open=round(open_p, 2), high=round(high, 2),
            low=round(low, 2), close=round(close, 2),
            volume=vol, amount=vol * close,
            chg_pct=(close - base_price) / base_price * 100,
            volume_ratio=vol / 100000
        )
        mock_daily.append(bar)
        base_price = close

    weekly = daily_to_weekly(mock_daily)
    print(f"日K线数: {len(mock_daily)} → 周K线数: {len(weekly)}")
    
    new_scores = score_all_new_dimensions(mock_daily, weekly)
    
    print(f"\n{'编号':^6} {'维度名':^14} {'满分':^6} {'得分':^8} {'通过':^6} {'详情'}")
    print("-" * 100)
    for i, ds in enumerate(new_scores):
        tag = "✓" if ds.passed else "✗"
        print(f" D{i+37:<3d}  {ds.name:<14s} {ds.max_score:>4.0f}分  {ds.actual_score:>4.1f}分   {tag:^4}    {ds.detail[:60]}")
    
    total_new = sum(ds.actual_score for ds in new_scores)
    print(f"\nD37-D46总计: {total_new:.1f} / 48 分")
    print("V13.5.20新维度验证完成!")
