# -*- coding: utf-8 -*-
"""
research/forward_oos_collector.py — 14:50 采集 → forward_oos 记录；T+1 标注（圣杯前向 OOS 闭环·生产端）
================================================================================
把"实时微观结构 + 龙虎榜"特征在 14:50 采集，写成 forward_oos 的 JSONL 记录；T+1 用日线缓存
回填真实标签（T+1 收益 / 次日涨停 / 同日市场均值），由 forward_oos.eval_records 按 adjudicate_grail
口径诚实评估。绝不降门。

入口：
  --collect  --date YYYYMMDD [--force]   14:50 抓 gtimg 全 universe 快照 → 写记录（MICRO* + LHB_pos + 组合）
  --label    <jsonl>                     回填 T+1 标签 + 同日市场（完成闭环）
  --simulate-lhb <out> [--wstart --wend]  历史代理：扫日线缓存，发 LHB_pos 记录(标签留空) → 证明 loop
  --eval     <jsonl> --base <rate>       评估（转发 forward_oos.eval_records）

微观结构特征见 intraday_microstructure；龙虎榜见 oos_lhb_signal（T-1 上榜=聪明钱）。
组合精度假设：LHB_pos(3.5× 涨停率 lift、负期望彩票型) × MICRO_accum(14:50 强收集) → 把"会涨停的"
从"已跑过"中挑出来，使组合同时有方向性 + 正期望。该假设只能 live + 前向实测验证（历史无微观结构）。
"""
from __future__ import annotations
import os, sys, json, math, time, argparse, urllib.request
from pathlib import Path
import numpy as np
import pandas as pd

import oospf_validator as V
from research.intraday_microstructure import parse_gtimg_line, feature_tags, compute_features
from research.board_limit import board_limit_pct
import research.forward_oos as FO

DAILY = V.DAILY
LHB_DIR = V.CACHE / "f10_cache"
UNIV = V.PROJ / "data" / "intraday_universe.json"
ROOT = V.PROJ


def load_universe(date=None):
    try:
        d = json.load(open(UNIV, encoding="utf-8"))
    except Exception:
        return []
    return [c.get("code") for c in d.get("codes", []) if c.get("code")]


def prefix(code):
    c = code[0]
    if c in ("5", "6", "9"):
        return "sh" + code
    if c in ("8", "4"):
        return "bj" + code
    return "sz" + code


def fetch_gtimg(codes):
    out = {}
    for i in range(0, len(codes), 60):
        batch = codes[i:i + 60]
        url = "https://qt.gtimg.cn/q=" + ",".join(prefix(c) for c in batch)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk")
        except Exception:
            continue
        for line in raw.strip().split(";"):
            if "=" not in line:
                continue
            raw_f = parse_gtimg_line(line)
            if raw_f and raw_f.get("code"):
                out[raw_f["code"]] = raw_f
    return out


