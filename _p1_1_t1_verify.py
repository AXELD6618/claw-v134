#!/usr/bin/env python3
"""P1-1 T+1 Verification Pipeline - 2026-06-24 Maiden Run"""
import sqlite3, json, math, os, sys
from datetime import datetime
from collections import OrderedDict

# ============================================================
# Step 1: Load signals from DB
# ============================================================
conn = sqlite3.connect('data/holy_grail.db')
cursor = conn.execute("SELECT * FROM daily_signals WHERE signal_date='2026-06-24' ORDER BY v132_score DESC")
cols = [d[0] for d in cursor.description]
signals = [dict(zip(cols, r)) for r in cursor.fetchall()]

print(f"[STEP 1] Loaded {len(signals)} signals from daily_signals (2026-06-24)")

# ============================================================
# Step 2: TDX real kline data (fetched live from TDX MCP)
# ============================================================
# All data is from 2026-06-24 (latest available).
# T+1 date 2026-06-25 has NOT occurred yet.
# We store 2026-06-24 close data and mark T+1 as pending.

tdx_data = {
    "600977": {"name":"中国电影","t_close":15.10,"t_open":14.60,"t_high":14.70,"t_low":13.59,"t_now":13.59,"t_change":-10.00,"t_hsl":5.21},
    "300540": {"name":"蜀道装备","t_close":28.77,"t_open":27.48,"t_high":27.75,"t_low":25.20,"t_now":26.19,"t_change":-8.97,"t_hsl":11.26},
    "688367": {"name":"工大高科","t_close":47.66,"t_open":48.20,"t_high":48.99,"t_low":39.08,"t_now":40.54,"t_change":-14.94,"t_hsl":14.63},
    "301231": {"name":"荣信文化","t_close":31.66,"t_open":31.35,"t_high":31.69,"t_low":28.04,"t_now":29.31,"t_change":-7.42,"t_hsl":15.72},
    "605566": {"name":"福莱蒽特","t_close":48.68,"t_open":47.52,"t_high":47.92,"t_low":43.81,"t_now":43.81,"t_change":-10.00,"t_hsl":6.55},
    "300465": {"name":"高伟达","t_close":14.70,"t_open":14.50,"t_high":14.70,"t_low":12.96,"t_now":13.04,"t_change":-11.29,"t_hsl":8.32},
    "300461": {"name":"田中精机","t_close":45.95,"t_open":45.63,"t_high":45.89,"t_low":40.69,"t_now":41.28,"t_change":-10.16,"t_hsl":8.45},
    "000151": {"name":"中成股份","t_close":13.28,"t_open":13.14,"t_high":13.14,"t_low":11.95,"t_now":11.95,"t_change":-10.02,"t_hsl":6.52},
    "600255": {"name":"鑫科材料","t_close":4.58,"t_open":4.44,"t_high":4.56,"t_low":4.12,"t_now":4.20,"t_change":-8.30,"t_hsl":18.51},
    "688737": {"name":"中自科技","t_close":25.88,"t_open":25.57,"t_high":25.90,"t_low":23.03,"t_now":23.38,"t_change":-9.66,"t_hsl":4.60},
    "600121": {"name":"郑州煤电","t_close":4.18,"t_open":4.15,"t_high":4.16,"t_low":3.77,"t_now":3.82,"t_change":-8.61,"t_hsl":8.38},
    "002453": {"name":"华软科技","t_close":5.41,"t_open":5.36,"t_high":5.36,"t_low":4.87,"t_now":4.94,"t_change":-8.69,"t_hsl":9.37},
    "301138": {"name":"华研精机","t_close":32.56,"t_open":32.40,"t_high":32.68,"t_low":29.21,"t_now":29.83,"t_change":-8.38,"t_hsl":4.98},
    "600793": {"name":"宜宾纸业","t_close":13.95,"t_open":14.02,"t_high":14.02,"t_low":12.63,"t_now":12.70,"t_change":-8.96,"t_hsl":6.78},
    "600403": {"name":"大有能源","t_close":6.23,"t_open":6.23,"t_high":6.23,"t_low":5.61,"t_now":5.71,"t_change":-8.35,"t_hsl":4.25},
    "688338": {"name":"赛科希德","t_close":29.99,"t_open":29.90,"t_high":30.20,"t_low":27.17,"t_now":27.45,"t_change":-8.47,"t_hsl":3.60},
    "002535": {"name":"林州重机","t_close":2.47,"t_open":2.45,"t_high":2.48,"t_low":2.22,"t_now":2.22,"t_change":-10.12,"t_hsl":3.51},
    "600769": {"name":"祥龙电业","t_close":21.97,"t_open":21.92,"t_high":22.60,"t_low":19.77,"t_now":20.08,"t_change":-8.60,"t_hsl":13.91},
    "600367": {"name":"红星发展","t_close":56.00,"t_open":53.00,"t_high":53.98,"t_low":50.66,"t_now":51.00,"t_change":-8.93,"t_hsl":18.39},
    "002672": {"name":"东江环保","t_close":4.13,"t_open":4.05,"t_high":4.06,"t_low":3.73,"t_now":3.77,"t_change":-8.72,"t_hsl":5.29},
    "600876": {"name":"凯盛新能","t_close":8.51,"t_open":8.36,"t_high":8.38,"t_low":7.69,"t_now":7.78,"t_change":-8.58,"t_hsl":4.19},
    "603070": {"name":"万控智造","t_close":15.28,"t_open":15.00,"t_high":15.00,"t_low":13.90,"t_now":14.04,"t_change":-8.12,"t_hsl":3.24},
    "603318": {"name":"水发燃气","t_close":11.69,"t_open":10.99,"t_high":10.99,"t_low":10.52,"t_now":10.52,"t_change":-10.01,"t_hsl":7.93},
    "600322": {"name":"津投城开","t_close":2.93,"t_open":2.90,"t_high":2.93,"t_low":2.68,"t_now":2.72,"t_change":-7.17,"t_hsl":10.97},
    "600598": {"name":"北大荒","t_close":12.47,"t_open":11.22,"t_high":11.65,"t_low":11.22,"t_now":11.22,"t_change":-10.02,"t_hsl":3.45},
    "600456": {"name":"宝钛股份","t_close":31.57,"t_open":28.66,"t_high":29.27,"t_low":28.46,"t_now":28.99,"t_change":-8.17,"t_hsl":4.70},
    "603311": {"name":"金海高科","t_close":36.64,"t_open":36.49,"t_high":36.49,"t_low":33.63,"t_now":33.98,"t_change":-7.26,"t_hsl":7.90},
    "000802": {"name":"北京文化","t_close":4.01,"t_open":3.99,"t_high":4.01,"t_low":3.67,"t_now":3.71,"t_change":-7.48,"t_hsl":6.04},
    "600280": {"name":"中央商场","t_close":2.98,"t_open":2.95,"t_high":2.96,"t_low":2.72,"t_now":2.75,"t_change":-7.72,"t_hsl":6.43},
    "688087": {"name":"英科再生","t_close":38.22,"t_open":37.92,"t_high":38.66,"t_low":34.59,"t_now":35.19,"t_change":-7.93,"t_hsl":2.47},
}

