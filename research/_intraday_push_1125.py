# -*- coding: utf-8 -*-
import json, os, sys, datetime
sys.path.insert(0, r"E:/WorkBuddy_dot_workbuddy/Claw")
import push_channel

DATE = "2026-11-02"
DATA = r"E:/WorkBuddy_dot_workbuddy/Claw/data"
ANOM = os.path.join(r"E:/WorkBuddy_dot_workbuddy/Claw", f"_intraday_anoms_{DATE}_1125.json")
LOGP = os.path.join(DATA, "intraday_agentic_log.json")

anom = json.load(open(ANOM, encoding='utf-8'))
board = anom['board']; first = anom['first_board']
snap = anom['snapshot_time']  # 20261102120537
snap_hm = f"{snap[8:10]}:{snap[10:12]}"
pool_n = anom['pool_n']

# ---- load today's log for dedup ----
log = json.load(open(LOGP, encoding='utf-8')) if os.path.exists(LOGP) else []
today_codes = set()
for e in log:
    if str(e.get('date')) == DATE or (e.get('ts','')[:10] == DATE):
        today_codes.add(str(e.get('code')))

def tag(code):
    return '【早间已报】' if code in today_codes else '【新】'

# ---- categorize board ----
yizi = [r for r in board if r['board']=='一字板']
feng = [r for r in board if r['board']=='已封板']
lin = [r for r in board if r['board']=='临界']

def fmt(r):
    return (f"{r['sec_name']}({r['sec_code']},{r['chg']:.2f}%,px{r['price']}"
            f"{'=' if r['board'] in ('一字板','已封板') else '≈'}涨停{r['limit_price']},"
            f"src={r['src']})")

# ---- build body ----
L = []
L.append(f"【盘中异动研判·午前终轮】11:25档")
L.append(f"快照 {snap_hm}（午间收盘冻结）｜池子覆盖 {pool_n} 只（≥80 达标）")
L.append(f"⚠ 快照 {snap_hm} 距今>10分钟，下单前以实时盘口复核。")
L.append("")
L.append("一、盘面与主线")
L.append("沪+1.16%/深+2.39%/创+3.28%；半日成交1.14万亿(+1303亿)；涨停93/跌停6，封板率85%，超4700涨。")
L.append("⚠ 我监控靶心赛道(机器人/半导体/商业航天/AI算力/物理AI)今日无整体联动拉升；真实主线=金融科技(央行优化回购增持再贷款)+AI漫剧传媒+白酒，属超跌反弹，非靶心。")
L.append("")
L.append("二、连板专项（优先级最高）")
L.append("· 一字板·不可买：")
for r in yizi:
    L.append(f"   {tag(r['sec_code'])} {fmt(r)}")
L.append("· 已封板·只排队，禁喊\"可买\"：")
for r in feng:
    L.append(f"   {tag(r['sec_code'])} {fmt(r)}")
L.append("· 临界逼近涨停·重点点名·非买点：")
for r in lin:
    L.append(f"   {tag(r['sec_code'])} {fmt(r)}")
L.append("")
L.append("三、首板/爆发专项（下午&次日连板候选池）")
L.append(f"今日首板且T-1未涨停共 {len(first)} 只（封板时间需盘口实时，快照无）：")
for r in first:
    ds = r['daily']
    L.append(f"   {tag(r['sec_code'])} {fmt(r)} T1ret={ds['t1_ret']:.2f}% lim120={ds['limit120']} T1vr={ds['t1_vol_ratio']:.2f}")
L.append("")
L.append("四、其他异动（WebSearch 实证）")
L.append("· 指南针(300803,+14.94% 量比12.48,金融IT,OVER)：金融科技板块强势(银之杰20CM/ETF+8%)，央行优化回购增持再贷款催化，超跌反弹高贝塔；非连板、不追。")
L.append("· 麦格米特(002862,-9.59% 量比9.79,AI电源,T1B)：高位补跌，PE(TTM)曾607倍+汇兑损失+H股稀释预期+量化杀跌；破位，勿接飞刀。")
L.append("· 襄阳轴承(000678,8天6板,机器人减速器)：公司公告机器人轴承营收仅16万占比0.023%未盈利、连续亏损，纯情绪炒作；估值脱离基本面。")
L.append("· 国芳集团(601086,零售消费首板)：历史9月连板后大幅回撤、市盈率曾99倍严重脱离基本面、多次风险提示；今日首板纯情绪。")
L.append("· 光洋股份(002708,临界+9.25%,机器人/汽车,事故股)：10月底曾连板，公司澄清机器人业务未盈利；临界非买点。")
L.append("· 亚玛顿(002623,光伏玻璃/钙钛矿首板)：钙钛矿国标政策催化，10-27曾首板；题材小批量供货未盈利。")
L.append("")
L.append("五、纪律")
L.append("· R001：不追≥5%涨停股；一字不可买、封板只排队禁喊\"可买\"；临界非买点。")
L.append("· 连板惯性实证：T+1涨停率34.55% vs 基线1.92%；剔除涨停日后均值-2.28%，须小仓。")
L.append("· 断层反包已降级：样本外均值-0.28%、胜率43.6%，不单独出手。")
L.append("· 本轮有异动→已推；快照>10min，下单前以实时盘口复核。")

body = "\n".join(L)
title = f"[盘中异动研判] 11:25 午前终轮 覆盖{pool_n}只 连板{len(board)} 首板{len(first)}"

# ---- send ----
ok, ch, detail, n = push_channel.send(title, body, backup=True)
print("DELIVER ok=%s channel=%s tries=%d detail=%s" % (ok, ch, n, detail))
print("=====BODY=====")
print(body)

# ---- append NEW alerts to log ----
new_entries = []
for r in board + first:
    if r['sec_code'] not in today_codes:
        bt = {'一字板':'ZTIAN','已封板':'ZTFD','临界':'LINJIE'}.get(r['board'],'FIRSTZT' if r['first_board'] else 'BOARD')
        new_entries.append(dict(date=DATE, ts=datetime.datetime.now().isoformat(timespec='seconds'),
                               code=r['sec_code'], name=r['sec_name'], type=bt,
                               chg=r['chg'], price=r['price'], limit=r['limit_price'], status='alert'))
if new_entries:
    log.extend(new_entries)
    json.dump(log, open(LOGP,'w',encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f"\nLOG appended {len(new_entries)} new entries (total {len(log)})")
else:
    print("\nNo new entries to log (all already reported today)")
