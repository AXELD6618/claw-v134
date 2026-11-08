# -*- coding: utf-8 -*-
"""
机构持仓 / 调研 事件特征（Round35）

把两张季频/日频情报表压成「事件表」，供 lead_engine 按票展开到后续交易日。

A. 十大流通股东（季频，intel_holders.py 采集）
   per (code, END_DATE) 聚合，再与上一季相减得到**环比变化**：
     n_org / org_ratio / top10_ratio / n_up / n_dn / net_chg_pct
     d_org_ratio / d_top10 / d_n_org            ← 真正的「机构在加仓还是在撤」
   可用日 = NOTICE_DATE（季报公告日）→ lead_engine 取下一交易日（lag=1）
   缺 NOTICE_DATE 时退化 END_DATE + 45 自然日（保守）

B. 机构调研（日频，RPT_ORG_SURVEYNEW；该表只保留滚动 1 年，2025-10-16 起）
   per (code, NOTICE_DATE)：n_inst（去重机构数）/ n_fund / n_broker / n_foreign
   可用日 = NOTICE_DATE

产物：
  backtest_cache/intel/intel_holder_events.csv.gz
  backtest_cache/intel/intel_survey_events.csv.gz

用法：python research/intel_holdfeat.py
"""
import glob
import gzip
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")


def _read(fp, **kw):
    try:
        if os.path.getsize(fp) < 100:
            return None
        return pd.read_csv(gzip.open(fp, "rt", encoding="utf-8"), dtype={"code": str}, **kw)
    except Exception:
        return None


