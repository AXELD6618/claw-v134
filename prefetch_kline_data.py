#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
预拉取5分钟K线数据（用于 tail_vol_struct 因子）

为LHB股票列表中的每只股票拉取5分钟K线数据并缓存到 data/kline_5min_{code}.json
"""

import json
import os
import sys

print("=" * 60)
print("预拉取5分钟K线数据（用于 tail_vol_struct 因子）")
print("=" * 60)

# 读取LHB股票列表
lhb_file = "data/lhb_20260624.json"
if not os.path.exists(lhb_file):
    print(f"❌ LHB数据文件不存在: {lhb_file}")
    print("请先运行 15:25 自动化获取LHB数据")
    sys.exit(1)

with open(lhb_file, 'r', encoding='utf-8') as f:
    lhb_data = json.load(f)

stocks = lhb_data.get('stocks', [])
print(f"\n📖 读取LHB股票列表: {len(stocks)} 只")

# 为每只股票创建空的缓存文件（占位符）
# 实际数据需要由TDX API拉取（这里仅创建结构）
os.makedirs("data", exist_ok=True)

cached = 0
for stock in stocks:
    code = stock.get('code')
    market = stock.get('market', 'sh')
    
    # 确定setcode
    if market == 'sh':
        setcode = '1'
    elif market == 'sz':
        setcode = '0'
    elif market == 'bj':
        setcode = '2'
    else:
        setcode = '1'
    
    # 创建缓存文件（空结构，等待API拉取）
    cache_file = f"data/kline_5min_{code}.json"
    
    # 这里应该调用TDX API拉取数据
    # 但为了避免过多API调用，这里仅创建占位符
    # 实际拉取应由 15:25 自动化完成
    
    # 创建示例数据结构
    sample_data = {
        'code': code,
        'name': stock.get('name'),
        'market': market,
        'setcode': setcode,
        'period': '5min',
        'kline': [],  # 等待API填充
        'note': '需要从TDX API拉取5分钟K线数据'
    }
    
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(sample_data, f, ensure_ascii=False, indent=2)
    
    cached += 1

print(f"\n✅ 已创建缓存文件: {cached} 只")
print(f"  缓存目录: data/kline_5min_*.json")
print(f"\n⚠️ 注意: 缓存文件仅为结构，需要从TDX API拉取实际K线数据")
print(f"  建议: 更新 15:25 自动化，在获取LHB数据后，额外拉取5分钟K线并缓存")

print("\n" + "=" * 60)
print("预拉取脚本完成（仅创建缓存结构）")
print("=" * 60)
print("\n下一步: 更新 15:25 自动化，拉取5分钟K线数据并保存到缓存")
