#!/usr/bin/env python3
"""
V13.5.19 T4 14:15 全市场临门一脚 - 37维度五确认体系蜀道装备模式版本
===============================================================
V13.5.19升级: D34拆单识别 + D35庄成本线 + D36委比异动 + 五确认体系(D29+D31+D32+D33+D34)

执行时间: 每日 14:15
核心升级(V13.5.19):
  1. D34拆单识别(5分): 超大单<0+大单>0+主力>0 = 主力暗中吸筹铁证
  2. D35庄成本线(5分): 股价回踩/站上庄家成本线 = 支撑确认
  3. D36委比异动(3分): 委比>15%且当日下跌 = 主力挂单护盘/洗盘信号
  4. 五确认体系: D29(洗盘)+D31(资金)+D32(DDX)+D33(外盘内盘)+D34(拆单) = 历史最强买入信号
  5. 三确认/四确认/五确认梯度加成: +2/+4/+8分

V13.5.18保留:
  - D25三路评分/D28催化/D29双洗盘/D30尾盘/D31主力意图/D32 DDX/D33外盘内盘
  - SECTOR_CRASH洗盘豁免: D29≥6时豁免崩盘惩罚
"""

import sys
import json
import os
import subprocess
from datetime import datetime
from typing import Dict, List, Optional

# 添加项目路径
sys.path.insert(0, ".")

def log(msg: str, level: str = 'INFO'):
    """日志输出"""
    icons = {'INFO': 'ℹ️', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'PROGRESS': '🔄'}
    icon = icons.get(level, 'ℹ️')
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {icon} {msg}")

