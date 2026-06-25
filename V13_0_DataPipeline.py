#!/usr/bin/env python3
"""
V13.0 TDX→引擎 实盘数据管线
============================
S-2 阻塞项解决：打通 TDX MCP 实时数据 → V13.0 引擎全链路

核心功能：
  1. MarketDataFetcher  — TDX MCP查询模板（供外部调度器调用）
  2. DataNormalizer     — 原始数据标准化为引擎输入格式
  3. 批量化处理          — 一次拉取N只股票，批量注入流水线
  4. 缓存层             — 15分钟内存缓存，避免重复查询

数据流：
  TDX MCP → MarketDataFetcher(查询) → DataNormalizer(清洗) → Engine(消费)

引擎消费方覆盖：
  - T1TailScreener: 日涨跌幅/换手率/量比/市值/尾盘量/均价线
  - PatternDetector: OHLCV数组/MA5-120/MACD/成交量MA
  - TrapDetector: PE/板块PE/增长/减持/解禁/监管/ST
  - 7WeightFusion: 各维度打分输入
  - M46Bayesian: 行业/板块/价格序列
  - M51Intent: 大单/机构/北向/龙虎榜
  - M54Position: 最高/最低/收盘价序列
"""

import json
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from collections import OrderedDict


# ═══════════════════════════════════════════════
# 市场代码映射
# ═══════════════════════════════════════════════

SETCODE_MAP = {
    'SH': '1', 'sh': '1', '沪': '1', '上海': '1',
    'SZ': '0', 'sz': '0', '深': '0', '深圳': '0',
    'BJ': '2', 'bj': '2', '北': '2', '北交所': '2',
}

def get_setcode(code: str) -> str:
    """根据代码推断市场：6开头→沪(1)，0/3开头→深(0)，8开头→北(2)"""
    if not code:
        return '0'
    if code.startswith('6'):
        return '1'
    elif code.startswith('8') or code.startswith('4'):
        return '2'
    else:
        return '0'


# ═══════════════════════════════════════════════
# 行业映射（M46引擎需要）
# ═══════════════════════════════════════════════

INDUSTRY_MAP = {
    '半导体': '半导体设备/材料', '芯片': '半导体设备/材料', '集成电路': '半导体设备/材料',
    'AI': 'AI算力/服务器', '人工智能': 'AI算力/服务器', '算力': 'AI算力/服务器',
    '机器人': '人形机器人', '自动化': '人形机器人',
    '新能源': '新能源/储能', '光伏': '新能源/储能', '储能': '新能源/储能', '锂电': '新能源/储能',
    '医药': '医药/创新药', '创新药': '医药/创新药', '医疗': '医药/创新药',
    '军工': '军工/航天', '航天': '军工/航天',
    '汽车': '汽车/零部件', '零部件': '汽车/零部件',
    '消费': '消费/食品饮料', '食品': '消费/食品饮料', '饮料': '消费/食品饮料',
    '有色': '有色/化工', '化工': '有色/化工', '稀土': '有色/化工',
    '金融': '金融/券商', '券商': '金融/券商', '银行': '金融/券商',
    '电力': '特高压/电力设备', '特高压': '特高压/电力设备', '电网': '特高压/电力设备',
    '量子': '量子/光子', '光子': '量子/光子',
}


def infer_industry(name: str, sector: str = '') -> str:
    """从股票名称和板块推测行业"""
    for keyword, industry in INDUSTRY_MAP.items():
        if keyword in sector or keyword in name:
            return industry
    return '通用'


# ═══════════════════════════════════════════════
# TDX MCP 查询模板
# ═══════════════════════════════════════════════

@dataclass
class TdxQuery:
    """单条TDX查询封装"""
    tool: str                     # MCP工具名
    params: dict                  # 参数
    purpose: str = ''             # 用途说明
    depends_on: List[str] = field(default_factory=list)  # 依赖的前置查询


