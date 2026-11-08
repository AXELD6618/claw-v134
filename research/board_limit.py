# -*- coding: utf-8 -*-
"""
board_limit.py — 涨跌停幅度「单一真相源」（北交所 30% 板已覆盖）。

2026-11-06 收敛：全仓 80+ 个 .py 各自复制了一份 limit_pct/board_limit/_lim 之类函数，
且口径彼此打架（部分漏北交所 920/921、部分把北交所 8/4 错当 20%、部分漏双创 300/301）。
此处定为唯一正确实现，供生产脚本逐步 import 取代副本。

口径（交易所规则）：
  科创板 688 / CDR 689 / 创业板 300·301 → 20%
  北交所 8·4·92 开头（83/87/88/43/920/921…） → 30%
  其余主板/中小板、沪市 B 股 900xxx → 10%

说明：ST 股口径（2026-11-06 修正，见下）——系统已 ST 剔除，生产链路不受影响，
      但单一真相源本身必须规则完备，供研究/回测脚本正确复用。

2026-11-06 二次修正（实证驱动，见 selftest 与 MIGRATION_OVERVIEW.md）：
  689xxx（科创板 CDR，如 689009）实测存在 +18.93% / -16.12% 单日波动，属 20% 板，
  旧实现按 0.10 处理会漏判涨停；900xxx 为沪市 B 股 10%，旧实现因 "9" 前缀误判 30%。
"""
ST_LIMIT = 0.05  # ST/退市整理期：仅主板 5%；双创 20%、北交所 30% 不因 ST 改变

from decimal import Decimal, ROUND_HALF_UP  # 涨停价四舍五入，与生产判定式同口径


def board_limit_pct(code, is_st=False):
    """返回某代码对应的涨跌停幅度（小数）。

    规则（A 股）：
      科创板 688 / CDR 689 / 创业板 300·301 → 20%（ST 亦 20%，不降为 5%）
      北交所 8·4·92 开头                    → 30%（ST 亦 30%，不降为 5%）
      主板/中小板 / 沪市 B 股 900xxx        → 10%；ST/*ST 主板 → 5%

    2026-11-06 数字契约修正①：旧实现 is_st=True 对【所有板块】一律返回 0.05，
      与交易所规则不符（双创 ST 仍 20%、北交所 ST 仍 30%）。
      旧值→新值：is_st=True 时 688/300/301: 0.05→0.20；8/4/9: 0.05→0.30；主板 0.05 不变。
      影响面：仅显式 is_st 调用（selftest / is_limit_up / 研究与回测脚本），
              生产链路 ST 已剔除，无实盘回退风险。

    2026-11-06 数字契约修正②（边界精确化，有真实 K 线实证）：
      旧值→新值：689xxx 0.10→0.20；900xxx/9xx(非92) 0.30→0.10；920/921 维持 0.30。
      实证：backtest_cache/daily/689009.csv 共 660 根 K 线(2024-02-19~2026-11-06，
            非上市首日)，有 5 个交易日 |单日涨跌|>10.5%（含 2024-10-08 +18.93%、
            2025-04-07 -16.12%），只可能是 20% 板；900xxx 在 daily 缓存(0 个)与
            intraday_universe(0 个) 均无标的。
      影响面：所有调用 board_limit_pct / is_limit_up 的文件；实际行为变化仅 689xxx
            与 900xxx 两类代码，当前股票池与日线缓存中均无此类标的参与生产判定。
      遗留：88xxxx 既是北交所号段又是通达信板块指数代码，纯 code 前缀无法区分，
            指数不进入个股涨停判定路径，暂不处理（如实记录）。
    """
    c = str(code or "")
    if c.startswith(("688", "689", "300", "301")):  # 科创板(含CDR) / 创业板（ST 不降板）
        return 0.20
    if c.startswith(("8", "4", "92")):               # 北交所: 83/87/88/43/920/921...（ST 不降板）
        return 0.30
    if is_st:                                  # 仅主板 ST/*ST 降为 5%
        return ST_LIMIT
    return 0.10                                # 主板 / 中小板


def is_limit_up(code, prev_close, price, is_st=False):
    """判定某票当日是否「涨停」（单一真相源，取代各文件散落的 `cur>=round(prev*(1+lim),2)-0.005`）。

    与 gen_lianban_watchlist / backtest 等生产脚本的判定式同口径：
      lim_px = round(prev_close*(1+lim), 2) 四舍五入（ROUND_HALF_UP）
      涨停 iff price >= lim_px - 0.005（二分位舍入容差）
    用法：is_limit_up(code, prev_close, price) — code 决定板块幅度。
    """
    try:
        prev = float(prev_close)
        cur = float(price)
    except (TypeError, ValueError):
        return False
    if prev <= 0:
        return False
    lim = board_limit_pct(code, is_st)
    lim_px = float((Decimal(str(prev)) * Decimal(str(1.0 + lim)))
                   .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return cur >= lim_px - 0.005


def selftest():
    cases = [
        ("920491", 0.30), ("921000", 0.30), ("830799", 0.30),
        ("430047", 0.30), ("400061", 0.30), ("888888", 0.30),
        ("688001", 0.20), ("300750", 0.20), ("301269", 0.20),
        ("689009", 0.20),  # 科创板 CDR，实证 20% 板
        ("600519", 0.10), ("000001", 0.10), ("002594", 0.10),
        ("900901", 0.10),  # 沪市 B 股，不得误判为北交所 30%
    ]
    for code, exp in cases:
        got = board_limit_pct(code)
        assert got == exp, "code=%s exp=%s got=%s" % (code, exp, got)
    # ST 分支：主板 5%；双创/北交所 ST 不降板（2026-11-06 数字契约修正）
    assert board_limit_pct("600519", is_st=True) == 0.05   # 主板 ST
    assert board_limit_pct("000001", is_st=True) == 0.05   # 深主板 ST
    assert board_limit_pct("300750", is_st=True) == 0.20   # 创业板 ST 仍 20%
    assert board_limit_pct("688001", is_st=True) == 0.20   # 科创板 ST 仍 20%
    assert board_limit_pct("689009", is_st=True) == 0.20   # 科创板 CDR ST 仍 20%
    assert board_limit_pct("920491", is_st=True) == 0.30   # 北交所 ST 仍 30%
    # 涨停判定：北交所 +30% 真涨停计入、+25% 不计入；主板 +10% 计入、+9% 不计入
    assert is_limit_up("920491", 10.00, 13.00) is True     # 北交所 +30%
    assert is_limit_up("920491", 10.00, 12.50) is False    # 北交所 +25%（未到 30% 板）
    assert is_limit_up("600519", 100.00, 110.00) is True   # 主板 +10%
    assert is_limit_up("600519", 100.00, 109.00) is False  # 主板 +9%
    assert is_limit_up("600519", 10.00, 10.50, is_st=True) is True  # ST +5%
    assert is_limit_up("689009", 10.00, 12.00) is True     # CDR 20% 涨停必须计入
    assert is_limit_up("689009", 10.00, 11.00) is False    # 旧 10% 口径会误判为涨停
    assert is_limit_up("900901", 10.00, 11.00) is True     # B 股 10% 涨停（旧 30% 口径会漏判）
    assert is_limit_up("900901", 10.00, 10.90) is False    # 未到 10% 不算涨停
    print("board_limit selftest PASS (%d cases + ST/CDR/B股 断言)" % len(cases))
    return True


if __name__ == "__main__":
    selftest()
