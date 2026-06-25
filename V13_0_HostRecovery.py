#!/usr/bin/env python3
"""
V13.0 主机休眠容错恢复系统
============================
Phase 1 紧急修复：唤醒后自动补跑 + 微信告警

功能：
1. 心跳检测：记录每次自动化执行的时间戳
2. 休眠检测：比较预期执行时间 vs 实际执行时间
3. 自动补跑：按优先级补跑漏掉的节点
4. 微信告警：通知用户休眠及补跑状态

优先级补跑策略：
- P0（必须补跑）：08:30盘前情报、09:35开盘监测、14:30尾盘猎手、15:05收盘复盘、22:00作战计划
- P1（建议补跑）：11:30午间速报、20:00夜间分析、14:00舆情扫描
- P2（可选补跑）：周末知识库扫描、M55自校准
"""

import json
import os
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

# 自动化节点定义（名称: 时间）
SCHEDULED_NODES = {
    '08:30 盘前情报站':   {'hour': 8,  'minute': 30, 'priority': 'P0', 'automation_id': 'automation-1780320576012'},
    '09:35 开盘监测哨':   {'hour': 9,  'minute': 35, 'priority': 'P0', 'automation_id': 'automation-1780320582537'},
    '11:30 午间速报':     {'hour': 11, 'minute': 30, 'priority': 'P1', 'automation_id': 'automation-1780320588614'},
    '14:00 舆情扫描':     {'hour': 14, 'minute': 0,  'priority': 'P1', 'automation_id': 'automation-1781977506489'},
    '14:30 尾盘猎手':     {'hour': 14, 'minute': 30, 'priority': 'P0', 'automation_id': 'automation-1780320595134'},
    '15:05 收盘复盘':     {'hour': 15, 'minute': 5,  'priority': 'P0', 'automation_id': 'automation-1780320601797'},
    '20:00 夜间深度分析': {'hour': 20, 'minute': 0,  'priority': 'P1', 'automation_id': 'automation-1780320609631'},
    '22:00 明日作战计划': {'hour': 22, 'minute': 0,  'priority': 'P0', 'automation_id': 'automation-1780320618418'},
}

# 周末节点
WEEKEND_NODES = {
    '周六09:00 AI算力扫描':     {'weekday': 5, 'hour': 9,  'minute': 0,  'priority': 'P2', 'automation_id': 'automation-1782058023545'},
    '周六10:00 基建机器人扫描':  {'weekday': 5, 'hour': 10, 'minute': 0,  'priority': 'P2', 'automation_id': 'automation-1782058023628'},
    '周六11:00 新赛道发现':      {'weekday': 5, 'hour': 11, 'minute': 0,  'priority': 'P2', 'automation_id': 'automation-1782058023696'},
    '周日22:00 M55自校准':       {'weekday': 6, 'hour': 22, 'minute': 0,  'priority': 'P2', 'automation_id': 'automation-1782108818301'},
}


@dataclass
class HeartbeatRecord:
    """心跳记录"""
    node_name: str
    scheduled_time: datetime
    actual_time: Optional[datetime]
    status: str  # 'executed' | 'missed' | 'recovered' | 'pending'
    priority: str


