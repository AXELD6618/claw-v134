#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · 弹簧池周度刷新（A→C 自动化重算 Orchestrator）
==================================================================
目的：把原本一次性的 8 只固定观察名单，变成每周自动重算的动态池。
      这样"新进入弹簧态"的票会自动入池，Layer D 监控自动覆盖到它们。

流程：
  1. 重跑 Layer A（本地，5225 日线）→ spring_soe_layerA.json（安静压缩池，随行情变化）
  2. 重建 Layer B：
       - 若 agent 经 TDX 拉取了新鲜国资宇宙写入 spring_soe_layerB_raw.json → 用它；
       - 否则用已冻结的 spring_soe_layerB.json（国资宇宙变化慢，冻结可用）。
     与 Layer A 相交 → layerB.json + layerAB.json
  3. 重跑 Layer C（本地 cyq）→ layerC.json
  4. 跑编排器 → spring_soe_final.json（动态弹簧池）
  5. 与上周池 diff → 新入池 / 出池
  6. 经 push_channel 推送刷新报告

用法（由 Weekly 自动化调用）：
  .venv/Scripts/python.exe research/spring_soe_refresh.py [--raw backtest_cache/spring_soe_layerB_raw.json]
"""
import json, os, sys, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)  # Claw 根目录
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"

import spring_soe_screen as A
import spring_soe_layerC as C
import spring_soe_screen_full as F
import push_channel as pc


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def rebuild_B(raw_list=None):
    """重建 Layer B + A∩B。raw_list 为 agent 经 TDX 拉取的新鲜国资宇宙。"""
    la = _load(os.path.join(OUT, "spring_soe_layerA.json"))
    la_codes = {x["code"] for x in la}
    if raw_list:
        by_code = {}
        for r in raw_list:
            c = str(r.get("code"))
            by_code[c] = {
                "code": c,
                "market": str(r.get("market", "")),
                "sec_name": r.get("sec_name", ""),
                "controller_type": r.get("controller_type", ""),
                "controller": r.get("controller", ""),
                "hold_rel": r.get("hold_rel"),
                "mktcap": int(r["mktcap"]) if isinstance(r.get("mktcap"), (int, float)) else None,
                "now_price": float(r["now_price"]) if r.get("now_price") not in (None, "") else None,
                "chg": float(r["chg"]) if r.get("chg") not in (None, "") else None,
            }
    else:
        by_code = {x["code"]: x for x in _load(os.path.join(OUT, "spring_soe_layerB.json"))}
    intersect = []
    for rec in by_code.values():
        if rec["code"] in la_codes:
            am = next((x for x in la if x["code"] == rec["code"]), None)
            if not am:
                continue
            m = dict(rec)
            m["layerA"] = {k: am[k] for k in
                           ("n", "close", "mean_c", "turnover", "vola", "amp", "drift", "hi", "lo", "near_high")
                           if k in am}
            intersect.append(m)
    intersect.sort(key=lambda x: x["layerA"]["turnover"])
    json.dump(list(by_code.values()), open(os.path.join(OUT, "spring_soe_layerB.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(intersect, open(os.path.join(OUT, "spring_soe_layerAB.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return len(by_code), len(intersect)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=os.path.join(OUT, "spring_soe_layerB_raw.json"),
                    help="agent 经 TDX 拉取的新鲜国资宇宙；不存在则用冻结宇宙")
    args = ap.parse_args()

    # Layer A (spring_soe_screen) 内部用相对路径 backtest_cache/daily，必须 cwd=Claw 根
    os.chdir(ROOT)
    print("[refresh] cwd -> %s" % os.getcwd(), flush=True)

    print("[refresh] 1/5 Layer A 重跑...", flush=True)
    A.main()

    raw = None
    if os.path.exists(args.raw):
        try:
            raw = _load(args.raw)
            print("[refresh] 使用新鲜国资宇宙 %d 只" % len(raw), flush=True)
        except Exception as e:
            print("[refresh] raw 解析失败，回退冻结宇宙: %s" % e, flush=True)
            raw = None
    else:
        print("[refresh] 无新鲜国资宇宙，用冻结宇宙", flush=True)

    print("[refresh] 2/5 重建 Layer B...", flush=True)
    nB, nAB = rebuild_B(raw)
    print("[refresh]   LayerB=%d 相交(中小盘安静国资)=%d" % (nB, nAB), flush=True)

    print("[refresh] 3/5 Layer C 重跑...", flush=True)
    C.main()

    prev = None
    prev_path = os.path.join(OUT, "spring_soe_final_prev.json")
    if os.path.exists(os.path.join(OUT, "spring_soe_final.json")):
        prev = _load(os.path.join(OUT, "spring_soe_final.json"))
        json.dump(prev, open(prev_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("[refresh] 4/5 编排器...", flush=True)
    F.main()

    print("[refresh] 5/5 diff + 推送...", flush=True)
    new = _load(os.path.join(OUT, "spring_soe_final.json"))
    new_codes = {x["code"]: x for x in new}
    prev_codes = {x["code"]: x for x in prev} if prev else {}
    entrants = [new_codes[c] for c in new_codes if c not in prev_codes]
    dropouts = [prev_codes[c] for c in prev_codes if c not in new_codes]
    stay = [c for c in new_codes if c in prev_codes]

    today = datetime.date.today().strftime("%Y-%m-%d")
    L = []
    L.append("【弹簧压到底 · 弹簧池周度刷新】%s" % today)
    L.append("弹簧池: %d 只（上周 %d）｜ 新入 %d ｜ 出池 %d ｜ 留存 %d" % (
        len(new), len(prev) if prev else 0, len(entrants), len(dropouts), len(stay)))
    L.append("-" * 40)
    if entrants:
        L.append("★ 新入池（自动纳入 Layer D 监控）:")
        for m in entrants:
            cat = ",".join(m.get("catalyst") or []) or "观察池"
            L.append("  %s %s  换手%.3f%% SCR分位%.2f %s" % (
                m["code"], m["sec_name"], m["layerA"]["turnover"], m["scr_pctl"], cat))
    if dropouts:
        L.append("· 出池:")
        for m in dropouts:
            L.append("  %s %s" % (m.get("code"), m.get("sec_name")))
    L.append("-" * 40)
    L.append("完整池(按弹簧紧度):")
    for m in new:
        cat = ",".join(m.get("catalyst") or []) or "观察池"
        L.append("  %s %s  SCR分位%.2f  %s" % (m["code"], m["sec_name"], m["scr_pctl"], cat))
    report = "\n".join(L)

    rp = os.path.join(OUT, "spring_soe_refresh_%s.txt" % today.replace("-", ""))
    open(rp, "w", encoding="utf-8").write(report)
    print(report)

    # 通道铁律：本任务为常规维护，刷新报告一律进对话框(DIALOG)，严禁主动推送微信/iLink。
    ok, channel, detail, n_try = pc.send("【弹簧池周度刷新 · %s】" % today, report, "弹簧压到底", wechat=False)
    print("[refresh] PUSH ok=%s channel=%s tries=%d" % (ok, channel, n_try))
    return 0


if __name__ == "__main__":
    sys.exit(main())
