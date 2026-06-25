#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 TDX Alpha因子激活器 — TDXAlphaEnricher                       ║
║  ================================================================    ║
║  专用模块：用TDX Tier2/Tier3真实数据激活M57的12个休眠Alpha因子        ║
║                                                                      ║
║  核心功能：                                                           ║
║  1. 生成Agent TDX数据采集指令（精确到每个MCP工具的参数）              ║
║  2. 将TDX API响应自动注入M57因子计算器                                ║
║  3. 验证12个因子的激活状态                                            ║
║  4. 输出因子激活报告和未激活原因诊断                                  ║
║                                                                      ║
║  M57因子 → TDX数据源映射：                                           ║
║  ┌──────────────────┬─────────────────────────────────┬──────────┐ ║
║  │ 因子              │ TDX数据源                       │ 状态     │ ║
║  ├──────────────────┼─────────────────────────────────┼──────────┤ ║
║  │ 1. tail_rs        │ tdx_quotes (行情)               │ ✅ 已激活 │ ║
║  │ 2. tail_vol_struct│ tdx_quotes + tdx_kline(1min)    │ ✅ 已激活 │ ║
║  │ 3. overnight_mom  │ tdx_kline(D) 开盘价序列         │ ✅ 已激活 │ ║
║  │ 4. intraday_rev   │ tdx_quotes (高低价)             │ ✅ 已激活 │ ║
║  │ 5. auction_sig    │ tdx_kline(1min, 14:57-15:00)    │ ⚠️ 需1min│ ║
║  │ 6. sector_alpha   │ tdx_screener (板块涨幅)         │ ⚠️ 需板块 │ ║
║  │ 7. streak_exp     │ tdx_screener (连板天数)         │ ⚠️ 需筛选 │ ║
║  │ 8. flow_accel     │ tdx_kline(1min, 尾盘30根)       │ ✅ 已激活 │ ║
║  │ 9. gap_fill_prob  │ tdx_kline(D) 历史跳空统计       │ ✅ 已激活 │ ║
║  │ 10. event_decay   │ wenda_news/notice (事件信号)    │ ❌ 需新闻 │ ║
║  │ 11. lhb_effect    │ tdx_api_data(jglhb) 龙虎榜     │ ❌ 需龙虎 │ ║
║  │ 12. sentiment_tr  │ tdx_quotes(指数) 市场情绪       │ ❌ 需指数 │ ║
║  └──────────────────┴─────────────────────────────────┴──────────┘ ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 因子激活状态定义
# ═══════════════════════════════════════════════════════════════

@dataclass
class FactorActivationStatus:
    """单个M57因子的激活状态"""
    name: str
    display_name: str
    tdx_source: str           # TDX数据源描述
    current_value: float = 0.0
    is_active: bool = False   # 是否已用真实数据激活
    is_dormant: bool = True   # 是否仍在休眠（使用默认值）
    activation_method: str = ''  # 激活方法描述
    dormant_reason: str = ''  # 休眠原因


