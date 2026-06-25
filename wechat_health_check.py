#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微信监听器健康检查脚本"""
import sqlite3
import os
import subprocess
import sys
from datetime import datetime, timedelta

PROJECT_DIR = r'E:\WorkBuddy_dot_workbuddy\Claw'
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'sentiment_db.db')
LOG_PATH = os.path.join(PROJECT_DIR, 'logs', 'wechat_sync.log')
HEALTH_LOG = os.path.join(PROJECT_DIR, 'logs', 'wechat_health.log')
BAT_PATH = os.path.join(PROJECT_DIR, 'start_wechat_sync.bat')


def write_health_log(msg):
    """写入健康检查日志"""
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')


def check_process():
    """检查 pythonw.exe 进程"""
    try:
        result = subprocess.run(
            'tasklist /FI "IMAGENAME eq pythonw.exe"',
            capture_output=True, shell=True
        )
        # 用 gbk 解码避免 utf-8 错误
        try:
            output = result.stdout.decode('gbk', errors='ignore')
        except Exception:
            output = result.stdout.decode('utf-8', errors='ignore')
        if 'pythonw.exe' in output and '没有' not in output and 'No tasks' not in output:
            pids = []
            for line in output.split('\n'):
                if 'pythonw.exe' in line.lower():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].lower() != 'info:':
                        pids.append(parts[1])
            return True, pids
        return False, []
    except Exception as e:
        return False, [f'Error: {e}']


def check_log():
    """检查日志文件最后修改时间"""
    if not os.path.exists(LOG_PATH):
        return False, None, None
    mtime = datetime.fromtimestamp(os.path.getmtime(LOG_PATH))
    age_minutes = (datetime.now() - mtime).total_seconds() / 60
    return age_minutes <= 60, mtime, age_minutes


def check_db():
    """检查数据库最新记录时间"""
    if not os.path.exists(DB_PATH):
        return False, None, 0
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # 优先查 wechat_messages, 然后 wechat_contents
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        target_table = None
        for t in ['wechat_messages', 'wechat_contents']:
            if t in tables:
                target_table = t
                break

        if not target_table:
            conn.close()
            return True, None, 0  # DB exists but no wechat table, treat as OK

        cursor.execute(f'SELECT COUNT(*) FROM {target_table}')
        count = cursor.fetchone()[0]
        # find timestamp column
        cursor.execute(f'PRAGMA table_info({target_table})')
        cols = [c[1] for c in cursor.fetchall()]
        ts_col = None
        for c in cols:
            if c.lower() in ('timestamp', 'collect_time', 'created_at', 'updated_at'):
                ts_col = c
                break

        last_ts = None
        if ts_col:
            cursor.execute(f'SELECT MAX({ts_col}) FROM {target_table}')
            row = cursor.fetchone()
            if row and row[0]:
                last_ts = row[0]

        conn.close()

        # 计算最新消息距今时间
        if last_ts:
            try:
                last_dt = datetime.strptime(str(last_ts)[:19], '%Y-%m-%d %H:%M:%S')
                age_min = (datetime.now() - last_dt).total_seconds() / 60
            except Exception:
                age_min = 9999
        else:
            age_min = 9999

        return age_min <= 60, last_ts, count
    except Exception as e:
        return False, f'Error: {e}', 0


