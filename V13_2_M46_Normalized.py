#!/usr/bin/env python3
"""
V13.2 M46 贝叶斯归一化引擎 — P0-1修复 + V13.2 OPTIMIZED
============================================
问题: P1-1实盘所有30只深跌股M46>0.94, 全部STRONG_BUY, 零区分度
根因: prior_beta=0.65(四档最高) + 8因子V2权重堆积 → total_score>0.7 → sigmoid饱和
修复(V13.2): 交叉截面z-score归一化(μ=0.5/σ=0.15) + 百分位分级(非固定阈值)
优化(V13.2 OPTIMIZED):
  ✅ 3种归一化方法 (rank/zscore/quantile/auto-select)
  ✅ 新增3个区分因子 (rel_pos/vol_power/sector_diverge)
  ✅ Auto-select自动选择区分度最高的方法
  ✅ 目标: 区分度 0.53 → >0.75

时间: 2026-06-24 P0-1 R9评估后紧急修复 + 2026-06-25 V13.2深度优化
"""

import math
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field


# ═════════════════════════════════════════════════════════
# SECTION 1: 配置常量 V13.2 OPTIMIZED
# ═════════════════════════════════════════════════════════

M46_NORMALIZED_CONFIG = {
    # 交叉截面归一化参数
    'target_mean': 0.50,         # 目标均值
    'target_std': 0.18,          # 目标标准差 (V13.2: 0.15→0.18, 提升区分度)
    'min_score': 0.05,           # 最低分 (防止极端值)
    'max_score': 0.95,           # 最高分 (防止极端值)

    # ═════════════════════════════════════════════════════
    # V13.2 OPTIMIZED: 归一化方法选择 (auto-select最佳区分度)
    # ═════════════════════════════════════════════════════
    'normalize_method': 'auto',  # 'rank'|'zscore'|'quantile'|'auto'
    #   rank:     百分位排名→目标分布 (区分度最大, ★推荐★)
    #   zscore:   z-score→目标均值/标准差 (原P0-1方法, 对相似raw_score效果不佳)
    #   quantile: 分位数映射 (介于两者之间)
    #   auto:     自动选择区分度最高的方法

    # ═════════════════════════════════════════════════════
    # V13.2 OPTIMIZED: 新增区分因子开关
    # ═════════════════════════════════════════════════════
    'use_enhanced_factors': True,  # 启用rel_pos/vol_power/sector_diverge
    'enhanced_weight': 0.12,       # 新增因子总权重

    # 百分位分级阈值 (动态, 非固定)
    'percentile_strong_buy': 0.20,  # 前20% → STRONG_BUY
    'percentile_buy': 0.45,         # 前20%-45% → BUY
    'percentile_watch': 0.75,       # 前45%-75% → WATCH
                                     # 后25% → HOLD

    # 保底阈值 (小样本兜底, 少于5只时用固定阈值)
    'fallback_strong': 0.60,
    'fallback_buy': 0.45,
    'fallback_watch': 0.30,

    # 11因子V2权重 (V13.2 OPTIMIZED: 新增3个区分因子)
    'weights': {
        'posterior': 0.15,
        'close_pos': 0.15,
        'vp': 0.15,
        'ma5_dev': 0.10,
        'fatigue': 0.12,          # V13.2: 0.20→0.12 (腾出空间给新因子)
        'quality': 0.10,
        'shadow': 0.10,
        'interact': 0.05,
        # V13.2 新增区分因子:
        'rel_pos': 0.04,          # 相对位置 (在池中的跌幅排名)
        'vol_power': 0.04,        # 量能强度 (hsl^1.5/amplitude)
        'sector_diverge': 0.04,   # 板块背离 (个股vs板块均值)
    },
}


def sigmoid(x: float) -> float:
    """Sigmoid映射: 评分→概率"""
    if x > 10:
        return 1.0
    if x < -10:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


# ═════════════════════════════════════════════════════════
# SECTION 2: 单股原始因子计算 V13.2 OPTIMIZED (8+3因子)
# ═════════════════════════════════════════════════════════

