#!/usr/bin/env python3
"""V13.4 夜间深度分析报告生成器"""

import json, os, sqlite3
from datetime import datetime, date

# ═══ LOAD ALL DATA ═══
with open('data/fullmarket_cache/state_20260624.json', 'r', encoding='utf-8') as f:
    state = json.load(f)
with open('data/fullmarket_cache/sentiment_alert.json', 'r', encoding='utf-8') as f:
    sent_alert = json.load(f)
with open('data/fullmarket_cache/news_20260624_18.json', 'r', encoding='utf-8') as f:
    news = json.load(f)

conn = sqlite3.connect('data/holy_grail.db')
c = conn.cursor()
c.execute('''SELECT code, name, v132_score, m46_score, m57_score, m64_score,
                    sector_heat, recommendation, decline_pct, amplitude, hsl
             FROM daily_signals WHERE signal_date="2026-06-24" 
             ORDER BY v132_score DESC''')
signals = [dict(zip(['code','name','v132','m46','m57','m64','heat','rec','decline','amplitude','hsl'], r))
           for r in c.fetchall()]
c.execute('SELECT COUNT(*), COUNT(CASE WHEN was_hit=1 THEN 1 END), COUNT(CASE WHEN was_hit=0 THEN 1 END) FROM p1_1_tracking WHERE signal_date="2026-06-24"')
t1 = c.fetchone()
conn.close()

# ═══ ANALYSIS ═══
summary = state['summary']
top_stocks = state['top_stocks']
market_data = news['markets']
indices = market_data['asiapacific']['indices']
sh_close = indices['SHANGHAI_COMPOSITE']['close']
sh_change = indices['SHANGHAI_COMPOSITE']['pct']
sz_close = indices['SHENZHEN_COMPONENT']['close']
sz_change = indices['SHENZHEN_COMPONENT']['pct']

v132_scores = [s['v132_score'] for s in top_stocks]
avg_v132 = sum(v132_scores) / len(v132_scores)
max_v132 = max(v132_scores)
m46_scores = [s.get('m46_score', 0) for s in top_stocks]
avg_m46 = sum(m46_scores) / len(m46_scores)
m57_scores = [s.get('m57_score', 0) for s in top_stocks]
avg_m57 = sum(m57_scores) / len(m57_scores)
m64_scores = [s.get('m64_score', 0) for s in top_stocks]
avg_m64 = sum(m64_scores) / len(m64_scores)

declines = [abs(s.get('decline_pct', 0)) for s in top_stocks]
avg_decline = sum(declines) / len(declines)

signal_v132 = [s['v132'] for s in signals]
signal_m46 = [s['m46'] for s in signals]
signal_m57 = [s['m57'] for s in signals]
signal_m64 = [s['m64'] for s in signals]

tier_a = sum(1 for s in top_stocks if s.get('tier') == 'A')
tier_b = sum(1 for s in top_stocks if s.get('tier') == 'B')
holy_grail_count = summary.get('holy_grail_count', 0)

rec_dist = {}
for s in signals:
    r = s['rec']
    rec_dist[r] = rec_dist.get(r, 0) + 1

# Portfolio
positions = [
    ('301669', '高特电子', 45.20, 19.99, 600, 6.94, 22956, 551),
    ('300540', '蜀道装备', 26.19, -8.97, 1300, 27.66, -1911, -5.3),
    ('920961', '创远信科', 22.40, 4.67, 100, 21.13, 127, 6.0),
]
total_pnl = sum(p[6] for p in positions)
total_value = sum(p[2] * p[4] for p in positions)

bullish = news['market_signals']['bullish']
bearish = news['market_signals']['bearish']

alerts_count = len(sent_alert['alerts'])

# ═══ HTML GENERATION ═══
html_parts = []

def w(s):
    html_parts.append(s)

def pnl_color(v):
    return '#00c853' if v > 0 else '#ff1744'

def stock_color(v):
    return '#00c853' if v > 0 else '#ff1744'