def main():
    now = datetime.now()
    write_health_log(f"\n{'='*60}")
    write_health_log(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 微信监听器健康检查开始")

    print(f"\n{'='*60}")
    print(f"微信监听器健康检查 - {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print('='*60)

    # 1. 进程检查
    proc_ok, pids = check_process()
    print(f"\n[1] 进程检查: {'✅ 运行中' if proc_ok else '❌ 未运行'}")
    if pids:
        print(f"    PID: {', '.join(pids)}")

    # 2. 日志检查
    log_ok, log_time, log_age = check_log()
    print(f"\n[2] 日志检查: {'✅ 1小时内有更新' if log_ok else '❌ 1小时内无更新或不存在'}")
    if log_time:
        print(f"    最后修改: {log_time.strftime('%Y-%m-%d %H:%M:%S')} (距今 {log_age:.1f} 分钟)")

    # 3. 数据库检查
    db_ok, last_msg, msg_count = check_db()
    print(f"\n[3] 数据库检查: {'✅ 1小时内有记录' if db_ok else '❌ 1小时内无记录'}")
    print(f"    消息总数: {msg_count}条")
    if last_msg:
        print(f"    最后消息: {last_msg}")

    # 综合判断
    is_healthy = proc_ok
    print(f"\n{'='*60}")
    print(f"综合状态: {'✅ 监听器运行正常' if is_healthy else '❌ 监听器已停止，需要启动'}")
    print('='*60)

    # 如果未运行,尝试启动
    if not is_healthy:
        print(f"\n⚠️ 监听器未运行,尝试启动...")
        write_health_log(f"[{now.strftime('%H:%M:%S')}] 状态: 未运行, 启动中...")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            print(f"\n[启动尝试 {attempt}/{max_attempts}]")
            try:
                # 方式1: 直接 pythonw.exe 后台分离启动 (2026-06-25 实测成功)
                pythonw_path = r"C:\Users\SGM-AXELD\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
                script_path = os.path.join(PROJECT_DIR, 'V13_2_WeChatRealtimeSync.py')
                if os.path.exists(pythonw_path):
                    result = subprocess.Popen(
                        [pythonw_path, script_path],
                        cwd=PROJECT_DIR,
                        creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                        close_fds=True
                    )
                    print(f"    启动方式: pythonw.exe分离进程 PID={result.pid}")
                else:
                    # 方式2: 退回bat脚本
                    result = subprocess.run(
                        ['cmd.exe', '/c', 'start', '', '/B', BAT_PATH],
                        capture_output=True, text=True, cwd=PROJECT_DIR
                    )
                    print(f"    启动方式: bat脚本 返回码={result.returncode}")

                # 等待6秒
                import time
                time.sleep(6)

                # 重新检查
                proc_ok2, pids2 = check_process()
                log_ok2, log_time2, log_age2 = check_log()

                if proc_ok2 or log_ok2:
                    print(f"    ✅ 启动成功!")
                    print(f"    进程: {'运行中' if proc_ok2 else '未运行'}")
                    print(f"    日志: {'1小时内有更新' if log_ok2 else '未更新'}")
                    write_health_log(f"[尝试{attempt}] 启动成功 PID: {pids2}")
                    is_healthy = True
                    break
                else:
                    print(f"    ❌ 启动失败 (尝试 {attempt}/{max_attempts})")
                    write_health_log(f"[尝试{attempt}] 启动失败")
            except Exception as e:
                print(f"    ❌ 异常: {e}")
                write_health_log(f"[尝试{attempt}] 异常: {e}")

        if not is_healthy:
            print(f"\n🚨 严重警告: 连续{max_attempts}次启动失败!")
            print(f"   请手动检查:")
            print(f"   1. wxauto 库是否安装: python -c \"import wxauto\"")
            print(f"   2. 微信PC客户端是否运行")
            print(f"   3. 日志文件: {LOG_PATH}")
            print(f"   4. 启动脚本: {BAT_PATH}")
            write_health_log(f"🚨 严重警告: 连续{max_attempts}次启动失败")
    else:
        write_health_log(f"[{now.strftime('%H:%M:%S')}] 状态: ✅ 运行正常 PID: {pids}")

    # 输出报告
    report = f"""
📊 微信监听器健康检查报告
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏰ 检查时间: {now.strftime('%Y-%m-%d %H:%M:%S')}
🔧 监听器状态: {'✅ 运行中' if is_healthy else '❌ 已停止/启动失败'}
📝 最后日志时间: {log_time.strftime('%Y-%m-%d %H:%M:%S') if log_time else 'N/A (日志不存在)'}
💾 最后消息时间: {last_msg if last_msg else 'N/A'}
📊 今日处理消息: {msg_count}条
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    print(report)
    write_health_log(f"报告: 状态={'运行中' if is_healthy else '停止'} 消息数={msg_count}")
    write_health_log(f"{'='*60}\n")

    return 0 if is_healthy else 1


if __name__ == '__main__':
    sys.exit(main())