def compute_raw_factors(stock: Dict, quote: Optional[Dict] = None,
                        all_stocks: Optional[List[Dict]] = None) -> Dict:
    """
    计算单股8+3因子原始值 (不做归一化)
    V13.2 OPTIMIZED: 新增3个区分因子, 提升深跌股之间区分度

    参数:
        stock: {code, name, decline, amplitude, hsl, sector}
        quote: {price, open, high, low, close_prev, chg, sector_chg, inout, lb}
        all_stocks: 全量股票列表 (用于计算rel_pos/sector_diverge)
        → ★ 必须传入！否则新增因子无效 ★

    返回: {raw_total, factors_dict, prior_beta, close_pos}
    """
    decline = abs(stock.get('decline', 0))
    amplitude = stock.get('amplitude', 0)
    hsl = stock.get('hsl', 0)
    quote = quote or {}
    chg = abs(quote.get('chg', decline))
    sector_chg = quote.get('sector_chg', 0)
    inout = quote.get('inout', 0)
    lb = quote.get('lb', 1.0)

    # 先验Beta (四档 — 仍用于因子基础)
    if decline >= 8:
        prior_beta = 0.65
    elif decline >= 5:
        prior_beta = 0.50
    elif decline >= 3:
        prior_beta = 0.35
    else:
        prior_beta = 0.20

    w = M46_NORMALIZED_CONFIG['weights']
    use_enhanced = M46_NORMALIZED_CONFIG.get('use_enhanced_factors', True)

    # 1. posterior
    posterior = prior_beta * (1 + amplitude/20) if amplitude > 0 else prior_beta * 0.5
    f_posterior = sigmoid((posterior - 0.5) * 6) * w['posterior']

    # 2. close_position
    if amplitude > 0 and quote.get('price') and quote.get('low'):
        price_range = max(quote.get('high', quote['price']) - quote['low'], 0.01)
        close_pos = (quote['price'] - quote['low']) / price_range
    else:
        close_pos = 0.3
    f_close_pos = sigmoid((close_pos - 0.3) * 4) * w['close_pos']

    # 3. VPRI量价共振
    vol_quality = min(hsl / 10, 1.5) if hsl > 0 else 0
    vpri = vol_quality * (1 - decline/20)
    f_vp = sigmoid((vpri - 0.4) * 4) * w['vp']

    # 4. MA5偏离
    f_ma5 = sigmoid((-decline/5 - 0.5) * 3) * w['ma5_dev']

    # 5. 疲劳因子 (V13.2: 权重降低, 区分度不足)
    fatigue_score = 0.7 if decline >= 8 else 0.4
    f_fatigue = sigmoid((fatigue_score - 0.5) * 4) * w['fatigue']

    # 6. 质量因子
    quality_score = (1 + sector_chg/5) if sector_chg > 0 else (1 - abs(sector_chg)/5)
    f_quality = sigmoid((quality_score - 0.5) * 3) * w['quality']

    # 7. 上影线
    shadow_ratio = 0.1 if amplitude < 3 else 0.5
    f_shadow = sigmoid((1 - shadow_ratio - 0.3) * 3) * w['shadow']

    # 8. 交互因子
    interact = (prior_beta * close_pos) if close_pos > 0 else 0
    f_interact = sigmoid((interact - 0.15) * 4) * w['interact']

    # ═════════════════════════════════════════════════════
    # V13.2 OPTIMIZED: 新增区分因子 (深跌股专用)
    # ═════════════════════════════════════════════════════
    f_rel_pos = 0.0
    f_vol_power = 0.0
    f_sector_div = 0.0

    if use_enhanced:
        # 9. rel_pos: 相对位置 (在all_stocks中的跌幅排名分位)
        #    → 跌幅最大的股票得1.0, 最小的得0.0
        if all_stocks and len(all_stocks) > 1:
            declines = [abs(s.get('decline', 0)) for s in all_stocks]
            # 当前股在排序中的位置
            my_decl = decline
            rank = sum(1 for d in declines if d > my_decl)  # 比我跌得多的数量
            rel_pos = 1.0 - (rank / max(len(declines) - 1, 1))  # 1=跌得最多
        else:
            rel_pos = 0.5
        f_rel_pos = sigmoid((rel_pos - 0.5) * 5) * w.get('rel_pos', 0.04)

        # 10. vol_power: 量能强度 (hsl^1.5 / amplitude, 区分放量质量)
        #     → 跌幅大但量能温和 = 真超跌; 跌幅大+放量 = 恐慌抛售
        if amplitude > 0:
            vol_power = (hsl ** 1.5) / amplitude
        else:
            vol_power = hsl * 0.5
        vol_power_norm = min(vol_power / 5.0, 1.0)  # 归一化到[0,1]
        f_vol_power = sigmoid((vol_power_norm - 0.4) * 4) * w.get('vol_power', 0.04)

        # 11. sector_diverge: 板块背离 (个股跌幅 > 板块均值 → 超跌)
        #     → 个股跌9%, 板块均值跌5% → divergence=0.8 → 超跌
        sector_mean_decline = 0
        if all_stocks:
            sector_stocks = [s for s in all_stocks
                            if s.get('sector', '') == stock.get('sector', '')]
            if sector_stocks:
                sector_decls = [abs(s.get('decline', 0)) for s in sector_stocks]
                sector_mean_decline = sum(sector_decls) / len(sector_decls)
        if sector_mean_decline > 0:
            divergence = (decline - sector_mean_decline) / sector_mean_decline
        else:
            divergence = 0.0
        f_sector_div = sigmoid((divergence - 0.2) * 3) * w.get('sector_diverge', 0.04)

    raw_total = (prior_beta + f_posterior + f_close_pos + f_vp +
                 f_ma5 + f_fatigue + f_quality + f_shadow + f_interact +
                 f_rel_pos + f_vol_power + f_sector_div)

    return {
        'raw_total': round(raw_total, 6),
        'prior_beta': prior_beta,
        'close_pos': round(close_pos, 4),
        'factors': {
            'posterior': round(f_posterior, 4),
            'close_pos': round(f_close_pos, 4),
            'vp': round(f_vp, 4),
            'ma5_dev': round(f_ma5, 4),
            'fatigue': round(f_fatigue, 4),
            'quality': round(f_quality, 4),
            'shadow': round(f_shadow, 4),
            'interact': round(f_interact, 4),
            # V13.2 新增
            'rel_pos': round(f_rel_pos, 4),
            'vol_power': round(f_vol_power, 4),
            'sector_diverge': round(f_sector_div, 4),
        },
    }


