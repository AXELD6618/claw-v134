#!/usr/bin/env python3
"""
V13.0 TDX实盘数据桥接器
========================
Agent（WorkBuddy）通过此模块将 TDX MCP 查询结果注入 V13.0 引擎。

用法（Agent端）:
  1. from V13_0_TdxBridge import TdxBridge
  2. bridge = TdxBridge()
  3. bridge.add_screener_results([{code, name, ...}, ...])  # 从tdx_screener
  4. bridge.add_per_stock_data(code, quote, kline_daily, kline_weekly, indicator)
  5. bridge.save_cache()  # 写入 data/tdx_realtime_input.json
  6. bridge.run_orchestrator()  # 或手动 python V13_0_Orchestrator.py

或直接用便捷函数:
  from V13_0_TdxBridge import inject_and_run
  inject_and_run(screening_results, per_stock_map)

数据流:
  TDX MCP → TdxBridge(收集) → DataPipeline(标准化) → 缓存JSON → Orchestrator(消费)
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Any


class TdxBridge:
    """
    TDX→V13.0 数据桥接器

    用于Agent将TDX MCP查询结果桥接到Orchestrator引擎。
    """

    def __init__(self, cache_dir: str = 'data', verbose: bool = True):
        self.cache_dir = cache_dir
        self.verbose = verbose
        self.candidates: List[dict] = []
        self.per_stock_data: Dict[str, dict] = {}  # code → {quote, kline, ..., name}
        os.makedirs(cache_dir, exist_ok=True)

    def _log(self, msg: str):
        if self.verbose:
            print(f"[TdxBridge] {msg}")

    # ═══════════════════════════════════════════════
    # 数据录入
    # ═══════════════════════════════════════════════

    def add_screener_results(self, results: List[dict], source: str = 'tdx_screener'):
        """
        添加TDX选股筛选结果

        results: [{code, name, changePct, turnoverRate, ...}, ...]
        """
        count = 0
        for item in results:
            code = item.get('code', '')
            name = item.get('name', '')
            if not code or not name:
                continue

            key = str(code)
            if key not in self.per_stock_data:
                self.per_stock_data[key] = {
                    'code': code,
                    'name': name,
                    'source': source,
                    'screener_meta': {
                        k: v for k, v in item.items()
                        if k not in ('code', 'name')
                    },
                }
                count += 1
            else:
                # 合并不同筛选源的数据
                self.per_stock_data[key]['source'] += f',{source}'

        self._log(f"   📥 录入筛选候选 {count}只 (来源: {source})")

    def add_quote(self, code: str, raw_response: dict):
        """录入实时行情"""
        key = str(code)
        if key in self.per_stock_data:
            self.per_stock_data[key]['quote'] = raw_response
        else:
            self.per_stock_data[key] = {
                'code': code, 'name': '', 'quote': raw_response
            }

    def add_kline_daily(self, code: str, raw_response: dict):
        """录入日K线"""
        key = str(code)
        if key in self.per_stock_data:
            self.per_stock_data[key]['kline'] = raw_response
        else:
            self.per_stock_data[key] = {
                'code': code, 'name': '', 'kline': raw_response
            }

    def add_kline_weekly(self, code: str, raw_response: dict):
        """录入周K线"""
        key = str(code)
        if key in self.per_stock_data:
            self.per_stock_data[key]['weekly_kline'] = raw_response
        else:
            self.per_stock_data[key] = {
                'code': code, 'name': '', 'weekly_kline': raw_response
            }

    def add_indicator(self, code: str, raw_response: dict):
        """录入财务指标"""
        key = str(code)
        if key in self.per_stock_data:
            self.per_stock_data[key]['indicator'] = raw_response
        else:
            self.per_stock_data[key] = {
                'code': code, 'name': '', 'indicator': raw_response
            }

    def add_per_stock_data(self, code: str, name: str = '',
                           quote: dict = None, kline: dict = None,
                           weekly_kline: dict = None, indicator: dict = None,
                           news: dict = None, notice: dict = None,
                           sector: str = ''):
        """一次性录入单只股票的全部数据"""
        key = str(code)
        entry = self.per_stock_data.get(key, {})
        entry['code'] = code
        if name:
            entry['name'] = name
        if quote:
            entry['quote'] = quote
        if kline:
            entry['kline'] = kline
        if weekly_kline:
            entry['weekly_kline'] = weekly_kline
        if indicator:
            entry['indicator'] = indicator
        if news:
            entry['news'] = news
        if notice:
            entry['notice'] = notice
        if sector:
            entry['sector'] = sector
        self.per_stock_data[key] = entry

    # ═══════════════════════════════════════════════
    # 缓存和消费
    # ═══════════════════════════════════════════════

    def build_candidates(self) -> List[dict]:
        """组装候选列表为DataPipeline可消费的格式"""
        return [
            {
                'code': data.get('code', ''),
                'name': data.get('name', ''),
                'quote': data.get('quote'),
                'kline': data.get('kline'),
                'weekly_kline': data.get('weekly_kline'),
                'indicator': data.get('indicator'),
                'news': data.get('news'),
                'notice': data.get('notice'),
                'sector': data.get('sector', ''),
            }
            for data in self.per_stock_data.values()
            if data.get('code')
        ]

    def save_cache(self, cache_file: str = 'tdx_realtime_input.json') -> str:
        """
        保存TDX实盘数据缓存

        返回缓存文件完整路径
        """
        candidates = self.build_candidates()

        cache_data = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': datetime.now().isoformat(),
            'source': 'TDX_MCP',
            'candidate_count': len(candidates),
            'candidates': candidates,
        }

        cache_path = os.path.join(self.cache_dir, cache_file)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)

        self._log(f"   💾 缓存已保存: {cache_path} ({len(candidates)}只)")
        return cache_path

    def run_orchestrator(self) -> dict:
        """保存缓存并调用Orchestrator执行全链路"""
        cache_path = self.save_cache()

        # 动态导入Orchestrator
        try:
            from V13_0_Orchestrator import V13Orchestrator, OrchestratorConfig

            config = OrchestratorConfig(
                data_mode='tdx_real',
                verbose=True,
            )
            orch = V13Orchestrator(config)
            report = orch.run_daily_tail_market()
            return report
        except Exception as e:
            self._log(f"   ❌ Orchestrator执行失败: {e}")

            # 兜底：直接用 argparse 方式
            import subprocess
            result = subprocess.run(
                [sys.executable, 'V13_0_Orchestrator.py', '--tdx-file', cache_path],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                print(result.stdout)
            else:
                print(result.stderr)

            return {'success': False, 'error': str(e)}

    def get_stats(self) -> dict:
        """获取当前桥接器状态"""
        candidates = self.build_candidates()
        with_quotes = sum(1 for c in candidates if c.get('quote'))
        with_kline = sum(1 for c in candidates if c.get('kline'))
        with_indicator = sum(1 for c in candidates if c.get('indicator'))

        return {
            'total_candidates': len(candidates),
            'with_quotes': with_quotes,
            'with_kline': with_kline,
            'with_indicator': with_indicator,
            'completeness': {
                'quotes': f'{with_quotes}/{len(candidates)}' if candidates else '0/0',
                'kline': f'{with_kline}/{len(candidates)}' if candidates else '0/0',
                'indicator': f'{with_indicator}/{len(candidates)}' if candidates else '0/0',
            },
        }


# ═══════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════

def inject_and_run(screening_results: List[dict],
                   per_stock_map: Dict[str, dict] = None,
                   cache_dir: str = 'data') -> dict:
    """
    一键注入TDX数据并运行全链路

    Args:
        screening_results: tdx_screener返回的股票列表
        per_stock_map: {code: {quote, kline, weekly_kline, indicator, name, ...}}
        cache_dir: 缓存目录

    Returns:
        Orchestrator执行报告
    """
    bridge = TdxBridge(cache_dir=cache_dir)
    bridge.add_screener_results(screening_results)

    if per_stock_map:
        for code, data in per_stock_map.items():
            bridge.add_per_stock_data(
                code=code,
                name=data.get('name', ''),
                quote=data.get('quote'),
                kline=data.get('kline'),
                weekly_kline=data.get('weekly_kline'),
                indicator=data.get('indicator'),
                news=data.get('news'),
                notice=data.get('notice'),
                sector=data.get('sector', ''),
            )

    return bridge.run_orchestrator()


def build_cache_from_mcp(screening_results: List[dict],
                         per_stock_map: Dict[str, dict],
                         cache_dir: str = 'data') -> str:
    """仅构建缓存，不运行Orchestrator"""
    bridge = TdxBridge(cache_dir=cache_dir)
    bridge.add_screener_results(screening_results)

    if per_stock_map:
        for code, data in per_stock_map.items():
            bridge.add_per_stock_data(
                code=code,
                name=data.get('name', ''),
                quote=data.get('quote'),
                kline=data.get('kline'),
                weekly_kline=data.get('weekly_kline'),
                indicator=data.get('indicator'),
                news=data.get('news'),
                notice=data.get('notice'),
                sector=data.get('sector', ''),
            )

    return bridge.save_cache()


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 TDX数据桥接器 自测")
    print("=" * 60)

    # 模拟TDX MCP返回数据
    mock_screening = [
        {'code': '002415', 'name': '海康威视', 'changePct': 3.8,
         'turnoverRate': 6.5, 'price': 29.50},
        {'code': '603019', 'name': '中科曙光', 'changePct': 4.2,
         'turnoverRate': 8.1, 'price': 58.30},
        {'code': '300750', 'name': '宁德时代', 'changePct': 2.9,
         'turnoverRate': 3.8, 'price': 215.00},
    ]

    mock_quote = {'response': {'transformed': {
        'hq': {'price': 29.50, 'open': 28.80, 'high': 29.80, 'low': 28.60,
               'changePct': 3.8, 'volume': 50000000, 'amount': 1450000000},
        'ext': {'turnoverRate': 6.5, 'liutongCap': 1.2e11},
        'pro': {'liangBi': 1.8, 'weiBi': 0.35},
        'calc': {'bigOrderRatio': 32.5, 'bigOrderNet': 8500000,
                 'tailVolumeRatio': 28.0, 'aboveAvgLine': True},
    }}}

    mock_kline = {'response': {'transformed': {
        'klines': [{'open': 25+i*0.1, 'high': 26+i*0.1, 'low': 24.5+i*0.1,
                     'close': 25.3+i*0.1, 'volume': 5e7+i*1e6, 'amount': 1.3e9}
                    for i in range(150)]
    }}}

    bridge = TdxBridge()
    bridge.add_screener_results(mock_screening)
    bridge.add_per_stock_data('002415', '海康威视',
                              quote=mock_quote, kline=mock_kline,
                              weekly_kline=mock_kline,
                              indicator={'response': {'transformed': {
                                  'pe': 45.5, 'pb': 6.8, 'ROE': 15.2,
                                  '净利润增长率': 25.3, '资产负债率': 42.5
                              }}},
                              sector='AI算力')

    stats = bridge.get_stats()
    print(f"\n📊 桥接器状态:")
    print(f"   候选总数: {stats['total_candidates']}")
    print(f"   有行情: {stats['completeness']['quotes']}")
    print(f"   有K线: {stats['completeness']['kline']}")
    print(f"   有财务: {stats['completeness']['indicator']}")

    path = bridge.save_cache()
    print(f"\n✅ 缓存已保存: {path}")
    print(f"🎉 V13.0 TDX数据桥接器 自测通过！")
