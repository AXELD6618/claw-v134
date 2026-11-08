# -*- coding: utf-8 -*-
"""
test_tail_pick_outcome_caliber.py — 尾盘核验回路「口径」独立回归锁
=============================================================================
锁定 2026-11-07 修复的两个业务缺陷，防止回退：

  缺陷 A  measure_one 曾把「T 日已涨停、14:50 根本买不进」的票计入统计，
          污染分母、扭曲 mean_close_ret。修复后在纳入统计前用
          is_limit_up(code, T-1收盘, T收盘) 判定并排除，落 status=
          excluded_t_limitup，由 summarize 计入 n_excluded_t_limitup。
  缺陷 B  compute_metrics 已 import is_limit_up 却仍手写等价式
          zt_price = round(base*(1+lim),2)-0.005; hit_zt = cur >= zt_price。
          修复后 hit_zt 唯一交给 board_limit.is_limit_up（Decimal
          ROUND_HALF_UP 交易所口径），手写比较判定彻底移除。

同时锁定：
  - _delivery_receipt 仍只认 ILINK / PUSHPLUS，DIALOG 仅 stdout 绝不接纳；
  - 既有字段零漂移：除 hit_zt 在「旧式假阳性窗口」被修正外，compute_metrics
    的全部既有字段（含 zt_price）逐值不变。

运行（硬性用仓库 venv python）：
  E:/WorkBuddy_dot_workbuddy/Claw/.venv/Scripts/python.exe \
      research/test_tail_pick_outcome_caliber.py
成功输出：=== ALL PASS (N checks) ===

本文件为独立回归锁：只读生产代码，不修改任何生产文件、不写任何台账、
不落盘、不 commit。合成样本用 monkeypatch 注入，不依赖回测产出文件是否齐全。
"""
from __future__ import annotations

import os
import sys
import ast
import json
import inspect
import textwrap
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import research.tail_pick_outcome as tpo                        # noqa: E402
from research.board_limit import board_limit_pct, is_limit_up  # noqa: E402

_ORIG_LOAD_BARS = tpo.load_bars
CHECKS = 0
FAILED = []


def ck(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    if cond:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + (("  | " + str(detail)) if detail else ""))
        FAILED.append(name)


def section(title):
    print()
    print("-" * 90)
    print(title)
    print("-" * 90)


class patch_bars:
    """把 tpo.load_bars 临时替换为返回给定合成 bars（按日期升序）。

    __exit__ 恢复「进入前」的引用而非硬编码原函数，以支持嵌套 patch。
    """

    def __init__(self, bars):
        self.bars = bars
        self._saved = None

    def __enter__(self):
        self._saved = tpo.load_bars
        tpo.load_bars = lambda code: list(self.bars)
        return self

    def __exit__(self, *exc):
        tpo.load_bars = self._saved
        return False


def legacy_hit(code, base, cur):
    """修复前 compute_metrics 的手写涨停判定（等价复现，不传 is_st）。"""
    lim = board_limit_pct(code)
    return bool(cur >= round(base * (1.0 + lim), 2) - 0.005)


def legacy_compute_metrics(code, name, base, b1):
    """修复前 compute_metrics 全量等价复现（用于既有字段零漂移对照）。"""
    lim = board_limit_pct(code)
    open_ret = b1[1] / base - 1.0
    close_ret = b1[2] / base - 1.0
    high_ret = b1[3] / base - 1.0
    low_ret = b1[4] / base - 1.0
    zt_price = round(base * (1.0 + lim), 2) - 0.005
    return {
        "t1_date": b1[0], "base_close": round(base, 4),
        "t1_open": round(b1[1], 4), "t1_close": round(b1[2], 4),
        "t1_high": round(b1[3], 4), "t1_low": round(b1[4], 4),
        "open_ret": round(open_ret, 6), "close_ret": round(close_ret, 6),
        "high_ret": round(high_ret, 6), "low_ret": round(low_ret, 6),
        "limit_pct": lim, "zt_price": zt_price,
        "hit_zt": bool(b1[2] >= zt_price), "win": bool(close_ret > 0),
    }


