#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""executability 回归：锁死 2026-10-27 三处「发现了但买不到 / 价格是错的」的修复。

背景（当日实测）：
  · 14:44 主引擎推送「603028 赛福天 4板 封板不可买」—— 一字板，全天买不到，占掉一个名额；
  · 000678 襄阳轴承 4 连板收盘，被引擎判「今日未涨停」淘汰（用 11:59 旧价 15.75，实际 14:35 已 16.01）；
  · 推送里写「买14.55 卖16.01」，而当时价已是 16.01 —— 买入价回退成了 T-1 收盘，不可能成交。

三处修复对应四个用例：
  A. 报价源按文件新旧合并，新快照必须覆盖旧价（原来按元组顺序，先命中的永不覆盖）
  B. 现价=涨停价时不能称"可买"，必须区分一字板 / 涨停价排队
  C. 推送正文必须呈现 fill_risk，且买入价用现价而非 T-1 收盘
  D. 同板数时非一字板优先（原来一字板排前面，等于优先推买不到的票）

用法：.venv/Scripts/python.exe research/_regress_executability.py
退出码 0 = 全部通过。
"""
import os
import sys
import json
import importlib.util
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def case_a_snap_newest_wins(utp, tmp):
    """A：报价源合并必须以「谁更新」为准，不能以文件名在元组里的位置为准。"""
    tail = os.path.join(tmp, "tail_raw")
    os.makedirs(tail)
    d = "2026-10-27"
    old = os.path.join(tail, d + ".json")
    new = os.path.join(tail, d + "_quotes.json")
    json.dump({"data": [{"sec_code": "000678", "price": 15.75, "prev_close": 14.55}]},
              open(old, "w", encoding="utf-8"))
    json.dump({"data": [{"sec_code": "000678", "price": 16.01, "prev_close": 14.55}]},
              open(new, "w", encoding="utf-8"))
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    _orig = utp.HERE
    utp.HERE = tmp
    try:
        snap = utp._load_snap(d) or {}
        got = (snap.get("000678") or {}).get("price")
    finally:
        utp.HERE = _orig
    ok = (got == 16.01)
    print("[A] 新快照覆盖旧价             : %s  取到 price=%s（期望 16.01，旧值 15.75 不得胜出）"
          % ("PASS" if ok else "FAIL", got))
    return ok


def case_b_classify(eb):
    """B：现价已在涨停价上时，"可买"这个词不许出现。"""
    rec = {"code": "000678", "board_len": 3}
    prev = 14.55
    zt = round(prev * 1.1, 2)          # 16.01

    # 一字板：全天最低就贴在涨停价
    s1, b1, buy1, note1 = eb.classify_lianban(rec, {"price": zt, "prev_close": prev,
                                                    "low": zt, "high": zt})
    # 盘中拉板后封住：low 远低于涨停价
    s2, b2, buy2, note2 = eb.classify_lianban(rec, {"price": zt, "prev_close": prev,
                                                    "low": 14.68, "high": zt})

    checks = [
        ("一字板 → 不可买", buy1 is False),
        ("一字板 → 提示含'一字'", "一字" in note1),
        ("盘中封板 → 提示含'排队'", "排队" in note2),
        ("盘中封板 → 不出现'可买'", "可买" not in note2),
        ("盘中封板 → 明确非确定买点", "非确定" in note2),
    ]
    ok = True
    print("[B] 涨停价上的可执行性语义     :")
    for label, good in checks:
        print("      %-28s %s" % (label, "PASS" if good else "FAIL"))
        ok = ok and good
    return ok


def case_c_fmt(utp):
    """C：正文必须写清成交流程风险，且买入价用现价（不是 T-1 收盘）。"""
    p = {"code": "600838", "name": "上海九百", "sub": "LIANBAN", "board_len": 5,
         "sealed": False, "buyable": False,
         "fill_risk": "涨停价排队，成交取决于封单量，非确定买点",
         "price": 13.31, "chg": 10.0, "exit": "T+1收盘(hold_days=1, 不设机械trailing)",
         "exec_note": "3-4板区间最优", "verify_tier": "A", "today_state": "ZT_TODAY"}
    line = utp._fmt(p)
    checks = [
        ("正文含'排队'", "排队" in line),
        ("不再出现'未封可买'", "未封可买" not in line),
        ("买入价=现价 13.31", "买13.31" in line),
        ("卖出价=次日涨停 14.64", "卖14.64" in line),
    ]
    ok = True
    print("[C] 推送正文的可执行性         :")
    for label, good in checks:
        print("      %-28s %s" % (label, "PASS" if good else "FAIL"))
        ok = ok and good
    if not ok:
        print("      实得: %s" % line)
    return ok


def _write_daily(path, seq):
    """seq: [(date, open, close, high, low, vol), ...]"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in seq:
            f.write("%s,%.2f,%.2f,%.2f,%.2f,%d\n" % r)


def case_d_sort(utp, tmp):
    """D：同板数时，非一字板（可排队）必须排在一字板（买不到）前面。"""
    daily = os.path.join(tmp, "backtest_cache", "daily")
    # 三天连续涨停：12.03 -> 13.23 -> 14.55 -> 16.01，每步 ≈+9.98%
    base = [("2026-10-22", 12.03, 12.03, 12.03, 11.90, 1000),
            ("2026-10-23", 12.10, 13.23, 13.23, 12.05, 1000),
            ("2026-10-24", 13.30, 14.55, 14.55, 13.25, 1000)]
    # 000002：一字板（low = 涨停价 16.01）
    _write_daily(os.path.join(daily, "000002.csv"),
                 base + [("2026-10-27", 16.01, 16.01, 16.01, 16.01, 1000)])
    # 000003：盘中拉板后封住（low = 14.68）
    _write_daily(os.path.join(daily, "000003.csv"),
                 base + [("2026-10-27", 14.70, 16.01, 16.01, 14.68, 1000)])

    _orig_here, _orig_cache = utp.HERE, utp._BARS_CACHE
    utp.HERE = tmp
    utp._BARS_CACHE = {}
    try:
        res = utp.lianban_scan("2026-10-27", {}, universe=["000002", "000003"])
    finally:
        utp.HERE = _orig_here
        utp._BARS_CACHE = _orig_cache

    order = [r["code"] for r in res]
    ok = len(res) == 2 and order[0] == "000003"
    print("[D] 非一字板排序优先           : %s  顺序=%s（期望 000003 在前）"
          % ("PASS" if ok else "FAIL", order))
    return ok


def main():
    utp = _load("utp_regress", os.path.join(ROOT, "unified_tail_pick.py"))
    eb = _load("eb_regress", os.path.join(ROOT, "edge_brief.py"))
    tmp = tempfile.mkdtemp(prefix="regress_exec_")
    results = []
    try:
        results.append(case_a_snap_newest_wins(utp, os.path.join(tmp, "a")))
        results.append(case_b_classify(eb))
        results.append(case_c_fmt(utp))
        results.append(case_d_sort(utp, os.path.join(tmp, "d")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n_pass = sum(1 for r in results if r)
    print()
    print("=== %d/%d PASS ===" % (n_pass, len(results)))
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
