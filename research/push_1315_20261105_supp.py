# -*- coding: utf-8 -*-
"""13:15 盘中异动研判 2026-11-05 — 二次刷新补报"""
import json, os, sys

sys.path.insert(0, r"E:/WorkBuddy_dot_workbuddy/Claw")
import push_channel

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "2026-11-05"

TITLE = "[盘中异动补报] 13:23 中邮科技20cm新封 / 科森长腿15pp"

BODY = """【盘中异动补报 · 13:23】

二次刷新：快照 13:22:55｜池 653（停牌剔1）｜封板 30（较 13:17 增 1）
池内 涨503/跌144 均值+2.31%

一、新封板（13:17 时距板 0.98%，13:22 封上）
① 688648 中邮科技｜T1B｜20cm 首板
   昨收29.01 → 开28.90 → 最低28.41(-2.07%) → 13:22 封 34.81 +19.99%
   振幅15.58%｜换手7.32%｜流通值仅22.48亿
   驱动：中字头局部异动（中成股份直线涨停 3天2板），无个股公告
   结论：绿盘拉起的20cm小盘封板，属午后新起的中字头分支。
       封板状态只能排队，不可当作"可买"

二、日内反转长腿（未封板）
② 603626 科森科技｜T1B｜0板
   最低 -6.30% → 现28.15 +8.81%（15.1pp）｜最高28.46，距板仅1.09%
   结论：苹果链精密结构件，端侧AI主线内的日内反转。未封板即非买点
③ 001330 博纳影业｜T1B｜0板 现6.09 +8.75%（板6.16，差1.14%，全天未摸板）
④ 001233 海安集团｜0板 现66.96 +8.44%（板67.93，差1.43%）

三、炸板票的回抽
⑤ 002995 天地在线 现19.81 +7.78%，距板2.03%（13:17 报19.68/+7.07%）
   封板→炸板→再逼近，属反复开合，换手已超25%，不做接力

更正：上一条写"快照已超10分钟"，实际推送时快照仅4分钟旧，表述过保守。

纪律不变：不追高开≥5%涨停股（R001）；连板T+1均值-2.28%，小仓。
下单前以实时盘口复核。
"""

LOG = os.path.join(ROOT, "data/intraday_agentic_log.json")
log = json.load(open(LOG, encoding="utf-8"))
new = [
    dict(date=DATE, time="13:23", code="688648", name="中邮科技", type="ZT_SEALED_NEW", group="UP",
         state="20cm首板新封", status="已封板", board=0, chg=19.99, price=34.81, limit=34.81,
         note="绿盘-2.07%拉起封板，中字头异动，流通22.48亿", src="T1B"),
    dict(date=DATE, time="13:23", code="603626", name="科森科技", type="REVERSAL_LEG", group="UP",
         state="日内长腿(低-6.30%→+8.81%)", status="摸板未封", board=0, chg=8.81, price=28.15, limit=28.46,
         note="15.1pp长腿，距板1.09%未封", src="T1B"),
]
have = {(r.get("date"), r.get("code"), r.get("type")) for r in log}
added = [x for x in new if (x["date"], x["code"], x["type"]) not in have]
log.extend(added)
json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("LOG +%d 条，今日累计%d" % (len(added), len([r for r in log if r.get("date") == DATE])))

ok, ch, detail, n = push_channel.send(TITLE, BODY, backup=True)
print("DELIVER ok=%s channel=%s tries=%d detail=%s" % (ok, ch, n, detail))