def eq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9
    return a == b


# ==========================================================================
# (a) 缺陷 A：T 日已涨停票被排除，且 n_excluded_t_limitup 计数正确
# ==========================================================================
def test_a():
    section("(a) 缺陷 A：T 日已涨停不可买样本被排除 + 显式排除计数")
    # 列序 (date, open, close, high, low)；T-2 / T-1 / T / T+1
    bars = [
        ("2026-10-20", 10.0, 10.0, 10.0, 10.0),
        ("2026-10-21", 10.0, 10.0, 10.0, 10.0),   # T-1 收盘 10.00
        ("2026-10-22", 11.0, 11.0, 11.0, 11.0),   # T 收盘 11.00 = +10% 封板
        ("2026-10-23", 11.5, 11.5, 11.5, 11.5),   # T+1
    ]
    pick = {"code": "600000", "name": "测试涨停", "sub": "HOLD5_GRAIL",
            "verify_tier": "A", "buyable": True}
    with patch_bars(bars):
        # off-by-one 校验：T-1 必须是 2026-10-21（收盘 10.00），不能是 T 或 T+1
        pb = tpo.prev_bar_before(bars, "2026-10-22")
        ck("prev_bar_before 取严格 T-1（非 T / 非 T+1）",
           pb is not None and pb[0] == "2026-10-21" and pb[2] == 10.0, str(pb))
        ck("T 为首根时 prev_bar_before 返回 None",
           tpo.prev_bar_before(bars, "2026-10-20") is None)

        rec = tpo.measure_one("2026-10-22", pick, True)
        ck("measure_one 返回非空记录", rec is not None)
        ck("T 日已涨停 -> status=excluded_t_limitup",
           rec.get("status") == "excluded_t_limitup", str(rec))
        ck("排除记录带审计字段 t_prev_close / t_close / t_prev_date",
           rec.get("t_prev_close") == 10.0 and rec.get("t_close") == 11.0
           and rec.get("t_prev_date") == "2026-10-21", str(rec))
        ck("排除记录不产生 close_ret（未进统计分母）",
           "close_ret" not in rec and "open_ret" not in rec, str(rec))
        ck("排除记录保留 delivered 事实可追溯", rec.get("delivered") is True)
        ck("排除原因写入 note（非静默丢样本）",
           "涨停" in str(rec.get("note", "")), str(rec.get("note")))

        # summarize 的显式排除计数：全部 rows 中被排除的样本都要计数
        ex_delivered = dict(rec)
        ex_undelivered = dict(rec, delivered=False)
        ok_rec = {"date": "2026-10-22", "code": "600001", "status": "ok",
                  "delivered": True, "sub": "REVERSAL", "verify_tier": "A",
                  "r1_forced": False, "win": True, "hit_zt": False,
                  "open_ret": 0.01, "close_ret": 0.02, "high_ret": 0.03,
                  "low_ret": -0.01}
        s = tpo.summarize([ex_delivered, ok_rec, ex_undelivered])
        ck("summarize 暴露 n_excluded_t_limitup 字段",
           "n_excluded_t_limitup" in s, str(sorted(s.keys())))
        ck("n_excluded_t_limitup 计数正确（2 条排除，含未送达）",
           s["n_excluded_t_limitup"] == 2, str(s["n_excluded_t_limitup"]))
        ck("被排除样本不进 delivered_ok 分母（n=1）",
           s["n_delivered_ok"] == 1 and s["overall_delivered"]["n"] == 1,
           str(s["overall_delivered"]))
        ck("ok 样本收益未被排除逻辑改写",
           s["overall_delivered"]["mean_close_ret"] == 0.02,
           str(s["overall_delivered"]))

    # 换一个 T-1 收盘，使同一 T 收盘「不涨停」，确认排除确实由 T-1 收盘驱动
    # （T-1=10.8 → T=11.0 仅 +1.85%，不封板）
    bars3 = [
        ("2026-10-21", 10.8, 10.8, 10.8, 10.8),
        ("2026-10-22", 11.0, 11.0, 11.0, 11.0),
        ("2026-10-23", 11.5, 11.5, 11.5, 11.5),
    ]
    with patch_bars(bars3):
        r3 = tpo.measure_one("2026-10-22", pick, True)
        ck("同一 T 收盘、T-1 抬高后不再判涨停（判定确用 T-1 收盘）",
           r3.get("status") == "ok", str(r3))


