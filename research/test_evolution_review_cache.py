# -*- coding: utf-8 -*-
"""test_evolution_review_cache.py — evolution_review 缓存健壮性 + 涨停口径回归锁

覆盖 2026-11-06 修复的三类缺陷：
  1) 主缓存原子写失败被静默吞掉（5225 只完整结果留在 .tmp，主缓存停在 2000 键）
  2) 残留 .tmp / partial 无自愈，每轮全量重解 14 分钟
  3) 涨停判定用比例阈值而非交易所口径 is_limit_up，污染「可买子集」与 grail_hit

用法：
  PYTHONPATH=<Claw 根> .venv/Scripts/python.exe research/test_evolution_review_cache.py
"""
from __future__ import annotations
import os
import sys
import pickle
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import evolution_review as er
from research.board_limit import is_limit_up, board_limit_pct

PASSED = []


def chk(name, cond, detail=""):
    line = ("PASS | " if cond else "FAIL | ") + name
    if detail:
        line += " | " + detail
    print(line)
    if not cond:
        raise AssertionError(name + " :: " + detail)
    PASSED.append(name)


def test_limit_up_caliber():
    """涨停判定必须走交易所口径（涨停价两位小数 ROUND_HALF_UP）。"""
    chk("is_limit_up: 10.00→11.00 涨停", is_limit_up("600519", 10.00, 11.00) is True)
    chk("is_limit_up: 10.00→10.95 非涨停", is_limit_up("600519", 10.00, 10.95) is False)
    chk("is_limit_up: 5.54→6.09 低价股真涨停", is_limit_up("601956", 5.54, 6.09) is True)
    old = (10.97 / 10.00 - 1) >= (board_limit_pct("600519") - 0.005)
    chk("旧比例阈值把 +9.7% 误判为涨停", old is True,
        "old=%s，交易所口径为 False" % old)
    chk("is_limit_up: 创业板 10.00→12.00 涨停", is_limit_up("300750", 10.00, 12.00) is True)
    chk("is_limit_up: 创业板 10.00→11.00 非涨停", is_limit_up("300750", 10.00, 11.00) is False)


def test_parse_one_caliber():
    """_parse_one 返回 4 元组，且 zt / zt_t1 走 is_limit_up。"""
    d = tempfile.mkdtemp(prefix="er_parse_test_")
    f = os.path.join(d, "600519.csv")
    # 列序 日期,开盘,收盘,最高,最低,成交量（非 OHLC）
    rows = [
        ("2026-11-02", 10.00),
        ("2026-11-03", 10.97),   # +9.7%，旧口径误判涨停，交易所口径非涨停
        ("2026-11-04", 11.00),
        ("2026-11-05", 12.10),   # 相对 11.00 +10.0% → 真涨停
    ]
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("日期,开盘,收盘,最高,最低,成交量\n")
        for dt, c in rows:
            fh.write("%s,%.2f,%.2f,%.2f,%.2f,1000\n" % (dt, c, c, c, c))
    res = er._parse_one(f)
    chk("_parse_one 返回 4 元组 (code,t1,zt,zt_t1)",
        isinstance(res, tuple) and len(res) == 4)
    code, t1, zt, zt_t1 = res
    chk("_parse_one code 取文件名主干", code == "600519", str(code))
    chk("zt: +9.7% 不再被误判为涨停", zt.get("2026-11-03") is False,
        str(zt.get("2026-11-03")))
    chk("zt: 11.00→12.10 真涨停判为涨停", zt.get("2026-11-05") is True,
        str(zt.get("2026-11-05")))
    chk("zt_t1: 11-04 的次日(11-05)涨停为 True", zt_t1.get("2026-11-04") is True)
    chk("zt_t1: 11-02 的次日(11-03)涨停为 False", zt_t1.get("2026-11-02") is False)
    chk("t1: 11-02→11-03 收益 = 9.7%",
        abs(t1.get("2026-11-02", 0) - 0.097) < 1e-9,
        str(t1.get("2026-11-02")))


