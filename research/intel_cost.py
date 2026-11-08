# -*- coding: utf-8 -*-
"""
持仓成本线 / 持有盈亏 情报（Round35 · 投资者直问的维度）

投资者原话：「机构、游资持仓成本……持有盈利状况/亏损……结算」。
这一项此前完全空白——系统知道谁买了，但不知道「他买在什么价位、现在赚还是亏」。

本脚本把两条真实成本线重建出来：
  1. 增持成本线：RPT_SHARE_HOLDER_INCREASE 的 TRADE_AVERAGE_PRICE（股东真金白银的成交均价）
  2. 大宗成本线：RPT_DATA_BLOCKTRADE 的 DEAL_PRICE（按 DEAL_AMT 加权）
     其中「机构买方」单列一条（BUYER_NAME 属机构专用/总部/分公司/自营/资管/基金/保险/信托）

产出「成本倒挂」特征（gap = 收盘 / 成本线 − 1）：
  gap < 0  → 该批资金浮亏（被套）
  gap > 0  → 浮盈
被套的机构/股东比浮盈的更有「做价动力」，这是可埋伏的逻辑，但**必须实证**。

时点铁律：
  增持 → 可用日 = NOTICE_DATE 的下一交易日
  大宗 → 可用日 = TRADE_DATE 的下一交易日（大宗盘后公布）
  成本线有效期 = 60 个交易日滚动窗口（超过 60 日视为已换手，不再算「这批资金的成本」）

用法：python research/intel_cost.py
产物：backtest_cache/intel/intel_cost_basis.csv.gz
"""
import glob
import gzip
import json
import os

import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT = os.path.join(INTEL, "intel_cost_basis.csv.gz")
WIN = 60  # 交易日

ORG_KW = ("机构专用", "总部", "分公司", "自营", "证券投资部", "资产管理",
          "基金", "保险", "信托", "证券股份有限公司", "投资有限公司")


def load_all(key, cols):
    fs = sorted(glob.glob(os.path.join(INTEL, key, "*.csv.gz")))
    parts = []
    for fp in fs:
        try:
            df = pd.read_csv(gzip.open(fp, "rt", encoding="utf-8"),
                             usecols=lambda c: c in cols, dtype={"code": str})
            if len(df):
                parts.append(df)
        except Exception:
            continue
    if not parts:
        return pd.DataFrame(columns=list(cols))
    return pd.concat(parts, ignore_index=True)


def is_org(name):
    s = str(name or "")
    if "营业部" in s:
        return False
    return any(k in s for k in ORG_KW)


def build_events():
    out = []

    inc = load_all("increase", {"code", "NOTICE_DATE", "TRADE_AVERAGE_PRICE",
                                "REAL_PRICE", "CHANGE_NUM", "DIRECTION", "SECURITY_NAME_ABBR"})
    if len(inc):
        inc["code"] = inc["code"].str.zfill(6)
        d = inc["DIRECTION"].astype(str) if "DIRECTION" in inc.columns else pd.Series([""] * len(inc))
        inc = inc[~d.str.contains("减持")].copy()
        px = pd.to_numeric(inc["TRADE_AVERAGE_PRICE"], errors="coerce")
        if "REAL_PRICE" in inc.columns:
            px = px.fillna(pd.to_numeric(inc["REAL_PRICE"], errors="coerce"))
        w = pd.to_numeric(inc["CHANGE_NUM"], errors="coerce").abs()
        inc = inc.assign(price=px, weight=w, src="inc",
                         avail_date=inc["NOTICE_DATE"].astype(str).str.slice(0, 10))
        inc = inc[(inc["price"] > 0) & (inc["weight"] > 0)]
        out.append(inc[["code", "avail_date", "price", "weight", "src"]])
        print("[inc] 增持事件 %s 条" % format(len(inc), ","))

    blk = load_all("block", {"code", "TRADE_DATE", "DEAL_PRICE", "DEAL_AMT", "BUYER_NAME"})
    if len(blk):
        blk["code"] = blk["code"].str.zfill(6)
        px = pd.to_numeric(blk["DEAL_PRICE"], errors="coerce")
        w = pd.to_numeric(blk["DEAL_AMT"], errors="coerce")
        blk = blk.assign(price=px, weight=w, src="blk",
                         avail_date=blk["TRADE_DATE"].astype(str).str.slice(0, 10))
        blk = blk[(blk["price"] > 0) & (blk["weight"] > 0)]
        morg = blk["BUYER_NAME"].map(is_org)
        org = blk[morg].copy()
        org["src"] = "org"
        out.append(blk[["code", "avail_date", "price", "weight", "src"]])
        out.append(org[["code", "avail_date", "price", "weight", "src"]])
        print("[blk] 大宗事件 %s 条，机构买方 %s 条" % (format(len(blk), ","), format(len(org), ",")))

    ev = pd.concat(out, ignore_index=True)
    ev["avail_date"] = ev["avail_date"].astype(str).str.slice(0, 10)
    ev = ev.dropna(subset=["avail_date"])
    ev = ev[ev["avail_date"] >= "2000-01-01"]
    return ev


