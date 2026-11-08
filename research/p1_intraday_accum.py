#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日内微观结构 多日累积驱动 (供每日自动化调用)。

流程: 把当日 tdx_kline 的 MCP 原始 .txt 放到 --src 目录
  -> 抽取 JSON -> backtest_cache/intraday/_raw_{code}.json
  -> p1_intraday_io 转 {code}_{date}.csv (新日期自动累加)
  -> p1_intraday_model 跑严格 OOS (pool 全部日期, 时间前70%%/后30%%)
  -> 追加一行 backtest_cache/intraday/_accum.log (AUC / 是否排名信号 / 是否击败基线)
"""
import os, sys, json, glob, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _extract_tdx_raw as ex
import p1_intraday_io as io
import p1_intraday_model as md

SRC = sys.argv[2] if len(sys.argv) > 2 else os.path.join(io.INTRADAY, "_pull")


def main():
    # 1) 抽取当日 MCP 原始输出
    n_ext = 0
    for fp in sorted(glob.glob(os.path.join(SRC, "*.txt"))):
        if "render" in fp:
            continue
        txt = open(fp, encoding="utf-8", errors="replace").read()
        js = ex.extract_outer_json(txt)
        if not js:
            continue
        try:
            o = json.loads(js)
        except Exception:
            continue
        code = o.get("Code")
        if not code:
            continue
        with open(os.path.join(io.INTRADAY, "_raw_%s.json" % code), "w",
                  encoding="utf-8") as f:
            json.dump(o, f, ensure_ascii=False)
        n_ext += 1
    print("[accum] extracted %d raw files" % n_ext)
    # 2) 转换全部 raw -> 按日期 CSV (累积)
    io.main()
    # 3) 严格 OOS (H 默认 5)
    sys.argv = ["p1_intraday_model.py"]
    md.main()
    # 4) 追加日志
    res = json.load(open(os.path.join(io.ROOT, "reports",
                                      "intraday_oos_result.json"), encoding="utf-8"))
    dates = set()
    for c in glob.glob(os.path.join(io.INTRADAY, "*.csv")):
        b = os.path.basename(c).replace(".csv", "")
        if "_raw" in b:
            continue
        parts = b.split("_")
        if len(parts) >= 2 and parts[-1].isdigit():
            dates.add(parts[-1])
    logp = os.path.join(io.INTRADAY, "_accum.log")
    # 2026-10-26：日志同时记录 ENTER lift（进场上行命中/基线上涨率）与闸门净收益对比。
    # 为什么：AUC 单点噪声带太宽，"leverage 不过 1" 比 AUC 更早暴露问题；且必须可跨日追踪。
    lift = res.get("enter_lift")
    with open(logp, "a", encoding="utf-8") as f:
        f.write("%s | n_days=%d | n_pooled=%d | auc=%.3f | ci=%s | rank=%s | beat_base=%s"
                " | enter_n=%s | enter_hit=%s | base_pos=%s | enter_lift=%s"
                " | net_gate=%s | net_always=%s | %s | ofi=%s\n"
                % (datetime.date.today().isoformat(), len(dates), res["n_pooled"],
                   res["test_auc"], res["auc_ci95"], res["rank_signal"],
                   res["beats_baseline"],
                   res.get("enter_n"), res.get("enter_hit"), res.get("base_pos_rate"),
                   ("%.3f" % lift) if isinstance(lift, (int, float)) else "NA",
                   res.get("net_gate"), res.get("net_always"),
                   res.get("gate_verdict", "-"), res.get("ofi_available")))
    print("[accum] logged -> %s (n_days=%d)" % (logp, len(dates)))


if __name__ == "__main__":
    main()
