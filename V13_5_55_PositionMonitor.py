#!/usr/bin/env python3
"""
V13.5.55 Position Monitor — 持仓盘中实时监控人机协同引擎
====================================================================
功能:
  1. 接收TDX实时行情数据(JSON) → 计算盈亏/量比/主买方向
  2. V54简化版对倒检测(主力/主买背离) → 实时排雷
  3. 生成HOLD/SELL/WATCH信号 + 具体价格位
  4. 格式化HTML推送(PushPlus兼容)
  5. 保存监控日志(JSON)

使用方式:
  python V13_5_55_PositionMonitor.py --data-file=data/v55_intraday.json --push
  python V13_5_55_PositionMonitor.py --data-file=data/v55_intraday.json --no-push

数据文件格式 (v55_intraday.json):
{
  "timestamp": "2026-07-13T09:45:00",
  "phase": "OPEN_15MIN",  // OPEN_AUCTION|OPEN_15MIN|OPEN_30MIN|MORNING_CLOSE|AFTERNOON_OPEN|AFTERNOON_30MIN|FINAL_DECISION
  "holdings": [
    {
      "code": "600118", "name": "中国卫星", "setcode": "1",
      "cost": 83.469, "shares": 2000,
      "quotes": { "Price": 92.50, "ZDF": 2.75, "HSL": 3.5, "InOut": -5000000, "Outside": 80000000, "Inside": 85000000, "Volume": 15000000, "Amount": 1380000000 },
      "zjlx_today": { "main_net": 11.62, "main_net_pct": 21.9, "active_buy_net": -12.0, "active_buy_pct": -22.6 },
      "key_levels": { "limit_up": 99.02, "prev_close": 90.02, "support": 84.0, "stop_loss": 81.84 }
    },
    ...
  ]
}
"""

import os, sys, json, argparse, subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════
# 持仓配置
# ═══════════════════════════════════════════════

PORTFOLIO = [
    {"code": "600118", "name": "中国卫星", "setcode": "1", "cost": 83.469, "shares": 2000, "priority": "CRITICAL"},
    {"code": "920249", "name": "利尔达",   "setcode": "2", "cost": 10.807, "shares": 145,  "priority": "LOW"},
    {"code": "300287", "name": "飞利信",   "setcode": "0", "cost": 3.793,  "shares": 1700, "priority": "MEDIUM"},
    {"code": "300017", "name": "网宿科技", "setcode": "0", "cost": 12.523, "shares": 500,  "priority": "MEDIUM"},
    {"code": "600255", "name": "鑫科材料", "setcode": "1", "cost": 3.055,  "shares": 3300, "priority": "MEDIUM"},
    {"code": "000958", "name": "电投产融", "setcode": "0", "cost": 5.370,  "shares": 4200, "priority": "MEDIUM"},
]

PHASE_NAMES = {
    "OPEN_AUCTION":     "09:25 开盘集合竞价",
    "OPEN_15MIN":       "09:45 开盘15分钟(最关键)",
    "OPEN_30MIN":       "10:00 开盘30分钟",
    "MORNING_CLOSE":    "11:20 上午收盘",
    "AFTERNOON_OPEN":   "13:00 下午开盘",
    "AFTERNOON_30MIN":  "13:30 下午30分钟(防脉冲)",
    "FINAL_DECISION":   "14:55 尾盘最终决策",
}

# ═══════════════════════════════════════════════
# V54 简化版实时对倒检测
# ═══════════════════════════════════════════════

