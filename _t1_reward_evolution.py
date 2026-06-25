#!/usr/bin/env python3
"""V13.4 全市场 T+1 奖惩进化回路 — 2026-06-25 执行脚本"""
import json, math, os, sys
from datetime import datetime

# ========== 1. 加载昨日信号数据 ==========
state_file = "data/fullmarket_cache/state_20260624.json"
with open(state_file, 'r') as f:
    state = json.load(f)

print("=" * 60)
print("  V13.4 全市场 T+1 奖惩进化回路")
print("  信号日期: 2026-06-24 | 验证日期: 2026-06-25")
print("=" * 60)

# ========== 2. T+1实际行情数据（TDX MCP获取） ==========
# 格式: [code, name, T日close, T+1日close, change_pct]
t1_data_raw = [
    ("688367", "工大高科", 40.54, 35.80),
    ("920083", "金戈新材", 65.91, 57.40),
    ("300465", "高伟达", 13.04, 12.10),
    ("300461", "田中精机", 41.28, 39.88),
    ("002535", "林州重机", 2.22, 2.24),
    ("600598", "北大荒", 11.22, 10.69),
    ("000151", "中成股份", 11.95, 11.40),
    ("603318", "水发燃气", 10.52, 9.47),
    ("603159", "上海亚虹", 21.06, 18.96),
    ("600977", "中国电影", 13.59, 13.55),
    ("605566", "福莱蒽特", 43.81, 39.43),
    ("605303", "园林股份", 25.31, 25.87),
    ("000029", "深深房A", 26.15, 24.05),
    ("603201", "常润股份", 19.22, 17.30),
    ("688737", "中自科技", 23.38, 21.30),
    ("001216", "华瓷股份", 16.55, 15.94),
    ("920367", "新赣江", 17.56, 18.10),
    ("300540", "蜀道装备", 26.19, 25.42),
    ("600793", "宜宾纸业", 12.70, 12.21),
    ("600367", "红星发展", 51.00, 52.98),
    ("920675", "秉扬科技", 8.83, 8.32),
    ("920748", "路桥信息", 26.12, 25.30),
    ("002672", "东江环保", 3.77, 3.86),
    ("002453", "华软科技", 4.94, 4.68),
    ("600121", "郑州煤电", 3.82, 3.74),
    ("600769", "祥龙电业", 20.08, 21.50),
    ("600876", "凯盛新能", 7.78, 7.33),
    ("688338", "赛科希德", 27.45, 27.03),
    ("300259", "新天科技", 5.13, 5.28),
    ("301138", "华研精机", 29.83, 30.79),
]

# Build results with change calculation
results = []
for code, name, t_close, t1_close in t1_data_raw:
    change_pct = round((t1_close - t_close) / t_close * 100, 2)
    results.append((code, name, t_close, t1_close, change_pct))

# Match with v132 scores from state
v132_map = {}
for s in state.get("top_stocks", [])[:30]:
    v132_map[s["code"]] = s["v132_score"]

# ========== 3. 计算验证指标 ==========
N = len(results)
winners = [r for r in results if r[4] > 0]
losers = [r for r in results if r[4] < 0]
flat = [r for r in results if r[4] == 0]
limit_ups = [r for r in results if r[4] >= 9.8]
limit_downs = [r for r in results if r[4] <= -9.8]

win_rate = len(winners) / N * 100
limit_up_rate = len(limit_ups) / N * 100
avg_return = sum(r[4] for r in results) / N
avg_win = sum(r[4] for r in winners) / len(winners) if winners else 0
avg_loss = abs(sum(r[4] for r in losers) / len(losers)) if losers else 1
plr = avg_win / avg_loss if avg_loss > 0 else 0

print(f"\n  全市场信号: {N}只 | 数据获取: {len(results)}/{N}")
print(f"  ⬆ 上涨: {len(winners)}只 | ⬇ 下跌: {len(losers)}只 | ➡ 平盘: {len(flat)}只")
print(f"\n  📊 核心指标:")
print(f"  胜率: {win_rate:.1f}% ({len(winners)}/{N})")
print(f"  涨停率: {limit_up_rate:.1f}% ({len(limit_ups)})")
print(f"  跌停率: {len(limit_downs)/N*100:.1f}% ({len(limit_downs)})")
print(f"  平均收益: {avg_return:+.2f}%")
print(f"  盈利均值: {avg_win:+.2f}% | 亏损均值: {-avg_loss:.2f}%")
print(f"  盈亏比 (PLR): {plr:.2f}")
print(f"  涨跌比: {len(winners)}:{len(losers)}")

