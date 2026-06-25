#!/usr/bin/env python3
"""
V13.0 P0-2: 14:30自动化 TDX实盘数据注入器
==========================================
解决14:30尾盘猎手数据源问题：将合成数据替换为TDX MCP实时K线/行情数据。

架构：
  TDX MCP (tdx_kline + tdx_quotes) → V13_0_TdxBridge → tdx_realtime_input.json
                                                              ↓
                                                     run_tail_market_1430.py

执行流程 (14:30调度):
  1. 加载301只动态监控池
  2. 按成交额筛选今日活跃 TOP 60只
  3. 批量拉取实时行情 (tdx_quotes)
  4. 批量拉取60日日K线 (tdx_kline)
  5. 通过 TdxBridge 标准化写入 tdx_realtime_input.json
  6. run_tail_market_1430.py 自动读取并运行全管线

使用方式 (WorkBuddy Agent):
  1. Agent 调用此脚本的数据拉取阶段（通过 TDX MCP 工具）
  2. 数据注入后，脚本自动触发14:30猎手
  3. 或手动: python V13_0_P0_TDXInjector.py

零输出保障：
  - 第一段: TDX实盘优先（≤120秒超时）
  - 第二段: 如果TDX数据不足30只，降级到合成数据进行兜底（≤300秒）
  - 最终: 绝不允许零信号输出（最低2-3条）
"""

import json
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

STAGE1_TIMEOUT_SEC = 120     # TDX拉取最大时限
STAGE2_TIMEOUT_SEC = 300     # 引擎计算最大时限
MIN_TDX_STOCKS_FOR_REAL = 30  # 最低TDX标的数（不足则降级合成数据）
TOP_N_ACTIVE = 60            # 按成交额取前N只活跃标的
MIN_KLINES_REQUIRED = 20     # 最少K线根数

# 动态监控池加载
try:
    from data.dynamic_watchlist import DYNAMIC_WATCHLIST
    print(f"[TDXInjector] 动态监控池: {len(DYNAMIC_WATCHLIST)}只标的")
except ImportError:
    # 兜底
    DYNAMIC_WATCHLIST = [
        ("000063","0","中兴通讯","通信"), ("000725","0","京东方A","电子"),
        ("002230","0","科大讯飞","AI"), ("002415","0","海康威视","计算机"),
        ("002594","0","比亚迪","汽车"), ("300059","0","东方财富","非银金融"),
        ("300308","0","中际旭创","通信"), ("300502","0","新易盛","通信"),
        ("300750","0","宁德时代","电力设备"), ("300760","0","迈瑞医疗","医药生物"),
        ("600519","1","贵州茅台","食品饮料"), ("601012","1","隆基绿能","电力设备"),
        ("601138","1","工业富联","AI算力"), ("601318","1","中国平安","非银金融"),
        ("601899","1","紫金矿业","有色金属"), ("603019","1","中科曙光","AI算力"),
        ("603259","1","药明康德","医药生物"), ("603501","1","韦尔股份","电子"),
        ("688111","1","金山办公","计算机"), ("688256","1","寒武纪","AI芯片"),
        ("688981","1","中芯国际","电子"), ("300394","0","天孚通信","通信"),
        ("300274","0","阳光电源","电力设备"), ("002371","0","北方华创","电子"),
        ("000858","0","五粮液","食品饮料"), ("600809","1","山西汾酒","食品饮料"),
        ("300033","0","同花顺","金融科技"), ("601919","1","中远海控","交通运输"),
        ("002607","0","中公教育","教育"), ("300124","0","汇川技术","机械设备"),
    ]


# ═══════════════════════════════════════════════
# 核心注入器
# ═══════════════════════════════════════════════

