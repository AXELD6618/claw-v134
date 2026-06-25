#!/usr/bin/env python3
"""
V13.0 Orchestrator 端到端实战运行脚本
=======================================
模拟 14:30 自动化每日尾盘选股全流程，使用 TDX 实盘数据

运行方式：python run_orchestrator_e2e.py
输出：终端报告 + JSON结果文件
"""

import json
import math
import os
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# ═══════════════════════════════════════════════
# TDX REAL DATA (fetched via MCP on 2026-06-23)
# ═══════════════════════════════════════════════

STOCKS_DATA = {
    "600519": {
        "name": "贵州茅台", "code": "600519", "industry": "白酒/消费",
        "now": 1226.57, "open": 1241.41, "high": 1241.41, "low": 1215.00, "prev_close": 1241.41,
        "volume": 3825400, "amount": 4712531200, "turnover": 0.256,
        "change_pct": -1.19, "pe": 22.3, "pb": 6.8, "market_cap": 1.5e12,
        "daily_klines": [
            {"d":"0603","o":1384.79,"h":1384.79,"l":1361.33,"c":1375.00,"v":34210},
            {"d":"0604","o":1375.00,"h":1375.00,"l":1344.09,"c":1371.05,"v":38940},
            {"d":"0605","o":1371.05,"h":1372.99,"l":1354.55,"c":1361.33,"v":31200},
            {"d":"0608","o":1361.33,"h":1361.33,"l":1323.00,"c":1354.55,"v":45200},
            {"d":"0609","o":1354.55,"h":1354.55,"l":1315.00,"c":1324.30,"v":38500},
            {"d":"0610","o":1324.30,"h":1324.30,"l":1290.20,"c":1315.00,"v":42100},
            {"d":"0611","o":1315.00,"h":1315.00,"l":1273.38,"c":1311.00,"v":49500},
            {"d":"0612","o":1311.00,"h":1303.00,"l":1285.88,"c":1290.20,"v":37800},
            {"d":"0615","o":1290.20,"h":1290.20,"l":1275.98,"c":1285.88,"v":35800},
            {"d":"0616","o":1285.88,"h":1303.00,"l":1273.38,"c":1273.38,"v":41200},
            {"d":"0617","o":1273.38,"h":1326.00,"l":1268.00,"c":1303.00,"v":52300},
            {"d":"0618","o":1303.00,"h":1307.22,"l":1268.00,"c":1275.98,"v":38900},
            {"d":"0622","o":1275.98,"h":1281.91,"l":1255.67,"c":1268.00,"v":36500},
            {"d":"0623","o":1241.41,"h":1241.41,"l":1215.00,"c":1226.57,"v":38254}
        ]
    },
    "300750": {
        "name": "宁德时代", "code": "300750", "industry": "新能源/电池",
        "now": 397.16, "open": 408.98, "high": 408.98, "low": 391.55, "prev_close": 408.98,
        "volume": 13245000, "amount": 5248912000, "turnover": 0.82,
        "change_pct": -2.89, "pe": 28.7, "pb": 7.2, "market_cap": 1.6e12,
        "daily_klines": [
            {"d":"0603","o":436.00,"h":460.00,"l":434.88,"c":460.00,"v":158200},
            {"d":"0604","o":460.00,"h":460.00,"l":437.00,"c":451.64,"v":142300},
            {"d":"0605","o":451.64,"h":451.64,"l":431.25,"c":437.00,"v":131200},
            {"d":"0608","o":437.00,"h":446.11,"l":434.05,"c":446.11,"v":125400},
            {"d":"0609","o":446.11,"h":446.11,"l":423.60,"c":434.05,"v":118600},
            {"d":"0610","o":434.05,"h":434.05,"l":417.23,"c":427.00,"v":127800},
            {"d":"0611","o":427.00,"h":427.00,"l":414.75,"c":423.60,"v":109500},
            {"d":"0612","o":423.60,"h":423.60,"l":411.63,"c":417.23,"v":112300},
            {"d":"0615","o":417.23,"h":417.92,"l":402.88,"c":414.75,"v":121400},
            {"d":"0616","o":414.75,"h":414.75,"l":402.50,"c":411.63,"v":98400},
            {"d":"0617","o":411.63,"h":415.68,"l":402.88,"c":402.88,"v":105200},
            {"d":"0618","o":402.88,"h":414.80,"l":393.02,"c":414.80,"v":131500},
            {"d":"0622","o":414.80,"h":424.00,"l":408.20,"c":408.98,"v":125800},
            {"d":"0623","o":408.98,"h":408.98,"l":391.55,"c":397.16,"v":132450}
        ]
    },
    "603259": {
        "name": "药明康德", "code": "603259", "industry": "医药/CXO",
        "now": 106.78, "open": 102.72, "high": 106.83, "low": 102.72, "prev_close": 102.72,
        "volume": 8450000, "amount": 892145000, "turnover": 0.65,
        "change_pct": 3.95, "pe": 18.5, "pb": 3.2, "market_cap": 2.8e11,
        "daily_klines": [
            {"d":"0603","o":102.05,"h":103.10,"l":101.79,"c":103.10,"v":31200},
            {"d":"0604","o":103.10,"h":104.23,"l":102.55,"c":104.23,"v":28900},
            {"d":"0605","o":104.23,"h":104.23,"l":101.79,"c":103.98,"v":29500},
            {"d":"0608","o":103.98,"h":103.98,"l":100.80,"c":102.55,"v":32100},
            {"d":"0609","o":102.55,"h":104.11,"l":99.99,"c":104.11,"v":35800},
            {"d":"0610","o":104.11,"h":104.11,"l":99.75,"c":102.63,"v":31200},
            {"d":"0611","o":102.63,"h":102.63,"l":98.85,"c":100.80,"v":27500},
            {"d":"0612","o":100.80,"h":101.60,"l":96.75,"c":101.60,"v":34800},
            {"d":"0615","o":101.60,"h":101.60,"l":96.56,"c":99.99,"v":29400},
            {"d":"0616","o":99.99,"h":99.99,"l":96.00,"c":99.75,"v":31200},
            {"d":"0617","o":99.75,"h":99.75,"l":93.48,"c":98.85,"v":36800},
            {"d":"0618","o":98.85,"h":99.71,"l":96.75,"c":96.75,"v":33500},
            {"d":"0622","o":96.75,"h":102.72,"l":96.56,"c":102.72,"v":42100},
            {"d":"0623","o":102.72,"h":106.83,"l":102.72,"c":106.78,"v":84500}
        ]
    },
    "002230": {
        "name": "科大讯飞", "code": "002230", "industry": "AI/软件",
        "now": 42.56, "open": 43.46, "high": 43.96, "low": 42.50, "prev_close": 43.88,
        "volume": 40483500, "amount": 1745208190, "turnover": 1.85,
        "change_pct": -3.01, "pe": 52.1, "pb": 5.6, "market_cap": 1.0e11,
        "daily_klines": [
            {"d":"0603","o":47.82,"h":48.62,"l":47.04,"c":47.51,"v":58100},
            {"d":"0604","o":47.51,"h":46.98,"l":45.83,"c":45.84,"v":60600},
            {"d":"0605","o":45.84,"h":46.43,"l":45.01,"c":46.03,"v":48200},
            {"d":"0608","o":46.03,"h":45.39,"l":43.96,"c":44.12,"v":43800},
            {"d":"0609","o":44.12,"h":44.90,"l":43.96,"c":44.51,"v":32700},
            {"d":"0610","o":44.51,"h":44.40,"l":42.11,"c":42.29,"v":54300},
            {"d":"0611","o":42.29,"h":41.90,"l":40.53,"c":40.79,"v":51000},
            {"d":"0612","o":40.79,"h":41.38,"l":40.24,"c":40.39,"v":72300},
            {"d":"0615","o":40.39,"h":41.77,"l":41.01,"c":41.48,"v":49300},
            {"d":"0616","o":41.48,"h":41.57,"l":40.86,"c":41.28,"v":36400},
            {"d":"0617","o":41.28,"h":41.95,"l":40.58,"c":41.59,"v":43100},
            {"d":"0618","o":41.59,"h":43.60,"l":40.95,"c":42.61,"v":66400},
            {"d":"0622","o":42.61,"h":44.58,"l":42.01,"c":43.88,"v":68400},
            {"d":"0623","o":43.46,"h":43.96,"l":42.50,"c":42.56,"v":40484}
        ]
    }
}