def detect_wash_trade_realtime(quotes: Dict, zjlx_today: Optional[Dict] = None) -> Dict:
    """
    实时对倒检测 — 基于TDX quotes的InOut/Outside/Inside + zjlx主力/主买方向
    
    返回:
    {
        "is_wash": bool,
        "confidence": float,  # 0-1
        "signal": str,        # GENUINE / WASH_TRADE / NEUTRAL
        "reason": str,
        "main_buy_direction": str,  # POSITIVE / NEGATIVE / NEUTRAL
    }
    """
    inout = quotes.get("InOut", 0) or 0
    outside = quotes.get("Outside", 0) or 0
    inside = quotes.get("Inside", 0) or 0
    
    # 主买方向判断
    if inout > 0:
        main_buy_dir = "POSITIVE"
    elif inout < 0:
        main_buy_dir = "NEGATIVE"
    else:
        main_buy_dir = "NEUTRAL"
    
    # 外盘/内盘比率
    total_oi = outside + inside
    if total_oi > 0:
        outside_ratio = outside / total_oi
    else:
        outside_ratio = 0.5
    
    # 如果有zjlx数据，进行深度检测
    wash_confidence = 0.0
    reasons = []
    
    if zjlx_today:
        main_net_pct = zjlx_today.get("main_net_pct", 0) or 0
        active_buy_pct = zjlx_today.get("active_buy_pct", 0) or 0
        
        # 核心对倒检测: 主力正但主买负
        if main_net_pct > 3 and active_buy_pct < -3:
            wash_confidence = min(abs(main_net_pct - active_buy_pct) / 50, 0.95)
            reasons.append(f"主力{main_net_pct:+.1f}%但主买{active_buy_pct:+.1f}% (背离{abs(main_net_pct-active_buy_pct):.1f}%)")
        elif main_net_pct > 0 and active_buy_pct < 0:
            wash_confidence = min(abs(main_net_pct - active_buy_pct) / 30, 0.6)
            reasons.append(f"主力{main_net_pct:+.1f}%主买{active_buy_pct:+.1f}% (轻度背离)")
        elif main_net_pct > 0 and active_buy_pct > 0:
            reasons.append(f"主力{main_net_pct:+.1f}%主买{active_buy_pct:+.1f}% (同向正=真实买入)")
        elif main_net_pct < 0 and active_buy_pct < 0:
            reasons.append(f"主力{main_net_pct:+.1f}%主买{active_buy_pct:+.1f}% (同向负=流出)")
    
    # InOut验证
    if inout < 0 and quotes.get("ZDF", 0) > 3:
        wash_confidence = max(wash_confidence, 0.5)
        reasons.append(f"涨幅{quotes.get('ZDF',0):+.1f}%但主买净额为负(InOut={inout/10000:.0f}万)")
    elif inout > 0 and quotes.get("ZDF", 0) > 0:
        reasons.append(f"涨幅{quotes.get('ZDF',0):+.1f}%主买净额为正(InOut={inout/10000:.0f}万)=真实买入")
    
    # 外盘内盘
    if outside_ratio < 0.45 and quotes.get("ZDF", 0) > 2:
        wash_confidence = max(wash_confidence, 0.4)
        reasons.append(f"外盘占比{outside_ratio*100:.0f}%<45% (内盘主导)")
    
    # 最终信号
    if wash_confidence >= 0.6:
        signal = "WASH_TRADE"
        is_wash = True
    elif wash_confidence >= 0.3:
        signal = "NEUTRAL"
        is_wash = False
    else:
        signal = "GENUINE"
        is_wash = False
    
    return {
        "is_wash": is_wash,
        "confidence": round(wash_confidence, 3),
        "signal": signal,
        "reason": "; ".join(reasons) if reasons else "数据不足",
        "main_buy_direction": main_buy_dir,
        "outside_ratio": round(outside_ratio, 3),
    }


# ═══════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════

def generate_signal(holding: Dict, wash_result: Dict, phase: str) -> Dict:
    """生成交易信号"""
    quotes = holding.get("quotes", {})
    zjlx = holding.get("zjlx_today")
    cost = holding["cost"]
    shares = holding["shares"]
    price = quotes.get("Price", 0)
    zdf = quotes.get("ZDF", 0)
    hsl = quotes.get("HSL", 0)
    
    # 盈亏
    pnl_per_share = price - cost
    pnl_total = pnl_per_share * shares
    pnl_pct = (pnl_per_share / cost * 100) if cost > 0 else 0
    
    # 信号逻辑
    signal = "HOLD"
    action = "持有观察"
    urgency = "LOW"
    details = []
    
    # 对倒检测优先
    if wash_result["signal"] == "WASH_TRADE":
        if pnl_pct > 5:
            signal = "SELL"
            action = f"分批止盈(先卖50%)"
            urgency = "HIGH"
        elif pnl_pct > 0:
            signal = "SELL"
            action = f"减仓止损(先卖30%)"
            urgency = "HIGH"
        else:
            signal = "WATCH"
            action = "密切观察(亏损中对倒)"
            urgency = "MEDIUM"
        details.append(f"⚠️ 对倒检测: {wash_result['reason']}")
    elif wash_result["signal"] == "GENUINE":
        if zdf > 5 and wash_result["main_buy_direction"] == "POSITIVE":
            signal = "STRONG_HOLD"
            action = "坚定持有(真实买入+涨幅)"
            urgency = "LOW"
            details.append(f"✅ 真实买入: {wash_result['reason']}")
        elif zdf < -3 and wash_result["main_buy_direction"] == "NEGATIVE":
            signal = "WATCH"
            action = "观察支撑(流出但未破位)"
            urgency = "MEDIUM"
            details.append(f"流出: {wash_result['reason']}")
        else:
            signal = "HOLD"
            action = "持有"
            urgency = "LOW"
            details.append(f"正常: {wash_result['reason']}")
    else:  # NEUTRAL
        signal = "HOLD"
        action = "持有观察"
        urgency = "LOW"
        details.append(f"中性: {wash_result['reason']}")
    
    # 关键价位检查
    key_levels = holding.get("key_levels", {})
    if key_levels:
        if price >= key_levels.get("limit_up", 999999):
            details.append(f"📌 涨停价{key_levels['limit_up']}")
        if price <= key_levels.get("stop_loss", 0) and price > 0:
            signal = "SELL"
            action = "止损清仓"
            urgency = "CRITICAL"
            details.append(f"🛑 跌破止损线{key_levels['stop_loss']}!")
        elif price <= key_levels.get("support", 0) and price > 0:
            signal = "WATCH"
            action = "关注支撑"
            urgency = "HIGH"
            details.append(f"⚠️ 接近支撑位{key_levels['support']}")
    
    # 量比异常
    if hsl > 10:
        details.append(f"🔥 高换手{hsl:.1f}%")
    
    return {
        "code": holding["code"],
        "name": holding["name"],
        "price": price,
        "zdf": zdf,
        "hsl": hsl,
        "cost": cost,
        "shares": shares,
        "pnl_pct": round(pnl_pct, 2),
        "pnl_total": round(pnl_total, 2),
        "signal": signal,
        "action": action,
        "urgency": urgency,
        "wash_detection": wash_result,
        "details": details,
        "phase": phase,
        "timestamp": datetime.now().isoformat(),
    }


