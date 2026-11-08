# -*- coding: utf-8 -*-
"""
龙虎榜 / 机构席位 / 游资营业部 情报采集器（Round33 情报层 P0）

数据源：东财 datacenter-web（实测可通，历史 13.4 万页）
  1. RPT_DAILYBILLBOARD_DETAILSNEW   龙虎榜个股明细（含净买额、上榜原因）
  2. RPT_ORGANIZATION_TRADE_DETAILS  机构专用席位（机构买/卖/净额、买卖次数）
  3. RPT_BILLBOARD_DAILYDETAILSBUY   营业部买入明细（游资席位名 + 席位3日上涨概率）
  4. RPT_BILLBOARD_DAILYDETAILSSELL  营业部卖出明细

铁律：
  - ONLY 落盘 T 日收盘后可知的字段。D1/D2/D5/D10_CLOSE_ADJCHRATE 等未来涨幅
    **不进特征表**，仅单独存一份供席位画像回溯验证用（_fwd 后缀文件）。
  - 采集失败/空数据必须显式记录，不得静默跳过（空数据红线）。
  - 幂等：按日期落盘，已存在且行数>0 则跳过（--force 覆盖）。

用法：
  python research/intel_lhb.py --sync 2026-09-15 2026-10-16
  python research/intel_lhb.py --features 2026-10-16
  python research/intel_lhb.py --status
"""
import argparse
import gzip
import io
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

import pandas as pd

# === REJECTED TRACK GATE =================================================
# 龙虎榜/机构席位因子已证伪（backtest_cache/intel/intel_edge_test.json：
# 2134 交易日, AUC≈0.50, T+1≈−0.8%, PF≈0.70）。此采集器为已废弃 track，
# 此前持续向东财拉取 ~13.4 万页 LHB 数据，属纯浪费运行时与外部带宽。
# 默认关闭；需审计历史数据时置 RUN_REJECTED_INTEL=True。
RUN_REJECTED_INTEL = False
# =========================================================================

BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
OUT = os.path.join(PROJ, "backtest_cache", "intel")
MANIFEST = os.path.join(OUT, "_manifest.json")

# 表定义：(键, reportName, 排序字段, 是否未来信息列)
TABLES = {
    "board": ("RPT_DAILYBILLBOARD_DETAILSNEW", "TRADE_DATE", False),
    "inst": ("RPT_ORGANIZATION_TRADE_DETAILS", "TRADE_DATE", False),
    "seatbuy": ("RPT_BILLBOARD_DAILYDETAILSBUY", "TRADE_DATE", True),
    "seat_sell": ("RPT_BILLBOARD_DAILYDETAILSSELL", "TRADE_DATE", True),
}
FWD_COLS = [c for c in [
    "D1_CLOSE_ADJCHRATE", "D2_CLOSE_ADJCHRATE", "D3_CLOSE_ADJCHRATE",
    "D5_CLOSE_ADJCHRATE", "D10_CLOSE_ADJCHRATE", "D20_CLOSE_ADJCHRATE",
    "D30_CLOSE_ADJCHRATE", "RISE_PROBABILITY_3DAY"]]


def _get(url, timeout=20, retry=3):
    last = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://data.eastmoney.com/", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise last


def fetch_page(report, page, pz=500, day=None):
    q = {
        "reportName": report, "columns": "ALL", "pageNumber": str(page),
        "pageSize": str(pz), "source": "WEB", "client": "WEB",
        "sortColumns": "TRADE_DATE", "sortTypes": "-1",
    }
    if day:
        q["filter"] = f"(TRADE_DATE='{day}')"
    url = BASE + "?" + urllib.parse.urlencode(q, safe="()'=")
    raw = _get(url)
    j = json.loads(raw)
    res = j.get("result")
    if not res:
        return [], 0
    return res.get("data") or [], int(res.get("pages") or 0)


def fetch_day(key, day):
    """拉某张表某一天的全部行。"""
    report = TABLES[key][0]
    rows, page = [], 1
    while True:
        data, pages = fetch_page(report, page, pz=500, day=day)
        if not data:
            break
        rows.extend(data)
        if page >= pages or page >= 20:
            break
        page += 1
        time.sleep(0.12)
    return rows


