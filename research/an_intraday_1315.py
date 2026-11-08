# -*- coding: utf-8 -*-
"""13:15 盘中异动研判 —— 快照分析（只读，不改生产代码）"""
import json, os, csv, urllib.request
from research.board_limit import board_limit_pct, is_limit_up

BASE = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "20261028"
DAILY = os.path.join(BASE, "backtest_cache", "daily")
RICH = os.path.join(BASE, "tail_raw", "_riched_" + DATE + ".json")
UNIV = os.path.join(BASE, "data", "intraday_universe.json")
HOLD = os.path.join(BASE, "current_holdings.json")

rows = json.load(open(RICH, encoding="utf-8"))
univ = {c["code"]: c for c in json.load(open(UNIV, encoding="utf-8"))["codes"]}
holds = {h["code"]: h for h in json.load(open(HOLD, encoding="utf-8"))["holdings"]}

def limit_pct(code, name):
    return board_limit_pct(code)


def load_daily(code):
    p = os.path.join(DAILY, code + ".csv")
    if not os.path.exists(p):
        return None
    out = []
    with open(p, encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)
        for x in r:
            if len(x) < 6:
                continue
            try:
                out.append({"d": x[0], "o": float(x[1]), "c": float(x[2]),
                            "h": float(x[3]), "l": float(x[4]), "v": float(x[5])})
            except Exception:
                continue
    return out

def stats(code, name):
    d = load_daily(code)
    if not d:
        return None
    lim = limit_pct(code, name)
    # 连板数（截至 T-1）
    streak = 0
    i = len(d) - 1
    while i >= 1:
        pc = d[i - 1]["c"]
        gain = d[i]["c"] / pc - 1 if pc else 0
        if gain >= lim - 0.005:
            streak += 1
            i -= 1
        else:
            break
    # 120日涨停次数
    n120 = 0
    for k in range(max(1, len(d) - 120), len(d)):
        pc = d[k - 1]["c"]
        if pc and (d[k]["c"] / pc - 1) >= lim - 0.005:
            n120 += 1
    t1 = d[-1]
    t0 = d[-2]
    t1_chg = t1["c"] / t0["c"] - 1 if t0["c"] else 0
    v20 = sum(x["v"] for x in d[-21:-1]) / 20 if len(d) > 21 else None
    t1_vr = t1["v"] / v20 if v20 else None
    max20 = max(x["h"] for x in d[-21:-1]) if len(d) > 21 else t1["h"]
    return {"streak": streak, "n120": n120, "t1_chg": t1_chg,
            "t1_vr": t1_vr, "t1_close": t1["c"], "max20": max20,
            "v20": v20, "last_date": t1["d"], "lim": lim}

# 交易分钟数（9:30-11:30=120, 13:00-）
now_ts = max(r["t"] for r in rows)
hm = now_ts[8:12]
hh, mm = int(hm[:2]), int(hm[2:])
elapsed = 120 + max(0, (hh - 13) * 60 + mm) if hh >= 13 else (hh - 9) * 60 + mm - 30
elapsed = max(15, min(240, elapsed))

res = []
for r in rows:
    st = stats(r["sec_code"], r["sec_name"])
    prev = r["prev_close"]
    if prev <= 0:
        continue
    lim = limit_pct(r["sec_code"], r["sec_name"])
    lp = round(prev * (1 + lim), 2)
    chg = r["price"] / prev - 1
    vr = None
    if st and st["v20"]:
        vr = r["vol"] / (st["v20"] * elapsed / 240.0)
    tags = []
    if r["price"] >= lp - 0.001:
        tags.append("一字板" if r["low"] >= lp - 0.001 else "封板(开过板)")
    elif lp - r["price"] > 0 and (lp - r["price"]) / prev <= 0.025:
        tags.append("临界逼近涨停")
    if st and st["streak"] >= 1 and ("封板" in "".join(tags) or "板" in "".join(tags)):
        tags.append("%d连板" % (st["streak"] + 1))
    if st and st["t1_chg"] < lim - 0.005 and r["price"] >= lp - 0.001:
        tags.append("首板")
    if r["high"] >= prev * 1.07 and r["price"] < prev * 1.02:
        tags.append("冲高回落")
    if vr and vr >= 2.5:
        tags.append("放量%.1f" % vr)
    if chg >= 0.05 and st and st["max20"] and (r["price"] / prev) >= (st["max20"] / st["t1_close"]) * 0.999:
        tags.append("新突破")
    if chg <= -0.05:
        tags.append("破位")
    res.append({
        "code": r["sec_code"], "name": r["sec_name"], "price": r["price"],
        "prev": prev, "lp": lp, "chg": chg * 100, "high": r["high"], "low": r["low"],
        "vr": vr, "streak": st["streak"] if st else None,
        "n120": st["n120"] if st else None,
        "t1_chg": st["t1_chg"] * 100 if st else None,
        "t1_vr": st["t1_vr"] if st else None,
        "tags": tags, "src": univ.get(r["sec_code"], {}).get("src", []),
        "hold": holds.get(r["sec_code"]),
    })

def show(pred, title, n=40):
    sel = [x for x in res if pred(x)]
    sel.sort(key=lambda x: -x["chg"])
    print("\n=== %s  (%d) ===" % (title, len(sel)))
    for x in sel[:n]:
        print("%s %-8s %7.2f  %+6.2f%%  涨停%6.2f  低%6.2f  量比%s  连%s 120日板%s T1%s%%  %s  src=%s%s" % (
            x["code"], x["name"] or "-", x["price"], x["chg"], x["lp"], x["low"],
            ("%.1f" % x["vr"]) if x["vr"] else "NA", x["streak"],
            x["n120"], ("%.2f" % x["t1_chg"]) if x["t1_chg"] is not None else "NA",
            "/".join(x["tags"]), ",".join(x["src"]), " [持仓]" if x["hold"] else ""))
    return sel

print("SNAPSHOT_TS=%s  elapsed_min=%d  n=%d" % (now_ts, elapsed, len(res)))
show(lambda x: "封板(开过板)" in x["tags"] or "一字板" in x["tags"] or "首板" in x["tags"], "涨停/封板")
show(lambda x: "临界逼近涨停" in x["tags"], "临界逼近涨停(<=2.5pp)")
show(lambda x: "冲高回落" in x["tags"], "冲高回落")
show(lambda x: "破位" in x["tags"], "破位(<=-5%)")
show(lambda x: "新突破" in x["tags"], "新突破(>=+5%)")
show(lambda x: "放量" in x["tags"], "量比>=2.5", 25)
print("\n=== 持仓 ===")
for c, h in holds.items():
    m = [x for x in res if x["code"] == c]
    if m:
        x = m[0]
        print("%s %s 现%.2f 成本%.2f 盈亏%+.2f%% 涨停%.2f %s %s" % (
            c, x["name"], x["price"], h["cost"], (x["price"] / h["cost"] - 1) * 100,
            x["lp"], "/".join(x["tags"]), x["src"]))
    else:
        print("%s 不在快照内" % c)
json.dump(res, open(os.path.join(BASE, "tail_raw", "_an_" + DATE + ".json"), "w", encoding="utf-8"),
          ensure_ascii=False)