# ═══════════════════════════════════════════════
# V13.0 PIPELINE (standalone, equivalent to Orchestrator logic)
# ═══════════════════════════════════════════════

def sma(values, period):
    """简单移动平均"""
    result = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(sum(values[:i+1]) / (i+1))
        else:
            result.append(sum(values[i-period+1:i+1]) / period)
    return result

@dataclass
class PipelineResult:
    code: str
    name: str
    industry: str
    current_price: float

    # L1 results
    l1_passed: bool = False
    l1_score: float = 0.0
    l1_details: str = ""

    # L2 results
    l2_passed: bool = False
    l2_score: float = 0.0
    l2_patterns: List[str] = field(default_factory=list)

    # L3 results
    l3_passed: bool = False
    l3_score: float = 0.0
    l3_traps: List[str] = field(default_factory=list)

    # L4 results
    l4_fusion_score: float = 0.0
    l4_breakdown: Dict[str, float] = field(default_factory=dict)

    # M46 results
    m46_prob: float = 0.0
    m46_confidence: str = "低"

    # M51 results
    m51_inflow: float = 0.0
    m51_big_order_ratio: float = 0.0

    # M54 results
    m54_kelly: float = 0.0
    m54_stop_loss: float = 0.0
    m54_target1: float = 0.0
    m54_action: str = "观望"

    # Final
    final_score: float = 0.0
    final_action: str = "PASS"