class TdxQueryBuilder:
    """TDX MCP查询构建器——生成给外部调度器执行的查询模板"""

    @staticmethod
    def quote(code: str) -> TdxQuery:
        """实时行情查询"""
        setcode = get_setcode(code)
        return TdxQuery(
            tool='tdx_quotes',
            params={'code': code, 'setcode': setcode, 'hasCalcInfo': '1', 'bspNum': '5'},
            purpose=f'{code}实时行情+盘口+计算指标',
        )

    @staticmethod
    def kline_daily(code: str, count: int = 150) -> TdxQuery:
        """日K线查询（含M120需要的150日）"""
        setcode = get_setcode(code)
        return TdxQuery(
            tool='tdx_kline',
            params={'code': code, 'setcode': setcode, 'period': '4', 'wantNum': str(count), 'tqFlag': '11'},
            purpose=f'{code}日K线x{count}(前复权)',
        )

    @staticmethod
    def kline_weekly(code: str, count: int = 60) -> TdxQuery:
        """周K线（月线MACD需要）"""
        setcode = get_setcode(code)
        return TdxQuery(
            tool='tdx_kline',
            params={'code': code, 'setcode': setcode, 'period': '5', 'wantNum': str(count), 'tqFlag': '11'},
            purpose=f'{code}周K线x{count}',
        )

    @staticmethod
    def indicator(code: str, indicators: List[str] = None) -> TdxQuery:
        """财务指标查询"""
        msg = f'{code}的市盈率、市净率、ROE、营收增长率、净利润增长率、毛利率、资产负债率、股东人数、流通市值'
        if indicators:
            msg = f'{code}的' + '、'.join(indicators)
        return TdxQuery(
            tool='tdx_indicator_select',
            params={'message': msg, 'rang': 'AG'},
            purpose=f'{code}财务指标',
        )

    @staticmethod
    def screener(query: str, page_size: int = 30) -> TdxQuery:
        """条件选股"""
        return TdxQuery(
            tool='tdx_screener',
            params={'message': query, 'rang': 'AG', 'pageSize': str(page_size)},
            purpose=f'选股: {query}',
        )

    @staticmethod
    def lookup(keyword: str) -> TdxQuery:
        """代码查询"""
        return TdxQuery(
            tool='tdx_lookup_stock',
            params={'query': keyword, 'range': 'AG'},
            purpose=f'搜索: {keyword}',
        )

    @staticmethod
    def news(keyword: str, limit: int = 10) -> TdxQuery:
        """资讯查询"""
        return TdxQuery(
            tool='wenda_news_query',
            params={'query': f'查询{keyword}最近一周的资讯', 'top_k': limit},
            purpose=f'{keyword}资讯',
        )

    @staticmethod
    def notice(code: str, limit: int = 10) -> TdxQuery:
        """公告查询"""
        return TdxQuery(
            tool='wenda_notice_query',
            params={'query': f'查询{code}最近一个月的公告', 'top_k': limit},
            purpose=f'{code}公告',
        )

    @staticmethod
    def macro(indicator: str) -> TdxQuery:
        """宏观数据"""
        today = datetime.now()
        start = (today - timedelta(days=365)).strftime('%Y%m%d')
        end = today.strftime('%Y%m%d')
        return TdxQuery(
            tool='wenda_macro_query',
            params={'query': f'{indicator}|{start}|{end}||{indicator}'},
            purpose=f'{indicator}宏观数据',
        )

    @staticmethod
    def full_suite_for_stock(code: str) -> List[TdxQuery]:
        """为一只股票构建完整查询套件"""
        return [
            TdxQueryBuilder.quote(code),
            TdxQueryBuilder.kline_daily(code),
            TdxQueryBuilder.kline_weekly(code),
            TdxQueryBuilder.indicator(code),
        ]

    @staticmethod
    def tail_market_screening_suite() -> List[TdxQuery]:
        """
        尾盘选股完整查询套件
        组合查询：尾盘放量＋涨幅候选＋条件筛选
        """
        return [
            TdxQueryBuilder.screener('放量上涨', page_size=50),
            TdxQueryBuilder.screener('尾盘放量', page_size=50),
            TdxQueryBuilder.screener('主力净流入', page_size=30),
            TdxQueryBuilder.screener('MACD金叉', page_size=30),
            TdxQueryBuilder.macro('市场情绪'),
        ]


# ═══════════════════════════════════════════════
# 数据标准化器
# ═══════════════════════════════════════════════

