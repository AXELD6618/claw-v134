#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
reversal7 选股标注 + 仓位提示模块（供推送）
========================================================================
对「连跌≥7日未跌停」信号，生成推送所需的完整标注：
  ① 尾盘可买价位区间
  ② 次日卖出价位（止盈/止损目标价，由 T 收盘推导，非预测）
  ③ 整体持有策略（极短 scalp）
  ④ 买入/卖出仓位提示（绑定左尾风险与风险预算）

⚠️ 重要：本信号经全市场 OOS 公平对照 **未过验收**（信号 scalp PF 1.19 < 随机 scalp PF 1.63，
   PF比 0.732 < 1，命中倍数 1.03 < 1.15）。本模块仅交付"标注格式/模板"，
   产出的标的默认带 UNVALIDATED 旗标，**不自动推送、不进 buy_score**。

用法:
  python research/reversal7_annotate.py            # 扫描最近交易日信号并出标注
  python research/reversal7_annotate.py --date 2026-10-20
"""
import os, sys, json, argparse, glob, math
from collections import deque

BASE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(BASE, "..", "backtest_cache", "daily")
TP = 0.015          # 次日盘中止盈 +1.5%
SL = 0.020          # 次日盘中止损 -2.0%
RISK_PER_TRADE = 0.005   # 单信号最大账户亏损预算 0.5%
MAX_SINGLE = 0.15        # 单信号仓位上限 15%
MAX_TOTAL = 0.30         # 日内 scalp 总暴露上限 30%
STREAK_MIN = 7


def thr_of(code):
    return 19.8 if code.startswith(("300", "301", "688", "689")) else 9.8


def load_tail(path, k=16):
    """只读尾部 k 行（足够判断近期连跌），快。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = deque(f, maxlen=k)
    except Exception:
        return None
    rows = []
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("日期"):
            continue
        p = ln.split(",")
        if len(p) < 6:
            continue
        try:
            rows.append((p[0], float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[5])))
        except Exception:
            continue
    return rows  # (date, open, close, high, low, vol)


def screen_recent():
    """扫描最近交易日里 streak≥7 的信号（尾部读取）。返回 list of dict。"""
    files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
    files = [f for f in files if not f.endswith(".bak")]
    out = []
    for f in files:
        code = os.path.basename(f)[:6]
        rows = load_tail(f)
        if not rows or len(rows) < STREAK_MIN + 1:
            continue
        dates = [int(r[0].replace("-", "")) for r in rows]
        closes = [r[2] for r in rows]
        ret = [0.0] * len(rows)
        for i in range(1, len(rows)):
            ret[i] = closes[i] / closes[i - 1] - 1.0 if closes[i - 1] else 0.0
        # 连跌 streak
        streak = [0] * len(rows)
        for i in range(1, len(rows)):
            if ret[i] < 0:
                streak[i] = streak[i - 1] + 1
            else:
                streak[i] = 0
        last_date = dates[-1]
        for i in range(len(rows)):
            if streak[i] >= STREAK_MIN and ret[i] < 0:
                if dates[i] < last_date - 15:   # 只看最近 ~15 天
                    continue
                if ret[i] <= -thr_of(code) / 100.0 * 0.999:
                    continue
                out.append(dict(
                    code=code, date=rows[i][0], close=closes[i],
                    open=rows[i][1], high=rows[i][3], low=rows[i][4],
                    streak=streak[i],
                    name=_name(code)))
    # 每只票仅保留最新一条信号（连跌过程中每天都会触发，取末日）
    best = {}
    for s in out:
        if s["code"] not in best or int(s["date"].replace("-", "")) > int(best[s["code"]]["date"].replace("-", "")):
            best[s["code"]] = s
    return list(best.values())


def _name(code):
    # 名称留空，推送时由调用方补全；避免每只重复 import 拖慢扫描
    return ""


def annotate(sig, n_signals):
    """把单个信号转成标注 dict。"""
    cT = sig["close"]
    buy_lo = round(cT * (1 - 0.01), 3)        # 尾盘回踩低吸下沿 -1%
    buy_hi = round(cT * 1.001, 3)             # 基本以收盘价附近为上限
    tp_price = round(cT * (1 + TP), 3)        # 次日止盈价
    sl_price = round(cT * (1 - SL), 3)        # 次日止损价
    # 仓位：风险预算 / 止损距离，封顶 MAX_SINGLE；多信号再平分总暴露
    pos_raw = RISK_PER_TRADE / SL
    per = min(MAX_SINGLE, pos_raw)     # 单信号仓位上限（风险预算÷止损距离，封顶15%）
    return dict(
        code=sig["code"], name=sig.get("name", ""), date=sig["date"],
        streak=sig["streak"], close=cT,
        buy_zone=f"{buy_lo} ~ {buy_hi}（尾盘收盘价附近，回踩不破 {buy_lo} 低吸）",
        sell_tp=f"{tp_price}（次日触 +1.5% 止盈全平）",
        sell_sl=f"{sl_price}（次日触 -2% 止损全平）",
        holding=("极短 scalp：T 尾盘买 → T+1 开盘后冲高即走，"
                 "不持仓过 T+1 收盘；触发止盈/止损或收盘一律清仓"),
        position=(f"单信号 ≤ {per*100:.1f}% 账户权益（风险预算0.5%/笔 ÷ 止损2% 推导，"
                  f"封顶15%）；若同日多标的，总日内暴露 ≤ 30%；买入一次到位，"
                  f"卖出按止盈/止损/收盘三选一全平，不留隔夜敞口"),
        flag="UNVALIDATED·未过OOS公平对照·不自动推送·不进buy_score",
    )


def render_markdown(sigs):
    n = len(sigs)
    lines = []
    lines.append("━" * 60)
    lines.append("超跌反弹(连跌≥7日) 选股标注 · 研究版（非推送）")
    lines.append("━" * 60)
    lines.append(f"⚠️ 本信号全市场 OOS 公平对照 **未过验收**：信号 scalp PF 1.19 < 随机 scalp PF 1.63"
                 f"（PF比 0.732），命中倍数 1.03<1.15。仅展示标注格式，不自动推送。")
    lines.append(f"筛选出信号 {n} 只（最近交易日）。")
    if n == 0:
        lines.append("\n近期无连跌≥7日未跌停标的。")
        return "\n".join(lines)
    for s in sigs:
        a = annotate(s, n)
        lines.append(f"\n【{a['code']} {a['name']}】信号日 {a['date']} 连跌{a['streak']}日 收 {a['close']}")
        lines.append(f"  ① 买入价位（尾盘）：{a['buy_zone']}")
        lines.append(f"  ② 卖出价位（次日）：止盈 {a['sell_tp']} ｜ 止损 {a['sell_sl']}")
        lines.append(f"  ③ 持有策略：{a['holding']}")
        lines.append(f"  ④ 仓位提示：{a['position']}")
        lines.append(f"  🚩 {a['flag']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    sigs = screen_recent()
    if a.date:
        sigs = [s for s in sigs if s["date"] == a.date.replace("-", "")]
    md = render_markdown(sigs)
    print(md)
    # 落盘供审阅
    out = os.path.join(BASE, "..", "backtest_cache", "reversal7_annotated.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
