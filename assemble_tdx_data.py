#!/usr/bin/env python3
"""
从TDX MCP工具调用结果组装 tdx_realtime_input.json
需要手动将从工具调用结果中获取的数据填充到此处
"""

import json
import os

# 10只候选股票的基础信息
CANDIDATES_INFO = [
    {"code": "920180", "name": "爱得科技", "setcode": "2", "market": "北交所"},
    {"code": "920017", "name": "星昊医药", "setcode": "2", "market": "北交所"},
    {"code": "301633", "name": "港迪技术", "setcode": "0", "market": "深市"},
    {"code": "920030", "name": "德众汽车", "setcode": "2", "market": "北交所"},
    {"code": "603339", "name": "四方科技", "setcode": "1", "market": "沪市"},
    {"code": "688237", "name": "超卓航科", "setcode": "1", "market": "沪市"},
    {"code": "002546", "name": "新联电子", "setcode": "0", "market": "深市"},
    {"code": "600717", "name": "天津港", "setcode": "1", "market": "沪市"},
    {"code": "002312", "name": "川发龙蟒", "setcode": "0", "market": "深市"},
    {"code": "002907", "name": "华森制药", "setcode": "0", "market": "深市"},
]

def build_tdx_input():
    """构建TDX实时输入数据"""
    
    # 注意：这里需要填充实际的TDX工具调用结果
    # 由于数据量很大，这里提供一个框架
    
    tdx_input = {
        "date": "2026-06-23",
        "time": "15:00:00",
        "source": "TDX_MCP",
        "candidates": []
    }
    
    # 为每只股票构建candidate数据
    # 实际使用时需要从TDX工具调用结果中复制数据
    for info in CANDIDATES_INFO:
        candidate = {
            "code": info["code"],
            "name": info["name"],
            "quote": {},  # 从 tdx_quotes 结果填充
            "kline": {},   # 从 tdx_kline period=4 结果填充
            "indicator": {},  # 从 tdx_indicator_select 结果填充
            "sector": "未知"
        }
        tdx_input["candidates"].append(candidate)
    
    # 保存到文件
    output_file = "tdx_realtime_input.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(tdx_input, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 已创建 {output_file}")
    print(f"   包含 {len(tdx_input['candidates'])} 只候选股票")
    print()
    print("⚠️ 注意: 需要手动填充 quote/kline/indicator 的实际数据")
    print("   请从TDX MCP工具调用结果中复制数据并填充到对应字段")

if __name__ == "__main__":
    build_tdx_input()
