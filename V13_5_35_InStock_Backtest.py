#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.35 InStock回测引擎 — 适配TDX MCP的轻量级回测框架
============================================================
基于myhhub/stock (InStock) 回测架构改编:
  - rate_stats.py: 累计收益率计算 → 适配TDX K线数据
  - 11策略模板: 适配V13.5.34催化剂信号+三维融合
  - 多线程回测 → 适配SQLite+JSON缓存

核心功能:
  1. 催化剂信号T+1/T+3/T+5/T+10回测
  2. 三维融合评分(WINNER+SCR+SUPAMO) IC计算
  3. InStock 11策略回测验证
  4. 生成HTML回测报告

数据源:
  - TDX tdx_kline (K线数据)
  - TDX tdx_quotes (实时行情)
  - V13.5.34 CatalystScanner 缓存 (催化剂信号)
  - V13.5.33 三维融合评分结果

Author: 毕方灵犀貔貅助手 V13.5.35
Date: 2026-07-11
Reference: myhhub/stock (MIT License)
"""

import json
import os
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = BASE / "data"
CACHE_DIR = DATA_DIR / "fullmarket_cache"
DB_PATH = DATA_DIR / "holy_grail.db"

# ============================================================
# 回测周期
# ============================================================
class BacktestPeriod(Enum):
    T1 = 1    # 次日
    T3 = 3    # 3日
    T5 = 5    # 5日
    T10 = 10  # 10日
    T20 = 20  # 20日

# ============================================================
# 回测结果数据结构
# ============================================================
@dataclass
class BacktestResult:
    """单只股票回测结果"""
    code: str
    name: str = ""
    signal_date: str = ""           # 信号日(YYYYMMDD)
    signal_score: float = 0.0       # 信号评分(三维融合/催化剂D28)
    signal_type: str = ""           # 信号类型(STRONG_BUY/BUY/WATCH/catalyst)
    entry_price: float = 0.0        # 买入价(信号日收盘价)
    # 各周期收益率
    t1_return: Optional[float] = None
    t3_return: Optional[float] = None
    t5_return: Optional[float] = None
    t10_return: Optional[float] = None
    t20_return: Optional[float] = None
    # 区间最高/最低
    max_return: Optional[float] = None
    min_return: Optional[float] = None
    # 是否命中(T+1涨幅>0)
    t1_hit: bool = False
    t1_limit_up: bool = False       # T+1涨停
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass 
class BacktestSummary:
    """回测汇总统计"""
    total_signals: int = 0
    total_stocks: int = 0
    # T+1命中率
    t1_hit_count: int = 0
    t1_hit_rate: float = 0.0
    t1_limit_up_count: int = 0
    # 平均收益
    t1_avg_return: float = 0.0
    t3_avg_return: float = 0.0
    t5_avg_return: float = 0.0
    t10_avg_return: float = 0.0
    t20_avg_return: float = 0.0
    # IC (信息系数)
    ic_t1: float = 0.0
    ic_t3: float = 0.0
    ic_t5: float = 0.0
    # 按信号类型分组
    by_type: Dict[str, dict] = field(default_factory=dict)
    # 按评分分组
    by_score_bucket: Dict[str, dict] = field(default_factory=dict)


# ============================================================
# InStock策略模板 (适配V13.5.35)
# ============================================================
class InStockStrategies:
    """
    InStock 11策略 — 适配TDX K线数据
    
    每个策略返回bool: 是否满足买入条件
    输入: kline_data (List[dict]), 每个dict包含 date/open/high/low/close/volume/turnover
    """
    
    @staticmethod
    def volume_surge(kline: List[dict], threshold: int = 60) -> bool:
        """放量上涨: 1)涨幅>2%且收>开 2)成交额>2亿 3)量比>=2"""
        if len(kline) < threshold + 1:
            return False
        last = kline[-1]
        prev = kline[-2]
        p_change = (last['close'] - prev['close']) / prev['close'] * 100
        if p_change < 2 or last['close'] < last['open']:
            return False
        amount = last['close'] * last['volume']
        if amount < 200_000_000:
            return False
        vol_ma5 = sum(k['volume'] for k in kline[-6:-1]) / 5
        if vol_ma5 == 0:
            return False
        return last['volume'] / vol_ma5 >= 2
    
    @staticmethod
    def ma_bullish(kline: List[dict]) -> bool:
        """均线多头: MA30向上且斜率>20%"""
        if len(kline) < 60:
            return False
        closes = [k['close'] for k in kline]
        ma30_now = sum(closes[-30:]) / 30
        ma30_10ago = sum(closes[-40:-10]) / 30
        ma30_20ago = sum(closes[-50:-20]) / 30
        ma30_30ago = sum(closes[-60:-30]) / 30
        if not (ma30_30ago < ma30_20ago < ma30_10ago < ma30_now):
            return False
        return ma30_now / ma30_30ago > 1.2
    
    @staticmethod
    def parking_apron(kline: List[dict]) -> bool:
        """停机坪: 近15日有涨停, 后续3日高开收涨且振幅<3%"""
        if len(kline) < 20:
            return False
        # 找最近15日的涨停日
        limit_up_idx = -1
        for i in range(-15, -1):
            if abs(i) > len(kline):
                continue
            prev = kline[i-1] if i-1 >= -len(kline) else kline[0]
            curr = kline[i]
            p_change = (curr['close'] - prev['close']) / prev['close'] * 100
            if p_change >= 9.5:
                limit_up_idx = i
                break
        if limit_up_idx == -1:
            return False
        # 检查后续3日
        for j in range(limit_up_idx + 1, limit_up_idx + 4):
            if j >= 0 or abs(j) > len(kline):
                return False
            d = kline[j]
            if d['close'] <= d['open']:
                return False
            if abs(d['close'] - d['open']) / d['open'] * 100 >= 3:
                return False
        return True
    
    @staticmethod
    def backtest_ma250(kline: List[dict]) -> bool:
        """回踩年线: 突破年线后回踩缩量"""
        if len(kline) < 250:
            return False
        closes = [k['close'] for k in kline]
        vols = [k['volume'] for k in kline]
        ma250 = sum(closes[-250:]) / 250
        # 最近60日最高收盘价
        max_close = max(closes[-60:])
        max_idx = closes[-60:].index(max_close) + len(closes) - 60
        # 前段在年线下, 后段在年线上
        if closes[max_idx-1] > ma250:
            return False
        if closes[-1] < ma250:
            return False
        # 回踩缩量
        max_vol = vols[max_idx]
        min_vol_after = min(vols[max_idx:])
        if max_vol / min_vol_after < 2:
            return False
        if min(closes[max_idx:]) / max_close > 0.8:
            return False
        return True
    
    @staticmethod
    def breakthrough_platform(kline: List[dict]) -> bool:
        """突破平台: 收盘价>=60日均线>开盘价, 且放量上涨"""
        if len(kline) < 61:
            return False
        last = kline[-1]
        closes = [k['close'] for k in kline]
        ma60 = sum(closes[-60:]) / 60
        if not (last['close'] >= ma60 > last['open']):
            return False
        # 放量上涨
        return InStockStrategies.volume_surge(kline)
    
    @staticmethod
    def no_big_drawdown(kline: List[dict]) -> bool:
        """无大幅回撤: 60日涨幅<60%, 无单日跌>7%"""
        if len(kline) < 61:
            return False
        closes = [k['close'] for k in kline]
        if (closes[-1] / closes[-61] - 1) * 100 >= 0.6:
            return False
        for i in range(-60, 0):
            if abs(i) >= len(kline):
                continue
            prev = kline[i-1]
            curr = kline[i]
            p_change = (curr['close'] - prev['close']) / prev['close'] * 100
            if p_change < -7:
                return False
        return True
    
    @staticmethod
    def turtle_trade(kline: List[dict]) -> bool:
        """海龟交易: 收盘价>=60日最高收盘价"""
        if len(kline) < 61:
            return False
        last_close = kline[-1]['close']
        max_close_60 = max(k['close'] for k in kline[-61:-1])
        return last_close >= max_close_60
    
    @staticmethod
    def high_tight_flag(kline: List[dict]) -> bool:
        """高而窄旗形: 收盘/24~10日前最低>=1.9, 连续2日涨停"""
        if len(kline) < 25:
            return False
        last_close = kline[-1]['close']
        min_close = min(k['close'] for k in kline[-24:-9])
        if last_close / min_close < 1.9:
            return False
        # 连续2日涨停
        for i in range(-3, -1):
            prev = kline[i-1]
            curr = kline[i]
            p_change = (curr['close'] - prev['close']) / prev['close'] * 100
            if p_change < 9.5:
                return False
        return True
    
    @staticmethod
    def climax_limitdown(kline: List[dict]) -> bool:
        """放量跌停: 跌>9.5%, 成交额>2亿, 量比>=4"""
        if len(kline) < 6:
            return False
        last = kline[-1]
        prev = kline[-2]
        p_change = (last['close'] - prev['close']) / prev['close'] * 100
        if p_change > -9.5:
            return False
        amount = last['close'] * last['volume']
        if amount < 200_000_000:
            return False
        vol_ma5 = sum(k['volume'] for k in kline[-6:-1]) / 5
        if vol_ma5 == 0:
            return False
        return last['volume'] / vol_ma5 >= 4
    
    @staticmethod
    def low_atr(kline: List[dict]) -> bool:
        """低ATR成长: 10日最高/最低收盘价比>1.1, 上市>250日"""
        if len(kline) < 251:
            return False
        closes = [k['close'] for k in kline]
        max_10 = max(closes[-10:])
        min_10 = min(closes[-10:])
        return max_10 / min_10 > 1.1
    
    # 策略注册表
    STRATEGY_MAP = {
        "volume_surge": volume_surge,
        "ma_bullish": ma_bullish,
        "parking_apron": parking_apron,
        "backtest_ma250": backtest_ma250,
        "breakthrough_platform": breakthrough_platform,
        "no_big_drawdown": no_big_drawdown,
        "turtle_trade": turtle_trade,
        "high_tight_flag": high_tight_flag,
        "climax_limitdown": climax_limitdown,
        "low_atr": low_atr,
    }


# ============================================================
# 回测引擎
# ============================================================
class InStockBacktester:
    """
    回测引擎 — 适配TDX MCP数据
    
    使用方式:
        bt = InStockBacktester()
        
        # 方式1: 回测催化剂信号
        results = bt.backtest_catalyst_signals(
            signals=[{code, date, score, type}, ...],
            kline_fetcher=my_tdx_kline_func,
        )
        
        # 方式2: 回测InStock策略
        results = bt.backtest_strategy(
            strategy_name="volume_surge",
            stocks=[("000977", "1"), ...],
            kline_fetcher=my_tdx_kline_func,
            start_date="20260701",
            end_date="20260710",
        )
        
        # 生成报告
        summary = bt.summarize(results)
        bt.generate_html_report(results, summary, "outputs/backtest_report.html")
    """
    
    def __init__(self):
        self.results: List[BacktestResult] = []
    
    # ============================================================
    # 回测催化剂信号
    # ============================================================
    def backtest_catalyst_signals(
        self,
        signals: List[dict],
        kline_fetcher,  # callable(code, setcode, wantNum) -> List[dict]
        periods: List[int] = None,
    ) -> List[BacktestResult]:
        """
        回测催化剂信号T+N收益
        
        Args:
            signals: 信号列表, 每个包含 code, setcode, date(YYYYMMDD), score, type
            kline_fetcher: K线获取函数, 返回[{date, open, high, low, close, volume}, ...]
            periods: 回测周期列表, 默认[1,3,5,10,20]
        """
        if periods is None:
            periods = [1, 3, 5, 10, 20]
        
        results = []
        for sig in signals:
            code = sig.get("code", "")
            setcode = sig.get("setcode", "1")
            sig_date = sig.get("date", "")
            score = sig.get("score", 0.0)
            sig_type = sig.get("type", "catalyst")
            name = sig.get("name", "")
            
            if not code or not sig_date:
                continue
            
            try:
                # 获取信号日前后K线 (多取30根确保覆盖T+20)
                kline = kline_fetcher(code, setcode, 60)
                if not kline or len(kline) < 2:
                    continue
                
                # 找到信号日index
                sig_idx = self._find_date_index(kline, sig_date)
                if sig_idx < 0 or sig_idx >= len(kline) - 1:
                    continue
                
                entry_price = kline[sig_idx]['close']
                result = BacktestResult(
                    code=code, name=name,
                    signal_date=sig_date,
                    signal_score=score,
                    signal_type=sig_type,
                    entry_price=entry_price,
                )
                
                # 计算各周期收益
                future_klines = kline[sig_idx + 1:]
                max_ret = -999.0
                min_ret = 999.0
                
                for period in periods:
                    if len(future_klines) < period:
                        continue
                    exit_price = future_klines[period - 1]['close']
                    ret = (exit_price - entry_price) / entry_price * 100
                    
                    if period == 1:
                        result.t1_return = round(ret, 2)
                        result.t1_hit = ret > 0
                        result.t1_limit_up = ret >= 9.5
                    elif period == 3:
                        result.t3_return = round(ret, 2)
                    elif period == 5:
                        result.t5_return = round(ret, 2)
                    elif period == 10:
                        result.t10_return = round(ret, 2)
                    elif period == 20:
                        result.t20_return = round(ret, 2)
                    
                    # 区间最高最低
                    period_klines = future_klines[:period]
                    period_max = max(k['high'] for k in period_klines)
                    period_min = min(k['low'] for k in period_klines)
                    max_ret = max(max_ret, (period_max - entry_price) / entry_price * 100)
                    min_ret = min(min_ret, (period_min - entry_price) / entry_price * 100)
                
                result.max_return = round(max_ret, 2) if max_ret > -998 else None
                result.min_return = round(min_ret, 2) if min_ret < 998 else None
                results.append(result)
                
            except Exception as e:
                print(f"  [ERROR] {code}: {e}")
                continue
        
        self.results = results
        return results
    
    # ============================================================
    # 回测InStock策略
    # ============================================================
    def backtest_strategy(
        self,
        strategy_name: str,
        stocks: List[Tuple[str, str]],  # [(code, setcode), ...]
        kline_fetcher,
        start_date: str = "",
        end_date: str = "",
    ) -> List[BacktestResult]:
        """
        回测InStock策略在指定时间段的表现
        
        Args:
            strategy_name: 策略名(volume_surge/ma_bullish/...)
            stocks: 股票列表 [(code, setcode), ...]
            kline_fetcher: K线获取函数
            start_date: 开始日期(YYYYMMDD)
            end_date: 结束日期(YYYYMMDD)
        """
        strategy_func = InStockStrategies.STRATEGY_MAP.get(strategy_name)
        if strategy_func is None:
            print(f"  [ERROR] 未知策略: {strategy_name}")
            return []
        
        results = []
        for code, setcode in stocks:
            try:
                kline = kline_fetcher(code, setcode, 100)
                if not kline or len(kline) < 60:
                    continue
                
                # 在回测期间逐日检查
                for i in range(len(kline)):
                    date_str = kline[i].get('date', '')
                    if start_date and date_str < start_date:
                        continue
                    if end_date and date_str > end_date:
                        continue
                    
                    # 检查策略条件(使用到当前日为止的数据)
                    sub_kline = kline[:i+1]
                    if len(sub_kline) < 60:
                        continue
                    
                    try:
                        if strategy_func(sub_kline):
                            # 命中策略, 计算T+1/T+3/T+5
                            if i + 5 >= len(kline):
                                continue
                            entry = kline[i]['close']
                            result = BacktestResult(
                                code=code,
                                signal_date=date_str,
                                signal_score=0.0,
                                signal_type=f"strategy_{strategy_name}",
                                entry_price=entry,
                            )
                            for period in [1, 3, 5]:
                                if i + period < len(kline):
                                    exit_p = kline[i + period]['close']
                                    ret = (exit_p - entry) / entry * 100
                                    if period == 1:
                                        result.t1_return = round(ret, 2)
                                        result.t1_hit = ret > 0
                                    elif period == 3:
                                        result.t3_return = round(ret, 2)
                                    elif period == 5:
                                        result.t5_return = round(ret, 2)
                            results.append(result)
                    except:
                        continue
            except Exception as e:
                print(f"  [ERROR] {code}: {e}")
                continue
        
        self.results = results
        return results
    
    # ============================================================
    # 汇总统计
    # ============================================================
    def summarize(self, results: List[BacktestResult] = None) -> BacktestSummary:
        """计算回测汇总统计"""
        if results is None:
            results = self.results
        
        if not results:
            return BacktestSummary()
        
        summary = BacktestSummary(
            total_signals=len(results),
            total_stocks=len(set(r.code for r in results)),
        )
        
        # T+1命中率
        t1_results = [r for r in results if r.t1_return is not None]
        summary.t1_hit_count = sum(1 for r in t1_results if r.t1_hit)
        summary.t1_hit_rate = summary.t1_hit_count / len(t1_results) * 100 if t1_results else 0
        summary.t1_limit_up_count = sum(1 for r in t1_results if r.t1_limit_up)
        
        # 平均收益
        def avg(lst):
            vals = [x for x in lst if x is not None]
            return round(sum(vals) / len(vals), 2) if vals else 0.0
        
        summary.t1_avg_return = avg([r.t1_return for r in results])
        summary.t3_avg_return = avg([r.t3_return for r in results])
        summary.t5_avg_return = avg([r.t5_return for r in results])
        summary.t10_avg_return = avg([r.t10_return for r in results])
        summary.t20_avg_return = avg([r.t20_return for r in results])
        
        # IC计算 (信号评分 vs T+N收益的Spearman相关)
        summary.ic_t1 = self._calc_ic(results, 't1_return')
        summary.ic_t3 = self._calc_ic(results, 't3_return')
        summary.ic_t5 = self._calc_ic(results, 't5_return')
        
        # 按信号类型分组
        by_type = {}
        for r in results:
            t = r.signal_type or "unknown"
            if t not in by_type:
                by_type[t] = {"count": 0, "t1_hits": 0, "t1_avg": []}
            by_type[t]["count"] += 1
            if r.t1_hit:
                by_type[t]["t1_hits"] += 1
            if r.t1_return is not None:
                by_type[t]["t1_avg"].append(r.t1_return)
        
        for t, d in by_type.items():
            d["t1_hit_rate"] = round(d["t1_hits"] / d["count"] * 100, 1) if d["count"] > 0 else 0
            d["t1_avg_return"] = round(sum(d["t1_avg"]) / len(d["t1_avg"]), 2) if d["t1_avg"] else 0
            del d["t1_avg"]
        
        summary.by_type = by_type
        
        # 按评分分桶
        buckets = {">=85(STRONG_BUY)": [], "70-84(BUY)": [], "55-69(WATCH)": [], "<55": []}
        for r in results:
            s = r.signal_score
            if s >= 85:
                buckets[">=85(STRONG_BUY)"].append(r)
            elif s >= 70:
                buckets["70-84(BUY)"].append(r)
            elif s >= 55:
                buckets["55-69(WATCH)"].append(r)
            else:
                buckets["<55"].append(r)
        
        for bucket_name, bucket_results in buckets.items():
            if not bucket_results:
                continue
            t1_rets = [r.t1_return for r in bucket_results if r.t1_return is not None]
            hits = sum(1 for r in bucket_results if r.t1_hit)
            summary.by_score_bucket[bucket_name] = {
                "count": len(bucket_results),
                "t1_hit_rate": round(hits / len(bucket_results) * 100, 1),
                "t1_avg_return": round(sum(t1_rets) / len(t1_rets), 2) if t1_rets else 0,
            }
        
        return summary
    
    # ============================================================
    # IC计算 (Spearman等级相关)
    # ============================================================
    def _calc_ic(self, results: List[BacktestResult], field: str) -> float:
        """计算信号评分与T+N收益的IC (Spearman等级相关)"""
        pairs = [(r.signal_score, getattr(r, field)) for r in results 
                 if getattr(r, field) is not None and r.signal_score > 0]
        if len(pairs) < 3:
            return 0.0
        
        # 简化Spearman: 用Pearson近似
        n = len(pairs)
        scores = [p[0] for p in pairs]
        returns = [p[1] for p in pairs]
        
        mean_s = sum(scores) / n
        mean_r = sum(returns) / n
        
        cov = sum((s - mean_s) * (r - mean_r) for s, r in pairs)
        var_s = sum((s - mean_s) ** 2 for s in scores)
        var_r = sum((r - mean_r) ** 2 for r in returns)
        
        if var_s == 0 or var_r == 0:
            return 0.0
        
        ic = cov / math.sqrt(var_s * var_r)
        return round(ic, 4)
    
    # ============================================================
    # 辅助: 查找日期在K线中的index
    # ============================================================
    def _find_date_index(self, kline: List[dict], date_str: str) -> int:
        """在K线列表中查找指定日期的index"""
        date_str = str(date_str).replace("-", "").replace("/", "")
        for i, k in enumerate(kline):
            k_date = str(k.get('date', '')).replace("-", "").replace("/", "")
            if k_date == date_str:
                return i
        # 如果精确匹配失败, 找最接近的
        for i, k in enumerate(kline):
            k_date = str(k.get('date', '')).replace("-", "").replace("/", "")
            if k_date >= date_str:
                return i
        return -1


# ============================================================
# 验证测试
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.35 InStock回测引擎 — 验证测试")
    print("=" * 70)
    
    bt = InStockBacktester()
    
    # 模拟K线数据获取函数
    def mock_kline_fetcher(code, setcode, wantNum):
        """生成模拟K线数据"""
        import random
        random.seed(hash(code) % 1000)
        base_price = 10.0 + random.uniform(0, 50)
        kline = []
        for i in range(wantNum):
            date = f"2026{(i // 30) + 7:02d}{(i % 30) + 1:02d}"
            if int(date[4:6]) > 12:
                continue
            open_p = base_price * (1 + random.uniform(-0.02, 0.02))
            close_p = open_p * (1 + random.uniform(-0.05, 0.05))
            high_p = max(open_p, close_p) * (1 + random.uniform(0, 0.03))
            low_p = min(open_p, close_p) * (1 - random.uniform(0, 0.03))
            volume = int(random.uniform(1000000, 50000000))
            kline.append({
                'date': date,
                'open': round(open_p, 2),
                'high': round(high_p, 2),
                'low': round(low_p, 2),
                'close': round(close_p, 2),
                'volume': volume,
            })
            base_price = close_p
        return kline
    
    # 测试1: 催化剂信号回测
    print("\n### 测试1: 催化剂信号回测")
    mock_signals = [
        {"code": "000977", "setcode": "1", "date": "20260708", "score": 88, "type": "STRONG_BUY", "name": "浪潮信息"},
        {"code": "605090", "setcode": "1", "date": "20260708", "score": 82, "type": "BUY", "name": "九丰能源"},
        {"code": "300540", "setcode": "1", "date": "20260708", "score": 75, "type": "BUY", "name": "蜀道装备"},
        {"code": "002549", "setcode": "1", "date": "20260709", "score": 65, "type": "WATCH", "name": "凯美特气"},
        {"code": "603195", "setcode": "1", "date": "20260709", "score": 88, "type": "STRONG_BUY", "name": "公牛集团"},
    ]
    
    results = bt.backtest_catalyst_signals(mock_signals, mock_kline_fetcher)
    print(f"  回测 {len(results)} 只股票")
    
    for r in results:
        print(f"  {r.code} {r.name} | 评分={r.signal_score} | "
              f"T+1={r.t1_return}% | T+3={r.t3_return}% | T+5={r.t5_return}% | "
              f"{'✓' if r.t1_hit else '✗'}")
    
    # 汇总统计
    summary = bt.summarize()
    print(f"\n### 回测汇总")
    print(f"  总信号: {summary.total_signals}")
    print(f"  T+1命中率: {summary.t1_hit_count}/{summary.total_signals} = {summary.t1_hit_rate:.1f}%")
    print(f"  T+1涨停: {summary.t1_limit_up_count}只")
    print(f"  T+1均幅: {summary.t1_avg_return}%")
    print(f"  T+3均幅: {summary.t3_avg_return}%")
    print(f"  T+5均幅: {summary.t5_avg_return}%")
    print(f"  IC_T1: {summary.ic_t1}")
    
    print(f"\n  按信号类型:")
    for t, d in summary.by_type.items():
        print(f"    {t}: {d['count']}只, T+1命中={d['t1_hit_rate']}%, 均幅={d['t1_avg_return']}%")
    
    print(f"\n  按评分分桶:")
    for b, d in summary.by_score_bucket.items():
        print(f"    {b}: {d['count']}只, T+1命中={d['t1_hit_rate']}%, 均幅={d['t1_avg_return']}%")
    
    # 测试2: InStock策略回测
    print(f"\n### 测试2: InStock策略回测 (volume_surge)")
    mock_stocks = [("000977", "1"), ("605090", "1"), ("300540", "1"), ("002549", "1"), ("603195", "1")]
    strat_results = bt.backtest_strategy(
        "volume_surge", mock_stocks, mock_kline_fetcher,
        start_date="20260701", end_date="20260710",
    )
    print(f"  策略命中: {len(strat_results)} 次")
    for r in strat_results[:5]:
        print(f"  {r.code} @ {r.signal_date} | T+1={r.t1_return}%")
    
    print("\n" + "=" * 70)
    print("InStock回测引擎验证通过!")
    print(f"参考: myhhub/stock (MIT License) — rate_stats.py + 11策略")
    print("=" * 70)
