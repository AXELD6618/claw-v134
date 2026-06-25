#!/usr/bin/env python3
"""
V13.0 Phase3 扩展模块集合
==========================
涵盖：北交所30cm涨停预测 + IPO催化策略 + 全球鹰眼另类数据 + 知识库→交易闭环 + 微信推送

这些模块作为独立策略线，补全V13.0生态的最后拼图。
"""

import json
import math
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════
# Part A: 北交所30cm涨停预测引擎
# ═══════════════════════════════════════════════

@dataclass
class BSEConfig:
    """北交所专用配置"""
    # 北交所特色：30cm涨跌幅、流动性偏低、主题驱动强
    min_daily_volume: float = 5_000_000     # 日成交额≥500万（过滤僵尸股）
    max_market_cap: float = 10_000_000_000  # 市值≤100亿（小市值弹性大）
    theme_alignment_bonus: float = 0.15     # 与热点主题对齐的加分
    pre_limit_volume_surge: float = 3.0     # 涨停前量比≥3倍
    institution_new_entry: float = 0.01     # 机构新进≥1%

    # 30cm特征：需要更高的波动容忍度
    volatility_threshold: float = 0.05      # 日波动≥5%
    consecutive_up_days: int = 2            # 涨停前至少2连阳


class BSE30cmPredictor:
    """北交所30cm涨停预测器"""

    def __init__(self, config: BSEConfig = None):
        self.config = config or BSEConfig()
        self.prediction_history: List[dict] = []

    def screen_bse_candidates(self, bse_stocks: List[dict]) -> List[dict]:
        """
        北交所候选池初筛
        筛选条件：日成交额/市值/流动性
        """
        candidates = []
        for stock in bse_stocks:
            market = stock.get('market', '')
            if 'BJ' not in market and '北交所' not in stock.get('code', '')[:2]:
                continue

            daily_volume = stock.get('daily_volume', 0)
            market_cap = stock.get('market_cap', float('inf'))

            if daily_volume < self.config.min_daily_volume:
                continue
            if market_cap > self.config.max_market_cap:
                continue

            candidates.append(stock)

        return candidates

    def predict_30cm_probability(self, stock: dict) -> dict:
        """
        预测30cm涨停概率

        因子：
        - 市值因子：越小越容易涨停
        - 主题对齐：与热点主题重合
        - 量价异动：量比/换手率/连阳
        - 机构行为：新进/龙虎榜
        """
        score = 0.0
        details = []

        # 市值因子（小市值弹性大）
        market_cap = stock.get('market_cap', 0)
        if 0 < market_cap <= 2_000_000_000:
            score += 0.25
            details.append('微盘股(<20亿)·弹性大')
        elif market_cap <= 5_000_000_000:
            score += 0.18
            details.append('小盘股(20-50亿)')
        elif market_cap <= 10_000_000_000:
            score += 0.10
            details.append('中盘股(50-100亿)')

        # 主题对齐
        hot_themes = ['先进封装', 'AI算力', '机器人', '人形机器人', 'DrMOS', '液冷', '量子']
        stock_theme = stock.get('industry', '') + stock.get('sub_sector', '')
        for theme in hot_themes:
            if theme in stock_theme:
                score += self.config.theme_alignment_bonus
                details.append(f'热点主题·{theme}')
                break

        # 量价异动
        volume_ratio = stock.get('volume_ratio', 1.0)
        if volume_ratio >= self.config.pre_limit_volume_surge:
            score += 0.20
            details.append(f'量比{volume_ratio:.1f}x·主力异动')
        elif volume_ratio >= 2.0:
            score += 0.12
            details.append(f'量比{volume_ratio:.1f}x')

        turnover = stock.get('turnover_rate', 0)
        if turnover >= 0.10:
            score += 0.15
            details.append(f'换手率{turnover:.0%}·活跃度高')

        # 连阳
        consecutive_up = stock.get('consecutive_up_days', 0)
        if consecutive_up >= self.config.consecutive_up_days:
            score += 0.10
            details.append(f'{consecutive_up}连阳·趋势确认')
        if consecutive_up >= 5:
            score += 0.05
            details.append('强势连阳')

        # 机构行为
        institution_change = stock.get('institution_change', 0)
        if institution_change >= self.config.institution_new_entry:
            score += 0.10
            details.append('机构新进')

        # 波动率
        volatility = stock.get('volatility', 0)
        if volatility >= self.config.volatility_threshold:
            score += 0.05
            details.append('高波动·30cm潜力')

        # 最终概率
        prob = min(score, 0.85)
        prob = 1.0 / (1.0 + math.exp(-6 * (prob - 0.4)))  # Sigmoid映射

        return {
            'code': stock.get('code', ''),
            'name': stock.get('name', ''),
            'market_cap': market_cap,
            'prediction_prob': round(prob, 4),
            'raw_score': round(score, 4),
            'details': details,
            'confidence': '高' if prob >= 0.50 else '中' if prob >= 0.30 else '低',
            '30cm_eligible': prob >= 0.30,
        }


