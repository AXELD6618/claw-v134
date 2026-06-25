"""V13.4 桌面通知监控守护进程 — 实时推送圣杯信号"""
import json, os, time, sys
from datetime import datetime

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'fullmarket_cache')
POLL_INTERVAL = 10  # seconds

def send_notification(title, body):
    """Windows toast notification via PowerShell"""
    try:
        ps_script = f'''
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textNodes = $template.GetElementsByTagName("text")
        $textNodes[0].AppendChild($template.CreateTextNode("{title}")) > $null
        $textNodes[1].AppendChild($template.CreateTextNode("{body}")) > $null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("V13.4 毕方灵犀").Show($toast)
        '''
        os.system(f'powershell -Command "{ps_script}" 2>nul')
    except:
        pass

def load_last_state():
    """Load the last known state to detect changes"""
    state_file = os.path.join(CACHE_DIR, '.watchdog_last.json')
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return json.load(f)
    return {'last_holy_count': 0, 'last_signal_count': 0, 'last_top_code': ''}

def save_last_state(state):
    state_file = os.path.join(CACHE_DIR, '.watchdog_last.json')
    with open(state_file, 'w') as f:
        json.dump(state, f)

def find_latest_state():
    """Find the most recent state file"""
    if not os.path.exists(CACHE_DIR):
        return None
    files = sorted([f for f in os.listdir(CACHE_DIR) if f.startswith('state_') and f.endswith('.json') and 'watchdog' not in f],
                   reverse=True)
    if not files:
        return None
    return os.path.join(CACHE_DIR, files[0])

def main():
    print(f"🦅 V13.4 桌面通知守护进程启动")
    print(f"   监控目录: {CACHE_DIR}")
    print(f"   轮询间隔: {POLL_INTERVAL}秒")
    print(f"   [Ctrl+C 退出]")
    sys.stdout.flush()
    
    last_state = load_last_state()
    cycle = 0
    
    while True:
        try:
            state_path = find_latest_state()
            if not state_path:
                time.sleep(POLL_INTERVAL)
                continue
            
            with open(state_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            holy_count = state.get('holy_grail_count', 0) or state.get('summary', {}).get('holy_grail_count', 0)
            signal_count = len(state.get('top_stocks', []))
            if signal_count == 0:
                # fallback: count from summary dict
                signal_count = len(state.get('summary', {}))
            top_stocks = state.get('top_stocks', [])
            top_code = top_stocks[0]['code'] if top_stocks else ''
            
            # Detect Holy Grail signal change
            if holy_count > last_state['last_holy_count']:
                new_count = holy_count - last_state['last_holy_count']
                top_holy = [s for s in top_stocks if s.get('alert_level', '').find('HOLY') >= 0 or s.get('v132_score', 0) > 0.75][:3]
                names = ', '.join(f"{s['code']} {s['name']}" for s in top_holy)
                msg = f"{new_count}只圣杯信号: {names}" if names else f"{new_count}只圣杯信号触发!"
                send_notification(f"🏆 圣杯信号! ({holy_count}只)", msg)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔔 HOLY GRAIL: {msg}")
                sys.stdout.flush()
            
            # Detect new top signal
            if top_code != last_state.get('last_top_code', '') and signal_count > 0:
                top = top_stocks[0]
                if top.get('v132_score', 0) > 0.6:
                    send_notification(
                        f"⚡ {top_code} {top.get('name','')}",
                        f"v132={top.get('v132_score',0):.4f} 跌幅{top.get('decline_pct',0):+.2f}% | {top.get('tier','')}档信号"
                    )
            
            # Save state
            last_state['last_holy_count'] = holy_count
            last_state['last_signal_count'] = signal_count
            last_state['last_top_code'] = top_code
            save_last_state(last_state)
            
            cycle += 1
            if cycle % 6 == 0:  # Every minute
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 监控中... ({holy_count}圣杯/{signal_count}信号)")
                sys.stdout.flush()
            
            time.sleep(POLL_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n守护进程已停止")
            break
        except Exception as e:
            print(f"[ERROR] {e}")
            sys.stdout.flush()
            time.sleep(POLL_INTERVAL * 3)

if __name__ == '__main__':
    main()