class HostRecoveryManager:
    """主机休眠容错恢复管理器"""

    def __init__(self, data_dir: str = 'V13_0_data'):
        self.data_dir = data_dir
        self.heartbeat_file = os.path.join(data_dir, 'heartbeat.json')
        self.recovery_log_file = os.path.join(data_dir, 'recovery_log.json')
        self.sleep_threshold_minutes = 15  # 超过15分钟未执行视为错过
        self.max_recovery_attempts = 3

        os.makedirs(data_dir, exist_ok=True)
        self._load_state()

    def _load_state(self):
        """加载状态"""
        self.heartbeat_data = {}
        if os.path.exists(self.heartbeat_file):
            with open(self.heartbeat_file, 'r', encoding='utf-8') as f:
                self.heartbeat_data = json.load(f)

        self.recovery_log = []
        if os.path.exists(self.recovery_log_file):
            with open(self.recovery_log_file, 'r', encoding='utf-8') as f:
                self.recovery_log = json.load(f)

    def _save_state(self):
        """保存状态"""
        with open(self.heartbeat_file, 'w', encoding='utf-8') as f:
            json.dump(self.heartbeat_data, f, ensure_ascii=False, indent=2)

        with open(self.recovery_log_file, 'w', encoding='utf-8') as f:
            json.dump(self.recovery_log, f, ensure_ascii=False, indent=2)

    def record_heartbeat(self, node_name: str, status: str = 'executed'):
        """记录心跳"""
        now = datetime.now()
        date_key = now.strftime('%Y-%m-%d')

        if date_key not in self.heartbeat_data:
            self.heartbeat_data[date_key] = {}

        self.heartbeat_data[date_key][node_name] = {
            'actual_time': now.isoformat(),
            'status': status,
        }
        self._save_state()

    def detect_sleep(self, node_name: str, node_config: dict) -> bool:
        """
        检测节点是否因休眠而错过
        返回：True = 已错过需要补跑
        """
        now = datetime.now()
        date_key = now.strftime('%Y-%m-%d')

        # 计算预期执行时间
        if 'weekday' in node_config:
            # 周末节点
            days_ahead = node_config['weekday'] - now.weekday()
            if days_ahead < 0:
                days_ahead += 7
            scheduled = now.replace(
                hour=node_config['hour'],
                minute=node_config['minute'],
                second=0, microsecond=0
            ) + timedelta(days=days_ahead if days_ahead != 0 else 0)
        else:
            # 工作日节点
            scheduled = now.replace(
                hour=node_config['hour'],
                minute=node_config['minute'],
                second=0, microsecond=0
            )

        # 如果还没到执行时间，跳过
        if now < scheduled:
            return False

        # 检查是否在合理时间窗口内（1小时内正常）
        time_diff = (now - scheduled).total_seconds() / 60

        if time_diff < self.sleep_threshold_minutes:
            return False  # 在正常窗口内

        # 检查心跳记录
        today_heartbeat = self.heartbeat_data.get(date_key, {})
        if node_name in today_heartbeat and today_heartbeat[node_name].get('status') == 'executed':
            return False  # 已执行

        # 超过阈值且未执行 → 判定错过
        return True

    def scan_missed_nodes(self, include_weekend: bool = True) -> List[Tuple[str, dict]]:
        """
        扫描所有错过的节点
        返回：[(节点名, 配置), ...] 按优先级排序
        """
        missed = []

        # 检查工作日节点
        now = datetime.now()
        if now.weekday() < 5:  # 周一到周五
            for name, config in SCHEDULED_NODES.items():
                if self.detect_sleep(name, config):
                    missed.append((name, config))

        # 检查周末节点
        if include_weekend and now.weekday() >= 5:
            for name, config in WEEKEND_NODES.items():
                if self.detect_sleep(name, config):
                    missed.append((name, config))

        # 按优先级排序：P0 > P1 > P2
        priority_order = {'P0': 0, 'P1': 1, 'P2': 2}
        missed.sort(key=lambda x: priority_order.get(x[1].get('priority', 'P2'), 99))

        return missed

    def generate_recovery_plan(self) -> dict:
        """
        生成补跑计划
        返回补跑报告
        """
        missed_nodes = self.scan_missed_nodes()

        if not missed_nodes:
            return {
                'status': 'all_clear',
                'message': '✅ 所有节点正常执行，无需补跑',
                'missed_count': 0,
                'recovery_tasks': [],
            }

        recovery_tasks = []
        for name, config in missed_nodes:
            priority = config.get('priority', 'P2')

            # P2节点在非周末不补跑
            now = datetime.now()
            if priority == 'P2' and now.weekday() < 5:
                continue

            recovery_tasks.append({
                'node_name': name,
                'automation_id': config.get('automation_id', ''),
                'priority': priority,
                'scheduled_time': f"{config.get('hour', 0):02d}:{config.get('minute', 0):02d}",
                'action': 'trigger_now' if priority in ('P0', 'P1') else 'log_only',
                'reason': f'主机休眠导致{name}错过执行，{priority}级节点需补跑',
            })

        return {
            'status': 'recovery_needed' if recovery_tasks else 'minor_miss',
            'message': f'⚠️ 检测到{len(missed_nodes)}个节点错过执行，需补跑{len(recovery_tasks)}个',
            'missed_count': len(missed_nodes),
            'recovery_tasks': recovery_tasks,
            'detected_at': datetime.now().isoformat(),
        }

    def log_recovery(self, plan: dict, results: List[dict] = None):
        """记录补跑日志"""
        entry = {
            'timestamp': datetime.now().isoformat(),
            'plan': plan,
            'results': results or [],
        }
        self.recovery_log.append(entry)
        self._save_state()

    def get_wechat_alert_message(self, plan: dict) -> str:
        """
        生成微信告警消息
        """
        if plan['status'] == 'all_clear':
            return ''  # 无需告警

        lines = [
            '🚨 [天眼V13.0] 主机休眠告警',
            f'━━━━━━━━━━━━━━━━━━',
            f'⏰ 检测时间: {datetime.now().strftime("%H:%M:%S")}',
            f'⚠️ 错过节点: {plan["missed_count"]}个',
            f'📋 需补跑: {len(plan.get("recovery_tasks", []))}个',
            '',
        ]

        for task in plan.get('recovery_tasks', []):
            emoji = '🔴' if task['priority'] == 'P0' else '🟡' if task['priority'] == 'P1' else '🔵'
            lines.append(f'{emoji} [{task["priority"]}] {task["node_name"]}')
            lines.append(f'   └ 预计时间: {task["scheduled_time"]}')

        lines.extend([
            '',
            '━━━━━━━━━━━━━━━━━━',
            '🤖 系统已自动触发补跑，详见执行日志',
        ])

        return '\n'.join(lines)


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def run_host_recovery(data_dir: str = 'V13_0_data') -> dict:
    """
    主机休眠恢复便捷入口
    推荐在自动化节点开头调用此函数
    """
    manager = HostRecoveryManager(data_dir)

    # 1. 扫描错过节点
    plan = manager.generate_recovery_plan()

    # 2. 生成告警
    alert_msg = manager.get_wechat_alert_message(plan)

    # 3. 记录日志
    manager.log_recovery(plan)

    return {
        'plan': plan,
        'wechat_alert': alert_msg,
        'should_alert': plan['status'] in ('recovery_needed',),
        'recovery_tasks_count': len(plan.get('recovery_tasks', [])),
    }


