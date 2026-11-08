#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""增量富集引擎：把 discovery 命中票经 theme_intel 判定写入 candidate_research.json。

两种输入：
  1. --intel-json PATH（主要路径，由每日 16:30 自动化经 WebSearch 产出）：
     接收 {code: {name, theme, catalyst, fundamental, risk, agent_judgment, ...}}，
     对每条调用 theme_intel.decide_theme_fit(code, e, has_price_signal=True) 落 theme_fit。
  2. 默认（兜底）：读 data/discovery_candidates.json 的 emerg 列表，对其中 research 已含文本
     且 theme_fit=? 的票重新判定（用于 agent 已写 research 但漏置 theme_fit 的情形）。

不覆盖已有 on/weak 研判（除非 --force）。沙箱可达，不依赖 WebSearch。
"""
import os, sys, json, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import theme_intel

RESEARCH_P = os.path.join(ROOT, "data", "candidate_research.json")
DISCOVERY_P = os.path.join(ROOT, "data", "discovery_candidates.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intel-json", default=None, help="WebSearch 产出的研究 json")
    ap.add_argument("--force", action="store_true", help="覆盖已有 on/weak 研判")
    ap.add_argument("--limit", type=int, default=30, help="单次最多应用条数")
    args = ap.parse_args()

    research = json.load(open(RESEARCH_P, encoding="utf-8")) if os.path.exists(RESEARCH_P) else {}
    reg = theme_intel.get_registry()
    applied = []

    if args.intel_json and os.path.exists(args.intel_json):
        intel = json.load(open(args.intel_json, encoding="utf-8"))
        for code, e in list(intel.items())[:args.limit]:
            e = dict(e)
            e.setdefault("source_date", datetime.date.today().isoformat())
            old = research.get(code, {}).get("theme_fit")
            if old in ("on", "weak") and not args.force:
                continue
            fit, info = theme_intel.decide_theme_fit(code, e, has_price_signal=True, registry=reg)
            e["theme_fit"] = fit
            e["theme_intel"] = info
            research[code] = e
            applied.append((code, e.get("name"), old, fit, info.get("reason")))
    else:
        disc = json.load(open(DISCOVERY_P, encoding="utf-8")) if os.path.exists(DISCOVERY_P) else {}
        emerg = disc.get("emerg", []) if isinstance(disc, dict) else []
        n = 0
        for e in emerg:
            code = e.get("code")
            if not code:
                continue
            re = research.get(code)
            if re and re.get("theme_fit") in ("?", None, "") and n < args.limit:
                fit, info = theme_intel.decide_theme_fit(code, re, has_price_signal=True, registry=reg)
                if fit != re.get("theme_fit"):
                    re["theme_fit"] = fit
                    re["theme_intel"] = info
                    applied.append((code, re.get("name"), "?", fit, info.get("reason")))
                    n += 1

    json.dump(research, open(RESEARCH_P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    on = sum(1 for c in research.values() if c.get("theme_fit") == "on")
    weak = sum(1 for c in research.values() if c.get("theme_fit") == "weak")
    emerging = sum(1 for c in research.values() if c.get("theme_fit") == "emerging")
    print("[enrich] 本次应用 %d 条 | 全量 %d (on=%d weak=%d emerging=%d)" %
          (len(applied), len(research), on, weak, emerging))
    for a in applied[:30]:
        print("  %s %s 旧=%s -> %s (%s)" % a)


if __name__ == "__main__":
    main()