def main():
    print("[1/4] 读事件 ...", flush=True)
    ev = build_events()
    codes = sorted(os.path.splitext(os.path.basename(f))[0]
                   for f in glob.glob(os.path.join(DAILY, "*.csv")))
    print("[2/4] 面板 %d 只" % len(codes), flush=True)

    rows = []
    bycode = {c: g for c, g in ev.groupby("code")}
    n_hit = 0
    for k, code in enumerate(codes):
        g = bycode.get(code)
        if g is None or g.empty:
            continue
        try:
            df = pd.read_csv(os.path.join(DAILY, "%s.csv" % code), usecols=["\u65e5\u671f", "\u6536\u76d8"]).dropna()
        except Exception:
            continue
        if len(df) < 80:
            continue
        d = df.iloc[:, 0].astype(str).str.slice(0, 10).values
        c = df.iloc[:, 1].values.astype(float)
        n = len(d)
        if c[-1] <= 0:
            continue
        rec = {}
        for src in ("inc", "blk", "org"):
            sub = g[g["src"] == src]
            if sub.empty:
                continue
            pos = np.searchsorted(d, sub["avail_date"].values, side="right")
            ok = (pos > 0) & (pos < n)
            if not ok.any():
                continue
            pa, wa, pq = pos[ok], sub["weight"].values[ok], sub["price"].values[ok]
            inc_pw = np.zeros(n)
            inc_w = np.zeros(n)
            np.add.at(inc_pw, pa, pq * wa)
            np.add.at(inc_w, pa, wa)
            cpw = np.concatenate([[0.0], np.cumsum(inc_pw)])
            cw = np.concatenate([[0.0], np.cumsum(inc_w)])
            idx = np.arange(n)
            lo = np.maximum(0, idx - WIN + 1)
            wpw = cpw[idx + 1] - cpw[lo]
            ww = cw[idx + 1] - cw[lo]
            valid = ww > 0
            cost = np.where(valid, wpw / np.maximum(ww, 1e-12), np.nan)
            rec[src] = (c / cost - 1.0, ww)
        if not rec:
            continue
        n_hit += 1
        out = pd.DataFrame({"code": code, "date": d})
        for src in ("inc", "blk", "org"):
            if src in rec:
                out["gap_%s" % src] = np.round(rec[src][0], 5)
                out["w60_%s" % src] = np.round(rec[src][1], 2)
            else:
                out["gap_%s" % src] = np.nan
                out["w60_%s" % src] = 0.0
        out = out[out[["gap_inc", "gap_blk", "gap_org"]].notna().any(axis=1)]
        if len(out):
            rows.append(out)
        if (k + 1) % 1500 == 0:
            print("  ...%d/%d 有成本线 %d" % (k + 1, len(codes), n_hit), flush=True)

    if not rows:
        print("[!] 无可用成本线")
        return 1
    res = pd.concat(rows, ignore_index=True)
    print("[3/4] 成本线 %s 行 / %s 只 / %d 日" % (format(len(res), ","),
          format(res["code"].nunique(), ","), res["date"].nunique()), flush=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        res.to_csv(f, index=False)
    print("[4/4] [OK] %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1e6))

    for col in ("gap_inc", "gap_blk", "gap_org"):
        s = res[col].dropna()
        if len(s) < 100:
            print("  %s: 样本 %d" % (col, len(s)))
            continue
        print("  %s: n=%s 中位 %+.2f%% 被套(<0) %.1f%% 深套(<-10%%) %.1f%%" % (
            col, format(len(s), ","), s.median() * 100, (s < 0).mean() * 100, (s < -0.10).mean() * 100))
    json.dump({"rows": int(len(res)), "codes": int(res["code"].nunique()),
               "dates": int(res["date"].nunique())},
              open(os.path.join(PROJ, "research", "intel_cost.json"), "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