# 12因子的元数据
FACTOR_METADATA = {
    'tail_rs': {
        'display': '尾盘相对强度',
        'tdx_source': 'tdx_quotes (intraday_change + tail_30min_change)',
        'activation': '自动激活 — 从tdx_quotes的HQInfo提取涨幅数据',
    },
    'tail_vol_struct': {
        'display': '尾盘量能结构',
        'tdx_source': 'tdx_quotes (amount) + tdx_kline(1min, volume)',
        'activation': '自动激活 — 从成交额和1分钟量计算',
    },
    'overnight_mom': {
        'display': '隔夜动量',
        'tdx_source': 'tdx_kline(period=7, 日K线开盘价序列)',
        'activation': '自动激活 — 从60日K线提取T-1收盘→T开盘收益序列',
    },
    'intraday_rev': {
        'display': '日内反转强度',
        'tdx_source': 'tdx_quotes (day_low_pct + day_close_pct)',
        'activation': '自动激活 — 从日内高低价计算',
    },
    'auction_sig': {
        'display': '集合竞价信号',
        'tdx_source': 'tdx_kline(period=0, 1分钟K线, 14:57-15:00)',
        'activation': '需1分钟K线 — 从最后3根1分钟K线提取竞价信号',
    },
    'sector_alpha': {
        'display': '板块Alpha',
        'tdx_source': 'tdx_screener (板块涨幅) 或 tdx_api_data(板块数据)',
        'activation': '需板块数据 — 从tdx_screener获取个股所属板块涨幅',
    },
    'streak_exp': {
        'display': '连板预期',
        'tdx_source': 'tdx_screener (涨停筛选, consecutive_days)',
        'activation': '需涨停筛选 — 从tdx_screener获取连板天数',
    },
    'flow_accel': {
        'display': '资金流入加速度',
        'tdx_source': 'tdx_kline(period=0, 1分钟K线, 尾盘30根)',
        'activation': '自动激活 — 从尾盘1分钟量价序列计算',
    },
    'gap_fill_prob': {
        'display': '跳空回补概率',
        'tdx_source': 'tdx_kline(period=7, 60日K线跳空统计)',
        'activation': '自动激活 — 从历史K线统计跳空回补率',
    },
    'event_decay': {
        'display': '事件衰减因子',
        'tdx_source': 'wenda_news_query + wenda_notice_query (新闻/公告)',
        'activation': '需新闻公告 — 从wenda_news/notice提取事件信号',
    },
    'lhb_effect': {
        'display': '龙虎榜效应',
        'tdx_source': 'tdx_api_data(fixedTag=jglhb, 龙虎榜数据)',
        'activation': '需龙虎榜 — 从tdx_api_data获取买卖席位数据',
    },
    'sentiment_trans': {
        'display': '市场情绪传导',
        'tdx_source': 'tdx_quotes(指数代码, 市场代理) + 个股Beta',
        'activation': '需指数数据 — 从指数行情计算市场情绪',
    },
}


# ═══════════════════════════════════════════════════════════════
# SECTION 1: Agent TDX数据采集指令生成器
# ═══════════════════════════════════════════════════════════════

