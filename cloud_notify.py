#!/usr/bin/env python3
"""
V13.4 Cloud Notifier — PushPlus 云端微信推送
==============================================
GitHub Actions 运行完毕后，推送选股信号/告警到用户微信。

通道：
  1. PushPlus (主力) — https://www.pushplus.plus — 200条/天免费
  2. Server酱 (备用) — https://sct.ftqq.com

使用方式 (GitHub Actions):
  env:
    PUSHPLUS_TOKEN: ${{ secrets.PUSHPLUS_TOKEN }}
    SERVERCHAN_KEY: ${{ secrets.SERVERCHAN_KEY }}  # 可选备用
  run: python cloud_notify.py --step=T5 --data=cloud_outputs/holy_grail_signals.json
"""

import os, sys, json, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional


# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

PUSHPLUS_API = "https://www.pushplus.plus/send"
SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "")
SERVERCHAN_KEY = os.environ.get("SERVERCHAN_KEY", "")


def push_pushplus(token: str, title: str, content: str, template: str = "html") -> bool:
    """通过PushPlus推送到微信。"""
    data = {
        "token": token,
        "title": title,
        "content": content,
        "template": template,
    }
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(PUSHPLUS_API, data=body, headers={"Content-Type": "application/json"})
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        success = resp.get("code") == 200
        print(f"[PushPlus] {'OK' if success else 'FAIL'}: {resp.get('msg', '?')}")
        return success
    except Exception as e:
        print(f"[PushPlus ERROR] {e}")
        return False


def push_serverchan(key: str, title: str, content: str) -> bool:
    """通过Server酱推送到微信（备用通道）。"""
    try:
        url = f"{SERVERCHAN_API.format(key=key)}?title={urllib.parse.quote(title)}&desp={urllib.parse.quote(content)}"
        resp = json.loads(urllib.request.urlopen(url, timeout=10).read())
        success = resp.get("code") == 0
        print(f"[Server酱] {'OK' if success else 'FAIL'}: {resp.get('message', '?')}")
        return success
    except Exception as e:
        print(f"[Server酱 ERROR] {e}")
        return False


def push(title: str, content: str, template: str = "html") -> bool:
    """统一推送入口，PushPlus优先，失败自动切Server酱。"""
    if PUSHPLUS_TOKEN:
        if push_pushplus(PUSHPLUS_TOKEN, title, content, template):
            return True
        print("[WARN] PushPlus failed, trying Server酱...")

    if SERVERCHAN_KEY:
        if push_serverchan(SERVERCHAN_KEY, title, content):
            return True

    print("[ERROR] All push channels failed!")
    return False


# ═══════════════════════════════════════════════
# 消息格式化
# ═══════════════════════════════════════════════

ICON = "🦅🔥💰"


def format_t4_notification(data: Dict) -> str:
    """T4 最终确认候选池通知。"""
    candidates = data.get("list", data.get("final_candidates", data))
    if isinstance(candidates, int):
        count = candidates
        items = []
    elif isinstance(candidates, list):
        count = len(candidates)
        items = candidates[:10]
    else:
        count = data.get("final_candidates", 0)
        items = []

    now = datetime.now().strftime("%H:%M:%S")
    lines = [
        f"<h2>{ICON} V13.4 T4 最终确认</h2>",
        f"<p><b>时间：</b>{now} | <b>候选数：</b>{count}只</p>",
        "<hr/>",
    ]

    if items:
        lines.append("<h3>🏆 顶级候选 (Top 10)</h3>")
        lines.append("<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse;width:100%'>")
        lines.append("<tr style='background:#f0f0f0'><th>排名</th><th>代码</th><th>名称</th><th>评分</th><th>涨幅%</th><th>换手%</th></tr>")
        for i, s in enumerate(items, 1):
            code = s.get("code", "?")
            name = s.get("name", "?")
            score = s.get("score", s.get("cloud_score", "?"))
            pct = s.get("pct", s.get("pct_chg", "?"))
            turnover = s.get("turnover", "?")
            color = "#e74c3c" if isinstance(pct, (int, float)) and pct > 0 else "#27ae60"
            lines.append(
                f"<tr><td>{i}</td><td>{code}</td><td>{name}</td>"
                f"<td>{score}</td><td style='color:{color};font-weight:bold'>{pct}%</td><td>{turnover}%</td></tr>"
            )
        lines.append("</table>")

    lines.append(f"<p style='color:#888;font-size:12px'>⏰ T5 圣杯选股将在14:30执行，请关注后续推送。</p>")
    return "\n".join(lines)


