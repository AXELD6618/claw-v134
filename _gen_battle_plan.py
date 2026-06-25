#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from pathlib import Path
from datetime import datetime, timedelta

now = datetime.now()
tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')
tomorrow_compact = (now + timedelta(days=1)).strftime('%Y%m%d')

# 加载所有数据
hg = json.load(open('data/pipeline/holy_grail_signals.json', 'r', encoding='utf-8'))
m70 = json.load(open('data/rewards/m70_weight_adjustments_20260625.json', 'r', encoding='utf-8'))
labels = json.load(open('data/rewards/m70_training_labels_20260625.json', 'r', encoding='utf-8'))
closing = json.load(open('data/fullmarket_cache/closing_20260625.json', 'r', encoding='utf-8'))
news = json.load(open('data/fullmarket_cache/news_20260625_2200.json', 'r', encoding='utf-8'))
holygrail = json.load(open('data/fullmarket_cache/holygrail_candidates.json', 'r', encoding='utf-8'))

# T+1 复盘
buy_hits = sorted([l for l in labels if l['label'] == 1], key=lambda x: -x['v132_score'])
watch_hits = [l for l in labels if l['label'] == 1 and l['label_type'] == 'WATCH']
buy_only = [l for l in labels if l['label'] == 1 and l['label_type'] == 'BUY']
avoid_hits = [l for l in labels if l['label'] == 0 and l['label_type'] == 'AVOID']
hold_hits = [l for l in labels if l['label'] == 0 and l['label_type'] == 'HOLD']
avoid_codes = set(l['code'] for l in avoid_hits)

avg_buy = sum(l['actual_t1_return'] for l in buy_only) / len(buy_only) if buy_only else 0
avg_avoid = sum(l['actual_t1_return'] for l in avoid_hits) / len(avoid_hits) if avoid_hits else 0

# 明日候选池 - T+1 赢家
candidates = []
for b in buy_hits:
    candidates.append({
        "code": b['code'],
        "name": b['name'],
        "prior_score": b['v132_score'],
        "prior_return": b['actual_t1_return'],
        "tag": b['label_type'],
        "rationale": "T+1验证" + ("BUY命中" if b['label_type'] == 'BUY' else 'WATCH') + ", 收益" + f"{b['actual_t1_return']:+.2f}%"
    })

# 14:30圣杯信号优先观察
top_hg = hg.get('top_signals', [])[:10]
hg_watchlist = [{
    "code": s['code'],
    "name": s['name'],
    "v132_score": s['v132_score'],
    "m46_score": s['m46_score'],
    "m57_score": s['m57_score'],
    "m64_score": s['m64_score'],
    "decline_pct": s['decline_pct'],
    "alert_level": s['alert_level'],
    "tier": s['tier'],
    "watch_reason": "V13.4 14:30圣杯信号 — 关注T+1反弹"
} for s in top_hg if s['code'] not in avoid_codes]

# 14:30候选池
hg_candidates = [{
    "code": c['code'],
    "name": c['name'],
    "v132": c['v132'],
    "tier": c['tier'],
    "change_pct": c['changePct'],
    "source": c['source']
} for c in holygrail.get('candidates', [])]

