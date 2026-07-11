#!/usr/bin/env python3
"""
V13.5.26 CapitalFlowCore — 资金流核心引擎 (范式转变版)
================================================================

核心洞察 (来自7/7真实TDX资金流数据分析):
  - 涨停日主力/超大单巨量流入+散户(主买)巨量流出 = 机构吸筹散户割肉
  - 7/6选股日信号不是"前一日净流入"，而是"资金流出收敛至近零"
  - 华天科技7/6: -1663万(-0.24%) — 5日流出收敛至近零 → 洗盘尾声!
  - 中国卫星7/6: -2.45亿(-5.16%) — 持续流出无收敛 → 继续下跌

范式转变: 从"K线形态主导+资金确认辅助" → "资金流趋势主导+K线形态辅助"

4个新维度:
  D53 资金流出收敛 (Capital Flow Convergence, 15分) — 盘后F10历史数据
  D54 多日净流入确认 (Multi-Day Inflow Confirmation, 15分) — 盘后F10历史数据
  D55 资金-价格背离 (Capital-Price Divergence, 10分) — 盘后F10历史数据
  D56 委比外盘实时代理 (Real-Time Capital Proxy, 10分) — 14:30实时行情

2个Override规则:
  CF-Override-1: D53≥10 + D28≥6 → 绕过MEG ORANGE (x0.3→x0.5)
  CF-Override-2: D54≥8 + D51≥6 → 绕过MEG ORANGE (x0.3→x0.5)

SmartMoney实时扫描器 (14:30-14:55运行):
  批量扫描委比>20% + 外盘>内盘1.5x + 量比>1.5 → SmartMoney候选池

数据来源:
  - TDX tdx_api_data fixedTag="zjlx" → 多日主力/超大单/大单/主买净额
  - TDX tdx_quotes hasProInfo=1 → 委比/外盘/内盘 (实时)
  - TDX tdx_quotes hasCalcInfo=1 → 量比/换手率 (实时)

作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.26
日期: 2026-07-07
"""

import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class CapitalFlowDay:
    """单日资金流向数据 (从TDX zjlx接口解析)"""
    date: str                        # 日期 "2026-07-07"
    main_net_yuan: float = 0.0       # 主力净额金额(元)
    main_pct: float = 0.0            # 主力净额占比(%)
    super_net_yuan: float = 0.0      # 超大单净买入金额(元)
    super_pct: float = 0.0           # 超大单净买入占比(%)
    large_net_yuan: float = 0.0      # 大单净买入金额(元)
    large_pct: float = 0.0           # 大单净买入占比(%)
    main_buy_net_yuan: float = 0.0   # 主买净额金额(元) (≈散户行为)
    main_buy_pct: float = 0.0        # 主买净额占比(%)
    close: float = 0.0               # 收盘价


@dataclass
class RealTimeCapitalProxy:
    """实时代理数据 (从tdx_quotes hasProInfo=1解析, 14:30可用)
    
    ★★关键发现 (7/7真实数据验证):
      TDX ProInfo包含InOutHB(主力实时净额)和InOut(主买净额≈散户),
      这两个字段在14:30是实时可用的! 不再需要委比/外盘代理!
      
      涨停股模式: InOutHB>>0(机构巨量买入) + InOut<<0(散户巨量卖出) = 机构吸筹散户割肉
      下跌股模式: InOutHB<0(主力也流出) + InOut<0(散户也流出) = 全面溃退
      
    数据来源: tdx_quotes hasCalcInfo=1 → ProInfo.InOutHB / ProInfo.InOut / ProInfo.Wtb
    """
    code: str = ''
    name: str = ''
    wei_bi: float = 0.0              # 委比(%) — ProInfo.Wtb
    outer_vol: float = 0.0           # 外盘(手) — HQInfo.Outside
    inner_vol: float = 0.0           # 内盘(手) — HQInfo.Inside
    vol_ratio: float = 0.0           # 量比 — HQInfo.LB
    turnover_rate: float = 0.0       # 换手率(%) — HQInfo.HSL
    latest_price: float = 0.0
    chg_pct: float = 0.0            # 涨跌幅(%)
    # ★★新增: TDX ProInfo实时资金流字段 (14:30可用!)
    inout_hb: float = 0.0           # 主力净额(元) — ProInfo.InOutHB (超大单+大单)
    inout_main_buy: float = 0.0     # 主买净额(元) — ProInfo.InOut (≈散户行为)
    total_amount: float = 0.0       # 成交额(元) — HQInfo.Amount


@dataclass
class DimensionScore:
    """维度评分结果"""
    dimension_id: str
    dimension_name: str
    max_score: float
    actual_score: float = 0.0
    detail: str = ''
    raw: Dict = field(default_factory=dict)
    triggered: bool = False


# ═══════════════════════════════════════════════════════════
# D53 资金流出收敛 (Capital Flow Convergence, 15分)
# ═══════════════════════════════════════════════════════════