def get_realtime_quote(code: str) -> Optional[Dict]:
    """
    获取实时行情数据 - 用于D15集合竞价缺口计算
    
    返回: {
        'open': 今日开盘价,
        'close': 昨日收盘价,
        'high': 最高价,
        'low': 最低价,
        'volume': 成交量,
        'amount': 成交额,
        'chg_pct': 涨跌幅(%)
    }
    """
    try:
        # 调用TDX quotes API
        result = subprocess.run([
            'python', '-c', f'''
import json
import sys
sys.path.insert(0, ".")
try:
    from tdx_connector import tdx_quotes
    quotes = tdx_quotes(codes=["{code}"], fields="open,high,low,close,volume,amount,chg_pct,turnover")
    if quotes and len(quotes) > 0:
        print(json.dumps(quotes[0]))
    else:
        print("{{}}")
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    print("{{}}")
'''
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if data and 'HQInfo.Open' in data:
                return {
                    'open': float(data.get('HQInfo.Open', 0)),
                    'close': float(data.get('HQInfo.Close', 0)),
                    'high': float(data.get('HQInfo.High', 0)),
                    'low': float(data.get('HQInfo.Low', 0)),
                    'volume': float(data.get('HQInfo.Volume', 0)),
                    'amount': float(data.get('HQInfo.Amount', 0)),
                    'chg_pct': float(data.get('HQInfo.ChgPct', 0)),
                }
    except Exception as e:
        log(f"获取{code}实时行情失败: {e}", 'WARNING')
    
    return None

def get_sentiment_data(code: str, name: str) -> Optional[Dict]:
    """
    获取舆情数据 - 用于D18舆情热度 + D19舆情趋势
    
    返回: {
        'd18_score': 0~10,
        'd19_score': 0~10,
        'news_count': N,
        'detail': '...'
    }
    """
    try:
        # 调用TDX问达API获取新闻
        result = subprocess.run([
            'python', '-c', f'''
import json
import sys
sys.path.insert(0, ".")
try:
    from tdx_connector import wenda_news_query
    news = wenda_news_query(query="{name} {code}", symbol="{code}", top_k=10)
    print(json.dumps(news))
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    print("[]")
'''
        ], capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0 and result.stdout.strip():
            news_data = json.loads(result.stdout.strip())
            
            # 解析并计算舆情评分
            from V13_6_M62_SentimentCollector import parse_tdx_wenda_result, compute_sentiment_score
            
            sentiment_result = parse_tdx_wenda_result(news_data, code, name)
            news_list = sentiment_result.get('news_list', [])
            
            if news_list:
                sentiment_data = compute_sentiment_score(news_list)
                return sentiment_data
    except Exception as e:
        log(f"获取{code}舆情数据失败: {e}", 'WARNING')
    
    # 返回默认中性评分
    return {
        'd18_score': 5.0,
        'd19_score': 5.0,
        'news_count': 0,
        'detail': '舆情数据获取失败(默认中性)'
    }

def fetch_data_for_candidates(candidates: List[Dict], top_n: int = 30):
    """
    为Top N候选股获取实时数据和舆情数据
    """
    log(f"开始为Top {min(top_n, len(candidates))} 候选股获取实时数据...")
    
    for i, stock in enumerate(candidates[:top_n], 1):
        code = stock.get('code', '')
        name = stock.get('name', '')
        
        if not code:
            continue
        
        # 1. 获取实时行情 (D15)
        quote_cache = f'data/fullmarket_cache/quote_{code}.json'
        if not os.path.exists(quote_cache):
            quote_data = get_realtime_quote(code)
            if quote_data:
                with open(quote_cache, 'w', encoding='utf-8') as f:
                    json.dump(quote_data, f, ensure_ascii=False, indent=2)
                log(f"[{i}/{top_n}] {code} {name} 实时行情已缓存", 'SUCCESS')
            else:
                log(f"[{i}/{top_n}] {code} {name} 实时行情获取失败", 'WARNING')
        else:
            log(f"[{i}/{top_n}] {code} {name} 实时行情已存在", 'INFO')
        
        # 2. 获取舆情数据 (D18/D19)
        sentiment_cache = f'data/fullmarket_cache/sentiment_{code}.json'
        if not os.path.exists(sentiment_cache):
            sentiment_data = get_sentiment_data(code, name)
            if sentiment_data:
                with open(sentiment_cache, 'w', encoding='utf-8') as f:
                    json.dump(sentiment_data, f, ensure_ascii=False, indent=2)
                log(f"[{i}/{top_n}] {code} {name} 舆情数据已缓存 (D18={sentiment_data.get('d18_score', 5.0):.1f})", 'SUCCESS')
            else:
                log(f"[{i}/{top_n}] {code} {name} 舆情数据获取失败", 'WARNING')
        else:
            log(f"[{i}/{top_n}] {code} {name} 舆情数据已存在", 'INFO')
    
    log(f"实时数据获取完成", 'SUCCESS')

def run_t4_realtime_integration():
    """
    执行T4 14:15 全市场临门一脚 (V13.5.19 37维度五确认体系)
    """
    log("="*80)
    log("🎯 T4 14:15 全市场临门一脚 (V13.5.19 37维度 五确认体系)")
    log("="*80)
    
    # Step 1: 加载全市场缓存(包含三面数据)
    today = datetime.now().strftime('%Y%m%d')
    state_file = f'data/fullmarket_cache/state_{today}.json'
    
    if not os.path.exists(state_file):
        log(f"缓存文件不存在: {state_file}", 'ERROR')
        log("请先运行T0/T1/T3扫描生成缓存", 'ERROR')
        return
    
    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)
    
    # V13.5.13: 三面分类候选
    all_candidates = state.get('candidates', [])
    decline_candidates = [c for c in all_candidates if c.get('scan_type', 'decline') == 'decline']
    surge_candidates = [c for c in all_candidates if c.get('scan_type', 'surge') == 'surge']
    accum_candidates = [c for c in all_candidates if c.get('scan_type', 'accum') == 'accum']
    
    log(f"加载缓存: {len(all_candidates)} 总候选 | 🟥{len(decline_candidates)}跌幅 | 🟦{len(surge_candidates)}放量 | 🟩{len(accum_candidates)}蓄势", 'SUCCESS')
    
    # Step 2: 三面并行获取实时数据和舆情数据
    # 跌幅面: TOP30 (超跌反转, 传统重点)
    # 放量面: TOP20 (放量启动, D25新信号)
    # 蓄势面: TOP15 (趋势延续/低位蓄势, D26/D27新信号)
    fetch_data_for_candidates(decline_candidates, top_n=30)
    fetch_data_for_candidates(surge_candidates, top_n=20)
    fetch_data_for_candidates(accum_candidates, top_n=15)
    
    # Step 3: 执行M71 37维度增强(含D25-D36, 五确认体系)
    log("开始M71 37维度增强(V13.5.19 D34拆单+D35庄成本+D36委比异动+五确认)...", 'PROGRESS')
    
    try:
        # V13.5.19: 使用全局扫描器加载state数据, 确保M71可访问v132_score排序候选
        from V13_4_FullMarketMonitor import get_global_scanner, run_fullmarket_scan
        screener_file = f'data/fullmarket_cache/screener_t4_1415.json'
        if os.path.exists(screener_file):
            run_fullmarket_scan('14:15', screener_file)
        scanner = get_global_scanner()
        
        results = enhance_with_m71(
            scanner=scanner,
            period='14:15',
            top_n=60,  # V13.5.13: 扩大到60以覆盖三面
        )
        
        enhanced_count = results.get('enhanced', 0)
        reversals = results.get('reversals', [])
        five_confirm_candidates = results.get('five_confirm_candidates', [])
        log(f"M71增强完成: {enhanced_count} 只反转确认, {len(reversals)} 只候选, {len(five_confirm_candidates)} 只五确认候选", 'SUCCESS')
        
        print("\n" + "="*80)
        print("🎯 T4 14:15 尾盘选股结果 (V13.5.19 37维度 五确认体系)")
        print("="*80)
        
        strong_reversal = [s for s in reversals if s.get('grade') == 'STRONG_REVERSAL']
        reversal = [s for s in reversals if s.get('grade') == 'REVERSAL']
        
        print(f"\n📊 M71统计:")
        print(f"  总处理: {results.get('total_processed', 0)}")
        print(f"  ⚡ STRONG_REVERSAL (≥70分): {len(strong_reversal)}")
        print(f"  ✅ REVERSAL (45-69分): {len(reversal)}")
        print(f"  🔥 五确认候选 (D29+D31+D32+D33+D34 ≥4): {len(five_confirm_candidates)}")
        
        print(f"\n🏆 Top 10 圣杯候选 (M71综合排名):")
        for i, stock in enumerate(reversals[:10], 1):
            d25 = stock.get('d25_score', 0)
            d26 = stock.get('d26_score', 0)
            d27 = stock.get('d27_score', 0)
            d28 = stock.get('d28_score', 0)
            d29 = stock.get('d29_score', 0)
            d30 = stock.get('d30_score', 0)
            d31 = stock.get('d31_score', 0)
            d32 = stock.get('d32_score', 0)
            d33 = stock.get('d33_score', 0)
            d34 = stock.get('d34_score', 0)
            d35 = stock.get('d35_score', 0)
            d36 = stock.get('d36_score', 0)
            fc = stock.get('five_confirm_count', 0)
            print(f"  {i}. {stock['code']} {stock['name']} — M71={stock['m71_score']:.1f}分 [{stock['grade']}] 五确认={fc}/5")
            print(f"     D25={d25:.1f} D26={d26:.1f} D27={d27:.1f} D28={d28:.1f} D29={d29:.1f} D30={d30:.1f} D31={d31:.1f} D32={d32:.1f} D33={d33:.1f} D34={d34:.1f} D35={d35:.1f} D36={d36:.1f}")
        
        # 五确认候选专区
        if five_confirm_candidates:
            print(f"\n🔥 五确认/四确认强势候选 (优先观察):")
            for i, stock in enumerate(five_confirm_candidates[:5], 1):
                print(f"  {i}. {stock['code']} {stock['name']} — M71={stock['m71_score']:.1f}分 五确认={stock['five_confirm_count']}/5 [{stock['grade']}]")
        
        # Step 5: 推送
        log("开始推送...", 'PROGRESS')
        try:
            from V13_4_HolyGrailPusher import push_holy_grail
            
            push_data = {
                'period': '14:15',
                'strong_reversal': strong_reversal[:5],
                'reversal': reversal[:5],
                'five_confirm_candidates': five_confirm_candidates[:5],
                'timestamp': datetime.now().isoformat(),
                'version': 'V13.5.19',
                'realtime_enabled': True,
                'three_face_enabled': True,
                'five_confirm_enabled': True,
            }
            
            push_holy_grail(push_data)
            log("推送完成", 'SUCCESS')
        except Exception as e:
            log(f"推送失败: {e}", 'WARNING')
        
        # 保存结果
        output_file = f'data/fullmarket_cache/t4_realtime_{today}.json'
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        log(f"结果已保存: {output_file}", 'SUCCESS')
        
    except Exception as e:
        log(f"M71增强失败: {e}", 'ERROR')
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    run_t4_realtime_integration()
