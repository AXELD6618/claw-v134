# -*- coding: utf-8 -*-
"""
机构资金流向情报采集（Round34 · 慢变量层）

与 intel_lhb.py 的分工：
  intel_lhb  = 龙虎榜/席位  → 快变量（T+1 事件），已证伪不可进 buy_score
  intel_flow = 调研/增持/大宗/解禁/北向 → **慢变量**（数周~数月发酵），
               用途是「圈池 + 排雷」，不是 T+1 打分。

五张表（东财 datacenter-web，实测可通）：
  survey   RPT_ORG_SURVEY            机构调研（RECEIVE_OBJECT=调研机构）
  increase RPT_SHARE_HOLDER_INCREASE 股东增持（TRADE_AVERAGE_PRICE=增持均价→成本线）
  block    RPT_DATA_BLOCKTRADE       大宗交易（DEAL_PRICE/PREMIUM_RATIO/BUYER_NAME）
  lift     RPT_LIFT_STAGE            限售解禁（FREE_DATE/LIFT_MARKET_CAP→抛压）
  north    RPT_MUTUAL_HOLD_DET       北向持股个股（HOLD_SHARES_RATIO 变化）

时点铁律：公告类数据（调研/增持/解禁）均盘后或次日发布 → 特征一律 lag=1。
大宗交易当日盘后公布 → lag=1。北向持股 T+1 日公布 → lag=2（保守）。

用法：
  python research/intel_flow.py --sync 2024-07-01 2026-10-16
  python research/intel_flow.py --status
"""
import argparse
import gzip
import json
import os
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
OUT = os.path.join(PROJ, "backtest_cache", "intel")
MANIFEST = os.path.join(OUT, "_manifest_flow.json")
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

TABLES = {
    # key: (reportName, 日期筛选列, 每页行数上限)
    # survey  : RPT_ORG_SURVEY 限 pageSize<=50（>50 报「服务器繁忙」）→ 一 天 700+ 行需 16 页，弃用
    # survey2 : RPT_ORG_SURVEYNEW 支持 pageSize=500，一天仅 2 页 → **这才是日常同步该用的表**
    "survey2":  ("RPT_ORG_SURVEYNEW", "NOTICE_DATE", 500),
    "increase": ("RPT_SHARE_HOLDER_INCREASE", "NOTICE_DATE", 500),
    "block":    ("RPT_DATA_BLOCKTRADE", "TRADE_DATE", 500),
    "lift":     ("RPT_LIFT_STAGE", "FREE_DATE", 500),
}
# 北向持 A 股个股明细：RPT_MUTUAL_HOLD_DET / RPT_MUTUAL_STOCK_HOLDRANKS / RPT_MUTUAL_STOCK_NORTHSTA
# 实测**全部只返回港股**（SECUCODE=*.HK、MARKET_CODE=S，即港股通南向），2024 年及更早直接空。
# 结论：北向持 A 股个股披露已停（监管口径变化），标记 NO_PUBLIC_SOURCE，不猜不补。


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


def fetch_day(key, day, max_pages=40):
    report, datecol, pz = TABLES[key]
    rows, page = [], 1
    while page <= max_pages:
        q = {"reportName": report, "columns": "ALL", "pageNumber": str(page),
             "pageSize": str(pz), "source": "WEB", "client": "WEB",
             "filter": f"({datecol}='{day}')"}
        url = BASE + "?" + urllib.parse.urlencode(q, safe="()'=")
        j = json.loads(_get(url))
        res = j.get("result")
        if not res:
            break
        data = res.get("data") or []
        if not data:
            break
        rows.extend(data)
        if page >= int(res.get("pages") or 1):
            break
        page += 1
        time.sleep(0.12)
    return rows


def save_day(key, day, rows):
    d = os.path.join(OUT, key)
    os.makedirs(d, exist_ok=True)
    df = pd.DataFrame(rows)
    if len(df):
        for c in df.columns:
            if c.endswith("_DATE") or c in ("TRADE_DATE", "END_DATE"):
                df[c] = df[c].astype(str).str.slice(0, 10)
        if "SECURITY_CODE" in df.columns:
            df["code"] = df["SECURITY_CODE"].astype(str).str.zfill(6)
    fp = os.path.join(d, f"{day}.csv.gz")
    with gzip.open(fp, "wt", encoding="utf-8") as f:
        df.to_csv(f, index=False)
    return len(df), fp