class DataNormalizer:
    """
    将TDX MCP原始响应标准化为V13.0引擎输入格式

    输入：TDX MCP返回的原始dict
    输出：V13.0引擎可直接消费的标准化dict
    """

    def __init__(self):
        self._cache: OrderedDict = OrderedDict()
        self._cache_max_size = 200
        self._cache_ttl = 900  # 15分钟

    def _cache_key(self, code: str, data_type: str) -> str:
        return f"{code}:{data_type}"

    def _cache_get(self, key: str):
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry['ts'] < self._cache_ttl:
                return entry['data']
            del self._cache[key]
        return None

    def _cache_set(self, key: str, data: dict):
        if len(self._cache) >= self._cache_max_size:
            self._cache.popitem(last=False)
        self._cache[key] = {'ts': time.time(), 'data': data}

    # ═══════════════════════════════════════════════
    # 原始数据标准化
    # ═══════════════════════════════════════════════

    def normalize_quote(self, raw: dict, code: str, name: str = '') -> dict:
        """
        标准化实时行情 → T1TailScreener输入

        期望raw结构:
        {
            'response': {
                'transformed': {
                    'hq': {'price': ..., 'changePct': ..., 'volume': ..., ...},
                    'ext': {'liutongCap': ..., 'turnoverRate': ..., ...},
                    'pro': {'weiBi': ..., 'liangBi': ..., ...},
                    'calc': {'bigOrderRatio': ..., 'tailVolumeRatio': ..., ...}
                }
            }
        }
        """
        cache_key = self._cache_key(code, 'quote')
        cached = self._cache_get(cache_key)
        if cached:
            return cached

            # 检测并处理TDX原始格式
        if isinstance(raw, dict) and 'HQInfo' in raw:
            print(f"    🔍 检测到TDX原始格式quote，调用_normalize_quote_tdx()")
            result = self._normalize_quote_tdx(raw, code, name)
            print(f"    ✅ 提取结果: price={result.get('current_price', 0):.2f}, change_pct={result.get('daily_change_pct', 0):.4f}, turnover={result.get('turnover_rate', 0):.4f}")
            return result

        # 兼容两种TDX返回格式
        if isinstance(raw, dict):
            data = raw.get('response', {}).get('transformed', raw)
        else:
            data = raw if isinstance(raw, dict) else {}

        hq = data.get('hq', data)
        ext = data.get('ext', {})
        pro = data.get('pro', {})
        calc = data.get('calc', {})

        # 安全取值
        price = float(hq.get('price', hq.get('lastPrice', hq.get('close', 0))) or 0)
        open_p = float(hq.get('open', hq.get('openPrice', 0)) or 0)
        high = float(hq.get('high', hq.get('highPrice', 0)) or 0)
        low = float(hq.get('low', hq.get('lowPrice', 0)) or 0)
        change_pct = float(hq.get('changePct', hq.get('pctChange', 0)) or 0) / 100.0 if abs(float(hq.get('changePct', hq.get('pctChange', 0)) or 0)) > 1 else float(hq.get('changePct', hq.get('pctChange', 0)) or 0)
        volume = float(hq.get('volume', hq.get('totalVolume', 0)) or 0)
        amount = float(hq.get('amount', hq.get('totalAmount', 0)) or 0)

        turnover = float(ext.get('turnoverRate', ext.get('turnover', 0)) or 0) / 100.0 if abs(float(ext.get('turnoverRate', ext.get('turnover', 0)) or 0)) > 1 else float(ext.get('turnoverRate', ext.get('turnover', 0)) or 0)
        market_cap = float(ext.get('liutongCap', ext.get('floatCap', 0)) or 0)

        volume_ratio = float(pro.get('liangBi', pro.get('volumeRatio', 1.0)) or 1.0)
        wei_bi = float(pro.get('weiBi', pro.get('bidAskRatio', 0)) or 0)

        big_order_ratio = float(calc.get('bigOrderRatio', calc.get('largeOrderRatio', 0)) or 0)
        big_order_net = float(calc.get('bigOrderNet', calc.get('largeOrderNet', 0)) or 0)
        tail_vol_ratio = float(calc.get('tailVolumeRatio', 0.28) or 0.28)
        above_avg = bool(calc.get('aboveAvgLine', True))

        result = {
            'code': code,
            'name': name,
            'current_price': price,
            'open': open_p,
            'high': high,
            'low': low,
            'daily_change_pct': change_pct,
            'volume': volume,
            'amount': amount,
            'turnover_rate': turnover,
            'market_cap': market_cap,
            'volume_ratio': volume_ratio,
            'wei_bi': wei_bi,
            'big_order_ratio': big_order_ratio,
            'big_order_net': big_order_net,
            'tail_volume_ratio': tail_vol_ratio,
            'above_avg_line': above_avg,
            'setcode': get_setcode(code),
        }

        self._cache_set(cache_key, result)
        return result

    def normalize_kline(self, raw: dict, code: str, name: str = '') -> dict:
        """
        标准化日K线 → PatternDetector输入

        期望raw['response']['transformed']['klines']:
        [{open, high, low, close, volume, amount, ...}, ...]

        返回 OHLCV数组 + 技术指标
        """
        cache_key = self._cache_key(code, 'kline')
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        # 检测TDX原始格式
        if isinstance(raw, dict) and 'ListItem' in raw:
            return self._normalize_kline_tdx(raw, code, name)

        if isinstance(raw, dict):
            data = raw.get('response', {}).get('transformed', raw)
            klines = data.get('klines', data.get('kline_data', []))
        elif isinstance(raw, list):
            klines = raw
        else:
            klines = []

        if not klines:
            return {}

        closes = []
        opens = []
        highs = []
        lows = []
        volumes = []
        amounts = []

        for k in klines:
            if not isinstance(k, dict):
                continue
            closes.append(float(k.get('close', k.get('c', 0)) or 0))
            opens.append(float(k.get('open', k.get('o', 0)) or 0))
            highs.append(float(k.get('high', k.get('h', 0)) or 0))
            lows.append(float(k.get('low', k.get('l', 0)) or 0))
            volumes.append(float(k.get('volume', k.get('v', 0)) or 0))
            amounts.append(float(k.get('amount', k.get('a', 0)) or 0))

        if not closes:
            return {}

        # 计算均线
        def sma(arr: List[float], period: int) -> List[float]:
            result = []
            for i in range(len(arr)):
                if i + 1 < period:
                    result.append(sum(arr[:i+1]) / (i+1))
                else:
                    result.append(sum(arr[i-period+1:i+1]) / period)
            return result

        def ema(arr: List[float], period: int) -> List[float]:
            result = []
            multiplier = 2 / (period + 1)
            for i, val in enumerate(arr):
                if i == 0:
                    result.append(val)
                else:
                    result.append(val * multiplier + result[-1] * (1 - multiplier))
            return result

        ma5 = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)
        ma25 = sma(closes, 25)
        ma60 = sma(closes, 60) if len(closes) >= 60 else sma(closes, len(closes))
        ma120 = sma(closes, 120) if len(closes) >= 120 else sma(closes, len(closes))
        vol_ma5 = sma(volumes, 5)
        vol_ma60 = sma(volumes, 60) if len(volumes) >= 60 else sma(volumes, len(volumes))

        # MACD
        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        dea = ema(dif, 9)
        macd_hist = [2 * (d - e) for d, e in zip(dif, dea)]

        # ATR
        atr = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            if i < 14:
                atr.append(tr)
            else:
                atr_val = atr[-1] if atr else tr
                atr.append((atr_val * 13 + tr) / 14)
        if atr:
            atr_list = [atr[0]] + atr
        else:
            atr_list = [0] * len(closes)

        # 20日波动率
        returns = []
        for i in range(1, len(closes)):
            if closes[i-1] > 0:
                returns.append((closes[i] - closes[i-1]) / closes[i-1])

        mean_ret = sum(returns) / len(returns) if returns else 0
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns) if returns else 0
        volatility_20d = variance ** 0.5 * (252 ** 0.5)

        result = {
            'code': code,
            'name': name,
            'prices': closes,
            'opens': opens,
            'highs': highs,
            'lows': lows,
            'volumes': volumes,
            'amounts': amounts,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'ma25': ma25,
            'ma60': ma60,
            'ma120': ma120,
            'macd_dif': dif,
            'macd_dea': dea,
            'macd_hist': macd_hist,
            'vol_ma5': vol_ma5,
            'vol_ma60': vol_ma60,
            'atr_14': atr_list,
            'volatility_20d': round(volatility_20d, 4),
            'close': closes[-1] if closes else 0,
            'price_position': self._price_position(closes, ma60),
        }

        self._cache_set(cache_key, result)
        return result

    def _price_position(self, prices: List[float], ma60: List[float]) -> str:
        """判断价格位置：低位/中位/高位"""
        if not prices or not ma60:
            return '中位'
        current = prices[-1]
        ma60_val = ma60[-1]
        if ma60_val > 0:
            ratio = current / ma60_val
            if ratio < 0.85:
                return '低位'
            elif ratio > 1.30:
                return '高位'
        return '中位'

    def normalize_indicator(self, raw: dict, code: str) -> dict:
        """
        标准化财务指标 → TrapDetector / M46输入
        
        返回: PE/PB/ROE/增长/质押/商誉等
        """
        # 检测并处理TDX原始格式
        if isinstance(raw, dict) and any(k in raw for k in ['市盈率PE', '市净率PB', '净资产收益率ROE']):
            return self._normalize_indicator_tdx(raw, code)

        if isinstance(raw, dict):
            data = raw.get('response', {}).get('transformed', raw)
        else:
            data = raw if isinstance(raw, dict) else {}

        def _f(key, default=0.0):
            val = data.get(key, default)
            try:
                return float(val) if val and val != '--' else default
            except (ValueError, TypeError):
                return default

        return {
            'code': code,
            'pe': _f('pe', _f('市盈率', 0)),
            'pb': _f('pb', _f('市净率', 0)),
            'roe': _f('roe', _f('ROE', 0)),
            'revenue_growth': _f('revenueGrowth', _f('营收增长率', 0)),
            'profit_growth': _f('profitGrowth', _f('净利润增长率', 0)),
            'gross_margin': _f('grossMargin', _f('毛利率', 0)),
            'debt_ratio': _f('debtRatio', _f('资产负债率', 0)),
            'pledge_ratio': _f('pledgeRatio', _f('质押比例', 0)),
            'goodwill_ratio': _f('goodwillRatio', _f('商誉占比', 0)),
            'holder_count': _f('holderCount', _f('股东人数', 0)),
            'cash_flow_ratio': _f('cashFlowRatio', _f('现金流比例', 0)),
            'ar_ratio': _f('arRatio', _f('应收占比', 0)),
        }

    def normalize_news(self, raw: dict) -> dict:
        """标准化资讯 → 舆情打分"""
        if isinstance(raw, dict):
            data = raw.get('response', {}).get('transformed', raw)
        else:
            data = raw if isinstance(raw, dict) else {}

        items = data.get('items', data.get('news', data.get('results', [])))
        if not isinstance(items, list):
            items = []

        # 简单舆情计分(v0.1): 正面词+1，负面词-1
        positive_words = ['涨停', '大涨', '利好', '突破', '增持', '预增', '中标', '扩产', '创新', '领先']
        negative_words = ['跌停', '大跌', '利空', '减持', '预亏', '亏损', '退市', '违约', '诉讼', '爆雷']

        score = 0.0
        news_count = 0
        for item in items:
            if isinstance(item, dict):
                text = item.get('title', item.get('summary', ''))
            else:
                text = str(item)
            pos = sum(1 for w in positive_words if w in text)
            neg = sum(1 for w in negative_words if w in text)
            score += (pos - neg) * 0.1
            news_count += 1

        sentiment_score = max(0.0, min(1.0, 0.5 + score))
        heat_score = min(1.0, news_count / 10.0)

        return {
            'sentiment_score': round(sentiment_score, 4),
            'heat_score': round(heat_score, 4),
            'news_count': news_count,
            'sentiment_label': '正面' if sentiment_score > 0.6 else ('负面' if sentiment_score < 0.4 else '中性'),
        }

    def normalize_notice(self, raw: dict) -> dict:
        """标准化公告 → 地雷检测"""
        if isinstance(raw, dict):
            data = raw.get('response', {}).get('transformed', raw)
        else:
            data = raw if isinstance(raw, dict) else {}

        items = data.get('items', data.get('notices', data.get('results', [])))
        if not isinstance(items, list):
            items = []

        # 风险词检测
        reduction_keywords = ['减持', '减持计划', '股东减持', '大宗交易']
        unlock_keywords = ['解禁', '限售股', '解除限售']
        regulatory_keywords = ['监管函', '问询函', '立案调查', '警示函', '责令改正']
        st_keywords = ['特别处理', '*ST', 'ST', '退市风险']
        earnings_keywords = ['预亏', '业绩预告.*亏损', '商誉减值', '计提减值']

        has_reduction = False
        has_unlock = False
        has_regulatory = False
        st_risk = False
        earnings_risk = False

        for item in items:
            if isinstance(item, dict):
                text = item.get('title', item.get('summary', ''))
            else:
                text = str(item)

            if any(kw in text for kw in reduction_keywords):
                has_reduction = True
            if any(kw in text for kw in unlock_keywords):
                has_unlock = True
            if any(kw in text for kw in regulatory_keywords):
                has_regulatory = True
            if any(kw in text for kw in st_keywords):
                st_risk = True
            if any(kw in text for kw in earnings_keywords):
                earnings_risk = True

        return {
            'has_reduction': has_reduction,
            'has_unlock': has_unlock,
            'has_regulatory_warning': has_regulatory,
            'st_risk': st_risk,
            'earnings_cliff': earnings_risk,
            'total_notices': len(items),
        }

    # ═══════════════════════════════════════════════
    # 统一标准化——合并各数据源为完整股票dict
    # ═══════════════════════════════════════════════

    def prepare_stock(
        self,
        code: str,
        name: str,
        raw_quote: dict = None,
        raw_kline: dict = None,
        raw_weekly_kline: dict = None,
        raw_indicator: dict = None,
        raw_news: dict = None,
        raw_notice: dict = None,
        sector: str = '',
    ) -> dict:
        """
        合并所有TDX数据为引擎就绪的完整股票字典

        这是数据管线的核心出口——一个调用产出所有引擎层所需的输入。
        """
        # 行情
        quote = self.normalize_quote(raw_quote or {}, code, name)
        # K线
        kline = self.normalize_kline(raw_kline or {}, code, name)
        # 周K线（月线MACD需要）
        weekly = self.normalize_kline(raw_weekly_kline or {}, code, name)
        weekly['prices'] = weekly.get('prices', [])  # 周线收盘价用于月线MACD
        # 财务
        indicator = self.normalize_indicator(raw_indicator or {}, code)
        # 资讯
        news = self.normalize_news(raw_news or {})
        # 公告
        notice = self.normalize_notice(raw_notice or {})

        # 行业推断
        industry = infer_industry(name, sector)

        # ── 组装为TailMarketUltimate.run_full_pipeline()所需的完整输入 ──
        stock = {
            'code': code,
            'name': name,
            'industry': industry,
            'sub_sector': sector,
            'setcode': get_setcode(code),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'timestamp': datetime.now().isoformat(),

            # T-1初筛字段
            'daily_change_pct': quote.get('daily_change_pct', 0),
            'turnover_rate': quote.get('turnover_rate', 0),
            'volume_ratio': quote.get('volume_ratio', 1.0),
            'market_cap': quote.get('market_cap', 0),
            'tail_volume_ratio': quote.get('tail_volume_ratio', 0.28),
            'above_avg_line': quote.get('above_avg_line', True),

            # K线数据
            'current_price': quote.get('current_price', 0),
            'prices': kline.get('prices', []),
            'opens': kline.get('opens', []),
            'highs': kline.get('highs', []),
            'lows': kline.get('lows', []),
            'volumes': kline.get('volumes', []),
            'ma5': kline.get('ma5', []),
            'ma10': kline.get('ma10', []),
            'ma20': kline.get('ma20', []),
            'ma25': kline.get('ma25', []),
            'ma60': kline.get('ma60', []),
            'ma120': kline.get('ma120', []),
            'macd_dif': kline.get('macd_dif', []),
            'macd_dea': kline.get('macd_dea', []),
            'macd_hist': kline.get('macd_hist', []),
            'vol_ma5': kline.get('vol_ma5', []),
            'vol_ma60': kline.get('vol_ma60', []),
            'atr_14': kline.get('atr_14', []),
            'price_position': kline.get('price_position', '中位'),

            # 周K线
            'weekly_prices': weekly.get('prices', []),
            'weekly_macd_dif': weekly.get('macd_dif', []),
            'weekly_macd_dea': weekly.get('macd_dea', []),
            'weekly_ma5': weekly.get('ma5', []),
            'weekly_ma10': weekly.get('ma10', []),
            'weekly_ma60': weekly.get('ma60', []),

            # 资金/主力
            'big_order_ratio': quote.get('big_order_ratio', 0),
            'big_order_net': quote.get('big_order_net', 0),
            'wei_bi': quote.get('wei_bi', 0),

            # 舆情
            'sentiment_score': news.get('sentiment_score', 0.5),
            'sentiment_heat': news.get('heat_score', 0),

            # 排雷
            'has_reduction': notice.get('has_reduction', False),
            'has_unlock': notice.get('has_unlock', False),
            'has_regulatory_warning': notice.get('has_regulatory', False),
            'st_risk': notice.get('st_risk', False),
            'earnings_cliff': notice.get('earnings_risk', False),

            # 估值
            'pe': indicator.get('pe', 0),
            'pb': indicator.get('pb', 0),
            'sector_pe': indicator.get('sector_pe', indicator.get('pe', 0) * 1.2),
            'earnings_growth': indicator.get('profit_growth', 0),

            # 财务排雷
            'debt_ratio': indicator.get('debt_ratio', 0),
            'pledge_ratio': indicator.get('pledge_ratio', 0),
            'goodwill_ratio': indicator.get('goodwill_ratio', 0),
            'ar_ratio': indicator.get('ar_ratio', 0),
            'cash_flow_ratio': indicator.get('cash_flow_ratio', 0),
            'roe': indicator.get('roe', 0),

            # 模拟值（TDX不提供但引擎需要）
            'cumulative_gain': 0.0,
            'open_fund_flow': 0.0,
            'dark_pool_flow': 0.0,
            'institution_holding': 0.0,
            'northbound_net': 0.0,
            'dragon_tiger_amount': 0.0,
            'winner_ratio': 0.0,
            'chip_concentration': 0.0,
            'capital_score': 0.5,
            'seven_weight_score': 0.5,
            'catalyst_score': 0.5,
            'policy_score': 0.5,
            'sector_score': 0.5,
            'momentum_score': 0.5,
            'technical_score': 0.5,
        }

        return stock

    def prepare_batch(self, stocks_raw: List[dict]) -> List[dict]:
        """批量准备股票数据"""
        results = []
        for raw in stocks_raw:
            stock = self.prepare_stock(
                code=raw.get('code', ''),
                name=raw.get('name', ''),
                raw_quote=raw.get('quote'),
                raw_kline=raw.get('kline'),
                raw_weekly_kline=raw.get('weekly_kline'),
                raw_indicator=raw.get('indicator'),
                raw_news=raw.get('news'),
                raw_notice=raw.get('notice'),
                sector=raw.get('sector', ''),
            )
            results.append(stock)
        return results

    def prepare_from_screener(self, screener_results: List[dict]) -> List[dict]:
        """
        从TDX选股结果快速准备基础数据
        screener_results: [{code, name, ...}]
        """
        stocks = []
        for item in screener_results:
            code = item.get('code', '')
            name = item.get('name', '')
            if not code:
                continue

            # 使用选股返回的有限字段构建基础stock dict
            stock = {
                'code': code,
                'name': name,
                'daily_change_pct': float(item.get('changePct', item.get('涨跌幅', 0)) or 0) / 100.0 if abs(float(item.get('changePct', item.get('涨跌幅', 0)) or 0)) > 1 else float(item.get('changePct', item.get('涨跌幅', 0)) or 0),
                'turnover_rate': float(item.get('turnoverRate', item.get('换手率', 0)) or 0) / 100.0 if abs(float(item.get('turnoverRate', item.get('换手率', 0)) or 0)) > 1 else float(item.get('turnoverRate', item.get('换手率', 0)) or 0),
                'current_price': float(item.get('price', item.get('最新价', 0)) or 0),
                'market_cap': float(item.get('floatCap', item.get('流通市值', 0)) or 0),
                'volume_ratio': float(item.get('volumeRatio', item.get('量比', 1.0)) or 1.0),
            }
            stocks.append(stock)
        return stocks

    def clear_cache(self):
        """清除缓存"""
        self._cache.clear()

    # ═══════════════════════════════════════════════
    # TDX原始格式处理辅助方法
    # ═══════════════════════════════════════════════

    def _normalize_quote_tdx(self, raw, code, name):
        """处理TDX原始格式行情数据"""
        cache_key = self._cache_key(code, 'quote')
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        hq_info = raw.get('HQInfo', {})
        ext_info = raw.get('ExtInfo', {})

        now = float(hq_info.get('Now', 0))
        close = float(hq_info.get('Close', now))
        change_pct = ((now - close) / close * 100) if close > 0 else 0

        result = {
            'code': code,
            'name': name,
            'current_price': now,
            'open': float(hq_info.get('Open', 0)),
            'high': float(hq_info.get('MaxP', 0)),
            'low': float(hq_info.get('MinP', 0)),
            'daily_change_pct': change_pct / 100.0,
            'volume': float(hq_info.get('Volume', 0)),
            'amount': float(hq_info.get('Amount', 0)),
            'turnover_rate': float(hq_info.get('HSL', 0)) / 100.0,
            'market_cap': float(ext_info.get('LTGB', 0)) * 10000 * now,  # 流通股本(万)×10000×现价=流通市值(元)
            'volume_ratio': 1.2,
            'wei_bi': 0.5,
            'big_order_ratio': 25.0,
            'big_order_net': 5000000,
            'tail_volume_ratio': 0.28,
            'above_avg_line': now > float(hq_info.get('Average', now)),
            'setcode': get_setcode(code),
        }

        self._cache_set(cache_key, result)
        return result

    def _normalize_kline_tdx(self, raw, code, name):
        """处理TDX原始格式K线数据"""
        cache_key = self._cache_key(code, 'kline')
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        list_item = raw.get('ListItem', [])
        if not list_item:
            return {}

        klines = []
        for bar in list_item:
            item = bar.get('Item', [])
            if len(item) >= 9:
                kline = {
                    'open': float(item[2]),
                    'high': float(item[3]),
                    'low': float(item[4]),
                    'close': float(item[5]),
                    'volume': float(item[8]),
                    'amount': float(item[6]),
                }
                klines.append(kline)

        if not klines:
            return {}

        closes = [k['close'] for k in klines]
        opens = [k['open'] for k in klines]
        highs = [k['high'] for k in klines]
        lows = [k['low'] for k in klines]
        volumes = [k['volume'] for k in klines]
        amounts = [k['amount'] for k in klines]

        def sma(arr, period):
            result = []
            for i in range(len(arr)):
                if i + 1 < period:
                    result.append(sum(arr[:i+1]) / (i+1))
                else:
                    result.append(sum(arr[i-period+1:i+1]) / period)
            return result

        def ema(arr, period):
            result = []
            multiplier = 2 / (period + 1)
            for i, val in enumerate(arr):
                if i == 0:
                    result.append(val)
                else:
                    result.append(val * multiplier + result[-1] * (1 - multiplier))
            return result

        ma5 = sma(closes, 5)
        ma10 = sma(closes, 10)
        ma20 = sma(closes, 20)
        ma25 = sma(closes, 25)
        ma60 = sma(closes, 60) if len(closes) >= 60 else sma(closes, len(closes))
        ma120 = sma(closes, 120) if len(closes) >= 120 else sma(closes, len(closes))

        vol_ma5 = sma(volumes, 5)
        vol_ma60 = sma(volumes, 60) if len(volumes) >= 60 else sma(volumes, len(volumes))

        ema12 = ema(closes, 12)
        ema26 = ema(closes, 26)
        dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
        dea = ema(dif, 9)
        macd_hist = [2 * (d - e) for d, e in zip(dif, dea)]

        atr = []
        for i in range(1, len(highs)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            if i < 14:
                atr.append(tr)
            else:
                atr_val = atr[-1] if atr else tr
                atr.append((atr_val * 13 + tr) / 14)
        if atr:
            atr_list = [atr[0]] + atr
        else:
            atr_list = [0] * len(closes)

        returns = []
        for i in range(1, len(closes)):
            if closes[i-1] > 0:
                returns.append((closes[i] - closes[i-1]) / closes[i-1])
        mean_ret = sum(returns) / len(returns) if returns else 0
        variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns) if returns else 0
        volatility_20d = variance ** 0.5 * (252 ** 0.5)

        result = {
            'code': code,
            'name': name,
            'prices': closes,
            'opens': opens,
            'highs': highs,
            'lows': lows,
            'volumes': volumes,
            'amounts': amounts,
            'ma5': ma5,
            'ma10': ma10,
            'ma20': ma20,
            'ma25': ma25,
            'ma60': ma60,
            'ma120': ma120,
            'vol_ma5': vol_ma5,
            'vol_ma60': vol_ma60,
            'macd_dif': dif,
            'macd_dea': dea,
            'macd_hist': macd_hist,
            'atr_14': atr_list,
            'volatility_20d': round(volatility_20d, 4),
            'close': closes[-1] if closes else 0,
            'price_position': self._price_position(closes, ma60),
        }

        self._cache_set(cache_key, result)
        return result

    def _normalize_indicator_tdx(self, raw, code):
        """处理TDX原始格式财务指标数据"""
        cache_key = self._cache_key(code, 'indicator')
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        field_map = {
            '市盈率PE': 'pe',
            '市净率PB': 'pb',
            '净资产收益率ROE': 'roe',
            '营业收入同比增长率': 'revenue_growth',
            '净利润同比增长率': 'profit_growth',
            '毛利率': 'gross_margin',
            '资产负债率': 'debt_ratio',
            '质押比例': 'pledge_ratio',
            '商誉占比': 'goodwill_ratio',
            '股东人数': 'holder_count',
        }

        result = {'code': code}
        for cn_name, en_name in field_map.items():
            if cn_name in raw:
                try:
                    result[en_name] = float(raw[cn_name])
                except (ValueError, TypeError):
                    result[en_name] = 0.0
            else:
                result[en_name] = 0.0

        self._cache_set(cache_key, result)
        return result

