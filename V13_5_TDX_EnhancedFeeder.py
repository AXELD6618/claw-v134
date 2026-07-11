# -*- coding: utf-8 -*-
"""
V13.5.18 TDX增强数据馈送模块 — 4大TDX工具M71维度集成
================================================================
作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.18
日期: 2026-07-03

集成4大TDX MCP工具到M71 34维度系统:
  1. tdx_ai_listening  → D18/D19 舆情热度+趋势 (AI聚合24h资讯+多空权重)
  2. wenda_notice_query → D28 催化强度 (公告事件驱动: 重组/回购/定增/业绩预告)
  3. tdx_indicator_select → 基本面增强 (PE/PB/ROE/主营构成 → D24/D21)
  4. wenda_macro_query → 宏观环境 (CPI/PMI/M2 → 市场状态判断)

TDX MCP 14工具完整映射:
  Tier1 实时: tdx_quotes / tdx_kline / tdx_screener / tdx_lookup_stock
  Tier2 增强: tdx_api_data(70+路由) / tdx_indicator_select(NLP指标)
  Tier3 研究: wenda_news_query / wenda_notice_query / wenda_report_query
  Tier4 AI:   tdx_ai_listening(AI聚合) / wenda_macro_query(宏观)
  Tier5 衍生: tdx_futures_deep_info / tdx_futures_quotes / tdx_option_t_quote

TdxClaw说明:
  TdxClaw是通达信2026年4月推出的AI投研龙虾桌面应用(基于OpenClaw内核),
  内置40+专业Skills + 通达信自研金融大模型(GLM-4.7) + 词元积分服务。
  本模块通过TDX MCP Connector(14工具)获取通达信专业金融数据,
  在自动化执行时由AI Agent调用MCP工具→结果存入JSON→Python脚本消费。
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════════════════════
# SECTION 1: TDX 14工具完整映射
# ═══════════════════════════════════════════════════════════════

TDX_14_TOOLS = {
    # Tier1: 实时行情与K线
    'quotes':       'tdx_quotes',              # 实时行情 OHLCV + 5档盘口 + 外盘/内盘/委比
    'kline':        'tdx_kline',               # 多周期K线 1m/5m/15m/30m/1h/day/week/month
    'screener':     'tdx_screener',            # 自然语言条件选股
    'lookup':       'tdx_lookup_stock',        # 证券代码检索

    # Tier2: 结构化数据
    'api_data':     'tdx_api_data',            # 70+结构化API路由(资金流/龙虎榜/财务/股东)
    'indicator':    'tdx_indicator_select',    # ★NLP结构化指标查询 PE/PB/ROE/主营构成

    # Tier3: 研究资讯
    'news':         'wenda_news_query',        # 新闻/快讯/主题资讯
    'notice':       'wenda_notice_query',      # ★公司公告/定期报告/临时公告
    'report':       'wenda_report_query',      # 券商研报/评级/目标价
    'macro':        'wenda_macro_query',       # ★宏观经济 GDP/CPI/PPI/M2/PMI

    # Tier4: AI聚合
    'ai_listening': 'tdx_ai_listening',        # ★AI智能听 24h资讯聚合+多空分析

    # Tier5: 衍生品
    'futures_deep': 'tdx_futures_deep_info',   # 期货深度资料
    'futures_quote':'tdx_futures_quotes',      # 期货多合约行情
    'option_t':     'tdx_option_t_quote',      # 期权T型报价
}

# tdx_api_data 常用路由 (14个高频路由)
API_ROUTES_14 = {
    'capital_flow':            'zjlx',    # 资金流向(主力/超大单/大单净买) → D5/D31
    'limit_up_analysis':       'ztfx',    # 涨停分析(封单/连板/题材) → D26
    'dragon_tiger':            'jglhb',   # 龙虎榜(机构/游资席位) → D3/D4
    'northbound':              'bszj',    # 北向资金 → D23
    'margin_trading':          'rzrq',    # 融资融券
    'block_trade':             'dzjy',    # 大宗交易
    'inst_holding':            'jgcg',    # 机构持股 → D24
    'top_float_shareholders':  'ltgd',    # 十大流通股东 → D24
    'shareholder_count':       'gdrs',    # 股东人数 → D24
    'income_statement':        'lrb',     # 利润表 → D21
    'balance_sheet':           'zcfzb',   # 资产负债表 → D21
    'cashflow_statement':      'xjllb',   # 现金流量表 → D21
    'business_composition':    'ycfbb',   # 营业成本构成 → D21
    'valuation_history':       'gslsb',   # 估值历史 → D24
}

# setcode映射
SETCODE_MAP = {
    'SH': '1', '60': '1', '68': '1',
    'SZ': '0', '00': '0', '30': '0',
    'BJ': '2', '83': '2', '87': '2', '92': '2', '43': '2',
    'HK': '31',
}


def infer_setcode(code: str) -> str:
    """从股票代码推断setcode"""
    code = str(code).strip()
    if code.startswith(('60', '68', '11', '13')):
        return '1'
    elif code.startswith(('00', '30', '12')):
        return '0'
    elif code.startswith(('83', '87', '88', '92', '43')):
        return '2'
    return '1'


def build_setcode_code(code: str) -> str:
    """构建tdx_ai_listening所需的 setcode_code 格式: '市场代码_品种代码'"""
    setcode = infer_setcode(code)
    return f"{setcode}_{code}"


# ═══════════════════════════════════════════════════════════════
# SECTION 2: tdx_ai_listening → D18/D19 舆情数据解析
# ═══════════════════════════════════════════════════════════════

def parse_ai_listening(ai_response: Dict) -> Dict:
    """
    解析tdx_ai_listening返回的AI聚合资讯 → D18/D19舆情数据格式

    tdx_ai_listening返回:
    - 一句话概要(summary)
    - 关键事件列表(key_events)
    - 多空权重(bull_bear_weight)
    - 资讯热度(heat_score)

    转换为M71 D18/D19所需格式:
    - d18_score: 舆情热度评分(0-10)
    - d19_trend: 舆情趋势('improving'/'stable'/'deteriorating')
    - d18_summary: AI摘要文本
    - d18_events: 关键事件列表
    - d18_bull_bear: 多空权重
    """
    if not ai_response:
        return _default_sentiment()

    result = _default_sentiment()

    try:
        # 解析AI聚合摘要
        summary = ai_response.get('summary', '') or ai_response.get('概要', '')
        key_events = ai_response.get('key_events', []) or ai_response.get('关键事件', [])
        bull_bear = ai_response.get('bull_bear_weight', {}) or ai_response.get('多空权重', {})

        # 多空权重 → 舆情热度评分
        bull_score = 0
        bear_score = 0
        if isinstance(bull_bear, dict):
            bull_score = bull_bear.get('bull', 0) or bull_bear.get('多', 0)
            bear_score = bull_bear.get('bear', 0) or bull_bear.get('空', 0)
        elif isinstance(bull_bear, (int, float)):
            bull_score = float(bull_bear)
            bear_score = 100 - bull_score

        # D18 舆情热度: 基于事件数量+多空分歧度
        event_count = len(key_events) if key_events else 0
        if event_count >= 5:
            heat_base = 8.0  # 高度关注
        elif event_count >= 3:
            heat_base = 6.0  # 关注度高
        elif event_count >= 1:
            heat_base = 4.0  # 中性关注
        else:
            heat_base = 2.0  # 无人关注

        # 多空分歧度加成: 分歧越大→关注度越高
        total = bull_score + bear_score
        if total > 0:
            divergence = abs(bull_score - bear_score) / total
            if divergence < 0.2:  # 多空接近 → 高度关注
                heat_base += 1.5
            elif divergence < 0.4:
                heat_base += 0.5

        d18_score = min(10.0, heat_base)

        # D19 舆情趋势: 基于多空权重
        if bull_score > bear_score * 1.3:
            d19_trend = 'improving'  # 多方占优 → 舆情升温
        elif bear_score > bull_score * 1.3:
            d19_trend = 'deteriorating'  # 空方占优 → 舆情降温
        else:
            d19_trend = 'stable'  # 多空均衡 → 平稳

        # 关键事件中的利好/利空关键词检测
        bull_keywords = ['涨停', '利好', '增长', '突破', '超预期', '回购', '增持', '量产', '订单', '中标']
        bear_keywords = ['跌停', '利空', '下降', '亏损', '减持', '违规', '警告', '退市', '爆雷', '问询']

        bull_count = 0
        bear_count = 0
        for event in key_events:
            event_text = str(event) if not isinstance(event, dict) else json.dumps(event, ensure_ascii=False)
            for kw in bull_keywords:
                if kw in event_text:
                    bull_count += 1
            for kw in bear_keywords:
                if kw in event_text:
                    bear_count += 1

        # 利好事件占比 → 调整趋势
        total_kw = bull_count + bear_count
        if total_kw > 0:
            bull_ratio = bull_count / total_kw
            if bull_ratio > 0.6:
                d19_trend = 'improving'
                d18_score = min(10.0, d18_score + 0.5)
            elif bull_ratio < 0.3:
                d19_trend = 'deteriorating'
                d18_score = max(0, d18_score - 0.5)

        result = {
            'd18_score': d18_score,
            'd19_trend': d19_trend,
            'd18_summary': summary,
            'd18_events': key_events,
            'd18_bull_bear': {'bull': bull_score, 'bear': bear_score},
            'd18_event_count': event_count,
            'd18_bull_keywords': bull_count,
            'd18_bear_keywords': bear_count,
            'data_source': 'tdx_ai_listening',
        }

    except Exception as e:
        result['parse_error'] = str(e)

    return result


def _default_sentiment() -> Dict:
    return {
        'd18_score': 5.0,
        'd19_trend': 'stable',
        'd18_summary': '',
        'd18_events': [],
        'd18_bull_bear': {'bull': 50, 'bear': 50},
        'data_source': 'default',
    }


# ═══════════════════════════════════════════════════════════════
# SECTION 2.5: tdx_api_data(资金流向) → D29 洗盘识别数据解析
# ═══════════════════════════════════════════════════════════════

def parse_capital_flow_to_history(api_data_response: Dict) -> List[Dict]:
    """
    解析tdx_api_data返回的主力资金流向数据 → D29洗盘识别所需格式

    tdx_api_data调用方式:
      tdx_api_data(entry="TdxSharePCCW.tdxf10_gg_jyds", fixedTag="zjlx", code="300540")

    返回数据格式:
      {
        "tables": [{
          "name": "capital_flow",
          "rows": [
            {"日期": "2026-06-24", "主力净额金额(元)": 1692792, "收盘价": 26.19},
            ...
          ]
        }]
      }

    转换为M71 D29所需的capital_flow_history格式:
      [
        {'date': '2026-06-24', 'main_net': 169.0, 'close': 26.19},  # main_net单位: 万元
        ...
      ]

    关键验证（蜀道装备6/24模式）:
      - 6/24: 暴跌-8.97% + 主力净额+169万 = 洗盘铁证
      - D29洗盘日主力微正+2分: drop_today>=5.0 and capital_flow_history[-1]['main_net'] > 0
    """
    result = []

    if not api_data_response or not api_data_response.get('ok'):
        return result

    try:
        response_data = api_data_response.get('response', {})
        transformed = response_data.get('transformed', {})
        tables = transformed.get('tables', [])

        if not tables:
            return result

        # 查找capital_flow表
        capital_flow_table = None
        for table in tables:
            if table.get('name') == 'capital_flow':
                capital_flow_table = table
                break

        if not capital_flow_table:
            return result

        # 解析每一行
        rows = capital_flow_table.get('rows', [])
        for row in rows:
            date_str = row.get('日期', '')
            main_net_yuan = row.get('主力净额金额(元)', 0)  # 单位: 元
            close = row.get('收盘价', 0.0)

            if not date_str:
                continue

            # 转换主力净额: 元 → 万元 (M71 D29使用万元单位)
            main_net_wan = main_net_yuan / 10000.0

            result.append({
                'date': date_str,
                'main_net': main_net_wan,  # 万元
                'close': close,
            })

        # 按日期升序排列（最早的在前面）
        result.sort(key=lambda x: x['date'])

    except Exception as e:
        print(f"[parse_capital_flow_to_history] 解析错误: {e}")

    return result


def get_capital_flow_for_d29(stock_code: str, tdx_api_data_func=None) -> List[Dict]:
    """
    获取指定股票的历史主力资金流向数据 → D29洗盘识别格式

    参数:
      stock_code: 股票代码（如 "300540"）
      tdx_api_data_func: 调用tdx_api_data的工具函数（由集成层注入）

    返回:
      capital_flow_history: List[Dict] 格式，按日期升序
      - 最近N天的数据（默认25天，与tdx_api_data返回行数一致）

    使用示例:
      history = get_capital_flow_for_d29("300540", tdx_api_data)
      # history = [
      #   {'date': '2026-06-05', 'main_net': -77.6, 'close': 26.62},
      #   {'date': '2026-06-24', 'main_net': 169.0, 'close': 26.19},  # 蜀道模式
      #   ...
      # ]
    """
    if not tdx_api_data_func:
        print("[get_capital_flow_for_d29] 错误: 未提供tdx_api_data_func")
        return []

    try:
        # 调用tdx_api_data获取资金流向
        response = tdx_api_data_func(
            entry="TdxSharePCCW.tdxf10_gg_jyds",
            fixedTag="zjlx",
            code=stock_code
        )

        # 解析为D29所需格式
        history = parse_capital_flow_to_history(response)
        return history

    except Exception as e:
        print(f"[get_capital_flow_for_d29] 获取失败: {e}")
        return []



# ═══════════════════════════════════════════════════════════════
# SECTION 3: wenda_notice_query → D28 催化数据解析
# ═══════════════════════════════════════════════════════════════

# 催化事件关键词映射
CATALYST_KEYWORD_MAP = {
    # 产业催化 (8分) — 最高优先级
    'industry': {
        'keywords': ['量产', '投产', '下线', '首发', '突破', '国产替代', '自主可控', '首条产线', '交付', '订单'],
        'base_score': 8.0,
        'type_name': '产业催化',
    },
    # 政策催化 (6分)
    'policy': {
        'keywords': ['政策', '规划', '补贴', '税收优惠', '专项债', '发改委', '工信部', '国务院', '证监会', '央行'],
        'base_score': 6.0,
        'type_name': '政策催化',
    },
    # 事件催化 (5分)
    'event': {
        'keywords': ['重组', '并购', '借壳', '合并', '分拆', '战略合作', '协议', '框架'],
        'base_score': 5.0,
        'type_name': '事件催化',
    },
    # 涨价催化 (4分)
    'price': {
        'keywords': ['涨价', '提价', '上调', '议价', '供需偏紧', '缺货', '断供'],
        'base_score': 4.0,
        'type_name': '涨价催化',
    },
    # 回购/增持 (3分) — 正面但非产业级
    'buyback': {
        'keywords': ['回购', '增持', '注销', '股权激励', '员工持股'],
        'base_score': 3.0,
        'type_name': '回购增持',
    },
    # 业绩催化 (4分)
    'earnings': {
        'keywords': ['业绩预告', '业绩快报', '预增', '扭亏', '超预期', '大幅增长'],
        'base_score': 4.0,
        'type_name': '业绩催化',
    },
}


def parse_notice_to_catalyst(notice_response: Dict, stock_name: str = '') -> Dict:
    """
    解析wenda_notice_query返回的公告数据 → D28催化数据格式

    wenda_notice_query返回: 公告列表(标题/日期/类型/内容摘要)

    转换为M71 D28所需格式:
    - type: 催化类型(industry/policy/event/price/buyback/earnings)
    - name: 催化剂名称
    - continuity_days: 催化持续天数
    - daily_limitup_count: 相关涨停股数量(默认0)
    - relevance: 标的匹配度(direct/indirect/none)
    - declining: 催化是否衰减
    - source: 'wenda_notice_query'
    """
    if not notice_response:
        return _default_catalyst()

    result = _default_catalyst()

    try:
        # 解析公告列表
        notices = notice_response if isinstance(notice_response, list) else notice_response.get('notices', notice_response.get('data', []))

        if not notices:
            return result

        best_type = 'concept'
        best_score = 3.0
        best_name = ''
        best_keywords_matched = []

        # 遍历每条公告, 检测催化关键词
        for notice in notices[:20]:  # 最多检查20条
            title = notice.get('title', '') if isinstance(notice, dict) else str(notice)
            content = notice.get('content', '') if isinstance(notice, dict) else ''
            full_text = f"{title} {content}"

            for cat_type, cat_info in CATALYST_KEYWORD_MAP.items():
                matched = [kw for kw in cat_info['keywords'] if kw in full_text]
                if matched:
                    if cat_info['base_score'] > best_score:
                        best_score = cat_info['base_score']
                        best_type = cat_type
                        best_name = title[:50]
                        best_keywords_matched = matched

        # 计算催化持续性: 同类公告在7天内出现多次
        today = datetime.now()
        continuity = 0
        for notice in notices:
            notice_date_str = notice.get('date', '') if isinstance(notice, dict) else ''
            if notice_date_str:
                try:
                    notice_date = datetime.strptime(notice_date_str[:10], '%Y-%m-%d')
                    if (today - notice_date).days <= 7:
                        continuity += 1
                except:
                    pass

        # 标的匹配度: 公告中是否包含股票名称
        relevance = 'none'
        if stock_name and stock_name in json.dumps(notices, ensure_ascii=False):
            relevance = 'direct'

        result = {
            'type': best_type,
            'name': best_name or f'{stock_name}公告催化',
            'continuity_days': min(continuity, 7),
            'daily_limitup_count': 0,  # 需配合tdx_screener获取
            'relevance': relevance,
            'declining': False,
            'source': 'wenda_notice_query',
            'keywords_matched': best_keywords_matched,
            'notice_count': len(notices),
        }

    except Exception as e:
        result['parse_error'] = str(e)

    return result


def _default_catalyst() -> Dict:
    return {
        'type': 'unknown',
        'name': '',
        'continuity_days': 0,
        'daily_limitup_count': 0,
        'relevance': 'none',
        'declining': False,
        'source': 'default',
    }


# ═══════════════════════════════════════════════════════════════
# SECTION 4: tdx_indicator_select → 基本面数据解析
# ═══════════════════════════════════════════════════════════════

def parse_indicator_to_fundamental(indicator_response: Dict) -> Dict:
    """
    解析tdx_indicator_select返回的结构化指标 → 基本面数据格式

    tdx_indicator_select返回: PE/PB/ROE/营收/净利润/主营构成等

    转换为M71基本面增强数据:
    - pe: 市盈率TTM
    - pb: 市净率
    - roe: 净资产收益率
    - gross_margin: 毛利率
    - revenue_growth: 营收增长率
    - profit_growth: 净利润增长率
    - debt_ratio: 资产负债率
    - main_business: 主营构成
    - valuation_level: 估值水平('undervalued'/'fair'/'overvalued'/'expensive')
    - quality_score: 基本面质量评分(0-10)
    """
    result = _default_fundamental()

    if not indicator_response:
        return result

    try:
        # tdx_indicator_select返回可能是字典或列表
        data = indicator_response
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except:
                pass

        # 尝试从多种格式中提取指标
        if isinstance(data, dict):
            # 直接字段
            pe = _extract_numeric(data, ['pe', 'PE', '市盈率', 'pe_ttm', 'PE(TTM)'])
            pb = _extract_numeric(data, ['pb', 'PB', '市净率'])
            roe = _extract_numeric(data, ['roe', 'ROE', '净资产收益率'])
            gross_margin = _extract_numeric(data, ['gross_margin', '毛利率'])
            revenue = _extract_numeric(data, ['revenue', '营收', '营业收入'])
            profit = _extract_numeric(data, ['profit', '净利润', '归母净利润'])
            revenue_growth = _extract_numeric(data, ['revenue_growth', '营收增长率', '营收同比'])
            profit_growth = _extract_numeric(data, ['profit_growth', '净利润增长率', '净利润同比'])
            debt_ratio = _extract_numeric(data, ['debt_ratio', '资产负债率'])

            # 主营构成
            main_business = data.get('main_business', data.get('主营构成', ''))
            if not main_business and isinstance(data.get('data'), list):
                for item in data['data']:
                    if isinstance(item, dict):
                        name = item.get('name', item.get('指标', ''))
                        val = item.get('value', item.get('值', ''))
                        if '主营' in str(name) or '业务' in str(name):
                            main_business = str(val)
                            break

        elif isinstance(data, list):
            # 列表格式: [{name: 'PE', value: 25.3}, ...]
            pe = pb = roe = gross_margin = revenue_growth = profit_growth = debt_ratio = None
            main_business = ''
            for item in data:
                if not isinstance(item, dict):
                    continue
                name = str(item.get('name', item.get('指标', '')))
                val = item.get('value', item.get('值', None))
                if 'PE' in name.upper() or '市盈率' in name:
                    pe = _safe_float(val)
                elif 'PB' in name.upper() or '市净率' in name:
                    pb = _safe_float(val)
                elif 'ROE' in name.upper() or '净资产收益率' in name:
                    roe = _safe_float(val)
                elif '毛利率' in name:
                    gross_margin = _safe_float(val)
                elif '营收' in name and ('增长' in name or '同比' in name):
                    revenue_growth = _safe_float(val)
                elif '净利润' in name and ('增长' in name or '同比' in name):
                    profit_growth = _safe_float(val)
                elif '资产负债率' in name:
                    debt_ratio = _safe_float(val)
                elif '主营' in name or '业务构成' in name:
                    main_business = str(val)

        # 估值水平判定
        valuation_level = 'fair'
        if pe is not None:
            if pe < 15:
                valuation_level = 'undervalued'
            elif pe < 30:
                valuation_level = 'fair'
            elif pe < 60:
                valuation_level = 'overvalued'
            else:
                valuation_level = 'expensive'

        # 基本面质量评分 (0-10)
        quality_score = 5.0  # 默认中性
        if roe is not None:
            if roe > 20:
                quality_score += 2.0
            elif roe > 15:
                quality_score += 1.5
            elif roe > 10:
                quality_score += 1.0
            elif roe < 5:
                quality_score -= 1.0
        if revenue_growth is not None:
            if revenue_growth > 30:
                quality_score += 1.5
            elif revenue_growth > 15:
                quality_score += 1.0
            elif revenue_growth < 0:
                quality_score -= 1.0
        if gross_margin is not None:
            if gross_margin > 40:
                quality_score += 1.0
            elif gross_margin < 20:
                quality_score -= 0.5
        if debt_ratio is not None:
            if debt_ratio > 70:
                quality_score -= 1.0
            elif debt_ratio < 30:
                quality_score += 0.5

        quality_score = max(0, min(10, quality_score))

        result = {
            'pe': pe,
            'pb': pb,
            'roe': roe,
            'gross_margin': gross_margin,
            'revenue_growth': revenue_growth,
            'profit_growth': profit_growth,
            'debt_ratio': debt_ratio,
            'main_business': main_business,
            'valuation_level': valuation_level,
            'quality_score': quality_score,
            'data_source': 'tdx_indicator_select',
        }

    except Exception as e:
        result['parse_error'] = str(e)

    return result


def _default_fundamental() -> Dict:
    return {
        'pe': None,
        'pb': None,
        'roe': None,
        'gross_margin': None,
        'revenue_growth': None,
        'profit_growth': None,
        'debt_ratio': None,
        'main_business': '',
        'valuation_level': 'fair',
        'quality_score': 5.0,
        'data_source': 'default',
    }


def _extract_numeric(data: Dict, keys: List[str]) -> Optional[float]:
    """从字典中尝试多个key提取数值"""
    for key in keys:
        val = data.get(key)
        if val is not None:
            return _safe_float(val)
    return None


def _safe_float(val) -> Optional[float]:
    """安全转换为float"""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            val = val.replace('%', '').replace(',', '').replace('亿', '').strip()
        return float(val)
    except:
        return None


# ═══════════════════════════════════════════════════════════════
# SECTION 5: wenda_macro_query → 宏观环境数据解析
# ═══════════════════════════════════════════════════════════════

def parse_macro_to_market_state(macro_response: Dict) -> Dict:
    """
    解析wenda_macro_query返回的宏观数据 → 市场状态判断

    检测指标: CPI/PPI/PMI/M2/社融/利率
    转换为:
    - macro_environment: 'expansion'/'neutral'/'contraction'
    - liquidity: 'loose'/'neutral'/'tight'
    - inflation: 'high'/'moderate'/'low'
    - market_risk_level: 'low'/'medium'/'high'
    """
    result = {
        'macro_environment': 'neutral',
        'liquidity': 'neutral',
        'inflation': 'moderate',
        'market_risk_level': 'medium',
        'data_source': 'default',
    }

    if not macro_response:
        return result

    try:
        data = macro_response
        if isinstance(data, str):
            data = json.loads(data)

        # 尝试提取关键宏观指标
        cpi = _extract_macro_value(data, ['CPI', '居民消费价格'])
        pmi = _extract_macro_value(data, ['PMI', '采购经理指数'])
        m2 = _extract_macro_value(data, ['M2', '广义货币'])

        # PMI判断经济环境
        if pmi is not None:
            if pmi > 51:
                result['macro_environment'] = 'expansion'
            elif pmi < 49:
                result['macro_environment'] = 'contraction'

        # CPI判断通胀
        if cpi is not None:
            if cpi > 3:
                result['inflation'] = 'high'
                result['market_risk_level'] = 'high'
            elif cpi < 1:
                result['inflation'] = 'low'

        # M2判断流动性
        if m2 is not None:
            if m2 > 10:
                result['liquidity'] = 'loose'
            elif m2 < 7:
                result['liquidity'] = 'tight'
                result['market_risk_level'] = 'high'

        result['data_source'] = 'wenda_macro_query'
        result['cpi'] = cpi
        result['pmi'] = pmi
        result['m2'] = m2

    except Exception as e:
        result['parse_error'] = str(e)

    return result


def _extract_macro_value(data: Any, keywords: List[str]) -> Optional[float]:
    """从宏观数据中提取指标值"""
    if isinstance(data, dict):
        for kw in keywords:
            for key, val in data.items():
                if kw in str(key):
                    return _safe_float(val)
        # 检查data内的列表
        for key, val in data.items():
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, dict):
                        for kw in keywords:
                            for k, v in item.items():
                                if kw in str(k):
                                    return _safe_float(v)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for kw in keywords:
                    for k, v in item.items():
                        if kw in str(k):
                            return _safe_float(v)
    return None


# ═══════════════════════════════════════════════════════════════
# SECTION 6: TDX数据采集指令生成器 (供自动化Agent使用)
# ═══════════════════════════════════════════════════════════════

def generate_tdx_collection_instructions(codes: List[str], date_str: str = None) -> str:
    """
    生成TDX数据采集指令清单 (供自动化Agent在执行时调用MCP工具)

    参数:
        codes: 股票代码列表
        date_str: 查询日期 (YYYY-MM-DD), 默认今天
    """
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')

    instructions = []
    instructions.append(f"# TDX 14工具数据采集指令 (日期: {date_str})")
    instructions.append(f"# 候选股票: {', '.join(codes[:20])}{'...' if len(codes) > 20 else ''}")
    instructions.append("")

    for i, code in enumerate(codes[:20]):
        setcode_code = build_setcode_code(code)
        instructions.append(f"## 股票 {i+1}: {code}")
        instructions.append(f"  # Tier1: 实时行情+K线")
        instructions.append(f"  tdx_quotes(codes=['{code}'])")
        instructions.append(f"  tdx_kline(code='{code}', period='day', count=60)")
        instructions.append(f"  # Tier2: 结构化数据")
        instructions.append(f"  tdx_api_data(entry='TdxSharePCCW.tdxf10_gg_jyds', code='{code}', fixedTag='zjlx')  # 资金流向")
        instructions.append(f"  tdx_indicator_select(message='{code} 市盈率,市净率,ROE')  # 基本面指标")
        instructions.append(f"  # Tier3: 研究资讯")
        instructions.append(f"  wenda_notice_query(symbol='{code}', bdate='{date_str.replace('-','')[:8]}', edate='{date_str.replace('-','')[:8]}')  # 公告催化")
        instructions.append(f"  wenda_news_query(keyword='{code}')  # 新闻资讯")
        instructions.append(f"  wenda_report_query(code='{code}', date_range='last_30_days')  # 研报")
        instructions.append(f"  # Tier4: AI聚合")
        instructions.append(f"  tdx_ai_listening(setcode_code='{setcode_code}', date='{date_str}')  # AI智能听")
        instructions.append("")

    # 宏观数据
    instructions.append("## 宏观数据")
    yyyymmdd = date_str.replace('-', '')
    instructions.append(f"  wenda_macro_query(query='中国|{yyyymmdd[:6]}01|{yyyymmdd}||CPI,PMI,M2')  # 宏观环境")
    instructions.append("")

    # 期货(跨市场信号)
    instructions.append("## 期货跨市场 (可选)")
    instructions.append(f"  tdx_futures_quotes(codes=['CU', 'AU', 'RB'])  # 铜/金/螺纹钢")
    instructions.append("")

    instructions.append("# 采集完成后, 将结果保存到 data/fullmarket_cache/tdx_enhanced_YYYYMMDD.json")
    instructions.append("# M71将通过V13_5_TDX_EnhancedFeeder.py解析这些数据")

    return '\n'.join(instructions)


# ═══════════════════════════════════════════════════════════════
# SECTION 7: 完整M71数据组装器
# ═══════════════════════════════════════════════════════════════

def assemble_m71_enhanced_data(
    ai_listening_data: Dict = None,
    notice_data: Dict = None,
    indicator_data: Dict = None,
    macro_data: Dict = None,
    quotes_data: Dict = None,
    kline_data: Dict = None,
    capital_flow_data: Dict = None,
    capital_flow_history: List[Dict] = None,
) -> Dict:
    """
    将所有TDX工具返回的数据组装为M71 predict()所需的完整参数集

    返回:
    {
        'sentiment_data': {...},        # → D18/D19
        'catalyst_data': {...},         # → D28
        'quote_data': {...},            # → D15/D33
        'capital_flow': CapitalFlow(),  # → D5/D31
        'capital_flow_history': [...],  # → D29/D31
        'market_state': {...},          # → SECTOR_CRASH
        'fundamental_data': {...},      # → D21/D24增强
        'intraday_data': {...},         # → D30
    }
    """
    # D18/D19 舆情数据 (从tdx_ai_listening解析)
    sentiment_data = parse_ai_listening(ai_listening_data) if ai_listening_data else None

    # D28 催化数据 (从wenda_notice_query解析)
    catalyst_data = parse_notice_to_catalyst(notice_data) if notice_data else None

    # 基本面数据 (从tdx_indicator_select解析)
    fundamental_data = parse_indicator_to_fundamental(indicator_data) if indicator_data else None

    # 宏观环境 (从wenda_macro_query解析)
    market_state = parse_macro_to_market_state(macro_data) if macro_data else None

    # 行情数据 (从tdx_quotes解析) — 已有解析逻辑, 这里透传
    quote_data = quotes_data if quotes_data else None

    # 资金流向历史 — 透传
    cf_history = capital_flow_history if capital_flow_history else None

    return {
        'sentiment_data': sentiment_data,
        'catalyst_data': catalyst_data,
        'fundamental_data': fundamental_data,
        'market_state': market_state,
        'quote_data': quote_data,
        'capital_flow_history': cf_history,
        # kline_data和capital_flow_data需要通过现有解析器处理
        'raw_kline': kline_data,
        'raw_capital_flow': capital_flow_data,
    }


# ═══════════════════════════════════════════════════════════════
# SECTION 8: TdxClaw产品说明
# ═══════════════════════════════════════════════════════════════

TDXCLAW_INFO = """
TdxClaw (通达信智能投研龙虾) 产品说明:
=========================================
发布时间: 2026年4月10日
官网: https://www.tdx.com.cn/tdxclaw/
内核: 基于开源OpenClaw内核

