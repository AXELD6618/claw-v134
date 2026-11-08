#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_tail_raw_from_screener.py — 把 TDX screener(MCP) 返回的 JSON 转成 tail_raw/{date}.json。

背景：T4 screener 在 CI 中不稳定，导致 tail_raw/{date}.json 缺失 → V14/HOLD5_GRAIL 无上游。
本脚本只做「格式转换」（确定性），取数仍由 agent 经 TDX MCP tdx_screener 完成，二者解耦；
这样即便 T4 CI 挂了，agent 会话内也能一键补齐上游。

输入 screener JSON（tdx_screener 返回的完整 JSON，含 data 列表）：
  {"meta":{...}, "headers":[...], "data":[{POS,market,sec_code,sec_name,now_price,chg,...}]}
兼容：{"data":[...]} 或被直接包裹为 list。

输出 tail_raw 结构（与历史一致）：
  {"date","query","source","snapshot_time","data":[{sec_code,sec_name,market,now_price,chg,涨停区间次数,选股名称,...}]}
缺失字段(open/high/low/振幅/成交量/换手率/主力净额等)留空，tail_pick.py 会按需抓腾讯实时补齐。

用法：
  .venv/Scripts/python.exe research/build_tail_raw_from_screener.py --input <screener.json> --date 2026-10-27
"""
import os, sys, json, argparse, datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(HERE, "tail_raw")

_PASS_KEYS = ("open", "high", "low", "prev_close", "close",
              "振幅", "成交量", "vol", "换手率", "主力净额", "live")


def _num(x):
    try:
        return float(x)
    except Exception:
        return x


def build(screener, date):
    if isinstance(screener, dict):
        data = screener.get("data")
        query = (screener.get("meta") or {}).get("query")
    elif isinstance(screener, list):
        data, query = screener, None
    else:
        data, query = [], None
    if not isinstance(data, list):
        data = []

    rows = []
    for r in data:
        if not isinstance(r, dict):
            continue
        code = str(r.get("sec_code") or r.get("code") or "").strip()
        if not code:
            continue
        row = {
            "sec_code": code,
            "sec_name": r.get("sec_name") or r.get("name") or "",
            "market": str(r.get("market") or ""),
            "now_price": _num(r.get("now_price")),
            "chg": _num(r.get("chg")),
            "涨停区间次数": r.get("涨停区间次数") or r.get("涨停次数"),
            "选股名称": r.get("选股名称"),
        }
        for k in _PASS_KEYS:
            if k in r and r[k] not in (None, ""):
                row[k] = r[k]
        rows.append(row)

    return {
        "date": date,
        "query": query,
        "source": "tdx_screener(MCP)->build_tail_raw_from_screener",
        "snapshot_time": datetime.datetime.now().isoformat(timespec="seconds"),
        "data": rows,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="tdx_screener 返回的 JSON 文件")
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    screener = json.load(open(a.input, encoding="utf-8"))
    out = build(screener, a.date)
    outp = a.out or os.path.join(OUTDIR, "%s.json" % a.date)
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    json.dump(out, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[build_tail_raw] %s → %s · 候选 %d 只 · query=%r"
          % (a.input, outp, len(out["data"]), out["query"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
