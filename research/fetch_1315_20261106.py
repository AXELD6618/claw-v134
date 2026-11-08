# -*- coding: utf-8 -*-
"""13:15 盘中异动 2026-11-06 — 腾讯 gtimg 批量拉取 + 落盘"""
import json, os, sys, time
import urllib.request

ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = sys.argv[1] if len(sys.argv) > 1 else "2026-11-06"
TAG = sys.argv[2] if len(sys.argv) > 2 else ""
univ = json.load(open(os.path.join(ROOT, "data/intraday_universe.json"), encoding="utf-8"))
codes = [x["code"] for x in univ["codes"]]
names = {x["code"]: x.get("name") or "" for x in univ["codes"]}


def pref(c):
    if c.startswith(("6", "9")): return "sh" + c
    if c.startswith(("4", "8", "92")): return "bj" + c
    return "sz" + c


def fetch(batch):
    q = ",".join(pref(c) for c in batch)
    url = "https://qt.gtimg.cn/q=" + q
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://qt.gtimg.cn/"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
    out = {}
    for seg in raw.split(";"):
        seg = seg.strip()
        if "=" not in seg or "~" not in seg: continue
        body = seg.split("=", 1)[1].strip().strip('"')
        f = body.split("~")
        if len(f) < 35: continue
        try:
            code = f[2]
            rec = dict(sec_code=code, sec_name=f[1], price=float(f[3]), prev_close=float(f[4]),
                       open=float(f[5]), vol=float(f[6]), time=f[30],
                       high=float(f[33]), low=float(f[34]))
            rec["chg_pct"] = round((rec["price"] / rec["prev_close"] - 1) * 100, 2) if rec["prev_close"] > 0 else 0.0
            if rec["price"] <= 0: continue
            out[code] = rec
        except Exception:
            continue
    return out


allrows = {}
B = 60
t0 = time.time()
for i in range(0, len(codes), B):
    batch = codes[i:i + B]
    for attempt in range(3):
        try:
            allrows.update(fetch(batch))
            break
        except Exception as e:
            print("  batch %d retry %d: %s" % (i, attempt, e)); time.sleep(1)
    print("  batch %d-%d done, cum=%d" % (i, i + len(batch), len(allrows)))

data = [allrows[c] for c in codes if c in allrows]
snap_t = max((r["time"] for r in data), default="")
miss = [c for c in codes if c not in allrows]
print("FETCH n=%d / pool=%d miss=%d snap=%s elapsed=%.1fs" %
      (len(data), len(codes), len(miss), snap_t, time.time() - t0))
if miss:
    print("MISS:", ",".join("%s(%s)" % (c, names.get(c, "")) for c in miss[:40]))

payload = {"date": DATE, "snapshot": snap_t, "n": len(data), "data": data}
suffix = ("_" + TAG) if TAG else ""
for fn in ("%s_quotes%s.json" % (DATE, suffix), "%s_zt%s.json" % (DATE, suffix)):
    p = os.path.join(ROOT, "tail_raw", fn)
    json.dump(payload, open(p, "w", encoding="utf-8"), ensure_ascii=False)

back = json.load(open(os.path.join(ROOT, "tail_raw", "%s_quotes%s.json" % (DATE, suffix)), encoding="utf-8"))
print("READBACK %s n=%d snapshot=%s" % (DATE, len(back["data"]), back.get("snapshot")))
if len(miss) / max(1, len(codes)) > 0.20:
    print("!! 缺票 >20%%")
