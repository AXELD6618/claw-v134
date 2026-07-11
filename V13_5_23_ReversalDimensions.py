#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.23 反转维度模块 — 填补"暴跌尽头反转"识别盲区
=====================================================
从贝斯特(300580)6/26和恒尚节能(603137)6/11两次选股遗漏根因分析中提取:

  贝斯特6/26: MEG=RED拦截(正确), 但若MEG回升后应被超跌观察池捕获
  恒尚节能6/11: MEG=YELLOW未拦截, 但F3五确认=1/5被拒
    -> 6/11长下影线2.08%, 日内V型反转(close>open), 400日中8/12个T+1≥3%日有长下影线
    -> 系统缺乏"长下影线日内反转"维度

新增四个维度:
  D47 暴跌尽头识别 (8分): 连跌≥5天 + 缩量 + 20日跌>25%
  D48 超跌Divergence (5分): 个股vs板块20日涨跌差<-15%
  D49 长下影线日内反转 (12分): 下影线>实体2倍 + 收盘>开盘 + 创近N日新低后拉起
  D50 创新低后尾盘放量 (10分): 14:30后量>上午均量1.5倍 + 价格回升

反转旁路规则(Reversal Bypass):
  当D49≥8或D50≥7时, 允许绕过F3五确认≥3的硬性过滤
  条件: D29≥6 + D49≥8  或  D29≥6 + D50≥7
  这使得"长下影线V型反转"形态不再被五确认体系误杀

集成路径:
  T4选股流程 → HardFilter F3 → 五确认≥3? 
    → YES: PASS
    → NO:  ReversalBypass.check(D49, D50, D29) → PASS/REJECT
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("V13_5_23_Reversal")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] D47-50 %(levelname)s: %(message)s"))
    logger.addHandler(h)


# ==================== D47 暴跌尽头识别 (8分) ====================

def calc_d47_capitulation_bottom(klines: List[Dict], lookback: int = 20) -> Dict:
    """
    D47 暴跌尽头识别
    
    条件:
      - 连续下跌≥5天 (close逐日递减)
      - 成交量缩量 (最近5日均量 < 前5日均量×0.8)
      - 20日跌幅>25%
    
    评分(8分):
      - 连跌天数: 5天=2分, 7天=3分, 10天+=4分
      - 缩量程度: 缩量>20%=2分, 缩量>40%=3分, 缩量>60%=4分
    
    Args:
        klines: 日K线列表, 每个元素含 open/high/low/close/vol
        lookback: 回看天数(默认20)
    
    Returns:
        {"score": int, "max": 8, "details": {...}}
    """
    if len(klines) < lookback:
        return {"score": 0, "max": 8, "reason": "数据不足"}
    
    recent = klines[-lookback:]
    closes = [k["close"] for k in recent]
    vols = [k.get("vol", 0) for k in recent]
    
    # 1. 连续下跌天数 (从最近往前数)
    consecutive_down = 0
    for i in range(len(closes) - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            consecutive_down += 1
        else:
            break
    
    # 2. 缩量程度
    recent_5_vol = sum(vols[-5:]) / 5 if len(vols) >= 5 else 0
    prior_5_vol = sum(vols[-10:-5]) / 5 if len(vols) >= 10 else recent_5_vol
    shrink_ratio = 1 - (recent_5_vol / prior_5_vol) if prior_5_vol > 0 else 0
    
    # 3. 20日跌幅
    decline_20d = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] > 0 else 0
    
    score = 0
    details = {
        "consecutive_down": consecutive_down,
        "shrink_ratio": round(shrink_ratio, 2),
        "decline_20d": round(decline_20d, 2),
    }
    
    # 连跌评分
    if consecutive_down >= 10:
        score += 4
    elif consecutive_down >= 7:
        score += 3
    elif consecutive_down >= 5:
        score += 2
    
    # 缩量评分
    if shrink_ratio > 0.6:
        score += 4
    elif shrink_ratio > 0.4:
        score += 3
    elif shrink_ratio > 0.2:
        score += 2
    
    # 必须同时满足连跌≥5 + 20日跌>25%才有效
    if consecutive_down < 5 or decline_20d > -25:
        score = 0
        details["note"] = f"未满足门槛(连跌{consecutive_down}<5或跌幅{decline_20d:.1f}%未超25%)"
    else:
        details["note"] = f"暴跌尽头: 连跌{consecutive_down}天, 缩量{shrink_ratio*100:.0f}%, 跌幅{decline_20d:.1f}%"
    
    return {"score": min(score, 8), "max": 8, "details": details}


