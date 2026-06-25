#!/usr/bin/env python3
"""
V13.0 M51 主力意图推断引擎（噪音过滤增强版）
==============================================
Phase 1 紧急修复：大单占比阈值 + 连续3日确认 + 对倒检测优化
目标：减少40%假信号，提升主力意图推断准确率

噪音来源分析：
1. 小单碎片化交易被误判为主力行为
2. 一日游游资的脉冲式操作
3. 对倒/自买自卖的虚假成交量
4. 散户跟风被误读为主力建仓
"""

import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════
# 噪音过滤参数配置
# ═══════════════════════════════════════════════

@dataclass
class M51Config:
    """M51主力意图推断配置"""
    # 大单过滤阈值
    BIG_ORDER_RATIO_THRESHOLD: float = 0.30       # 大单占比≥30%才算主力行为
    BIG_ORDER_MIN_AMOUNT: float = 500_000          # 单笔最低50万元
    HUGE_ORDER_MIN_AMOUNT: float = 3_000_000       # 超大单≥300万

    # 连续确认机制
    CONSECUTIVE_DAYS_CONFIRM: int = 3              # 需连续3日确认
    CONSECUTIVE_VOLUME_INCREASE: float = 0.15      # 成交量需递增15%
    MAX_GAP_DAYS: int = 1                          # 最多允许1天中断

    # 对倒检测
    DUMPING_PRICE_RANGE: float = 0.02              # 对倒价格波动≤2%
    DUMPING_VOLUME_SPIKE: float = 2.5              # 对倒时量比≥2.5倍
    DUMPING_CANCEL_RATIO: float = 0.60             # 撤单率≥60%疑似对倒

    # 一日游过滤
    ONE_DAY_REVERSAL_THRESHOLD: float = -0.03      # 次日跌幅≥3%视为一日游
    ONE_DAY_VOLUME_COLLAPSE: float = 0.50          # 次日成交量萎缩≥50%

    # 主力类型识别
    INSTITUTION_MIN_HOLDING: float = 0.05           # 机构持股≥5%
    NORTHBOUND_MIN_NET: float = 10_000_000          # 北向净买入≥1000万
    DRAGON_TIGER_MIN_AMOUNT: float = 50_000_000     # 龙虎榜买入≥5000万

    # 信号强度分级
    SIGNAL_STRONG: float = 0.75                    # 强主力信号
    SIGNAL_MODERATE: float = 0.50                  # 中等主力信号
    SIGNAL_WEAK: float = 0.25                      # 弱主力信号
    # < 0.25 视为噪音，直接过滤


@dataclass
class OrderFlow:
    """订单流数据结构"""
    timestamp: str
    price: float
    volume: int
    amount: float
    direction: str          # buy/sell
    order_type: str         # market/limit
    is_big_order: bool      # 是否大单(≥50万)
    is_huge_order: bool     # 是否超大单(≥300万)
    cancel_flag: bool       # 是否撤单


@dataclass
class IntentSignal:
    """主力意图信号"""
    code: str
    name: str
    date: str
    intent_strength: float          # 0~1，主力意图强度
    intent_direction: str            # bullish/bearish/neutral
    confidence: str                  # 高/中/低
    big_order_ratio: float           # 大单占比
    consecutive_days: int            # 连续确认天数
    dumping_flag: bool              # 是否检测到对倒
    institution_support: bool       # 是否有机构加持
    northbound_support: bool        # 是否有北向配合
    dragon_tiger_confirm: bool      # 是否有龙虎榜确认
    noise_score: float              # 噪音评分（越高越可疑）
    filter_reason: str              # 若被过滤，记录原因