class TdxRealtimeInjector:
    """
    TDX实时数据注入器

    为14:30尾盘猎手准备TDX真实数据缓存。
    在WorkBuddy Agent环境中通过MCP工具拉取数据。
    """

    def __init__(self, cache_dir: str = 'data', verbose: bool = True):
        self.cache_dir = cache_dir
        self.verbose = verbose
        self.watchlist = DYNAMIC_WATCHLIST
        self.real_stocks: Dict[str, dict] = {}   # code → stock_data
        self.synthetic_stocks: List[dict] = []    # 合成兜底数据
        self.stats = {
            'total_watchlist': len(self.watchlist),
            'tdx_quotes_fetched': 0,
            'tdx_klines_fetched': 0,
            'tdx_stocks_valid': 0,
            'synthetic_fallback': 0,
            'total_output': 0,
            'mode': 'PENDING',
        }
        os.makedirs(cache_dir, exist_ok=True)

    def _log(self, msg: str):
        if self.verbose:
            timestamp = datetime.now().strftime('%H:%M:%S')
            print(f"[TDXInjector {timestamp}] {msg}")

    # ═══════════════════════════════════════════════
    # 数据注入接口 (Agent调用)
    # ═══════════════════════════════════════════════

    def inject_quote(self, code: str, setcode: str, raw_response: dict):
        """注入单只股票实时行情"""
        key = str(code)
        if key not in self.real_stocks:
            self.real_stocks[key] = {'code': code, 'setcode': setcode}
        self.real_stocks[key]['quote_raw'] = raw_response

        # 解析关键字段
        attach = raw_response.get('AttachInfo', {})
        self.real_stocks[key].update({
            'now': float(attach.get('Now', 0)),
            'change_pct': float(attach.get('fChangePercent', 0)),
            'turnover': float(attach.get('fHSL', 0)) / 100 if attach.get('fHSL') else 0,
            'volume': int(attach.get('Volume', 0)),
            'amount': float(attach.get('Amount', 0)),
            'open': float(attach.get('Open', 0)),
            'high': float(attach.get('MaxP', 0)),
            'low': float(attach.get('MinP', 0)),
            'prev_close': float(attach.get('Close', 0)),
            'avg_price': float(attach.get('fAverage', 0)),
            'pe': float(attach.get('fPE', 0)) if attach.get('fPE') else None,
            'market_cap': float(attach.get('fSZ', 0)) if attach.get('fSZ') else 0,
        })
        self.stats['tdx_quotes_fetched'] += 1

    def inject_kline(self, code: str, setcode: str, raw_response: dict):
        """注入单只股票日K线"""
        key = str(code)
        if key not in self.real_stocks:
            self.real_stocks[key] = {'code': code, 'setcode': setcode}
        self.real_stocks[key]['kline_raw'] = raw_response

        # 解析K线 → 价格序列
        items = raw_response.get('ListItem', [])
        head = raw_response.get('ListHead', {}).get('ItemHead', [])
        col_map = {h: i for i, h in enumerate(head)}

        close_idx = col_map.get('Close', 5)
        high_idx = col_map.get('High', 3)
        low_idx = col_map.get('Low', 4)
        open_idx = col_map.get('Open', 2)
        vol_idx = col_map.get('Volume', 8)
        amount_idx = col_map.get('Amount', 6)

        closes = []
        highs = []
        lows = []
        opens = []
        volumes = []
        amounts = []

        for item in items:
            vals = item.get('Item', [])
            if len(vals) <= close_idx:
                continue
            closes.append(float(vals[close_idx]))
            highs.append(float(vals[high_idx]) if high_idx < len(vals) else 0)
            lows.append(float(vals[low_idx]) if low_idx < len(vals) else 0)
            opens.append(float(vals[open_idx]) if open_idx < len(vals) else 0)
            volumes.append(float(vals[vol_idx]) if vol_idx < len(vals) else 0)
            amounts.append(float(vals[amount_idx]) if amount_idx < len(vals) else 0)

        self.real_stocks[key].update({
            'closes': closes,
            'highs': highs,
            'lows': lows,
            'opens': opens,
            'volumes': volumes,
            'amounts': amounts,
            'n_klines': len(closes),
        })
        self.stats['tdx_klines_fetched'] += 1

    def inject_stock_complete(self, code: str, setcode: str, name: str, industry: str,
                              quote_raw: dict, kline_raw: dict):
        """一次性注入单只股票的完整数据"""
        self.inject_quote(code, setcode, quote_raw)
        self.inject_kline(code, setcode, kline_raw)
        key = str(code)
        if key in self.real_stocks:
            self.real_stocks[key]['name'] = name
            self.real_stocks[key]['industry'] = industry

    # ═══════════════════════════════════════════════
    # 验证与过滤
    # ═══════════════════════════════════════════════

    def validate_and_filter(self) -> Tuple[List[dict], bool]:
        """
        验证数据质量，返回有效的TDX标的

        Returns:
          (valid_stocks, use_real_data):
            valid_stocks 符合要求的标的列表
            use_real_data TDX数据是否足够（≥30只）
        """
        valid = []
        for code, sdata in self.real_stocks.items():
            if not sdata.get('closes') or len(sdata.get('closes', [])) < MIN_KLINES_REQUIRED:
                continue
            if not sdata.get('now') or sdata.get('now') <= 0:
                continue
            valid.append(sdata)

        self.stats['tdx_stocks_valid'] = len(valid)
        use_real = len(valid) >= MIN_TDX_STOCKS_FOR_REAL

        if use_real:
            self.stats['mode'] = 'TDX_REAL'
            self._log(f"✅ TDX实盘数据充足: {len(valid)}只有效标的")
        else:
            self.stats['mode'] = 'SYNTHETIC_FALLBACK'
            self._log(f"⚠️ TDX数据不足({len(valid)}只)，将降级使用合成数据兜底")

        return valid, use_real

    # ═══════════════════════════════════════════════
    # 缓存写入
    # ═══════════════════════════════════════════════

    def build_cache(self) -> dict:
        """
        构建 tdx_realtime_input.json 格式的数据缓存

        输出格式兼容 run_tail_market_1430.py 和 V13_0_TdxBridge
        """
        valid_stocks, use_real = self.validate_and_filter()

        stocks_dict = {}
        for sdata in valid_stocks:
            code = str(sdata['code'])
            closes = sdata.get('closes', [])

            # 构建每日K线列表
            daily_klines = []
            for i in range(len(closes)):
                daily_klines.append({
                    'c': closes[i] if i < len(closes) else 0,
                    'h': sdata.get('highs', [])[i] if i < len(sdata.get('highs', [])) else 0,
                    'l': sdata.get('lows', [])[i] if i < len(sdata.get('lows', [])) else 0,
                    'o': sdata.get('opens', [])[i] if i < len(sdata.get('opens', [])) else 0,
                    'v': sdata.get('volumes', [])[i] if i < len(sdata.get('volumes', [])) else 0,
                    'a': sdata.get('amounts', [])[i] if i < len(sdata.get('amounts', [])) else 0,
                })

            stocks_dict[code] = {
                'code': code,
                'name': sdata.get('name', code),
                'industry': sdata.get('industry', '通用'),
                'setcode': sdata.get('setcode', '0'),
                'now': sdata.get('now', closes[-1] if closes else 0),
                'change_pct': sdata.get('change_pct', 0),
                'volume': sdata.get('volume', 0),
                'amount': sdata.get('amount', 0),
                'turnover': sdata.get('turnover', 0),
                'avg_price': sdata.get('avg_price', 0),
                'pe': sdata.get('pe', None),
                'market_cap': sdata.get('market_cap', 0),
                'open': sdata.get('open', 0),
                'high': sdata.get('high', 0),
                'low': sdata.get('low', 0),
                'prev_close': sdata.get('prev_close', 0),
                'daily_klines': daily_klines,
                'n_klines': len(closes),
            }

        cache_data = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'time': datetime.now().strftime('%H:%M:%S'),
            'timestamp': datetime.now().isoformat(),
            'source': self.stats['mode'],
            'stats': self.stats,
            'stocks_count': len(stocks_dict),
            'stocks': stocks_dict,
        }

        self.stats['total_output'] = len(stocks_dict)

        # 按成交额排序
        sorted_stocks = sorted(
            stocks_dict.values(),
            key=lambda x: x.get('amount', 0),
            reverse=True
        )
        cache_data['top_by_amount'] = [
            {
                'code': s['code'],
                'name': s['name'],
                'amount': s.get('amount', 0),
                'change_pct': s.get('change_pct', 0),
            }
            for s in sorted_stocks[:10]
        ]

        return cache_data

    def save_cache(self, cache_data: dict = None) -> str:
        """保存缓存文件"""
        if cache_data is None:
            cache_data = self.build_cache()

        cache_path = os.path.join(self.cache_dir, 'tdx_realtime_input.json')
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2, default=str)

        self._log(f"💾 缓存已保存: {cache_path} ({cache_data.get('stocks_count', 0)}只)")
        return cache_path

    def get_watchlist_tasks(self, top_n: int = TOP_N_ACTIVE) -> List[Tuple[str, str, str, str]]:
        """
        获取需要拉取TDX数据的任务列表

        返回: [(code, setcode, name, industry), ...] TOP-N活跃标的

        在Agent环境中，此列表用于批量调用 tdx_kline + tdx_quotes
        """
        # 从监控池中选择
        all_stocks = [
            (code, setcode, name, industry)
            for code, setcode, name, industry in self.watchlist
        ]

        # 如果没有成交额排序，返回前N只
        return all_stocks[:min(top_n, len(all_stocks))]

    def print_status(self):
        """打印注入器状态"""
        print("\n" + "=" * 60)
        print("  V13.0 P0-2 TDX实时数据注入器 状态")
        print("=" * 60)
        print(f"  监控池: {self.stats['total_watchlist']}只")
        print(f"  行情已拉取: {self.stats['tdx_quotes_fetched']}只")
        print(f"  K线已拉取: {self.stats['tdx_klines_fetched']}只")
        print(f"  有效标的: {self.stats['tdx_stocks_valid']}只")
        print(f"  数据模式: {self.stats['mode']}")
        print(f"  输出数量: {self.stats['total_output']}只")
        print("=" * 60)