def load_manifest():
    if os.path.exists(MANIFEST):
        try:
            return json.load(open(MANIFEST, encoding="utf-8"))
        except Exception:
            pass
    return {}


def sync(start, end, keys, force=False, workers=8):
    m = load_manifest()
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    days = []
    x = d0
    while x <= d1:
        if x.weekday() < 5:
            days.append(x.isoformat())
        x += timedelta(days=1)

    # 收集待抓任务（已完成的跳过）
    todo = []
    for day in days:
        for key in keys:
            fp = os.path.join(OUT, key, f"{day}.csv.gz")
            mk = fp + ".ok"
            if not force and os.path.exists(mk) and os.path.exists(fp):
                continue
            todo.append((key, day))
    print(f"[sync] 待抓 {len(todo)} 个 (表×日)，并发 {workers}", flush=True)
    if not todo:
        print("[sync] 全部已存在，跳过")
        return

    from concurrent.futures import ThreadPoolExecutor, as_completed
    tot, done, fails = 0, 0, 0

    def job(kd):
        key, day = kd
        rows = fetch_day(key, day)
        n, _ = save_day(key, day, rows)
        with open(os.path.join(OUT, key, f"{day}.csv.gz.ok"), "w") as f:
            f.write(str(n))
        return key, day, n

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(job, kd): kd for kd in todo}
        for fu in as_completed(futs):
            key, day = futs[fu]
            try:
                k2, d2, n = fu.result()
                m.setdefault(k2, {})[d2] = {"rows": n, "ok": n > 0,
                                            "at": datetime.now().isoformat(timespec="seconds")}
                tot += n
            except Exception as e:
                fails += 1
                m.setdefault(key, {})[day] = {"rows": 0, "ok": False,
                                              "err": f"{type(e).__name__}:{str(e)[:60]}",
                                              "at": datetime.now().isoformat(timespec="seconds")}
            done += 1
            if done % 40 == 0:
                print(f"  ...{done}/{len(todo)} 成功行 {tot:,} 失败 {fails}", flush=True)
                os.makedirs(OUT, exist_ok=True)
                with open(MANIFEST + ".tmp", "w", encoding="utf-8") as fm:
                    json.dump(m, fm, ensure_ascii=False, indent=1)
    os.makedirs(OUT, exist_ok=True)
    with open(MANIFEST + ".tmp", "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(MANIFEST + ".tmp", MANIFEST)
    print(f"[sync] 完成，新增 {tot:,} 行，失败 {fails} 个")


def status():
    m = load_manifest()
    print(f"{'表':10s} {'天':>5s} {'行数':>10s} {'失败':>5s}  最近")
    for k in TABLES:
        rec = m.get(k, {})
        if not rec:
            print(f"{k:10s}     0          0     0  —")
            continue
        ds = sorted(rec)
        print(f"{k:10s} {len(ds):5d} {sum(v.get('rows',0) for v in rec.values()):10,d} "
              f"{sum(1 for v in rec.values() if not v.get('ok')):5d}  {ds[-1]}")


# 龙虎榜/机构席位及慢变量情报因子均已在项目记忆(MEMORY.md §3)证伪/废弃；
# 此采集器为已废弃 track，默认关闭以节省运行时/外部拉取。审计时置 RUN_REJECTED_INTEL=True。
RUN_REJECTED_INTEL = False

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", nargs=2, metavar=("START", "END"))
    ap.add_argument("--keys", default="survey2,increase,block,lift")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        status()
    elif a.sync:
        if not RUN_REJECTED_INTEL:
            print("[SKIP] intel_flow 慢变量情报采集已 REJECT（见 MEMORY.md §3）。"
                  "置 RUN_REJECTED_INTEL=True 可临时重开用于审计。")
            raise SystemExit(0)
        sync(a.sync[0], a.sync[1], [k.strip() for k in a.keys.split(",") if k.strip()], a.force)
    else:
        ap.print_help()