# CSS
w('''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.4 夜间深度分析报告 2026-06-24</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Segoe UI',system-ui,-apple-system,sans-serif;background:#0a0e17;color:#e0e6f0;min-height:100vh}
.header{background:linear-gradient(135deg,#1a1f35 0%,#0d1119 100%);border-bottom:1px solid #1e2a3a;padding:24px 32px;display:flex;justify-content:space-between;align-items:center}
.header h1{font-size:1.6em;background:linear-gradient(135deg,#ff6b35,#ffd700);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .subtitle{color:#6b7d95;font-size:0.85em}
.container{max-width:1400px;margin:0 auto;padding:24px 32px}
.row{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}
.card{background:#111827;border:1px solid #1e2a3a;border-radius:12px;padding:20px}
.card h3{color:#6b7d95;font-size:0.8em;text-transform:uppercase;margin-bottom:8px;letter-spacing:0.5px}
.card .value{font-size:2em;font-weight:700}
.card .sub{color:#6b7d95;font-size:0.8em;margin-top:4px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
.chart-card{background:#111827;border:1px solid #1e2a3a;border-radius:12px;padding:20px}
.chart-card h3{color:#fff;font-size:1em;margin-bottom:16px}
canvas{max-height:300px}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:0.88em}
th{text-align:left;padding:10px 12px;border-bottom:2px solid #1e2a3a;color:#6b7d95;font-weight:600}
td{padding:10px 12px;border-bottom:1px solid #1a2235}
tr:hover{background:rgba(255,107,53,0.05)}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.75em;font-weight:600}
.badge-red{background:rgba(255,23,68,0.15);color:#ff1744}
.badge-orange{background:rgba(255,145,0,0.15);color:#ff9100}
.badge-yellow{background:rgba(255,214,0,0.15);color:#ffd600}
.badge-green{background:rgba(0,200,83,0.15);color:#00c853}
.badge-blue{background:rgba(68,138,255,0.15);color:#448aff}
.section-title{font-size:1.3em;color:#fff;margin:28px 0 16px;padding-bottom:8px;border-bottom:2px solid #1e2a3a}
.alert-box{border-left:3px solid;padding:12px 16px;margin:8px 0;border-radius:0 8px 8px 0;background:rgba(255,255,255,0.02)}
.alert-box.red{border-color:#ff1744}
.alert-box.orange{border-color:#ff9100}
.alert-box.yellow{border-color:#ffd600}
.alert-box h4{margin-bottom:4px}
.alert-box p{color:#8b9dc3;font-size:0.88em}
.warning{background:rgba(255,145,0,0.08);border:1px solid rgba(255,145,0,0.2);border-radius:8px;padding:16px;margin:16px 0}
.warning h4{color:#ff9100;margin-bottom:8px}
.warning p{color:#b8c7d9;font-size:0.88em}
.footer{text-align:center;padding:24px;color:#3a4d66;font-size:0.78em;border-top:1px solid #1e2a3a;margin-top:32px}
</style>
</head>
<body>
''')

# Header
w(f'''<div class="header">
  <div>
    <h1>🦅 毕方灵犀貔貅 · V13.4 夜间深度分析</h1>
    <div class="subtitle">2026-06-24 (周三) 20:00 | 执行ID: automation-1780320609631</div>
  </div>
  <div style="text-align:right">
    <div style="font-size:1.4em;font-weight:700;color:#ff6b35">R9=75.7</div>
    <div class="subtitle">圣杯对齐 91.6分</div>
  </div>
</div>
<div class="container">
''')

