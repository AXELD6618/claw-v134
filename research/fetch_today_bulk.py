# -*- coding: utf-8 -*-
"""
fetch_today_bulk.py — 批量抓取全市场当日日线，供 refresh_daily.py --apply 消费

背景：TDX MCP 逐只调用 100 次上下文开销不可承受（约 250k tokens），
      改为 stdlib urllib 直连腾讯 ifzq（memory 记录的降级链第 2 档），
      并用已抓到的 TDX MCP 权威 bar 做抽样校验（--verify）。

用法:
  python research/fetch_today_bulk.py --codes data/_codes.txt --out data/fetched.json --date 2026-10-27
  python research/fetch_today_bulk.py --verify data/fetched_part1.json data/fetched.json
"""
from __future__ import annotations
import os, sys, json, time, argparse, urllib.request

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mk(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return "sz" + code


def fetch_one(code: str, days: int = 20):
    """返回 [[date,open,close,high,low,volume(手)], ...]，失败返回 None"""
    sym = mk(code)
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={sym},day,,,{days},qfq")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://gu.qq.com/",
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                js = json.loads(r.read().decode("utf-8", "ignore"))
            d = js.get("data", {}).get(sym, {})
            rows = d.get("qfqday") or d.get("day") or []
            return rows
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return None


def build(codes, target_date: str, out_path: str):
    tgt = target_date.replace("-", "")
    ok, fail, nodata = [], [], []
    fetched = {}
    for i, code in enumerate(codes, 1):
        rows = fetch_one(code)
        if rows is None:
            fail.append(code); continue
        hit = None
        for r in rows:
            if str(r[0]).replace("-", "") == tgt:
                hit = r; break
        if hit is None:
            nodata.append(code); continue
        # r = [date, open, close, high, low, volume]
        rec = {
            "Data": tgt,
            "Open": str(hit[1]), "Close": str(hit[2]),
            "High": str(hit[3]), "Low": str(hit[4]),
            "Volume": float(hit[5]),
            "RawVolume": float(hit[5]) * 100,
            "src": "ifzq",
        }
        fetched[code] = [rec]
        ok.append(code)
        if i % 20 == 0:
            print(f"  [{i}/{len(codes)}] ok={len(ok)} fail={len(fail)} nodata={len(nodata)}")
    json.dump(fetched, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[bulk] 成功 {len(ok)} / 失败 {len(fail)} / 当日无数据 {len(nodata)} -> {out_path}")
    if fail:  print("[bulk] FAIL:", fail)
    if nodata: print("[bulk] NODATA:", nodata)
    return fetched


def verify(mcp_path: str, bulk_path: str):
    """用 TDX MCP 权威 bar 校验批量源，逐字段比对 OHLC（容差 0.01）"""
    a = json.load(open(mcp_path, encoding="utf-8"))
    b = json.load(open(bulk_path, encoding="utf-8"))
    tot = match = 0
    diffs = []
    for code, rows in a.items():
        if code not in b:
            diffs.append((code, "bulk缺该票")); continue
        r1, r2 = rows[0], b[code][0]
        tot += 1
        bad = []
        for k in ("Open", "High", "Low", "Close"):
            v1, v2 = float(r1[k]), float(r2[k])
            if abs(v1 - v2) > 0.011:
                bad.append(f"{k}: MCP {v1} vs bulk {v2}")
        if bad:
            diffs.append((code, "; ".join(bad)))
        else:
            match += 1
    print(f"[verify] 抽检 {tot} 只，OHLC 一致 {match} 只，偏差 {len(diffs)} 只")
    for d in diffs:
        print("   DIFF", d)
    return match, tot, diffs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes")
    ap.add_argument("--out", default=os.path.join(PROJ, "data", "fetched.json"))
    ap.add_argument("--date", default="2026-10-27")
    ap.add_argument("--verify", nargs=2)
    a = ap.parse_args()
    if a.verify:
        verify(a.verify[0], a.verify[1])
    else:
        codes = [x.strip() for x in open(a.codes, encoding="utf-8").read().split() if x.strip()]
        print(f"[bulk] 待抓 {len(codes)} 只，目标日 {a.date}")
        build(codes, a.date, a.out)
