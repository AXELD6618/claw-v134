#!/usr/bin/env python3
"""
V13.0 黑名单 + 财务暴雷预警系统
================================
Phase 1 紧急修复：踩雷率从8%降至≤5%

预警维度：
1. 财务暴雷预警（应收账款/商誉/质押率/现金流）
2. 业绩变脸检测（预告修正/营收断崖/利润跳水）
3. 监管风险（问询函/立案调查/ST风险）
4. 黑名单管理（历史暴雷/违规/退市风险）
5. 大股东行为（减持/质押/冻结）

踩雷触发条件（任一满足即入警告池）：
- 应收账款/营收 > 50% 且 增速>营收增速
- 商誉/净资产 > 30%
- 大股东质押率 > 70%
- 经营现金流连续2季度为负
- 近60日收到问询函/关注函
- 业绩预告向下修正幅度 > 50%
"""

import math
import json
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum


class RiskLevel(Enum):
    """风险等级"""
    SAFE = '安全'           # 无预警
    WATCH = '观察'          # 单项预警，关注
    WARNING = '警告'        # 多项预警，减仓
    DANGER = '危险'         # 严重预警，清仓
    BLACKLIST = '黑名单'    # 禁止交易


@dataclass
class FinancialMetrics:
    """财务指标"""
    code: str
    name: str
    report_date: str                         # 最新报告期

    # 应收账款风险
    accounts_receivable: float               # 应收账款
    revenue: float                           # 营业收入
    ar_to_revenue_ratio: float = 0.0         # 应收账款/营收
    ar_growth: float = 0.0                   # 应收增速
    revenue_growth: float = 0.0              # 营收增速

    # 商誉风险
    goodwill: float = 0.0                          # 商誉
    net_assets: float = 1.0                        # 净资产
    goodwill_to_equity_ratio: float = 0.0    # 商誉/净资产

    # 质押风险
    pledge_ratio: float = 0.0                # 大股东质押率

    # 现金流风险
    operating_cf: float = 0.0                # 经营活动现金流
    operating_cf_q2: float = 0.0             # 上季度经营现金流
    cf_negative_quarters: int = 0            # 连续负现金流季度数

    # 盈利能力
    net_profit: float = 0.0                  # 净利润
    net_profit_growth: float = 0.0           # 净利润增速
    gross_margin: float = 0.0                # 毛利率
    gross_margin_change: float = 0.0         # 毛利率同比变化

    # 债务风险
    debt_to_assets: float = 0.0              # 资产负债率
    short_term_debt_ratio: float = 0.0       # 短期债务占比


@dataclass
class RegulatoryRisk:
    """监管风险"""
    code: str
    has_inquiry_letter: bool = False         # 收到问询函
    inquiry_date: str = ''                   # 问询日期
    has_investigation: bool = False          # 被立案调查
    has_st_risk: bool = False                # ST风险
    violation_count: int = 0                 # 近2年违规次数
    audit_opinion: str = '标准无保留'        # 审计意见


@dataclass
class ShareholderBehavior:
    """大股东行为"""
    code: str
    major_reduction_plan: bool = False       # 大股东减持计划
    reduction_progress: float = 0.0          # 减持进度
    insider_selling: bool = False            # 内部人减持
    freeze_ratio: float = 0.0                # 股份冻结比例


@dataclass
class RiskReport:
    """综合风险报告"""
    code: str
    name: str
    date: str
    risk_level: RiskLevel
    total_score: float                       # 综合风险评分（0~1，越高越危险）
    financial_score: float                   # 财务风险评分
    regulatory_score: float                  # 监管风险评分
    shareholder_score: float                 # 大股东行为评分
    warnings: List[str]                      # 具体预警项
    recommendation: str                      # 建议操作