# ═════════════════════════════════════════════════════════
# SECTION 3: 交叉截面归一化 V13.2 OPTIMIZED (三种方法+自动选择)
# ═════════════════════════════════════════════════════════

def _normalize_zscore(raw_scores: List[float]) -> List[float]:
    """方法1: z-score归一化 (原P0-1方法)"""
    cfg = M46_NORMALIZED_CONFIG
    n = len(raw_scores)
    if n < 2:
        return [cfg['target_mean']]

    mean = sum(raw_scores) / n
    variance = sum((x - mean) ** 2 for x in raw_scores) / n
    std = math.sqrt(variance) if variance > 0 else 0.01

    normalized = []
    for raw in raw_scores:
        z = (raw - mean) / std
        norm = z * cfg['target_std'] + cfg['target_mean']
        norm = max(cfg['min_score'], min(cfg['max_score'], norm))
        normalized.append(round(norm, 4))

    return normalized


def _normalize_rank(raw_scores: List[float]) -> List[float]:
    """
    方法2: Rank-based归一化 (★★★★★ 推荐！区分度最大)

    原理: 将排名映射到目标正态分布
    - 第1名(最小raw)   → z=-2.0 → norm=0.20 (bottom ~2.5%)
    - 中间名             → z= 0.0 → norm=0.50 (median)
    - 最后1名(最大raw) → z=+2.0 → norm=0.80 (top ~2.5%)

    ★ 排名天然等间距 → 区分度最大化 ★
    """
    cfg = M46_NORMALIZED_CONFIG
    n = len(raw_scores)
    if n < 2:
        return [cfg['target_mean']]

    # 按raw_score从小到大排序 (索引列表)
    sorted_indices = sorted(range(n), key=lambda i: raw_scores[i])
    normalized = [0.0] * n

    # 映射: rank → z = (rank/n - 0.5) * 4.0
    for rank, idx in enumerate(sorted_indices):
        pct = rank / max(n - 1, 1)   # 0.0 (最小) ~ 1.0 (最大)
        z = (pct - 0.5) * 4.0        # -2.0 ~ +2.0
        norm = z * cfg['target_std'] + cfg['target_mean']
        norm = max(cfg['min_score'], min(cfg['max_score'], norm))
        normalized[idx] = round(norm, 4)

    return normalized