# ==========================================================================
# (b) 未涨停票仍正常计入
# ==========================================================================
def test_b():
    section("(b) 未涨停票仍正常计入统计（不误杀）")
    pick = {"code": "600000", "name": "普通票", "sub": "REVERSAL",
            "verify_tier": "A", "buyable": True}

    # T-1=10.00, T=10.50 (+5% 未涨停), T+1=11.00
    bars = [
        ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
        ("2026-10-22", 10.5, 10.5, 10.6, 10.4),   # T 未涨停
        ("2026-10-23", 11.0, 11.0, 11.2, 10.9),   # T+1
    ]
    with patch_bars(bars):
        rec = tpo.measure_one("2026-10-22", pick, True)
        ck("未涨停票 status=ok", rec.get("status") == "ok", str(rec))
        ck("未涨停票正常产出 close_ret", "close_ret" in rec, str(rec))
        ck("close_ret 口径正确 (11.0/10.5-1)",
           abs(rec["close_ret"] - (11.0 / 10.5 - 1.0)) < 1e-6, str(rec.get("close_ret")))
        s = tpo.summarize([rec])
        ck("未涨停票 n_excluded_t_limitup=0", s["n_excluded_t_limitup"] == 0,
           str(s["n_excluded_t_limitup"]))
        ck("未涨停票计入 delivered_ok（n=1）",
           s["n_delivered_ok"] == 1 and s["overall_delivered"]["n"] == 1,
           str(s["overall_delivered"]))

    # 边界：T 收盘 10.99，交易所涨停价 11.00，判定线 10.995 -> 不涨停，应计入
    bars_edge = [
        ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
        ("2026-10-22", 10.99, 10.99, 10.99, 10.99),
        ("2026-10-23", 11.0, 11.0, 11.0, 11.0),
    ]
    with patch_bars(bars_edge):
        rec = tpo.measure_one("2026-10-22", pick, True)
        ck("T 收盘 10.99 差 0.005 不涨停 -> 正常计入",
           rec.get("status") == "ok", str(rec))

    # 边界：T 收盘 11.00 恰封板 -> 排除（与 10.99 只差 0.01，验证不 off-by-one）
    bars_edge2 = [
        ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
        ("2026-10-22", 11.0, 11.0, 11.0, 11.0),
        ("2026-10-23", 11.5, 11.5, 11.5, 11.5),
    ]
    with patch_bars(bars_edge2):
        rec = tpo.measure_one("2026-10-22", pick, True)
        ck("T 收盘 11.00 恰封板 -> 排除（边界对称）",
           rec.get("status") == "excluded_t_limitup", str(rec))

    # T 为本地首根 bar（无 T-1）：无法判定封板，保守计入而非静默丢样本
    bars_first = [
        ("2026-10-22", 11.0, 11.0, 11.0, 11.0),
        ("2026-10-23", 11.5, 11.5, 11.5, 11.5),
    ]
    with patch_bars(bars_first):
        rec = tpo.measure_one("2026-10-22", pick, True)
        ck("无 T-1 日线时不静默丢样本，仍计入 ok",
           rec.get("status") == "ok", str(rec))


