"""
L2形态检测诊断脚本 - 验证K线数据是否正确传入形态检测器
"""
import json
import sys
sys.path.insert(0, '.')

from V13_0_DataPipeline import DataPipeline
from V13_0_TailMarket_Ultimate import PatternDetector

# Load JSON
with open('tdx_realtime_input.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

dp = DataPipeline()
pd_detector = PatternDetector()

for c in data['candidates']:
    code = c['code']
    name = c['name']
    
    stock = dp.normalizer.prepare_stock(
        code=code, name=name,
        raw_quote=c.get('quote'),
        raw_kline=c.get('kline'),
        raw_indicator=c.get('indicator'),
        sector=c.get('sector', ''),
    )
    
    prices = stock.get('prices', [])
    volumes = stock.get('volumes', [])
    ma5 = stock.get('ma5', [])
    
    print(f"\n{'='*60}")
    print(f"  {code} {name}")
    print(f"  价格: {stock.get('current_price', 0)}")
    print(f"  涨跌幅: {stock.get('daily_change_pct', 0):+.2%}")
    print(f"  换手率: {stock.get('turnover_rate', 0):.2%}")
    print(f"  K线根数: prices={len(prices)} volumes={len(volumes)} ma5={len(ma5)}")
    
    if len(prices) > 0:
        print(f"  价格范围: {min(prices):.2f} - {max(prices):.2f}")
        print(f"  均线: ma5[-1]={ma5[-1]:.2f} ma20[-1]={stock['ma20'][-1]:.2f} ma60[-1]={stock['ma60'][-1]:.2f}")
    
    # Run L2 pattern detection
    l2 = pd_detector.comprehensive_pattern_score(stock)
    
    patterns = l2.get('patterns', {})
    active = [(name, info) for name, info in patterns.items() 
              if info.get('triggered') or info.get('score', 0) > 0]
    
    print(f"  综合评分: {l2.get('total_score', 0):.2f}")
    print(f"  评级: {l2.get('composite_rating', '?')}")
    print(f"  通过: {l2.get('passed', False)}")
    print(f"  活跃形态 ({len(active)}):")
    for pname, pinfo in active:
        print(f"    - {pname}: score={pinfo.get('score',0):.2f} triggered={pinfo.get('triggered',False)}")
    
    if not active:
        print(f"    (无)")
        # Show trap warnings
        print(f"  陷阱警告: {l2.get('trap_warnings', 0)}")

print("\n" + "="*60)
print("  诊断完成")