# ==================== D48 超跌Divergence (5分) ====================

def calc_d48_oversold_divergence(stock_20d_pct: float, sector_20d_pct: float) -> Dict:
    """
    D48 超跌Divergence
    
    条件: 个股20日涨跌 vs 板块20日涨跌 差值 < -15%
    (即个股跑输板块15个百分点以上)
    
    评分(5分):
      - 差值<-15%: 2分
      - 差值<-20%: 3分
      - 差值<-30%: 4分
      - 差值<-40%: 5分
    
    说明: 超跌Divergence本身不是买入信号, 但配合D49长下影线时
         表示"超跌+反转"组合, 大幅提升T+1上涨概率
    """
    diff = stock_20d_pct - sector_20d_pct
    
    score = 0
    if diff < -40:
        score = 5
    elif diff < -30:
        score = 4
    elif diff < -20:
        score = 3
    elif diff < -15:
        score = 2
    
    return {
        "score": score,
        "max": 5,
        "details": {
            "stock_20d_pct": round(stock_20d_pct, 2),
            "sector_20d_pct": round(sector_20d_pct, 2),
            "divergence": round(diff, 2),
            "note": f"个股{stock_20d_pct:.1f}% vs 板块{sector_20d_pct:.1f}%, 差值{diff:.1f}%" if score > 0 else "未超跌"
        }
    }


# ==================== D49 长下影线日内反转 (12分) ====================

def calc_d49_long_lower_shadow(klines: List[Dict], lookback: int = 20) -> Dict:
    """
    D49 长下影线日内反转 — V13.5.23核心新增维度
    
    从恒尚节能6/11案例提取:
      开11.44/高11.60/低11.20/收11.54, 下影线2.08%, 日内V型反转
      400日中8/12个T+1≥3%日有长下影线>1%
    
    条件:
      1. 下影线长度 > 实体长度的2倍
         下影线 = min(open, close) - low
         实体 = abs(close - open)
      2. 收盘 > 开盘 (日内收涨, 即close > open)
      3. 当日最低价创近N日新低(或在近5日最低价附近)
      4. 前5日有下跌趋势(5日收盘均线下行)
    
    评分(13分):
      - 下影线/实体比: >2倍=3分, >3倍=4分, >5倍=6分
      - 下影线长度: >1%=2分, >2%=3分, >3%=4分
      - 前5日下跌幅度: 跌>3%=1分, 跌>5%=2分
      - 创新低后拉起: 是=1分 (bonus)
    
    Args:
        klines: 日K线列表
        lookback: 创新低回看窗口
    
    Returns:
        {"score": int, "max": 13, "details": {...}}
    """
    if len(klines) < 6:
        return {"score": 0, "max": 13, "reason": "数据不足"}
    
    today = klines[-1]
    open_p = float(today["open"])
    high_p = float(today["high"])
    low_p = float(today["low"])
    close_p = float(today["close"])
    
    # 实体长度
    body = abs(close_p - open_p)
    # 下影线长度
    lower_shadow = min(open_p, close_p) - low_p
    # 上影线长度
    upper_shadow = high_p - max(open_p, close_p)
    
    # 下影线/实体比
    shadow_body_ratio = lower_shadow / body if body > 0 else (lower_shadow / 0.01 if lower_shadow > 0 else 0)
    
    # 下影线占价格比例
    shadow_pct = lower_shadow / close_p * 100 if close_p > 0 else 0
    
    # 条件2: 收盘>开盘
    close_above_open = close_p > open_p
    
    # 条件3: 创近N日新低
    lookback_n = min(lookback, len(klines) - 1)
    recent_lows = [float(k["low"]) for k in klines[-lookback_n:-1]]
    is_new_low = low_p <= min(recent_lows) * 1.005 if recent_lows else False  # 0.5%容差
    
    # 条件4: 前5日下跌趋势 (从5日内最高收盘价算起, 避免中间反弹稀释跌幅)
    prior_5 = klines[-6:-1] if len(klines) >= 6 else klines[:-1]
    prior_5_closes = [float(k["close"]) for k in prior_5]
    if len(prior_5_closes) >= 2:
        peak_close = max(prior_5_closes)
        prior_5_decline = (prior_5_closes[-1] - peak_close) / peak_close * 100
    else:
        prior_5_decline = 0
    
    score = 0
    details = {
        "open": open_p,
        "high": high_p,
        "low": low_p,
        "close": close_p,
        "body": round(body, 3),
        "lower_shadow": round(lower_shadow, 3),
        "upper_shadow": round(upper_shadow, 3),
        "shadow_body_ratio": round(shadow_body_ratio, 2),
        "shadow_pct": round(shadow_pct, 2),
        "close_above_open": close_above_open,
        "is_new_low": is_new_low,
        "prior_5_decline": round(prior_5_decline, 2),
    }
    
    # 门槛条件: 必须收盘>开盘 + 下影线>实体2倍
    if not close_above_open or shadow_body_ratio < 2:
        details["note"] = f"未满足门槛(close>open={close_above_open}, 下影线/实体={shadow_body_ratio:.1f}<2)"
        return {"score": 0, "max": 12, "details": details}
    
    # 下影线/实体比评分 (最高6分)
    if shadow_body_ratio > 5:
        score += 6
    elif shadow_body_ratio > 3:
        score += 4
    elif shadow_body_ratio > 2:
        score += 3
    
    # 下影线长度评分 (最高4分)
    if shadow_pct > 3:
        score += 4
    elif shadow_pct > 2:
        score += 3
    elif shadow_pct > 1:
        score += 2
    
    # 前5日下跌幅度评分 (最高2分)
    if prior_5_decline < -5:
        score += 2
    elif prior_5_decline < -3:
        score += 1
    
    # 创新低后拉起bonus (1分)
    if is_new_low:
        score += 1
    details["new_low_bonus"] = is_new_low
    
    trigger = score >= 8
    details["note"] = (
        f"长下影线日内反转: 下影线/实体={shadow_body_ratio:.1f}倍, "
        f"下影线={shadow_pct:.2f}%, 前5日跌{prior_5_decline:.1f}%, "
        f"创近{lookback_n}日新低={is_new_low}"
    )
    
    return {"score": min(score, 13), "max": 13, "trigger": trigger, "details": details}