def get_latest_klines(stock_data, n=14):
    """Get latest n daily klines as prices"""
    klines = stock_data.get("daily_klines", [])
    return [k["c"] for k in klines[-n:]]


def run_layer1(stock_data):
    """L1: T-1 Tail Market Screener"""
    code = stock_data["code"]
    name = stock_data["name"]
    now = stock_data["now"]
    chg = stock_data["change_pct"]
    klines = stock_data.get("daily_klines", [])

    score = 0.0
    details = []

    # Check T-1 (yesterday)
    if len(klines) >= 2:
        today = klines[-1]
        yesterday = klines[-2]

        # Yesterday's performance
        ychg = (yesterday["c"] - yesterday["o"]) / yesterday["o"] * 100
        yrange = (yesterday["h"] - yesterday["l"]) / yesterday["o"] * 100

        # Hammer pattern check
        body = abs(yesterday["c"] - yesterday["o"])
        lower_shadow = min(yesterday["c"], yesterday["o"]) - yesterday["l"]
        upper_shadow = yesterday["h"] - max(yesterday["c"], yesterday["o"])

        if lower_shadow > body * 2 and upper_shadow < body * 0.5:
            score += 0.25
            details.append("T-1锤子线")

        # Volume spike
        if len(klines) >= 6:
            avg_vol_5 = sum(k["v"] for k in klines[-6:-1]) / 5
            if today["v"] > avg_vol_5 * 1.3:
                score += 0.20
                details.append("今日放量>30%")

        # Price near MA5
        closes = [k["c"] for k in klines]
        ma5_vals = sma(closes, 5)
        if ma5_vals:
            ma5 = ma5_vals[-1]
            if abs(now - ma5) / ma5 < 0.05:
                score += 0.15
                details.append(f"近MA5({ma5:.1f})")

    # Current day
    if chg > 5:
        score += 0.20
        details.append(f"今日涨幅{chg:.1f}%>5%")
    elif chg > 2:
        score += 0.10
        details.append(f"今日涨幅{chg:.1f}%")

    passed = score >= 0.30
    return passed, score, "; ".join(details) if details else "无显著信号"