核心能力:
  1. 40+专业Skills (持续增长中, 覆盖投研全流程)
  2. 通达信自研金融大模型 (GLM-4.7) + 10余种主流AI模型
  3. 词元积分服务 (使用通达信大模型时消耗词元)
  4. 直连通达信30年专业金融数据库 (非爬虫)
  5. 7×24小时AI守夜人 (美股/欧盘/早盘监控)
  6. 桌面端应用 (Windows 10+, 一键安装)

与TDX MCP Connector的关系:
  - TDX MCP Connector (14工具) = 数据查询层 (本系统已集成)
  - TdxClaw = AI投研应用层 (独立桌面应用, 非API可调用)
  - tdx_ai_listening = TDX MCP中最接近TdxClaw AI能力的工具

本系统集成方案:
  通过TDX MCP Connector的14个工具获取通达信专业金融数据,
  在自动化执行时由AI Agent调用MCP工具 → 结果存入JSON → Python脚本消费。
  TdxClaw的40+Skills和通达信大模型为独立桌面应用能力,
  当前无法通过API/MCP直接调用, 但tdx_ai_listening提供了AI聚合能力。
"""


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.5.18 TDX增强数据馈送模块 — 4大TDX工具M71集成")
    print("=" * 60)
    print()
    print("TDX MCP 14工具完整映射:")
    for tier, tools in [
        ("Tier1 实时", ['quotes', 'kline', 'screener', 'lookup']),
        ("Tier2 结构化", ['api_data', 'indicator']),
        ("Tier3 研究", ['news', 'notice', 'report', 'macro']),
        ("Tier4 AI", ['ai_listening']),
        ("Tier5 衍生品", ['futures_deep', 'futures_quote', 'option_t']),
    ]:
        print(f"  {tier}:")
        for t in tools:
            print(f"    {t:15s} → {TDX_14_TOOLS[t]}")

    print()
    print("tdx_api_data 14个高频路由:")
    for name, route in API_ROUTES_14.items():
        print(f"  {name:30s} → fixedTag={route}")

    print()
    print("4大新集成工具 → M71维度映射:")
    print("  tdx_ai_listening   → D18舆情热度 + D19舆情趋势 (AI聚合24h资讯+多空权重)")
    print("  wenda_notice_query → D28催化强度 (公告事件: 重组/回购/定增/业绩预告)")
    print("  tdx_indicator_select → D21四阶段 + D24三高筹码 (PE/PB/ROE/主营构成)")
    print("  wenda_macro_query  → 市场状态 (CPI/PMI/M2 → 宏观环境判断)")

    print()
    print(TDXCLAW_INFO)
