#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
r1_get_codes.py — 输出 R1 每日监控代码集（去重并集），供自动化取数使用。

并集 = 连板台账(backtest_cache/lianban_watchlist.json) 代码
     ∪ 当前 deny-list(data/position_deny_list.json) 代码
     ∪ 静态回退集群(物理AI/机器人主题)

设计目的：
- 连板台账 = 系统真实交易池，R1 只应检查会被推送的票；
- deny-list 代码并入 = 已 deny 的票持续被复核，避免自动重建时误清空（TTL 由14日窗口实现）；
- 回退集群 = 台账缺失时的兜底，保证至少有物理AI主题覆盖。

输出：JSON 数组（sorted）到 stdout，供 agent 解析后逐码经 TDX MCP 取公告。
"""
import os
import json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LWB = os.path.join(HERE, "backtest_cache", "lianban_watchlist.json")
DENY = os.path.join(HERE, "data", "position_deny_list.json")
FALLBACK = ["603028", "003021", "603528", "603728", "600838", "000678", "603278"]


def _load_codes(path, key=None):
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    if isinstance(d, dict):
        v = d.get(key) if key else d.get("codes")
        if v is None:
            return []
    else:
        v = d
    out = []
    for c in v:
        code = str(c.get("code") if isinstance(c, dict) else c).strip()
        if code:
            out.append(code)
    return out


def main():
    codes = (set(_load_codes(LWB, "codes"))
             | set(_load_codes(DENY))
             | set(FALLBACK))
    print(json.dumps(sorted(codes), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
