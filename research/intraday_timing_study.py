# -*- coding: utf-8 -*-
"""
research/intraday_timing_study.py — 盘中提前买入的代价，值不值得做「全时段」
================================================================================

问题：把选股从 14:32 提前到 10:00/10:30/13:30，是多赚还是多亏？

核心恒等式（这是本研究的全部关键）：
    持有到 T+1 开盘的收益  =  next_open / entry_price - 1
    而 next_open 是外生常量，所以不同入场时点的差异 **100% 来自入场价**：
        diff(h) = ret_to_nextopen(h) - ret_to_nextopen(close)
                = close_T / price_h - 1
    → 入场价 < 收盘价 则早买占优，入场价 > 收盘价 则早买吃亏。

然后按当日**最终**涨跌分组，就能看出早买到底赚在谁身上、亏在谁身上：
  - 最终大涨/涨停的票：盘中价低于收盘价 → 早买占优
  - 最终下跌的票：盘中价高于收盘价 → 早买吃亏
所以「早买好不好」= 「你盘中挑中的票，最终涨的比例有多高」。这把问题从"时点"转成"精度"。

这正是本项目一直在回避的那道题：收盘模型的精度已经很弱（gapup AUC 0.55），
盘中信息更少，精度只会更低。本脚本把"精度需要多高才不亏"算出来。

数据：backtest_cache/minute_5m/*.json（300 只 × 约 32 个交易日，2026-08-25 起）
     + backtest_cache/daily/*.csv（T 收盘 / T+1 开盘 / 前收）

输出：research/intraday_timing_report.md + .json

run:
  .venv/Scripts/python.exe -m research.intraday_timing_study
"""
from __future__ import annotations
import os, glob, json
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

CMAP = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "vol", "date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "vol"}

# 检查点（A 股交易时段；11:30/13:00 边界跳过）
CHECKPOINTS = ["10:00", "10:30", "11:00", "13:30", "14:00", "14:30", "14:50", "15:00"]

TRADING_MIN_PER_DAY = 48  # 5min bar × 48 = 4 小时


def _proj():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _limit_pct(code):
    return board_limit_pct(code)



