#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日内分钟线 本地兜底拉取 (pytdx) —— 仅在 tdx_kline MCP 不可用时使用。

背景（2026-10-29）：tdx-connector 处于 auth-required，无人值守时无法授权，
当日 1 分钟 K 线拉不到，累积会空转。本脚本用同一 TDX 协议直连行情服务器，
产出与 MCP 结果**同构**的外层 JSON，落到 _pull/<code>.txt，
下游 p1_intraday_accum.py / _extract_tdx_raw.py / p1_intraday_io.py 无需改动。

差异声明（不得掩盖）：
- pytdx 的 1 分钟棒不提供 up/down（主动性买卖量），统一写 0
  -> 与既有 ofi_available=False 状态一致，不引入伪订单流。
- 字段单位沿用 pytdx 原始 vol/amount，不二次换算。

用法:
  python research/p1_intraday_pull_pytdx.py --date 20261029
"""
import os, sys, json, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "backtest_cache", "intraday", "_pull")

# (market, code, setcode)
TARGETS = [(1, "603278", 1), (1, "688787", 1), (0, "300839", 0)]


def fetch(market, code, want=240):
    from pytdx.hq import TdxHq_API
    from pytdx.config.hosts import hq_hosts
    api = TdxHq_API(heartbeat=True, auto_retry=True)
    last_err = None
    for h in hq_hosts:
        name, ip, port = h[0], h[1], h[2]
        try:
            if not api.connect(ip, port, time_out=4):
                continue
            bars = api.get_security_bars(8, market, code, 0, want)  # 8 = 1min
            api.disconnect()
            if bars:
                return list(bars), "%s(%s:%s)" % (name, ip, port)
        except Exception as e:
            last_err = "%s %s" % (name, e)
            try:
                api.disconnect()
            except Exception:
                pass
            continue
    raise RuntimeError("all hosts failed: %s" % last_err)


def to_obj(code, setcode, bars, src):
    rows = []
    for b in bars:
        dt = b.get("datetime") or ""
        # pytdx: 'YYYY-MM-DD HH:MM'
        try:
            d, hm = dt.split(" ")
            hh, mm = hm.split(":")[:2]
            sec = int(hh) * 3600 + int(mm) * 60
            data = d.replace("-", "")
        except Exception:
            continue
        rows.append({
            "Data": data,
            "Second": sec,
            "Open": float(b.get("open") or 0),
            "High": float(b.get("high") or 0),
            "Low": float(b.get("low") or 0),
            "Close": float(b.get("close") or 0),
            "Volume": float(b.get("vol") or 0),
            "Amount": float(b.get("amount") or 0),
            "up": 0,
            "down": 0,
        })
    hqdate = rows[-1]["Data"] if rows else "00000000"
    return {
        "Setcode": setcode, "Code": code, "Period": 7,
        "Rows": rows,
        "AttachInfo": {"HqDate": hqdate, "Source": "pytdx_1min", "Server": src},
        "_note": "pytdx local fallback, up/down=0 (no order flow)",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ok = 0
    for market, code, setcode in TARGETS:
        try:
            bars, src = fetch(market, code)
        except Exception as e:
            print("[FAIL] %s: %s" % (code, e))
            continue
        obj = to_obj(code, setcode, bars, src)
        # 只保留目标交易日的棒
        day_rows = [r for r in obj["Rows"] if r["Data"] == a.date]
        if not day_rows:
            print("[MISS] %s: no bars for %s (last=%s, n=%d)"
                  % (code, a.date, obj["Rows"][-1]["Data"] if obj["Rows"] else "-",
                     len(obj["Rows"])))
            continue
        obj["Rows"] = day_rows
        obj["AttachInfo"]["HqDate"] = a.date
        p = os.path.join(a.out, "%s.txt" % code)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        print("[OK] %s bars=%d last=%s:%02d:%02d src=%s -> %s"
              % (code, len(day_rows),
                 day_rows[-1]["Data"],
                 day_rows[-1]["Second"] // 3600, (day_rows[-1]["Second"] % 3600) // 60,
                 src, p))
        ok += 1
    print("[pull] ok=%d/%d" % (ok, len(TARGETS)))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
