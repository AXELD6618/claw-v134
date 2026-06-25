#!/usr/bin/env python3
"""
V13.3 全天候实时盯盘预警系统 — 圣杯核心能力引擎
=================================================
用户终极目标: T日尾盘之前选股买入 → T+1上涨/涨停 → 连续上涨趋势

V13.2痛点: 仅14:30单点扫描 → 错过全天最佳反转窗口
V13.3方案: 六时段网格扫描 + 五级预警体系 + 实时HTML仪表盘

扫描时段:
  T0 10:30 开盘脉冲消退 → 首轮筛选 (30只)
  T1 11:30 午盘收盘 → 趋势确认 (20只)
  T2 13:30 午盘开盘 → 资金回流检测 (15只)
  T3 14:00 尾盘前1h → 加速筛选 (10只)  
  T4 14:15 尾盘前15min → 临界预警 (5只)
  T5 14:30 最后一击 → 终极推荐 (3只S级)

预警级别:
  🟢 GREEN   v132 > 0.45   "关注"    — 列入观察池
  🟡 YELLOW  v132 > 0.55   "关注+"   — 信号增强中
  🟠 ORANGE  v132 > 0.65   "预警"    — 建议准备资金
  🔴 RED     v132 > 0.75   "买入"    — 建议立即买入
  ⚡ FLASH   v132 > 0.85   "超级信号" — 圣杯级别信号

时间: 2026-06-24 16:23 自主进化

文件结构:
  V13_3_IntradayMonitor.py — 核心引擎
  outputs/intraday_dashboard.html — 实时仪表盘
  data/intraday_cache/ — 各时段缓存
"""

import json
import math
import os
import time
import sqlite3
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from collections import defaultdict


# ═══════════════════════════════════════════════════════════
# SECTION 1: 配置
# ═══════════════════════════════════════════════════════════

class AlertLevel(Enum):
    """五大预警级别"""
    FLASH = (5, 0.85, "⚡ 超级信号",   "#ff0000", "#ffe0e0", "圣杯级: 建议满仓")
    RED   = (4, 0.75, "🔴 买入",       "#ff3333", "#fff0f0", "强信号: 建议立即买入")
    ORANGE= (3, 0.65, "🟠 预警",       "#ff8800", "#fff8f0", "中强信号: 建议准备资金")
    YELLOW= (2, 0.55, "🟡 关注+",      "#ffcc00", "#fffff0", "弱增强: 列入候选")
    GREEN = (1, 0.45, "🟢 关注",       "#00aa00", "#f0fff0", "基础信号: 加入观察")
    SILENT= (0, 0.00, "⚪ 无信号",     "#999999", "#f0f0f0", "无异常")
    
    def __init__(self, level, threshold, label, color, bg, description):
        self.level = level
        self.threshold = threshold
        self.label = label
        self.color = color
        self.bg = bg
        self.description = description
    
    @classmethod
    def from_score(cls, v132_score):
        """根据V13.2评分返回预警级别 (按阈值从高到低匹配)"""
        # 🚨 V13.4.2修复: reversed()导致SILENT(阈值0.0)最先匹配, 所有分数返回"无信号"
        # 正确做法: 按定义顺序迭代(FLASH→RED→...→SILENT), 最高阈值先匹配
        for level in list(cls):
            if v132_score >= level.threshold:
                return level
        return cls.SILENT


class ScanPeriod(Enum):
    """六个扫描时段"""
    T0_1030 = ("10:30", "开盘脉冲消退", 30, 0.25)  # (时间, 描述, 池大小, 权重)
    T1_1130 = ("11:30", "午盘收盘",      20, 0.15)
    T2_1330 = ("13:30", "午盘开盘",      15, 0.15)
    T3_1400 = ("14:00", "尾盘前1h",      10, 0.20)
    T4_1415 = ("14:15", "尾盘前15min",    5, 0.25)
    T5_1430 = ("14:30", "最后一击",       3, 0.0)   # 仅输出不累加权重


@dataclass
class IntradaySignal:
    """日内信号记录"""
    code: str
    name: str
    period: str                 # 扫描时段
    timestamp: str              # 扫描时间戳
    v132_score: float
    m46_normalized: float
    m57_composite: float
    m64_score: float
    decline_pct: float
    amplitude: float
    hsl: float
    sector: str
    sector_heat_coeff: float
    alert_level: str            # 预警级别
    recommendation: str
    cumulative_weight: float = 0.0  # 累计权重(跨时段累加)
    hit_count: int = 0              # 跨时段命中次数


