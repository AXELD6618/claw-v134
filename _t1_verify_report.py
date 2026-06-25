#!/usr/bin/env python3
"""T+1 Verification Report Generator - 2026-06-25"""
import json

signals = [
    {'code':'688367','name':'工大高科','t_close':40.54,'t1_close':35.80,'tier':'A','decline_624':-14.94},
    {'code':'920083','name':'金戈新材','t_close':65.91,'t1_close':57.40,'tier':'A','decline_624':-11.98},
    {'code':'300465','name':'高伟达','t_close':13.04,'t1_close':12.10,'tier':'A','decline_624':-11.29},
    {'code':'300461','name':'田中精机','t_close':41.28,'t1_close':39.88,'tier':'A','decline_624':-10.16},
    {'code':'600598','name':'北大荒','t_close':11.22,'t1_close':10.69,'tier':'A','decline_624':-10.02},
    {'code':'000151','name':'中成股份','t_close':11.95,'t1_close':11.40,'tier':'A','decline_624':-10.02},
    {'code':'603318','name':'水发燃气','t_close':10.52,'t1_close':9.47,'tier':'A','decline_624':-10.01},
    {'code':'002354','name':'天娱数科','t_close':8.92,'t1_close':8.03,'tier':'A','decline_624':-9.98},
    {'code':'301512','name':'智信精密','t_close':57.08,'t1_close':49.44,'tier':'A','decline_624':-12.40},
    {'code':'301580','name':'爱迪特','t_close':80.68,'t1_close':72.50,'tier':'A','decline_624':-9.63},
    {'code':'920206','name':'彩客科技','t_close':64.80,'t1_close':58.48,'tier':'A','decline_624':-9.01},
    {'code':'002421','name':'达实智能','t_close':4.09,'t1_close':3.75,'tier':'A','decline_624':-8.80},
    {'code':'301313','name':'凡拓数创','t_close':59.22,'t1_close':54.20,'tier':'A','decline_624':-8.51},
    {'code':'300665','name':'飞鹿股份','t_close':12.22,'t1_close':11.29,'tier':'A','decline_624':-7.28},
    {'code':'688729','name':'屹唐股份','t_close':40.48,'t1_close':37.18,'tier':'A','decline_624':-7.63},
]

for s in signals:
    s['t1_return'] = round((s['t1_close'] - s['t_close']) / s['t_close'] * 100, 2)
    s['hit'] = s['t1_return'] > 0

indices = {
    '上证指数': {'t': 4110.81, 't1': 4120.28, 'return_pct': 0.23},
    '深证成指': {'t': 16051.32, 't1': 16344.08, 'return_pct': 1.82},
    '创业板指': {'t': 4251.42, 't1': 4371.99, 'return_pct': 2.84},
}

returns = [s['t1_return'] for s in signals]
hits = sum(1 for s in signals if s['hit'])
total = len(signals)
avg_return = round(sum(returns)/len(returns), 2)
max_return = max(returns)
min_return = min(returns)
hit_rate = round(hits/total*100, 1)

holy_grails = ['920083','002354','301313','920206','301512','603318','301580','002421']
hg_signals = [s for s in signals if s['code'] in holy_grails]
hg_returns = [s['t1_return'] for s in hg_signals]
hg_avg = round(sum(hg_returns)/len(hg_returns), 2) if hg_returns else 0

lines = []
lines.append("=" * 60)
lines.append("  V13.4 P1-1 T+1 验证报告")
lines.append("  日期: 2026-06-25 (T+1, 周四)")
lines.append("  T日: 2026-06-24 (周三)")
lines.append("  生成时间: 2026-06-25 15:25")
lines.append("=" * 60)
lines.append("")
lines.append("## 一、市场环境")
lines.append("")
lines.append("| 指数 | 6/24收盘 | 6/25收盘 | 涨跌幅 |")
lines.append("|------|----------|----------|--------|")
lines.append("| 上证指数 | {:.2f} | {:.2f} | +{:.2f}% |".format(
    indices['上证指数']['t'], indices['上证指数']['t1'], indices['上证指数']['return_pct']))
lines.append("| 深证成指 | {:.2f} | {:.2f} | +{:.2f}% |".format(
    indices['深证成指']['t'], indices['深证成指']['t1'], indices['深证成指']['return_pct']))
lines.append("| 创业板指 | {:.2f} | {:.2f} | +{:.2f}% |".format(
    indices['创业板指']['t'], indices['创业板指']['t1'], indices['创业板指']['return_pct']))
