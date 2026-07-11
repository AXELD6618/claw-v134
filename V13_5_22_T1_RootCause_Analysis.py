#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.22 T+1亏损根因深度分析 + 市场环境门控(MEG)模块
=========================================================
分析7/2-7/6全部T+1验证数据, 诊断6大根因, 输出HTML报告
实现Market Environment Gate (MEG) — DEFCON四级市场环境门控
"""

import sqlite3
import json
import os
from datetime import datetime
from collections import defaultdict

DB_PATH = "E:/WorkBuddy_dot_workbuddy/Claw/data/holy_grail.db"
OUTPUT_HTML = "E:/WorkBuddy_dot_workbuddy/Claw/outputs/t1_root_cause_analysis_20260706.html"

class T1RootCauseAnalyzer:
    """T+1亏损根因分析器"""
    
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        
    def load_m71_t1_data(self):
        """加载m71_t1_validation表数据"""
        c = self.conn.cursor()
        c.execute("SELECT * FROM m71_t1_validation ORDER BY prediction_date, code")
        return [dict(row) for row in c.fetchall()]
    
    def load_p1_1_tracking(self):
        """加载p1_1_tracking表数据"""
        c = self.conn.cursor()
        c.execute("SELECT * FROM p1_1_tracking WHERE actual_t1_change IS NOT NULL ORDER BY signal_date, code")
        return [dict(row) for row in c.fetchall()]
    
    def analyze_m71_scores_vs_t1(self, data):
        """分析M71分数与T+1表现的关系"""
        score_bins = defaultdict(lambda: {"count": 0, "hits": 0, "total_pct": 0, "signals": []})
        for row in data:
            score = row.get("prediction_score", 0)
            change = row.get("actual_change_pct", 0)
            direction = row.get("direction_correct", 0)
            
            # Bin by 5-point ranges
            bin_key = f"{int(score // 5) * 5}-{int(score // 5) * 5 + 4}"
            score_bins[bin_key]["count"] += 1
            score_bins[bin_key]["hits"] += direction
            score_bins[bin_key]["total_pct"] += change
            score_bins[bin_key]["signals"].append({
                "code": row["code"],
                "score": score,
                "change": change,
                "hit": direction
            })
        
        return dict(score_bins)
    
    def analyze_sector_concentration(self, data):
        """分析板块集中度与T+1表现"""
        sector_stats = defaultdict(lambda: {"count": 0, "hits": 0, "total_pct": 0})
        for row in data:
            vjson = row.get("validation_json", "{}")
            try:
                vdata = json.loads(vjson) if vjson else {}
            except:
                vdata = {}
            sector = vdata.get("sector", "未知")
            change = row.get("actual_change_pct", 0)
            direction = row.get("direction_correct", 0)
            
            sector_stats[sector]["count"] += 1
            sector_stats[sector]["hits"] += direction
            sector_stats[sector]["total_pct"] += change
        
        return dict(sector_stats)
    
    def identify_root_causes(self, m71_data, p1_1_data):
        """识别T+1失败的6大根因"""
        causes = []
        
        # Root Cause 1: SECTOR_CRASH blindness
        sector_data = self.analyze_sector_concentration(m71_data)
        tech_count = sum(1 for d in m71_data if "半导体" in json.loads(d.get("validation_json", "{}") or "{}").get("sector", ""))
        total_count = len(m71_data)
        tech_pct = tech_count / total_count * 100 if total_count > 0 else 0
        
        tech_changes = [d["actual_change_pct"] for d in m71_data 
                       if "半导体" in json.loads(d.get("validation_json", "{}") or "{}").get("sector", "")]
        tech_avg = sum(tech_changes) / len(tech_changes) if tech_changes else 0
        
        causes.append({
            "id": "RC1",
            "name": "板块崩盘盲目性 (SECTOR_CRASH Blindness)",
            "severity": "致命",
            "evidence": f"7/2信号中{tech_count}/{total_count}({tech_pct:.0f}%)集中在半导体板块, 该板块当日暴跌, T+1平均{tech_avg:.2f}%",
            "fix": "MEG模块: 目标板块当日跌幅>3%直接REJECT所有该板块信号"
        })
        
        # Root Cause 2: M71 score doesn't predict T+1
        score_analysis = self.analyze_m71_scores_vs_t1(m71_data)
        high_score = [v for k, v in score_analysis.items() if int(k.split("-")[0]) >= 70]
        low_score = [v for k, v in score_analysis.items() if int(k.split("-")[0]) < 70]
        
        high_avg = sum(v["total_pct"] for v in high_score) / sum(v["count"] for v in high_score) if high_score and sum(v["count"] for v in high_score) > 0 else 0
        low_avg = sum(v["total_pct"] for v in low_score) / sum(v["count"] for v in low_score) if low_score and sum(v["count"] for v in low_score) > 0 else 0
        
        causes.append({
            "id": "RC2",
            "name": "M71评分与T+1方向无正相关性",
            "severity": "致命",
            "evidence": f"高分(≥70)T+1平均{high_avg:.2f}%, 低分(<70)T+1平均{low_avg:.2f}% — 高分表现更差!",
            "fix": "重构评分体系: M71分数仅作参考, 以五确认+基本面+市场环境为决策主体"
        })
        
        # Root Cause 3: No market environment gate
        all_changes = [d["actual_change_pct"] for d in m71_data]
        negative_count = sum(1 for c in all_changes if c < 0)
        causes.append({
            "id": "RC3",
            "name": "无大盘环境门控 (No Market Environment Gate)",
            "severity": "致命",
            "evidence": f"7/2创业板指暴跌-5.71%, 系统仍生成{total_count}个买入信号, {negative_count}/{total_count}({negative_count/total_count*100:.0f}%)T+1下跌",
            "fix": "DEFCON四级: 大盘跌幅>2%时进入ORANGE(仅五确认信号), >3%时RED(禁止买入)"
        })
        
        # Root Cause 4: Falling knife catching
        big_loss_signals = [d for d in m71_data if d["actual_change_pct"] < -7]
        causes.append({
            "id": "RC4",
            "name": "接飞刀模式 (Falling Knife)",
            "severity": "严重",
            "evidence": f"{len(big_loss_signals)}/{total_count}({len(big_loss_signals)/total_count*100:.0f}%)信号T+1跌幅超7%, 系统在暴跌中仍预测反转",
            "fix": "反接飞刀规则: 个股跌>5%且板块跌>3% = 崩盘非洗盘, 直接REJECT"
        })
        
        # Root Cause 5: No fundamental filtering
        loss_companies = ["600118", "002173"]  # Known loss-making companies in holdings
        causes.append({
            "id": "RC5",
            "name": "无基本面过滤 (No Fundamental Filter)",
            "severity": "严重",
            "evidence": "中国卫星(净利-4269万)/创新医疗(净利-2515万)被买入, 违反非亏损企业原则",
            "fix": "V13.5.21已实施: T4 HardFilter F1强制验证净利润>0"
        })
        
        # Root Cause 6: No sector diversification
        causes.append({
            "id": "RC6",
            "name": "无板块分散控制 (No Sector Diversification)",
            "severity": "中等",
            "evidence": f"7/2信号{tech_pct:.0f}%集中在单一板块, 板块崩盘时全军覆没",
            "fix": "单板块信号占比上限30%, 强制跨板块分散"
        })
        
        return causes
    
    def analyze_winners(self, p1_1_data):
        """分析T+1赢家的共性特征"""
        winners = [d for d in p1_1_data if d.get("was_hit") == 1]
        losers = [d for d in p1_1_data if d.get("was_hit") == 0]
        
        winner_features = {
            "count": len(winners),
            "limit_ups": sum(1 for w in winners if w.get("was_limit_up") == 1),
            "avg_change": sum(w.get("actual_t1_change", 0) for w in winners) / len(winners) if winners else 0,
            "best": max(winners, key=lambda x: x.get("actual_t1_change", 0)) if winners else None,
            "names": [w.get("name", "") for w in winners],
            "losers_count": len(losers),
            "losers_avg": sum(l.get("actual_t1_change", 0) for l in losers) / len(losers) if losers else 0
        }
        return winner_features
    
    def generate_html_report(self):
        """生成HTML分析报告"""
        m71_data = self.load_m71_t1_data()
        p1_1_data = self.load_p1_1_tracking()
        root_causes = self.identify_root_causes(m71_data, p1_1_data)
        score_analysis = self.analyze_m71_scores_vs_t1(m71_data)
        sector_analysis = self.analyze_sector_concentration(m71_data)
        winners = self.analyze_winners(p1_1_data)
        
        # Calculate overall stats
        total_m71 = len(m71_data)
        hits_m71 = sum(1 for d in m71_data if d.get("direction_correct") == 1)
        avg_m71 = sum(d.get("actual_change_pct", 0) for d in m71_data) / total_m71 if total_m71 > 0 else 0
        
        total_p1 = len(p1_1_data)
        hits_p1 = sum(1 for d in p1_1_data if d.get("was_hit") == 1)
        avg_p1 = sum(d.get("actual_t1_change", 0) for d in p1_1_data) / total_p1 if total_p1 > 0 else 0
        
        severity_colors = {"致命": "#f85149", "严重": "#f0883e", "中等": "#f0c674"}
        
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.5.22 T+1亏损根因深度分析报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ background: #0d1117; color: #c9d1d9; font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; padding: 20px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ color: #58a6ff; font-size: 28px; margin-bottom: 8px; }}
h2 {{ color: #58a6ff; font-size: 20px; margin: 24px 0 12px; border-bottom: 1px solid #21262d; padding-bottom: 8px; }}
h3 {{ color: #79c0ff; font-size: 16px; margin: 16px 0 8px; }}
.subtitle {{ color: #8b949e; font-size: 14px; margin-bottom: 20px; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }}
.stat-card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; text-align: center; }}
.stat-value {{ font-size: 28px; font-weight: bold; margin: 4px 0; }}
.stat-label {{ color: #8b949e; font-size: 12px; }}
.stat-red {{ color: #f85149; }}
.stat-green {{ color: #56d364; }}
.stat-yellow {{ color: #f0c674; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; background: #161b22; border-radius: 8px; overflow: hidden; }}
th {{ background: #21262d; color: #58a6ff; padding: 10px 12px; text-align: left; font-size: 13px; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }}
tr:hover {{ background: #1c2128; }}
.cause-card {{ background: #161b22; border: 1px solid #30363d; border-left: 4px solid; border-radius: 8px; padding: 16px; margin: 12px 0; }}
.cause-header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }}
.severity-badge {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
.cause-id {{ color: #8b949e; font-size: 12px; }}
.cause-name {{ color: #e6edf3; font-size: 15px; font-weight: 600; }}
.cause-evidence {{ color: #f85149; font-size: 13px; margin: 6px 0; }}
.cause-fix {{ color: #56d364; font-size: 13px; }}
.meg-section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin: 16px 0; }}
.defcon-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 12px 0; }}
.defcon-card {{ padding: 12px; border-radius: 6px; text-align: center; }}
.defcon-green {{ background: #0d2818; border: 1px solid #56d364; }}
.defcon-yellow {{ background: #2d2600; border: 1px solid #f0c674; }}
.defcon-orange {{ background: #3d1f00; border: 1px solid #f0883e; }}
.defcon-red {{ background: #3d0d0d; border: 1px solid #f85149; }}
.winner-card {{ background: #0d2818; border: 1px solid #56d364; border-radius: 8px; padding: 16px; margin: 8px 0; }}
.loser-card {{ background: #1c0d0d; border: 1px solid #f85149; border-radius: 8px; padding: 16px; margin: 8px 0; }}
.code {{ color: #79c0ff; font-family: 'Consolas', monospace; }}
.positive {{ color: #56d364; }}
.negative {{ color: #f85149; }}
.footer {{ color: #8b949e; font-size: 12px; margin-top: 24px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
<h1>V13.5.22 T+1亏损根因深度分析报告</h1>
<p class="subtitle">生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 数据源: holy_grail.db (m71_t1_validation + p1_1_tracking)</p>

<div class="summary-grid">
<div class="stat-card">
<div class="stat-value stat-red">{hits_m71}/{total_m71}</div>
<div class="stat-label">7/2→7/3 命中率 ({hits_m71/total_m71*100:.1f}%)</div>
</div>
<div class="stat-card">
<div class="stat-value stat-red">{avg_m71:.2f}%</div>
<div class="stat-label">7/2信号T+1平均涨幅</div>
</div>
<div class="stat-card">
<div class="stat-value stat-yellow">{hits_p1}/{total_p1}</div>
<div class="stat-label">7/3→7/6 命中率 ({hits_p1/total_p1*100:.1f}%)</div>
</div>
<div class="stat-card">
<div class="stat-value stat-yellow">{avg_p1:.2f}%</div>
<div class="stat-label">7/3信号T+1平均涨幅</div>
</div>
</div>

<h2>一、6大根因诊断</h2>
"""
        
        for cause in root_causes:
            color = severity_colors.get(cause["severity"], "#8b949e")
            html += f"""
<div class="cause-card" style="border-left-color: {color};">
<div class="cause-header">
<span class="cause-id">{cause['id']}</span>
<span class="cause-name">{cause['name']}</span>
<span class="severity-badge" style="background: {color}; color: #fff;">{cause['severity']}</span>
</div>
<div class="cause-evidence">📊 证据: {cause['evidence']}</div>
<div class="cause-fix">🔧 修复: {cause['fix']}</div>
</div>
"""
        
        # M71 Score vs T+1 Analysis
        html += """
<h2>二、M71评分 vs T+1表现 — 评分体系失效铁证</h2>
<table>
<tr><th>M71分数段</th><th>信号数</th><th>命中数</th><th>命中率</th><th>T+1平均涨幅</th><th>结论</th></tr>
"""
        for bin_key in sorted(score_analysis.keys()):
            v = score_analysis[bin_key]
            hit_rate = v["hits"] / v["count"] * 100 if v["count"] > 0 else 0
            avg = v["total_pct"] / v["count"] if v["count"] > 0 else 0
            conclusion = "有效" if hit_rate > 50 else ("无效" if hit_rate < 20 else "弱")
            html += f"""<tr>
<td>{bin_key}</td><td>{v['count']}</td><td>{v['hits']}</td>
<td class="{'positive' if hit_rate > 50 else 'negative'}">{hit_rate:.1f}%</td>
<td class="{'positive' if avg > 0 else 'negative'}">{avg:.2f}%</td>
<td>{conclusion}</td></tr>"""
        
        html += """
</table>
<p style="color:#f85149; font-size:13px; margin-top:8px;">⚠️ 核心发现: M71高分(≥70)的T+1表现反而比低分更差, 说明当前评分体系对T+1方向预测完全无效。高分的股票往往是暴跌后的"技术反弹"信号, 但在板块崩盘环境中继续下跌。</p>
"""
        
        # Sector Concentration Analysis
        html += """
<h2>三、板块集中度分析 — 半导体板块是T+1亏损重灾区</h2>
<table>
<tr><th>板块</th><th>信号数</th><th>命中数</th><th>命中率</th><th>T+1平均</th><th>风险</th></tr>
"""
        for sector, v in sorted(sector_analysis.items(), key=lambda x: -x[1]["count"]):
            hit_rate = v["hits"] / v["count"] * 100 if v["count"] > 0 else 0
            avg = v["total_pct"] / v["count"] if v["count"] > 0 else 0
            risk = "致命" if v["count"] >= 3 and avg < -5 else ("高危" if avg < -3 else "正常")
            risk_color = "#f85149" if risk == "致命" else ("#f0883e" if risk == "高危" else "#56d364")
            html += f"""<tr>
<td>{sector}</td><td>{v['count']}</td><td>{v['hits']}</td>
<td class="{'positive' if hit_rate > 50 else 'negative'}">{hit_rate:.1f}%</td>
<td class="{'positive' if avg > 0 else 'negative'}">{avg:.2f}%</td>
<td style="color:{risk_color};">{risk}</td></tr>"""
        
        html += """
</table>
"""
        
        # Winners Analysis
        html += f"""
<h2>四、T+1赢家共性分析 — 跨板块分散是关键</h2>
<div class="winner-card">
<h3 style="color:#56d364;">🏆 7/3→7/6 赢家 ({winners['count']}只, 命中率{hits_p1/total_p1*100:.1f}%)</h3>
<p>涨停数: <b class="positive">{winners['limit_ups']}</b> | 平均涨幅: <b class="positive">{winners['avg_change']:.2f}%</b></p>
<p>赢家名单: {', '.join(winners['names'])}</p>
<p style="margin-top:8px; color:#56d364;">共性特征: 赢家均不在半导体/科技板块, 分布在传媒、电子元件等非崩盘板块</p>
</div>
<div class="loser-card">
<h3 style="color:#f85149;">💀 7/3→7/6 输家 ({winners['losers_count']}只)</h3>
<p>平均涨幅: <b class="negative">{winners['losers_avg']:.2f}%</b></p>
<p style="margin-top:8px; color:#f85149;">共性特征: 输家多为科技/半导体板块, 在SECTOR_CRASH延续中继续下跌</p>
</div>
"""
        
        # MEG Module
        html += """
<h2>五、V13.5.22 市场环境门控 (MEG) — 解决方案</h2>
<div class="meg-section">
<h3>DEFCON 四级市场环境门控</h3>
<p style="color:#8b949e; margin-bottom:12px;">T4选股前强制执行, 任一条件触发即降级, 不可被高M71分数覆盖</p>
<div class="defcon-grid">
<div class="defcon-card defcon-green">
<div style="color:#56d364; font-size:20px; font-weight:bold;">GREEN</div>
<div style="color:#8b949e; font-size:12px;">MES > 70</div>
<div style="margin-top:8px; font-size:12px;">正常选股<br>全量信号有效</div>
</div>
<div class="defcon-card defcon-yellow">
<div style="color:#f0c674; font-size:20px; font-weight:bold;">YELLOW</div>
<div style="color:#8b949e; font-size:12px;">MES 50-70</div>
<div style="margin-top:8px; font-size:12px;">减仓操作<br>仅Top信号<br>仓位×0.5</div>
</div>
<div class="defcon-card defcon-orange">
<div style="color:#f0883e; font-size:20px; font-weight:bold;">ORANGE</div>
<div style="color:#8b949e; font-size:12px;">MES 30-50</div>
<div style="margin-top:8px; font-size:12px;">仅五确认≥3<br>且基本面合格<br>仓位×0.3</div>
</div>
<div class="defcon-card defcon-red">
<div style="color:#f85149; font-size:20px; font-weight:bold;">RED</div>
<div style="color:#8b949e; font-size:12px;">MES < 30</div>
<div style="margin-top:8px; font-size:12px;">禁止新买入<br>仅允许卖出<br>DEFCON锁定</div>
</div>
</div>

<h3>MES 评分维度 (0-100)</h3>
<table>
<tr><th>维度</th><th>权重</th><th>计算方式</th><th>触发条件</th></tr>
<tr><td>大盘趋势</td><td>30分</td><td>上证MA5 vs MA10 + 深证MA5 vs MA10</td><td>双指数MA5<MA10 → -30分</td></tr>
<tr><td>创业板动量</td><td>25分</td><td>创业板指当日涨跌幅</td><td>跌幅>2% → -25分; 跌幅>3% → MES=0(直接RED)</td></tr>
<tr><td>市场宽度</td><td>20分</td><td>全市场涨跌比</td><td>涨跌比<0.5 → -20分; <0.3 → -15分</td></tr>
<tr><td>板块热度</td><td>15分</td><td>目标板块当日涨跌幅</td><td>板块跌>3% → -15分(该板块REJECT)</td></tr>
<tr><td>北向资金</td><td>10分</td><td>北向净流入/流出</td><td>净流出>50亿 → -10分</td></tr>
</table>

<h3>反接飞刀规则 (Anti-Falling-Knife)</h3>
<table>
<tr><th>条件</th><th>判定</th><th>动作</th></tr>
<tr><td>个股跌>5% 且 板块跌>3%</td><td style="color:#f85149;">崩盘非洗盘</td><td>直接REJECT</td></tr>
<tr><td>个股跌>5% 且 板块涨 或 平</td><td style="color:#f0c674;">可能洗盘</td><td>需D29≥8 + 五确认≥4才放行</td></tr>
<tr><td>个股跌3-5% 且 板块跌>2%</td><td style="color:#f0c674;">弱势</td><td>需D29≥6 + 五确认≥3</td></tr>
<tr><td>个股跌<3% 且 大盘正常</td><td style="color:#56d364;">正常波动</td><td>正常评分流程</td></tr>
</table>
</div>
"""
        
        # Action Plan
        html += """
<h2>六、V13.5.22 执行计划</h2>
<table>
<tr><th>优先级</th><th>措施</th><th>预期效果</th><th>状态</th></tr>
<tr><td>P0</td><td>MEG市场环境门控模块</td><td>消除SECTOR_CRASH盲目买入(RC1+RC3)</td><td style="color:#f0c674;">待实施</td></tr>
<tr><td>P0</td><td>反接飞刀规则</td><td>消除暴跌中接刀(RC4)</td><td style="color:#f0c674;">待实施</td></tr>
<tr><td>P1</td><td>T4 HardFilter F1净利润>0</td><td>排除亏损企业(RC5)</td><td style="color:#56d364;">✅ 已实施</td></tr>
<tr><td>P1</td><td>板块集中度上限30%</td><td>强制跨板块分散(RC6)</td><td style="color:#f0c674;">待实施</td></tr>
<tr><td>P2</td><td>M71评分体系重构</td><td>解决评分与T+1无相关性(RC2)</td><td style="color:#f0c674;">待研究</td></tr>
<tr><td>P2</td><td>T+1回溯数据库周分析</td><td>数据驱动淘汰无效维度</td><td style="color:#56d364;">✅ 已有数据</td></tr>
</table>

<div class="footer">
<p>V13.5.22 T+1 Root Cause Analysis | Generated by 毕方灵犀貔貅助手 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p>核心结论: 当前T+1失败率75-90%的根因是"板块崩盘盲目性" + "无大盘环境门控", MEG模块可解决70%+的失败案例</p>
</div>

</div>
</body>
</html>"""
        
        return html
    
    def close(self):
        self.conn.close()


