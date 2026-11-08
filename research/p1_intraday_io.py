#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TDX 分钟K线 MCP 结果 -> CSV 缓存 (agent 驱动)。

流程: agent 调 tdx_kline(code,setcode,period="7"/"0",wantNum=N) 拿到 JSON
      -> 存为 backtest_cache/intraday/_raw_{code}.json
      -> 运行本脚本转换全部 _raw_*.json -> {code}.csv

列: datetime, open, high, low, close, vol, amount, buy_vol, sell_vol
- vol/amount: 直接用 Rows.Volume / Rows.Amount (文档: 已处理好, 禁止二次换算)
- buy_vol/up, sell_vol/down: 主动性买/卖量 = 内盘外盘 tick 代理, 订单流 imbalance 来源
"""
import os, sys, json, glob

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INTRADAY = os.path.join(ROOT, "backtest_cache", "intraday")


def convert(raw_path):
    obj = json.load(open(raw_path, encoding="utf-8"))
    rows = obj.get("Rows", [])
    out = []
    for r in rows:
        try:
            d = r["Data"]
            sec = int(r["Second"])
            hh, mm, ss = sec // 3600, (sec % 3600) // 60, sec % 60
            dt_ = "%s %02d:%02d:%02d" % (d, hh, mm, ss)
            out.append((dt_, float(r["Open"]), float(r["High"]), float(r["Low"]),
                        float(r["Close"]), float(r["Volume"]), float(r["Amount"]),
                        float(r.get("up") or 0), float(r.get("down") or 0)))
        except Exception:
            continue
    code = obj.get("Code")
    # 按交易日分文件, 支持多日累积 (HqDate 优先, 退化为首棒 Data)
    date = (obj.get("AttachInfo") or {}).get("HqDate") or rows[0].get("Data") or "00000000"
    csv_path = os.path.join(INTRADAY, "%s_%s.csv" % (code, date))
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("datetime,open,high,low,close,vol,amount,buy_vol,sell_vol\n")
        for o in out:
            f.write("%s,%.4f,%.4f,%.4f,%.4f,%.0f,%.0f,%.0f,%.0f\n" % o)
    print("[io] %s -> %s (%d bars)" % (raw_path, csv_path, len(out)))
    return csv_path


def main():
    for p in sorted(glob.glob(os.path.join(INTRADAY, "_raw_*.json"))):
        convert(p)


if __name__ == "__main__":
    main()
