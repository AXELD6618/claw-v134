# -*- coding: utf-8 -*-
"""16:30 任务收尾体检：覆盖率、池 theme_fit 分布、交集、驱逐比例。只读，不写业务文件。"""
import json, os, sys, collections

ROOT = r"E:\WorkBuddy_dot_workbuddy\Claw"
sys.path.insert(0, os.path.join(ROOT, "research"))
import enrich_planner as EP

DATA = os.path.join(ROOT, "data")
TODAY = "2026-10-28"

plan_after = EP.build_plan(date=TODAY)
print("[after] todo=%d quota=%d covered=%d total=%d coverage=%.2f%%"
      % (plan_after["todo"], plan_after["quota"], plan_after["covered"],
         plan_after["total_candidates"], 100 * plan_after["coverage_before"]))

research = json.load(open(os.path.join(DATA, "candidate_research.json"), encoding="utf-8"))
life = json.load(open(os.path.join(DATA, "pool_lifecycle.json"), encoding="utf-8"))
recs = [r for r in life if r.get("date") == TODAY]
ent = recs[-1].get("entries", []) if recs else []
evi = recs[-1].get("evicted", []) if recs else []

pool = []
for e in ent:
    c = str(e.get("code", ""))
    if len(c) == 6 and c.isdigit():
        pool.append(c)
poolset = set(pool)

print("[pool] entries=%d evicted=%d 活跃合计=%d" % (len(ent), len(evi), len(ent) + len(evi)))
if evi:
    print("[pool] 驱逐占比=%.1f%% (>15%% 需告警)  cap=%s"
          % (100.0 * len(evi) / max(1, len(ent) + len(evi)), __import__("p1_discovery").pool_cap()))

# 池内 theme_fit 分布：优先读研究库（权威），缺失记 ?
dist = collections.Counter()
on_codes = []
for c in pool:
    fit = (research.get(c) or {}).get("theme_fit", "?")
    dist[fit] += 1
    if fit == "on":
        on_codes.append((c, (research.get(c) or {}).get("name", "")))
print("[pool] theme_fit 分布 %s" % dict(dist))
print("[pool] on=%d 只: %s" % (len(on_codes), on_codes))

inter = poolset & set(research)
print("[x] 池活跃 %d｜研究库 %d｜交集 %d = %.1f%% 个池内命中"
      % (len(poolset), len(research), len(inter), 100.0 * len(inter) / max(1, len(poolset))))

# 本批双字段比例
q = json.load(open(os.path.join(DATA, "enrich_queue_%s.json" % TODAY), encoding="utf-8"))
picked = [p["code"] for p in q["picked"]]
both = sum(1 for c in picked
           if (research.get(c) or {}).get("researched_at") and (research.get(c) or {}).get("source_date"))
print("[batch] picked=%d 双字段齐全=%d/%d = %.1f%%" % (len(picked), both, len(picked),
                                                       100.0 * both / max(1, len(picked))))
# 全库合规率
ok = sum(1 for v in research.values() if v.get("researched_at") and v.get("source_date"))
print("[lib] 全库 %d 条，双字段齐全 %d = %.1f%%" % (len(research), ok, 100.0 * ok / len(research)))
