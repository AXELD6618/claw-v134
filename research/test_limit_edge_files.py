# -*- coding: utf-8 -*-
"""独立验收锁（2026-11-06）：边界/内联/带前缀幅度逻辑的磁盘态复核。

这些文件顶层有 IO（json.load、行情读取），不能整文件 import；验收方从磁盘读源码、
AST 抽取目标函数、注入 canonical 后隔离 exec，逐向量实跑比对，不采信任何注释或自报 status。

覆盖：
  1) 3 个原 MANUAL 幅度函数 → 板块向量 + ST 向量（主板 5% / 双创 20% / 北交 30%）
  2) 2 个带市场前缀输入函数 → bj/sh/sz 前缀剥离后与 canonical 一致
  3) pref() → 920/921 归北交所（旧版漏判为 sh，行情 URL 会错）
  4) live_capture_pytdx.bars_to_proinfo → 去掉 0.10 兜底后不再把北交所 +20% 误记涨停

运行：.venv/Scripts/python.exe research/test_limit_edge_files.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from research.board_limit import board_limit_pct  # 真值

CODES = ["920491", "921000", "830799", "430047", "688001", "689009",
         "300750", "301269", "600519", "000001", "002594", "900901"]
PREFIXED = ["bj920491", "sh688001", "sz300750", "bj430047", "sh900901", "bj830799"]
ST_CASES = [("600519", "*ST 主板"), ("300750", "ST 创业"),
            ("920491", "ST 北交"), ("689009", "*ST CDR")]


def _funcs_from_disk(rel, names):
    """只读磁盘 + AST 抽取，不执行模块顶层任何语句。"""
    txt = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    tree = ast.parse(txt)
    segs = []
    for node in tree.body:  # 只看顶层函数，避免抓到闭包里的同名 helper
        if isinstance(node, ast.FunctionDef) and node.name in names:
            segs.append(ast.get_source_segment(txt, node))
    ns = {"board_limit_pct": board_limit_pct}
    exec("\n\n".join(segs), ns)
    return txt, ns


def test_delegated_limit_funcs():
    """3 个原 MANUAL + 2 个带前缀函数：板块/ST/前缀向量全对齐 canonical。"""
    two_arg = [
        ("data/_intraday_analyze_20261106.py", "limit_pct"),
        ("research/an_1315_20261102.py", "limit_pct"),
        ("research/diff_1315_20261102.py", "lp"),
    ]
    one_arg = [
        ("data/intraday_fetch_1125.py", "limit_pct"),
        ("data/intraday_push_1125.py", "lp"),
    ]
    checked = 0
    for rel, fname in two_arg + one_arg:
        txt, ns = _funcs_from_disk(rel, {fname})
        fn = ns[fname]
        assert "from research.board_limit import" in txt, "%s 未委托单一真相源" % rel
        assert '("8", "4", "9")' not in txt, "%s 仍含旧内联元组" % rel
        two = (rel, fname) in two_arg
        for c in CODES:
            got = fn(c, "") if two else fn(c)
            assert got == board_limit_pct(c), "%s.%s(%s) 偏离 canonical: %s" % (rel, fname, c, got)
            checked += 1
        if two:
            for c, nm in ST_CASES:
                exp = board_limit_pct(c, is_st=True)
                assert fn(c, nm) == exp, "%s.%s(%s,%s) ST 语义偏离" % (rel, fname, c, nm)
                checked += 1
        else:
            for c in PREFIXED:
                assert fn(c) == board_limit_pct(c[2:]), "%s.%s(%s) 前缀剥离错误" % (rel, fname, c)
                checked += 1
    print("[gate] 5 个手工修复文件：%d 个向量实跑一致" % checked)


def test_pref_bj_92():
    _, ns = _funcs_from_disk("data/intraday_fetch_1125.py", {"pref"})
    pref = ns["pref"]
    assert pref("920491") == "bj920491", "920 号段未归北交所"
    assert pref("830799") == "bj830799"
    assert pref("689009") == "sh689009"
    assert pref("900901") == "sh900901"
    assert pref("300750") == "sz300750"
    print("[gate] pref() 920/921→bj，689/900→sh，创业板→sz")


def test_year_ztday_no_fallback():
    """旧实现 except 兜底 lp=0.10；北交所 +20% 不是涨停，不得误记 YearZTDay。"""
    _, ns = _funcs_from_disk("live_capture_pytdx.py",
                             {"_f", "_ytd_return", "bars_to_proinfo"})
    fn = ns["bars_to_proinfo"]
    bars = [
        {"datetime": "2026-01-05", "close": 10.0},
        {"datetime": "2026-01-06", "close": 12.0},   # +20%：北交所 30% 板下不是涨停
        {"datetime": "2026-01-07", "close": 13.0},   # 相对前收仅 +8.3%
    ]
    out = fn(bars, "920491")
    assert out.get("YearZTDay") == 0, "北交所 +20%% 被误记为涨停: %r" % out.get("YearZTDay")
    bars2 = [
        {"datetime": "2026-01-05", "close": 10.0},
        {"datetime": "2026-01-06", "close": 13.0},   # +30% 真涨停
    ]
    out2 = fn(bars2, "920491")
    assert out2.get("YearZTDay") == 1, "北交所 +30%% 真涨停漏计: %r" % out2.get("YearZTDay")
    bars3 = [
        {"datetime": "2026-01-05", "close": 10.0},
        {"datetime": "2026-01-06", "close": 11.0},   # 主板 10% 真涨停
    ]
    assert fn(bars3, "600519").get("YearZTDay") == 1
    print("[gate] YearZTDay：北交所 +20%不误记 / +30%计入 / 主板 +10%计入")


if __name__ == "__main__":
    test_delegated_limit_funcs()
    test_pref_bj_92()
    test_year_ztday_no_fallback()
    print("test_limit_edge_files PASS")