class TDXFetchInstructionGenerator:
    """
    为每个M57因子生成精确的TDX MCP调用指令

    Agent按照这些指令调用TDX MCP工具，获取数据后保存到缓存JSON
    """

    @staticmethod
    def generate_fetch_instructions(
        stock_list: List[Dict],
        fetch_tier: str = 'all',
    ) -> List[Dict]:
        """
        生成TDX数据采集指令列表

        Args:
            stock_list: [{code, name, setcode}, ...]
            fetch_tier: 'tier1'|'tier2'|'tier3'|'all'

        Returns:
            [{tool, params, purpose, factor_target}, ...]
        """
        instructions = []

        for stock in stock_list:
            code = str(stock.get('code', ''))
            setcode = stock.get('setcode', '')
            if not setcode:
                from V13_2_TDX_RealtimeFeed import infer_setcode
                setcode = infer_setcode(code)

            # ── Tier1: 实时行情 ──
            if fetch_tier in ('tier1', 'all'):
                instructions.append({
                    'tool': 'tdx_quotes',
                    'params': {'code': code, 'setcode': setcode},
                    'purpose': f'获取{stock.get("name",code)}实时行情',
                    'factor_target': ['tail_rs', 'tail_vol_struct', 'intraday_rev'],
                    'tier': 1,
                })
                instructions.append({
                    'tool': 'tdx_kline',
                    'params': {'code': code, 'setcode': setcode, 'period': 7, 'count': 60},
                    'purpose': f'获取{code} 60日K线（隔夜动量+跳空回补）',
                    'factor_target': ['overnight_mom', 'gap_fill_prob'],
                    'tier': 1,
                })
                instructions.append({
                    'tool': 'tdx_kline',
                    'params': {'code': code, 'setcode': setcode, 'period': 0, 'count': 30},
                    'purpose': f'获取{code} 1分钟K线（尾盘30根）',
                    'factor_target': ['flow_accel', 'auction_sig', 'tail_vol_struct'],
                    'tier': 1,
                })

            # ── Tier2: 增强数据 ──
            if fetch_tier in ('tier2', 'all'):
                instructions.append({
                    'tool': 'tdx_api_data',
                    'params': {'code': code, 'setcode': setcode, 'fixedTag': 'zjlx'},
                    'purpose': f'获取{code}资金流向',
                    'factor_target': ['flow_accel(增强)'],
                    'tier': 2,
                })
                instructions.append({
                    'tool': 'tdx_api_data',
                    'params': {'code': code, 'setcode': setcode, 'fixedTag': 'jglhb'},
                    'purpose': f'获取{code}龙虎榜数据',
                    'factor_target': ['lhb_effect'],
                    'tier': 2,
                })
                instructions.append({
                    'tool': 'tdx_api_data',
                    'params': {'code': code, 'setcode': setcode, 'fixedTag': 'ztfx'},
                    'purpose': f'获取{code}涨停分析',
                    'factor_target': ['streak_exp', 'event_decay'],
                    'tier': 2,
                })

            # ── Tier3: 深度研究 ──
            if fetch_tier in ('tier3', 'all'):
                instructions.append({
                    'tool': 'wenda_news_query',
                    'params': {'query': code, 'count': 10},
                    'purpose': f'获取{code}近期新闻',
                    'factor_target': ['event_decay'],
                    'tier': 3,
                })
                instructions.append({
                    'tool': 'wenda_notice_query',
                    'params': {'query': code, 'count': 10},
                    'purpose': f'获取{code}公司公告',
                    'factor_target': ['event_decay'],
                    'tier': 3,
                })

        # 全市场数据（只需获取一次）
        if fetch_tier in ('tier1', 'all'):
            instructions.append({
                'tool': 'tdx_screener',
                'params': {'query': '涨停', 'market': 'A股'},
                'purpose': '获取全市场涨停股筛选（连板天数+板块）',
                'factor_target': ['streak_exp', 'sector_alpha'],
                'tier': 1,
                'is_market_wide': True,
            })

        if fetch_tier in ('tier2', 'all'):
            # 市场指数行情（用于sentiment_trans）
            instructions.append({
                'tool': 'tdx_quotes',
                'params': {'code': '000001', 'setcode': '1'},
                'purpose': '获取上证指数行情（市场情绪代理）',
                'factor_target': ['sentiment_trans'],
                'tier': 2,
                'is_market_wide': True,
            })

        return instructions

    @staticmethod
    def format_as_agent_prompt(instructions: List[Dict]) -> str:
        """将指令列表格式化为Agent可执行的prompt"""
        lines = [
            "# TDX数据采集任务",
            f"## 共 {len(instructions)} 条采集指令",
            f"## 生成时间: {datetime.now().isoformat()}",
            "",
            "请按顺序执行以下TDX MCP工具调用，将结果保存到缓存JSON文件：",
            "",
        ]

        for i, inst in enumerate(instructions, 1):
            params_str = ', '.join(f'{k}="{v}"' if isinstance(v, str) else f'{k}={v}'
                                   for k, v in inst['params'].items())
            lines.append(f"### {i}. {inst['tool']}({params_str})")
            lines.append(f"  目的: {inst['purpose']}")
            lines.append(f"  激活因子: {', '.join(inst['factor_target'])}")
            lines.append(f"  数据层级: Tier{inst.get('tier', '?')}")
            if inst.get('is_market_wide'):
                lines.append(f"  ⚠️ 全市场数据，只需获取一次")
            lines.append("")

        lines.append("## 缓存JSON格式")
        lines.append("```json")
        lines.append(json.dumps({
            "fetch_time": datetime.now().isoformat(),
            "stocks": {
                "CODE": {
                    "name": "股票名称",
                    "setcode": "1",
                    "quote": "{}  // tdx_quotes返回",
                    "kline": "{}  // tdx_kline日K线返回",
                    "kline_1min": "{}  // tdx_kline 1分钟返回",
                    "capital_flow": "{}  // tdx_api_data zjlx",
                    "dragon_tiger": "{}  // tdx_api_data jglhb",
                    "limit_up_analysis": "{}  // tdx_api_data ztfx",
                    "news": "[]  // wenda_news_query返回",
                    "notices": "[]  // wenda_notice_query返回",
                }
            },
            "screener": "{}  // tdx_screener返回",
            "market_index": "{}  // 指数行情"
        }, indent=2, ensure_ascii=False))
        lines.append("```")

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 2: M57因子激活验证器
# ═══════════════════════════════════════════════════════════════

