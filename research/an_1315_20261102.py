# -*- coding: utf-8 -*-
"""13:15 盘中异动研判 — 全量分类 + 状态机对比（vs 12:51 台账）+ 连板计数 + 持仓"""
import json, os, sys

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research.board_limit import board_limit_pct  # 涨跌停幅度单一真相源
DATE = "2026-11-02"
DAILY = os.path.join(ROOT, "backtest_cache/daily")

univ = json.load(open(os.path.join(ROOT, "data/intraday_universe.json"), encoding="utf-8"))
SRC = {x["code"]: x.get("src", []) for x in univ["codes"]}
snap = json.load(open(os.path.join(ROOT, "tail_raw/%s_quotes.json" % DATE), encoding="utf-8"))
rows = snap["data"]
SNAP_T = snap.get("snapshot", "")
print("snapshot =", SNAP_T, " n =", len(rows))


def limit_pct(code, name):
    """委托单一真相源（ST 语义：主板 5%，双创/北交不降板）。"""
    return board_limit_pct(code, is_st=("ST" in str(name or "").upper()))


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
    """连板数（含今日，若今日涨停）；返回 (boards, t1_pct, t1_volratio, limit120)"""
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
    w = cur[-120:] if len(cur) >= 120 else cur
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
    dist = (zt - price) / zt * 100
    sealed = price >= zt - 0.005
    touched = hi >= zt - 0.005
    broke = touched and not sealed          # 曾摸板现未封 = 炸板
    state = "SEALED" if sealed else ("BROKE" if broke else "NORMAL")
    one_word = sealed and lo >= zt - 0.005
    if sealed:
        if one_word: state = "ONE_WORD"
        elif lo >= zt - 0.005: state = "ONE_WORD"
        elif touched and lo < zt: state = "SEALED"   # 曾开板后回封
    recs.append(dict(code=code, name=name, pct=pct, price=price, prev=pc, zt=zt, hi=hi, lo=lo,
                     hi_pct=hi_pct, dist=dist, state=state, open=r["open"],
                     vol=r["vol"], src=SRC.get(code, []), lp=lp))

# ---- 连板计数（仅对有意义的：涨停/炸板/临界/涨≥5%）----
focus = [x for x in recs if x["state"] in ("SEALED", "ONE_WORD", "BROKE") or x["dist"] <= 3.0 or x["pct"] >= 5]
for x in focus:
    b, t1p, t1v, c120 = board_count(x["code"], x["name"])
    x["board"], x["t1_pct"], x["t1_vr"], x["c120"] = b, t1p, t1v, c120

print("\n== 一字板 ==")
for x in sorted([y for y in recs if y["state"] == "ONE_WORD"], key=lambda z: -z["pct"]):
    print("  %s %s %+.2f%% src=%s" % (x["code"], x["name"], x["pct"], ",".join(x["src"])))

print("\n== 封板（非一字）==")
sealed = sorted([y for y in recs if y["state"] == "SEALED"], key=lambda z: -(z.get("board") or 0))
for x in sealed:
    print("  %s %s %d板 %+.2f%% low=%.2f zt=%.2f src=%s" %
          (x["code"], x["name"], x.get("board", 0), x["pct"], x["lo"], x["zt"], ",".join(x["src"])))
print("  封板合计 %d（一字%d + 非一字%d）" % (len(sealed) + len([y for y in recs if y["state"] == "ONE_WORD"]),
                                          len([y for y in recs if y["state"] == "ONE_WORD"]), len(sealed)))

print("\n== 炸板（曾摸板现未封）==")
for x in sorted([y for y in recs if y["state"] == "BROKE"], key=lambda z: -z["hi_pct"]):
    b, t1p, t1v, c120 = board_count(x["code"], x["name"])
    print("  %s %s %d板 高+%.1f%%→现%+.2f%% 回落%.1fpp src=%s" %
          (x["code"], x["name"], b, x["hi_pct"], x["pct"], x["hi_pct"] - x["pct"], ",".join(x["src"])))

