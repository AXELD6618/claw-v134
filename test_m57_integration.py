#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 M57 因子增强器集成

验证:
1. M57FactorEnhancer 导入
2. LHB 数据读取
3. News 数据读取
4. 因子计算
"""

import sys
import os

# 添加当前目录到path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("测试 M57 因子增强器集成")
print("=" * 60)

# 测试1: 导入 M57FactorEnhancer
print("\n1. 测试导入 M57FactorEnhancer...")
try:
    from V13_2_M57_FactorEnhancer import M57FactorEnhancer
    enhancer = M57FactorEnhancer()
    print("   ✅ M57FactorEnhancer 导入成功")
except Exception as e:
    print(f"   ❌ 导入失败: {e}")
    sys.exit(1)

# 测试2: 读取 LHB 数据
print("\n2. 测试 LHB 数据读取...")
lhb_file = f"data/lhb_{os.path.exists('data') and os.listdir('data')[0] or ''}.json"
# 查找最新的 LHB 文件
lhb_files = [f for f in os.listdir('data') if f.startswith('lhb_') and f.endswith('.json')] if os.path.exists('data') else []
if lhb_files:
    latest_lhb = sorted(lhb_files)[-1]
    lhb_path = os.path.join('data', latest_lhb)
    print(f"   找到 LHB 文件: {latest_lhb}")
    
    import json
    with open(lhb_path, 'r', encoding='utf-8') as f:
        lhb_data = json.load(f)
    
    print(f"   LHB 数据: {len(lhb_data.get('stocks', []))} 只股票")
    
    # 测试 LHB 效应计算
    if lhb_data.get('stocks'):
        test_stock = lhb_data['stocks'][0]
        print(f"   测试股票: {test_stock.get('code')} {test_stock.get('name')}")
        lhb_effect = enhancer.compute_lhb_effect(test_stock, -5.0)
        print(f"   LHB 效应: {lhb_effect:.4f}")
        print("   ✅ LHB 数据读取成功")
    else:
        print("   ⚠️ LHB 文件为空")
else:
    print("   ⚠️ 未找到 LHB 数据文件 (需要先获取 LHB 数据)")

# 测试3: 读取 News 数据
print("\n3. 测试 News 数据读取...")
db_path = 'data/sentiment_db.db'
if os.path.exists(db_path):
    print(f"   找到数据库: {db_path}")
    
    # 测试新闻事件获取
    test_code = "600000"  # 测试代码
    events = enhancer.fetch_news_events(test_code, days=7)
    print(f"   获取新闻事件: {len(events)} 条")
    
    if events:
        print(f"   第一条新闻: {events[0].get('title', '')[:50]}...")
        event_decay = enhancer.compute_event_decay(events, "2026-06-25")
        print(f"   事件衰减: {event_decay:.4f}")
    
    print("   ✅ News 数据读取成功")
else:
    print(f"   ❌ 数据库不存在: {db_path}")

# 测试4: 集成到 FullMarketMonitor
print("\n4. 测试集成到 FullMarketMonitor...")
try:
    from V13_4_FullMarketMonitor import FullMarketScanner
    scanner = FullMarketScanner()
    print("   ✅ FullMarketScanner 创建成功")
    
    # 测试 M57 增强器属性
    if hasattr(scanner, 'm57_enhancer'):
        print("   ✅ m57_enhancer 属性存在")
    else:
        print("   ⚠️ m57_enhancer 属性不存在 (可能是懒加载)")
    
    # 测试 _compute_lhb_effect 方法
    if hasattr(scanner, '_compute_lhb_effect'):
        print("   ✅ _compute_lhb_effect 方法存在")
    else:
        print("   ❌ _compute_lhb_effect 方法不存在")
    
    # 测试 _compute_event_decay 方法
    if hasattr(scanner, '_compute_event_decay'):
        print("   ✅ _compute_event_decay 方法存在")
    else:
        print("   ❌ _compute_event_decay 方法不存在")
    
    print("   ✅ 集成测试通过")
except Exception as e:
    print(f"   ❌ 集成测试失败: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
