#!/usr/bin/env python3
"""
V13.0 逆向选股策略引擎
=======================
Phase 2 能力跃升：独立策略线——超跌+低拥挤+基本面反转

核心思想：
当市场情绪极度悲观时，优质资产被错杀，形成"逆向买入"机会。
本模块致力于在别人恐惧时贪婪，系统化逆向选股逻辑。

三因子框架：
1. 超跌因子（Price Crash）——识别过度下跌的错杀标的
2. 低拥挤因子（Low Crowding）——避开机构扎堆的拥挤标的
3. 基本面反转因子（Fundamental Reversal）——寻找业绩拐点信号

筛选流程：
超跌扫描 → 拥挤度过滤 → 基本面验证 → 逆向信号生成
"""

import json
import math
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class ReverseSelectionConfig:
    """逆向选股配置"""

    # 超跌因子
    crash_threshold: float = -0.30        # 60日跌幅 > 30%
    crash_severe: float = -0.50           # 60日跌幅 > 50%（极度超跌）
    crash_short_term: float = -0.10       # 近5日跌幅 > 10%（加速赶底）

    # 低拥挤因子
    institution_holding_max: float = 0.05   # 机构持股 < 5%
    fund_position_min: float = 0.01        # 基金持仓 > 1%最低（至少有点机构关注）
    northbound_selling_reversal: float = 0  # 北向由卖转买信号

    # 基本面反转
    pe_max: float = 50.0                   # 市盈率 < 50（避免亏损股）
    pb_min: float = 0.5                    # 市净率 > 0.5（避免破产风险股）
    revenue_growth_min: float = 0.05       # 营收增速 > 5%（基本面不差）
    profit_turning: bool = True            # 利润拐点（QoQ改善）
    cash_flow_positive: bool = True        # 经营现金流为正

    # 技术面辅助
    volume_drying: bool = True             # 缩量止跌（成交量萎缩至20日均量60%以下）
    rsi_oversold: float = 30.0             # RSI < 30（超卖）
    macd_divergence: bool = True           # MACD底背离（价格新低但MACD未新低）

    # 加权评分
    weight_crash: float = 0.35             # 超跌权重
    weight_crowding: float = 0.25          # 拥挤度权重
    weight_fundamental: float = 0.40       # 基本面权重


@dataclass
class ReverseSignal:
    """逆向选股信号"""
    code: str
    name: str
    industry: str
    date: str

    # 三因子得分
    crash_score: float          # 超跌得分（0~1，越高越超跌）
    crowding_score: float       # 拥挤度得分（0~1，越低越好，1=完全不拥挤）
    fundamental_score: float    # 基本面得分（0~1，越高越好）

    # 综合得分
    total_score: float          # 加权总分
    signal_strength: str        # 强/中/弱

    # 技术确认
    rsi: float = 50
    volume_ratio: float = 1.0   # 当前量/20日均量
    macd_divergence: bool = False

    # 估值
    pe: float = 0
    pb: float = 0

    # 60日跌幅
    decline_60d: float = 0.0

    # 操作建议
    entry_suggestion: str = ''
    stop_loss_pct: float = -0.08