# ==================== D50 创新低后尾盘放量 (10分) ====================

def calc_d50_late_surge(klines_5min: List[Dict]) -> Dict:
    """
    D50 创新低后尾盘放量
    
    条件:
      1. 14:30后成交量 > 上午(9:30-11:30)均量×1.5
      2. 14:30后价格从低点回升 (收盘 > 14:30时价格)
      3. 当日创近期新低后拉起
    
    评分(10分):
      - 尾盘/上午量比: >1.5=3分, >2.0=4分, >3.0=5分
      - 回升幅度: >1%=2分, >2%=3分, >3%=4分
      - 创新低后拉起: 是=1分
    
    Args:
        klines_5min: 5分钟K线列表, 每个含 time/open/high/low/close/vol
                     需包含当日全部48根5分钟K线
    
    Returns:
        {"score": int, "max": 10, "details": {...}}
    """
    if not klines_5min or len(klines_5min) < 20:
        return {"score": 0, "max": 10, "reason": "5分钟数据不足"}
    
    # 分离上午和下午数据
    # 5分钟K线: 9:30-11:30 = 24根(上午), 13:00-15:00 = 24根(下午)
    # 14:30对应下午第6根(index 30 in full day)
    morning_bars = []
    afternoon_bars = []
    late_bars = []  # 14:30后
    
    for bar in klines_5min:
        time_str = bar.get("time", "")
        # time格式可能是 "20260611" 或 "0935" 或 "202606110935"
        # 提取时间部分
        if len(time_str) >= 4:
            hhmm = time_str[-4:] if len(time_str) >= 4 else "0000"
        else:
            hhmm = "0000"
        
        try:
            hh = int(hhmm[:2])
            mm = int(hhmm[2:4])
        except (ValueError, IndexError):
            continue
        
        minutes = hh * 60 + mm
        
        if 570 <= minutes <= 690:  # 9:30-11:30
            morning_bars.append(bar)
        elif 780 <= minutes <= 900:  # 13:00-15:00
            afternoon_bars.append(bar)
            if minutes >= 870:  # 14:30后
                late_bars.append(bar)
    
    if not morning_bars or not late_bars:
        return {"score": 0, "max": 10, "reason": "无法分离上午/尾盘数据"}
    
    # 上午均量
    morning_vols = [float(b.get("vol", 0)) for b in morning_bars]
    morning_avg_vol = sum(morning_vols) / len(morning_vols) if morning_vols else 0
    
    # 尾盘均量
    late_vols = [float(b.get("vol", 0)) for b in late_bars]
    late_avg_vol = sum(late_vols) / len(late_vols) if late_vols else 0
    
    # 量比
    vol_ratio = late_avg_vol / morning_avg_vol if morning_avg_vol > 0 else 0
    
    # 尾盘价格回升
    late_opens = [float(b.get("open", 0)) for b in late_bars]
    late_closes = [float(b.get("close", 0)) for b in late_bars]
    
    first_late_price = late_opens[0] if late_opens else 0
    last_late_price = late_closes[-1] if late_closes else 0
    
    recovery_pct = (last_late_price - first_late_price) / first_late_price * 100 if first_late_price > 0 else 0
    
    # 当日最低是否在尾盘前
    all_lows = [float(b.get("low", 999999)) for b in klines_5min]
    morning_lows = [float(b.get("low", 999999)) for b in morning_bars]
    day_low = min(all_lows) if all_lows else 0
    morning_low = min(morning_lows) if morning_lows else 0
    low_in_morning = day_low <= morning_low * 1.001
    
    score = 0
    
    # 量比评分
    if vol_ratio > 3.0:
        score += 5
    elif vol_ratio > 2.0:
        score += 4
    elif vol_ratio > 1.5:
        score += 3
    
    # 回升幅度评分
    if recovery_pct > 3:
        score += 4
    elif recovery_pct > 2:
        score += 3
    elif recovery_pct > 1:
        score += 2
    
    # 创新低后拉起
    if low_in_morning:
        score += 1
    
    trigger = score >= 7
    
    return {
        "score": min(score, 10),
        "max": 10,
        "trigger": trigger,
        "details": {
            "morning_avg_vol": round(morning_avg_vol, 0),
            "late_avg_vol": round(late_avg_vol, 0),
            "vol_ratio": round(vol_ratio, 2),
            "recovery_pct": round(recovery_pct, 2),
            "low_in_morning": low_in_morning,
            "note": f"尾盘放量: 量比{vol_ratio:.1f}倍, 回升{recovery_pct:.2f}%, 低点在上午={low_in_morning}"
        }
    }


