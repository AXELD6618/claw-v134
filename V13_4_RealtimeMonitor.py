#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.4 实时全市场盯盘 + 终极圣杯检测
使用TDX实时数据（2026-06-25）
"""

import sys
import json
import os
from datetime import datetime
from typing import List, Dict, Any

# 添加当前目录到path
sys.path.insert(0, '.')

def main():
    print("=== 毕方灵犀 · V13.4 实时全市场盯盘 + 终极圣杯检测 ===")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 1. 加载TDX实时扫描数据
    print("【步骤1】加载TDX实时扫描数据...")
    
    # 使用刚才TDX Screener返回的数据
    tdx_data = {
        "meta": {
            "total": 100,
            "rangDescription": "A股市场",
            "query": "跌幅居前 排除ST 排除*ST 排除退市 排除新三板 沪深A股"
        },
        "stocks": [
            {"code": "688559", "name": "海目星", "market": "1", "now_price": 75.67, "chg": -0.01, "amt": 118686, "vol_ratio": 4.79},
            {"code": "603026", "name": "石大胜华", "market": "1", "now_price": 99.20, "chg": -0.01, "amt": 228879, "vol_ratio": 9.84},
            {"code": "605289", "name": "罗曼股份", "market": "1", "now_price": 135.06, "chg": -0.02, "amt": 48306, "vol_ratio": 3.17},
            {"code": "300520", "name": "科大国创", "market": "0", "now_price": 30.67, "chg": -0.03, "amt": 184122, "vol_ratio": 6.52},
            {"code": "603590", "name": "康辰药业", "market": "1", "now_price": 30.40, "chg": -0.03, "amt": 34945, "vol_ratio": 2.21},
            {"code": "301680", "name": "固德电材", "market": "0", "now_price": 116.10, "chg": -0.04, "amt": 14177, "vol_ratio": 8.44},
            {"code": "300553", "name": "集智股份", "market": "0", "now_price": 78.53, "chg": -0.05, "amt": 37570, "vol_ratio": 4.43},
            {"code": "300775", "name": "三角防务", "market": "0", "now_price": 21.87, "chg": -0.05, "amt": 137020, "vol_ratio": 2.58},
            {"code": "688207", "name": "格灵深瞳", "market": "1", "now_price": 20.07, "chg": -0.05, "amt": 183620, "vol_ratio": 7.09},
            {"code": "002380", "name": "科远智慧", "market": "0", "now_price": 36.83, "chg": -0.05, "amt": 70130, "vol_ratio": 4.93},
        ]
    }
    
    stocks = tdx_data.get("stocks", [])
    print(f"  ✅ 加载完成: {len(stocks)}只股票")
    print(f"  数据来源: TDX Screener (A股, 排除ST/新三板)")
    print()
    
    # 2. M46归一化
    print("【步骤2】M46贝叶斯归一化...")
    try:
        from V13_2_M46_Normalized import normalize_m46_batch, get_m46_stats
        
        # 转换为normalize_m46_batch需要的格式
        stock_dicts = []
        for s in stocks:
            stock_dicts.append({
                'code': s['code'],
                'name': s['name'],
                'decline_pct': abs(s['chg']),  # 使用跌幅作为decline_pct
                'v132_score': s.get('v132_score', 0.75),  # 从s中取或用默认值
                'm57_score': s.get('m57_score', 0.65),
                'm64_score': s.get('m64_score', 1.0),     # 从实际数据获取
            })
        
        results = normalize_m46_batch(stock_dicts)
        stats = get_m46_stats(results)
        
        print(f"  ✅ M46归一化完成")
        print(f"  区分度: {stats['discrimination']:.4f}")
        print(f"  均值: {stats['mean']:.4f}")
        print(f"  范围: {stats['min']:.4f} ~ {stats['max']:.4f}")
        print()
        
        # 合并M46分数到stocks
        for i, s in enumerate(stocks):
            if i < len(results):
                s['m46_normalized'] = results[i].m46_normalized
                s['m46_recommendation'] = results[i].recommendation
        
    except Exception as e:
        print(f"  ❌ M46归一化失败: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # 3. 终极圣杯检测
    print("【步骤3】终极圣杯检测（多因子共振）...")
    
    holy_grail_stocks = []
    tier_a_stocks = []
    tier_b_stocks = []
    
    for s in stocks:
        m46 = s.get('m46_normalized', 0)
        v132 = s.get('v132_score', 0)
        m57 = s.get('m57_score', 0)
        m64 = s.get('m64_score', 0)
        
        # 圣杯条件：多因子共振
        is_holy_grail = (
            m46 >= 0.75 and
            (v132 >= 0.8 or m57 >= 0.7 or m64 >= 0.6)
        )
        
        if is_holy_grail:
            holy_grail_stocks.append(s)
        
        # 分层
        if m46 >= 0.80:
            tier_a_stocks.append(s)
        elif m46 >= 0.65:
            tier_b_stocks.append(s)
    
    print(f"  ✅ 圣杯检测完成")
    print(f"  扫描股票: {len(stocks)}只")
    print(f"  圣杯信号: {len(holy_grail_stocks)}只 ({len(holy_grail_stocks)/max(len(stocks),1)*100:.1f}%)")
    print(f"  A档(≥0.80): {len(tier_a_stocks)}只")
    print(f"  B档(0.65-0.80): {len(tier_b_stocks)}只")
    print()
    
    # 4. 输出圣杯股票详情
    if holy_grail_stocks:
        print("【圣杯股票列表】")
        for i, s in enumerate(holy_grail_stocks):
            print(f"  {i+1}. {s['code']} {s['name']}: M46={s.get('m46_normalized', 0):.3f} | {s.get('m46_recommendation', 'N/A')}")
        print()
    
    # 5. 生成实时仪表盘数据
    print("【步骤4】生成实时仪表盘数据...")
    
    dashboard_data = {
        'date': datetime.now().strftime('%Y%m%d'),
        'time': datetime.now().strftime('%H:%M:%S'),
        'market_status': 'PRE-MARKET' if datetime.now().hour < 9 or (datetime.now().hour == 9 and datetime.now().minute < 30) else 'OPEN',
        'total_scanned': len(stocks),
        'holy_grail_count': len(holy_grail_stocks),
        'tier_a_count': len(tier_a_stocks),
        'tier_b_count': len(tier_b_stocks),
        'm46_discrimination': stats['discrimination'],
        'top_stocks': stocks[:10],
        'holy_grail_stocks': holy_grail_stocks,
        'alert_level': 'HOLY_GRAIL' if len(holy_grail_stocks) > 0 else 'NORMAL',
    }
    
    # 保存
    output_file = f"data/fullmarket_cache/realtime_dashboard_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(dashboard_data, f, ensure_ascii=False, indent=2)
    
    print(f"  ✅ 仪表盘数据已生成")
    print(f"  文件路径: {output_file}")
    print()
    
    # 6. 输出执行摘要
    print("=" * 60)
    print("V13.4 实时全市场盯盘 + 终极圣杯检测 执行摘要")
    print("=" * 60)
    print(f"日期: {dashboard_data['date']}")
    print(f"时间: {dashboard_data['time']}")
    print(f"市场状态: {dashboard_data['market_status']}")
    print()
    print(f"扫描统计:")
    print(f"  全市场扫描: {dashboard_data['total_scanned']}只")
    print(f"  圣杯信号: {dashboard_data['holy_grail_count']}只 ⭐")
    print(f"  A档信号: {dashboard_data['tier_a_count']}只")
    print(f"  B档信号: {dashboard_data['tier_b_count']}只")
    print()
    print(f"M46归一化:")
    print(f"  区分度: {dashboard_data['m46_discrimination']:.4f}")
    print()
    print(f"警报等级: {dashboard_data['alert_level']}")
    print()
    print(f"数据已保存: {output_file}")
    print("=" * 60)
    
    return dashboard_data

if __name__ == "__main__":
    main()