# KPI Cards
w(f'''<div class="row">
  <div class="card">
    <h3>📊 上证指数</h3>
    <div class="value" style="color:#ff6b35">{sh_close:.1f}</div>
    <div class="sub" style="color:{stock_color(sh_change)}">{sh_change:+.2f}%</div>
  </div>
  <div class="card">
    <h3>📊 深证成指</h3>
    <div class="value" style="color:#ff6b35">{sz_close:.1f}</div>
    <div class="sub" style="color:{stock_color(sz_change)}">{sz_change:+.2f}%</div>
  </div>
  <div class="card">
    <h3>🎯 全市场扫描</h3>
    <div class="value" style="color:#448aff">{summary['total_scanned']}</div>
    <div class="sub">A档{tier_a}只 | B档{tier_b}只 | 圣杯{holy_grail_count}</div>
  </div>
  <div class="card">
    <h3>📈 P1-1 信号</h3>
    <div class="value" style="color:#ff9100">{len(signals)}</div>
    <div class="sub">V132均{avg_v132:.3f} | T+1待验证</div>
  </div>
  <div class="card">
    <h3>💰 持仓总市值</h3>
    <div class="value" style="color:#00c853">¥{total_value:,.0f}</div>
    <div class="sub" style="color:{pnl_color(total_pnl)}">浮盈 ¥{total_pnl:+,.0f}</div>
  </div>
  <div class="card">
    <h3>🏆 圣杯交叉学习</h3>
    <div class="value" style="color:#b388ff">5/5</div>
    <div class="sub">命中率 100%</div>
  </div>
</div>
''')

# Charts
w('''<div class="grid2">
  <div class="chart-card">
    <h3>📊 全市场V132评分分布</h3>
    <canvas id="v132Hist"></canvas>
  </div>
  <div class="chart-card">
    <h3>📊 M46/M57/M64 均值对比</h3>
    <canvas id="factorRadar"></canvas>
  </div>
</div>
''')

# TOP 10 Signals
w('<h3 class="section-title">🔥 P1-1 14:30 信号 TOP 10</h3>')
w('<table><thead><tr><th>排名</th><th>代码</th><th>名称</th><th>V132</th><th>M46</th><th>M57</th><th>M64</th><th>跌幅</th><th>板块热度</th><th>建议</th></tr></thead><tbody>')
for i, s in enumerate(signals[:10]):
    color = '#ff1744' if s['v132'] > 0.75 else ('#ff9100' if s['v132'] > 0.65 else '#ffd600')
    decl = s['decline'] if s['decline'] else 0
    w(f'<tr><td style="color:#6b7d95">#{i+1}</td><td style="font-weight:600">{s["code"]}</td><td>{s["name"]}</td><td style="color:{color};font-weight:700">{s["v132"]:.4f}</td><td>{s["m46"]:.4f}</td><td>{s["m57"]:.4f}</td><td>{s["m64"]:.4f}</td><td style="color:#ff1744">{decl:+.2f}%</td><td>{s["heat"]}</td><td><span class="badge badge-red">{s["rec"]}</span></td></tr>')
w('</tbody></table>')

# Portfolio
w('<h3 class="section-title">💼 持仓快照</h3>')
w('<table><thead><tr><th>代码</th><th>名称</th><th>收盘</th><th>涨跌</th><th>持仓</th><th>成本</th><th>市值</th><th>浮盈</th><th>浮盈%</th></tr></thead><tbody>')
for p in positions:
    code, name, close, pct, shares, cost, pnl, pnl_pct = p
    mkt_val = close * shares
    w(f'<tr><td style="font-weight:600">{code}</td><td>{name}</td><td>¥{close:.2f}</td><td style="color:{stock_color(pct)};font-weight:600">{pct:+.2f}%</td><td>{shares}股</td><td>¥{cost:.2f}</td><td>¥{mkt_val:,.0f}</td><td style="color:{pnl_color(pnl)};font-weight:600">¥{pnl:+,.0f}</td><td style="color:{pnl_color(pnl)}">{pnl_pct:+.1f}%</td></tr>')
w(f'<tr style="background:rgba(255,107,53,0.05);font-weight:700"><td colspan="4"></td><td>合计</td><td></td><td>¥{total_value:,.0f}</td><td style="color:{pnl_color(total_pnl)}">¥{total_pnl:+,.0f}</td><td style="color:{pnl_color(total_pnl)}">+{total_pnl/total_value*100:.1f}%</td></tr>')
w('</tbody></table>')

# Sentiment Alerts
w('<h3 class="section-title">🌍 全球市场情绪与舆情</h3>')
w('<div class="grid2"><div>')
for a in sent_alert['alerts']:
    lvl = a['level'].lower()
    lvl_cn = {'red': '🔴 红色预警', 'orange': '🟠 橙色预警', 'yellow': '🟡 黄色关注'}.get(lvl, lvl)
    w(f'''<div class="alert-box {lvl}">
      <h4>{lvl_cn}: {a['headline']}</h4>
      <p><strong>市场影响:</strong> {a['market_impact']}</p>
      <p><strong>A股影响:</strong> {a['a_share_impact']}</p>
    </div>''')