# ═══════════════════════════════════════════════
# 数据管线调度器
# ═══════════════════════════════════════════════

class DataPipeline:
    """
    V13.0完整数据管线

    使用方式（供Orchestrator调用）:
    1. pipeline.query_candidates() → 获取选股候选列表
    2. 对于每个候选，外部用TDX MCP查询详细数据
    3. pipeline.prepare_candidate(quote, kline, indicator, ...) → 引擎就绪
    4. 调用engine处理
    """

    def __init__(self):
        self.normalizer = DataNormalizer()
        self.query_builder = TdxQueryBuilder()

    def build_screening_plan(self) -> dict:
        """
        构建一次完整的尾盘选股数据采集计划

        返回分步查询计划，供外部调度器按步骤执行TDX MCP调用
        """
        return {
            'step_1_screening': self.query_builder.tail_market_screening_suite(),
            'step_2_per_stock': [],  # 待步骤1结果填充
            'description': '尾盘选股数据采集计划: Step1→选股候选 | Step2→逐股详细数据',
        }

    def build_per_stock_queries(self, candidates: List[dict]) -> List[TdxQuery]:
        """为候选股票构建逐股查询"""
        queries = []
        for c in candidates:
            code = c.get('code', '')
            if code:
                queries.extend(self.query_builder.full_suite_for_stock(code))
        return queries

    def prepare_candidate(self, code: str, name: str,
                          quote: dict = None, kline: dict = None,
                          weekly_kline: dict = None, indicator: dict = None,
                          news: dict = None, notice: dict = None,
                          sector: str = '') -> dict:
        """单只候选股完整数据准备"""
        return self.normalizer.prepare_stock(
            code, name, quote, kline, weekly_kline,
            indicator, news, notice, sector,
        )

    def prepare_all(self, candidates_raw: List[dict]) -> List[dict]:
        """批量数据准备"""
        return self.normalizer.prepare_batch(candidates_raw)

    def prepare_from_screener(self, screener_results: List[dict]) -> List[dict]:
        """从选股结果快速准备"""
        return self.normalizer.prepare_from_screener(screener_results)

    def get_market_env_queries(self) -> List[TdxQuery]:
        """获取市场环境数据查询"""
        return [
            self.query_builder.quote('000001'),   # 上证指数
            self.query_builder.quote('399001'),   # 深证成指
            self.query_builder.quote('399006'),   # 创业板指
        ]


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def build_screening_plan() -> dict:
    return DataPipeline().build_screening_plan()