class BlacklistManager:
    """黑名单管理器"""

    BLACKLIST_REASONS = {
        'financial_fraud': '财务造假',
        'delisting_risk': '退市风险',
        'major_violation': '重大违规',
        'bankruptcy_risk': '破产风险',
        'audit_rejection': '审计拒绝表示意见',
        'continuous_loss': '连续亏损',
        'pledge_crisis': '质押危机',
    }

    def __init__(self, blacklist_file: str = None):
        self.blacklist: Dict[str, dict] = {}  # code -> {reason, date, note}
        self.watchlist: Dict[str, dict] = {}  # code -> {warnings, date}
        self.history: List[dict] = []         # 历史风险评估记录

        if blacklist_file:
            self.load(blacklist_file)

    def load(self, file_path: str):
        """加载黑名单"""
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.blacklist = data.get('blacklist', {})
                self.watchlist = data.get('watchlist', {})
                self.history = data.get('history', [])

    def save(self, file_path: str):
        """保存黑名单"""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump({
                'blacklist': self.blacklist,
                'watchlist': self.watchlist,
                'history': self.history,
                'updated': datetime.now().isoformat(),
            }, f, ensure_ascii=False, indent=2)

    def add_to_blacklist(self, code: str, name: str, reason: str, note: str = ''):
        """加入黑名单"""
        self.blacklist[code] = {
            'name': name,
            'reason': reason,
            'note': note,
            'date': datetime.now().strftime('%Y-%m-%d'),
        }

    def is_blacklisted(self, code: str) -> bool:
        """检查是否在黑名单"""
        return code in self.blacklist

    def get_blacklist_info(self, code: str) -> Optional[dict]:
        """获取黑名单信息"""
        return self.blacklist.get(code)