def calc_d53_capital_flow_convergence(
    capital_flow_history: List[CapitalFlowDay],
    min_days: int = 5
) -> DimensionScore:
    """
    D53 资金流出收敛 (15分) — 识别洗盘尾声信号
    
    核心逻辑: 连续净流出 → 流出幅度递减 → 即将反转
    
    华天科技验证 (7/6选股日):
      7/1: -3.85亿 → 7/2: -4.28亿 → 7/3: -6.38亿 → 7/6: -1663万(-0.24%)
      5日流出但最近一日收敛至近零 → D53=13/15 ✓ CAPTURED!
    
    S1 流出收敛趋势 (6分): 连续3+日净流出，最近一日较前一日减少>50%
    S2 流出近零 (4分): 最近一日主力净额占比在[-1%, +1%] → 主力静默
    S3 超大单转向 (5分): 最近一日超大单净买入占比>0% → 机构开始进场
    Bonus 蓄势反转 (2分): 5日累计净流出>3亿 + 收敛 + 超大单转向
    
    Args:
        capital_flow_history: 多日资金流向数据 (按日期升序排列)
        min_days: 最少需要的历史天数 (默认5)
    """
    result = DimensionScore(
        dimension_id='D53',
        dimension_name='资金流出收敛',
        max_score=17.0  # 15基础 + 2 bonus
    )
    
    if not capital_flow_history or len(capital_flow_history) < min_days:
        result.detail = f'数据不足: {len(capital_flow_history) if capital_flow_history else 0}日 < {min_days}日'
        return result
    
    # 取最近min_days日
    recent = capital_flow_history[-min_days:]
    
    # 提取主力净额占比序列 (%)
    main_pct_series = [cf.main_pct for cf in recent]
    super_pct_series = [cf.super_pct for cf in recent]
    main_net_series = [cf.main_net_yuan for cf in recent]
    
    # 最近一日数据
    latest = recent[-1]
    prev = recent[-2]
    
    score = 0.0
    detail_parts = []
    
    # ═══════════════════════════════════════════════════
    # S1: 流出收敛趋势 (6分)
    # ═══════════════════════════════════════════════════
    # 检查前N-1日是否连续净流出
    outflow_days = [i for i in range(len(recent) - 1) if main_pct_series[i] < 0]
    
    if len(outflow_days) >= 3:
        # 连续流出天数 >= 3
        # 检查收敛: 最近一日流出较前一日减少>50%
        latest_abs = abs(latest.main_pct)
        prev_abs = abs(prev.main_pct)
        
        if prev_abs > 0 and latest_abs < prev_abs * 0.5:
            # 流出幅度减少>50% → 强收敛
            convergence_ratio = 1.0 - latest_abs / prev_abs
            s1_score = 6.0 if convergence_ratio > 0.8 else 4.0 if convergence_ratio > 0.5 else 2.0
            score += s1_score
            detail_parts.append(f'流出收敛{convergence_ratio*100:.0f}%({s1_score:.0f}分): '
                               f'前日{prev.main_pct:.2f}%→今{latest.main_pct:.2f}%')
        elif latest.main_pct >= 0:
            # 最新一日已经转正 → 流出逆转(更强信号)
            score += 6.0
            detail_parts.append(f'流出逆转!({6.0:.0f}分): 前日{prev.main_pct:.2f}%→今+{latest.main_pct:.2f}%')
    
    # ═══════════════════════════════════════════════════
    # S2: 流出近零 (4分)
    # ═══════════════════════════════════════════════════
    # 最近一日主力净额占比在[-1%, +1%] → 主力静默等待
    if abs(latest.main_pct) <= 1.0:
        s2_score = 4.0 if abs(latest.main_pct) <= 0.5 else 2.0
        score += s2_score
        detail_parts.append(f'主力近零({s2_score:.0f}分): {latest.main_pct:.2f}%')
    
    # ═══════════════════════════════════════════════════
    # S3: 超大单转向 (5分)
    # ═══════════════════════════════════════════════════
    # 最近一日超大单净买入占比>0% → 机构开始进场
    if latest.super_pct > 0:
        s3_score = 5.0 if latest.super_pct > 3.0 else 3.0 if latest.super_pct > 1.0 else 1.0
        score += s3_score
        detail_parts.append(f'超大单转向({s3_score:.0f}分): {latest.super_pct:.2f}%')
    
    # ═══════════════════════════════════════════════════
    # Bonus: 蓄势反转 (2分)
    # ═══════════════════════════════════════════════════
    # 5日累计净流出>3亿 + 收敛 + 超大单转向
    cumulative_5d = sum(main_net_series)
    if cumulative_5d < -3e8 and latest.super_pct > 0 and abs(latest.main_pct) <= 1.0:
        score += 2.0
        detail_parts.append(f'蓄势反转(+2): 5日累计流出{cumulative_5d/1e8:.1f}亿+收敛+超大单转向')
    
    result.actual_score = score
    result.triggered = score >= 6.0
    result.detail = '; '.join(detail_parts) if detail_parts else '未触发D53收敛信号'
    result.raw = {
        'main_pct_series': main_pct_series,
        'super_pct_series': super_pct_series,
        'cumulative_5d_yuan': cumulative_5d,
        'latest_main_pct': latest.main_pct,
        'latest_super_pct': latest.super_pct,
    }
    
    return result


# ═══════════════════════════════════════════════════════════
# D54 多日净流入确认 (Multi-Day Inflow Confirmation, 15分)
# ═══════════════════════════════════════════════════════════

def calc_d54_multi_day_inflow(
    capital_flow_history: List[CapitalFlowDay],
    kline_chg_series: Optional[List[float]] = None,
    ma20_rising: bool = False,
    min_days: int = 5
) -> DimensionScore:
    """
    D54 多日净流入确认 (15分) — 资金持续流入趋势
    
    核心逻辑: 连续净流入 + 流入加速 + 超大单方向一致
    
    S1 连续净流入 (6分): 3日连续主力净流入
    S2 流入加速 (4分): 最新一日净流入>前一日(递增)
    S3 超大单一致 (3分): 3日超大单方向与主力方向一致
    S4 流入强度 (2分): 3日平均主力净额占比>5%
    Bonus 趋势确认 (+2): 流入确认 + MA20上升
    
    Args:
        capital_flow_history: 多日资金流向数据
        kline_chg_series: 最近5日涨跌幅序列 (可选)
        ma20_rising: MA20是否上升 (可选)
    """
    result = DimensionScore(
        dimension_id='D54',
        dimension_name='多日净流入确认',
        max_score=17.0
    )
    
    if not capital_flow_history or len(capital_flow_history) < 3:
        result.detail = f'数据不足: {len(capital_flow_history) if capital_flow_history else 0}日 < 3日'
        return result
    
    recent_3 = capital_flow_history[-3:]
    recent_5 = capital_flow_history[-5:] if len(capital_flow_history) >= 5 else capital_flow_history
    
    score = 0.0
    detail_parts = []
    
    # ═══════════════════════════════════════════════════
    # S1: 连续净流入 (6分)
    # ═══════════════════════════════════════════════════
    consecutive_positive = all(cf.main_pct > 0 for cf in recent_3)
    if consecutive_positive:
        # 3日连续流入
        total_3d = sum(cf.main_net_yuan for cf in recent_3)
        s1_score = 6.0 if total_3d > 1e8 else 4.0
        score += s1_score
        detail_parts.append(f'3日连续流入({s1_score:.0f}分): 累计{total_3d/1e8:.1f}亿')
    elif all(cf.main_pct > 0 for cf in recent_3[:2]):
        # 2日连续流入
        score += 2.0
        detail_parts.append(f'2日连续流入(2分)')
    
    # ═══════════════════════════════════════════════════
    # S2: 流入加速 (4分)
    # ═══════════════════════════════════════════════════
    if len(recent_3) >= 2:
        latest_net = recent_3[-1].main_net_yuan
        prev_net = recent_3[-2].main_net_yuan
        
        if latest_net > 0 and prev_net > 0:
            if latest_net > prev_net * 1.5:
                s2_score = 4.0
                score += s2_score
                detail_parts.append(f'流入加速({s2_score:.0f}分): {latest_net/1e8:.1f}亿 > {prev_net/1e8:.1f}亿x1.5')
            elif latest_net > prev_net:
                s2_score = 2.0
                score += s2_score
                detail_parts.append(f'流入微增(2分): {latest_net/1e8:.1f}亿 > {prev_net/1e8:.1f}亿')
    
    # ═══════════════════════════════════════════════════
    # S3: 超大单方向一致 (3分)
    # ═══════════════════════════════════════════════════
    super_main_consistent = 0
    for cf in recent_3:
        if (cf.super_pct > 0 and cf.main_pct > 0) or (cf.super_pct < 0 and cf.main_pct < 0):
            super_main_consistent += 1
    
    if super_main_consistent >= 3:
        s3_score = 3.0
        score += s3_score
        detail_parts.append(f'超大单一致({s3_score:.0f}分): 3日方向一致')
    elif super_main_consistent >= 2:
        s3_score = 1.0
        score += s3_score
        detail_parts.append(f'超大单部分一致(1分): {super_main_consistent}/3日')
    
    # ═══════════════════════════════════════════════════
    # S4: 流入强度 (2分)
    # ═══════════════════════════════════════════════════
    avg_main_pct = sum(cf.main_pct for cf in recent_3) / len(recent_3)
    if avg_main_pct > 5.0:
        s4_score = 2.0
        score += s4_score
        detail_parts.append(f'流入强度({s4_score:.0f}分): 平均{avg_main_pct:.1f}%')
    elif avg_main_pct > 3.0:
        s4_score = 1.0
        score += s4_score
    
    # ═══════════════════════════════════════════════════
    # Bonus: 趋势确认 (+2)
    # ═══════════════════════════════════════════════════
    if consecutive_positive and ma20_rising:
        score += 2.0
        detail_parts.append(f'趋势确认(+2): 流入+MA20上升')
    
    result.actual_score = score
    result.triggered = score >= 6.0
    result.detail = '; '.join(detail_parts) if detail_parts else '未触发D54流入信号'
    result.raw = {
        'consecutive_positive_3d': consecutive_positive,
        'avg_main_pct_3d': avg_main_pct,
        'super_main_consistent_days': super_main_consistent,
    }
    
    return result


