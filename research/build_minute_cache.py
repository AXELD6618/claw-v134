#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
妖股特征工程 · 分钟缓存构建器（③分钟数据扩面）

架构：agent 侧用 tdx_kline(period="0"=5分钟) 拉原始数据，落 research/raw_minute/{code}.json
      （即 tdx_kline 返回的完整 JSON，含顶层 Rows）；本脚本将其转换为
      v2 引擎 load_minute() 消费的 backtest_cache/minute_5m/{code}.json
      格式：[{day:"20261023 14:56:00", open, high, low, close, volume}, ...]

v2 日内特征对 day 字段的要求：
  - str(day).startswith(day_str)        -> 用日期前缀取当日
  - str(day)[-8:] 落在 "14:30:00"~"15:00:00" -> 尾盘时段
故 day 必须形如 "YYYYMMDD HH:MM:SS"。

复用：通达信 tdx_kline(period="0")。无需自建分钟源。
"""
import json, os, glob, sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJ, "research", "raw_minute")
OUT = os.path.join(PROJ, "backtest_cache", "minute_5m")


def conv_day(data, second):
    """tdx_kline 返回 Data='20261023' Second=53760(秒) -> '20261023 14:56:00'"""
    try:
        s = int(float(second))
    except Exception:
        return str(data)
    h = s // 3600
    m = (s % 3600) // 60
    return f"{data} {h:02d}:{m:02d}:00"


def convert(resp):
    rows = resp.get("Rows") if isinstance(resp, dict) else None
    if not rows:
        # 兼容包了一层的格式
        rows = (((resp.get("response", {}).get("transformed", {})
                  .get("tables", [{}]) or [{}])[0] or {}).get("rows")) if isinstance(resp, dict) else None
    if not rows:
        return []
    out = []
    for r in rows:
        out.append({
            "day": conv_day(r.get("Data"), r.get("Second")),
            "open": float(r.get("Open", 0) or 0),
            "high": float(r.get("High", 0) or 0),
            "low": float(r.get("Low", 0) or 0),
            "close": float(r.get("Close", 0) or 0),
            "volume": float(r.get("Volume", 0) or 0),
        })
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    print(f"[minute] {len(files)} raw files in {RAW}")
    n_ok = 0
    for fp in files:
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            resp = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            print(f"  skip {code}: {e}")
            continue
        bars = convert(resp)
        if not bars:
            print(f"  skip {code}: no bars")
            continue
        json.dump(bars, open(os.path.join(OUT, f"{code}.json"), "w", encoding="utf-8"))
        n_ok += 1
    print(f"[minute] wrote {n_ok}/{len(files)} -> {OUT}")


if __name__ == "__main__":
    main()
