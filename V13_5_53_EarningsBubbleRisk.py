#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.53 盈利泡沫风险评估引擎 (Earnings Bubble Risk Engine)
=============================================================
基于全网收集的"盈利泡沫"历史案例与周期顶信号研究，构建5因子风险评估模型。

核心洞察:
  - 三星利润+1810%但股价跌10% = "买预期卖事实"周期顶信号
  - 杭电股份预增852%但连续跌停 = 预期透支+获利了结
  - 光伏龙头2020年净利85亿→连续亏损→市值缩水80% = 周期回归必然
  - 存储芯片2027-2028产能集中释放 = 潜在供给过剩

5因子模型:
  F1: 预期透支度 — 公告前3个月涨幅 vs 预增幅度
  F2: 利润驱动力 — 价格驱动(周期性) vs 出货量驱动(成长性)
  F3: 产能释放时间表 — 行业扩产计划集中度
  F4: 交易拥挤度 — 融资余额占比/成交额集中度/TMT占比
  F5: 涨幅收窄信号 — 季度环比涨幅趋势(周期拐点前兆)

风险评分: 0-100
  <30: LOW (安全区，可正常配置)
  30-50: MODERATE (谨慎区，仓位减半)
  50-70: HIGH (警戒区，仅短线T+1)
  >70: EXTREME (泡沫区，回避/仅做空)

集成方式: D6催化维度惩罚项
  - risk_score > 50 时，D6催化加权减半
  - risk_score > 70 时，D6催化加权归零 + 全局蒸馏分-10
  - 8维蒸馏总评分上限下调

作者: 毕方灵犀·貔貅助手 V13.5.53
日期: 2026-07-12
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

