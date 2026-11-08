#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Batch pull realtime quotes from qt.gtimg.cn for the intraday monitoring pool.
Writes tail_raw/{date}_quotes.json and copies to {date}_zt.json.
Schema: {"date","data":[{"sec_code","sec_name","price","prev_close","open","high","low","vol"}]}
"""
import json, os, sys, time, datetime
import urllib.request

DATE = "2026-11-02"
CLAU = "E:/WorkBuddy_dot_workbuddy/Claw"
UNIV = os.path.join(CLAU, "data", "intraday_universe.json")
OUTDIR = os.path.join(CLAU, "tail_raw")
os.makedirs(OUTDIR, exist_ok=True)

def mkt_prefix(code):
    c = code.strip()
    if c[0] in ("6", "9", "5"):
        return "sh" + c
    if c[0] in ("4", "8"):
        return "bj" + c
    return "sz" + c  # 0,2,3

def fetch_batch(codes):
    q = ",".join(mkt_prefix(c) for c in codes)
    url = "https://qt.gtimg.cn/q=" + q
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
    out = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        key = line.split("=")[0].split("_")[-1]  # e.g. sz000011
        body = line.split('"')[1] if '"' in line else ""
        if not body:
            continue
        f = body.split("~")
        out[key] = f
    return out

def main():
    uni = json.load(open(UNIV, encoding="utf-8"))
    items = uni.get("codes", [])
    # dedupe by code, keep name
    seen = {}
    for it in items:
        c = it["code"]
        if c not in seen:
            seen[c] = it.get("name")
    codes = list(seen.keys())
    print("unique codes:", len(codes))

    batches = [codes[i:i+60] for i in range(0, len(codes), 60)]
    print("batches:", len(batches))

    data = []
    missing = []
    for b in batches:
        got = fetch_batch(b)
        for c in b:
            key = mkt_prefix(c)
            f = got.get(key)
            if not f or len(f) < 35:
                missing.append(c)
                continue
            try:
                price = float(f[3]); prev_close = float(f[4]); openp = float(f[5])
                high = float(f[33]); low = float(f[34]); vol = float(f[6])
            except (ValueError, IndexError):
                missing.append(c)
                continue
            data.append({
                "sec_code": c,
                "sec_name": seen[c] or (f[1] if len(f) > 1 else ""),
                "price": price,
                "prev_close": prev_close,
                "open": openp,
                "high": high,
                "low": low,
                "vol": vol,
            })
        time.sleep(0.3)

    missing = sorted(set(missing))
    print("got:", len(data), "missing:", len(missing))
    if missing:
        print("missing sample:", missing[:30])

    obj = {"date": DATE, "data": data}
    qpath = os.path.join(OUTDIR, f"{DATE}_quotes.json")
    zpath = os.path.join(OUTDIR, f"{DATE}_zt.json")
    with open(qpath, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1)
    with open(zpath, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=1)
    print("written:", qpath)
    # also dump missing list for report
    with open(os.path.join(OUTDIR, f"{DATE}_missing.json"), "w", encoding="utf-8") as fp:
        json.dump({"date": DATE, "missing": missing}, fp, ensure_ascii=False)
    # timestamp
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print("snapshot_time:", ts)
    print("MISSING_PCT:", round(100*len(missing)/len(codes), 2) if codes else 0)

if __name__ == "__main__":
    main()
