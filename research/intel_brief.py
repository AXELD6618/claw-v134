# -*- coding: utf-8 -*-
"""
每日情报简报（Round33）：把龙虎榜/机构席位/游资席位翻译成人话

定位：**情报解释层，不是信号层**。
  实测（research/intel_edge_test.py，3,450,495 stock-day / 2124 日）：
    上榜股 BUY 命中 18.86% vs 全市场 9.87% = 1.91 倍，
    但事件组 T+1 均值 −0.822%、组合 PF 0.701 → 不可作为选股因子。
  所以本模块只回答「今天谁在动、为什么动、谁在卖」，不给买卖建议。

用法：
  python research/intel_brief.py                  # 最近一个已落盘交易日
  python research/intel_brief.py --date 2026-10-16
  python research/intel_brief.py --top 15 --json
"""
import argparse
import gzip
import json
import os
import sys

import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")
INST_SEAT = "机构专用"


def seat_type(name: str) -> str:
    """席位分类。营业部 ≠ 游资：总部/分公司/自营多为机构通道，股通是北向。"""
    s = str(name or "")
    if "股通" in s:
        return "北向"
    if INST_SEAT in s:
        return "机构"
    if any(k in s for k in ("总部", "分公司", "自营", "证券投资部", "资产管理")):
        return "机构通道"
    if "证券营业部" in s or "营业部" in s:
        return "营业部"
    return "其他"


# A 股代码前缀（沪主板 60 / 科创 68 / 深主板 00 / 创业板 30 / 北交所 43·83·87·92·8）
_A_PREFIX2 = {"60", "68", "00", "30", "43", "83", "87", "88", "92"}


def _is_a_share(code):
    c = str(code).zfill(6)
    return c[:2] in _A_PREFIX2 or c[:1] == "8"


def load_day(key, day):
    p = os.path.join(INTEL, key, f"{day}.csv.gz")
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            d = pd.read_csv(f, dtype={"code": str, "SECURITY_CODE": str})
    except Exception:
        return pd.DataFrame()
    # 龙虎榜/席位表会混入可转债等非 A 股标的。实测 2026-10-20 华宝证券上海东大名路
    # 单只 111012（可转债）净买 +4.58 亿、净卖 −4.56 亿，同时霸占游资买/卖榜第一名，
    # 把真正的股票席位全部挤下去。非正股一律剔除，简报只谈股票。
    if "code" in d.columns and not d.empty:
        d = d[d["code"].map(_is_a_share)]
    return d


def latest_day():
    d = os.path.join(INTEL, "board")
    if not os.path.isdir(d):
        return None
    fs = sorted(f for f in os.listdir(d) if f.endswith(".csv.gz"))
    return fs[-1][:10] if fs else None


def fmt_yi(x):
    try:
        return f"{float(x)/1e8:+.2f}亿"
    except Exception:
        return "—"


def _primary(df, amt_cols, expl_col="EXPLANATION"):
    """同一只票当日会上多个榜单（日涨幅/日振幅/日换手/连续三日累计），
    资金额与席位是同一批成交的重复口径，直接 groupby-sum 会成倍虚增
    （实测 2026-10-19 慈文传媒 4 条榜单 sum=+2.68亿，真实单日 +0.76亿）。
    故每只票只保留一条主榜：优先单日榜，其次成交额最大者。"""
    if df.empty or "code" not in df.columns:
        return df
    d = df.copy()
    a = None
    for c in amt_cols:
        if c in d.columns:
            v = pd.to_numeric(d[c], errors="coerce").fillna(0)
            a = v if a is None else a + v
    d["_amt"] = a if a is not None else 0.0
    # 逐票择优：单日榜优先于「连续三日」累计榜；同类里取成交额最大的一条。
    # 注意不能整体筛单日榜——只上过三日累计榜的票（如 2026-10-19 振华股份）会被整只丢掉。
    if expl_col in d.columns:
        d["_cum"] = d[expl_col].astype(str).str.contains("连续").astype(int)
    else:
        d["_cum"] = 0
    d = d.sort_values(["code", "_cum", "_amt"], ascending=[True, True, False])
    return d.drop_duplicates("code", keep="first")