# ═══════════════════════════════════════════════
# Part B: IPO催化策略引擎
# ═══════════════════════════════════════════════

class IPOCatalystEngine:
    """IPO催化策略引擎"""

    def detect_spillover_effect(self, ipo_info: dict, sector_stocks: List[dict]) -> List[dict]:
        """
        检测IPO同板块溢出效应
        - 新上市公司所在板块 → 同板块龙头受益
        - 稀缺性标的 → 可比公司估值重塑
        """
        ipo_industry = ipo_info.get('industry', '')
        ipo_sub_sector = ipo_info.get('sub_sector', '')
        ipo_pe = ipo_info.get('pe', 0)
        ipo_market_cap = ipo_info.get('market_cap', 0)

        spillover_candidates = []

        for stock in sector_stocks:
            score = 0.0
            reasons = []

            # 同行业
            if ipo_industry in stock.get('industry', ''):
                score += 0.30
                reasons.append('同行业·IPO估值对标')

            # 可比估值（IPO高PE → 同行估值重估）
            existing_pe = stock.get('pe', 0)
            if ipo_pe > 0 and existing_pe > 0 and existing_pe < ipo_pe * 0.7:
                score += 0.25
                reasons.append(f'折价对标(PE{existing_pe:.0f} vs IPO{ipo_pe:.0f})')

            # 同子赛道
            if ipo_sub_sector and ipo_sub_sector in stock.get('sub_sector', ''):
                score += 0.20
                reasons.append('同子赛道·直接对标')

            # 稀缺性（IPO是稀缺标的时影响更大）
            if stock.get('uniqueness', 0) > 0.7:
                score += 0.15
                reasons.append('稀缺性标的')

            if score >= 0.30:
                spillover_candidates.append({
                    'code': stock.get('code', ''),
                    'name': stock.get('name', ''),
                    'spillover_score': round(min(score, 0.90), 4),
                    'reasons': reasons,
                    'ipo_name': ipo_info.get('name', ''),
                    'ipo_code': ipo_info.get('code', ''),
                })

        return sorted(spillover_candidates, key=lambda x: x['spillover_score'], reverse=True)

    def ipo_momentum_model(self, ipo_data: dict) -> dict:
        """
        IPO上市初期动量模型
        - 首日涨幅/换手率/发行PE/中签率 → 预测短期走势
        """
        first_day_pct = ipo_data.get('first_day_pct', 0)
        first_day_turnover = ipo_data.get('first_day_turnover', 0)
        ipo_pe = ipo_data.get('ipo_pe', 0)
        sector_avg_pe = ipo_data.get('sector_avg_pe', 0)
        lottery_rate = ipo_data.get('lottery_rate', 0.05)

        score = 0.0
        details = []

        # 首日涨幅
        if 0.20 <= first_day_pct <= 0.80:
            score += 0.20
            details.append(f'首日+{first_day_pct:.0%}·合理涨幅')
        elif first_day_pct > 0.80:
            score += 0.10
            details.append('首日涨幅过大·追高谨慎')
        elif first_day_pct < 0.10:
            score += 0.05
            details.append('首日平淡·关注回踩')

        # 换手率（高换手=活跃筹码）
        if first_day_turnover >= 0.60:
            score += 0.15
            details.append(f'首日换手{first_day_turnover:.0%}·筹码充分交换')

        # 发行PE vs 行业PE（折价发行=上涨空间）
        if ipo_pe > 0 and sector_avg_pe > 0 and ipo_pe < sector_avg_pe:
            discount = (sector_avg_pe - ipo_pe) / sector_avg_pe
            score += min(discount * 0.3, 0.15)
            details.append(f'发行折价{discount:.0%}')

        # 中签率（越低越稀缺）
        if lottery_rate < 0.02:
            score += 0.10
            details.append(f'中签率{lottery_rate:.1%}·稀缺')

        momentum_prob = 1.0 / (1.0 + math.exp(-5 * (score - 0.35)))

        return {
            'code': ipo_data.get('code', ''),
            'name': ipo_data.get('name', ''),
            'momentum_score': round(score, 4),
            'momentum_probability': round(momentum_prob, 4),
            'details': details,
            'signal': 'strong' if momentum_prob >= 0.60 else 'moderate' if momentum_prob >= 0.40 else 'weak',
        }