# Bull/Bear signals
w(f'</div><div><h4 style="color:#6b7d95;margin-bottom:8px">📊 市场多空信号</h4><div style="margin-bottom:12px"><h5 style="color:#00c853;margin-bottom:4px">🟢 利多信号 ({len(bullish)})</h5>')
for b in bullish:
    w(f'<p style="color:#8b9dc3;font-size:0.85em;margin-left:12px">• {b}</p>')
w('</div><div><h5 style="color:#ff1744;margin-bottom:4px">🔴 利空信号</h5>')
for b in bearish:
    w(f'<p style="color:#8b9dc3;font-size:0.85em;margin-left:12px">• {b}</p>')
w('</div></div></div>')

# Asia-Pacific Market Table
w('<h3 class="section-title">📈 亚太市场收盘数据</h3>')
w('<table><thead><tr><th>指数</th><th>收盘价</th><th>涨跌幅</th><th>状态</th></tr></thead><tbody>')
for name, data in indices.items():
    pct = data['pct']
    bc = 'badge-green' if pct > 1 else ('badge-blue' if pct > 0 else ('badge-yellow' if pct > -1 else 'badge-red'))
    status = '大涨' if pct > 2 else ('上涨' if pct > 0 else ('微跌' if pct > -1 else '大跌'))
    w(f'<tr><td style="font-weight:600">{name}</td><td>{data["close"]:,.2f}</td><td style="color:{stock_color(pct)};font-weight:600">{pct:+.2f}%</td><td><span class="badge {bc}">{status}</span></td></tr>')
w('</tbody></table>')

# M70 & T0-T5 Backtest
w('<div class="grid2">')
w('''<div class="chart-card">
    <h3>🤖 M70 LightGBM 自适应权重</h3>
    <canvas id="m70Chart"></canvas>
    <div style="margin-top:12px">
      <p style="color:#8b9dc3;font-size:0.88em">状态: 就绪 (30训练样本/0已验证T+1)</p>
      <p style="color:#6b7d95;font-size:0.82em">下轮: 6/25 15:10 奖惩回路 (30条P1-1数据)</p>
    </div>
  </div>''')
w('''<div class="chart-card">
    <h3>📋 T0-T5 全市场回测摘要</h3>
    <div style="margin-top:8px">
      <p style="color:#8b9dc3;font-size:0.88em;margin-bottom:8px">2026-06-24 各时段状态:</p>
      <table style="font-size:0.82em">
        <tr><td style="color:#6b7d95">T0 10:30</td><td>⚪ 未执行</td><td style="color:#6b7d95">(自动化后创建)</td></tr>
        <tr><td style="color:#6b7d95">T1 11:30</td><td>⚪ 未执行</td><td style="color:#6b7d95">(同上)</td></tr>
        <tr><td style="color:#6b7d95">T3 14:00</td><td>⚪ 未执行</td><td style="color:#6b7d95">(同上)</td></tr>
        <tr><td style="color:#6b7d95">T4 14:15</td><td>⚪ 未执行</td><td style="color:#6b7d95">(同上)</td></tr>
        <tr><td style="color:#6b7d95">T5 14:30</td><td style="color:#448aff">✅ 139只</td><td>A{tier_a}/B{tier_b} 圣杯0</td></tr>
      </table>
      <p style="color:#ff9100;font-size:0.82em;margin-top:8px">⚠️ T0-T4明日(6/25)首次完整运行</p>
    </div>
  </div>''')
w('</div>')

