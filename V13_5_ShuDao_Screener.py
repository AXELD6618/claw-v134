#!/usr/bin/env python3
"""
V13.5.19 蜀道模式全市场扫描器
================================
基于蜀道装备(300540) 6/24暴跌-8.97% + 主力净额+169万 + 超大单-166万/大单+335万
的拆单吸筹模式，构建T日尾盘全市场实时扫描器。

核心维度：
  D29 双洗盘识别   (12分) — 底部抬高 + 暴跌日主力微正
  D31 主力资金意图 (15分) — 洗盘日主力净额为正 + 连续3日正
  D32 DDX大单动向  (10分) — 暴跌日修正
  D33 外盘/内盘比率 (3分) — 外盘>内盘
  D34 主力拆单识别  (5分) — 超大单<0 + 大单>0 + 主力>0
  D35 庄成本线距离  (5分) — 股价回踩/站上庄家成本线
  D36 委比异动      (3分) — 委比>15%且当日下跌 = 护盘/洗盘

五确认体系：D29 + D31 + D32 + D33 + D34 同时满足 → 历史最强买入信号

执行方式：
  python V13_5_ShuDao_Screener.py [--limit N] [--output path]

作者：毕方灵犀貔貅助手（亚瑟数字分身）
版本：V13.5.19
日期：2026-07-05
"""

import sys
import json
import os
import time
from datetime import datetime
from typing import Dict, List, Optional, Any

sys.path.insert(0, ".")

# ═══════════════════════════════════════════════════════════
# 1. TDX 连接器导入（在 WorkBuddy 运行环境内由 TDX MCP 提供）
# ═══════════════════════════════════════════════════════════
try:
    from tdx_connector import tdx_screener, tdx_api_data, tdx_quotes, tdx_kline
    TDX_AVAILABLE = True
except Exception as e:
    TDX_AVAILABLE = False
    tdx_screener = tdx_api_data = tdx_quotes = tdx_kline = None
    print(f"[WARNING] tdx_connector 未导入: {e}")

from V13_5_TDX_EnhancedFeeder import infer_setcode
from V13_5_M71_ReversalPredictor import (
    ReversalPredictor,
    TDXDataAdapter,
    CapitalFlow,
    DragonTigerEntry,
)


# ═══════════════════════════════════════════════════════════
# 2. 日志工具
# ═══════════════════════════════════════════════════════════
def log(msg: str, level: str = 'INFO'):
    icons = {'INFO': 'ℹ️', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'PROGRESS': '🔄'}
    icon = icons.get(level, 'ℹ️')
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {icon} {msg}")


# ═══════════════════════════════════════════════════════════
# 3. TDX 数据获取与解析
# ═══════════════════════════════════════════════════════════
def get_screener_candidates(message: str = "跌幅超过5%且主力资金净流入", page_size: int = 30) -> List[Dict]:
    """通过 TDX 自然语言选股器获取候选股票"""
    if not TDX_AVAILABLE or not tdx_screener:
        log("TDX screener 不可用，返回空候选列表", 'WARNING')
        return []
    try:
        results = tdx_screener(message=message, pageSize=page_size)
        if not results:
            return []
        if isinstance(results, dict):
            results = results.get('results', results.get('data', []))
        return [r for r in results if isinstance(r, dict)]
    except Exception as e:
        log(f"TDX screener 调用失败: {e}", 'ERROR')
        return []


def get_klines(code: str, count: int = 60) -> List[Any]:
    """获取日K线并转换为 KlineBar 列表"""
    if not TDX_AVAILABLE or not tdx_kline:
        return []
    setcode = infer_setcode(code)
    raw = None
    # 优先尝试两种常见参数风格
    try:
        raw = tdx_kline(code=code, setcode=setcode, period=7, count=count)
    except Exception:
        try:
            raw = tdx_kline(code=code, setcode=setcode, period='4', wantNum=str(count))
        except Exception as e:
            log(f"获取 {code} K线失败: {e}", 'WARNING')
            return []
    if not raw:
        return []
    try:
        return TDXDataAdapter.parse_kline(raw)
    except Exception as e:
        log(f"解析 {code} K线失败: {e}", 'WARNING')
        return []