# ==========================================================================
# (c) 缺陷 B：is_limit_up 被用于涨停判定，旧手写判定不再出现
# ==========================================================================
def test_c():
    section("(c) 缺陷 B：hit_zt 唯一交给 is_limit_up，手写判定移除")
    src = textwrap.dedent(inspect.getsource(tpo.compute_metrics))
    tree = ast.parse(src)

    hit_value = None
    zt_compares = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "hit_zt":
                    hit_value = node.value
        if isinstance(node, ast.Compare):
            names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]
            if "zt_price" in names:
                zt_compares.append(node)

    ck("compute_metrics 源码含 is_limit_up( 调用", "is_limit_up(" in src)
    ck("hit_zt 的赋值值是一个函数调用", isinstance(hit_value, ast.Call),
       ast.dump(hit_value) if hit_value is not None else "None")
    ck("hit_zt 由 is_limit_up 调用产生（单一真相源）",
       isinstance(hit_value, ast.Call)
       and getattr(hit_value.func, "id", None) == "is_limit_up",
       ast.dump(hit_value) if hit_value is not None else "None")
    ck("compute_metrics 内不再有任何以 zt_price 为比较对象的手写判定",
       len(zt_compares) == 0, "残留 %d 处" % len(zt_compares))
    ck("旧手写比例/价格阈值判定式不再出现",
       (">= zt_price" not in src) and ("ret >= lim" not in src)
       and ("lim - 0.005" not in src) and ("lim-0.005" not in src))
    ck("is_limit_up 来自 board_limit 的 import 未被移除",
       "is_limit_up" in textwrap.dedent(inspect.getsource(tpo)))

    # 数值：hit_zt 逐例等于 is_limit_up(code, base, t1_close)
    cases = [
        ("600000", 10.0, ("2026-10-23", 11.0, 11.0, 11.0, 10.9)),   # 主板涨停
        ("601956", 5.54, ("2026-10-23", 6.05, 6.09, 6.09, 6.0)),    # 低价股 5.54->6.09
        ("300750", 10.0, ("2026-10-23", 12.0, 12.0, 12.0, 11.8)),   # 创业板 20cm
        ("830799", 10.0, ("2026-10-23", 13.0, 13.0, 13.0, 12.8)),   # 北交所 30cm
        ("600000", 10.0, ("2026-10-23", 9.5, 9.4, 9.6, 9.3)),       # 亏损
    ]
    for code, base, b1 in cases:
        m = tpo.compute_metrics(code, "", base, b1)
        ck("hit_zt == is_limit_up(%s, %.2f, %.2f)" % (code, base, b1[2]),
           m["hit_zt"] is is_limit_up(code, base, b1[2]),
           "got=%s expected=%s" % (m["hit_zt"], is_limit_up(code, base, b1[2])))
    # zt_price 展示字段仍在且沿用历史 round 口径（既有字段零漂移）
    m = tpo.compute_metrics("600000", "", 10.0,
                            ("2026-10-23", 11.0, 11.0, 11.0, 10.9))
    ck("zt_price 展示字段仍存在", "zt_price" in m)
    ck("zt_price 值沿用 round(base*(1+lim),2)-0.005 历史口径",
       eq(m["zt_price"], round(10.0 * (1.0 + board_limit_pct("600000")), 2) - 0.005),
       str(m.get("zt_price")))


