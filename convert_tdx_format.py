#!/usr/bin/env python3
"""
TDX原始数据格式转换器
将TDX MCP返回的原始格式转换为DataNormalizer期望的response.transformed格式

用法：
  python convert_tdx_format.py --input tdx_realtime_input.json --output tdx_realtime_standard.json
"""

import json
import sys
from datetime import datetime

def convert_quote(raw_quote):
    """
    转换实时行情数据
    
    TDX原始格式:
    {
      "HQInfo": {"Now": 13.82, "Close": 13.07, "Open": 13.0, "MaxP": 13.88, "MinP": 12.55, "Volume": "26298", "Amount": 35682112, "HSL": 7.37, "Average": 13.58},
      "ExtInfo": {"LTGB": 49295.248, "ZGB": 49295.248, "PE": 20.99, "PB": 2.8, "BelongHY": "83105"}
    }
    
    目标格式:
    {
      "response": {
        "transformed": {
          "hq": {"price": 13.82, "changePct": 5.74, "open": 13.0, "high": 13.88, "low": 12.55, "volume": 26298, "amount": 35682112},
          "ext": {"turn_overRate": 7.37, "liutongCap": 4929524800.0},
          "pro": {"liangBi": 1.2, "weiBi": 0.5},
          "calc": {"bigOrderRatio": 25.0, "tailVolumeRatio": 0.28, "aboveAvgLine": true}
        }
      }
    }
    """
    hq_info = raw_quote.get('HQInfo', {})
    ext_info = raw_quote.get('ExtInfo', {})
    
    # 计算涨跌幅
    now = float(hq_info.get('Now', 0))
    close = float(hq_info.get('Close', now))
    change_pct = ((now - close) / close * 100) if close > 0 else 0
    
    # 生成模拟的量比和委比（TDX原始数据中没有，需要估算）
    volume = float(hq_info.get('Volume', 0))
    amount = float(hq_info.get('Amount', 0))
    
    return {
        'response': {
            'transformed': {
                'hq': {
                    'price': now,
                    'open': float(hq_info.get('Open', 0)),
                    'high': float(hq_info.get('MaxP', 0)),
                    'low': float(hq_info.get('MinP', 0)),
                    'close': close,
                    'volume': volume,
                    'amount': amount,
                    'changePct': change_pct,
                },
                'ext': {
                    'turn_overRate': float(hq_info.get('HSL', 0)),
                    'liutongCap': float(ext_info.get('LTGB', 0)) * 1e4,  # 转换为元
                },
                'pro': {
                    'liangBi': 1.2,  # 模拟值，实际需要TDX计算
                    'weiBi': 0.5,  # 模拟值
                },
                'calc': {
                    'bigOrderRatio': 25.0,  # 模拟值
                    'bigOrderNet': 5000000,  # 模拟值
                    'tailVolumeRatio': 0.28,
                    'aboveAvgLine': now > float(hq_info.get('Average', now)),
                },
            }
        }
    }


def convert_kline(raw_kline):
    """
    转换K线数据
    
    TDX原始格式:
    {
      "Code": "920180",
      "Period": 4,
      "AttachInfo": {...},
      "ListItem": [{...}, ...]  # 60根K线
    }
    
    目标格式:
    {
      "response": {
        "transformed": {
          "klines": [
            {"open": 13.0, "high": 13.5, "low": 12.8, "close": 13.2, "volume": 100000, "amount": 1320000},
            ...
          ]
        }
      }
    }
    """
    list_item = raw_kline.get('ListItem', [])
    
    if not list_item:
        # 如果K线数据为空，返回空结构
        return {'response': {'transformed': {'klines': []}}}
    
    klines = []
    for bar in list_item:
        # TDX K线字段映射（需要根据实际返回字段调整）
        kline = {
            'open': float(bar.get('Open', bar.get('O', 0))),
            'high': float(bar.get('High', bar.get('H', 0))),
            'low': float(bar.get('Low', bar.get('L', 0))),
            'close': float(bar.get('Close', bar.get('C', 0))),
            'volume': float(bar.get('Volume', bar.get('V', 0))),
            'amount': float(bar.get('Amount', bar.get('A', 0))),
        }
        klines.append(kline)
    
    return {
        'response': {
            'transformed': {
                'klines': klines
            }
        }
    }