def format_t5_notification(data: Dict) -> str:
    """T5 圣杯选股结果推送。"""
    signals = data.get("signals", data.get("list", []))
    run_time = datetime.now().strftime("%H:%M:%S")

    if isinstance(signals, list):
        ss_count = sum(1 for s in signals if s.get("level", s.get("grade", "")) in ("SSS", "SS"))
        lines = [
            f"<h2>{ICON} ⚡ 圣杯选股结果 ⚡</h2>",
            f"<p><b>时间：</b>{run_time} | <b>总信号：</b>{len(signals)}只 | <b>强信号(SS+)：</b>{ss_count}只</p>",
            "<hr/>",
        ]
    else:
        lines = [
            f"<h2>{ICON} ⚡ 圣杯选股结果 ⚡</h2>",
            f"<p><b>时间：</b>{run_time}</p>",
            "<hr/>",
        ]

    if not signals or (isinstance(signals, list) and len(signals) == 0):
        lines.append("<p style='color:#e67e22'>⚠️ 今日无圣杯信号，建议空仓观望。</p>")
        return "\n".join(lines)

    if isinstance(signals, list):
        lines.append("<h3>🎯 买入信号</h3>")
        lines.append("<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse;width:100%'>")
        lines.append("<tr style='background:#f0f0f0'><th>级别</th><th>代码</th><th>名称</th><th>评分</th><th>现价</th><th>建议仓位</th></tr>")

        for s in signals[:15]:
            level = s.get("level", s.get("grade", "?"))
            code = s.get("code", "?")
            name = s.get("name", "?")
            score = s.get("hg_score", s.get("score", "?"))
            price = s.get("price", s.get("close", "?"))
            position = s.get("position_pct", s.get("position", "?"))

            # Color by level
            if level in ("SSS",):
                bg = "#fff3cd"; emoji = "👑"
            elif level in ("SS",):
                bg = "#ffeaa7"; emoji = "⭐"
            elif level in ("S",):
                bg = "#dfe6e9"; emoji = "📈"
            else:
                bg = "#fff"; emoji = "📊"

            lines.append(
                f"<tr style='background:{bg}'><td>{emoji} {level}</td><td><b>{code}</b></td>"
                f"<td>{name}</td><td>{score}</td><td>{price}</td><td>{position}%</td></tr>"
            )
        lines.append("</table>")

    # Action hint
    lines.extend([
        "<hr/>",
        "<p style='color:#e74c3c;font-size:13px'>⏰ <b>操作窗口：14:30-14:57</b> | 务必在收盘前3分钟完成建仓</p>",
        "<p style='color:#888;font-size:11px'>圣杯策略：T日14:30尾盘买入 → T+1日上涨/涨停 → 启动连续上涨趋势</p>",
    ])

    return "\n".join(lines)


def format_guardian_alert(data: Dict) -> str:
    """Guardian 异常告警。"""
    status = data.get("status", "OK")
    if status == "OK":
        return ""  # No push for normal guardians

    pct = data.get("completion_pct", 0)
    checks = data.get("checks", {})

    lines = [
        f"<h2>🚨 V13.4 Guardian 告警</h2>",
        f"<p><b>状态：</b>{status} | <b>完成率：</b>{pct}%</p>",
        "<hr/>",
        "<h3>流水线状态</h3>",
        "<ul>",
    ]
    for step, state in checks.items():
        emoji = "✅" if state == "completed" else "❌"
        lines.append(f"<li>{emoji} {step}: {state}</li>")
    lines.append("</ul>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="V13.4 Cloud Notifier")
    ap.add_argument("--step", required=True, choices=["T4", "T5", "GUARDIAN", "TEST", "BATTLE"],
                    help="Pipeline step to notify about")
    ap.add_argument("--data-file", default=None, help="JSON file with step output")
    ap.add_argument("--message", default=None, help="Direct message (for TEST)")
    args = ap.parse_args()

    if not PUSHPLUS_TOKEN and not SERVERCHAN_KEY:
        print("[ERROR] No push channel configured. Set PUSHPLUS_TOKEN or SERVERCHAN_KEY env.")
        sys.exit(1)

    # Load data
    data = {}
    if args.data_file:
        data_path = Path(args.data_file)
        if data_path.exists():
            with open(data_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            print(f"[WARN] Data file not found: {args.data_file}")
            data = {"error": "data_file_not_found"}

    # Format and push
    title, content = "", ""

    if args.step == "TEST":
        title = f"{ICON} V13.4 云端推送测试"
        content = args.message or "<h3>✅ 推送通道正常</h3><p>云端 GitHub Actions → PushPlus → 微信 全链路OK</p>"

    elif args.step == "T4":
        title = f"{ICON} V13.4 T4 尾盘候选确认"
        content = format_t4_notification(data)

    elif args.step == "T5":
        # Try to load from holy grail signals file
        hg_path = Path("cloud_outputs/holy_grail_signals.json")
        if hg_path.exists() and not data:
            with open(hg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        title = f"{ICON} ⚡ V13.4 圣杯选股信号 ⚡"
        content = format_t5_notification(data)

    elif args.step == "BATTLE":
        title = f"{ICON} V13.4 明日作战计划"
        items = data.get("watchlist", [])
        content = f"<h2>📋 明日盯盘清单</h2><p>共 {len(items)} 只</p><hr/>"
        for i, s in enumerate(items, 1):
            content += f"<p>{i}. <b>{s.get('code','?')}</b> {s.get('name','?')} | 评分: {s.get('score','?')}</p>"

    elif args.step == "GUARDIAN":
        title = f"🚨 V13.4 Guardian 告警"
        content = format_guardian_alert(data)
        if not content:
            print("[GUARDIAN] Status OK, skipping push.")
            sys.exit(0)

    print(f"\n{'='*50}")
    print(f"  PUSH: {args.step}")
    print(f"  Title: {title}")
    print(f"  Content: {content[:100]}...")
    print(f"{'='*50}\n")

    success = push(title, content)
    sys.exit(0 if success else 1)