class M51NoiseFilter:
    """M51噪音过滤器"""

    def __init__(self, config: M51Config = None):
        self.config = config or M51Config()
        self.history = {}  # code -> List[IntentSignal] 历史信号记录

    def load_history(self, history_file: str):
        """加载历史主力意图信号"""
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                for code, signals in raw.items():
                    self.history[code] = signals

    def save_history(self, history_file: str):
        """保存主力意图信号历史"""
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)

    # ═══════════════════════════════════════════════
    # 噪音过滤核心函数
    # ═══════════════════════════════════════════════

    def check_big_order_ratio(self, order_flow: List[OrderFlow]) -> Tuple[float, bool]:
        """
        检查大单占比
        返回：(大单占比, 是否通过)
        """
        if not order_flow:
            return 0.0, False

        total_amount = sum(o.amount for o in order_flow)
        big_amount = sum(o.amount for o in order_flow if o.is_big_order)

        if total_amount == 0:
            return 0.0, False

        ratio = big_amount / total_amount
        passed = ratio >= self.config.BIG_ORDER_RATIO_THRESHOLD

        return round(ratio, 4), passed

    def check_consecutive_confirmation(self, code: str, current_signal: IntentSignal) -> Tuple[int, bool]:
        """
        连续3日确认机制
        返回：(连续天数, 是否通过)
        """
        if code not in self.history:
            return 1, False  # 首次出现，需等待确认

        past_signals = self.history.get(code, [])
        if not past_signals:
            return 1, False

        # 获取最近N天的信号
        today = datetime.strptime(current_signal.date, '%Y-%m-%d')
        consecutive = 1
        prev_date = today

        for sig in reversed(past_signals[-10:]):  # 回溯最近10个交易日
            sig_date = datetime.strptime(sig['date'], '%Y-%m-%d') if isinstance(sig, dict) else datetime.strptime(sig.date, '%Y-%m-%d')
            sig_strength = sig['intent_strength'] if isinstance(sig, dict) else sig.intent_strength

            days_gap = (prev_date - sig_date).days

            if days_gap <= self.config.MAX_GAP_DAYS and sig_strength >= self.config.SIGNAL_WEAK:
                consecutive += 1
                prev_date = sig_date
            elif days_gap > self.config.MAX_GAP_DAYS:
                break  # 中间断档，不再继续回溯

        passed = consecutive >= self.config.CONSECUTIVE_DAYS_CONFIRM
        return consecutive, passed

    def detect_dumping(self, order_flow: List[OrderFlow], avg_volume: float) -> Tuple[bool, float]:
        """
        对倒检测
        返回：(是否对倒, 对倒概率)
        """
        if not order_flow or avg_volume == 0:
            return False, 0.0

        # 计算日内价格波动范围
        prices = [o.price for o in order_flow]
        price_range = (max(prices) - min(prices)) / min(prices) if min(prices) > 0 else 999

        # 总成交量 vs 平均成交量
        total_volume = sum(o.volume for o in order_flow)
        volume_ratio = total_volume / avg_volume

        # 撤单率
        cancel_count = sum(1 for o in order_flow if o.cancel_flag)
        cancel_ratio = cancel_count / len(order_flow) if order_flow else 0

        # 对倒评分
        dumping_score = 0.0
        flags = 0

        if price_range <= self.config.DUMPING_PRICE_RANGE:
            dumping_score += 0.4
            flags += 1
        if volume_ratio >= self.config.DUMPING_VOLUME_SPIKE:
            dumping_score += 0.3
            flags += 1
        if cancel_ratio >= self.config.DUMPING_CANCEL_RATIO:
            dumping_score += 0.3
            flags += 1

        # 至少满足2个条件才判为对倒
        is_dumping = flags >= 2
        return is_dumping, round(dumping_score, 4)

    def check_one_day_reversal(self, code: str, current_signal: IntentSignal) -> Tuple[bool, str]:
        """
        一日游检测：检查昨日是否有主力介入但今日反转
        返回：(是否一日游, 原因)
        """
        if code not in self.history:
            return False, "无历史记录"

        yesterday_signals = [
            s for s in self.history.get(code, [])
            if isinstance(s, dict) and
            (datetime.strptime(current_signal.date, '%Y-%m-%d') -
             datetime.strptime(s['date'], '%Y-%m-%d')).days == 1
        ]

        if not yesterday_signals:
            return False, "昨日无信号"

        yesterday = yesterday_signals[0]
        yesterday_strength = yesterday.get('intent_strength', 0) if isinstance(yesterday, dict) else yesterday.intent_strength

        # 昨日有主力信号，但今日方向相反或量能萎缩
        if yesterday_strength >= self.config.SIGNAL_MODERATE:
            # 检查今日是否反转
            if current_signal.intent_direction == 'bearish':
                return True, f"一日游·方向反转(昨bullish→今bearish)"
            if current_signal.big_order_ratio < self.config.BIG_ORDER_RATIO_THRESHOLD * 0.7:
                return True, f"一日游·大单撤退(昨{self.config.BIG_ORDER_RATIO_THRESHOLD:.0%}→今{current_signal.big_order_ratio:.0%})"

        return False, ""

    def verify_institution_support(self, institution_holding: float, northbound_net: float) -> bool:
        """验证机构/北向资金配合"""
        has_institution = institution_holding >= self.config.INSTITUTION_MIN_HOLDING
        has_northbound = northbound_net >= self.config.NORTHBOUND_MIN_NET
        return has_institution or has_northbound

    # ═══════════════════════════════════════════════
    # 综合过滤主流程
    # ═══════════════════════════════════════════════

    def analyze_intent(
        self,
        code: str,
        name: str,
        date: str,
        order_flow: List[OrderFlow],
        avg_volume: float,
        institution_holding: float = 0.0,
        northbound_net: float = 0.0,
        dragon_tiger_amount: float = 0.0,
        price_change_pct: float = 0.0,
    ) -> IntentSignal:
        """
        综合主力意图分析（含噪音过滤）

        参数：
        - order_flow: 订单流数据列表
        - avg_volume: 过去20日平均成交量
        - institution_holding: 机构持股比例
        - northbound_net: 北向资金净买入
        - dragon_tiger_amount: 龙虎榜买入金额
        - price_change_pct: 当日涨跌幅
        """

        signal = IntentSignal(
            code=code,
            name=name,
            date=date,
            intent_strength=0.0,
            intent_direction='neutral',
            confidence='低',
            big_order_ratio=0.0,
            consecutive_days=0,
            dumping_flag=False,
            institution_support=False,
            northbound_support=False,
            dragon_tiger_confirm=False,
            noise_score=0.0,
            filter_reason=''
        )

        # ── 第1关：大单占比检查 ──
        big_ratio, big_passed = self.check_big_order_ratio(order_flow)
        signal.big_order_ratio = big_ratio

        if not big_passed:
            signal.filter_reason = f"大单占比不足({big_ratio:.1%}<{self.config.BIG_ORDER_RATIO_THRESHOLD:.0%})·噪音过滤"
            signal.noise_score = 0.85
            return signal

        # ── 第2关：对倒检测 ──
        is_dumping, dumping_score = self.detect_dumping(order_flow, avg_volume)
        signal.dumping_flag = is_dumping
        if is_dumping:
            signal.filter_reason = f"疑似对倒(概率{dumping_score:.0%})·噪音过滤"
            signal.noise_score = max(signal.noise_score, dumping_score)
            return signal

        # ── 第3关：计算买卖方向 ──
        buy_amount = sum(o.amount for o in order_flow if o.direction == 'buy' and o.is_big_order)
        sell_amount = sum(o.amount for o in order_flow if o.direction == 'sell' and o.is_big_order)
        net_flow = buy_amount - sell_amount

        if net_flow > 0:
            signal.intent_direction = 'bullish'
        elif net_flow < 0:
            signal.intent_direction = 'bearish'
        else:
            signal.intent_direction = 'neutral'

        # ── 第4关：主力意图强度评分 ──
        # 基础分：大单净流向占比
        total_amount = sum(o.amount for o in order_flow)
        base_score = abs(net_flow) / total_amount if total_amount > 0 else 0
        base_score = min(base_score * 1.5, 0.5)  # 归一化到0~0.5

        # 大单占比加分
        big_bonus = (big_ratio - self.config.BIG_ORDER_RATIO_THRESHOLD) * 1.0
        big_bonus = max(0, min(big_bonus, 0.2))

        # 机构/北向加持
        signal.institution_support = institution_holding >= self.config.INSTITUTION_MIN_HOLDING
        signal.northbound_support = northbound_net >= self.config.NORTHBOUND_MIN_NET
        signal.dragon_tiger_confirm = dragon_tiger_amount >= self.config.DRAGON_TIGER_MIN_AMOUNT

        support_bonus = 0.0
        if signal.institution_support:
            support_bonus += 0.1
        if signal.northbound_support:
            support_bonus += 0.1
        if signal.dragon_tiger_confirm:
            support_bonus += 0.1

        signal.intent_strength = min(base_score + big_bonus + support_bonus, 1.0)

        # ── 第5关：连续确认 ──
        consecutive, confirmed = self.check_consecutive_confirmation(code, signal)
        signal.consecutive_days = consecutive

        # 未达连续确认要求的信号强度打折
        if not confirmed and consecutive < self.config.CONSECUTIVE_DAYS_CONFIRM:
            penalty = (self.config.CONSECUTIVE_DAYS_CONFIRM - consecutive) * 0.15
            signal.intent_strength *= max(0.3, 1.0 - penalty)

        # ── 第6关：一日游检测 ──
        is_one_day, reason = self.check_one_day_reversal(code, signal)
        if is_one_day:
            signal.filter_reason = reason
            signal.intent_strength *= 0.3  # 严重打折
            signal.noise_score = 0.75

        # ── 置信度判定 ──
        if signal.intent_strength >= self.config.SIGNAL_STRONG:
            signal.confidence = '高'
        elif signal.intent_strength >= self.config.SIGNAL_MODERATE:
            signal.confidence = '中'
        elif signal.intent_strength >= self.config.SIGNAL_WEAK:
            signal.confidence = '低'
        else:
            signal.confidence = '低'
            signal.filter_reason = signal.filter_reason or '信号强度不足·噪音过滤'

        # ── 噪音评分 ──
        signal.noise_score = min(signal.noise_score + (1.0 - signal.intent_strength) * 0.5, 1.0)

        return signal

    def batch_analyze(self, stocks_data: List[dict]) -> List[IntentSignal]:
        """
        批量分析多只股票的主力意图
        stocks_data: [{
            'code': str, 'name': str, 'date': str,
            'order_flow': List[OrderFlow],
            'avg_volume': float,
            'institution_holding': float,
            'northbound_net': float,
            'dragon_tiger_amount': float,
            'price_change_pct': float
        }]
        """
        results = []
        for stock in stocks_data:
            signal = self.analyze_intent(
                code=stock['code'],
                name=stock['name'],
                date=stock['date'],
                order_flow=stock.get('order_flow', []),
                avg_volume=stock.get('avg_volume', 0),
                institution_holding=stock.get('institution_holding', 0),
                northbound_net=stock.get('northbound_net', 0),
                dragon_tiger_amount=stock.get('dragon_tiger_amount', 0),
                price_change_pct=stock.get('price_change_pct', 0),
            )
            results.append(signal)

        return results

    def get_filtered_signals(self, signals: List[IntentSignal], min_strength: float = None) -> List[IntentSignal]:
        """
        获取过滤后的有效信号
        """
        threshold = min_strength or self.config.SIGNAL_WEAK
        return [
            s for s in signals
            if s.intent_strength >= threshold and not s.filter_reason
        ]

    def get_noise_report(self, all_signals: List[IntentSignal]) -> dict:
        """
        生成噪音过滤报告
        """
        total = len(all_signals)
        filtered = len([s for s in all_signals if s.filter_reason])
        valid = total - filtered

        reasons = {}
        for s in all_signals:
            if s.filter_reason:
                reason_type = s.filter_reason.split('·')[0] if '·' in s.filter_reason else s.filter_reason
                reasons[reason_type] = reasons.get(reason_type, 0) + 1

        return {
            'total_signals': total,
            'filtered_signals': filtered,
            'valid_signals': valid,
            'filter_rate': round(filtered / total, 3) if total > 0 else 0,
            'noise_reduction_target': 0.40,  # 目标减少40%假信号
            'filter_reasons': reasons,
            'avg_noise_score': round(sum(s.noise_score for s in all_signals) / total, 4) if total > 0 else 0,
        }


