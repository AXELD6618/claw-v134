# -*- coding: utf-8 -*-
"""合并 4 个富集分片 -> data/candidate_research.json（2026-11-02）

规则：
  - 只写入 enrich_queue_2026-11-02.json 里 picked 的票，不多不少
  - 每条强制写 researched_at + source_date
  - theme_fit 由 theme_intel.decide_theme_fit 现算（has_price_signal=True），不采信子代理主观判定
  - 交集体检分母用 enrich_queue 的 total_candidates（真实池），不用 lifecycle entries
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

DATA = os.path.join(ROOT, "data")
TODAY = "2026-11-02"
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

allfit = collections.Counter(v.get("theme_fit", "?") for v in research.values())
print("[merge] 全库 theme_fit 分布: %s" % dict(allfit))
allboth = sum(1 for v in research.values() if v.get("researched_at") and v.get("source_date"))
print("[merge] 全库双字段 %d/%d = %.1f%%" % (allboth, len(research), 100.0 * allboth / max(1, len(research))))

# 交集体检：研究库 vs 今日真实候选池（分母 = enrich_queue.total_candidates）
total = queue.get("total_candidates") or 0
cand_p = os.path.join(DATA, "candidates.json")
pool_codes = set()
try:
    c = json.load(open(cand_p, encoding="utf-8"))
    for t in ("tier1", "tier2"):
        for x in c.get(t) or []:
            pool_codes.add(str(x.get("code", "")))
except Exception as e:
    print("[merge] candidates 读取失败 %s" % e)
pool_codes = {x for x in pool_codes if len(x) == 6 and x.isdigit()}
inter = set(research) & pool_codes
print("[merge] 池 %d(声明%d)｜研究库 %d｜交集 %d = %.1f%%" % (
    len(pool_codes), total, len(research), len(inter), 100.0 * len(inter) / max(1, len(pool_codes))))
on_codes = sorted([c for c in pool_codes if research.get(c, {}).get("theme_fit") == "on"])
print("[merge] 池内 theme_fit=on: %d 只 %s" % (len(on_codes), on_codes))

# 覆盖率（分母=planner universe，与 enrich_queue.coverage_before 同口径）
q2 = json.load(open(QUEUE_P, encoding="utf-8"))
after_cov = 0
disc = {}
try:
    disc = json.load(open(os.path.join(DATA, "discovery_candidates.json"), encoding="utf-8"))
except Exception:
    pass
universe = set(pool_codes) | {str(e.get("code")) for e in (disc.get("entries") or [])}
covered_after = 0
for c_ in universe:
    r = research.get(c_) or {}
    if r and str(r.get("theme") or "").strip() not in ("", "?", "无"):
        covered_after += 1
print("[merge] universe %d｜富集后覆盖 %d = %.2f%%（before %.2f%%）" % (
    len(universe), covered_after, 100.0 * covered_after / max(1, len(universe)),
    100.0 * (q2.get("coverage_before") or 0)))