lines.append("")
lines.append("**市场判断**: 三大指数全线上涨，创业板领涨+2.84%，市场整体回暖。")
lines.append("")
lines.append("## 二、T+1信号验证 (样本: {}只)".format(total))
lines.append("")
lines.append("| # | 代码 | 名称 | T日跌幅 | T日收盘 | T+1收盘 | T+1涨跌 | 结果 |")
lines.append("|---|------|------|---------|---------|---------|---------|------|")

for i, s in enumerate(signals):
    mark = 'HIT' if s['hit'] else 'MISS'
    lines.append("| {} | {} | {} | {:.2f}% | {:.2f} | {:.2f} | {:.2f}% | {} |".format(
        i+1, s['code'], s['name'], s['decline_624'], s['t_close'], s['t1_close'], s['t1_return'], mark))

lines.append("")
lines.append("## 三、统计摘要")
lines.append("")
lines.append("| 指标 | 数值 |")
lines.append("|------|------|")
lines.append("| 验证样本 | {}只 |".format(total))
lines.append("| 命中(上涨) | {}只 |".format(hits))
lines.append("| 命中率 | {:.1f}% |".format(hit_rate))
lines.append("| 平均T+1收益 | {:.2f}% |".format(avg_return))
lines.append("| 最大涨幅 | {:.2f}% |".format(max_return))
lines.append("| 最大跌幅 | {:.2f}% |".format(min_return))
lines.append("")
lines.append("## 四、圣杯信号专项 (今日8只)")
lines.append("")
lines.append("| 代码 | 名称 | T日跌幅 | T+1涨跌 |")
lines.append("|------|------|---------|---------|")

for s in hg_signals:
    lines.append("| {} | {} | {:.2f}% | {:.2f}% |".format(
        s['code'], s['name'], s['decline_624'], s['t1_return']))

lines.append("")
lines.append("圣杯平均T+1收益: {:.2f}%".format(hg_avg))
lines.append("")
lines.append("## 五、关键结论")
lines.append("")
lines.append("### 重大发现：超跌反转策略连续两日失效")
lines.append("")
lines.append("1. **命中率崩溃**: {}只信号股 T+1 {:.1f}%命中率，全军覆没".format(total, hit_rate))
lines.append("2. **系统性跑输**: 三大指数全线上涨(+0.23~+2.84%)，信号股{}只全跌".format(total))
lines.append("3. **连续普跌**: 6/24(普跌-5~-15%) -> 6/25(继续下跌-3~-13%)")
lines.append("4. **圣杯信号失效**: 今日8只圣杯，均来自昨日的超跌股继续下跌")
lines.append("5. **超跌不反转**: 跌幅越大的股(T日-10%+)，T+1继续下跌越严重")
lines.append("")
lines.append("### 根因分析")
lines.append("")
lines.append("- V13.4策略核心: 超跌反弹+Beta放大 -> 当前市场环境不支持")
lines.append("- M64超跌反转加成: 在连续下跌市场中反而放大亏损")
lines.append("- 低吸策略陷阱: 在下跌趋势中接飞刀，而非抄底")
lines.append("- 涨停板炸板混淆: 如002354(6/24涨停但烂板)，被识别为买入信号")
lines.append("")
lines.append("### 优化建议 (P0)")
lines.append("")
lines.append("1. 增加趋势过滤器: 在买入前确认短期趋势不再向下(如5日MA方向)")
lines.append("2. 增加市场宽度条件: 上涨家数/下跌家数比需>0.5才考虑低吸")
lines.append("3. T日涨停板检测: 涨停股排除(避免炸板股进入信号池)")
lines.append("4. 连续跌幅上限: 连续2日累计跌幅>15%自动排除")
lines.append("5. 成交量确认: 缩量下跌(非放量)才考虑超跌反弹")
lines.append("")

report = "\n".join(lines)
print(report)

with open('data/t1_verification_20260625.txt', 'w', encoding='utf-8') as f:
    f.write(report)

t1_data = {
    'date': '20260625',
    't_date': '20260624',
    'indices': indices,
    'signals': [{'code':s['code'],'name':s['name'],'t_close':s['t_close'],
                 't1_close':s['t1_close'],'t1_return':s['t1_return'],
                 'decline_624':s['decline_624'],'hit':s['hit']} for s in signals],
    'stats': {
        'total': total, 'hits': hits, 'hit_rate': hit_rate,
        'avg_return': avg_return, 'max_return': max_return,
        'min_return': min_return, 'holy_grail_avg': hg_avg,
    },
    'conclusion': 'STRATEGY_FAILURE_CONTINUED',
    'market_up': True,
    'strategy_underperform': True,
}
with open('data/t1_verification_20260625.json', 'w', encoding='utf-8') as f:
    json.dump(t1_data, f, ensure_ascii=False, indent=2)

print("\nReport saved to data/t1_verification_20260625.txt and .json")
