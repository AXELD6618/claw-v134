# -*- coding: utf-8 -*-
"""
情报特征构建器：龙虎榜 / 机构席位 → per-(code,date) 特征表（Round33 情报层 P1）

时点铁律（最容易犯错的地方）：
  龙虎榜/机构席位均在 **T 日收盘后（约 18:00）** 公布。
  所以 T 日 14:50 尾盘选股时，能用的只有 **T-1 日**的榜单。
  → 榜单日 d 的数据，可用日 = d 的下一个交易日。本模块按此对齐，绝不泄漏。

输出：backtest_cache/intel/intel_features.csv.gz
  列 = code, date, lhb_prev, lhb_net_ratio, ... （date 为"可用日"）

用法：
  python research/intel_features.py            # 全量重建
  python research/intel_features.py --days 60  # 只重建最近 60 个交易日
"""
import argparse
import glob
import gzip
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
OUT = os.path.join(INTEL, "intel_features.csv.gz")
CAL_FILE = os.path.join(DAILY, "000001.csv")


def trading_calendar():
    df = pd.read_csv(CAL_FILE, usecols=["日期"])
    d = sorted(df["日期"].astype(str).str.slice(0, 10).unique())
    return d


def load_table(key):
    d = os.path.join(INTEL, key)
    if not os.path.isdir(d):
        return pd.DataFrame()
    fs = sorted(f for f in os.listdir(d) if f.endswith(".csv.gz"))
    parts = []
    for f in fs:
        try:
            x = pd.read_csv(os.path.join(d, f), compression="gzip",
                            dtype={"code": str, "SECURITY_CODE": str})
            if len(x):
                parts.append(x)
        except Exception:
            pass
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["TRADE_DATE"] = df["TRADE_DATE"].astype(str).str.slice(0, 10)
    return df.drop_duplicates(subset=["code", "TRADE_DATE"])


def build_seat_conc():
    """龙虎榜席位买卖集中度（r36 Task1）：
        lhb_buy_conc = (T日上榜票 top-3 买席金额之和) ÷ (top-3 卖席金额之和)
        lhb_buy_gt_sell = (集中度 > 1) 二值。
    数据来自 seatbuy(seat 买入明细) / seat_sell(seat 卖出明细)，按 (code,TRADE_DATE) 聚合。
    与现有 LHB 特征共用 lag-1 对齐（榜单日→下一交易日可用），无未来泄漏。
    """
    sb = load_table("seatbuy")
    ss = load_table("seat_sell")
    out = pd.DataFrame()
    if not sb.empty:
        sb["BUY"] = pd.to_numeric(sb["BUY"], errors="coerce").fillna(0.0)
        sb_top = sb.assign(_r=sb.groupby(["code", "TRADE_DATE"])["BUY"]
                           .rank(ascending=False, method="first"))
        sb_top = sb_top[sb_top["_r"] <= 3]
        buy_top3 = sb_top.groupby(["code", "TRADE_DATE"])["BUY"].sum().rename("lhb_buy_top3")
        out = buy_top3.reset_index()
    if not ss.empty:
        ss["SELL"] = pd.to_numeric(ss["SELL"], errors="coerce").fillna(0.0)
        ss_top = ss.assign(_r=ss.groupby(["code", "TRADE_DATE"])["SELL"]
                           .rank(ascending=False, method="first"))
        ss_top = ss_top[ss_top["_r"] <= 3]
        sell_top3 = ss_top.groupby(["code", "TRADE_DATE"])["SELL"].sum().rename("lhb_sell_top3")
        s2 = sell_top3.reset_index()
        out = out.merge(s2, on=["code", "TRADE_DATE"], how="outer") if not out.empty else s2
    if out.empty:
        return out
    out["lhb_buy_conc"] = out["lhb_buy_top3"] / out["lhb_sell_top3"].replace(0, np.nan)
    out["lhb_buy_conc"] = out["lhb_buy_conc"].fillna(0.0)
    out["lhb_buy_gt_sell"] = (out["lhb_buy_conc"] > 1).astype(np.int8)
    return out