class M57FactorValidator:
    """
    验证M57 12因子的激活状态

    检查每个因子是否使用了真实TDX数据（而非默认值）
    """

    # 因子默认值（如果因子值等于默认值，说明未激活）
    DEFAULT_VALUES = {
        'tail_rs': 0.0,
        'tail_vol_struct': 0.0,
        'overnight_mom': 0.0,      # 如果为0，说明没有隔夜收益数据
        'intraday_rev': 0.0,
        'auction_sig': 0.0,        # 如果为0，说明没有1分钟K线
        'sector_alpha': 0.0,       # 如果为0，说明没有板块数据
        'streak_exp': 0.0,         # 如果为0，说明不是连板股
        'flow_accel': 0.0,
        'gap_fill_prob': 0.5,      # 默认50%回补率
        'event_decay': 0.0,        # 如果为0，说明没有事件信号
        'lhb_effect': 0.0,         # 如果为0，说明没有龙虎榜数据
        'sentiment_trans': 0.0,    # 如果为0，说明没有市场数据
    }

    @classmethod
    def validate_factors(cls, factor_dict: Dict) -> List[FactorActivationStatus]:
        """
        验证因子激活状态

        Args:
            factor_dict: {factor_name: value, ...}

        Returns:
            List[FactorActivationStatus]
        """
        statuses = []

        for fname, default_val in cls.DEFAULT_VALUES.items():
            actual_val = factor_dict.get(fname, default_val)
            meta = FACTOR_METADATA.get(fname, {})

            # 判断是否激活（值不等于默认值）
            is_active = abs(actual_val - default_val) > 1e-6

            status = FactorActivationStatus(
                name=fname,
                display_name=meta.get('display', fname),
                tdx_source=meta.get('tdx_source', ''),
                current_value=actual_val,
                is_active=is_active,
                is_dormant=not is_active,
                activation_method=meta.get('activation', ''),
                dormant_reason=cls._diagnose_dormant(fname, actual_val, factor_dict) if not is_active else '',
            )
            statuses.append(status)

        return statuses

    @staticmethod
    def _diagnose_dormant(factor_name: str, value: float, all_factors: Dict) -> str:
        """诊断因子休眠原因"""
        reasons = {
            'overnight_mom': '缺少日K线开盘价数据 — 确保tdx_kline(period=7)返回包含open字段',
            'auction_sig': '缺少1分钟K线数据 — 需tdx_kline(period=0, count=30)获取14:30-15:00数据',
            'sector_alpha': '缺少板块涨幅数据 — 需tdx_screener或手动指定sector_intraday_change',
            'streak_exp': '该股非连板股(consecutive_limit_up=0) — 如果是涨停股，需tdx_screener确认连板天数',
            'event_decay': '缺少新闻/公告事件信号 — 需wenda_news_query/wenda_notice_query获取近期事件',
            'lhb_effect': '缺少龙虎榜数据 — 需tdx_api_data(fixedTag=jglhb)获取席位数据',
            'sentiment_trans': '缺少市场指数数据 — 需tdx_quotes获取上证指数行情作为市场代理',
        }
        return reasons.get(factor_name, f'因子值={value}，等于默认值，未激活')

    @classmethod
    def generate_activation_report(cls, factor_dict: Dict) -> str:
        """生成因子激活报告"""
        statuses = cls.validate_factors(factor_dict)

        active_count = sum(1 for s in statuses if s.is_active)
        dormant_count = sum(1 for s in statuses if s.is_dormant)

        lines = [
            '=' * 70,
            '  M57 隔夜Alpha因子激活报告',
            f'  时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'  激活: {active_count}/12 | 休眠: {dormant_count}/12',
            '=' * 70,
            '',
            f'{"因子":<20s} {"值":>10s} {"状态":>6s} {"数据源"}',
            '-' * 70,
        ]

        for s in statuses:
            status_icon = '✅' if s.is_active else '❌'
            lines.append(
                f'{s.display_name:<18s} {s.current_value:>10.4f} {status_icon:>4s}  '
                f'{s.tdx_source[:40]}'
            )

        # 休眠因子诊断
        dormant = [s for s in statuses if s.is_dormant]
        if dormant:
            lines.append(f'\n{"─" * 70}')
            lines.append('休眠因子诊断:')
            lines.append(f'{"─" * 70}')
            for s in dormant:
                lines.append(f'\n  ❌ {s.display_name} ({s.name})')
                lines.append(f'     原因: {s.dormant_reason}')
                lines.append(f'     激活: {s.activation_method}')

        lines.append(f'\n{"=" * 70}')
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 3: TDX Alpha增强主引擎
# ═══════════════════════════════════════════════════════════════

