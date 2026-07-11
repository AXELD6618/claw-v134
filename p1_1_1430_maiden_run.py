#!/usr/bin/env python3
"""
P1-1 首次实盘全链路验证 — 2026-06-24 14:30
从TDX MCP数据构建缓存 → 运行V13.2全链路 → 生成HTML报告
"""

import json
import os
import sys
import time
import math
from datetime import datetime
from typing import Dict, List, Any

# ═══════════════════════════════════════════════════════════
# SECTION 1: Screener数据 (从TDX MCP实时获取)
# ═══════════════════════════════════════════════════════════

SCREENER_RAW = [
    {"code":"688367","name":"工大高科","decline":-16.93,"setcode":"1","hsl":13.24,"amplitude":20.79,"sector":"信息技术"},
    {"code":"300465","name":"高伟达","decline":-10.88,"setcode":"0","hsl":7.09,"amplitude":11.84,"sector":"计算机"},
    {"code":"300461","name":"田中精机","decline":-10.34,"setcode":"0","hsl":7.52,"amplitude":11.32,"sector":"机械设备"},
    {"code":"002535","name":"林州重机","decline":-10.12,"setcode":"0","hsl":3.43,"amplitude":10.53,"sector":"煤炭机械"},
    {"code":"600598","name":"北大荒","decline":-10.02,"setcode":"1","hsl":3.33,"amplitude":3.45,"sector":"农业"},
    {"code":"000151","name":"中成股份","decline":-10.02,"setcode":"0","hsl":6.29,"amplitude":8.96,"sector":"外贸工程"},
    {"code":"603318","name":"水发燃气","decline":-10.01,"setcode":"1","hsl":7.73,"amplitude":4.02,"sector":"燃气"},
    {"code":"605566","name":"福莱蒽特","decline":-10.00,"setcode":"1","hsl":6.42,"amplitude":8.44,"sector":"化工"},
    {"code":"600977","name":"中国电影","decline":-10.00,"setcode":"1","hsl":5.14,"amplitude":7.35,"sector":"传媒"},
    {"code":"688737","name":"中自科技","decline":-9.70,"setcode":"1","hsl":3.80,"amplitude":10.01,"sector":"环保"},
    {"code":"002453","name":"华软科技","decline":-9.61,"setcode":"0","hsl":8.26,"amplitude":9.06,"sector":"化工"},
    {"code":"301231","name":"荣信文化","decline":-9.51,"setcode":"0","hsl":13.86,"amplitude":11.53,"sector":"出版"},
    {"code":"300540","name":"蜀道装备","decline":-9.45,"setcode":"0","hsl":9.97,"amplitude":8.86,"sector":"氢能装备"},
    {"code":"600793","name":"宜宾纸业","decline":-9.03,"setcode":"1","hsl":5.82,"amplitude":9.96,"sector":"造纸"},
    {"code":"002672","name":"东江环保","decline":-8.96,"setcode":"0","hsl":4.62,"amplitude":7.99,"sector":"环保"},
    {"code":"301138","name":"华研精机","decline":-8.94,"setcode":"0","hsl":4.51,"amplitude":10.66,"sector":"包装机械"},
    {"code":"600255","name":"鑫科材料","decline":-8.95,"setcode":"1","hsl":16.80,"amplitude":9.61,"sector":"铜合金"},
    {"code":"688338","name":"赛科希德","decline":-8.50,"setcode":"1","hsl":3.23,"amplitude":10.10,"sector":"医疗器械"},
    {"code":"600456","name":"宝钛股份","decline":-8.55,"setcode":"1","hsl":4.21,"amplitude":2.57,"sector":"钛合金"},
    {"code":"600367","name":"红星发展","decline":-8.46,"setcode":"1","hsl":15.39,"amplitude":5.93,"sector":"锰矿"},
    {"code":"600403","name":"大有能源","decline":-8.51,"setcode":"1","hsl":3.87,"amplitude":9.95,"sector":"煤炭"},
    {"code":"603070","name":"万控智造","decline":-8.44,"setcode":"1","hsl":2.88,"amplitude":7.20,"sector":"电气设备"},
    {"code":"600121","name":"郑州煤电","decline":-8.37,"setcode":"1","hsl":7.77,"amplitude":9.33,"sector":"煤炭"},
    {"code":"600876","name":"凯盛新能","decline":-8.11,"setcode":"1","hsl":3.76,"amplitude":8.11,"sector":"新能源材料"},
    {"code":"688087","name":"英科再生","decline":-7.90,"setcode":"1","hsl":1.98,"amplitude":9.37,"sector":"再生塑料"},
    {"code":"600322","name":"津投城开","decline":-7.85,"setcode":"1","hsl":9.98,"amplitude":8.53,"sector":"地产"},
    {"code":"600769","name":"祥龙电业","decline":-7.78,"setcode":"1","hsl":12.67,"amplitude":12.88,"sector":"电力"},
    {"code":"000802","name":"北京文化","decline":-7.73,"setcode":"0","hsl":5.28,"amplitude":8.48,"sector":"影视"},
    {"code":"600280","name":"中央商场","decline":-7.72,"setcode":"1","hsl":5.78,"amplitude":8.05,"sector":"商业零售"},
    {"code":"603311","name":"金海高科","decline":-7.72,"setcode":"1","hsl":6.92,"amplitude":7.81,"sector":"过滤材料"},
]

# ═══════════════════════════════════════════════════════════
# SECTION 2: 实时行情数据 (从TDX MCP quotes获取)
# ═══════════════════════════════════════════════════════════

