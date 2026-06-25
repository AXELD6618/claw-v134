#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 TDX 实时数据集成层 — TDXRealtimeFeed                          ║
║  ================================================================    ║
║  封装10个TDX MCP工具，统一数据缓存/读取/标准化接口                    ║
║                                                                      ║
║  三层数据管线：                                                       ║
║  ├── Tier1 实时行情: tdx_quotes + tdx_kline(5m/1min) + tdx_screener ║
║  ├── Tier2 增强数据: tdx_api_data (zjlx/ztfx/ltgd/jgcg/jglhb)      ║
║  └── Tier3 深度研究: tdx_api_data(财务) + wenda_news/notice/report   ║
║                                                                      ║
║  数据消费者：                                                         ║
║  ├── M56 尾盘30分钟引擎 ← Tier1 (1min K线 + quotes)                 ║
║  ├── M57 隔夜Alpha引擎 ← Tier1+Tier2 (资金流/龙虎榜/事件)           ║
║  ├── M46 贝叶斯校准   ← Tier1 (日K线 + quotes)                     ║
║  └── M51 主力意图     ← Tier2 (大单/委买委卖)                       ║
║                                                                      ║
║  使用方式：                                                           ║
║  # 在线模式: Agent调用TDX MCP获取数据，保存到JSON，本模块读取标准化   ║
║  feed = TDXRealtimeFeed(cache_dir='data/')                           ║
║  feed.load_from_cache('tdx_1430_cache.json')                         ║
║  stock_data_map = feed.build_stock_data_map(stock_list)              ║
║                                                                      ║
║  # 批量模式: 从多个缓存文件合并数据                                   ║
║  feed = TDXRealtimeFeed()                                             ║
║  feed.merge_caches(['tdx_600519_60d.json', 'tdx_300750_60d.json'])   ║
║                                                                      ║
║  # M57激活模式: 构建M57所需的全部12因子输入                          ║
║  m57_inputs = feed.build_m57_factor_inputs(code)                     ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
import random
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Union
from dataclasses import dataclass, field
from collections import defaultdict


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 常量与数据结构
# ═══════════════════════════════════════════════════════════════

# TDX MCP 10工具映射
TDX_TOOLS = {
    'quotes':    'tdx_quotes',           # 实时行情 OHLCV + 5档盘口
    'kline':     'tdx_kline',            # 多周期K线 5m/15m/30m/1h/D/W/M
    'screener':  'tdx_screener',         # 自然语言选股 涨停/题材
    'lookup':    'tdx_lookup_stock',     # 代码搜索
    'api_data':  'tdx_api_data',         # 70+结构化API路由
    'indicator': 'tdx_indicator_select', # PE/PB/财务指标
    'macro':     'wenda_macro_query',    # 宏观数据 GDP/CPI
    'news':      'wenda_news_query',     # 新闻资讯
    'notice':    'wenda_notice_query',   # 公司公告
    'report':    'wenda_report_query',   # 研报
}

# tdx_api_data 常用路由
API_ROUTES = {
    'capital_flow':    'zjlx',   # 资金流向（主力/超大单/大单净买）
    'limit_up_analysis': 'ztfx', # 涨停分析（封单/连板/题材）
    'dragon_tiger':    'jglhb',  # 龙虎榜（机构/游资席位）
    'northbound':      'bszj',   # 北向资金
    'margin_trading':  'rzrq',   # 融资融券
    'block_trade':     'dzjy',   # 大宗交易
    'inst_holding':    'jgcg',   # 机构持股
    'top_float_shareholders': 'ltgd', # 十大流通股东
    'shareholder_count': 'gdrs', # 股东人数
    'income_statement':  'lrb',  # 利润表
    'balance_sheet':     'zcfzb',# 资产负债表
    'cashflow_statement':'xjllb',# 现金流量表
    'business_composition': 'ycfbb', # 营业成本构成
    'valuation_history': 'gslsb',# 估值历史
}

# TDX setcode 映射: 0=深市 1=沪市 2=北交所 3=港股 4=美股
SETCODE_MAP = {
    'SH': '1', '60': '1', '68': '1',
    'SZ': '0', '00': '0', '30': '0',
    'BJ': '2', '83': '2', '87': '2', '92': '2',
    'HK': '3',
    'US': '4',
}


def infer_setcode(code: str) -> str:
    """从股票代码推断setcode"""
    code = str(code).strip()
    if code.startswith(('60', '68', '11', '13')):
        return '1'  # 沪市
    elif code.startswith(('00', '30', '12')):
        return '0'  # 深市
    elif code.startswith(('83', '87', '88', '92', '43')):
        return '2'  # 北交所
    elif code.startswith('5'):
        return '1'  # 沪市基金
    elif code.startswith('1'):
        return '0'  # 深市基金
    return '1'  # 默认沪市


def infer_board_tier(code: str) -> str:
    """从代码推断板块层级"""
    code = str(code).strip()
    if code.startswith('60'):
        return 'main'   # 主板
    elif code.startswith('00'):
        return 'main'
    elif code.startswith('30'):
        return 'gem'    # 创业板 20cm
    elif code.startswith('68'):
        return 'star'   # 科创板 20cm
    elif code.startswith(('83', '87', '88', '92', '43')):
        return 'bse'    # 北交所 30cm
    return 'main'


def get_limit_up_pct(board_tier: str) -> float:
    """获取涨停幅度"""
    return {
        'main': 0.10,
        'gem':  0.20,
        'star': 0.20,
        'bse':  0.30,
    }.get(board_tier, 0.10)


@dataclass
class TDXStockData:
    """单只股票的完整TDX数据集合"""
    code: str = ''
    name: str = ''
    setcode: str = ''

    # Tier1: 实时行情
    quote: Dict = field(default_factory=dict)         # tdx_quotes 原始返回
    kline_daily: Dict = field(default_factory=dict)   # tdx_kline 日K线
    kline_1min: Dict = field(default_factory=dict)    # tdx_kline 1分钟K线
    kline_5min: Dict = field(default_factory=dict)    # tdx_kline 5分钟K线

    # Tier2: 增强数据
    capital_flow: Dict = field(default_factory=dict)  # zjlx 资金流向
    limit_up_analysis: Dict = field(default_factory=dict)  # ztfx 涨停分析
    dragon_tiger: Dict = field(default_factory=dict)  # jglhb 龙虎榜
    northbound: Dict = field(default_factory=dict)    # bszj 北向资金
    inst_holding: Dict = field(default_factory=dict)  # jgcg 机构持股
    top_shareholders: Dict = field(default_factory=dict)  # ltgd 十大流通股东

    # Tier3: 深度研究
    indicator: Dict = field(default_factory=dict)     # PE/PB
    income_statement: Dict = field(default_factory=dict)
    balance_sheet: Dict = field(default_factory=dict)
    news: List[Dict] = field(default_factory=list)
    notices: List[Dict] = field(default_factory=list)
    reports: List[Dict] = field(default_factory=list)

    # 标准化后的数据
    normalized: Dict = field(default_factory=dict)

    # 元数据
    fetch_time: str = ''
    data_quality: int = 0  # 0=无数据 1=仅基础 2=基础+增强 3=完整


