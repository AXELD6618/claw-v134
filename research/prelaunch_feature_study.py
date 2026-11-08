#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""prelaunch_feature_study.py — 启动前一日 + 前 120 日特征画像（严格无未来函数）

用途：回答「某只票在启动前一日长什么样，能不能被提前识别」。

无未来函数保证：
  · 基准日 as_of 的所有特征，只使用 date <= as_of 的 K 线；
  · 标签（T 日是否涨停）只在 as_of 之后的日子取，且只用于事后验证，不参与特征计算；
  · 任何 rolling 窗口都只向过去看，不含 as_of 之后的任何数据。

用法：
  .venv/Scripts/python.exe research/prelaunch_feature_study.py snapshot --as-of 2026-10-26 --codes 002249,002708
  .venv/Scripts/python.exe research/prelaunch_feature_study.py validate --as-of 2026-10-26 --days 250
"""
import os
import sys
import csv
import json
import argparse
import statistics
from research.board_limit import board_limit_pct, is_limit_up

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(ROOT, "backtest_cache", "daily")
CACHE = os.path.join(ROOT, "backtest_cache")
WIN = 120


def lim_of(code):
    return board_limit_pct(code)



def load_bars(code):
    """返回 [(date, open, close, high, low, vol), ...]，按日期升序。"""
    p = os.path.join(DAILY, "%s.csv" % code)
    out = []
    if not os.path.exists(p):
        return out
    try:
        for r in csv.reader(open(p, encoding="utf-8")):
            if len(r) < 6:
                continue
            if not (len(r[0]) == 10 and r[0][4] == "-" and r[0][7] == "-"):
                continue
            try:
                out.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
            except Exception:
                continue
    except Exception:
        return out
    return out


def _ma(vals, n, i):
    if i + 1 < n:
        return None
    seg = vals[i + 1 - n:i + 1]
    return sum(seg) / float(n)


def features_at(bars, i, lim):
    """在索引 i（=as_of 当日）计算特征。只使用 bars[0..i]。"""
    if i < 61:
        return None
    closes = [b[2] for b in bars]
    highs = [b[3] for b in bars]
    lows = [b[4] for b in bars]
    vols = [b[5] for b in bars]
    c = closes[i]
    o, h, l, v = bars[i][1], highs[i], lows[i], vols[i]
    if c <= 0 or v <= 0:
        return None

    f = {}
    # ---- 动量 ----
    def ret(n):
        if i - n < 0 or closes[i - n] <= 0:
            return None
        return c / closes[i - n] - 1.0
    f["r1"] = c / closes[i - 1] - 1.0 if closes[i - 1] > 0 else None
    f["r5"] = ret(5)
    f["r10"] = ret(10)
    f["r20"] = ret(20)
    f["r60"] = ret(60)

    # ---- 均线位置 ----
    for n in (5, 10, 20, 60):
        m = _ma(closes, n, i)
        f["ma%d_dev" % n] = (c / m - 1.0) if m else None
    m20_now = _ma(closes, 20, i)
    m20_prev = _ma(closes, 20, i - 5) if i >= 25 else None
    f["ma20_slope5"] = (m20_now / m20_prev - 1.0) if (m20_now and m20_prev) else None

    # ---- 120 日位置（只回看，不含未来）----
    lo = max(0, i - WIN + 1)
    w_high = max(highs[lo:i + 1])
    w_low = min(lows[lo:i + 1])
    f["to_high120"] = c / w_high - 1.0
    f["pos120"] = (c - w_low) / (w_high - w_low) if w_high > w_low else None
    h60 = max(highs[max(0, i - 59):i + 1])
    f["to_high60"] = c / h60 - 1.0

    # ---- 量能 ----
    def vr(n):
        if i + 1 < n:
            return None
        avg = sum(vols[i + 1 - n:i + 1]) / float(n)
        return v / avg if avg > 0 else None
    f["vr5"] = vr(5)
    f["vr20"] = vr(20)
    f["vr60"] = vr(60)

    # ---- 波动 ----
    rng = [ (highs[k] - lows[k]) / closes[k] for k in range(max(1, i - 19), i + 1) if closes[k] > 0 ]
    f["range20"] = sum(rng) / len(rng) if rng else None
    rets = []
    for k in range(max(1, i - 19), i + 1):
        if closes[k - 1] > 0:
            rets.append(closes[k] / closes[k - 1] - 1.0)
    f["vol20"] = statistics.pstdev(rets) if len(rets) >= 2 else None

    # ---- 涨停基因（只用过去的涨停）----
    zt_cnt = 0
    last_zt = None
    for k in range(max(1, i - WIN + 1), i + 1):
        if closes[k - 1] > 0 and closes[k] / closes[k - 1] - 1.0 >= lim - 0.005:
            zt_cnt += 1
            last_zt = k
    f["zt120"] = zt_cnt
    f["days_since_zt"] = (i - last_zt) if last_zt is not None else WIN

    # ---- T-1 当日形态 ----
    f["close_pos"] = (c - l) / (h - l) if h > l else None      # 收在当日区间的位置
    f["up_shadow"] = (h - c) / c
    f["dn_shadow"] = (c - l) / c
    f["gap_open"] = o / closes[i - 1] - 1.0 if closes[i - 1] > 0 else None

    # ---- 规模代理 ----
    # vol 单位是【手】，需 ×100 换成股，否则成交额会小两个数量级
    amt = [closes[k] * vols[k] * 100.0 for k in range(max(0, i - 19), i + 1)]
    f["amt20_yi"] = (sum(amt) / len(amt)) / 1e8 if amt else None   # 亿元

    # ---- 连续上涨天数 ----
    cu = 0
    k = i
    while k >= 1 and closes[k] > closes[k - 1]:
        cu += 1
        k -= 1
    f["consec_up"] = cu
    return f


def build_snapshot(as_of, want_codes):
    files = sorted(f for f in os.listdir(DAILY) if f.endswith(".csv"))
    rows = {}
    for fn in files:
        code = fn[:-4]
        bars = load_bars(code)
        if len(bars) < 62:
            continue
        idx = None
        for j in range(len(bars) - 1, max(-1, len(bars) - 6), -1):
            if bars[j][0] == as_of:
                idx = j
                break
        if idx is None:
            continue
        f = features_at(bars, idx, lim_of(code))
        if f:
            f["_close"] = bars[idx][2]
            rows[code] = f
    return rows


FEATS = ["r1", "r5", "r10", "r20", "r60", "ma5_dev", "ma10_dev", "ma20_dev", "ma60_dev",
         "ma20_slope5", "to_high120", "pos120", "to_high60", "vr5", "vr20", "vr60",
         "range20", "vol20", "zt120", "days_since_zt", "close_pos", "up_shadow",
         "dn_shadow", "gap_open", "amt20_yi", "consec_up"]


def pct_rank(vals, x):
    """x 在 vals 中的分位（0-100）。"""
    if x is None or not vals:
        return None
    n_less = sum(1 for v in vals if v < x)
    return 100.0 * n_less / len(vals)


def cmd_snapshot(a):
    codes = [c.strip() for c in str(a.codes).split(",") if c.strip()]
    rows = build_snapshot(a.as_of, codes)
    print("截面样本（%s 有日线的票）: %d 只" % (a.as_of, len(rows)))
    if not rows:
        return 1
    # 全市场分布 & 目标分位
    dist = {k: [r[k] for r in rows.values() if r.get(k) is not None] for k in FEATS}
    outp = {"as_of": a.as_of, "n_universe": len(rows), "targets": {}}
    for code in codes:
        r = rows.get(code)
        if not r:
            print("\n%s: 无 %s 日线" % (code, a.as_of))
            continue
        print("\n" + "=" * 78)
        print("【%s】%s 收盘 %.2f   —— 每个特征后的 ↑ 表示在全市场中的分位" % (code, a.as_of, r["_close"]))
        print("%-16s %12s %8s   %s" % ("特征", "值", "分位", "含义"))
        desc = {
            "r1": "T-1 当日涨跌幅", "r5": "近5日涨幅", "r10": "近10日涨幅",
            "r20": "近20日涨幅", "r60": "近60日涨幅",
            "ma5_dev": "偏离MA5", "ma10_dev": "偏离MA10", "ma20_dev": "偏离MA20",
            "ma60_dev": "偏离MA60", "ma20_slope5": "MA20五日斜率",
            "to_high120": "距120日最高", "pos120": "120日区间位置(0=最低,1=最高)",
            "to_high60": "距60日最高", "vr5": "量比(vs5日)", "vr20": "量比(vs20日)",
            "vr60": "量比(vs60日)", "range20": "20日平均振幅", "vol20": "20日收益波动率",
            "zt120": "120日涨停次数", "days_since_zt": "距上次涨停天数",
            "close_pos": "收盘在当日区间位置", "up_shadow": "上影线占比",
            "dn_shadow": "下影线占比", "gap_open": "当日跳空幅度",
            "amt20_yi": "20日均成交额(亿)", "consec_up": "连续上涨天数",
        }
        tgt = {}
        for k in FEATS:
            v = r.get(k)
            p = pct_rank(dist[k], v) if v is not None else None
            vs = ("%.4f" % v) if v is not None else "None"
            ps = ("%5.1f%%" % p) if p is not None else "  --"
            bar = ""
            if p is not None:
                bar = "█" * int(round(p / 10.0))
            print("%-16s %12s %8s   %-26s %s" % (k, vs, ps, desc.get(k, ""), bar))
            tgt[k] = {"value": v, "pct": p}
        outp["targets"][code] = tgt
    json.dump(outp, open(os.path.join(CACHE, "feature_snapshot_%s.json" % a.as_of), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n落盘: backtest_cache/feature_snapshot_%s.json" % a.as_of)
    return 0


RULES_SPEC = [
    ("BASE_ALL", lambda f: True, "全市场基线"),
    # 大洋电机的形态：涨停基因 + 20日大涨 + 爆量 + T-1 大阴线洗盘
    ("WASHOUT", lambda f: (f.get("zt120", 0) >= 2 and (f.get("vr20") or 0) >= 3.0
                           and (f.get("r20") or 0) >= 0.08 and (f.get("r1") or 0) <= -0.05),
     "涨停基因≥2 + 量比20≥3 + 20日涨≥8% + T-1跌≤-5%"),
    # 光洋股份的形态：强涨停基因 + T-1 封板收在最高
    ("FIRSTBOARD_STRONG", lambda f: (f.get("zt120", 0) >= 3 and (f.get("r1") or 0) >= 0.095
                                    and (f.get("close_pos") or 0) >= 0.95),
     "120日涨停≥3 + T-1涨停 + 收在最高"),
    # 高位突破（不要求 T-1 涨停）
    ("HIGH_BREAKOUT", lambda f: (f.get("zt120", 0) >= 2 and (f.get("pos120") or 0) >= 0.70
                                 and (f.get("to_high120") or -1) >= -0.15
                                 and (f.get("r20") or 0) >= 0.05),
     "涨停基因≥2 + 120日高位≥0.7 + 距高≤15% + 20日涨≥5%"),
    # 纯 T-1 涨停（现有连板策略的底片）
    ("T1_ZT", lambda f: (f.get("r1") or 0) >= 0.095, "T-1 涨停（现连板策略的底片）"),
    # 可执行化尝试：T-1 涨停但收盘不在最高（开过板 / 未封死），尾盘理论上买得到
    # close_pos<0.95 意味着收盘时价格离当日最高≥5% 的振幅，不是一字或封死板
    ("T1_ZT_UNSEALED", lambda f: (f.get("r1") or 0) >= 0.095
                                  and (f.get("close_pos") is not None)
                                  and (f.get("close_pos") or 0) < 0.95,
     "T-1涨停但未封死收盘（可买入子集，检验 edge 是否随封板一起消失）"),
    # 已封死子集，用来与上面做干净对照
    ("T1_ZT_SEALED", lambda f: (f.get("r1") or 0) >= 0.095
                                and (f.get("close_pos") is not None)
                                and (f.get("close_pos") or 0) >= 0.95,
     "T-1涨停且封死收盘（不可买入子集，FIRSTBOARD_STRONG 的父集）"),
]


def trade_calendar():
    """全市场交易日并集（升序）。用来对齐样本，避免不同票覆盖不同时间段造成混样。"""
    allds = set()
    for fn in os.listdir(DAILY):
        if not fn.endswith(".csv"):
            continue
        p = os.path.join(DAILY, fn)
        try:
            for r in csv.reader(open(p, encoding="utf-8")):
                if r and len(r[0]) == 10 and r[0][4] == "-" and r[0][7] == "-":
                    allds.add(r[0])
        except Exception:
            continue
    return sorted(allds)


def cmd_check_date(a):
    """指定日期回放：该日收盘后，各规则会选出哪些票。用于验证「某只票能否被提前选出」。"""
    date = a.date
    files = sorted(f for f in os.listdir(DAILY) if f.endswith(".csv"))
    hits = {name: [] for name, _, _ in RULES_SPEC}
    n_done = 0
    for fn in files:
        code = fn[:-4]
        n_done += 1
        if n_done % 1000 == 0:
            print("[progress] %d/%d" % (n_done, len(files)), flush=True)
        bars = load_bars(code)
        if len(bars) < 62:
            continue
        idx = None
        for j in range(len(bars) - 1, max(-1, len(bars) - 8), -1):
            if bars[j][0] == date:
                idx = j
                break
        if idx is None:
            continue
        f = features_at(bars, idx, lim_of(code))
        if not f:
            continue
        for name, fn_rule, _ in RULES_SPEC:
            try:
                if fn_rule(f):
                    hits[name].append((code, f))
            except Exception:
                continue
    print("回放日 %s：当日有日线的票 %d 只" % (date, len(files)))
    probes = [c.strip() for c in str(a.codes).split(",") if c.strip()]
    for name, _, desc in RULES_SPEC:
        lst = hits[name]
        mark = [c for c, _ in lst if c in probes]
        print("\n%-20s 命中 %d 只  %s" % (name, len(lst), ("← 含探针 " + ",".join(mark)) if mark else ""))
        print("   规则: %s" % desc)
        show = [c for c, _ in lst if c in probes] + [c for c, _ in lst if c not in probes][:8]
        for c in show[:12]:
            f = dict(lst[[x for x, _ in lst].index(c)][1])
            print("     %s r1=%+.2f%% r20=%+.1f%% vr20=%.2f zt120=%d close_pos=%s" % (
                c, (f.get("r1") or 0) * 100, (f.get("r20") or 0) * 100,
                f.get("vr20") or 0, f.get("zt120") or 0,
                ("%.2f" % f["close_pos"]) if f.get("close_pos") is not None else "-"))
    if getattr(a, "out", None):
        dump = {"date": date,
                "rules": {name: [{"code": c,
                                  "r1": f.get("r1"), "r20": f.get("r20"),
                                  "vr20": f.get("vr20"), "zt120": f.get("zt120"),
                                  "close_pos": f.get("close_pos"),
                                  "pos120": f.get("pos120"),
                                  "to_high120": f.get("to_high120")}
                                 for c, f in lst] for name, lst in hits.items()}}
        json.dump(dump, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\n全量名单落盘: %s" % a.out)
    return 0


def cmd_validate(a):
    """历史大样本验证：特征只用 T-1 及之前，标签用 T 日，严格无未来函数。

    2026-10-27 修正：原实现按"每只票的最后 N 根"取样，不同票覆盖的日历区间不同，
    会把不同时间段的样本混在一起（实测出现 290 个观测日 > 请求的 120 日）。
    现改为先建统一交易日历、截取最近 N 日，再只统计这些日历日上的样本；
    并按时间前后对半做一次 OOS 稳定性检查（前半年 vs 后半年），避免只报样本内数字。
    """
    days = int(a.days)
    offset = int(getattr(a, "offset", 0) or 0)
    files = sorted(f for f in os.listdir(DAILY) if f.endswith(".csv"))
    cal = trade_calendar()
    if not cal:
        print("交易日历为空，无法验证")
        return 1
    # offset>0 时取"再往前推 offset 个交易日"的窗口，用来做与主窗口完全不重叠的 HOLDOUT
    end = len(cal) - offset
    window = set(cal[max(0, end - days):end])
    if not window:
        print("窗口为空：offset=%d days=%d" % (offset, days))
        return 1
    mid = sorted(window)[len(window) // 2]
    RULES = RULES_SPEC

    def blank():
        return {"n": 0, "zt": 0, "rets": [],
                "h1_n": 0, "h1_zt": 0, "h1_rets": [],
                "h2_n": 0, "h2_zt": 0, "h2_rets": []}

    stats = {name: blank() for name, _, _ in RULES}
    for fn in files:
        code = fn[:-4]
        bars = load_bars(code)
        if len(bars) < 70:
            continue
        lim = lim_of(code)
        start = max(61, len(bars) - days - offset - 2)
        for i in range(start, len(bars) - 1):
            d = bars[i][0]
            if d not in window:
                continue
            f = features_at(bars, i, lim)
            if not f:
                continue
            t_prev = bars[i][2]
            t_close = bars[i + 1][2]
            if t_prev <= 0:
                continue
            t_ret = t_close / t_prev - 1.0
            t_is_zt = t_ret >= lim - 0.005
            second_half = d >= mid
            for name, fn_rule, _ in RULES:
                try:
                    if not fn_rule(f):
                        continue
                except Exception:
                    continue
                s = stats[name]
                s["n"] += 1
                s["rets"].append(t_ret)
                if t_is_zt:
                    s["zt"] += 1
                if second_half:
                    s["h2_n"] += 1
                    s["h2_rets"].append(t_ret)
                    if t_is_zt:
                        s["h2_zt"] += 1
                else:
                    s["h1_n"] += 1
                    s["h1_rets"].append(t_ret)
                    if t_is_zt:
                        s["h1_zt"] += 1

    print("验证窗口：最后 %d 个交易日（%s ~ %s，实际样本日历 %d 天）"
          % (days, min(window), max(window), len(window)))
    print("特征只用 T-1 及之前；标签 = T 日收盘涨跌 / 是否涨停；ST 按普通股限幅（无法识别）")
    print()
    base_zt = (stats["BASE_ALL"]["zt"] / float(stats["BASE_ALL"]["n"])) if stats["BASE_ALL"]["n"] else 0.0
    print("%-20s %9s %9s %9s %9s %10s" % ("规则", "触发", "次日涨停", "涨停率", "vs基线", "次日均值"))
    print("-" * 84)
    out = {}
    for name, _, desc in RULES:
        s = stats[name]
        if not s["n"]:
            print("%-20s %9d %9s %9s %9s %10s" % (name, 0, "-", "-", "-", "-"))
            continue
        zr = s["zt"] / float(s["n"])
        avg = sum(s["rets"]) / len(s["rets"])
        print("%-20s %9d %9d %8.2f%% %8.2fx %9.3f%%" % (
            name, s["n"], s["zt"], zr * 100, (zr / base_zt if base_zt else 0), avg * 100))
        h1 = (s["h1_zt"] / float(s["h1_n"])) if s["h1_n"] else 0.0
        h2 = (s["h2_zt"] / float(s["h2_n"])) if s["h2_n"] else 0.0
        a1 = (sum(s["h1_rets"]) / len(s["h1_rets"])) if s["h1_rets"] else 0.0
        a2 = (sum(s["h2_rets"]) / len(s["h2_rets"])) if s["h2_rets"] else 0.0
        out[name] = {"desc": desc, "n": s["n"], "zt_rate": zr, "ret_avg": avg,
                     "h1": {"n": s["h1_n"], "zt_rate": h1, "ret_avg": a1},
                     "h2": {"n": s["h2_n"], "zt_rate": h2, "ret_avg": a2}}
    print()
    print("OOS 稳定性（前半年 vs 后半年，各自独立统计，不是同一批样本的两半随机切）")
    print("%-20s %16s %16s" % ("规则", "前半段", "后半段"))
    print("-" * 60)
    for name, _, _ in RULES:
        if name not in out:
            continue
        o = out[name]
        print("%-20s n=%-6d %.2f%%/%+.2f%%   n=%-6d %.2f%%/%+.2f%%" % (
            name, o["h1"]["n"], o["h1"]["zt_rate"] * 100, o["h1"]["ret_avg"] * 100,
            o["h2"]["n"], o["h2"]["zt_rate"] * 100, o["h2"]["ret_avg"] * 100))
    print()
    for name, _, desc in RULES:
        print("  %-20s %s" % (name, desc))
    # offset>0 是 HOLDOUT 窗口，必须与主窗口分开落盘，否则会互相覆盖导致无法复核
    fn_out = "prelaunch_validate%s.json" % (("_holdout_off%d" % offset) if offset else "")
    json.dump({"days": days, "offset": offset, "window": [min(window), max(window)],
               "rules": out},
              open(os.path.join(CACHE, fn_out), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n落盘: backtest_cache/%s" % fn_out)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("snapshot")
    p1.add_argument("--as-of", required=True)
    p1.add_argument("--codes", default="002249,002708")
    p2 = sub.add_parser("validate")
    p2.add_argument("--as-of", default=None, help="占位：验证只用 bars 尾部，按 --days 回溯")
    p2.add_argument("--days", default="120")
    p2.add_argument("--offset", default="0",
                    help="再往前推 N 个交易日取窗口；用于与主窗口完全不重叠的 HOLDOUT 验证")
    p3 = sub.add_parser("check-date")
    p3.add_argument("--date", required=True, help="回放日（=T-1，该日收盘后跑规则）")
    p3.add_argument("--codes", default="002249,002708", help="探针代码，用于标出是否被选中")
    p3.add_argument("--out", default=None, help="把各规则命中的全量名单落到指定 json")
    a = ap.parse_args()
    if a.cmd == "snapshot":
        sys.exit(cmd_snapshot(a))
    if a.cmd == "validate":
        sys.exit(cmd_validate(a))
    if a.cmd == "check-date":
        sys.exit(cmd_check_date(a))
    ap.print_help()
