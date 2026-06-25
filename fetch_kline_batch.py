#!/usr/bin/env python3
"""
批量获取股票K线数据脚本
用于M46校准和回测
"""

import json
import time
from typing import List, Dict

# 股票列表（从前一个tdx_screener结果）
TARGET_STOCKS = [
    ('300835', '0', '龙磁科技'),
    ('300665', '0', '飞鹿股份'),
    ('688291', '1', '金橙子'),
    ('300085', '0', '银之杰'),
    ('301211', '0', '亨迪药业'),
    ('300961', '0', '深水海纳'),
    ('300149', '0', '睿智医药'),
    ('688179', '1', '阿拉丁'),
    ('301237', '0', '和顺科技'),
    ('300077', '0', '国民技术'),
]


def fetch_kline_batch(stocks: List[tuple], output_dir: str = '.'):
    """批量获取K线数据"""
    print(f"开始批量获取 {len(stocks)} 只股票的K线数据...")
    
    results = []
    for code, setcode, name in stocks:
        print(f"\n正在获取 {code} {name}...")
        # 这里需要调用 tdx_kline
        # 由于无法在这里直接调用，我们生成一个脚本供用户执行
        results.append({
            'code': code,
            'setcode': setcode,
            'name': name,
            'status': 'pending',
        })
    
    print(f"\n✅ 批量获取脚本已生成")
    return results


def generate_fetch_script(output_file: str = 'fetch_kline_batch.py'):
    """生成批量获取K线数据的脚本"""
    script = '''#!/usr/bin/env python3
"""
自动批量获取K线数据
需要在有TDX连接器的环境中运行
"""

import json
import sys
import os

# 检查TDX连接器是否可用
try:
    # 这里需要实际的TDX连接器调用
    print("⚠️ 此脚本需要在WorkBuddy环境中运行")
    print("   因为需要调用 mcp__tdx-connector__tdx_kline 工具")
    sys.exit(1)
except Exception as e:
    print(f"❌ 错误: {e}")
    sys.exit(1)

TARGET_STOCKS = [
    ('300835', '0', '龙磁科技'),
    ('300665', '0', '飞鹿股份'),
    ('688291', '1', '金橙子'),
    ('300085', '0', '银之杰'),
    ('301211', '0', '亨迪药业'),
]

print(f"开始获取 {len(TARGET_STOCKS)} 只股票的K线数据...")

# 这里需要循环调用TDX接口
# 由于TDX连接器是通过工具调用的，无法在普通Python脚本中使用
# 建议在WorkBuddy对话中直接使用工具调用

print("\n💡 建议：")
print("   在WorkBuddy对话中，使用以下命令逐个获取：")
for code, setcode, name in TARGET_STOCKS:
    print(f"   tdx_kline code={code} setcode={setcode} period=4 wantNum=60")
'''
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(script)
    
    print(f"✅ 已生成脚本: {output_file}")
    print(f"   注意：由于TDX连接器限制，建议手动在对话中调用")


if __name__ == '__main__':
    generate_fetch_script()
    print("\n📝 下一步：")
    print("   1. 在WorkBuddy对话中使用 tdx_kline 工具获取更多股票数据")
    print("   2. 或者运行 python fetch_kline_batch.py 查看说明")
