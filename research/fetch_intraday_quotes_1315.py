# -*- coding: utf-8 -*-
"""盘中快照拉取（13:15 轮）—— 仅本任务用，不改动生产代码"""
import json, sys, time, urllib.request, os

DATE = "20261028"
BASE = r"E:/WorkBuddy_dot_workbuddy/Claw"
UNIV = os.path.join(BASE, "data", "intraday_universe.json")
OUT_DIR = os.path.join(BASE, "tail_raw")

def pfx(code):
    if code.startswith(("60", "68", "90", "51", "58", "11", "50")):
        return "sh"
    if code.startswith(("4", "8", "92")):
        return "bj"
    return "sz"

def fetch(batch):
    q = ",".join(pfx(c["code"]) + c["code"] for c in batch)
    url = "https://qt.gtimg.cn/q=" + q
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://gu.qq.com/"})
    raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
    out = []
    for line in raw.split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        body = line.split("=", 1)[1].strip().strip('"')
        f = body.split("~")
        if len(f) < 35:
            continue
        try:
            out.append({
                "sec_code": f[2], "sec_name": f[1],
                "price": float(f[3]), "prev_close": float(f[4]),
                "open": float(f[5]), "vol": float(f[6]),
                "t": f[30], "chg": float(f[32]),
                "high": float(f[33]), "low": float(f[34]),
                "amount": float(f[37]) if len(f) > 37 and f[37] else 0.0,
                "turnover": float(f[38]) if len(f) > 38 and f[38] else 0.0,
                "float_mv": float(f[44]) if len(f) > 44 and f[44] else 0.0,
            })
        except Exception:
            continue
    return out

def main():
    d = json.load(open(UNIV, encoding="utf-8"))
    codes = d["codes"]
    rows, seen = [], set()
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        try:
            got = fetch(batch)
        except Exception as e:
            print("[warn] batch %d fail %s" % (i, e))
            got = []
        for r in got:
            if r["sec_code"] not in seen:
                seen.add(r["sec_code"])
                rows.append(r)
        time.sleep(0.8)
    os.makedirs(OUT_DIR, exist_ok=True)
    payload = {"data": [{k: r[k] for k in ("sec_code", "sec_name", "price", "prev_close",
                                          "open", "high", "low", "vol")} for r in rows],
               "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "date": DATE, "n": len(rows), "pool_n": len(codes)}
    for fn in (DATE + "_quotes.json", DATE + "_zt.json"):
        with open(os.path.join(OUT_DIR, fn), "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)

    # 回读核对
    chk = json.load(open(os.path.join(OUT_DIR, DATE + "_quotes.json"), encoding="utf-8"))
    have = {x["sec_code"] for x in chk["data"]}
    miss = [c["code"] for c in codes if c["code"] not in have]
    print("FETCH rows=%d pool=%d readback=%d missing=%d" % (len(rows), len(codes), chk["n"], len(miss)))
    if miss:
        print("MISSING:", ",".join(miss[:60]))
    ts = sorted({r["t"] for r in rows})
    print("TS:", ts[:3], "...", ts[-3:] if len(ts) > 3 else "")
    json.dump(rows, open(os.path.join(OUT_DIR, "_riched_" + DATE + ".json"), "w", encoding="utf-8"),
              ensure_ascii=False)

main()