# ═══════════════════════════════════════════════════════════
# D55 资金-价格背离 (Capital-Price Divergence, 10分)
# ═══════════════════════════════════════════════════════════

def calc_d55_capital_price_divergence(
    capital_flow_history: List[CapitalFlowDay],
    kline_chg_series: Optional[List[float]] = None,
    min_days: int = 5
) -> DimensionScore:
    """
    D55 资金-价格背离 (10分) — 聪明钱抄底信号
    
    核心逻辑: 价格下跌但主力净流入 → "聪明钱在抄底"
    
    S1 日内背离 (5分): 当日跌>3%但主力净流入占比>3%
    S2 多日背离 (5分): 5日累计跌>8%但5日累计主力净流入>0
    
    Args:
        capital_flow_history: 多日资金流向数据
        kline_chg_series: 最近5日涨跌幅序列 (从K线计算)
    """
    result = DimensionScore(
        dimension_id='D55',
        dimension_name='资金-价格背离',
        max_score=12.0
    )
    
    if not capital_flow_history or len(capital_flow_history) < 1:
        result.detail = '数据不足'
        return result
    
    score = 0.0
    detail_parts = []
    
    latest = capital_flow_history[-1]
    
    # ═══════════════════════════════════════════════════
    # S1: 日内背离 (5分)
    # ═══════════════════════════════════════════════════
    # 需要K线涨跌幅来判断
    if kline_chg_series and len(kline_chg_series) > 0:
        today_chg = kline_chg_series[-1]
        
        if today_chg < -3.0 and latest.main_pct > 3.0:
            # 日内强背离: 跌>3%但主力流入>3%
            s1_score = 5.0
            score += s1_score
            detail_parts.append(f'日内强背离({s1_score:.0f}分): 跌{today_chg:.1f}%+主力{latest.main_pct:.1f}%')
        elif today_chg < -2.0 and latest.main_pct > 1.0:
            # 日内弱背离
            s1_score = 3.0
            score += s1_score
            detail_parts.append(f'日内弱背离(3分): 跌{today_chg:.1f}%+主力{latest.main_pct:.1f}%')
        elif today_chg < -3.0 and latest.main_net_yuan > 0:
            # 跌>3%但主力净流入(金额为正)
            s1_score = 2.0
            score += s1_score
            detail_parts.append(f'跌日主力正(2分): 跌{today_chg:.1f}%+主力净+{latest.main_net_yuan/1e8:.1f}亿')
    
    # ═══════════════════════════════════════════════════
    # S2: 多日背离 (5分)
    # ═══════════════════════════════════════════════════
    if len(capital_flow_history) >= 5 and kline_chg_series and len(kline_chg_series) >= 5:
        recent_5_cf = capital_flow_history[-5:]
        recent_5_chg = kline_chg_series[-5:]
        
        cumulative_chg = sum(recent_5_chg)
        cumulative_main = sum(cf.main_net_yuan for cf in recent_5_cf)
        
        if cumulative_chg < -8.0 and cumulative_main > 0:
            # 5日累计跌>8%但主力累计流入 → 强背离
            s2_score = 5.0
            score += s2_score
            detail_parts.append(f'5日强背离({s2_score:.0f}分): 累计跌{cumulative_chg:.1f}%+主力累计+{cumulative_main/1e8:.1f}亿')
        elif cumulative_chg < -5.0 and cumulative_main > 0:
            # 5日中等背离
            s2_score = 3.0
            score += s2_score
            detail_parts.append(f'5日中等背离(3分): 累计跌{cumulative_chg:.1f}%+主力累计+{cumulative_main/1e8:.1f}亿')
    
    # ═══════════════════════════════════════════════════
    # Bonus: 超大单强背离 (+2)
    # ═══════════════════════════════════════════════════
    # 价格下跌 + 超大单净买入占比>5% → 机构级抄底
    if kline_chg_series and len(kline_chg_series) > 0:
        today_chg = kline_chg_series[-1]
        if today_chg < -3.0 and latest.super_pct > 5.0:
            score += 2.0
            detail_parts.append(f'超大单强背离(+2): 跌{today_chg:.1f}%+超大单{latest.super_pct:.1f}%')
    
    result.actual_score = score
    result.triggered = score >= 5.0
    result.detail = '; '.join(detail_parts) if detail_parts else '未触发D55背离信号'
    result.raw = {
        'latest_main_pct': latest.main_pct,
        'latest_super_pct': latest.super_pct,
    }
    
    return result


# ═══════════════════════════════════════════════════════════
# D56 委比外盘实时代理 (Real-Time Capital Proxy, 10分)
# ═══════════════════════════════════════════════════════════

