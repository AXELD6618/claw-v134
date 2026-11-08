# -*- coding: utf-8 -*-
"""13:15 盘中异动研判 2026-11-05 — 正文组装 + 台账去重写入 + 推送"""
import json, os, sys

sys.path.insert(0, r"E:/WorkBuddy_dot_workbuddy/Claw")
import push_channel

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "2026-11-05"

TITLE = "[盘中异动研判] 13:17 炸板3只 / 临界集体钝化 / 光伏+端侧AI双主线"

BODY = """【盘中异动研判 · 13:17 午后首轮】

快照 13:17:45｜池 654/654（缺0，停牌剔1只 002623 亚玛顿）
池内 涨504/跌147 均值+2.36%｜封板29（一字5+曾开板24）｜炸板19

一、炸板 3 只（全部是本日曾记录封板的票）
① 002995 天地在线｜LIANBAN·T1B｜0板(4天2板)
   11:06 封板封单1.78亿 → 13:17 19.68 +7.07%，板20.22，差2.70%
   换手23.69% 量比2.99 主力净流入7557万 —— 钱还在，价没封住
   结论：传媒/AI应用情绪票，放量炸板不接力
② 300751 迈为股份｜DISC｜0板
   20cm板170.46 → 166.21 +17.01%，量比3.42，T-1已+17.19%
   结论：光伏设备龙头，两日累计超37%，R001 禁追
③ 688516 奥特维｜T1B·DISC｜0板
   20cm板51.54 → 49.68 +15.67%，最低仅平昨收
   结论：板块最强的一天不封板，说明追高盘已枯

二、新增异动
④ 002827 高争民爆｜T1B·DISC｜3板
   重组复牌（购紫源铜业51%+晟源矿业100%），开75.84 即涨停 → 现67.70 -1.81%
   回撤11.8pp｜量比5.04｜换手11.79%
   结论：三个一字板后首日开板即崩，重组预期透支

三、回封（唯一逆势结构）
⑤ 603893 瑞芯微｜DISC｜2板
   10:48封 → 11:29开 → 13:17 回封 217.81 +10.00%
   最低仅 +0.51%（绿盘拉起）｜量比2.14
   结论：端侧AI，全池结构最干净的一只

四、持仓
002223 鱼跃医疗 28.90 浮盈+2.05%（成本28.32），未破位
301106 骏成科技 16.50 浮亏-4.24%（成本17.23），今日+3.19%，首次收回-5%以内

五、盘面真实动作（与"上午封板午后易炸"的预设相反）
11:29 涨停28只 → 13:17 只炸1只（天地在线），炸板率3.6%，预设今日不成立。
真正的变化是临界票集体钝化：11:29 八只临界票，只有瑞芯微封上；兴业银锡(距板1.21→2.00)、三利谱(1.25→2.01)、赤峰黄金(1.30→1.50)、凯莱英(1.61→2.41)、新洁能(1.94→2.26)、皇台酒业(2.20→2.22)、郑中设计(2.36→2.90) 七只全部后退，无一封板。

主线两条：
· 光伏设备（午盘板块+9.38%涨停潮，9月单月新增装机13.64GW、同比+41.2%，多晶硅11月减产10%）
· 端侧AI（苹果国行Apple Intelligence接阿里通义千问，11-04开测、12月推送）
崩的是同属"AI硬件"的光模块与金融IT：
中际旭创-9.19%、新易盛-7.49%、天孚通信-5.51%、光库科技-6.55%；银之杰-7.79%、赢时胜-7.24%、指南针-6.28%。

纪律：不追高开≥5%涨停股（R001）；连板惯性T+1均值-2.28%（剔涨停日），只可小仓。
快照已超10分钟，下单前以实时盘口复核。
"""

# ---------- 台账去重写入 ----------
LOG = os.path.join(ROOT, "data/intraday_agentic_log.json")
log = json.load(open(LOG, encoding="utf-8"))
new = [
    dict(date=DATE, time="13:17", code="002995", name="天地在线", type="ZT_BROKE", group="DOWN",
         state="炸板(封→开)", status="炸板", board=0, chg=7.07, price=19.68, limit=20.22,
         note="11:06封单1.78亿→13:17炸板，换手23.69%量比2.99", src="LIANBAN,T1B"),
    dict(date=DATE, time="13:17", code="300751", name="迈为股份", type="ZT_BROKE", group="DOWN",
         state="炸板(20cm未封)", status="炸板", board=0, chg=17.01, price=166.21, limit=170.46,
         note="光伏设备龙头，T-1已+17.19%，两日累计37%+", src="DISC"),
    dict(date=DATE, time="13:17", code="688516", name="奥特维", type="ZT_BROKE", group="DOWN",
         state="炸板(20cm未封)", status="炸板", board=0, chg=15.67, price=49.68, limit=51.54,
         note="20cm板打开，最低平昨收", src="T1B,DISC"),
    dict(date=DATE, time="13:17", code="002827", name="高争民爆", type="ZT_BROKE", group="DOWN",
         state="3板开板跳水(天地板)", status="开板", board=3, chg=-1.81, price=67.70, limit=75.85,
         note="重组复牌开75.84即板→13:17 -1.81%，回撤11.8pp", src="T1B,DISC"),
    dict(date=DATE, time="13:17", code="603893", name="瑞芯微", type="ZT_RESEAL", group="UP",
         state="开板后回封", status="回封", board=2, chg=10.00, price=217.81, limit=217.81,
         note="10:48封→11:29开→13:17回封，最低+0.51%", src="DISC"),
]
# 去重：同代码同类型今日已有则不再追加
have = {(r.get("date"), r.get("code"), r.get("type")) for r in log}
added = [x for x in new if (x["date"], x["code"], x["type"]) not in have]
log.extend(added)
json.dump(log, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("LOG +%d 条（候选%d，今日累计%d）" % (len(added), len(new), len([r for r in log if r.get("date") == DATE])))

ok, ch, detail, n = push_channel.send(TITLE, BODY, backup=True)
print("DELIVER ok=%s channel=%s tries=%d detail=%s" % (ok, ch, n, detail))
print("BODY_LEN=%d" % len(BODY))