class TDXAlphaEnricher:
    """
    V13.2 TDX Alpha因子增强主引擎

    整合TDXRealtimeFeed + M57因子验证 + Agent指令生成

    使用方式：
        enricher = TDXAlphaEnricher()
        enricher.load_cache('data/tdx_1430_cache.json')
        report = enricher.enrich_and_validate(stock_list)
    """

    def __init__(self, cache_dir: str = ''):
        self.cache_dir = cache_dir
        self.feed = None
        self._init_feed()

    def _init_feed(self):
        """初始化TDXRealtimeFeed"""
        try:
            from V13_2_TDX_RealtimeFeed import TDXRealtimeFeed
            self.feed = TDXRealtimeFeed(cache_dir=self.cache_dir)
        except ImportError as e:
            print(f"[Enricher] TDXRealtimeFeed导入失败: {e}")

    def load_cache(self, filename: str) -> bool:
        """加载TDX缓存数据"""
        if self.feed:
            return self.feed.load_from_cache(filename)
        return False

    def enrich_and_validate(
        self,
        stock_list: List[Dict],
        run_analysis: bool = True,
    ) -> Dict:
        """
        执行完整的Alpha增强和验证流程

        1. 构建stock_data_map
        2. 构建M57因子输入
        3. 运行M57引擎
        4. 验证12因子激活状态
        5. 生成报告

        Returns:
            {results, activation_report, stats, dormant_factors}
        """
        if not self.feed:
            return {'error': 'TDXRealtimeFeed未初始化'}

        # Step 1: 构建stock_data_map
        stock_data_map = self.feed.build_stock_data_map(stock_list)

        # Step 2: 构建M57输入
        m57_inputs = self.feed.build_all_m57_inputs(stock_list)

        # Step 3: 运行M57引擎
        m57_results = {}
        if run_analysis:
            try:
                from V13_1_M57_OvernightAlphaEngine import OvernightAlphaEngine
                engine = OvernightAlphaEngine()

                factor_list = []
                for code, inputs in m57_inputs.items():
                    try:
                        factors = engine.compute_all_factors(**inputs)
                        factor_list.append(factors)
                    except Exception as e:
                        print(f"[Enricher] {code} M57计算失败: {e}")

                if factor_list:
                    factor_list = engine.batch_evaluate(factor_list)
                    for f in factor_list:
                        m57_results[f.code] = {
                            'tail_rs': f.tail_rs,
                            'tail_vol_struct': f.tail_vol_struct,
                            'overnight_mom': f.overnight_mom,
                            'intraday_rev': f.intraday_rev,
                            'auction_sig': f.auction_sig,
                            'sector_alpha': f.sector_alpha,
                            'streak_exp': f.streak_exp,
                            'flow_accel': f.flow_accel,
                            'gap_fill_prob': f.gap_fill_prob,
                            'event_decay': f.event_decay,
                            'lhb_effect': f.lhb_effect,
                            'sentiment_trans': f.sentiment_trans,
                            'composite_score': f.composite_score,
                            't1_return_forecast': f.t1_return_forecast,
                        }
            except ImportError as e:
                print(f"[Enricher] M57引擎不可用: {e}")

        # Step 4: 验证因子激活状态
        activation_reports = {}
        all_dormant = set()

        for code, factors in m57_results.items():
            # 过滤掉非因子字段
            factor_only = {k: v for k, v in factors.items()
                          if k not in ('composite_score', 't1_return_forecast')}
            statuses = M57FactorValidator.validate_factors(factor_only)
            activation_reports[code] = statuses

            # 收集休眠因子
            for s in statuses:
                if s.is_dormant:
                    all_dormant.add(s.name)

        # Step 5: 统计
        total = len(m57_results)
        factor_activation_count = {}
        for fname in FACTOR_METADATA:
            active = sum(1 for code, statuses in activation_reports.items()
                        for s in statuses if s.name == fname and s.is_active)
            factor_activation_count[fname] = {
                'active': active,
                'total': total,
                'rate': active / total if total > 0 else 0,
            }

        return {
            'results': m57_results,
            'activation_reports': activation_reports,
            'factor_activation_count': factor_activation_count,
            'dormant_factors': list(all_dormant),
            'stats': {
                'total_stocks': total,
                'avg_active_factors': sum(
                    sum(1 for s in statuses if s.is_active)
                    for statuses in activation_reports.values()
                ) / max(1, total),
                'fully_active_stocks': sum(
                    1 for statuses in activation_reports.values()
                    if all(s.is_active for s in statuses)
                ),
            },
        }

    def generate_fetch_instructions(
        self,
        stock_list: List[Dict],
        tier: str = 'all',
    ) -> str:
        """生成Agent TDX数据采集指令（prompt格式）"""
        instructions = TDXFetchInstructionGenerator.generate_fetch_instructions(
            stock_list, fetch_tier=tier)
        return TDXFetchInstructionGenerator.format_as_agent_prompt(instructions)

    def generate_enrichment_report(self, stock_list: List[Dict]) -> str:
        """生成完整的Alpha增强报告"""
        result = self.enrich_and_validate(stock_list, run_analysis=True)

        lines = [
            '=' * 70,
            '  V13.2 TDX Alpha因子增强报告',
            f'  时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'  股票数: {result["stats"]["total_stocks"]}',
            '=' * 70,
            '',
            '── 12因子激活率 ──',
            f'{"因子":<20s} {"激活/总数":>10s} {"激活率":>8s}  进度条',
            '-' * 70,
        ]

        for fname, meta in FACTOR_METADATA.items():
            count = result['factor_activation_count'].get(fname, {})
            active = count.get('active', 0)
            total = count.get('total', 0)
            rate = count.get('rate', 0)
            bar_len = int(rate * 20)
            bar = '█' * bar_len + '░' * (20 - bar_len)
            lines.append(
                f'{meta["display"]:<18s} {active:>5d}/{total:<4d} {rate*100:>6.1f}%  {bar}'
            )

        lines.append(f'\n── 统计 ──')
        lines.append(f'  平均激活因子数: {result["stats"]["avg_active_factors"]:.1f}/12')
        lines.append(f'  完全激活股票数: {result["stats"]["fully_active_stocks"]}')

        dormant = result['dormant_factors']
        if dormant:
            lines.append(f'\n── 休眠因子 ({len(dormant)}个) ──')
            for fname in dormant:
                meta = FACTOR_METADATA.get(fname, {})
                lines.append(f'  ❌ {meta.get("display", fname)}: {meta.get("tdx_source", "")}')
                lines.append(f'     激活方法: {meta.get("activation", "")}')

        # Top 5 Alpha评分
        top5 = sorted(result['results'].items(),
                      key=lambda x: x[1].get('composite_score', 0),
                      reverse=True)[:5]
        if top5:
            lines.append(f'\n── Top 5 Alpha评分 ──')
            for code, factors in top5:
                lines.append(
                    f'  {code}: composite={factors.get("composite_score",0):+.4f} | '
                    f'T+1={factors.get("t1_return_forecast",0):+.3f}%'
                )

        lines.append(f'\n{"=" * 70}')
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 导出
# ═══════════════════════════════════════════════════════════════

