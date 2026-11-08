# -*- coding: utf-8 -*-
"""grail_cohort_20261102.py — 反推 2026-11-02 的"圣杯目标群"
================================================================
用户问题：哪些是"上个交易日(10-30)收盘前未跌停未涨停，却今日(11-02)涨停或上涨的"。
这正是系统核心目标(圣杯)的 ex-post 目标群：T-1 非涨跌停 → T 涨停/上涨。
本脚本用本地日线缓存(前复权)逐票计算，输出：
  - 11-02 全市场触板/封住/上涨统计(与用户提供 153/125/28/82% 交叉验证)
  - 目标群 A：10-30 非涨跌停 且 11-02 涨停（首板/圣杯命中）
  - 目标群 B：10-30 非涨跌停 且 11-02 上涨但未涨停（更宽的"次日上涨"目标）
  - 对照：10-30 已涨停 → 11-02 续板/断板（接力结构）
口径严格按交易所：close >= prev*(1+lim)-0.005 判涨停；跌停对称。
"""
from __future__ import annotations
import os
import glob
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", "backtest_cache")
DAILY = os.path.join(CACHE, "daily")
sys.path.insert(0, os.path.join(HERE, ".."))
from evolution_review import limit_of  # 复用涨停判定


def load_closes():
    out = {}
    for f in glob.glob(os.path.join(DAILY, "*.csv")):
        code = os.path.splitext(os.path.basename(f))[0]
        try:
            rows = []
            with open(f, encoding="utf-8", errors="ignore") as fh:
                fh.readline()
                for line in fh:
                    p = line.rstrip("\n").split(",")
                    if len(p) < 3:
                        continue
                    try:
                        rows.append((p[0].strip(), float(p[2])))
                    except Exception:
                        continue
            out[code] = {d: c for d, c in rows}
        except Exception:
            continue
    return out


def is_lup(code, d_prev, d_cur):
    lim = limit_of(code)
    if d_prev is None or d_cur is None or d_prev == 0:
        return False
    return d_cur >= d_prev * (1 + lim) - 0.005


def is_ldn(code, d_prev, d_cur):
    lim = limit_of(code)
    if d_prev is None or d_cur is None or d_prev == 0:
        return False
    return d_cur <= d_prev * (1 - lim) + 0.005


def main():
    closes = load_closes()
    D29, D30, D02 = "2026-10-29", "2026-10-30", "2026-11-02"

    n02 = sum(1 for c in closes if D02 in closes[c])
    touched = 0          # 11-02 曾触板（高>=前收*1.095 近似，主用涨停收盘判定）
    sealed = 0           # 11-02 收涨停
    up = 0               # 11-02 收涨（不含涨停）
    cohortA = []         # 10-30 非涨跌停 & 11-02 涨停
    cohortB = []         # 10-30 非涨跌停 & 11-02 涨但不涨停
    relay_cont = []      # 10-30 涨停 & 11-02 续涨停（接力续板）
    relay_broke = []     # 10-30 涨停 & 11-02 未涨停
    for c, m in closes.items():
        if D02 not in m or D30 not in m or D29 not in m:
            continue
        c30, c02, c29 = m[D30], m[D02], m[D29]
        # 10-30 状态
        lup30 = is_lup(c, c29, c30)
        ldn30 = is_ldn(c, c29, c30)
        norm30 = (not lup30) and (not ldn30)
        # 11-02 状态
        lup02 = is_lup(c, c30, c02)
        rose02 = c02 > c30
        if lup02:
            sealed += 1
        elif rose02:
            up += 1
        if norm30 and lup02:
            cohortA.append((c, (c02 / c30 - 1)))
        elif norm30 and rose02:
            cohortB.append((c, (c02 / c30 - 1)))
        elif lup30 and lup02:
            relay_cont.append((c, (c02 / c30 - 1)))
        elif lup30 and not lup02:
            relay_broke.append((c, (c02 / c30 - 1)))

    print("=== 11-02 全市场（本地缓存口径，n=%d 只有 11-02 数据的票）===" % n02)
    print("  收涨停(封住): %d | 收涨未涨停: %d | 收涨停率(占全部)=%.1f%%"
          % (sealed, up, 100.0 * sealed / n02 if n02 else 0))
    print("  用户提供: 触板153 / 封住125 / 炸板28 / 成功率82% （本地为收盘口径，不含盘中触板数）")
    print()
    print("=== 圣杯目标群 A：10-30 非涨跌停 & 11-02 涨停（首板命中）===")
    print("  数量: %d" % len(cohortA))
    for c, r in sorted(cohortA, key=lambda x: -x[1])[:25]:
        print("    %s  +%.2f%%" % (c, r * 100))
    print()
    print("=== 目标群 B：10-30 非涨跌停 & 11-02 上涨但未涨停 ===")
    print("  数量: %d" % len(cohortB))
    print()
    print("=== 接力结构：10-30 已涨停 → 11-02 ===")
    print("  续涨停(连板): %d | 断板(未续涨): %d" % (len(relay_cont), len(relay_broke)))
    for c, r in sorted(relay_broke, key=lambda x: x[1])[:15]:
        print("    断板 %s  %+.2f%%" % (c, r * 100))
    print()
    print("=== 汇总 ===")
    print("  目标群 A+B 合计(10-30 非涨跌停 & 11-02 上涨): %d" % (len(cohortA) + len(cohortB)))
    print("  → 这正是 14:50 需在 T-1 选出、T 日验证的目标规模")


if __name__ == "__main__":
    main()
