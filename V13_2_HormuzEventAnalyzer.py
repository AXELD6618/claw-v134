#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13_2_HormuzEventAnalyzer.py
霍尔木兹海峡舆情事件分析器
毕方灵犀·天眼 V13.2 — 舆情驱动选股模块

功能:
1. 解析霍尔木兹海峡恢复通航事件
2. 识别受益/受损A股板块和个股
3. 结合TDX实时行情生成交易信号
4. 输出V13.2综合评分推荐
"""

import json
import os
from datetime import datetime, timedelta

# ============================================================
# 事件解析
# ============================================================

HORMUZ_EVENT = {
    "event_name": "霍尔木兹海峡恢复通航",
    "date": "2026-06-23",
    "source": "新华社/财联社",
    "key_facts": [
        "特朗普同意允许霍尔木兹海峡保持开放，不再实施海上封锁",
        "阿曼和伊朗发表联合声明，建立联合工作组管理未来航行",
        "22日至少36艘商船穿越海峡，为2月底以来单日最高",
        "通航量已恢复至战前近1/3水平",
        "23日两艘超级油轮（各载200万桶原油）通过海峡",
        "美伊17日签署谅解备忘录：美解除海上封锁，伊确保60天内免费安全通航",
    ],
    "market_implications": {
        "航运板块": {
            "direction": "BULLISH",
            "strength": "STRONG",
            "logic": [
                "霍尔木兹海峡是全球20%石油运输通道",
                "通航恢复→航运需求激增→运价上涨",
                "此前滞留船舶获准通行→一次性收益",
                "地缘风险溢价下降→保险费用降低",
            ],
            "a_share_exposure": [
                "招商轮船(601872) — 全球最大VLCC船东之一",
                "中远海控(601919) — 集装箱航运龙头",
                "中远海特(600428) — 特种运输",
                "中远海能(600026) — 能源运输",
                "招商南油(601975) — 成品油运输",
            ],
        },
        "石油板块": {
            "direction": "MIXED",
            "strength": "MODERATE",
            "logic": [
                "通航恢复→伊朗石油出口增加→供给增加→油价承压",
                "但：地缘风险下降→风险资产偏好上升→整体股市上涨",
                "石油服务股（统一股份等）受益于通航恢复后的业务量增加",
                "上游开采股（中国石油等）可能承压于油价下跌",
            ],
            "a_share_exposure": [
                "统一股份(600506) — 石油服务 ⬆",
                "新潮能源(600777) — 石油开采 ⬇",
                "中国石化(600028) — 炼化受益于低成本原油 ⬆",
            ],
        },
        "石化板块": {
            "direction": "BULLISH",
            "strength": "MODERATE",
            "logic": [
                "原油供应增加→原材料成本下降",
                "石化产品利润空间扩大",
                "航空、航运等下游行业成本下降→需求增加",
            ],
        },
        "军工板块": {
            "direction": "BEARISH",
            "strength": "WEAK",
            "logic": [
                "地缘紧张缓和→军工订单预期下降",
                "但：长期国防预算增长趋势不变",
            ],
        },
    },
}

# ============================================================
# TDX实时数据（6月23日收盘）
# ============================================================

TDX_MARKET_DATA = {
    "trading_date": "2026-06-23",
    "market_session": "CLOSE",
    "next_session": "2026-06-24 09:30",
    "sector_performance": {
        "航运": {
            "total_stocks": 19,
            "limit_up": 0,
            "avg_change_pct": 1.20,
            "leader": "招商轮船(+5.68%)",
            "notes": "板块整体上涨，但未现涨停股",
        },
        "石油": {
            "total_stocks": 38,
            "limit_up": 2,
            "avg_change_pct": 3.50,
            "leader": "统一股份(+9.99%)、新潮能源(+9.98%)",
            "notes": "石油服务股领涨，开采股跟涨",
        },
        "石化": {
            "total_stocks": 18,
            "limit_up": 0,
            "avg_change_pct": 1.50,
            "leader": "统一股份(+9.99%)",
            "notes": "与石油板块重叠",
        },
    },
    "top_stocks": {
        "601872": {  # 招商轮船
            "name": "招商轮船",
            "price": 19.92,
            "change_pct": 5.68,
            "volume_k_lots": 2214.8,
            "amount_yuan": 4.386e9,
            "turnover_rate": 2.74,
            "buy_sell_ratio": 1.29,  # Outside/Inside = 1237/977 = 1.27
            "kline_5d": {
                "20260616": {"open": 17.77, "high": 17.94, "low": 16.04, "close": 16.38},
                "20260617": {"open": 16.30, "high": 17.85, "low": 16.30, "close": 17.10},
                "20260618": {"open": 17.16, "high": 17.49, "low": 16.60, "close": 17.14},
                "20260622": {"open": 17.20, "high": 18.85, "low": 17.15, "close": 18.85},
                "20260623": {"open": 19.43, "high": 20.50, "low": 18.90, "close": 19.92},
            },
            "trend_analysis": {
                "5d_return": "+19.2% (16.04→19.92)",
                "volume_trend": "持续放量",
                "pattern": "V型反转+突破前高",
                "m56_signal": "STRONG_BUY",
            },
            "hormuz_exposure": "DIRECT",
            "exposure_desc": "全球最大VLCC船东之一，霍尔木兹通航恢复直接受益",
        },
        "600506": {  # 统一股份
            "name": "统一股份",
            "price": 16.41,
            "change_pct": 9.99,
            "volume_k_lots": 1182.4,
            "amount_yuan": 1.934e9,
            "turnover_rate": 4.74,
            "limit_up": True,
            "kline_5d": {
                "20260616": {"open": 16.20, "high": 16.40, "low": 16.00, "close": 16.13},
                "20260617": {"open": 16.00, "high": 16.10, "low": 15.65, "close": 15.70},
                "20260618": {"open": 15.55, "high": 15.66, "low": 15.03, "close": 15.19},
                "20260622": {"open": 15.18, "high": 15.19, "low": 14.46, "close": 14.92},
                "20260623": {"open": 16.15, "high": 16.41, "low": 15.87, "close": 16.41},
            },
            "trend_analysis": {
                "5d_return": "+10.2% (14.92→16.41)",
                "volume_trend": "今日爆量（118万手 vs 前日80万手）",
                "pattern": "底部反转+涨停突破",
                "m56_signal": "STRONG_BUY",
            },
            "hormuz_exposure": "INDIRECT",
            "exposure_desc": "石油服务股，通航恢复后业务量增加",
        },
        "600428": {  # 中远海特
            "name": "中远海特",
            "price": 8.36,
            "change_pct": 2.58,
            "volume_k_lots": 609.8,
            "amount_yuan": 5.137e8,
            "turnover_rate": 2.49,
            "kline_5d": {
                "20260616": {"open": 7.50, "high": 7.60, "low": 7.40, "close": 7.45},
                "20260617": {"open": 7.48, "high": 7.80, "low": 7.45, "close": 7.65},
                "20260618": {"open": 7.62, "high": 7.85, "low": 7.55, "close": 7.70},
                "20260622": {"open": 7.72, "high": 8.10, "low": 7.65, "close": 8.05},
                "20260623": {"open": 8.20, "high": 8.54, "low": 8.15, "close": 8.36},
            },
            "trend_analysis": {
                "5d_return": "+12.2% (7.45→8.36)",
                "volume_trend": "温和放量",
                "pattern": "稳步上涨+连续阳线",
                "m56_signal": "BUY",
            },
            "hormuz_exposure": "DIRECT",
            "exposure_desc": "特种运输船东，受益于通航恢复后的特种货物运输需求增加",
        },
    },
}

# ============================================================
# V13.2 舆情驱动评分
# ============================================================

def compute_event_driven_score(stock_code: str) -> dict:
    """
    基于霍尔木兹事件驱动评分
    评分维度：
    1. 事件暴露度 (0-30分)
    2. 技术面(M56尾盘+K线形态) (0-30分)
    3. 资金面(今日涨跌幅+换手率+内外盘) (0-25分)
    4. 基本面(估值+业绩) (0-15分)
    """
    data = TDX_MARKET_DATA["top_stocks"].get(stock_code, {})
    if not data:
        return {"score": 0.0, "reason": "无数据"}
    
    score = 0.0
    reasons = []
    
    # 1. 事件暴露度 (0-30分)
    exposure = data.get("hormuz_exposure", "NONE")
    if exposure == "DIRECT":
        score += 25
        reasons.append("直接受益(+25)")
    elif exposure == "INDIRECT":
        score += 15
        reasons.append("间接受益(+15)")
    else:
        score += 5
        reasons.append("无直接暴露(+5)")
    
    # 2. 技术面 (0-30分)
    trend = data.get("trend_analysis", {})
    pattern = trend.get("pattern", "")
    if "突破" in pattern or "V型反转" in pattern:
        score += 20
        reasons.append("技术突破(+20)")
    elif "稳步上涨" in pattern:
        score += 15
        reasons.append("趋势向上(+15)")
    else:
        score += 8
        reasons.append("技术中性(+8)")
    
    # 3. 资金面 (0-25分)
    change_pct = data.get("change_pct", 0)
    turnover = data.get("turnover_rate", 0)
    
    if change_pct >= 9.0:  # 涨停
        score += 25
        reasons.append("涨停强势(+25)")
    elif change_pct >= 5.0:
        score += 18
        reasons.append("大涨(+18)")
    elif change_pct >= 3.0:
        score += 12
        reasons.append("上涨(+12)")
    else:
        score += 6
        reasons.append("温和(+6)")
    
    if turnover >= 5.0:  # 高换手
        score += 10
        reasons.append("高换手(+10)")
    elif turnover >= 2.0:
        score += 6
        reasons.append("中等换手(+6)")
    
    # 4. 基本面 (0-15分)
    # 简化：用PE估算
    if stock_code == "601872":  # 招商轮船 PE=20.3
        score += 10
        reasons.append("估值合理PE20(+10)")
    elif stock_code == "600506":  # 统一股份（ Petroleum service）
        score += 8
        reasons.append("服务股受益(+8)")
    else:
        score += 5
        reasons.append("基本面中性(+5)")
    
    return {
        "code": stock_code,
        "name": data.get("name", ""),
        "event_score": round(score, 1),
        "recommendation": "STRONG_BUY" if score >= 75 else "BUY" if score >= 60 else "WATCH",
        "reasons": reasons,
        "risk_warning": _gen_risk_warning(stock_code, data),
    }

def _gen_risk_warning(code: str, data: dict) -> str:
    """生成风险提示"""
    warnings = []
    
    # 涨停股风险提示
    if data.get("limit_up"):
        warnings.append("⚠️ 已涨停，明日开盘需观察是否继续强势")
    
    # 高换手风险提示
    if data.get("turnover_rate", 0) >= 5.0:
        warnings.append("⚠️ 高换手，注意短期波动风险")
    
    # 事件驱动风险
    if code in ("601872", "600428"):
        warnings.append("⚠️ 地缘事件驱动，若谈判反复可能回撤")
    
    if not warnings:
        warnings.append("✅ 风险可控")
    
    return " | ".join(warnings)

# ============================================================
# 主分析函数
# ============================================================

def analyze_hormuz_event() -> dict:
    """完整分析霍尔木兹事件对A股的影响"""
    
    print("=" * 70)
    print("  毕方灵犀·天眼 V13.2 — 霍尔木兹海峡事件分析报告")
    print("  事件: 霍尔木兹海峡恢复通航")
    print("  日期: 2026-06-23")
    print("  分析时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 70)
    
    # Step 1: 事件影响分析
    print("\n【一、事件影响分析】")
    for sector, impl in HORMUZ_EVENT["market_implications"].items():
        direction = impl["direction"]
        strength = impl["strength"]
        emoji = "📈" if direction == "BULLISH" else "📉" if direction == "BEARISH" else "📊"
        print(f"\n  {emoji} {sector}板块: {direction} ({strength})")
        for logic in impl["logic"][:2]:  # 只显示前两条逻辑
            print(f"    - {logic}")
    
    # Step 2: TDX市场数据
    print("\n【二、TDX市场数据（6月23日收盘）】")
    for sector, perf in TDX_MARKET_DATA["sector_performance"].items():
        print(f"\n  📊 {sector}板块:")
        print(f"    样本数: {perf['total_stocks']}只 | 涨停: {perf['limit_up']}只 | 平均涨幅: {perf['avg_change_pct']}%")
        print(f"    龙头: {perf['leader']}")
    
    # Step 3: 事件驱动评分
    print("\n【三、事件驱动评分（V13.2舆情增强版）】")
    scores = []
    for code in TDX_MARKET_DATA["top_stocks"]:
        result = compute_event_driven_score(code)
        scores.append(result)
    
    # 按评分排序
    scores.sort(key=lambda x: x["event_score"], reverse=True)
    
    for i, r in enumerate(scores, 1):
        rec_emoji = "🟢" if r["recommendation"] == "STRONG_BUY" else "🔵" if r["recommendation"] == "BUY" else "🟡"
        print(f"\n  {i}. {rec_emoji} {r['name']}({r['code']})")
        print(f"      事件评分: {r['event_score']}/100 | 推荐: {r['recommendation']}")
        print(f"      理由: {', '.join(r['reasons'])}")
        print(f"      风险: {r['risk_warning']}")
    
    # Step 4: 交易建议
    print("\n【四、T+1交易建议（2026-06-24开盘）】")
    print("\n  📋 推荐买入列表:")
    for i, r in enumerate(scores[:3], 1):
        data = TDX_MARKET_DATA["top_stocks"][r["code"]]
        print(f"\n    {i}. {r['name']}({r['code']}) @ {data['price']}")
        print(f"       建议动作: {'竞价买入' if r['event_score'] >= 75 else '开盘观察+分批买入'}")
        print(f"       止损位: {data['price'] * 0.97:.2f} (-3%)")
        print(f"       止盈位: {data['price'] * 1.09:.2f} (+9% 或涨停)")
        print(f"       持有目标: T+1涨停 或 T+2续涨")
    
    # Step 5: 风险提示
    print("\n【五、风险提示】")
    print("\n  ⚠️  1. 事件驱动型交易具有高度不确定性")
    print("      若美伊谈判出现反复，航运股可能回撤")
    print("\n  ⚠️  2. 6月23日已大幅上涨，存在短期获利回吐压力")
    print("      建议：分批买入，勿追高")
    print("\n  ⚠️  3. 石油板块逻辑复杂")
    print("      通航恢复→油价下跌→石油开采股承压")
    print("      但中国石油服务股受益于业务量增加")
    print("\n  ⚠️  4. 明日（6月24日）需观察:")
    print("      - 招商轮船是否继续上涨（确认趋势）")
    print("      - 统一股份涨停后是否继续走强")
    print("      - 若开盘大幅高开（>7%），注意追高风险")
    
    # Step 6: V13.2系统指令
    print("\n【六、V13.2系统指令】")
    print("\n  🤖 1. 14:30自动化已升级为V13.2 TDX实时集成")
    print("     明日14:30将自动运行TDX实时数据分析")
    print("\n  🤖 2. 15:10奖惩引擎将首次评估T+1表现")
    print("     若今日推荐股票在明日涨停→S级奖励(+100分)")
    print("\n  🤖 3. 进化引擎将持续优化事件驱动策略")
    print("     目标：提升事件驱动型选股的命中率至60%+")
    
    return {
        "event": HORMUZ_EVENT["event_name"],
        "analysis_time": datetime.now().isoformat(),
        "top_recommendations": scores[:3],
        "sector_analysis": TDX_MARKET_DATA["sector_performance"],
    }

# ============================================================
# HTML报告生成
# ============================================================

def generate_html_report(analysis_result: dict) -> str:
    """生成交互式HTML报告"""
    
    scores = analysis_result.get("top_recommendations", [])
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>毕方灵犀·天眼 V13.2 — 霍尔木兹海峡事件分析报告</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0a0e17; color: #e1e8f0; min-height: 100vh; }}
        .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
        
        .header {{ text-align: center; padding: 30px 0; background: linear-gradient(135deg, #1a1f3a 0%, #2a3050 100%); border-radius: 12px; margin-bottom: 20px; }}
        .header h1 {{ font-size: 26px; color: #00d4ff; margin-bottom: 8px; }}
        .header .subtitle {{ color: #8899aa; font-size: 14px; }}
        
        .event-card {{ background: #111827; border-radius: 12px; padding: 20px; margin-bottom: 20px; border-left: 4px solid #00d4ff; }}
        .event-card h2 {{ color: #00d4ff; margin-bottom: 12px; font-size: 18px; }}
        .event-fact {{ background: #0a0e17; padding: 8px 12px; border-radius: 6px; margin: 6px 0; font-size: 13px; color: #aabbcc; }}
        
        .score-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 16px; margin: 20px 0; }}
        .score-card {{ background: #111827; border-radius: 12px; padding: 20px; border: 1px solid #2a3050; transition: all 0.3s; }}
        .score-card:hover {{ border-color: #00d4ff; transform: translateY(-2px); }}
        .score-card.strong-buy {{ border-color: #00ff88; }}
        .score-card.buy {{ border-color: #00d4ff; }}
        .stock-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }}
        .stock-name {{ font-size: 18px; font-weight: 600; }}
        .stock-code {{ color: #8899aa; font-size: 13px; }}
        .event-score {{ font-size: 28px; font-weight: 700; }}
        .event-score.high {{ color: #00ff88; }}
        .event-score.medium {{ color: #00d4ff; }}
        .reason-list {{ list-style: none; font-size: 12px; color: #aabbcc; }}
        .reason-list li {{ padding: 3px 0; }}
        .risk-warning {{ background: #1a0a0a; border: 1px solid #ff4444; border-radius: 6px; padding: 8px 12px; margin-top: 10px; font-size: 12px; color: #ff8888; }}
        
        .sector-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 12px; margin: 20px 0; }}
        .sector-card {{ background: #111827; border-radius: 10px; padding: 16px; }}
        .sector-card.bullish {{ border-left: 4px solid #00ff88; }}
        .sector-card.bearish {{ border-left: 4px solid #ff4444; }}
        .sector-name {{ font-size: 16px; font-weight: 600; margin-bottom: 8px; }}
        
        .trading-plan {{ background: #111827; border-radius: 12px; padding: 20px; margin: 20px 0; border: 1px solid #2a5030; }}
        .trading-plan h2 {{ color: #00ff88; margin-bottom: 12px; }}
        .plan-item {{ background: #0a0e17; padding: 12px; border-radius: 8px; margin: 8px 0; }}
        
        .footer {{ text-align: center; padding: 20px; color: #556677; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🏛️ 毕方灵犀·天眼 V13.2</h1>
            <div class="subtitle">霍尔木兹海峡恢复通航 — 事件驱动分析报告</div>
            <div class="subtitle">分析时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | 数据基准: 2026-06-23 收盘</div>
        </div>
        
        <div class="event-card">
            <h2>📢 事件概要</h2>
            <div class="event-fact">📅 日期: 2026-06-23 | 来源: 新华社/财联社</div>
            <div class="event-fact">📝 美国总统特朗普同意允许霍尔木兹海峡保持开放，不再实施海上封锁</div>
            <div class="event-fact">🚢 22日至少36艘商船穿越海峡，为2月底以来单日最高</div>
            <div class="event-fact">📈 通航量已恢复至战前近1/3水平</div>
            <div class="event-fact">🤝 美伊17日签署谅解备忘录：美解除封锁，伊确保60天内免费通航</div>
        </div>
        
        <h2 style="color: #00d4ff; margin: 20px 0 12px;">📊 板块影响分析</h2>
        <div class="sector-grid">
            <div class="sector-card bullish">
                <div class="sector-name">📈 航运板块 — BULLISH</div>
                <div>通航恢复→航运需求激增→运价上涨</div>
                <div style="margin-top: 8px; color: #00ff88;">6月23日: 招商轮船+5.68% | 板块平均+1.20%</div>
            </div>
            <div class="sector-card bullish">
                <div class="sector-name">📈 石油服务 — BULLISH</div>
                <div>通航恢复后业务量增加</div>
                <div style="margin-top: 8px; color: #00ff88;">6月23日: 统一股份+9.99%涨停!</div>
            </div>
            <div class="sector-card bullish">
                <div class="sector-name">📈 石化板块 — BULLISH</div>
                <div>原油供应增加→成本下降→利润扩大</div>
                <div style="margin-top: 8px; color: #00ff88;">间接受益于油价下跌</div>
            </div>
            <div class="sector-card bearish">
                <div class="sector-name">📉 军工板块 — BEARISH（弱）</div>
                <div>地缘紧张缓和→订单预期下降</div>
                <div style="margin-top: 8px; color: #ff8888;">但长期国防预算增长不变</div>
            </div>
        </div>
        
        <h2 style="color: #00d4ff; margin: 20px 0 12px;">🎯 事件驱动评分（V13.2舆情增强版）</h2>
        <div class="score-grid">
"""
    
    for r in scores:
        card_class = "strong-buy" if r["recommendation"] == "STRONG_BUY" else "buy"
        score_class = "high" if r["event_score"] >= 75 else "medium"
        data = TDX_MARKET_DATA["top_stocks"].get(r["code"], {})
        
        html += f"""
            <div class="score-card {card_class}">
                <div class="stock-header">
                    <div>
                        <div class="stock-name">{r['name']} <span class="stock-code">{r['code']}</span></div>
                    </div>
                    <div class="event-score {score_class}">{r['event_score']}</div>
                </div>
                <div style="font-size: 13px; color: #aabbcc; margin-bottom: 8px;">
                    现价: {data.get('price', '?')} | 涨幅: +{data.get('change_pct', 0)}% | 换手: {data.get('turnover_rate', 0)}%
                </div>
                <ul class="reason-list">
"""
        
        for reason in r["reasons"]:
            html += f"                    <li>✅ {reason}</li>\n"
        
        html += f"""
                </ul>
                <div class="risk-warning">{r['risk_warning']}</div>
            </div>
"""
    
    html += f"""
        </div>
        
        <div class="trading-plan">
            <h2>📋 T+1交易建议（2026-06-24开盘）</h2>
"""
    
    for i, r in enumerate(scores[:3], 1):
        data = TDX_MARKET_DATA["top_stocks"].get(r["code"], {})
        price = data.get("price", 0)
        
        html += f"""
            <div class="plan-item">
                <div style="font-size: 16px; font-weight: 600; color: #00ff88;">{i}. {r['name']}({r['code']}) @ {price}</div>
                <div style="margin: 8px 0; font-size: 13px; color: #aabbcc;">
                    建议动作: <span style="color: #00ff88;">{'竞价买入' if r['event_score'] >= 75 else '开盘观察+分批买入'}</span><br>
                    止损位: <span style="color: #ff4444;">{price * 0.97:.2f} (-3%)</span><br>
                    止盈位: <span style="color: #00ff88;">{price * 1.09:.2f} (+9% 或涨停)</span><br>
                    持有目标: T+1涨停 或 T+2续涨
                </div>
            </div>
"""
    
    html += """
            <div style="margin-top: 16px; padding: 12px; background: #1a0a0a; border-radius: 8px; font-size: 13px; color: #ff8888;">
                ⚠️ 风险提示:<br>
                1. 事件驱动型交易具有高度不确定性，若美伊谈判出现反复，航运股可能回撤<br>
                2. 6月23日已大幅上涨，存在短期获利回吐压力，建议分批买入<br>
                3. 明日需观察: 招商轮船是否继续上涨（确认趋势），若开盘大幅高开(>7%)，注意追高风险
            </div>
        </div>
        
        <div class="footer">
            毕方灵犀·天眼 V13.2 — 奖惩驱动自主进化系统 | 生成时间: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """<br>
            本報告僅供參考，不構成投資建議。市場有風險，投資需謹慎。
        </div>
    </div>
</body>
</html>
"""
    
    return html

# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":
    # 运行分析
    result = analyze_hormuz_event()
    
    # 生成HTML报告
    html_content = generate_html_report(result)
    output_file = os.path.join("data", f"V13_2_霍尔木兹事件分析_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html")
    
    os.makedirs("data", exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    print(f"\n\n{'=' * 70}")
    print(f"  ✅ HTML报告已生成: {output_file}")
    print(f"{'=' * 70}")