__all__ = [
    'TDXAlphaEnricher',
    'TDXFetchInstructionGenerator',
    'M57FactorValidator',
    'FactorActivationStatus',
    'FACTOR_METADATA',
]


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='V13.2 TDX Alpha因子激活器')
    parser.add_argument('--cache', default='tdx_realtime_input.json', help='缓存JSON文件')
    parser.add_argument('--instructions', action='store_true', help='生成Agent采集指令')
    parser.add_argument('--tier', default='all', choices=['tier1', 'tier2', 'tier3', 'all'])
    args = parser.parse_args()

    enricher = TDXAlphaEnricher()

    if args.instructions:
        # 生成采集指令
        if enricher.load_cache(args.cache):
            stock_list = [{'code': c, 'name': s.name, 'setcode': s.setcode}
                         for c, s in enricher.feed.stocks.items()][:5]
        else:
            stock_list = [
                {'code': '600519', 'name': '贵州茅台', 'setcode': '1'},
                {'code': '300750', 'name': '宁德时代', 'setcode': '0'},
            ]
        print(enricher.generate_fetch_instructions(stock_list, tier=args.tier))
    else:
        # 运行增强报告
        if enricher.load_cache(args.cache):
            stock_list = [{'code': c, 'name': s.name} for c, s in enricher.feed.stocks.items()]
            print(enricher.generate_enrichment_report(stock_list))
        else:
            print("缓存加载失败")