class FinancialMineDetector:
    """财务暴雷检测器"""

    # 预警阈值配置
    THRESHOLDS = {
        # 应收账款
        'ar_to_revenue_max': 0.50,           # 应收账款/营收 > 50%
        'ar_growth_vs_revenue': 0.20,        # 应收增速超过营收增速20pp
        # 商誉
        'goodwill_to_equity_max': 0.30,      # 商誉/净资产 > 30%
        'goodwill_to_equity_severe': 0.50,   # 商誉/净资产 > 50%（严重）
        # 质押
        'pledge_ratio_max': 0.70,            # 大股东质押 > 70%
        'pledge_ratio_severe': 0.85,         # 大股东质押 > 85%（严重）
        # 现金流
        'cf_negative_quarters': 2,           # 连续2季经营现金流为负
        'cf_negative_quarters_severe': 4,    # 连续4季（严重）
        # 利润
        'profit_decline': 0.50,              # 净利润下降 > 50%
        'profit_decline_severe': 0.80,       # 净利润下降 > 80%（严重）
        'gross_margin_decline': 0.05,        # 毛利率下降 > 5pp
        # 债务
        'debt_to_assets_high': 0.70,         # 资产负债率 > 70%
        'short_term_debt_high': 0.60,        # 短期债务占比 > 60%
    }

    def __init__(self):
        self.risk_history: Dict[str, List[dict]] = {}

    def assess_financial_risk(self, metrics: FinancialMetrics) -> Tuple[float, List[str]]:
        """
        评估财务暴雷风险
        返回：(风险评分0~1, 预警项列表)
        """
        score = 0.0
        warnings = []

        # ── 1. 应收账款异常检测 ──
        if metrics.ar_to_revenue_ratio > self.THRESHOLDS['ar_to_revenue_max']:
            score += 0.15
            warnings.append(f"⚠️ 应收账款/营收={metrics.ar_to_revenue_ratio:.1%}>50%，回款风险")
        if metrics.ar_growth > metrics.revenue_growth + self.THRESHOLDS['ar_growth_vs_revenue']:
            score += 0.10
            warnings.append(f"⚠️ 应收增速({metrics.ar_growth:.1%})远超营收增速({metrics.revenue_growth:.1%})，收入质量差")

        # ── 2. 商誉减值风险 ──
        if metrics.goodwill_to_equity_ratio > self.THRESHOLDS['goodwill_to_equity_severe']:
            score += 0.20
            warnings.append(f"🔴 商誉/净资产={metrics.goodwill_to_equity_ratio:.1%}>50%，商誉减值风险极高")
        elif metrics.goodwill_to_equity_ratio > self.THRESHOLDS['goodwill_to_equity_max']:
            score += 0.10
            warnings.append(f"⚠️ 商誉/净资产={metrics.goodwill_to_equity_ratio:.1%}>30%，商誉减值风险")

        # ── 3. 大股东质押风险 ──
        if metrics.pledge_ratio > self.THRESHOLDS['pledge_ratio_severe']:
            score += 0.20
            warnings.append(f"🔴 大股东质押率={metrics.pledge_ratio:.1%}>85%，爆仓风险")
        elif metrics.pledge_ratio > self.THRESHOLDS['pledge_ratio_max']:
            score += 0.10
            warnings.append(f"⚠️ 大股东质押率={metrics.pledge_ratio:.1%}>70%，质押风险")

        # ── 4. 经营现金流 ──
        if metrics.cf_negative_quarters >= self.THRESHOLDS['cf_negative_quarters_severe']:
            score += 0.20
            warnings.append(f"🔴 经营现金流连续{metrics.cf_negative_quarters}季为负，现金流枯竭")
        elif metrics.cf_negative_quarters >= self.THRESHOLDS['cf_negative_quarters']:
            score += 0.10
            warnings.append(f"⚠️ 经营现金流连续{metrics.cf_negative_quarters}季为负")

        # ── 5. 利润断崖 ──
        if metrics.net_profit_growth < -self.THRESHOLDS['profit_decline_severe']:
            score += 0.15
            warnings.append(f"🔴 净利润增速={metrics.net_profit_growth:.1%}，业绩断崖")
        elif metrics.net_profit_growth < -self.THRESHOLDS['profit_decline']:
            score += 0.08
            warnings.append(f"⚠️ 净利润增速={metrics.net_profit_growth:.1%}，利润大幅下滑")

        # ── 6. 毛利率恶化 ──
        if metrics.gross_margin_change < -self.THRESHOLDS['gross_margin_decline']:
            score += 0.07
            warnings.append(f"⚠️ 毛利率同比下降{abs(metrics.gross_margin_change):.1%}，竞争力下降")

        # ── 7. 债务风险 ──
        if metrics.debt_to_assets > self.THRESHOLDS['debt_to_assets_high']:
            score += 0.08
            warnings.append(f"⚠️ 资产负债率={metrics.debt_to_assets:.1%}>70%，债务压力大")
        if metrics.short_term_debt_ratio > self.THRESHOLDS['short_term_debt_high']:
            score += 0.05
            warnings.append(f"⚠️ 短期债务占比={metrics.short_term_debt_ratio:.1%}>60%，流动性风险")

        return min(score, 1.0), warnings

    def assess_regulatory_risk(self, reg: RegulatoryRisk) -> Tuple[float, List[str]]:
        """评估监管风险"""
        score = 0.0
        warnings = []

        if reg.has_investigation:
            score += 0.30
            warnings.append(f"🔴 被立案调查({reg.inquiry_date})")

        if reg.has_st_risk:
            score += 0.25
            warnings.append(f"🔴 ST风险警示")

        if reg.has_inquiry_letter:
            score += 0.10
            warnings.append(f"⚠️ 收到问询函({reg.inquiry_date})")

        if reg.violation_count >= 3:
            score += 0.15
            warnings.append(f"⚠️ 近2年违规{reg.violation_count}次，合规风险高")

        if reg.audit_opinion != '标准无保留':
            score += 0.20
            warnings.append(f"🔴 审计意见: {reg.audit_opinion}")

        return min(score, 1.0), warnings

    def assess_shareholder_risk(self, sh: ShareholderBehavior) -> Tuple[float, List[str]]:
        """评估大股东行为风险"""
        score = 0.0
        warnings = []

        if sh.major_reduction_plan and sh.reduction_progress < 1.0:
            score += 0.10
            warnings.append(f"⚠️ 大股东减持中(进度{sh.reduction_progress:.0%})")

        if sh.insider_selling:
            score += 0.08
            warnings.append(f"⚠️ 内部人减持")

        if sh.freeze_ratio > 0.30:
            score += 0.15
            warnings.append(f"🔴 股份冻结{sh.freeze_ratio:.1%}")

        return min(score, 1.0), warnings

    def comprehensive_assessment(
        self,
        metrics: FinancialMetrics,
        reg: RegulatoryRisk = None,
        shareholder: ShareholderBehavior = None,
        blacklist_mgr: BlacklistManager = None,
    ) -> RiskReport:
        """
        综合风险评定
        """
        all_warnings = []
        financial_score = 0.0
        regulatory_score = 0.0
        shareholder_score = 0.0

        # ── 财务风险 ──
        financial_score, fin_warnings = self.assess_financial_risk(metrics)
        all_warnings.extend(fin_warnings)

        # ── 监管风险 ──
        if reg:
            regulatory_score, reg_warnings = self.assess_regulatory_risk(reg)
            all_warnings.extend(reg_warnings)

        # ── 大股东行为风险 ──
        if shareholder:
            shareholder_score, sh_warnings = self.assess_shareholder_risk(shareholder)
            all_warnings.extend(sh_warnings)

        # ── 黑名单检查 ──
        if blacklist_mgr and blacklist_mgr.is_blacklisted(metrics.code):
            bl_info = blacklist_mgr.get_blacklist_info(metrics.code)
            all_warnings.append(f"🔴 黑名单: {bl_info.get('reason', '')}")
            financial_score = max(financial_score, 0.90)

        # ── 综合评分 ──
        total_score = (
            financial_score * 0.55 +
            regulatory_score * 0.30 +
            shareholder_score * 0.15
        )

        # ── 风险等级判定 ──
        if blacklist_mgr and blacklist_mgr.is_blacklisted(metrics.code):
            risk_level = RiskLevel.BLACKLIST
            recommendation = '🚫 禁止交易，已在黑名单中'
        elif total_score >= 0.60:
            risk_level = RiskLevel.DANGER
            recommendation = '🔴 严重预警，建议清仓'
        elif total_score >= 0.35:
            risk_level = RiskLevel.WARNING
            recommendation = '🟡 多项预警，建议减仓至半仓以下'
        elif total_score >= 0.15:
            risk_level = RiskLevel.WATCH
            recommendation = '🔵 单项预警，关注但不影响持仓'
        else:
            risk_level = RiskLevel.SAFE
            recommendation = '✅ 安全，可正常交易'

        # ── 额外：踩雷概率估算 ──
        mine_probability = 1.0 / (1.0 + math.exp(-8 * (total_score - 0.4)))

        return RiskReport(
            code=metrics.code,
            name=metrics.name,
            date=datetime.now().strftime('%Y-%m-%d'),
            risk_level=risk_level,
            total_score=round(total_score, 4),
            financial_score=round(financial_score, 4),
            regulatory_score=round(regulatory_score, 4),
            shareholder_score=round(shareholder_score, 4),
            warnings=all_warnings,
            recommendation=f"{recommendation} | 踩雷概率: {mine_probability:.1%}",
        )