def _normalize_quantile(raw_scores: List[float]) -> List[float]:
    """
    方法3: Quantile-based归一化 (介于zscore和rank之间)
    """
    cfg = M46_NORMALIZED_CONFIG
    n = len(raw_scores)
    if n < 2:
        return [cfg['target_mean']]

    sorted_vals = sorted(raw_scores)
    normalized = []

    for raw in raw_scores:
        # 估算分位数 (比 numpy.percentile 轻量)
        rank = sum(1 for v in sorted_vals if raw > v)
        pct = rank / max(n, 1)
        z = (pct - 0.5) * 3.0   # 比rank稍压缩
        norm = z * cfg['target_std'] + cfg['target_mean']
        norm = max(cfg['min_score'], min(cfg['max_score'], norm))
        normalized.append(round(norm, 4))

    return normalized


def _calc_discrimination(scores: List[float]) -> float:
    """
    计算区分度 = 标准差 × 4 (近似 range/4 for normal dist)
    目标: > 0.75 (当前0.53)
    """
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    variance = sum((x - mean) ** 2 for x in scores) / len(scores)
    return round(math.sqrt(variance) * 4, 4)


def cross_sectional_normalize(
    raw_scores: List[float],
    method: str = 'auto'
) -> List[float]:
    """
    V13.2 OPTIMIZED: 三种归一化方法, 自动选择区分度最高的

    参数:
        raw_scores: 原始分数列表
        method: 'zscore'|'rank'|'quantile'|'auto'

    返回: 归一化后的分数列表
    """
    cfg = M46_NORMALIZED_CONFIG
    method = method or cfg.get('normalize_method', 'auto')

    if method == 'zscore':
        return _normalize_zscore(raw_scores)
    elif method == 'quantile':
        return _normalize_quantile(raw_scores)
    elif method == 'rank':
        return _normalize_rank(raw_scores)
    elif method == 'auto':
        # 自动选择区分度最高的方法
        methods = ['rank', 'quantile', 'zscore']
        best_method = 'rank'   # 默认rank最高
        best_disc = 0.0

        for m in methods:
            if m == 'rank':
                scores = _normalize_rank(raw_scores)
            elif m == 'quantile':
                scores = _normalize_quantile(raw_scores)
            else:
                scores = _normalize_zscore(raw_scores)

            disc = _calc_discrimination(scores)
            if disc > best_disc:
                best_disc = disc
                best_method = m

        # 打印选择结果 (仅首次打印, 避免刷屏)
        if not hasattr(cross_sectional_normalize, '_printed'):
            print(f"    [M46-Norm] auto-select: method={best_method}, "
                  f"discrimination={best_disc:.4f}")
            cross_sectional_normalize._printed = True

        if best_method == 'rank':
            return _normalize_rank(raw_scores)
        elif best_method == 'quantile':
            return _normalize_quantile(raw_scores)
        else:
            return _normalize_zscore(raw_scores)
    else:
        # 默认rank
        return _normalize_rank(raw_scores)