_MUNI = ("上海", "北京", "天津", "重庆")
_SEAT_SUFFIX = "证券营业部"


def _seat_norm(name):
    """席位名归一：东财同一营业部存在「上海长宁区江苏路」/「长宁区江苏路」两种写法，
    不归一会被拆成两个主体，金额分散后排名失真（2026-10-22 江苏路买入 1.73 亿
    被拆成 1.23 亿 + 0.51 亿，本该排第 3 却掉到第 6 / 第 12）。

    规则：只对「<券商全称><地理>证券营业部」结构做处理，剥掉紧邻的直辖市前缀
    （仅当前缀 + 2~4 字内就是 区/县，说明前缀是冗余的地级限定）。其余一律原样返回，
    宁可漏合并，也不要把不同分支错并成一家。"""
    s = str(name) if name is not None else ""
    if not s.endswith(_SEAT_SUFFIX):
        return s
    body = s[: -len(_SEAT_SUFFIX)]
    cut = max(body.rfind("股份有限公司"), body.rfind("有限责任公司"))
    if cut < 0:
        return s
    head, loc = body[: cut + 6], body[cut + 6:]
    for m in _MUNI:
        if loc.startswith(m) and len(loc) > len(m):
            rest = loc[len(m):]
            if "区" in rest[:4] or "县" in rest[:4]:
                return head + rest + _SEAT_SUFFIX
    return s


def _dedup_seat(s):
    """同一席位同一票在多个榜单里重复出现（金额完全相同），
    按 |NET| 保留一条，避免游资榜金额被赛道重复计算放大。
    分组键用 _seat_norm 归一后的 _seat_key，显示名仍用原始全名（可读性）。"""
    s = s.copy()
    s["NET"] = pd.to_numeric(s.get("NET"), errors="coerce")
    s["_abs"] = s["NET"].abs()
    s = s.sort_values("_abs", ascending=False)
    s["_seat_key"] = s["OPERATEDEPT_NAME"].map(_seat_norm)
    s = s.drop_duplicates(["_seat_key", "code"], keep="first")
    # 同一 key 若有多种写法，展示最长的那个（信息最全）
    disp = s.groupby("_seat_key")["OPERATEDEPT_NAME"].agg(
        lambda x: max(set(x), key=len))
    s["_seat_disp"] = s["_seat_key"].map(disp)
    return s