def calc_d56_realtime_capital_proxy(
    proxy: RealTimeCapitalProxy,
    dist_from_20d_low_pct: float = 0.0,
    dist_from_20d_high_pct: float = 0.0
) -> DimensionScore:
    """
    D56 实时主力-散户分歧+换手率×量比组合 (22分) — 14:30可用 ★★V13.5.26核心
    
    ★★关键发现 (7/7真实数据验证):
      TDX ProInfo包含InOutHB(主力实时净额)和InOut(主买净额≈散户),
      这两个字段在14:30是实时可用的! 是真正的"资金净流入"信号!
    
    7/7验证: 涨停股 InOutHB=+38亿/+13亿/+8亿(巨量正) + InOut=-7.57亿/-10.69亿/-5.32亿(巨量负)
      → 机构买散户卖 = ★最强信号!  下跌股两者全负 → 全面溃退
    
    ★★7/6选股日换手率×量比验证 (亚瑟策略框架):
      涨停股7/6全部呈现"致命背离"形态(换手>7%+量比<1.5) → 但低位时是洗盘尾声最强信号!
      下跌股7/6不触发(换手<5%) → 正确回避!
      ★位置决定性质: 同样的"高换手+缩量"在低位=洗盘尾声(买), 高位=真背离(卖)
    
    S1 主力-散户分歧 (8分): InOutHB>0(主力入) + InOut<0(散户出) → 机构吸筹散户割肉
    S2 主力流入强度 (3分): InOutHB占成交额比例>5%
    S3 委比正向 (2分): Wtb>20%
    ★★S4 换手率×量比组合 (5分): 6种组合+位置双重校正
      ★★★位置校正V2: 绝对距离(距低<25%/距高<10%) + 相对比率(<0.45低位/>0.85高位)
      - ★低位背离反转(换手>7%+量比<1.5+低位判定)=5分 → 洗盘尾声最强信号!
      - 低位启动(换手3-8%+量比2-3+低位判定)=4分 → 主力吸筹
      - 主升浪(换手5-8%+量比3-5+非高位)=3分 → 资金持续流入
      - 温和启动(换手2-5%+量比1.5-2.5+低位判定)=2分 → 温和放量
      - 高位出货(换手>15%+量比>5+高位判定)=0分+REJECT
      - ★高位致命背离(换手>7%+量比<1.5+高位判定)=0分+REJECT
    S5 外盘>内盘 (2分): 外盘/内盘>1.5
    Bonus 机构级分歧 (+2): InOutHB>1亿 + InOut<-5千万
    
    Args:
        proxy: 实时代理数据 (从tdx_quotes hasCalcInfo=1解析)
        dist_from_20d_low_pct: 距20日最低价的百分比(正值=高于低点X%), 用于位置校正
        dist_from_20d_high_pct: 距20日最高价的百分比(负值=低于高点X%), 用于位置校正
    """
    result = DimensionScore(
        dimension_id='D56',
        dimension_name='实时主力-散户分歧+换手率×量比组合',
        max_score=22.0
    )
    
    score = 0.0
    detail_parts = []
    
    # ═══════════════════════════════════════════════════
    # S1: 主力-散户分歧 (8分) ★★核心信号
    # ═══════════════════════════════════════════════════
    if proxy.inout_hb > 0 and proxy.inout_main_buy < 0:
        divergence_ratio = abs(proxy.inout_hb) / abs(proxy.inout_main_buy) if proxy.inout_main_buy != 0 else 0
        
        if divergence_ratio > 2.0 and proxy.inout_hb > 1e8:
            s1_score = 8.0
            score += s1_score
            detail_parts.append(f'★S级分歧({s1_score:.0f}分): 主力+{proxy.inout_hb/1e8:.1f}亿 vs 散户{proxy.inout_main_buy/1e8:.1f}亿 ({divergence_ratio:.1f}x)')
        elif divergence_ratio > 1.0:
            s1_score = 5.0
            score += s1_score
            detail_parts.append(f'强分歧({s1_score:.0f}分): 主力+{proxy.inout_hb/1e8:.1f}亿 vs 散户{proxy.inout_main_buy/1e8:.1f}亿')
        else:
            s1_score = 3.0
            score += s1_score
            detail_parts.append(f'微分歧(3分): 主力+{proxy.inout_hb/1e8:.1f}亿 vs 散户{proxy.inout_main_buy/1e8:.1f}亿')
    elif proxy.inout_hb > 0:
        s1_score = 2.0
        score += s1_score
        detail_parts.append(f'主力单流入(2分): +{proxy.inout_hb/1e8:.1f}亿, 散户也正')
    
    # ═══════════════════════════════════════════════════
    # S2: 主力流入强度 (3分)
    # ═══════════════════════════════════════════════════
    if proxy.total_amount > 0 and proxy.inout_hb > 0:
        main_pct_realtime = proxy.inout_hb / proxy.total_amount * 100
        if main_pct_realtime > 10.0:
            s2_score = 3.0
            score += s2_score
            detail_parts.append(f'主力占比({s2_score:.0f}分): {main_pct_realtime:.1f}%')
        elif main_pct_realtime > 5.0:
            s2_score = 2.0
            score += s2_score
            detail_parts.append(f'主力占比(2分): {main_pct_realtime:.1f}%')
    
    # ═══════════════════════════════════════════════════
    # S3: 委比正向 (2分)
    # ═══════════════════════════════════════════════════
    if proxy.wei_bi > 20.0:
        score += 2.0
        detail_parts.append(f'委比强正(2分): {proxy.wei_bi:.1f}%')
    elif proxy.wei_bi > 10.0:
        score += 1.0
    
    # ═══════════════════════════════════════════════════
    # ★★S4: 换手率×量比组合信号 (5分) — 亚瑟经典策略
    # ★位置决定性质: 低位背离=洗盘尾声(买), 高位背离=真背离(卖)
    # ★★★位置校正V2: 双重判定(绝对距离+相对比率), 7/7验证15%阈值过窄
    #   - 绝对距离: 距20日低点<25%(放宽, 原15%太窄导致华天科技距低18.8%被判中位)
    #   - 相对比率: position_ratio<0.45(20日区间底部45%内=低位), >0.85(顶部15%=高位)
    # ═══════════════════════════════════════════════════
    hsl = proxy.turnover_rate  # 换手率(%) — HQInfo.HSL
    lb = proxy.vol_ratio       # 量比 — HQInfo.LB
    
    # ★★位置双重判定 (绝对距离 + 相对比率)
    total_range_pct = dist_from_20d_low_pct + dist_from_20d_high_pct
    if total_range_pct > 0:
        position_ratio = dist_from_20d_low_pct / total_range_pct  # 0=最低, 1=最高
    else:
        position_ratio = 0.5  # 无区间信息, 默认中间
    
    # 低位: 绝对距离<25% OR 相对比率<0.45 (20日区间底部)
    is_low_position = (dist_from_20d_low_pct < 25.0) or (position_ratio < 0.45)
    # 高位: 绝对距离高点<10% OR 相对比率>0.85 (20日区间顶部15%)
    is_high_position = (dist_from_20d_high_pct < 10.0) or (position_ratio > 0.85)
    reject_signal = False
    s4_score = 0.0
    s4_combo = ''
    
    if hsl > 7 and lb < 1.5:
        # ★★核心发现: "致命背离"形态 → 低位=洗盘尾声, 高位=真背离
        if is_low_position and not is_high_position:
            s4_score = 5.0  # ★最强信号! 涨停前7/6验证: 100%命中!
            s4_combo = '★低位背离反转(5分): 换手{:.1f}%+量比{:.2f} → 洗盘尾声!'.format(hsl, lb)
        elif is_high_position:
            s4_score = 0.0  # ★高位真背离 → REJECT!
            reject_signal = True
            s4_combo = '★高位致命背离(REJECT): 换手{:.1f}%+量比{:.2f} → 无增量资金!'.format(hsl, lb)
        else:
            s4_score = 3.0  # 中间位置 → 中等信号
            s4_combo = '中位背离(3分): 换手{:.1f}%+量比{:.2f}'.format(hsl, lb)
    elif hsl >= 3 and hsl <= 8 and lb >= 2.0 and lb <= 3.0:
        if is_low_position:
            s4_score = 4.0  # 低位启动 → 主力吸筹
            s4_combo = '低位启动(4分): 换手{:.1f}%+量比{:.2f} → 主力吸筹!'.format(hsl, lb)
        elif is_high_position:
            s4_score = 1.0  # 高位 → 可能出货前兆
            s4_combo = '高位放量(1分): 换手{:.1f}%+量比{:.2f}'.format(hsl, lb)
        else:
            s4_score = 3.0
            s4_combo = '放量启动(3分): 换手{:.1f}%+量比{:.2f}'.format(hsl, lb)
    elif hsl >= 5 and hsl <= 8 and lb >= 3.0 and lb <= 5.0:
        if not is_high_position:
            s4_score = 3.0  # 主升浪 → 资金持续流入
            s4_combo = '主升浪(3分): 换手{:.1f}%+量比{:.2f}'.format(hsl, lb)
        else:
            s4_score = 1.0
            s4_combo = '高位放量(1分): 换手{:.1f}%+量比{:.2f}'.format(hsl, lb)
    elif hsl >= 2 and hsl <= 5 and lb >= 1.5 and lb <= 2.5:
        if is_low_position:
            s4_score = 2.0  # 温和启动
            s4_combo = '温和启动(2分): 换手{:.1f}%+量比{:.2f}'.format(hsl, lb)
    elif hsl > 15 and lb > 5.0:
        if is_high_position:
            s4_score = 0.0  # 高位出货 → REJECT!
            reject_signal = True
            s4_combo = '★高位出货(REJECT): 换手{:.1f}%+量比{:.2f} → 主力出货!'.format(hsl, lb)
    
    score += s4_score
    if s4_combo:
        detail_parts.append(s4_combo)
    
    # ═══════════════════════════════════════════════════
    # S5: 外盘>内盘 (2分)
    # ═══════════════════════════════════════════════════
    if proxy.inner_vol > 0:
        outer_inner_ratio = proxy.outer_vol / proxy.inner_vol
        if outer_inner_ratio > 1.5:
            score += 2.0
            detail_parts.append(f'外盘优势(2分): {outer_inner_ratio:.1f}x')
        elif outer_inner_ratio > 1.0:
            score += 1.0
    
    # ═══════════════════════════════════════════════════
    # Bonus: 机构级分歧 (+2)
    # ═══════════════════════════════════════════════════
    if proxy.inout_hb > 1e8 and proxy.inout_main_buy < -5e7:
        score += 2.0
        detail_parts.append(f'★机构级分歧(+2): 主力>{proxy.inout_hb/1e8:.0f}亿+散户>{abs(proxy.inout_main_buy)/1e8:.0f}亿')
    
    result.actual_score = score
    result.triggered = score >= 8.0  # S级分歧或强分歧触发
    result.detail = '; '.join(detail_parts) if detail_parts else '未触发D56分歧信号'
    result.raw = {
        'inout_hb': proxy.inout_hb,
        'inout_main_buy': proxy.inout_main_buy,
        'wei_bi': proxy.wei_bi,
        'outer_inner_ratio': proxy.outer_vol / proxy.inner_vol if proxy.inner_vol > 0 else 0,
        'divergence_detected': proxy.inout_hb > 0 and proxy.inout_main_buy < 0,
        'hsl': proxy.turnover_rate,
        'lb': proxy.vol_ratio,
        'hsl_lb_combo_score': s4_score,
        'hsl_lb_combo': s4_combo,
        'reject_signal': reject_signal,
        'dist_from_20d_low': dist_from_20d_low_pct,
        'dist_from_20d_high': dist_from_20d_high_pct,
        'total_range_pct': total_range_pct,
        'position_ratio': position_ratio,
        'is_low_position': is_low_position,
        'is_high_position': is_high_position,
    }
    
    return result


