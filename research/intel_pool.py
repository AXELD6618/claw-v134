# -*- coding: utf-8 -*-
"""
慢变量情报池（Round34）：增持成本线 / 大宗折溢价 / 解禁抛压 → 圈池 + 排雷

与快变量（龙虎榜 T+1 事件）的区别：
  这三类是**提前可知或持续数周**的信息，用途是「圈定有人在收集筹码的池子」+「排掉即将被砸的池子」，
  然后在池内做 T+1 择时，而不是直接给 T+1 打分。

特征（全部 lag=1，公告类盘后发布，T 日尾盘只知 T-1）：
  inc_20d          近 20 交易日有股东增持公告
  inc_amt_20d      近 20 日增持金额合计（元）
  inc_cost_gap     增持均价 / T日收盘 − 1   ← 负值=现价低于增持成本（机构浮亏）
  block_5d         近 5 日大宗交易笔数
  block_prem_5d    大宗成交量加权溢价率（正=溢价抢筹，负=折价甩货）
  block_inst_buy   买方为"机构专用"的笔数
  lift_30d         未来 30 自然日内解禁市值 / 流通市值   ← 解禁日提前公告，不构成未来函数

用法：python research/intel_pool.py [--test]
"""
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT = os.path.join(INTEL, "intel_pool.csv.gz")
CAL_FILE = os.path.join(DAILY, "000001.csv")


def cal():
    df = pd.read_csv(CAL_FILE, usecols=["日期"])
    return sorted(df["日期"].astype(str).str.slice(0, 10).unique())


def load(key):
    d = os.path.join(INTEL, key)
    if not os.path.isdir(d):
        return pd.DataFrame()
    parts = []
    for f in sorted(os.listdir(d)):
        if not f.endswith(".csv.gz"):
            continue
        try:
            x = pd.read_csv(os.path.join(d, f), compression="gzip", dtype={"code": str})
            if len(x):
                parts.append(x)
        except Exception:
            pass
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def build():
    calendar = cal()
    nxt = {calendar[i]: calendar[i + 1] for i in range(len(calendar) - 1)}
    rows = []

    inc = load("increase")
    if not inc.empty:
        c = "NOTICE_DATE" if "NOTICE_DATE" in inc.columns else None
        if c:
            inc["d"] = inc[c].astype(str).str.slice(0, 10)
            inc["notional"] = pd.to_numeric(inc.get("CHANGE_NUM"), errors="coerce").abs() * \
                pd.to_numeric(inc.get("TRADE_AVERAGE_PRICE"), errors="coerce")
            inc["avg_px"] = pd.to_numeric(inc.get("TRADE_AVERAGE_PRICE"), errors="coerce")
            for (code, d), g in inc.groupby(["code", "d"]):
                rows.append({"code": code, "src": "inc", "d": d,
                             "notional": float(g["notional"].sum(skipna=True)),
                             "avg_px": float(g["avg_px"].mean()) if g["avg_px"].notna().any() else np.nan})
    blk = load("block")
    if not blk.empty:
        blk["d"] = blk["TRADE_DATE"].astype(str).str.slice(0, 10)
        blk["amt"] = pd.to_numeric(blk.get("DEAL_AMT"), errors="coerce")
        blk["prem"] = pd.to_numeric(blk.get("PREMIUM_RATIO"), errors="coerce")
        buyer = blk.get("BUYER_NAME", pd.Series([""] * len(blk))).astype(str)
        blk["inst_buy"] = buyer.str.contains("机构专用").astype(int)
        for (code, d), g in blk.groupby(["code", "d"]):
            w = g["amt"].fillna(1.0)
            rows.append({"code": code, "src": "blk", "d": d,
                         "notional": float(g["amt"].sum(skipna=True)),
                         "avg_px": float((g["prem"] * w).sum() / w.sum()) if g["prem"].notna().any() else np.nan,
                         "n": int(len(g)), "inst_buy": int(g["inst_buy"].sum())})
    lift = load("lift")
    if not lift.empty:
        lift["d"] = lift["FREE_DATE"].astype(str).str.slice(0, 10)
        lift["cap"] = pd.to_numeric(lift.get("LIFT_MARKET_CAP"), errors="coerce")
        lift["fr"] = pd.to_numeric(lift.get("FREE_RATIO"), errors="coerce")
        lift = lift.groupby(["code", "d"]).agg(cap=("cap", "sum"), fr=("fr", "first")).reset_index()

    ev = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["code", "src", "d", "notional", "avg_px"])
    if ev.empty and lift.empty:
        print("[ERR] 无慢变量情报数据，先跑 intel_flow.py --sync", file=sys.stderr)
        return 1

    # 事件表 → 可用日 lag=1
    if not ev.empty:
        ev["date"] = ev["d"].map(nxt)
        ev = ev.dropna(subset=["date"])
    if not lift.empty:
        lift["date"] = lift["d"]  # 解禁日是未来事件，T 日即可知（提前公告）

    # 出全市场日历 × 代码骨架太大，改为按代码聚合后与日线 join
    print(f"[inc ] {len(ev[ev.src=='inc']) if not ev.empty else 0:,} 条增持事件")
    print(f"[blk ] {len(ev[ev.src=='blk']) if not ev.empty else 0:,} 条大宗事件")
    print(f"[lift] {len(lift):,} 条解禁事件")

    if not ev.empty:
        ev.to_csv(OUT.replace(".csv.gz", "_events.csv.gz"), index=False,
                  compression="gzip", encoding="utf-8")
    if not lift.empty:
        lift.to_csv(OUT.replace(".csv.gz", "_lift.csv.gz"), index=False,
                    compression="gzip", encoding="utf-8")
    print(f"[OK] 落盘 {OUT.replace('.csv.gz','_events.csv.gz')} / _lift.csv.gz")
    return 0


# 龙虎榜/机构席位情报因子已证伪（MEMORY.md §3）；此构建器属已废弃 track。
RUN_REJECTED_INTEL = False

if __name__ == "__main__":
    if not RUN_REJECTED_INTEL:
        print("[SKIP] intel_pool 情报特征构建已 REJECT（见 MEMORY.md §3）。"
              "置 RUN_REJECTED_INTEL=True 可临时重开用于审计。")
        raise SystemExit(0)
    sys.exit(build())