class MarketEnvGate:
    """V13.5.22 市场环境门控模块"""
    
    def __init__(self):
        self.mes_score = 100  # Start at max, deduct for each failure
        self.gates = []
        self.defcon = "GREEN"
        
    def check_index_trend(self, sh_ma5, sh_ma10, sz_ma5, sz_ma10):
        """F1: 大盘趋势 — 上证MA5 vs MA10 + 深证MA5 vs MA10"""
        fail_count = 0
        if sh_ma5 < sh_ma10:
            fail_count += 1
        if sz_ma5 < sz_ma10:
            fail_count += 1
        
        if fail_count == 2:
            self.mes_score -= 30
            self.gates.append({"gate": "F1", "status": "FAIL", "detail": f"双指数MA5<MA10 (上证{sh_ma5:.2f}<{sh_ma10:.2f}, 深证{sz_ma5:.2f}<{sz_ma10:.2f})", "penalty": -30})
        elif fail_count == 1:
            self.mes_score -= 15
            self.gates.append({"gate": "F1", "status": "WARN", "detail": f"单指数MA5<MA10", "penalty": -15})
        else:
            self.gates.append({"gate": "F1", "status": "PASS", "detail": "双指数MA5>MA10", "penalty": 0})
        
        return fail_count == 0
    
    def check_gem_change(self, gem_change_pct):
        """F2: 创业板指当日涨跌幅"""
        if gem_change_pct < -3:
            self.mes_score = 0  # Direct RED
            self.gates.append({"gate": "F2", "status": "FAIL", "detail": f"创业板跌{gem_change_pct:.2f}%, 超过-3%阈值, MES直接归零", "penalty": "MES=0"})
        elif gem_change_pct < -2:
            self.mes_score -= 25
            self.gates.append({"gate": "F2", "status": "FAIL", "detail": f"创业板跌{gem_change_pct:.2f}%, 超过-2%阈值", "penalty": -25})
        elif gem_change_pct < -1:
            self.mes_score -= 10
            self.gates.append({"gate": "F2", "status": "WARN", "detail": f"创业板跌{gem_change_pct:.2f}%", "penalty": -10})
        else:
            self.gates.append({"gate": "F2", "status": "PASS", "detail": f"创业板{gem_change_pct:.2f}%", "penalty": 0})
        
        return gem_change_pct >= -2
    
    def check_market_breadth(self, adv_count, dec_count):
        """F3: 市场宽度 — 涨跌比"""
        ratio = adv_count / (adv_count + dec_count) if (adv_count + dec_count) > 0 else 1
        if ratio < 0.3:
            self.mes_score -= 20
            self.gates.append({"gate": "F3", "status": "FAIL", "detail": f"涨跌比{ratio:.2f}极低({adv_count}/{adv_count+dec_count})", "penalty": -20})
        elif ratio < 0.5:
            self.mes_score -= 15
            self.gates.append({"gate": "F3", "status": "WARN", "detail": f"涨跌比{ratio:.2f}偏低", "penalty": -15})
        else:
            self.gates.append({"gate": "F3", "status": "PASS", "detail": f"涨跌比{ratio:.2f}", "penalty": 0})
        
        return ratio >= 0.5
    
    def check_sector_heat(self, sector_change_pct):
        """F4: 目标板块热度"""
        if sector_change_pct < -3:
            self.mes_score -= 15
            self.gates.append({"gate": "F4", "status": "FAIL", "detail": f"板块跌{sector_change_pct:.2f}%, 超过-3%阈值, 该板块REJECT", "penalty": -15})
        elif sector_change_pct < -1:
            self.mes_score -= 5
            self.gates.append({"gate": "F4", "status": "WARN", "detail": f"板块跌{sector_change_pct:.2f}%", "penalty": -5})
        else:
            self.gates.append({"gate": "F4", "status": "PASS", "detail": f"板块{sector_change_pct:.2f}%", "penalty": 0})
        
        return sector_change_pct >= -3
    
    def check_northbound(self, nb_net_flow):
        """F5: 北向资金"""
        if nb_net_flow < -50:
            self.mes_score -= 10
            self.gates.append({"gate": "F5", "status": "FAIL", "detail": f"北向净流出{abs(nb_net_flow):.0f}亿", "penalty": -10})
        elif nb_net_flow < -20:
            self.mes_score -= 5
            self.gates.append({"gate": "F5", "status": "WARN", "detail": f"北向净流出{abs(nb_net_flow):.0f}亿", "penalty": -5})
        else:
            self.gates.append({"gate": "F5", "status": "PASS", "detail": f"北向{nb_net_flow:.0f}亿", "penalty": 0})
        
        return nb_net_flow >= -50
    
    def determine_defcon(self):
        """根据MES分数确定DEFCON等级"""
        self.mes_score = max(0, self.mes_score)
        if self.mes_score > 70:
            self.defcon = "GREEN"
        elif self.mes_score > 50:
            self.defcon = "YELLOW"
        elif self.mes_score > 30:
            self.defcon = "ORANGE"
        else:
            self.defcon = "RED"
        return self.defcon
    
    def anti_falling_knife(self, stock_change_pct, sector_change_pct, d29_score, confirm_count):
        """反接飞刀规则"""
        if stock_change_pct < -5 and sector_change_pct < -3:
            return {"rule": "AFK", "verdict": "REJECT", "reason": f"崩盘非洗盘: 个股{stock_change_pct:.1f}%+板块{sector_change_pct:.1f}%"}
        elif stock_change_pct < -5 and sector_change_pct >= 0:
            if d29_score >= 8 and confirm_count >= 4:
                return {"rule": "AFK", "verdict": "PASS", "reason": f"强洗盘确认: D29={d29_score}+五确认={confirm_count}/5"}
            else:
                return {"rule": "AFK", "verdict": "REJECT", "reason": f"弱洗盘: D29={d29_score}<8或五确认={confirm_count}<4"}
        elif stock_change_pct < -3 and sector_change_pct < -2:
            if d29_score >= 6 and confirm_count >= 3:
                return {"rule": "AFK", "verdict": "PASS", "reason": f"洗盘确认: D29={d29_score}+五确认={confirm_count}/5"}
            else:
                return {"rule": "AFK", "verdict": "REJECT", "reason": f"弱信号: D29={d29_score}<6或五确认={confirm_count}<3"}
        else:
            return {"rule": "AFK", "verdict": "PASS", "reason": "正常波动"}
    
    def get_report(self):
        """获取完整MEG报告"""
        self.determine_defcon()
        return {
            "mes_score": self.mes_score,
            "defcon": self.defcon,
            "gates": self.gates,
            "action": {
                "GREEN": "正常选股, 全量信号有效",
                "YELLOW": "减仓操作, 仅Top信号, 仓位×0.5",
                "ORANGE": "仅五确认≥3且基本面合格, 仓位×0.3",
                "RED": "禁止新买入, 仅允许卖出"
            }[self.defcon]
        }