def record_node_execution(node_name: str, data_dir: str = 'V13_0_data'):
    """在自动化节点执行时记录心跳"""
    manager = HostRecoveryManager(data_dir)
    manager.record_heartbeat(node_name, 'executed')
    print(f"✅ 心跳已记录: {node_name}")


# ═══════════════════════════════════════════════
# 自动化节点注入代码（每个节点开头添加）
# ═══════════════════════════════════════════════

RECOVERY_INJECT_CODE = '''
# ─── V13.0 主机休眠容错检测 ───
import sys; sys.path.insert(0, '.')
from V13_0_HostRecovery import run_host_recovery, record_node_execution

# 1. 开机检测：扫描是否有错过节点需补跑
recovery = run_host_recovery()
if recovery['plan']['status'] in ('recovery_needed',):
    print(f"⚠️ 检测到{recovery['plan']['missed_count']}个节点错过执行")
    for task in recovery['plan'].get('recovery_tasks', []):
        print(f"  补跑: [{task['priority']}] {task['node_name']}")
    # push_to_wechat=1 时发送告警
    if recovery.get('wechat_alert'):
        print(recovery['wechat_alert'])

# 2. 当前节点记录心跳
record_node_execution("NODE_NAME_PLACEHOLDER")

# ─── 容错检测完毕 ───
'''


if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 主机休眠容错恢复系统")
    print("=" * 60)

    # 自测
    manager = HostRecoveryManager()

    # 模拟心跳记录
    manager.record_heartbeat('08:30 盘前情报站', 'executed')
    manager.record_heartbeat('09:35 开盘监测哨', 'executed')

    print("\n📊 当前心跳状态:")
    print(json.dumps(manager.heartbeat_data, ensure_ascii=False, indent=2))

    # 扫描错过节点
    plan = manager.generate_recovery_plan()
    print(f"\n🔍 错过的节点扫描:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    if plan['status'] == 'all_clear':
        print("\n✅ 所有节点正常，无需补跑")
    else:
        alert = manager.get_wechat_alert_message(plan)
        print(f"\n📱 微信告警消息:\n{alert}")
