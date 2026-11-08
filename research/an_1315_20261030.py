#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""13:15 午后首轮异动研判 — 在 v4 落盘快照基础上做全量分类 + 与上午 11:29 状态机对比。
不重复拉行情，直接读 tail_raw/2026-10-30_quotes.json（13:18:52 快照，428/428）。
"""
import json, os, sys
from research.board_limit import board_limit_pct, is_limit_up

BASE = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "2026-10-30"
QUOTES = os.path.join(BASE, "tail_raw", DATE + "_quotes.json")
MORN = os.path.join(BASE, "tail_raw", "_intraday_anoms_2026-10-30_1125.json")
DAILY = os.path.join(BASE, "backtest_cache", "daily")


def limit_pct(code, name):
    if name and "ST" in name:
        return 0.05
    return board_limit_pct(code)



def daily_ctx(code):
    p = os.path.join(DAILY, code + ".csv")
    if not os.path.exists(p):
        return None
    rows = []
    try:
        lines = open(p, encoding="utf-8").read().splitlines()
    except Exception:
        return None
    for ln in lines[1:]:
        if not ln.strip():
            continue
        q = ln.split(",")
        if len(q) < 6:
            continue
        try:
            rows.append({"d": q[0], "c": float(q[2]), "h": float(q[3]), "l": float(q[4]), "v": float(q[5])})
        except Exception:
            continue
    if len(rows) < 2:
        return None
    cur = rows[:-1] if rows[-1]["d"] == DATE else rows[:]
    if len(cur) < 2:
        return None
    t1, t2 = cur[-1], cur[-2]
    w20 = cur[-20:]
    avg20 = sum(r["v"] for r in w20) / len(w20)
    w120 = cur[-120:] if len(cur) >= 120 else cur
    cnt = 0
    for i in range(1, len(w120)):
        pc = w120[i - 1]["c"]
        if pc > 0 and w120[i]["c"] >= round(pc * 1.1, 2) - 0.005:
            cnt += 1
    # 连续涨停天数（含 T-1 往前）
    streak = 0
    for i in range(len(cur) - 1, 0, -1):
        pc = cur[i - 1]["c"]
        if pc > 0 and cur[i]["c"] >= round(pc * 1.1, 2) - 0.005:
            streak += 1
        else:
            break
    return {"t1_pct": (t1["c"] - t2["c"]) / t2["c"] * 100 if t2["c"] else 0,
            "t1_vol_ratio": t1["v"] / avg20 if avg20 else 0,
            "limit120": cnt, "streak": streak, "t1_date": t1["d"]}


q = json.load(open(QUOTES, encoding="utf-8"))
rows = q["data"]
snap = q.get("snapshot", "")
pool = json.load(open(os.path.join(BASE, "data", "intraday_universe.json"), encoding="utf-8"))["codes"]
srcmap = {x["code"]: x["src"] for x in pool}

# 上午状态
morn = {}
try:
    m = json.load(open(MORN, encoding="utf-8"))
    for a in m["anoms"]:
        morn[a["sec_code"]] = a
    morn_snap = m["snap_time"]
except Exception:
    morn_snap = "NA"

cats = {"ZTIAN": [], "ZTFD": [], "NEARZT": [], "RETRACT": [], "BREAKDOWN": [], "BREAK": [], "VOL": []}
upgrades = []
detail = {}
up_cnt = 0
down_cnt = 0
flat_cnt = 0
for r in rows:
    code, name = r["sec_code"], r["sec_name"] or r["sec_code"]
    pc, px, hi, lo = r["prev_close"], r["price"], r["high"], r["low"]
    if pc <= 0 or px <= 0:
        continue
    lp = limit_pct(code, name)
    zt = round(pc * (1 + lp), 2)
    pct = (px - pc) / pc * 100
    hi_pct = (hi - pc) / pc * 100
    dist = (zt - px) / zt * 100
    ctx = daily_ctx(code) or {}
    el = 138.0
    vr = (r["vol"] / (ctx["t1_vol_ratio"] and 1 or 1)) if False else None
    # 量比: 当日量 / (20日均量 * 时间占比)
    if ctx and ctx.get("t1_vol_ratio"):
        pass
    item = {"code": code, "name": name, "px": px, "pc": pc, "zt": zt, "pct": pct,
            "hi_pct": hi_pct, "dist": dist, "hi": hi, "lo": lo,
            "src": srcmap.get(code, []), "ctx": ctx}
    detail[code] = item
    if pct >= 5:
        cats["BREAK"].append(item)
    if pct <= -5:
        cats["BREAKDOWN"].append(item)
    if hi_pct >= 7 and pct < 2:
        cats["RETRACT"].append(item)
    sealed = abs(px - zt) < 0.011
    if sealed and abs(lo - zt) < 0.011:
        cats["ZTIAN"].append(item)
    elif sealed:
        cats["ZTFD"].append(item)
    elif dist <= 2.5:
        cats["NEARZT"].append(item)
    # 状态机对比（上午 vs 现在）
    a = morn.get(code)
    if a and a.get("price") and a.get("prev_close"):
        apx, apc = a["price"], a["prev_close"]
        azt = round(apc * (1 + lp), 2)
        a_sealed = abs(apx - azt) < 0.011
        a_dist = (azt - apx) / azt * 100
        if a_sealed and not sealed:
            upgrades.append(("炸板", item, "上午封板 %.2f → 现 %.2f (%.2f%%)" % (apx, px, pct)))
        elif (not a_sealed and a_dist <= 2.5) and sealed:
            upgrades.append(("逼近→封板", item, "上午距板%.2f%% → 现封板" % a_dist))

print("SNAP", snap, "n=%d" % len(rows))
print("\n=== 池内涨跌分布 ===")
for th in (5, 3, 0, -3, -5, -7):
    pass
up = sum(1 for x in detail.values() if x["pct"] > 0)
dn = sum(1 for x in detail.values() if x["pct"] < 0)
print("上涨 %d / 下跌 %d / 平 %d" % (up, dn, len(detail) - up - dn))
print("涨幅>=5%%: %d   >=9%%(接近板): %d" % (len(cats["BREAK"]), sum(1 for x in detail.values() if x["pct"] >= 9)))
print("跌幅<=-5%%: %d" % len(cats["BREAKDOWN"]))
print("均值涨跌 %.2f%%" % (sum(x["pct"] for x in detail.values()) / len(detail)))

def show(title, lst, key=None, n=40):
    print("\n=== %s (%d) ===" % (title, len(lst)))
    s = sorted(lst, key=key or (lambda x: -x["pct"]))
    for x in s[:n]:
        print("  %s %-6s %6.2f%% px=%7.2f zt=%7.2f dist=%5.2f hi=%5.2f%% src=%s t1=%s streak=%s" % (
            x["code"], x["name"], x["pct"], x["px"], x["zt"], x["dist"], x["hi_pct"],
            "/".join(x["src"]), ("%.2f" % x["ctx"].get("t1_pct", 0)) if x["ctx"] else "NA",
            x["ctx"].get("streak", "NA") if x["ctx"] else "NA"))

show("一字板 ZTIAN", cats["ZTIAN"])
show("已封板(曾开板) ZTFD", cats["ZTFD"])
show("临界逼近 NEARZT", cats["NEARZT"], key=lambda x: x["dist"])
show("冲高回落 RETRACT", cats["RETRACT"], key=lambda x: x["hi_pct"] - x["pct"])
show("破位 BREAKDOWN", cats["BREAKDOWN"], key=lambda x: x["pct"])

print("\n=== 状态升级 (%d) ===" % len(upgrades))
for kind, x, note in upgrades:
    print("  [%s] %s %-6s %s  pct=%.2f src=%s" % (kind, x["code"], x["name"], note, x["pct"], "/".join(x["src"])))

# 持仓
hp = os.path.join(BASE, "current_holdings.json")
if os.path.exists(hp):
    h = json.load(open(hp, encoding="utf-8"))
    print("\n=== 持仓 ===")
    for x in h["holdings"]:
        c = x["code"]
        it = detail.get(c)
        if it:
            print("  %s %s 成本%.2f 现%.2f 今日%+.2f%% 盈亏%+.2f%% (成本口径) zt=%.2f dist=%.2f%% src=%s" % (
                c, x["name"], x["cost"], it["px"], it["pct"], (it["px"] - x["cost"]) / x["cost"] * 100,
                it["zt"], it["dist"], "/".join(it["src"])))
        else:
            print("  %s %s 不在池内(需补拉)" % (c, x["name"]))

json.dump({"snap": snap, "upgrades": [{"kind": k, "code": x["code"], "name": x["name"], "note": nt,
                                       "pct": x["pct"], "px": x["px"], "zt": x["zt"]} for k, x, nt in upgrades],
           "cats": {k: [{"code": x["code"], "name": x["name"], "pct": round(x["pct"], 2),
                         "px": x["px"], "zt": x["zt"], "dist": round(x["dist"], 2),
                         "hi_pct": round(x["hi_pct"], 2), "src": x["src"],
                         "t1_pct": round(x["ctx"].get("t1_pct", 0), 2) if x["ctx"] else None,
                         "streak": x["ctx"].get("streak") if x["ctx"] else None,
                         "limit120": x["ctx"].get("limit120") if x["ctx"] else None}
                        for x in v] for k, v in cats.items()}},
          open(os.path.join(BASE, "data", "an1315_20261030.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nsaved data/an1315_20261030.json")
