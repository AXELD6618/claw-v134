#!/usr/bin/env python3
"""
TDX Data Fetcher - Helper module
Saves TDX data to JSON files and updates backtest_tasks table
"""
import sqlite3
import json
import os
from datetime import datetime
from pathlib import Path

DB_PATH = "data/sentiment_db.db"
DATA_DIR = "data/tdx_history"
LOG_PATH = "logs/tdx_data_fetch_error.log"

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs("logs", exist_ok=True)

def save_stock_data(stock_code, stock_name, start_date, end_date,
                    daily_kline, minute_kline, realtime_quote, fetch_time):
    """Save complete stock data to JSON file"""
    ensure_dirs()
    file_path = os.path.join(DATA_DIR, f"{stock_code}_{start_date}_{end_date}.json")
    data = {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "start_date": start_date,
        "end_date": end_date,
        "daily_kline": daily_kline,
        "minute_5kline": minute_kline,  # Note: TDX MCP doesn't have 1-min, uses 5-min
        "realtime_quote": realtime_quote,
        "fetch_time": fetch_time,
        "data_source": "TDX_MCP"
    }
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return file_path

def update_task_status(task_id, status, data_file=None, error_msg=None):
    """Update task status in DB"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if error_msg:
        cur.execute('''UPDATE backtest_tasks
                       SET status=?, data_file=?, updated_at=?
                       WHERE id=?''',
                    (status, data_file, now, task_id))
    else:
        cur.execute('''UPDATE backtest_tasks
                       SET status=?, data_file=?, updated_at=?
                       WHERE id=?''',
                    (status, data_file, now, task_id))
    conn.commit()
    conn.close()

def log_error(msg):
    """Log error to file"""
    ensure_dirs()
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

def get_pending_tasks():
    """Get all pending tasks"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''SELECT id, stock_code, stock_name, start_date, end_date, v132_score
                   FROM backtest_tasks
                   WHERE status="pending"
                   ORDER BY id''')
    tasks = cur.fetchall()
    conn.close()
    return tasks

def generate_report():
    """Generate execution report"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*), status FROM backtest_tasks GROUP BY status')
    status_counts = dict(cur.fetchall())
    cur.execute('''SELECT stock_code, data_file, status FROM backtest_tasks
                   WHERE data_file IS NOT NULL ORDER BY id''')
    files = cur.fetchall()
    conn.close()
    return status_counts, files

def get_setcode(code):
    """Determine market code from stock code"""
    if code.startswith(('600', '601', '603', '605', '688')):
        return '1'  # Shanghai
    elif code.startswith(('000', '001', '002', '003', '300', '301')):
        return '0'  # Shenzhen
    elif code.startswith(('8', '4', '92')):
        return '2'  # Beijing
    return '0'

if __name__ == "__main__":
    ensure_dirs()
    tasks = get_pending_tasks()
    print(f"Pending tasks: {len(tasks)}")
    for t in tasks[:5]:
        print(f"  {t}")