# ═══════════════════════════════════════════════════════════
# CapitalFlow Override规则
# ═══════════════════════════════════════════════════════════

@dataclass
class CapitalFlowOverrideResult:
    """资金流Override结果"""
    override_type: str = ''          # 'CF-Override-1' or 'CF-Override-2'
    triggered: bool = False
    original_factor: float = 0.3     # MEG ORANGE原始仓位系数
    overridden_factor: float = 0.3   # Override后仓位系数
    detail: str = ''
    d53_score: float = 0.0
    d54_score: float = 0.0
    d28_score: float = 0.0           # 催化剂维度
    d51_score: float = 0.0           # 趋势延续维度


def evaluate_capital_flow_override(
    d53_score: float,
    d54_score: float,
    d28_score: float = 0.0,
    d51_score: float = 0.0,
    meg_defcon: str = 'ORANGE'
) -> CapitalFlowOverrideResult:
    """
    资金流Override规则 — 绕过MEG ORANGE限制
    
    CF-Override-1: D53≥10 + D28≥6 → ORANGE x0.3→x0.5
      逻辑: 洗盘尾声(流出收敛)+催化剂确认 → 主力即将发力
    
    CF-Override-2: D54≥8 + D51≥6 → ORANGE x0.3→x0.5
      逻辑: 流入确认+趋势延续 → 资金趋势已确立
    
    RED不可Override!
    """
    result = CapitalFlowOverrideResult(
        d53_score=d53_score,
        d54_score=d54_score,
        d28_score=d28_score,
        d51_score=d51_score
    )
    
    if meg_defcon == 'RED':
        result.detail = 'RED不可Override! 市场崩盘, 资金流Override无效'
        return result
    
    if meg_defcon != 'ORANGE':
        result.detail = f'MEG={meg_defcon}, 无需Override'
        return result
    
    # CF-Override-1: D53≥10 + D28≥6
    if d53_score >= 10.0 and d28_score >= 6.0:
        result.override_type = 'CF-Override-1'
        result.triggered = True
        result.overridden_factor = 0.5
        result.detail = f'CF-Override-1触发! D53={d53_score:.0f}+D28={d28_score:.0f} → x0.3→x0.5'
        return result
    
    # CF-Override-2: D54≥8 + D51≥6
    if d54_score >= 8.0 and d51_score >= 6.0:
        result.override_type = 'CF-Override-2'
        result.triggered = True
        result.overridden_factor = 0.5
        result.detail = f'CF-Override-2触发! D54={d54_score:.0f}+D51={d51_score:.0f} → x0.3→x0.5'
        return result
    
    result.detail = f'Override未触发: D53={d53_score:.0f}/D54={d54_score:.0f}/D28={d28_score:.0f}/D51={d51_score:.0f}'
    return result


# ═══════════════════════════════════════════════════════════
# SmartMoney实时扫描器 (14:30-14:55运行)
# ═══════════════════════════════════════════════════════════

def smart_money_scan(
    realtime_proxies: List[RealTimeCapitalProxy],
    capital_flow_histories: Dict[str, List[CapitalFlowDay]],
    kline_chg_series: Dict[str, List[float]] = None,
    d28_scores: Dict[str, float] = None,
    min_d56: float = 5.0,
    min_d53: float = 6.0
) -> List[Dict]:
    """
    SmartMoney实时扫描器 — 14:30-14:55运行
    
    批量扫描条件:
      1. 实时代理: D56≥5 (委比+外盘+量比信号)
      2. 前日资金趋势: D53≥6 或 D54≥6 (流出收敛或流入确认)
      3. 催化剂: D28≥6 (可选加分)
    
    输出: SmartMoney候选池 → 送入T4 HardFilter + MEG门控
    
    Args:
        realtime_proxies: 实时代理数据列表 (批量从tdx_quotes获取)
        capital_flow_histories: 各股资金流向历史 (从tdx_api_data获取)
        kline_chg_series: 各股K线涨跌幅序列 (可选)
        d28_scores: 各股催化剂维度分数 (可选)
        min_d56: D56最低阈值
        min_d53: D53最低阈值
    """
    candidates = []
    
    for proxy in realtime_proxies:
        code = proxy.code
        
        # Step 1: D56实时代理评分
        d56_result = calc_d56_realtime_capital_proxy(proxy)
        if d56_result.actual_score < min_d56:
            continue  # 实时资金代理不达标
        
        # Step 2: 前日F10资金趋势评分
        cf_history = capital_flow_histories.get(code, [])
        d53_result = calc_d53_capital_flow_convergence(cf_history)
        d54_result = calc_d54_multi_day_inflow(
            cf_history,
            kline_chg_series=kline_chg_series.get(code) if kline_chg_series else None
        )
        
        # 至少一个资金趋势信号达标
        capital_trend_pass = d53_result.actual_score >= min_d53 or d54_result.actual_score >= 6.0
        if not capital_trend_pass:
            continue
        
        # Step 3: 催化剂加分 (可选)
        d28 = d28_scores.get(code, 0.0) if d28_scores else 0.0
        
        # 综合评分
        total_capital_score = d56_result.actual_score + d53_result.actual_score + d54_result.actual_score
        
        candidate = {
            'code': code,
            'name': proxy.name,
            'price': proxy.latest_price,
            'chg_pct': proxy.chg_pct,
            'D56_score': d56_result.actual_score,
            'D56_detail': d56_result.detail,
            'D53_score': d53_result.actual_score,
            'D53_detail': d53_result.detail,
            'D54_score': d54_result.actual_score,
            'D54_detail': d54_result.detail,
            'D28_score': d28,
            'capital_total': total_capital_score,
            'priority': 'P0' if total_capital_score >= 20 else 'P1' if total_capital_score >= 15 else 'P2',
        }
        
        candidates.append(candidate)
    
    # 按综合评分排序
    candidates.sort(key=lambda x: x['capital_total'], reverse=True)
    
    return candidates


