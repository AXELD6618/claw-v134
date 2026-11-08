# -*- coding: utf-8 -*-
"""合并 4 个富集分片 -> data/candidate_research.json（2026-11-03）

规则：
  - 只写入 enrich_queue_2026-11-03.json 里 picked 的票，不多不少
  - 每条强制写 researched_at + source_date
  - theme_fit 由 theme_intel.decide_theme_fit 现算（has_price_signal=True），不采信子代理主观判定
  - 交集体检分母用真实池（candidates.json tier1+tier2），不用 lifecycle entries
  - 富集后覆盖率用 enrich_planner.build_plan 只算不落盘（不覆盖当日派单凭证）
"""
import json
import os
import shutil
import sys
import datetime
import collections

ROOT = r"E:\WorkBuddy_dot_workbuddy\Claw"
sys.path.insert(0, os.path.join(ROOT, "research"))
import theme_intel  # noqa: E402
import enrich_planner  # noqa: E402

DATA = os.path.join(ROOT, "data")
TODAY = "2026-11-03"
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
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print("[merge] BAD frag %s -> %s" % (os.path.basename(f), e))
        continue
    print("[merge] %s -> %d 条" % (os.path.basename(f), len(d)))
    frag.update(d)

missing = [c for c in picked if c not in frag]
extra = [c for c in frag if c not in picked]
print("[merge] picked=%d 合并得到=%d 缺=%d 多余=%d" % (len(picked), len(frag), len(missing), len(extra)))
if missing:
    print("[merge] 缺失代码: %s" % ",".join(missing))
if extra:
    print("[merge] 多余代码: %s" % ",".join(extra))

names = {}
np_ = os.path.join(DATA, "_names_%s.json" % TODAY)
if os.path.exists(np_):
    names = json.load(open(np_, encoding="utf-8"))

written = 0
both = 0
fit_dist = {}
new_codes = []
nodata = []
srcdate_dist = collections.Counter()


def _clean(v, default="无数据源"):
    if v is None:
        return default
    s = str(v).strip()
    return s if s else default


for code in picked:
    src = frag.get(code)
    if not src:
        continue
    entry = {
        "name": _clean(src.get("name"), "") or (names.get(code, {}) or {}).get("name", "") or research.get(code, {}).get("name", ""),
        "theme": _clean(src.get("theme")),
        "catalyst": _clean(src.get("catalyst")),
        "fundamental": _clean(src.get("fundamental")),
        "risk": _clean(src.get("risk")),
        "agent_judgment": _clean(src.get("agent_judgment")),
        "source_date": _clean(src.get("source_date"), TODAY),
        "researched_at": _clean(src.get("researched_at"), TODAY),
        "sources": src.get("sources") or [],
    }
    try:
        fit, info = theme_intel.decide_theme_fit(code, entry, has_price_signal=True)
    except Exception as e:
        fit, info = "?", {"reason": "decide_error:%s" % e}
    entry["theme_fit"] = fit
    entry["theme_intel"] = info
    if code not in research:
        new_codes.append(code)
    research[code] = entry
    written += 1
    fit_dist[fit] = fit_dist.get(fit, 0) + 1
    srcdate_dist[entry["source_date"]] += 1
    if entry.get("researched_at") and entry.get("source_date"):
        both += 1
    if entry["theme"] == "无数据源":
        nodata.append(code)

bak = RESEARCH_P + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M")
shutil.copy2(RESEARCH_P, bak)
json.dump(research, open(RESEARCH_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("[merge] 写入 %d 条（新增 %d / 覆盖 %d），备份 %s" % (written, len(new_codes), written - len(new_codes), os.path.basename(bak)))
print("[merge] 双字段齐全 %d/%d = %.1f%%" % (both, written, 100.0 * both / max(1, written)))
print("[merge] 新批 theme_fit 分布: %s" % dict(fit_dist))
print("[merge] 新批 source_date 分布: %s" % dict(sorted(srcdate_dist.items(), reverse=True)))
print("[merge] 库总量 %d -> %d" % (legacy_n, len(research)))
print("[merge] 无数据源条目: %d %s" % (len(nodata), nodata))

allfit = collections.Counter(v.get("theme_fit", "?") for v in research.values())
print("[merge] 全库 theme_fit 分布: %s" % dict(allfit))
allboth = sum(1 for v in research.values() if v.get("researched_at") and v.get("source_date"))
print("[merge] 全库双字段 %d/%d = %.1f%%" % (allboth, len(research), 100.0 * allboth / max(1, len(research))))
old = [c for c, v in research.items() if not v.get("researched_at")]
print("[merge] 全库仍缺 researched_at 的历史条目: %d" % len(old))

# 交集体检：研究库 vs 真实池（candidates.json tier1+tier2）
pool_codes = set()
try:
    c = json.load(open(os.path.join(DATA, "candidates.json"), encoding="utf-8"))
    for t in ("tier1", "tier2"):
        for x in c.get(t) or []:
            pool_codes.add(str(x.get("code", "")))
except Exception as e:
    print("[merge] candidates 读取失败 %s" % e)
pool_codes = {x for x in pool_codes if len(x) == 6 and x.isdigit()}
inter = set(research) & pool_codes
print("[merge] 池 %d（candidates 日期 %s）｜研究库 %d｜交集 %d = %.1f%%" % (
    len(pool_codes), (c.get("date") if isinstance(c, dict) else "?"), len(research), len(inter),
    100.0 * len(inter) / max(1, len(pool_codes))))
on_codes = sorted([x for x in pool_codes if research.get(x, {}).get("theme_fit") == "on"])
print("[merge] 池内 theme_fit=on: %d 只 %s" % (len(on_codes), on_codes))
poolfit = collections.Counter(research.get(x, {}).get("theme_fit", "?") for x in pool_codes)
print("[merge] 池内 theme_fit 分布: %s" % dict(poolfit))

# 富集后覆盖率：用 build_plan 复算（只算不落盘）
try:
    plan = enrich_planner.build_plan(TODAY)
    print("[merge] 复算 todo=%s quota=%s 覆盖率=%.2f%%（%d/%d，before %.2f%%）" % (
        plan.get("todo"), plan.get("quota"), 100.0 * (plan.get("coverage_before") or 0),
        plan.get("covered") or 0, plan.get("total_candidates") or 0,
        100.0 * (queue.get("coverage_before") or 0)))
except Exception as e:
    print("[merge] build_plan 复算失败 %s" % e)