# ═══════════════════════════════════════════════════════════════
# V13.4.5 P0: MineDetector 2.0 — 三大新检测模块
# ═══════════════════════════════════════════════════════════════

@dataclass
class LimitDownInfo:
    """连续跌停检测数据"""
    code: str
    consecutive_limit_downs: int = 0     # 连续跌停天数
    recent_max_decline: float = 0.0      # 近5日最大跌幅
    limit_down_risk_score: float = 0.0   # 连续跌停风险评分
    is_limit_down_pattern: bool = False  # 是否匹配返利科技模式


@dataclass
class LiquidityInfo:
    """流动性检测数据"""
    code: str
    hsl: float = 0.0                     # 换手率
    turnover_amount: float = 0.0         # 成交额(亿元)
    avg_hsl_20d: float = 0.0             # 20日均换手率
    liquidity_score: float = 0.0         # 流动性风险评分 (高=风险)
    is_illiquid: bool = False            # 是否流动性不足


class LimitDownDetector:
    """V13.4.5: 连续跌停检测器 — 返利科技模式识别"""
    
    LIMIT_DOWN_THRESHOLDS = {
        'consecutive_days_danger': 2,     # ≥2天连续跌停 → 危险
        'consecutive_days_warning': 1,    # 1天跌停 → 警告
        'max_decline_5d_danger': 15.0,   # 5日跌幅>15% → 危险
        'max_decline_5d_warning': 10.0,  # 5日跌幅>10% → 警告
        'volume_spike_ratio': 2.0,       # 跌停日量比>2.0 → 恐慌式跌停
    }
    
    def __init__(self):
        self._kline_cache = {}
    
    def detect(self, code: str, decline_pct: float, kline_data: Optional[List[Dict]] = None) -> LimitDownInfo:
        """
        检测连续跌停模式
        
        返利科技模式特征:
        - 连续2天以上跌停
        - 跌停日伴随放量(恐慌抛售)
        - 短期跌幅累积>15%
        
        kline_data: [{date, close, volume_ratio}, ...] 近20日K线
        """
        info = LimitDownInfo(code=code)
        
        if not kline_data:
            # 简化版: 仅基于当日跌幅判断
            if abs(decline_pct) >= 9.5:
                info.consecutive_limit_downs = 1
                info.is_limit_down_pattern = True
                info.limit_down_risk_score = 0.50
            elif abs(decline_pct) >= 7.0:
                info.limit_down_risk_score = 0.25
            return info
        
        # 完整版: 从K线检测
        consecutive = 0
        total_decline_5d = 0.0
        volume_spike_count = 0
        
        for i, bar in enumerate(kline_data):
            bar_decline = abs(bar.get('chg_pct', 0))
            
            # 检测连续跌停
            if bar_decline >= 9.5:
                consecutive += 1
                if bar.get('volume_ratio', 1.0) > self.LIMIT_DOWN_THRESHOLDS['volume_spike_ratio']:
                    volume_spike_count += 1
            else:
                if consecutive < 2:
                    consecutive = 0  # 不够2天不算模式
            
            # 累计5日跌幅
            if i < 5:
                total_decline_5d += bar_decline
        
        info.consecutive_limit_downs = consecutive
        info.recent_max_decline = total_decline_5d
        
        # 返利科技模式判定
        if consecutive >= 2:
            info.is_limit_down_pattern = True
            info.limit_down_risk_score = min(0.95, 0.40 + consecutive * 0.15 + volume_spike_count * 0.10)
        elif consecutive >= 1 and total_decline_5d > self.LIMIT_DOWN_THRESHOLDS['max_decline_5d_danger']:
            info.is_limit_down_pattern = True
            info.limit_down_risk_score = 0.55
        elif consecutive >= 1:
            info.limit_down_risk_score = 0.30
        elif total_decline_5d > self.LIMIT_DOWN_THRESHOLDS['max_decline_5d_warning']:
            info.limit_down_risk_score = 0.15
        
        return info


