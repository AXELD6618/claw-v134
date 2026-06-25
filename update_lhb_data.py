#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新 LHB 数据文件（添加买卖详情）

将 branch=3 获取的买卖金额数据合并到 LHB 数据文件中
"""

import json
import os

print("=" * 60)
print("更新 LHB 数据文件（添加买卖详情）")
print("=" * 60)

# 读取基础 LHB 数据（branch=0）
base_file = "data/lhb_20260624.json"
if not os.path.exists(base_file):
    print(f"❌ 基础 LHB 文件不存在: {base_file}")
    exit(1)

with open(base_file, 'r', encoding='utf-8') as f:
    base_data = json.load(f)

print(f"\n📖 读取基础 LHB 数据: {base_file}")
print(f"  股票数量: {len(base_data.get('stocks', []))}")

# branch=3 数据（从 TDX API 获取）
# 这里使用之前 API 返回的数据
branch3_data = [
    {"code": "002421", "name": "达实智能", "buy_amount": 277679.81, "sell_amount": 297576.99, "net_amount": -19897.18},
    {"code": "920161", "name": "龙辰科技", "buy_amount": 41163.25, "sell_amount": 43680.44, "net_amount": -2517.19},
    {"code": "002579", "name": "中京电子", "buy_amount": 170440.74, "sell_amount": 162365.91, "net_amount": 8074.82},
    {"code": "920725", "name": "惠丰钻石", "buy_amount": 27744.95, "sell_amount": 25070.25, "net_amount": 2674.70},
    {"code": "301563", "name": "云汉芯城", "buy_amount": 170424.20, "sell_amount": 189932.04, "net_amount": -19507.84},
    {"code": "920083", "name": "金戈新材", "buy_amount": 13716.04, "sell_amount": 11840.89, "net_amount": 1875.15},
    {"code": "001257", "name": "盛龙股份", "buy_amount": 206061.57, "sell_amount": 194540.84, "net_amount": 11520.73},
    {"code": "301013", "name": "利和兴", "buy_amount": 421782.05, "sell_amount": 371008.18, "net_amount": 50773.87},
    {"code": "301313", "name": "凡拓数创", "buy_amount": 183792.52, "sell_amount": 174618.02, "net_amount": 9174.50},
    {"code": "002354", "name": "天娱数科", "buy_amount": 126318.06, "sell_amount": 105269.26, "net_amount": 21048.79},
]

# 创建映射
branch3_map = {item['code']: item for item in branch3_data}

# 更新基础数据
updated = 0
for stock in base_data.get('stocks', []):
    code = stock.get('code')
    if code in branch3_map:
        detail = branch3_map[code]
        stock['buy_amount'] = detail['buy_amount']
        stock['sell_amount'] = detail['sell_amount']
        stock['net_amount'] = detail['net_amount']
        updated += 1

print(f"\n✅ 更新完成: {updated} 只股票添加了买卖详情")

# 保存更新后的数据
output_file = "data/lhb_with_details_20260624.json"
with open(output_file, 'w', encoding='utf-8') as f:
    json.dump(base_data, f, ensure_ascii=False, indent=2)

print(f"\n💾 数据已保存: {output_file}")
print(f"  文件大小: {os.path.getsize(output_file)} 字节")

# 验证数据
print(f"\n🔍 验证数据（前3只）:")
for i, stock in enumerate(base_data.get('stocks', [])[:3]):
    print(f"  {i+1}. {stock.get('code')} {stock.get('name')}")
    if 'buy_amount' in stock:
        print(f"     买入额: {stock['buy_amount']:.2f}万 | 卖出额: {stock['sell_amount']:.2f}万 | 净额: {stock['net_amount']:.2f}万")
    else:
        print(f"     ⚠️ 无买卖详情")

print("\n" + "=" * 60)
print("更新完成")
print("=" * 60)