def build(day, top=15):
    b = load_day("board", day)
    i = load_day("inst", day)
    sb = load_day("seatbuy", day)
    ss = load_day("seat_sell", day)

    out = {"date": day, "n_board": 0, "n_inst": 0, "items": [],
           "inst_net_buy": [], "inst_net_sell": [], "hot_seats": [],
           "notes": []}

    if b.empty and i.empty:
        out["notes"].append("当日无榜单数据（可能是非交易日或采集未覆盖）")
        return out

    # ---- 个股层：合并龙虎榜 + 机构席位 ----
    agg = pd.DataFrame()
    if not b.empty:
        for c in ("BILLBOARD_NET_AMT", "BILLBOARD_BUY_AMT", "BILLBOARD_SELL_AMT",
                  "ACCUM_AMOUNT", "CHANGE_RATE", "FREE_MARKET_CAP"):
            if c in b.columns:
                b[c] = pd.to_numeric(b[c], errors="coerce")
        b = _primary(b, ["BILLBOARD_BUY_AMT", "BILLBOARD_SELL_AMT"])
        agg = b.groupby("code").agg(
            name=("SECURITY_NAME_ABBR", "first"),
            chg=("CHANGE_RATE", "first"),
            net=("BILLBOARD_NET_AMT", "sum"),
            buy=("BILLBOARD_BUY_AMT", "sum"),
            sell=("BILLBOARD_SELL_AMT", "sum"),
            amt=("ACCUM_AMOUNT", "max"),
            cap=("FREE_MARKET_CAP", "max"),
            why=("EXPLANATION", "first"),
        ).reset_index()
    if not i.empty:
        for c in ("NET_BUY_AMT", "BUY_AMT", "SELL_AMT", "FREECAP", "BUY_TIMES", "SELL_TIMES"):
            if c in i.columns:
                i[c] = pd.to_numeric(i[c], errors="coerce")
        i = _primary(i, ["BUY_AMT", "SELL_AMT"])
        ia = i.groupby("code").agg(
            iname=("SECURITY_NAME_ABBR", "first"),
            inst_net=("NET_BUY_AMT", "sum"),
            inst_buy=("BUY_AMT", "sum"),
            inst_sell=("SELL_AMT", "sum"),
            inst_bt=("BUY_TIMES", "sum"),
            inst_st=("SELL_TIMES", "sum"),
        ).reset_index()
        agg = agg.merge(ia, on="code", how="outer") if not agg.empty else ia
        if "name" not in agg.columns:
            agg["name"] = agg["iname"]

    agg["net"] = pd.to_numeric(agg.get("net"), errors="coerce")
    agg["inst_net"] = pd.to_numeric(agg.get("inst_net"), errors="coerce")
    agg["amt"] = pd.to_numeric(agg.get("amt"), errors="coerce")
    agg["net_pct"] = agg["net"] / agg["amt"].replace(0, pd.NA) * 100

    out["n_board"] = int(b["code"].nunique()) if not b.empty else 0
    out["n_inst"] = int(i["code"].nunique()) if not i.empty else 0

    agg = agg.sort_values("net", ascending=False)
    for _, r in agg.head(top).iterrows():
        out["items"].append({
            "code": str(r["code"]), "name": r.get("name"),
            "chg": None if pd.isna(r.get("chg")) else float(r["chg"]),
            "net": None if pd.isna(r["net"]) else float(r["net"]),
            "inst_net": None if pd.isna(r.get("inst_net")) else float(r["inst_net"]),
            "inst_bt": None if pd.isna(r.get("inst_bt")) else int(r["inst_bt"]),
            "inst_st": None if pd.isna(r.get("inst_st")) else int(r["inst_st"]),
            "why": r.get("why") if isinstance(r.get("why"), str) else None,
        })

    # ---- 机构净买卖榜 ----
    if not i.empty:
        i2 = i.copy()
        i2 = i2.sort_values("NET_BUY_AMT", ascending=False)
        for _, r in i2.head(8).iterrows():
            if pd.notna(r.get("NET_BUY_AMT")):
                out["inst_net_buy"].append({"code": str(r["code"]),
                                            "name": r.get("SECURITY_NAME_ABBR"),
                                            "net": float(r["NET_BUY_AMT"])})
        for _, r in i2.tail(8).iloc[::-1].iterrows():
            if pd.notna(r.get("NET_BUY_AMT")):
                out["inst_net_sell"].append({"code": str(r["code"]),
                                             "name": r.get("SECURITY_NAME_ABBR"),
                                             "net": float(r["NET_BUY_AMT"])})

    # ---- 游资席位（剔除机构专用/股通/机构通道）----
    if not sb.empty and "OPERATEDEPT_NAME" in sb.columns:
        s = sb.copy()
        s["_type"] = s["OPERATEDEPT_NAME"].map(seat_type)
        s = s[s["_type"] == "营业部"]
        s = _dedup_seat(s)
        g = s.groupby("_seat_key").agg(
            net=("NET", "sum"), cnt=("code", "nunique"),
            seat=("_seat_disp", "first")).reset_index()
        g = g.sort_values("net", ascending=False)
        for _, r in g.head(8).iterrows():
            out["hot_seats"].append({"seat": r["seat"],
                                     "net": None if pd.isna(r["net"]) else float(r["net"]),
                                     "n_stock": int(r["cnt"])})
    if not ss.empty and "OPERATEDEPT_NAME" in ss.columns:
        s = ss.copy()
        s["_type"] = s["OPERATEDEPT_NAME"].map(seat_type)
        s = s[s["_type"] == "营业部"]
        s = _dedup_seat(s)
        g = s.groupby("_seat_key").agg(
            net=("NET", "sum"), cnt=("code", "nunique"),
            seat=("_seat_disp", "first")).reset_index()
        g = g.sort_values("net")
        if len(g):
            out["notes"].append("游资卖出最集中：" + " ｜ ".join(
                f"{r['seat']}（{fmt_yi(r['net'])}，{int(r['cnt'])} 只）"
                for _, r in g.head(3).iterrows()))

    # ---- 北向通道单独列（这是外资，不是游资）----
    for src, tag in ((sb, "买"), (ss, "卖")):
        if src.empty or "OPERATEDEPT_NAME" not in src.columns:
            continue
        n = src[src["OPERATEDEPT_NAME"].map(seat_type) == "北向"].copy()
        if n.empty:
            continue
        n["NET"] = pd.to_numeric(n.get("NET"), errors="coerce")
        tot = n["NET"].sum()
        out["notes"].append(f"北向通道{tag}方合计净额 {fmt_yi(tot)}（{n['code'].nunique()} 只）")

    return out