def build(days=None):
    cal = trading_calendar()
    nxt = {cal[i]: cal[i + 1] for i in range(len(cal) - 1)}

    board = load_table("board")
    inst = load_table("inst")
    seat = build_seat_conc()
    if board.empty and inst.empty:
        print("[ERR] 无情报数据，先跑 intel_lhb.py --sync", file=sys.stderr)
        return 1

    # ---- 龙虎榜 ----
    b = pd.DataFrame()
    if not board.empty:
        g = board.copy()
        for c in ("BILLBOARD_NET_AMT", "ACCUM_AMOUNT", "BUY_RATIO", "SELL_RATIO",
                  "DEAL_AMOUNT_RATIO", "TURNOVERRATE", "CHANGE_RATE"):
            if c in g.columns:
                g[c] = pd.to_numeric(g[c], errors="coerce")
        b = g.groupby(["code", "TRADE_DATE"]).agg(
            lhb_buy=("BILLBOARD_BUY_AMT", "sum"),
            lhb_sell=("BILLBOARD_SELL_AMT", "sum"),
            lhb_net=("BILLBOARD_NET_AMT", "sum"),
            lhb_amt=("ACCUM_AMOUNT", "max"),
            lhb_deal_ratio=("DEAL_AMOUNT_RATIO", "max"),
            lhb_chg=("CHANGE_RATE", "max"),
            lhb_turn=("TURNOVERRATE", "max"),
            lhb_rows=("code", "size"),
        ).reset_index()
        b["lhb_net_ratio"] = b["lhb_net"] / b["lhb_amt"].replace(0, np.nan)
        b["lhb_deal_pct"] = b["lhb_net"] / b["lhb_deal_ratio"].replace(0, np.nan)

    # ---- 机构席位 ----
    i = pd.DataFrame()
    if not inst.empty:
        g = inst.copy()
        for c in ("BUY_AMT", "SELL_AMT", "NET_BUY_AMT", "FREECAP", "RATIO",
                  "BUY_TIMES", "SELL_TIMES", "ACCUM_AMOUNT"):
            if c in g.columns:
                g[c] = pd.to_numeric(g[c], errors="coerce")
        i = g.groupby(["code", "TRADE_DATE"]).agg(
            inst_buy=("BUY_AMT", "sum"),
            inst_sell=("SELL_AMT", "sum"),
            inst_net=("NET_BUY_AMT", "sum"),
            inst_buy_times=("BUY_TIMES", "sum"),
            inst_sell_times=("SELL_TIMES", "sum"),
            inst_freecap=("FREECAP", "max"),
            inst_ratio=("RATIO", "max"),
        ).reset_index()
        # FREECAP 单位=亿元 → 净买额占流通市值
        i["inst_net_cap"] = i["inst_net"] / (i["inst_freecap"] * 1e8).replace(0, np.nan)

    m = b.merge(i, on=["code", "TRADE_DATE"], how="outer")
    if not seat.empty:
        m = m.merge(seat, on=["code", "TRADE_DATE"], how="outer")
    m["date"] = m["TRADE_DATE"].map(nxt)          # ★ lag=1 对齐：榜单日→可用日
    m = m.dropna(subset=["date"])
    m["lhb_prev"] = m["lhb_net"].notna().astype(np.int8)
    m["inst_prev"] = m["inst_net"].notna().astype(np.int8)
    m["inst_net_sign"] = np.sign(m["inst_net"].fillna(0)).astype(np.int8)
    m["inst_net_cap"] = m["inst_net_cap"].fillna(0.0)
    m["lhb_net_ratio"] = m["lhb_net_ratio"].fillna(0.0)
    m["lhb_deal_ratio"] = m["lhb_deal_ratio"].fillna(0.0)
    m["lhb_chg"] = m["lhb_chg"].fillna(0.0)
    m["lhb_turn"] = m["lhb_turn"].fillna(0.0)
    for c in ("inst_buy_times", "inst_sell_times", "inst_buy", "inst_sell",
              "inst_net", "lhb_net", "lhb_buy", "lhb_sell", "lhb_rows",
              "lhb_buy_top3", "lhb_sell_top3", "lhb_buy_conc"):
        if c in m.columns:
            m[c] = m[c].fillna(0.0)
    if "lhb_buy_gt_sell" in m.columns:
        m["lhb_buy_gt_sell"] = m["lhb_buy_gt_sell"].fillna(0).astype(np.int8)

    keep = ["code", "date", "lhb_prev", "inst_prev", "lhb_net", "lhb_net_ratio",
            "lhb_deal_ratio", "lhb_chg", "lhb_turn", "lhb_rows",
            "inst_net", "inst_net_cap", "inst_net_sign", "inst_buy_times",
            "inst_sell_times", "inst_buy", "inst_sell", "inst_ratio",
            "lhb_buy_top3", "lhb_sell_top3", "lhb_buy_conc", "lhb_buy_gt_sell"]
    keep = [c for c in keep if c in m.columns]
    m = m[keep].sort_values(["code", "date"])

    # ---- 滚动窗口（只用历史，按 code 分组，保证不跨未来）----
    g = m.groupby("code", sort=False)
    m["lhb_cnt_5d"] = g["lhb_prev"].rolling(5, min_periods=1).sum().reset_index(0, drop=True)
    m["lhb_net_ratio_5d"] = g["lhb_net_ratio"].rolling(5, min_periods=1).sum().reset_index(0, drop=True)
    m["inst_net_cap_5d"] = g["inst_net_cap"].rolling(5, min_periods=1).sum().reset_index(0, drop=True)
    m["inst_cnt_5d"] = g["inst_prev"].rolling(5, min_periods=1).sum().reset_index(0, drop=True)

    if days:
        keep_dates = sorted(m["date"].unique())[-days:]
        m = m[m["date"].isin(keep_dates)]

    os.makedirs(INTEL, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        m.to_csv(f, index=False)
    print(f"[OK] {len(m):,} 行 × {len(m.columns)} 列 → {OUT}")
    print(f"     覆盖 {m['date'].nunique()} 个可用日（{m['date'].min()} ~ {m['date'].max()}）")
    print(f"     涉及 {m['code'].nunique()} 只标的")
    print(f"     lhb_prev 命中率 {m['lhb_prev'].mean():.4f} / inst_prev {m['inst_prev'].mean():.4f}")
    return 0


# 龙虎榜/机构席位情报因子已证伪（MEMORY.md §3）；此特征构建属已废弃 track。
RUN_REJECTED_INTEL = False

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None)
    a = ap.parse_args()
    if not RUN_REJECTED_INTEL:
        print("[SKIP] intel_features 情报特征构建已 REJECT（见 MEMORY.md §3）。"
              "置 RUN_REJECTED_INTEL=True 可临时重开用于审计。")
        raise SystemExit(0)
    sys.exit(build(a.days))