# This Week Trend
w('<h3 class="section-title">📈 本周趋势评估 (6/23 - 6/27)</h3>')
w('''<div class="card" style="margin-bottom:16px">
  <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;text-align:center;margin-bottom:16px">
    <div><div style="color:#6b7d95;font-size:0.75em">周一 6/22</div><div style="color:#8b9dc3">端午节</div><div style="font-size:0.75em">休市</div></div>
    <div><div style="color:#6b7d95;font-size:0.75em">周二 6/23</div><div style="color:#ff1744;font-size:1.2em;font-weight:700">上证-1.37%</div><div style="color:#ff1744">创业板-3.84%</div></div>
    <div><div style="color:#6b7d95;font-size:0.75em">周三 6/24</div><div style="color:#00c853;font-size:1.2em;font-weight:700">上证+0.11%</div><div style="color:#00c853">深证+1.24%</div></div>
    <div><div style="color:#6b7d95;font-size:0.75em">周四 6/25</div><div style="color:#ff9100;font-size:1.2em;font-weight:700">待定</div><div style="color:#ff9100">P1-1 T+1验证</div></div>
    <div><div style="color:#6b7d95;font-size:0.75em">周五 6/26</div><div style="color:#ff9100;font-size:1.2em;font-weight:700">待定</div><div style="color:#ff9100">T+2追踪</div></div>
  </div>
  <div style="border-top:1px solid #1e2a3a;padding-top:12px">
    <p style="color:#8b9dc3;font-size:0.88em"><strong>本周核心矛盾:</strong> 美伊和平协议(利多风险偏好) vs 美国制造业衰退信号(利空全球经济)</p>
    <ul style="color:#6b7d95;font-size:0.85em;margin-left:16px;margin-top:4px">
      <li>A股大盘弱(上证+0.11%) 中小盘强(深证+1.24%) — 风格切换信号</li>
      <li>油价暴跌(Brent-1.53%至$75.9) → 利好航空/化工/物流</li>
      <li>韩国KOSPI暴涨3.26%领涨亚太 → 出口导向乐观</li>
      <li>P1-1 30只信号全部超跌(-5%~-17%) → 明日T+1验证</li>
    </ul>
  </div>
</div>''')

# Warnings
w('''<div class="warning">
  <h4>⚠️ 关键风险提醒</h4>
  <p>1. 美国制造业裁员达金融危机水平 — 可能预示全球衰退</p>
  <p>2. P1-1 DB中M46仍为旧值(~0.96) — state文件已修复(~0.5)但信号DB未刷新</p>
  <p>3. T0-T4时段自动化今日未执行 — 明日6/25首次完整运行</p>
</div>''')

w('</div>')  # close container

# Footer
w('''<div class="footer">
  <p>毕方灵犀貔貅助手 · V13.4 · 2026-06-24 20:00 自动生成</p>
  <p>26自动化(18 ACTIVE) · 50模块 · ~37,500行 · 15 SQLite表 · 圣杯对齐91.6</p>
</div>''')

# JS Charts
w(f'''<script>
new Chart(document.getElementById('v132Hist'),{{
  type:'bar',
  data:{{labels:['0.35-0.45','0.45-0.55','0.55-0.65','0.65-0.75','0.75-0.85','>0.85'],
    datasets:[{{label:'股票数',data:{json.dumps([
      sum(1 for s in v132_scores if 0.35<=s<0.45),
      sum(1 for s in v132_scores if 0.45<=s<0.55),
      sum(1 for s in v132_scores if 0.55<=s<0.65),
      sum(1 for s in v132_scores if 0.65<=s<0.75),
      sum(1 for s in v132_scores if 0.75<=s<0.85),
      sum(1 for s in v132_scores if s>=0.85),
    ])},backgroundColor:'rgba(255,107,53,0.7)',borderRadius:4}}]}},
  options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
    scales:{{y:{{grid:{{color:'#1e2a3a'}}}},x:{{grid:{{display:false}}}}}}}}
}});

new Chart(document.getElementById('factorRadar'),{{
  type:'radar',
  data:{{labels:['全市场M46','全市场M57','全市场M64','P1-1 M46','P1-1 M57','P1-1 M64'],
    datasets:[{{label:'缩放均值',data:[{avg_m46*10:.1f},{avg_m57*10:.1f},{avg_m64*2:.1f},
      {(sum(signal_m46)/len(signal_m46))*10:.1f},{(sum(signal_m57)/len(signal_m57))*10:.1f},{(sum(signal_m64)/len(signal_m64))*2:.1f}],
      backgroundColor:'rgba(255,107,53,0.2)',borderColor:'rgba(255,107,53,0.8)',borderWidth:2}}]
  }},
  options:{{responsive:true,maintainAspectRatio:false,
    scales:{{r:{{grid:{{color:'#1e2a3a'}},pointLabels:{{color:'#8b9dc3'}},ticks:{{display:false}}}}}}}}
}});

new Chart(document.getElementById('m70Chart'),{{
  type:'bar',
  data:{{labels:['M64','跌幅','M46','M57','板块热度'],
    datasets:[{{label:'特征重要性%',data:[26.24,21.67,18.32,18.08,15.69],
      backgroundColor:['#b388ff','#ff1744','#448aff','#ff9100','#00c853'],borderRadius:4}}]
  }},
  options:{{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},
    scales:{{x:{{grid:{{color:'#1e2a3a'}},max:30}},y:{{grid:{{display:false}}}}}}}}
}});
</script>
</body></html>''')