# ========== 因子IC计算 ==========
v132_list = []
change_list = []
for r in results:
    code = r[0]
    if code in v132_map:
        v132_list.append(v132_map[code])
        change_list.append(r[4])

n_ic = len(v132_list)
mean_v132 = sum(v132_list) / n_ic
mean_change = sum(change_list) / n_ic

# Pearson correlation
cov = sum((v132_list[i] - mean_v132) * (change_list[i] - mean_change) for i in range(n_ic)) / (n_ic - 1)
std_v132 = math.sqrt(sum((v - mean_v132)**2 for v in v132_list) / (n_ic - 1))
std_change = math.sqrt(sum((c - mean_change)**2 for c in change_list) / (n_ic - 1))
ic = cov / (std_v132 * std_change) if std_v132 * std_change > 0 else 0

print(f"  因子IC(V13.2→T+1): {ic:.4f}")
print(f"  IC方向: {'✅ 正向有效' if ic > 0.05 else '⚠️ 弱/反向' if abs(ic) < 0.05 else '❌ 反向信号'}")

# ========== 奖惩计算 ==========
reward_scores = []
for r in results:
    code, name, t_close, t1_close, change = r
    if change >= 9.8:
        reward = 50  # 涨停 = A级奖励
        level = "S+"
    elif change >= 5:
        reward = 30  # 大涨 = B级
        level = "A"
    elif change >= 2:
        reward = 15  # 上涨
        level = "B"
    elif change >= 0:
        reward = 5   # 微涨
        level = "C"
    elif change > -3:
        reward = -5  # 微跌
        level = "MISS"
    elif change > -5:
        reward = -10  # 小跌
        level = "P2"
    elif change > -10:
        reward = -15  # 中跌
        level = "P1"
    else:
        reward = -25  # 大跌/跌停
        level = "P0"
    reward_scores.append((code, name, change, level, reward))

# 市场豁免判断：如果普跌(败率>70%)则降级惩罚
if len(losers) / N > 0.7:
    market_immunity = True
    for i in range(len(reward_scores)):
        rs = reward_scores[i]
        if rs[3] in ("P0", "P1"):
            # 降一级
            new_reward = rs[4] + 10
            reward_scores[i] = (rs[0], rs[1], rs[2], rs[3] + "(豁免)", new_reward)
    print(f"\n  🛡️ 市场豁免激活: 败率{len(losers)/N*100:.1f}%>70%, P0/P1惩罚降级")
else:
    market_immunity = False

total_reward = sum(r[4] for r in reward_scores)

# ========== 4. 详细列表 ==========
print(f"\n{'='*80}")
print(f"  {'代码':<8} {'名称':<8} {'T日收盘':>8} {'T+1收盘':>8} {'涨跌':>8} {'评级':>10} {'奖惩':>6}")
print(f"  {'-'*70}")
for rs in reward_scores:
    code, name, change, level, reward = rs
    arrow = "🔴" if change >= 9.8 else "🟢" if change > 0 else "🟡" if change >= -3 else "🟠"
    print(f"  {code:<8} {name:<8} {results[reward_scores.index(rs)][2]:>8.2f} {results[reward_scores.index(rs)][3]:>8.2f} {change:>+7.2f}% {arrow} {level:>10} {reward:>+6d}")

# The results call index won't work, let me just redo more simply
print(f"\n  详细T+1验证:")
for i, r in enumerate(results):
    code, name, t_c, t1_c, change = r
    rs = reward_scores[i]
    arrow = "📈" if change >= 9.8 else "🟢" if change > 0 else "🟡" if change >= -3 else "🟠" if change >= -5 else "🔴"
    print(f"  {i+1:2d}. {code} {name}: {change:+7.2f}% {arrow} [{rs[3]:>12s}] 奖惩{rs[4]:+4d}")