T1_DATE = "2026-06-25"
T1_PENDING = True  # June 25 hasn't occurred yet

print(f"[STEP 2] TDX data loaded for {len(tdx_data)} stocks (latest: 2026-06-24)")
print(f"[WARNING] T+1 date {T1_DATE} has NOT occurred yet (current time: {datetime.now().strftime('%Y-%m-%d %H:%M')})")
print(f"[WARNING] Using 2026-06-24 close data for pipeline validation; T+1 verification PENDING")

# ============================================================
# Step 3: Compute T+1 verification (June 24 closes → June 25 T+1)
# ============================================================
results = []

for s in signals:
    code = s['code']
    td = tdx_data.get(code, {})
    
    t_close = td.get('t_close', 0)      # June 23 close (previous)
    t_now = td.get('t_now', 0)           # June 24 close
    t_change = td.get('t_change', 0)     # June 24 daily change
    
    # Buy entry: close price at signal time (14:30-15:00 June 24)
    entry_price = t_now  # We buy at June 24 close
    
    # T+1 verification: PENDING - June 25 hasn't occurred yet
    # For now, compute what we know
    actual_t1_change = None
    actual_t1_open = None
    actual_t1_close = None
    
    result = {
        'code': code,
        'name': s['name'],
        'recommendation': s['recommendation'],
        'tier': s['tier'],
        'v132_score': s['v132_score'],
        'm46_score': s['m46_score'],
        'm57_score': s['m57_score'],
        'm64_score': s['m64_score'],
        'change_pct': s['change_pct'],       # drop on signal day
        'sector_heat': s['sector_heat'],
        'data_quality': s['data_quality'],
        # Entry data (June 24 close)
        'entry_price': round(entry_price, 2),
        'entry_date': '2026-06-24',
        'prev_close': t_close,
        't_day_change': t_change,            # intraday change on June 24
        # T+1 data - PENDING
        't1_date': T1_DATE,
        'actual_t1_change': actual_t1_change,
        'actual_t1_open': actual_t1_open,
        'actual_t1_close': actual_t1_close,
        'was_hit': None,
        'was_limit_up': None,
        'was_stop_loss': None,
        't1_status': 'PENDING_2026-06-25',
    }
    results.append(result)