# Write HTML
html_content = ''.join(html_parts)
output_path = 'outputs/V134_nightly_report_20260624.html'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f'✅ 报告: {output_path} ({len(html_content)} chars)')

# JSON Summary
summary_json = {
    'report_time': '2026-06-24T20:00:00+08:00',
    'market': {'shanghai': {'close': sh_close, 'change_pct': sh_change}, 'shenzhen': {'close': sz_close, 'change_pct': sz_change}},
    'fullmarket': {'total_scanned': summary['total_scanned'], 'tier_a': tier_a, 'tier_b': tier_b, 'holy_grail': holy_grail_count, 'avg_v132': round(avg_v132, 4), 'avg_m46': round(avg_m46, 4), 'avg_m57': round(avg_m57, 4), 'avg_m64': round(avg_m64, 4), 'avg_decline': round(avg_decline, 2)},
    'p1_1': {'total': len(signals), 'avg_v132': round(sum(signal_v132)/len(signal_v132), 4), 'avg_m46': round(sum(signal_m46)/len(signal_m46), 4), 'avg_m57': round(sum(signal_m57)/len(signal_m57), 4), 'avg_m64': round(sum(signal_m64)/len(signal_m64), 4), 't1_pending': t1[0], 'rec_dist': rec_dist, 'top3': [{'code': s['code'], 'name': s['name'], 'v132': s['v132']} for s in signals[:3]]},
    'portfolio': {'total_value': total_value, 'total_pnl': total_pnl, 'pnl_pct': round(total_pnl/total_value*100, 1)},
    'sentiment': {'alerts': alerts_count, 'levels': {'RED': 1, 'ORANGE': 1, 'YELLOW': 1}, 'reward': 75},
    'm70': {'status': 'ready', 'samples': 30, 'verified_t1': 0, 'next': '2026-06-25 15:10', 'features': {'m64_score': 0.2624, 'decline_pct': 0.2167, 'm46_normalized': 0.1832, 'm57_composite': 0.1808, 'sector_heat_coeff': 0.1569}},
    'holygrail': {'cross_learn_hit': 5, 'cross_learn_total': 5, 'hit_rate': 1.0},
    'week': {'monday': '休市', 'tuesday': '上证-1.37% 创业板-3.84%', 'wednesday': f'上证{sh_change:+.2f}% 深证{sz_change:+.2f}%', 'thursday': 'P1-1 T+1验证', 'friday': '待定'}
}

json_path = 'outputs/V134_nightly_summary_20260624.json'
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(summary_json, f, ensure_ascii=False, indent=2)

print(f'✅ JSON: {json_path}')
print(f'   全市场: {summary["total_scanned"]}只 A{tier_a}/B{tier_b} V132均值={avg_v132:.4f}')
print(f'   P1-1: {len(signals)}只 STRONG_BUY T+1={t1[0]}待验证')
print(f'   持仓: ¥{total_value:,.0f} 浮盈¥{total_pnl:+,.0f} (+{total_pnl/total_value*100:.1f}%)')
print(f'   舆情: {alerts_count}条预警 奖惩+75分')
print(f'   M70: 30样本 6/25注入T+1')
print(f'   圣杯: 交叉学习5/5 100%命中')
