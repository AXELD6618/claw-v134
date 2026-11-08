# -*- coding: utf-8 -*-
"""
test_tail_pick_outcome_forward.py — 核验台账「续涨原料 + 汇总层排除收口」独立回归锁
=============================================================================
锁定 2026-11-07 增量包（补齐核心业务目标的核验原料），防止回退：

  1. forward_bars_after()      一次取齐 T+1/T+2/T+3 三根前向 bar，严格晚于 T，
                               升序，不足 n 根时返回实际有多少根（绝不臆造）。
  2. measure_one 前向字段      落 t2_close_ret / t3_close_ret / forward_status /
                               forward_dates 原始逐日收益；只出原料、不做成功判定；
                               compute_metrics 既有字段集合与数值零漂移。
  3. rescan_excluded()         汇总层复核所有 status=ok 行（含源文件已轮转删除、
                               revive_stale 不回溯的历史行），命中 T 日涨停即改标
                               excluded_t_limitup；幂等、不误杀未涨停、绝不删除行。
  4. 接入 run()                 rescan_excluded 必须在 merge_rows 之后、summarize
                               之前调用；write=False 离线预演同样生效且不落盘。
  5. forward_path_coverage     summarize 只报原料齐备度，不判定续涨成功；
                               n_not_measured 是新增字段前落盘的历史行。

纪律（本锁自身也遵守）：
  - 只读生产代码；不修改任何生产文件、不写任何真实台账、不落盘到仓库、不 commit。
  - 合成样本用 monkeypatch 注入 load_bars / CACHE / OUT_JSONL；端到端一律 write=False。
  - 核心目标完整代理仍以 evolution_review.grail_success 为唯一真相源，本锁绝不
    重复实现「成功判定」，也绝不触碰门控 / 调权 / PROMOTE。

运行（硬性用仓库 venv python）：
  E:/WorkBuddy_dot_workbuddy/Claw/.venv/Scripts/python.exe \
      research/test_tail_pick_outcome_forward.py
成功输出：=== ALL PASS (N checks) ===
"""
from __future__ import annotations

import os
import sys
import json
import inspect
import textwrap
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import research.tail_pick_outcome as tpo                        # noqa: E402
from research.board_limit import is_limit_up                    # noqa: E402

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


class patch_bars_map:
    """把 tpo.load_bars 临时替换为「按 code 查表」；未登记的 code 返回 []。

    __exit__ 恢复「进入前」的引用而非硬编码原函数，以支持嵌套 patch。
    """

    def __init__(self, mapping):
        self.mapping = mapping
        self._saved = None

    def __enter__(self):
        self._saved = tpo.load_bars
        tpo.load_bars = lambda code: list(self.mapping.get(str(code), []))
        return self

    def __exit__(self, *exc):
        tpo.load_bars = self._saved
        return False


