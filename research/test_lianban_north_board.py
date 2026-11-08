# -*- coding: utf-8 -*-
"""北交所30%板修复的【运行时】验证：证明修复流入核心连板路径并消除假连板污染。

不依赖实时行情，直接用生产代码 gen_lianban_watchlist.lim_of（已修复）+
生产判定式（main 中第89-91行：cur >= round(prev*(1+lim),2) - 0.005）做断言。

证明两点：
  (1) lim_of 对北交所/双创/主板分类正确（运行时取数，非静态 grep）；
  (2) 修复后：北交所 +30% 真涨停→计入连板；北交所 +25%（未到30%板）→不计入；
      而旧错口径(lim=0.10)会把 +25% 误判为涨停 → 假连板污染，现已消除。
"""
import os
import sys
import json
import shutil
import unittest
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gen_lianban_watchlist as gl
from research.board_limit import is_limit_up as _canon_is_zt


def is_zt(prev, cur, lim):
    """复刻生产判定式（gen_lianban_watchlist.main 第89-91行）。"""
    if prev <= 0:
        return False
    lim_px = float((Decimal(str(prev)) * Decimal(str(1.0 + lim)))
                   .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return cur >= lim_px - 0.005


class TestNorthBoardRuntime(unittest.TestCase):

    def test_lim_of_classification(self):
        cases = [
            ("920491", 0.30),   # 北交所(920)
            ("921000", 0.30),   # 北交所(921)
            ("830799", 0.30),   # 北交所(83)
            ("430047", 0.30),   # 北交所(43)
            ("688001", 0.20),   # 科创板
            ("300750", 0.20),   # 创业板
            ("301269", 0.20),   # 创业板(301)
            ("600519", 0.10),   # 主板
        ]
        for code, exp in cases:
            self.assertEqual(gl.lim_of(code), exp, "lim_of(%s)" % code)

    def test_bjx_true_zt_counts(self):
        """北交所 prev=10.00, cur=13.00(+30% 真涨停)→应计入连板。"""
        self.assertTrue(is_zt(10.00, 13.00, gl.lim_of("920491")))

    def test_bjx_sub_30_not_zt(self):
        """北交所 prev=10.00, cur=12.50(+25%，未到30%板)→不应计入连板。"""
        self.assertFalse(is_zt(10.00, 12.50, gl.lim_of("920491")))

    def test_old_bug_false_positive_eliminated(self):
        """关键证明：旧错口径 lim=0.10 会把 +25% 北交所误判为涨停（假连板）。
        修复后 lim=0.30，该票正确不计入。回归此不变量，防止口径回退。"""
        # 旧错口径
        old_lim = 0.10
        self.assertTrue(is_zt(10.00, 12.50, old_lim),
                        "旧错口径(lim=0.10)会把北交所+25%误判为涨停(假连板)")
        # 修复后口径
        self.assertFalse(is_zt(10.00, 12.50, gl.lim_of("920491")),
                         "修复后(lim=0.30)北交所+25%正确不计入连板")

    def test_canonical_is_limit_up_direct(self):
        """直接断言单一真相源 is_limit_up（取代散落判定式）。"""
        # 北交所 +30% 真涨停 / +25% 未到板
        self.assertTrue(_canon_is_zt("920491", 10.00, 13.00))
        self.assertFalse(_canon_is_zt("920491", 10.00, 12.50))
        # 主板 +10% / +9%
        self.assertTrue(_canon_is_zt("600519", 100.00, 110.00))
        self.assertFalse(_canon_is_zt("600519", 100.00, 109.00))
        # ST +5%
        self.assertTrue(_canon_is_zt("600519", 10.00, 10.50, is_st=True))

    def test_lianban_detects_bjx_two_board(self):
        """集成级证明：gen_lianban_watchlist 用 is_limit_up 正确检出北交所 2-连板。
        构造合成日线（920491 连续两个 +30% 涨停），跑 main() 断言 board_len=2。
        用临时目录隔离 gl.DAILY/gl.OUT，避免扫描真实全市场日线缓存（数千文件），
        也不污染真实台账：测试后还原常量并清理临时目录。"""
        import tempfile
        old_daily = gl.DAILY
        old_out = gl.OUT
        tmp = tempfile.mkdtemp(prefix="lianban_test_")
        try:
            daily_dir = os.path.join(tmp, "daily")
            out_json = os.path.join(tmp, "lianban_watchlist.json")
            gl.DAILY = daily_dir
            gl.OUT = out_json
            os.makedirs(daily_dir, exist_ok=True)
            syn_csv = os.path.join(daily_dir, "920491.csv")
            with open(syn_csv, "w", encoding="utf-8") as f:
                f.write("date,open,close,high,low,vol,amount\n")
                f.write("2026-11-02,10.00,10.00,10.00,10.00,100,1000\n")
                f.write("2026-11-03,10.00,13.00,13.00,10.00,200,2600\n")
                f.write("2026-11-04,13.00,16.90,16.90,13.00,200,3380\n")
            # 跑 main（--as-of 2026-11-04 --min-board 2）
            old_argv = sys.argv
            sys.argv = ["gen_lianban_watchlist", "--as-of", "2026-11-04", "--min-board", "2"]
            try:
                gl.main()
            finally:
                sys.argv = old_argv
            with open(out_json, encoding="utf-8") as f:
                snap = json.load(f)
            hits = [c for c in snap["codes"] if c["code"] == "920491"]
            self.assertEqual(len(hits), 1, "北交所 920491 应被检出为连板")
            self.assertEqual(hits[0]["board_len"], 2, "连续两个 +30% 应为 2 连板")
        finally:
            # 还原真实常量 + 清理临时目录
            gl.DAILY = old_daily
            gl.OUT = old_out
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNorthBoardRuntime)
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if res.wasSuccessful() else 1)
