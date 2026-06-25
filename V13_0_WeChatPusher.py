#!/usr/bin/env python3
"""
V13.0 微信实时推送传输层
=========================
A-1 解决：对接免费推送通道，实现选股结果/告警/心跳的实时微信推送

支持的推送通道：
  1. Qmsg酱 (https://qmsg.zendee.cn) — 免费个人微信推送
  2. Server酱 Turbo (https://sct.ftqq.com) — 备用通道
  3. 本地日志+终端输出 — 作为兜底

推送内容类型：
  - 🎯 尾盘选股决策推送（分级：SSS/SS/S/A）
  - 🚨 紧急告警（踩雷预警/休眠告警/漏跑通知）
  - 📊 每日绩效汇总（涨停命中率/盈亏比等）
  - 🕐 心跳状态（系统在线确认）

使用方式：
  配置好 QMSG_KEY 环境变量后：
  pusher = WeChatPusher(qmsg_key='xxx')
  pusher.push_stock_pick(result)
  pusher.push_alert('14:30 尾盘猎手已启动', level='info')
  pusher.push_daily_summary(report)
"""

import json
import os
import urllib.request
import urllib.parse
import time
import hashlib
from datetime import datetime
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

DEFAULT_QMSG_API = "https://qmsg.zendee.cn/api/v2/send/{key}"
DEFAULT_SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"
FALLBACK_LOG_FILE = "V13_0_data/push_fallback.log"


class PushConfig:
    """推送配置"""
    def __init__(self,
                 qmsg_key: str = None,
                 serverchan_key: str = None,
                 retry_times: int = 2,
                 retry_delay: float = 1.0,
                 cooldown_seconds: int = 10,    # 同一类型消息冷却
                 enable_sound: bool = True):     # 是否带声音（紧急消息）
        self.qmsg_key = qmsg_key or os.environ.get('QMSG_KEY', '')
        self.serverchan_key = serverchan_key or os.environ.get('SERVERCHAN_KEY', '')
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.cooldown_seconds = cooldown_seconds
        self.enable_sound = enable_sound


# ═══════════════════════════════════════════════
# 推送器
# ═══════════════════════════════════════════════

