#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
1分钟K线获取器（免费方案，无需注册）

数据源:
1. Sina Finance (免费, 无需注册)
2. Eastmoney (免费, 无需注册)

用法:
from fetch_1min_kline import get_1min_kline
data = get_1min_kline('600026', days=1)
"""

import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("[1MinKline] requests 未安装, 尝试使用 urllib")


def get_1min_kline_sina(code: str, market: str = 'sh', days: int = 1) -> Optional[List[Dict]]:
    """
    从Sina Finance获取1分钟K线（免费）
    
    Args:
        code: 股票代码 (如 '600026')
        market: 市场 ('sh'=上海, 'sz'=深圳)
        days: 获取最近N天的数据 (实际返回约 days*240根)
    
    Returns:
        [{'time': '2026-06-24 14:30', 'open': 22.5, 'high': 22.8, ...}, ...]
    """
    if not HAS_REQUESTS:
        print("[1MinKline] requests 未安装, 无法获取1分钟K线")
        return None
    
    try:
        # Sina API
        # scale=1 表示1分钟
        url = f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
        params = {
            'symbol': f"{market}{code}",
            'scale': '1',  # 1分钟
            'ma': 'no',
            'datalen': str(days * 240),  # 交易日约240分钟
        }
        
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code != 200:
            print(f"[1MinKline] Sina API失败: {resp.status_code}")
            return None
        
        data = resp.json()
        
        if not data:
            return None
        
        # 转换格式
        result = []
        for item in data:
            result.append({
                'time': item.get('day'),  # '2026-06-24 14:30:00'
                'open': float(item.get('open')),
                'high': float(item.get('high')),
                'low': float(item.get('low')),
                'close': float(item.get('close')),
                'volume': int(item.get('volume', 0)),  # 成交量(手)
                'amount': float(item.get('amount', 0)),  # 成交额(元)
            })
        
        return result
    
    except Exception as e:
        print(f"[1MinKline] Sina API异常: {e}")
        return None


def get_1min_kline_eastmoney(code: str, market: str = '0', days: int = 1) -> Optional[List[Dict]]:
    """
    从Eastmoney获取1分钟K线（免费）
    
    Args:
        code: 股票代码 (如 '600026')
        market: 市场 ('0'=深圳, '1'=上海)
        days: 获取最近N天的数据
    """
    if not HAS_REQUESTS:
        return None
    
    try:
        # Eastmoney API (非官方, 但稳定)
        url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            'secid': f"{market}.{code}",  # 如 '1.600026'
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
            'klt': '1',  # 1分钟
            'fqt': '1',  # 前复权
            'end': '20500101',
            'lmt': str(days * 240),
        }
        
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        
        if data.get('data') is None:
            return None
        
        klines = data['data'].get('klines', [])
        
        result = []
        for line in klines:
            parts = line.split(',')
            result.append({
                'time': parts[0],
                'open': float(parts[1]),
                'close': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'volume': int(parts[5]),
                'amount': float(parts[6]),
            })
        
        return result
    
    except Exception as e:
        print(f"[1MinKline] Eastmoney API异常: {e}")
        return None


def get_1min_kline(code: str, market: str = 'sh', days: int = 1) -> Optional[List[Dict]]:
    """
    获取1分钟K线（自动选择数据源）
    
    优先级: Sina → Eastmoney
    """
    # 确定market代码
    if market == 'sh':
        em_market = '1'
    elif market == 'sz':
        em_market = '0'
    else:
        em_market = market
    
    # 尝试Sina
    data = get_1min_kline_sina(code, market, days)
    if data:
        return data
    
    # 回退到Eastmoney
    data = get_1min_kline_eastmoney(code, em_market, days)
    if data:
        return data
    
    return None


def save_1min_kline_cache(code: str, market: str = 'sh', days: int = 1, cache_dir: str = 'data') -> bool:
    """
    获取并缓存1分钟K线数据
    """
    os.makedirs(cache_dir, exist_ok=True)
    
    data = get_1min_kline(code, market, days)
    if not data:
        return False
    
    cache_file = os.path.join(cache_dir, f"kline_1min_{code}.json")
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump({
            'code': code,
            'market': market,
            'period': '1min',
            'update_time': datetime.now().isoformat(),
            'data': data,
        }, f, ensure_ascii=False, indent=2)
    
    print(f"[1MinKline] ✅ 缓存已保存: {cache_file} ({len(data)}根K线)")
    return True


if __name__ == '__main__':
    print("=" * 60)
    print("测试1分钟K线获取（免费方案）")
    print("=" * 60)
    
    # 安装requests (如果没有)
    if not HAS_REQUESTS:
        print("\n⚠️ requests 未安装, 正在安装...")
        import subprocess
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'requests', '-q'])
        import requests
        HAS_REQUESTS = True
        print("✅ requests 已安装")
    
    # 测试
    test_code = '600026'
    print(f"\n📊 获取 {test_code} 的1分钟K线...")
    
    data = get_1min_kline(test_code, 'sh', days=1)
    
    if data:
        print(f"✅ 获取成功: {len(data)}根K线")
        print(f"\n前3根:")
        for i, k in enumerate(data[:3]):
            print(f"  {i+1}. {k['time']} O={k['open']} H={k['high']} L={k['low']} C={k['close']} V={k['volume']}")
        print(f"\n后3根:")
        for i, k in enumerate(data[-3:]):
            print(f"  {len(data)-3+i+1}. {k['time']} O={k['open']} H={k['high']} L={k['low']} C={k['close']} V={k['volume']}")
    else:
        print("❌ 获取失败")
    
    print("\n" + "=" * 60)