class LiquidityDetector:
    """V13.4.5: 流动性检测器"""
    
    LIQUIDITY_THRESHOLDS = {
        'hsl_min_safe': 3.0,            # 换手率≥3% → 安全
        'hsl_warning': 1.0,             # 换手率<1% → 警告(僵尸股)
        'hsl_danger': 0.3,              # 换手率<0.3% → 危险
        'turnover_min_safe': 0.5,       # 成交额≥5000万 → 安全
        'turnover_warning': 0.1,        # 成交额<1000万 → 警告
        'avg_hsl_min': 5.0,             # 20日均换手率≥5% → 低风险
    }
    
    def detect(self, code: str, hsl: float, turnover_amount: float = 0,
               avg_hsl_20d: float = 0) -> LiquidityInfo:
        """检测流动性风险
        
        V13.4.5智能降级: 当所有流动性数据缺失(hsl==0 && turnover==0)时，
        返回中性评分而非误报为流动性不足。缺数据≠僵尸股。
        """
        info = LiquidityInfo(code=code, hsl=hsl, turnover_amount=turnover_amount, avg_hsl_20d=avg_hsl_20d)
        
        # ── 数据完整性检查 ──
        has_any_data = hsl > 0 or turnover_amount > 0 or avg_hsl_20d > 0
        if not has_any_data:
            # 流动性数据完全缺失 → 中性评估，不误报
            info.liquidity_score = 0.0
            info.is_illiquid = False
            info._data_missing = True
            return info
        
        score = 0.0
        
        # 换手率检测 (仅在有数据时)
        if hsl > 0:
            if hsl < self.LIQUIDITY_THRESHOLDS['hsl_danger']:
                score += 0.40
                info.is_illiquid = True
            elif hsl < self.LIQUIDITY_THRESHOLDS['hsl_warning']:
                score += 0.20
            elif hsl >= self.LIQUIDITY_THRESHOLDS['hsl_min_safe']:
                score -= 0.10  # 扣分(降低风险)
        
        # 成交额检测
        if turnover_amount > 0:
            if turnover_amount < self.LIQUIDITY_THRESHOLDS['turnover_warning']:
                score += 0.30
                info.is_illiquid = True
            elif turnover_amount < self.LIQUIDITY_THRESHOLDS['turnover_min_safe']:
                score += 0.10
            elif turnover_amount >= self.LIQUIDITY_THRESHOLDS['turnover_min_safe'] * 2:
                score -= 0.05
        
        # 20日均换手率
        if avg_hsl_20d > 0 and avg_hsl_20d < self.LIQUIDITY_THRESHOLDS['avg_hsl_min']:
            score += 0.10
        
        info.liquidity_score = max(0.0, min(1.0, score))
        return info


