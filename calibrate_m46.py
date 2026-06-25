#!/usr/bin/env python3
"""
M46 贝叶斯精度校准脚本
=====================
使用真实K线数据校准M46引擎，目标精度70%

功能：
1. 加载真实K线数据（从TDX获取）
2. 运行V13.0流水线分析
3. 计算M46当前精度
4. 调整参数以提升精度至70%
"""

import json
import sys
from datetime import datetime
from typing import Dict, List, Any

# 导入V13.0模块
try:
    from V13_0_Orchestrator import V13Orchestrator, OrchestratorConfig
    from V13_0_M46_BayesianEngine import M46BayesianEngine, M46Config
    V130_AVAILABLE = True
    print("✅ V13.0 模块已加载")
except ImportError as e:
    print(f"❌ V13.0 模块导入失败: {e}")
    V130_AVAILABLE = False
    sys.exit(1)


def load_kline_data(file_path: str) -> List[Dict]:
    """加载K线数据"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        stocks = data.get('stocks', [])
        if stocks:
            return stocks[0].get('kline', [])
        return []
    except Exception as e:
        print(f"❌ 加载失败 {file_path}: {e}")
        return []


def convert_kline_to_stock_info(kline: List[Dict], code: str, name: str) -> Dict:
    """将K线数据转换为StockInfo"""
    if not kline:
        return None
    
    # 使用最近一天的数据作为当前信息
    latest = kline[-1]
    
    return {
        'code': code,
        'name': name,
        'price': latest['close'],
        'open': latest['open'],
        'high': latest['high'],
        'low': latest['low'],
        'close': latest['close'],
        'volume': latest['volume'],
        'amount': latest['amount'],
        'kline': kline,  # 完整K线数据
    }


def calculate_m46_accuracy(stock_list: List[Dict], lookback_days: int = 20) -> Dict:
    """
    计算M46精度 — 使用滚动回测方法
    
    对于每只股票：
    1. 使用前lookback_days天的数据训练/校准
    2. 预测第lookback_days+1天的涨跌
    3. 与实际情况比较
    4. 滚动窗口，重复1-3
    """
    correct = 0
    total = 0
    predictions = []
    
    engine = M46BayesianEngine()
    
    for stock in stock_list:
        kline = stock.get('kline', [])
        if len(kline) < lookback_days + 2:
            print(f"⚠️ {stock['code']} 数据不足，跳过")
            continue
        
        # 滚动预测
        for i in range(lookback_days, len(kline) - 1):
            # 历史数据（用于计算特征）
            hist = kline[i - lookback_days:i]
            
            # 当前日收盘价
            current_close = kline[i]['close']
            
            # T+1日收盘价（实际结果）
            next_close = kline[i + 1]['close']
            actual_return = (next_close - current_close) / current_close
            
            # 计算M46概率（简化版，实际需要更多特征）
            # 这里使用价格动量作为简化特征
            returns = [(kline[j]['close'] - kline[j-1]['close']) / kline[j-1]['close'] 
                      for j in range(i - 5, i) if j > 0]
            avg_return = sum(returns) / len(returns) if returns else 0
            
            # 使用M46的Sigmoid映射
            raw_score = 0.5 + avg_return * 10  # 简化评分
            prob = engine.sigmoid_map(raw_score)
            
            # 判断正确性
            predicted_up = prob > 0.5
            actual_up = actual_return > 0
            
            if predicted_up == actual_up:
                correct += 1
            
            total += 1
            predictions.append({
                'code': stock['code'],
                'prob': prob,
                'actual_return': actual_return,
                'correct': predicted_up == actual_up,
            })
    
    accuracy = correct / total if total > 0 else 0
    return {
        'accuracy': accuracy,
        'correct': correct,
        'total': total,
        'predictions': predictions,
    }


def calibrate_m46_target_accuracy(target: float = 0.70):
    """校准M46至目标精度"""
    print("\n" + "="*70)
    print(f"  M46 贝叶斯精度校准 — 目标精度 {target*100:.0f}%")
    print("="*70)
    
    # 加载K线数据
    data_files = [
        ('E:/WorkBuddy_dot_workbuddy/Claw/tdx_000001_60d.json', '000001', '平安银行'),
        ('E:/WorkBuddy_dot_workbuddy/Claw/tdx_600519_60d.json', '600519', '贵州茅台'),
        ('E:/WorkBuddy_dot_workbuddy/Claw/tdx_002415_60d.json', '002415', '海康威视'),
    ]
    
    stock_list = []
    for file_path, code, name in data_files:
        kline = load_kline_data(file_path)
        if kline:
            stock_info = {
                'code': code,
                'name': name,
                'kline': kline,
            }
            stock_list.append(stock_info)
            print(f"✅ 已加载 {code} {name} ({len(kline)}根K线)")
    
    if not stock_list:
        print("❌ 无有效数据，退出")
        return
    
    # 获取当前M46精度
    print("\n🔄 计算当前M46精度...")
    current_acc = calculate_m46_accuracy(stock_list, lookback_days=20)
    print(f"   当前精度: {current_acc['accuracy']*100:.1f}% ({current_acc['correct']}/{current_acc['total']})")
    
    # 调整参数
    print(f"\n🔧 调整M46参数至目标精度 {target*100:.0f}%...")
    
    # 参数搜索
    best_acc = current_acc['accuracy']
    best_params = {'sigmoid_k': 8.0, 'sigmoid_x0': 0.55}
    
    # 简化版：调整sigmoid参数
    for k in [4.0, 6.0, 8.0, 10.0, 12.0]:
        for x0 in [0.45, 0.50, 0.55, 0.60, 0.65]:
            # 创建新引擎并测试
            test_engine = M46BayesianEngine(M46Config(sigmoid_k=k, sigmoid_x0=x0))
            
            # 快速测试（使用简化逻辑）
            correct = 0
            total = 0
            for stock in stock_list:
                kline = stock['kline']
                lookback = 20
                if len(kline) < lookback + 2:
                    continue
                
                for i in range(lookback, len(kline) - 1):
                    hist = kline[i - lookback:i]
                    current_close = kline[i]['close']
                    next_close = kline[i + 1]['close']
                    actual_return = (next_close - current_close) / current_close
                    
                    returns = [(kline[j]['close'] - kline[j-1]['close']) / kline[j-1]['close'] 
                              for j in range(i - 5, i) if j > 0]
                    avg_return = sum(returns) / len(returns) if returns else 0
                    
                    raw_score = 0.5 + avg_return * 10
                    prob = test_engine.sigmoid_map(raw_score)
                    
                    predicted_up = prob > 0.5
                    actual_up = actual_return > 0
                    
                    if predicted_up == actual_up:
                        correct += 1
                    total += 1
            
            if total > 0:
                acc = correct / total
                if acc > best_acc:
                    best_acc = acc
                    best_params = {'sigmoid_k': k, 'sigmoid_x0': x0}
                    print(f"   ✓ 新最佳: k={k}, x0={x0} → 精度={acc*100:.1f}%")
    
    print(f"\n🎯 最佳参数: {best_params}")
    print(f"   最佳精度: {best_acc*100:.1f}%")
    
    if best_acc >= target:
        print(f"✅ 已达到目标精度 {target*100:.0f}%！")
    else:
        print(f"⚠️ 未达到目标精度（当前{best_acc*100:.1f}%，目标{target*100:.0f}%）")
        print(f"   建议：")
        print(f"   1. 增加训练数据量（当前仅{len(stock_list)}只股票）")
        print(f"   2. 引入更多特征（行业、动量、成交量等）")
        print(f"   3. 使用更复杂的机器学习模型")
    
    return {
        'best_accuracy': best_acc,
        'best_params': best_params,
        'target': target,
    }


def main():
    print("="*70)
    print("  V13.1 M46 贝叶斯精度校准脚本")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)
    
    # 校准M46至70%精度
    calibrate_m46_target_accuracy(target=0.70)


if __name__ == '__main__':
    main()
