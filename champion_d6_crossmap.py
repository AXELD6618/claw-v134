#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.52+53 60只隐形冠军 × D6知识库交叉映射 + 7/13最终融合策略
================================================================
1. D6知识库15大主题 × 60只冠军股票交叉映射
2. 12只BUY候选 × 6只现有持仓融合策略
3. 7/13下周一T4 14:30选股最终操作方案
4. T+1退出策略 + 仓位管理
"""

import json
import os
from datetime import datetime

# ============ 路径 ============
BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "outputs")

# ============ 现有持仓 ============
HOLDINGS = [
    {"code": "600118", "name": "中国卫星", "shares": 2000, "cost": 83.469, "sector": "商业航天",
     "d6_themes": ["T2_COMMERCIAL_SPACE", "T10_IRAN_US_CONFLICT", "T7_GERMAN_MILITARY_SAT"],
     "d6_level": "STRONG×3", "d6_boost": 45, "strategy": "HOLD/加仓",
     "logic": "T2商业航天(STRONG)+T10伊朗冲突(STRONG)+T7德国军事卫星(LONG-TERM)三重催化，国防安全超级主线"},
    {"code": "920249", "name": "利尔达", "shares": 145, "cost": 10.807, "sector": "半导体",
     "d6_themes": ["T1_CXMT_IPO", "T11_HELIUM_EXPORT_BAN"],
     "d6_level": "STRONG×2", "d6_boost": 30, "strategy": "HOLD观望",
     "logic": "T1长鑫IPO+T11氦气禁令双催化，但T15盈利泡沫HIGH风险→D6加权0.5，小仓位持有"},
    {"code": "300287", "name": "飞利信", "shares": 1700, "cost": 3.793, "sector": "信息技术",
     "d6_themes": ["T12_PBOC_Q2_EASING", "T9_CHINA_INDUSTRIAL_CAPACITY"],
     "d6_level": "MEDIUM×2", "d6_boost": 16, "strategy": "HOLD",
     "logic": "T12央行支持科技+T9工业产能双MEDIUM，AI/信创长期受益"},
    {"code": "300017", "name": "网宿科技", "shares": 500, "cost": 12.523, "sector": "AI算力",
     "d6_themes": ["T14_EARNINGS_PRE_ANNOUNCE", "T12_PBOC_Q2_EASING", "T13_FED_SEMIANNUAL_REPORT"],
     "d6_level": "STRONG+MEDIUM×2", "d6_boost": 31, "strategy": "HOLD/加仓",
     "logic": "T14东阳光算力合同+T12央行支持科技+T13美联储AI基建推高通胀→AI算力链三重共振"},
    {"code": "600255", "name": "鑫科材料", "shares": 3300, "cost": 3.055, "sector": "有色金属",
     "d6_themes": ["T10_IRAN_US_CONFLICT", "T8_US_DEFENSE_PURGE"],
     "d6_level": "STRONG+LONG-TERM", "d6_boost": 18, "strategy": "HOLD",
     "logic": "T10地缘冲突推升战略资源+T8军工清洗稀土价值，有色金属间接受益"},
    {"code": "000958", "name": "电投产融", "shares": 4200, "cost": 5.370, "sector": "能源金融",
     "d6_themes": ["T10_IRAN_US_CONFLICT"],
     "d6_level": "STRONG", "d6_boost": 15, "strategy": "HOLD/加仓",
     "logic": "T10霍尔木兹海峡中断→全球石油供应链危机→能源价格上行→电力能源板块受益，D6从LONG-TERM升级STRONG"},
]

# ============ D6知识库15大主题 × 冠军股票交叉映射 ============
D6_CHAMPION_CROSSMAP = {
    "T2_COMMERCIAL_SPACE": {
        "name": "商业航天(国家天字号工程)",
        "level": "STRONG", "hotness": 98,
        "champion_overlap": [],
        "portfolio_overlap": ["600118中国卫星"],
        "synergy": "持仓600118直接受益，冠军池无直接重叠但高端装备赛道(新强联/国茂股份)间接受益于航天制造",
    },
    "T10_IRAN_US_CONFLICT": {
        "name": "伊朗-美国军事冲突(地缘黑天鹅)",
        "level": "STRONG", "hotness": 99,
        "champion_overlap": [],
        "portfolio_overlap": ["600118中国卫星", "000958电投产融", "600255鑫科材料"],
        "synergy": "三持仓同时受益! 军工/石油/黄金/航运四大板块同步催化。冠军池无直接标的但化工(新和成/浙江龙盛)受益于油价上行",
    },
    "T1_CXMT_IPO": {
        "name": "长鑫存储IPO",
        "level": "STRONG", "hotness": 95,
        "champion_overlap": ["宏发股份(600885)", "万业企业(600641)"],
        "portfolio_overlap": ["920249利尔达"],
        "synergy": "冠军池半导体标的(宏发/万业)受益但T15 HIGH风险→D6加权0.5。7/16申购日前为最强催化窗口",
    },
    "T11_HELIUM_EXPORT_BAN": {
        "name": "中国禁止氦气出口",
        "level": "STRONG", "hotness": 92,
        "champion_overlap": ["华特气体(688268)PASS", "宏发股份(600885)WATCH"],
        "portfolio_overlap": ["920249利尔达"],
        "synergy": "华特气体在冠军池中但-14.83%暴跌被PASS。氦气禁令→海外晶圆厂减产→存储涨价加速→间接利好",
    },
    "T14_EARNINGS_PRE_ANNOUNCE": {
        "name": "业绩预增公告群",
        "level": "STRONG", "hotness": 96,
        "champion_overlap": ["新和成(002001)BUY-83.6", "惠泰医疗(688617)BUY-82.0"],
        "portfolio_overlap": ["300017网宿科技"],
        "synergy": "★关键映射! 新和成PE12.1+Safe95=低估值高安全，业绩预增+化工涨价双驱动。惠泰医疗PE31.6+Safe95=医疗器械高成长",
    },
    "T15_EARNINGS_BUBBLE_RISK": {
        "name": "盈利泡沫风险评估",
        "level": "RISK", "hotness": 85,
        "champion_overlap": ["半导体HIGH(宏发/万业)", "光通信MODERATE(腾景/锐科)", "新能源MODERATE(德业/海优)"],
        "portfolio_overlap": ["920249利尔达"],
        "synergy": "★V53盈泡引擎已正确过滤! 半导体HIGH→D6惩罚0.5+全局-5。冠军池BUY标的全为LOW风险，策略有效",
    },
    "T3_NUCLEAR_FUSION": {
        "name": "核聚变(第四次工业革命)",
        "level": "MEDIUM", "hotness": 75,
        "champion_overlap": ["新强联(300850)BUY-70.6"],
        "portfolio_overlap": [],
        "synergy": "新强联(风电主轴→核聚变超导磁体配套)间接受益，中长期催化",
    },
    "T4_PHOSPHATE_EXPORT_BAN": {
        "name": "日本化肥危机-磷肥出口限制",
        "level": "MEDIUM", "hotness": 70,
        "champion_overlap": ["新和成(002001)BUY-83.6", "浙江龙盛(600352)WATCH-68.9", "晨光生物(300138)WATCH-69.7"],
        "portfolio_overlap": [],
        "synergy": "新和成(精细化工+新材料)间接受益于化工涨价周期，磷矿战略资源重估支撑化工板块估值",
    },
    "T5_JULANG2_TEST": {
        "name": "巨浪-2全射程试射",
        "level": "STRONG", "hotness": 72,
        "champion_overlap": [],
        "portfolio_overlap": ["600118中国卫星"],
        "synergy": "军工板块催化，持仓600118直接受益",
    },
    "T6_VW_RESTRUCTURING": {
        "name": "大众汽车关厂裁员",
        "level": "LONG-TERM", "hotness": 55,
        "champion_overlap": ["新强联(300850)BUY", "金雷股份(300443)WATCH"],
        "synergy": "中国汽车产业链份额提升，风电/汽车零部件标的间接受益",
    },
    "T7_GERMAN_MILITARY_SAT": {
        "name": "德国进攻型军事卫星",
        "level": "LONG-TERM", "hotness": 50,
        "champion_overlap": [],
        "portfolio_overlap": ["600118中国卫星"],
        "synergy": "太空军事化趋势长期催化，持仓600118太空安全概念加持",
    },
    "T8_US_DEFENSE_PURGE": {
        "name": "美国军工清洗",
        "level": "LONG-TERM", "hotness": 55,
        "champion_overlap": [],
        "portfolio_overlap": ["600255鑫科材料"],
        "synergy": "稀土战略价值提升+国产军工替代加速，鑫科材料间接受益",
    },
    "T9_CHINA_INDUSTRIAL_CAPACITY": {
        "name": "中国工业产能全球占比",
        "level": "LONG-TERM", "hotness": 60,
        "champion_overlap": ["中密控股(300470)BUY", "科德数控(688305)BUY", "银都股份(603277)BUY",
                            "华测检测(300012)BUY", "新强联(300850)BUY", "国茂股份(603915)BUY",
                            "绿的谐波(688017)WATCH", "埃斯顿(002747)WATCH"],
        "portfolio_overlap": ["300287飞利信"],
        "synergy": "★核心映射! 高端装备赛道8只冠军标的全部受益于中国制造出海+工业产能扩张长期趋势",
    },
    "T12_PBOC_Q2_EASING": {
        "name": "央行Q2例会: 货币宽松+支持科技",
        "level": "MEDIUM", "hotness": 75,
        "champion_overlap": ["ALL 60 stocks"],
        "portfolio_overlap": ["ALL holdings"],
        "synergy": "★全局映射! 央行宽松+科技创新五大文章→A股估值支撑+科技/AI/半导体/高端制造融资环境改善→全部冠军标的受益",
    },
    "T13_FED_SEMIANNUAL_REPORT": {
        "name": "美联储半年度报告",
        "level": "MEDIUM", "hotness": 70,
        "champion_overlap": ["半导体赛道ALL", "光通信赛道ALL"],
        "portfolio_overlap": ["920249利尔达", "300017网宿科技"],
        "synergy": "美联储确认AI驱动内存涨价→存储涨价周期央行级背书。但9月加息51.1%为外部风险因子需监控",
    },
}

# ============ 12只BUY候选 × 持仓融合策略 ============
def build_fusion_strategy():
    """构建12只BUY候选与6只持仓的融合策略"""

    # 读取冠军评分
    with open(os.path.join(DATA, "champion_60_scores.json"), "r", encoding="utf-8") as f:
        scores = json.load(f)

    buy_candidates = [s for s in scores if s["signal"] == "BUY"]

    # 按赛道分组BUY候选
    sector_groups = {}
    for s in buy_candidates:
        sec = s["sector"]
        if sec not in sector_groups:
            sector_groups[sec] = []
        sector_groups[sec].append(s)

    # 融合策略：持仓去留 + 新买入优先级
    strategy = {
        "date": "2026-07-13",
        "version": "V13.5.52+53",
        "holdings_assessment": [],
        "new_buy_priority": [],
        "position_allocation": {},
        "t1_exit_strategy": {},
        "risk_control": {},
    }

    # === 持仓评估 ===
    for h in HOLDINGS:
        assessment = {
            "code": h["code"], "name": h["name"], "shares": h["shares"], "cost": h["cost"],
            "sector": h["sector"], "d6_level": h["d6_level"], "d6_boost": h["d6_boost"],
            "strategy": h["strategy"], "logic": h["logic"],
            "market_value": round(h["shares"] * h["cost"], 2),
        }
        strategy["holdings_assessment"].append(assessment)

    # === 新买入优先级排序 ===
    # 排序逻辑: 1.盈泡风险(LOW>MOD>HIGH) 2.蒸馏总分 3.D6催化叠加 4.赛道分散
    priority_order = []
    for s in buy_candidates:
        # D6催化叠加评分
        d6_synergy = 0
        d6_themes = []
        if s["sector"] == "化工":
            d6_synergy = 23  # T4磷肥+T14业绩预增+化工涨价
            d6_themes = ["T4_PHOSPHATE(MEDIUM)", "T14_EARNINGS(STRONG)", "T10_IRAN(MEDIUM)"]
        elif s["sector"] == "医疗":
            d6_synergy = 15  # T14业绩预增+国产替代
            d6_themes = ["T14_EARNINGS(STRONG)", "T12_PBOC(MEDIUM)"]
        elif s["sector"] == "高端装备":
            d6_synergy = 18  # T9工业产能+T12央行科技+T6大众关厂
            d6_themes = ["T9_CAPACITY(LONG-TERM)", "T12_PBOC(MEDIUM)", "T6_VW(LONG-TERM)"]
        elif s["sector"] == "新能源":
            d6_synergy = 10  # T12央行科技
            d6_themes = ["T12_PBOC(MEDIUM)"]
        elif s["sector"] == "半导体":
            d6_synergy = 5  # T15 HIGH风险→D6加权0.5
            d6_themes = ["T1_CXMT(STRONG×0.5)", "T11_HELIUM(STRONG×0.5)", "T15_RISK(HIGH)"]
        elif s["sector"] == "光通信":
            d6_synergy = 8  # T13美联储AI+T12央行科技
            d6_themes = ["T13_FED(MEDIUM)", "T12_PBOC(MEDIUM)"]

        bubble_order = {"LOW": 0, "MODERATE": 1, "HIGH": 2}
        priority_order.append({
            "name": s["name"], "code": s["code"], "sector": s["sector"],
            "price": s["price"], "score": s["total_score"],
            "confidence": s["confidence"], "active_dims": s["active_dims"],
            "bubble_risk": s["bubble_risk"], "bubble_order": bubble_order.get(s["bubble_risk"], 3),
            "d6_synergy": d6_synergy, "d6_themes": d6_themes,
            "catalyst": s["catalyst"],
            "fusion_score": round(s["total_score"] + d6_synergy * 0.3, 1),
        })

    # 按融合分排序
    priority_order.sort(key=lambda x: (-x["bubble_order"] * -1, -x["fusion_score"]))
    # 重新按 bubble_order ASC, fusion_score DESC
    priority_order.sort(key=lambda x: (x["bubble_order"], -x["fusion_score"]))

    for i, p in enumerate(priority_order):
        p["priority_rank"] = i + 1
        strategy["new_buy_priority"].append(p)

    # === 仓位分配 ===
    total_capital = sum(h["shares"] * h["cost"] for h in HOLDINGS)
    strategy["position_allocation"] = {
        "total_holdings_value": round(total_capital, 2),
        "available_capital_ratio": "30-40%(释放获利仓位)",
        "allocation_rules": [
            "第一优先(LOW风险+高融合分): 新和成/惠泰医疗/中密控股 — 各分配可用资金25-30%",
            "第二优先(LOW风险+中融合分): 科德数控/爱博医疗/健帆生物/银都股份 — 各分配15-20%",
            "第三优先(LOW风险+较低分): 百普赛斯/华测检测/新强联/国茂股份 — 各分配10-15%",
            "MODERATE风险(德业股份): 仅T+1短线，仓位减半，分配5-10%",
            "HIGH风险(半导体赛道): 不新买入，仅持有现有仓位",
        ],
        "kelly_fraction": "Kelly公式: f=(bp-q)/b, 实际取Kelly值的1/2-1/3",
        "max_single_position": "单只不超过总仓位15%",
        "max_sector_position": "单赛道不超过总仓位35%",
    }

    # === T+1退出策略 ===
    strategy["t1_exit_strategy"] = {
        "core_rule": "T日尾盘买入→T+1日获利5-10%卖出50%→涨停则持有至打开涨停",
        "exit_levels": [
            {"level": "STOP_LOSS", "condition": "T+1跌幅>3%", "action": "立即止损卖出全部"},
            {"level": "PROFIT_TAKE_1", "condition": "T+1涨幅5-8%", "action": "卖出50%锁定利润"},
            {"level": "PROFIT_TAKE_2", "condition": "T+1涨幅8-12%", "action": "卖出30%，留20%搏连板"},
            {"level": "LIMIT_UP_HOLD", "condition": "T+1涨停", "action": "持有至打开涨停日卖出"},
            {"level": "LIMIT_UP_2", "condition": "连续2涨停", "action": "卖出50%，留50%搏3板"},
            {"level": "LIMIT_UP_3", "condition": "连续3涨停", "action": "卖出全部，不贪第4板"},
        ],
        "compound_rule": "T+1复利滚动: 每次卖出资金立即投入下一只BUY候选，实现复利增长",
        "time_window": "T+1 14:30-15:00为最佳卖出窗口(尾盘流动性最好)",
    }

    # === 风险控制 ===
    strategy["risk_control"] = {
        "v53_bubble_filter": "V13.5.53盈泡引擎: HIGH风险→D6加权0.5+全局-5+仓位减半 | MODERATE→D6加权0.8",
        "max_loss_per_day": "单日最大亏损: 总仓位3%(约15000元)",
        "max_trades_per_day": "单日最多交易5只(避免过度分散)",
        "circuit_breaker": "连续2日亏损→暂停1日，重新评估市场",
        "d6_monitoring": "持续监控D6知识库15大主题热度变化，T10伊朗冲突/T1长鑫IPO/T14业绩预增为核心驱动",
        "fed_risk": "T13美联储9月加息概率51.1%→若实际加息，全球科技股承压→减仓半导体/光通信",
    }

    return strategy, D6_CHAMPION_CROSSMAP


# ============ 生成HTML报告 ============
def generate_html(strategy, crossmap):
    """生成综合HTML报告"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.5.52+53 7/13最终融合策略 — 60只隐形冠军 × D6知识库 × 6只持仓</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0a0e1a; color: #e0e6f0; font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; padding: 20px; }}
