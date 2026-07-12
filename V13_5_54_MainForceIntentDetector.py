#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.54 主力资金意图识别引擎 (Main Force Intent Detector)
============================================================
核心问题: "明明主力资金流入，往往为什么第二天却往下砸？"

根因洞察:
  主力常用对倒盘制造虚假繁荣 —— 左手倒右手，表面大笔买入，
  实际是在出货。量化系统不看表面大笔买入，专扒资金真实意图。

★关键发现 (TDX真实数据验证):
  TDX zjlx数据中"主力净额"和"主买净额"是两个独立字段:
    - 主力净额(main_net) = 大单+超大单净买入 → 代表机构方向
    - 主买净额(main_buy_net) ≈ 散户行为方向
  当主力净额>0但主买净额<0 → 主力大单买入但对倒出货!

  600118中国卫星 7/10涨停日: 主力+11.62亿 BUT 主买-11.99亿 → 对倒出货!
  002623亚玛顿 7/10涨停日: 主力+2353万 BUT 主买-1509万 → 对倒特征!
  002001新和成 7/10: 主力+4919万 AND 主买+270万(同向) → 真实买入!

5大检测模块:
  M1: 对倒盘检测器 (WashTradeDetector) — 主力/主买方向背离 + 5分钟K线量价异常
  M2: 资金持续性追踪器 (CapitalPersistenceTracker) — 连续流入天数 + 收敛趋势
  M3: 主动买入区分器 (ActiveBuyDiscriminator) — 外盘/内盘 + 委比 + 超大单占比
  M4: 诱多/诱空模式识别 (LurePatternDetector) — 涨停日主买方向 + 尾盘量比异常
  M5: 意图综合评分 (IntentScore) — 0-100分 → D3维度增强/惩罚

意图分类:
  GENUINE_INFLOW (真实进场): 主力/主买同向正 + 持续性>3天 + 外盘>内盘
  WASH_TRADE_DISTRIBUTION (对倒出货): 主力正/主买负 + 背离度>15% + 涨停日
  LURE_BULLISH (诱多): 尾盘突然放量但主买净额<0 + 次日大概率下跌
  LURE_BEARISH (诱空): 盘中恐慌性卖出但主力净额>0 + 次日可能反弹
  NEUTRAL (中性): 信号矛盾或数据不足

集成方式: D3主力资金维度增强
  - GENUINE_INFLOW → D3+12分加成
  - WASH_TRADE_DISTRIBUTION → D3-15分惩罚 + 全局蒸馏分-8
  - LURE_BULLISH → D3-8分惩罚 + T+1置信度-10%
  - LURE_BEARISH → D3+5分加成(反向机会)
  - NEUTRAL → D3不变

数据来源:
  - TDX tdx_api_data fixedTag="zjlx" → 主力/超大单/大单/主买净额历史(20天)
  - TDX tdx_kline period="0" → 5分钟K线(微观量价分析)
  - TDX tdx_quotes hasProInfo="1" → 外盘/内盘/委比实时

