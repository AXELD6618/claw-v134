# -*- coding: utf-8 -*-
"""微信 iLink 补发重试（出口 IP 轮换导致 prepare failed ret -2，退避重试可通过）"""
import sys, json, time, os
sys.path.insert(0, r"E:/WorkBuddy_dot_workbuddy/Claw")
sys.stdout.reconfigure(encoding="utf-8")

BASE = r"E:/WorkBuddy_dot_workbuddy/Claw"
DATE = "2026-10-28"
src = os.path.join(BASE, "data", "intraday_push_1315_%s.json" % DATE)
d = json.load(open(src, encoding="utf-8"))
text = d["title"] + "\n\n" + d["body"]

import push_tail_pick as pp

ok, ch, detail, n = False, None, None, 0
for rnd in range(1, 9):
    ok, ch, detail, n = pp._send_resilient(text)
    print("round %d -> ok=%s channel=%s detail=%s" % (rnd, ok, ch, str(detail)[:160]))
    if ok:
        break
    time.sleep(12)

print("DELIVER ok=%s channel=%s tries=%d detail=%s" % (ok, ch, n, detail))
d["ilink_ok"] = bool(ok)
d["ilink_channel"] = ch
d["ilink_detail"] = str(detail)[:300]
json.dump(d, open(src, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