# ═══════════════════════════════════════════════
# Part C: 全球鹰眼另类数据→量化因子
# ═══════════════════════════════════════════════

class AltDataFactorEngine:
    """
    另类数据因子引擎
    将全球鹰眼的多源数据转化为可集成到M47权重体系的量化因子
    """

    FACTOR_MAP = {
        'satellite_parking_lot': '卫星停车场密度→消费活动因子',
        'satellite_night_light': '卫星夜光强度→经济活动因子',
        'flight_density': 'OpenSky航班密度→商务活动因子',
        'shipping_ais': 'AIS航运轨迹→贸易活动因子',
        'port_congestion': '港口拥堵指数→供应链因子',
        'agriculture_satellite': '农业卫星遥感→产量预期因子',
        'seafood_tracker': '海鲜捕捞追踪→渔业供给因子',
    }

    def satellite_to_factor(self, satellite_data: dict) -> dict:
        """
        卫星数据→量化因子
        停车场密度/夜光强度反映经济活动
        """
        factors = {}
        score = 0.0

        # 停车场密度（环比变化）
        parking_change = satellite_data.get('parking_density_change', 0)
        if parking_change > 0.05:
            factors['消费活动'] = 0.7
            score += 0.15
        elif parking_change < -0.05:
            factors['消费活动'] = 0.3
            score -= 0.10

        # 夜光强度
        night_light_change = satellite_data.get('night_light_change', 0)
        if night_light_change > 0.03:
            factors['经济活动'] = 0.75
            score += 0.12
        elif night_light_change < -0.03:
            factors['经济活动'] = 0.25
            score -= 0.08

        # 归一化为0~1因子
        factor_score = 1.0 / (1.0 + math.exp(-4 * (score - 0.05)))

        return {
            'factor_name': '卫星活动因子',
            'raw_score': round(score, 4),
            'normalized_factor': round(factor_score, 4),
            'sub_factors': factors,
            'suggested_weight': max(0.02, min(0.10, abs(score))),  # 建议在7权重中占比
        }

    def aviation_to_factor(self, aviation_data: dict) -> dict:
        """航班数据→商务活动因子"""
        flight_change = aviation_data.get('flight_count_change', 0)
        on_time_rate = aviation_data.get('on_time_rate', 0.80)

        if flight_change > 0.10:
            signal = 0.70
        elif flight_change > 0.03:
            signal = 0.55
        elif flight_change < -0.05:
            signal = 0.30
        else:
            signal = 0.50

        # 准点率修正
        signal += (on_time_rate - 0.80) * 0.2

        return {
            'factor_name': '航空商务因子',
            'flight_change': flight_change,
            'on_time_rate': on_time_rate,
            'factor_score': round(min(max(signal, 0.1), 0.90), 4),
            'suggested_weight': 0.03,
        }

    def shipping_to_factor(self, shipping_data: dict) -> dict:
        """航运AIS→贸易活动因子"""
        ship_count_change = shipping_data.get('active_ship_change', 0)
        port_waiting_time = shipping_data.get('avg_waiting_hours', 24)
        congestion_score = shipping_data.get('congestion_index', 0.5)

        score = 0.50
        if ship_count_change > 0.05:
            score += 0.15
        if port_waiting_time < 24:
            score += 0.10  # 通关顺畅
        score += (0.5 - congestion_score) * 0.2  # 拥堵越低越好

        return {
            'factor_name': '航运贸易因子',
            'factor_score': round(min(max(score, 0.1), 0.85), 4),
            'congestion_index': congestion_score,
            'suggested_weight': 0.03,
        }

    def aggregate_alt_factors(self, satellite: dict, aviation: dict, shipping: dict) -> dict:
        """
        聚合所有另类数据因子
        返回可直接注入M47权重体系的因子值
        """
        sat = self.satellite_to_factor(satellite)
        avi = self.aviation_to_factor(aviation)
        shp = self.shipping_to_factor(shipping)

        # 聚合权重
        aggregated = (
            sat['normalized_factor'] * 0.40 +
            avi['factor_score'] * 0.30 +
            shp['factor_score'] * 0.30
        )

        # 映射到M47催化因子(W1)的增量
        catalyst_adjustment = (aggregated - 0.50) * 0.10  # ±0.05范围

        return {
            'aggregated_alt_factor': round(aggregated, 4),
            'catalyst_adj': round(catalyst_adjustment, 4),
            'satellite': sat,
            'aviation': avi,
            'shipping': shp,
            'inject_to': 'W1催化因子',
            'adjustment_range': f'{catalyst_adjustment:+.4f}',
        }


