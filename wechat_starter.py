#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""微信监听器启动器 - 修复版"""
import os
import sys
import time
import subprocess
import shutil

PROJECT_DIR = r'E:\WorkBuddy_dot_workbuddy\Claw'
BAT_PATH = os.path.join(PROJECT_DIR, 'start_wechat_sync.bat')
PYTHONW = r'C:\Users\SGM-AXELD\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe'
SYNC_SCRIPT = os.path.join(PROJECT_DIR, 'V13_2_WeChatRealtimeSync.py')


def start_via_bat():
    """通过 bat 启动 (使用 os.startfile 异步启动)"""
    try:
        # os.startfile 异步打开bat, 不会阻塞
        os.startfile(BAT_PATH)
        return True, "os.startfile OK"
    except Exception as e:
        return False, f"os.startfile failed: {e}"


def start_via_pythonw():
    """直接用 pythonw.exe 启动监听器 (绕过bat)"""
    try:
        # 使用 DETACHED_PROCESS flag 让进程完全独立
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [PYTHONW, SYNC_SCRIPT],
            cwd=PROJECT_DIR,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "pythonw.exe direct launch OK"
    except Exception as e:
        return False, f"pythonw direct failed: {e}"


def start_via_shell():
    """通过 cmd /c start 启动 (新窗口)"""
    try:
        # 用 shell=True 避免参数转义问题
        subprocess.Popen(
            f'start "" "{BAT_PATH}"',
            shell=True,
            cwd=PROJECT_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True, "shell start OK"
    except Exception as e:
        return False, f"shell start failed: {e}"


def check_after_start():
    """启动后等待并检查"""
    time.sleep(5)
    # 检查进程
    try:
        out = subprocess.check_output(
            'tasklist /FI "IMAGENAME eq pythonw.exe"',
            shell=True, text=False
        ).decode('gbk', errors='ignore')
        if 'pythonw.exe' in out and '没有' not in out:
            # 找PID
            for line in out.split('\n'):
                if 'pythonw.exe' in line.lower():
                    parts = line.split()
                    if len(parts) >= 2:
                        return True, parts[1]
            return True, "unknown"
        return False, "no pythonw process"
    except Exception as e:
        return False, f"check error: {e}"


if __name__ == '__main__':
    print("=" * 60)
    print("微信监听器启动器")
    print("=" * 60)

    # 三种方式依次尝试
    methods = [
        ("方式1: os.startfile", start_via_bat),
        ("方式2: pythonw.exe 直启", start_via_pythonw),
        ("方式3: shell start", start_via_shell),
    ]

    for name, fn in methods:
        print(f"\n{name}...")
        ok, msg = fn()
        print(f"   返回: {msg}")
        if ok:
            print(f"   等待5秒检查进程...")
            proc_ok, info = check_after_start()
            if proc_ok:
                print(f"   ✅ 启动成功! PID={info}")
                sys.exit(0)
            else:
                print(f"   ⚠️ 启动命令OK但进程未出现: {info}")
                continue

    print(f"\n🚨 全部方式失败, 请手动检查")
    sys.exit(1)