def normalize_quote(raw: Any) -> Dict[str, Any]:
    """将 TDX quotes 返回解析为统一格式"""
    if isinstance(raw, list) and len(raw) > 0:
        raw = raw[0]
    if not isinstance(raw, dict):
        return {}

    quote = {}

    # 常见字段映射（HQInfo 前缀 / 中文 / 英文）
    mappings = {
        'open': ['HQInfo.Open', 'Open', 'open', '开盘价', '今开'],
        'close': ['HQInfo.Close', 'Close', 'close', '最新价', '收盘价', '现价'],
        'high': ['HQInfo.High', 'High', 'high', '最高价'],
        'low': ['HQInfo.Low', 'Low', 'low', '最低价'],
        'volume': ['HQInfo.Volume', 'Volume', 'volume', '成交量', 'Vol'],
        'amount': ['HQInfo.Amount', 'Amount', 'amount', '成交额', 'Amount'],
        'chg_pct': ['HQInfo.ChgPct', 'ChgPct', 'chg_pct', '涨跌幅', 'change_percent'],
        'turnover': ['HQInfo.Turnover', 'Turnover', 'turnover', '换手率', 'Hsl'],
    }
    for key, aliases in mappings.items():
        for alias in aliases:
            if alias in raw and raw[alias] is not None:
                try:
                    quote[key] = float(raw[alias])
                except (ValueError, TypeError):
                    quote[key] = raw[alias]
                break

    # 委比 (Wtb / 委比)
    for k in ['HQInfo.Wtb', 'Wtb', 'weibi', '委比', 'WeiBi']:
        if k in raw and raw[k] is not None:
            try:
                quote['weibi'] = float(raw[k])
            except (ValueError, TypeError):
                pass
            break

    # 外盘/内盘
    outer = None
    inner = None
    for k in ['HQInfo.Wp', 'Wp', 'outer', '外盘', 'WaiPan']:
        if k in raw and raw[k] is not None:
            try:
                outer = float(raw[k])
            except (ValueError, TypeError):
                pass
            break
    for k in ['HQInfo.Np', 'Np', 'inner', '内盘', 'NeiPan']:
        if k in raw and raw[k] is not None:
            try:
                inner = float(raw[k])
            except (ValueError, TypeError):
                pass
            break
    if outer is not None and inner is not None and inner > 0:
        quote['outer_inner_ratio'] = outer / inner
        quote['outer'] = outer
        quote['inner'] = inner

    return quote


def get_quote(code: str) -> Dict[str, Any]:
    """获取实时行情数据"""
    if not TDX_AVAILABLE or not tdx_quotes:
        return {}
    try:
        raw = tdx_quotes(codes=[code], fields="open,high,low,close,volume,amount,chg_pct,turnover,Wtb,Wp,Np")
        return normalize_quote(raw)
    except Exception as e:
        log(f"获取 {code} 实时行情失败: {e}", 'WARNING')
        return {}