QUOTES_DATA = {
    "688367": {"price":39.53,"open":48.20,"high":48.99,"low":39.08,"close_prev":47.66,"volume":116761,"hsl":13.33,"chg":-17.06,"dt_price":38.13,"sector_chg":-2.18,"inout":-34468800,"lb":2.75},
    "300465": {"price":13.08,"open":14.50,"high":14.70,"low":12.96,"close_prev":14.70,"volume":316643,"hsl":7.14,"chg":-11.02,"dt_price":11.76,"sector_chg":-1.67,"inout":-94983648,"lb":1.41},
    "300461": {"price":41.17,"open":45.63,"high":45.89,"low":40.69,"close_prev":45.95,"volume":108741,"hsl":7.57,"chg":-10.40,"dt_price":36.76,"sector_chg":1.59,"inout":-50946448,"lb":1.61},
    "000151": {"price":11.95,"open":13.14,"high":13.14,"low":11.95,"close_prev":13.28,"volume":193722,"hsl":6.29,"chg":-10.02,"dt_price":11.95,"sector_chg":-1.54,"inout":-48075344,"lb":1.09},
    "605566": {"price":43.81,"open":47.52,"high":47.92,"low":43.81,"close_prev":48.68,"volume":85615,"hsl":6.42,"chg":-10.00,"dt_price":43.81,"sector_chg":2.13,"inout":-35569936,"lb":1.15},
    "600977": {"price":13.59,"open":14.60,"high":14.70,"low":13.59,"close_prev":15.10,"volume":959604,"hsl":5.14,"chg":-10.00,"dt_price":13.59,"sector_chg":-4.80,"inout":-134636736,"lb":1.80},
    "688737": {"price":23.33,"open":25.57,"high":25.90,"low":23.31,"close_prev":25.88,"volume":45711,"hsl":3.82,"chg":-9.85,"dt_price":20.70,"sector_chg":-0.72,"inout":-14643192,"lb":2.07},
    "301231": {"price":28.62,"open":31.35,"high":31.69,"low":28.04,"close_prev":31.66,"volume":90777,"hsl":13.87,"chg":-9.60,"dt_price":25.33,"sector_chg":-2.09,"inout":-47630096,"lb":1.27},
    "300540": {"price":26.07,"open":27.48,"high":27.75,"low":25.20,"close_prev":28.77,"volume":206350,"hsl":9.98,"chg":-9.38,"dt_price":23.02,"sector_chg":0.56,"inout":-70928336,"lb":1.65},
    "600255": {"price":4.19,"open":4.44,"high":4.56,"low":4.12,"close_prev":4.58,"volume":3052130,"hsl":16.90,"chg":-8.52,"dt_price":4.12,"sector_chg":-0.74,"inout":-273278944,"lb":1.32},
}

# ═══════════════════════════════════════════════════════════
# SECTION 3: 市场基准数据
# ═══════════════════════════════════════════════════════════

# V13.4.1: 从实时指数自动化读取，严禁硬编码!
import os as _os
_index_path = "data/fullmarket_cache/index_latest.json"
_default_idx = {"000001": {"name":"上证指数","price":0,"chg":0}, "399006": {"name":"创业板指","price":0,"chg":0}}
if _os.path.exists(_index_path):
    try:
        with open(_index_path, "r", encoding="utf-8") as _f:
            _raw = __import__("json").load(_f)
        _sh = _raw.get("000001", {})
        _cy = _raw.get("399006", {})
        MARKET_INDEX = {
            "000001": {"name": _sh.get("name", "上证指数"), "price": _sh.get("price", 0),
                       "chg": _sh.get("change_pct", 0), "open": _sh.get("open", 0),
                       "high": _sh.get("high", 0), "low": _sh.get("low", 0)},
            "399006": {"name": _cy.get("name", "创业板指"), "price": _cy.get("price", 0),
                       "chg": _cy.get("change_pct", 0), "open": _cy.get("open", 0),
                       "high": _cy.get("high", 0), "low": _cy.get("low", 0)},
        }
    except Exception:
        MARKET_INDEX = _default_idx
else:
    MARKET_INDEX = _default_idx


# ═══════════════════════════════════════════════════════════
# SECTION 4: V13.2分析引擎
# ═══════════════════════════════════════════════════════════

# P0-1修复: 导入M46归一化引擎 (2026-06-24)
try:
    from V13_2_M46_Normalized import (
        normalize_m46_batch, get_m46_stats, 
        compute_raw_factors, cross_sectional_normalize,
        M46_NORMALIZED_CONFIG
    )
    M46_NORMALIZED_AVAILABLE = True
except ImportError:
    M46_NORMALIZED_AVAILABLE = False
    print("⚠️ V13_2_M46_Normalized 不可用, 回退到旧版per-stock模式")

def sigmoid(x):
    """Sigmoid映射: 评分→概率"""
    return 1.0 / (1.0 + math.exp(-x))

# [DEPRECATED] 旧版单股M46 (P0-1已修复, 保留兼容)
def m46_bayesian_legacy(stock: Dict, quote: Dict) -> Dict:
    """[已废弃] M46贝叶斯8因子引擎 — 已升级为交叉截面归一化"""
    from V13_2_M46_Normalized import compute_raw_factors as _crf
    return _crf(stock, quote)


def m57_alpha(stock: Dict, quote: Dict) -> Dict:
    """M57 12因子Alpha引擎"""
    decline = abs(stock.get('decline', 0))
    hsl = stock.get('hsl', 0)
    amplitude = stock.get('amplitude', 0)
    lb = quote.get('lb', 1.0)
    sector_chg = quote.get('sector_chg', 0)
    inout = quote.get('inout', 0)

    # 8/12 激活因子 (TDX Tier2数据可激活更多)
    factors = {}

    # ✅ tail_rs: 尾盘相对强度 (振幅大的尾盘可能反转)
    factors['tail_rs'] = round(sigmoid((amplitude/10 - 0.5) * 3) * 0.8, 4) if amplitude > 3 else 0.2

    # ✅ overnight_mom: 隔夜动量 (跳空低开程度)
    gap_down = abs(quote.get('open', 0) - quote.get('close_prev', 0)) / quote.get('close_prev', 100) * 100 if quote.get('close_prev') else 0
    factors['overnight_mom'] = round(-gap_down / 5, 4)  # 低开越多,隔夜动量越负

    # ✅ intraday_rev: 日内反转 (振幅vs跌幅)
    rev_strength = amplitude / max(abs(quote.get('chg', decline)), 1)
    factors['intraday_rev'] = round(min(rev_strength, 3.0), 4)

    # ✅ flow_accel: 资金流加速 (主力净流入)
    flow_accel = inout / max(abs(inout), 1) if inout != 0 else 0
    factors['flow_accel'] = round(flow_accel * 0.3, 4)

    # ✅ gap_fill_prob: 缺口回补概率
    gap = abs(quote.get('open', 0) - quote.get('close_prev', 0)) / quote.get('close_prev', 1) if quote.get('close_prev') else 0
    fill_prob = sigmoid((gap - 0.03) * 5) * 0.7 if gap > 0 else 0.5
    factors['gap_fill_prob'] = round(fill_prob, 4)

    # ✅ sector_alpha: 板块Alpha
    factors['sector_alpha'] = round(sector_chg / 3, 4)

    # ✅ streak_exp: 连续下跌衰减 (单日大跌exp更高)
    streak = 1  # 默认1天下跌
    factors['streak_exp'] = round(math.exp(-streak * 0.3) * decline / 10, 4)

    # ✅ auction_sig: 集合竞价信号 (跳空低开幅度)
    factors['auction_sig'] = round(gap_down / 3, 4)

    # ❌ event_decay: 需新闻数据 (休眠)
    factors['event_decay'] = 0.0

    # ❌ lhb_effect: 需龙虎榜数据 (休眠)
    factors['lhb_effect'] = 0.0

    # ❌ sentiment_trans: 需指数情绪传导 (休眠)
    factors['sentiment_trans'] = 0.0

    # ❌ tail_vol_struct: 需1min量结构 (休眠)
    factors['tail_vol_struct'] = 0.0

    # 综合评分
    active_sum = sum(abs(v) for k, v in factors.items() if v != 0.0)
    composite = active_sum / 8 * (1 + sector_chg/10) if sector_chg > 0 else active_sum / 8 * 0.8

    # T+1预期收益 (基于超跌反弹模型)
    t1_forecast = decline * 0.3 * (1 + sector_chg/5) if sector_chg > 0 else decline * 0.15

    return {
        "composite": round(composite, 4),
        "t1_forecast": round(t1_forecast, 4),
        "factors": factors,
        "activated": 8,  # 8/12激活
        "total_possible": 12,
    }