class TDXRealtimeFeed:
    """
    V13.2 TDX实时数据集成层

    核心职责：
    1. 从TDX MCP缓存JSON读取数据
    2. 标准化为V13.1引擎兼容格式
    3. 构建M57/M56/M46所需的全部输入
    4. 支持批量查询和数据合并
    """

    def __init__(self, cache_dir: str = 'data/'):
        self.cache_dir = cache_dir
        self.stocks: Dict[str, TDXStockData] = {}  # code -> TDXStockData
        self.screener_result: Dict = {}  # tdx_screener结果
        self.macro_data: Dict = {}  # 宏观数据
        self.news_cache: List[Dict] = []  # 新闻缓存
        self.report_cache: List[Dict] = []  # 研报缓存
        self.fetch_timestamp: str = ''

    # ═══════════════════════════════════════════════════════════════
    # SECTION 1: 数据加载（从缓存JSON读取）
    # ═══════════════════════════════════════════════════════════════

    def load_from_cache(self, filename: str) -> bool:
        """
        从缓存JSON文件加载TDX数据

        支持多种格式：
        - tdx_1430_cache.json (14:30部署格式: {stocks: {code: {quote, kline, ...}}})
        - tdx_realtime_input.json (实时输入格式)
        - tdx_{code}_60d.json (单股K线格式)
        """
        filepath = os.path.join(self.cache_dir, filename) if not os.path.isabs(filename) else filename

        if not os.path.exists(filepath):
            print(f"[TDXFeed] 缓存文件不存在: {filepath}")
            return False

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[TDXFeed] JSON解析失败: {filepath}: {e}")
            return False

        self._ingest_raw_data(raw)
        self.fetch_timestamp = raw.get('fetch_time', raw.get('timestamp', datetime.now().isoformat()))
        print(f"[TDXFeed] 已加载缓存: {filename} | 股票数: {len(self.stocks)}")
        return True

    def merge_caches(self, filenames: List[str]) -> int:
        """合并多个缓存文件"""
        count_before = len(self.stocks)
        for fn in filenames:
            self.load_from_cache(fn)
        merged = len(self.stocks) - count_before
        print(f"[TDXFeed] 合并完成: 新增 {merged} 只, 总计 {len(self.stocks)} 只")
        return merged

    def _ingest_raw_data(self, raw: Dict):
        """将原始JSON数据导入到self.stocks"""

        # 格式1: {stocks: {code: {quote, kline, kline_1min, ...}}}
        if 'stocks' in raw and isinstance(raw['stocks'], dict):
            for code, sdata in raw['stocks'].items():
                self._add_stock_from_dict(code, sdata)

        # 格式2: {candidates: [{code, name, quote, kline, ...}]}
        elif 'candidates' in raw and isinstance(raw['candidates'], list):
            for c in raw['candidates']:
                code = str(c.get('code', ''))
                if code:
                    self._add_stock_from_dict(code, c)

        # 格式3: {code: "600519", ListItem: [...]} (单股K线格式)
        elif 'code' in raw and 'ListItem' in raw:
            code = str(raw['code'])
            self._add_stock_from_dict(code, {'kline': raw})

        # 格式4: {data: {code: {...}}} 或直接 {code: {...}}
        elif 'data' in raw and isinstance(raw['data'], dict):
            for code, sdata in raw['data'].items():
                self._add_stock_from_dict(code, sdata)
        else:
            # 尝试直接作为 {code: {...}} 字典处理
            for key, val in raw.items():
                if isinstance(val, dict) and key.isdigit():
                    self._add_stock_from_dict(key, val)

        # 加载screener结果
        if 'screener' in raw:
            self.screener_result = raw['screener']

        # 加载宏观数据
        if 'macro' in raw:
            self.macro_data = raw['macro']

    def _add_stock_from_dict(self, code: str, sdata: Dict):
        """从字典添加/更新股票数据"""
        code = str(code).strip()
        if not code:
            return

        if code not in self.stocks:
            self.stocks[code] = TDXStockData(
                code=code,
                name=sdata.get('name', ''),
                setcode=sdata.get('setcode', infer_setcode(code)),
                fetch_time=datetime.now().isoformat(),
            )

        stock = self.stocks[code]

        # 更新名称
        if sdata.get('name') and not stock.name:
            stock.name = sdata['name']
        if sdata.get('setcode') and not stock.setcode:
            stock.setcode = sdata['setcode']

        # Tier1: 实时行情
        if 'quote' in sdata:
            stock.quote = sdata['quote']
            stock.data_quality = max(stock.data_quality, 1)
        if 'kline' in sdata:
            stock.kline_daily = sdata['kline']
            stock.data_quality = max(stock.data_quality, 1)
        elif 'daily_klines' in sdata:
            stock.kline_daily = {'ListItem': sdata['daily_klines']}
            stock.data_quality = max(stock.data_quality, 1)
        if 'kline_1min' in sdata:
            stock.kline_1min = sdata['kline_1min']
            stock.data_quality = max(stock.data_quality, 2)
        if 'kline_5min' in sdata:
            stock.kline_5min = sdata['kline_5min']
        if 'closes' in sdata:
            # 预处理过的K线数据
            stock.kline_daily = {
                'ListItem': [
                    {'c': c, 'h': h, 'l': l, 'o': o, 'a': a, 'v': v}
                    for c, h, l, o, a, v in zip(
                        sdata['closes'], sdata.get('highs', sdata['closes']),
                        sdata.get('lows', sdata['closes']),
                        sdata.get('opens', sdata['closes']),
                        sdata.get('amounts', [0]*len(sdata['closes'])),
                        sdata.get('volumes', [0]*len(sdata['closes'])),
                    )
                ]
            }
            stock.data_quality = max(stock.data_quality, 1)

        # Tier2: 增强数据
        for route_key, route_tag in API_ROUTES.items():
            if route_tag in sdata:
                setattr(stock, route_key, sdata[route_tag])
                stock.data_quality = max(stock.data_quality, 2)

        # Tier3: 深度研究
        if 'indicator' in sdata:
            stock.indicator = sdata['indicator']
            stock.data_quality = max(stock.data_quality, 3)
        if 'income_statement' in sdata:
            stock.income_statement = sdata['income_statement']
        if 'balance_sheet' in sdata:
            stock.balance_sheet = sdata['balance_sheet']
        if 'news' in sdata:
            stock.news = sdata['news']
        if 'notices' in sdata:
            stock.notices = sdata['notices']
        if 'reports' in sdata:
            stock.reports = sdata['reports']

    # ═══════════════════════════════════════════════════════════════
    # SECTION 2: 行情数据标准化
    # ═══════════════════════════════════════════════════════════════

    def get_quote_fields(self, code: str) -> Dict[str, float]:
        """
        从tdx_quotes响应中提取标准化行情字段

        Returns:
            {prev_close, now, open, high, low, amount, volume, ...}
        """
        stock = self.stocks.get(code)
        if not stock or not stock.quote:
            return {}

        q = stock.quote
        hq = q.get('HQInfo', q.get('AttachInfo', {}))

        return {
            'prev_close':  float(hq.get('Close', 0)),
            'now':         float(hq.get('Now', 0)),
            'open':        float(hq.get('Open', 0)),
            'high':        float(hq.get('MaxP', 0)),
            'low':         float(hq.get('MinP', 0)),
            'amount':      float(hq.get('Amount', 0)),
            'volume':      float(hq.get('Volume', 0)),
            'turnover':    float(hq.get('Turnover', 0)),
            'bid1_price':  float(hq.get('BuyPrice1', 0)),
            'ask1_price':  float(hq.get('SellPrice1', 0)),
            'bid1_volume': float(hq.get('BuyVolume1', 0)),
            'ask1_volume': float(hq.get('SellVolume1', 0)),
        }

    def get_daily_klines(self, code: str, n: int = 60) -> List[Dict]:
        """
        从tdx_kline响应中提取日K线列表

        Returns:
            [{date, open, high, low, close, volume, amount, ...}, ...]
        """
        stock = self.stocks.get(code)
        if not stock or not stock.kline_daily:
            return []

        kline = stock.kline_daily
        items = kline.get('ListItem', [])

        bars = []
        for bar in items[-n:] if isinstance(items, list) else []:
            if isinstance(bar, dict):
                vals = bar.get('Item', [])
                if isinstance(vals, list) and len(vals) >= 7:
                    # TDX K-line Item数组格式（10字段扩展版）:
                    # [0]=date [1]=占位 [2]=open [3]=high [4]=low
                    # [5]=close [6]=amount [7]=turnover [8]=volume [9]=?
                    if len(vals) >= 10:
                        bars.append({
                            'date':   str(vals[0]),
                            'open':   float(vals[2]) if float(vals[2]) > 0 else float(vals[1]),
                            'high':   float(vals[3]),
                            'low':    float(vals[4]),
                            'close':  float(vals[5]),
                            'amount': float(vals[6]),
                            'volume': float(vals[8]),
                        })
                    # 7字段标准版: [0]=date [1]=open [2]=high [3]=low [4]=close [5]=amount [6]=volume
                    else:
                        bars.append({
                            'date':   str(vals[0]),
                            'open':   float(vals[1]),
                            'high':   float(vals[2]),
                            'low':    float(vals[3]),
                            'close':  float(vals[4]),
                            'amount': float(vals[5]),
                            'volume': float(vals[6]),
                        })
                else:
                    bars.append({
                        'date':   bar.get('d', bar.get('date', '')),
                        'open':   float(bar.get('o', bar.get('Open', 0))),
                        'high':   float(bar.get('h', bar.get('High', 0))),
                        'low':    float(bar.get('l', bar.get('Low', 0))),
                        'close':  float(bar.get('c', bar.get('Close', 0))),
                        'volume': float(bar.get('v', bar.get('Volume', 0))),
                        'amount': float(bar.get('a', bar.get('Amount', 0))),
                    })
            elif isinstance(bar, dict) and 'c' in bar:
                bars.append({
                    'close':  float(bar.get('c', 0)),
                    'high':   float(bar.get('h', 0)),
                    'low':    float(bar.get('l', 0)),
                    'open':   float(bar.get('o', 0)),
                    'amount': float(bar.get('a', 0)),
                    'volume': float(bar.get('v', 0)),
                })

        return bars

    def get_1min_klines(self, code: str, n: int = 30) -> Tuple[List[float], List[float]]:
        """
        从tdx_kline(period=0)提取1分钟K线的收盘价和成交量

        用于M56尾盘30分钟引擎和M57 flow_accel因子

        Returns:
            (tail_prices, tail_volumes) - 最后n根1分钟K线
        """
        stock = self.stocks.get(code)
        if not stock or not stock.kline_1min:
            return [], []

        items = stock.kline_1min.get('ListItem', [])
        if not isinstance(items, list):
            return [], []

        prices = []
        volumes = []
        for bar in items[-n:]:
            if isinstance(bar, dict):
                vals = bar.get('Item', [])
                if isinstance(vals, list) and len(vals) >= 7:
                    if len(vals) >= 10:
                        prices.append(float(vals[5]))   # Close
                        volumes.append(float(vals[8]))  # Volume
                    else:
                        prices.append(float(vals[4]))   # Close (7-field format)
                        volumes.append(float(vals[6]))  # Volume
                else:
                    prices.append(float(bar.get('c', 0)))
                    volumes.append(float(bar.get('v', 0)))

        return prices, volumes

    def get_5min_klines(self, code: str, n: int = 48) -> List[Dict]:
        """获取5分钟K线（用于日内结构分析）"""
        stock = self.stocks.get(code)
        if not stock or not stock.kline_5min:
            return []

        items = stock.kline_5min.get('ListItem', [])
        bars = []
        for bar in items[-n:] if isinstance(items, list) else []:
            if isinstance(bar, dict):
                vals = bar.get('Item', [])
                if isinstance(vals, list) and len(vals) >= 7:
                    if len(vals) >= 10:
                        bars.append({
                            'close':  float(vals[5]),
                            'high':   float(vals[3]),
                            'low':    float(vals[4]),
                            'open':   float(vals[2]) if float(vals[2]) > 0 else float(vals[1]),
                            'volume': float(vals[8]),
                            'amount': float(vals[6]),
                        })
                    else:
                        bars.append({
                            'close':  float(vals[4]),
                            'high':   float(vals[2]),
                            'low':    float(vals[3]),
                            'open':   float(vals[1]),
                            'volume': float(vals[6]),
                            'amount': float(vals[5]),
                        })
        return bars

    # ═══════════════════════════════════════════════════════════════
    # SECTION 3: stock_data_map 构建（V13.1引擎兼容格式）
    # ═══════════════════════════════════════════════════════════════

    def build_stock_data_map(
        self,
        stock_list: List[Dict],
        use_real_tail: bool = True,
        fill_missing: bool = True,
    ) -> Dict[str, Dict]:
        """
        构建V13.1引擎兼容的stock_data_map

        这是核心方法 — 将TDX原始数据转换为M56/M57/M46/M51引擎所需的格式

        Args:
            stock_list: [{code, name, ...}, ...] 需要分析的股票列表
            use_real_tail: 是否使用真实1分钟K线（False=合成数据）
            fill_missing: 是否用合理默认值填充缺失数据

        Returns:
            {code: {name, current_price, prev_close, open, high, low, amount,
                    change_pct, tail_prices, tail_volumes, ma5, ma20, ...}}
        """
        stock_data_map = {}

        for stock_info in stock_list:
            code = str(stock_info.get('code', ''))
            name = stock_info.get('name', code)

            tdx = self.stocks.get(code)
            if tdx is None and fill_missing:
                # 创建空壳数据
                tdx = TDXStockData(code=code, name=name, setcode=infer_setcode(code))

            if tdx is None:
                continue

            # 提取行情数据
            qf = self.get_quote_fields(code)

            # 优先从quote获取，回退到K线最后一根
            prev_close = qf.get('prev_close', 0)
            now_price  = qf.get('now', 0)
            open_price = qf.get('open', 0)
            high_price = qf.get('high', 0)
            low_price  = qf.get('low', 0)
            amount     = qf.get('amount', 0)
            volume     = qf.get('volume', 0)

            # 从日K线回退
            daily_bars = self.get_daily_klines(code, n=60)

            if now_price <= 0 and daily_bars:
                now_price = daily_bars[-1].get('close', 0)
            if prev_close <= 0 and len(daily_bars) >= 2:
                prev_close = daily_bars[-2].get('close', 0)
            elif prev_close <= 0 and daily_bars:
                prev_close = daily_bars[-1].get('open', 0)
            if open_price <= 0 and daily_bars:
                open_price = daily_bars[-1].get('open', 0)
            if high_price <= 0 and daily_bars:
                high_price = daily_bars[-1].get('high', 0)
            if low_price <= 0 and daily_bars:
                low_price = daily_bars[-1].get('low', 0)
            if amount <= 0 and daily_bars:
                amount = daily_bars[-1].get('amount', 0)

            # 最终回退
            if now_price <= 0:
                now_price = 10.0
            if prev_close <= 0:
                prev_close = now_price * 0.95
            if open_price <= 0:
                open_price = prev_close
            if high_price <= 0:
                high_price = now_price * 1.02
            if low_price <= 0:
                low_price = now_price * 0.98
            if amount <= 0:
                amount = 50000000

            change_pct = (now_price / prev_close - 1) * 100 if prev_close > 0 else 0

            # 技术指标计算
            closes = [b['close'] for b in daily_bars if b.get('close', 0) > 0]
            highs  = [b['high'] for b in daily_bars if b.get('high', 0) > 0]
            amounts_list = [b['amount'] for b in daily_bars if b.get('amount', 0) > 0]

            ma5 = sum(closes[-5:]) / min(5, len(closes)) if len(closes) >= 1 else now_price
            ma20 = sum(closes[-20:]) / min(20, len(closes)) if closes else None
            high_20d = max(highs[-20:]) if highs else None

            ma5_dev = (now_price - ma5) / ma5 * 100 if ma5 > 0 else 0
            prior_5d_return = 0
            if len(closes) >= 6 and closes[-6] > 0:
                prior_5d_return = (closes[-1] / closes[-6] - 1) * 100

            avg_5d_amount = sum(amounts_list[-5:]) / min(5, len(amounts_list)) if amounts_list else amount
            volume_ratio = amount / avg_5d_amount if avg_5d_amount > 0 else 1.0
            avg_volume_yuan = sum(amounts_list) / len(amounts_list) if amounts_list else amount

            # 收盘位置和上影线
            if high_price > low_price:
                close_position = (now_price - low_price) / (high_price - low_price)
                upper_shadow = (high_price - now_price) / (high_price - low_price)
            else:
                close_position = 0.5
                upper_shadow = 0.0

            # 尾盘1分钟数据
            tail_prices = None
            tail_volumes = None

            if use_real_tail:
                tail_prices, tail_volumes = self.get_1min_klines(code, n=30)

            if not tail_prices or len(tail_prices) < 5:
                # 合成尾盘数据
                daily_chg = change_pct / 100.0
                tail_prices = self._gen_synthetic_tail_prices(
                    open_price, now_price, high_price, low_price, prev_close, daily_chg)
                tail_volumes = self._gen_synthetic_tail_volumes(amount, daily_chg)

            prev_30_avg_vol = self._compute_prev_30min_avg_vol(amount, change_pct / 100.0)

            # 板块信息
            board_tier = infer_board_tier(code)
            limit_up_pct = get_limit_up_pct(board_tier)

            # 尾盘30分钟涨幅估算
            if tail_prices and len(tail_prices) >= 2:
                tail_start = tail_prices[0]
                tail_end = tail_prices[-1]
                tail_30min_change_pct = (tail_end / tail_start - 1) * 100 if tail_start > 0 else 0
            else:
                tail_30min_change_pct = change_pct * 0.35

            # 日内低点/收盘相对昨收
            day_low_pct = (low_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
            day_close_pct = change_pct

            # 跳空数据
            today_gap_pct = (open_price - prev_close) / prev_close * 100 if prev_close > 0 else 0
            gap_direction = 1 if today_gap_pct > 0.5 else (-1 if today_gap_pct < -0.5 else 0)

            # M46置信度
            posterior_mean = self._get_posterior_mean(change_pct)
            m46_confidence = self._compute_m46_confidence(
                change_pct, posterior_mean, close_position,
                upper_shadow, ma5_dev, prior_5d_return, volume_ratio)

            # 构建数据字典
            data = {
                'name': name,
                'code': code,
                'current_price': now_price,
                'prev_close': prev_close,
                'open': open_price,
                'high': high_price,
                'low': low_price,
                'amount': amount,
                'volume': volume,
                'change_pct': round(change_pct, 2),
                'listed_days': len(closes) if closes else 9999,
                'is_suspended': False,
                'consecutive_limit_up': self._get_consecutive_limits(code),
                'avg_volume_yuan': avg_volume_yuan or 1e8,
                'intraday_change_pct': round(change_pct, 2),
                'day_low_pct': round(day_low_pct, 2),
                'day_close_pct': round(day_close_pct, 2),
                'tail_30min_change_pct': round(tail_30min_change_pct, 2),
                'total_day_volume': amount,
                'tail_30min_volume': sum(tail_volumes) if tail_volumes else amount * 0.18,
                'ma20': ma20,
                'ma5': round(ma5, 3),
                'ma5_dev': round(ma5_dev, 2),
                'high_20d': high_20d,
                'prior_5d_return': round(prior_5d_return, 2),
                'close_position': round(close_position, 4),
                'upper_shadow': round(upper_shadow, 4),
                'volume_ratio': round(volume_ratio, 2),
                'prev_30_avg_vol': prev_30_avg_vol,
                'tail_prices': tail_prices,
                'tail_volumes': tail_volumes,
                'sector_data': None,
                # 板块信息
                'board_tier': board_tier,
                'limit_up_pct': limit_up_pct,
                'setcode': tdx.setcode or infer_setcode(code),
                # M46校准
                'm46_posterior_mean': round(posterior_mean, 4),
                'm46_confidence': m46_confidence,
                'm46_bracket': self._get_bracket(change_pct),
                'm46_threshold': 0.63,
                'm46_recommended': m46_confidence >= 0.63,
                # 跳空数据
                'today_gap_pct': round(today_gap_pct, 2),
                'gap_direction': gap_direction,
                # TDX增强数据可用性标记
                'tdx_data_quality': tdx.data_quality,
                'has_capital_flow': bool(tdx.capital_flow),
                'has_dragon_tiger': bool(tdx.dragon_tiger),
                'has_limit_up_analysis': bool(tdx.limit_up_analysis),
                'has_inst_holding': bool(tdx.inst_holding),
                'has_top_shareholders': bool(tdx.top_shareholders),
            }

            # 板块数据
            sector_name = stock_info.get('industry', tdx.name and '' or '')
            if sector_name:
                data['sector_data'] = {
                    'change': change_pct * 0.6,
                    'volume_ratio': 1.2,
                    'up_count': 60, 'total': 100,
                    'leader_change': change_pct,
                }
                data['sector_name'] = sector_name

            # 如果有资金流向数据，注入
            if tdx.capital_flow:
                cf = self._parse_capital_flow(tdx.capital_flow)
                if cf:
                    data['capital_flow'] = cf

            # 如果有龙虎榜数据，注入
            if tdx.dragon_tiger:
                lhb = self._parse_dragon_tiger(tdx.dragon_tiger)
                if lhb:
                    data['dragon_tiger'] = lhb

            # 如果有涨停分析数据，注入
            if tdx.limit_up_analysis:
                ztfx = self._parse_limit_up_analysis(tdx.limit_up_analysis)
                if ztfx:
                    data['limit_up_analysis'] = ztfx

            stock_data_map[code] = data

        return stock_data_map

    # ═══════════════════════════════════════════════════════════════
    # SECTION 4: M57 Alpha因子输入构建器
    # ═══════════════════════════════════════════════════════════════

    def build_m57_factor_inputs(self, code: str, stock_data: Dict = None) -> Dict:
        """
        为M57 OvernightAlphaEngine.compute_all_factors()构建全部12因子输入

        这是V13.2的核心增强 — 用TDX真实数据激活M57的休眠因子

        Returns:
            包含compute_all_factors()所需的全部参数的字典
        """
        if stock_data is None:
            stock_data = self.build_stock_data_map([{'code': code}]).get(code, {})

        if not stock_data:
            return {}

        tdx = self.stocks.get(code)
        board_tier = stock_data.get('board_tier', infer_board_tier(code))

        # ── 因子1: tail_rs ──
        intraday_change_pct = stock_data.get('intraday_change_pct', 0)
        tail_30min_change_pct = stock_data.get('tail_30min_change_pct', 0)

        # ── 因子2: tail_vol_struct ──
        tail_30min_volume = stock_data.get('tail_30min_volume', 0)
        total_day_volume = stock_data.get('total_day_volume', 0)

        # ── 因子3: overnight_mom ──
        # 从日K线计算近10日隔夜收益(T-1收盘→T开盘)
        overnight_returns = self._compute_overnight_returns(code, lookback=10)

        # ── 因子4: intraday_rev ──
        day_low_pct = stock_data.get('day_low_pct', 0)
        day_close_pct = stock_data.get('day_close_pct', 0)

        # ── 因子5: auction_sig ──
        # 从1分钟K线提取14:57-15:00的集合竞价信号
        auction_data = self._extract_auction_signal(code)
        auction_price_change_pct = auction_data.get('price_change_pct', 0)
        auction_volume_ratio = auction_data.get('volume_ratio', 0)
        price_before_auction_pct = auction_data.get('price_before_pct', 0)

        # ── 因子6: sector_alpha ──
        sector_intraday_change = stock_data.get('sector_data', {}).get('change', 0) if stock_data.get('sector_data') else 0
        sector_std = 1.5

        # ── 因子7: streak_exp ──
        consecutive_limit_up = stock_data.get('consecutive_limit_up', 0)

        # ── 因子8: flow_accel ──
        tail_volumes = stock_data.get('tail_volumes', [])
        tail_prices = stock_data.get('tail_prices', [])

        # ── 因子9: gap_fill_prob ──
        today_gap_pct = stock_data.get('today_gap_pct', 0)
        gap_direction = stock_data.get('gap_direction', 0)
        historical_fill_rate = self._compute_historical_fill_rate(code)

        # ── 因子10: event_decay ──
        events = self._extract_event_signals(code)

        # ── 因子11: lhb_effect ──
        lhb_data = self._extract_lhb_data(code, stock_data)

        # ── 因子12: sentiment_trans ──
        market_data = self._compute_market_sentiment()
        market_tail_change_pct = market_data.get('tail_change_pct', 0)
        stock_beta = self._compute_stock_beta(code)
        market_sentiment_idx = market_data.get('sentiment_idx', 0)
        vix_proxy = market_data.get('vix_proxy', 20.0)

        return {
            'code': code,
            'date': datetime.now().strftime('%Y-%m-%d'),
            # 价格数据
            'intraday_change_pct': intraday_change_pct,
            'tail_30min_change_pct': tail_30min_change_pct,
            'day_low_pct': day_low_pct,
            'day_close_pct': day_close_pct,
            'today_gap_pct': today_gap_pct,
            'gap_direction': gap_direction,
            # 量能数据
            'tail_30min_volume': tail_30min_volume,
            'total_day_volume': total_day_volume,
            'avg_tail_ratio': 0.15,
            'tail_volumes': tail_volumes,
            'tail_prices': tail_prices,
            # 竞价数据
            'auction_price_change_pct': auction_price_change_pct,
            'auction_volume_ratio': auction_volume_ratio,
            'price_before_auction_pct': price_before_auction_pct,
            # 板块数据
            'sector_intraday_change': sector_intraday_change,
            'sector_std': sector_std,
            # 历史数据
            'overnight_returns': overnight_returns,
            'historical_fill_rate': historical_fill_rate,
            # 连板
            'consecutive_limit_up': consecutive_limit_up,
            'board_tier': board_tier,
            # 事件
            'events': events,
            # 龙虎榜
            'lhb_buy_amount': lhb_data.get('buy_amount', 0),
            'lhb_sell_amount': lhb_data.get('sell_amount', 0),
            'total_turnover': lhb_data.get('total_turnover', stock_data.get('amount', 0)),
            'is_bullish_seat': lhb_data.get('is_bullish_seat', False),
            'days_since_lhb': lhb_data.get('days_since', 0),
            # 市场
            'market_tail_change_pct': market_tail_change_pct,
            'stock_beta': stock_beta,
            'market_sentiment_idx': market_sentiment_idx,
            'vix_proxy': vix_proxy,
        }

    # ═══════════════════════════════════════════════════════════════
    # SECTION 5: TDX增强数据解析器
    # ═══════════════════════════════════════════════════════════════

    def _parse_capital_flow(self, raw: Dict) -> Dict:
        """解析tdx_api_data zjlx (资金流向)"""
        items = raw.get('ListItem', raw.get('data', []))
        if not isinstance(items, list) or not items:
            return {}

        latest = items[-1] if items else {}
        if isinstance(latest, dict):
            vals = latest.get('Item', latest)
            return {
                'main_force_net': float(vals.get('zljlr', vals.get('main_net', 0))),
                'super_large_net': float(vals.get('cddjlr', vals.get('super_net', 0))),
                'large_net': float(vals.get('ddjkr', vals.get('large_net', 0))),
                'medium_net': float(vals.get('zdjkr', vals.get('medium_net', 0))),
                'small_net': float(vals.get('ddjkr', vals.get('small_net', 0))),
                'close_price': float(vals.get('close', vals.get('c', 0))),
                'date': str(vals.get('date', vals.get('d', ''))),
            }
        return {}

    def _parse_dragon_tiger(self, raw: Dict) -> Dict:
        """解析tdx_api_data jglhb (龙虎榜)"""
        items = raw.get('ListItem', raw.get('data', []))
        if not isinstance(items, list) or not items:
            return {}

        result = {'seats': [], 'net_buy': 0, 'total_buy': 0, 'total_sell': 0}
        for item in items[:20]:
            if isinstance(item, dict):
                vals = item.get('Item', item)
                buy = float(vals.get('buy', vals.get('b', 0)))
                sell = float(vals.get('sell', vals.get('s', 0)))
                result['seats'].append({
                    'name': str(vals.get('name', vals.get('seat', ''))),
                    'buy': buy,
                    'sell': sell,
                    'net': buy - sell,
                    'type': str(vals.get('type', '')),
                })
                result['total_buy'] += buy
                result['total_sell'] += sell

        result['net_buy'] = result['total_buy'] - result['total_sell']
        return result

    def _parse_limit_up_analysis(self, raw: Dict) -> Dict:
        """解析tdx_api_data ztfx (涨停分析)"""
        items = raw.get('ListItem', raw.get('data', []))
        if not isinstance(items, list) or not items:
            return {}

        latest = items[-1] if items else {}
        if isinstance(latest, dict):
            vals = latest.get('Item', latest)
            return {
                'seal_amount': float(vals.get('fbc', vals.get('seal_amount', 0))),
                'first_limit_time': str(vals.get('fbt', vals.get('first_time', ''))),
                'open_count': int(vals.get('opc', vals.get('open_count', 0))),
                'reason': str(vals.get('reason', vals.get('ztreason', ''))),
                'consecutive_days': int(vals.get('cts', vals.get('consecutive', 0))),
                'board_type': str(vals.get('board', vals.get('btype', ''))),
                'seal_traded_ratio': float(vals.get('stb', vals.get('seal_ratio', 0))),
            }
        return {}

    # ═══════════════════════════════════════════════════════════════
    # SECTION 6: M57因子专用计算器
    # ═══════════════════════════════════════════════════════════════

    def _compute_overnight_returns(self, code: str, lookback: int = 10) -> List[float]:
        """
        计算近N日隔夜收益 (T-1收盘→T开盘)

        从日K线提取: overnight_return = (T_open - T-1_close) / T-1_close
        """
        bars = self.get_daily_klines(code, n=lookback + 1)
        if len(bars) < 2:
            return []

        returns = []
        for i in range(1, len(bars)):
            prev_close = bars[i-1].get('close', 0)
            curr_open = bars[i].get('open', 0)
            if prev_close > 0 and curr_open > 0:
                ret = (curr_open - prev_close) / prev_close * 100
                returns.append(round(ret, 4))

        return returns[-lookback:]

    def _extract_auction_signal(self, code: str) -> Dict:
        """
        从1分钟K线提取14:57-15:00集合竞价信号

        如果有真实1分钟数据:
        - auction_price_change = 最后1根(15:00) vs 倒数第2根(14:59)的收盘价变化
        - auction_volume_ratio = 最后1根量 / 前5根平均量
        - price_before_auction_pct = 14:56收盘 vs 昨收
        """
        prices, volumes = self.get_1min_klines(code, n=30)

        if len(prices) < 7:
            return {'price_change_pct': 0, 'volume_ratio': 0, 'price_before_pct': 0}

        # 最后1根 = 15:00集合竞价
        last_price = prices[-1]
        prev_price = prices[-2]

        if prev_price > 0:
            auction_change = (last_price / prev_price - 1) * 100
        else:
            auction_change = 0

        # 竞价量比
        if len(volumes) >= 6:
            avg_5_vol = sum(volumes[-6:-1]) / 5
            auction_vol_ratio = volumes[-1] / avg_5_vol if avg_5_vol > 0 else 0
        else:
            auction_vol_ratio = 0

        # 竞价前价格(14:56)相对昨收
        stock = self.stocks.get(code)
        prev_close = 0
        if stock and stock.quote:
            hq = stock.quote.get('HQInfo', stock.quote.get('AttachInfo', {}))
            prev_close = float(hq.get('Close', 0))

        if prev_close <= 0:
            bars = self.get_daily_klines(code, n=2)
            if len(bars) >= 2:
                prev_close = bars[-2].get('close', 0)

        price_before_pct = 0
        if prev_close > 0 and len(prices) >= 4:
            price_1456 = prices[-4]  # 倒数第4根 ≈ 14:56
            price_before_pct = (price_1456 / prev_close - 1) * 100

        return {
            'price_change_pct': round(auction_change, 4),
            'volume_ratio': round(auction_vol_ratio, 4),
            'price_before_pct': round(price_before_pct, 4),
        }

    def _compute_historical_fill_rate(self, code: str) -> float:
        """
        计算历史跳空回补率

        从日K线统计: 过去60日中，跳空后次日回补的比例
        """
        bars = self.get_daily_klines(code, n=60)
        if len(bars) < 5:
            return 0.5  # 默认50%回补率

        gaps = []
        for i in range(1, len(bars)):
            prev_close = bars[i-1].get('close', 0)
            curr_open = bars[i].get('open', 0)
            if prev_close > 0:
                gap_pct = (curr_open - prev_close) / prev_close * 100
                if abs(gap_pct) > 0.5:
                    # 有跳空，检查是否在当日或次日回补
                    curr_high = bars[i].get('high', 0)
                    curr_low = bars[i].get('low', 0)
                    # 向上跳空回补 = 当日最低 <= 昨收
                    # 向下跳空回补 = 当日最高 >= 昨收
                    if gap_pct > 0:
                        filled = curr_low <= prev_close
                    else:
                        filled = curr_high >= prev_close
                    gaps.append(filled)

        if not gaps:
            return 0.5

        return round(sum(gaps) / len(gaps), 4)

    def _extract_event_signals(self, code: str) -> List:
        """
        从新闻/公告/研报提取事件信号

        将TDX wenda_news/notice/report转换为EventSignal格式
        """
        from V13_1_M57_OvernightAlphaEngine import EventSignal

        stock = self.stocks.get(code)
        if not stock:
            return []

        events = []

        # 从公告提取
        for notice in stock.notices[:10]:
            title = str(notice.get('title', ''))
            date = str(notice.get('date', notice.get('Date', '')))[:10]
            # 简单关键词分类
            if any(kw in title for kw in ['增持', '回购', '业绩预增', '中标', '合同', '收购']):
                events.append(EventSignal(
                    event_type='notice_bullish', date=date,
                    strength=0.6, direction=1, half_life=5))
            elif any(kw in title for kw in ['减持', '业绩预减', '亏损', '诉讼', '违规']):
                events.append(EventSignal(
                    event_type='notice_bearish', date=date,
                    strength=0.5, direction=-1, half_life=5))

        # 从新闻提取
        for news in stock.news[:10]:
            title = str(news.get('title', news.get('Title', '')))
            date = str(news.get('date', news.get('Date', '')))[:10]
            if any(kw in title for kw in ['利好', '突破', '创新高', '超预期']):
                events.append(EventSignal(
                    event_type='news_bullish', date=date,
                    strength=0.4, direction=1, half_life=3))
            elif any(kw in title for kw in ['利空', '暴跌', '预警', '风险']):
                events.append(EventSignal(
                    event_type='news_bearish', date=date,
                    strength=0.4, direction=-1, half_life=3))

        # 从涨停分析提取（如果有）
        if stock.limit_up_analysis:
            ztfx = self._parse_limit_up_analysis(stock.limit_up_analysis)
            if ztfx.get('reason'):
                events.append(EventSignal(
                    event_type='limit_up', date=datetime.now().strftime('%Y-%m-%d'),
                    strength=0.8, direction=1, half_life=3))

        return events

    def _extract_lhb_data(self, code: str, stock_data: Dict) -> Dict:
        """提取龙虎榜数据用于M57 lhb_effect因子"""
        stock = self.stocks.get(code)
        if not stock or not stock.dragon_tiger:
            return {}

        lhb = self._parse_dragon_tiger(stock.dragon_tiger)
        if not lhb:
            return {}

        # 检测知名席位
        bullish_seats = ['机构专用', '外资', '社保', ' QFII']
        is_bullish = any(
            any(bs in seat.get('name', '') for bs in bullish_seats)
            for seat in lhb.get('seats', [])
        )

        return {
            'buy_amount': lhb.get('total_buy', 0),
            'sell_amount': lhb.get('total_sell', 0),
            'total_turnover': stock_data.get('amount', 0),
            'is_bullish_seat': is_bullish,
            'days_since': 0,  # 当日数据
        }

    def _compute_market_sentiment(self) -> Dict:
        """
        计算大盘市场情绪

        使用沪深300/上证指数作为市场代理
        """
        # 尝试从缓存中获取指数数据
        # 如果screener_result中有市场数据，使用它
        if self.screener_result:
            sr = self.screener_result
            market_change = float(sr.get('market_change', 0))
            up_count = int(sr.get('up_count', 0))
            down_count = int(sr.get('down_count', 0))
            total = up_count + down_count
            if total > 0:
                sentiment_idx = (up_count - down_count) / total
            else:
                sentiment_idx = 0
        else:
            market_change = 0
            sentiment_idx = 0

        # VIX代理: 用市场波动率估算
        # 如果有宏观数据，使用它
        vix_proxy = 20.0  # 默认值
        if self.macro_data:
            vix_proxy = float(self.macro_data.get('vix_proxy', 20.0))

        return {
            'tail_change_pct': market_change * 0.4,  # 尾盘约占全天40%
            'sentiment_idx': sentiment_idx,
            'vix_proxy': vix_proxy,
        }

    def _compute_stock_beta(self, code: str) -> float:
        """
        计算个股Beta（相对大盘）

        Beta = Cov(stock, market) / Var(market)
        使用近20日收益率计算
        """
        bars = self.get_daily_klines(code, n=25)
        if len(bars) < 10:
            return 1.0  # 默认Beta=1

        # 个股日收益率
        stock_returns = []
        for i in range(1, len(bars)):
            prev = bars[i-1].get('close', 0)
            curr = bars[i].get('close', 0)
            if prev > 0:
                stock_returns.append((curr / prev - 1))

        if len(stock_returns) < 5:
            return 1.0

        # 简化: 用个股波动率/市场波动率(15%)作为Beta近似
        mean_ret = sum(stock_returns) / len(stock_returns)
        var_ret = sum((r - mean_ret)**2 for r in stock_returns) / len(stock_returns)
        stock_vol = math.sqrt(var_ret) if var_ret > 0 else 0.015

        market_vol = 0.015  # A股大盘日均波动率约1.5%
        beta = stock_vol / market_vol if market_vol > 0 else 1.0

        # 限制在合理范围 [0.3, 3.0]
        return round(max(0.3, min(3.0, beta)), 4)

    def _get_consecutive_limits(self, code: str) -> int:
        """从screener结果或涨停分析中获取连板天数"""
        stock = self.stocks.get(code)
        if stock and stock.limit_up_analysis:
            ztfx = self._parse_limit_up_analysis(stock.limit_up_analysis)
            return ztfx.get('consecutive_days', 0)

        # 从screener结果搜索
        if self.screener_result:
            for item in self.screener_result.get('stocks', []):
                if str(item.get('code', '')) == code:
                    return int(item.get('consecutive_days', item.get('cts', 0)))

        return 0

    # ═══════════════════════════════════════════════════════════════
    # SECTION 7: M46贝叶斯校准（从Deploy脚本移植）
    # ═══════════════════════════════════════════════════════════════

    M46_CALIBRATION = {
        'confidence_threshold': 0.63,
        'target_hit_rate': 0.711,
        'brackets': {
            'limit_up':  {'prior_alpha': 15, 'prior_beta': 5,  'posterior_mean': 0.7391, 'min_change': 19.5},
            'big_surge': {'prior_alpha': 17, 'prior_beta': 8,  'posterior_mean': 0.6364, 'min_change': 10.0},
            'mid_surge': {'prior_alpha': 15, 'prior_beta': 10, 'posterior_mean': 0.5132, 'min_change': 5.0},
            'small_surge': {'prior_alpha': 11, 'prior_beta': 9, 'posterior_mean': 0.55,  'min_change': 3.0},
        },
    }

    def _get_bracket(self, change_pct: float) -> str:
        if change_pct >= 19.5: return 'limit_up'
        elif change_pct >= 10.0: return 'big_surge'
        elif change_pct >= 5.0: return 'mid_surge'
        return 'small_surge'

    def _get_posterior_mean(self, change_pct: float) -> float:
        bracket = self._get_bracket(change_pct)
        return self.M46_CALIBRATION['brackets'][bracket]['posterior_mean']

    def _compute_m46_confidence(
        self, change_pct, posterior_mean, close_position=0.5,
        upper_shadow=0, ma5_dev=0, prior_5d_return=0, volume_ratio=1.0,
    ) -> float:
        """8因子置信度评分 V2"""
        f1 = posterior_mean
        f2 = 1.0 / (1.0 + math.exp(-8 * (close_position - 0.6)))
        if change_pct > 0 and volume_ratio > 1.0:
            f3 = min(1.0, 0.4 + 0.3 * min(volume_ratio / 2.0, 1.0) + 0.3 * min(change_pct / 10.0, 1.0))
        elif change_pct > 0:
            f3 = 0.5 + 0.2 * min(change_pct / 10.0, 1.0)
        else:
            f3 = 0.3
        if ma5_dev > 0:
            if ma5_dev <= 5: f4 = 0.6 + 0.04 * ma5_dev
            elif ma5_dev <= 10: f4 = 0.8 - 0.02 * (ma5_dev - 5)
            elif ma5_dev <= 15: f4 = 0.7 - 0.04 * (ma5_dev - 10)
            else: f4 = max(0.2, 0.5 - 0.02 * (ma5_dev - 15))
        else:
            f4 = 0.5
        if prior_5d_return > 20: f5 = 0.15
        elif prior_5d_return > 15: f5 = 0.25
        elif prior_5d_return > 10: f5 = 0.35
        elif prior_5d_return > 5: f5 = 0.55
        elif prior_5d_return > 3: f5 = 0.80
        else: f5 = 0.90
        if change_pct >= 19.5: f6 = 1.0
        elif change_pct >= 15: f6 = 0.85
        elif change_pct >= 10: f6 = 0.70
        elif change_pct >= 7: f6 = 0.60
        elif change_pct >= 5: f6 = 0.50
        else: f6 = 0.35
        if upper_shadow < 0.05: f7 = 0.95
        elif upper_shadow < 0.10: f7 = 0.85
        elif upper_shadow < 0.20: f7 = 0.70
        elif upper_shadow < 0.30: f7 = 0.55
        elif upper_shadow < 0.40: f7 = 0.40
        else: f7 = 0.25
        if prior_5d_return > 10 and ma5_dev > 8: f8 = 0.15
        elif prior_5d_return > 10 or ma5_dev > 8: f8 = 0.30
        elif prior_5d_return > 5 and ma5_dev > 5: f8 = 0.50
        else: f8 = 0.75

        return round(
            0.15 * f1 + 0.15 * f2 + 0.15 * f3 + 0.10 * f4 +
            0.20 * f5 + 0.10 * f6 + 0.10 * f7 + 0.05 * f8, 4)

    # ═══════════════════════════════════════════════════════════════
    # SECTION 8: 合成数据生成器（从Deploy脚本移植）
    # ═══════════════════════════════════════════════════════════════

    def _gen_synthetic_tail_prices(self, open_p, close_p, high_p, low_p, prev_close,
                                    daily_change_pct=0.0, n_bars=30):
        """从日K线特征智能生成合成尾盘1分钟价格序列"""
        random.seed(42)
        if prev_close <= 0: prev_close = close_p
        total_change = (close_p - prev_close) / prev_close
        prices = [0.0] * n_bars

        if total_change >= 0.03:
            tail_portion = 0.38 + random.uniform(0, 0.07)
            tail_change = total_change * tail_portion
            tail_start_pct = total_change * (1 - tail_portion)
            for i in range(n_bars):
                progress = i / (n_bars - 1) if n_bars > 1 else 0
                curve = progress ** 1.6
                pct = tail_start_pct + tail_change * curve
                noise = random.gauss(0, abs(tail_change) * 0.025)
                prices[i] = prev_close * (1 + pct + noise)
        elif total_change >= 0.01:
            tail_portion = 0.25
            tail_change = total_change * tail_portion
            tail_start_pct = total_change * (1 - tail_portion)
            for i in range(n_bars):
                progress = i / (n_bars - 1) if n_bars > 1 else 0
                pct = tail_start_pct + tail_change * progress
                noise = random.gauss(0, abs(tail_change) * 0.03)
                prices[i] = prev_close * (1 + pct + noise)
        elif total_change >= -0.01:
            for i in range(n_bars):
                noise = random.gauss(0, 0.0008)
                prices[i] = close_p * (1 + noise)
        else:
            tail_portion = 0.20
            tail_change = total_change * tail_portion * 0.5
            tail_start_pct = total_change * 0.80
            for i in range(n_bars):
                progress = i / (n_bars - 1) if n_bars > 1 else 0
                pct = tail_start_pct + tail_change * progress
                noise = random.gauss(0, abs(tail_change) * 0.03)
                prices[i] = prev_close * (1 + pct + noise)

        for i in range(n_bars):
            prices[i] = round(max(low_p * 0.995, min(high_p * 1.005, prices[i])), 2)
        return prices

    def _gen_synthetic_tail_volumes(self, day_volume, daily_change_pct=0.0, n_bars=30):
        """从全天成交量智能生成合成尾盘1分钟量序列"""
        random.seed(123)
        if day_volume <= 0: day_volume = 50000000
        if daily_change_pct >= 0.05: tail_share = 0.22 + random.uniform(0, 0.06)
        elif daily_change_pct >= 0.03: tail_share = 0.18 + random.uniform(0, 0.04)
        elif daily_change_pct >= 0.01: tail_share = 0.15 + random.uniform(0, 0.03)
        else: tail_share = 0.12 + random.uniform(0, 0.03)

        total_tail_vol = day_volume * tail_share
        avg_bar_vol = total_tail_vol / n_bars
        volumes = []
        for i in range(n_bars):
            progress = i / (n_bars - 1) if n_bars > 1 else 0
            if daily_change_pct >= 0.03: factor = 0.6 + progress * 2.0
            else: factor = 0.7 + progress * 1.0
            vol = avg_bar_vol * factor * random.gauss(1.0, 0.12)
            volumes.append(max(10, round(vol, 0)))
        return volumes

    def _compute_prev_30min_avg_vol(self, day_volume, daily_change_pct=0.0):
        """计算14:00-14:30的平均per-minute成交量"""
        if day_volume <= 0: day_volume = 50000000
        if daily_change_pct >= 0.05: prev_share = 0.10
        elif daily_change_pct >= 0.03: prev_share = 0.12
        elif daily_change_pct >= 0.01: prev_share = 0.13
        else: prev_share = 0.14
        return day_volume * prev_share / 30

    # ═══════════════════════════════════════════════════════════════
    # SECTION 9: 批量分析接口
    # ═══════════════════════════════════════════════════════════════

    def build_all_m57_inputs(self, stock_list: List[Dict]) -> Dict[str, Dict]:
        """
        批量构建所有股票的M57因子输入

        Returns:
            {code: {factor_input_dict}, ...}
        """
        # 先构建stock_data_map
        stock_data_map = self.build_stock_data_map(stock_list)

        # 逐股构建M57输入
        m57_inputs = {}
        for code, data in stock_data_map.items():
            try:
                inputs = self.build_m57_factor_inputs(code, data)
                if inputs:
                    m57_inputs[code] = inputs
            except Exception as e:
                print(f"[TDXFeed] M57输入构建失败 {code}: {e}")
                continue

        print(f"[TDXFeed] M57因子输入构建完成: {len(m57_inputs)}/{len(stock_data_map)} 只")
        return m57_inputs

    def run_full_analysis(
        self,
        stock_list: List[Dict],
        verbose: bool = True,
    ) -> Dict:
        """
        运行完整的V13.2分析管线

        1. 构建stock_data_map
        2. 构建M57因子输入
        3. 调用M57引擎批量评估
        4. 调用V13.1 HolyGrail集成
        5. 返回统一结果

        Returns:
            {results, m57_results, stats, report}
        """
        if verbose:
            print("=" * 70)
            print("  V13.2 TDX实时数据集成分析")
            print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  股票数: {len(stock_list)}")
            print("=" * 70)

        # Step 1: 构建stock_data_map
        stock_data_map = self.build_stock_data_map(stock_list)
        if verbose:
            print(f"\n[Step 1] stock_data_map构建完成: {len(stock_data_map)} 只")
            q_counts = sum(1 for d in stock_data_map.values() if d.get('tdx_data_quality', 0) >= 1)
            e_counts = sum(1 for d in stock_data_map.values() if d.get('tdx_data_quality', 0) >= 2)
            print(f"  基础数据: {q_counts} 只 | 增强数据: {e_counts} 只")
        
        # ══ P0-1修复: M46批量交叉截面归一化注入 (2026-06-24) ══
        # 覆盖build_stock_data_map中的旧版M46置信度，使用归一化引擎
        try:
            from V13_2_M46_Normalized import normalize_m46_batch, get_m46_stats
            
            # 构建归一化输入
            m46_batch_input = []
            for code, data in stock_data_map.items():
                m46_batch_input.append({
                    'code': code,
                    'name': data.get('name', code),
                    'decline': data.get('change_pct', 0),
                    'amplitude': data.get('amplitude', 0),
                    'hsl': data.get('turnover_rate', 0) if data.get('turnover_rate', 0) > 0 else data.get('hsl', 0),
                    'sector': data.get('industry', ''),
                })
            
            # 构建行情映射
            quotes_map = {}
            for code, data in stock_data_map.items():
                quotes_map[code] = {
                    'price': data.get('current_price', 0),
                    'open': data.get('open', 0),
                    'high': data.get('high', 0),
                    'low': data.get('low', 0),
                    'close_prev': data.get('prev_close', 0),
                    'chg': data.get('change_pct', 0),
                    'sector_chg': data.get('sector_chg', 0),
                    'inout': data.get('net_inflow', 0) if data.get('net_inflow', 0) else 0,
                    'lb': data.get('volume_ratio', 1.0),
                }
            
            m46_batch = normalize_m46_batch(m46_batch_input, quotes_map)
            m46_stats = get_m46_stats(m46_batch)
            
            # 覆盖stock_data_map中的M46值
            for r in m46_batch:
                if r.code in stock_data_map:
                    stock_data_map[r.code]['m46_confidence'] = r.m46_normalized
                    stock_data_map[r.code]['m46_recommended'] = r.recommendation in ('STRONG_BUY', 'BUY')
                    stock_data_map[r.code]['m46_bracket'] = r.bracket
                    stock_data_map[r.code]['m46_raw_total'] = r.raw_score
                    stock_data_map[r.code]['m46_z_score'] = r.z_score
            
            if verbose:
                print(f"   📊 M46归一化: μ={m46_stats['mean']:.4f} σ={m46_stats['std']:.4f} 区分度={m46_stats['range']:.4f}")
                print(f"      SB={m46_stats['strong_buy_pct']}% BUY={m46_stats['buy_pct']}% WATCH={m46_stats['watch_pct']}% HOLD={m46_stats['hold_pct']}%")
        except Exception as e:
            if verbose:
                print(f"   ⚠️ M46归一化注入失败(使用旧版): {e}")

        # Step 2: 构建M57输入并运行
        m57_results = {}
        try:
            from V13_1_M57_OvernightAlphaEngine import OvernightAlphaEngine
            alpha_engine = OvernightAlphaEngine()
            m57_inputs = self.build_all_m57_inputs(stock_list)

            factor_list = []
            for code, inputs in m57_inputs.items():
                try:
                    factors = alpha_engine.compute_all_factors(**inputs)
                    factor_list.append(factors)
                except Exception as e:
                    if verbose:
                        print(f"  [M57] {code} 因子计算失败: {e}")

            if factor_list:
                factor_list = alpha_engine.batch_evaluate(factor_list)
                for f in factor_list:
                    m57_results[f.code] = {
                        'composite_score': f.composite_score,
                        't1_return_forecast': f.t1_return_forecast,
                        'tail_rs': f.tail_rs,
                        'tail_vol_struct': f.tail_vol_struct,
                        'overnight_mom': f.overnight_mom,
                        'intraday_rev': f.intraday_rev,
                        'flow_accel': f.flow_accel,
                        'auction_sig': f.auction_sig,
                        'sector_alpha': f.sector_alpha,
                        'streak_exp': f.streak_exp,
                        'gap_fill_prob': f.gap_fill_prob,
                        'event_decay': f.event_decay,
                        'lhb_effect': f.lhb_effect,
                        'sentiment_trans': f.sentiment_trans,
                    }
                if verbose:
                    print(f"\n[Step 2] M57 Alpha引擎完成: {len(m57_results)} 只")
                    activated = sum(1 for r in m57_results.values() if any([
                        r.get('overnight_mom', 0) != 0,
                        r.get('event_decay', 0) != 0,
                        r.get('lhb_effect', 0) != 0,
                        r.get('gap_fill_prob', 0) != 0.5,
                    ]))
                    print(f"  激活因子数: {activated}/{len(m57_results)}")

        except ImportError as e:
            if verbose:
                print(f"\n[Step 2] M57引擎不可用: {e}")

        # Step 3: 运行V13.1 HolyGrail集成
        v131_results = {}
        try:
            from V13_1_HolyGrailIntegration import HolyGrailIntegrator
            integrator = HolyGrailIntegrator()

            results = []
            for code, data in stock_data_map.items():
                try:
                    result = integrator.analyze_stock(
                        code=code,
                        name=data.get('name', code),
                        listed_days=data.get('listed_days', 9999),
                        avg_volume_yuan=data.get('avg_volume_yuan', 0),
                        current_price=data.get('current_price', 0),
                        prev_close=data.get('prev_close', 0),
                        tail_1min_prices=data.get('tail_prices'),
                        tail_1min_volumes=data.get('tail_volumes'),
                        prev_30_avg_volume=data.get('prev_30_avg_vol', 0),
                        ma20_price=data.get('ma20'),
                        high_20d_price=data.get('high_20d'),
                        sector_data=data.get('sector_data'),
                        intraday_change_pct=data.get('intraday_change_pct', 0),
                        tail_30min_change_pct=data.get('tail_30min_change_pct', 0),
                        day_low_pct=data.get('day_low_pct', 0),
                        day_close_pct=data.get('day_close_pct', 0),
                        total_day_volume=data.get('total_day_volume', 0),
                        tail_30min_volume=data.get('tail_30min_volume', 0),
                    )
                    if result is not None:
                        result.m46_confidence = data.get('m46_confidence', 0)
                        results.append(result)
                except Exception as e:
                    if verbose:
                        print(f"  [V13.1] {code} 分析失败: {e}")

            if verbose:
                print(f"\n[Step 3] V13.1 HolyGrail集成完成: {len(results)} 只")

        except ImportError as e:
            if verbose:
                print(f"\n[Step 3] V13.1集成层不可用: {e}")

        # Step 4: 合并结果
        final_results = []
        for code, data in stock_data_map.items():
            m57 = m57_results.get(code, {})
            entry = {
                'code': code,
                'name': data.get('name', code),
                'change_pct': data.get('change_pct', 0),
                'current_price': data.get('current_price', 0),
                'm46_confidence': data.get('m46_confidence', 0),
                'm46_recommended': data.get('m46_recommended', False),
                'm46_bracket': data.get('m46_bracket', ''),
                'm57_composite': m57.get('composite_score', 0),
                'm57_t1_forecast': m57.get('t1_return_forecast', 0),
                'tdx_data_quality': data.get('tdx_data_quality', 0),
                'has_capital_flow': data.get('has_capital_flow', False),
                'has_dragon_tiger': data.get('has_dragon_tiger', False),
                'close_position': data.get('close_position', 0.5),
                'upper_shadow': data.get('upper_shadow', 0),
                'ma5_dev': data.get('ma5_dev', 0),
                'prior_5d_return': data.get('prior_5d_return', 0),
                'volume_ratio': data.get('volume_ratio', 1.0),
                # M57因子详情
                **{k: v for k, v in m57.items() if k not in ('composite_score', 't1_return_forecast')},
            }

            # 综合评分: M46(40%) + M57(40%) + 行情质量(20%)
            m46_score = data.get('m46_confidence', 0)
            m57_score = max(0, m57.get('composite_score', 0))  # tanh可能为负
            quality_score = min(1.0, data.get('tdx_data_quality', 0) / 3.0)

            entry['v132_score'] = round(0.40 * m46_score + 0.40 * m57_score + 0.20 * quality_score, 4)

            # 推荐
            if entry['v132_score'] >= 0.70:
                entry['recommendation'] = 'STRONG_BUY'
            elif entry['v132_score'] >= 0.55:
                entry['recommendation'] = 'BUY'
            elif entry['v132_score'] >= 0.40:
                entry['recommendation'] = 'WATCH'
            else:
                entry['recommendation'] = 'HOLD'

            final_results.append(entry)

        # 按综合评分排序
        final_results.sort(key=lambda x: x.get('v132_score', 0), reverse=True)

        # 统计
        stats = {
            'total_stocks': len(final_results),
            'strong_buy': sum(1 for r in final_results if r.get('recommendation') == 'STRONG_BUY'),
            'buy': sum(1 for r in final_results if r.get('recommendation') == 'BUY'),
            'watch': sum(1 for r in final_results if r.get('recommendation') == 'WATCH'),
            'hold': sum(1 for r in final_results if r.get('recommendation') == 'HOLD'),
            'm46_recommended': sum(1 for r in final_results if r.get('m46_recommended')),
            'avg_v132_score': round(sum(r.get('v132_score', 0) for r in final_results) / max(1, len(final_results)), 4),
            'avg_m57_composite': round(sum(r.get('m57_composite', 0) for r in final_results) / max(1, len(final_results)), 4),
            'm57_activated': sum(1 for r in final_results if any([
                r.get('overnight_mom', 0) != 0,
                r.get('event_decay', 0) != 0,
                r.get('lhb_effect', 0) != 0,
            ])),
        }

        if verbose:
            print(f"\n{'=' * 70}")
            print(f"  V13.2 分析完成")
            print(f"  STRONG_BUY: {stats['strong_buy']} | BUY: {stats['buy']} | "
                  f"WATCH: {stats['watch']} | HOLD: {stats['hold']}")
            print(f"  M46推荐: {stats['m46_recommended']} | M57激活: {stats['m57_activated']}")
            print(f"  平均V13.2评分: {stats['avg_v132_score']}")
            print(f"{'=' * 70}")

        return {
            'results': final_results,
            'm57_results': m57_results,
            'stock_data_map': stock_data_map,
            'stats': stats,
        }

    # ═══════════════════════════════════════════════════════════════
    # SECTION 10: 数据质量报告
    # ═══════════════════════════════════════════════════════════════

    def get_data_quality_report(self) -> Dict:
        """生成数据质量报告"""
        total = len(self.stocks)
        if total == 0:
            return {'total': 0, 'message': '无数据'}

        q1 = sum(1 for s in self.stocks.values() if s.data_quality >= 1)
        q2 = sum(1 for s in self.stocks.values() if s.data_quality >= 2)
        q3 = sum(1 for s in self.stocks.values() if s.data_quality >= 3)

        has_quote = sum(1 for s in self.stocks.values() if s.quote)
        has_daily = sum(1 for s in self.stocks.values() if s.kline_daily)
        has_1min  = sum(1 for s in self.stocks.values() if s.kline_1min)
        has_cf    = sum(1 for s in self.stocks.values() if s.capital_flow)
        has_lhb   = sum(1 for s in self.stocks.values() if s.dragon_tiger)
        has_ztfx  = sum(1 for s in self.stocks.values() if s.limit_up_analysis)
        has_inst  = sum(1 for s in self.stocks.values() if s.inst_holding)
        has_sh    = sum(1 for s in self.stocks.values() if s.top_shareholders)
        has_news  = sum(1 for s in self.stocks.values() if s.news)
        has_ind   = sum(1 for s in self.stocks.values() if s.indicator)

        return {
            'total_stocks': total,
            'quality_distribution': {
                'Q1_basic': q1,
                'Q2_enriched': q2,
                'Q3_full': q3,
            },
            'data_coverage': {
                'realtime_quote': has_quote,
                'daily_kline': has_daily,
                '1min_kline': has_1min,
                'capital_flow': has_cf,
                'dragon_tiger': has_lhb,
                'limit_up_analysis': has_ztfx,
                'institutional_holding': has_inst,
                'top_shareholders': has_sh,
                'news': has_news,
                'indicator': has_ind,
            },
            'coverage_pct': {
                k: round(v / total * 100, 1) for k, v in {
                    'realtime_quote': has_quote,
                    'daily_kline': has_daily,
                    '1min_kline': has_1min,
                    'capital_flow': has_cf,
                    'dragon_tiger': has_lhb,
                    'limit_up_analysis': has_ztfx,
                }.items()
            },
        }

    def generate_report(self) -> str:
        """生成数据质量报告文本"""
        rpt = self.get_data_quality_report()
        lines = [
            "=" * 70,
            "  V13.2 TDX数据质量报告",
            f"  时间: {self.fetch_timestamp or datetime.now().isoformat()}",
            "=" * 70,
            f"\n总股票数: {rpt['total_stocks']}",
            f"\n数据质量分布:",
            f"  Q1 基础数据 (行情+K线): {rpt['quality_distribution']['Q1_basic']} 只",
            f"  Q2 增强数据 (+资金流/龙虎榜): {rpt['quality_distribution']['Q2_enriched']} 只",
            f"  Q3 完整数据 (+财务/新闻): {rpt['quality_distribution']['Q3_full']} 只",
            f"\n数据覆盖率:",
        ]
        for k, v in rpt['coverage_pct'].items():
            bar = '█' * int(v / 5) + '░' * (20 - int(v / 5))
            lines.append(f"  {k:25s} {bar} {v:5.1f}%")

        lines.append("\n" + "=" * 70)
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 11: Agent数据采集指令模板
# ═══════════════════════════════════════════════════════════════

AGENT_FETCH_TEMPLATE = """
# TDX数据采集指令模板 (Agent执行)

## Tier1: 实时行情 (交易时段9:30-15:00)
# 1. 获取实时行情
tdx_quotes(code="600519", setcode="1")
# 2. 获取日K线 (60根)
tdx_kline(code="600519", setcode="1", period=7, count=60)
# 3. 获取1分钟K线 (尾盘30根)
tdx_kline(code="600519", setcode="1", period=0, count=30)
# 4. 涨停股筛选
tdx_screener(query="涨停", market="A股")

## Tier2: 增强数据 (24/7可查)
# 5. 资金流向
tdx_api_data(code="600519", setcode="1", fixedTag="zjlx")
# 6. 涨停分析
tdx_api_data(code="600519", setcode="1", fixedTag="ztfx")
# 7. 龙虎榜
tdx_api_data(code="600519", setcode="1", fixedTag="jglhb")
# 8. 十大流通股东
tdx_api_data(code="600519", setcode="1", fixedTag="ltgd")
# 9. 机构持股
tdx_api_data(code="600519", setcode="1", fixedTag="jgcg")

## Tier3: 深度研究 (24/7可查)
# 10. PE/PB指标
tdx_indicator_select(code="600519", setcode="1")
# 11. 新闻
wenda_news_query(query="600519", count=10)
# 12. 公告
wenda_notice_query(query="600519", count=10)
# 13. 研报
wenda_report_query(query="600519", count=5)
"""

# 缓存JSON格式模板
CACHE_JSON_TEMPLATE = {
    "fetch_time": "2026-06-23T14:30:00",
    "stocks": {
        "600519": {
            "name": "贵州茅台",
            "setcode": "1",
            "quote": {},          # tdx_quotes原始返回
            "kline": {},           # tdx_kline 日K线原始返回
            "kline_1min": {},      # tdx_kline 1分钟K线原始返回
            "capital_flow": {},    # tdx_api_data zjlx
            "limit_up_analysis": {},  # tdx_api_data ztfx
            "dragon_tiger": {},    # tdx_api_data jglhb
            "top_shareholders": {},  # tdx_api_data ltgd
            "inst_holding": {},    # tdx_api_data jgcg
            "indicator": {},       # tdx_indicator_select
            "news": [],            # wenda_news_query
            "notices": [],         # wenda_notice_query
        }
    },
    "screener": {},  # tdx_screener结果
    "macro": {},     # wenda_macro_query结果
}


# ═══════════════════════════════════════════════════════════════
# 导出
# ═══════════════════════════════════════════════════════════════

__all__ = [
    'TDXRealtimeFeed',
    'TDXStockData',
    'TDX_TOOLS',
    'API_ROUTES',
    'SETCODE_MAP',
    'infer_setcode',
    'infer_board_tier',
    'get_limit_up_pct',
    'AGENT_FETCH_TEMPLATE',
    'CACHE_JSON_TEMPLATE',
]


# ═══════════════════════════════════════════════════════════════
# 主入口: 命令行测试
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='V13.2 TDX实时数据集成层')
    parser.add_argument('--cache', default='data/tdx_1430_cache.json', help='缓存JSON文件路径')
    parser.add_argument('--report', action='store_true', help='生成数据质量报告')
    args = parser.parse_args()

    feed = TDXRealtimeFeed(cache_dir=os.path.dirname(args.cache) or '.')

    if feed.load_from_cache(args.cache):
        print(feed.generate_report())

        # 测试构建stock_data_map
        stock_list = [{'code': code, 'name': s.name} for code, s in feed.stocks.items()]
        if stock_list:
            print(f"\n测试构建stock_data_map ({len(stock_list)} 只)...")
            sdm = feed.build_stock_data_map(stock_list)
            print(f"  成功: {len(sdm)} 只")

            # 测试M57输入构建
            if sdm:
                first_code = list(sdm.keys())[0]
                print(f"\n测试M57因子输入构建 ({first_code})...")
                m57_in = feed.build_m57_factor_inputs(first_code, sdm[first_code])
                activated = sum(1 for v in m57_in.values() if v != 0 and v != [] and v != 0.5)
                print(f"  输入参数: {len(m57_in)} 个")
                print(f"  非零参数: {activated} 个")
                for k, v in m57_in.items():
                    if isinstance(v, (int, float)) and v != 0:
                        print(f"    {k}: {v}")
                    elif isinstance(v, list) and v:
                        print(f"    {k}: [{len(v)} items] e.g. {v[:3]}")
    else:
        print("缓存加载失败，请检查文件路径")
        print("\n缓存JSON格式模板:")
        print(json.dumps(CACHE_JSON_TEMPLATE, indent=2, ensure_ascii=False))
