# -*- coding: utf-8 -*-
"""补全 enrich_queue_2026-10-30.json picked 的名称 + 当日价格信号 -> data/_names_2026-10-30.json

qt.gtimg.cn 字段序：1=名称 3=现价 32=涨跌幅(%) 36=量比? 实际取 3/32，别写反。
"""
import json
import os
import urllib.request

ROOT = r"E:\WorkBuddy_dot_workbuddy\Claw"
DATA = os.path.join(ROOT, "data")
TODAY = "2026-10-30"

queue = json.load(open(os.path.join(DATA, "enrich_queue_%s.json" % TODAY), encoding="utf-8"))
codes = [p["code"] for p in queue["picked"]]


def mkt(c):
    if c.startswith(("6", "9")):
        return "sh" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return "sz" + c


try:
    cand = json.load(open(os.path.join(DATA, "candidates.json"), encoding="utf-8"))
except Exception:
    cand = {}
if isinstance(cand, list):
    cand = {str(x.get("code", "")): x for x in cand}

out = {}
for i in range(0, len(codes), 20):
    batch = codes[i:i + 20]
    url = "http://qt.gtimg.cn/q=" + ",".join(mkt(c) for c in batch)
    try:
        raw = urllib.request.urlopen(url, timeout=15).read().decode("gbk", "ignore")
    except Exception as e:
        print("[names] batch %d fail %s" % (i, e))
        continue
    for line in raw.split(";"):
        if "=" not in line:
            continue
        f = line.split('="')[-1].strip().strip('"').split("~")
        if len(f) < 33:
            continue
        code = f[2]
        try:
            pct = float(f[32])
        except Exception:
            pct = None
        try:
            price = float(f[3])
        except Exception:
            price = None
        c0 = cand.get(code, {}) or {}
        out[code] = {
            "name": f[1],
            "price": price,
            "pct": pct,
            "tier": c0.get("tier"),
            "signal": c0.get("signal") or c0.get("reason") or "",
            "vol_ratio": c0.get("vol_ratio"),
            "prob": c0.get("prob"),
        }

json.dump(out, open(os.path.join(DATA, "_names_%s.json" % TODAY), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("[names] got %d / %d" % (len(out), len(codes)))
missing = [c for c in codes if c not in out]
if missing:
    print("[names] missing %s" % missing)
for c in codes[:5]:
    print(c, out.get(c))