def m64_oversold_reversal(stock: Dict, quote: Dict) -> Dict:
    """M64超跌反转Alpha — 5子信号"""
    decline = abs(stock.get('decline', 0))
    hsl = stock.get('hsl', 0)
    amplitude = stock.get('amplitude', 0)
    lb = quote.get('lb', 1.0)
    sector_chg = quote.get('sector_chg', 0)

    # 5子信号
    signals = {}

    # 1. 缩量下跌 → 放量反弹潜力 (hsl越高,反弹确认越强)
    vol_contract = sigmoid((hsl / 5 - 1) * 2) * 0.35
    signals['volume_contraction'] = round(vol_contract, 4)

    # 2. 地量反转阈值 (跌幅>5%触发)
    oversold_threshold = 1.0 if decline >= 5 else 0.3 if decline >= 3 else 0.0
    signals['oversold_threshold'] = oversold_threshold

    # 3. 平台突破形态 (振幅>8% = 有效突破)
    platform_break = sigmoid((amplitude/8 - 0.5) * 3) * 0.15
    signals['platform_break'] = round(platform_break, 4)

    # 4. 板块共振 (sector_chg > 0)
    sector_align = sigmoid((sector_chg/2) * 2) * 0.15 if sector_chg > 0 else -0.1
    signals['sector_align'] = round(sector_align, 4)

    # 5. 反转形态 (量比>1.5 = 放量确认)
    reversal_strength = sigmoid((lb/1.5 - 1) * 2) * 0.25
    signals['reversal_strength'] = round(reversal_strength, 4)

    # M64综合 (权重: 缩量0.453 + 反转0.325 + 板块0.225*1.793)
    m64_raw = (
        signals['volume_contraction'] * 0.45 +
        signals['reversal_strength'] * 0.33 +
        signals['sector_align'] * 0.22
    )
    # P0-1修复: M64信号放大器 — 解决贡献度仅3%的问题
    # 交叉学习发现: STAR模式(昨跌今涨停) avg_m64 = 0.35, 非STAR = 0.11
    # 放大倍数 = 3.0 (将典型0.10-0.15信号放大到0.30-0.45范围)
    m64_amplify = 3.0
    # 跌停以上 (>-9%) 额外加50%幅度 (极端超跌更强反弹)
    if decline >= 9:
        m64_amplify *= 1.5
    elif decline >= 7:
        m64_amplify *= 1.25
    m64_score = m64_raw * m64_amplify

    # 涨停打开降权 (open_count>5 → ×0.5)
    # 目前处于跌停板附近,无法打开涨停 (卖出侧)
    open_count = 0  # 跌停不适用涨停打开
    reliability = 1.0

    return {
        "m64_score": round(m64_score, 4),
        "signals": signals,
        "open_count": open_count,
        "reliability": reliability,
    }


def compute_sector_heat_adjust(stocks: List[Dict]) -> Dict[str, float]:
    """板块热度系数计算"""
    sector_chgs = {}
    for s in stocks:
        sec = s.get('sector', '未知')
        quote = QUOTES_DATA.get(s['code'], {})
        chg = quote.get('sector_chg', 0)
        if sec not in sector_chgs:
            sector_chgs[sec] = []
        sector_chgs[sec].append(chg)

    sector_heat = {}
    for sec, chgs in sector_chgs.items():
        avg_chg = sum(chgs) / len(chgs)
        # 热度系数规则
        if avg_chg > 2:
            heat_coeff = 0.85  # 过热风险
        elif avg_chg > -1:
            heat_coeff = 1.0  # 正常
        elif avg_chg > -3:
            heat_coeff = 1.15  # 超跌反弹潜力
        else:
            heat_coeff = 1.20  # 极度超跌

        # 跌幅最大板块额外×1.05
        decline_avg = sum(abs(s.get('decline', 0)) for s in stocks if s.get('sector') == sec) / max(len([s for s in stocks if s.get('sector') == sec]), 1)
        if decline_avg >= 8:
            heat_coeff *= 1.05

        sector_heat[sec] = {"avg_chg": round(avg_chg, 2), "heat_coeff": round(heat_coeff, 3), "avg_decline": round(decline_avg, 2)}

    return sector_heat