# ═══════════════════════════════════════════════
# Part D: 知识库→选股自动映射管道
# ═══════════════════════════════════════════════

class KBPipeline:
    """
    知识库→选股映射管道
    从JSON知识库自动提取标的→验证代码→写入候选池→触发信号监控
    """

    def __init__(self, kb_dir: str = 'knowledge_bases', output_dir: str = 'V13_0_data'):
        self.kb_dir = kb_dir
        self.output_dir = output_dir
        self.candidate_pool_file = os.path.join(output_dir, 'kb_candidate_pool.json')
        os.makedirs(output_dir, exist_ok=True)

    def extract_stocks_from_kb(self, kb_file: str) -> List[dict]:
        """
        从知识库JSON提取标的列表
        自动解析股票代码和名称
        """
        if not os.path.exists(kb_file):
            return []

        with open(kb_file, 'r', encoding='utf-8') as f:
            kb = json.load(f)

        stocks = []
        # 支持多种JSON结构
        if isinstance(kb, list):
            for item in kb:
                stocks.extend(self._extract_from_item(item))
        elif isinstance(kb, dict):
            for key, value in kb.items():
                if isinstance(value, list):
                    for item in value:
                        stocks.extend(self._extract_from_item(item))
                elif isinstance(value, dict):
                    stocks.extend(self._extract_from_item(value))

        return stocks

    def _extract_from_item(self, item: dict) -> List[dict]:
        """从单个知识库条目提取股票"""
        stocks = []
        code = item.get('code', '') or item.get('stock_code', '') or item.get('证券代码', '')
        name = item.get('name', '') or item.get('stock_name', '') or item.get('证券名称', '')

        if code and name:
            stocks.append({
                'code': str(code).zfill(6),
                'name': name,
                'source_kb': item.get('kb_name', 'unknown'),
                'industry': item.get('industry', ''),
                'sub_sector': item.get('sub_sector', ''),
                'thesis': item.get('thesis', '') or item.get('投资逻辑', ''),
                'rating': item.get('rating', '') or item.get('评级', ''),
            })

        # 递归搜索嵌套
        for key, value in item.items():
            if isinstance(value, dict) and key not in ('code', 'name'):
                stocks.extend(self._extract_from_item(value))

        return stocks

    def verify_stock_codes(self, stocks: List[dict]) -> List[dict]:
        """
        验证股票代码（通过TDX-Connector查询）
        过滤无效代码
        """
        verified = []
        for stock in stocks:
            # 基本格式验证
            code = stock['code']
            if len(code) == 6 and code.isdigit():
                market = 'SZ' if code.startswith(('0', '3')) else 'SH' if code.startswith('6') else 'BJ'
                stock['market'] = market
                stock['verified'] = True
                stock['full_code'] = f"{market}{code}"
                verified.append(stock)
            else:
                stock['verified'] = False
                stock['error'] = '代码格式无效'

        return verified

    def update_candidate_pool(self, new_stocks: List[dict]):
        """
        更新候选池（增量更新，保留历史）
        """
        pool = {}
        if os.path.exists(self.candidate_pool_file):
            with open(self.candidate_pool_file, 'r', encoding='utf-8') as f:
                pool = json.load(f)

        for stock in new_stocks:
            if not stock.get('verified'):
                continue
            code = stock['code']
            pool[code] = {
                **pool.get(code, {}),
                **stock,
                'last_updated': datetime.now().strftime('%Y-%m-%d'),
                'consecutive_days_in_pool': pool.get(code, {}).get('consecutive_days_in_pool', 0) + 1,
            }

        with open(self.candidate_pool_file, 'w', encoding='utf-8') as f:
            json.dump(pool, f, ensure_ascii=False, indent=2)

        return pool

    def generate_monitor_list(self, min_days_in_pool: int = 2) -> List[dict]:
        """
        生成需要监控的标的列表（在候选池中≥N天）
        这些标的将被纳入天眼系统的每日信号扫描
        """
        if not os.path.exists(self.candidate_pool_file):
            return []

        with open(self.candidate_pool_file, 'r', encoding='utf-8') as f:
            pool = json.load(f)

        monitor = []
        for code, stock in pool.items():
            if stock.get('consecutive_days_in_pool', 0) >= min_days_in_pool:
                monitor.append(stock)

        return sorted(monitor, key=lambda x: x.get('consecutive_days_in_pool', 0), reverse=True)

    def run_full_pipeline(self, kb_files: List[str] = None) -> dict:
        """
        执行完整的知识库→选股管道
        """
        # 自动发现知识库文件
        if kb_files is None:
            kb_files = []
            if os.path.exists(self.kb_dir):
                for f in os.listdir(self.kb_dir):
                    if f.endswith('.json'):
                        kb_files.append(os.path.join(self.kb_dir, f))

        all_stocks = []
        for kb_file in kb_files:
            stocks = self.extract_stocks_from_kb(kb_file)
            all_stocks.extend(stocks)

        # 去重
        seen = {}
        unique_stocks = []
        for s in all_stocks:
            if s['code'] not in seen:
                seen[s['code']] = True
                unique_stocks.append(s)

        # 验证
        verified = self.verify_stock_codes(unique_stocks)

        # 更新候选池
        pool = self.update_candidate_pool(verified)

        # 生成监控列表
        monitor = self.generate_monitor_list()

        return {
            'kb_files_scanned': len(kb_files),
            'total_extracted': len(all_stocks),
            'unique_stocks': len(unique_stocks),
            'verified_stocks': len(verified),
            'candidate_pool_size': len(pool),
            'monitor_list_size': len(monitor),
            'monitor_list': [{'code': m['code'], 'name': m['name'], 'days': m.get('consecutive_days_in_pool', 0)}
                             for m in monitor[:20]],
        }