# ═══════════════════════════════════════════════
# HTML推送格式
# ═══════════════════════════════════════════════

def format_push_html(signals: List[Dict], phase: str, portfolio_pnl: float, portfolio_pnl_pct: float) -> str:
    """格式化HTML推送内容"""
    now = datetime.now().strftime("%H:%M:%S")
    phase_name = PHASE_NAMES.get(phase, phase)
    
    # 按紧急程度排序
    urgency_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    sorted_signals = sorted(signals, key=lambda x: urgency_order.get(x["urgency"], 4))
    
    # 统计
    sell_count = sum(1 for s in signals if s["signal"] in ("SELL", "STRONG_SELL"))
    wash_count = sum(1 for s in signals if s["wash_detection"]["signal"] == "WASH_TRADE")
    genuine_count = sum(1 for s in signals if s["wash_detection"]["signal"] == "GENUINE")
    
    pnl_color = "#e74c3c" if portfolio_pnl >= 0 else "#27ae60"
    pnl_emoji = "📈" if portfolio_pnl >= 0 else "📉"
    
    lines = [
        f"<h2>🛡️ V55 持仓监控 — {phase_name}</h2>",
        f"<p><b>时间:</b> {now} | <b>总盈亏:</b> <span style='color:{pnl_color};font-weight:bold'>{pnl_emoji} {portfolio_pnl:+.0f}元 ({portfolio_pnl_pct:+.2f}%)</span></p>",
        f"<p><b>对倒警报:</b> {wash_count}只 | <b>真实买入:</b> {genuine_count}只 | <b>卖出信号:</b> {sell_count}只</p>",
        "<hr/>",
        "<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse;width:100%;font-size:13px'>",
        "<tr style='background:#2c3e50;color:white'><th> urgency </th><th>信号</th><th>代码</th><th>名称</th><th>现价</th><th>涨幅%</th><th>盈亏%</th><th>主买</th><th>操作建议</th></tr>",
    ]
    
    for s in sorted_signals:
        urgency = s["urgency"]
        signal = s["signal"]
        
        # 颜色
        if urgency == "CRITICAL":
            bg = "#e74c3c"; fg = "white"
        elif urgency == "HIGH":
            bg = "#fff3cd"; fg = "#856404"
        elif urgency == "MEDIUM":
            bg = "#fff8e1"; fg = "#666"
        else:
            bg = "#f8f9fa"; fg = "#333"
        
        # 信号emoji
        signal_emoji = {
            "STRONG_HOLD": "🟢坚定持有",
            "HOLD": "🟡持有",
            "WATCH": "🟠观察",
            "SELL": "🔴卖出",
            "STRONG_SELL": "🔴🔴清仓",
        }.get(signal, "❓")
        
        # 主买方向
        main_buy = s["wash_detection"]["main_buy_direction"]
        if main_buy == "POSITIVE":
            mb_display = "<span style='color:#e74c3c'>正✅</span>"
        elif main_buy == "NEGATIVE":
            mb_display = "<span style='color:#27ae60'>负⚠️</span>"
        else:
            mb_display = "中性"
        
        # 涨幅颜色 (A股: 红涨绿跌)
        zdf = s["zdf"]
        zdf_color = "#e74c3c" if zdf > 0 else ("#27ae60" if zdf < 0 else "#333")
        
        # 盈亏颜色
        pnl = s["pnl_pct"]
        pnl_color = "#e74c3c" if pnl > 0 else ("#27ae60" if pnl < 0 else "#333")
        
        lines.append(
            f"<tr style='background:{bg};color:{fg}'>"
            f"<td style='font-weight:bold'>{urgency}</td>"
            f"<td>{signal_emoji}</td>"
            f"<td><b>{s['code']}</b></td>"
            f"<td>{s['name']}</td>"
            f"<td>{s['price']:.2f}</td>"
            f"<td style='color:{zdf_color};font-weight:bold'>{zdf:+.2f}%</td>"
            f"<td style='color:{pnl_color};font-weight:bold'>{pnl:+.2f}%</td>"
            f"<td>{mb_display}</td>"
            f"<td style='font-size:12px'>{s['action']}</td>"
            f"</tr>"
        )
    
    lines.append("</table>")
    
    # 详细分析
    critical_signals = [s for s in sorted_signals if s["urgency"] in ("CRITICAL", "HIGH")]
    if critical_signals:
        lines.append("<hr/>")
        lines.append("<h3>⚠️ 关键信号详情</h3>")
        for s in critical_signals:
            lines.append(f"<div style='background:#fff3cd;padding:8px;margin:4px 0;border-radius:4px'>")
            lines.append(f"<b>{s['code']} {s['name']}</b> — {s['action']}")
            lines.append(f"<br/><span style='font-size:12px;color:#666'>{' | '.join(s['details'])}</span>")
            lines.append("</div>")
    
    # 底部
    lines.append("<hr/>")
    lines.append(f"<p style='color:#888;font-size:11px'>毕方灵犀·天眼 V13.5.55 持仓监控 | {now} | 人机协同: 系统提供信号, 您决策执行</p>")
    
    return "\n".join(lines)