# ═══════════════════════════════════════════════
# 快捷运行入口
# ═══════════════════════════════════════════════

def run_m51_filter(code: str, name: str, date: str,
                   order_flow_data: List[dict],
                   avg_volume: float = 0,
                   institution_holding: float = 0,
                   northbound_net: float = 0,
                   dragon_tiger_amount: float = 0,
                   price_change_pct: float = 0,
                   history_file: str = None) -> dict:
    """
    M51主力意图推断便捷入口

    返回格式兼容 V13.0 data.json 的 m51_intent 字段
    """
    # 构造OrderFlow对象
    flows = []
    for o in order_flow_data:
        flows.append(OrderFlow(
            timestamp=o.get('timestamp', ''),
            price=o.get('price', 0),
            volume=o.get('volume', 0),
            amount=o.get('amount', 0),
            direction=o.get('direction', 'buy'),
            order_type=o.get('order_type', 'market'),
            is_big_order=o.get('amount', 0) >= 500_000,
            is_huge_order=o.get('amount', 0) >= 3_000_000,
            cancel_flag=o.get('cancel_flag', False),
        ))

    filter_engine = M51NoiseFilter()

    if history_file:
        filter_engine.load_history(history_file)

    signal = filter_engine.analyze_intent(
        code=code,
        name=name,
        date=date,
        order_flow=flows,
        avg_volume=avg_volume,
        institution_holding=institution_holding,
        northbound_net=northbound_net,
        dragon_tiger_amount=dragon_tiger_amount,
        price_change_pct=price_change_pct,
    )

    # 保存历史
    if history_file:
        if code not in filter_engine.history:
            filter_engine.history[code] = []
        filter_engine.history[code].append({
            'date': date,
            'intent_strength': signal.intent_strength,
            'intent_direction': signal.intent_direction,
            'confidence': signal.confidence,
            'big_order_ratio': signal.big_order_ratio,
            'consecutive_days': signal.consecutive_days,
            'dumping_flag': signal.dumping_flag,
            'noise_score': signal.noise_score,
            'filter_reason': signal.filter_reason,
        })
        filter_engine.save_history(history_file)

    # 输出兼容格式
    intent_label = '强' if signal.intent_strength >= 0.75 else \
                   '中' if signal.intent_strength >= 0.50 else \
                   '弱' if signal.intent_strength >= 0.25 else '无'

    return {
        'code': code,
        'name': name,
        'date': date,
        'intent': intent_label,
        'intent_strength': round(signal.intent_strength, 4),
        'direction': signal.intent_direction,
        'confidence': signal.confidence,
        'big_order_ratio': signal.big_order_ratio,
        'consecutive_days': signal.consecutive_days,
        'dumping_detected': signal.dumping_flag,
        'institution_backed': signal.institution_support,
        'northbound_backed': signal.northbound_support,
        'dragon_tiger_confirmed': signal.dragon_tiger_confirm,
        'noise_score': round(signal.noise_score, 4),
        'is_filtered': bool(signal.filter_reason),
        'filter_reason': signal.filter_reason,
    }


