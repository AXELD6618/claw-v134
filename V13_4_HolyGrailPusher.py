#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.4 圣杯推送集成器
==================
解决 T5 14:30 终极圣杯选股结果 → 微信/小程序 → 全市场盯盘仪表盘 的端到端推送链路。

核心职责：
  1. 读取 T5 产生的 holy_grail_signals.json
  2. 通过 Qmsg酱 / Server酱 推送 TOP N 圣杯信号到微信
  3. 触发 deploy/data 即时同步（保证仪表盘 14:50 前刷新）
  4. 提供小程序推送占位接口（待接入真实订阅消息）
  5. 全程日志 + 降级兜底

调用方式：
  from V13_4_HolyGrailPusher import HolyGrailPusher
  pusher = HolyGrailPusher()
  pusher.push_t5_results(date_str='20260629', period='14:30')
"""

import json
import os
import sys
import traceback
import urllib.request
import urllib.parse
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

BASE = Path(r"E:/WorkBuddy_dot_workbuddy/Claw")
CONFIG_PATH = BASE / "data/push_config.json"
PIPELINE_DIR = BASE / "data/pipeline"
CACHE_DIR = BASE / "data/fullmarket_cache"
DEPLOY_DIR = BASE / "deploy"
DEPLOY_DATA = DEPLOY_DIR / "data"
FALLBACK_LOG = BASE / "V13_0_data/push_fallback.log"

DEFAULT_QMSG_API = "https://qmsg.zendee.cn/api/v2/send/{key}"
DEFAULT_SERVERCHAN_API = "https://sctapi.ftqq.com/{key}.send"


class PushChannel:
    """推送通道封装（Qmsg / Server酱）"""
    def __init__(self, config: dict):
        self.cfg = config
        self.qmsg_key = config.get('qmsg_key', '')
        self.serverchan_key = config.get('serverchan_key', '')
        self.retry_times = config.get('retry_times', 2)
        self.retry_delay = config.get('retry_delay', 1.0)
        self.enable_push = config.get('enable_push', True)

    def _post(self, url: str, data: bytes, timeout: int = 10) -> dict:
        try:
            req = urllib.request.Request(url, data=data, method='POST')
            req.add_header('Content-Type', 'application/x-www-form-urlencoded')
            resp = urllib.request.urlopen(req, timeout=timeout)
            return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def push_qmsg(self, message: str) -> bool:
        if not self.qmsg_key:
            return False
        url = DEFAULT_QMSG_API.format(key=self.qmsg_key)
        encoded = urllib.parse.urlencode({'msg': message.encode('utf-8'), 'qq': ''}).encode('utf-8')
        for attempt in range(self.retry_times):
            result = self._post(url, encoded)
            if result.get('success') or result.get('code') == 0:
                return True
            if attempt < self.retry_times - 1:
                time.sleep(self.retry_delay)
        return False

    def push_serverchan(self, title: str, message: str) -> bool:
        if not self.serverchan_key:
            return False
        url = DEFAULT_SERVERCHAN_API.format(key=self.serverchan_key)
        encoded = urllib.parse.urlencode({
            'title': title.encode('utf-8'),
            'desp': message.encode('utf-8')
        }).encode('utf-8')
        for attempt in range(self.retry_times):
            result = self._post(url, encoded)
            if result.get('code') == 0:
                return True
            if attempt < self.retry_times - 1:
                time.sleep(self.retry_delay)
        return False

    def send(self, title: str, message: str) -> dict:
        if not self.enable_push:
            return {'success': False, 'channel': 'disabled', 'reason': 'enable_push=false'}

        if not self.qmsg_key and not self.serverchan_key:
            return {'success': False, 'channel': 'none', 'reason': '未配置 QMSG_KEY 或 SERVERCHAN_KEY'}

        success = False
        channel = 'none'
        if self.qmsg_key:
            if self.push_qmsg(f"{title}\n{message}"):
                success = True
                channel = 'qmsg'
        if not success and self.serverchan_key:
            if self.push_serverchan(title, message):
                success = True
                channel = 'serverchan'
        if not success:
            return {'success': False, 'channel': 'none', 'reason': '所有通道推送失败（网络或key无效）'}
        return {'success': success, 'channel': channel}


class MiniProgramPusher:
    """
    微信小程序订阅消息推送占位实现。
    真实接入需：小程序后台申请 '财经资讯' 类目 + 用户授权订阅模板。
    """
    def __init__(self, config: dict):
        self.cfg = config.get('channels', {}).get('miniprogram', {})
        self.enabled = config.get('enable_miniprogram_push', False)

    def push(self, openid: str, template_id: str, data: dict, page: str = '') -> dict:
        if not self.enabled:
            return {'success': False, 'channel': 'miniprogram', 'reason': '未启用'}
        # TODO: 接入微信服务端 API:
        # POST https://api.weixin.qq.com/cgi-bin/message/subscribe/send?access_token=...
        return {
            'success': False,
            'channel': 'miniprogram',
            'reason': 'stub: 需接入真实小程序订阅消息服务',
            'hint': '在小程序后台开通订阅消息并配置 appid/app_secret/template_id'
        }


class HolyGrailPusher:
    """圣杯推送主控"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_PATH
        self.config = self._load_config()
        self.channel = PushChannel(self.config)
        self.mp = MiniProgramPusher(self.config)
        self.push_log: List[dict] = []

    def _load_config(self) -> dict:
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[PUSH] ⚠️ 配置文件解析失败: {e}，使用默认空配置")
        return {
            'qmsg_key': '', 'serverchan_key': '', 'enable_push': True,
            'push_top_n': 3, 'cooldown_seconds': 10
        }

    def _log(self, level: str, message: str, detail: dict = None):
        entry = {
            'time': datetime.now().strftime('%H:%M:%S'),
            'level': level,
            'message': message,
            'detail': detail or {}
        }
        self.push_log.append(entry)
        emoji = {'info': 'ℹ️', 'success': '✅', 'warning': '⚠️', 'error': '❌'}.get(level, 'ℹ️')
        print(f"[PUSH] {emoji} {message}")

    def _fallback_log(self, message: str):
        os.makedirs(os.path.dirname(FALLBACK_LOG), exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(FALLBACK_LOG, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [T5] {message}\n")

    def load_holy_grail_signals(self, date_str: str, period: str = '14:30') -> Optional[dict]:
        """读取T5产生的圣杯信号文件"""
        # 优先读 pipeline/holy_grail_signals.json
        primary = PIPELINE_DIR / 'holy_grail_signals.json'
        if primary.exists():
            try:
                with open(primary, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self._log('warning', f'读取 {primary} 失败: {e}')

        # 备选: cache/holygrail_candidates.json
        fallback = CACHE_DIR / 'holygrail_candidates.json'
        if fallback.exists():
            try:
                with open(fallback, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                self._log('warning', f'读取 {fallback} 失败: {e}')
        return None

    def _grade_signal(self, s: dict) -> str:
        """根据评分判定信号等级: SSS/SS/S/A"""
        score = s.get('v132_score', 0)
        score_pct = min(score, 1.0)  # V13.4 cumulative weight 可 >1
        if score_pct >= 0.80:
            return 'SSS'
        elif score_pct >= 0.65:
            return 'SS'
        elif score_pct >= 0.50:
            return 'S'
        else:
            return 'A'

    def _quality_filter(self, signals: List[dict]) -> List[dict]:
        """
        质量筛选: 只保留高成功率信号
        默认只推送 SSS + SS 级 (v132_score ≥ 0.65)
        可通过 push_config.json quality_filter 自定义
        """
        qf = self.config.get('quality_filter', {})
        min_score = qf.get('min_v132_score', 0.65)
        min_m46 = qf.get('min_m46_prob', 0.55)
        min_plr = qf.get('min_plr', 3.0)
        exclude_tiers = qf.get('exclude_tier', ['C', 'D'])
        min_grade = self.config.get('min_grade', 'SS')

        # grade hierarchy: SSS > SS > S > A
        grade_order = {'SSS': 4, 'SS': 3, 'S': 2, 'A': 1}
        min_grade_val = grade_order.get(min_grade, 3)

        filtered = []
        for s in signals:
            grade = self._grade_signal(s)
            grade_val = grade_order.get(grade, 1)
            tier = s.get('tier', '?')

            # 必须满足最低等级
            if grade_val < min_grade_val:
                continue
            # 必须满足最低评分
            if s.get('v132_score', 0) < min_score:
                continue
            # 排除低档tier
            if tier in exclude_tiers:
                continue
            # 可选: m46 贝叶斯概率门槛
            m46_prob = s.get('m46_prob', s.get('m46_normalized', 0))
            if m46_prob and m46_prob < min_m46:
                continue
            # 可选: 盈亏比门槛
            plr = s.get('plr', s.get('m54_plr', 0))
            if plr and plr < min_plr:
                continue

            filtered.append(s)

        # 按评分降序排列
        filtered.sort(key=lambda x: x.get('v132_score', 0), reverse=True)
        return filtered

    def build_message(self, signals: List[dict], date_str: str, period: str) -> tuple:
        """构造微信推送标题与正文 — 仅包含高成功率信号"""
        now = datetime.now().strftime('%m-%d %H:%M')
        total_raw = len(signals)

        # 质量筛选
        qualified = self._quality_filter(signals)
        total_qualified = len(qualified)
        top_n = min(self.config.get('push_top_n', 5), total_qualified)
        top = qualified[:top_n]

        if top_n == 0:
            title = f"🦅 T5圣杯 {now} | ⚠️ 今日无高成功率信号"
            lines = [
                f"⏰ 时段: {date_str} {period}",
                f"📊 总扫描信号: {total_raw} 只",
                f"⚠️ 高成功率(SSS/SS)信号: 0 只",
                f"原因: 普跌环境或评分未达≥0.65门槛",
                f"\n💡 建议: 今日观望，等待次日确认",
                "⚠️ 投资有风险，信号仅供参考"
            ]
            return title, '\n'.join(lines)

        title = f"🦅🔥💰 T5圣杯TOP{top_n} {now} | {total_qualified}只高成功率"

        lines = [
            f"⏰ 时段: {date_str} {period}",
            f"📊 总扫描: {total_raw} 只 → 高成功率筛选: {total_qualified} 只",
            f"🎯 TOP{top_n} 高成功率推荐:"
        ]
        for i, s in enumerate(top, 1):
            code = s.get('code', '?')
            name = s.get('name', '?')
            score = s.get('v132_score', 0)
            tier = s.get('tier', '?')
            price = s.get('price', s.get('now_price', 0))
            change_pct = s.get('change_pct', 0)
            grade = self._grade_signal(s)
            grade_emoji = {'SSS': '🌟', 'SS': '⭐', 'S': '🔥', 'A': '📉'}[grade]

            # 贝叶斯概率
            m46_prob = s.get('m46_prob', s.get('m46_normalized', ''))
            # 盈亏比
            plr = s.get('plr', s.get('m54_plr', ''))
            # 主力意图
            intent = s.get('m51_intent', '')

            line = f"  {i}. {grade_emoji}{grade} {name}({code})"
            line += f" [{tier}档]"
            line += f" 评分{score:.4f}"
            if price:
                line += f" | ¥{price}"
            if change_pct:
                line += f" | {change_pct:+.2f}%"
            lines.append(line)

            # 详细行: 贝叶斯+盈亏比+意图
            details = []
            if m46_prob:
                details.append(f"贝叶斯:{m46_prob:.1%}" if isinstance(m46_prob, float) else f"贝叶斯:{m46_prob}")
            if plr:
                details.append(f"盈亏比:{plr:.1f}:1" if isinstance(plr, float) else f"盈亏比:{plr}")
            if intent:
                details.append(f"主力意图:{intent:.2f}" if isinstance(intent, float) else f"主力:{intent}")
            if details:
                lines.append(f"     ├ {', '.join(details)}")

            # 操作建议行
            if grade in ('SSS', 'SS'):
                lines.append(f"     ├ 操作: 14:50前竞价买入 | 仓位≤{25 if grade=='SSS' else 15}%")
                lines.append(f"     ├ 止损: 次日开盘跌>3%即出")
                lines.append(f"     ├ 止盈: 次日涨≥5%可减半, ≥8%可全出")

        lines.append(f"\n⏰ 截止线: {self.config.get('push_deadline', '14:50')} 前完成操作")
        lines.append(f"筛选门槛: 仅SSS(≥0.80)+SS(≥0.65)级信号")
        lines.append("⚠️ 投资有风险，信号仅供参考")
        return title, '\n'.join(lines)

    def sync_dashboard(self, date_str: str) -> dict:
        """立即同步最新 state/index 到 deploy/data，保证仪表盘刷新"""
        result = {'success': False, 'synced': []}
        try:
            sys.path.insert(0, str(DEPLOY_DIR))
            import sync_to_cloud
            sync_to_cloud.sync()
            result['success'] = True
            result['synced'] = '详见 sync_to_cloud.py 输出'
            self._log('success', '仪表盘数据已即时同步到 deploy/data')
        except Exception as e:
            # 手动兜底同步
            try:
                DEPLOY_DATA.mkdir(parents=True, exist_ok=True)
                state_files = sorted(
                    [f for f in os.listdir(CACHE_DIR) if f.startswith('state_') and f.endswith('.json') and 'watchdog' not in f],
                    reverse=True
                )
                if state_files:
                    import shutil
                    src = CACHE_DIR / state_files[0]
                    dst = DEPLOY_DATA / state_files[0]
                    shutil.copy2(src, dst)
                    result['synced'].append(state_files[0])
                    # 同时更新 state_latest.json
                    latest_dst = DEPLOY_DATA / 'state_latest.json'
                    shutil.copy2(src, latest_dst)
                    result['synced'].append('state_latest.json')
                    result['success'] = True
                    self._log('success', f'兜底同步完成: {state_files[0]}')
            except Exception as e2:
                result['error'] = f'{e}; fallback error: {e2}'
                self._log('error', f'仪表盘同步失败: {e2}')
        return result

    def push_t5_results(self, date_str: Optional[str] = None, period: str = '14:30') -> dict:
        """
        T5 结果一站式推送入口
        返回: {'wechat': {...}, 'miniprogram': {...}, 'dashboard': {...}, 'signals_count': int, 'qualified_count': int}
        """
        date_str = date_str or datetime.now().strftime('%Y%m%d')
        self._log('info', f'开始 T5({date_str} {period}) 圣杯推送流程')

        # 1. 读取信号
        raw = self.load_holy_grail_signals(date_str, period)
        if not raw:
            self._log('error', '未找到圣杯信号文件，推送终止')
            self._fallback_log('未找到圣杯信号文件')
            return {'success': False, 'reason': 'no_signals'}

        signals = raw.get('signals', raw.get('candidates', []))
        total_raw = len(signals)

        # 2. 质量筛选: 只保留高成功率信号
        qualified = self._quality_filter(signals)
        total_qualified = len(qualified)
        self._log('info', f'读取 {total_raw} 只信号 → 质量筛选后 {total_qualified} 只高成功率(SSS/SS)')

        # 3. 微信推送
        title, message = self.build_message(signals, date_str, period)
        wechat_result = self.channel.send(title, message)
        if wechat_result['success']:
            self._log('success', f"微信推送成功 → 通道={wechat_result['channel']}")
        else:
            reason = wechat_result.get('reason', '未配置key或通道失败')
            self._log('warning', f"微信推送未成功: {reason}，已写入兜底日志")
            self._fallback_log(message)

        # 4. 小程序推送（占位）
        mp_result = self.mp.push(openid='', template_id='', data={})
        if mp_result.get('success'):
            self._log('success', '小程序推送成功')
        else:
            self._log('info', f"小程序推送: {mp_result.get('reason')}")

        # 5. 仪表盘即时同步
        dashboard_result = self.sync_dashboard(date_str)

        # 6. 汇总
        summary = {
            'timestamp': datetime.now().isoformat(),
            'date': date_str,
            'period': period,
            'signals_count': total_raw,
            'qualified_count': total_qualified,
            'push_top_n': min(self.config.get('push_top_n', 5), total_qualified),
            'min_grade': self.config.get('min_grade', 'SS'),
            'wechat': wechat_result,
            'miniprogram': mp_result,
            'dashboard': dashboard_result,
            'message_preview': message[:300],
        }

        # 写入推送记录
        log_path = PIPELINE_DIR / f'push_record_{date_str}_{period.replace(":", "")}.json'
        PIPELINE_DIR.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        self._log('success', f"T5 推送流程结束 | 原始{total_raw}只 → 精选{total_qualified}只 → 推送TOP{summary['push_top_n']}")
        return summary

    def diagnose(self) -> dict:
        """推送链路自检"""
        cfg = self.config
        has_qmsg = bool(cfg.get('qmsg_key'))
        has_serverchan = bool(cfg.get('serverchan_key'))
        qf = cfg.get('quality_filter', {})
        return {
            'config_exists': self.config_path.exists(),
            'qmsg_configured': has_qmsg,
            'serverchan_configured': has_serverchan,
            'any_channel_ready': has_qmsg or has_serverchan,
            'enable_push': cfg.get('enable_push', True),
            'enable_miniprogram': cfg.get('enable_miniprogram_push', False),
            'push_top_n': cfg.get('push_top_n', 5),
            'min_grade': cfg.get('min_grade', 'SS'),
            'deadline': cfg.get('push_deadline', '14:50'),
            'quality_filter': {
                'min_v132_score': qf.get('min_v132_score', 0.65),
                'min_m46_prob': qf.get('min_m46_prob', 0.55),
                'min_plr': qf.get('min_plr', 3.0),
                'exclude_tier': qf.get('exclude_tier', ['C', 'D']),
            },
        }


def main():
    print("═" * 60)
    print(" V13.4 圣杯推送集成器 — 一键诊断 & 测试推送")
    print("═" * 60)

    pusher = HolyGrailPusher()
    diag = pusher.diagnose()
    print("\n📋 推送链路自检:")
    for k, v in diag.items():
        print(f"  {k}: {v}")

    if not diag['any_channel_ready']:
        print("\n⚠️ 当前未配置任何微信推送 Key，推送将仅写入兜底日志。")
        print("   请按 data/push_config.json 中 setup_guide 配置 QMSG_KEY 或 SERVERCHAN_KEY。")

    # 如果存在今日T5信号则执行真实推送
    today = datetime.now().strftime('%Y%m%d')
    raw = pusher.load_holy_grail_signals(today, '14:30')
    if raw:
        print(f"\n🚀 检测到今日 T5 信号，执行推送...")
        result = pusher.push_t5_results(today, '14:30')
        print(json.dumps(result, ensure_ascii=False, indent=2)[:1000])
    else:
        print("\n🧪 未检测到今日T5信号，仅做诊断。")

    print("\n" + "═" * 60)


if __name__ == '__main__':
    main()