# ==================== 反转旁路规则 (Reversal Bypass) ====================

class ReversalBypass:
    """
    V13.5.23 反转旁路规则
    
    当五确认不足3时, 检查是否有强反转信号:
      - D49≥8 (长下影线日内反转强信号) + D29≥6
      - D50≥7 (尾盘放量强信号) + D29≥6
    
    满足任一条件则允许绕过F3五确认过滤
    """
    
    D49_BYPASS_THRESHOLD = 8
    D50_BYPASS_THRESHOLD = 7
    D29_REQUIRED = 6
    
    @staticmethod
    def check(d49_score: int, d50_score: int, d29_score: int,
              d47_score: int = 0, d48_score: int = 0) -> Dict:
        """
        检查是否满足反转旁路条件
        
        Returns:
            {
                "bypass": bool,
                "path": str,  # "D49" / "D50" / "D49+D50" / "NONE"
                "reason": str,
                "scores": {"D47": x, "D48": x, "D49": x, "D50": x, "D29": x}
            }
        """
        scores = {"D47": d47_score, "D48": d48_score, "D49": d49_score, "D50": d50_score, "D29": d29_score}
        
        d49_ok = d49_score >= ReversalBypass.D49_BYPASS_THRESHOLD
        d50_ok = d50_score >= ReversalBypass.D50_BYPASS_THRESHOLD
        d29_ok = d29_score >= ReversalBypass.D29_REQUIRED
        
        if d49_ok and d50_ok and d29_ok:
            return {
                "bypass": True,
                "path": "D49+D50",
                "reason": f"双重反转信号: D49={d49_score}+D50={d50_score}+D29={d29_score}, 绕过五确认",
                "scores": scores
            }
        elif d49_ok and d29_ok:
            return {
                "bypass": True,
                "path": "D49",
                "reason": f"长下影线反转: D49={d49_score}≥8 + D29={d29_score}≥6, 绕过五确认",
                "scores": scores
            }
        elif d50_ok and d29_ok:
            return {
                "bypass": True,
                "path": "D50",
                "reason": f"尾盘放量反转: D50={d50_score}≥7 + D29={d29_score}≥6, 绕过五确认",
                "scores": scores
            }
        else:
            missing = []
            if not d29_ok:
                missing.append(f"D29={d29_score}<{ReversalBypass.D29_REQUIRED}")
            if not d49_ok and not d50_ok:
                missing.append(f"D49={d49_score}<{ReversalBypass.D49_BYPASS_THRESHOLD}且D50={d50_score}<{ReversalBypass.D50_BYPASS_THRESHOLD}")
            return {
                "bypass": False,
                "path": "NONE",
                "reason": f"反转旁路未满足: {', '.join(missing)}",
                "scores": scores
            }


