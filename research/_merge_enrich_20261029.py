# -*- coding: utf-8 -*-
"""合并 4 个富集分片 -> data/candidate_research.json（2026-10-29）

规则：
  - 只写入 enrich_queue_2026-10-29.json 里 picked 的票，不多不少
  - 每条强制写 researched_at + source_date
  - theme_fit 由 theme_intel.decide_theme_fit 现算（has_price_signal=True），不采信子代理主观判定
"""
import json
import os
import shutil
import sys
import datetime

ROOT = r"E:\WorkBuddy_dot_workbuddy\Claw"
sys.path.insert(0, os.path.join(ROOT, "research"))
import theme_intel  # noqa: E402

DATA = os.path.join(ROOT, "data")
TODAY = "2026-10-29"
RESEARCH_P = os.path.join(DATA, "candidate_research.json")
QUEUE_P = os.path.join(DATA, "enrich_queue_%s.json" % TODAY)
FRAGS = [os.path.join(DATA, "_enrich_frag_%s.json" % s) for s in "abcd"]

research = json.load(open(RESEARCH_P, encoding="utf-8"))
legacy_n = len(research)
queue = json.load(open(QUEUE_P, encoding="utf-8"))
picked = [p["code"] for p in queue["picked"]]

frag = {}
for f in FRAGS:
    if not os.path.exists(f):
        print("[merge] MISSING frag %s" % f)
        continue
    d = json.load(open(f, encoding="utf-8"))
    print("[merge] %s -> %d 条" % (os.path.basename(f), len(d)))
    frag.update(d)

missing = [c for c in picked if c not in frag]
extra = [c for c in frag if c not in picked]
print("[merge] picked=%d 合并得到=%d 缺=%d 多余=%d" % (len(picked), len(frag), len(missing), len(extra)))
if missing:
    print("[merge] 缺失代码: %s" % ",".join(missing))

names = {}
np_ = os.path.join(DATA, "_names_%s.json" % TODAY)
if os.path.exists(np_):
    names = json.load(open(np_, encoding="utf-8"))

written = 0
both = 0
fit_dist = {}
new_codes = []
nodata = []
for code in picked:
    src = frag.get(code)
    if not src:
        continue
    entry = {
        "name": src.get("name") or (names.get(code, {}) or {}).get("name", "") or research.get(code, {}).get("name", ""),
        "theme": src.get("theme") or "无数据源",
        "catalyst": src.get("catalyst") or "无数据源",
        "fundamental": src.get("fundamental") or "无数据源",
        "risk": src.get("risk") or "无数据源",
        "agent_judgment": src.get("agent_judgment") or "无数据源",
        "source_date": src.get("source_date") or TODAY,
        "researched_at": src.get("researched_at") or TODAY,
        "sources": src.get("sources") or [],
    }
    try:
        fit, info = theme_intel.decide_theme_fit(code, entry, has_price_signal=True)
    except Exception as e:  # 判定失败不得阻塞落盘
        fit, info = "?", {"reason": "decide_error:%s" % e}
    entry["theme_fit"] = fit
    entry["theme_intel"] = info
    if code not in research:
        new_codes.append(code)
    research[code] = entry
    written += 1
    fit_dist[fit] = fit_dist.get(fit, 0) + 1
    if entry.get("researched_at") and entry.get("source_date"):
        both += 1
    if entry["theme"] == "无数据源":
        nodata.append(code)

bak = RESEARCH_P + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M")
shutil.copy2(RESEARCH_P, bak)
json.dump(research, open(RESEARCH_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("[merge] 写入 %d 条（新增 %d / 覆盖 %d），备份 %s" % (written, len(new_codes), written - len(new_codes), os.path.basename(bak)))
print("[merge] 双字段齐全 %d/%d = %.1f%%" % (both, written, 100.0 * both / max(1, written)))
print("[merge] 新批 theme_fit 分布: %s" % fit_dist)
print("[merge] 库总量 %d -> %d" % (legacy_n, len(research)))
print("[merge] 无数据源条目: %d %s" % (len(nodata), nodata))

# 交集体检：研究库 vs 今日活跃池
life = json.load(open(os.path.join(DATA, "pool_lifecycle.json"), encoding="utf-8"))
today_rec = [r for r in life if r.get("date") == TODAY]
pool_codes = set()
for r in today_rec:
    for k in ("entries", "evicted"):
        for e in r.get(k) or []:
            pool_codes.add(str(e.get("code", "")))
pool_codes = {c for c in pool_codes if len(c) == 6 and c.isdigit()}
inter = set(research) & pool_codes
print("[merge] 池活跃 %d 只｜研究库 %d 只｜交集 %d = %.1f%%" % (
    len(pool_codes), len(research), len(inter), 100.0 * len(inter) / max(1, len(pool_codes))))
