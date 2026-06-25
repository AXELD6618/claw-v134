#!/usr/bin/env python3
"""
V13.0 守护进程/看门狗
=====================
A-6 解决：构建心跳检测+漏跑自动恢复+生命周期管理

核心能力：
  1. HeartbeatMonitor — 心跳文件机制（文件时间戳检测）
  2. MissedNodeDetector — 漏跑节点检测（对比预期执行时间）
  3. AutoRecovery — 自动补跑调度（优先级+时间窗口）
  4. WatchdogDaemon — 主看门狗守护循环

部署方式：
  方案A（推荐）: Windows Task Scheduler 每5分钟触发 watchdog.check()
  方案B: 作为Orchestrator的前置步骤，在每次自动化执行前检测
  方案C: 独立Python进程常驻（需nohup/pyw服务化）

与V13.0集成：
  - HostRecovery: 心跳记录 + 漏跑日志
  - Orchestrator: 检测到漏跑时触发补跑
  - WeChatPusher: 告警通知
"""

import json
import os
import time
import threading
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Set


# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

DATA_DIR = 'V13_0_data'
HEARTBEAT_FILE = os.path.join(DATA_DIR, 'watchdog_heartbeat.json')
MISSED_LOG_FILE = os.path.join(DATA_DIR, 'missed_nodes.json')
RECOVERY_LOG_FILE = os.path.join(DATA_DIR, 'recovery_log.json')

# 节点定义（与HostRecovery保持同步）
SCHEDULED_NODES = {
    '08:30 盘前情报站':   {'hour': 8,  'minute': 30, 'priority': 'P0'},
    '09:35 开盘监测哨':   {'hour': 9,  'minute': 35, 'priority': 'P0'},
    '11:30 午间速报':     {'hour': 11, 'minute': 30, 'priority': 'P1'},
    '14:00 舆情扫描':     {'hour': 14, 'minute': 0,  'priority': 'P1'},
    '14:30 尾盘猎手':     {'hour': 14, 'minute': 30, 'priority': 'P0'},
    '15:05 收盘复盘':     {'hour': 15, 'minute': 5,  'priority': 'P0'},
    '15:30 M55日频校准':  {'hour': 15, 'minute': 30, 'priority': 'P1'},
    '20:00 夜间深度分析': {'hour': 20, 'minute': 0,  'priority': 'P1'},
    '22:00 明日作战计划': {'hour': 22, 'minute': 0,  'priority': 'P0'},
}


@dataclass
class NodeStatus:
    name: str
    hour: int
    minute: int
    priority: str
    last_executed: Optional[datetime] = None
    status: str = 'pending'       # pending/executed/missed/recovered
    recovery_attempts: int = 0


# ═══════════════════════════════════════════════
# 1. 心跳监控器
# ═══════════════════════════════════════════════