def format_push_plain(signals: List[Dict], phase: str, portfolio_pnl: float, portfolio_pnl_pct: float) -> str:
    """纯文本推送格式(降级)"""
    now = datetime.now().strftime("%H:%M:%S")
    phase_name = PHASE_NAMES.get(phase, phase)
    
    urgency_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    sorted_signals = sorted(signals, key=lambda x: urgency_order.get(x["urgency"], 4))
    
    sell_count = sum(1 for s in signals if s["signal"] in ("SELL", "STRONG_SELL"))
    wash_count = sum(1 for s in signals if s["wash_detection"]["signal"] == "WASH_TRADE")
    
    pnl_emoji = "📈" if portfolio_pnl >= 0 else "📉"
    
    lines = [
        f"V55 持仓监控 — {phase_name}",
        "=" * 40,
        f"时间: {now}",
        f"总盈亏: {pnl_emoji} {portfolio_pnl:+.0f}元 ({portfolio_pnl_pct:+.2f}%)",
        f"对倒警报: {wash_count}只 | 卖出信号: {sell_count}只",
        "",
        "-" * 40,
    ]
    
    for s in sorted_signals:
        signal_emoji = {"STRONG_HOLD": "🟢", "HOLD": "🟡", "WATCH": "🟠", "SELL": "🔴", "STRONG_SELL": "🔴🔴"}.get(s["signal"], "?")
        main_buy = s["wash_detection"]["main_buy_direction"][0] if s["wash_detection"]["main_buy_direction"] != "NEUTRAL" else "N"
        lines.append(
            f"{signal_emoji} [{s['urgency']:8s}] {s['code']} {s['name']} "
            f"| {s['price']:.2f}({s['zdf']:+.2f}%) "
            f"| 盈亏{s['pnl_pct']:+.1f}% "
            f"| 主买:{main_buy} "
            f"| {s['action']}"
        )
    
    lines.append("")
    lines.append("-" * 40)
    
    critical = [s for s in sorted_signals if s["urgency"] in ("CRITICAL", "HIGH")]
    if critical:
        lines.append("KEY SIGNALS:")
        for s in critical:
            lines.append(f"  {s['code']} {s['name']}: {' | '.join(s['details'])}")
    
    lines.append("")
    lines.append("人机协同: 系统提供信号, 您决策执行")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# PushPlus推送