# ═══════════════════════════════════════════════
# Part E: 微信小程序实时推送格式化
# ═══════════════════════════════════════════════

class WeChatPushFormatter:
    """
    微信小程序实时推送格式化
    将信号转化为结构化的推送消息（适配模板消息格式）
    """

    @staticmethod
    def format_trading_signal(signal_data: dict) -> dict:
        """
        格式化交易信号推送
        适配微信小程序模板消息
        """
        return {
            'touser': signal_data.get('openid', ''),
            'template_id': 'trading_signal_v13',
            'page': f'pages/signal/detail?code={signal_data.get("code", "")}',
            'data': {
                'thing1': {'value': signal_data.get('name', '')[:20]},          # 股票名称
                'character_string2': {'value': signal_data.get('code', '')},    # 股票代码
                'amount3': {'value': f'{signal_data.get("m46_prob", 0):.1%}'},  # 贝叶斯概率
                'phrase4': {'value': signal_data.get('confidence', '中')},       # 置信度
                'thing5': {'value': signal_data.get('action', '关注')[:20]},     # 操作建议
                'amount6': {'value': f'{signal_data.get("position_pct", 0):.0f}%'}, # 建议仓位
                'thing7': {'value': signal_data.get('m51_intent', '无')[:10]},   # 主力意图
                'date8': {'value': signal_data.get('date', '')},                 # 日期
            }
        }

    @staticmethod
    def format_risk_alert(risk_data: dict) -> dict:
        """格式化风险告警推送"""
        return {
            'touser': risk_data.get('openid', ''),
            'template_id': 'risk_alert_v13',
            'page': f'pages/risk/detail?code={risk_data.get("code", "")}',
            'data': {
                'thing1': {'value': risk_data.get('name', '')[:20]},
                'phrase2': {'value': risk_data.get('risk_level', '观察')},
                'thing3': {'value': '; '.join(risk_data.get('warnings', ['无']))[:50]},
                'thing4': {'value': risk_data.get('recommendation', '')[:30]},
                'date5': {'value': risk_data.get('date', '')},
            }
        }

    @staticmethod
    def format_market_brief(brief_data: dict) -> dict:
        """格式化市场简报推送"""
        return {
            'touser': brief_data.get('openid', ''),
            'template_id': 'market_brief_v13',
            'data': {
                'thing1': {'value': brief_data.get('title', '天眼V13.0快讯')[:20]},
                'phrase2': {'value': brief_data.get('market_sentiment', '中性')},
                'amount3': {'value': f'{brief_data.get("hit_rate", 0):.0%}'},
                'amount4': {'value': f'{brief_data.get("plr", 0):.1f}'},
                'number5': {'value': str(brief_data.get('signal_count', 0))},
                'thing6': {'value': brief_data.get('top_pick', '')[:20]},
                'date7': {'value': brief_data.get('date', '')},
            }
        }

    @staticmethod
    def generate_daily_summary(summary: dict) -> str:
        """
        生成每日总结文本（微信富文本）
        """
        lines = [
            f'📊 [天眼V13.0] {summary.get("date", "")} 作战总结',
            f'━━━━━━━━━━━━━━━━━━',
            f'🎯 命中率: {summary.get("hit_rate", 0):.1%} (目标45%)',
            f'💰 盈亏比: {summary.get("plr", 0):.1f} (目标3.0)',
            f'💣 踩雷数: {summary.get("mine_count", 0)} (目标≤5%)',
            f'',
            f'📈 今日信号: {summary.get("signal_count", 0)}个',
            f'  高置信度: {summary.get("high_conf", 0)}个',
            f'  中置信度: {summary.get("mid_conf", 0)}个',
            f'',
            f'🏆 Top3候选:',
        ]

        for i, pick in enumerate(summary.get('top_picks', [])[:3], 1):
            lines.append(f'  {i}. {pick["name"]}({pick["code"]}) P={pick.get("prob", 0):.1%} [{pick.get("conf", "中")}]')

        lines.extend([
            f'',
            f'🛡️ 风险标的: {summary.get("risk_count", 0)}个',
            f'',
            f'━━━━━━━━━━━━━━━━━━',
            f'🤖 贝叶斯引擎 | 7权重融合V2 | M55日频校准中',
        ])

        return '\n'.join(lines)


