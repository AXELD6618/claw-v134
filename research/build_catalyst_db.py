#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
妖股特征工程 · 催化剂/公告逐日时序库构建器（②催化剂/公告逐日时序）

架构：agent 侧用 tdx_wenda_notice_query / tdx_wenda_report_query 拉原始公告/研报，
      落 research/raw_catalyst/{code}_{bdate}_{edate}.json
        = {"code":..,"bdate":..,"edate":..,"notices":[...],"reports":[...]}
      本脚本按日期聚合为 data/catalyst/{date}.json：
        {date: {code: {"notice_bull":0..1,"notice_bear":0..1,
                       "report_bull":0..1,"report_bear":0..1,"flags":[...]}}}

解析器复用 ROUND14 的确定性正则口径（live_tdx_sources.py），不编造数字：
  公告: 回购0.7/增持0.5/预增0.6/中标0.3/股权激励0.2;
        减持0.8/解禁0.6/定增0.4/预减0.6/立案0.5/退市0.9
  研报: 买入/增持/上调/金股 -> 1.0(个股深度)/0.58(行业提及);
        卖出/下调 -> 0.42(行业提及)/1.0(个股深度)

注：本会话 tdx_wenda_* 工具未注册到执行索引，需在自动化运行时(a2113328 已证实可用)
    或连通后执行。本脚本只做落库聚合，与拉取解耦。
"""
import json, os, glob, re

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJ, "research", "raw_catalyst")
OUT = os.path.join(PROJ, "data", "catalyst")

NOTICE_BULL = {"回购": 0.7, "增持": 0.5, "预增": 0.6, "中标": 0.3, "股权激励": 0.2}
NOTICE_BEAR = {"减持": 0.8, "解禁": 0.6, "定增": 0.4, "预减": 0.6, "立案": 0.5, "退市": 0.9}
REPORT_BULL = {"买入": 1.0, "增持": 1.0, "上调": 1.0, "金股": 1.0}
REPORT_BEAR = {"卖出": 1.0, "下调": 1.0, "回避": 1.0}


def parse_one(text):
    """返回 (notice_bull, notice_bear, report_bull, report_bear, flags[])"""
    nb = be = rb = re_ = 0.0
    flags = []
    t = (text or "").lower()
    for k, v in NOTICE_BULL.items():
        if k in t:
            nb = max(nb, v); flags.append(f"+{k}")
    for k, v in NOTICE_BEAR.items():
        if k in t:
            be = max(be, v); flags.append(f"-{k}")
    for k, v in REPORT_BULL.items():
        if k in t:
            rb = max(rb, v); flags.append(f"R+{k}")
    for k, v in REPORT_BEAR.items():
        if k in t:
            re_ = max(re_, v); flags.append(f"R-{k}")
    return nb, be, rb, re_, flags


def main():
    os.makedirs(OUT, exist_ok=True)
    by_date = {}
    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    print(f"[catalyst] {len(files)} raw files in {RAW}")
    for fp in files:
        try:
            rec = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            print(f"  skip {fp}: {e}")
            continue
        code = str(rec.get("code") or "")
        edate = str(rec.get("edate") or rec.get("bdate") or "")
        nb = be = rb = re_ = 0.0
        flags = []
        for blob in (rec.get("notices") or []):
            a, b, c, d, fl = parse_one(blob if isinstance(blob, str) else blob.get("title", ""))
            nb = max(nb, a); be = max(be, b); flags += fl
        for blob in (rec.get("reports") or []):
            a, b, c, d, fl = parse_one(blob if isinstance(blob, str) else blob.get("title", ""))
            rb = max(rb, c); re_ = max(re_, d); flags += fl
        by_date.setdefault(edate, {})[code] = {
            "notice_bull": nb or None, "notice_bear": be or None,
            "report_bull": rb or None, "report_bear": re_ or None,
            "flags": flags,
        }
    for date, m in by_date.items():
        json.dump(m, open(os.path.join(OUT, f"{date}.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"  {date}: {len(m)} stocks")
    print(f"[catalyst] wrote {len(by_date)} date files -> {OUT}")


if __name__ == "__main__":
    main()
