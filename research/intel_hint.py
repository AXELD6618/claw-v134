# -*- coding: utf-8 -*-
"""
每日情报提示层（Round35 · 解释层 / 风险侧，**不改 buy_score**）

定位：R33~R35 已用 11M stock-day 证明「情报事件作选股因子」在 OOS 上全部 PF<1.30，
      所以情报**不能**用来给候选加分。但情报能把「这只票为什么在候选里 / 有什么雷」
      说清楚，这正是投资者要的「动态追踪识别研判 + 及时提示买卖调整」。

本脚本对当日 tail_pick 候选逐票输出一块情报摘要：
  榜 / 机构席位 · 增持（含增持均价→浮盈浮亏） · 大宗（溢价/机构买方）
  机构成本线（大宗买方被套幅度） · 季报机构加仓/减仓 · 调研热度 · 未来解禁

用法：
  python research/intel_hint.py --date 2026-10-16
  python research/intel_hint.py --date 2026-10-16 --codes 600825,000001
  python research/intel_hint.py --date 2026-10-16 --append   # 打印可直接拼进推送的文本
"""
import argparse
import glob
import gzip
import json
import os
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
CACHE = os.path.join(PROJ, "backtest_cache")
INTEL = os.path.join(CACHE, "intel")
DAILY = os.path.join(CACHE, "daily")


def load_gz(p):
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        return pd.read_csv(gzip.open(p, "rt", encoding="utf-8"), dtype={"code": str})
    except Exception:
        return pd.DataFrame()


def money(x):
    """金额自适应单位：>=0.1 亿用亿，否则用万。"""
    try:
        v = float(x)
    except Exception:
        return "—"
    return ("%.2f 亿" % (v / 1e8)) if abs(v) >= 1e7 else ("%.0f 万" % (v / 1e4))


def _board_dates():
    d = os.path.join(INTEL, "board")
    if not os.path.isdir(d):
        return []
    return sorted(f[:10] for f in os.listdir(d) if f.endswith(".csv.gz"))


_BOARD_DATES = _board_dates()


def _prev_board_date(day):
    """day 的上一交易日（以本地龙虎榜落盘日为准，缺档自动跳过）。"""
    c = [x for x in _BOARD_DATES if x < str(day)]
    return c[-1] if c else None


def _td_gap(bd, day):
    """bd → day 之间隔了几个交易日（1 = 上一交易日）。
    注意不能用 biz_days_between：那是自然日差，周五→周一会被算成 3 天。"""
    if not bd:
        return 999
    seq = sorted(set(_BOARD_DATES) | {str(bd), str(day)})
    return len([x for x in seq if str(bd) < x <= str(day)])


def biz_days_between(d0, d1):
    """粗略自然日差（够用于「近 N 日」口径）"""
    try:
        return (datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d0, "%Y-%m-%d")).days
    except Exception:
        return 9999


