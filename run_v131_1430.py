#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.1 14:30 尾盘实战脚本 — run_v131_1430.py                        ║
║  =====================================================                ║
║  集成M56+M57+M59的V13.1增强版尾盘选股实战脚本                        ║
║                                                                      ║
║  功能：                                                                ║
║  1. 加载TDX实盘数据 或 使用合成数据                                   ║
║  2. 运行V13.1圣杯增强分析 (M56+M57+M59+V13.0)                       ║
║  3. 输出圣杯选股报告                                                   ║
║  4. 可选：推送微信提醒                                                ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import time
import argparse
from datetime import datetime
from typing import Dict, List, Any, Optional

# 导入V13.0 Orchestrator
try:
    from V13_0_Orchestrator import V13Orchestrator, OrchestratorConfig
except ImportError:
    print("❌ 无法导入V13_0_Orchestrator，请确保文件存在")
    exit(1)

# 导入V13.1集成层
try:
    from V13_1_HolyGrailIntegration import (
        HolyGrailIntegrator,
        V131OrchestratorPatch,
        V131StockResult,
    )
    V131_AVAILABLE = True
    print("✅ V13.1 圣杯集成层已加载")
except ImportError as e:
    print(f"⚠️ V13.1 模块导入失败: {e}")
    print("   将以V13.0模式运行")
    V131_AVAILABLE = False


def load_tdx_data(file_path: str) -> Optional[Dict]:
    """加载TDX实盘数据文件"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"✅ 已加载TDX数据: {file_path} ({len(data.get('stocks', []))}只股票)")
        return data
    except FileNotFoundError:
        print(f"❌ 文件不存在: {file_path}")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}")
        return None
    except Exception as e:
        print(f"❌ 加载失败: {e}")
        return None


def run_v130_analysis(tdx_data: Dict) -> List[Dict]:
    """运行V13.0分析"""
    config = OrchestratorConfig(
        verbose=True,
        data_mode='tdx_real',
    )
    orch = V13Orchestrator(config)

    # 注入TDX数据
    report = orch.inject_tdx_data_and_run(tdx_data)

    # 提取结果
    results = report.get('results', [])
    print(f"✅ V13.0分析完成: {len(results)}只股票")
    return results


def enhance_with_v131(
    v130_results: List[Dict],
    stock_data_map: Dict[str, Dict],
) -> List[V131StockResult]:
    """使用V13.1增强分析结果"""
    if not V131_AVAILABLE:
        print("⚠️ V13.1不可用，跳过增强")
        return []

    patch = V131OrchestratorPatch()
    enhanced = patch.enhance(v130_results, stock_data_map)
    print(f"✅ V13.1增强完成: {len(enhanced)}只股票")
    return enhanced


def generate_reports(
    v130_results: List[Dict],
    v131_results: List[V131StockResult],
    output_dir: str = '.',
):
    """生成分析报告"""
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # V13.0报告
    v130_report = {
        'timestamp': timestamp,
        'version': 'V13.0',
        'results': v130_results,
    }
    v130_file = f"{output_dir}/v130_report_{timestamp}.json"
    with open(v130_file, 'w', encoding='utf-8') as f:
        json.dump(v130_report, f, ensure_ascii=False, indent=2)
    print(f"✅ V13.0报告已保存: {v130_file}")

    # V13.1报告
    if v131_results:
        integrator = HolyGrailIntegrator()
        holy_grail_report = integrator.generate_holy_grail_report(v131_results)

        # 保存文本报告
        v131_txt_file = f"{output_dir}/v131_holy_grail_{timestamp}.txt"
        with open(v131_txt_file, 'w', encoding='utf-8') as f:
            f.write(holy_grail_report)
        print(f"✅ V13.1圣杯报告已保存: {v131_txt_file}")

        # 保存JSON报告
        v131_json = {
            'timestamp': timestamp,
            'version': 'V13.1',
            'holy_grail_score mean': sum(r.holy_grail_score for r in v131_results) / len(v131_results),
            'results': [
                {
                    'code': r.code,
                    'name': r.name,
                    'holy_grail_score': r.holy_grail_score,
                    'recommendation': r.recommendation,
                    'board': r.board,
                    'tail_pattern': r.tail_pattern,
                    'tail_grade': r.tail_grade,
                    'alpha_composite': r.alpha_composite,
                    't1_return_forecast': r.t1_return_forecast,
                }
                for r in v131_results
            ],
        }
        v131_json_file = f"{output_dir}/v131_report_{timestamp}.json"
        with open(v131_json_file, 'w', encoding='utf-8') as f:
            json.dump(v131_json, f, ensure_ascii=False, indent=2)
        print(f"✅ V13.1 JSON报告已保存: {v131_json_file}")

        # 打印圣杯报告
        print("\n" + holy_grail_report)


def main():
    parser = argparse.ArgumentParser(description='V13.1 14:30 尾盘实战脚本')
    parser.add_argument('--tdx-file', type=str, default=None,
                        help='TDX实盘数据JSON文件路径')
    parser.add_argument('--data-mode', type=str, default='tdx_real',
                        choices=['tdx_real', 'synthetic', 'auto'],
                        help='数据模式 (default: tdx_real)')
    parser.add_argument('--skip-v131', action='store_true',
                        help='跳过V13.1增强（仅运行V13.0）')
    parser.add_argument('--output-dir', type=str, default='.',
                        help='报告输出目录 (default: 当前目录)')
    parser.add_argument('--quiet', action='store_true',
                        help='静默模式')
    args = parser.parse_args()

    print("=" * 70)
    print("  V13.1 14:30 尾盘实战系统")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据模式: {args.data_mode}")
    print(f"  V13.1增强: {'禁用' if args.skip_v131 else '启用'}")
    print("=" * 70)

    # 1. 加载数据
    tdx_data = None
    if args.tdx_file:
        tdx_data = load_tdx_data(args.tdx_file)
        if not tdx_data:
            print("❌ TDX数据加载失败，退出")
            return
    else:
        print("⚠️ 未提供TDX数据文件，将使用合成数据")
        # TODO: 生成合成数据

    # 2. 运行V13.0分析
    print("\n🔄 运行V13.0分析...")
    v130_results = run_v130_analysis(tdx_data)

    # 3. V13.1增强
    v131_results = []
    if not args.skip_v131 and V131_AVAILABLE:
        print("\n🔄 运行V13.1增强...")
        # 构建stock_data_map (这里需要从TDX数据中提取)
        stock_data_map = {}
        if tdx_data and 'stocks' in tdx_data:
            for stock in tdx_data['stocks']:
                code = stock.get('code', '')
                stock_data_map[code] = {
                    'tail_prices': stock.get('tail_prices'),
                    'tail_volumes': stock.get('tail_volumes'),
                    'listed_days': stock.get('listed_days', 9999),
                    'is_suspended': stock.get('is_suspended', False),
                    'avg_volume_yuan': stock.get('avg_volume_yuan', 0),
                    'current_price': stock.get('current_price', 0),
                    'prev_close': stock.get('prev_close', 0),
                    'consecutive_limit_up': stock.get('consecutive_limit_up', 0),
                }
        v131_results = enhance_with_v131(v130_results, stock_data_map)

    # 4. 生成报告
    print("\n📊 生成报告...")
    generate_reports(v130_results, v131_results, args.output_dir)

    print("\n🎉 V13.1 14:30 尾盘实战完成！")


if __name__ == '__main__':
    main()