class ReverseSelectionEngine:
    """逆向选股引擎"""

    def __init__(self, config: ReverseSelectionConfig = None):
        self.config = config or ReverseSelectionConfig()
        self.signal_history: List[ReverseSignal] = []

    # ═══════════════════════════════════════════════
    # 因子1：超跌因子
    # ═══════════════════════════════════════════════

    def score_crash(self, decline_60d: float, decline_5d: float,
                    decline_20d: float) -> Tuple[float, str]:
        """
        评估超跌程度
        返回：(得分0~1, 诊断)
        """
        score = 0.0
        reasons = []

        # 60日跌幅
        if decline_60d <= self.config.crash_severe:
            score += 0.50
            reasons.append(f'60日跌{abs(decline_60d):.0%}·极度超跌')
        elif decline_60d <= self.config.crash_threshold:
            score += 0.35
            reasons.append(f'60日跌{abs(decline_60d):.0%}·超跌区间')
        elif decline_60d <= -0.20:
            score += 0.20
            reasons.append(f'60日跌{abs(decline_60d):.0%}·下跌中')

        # 5日加速赶底
        if decline_5d <= self.config.crash_short_term:
            score += 0.25
            reasons.append(f'近5日跌{abs(decline_5d):.0%}·加速赶底')
        elif decline_5d <= -0.05:
            score += 0.10
            reasons.append(f'近5日跌{abs(decline_5d):.0%}')

        # 20日跌幅辅助
        if decline_20d <= -0.15:
            score += 0.15
            reasons.append(f'20日跌幅{abs(decline_20d):.0%}')

        # 严重连续下跌加分
        if decline_60d <= -0.30 and decline_5d <= -0.08:
            score += 0.10
            reasons.append('连续超跌·恐慌性抛售')

        return min(score, 1.0), ' | '.join(reasons) if reasons else '未超跌'

    # ═══════════════════════════════════════════════
    # 因子2：低拥挤因子
    # ═══════════════════════════════════════════════

    def score_crowding(self, institution_holding: float,
                       fund_position_change: float,
                       northbound_net_change: float,
                       analyst_coverage: int) -> Tuple[float, str]:
        """
        评估拥挤度（越低越好）
        返回：(得分0~1，越高=越不拥挤，诊断)
        """
        score = 1.0  # 初始满分（完全不拥挤）
        reasons = []

        # 机构持股（越低越好）
        if institution_holding > 0.15:
            score -= 0.25
            reasons.append(f'机构持股{institution_holding:.0%}·偏拥挤')
        elif institution_holding > 0.08:
            score -= 0.10
            reasons.append(f'机构持股{institution_holding:.0%}')
        elif institution_holding <= self.config.institution_holding_max:
            score += 0.10  # 加分
            reasons.append(f'机构持股{institution_holding:.0%}·非拥挤')

        # 基金减仓（我们逆势买入的好时机）
        if fund_position_change < -0.02:
            score += 0.10
            reasons.append('基金减仓·逆向机会')
        elif fund_position_change > 0.03:
            score -= 0.15
            reasons.append('基金加仓·可能追高')

        # 北向资金
        if northbound_net_change > 0:
            score += 0.05
            reasons.append('北向开始回流·确认信号')

        # 分析师覆盖（覆盖少=关注低=不拥挤）
        if analyst_coverage <= 3:
            score += 0.08
            reasons.append(f'仅{analyst_coverage}家覆盖·低关注')

        return max(0.0, min(score, 1.0)), ' | '.join(reasons) if reasons else '正常'

    # ═══════════════════════════════════════════════
    # 因子3：基本面反转因子
    # ═══════════════════════════════════════════════

    def score_fundamental(self, pe: float, pb: float,
                          revenue_growth: float, profit_growth_qoq: float,
                          gross_margin_change: float, operating_cf: float,
                          debt_to_assets: float) -> Tuple[float, str]:
        """
        评估基本面反转可能性
        返回：(得分0~1, 诊断)
        """
        score = 0.0
        reasons = []

        # 估值合理
        if 0 < pe <= 20:
            score += 0.20
            reasons.append(f'PE={pe:.1f}·估值合理')
        elif 0 < pe <= 35:
            score += 0.12
            reasons.append(f'PE={pe:.1f}')
        elif pe <= 0:
            reasons.append('PE为负·需注意亏损风险')
        else:
            score += 0.05

        if 0.5 < pb <= 2.0:
            score += 0.12
            reasons.append(f'PB={pb:.1f}·低估值')
        elif 0 < pb <= 0.5:
            score -= 0.08
            reasons.append(f'PB={pb:.1f}·破产折价风险')

        # 营收增长
        if revenue_growth >= 0.15:
            score += 0.18
            reasons.append(f'营收增速{revenue_growth:.0%}·成长性佳')
        elif revenue_growth >= 0.05:
            score += 0.10
            reasons.append(f'营收增速{revenue_growth:.0%}')

        # 利润拐点（QoQ改善）
        if profit_growth_qoq > 0.10:
            score += 0.20
            reasons.append(f'利润QoQ+{profit_growth_qoq:.0%}·拐点确认')
        elif profit_growth_qoq > 0:
            score += 0.10
            reasons.append('利润环比改善')

        # 毛利率稳定/改善
        if gross_margin_change > 0:
            score += 0.10
            reasons.append('毛利率改善')

        # 现金流
        if operating_cf > 0:
            score += 0.12
            reasons.append('经营现金流为正')

        # 债务可控
        if debt_to_assets < 0.50:
            score += 0.08
            reasons.append('负债率可控')

        return min(score, 1.0), ' | '.join(reasons) if reasons else '基本面一般'

    # ═══════════════════════════════════════════════
    # 技术确认
    # ═══════════════════════════════════════════════

    def technical_confirm(self, rsi: float, volume_ratio: float,
                          macd_histogram: List[float]) -> Tuple[float, bool]:
        """
        技术面确认
        返回：(技术确认得分, 是否MACD底背离)
        """
        tech_score = 0.0
        macd_div = False

        # RSI超卖
        if rsi <= 25:
            tech_score += 0.35
        elif rsi <= 35:
            tech_score += 0.20
        elif rsi <= 45:
            tech_score += 0.10

        # 缩量止跌
        if volume_ratio <= 0.60:
            tech_score += 0.30
        elif volume_ratio <= 0.80:
            tech_score += 0.15

        # MACD底背离检测
        if len(macd_histogram) >= 20:
            recent_macd = macd_histogram[-5:]
            earlier_macd = macd_histogram[-20:-15]
            avg_recent = sum(recent_macd) / len(recent_macd) if recent_macd else 0
            avg_earlier = sum(earlier_macd) / len(earlier_macd) if earlier_macd else 0

            if avg_recent > avg_earlier:  # MACD柱状线走高（底背离特征）
                macd_div = True
                tech_score += 0.20

        return min(tech_score, 0.75), macd_div

    # ═══════════════════════════════════════════════
    # 综合评分
    # ═══════════════════════════════════════════════

    def evaluate(self, code: str, name: str, industry: str,
                 # 超跌数据
                 decline_60d: float, decline_20d: float, decline_5d: float,
                 # 拥挤度数据
                 institution_holding: float, fund_position_change: float,
                 northbound_net_change: float, analyst_coverage: int,
                 # 基本面数据
                 pe: float, pb: float, revenue_growth: float,
                 profit_growth_qoq: float, gross_margin_change: float,
                 operating_cf: float, debt_to_assets: float,
                 # 技术面
                 rsi: float, volume_ratio: float,
                 macd_histogram: List[float] = None,
                 ) -> ReverseSignal:
        """
        综合逆向选股评估
        """
        macd_histogram = macd_histogram or []

        # 各因子独立评分
        crash_score, crash_diag = self.score_crash(decline_60d, decline_5d, decline_20d)
        crowding_score, crowd_diag = self.score_crowding(
            institution_holding, fund_position_change, northbound_net_change, analyst_coverage)
        fundamental_score, fund_diag = self.score_fundamental(
            pe, pb, revenue_growth, profit_growth_qoq, gross_margin_change, operating_cf, debt_to_assets)

        # 技术确认
        tech_score, macd_div = self.technical_confirm(rsi, volume_ratio, macd_histogram)

        # 加权综合
        total_score = (
            self.config.weight_crash * crash_score +
            self.config.weight_crowding * crowding_score +
            self.config.weight_fundamental * fundamental_score
        )

        # 技术确认加权：技术确认强→总得分+bonus
        total_score += tech_score * 0.15
        total_score = min(total_score, 1.0)

        # 信号强度分档
        if total_score >= 0.70:
            signal_strength = '强'
            entry = '分批建仓，首仓30%'
        elif total_score >= 0.55:
            signal_strength = '中'
            entry = '轻仓试探，首仓15%'
        elif total_score >= 0.40:
            signal_strength = '弱'
            entry = '加入观察列表，等待确认'
        else:
            signal_strength = '无'
            entry = '不符合逆向买入条件'

        # 止损位（基于60日超跌程度动态设定）
        if decline_60d <= -0.40:
            stop_loss = -0.10  # 极度超跌放宽止损
        elif decline_60d <= -0.30:
            stop_loss = -0.08
        else:
            stop_loss = -0.06

        signal = ReverseSignal(
            code=code,
            name=name,
            industry=industry,
            date=datetime.now().strftime('%Y-%m-%d'),
            crash_score=round(crash_score, 4),
            crowding_score=round(crowding_score, 4),
            fundamental_score=round(fundamental_score, 4),
            total_score=round(total_score, 4),
            signal_strength=signal_strength,
            rsi=rsi,
            volume_ratio=volume_ratio,
            macd_divergence=macd_div,
            pe=pe,
            pb=pb,
            decline_60d=decline_60d,
            entry_suggestion=entry,
            stop_loss_pct=stop_loss,
        )

        self.signal_history.append(signal)
        return signal

    def batch_scan(self, stocks: List[dict]) -> List[ReverseSignal]:
        """
        批量逆向扫描
        stocks: [{code, name, industry, decline_60d, decline_20d, ...}]
        返回：按总分降序排列的信号列表
        """
        results = []
        for s in stocks:
            signal = self.evaluate(
                code=s.get('code', ''),
                name=s.get('name', ''),
                industry=s.get('industry', '通用'),
                decline_60d=s.get('decline_60d', 0),
                decline_20d=s.get('decline_20d', 0),
                decline_5d=s.get('decline_5d', 0),
                institution_holding=s.get('institution_holding', 0),
                fund_position_change=s.get('fund_position_change', 0),
                northbound_net_change=s.get('northbound_net_change', 0),
                analyst_coverage=s.get('analyst_coverage', 10),
                pe=s.get('pe', 0),
                pb=s.get('pb', 0),
                revenue_growth=s.get('revenue_growth', 0),
                profit_growth_qoq=s.get('profit_growth_qoq', 0),
                gross_margin_change=s.get('gross_margin_change', 0),
                operating_cf=s.get('operating_cf', 0),
                debt_to_assets=s.get('debt_to_assets', 0),
                rsi=s.get('rsi', 50),
                volume_ratio=s.get('volume_ratio', 1.0),
                macd_histogram=s.get('macd_histogram', []),
            )
            results.append(signal)

        # 按总分降序
        results.sort(key=lambda x: x.total_score, reverse=True)
        return results

    def get_actionable_signals(self, signals: List[ReverseSignal],
                               min_strength: str = '中') -> List[ReverseSignal]:
        """
        获取可操作的逆向信号
        """
        strength_order = {'强': 0, '中': 1, '弱': 2, '无': 3}
        return [
            s for s in signals
            if strength_order.get(s.signal_strength, 99) <= strength_order.get(min_strength, 1)
        ]


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def run_reverse_selection(stocks_data: List[dict]) -> dict:
    """
    逆向选股快捷入口
    """
    engine = ReverseSelectionEngine()
    signals = engine.batch_scan(stocks_data)
    actionable = engine.get_actionable_signals(signals, '中')

    return {
        'total_scanned': len(stocks_data),
        'total_signals': len(signals),
        'actionable_signals': len(actionable),
        'top_picks': [
            {
                'code': s.code,
                'name': s.name,
                'industry': s.industry,
                'total_score': s.total_score,
                'signal_strength': s.signal_strength,
                'decline_60d': round(s.decline_60d * 100, 1),
                'crash_score': s.crash_score,
                'crowding_score': s.crowding_score,
                'fundamental_score': s.fundamental_score,
                'entry_suggestion': s.entry_suggestion,
                'stop_loss_pct': round(s.stop_loss_pct * 100, 1),
            }
            for s in actionable[:5]
        ],
        'all_results': [
            {
                'code': s.code,
                'name': s.name,
                'total_score': s.total_score,
                'signal_strength': s.signal_strength,
            }
            for s in signals[:20]
        ],
    }


