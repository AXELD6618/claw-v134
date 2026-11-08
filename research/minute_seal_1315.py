# -*- coding: utf-8 -*-
"""分钟线取封板时间（真实分时，非估算）"""
import json, urllib.request, sys

def pfx(c):
    return "sh" if c.startswith(("60", "68")) else ("bj" if c.startswith(("4", "8")) else "sz")

def minute(code):
    url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=%s%s" % (pfx(code), code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
    d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8"))
    node = d["data"]["%s%s" % (pfx(code), code)]["data"]
    rows = []
    for ln in node["data"]:
        p = ln.split(" ")
        if len(p) >= 3:
            rows.append((p[0], float(p[1])))
    return rows

def analyse(code, lp):
    rows = minute(code)
    if not rows:
        return None
    first = None
    for t, p in rows:
        if p >= lp - 0.001:
            first = t
            break
    # 开板次数：触及涨停后出现低于涨停价
    opens, sealed = 0, False
    for t, p in rows:
        if p >= lp - 0.001:
            sealed = True
        elif sealed:
            opens += 1
            sealed = False
    last = rows[-1]
    return {"first_seal": first, "open_breaks": opens, "last_t": last[0],
            "last_p": last[1], "n": len(rows)}

if __name__ == "__main__":
    for code, lp in [("688585", 185.41), ("603728", 60.54), ("003021", 101.75),
                     ("301428", 44.95), ("001219", 16.48), ("000678", 17.61),
                     ("605333", 21.90), ("002708", 18.94), ("301106", 19.54)]:
        try:
            print(code, analyse(code, lp))
        except Exception as e:
            print(code, "ERR", e)
