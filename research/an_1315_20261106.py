# -*- coding: utf-8 -*-
"""13:15 盘中异动研判 2026-11-06 — 全量分类 + 连板计数 + 首板 + 持仓 + 午收状态 diff"""
import json, os, sys
sys.path.insert(0, r"E:/WorkBuddy_dot_workbuddy/Claw")
sys.path.insert(0, r"E:/WorkBuddy_dot_workbuddy/Claw/research")

import intraday_agentic_v4 as V4

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "2026-11-06"
V4.DATE = DATE

univ = json.load(open(os.path.join(ROOT, "data/intraday_universe.json"), encoding="utf-8"))
meta = {x["code"]: x for x in univ["codes"]}
snap = json.load(open(os.path.join(ROOT, "tail_raw", "%s_quotes.json" % DATE), encoding="utf-8"))
quotes = {r["sec_code"]: r for r in snap["data"]}
SNAP = snap["snapshot"]
HH, MM = int(SNAP[8:10]), int(SNAP[10:12])
# 已交易分钟：上午 9:30-11:30 =120，午后 13:00 起
if HH < 12:
    elapsed = max(1, (HH - 9) * 60 + MM - 30)
else:
    elapsed = 120 + (HH - 13) * 60 + MM
elapsed = min(elapsed, 240)
print("SNAP=%s  %02d:%02d  elapsed_min=%d  n=%d" % (SNAP, HH, MM, elapsed, len(quotes)))


def board_count(code, name):
    """截至 T-1 收盘的连续涨停板数"""
    p = os.path.join(ROOT, "backtest_cache/daily", code + ".csv")
    if not os.path.exists(p):
        return 0
    rows = []
    for ln in open(p, encoding="utf-8").read().splitlines()[1:]:
        ps = ln.split(",")
        if len(ps) < 6: continue
        try: rows.append({"d": ps[0], "c": float(ps[2])})
        except Exception: continue
    if rows and rows[-1]["d"] == DATE: rows = rows[:-1]
    n = 0
    for i in range(len(rows) - 1, 0, -1):
        if V4._is_limit_up(code, name, rows[i - 1]["c"], rows[i]["c"]): n += 1
        else: break
    return n