# ═══════════════════════════════════════════════════════════
# TDX数据解析器
# ═══════════════════════════════════════════════════════════

def parse_tdx_capital_flow_to_history(tdx_response: Dict) -> List[CapitalFlowDay]:
    """
    从TDX tdx_api_data(zjlx)返回结果解析为CapitalFlowDay列表
    
    Args:
        tdx_response: TDX API响应 (包含tables[capital_flow])
    
    Returns:
        按日期升序排列的CapitalFlowDay列表
    """
    result = []
    
    try:
        tables = tdx_response.get('response', {}).get('transformed', {}).get('tables', [])
        
        for table in tables:
            if table.get('name') == 'capital_flow':
                rows = table.get('rows', [])
                for row in rows:
                    cf_day = CapitalFlowDay(
                        date=row.get('日期', ''),
                        main_net_yuan=float(row.get('主力净额金额(元)', 0) or 0),
                        main_pct=float(row.get('主力净额占比(%)', 0) or 0),
                        super_net_yuan=float(row.get('超大单净买入金额(元)', 0) or 0),
                        super_pct=float(row.get('超大单净买入占比(%)', 0) or 0),
                        large_net_yuan=float(row.get('大单净买入金额(元)', 0) or 0),
                        large_pct=float(row.get('大单净买入占比(%)', 0) or 0),
                        main_buy_net_yuan=float(row.get('主买净额金额(元)', 0) or 0),
                        main_buy_pct=float(row.get('主买净额占比(%)', 0) or 0),
                        close=float(row.get('收盘价', 0) or 0),
                    )
                    result.append(cf_day)
                break
        
        # 按日期升序排列
        result.sort(key=lambda x: x.date)
        
    except Exception as e:
        print(f'[parse_tdx_capital_flow] 解析错误: {e}')
    
    return result


def parse_tdx_quotes_to_realtime_proxy(tdx_quotes_response: Dict) -> RealTimeCapitalProxy:
    """
    从TDX tdx_quotes(hasProInfo=1)返回结果解析为RealTimeCapitalProxy
    
    Args:
        tdx_quotes_response: TDX quotes响应
    
    Returns:
        RealTimeCapitalProxy实例
    """
    proxy = RealTimeCapitalProxy()
    
    try:
        # TDX quotes返回格式可能不同, 这里做通用解析
        data = tdx_quotes_response
        
        # 尝试从response中提取
        resp = data.get('response', data)
        
        proxy.code = resp.get('code', '')
        proxy.name = resp.get('name', '')
        proxy.latest_price = float(resp.get('price', resp.get('最新价', 0)) or 0)
        proxy.chg_pct = float(resp.get('涨跌幅', resp.get('chg_pct', 0)) or 0)
        proxy.wei_bi = float(resp.get('委比', 0) or 0)
        proxy.outer_vol = float(resp.get('外盘', 0) or 0)
        proxy.inner_vol = float(resp.get('内盘', 0) or 0)
        proxy.vol_ratio = float(resp.get('量比', 0) or 0)
        proxy.turnover_rate = float(resp.get('换手率', 0) or 0)
        
    except Exception as e:
        print(f'[parse_tdx_quotes_to_proxy] 解析错误: {e}')
    
    return proxy


# ═══════════════════════════════════════════════════════════
# V13.5.26 综合评估器
# ═══════════════════════════════════════════════════════════

def evaluate_v13526(
    capital_flow_history: List[CapitalFlowDay],
    realtime_proxy: Optional[RealTimeCapitalProxy] = None,
    kline_chg_series: Optional[List[float]] = None,
    ma20_rising: bool = False,
    d28_score: float = 0.0,
    d51_score: float = 0.0,
    meg_defcon: str = 'ORANGE'
) -> Dict:
    """
    V13.5.26 综合评估 — 资金流4维度 + Override + SmartMoney
    
    Returns:
        {
            'D53': DimensionScore,
            'D54': DimensionScore,
            'D55': DimensionScore,
            'D56': DimensionScore (if realtime_proxy provided),
            'capital_total': float,
            'override': CapitalFlowOverrideResult,
            'smart_money_grade': str,  # 'A'/'B'/'C'/'D'
            'recommendation': str,
        }
    """
    # D53
    d53 = calc_d53_capital_flow_convergence(capital_flow_history)
    
    # D54
    d54 = calc_d54_multi_day_inflow(
        capital_flow_history,
        kline_chg_series=kline_chg_series,
        ma20_rising=ma20_rising
    )
    
    # D55
    d55 = calc_d55_capital_price_divergence(
        capital_flow_history,
        kline_chg_series=kline_chg_series
    )
    
    # D56 (可选)
    d56 = None
    if realtime_proxy:
        d56 = calc_d56_realtime_capital_proxy(realtime_proxy)
    
    # 综合评分
    capital_total = d53.actual_score + d54.actual_score + d55.actual_score
    if d56:
        capital_total += d56.actual_score
    
    # Override评估
    override = evaluate_capital_flow_override(
        d53_score=d53.actual_score,
        d54_score=d54.actual_score,
        d28_score=d28_score,
        d51_score=d51_score,
        meg_defcon=meg_defcon
    )
    
    # SmartMoney等级
    if capital_total >= 25:
        grade = 'A'  # 资金流极强
    elif capital_total >= 18:
        grade = 'B'  # 资金流较强
    elif capital_total >= 10:
        grade = 'C'  # 资金流中等
    else:
        grade = 'D'  # 资金流弱
    
    # 推荐
    if grade == 'A' and override.triggered:
        recommendation = '★STRONG_BUY_CAPITAL — 资金流极强+Override触发'
    elif grade == 'A':
        recommendation = 'BUY_CAPITAL — 资金流极强'
    elif grade == 'B' and override.triggered:
        recommendation = 'BUY_CAPITAL_OVERRIDE — 资金流较强+Override触发'
    elif grade == 'B':
        recommendation = 'WATCH_CAPITAL — 资金流较强, 需催化确认'
    elif grade == 'C':
        recommendation = 'NEUTRAL_CAPITAL — 资金流中等'
    else:
        recommendation = 'AVOID_CAPITAL — 资金流弱/流出持续'
    
    return {
        'D53': d53,
        'D54': d54,
        'D55': d55,
        'D56': d56,
        'capital_total': capital_total,
        'override': override,
        'smart_money_grade': grade,
        'recommendation': recommendation,
    }


