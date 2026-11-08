#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""expiry_calendar.py — A股月度交割周判定（纯推导，无外部数据源）
=============================================================================
中国指数期货(IF/IC/IM)与 ETF 期权均在**每月第 3 个周五**到期交割（遇法定假日
顺延至下一交易日）。交割周 = 到期日前 5 个交易日（含到期日）构成的窗口。该窗口
内量化/对冲资金移仓与平仓会放大现货抛压，是用户论述中"月度交割制度与现货交易
机制错配"的可计算代理。

关键修正：必须以**日历第 3 周五**为准，再映射到最近交易日；不能数"第 3 个交易
周五"——例如 2026-10 因国庆假期 10-02(首个周五)非交易日，数交易周五会得到
10-23（错误），正确应为日历第 3 周五 10-16。

数据来源：交易日历由 000001(上证指数)日线 `日期` 列去重升序推导（与
intel_features.py / market_regime.py 同口径，无独立节假日文件）。

用法
----
    from expiry_calendar import is_expiry_week, expiry_fridays, trading_days
    is_expiry_week("2026-10-16")   # -> True（2026-10 第3周五=10-16）
"""
from __future__ import annotations

import os
import bisect
import datetime as _dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(ROOT, "backtest_cache", "daily")

_TD = None          # 排序后的交易日列表 (str, YYYY-MM-DD)
_FRI_SET = None     # 交割周五的交易日索引集合


def trading_days():
    """返回排序后的交易日列表（缓存）。"""
    global _TD
    if _TD is None:
        fp = os.path.join(DAILY, "000001.csv")
        dates = []
        if os.path.exists(fp):
            import csv
            with open(fp, "r", encoding="utf-8") as f:
                r = csv.reader(f)
                next(r, None)
                for row in r:           # 列序: 日期,开盘,收盘,最高,最低,成交量
                    if row and row[0].strip():
                        dates.append(row[0].strip()[:10])
            dates = sorted(set(dates))
        _TD = dates
    return _TD


def _third_calendar_friday(y, m):
    """该月第 3 个日历周五（不依赖交易日历）。"""
    d1 = _dt.date(y, m, 1)
    # 首个周五 = 1 + ((周五(4) - 1号星期几) mod 7)
    first_fri = 1 + ((4 - d1.weekday()) % 7)
    return _dt.date(y, m, first_fri + 14)


def expiry_fridays():
    """返回所有"第3周五"的**交易日**日期列表（缓存）。遇假日顺延至下一交易日。"""
    td = trading_days()
    out = []
    if not td:
        return out
    months = set()
    for d in td:
        y, m, _ = (int(x) for x in d.split("-"))
        months.add((y, m))
    tset = set(td)
    for (y, m) in sorted(months):
        fri = _third_calendar_friday(y, m)
        s = fri.isoformat()
        if s in tset:
            out.append(s)
            continue
        # 顺延至下一交易日
        i = bisect.bisect_left(td, s)
        if i < len(td):
            out.append(td[i])
    return out


def _expiry_week_index_set():
    global _FRI_SET
    if _FRI_SET is None:
        td = trading_days()
        fridays = expiry_fridays()
        s = set()
        for fr in fridays:
            i = bisect.bisect_left(td, fr)
            if i < len(td) and td[i] == fr:
                lo = max(0, i - 4)              # 前 5 个交易日（含到期日）
                for j in range(lo, i + 1):
                    s.add(j)
        _FRI_SET = s
    return _FRI_SET


def is_expiry_week(date: str) -> bool:
    """date(YYYY-MM-DD) 是否落在某月交割周内。"""
    td = trading_days()
    if not td:
        return False
    i = bisect.bisect_left(td, str(date)[:10])
    if i < len(td) and td[i] == str(date)[:10]:
        return i in _expiry_week_index_set()
    return False


if __name__ == "__main__":
    frs = expiry_fridays()
    print("交易日数: %d" % len(trading_days()))
    print("交割周五(末12): %s" % ", ".join(frs[-12:]))
    for d in ["2026-09-18", "2026-10-16", "2026-10-19", "2026-10-12",
              "2026-11-20", "2026-12-18"]:
        print("  %s -> expiry_week=%s" % (d, is_expiry_week(d)))
