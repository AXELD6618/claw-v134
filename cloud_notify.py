#!/usr/bin/env python3
"""
V13.4 Cloud Notifier — PushPlus 云端微信推送 (V13.4.2 修复版)
====================================================================
修复内容：
1. 增加详细调试日志（写入文件 + 打印）
2. 修复编码问题（强制UTF-8，避免Shell乱码）
3. 增加纯文本降级格式（HTML失败时自动降级）
4. 增加推送前内容校验
5. 增加 --debug 参数，输出原始推送内容到文件

使用方式：
  python cloud_notify.py --step=T5 --data-file=cloud_outputs/holy_grail_signals.json
  python cloud_notify.py --step=TEST --message="测试消息"
  python cloud_notify.py --step=T5 --debug  # 输出调试日志
"""

import os, sys, json, tempfile, subprocess, urllib.request, urllib.error, urllib.parse
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

# Debug mode
DEBUG = os.environ.get("DEBUG", "0") == "1"

# ═══════════════════════════════════════════════
# 调试日志
# ═══════════════════════════════════════════════

def log(msg: str, level: str = "INFO"):
    """输出日志到 stdout + 调试文件"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {msg}"
    print(line, flush=True)
    
    # 写入调试文件
    if DEBUG:
        debug_file = Path("cloud_outputs/notify_debug.log")
        try:
            with open(debug_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def write_debug_content(step: str, title: str, content: str):
    """将推送内容写入调试文件，方便检查"""
    if not DEBUG:
        return
    debug_dir = Path("cloud_outputs/debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = debug_dir / f"notify_{step}_{timestamp}.txt"
    
    try:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"Title: {title}\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"{'='*60}\n\n")
            f.write(content)
        log(f"Debug content written to {filename}", "DEBUG")
    except Exception as e:
        log(f"Failed to write debug content: {e}", "WARN")


# ═══════════════════════════════════════════════
# 推送函数
# ═══════════════════════════════════════════════

def push_pushplus(token: str, title: str, content: str, template: str = "html") -> bool:
    """通过PushPlus推送到微信（urllib优先，curl @file fallback）。"""
    log(f"Sending PushPlus notification: {title[:30]}...")
    log(f"Content length: {len(content)} chars")
    
    # 验证内容不为空
    if not content or not content.strip():
        log("ERROR: Content is empty!", "ERROR")
        return False
    
    payload = json.dumps({
        "token": token,
        "title": title,
        "content": content,
        "template": template
    }, ensure_ascii=False).encode("utf-8")
    
    log(f"Payload size: {len(payload)} bytes")
    
    # ---- 方法1: urllib (原生 UTF-8) ----
    try:
        req = urllib.request.Request(
            PUSHPLUS_API,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json; charset=utf-8",
                "User-Agent": "V13.4-Cloud/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            log(f"PushPlus response: {body[:200]}")
            result = json.loads(body)
            success = result.get("code") == 200
            if success:
                log(f"PushPlus urllib: OK - {result.get('msg', '')}", "OK")
                return True
            else:
                log(f"PushPlus urllib: FAIL - {result.get('msg', body[:200])}", "ERROR")
    except Exception as e:
        log(f"PushPlus urllib ERROR: {e}", "ERROR")

    # ---- 方法2: curl @file (避免 shell 编码问题) ----
    log("Trying curl fallback...")
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".json", delete=False
        ) as f:
            f.write(payload)
            tmpfile = f.name

        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST", PUSHPLUS_API,
                "-H", "Content-Type: application/json; charset=utf-8",
                "-d", f"@{tmpfile}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        os.unlink(tmpfile)

        log(f"PushPlus curl response: {result.stdout[:200]}")
        resp = json.loads(result.stdout)
        success = resp.get("code") == 200
        if success:
            log(f"PushPlus curl@file: OK - {resp.get('msg', '')}", "OK")
            return True
        else:
            log(f"PushPlus curl@file: FAIL - {resp.get('msg', result.stdout[:200])}", "ERROR")
    except Exception as e:
        log(f"PushPlus curl@file ERROR: {e}", "ERROR")
        return False

    return False


def push_serverchan(key: str, title: str, content: str) -> bool:
    """通过Server酱推送到微信（备用通道）。"""
    log(f"Sending Server酱 notification: {title[:30]}...")
    
    if not content or not content.strip():
        log("ERROR: Content is empty for Server酱!", "ERROR")
        return False
    
    data = urllib.parse.urlencode({
        "title": title,
        "desp": content,
    }).encode("utf-8")
    url = SERVERCHAN_API.format(key=key)
    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            log(f"Server酱 response: {body[:200]}")
            result = json.loads(body)
            success = result.get("code") == 0
            if success:
                log(f"Server酱: OK", "OK")
            else:
                log(f"Server酱: FAIL - {result.get('message', '?')}", "ERROR")
            return success
    except Exception as e:
        log(f"Server酱 ERROR: {e}", "ERROR")
        return False


def push(title: str, content: str, template: str = "html") -> bool:
    """统一推送入口，PushPlus优先，失败自动切Server酱。"""
    log(f"=== push() called ===")
    log(f"Title: {title}")
    log(f"Template: {template}")
    log(f"Content preview: {content[:100]}...")
    
    if not content or not content.strip():
        log("ERROR: Content is empty in push()!", "ERROR")
        return False
    
    if PUSHPLUS_TOKEN:
        log("Trying PushPlus...", "INFO")
        if push_pushplus(PUSHPLUS_TOKEN, title, content, template):
            return True
        log("PushPlus failed, trying Server酱...", "WARN")
    else:
        log("No PUSHPLUS_TOKEN configured", "WARN")

    if SERVERCHAN_KEY:
        log("Trying Server酱...", "INFO")
        if push_serverchan(SERVERCHAN_KEY, title, content):
            return True
    else:
        log("No SERVERCHAN_KEY configured", "WARN")

    log("ERROR: All push channels failed!", "ERROR")
    return False


# ═══════════════════════════════════════════════
# 消息格式化（纯文本版本，避免编码问题）
# ═══════════════════════════════════════════════

ICON = "✅🔥💎"

def format_t4_notification_plain(data: Dict) -> str:
    """T4 最终确认候选池通知（纯文本）。"""
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
        f"{ICON} V13.4 T4 最终确认",
        "=" * 40,
        f"时间：{now}",
        f"候选数：{count}只",
        "",
        "─" * 40,
    ]
    
    if items:
        lines.append("🏆 顶级候选 (Top 10)")
        lines.append("-" * 40)
        for i, s in enumerate(items, 1):
            code = s.get("code", "?")
            name = s.get("name", "?")
            score = s.get("score", s.get("cloud_score", "?"))
            pct = s.get("pct", s.get("pct_chg", "?"))
            turnover = s.get("turnover", "?")
            lines.append(f"{i}. {code} {name} | 评分:{score} | 涨幅:{pct}% | 换手:{turnover}%")
    
    lines.append("")
    lines.append("⏰ T5 圣杯选股将在14:30执行，请关注后续推送。")
    
    return "\n".join(lines)


def format_t5_notification_plain(data: Dict) -> str:
    """T5 圣杯选股结果推送（纯文本）。"""
    signals = data.get("signals", data.get("list", []))
    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        f"{ICON} ⚡ 圣杯选股结果 ⚡",
        "=" * 40,
        f"时间：{run_time}",
    ]
    
    if not signals or (isinstance(signals, list) and len(signals) == 0):
        lines.append("")
        lines.append("⚠️ 今日无圣杯信号，建议空仓观望。")
        lines.append("")
        lines.append("─" * 40)
        lines.append("圣杯策略：T日14:30尾盘买入 → T+1日上涨/涨停 → 启动连续上涨趋势")
        return "\n".join(lines)
    
    if isinstance(signals, list):
        ss_count = sum(1 for s in signals if s.get("level", s.get("grade", "")) in ("SSS", "SS"))
        lines.append(f"总信号：{len(signals)}只 | 强信号(SS+)：{ss_count}只")
        lines.append("")
        lines.append("─" * 40)
        lines.append("🎯 买入信号")
        lines.append("-" * 40)
        
        for s in signals[:15]:
            level = s.get("level", s.get("grade", "?"))
            code = s.get("code", "?")
            name = s.get("name", "?")
            score = s.get("hg_score", s.get("score", "?"))
            price = s.get("price", s.get("close", "?"))
            position = s.get("position_pct", s.get("position", "?"))
            
            # Level emoji
            if level in ("SSS",):
                emoji = "👑"
            elif level in ("SS",):
                emoji = "⭐"
            elif level in ("S",):
                emoji = "📈"
            else:
                emoji = "📊"
                
            lines.append(f"{emoji} {level} | {code} {name} | 评分:{score} | 现价:{price} | 仓位:{position}%")
    
    lines.append("")
    lines.append("─" * 40)
    lines.append("⏰ 操作窗口：14:30-14:57 | 务必在收盘前3分钟完成建仓")
    lines.append("圣杯策略：T日14:30尾盘买入 → T+1日上涨/涨停 → 启动连续上涨趋势")
    
    return "\n".join(lines)


def format_guardian_alert_plain(data: Dict) -> str:
    """Guardian 异常告警（纯文本）。"""
    status = data.get("status", "OK")
    if status == "OK":
        return ""  # No push for normal guardians
    
    pct = data.get("completion_pct", 0)
    checks = data.get("checks", {})
    
    lines = [
        "🚨 V13.4 Guardian 告警",
        "=" * 40,
        f"状态：{status}",
        f"完成率：{pct}%",
        "",
        "─" * 40,
        "流水线状态：",
    ]
    
    for step, state in checks.items():
        emoji = "✅" if state == "completed" else "❌"
        lines.append(f"  {emoji} {step}: {state}")
    
    lines.append("")
    lines.append("─" * 40)
    lines.append("请登录 GitHub 查看详情。")
    
    return "\n".join(lines)


def format_generic_notification_plain(step: str, data: Dict) -> str:
    """通用步骤通知（纯文本）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    step_names = {
        "T0": "T0 全市场筛选",
        "T1": "T1 午盘刷新",
        "T3": "T3 深度分析",
        "NIGHT": "夜间分析",
    }
    name = step_names.get(step, step)
    
    lines = [
        f"{ICON} V13.4 {name}",
        "=" * 40,
        f"时间：{now}",
        "",
    ]
    
    if data:
        status = data.get("status", "unknown")
        count = data.get("count", data.get("total", 0))
        lines.append(f"状态：{'✅ 完成' if status == 'success' or status == 'ok' else status}")
        
        if count:
            lines.append(f"筛选数量：{count}")
        
        # Show top items if available
        items = data.get("list", data.get("results", data.get("signals", [])))
        if isinstance(items, list) and items:
            lines.append("")
            lines.append("部分结果：")
            for item in items[:5]:
                code = item.get("code", "?")
                sname = item.get("name", "?")
                score = item.get("score", "?")
                lines.append(f"  • {code} {sname} — 评分: {score}")
    
    lines.append("")
    lines.append("─" * 40)
    lines.append("毕方灵犀·天眼 V13.4 云端自动运行")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# HTML 格式版本（保留，但增加错误处理）
