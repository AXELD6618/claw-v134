#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复 V13_4_FullMarketMonitor.py：添加缺失的 _detect_market_anomaly() 方法定义
"""
import re

file_path = "V13_4_FullMarketMonitor.py"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 方法定义代码（将被插入到 score_stocks() 方法之前）
method_def = '''
    # ─── 市场异常检测 (P1-11) ───
    
    def _detect_market_anomaly(self) -> tuple:
        """
        检测市场异常状态（调用 market_anomaly_detector 模块）
        
        Returns:
            (anomaly_score, recommendation)
            recommendation: 'NORMAL' | 'REDUCE_POSITION' | 'SKIP_TRADING'
        """
        if not HAS_ANOMALY_DETECTOR:
            return 0.0, 'NORMAL'
        
        try:
            detector = MarketAnomalyDetector()
            
            # 1. 收集池内股票
            stocks = []
            for s in self.all_stocks.values():
                if s.v132_score > 0:
                    stocks.append({
                        'code': s.code,
                        'name': s.name,
                        'decline_pct': s.decline_pct,
                        'tier': s.tier,
                    })
            
            # 2. 检测异常
            anomaly_score, recommendation = detector.detect_anomaly(
                self.market_index, 
                stocks
            )
            
            return anomaly_score, recommendation
            
        except Exception as e:
            self.log(f"  [Anomaly] 检测失败: {e}", "WARN")
            return 0.0, 'NORMAL'
    
'''

# 找到 score_stocks() 方法的定义位置
# 在 "    # ─── 评分引擎 ───" 之后插入
insert_marker = "    # ─── 评分引擎 ───\n    "

if insert_marker in content:
    new_content = content.replace(
        insert_marker,
        insert_marker + method_def,
        1  # 只替换第一次
    )
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ 已添加 _detect_market_anomaly() 方法定义")
    print(f"   位置: score_stocks() 方法之前")
else:
    print(f"❌ 未找到插入位置: {insert_marker}")
    print(f"   需要手动添加")

# 验证语法
print(f"\n🔍 验证语法...")
try:
    import py_compile
    py_compile.compile(file_path, doraise=True)
    print(f"✅ 语法检查通过!")
except Exception as e:
    print(f"❌ 语法错误: {e}")