def build_holders():
    fs = sorted(glob.glob(os.path.join(INTEL, "holders", "*.csv.gz")))
    print("[holders] 季度文件 %d" % len(fs), flush=True)
    snaps = []
    for fp in fs:
        q = os.path.basename(fp)[:10]
        df = _read(fp)
        if df is None or not len(df):
            continue
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["END_DATE"] = df["END_DATE"].astype(str).str.slice(0, 10)
        df["NOTICE_DATE"] = df["NOTICE_DATE"].astype(str).str.slice(0, 10)
        for c in ("HOLD_NUM", "FREE_HOLDNUM_RATIO", "HOLD_RATIO", "CHANGE_RATIO"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "HOLD_NUM_CHANGE" in df.columns:
            df["HNC"] = pd.to_numeric(df["HOLD_NUM_CHANGE"], errors="coerce").fillna(0.0)
        else:
            df["HNC"] = 0.0
        df["ISORG"] = (pd.to_numeric(df.get("IS_HOLDORG"), errors="coerce").fillna(0) > 0) \
            if "IS_HOLDORG" in df.columns else False
        g = df.groupby("code")
        s = pd.DataFrame({
            "end": g["END_DATE"].max(),
            "notice": g["NOTICE_DATE"].max(),
            "n_org": g["ISORG"].sum(),
            "org_ratio": g.apply(lambda x: x.loc[x["ISORG"], "FREE_HOLDNUM_RATIO"].sum(),
                                 include_groups=False),
            "top10_ratio": g["FREE_HOLDNUM_RATIO"].sum(),
            "n_up": g.apply(lambda x: int((x["CHANGE_RATIO"] > 0).sum()), include_groups=False),
            "n_dn": g.apply(lambda x: int((x["CHANGE_RATIO"] < 0).sum()), include_groups=False),
            "hold_sum": g["HOLD_NUM"].sum(),
            "chg_sum": g["HNC"].sum(),
        }).reset_index()
        s["quarter"] = q
        snaps.append(s)
        print("  %s  %6d 只  公告日样本 %s" % (q, len(s), s["notice"].iloc[0] if len(s) else "-"), flush=True)
    if not snaps:
        return pd.DataFrame()
    ev = pd.concat(snaps, ignore_index=True)
    ev = ev.sort_values(["code", "end"], kind="mergesort")
    g = ev.groupby("code")
    for c in ("org_ratio", "top10_ratio", "n_org"):
        ev["prev_" + c] = g[c].shift(1)
    ev["d_org_ratio"] = ev["org_ratio"] - ev["prev_org_ratio"]
    ev["d_top10"] = ev["top10_ratio"] - ev["prev_top10_ratio"]
    ev["d_n_org"] = ev["n_org"] - ev["prev_n_org"]
    ev["net_chg_pct"] = np.where(ev["hold_sum"] > 0, ev["chg_sum"] / ev["hold_sum"], np.nan)
    # 可用日：优先 NOTICE_DATE；缺失/异常则 END_DATE + 45 天
    nd = pd.to_datetime(ev["notice"], errors="coerce")
    ed = pd.to_datetime(ev["end"], errors="coerce")
    bad = nd.isna() | (nd > ed + timedelta(days=200))
    nd = nd.where(~bad, ed + timedelta(days=45))
    ev["avail_date"] = nd.dt.strftime("%Y-%m-%d")
    ev = ev[ev["avail_date"].notna()]
    keep = ["code", "end", "avail_date", "n_org", "org_ratio", "top10_ratio", "n_up", "n_dn",
            "net_chg_pct", "d_org_ratio", "d_top10", "d_n_org"]
    return ev[keep]


FUND_KW = ("基金", "资管", "资产管理", "投资管理", "私募", "资本管理")
BROKER_KW = ("证券", "证券营业部")
FOREIGN_KW = ("QFII", "Capital", "capital", "Asset", "ASSET", "Fund", "FUND",
              "Invest", "INVEST", "Limited", "LTD", "LLC", "Bank", "BANK", "Hong Kong")


def build_survey():
    fs = sorted(glob.glob(os.path.join(INTEL, "survey2", "*.csv.gz")))
    print("[survey] 日文件 %d" % len(fs), flush=True)
    parts = []
    for fp in fs:
        df = _read(fp)
        if df is None or not len(df):
            continue
        parts.append(df[["code", "NOTICE_DATE", "RECEIVE_OBJECT"]])
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["NOTICE_DATE"] = df["NOTICE_DATE"].astype(str).str.slice(0, 10)
    df["obj"] = df["RECEIVE_OBJECT"].astype(str)
    df = df.drop_duplicates(["code", "NOTICE_DATE", "obj"])
    df["is_fund"] = df["obj"].apply(lambda s: any(k in s for k in FUND_KW))
    df["is_broker"] = df["obj"].apply(lambda s: any(k in s for k in BROKER_KW))
    df["is_foreign"] = df["obj"].apply(lambda s: any(k in s for k in FOREIGN_KW))
    g = df.groupby(["code", "NOTICE_DATE"])
    out = pd.DataFrame({"n_inst": g["obj"].nunique(),
                        "n_fund": g["is_fund"].sum(),
                        "n_broker": g["is_broker"].sum(),
                        "n_foreign": g["is_foreign"].sum()}).reset_index()
    out = out.rename(columns={"NOTICE_DATE": "avail_date"})
    print("  事件 %s 条 / %s 只 / %d 日" % (format(len(out), ","),
          format(out["code"].nunique(), ","), out["avail_date"].nunique()), flush=True)
    return out


def main():
    h = build_holders()
    if len(h):
        fp = os.path.join(INTEL, "intel_holder_events.csv.gz")
        with gzip.open(fp, "wt", encoding="utf-8") as f:
            h.to_csv(f, index=False)
        print("[OK] %s  %s 行 / %s 只" % (fp, format(len(h), ","), format(h["code"].nunique(), ",")))
        for c in ("org_ratio", "d_org_ratio", "net_chg_pct"):
            s = pd.to_numeric(h[c], errors="coerce").dropna()
            if len(s):
                print("   %-12s 中位 %+.3f  正占比 %.1f%%" % (c, s.median(), (s > 0).mean() * 100))
    s = build_survey()
    if len(s):
        fp = os.path.join(INTEL, "intel_survey_events.csv.gz")
        with gzip.open(fp, "wt", encoding="utf-8") as f:
            s.to_csv(f, index=False)
        print("[OK] %s  %s 行" % (fp, format(len(s), ",")))
        q = s["n_inst"]
        print("   调研机构数 分位 p25 %.0f / p50 %.0f / p75 %.0f / p90 %.0f / max %.0f" % (
            q.quantile(.25), q.median(), q.quantile(.75), q.quantile(.90), q.max()))
    json.dump({"holder_rows": int(len(h)), "survey_rows": int(len(s))},
              open(os.path.join(PROJ, "research", "intel_holdfeat.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