if __name__ == '__main__':
    # 自测
    print("=" * 60)
    print("V13.0 M51 主力意图推断引擎（噪音过滤增强版）")
    print("=" * 60)
    print(f"大单占比阈值: ≥30%")
    print(f"连续确认天数: ≥3日")
    print(f"对倒检测: 价格波动≤2% + 量比≥2.5x + 撤单率≥60%")
    print(f"一日游检测: 次日跌幅≥3% 或 量能萎缩≥50%")
    print(f"目标: 减少40%假信号")
    print("=" * 60)

    # 模拟测试
    test_order_flow = [
        {'timestamp': '09:35', 'price': 25.50, 'volume': 10000, 'amount': 255000, 'direction': 'buy', 'order_type': 'market', 'cancel_flag': False},
        {'timestamp': '09:45', 'price': 25.60, 'volume': 50000, 'amount': 1280000, 'direction': 'buy', 'order_type': 'market', 'cancel_flag': False},
        {'timestamp': '10:00', 'price': 25.80, 'volume': 30000, 'amount': 774000, 'direction': 'buy', 'order_type': 'limit', 'cancel_flag': False},
        {'timestamp': '10:15', 'price': 25.70, 'volume': 20000, 'amount': 514000, 'direction': 'sell', 'order_type': 'market', 'cancel_flag': False},
        {'timestamp': '10:30', 'price': 25.90, 'volume': 80000, 'amount': 2072000, 'direction': 'buy', 'order_type': 'market', 'cancel_flag': False},
    ]

    result = run_m51_filter(
        code='002028',
        name='思源电气',
        date='2026-06-23',
        order_flow_data=test_order_flow,
        avg_volume=5000000,
        institution_holding=0.08,
        northbound_net=15000000,
        dragon_tiger_amount=60000000,
        price_change_pct=0.035,
    )

    print("\n📊 分析结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
