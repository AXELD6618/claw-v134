#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · Layer D 实时触发链 (watch / diff 引擎)
==================================================================
职责:
  1. 读取上一快照 spring_soe_layerD_catalysts.json (由 agent 经 TDX MCP 拉取 F10 落盘)
  2. 读取本轮 fresh 主题 spring_soe_layerD_fresh.json (agent 拉取后写入)
  3. 对每只做 催化剂主题 diff: 本轮命中 - 上轮命中 = 新搭引信
  4. 回写更新缓存 (themes / fetched_date 刷新, risk_flags 保留)
  5. 若有新引信 -> 写 spring_soe_layerD_triggers_<YYYYMMDD>.json + report.txt
     (即便后续推送失败, 触发数据也已落盘, 可补推)

本地 Python 无法直连 TDX MCP, 故 fresh 由 agent 抓取后写入; 本脚本只做 diff + 落盘。

用法 (由自动化 agent 调用):
  python research/spring_soe_layerD_watch.py --fresh spring_soe_layerD_fresh.json
"""
import json, os, sys, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

OUT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"
CACHE = os.path.join(OUT, "spring_soe_layerD_catalysts.json")
FRESH = os.path.join(OUT, "spring_soe_layerD_fresh.json")

# 与 spring_soe_screen_full.py 保持一致的催化剂主题词表
CATALYST_THEMES = ["国企改革", "广东国资改革", "上海国资改革", "北京国资改革",
                   "浙江国资改革", "深圳国资改革", "活跃小盘国企", "双百企业",
                   "并购重组预案", "定增预案", "整体上市", "资产重组", "资产注入",
                   "停牌", "筹划重大事项", "借壳", "混改", "国有資本", "注入", "并购"]

def catalyst_of(themes):
    return [t for t in (themes or []) if t in CATALYST_THEMES]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", default=FRESH)
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    today = datetime.date.today().strftime("%Y-%m-%d")
    today_compact = datetime.date.today().strftime("%Y%m%d")

    # ---- 载入上一快照 ----
    prev = {}
    if os.path.exists(args.cache):
        try:
            for r in json.load(open(args.cache, encoding="utf-8")):
                prev[r["code"]] = r
        except Exception:
            prev = {}

    # ---- 载入本轮 fresh ----
    if not os.path.exists(args.fresh):
        print("[watch] ERROR: fresh 文件不存在: %s" % args.fresh)
        sys.exit(2)
    fresh = json.load(open(args.fresh, encoding="utf-8"))

    triggers = []
    seen = set()
    for rec in fresh:
        code = rec["code"]; sec = rec.get("sec_name", code)
        themes = rec.get("themes", [])
        cat_now = catalyst_of(themes)
        p = prev.get(code)
        cat_prev = catalyst_of(p.get("themes")) if p else []
        newly = [t for t in cat_now if t not in cat_prev]
        # 回写缓存: themes 刷新, risk_flags 保留旧值(除 fresh 自带)
        updated = {
            "code": code, "sec_name": sec, "fetched_date": today,
            "source": "tdx_api_data zxts/gsgy",
            "themes": themes,
            "risk_flags": rec.get("risk_flags") or (p.get("risk_flags") if p else []),
        }
        prev[code] = updated
        seen.add(code)
        # 仅当该 code 此前已有基线(上一快照中存在)才记录触发；
        # 首次播种(bootstrap, 缓存为空)或宇宙后续扩容的新进 code，
        # p is None -> 视为基线写入，不记触发，避免把初始主题当成新引信污染历史。
        if newly and p is not None:
            triggers.append({
                "code": code, "sec_name": sec,
                "new_catalyst": newly,
                "all_catalyst_now": cat_now,
                "prev_catalyst": cat_prev,
            })

    # ---- 回写缓存 ----
    cache_list = [prev[c] for c in prev]  # 保留所有历史(含未被本轮覆盖的)
    json.dump(cache_list, open(args.cache, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- 报告 ----
    n_total = len(fresh)
    n_trigger = len(triggers)
    lines = []
    lines.append("【弹簧压到底 · Layer D 引信监控】%s" % today)
    lines.append("扫描 %d 只 / 新搭引信 %d 只" % (n_total, n_trigger))
    lines.append("-" * 40)
    if triggers:
        for t in triggers:
            lines.append("★ %s %s" % (t["code"], t["sec_name"]))
            lines.append("  新引信: %s" % "、".join(t["new_catalyst"]))
            lines.append("  当前全部引信: %s" % (",".join(t["all_catalyst_now"]) or "无"))
    else:
        lines.append("本轮无新引信, 弹簧池保持观察。")
    lines.append("=" * 40)
    report = "\n".join(lines)

    HISTORY = os.path.join(args.out, "spring_soe_trigger_history.jsonl")
    if triggers:
        trig_path = os.path.join(args.out, "spring_soe_layerD_triggers_%s.json" % today_compact)
        json.dump(triggers, open(trig_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[watch] TRIGGERS=%d -> %s" % (n_trigger, trig_path))
        # ---- 累积进历史 jsonl（供 research.py review 回填统计），按 (code,date,引信) 去重 ----
        seen_hist = set()
        if os.path.exists(HISTORY):
            for l in open(HISTORY, encoding="utf-8"):
                l = l.strip()
                if not l:
                    continue
                try:
                    h = json.loads(l)
                    seen_hist.add((h["code"], h.get("trigger_date"), tuple(h.get("new_catalyst", []))))
                except Exception:
                    pass
        appended = 0
        with open(HISTORY, "a", encoding="utf-8") as hf:
            for t in triggers:
                key = (t["code"], today, tuple(t["new_catalyst"]))
                if key in seen_hist:
                    continue
                hf.write(json.dumps({
                    "code": t["code"],
                    "sec_name": t.get("sec_name", ""),
                    "trigger_date": today,
                    "new_catalyst": t["new_catalyst"],
                    "all_catalyst_now": t.get("all_catalyst_now", []),
                    "prev_catalyst": t.get("prev_catalyst", []),
                }, ensure_ascii=False) + "\n")
                seen_hist.add(key)
                appended += 1
        if appended:
            print("[watch] HISTORY +%d -> %s" % (appended, HISTORY))
    else:
        print("[watch] NO NEW TRIGGERS (%d scanned)" % n_total)

    rp = os.path.join(args.out, "spring_soe_layerD_report.txt")
    open(rp, "w", encoding="utf-8").write(report)
    print(report)

    # ---- 原子推送（触发时经对话框主通道，不依赖 agent 步骤解析）----
    # 以往推送依赖自动化代理解析 stdout 再调用 push 脚本，易漏推；
    # 现改为 watch 自身在发现引信时直接推送，确保必到对话框。
    if triggers:
        try:
            _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _root not in sys.path:
                sys.path.insert(0, _root)
            import push_channel as _pc
            _ok, _ch, _det, _nt = _pc.send(
                "【弹簧压到底 · Layer D 引信监控】%s" % today,
                report, "弹簧压到底·LayerD引信监控")
            print("[watch] AUTO-PUSH ok=%s channel=%s tries=%d" % (_ok, _ch, _nt))
        except Exception as _e:
            print("[watch] AUTO-PUSH FAIL: %s" % _e)

    # 退出码: 有触发=0(但标记在 stdout), 无触发=0; 仅文件缺失=2
    sys.exit(0)

if __name__ == "__main__":
    main()
