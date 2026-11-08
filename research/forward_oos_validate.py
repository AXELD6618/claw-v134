# -*- coding: utf-8 -*-
"""
research/forward_oos_validate.py — 多 pivot 历史回放聚合验证(不污染真实台账)

目的: 证明 grail_hit_forward_oos 的"标注 + 复核"管线在 n>=200 时能正确复现
历史 OOS 的 CAT_PROXIMITY PROMOTE_GRAIL 边(lift>=1.5 / 二项z>=2 / 超额>0 / PCV>=0.60),
即评估器本身是健全的, 而非仅因 n 不足而 INSUFFICIENT。

用法:
  .venv/Scripts/python.exe research/forward_oos_validate.py [--pivots 20260101 20260201 ...]
默认在 2026 年内每 ~2 周取一个 pivot, 累积标注后跑 evaluate(写 _grail_hit_agg_sim.jsonl)。
"""
from __future__ import annotations
import sys, os, json, glob, argparse, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.grail_hit_forward_oos import (
    label_record, evaluate, CACHE, compute_baseline,
)

AGG_PATH = CACHE / "_grail_hit_agg_sim.jsonl"


def _default_pivots(step=14):
    """2026 年内每 step 天一个 pivot(日历日, selector 会落到最近交易日)。"""
    out = []
    d = dt.date(2026, 1, 15)
    while d <= dt.date(2026, 10, 1):
        out.append(int(d.strftime("%Y%m%d")))
        d += dt.timedelta(days=step)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pivots", nargs="*", type=int, default=None, help="显式 pivot 列表")
    ap.add_argument("--step", type=int, default=14, help="默认 pivot 间隔天数(默认14)")
    ap.add_argument("--top-n", type=int, default=30)
    a = ap.parse_args()
    pivots = a.pivots or _default_pivots(step=a.step)

    from research.grail_hit_selector import select_candidates

    recs = []
    for piv in pivots:
        try:
            res = select_candidates(as_of=piv, K=10, top_n=a.top_n, require_boost=True)
        except Exception as e:
            print("pivot", piv, "select err:", e)
            continue
        for c in res.get("top", []):
            rec = {
                "symbol": str(c.get("symbol")).zfill(6), "T_date": int(c.get("T_date")),
                "category": c.get("category"), "expected_lift": c.get("expected_lift"),
                "verdict_ref": "PROMOTE_GRAIL", "emitted_at": "SIM", "status": "EMITTED",
                "pivot": piv,
            }
            lab = label_record(rec, max_lookforward=3)
            if lab.get("label_status") == "LABELED":
                recs.append(lab)
        print(f"pivot {piv}: candidates={len(res.get('top', []))} cumulative_labeled={len(recs)}", flush=True)

    os.makedirs(os.path.dirname(AGG_PATH), exist_ok=True)
    with open(AGG_PATH, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    base = compute_baseline()
    ev = evaluate(path=AGG_PATH, force_baseline=False)
    print("\n=== AGGREGATED EVALUATE (n=%d) ===" % len(recs))
    print(json.dumps(ev, ensure_ascii=False, indent=2, default=str))
    print("\nbaseline:", json.dumps({k: round(v, 6) if isinstance(v, float) else v
                                    for k, v in base.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
