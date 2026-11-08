#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归锁：p1_edge_oos._zt_array 的涨停数组索引必须与 legacy 对齐，且 canonical 与 is_limit_up 一致。

2026-11-07 修复的 off-by-one：canonical 分支原 prev=c[:-1];cur=c[1:] 让 zt[j] 表示第 j+1 日涨停，
与 legacy zt[j]=第 j 日涨停 错位一位。此测试固化该修复，生产者不得自证（直接调函数+标量对照）。
"""
import os, sys
import numpy as np

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
from research.p1_edge_oos import _zt_array, _code_of
from research.board_limit import is_limit_up


def test_legacy_alignment():
    c = np.array([10.0, 11.0, 12.1, 9.5, 10.5, 11.6])
    zt = _zt_array("600519", c, "legacy")
    exp = np.array([False, True, True, False, True, True])
    assert zt.shape == c.shape, "legacy 长度未对齐 len(c)"
    assert np.array_equal(zt, exp), "legacy 分支漂移"


def test_canonical_index_alignment():
    """zt[k] 必须 = 第 k 日涨停（与 legacy 同一语义），而非错位。"""
    c = np.array([10.0, 11.0, 12.1, 9.5, 10.5, 11.6])
    code = "688001"
    zt = _zt_array(code, c, "canonical")
    assert zt.shape == c.shape, "canonical 长度必须 = len(c)"
    manual = np.array([False] + [is_limit_up(code, c[k - 1], c[k]) for k in range(1, len(c))])
    assert np.array_equal(zt, manual), "canonical 与 is_limit_up(逐日) 不一致（off-by-one?）"


def test_boundaries():
    # 689009 +18.93% 不应判涨停（旧 LIM=0.095 会误判）；注意 numpy 返回 np.bool_，用 == 不 is
    assert _zt_array("689009", np.array([10.0, 11.893]), "canonical")[1] == False
    # 689009 真实 20% 涨停
    assert _zt_array("689009", np.array([10.0, 12.00]), "canonical")[1] == True
    # 900901 沪市 B 股 10% 涨停
    assert _zt_array("900901", np.array([10.0, 11.0]), "canonical")[1] == True
    # 920491 北交所 30% 涨停
    assert _zt_array("920491", np.array([10.0, 13.0]), "canonical")[1] == True


def test_code_of():
    assert _code_of("backtest_cache/daily/689009.csv") == "689009"
    assert _code_of("x/300750.CSV") == "300750"  # 大小写与扩展名容错


if __name__ == "__main__":
    test_legacy_alignment()
    test_canonical_index_alignment()
    test_boundaries()
    test_code_of()
    print("test_oos_zt_array: 4/4 PASS (legacy 对齐 / canonical 对齐 / 边界 / code 解析)")