def test_payload_compat():
    d = tempfile.mkdtemp(prefix="er_payload_test_")
    p = os.path.join(d, "c.pkl")
    with open(p, "wb") as fh:
        pickle.dump(({"a": 1}, {"x": {}}, {"x": {}}), fh)
    old = er._load_payload(p)
    chk("_load_payload 兼容旧三元组 → schema=1",
        old["schema"] == 1 and "zt_t1" in old)
    new = er._new_payload()
    with open(p, "wb") as fh:
        pickle.dump(new, fh)
    got = er._load_payload(p)
    chk("_load_payload 读回新 dict → schema=2",
        got["schema"] == er.CACHE_SCHEMA)
    chk("CACHE_SCHEMA == 2（v1 比例阈值缓存必须失效重建）",
        er.CACHE_SCHEMA == 2)


def test_atomic_write_and_recover():
    d = tempfile.mkdtemp(prefix="er_atomic_test_")
    main = os.path.join(d, "_daily_t1map.pkl")
    part = os.path.join(d, "_daily_t1map.partial.pkl")
    old_main, old_part = er.CACHE_PKL, er.PARTIAL_PKL
    er.CACHE_PKL, er.PARTIAL_PKL = main, part
    try:
        p = er._new_payload()
        p["t1map"]["600519"] = {"2026-11-06": 0.01}
        with open(part, "wb") as fh:
            pickle.dump(p, fh)
        ok = er._atomic_write_cache(p)
        chk("_atomic_write_cache 返回 True", ok is True)
        chk("原子写后主缓存可读且 schema=2",
            er._load_payload(main)["schema"] == 2)
        chk("原子写后清理 partial", not os.path.exists(part))
        chk("原子写后无残留 .tmp", not os.path.exists(main + ".tmp"))

        # 模拟上次 os.replace 失败：主缓存残缺 + 完整 .tmp 残留
        small = er._new_payload()
        small["t1map"]["000001"] = {}
        with open(main, "wb") as fh:
            pickle.dump(small, fh)
        full = er._new_payload()
        for i in range(10):
            full["t1map"]["%06d" % i] = {}
        with open(main + ".tmp", "wb") as fh:
            pickle.dump(full, fh)
        er._recover_stale_tmp()
        n_keys = len(er._load_payload(main)["t1map"])
        chk("_recover_stale_tmp 把完整 .tmp 提升为主缓存",
            n_keys == 10, "keys=%d" % n_keys)
        chk("自愈后 .tmp 已清理", not os.path.exists(main + ".tmp"))

        # 过期 tmp 应清理而非覆盖更完整主缓存
        with open(main + ".tmp", "wb") as fh:
            pickle.dump(er._new_payload(), fh)
        er._recover_stale_tmp()
        chk("过期 .tmp 被清理且不覆盖主缓存",
            not os.path.exists(main + ".tmp")
            and len(er._load_payload(main)["t1map"]) == 10)
    finally:
        er.CACHE_PKL, er.PARTIAL_PKL = old_main, old_part


def test_core_grail_filter():
    """核心口径必须只保留 HOLD5_GRAIL 且交付日未涨停的样本。"""
    hs = [
        {"sub": "LIANBAN", "was_limit_up": True, "hit": False},
        {"sub": "HOLD5_GRAIL", "was_limit_up": True, "hit": False},
        {"sub": "HOLD5_GRAIL", "was_limit_up": False, "hit": True},
        {"sub": "HOLD5_GRAIL", "was_limit_up": False, "hit": False},
        {"sub": "PRE_BREAKOUT", "was_limit_up": False, "hit": False},
        {"sub": "EVENT_CATALYST", "was_limit_up": False, "hit": False},
    ]
    core, exc = er.core_grail_records(hs)
    chk("核心口径只保留 HOLD5_GRAIL 非涨停",
        len(core) == 2
        and all(r["sub"] == "HOLD5_GRAIL" and not r["was_limit_up"] for r in core),
        "n=%d" % len(core))
    chk("核心口径命中数 = 1/2", sum(1 for r in core if r["hit"]) == 1)
    chk("排除分解计数正确",
        exc["not_grail_sub"] == 3 and exc["already_limit_up"] == 1, str(exc))