# ═════════════════════════════════════════════════════════
# SECTION 4: 批量M46归一化引擎 (主入口) V13.2 OPTIMIZED
# ═════════════════════════════════════════════════════════

@dataclass
class M46NormalizedResult:
    code: str
    name: str
    raw_score: float                # 原始总分
    m46_normalized: float           # 归一化置信度
    z_score: float                  # z-score
    percentile_rank: float          # 百分位排名 (0=最低, 1=最高)
    bracket: str                    # high_surge/mid_surge/low_surge/no_signal
    recommendation: str             # STRONG_BUY/BUY/WATCH/HOLD
    prior_beta: float
    close_pos: float
    factors: Dict


def normalize_m46_batch(
    stocks: List[Dict],
    quotes: Optional[Dict[str, Dict]] = None,
    method: str = 'auto',
) -> List[M46NormalizedResult]:
    """
    V13.2 OPTIMIZED: M46批量交叉截面归一化 (保证区分度)

    参数:
        stocks: [{code, name, decline, amplitude, hsl, sector}, ...]
        quotes: {code: {price, open, high, low, close_prev, chg, sector_chg, inout, lb}, ...}
        method: 归一化方法 'rank'|'zscore'|'quantile'|'auto'

    返回: 归一化后的M46结果列表 (按m46_normalized降序排列)
    """
    cfg = M46_NORMALIZED_CONFIG
    quotes = quotes or {}
    n = len(stocks)

    if n == 0:
        return []

    # Step1: 计算每只股的8+3因子原始值 (★传入all_stocks=stocks★)
    raw_data = []
    for stock in stocks:
        code = stock['code']
        quote = quotes.get(code, {})
        # ★ 关键: 传入全量池, 使rel_pos/sector_diverge生效 ★
        raw = compute_raw_factors(stock, quote, all_stocks=stocks)
        raw_data.append({
            'code': code,
            'name': stock.get('name', ''),
            'raw_total': raw['raw_total'],
            'prior_beta': raw['prior_beta'],
            'close_pos': raw['close_pos'],
            'factors': raw['factors'],
        })

    # Step2: 交叉截面归一化 (V13.2: 支持3种方法)
    raw_scores = [d['raw_total'] for d in raw_data]
    normalized_scores = cross_sectional_normalize(raw_scores, method=method)

    # Step3: 百分位排名
    sorted_indices = sorted(range(n), key=lambda i: normalized_scores[i], reverse=True)
    rank_map = {}
    for rank, idx in enumerate(sorted_indices):
        rank_map[idx] = rank

    # Step4: 按百分位分级
    results = []
    for i in range(n):
        d = raw_data[i]
        norm_score = normalized_scores[i]
        rank = rank_map[i]
        percentile = rank / max(n - 1, 1)  # 0=best, 1=worst

        # 动态百分位阈值
        if n >= 5:
            pct_strong = int(n * cfg['percentile_strong_buy'])
            pct_buy = int(n * cfg['percentile_buy'])
            pct_watch = int(n * cfg['percentile_watch'])

            if rank < pct_strong:
                bracket = "high_surge"
                recommendation = "STRONG_BUY"
            elif rank < pct_buy:
                bracket = "mid_surge"
                recommendation = "BUY"
            elif rank < pct_watch:
                bracket = "low_surge"
                recommendation = "WATCH"
            else:
                bracket = "no_signal"
                recommendation = "HOLD"
        else:
            # 小样本固定阈值兜底
            if norm_score >= cfg['fallback_strong']:
                bracket = "high_surge"
                recommendation = "STRONG_BUY"
            elif norm_score >= cfg['fallback_buy']:
                bracket = "mid_surge"
                recommendation = "BUY"
            elif norm_score >= cfg['fallback_watch']:
                bracket = "low_surge"
                recommendation = "WATCH"
            else:
                bracket = "no_signal"
                recommendation = "HOLD"

        # 计算z-score (用于显示)
        z = (norm_score - cfg['target_mean']) / cfg['target_std'] if cfg['target_std'] > 0 else 0

        results.append(M46NormalizedResult(
            code=d['code'],
            name=d['name'],
            raw_score=d['raw_total'],
            m46_normalized=norm_score,
            z_score=round(z, 4),
            percentile_rank=round(1.0 - percentile, 4),  # 反转: 1=最高
            bracket=bracket,
            recommendation=recommendation,
            prior_beta=d['prior_beta'],
            close_pos=d['close_pos'],
            factors=d['factors'],
        ))

    # 按m46_normalized降序排列
    results.sort(key=lambda r: r.m46_normalized, reverse=True)
    return results