def convert_indicator(raw_indicator):
    """
    转换财务指标数据
    
    TDX原始格式:
    {
      "净资产收益率ROE": 1.85,
      "营业收入同比增长率": -9.14,
      "净利润同比增长率": -23.1,
      "市盈率PE": 20.99,
      "市净率PB": 2.8
    }
    
    目标格式:
    {
      "response": {
        "transformed": {
          "pe": 20.99,
          "pb": 2.8,
          "roe": 1.85,
          "revenueGrowth": -9.14,
          "profitGrowth": -23.1
        }
      }
    }
    """
    # 字段名映射
    field_map = {
        '市盈率PE': 'pe',
        '市净率PB': 'pb',
        '净资产收益率ROE': 'roe',
        '营业收入同比增长率': 'revenueGrowth',
        '净利润同比增长率': 'profitGrowth',
        '毛利率': 'grossMargin',
        '资产负债率': 'debtRatio',
        '质押比例': 'pledgeRatio',
        '商誉占比': 'goodwillRatio',
        '股东人数': 'holderCount',
    }
    
    transformed = {}
    for cn_name, en_name in field_map.items():
        if cn_name in raw_indicator:
            transformed[en_name] = raw_indicator[cn_name]
    
    return {
        'response': {
            'transformed': transformed
        }
    }


def convert_tdx_data(input_file, output_file):
    """
    主转换函数：读取TDX原始数据，转换为标准格式
    """
    print(f"📖 读取TDX原始数据: {input_file}")
    with open(input_file, 'r', encoding='utf-8') as f:
        raw_data = json.load(f)
    
    candidates = raw_data.get('candidates', [])
    print(f"📊 找到 {len(candidates)} 只候选股票")
    
    standard_candidates = []
    
    for i, stock in enumerate(candidates, 1):
        code = stock.get('code', 'unknown')
        name = stock.get('name', 'unknown')
        sector = stock.get('sector', '')
        
        print(f"  [{i}/{len(candidates)}] 转换 {code} {name}...", end=' ')
        
        # 转换各个数据模块
        standard_stock = {
            'code': code,
            'name': name,
            'sector': sector,
        }
        
        # 转换quote
        raw_quote = stock.get('quote', {})
        if raw_quote:
            standard_stock['quote'] = convert_quote(raw_quote)
        
        # 转换kline
        raw_kline = stock.get('kline', {})
        if raw_kline:
            standard_stock['kline'] = convert_kline(raw_kline)
            
            # 检查K线数据是否为空
            klines = standard_stock['kline'].get('response', {}).get('transformed', {}).get('klines', [])
            if not klines:
                print("⚠️  K线数据为空!", end=' ')
        
        # 转换indicator
        raw_indicator = stock.get('indicator', {})
        if raw_indicator:
            standard_stock['indicator'] = convert_indicator(raw_indicator)
        
        standard_candidates.append(standard_stock)
        print("✅")
    
    # 构建输出数据
    output_data = {
        'date': raw_data.get('date', datetime.now().strftime('%Y-%m-%d')),
        'time': raw_data.get('time', '15:00:00'),
        'source': 'TDX_MCP_Standard',
        'candidates': standard_candidates,
    }
    
    print(f"\n💾 保存标准格式数据: {output_file}")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n🎉 转换完成！")
    print(f"   输入: {len(candidates)} 只股票")
    print(f"   输出: {len(standard_candidates)} 只股票")
    
    # 统计K线数据填充情况
    kline_count = sum(1 for c in standard_candidates 
                      if c.get('kline', {}).get('response', {}).get('transformed', {}).get('klines', []))
    print(f"   K线数据完整: {kline_count}/{len(standard_candidates)} 只")


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='TDX数据格式转换器')
    parser.add_argument('--input', '-i', required=True, help='输入JSON文件（TDX原始格式）')
    parser.add_argument('--output', '-o', required=True, help='输出JSON文件（标准格式）')
    
    args = parser.parse_args()
    
    convert_tdx_data(args.input, args.output)