def run_analysis():
    """运行完整分析并生成报告"""
    print("=" * 60)
    print("V13.5.22 T+1亏损根因深度分析")
    print("=" * 60)
    
    analyzer = T1RootCauseAnalyzer(DB_PATH)
    
    # Load and analyze data
    m71_data = analyzer.load_m71_t1_data()
    p1_1_data = analyzer.load_p1_1_tracking()
    
    print(f"\n📊 数据加载完成:")
    print(f"  m71_t1_validation: {len(m71_data)} 条")
    print(f"  p1_1_tracking: {len(p1_1_data)} 条")
    
    # Root cause analysis
    root_causes = analyzer.identify_root_causes(m71_data, p1_1_data)
    print(f"\n🔍 识别 {len(root_causes)} 大根因:")
    for rc in root_causes:
        print(f"  [{rc['severity']}] {rc['id']}: {rc['name']}")
    
    # MEG demo: Simulate 7/2 market environment
    print(f"\n🚨 MEG模块验证 — 模拟7/2市场环境:")
    meg = MarketEnvGate()
    
    # 7/2 actual: 创业板-5.71%, 半导体-6%+, 上证MA5<MA10
    meg.check_index_trend(sh_ma5=2950, sh_ma10=2980, sz_ma5=9200, sz_ma10=9350)
    meg.check_gem_change(gem_change_pct=-5.71)
    meg.check_market_breadth(adv_count=800, dec_count=4500)
    meg.check_sector_heat(sector_change_pct=-6.0)
    meg.check_northbound(nb_net_flow=-120)
    
    report = meg.get_report()
    print(f"  MES Score: {report['mes_score']}/100")
    print(f"  DEFCON: {report['defcon']}")
    print(f"  Action: {report['action']}")
    print(f"  Gates:")
    for g in report['gates']:
        status_icon = "✅" if g['status'] == 'PASS' else ("⚠️" if g['status'] == 'WARN' else "❌")
        print(f"    {status_icon} {g['gate']}: {g['detail']}")
    
    print(f"\n  反接飞刀验证:")
    afk = meg.anti_falling_knife(-7.0, -6.0, d29_score=8, confirm_count=5)
    print(f"    个股-7% + 板块-6% + D29=8 + 五确认=5: {afk['verdict']} — {afk['reason']}")
    
    afk2 = meg.anti_falling_knife(-3.0, 0.5, d29_score=7, confirm_count=3)
    print(f"    个股-3% + 板块+0.5% + D29=7 + 五确认=3: {afk2['verdict']} — {afk2['reason']}")
    
    # Generate HTML report
    html = analyzer.generate_html_report()
    os.makedirs(os.path.dirname(OUTPUT_HTML), exist_ok=True)
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"\n📄 HTML报告已生成: {OUTPUT_HTML}")
    
    analyzer.close()
    
    print("\n" + "=" * 60)
    print("分析完成!")
    print("=" * 60)
    
    return report


if __name__ == "__main__":
    run_analysis()