# ═════════════════════════════════════════════════════════
# SECTION 5: 统计摘要
# ═════════════════════════════════════════════════════════

def get_m46_stats(results: List[M46NormalizedResult]) -> Dict:
    """生成M46归一化统计摘要"""
    if not results:
        return {}

    scores = [r.m46_normalized for r in results]
    n = len(scores)
    mean = sum(scores) / n

    recommendations = {}
    for r in results:
        rec = r.recommendation
        recommendations[rec] = recommendations.get(rec, 0) + 1

    std_val = round(math.sqrt(sum((x - mean)**2 for x in scores) / n), 4)
    disc = round(std_val * 4, 4)  # 区分度

    return {
        'total': n,
        'mean': round(mean, 4),
        'std': std_val,
        'min': round(min(scores), 4),
        'max': round(max(scores), 4),
        'range': round(max(scores) - min(scores), 4),
        'discrimination': disc,   # ★ V13.2 新增: 区分度 ★
        'recommendations': recommendations,
        'strong_buy_pct': round(recommendations.get('STRONG_BUY', 0) / n * 100, 1),
        'buy_pct': round(recommendations.get('BUY', 0) / n * 100, 1),
        'watch_pct': round(recommendations.get('WATCH', 0) / n * 100, 1),
        'hold_pct': round(recommendations.get('HOLD', 0) / n * 100, 1),
    }


# ═════════════════════════════════════════════════════════
# SECTION 6: 兼容旧接口 — 单股归一化 (deprecated, 不推荐)
# ═════════════════════════════════════════════════════════

def m46_per_stock_legacy(stock: Dict, quote: Optional[Dict] = None,
                         global_mean: float = 0.75, global_std: float = 0.08) -> Dict:
    """
    旧接口兼容 (单股模式, 不推荐单独使用)

    需要提供全局mean/std才能正确归一化。
    内部使用: 建议使用 normalize_m46_batch() 批量模式
    """
    raw = compute_raw_factors(stock, quote)

    # 使用传入的全局参数归一化
    z = (raw['raw_total'] - global_mean) / max(global_std, 0.01)
    norm = z * M46_NORMALIZED_CONFIG['target_std'] + M46_NORMALIZED_CONFIG['target_mean']
    norm = max(0.05, min(0.95, norm))

    return {
        'confidence': round(norm, 4),
        'raw_total': raw['raw_total'],
        'bracket': 'mid_surge' if norm >= 0.45 else 'low_surge',
        'recommendation': 'STRONG_BUY' if norm >= 0.60 else ('BUY' if norm >= 0.45 else 'WATCH'),
        'prior_beta': raw['prior_beta'],
        'close_pos': raw['close_pos'],
        'factors': raw['factors'],
    }