def parse_capital_flow_history(api_response: Dict) -> List[Dict]:
    """
    解析 tdx_api_data(zjlx) 返回为 capital_flow_history。
    包含 main_net / super_large_net / large_net 三个字段，单位为万元。
    """
    history = []
    if not api_response or not api_response.get('ok'):
        return history

    try:
        response_data = api_response.get('response', {})
        transformed = response_data.get('transformed', {})
        tables = transformed.get('tables', [])
    except Exception:
        return history

    if not tables:
        return history

    capital_flow_table = None
    for table in tables:
        if table.get('name') == 'capital_flow':
            capital_flow_table = table
            break
    if not capital_flow_table:
        return history

    rows = capital_flow_table.get('rows', [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_str = row.get('日期', '')
        if not date_str:
            continue

        def _wan(key_options):
            for k in key_options:
                if k in row and row[k] is not None:
                    try:
                        return float(row[k]) / 10000.0
                    except (ValueError, TypeError):
                        return 0.0
            return 0.0

        main_net = _wan(['主力净额金额(元)', '主力净额', 'main_net', '主力净额(元)'])
        super_large_net = _wan(['超大单净买入金额(元)', '超大单净额', 'super_large_net', '超大单净额(元)'])
        large_net = _wan(['大单净买入金额(元)', '大单净额', 'large_net', '大单净额(元)'])
        close = _wan(['收盘价', 'close', 'Close'])  # 收盘价不需要万元转换，但_wan做了/10000；这里单独处理
        close_val = 0.0
        for k in ['收盘价', 'close', 'Close']:
            if k in row and row[k] is not None:
                try:
                    close_val = float(row[k])
                    break
                except (ValueError, TypeError):
                    pass

        history.append({
            'date': date_str,
            'main_net': main_net,
            'super_large_net': super_large_net,
            'large_net': large_net,
            'close': close_val,
        })

    history.sort(key=lambda x: x['date'])
    return history


def get_capital_flow_history(code: str) -> List[Dict]:
    """通过 tdx_api_data(zjlx) 获取历史资金流向"""
    if not TDX_AVAILABLE or not tdx_api_data:
        return []
    try:
        response = tdx_api_data(
            entry="TdxSharePCCW.tdxf10_gg_jyds",
            fixedTag="zjlx",
            code=code,
        )
        return parse_capital_flow_history(response)
    except Exception as e:
        log(f"获取 {code} 资金流向失败: {e}", 'WARNING')
        return []


def build_capital_flow(capital_flow_history: List[Dict]) -> CapitalFlow:
    """从历史资金流向构建当日 CapitalFlow 对象（供 D31 使用）"""
    if not capital_flow_history:
        return CapitalFlow()

    latest = capital_flow_history[-1]
    main_net = latest.get('main_net', 0) * 10000  # 转回元
    super_large_net = latest.get('super_large_net', 0) * 10000
    large_net = latest.get('large_net', 0) * 10000

    # 5日/10日/60日累计（元）
    main_5d = sum(d.get('main_net', 0) for d in capital_flow_history[-5:]) * 10000
    main_10d = sum(d.get('main_net', 0) for d in capital_flow_history[-10:]) * 10000
    main_60d = sum(d.get('main_net', 0) for d in capital_flow_history[-60:]) * 10000

    # 计算近5日正负天数
    recent = capital_flow_history[-6:-1] if len(capital_flow_history) >= 6 else capital_flow_history[:-1]
    positive_days = sum(1 for d in recent if d.get('main_net', 0) > 0)
    negative_days = sum(1 for d in recent if d.get('main_net', 0) < 0)

    flow_trend = '振荡'
    flow_reversal = False
    if main_net > 0 and positive_days >= 3:
        flow_trend = '持续流入'
    elif main_net > 0 and negative_days >= 3:
        flow_trend = '转正'
        flow_reversal = True
    elif main_net < 0 and negative_days >= 3:
        flow_trend = '持续流出'

    return CapitalFlow(
        ddx=0.0,
        ddy=0.0,
        ddf=0.0,
        mainlx=main_net,
        main_5d=main_5d,
        main_10d=main_10d,
        main_60d=main_60d,
        super_large_net=super_large_net,
        large_net=large_net,
        flow_trend=flow_trend,
        flow_reversal=flow_reversal,
    )


# ═══════════════════════════════════════════════════════════
# 4. 蜀道模式评分
# ═══════════════════════════════════════════════════════════
def evaluate_shudao_pattern(code: str, name: str,
                             klines: List[Any],
                             quote: Dict[str, Any],
                             capital_flow_history: List[Dict]) -> Dict[str, Any]:
    """对单只股票执行蜀道模式 7 维度评分"""
    predictor = ReversalPredictor()
    engine = predictor.engine

    capital_flow = build_capital_flow(capital_flow_history)

    d29 = engine.score_double_washout(klines, capital_flow_history=capital_flow_history)
    d31 = engine.score_main_force_intent(klines,
                                         capital_flow=capital_flow,
                                         capital_flow_history=capital_flow_history,
                                         d28_score=0.0)
    d32 = engine.score_ddx_estimate(klines)
    d33 = engine.score_outer_inner_ratio(klines, quote_data=quote)
    d34 = engine.score_split_order(klines, capital_flow=capital_flow,
                                   capital_flow_history=capital_flow_history)
    d35 = engine.score_dealer_cost_line(klines)
    d36 = engine.score_weibi_anomaly(klines, quote_data=quote)

    five_confirm_count = 0
    confirm_details = []
    if d29.actual_score >= 6.0:
        five_confirm_count += 1
        confirm_details.append(f'D29={d29.actual_score:.0f}')
    if d31.actual_score >= 6.0:
        five_confirm_count += 1
        confirm_details.append(f'D31={d31.actual_score:.0f}')
    if d32.actual_score >= 5.0:
        five_confirm_count += 1
        confirm_details.append(f'D32={d32.actual_score:.0f}')
    if d33.actual_score >= 2.0:
        five_confirm_count += 1
        confirm_details.append(f'D33={d33.actual_score:.0f}')
    if d34.actual_score >= 3.0:
        five_confirm_count += 1
        confirm_details.append(f'D34={d34.actual_score:.0f}')

    # 简易 grade
    grade = 'NO_SIGNAL'
    if five_confirm_count >= 5:
        grade = 'STRONG_BUY'
    elif five_confirm_count >= 4:
        grade = 'BUY'
    elif five_confirm_count >= 3:
        grade = 'WATCH'

    return {
        'code': code,
        'name': name,
        'price': quote.get('close', 0),
        'chg_pct': quote.get('chg_pct', 0),
        'd29_score': d29.actual_score,
        'd31_score': d31.actual_score,
        'd32_score': d32.actual_score,
        'd33_score': d33.actual_score,
        'd34_score': d34.actual_score,
        'd35_score': d35.actual_score,
        'd36_score': d36.actual_score,
        'five_confirm_count': five_confirm_count,
        'confirm_details': confirm_details,
        'grade': grade,
        'd29_detail': d29.detail,
        'd31_detail': d31.detail,
        'd34_detail': d34.detail,
        'd35_detail': d35.detail,
        'd36_detail': d36.detail,
        'capital_flow_history': capital_flow_history,
    }


# ═══════════════════════════════════════════════════════════
# 5. 主控流程
# ═══════════════════════════════════════════════════════════
def scan_shudao_mode(limit: int = 30, max_eval: int = 20) -> Dict[str, Any]:
    """
    执行蜀道模式全市场扫描

    Args:
        limit: TDX screener 返回候选数量上限
        max_eval: 实际深度评分候选数量上限（控制积分消耗）
    """
    log("=" * 80)
    log("🎯 V13.5.19 蜀道模式全市场扫描器启动")
    log("=" * 80)

    if not TDX_AVAILABLE:
        log("TDX 连接器不可用，无法执行实时扫描。请确认在 WorkBuddy 环境内运行。", 'ERROR')
        return {
            'timestamp': datetime.now().isoformat(),
            'version': 'V13.5.19',
            'tdx_available': False,
            'candidates': [],
            'five_confirm_candidates': [],
        }

    # Step 1: 获取 screener 候选
    log("Step 1: TDX screener 获取跌幅+主力净流入候选...", 'PROGRESS')
    screener_results = get_screener_candidates(message="跌幅超过5%且主力资金净流入", page_size=limit)
    log(f"获取候选 {len(screener_results)} 只", 'SUCCESS')

    # Step 2: 逐只深度评分
    log(f"Step 2: 对前 {max_eval} 只候选执行 D29-D36 深度评分...", 'PROGRESS')
    evaluated = []
    for i, item in enumerate(screener_results[:max_eval], 1):
        code = str(item.get('code', '')).strip()
        name = item.get('name', '')
        if not code:
            continue

        log(f"[{i}/{min(max_eval, len(screener_results))}] {code} {name}", 'INFO')

        klines = get_klines(code)
        if len(klines) < 10:
            log(f"  {code} K线不足，跳过", 'WARNING')
            continue

        quote = get_quote(code)
        capital_flow_history = get_capital_flow_history(code)

        result = evaluate_shudao_pattern(code, name, klines, quote, capital_flow_history)
        result['screener_meta'] = {k: v for k, v in item.items() if k not in ('code', 'name')}
        evaluated.append(result)

        # 简单反压，避免 MCP 限流
        time.sleep(0.1)

    # Step 3: 排序与筛选
    # 优先：五确认数高 → D34 高 → D29 高
    evaluated.sort(key=lambda x: (x['five_confirm_count'], x['d34_score'], x['d29_score']), reverse=True)
    five_confirm_candidates = [c for c in evaluated if c['five_confirm_count'] >= 4]

    # Step 4: 输出
    output = {
        'timestamp': datetime.now().isoformat(),
        'version': 'V13.5.19',
        'tdx_available': True,
        'scan_message': '跌幅超过5%且主力资金净流入',
        'total_evaluated': len(evaluated),
        'candidates': evaluated,
        'five_confirm_candidates': five_confirm_candidates,
    }

    # 保存 JSON
    os.makedirs('data/fullmarket_cache', exist_ok=True)
    today = datetime.now().strftime('%Y%m%d')
    output_file = f'data/fullmarket_cache/shudao_screener_{today}.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log(f"结果已保存: {output_file}", 'SUCCESS')

    # 控制台摘要
    print("\n" + "=" * 80)
    print(f"📊 蜀道模式扫描结果 | 共评估 {len(evaluated)} 只 | 五确认候选 {len(five_confirm_candidates)} 只")
    print("=" * 80)
    for i, c in enumerate(evaluated[:10], 1):
        print(f"  {i}. {c['code']} {c['name']} — 五确认={c['five_confirm_count']}/5 "
              f"D29={c['d29_score']:.1f} D31={c['d31_score']:.1f} D32={c['d32_score']:.1f} "
              f"D33={c['d33_score']:.1f} D34={c['d34_score']:.1f} D35={c['d35_score']:.1f} D36={c['d36_score']:.1f} "
              f"[{c['grade']}]")
    if five_confirm_candidates:
        print("\n🔥 五确认/四确认强势候选（优先观察）:")
        for i, c in enumerate(five_confirm_candidates[:5], 1):
            print(f"  {i}. {c['code']} {c['name']} — 五确认={c['five_confirm_count']}/5 | D34={c['d34_score']:.1f}")

    return output


# ═══════════════════════════════════════════════════════════
# 6. CLI 入口
# ═══════════════════════════════════════════════════════════
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='V13.5.19 蜀道模式全市场扫描器')
    parser.add_argument('--limit', type=int, default=30, help='TDX screener 返回数量上限')
    parser.add_argument('--max-eval', type=int, default=20, help='深度评分候选数量上限（控制积分）')
    parser.add_argument('--output', type=str, default=None, help='输出 JSON 路径（默认按日期生成）')
    args = parser.parse_args()

    result = scan_shudao_mode(limit=args.limit, max_eval=args.max_eval)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        log(f"自定义输出已保存: {args.output}", 'SUCCESS')
