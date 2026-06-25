#!/usr/bin/env python3
"""
V13.0 P0-1: 50+股批量回测 + M46行业先验校准
================================================
从301只动态监控池取TOP-50代表性标的，运行60日滚动回测，
输出：命中率(Precision)/盈亏比(PLR)/踩雷率 + 31行业M46先验校准值。

使用方式：
  python run_bulk_backtest.py [--top 50] [--days 60] [--output data/backtest_result.json]
  在WorkBuddy自动化中：此脚本被调用前，TDX MCP需预先拉取K线数据到缓存
"""

import json, math, os, sys, time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

MAX_STOCKS = 50        # 回测股票数
BACKTEST_DAYS = 60     # 回测窗口（交易日）
HIT_THRESHOLD = 9.5    # 涨停阈值（9.5%涨幅即视为逼近涨停）
MIN_KLINES = 40        # 最低K线数量要求

# ═══════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════

def sma(values, period):
    result = []
    for i in range(len(values)):
        if i < period - 1: result.append(sum(values[:i+1])/(i+1))
        else: result.append(sum(values[i-period+1:i+1])/period)
    return result

def load_watchlist(top_n=50):
    """加载动态监控池TOP-N"""
    wl_path = os.path.join(os.path.dirname(__file__) or ".", "data", "dynamic_watchlist.json")
    if not os.path.exists(wl_path):
        print("⚠️ 动态监控池未找到，使用默认列表")
        return DEFAULT_TOP50
    with open(wl_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    stocks = data.get("stocks", [])
    # 从每个行业至少取1只，确保行业覆盖
    by_ind = defaultdict(list)
    for s in stocks:
        by_ind[s["industry"]].append(s)
    selected = []
    for ind, lst in sorted(by_ind.items()):
        n = max(1, min(len(lst), 2))  # 每行业1-2只
        selected.extend(lst[:n])
        if len(selected) >= top_n:
            break
    # 如果不够，再补
    if len(selected) < top_n:
        for s in stocks:
            if s not in selected:
                selected.append(s)
            if len(selected) >= top_n:
                break
    return selected[:top_n]

# 默认50只（如果JSON不可用）
DEFAULT_TOP50 = [
    {"code":"600519","market":"1","name":"贵州茅台","industry":"食品饮料"},
    {"code":"000858","market":"0","name":"五粮液","industry":"食品饮料"},
    {"code":"300750","market":"0","name":"宁德时代","industry":"电力设备"},
    {"code":"002594","market":"0","name":"比亚迪","industry":"电力设备"},
    {"code":"603259","market":"1","name":"药明康德","industry":"医药生物"},
    {"code":"300760","market":"0","name":"迈瑞医疗","industry":"医药生物"},
    {"code":"002230","market":"0","name":"科大讯飞","industry":"计算机"},
    {"code":"688111","market":"1","name":"金山办公","industry":"计算机"},
    {"code":"000001","market":"0","name":"平安银行","industry":"银行"},
    {"code":"600036","market":"1","name":"招商银行","industry":"银行"},
    {"code":"601318","market":"1","name":"中国平安","industry":"非银金融"},
    {"code":"300059","market":"0","name":"东方财富","industry":"非银金融"},
    {"code":"688256","market":"1","name":"寒武纪","industry":"AI算力"},
    {"code":"002475","market":"0","name":"立讯精密","industry":"电子"},
    {"code":"603501","market":"1","name":"韦尔股份","industry":"电子"},
    {"code":"601899","market":"1","name":"紫金矿业","industry":"有色金属"},
    {"code":"002460","market":"0","name":"赣锋锂业","industry":"有色金属"},
    {"code":"300274","market":"0","name":"阳光电源","industry":"电力设备"},
    {"code":"601012","market":"1","name":"隆基绿能","industry":"电力设备"},
    {"code":"300308","market":"0","name":"中际旭创","industry":"通信"},
    {"code":"300502","market":"0","name":"新易盛","industry":"通信"},
    {"code":"600276","market":"1","name":"恒瑞医药","industry":"医药生物"},
    {"code":"300015","market":"0","name":"爱尔眼科","industry":"医药生物"},
    {"code":"688981","market":"1","name":"中芯国际","industry":"电子"},
    {"code":"002371","market":"0","name":"北方华创","industry":"电子"},
    {"code":"600809","market":"1","name":"山西汾酒","industry":"食品饮料"},
    {"code":"000568","market":"0","name":"泸州老窖","industry":"食品饮料"},
    {"code":"600030","market":"1","name":"中信证券","industry":"非银金融"},
    {"code":"601688","market":"1","name":"华泰证券","industry":"非银金融"},
    {"code":"000333","market":"0","name":"美的集团","industry":"家用电器"},
    {"code":"000651","market":"0","name":"格力电器","industry":"家用电器"},
    {"code":"688017","market":"1","name":"绿的谐波","industry":"机器人"},
    {"code":"002415","market":"0","name":"海康威视","industry":"计算机"},
    {"code":"300454","market":"0","name":"深信服","industry":"计算机"},
    {"code":"600031","market":"1","name":"三一重工","industry":"机械设备"},
    {"code":"300124","market":"0","name":"汇川技术","industry":"机械设备"},
    {"code":"300896","market":"0","name":"爱美客","industry":"美容护理"},
    {"code":"603605","market":"1","name":"珀莱雅","industry":"美容护理"},
    {"code":"601888","market":"1","name":"中国中免","industry":"商贸零售"},
    {"code":"600900","market":"1","name":"长江电力","industry":"公用事业"},
    {"code":"601088","market":"1","name":"中国神华","industry":"煤炭"},
    {"code":"601857","market":"1","name":"中国石油","industry":"石油石化"},
    {"code":"300498","market":"0","name":"温氏股份","industry":"农林牧渔"},
    {"code":"600585","market":"1","name":"海螺水泥","industry":"建筑材料"},
    {"code":"601668","market":"1","name":"中国建筑","industry":"建筑装饰"},
    {"code":"601111","market":"1","name":"中国国航","industry":"交通运输"},
    {"code":"300418","market":"0","name":"昆仑万维","industry":"传媒"},
    {"code":"002032","market":"0","name":"苏泊尔","industry":"家用电器"},
    {"code":"600760","market":"1","name":"中航沈飞","industry":"国防军工"},
    {"code":"601919","market":"1","name":"中远海控","industry":"交通运输"},
]


# ═══════════════════════════════════════════════
# 快速管线（与 run_tail_market_1430.py 一致）
# ═══════════════════════════════════════════════

def fast_pipeline(code, name, industry, daily_closes, today_close, change_pct, today_vol, avg_vol_5, pe=None):
    """单股快速管线"""
    n = len(daily_closes)
    l1_passed = False; l2_passed = False; l3_passed = True

    # L1: T-1 尾盘初筛
    l1_score = 0.0
    if change_pct > 2: l1_score += 0.15
    elif change_pct < -3: l1_score += 0.10
    if avg_vol_5 > 0 and today_vol > avg_vol_5 * 1.2: l1_score += 0.15
    if change_pct > 5: l1_score += 0.10
    if n >= 5:
        ma5 = sma(daily_closes, 5)[-1]
        if abs(today_close - ma5) / ma5 < 0.03: l1_score += 0.10
    l1_passed = l1_score >= 0.20

    # L2: 形态检测
    l2_score = 0.0
    if n >= 60:
        peak60 = max(daily_closes[-60:])
        dd = (peak60 - today_close) / peak60
        if dd > 0.15: l2_score += 0.15
        if dd > 0.25: l2_score += 0.10
    if n >= 20:
        ma20 = sma(daily_closes, 20)[-1]
        if today_close > ma20: l2_score += 0.10
    if n >= 5:
        ma5 = sma(daily_closes, 5)[-1]
        ma10 = sma(daily_closes, 10)[-1] if n >= 10 else ma5
        if ma5 > ma10: l2_score += 0.10
    if avg_vol_5 > 0 and today_vol > avg_vol_5 * 1.8: l2_score += 0.12
    l2_passed = l2_score >= 0.25

    # L3: 排雷
    l3_score = 1.0
    if pe is not None and pe <= 0: l3_score -= 0.15
    if pe is not None and pe > 80: l3_score -= 0.10
    if today_close < 3: l3_score -= 0.05
    if n >= 5:
        drops = sum(1 for i in range(-4,0) if daily_closes[i+1] < daily_closes[i])
        if drops >= 3: l3_score -= 0.10
    l3_passed = l3_score >= 0.70

    # L4: 7权重融合（简化版）
    WEIGHTS = {"tech":0.20,"capital":0.18,"sentiment":0.15,"fundamental":0.15,"industry":0.12,"event":0.10,"game":0.10}
    tech = max(10, min(95, (l1_score*0.5+l2_score*0.5)*200))
    capital = 70 if change_pct>3 else (60 if change_pct>0 else 40)
    sentiment = 75 if l2_passed else (55 if l1_passed else 40)
    fund = 70 if (pe and 10<pe<30) else (55 if pe and 30<=pe<50 else 35)
    ind_score = 65
    event = 50
    game = 70 if l2_passed else 50
    fusion = (tech*0.20+capital*0.18+sentiment*0.15+fund*0.15+65*0.12+50*0.10+game*0.10)
    
    # M46: 贝叶斯
    m46 = fusion/100*0.5 + 0.25 + (0.1 if l2_passed else 0)
    
    return {
        "fusion_score": round(fusion,1),
        "m46_prob": round(m46,2),
        "l1_passed": l1_passed, "l2_passed": l2_passed,
        "l3_passed": l3_passed, "action": "BUY" if fusion>=42 and l3_passed else "PASS"
    }


# ═══════════════════════════════════════════════
# 回测引擎
# ═══════════════════════════════════════════════

@dataclass
class BacktestResult:
    code: str; name: str; industry: str
    total_signals: int = 0      # 总产生的买入信号
    hit_count: int = 0          # 命中涨停数
    miss_count: int = 0         # 产生信号但未涨停
    false_negative: int = 0     # 未产生信号但涨停了（漏选）
    total_days: int = 0         # 回测总天数
    precision: float = 0.0      # 命中率
    recall: float = 0.0         # 召回率
    avg_return: float = 0.0     # T+1平均收益
    max_return: float = 0.0     # 最大单日收益
    min_return: float = 0.0     # 最大单日亏损
    pl_ratio: float = 0.0       # 盈亏比
    m46_prior_optimal: float = 0.25  # 最优先验值

def run_backtest_on_stock(code, name, industry, klines, pe=None, min_days=40):
    """
    对单只股票运行60日滚动回测。
    klines: [{date, o, h, l, c, v, ...}, ...] 按时间正序
    """
    if len(klines) < min_days:
        return None
    
    result = BacktestResult(code=code, name=name, industry=industry)
    closes = [k["c"] for k in klines]
    volumes = [k.get("v", 0) for k in klines]
    highs = [k.get("h", k["c"]) for k in klines]
    
    daily_returns = []  # T+1收益
    signals_produced = 0
    hits = 0
    limit_up_count = 0  # 实际涨停总次数
    
    # 从第40天开始（确保有足够的历史数据），到倒数第1天（需要T+1验证）
    for t in range(min_days - 1, len(klines) - 1):
        # T日数据
        today_close = closes[t]
        today_open = klines[t].get("o", today_close)
        today_vol = volumes[t]
        today_high = highs[t]
        
        # 前5日均量
        if t >= 5:
            avg_vol_5 = sum(volumes[t-5:t]) / 5
        else:
            avg_vol_5 = sum(volumes[:t+1]) / (t+1)
        
        # T日涨跌幅
        prev_close = closes[t-1] if t > 0 else today_open
        change_pct = (today_close - prev_close) / prev_close * 100 if prev_close else 0
        
        # T+1日验证
        t1_open = klines[t+1].get("o", closes[t+1])
        t1_close = closes[t+1]
        t1_high = highs[t+1]
        t1_return = (t1_close - today_close) / today_close * 100
        
        # 跑管线
        hist_closes = closes[:t+1]
        signal = fast_pipeline(code, name, industry, hist_closes, today_close,
                               change_pct, today_vol, avg_vol_5, pe)
        
        # T+1是否涨停？
        t1_limit_up = (t1_high >= today_close * 1.095) or (t1_close >= today_close * 1.09)
        if t1_limit_up:
            limit_up_count += 1
        
        if signal["action"] == "BUY":
            signals_produced += 1
            daily_returns.append(t1_return)
            if t1_limit_up:
                hits += 1
            else:
                result.miss_count += 1
        else:
            # 没产生信号但T+1涨停了 → 漏选
            if t1_limit_up:
                result.false_negative += 1
    
    result.total_days = len(klines) - min_days - 1
    result.total_signals = signals_produced
    result.hit_count = hits
    
    if signals_produced > 0:
        result.precision = round(hits/signals_produced, 4)
        result.avg_return = round(sum(daily_returns)/len(daily_returns), 2)
        result.max_return = round(max(daily_returns), 2)
        result.min_return = round(min(daily_returns), 2)
        # 盈亏比：平均盈利/平均亏损（绝对值）
        gains = [r for r in daily_returns if r > 0]
        losses = [abs(r) for r in daily_returns if r < 0]
        if losses and gains:
            result.pl_ratio = round((sum(gains)/len(gains))/(sum(losses)/len(losses)), 2)
    
    total_lu = limit_up_count
    if total_lu > 0:
        result.recall = round(hits/total_lu, 4)
    
    # 计算最优M46先验：使命中率最大化的先验值
    if signals_produced >= 5:
        result.m46_prior_optimal = round(max(0.10, min(0.40, result.precision * 0.7 + 0.15)), 4)
    
    return result


# ═══════════════════════════════════════════════
# 行业先验校准
# ═══════════════════════════════════════════════

def calibrate_industry_priors(results: List[BacktestResult]) -> Dict[str, float]:
    """根据回测结果计算每个行业的M46贝叶斯先验值"""
    by_ind = defaultdict(list)
    for r in results:
        if r and r.total_signals >= 3:  # 至少3个信号才有统计意义
            by_ind[r.industry].append(r)
    
    priors = {}
    global_avg_precision = 0
    total_r = [r for r in results if r and r.total_signals >= 3]
    if total_r:
        global_avg_precision = sum(r.precision for r in total_r) / len(total_r)
    
    # 基准先验 = 全局平均命中率
    base_prior = max(0.10, min(0.35, global_avg_precision))
    
    for ind, rs in sorted(by_ind.items()):
        avg_p = sum(r.precision for r in rs) / len(rs)
        # 向全局均值回归：行业命中率×0.7 + 基准×0.3
        calibrated = avg_p * 0.7 + base_prior * 0.3
        priors[ind] = round(max(0.08, min(0.40, calibrated)), 4)
    
    return priors


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def main(top_n=50, backtest_days=60, output_path=None, verbose=True):
    t0 = time.time()
    
    print("=" * 72)
    print(f"  📊 V13.0 P0-1: {top_n}股批量回测 + M46行业先验校准")
    print(f"  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  回测窗口: {backtest_days}交易日 | 目标标的: {top_n}只")
    print("=" * 72)
    
    # 1. 加载监控池
    watchlist = load_watchlist(top_n)
    print(f"\n📋 加载监控池: {len(watchlist)}只")
    
    # 2. 尝试加载TDX缓存K线数据
    tdx_cache = {}
    cache_path = os.path.join(os.path.dirname(__file__) or ".", "data", "tdx_realtime_input.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
            for code, sdata in cache_data.get("stocks", {}).items():
                klines = sdata.get("daily_klines", [])
                if len(klines) >= MIN_KLINES:
                    tdx_cache[code] = {
                        "klines": klines, "pe": sdata.get("pe"),
                        "market": sdata.get("market", "0")
                    }
            if tdx_cache:
                print(f"📡 TDX缓存命中: {len(tdx_cache)}只有效K线数据")
        except Exception as e:
            print(f"⚠️ TDX缓存读取异常: {e}")
    
    # 3. 生成合成K线（当TDX数据不可用时）
    if not tdx_cache:
        print("⚠️ TDX缓存无数据，使用合成K线进行测试验证")
        import random
        random.seed(42)
    
    # 4. 逐股回测
    results = []
    available = 0
    for i, s in enumerate(watchlist):
        code, market, name, ind = s["code"], s["market"], s["name"], s["industry"]
        
        if code in tdx_cache:
            klines = tdx_cache[code]["klines"]
            pe = tdx_cache[code].get("pe")
            available += 1
        else:
            # 合成K线（60天+40天=100天历史数据）
            import random as rnd
            seed = int(code) * 7 % 10000
            rnd.seed(seed)
            base = rnd.uniform(20, 500)
            klines = []
            price = base
            for d in range(backtest_days + MIN_KLINES):
                chg = rnd.uniform(-0.04, 0.04)
                # 偶尔有涨停
                if rnd.random() < 0.03: chg = rnd.uniform(0.05, 0.10)
                if rnd.random() < 0.015: chg = rnd.uniform(-0.07, -0.05)
                price = max(3, price * (1 + chg))
                vol = int(rnd.uniform(100000, 10000000))
                klines.append({"c": round(price,2), "o": round(price*(1+rnd.uniform(-0.02,0.02)),2),
                              "h": round(price*(1+abs(rnd.uniform(0,0.04))),2),
                              "l": round(price*(1-abs(rnd.uniform(0,0.03))),2),
                              "v": vol})
            pe = rnd.uniform(5, 80)
        
        r = run_backtest_on_stock(code, name, ind, klines, pe, MIN_KLINES)
        if r:
            results.append(r)
            
            if verbose and (i < 10 or r.total_signals > 0):
                tag = "🔥" if r.precision >= 0.3 else ("✅" if r.total_signals > 0 else "⚪")
                print(f"  {tag} [{code}] {name:8s} | {ind:6s} | 信号{r.total_signals:2d} | "
                      f"命中{r.hit_count:2d} | P={r.precision:.1%} | R={r.recall:.1%} | "
                      f"收益{r.avg_return:+.1f}% | PLR={r.pl_ratio:.1f}")
    
    # 5. 汇总统计
    total_signals = sum(r.total_signals for r in results)
    total_hits = sum(r.hit_count for r in results)
    total_missed = sum(r.false_negative for r in results)
    
    global_precision = round(total_hits / total_signals, 4) if total_signals > 0 else 0
    global_recall = round(total_hits / (total_hits + total_missed), 4) if (total_hits + total_missed) > 0 else 0
    
    avg_returns = [r.avg_return for r in results if r.total_signals > 0]
    global_avg_return = round(sum(avg_returns)/len(avg_returns), 2) if avg_returns else 0
    
    pl_ratios = [r.pl_ratio for r in results if r.total_signals >= 3]
    global_plr = round(sum(pl_ratios)/len(pl_ratios), 2) if pl_ratios else 0
    
    # 6. 行业先验校准
    industry_priors = calibrate_industry_priors(results)
    
    elapsed = time.time() - t0
    
    # 7. 输出汇总
    print(f"\n{'─'*72}")
    print(f"  📊 回测汇总:")
    print(f"  有效股票: {len(results)}/{top_n} | 数据来源: {'TDX实盘' if tdx_cache else '合成数据'}")
    print(f"  总信号: {total_signals} | 命中: {total_hits} | 漏选: {total_missed}")
    print(f"  全局命中率: {global_precision:.1%} | 全局召回率: {global_recall:.1%}")
    print(f"  平均T+1收益: {global_avg_return:+.1f}% | 平均盈亏比: {global_plr:.1f}")
    
    # 按行业分组
    by_ind = defaultdict(list)
    for r in results:
        by_ind[r.industry].append(r)
    
    print(f"\n  📈 行业分组统计 ({len(by_ind)}个行业):")
    print(f"  {'行业':12s} {'股票':>4s} {'信号':>4s} {'命中':>4s} {'命中率':>6s} {'收益':>6s} {'M46先验':>8s}")
    print(f"  {'─'*50}")
    for ind, rs in sorted(by_ind.items()):
        sigs = sum(r.total_signals for r in rs)
        hits = sum(r.hit_count for r in rs)
        p = round(hits/sigs, 3) if sigs > 0 else 0
        rets = [r.avg_return for r in rs if r.total_signals > 0]
        avg_ret = round(sum(rets)/len(rets), 1) if rets else 0
        prior = industry_priors.get(ind, 0.25)
        print(f"  {ind:12s} {len(rs):>4d} {sigs:>4d} {hits:>4d} {p:>6.1%} {avg_ret:>+5.1f}% {prior:>8.4f}")
    
    print(f"\n  ⏱️ 回测耗时: {elapsed:.1f}秒")
    
    # 8. 保存结果
    output = {
        "run_time": datetime.now().isoformat(),
        "top_n": top_n, "backtest_days": backtest_days,
        "data_source": "TDX_REAL" if tdx_cache else "SYNTHETIC",
        "elapsed_sec": round(elapsed, 1),
        "global_stats": {
            "total_stocks": len(results), "total_signals": total_signals,
            "total_hits": total_hits, "total_missed": total_missed,
            "precision": global_precision, "recall": global_recall,
            "avg_return": global_avg_return, "pl_ratio": global_plr,
        },
        "industry_priors": industry_priors,
        "top_signals": sorted(
            [{"code":r.code, "name":r.name, "industry":r.industry,
              "signals":r.total_signals, "hits":r.hit_count,
              "precision":r.precision, "pl_ratio":r.pl_ratio,
              "avg_return":r.avg_return, "m46_prior":r.m46_prior_optimal}
             for r in results if r.total_signals >= 3],
            key=lambda x: x["precision"], reverse=True
        )[:20],
    }
    
    if output_path is None:
        output_path = os.path.join(os.path.dirname(__file__) or ".", "data", "backtest_result.json")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  📁 结果已保存: {output_path}")
    
    return output


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V13.0 批量回测 + M46校准")
    parser.add_argument("--top", type=int, default=50, help="回测股票数量")
    parser.add_argument("--days", type=int, default=60, help="回测窗口天数")
    parser.add_argument("--output", type=str, default=None, help="输出路径")
    parser.add_argument("--quiet", action="store_true", help="简洁模式")
    args = parser.parse_args()
    main(top_n=args.top, backtest_days=args.days, output_path=args.output, verbose=not args.quiet)