# ═══════════════════════════════════════════════

def push_notification(title: str, html_content: str, plain_content: str) -> bool:
    """通过PushPlus推送(优先HTML, 降级纯文本)"""
    token = os.environ.get("PUSHPLUS_TOKEN", "")
    
    if not token:
        # 读取.env
        env_path = Path(".env")
        if env_path.exists():
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("PUSHPLUS_TOKEN="):
                        token = line.strip().split("=", 1)[1].strip()
                        break
    
    if not token:
        print("[V55] WARNING: PUSHPLUS_TOKEN not configured. Push skipped.")
        print("[V55] Content preview:")
        print(plain_content[:500])
        return False
    
    # 使用cloud_notify.push
    try:
        sys.path.insert(0, str(Path.cwd()))
        from cloud_notify import push
        return push(title, html_content, template="html")
    except Exception as e:
        print(f"[V55] Push error: {e}")
        return False


# ═══════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════

def run_monitor(data_file: str, do_push: bool = True) -> Dict:
    """运行持仓监控"""
    # 加载数据
    data_path = Path(data_file)
    if not data_path.exists():
        print(f"[V55] ERROR: Data file not found: {data_file}")
        return {"error": "data_file_not_found"}
    
    with open(data_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    phase = data.get("phase", "UNKNOWN")
    holdings_data = data.get("holdings", [])
    timestamp = data.get("timestamp", datetime.now().isoformat())
    
    print(f"[V55] Running monitor: phase={phase}, holdings={len(holdings_data)}")
    
    # 分析每只持仓
    signals = []
    total_cost = 0
    total_value = 0
    
    for h in holdings_data:
        quotes = h.get("quotes", {})
        wash_result = detect_wash_trade_realtime(quotes, h.get("zjlx_today"))
        signal = generate_signal(h, wash_result, phase)
        signals.append(signal)
        
        total_cost += h["cost"] * h["shares"]
        if quotes.get("Price", 0) > 0:
            total_value += quotes["Price"] * h["shares"]
    
    portfolio_pnl = total_value - total_cost
    portfolio_pnl_pct = (portfolio_pnl / total_cost * 100) if total_cost > 0 else 0
    
    print(f"[V55] Portfolio P&L: {portfolio_pnl:+.0f} ({portfolio_pnl_pct:+.2f}%)")
    
    # 打印信号
    for s in signals:
        urgency_emoji = {"CRITICAL": "🚨", "HIGH": "⚠️", "MEDIUM": "🟡", "LOW": "🟢"}.get(s["urgency"], "?")
        print(f"  {urgency_emoji} {s['code']} {s['name']} | {s['signal']:12s} | {s['price']:.2f}({s['zdf']:+.2f}%) | P&L:{s['pnl_pct']:+.1f}% | {s['action']}")
    
    # 推送
    if do_push:
        phase_name = PHASE_NAMES.get(phase, phase)
        title = f"V55持仓监控 {phase_name} | 盈亏{portfolio_pnl:+.0f}元"
        html = format_push_html(signals, phase, portfolio_pnl, portfolio_pnl_pct)
        plain = format_push_plain(signals, phase, portfolio_pnl, portfolio_pnl_pct)
        
        push_result = push_notification(title, html, plain)
        print(f"[V55] Push: {'OK' if push_result else 'SKIPPED'}")
    else:
        push_result = False
    
    # 保存结果
    result = {
        "timestamp": timestamp,
        "phase": phase,
        "portfolio_pnl": round(portfolio_pnl, 2),
        "portfolio_pnl_pct": round(portfolio_pnl_pct, 2),
        "total_cost": round(total_cost, 2),
        "total_value": round(total_value, 2),
        "signals": signals,
        "push_sent": push_result,
    }
    
    output_dir = Path("data/v55_monitor")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"monitor_{phase}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"[V55] Result saved: {output_file}")
    
    # 也输出控制台摘要
    print("\n" + "=" * 60)
    print(format_push_plain(signals, phase, portfolio_pnl, portfolio_pnl_pct))
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="V13.5.55 Position Monitor")
    ap.add_argument("--data-file", required=True, help="JSON file with TDX data")
    ap.add_argument("--push", action="store_true", default=True, help="Push notification (default)")
    ap.add_argument("--no-push", action="store_true", help="Skip push notification")
    
    args = ap.parse_args()
    
    do_push = not args.no_push
    result = run_monitor(args.data_file, do_push)
    
    sys.exit(0 if "error" not in result else 1)
