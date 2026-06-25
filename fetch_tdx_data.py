#!/usr/bin/env python3
"""
批量获取TDX数据 for V13.1流水线
获取10只候选股票的完整数据：quote + kline(60d) + indicator
"""

import json
import os
import sys
from datetime import datetime

# 10只候选股票列表
CANDIDATES = [
    {"code": "920180", "name": "爱得科技", "setcode": "2"},
    {"code": "920017", "name": "星昊医药", "setcode": "2"},
    {"code": "301633", "name": "港迪技术", "setcode": "0"},
    {"code": "920030", "name": "德众汽车", "setcode": "2"},
    {"code": "603339", "name": "四方科技", "setcode": "1"},
    {"code": "688237", "name": "超卓航科", "setcode": "1"},
    {"code": "002546", "name": "新联电子", "setcode": "0"},
    {"code": "600717", "name": "天津港", "setcode": "1"},
    {"code": "002312", "name": "川发龙蟒", "setcode": "0"},
    {"code": "002907", "name": "华森制药", "setcode": "0"},
]

def load_json(filename):
    """加载JSON文件"""
    if os.path.exists(filename):
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_json(data, filename):
    """保存JSON文件"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 已保存: {filename}")

def main():
    print("=" * 60)
    print("TDX数据获取脚本")
    print("=" * 60)
    print(f"目标: 获取{len(CANDIDATES)}只候选股票的完整TDX数据")
    print(f"数据目录: {os.path.abspath('.')}")
    print()
    
    # 创建输出目录
    output_dir = "tdx_data"
    os.makedirs(output_dir, exist_ok=True)
    
    # 加载已获取的行情数据（从之前的工具调用结果）
    # 注意：这个函数需要通过MCP工具获取数据，不能直接运行
    # 这里只是一个框架脚本
    
    print("⚠️ 注意: 此脚本需要通过WorkBuddy MCP工具获取数据")
    print("请使用WorkBuddy环境运行此脚本，或通过DeferExecuteTool调用TDX MCP工具")
    print()
    print("手动获取数据步骤:")
    print("1. 对每只股票调用 tdx_quotes (实时行情)")
    print("2. 对每只股票调用 tdx_kline (日K线, period=4, wantNum=60)")
    print("3. 对每只股票调用 tdx_indicator_select (财务指标)")
    print("4. 组装为 tdx_realtime_input.json 标准格式")
    print()
    
    # 输出标准格式示例
    example_format = {
        "date": "2026-06-23",
        "time": "15:00:00",
        "source": "TDX_MCP",
        "stocks": [
            {
                "code": "600519",
                "setcode": "1",
                "name": "贵州茅台",
                "quote": { /* tdx_quotes 返回结果 */ },
                "kline": { /* tdx_kline period=4 返回结果 */ },
                "indicator": { /* tdx_indicator_select 返回结果 */ }
            }
        ]
    }
    
    print("标准输出格式 (tdx_realtime_input.json):")
    print(json.dumps(example_format, ensure_ascii=False, indent=2))
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