if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 逆向选股策略引擎")
    print("=" * 60)
    print("三因子：超跌(35%) + 低拥挤(25%) + 基本面反转(40%)")
    print("=" * 60)

    test_stocks = [
        {
            'code': '002028', 'name': '思源电气', 'industry': '特高压/电力设备',
            'decline_60d': -0.35, 'decline_20d': -0.15, 'decline_5d': -0.12,
            'institution_holding': 0.04, 'fund_position_change': -0.03,
            'northbound_net_change': 5000000, 'analyst_coverage': 8,
            'pe': 18.5, 'pb': 2.1, 'revenue_growth': 0.15,
            'profit_growth_qoq': 0.20, 'gross_margin_change': 0.02,
            'operating_cf': 500_000_000, 'debt_to_assets': 0.35,
            'rsi': 28, 'volume_ratio': 0.55,
            'macd_histogram': [-0.5, -0.4, -0.3, -0.2, -0.1] + [0.0] * 5 + [0.1] * 5 + [0.15] * 5,
        },
        {
            'code': '300750', 'name': '宁德时代', 'industry': '新能源/储能',
            'decline_60d': -0.18, 'decline_20d': -0.08, 'decline_5d': -0.03,
            'institution_holding': 0.18, 'fund_position_change': 0.05,
            'northbound_net_change': -20000000, 'analyst_coverage': 35,
            'pe': 22.0, 'pb': 3.5, 'revenue_growth': 0.25,
            'profit_growth_qoq': 0.15, 'gross_margin_change': -0.01,
            'operating_cf': 10_000_000_000, 'debt_to_assets': 0.55,
            'rsi': 48, 'volume_ratio': 1.1,
            'macd_histogram': [0.1] * 20,
        },
    ]

    result = run_reverse_selection(test_stocks)
    print(f"\n📊 逆向选股结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