def _lhb_map(lhb_dir=LHB_DIR):
    m = {}
    for f in sorted(lhb_dir.glob("lhb_20*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        dt = int(d.get("date"))
        stocks = d.get("stocks", {})
        m[dt] = {c: v / 100.0 for c, v in stocks.items() if isinstance(v, (int, float))}
    return m


def _recent_lhb(lhb_map, date_int, max_gap=4):
    """point-in-time：取 <= date_int 最近且间隔<=max_gap 日历日的龙虎榜快照（周度快照的 relaxed 处理）。"""
    best = None
    for dt in lhb_map:
        if dt <= date_int and (date_int - dt) <= max_gap:
            if best is None or dt > best:
                best = dt
    return best


def emit_for_date(date_int, quotes, out_jsonl, lhb_map=None, force=False):
    """14:50 实时采集：对 universe 每只票解析快照 → 微观结构特征 + 龙虎榜(T-1)标签 → 写记录。
    返回写出记录数。"""
    if lhb_map is None:
        lhb_map = _lhb_map()
    recs = []
    for code, raw in quotes.items():
        if raw is None:
            continue
        bl = board_limit_pct(code)
        limit_price = raw["prev"] * (1 + bl)
        feats = compute_features(raw, limit_price)
        if not feats.get("valid"):
            continue
        is_limit = feats.get("is_limit")
        is_limit_down = raw["price"] <= raw["prev"] * (1 - bl) + 0.005
        if is_limit or is_limit_down:
            continue  # 可买宇宙：未涨停/未跌停
        tags = feature_tags(raw, limit_price)
        # 龙虎榜 booster（T-1 上榜且收益>0）
        lhb_dt = _recent_lhb(lhb_map, date_int)
        lhb_r = lhb_map.get(lhb_dt, {}).get(code) if lhb_dt else None
        lhb_pos = (lhb_r is not None and lhb_r > 0)
        # 写记录（每个激活标签一条；组合单独一条）
        for t in tags:
            if t == "MICRO_valid":
                continue
            recs.append(FO.make_record(date_int, code, t, True, float("nan"), False, float("nan")))
        if lhb_pos:
            recs.append(FO.make_record(date_int, code, "LHB_pos", True, float("nan"), False, float("nan")))
            if "MICRO_accum" in tags:
                recs.append(FO.make_record(date_int, code, "LHB_pos+MICRO_accum", True, float("nan"), False, float("nan")))
            if "MICRO_activebuy" in tags:
                recs.append(FO.make_record(date_int, code, "LHB_pos+MICRO_activebuy", True, float("nan"), False, float("nan")))
    n = FO.append_records(out_jsonl, recs)
    return n


def simulate_lhb_window(wstart, wend, out_jsonl, lhb_dir=LHB_DIR, daily_dir=DAILY):
    """历史代理验证：扫日线缓存，对 T-1 有龙虎榜快照(收益>0) 且 T 可买的股票 → 发 LHB_pos 记录
    （标签留空，交 --label 回填）。证明 采集→标注→评估 闭环。"""
    wstart = int(wstart); wend = int(wend)
    lhb_map = _lhb_map(lhb_dir)
    lhb_dates = sorted(lhb_map.keys())
    recs = []
    files = sorted(f for f in daily_dir.glob("*.csv") if not f.name.endswith(".bak"))
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < V.MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float)
        n = len(close)
        dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        f1 = np.full(n, np.nan); f1[:-1] = close[1:] / close[:-1] - 1.0
        code = f.name[:-4]
        bl = board_limit_pct(code)
        for i in range(1, n - 1):
            di = dates[i]
            if di < wstart or di > wend:
                continue
            if np.isnan(ret[i]) or np.isnan(f1[i]):
                continue
            buyable = (ret[i] < bl) and (ret[i] > -bl)
            if not buyable:
                continue
            dim1 = dates[i - 1]
            lhb_prev = lhb_map.get(dim1)
            if not lhb_prev:
                continue
            r = lhb_prev.get(code)
            if r is None or r <= 0:
                continue
            recs.append(FO.make_record(di, code, "LHB_pos", True, float("nan"), False, float("nan")))
        if (fi + 1) % 1000 == 0:
            print(f"  [sim load] {fi+1}/{len(files)}")
    n = FO.append_records(out_jsonl, recs)
    return n


def label_outcomes(jsonl, daily_dir=DAILY):
    """回填每条记录的 T+1 收益/次日涨停 + 同日市场均值（excess 基准）。改写 jsonl。"""
    recs = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
    need_dates = sorted(set(r["date"] for r in recs))
    # 建 date -> 市场均值（扫日线缓存一次）
    msum = {}; mcnt = {}
    files = sorted(f for f in daily_dir.glob("*.csv") if not f.name.endswith(".bak"))
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < V.MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float)
        n = len(close)
        dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        for i in range(1, n):
            di = dates[i]
            if di in need_dates and not np.isnan(ret[i]):
                msum[di] = msum.get(di, 0.0) + ret[i]
                mcnt[di] = mcnt.get(di, 0) + 1
        if (fi + 1) % 1000 == 0:
            print(f"  [label load] {fi+1}/{len(files)}")
    # 每只票定位 date 行取 T+1
    code_dates = {}
    for r in recs:
        code_dates.setdefault(r["code"], []).append(r)
    # 缓存已加载股票的 date->(close array, dates array)
    loaded = {}
    for code, rlist in code_dates.items():
        p = daily_dir / (code + ".csv")
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        close = df["收盘"].to_numpy(float)
        dint = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        dpos = {d: k for k, d in enumerate(dint)}
        bl = board_limit_pct(code)
        loaded[code] = (close, dpos, bl)
    out = []
    for r in recs:
        code = r["code"]
        if code in loaded:
            close, dpos, bl = loaded[code]
            k = dpos.get(r["date"])
            if k is not None and k + 1 < len(close):
                f1 = close[k + 1] / close[k] - 1.0
                r["f1_ret"] = float(f1)
                r["zt_next"] = bool(f1 >= bl - 0.005)
                mc = mcnt.get(r["date"], 0)
                r["mkt_ret"] = (msum.get(r["date"], 0.0) / mc) if mc > 0 else None
        out.append(r)
    with open(jsonl, "w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    filled = sum(1 for r in out if isinstance(r.get("f1_ret"), float))
    return filled, len(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true", help="14:50 实时采集（需交易日盘中）")
    ap.add_argument("--date", type=int, help="信号日 YYYYMMDD")
    ap.add_argument("--force", action="store_true", help="允许盘后/非交易时段测试（微观结构字段可能失效）")
    ap.add_argument("--label", help="回填 T+1 标签的 jsonl")
    ap.add_argument("--simulate-lhb", dest="sim", help="历史代理输出 jsonl")
    ap.add_argument("--wstart", type=int, default=20260107)
    ap.add_argument("--wend", type=int, default=20260525)
    ap.add_argument("--eval", help="评估 jsonl")
    ap.add_argument("--base", type=float, help="基准可买次日涨停率")
    ap.add_argument("--out", default="backtest_cache/forward_oos_live.jsonl")
    a = ap.parse_args()

    if a.collect:
        date = a.date or int(time.strftime("%Y%m%d"))
        if not a.force:
            hm = time.localtime().tm_hour * 100 + time.localtime().tm_min
            if not (1430 <= hm <= 1500):
                print("非 14:30-15:00；用 --force 强制（盘后微观结构字段可能失效）")
                return
        codes = load_universe(date)
        print(f"[collect] date={date} universe={len(codes)}")
        quotes = fetch_gtimg(codes)
        print(f"[collect] 抓到 {len(quotes)}/{len(codes)}")
        n = emit_for_date(date, quotes, a.out)
        print(f"[collect] 写出记录 {n} → {a.out}（T+1 用 --label 回填）")
    elif a.label:
        t0 = time.time()
        filled, total = label_outcomes(a.label)
        print(f"[label] 回填 {filled}/{total} 条  t={time.time()-t0:.0f}s")
    elif a.sim:
        t0 = time.time()
        n = simulate_lhb_window(a.wstart, a.wend, a.sim)
        print(f"[simulate-lhb] 发 LHB_pos 记录 {n} → {a.sim}  t={time.time()-t0:.0f}s")
        print(f"  下一步：--label {a.sim} 回填 → --eval {a.sim} --base <rate>")
    elif a.eval:
        recs = [json.loads(l) for l in open(a.eval, encoding="utf-8")]
        base = a.base if a.base else float("nan")
        feats = sorted(set(r["feature"] for r in recs))
        print(f"[eval] {a.eval} 特征={feats} 基准率={base*100 if base==base else 'nan'}%")
        for ft in feats:
            r = FO.eval_records(recs, ft, base)
            print(f"  {ft}: n={r['n']} 次日涨停={r['sig_zt']*100 if r['sig_zt']==r['sig_zt'] else 0:.3f}% "
                  f"lift={r['lift']:.3f} 超额vs市={r['excess_vs_mkt']:.2f}bp "
                  f"t市={r['t_vs_mkt']:.2f} t空={r['t_vs_cash']:.2f} PCV={r['purged_cv']:.2f} -> {r['verdict']}")
    else:
        print("用法: --collect --date D [--force] | --label <j> | --simulate-lhb <o> | --eval <j> --base R")


if __name__ == "__main__":
    main()
