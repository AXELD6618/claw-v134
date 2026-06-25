#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
市场异常检测器 (Market Anomaly Detector)

功能:
1. 检测市场普跌/普涨异常
2. 检测板块联动异常
3. 检测个股与市场背离
4. 自动调整仓位建议

用法:
from market_anomaly_detector import MarketAnomalyDetector
detector = MarketAnomalyDetector()
anomaly_score = detector.detect_anomaly(market_index, all_stocks)
"""

import json
from typing import Dict, List, Tuple
from datetime import datetime

class MarketAnomalyDetector:
    """
    市场异常检测器
    
    检测并预警:
    1. 市场普跌 (>{threshold}%股票下跌) → 降低仓位
    2. 市场普涨 (>{threshold}%股票上涨) → 适度加仓
    3. 板块异常 (某板块异常活跃) → 调整板块权重
    4. 个股背离 (个股与市场反向) → 标记异常信号
    """
    
    def __init__(self):
        self.threshold_universal_drop = 0.85  # 85%股票下跌 = 市场异常
        self.threshold_universal_rise = 0.80  # 80%股票上涨 = 市场异常
        self.threshold_sector_anomaly = 0.70  # 板块内70%股票联动 = 板块异常
        
        print("[Anomaly] 🚨 市场异常检测器启动")
    
    def detect_anomaly(
        self,
        market_index: Dict[str, float],  # {code: change_pct}
        all_stocks: List[Dict],       # [{code, name, decline_pct, ...}, ...]
        sector_data: Dict[str, List] = None,  # {sector: [stocks]}
    ) -> Tuple[float, str]:
        """
        检测市场异常
        
        返回:
        (anomaly_score, recommendation)
        - anomaly_score: 0.0 (正常) ~ 1.0 (极度异常)
        - recommendation: 'REDUCE_POSITION' | 'SKIP_TRADING' | 'NORMAL'
        """
        if not all_stocks or len(all_stocks) < 10:
            return 0.0, 'NORMAL'
        
        # 1. 市场普跌/普涨检测
        drop_count = sum(1 for s in all_stocks if s.get('decline_pct', 0) < -3.0)
        rise_count = sum(1 for s in all_stocks if s.get('decline_pct', 0) > 3.0)
        total = len(all_stocks)
        
        drop_ratio = drop_count / total
        rise_ratio = rise_count / total
        
        anomaly_score = 0.0
        recommendation = 'NORMAL'
        
        if drop_ratio >= self.threshold_universal_drop:
            # 市场普跌异常
            anomaly_score = drop_ratio
            if drop_ratio >= 0.95:
                recommendation = 'SKIP_TRADING'  # 极度异常，暂停交易
            else:
                recommendation = 'REDUCE_POSITION'  # 降低仓位
            
            print(f"[Anomaly] 🚨 市场普跌异常: {drop_ratio:.1%} 股票下跌")
            print(f"  建议: {recommendation}")
            
        elif rise_ratio >= self.threshold_universal_rise:
            # 市场普涨异常
            anomaly_score = rise_ratio
            recommendation = 'REDUCE_POSITION'  # 防止追高
            
            print(f"[Anomaly] 🚨 市场普涨异常: {rise_ratio:.1%} 股票上涨")
            print(f"  建议: {recommendation}")
        
        # 2. 板块异常检测
        if sector_data:
            sector_anomaly = self._detect_sector_anomaly(sector_data)
            if sector_anomaly > anomaly_score:
                anomaly_score = sector_anomaly
                recommendation = 'ADJUST_SECTOR_WEIGHT'
        
        return anomaly_score, recommendation
    
    def _detect_sector_anomaly(self, sector_data: Dict[str, List]) -> float:
        """检测板块异常"""
        max_anomaly = 0.0
        
        for sector, stocks in sector_data.items():
            if not stocks or len(stocks) < 5:
                continue
            
            # 计算板块内涨跌一致性
            drop_count = sum(1 for s in stocks if s.get('decline_pct', 0) < 0)
            rise_count = sum(1 for s in stocks if s.get('decline_pct', 0) > 0)
            
            consistency = max(drop_count, rise_count) / len(stocks)
            
            if consistency >= self.threshold_sector_anomaly:
                print(f"[Anomaly] 🚨 板块异常: {sector} ({consistency:.1%} 一致性)")
                max_anomaly = max(max_anomaly, consistency)
        
        return max_anomaly
    
    def adjust_position(
        self,
        base_position: float,  # 基础仓位 (0.0 ~ 1.0)
        anomaly_score: float,     # 异常分数 (0.0 ~ 1.0)
        recommendation: str,     # 建议
    ) -> float:
        """
        根据异常分数调整仓位
        
        返回: 调整后的仓位 (0.0 ~ 1.0)
        """
        if recommendation == 'SKIP_TRADING':
            return 0.0  # 暂停交易
        
        elif recommendation == 'REDUCE_POSITION':
            # 降低仓位: anomaly_score 越高，仓位越低
            reduction = anomaly_score * 0.8  # 最多降低80%
            return max(base_position * (1 - reduction), 0.1)
        
        elif recommendation == 'ADJUST_SECTOR_WEIGHT':
            # 调整板块权重 (由调用方处理)
            return base_position * 0.9
        
        else:
            return base_position
    
    def mark_abnormal_stocks(
        self,
        all_stocks: List[Dict],
        market_index: Dict[str, float],
    ) -> List[Dict]:
        """
        标记异常个股 (与市场背离的个股)
        
        返回: 更新后的 all_stocks (添加 'is_abnormal' 和 'abnormality_reason' 字段)
        """
        if not market_index:
            return all_stocks
        
        # 计算市场平均涨跌幅
        market_avg = sum(market_index.values()) / len(market_index) if market_index else 0.0
        
        for stock in all_stocks:
            stock_change = stock.get('decline_pct', 0)
            
            # 背离检测: 市场涨 > 1%，但个股跌 > 3%
            if market_avg > 1.0 and stock_change < -3.0:
                stock['is_abnormal'] = True
                stock['abnormality_reason'] = f"市场涨{market_avg:.1%}，个股跌{stock_change:.1%} (背离)"
            
            # 背离检测: 市场跌 > 1%，但个股涨 > 3%
            elif market_avg < -1.0 and stock_change > 3.0:
                stock['is_abnormal'] = True
                stock['abnormality_reason'] = f"市场跌{market_avg:.1%}，个股涨{stock_change:.1%} (背离)"
            
            else:
                stock['is_abnormal'] = False
                stock['abnormality_reason'] = None
        
        return all_stocks


# 测试
if __name__ == '__main__':
    print("=" * 60)
    print("市场异常检测器测试")
    print("=" * 60)
    
    detector = MarketAnomalyDetector()
    
    # 模拟6月24日市场异常 (普跌)
    test_stocks = [
        {'code': '002080', 'name': '中略股份', 'decline_pct': -9.98},
        {'code': '600667', 'name': '爱施德', 'decline_pct': -9.96},
        {'code': '002046', 'name': '国轩高科', 'decline_pct': -9.97},
        {'code': '002254', 'name': '泰尔股份', 'decline_pct': -9.95},
        {'code': '002051', 'name': '中捷股份', 'decline_pct': -9.94},
        # ... 假设30只全部下跌
    ] * 6  # 模拟180只股票，85%下跌
    
    market_index = {'000001': -4.5, '399006': -5.2}  # 上证-4.5%，创业板-5.2%
    
    anomaly_score, recommendation = detector.detect_anomaly(market_index, test_stocks)
    
    print(f"\n测试结果:")
    print(f"  异常分数: {anomaly_score:.4f}")
    print(f"  建议: {recommendation}")
    
    # 测试仓位调整
    base_pos = 0.5  # 基础仓位50%
    adjusted_pos = detector.adjust_position(base_pos, anomaly_score, recommendation)
    print(f"\n仓位调整:")
    print(f"  基础仓位: {base_pos:.0%}")
    print(f"  调整后: {adjusted_pos:.0%}")
    
    print("\n" + "=" * 60)
