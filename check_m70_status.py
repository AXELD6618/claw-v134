#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Check M70 training data status + Fetch news data
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta

# ═════════════════════════════════════
# 1. Check daily_signals table (M70 training data)
# ═════════════════════════════════════

db_path = 'data/holy_grail.db'
print("=" * 60)
print("1. Checking daily_signals table (M70 training data)")
print("=" * 60)

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_signals'")
    if cursor.fetchone():
        # Get table schema
        cursor.execute("PRAGMA table_info(daily_signals)")
        cols = [r[1] for r in cursor.fetchall()]
        print(f"daily_signals columns ({len(cols)}): {', '.join(cols)}")
        
        # Get recent signals with T+1 data
        cursor.execute("""
            SELECT signal_date, code, name, v132_score, t1_actual_return, t1_verified
            FROM daily_signals 
            WHERE signal_date >= '2026-06-20'
            ORDER BY signal_date DESC, v132_score DESC
            LIMIT 20
        """)
        rows = cursor.fetchall()
        print(f"\nFound {len(rows)} signals with T+1 data (>=2026-06-20):")
        for r in rows:
            print(f"  {r[0]} {r[1]} {r[2]}: v132={r[3]:.4f} t1_ret={r[4]} verified={r[5]}")
        
        # Count all signals
        cursor.execute("SELECT COUNT(*) FROM daily_signals")
        total = cursor.fetchone()[0]
        print(f"\nTotal signals in daily_signals: {total}")
        
        # Count signals with T+1 data
        cursor.execute("SELECT COUNT(*) FROM daily_signals WHERE t1_actual_return IS NOT NULL")
        with_t1 = cursor.fetchone()[0]
        print(f"Signals with T+1 data: {with_t1}")
    else:
        print("daily_signals table not found")
        # List all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        print(f"Available tables: {', '.join(tables)}")
    
    conn.close()
else:
    print(f"DB not found: {db_path}")

# ═════════════════════════════════════
# 2. Fetch news data from sentiment_db.db
# ═════════════════════════════════════

db_path2 = 'data/sentiment_db.db'
print("\n" + "=" * 60)
print("2. Fetching news data from sentiment_db.db")
print("=" * 60)

if os.path.exists(db_path2):
    conn2 = sqlite3.connect(db_path2)
    cursor2 = conn2.cursor()
    
    # List all tables
    cursor2.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in cursor2.fetchall()]
    print(f"Tables in sentiment_db.db: {', '.join(tables)}")
    
    # Check english_news table
    if 'english_news' in tables:
        cursor2.execute("SELECT COUNT(*) FROM english_news")
        cnt = cursor2.fetchone()[0]
        print(f"\nenglish_news: {cnt} records")
        
        # Get recent news
        cursor2.execute("""
            SELECT title, publish_time, sentiment_score, impact_score
            FROM english_news
            WHERE date(publish_time) >= '2026-06-20'
            ORDER BY publish_time DESC
            LIMIT 10
        """)
        rows2 = cursor2.fetchall()
        print(f"Recent news (>=2026-06-20): {len(rows2)}")
        for r in rows2:
            print(f"  {r[1]}: {r[0][:40]} sentiment={r[2]} impact={r[3]}")
    
    conn2.close()
else:
    print(f"DB not found: {db_path2}")

# ═════════════════════════════════════
# 3. Check M70 model status
# ═════════════════════════════════════

model_path = 'data/M70_lgb_model.pkl'
print("\n" + "=" * 60)
print("3. Checking M70 model status")
print("=" * 60)

if os.path.exists(model_path):
    import pickle
    with open(model_path, 'rb') as f:
        saved = pickle.load(f)
    print(f"Model loaded: {model_path}")
    print(f"Training samples: {len(saved.get('samples', []))}")
    print(f"Feature importance: {saved.get('importance', {})}")
    print(f"Updated at: {saved.get('updated_at', 'unknown')}")
else:
    print(f"Model not found: {model_path}")
    print("Will train new model with available data")

print("\nDone!")