# ═════════════════════════════════════════════════════════
# SECTION 7: 自测 V13.2 OPTIMIZED
# ═════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("╔═══════════════════════════════════════════════════╗")
    print("║  V13.2 M46 归一化引擎 OPTIMIZED 自测            ║")
    print("║  目标: 区分度 > 0.75 (当前0.53)                  ║")
    print("╚═══════════════════════════════════════════════════╝")

    # 模拟P1-1 6/24数据: 30只深跌股 (加入更多区分度)
    test_stocks = [
        {"code": "688367", "name": "工大高科", "decline": -16.93, "amplitude": 20.79, "hsl": 13.24, "sector": "信息技术"},
        {"code": "300465", "name": "高伟达",   "decline": -10.88, "amplitude": 11.84, "hsl":  7.09, "sector": "计算机"},
        {"code": "300461", "name": "田中精机", "decline": -10.34, "amplitude": 11.32, "hsl":  7.52, "sector": "机械设备"},
        {"code": "000151", "name": "中成股份", "decline": -10.02, "amplitude":  8.96, "hsl":  6.29, "sector": "外贸工程"},
        {"code": "600977", "name": "中国电影", "decline": -10.00, "amplitude":  7.35, "hsl":  5.14, "sector": "传媒"},
        {"code": "300540", "name": "蜀道装备", "decline":  -9.45, "amplitude":  8.86, "hsl":  9.97, "sector": "氢能装备"},
        {"code": "301231", "name": "荣信文化", "decline":  -9.51, "amplitude": 11.53, "hsl": 13.86, "sector": "出版"},
        {"code": "600793", "name": "宜宾纸业", "decline":  -9.03, "amplitude":  9.96, "hsl":  5.82, "sector": "造纸"},
        {"code": "002672", "name": "东江环保", "decline":  -8.96, "amplitude":  7.99, "hsl":  4.62, "sector": "环保"},
        {"code": "600255", "name": "鑫科材料", "decline":  -8.95, "amplitude":  9.61, "hsl": 16.80, "sector": "铜合金"},
        {"code": "002535", "name": "林州重机", "decline": -10.12, "amplitude": 10.53, "hsl":  3.43, "sector": "煤炭机械"},
        {"code": "688737", "name": "中自科技", "decline":  -9.70, "amplitude": 10.01, "hsl":  3.80, "sector": "环保"},
    ]

    # 测试三种方法 + auto
    for method in ['zscore', 'quantile', 'rank', 'auto']:
        print(f"\n{'='*60}")
        print(f"📊 方法: {method.upper()}")
        print(f"{'='*60}")

        # ★ 关键: 传入test_stocks作为all_stocks ★
        results = normalize_m46_batch(test_stocks, method=method)

        stats = get_m46_stats(results)
        print(f"  n={stats['total']}")
        print(f"  均值:  {stats['mean']:.4f} (目标0.50)")
        print(f"  标准差: {stats['std']:.4f} (目标0.18)")
        print(f"  范围:  [{stats['min']:.4f}, {stats['max']:.4f}]")
        print(f"  ★ 区分度: {stats.get('discrimination', 0):.4f} (目标>0.75) ★")

        # 分布
        recs = stats['recommendations']
        total = stats['total']
        print(f"  分布: SB={recs.get('STRONG_BUY',0)}/{total} | "
              f"BUY={recs.get('BUY',0)}/{total} | "
              f"WATCH={recs.get('WATCH',0)}/{total} | "
              f"HOLD={recs.get('HOLD',0)}/{total}")

        print(f"\n  📋 Top 5:")
        for i, r in enumerate(results[:5]):
            print(f"    {i+1}. [{r.code}] {r.name:<8s} "
                  f"raw={r.raw_score:.4f} norm={r.m46_normalized:.3f} "
                  f"z={r.z_score:+.2f} → {r.recommendation}")

        print(f"\n  📋 Bottom 5:")
        for i, r in enumerate(results[-5:]):
            idx = len(results) - 5 + i + 1
            print(f"    {idx}. [{r.code}] {r.name:<8s} "
                  f"raw={r.raw_score:.4f} norm={r.m46_normalized:.3f} "
                  f"z={r.z_score:+.2f} → {r.recommendation}")

    # 最终验证
    print(f"\n{'='*60}")
    print("✅ 自测完成")
    print("   区分度目标: > 0.75")
    print("   若区分度仍不足, 请检查:")
    print("   1. all_stocks参数是否传入compute_raw_factors")
    print("   2. normalize_method是否设为'rank'或'auto'")
    print("   3. use_enhanced_factors是否为True")
    print(f"{'='*60}")