def test_grail_success():
    """核心业务目标完整代理：按需读 CSV，不动缓存，判定严格可复核。"""
    import shutil
    d = tempfile.mkdtemp(prefix="er_fwd_test_")
    old_daily = er.DAILY
    er.DAILY = d
    try:
        def w(code, rows):
            with open(os.path.join(d, code + ".csv"), "w", encoding="utf-8") as fh:
                fh.write("日期,开盘,收盘,最高,最低,成交量\n")
                for dt, o, c in rows:
                    fh.write("%s,%.2f,%.2f,%.2f,%.2f,1000\n" % (dt, o, c, c, c))
        # T=11-02 收 10.00；T+1 高开且收涨；T+2/T+3 连续上涨 → success
        w("000001", [("2026-11-02", 10.00, 10.00), ("2026-11-03", 10.50, 11.00),
                     ("2026-11-04", 11.00, 11.50), ("2026-11-05", 11.50, 12.00)])
        # T+2 下跌 → followthrough=False → success=False
        w("000002", [("2026-11-02", 10.00, 10.00), ("2026-11-03", 10.50, 11.00),
                     ("2026-11-04", 11.00, 10.50), ("2026-11-05", 10.50, 11.00)])
        # 前向不足 3 日 → no_forward_data，绝不臆判
        w("000003", [("2026-11-04", 10.00, 10.00), ("2026-11-05", 10.20, 10.30),
                     ("2026-11-06", 10.30, 10.40)])
        fp = er.forward_path("000001", "2026-11-02")
        chk("forward_path 读 T+1 开盘收益 = +5%",
            fp is not None and abs(fp["t1_open_ret"] - 0.05) < 1e-9, str(fp))
        chk("forward_path entry_profit=True", fp["entry_profit"] is True)
        chk("forward_path followthrough=True", fp["followthrough"] is True)
        chk("forward_path 前向不足返回 None",
            er.forward_path("000003", "2026-11-04") is None)
        core = [
            {"code": "000001", "date": "2026-11-02", "hit": True, "channel_verified": True},
            {"code": "000002", "date": "2026-11-02", "hit": False, "channel_verified": True},
            {"code": "000003", "date": "2026-11-04", "hit": False, "channel_verified": False},
        ]
        st, recs = er.grail_success_records(core)
        chk("grail_success n=3 / n_evaluable=2 / n_no_forward=1",
            st["n"] == 3 and st["n_evaluable"] == 2 and st["n_no_forward"] == 1, str(st))
        chk("grail_success entry_profit_n=2", st["entry_profit_n"] == 2, str(st))
        chk("grail_success followthrough_n=1", st["followthrough_n"] == 1, str(st))
        chk("grail_success success_n=1 rate=0.5",
            st["success_n"] == 1 and abs(st["success_rate"] - 0.5) < 1e-9, str(st))
        chk("grail_success 定义写死在 definition 中",
            "followthrough" in st["definition"] and "entry_profit" in st["definition"])
        chk("grail_success 声明不构成 edge / 不得用于门控",
            "不构成 validated edge" in st["note"] and "PROMOTE" in st["note"])
    finally:
        er.DAILY = old_daily
        shutil.rmtree(d, ignore_errors=True)


def test_verified_only():
    """严格送达真值子口径：只认 channel_verified=True，收紧不放宽。"""
    core = [
        {"hit": True, "channel_verified": True},
        {"hit": False, "channel_verified": True},
        {"hit": True, "channel_verified": False},
        {"hit": False, "channel_verified": None},
    ]
    vo = er.verified_only_stats(core)
    chk("verified_only n=2", vo["n"] == 2, str(vo))
    chk("verified_only n_limitup=1 / hit_rate=0.5",
        vo["n_limitup"] == 1 and abs(vo["hit_rate"] - 0.5) < 1e-9, str(vo))
    chk("verified_only 剔除 2 条未验证", vo["excluded_unverified"] == 2, str(vo))
    chk("verified_only 声明为收紧口径", "收紧" in vo["note"])


if __name__ == "__main__":
    test_limit_up_caliber()
    test_parse_one_caliber()
    test_payload_compat()
    test_atomic_write_and_recover()
    test_core_grail_filter()
    test_grail_success()
    test_verified_only()
    print("=== ALL PASS (%d checks) ===" % len(PASSED))
