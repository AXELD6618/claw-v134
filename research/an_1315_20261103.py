# -*- coding: utf-8 -*-
"""13:15 盘中异动研判 2026-11-03 — 全量分类 + 板数 + 状态机对比 vs 12:05 基线 + 持仓"""
import json, os, sys
from research.board_limit import board_limit_pct, is_limit_up

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "2026-11-03"
DAILY = os.path.join(ROOT, "backtest_cache/daily")

univ = json.load(open(os.path.join(ROOT, "data/intraday_universe.json"), encoding="utf-8"))
SRC = {x["code"]: x.get("src", []) for x in univ["codes"]}
snap = json.load(open(os.path.join(ROOT, "tail_raw/%s_quotes.json" % DATE), encoding="utf-8"))
rows = snap["data"]
SNAP_T = snap.get("snapshot", "")
print("snapshot =", SNAP_T, " n =", len(rows), " pool =", len(univ["codes"]))


def limit_pct(code, name):
    return board_limit_pct(code)



def load_csv(code):
    p = os.path.join(DAILY, code + ".csv")
    if not os.path.exists(p): return None
    out = []
    for ln in open(p, encoding="utf-8").read().splitlines()[1:]:
        parts = ln.split(",")
        if len(parts) < 6: continue
        try:
            out.append(dict(d=parts[0], o=float(parts[1]), c=float(parts[2]),
                            h=float(parts[3]), l=float(parts[4]), v=float(parts[5])))
        except Exception:
            pass
    return out or None


def zt_hit(code, name, prev, close):
    lp = limit_pct(code, name)
    return close >= round(prev * (1 + lp), 2) - 0.005


def board_count(code, name):
    """连板数（截至 T-1，交易所口径）；返回 (boards, t1_pct, t1_volratio, limit120)"""
    hist = load_csv(code)
    if not hist: return 0, None, None, 0
    cur = hist[:-1] if hist[-1]["d"] == DATE else hist
    if len(cur) < 2: return 0, None, None, 0
    n = 0
    i = len(cur) - 1
    while i >= 1:
        if zt_hit(code, name, cur[i - 1]["c"], cur[i]["c"]):
            n += 1; i -= 1
        else:
            break
    t1 = cur[-1]; t2 = cur[-2]
    avg20 = sum(r["v"] for r in cur[-20:]) / len(cur[-20:])
    w = cur[-121:] if len(cur) >= 121 else cur
    c120 = sum(1 for k in range(1, len(w)) if zt_hit(code, name, w[k - 1]["c"], w[k]["c"]))
    return n, (t1["c"] - t2["c"]) / t2["c"] * 100, (t1["v"] / avg20 if avg20 else None), c120


recs = []
for r in rows:
    code, name = r["sec_code"], r["sec_name"]
    pc, price, hi, lo = r["prev_close"], r["price"], r["high"], r["low"]
    if pc <= 0 or price <= 0: continue
    lp = limit_pct(code, name)
    zt = round(pc * (1 + lp), 2)
    pct = (price - pc) / pc * 100
    hi_pct = (hi - pc) / pc * 100
    lo_pct = (lo - pc) / pc * 100
    dist = (zt - price) / zt * 100
    sealed = price >= zt - 0.005
    touched = hi >= zt - 0.005
    one_word = sealed and lo >= zt - 0.005
    if one_word:
        state = "ONE_WORD"
    elif sealed:
        state = "SEALED"
    elif touched:
        state = "BROKE"
    else:
        state = "NORMAL"
    recs.append(dict(code=code, name=name, pct=pct, price=price, prev=pc, zt=zt, hi=hi, lo=lo,
                     hi_pct=hi_pct, lo_pct=lo_pct, dist=dist, state=state, open=r["open"],
                     vol=r["vol"], src=SRC.get(code, []), lp=lp))

now = {x["code"]: x for x in recs}

