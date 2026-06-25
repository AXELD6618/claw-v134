#!/usr/bin/env python3
"""Full WeChat window analysis - check the running xwechat"""
import win32gui
import win32process

# All WeChat-related PIDs
wechat_pids = [4964, 17500, 18048, 19040, 24976, 25664, 25700, 30788, 31796, 33288]

def enum_all(hwnd, results):
    title = win32gui.GetWindowText(hwnd)
    class_name = win32gui.GetClassName(hwnd)
    _, pid = win32process.GetWindowThreadProcessId(hwnd)
    visible = win32gui.IsWindowVisible(hwnd)
    
    if pid in wechat_pids or 'WeChat' in class_name or '微信' in title or 'Weixin' in title:
        rect = win32gui.GetWindowRect(hwnd)
        results.append((hwnd, title, class_name, pid, visible, rect))

results = []
win32gui.EnumWindows(enum_all, results)
print(f'Found {len(results)} WeChat-related windows:')
for hwnd, title, cls, pid, visible, rect in results:
    w = rect[2] - rect[0]
    h = rect[3] - rect[1]
    print(f'  HWND={hwnd} PID={pid} Vis={visible} Class="{cls}" Title="{title}" Size={w}x{h}')

# Check for WeChatMainWndForPC
hwnd1 = win32gui.FindWindow("WeChatMainWndForPC", None)
hwnd2 = win32gui.FindWindow("WeChatMainWnd", None)
hwnd3 = win32gui.FindWindow("Qt51514QWindowIcon", "微信")
hwnd4 = win32gui.FindWindow("Qt51514QWindowIcon", "Weixin")
print(f'\nWeChatMainWndForPC: {hwnd1}')
print(f'WeChatMainWnd: {hwnd2}')
print(f'Qt51514+微信: {hwnd3}')
print(f'Qt51514+Weixin: {hwnd4}')

# Try UIA
try:
    import uiautomation as uia
    print('\n=== UIA Search ===')
    for name in ['微信', 'Weixin', 'WeChat']:
        wc = uia.WindowControl(Name=name, searchDepth=1)
        if wc.Exists(0):
            print(f'Name="{name}": Exists=True ClassName="{wc.ClassName}" AutoId="{wc.AutomationId}"')
        else:
            print(f'Name="{name}": Not found')
except:
    print('uiautomation not available')
