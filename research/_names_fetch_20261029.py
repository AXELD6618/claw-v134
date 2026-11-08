# -*- coding: utf-8 -*-
"""批量补 name + 当日行情（腾讯 qt.gtimg.cn），供富集合并用。
字段序注意：parts[1]=名称 parts[3]=现价 parts[32]=涨跌幅 parts[49]=量比
"""
import json, os, sys, time, urllib.request

ROOT = r"E:\WorkBuddy_dot_workbuddy\Claw"
DATA = os.path.join(ROOT, "data")
TODAY = "2026-10-29"

queue = json.load(open(os.path.join(DATA, "enrich_queue_%s.json" % TODAY), encoding="utf-8"))
codes = [p["code"] for p in queue["picked"]]


def mkt(c):
    return ("sh" if c[0] in "6" else ("bj" if c[0] in "489" else "sz")) + c


out = {}
B = 20
for i in range(0, len(codes), B):
    chunk = codes[i:i + B]
    url = "http://qt.gtimg.cn/q=" + ",".join(mkt(c) for c in chunk)
    try:
        raw = urllib.request.urlopen(url, timeout=15).read().decode("gbk", "ignore")
    except Exception as e:
        print("[names] batch %d fail %s" % (i, e))
        continue
    for line in raw.split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('"')
        p = body.split("~")
        if len(p) < 50:
            continue
        code = p[2]
        out[code] = {
            "name": p[1],
            "price": p[3],
            "chg": p[32],
            "volr": p[49] if len(p) > 49 else "",
            "turnover": p[38] if len(p) > 38 else "",
            "amount": p[37] if len(p) > 37 else "",
            "high": p[33], "low": p[34], "prev": p[4], "open": p[5],
        }
    time.sleep(0.3)

json.dump(out, open(os.path.join(DATA, "_names_%s.json" % TODAY), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("[names] 取到 %d/%d" % (len(out), len(codes)))
miss = [c for c in codes if c not in out]
if miss:
    print("[names] 缺失: %s" % ",".join(miss))
for c in codes[:60]:
    v = out.get(c)
    if v:
        print("%s %-6s chg=%s%% volr=%s 价=%s" % (c, v["name"], v["chg"], v["volr"], v["price"]))