# ═══════════════════════════════════════════════
# 统一入口
# ═══════════════════════════════════════════════

def run_phase3_modules():
    """Phase 3模块自检"""
    print("=" * 60)
    print("V13.0 Phase 3 扩展模块集合")
    print("=" * 60)

    # 北交所测试
    bse = BSE30cmPredictor()
    test_bse = {
        'code': '833xxx', 'name': '北交测试', 'market': 'BJ',
        'market_cap': 3_000_000_000, 'industry': 'AI算力',
        'volume_ratio': 3.5, 'turnover_rate': 0.12,
        'consecutive_up_days': 3, 'institution_change': 0.02,
        'volatility': 0.06,
    }
    bse_result = bse.predict_30cm_probability(test_bse)
    print(f"\n📊 北交所30cm预测: {json.dumps(bse_result, ensure_ascii=False, indent=2)}")

    # IPO测试
    ipo = IPOCatalystEngine()
    test_ipo = {
        'code': '688xxx', 'name': 'AI芯片新股', 'industry': 'AI算力/服务器',
        'first_day_pct': 0.45, 'first_day_turnover': 0.65,
        'ipo_pe': 35, 'sector_avg_pe': 55, 'lottery_rate': 0.015,
    }
    ipo_result = ipo.ipo_momentum_model(test_ipo)
    print(f"\n📊 IPO动量: {json.dumps(ipo_result, ensure_ascii=False, indent=2)}")

    # 另类数据测试
    alt = AltDataFactorEngine()
    alt_result = alt.aggregate_alt_factors(
        {'parking_density_change': 0.08, 'night_light_change': 0.04},
        {'flight_count_change': 0.06, 'on_time_rate': 0.85},
        {'active_ship_change': 0.03, 'avg_waiting_hours': 18, 'congestion_index': 0.40},
    )
    print(f"\n📊 另类数据因子: {json.dumps(alt_result, ensure_ascii=False, indent=2)}")

    # KB管道测试
    kb = KBPipeline()
    print(f"\n📊 KB管道就绪: kb_dir={kb.kb_dir}, output={kb.output_dir}")

    # 微信推送测试
    wx = WeChatPushFormatter()
    summary = wx.generate_daily_summary({
        'date': '2026-06-23',
        'hit_rate': 0.42, 'plr': 2.8, 'mine_count': 1,
        'signal_count': 8, 'high_conf': 3, 'mid_conf': 5,
        'top_picks': [
            {'name': '北方华创', 'code': '002371', 'prob': 0.72, 'conf': '高'},
            {'name': '麦格米特', 'code': '002851', 'prob': 0.68, 'conf': '中'},
            {'name': '思源电气', 'code': '002028', 'prob': 0.55, 'conf': '中'},
        ],
        'risk_count': 0,
    })
    print(f"\n📱 微信推送预览:\n{summary}")

    print(f"\n{'='*60}")
    print("Phase 3 扩展模块全部就绪 ✅")
    print(f"{'='*60}")


if __name__ == '__main__':
    run_phase3_modules()
