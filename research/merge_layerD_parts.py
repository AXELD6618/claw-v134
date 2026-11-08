#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Merge per-batch part files into the final Layer D fresh snapshot."""
import json, glob, os

OUT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"
parts = sorted(glob.glob(os.path.join(OUT, "spring_soe_layerD_broad_fresh.part*.json")))
allrecs = []
for p in parts:
    try:
        allrecs += json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print("skip part %s: %s" % (p, e))

seen = {}
for r in allrecs:
    seen[r["code"]] = r  # last wins (later batch overwrites)
final = sorted(seen.values(), key=lambda x: x["code"])
dst = os.path.join(OUT, "spring_soe_layerD_broad_fresh.json")
json.dump(final, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("MERGED codes=%d from parts=%d -> %s" % (len(final), len(parts), dst))