print(f"\n[STEP 3] Computed entry data for {len(results)} stocks")
print(f"  T+1 status: ALL PENDING (June 25 market not open yet)")

# ============================================================
# Step 4: KPI Summary (using available data)
# ============================================================
n_total = len(results)
n_tier1 = sum(1 for r in results if r['tier'] == 'Tier1')
n_tier0 = sum(1 for r in results if r['tier'] == 'Tier0')
n_sb = sum(1 for r in results if r['recommendation'] == 'STRONG_BUY')

# Signal day stats (June 24 itself)
t_day_changes = [r['t_day_change'] for r in results if r['t_day_change'] is not None]
avg_t_day_change = sum(t_day_changes) / len(t_day_changes) if t_day_changes else 0

# Score distribution
scores = [r['v132_score'] for r in results]
avg_score = sum(scores) / len(scores) if scores else 0
min_score = min(scores) if scores else 0
max_score = max(scores) if scores else 0

print(f"\n[STEP 4] KPI Summary (Pending T+1)")
print(f"  Total signals: {n_total}")
print(f"  Tier1: {n_tier1} | Tier0: {n_tier0}")
print(f"  STRONG_BUY: {n_sb}")
print(f"  Avg V13.2 Score: {avg_score:.4f} (range {min_score:.4f}-{max_score:.4f})")
print(f"  Avg T-day change: {avg_t_day_change:.2f}%")
print(f"  T+1 Win Rate: PENDING")
print(f"  T+1 Avg Return: PENDING")
print(f"  T+1 PLR: PENDING")
print(f"  T+1 Limit-Up Rate: PENDING")
print(f"  Factor IC: PENDING")

# ============================================================
# Step 5: Reward Engine (placeholder for now)
# ============================================================
print(f"\n[STEP 5] Reward Engine - DEFERRED")
print(f"  T+1 data not available yet, rewards will be computed on {T1_DATE} at 15:10")

# ============================================================
# Step 6: Write p1_1_tracking records
# ============================================================
# First, clear existing records for 2026-06-24
conn.execute("DELETE FROM p1_1_tracking WHERE signal_date='2026-06-24'")
conn.commit()

for r in results:
    conn.execute("""
        INSERT INTO p1_1_tracking 
        (signal_date, code, name, recommendation, v132_score, 
         predicted_t1_change, actual_t1_change, actual_t1_open, actual_t1_close,
         was_hit, was_limit_up, was_stop_loss, tracking_notes, verified_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'))
    """, (
        '2026-06-24', r['code'], r['name'], r['recommendation'], r['v132_score'],
        None,  # predicted_t1_change (not computed on signal day)
        r['actual_t1_change'],
        r['actual_t1_open'],
        r['actual_t1_close'],
        r['was_hit'],
        r['was_limit_up'],
        r['was_stop_loss'],
        f"T+1({T1_DATE}) PENDING - market not open yet. Entry@{r['entry_price']}. T-day change: {r['t_day_change']}%",
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    ))

conn.commit()
print(f"[STEP 6] Wrote {len(results)} tracking records to p1_1_tracking (T+1 PENDING)")

# Verify
count = conn.execute("SELECT COUNT(*) FROM p1_1_tracking WHERE signal_date='2026-06-24'").fetchone()[0]
print(f"  Verified: {count} records in p1_1_tracking")

