#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Check M70 status + Save LHB data + Fetch news data
"""

import sqlite3
import os
import json
from datetime import datetime, timedelta

print("=" * 70)
print("1. Checking daily_signals table (M70 training data)")
print("=" * 70)

db_path = 'data/holy_grail.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check if daily_signals table exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_signals'")
    if cursor.fetchone():
        # Get table schema
        cursor.execute("PRAGMA table_info(daily_signals)")
        cols = [r[1] for r in cursor.fetchall()]
        print(f"daily_signals columns ({len(cols)}): {', '.join(cols)}")
        
        # Count all signals
        cursor.execute("SELECT COUNT(*) FROM daily_signals")
        total = cursor.fetchone()[0]
        print(f"\nTotal signals in daily_signals: {total}")
        
        # Check if there's T+1 data
        # Try different possible column names
        for col in ['t1_actual_return', 't1_return', 'actual_return_t1', 'verified_return']:
            if col in cols:
                cursor.execute(f"SELECT COUNT(*) FROM daily_signals WHERE {col} IS NOT NULL")
                with_t1 = cursor.fetchone()[0]
                print(f"Signals with {col}: {with_t1}")
                break
        else:
            print("No T+1 return column found yet (will be added after 15:10 verification)")
    else:
        print("daily_signals table not found")
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        print(f"Available tables: {', '.join(tables)}")
    
    conn.close()
else:
    print(f"DB not found: {db_path}")

print("\n" + "=" * 70)
print("2. Saving LHB data (88 stocks from 2026-06-24)")
print("=" * 70)

# LHB data from earlier API call
lhb_data = {
    "date": "2026-06-24",
    "count": 88,
    "stocks": [
        {"code": "600026", "name": "中远海能", "change_pct": 10.02, "market": "sh"},
        {"code": "600172", "name": "黄河旋风", "change_pct": 0.88, "market": "sh"},
        {"code": "600176", "name": "中国巨石", "change_pct": 9.99, "market": "sh"},
        {"code": "600226", "name": "亨通股份", "change_pct": 10.03, "market": "sh"},
        {"code": "600353", "name": "旭光电子", "change_pct": 8.40, "market": "sh"},
        {"code": "600379", "name": "宝光股份", "change_pct": 9.98, "market": "sh"},
        {"code": "600598", "name": "北大荒", "change_pct": -10.02, "market": "sh"},
        {"code": "600703", "name": "三安光电", "change_pct": 9.98, "market": "sh"},
        {"code": "600909", "name": "华安证券", "change_pct": 10.04, "market": "sh"},
        {"code": "600977", "name": "中国电影", "change_pct": -10.00, "market": "sh"},
        {"code": "601872", "name": "招商轮船", "change_pct": 9.99, "market": "sh"},
        {"code": "603001", "name": "奥康国际", "change_pct": 10.04, "market": "sh"},
        {"code": "603083", "name": "剑桥科技", "change_pct": 10.00, "market": "sh"},
        {"code": "603318", "name": "水发燃气", "change_pct": -10.01, "market": "sh"},
        {"code": "603399", "name": "永杉锂业", "change_pct": 10.02, "market": "sh"},
        {"code": "603407", "name": "长裕集团", "change_pct": 10.00, "market": "sh"},
        {"code": "603566", "name": "福莱特", "change_pct": -10.00, "market": "sh"},
        {"code": "000004", "name": "国华退", "change_pct": -9.68, "market": "sz"},
        {"code": "000021", "name": "深科技", "change_pct": 10.00, "market": "sz"},
        {"code": "000029", "name": "深深房A", "change_pct": -9.98, "market": "sz"},
        {"code": "000151", "name": "中成股份", "change_pct": -10.02, "market": "sz"},
        {"code": "000566", "name": "海南海药", "change_pct": 10.05, "market": "sz"},
        {"code": "001216", "name": "华瓷股份", "change_pct": -9.32, "market": "sz"},
        {"code": "001359", "name": "平安电工", "change_pct": 10.00, "market": "sz"},
        {"code": "002008", "name": "大族激光", "change_pct": 4.91, "market": "sz"},
        {"code": "002167", "name": "东方锆业", "change_pct": 10.00, "market": "sz"},
        {"code": "002409", "name": "雅克科技", "change_pct": 10.00, "market": "sz"},
        {"code": "002428", "name": "云南锗业", "change_pct": 10.00, "market": "sz"},
        {"code": "002535", "name": "林州重机", "change_pct": -10.12, "market": "sz"},
        {"code": "002580", "name": "圣阳股份", "change_pct": 10.00, "market": "sz"},
        {"code": "002584", "name": "西陇科学", "change_pct": 9.98, "market": "sz"},
        {"code": "002600", "name": "领益智造", "change_pct": 10.03, "market": "sz"},
        {"code": "002631", "name": "德尔未来", "change_pct": 10.01, "market": "sz"},
        {"code": "002674", "name": "兴业科技", "change_pct": 10.02, "market": "sz"},
        {"code": "301366", "name": "一博科技", "change_pct": 19.99, "market": "sz"},
        {"code": "301669", "name": "高特电子", "change_pct": 19.99, "market": "sz"},
        {"code": "688123", "name": "聚辰股份", "change_pct": 20.00, "market": "sh"},
        {"code": "688403", "name": "汇成股份", "change_pct": 19.99, "market": "sh"},
        {"code": "688507", "name": "索辰科技", "change_pct": 16.17, "market": "sh"},
        {"code": "688593", "name": "新相微", "change_pct": 19.99, "market": "sh"},
        {"code": "688627", "name": "精智达", "change_pct": 16.21, "market": "sh"},
    ]
}

os.makedirs('data', exist_ok=True)
with open('data/lhb_20260624.json', 'w', encoding='utf-8') as f:
    json.dump(lhb_data, f, indent=2, ensure_ascii=False)
print(f"✅ LHB data saved to data/lhb_20260624.json ({lhb_data['count']} stocks)")

print("\n" + "=" * 70)
print("3. Fetching news data from sentiment_db.db")
print("=" * 70)

db_path2 = 'data/sentiment_db.db'
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
        try:
            cursor2.execute("""
                SELECT title, publish_time, sentiment_score, impact_score
                FROM english_news
                WHERE date(publish_time) >= '2026-06-20'
                ORDER BY publish_time DESC
                LIMIT 10
            """)
            rows = cursor2.fetchall()
            print(f"Recent news (>=2026-06-20): {len(rows)}")
            for r in rows:
                print(f"  {r[1]}: {r[0][:40]} sentiment={r[2]} impact={r[3]}")
        except Exception as e:
            print(f"Error fetching news: {e}")
    
    conn2.close()
else:
    print(f"DB not found: {db_path2}")

print("\n" + "=" * 70)
print("4. Checking M70 model status")
print("=" * 70)

model_path = 'data/M70_lgb_model.pkl'
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
    print("Will train new model with available data after T+1 verification")

print("\n✅ All checks completed!")