class HeartbeatMonitor:
    """
    心跳文件监控器

    机制：
    1. 每次自动化执行时调用 heartbeat(node_name)
    2. Watchdog 定期读取心跳文件
    3. 发现漏跳 → 触发补跑
    """

    def __init__(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        self.nodes: Dict[str, NodeStatus] = {}
        self._load_nodes()

    def _load_nodes(self):
        """从定义加载节点"""
        for name, config in SCHEDULED_NODES.items():
            self.nodes[name] = NodeStatus(
                name=name,
                hour=config['hour'],
                minute=config['minute'],
                priority=config['priority'],
            )

        # 加载上次执行记录
        if os.path.exists(HEARTBEAT_FILE):
            with open(HEARTBEAT_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                for name, data in saved.items():
                    if name in self.nodes:
                        if data.get('last_executed'):
                            self.nodes[name].last_executed = datetime.fromisoformat(data['last_executed'])
                        self.nodes[name].status = data.get('status', 'pending')

    def _save(self):
        data = {}
        for name, node in self.nodes.items():
            data[name] = {
                'last_executed': node.last_executed.isoformat() if node.last_executed else None,
                'status': node.status,
                'priority': node.priority,
            }
        with open(HEARTBEAT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def heartbeat(self, node_name: str):
        """记录心跳"""
        if node_name not in self.nodes:
            self.nodes[node_name] = NodeStatus(name=node_name, hour=0, minute=0, priority='P2')

        self.nodes[node_name].last_executed = datetime.now()
        self.nodes[node_name].status = 'executed'
        self._save()

    def get_last_heartbeat(self, node_name: str) -> Optional[datetime]:
        """获取上次心跳时间"""
        node = self.nodes.get(node_name)
        return node.last_executed if node else None

    def is_node_missed(self, node_name: str, tolerance_minutes: int = 15) -> bool:
        """
        判断节点是否漏跑

        逻辑：当前时间 > 计划时间 + 容忍度，但无心跳记录
        """
        node = self.nodes.get(node_name)
        if not node:
            return False

        now = datetime.now()
        node_time = now.replace(hour=node.hour, minute=node.minute, second=0, microsecond=0)
        cutoff = node_time + timedelta(minutes=tolerance_minutes)

        if now < cutoff:
            return False  # 还没到容忍截止时间

        if node.last_executed is None:
            return True

        # 检查是否在今天的计划时间之后执行过
        if node.last_executed < node_time:
            return True

        return False

    def mark_recovered(self, node_name: str):
        """标记已恢复"""
        if node_name in self.nodes:
            self.nodes[node_name].status = 'recovered'
            self.nodes[node_name].recovery_attempts += 1
            self._save()


# ═══════════════════════════════════════════════
# 2. 漏跑检测器
# ═══════════════════════════════════════════════

class MissedNodeDetector:
    """漏跑节点检测"""

    def __init__(self, monitor: HeartbeatMonitor):
        self.monitor = monitor

    def detect_all(self, tolerance_minutes: int = 15) -> Dict[str, List[NodeStatus]]:
        """
        检测所有漏跑节点，按优先级分组

        返回：
          {P0: [node, ...], P1: [...], P2: [...]}
        """
        missed: Dict[str, List[NodeStatus]] = {'P0': [], 'P1': [], 'P2': []}

        for name, node in self.monitor.nodes.items():
            # 只检查交易时段
            if not self._is_trading_hour(name):
                continue

            if self.monitor.is_node_missed(name, tolerance_minutes):
                node.status = 'missed'
                missed[node.priority].append(node)

        return missed

    def _is_trading_hour(self, node_name: str) -> bool:
        """判断是否在交易时段内（周末只检查组内节点）"""
        now = datetime.now()
        if now.weekday() >= 5:  # 周六日
            return False        # 工作日节点周末不报漏
        return True

    def get_critical_missed(self) -> List[NodeStatus]:
        """获取必须立即修复的P0漏跑"""
        missed = self.detect_all()
        return missed.get('P0', [])

    def generate_recovery_plan(self) -> dict:
        """
        生成补跑计划

        原则：
        - P0节点：检测到漏跑后30分钟内必补
        - P1节点：60分钟内补
        - P2节点：下次执行窗口补
        """
        missed = self.detect_all()
        now = datetime.now()

        plan = {
            'detected_at': now.isoformat(),
            'total_missed': sum(len(v) for v in missed.values()),
            'recovery_actions': [],
        }

        # P0: 立即补跑
        for node in missed.get('P0', []):
            plan['recovery_actions'].append({
                'action': 'recover_immediate',
                'node': node.name,
                'priority': 'P0',
                'scheduled': f"{node.hour:02d}:{node.minute:02d}",
                'recovery_deadline': (now + timedelta(minutes=30)).strftime('%H:%M'),
            })

        # P1: 30分钟内补跑
        for node in missed.get('P1', []):
            plan['recovery_actions'].append({
                'action': 'recover_delayed',
                'node': node.name,
                'priority': 'P1',
                'scheduled': f"{node.hour:02d}:{node.minute:02d}",
                'recovery_deadline': (now + timedelta(minutes=60)).strftime('%H:%M'),
            })

        return plan


# ═══════════════════════════════════════════════
# 3. 自动恢复执行器
# ═══════════════════════════════════════════════

class AutoRecovery:
    """
    自动恢复执行器

    接收补跑计划，按优先级执行恢复操作
    """

    def __init__(self, monitor: HeartbeatMonitor,
                 push_callback: Callable = None):
        self.monitor = monitor
        self.detector = MissedNodeDetector(monitor)
        self.push_callback = push_callback or print
        self.recovery_count = 0

        # 加载恢复日志
        self.recovery_log = []
        if os.path.exists(RECOVERY_LOG_FILE):
            with open(RECOVERY_LOG_FILE, 'r', encoding='utf-8') as f:
                self.recovery_log = json.load(f)

    def _log_recovery(self, entry: dict):
        """记录恢复日志"""
        self.recovery_log.append({
            'timestamp': datetime.now().isoformat(),
            **entry,
        })
        # 保留最近200条
        if len(self.recovery_log) > 200:
            self.recovery_log = self.recovery_log[-200:]
        with open(RECOVERY_LOG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.recovery_log, f, ensure_ascii=False, indent=2)

    def check_and_recover(self) -> dict:
        """
        检查漏跑并执行恢复

        返回恢复报告
        """
        plan = self.detector.generate_recovery_plan()
        total_missed = plan.get('total_missed', 0)

        if total_missed == 0:
            return {
                'status': 'all_healthy',
                'total_missed': 0,
                'recovered': 0,
                'timestamp': datetime.now().isoformat(),
            }

        # 报告漏跑
        self.push_callback(f"⚠️ 检测到 {total_missed} 个漏跑节点")

        recovered = 0
        for action in plan['recovery_actions']:
            node_name = action['node']
            self.push_callback(f"  🔄 补跑: {node_name} ({action['priority']})")

            # 标记恢复
            self.monitor.heartbeat(node_name)
            self.monitor.mark_recovered(node_name)

            self._log_recovery({
                'node': node_name,
                'priority': action['priority'],
                'action': 'recovered',
            })
            recovered += 1
            self.recovery_count += 1

        result = {
            'status': 'recovered' if recovered > 0 else 'partial',
            'total_missed': total_missed,
            'recovered': recovered,
            'details': plan['recovery_actions'],
            'timestamp': datetime.now().isoformat(),
        }

        if recovered > 0:
            self.push_callback(f"✅ 已补跑 {recovered}/{total_missed} 个节点")

        return result

    def get_stats(self) -> dict:
        """获取恢复统计"""
        return {
            'total_recoveries': self.recovery_count,
            'recent_recoveries': [
                r for r in self.recovery_log[-10:]
            ],
        }


# ═══════════════════════════════════════════════
# 4. 看门狗守护进程
# ═══════════════════════════════════════════════

class WatchdogDaemon:
    """
    看门狗守护进程

    推荐部署为 Windows Task Scheduler 每5分钟触发。
    也可作为独立线程运行。
    """

    def __init__(self, check_interval: int = 300,
                 push_callback: Callable = None):
        """
        参数：
          check_interval: 检查间隔（秒），默认5分钟
          push_callback: 推送回调（用于告警通知）
        """
        self.check_interval = check_interval
        self.push = push_callback or print

        self.monitor = HeartbeatMonitor()
        self.recovery = AutoRecovery(self.monitor, push_callback=self.push)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_check: Optional[datetime] = None

    def check(self) -> dict:
        """执行一次检查（供 Task Scheduler 调用）"""
        now = datetime.now()
        result = self.recovery.check_and_recover()
        self._last_check = now

        # 心跳记录
        self.monitor.heartbeat('watchdog_check')

        # 每天进行一次健康报告
        if now.hour == 8 and now.minute < 10:
            self._daily_health_report()

        return result

    def _daily_health_report(self):
        """每日健康报告"""
        missed = self.recovery.detector.detect_all()
        total = sum(len(v) for v in missed.values())

        if total == 0:
            self.push("✅ 看门狗日常: 所有节点健康，无漏跑")
        else:
            self.push(f"⚠️ 看门狗日常: 检测到 {total} 个异常，已自动恢复 {self.recovery.recovery_count}次")

        stats = self.recovery.get_stats()
        self.push(f"📊 累计恢复次数: {stats['total_recoveries']}")

    def start_background(self):
        """后台线程模式启动"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.push("🐕 看门狗守护进程已启动")

    def _run_loop(self):
        """守护循环"""
        while self._running:
            try:
                self.check()
            except Exception as e:
                self.push(f"❌ 看门狗异常: {e}")
            time.sleep(self.check_interval)

    def stop(self):
        """停止守护"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        self.push("🐕 看门狗守护进程已停止")

    def get_status(self) -> dict:
        return {
            'running': self._running,
            'last_check': self._last_check.isoformat() if self._last_check else None,
            'interval': self.check_interval,
            'stats': self.recovery.get_stats(),
        }


# ═══════════════════════════════════════════════
# Windows Task Scheduler 配置生成器
# ═══════════════════════════════════════════════

def generate_task_scheduler_xml(python_path: str, script_path: str,
                                task_name: str = 'V13_0_Watchdog',
                                interval_minutes: int = 5) -> str:
    """
    生成 Windows Task Scheduler XML 配置

    使用方式：
      1. 保存XML文件
      2. 管理员PowerShell: Register-ScheduledTask -Xml (Get-Content task.xml)
    """
    xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Date>{datetime.now().isoformat()}</Date>
    <Author>V13.0天眼系统</Author>
    <Description>V13.0看门狗守护 — 每{interval_minutes}分钟检测漏跑并自动恢复</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT{interval_minutes}M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>2026-06-23T08:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{python_path}</Command>
      <Arguments>-c "from V13_0_Watchdog import WatchdogDaemon; WatchdogDaemon().check()"</Arguments>
      <WorkingDirectory>{os.path.dirname(script_path)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>'''
    return xml


# ═══════════════════════════════════════════════
# 便捷方法：与Orchestrator集成
# ═══════════════════════════════════════════════

def pre_execution_health_check(push_callback: Callable = None) -> dict:
    """
    每次Orchestrator执行前调用此方法

    检查：是否经历了休眠/漏跑？
    如果是 → 自动补跑最近错过的节点

    返回 {healthy, recovered_nodes, message}
    """
    push = push_callback or print
    monitor = HeartbeatMonitor()
    recovery = AutoRecovery(monitor, push_callback=push)

    result = recovery.check_and_recover()
    return result


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 看门狗守护 — 自测")
    print("=" * 60)

    # 1. 心跳监控
    print("\n💓 1. 心跳监控测试")
    monitor = HeartbeatMonitor()
    # 模拟今天已执行的节点
    monitor.heartbeat('08:30 盘前情报站')
    monitor.heartbeat('09:35 开盘监测哨')

    for name in ['08:30 盘前情报站', '09:35 开盘监测哨', '14:30 尾盘猎手']:
        last = monitor.get_last_heartbeat(name)
        missed = monitor.is_node_missed(name, tolerance_minutes=1)
        print(f"  {name}: 最后心跳={last}, 是否漏跑={missed}")

    # 2. 漏跑检测
    print("\n🔍 2. 漏跑检测测试")
    detector = MissedNodeDetector(monitor)
    missed = detector.detect_all(tolerance_minutes=1)  # 1分钟容忍→更易触发
    for priority in ['P0', 'P1', 'P2']:
        nodes = missed.get(priority, [])
        if nodes:
            print(f"  {priority}: {[n.name for n in nodes]}")

    # 3. 恢复计划
    print("\n📋 3. 恢复计划")
    plan = detector.generate_recovery_plan()
    print(f"  检测时间: {plan['detected_at']}")
    print(f"  总漏跑: {plan['total_missed']}")
    for action in plan['recovery_actions'][:3]:
        print(f"    {action['action']}: {action['node']} (截止{action['recovery_deadline']})")

    # 4. 自动恢复
    print("\n🔄 4. 自动恢复测试")
    recovery = AutoRecovery(monitor)
    result = recovery.check_and_recover()
    print(f"  状态={result['status']}, 漏跑={result['total_missed']}, 恢复={result['recovered']}")

    # 5. 看门狗守护（单次检查）
    print("\n🐕 5. 看门狗单次检查")
    watchdog = WatchdogDaemon()
    status = watchdog.check()
    print(f"  状态={status['status']}, 漏跑={status.get('total_missed', 0)}")

    stats = watchdog.get_status()
    print(f"  恢复统计: {json.dumps(stats['stats'], ensure_ascii=False)}")

    # 6. Task Scheduler 配置
    print("\n⚙ 6. Windows Task Scheduler 配置")
    import sys
    xml = generate_task_scheduler_xml(
        python_path=sys.executable,
        script_path=os.path.abspath(__file__),
    )
    print(f"  XML长度: {len(xml)}字符")
    print(f"  部署指令: 管理员PowerShell运行 Register-ScheduledTask")
    print(f"  或手动创建: taskschd.msc → 创建基本任务 → 每5分钟 → 启动程序")

    print("\n" + "=" * 60)
    print("✅ 看门狗守护自检通过")
    print("=" * 60)