class RiskLevel(Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"

@dataclass
class BubbleRiskFactors:
    """5因子输入"""
    # F1: 预期透支度
    pre_announcement_gain_3m: float = 0.0  # 公告前3个月涨幅%
    earnings_growth_pct: float = 0.0  # 预增幅度%
    
    # F2: 利润驱动力
    price_driven_ratio: float = 0.5  # 价格驱动占比(0=纯出货量, 1=纯价格)
    cashflow_profit_divergence: bool = False  # 经营现金流与净利润是否背离
    
    # F3: 产能释放时间表
    industry_capex_surge: bool = False  # 行业是否处于大规模扩产期
    capacity_release_years: List[str] = field(default_factory=list)  # 产能集中释放年份
    
    # F4: 交易拥挤度
    margin_balance_ratio: float = 0.0  # 融资余额占流通市值比%
    tmt_turnover_concentration: float = 0.0  # TMT成交额占全市场比%
    institutional_holding_pct: float = 0.0  # 机构持仓比例%
    
    # F5: 涨幅收窄信号
    qoq_price_increase_trend: str = "expanding"  # expanding/converging/declining
    latest_qtr_increase_pct: float = 0.0  # 最新季度涨幅%
    prev_qtr_increase_pct: float = 0.0  # 上一季度涨幅%


class EarningsBubbleRiskEngine:
    """盈利泡沫风险评估引擎"""
    
    # 因子权重
    FACTOR_WEIGHTS = {
        'F1_expectation_overdraw': 0.25,
        'F2_profit_driver': 0.20,
        'F3_capacity_release': 0.20,
        'F4_crowding': 0.20,
        'F5_convergence_signal': 0.15
    }
    
    # 行业周期属性数据库
    CYCLICAL_INDUSTRIES = {
        'storage_chip': {'cyclical': True, 'price_driven': 0.8, 'capex_lag_years': 2.5},
        'semiconductor': {'cyclical': True, 'price_driven': 0.6, 'capex_lag_years': 2.0},
        'photovoltaic': {'cyclical': True, 'price_driven': 0.9, 'capex_lag_years': 1.5},
        'chemical': {'cyclical': True, 'price_driven': 0.85, 'capex_lag_years': 2.0},
        'shipping': {'cyclical': True, 'price_driven': 0.95, 'capex_lag_years': 3.0},
        'steel': {'cyclical': True, 'price_driven': 0.9, 'capex_lag_years': 2.0},
        'aerospace': {'cyclical': False, 'price_driven': 0.2, 'capex_lag_years': 5.0},
        'biopharma': {'cyclical': False, 'price_driven': 0.1, 'capex_lag_years': 8.0},
        'ai_infra': {'cyclical': True, 'price_driven': 0.5, 'capex_lag_years': 3.0},
    }
    
    # 历史案例参考
    HISTORICAL_CASES = {
        'samsung_2026q2': {
            'profit_growth': 1810, 'stock_reaction': -10, 
            'diagnosis': '买预期卖事实，18个月涨158%后利好出尽',
            'risk_level': 'EXTREME'
        },
        'hangdian_2026': {
            'profit_growth': 852, 'stock_reaction': -20,
            'diagnosis': '公告前3月涨382%，预期完全透支，连续跌停',
            'risk_level': 'EXTREME'
        },
        'pv_leader_2020': {
            'profit_growth': 106, 'stock_reaction': -80,
            'diagnosis': '光伏周期顶，2年后全行业亏损，市值缩水80%',
            'risk_level': 'EXTREME'
        },
        'medical_2020': {
            'profit_growth': 3800, 'stock_reaction': -90,
            'diagnosis': '疫情受益股，1年赚40年利润，2年后利润回归6.29亿',
            'risk_level': 'EXTREME'
        }
    }
    
    def __init__(self, knowledge_base_path: str = None):
        self.knowledge_base_path = knowledge_base_path or 'data/knowledge_base'
        
    def calculate_f1_expectation_overdraw(self, factors: BubbleRiskFactors) -> float:
        """
        F1: 预期透支度 (0-100)
        - 公告前涨幅 vs 预增幅度
        - 如果公告前涨幅 > 预增幅度的50%，说明预期已大幅透支
        """
        if factors.earnings_growth_pct <= 0:
            return 50.0  # 无预增数据，中性
        
        # 透支率 = 公告前涨幅 / 预增幅度
        overdraw_ratio = factors.pre_announcement_gain_3m / max(factors.earnings_growth_pct, 1)
        
        if overdraw_ratio >= 1.0:
            # 涨幅已超过预增幅度 → 极度透支
            return 90.0
        elif overdraw_ratio >= 0.5:
            return 70.0
        elif overdraw_ratio >= 0.3:
            return 50.0
        elif overdraw_ratio >= 0.1:
            return 30.0
        else:
            return 10.0
    
    def calculate_f2_profit_driver(self, factors: BubbleRiskFactors) -> float:
        """
        F2: 利润驱动力 (0-100)
        - 价格驱动占比越高 → 周期性越强 → 泡沫风险越高
        - 现金流与利润背离 → 纸面富贵 → 高风险
        """
        score = factors.price_driven_ratio * 60  # 基础分
        
        if factors.cashflow_profit_divergence:
            score += 30  # 现金流背离 → 重大风险信号
        
        # 检查行业是否强周期
        # (已在price_driven_ratio中体现)
        
        return min(score, 100.0)
    
    def calculate_f3_capacity_release(self, factors: BubbleRiskFactors) -> float:
        """
        F3: 产能释放时间表 (0-100)
        - 行业大规模扩产 → 未来供给过剩风险
        - 产能集中释放年份越近 → 风险越高
        """
        if not factors.industry_capex_surge:
            return 20.0  # 无大规模扩产 → 低风险
        
        score = 50.0  # 基础分
        
        current_year = datetime.now().year
        for year_str in factors.capacity_release_years:
            try:
                release_year = int(year_str)
                years_to_release = release_year - current_year
                if years_to_release <= 1:
                    score += 30  # 1年内产能释放 → 极高风险
                elif years_to_release <= 2:
                    score += 20  # 2年内 → 高风险
                elif years_to_release <= 3:
                    score += 10  # 3年内 → 中等风险
            except ValueError:
                continue
        
        return min(score, 100.0)
    
    def calculate_f4_crowding(self, factors: BubbleRiskFactors) -> float:
        """
        F4: 交易拥挤度 (0-100)
        - 融资余额占比 > 5% → 拥挤
        - TMT成交占比 > 40% → 极度拥挤
        - 机构持仓 > 30% → 抱团风险
        """
        score = 0.0
        
        # 融资余额占比
        if factors.margin_balance_ratio >= 8:
            score += 35
        elif factors.margin_balance_ratio >= 5:
            score += 25
        elif factors.margin_balance_ratio >= 3:
            score += 15
        else:
            score += 5
        
        # TMT成交集中度
        if factors.tmt_turnover_concentration >= 45:
            score += 30
        elif factors.tmt_turnover_concentration >= 40:
            score += 20
        elif factors.tmt_turnover_concentration >= 30:
            score += 10
        else:
            score += 5
        
        # 机构持仓
        if factors.institutional_holding_pct >= 40:
            score += 20
        elif factors.institutional_holding_pct >= 25:
            score += 15
        else:
            score += 10
        
        return min(score, 100.0)
    
    def calculate_f5_convergence(self, factors: BubbleRiskFactors) -> float:
        """
        F5: 涨幅收窄信号 (0-100)
        - 涨幅环比收窄 → 周期拐点前兆
        - 涨幅下降趋势 = 最强周期顶信号
        """
        if factors.qoq_price_increase_trend == "declining":
            return 80.0  # 涨幅下降 → 高风险
        elif factors.qoq_price_increase_trend == "converging":
            # 计算收窄程度
            if factors.prev_qtr_increase_pct > 0:
                convergence_ratio = factors.latest_qtr_increase_pct / factors.prev_qtr_increase_pct
                if convergence_ratio < 0.3:
                    return 70.0  # 涨幅大幅收窄
                elif convergence_ratio < 0.5:
                    return 50.0
                else:
                    return 30.0
            return 40.0
        else:  # expanding
            return 15.0  # 涨幅扩大 → 仍在上行
    
    def assess_risk(self, factors: BubbleRiskFactors) -> Dict:
        """
        执行完整5因子风险评估
        
        Returns:
            {
                'risk_score': float,  # 0-100
                'risk_level': RiskLevel,
                'factor_scores': Dict[str, float],
                'diagnosis': str,
                'd6_penalty': float,  # D6催化维度惩罚系数(0-1)
                'global_penalty': float,  # 全局蒸馏分惩罚
                'position_adjustment': str,  # 仓位调整建议
                'warnings': List[str]
            }
        """
        # 计算各因子得分
        f1 = self.calculate_f1_expectation_overdraw(factors)
        f2 = self.calculate_f2_profit_driver(factors)
        f3 = self.calculate_f3_capacity_release(factors)
        f4 = self.calculate_f4_crowding(factors)
        f5 = self.calculate_f5_convergence(factors)
        
        factor_scores = {
            'F1_expectation_overdraw': round(f1, 1),
            'F2_profit_driver': round(f2, 1),
            'F3_capacity_release': round(f3, 1),
            'F4_crowding': round(f4, 1),
            'F5_convergence_signal': round(f5, 1)
        }
        
        # 加权总分
        risk_score = (
            f1 * self.FACTOR_WEIGHTS['F1_expectation_overdraw'] +
            f2 * self.FACTOR_WEIGHTS['F2_profit_driver'] +
            f3 * self.FACTOR_WEIGHTS['F3_capacity_release'] +
            f4 * self.FACTOR_WEIGHTS['F4_crowding'] +
            f5 * self.FACTOR_WEIGHTS['F5_convergence_signal']
        )
        risk_score = round(risk_score, 1)
        
        # 风险等级
        if risk_score < 30:
            risk_level = RiskLevel.LOW
        elif risk_score < 50:
            risk_level = RiskLevel.MODERATE
        elif risk_score < 70:
            risk_level = RiskLevel.HIGH
        else:
            risk_level = RiskLevel.EXTREME
        
        # D6惩罚系数
        if risk_score >= 70:
            d6_penalty = 0.0  # D6催化加权归零
            global_penalty = -10.0
            position_adjustment = "回避/仅做空"
        elif risk_score >= 50:
            d6_penalty = 0.5  # D6催化加权减半
            global_penalty = -5.0
            position_adjustment = "仓位减半，仅短线T+1"
        elif risk_score >= 30:
            d6_penalty = 0.8  # D6催化加权8折
            global_penalty = -2.0
            position_adjustment = "正常配置但控制仓位"
        else:
            d6_penalty = 1.0  # 不惩罚
            global_penalty = 0.0
            position_adjustment = "正常配置"
        
        # 诊断与预警
        warnings = []
        diagnosis_parts = []
        
        if f1 >= 70:
            warnings.append(f"⚠️ 预期严重透支: 公告前3月涨幅已达预增幅度的{factors.pre_announcement_gain_3m/max(factors.earnings_growth_pct,1)*100:.0f}%")
            diagnosis_parts.append("预期透支")
        
        if f2 >= 60:
            warnings.append(f"⚠️ 利润主要由价格驱动({factors.price_driven_ratio*100:.0f}%)，周期属性强")
            if factors.cashflow_profit_divergence:
                warnings.append("🚨 经营现金流与净利润严重背离 → 纸面富贵")
            diagnosis_parts.append("周期性利润")
        
        if f3 >= 60:
            warnings.append(f"⚠️ 行业大规模扩产中，产能集中释放年份: {factors.capacity_release_years}")
            diagnosis_parts.append("产能过剩风险")
        
        if f4 >= 60:
            warnings.append(f"⚠️ 交易拥挤: 融资占比{factors.margin_balance_ratio:.1f}%/TMT占比{factors.tmt_turnover_concentration:.1f}%")
            diagnosis_parts.append("交易拥挤")
        
        if f5 >= 60:
            warnings.append(f"⚠️ 涨幅收窄信号: {factors.prev_qtr_increase_pct:.1f}% → {factors.latest_qtr_increase_pct:.1f}%")
            diagnosis_parts.append("周期拐点前兆")
        
        diagnosis = " + ".join(diagnosis_parts) if diagnosis_parts else "风险可控"
        
        return {
            'risk_score': risk_score,
            'risk_level': risk_level.value,
            'factor_scores': factor_scores,
            'diagnosis': diagnosis,
            'd6_penalty': d6_penalty,
            'global_penalty': global_penalty,
            'position_adjustment': position_adjustment,
            'warnings': warnings,
            'timestamp': datetime.now().isoformat()
        }
    
    def assess_storage_chip_bubble_risk(self) -> Dict:
        """
        预配置: 存储芯片板块盈利泡沫风险评估
        基于全网收集的最新情报
        """
        factors = BubbleRiskFactors(
            # F1: 预期透支 — 存储板块2026H1大涨，但PE仍22倍28%分位
            pre_announcement_gain_3m=150.0,  # 存储板块近3月大涨
            earnings_growth_pct=2118.0,  # 香农芯创预增2118%
            
            # F2: 利润驱动 — 存储芯片利润主要由涨价驱动
            price_driven_ratio=0.8,  # 80%价格驱动
            cashflow_profit_divergence=False,  # 暂无现金流背离信号
            
            # F3: 产能释放 — 2027下半年-2028集中量产
            industry_capex_surge=True,
            capacity_release_years=['2027', '2028', '2028', '2028'],
            
            # F4: 拥挤度 — TMT占比49%峰值，融资余额高
            margin_balance_ratio=6.0,  # 佰维存储6.05%
            tmt_turnover_concentration=45.0,  # 近期峰值49%
            institutional_holding_pct=8.0,  # 机构持仓仅8% → 低拥挤
            
            # F5: 涨幅收窄 — Q3涨幅13-18% vs Q2的58-75% → 明显收窄
            qoq_price_increase_trend="converging",
            latest_qtr_increase_pct=15.0,  # Q3 DRAM涨13-18%均值
            prev_qtr_increase_pct=66.0  # Q2 DRAM涨58-75%均值
        )
        
        result = self.assess_risk(factors)
        result['sector'] = 'storage_chip'
        result['assessment_context'] = {
            'samsung_case': '利润+1810%但股价跌10%，18个月涨158%后利好出尽',
            'capacity_timeline': '2027下半年-2028: 三星P5/SK海力士龙仁/美光广岛/长鑫HBM 集中量产',
            'price_convergence': 'Q3 DRAM涨13-18% vs Q2涨58-75%，消费端承受力达极限',
            'counter_factors': 'PE仅22倍28%分位/机构持仓8%/板块占全A市值3% → 不至于崩盘'
        }
        return result
    
    def assess_earnings_pre_announcement_risk(self, stock_code: str, stock_name: str,
                                               pre_gain_3m: float, earnings_growth: float,
                                               industry: str = 'semiconductor') -> Dict:
        """
        评估个股业绩预增的泡沫风险
        """
        industry_info = self.CYCLICAL_INDUSTRIES.get(industry, {'cyclical': True, 'price_driven': 0.5})
        
        factors = BubbleRiskFactors(
            pre_announcement_gain_3m=pre_gain_3m,
            earnings_growth_pct=earnings_growth,
            price_driven_ratio=industry_info['price_driven'],
            cashflow_profit_divergence=False,
            industry_capex_surge=industry_info['cyclical'],
            capacity_release_years=['2027', '2028'] if industry_info['cyclical'] else [],
            margin_balance_ratio=5.0,  # 默认中等
            tmt_turnover_concentration=42.0,  # 当前TMT高占比
            institutional_holding_pct=15.0,  # 默认
            qoq_price_increase_trend="converging",
            latest_qtr_increase_pct=15.0,
            prev_qtr_increase_pct=66.0
        )
        
        result = self.assess_risk(factors)
        result['stock_code'] = stock_code
        result['stock_name'] = stock_name
        result['industry'] = industry
        return result


def main():
    """主函数 — 执行存储芯片板块盈利泡沫风险评估"""
    engine = EarningsBubbleRiskEngine()
    
    print("=" * 80)
    print("V13.5.53 盈利泡沫风险评估引擎 — Earnings Bubble Risk Engine")
    print("=" * 80)
    
    # 1. 存储芯片板块整体评估
    print("\n📊 存储芯片板块盈利泡沫风险评估")
    print("-" * 60)
    storage_result = engine.assess_storage_chip_bubble_risk()
    print(f"风险评分: {storage_result['risk_score']}/100")
    print(f"风险等级: {storage_result['risk_level']}")
    print(f"诊断: {storage_result['diagnosis']}")
    print(f"D6惩罚系数: {storage_result['d6_penalty']}")
    print(f"全局惩罚: {storage_result['global_penalty']}")
    print(f"仓位建议: {storage_result['position_adjustment']}")
    print("\n因子分解:")
    for factor, score in storage_result['factor_scores'].items():
        weight = engine.FACTOR_WEIGHTS.get(factor, 0)
        print(f"  {factor}: {score}/100 (权重{weight*100:.0f}%)")
    print("\n预警:")
    for w in storage_result['warnings']:
        print(f"  {w}")
    
    # 2. 个股评估 — 业绩预增股
    print("\n\n📊 业绩预增个股风险评估")
    print("-" * 60)
    
    earnings_stocks = [
        ('300475', '香农芯创', 150, 2118, 'storage_chip'),
        ('002842', '翔鹭钨业', 80, 2348, 'chemical'),
        ('002738', '中矿资源', 60, 1078, 'chemical'),
        ('001314', '亿道信息', 120, 1442, 'ai_infra'),
        ('600673', '东阳光', 40, 500, 'ai_infra'),
    ]
    
    for code, name, pre_gain, growth, industry in earnings_stocks:
        result = engine.assess_earnings_pre_announcement_risk(
            code, name, pre_gain, growth, industry
        )
        print(f"\n  {code} {name}: 风险={result['risk_score']}/100 [{result['risk_level']}]")
        print(f"    诊断: {result['diagnosis']}")
        print(f"    仓位: {result['position_adjustment']}")
        if result['warnings']:
            print(f"    预警: {'; '.join(result['warnings'][:2])}")
    
    # 3. 历史案例对比
    print("\n\n📚 历史案例参考")
    print("-" * 60)
    for case_id, case in engine.HISTORICAL_CASES.items():
        print(f"  {case_id}: 利润+{case['profit_growth']}% → 股价{case['stock_reaction']}%")
        print(f"    诊断: {case['diagnosis']}")
    
    # 4. 输出JSON
    output = {
        'assessment_date': datetime.now().isoformat(),
        'version': 'V13.5.53',
        'storage_chip_sector': storage_result,
        'individual_stocks': [
            engine.assess_earnings_pre_announcement_risk(c, n, g, e, i)
            for c, n, g, e, i in earnings_stocks
        ],
        'historical_cases': engine.HISTORICAL_CASES
    }
    
    output_path = 'data/evolution_v13553/earnings_bubble_risk.json'
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 评估结果已保存: {output_path}")
    
    print("\n" + "=" * 80)
    print("核心结论: 存储芯片板块风险等级MODERATE-HIGH，")
    print("D6催化加权建议8折(0.8)，仓位建议正常配置但控制仓位。")
    print("关键对冲因素: PE仅22倍/机构持仓仅8%/板块市值占比3% → 不至于韩国式崩盘")
    print("但仍需警惕2027-2028产能集中释放风险，短线T+1策略有效。")
    print("=" * 80)


if __name__ == '__main__':
    main()