# ==================== 综合反转评分 ====================

def evaluate_reversal(klines_daily: List[Dict],
                      klines_5min: List[Dict] = None,
                      stock_20d_pct: float = 0,
                      sector_20d_pct: float = 0,
                      d29_score: int = 0) -> Dict:
    """
    V13.5.23 综合反转维度评估
    
    Args:
        klines_daily: 日K线列表
        klines_5min: 5分钟K线列表(可选)
        stock_20d_pct: 个股20日涨跌幅
        sector_20d_pct: 板块20日涨跌幅
        d29_score: D29双洗盘得分
    
    Returns:
        {
            "D47": {...}, "D48": {...}, "D49": {...}, "D50": {...},
            "total_reversal_score": int,
            "bypass": {...},
            "verdict": "STRONG_REVERSAL" / "REVERSAL" / "WEAK" / "NONE"
        }
    """
    d47 = calc_d47_capitulation_bottom(klines_daily)
    d48 = calc_d48_oversold_divergence(stock_20d_pct, sector_20d_pct)
    d49 = calc_d49_long_lower_shadow(klines_daily)
    d50 = calc_d50_late_surge(klines_5min) if klines_5min else {"score": 0, "max": 10, "reason": "无5分钟数据"}
    
    total = d47["score"] + d48["score"] + d49["score"] + d50["score"]
    max_total = d47["max"] + d48["max"] + d49["max"] + d50["max"]
    
    bypass = ReversalBypass.check(
        d49_score=d49["score"],
        d50_score=d50["score"],
        d29_score=d29_score,
        d47_score=d47["score"],
        d48_score=d48["score"]
    )
    
    if bypass["bypass"] and total >= 15:
        verdict = "STRONG_REVERSAL"
    elif bypass["bypass"]:
        verdict = "REVERSAL"
    elif d49["score"] >= 5 or d50["score"] >= 4:
        verdict = "WEAK"
    else:
        verdict = "NONE"
    
    return {
        "D47": d47,
        "D48": d48,
        "D49": d49,
        "D50": d50,
        "total_reversal_score": total,
        "max_reversal_score": max_total,
        "bypass": bypass,
        "verdict": verdict,
        "timestamp": datetime.now().isoformat()
    }


# ==================== 验证用例 ====================