# ==========================================================================
# (d) 等价性验证逐例断言（替换前置验证的固化）
# ==========================================================================
def test_d():
    section("(d) 等价性验证逐例断言：旧手写式 vs is_limit_up")
    # (label, code, base=T日收盘, cur=T+1收盘, is_st, 期望 old, 期望 new, 说明)
    cases = [
        ("主板10% 普通涨停 10.00->11.00", "600000", 10.00, 11.00, False, True, True, ""),
        ("主板10% 未涨停 +5%", "600000", 10.00, 10.50, False, False, False, ""),
        ("低价股 5.54->6.09 涨停价 6.09", "601956", 5.54, 6.09, False, True, True,
         "实涨 +9.928%，交易所舍入口径仍判涨停"),
        ("低价股 5.54->6.08 差一分", "601956", 5.54, 6.08, False, False, False, ""),
        ("创业板 20cm 10.00->12.00", "300750", 10.00, 12.00, False, True, True, ""),
        ("创业板 20cm 未涨停 +19%", "300750", 10.00, 11.90, False, False, False, ""),
        ("科创板 20cm 10.00->12.00", "688001", 10.00, 12.00, False, True, True, ""),
        ("北交所 30cm 10.00->13.00", "830799", 10.00, 13.00, False, True, True, ""),
        ("北交所 30cm 未涨停 +25%", "830799", 10.00, 12.50, False, False, False, ""),
        ("接近阈值 100.00->109.60 未涨停不误判", "600519", 100.00, 109.60, False, False, False, ""),
        ("接近阈值 100.00->109.99 差 0.005", "600519", 100.00, 109.99, False, False, False, ""),
        ("恰好阈值 100.00->110.00 涨停", "600519", 100.00, 110.00, False, True, True, ""),
    ]
    for label, code, base, cur, is_st, exp_old, exp_new, note in cases:
        o = legacy_hit(code, base, cur)
        n = bool(is_limit_up(code, base, cur, is_st))
        ck("%s → old=%s new=%s 一致" % (label, o, n),
           o == exp_old and n == exp_new and o == n,
           "old=%s(exp %s) new=%s(exp %s) %s" % (o, exp_old, n, exp_new, note))

    # ST 5%：旧手写式从不传 is_st（用 10% 口径）故漏判；is_limit_up(is_st=True) 正确判中。
    # 这是「旧式误判修正」，明确列出，不计入生产口径 changed。
    st_old = legacy_hit("600519", 10.00, 10.50)
    st_new = bool(is_limit_up("600519", 10.00, 10.50, True))
    ck("ST 5% 真涨停：旧式漏判 False / is_limit_up(is_st=True) 判中 True",
       st_old is False and st_new is True, "old=%s new=%s" % (st_old, st_new))
    ck("ST 5% 双创不降板仍 20%", board_limit_pct("300750", is_st=True) == 0.20)
    ck("ST 5% 北交所不降板仍 30%", board_limit_pct("830799", is_st=True) == 0.30)
    ck("ST 5% 主板降为 5%", board_limit_pct("600519", is_st=True) == 0.05)

    # 「未涨停但接近阈值」的反向误判边界：Python round(banker's) 把涨停价算低 1 分，
    # 旧式把未涨停票误判为涨停（假阳性），is_limit_up 用 Decimal HALF_UP 修正。
    # 实证样例 base=1.15: round(1.15*1.1,2)=1.26 而交易所 HALF_UP=1.27。
    edge_old = legacy_hit("600000", 1.15, 1.26)
    edge_new = bool(is_limit_up("600000", 1.15, 1.26))
    ck("反向误判边界 base=1.15 cur=1.26：旧式误判涨停 True",
       edge_old is True, str(edge_old))
    ck("反向误判边界 base=1.15 cur=1.26：is_limit_up 正确判不涨停 False",
       edge_new is False, str(edge_new))
    ck("该边界属「旧式假阳性被修正」方向 old=True/new=False",
       edge_old is True and edge_new is False)

    # 缩小网格：生产口径（is_st=False）下所有 changed 必须是「旧式假阳性被修正」，
    # 不允许出现 old=False/new=True（新式把真涨停漏判）的反向回归。
    grid_changed = 0
    bad_direction = 0
    for code in ("600000", "601956", "300750", "688001", "830799"):
        lim = board_limit_pct(code)
        for i in range(100, 3001):                 # base 1.00..30.00 步长 0.01
            base = i / 100.0
            from decimal import Decimal, ROUND_HALF_UP
            lim_px = float((Decimal(str(base)) * Decimal(str(1.0 + lim)))
                           .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            for cur in (round(lim_px - 0.01, 2), round(lim_px, 2)):
                if cur <= 0:
                    continue
                o = legacy_hit(code, base, cur)
                n = bool(is_limit_up(code, base, cur))
                if o != n:
                    grid_changed += 1
                    if (not o) and n:
                        bad_direction += 1
    print("  [grid] base 1.00~30.00 步长 0.01 × 5 板块，changed=%d，"
          "反向回归(old=False/new=True)=%d" % (grid_changed, bad_direction))
    ck("网格 changed 全部为旧式假阳性修正，无反向回归", bad_direction == 0,
       "bad_direction=%d" % bad_direction)
    ck("网格确实复现出旧式误判窗口（changed>0，非空洞断言）", grid_changed > 0,
       "changed=%d" % grid_changed)

    # 现有台账 105 条 ok 记录：生产口径三方比对 changed 必须为 0（hit_zt 零漂移）
    jl = os.path.join(ROOT, "data", "tail_pick_outcome.jsonl")
    if os.path.exists(jl):
        rows = [json.loads(l) for l in open(jl, encoding="utf-8") if l.strip()]
        ok = [r for r in rows if r.get("status") == "ok"]
        changed = 0
        for r in ok:
            o = legacy_hit(r["code"], r["base_close"], r["t1_close"])
            n = bool(is_limit_up(r["code"], r["base_close"], r["t1_close"]))
            if not (o == n == bool(r.get("hit_zt"))):
                changed += 1
        print("  [ledger] 现有台账 ok=%d 条，生产口径三方(old/is_limit_up/台账) changed=%d"
              % (len(ok), changed))
        ck("现有台账 hit_zt 生产口径等价 changed=0（零漂移）", changed == 0,
           "changed=%d" % changed)
    else:
        print("  [ledger] 台账不存在，跳过（合成用例已覆盖等价性）")


# ==========================================================================
# (e) _delivery_receipt 仍只认 ILINK / PUSHPLUS，DIALOG 被拒
# ==========================================================================
def test_e():
    section("(e) 送达回执纪律：只认 ILINK / PUSHPLUS，DIALOG 仅 stdout 不接纳")
    tmp = tempfile.mkdtemp(prefix="tail_receipt_")
    old_cache = tpo.CACHE
    tpo.CACHE = tmp
    receipt_path = os.path.join(tmp, "delivered_14h50_2026-01-01.json")
    try:
        for channel, delivered, expected in [
            ("ILINK", True, True),
            ("PUSHPLUS", True, True),
            ("DIALOG", True, False),      # 关键纪律：仅 stdout 绝不算送达
            ("WECHAT", True, False),
            (None, True, False),
            ("ILINK", False, False),      # delivered=False 即便渠道合法也不算
        ]:
            with open(receipt_path, "w", encoding="utf-8") as f:
                json.dump({"delivered": delivered, "channel": channel}, f)
            got = tpo._delivery_receipt("2026-01-01")
            ck("_delivery_receipt(channel=%r, delivered=%s) is %s"
               % (channel, delivered, expected), got is expected, "got=%s" % got)
        if os.path.exists(receipt_path):
            os.remove(receipt_path)
        ck("回执文件不存在时返回 False",
           tpo._delivery_receipt("2026-01-01") is False)
        ck("从未放宽接纳 DIALOG（源码仍为 ILINK/PUSHPLUS 白名单）",
           "DIALOG" not in textwrap.dedent(inspect.getsource(tpo._delivery_receipt))
           and '("ILINK", "PUSHPLUS")' in textwrap.dedent(
               inspect.getsource(tpo._delivery_receipt)))
    finally:
        tpo.CACHE = old_cache
        shutil.rmtree(tmp, ignore_errors=True)

    # 回执为 False 时，resolve_delivered 不得把 dry 的 picks 全量升级为送达
    ck("receipt=False 时 dry+picks 不被升级为送达",
       tpo.resolve_delivered({"push_ok": True, "dry": True}, "picks", False) is False)


# ==========================================================================
# (f) 既有字段零漂移：legacy vs new 同输入逐字段比对
# ==========================================================================
def test_f():
    section("(f) 既有字段零漂移：修复前后 compute_metrics 同输入逐字段比对")
    # 合成输入（含低价股 5.54、20cm、30cm、亏损、恰好涨停边界）
    synth = [
        ("600000", 10.0, ("2026-10-23", 11.0, 11.0, 11.1, 10.8)),
        ("601956", 5.54, ("2026-10-23", 6.05, 6.09, 6.09, 6.0)),
        ("300750", 10.0, ("2026-10-23", 12.0, 12.0, 12.2, 11.8)),
        ("830799", 10.0, ("2026-10-23", 13.0, 12.5, 13.1, 12.0)),
        ("600000", 10.0, ("2026-10-23", 9.5, 9.4, 9.6, 9.3)),
        ("000678", 18.75, ("2026-10-30", 20.6, 20.62, 20.7, 20.5)),
    ]
    drift = 0          # 「未触及字段」漂移（必须为 0）
    hit_changed = 0    # hit_zt 被缺陷 B 触及，允许旧式假阳性被修正
    compared = 0
    for code, base, b1 in synth:
        old_m = legacy_compute_metrics(code, "", base, b1)
        new_m = tpo.compute_metrics(code, "", base, b1)
        ck("%s 字段集合一致" % code, set(old_m) == set(new_m),
           str(set(old_m) ^ set(new_m)))
        for k in old_m:
            compared += 1
            if k == "hit_zt":
                # hit_zt 是缺陷 B 唯一「被触及」的字段：其变化必须是
                # 「旧式误判涨停(True) -> is_limit_up 修正为不涨停(False)」，禁止反向。
                if not eq(old_m[k], new_m[k]):
                    hit_changed += 1
                    ck("%s hit_zt 变化方向为 old=True/new=False（旧式假阳性修正）"
                       % code, old_m[k] is True and new_m[k] is False,
                       "%r -> %r" % (old_m[k], new_m[k]))
                ck("%s hit_zt 恒等于 is_limit_up(code, base, t1_close)" % code,
                   new_m[k] is is_limit_up(code, base, b1[2]),
                   "got=%s" % new_m[k])
                continue
            if not eq(old_m[k], new_m[k]):
                drift += 1
                print("      漂移 %s %s: %r -> %r" % (code, k, old_m[k], new_m[k]))
    print("  [zero-drift] 合成输入逐字段比对 %d 处；未触及字段漂移 %d 处；"
          "hit_zt 预期修正 %d 处（000678/base=18.75 是 round/Decimal 差异点）"
          % (compared, drift, hit_changed))
    ck("合成输入「未触及字段」零漂移（zt_price / 各 ret / win / limit_pct 等）",
       drift == 0, "drift=%d" % drift)
    ck("hit_zt 的修正仅发生在旧式假阳性窗口（未出现反向）", hit_changed <= 1,
       "hit_changed=%d" % hit_changed)

    # 真实台账（若存在）：用原始日线输入做逐字段比对，杜绝 round4 重建引入的假差异
    jl = os.path.join(ROOT, "data", "tail_pick_outcome.jsonl")
    if os.path.exists(jl):
        rows = [json.loads(l) for l in open(jl, encoding="utf-8") if l.strip()]
        ok = [r for r in rows if r.get("status") == "ok"]
        dr, comp = 0, 0
        for r in ok:
            bars = _ORIG_LOAD_BARS(r["code"])
            b0 = tpo.bar_on(bars, r["date"])
            b1 = tpo.next_bar_after(bars, r["date"])
            if b0 is None or b1 is None:
                continue
            om = legacy_compute_metrics(r["code"], r["name"], b0[2], b1)
            nm = tpo.compute_metrics(r["code"], r["name"], b0[2], b1)
            for k in om:
                comp += 1
                if not eq(om[k], nm[k]):
                    dr += 1
        print("  [zero-drift] 真实台账 ok=%d 条，逐字段 %d 处，漂移 %d 处"
              % (len(ok), comp, dr))
        ck("真实台账 105 条 ok 记录既有字段零漂移", dr == 0, "drift=%d" % dr)
    else:
        print("  [zero-drift] 台账不存在，跳过真实数据段")


def main():
    print("=" * 90)
    print("尾盘核验回路 口径回归锁  (venv: %s)" % sys.executable)
    print("=" * 90)
    test_a()
    test_b()
    test_c()
    test_d()
    test_e()
    test_f()
    print()
    print("=" * 90)
    if FAILED:
        print("=== FAILED (%d/%d checks) ===" % (len(FAILED), CHECKS))
        for f in FAILED:
            print("   - " + f)
        return 1
    print("=== ALL PASS (%d checks) ===" % CHECKS)
    print("=" * 90)
    return 0


if __name__ == "__main__":
    sys.exit(main())
