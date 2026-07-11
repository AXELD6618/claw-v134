#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.32 D61 WINNER获利盘计算引擎
====================================
从K线OHLCV数据精确计算WINNER(获利盘比例)，使用筹码流转模型(Chip Flow Model)。

核心算法:
  1. 每日成交量按换手率对历史筹码进行衰减
  2. 新筹码按VWAP(成交额/成交量)进入成本分布
  3. WINNER = 成本<=当前价的筹码占比

关键指标:
  - WINNER_today:  今日收盘获利盘比例
  - WINNER_yesterday: 昨日收盘获利盘比例
  - WINNER_weekly:  近5日平均获利盘比例
  - WINNER_convergence: |WINNER_today - WINNER_weekly| 趋同度

筛选标准(V13.5.31 P11旁路):
  - 极致买点: WINNER_today ∈ [0.00%, 0.03%]
  - 可选买点: WINNER_today ∈ [0.00%, 2.00%]
  - 趋同确认: WINNER_yesterday ∈ [0.00%, 2.00%] 且 WINNER_weekly ∈ [0.00%, 2.00%]
  - 最优信号: 三者趋同度 < 0.5%

Author: V13.5.32 Claw System
Date: 2026-07-09
"""

import json
import math
from collections import defaultdict
from typing import List, Dict, Tuple, Optional


class WINNEREngine:
    """D61 WINNER获利盘计算引擎"""

    def __init__(self, bucket_size: float = 0.05):
        """
        Args:
            bucket_size: 价格分桶粒度(元)，默认0.05元
        """
        self.bucket_size = bucket_size

    def _price_to_bucket(self, price: float) -> int:
        """将价格归入最近的桶(整数索引, 避免浮点key问题)"""
        return int(round(price / self.bucket_size))

    def _bucket_to_price(self, bucket: int) -> float:
        """将桶索引转回价格"""
        return bucket * self.bucket_size

    def calc_chip_distribution(
        self,
        klines: List[Dict],
        total_shares: float
    ) -> Dict[int, float]:
        """
        计算筹码分布(Chip Distribution)

        使用筹码流转模型:
        - 每日换手率 = 成交量 / 流通股本
        - 旧筹码存活率 = 1 - 换手率
        - 新筹码按VWAP进入分布

        Args:
            klines: K线数据列表(旧→新), 每项含 high/low/close/volume/amount
            total_shares: 流通股本(股)

        Returns:
            {bucket_index: chip_count} 筹码分布(整数key避免浮点问题)
        """
        if total_shares <= 0 or not klines:
            return {}

        chips = defaultdict(float)  # bucket_index -> shares

        for bar in klines:
            # 成交量(股) - TDX volume单位为手(100股)
            vol_shares = bar.get('volume', 0) * 100

            if vol_shares <= 0:
                continue

            # 换手率
            turnover = min(vol_shares / total_shares, 0.99)

            # 旧筹码衰减
            survival = 1.0 - turnover
            if survival < 1.0:
                for p in list(chips.keys()):
                    chips[p] *= survival
                    if chips[p] < 0.01:
                        del chips[p]

            # VWAP(成交均价)
            amount = bar.get('amount', 0)
            if amount > 0:
                vwap = amount / vol_shares
            else:
                vwap = (bar.get('high', 0) + bar.get('low', 0) + bar.get('close', 0)) / 3

            # 将当日成交量按三角分布分散到[low, high]区间
            high = bar.get('high', vwap)
            low = bar.get('low', vwap)
            if high <= low:
                high = low + 0.01

            vwap_bucket = self._price_to_bucket(vwap)
            low_bucket = self._price_to_bucket(low)
            high_bucket = self._price_to_bucket(high)

            if high_bucket <= low_bucket:
                chips[vwap_bucket] += vol_shares
                continue

            # 三角分布权重(以VWAP为中心) - 使用整数bucket遍历
            total_weight = 0.0
            weights = []
            for b in range(low_bucket, high_bucket + 1):
                price = self._bucket_to_price(b)
                price_range = high - low
                if price_range > 0:
                    d = abs(price - vwap) / price_range
                else:
                    d = 0
                w = max(1.0 - d, 0.0)
                weights.append((b, w))
                total_weight += w

            # 归一化并添加到筹码分布
            if total_weight > 0:
                for bucket_idx, w in weights:
                    chips[bucket_idx] += vol_shares * w / total_weight

        return dict(chips)

    def calc_winner_from_chips(
        self,
        chips: Dict[int, float],
        current_price: float
    ) -> float:
        """从已有筹码分布计算WINNER(避免重复计算)"""
        total = sum(chips.values())
        if total <= 0:
            return 0.5

        price_bucket = self._price_to_bucket(current_price)
        winning = sum(v for b, v in chips.items() if b <= price_bucket)
        return winning / total

    def calc_winner(
        self,
        klines: List[Dict],
        current_price: float,
        total_shares: float
    ) -> float:
        """
        计算WINNER(获利盘比例)

        WINNER = 成本 <= current_price 的筹码占比
        """
        chips = self.calc_chip_distribution(klines, total_shares)
        return self.calc_winner_from_chips(chips, current_price)

    def calc_winner_series(
        self,
        klines: List[Dict],
        total_shares: float,
        lookback_days: int = 10
    ) -> Dict[str, float]:
        """
        计算WINNER时间序列: 今日/昨日/近5日均值/趋同度

        优化版: 使用增量更新筹码分布, 避免O(n^2)重复计算
        """
        if len(klines) < 2:
            return self._empty_result()

        # 增量构建筹码分布, 在每个时间点计算WINNER
        chips = defaultdict(float)
        daily_winners = []
        daily_closes = []

        for i, bar in enumerate(klines):
            vol_shares = bar.get('volume', 0) * 100
            if vol_shares > 0 and total_shares > 0:
                turnover = min(vol_shares / total_shares, 0.99)
                survival = 1.0 - turnover

                if survival < 1.0:
                    for p in list(chips.keys()):
                        chips[p] *= survival
                        if chips[p] < 0.01:
                            del chips[p]

                amount = bar.get('amount', 0)
                if amount > 0:
                    vwap = amount / vol_shares
                else:
                    vwap = (bar.get('high', 0) + bar.get('low', 0) + bar.get('close', 0)) / 3

                high = bar.get('high', vwap)
                low = bar.get('low', vwap)
                if high <= low:
                    high = low + 0.01

                vwap_bucket = self._price_to_bucket(vwap)
                low_bucket = self._price_to_bucket(low)
                high_bucket = self._price_to_bucket(high)

                if high_bucket <= low_bucket:
                    chips[vwap_bucket] += vol_shares
                else:
                    total_weight = 0.0
                    weights = []
                    for b in range(low_bucket, high_bucket + 1):
                        price = self._bucket_to_price(b)
                        price_range = high - low
                        d = abs(price - vwap) / price_range if price_range > 0 else 0
                        w = max(1.0 - d, 0.0)
                        weights.append((b, w))
                        total_weight += w
                    if total_weight > 0:
                        for bucket_idx, w in weights:
                            chips[bucket_idx] += vol_shares * w / total_weight

            # 在每个交易日收盘后计算WINNER
            close = bar.get('close', 0)
            daily_closes.append(close)
            if i >= 1:  # 至少需要2根K线
                w = self.calc_winner_from_chips(dict(chips), close)
                daily_winners.append(w)

        if not daily_winners:
            return self._empty_result()

        winner_today = daily_winners[-1]
        winner_yesterday = daily_winners[-2] if len(daily_winners) >= 2 else winner_today

        # 近5日WINNER均值
        recent_5 = daily_winners[-5:] if len(daily_winners) >= 5 else daily_winners
        winner_weekly_avg = sum(recent_5) / len(recent_5)

        # 5日前WINNER
        winner_5d_ago = daily_winners[-6] if len(daily_winners) >= 6 else winner_yesterday

        today_close = daily_closes[-1] if daily_closes else 0
        yesterday_close = daily_closes[-2] if len(daily_closes) >= 2 else today_close

        # 趋同度
        convergence = abs(winner_today - winner_weekly_avg)

        # 信号分级
        is_extreme = winner_today <= 0.0003  # 0.03%
        is_acceptable = winner_today <= 0.02   # 2%
        is_converged = (
            winner_yesterday <= 0.02 and
            winner_weekly_avg <= 0.02 and
            convergence < 0.005  # 0.5%
        )

        if is_extreme and is_converged:
            grade = "EXTREME_CONVERGED"  # 最强信号
        elif is_extreme:
            grade = "EXTREME"
        elif is_acceptable and is_converged:
            grade = "ACCEPTABLE_CONVERGED"
        elif is_acceptable:
            grade = "ACCEPTABLE"
        elif winner_today <= 0.10:
            grade = "LOW"
        else:
            grade = "NORMAL"

        return {
            'winner_today': winner_today,
            'winner_yesterday': winner_yesterday,
            'winner_weekly_avg': winner_weekly_avg,
            'winner_5d_ago': winner_5d_ago,
            'convergence': convergence,
            'is_extreme': is_extreme,
            'is_acceptable': is_acceptable,
            'is_converged': is_converged,
            'signal_grade': grade,
            'today_close': today_close,
            'yesterday_close': yesterday_close,
        }

    def _empty_result(self) -> Dict:
        return {
            'winner_today': 0.5,
            'winner_yesterday': 0.5,
            'winner_weekly_avg': 0.5,
            'winner_5d_ago': 0.5,
            'convergence': 0.0,
            'is_extreme': False,
            'is_acceptable': False,
            'is_converged': False,
            'signal_grade': 'NO_DATA',
            'today_close': 0,
            'yesterday_close': 0,
        }

    def calc_scr_proxy(
        self,
        klines: List[Dict],
        total_shares: float
    ) -> Dict[str, float]:
        """
        计算SCR筹码集中度代理指标(从筹码分布推导)

        SCR ≈ 90%筹码的价格区间宽度 / 均价

        Returns:
            {
                'scr_proxy': float,      # SCR代理值
                'price_90_low': float,   # 90%筹码下沿
                'price_90_high': float,  # 90%筹码上沿
                'price_center': float,   # 筹码中心价(加权均价)
                'concentration': float,  # 集中度(0-1, 越高越集中)
            }
        """
        chips = self.calc_chip_distribution(klines, total_shares)
        total = sum(chips.values())

        if total <= 0:
            return {'scr_proxy': 99, 'price_90_low': 0, 'price_90_high': 0,
                    'price_center': 0, 'concentration': 0}

        # 按bucket索引排序(整数key, 无浮点问题)
        sorted_buckets = sorted(chips.keys())
        
        # 筹码中心价(加权均价) - 使用bucket索引计算
        price_center_bucket = sum(b * v for b, v in chips.items()) / total
        price_center = self._bucket_to_price(price_center_bucket)

        # 计算90%筹码区间 (5th ~ 95th percentile)
        cum = 0.0
        p5_bucket = sorted_buckets[0]
        p95_bucket = sorted_buckets[-1]
        p5_found = False
        for b in sorted_buckets:
            cum += chips[b]
            ratio = cum / total
            if not p5_found and ratio >= 0.05:
                p5_bucket = b
                p5_found = True
            if ratio >= 0.95:
                p95_bucket = b
                break

        p5 = self._bucket_to_price(p5_bucket)
        p95 = self._bucket_to_price(p95_bucket)

        scr_proxy = (p95 - p5) / price_center * 100 if price_center > 0 else 99
        concentration = 1.0 - min(scr_proxy / 50, 1.0)  # SCR<50时concentration较高

        return {
            'scr_proxy': scr_proxy,
            'price_90_low': p5,
            'price_90_high': p95,
            'price_center': price_center,
            'concentration': concentration,
        }


def parse_tdx_kline(raw_data) -> List[Dict]:
    """
    解析TDX K线API返回数据为标准格式

    支持多种格式:
    - 列表格式: [[time, open, high, low, close, volume, amount], ...]
    - 字典格式: {'data': [...], 'columns': [...]}
    - JSON字符串
    """
    if isinstance(raw_data, str):
        try:
            raw_data = json.loads(raw_data)
        except:
            return []

    klines = []

    # 尝试多种格式
    if isinstance(raw_data, dict):
        # 可能有 'data' 或 'klines' 或 'rows' 键
        data = raw_data.get('data') or raw_data.get('klines') or raw_data.get('rows') or []
        columns = raw_data.get('columns') or raw_data.get('fields') or []
    elif isinstance(raw_data, list):
        data = raw_data
        columns = []
    else:
        return []

    for item in data:
        if isinstance(item, dict):
            # 字典格式
            klines.append({
                'time': item.get('time') or item.get('date') or item.get('日期'),
                'open': float(item.get('open') or item.get('开盘') or 0),
                'high': float(item.get('high') or item.get('最高') or 0),
                'low': float(item.get('low') or item.get('最低') or 0),
                'close': float(item.get('close') or item.get('收盘') or 0),
                'volume': float(item.get('volume') or item.get('成交量') or item.get('vol') or 0),
                'amount': float(item.get('amount') or item.get('成交额') or item.get('amt') or 0),
            })
        elif isinstance(item, (list, tuple)):
            # 列表格式
            if len(item) >= 6:
                klines.append({
                    'time': item[0],
                    'open': float(item[1]),
                    'high': float(item[2]),
                    'low': float(item[3]),
                    'close': float(item[4]),
                    'volume': float(item[5]),
                    'amount': float(item[6]) if len(item) > 6 else 0,
                })

    return klines


def analyze_stock(
    klines: List[Dict],
    total_shares: float,
    code: str = "",
    name: str = ""
) -> Dict:
    """
    对单只股票进行完整WINNER+SCR分析

    Args:
        klines: K线数据(旧→新)
        total_shares: 流通股本(股)
        code: 股票代码
        name: 股票名称

    Returns:
        完整分析结果字典
    """
    engine = WINNEREngine()

    winner_info = engine.calc_winner_series(klines, total_shares)
    scr_info = engine.calc_scr_proxy(klines, total_shares)

    # 综合评分
    score = 0

    # WINNER评分 (40分)
    wt = winner_info['winner_today']
    if wt <= 0.0003:  # 0.03%
        score += 40
    elif wt <= 0.001:  # 0.1%
        score += 35
    elif wt <= 0.005:  # 0.5%
        score += 30
    elif wt <= 0.01:   # 1%
        score += 25
    elif wt <= 0.02:   # 2%
        score += 20
    elif wt <= 0.05:   # 5%
        score += 10
    elif wt <= 0.10:   # 10%
        score += 5

    # 趋同度评分 (20分)
    if winner_info['is_converged']:
        score += 20
    elif winner_info['convergence'] < 0.01:
        score += 15
    elif winner_info['convergence'] < 0.02:
        score += 10
    elif winner_info['convergence'] < 0.05:
        score += 5

    # SCR筹码集中度评分 (25分)
    scr = scr_info['scr_proxy']
    if scr < 5:
        score += 25
    elif scr < 8:
        score += 20
    elif scr < 10:
        score += 15
    elif scr < 15:
        score += 10
    elif scr < 20:
        score += 5

    # 筹码中心价偏离度 (15分)
    # 当前价低于筹码中心价越多(套牢越多)，未来上涨阻力越小
    today_close = winner_info['today_close']
    center = scr_info['price_center']
    if center > 0 and today_close > 0:
        deviation = (center - today_close) / center  # 正值=套牢
        if deviation > 0.10:
            score += 15
        elif deviation > 0.05:
            score += 12
        elif deviation > 0.02:
            score += 8
        elif deviation > 0:
            score += 5
        elif deviation > -0.02:
            score += 3  # 略高于成本

    # 双信号检测
    d60_signal = scr < 10  # SCR高度集中
    d61_signal = wt <= 0.02  # WINNER极低
    dual_signal = d60_signal and d61_signal

    return {
        'code': code,
        'name': name,
        'total_shares': total_shares,
        'today_close': today_close,
        'winner_today_pct': wt * 100,
        'winner_yesterday_pct': winner_info['winner_yesterday'] * 100,
        'winner_weekly_avg_pct': winner_info['winner_weekly_avg'] * 100,
        'winner_5d_ago_pct': winner_info['winner_5d_ago'] * 100,
        'convergence_pct': winner_info['convergence'] * 100,
        'signal_grade': winner_info['signal_grade'],
        'is_extreme': winner_info['is_extreme'],
        'is_acceptable': winner_info['is_acceptable'],
        'is_converged': winner_info['is_converged'],
        'scr_proxy': scr,
        'price_90_low': scr_info['price_90_low'],
        'price_90_high': scr_info['price_90_high'],
        'price_center': center,
        'concentration': scr_info['concentration'],
        'd60_signal': d60_signal,
        'd61_signal': d61_signal,
        'dual_signal': dual_signal,
        'total_score': score,
    }


def main():
    """命令行入口: 读取K线JSON文件并计算WINNER"""
    import sys

    if len(sys.argv) < 3:
        print("Usage: python V13_5_32_WINNER_Engine.py <kline_json_file> <total_shares> [code] [name]")
        print("Example: python V13_5_32_WINNER_Engine.py kline_603726.json 82740000 603726 朗迪集团")
        sys.exit(1)

    kline_file = sys.argv[1]
    total_shares = float(sys.argv[2])
    code = sys.argv[3] if len(sys.argv) > 3 else ""
    name = sys.argv[4] if len(sys.argv) > 4 else ""

    with open(kline_file, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    klines = parse_tdx_kline(raw)

    if not klines:
        print(f"ERROR: No kline data parsed from {kline_file}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"V13.5.32 D61 WINNER Engine - {code} {name}")
    print(f"{'='*60}")
    print(f"K线数据: {len(klines)} 根")
    print(f"流通股本: {total_shares:,.0f} 股")
    print()

    result = analyze_stock(klines, total_shares, code, name)

    print(f"今日收盘: {result['today_close']:.2f}")
    print(f"筹码中心: {result['price_center']:.2f}")
    print(f"90%筹码区间: [{result['price_90_low']:.2f}, {result['price_90_high']:.2f}]")
    print()
    print(f"WINNER今日:     {result['winner_today_pct']:.4f}%")
    print(f"WINNER昨日:     {result['winner_yesterday_pct']:.4f}%")
    print(f"WINNER周均:     {result['winner_weekly_avg_pct']:.4f}%")
    print(f"WINNER5日前:    {result['winner_5d_ago_pct']:.4f}%")
    print(f"趋同度:         {result['convergence_pct']:.4f}%")
    print()
    print(f"SCR代理:        {result['scr_proxy']:.2f}")
    print(f"集中度:         {result['concentration']:.2%}")
    print()
    print(f"信号等级:       {result['signal_grade']}")
    print(f"D60 SCR信号:    {'YES' if result['d60_signal'] else 'NO'}")
    print(f"D61 WINNER信号: {'YES' if result['d61_signal'] else 'NO'}")
    print(f"双信号:         {'*** YES ***' if result['dual_signal'] else 'NO'}")
    print(f"综合评分:       {result['total_score']}/100")
    print()

    # JSON输出
    print("JSON_RESULT_START")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("JSON_RESULT_END")


if __name__ == '__main__':
    main()