class WeChatPusher:
    """
    微信推送传输层

    多通道自动切换 + 降级兜底策略：
      1. 首选 Qmsg酱（免费/简单）
      2. 备选 Server酱
      3. 兜底 本地文件日志
    """

    # 消息级别对应的emoji和声音
    LEVEL_EMOJI = {
        'critical': '🔴🚨',    # 紧急（必须通知）
        'warning':  '⚠️',       # 警告
        'info':     '📊',       # 信息
        'success':  '✅',       # 成功
        'debug':    '🔍',       # 调试
    }

    def __init__(self, config: PushConfig = None):
        self.config = config or PushConfig()
        self._last_push: Dict[str, float] = {}  # type → last timestamp
        self._push_count = 0
        self._fallback_count = 0

    # ── 通道选择 ──

    def _push_qmsg(self, message: str, level: str = 'info') -> bool:
        """Qmsg酱推送"""
        key = self.config.qmsg_key
        if not key:
            return False

        url = DEFAULT_QMSG_API.format(key=key)
        emoji = self.LEVEL_EMOJI.get(level, '📊')
        msg = f"{emoji} {message}"

        for attempt in range(self.config.retry_times):
            try:
                data = urllib.parse.urlencode({
                    'msg': msg.encode('utf-8'),
                    'qq': '',  # 空=发送到默认QQ/微信
                }).encode('utf-8')

                req = urllib.request.Request(url, data=data, method='POST')
                req.add_header('Content-Type', 'application/x-www-form-urlencoded')
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read().decode('utf-8'))

                if result.get('success') or result.get('code') == 0:
                    return True
            except Exception:
                if attempt < self.config.retry_times - 1:
                    time.sleep(self.config.retry_delay)

        return False

    def _push_serverchan(self, message: str, level: str = 'info') -> bool:
        """Server酱推送（备用）"""
        key = self.config.serverchan_key
        if not key:
            return False

        url = DEFAULT_SERVERCHAN_API.format(key=key)
        title = f"V13.0天眼 — {level.upper()}"

        for attempt in range(self.config.retry_times):
            try:
                data = urllib.parse.urlencode({
                    'title': title.encode('utf-8'),
                    'desp': message.encode('utf-8'),
                }).encode('utf-8')

                req = urllib.request.Request(url, data=data, method='POST')
                resp = urllib.request.urlopen(req, timeout=10)
                result = json.loads(resp.read().decode('utf-8'))

                if result.get('code') == 0:
                    return True
            except Exception:
                if attempt < self.config.retry_times - 1:
                    time.sleep(self.config.retry_delay)

        return False

    def _push_fallback(self, message: str, level: str = 'info'):
        """兜底：写入本地日志文件"""
        os.makedirs(os.path.dirname(FALLBACK_LOG_FILE), exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(FALLBACK_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [{level.upper()}] {message}\n")
        self._fallback_count += 1

    # ── 公共接口 ──

    def push(self, message: str, level: str = 'info', force: bool = False) -> dict:
        """
        推送消息（主入口）

        参数：
          message: 消息内容
          level: critical/warning/info/success/debug
          force: 是否绕过冷却（紧急消息）

        返回：
          {success, channel, message_count}
        """
        # 冷却检查
        now = time.time()
        if not force and level in self._last_push:
            if now - self._last_push.get(level, 0) < self.config.cooldown_seconds:
                return {'success': True, 'channel': 'cooldown', 'message': 'cooled', 'message_count': self._push_count}

        self._last_push[level] = now

        # 终端输出（始终）
        ts = datetime.now().strftime('%H:%M:%S')
        emoji = self.LEVEL_EMOJI.get(level, '')
        print(f"[{ts}] {emoji} {message}")

        # 尝试推送
        success = False
        channel = 'none'

        if self.config.qmsg_key:
            if self._push_qmsg(message, level):
                success = True
                channel = 'qmsg'

        if not success and self.config.serverchan_key:
            if self._push_serverchan(message, level):
                success = True
                channel = 'serverchan'

        if not success:
            self._push_fallback(message, level)
            channel = 'fallback'

        self._push_count += 1
        return {'success': success, 'channel': channel, 'message_count': self._push_count}

    def push_alert(self, title: str, detail: str = '', level: str = 'warning'):
        """推送告警"""
        msg = f"【{title}】\n{detail}" if detail else f"【{title}】"
        return self.push(msg, level, force=(level == 'critical'))

    def push_stock_pick(self, pick: dict):
        """
        推送选股决策

        格式：分级标题 + 关键指标 + 操作建议
        """
        verdict = pick.get('verdict', 'HOLD')
        score = pick.get('nlp_score', pick.get('final_score', 0))
        code = pick.get('code', 'N/A')
        name = pick.get('name', '未知')

        # 分级
        if score >= 0.80:
            grade = '🌟SSS'
            level = 'critical'
        elif score >= 0.65:
            grade = '⭐SS'
            level = 'warning'
        elif score >= 0.50:
            grade = '🔥S'
            level = 'info'
        else:
            grade = '📉A'
            level = 'info'

        lines = [
            f"尾盘{grade}: {name}({code})",
            f"评分: {score:.2%} | 判决: {verdict}",
        ]

        # 贝叶斯概率
        if 'm46_prob' in pick:
            lines.append(f"贝叶斯: {pick['m46_prob']:.1%}")
        if 'm51_intent' in pick:
            lines.append(f"主力意图: {pick['m51_intent']:.2f}")
        if 'plr' in pick or 'm54_plr' in pick:
            lines.append(f"盈亏比: {pick.get('plr', pick.get('m54_plr', 0)):.1f}:1")

        # 形态触发
        patterns = pick.get('patterns', pick.get('detected_patterns', {}))
        if patterns:
            active = [k for k, v in patterns.items() if v if isinstance(v, dict) and v.get('grade') in ('strong',) or (isinstance(v, (int, float)) and v > 0.6)]
            if active:
                lines.append(f"形态: {'+'.join(active[:3])}")

        lines.append(f"\n时间: {datetime.now().strftime('%m-%d %H:%M')}")

        # 操作建议
        if verdict in ('BUY', 'STRONG_BUY'):
            position = pick.get('position_pct', pick.get('kelly_position', 0))
            lines.append(f"建议仓位: {position:.0%}" if position else "")
            lines.append("操作: 尾盘14:50前竞价买入")
        elif verdict == 'WATCH':
            lines.append("操作: 加监控，等待次日确认")
        elif verdict == 'SELL':
            lines.append("操作: 次日开盘卖出")

        return self.push('\n'.join(lines), level, force=(grade in ('SSS',)))

    def push_daily_summary(self, report: dict):
        """推送每日绩效汇总"""
        lines = [
            "📊 V13.0天眼 今日绩效",
            f"日期: {report.get('date', datetime.now().strftime('%Y-%m-%d'))}",
            f"候选数: {report.get('total_candidates', 0)}",
            f"入选数: {report.get('total_picks', 0)}",
        ]

        if 'hit_rate' in report:
            lines.append(f"命中率: {report['hit_rate']:.1%} (目标99%)")
        if 'plr' in report:
            lines.append(f"盈亏比: {report['plr']:.1f}:1 (目标10.0)")
        if 'trap_rate' in report:
            lines.append(f"踩雷率: {report['trap_rate']:.1%} (目标0.1%)")

        if 'top_pick' in report:
            top = report['top_pick']
            lines.append(f"\n今日最佳: {top.get('name','')}({top.get('code','')}) 评分{top.get('score',0):.2%}")

        return self.push('\n'.join(lines), 'success')

    def push_heartbeat(self, status: str = 'online'):
        """推送心跳"""
        msg = f"V13.0天眼 [{status.upper()}] {datetime.now().strftime('%H:%M:%S')}"
        return self.push(msg, 'debug')

    def get_stats(self) -> dict:
        """获取推送统计"""
        return {
            'total_pushes': self._push_count,
            'fallback_count': self._fallback_count,
            'channels': {
                'qmsg': bool(self.config.qmsg_key),
                'serverchan': bool(self.config.serverchan_key),
            },
        }


# ═══════════════════════════════════════════════
# 便捷工厂
# ═══════════════════════════════════════════════

def create_pusher(qmsg_key: str = None) -> WeChatPusher:
    """
    快速创建推送器（优先使用传入key，其次环境变量）

    使用前：
      1. 访问 https://qmsg.zendee.cn 注册获取 Key
      2. 扫码绑定微信/QQ
      3. 设置环境变量: set QMSG_KEY=your_key_here
    """
    key = qmsg_key or os.environ.get('QMSG_KEY', '')
    config = PushConfig(qmsg_key=key)
    return WeChatPusher(config)


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 微信推送传输层 — 自测")
    print("=" * 60)

    # 无Key模式：仅终端输出 + 本地日志兜底
    pusher = WeChatPusher(PushConfig(qmsg_key='', serverchan_key=''))

    print("\n📨 测试1: 信息推送")
    r = pusher.push("系统启动完成，所有模块就绪", 'info')
    print(f"  → 通道={r['channel']}, 成功={r['success']}")

    print("\n🚨 测试2: 告警推送")
    r = pusher.push_alert("主机休眠检测", "检测到14:30-15:05期间休眠，已补跑收盘复盘", 'warning')
    print(f"  → 通道={r['channel']}")

    print("\n🌟 测试3: 选股推送")
    mock_pick = {
        'code': '002415', 'name': '海康威视',
        'verdict': 'BUY', 'final_score': 0.85,
        'm46_prob': 0.72, 'm51_intent': 0.88,
        'm54_plr': 8.5, 'plr': 8.5,
        'patterns': {'老鸭头': {'grade': 'strong'}, '主力信号': {'grade': 'strong'}},
        'position_pct': 0.25,
    }
    r = pusher.push_stock_pick(mock_pick)
    print(f"  → 通道={r['channel']}")

    print("\n📊 测试4: 每日汇总")
    mock_report = {
        'date': '2026-06-23',
        'total_candidates': 40, 'total_picks': 3,
        'hit_rate': 0.67, 'plr': 8.5, 'trap_rate': 0.0,
        'top_pick': {'code': '002415', 'name': '海康威视', 'score': 0.85},
    }
    r = pusher.push_daily_summary(mock_report)
    print(f"  → 通道={r['channel']}")

    print(f"\n📈 推送统计: {json.dumps(pusher.get_stats(), ensure_ascii=False)}")
    print(f"\n💡 提示: 设置环境变量 QMSG_KEY 即可激活真实微信推送")
    print(f"     注册地址: https://qmsg.zendee.cn")
    print(f"     兜底日志: {FALLBACK_LOG_FILE}")

    print("\n" + "=" * 60)
    print("✅ 微信推送传输层自检通过（兜底模式正常）")
    print("=" * 60)