def _norm(df):
    df = df.copy()
    for c in df.columns:
        if c.endswith("_DATE") or c in ("TRADE_DATE",):
            df[c] = df[c].astype(str).str.slice(0, 10)
    if "SECURITY_CODE" in df.columns:
        df["code"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    return df


def save_day(key, day, rows):
    os.makedirs(OUT, exist_ok=True)
    df = _norm(pd.DataFrame(rows))
    live = df.drop(columns=[c for c in FWD_COLS if c in df.columns], errors="ignore")
    fwd = df[[c for c in (["code", "SECURITY_CODE", "TRADE_DATE", "OPERATEDEPT_NAME"]
                          + FWD_COLS) if c in df.columns]].copy()
    d = os.path.join(OUT, key)
    os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, f"{day}.csv.gz"), "wt", encoding="utf-8") as f:
        live.to_csv(f, index=False)
    fd = os.path.join(OUT, "_fwd")
    os.makedirs(fd, exist_ok=True)
    with gzip.open(os.path.join(fd, f"{key}_{day}.csv.gz"), "wt", encoding="utf-8") as f:
        fwd.to_csv(f, index=False)
    return len(live), len(fwd)


def load_manifest():
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_manifest(m):
    os.makedirs(OUT, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, MANIFEST)


def sync(start, end, keys=None, force=False):
    keys = keys or list(TABLES)
    m = load_manifest()
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    days = []
    x = d0
    while x <= d1:
        if x.weekday() < 5:
            days.append(x.isoformat())
        x += timedelta(days=1)
    print(f"[sync] {len(days)} 交易日 × {len(keys)} 表 = {len(days)*len(keys)} 次拉取", flush=True)
    tot = 0
    done = 0
    for day in days:
        for key in keys:
            fp = os.path.join(OUT, key, f"{day}.csv.gz")
            marker = fp + ".ok"          # 幂等标记：成功拉取过（含 0 行）才写
            if not force and os.path.exists(marker) and os.path.exists(fp):
                continue
            try:
                rows = fetch_day(key, day)
                n, nf = save_day(key, day, rows)
                with open(marker, "w") as f:
                    f.write(str(n))
                m.setdefault(key, {})[day] = {"rows": n, "fwd_rows": nf,
                                              "at": datetime.now().isoformat(timespec="seconds"),
                                              "ok": n > 0}
                tot += n
                done += 1
                print(f"  {day} {key:10s} {n:5d} 行{'  (空)' if n == 0 else ''}", flush=True)
            except Exception as e:
                m.setdefault(key, {})[day] = {
                    "rows": 0, "ok": False, "err": f"{type(e).__name__}:{str(e)[:80]}",
                    "at": datetime.now().isoformat(timespec="seconds")}
                print(f"  {day} {key:10s} FAIL {type(e).__name__}: {str(e)[:60]}", flush=True)
            time.sleep(0.15)
            # 每 20 次落盘一次，中断不丢进度
            if done and done % 20 == 0:
                save_manifest(m)
    save_manifest(m)
    print(f"[sync] 完成，新增 {tot:,} 行 → {OUT}")


def load_all(key, days=None):
    d = os.path.join(OUT, key)
    if not os.path.isdir(d):
        return pd.DataFrame()
    fs = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".csv.gz"))
    if days:
        fs = [f for f in fs if os.path.basename(f)[:10] in days]
    parts = []
    for f in fs:
        try:
            parts.append(pd.read_csv(f, compression="gzip", dtype={"code": str}))
        except Exception:
            pass
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def status():
    m = load_manifest()
    print(f"{'表':10s} {'天数':>5s} {'总行数':>10s} {'空日':>5s} {'失败':>5s}  最近日期")
    for key in TABLES:
        rec = m.get(key, {})
        if not rec:
            print(f"{key:10s}      0          0     0     0  —")
            continue
        days = sorted(rec)
        rows = sum(v.get("rows", 0) for v in rec.values())
        empty = sum(1 for v in rec.values() if v.get("ok") and v.get("rows", 0) == 0)
        fail = sum(1 for v in rec.values() if not v.get("ok"))
        print(f"{key:10s} {len(days):5d} {rows:10,d} {empty:5d} {fail:5d}  {days[-1]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", nargs=2, metavar=("START", "END"))
    ap.add_argument("--keys", default="board,inst,seatbuy,seat_sell")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
    elif a.sync:
        if not RUN_REJECTED_INTEL:
            print("[SKIP] intel_lhb 龙虎榜/机构席位 采集已 REJECT（见 MEMORY.md §3 / "
                  "intel_edge_test.json）。置 RUN_REJECTED_INTEL=True 可临时重开用于审计。")
            raise SystemExit(0)
        sync(a.sync[0], a.sync[1], [k.strip() for k in a.keys.split(",") if k.strip()], a.force)
    else:
        ap.print_help()