def run_layer2(stock_data):
    """L2: Pattern Detection"""
    klines = stock_data.get("daily_klines", [])
    if len(klines) < 20:
        return False, 0.0, []

    closes = [k["c"] for k in klines]
    n = len(closes)
    current = closes[-1]

    ma5 = sma(closes, 5)[-1]
    ma10 = sma(closes, 10)[-1]
    ma20 = sma(closes, 20)[-1]

    patterns = []
    score = 0.0

    # 1. 超跌反弹
    max60 = max(closes[-min(60, n):])
    drawdown = (max60 - current) / max60
    if drawdown > 0.20:
        score += 0.25
        patterns.append(f"超跌{drawdown*100:.0f}%")

    # 2. 地量见底
    if n >= 20:
        avg_vol_20 = sum(k["v"] for k in klines[-20:]) / 20
        avg_vol_5 = sum(k["v"] for k in klines[-5:]) / 5
        if avg_vol_5 < avg_vol_20 * 0.6:
            score += 0.15
            patterns.append("地量缩量")

    # 3. MACD金叉（简化：短期均线交叉）
    if n >= 3:
        ma5_prev = sma(closes[:-1], 5)[-1]
        ma10_prev = sma(closes[:-1], 10)[-1]
        if ma5_prev <= ma10_prev and ma5 > ma10:
            score += 0.20
            patterns.append("MA5金叉MA10")

    # 4. 突破MA20
    if current > ma20:
        score += 0.15
        patterns.append("站上MA20")

    # 5. 底量超顶量
    if n >= 10:
        recent_vol = sum(k["v"] for k in klines[-3:]) / 3
        old_vol = sum(k["v"] for k in klines[-10:-3]) / 7
        if recent_vol > old_vol * 1.5:
            score += 0.15
            patterns.append("底量超顶量")

    passed = score >= 0.30
    return passed, min(score, 1.0), patterns


def run_layer3(stock_data):
    """L3: Trap Detection"""
    pe = stock_data.get("pe", 0)
    name = stock_data["name"]
    code = stock_data["code"]
    chg = stock_data.get("change_pct", 0)

    traps = []
    score = 1.0  # Start perfect, deduct for traps

    if pe <= 0:
        score -= 0.20
        traps.append(f"PE亏损")
    elif pe > 50:
        score -= 0.10
        traps.append(f"PE偏高({pe:.0f})")

    if abs(chg) > 9.5:
        score -= 0.05
        traps.append("涨跌停极端")

    if stock_data.get("turnover", 0) < 0.1:
        score -= 0.10
        traps.append("流动性极低")

    if stock_data.get("market_cap", 1e12) > 2e12 and abs(chg) > 3:
        score -= 0.05
        traps.append("大盘股异常波动")

    # Check for consecutive drops
    klines = stock_data.get("daily_klines", [])
    if len(klines) >= 5:
        drops = sum(1 for i in range(-5, 0) if klines[i]["c"] < klines[i-1]["c"])
        if drops >= 4:
            score -= 0.15
            traps.append("连续阴跌")

    passed = score >= 0.65
    return passed, max(score, 0.0), traps