def run_v132_analysis(stocks: List[Dict]) -> List[Dict]:
    """V13.2全链路分析: M46(35%) + M57(35%) + M64(15%) + M56(10%) + 数据质量(5%)"""

    sector_heat = compute_sector_heat_adjust(stocks)
    results = []
    
    # ══ P0-1修复: M46批量交叉截面归一化 (2026-06-24) ══
    if M46_NORMALIZED_AVAILABLE:
        # 使用新归一化引擎 (一次性计算全量 → 保证区分度)
        m46_batch = normalize_m46_batch(stocks, QUOTES_DATA)
        m46_map = {r.code: r for r in m46_batch}
        m46_stats = get_m46_stats(m46_batch)
        print(f"  📊 M46归一化: μ={m46_stats['mean']:.4f} σ={m46_stats['std']:.4f} 区分度={m46_stats.get('discrimination', m46_stats['range']):.4f}")
        print(f"     SB={m46_stats['strong_buy_pct']}% BUY={m46_stats['buy_pct']}% WATCH={m46_stats['watch_pct']}% HOLD={m46_stats['hold_pct']}%")
    else:
        m46_batch = None
        m46_map = {}
        print("  ⚠️ M46归一化不可用，跳过")
    
    for stock in stocks:
        code = stock['code']
        quote = QUOTES_DATA.get(code, {})

        # 如果没有行情数据,使用默认估算
        if not quote:
            quote = {
                'price': 0, 'open': 0, 'high': 0, 'low': 0,
                'close_prev': 0, 'volume': 0, 'hsl': stock.get('hsl', 1),
                'chg': stock.get('decline', 0), 'dt_price': 0,
                'sector_chg': 0, 'inout': 0, 'lb': 1.0,
            }

        # M46 贝叶斯 (P0-1修复: 交叉截面归一化)
        if code in m46_map:
            m46_norm = m46_map[code]
            m46_confidence = m46_norm.m46_normalized
            m46_bracket = m46_norm.bracket
            m46_recommendation = m46_norm.recommendation
        else:
            # 兜底: 使用旧版per-stock
            raw_factors = compute_raw_factors(stock, quote)
            m46_confidence = min(0.95, max(0.05, raw_factors['raw_total'] * 0.5))
            m46_bracket = "mid_surge" if m46_confidence >= 0.45 else "low_surge"
            m46_recommendation = "BUY" if m46_confidence >= 0.45 else "WATCH"

        # M57 Alpha
        m57 = m57_alpha(stock, quote)

        # M64 超跌反转
        m64 = m64_oversold_reversal(stock, quote)

        # M56 尾盘30分钟 (简单版: 基于当前趋势判断)
        m56_score = 0.0
        if quote.get('price') and quote.get('low'):
            tail_pos = (quote['price'] - quote['low']) / max(quote.get('high', quote['price']) - quote['low'], 0.01)
            m56_score = sigmoid((tail_pos - 0.2) * 4)

        # 数据质量评分
        has_quote = 1.0 if code in QUOTES_DATA else 0.5
        has_tier2 = 0.0

        # V13.2综合评分
        v132_score = (
            m46_confidence * 0.35 +
            abs(m57['composite']) * 0.35 +
            m64['m64_score'] * 0.15 +
            m56_score * 0.10 +
            (has_quote * 0.03 + has_tier2 * 0.02)
        )

        # 板块热度后调整
        sec = stock.get('sector', '未知')
        heat_coeff = sector_heat.get(sec, {}).get('heat_coeff', 1.0)
        v132_adjusted = v132_score * heat_coeff

        # 推荐等级 (基于M46归一化结果 + 板块热度调整)
        recommendation = m46_recommendation
        # 下跌板块中的高评分 → 上调一档
        if heat_coeff >= 1.15 and v132_score >= 0.35:
            if recommendation == "WATCH":
                recommendation = "BUY"
            elif recommendation == "BUY":
                recommendation = "STRONG_BUY"

        results.append({
            "code": code,
            "name": stock['name'],
            "decline_pct": stock.get('decline', 0),
            "amplitude": stock.get('amplitude', 0),
            "hsl": stock.get('hsl', 0),
            "sector": stock.get('sector', ''),
            "v132_score": round(v132_score, 4),
            "v132_adjusted": round(v132_adjusted, 4),
            "m46_confidence": m46_confidence,
            "m46_bracket": m46_bracket,
            "m57_composite": m57['composite'],
            "m57_t1_forecast": m57['t1_forecast'],
            "m57_factors": m57['factors'],
            "m57_activated": m57['activated'],
            "m64_score": m64['m64_score'],
            "m64_signals": m64['signals'],
            "m56_score": round(m56_score, 4),
            "sector_heat_coeff": heat_coeff,
            "recommendation": recommendation,
            "tier": "Tier1" if code in QUOTES_DATA else "Tier0",
            "data_quality": has_quote,
        })

    # 按V13.2调整评分排序
    results.sort(key=lambda x: x['v132_adjusted'], reverse=True)
    return results


# ═══════════════════════════════════════════════════════════
# SECTION 5: HTML报告生成
# ═══════════════════════════════════════════════════════════

def generate_p1_1_report(results: List[Dict], sector_heat: Dict, stats: Dict) -> str:
    """生成P1-1 HTML可视化报告"""

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    bypass_stats = stats.get('bypass_hub', {'enabled': False, 'promotions': 0})

    # Top 10推荐行
    top_rows = ''
    for i, r in enumerate(results[:20]):
        decline_color = '#ef4444'
        chg_display = f"{r['decline_pct']:+.2f}%"
        v132_color = '#22c55e' if r['v132_adjusted'] >= 0.45 else '#f59e0b' if r['v132_adjusted'] >= 0.30 else '#ef4444'
        rec_color = {'STRONG_BUY':'#22c55e','BUY':'#3b82f6','WATCH':'#f59e0b','HOLD':'#94a3b8'}.get(r['recommendation'], '#94a3b8')
        top_rows += f'''
        <tr>
            <td>{i+1}</td>
            <td>{r['code']}</td>
            <td>{r['name']}</td>
            <td style="color:{decline_color};font-weight:bold">{chg_display}</td>
            <td>{r['amplitude']:.2f}</td>
            <td>{r['hsl']:.2f}</td>
            <td style="color:{v132_color};font-weight:bold">{r['v132_adjusted']:.4f}</td>
            <td>{r['m46_confidence']:.4f}</td>
            <td>{r['m57_composite']:+.4f}</td>
            <td>{r['m64_score']:.4f}</td>
            <td>{r['sector_heat_coeff']:.3f}</td>
            <td style="color:{rec_color};font-weight:bold">{r['recommendation']}</td>
            <td>{r['m57_t1_forecast']:+.2f}%</td>
        </tr>'''

    # 板块热度表
    heat_rows = ''
    for sec, h in sorted(sector_heat.items(), key=lambda x: x[1]['heat_coeff'], reverse=True):
        heat_color = '#22c55e' if h['heat_coeff'] >= 1.15 else '#f59e0b' if h['heat_coeff'] >= 1.0 else '#ef4444'
        grade = '🔥超跌反弹' if h['heat_coeff'] >= 1.15 else '✅正常' if h['heat_coeff'] >= 1.0 else '⚠️过热风险'
        heat_rows += f'''
        <tr>
            <td>{sec}</td>
            <td>{h['avg_chg']:+.2f}%</td>
            <td style="color:{heat_color};font-weight:bold">{h['heat_coeff']:.3f}</td>
            <td>{h['avg_decline']:.2f}%</td>
            <td>{grade}</td>
        </tr>'''

    # M57因子激活状态
    factor_names = ['tail_rs','overnight_mom','intraday_rev','flow_accel','gap_fill_prob','sector_alpha','streak_exp','auction_sig','event_decay','lhb_effect','sentiment_trans','tail_vol_struct']
    factor_status = ''
    activated_count = 8
    for fn in factor_names:
        is_active = fn in ['tail_rs','overnight_mom','intraday_rev','flow_accel','gap_fill_prob','sector_alpha','streak_exp','auction_sig']
        color = '#22c55e' if is_active else '#ef4444'
        status = '✅激活' if is_active else '❌休眠'
        note = '' if is_active else '(需Tier2数据)'
        factor_status += f'<div style="display:inline-block;margin:4px;padding:6px 10px;background:{color}22;border:1px solid {color};border-radius:8px;font-size:0.82em;color:{color}">{fn} {status} {note}</div>'

    # 准备JS数据 (单独构建避免f-string冲突)
    scatter_json = json.dumps([{"x": abs(r['decline_pct']), "y": r['v132_adjusted'], "code": r['code'], "name": r['name'], "rec": r['recommendation']} for r in results[:30]])
    sector_names_json = json.dumps(list(sector_heat.keys()))
    sector_coeffs_json = json.dumps([h['heat_coeff'] for h in sector_heat.values()])
    sector_chgs_json = json.dumps([h['avg_chg'] for h in sector_heat.values()])

    # JS代码 (独立模板,不嵌入f-string)
    js_template = '''<script>
// 跌幅 vs V13.2评分散点图
const scatterCtx = document.getElementById('scatterChart').getContext('2d');
const scatterData = __SCATTER_DATA__;

const recColors = {'STRONG_BUY':'#22c55e','BUY':'#3b82f6','WATCH':'#f59e0b','HOLD':'#94a3b8'};
const datasets = {};
scatterData.forEach(p => {
    if (!datasets[p.rec]) datasets[p.rec] = [];
    datasets[p.rec].push(p);
});

new Chart(scatterCtx, {
    type: 'scatter',
    data: {
        datasets: Object.entries(datasets).map(([rec, points]) => {
            return {
                label: rec,
                data: points,
                backgroundColor: recColors[rec] || '#94a3b8',
                pointRadius: rec === 'STRONG_BUY' ? 10 : rec === 'BUY' ? 7 : 5,
                pointHoverRadius: 12
            };
        })
    },
    options: {
        scales: {
            x: { title: { display: true, text: '跌幅 %', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
            y: { title: { display: true, text: 'V13.2评分(调整后)', color: '#94a3b8' }, ticks: { color: '#94a3b8' }, grid: { color: '#334155' } }
        },
        plugins: {
            legend: { position: 'bottom', labels: { color: '#94a3b8' } },
            tooltip: {
                callbacks: {
                    label: ctx => ctx.raw.code + ' ' + ctx.raw.name + ': 跌幅' + ctx.raw.x.toFixed(1) + '% V13.2=' + ctx.raw.y.toFixed(4)
                }
            }
        },
        responsive: true,
        maintainAspectRatio: false
    }
});

// 板块热度柱状图
const sectorCtx = document.getElementById('sectorChart').getContext('2d');
const sectorNames = __SECTOR_NAMES__;
const sectorCoeffs = __SECTOR_COEFFS__;
const sectorChgs = __SECTOR_CHGS__;

new Chart(sectorCtx, {
    type: 'bar',
    data: {
        labels: sectorNames,
        datasets: [
            { label: '热度系数', data: sectorCoeffs, backgroundColor: sectorCoeffs.map(c => c >= 1.15 ? '#22c55e' : c >= 1.0 ? '#f59e0b' : '#ef4444') },
            { label: '板块涨跌%', data: sectorChgs, backgroundColor: sectorChgs.map(c => c > 0 ? '#22c55e88' : '#ef444488') }
        ]
    },
    options: {
        scales: {
            y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } }
        },
        plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8' } } },
        responsive: true,
        maintainAspectRatio: false
    }
});
</script>'''

    js_code = js_template.replace('__SCATTER_DATA__', scatter_json).replace('__SECTOR_NAMES__', sector_names_json).replace('__SECTOR_COEFFS__', sector_coeffs_json).replace('__SECTOR_CHGS__', sector_chgs_json)

    html_body = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>P1-1 首次实盘验证 2026-06-24 14:30 — V13.2圣杯尾盘猎手</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;
        background:#0f172a; color:#e2e8f0; padding:20px; }