# ---- 板数只对关注标的算 ----
focus = [x for x in recs if x["state"] != "NORMAL" or x["dist"] <= 3.0 or x["pct"] >= 5]
for x in focus:
    b, t1p, t1v, c120 = board_count(x["code"], x["name"])
    x["board"], x["t1_pct"], x["t1_vr"], x["c120"] = b, t1p, t1v, c120

onew = [x for x in recs if x["state"] == "ONE_WORD"]
sealed = [x for x in recs if x["state"] == "SEALED"]
broke = [x for x in recs if x["state"] == "BROKE"]
near = [x for x in recs if x["state"] == "NORMAL" and x["dist"] <= 2.5]

print("\n== 一字板 %d ==" % len(onew))
for x in sorted(onew, key=lambda z: -z["pct"]):
    print("  %s %s %d板 %+.2f%% src=%s" % (x["code"], x["name"], x.get("board") or 0, x["pct"], ",".join(x["src"])))

print("\n== 封板(曾开板) %d ==" % len(sealed))
for x in sorted(sealed, key=lambda z: -(z.get("board") or 0)):
    print("  %s %s %d板 %+.2f%% low=%.2f zt=%.2f 开板?%s src=%s" %
          (x["code"], x["name"], x.get("board") or 0, x["pct"], x["lo"], x["zt"],
           "是" if x["lo"] < x["zt"] - 0.005 else "否", ",".join(x["src"])))
print("  封板合计 %d（一字%d + 曾开板%d）" % (len(onew) + len(sealed), len(onew), len(sealed)))

print("\n== 炸板(曾摸板未封) %d ==" % len(broke))
for x in sorted(broke, key=lambda z: -z["hi_pct"]):
    print("  %s %s %d板 高+%.1f%%→现%+.2f%% 回落%.1fpp src=%s" %
          (x["code"], x["name"], x.get("board") or 0, x["hi_pct"], x["pct"],
           x["hi_pct"] - x["pct"], ",".join(x["src"])))

print("\n== 临界逼近(距板≤2.5pp 未摸板) %d ==" % len(near))
for x in sorted(near, key=lambda z: z["dist"]):
    print("  %s %s %d板 现%.2f 板%.2f 差%.2f%% 现%+.2f%% 高+%.1f%% src=%s" %
          (x["code"], x["name"], x.get("board") or 0, x["price"], x["zt"], x["dist"],
           x["pct"], x["hi_pct"], ",".join(x["src"])))

print("\n== 首板(T-1未涨停 今日封) ==")
first = [x for x in recs if x["state"] in ("SEALED", "ONE_WORD") and (x.get("board") or 0) == 0]
for x in sorted(first, key=lambda z: -z["pct"]):
    print("  %s %s %+.2f%% T-1 %+.2f%% 120日涨停%d次 T-1量比%.2f 低%.2f src=%s" %
          (x["code"], x["name"], x["pct"], x.get("t1_pct") or 0, x.get("c120") or 0,
           x.get("t1_vr") or 0, x["lo"], ",".join(x["src"])))
print("  首板合计 %d" % len(first))

print("\n== 冲高回落(高≥+7% 现<+2%) ==")
for x in sorted([y for y in recs if y["hi_pct"] >= 7 and y["pct"] < 2], key=lambda z: -(z["hi_pct"] - z["pct"]))[:15]:
    print("  %s %s 高+%.1f%%→现%+.2f%% 回落%.1fpp src=%s" %
          (x["code"], x["name"], x["hi_pct"], x["pct"], x["hi_pct"] - x["pct"], ",".join(x["src"])))

print("\n== 破位(≤-5%) ==")
for x in sorted([y for y in recs if y["pct"] <= -5], key=lambda z: z["pct"])[:15]:
    print("  %s %s %+.2f%% src=%s" % (x["code"], x["name"], x["pct"], ",".join(x["src"])))

up = len([x for x in recs if x["pct"] > 0]); dn = len([x for x in recs if x["pct"] < 0])
avg = sum(x["pct"] for x in recs) / len(recs)
print("\n== 池内情绪 ==  涨%d 跌%d 平%d 均值%+.2f%%" % (up, dn, len(recs) - up - dn, avg))

