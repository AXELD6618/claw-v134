#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓分析报告生成器
计算三只持仓股票的浮动盈亏、交易回顾、风险评估
"""

import json
import os
from datetime import datetime


def generate_holdings_report():
    """生成HTML持仓分析报告"""
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # ============ 持仓数据 ============
    holdings = [
        {
            "name": "高特电子",
            "code": "301669",
            "market": "深市创业板",
            "shares": 600,
            "cost": 6.94,
            "cost_total": 4164.00,
            "price": 45.20,
            "prev_close": 37.67,
            "change_pct": 19.99,
            "market_value": 27120.00,
            "pnl": 22956.00,
            "pnl_pct": 551.30,
            "zt_price": 45.20,
            "is_zt": True,
            "note": "涨停板！中签底仓+日内T+0"
        },
        {
            "name": "蜀道装备",
            "code": "300540",
            "market": "深市创业板",
            "shares": 1300,
            "cost": 27.66,
            "cost_total": 35958.00,
            "price": 25.67,
            "prev_close": 28.77,
            "change_pct": -10.78,
            "market_value": 33371.00,
            "pnl": -2587.00,
            "pnl_pct": -7.20,
            "zt_price": 34.52,
            "dt_price": 23.02,
            "is_zt": False,
            "note": "今日大跌，距跌停还有10.3%"
        },
        {
            "name": "创远信科",
            "code": "920961",
            "market": "北交所",
            "shares": 100,
            "cost": 21.132,
            "cost_total": 2113.20,
            "price": 21.81,
            "prev_close": 21.40,
            "change_pct": 1.92,
            "market_value": 2181.00,
            "pnl": 67.80,
            "pnl_pct": 3.21,
            "zt_price": 27.82,
            "is_zt": False,
            "note": "小仓位观察仓"
        }
    ]
    
    # ============ 高特电子交易回顾 ============
    gt_trades_buy = [
        {"date": "06-23", "price": 38.220, "shares": 600, "amount": 22932.00},
        {"date": "06-23", "price": 38.350, "shares": 1300, "amount": 49855.00},
        {"date": "06-23", "price": 38.020, "shares": 900, "amount": 34218.00},
        {"date": "06-23", "price": 37.890, "shares": 900, "amount": 34101.00},
    ]
    gt_trades_sell = [
        {"date": "06-24", "price": 42.600, "shares": 900, "amount": 38340.00},
        {"date": "06-24", "price": 43.750, "shares": 500, "amount": 21875.00},
        {"date": "06-24", "price": 45.200, "shares": 1700, "amount": 76840.00},
    ]
    
    total_buy_amount = sum(t["amount"] for t in gt_trades_buy)
    total_buy_shares = sum(t["shares"] for t in gt_trades_buy)
    avg_buy_price = total_buy_amount / total_buy_shares  # 38.137
    
    total_sell_amount = sum(t["amount"] for t in gt_trades_sell)
    total_sell_shares = sum(t["shares"] for t in gt_trades_sell)
    avg_sell_price = total_sell_amount / total_sell_shares  # 44.21
    
    # T+0部分盈亏（卖出3100股按买入均价计算成本）
    t_zero_cost = total_sell_shares * avg_buy_price
    t_zero_pnl = total_sell_amount - t_zero_cost
    
    # ============ 总持仓汇总 ============
    total_market_value = sum(h["market_value"] for h in holdings)
    total_cost = sum(h["cost_total"] for h in holdings)
    total_pnl = total_market_value - total_cost
    total_pnl_pct = (total_pnl / total_cost) * 100
    
    # ============ 生成HTML ============
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>持仓分析报告 | 2026-06-24 11:30</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Microsoft YaHei', sans-serif; background: #0a0e17; color: #e0e6ed; padding: 20px; }}
.container {{ max-width: 1000px; margin: 0 auto; }}
.header {{ text-align: center; padding: 30px 0; border-bottom: 2px solid #1a2332; margin-bottom: 30px; }}
.header h1 {{ font-size: 28px; color: #ffd700; margin-bottom: 8px; }}
.header .time {{ color: #8899aa; font-size: 14px; }}

/* 总览卡片 */
.overview {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 30px; }}
.overview-card {{ background: linear-gradient(135deg, #111827, #1a2332); border: 1px solid #1e3a5f; border-radius: 12px; padding: 20px; text-align: center; }}
.overview-card .label {{ color: #8899aa; font-size: 13px; margin-bottom: 8px; }}
.overview-card .value {{ font-size: 24px; font-weight: bold; }}
.overview-card .sub {{ color: #8899aa; font-size: 12px; margin-top: 4px; }}
.value.positive {{ color: #ff4444; }}
.value.negative {{ color: #00cc66; }}

/* 持仓详情 */
.section-title {{ font-size: 20px; color: #ffd700; margin: 25px 0 15px; padding-left: 12px; border-left: 4px solid #ffd700; }}

.holding-card {{ background: linear-gradient(135deg, #111827, #1a2332); border: 1px solid #1e3a5f; border-radius: 12px; padding: 25px; margin-bottom: 20px; }}
.holding-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; }}
.holding-name {{ font-size: 22px; font-weight: bold; }}
.holding-code {{ color: #8899aa; font-size: 13px; margin-left: 10px; }}
.holding-badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
.badge-zt {{ background: #ff444440; color: #ff6666; border: 1px solid #ff4444; }}
.badge-up {{ background: #ff444420; color: #ff6666; border: 1px solid #ff444460; }}
.badge-down {{ background: #00cc6620; color: #00cc66; border: 1px solid #00cc6660; }}

.holding-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 15px; }}
.holding-item {{ background: #0d1321; border-radius: 8px; padding: 12px; text-align: center; }}
.holding-item .item-label {{ color: #667788; font-size: 11px; margin-bottom: 4px; }}
.holding-item .item-value {{ font-size: 18px; font-weight: bold; }}

.pnl-bar {{ height: 8px; background: #1a2332; border-radius: 4px; overflow: hidden; margin-top: 10px; }}
.pnl-bar-fill {{ height: 100%; border-radius: 4px; transition: width 0.5s; }}
.pnl-bar-fill.positive {{ background: linear-gradient(90deg, #ff4444, #ff6666); }}
.pnl-bar-fill.negative {{ background: linear-gradient(90deg, #00cc66, #00ff88); }}

/* 交易回顾 */
.trade-review {{ background: linear-gradient(135deg, #111827, #1a2332); border: 1px solid #1e3a5f; border-radius: 12px; padding: 25px; margin-bottom: 20px; }}
.trade-table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 14px; }}
.trade-table th {{ background: #0d1321; color: #8899aa; padding: 10px 12px; text-align: center; border-bottom: 1px solid #1e3a5f; }}
.trade-table td {{ padding: 10px 12px; text-align: center; border-bottom: 1px solid #1a2332; }}
.trade-table .buy-row td {{ color: #ff6666; }}
.trade-table .sell-row td {{ color: #00cc66; }}
.trade-table .total-row td {{ font-weight: bold; border-top: 2px solid #1e3a5f; }}

/* 风险提示 */
.risk-alert {{ background: linear-gradient(135deg, #1a0a0a, #2a1515); border: 1px solid #ff444440; border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
.risk-alert .risk-title {{ color: #ff6666; font-size: 16px; font-weight: bold; margin-bottom: 12px; }}
.risk-alert .risk-item {{ color: #cc9999; font-size: 14px; margin: 8px 0; padding-left: 16px; position: relative; }}
.risk-alert .risk-item::before {{ content: '⚠'; position: absolute; left: 0; }}

.footer {{ text-align: center; padding: 30px 0; color: #445566; font-size: 12px; border-top: 1px solid #1a2332; margin-top: 30px; }}

@media (max-width: 768px) {{
    .overview {{ grid-template-columns: repeat(2, 1fr); }}
    .holding-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
</style>
</head>
<body>
<div class="container">

<div class="header">
    <h1>📊 持仓分析报告</h1>
    <div class="time">生成时间: {now} | 数据截至 11:30 盘中</div>
</div>

<!-- 总览 -->
<div class="overview">
    <div class="overview-card">
        <div class="label">持仓总市值</div>
        <div class="value" style="color:#ffd700">¥{total_market_value:,.2f}</div>
        <div class="sub">3只股票</div>
    </div>
    <div class="overview-card">
        <div class="label">持仓总成本</div>
        <div class="value" style="color:#8899aa">¥{total_cost:,.2f}</div>
        <div class="sub">加权平均</div>
    </div>
    <div class="overview-card">
        <div class="label">浮动盈亏</div>
        <div class="value {'positive' if total_pnl >= 0 else 'negative'}">{"+" if total_pnl >= 0 else ""}¥{total_pnl:,.2f}</div>
        <div class="sub">收益率 {"+" if total_pnl_pct >= 0 else ""}{total_pnl_pct:.2f}%</div>
    </div>
    <div class="overview-card">
        <div class="label">今日已实现盈亏</div>
        <div class="value positive">+¥{t_zero_pnl:,.2f}</div>
        <div class="sub">高特电子T+0部分</div>
    </div>
</div>

<!-- 持仓详情 -->
<div class="section-title">📋 持仓详情</div>

"""

    # 每只股票的卡片
    for h in holdings:
        pnl_class = "positive" if h["pnl"] >= 0 else "negative"
        pnl_sign = "+" if h["pnl"] >= 0 else ""
        
        bar_pct = abs(h["pnl_pct"])
        bar_class = "positive" if h["pnl"] >= 0 else "negative"
        
        if h["is_zt"]:
            badge = '<span class="holding-badge badge-zt">涨停</span>'
        elif h["change_pct"] > 0:
            badge = f'<span class="holding-badge badge-up">+{h["change_pct"]:.2f}%</span>'
        else:
            badge = f'<span class="holding-badge badge-down">{h["change_pct"]:.2f}%</span>'
        
        html += f"""
<div class="holding-card">
    <div class="holding-header">
        <div>
            <span class="holding-name">{h["name"]}</span>
            <span class="holding-code">{h["code"]} | {h["market"]}</span>
        </div>
        <div>{badge}</div>
    </div>
    
    <div class="holding-grid">
        <div class="holding-item">
            <div class="item-label">持仓数量</div>
            <div class="item-value" style="color:#c0c8d0">{h["shares"]:,}股</div>
        </div>
        <div class="holding-item">
            <div class="item-label">成本价</div>
            <div class="item-value" style="color:#8899aa">¥{h["cost"]:,.2f}</div>
        </div>
        <div class="holding-item">
            <div class="item-label">现价</div>
            <div class="item-value" style="color:#ffd700">¥{h["price"]:,.2f}</div>
        </div>
        <div class="holding-item">
            <div class="item-label">今日涨跌</div>
            <div class="item-value {pnl_class}">{h["change_pct"]:+.2f}%</div>
        </div>
    </div>
    
    <div class="holding-grid">
        <div class="holding-item">
            <div class="item-label">持仓市值</div>
            <div class="item-value" style="color:#c0c8d0">¥{h["market_value"]:,.2f}</div>
        </div>
        <div class="holding-item">
            <div class="item-label">成本总额</div>
            <div class="item-value" style="color:#8899aa">¥{h["cost_total"]:,.2f}</div>
        </div>
        <div class="holding-item">
            <div class="item-label">浮动盈亏</div>
            <div class="item-value {pnl_class}">{pnl_sign}¥{h["pnl"]:,.2f}</div>
        </div>
        <div class="holding-item">
            <div class="item-label">收益率</div>
            <div class="item-value {pnl_class}">{pnl_sign}{h["pnl_pct"]:.2f}%</div>
        </div>
    </div>
    
    <div class="pnl-bar">
        <div class="pnl-bar-fill {bar_class}" style="width:{min(bar_pct, 100)}%"></div>
    </div>
    <div style="color:#667788;font-size:11px;margin-top:5px;">{h.get('note','')}</div>
</div>
"""

    # 高特电子交易回顾
    html += """
<div class="section-title">📈 高特电子交易回顾</div>

<div class="trade-review">
    <div style="display:flex;justify-content:space-between;margin-bottom:15px;">
        <div style="color:#ffd700;font-weight:bold;">6月23日 买入</div>
        <div style="color:#8899aa;font-size:13px;">总买入 {total_buy_shares} 股 | 均价 ¥{avg_buy_price:.3f}</div>
    </div>
    <table class="trade-table">
        <tr><th>日期</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th></tr>
""".format(total_buy_shares=total_buy_shares, avg_buy_price=avg_buy_price)
    
    for t in gt_trades_buy:
        html += f"<tr class='buy-row'><td>{t['date']}</td><td>买入</td><td>¥{t['price']:.3f}</td><td>{t['shares']}股</td><td>¥{t['amount']:,.2f}</td></tr>\n"
    
    html += f"<tr class='total-row' style='color:#ff6666;'><td colspan='2'>合计</td><td>¥{avg_buy_price:.3f}</td><td>{total_buy_shares}股</td><td>¥{total_buy_amount:,.2f}</td></tr>\n"
    
    html += f"""
</table>

<div style="display:flex;justify-content:space-between;margin:25px 0 15px;">
    <div style="color:#00cc66;font-weight:bold;">6月24日 卖出</div>
    <div style="color:#8899aa;font-size:13px;">总卖出 {total_sell_shares} 股 | 均价 ¥{avg_sell_price:.3f}</div>
</div>
<table class="trade-table">
    <tr><th>日期</th><th>方向</th><th>价格</th><th>数量</th><th>金额</th></tr>
"""
    
    for t in gt_trades_sell:
        html += f"<tr class='sell-row'><td>{t['date']}</td><td>卖出</td><td>¥{t['price']:.3f}</td><td>{t['shares']}股</td><td>¥{t['amount']:,.2f}</td></tr>\n"
    
    html += f"<tr class='total-row' style='color:#00cc66;'><td colspan='2'>合计</td><td>¥{avg_sell_price:.3f}</td><td>{total_sell_shares}股</td><td>¥{total_sell_amount:,.2f}</td></tr>\n"
    
    html += f"""
</table>

<div style="margin-top:20px;padding:15px;background:#0d1321;border-radius:8px;">
    <div style="color:#ffd700;font-weight:bold;margin-bottom:10px;">T+0 已实现盈亏</div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
        <div><span style="color:#8899aa">卖出总额</span><br><span style="color:#00cc66;font-size:18px;">¥{total_sell_amount:,.2f}</span></div>
        <div><span style="color:#8899aa">成本</span><br><span style="color:#8899aa;font-size:18px;">¥{t_zero_cost:,.2f}</span></div>
        <div><span style="color:#8899aa">净盈亏</span><br><span style="color:#ff4444;font-size:18px;">+¥{t_zero_pnl:,.2f}</span></div>
        <div><span style="color:#8899aa">收益率</span><br><span style="color:#ff4444;font-size:18px;">+{t_zero_pnl/t_zero_cost*100:.2f}%</span></div>
    </div>
</div>

<div style="margin-top:15px;padding:15px;background:#0d1321;border-radius:8px;">
    <div style="color:#ffd700;font-weight:bold;margin-bottom:10px;">剩余持仓（中签底仓）</div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;">
        <div><span style="color:#8899aa">持仓数量</span><br><span style="color:#c0c8d0;font-size:18px;">600股</span></div>
        <div><span style="color:#8899aa">成本价</span><br><span style="color:#8899aa;font-size:18px;">¥6.94</span></div>
        <div><span style="color:#8899aa">现价/涨停价</span><br><span style="color:#ff4444;font-size:18px;">¥45.20</span></div>
        <div><span style="color:#8899aa">浮动盈利</span><br><span style="color:#ff4444;font-size:18px;">+¥{holdings[0]['pnl']:,.2f}</span></div>
    </div>
</div>
</div>
"""

    # 风险提示
    html += f"""
<div class="section-title">⚠️ 风险评估</div>

<div class="risk-alert">
    <div class="risk-title">🔴 高风险预警</div>
    <div class="risk-item">蜀道装备今日大跌 -10.78%，距跌停价 23.02 仅差 10.3%，若跌停将再亏损约 ¥{(holdings[1]['price'] - holdings[1]['dt_price']) * holdings[1]['shares']:,.2f}</div>
    <div class="risk-item">高特电子已连续涨停，今日封板于 45.20，换手率 44% 极高，筹码松动风险大</div>
    <div class="risk-item">高特电子20日涨幅高达 538%，短期存在获利回吐压力</div>
</div>

<div class="risk-alert" style="background:linear-gradient(135deg, #0a1a0a, #152a15);border-color:#00cc6640;">
    <div class="risk-title" style="color:#00cc66;">🟢 有利因素</div>
    <div class="risk-item" style="color:#99cc99;">高特电子T+0操作成功，已实现盈利 ¥{t_zero_pnl:,.2f}</div>
    <div class="risk-item" style="color:#99cc99;">高特电子中签底仓成本极低（6.94元），安全垫充足，浮盈超过5.5倍</div>
    <div class="risk-item" style="color:#99cc99;">创远信科小仓位观察，成本21.13元，现价21.81元，小幅盈利</div>
</div>
"""

    html += f"""
<div class="section-title">💡 操作建议</div>

<div class="trade-review">
    <table style="width:100%;font-size:14px;">
        <tr style="border-bottom:1px solid #1a2332;">
            <td style="padding:12px;font-weight:bold;color:#ffd700;width:80px;">高特电子</td>
            <td style="padding:12px;color:#c0c8d0;">
                <strong>建议：持有观察</strong><br>
                底仓600股成本极低（6.94元），浮盈超5.5倍，可继续持有。<br>
                但涨停封板44%换手率极高，如明日开板或低开，建议至少减持一半锁定利润。<br>
                如继续涨停，可持至开板日。
            </td>
        </tr>
        <tr style="border-bottom:1px solid #1a2332;">
            <td style="padding:12px;font-weight:bold;color:#ffd700;">蜀道装备</td>
            <td style="padding:12px;color:#c0c8d0;">
                <strong>建议：观望或设止损</strong><br>
                今日大跌10.78%，浮亏约2,587元。股价接近跌停价23.02。<br>
                建议设置止损线在23.00元（跌停价附近），若跌破立即止损。<br>
                如午后反弹至26.50以上，可考虑减仓降低风险。
            </td>
        </tr>
        <tr>
            <td style="padding:12px;font-weight:bold;color:#ffd700;">创远信科</td>
            <td style="padding:12px;color:#c0c8d0;">
                <strong>建议：继续持有</strong><br>
                小仓位观察仓，现小幅盈利。北交所股票波动较大。<br>
                如跌破成本价21.13元可止损，否则继续持有观察。
            </td>
        </tr>
    </table>
</div>
"""

    html += f"""
<div class="footer">
    毕方灵犀·天眼 贝叶斯概率量化交易系统 V13.2<br>
    报告生成时间: {now} | 数据来源: TDX实时行情<br>
    ⚠️ 本报告仅供参考，不构成投资建议。投资有风险，入市需谨慎。
</div>

</div>
</body>
</html>"""
    
    return html


if __name__ == "__main__":
    html = generate_holdings_report()
    output_path = "持仓分析报告_20260624_1130.html"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ 报告已生成: {output_path}")
    print(f"   文件大小: {len(html):,} 字符")