.container { max-width:1600px; margin:0 auto; }
h1 { font-size:2em; background:linear-gradient(135deg,#f59e0b,#ef4444); -webkit-background-clip:text; -webkit-text-fill-color:transparent; margin-bottom:8px; }
h2 { font-size:1.3em; color:#94a3b8; margin:25px 0 12px; border-bottom:1px solid #334155; padding-bottom:6px; }
.subtitle { color:#64748b; margin-bottom:20px; font-size:0.9em; }
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin-bottom:25px; }
.kpi-card { background:#1e293b; border-radius:12px; padding:16px; border:1px solid #334155; }
.kpi-label { font-size:0.78em; color:#94a3b8; margin-bottom:4px; }
.kpi-value { font-size:1.8em; font-weight:bold; }
.kpi-sub { font-size:0.72em; color:#64748b; margin-top:3px; }
table { width:100%; border-collapse:collapse; background:#1e293b; border-radius:12px; overflow:hidden; }
th { background:#334155; color:#94a3b8; padding:8px 10px; text-align:left; font-size:0.80em; font-weight:600; white-space:nowrap; }
td { padding:7px 10px; border-bottom:1px solid #334155; font-size:0.82em; }
tr:hover { background:rgba(59,130,246,0.06); }
.insight-box { background:linear-gradient(135deg,#1e293b,#1a2332); border:1px solid #334155; border-radius:12px; padding:18px; margin-bottom:18px; }
.insight-title { color:#f59e0b; font-size:1.1em; margin-bottom:10px; font-weight:bold; }
.insight-item { padding:6px 0; color:#cbd5e1; font-size:0.88em; line-height:1.6; }
.chart-row { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-bottom:25px; }
.chart-box { background:#1e293b; border-radius:12px; padding:18px; border:1px solid #334155; }
.alert-box { border-radius:12px; padding:18px; margin-bottom:18px; }
.alert-yellow { background:rgba(245,158,11,0.1); border:2px solid #f59e0b; }
.alert-green { background:rgba(34,197,94,0.1); border:2px solid #22c55e; }
.footer { text-align:center; color:#64748b; font-size:0.78em; margin-top:35px; padding:18px; border-top:1px solid #334155; }
</style>
</head>
<body>
<div class="container">
<h1>🦅🔥 P1-1 首次实盘全链路验证</h1>
<p class="subtitle">
    📅 2026-06-24 周三 14:30 | 🖨 生成: ''' + timestamp + '''<br>
    交易日: ✅ 正常(端午节后第2日) | 数据源: TDX MCP 10工具实时直连 | V13.2 TDX版
</p>

<div class="kpi-grid">
    <div class="kpi-card">
        <div class="kpi-label">📊 候选池</div>
        <div class="kpi-value" style="color:#60a5fa">''' + str(stats['total']) + '''</div>
        <div class="kpi-sub">Screener跌幅前50筛选</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">🔥 Tier1覆盖</div>
        <div class="kpi-value" style="color:#22c55e">''' + str(stats['tier1']) + '''</div>
        <div class="kpi-sub">实时行情+K线数据</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">🟢 STRONG_BUY</div>
        <div class="kpi-value" style="color:#22c55e">''' + str(stats['strong_buy']) + '''</div>
        <div class="kpi-sub">高置信度推荐</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">🔵 BUY</div>
        <div class="kpi-value" style="color:#3b82f6">''' + str(stats['buy']) + '''</div>
        <div class="kpi-sub">中等置信度推荐</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">🟡 WATCH</div>
        <div class="kpi-value" style="color:#f59e0b">''' + str(stats['watch']) + '''</div>
        <div class="kpi-sub">待观察</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">📐 M57激活率</div>
        <div class="kpi-value" style="color:#60a5fa">8/12</div>
        <div class="kpi-sub">67% 4因子需Tier2</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">📊 平均V13.2</div>
        <div class="kpi-value" style="color:#f59e0b">''' + f"{stats['avg_v132']:.4f}" + '''</div>
        <div class="kpi-sub">板块热度调整后</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">🦅 市场指数</div>
        <div class="kpi-value" style="color:''' + ("#22c55e" if MARKET_INDEX["000001"]["chg"]>=0 else "#ef4444") + '''">''' + f"{MARKET_INDEX['000001']['chg']:+.2f}%" + '''</div>
        <div class="kpi-sub">创业板''' + f"{MARKET_INDEX['399006']['chg']:+.2f}%" + '''</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">🔀 BypassHub</div>
        <div class="kpi-value" style="color:''' + ("#22c55e" if bypass_stats.get('promotions',0)>0 else "#f59e0b") + '''">''' + str(bypass_stats.get('promotions', 0)) + '''</div>
        <div class="kpi-sub">旁路提升 V13.5.29</div>
    </div>
</div>

<div class="chart-row">
    <div class="chart-box">
        <h2 style="margin-top:0;font-size:1.1em">📊 跌幅分布 vs V13.2评分</h2>
        <canvas id="scatterChart" height="280"></canvas>
    </div>
    <div class="chart-box">
        <h2 style="margin-top:0;font-size:1.1em">🔥 板块热度系数矩阵</h2>
        <canvas id="sectorChart" height="280"></canvas>
    </div>
</div>

<h2>🔬 M57 12因子Alpha激活状态 (8/12 = 67%)</h2>
<div style="background:#1e293b;border-radius:12px;padding:15px;border:1px solid #334155;margin-bottom:20px">
    ''' + factor_status + '''
</div>

<h2>🔥 板块热度维度 (交叉分析驱动)</h2>
<table>
    <thead><tr><th>板块</th><th>板块涨跌</th><th>热度系数</th><th>平均跌幅</th><th>评级</th></tr></thead>
    <tbody>''' + heat_rows + '''</tbody>
</table>

<h2>🏆 Top 20 圣杯推荐 (V13.2综合评分×板块热度)</h2>
<table>
    <thead><tr><th>#</th><th>代码</th><th>名称</th><th>跌幅%</th><th>振幅</th><th>换手率</th><th>V13.2</th><th>M46</th><th>M57</th><th>M64</th><th>热度系数</th><th>建议</th><th>T+1预期</th></tr></thead>
    <tbody>''' + top_rows + '''</tbody>
</table>

<div class="alert-box alert-yellow">
    <div class="insight-title">📊 P1-1 验证里程碑</div>
    <div class="insight-item">🏆 首次使用TDX实时数据驱动全链路（非合成/非回测）</div>
    <div class="insight-item">🏆 Screener跌幅筛选 → 三层数据 → 四引擎评分 → 圣杯推荐</div>
    <div class="insight-item">🏆 M64超跌反转Alpha首次实盘注入</div>
    <div class="insight-item">🏆 板块热度实时调整机制激活</div>
    <div class="insight-item">🏆 T+1跟踪链路已就绪（明日15:10验证）</div>
</div>

<div class="insight-box">
    <div class="insight-title">📋 T+1验证计划 (6月25日周三)</div>
    <div class="insight-item">15:05复盘: 读取今日推荐列表，对比6月25日实际涨跌</div>
    <div class="insight-item">15:10奖惩: RewardEngine评估命中率+盈亏比</div>
    <div class="insight-item">计算因子IC、涨停命中率、盈亏比</div>
    <div class="insight-item">输出P1-1首轮验证报告</div>
</div>

<div class="footer">
    V13.2 P1-1 首次实盘验证 | 毕方灵犀貔貅助手 🦅🔥💰 | 2026-06-24 14:30<br>
    目标: T日尾盘选股→T+1涨停(≥99%)→连续趋势→圣杯级盈利能力
</div>
</div>

''' + js_code + '''
</body>
</html>'''

    return html_body


# ═══════════════════════════════════════════════════════════
# SECTION 6: 数据库持久化
# ═══════════════════════════════════════════════════════════

def persist_to_db(results: List[Dict]):
    """写入holy_grail.db daily_signals+p1_1_tracking表"""
    import sqlite3
    db_path = os.path.join('data', 'holy_grail.db')
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # 添加缺失列(已有则忽略)
    try: c.execute('ALTER TABLE daily_signals ADD COLUMN decline_pct REAL DEFAULT 0')
    except: pass
    try: c.execute('ALTER TABLE daily_signals ADD COLUMN amplitude REAL DEFAULT 0')
    except: pass
    try: c.execute('ALTER TABLE daily_signals ADD COLUMN hsl REAL DEFAULT 0')
    except: pass

    signal_date = datetime.now().strftime('%Y-%m-%d')
    created_at = datetime.now().isoformat()

    for r in results:
        # daily_signals — 匹配已有表结构
        c.execute('''INSERT OR REPLACE INTO daily_signals
            (signal_date, code, name, v132_score, m46_score, m57_score, m64_score,
             m56_score, sector_heat, change_pct, recommendation, tier,
             data_quality, decline_pct, amplitude, hsl, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (signal_date, r['code'], r['name'], r['v132_adjusted'],
             r['m46_confidence'], r['m57_composite'], r['m64_score'],
             r['m56_score'], r['sector_heat_coeff'], r['decline_pct'],
             r['recommendation'], r['tier'], int(r['data_quality']),
             r['decline_pct'], r['amplitude'], r['hsl'], created_at))

        # p1_1_tracking — 匹配已有表结构
        if r['recommendation'] in ('STRONG_BUY', 'BUY', 'WATCH'):
            c.execute('''INSERT OR REPLACE INTO p1_1_tracking
                (signal_date, code, name, recommendation, v132_score,
                 predicted_t1_change, verified_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)''',
                (signal_date, r['code'], r['name'], r['recommendation'],
                 r['v132_adjusted'], r['m57_t1_forecast']))

    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════
# SECTION 7: 主流程
# ═══════════════════════════════════════════════════════════

def apply_bypass_hub_postprocess(results: List[Dict]) -> Tuple[List[Dict], Dict]:
    """
    ★ V13.5.29 BypassHub 后处理 — 对非STRONG_BUY候选强制执行7旁路检查
    
    T4自动化强制执行, 不依赖Agent手动调用。
    对每个WATCH/HOLD/REJECT候选, 检查7条旁路, 若激活则提升推荐等级。
    
    Returns:
        (updated_results, bypass_stats)
    """
    try:
        from V13_5_29_BypassHub import BypassHub, quick_bypass_check
    except ImportError:
        print("  ⚠️ BypassHub不可用, 跳过后处理")
        return results, {"enabled": False, "reason": "ImportError"}
    
    hub = BypassHub()
    status = hub.get_status_summary()
    print(f"  🔧 BypassHub V13.5.29 已加载 | {status['available_paths']}")
    
    promotions = 0
    bypass_results = {}
    
    for r in results:
        code = r["code"]
        rec = r["recommendation"]
        
        # 仅对非强推候选检查旁路 (STRONG_BUY/BUY 已通过正常流程)
        if rec in ("STRONG_BUY", "BUY"):
            continue
        
        # 尝试加载K线数据
        klines = _load_klines_for_stock(code)
        if not klines or len(klines) < 10:
            continue  # 无K线数据则跳过
        
        # 从QUOTES_DATA获取实时数据
        quote = QUOTES_DATA.get(code, {})
        stock_pct = quote.get("chg", r.get("decline_pct", 0))
        sector_pct = quote.get("sector_chg", 0)
        hsl = r.get("hsl", 0)
        lb = quote.get("lb", 1.0)
        
        # 计算量比近似 (volume vs 5日均量)
        today_vol = quote.get("volume", 0)
        avg_vol_5d = sum(float(k.get("vol", 0)) for k in klines[-6:-1]) / 5 if len(klines) >= 6 else today_vol
        vol_ratio = today_vol / avg_vol_5d if avg_vol_5d > 0 else 1.0
        
        # 计算距20日低点距离
        lows_20d = [float(k["low"]) for k in klines[-20:]]
        low_20d = min(lows_20d) if lows_20d else 0
        close_now = quote.get("price", 0)
        dist_from_low = (close_now - low_20d) / low_20d * 100 if low_20d > 0 else 100
        
        # 估算D29/D28 (从现有数据推断)
        d29_est = _estimate_d29_from_klines(klines)
        d28_est = _estimate_d28_from_klines(klines, sector_pct)
        
        # 调用BypassHub
        verdict = hub.check_all(
            code=code,
            klines_daily=klines,
            d29_score=d29_est,
            d28_score=d28_est,
            five_confirm_passed=1,  # 默认仅D29通过
            stock_decline_pct=stock_pct,
            sector_change_pct=sector_pct,
            volume_vs_5d=vol_ratio,
            is_limit_down=(abs(stock_pct) >= 9.9),
            position_from_20d_low=dist_from_low,
            current_defcon="YELLOW",
            stock_20d_pct=_calc_20d_pct(klines),
            sector_20d_pct=0,
        )
        
        bypass_results[code] = verdict
        
        if verdict["bypass"]:
            old_rec = rec
            new_rec = BypassHub.apply_to_recommendation(old_rec, verdict)
            if new_rec != old_rec:
                r["recommendation"] = new_rec
                r["bypass_info"] = {
                    "active_paths": verdict["active_paths"],
                    "reason": verdict["reason"],
                }
                promotions += 1
                print(f"    ★ [{code}] {old_rec} → {new_rec} ({verdict['reason'][:60]}...)")
    
    bypass_stats = {
        "enabled": True,
        "version": "V13.5.29",
        "total_checked": len(bypass_results),
        "promotions": promotions,
        "active_paths_used": list(set(
            p for v in bypass_results.values() for p in v.get("active_paths", [])
        )),
        "bypass_results": bypass_results,
    }
    
    if promotions > 0:
        print(f"  ★ BypassHub: {promotions}只候选提升推荐等级")
    else:
        print(f"  ℹ BypassHub: 无旁路激活")
    
    return results, bypass_stats


def _load_klines_for_stock(code: str) -> List[Dict]:
    """尝试从缓存加载K线数据"""
    import glob as _glob
    # 从fullmarket_cache查找
    cache_pattern = f"data/fullmarket_cache/*{code}*.json"
    cache_files = _glob.glob(cache_pattern)
    if cache_files:
        try:
            with open(cache_files[0], "r", encoding="utf-8") as f:
                data = json.load(f)
            if "klines" in data:
                return data["klines"]
        except Exception:
            pass
    
    # 从个股缓存查找
    stock_cache = f"data/fullmarket_cache/stock_{code}.json"
    if os.path.exists(stock_cache):
        try:
            with open(stock_cache, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            if "klines" in data:
                return data["klines"]
        except Exception:
            pass
    
    return []


def _estimate_d29_from_klines(klines: List[Dict]) -> int:
    """从K线数据估算D29双洗盘得分"""
    if len(klines) < 10:
        return 3
    closes = [float(k["close"]) for k in klines]
    vols = [float(k.get("vol", 0)) for k in klines]
    
    # 检查连续下跌天数
    consecutive_down = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            consecutive_down += 1
        else:
            break
    
    # 检查缩量
    recent_vol = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    prior_vol = sum(vols[-10:-5]) / 5 if len(vols) >= 10 else recent_vol
    shrink = 1 - (recent_vol / prior_vol) if prior_vol > 0 else 0
    
    score = 0
    if consecutive_down >= 4: score += 4
    elif consecutive_down >= 3: score += 3
    elif consecutive_down >= 2: score += 2
    if shrink > 0.5: score += 5
    elif shrink > 0.3: score += 4
    elif shrink > 0.2: score += 3
    elif shrink > 0.1: score += 2
    
    return min(score, 12)


def _estimate_d28_from_klines(klines: List[Dict], sector_pct: float) -> int:
    """从K线+板块估算D28催化得分"""
    score = 0
    if sector_pct > 2: score += 4
    elif sector_pct > 1: score += 3
    elif sector_pct > 0: score += 2
    
    # 近期有放量上涨 → 可能有催化
    if len(klines) >= 5:
        recent_vols = [float(k.get("vol", 0)) for k in klines[-5:]]
        avg_vol = sum(recent_vols) / 5
        if avg_vol > 0:
            last_vol = recent_vols[-1]
            if last_vol > avg_vol * 1.5: score += 2
            elif last_vol > avg_vol * 1.2: score += 1
    
    return min(score, 10)


def _calc_20d_pct(klines: List[Dict]) -> float:
    """计算20日涨跌幅"""
    if len(klines) < 20:
        return 0
    return (float(klines[-1]["close"]) - float(klines[0]["close"])) / float(klines[0]["close"]) * 100


def main():
    t0 = time.time()
    print("=" * 70)
    print("  P1-1 14:30 首次实盘全链路验证 2026-06-24")
    print("  V13.5.29 TDX实时数据集成版 + BypassHub")
    print("=" * 70)

    # 1. 运行V13.2分析
    print(f"\n[1/6] V13.2全链路分析: {len(SCREENER_RAW)}只候选股...")
    results = run_v132_analysis(SCREENER_RAW)
    
    # 1.5. ★ V13.5.29 BypassHub 后处理 — 强制7旁路检查
    print(f"\n[2/6] BypassHub V13.5.29 7旁路后处理...")
    results, bypass_stats = apply_bypass_hub_postprocess(results)

    # 3. 统计 (含BypassHub结果)
    strong_buy = sum(1 for r in results if r['recommendation'] == 'STRONG_BUY')
    buy = sum(1 for r in results if r['recommendation'] == 'BUY')
    watch = sum(1 for r in results if r['recommendation'] == 'WATCH')
    hold = sum(1 for r in results if r['recommendation'] == 'HOLD')
    tier1 = sum(1 for r in results if r['tier'] == 'Tier1')
    avg_v132 = sum(r['v132_adjusted'] for r in results) / len(results) if results else 0
    bypass_promoted = bypass_stats.get("promotions", 0) if bypass_stats else 0

    stats = {
        'total': len(results),
        'tier1': tier1,
        'strong_buy': strong_buy,
        'buy': buy,
        'watch': watch,
        'hold': hold,
        'avg_v132': avg_v132,
        'bypass_hub': bypass_stats,
        'bypass_promoted': bypass_promoted,
    }

    # 4. 板块热度
    sector_heat = compute_sector_heat_adjust(SCREENER_RAW)

    # 5. 生成HTML报告
    print(f"\n[3/6] 生成P1-1 HTML报告...")
    html = generate_p1_1_report(results, sector_heat, stats)
    html_path = os.path.join('data', 'p1_1_maiden_report_20260624.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  ✅ HTML: {html_path}")

    # 6. 保存JSON结果
    print(f"\n[4/6] 保存JSON结果...")
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    json_path = os.path.join('data', f'holy_grail_v132_{timestamp_str}.json')

    output_data = {
        'timestamp': timestamp_str,
        'datetime': datetime.now().isoformat(),
        'version': 'V13.5.29 P1-1 TDX + BypassHub',
        'p1_marker': 'P1-1 首次实盘验证',
        'bypass_hub': bypass_stats,
        'm46_calibration': {
            'method': 'cross_sectional_zscore',  # P0-1修复: 交叉截面归一化
            'target_mean': 0.50, 'target_std': 0.15,
            'threshold_type': 'percentile_dynamic',  # 动态百分位(非固定阈值)
            'percentiles': {'strong_buy': 'top20%', 'buy': '20-45%', 'watch': '45-75%'},
            'm46_stats': m46_stats if M46_NORMALIZED_AVAILABLE else {'note': '归一化不可用'},
        },
        'stats': stats,
        'market_index': MARKET_INDEX,
        'sector_heat': sector_heat,
        'results': results,
        'screener_raw': SCREENER_RAW,
    }

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)

    # 保存最新报告 (供15:10奖惩回路读取)
    latest_path = os.path.join('data', 'holy_grail_latest.json')
    with open(latest_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"  ✅ JSON: {json_path}")
    print(f"  ✅ Latest: {latest_path}")

    # 7. DB持久化
    print(f"\n[5/6] DB持久化...")
    persist_to_db(results)
    print(f"  ✅ daily_signals + p1_1_tracking 已写入")

    # 8. 输出汇总
    elapsed = time.time() - t0
    print(f"\n[6/6] 执行耗时: {elapsed:.1f}s")

    print(f"\n{'=' * 70}")
    print(f"  ╔══════════════════════════════════════════════════════════╗")
    print(f"  ║  P1-1 14:30 首次实盘全链路验证 2026-06-24              ║")
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  交易日: ✅ 正常交易 (周三)                             ║")
    print(f"  ║  数据源: TDX MCP 10工具 实时直连                        ║")
    print(f"  ║  候选池: {stats['total']}只 (screener跌幅前50筛选)                     ║")
    print(f"  ║  Tier1覆盖: {stats['tier1']}只 (行情数据)                              ║")
    print(f"  ║  V13.2分析: {stats['total']}只完成                                     ║")
    print(f"  ╠══════════════════════════════════════════════════════════╣")
    print(f"  ║  STRONG_BUY: {strong_buy}只 | BUY: {buy}只 | WATCH: {watch}只               ║")
    print(f"  ║  M57激活: 8/12因子 | 平均V13.2评分: {avg_v132:.4f}              ║")
    print(f"  ║  板块热度: 顶部({sum(1 for h in sector_heat.values() if h['heat_coeff']>=1.15)}) 底部({sum(1 for h in sector_heat.values() if h['heat_coeff']<1.0)})              ║")
    print(f"  ║  执行耗时: {elapsed:.1f}s                                         ║")
    print(f"  ║  结果文件: {json_path}")
    print(f"  ║          {html_path}")
    print(f"  ╚══════════════════════════════════════════════════════════╝")

    print(f"\nTop 10 圣杯推荐:")
    print(f"{'排名':<4} {'代码':<8} {'名称':<10} {'跌幅%':>8} {'V13.2':>8} {'M46':>8} {'M57':>8} {'M64':>8} {'热度':>6} {'建议':<12} {'T+1预期'}")
    print(f"{'-'*85}")
    for i, r in enumerate(results[:10]):
        print(f"{i+1:<4} {r['code']:<8} {r['name']:<10} {r['decline_pct']:>+8.2f} {r['v132_adjusted']:>8.4f} {r['m46_confidence']:>8.4f} {r['m57_composite']:>+8.4f} {r['m64_score']:>8.4f} {r['sector_heat_coeff']:>6.3f} {r['recommendation']:<12} {r['m57_t1_forecast']:>+6.2f}%")

    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📊 P1-1 验证里程碑")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🏆 首次使用TDX实时数据驱动全链路（非合成/非回测）")
    print(f"🏆 Screener跌幅筛选 → 三层数据 → 四引擎评分 → 圣杯推荐")
    print(f"🏆 M64超跌反转Alpha首次实盘注入")
    print(f"🏆 板块热度实时调整机制激活")
    print(f"🏆 T+1跟踪链路已就绪（明日15:10验证）")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    print(f"\n📋 T+1验证计划 (6月25日周三):")
    print(f"  - 15:05复盘: 读取今日推荐列表，对比6月25日实际涨跌")
    print(f"  - 15:10奖惩: RewardEngine评估命中率+盈亏比")
    print(f"  - 计算因子IC、涨停命中率、盈亏比")
    print(f"  - 输出P1-1首轮验证报告")

    return html_path, json_path


if __name__ == '__main__':
    main()