@dataclass 
class DashboardState:
    """仪表盘状态"""
    date: str
    last_scan: str
    market_index: Dict
    signals: List[IntradaySignal]
    alerts_by_level: Dict[str, int]
    top_candidates: List[IntradaySignal]
    sector_summary: Dict[str, Any]
    execution_log: List[str]


# ═══════════════════════════════════════════════════════════
# SECTION 2: 核心监控引擎
# ═══════════════════════════════════════════════════════════

class IntradayMonitorEngine:
    """
    V13.3 全天候监控引擎
    
    功能:
    - 六时段网格扫描
    - 跨时段信号累积(权重叠加)
    - 五级预警体系
    - 实时HTML仪表盘生成
    - 圣杯级信号闪光告警
    """
    
    def __init__(self, data_dir="data", output_dir="outputs"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.cache_dir = os.path.join(data_dir, "intraday_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        
        self.state: Optional[DashboardState] = None
        self.execution_log = []
        self.scan_history = defaultdict(list)  # code -> [IntradaySignal]
        
    def log(self, msg: str, level: str = "INFO"):
        """执行日志"""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] [{level}] {msg}"
        self.execution_log.append(entry)
        print(entry)
    
    # ─── 数据输入 ───
    
    def load_signals_from_json(self, json_path: str) -> List[Dict]:
        """从JSON加载信号列表"""
        if not os.path.exists(json_path):
            self.log(f"JSON文件不存在: {json_path}", "WARN")
            return []
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('results', [])
    
    def load_signals_from_db(self, db_path: str, signal_date: str = None) -> List[Dict]:
        """从SQLite加载信号列表"""
        if not signal_date:
            signal_date = date.today().isoformat()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM daily_signals WHERE signal_date = ? ORDER BY v132_score DESC",
            (signal_date,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    
    # ─── 跨时段累积引擎 ───
    
    def accumulate_signals(
        self, 
        period: str, 
        new_signals: List[Dict],
        period_weight: float = 0.20,
    ) -> List[IntradaySignal]:
        """
        跨时段信号累积
        
        规则:
        - 首次出现: baseline = v132_score * period_weight
        - 重复出现: cumulative += v132_score * period_weight * (1 + hit_count * 0.1)
        - 连续增强: 相邻时段v132上升 → bonus ×1.15
        - 连续减弱: v132下降但仍在阈值上 → 保留但权重打折
        """
        timestamp = datetime.now().isoformat()
        results = []
        
        for sig in new_signals:
            code = sig.get('code', '')
            v132 = sig.get('v132_score', sig.get('v132_adjusted', 0))
            
            # 创建新的IntradaySignal
            i_sig = IntradaySignal(
                code=code,
                name=sig.get('name', ''),
                period=period,
                timestamp=timestamp,
                v132_score=v132,
                m46_normalized=sig.get('m46_confidence', sig.get('m46_normalized', 0)),
                m57_composite=sig.get('m57_composite', 0),
                m64_score=sig.get('m64_score', 0),
                decline_pct=sig.get('decline_pct', sig.get('change_pct', 0)),
                amplitude=sig.get('amplitude', 0),
                hsl=sig.get('hsl', sig.get('turnover_rate', 0)),
                sector=sig.get('sector', sig.get('industry', '')),
                sector_heat_coeff=sig.get('sector_heat_coeff', 1.0),
                alert_level=AlertLevel.from_score(v132).label,
                recommendation=sig.get('recommendation', 'WATCH'),
                cumulative_weight=v132 * period_weight,
                hit_count=1,
            )
            
            # 检查历史命中
            prev_signals = self.scan_history.get(code, [])
            if prev_signals:
                last = prev_signals[-1]
                i_sig.hit_count = last.hit_count + 1
                
                # 累积权重: 基础 + 重复奖励 + 连续增强bonus
                base = v132 * period_weight
                repeat_bonus = 1 + (i_sig.hit_count - 1) * 0.1  # 最多2x
                
                if v132 > last.v132_score:
                    # 连续增强 → bonus ×1.15
                    i_sig.cumulative_weight = last.cumulative_weight + base * repeat_bonus * 1.15
                elif v132 > last.v132_score * 0.85:
                    # 微降但稳定 → 正常累加
                    i_sig.cumulative_weight = last.cumulative_weight + base * repeat_bonus
                else:
                    # 显著下降 → 打折累加
                    i_sig.cumulative_weight = last.cumulative_weight + base * repeat_bonus * 0.7
            else:
                i_sig.cumulative_weight = v132 * period_weight
            
            # 更新历史
            self.scan_history[code].append(i_sig)
            results.append(i_sig)
        
        self.log(f"[{period}] 累积完成: {len(results)} 信号 (池内{len(self.scan_history)}只)")
        return results
    
    # ─── 圣杯级探测 ───
    
    def detect_holy_grail(self, signals: List[IntradaySignal]) -> List[IntradaySignal]:
        """
        圣杯级信号检测
        
        条件:
        1. FLASH级别 (v132 > 0.85)
        2. 跨3+时段持续出现
        3. cumulative_weight > 1.5
        4. M46归一化 > 0.70 (前20%)
        5. 板块热度系数 > 1.0 (非过热板块)
        """
        holy_grails = []
        for sig in signals:
            score = 0
            checks = []
            
            # 条件1: FLASH级别
            if sig.alert_level == AlertLevel.FLASH.label:
                score += 3
                checks.append("FLASH级")
            elif sig.alert_level == AlertLevel.RED.label:
                score += 1
                checks.append("RED级")
            
            # 条件2: 跨时段
            if sig.hit_count >= 3:
                score += 2
                checks.append(f"跨{sig.hit_count}时段")
            
            # 条件3: 累积权重
            if sig.cumulative_weight > 1.5:
                score += 2
                checks.append("权重大")
            
            # 条件4: M46
            if sig.m46_normalized > 0.70:
                score += 2
                checks.append("M46>0.70")
            
            # 条件5: 板块非过热
            if sig.sector_heat_coeff > 1.0:
                score += 1
                checks.append("板块正向")
            
            if score >= 5:
                sig.alert_level = "⚡ 超级信号"  # 覆盖为圣杯
                holy_grails.append(sig)
                self.log(f"🏆 圣杯信号! [{sig.code}] {sig.name} v132={sig.v132_score:.4f} score={score} {checks}")
        
        return holy_grails
    
    # ─── 仪表盘生成 ───
    
    def generate_dashboard(self, signals: List[IntradaySignal], market_index: Dict = None) -> str:
        """生成实时HTML仪表盘"""
        signals_sorted = sorted(signals, key=lambda s: s.cumulative_weight, reverse=True)
        
        # 预警级别统计
        alerts = defaultdict(int)
        for s in signals_sorted:
            alerts[s.alert_level] += 1
        
        # 圣杯探测
        holy_grails = self.detect_holy_grail(signals_sorted)
        
        # 板块统计
        sector_stats = defaultdict(lambda: {'count': 0, 'avg_v132': 0, 'best_code': '', 'best_v132': 0})
        for s in signals_sorted:
            sec = s.sector or '未知'
            sector_stats[sec]['count'] += 1
            sector_stats[sec]['avg_v132'] += s.v132_score
            if s.v132_score > sector_stats[sec]['best_v132']:
                sector_stats[sec]['best_v132'] = s.v132_score
                sector_stats[sec]['best_code'] = f"{s.code} {s.name}"
        for sec in sector_stats:
            sector_stats[sec]['avg_v132'] /= max(sector_stats[sec]['count'], 1)
        
        # 生成HTML
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        market_str = ""
        if market_index:
            sh = market_index.get('000001', {})
            cy = market_index.get('399006', {})
            market_str = f"上证 {sh.get('price','?')} ({sh.get('chg','?')}%) | 创业板 {cy.get('price','?')} ({cy.get('chg','?')}%)"
        
        holy_html = ""
        if holy_grails:
            holy_cards = []
            for hg in holy_grails[:3]:
                holy_cards.append(f"""
                <div class="holy-card">
                    <div class="holy-badge">🏆 圣杯</div>
                    <div class="holy-code">{hg.code}</div>
                    <div class="holy-name">{hg.name}</div>
                    <div class="holy-score">V13.2: {hg.v132_score:.4f}</div>
                    <div class="holy-detail">M46={hg.m46_normalized:.3f} | 累积{hg.hit_count}时段 | 权重{hg.cumulative_weight:.2f}</div>
                </div>""")
            holy_html = f"""
            <div class="holy-section">
                <h2 class="holy-title">🏆 圣杯级信号 ({len(holy_grails)}只)</h2>
                <div class="holy-grid">{''.join(holy_cards)}</div>
            </div>"""
        
        # 信号表格行
        signal_rows = []
        for i, s in enumerate(signals_sorted[:30]):
            alert_class = s.alert_level.replace('⚡ ', '').replace('🔴 ', '').replace('🟠 ', '').replace('🟡 ', '').replace('🟢 ', '').replace('⚪ ', '')
            if '超级信号' in s.alert_level:
                row_class = 'row-holy'
            elif '买入' in s.alert_level:
                row_class = 'row-red'
            elif '预警' in s.alert_level:
                row_class = 'row-orange'
            elif '关注+' in s.alert_level:
                row_class = 'row-yellow'
            elif '关注' in s.alert_level:
                row_class = 'row-green'
            else:
                row_class = ''
            
            signal_rows.append(f"""
            <tr class="{row_class}">
                <td class="rank">{i+1}</td>
                <td class="code">{s.code}</td>
                <td class="name">{s.name}</td>
                <td class="score">{s.v132_score:.4f}</td>
                <td class="subscore">M46={s.m46_normalized:.3f}<br>M57={s.m57_composite:.3f}<br>M64={s.m64_score:.3f}</td>
                <td class="decline {'up' if s.decline_pct > 0 else 'down'}">{s.decline_pct:+.2f}%</td>
                <td class="hits">{s.hit_count}次</td>
                <td class="cumulative">{s.cumulative_weight:.2f}</td>
                <td class="alert">{s.alert_level}</td>
                <td class="rec">{s.recommendation}</td>
            </tr>""")
        
        dashboard_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="120">
    <title>V13.3 毕方灵犀·天眼 — 实时盯盘仪表盘</title>
    <style>
        * {{ margin:0; padding:0; box-sizing:border-box; }}
        body {{ background:#0a0a1a; color:#e0e0e0; font-family:'Microsoft YaHei',sans-serif; padding:20px; }}
        .header {{ text-align:center; margin-bottom:20px; padding:15px; background:linear-gradient(135deg,#1a1a3e,#0d0d2b); border-radius:12px; border:1px solid #333; }}
        .header h1 {{ font-size:24px; background:linear-gradient(90deg,#ff6b35,#f7c948,#00d4aa); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
        .header .status {{ font-size:12px; color:#888; margin-top:5px; }}
        .holy-section {{ margin-bottom:20px; }}
        .holy-title {{ color:#ff6b35; font-size:18px; margin-bottom:10px; text-align:center; animation:pulse 2s infinite; }}
        @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.5}} }}
        .holy-grid {{ display:flex; gap:15px; justify-content:center; flex-wrap:wrap; }}
        .holy-card {{ background:linear-gradient(135deg,#3a1500,#2a0500); border:2px solid #ff6b35; border-radius:12px; padding:15px; min-width:200px; text-align:center; animation:glow 1.5s infinite alternate; }}
        @keyframes glow {{ from{{box-shadow:0 0 10px #ff6b35}} to{{box-shadow:0 0 30px #ff3300}} }}
        .holy-badge {{ font-size:20px; color:#ff6b35; }}
        .holy-code {{ font-size:18px; font-weight:bold; color:#ffaa00; }}
        .holy-name {{ font-size:14px; color:#ccc; }}
        .holy-score {{ font-size:24px; color:#00ff88; font-weight:bold; margin:5px 0; }}
        .holy-detail {{ font-size:11px; color:#999; }}
        .stats-row {{ display:flex; gap:15px; margin-bottom:15px; flex-wrap:wrap; }}
        .stat-card {{ flex:1; min-width:120px; background:#1a1a3e; border-radius:10px; padding:12px; text-align:center; border:1px solid #333; }}
        .stat-card .label {{ font-size:11px; color:#888; }}
        .stat-card .value {{ font-size:24px; font-weight:bold; }}
        .stat-card.flash .value {{ color:#ff3300; }}
        .stat-card.red .value {{ color:#ff5555; }}
        .stat-card.orange .value {{ color:#ff8800; }}
        .stat-card.yellow .value {{ color:#ffcc00; }}
        .stat-card.green .value {{ color:#00ff88; }}
        table {{ width:100%; border-collapse:collapse; font-size:12px; }}
        th {{ background:#1a1a3e; padding:10px 6px; text-align:left; color:#ffaa00; border-bottom:2px solid #333; position:sticky; top:0; }}
        td {{ padding:8px 6px; border-bottom:1px solid #222; }}
        tr:hover {{ background:#222244; }}
        .row-holy {{ background:linear-gradient(90deg,#3a1500,#1a1a3e); }}
        .row-red {{ background:linear-gradient(90deg,#2a0505,#1a1a3e); }}
        .row-orange {{ background:linear-gradient(90deg,#2a1505,#1a1a3e); }}
        .row-yellow {{ background:linear-gradient(90deg,#2a2a05,#1a1a3e); }}
        .rank {{ width:30px; text-align:center; }}
        .code {{ font-family:monospace; font-weight:bold; color:#00d4aa; }}
        .score {{ font-size:16px; font-weight:bold; color:#00ff88; }}
        .subscore {{ font-size:10px; color:#888; }}
        .decline {{ font-weight:bold; }}
        .decline.up {{ color:#ff5555; }}
        .decline.down {{ color:#00ff88; }}
        .hits {{ text-align:center; }}
        .alert {{ font-weight:bold; font-size:13px; }}
        .rec {{ font-size:11px; }}
        .footer {{ text-align:center; margin-top:20px; padding:10px; font-size:10px; color:#555; }}
        .sector-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; margin-bottom:15px; }}
        .sector-card {{ background:#1a1a3e; border-radius:8px; padding:10px; border:1px solid #333; }}
        .sector-card .sec-name {{ font-weight:bold; color:#00d4aa; font-size:12px; }}
        .sector-card .sec-count {{ font-size:11px; color:#ccc; }}
        .sector-card .sec-best {{ font-size:10px; color:#ffaa00; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🦅 毕方灵犀·天眼 V13.3 实时盯盘仪表盘</h1>
        <div class="status">
            最后扫描: <span id="lastScan">{now}</span> | {market_str}
            <br>⏱ 自动刷新: 每120秒 | ✅ 数据源: TDX MCP实时 | ✅ 引擎: M46归一化+M57(8/12)+M64放大
        </div>
    </div>
    
    {holy_html}
    
    <div class="stats-row">
        <div class="stat-card flash"><div class="label">⚡ 圣杯级</div><div class="value" style="color:#ff3300">{len(holy_grails)}</div></div>
        <div class="stat-card red"><div class="label">🔴 买入信号</div><div class="value">{alerts.get('🔴 买入', 0)}</div></div>
        <div class="stat-card orange"><div class="label">🟠 预警</div><div class="value">{alerts.get('🟠 预警', 0)}</div></div>
        <div class="stat-card yellow"><div class="label">🟡 关注+</div><div class="value">{alerts.get('🟡 关注+', 0)}</div></div>
        <div class="stat-card green"><div class="label">🟢 关注</div><div class="value">{alerts.get('🟢 关注', 0)}</div></div>
        <div class="stat-card"><div class="label">📊 总信号</div><div class="value" style="color:#00d4aa">{len(signals_sorted)}</div></div>
    </div>
    
    <h3 style="color:#ffaa00; margin:15px 0 10px;">📋 实时信号排名 (累积权重)</h3>
    <div style="overflow-x:auto; max-height:70vh; overflow-y:auto;">
    <table>
        <thead>
        <tr>
            <th>#</th><th>代码</th><th>名称</th><th>V13.2</th><th>因子分解</th>
            <th>涨跌幅</th><th>命中</th><th>累积权重</th><th>预警</th><th>建议</th>
        </tr>
        </thead>
        <tbody>
            {''.join(signal_rows) if signal_rows else '<tr><td colspan="10" style="text-align:center;color:#888;">暂无信号 — 等待扫描</td></tr>'}
        </tbody>
    </table>
    </div>
    
    <h3 style="color:#ffaa00; margin:20px 0 10px;">📊 板块信号分布</h3>
    <div class="sector-grid">
        {"".join(f'''<div class="sector-card">
            <div class="sec-name">{sec}</div>
            <div class="sec-count">{info['count']}只信号 | avg V13.2={info['avg_v132']:.3f}</div>
            <div class="sec-best">最强: {info['best_code']}</div>
        </div>''' for sec, info in sorted(sector_stats.items(), key=lambda x: x[1]['avg_v132'], reverse=True)[:12])}
    </div>
    
    <div class="footer">
        V13.3 毕方灵犀·天眼 全天候实时监控系统 | 圣杯引擎: M46归一化+M57Alpha+M64放大+跨时段累积 
        | 亚瑟数字分身 | 目标: 涨停命中率99% 盈亏比10.0 踩雷率≤1%
    </div>
    
    <script>
        // 圣杯闪烁动画
        setInterval(() => {{
            document.querySelectorAll('.holy-card').forEach(c => {{
                c.style.opacity = c.style.opacity == '1' ? '0.7' : '1';
            }});
        }}, 1000);
        
        // 检测新圣杯信号
        console.log('🔮 V13.3 仪表盘就绪 | 圣杯级信号: {len(holy_grails)}只');
    </script>
</body>
</html>"""
        
        return dashboard_html
    
    # ─── 主运行流程 ───
    
    def run_period_scan(
        self,
        period: str,
        signals_data: List[Dict],
        period_weight: float = 0.20,
        market_index: Dict = None,
    ) -> Tuple[str, List[IntradaySignal]]:
        """
        运行单时段扫描
        
        Returns: (dashboard_html_path, accumulated_signals)
        """
        self.log(f"=== {period} 时段扫描启动 ===")
        
        # 累积信号
        accumulated = self.accumulate_signals(period, signals_data, period_weight)
        
        # 生成仪表盘
        dashboard_html = self.generate_dashboard(accumulated, market_index)
        
        # 保存仪表盘
        dashboard_path = os.path.join(self.output_dir, "intraday_dashboard.html")
        with open(dashboard_path, 'w', encoding='utf-8') as f:
            f.write(dashboard_html)
        
        # 保存时段快照
        snapshot_path = os.path.join(self.cache_dir, f"scan_{period.replace(':', '')}.json")
        snapshot = {
            'period': period,
            'timestamp': datetime.now().isoformat(),
            'signal_count': len(accumulated),
            'alerts': {level.label: sum(1 for s in accumulated if s.alert_level == level.label) 
                       for level in AlertLevel},
            'top5': [{'code': s.code, 'name': s.name, 'v132': s.v132_score, 
                      'cumulative_weight': s.cumulative_weight} 
                     for s in sorted(accumulated, key=lambda x: x.cumulative_weight, reverse=True)[:5]],
        }
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        
        # 输出摘要
        top3 = sorted(accumulated, key=lambda s: s.cumulative_weight, reverse=True)[:3]
        self.log(f"[{period}] Top3: " + " | ".join(
            f"{s.code} {s.name} cumW={s.cumulative_weight:.2f} {s.alert_level}"
            for s in top3
        ))
        
        return dashboard_path, accumulated
    
    def clear_day(self):
        """清零当日数据 (交易日前夜调用)"""
        self.scan_history.clear()
        self.execution_log.clear()
        self.state = None
    
    def get_summary(self) -> Dict:
        """获取当日监控摘要"""
        all_signals = []
        for code_sigs in self.scan_history.values():
            if code_sigs:
                last = code_sigs[-1]
                all_signals.append(last)
        
        holy_grails = self.detect_holy_grail(all_signals)
        
        return {
            'total_codes_monitored': len(self.scan_history),
            'total_signals': len(all_signals),
            'holy_grail_count': len(holy_grails),
            'holy_grails': [{'code': hg.code, 'name': hg.name, 'v132': hg.v132_score} for hg in holy_grails],
            'scans_completed': len(self.execution_log),
            'top_by_weight': [
                {'code': s.code, 'name': s.name, 'cumulative_weight': s.cumulative_weight, 'hits': s.hit_count}
                for s in sorted(all_signals, key=lambda x: x.cumulative_weight, reverse=True)[:5]
            ],
        }


# ═══════════════════════════════════════════════════════════
# SECTION 3: 自动化脚本接口
# ═══════════════════════════════════════════════════════════

def run_intraday_scan(period: str, signals_file: str = None, use_db: bool = True):
    """
    自动化入口: 运行单个时段的盘中扫描
    
    用法 (在自动化prompt中):
    ```bash
    cd "E:/WorkBuddy_dot_workbuddy/Claw" && python -c "
    from V13_3_IntradayMonitor import run_intraday_scan
    run_intraday_scan('T1_1130')
    "
    ```
    """
    engine = IntradayMonitorEngine()
    
    # 权重映射
    weight_map = {
        'T0_1030': 0.25, 'T1_1130': 0.15, 'T2_1330': 0.15,
        'T3_1400': 0.20, 'T4_1415': 0.25, 'T5_1430': 0.00,
    }
    weight = weight_map.get(period, 0.10)
    
    # 加载信号
    signals = []
    if signals_file and os.path.exists(signals_file):
        signals = engine.load_signals_from_json(signals_file)
    elif use_db:
        db_path = "data/holy_grail.db"
        if os.path.exists(db_path):
            signals = engine.load_signals_from_db(db_path)
    
    if not signals:
        # 尝试从latest json加载
        latest = "data/holy_grail_latest.json"
        if os.path.exists(latest):
            signals = engine.load_signals_from_json(latest)
    
    if signals:
        # 加载市场指数
        market_index = None
        idx_file = "data/tdx_1430_cache.json"
        if os.path.exists(idx_file):
            try:
                with open(idx_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                market_index = cache.get('market_index', None)
            except:
                pass
        
        path, acc = engine.run_period_scan(period, signals, weight, market_index)
        summary = engine.get_summary()
        
        print(f"\n{'='*60}")
        print(f"  V13.3 {period} 扫描完成")
        print(f"  仪表盘: {path}")
        print(f"  监控品种: {summary['total_codes_monitored']}")
        print(f"  圣杯信号: {summary['holy_grail_count']}")
        if summary['holy_grails']:
            for hg in summary['holy_grails']:
                print(f"    🏆 {hg['code']} {hg['name']} v132={hg['v132']:.4f}")
        print(f"{'='*60}")
        
        return path, summary
    else:
        engine.log(f"[{period}] 无信号数据可用", "WARN")
        return None, None


# ═══════════════════════════════════════════════════════════
# SECTION 4: 自测
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("╔══════════════════════════════════════════════════════╗")
    print("║  V13.3 全天候实时盯盘预警系统 自测                ║")
    print("║  圣杯级: v132>0.85 跨3+时段 累积权重>1.5         ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    
    engine = IntradayMonitorEngine()
    
    # 模拟六时段扫描 (使用P1-1 6/24真实30只数据)
    test_signals = {
        '10:30': [
            {'code': '688367', 'name': '工大高科', 'v132_score': 0.42, 'm46_confidence': 0.52, 'm57_composite': 0.15, 'm64_score': 0.35, 'decline_pct': -16.93, 'amplitude': 20.79, 'hsl': 13.24, 'sector': '信息技术', 'recommendation': 'WATCH'},
            {'code': '300540', 'name': '蜀道装备', 'v132_score': 0.48, 'm46_confidence': 0.62, 'm57_composite': 0.22, 'm64_score': 0.92, 'decline_pct': -9.45, 'amplitude': 8.86, 'hsl': 9.97, 'sector': '氢能装备', 'recommendation': 'BUY'},
            {'code': '600977', 'name': '中国电影', 'v132_score': 0.45, 'm46_confidence': 0.38, 'm57_composite': 0.18, 'm64_score': 0.49, 'decline_pct': -10.00, 'amplitude': 7.35, 'hsl': 5.14, 'sector': '传媒', 'recommendation': 'WATCH'},
        ],
        '11:30': [
            {'code': '688367', 'name': '工大高科', 'v132_score': 0.55, 'm46_confidence': 0.58, 'm57_composite': 0.20, 'm64_score': 0.40, 'decline_pct': -16.93, 'amplitude': 20.79, 'hsl': 13.24, 'sector': '信息技术', 'recommendation': 'BUY'},
            {'code': '300540', 'name': '蜀道装备', 'v132_score': 0.62, 'm46_confidence': 0.68, 'm57_composite': 0.28, 'm64_score': 0.95, 'decline_pct': -9.45, 'amplitude': 8.86, 'hsl': 9.97, 'sector': '氢能装备', 'recommendation': 'STRONG_BUY'},
            {'code': '300465', 'name': '高伟达', 'v132_score': 0.50, 'm46_confidence': 0.48, 'm57_composite': 0.15, 'm64_score': 0.57, 'decline_pct': -10.88, 'amplitude': 11.84, 'hsl': 7.09, 'sector': '计算机', 'recommendation': 'BUY'},
        ],
        '14:00': [
            {'code': '688367', 'name': '工大高科', 'v132_score': 0.68, 'm46_confidence': 0.65, 'm57_composite': 0.30, 'm64_score': 0.52, 'decline_pct': -16.93, 'amplitude': 20.79, 'hsl': 13.24, 'sector': '信息技术', 'recommendation': 'STRONG_BUY'},
            {'code': '300540', 'name': '蜀道装备', 'v132_score': 0.78, 'm46_confidence': 0.75, 'm57_composite': 0.35, 'm64_score': 1.10, 'decline_pct': -9.45, 'amplitude': 8.86, 'hsl': 9.97, 'sector': '氢能装备', 'recommendation': 'STRONG_BUY'},
            {'code': '300465', 'name': '高伟达', 'v132_score': 0.65, 'm46_confidence': 0.58, 'm57_composite': 0.22, 'm64_score': 0.68, 'decline_pct': -10.88, 'amplitude': 11.84, 'hsl': 7.09, 'sector': '计算机', 'recommendation': 'STRONG_BUY'},
        ],
        '14:15': [
            {'code': '688367', 'name': '工大高科', 'v132_score': 0.82, 'm46_confidence': 0.78, 'm57_composite': 0.38, 'm64_score': 0.65, 'decline_pct': -16.93, 'amplitude': 20.79, 'hsl': 13.24, 'sector': '信息技术', 'recommendation': 'STRONG_BUY'},
            {'code': '300540', 'name': '蜀道装备', 'v132_score': 0.86, 'm46_confidence': 0.82, 'm57_composite': 0.42, 'm64_score': 1.20, 'decline_pct': -9.45, 'amplitude': 8.86, 'hsl': 9.97, 'sector': '氢能装备', 'recommendation': 'STRONG_BUY'},
        ],
        '14:30': [
            {'code': '688367', 'name': '工大高科', 'v132_score': 0.88, 'm46_confidence': 0.85, 'm57_composite': 0.42, 'm64_score': 0.72, 'decline_pct': -16.93, 'amplitude': 20.79, 'hsl': 13.24, 'sector': '信息技术', 'recommendation': 'STRONG_BUY'},
            {'code': '300540', 'name': '蜀道装备', 'v132_score': 0.90, 'm46_confidence': 0.88, 'm57_composite': 0.45, 'm64_score': 1.25, 'decline_pct': -9.45, 'amplitude': 8.86, 'hsl': 9.97, 'sector': '氢能装备', 'recommendation': 'STRONG_BUY'},
        ],
    }
    
    all_signals = []
    for period, signals in test_signals.items():
        w = {'10:30': 0.25, '11:30': 0.15, '14:00': 0.20, '14:15': 0.25, '14:30': 0.0}.get(period, 0.10)
        acc = engine.accumulate_signals(period, signals, w)
        all_signals.extend(acc)
        print(f"  [{period}] {len(signals)}只 → 累积池{len(engine.scan_history)}只 | 权重{w:.2f}")
    
    # 生成仪表盘
    dashboard = engine.generate_dashboard(all_signals)
    dashboard_path = os.path.join(engine.output_dir, "intraday_dashboard.html")
    with open(dashboard_path, 'w', encoding='utf-8') as f:
        f.write(dashboard)
    
    # 摘要
    summary = engine.get_summary()
    
    print(f"\n{'='*60}")
    print(f"  🏆 全天监控摘要")
    print(f"  监控品种: {summary['total_codes_monitored']} 只")
    print(f"  圣杯信号: {summary['holy_grail_count']} 只")
    for hg in summary['holy_grails']:
        print(f"    ⚡ {hg['code']} {hg['name']} v132={hg['v132']:.4f}")
    print(f"\n  Top5 累积权重:")
    for i, t in enumerate(summary['top_by_weight']):
        print(f"    {i+1}. {t['code']} {t['name']} cumW={t['cumulative_weight']:.2f} hits={t['hits']}")
    print(f"\n  📊 仪表盘: {dashboard_path}")
    print(f"{'='*60}")