rows = []
for code, q in quotes.items():
    nm = q["sec_name"] or meta.get(code, {}).get("name") or ""
    prev = q["prev_close"]
    if prev <= 0 or q["price"] <= 0: continue
    if q["vol"] <= 0 and q["high"] == 0:  # 停牌护栏
        continue
    lp = V4.limit_pct(code, nm)
    # 交易所四舍五入口径（ROUND_HALF_UP）；Python round 是 banker's+浮点误差，
    # 002578 闽发铝业 4.55*1.1=5.005 会被算成 5.00，真板价 5.01
    from decimal import Decimal, ROUND_HALF_UP
    zt = float((Decimal(str(prev)) * (1 + Decimal(str(lp)))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    ctx = V4.daily_ctx(code, nm)
    bc = board_count(code, nm)
    pct = (q["price"] / prev - 1) * 100
    hpct = (q["high"] / prev - 1) * 100
    at_zt = q["price"] >= zt - 0.005
    touch = q["high"] >= zt - 0.005
    low_at_zt = q["low"] >= zt - 0.005
    vr = (q["vol"] / (ctx["avg20"] * elapsed / 240.0)) if ctx and ctx["avg20"] > 0 else None
    was_zt = ctx and V4._is_limit_up(code, nm, ctx["t1_prev"], ctx["t1_close"])
    rows.append(dict(code=code, name=nm, src=meta.get(code, {}).get("src", []),
                     price=q["price"], prev=prev, pct=round(pct, 2), high=q["high"], low=q["low"],
                     hpct=round(hpct, 2), lp=lp, zt=zt, gap=round((zt / q["price"] - 1) * 100, 2),
                     at_zt=at_zt, touch=touch, low_at_zt=low_at_zt, bc=bc, vr=vr,
                     was_zt=bool(was_zt), t1_pct=round((ctx["t1_close"] / ctx["t1_prev"] - 1) * 100, 2) if ctx else None,
                     limit120=ctx["limit120"] if ctx else None,
                     t1_vr=round(ctx["t1_vol"] / ctx["avg20"], 2) if ctx else None))

print("有效行情 %d 只（剔除停牌/无成交 %d）" % (len(rows), len(quotes) - len(rows)))
up = sum(1 for r in rows if r["pct"] > 0); dn = sum(1 for r in rows if r["pct"] < 0)
print("涨 %d / 跌 %d / 均值 %.2f%%" % (up, dn, sum(r["pct"] for r in rows) / len(rows)))

ZT = [r for r in rows if r["at_zt"]]
YIZI = [r for r in ZT if r["low_at_zt"]]
SEALED = [r for r in ZT if not r["low_at_zt"]]
CRIT = sorted([r for r in rows if not r["at_zt"] and 0 < r["gap"] <= 2.5 and r["pct"] > 0], key=lambda x: x["gap"])
TOUCH_FELL = sorted([r for r in rows if r["touch"] and not r["at_zt"]], key=lambda x: -x["hpct"])
FIRST = sorted([r for r in ZT if not r["was_zt"]], key=lambda x: -x["pct"])
MULTI = sorted([r for r in ZT if r["was_zt"]], key=lambda x: -x["bc"])
HIGHVOL = sorted([r for r in rows if r["vr"] and r["vr"] >= 2.5 and abs(r["pct"]) >= 3], key=lambda x: -x["vr"])
BREAK = sorted([r for r in rows if r["pct"] <= -5], key=lambda x: x["pct"])
SURGE = sorted([r for r in rows if r["pct"] >= 5], key=lambda x: -x["pct"])

print("\n== 涨停 %d（一字 %d / 曾开板 %d）==" % (len(ZT), len(YIZI), len(SEALED)))
for r in sorted(ZT, key=lambda x: -x["bc"]):
    print("  %s %-7s %-11s 昨%2d板 今%d板 现%.2f/板%.2f%s vr=%s %s%s" % (
        r["code"], r["name"], "/".join(r["src"]), r["bc"], r["bc"] + 1, r["price"], r["zt"],
        "(一字)" if r["low_at_zt"] else "(曾开板)", ("%.1f" % r["vr"]) if r["vr"] else "NA",
        "首板!" if not r["was_zt"] else "", " T1=%+.1f%%" % r["t1_pct"] if r["t1_pct"] is not None else ""))

print("\n== 首板/爆发（T-1 未涨停，今日涨停）%d ==" % len(FIRST))
for r in FIRST:
    print("  %s %-7s %-11s T1=%+.2f%% 120日涨停%s次 T1量比%s 现%.2f 板%.2f 最低%.2f" % (
        r["code"], r["name"], "/".join(r["src"]), r["t1_pct"] or 0, r["limit120"], r["t1_vr"], r["price"], r["zt"], r["low"]))

print("\n== 临界逼近涨停（距板 ≤2.5pp）%d ==" % len(CRIT))
for r in CRIT:
    print("  %s %-7s %-11s 现%.2f 板%.2f 差%.2f%% 涨%+.2f%% 最高%+.2f%% 昨%d板 vr=%s" % (
        r["code"], r["name"], "/".join(r["src"]), r["price"], r["zt"], r["gap"], r["pct"], r["hpct"], r["bc"],
        ("%.1f" % r["vr"]) if r["vr"] else "NA"))

print("\n== 冲高回落（摸板未封）%d ==" % len(TOUCH_FELL))
for r in TOUCH_FELL[:15]:
    print("  %s %-7s %-11s 最高%+.2f%% → 现%+.2f%% 回撤%.1fpp 昨%d板 vr=%s" % (
        r["code"], r["name"], "/".join(r["src"]), r["hpct"], r["pct"], r["hpct"] - r["pct"], r["bc"],
        ("%.1f" % r["vr"]) if r["vr"] else "NA"))

print("\n== 破位 ≤-5%% %d ==" % len(BREAK))
for r in BREAK[:20]:
    print("  %s %-7s %-11s %+.2f%% 昨%d板 vr=%s" % (r["code"], r["name"], "/".join(r["src"]), r["pct"], r["bc"],
                                                 ("%.1f" % r["vr"]) if r["vr"] else "NA"))

print("\n== 放量异动 vr≥2.5 且 |涨|≥3%% %d ==" % len(HIGHVOL))
for r in HIGHVOL[:20]:
    print("  %s %-7s %-11s %+.2f%% vr=%.1f 昨%d板" % (r["code"], r["name"], "/".join(r["src"]), r["pct"], r["vr"], r["bc"]))

print("\n== 涨幅榜 top15 ==")
for r in SURGE[:15]:
    print("  %s %-7s %-11s %+.2f%% 昨%d板 %s" % (r["code"], r["name"], "/".join(r["src"]), r["pct"], r["bc"],
                                            "涨停" if r["at_zt"] else "距板%.2f%%" % r["gap"]))

print("\n== 持仓 ==")
H = json.load(open(os.path.join(ROOT, "current_holdings.json"), encoding="utf-8"))["holdings"]
for h in H:
    q = quotes.get(h["code"])
    if not q: print("  %s 无行情" % h["code"]); continue
    pnl = (q["price"] / h["cost"] - 1) * 100
    print("  %s %-6s 成本%.2f 现%.2f 盈亏%+.2f%% 今日%+.2f%% 昨日线MA无关 最高%+.2f%% 最低%+.2f%%" % (
        h["code"], h["name"], h["cost"], q["price"], pnl, (q["price"] / q["prev_close"] - 1) * 100,
        (q["high"] / q["prev_close"] - 1) * 100, (q["low"] / q["prev_close"] - 1) * 100))

# 午收状态 diff
log = json.load(open(os.path.join(ROOT, "data/intraday_agentic_log.json"), encoding="utf-8"))
morn = {}
for e in log:
    if e.get("date") == DATE:
        morn[e["code"]] = e
print("\n== 午收基线 diff（台账 %d 条）==" % len(morn))
for code, e in morn.items():
    r = next((x for x in rows if x["code"] == code), None)
    if not r: continue
    now = "涨停" if r["at_zt"] else ("距板%.2f%%" % r["gap"] if r["gap"] <= 3 else "%+.2f%%" % r["pct"])
    print("  %s %-7s 午前[%s %s] → 现在 %s (涨%+.2f%%)" % (code, e.get("name"), e.get("time"),
                                                    e.get("state") or e.get("status"), now, r["pct"]))

json.dump({"snap": SNAP, "elapsed": elapsed, "rows": rows}, open(os.path.join(ROOT, "research/_an_1315_20261106.json"), "w", encoding="utf-8"), ensure_ascii=False)
print("\nSAVED research/_an_1315_20261106.json")