def get_candidates(date, codes=None):
    if codes:
        return [{"code": c.strip().zfill(6), "name": ""} for c in codes.split(",") if c.strip()]
    for fp in (os.path.join(CACHE, f"unified_tail_pick_{date}.json"),
               os.path.join(CACHE, f"tail_pick_{date}.json"),
               os.path.join(CACHE, "tail_raw", f"{date}.json"),
               os.path.join(CACHE, "tail_raw", f"{date}-preview.json")):
        if not os.path.exists(fp):
            continue
        try:
            j = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        rows = j.get("picks") or j.get("data") or []
        out = []
        for r in rows:
            c = r.get("code") or r.get("sec_code") or r.get("symbol") or r.get("ts_code")
            if not c:
                continue
            out.append({"code": str(c).split(".")[0].zfill(6),
                        "name": r.get("sec_name") or r.get("name") or ""})
        if out:
            print("[src] %s（%d 只）" % (os.path.basename(fp), len(out)))
            return out
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--codes", default="")
    ap.add_argument("--append", action="store_true")
    a = ap.parse_args()
    date = a.date

    cands = get_candidates(date, a.codes or None)
    if not cands:
        print("[!] 无候选（%s）" % date)
        return 1
    codes = [c["code"] for c in cands]

    # ── 一次性载入各情报表，按 code 过滤 ────────────────────────────
    feat = load_gz(os.path.join(INTEL, "intel_features.csv.gz"))
    if not feat.empty:
        feat["code"] = feat["code"].astype(str).str.zfill(6)
        feat = feat[feat["code"].isin(codes)]
        # 防御：特征表若被超前重建，D 日不得读 > D 的行（盘后公布，lag=1）
        feat = feat[feat["date"].astype(str) <= date]
    poolev = load_gz(os.path.join(INTEL, "intel_pool_events.csv.gz"))
    if not poolev.empty:
        poolev["code"] = poolev["code"].astype(str).str.zfill(6)
        poolev = poolev[poolev["code"].isin(codes)]
        # 时点铁律：增持/大宗都是盘后公告，D 日只可用 date(=D+1 可用日) <= D 的记录。
        # 原来按事件日 d 过滤，等于当天公告当天就用，属前视。
        poolev = poolev[poolev["date"].astype(str) <= date]
    cost = load_gz(os.path.join(INTEL, "intel_cost_basis.csv.gz"))
    if not cost.empty:
        cost["code"] = cost["code"].astype(str).str.zfill(6)
        cost = cost[(cost["code"].isin(codes)) & (cost["date"] <= date)]
    hold = load_gz(os.path.join(INTEL, "intel_holder_events.csv.gz"))
    if not hold.empty:
        hold["code"] = hold["code"].astype(str).str.zfill(6)
        # 季报公告日 = NOTICE_DATE，盘后才可知 → 严格小于 D 才可用（lag=1）
        hold = hold[(hold["code"].isin(codes)) & (hold["avail_date"] < date)]
    surv = load_gz(os.path.join(INTEL, "intel_survey_events.csv.gz"))
    if not surv.empty:
        surv["code"] = surv["code"].astype(str).str.zfill(6)
        # 调研公告同为盘后发布 → 严格小于 D 才可用
        surv = surv[(surv["code"].isin(codes)) & (surv["avail_date"] < date)]
    lift = load_gz(os.path.join(INTEL, "intel_pool_lift.csv.gz"))
    if not lift.empty:
        lift["code"] = lift["code"].astype(str).str.zfill(6)
        lift = lift[lift["code"].isin(codes)]

    blocks = []
    recs = []
    for cd in cands:
        code, name = cd["code"], cd["name"]
        L = []
        r = {"code": code, "name": name}

        # 1) 龙虎榜 / 机构席位（快变量，lag=1：特征行的 date 行装的是**上一交易日**的榜）
        if not feat.empty:
            f = feat[feat["code"] == code].sort_values("date")
            if len(f):
                last = f.iloc[-1]
                fresh = biz_days_between(str(last["date"]), date) <= 15
                if fresh and "lhb_prev" in f.columns and float(last.get("lhb_prev") or 0) > 0:
                    nb = float(last.get("lhb_net") or 0) / 1e8
                    has_inst = float(last.get("inst_prev") or 0) > 0
                    # 标签必须落到真实上榜日：last["date"] 是该行可用日，榜在它的前一交易日。
                    # 若该票最后一条特征行早于 D（中间无新事件），榜就可能不是「上一交易日」，
                    # 原来硬编码会写错（实测 603893 于 10-16 上榜，D=10-20 被标成「上一交易日」）。
                    bd = _prev_board_date(str(last["date"]))
                    gap = _td_gap(bd, date)
                    tag = "上一交易日上榜" if gap == 1 else "%s 上榜（已过 %d 个交易日）" % (str(bd)[5:], gap)
                    ins_txt = ""
                    if has_inst:
                        inet = float(last.get("inst_net") or 0) / 1e8
                        ins_txt = "，机构席位净买 %+.2f 亿%s" % (
                            inet, "（**机构在出**）" if inet < 0 else "")
                        r["inst_net"] = inet
                    L.append("榜: %s，榜单净买 %+.2f 亿%s" % (tag, nb, ins_txt))
                    r["lhb"] = True
                    r["lhb_d"] = bd

        # 2) 增持（近 20 自然日）
        inc = pd.DataFrame()
        if not poolev.empty:
            inc = poolev[(poolev["code"] == code) & (poolev["src"] == "inc")]
            inc = inc[inc["d"].astype(str).map(lambda d: biz_days_between(d, date) <= 20)]
        if len(inc):
            i0 = inc.sort_values("d").iloc[-1]
            L.append("增持: %s 股东增持（金额 %s）" % (str(i0["d"])[5:],
                     money(i0["notional"])))
            r["inc"] = True

        # 3) 大宗（近 10 自然日，看溢价）
        blk = pd.DataFrame()
        if not poolev.empty:
            blk = poolev[(poolev["code"] == code) & (poolev["src"] == "blk")]
            blk = blk[blk["d"].astype(str).map(lambda d: biz_days_between(d, date) <= 10)]
        if len(blk):
            b0 = blk.sort_values("d").iloc[-1]
            px = float(b0.get("avg_px") or 0)
            L.append("大宗: %s 加权溢价 %+.1f%%，%s%s"
                     % (str(b0["d"])[5:], px * 100, money(b0["notional"]),
                        "（溢价接货）" if px > 0 else "（折价出货）"))
            r["blk"] = True
            r["blk_prem"] = px

        # 4) 机构成本线（被套 / 浮盈）
        # 时效铁律：成本线 =「该日收盘价 vs 截至该日的 60 日加权大宗成本」，只对当日成立。
        # 表按日连续生成，一旦窗口内的大宗事件滚出 60 日就停止出新行 —— 末行可能是几个月前。
        # 原实现直接取 iloc[-1] 无限回溯（实测 300115 末行 2026-05-14，D=10-21 仍报「低于成本线
        # 7.6%，被套」），等于拿 5 个月前的快照当当日风险项，属严重误导。全表 3,830 只中
        # 2,858 只（75%）末行早于上月，2,146 只（56%）早于上季。改为只认近 3 个交易日内的记录。
        if not cost.empty:
            c = cost[cost["code"] == code].sort_values("date")
            if len(c):
                c0 = c.iloc[-1]
                stale_td = biz_days_between(str(c0["date"]), date)
                if stale_td > 3:
                    r["cost_stale_td"] = stale_td
                    r["cost_stale_last"] = str(c0["date"])
                    c = c.iloc[0:0]
            if len(c):
                c0 = c.iloc[-1]
                for col, lab in (("gap_org", "机构大宗成本"), ("gap_inc", "股东增持成本")):
                    v = c0.get(col)
                    if v is not None and np.isfinite(float(v)):
                        v = float(v)
                        L.append("%s: 现价%s成本线 %.1f%%%s"
                                 % (lab, "低于" if v < 0 else "高于", abs(v) * 100,
                                    "（**这批资金被套**）" if v < -0.05 else ""))
                        r[col] = v
                        break

        # 5) 季报机构持仓变化（最近一期）
        if not hold.empty:
            h = hold[hold["code"] == code].sort_values("avail_date")
            if len(h):
                h0 = h.iloc[-1]
                d_org = h0.get("d_org_ratio")
                if d_org is not None and np.isfinite(float(d_org)):
                    d_org = float(d_org)
                    # 三档，别用 >0 二元判定：实测 600319 环比为 0.0（显示 +0.0）却被标「减仓」。
                    # 阈值取 ±0.05 个百分点，与 %.1f 的显示精度对齐。
                    tag_hold = ("**加仓**" if d_org > 0.05
                                else ("减仓" if d_org < -0.05 else "基本持平"))
                    L.append("机构持仓: %s 期 机构流通占比 %.1f%%（环比 %+.1f 个百分点）%s"
                             % (str(h0["end"])[:7], float(h0.get("org_ratio") or 0),
                                d_org, tag_hold))
                    r["d_org_ratio"] = d_org

        # 6) 调研热度（近 20 自然日）
        if not surv.empty:
            s = surv[surv["code"] == code]
            s = s[s["avail_date"].map(lambda d: biz_days_between(d, date) <= 20)]
            if len(s):
                n = int(s["n_inst"].sum())
                nf = int(s["n_fund"].sum())
                L.append("调研: 近20日 %d 家机构（基金/资管 %d 家）" % (n, nf))
                r["survey_20d"] = n

        # 7) 解禁（未来 60 自然日）
        if not lift.empty:
            lf = lift[lift["code"] == code]
            fut = lf[lf["d"].astype(str).map(lambda d: 0 <= biz_days_between(date, d) <= 60)]
            if len(fut):
                l0 = fut.sort_values("d").iloc[0]
                L.append("⚠ 解禁: %s 解禁（排雷项）" % str(l0["d"])[5:])
                r["lift_d"] = str(l0["d"])

        if L:
            blocks.append("【情报 · %s %s】\n  " % (code, name or "") + "\n  ".join(L))
        recs.append(r)

    txt = "\n".join(blocks) if blocks else "（无情报命中）"
    print("\n" + txt + "\n")
    out = os.path.join(CACHE, f"intel_hint_{date}.json")
    json.dump({"date": date, "n": len(recs), "items": recs},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=float)
    print("[OK] %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