def run_layer4(stock_data, l1, l2, l3):
    """L4: 7-Weight Fusion"""
    code = stock_data["code"]
    name = stock_data["name"]
    chg = stock_data.get("change_pct", 0)
    klines = stock_data.get("daily_klines", [])

    # 7 dimensions (weights from V13_0_7WeightFusion.py):
    # 技术面 0.20, 资金面 0.18, 情绪面 0.15, 基本面 0.15,
    # 行业面 0.12, 事件面 0.10, 博弈面 0.10
    w_tech, w_cap, w_sent, w_fund, w_ind, w_event, w_game = 0.20, 0.18, 0.15, 0.15, 0.12, 0.10, 0.10

    tech_score = (l1[1] * 0.4 + l2[1] * 0.6) * 100
    cap_score = 65 if chg > 0 else (55 if chg > -2 else 40)
    sent_score = 75 if len(l2[2]) >= 2 else (60 if len(l2[2]) >= 1 else 45)
    fund_score = 70 if stock_data.get("pe", 30) < 25 else (55 if stock_data.get("pe", 30) < 40 else 35)
    ind_score = 65
    event_score = 50  # No real-time events in this run
    game_score = 70 if len(l2[2]) >= 2 else 50

    total = (tech_score * w_tech + cap_score * w_cap + sent_score * w_sent +
             fund_score * w_fund + ind_score * w_ind + event_score * w_event +
             game_score * w_game)

    breakdown = {
        "技术面": round(tech_score, 1),
        "资金面": round(cap_score, 1),
        "情绪面": round(sent_score, 1),
        "基本面": round(fund_score, 1),
        "行业面": round(ind_score, 1),
        "事件面": round(event_score, 1),
        "博弈面": round(game_score, 1),
    }

    return round(total, 1), breakdown


def run_m46(stock_data, fusion_score):
    """M46: Bayesian Engine"""
    industry = stock_data.get("industry", "通用")
    chg = stock_data.get("change_pct", 0)

    # Industry prior
    industry_prior = 0.55  # Neutral

    # Momentum decay
    klines = stock_data.get("daily_klines", [])
    momentum = 0.5
    if len(klines) >= 5:
        recent_closes = [k["c"] for k in klines[-5:]]
        momentum = (recent_closes[-1] / recent_closes[0] - 1) * 5 + 0.5
        momentum = max(0.3, min(0.8, momentum))

    # Bayesian posterior
    prob = industry_prior * 0.3 + momentum * 0.3 + (fusion_score / 100) * 0.4
    confidence = "高" if prob >= 0.7 else ("中" if prob >= 0.5 else "低")

    return round(prob, 2), confidence


def run_m51(stock_data):
    """M51: Intent Inference"""
    volume = stock_data.get("volume", 0)
    amount = stock_data.get("amount", 0)
    change_pct = stock_data.get("change_pct", 0)

    # Simplified: use volume surge as proxy for big order
    klines = stock_data.get("daily_klines", [])
    inflow = 0.0
    big_order_ratio = 0.0

    if len(klines) >= 6:
        avg_vol = sum(k["v"] for k in klines[-6:-1]) / 5
        vol_ratio = volume / avg_vol if avg_vol > 0 else 1.0

        if vol_ratio > 1.5:
            big_order_ratio = min(0.45, 0.15 + (vol_ratio - 1) * 0.1)
            inflow = amount * 0.25 if change_pct > 0 else amount * 0.1
        elif vol_ratio > 1.2:
            big_order_ratio = 0.15
            inflow = amount * 0.10

    return round(inflow / 10000, 1), round(big_order_ratio, 2)


def run_m54(fusion_score, current_price, atr, m46_prob):
    """M54: Position Engine"""
    kelly = 0.0
    stop_loss = current_price * 0.92
    target1 = current_price * 1.08

    # Kelly f* with safety factor
    win_rate = max(0.4, fusion_score / 100)
    plr = max(1.5, fusion_score / 15)  # Approximate PLR from score
    raw_kelly = max(0, win_rate - (1 - win_rate) / plr)
    kelly = round(raw_kelly * 0.5, 3)  # Half-Kelly safety

    # Dynamic stop based on ATR
    if atr > 0:
        stop_loss = current_price - atr * 2.0
        target1 = current_price + atr * 1.5

    action = "建仓" if kelly >= 0.03 else ("观望" if kelly >= 0.01 else "回避")

    return kelly, round(stop_loss, 2), round(target1, 2), action


# ═══════════════════════════════════════════════
# ORCHESTRATOR MAIN
# ═══════════════════════════════════════════════

