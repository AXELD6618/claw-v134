#!/usr/bin/env python3
"""
V13.5.18 资本流向数据获取器 — 为M71 D29/D31提供真实historical主力净额数据
==========================================================================
从TDX MCP tdx_api_data工具获取Historical主力净额数据，
解析为M71 D29/D31所需的capital_flow_history格式。

依赖:
  - TDX_EnhancedFeeder.parse_capital_flow_to_history()
  - tdx_api_data MCP工具 (通过subprocess调用或AI Agent调用)

调用方式:
  # 方式1: 在自动化prompt中由AI Agent调用 (推荐)
  # AI Agent调用tdx_api_data → 保存JSON → 本脚本读取

  # 方式2: 独立运行 (需要MCP环境)
  python V13_5_FetchCapitalFlow.py --codes 300540 600973 000001

作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.18
日期: 2026-07-04
"""

import json
import os
import sys
from typing import Dict, List, Any

# 添加当前目录到path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from V13_5_TDX_EnhancedFeeder import parse_capital_flow_to_history
except ImportError:
    print("[FetchCapitalFlow] 警告: 无法导入TDX_EnhancedFeeder, 使用内置解析器")
    parse_capital_flow_to_history = None


# ═══════════════════════════════════════════════════════════
# 核心功能
# ═══════════════════════════════════════════════════════════

def fetch_capital_flow_from_json(json_path: str) -> Dict[str, List[Dict]]:
    """
    从JSON文件读取tdx_api_data返回结果 → 解析为capital_flow_history格式

    JSON文件格式 (由AI Agent保存):
      {
        "300540": {"ok": true, "response": {"transformed": {"tables": [...]}}},
        "600973": {"ok": true, "response": {...}},
        ...
      }

    返回:
      {
        "300540": [
          {"date": "2026-06-24", "main_net": 169.0, "close": 26.19},
          ...
        ],
        ...
      }
    """
    result = {}

    if not os.path.exists(json_path):
        print(f"[fetch_capital_flow_from_json] 文件不存在: {json_path}")
        return result

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)

        for code, api_response in raw_data.items():
            if parse_capital_flow_to_history:
                history = parse_capital_flow_to_history(api_response)
            else:
                history = _parse_capital_flow_builtin(api_response)
            result[code] = history

        print(f"[fetch_capital_flow_from_json] 解析完成: {len(result)}只股票")

    except Exception as e:
        print(f"[fetch_capital_flow_from_json] 解析失败: {e}")

    return result


def _parse_capital_flow_builtin(api_response: Dict) -> List[Dict]:
    """
    内置解析器 (当无法导入TDX_EnhancedFeeder时使用)
    """
    result = []

    if not api_response or not api_response.get('ok'):
        return result

    try:
        tables = api_response.get('response', {}).get('transformed', {}).get('tables', [])

        for table in tables:
            if table.get('name') == 'capital_flow':
                rows = table.get('rows', [])
                for row in rows:
                    date_str = row.get('日期', '')
                    main_net_yuan = row.get('主力净额金额(元)', 0)
                    close = row.get('收盘价', 0.0)

                    if date_str:
                        result.append({
                            'date': date_str,
                            'main_net': main_net_yuan / 10000.0,  # 元→万元
                            'close': close,
                        })

                break

        result.sort(key=lambda x: x['date'])

    except Exception as e:
        print(f"[_parse_capital_flow_builtin] 解析错误: {e}")

    return result


def save_capital_flow_for_m71(capital_flow_data: Dict[str, List[Dict]], output_path: str) -> bool:
    """
    保存capital_flow_history数据为JSON → 供M71集成层读取

    输出格式:
      {
        "300540": [{"date": "...", "main_net": 169.0, "close": 26.19}, ...],
        "600973": [...],
        ...
      }
    """
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(capital_flow_data, f, ensure_ascii=False, indent=2)

        print(f"[save_capital_flow_for_m71] 保存成功: {output_path} ({len(capital_flow_data)}只股票)")
        return True

    except Exception as e:
        print(f"[save_capital_flow_for_m71] 保存失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════

def main():
    """CLI入口"""
    import argparse

    parser = argparse.ArgumentParser(description='资本流向数据获取器')
    parser.add_argument('--input-json', type=str, default='data/capital_flow_raw.json',
                        help='tdx_api_data返回结果JSON文件路径')
    parser.add_argument('--output-json', type=str, default='data/capital_flow_history.json',
                        help='输出JSON文件路径 (M71集成层读取)')
    parser.add_argument('--codes', type=str, nargs='+',
                        help='股票代码列表 (如 300540 600973)')

    args = parser.parse_args()

    # 从JSON文件读取并解析
    if os.path.exists(args.input_json):
        print(f"[FetchCapitalFlow] 从JSON文件解析: {args.input_json}")
        data = fetch_capital_flow_from_json(args.input_json)
        save_capital_flow_for_m71(data, args.output_json)
    else:
        print(f"[FetchCapitalFlow] 错误: 输入文件不存在: {args.input_json}")
        print("提示: 请先由AI Agent调用tdx_api_data获取资本流向数据并保存为JSON")
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