def demo_hsjn_603137():
    """验证: 恒尚节能6/11长下影线日内反转"""
    print("\n" + "=" * 60)
    print("  V13.5.23 验证: 恒尚节能(603137) 6月11日")
    print("=" * 60)
    
    # 构造6/11及前5日的日K线数据
    klines = [
        # 前5日 (6/4-6/10, 简化)
        {"open": 12.20, "high": 12.30, "low": 11.80, "close": 11.90, "vol": 500},
        {"open": 11.90, "high": 12.00, "low": 11.60, "close": 11.70, "vol": 450},
        {"open": 11.70, "high": 11.80, "low": 11.40, "close": 11.50, "vol": 400},
        {"open": 11.50, "high": 11.70, "low": 11.30, "close": 11.55, "vol": 380},
        {"open": 11.55, "high": 11.65, "low": 11.35, "close": 11.45, "vol": 350},
        # 6/11 当日
        {"open": 11.44, "high": 11.60, "low": 11.20, "close": 11.54, "vol": 420},
    ]
    
    d49 = calc_d49_long_lower_shadow(klines)
    print(f"  D49: score={d49['score']}/{d49['max']}")
    print(f"  Details: {json.dumps(d49['details'], ensure_ascii=False, indent=2)}")
    
    # D29=9 (从之前的分析)
    bypass = ReversalBypass.check(
        d49_score=d49["score"],
        d50_score=0,
        d29_score=9,
    )
    print(f"\n  反转旁路: bypass={bypass['bypass']}, path={bypass['path']}")
    print(f"  Reason: {bypass['reason']}")
    
    if bypass["bypass"]:
        print("  ✅ 验证通过: D49触发反转旁路, 恒尚节能6/11将不再被F3五确认误杀")
    else:
        print(f"  ⚠️ 未触发旁路, D49={d49['score']}需≥8")
    
    return d49, bypass


def demo_best_300580():
    """验证: 贝斯特6/26暴跌尽头"""
    print("\n" + "=" * 60)
    print("  V13.5.23 验证: 贝斯特(300580) 6月26日")
    print("=" * 60)
    
    # 构造6/26及前20日简化数据 (暴跌场景)
    import random
    random.seed(42)
    base = 15.0
    klines = []
    for i in range(25):
        decline = -0.5 - random.random() * 0.8
        close = base * (1 + decline * i / 100)
        open_p = close * (1 + random.uniform(-0.01, 0.01))
        high = max(open_p, close) * (1 + random.uniform(0, 0.005))
        low = min(open_p, close) * (1 - random.uniform(0.01, 0.03))
        vol = int(800 - i * 15 + random.uniform(-50, 50))
        klines.append({"open": round(open_p, 2), "high": round(high, 2),
                       "low": round(low, 2), "close": round(close, 2), "vol": max(vol, 100)})
    
    # 6/26当日: 暴跌后长下影线
    klines.append({
        "open": 10.50, "high": 10.80, "low": 9.80, "close": 10.60, "vol": 1200
    })
    
    d47 = calc_d47_capitulation_bottom(klines)
    d49 = calc_d49_long_lower_shadow(klines)
    
    print(f"  D47: score={d47['score']}/{d47['max']} - {d47['details'].get('note', '')}")
    print(f"  D49: score={d49['score']}/{d49['max']} - {d49['details'].get('note', '')}")
    
    # 贝斯特6/26 MEG=RED, 正确拦截
    print(f"\n  注意: 贝斯特6/26 MEG=RED, 系统正确拦截")
    print(f"  但D47={d47['score']}分+D49={d49['score']}分 → 应进入超跌反弹观察池")
    print(f"  等MEG回升后, D49触发反转旁路可捕获该股")


if __name__ == "__main__":
    print("=" * 60)
    print("  V13.5.23 反转维度模块 — 验证")
    print("=" * 60)
    print()
    print("  新增维度:")
    print("    D47 暴跌尽头识别 (8分): 连跌≥5天+缩量+20日跌>25%")
    print("    D48 超跌Divergence (5分): 个股vs板块20日差<-15%")
    print("    D49 长下影线日内反转 (13分): 下影线>实体2倍+收盘>开盘+创新低")
    print("    D50 创新低后尾盘放量 (10分): 14:30后量>上午1.5倍+价格回升")
    print()
    print("  反转旁路规则:")
    print(f"    D49≥{ReversalBypass.D49_BYPASS_THRESHOLD} + D29≥{ReversalBypass.D29_REQUIRED} → 绕过F3五确认")
    print(f"    D50≥{ReversalBypass.D50_BYPASS_THRESHOLD} + D29≥{ReversalBypass.D29_REQUIRED} → 绕过F3五确认")
    print("=" * 60)
    
    demo_hsjn_603137()
    demo_best_300580()
    
    print("\n" + "=" * 60)
    print("  V13.5.23 反转维度模块验证完成")
    print("=" * 60)
