# -*- coding: utf-8 -*-
"""
research/execution_model.py — 成交现实性模型（防回测 PF 虚高）

关键现实：
  - 开盘即涨停的票，散户买不进（涨停价排队，成交量优先给大单）→ 必须判"不可成交"
  - 尾盘信号收盘价 ≠ 实际成交价；加滑点+冲击成本
  - 双边费用（佣金+印花税）
run: python -m research.execution_model --selftest
"""
from __future__ import annotations
from research.board_limit import board_limit_pct, is_limit_up


def limit_pct(code):
    return board_limit_pct(code)



def limit_up_price(prev_close, code):
    return round(prev_close * (1 + limit_pct(code)) + 1e-8, 2)


def can_buy_at_open(open_next, prev_close, code, tol=0.002):
    """开盘涨停(>=涨停价*(1-tol))视为不可买。"""
    lu = limit_up_price(prev_close, code)
    if open_next >= lu * (1 - tol):
        return False, "limit_up_open_unfillable"
    return True, "ok"


def fill_price(open_next, prev_close, code, slippage_bps=10, impact_bps=0):
    ok, why = can_buy_at_open(open_next, prev_close, code)
    if not ok:
        return None, why
    return open_next * (1 + (slippage_bps + impact_bps) / 1e4), "filled"


def net_return(entry_px, exit_px, slippage_bps=10, impact_bps=0, fee_bps=8):
    """双边成本已在 entry/exit 各计一次滑点；此处再扣一次费用。返回扣成本后的净收益。"""
    if entry_px is None or exit_px is None:
        return None
    cost = (slippage_bps + impact_bps + fee_bps) / 1e4
    return (exit_px / entry_px - 1.0) - cost


def simulate_trade(code, prev_close, open_next, exit_px, slippage_bps=10, impact_bps=0, fee_bps=8):
    entry, why = fill_price(open_next, prev_close, code, slippage_bps, impact_bps)
    if entry is None:
        return {"filled": False, "reason": why, "net_ret": None}
    nr = net_return(entry, exit_px, slippage_bps, impact_bps, fee_bps)
    return {"filled": True, "entry": round(entry, 3), "exit": exit_px,
            "net_ret": round(nr, 5), "reason": why}


def _selftest():
    # 主板：昨收10，次日开盘10.9(非涨停) -> 可买
    print("[selftest] 可买:", simulate_trade("600000", 10.0, 10.9, 11.2))
    # 主板：次日开盘恰涨停 11.0 -> 不可买
    print("[selftest] 涨停开盘:", simulate_trade("600000", 10.0, 11.0, 11.5))
    # 创业板20%：昨收10，开盘12 -> 不可买
    print("[selftest] 创业板涨停开盘:", simulate_trade("300001", 10.0, 12.0, 12.5))
    assert simulate_trade("600000", 10.0, 11.0, 11.5)["filled"] is False
    assert simulate_trade("600000", 10.0, 10.9, 11.2)["filled"] is True
    print("[selftest] PASS — 涨停开盘正确判定为不可成交")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python -m research.execution_model --selftest")
