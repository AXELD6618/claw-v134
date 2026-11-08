# -*- coding: utf-8 -*-
"""
机构持仓情报采集（Round35 · 季频基本面层）

对应投资者要的「机构持仓成本 / 标的组合 / 持有盈亏」三项。

表（东财 datacenter-web，实测可通）：
  holders   RPT_F10_EH_FREEHOLDERS      十大流通股东（季频，113 页/季，约 5.6 万行）
  holdernum RPT_HOLDERNUMLATEST         股东户数（筹码集中度）

时点铁律：季报公布滞后 —— END_DATE=Q 的数据，NOTICE_DATE 多在 Q+1 月。
  特征可用日 = NOTICE_DATE 的下一个交易日（lag=1），**绝不用 END_DATE 对齐**。
  无 NOTICE_DATE 时退化为 END_DATE + 45 自然日（保守）。

产物：
  backtest_cache/intel/holders/{END_DATE}.csv.gz
  backtest_cache/intel/holdernum/{END_DATE}.csv.gz

用法：
  python research/intel_holders.py --sync 2024-03-31 2026-09-30
  python research/intel_holders.py --status
"""
import argparse
import gzip
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import date, datetime

import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
OUT = os.path.join(PROJ, "backtest_cache", "intel")
MANIFEST = os.path.join(OUT, "_manifest_holders.json")
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 流通股东表只保留必要列（47 列里大部分无用，砍掉后单季 ~8MB → ~1.5MB）
KEEP_HOLDERS = ["SECURITY_CODE", "SECURITY_NAME_ABBR", "END_DATE", "NOTICE_DATE",
                "HOLDER_NAME", "HOLDER_TYPE", "HOLD_NUM", "HOLD_NUM_CHANGE", "CHANGE_RATIO",
                "FREE_HOLDNUM_RATIO", "HOLD_RATIO", "IS_HOLDORG", "HOLDER_RANK",
                "HOLDER_STATE", "SHARES_TYPE"]


def _flush(m):
    """原子落盘 manifest —— 必须显式 close，否则 Windows 下 os.replace 报 WinError 5。"""
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, MANIFEST)


def _get(url, timeout=30, retry=6):
    """页级重试：东财对高并发会返回 success=false/服务器繁忙，而非 HTTP 错误。"""
    last = None
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://data.eastmoney.com/", "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
                b = r.read()
            j = json.loads(b)
            if j.get("success") is False and "繁忙" in str(j.get("message", "")):
                raise RuntimeError("busy")
            return b
        except Exception as e:
            last = e
            time.sleep(2.0 * (i + 1))   # 2/4/6/8/10/12s 退避
    raise last


# ★ 东财 datacenter-web 单次查询的 offset 上限 = 50,000 行：
#   超过后 pageNumber 继续翻会返回「服务器繁忙」（不是真的繁忙，是越界）。
#   十大流通股东一季约 5.4 万行 → 必须分区。SECURITY_CODE 不支持 >= / like / in，
#   但 HOLDER_RANK 支持 in(...)，且排名 1~5 与 6~10 各自远低于 5 万行 → 按名次二分最稳。
#   ⚠ 危险：filter 里写 like '%.SH' 不报错但**被静默忽略**（返回全表 count），绝不可用。
RANK_SLICES = ["HOLDER_RANK in (1,2,3,4,5)", "HOLDER_RANK in (6,7,8,9,10)"]


def fetch_pages(report, datecol, day, pz=500, max_pages=400, keep=None, slices=None, extra=None):
    if slices:
        allrows = []
        tot_pages = 0
        for sl in slices:
            r, pg = fetch_pages(report, datecol, day, pz, max_pages, keep,
                                slices=None, extra=sl)
            allrows.extend(r)
            tot_pages += (pg or 0)
        return allrows, tot_pages
    rows, page, pages = [], 1, None
    while page <= max_pages:
        flt = f"({datecol}='{day}')" + (f"({extra})" if extra else "")
        q = {"reportName": report, "columns": ",".join(keep) if keep else "ALL",
             "pageNumber": str(page), "pageSize": str(pz), "source": "WEB", "client": "WEB",
             "filter": flt}
        url = BASE + "?" + urllib.parse.urlencode(q, safe="()'=,")
        j = json.loads(_get(url))
        res = j.get("result")
        if not res:
            if j.get("success") is False:
                raise RuntimeError(f"api fail: {str(j.get('message'))[:60]}")
            break
        data = res.get("data") or []
        if not data:
            break
        rows.extend(data)
        pages = int(res.get("pages") or 1)
        if page >= pages:
            break
        page += 1
        time.sleep(0.16)
    return rows, pages