h1 {{ color: #00d4ff; font-size: 24px; text-align: center; margin-bottom: 5px; }}
h2 {{ color: #00d4ff; font-size: 18px; margin: 20px 0 10px; border-bottom: 1px solid #1e2a4a; padding-bottom: 5px; }}
h3 {{ color: #ffa500; font-size: 15px; margin: 15px 0 8px; }}
.subtitle {{ text-align: center; color: #8899bb; font-size: 13px; margin-bottom: 20px; }}
.card {{ background: #111827; border: 1px solid #1e2a4a; border-radius: 8px; padding: 15px; margin-bottom: 15px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 20px; }}
.stat-card {{ background: linear-gradient(135deg, #1a2332, #0f1620); border: 1px solid #1e3a5f; border-radius: 8px; padding: 15px; text-align: center; }}
.stat-value {{ font-size: 28px; font-weight: bold; color: #00d4ff; }}
.stat-label {{ font-size: 12px; color: #8899bb; margin-top: 5px; }}
table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; font-size: 13px; }}
th {{ background: #1a2332; color: #00d4ff; padding: 8px; text-align: left; border-bottom: 2px solid #1e3a5f; }}
td {{ padding: 6px 8px; border-bottom: 1px solid #1e2a4a; }}
tr:hover {{ background: #141b2d; }}
.buy {{ color: #ff4444; font-weight: bold; }}
.watch {{ color: #ffaa00; }}
.pass {{ color: #666; }}
.hold {{ color: #00d4ff; }}
.low {{ color: #44ff44; }}
.moderate {{ color: #ffaa00; }}
.high {{ color: #ff4444; }}
.strong {{ color: #ff4444; font-weight: bold; }}
.medium {{ color: #ffaa00; }}
.longterm {{ color: #8899bb; }}
.risk {{ color: #ff00ff; }}
.priority-badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; margin-right: 5px; }}
.p1 {{ background: #ff4444; color: #fff; }}
.p2 {{ background: #ff8800; color: #fff; }}
.p3 {{ background: #ffaa00; color: #000; }}
.sector-tag {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-right: 3px; }}
.tag-chemical {{ background: #2d4a2d; color: #88ff88; }}
.tag-medical {{ background: #4a2d4a; color: #ff88ff; }}
.tag-equipment {{ background: #2d3a5a; color: #88aaff; }}
.tag-energy {{ background: #5a4a2d; color: #ffaa44; }}
.tag-semi {{ background: #5a2d2d; color: #ff8888; }}
.tag-optical {{ background: #2d5a4a; color: #44ffaa; }}
.tag-space {{ background: #3a2d5a; color: #aa88ff; }}
.tag-finance {{ background: #4a4a2d; color: #dddd88; }}
.tag-tech {{ background: #2d5a5a; color: #88dddd; }}
.tag-metal {{ background: #5a3a2d; color: #ffaa88; }}
.d6-theme {{ display: inline-block; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin: 1px; }}
.theme-strong {{ background: #4a1a1a; color: #ff6666; border: 1px solid #ff4444; }}
.theme-medium {{ background: #4a3a1a; color: #ffaa44; border: 1px solid #ff8800; }}
.theme-long {{ background: #2a2a3a; color: #8899bb; border: 1px solid #445566; }}
.theme-risk {{ background: #3a1a3a; color: #ff44ff; border: 1px solid #ff00ff; }}
.strategy-box {{ background: #0f1620; border-left: 3px solid #00d4ff; padding: 10px 15px; margin: 10px 0; font-size: 13px; }}
.alert-box {{ background: #2a1a1a; border-left: 3px solid #ff4444; padding: 10px 15px; margin: 10px 0; font-size: 13px; }}
.success-box {{ background: #1a2a1a; border-left: 3px solid #44ff44; padding: 10px 15px; margin: 10px 0; font-size: 13px; }}
.footer {{ text-align: center; color: #445566; font-size: 11px; margin-top: 30px; padding-top: 15px; border-top: 1px solid #1e2a4a; }}
.crossmap-grid {{ display: grid; grid-template-columns: 1fr; gap: 8px; }}
.crossmap-item {{ background: #111827; border: 1px solid #1e2a4a; border-radius: 6px; padding: 10px; }}
.crossmap-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }}
.crossmap-title {{ color: #00d4ff; font-size: 13px; font-weight: bold; }}
.crossmap-synergy {{ color: #8899bb; font-size: 12px; margin-top: 5px; }}
</style>
</head>
<body>

<h1>V13.5.52+53 | 7/13下周一最终融合策略</h1>
<p class="subtitle">60只隐形冠军 × D6知识库15大主题 × 6只现有持仓 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>

<!-- 统计卡片 -->
<div class="stats-grid">
    <div class="stat-card">
        <div class="stat-value">{len(HOLDINGS)}</div>
        <div class="stat-label">现有持仓</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">12</div>
        <div class="stat-label">BUY候选</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">15</div>
        <div class="stat-label">D6知识库主题</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{sum(1 for s in strategy['new_buy_priority'] if s['bubble_risk']=='LOW')}</div>
        <div class="stat-label">LOW风险BUY</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">¥{strategy['position_allocation']['total_holdings_value']:,.0f}</div>
        <div class="stat-label">持仓总市值</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">6</div>
        <div class="stat-label">赛道覆盖</div>
    </div>
</div>

<!-- 第一部分: 持仓评估 -->
<h2>一、现有6只持仓评估 (D6催化叠加)</h2>
<table>
<tr>
    <th>代码</th><th>名称</th><th>赛道</th><th>持仓</th><th>成本</th><th>市值</th>
    <th>D6催化等级</th><th>D6加分</th><th>策略</th><th>催化逻辑</th>
</tr>
"""

    for h in strategy["holdings_assessment"]:
        sector_class = f"tag-{h['sector'][:4].lower()}" if h['sector'] else ""
        html += f"""<tr>
    <td>{h['code']}</td>
    <td><span class="sector-tag {sector_class}">{h['sector']}</span> {h['name']}</td>
    <td>{h['sector']}</td>
    <td>{h['shares']}股</td>
    <td>¥{h['cost']:.3f}</td>
    <td>¥{h['market_value']:,.0f}</td>
    <td><span class="strong">{h['d6_level']}</span></td>
    <td>+{h['d6_boost']}</td>
    <td><span class="hold">{h['strategy']}</span></td>
    <td style="font-size:11px;color:#8899bb">{h['logic']}</td>
</tr>"""

    html += """
</table>

<div class="strategy-box">
<b>持仓总结:</b> 6只持仓中，600118中国卫星受T2+T10+T7三重STRONG催化为核心持仓；000958电投产融受T10地缘冲突STRONG催化升级；300017网宿科技受T14+T12+T13三重催化AI算力链受益。总持仓D6加权平均+25.8分，整体偏强。
</div>

<!-- 第二部分: 12只BUY候选融合优先级 -->
<h2>二、12只BUY候选 × D6催化融合优先级排序</h2>
<p style="color:#8899bb;font-size:13px;">排序逻辑: 1.盈泡风险(LOW>MOD>HIGH) → 2.融合分(蒸馏分+D6催化叠加×0.3) → 3.赛道分散</p>
<table>
<tr>
    <th>优先级</th><th>代码</th><th>名称</th><th>赛道</th><th>现价</th>
    <th>蒸馏分</th><th>融合分</th><th>置信度</th><th>活跃维</th>
    <th>盈泡风险</th><th>D6催化</th><th>D6主题映射</th>
</tr>
"""

    # 按优先级排序的颜色映射
    priority_colors = {1: "p1", 2: "p2", 3: "p3"}
    for p in strategy["new_buy_priority"]:
        sec_short = p["sector"][:4].lower()
        bubble_class = p["bubble_risk"].lower()
        badge_class = priority_colors.get(p["priority_rank"], "p3")

        d6_themes_html = ""
        for t in p["d6_themes"]:
            if "STRONG" in t:
                d6_themes_html += f'<span class="d6-theme theme-strong">{t}</span>'
            elif "MEDIUM" in t:
                d6_themes_html += f'<span class="d6-theme theme-medium">{t}</span>'
            elif "LONG" in t:
                d6_themes_html += f'<span class="d6-theme theme-long">{t}</span>'
            elif "RISK" in t or "HIGH" in t:
                d6_themes_html += f'<span class="d6-theme theme-risk">{t}</span>'
            else:
                d6_themes_html += f'<span class="d6-theme theme-long">{t}</span>'

        html += f"""<tr>
    <td><span class="priority-badge {badge_class}">#{p['priority_rank']}</span></td>
    <td>{p['code']}</td>
    <td><span class="sector-tag tag-{sec_short}">{p['sector']}</span> {p['name']}</td>
    <td>¥{p['price']:.2f}</td>
    <td><b>{p['score']:.1f}</b></td>
    <td style="color:#00d4ff"><b>{p['fusion_score']:.1f}</b></td>
    <td>{p['confidence']}%</td>
    <td>{p['active_dims']}维</td>
    <td><span class="{bubble_class}">{p['bubble_risk']}</span></td>
    <td>+{p['d6_synergy']}</td>
    <td>{d6_themes_html}</td>
</tr>"""

    html += """
</table>

<div class="success-box">
<b>BUY候选关键发现:</b><br>
1. <b>新和成(002001)</b> — 融合分最高! PE12.1+Safe95+8维全活跃+化工涨价+业绩预增+低估值高安全 = 最优T+1标的<br>
2. <b>医疗板块4只BUY</b> — 惠泰/爱博/健帆/百普赛斯全部LOW风险+高SafeValue+国产替代催化 = 防御型T+1组合<br>
3. <b>高端装备6只BUY</b> — 中密/科德/银都/华测/新强联/国茂全部LOW风险+T9工业产能长期趋势 = 稳健型T+1组合<br>
4. <b>德业股份(605117)</b> — 唯一MODERATE风险BUY，新能源赛道盈泡风险42.3分，仅T+1短线<br>
5. <b>半导体0只BUY</b> — V53盈泡HIGH风险正确过滤，19只688xxx暴跌标的全部PASS
</div>

<!-- 第三部分: D6知识库 × 冠军股票交叉映射 -->
<h2>三、D6知识库15大主题 × 60只冠军股票交叉映射</h2>
<div class="crossmap-grid">
"""

    # 按热度排序D6主题
    sorted_themes = sorted(crossmap.items(), key=lambda x: -x[1]["hotness"])

    for theme_id, theme in sorted_themes:
        level_class = "theme-strong" if theme["level"] == "STRONG" else \
                      "theme-medium" if theme["level"] == "MEDIUM" else \
                      "theme-long" if theme["level"] == "LONG-TERM" else "theme-risk"

        champion_html = ", ".join(theme.get("champion_overlap", [])) if theme.get("champion_overlap") else "<span style='color:#445566'>无直接重叠</span>"
        portfolio_html = ", ".join(theme.get("portfolio_overlap", [])) if theme.get("portfolio_overlap") else "<span style='color:#445566'>无</span>"

        html += f"""<div class="crossmap-item">
    <div class="crossmap-header">
        <span class="crossmap-title">{theme_id}: {theme['name']}</span>
        <span><span class="d6-theme {level_class}">{theme['level']}</span> 热度: {theme['hotness']}</span>
    </div>
    <div style="font-size:12px; margin: 3px 0;">
        <b style="color:#00d4ff;">冠军池重叠:</b> {champion_html}<br>
        <b style="color:#ffa500;">持仓重叠:</b> {portfolio_html}
    </div>
    <div class="crossmap-synergy">{theme['synergy']}</div>
</div>"""

    html += """
</div>

<!-- 第四部分: 仓位分配 -->
<h2>四、7/13仓位分配方案</h2>
<div class="card">
"""

    for rule in strategy["position_allocation"]["allocation_rules"]:
        html += f"<div style='margin:5px 0;'>{rule}</div>"

    html += f"""
    <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e2a4a;">
        <b style="color:#ffa500;">Kelly公式:</b> {strategy['position_allocation']['kelly_fraction']}<br>
        <b style="color:#ffa500;">单只上限:</b> {strategy['position_allocation']['max_single_position']}<br>
        <b style="color:#ffa500;">赛道上限:</b> {strategy['position_allocation']['max_sector_position']}<br>
        <b style="color:#ffa500;">可用资金:</b> {strategy['position_allocation']['available_capital_ratio']}
    </div>
</div>

<!-- 第五部分: T+1退出策略 -->
<h2>五、T+1退出策略 (V13.5.45滚动复利)</h2>
<div class="card">
    <div style="margin-bottom:10px;color:#00d4ff;font-weight:bold;">{strategy['t1_exit_strategy']['core_rule']}</div>
    <table>
        <tr><th>退出级别</th><th>触发条件</th><th>操作</th></tr>
"""

    for exit_level in strategy["t1_exit_strategy"]["exit_levels"]:
        color = "#ff4444" if "STOP" in exit_level["level"] else "#44ff44" if "PROFIT" in exit_level["level"] else "#ffa500"
        html += f"""<tr>
            <td style="color:{color};font-weight:bold;">{exit_level['level']}</td>
            <td>{exit_level['condition']}</td>
            <td>{exit_level['action']}</td>
        </tr>"""

    html += f"""
    </table>
    <div style="margin-top:10px;color:#8899bb;font-size:12px;">
        <b>复利规则:</b> {strategy['t1_exit_strategy']['compound_rule']}<br>
        <b>最佳卖出窗口:</b> {strategy['t1_exit_strategy']['time_window']}
    </div>
</div>

<!-- 第六部分: 风险控制 -->
<h2>六、风险控制体系</h2>
<div class="alert-box">
    <b style="color:#ff4444;">V53盈泡引擎:</b> {strategy['risk_control']['v53_bubble_filter']}<br>
    <b style="color:#ff4444;">单日最大亏损:</b> {strategy['risk_control']['max_loss_per_day']}<br>
    <b style="color:#ff4444;">单日最多交易:</b> {strategy['risk_control']['max_trades_per_day']}<br>
    <b style="color:#ff4444;">熔断机制:</b> {strategy['risk_control']['circuit_breaker']}<br>
    <b style="color:#ffaa00;">D6监控:</b> {strategy['risk_control']['d6_monitoring']}<br>
    <b style="color:#ff00ff;">美联储风险:</b> {strategy['risk_control']['fed_risk']}
</div>

<!-- 第七部分: 7/13操作时间表 -->
<h2>七、7/13下周一操作时间表</h2>
<div class="card">
<table>
<tr><th>时间</th><th>任务</th><th>说明</th></tr>
<tr><td style="color:#00d4ff;">07:30</td><td>盘前TDX蒸馏预扫描</td><td>刷新60只冠军TDX数据+8维评分+V52进化权重+D6催化叠加</td></tr>
<tr><td style="color:#00d4ff;">09:25</td><td>集合竞价监控</td><td>观察12只BUY候选开盘价，高开>3%放弃追高</td></tr>
<tr><td style="color:#00d4ff;">10:30</td><td>T0实时蒸馏</td><td>更新实时行情+量比+换手率+主力资金流</td></tr>
<tr><td style="color:#00d4ff;">12:00</td><td>午间驾驶舱</td><td>汇总上午数据+V52进化状态面板+调整下午策略</td></tr>
<tr><td style="color:#ffa500;">14:15</td><td>WINNER精确扫描</td><td>60只冠军+全市场WINNER极致买点扫描+V47反转前兆</td></tr>
<tr><td style="color:#ff4444;font-weight:bold;">14:30</td><td style="font-weight:bold;">★T4核心蒸馏选股</td><td style="font-weight:bold;">读取V52进化权重+V46涨停门控+V47 E[R]+V45 T+1退出+V53盈泡过滤→最终买入决策</td></tr>
<tr><td style="color:#ff4444;">14:50</td><td>执行买入</td><td>按优先级排序执行买入，T日尾盘5分钟内完成</td></tr>
<tr><td style="color:#00d4ff;">15:05</td><td>收盘归档</td><td>V52进化权重快照+持仓更新+信号验证</td></tr>
<tr><td style="color:#00d4ff;">15:10</td><td>TDX真实数据验证</td><td>★V52自主进化引擎运行(权重调优)</td></tr>
<tr><td style="color:#00d4ff;">22:00</td><td>夜间作战计划</td><td>次日策略+V52进化权重检查+D6主题更新</td></tr>
</table>
</div>

<!-- 总结 -->
<h2>八、策略总结</h2>
<div class="strategy-box">
<b style="color:#00d4ff;font-size:15px;">7/13下周一核心策略:</b><br><br>
<b>1. 持仓策略:</b> 6只持仓全部HOLD，600118中国卫星为核心持仓(三重STRONG催化)，000958电投产融和300017网宿科技可逢低加仓<br><br>
<b>2. 新买入策略:</b> 按融合优先级执行12只BUY候选<br>
&nbsp;&nbsp;&nbsp;<b>第一优先(LOW+高融合分):</b> 新和成/惠泰医疗/中密控股 — 化工+医疗+高端装备三赛道分散<br>
&nbsp;&nbsp;&nbsp;<b>第二优先(LOW+中融合分):</b> 科德数控/爱博医疗/健帆生物/银都股份 — 补充赛道深度<br>
&nbsp;&nbsp;&nbsp;<b>第三优先(LOW+较低分):</b> 百普赛斯/华测检测/新强联/国茂股份 — 观察盘中走势择优<br>
&nbsp;&nbsp;&nbsp;<b>谨慎(MODERATE):</b> 德业股份仅T+1短线，仓位减半<br><br>
<b>3. D6催化核心驱动:</b><br>
&nbsp;&nbsp;&nbsp;T10伊朗冲突(STRONG,99热度) → 军工/能源/黄金三线受益<br>
&nbsp;&nbsp;&nbsp;T2商业航天(STRONG,98热度) → 600118核心持仓受益<br>
&nbsp;&nbsp;&nbsp;T14业绩预增(STRONG,96热度) → 新和成/惠泰医疗BUY候选受益<br>
&nbsp;&nbsp;&nbsp;T1长鑫IPO(STRONG,95热度) → 7/16申购日前最强催化窗口<br>
&nbsp;&nbsp;&nbsp;T11氦气禁令(STRONG,92热度) → 半导体材料供给收缩<br>
&nbsp;&nbsp;&nbsp;T15盈泡风险(RISK,85热度) → V53引擎正确过滤半导体HIGH风险<br><br>
<b>4. 圣杯目标:</b> T日(7/13)尾盘选股买入 → T+1日(7/14)上涨或涨停 → 启动连续上涨趋势 → T+1/T+2复利滚动退出
</div>

<div class="footer">
    V13.5.52+53 毕方灵犀·天眼 | 60只隐形冠军 × D6知识库15大主题 × 6只持仓融合策略<br>
    数据源: TDX MCP 14工具(唯一数据源) | 8维TDX蒸馏统一评分 | V52自主进化权重 | V53盈利泡沫风险评估<br>
    生成时间: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """ | Claw System
</div>

</body>
</html>"""

    return html


# ============ 主函数 ============
def main():
    print("=" * 70)
    print("V13.5.52+53 60只隐形冠军 × D6知识库交叉映射 + 7/13最终融合策略")
    print("=" * 70)

    # 构建融合策略
    strategy, crossmap = build_fusion_strategy()

    # 保存策略JSON
    strategy_path = os.path.join(DATA, "champion_final_strategy_0713.json")
    with open(strategy_path, "w", encoding="utf-8") as f:
        json.dump({"strategy": strategy, "d6_crossmap": crossmap}, f, ensure_ascii=False, indent=2)
    print(f"\n[1] 策略JSON已保存: {strategy_path}")

    # 保存D6交叉映射JSON
    crossmap_path = os.path.join(DATA, "knowledge_base", "d6_champion_crossmap_20260711.json")
    os.makedirs(os.path.dirname(crossmap_path), exist_ok=True)
    with open(crossmap_path, "w", encoding="utf-8") as f:
        json.dump(crossmap, f, ensure_ascii=False, indent=2)
    print(f"[2] D6交叉映射已保存: {crossmap_path}")

    # 生成HTML报告
    html = generate_html(strategy, crossmap)
    html_path = os.path.join(OUT, "V13_5_52_Champion60_FinalStrategy_0713.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[3] HTML报告已生成: {html_path}")

    # 打印摘要
    print("\n" + "=" * 70)
    print("策略摘要:")
    print(f"  持仓: {len(HOLDINGS)}只 | 总市值: ¥{strategy['position_allocation']['total_holdings_value']:,.0f}")
    print(f"  BUY候选: {len(strategy['new_buy_priority'])}只")
    print(f"  D6知识库: {len(crossmap)}大主题")
    print(f"\n  BUY优先级排序:")
    for p in strategy["new_buy_priority"]:
        print(f"    #{p['priority_rank']} {p['name']}({p['code']}) "
              f"融合分={p['fusion_score']:.1f} 风险={p['bubble_risk']} "
              f"D6=+{p['d6_synergy']} 赛道={p['sector']}")

    print(f"\n  D6核心驱动(按热度):")
    sorted_themes = sorted(crossmap.items(), key=lambda x: -x[1]["hotness"])
    for tid, t in sorted_themes[:5]:
        champ = len(t["champion_overlap"])
        port = len(t["portfolio_overlap"])
        print(f"    {tid}: {t['name']} [{t['level']}] 热度={t['hotness']} "
              f"冠军重叠={champ} 持仓重叠={port}")

    print("\n" + "=" * 70)
    print("完成!")


if __name__ == "__main__":
    main()