def eq(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9
    return a == b


# 合成日线：列序 (date, open, close, high, low)
# T-1=10.00 / T=10.50(未涨停) / T+1=11.00 / T+2=12.00 / T+3=13.00
BARS_FULL = [
    ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
    ("2026-10-22", 10.5, 10.5, 10.6, 10.4),
    ("2026-10-23", 11.0, 11.0, 11.1, 10.9),
    ("2026-10-26", 12.0, 12.0, 12.2, 11.8),
    ("2026-10-27", 13.0, 13.0, 13.1, 12.7),
]
# T-1=10.00 / T=11.00(+10% 封板) / T+1=11.50
BARS_T_LIMITUP = [
    ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
    ("2026-10-22", 11.0, 11.0, 11.0, 11.0),
    ("2026-10-23", 11.5, 11.5, 11.5, 11.5),
]
# 未涨停对照：T-1=10.00 / T=10.50 / T+1=11.00
BARS_NOT_LIMITUP = [
    ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
    ("2026-10-22", 10.5, 10.5, 10.6, 10.4),
    ("2026-10-23", 11.0, 11.0, 11.1, 10.9),
]


def base_pick(code="600000", sub="HOLD5_GRAIL"):
    return {"code": code, "name": "合成样本", "sub": sub,
            "verify_tier": "A", "buyable": True}


# ==========================================================================
# (a) forward_bars_after：严格晚于 T、升序、最多 n 根、不足时如实返回
# ==========================================================================
def test_a():
    section("(a) forward_bars_after：多日前向 bar 取数口径")

    f3 = tpo.forward_bars_after(BARS_FULL, "2026-10-22", 3)
    ck("取 3 根前向 bar", len(f3) == 3, str(f3))
    ck("严格晚于 T（T 日本身不入选）",
       all(b[0] > "2026-10-22" for b in f3) and f3[0][0] == "2026-10-23", str(f3))
    ck("按日期升序", [b[0] for b in f3] == sorted(b[0] for b in f3), str(f3))
    ck("默认 n=3", len(tpo.forward_bars_after(BARS_FULL, "2026-10-22")) == 3)

    f1 = tpo.forward_bars_after(BARS_FULL, "2026-10-22", 1)
    ck("n=1 只取 T+1 一根", len(f1) == 1 and f1[0][0] == "2026-10-23", str(f1))

    f_short = tpo.forward_bars_after(BARS_FULL, "2026-10-26", 3)
    ck("前向不足 3 根时如实返回实际根数（不臆造）",
       len(f_short) == 1 and f_short[0][0] == "2026-10-27", str(f_short))

    ck("末端无前向 bar 时返回空列表",
       tpo.forward_bars_after(BARS_FULL, "2026-10-27", 3) == [])
    ck("与 next_bar_after 首根一致（同一真相源）",
       tpo.next_bar_after(BARS_FULL, "2026-10-22")[0] == f3[0][0])


# ==========================================================================
# (b) measure_one 前向字段：与日线独立复算一致 + 既有字段零漂移
# ==========================================================================
def test_b():
    section("(b) measure_one 前向原料：t2/t3 逐日收益 + compute_metrics 零漂移")

    with patch_bars_map({"600000": BARS_FULL}):
        rec = tpo.measure_one("2026-10-22", base_pick(), True)

    ck("未涨停样本 status=ok", rec.get("status") == "ok", str(rec))
    ck("forward_status=complete（三根前向齐全）",
       rec.get("forward_status") == "complete", str(rec.get("forward_status")))
    # 生产代码按设计 round(..., 6) 落盘，断言必须用同口径舍入值，否则 9e-8 的
    # 浮点尾差会误报（不是生产缺陷）。
    ck("t2_close_ret = T+2/T+1 逐日收益（12.0/11.0-1）",
       eq(rec.get("t2_close_ret"), round(12.0 / 11.0 - 1.0, 6)),
       str(rec.get("t2_close_ret")))
    ck("t3_close_ret = T+3/T+2 逐日收益（13.0/12.0-1）",
       eq(rec.get("t3_close_ret"), round(13.0 / 12.0 - 1.0, 6)),
       str(rec.get("t3_close_ret")))
    ck("forward_dates 记录 T+2/T+3 日期",
       rec.get("forward_dates") == ["2026-10-26", "2026-10-27"],
       str(rec.get("forward_dates")))

    # 独立复算：不用被测函数，直接从合成 bars 手算
    t1, t2, t3 = BARS_FULL[2][2], BARS_FULL[3][2], BARS_FULL[4][2]
    ck("t2_close_ret 与独立复算一致",
       eq(rec.get("t2_close_ret"), round(t2 / t1 - 1.0, 6)))
    ck("t3_close_ret 与独立复算一致",
       eq(rec.get("t3_close_ret"), round(t3 / t2 - 1.0, 6)))

    # 既有字段零漂移：compute_metrics 返回集合必须完整保留在 rec 且值不变
    b1 = BARS_FULL[2]
    cm = tpo.compute_metrics("600000", "", 10.5, b1)
    ck("compute_metrics 字段集合完整保留在 measure_one 结果中",
       set(cm).issubset(set(rec)), str(set(cm) - set(rec)))
    drift = [k for k in cm if not eq(cm[k], rec.get(k))]
    ck("compute_metrics 全部既有字段逐值零漂移", len(drift) == 0, str(drift))
    ck("close_ret 仍为 T+1 单日收益（未被前向字段改写）",
       eq(rec["close_ret"], round(11.0 / 10.5 - 1.0, 6)), str(rec.get("close_ret")))

    # 新增字段必须只出现在 measure_one 层，compute_metrics 字段集合不变
    for k in ("t2_close_ret", "t3_close_ret", "forward_status", "forward_dates"):
        ck("新增前向字段 %s 不属于 compute_metrics（避免破坏 81 项锁）" % k,
           k not in cm)

    # 前向不足 3 根：字段为 None 且标 no_forward_data，绝不臆造
    bars_short = [
        ("2026-10-21", 10.0, 10.0, 10.0, 10.0),
        ("2026-10-22", 10.5, 10.5, 10.6, 10.4),
        ("2026-10-23", 11.0, 11.0, 11.1, 10.9),
    ]
    with patch_bars_map({"600000": bars_short}):
        rs = tpo.measure_one("2026-10-22", base_pick(), True)
    ck("前向不足 3 根 -> forward_status=no_forward_data",
       rs.get("forward_status") == "no_forward_data", str(rs.get("forward_status")))
    ck("前向不足时 t2/t3_close_ret 为 None（不臆造）",
       rs.get("t2_close_ret") is None and rs.get("t3_close_ret") is None, str(rs))
    ck("前向不足时 forward_dates 为 None", rs.get("forward_dates") is None)
    ck("前向不足时 T+1 收益仍正常产出（不因缺 T+2/T+3 丢样本）",
       rs.get("status") == "ok" and "close_ret" in rs, str(rs))

    # T 日已涨停：缺陷 A 排除分支不得产出任何前向字段
    with patch_bars_map({"600000": BARS_T_LIMITUP}):
        rl = tpo.measure_one("2026-10-22", base_pick(), True)
    ck("T 日已涨停 -> excluded_t_limitup（缺陷 A 仍生效）",
       rl.get("status") == "excluded_t_limitup", str(rl))
    ck("排除分支不产出前向字段",
       all(k not in rl for k in ("t2_close_ret", "t3_close_ret", "forward_status")))


# ==========================================================================
# (c) rescan_excluded：汇总层复核、幂等、不误杀、绝不删除行
# ==========================================================================
def _ok_row(code, date="2026-10-22", status="ok", delivered=True, **extra):
    r = {
        "date": date, "code": code, "name": "合成", "sub": "LIANBAN",
        "verify_tier": "A", "buyable": True, "delivered": delivered,
        "status": status, "r1_forced": False,
        "base_close": 10.5, "t1_close": 11.0,
        "open_ret": 0.04, "close_ret": 0.047619, "high_ret": 0.05,
        "low_ret": -0.01, "win": True, "hit_zt": False,
        "limit_pct": 0.1, "zt_price": 11.545,
        "src": "push", "src_file": "gone.json", "src_files": ["gone.json"],
    }
    r.update(extra)
    return r


def test_c():
    section("(c) rescan_excluded：历史 ok 行 retrospective 排除 + 幂等 + 不误杀")

    rows = [
        _ok_row("600000"),                                   # T 日涨停（应被改标）
        _ok_row("600001"),                                   # T 日未涨停（原样保留）
        {**_ok_row("600002"), "status": "excluded_t_limitup"},  # 已排除（跳过）
        {**_ok_row("600003"), "status": "pending"},         # pending（跳过）
    ]
    mapping = {
        "600000": BARS_T_LIMITUP,
        "600001": BARS_NOT_LIMITUP,
        "600002": BARS_T_LIMITUP,
        "600003": BARS_T_LIMITUP,
    }
    with patch_bars_map(mapping):
        out, n_fixed = tpo.rescan_excluded(rows)

    ck("返回行数不变（绝不删除任何行）", len(out) == len(rows), str(len(out)))
    ck("n_rescanned=1（只改标 1 条 ok 涨停行）", n_fixed == 1, str(n_fixed))

    r0 = out[0]
    ck("命中的 ok 行改标为 excluded_t_limitup", r0.get("status") == "excluded_t_limitup",
       str(r0.get("status")))
    ck("改标行保留原行全部既有字段",
       all(k in r0 for k in ("close_ret", "base_close", "delivered", "src_files")),
       str(sorted(r0.keys())))
    ck("改标行追加可追溯标记 excluded_by=rescan_excluded",
       r0.get("excluded_by") == "rescan_excluded", str(r0.get("excluded_by")))
    ck("改标行追加 T-1/T 收盘与日期审计字段",
       r0.get("t_prev_close") == 10.0 and r0.get("t_close") == 11.0
       and r0.get("t_prev_date") == "2026-10-21", str(r0))
    ck("改标行 note 说明「历史 ok 行 retrospective 排除」",
       "retrospective" in str(r0.get("note", "")), str(r0.get("note")))
    ck("delivered 事实未被改写（保留可追溯）", r0.get("delivered") is True)

    ck("未涨停 ok 行原样保留（不误杀）",
       out[1] is rows[1] or (out[1] == rows[1] and out[1].get("status") == "ok"),
       str(out[1].get("status")))
    ck("已 excluded 行跳过、不被二次改标",
       out[2].get("status") == "excluded_t_limitup"
       and "excluded_by" not in out[2], str(out[2]))
    ck("pending 行跳过", out[3].get("status") == "pending", str(out[3]))

    # 幂等：对已处理过的 out 再跑一次，不得再改标任何行
    with patch_bars_map(mapping):
        out2, n2 = tpo.rescan_excluded(out)
    ck("重复运行幂等：第二次 n_rescanned=0", n2 == 0, str(n2))
    ck("重复运行行数仍不变", len(out2) == len(rows), str(len(out2)))
    ck("重复运行不覆盖审计标记", out2[0].get("excluded_by") == "rescan_excluded")

    # 无日线 / 无 T-1：保守保留，不静默丢样本
    with patch_bars_map({"600000": []}):
        out3, n3 = tpo.rescan_excluded([_ok_row("600000")])
    ck("日线缺失时不改标（保守保留，不臆造）", n3 == 0 and out3[0].get("status") == "ok",
       str(n3))

    # 判定口径必须与 measure_one 一致（同一 is_limit_up）
    ck("rescan 判定与 measure_one 同源：is_limit_up(600000,10.0,11.0)=True",
       is_limit_up("600000", 10.0, 11.0) is True)
    ck("rescan 判定与 measure_one 同源：is_limit_up(600001,10.0,10.5)=False",
       is_limit_up("600001", 10.0, 10.5) is False)

    # 源码纪律：绝不删除行、绝不臆造成功判定
    src = textwrap.dedent(inspect.getsource(tpo.rescan_excluded))
    ck("rescan_excluded 源码不含删除行操作（.remove / del rows）",
       ".remove(" not in src and "del rows" not in src)
    ck("rescan_excluded 不做成功判定（不含 grail_success / PROMOTE / buy_score）",
       all(k not in src for k in ("grail_success", "PROMOTE", "buy_score")))


# ==========================================================================
# (d) 结构锁：rescan_excluded 已接入 run() 且在 summarize 之前
# ==========================================================================
def test_d():
    section("(d) 结构锁：rescan_excluded 接入 run() 且位于 summarize 之前")

    src = textwrap.dedent(inspect.getsource(tpo.run))
    ck("run() 源码含 rescan_excluded( 调用", "rescan_excluded(" in src)
    i_rescan = src.find("rescan_excluded(")
    i_merge = src.find("merge_rows(")
    i_sum = src.find("summarize(")
    ck("rescan_excluded 调用在 merge_rows 之后",
       0 <= i_merge < i_rescan, "merge=%d rescan=%d" % (i_merge, i_rescan))
    ck("rescan_excluded 调用在 summarize 之前（统一口径后才汇总）",
       0 <= i_rescan < i_sum, "rescan=%d summarize=%d" % (i_rescan, i_sum))
    ck("rescan 返回值回写 merged（merged, n_rescanned = ...）",
       "merged, n_rescanned" in src, "missing merged rebind")
    ck("summarize 消费的是 rescan 之后的 merged",
       src.find("summarize(merged)", i_rescan) > i_rescan)


# ==========================================================================
# (e) 端到端：run(write=False) 对历史 ok 涨停行 retrospective 排除，且不落盘
# ==========================================================================
def test_e():
    section("(e) 端到端：run(write=False) 离线预演生效且不覆盖台账")

    tmp = tempfile.mkdtemp(prefix="tpo_fwd_e2e_")
    cache_tmp = os.path.join(tmp, "cache")
    os.makedirs(cache_tmp, exist_ok=True)      # 空：无任何现存产出文件
    jl = os.path.join(tmp, "outcome.jsonl")
    sm = os.path.join(tmp, "outcome_summary.json")

    # 模拟 2026-11-02 002172 澳洋健康 LIANBAN：台账 status=ok、源文件已轮转删除、
    # revive_stale 不回溯 ok，缺陷 A 的 measure_one 排除对它无效 —— 只有 rescan 能收口。
    hist = _ok_row("600000")
    with open(jl, "w", encoding="utf-8") as f:
        f.write(json.dumps(hist, ensure_ascii=False) + "\n")

    saved = (tpo.CACHE, tpo.OUT_JSONL, tpo.OUT_SUM)
    tpo.CACHE, tpo.OUT_JSONL, tpo.OUT_SUM = cache_tmp, jl, sm
    try:
        with patch_bars_map({"600000": BARS_T_LIMITUP}):
            s = tpo.run(write=False, verbose=False)

        ck("历史 ok 涨停行被剔除出 delivered_ok（n=0）",
           s["n_delivered_ok"] == 0, str(s["n_delivered_ok"]))
        ck("n_excluded_t_limitup=1（汇总层复核确实生效）",
           s["n_excluded_t_limitup"] == 1, str(s["n_excluded_t_limitup"]))
        ck("n_ok=0（改标后不再是 ok）", s["n_ok"] == 0, str(s["n_ok"]))
        ck("overall_delivered 为空且不产生 edge 结论",
           s["overall_delivered"].get("n") == 0, str(s["overall_delivered"]))

        # write=False 绝不落盘
        with open(jl, "r", encoding="utf-8") as f:
            after = [json.loads(l) for l in f if l.strip()]
        ck("run(write=False) 不覆盖台账：仍是原始 1 行且 status=ok",
           len(after) == 1 and after[0]["status"] == "ok", str(after))
        ck("run(write=False) 不生成/不覆盖 summary 文件", not os.path.exists(sm))
        ck("discipline 文本仍判定样本不足（不放宽门槛）",
           "样本不足" in s["discipline"] or "天数不够" in s["discipline"],
           str(s["discipline"]))
    finally:
        tpo.CACHE, tpo.OUT_JSONL, tpo.OUT_SUM = saved
        shutil.rmtree(tmp, ignore_errors=True)


# ==========================================================================
# (f) forward_path_coverage：只报原料齐备度，不判定续涨成功
# ==========================================================================
def test_f():
    section("(f) forward_path_coverage：原料齐备度计数 + 纪律")

    rows = [
        _ok_row("600001", forward_status="complete"),
        _ok_row("600002", forward_status="complete"),
        _ok_row("600003", forward_status="no_forward_data"),
        _ok_row("600004"),                                   # 历史行：无该字段
        {**_ok_row("600005"), "status": "excluded_t_limitup"},
    ]
    s = tpo.summarize(rows)
    cov = s.get("forward_path_coverage")
    ck("summarize 暴露 forward_path_coverage", isinstance(cov, dict), str(cov))
    ck("n_delivered_ok=4（排除行不计入）", cov["n_delivered_ok"] == 4,
       str(cov["n_delivered_ok"]))
    ck("n_complete=2", cov["n_complete"] == 2, str(cov["n_complete"]))
    ck("n_no_forward_data=1", cov["n_no_forward_data"] == 1,
       str(cov["n_no_forward_data"]))
    ck("n_not_measured=1（新增字段前落盘的历史行）", cov["n_not_measured"] == 1,
       str(cov["n_not_measured"]))
    ck("三类计数之和 = n_delivered_ok",
       cov["n_complete"] + cov["n_no_forward_data"] + cov["n_not_measured"]
       == cov["n_delivered_ok"])
    ck("note 明确「不判定续涨成功」",
       "不判定续涨成功" in cov["note"], str(cov["note"]))
    ck("note 明确「不得与 grail_success 混用」",
       "grail_success" in cov["note"], str(cov["note"]))

    # 用「键集合」而非源码黑名单：注释里出现「不参与任何门控 / 调权 / PROMOTE」
    # 是纪律声明，字符串黑名单会把它误判成成功判定逻辑。键集合是精确的结构事实。
    ck("forward_path_coverage 键集合只含齐备度字段（无 success_rate / t_stat / verdict）",
       set(cov) == {"n_delivered_ok", "n_complete", "n_no_forward_data",
                    "n_not_measured", "note"},
       str(sorted(cov)))


def main():
    print("=" * 90)
    print("核验台账 续涨原料 + 汇总层排除收口 回归锁  (venv: %s)" % sys.executable)
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