def quarter_ends(start, end):
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    for y in range(d0.year, d1.year + 1):
        for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31)):
            x = date(y, m, dd)
            if d0 <= x <= d1:
                out.append(x.isoformat())
    return out


def _norm(df, datecols):
    for c in datecols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.slice(0, 10)
    if "SECURITY_CODE" in df.columns:
        df["code"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    return df


def collect(key, report, datecol, days, keep=None, force=False, workers=3):
    """days: 要抓的日期列表（季末或任意截面日）"""
    m = json.load(open(MANIFEST, encoding="utf-8")) if os.path.exists(MANIFEST) else {}
    todo = []
    for d in days:
        fp = os.path.join(OUT, key, f"{d}.csv.gz")
        if not force and os.path.exists(fp + ".ok") and os.path.exists(fp):
            continue
        todo.append(d)
    print(f"[{key}] 待抓 {len(todo)}/{len(days)} 个截面", flush=True)
    if not todo:
        return m

    from concurrent.futures import ThreadPoolExecutor, as_completed
    os.makedirs(os.path.join(OUT, key), exist_ok=True)

    def job(d):
        rows, pages = fetch_pages(report, datecol, d, keep=keep,
                                  slices=RANK_SLICES if key == "holders" else None)
        df = _norm(pd.DataFrame(rows), ["END_DATE", "NOTICE_DATE"])
        fp = os.path.join(OUT, key, f"{d}.csv.gz")
        with gzip.open(fp, "wt", encoding="utf-8") as f:
            df.to_csv(f, index=False)
        with open(fp + ".ok", "w") as f:
            f.write(str(len(df)))
        return d, len(df), pages

    done, nrow, fails = 0, 0, 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(job, d): d for d in todo}
        for fu in as_completed(futs):
            d = futs[fu]
            try:
                d2, n, pg = fu.result()
                m.setdefault(key, {})[d2] = {"rows": n, "pages": pg, "ok": n > 0,
                                             "at": datetime.now().isoformat(timespec="seconds")}
                nrow += n
                print(f"  {key} {d2} {n:7,d} 行 / {pg} 页", flush=True)
            except Exception as e:
                fails += 1
                m.setdefault(key, {})[d] = {"rows": 0, "ok": False,
                                             "err": f"{type(e).__name__}:{str(e)[:70]}",
                                             "at": datetime.now().isoformat(timespec="seconds")}
                print(f"  {key} {d} FAIL {type(e).__name__}:{str(e)[:70]}", flush=True)
            done += 1
            if done % 3 == 0:
                _flush(m)
    _flush(m)
    print(f"[{key}] 完成 {nrow:,} 行，失败 {fails}", flush=True)
    return m


def status():
    m = json.load(open(MANIFEST, encoding="utf-8")) if os.path.exists(MANIFEST) else {}
    for k, rec in m.items():
        ds = sorted(rec)
        tot = sum(v.get("rows", 0) for v in rec.values())
        bad = [d for d, v in rec.items() if not v.get("ok")]
        print(f"{k:10s} {len(ds):3d} 截面 {tot:9,d} 行  失败 {len(bad)}  {ds[0] if ds else '—'} ~ {ds[-1] if ds else '—'}")
    if not m:
        print("(空)")


# 股东户数环比因子已证伪（MEMORY.md §3: PF_t5=1.067, REJECT）；此 F10 采集器为已废弃 track，
# 默认关闭以节省运行时/外部拉取。审计时置 RUN_REJECTED_INTEL=True。
RUN_REJECTED_INTEL = False

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", nargs=2, metavar=("START", "END"))
    ap.add_argument("--keys", default="holders")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
    elif a.sync:
        if not RUN_REJECTED_INTEL:
            print("[SKIP] intel_holders 股东户数(F10) 采集已 REJECT（见 MEMORY.md §3）。"
                  "置 RUN_REJECTED_INTEL=True 可临时重开用于审计。")
            raise SystemExit(0)
        keys = [k.strip() for k in a.keys.split(",") if k.strip()]
        qe = quarter_ends(a.sync[0], a.sync[1])
        print(f"季末截面: {qe}")
        for k in keys:
            if k == "holders":
                collect("holders", "RPT_F10_EH_FREEHOLDERS", "END_DATE", qe,
                        keep=KEEP_HOLDERS, force=a.force)
            elif k == "holdernum":
                collect("holdernum", "RPT_HOLDERNUMLATEST", "END_DATE", qe, force=a.force)
            else:
                print(f"未知 key {k}")
    else:
        ap.print_help()