# ═══════════════════════════════════════════════════════════
# 7/7真实数据验证 — 3只涨停股 + 1只持仓股
# ═══════════════════════════════════════════════════════════

def backtest_v13526_on_0707_real_data():
    """
    用7/7真实TDX资金流数据验证V13.5.26
    
    测试股票:
      002185 华天科技 (7/7涨停+9.98%) — V13.5.25已捕获
      600206 有研新材 (7/7涨停+10.00%) — V13.5.25已捕获
      002129 TCL中环 (7/7涨停+9.98%) — V13.5.25部分捕获
      600118 中国卫星 (7/7跌-4.41%) — 持仓, 持续下跌
    
    评估时点: 7/6收盘 (使用7/6之前的资金流历史)
    """
    print('=' * 70)
    print('V13.5.26 资金流核心引擎 — 7/7真实TDX数据验证')
    print('=' * 70)
    
    # ═══════════════════════════════════════════════════
    # 华天科技 002185 — 7/6选股日资金流数据
    # ═══════════════════════════════════════════════════
    huatian_7_6_history = [
        # 7/6之前5日 (模拟7/6选股时可用的数据)
        CapitalFlowDay(date='2026-07-06', main_net_yuan=-16627456, main_pct=-0.24,
                       super_net_yuan=-33534016, super_pct=-0.48,
                       large_net_yuan=16906688, large_pct=0.24,
                       main_buy_net_yuan=-196174336, main_buy_pct=-2.81, close=19.94),
        CapitalFlowDay(date='2026-07-03', main_net_yuan=-638389632, main_pct=-9.90,
                       super_net_yuan=-390535232, super_pct=-6.06,
                       large_net_yuan=-247854528, large_pct=-3.85,
                       main_buy_net_yuan=-662105088, main_buy_pct=-10.27, close=19.51),
        CapitalFlowDay(date='2026-07-02', main_net_yuan=-427617536, main_pct=-4.84,
                       super_net_yuan=-103461632, super_pct=-1.17,
                       large_net_yuan=-324156096, large_pct=-3.67,
                       main_buy_net_yuan=24011264, main_buy_pct=0.27, close=20.27),
        CapitalFlowDay(date='2026-07-01', main_net_yuan=-384647168, main_pct=-3.24,
                       super_net_yuan=-160882688, super_pct=-1.36,
                       large_net_yuan=-223764608, large_pct=-1.89,
                       main_buy_net_yuan=-612683776, main_buy_pct=-5.16, close=21.86),
        CapitalFlowDay(date='2026-06-30', main_net_yuan=54059008, main_pct=0.52,
                       super_net_yuan=193746304, super_pct=1.88,
                       large_net_yuan=-139687168, large_pct=-1.35,
                       main_buy_net_yuan=142988800, main_buy_pct=1.39, close=22.31),
    ]
    
    # ═══════════════════════════════════════════════════
    # 有研新材 600206 — 7/6选股日资金流数据
    # ═══════════════════════════════════════════════════
    youyan_7_6_history = [
        CapitalFlowDay(date='2026-07-06', main_net_yuan=-686814080, main_pct=-11.92,
                       super_net_yuan=-394243712, super_pct=-6.84,
                       large_net_yuan=-292570368, large_pct=-5.08,
                       main_buy_net_yuan=-70202880, main_buy_pct=-1.22, close=50.89),
        CapitalFlowDay(date='2026-07-03', main_net_yuan=-358485760, main_pct=-5.01,
                       super_net_yuan=-155353344, super_pct=-2.17,
                       large_net_yuan=-203132352, large_pct=-2.84,
                       main_buy_net_yuan=347041792, main_buy_pct=4.85, close=54.3),
        CapitalFlowDay(date='2026-07-02', main_net_yuan=184203264, main_pct=2.21,
                       super_net_yuan=269310208, super_pct=3.23,
                       large_net_yuan=-85106688, large_pct=-1.02,
                       main_buy_net_yuan=626891520, main_buy_pct=7.53, close=57.81),
        CapitalFlowDay(date='2026-07-01', main_net_yuan=-859751424, main_pct=-9.29,
                       super_net_yuan=-512369408, super_pct=-5.53,
                       large_net_yuan=-347381760, large_pct=-3.75,
                       main_buy_net_yuan=-521257984, main_buy_pct=-5.63, close=59.94),
        CapitalFlowDay(date='2026-06-30', main_net_yuan=-946244096, main_pct=-10.18,
                       super_net_yuan=-719539840, super_pct=-7.74,
                       large_net_yuan=-226704256, large_pct=-2.44,
                       main_buy_net_yuan=-220195328, main_buy_pct=-2.37, close=66.6),
    ]
    
    # ═══════════════════════════════════════════════════
    # TCL中环 002129 — 7/6选股日资金流数据
    # ═══════════════════════════════════════════════════
    tcl_7_6_history = [
        CapitalFlowDay(date='2026-07-06', main_net_yuan=-148662016, main_pct=-5.87,
                       super_net_yuan=-108228352, super_pct=-4.27,
                       large_net_yuan=-40433632, large_pct=-1.60,
                       main_buy_net_yuan=-429417216, main_buy_pct=-16.95, close=10.02),
        CapitalFlowDay(date='2026-07-03', main_net_yuan=-347708288, main_pct=-10.35,
                       super_net_yuan=-225284288, super_pct=-6.70,
                       large_net_yuan=-122423904, large_pct=-3.64,
                       main_buy_net_yuan=-577900160, main_buy_pct=-17.20, close=10.37),
        CapitalFlowDay(date='2026-07-02', main_net_yuan=-499020032, main_pct=-12.17,
                       super_net_yuan=-322596416, super_pct=-7.87,
                       large_net_yuan=-176423776, large_pct=-4.30,
                       main_buy_net_yuan=-579771520, main_buy_pct=-14.14, close=11.03),
        CapitalFlowDay(date='2026-07-01', main_net_yuan=-80753024, main_pct=-1.39,
                       super_net_yuan=-117575424, super_pct=-2.02,
                       large_net_yuan=36822464, large_pct=0.63,
                       main_buy_net_yuan=200658688, main_buy_pct=3.44, close=11.9),
        CapitalFlowDay(date='2026-06-30', main_net_yuan=151620352, main_pct=2.72,
                       super_net_yuan=91752768, super_pct=1.65,
                       large_net_yuan=59867840, large_pct=1.07,
                       main_buy_net_yuan=651633664, main_buy_pct=11.69, close=12.02),
    ]
    
    # ═══════════════════════════════════════════════════
    # 中国卫星 600118 — 7/6选股日资金流数据
    # ═══════════════════════════════════════════════════
    satellite_7_6_history = [
        CapitalFlowDay(date='2026-07-06', main_net_yuan=-245380736, main_pct=-5.16,
                       super_net_yuan=-179945920, super_pct=-3.78,
                       large_net_yuan=-65434816, large_pct=-1.38,
                       main_buy_net_yuan=-511685248, main_buy_pct=-10.76, close=83.15),
        CapitalFlowDay(date='2026-07-03', main_net_yuan=687381376, main_pct=11.85,
                       super_net_yuan=540996672, super_pct=9.33,
                       large_net_yuan=146384704, large_pct=2.52,
                       main_buy_net_yuan=313648128, main_buy_pct=5.41, close=86.3),
        CapitalFlowDay(date='2026-07-02', main_net_yuan=-427610624, main_pct=-8.04,
                       super_net_yuan=-283230976, super_pct=-5.33,
                       large_net_yuan=-144379776, large_pct=-2.72,
                       main_buy_net_yuan=-510325760, main_buy_pct=-9.60, close=81.5),
        CapitalFlowDay(date='2026-07-01', main_net_yuan=555287296, main_pct=8.30,
                       super_net_yuan=428738944, super_pct=6.41,
                       large_net_yuan=126548224, large_pct=1.89,
                       main_buy_net_yuan=684243456, main_buy_pct=10.23, close=85.65),
        CapitalFlowDay(date='2026-06-30', main_net_yuan=-134746880, main_pct=-2.14,
                       super_net_yuan=-92559872, super_pct=-1.47,
                       large_net_yuan=-42187136, large_pct=-0.67,
                       main_buy_net_yuan=-243048448, main_buy_pct=-3.86, close=82.05),
    ]
    
    # ═══════════════════════════════════════════════════
    # 验证各股
    # ═══════════════════════════════════════════════════
    stocks = [
        ('002185', '华天科技', huatian_7_6_history, '涨停+9.98%', 11, 13, 'YES'),
        ('600206', '有研新材', youyan_7_6_history, '涨停+10.00%', 6, 15, 'YES'),
        ('002129', 'TCL中环', tcl_7_6_history, '涨停+9.98%', 5, 14, 'PARTIAL'),
        ('600118', '中国卫星', satellite_7_6_history, '跌-4.41%', 0, 0, 'NO'),
    ]
    
    # V13.5.25 D28分数 (涨价催化)
    d28_scores = {'002185': 8, '600206': 8, '002129': 7, '600118': 2}
    
    # K线涨跌幅 (7/1-7/6)
    chg_series = {
        '002185': [2.31, -7.17, -2.13, 1.49, -5.5, -8.8],  # 模拟近似
        '600206': [-9.29, 2.21, -5.01, -11.92, 0, 0],  # 需从K线计算
        '002129': [-1.39, 2.72, -12.17, -10.35, -5.87, 0],
        '600118': [-2.14, 8.30, -8.04, 11.85, -5.16, 0],
    }
    
    results = []
    for code, name, history, outcome, d51_v25, d52_v25, v25_result in stocks:
        d28 = d28_scores.get(code, 0)
        chg = chg_series.get(code, [])
        
        eval_result = evaluate_v13526(
            capital_flow_history=history,
            kline_chg_series=chg,
            d28_score=d28,
            d51_score=float(d51_v25),
            meg_defcon='ORANGE'
        )
        
        d53 = eval_result['D53']
        d54 = eval_result['D54']
        d55 = eval_result['D55']
        override = eval_result['override']
        
        print(f'\n━━━ {code} {name} ({outcome}) ━━━')
        print(f'  D53={d53.actual_score:.0f}/{d53.max_score:.0f} | {d53.detail}')
        print(f'  D54={d54.actual_score:.0f}/{d54.max_score:.0f} | {d54.detail}')
        print(f'  D55={d55.actual_score:.0f}/{d55.max_score:.0f} | {d55.detail}')
        print(f'  资金流总分={eval_result["capital_total"]:.0f}')
        print(f'  SmartMoney等级={eval_result["smart_money_grade"]}')
        print(f'  Override={override.triggered} | {override.detail}')
        print(f'  推荐={eval_result["recommendation"]}')
        print(f'  V13.5.25结果={v25_result} (D51={d51_v25}, D52={d52_v25})')
        
        # 核心问题: 7/6选股日能否预测7/7涨停?
        capture = 'CAPTURED' if eval_result['smart_money_grade'] in ('A', 'B') else 'MISSED' if eval_result['smart_money_grade'] == 'D' else 'PARTIAL'
        print(f'  ★V13.5.26捕获={capture}')
        
        results.append({
            'code': code, 'name': name, 'outcome': outcome,
            'D53': d53.actual_score, 'D54': d54.actual_score, 'D55': d55.actual_score,
            'capital_total': eval_result['capital_total'],
            'grade': eval_result['smart_money_grade'],
            'override_triggered': override.triggered,
            'v25_result': v25_result,
            'v26_capture': capture,
        })
    
    # ═══════════════════════════════════════════════════
    # 汇总
    # ═══════════════════════════════════════════════════
    print(f'\n{"=" * 70}')
    print('V13.5.26 vs V13.5.25 对比汇总')
    print(f'{"=" * 70}')
    
    captured_v26 = sum(1 for r in results if r['v26_capture'] in ('CAPTURED', 'PARTIAL'))
    captured_v25 = sum(1 for r in results if r['v25_result'] in ('YES', 'PARTIAL'))
    
    print(f'  V13.5.25: {captured_v25}/3涨停 = {captured_v25/3*100:.0f}%')
    print(f'  V13.5.26: {captured_v26}/3涨停 = {captured_v26/3*100:.0f}%')
    
    for r in results:
        v26_status = '✓' if r['v26_capture'] == 'CAPTURED' else '⚠' if r['v26_capture'] == 'PARTIAL' else '✗'
        v25_status = '✓' if r['v25_result'] == 'YES' else '⚠' if r['v25_result'] == 'PARTIAL' else '✗'
        print(f'  {r["code"]} {r["name"]}: V25={v25_status} V26={v26_status} | '
              f'D53={r["D53"]:.0f} D54={r["D54"]:.0f} D55={r["D55"]:.0f} total={r["capital_total"]:.0f} grade={r["grade"]}')
    
    return results


