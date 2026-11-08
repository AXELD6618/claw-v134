# -*- coding: utf-8 -*-
"""
fetch_today_market.py — 全市场当日 bar 批量抓取（腾讯 qt.gtimg 多标的一次性报价）

为什么需要它：
  连板台账(gen_lianban_watchlist.py) 以「缓存最大日期」为 as_of，全市场扫描。
  若只有少数票刷到 T 日，其余停在 T-1，则 as_of=T 但实际只扫了那几只 -> 连板数严重低估。
  故必须全市场刷新。

数据源：qt.gtimg.cn 批量报价，60 只/请求 -> 5200 只约 87 次请求（远低于 ifzq 5000 次 WAF 阈值）
字段映射（以 ~ 分隔）: [1]名称 [2]代码 [3]现价 [4]昨收 [5]今开 [6]成交量(手) [33]最高 [34]最低
停牌(成交量=0)跳过，不写假 bar。

用法:
  python research/fetch_today_market.py --out data/fetched_market.json --date 2026-10-27
  python research/fetch_today_market.py --out data/fetched_market.json --auto   # 自动取缓存最大日期+1交易日
"""
from __future__ import annotations
import os, sys, json, time, glob, argparse, urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")


def sym_of(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def fetch_batch(syms):
    url = "http://qt.gtimg.cn/q=" + ",".join(syms)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://gu.qq.com/",
    })
    for a in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read().decode("gbk", "ignore")
        except Exception:
            time.sleep(0.4 * (a + 1))
    return ""


def parse(txt, target: str):
    """返回 {code: [rec]}, 以及统计"""
    out, zero_vol, bad = {}, [], 0
    for seg in txt.split(";"):
        seg = seg.strip()
        if not seg.startswith("v_"):
            continue
        try:
            payload = seg.split("=", 1)[1].strip('"')
            f = payload.split("~")
            if len(f) < 35:
                bad += 1; continue
            code = f[2]
            o, c, h, l = float(f[5]), float(f[3]), float(f[33]), float(f[34])
            v = float(f[36]) if len(f) > 36 and f[36] else float(f[6])
            if v <= 0 or c <= 0 or o <= 0:
                zero_vol.append(code); continue
            # 今开可能为 0(开盘即停/一字未开)，用现价兜底不合理 -> 直接跳过
            if h <= 0 or l <= 0:
                zero_vol.append(code); continue
            out[code] = [{
                "Data": target.replace("-", ""),
                "Open": str(o), "Close": str(c), "High": str(h), "Low": str(l),
                "Volume": v, "RawVolume": v * 100, "src": "qt_gtimg",
            }]
        except Exception:
            bad += 1
    return out, zero_vol, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(PROJ, "data", "fetched_market.json"))
    ap.add_argument("--date", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    codes = sorted(os.path.basename(p)[:6] for p in glob.glob(os.path.join(DAILY, "*.csv")))
    if a.limit:
        codes = codes[:a.limit]
    print(f"[market] 待刷 {len(codes)} 只, 目标日 {a.date}")
    fetched, all_zero, all_bad = {}, [], 0
    B = 60
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        txt = fetch_batch([sym_of(c) for c in chunk])
        got, zv, bad = parse(txt, a.date)
        fetched.update(got)
        all_zero += zv
        all_bad += bad
        if (i // B + 1) % 15 == 0:
            print(f"  [{i+len(chunk)}/{len(codes)}] 已取 {len(fetched)}")
        time.sleep(0.12)
    json.dump(fetched, open(a.out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"[market] 成功 {len(fetched)} / 停牌或无效 {len(all_zero)} / 解析失败 {all_bad} -> {a.out}")


if __name__ == "__main__":
    main()