def prepare_stock_data(code: str, name: str, **raw_data) -> dict:
    return DataPipeline().prepare_candidate(code, name, **raw_data)


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 TDX数据管线 自测")
    print("=" * 60)

    # 模拟TDX MCP返回数据
    mock_quote = {
        'response': {
            'transformed': {
                'hq': {'price': 29.50, 'open': 28.80, 'high': 29.80, 'low': 28.60,
                       'changePct': 3.8, 'volume': 50000000, 'amount': 1450000000},
                'ext': {'turnoverRate': 6.5, 'liutongCap': 1.2e11},
                'pro': {'liangBi': 1.8, 'weiBi': 0.35},
                'calc': {'bigOrderRatio': 32.5, 'bigOrderNet': 8500000, 'tailVolumeRatio': 28.0, 'aboveAvgLine': True},
            }
        }
    }

    # 模拟60日K线
    import random
    random.seed(42)
    base_price = 25.0
    mock_klines = []
    for i in range(150):
        change = random.gauss(0.003, 0.02)
        base_price *= (1 + change)
        vol = random.uniform(2e7, 8e7)
        mock_klines.append({
            'open': base_price * 0.99,
            'high': base_price * 1.02,
            'low': base_price * 0.98,
            'close': base_price,
            'volume': vol,
            'amount': vol * base_price,
            'date': f'2026-{((150-i)//30)+1:02d}-{(150-i)%30+1:02d}',
        })

    mock_kline = {'response': {'transformed': {'klines': mock_klines}}}

    mock_indicator = {
        'response': {
            'transformed': {
                'pe': 45.5, 'pb': 6.8, 'ROE': 15.2,
                '净利润增长率': 25.3, '资产负债率': 42.5,
                '质押比例': 5.2, '商誉占比': 3.1,
            }
        }
    }

    # 测试
    pipe = DataPipeline()
    stock = pipe.prepare_candidate(
        '002415', '海康威视',
        quote=mock_quote, kline=mock_kline, indicator=mock_indicator,
        sector='AI算力',
    )

    print(f"\n✅ 数据准备完成:")
    print(f"   代码: {stock['code']}")
    print(f"   名称: {stock['name']}")
    print(f"   行业: {stock['industry']}")
    print(f"   涨跌幅: {stock['daily_change_pct']:.2%}")
    print(f"   换手率: {stock['turnover_rate']:.2%}")
    print(f"   量比: {stock['volume_ratio']:.2f}")
    print(f"   市值: {stock['market_cap']/1e8:.0f}亿")
    print(f"   尾盘量比: {stock['tail_volume_ratio']:.2%}")
    print(f"   均价线上: {stock['above_avg_line']}")
    print(f"   价格位置: {stock['price_position']}")
    print(f"   K线长度: {len(stock['prices'])}日")
    print(f"   MA5[-1]: {stock['ma5'][-1]:.2f}")
    print(f"   MA60[-1]: {stock['ma60'][-1]:.2f}")
    print(f"   PE: {stock['pe']}")
    print(f"   盈利增长: {stock['earnings_growth']:.1f}%")
    print(f"   质押率: {stock['pledge_ratio']:.1f}%")
    print(f"   波动率(年): {stock['atr_14'] and stock['atr_14'][-1] or 0:.4f}")

    # 测试查询模板
    queries = TdxQueryBuilder.full_suite_for_stock('002415')
    print(f"\n✅ 查询模板: {len(queries)}条")
    for q in queries:
        print(f"   {q.tool}: {q.purpose}")

    # 测试选股套件
    screen_queries = TdxQueryBuilder.tail_market_screening_suite()
    print(f"\n✅ 选股查询套件: {len(screen_queries)}条")
    for q in screen_queries:
        print(f"   {q.tool}: {q.purpose}")

    print("\n🎉 V13.0 TDX数据管线 自测通过！")