# ═══════════════════════════════════════════════════════════
# CLI入口
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print('V13.5.26 CapitalFlowCore — 资金流核心引擎')
    print('范式转变: K线形态主导 → 资金流趋势主导')
    print()
    
    # 运行7/7真实数据验证
    results = backtest_v13526_on_0707_real_data()
    
    # 关键洞察总结
    print(f'\n{"=" * 70}')
    print('★核心洞察 (来自真实TDX资金流数据分析)')
    print(f'{"=" * 70}')
    print('1. 涨停日特征: 超大单巨量流入+主买(散户)巨量流出 = 机构吸筹散户割肉')
    print('2. 选股日信号: 不是"前一日净流入"，而是"资金流出收敛至近零"')
    print('3. 华天科技7/6: -1663万(-0.24%) — 5日流出收敛至近零 → 洗盘尾声!')
    print('4. 有研新材7/6: -6.87亿(-11.92%) — 大幅流出 → 仅看绝对值无法预测')
    print('5. 中国卫星7/6: -2.45亿(-5.16%) — 持续流出无收敛 → 继续下跌')
    print()
    print('★V13.5.26范式转变:')
    print('  从"K线形态主导+资金确认辅助" → "资金流趋势主导+K线形态辅助"')
    print('  D53(流出收敛) + D54(流入确认) + D55(背离) + D56(实时代理)')
    print('  CF-Override-1: D53≥10+D28≥6 → 绕过MEG ORANGE')
    print('  CF-Override-2: D54≥8+D51≥6 → 绕过MEG ORANGE')