def render(o):
    L = [f"# 🔍 情报简报 · {o['date']}", ""]
    L.append(f"> 龙虎榜 {o['n_board']} 只 ／ 机构席位 {o['n_inst']} 只 ｜ **解释层，非信号层**")
    L.append("> 实测：上榜股次日高开率 18.86% vs 全市场 9.87%（1.91×），但事件组次日均值 −0.82%、PF 0.70 → 不做选股因子")
    L.append("")
    if o["notes"]:
        for n in o["notes"]:
            L.append(f"- {n}")
        L.append("")
    if o["items"]:
        L.append("## 净买入前列（龙虎榜口径）")
        L.append("| 代码 | 名称 | 涨幅 | 榜单净买 | 机构净买 | 机构买/卖次 |")
        L.append("|---|---|---|---|---|---|")
        for x in o["items"]:
            chg = "—" if x["chg"] is None else f"{x['chg']:+.2f}%"
            ins = "—" if x["inst_net"] is None else fmt_yi(x["inst_net"])
            bts = "—" if x["inst_bt"] is None else f"{x['inst_bt']}/{x['inst_st']}"
            L.append(f"| {x['code']} | {x['name']} | {chg} | {fmt_yi(x['net'])} | {ins} | {bts} |")
        L.append("")
    if o["inst_net_buy"]:
        L.append("## 机构净买入 Top")
        L.append(" ｜ ".join(f"{x['name']}({x['code']}) {fmt_yi(x['net'])}" for x in o["inst_net_buy"][:6]))
        L.append("")
    if o["inst_net_sell"]:
        L.append("## 机构净卖出 Top（风险侧）")
        L.append(" ｜ ".join(f"{x['name']}({x['code']}) {fmt_yi(x['net'])}" for x in o["inst_net_sell"][:6]))
        L.append("")
    if o["hot_seats"]:
        L.append("## 活跃游资席位（买入侧）")
        for x in o["hot_seats"]:
            L.append(f"- {x['seat']} ｜ {fmt_yi(x['net'])} ｜ 涉及 {x['n_stock']} 只")
        L.append("")
    L.append("---")
    L.append("**边界声明**：基金到期、ETF 份额变动**无免费可靠公开源**，本简报不含这两项，"
             "系统内标记 `NO_PUBLIC_SOURCE`，不猜不编。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    day = a.date or latest_day()
    if not day:
        print("[ERR] 无情报数据，先跑 research/intel_lhb.py --sync", file=sys.stderr)
        return 1
    o = build(day, a.top)
    if a.json:
        print(json.dumps(o, ensure_ascii=False, indent=1))
    else:
        md = render(o)
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"[OK] {a.out}")
        else:
            print(md)
    return 0


# 龙虎榜/机构席位情报因子已证伪（MEMORY.md §3）；此简报构建属已废弃 track。
RUN_REJECTED_INTEL = False

if __name__ == "__main__":
    if not RUN_REJECTED_INTEL:
        print("[SKIP] intel_brief 情报简报构建已 REJECT（见 MEMORY.md §3）。"
              "置 RUN_REJECTED_INTEL=True 可临时重开用于审计。")
        raise SystemExit(0)
    sys.exit(main())