# ═══════════════════════════════════════════════════════════════
# V13.4.5 P0: 增强版综合评估 (集成三大新检测器)
# ═══════════════════════════════════════════════════════════════

class MineDetectorV2:
    """V13.4.5 踩雷防御2.0 — 一站式排雷"""
    
    def __init__(self, blacklist_file: str = None):
        self.bl_manager = BlacklistManager(blacklist_file)
        self.financial = FinancialMineDetector()
        self.limit_down = LimitDownDetector()
        self.liquidity = LiquidityDetector()
        self._tdx_kline_cache = {}
    
    def quick_scan(self, code: str, name: str, stock_data: dict) -> dict:
        """
        快速排雷扫描 (供FullMarketMonitor调用)
        
        stock_data = {
            code, name, decline_pct, hsl, volume_ratio, amplitude,
            market, sector, v132_score, ...
        }
        
        返回: {safe: bool, risk_score: float, risk_level: str, warnings: [...]}
        """
        warnings = []
        risk_score = 0.0
        
        decline = abs(stock_data.get('decline_pct', 0))
        hsl = stock_data.get('hsl', 0)
        volume_ratio = stock_data.get('volume_ratio', 1.0)
        amplitude = stock_data.get('amplitude', 0)
        code_str = code
        
        # ── 检测1: 黑名单 ──
        if self.bl_manager.is_blacklisted(code_str):
            bl = self.bl_manager.get_blacklist_info(code_str)
            risk_score += 0.90
            warnings.append(f"🚫 黑名单: {bl.get('reason', '禁止交易')}")
            return self._format_result(code, name, risk_score, 'BLACKLIST', warnings)
        
        # ── 检测2: ST风险 ──
        if 'ST' in name.upper() or '*ST' in name:
            risk_score += 0.80
            warnings.append(f"🔴 ST股: {name}")
        
        # ── 检测3: 连续跌停检测 ──
        if decline >= 9.5 and volume_ratio > 1.5:
            # 恐慌式跌停
            risk_score += 0.55
            warnings.append(f"🔴 跌停+放量({volume_ratio:.1f}x): 返利科技模式疑似")
        elif decline >= 9.5:
            risk_score += 0.40
            warnings.append(f"⚠️ 当日跌停: -{decline:.1f}%")
        elif decline >= 7.0 and volume_ratio > 2.0:
            risk_score += 0.30
            warnings.append(f"⚠️ 大跌+放量: -{decline:.1f}%, 量比{volume_ratio:.1f}x")
        
        # ── 检测4: 流动性检查 ──
        liq = self.liquidity.detect(code_str, hsl)
        if liq.is_illiquid:
            risk_score += 0.30
            warnings.append(f"⚠️ 流动性不足: 换手率{hsl:.2f}%")
        elif liq.liquidity_score > 0.10:
            risk_score += 0.10
            warnings.append(f"💡 流动性偏低: 换手率{hsl:.2f}%")
        
        # ── 检测5: 基本面快速检查 ──
        # 高换手+低振幅 = 主力对倒出货 → 踩雷
        if hsl > 15 and amplitude < 3:
            risk_score += 0.20
            warnings.append(f"⚠️ 高换手({hsl:.1f}%)+低振幅({amplitude:.1f}%): 对倒嫌疑")
        
        # 出现新股爆炒后的估值回归
        market = stock_data.get('market', '')
        if decline > 12:
            risk_score += 0.15
            warnings.append(f"⚠️ 跌幅过大: -{decline:.1f}%, 趋势可能恶化")
        
        risk_score = min(1.0, risk_score)
        
        # 风险等级 (V13.4.5校准: DANGER严格化，仅确认模式触发)
        if risk_score >= 0.75:
            level = 'DANGER'
        elif risk_score >= 0.45:
            level = 'WARNING'
        elif risk_score >= 0.20:
            level = 'WATCH'
        else:
            level = 'SAFE'
        
        return self._format_result(code, name, risk_score, level, warnings)
    
    def _format_result(self, code, name, score, level, warnings):
        safe = level in ['SAFE', 'WATCH']
        return {
            'code': code, 'name': name,
            'safe': safe,
            'risk_score': round(score, 4),
            'risk_level': level,
            'warnings': warnings,
            'warning_count': len(warnings),
            'mine_probability': round(1.0 / (1.0 + math.exp(-8 * (score - 0.4))), 4),
        }


