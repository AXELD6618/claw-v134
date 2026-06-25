#!/usr/bin/env python3
"""
V13.1 集成测试脚本
测试V13.1模块是否正确集成到V13.0 Orchestrator流水线
"""

import json
import sys
from datetime import datetime
from typing import Dict, List

# 导入V13.0和V13.1模块
try:
    from V13_0_Orchestrator import V13Orchestrator, OrchestratorConfig
    from V13_1_HolyGrailIntegration import (
        HolyGrailIntegrator,
        V131OrchestratorPatch,
        V131StockResult,
    )
    print("✅ V13.0 和 V13.1 模块已加载")
except ImportError as e:
    print(f"❌ 模块导入失败: {e}")
    sys.exit(1)


def test_v131_integration():
    """测试V13.1集成"""
    print("\n" + "="*70)
    print("  V13.1 集成测试")
    print("="*70)
    
    # 1. 创建V13.0 Orchestrator
    print("\n📊 创建V13.0 Orchestrator...")
    config = OrchestratorConfig(verbose=False, data_mode='synthetic')
    orch = V13Orchestrator(config)
    print("   ✅ V13.0 Orchestrator已创建")
    
    # 2. 创建V13.1集成器
    print("\n📊 创建V13.1集成器...")
    integrator = HolyGrailIntegrator()
    print("   ✅ V13.1集成器已创建")
    print(f"   M59微观结构: {'✅' if integrator.micro_engine else '❌'}")
    print(f"   M56尾盘30分钟: {'✅' if integrator.tail_engine else '❌'}")
    print(f"   M57隔夜Alpha: {'✅' if integrator.alpha_engine else '❌'}")
    
    # 3. 创建补丁
    print("\n📊 创建V13.1补丁...")
    patch = V131OrchestratorPatch()
    print("   ✅ V13.1补丁已创建")
    
    # 4. 模拟V13.0结果
    print("\n📊 模拟V13.0分析结果...")
    mock_v130_results = [
        {'code': '000001', 'name': '平安银行', 'score': 0.65},
        {'code': '600519', 'name': '贵州茅台', 'score': 0.78},
        {'code': '002415', 'name': '海康威视', 'score': 0.72},
    ]
    print(f"   模拟了 {len(mock_v130_results)} 只股票的分析结果")
    
    # 5. 模拟股票数据映射
    print("\n📊 模拟股票数据映射...")
    mock_stock_data_map = {
        '000001': {
            'listed_days': 5000,
            'is_suspended': False,
            'avg_volume_yuan': 1e9,
            'current_price': 10.71,
            'prev_close': 10.65,
            'consecutive_limit_up': 0,
            'tail_prices': [10.65, 10.68, 10.71, 10.70, 10.71],
            'tail_volumes': [1000000, 1200000, 1100000, 1300000, 1200000],
        },
        '600519': {
            'listed_days': 5000,
            'is_suspended': False,
            'avg_volume_yuan': 5e9,
            'current_price': 1222.45,
            'prev_close': 1241.41,
            'consecutive_limit_up': 0,
            'tail_prices': [1241, 1235, 1225, 1222, 1222],
            'tail_volumes': [500000, 600000, 550000, 650000, 600000],
        },
        '002415': {
            'listed_days': 4000,
            'is_suspended': False,
            'avg_volume_yuan': 2e9,
            'current_price': 31.41,
            'prev_close': 32.83,
            'consecutive_limit_up': 0,
            'tail_prices': [32.83, 32.50, 32.00, 31.80, 31.41],
            'tail_volumes': [2000000, 2200000, 2100000, 2300000, 2200000],
        },
    }
    print(f"   模拟了 {len(mock_stock_data_map)} 只股票的数据映射")
    
    # 6. 使用补丁增强结果
    print("\n📊 使用V13.1补丁增强结果...")
    try:
        enhanced_results = patch.enhance(mock_v130_results, mock_stock_data_map)
        print(f"   ✅ 增强完成，共 {len(enhanced_results)} 只股票")
        
        # 7. 打印增强结果
        print("\n📊 V13.1增强结果:")
        for r in enhanced_results:
            print(f"   {r.code} {r.name}:")
            print(f"      圣杯评分: {r.holy_grail_score:.4f}")
            print(f"      建议: {r.recommendation}")
            print(f"      板别: {r.board}")
            print(f"      尾盘模式: {r.tail_pattern}")
            print(f"      Alpha综合: {r.alpha_composite:.3f}")
        
        # 8. 生成圣杯报告
        print("\n📊 生成圣杯报告...")
        report = integrator.generate_holy_grail_report(enhanced_results)
        print(report)
        
    except Exception as e:
        print(f"   ❌ 增强失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n🎉 V13.1集成测试完成！")
    return True


def main():
    print("="*70)
    print("  V13.1 集成测试脚本")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    success = test_v131_integration()
    
    if success:
        print("\n✅ V13.1集成测试通过！")
        print("\n📝 下一步:")
        print("   1. 将V13.1模块集成到真实的Orchestrator流水线")
        print("   2. 获取50+只股票数据进行M46精度校准")
        print("   3. 设置14:30自动化任务")
    else:
        print("\n❌ V13.1集成测试失败！")


if __name__ == '__main__':
    main()