# ═══════════════════════════════════════════════

def format_t4_notification_html(data: Dict) -> str:
    """T4 最终确认候选池通知（HTML）。"""
    try:
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
    except Exception as e:
        log(f"HTML format error: {e}", "ERROR")
        return format_t4_notification_plain(data)


def format_t5_notification_html(data: Dict) -> str:
    """T5 圣杯选股结果推送（HTML）。"""
    try:
        signals = data.get("signals", data.get("list", []))
        run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
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
    except Exception as e:
        log(f"HTML format error: {e}", "ERROR")
        return format_t5_notification_plain(data)


# ═══════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    
    ap = argparse.ArgumentParser(description="V13.4 Cloud Notifier (V13.4.2)")
    ap.add_argument("--step", required=True,
                    choices=["T0", "T1", "T3", "T4", "T5", "GUARDIAN", "TEST", "BATTLE", "NIGHT"],
                    help="Pipeline step to notify about")
    ap.add_argument("--data-file", default=None, help="JSON file with step output")
    ap.add_argument("--message", default=None, help="Direct message (for TEST)")
    ap.add_argument("--format", default="html", choices=["html", "plain"],
                    help="Notification format: html (default) or plain (text)")
    ap.add_argument("--debug", action="store_true",
                    help="Enable debug mode (write logs to file)")
    args = ap.parse_args()
    
    # Enable debug mode
    if args.debug:
        os.environ["DEBUG"] = "1"
        DEBUG = True
        log("Debug mode enabled", "DEBUG")
    
    log(f"=== V13.4 Cloud Notifier ===")
    log(f"Step: {args.step}")
    log(f"Data file: {args.data_file}")
    log(f"Format: {args.format}")
    
    if not PUSHPLUS_TOKEN and not SERVERCHAN_KEY:
        log("ERROR: No push channel configured. Set PUSHPLUS_TOKEN or SERVERCHAN_KEY env.", "ERROR")
        sys.exit(1)
    
    # Load data
    data = {}
    if args.data_file:
        data_path = Path(args.data_file)
        if data_path.exists():
            try:
                with open(data_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log(f"Data loaded from {args.data_file}: {len(str(data))} bytes", "OK")
            except Exception as e:
                log(f"ERROR loading data file: {e}", "ERROR")
                data = {"error": f"load_failed: {e}"}
        else:
            log(f"WARN: Data file not found: {args.data_file}", "WARN")
            # Try to load from default location
            if args.step == "T5":
                hg_path = Path("cloud_outputs/holy_grail_signals.json")
                if hg_path.exists():
                    try:
                        with open(hg_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        log(f"Data loaded from default location: {hg_path}", "OK")
                    except Exception as e:
                        log(f"ERROR loading default data: {e}", "ERROR")
    
    log(f"Data keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
    
    # Format and push
    title, content = "", ""
    use_plain = (args.format == "plain")
    
    if args.step == "TEST":
        title = f"{ICON} V13.4 云端推送测试"
        content = args.message or "✅ 推送通道正常\n\n云端 GitHub Actions → PushPlus → 微信 全链路OK"
        if use_plain:
            content = args.message or "✅ 推送通道正常\n\n云端 GitHub Actions → PushPlus → 微信 全链路OK"
        
    elif args.step == "T4":
        title = f"{ICON} V13.4 T4 尾盘候选确认"
        if use_plain:
            content = format_t4_notification_plain(data)
        else:
            content = format_t4_notification_html(data)
            
    elif args.step == "T5":
        title = f"{ICON} ⚡ V13.4 圣杯选股信号 ⚡"
        if use_plain:
            content = format_t5_notification_plain(data)
        else:
            content = format_t5_notification_html(data)
            
    elif args.step == "BATTLE":
        title = f"{ICON} V13.4 明日作战计划"
        items = data.get("watchlist", [])
        if use_plain:
            content = f"📋 明日盯盘清单\n{'='*40}\n共 {len(items)} 只\n\n"
            for i, s in enumerate(items, 1):
                content += f"{i}. {s.get('code','?')} {s.get('name','?')} | 评分: {s.get('score','?')}\n"
        else:
            content = f"<h2>📋 明日盯盘清单</h2><p>共 {len(items)} 只</p><hr/>"
            for i, s in enumerate(items, 1):
                content += f"<p>{i}. <b>{s.get('code','?')}</b> {s.get('name','?')} | 评分: {s.get('score','?')}</p>"
            
    elif args.step == "GUARDIAN":
        title = "🚨 V13.4 Guardian 告警"
        if use_plain:
            content = format_guardian_alert_plain(data)
        else:
            content = format_guardian_alert_html(data) if 'format_guardian_alert_html' in dir() else format_guardian_alert_plain(data)
        if not content:
            log("Guardian status OK, skipping push.", "INFO")
            sys.exit(0)
            
    else:
        # T0, T1, T3, NIGHT — generic notification
        step_names = {
            "T0": "T0 全市场筛选完成",
            "T1": "T1 午盘刷新完成",
            "T3": "T3 深度分析完成",
            "NIGHT": "夜间分析完成",
        }
        title = f"{ICON} V13.4 {step_names.get(args.step, args.step)}"
        if use_plain:
            content = format_generic_notification_plain(args.step, data)
        else:
            content = format_generic_notification_html(args.step, data) if 'format_generic_notification_html' in dir() else format_generic_notification_plain(args.step, data)
    
    # Validate content
    if not content or not content.strip():
        log("ERROR: Content is EMPTY after formatting!", "ERROR")
        log(f"Data: {json.dumps(data, ensure_ascii=False)[:200]}", "ERROR")
        sys.exit(1)
    
    log(f"=== Final Push ===")
    log(f"Title: {title}")
    log(f"Content length: {len(content)} chars")
    log(f"Content preview: {content[:100]}...")
    
    # Write debug content
    write_debug_content(args.step, title, content)
    
    # Print full content for GitHub Actions log
    print(f"\n{'='*50}")
    print(f"  PUSH CONTENT PREVIEW:")
    print(f"{'='*50}")
    print(content[:500])
    print(f"{'='*50}\n")
    
    success = push(title, content, template="html" if not use_plain else "txt")
    
    if success:
        log("Push completed successfully!", "OK")
    else:
        log("Push FAILED!", "ERROR")
        
    sys.exit(0 if success else 1)