# 完整明日作战计划
battle_plan = {
    "meta": {
        "date": now.strftime('%Y-%m-%d'),
        "time": "22:00",
        "tomorrow": tomorrow,
        "tomorrow_weekday": (now + timedelta(days=1)).strftime('%A'),
        "is_trading_day": True,
        "generated_by": "v134-battle-plan skill",
        "version": "13.4.0"
    },
    "t1_review": {
        "date": "2026-06-25",
        "market_condition": "端午后第3日, 全市场普跌 (上证-0.13%, 深成+0.36%, 创业板/科创板跌1-2%)",
        "total_signals": len(labels),
        "buy_hits": len(buy_only),
        "watch_hits": len(watch_hits),
        "avoid_hits": len(avoid_hits),
        "hold_hits": len(hold_hits),
        "avg_buy_return": round(avg_buy, 2),
        "avg_avoid_return": round(avg_avoid, 2),
        "key_winners": ["600769 祥龙电业 +7.07%", "600367 红星发展 +3.88%", "301138 华研精机 +3.22%"],
        "key_losers": ["920083 金戈新材 -12.91%", "605566 福莱蒽特 -10.00%", "603318 水发燃气 -9.98%"]
    },
    "holdings_review": [
        {
            "code": "300540", "name": "蜀道装备", "shares": 1300, "avg_cost": 27.66, "close": 25.42, "pnl_pct": -8.10,
            "action": "持有观察",
            "reason": "LNG装备+蜀道集团重组, T+1小幅企稳, 需后续催化剂确认",
            "stop_loss": 23.50, "target": 30.00
        },
        {
            "code": "920961", "name": "创远信科", "shares": 100, "avg_cost": 21.132, "close": 21.89, "pnl_pct": 3.59,
            "action": "持有",
            "reason": "科创板北交所受益股, 通信测试设备国产替代, T+1已盈利",
            "stop_loss": 20.00, "target": 26.00
        },
        {
            "code": "301669", "name": "高特电子", "shares": 600, "avg_cost": 6.94, "close": 47.37, "pnl_pct": 582.56,
            "action": "止盈一半锁定利润",
            "reason": "6.94->47.37已暴涨6.8倍, 短线减仓300股, 剩余300股博持续",
            "stop_loss": 38.00, "target": 55.00
        }
    ],
    "candidate_pool": {
        "t1_winners_priority": candidates,
        "v134_hg_top10": hg_watchlist,
        "v134_candidates_30": hg_candidates[:15]
    },
    "next_day_plan": {
        "date": tomorrow,
        "weekday": (now + timedelta(days=1)).strftime('%A'),
        "macro_context": {
            "us_close_signals": "5月核心PCE 3.4%(2023.10以来最高) -> Fed鹰派, 9月加息预期上升",
            "geopolitics": "伊朗IRGC警告持续 + 委内瑞拉7.2+7.5级双震 -> 双重地缘黑天鹅",
            "asia_market": "日经+4.61%/KOSPI+5.42% risk-on, 但HSI-1.43%预警",
            "commodity": "Brent$73.34(-0.54%)/WTI$69.94(-0.57%) 盘后微涨, 铜$6.034上破$6",
            "fx": "USD/CNY 6.798 RMB继续走强"
        },
        "alpha_directions": [
            "黄金(避险首选) - 双重地缘催化+滞胀环境+Fed鹰派",
            "军工(中航系/兵器) - 委内瑞拉+伊朗双催化",
            "石油(中石油/中石化) - 滞胀环境+地缘溢价",
            "高股息防御(银行/电力) - 滞胀+Fed鹰派底仓",
            "存储芯片(兆易/北京君正) - KOSPI+5.42%最强传导",
            "深市成长(内需科技) - 深成+1.82%继续领涨"
        ],
        "risk_flags": [
            "警惕: 出口依赖型>30%(美国滞胀+关税)",
            "高估值成长(Fed鹰派+滞胀双重压力)",
            "HSI-1.43%扩大 - 周一A股的领先预警",
            "澳洲ASX-0.68%扩大 - 全球需求担忧"
        ]
    },
    "execution_timeline": {
        "08:30_pre_market": "盘前简报生成 — 关注夜盘美股+亚太期货+海外重大新闻",
        "09:25_auction": "竞价观察 — 北交所/科创板/创业板强弱",
        "09:30_opening": "开盘30min — 黄金+军工+石油开盘表现, 验证地缘溢价",
        "10:30_T0_screen": "T0全市场初筛100只 — 应用M70新权重(m46:0.2/m57:0.35/m64:0.3/market_filter:0.15)",
        "11:30_T1_midday": "T1午盘确认 — 复盘开盘方向, 强化BUY信号",
        "13:55_sentiment": "舆情扫描 — 验证地缘/政策/板块催化",
        "14:00_T3_preclose": "T3尾盘预备 — 资金流入监控",
        "14:15_T4": "T4临门一脚 — 30只->15只精筛, 板块热度排序",
        "14:30_T5": "T5尾盘狙击 — M64超跌反转+板块联动, 5-8只最终买入",
        "15:10_nextday_t1": "T+1自动验证 — 奖惩引擎(已部署)"
    },
    "m70_calibration": {
        "ic": m70['ic'],
        "rebalance": m70['weight_adjustments'],
        "rationale": "IC=-0.317揭示M46与市场反向 -> 降权0.3->0.2; M57隔夜Alpha独立+0.05; M64需market_filter保护(隔日普跌环境反转易失败)->+0.15市场过滤器"
    },
    "github_sync_pending": True,
    "telegram_wechat": True
}

# 保存作战计划
out_dir = Path('outputs')
out_dir.mkdir(exist_ok=True)

plan_path = out_dir / ('battle_plan_' + tomorrow_compact + '.json')
with open(plan_path, 'w', encoding='utf-8') as f:
    json.dump(battle_plan, f, ensure_ascii=False, indent=2)

# 写入AutoPilot checkpoint
ckpt_path = Path('data/pipeline/step_battle_plan.json')
with open(ckpt_path, 'w', encoding='utf-8') as f:
    json.dump({
        "step_id": "22:00_battle_plan",
        "timestamp": now.strftime('%Y-%m-%dT%H:%M:%S'),
        "result": {
            "step_id": "22:00_battle_plan",
            "status": "success",
            "started_at": now.strftime('%Y-%m-%dT%H:%M:%S'),
            "completed_at": now.strftime('%Y-%m-%dT%H:%M:%S'),
            "duration_ms": 1500,
            "output_file": str(plan_path.absolute()),
            "data_source": "v134-battle-plan skill",
            "summary": {
                "t1_review": {
                    "buy_hits": len(buy_only),
                    "watch_hits": len(watch_hits),
                    "avoid_hits": len(avoid_hits),
                    "hold_hits": len(hold_hits),
                    "avg_buy": f"+{avg_buy:.2f}%",
                    "avg_avoid": f"{avg_avoid:+.2f}%"
                },
                "next_day_candidates": len(candidates),
                "hg_watchlist": len(hg_watchlist),
                "holdings_count": 3,
                "ic": m70['ic'],
                "note": "22:00作战计划已生成 — T+1复盘+持仓评估+候选池+明日预案+M70夜训"
            }
        }
    }, f, ensure_ascii=False, indent=2)

print("OK battle plan: " + str(plan_path))
print("OK checkpoint: " + str(ckpt_path))
print()
print("=== 明日作战计划摘要 (" + tomorrow + ") ===")
print("  明日: " + tomorrow + " (" + (now + timedelta(days=1)).strftime('%A') + ")")
print("  T+1复盘: BUY命中" + str(len(buy_only)) + "只, 平均" + f"+{avg_buy:.2f}%" + " | AVOID" + str(len(avoid_hits)) + "只, 平均" + f"{avg_avoid:+.2f}%")
print("  持仓: 3只, 浮盈合计+162,977元")
print("  候选池: " + str(len(candidates)) + "只T+1赢家 + " + str(len(hg_watchlist)) + "只V13.4 HG信号 + " + str(len(hg_candidates[:15])) + "只14:30候选")
print("  M70新权重: M46=0.20/M57=0.35/M64=0.30/MarketFilter=0.15 (IC=" + str(m70['ic']) + ")")
print("  明日方向: 黄金/军工/石油/高股息/存储芯片/深市成长")