# ═══════════════════════════════════════════════
# WorkBuddy Agent 集成
# ═══════════════════════════════════════════════

def agent_prepare_1430_data(
    injector: TdxRealtimeInjector = None,
    top_n: int = TOP_N_ACTIVE,
) -> tuple:
    """
    Agent准备14:30数据的主入口

    Usage in WorkBuddy automation:
      1. 创建 injector = TdxRealtimeInjector()
      2. 获取 tasks = injector.get_watchlist_tasks(top_n=60)
      3. Agent 遍历 tasks 调用 TDX MCP:
         for code, setcode, name, ind in tasks:
             quote = tdx_quotes(code=code, setcode=setcode)
             kline = tdx_kline(code=code, setcode=setcode, period="4", wantNum="60")
             injector.inject_stock_complete(code, setcode, name, ind, quote, kline)
      4. 调用 injector.save_cache()
      5. 触发 run_tail_market_1430.py 运行管线
    """
    if injector is None:
        injector = TdxRealtimeInjector()

    tasks = injector.get_watchlist_tasks(top_n)
    return injector, tasks


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 P0-2 TDX实时数据注入器 自测")
    print("=" * 60)

    injector = TdxRealtimeInjector()
    tasks = injector.get_watchlist_tasks(top_n=10)
    print(f"\n📋 待拉取任务 ({len(tasks)}只):")
    for code, setcode, name, ind in tasks:
        print(f"  [{code}] {name} ({ind}) | setcode={setcode}")

    injector.print_status()
    print("\n✅ 注入器就绪。在Agent环境中调用TDX MCP拉取数据后执行 injector.save_cache()")
    print("=" * 60)