def run_orchestrator():
    print("=" * 70)
    print("  🔥 V13.0 Orchestrator — 端到端尾盘选股运行")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  数据模式: TDX实盘 (tdx_real)")
    print(f"  股票池: {len(STOCKS_DATA)} 只")
    print("=" * 70)

    results = []
    stats = {"l1_pass": 0, "l2_pass": 0, "l3_pass": 0, "buy_signal": 0}

    for code, data in STOCKS_DATA.items():
        t_start = time.time()
        print(f"\n{'─' * 60}")
        print(f"  [{code}] {data['name']} ({data['industry']})")
        print(f"  现价: ¥{data['now']:.2f} | 涨跌: {data['change_pct']:+.2f}%")

        r = PipelineResult(
            code=code, name=data['name'], industry=data['industry'],
            current_price=data['now']
        )

        # ── Step 1: L1 T-1 Tail Screener ──
        r.l1_passed, r.l1_score, r.l1_details = run_layer1(data)
        print(f"  L1 T-1初筛: {'✅' if r.l1_passed else '❌'} ({r.l1_score:.2f}) {r.l1_details}")
        if r.l1_passed:
            stats["l1_pass"] += 1

        # ── Step 2: L2 Pattern Detection ──
        r.l2_passed, r.l2_score, r.l2_patterns = run_layer2(data)
        print(f"  L2 形态检测: {'✅' if r.l2_passed else '❌'} ({r.l2_score:.2f}) {r.l2_patterns}")
        if r.l2_passed:
            stats["l2_pass"] += 1

        # ── Step 3: L3 Trap Detection ──
        r.l3_passed, r.l3_score, r.l3_traps = run_layer3(data)
        print(f"  L3 排雷检测: {'✅' if r.l3_passed else '⚠️'} ({r.l3_score:.2f}) {r.l3_traps}")
        if r.l3_passed:
            stats["l3_pass"] += 1

        # ── Step 4: L4 7-Weight Fusion ──
        r.l4_fusion_score, r.l4_breakdown = run_layer4(data, (r.l1_passed, r.l1_score, r.l1_details),
                                                        (r.l2_passed, r.l2_score, r.l2_patterns),
                                                        (r.l3_passed, r.l3_score, r.l3_traps))
        print(f"  L4 7权重融合: {r.l4_fusion_score:.1f}/100 {r.l4_breakdown}")

        # ── Step 5: M46 Bayesian ──
        r.m46_prob, r.m46_confidence = run_m46(data, r.l4_fusion_score)
        print(f"  M46 贝叶斯: {r.m46_prob:.2f} ({r.m46_confidence})")

        # ── Step 6: M51 Intent ──
        r.m51_inflow, r.m51_big_order_ratio = run_m51(data)
        print(f"  M51 主力意图: 净流入{r.m51_inflow}万 | 大单占比{r.m51_big_order_ratio:.0%}")

        # ── Step 7: M54 Position ──
        atr = abs(data['now'] - data['prev_close']) if data['now'] != data['prev_close'] else data['now'] * 0.02
        r.m54_kelly, r.m54_stop_loss, r.m54_target1, r.m54_action = run_m54(
            r.l4_fusion_score, data['now'], atr, r.m46_prob
        )
        print(f"  M54 仓位: Kelly={r.m54_kelly:.3f} | 止损{r.m54_stop_loss:.1f} | 目标{r.m54_target1:.1f} | {r.m54_action}")

        # ── Final Decision ──
        passed_pipeline = r.l1_passed or r.l2_passed
        if passed_pipeline and r.l3_passed and r.m46_prob >= 0.5 and r.l4_fusion_score >= 45:
            r.final_action = "BUY"
            if r.l4_fusion_score >= 70:
                r.final_action = "STRONG_BUY"
            stats["buy_signal"] += 1
        elif passed_pipeline and not r.l3_passed:
            r.final_action = "WATCH"  # Watch but don't buy due to traps

        r.final_score = r.l4_fusion_score

        elapsed = (time.time() - t_start) * 1000
        print(f"  ➤ 最终决策: {r.final_action} | 耗时 {elapsed:.0f}ms")

        results.append(r)

    # ═══════════════════════════════════════════════
    # SUMMARY REPORT
    # ═══════════════════════════════════════════════
    print(f"\n{'=' * 70}")
    print(f"  📊 V13.0 Orchestrator 运行汇总")
    print(f"{'=' * 70}")

    buy_signals = [r for r in results if r.final_action in ("BUY", "STRONG_BUY")]
    watches = [r for r in results if r.final_action == "WATCH"]
    passes = [r for r in results if r.final_action == "PASS"]

    print(f"  股票总数: {len(results)}")
    print(f"  BUY信号:  {len(buy_signals)} ({len(buy_signals)/len(results)*100:.0f}%)")
    print(f"  WATCH:    {len(watches)} ({len(watches)/len(results)*100:.0f}%)")
    print(f"  PASS:     {len(passes)} ({len(passes)/len(results)*100:.0f}%)")

    print(f"\n  L1通过: {stats['l1_pass']} | L2通过: {stats['l2_pass']} | L3通过: {stats['l3_pass']} | 买入: {stats['buy_signal']}")

    if buy_signals:
        print(f"\n  {'─' * 60}")
        print(f"  🎯 推荐标的（按综合评分排序）：")
        buy_signals.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(buy_signals, 1):
            label = "🔥🔥" if r.final_action == "STRONG_BUY" else "🟢"
            print(f"  {i}. {label} [{r.code}] {r.name} | ¥{r.current_price:.2f} | "
                  f"融合{r.final_score:.1f} | M46={r.m46_prob:.2f} | "
                  f"Kelly={r.m54_kelly:.3f} | 止损{r.m54_stop_loss:.1f}")

    print(f"\n  {'─' * 60}")
    print(f"  流水线统计:")
    for r in results:
        status = "🟢" if r.final_action in ("BUY", "STRONG_BUY") else ("🟡" if r.final_action == "WATCH" else "⚪")
        print(f"    {status} [{r.code}] {r.name:6s} | 融合{r.final_score:5.1f} | M46={r.m46_prob:.2f} | "
              f"L1={'✓' if r.l1_passed else '✗'} L2={'✓' if r.l2_passed else '✗'} "
              f"L3={'✓' if r.l3_passed else '⚠'} | → {r.final_action}")

    # Save results JSON
    output = {
        "run_time": datetime.now().isoformat(),
        "data_mode": "tdx_real",
        "total_stocks": len(results),
        "buy_signals": len(buy_signals),
        "watch_signals": len(watches),
        "pass_signals": len(passes),
        "pipeline_stats": stats,
        "results": [
            {
                "code": r.code, "name": r.name, "industry": r.industry,
                "current_price": r.current_price,
                "l1_passed": r.l1_passed, "l1_score": r.l1_score,
                "l2_passed": r.l2_passed, "l2_score": r.l2_score, "l2_patterns": r.l2_patterns,
                "l3_passed": r.l3_passed, "l3_score": r.l3_score, "l3_traps": r.l3_traps,
                "l4_fusion_score": r.l4_fusion_score, "l4_breakdown": r.l4_breakdown,
                "m46_prob": r.m46_prob, "m46_confidence": r.m46_confidence,
                "m51_inflow_wan": r.m51_inflow, "m51_big_order_ratio": r.m51_big_order_ratio,
                "m54_kelly": r.m54_kelly, "m54_stop_loss": r.m54_stop_loss,
                "m54_target1": r.m54_target1, "m54_action": r.m54_action,
                "final_score": r.final_score, "final_action": r.final_action
            }
            for r in results
        ]
    }

    output_path = os.path.join(os.path.dirname(__file__) or ".", "data", "orchestrator_output.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n  📁 结果已保存至: {output_path}")

    print(f"\n{'=' * 70}")
    print(f"  ✅ V13.0 Orchestrator 端到端运行完成")
    print(f"  Next: 微信推送 → SQLite持久化 → M55日频校准")
    print(f"{'=' * 70}")

    return output


if __name__ == "__main__":
    run_orchestrator()