print("\n== 临界逼近（距板≤2.5pp，未摸板）==")
near = [y for y in recs if y["state"] == "NORMAL" and y["dist"] <= 2.5 and y["hi"] < y["zt"] - 0.005]
for x in sorted(near, key=lambda z: z["dist"]):
    b, t1p, t1v, c120 = board_count(x["code"], x["name"])
    print("  %s %s %d板 现%.2f 板%.2f 差%.2f%% 现%+.2f%% src=%s" %
          (x["code"], x["name"], b, x["price"], x["zt"], x["dist"], x["pct"], ",".join(x["src"])))
print("  临界合计 %d" % len(near))

print("\n== 冲高回落（高≥+7% 现<+2%）==")
for x in sorted([y for y in recs if y["hi_pct"] >= 7 and y["pct"] < 2], key=lambda z: -(z["hi_pct"] - z["pct"]))[:15]:
    print("  %s %s 高+%.1f%%→现%+.2f%% 回落%.1fpp" % (x["code"], x["name"], x["hi_pct"], x["pct"], x["hi_pct"] - x["pct"]))

print("\n== 破位（≤-5%）==")
for x in sorted([y for y in recs if y["pct"] <= -5], key=lambda z: z["pct"])[:15]:
    print("  %s %s %+.2f%% src=%s" % (x["code"], x["name"], x["pct"], ",".join(x["src"])))

print("\n== 涨幅榜 TOP20 ==")
for x in sorted(recs, key=lambda z: -z["pct"])[:20]:
    print("  %s %s %+.2f%% state=%s" % (x["code"], x["name"], x["pct"], x["state"]))

print("\n== 持仓 ==")
h = json.load(open(os.path.join(ROOT, "current_holdings.json"), encoding="utf-8"))
for p in h["holdings"]:
    m = next((y for y in recs if y["code"] == p["code"]), None)
    if not m:
        print("  %s %s 无报价" % (p["code"], p["name"])); continue
    pnl = (m["price"] - p["cost"]) / p["cost"] * 100
    print("  %s %s 成本%.2f 现%.2f 浮盈%+.2f%% 今日%+.2f%% 高%+.2f%% 低%+.2f%% 板%.2f 距板%.1f%%" %
          (p["code"], p["name"], p["cost"], m["price"], pnl, m["pct"],
           (m["hi"] - m["prev"]) / m["prev"] * 100, (m["lo"] - m["prev"]) / m["prev"] * 100, m["zt"], m["dist"]))

print("\n== 状态机对比 vs 12:51 台账（封板/临界）==")
log = json.load(open(os.path.join(ROOT, "data/intraday_agentic_log.json"), encoding="utf-8"))
prev = {}
for e in log:
    if str(e.get("date")) == DATE and str(e.get("ts", "")).startswith(DATE):
        prev[e["code"]] = e
now = {x["code"]: x for x in recs}
for code, e in prev.items():
    x = now.get(code)
    if not x: continue
    was = "SEALED" if e["type"] == "ZTFD" else ("NEAR" if e["type"] == "LINJIE" else e["type"])
    isnow = "SEALED" if x["state"] == "SEALED" else ("ONE_WORD" if x["state"] == "ONE_WORD"
            else ("BROKE" if x["state"] == "BROKE" else ("NEAR" if x["dist"] <= 2.5 else "NORMAL")))
    flag = "  <<< 升级/降级" if was != isnow else ""
    print("  %s %s %s→%s 现%+.2f%% (12:51 %+.2f%%)%s" %
          (code, x["name"], was, isnow, x["pct"], e.get("chg", 0), flag))

# 落盘供推送层
out = {"snap_t": SNAP_T, "n": len(rows),
       "one_word": [x["code"] for x in recs if x["state"] == "ONE_WORD"],
       "sealed": [x["code"] for x in recs if x["state"] == "SEALED"],
       "broke": [x["code"] for x in recs if x["state"] == "BROKE"],
       "near": [x["code"] for x in near]}
json.dump(out, open(os.path.join(ROOT, "data/_an1315_%s.json" % DATE), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
