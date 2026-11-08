# -*- coding: utf-8 -*-
"""11:30(午收) vs 13:18 状态机差异 + 最终榜单"""
import json, os

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from research.board_limit import board_limit_pct  # 涨跌停幅度单一真相源
DATE = "2026-11-02"

prev = json.load(open(os.path.join(ROOT, "_intraday_anoms_2026-11-02_1125.json"), encoding="utf-8"))
snap = json.load(open(os.path.join(ROOT, "tail_raw/%s_quotes.json" % DATE), encoding="utf-8"))["data"]
NOW = {r["sec_code"]: r for r in snap}


def lp(code, name):
    """委托单一真相源（ST 语义：主板 5%，双创/北交不降板）。"""
    return board_limit_pct(code, is_st=("ST" in str(name or "").upper()))


def state(r):
    code, name = r["sec_code"], r["sec_name"]
    pc = r["prev_close"]
    if pc <= 0: return "NA"
    zt = round(pc * (1 + lp(code, name)), 2)
    if r["price"] >= zt - 0.005:
        return "SEALED"
    if r["high"] >= zt - 0.005:
        return "BROKE"
    if (zt - r["price"]) / zt * 100 <= 2.5:
        return "NEAR"
    return "NORMAL"


# ---- 11:30 封板集合（board 已封板/一字板 + first_board 全部）----
p_sealed = {}
for x in prev["board"] + prev["first_board"]:
    if x["board"] in ("已封板", "一字板"):
        p_sealed[x["sec_code"]] = x
p_near = {x["sec_code"]: x for x in prev["board"] if x["board"] == "临界"}

# ---- 13:18 集合 ----
n_sealed, n_broke, n_near = {}, {}, {}
for r in snap:
    s = state(r)
    if s == "SEALED": n_sealed[r["sec_code"]] = r
    elif s == "BROKE": n_broke[r["sec_code"]] = r
    elif s == "NEAR": n_near[r["sec_code"]] = r

print("11:30 封板 %d（含首板）, 临界 %d" % (len(p_sealed), len(p_near)))
print("13:18 封板 %d, 炸板 %d, 临界 %d" % (len(n_sealed), len(n_broke), len(n_near)))

print("\n#### A. 11:30 封板 → 13:18 炸板（午后首轮破封，最高优先级）####")
br = [c for c in p_sealed if c in n_broke]
for c in sorted(br, key=lambda k: NOW[k]["price"] / NOW[k]["prev_close"]):
    r, p = NOW[c], p_sealed[c]
    zt = round(r["prev_close"] * (1 + lp(c, r["sec_name"])), 2)
    print("  %s %-6s 现%+.2f%%  高+%.1f%%  回落%.1fpp  量比%.2f  板=%s  盘中低%.2f(板%.2f) src=%s" %
          (c, r["sec_name"], (r["price"] - r["prev_close"]) / r["prev_close"] * 100,
           (r["high"] - r["prev_close"]) / r["prev_close"] * 100,
           (r["high"] - r["price"]) / r["prev_close"] * 100,
           p.get("vol_ratio") or 0, p.get("board"), r["low"], zt, p.get("src")))

print("\n#### B. 11:30 封板 → 13:18 仍封（守住）####")
hold = [c for c in p_sealed if c in n_sealed]
for c in hold:
    r = NOW[c]
    print("  %s %-6s %+.2f%% 低%.2f vp=%.1f src=%s" %
          (c, r["sec_name"], (r["price"] - r["prev_close"]) / r["prev_close"] * 100,
           r["low"], p_sealed[c].get("vol_ratio") or 0, p_sealed[c].get("src")))

print("\n#### C. 11:30 未封 → 13:18 新封（午后新封）####")
new = [c for c in n_sealed if c not in p_sealed]
for c in new:
    r = NOW[c]
    was = "临界" if c in p_near else "普通"
    print("  %s %-6s %+.2f%% 11:30状态=%s 现板%.2f 低%.2f src=%s" %
          (c, r["sec_name"], (r["price"] - r["prev_close"]) / r["prev_close"] * 100,
           was, round(r["prev_close"] * (1 + lp(c, r["sec_name"])), 2), r["low"],
           (p_sealed.get(c) or p_near.get(c) or {}).get("src", "")))

print("\n#### D. 11:30 临界 → 13:18 去向 ####")
for c, p in p_near.items():
    r = NOW.get(c)
    if not r: continue
    s = state(r)
    print("  %s %-6s %s → %s  11:30 %+.2f%% → 13:18 %+.2f%%  板%.2f src=%s" %
          (c, r["sec_name"], p["board"], s, p.get("chg", 0),
           (r["price"] - r["prev_close"]) / r["prev_close"] * 100, p.get("limit_price"), p.get("src")))

print("\n#### E. 13:18 仍临界 ####")
for c, r in sorted(n_near.items(), key=lambda kv: kv[1]["price"] * 0):
    zt = round(r["prev_close"] * (1 + lp(c, r["sec_name"])), 2)
    d = (zt - r["price"]) / zt * 100
    print("  %s %-6s 现%.2f 板%.2f 差%.2f%% 现%+.2f%% 高%+.2f%% src=%s" %
          (c, r["sec_name"], r["price"], zt, d,
           (r["price"] - r["prev_close"]) / r["prev_close"] * 100,
           (r["high"] - r["prev_close"]) / r["prev_close"] * 100,
           (p_sealed.get(c) or p_near.get(c) or {}).get("src", "")))

print("\n#### F. 炸板回落幅度 TOP10（含11:30前已炸）####")
allb = []
for c, r in n_broke.items():
    allb.append((c, r, (r["high"] - r["price"]) / r["prev_close"] * 100))
for c, r, drop in sorted(allb, key=lambda z: -z[2])[:10]:
    print("  %s %-6s 高+%.1f%% → 现%+.2f%%  回落%.1fpp 量比%.1f src=%s" %
          (c, r["sec_name"], (r["high"] - r["prev_close"]) / r["prev_close"] * 100,
           (r["price"] - r["prev_close"]) / r["prev_close"] * 100, drop,
           (p_sealed.get(c) or {}).get("vol_ratio") or 0,
           (p_sealed.get(c) or p_near.get(c) or {}).get("src", "")))

print("\n#### G. 指数/板块代理：池内涨跌分布 ####")
pcts = [(r["price"] - r["prev_close"]) / r["prev_close"] * 100 for r in snap if r["prev_close"] > 0]
up = sum(1 for p in pcts if p > 0); dn = sum(1 for p in pcts if p < 0)
print("  n=%d 上涨%d 下跌%d 平%d 均值%+.2f%% 中位%+.2f%%" %
      (len(pcts), up, dn, len(pcts) - up - dn, sum(pcts) / len(pcts), sorted(pcts)[len(pcts) // 2]))