def _load_daily(code):
    p = os.path.join(_proj(), "backtest_cache", "daily", f"{code}.csv")
    if not os.path.exists(p):
        return None
    try:
        df = pd.read_csv(p)
        df = df.rename(columns={c: CMAP.get(str(c).replace("\ufeff", "").strip(), c)
                                for c in df.columns})
        for c in ("open", "close", "high", "low", "vol"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        df = df.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
        df["next_open"] = df["open"].shift(-1)
        df["prev_close"] = df["close"].shift(1)
        return df.set_index("date")
    except Exception:
        return None


def _day_prices(bars_of_day, hhmm):
    """取 <= hhmm 的最后一根 5min bar 的收盘价；无则 None。"""
    ok = [b for b in bars_of_day if b["day"][11:16] <= hhmm]
    if not ok:
        return None
    try:
        return float(ok[-1]["close"])
    except Exception:
        return None


def build_records(max_files=None):
    files = sorted(glob.glob(os.path.join(_proj(), "backtest_cache", "minute_5m", "*.json")))
    if not files:
        raise SystemExit("无 5 分钟数据")
    if max_files:
        files = files[:max_files]
    recs, skipped = [], 0
    for f in files:
        code = os.path.basename(f)[:-5].zfill(6)
        try:
            bars = json.load(open(f, encoding="utf-8"))
        except Exception:
            skipped += 1
            continue
        daily = _load_daily(code)
        if daily is None:
            skipped += 1
            continue
        by_day = {}
        for b in bars:
            d = str(b.get("day", ""))[:10]
            if d:
                by_day.setdefault(d, []).append(b)
        lim = _limit_pct(code)
        for d, bs in by_day.items():
            if len(bs) < TRADING_MIN_PER_DAY * 0.5:   # 半天以下的残缺日跳过
                continue
            if d not in daily.index:
                continue
            row = daily.loc[d]
            close_T, prev_c, next_o = row["close"], row["prev_close"], row["next_open"]
            if pd.isna(close_T) or pd.isna(prev_c) or pd.isna(next_o):
                continue
            rec = {"code": code, "date": d,
                   "close_T": float(close_T), "prev_close": float(prev_c),
                   "next_open": float(next_o),
                   "day_ret": float(close_T / prev_c - 1.0)}
            got = False
            for h in CHECKPOINTS:
                p = _day_prices(bs, h)
                rec["px_" + h] = p
                if p:
                    got = True
            if got:
                recs.append(rec)
    return pd.DataFrame(recs), skipped, len(files)


def analyse(df):
    """计算各时点相对 15:00 收盘入场的差异，并按最终涨跌分组。"""
    out = {}
    df = df.copy()
    # 按最终涨跌分组
    def bucket(r):
        if r >= 0.095:
            return "D 最终涨停(≥9.5%)"
        if r >= 0.05:
            return "C 最终大涨(5~9.5%)"
        if r >= 0.0:
            return "B 最终小涨(0~5%)"
        return "A 最终下跌(<0)"
    df["bucket"] = df["day_ret"].apply(bucket)
    order = ["A 最终下跌(<0)", "B 最终小涨(0~5%)", "C 最终大涨(5~9.5%)", "D 最终涨停(≥9.5%)"]

    rows = []
    for h in CHECKPOINTS:
        col = "px_" + h
        d = df.dropna(subset=[col, "close_T", "next_open"]).copy()
        if len(d) < 50:
            continue
        d["ret_entry"] = d["next_open"] / d[col] - 1.0          # 该时点买入 → T+1 开盘
        d["ret_close"] = d["next_open"] / d["close_T"] - 1.0    # 收盘买入 → T+1 开盘
        d["diff"] = d["ret_entry"] - d["ret_close"]             # = close_T/px_h - 1
        rec = {"checkpoint": h, "n": int(len(d)),
               "entry_premium": round(float((d[col] / d["close_T"] - 1).mean()), 5),
               "ret_entry_mean": round(float(d["ret_entry"].mean()), 5),
               "ret_close_mean": round(float(d["ret_close"].mean()), 5),
               "diff_mean": round(float(d["diff"].mean()), 5),
               "diff_median": round(float(d["diff"].median()), 5),
               "diff_hit": round(float((d["diff"] > 0).mean()), 4),
               "ret_entry_hit": round(float((d["ret_entry"] > 0).mean()), 4)}
        # 分桶
        for bk in order:
            sub = d[d["bucket"] == bk]
            if len(sub) < 20:
                rec["diff_" + bk[0]] = None
                continue
            rec["diff_" + bk[0]] = round(float((sub["next_open"] / sub[col]
                                                - sub["next_open"] / sub["close_T"]).mean()), 5)
        rows.append(rec)
    out["by_checkpoint"] = rows

    # 分桶占比
    cnt = df["bucket"].value_counts()
    out["bucket_share"] = {k: round(float(v / len(df)), 4) for k, v in cnt.items()}

    # ---- 盈亏平衡精度：早买要多准才不亏 ----
    # 在 14:30 时点：diff>0 的样本（收盘>当时价，即后续还涨）平均赚 x；diff<0 平均亏 y
    eq = []
    for h in ("10:00", "10:30", "13:30", "14:30"):
        col = "px_" + h
        d = df.dropna(subset=[col, "close_T", "next_open"]).copy()
        if len(d) < 50:
            continue
        d["diff"] = d["close_T"] / d[col] - 1.0
        win = d[d["diff"] > 0]["diff"]
        lose = d[d["diff"] <= 0]["diff"]
        if len(win) < 20 or len(lose) < 20:
            continue
        gain = float(win.mean()); loss = float(-lose.mean())
        br = loss / (gain + loss) if (gain + loss) > 0 else None
        eq.append({"checkpoint": h,
                   "p_favorable": round(float(len(win) / len(d)), 4),
                   "avg_gain_if_right": round(gain, 5),
                   "avg_loss_if_wrong": round(loss, 5),
                   "breakeven_hit_rate": round(br, 4) if br else None})
    out["breakeven"] = eq
    return out


def main():
    print("[build] 读取 5 分钟数据 + 日线 ...")
    df, skipped, nfiles = build_records()
    if df.empty:
        raise SystemExit("无可用记录（检查 minute_5m 与 daily 是否同码同日）")
    print(f"[build] 文件 {nfiles}，跳过 {skipped}，记录 {len(df)} 条 stock-day，"
          f"日期 {df['date'].min()}~{df['date'].max()}，股票 {df['code'].nunique()} 只")

    res = analyse(df)
    print("\n=== 各时点买入 → T+1 开盘（相对 15:00 收盘买入的差异）===")
    print(f"{'时点':<7}{'n':>6}{'入场溢价':>10}{'早买收益':>10}{'收盘收益':>10}"
          f"{'差异':>9}{'差异中位':>10}{'占优率':>8}")
    for r in res["by_checkpoint"]:
        print(f"{r['checkpoint']:<7}{r['n']:>6}{r['entry_premium']:>10.4f}"
              f"{r['ret_entry_mean']:>10.4f}{r['ret_close_mean']:>10.4f}"
              f"{r['diff_mean']:>9.4f}{r['diff_median']:>10.4f}{r['diff_hit']:>8.3f}")

    print("\n=== 分桶：早买占优还是吃亏（差异=早买-收盘买）===")
    order = ["A", "B", "C", "D"]
    lab = {"A": "最终下跌", "B": "最终小涨0~5%", "C": "最终大涨5~9.5%", "D": "最终涨停"}
    print(f"{'时点':<7}" + "".join(f"{lab[k]:>16}" for k in order))
    for r in res["by_checkpoint"]:
        cells = []
        for k in order:
            v = r.get("diff_" + k)
            cells.append(f"{v:>16.4f}" if v is not None else f"{'-':>16}")
        print(f"{r['checkpoint']:<7}" + "".join(cells))

    print("\n=== 分桶占比 ===")
    for k in ["A 最终下跌(<0)", "B 最终小涨(0~5%)", "C 最终大涨(5~9.5%)", "D 最终涨停(≥9.5%)"]:
        print(f"  {k}: {res['bucket_share'].get(k, 0)*100:.2f}%")

    print("\n=== 盈亏平衡：早买需要多高的命中率才不亏 ===")
    for e in res["breakeven"]:
        print(f"  {e['checkpoint']}: 有利占比 {e['p_favorable']*100:.1f}% ｜ "
              f"对了平均赚 {e['avg_gain_if_right']*100:.3f}% ｜ 错了平均亏 {e['avg_loss_if_wrong']*100:.3f}% "
              f"→ 盈亏平衡命中率 **{e['breakeven_hit_rate']*100:.1f}%**")

    # ---- 报告 ----
    save(res, df)
    return res


def save(res, df):
    here = os.path.dirname(os.path.abspath(__file__))
    js = {"generated": pd.Timestamp.now().isoformat(timespec="seconds"),
          "n_records": int(len(df)), "n_stocks": int(df["code"].nunique()),
          "date_min": str(df["date"].min()), "date_max": str(df["date"].max()),
          "checkpoints": CHECKPOINTS, **res}
    json.dump(js, open(os.path.join(here, "intraday_timing_report.json"), "w",
                       encoding="utf-8"), ensure_ascii=False, indent=2)

    md = ["# 盘中提前买入的代价 — 全时段可行性实证", "",
          f"- 生成: {js['generated']}",
          f"- 样本: {js['n_records']} 条 stock-day / {js['n_stocks']} 只 / "
          f"{js['date_min']} ~ {js['date_max']}",
          "- 数据: backtest_cache/minute_5m（5 分钟 bar）+ 日线 CSV（T 收盘 / T+1 开盘）", "",
          "## 一、核心恒等式", "",
          "持有到 T+1 开盘的收益 = `next_open / entry_price - 1`。",
          "`next_open` 是外生常量，所以**不同入场时点的差异 100% 来自入场价**：",
          "", "```", "diff(h) = ret(entry@h) - ret(close) = close_T / price_h - 1", "```", "",
          "入场价低于收盘价 → 早买占优；高于收盘价 → 早买吃亏。没有第三种可能。", "",
          "## 二、各时点实测（相对 15:00 收盘入场）", "",
          "| 时点 | n | 入场溢价 | 早买收益 | 收盘收益 | 差异(均值) | 差异(中位) | 占优率 |",
          "|---|---|---|---|---|---|---|---|"]
    for r in res["by_checkpoint"]:
        md.append(f"| {r['checkpoint']} | {r['n']} | {r['entry_premium']:.4f} | "
                  f"{r['ret_entry_mean']:.4f} | {r['ret_close_mean']:.4f} | "
                  f"{r['diff_mean']:.4f} | {r['diff_median']:.4f} | {r['diff_hit']:.3f} |")
    md += ["", "## 三、按当日最终涨跌分组（关键表）", "",
           "| 时点 | 最终下跌 | 最终小涨 0~5% | 最终大涨 5~9.5% | 最终涨停 ≥9.5% |",
           "|---|---|---|---|---|"]
    for r in res["by_checkpoint"]:
        cells = []
        for k in ["A", "B", "C", "D"]:
            v = r.get("diff_" + k)
            cells.append(f"{v:.4f}" if v is not None else "-")
        md.append(f"| {r['checkpoint']} | " + " | ".join(cells) + " |")
    md += ["", "## 四、分桶占比（决定早买期望的关键）", ""]
    for k in ["A 最终下跌(<0)", "B 最终小涨(0~5%)", "C 最终大涨(5~9.5%)", "D 最终涨停(≥9.5%)"]:
        md.append(f"- {k}: **{res['bucket_share'].get(k, 0)*100:.2f}%**")
    md += ["", "## 五、盈亏平衡：早买需要多准才不亏", "",
           "| 时点 | 有利占比 | 对了平均赚 | 错了平均亏 | 盈亏平衡命中率 |",
           "|---|---|---|---|---|"]
    for e in res["breakeven"]:
        md.append(f"| {e['checkpoint']} | {e['p_favorable']*100:.1f}% | "
                  f"{e['avg_gain_if_right']*100:.3f}% | {e['avg_loss_if_wrong']*100:.3f}% | "
                  f"**{e['breakeven_hit_rate']*100:.1f}%** |")
    md += ["", "## 六、结论怎么读", "",
           "这张表把「要不要做全时段」从主观判断变成了算术题：",
           "",
           "1. 早买的额外收益/亏损 **完全取决于入场价 vs 收盘价**，不取决于你几点看盘。",
           "2. 在「最终大涨/涨停」组里早买占优（盘中价 < 收盘价），在「最终下跌」组里早买吃亏。",
           "3. 因此早买的期望 = Σ(各桶占比 × 该桶差异)。**只有当你盘中挑中的票，"
           "最终落在上涨桶的比例足够高时，全时段才有正期望。**",
           "4. 第五节给出明确的盈亏平衡命中率。如果你的盘中筛选精度达不到这个数，"
           "提前买入就是负期望 —— 而盘中信息比收盘少，精度只会更低，不会更高。",
           "",
           "> 注：本样本仅 %d 个交易日 / %d 只，属初步实证，不是定论。"
           "要下结论需先把 minute_5m 扩到全市场 × 1 年以上。" % (
               len(set(df["date"])), df["code"].nunique())]
    open(os.path.join(here, "intraday_timing_report.md"), "w",
         encoding="utf-8").write("\n".join(md) + "\n")
    print(f"\n[write] {os.path.join(here, 'intraday_timing_report.md')}")
    print(f"[write] {os.path.join(here, 'intraday_timing_report.json')}")


if __name__ == "__main__":
    main()