# ═══════════════════════════════════════════════
# 快捷运行入口
# ═══════════════════════════════════════════════

def run_mine_detection(code: str, name: str,
                       financial_data: dict,
                       regulatory_data: dict = None,
                       shareholder_data: dict = None,
                       blacklist_file: str = 'V13_0_data/blacklist.json') -> dict:
    """
    财务暴雷检测便捷入口
    返回格式兼容 V13.0 data.json 的 risk_assessment 字段
    """

    # 构造财务指标
    metrics = FinancialMetrics(
        code=code,
        name=name,
        report_date=financial_data.get('report_date', ''),
        accounts_receivable=financial_data.get('accounts_receivable', 0),
        revenue=financial_data.get('revenue', 1),
        ar_to_revenue_ratio=financial_data.get('ar_to_revenue_ratio', 0),
        ar_growth=financial_data.get('ar_growth', 0),
        revenue_growth=financial_data.get('revenue_growth', 0),
        goodwill=financial_data.get('goodwill', 0),
        net_assets=financial_data.get('net_assets', 1),
        goodwill_to_equity_ratio=financial_data.get('goodwill_to_equity_ratio', 0),
        pledge_ratio=financial_data.get('pledge_ratio', 0),
        operating_cf=financial_data.get('operating_cf', 0),
        operating_cf_q2=financial_data.get('operating_cf_q2', 0),
        cf_negative_quarters=financial_data.get('cf_negative_quarters', 0),
        net_profit=financial_data.get('net_profit', 0),
        net_profit_growth=financial_data.get('net_profit_growth', 0),
        gross_margin=financial_data.get('gross_margin', 0),
        gross_margin_change=financial_data.get('gross_margin_change', 0),
        debt_to_assets=financial_data.get('debt_to_assets', 0),
        short_term_debt_ratio=financial_data.get('short_term_debt_ratio', 0),
    )

    # 构造监管风险
    reg = None
    if regulatory_data:
        reg = RegulatoryRisk(
            code=code,
            has_inquiry_letter=regulatory_data.get('has_inquiry_letter', False),
            inquiry_date=regulatory_data.get('inquiry_date', ''),
            has_investigation=regulatory_data.get('has_investigation', False),
            has_st_risk=regulatory_data.get('has_st_risk', False),
            violation_count=regulatory_data.get('violation_count', 0),
            audit_opinion=regulatory_data.get('audit_opinion', '标准无保留'),
        )

    # 构造大股东行为
    shareholder = None
    if shareholder_data:
        shareholder = ShareholderBehavior(
            code=code,
            major_reduction_plan=shareholder_data.get('major_reduction_plan', False),
            reduction_progress=shareholder_data.get('reduction_progress', 0),
            insider_selling=shareholder_data.get('insider_selling', False),
            freeze_ratio=shareholder_data.get('freeze_ratio', 0),
        )

    # 黑名单管理器
    bl_mgr = BlacklistManager(blacklist_file)

    # 执行检测
    detector = FinancialMineDetector()
    report = detector.comprehensive_assessment(
        metrics=metrics,
        reg=reg,
        shareholder=shareholder,
        blacklist_mgr=bl_mgr,
    )

    return {
        'code': code,
        'name': name,
        'date': report.date,
        'risk_level': report.risk_level.value,
        'total_score': report.total_score,
        'financial_score': report.financial_score,
        'regulatory_score': report.regulatory_score,
        'shareholder_score': report.shareholder_score,
        'warnings': report.warnings,
        'warning_count': len(report.warnings),
        'recommendation': report.recommendation,
        'trade_allowed': report.risk_level.value not in ['黑名单', '危险'],
    }


if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 黑名单 + 财务暴雷预警系统")
    print("=" * 60)
    print("目标: 踩雷率从8%降至≤5%")
    print("=" * 60)

    # 模拟高雷股测试
    result = run_mine_detection(
        code='000000',
        name='高雷测试股',
        financial_data={
            'accounts_receivable': 5_000_000_000,
            'revenue': 8_000_000_000,
            'ar_to_revenue_ratio': 0.625,
            'ar_growth': 0.35,
            'revenue_growth': 0.05,
            'goodwill': 2_000_000_000,
            'net_assets': 3_500_000_000,
            'goodwill_to_equity_ratio': 0.571,
            'pledge_ratio': 0.88,
            'cf_negative_quarters': 3,
            'net_profit_growth': -0.65,
            'gross_margin_change': -0.07,
            'debt_to_assets': 0.75,
            'short_term_debt_ratio': 0.65,
        },
        regulatory_data={
            'has_inquiry_letter': True,
            'inquiry_date': '2026-05-15',
            'violation_count': 4,
        },
        shareholder_data={
            'major_reduction_plan': True,
            'reduction_progress': 0.35,
            'freeze_ratio': 0.40,
        },
    )

    print(f"\n📊 检测结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