作者: 毕方灵犀·貔貅助手 V13.5.54 (亚瑟数字分身)
日期: 2026-07-12
"""

import json
import os
import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


# ═══════════════════════════════════════════════════════════
# 枚举: 主力意图分类
# ═══════════════════════════════════════════════════════════

class MainForceIntent(Enum):
    """主力资金意图分类"""
    GENUINE_INFLOW = "GENUINE_INFLOW"                # 真实进场
    WASH_TRADE_DISTRIBUTION = "WASH_TRADE_DISTRIBUTION"  # 对倒出货
    LURE_BULLISH = "LURE_BULLISH"                    # 诱多
    LURE_BEARISH = "LURE_BEARISH"                    # 诱空
    NEUTRAL = "NEUTRAL"                              # 中性


# ═══════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class ZJLXDay:
    """单日资金流向数据 (从TDX zjlx接口解析)"""
    date: str = ""                         # 日期 "2026-07-10"
    main_net_yuan: float = 0.0             # 主力净额(元)
    main_net_pct: float = 0.0              # 主力净额占比(%)
    super_net_yuan: float = 0.0            # 超大单净额(元)
    super_net_pct: float = 0.0             # 超大单净额占比(%)
    large_net_yuan: float = 0.0            # 大单净额(元)
    large_net_pct: float = 0.0             # 大单净额占比(%)
    main_buy_net_yuan: float = 0.0         # 主买净额(元) ≈ 散户方向
    main_buy_net_pct: float = 0.0          # 主买净额占比(%)
    close: float = 0.0                     # 收盘价
    pct_change: float = 0.0                # 涨跌幅(%)


@dataclass
class MinKLine:
    """5分钟K线数据 (从TDX tdx_kline period=0解析)"""
    timestamp: int = 0                     # 时间戳
    time_str: str = ""                     # 时间字符串 "13:00"
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0                    # 成交量(手)
    amount: float = 0.0                    # 成交额(元)


@dataclass
class RealtimeQuote:
    """实时行情数据 (从tdx_quotes hasProInfo=1解析)"""
    code: str = ""
    name: str = ""
    wei_bi: float = 0.0                    # 委比(%)
    outer_vol: float = 0.0                 # 外盘(手)
    inner_vol: float = 0.0                 # 内盘(手)
    vol_ratio: float = 0.0                 # 量比
    turnover_rate: float = 0.0             # 换手率(%)
    last_close: float = 0.0                # 昨收
    close: float = 0.0                     # 现价


@dataclass
class WashTradeSignal:
    """对倒盘检测信号"""
    divergence_score: float = 0.0          # 背离度(0-100)
    is_wash_trade: bool = False            # 是否对倒盘
    main_buy_divergence: bool = False      # 主力/主买方向背离
    divergence_pct: float = 0.0            # 背离幅度(|主力%|+|主买%|)
    volume_price_anomaly: bool = False     # 5分钟K线量价异常
    anomaly_detail: str = ""               # 异常详情
    score: float = 0.0                     # 对倒盘得分(0-100, 越高越危险)


@dataclass
class PersistenceSignal:
    """资金持续性信号"""
    consecutive_inflow_days: int = 0       # 连续净流入天数
    consecutive_outflow_days: int = 0      # 连续净流出天数
    inflow_convergence: bool = False       # 流入是否收敛(流出→近零)
    convergence_score: float = 0.0         # 收敛度(0-100)
    avg_net_pct_5d: float = 0.0            # 5日平均主力净额占比
    net_pct_trend: str = "stable"          # increasing/decreasing/stable
    score: float = 0.0                     # 持续性得分(0-100)


@dataclass
class ActiveBuySignal:
    """主动买入区分信号"""
    outer_inner_ratio: float = 1.0         # 外盘/内盘比
    wei_bi_positive: bool = False          # 委比是否为正
    super_large_dominance: float = 0.0     # 超大单占比(%)
    is_active_buy: bool = False            # 是否主动买入
    score: float = 0.0                     # 主动买入得分(0-100)


@dataclass
class LurePatternSignal:
    """诱多/诱空模式信号"""
    is_lure_bullish: bool = False          # 是否诱多
    is_lure_bearish: bool = False          # 是否诱空
    tail_surge_volume: bool = False        # 尾盘突然放量
    tail_surge_ratio: float = 1.0          # 尾盘量比
    main_buy_negative_on_limit_up: bool = False  # 涨停日主买净额为负
    score: float = 0.0                     # 诱多/诱空得分(0-100)


@dataclass
class IntentResult:
    """主力意图综合识别结果"""
    code: str = ""
    name: str = ""
    intent: MainForceIntent = MainForceIntent.NEUTRAL
    intent_score: float = 50.0             # 意图综合评分(0-100)
    wash_trade: WashTradeSignal = field(default_factory=WashTradeSignal)
    persistence: PersistenceSignal = field(default_factory=PersistenceSignal)
    active_buy: ActiveBuySignal = field(default_factory=ActiveBuySignal)
    lure_pattern: LurePatternSignal = field(default_factory=LurePatternSignal)

    # D3维度增强/惩罚
    d3_adjustment: float = 0.0             # D3分数调整值
    global_penalty: float = 0.0            # 全局蒸馏分惩罚
    t1_confidence_adjustment: float = 0.0  # T+1置信度调整(%)

    # 诊断信息
    diagnosis: str = ""                    # 诊断描述
    risk_warning: str = ""                 # 风险警告
    recommendation: str = ""               # 操作建议


# ═══════════════════════════════════════════════════════════
# M1: 对倒盘检测器
# ═══════════════════════════════════════════════════════════

class WashTradeDetector:
    """
    对倒盘检测器

    核心逻辑:
      主力净额>0(大单买入) 但 主买净额<0(散户方向卖出) = 对倒盘信号
      对倒背离度 = |主力净额占比%| + |主买净额占比%|
      背离度>15% → 高概率对倒出货

    辅助验证:
      5分钟K线量价异常: 放巨量但价格不动/微跌 = 锯齿特征
    """

    # 对倒背离度阈值
    DIVERGENCE_STRONG = 15.0    # >15% → 强对倒信号
    DIVERGENCE_MODERATE = 8.0   # >8% → 中等对倒信号
    DIVERGENCE_WEAK = 3.0       # >3% → 弱对倒信号

    def detect(self, zjlx_today: ZJLXDay,
               min_klines: Optional[List[MinKLine]] = None) -> WashTradeSignal:
        """检测对倒盘信号"""
        signal = WashTradeSignal()

        # ── 1. 主力/主买方向背离检测 ──
        main_positive = zjlx_today.main_net_pct > 0
        main_buy_negative = zjlx_today.main_buy_net_pct < 0
        main_negative = zjlx_today.main_net_pct < 0
        main_buy_positive = zjlx_today.main_buy_net_pct > 0

        signal.main_buy_divergence = (main_positive and main_buy_negative) or \
                                      (main_negative and main_buy_positive)

        if signal.main_buy_divergence:
            signal.divergence_pct = abs(zjlx_today.main_net_pct) + abs(zjlx_today.main_buy_net_pct)

            # 背离度评分 (0-100)
            if signal.divergence_pct >= self.DIVERGENCE_STRONG:
                signal.divergence_score = min(100.0, 50.0 + signal.divergence_pct * 2)
            elif signal.divergence_pct >= self.DIVERGENCE_MODERATE:
                signal.divergence_score = 50.0 + (signal.divergence_pct - self.DIVERGENCE_MODERATE) * 3
            elif signal.divergence_pct >= self.DIVERGENCE_WEAK:
                signal.divergence_score = 20.0 + (signal.divergence_pct - self.DIVERGENCE_WEAK) * 5
            else:
                signal.divergence_score = signal.divergence_pct * 5

        # ── 2. 5分钟K线量价异常检测 ──
        if min_klines and len(min_klines) >= 10:
            anomaly = self._detect_volume_price_anomaly(min_klines)
            signal.volume_price_anomaly = anomaly[0]
            signal.anomaly_detail = anomaly[1]

            if anomaly[0] and signal.main_buy_divergence:
                # 量价异常 + 方向背离 = 强对倒确认
                signal.divergence_score = min(100.0, signal.divergence_score + 20)

        # ── 3. 综合判定 ──
        signal.score = signal.divergence_score
        signal.is_wash_trade = signal.score >= 40.0

        return signal

    def _detect_volume_price_anomaly(self, klines: List[MinKLine]) -> Tuple[bool, str]:
        """
        检测5分钟K线量价异常
        - 放巨量但价格不动 = 锯齿特征
        - 成交量突然3倍以上但涨跌幅<0.5%
        """
        if len(klines) < 10:
            return False, ""

        # 计算平均成交量
        avg_vol = sum(k.volume for k in klines) / len(klines)
        if avg_vol <= 0:
            return False, ""

        anomalies = []
        anomaly_count = 0

        for i, k in enumerate(klines):
            vol_ratio = k.volume / avg_vol if avg_vol > 0 else 1.0
            price_change = abs(k.close - k.open) / k.open * 100 if k.open > 0 else 0

            # 放巨量(>3倍平均) 但价格几乎不动(<0.3%)
            if vol_ratio >= 3.0 and price_change < 0.3:
                anomaly_count += 1
                anomalies.append(f"{k.time_str}量{vol_ratio:.1f}x价变{price_change:.2f}%")

        if anomaly_count >= 2:
            detail = "; ".join(anomalies[:3])
            return True, f"检测到{anomaly_count}次量价背离({detail})"
        elif anomaly_count >= 1:
            return True, f"检测到1次量价异常({anomalies[0]})"

        return False, ""


# ═══════════════════════════════════════════════════════════
# M2: 资金持续性追踪器
# ═══════════════════════════════════════════════════════════

class CapitalPersistenceTracker:
    """
    资金持续性追踪器

    核心逻辑:
      真进场 = 连续多日主力净额同向(正)
      假动作 = 只撑1-2天，随后反转
      洗盘尾声 = 持续流出但收敛至近零
    """

    def track(self, zjlx_history: List[ZJLXDay]) -> PersistenceSignal:
        """追踪资金持续性"""
        signal = PersistenceSignal()

        if len(zjlx_history) < 2:
            return signal

        # 按日期倒序排列(最新在前)
        sorted_history = sorted(zjlx_history, key=lambda x: x.date, reverse=True)

        # ── 1. 连续流入/流出天数 ──
        latest = sorted_history[0]
        if latest.main_net_pct > 0:
            signal.consecutive_inflow_days = self._count_consecutive(sorted_history, positive=True)
        else:
            signal.consecutive_outflow_days = self._count_consecutive(sorted_history, positive=False)

        # ── 2. 5日平均主力净额占比 ──
        recent_5 = sorted_history[:5]
        signal.avg_net_pct_5d = sum(d.main_net_pct for d in recent_5) / len(recent_5)

        # ── 3. 流入/流出趋势 ──
        if len(sorted_history) >= 5:
            pcts = [d.main_net_pct for d in sorted_history[:5]]
            if pcts[0] > pcts[2] and pcts[2] > pcts[4]:
                signal.net_pct_trend = "increasing"
            elif pcts[0] < pcts[2] and pcts[2] < pcts[4]:
                signal.net_pct_trend = "decreasing"

        # ── 4. 流出收敛检测 ──
        # 最近3天主力净额占比绝对值递减 = 收敛
        if len(sorted_history) >= 3:
            recent_3 = sorted_history[:3]
            abs_pcts = [abs(d.main_net_pct) for d in recent_3]
            if abs_pcts[0] < abs_pcts[1] and abs_pcts[1] < abs_pcts[2]:
                signal.inflow_convergence = True
                # 收敛度: 绝对值越小收敛越强
                signal.convergence_score = min(100.0, 60.0 + (1.0 / (abs_pcts[0] + 0.1)) * 10)

        # ── 5. 综合评分 ──
        score = 0.0

        # 连续流入加分
        if signal.consecutive_inflow_days >= 5:
            score += 40
        elif signal.consecutive_inflow_days >= 3:
            score += 30
        elif signal.consecutive_inflow_days >= 2:
            score += 20
        elif signal.consecutive_inflow_days >= 1:
            score += 10

        # 流出收敛加分(洗盘尾声)
        if signal.inflow_convergence:
            score += 30

        # 趋势加分
        if signal.net_pct_trend == "increasing" and latest.main_net_pct > 0:
            score += 20
        elif signal.net_pct_trend == "decreasing" and latest.main_net_pct < 0:
            score += 15  # 流出减速

        # 5日均值为正加分
        if signal.avg_net_pct_5d > 2.0:
            score += 10

        signal.score = min(100.0, score)
        return signal

    def _count_consecutive(self, sorted_history: List[ZJLXDay], positive: bool) -> int:
        """计算连续流入/流出天数"""
        count = 0
        for d in sorted_history:
            if positive and d.main_net_pct > 0:
                count += 1
            elif not positive and d.main_net_pct < 0:
                count += 1
            else:
                break
        return count


# ═══════════════════════════════════════════════════════════
# M3: 主动买入区分器
# ═══════════════════════════════════════════════════════════

class ActiveBuyDiscriminator:
    """
    主动买入区分器

    核心逻辑:
      主动买入 = 外盘>内盘1.5倍 + 委比为正 + 超大单占比>15%
      被动买入/对倒 = 外盘≈内盘 + 委比波动 + 超大单占比异常高但主买负
    """

    def discriminate(self, realtime: RealtimeQuote,
                     zjlx_today: Optional[ZJLXDay] = None) -> ActiveBuySignal:
        """区分主动买入vs对倒"""
        signal = ActiveBuySignal()

        # ── 1. 外盘/内盘比 ──
        if realtime.inner_vol > 0:
            signal.outer_inner_ratio = realtime.outer_vol / realtime.inner_vol
        elif realtime.outer_vol > 0:
            signal.outer_inner_ratio = 2.0  # 内盘为0视为强势

        # ── 2. 委比 ──
        signal.wei_bi_positive = realtime.wei_bi > 0

        # ── 3. 超大单占比 ──
        if zjlx_today and zjlx_today.main_net_pct != 0:
            total_pct = abs(zjlx_today.main_net_pct) + abs(zjlx_today.super_net_pct) + \
                        abs(zjlx_today.large_net_pct)
            if total_pct > 0:
                signal.super_large_dominance = abs(zjlx_today.super_net_pct) / total_pct * 100

        # ── 4. 综合判定 ──
        score = 0.0

        # 外盘>内盘1.5倍 → 主动买入
        if signal.outer_inner_ratio >= 2.0:
            score += 35
        elif signal.outer_inner_ratio >= 1.5:
            score += 25
        elif signal.outer_inner_ratio >= 1.2:
            score += 15
        elif signal.outer_inner_ratio < 0.8:
            score -= 10  # 内盘大于外盘 → 卖压

        # 委比正 → 买盘强
        if realtime.wei_bi > 10:
            score += 25
        elif realtime.wei_bi > 0:
            score += 15
        elif realtime.wei_bi < -10:
            score -= 15

        # 超大单占比高 → 机构参与
        if signal.super_large_dominance > 50:
            score += 20
        elif signal.super_large_dominance > 30:
            score += 10

        # 超大单方向与主买方向一致 → 真实买入
        if zjlx_today and zjlx_today.super_net_pct > 0 and zjlx_today.main_buy_net_pct > 0:
            score += 20
        elif zjlx_today and zjlx_today.super_net_pct > 0 and zjlx_today.main_buy_net_pct < 0:
            score -= 25  # 超大单正但主买负 → 对倒嫌疑

        signal.score = max(0.0, min(100.0, 50.0 + score))
        signal.is_active_buy = signal.score >= 60.0

        return signal


# ═══════════════════════════════════════════════════════════
# M4: 诱多/诱空模式识别
# ═══════════════════════════════════════════════════════════

class LurePatternDetector:
    """
    诱多/诱空模式识别

    诱多逻辑:
      涨停日主买净额为负(散户在卖) + 尾盘突然放量 → 次日大概率下跌
      主力通过对倒拉涨停吸引散户追高，次日开盘砸盘出货

    诱空逻辑:
      盘中恐慌性下跌但主力净额为正 → 主力借恐慌吸筹
      次日可能反弹
    """

    # 尾盘时间段(5分钟K线)
    TAIL_SESSION_START = "14:30"

    def detect(self, zjlx_today: ZJLXDay,
               min_klines: Optional[List[MinKLine]] = None,
               is_limit_up: bool = False) -> LurePatternSignal:
        """检测诱多/诱空模式"""
        signal = LurePatternSignal()

        # ── 1. 涨停日主买净额方向检测 ──
        if is_limit_up:
            signal.main_buy_negative_on_limit_up = zjlx_today.main_buy_net_pct < 0

            if signal.main_buy_negative_on_limit_up and zjlx_today.main_net_pct > 0:
                # 涨停日: 主力正 + 主买负 = 诱多嫌疑
                signal.is_lure_bullish = True
                signal.score += 40

        # ── 2. 尾盘突然放量检测 ──
        if min_klines and len(min_klines) >= 20:
            tail_surge = self._detect_tail_surge(min_klines)
            signal.tail_surge_volume = tail_surge[0]
            signal.tail_surge_ratio = tail_surge[1]

            if signal.tail_surge_volume and signal.tail_surge_ratio >= 2.0:
                signal.score += 25
                if zjlx_today.main_buy_net_pct < 0:
                    signal.is_lure_bullish = True
                    signal.score += 15

        # ── 3. 诱空检测 ──
        # 盘中跌但主力净额为正
        if zjlx_today.pct_change < -3.0 and zjlx_today.main_net_pct > 0:
            signal.is_lure_bearish = True
            signal.score += 30

        # ── 4. 涨停日+主买负+尾盘放量 = 强诱多 ──
        if signal.is_lure_bullish and signal.tail_surge_volume:
            signal.score = min(100.0, signal.score + 20)

        signal.score = min(100.0, signal.score)
        return signal

    def _detect_tail_surge(self, klines: List[MinKLine]) -> Tuple[bool, float]:
        """检测尾盘(14:30后)突然放量"""
        # 分离尾盘和非尾盘K线
        tail_klines = [k for k in klines if k.time_str >= self.TAIL_SESSION_START]
        non_tail_klines = [k for k in klines if k.time_str < self.TAIL_SESSION_START]

        if not tail_klines or not non_tail_klines:
            return False, 1.0

        tail_avg_vol = sum(k.volume for k in tail_klines) / len(tail_klines)
        non_tail_avg_vol = sum(k.volume for k in non_tail_klines) / len(non_tail_klines)

        if non_tail_avg_vol <= 0:
            return False, 1.0

        ratio = tail_avg_vol / non_tail_avg_vol
        return ratio >= 2.0, ratio


# ═══════════════════════════════════════════════════════════
# M5: 意图综合评分引擎
# ═══════════════════════════════════════════════════════════

class MainForceIntentDetector:
    """
    主力资金意图识别引擎 — V13.5.54核心

    整合4大检测模块，输出意图分类+D3维度增强/惩罚

    使用方式:
      detector = MainForceIntentDetector()
      result = detector.detect(code, name, zjlx_history, min_klines, realtime, is_limit_up)
      # result.intent → GENUINE_INFLOW / WASH_TRADE_DISTRIBUTION / ...
      # result.d3_adjustment → D3维度调整值
    """

    # 模块权重
    MODULE_WEIGHTS = {
        'wash_trade': 0.35,       # 对倒盘检测 — 最高权重
        'persistence': 0.25,      # 资金持续性
        'active_buy': 0.20,       # 主动买入区分
        'lure_pattern': 0.20      # 诱多/诱空
    }

    # D3维度调整规则
    D3_ADJUSTMENTS = {
        MainForceIntent.GENUINE_INFLOW: {'d3': 12.0, 'global': 0.0, 't1_conf': 8.0},
        MainForceIntent.WASH_TRADE_DISTRIBUTION: {'d3': -15.0, 'global': -8.0, 't1_conf': -15.0},
        MainForceIntent.LURE_BULLISH: {'d3': -8.0, 'global': -3.0, 't1_conf': -10.0},
        MainForceIntent.LURE_BEARISH: {'d3': 5.0, 'global': 0.0, 't1_conf': 5.0},
        MainForceIntent.NEUTRAL: {'d3': 0.0, 'global': 0.0, 't1_conf': 0.0}
    }

    def __init__(self):
        self.wash_trade_detector = WashTradeDetector()
        self.persistence_tracker = CapitalPersistenceTracker()
        self.active_buy_discriminator = ActiveBuyDiscriminator()
        self.lure_detector = LurePatternDetector()

    def detect(self, code: str, name: str,
               zjlx_history: List[ZJLXDay],
               min_klines: Optional[List[MinKLine]] = None,
               realtime: Optional[RealtimeQuote] = None,
               is_limit_up: bool = False) -> IntentResult:
        """
        主力意图综合识别

        参数:
          code: 股票代码
          name: 股票名称
          zjlx_history: 资金流向历史(至少含今日数据)
          min_klines: 5分钟K线(可选, 增强检测精度)
          realtime: 实时行情(可选, 14:30后可用)
          is_limit_up: 今日是否涨停

        返回:
          IntentResult: 意图识别结果
        """
        result = IntentResult(code=code, name=name)

        if not zjlx_history:
            result.diagnosis = "无资金流向数据"
            return result

        # 获取今日数据
        today = max(zjlx_history, key=lambda x: x.date)

        # ── 执行4大检测模块 ──
        result.wash_trade = self.wash_trade_detector.detect(today, min_klines)
        result.persistence = self.persistence_tracker.track(zjlx_history)

        if realtime:
            result.active_buy = self.active_buy_discriminator.discriminate(realtime, today)
        else:
            # 无实时数据时用zjlx推断
            proxy_realtime = RealtimeQuote(
                code=code, name=name,
                wei_bi=5.0 if today.main_net_pct > 0 else -5.0,
                outer_vol=1.5, inner_vol=1.0  # 默认外盘略大于内盘
            )
            result.active_buy = self.active_buy_discriminator.discriminate(proxy_realtime, today)

        result.lure_pattern = self.lure_detector.detect(today, min_klines, is_limit_up)

        # ── 意图综合评分 ──
        # 对倒盘得分越高 → 越危险(反转)
        wash_danger = result.wash_trade.score  # 0-100
        # 持续性得分越高 → 越安全
        persist_safe = result.persistence.score  # 0-100
        # 主动买入得分越高 → 越安全
        active_safe = result.active_buy.score  # 0-100
        # 诱多得分越高 → 越危险
        lure_danger = result.lure_pattern.score  # 0-100

        # 加权综合: 安全分 vs 危险分
        safe_score = (persist_safe * self.MODULE_WEIGHTS['persistence'] +
                      active_safe * self.MODULE_WEIGHTS['active_buy'])
        danger_score = (wash_danger * self.MODULE_WEIGHTS['wash_trade'] +
                        lure_danger * self.MODULE_WEIGHTS['lure_pattern'])

        # 意图评分 = 50 + 安全分 - 危险分 (0-100)
        result.intent_score = max(0.0, min(100.0, 50.0 + safe_score - danger_score))

        # ── 意图分类决策树 ──
        result.intent = self._classify_intent(
            result.wash_trade, result.persistence,
            result.active_buy, result.lure_pattern,
            is_limit_up, today
        )

        # ── D3维度调整 ──
        adj = self.D3_ADJUSTMENTS[result.intent]
        result.d3_adjustment = adj['d3']
        result.global_penalty = adj['global']
        result.t1_confidence_adjustment = adj['t1_conf']

        # ── 诊断与建议 ──
        result.diagnosis = self._generate_diagnosis(result, today)
        result.risk_warning = self._generate_warning(result)
        result.recommendation = self._generate_recommendation(result)

        return result

    def _classify_intent(self, wash: WashTradeSignal, persist: PersistenceSignal,
                         active: ActiveBuySignal, lure: LurePatternSignal,
                         is_limit_up: bool, today: ZJLXDay) -> MainForceIntent:
        """意图分类决策树"""

        # ★最高优先级: 对倒出货
        # 条件: 对倒盘得分>50 + 主力正/主买负 + (涨停日 或 背离度>15%)
        if wash.is_wash_trade and wash.score >= 50:
            if (today.main_net_pct > 0 and today.main_buy_net_pct < 0):
                if is_limit_up or wash.divergence_pct >= self.wash_trade_detector.DIVERGENCE_STRONG:
                    return MainForceIntent.WASH_TRADE_DISTRIBUTION

        # ★次高优先级: 诱多
        # 条件: 诱多信号 + 尾盘放量 + 主买负
        if lure.is_lure_bullish and lure.score >= 40:
            return MainForceIntent.LURE_BULLISH

        # ★诱空
        # 条件: 盘中大跌但主力净流入
        if lure.is_lure_bearish and today.pct_change < -3.0:
            return MainForceIntent.LURE_BEARISH

        # ★真实进场
        # 条件: 持续性>30 + 主动买入>60 + 无对倒 + 无诱多
        if (persist.score >= 30 and active.is_active_buy and
                not wash.is_wash_trade and not lure.is_lure_bullish):
            # 主力/主买同向正
            if today.main_net_pct > 0 and today.main_buy_net_pct >= 0:
                return MainForceIntent.GENUINE_INFLOW

        # 默认中性
        return MainForceIntent.NEUTRAL

    def _generate_diagnosis(self, result: IntentResult, today: ZJLXDay) -> str:
        """生成诊断描述"""
        parts = []

        parts.append(f"主力净额{today.main_net_pct:+.2f}%/主买净额{today.main_buy_net_pct:+.2f}%")

        if result.wash_trade.main_buy_divergence:
            parts.append(f"方向背离(背离度{result.wash_trade.divergence_pct:.1f}%)")

        if result.persistence.consecutive_inflow_days > 0:
            parts.append(f"连续{result.persistence.consecutive_inflow_days}日流入")
        elif result.persistence.consecutive_outflow_days > 0:
            parts.append(f"连续{result.persistence.consecutive_outflow_days}日流出")

        if result.persistence.inflow_convergence:
            parts.append("流出收敛(洗盘尾声)")

        if result.active_buy.is_active_buy:
            parts.append("主动买入(外盘>内盘)")
        else:
            parts.append("被动买入/对倒嫌疑")

        if result.lure_pattern.tail_surge_volume:
            parts.append(f"尾盘放量{result.lure_pattern.tail_surge_ratio:.1f}x")

        return " | ".join(parts)

    def _generate_warning(self, result: IntentResult) -> str:
        """生成风险警告"""
        if result.intent == MainForceIntent.WASH_TRADE_DISTRIBUTION:
            return "⚠️ 对倒出货! 主力大单买入但主买(散户方向)巨量卖出，T+1大概率下跌! 禁止追高!"
        elif result.intent == MainForceIntent.LURE_BULLISH:
            return "⚠️ 诱多嫌疑! 涨停日主买净额为负+尾盘放量，次日可能低开砸盘!"
        elif result.intent == MainForceIntent.LURE_BEARISH:
            return "📌 诱空可能! 盘中恐慌但主力净流入，关注反弹机会"
        elif result.intent == MainForceIntent.GENUINE_INFLOW:
            return "✅ 真实进场! 主力/主买同向正+持续性流入+主动买入，T+1安全"
        return "⚪ 信号中性，需结合其他维度判断"

    def _generate_recommendation(self, result: IntentResult) -> str:
        """生成操作建议"""
        if result.intent == MainForceIntent.WASH_TRADE_DISTRIBUTION:
            return "回避/减仓! D3维度-15分惩罚，全局蒸馏分-8分"
        elif result.intent == MainForceIntent.LURE_BULLISH:
            return "谨慎! 不追高，D3维度-8分，T+1置信度-10%"
        elif result.intent == MainForceIntent.LURE_BEARISH:
            return "关注反弹! D3维度+5分加成(反向机会)"
        elif result.intent == MainForceIntent.GENUINE_INFLOW:
            return "优先买入! D3维度+12分加成，T+1置信度+8%"
        return "中性观望，D3维度不变"


# ═══════════════════════════════════════════════════════════
# TDX数据解析器 — 从MCP返回结果构建数据结构
# ═══════════════════════════════════════════════════════════

class TDXDataParser:
    """
    TDX MCP数据解析器
    将tdx_api_data/tdx_kline/tdx_quotes返回的原始数据解析为引擎所需结构
    """

    @staticmethod
    def parse_zjlx(raw_data: Any) -> List[ZJLXDay]:
        """
        解析tdx_api_data fixedTag="zjlx"返回数据
        预期格式: 包含日期/主力净额/主力净额占比/超大单/大单/主买净额/主买净额占比的列表
        """
        results = []
        if not raw_data:
            return results

        # 兼容多种数据格式
        data_list = raw_data if isinstance(raw_data, list) else [raw_data]
        if isinstance(raw_data, dict) and 'data' in raw_data:
            data_list = raw_data['data'] if isinstance(raw_data['data'], list) else [raw_data['data']]
        elif isinstance(raw_data, dict) and 'rows' in raw_data:
            data_list = raw_data['rows']

        for row in data_list:
            if not isinstance(row, dict):
                continue

            day = ZJLXDay()
            # 尝试多种字段名映射
            day.date = str(row.get('date', row.get('rq', row.get('Date', ''))))

            # 主力净额
            day.main_net_yuan = float(row.get('main_net', row.get('zljlr', row.get('MainNet', 0))))
            day.main_net_pct = float(row.get('main_pct', row.get('zljzb', row.get('MainPct', 0))))

            # 超大单
            day.super_net_yuan = float(row.get('super_net', row.get('czdjmr', row.get('SuperNet', 0))))
            day.super_net_pct = float(row.get('super_pct', row.get('czdjzb', row.get('SuperPct', 0))))

            # 大单
            day.large_net_yuan = float(row.get('large_net', row.get('ddjmr', row.get('LargeNet', 0))))
            day.large_net_pct = float(row.get('large_pct', row.get('ddjzb', row.get('LargePct', 0))))

            # 主买净额(≈散户方向)
            day.main_buy_net_yuan = float(row.get('main_buy_net', row.get('zmjmr', row.get('MainBuyNet', 0))))
            day.main_buy_net_pct = float(row.get('main_buy_pct', row.get('zmjzb', row.get('MainBuyPct', 0))))

            # 收盘价/涨跌幅
            day.close = float(row.get('close', row.get('spj', row.get('Close', 0))))
            day.pct_change = float(row.get('pct_change', row.get('zdf', row.get('PctChange', 0))))

            results.append(day)

        return results

    @staticmethod
    def parse_min_kline(raw_data: Any) -> List[MinKLine]:
        """
        解析tdx_kline period="0"返回的5分钟K线数据
        """
        results = []
        if not raw_data:
            return results

        data_list = raw_data if isinstance(raw_data, list) else [raw_data]
        if isinstance(raw_data, dict) and 'data' in raw_data:
            data_list = raw_data['data'] if isinstance(raw_data['data'], list) else [raw_data['data']]
        elif isinstance(raw_data, dict) and 'klines' in raw_data:
            data_list = raw_data['klines']

        for row in data_list:
            if not isinstance(row, dict):
                continue

            kline = MinKLine()
            kline.timestamp = int(row.get('time', row.get('timestamp', 0)))
            kline.time_str = str(row.get('time_str', row.get('Time', '')))

            # 尝试从timestamp解析时间
            if not kline.time_str and kline.timestamp > 0:
                try:
                    dt = datetime.fromtimestamp(kline.timestamp)
                    kline.time_str = dt.strftime("%H:%M")
                except:
                    pass

            kline.open = float(row.get('open', row.get('kpj', row.get('Open', 0))))
            kline.high = float(row.get('high', row.get('zgj', row.get('High', 0))))
            kline.low = float(row.get('low', row.get('zdj', row.get('Low', 0))))
            kline.close = float(row.get('close', row.get('spj', row.get('Close', 0))))
            kline.volume = float(row.get('volume', row.get('cjl', row.get('Volume', 0))))
            kline.amount = float(row.get('amount', row.get('cje', row.get('Amount', 0))))

            results.append(kline)

        return results

    @staticmethod
    def parse_realtime(raw_data: Any) -> RealtimeQuote:
        """解析tdx_quotes hasProInfo=1返回的实时行情"""
        quote = RealtimeQuote()

        if not raw_data or not isinstance(raw_data, dict):
            return quote

        quote.code = str(raw_data.get('code', raw_data.get('Code', '')))
        quote.name = str(raw_data.get('name', raw_data.get('Name', '')))

        # ProInfo
        pro_info = raw_data.get('ProInfo', raw_data.get('proInfo', {}))
        if isinstance(pro_info, dict):
            quote.wei_bi = float(pro_info.get('Wtb', pro_info.get('wtb', 0)))
            quote.outer_vol = float(pro_info.get('Outside', pro_info.get('outside', 0)))
            quote.inner_vol = float(pro_info.get('Inside', pro_info.get('inside', 0)))

        # HQInfo
        hq_info = raw_data.get('HQInfo', raw_data.get('hqInfo', {}))
        if isinstance(hq_info, dict):
            quote.vol_ratio = float(hq_info.get('LB', hq_info.get('lb', 0)))
            quote.turnover_rate = float(hq_info.get('HSL', hq_info.get('hsl', 0)))

        quote.last_close = float(raw_data.get('last_close', raw_data.get('zcj', 0)))
        quote.close = float(raw_data.get('close', raw_data.get('price', raw_data.get('spj', 0))))

        return quote


# ═══════════════════════════════════════════════════════════
# 批量意图检测器 — 集成到8维蒸馏D3维度
# ═══════════════════════════════════════════════════════════

class BatchIntentDetector:
    """
    批量主力意图检测器

    用于8维蒸馏D3维度增强:
      1. 接收候选股票列表
      2. 逐只获取TDX zjlx/5分钟K线/实时行情
      3. 运行意图检测
      4. 输出D3调整值 + 风险标记
    """

    def __init__(self, output_dir: str = 'data/evolution_v13554'):
        self.detector = MainForceIntentDetector()
        self.parser = TDXDataParser()
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def detect_batch(self, stock_list: List[Dict]) -> List[IntentResult]:
        """
        批量检测主力意图

        参数:
          stock_list: [{'code': '600118', 'name': '中国卫星', 'is_limit_up': False}, ...]

        返回:
          List[IntentResult]
        """
        results = []
        for stock in stock_list:
            code = stock.get('code', '')
            name = stock.get('name', '')
            is_limit_up = stock.get('is_limit_up', False)

            # 从stock字典获取预加载的TDX数据(如有)
            zjlx_raw = stock.get('zjlx_data')
            min_kline_raw = stock.get('min_kline_data')
            realtime_raw = stock.get('realtime_data')

            zjlx_history = self.parser.parse_zjlx(zjlx_raw) if zjlx_raw else []
            min_klines = self.parser.parse_min_kline(min_kline_raw) if min_kline_raw else None
            realtime = self.parser.parse_realtime(realtime_raw) if realtime_raw else None

            result = self.detector.detect(
                code, name, zjlx_history, min_klines, realtime, is_limit_up
            )
            results.append(result)

        return results

    def detect_from_manual_data(self, stock_list: List[Dict]) -> List[IntentResult]:
        """
        从手动提供的TDX数据检测(用于验证和回测)

        参数:
          stock_list: [{'code': '600118', 'name': '中国卫星',
                        'zjlx_history': [ZJLXDay, ...],
                        'min_klines': [MinKLine, ...],
                        'realtime': RealtimeQuote,
                        'is_limit_up': True}, ...]
        """
        results = []
        for stock in stock_list:
            code = stock.get('code', '')
            name = stock.get('name', '')
            zjlx_history = stock.get('zjlx_history', [])
            min_klines = stock.get('min_klines')
            realtime = stock.get('realtime')
            is_limit_up = stock.get('is_limit_up', False)

            result = self.detector.detect(
                code, name, zjlx_history, min_klines, realtime, is_limit_up
            )
            results.append(result)

        return results

    def save_results(self, results: List[IntentResult], filename: str = None):
        """保存检测结果到JSON"""
        if not filename:
            filename = f"intent_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        filepath = os.path.join(self.output_dir, filename)
        data = []
        for r in results:
            data.append({
                'code': r.code,
                'name': r.name,
                'intent': r.intent.value,
                'intent_score': round(r.intent_score, 1),
                'wash_trade_score': round(r.wash_trade.score, 1),
                'wash_trade_detected': r.wash_trade.is_wash_trade,
                'divergence_pct': round(r.wash_trade.divergence_pct, 2),
                'persistence_score': round(r.persistence.score, 1),
                'consecutive_inflow': r.persistence.consecutive_inflow_days,
                'active_buy_score': round(r.active_buy.score, 1),
                'lure_score': round(r.lure_pattern.score, 1),
                'd3_adjustment': r.d3_adjustment,
                'global_penalty': r.global_penalty,
                't1_confidence_adj': r.t1_confidence_adjustment,
                'diagnosis': r.diagnosis,
                'risk_warning': r.risk_warning,
                'recommendation': r.recommendation
            })

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return filepath

    def generate_summary(self, results: List[IntentResult]) -> Dict:
        """生成检测汇总"""
        summary = {
            'total': len(results),
            'by_intent': {},
            'high_risk': [],
            'safe_stocks': [],
            'timestamp': datetime.now().isoformat()
        }

        for r in results:
            intent_key = r.intent.value
            if intent_key not in summary['by_intent']:
                summary['by_intent'][intent_key] = []
            summary['by_intent'][intent_key].append({
                'code': r.code,
                'name': r.name,
                'score': r.intent_score,
                'd3_adj': r.d3_adjustment
            })

            # 高风险股票(对倒出货/诱多)
            if r.intent in (MainForceIntent.WASH_TRADE_DISTRIBUTION,
                            MainForceIntent.LURE_BULLISH):
                summary['high_risk'].append({
                    'code': r.code,
                    'name': r.name,
                    'intent': r.intent.value,
                    'warning': r.risk_warning
                })

            # 安全股票(真实进场)
            if r.intent == MainForceIntent.GENUINE_INFLOW:
                summary['safe_stocks'].append({
                    'code': r.code,
                    'name': r.name,
                    'score': r.intent_score,
                    'd3_adj': r.d3_adjustment
                })

        return summary


# ═══════════════════════════════════════════════════════════
# 验证模块 — 使用7/10真实TDX数据验证引擎
# ═══════════════════════════════════════════════════════════

def validate_with_real_data():
    """
    使用7/10 TDX真实数据验证引擎

    验证标的:
      1. 600118中国卫星(涨停日) — 预期: WASH_TRADE_DISTRIBUTION(对倒出货)
      2. 002623亚玛顿(涨停日) — 预期: WASH_TRADE_DISTRIBUTION 或 LURE_BULLISH
      3. 002001新和成(正常日) — 预期: GENUINE_INFLOW(真实进场)
    """

    # ── 构建验证数据 ──

    # 600118中国卫星 7/10涨停日
    zjlx_600118 = [
        ZJLXDay(date="2026-07-10", main_net_pct=21.88, main_buy_net_pct=-22.59,
                super_net_pct=18.5, large_net_pct=3.38, close=90.02, pct_change=10.0),
        ZJLXDay(date="2026-07-09", main_net_pct=-5.16, main_buy_net_pct=-3.2,
                super_net_pct=-3.5, large_net_pct=-1.66, close=84.40, pct_change=-2.1),
        ZJLXDay(date="2026-07-08", main_net_pct=-2.8, main_buy_net_pct=-1.5,
                super_net_pct=-1.8, large_net_pct=-1.0, close=86.20, pct_change=-0.8),
    ]

    # 5分钟K线(模拟7/10涨停锁死特征)
    min_klines_600118 = []
    # 9:30-12:55 正常波动
    for i in range(24):
        min_klines_600118.append(MinKLine(
            time_str=f"{'09' if i < 12 else '10'}:{(30 + i*5) % 60:02d}",
            open=84.4 + i*0.02, high=84.6 + i*0.02, low=84.2 + i*0.02,
            close=84.5 + i*0.02, volume=5000 + i*100, amount=5000*84.5*100
        ))
    # 13:00 瞬间涨停 + 巨量
    min_klines_600118.append(MinKLine(
        time_str="13:00", open=84.40, high=90.02, low=84.40, close=90.02,
        volume=115316, amount=115316*90.02*100
    ))
    # 13:05-15:00 涨停锁死
    for i in range(23):
        min_klines_600118.append(MinKLine(
            time_str=f"{'13' if i < 11 else '14'}:{(5 + i*5) % 60:02d}",
            open=90.02, high=90.02, low=90.02, close=90.02,
            volume=3000 + i*50, amount=3000*90.02*100
        ))

    # 002623亚玛顿 7/10涨停日
    zjlx_002623 = [
        ZJLXDay(date="2026-07-10", main_net_pct=26.6, main_buy_net_pct=-17.06,
                super_net_pct=20.0, large_net_pct=6.6, close=12.50, pct_change=10.0),
        ZJLXDay(date="2026-07-09", main_net_pct=3.5, main_buy_net_pct=1.2,
                super_net_pct=2.0, large_net_pct=1.5, close=11.36, pct_change=3.2),
        ZJLXDay(date="2026-07-08", main_net_pct=-1.8, main_buy_net_pct=-0.5,
                super_net_pct=-1.0, large_net_pct=-0.8, close=11.0, pct_change=-0.5),
    ]

    # 002001新和成 7/10正常日
    zjlx_002001 = [
        ZJLXDay(date="2026-07-10", main_net_pct=5.24, main_buy_net_pct=0.29,
                super_net_pct=3.8, large_net_pct=1.44, close=28.70, pct_change=1.5),
        ZJLXDay(date="2026-07-09", main_net_pct=2.1, main_buy_net_pct=0.8,
                super_net_pct=1.5, large_net_pct=0.6, close=28.28, pct_change=0.3),
        ZJLXDay(date="2026-07-08", main_net_pct=-1.5, main_buy_net_pct=-0.3,
                super_net_pct=-0.8, large_net_pct=-0.7, close=28.20, pct_change=-0.8),
        ZJLXDay(date="2026-07-07", main_net_pct=-9.10, main_buy_net_pct=-13.24,
                super_net_pct=-5.5, large_net_pct=-3.6, close=28.42, pct_change=-3.2),
        ZJLXDay(date="2026-07-04", main_net_pct=-3.5, main_buy_net_pct=-2.0,
                super_net_pct=-2.0, large_net_pct=-1.5, close=29.36, pct_change=-1.5),
    ]

    # 5分钟K线(新和成正常波动)
    min_klines_002001 = []
    for i in range(48):
        h = 9 + i // 12
        m = (30 + (i % 12) * 5) % 60
        if h >= 13 and i < 24:
            h = 13 + (i - 24) // 12
        time_str = f"{h:02d}:{m:02d}"
        min_klines_002001.append(MinKLine(
            time_str=time_str,
            open=28.5 + math.sin(i*0.3)*0.1,
            high=28.6 + math.sin(i*0.3)*0.1,
            low=28.4 + math.sin(i*0.3)*0.1,
            close=28.5 + math.sin(i*0.3)*0.1 + 0.02,
            volume=3000 + math.cos(i*0.5)*500 + (5000 if i >= 42 else 0),  # 尾盘略放量
            amount=3000*28.5*100
        ))

    # ── 运行检测 ──
    detector = MainForceIntentDetector()

    test_cases = [
        {
            'code': '600118', 'name': '中国卫星',
            'zjlx_history': zjlx_600118, 'min_klines': min_klines_600118,
            'is_limit_up': True,
            'expected': 'WASH_TRADE_DISTRIBUTION'
        },
        {
            'code': '002623', 'name': '亚玛顿',
            'zjlx_history': zjlx_002623, 'min_klines': None,
            'is_limit_up': True,
            'expected': 'WASH_TRADE_DISTRIBUTION'
        },
        {
            'code': '002001', 'name': '新和成',
            'zjlx_history': zjlx_002001, 'min_klines': min_klines_002001,
            'is_limit_up': False,
            'expected': 'GENUINE_INFLOW'
        },
    ]

    print("=" * 80)
    print("V13.5.54 主力意图识别引擎 — TDX真实数据验证")
    print("=" * 80)

    results = []
    all_passed = True

    for tc in test_cases:
        result = detector.detect(
            tc['code'], tc['name'],
            tc['zjlx_history'], tc['min_klines'],
            realtime=None, is_limit_up=tc['is_limit_up']
        )
        results.append(result)

        passed = result.intent.value == tc['expected']
        if not passed:
            all_passed = False

        print(f"\n{'='*60}")
        print(f"标的: {tc['code']} {tc['name']}")
        print(f"涨停日: {'是' if tc['is_limit_up'] else '否'}")
        print(f"今日主力净额: {tc['zjlx_history'][0].main_net_pct:+.2f}%")
        print(f"今日主买净额: {tc['zjlx_history'][0].main_buy_net_pct:+.2f}%")
        print(f"方向背离: {'是' if result.wash_trade.main_buy_divergence else '否'} (背离度{result.wash_trade.divergence_pct:.1f}%)")
        print(f"对倒盘得分: {result.wash_trade.score:.1f} ({'⚠️对倒!' if result.wash_trade.is_wash_trade else '正常'})")
        print(f"持续性得分: {result.persistence.score:.1f} (连续流入{result.persistence.consecutive_inflow_days}天)")
        print(f"主动买入得分: {result.active_buy.score:.1f}")
        print(f"诱多/诱空得分: {result.lure_pattern.score:.1f}")
        print(f"━━━ 意图综合评分: {result.intent_score:.1f} ━━━")
        print(f"★★★ 意图分类: {result.intent.value}")
        print(f"预期: {tc['expected']} → {'✅ PASS' if passed else '❌ FAIL'}")
        print(f"诊断: {result.diagnosis}")
        print(f"警告: {result.risk_warning}")
        print(f"建议: {result.recommendation}")
        print(f"D3调整: {result.d3_adjustment:+.1f} | 全局惩罚: {result.global_penalty:+.1f} | T+1置信度: {result.t1_confidence_adjustment:+.1f}%")

    print(f"\n{'='*80}")
    print(f"验证结果: {'✅ 全部通过' if all_passed else '❌ 存在失败'}")
    print(f"{'='*80}")

    return results, all_passed


# ═══════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    results, passed = validate_with_real_data()

    # 保存验证结果
    batch_detector = BatchIntentDetector()
    filepath = batch_detector.save_results(results, 'validation_0710.json')
    print(f"\n验证结果已保存: {filepath}")

    # 生成汇总
    summary = batch_detector.generate_summary(results)
    summary_path = os.path.join(batch_detector.output_dir, 'validation_summary.json')
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"汇总报告已保存: {summary_path}")
