# -*- coding: utf-8 -*-
"""独立验收锁（2026-11-06）：canonical 边界修正必须有真实 K 线证据，生产者不得自证。

不采信 board_limit.py 的注释或 selftest 自报，而是：
  1. 直接从磁盘读 backtest_cache/daily/689009.csv，统计 |单日涨跌| > 10.5% 的交易日；
  2. 用 canonical is_limit_up 与"旧 10% 口径"分别判定同一根 K 线，证明旧口径会误判涨停；
  3. 锁定 689(CDR 20%) / 900(B股 10%) / 920(北交所 30%) 三类边界。

运行：.venv/Scripts/python.exe research/test_board_limit_edge_cases.py
"""
import csv
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from research.board_limit import board_limit_pct, is_limit_up  # 单一真相源

CDR_CSV = os.path.join(ROOT, "backtest_cache", "daily", "689009.csv")


def _old_10pct_limit_up(prev, close):
    """旧口径（把 689 当主板 10%）的判定式，用于反证，不参与生产。"""
    lim_px = round(prev * 1.10, 2)
    return close >= lim_px - 0.005


def test_cdr_has_real_20pct_moves():
    assert os.path.exists(CDR_CSV), "缺少实证数据: %s" % CDR_CSV
    rows = list(csv.reader(open(CDR_CSV, encoding="utf-8")))
    body = rows[1:]
    prev, moves = None, []
    for r in body:
        try:
            close = float(r[2])
        except (ValueError, IndexError):
            continue
        if prev and prev > 0:
            ret = close / prev - 1.0
            if abs(ret) > 0.105:
                moves.append((r[0], prev, close, ret))
        prev = close
    # 20% 板的必要条件：存在多日超过 10% 的波动，且不是上市首日一次性现象
    assert len(moves) >= 3, "实证不足：689009 超过 10% 的交易日仅 %d 个" % len(moves)
    print("[证据] 689009 bars=%d 期间 %s ~ %s，|单日涨跌|>10.5%% 的交易日=%d"
          % (len(body), body[0][0] if body else "?", body[-1][0] if body else "?", len(moves)))
    for d, p, c, ret in moves[:6]:
        print("        %s %+.2f%%" % (d, ret * 100))
    return moves


def test_old_10pct_mislabels_cdr_as_limit_up():
    moves = test_cdr_has_real_20pct_moves()
    positives = [m for m in moves if m[3] > 0]
    assert positives, "实证数据里没有正涨幅样本"
    d, prev, close, ret = max(positives, key=lambda x: x[3])
    # 20% 板下，+18.93% 远未涨停；旧 10% 口径会把它判成涨停（假连板污染源）
    assert is_limit_up("689009", prev, close) is False
    assert _old_10pct_limit_up(prev, close) is True
    print("[反证] %s 689009 %+.2f%%：新口径=未涨停(正确)，旧10%%口径=涨停(假连板)" % (d, ret * 100))


def test_boundaries():
    assert board_limit_pct("689009") == 0.20   # 科创板 CDR
    assert board_limit_pct("900901") == 0.10   # 沪市 B 股，不再被 "9" 前缀误判 30%
    assert board_limit_pct("920491") == 0.30   # 北交所 30% 板不受收紧影响
    assert board_limit_pct("830799") == 0.30
    assert board_limit_pct("688001") == 0.20
    assert board_limit_pct("300750") == 0.20
    assert board_limit_pct("600519") == 0.10
    assert is_limit_up("689009", 10.00, 12.00) is True
    assert is_limit_up("689009", 10.00, 11.00) is False
    assert is_limit_up("900901", 10.00, 11.00) is True
    assert is_limit_up("900901", 10.00, 10.90) is False
    assert board_limit_pct("689009", is_st=True) == 0.20
    assert board_limit_pct("920491", is_st=True) == 0.30
    assert board_limit_pct("600519", is_st=True) == 0.05
    print("[边界] 689/900/920 + ST 向量全部一致")


if __name__ == "__main__":
    test_cdr_has_real_20pct_moves()
    print()
    test_old_10pct_mislabels_cdr_as_limit_up()
    test_boundaries()
    print("test_board_limit_edge_cases PASS")