# ============================================================
# Step 7: Generate report JSON
# ============================================================
report = {
    "report_type": "P1-1_T+1_Verification",
    "report_version": "V13.2_Maiden_Run",
    "signal_date": "2026-06-24",
    "t1_date": "2026-06-25",
    "report_generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    "t1_status": "PENDING",
    "t1_pending_reason": "Market date is 2026-06-24; T+1 (2026-06-25) has not occurred yet",
    "summary": {
        "total_signals": n_total,
        "tier1_count": n_tier1,
        "tier0_count": n_tier0,
        "strong_buy_count": n_sb,
        "avg_v132_score": round(avg_score, 4),
        "score_range": [round(min_score, 4), round(max_score, 4)],
        "avg_t_day_change": round(avg_t_day_change, 2),
        "t1_win_rate": "PENDING",
        "t1_limit_up_rate": "PENDING",
        "t1_avg_return": "PENDING",
        "t1_plr": "PENDING",
        "t1_factor_ic": "PENDING",
        "reward_score": "PENDING",
    },
    "top10_by_score": [
        {
            "code": r['code'],
            "name": r['name'],
            "v132_score": r['v132_score'],
            "m46_score": r['m46_score'],
            "m57_score": r['m57_score'],
            "t_day_change": r['t_day_change'],
            "entry_price": r['entry_price'],
            "tier": r['tier'],
            "t1_status": r['t1_status'],
        }
        for r in results[:10]
    ],
    "all_signals": results,
    "data_sources": {
        "daily_signals": "data/holy_grail.db::daily_signals",
        "tdx_kline": "TDX MCP real-time (2026-06-24 latest)",
        "p1_1_tracking": "data/holy_grail.db::p1_1_tracking",
    },
    "next_scheduled_check": "2026-06-25 15:10 GMT+8",
}

os.makedirs('data', exist_ok=True)
with open('data/p1_1_t1_report_20260625.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2, default=str)

print(f"\n[STEP 7] Report saved to data/p1_1_t1_report_20260625.json")

# ============================================================
# Print formatted report
# ============================================================
print(f"""
╔══════════════════════════════════════════════════════════╗
║  P1-1 首次实盘验证 T+1 跟踪报告 2026-06-25              ║
╠══════════════════════════════════════════════════════════╣
║  信号日期: 2026-06-24 (周三)                            ║
║  验证日期: 2026-06-25 (周四)                            ║
║  数据源: TDX MCP 实时K线                                ║
║  ⚠️ T+1状态: 待验证 (市场尚未开盘)                      ║
╠══════════════════════════════════════════════════════════╣
║  总推荐: {n_total}只                                           ║
║  STRONG_BUY: {n_sb}只 | Tier1: {n_tier1}只 | Tier0: {n_tier0}只        ║
║  涨停命中率: PENDING | 胜率: PENDING                     ║
║  平均收益: PENDING | 盈亏比: PENDING                     ║
║  因子IC: PENDING | 奖惩得分: PENDING                     ║
║  V13.2 均分: {avg_score:.4f} | T日平均跌幅: {avg_t_day_change:.2f}%        ║
╠══════════════════════════════════════════════════════════╣
║  📋 信号日(6/24)收盘表现 (Top 10)                       ║
╠══════════════════════════════════════════════════════════╣
""")

for i, r in enumerate(results[:10]):
    print(f"║  {i+1:2d}. {r['code']} {r['name']:<6s} │ V13.2:{r['v132_score']:.4f} │ T日:{r['t_day_change']:+.2f}% │ 入场:{r['entry_price']:.2f}")

print("""╠══════════════════════════════════════════════════════════╣
║  ... 剩余20只详见JSON报告                                ║
╠══════════════════════════════════════════════════════════╣
║  ⚠️ 全30只T+1数据待6/25收盘后验证                       ║
╚══════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 P1-1 验证结论
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏳ 涨停命中率: PENDING (等待6/25收盘)
⏳ 盈亏比: PENDING (等待6/25收盘)
⏳ Factor IC: PENDING (等待6/25收盘)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔍 初步观察:
  - 30只信号全部为STRONG_BUY，全部为当日大幅下跌股
  - T日平均跌幅: {:.2f}% (范围 {:.2f}%~{:.2f}%)
  - 均来自M46超跌反转+STAR模式的叠加信号
  - Tier1(10只)高分池 vs Tier0(20只)低分池形成对照
  - 将在6/25验证超跌反弹假设
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 下一步: 6/25 15:10 自动化将触发T+1验证 + 奖惩计算
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""".format(avg_t_day_change, min(t_day_changes), max(t_day_changes)))

conn.close()
print("[DONE] P1-1 T+1 Verification Pipeline complete (T+1 PENDING)")
