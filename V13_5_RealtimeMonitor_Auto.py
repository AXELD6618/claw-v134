#!/usr/bin/env python3
"""
V13.5.19 全市场实时监控器 - 自动化任务版本
==============================================
在交易时间（09:30-11:30 / 13:00-15:00）每5分钟扫描全市场，
实时预警蜀道装备模式股票（37维度五确认体系）。

执行方式: 由自动化任务调用（每5分钟执行一次）
核心调用: V13_5_ShuDao_Screener.py
"""

import sys
import json
import os
from datetime import datetime, time

sys.path.insert(0, ".")

from V13_5_ShuDao_Screener import scan_shudao_mode


def log(msg: str, level: str = 'INFO'):
    icons = {'INFO': 'ℹ️', 'SUCCESS': '✅', 'WARNING': '⚠️', 'ERROR': '❌', 'PROGRESS': '🔄'}
    icon = icons.get(level, 'ℹ️')
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {icon} {msg}")


def is_trading_time() -> bool:
    """判断当前是否为交易时间"""
    now = datetime.now().time()
    return (time(9, 30) <= now <= time(11, 30)) or (time(13, 0) <= now <= time(15, 0))


def save_alerts(alerts: list):
    """保存预警记录到 JSON 文件"""
    if not alerts:
        return

    alert_file = 'data/realtime_alerts.json'
    os.makedirs('data', exist_ok=True)

    existing = []
    if os.path.exists(alert_file):
        try:
            with open(alert_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []

    existing.extend(alerts)
    existing = existing[-100:]  # 只保留最近100条

    with open(alert_file, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    log(f"已保存 {len(alerts)} 条预警到 {alert_file}", 'INFO')


def main():
    """主函数"""
    log("=== V13.5.19 全市场实时监控器启动 (37维度五确认蜀道模式) ===", 'INFO')

    if not is_trading_time():
        log("非交易时间，跳过扫描", 'WARNING')
        return

    log("交易时间内，开始调用 V13_5_ShuDao_Screener 扫描...", 'PROGRESS')

    # 轻量扫描：limit=20, max_eval=10（控制积分消耗）
    result = scan_shudao_mode(limit=20, max_eval=10)

    candidates = result.get('candidates', [])
    five_confirm_candidates = result.get('five_confirm_candidates', [])

    # 生成实时预警记录
    alerts = []
    for c in candidates[:5]:
        alerts.append({
            'code': c.get('code'),
            'name': c.get('name'),
            'price': c.get('price'),
            'chg_pct': c.get('chg_pct'),
            'five_confirm_count': c.get('five_confirm_count'),
            'd34_score': c.get('d34_score'),
            'd29_score': c.get('d29_score'),
            'd31_score': c.get('d31_score'),
            'grade': c.get('grade'),
            'pattern': 'shudao',
            'timestamp': datetime.now().isoformat(),
        })

    if alerts:
        save_alerts(alerts)
        log(f"🎯 发现 {len(five_confirm_candidates)} 只五确认/四确认候选，已保存 {len(alerts)} 条预警！", 'SUCCESS')
    else:
        log("未发现新模式股票", 'INFO')

    log("=== 扫描完成 ===", 'INFO')


if __name__ == '__main__':
    main()