print("\n== 涨幅榜 TOP20 ==")
for x in sorted(recs, key=lambda z: -z["pct"])[:20]:
    print("  %s %s %+.2f%% state=%s src=%s" % (x["code"], x["name"], x["pct"], x["state"], ",".join(x["src"])))

print("\n== 持仓 ==")
h = json.load(open(os.path.join(ROOT, "current_holdings.json"), encoding="utf-8"))
for p in h["holdings"]:
    m = now.get(p["code"])
    if not m:
        print("  %s %s 无报价(不在池/停牌)" % (p["code"], p["name"])); continue
    pnl = (m["price"] - p["cost"]) / p["cost"] * 100
    print("  %s %s 成本%.2f 现%.2f 浮盈%+.2f%% 今日%+.2f%% 高%+.2f%% 低%+.2f%% 板%.2f 距板%.1f%% src=%s" %
          (p["code"], p["name"], p["cost"], m["price"], pnl, m["pct"], m["hi_pct"], m["lo_pct"],
           m["zt"], m["dist"], ",".join(m["src"])))

print("\n== 状态机对比 vs 12:05 基线 ==")
base = json.load(open(os.path.join(ROOT, "data/_intraday_anoms_2026-11-03_1125.json"), encoding="utf-8"))
b_yizi = {r[0] for r in base["yizi"]}
b_feng = {r[0] for r in base["yifeng"]}
b_lin = {r[0] for r in base["linjie"]}
print("基线: 一字%d 封板%d 临界%d" % (len(b_yizi), len(b_feng), len(b_lin)))
# 上午封板(含一字) → 现在状态
for code in sorted(b_yizi | b_feng):
    x = now.get(code)
    if not x:
        print("  %s 无报价" % code); continue
    was = "一字" if code in b_yizi else "封板"
    print("  %s %s %s → %s 现%+.2f%% (板%.2f 现%.2f)%s" %
          (code, x["name"], was, x["state"], x["pct"], x["zt"], x["price"],
           "  <<< 炸板!" if x["state"] in ("BROKE", "NORMAL") else ""))
# 上午临界 → 现在
for code in sorted(b_lin - (b_yizi | b_feng)):
    x = now.get(code)
    if not x: continue
    print("  %s %s 临界 → %s 现%+.2f%% 距板%.2f%%%s" %
          (code, x["name"], x["state"], x["pct"], x["dist"],
           "  <<< 已封" if x["state"] in ("SEALED", "ONE_WORD") else ""))

print("\n== 午后新封(不在上午封板名单) ==")
newseal = [x for x in onew + sealed if x["code"] not in b_yizi | b_feng]
for x in sorted(newseal, key=lambda z: -(z.get("board") or 0)):
    print("  %s %s %d板 %+.2f%% 开板?%s src=%s" %
          (x["code"], x["name"], x.get("board") or 0, x["pct"],
           "是" if x["lo"] < x["zt"] - 0.005 else "否", ",".join(x["src"])))

out = {"snap_t": SNAP_T, "n": len(rows), "pool_n": len(univ["codes"]),
       "one_word": [{"code": x["code"], "name": x["name"], "board": x.get("board")} for x in onew],
       "sealed": [{"code": x["code"], "name": x["name"], "board": x.get("board"),
                   "opened": x["lo"] < x["zt"] - 0.005} for x in sealed],
       "broke": [{"code": x["code"], "name": x["name"], "hi_pct": x["hi_pct"]} for x in broke],
       "near": [{"code": x["code"], "name": x["name"], "dist": x["dist"]} for x in near],
       "first": [x["code"] for x in first],
       "up": up, "down": dn, "avg_pct": avg}
json.dump(out, open(os.path.join(ROOT, "data/_an1315_%s.json" % DATE), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nsaved data/_an1315_%s.json" % DATE)