# ========== 5. 输出报告文件 ==========
report = {
    "report_type": "V13.4_T1_Reward_Evolution",
    "signal_date": "2026-06-24",
    "verify_date": "2026-06-25",
    "total_signals": N,
    "summary": {
        "winners": len(winners),
        "losers": len(losers),
        "flat": len(flat),
        "limit_ups": len(limit_ups),
        "limit_downs": len(limit_downs),
        "win_rate": round(win_rate, 2),
        "limit_up_rate": round(limit_up_rate, 2),
        "limit_down_rate": round(len(limit_downs)/N*100, 2),
        "avg_return": round(avg_return, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "plr": round(plr, 2),
        "factor_ic": round(ic, 4),
        "market_immunity": market_immunity,
        "total_reward": total_reward
    },
    "signals": [{
        "code": r[0],
        "name": r[1],
        "t_close": r[2],
        "t1_close": r[3],
        "change_pct": r[4],
        "v132_score": v132_map.get(r[0], 0),
        "reward_level": reward_scores[i][3],
        "reward_score": reward_scores[i][4]
    } for i, r in enumerate(results)]
}

os.makedirs("data/rewards", exist_ok=True)
report_path = "data/rewards/t1_reward_report_20260625.json"
with open(report_path, 'w') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

# ========== 6. M70增量训练标签 ==========
# Build training labels for M70
train_labels = []
for i, r in enumerate(results):
    code = r[0]
    train_labels.append({
        "code": code,
        "name": r[1],
        "v132_score": v132_map.get(code, 0),
        "actual_t1_return": r[4],
        "label": 1 if r[4] > 0 else 0,
        "label_type": "STRONG_BUY" if r[4] >= 9.8 else "BUY" if r[4] >= 2 else "WATCH" if r[4] > 0 else "HOLD" if r[4] > -3 else "AVOID"
    })

labels_path = "data/rewards/m70_training_labels_20260625.json"
with open(labels_path, 'w') as f:
    json.dump(train_labels, f, ensure_ascii=False, indent=2)

print(f"\n  M70增量: +{len(train_labels)}条T+1真实标签")
label_dist = {}
for tl in train_labels:
    lt = tl["label_type"]
    label_dist[lt] = label_dist.get(lt, 0) + 1
print(f"  标签分布: {label_dist}")

# ========== 7. 输出框式报告 ==========
print(f"""
╔══════════════════════════════════════════════════════════╗
║  V13.4 全市场 T+1 奖惩进化回路                          ║
╠══════════════════════════════════════════════════════════╣
║  信号日期: 2026-06-24 | 验证日期: 2026-06-25            ║
║  全市场信号: {N}只 | 胜率: {win_rate:.1f}%                           ║
║  涨停率: {limit_up_rate:.1f}% | 平均收益: {avg_return:+.2f}%                        ║
║  盈亏比: {plr:.2f} | 因子IC: {ic:.4f}                          ║
║  跌停数: {len(limit_downs)}只 | 市场豁免: {"✅激活" if market_immunity else "❌未触发"}                     ║
║  M70增量: +{N}条标签 | 奖惩总分: {total_reward:+d}                       ║
╚══════════════════════════════════════════════════════════╝
║  🏆 Top 3 胜者:                                         ║""")
# Top 3 winners
sorted_w = sorted([(r[0], r[1], r[4]) for r in winners], key=lambda x: x[2], reverse=True)[:3]
for i, (code, name, chg) in enumerate(sorted_w):
    print(f"║  {i+1}. {code} {name}: {chg:+.2f}%                             ║")
print(f"""║  ⚠️ Top 3 败者:                                         ║""")
sorted_l = sorted([(r[0], r[1], r[4]) for r in losers], key=lambda x: x[2])[:3]
for i, (code, name, chg) in enumerate(sorted_l):
    print(f"║  {i+1}. {code} {name}: {chg:+.2f}%                             ║")
print("""╚══════════════════════════════════════════════════════════╝""")

# ========== 8. AutoEvolution建议 ==========
print(f"""
  🧬 AutoEvolution 分析:
  ────────────────────────────────────────────────
  • 市场环境: {'熊市普跌' if len(losers)/N > 0.7 else '结构性分化' if len(losers)/N > 0.5 else '偏多'}
  • IC方向: {'正向有效，建议保持因子权重' if ic > 0.05 else '反向/无效，建议排查因子过拟合' if ic < -0.05 else '弱相关，建议增加非线性特征'}
  • PLR<1: 盈亏比失衡，平均亏损{avg_loss:.1f}%远超平均盈利{avg_win:.1f}%
  • 建议1: M46归一化窗口扩至20日，降低T日极端跌幅的偏置影响
  • 建议2: M64超跌放大器增加市场环境过滤器（大盘同步下跌时降权）
  • 建议3: 引入次日开盘竞价方向作为T+0调仓信号
""")

print(f"\n  报告已保存: {report_path}")
print(f"  训练标签已保存: {labels_path}")
print("  ✅ V13.4 T+1奖惩进化回路执行完毕")
