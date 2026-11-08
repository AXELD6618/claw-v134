# -*- coding: utf-8 -*-
"""
research/test_oospf_nw.py — OOS t 估计量修复（iid → 日配对超额上的 NW HAC）验收测试
================================================================================
运行（PYTHONPATH 需含仓库根；脚本顶部亦已自行 insert）：
    PYTHONPATH=<仓库根> .venv/Scripts/python.exe -m research.test_oospf_nw
或：
    .venv/Scripts/python.exe research/test_oospf_nw.py

只读约束：本测试只读 backtest_cache/oospf_report.json、core_factor_model.json、
factor_registry.json，绝不修改；绝不调用 oospf_validator.run_all()（不扫 5225 CSV、
不覆写生产报告）。逐条打印 PASS/FAIL，全部通过末尾打印 "=== ALL PASS ==="。
"""
from __future__ import annotations
import math
import json
import sys
import py_compile
from pathlib import Path
import numpy as np

PROJ = Path(__file__).resolve().parent.parent
if str(PROJ) not in sys.path:
    sys.path.insert(0, str(PROJ))

from research.nw_stats import nw_tstat, stationary_bootstrap_t
import oospf_validator as v

NW_STATS = PROJ / "research" / "nw_stats.py"
OOSPF = PROJ / "oospf_validator.py"
REPORT = PROJ / "backtest_cache" / "oospf_report.json"
MODEL = PROJ / "core_factor_model.json"
REGISTRY = PROJ / "factor_registry.json"


def make_overlap(seed=7, n=513, sigma=0.01, target_naive_t=3.0):
    """构造 H=5 重叠（MA(4)）日度序列：x_t = mean(eps[t:t+5])。
    先去均值再反推漂移 MU，使 naive iid t 精确等于 target_naive_t。"""
    rng = np.random.default_rng(seed)
    eps = rng.normal(0.0, sigma, n + 4)
    eps -= eps.mean()
    x = np.array([eps[t:t + 5].mean() for t in range(n)])
    x -= x.mean()
    sd = x.std(ddof=1)
    return x + target_naive_t * sd / math.sqrt(n)


def t1():
    """T1 误晋级拦截：合成重叠数据 naive t=3.0 会被误判 PROMOTE，NW t 必须 <2.0。"""
    pe = make_overlap(seed=7, n=513, sigma=0.01, target_naive_t=3.0)
    sd = pe.std(ddof=1)
    n = pe.size
    se_iid = sd / math.sqrt(n)
    t_naive = pe.mean() / se_iid
    t_hac, se_hac = nw_tstat(pe, 4)
    assert abs(t_naive - 3.0) < 1e-9, "naive iid t 未精确=3.000: %r" % t_naive
    assert t_hac < 2.0, "NW t 未压到 2.0 以下（误晋级未拦截）: %.4f" % t_hac
    assert se_hac / se_iid > 1.5, "HAC se 未显著放大: %.4f" % (se_hac / se_iid)
    t_def, _ = nw_tstat(pe)                       # 默认 lag 应为 4
    assert abs(t_def - t_hac) < 1e-12, "默认 lag 不等于 4"
    return ("naive iid t=%.3f(误 PROMOTE) -> NW HAC t=%.3f(<2.0, 正确 REJECT)；"
            "se 放大 %.3fx；默认 lag=4 与显式一致"
            % (t_naive, t_hac, se_hac / se_iid))


def t2():
    """T2 不误伤：纯白噪声 10 seed，HAC/IID se 比值均值须落在 0.85~1.20。"""
    ratios = []
    for s in range(10):
        w = np.random.default_rng(s).normal(0.0, 0.01, 513)
        _, seh = nw_tstat(w, 4)
        sei = w.std(ddof=1) / math.sqrt(w.size)
        ratios.append(seh / sei)
    r = np.array(ratios)
    mean_r = float(r.mean())
    worst_idx = int(np.argmax(np.abs(r - 1.0)))
    worst = float(abs(r[worst_idx] - 1.0))
    assert 0.85 <= mean_r <= 1.20, "白噪声 mean(se_hac/se_iid)=%.4f 越界" % mean_r
    return ("10 seed mean(se_hac/se_iid)=%.4f (0.85~1.20)；"
            "最坏单次偏差=%.4f (seed=%d, ratio=%.4f)；min=%.4f max=%.4f"
            % (mean_r, worst, worst_idx, r[worst_idx], r.min(), r.max()))


def t3():
    """T3 修复后真实数据：random 空对照仍跑输基准(ms<mb)，且 NW-HAC t 不再被 iid 误放大（|t|<2）。只读报告。"""
    assert REPORT.exists(), "缺 backtest_cache/oospf_report.json"
    mtime0 = REPORT.stat().st_mtime_ns
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    h = rep["factors"]["random"]["horizons"]["5"]      # 注意 key 是字符串 '5'，不是 'H5'
    ms, mb, t_old = h["mean_sig_test"], h["mean_base_test"], h["t_stat"]
    assert ms < mb, "random 空对照应跑输基准: %.4f vs %.4f" % (ms, mb)
    assert abs(t_old) < 2.0, "修复后 NW-HAC t 应 |t|<2（空对照不再被 iid 误放大）: %r" % t_old
    # 近零但含 MA(4) 自相关的日配对超额序列：修复后空对照不再显著
    z = make_overlap(seed=11, n=513, sigma=0.01, target_naive_t=0.0)
    tz, _ = nw_tstat(z, 4)
    assert abs(tz) < 1.0, "近零 MA(4) 序列 |NW t| 应 <1: %.6f" % abs(tz)
    assert REPORT.stat().st_mtime_ns == mtime0, "oospf_report.json 被修改（只读约束违反）"
    return ("只读报告: random mean_sig=%.4f < mean_base=%.4f；NW-HAC t=%.3f (|t|<2，空对照不再被 iid 误放大)；"
            "近零 MA(4) 序列 |NW t|=%.6f<1；报告 mtime 未变（未修改）"
            % (ms, mb, t_old, abs(tz)))


def t10():
    """T10 门 B 完整化产物校验：报告含 white_rc_p/dsr/mean_sig_test_net，_pe_h5 不泄漏，
    且 H5 裁定全部 REJECT（无因子靠 stale/漏门 PROMOTE）。只读报告。"""
    assert REPORT.exists(), "缺 backtest_cache/oospf_report.json"
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    fac = ["R31_breakout", "ylim", "R32_d2", "random", "oversold_sig", "oversold_shallow", "oversold_deep"]
    assert "white_rc_p" in rep and isinstance(rep["white_rc_p"], (int, float)), "报告缺 white_rc_p"
    leak = False
    n_reject = 0
    for f in fac:
        h5 = rep["factors"][f]["horizons"]["5"]
        for k in ("dsr", "mean_sig_test_net", "white_rc_p"):
            assert k in h5, "因子 %s H5 缺字段 %s" % (f, k)
        if "_pe_h5" in h5:
            leak = True
        if str(rep["factors"][f]["verdict_H5"]).startswith("REJECT"):
            n_reject += 1
        # WhiteRC 加严项须进裁定理由（样本不足的早期 REJECT 除外）
        if rep["factors"][f]["verdict_H5"] == "REJECT(样本不足)":
            continue
        assert "WhiteRC" in rep["factors"][f]["verdict_reason_H5"], \
            "因子 %s 裁定理由未含 WhiteRC 加严项: %s" % (f, rep["factors"][f]["verdict_reason_H5"])
    assert not leak, "_pe_h5 泄漏进产物 schema"
    assert n_reject == len(fac), "存在非 REJECT 裁定（门 B 漏门）: %d/%d" % (n_reject, len(fac))
    return ("产物校验: white_rc_p=%.3f；7 因子 H5 字段齐全、_pe_h5 未泄漏、裁定全 REJECT（含 WhiteRC 加严）；"
            "只读未修改" % rep["white_rc_p"])


def t4():
    """T4 不误杀真 edge：强漂移序列 NW t 仍须 >=2.0。"""
    pe = make_overlap(seed=7, n=513, sigma=0.01, target_naive_t=6.0)
    t_hac, _ = nw_tstat(pe, 4)
    assert t_hac >= 2.0, "强漂移 NW t 应 >=2.0（真 edge 被误杀）: %.4f" % t_hac
    return "强漂移(2x T1)序列 NW t=%.3f >=2.0，真 edge 不误杀" % t_hac


def t5():
    """T5 退化输入 (nan,nan) 不抛异常；adjudicate(t=None/nan) 判 REJECT 不抛异常。"""
    bad_inputs = [
        ("n<5", np.array([1.0, 2.0])),
        ("常数", np.full(10, 0.5)),
        ("全 NaN", np.full(10, np.nan)),
        ("空数组", np.array([], dtype=float)),
    ]
    for label, x in bad_inputs:
        r = nw_tstat(x, 4)
        assert len(r) == 2 and all(math.isnan(q) for q in r), \
            "%s 未返回 (nan,nan): %r" % (label, r)
    # 基线 t=2.5 且其余门槛都过 → PROMOTE，用以证明保护不是「一律 REJECT」
    base = dict(n_events_test=250, hit_multiple=1.20, pf_sig_test=1.40,
                mean_sig_test=0.60, mean_base_test=0.30, t_stat=2.5)
    ok_verdict, _ = v.adjudicate(base)
    assert ok_verdict == "PROMOTE", "基线应 PROMOTE: %s" % ok_verdict
    for bad in (None, float("nan")):
        r = dict(base)
        r["t_stat"] = bad
        verdict, reason = v.adjudicate(r)
        assert verdict.startswith("REJECT"), "t=%r 未判 REJECT: %s" % (bad, verdict)
        assert "t=" in reason, "t=%r 未进入 reasons 明细" % bad
    return ("4 类退化输入均 (nan,nan) 不抛异常；adjudicate(t=None/nan) 均 REJECT 不抛且进 reasons；"
            "基线 t=2.5 仍 PROMOTE（保护不是一律拒）")


def t6():
    """T6 源码级断言门槛未改；冻结状态未改。"""
    src = OOSPF.read_text(encoding="utf-8")
    i = src.index("def adjudicate(")
    j = src.find("\ndef ", i + 1)
    body = src[i:j] if j > 0 else src[i:]
    for token in ("1.15", "1.30", "0.20", "2.0"):
        assert token in body, "adjudicate 门槛 %s 被改动/缺失" % token
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    assert model.get("n_factors") == 0, "core_factor_model.n_factors 非 0"
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert reg.get("mining_frozen") is True, "factor_registry.mining_frozen 非 true"
    return "adjudicate 仍含 1.15/1.30/0.20/2.0；n_factors=0；mining_frozen=true"


def t7():
    """T7 平稳块自助交叉校验：p_two_sided>0.05，与 NW t=1.645 一致。"""
    pe = make_overlap(seed=7, n=513, sigma=0.01, target_naive_t=3.0)
    t_boot, p_two, se_boot = stationary_bootstrap_t(pe)
    t_hac, _ = nw_tstat(pe, 4)
    assert p_two > 0.05, "bootstrap p_two 应 >0.05（与 NW t<2 一致）: %.4f" % p_two
    assert abs(t_boot - t_hac) < 0.5, "bootstrap t 与 NW t 量级不一致: %.3f vs %.3f" % (t_boot, t_hac)
    return ("p_two_sided=%.4f >0.05；t_boot=%.3f 与 NW t=%.3f 一致；se_boot=%.8f"
            % (p_two, t_boot, t_hac, se_boot))


def t8():
    """T8 py_compile 三个新建/编辑/测试文件。"""
    targets = [NW_STATS, OOSPF, Path(__file__).resolve()]
    for p in targets:
        py_compile.compile(str(p), doraise=True)
    return "py_compile 通过：" + ", ".join(p.name for p in targets)


def t9():
    """T9 门 B 完整化：DSR/WhiteRC/成本后作为"可选加严"接入 adjudicate，向后兼容。"""
    base = dict(n_events_test=250, hit_multiple=1.20, pf_sig_test=1.40,
                mean_sig_test=0.60, mean_base_test=0.30, t_stat=2.5)
    # 向后兼容：无新增字段 → 仍 PROMOTE
    vd, _ = v.adjudicate(base)
    assert vd == "PROMOTE", "无新增字段应仍 PROMOTE: %s" % vd
    # DSR 加严：0.50 < 0.95 → REJECT
    r1 = dict(base); r1["dsr"] = 0.50
    v1, reason1 = v.adjudicate(r1)
    assert v1.startswith("REJECT") and "DSR" in reason1, "DSR<0.95 未 REJECT: %s | %s" % (v1, reason1)
    # DSR 通过：0.99 → 仍 PROMOTE
    r2 = dict(base); r2["dsr"] = 0.99
    v2, _ = v.adjudicate(r2)
    assert v2 == "PROMOTE", "DSR>=0.95 应 PROMOTE: %s" % v2
    # WhiteRC 加严：p=0.50 >= 0.10 → REJECT
    r3 = dict(base); r3["white_rc_p"] = 0.50
    v3, reason3 = v.adjudicate(r3)
    assert v3.startswith("REJECT") and "WhiteRC" in reason3, "WhiteRC p>=0.10 未 REJECT: %s | %s" % (v3, reason3)
    # WhiteRC 通过：p=0.01 → 仍 PROMOTE
    r4 = dict(base); r4["white_rc_p"] = 0.01
    v4, _ = v.adjudicate(r4)
    assert v4 == "PROMOTE", "WhiteRC p<0.10 应 PROMOTE: %s" % v4
    # 成本后加严：net 间隙 <= 20bp（gross 仍够）→ REJECT
    r5 = dict(base); r5["mean_sig_test_net"] = 0.45   # 0.45-0.30=0.15 <= 0.20
    v5, reason5 = v.adjudicate(r5)
    assert v5.startswith("REJECT") and "均值间隙" in reason5, "成本后间隙<=20bp 未 REJECT: %s | %s" % (v5, reason5)
    # 成本后通过：net 间隙 > 20bp → 仍 PROMOTE
    r6 = dict(base); r6["mean_sig_test_net"] = 0.55
    v6, _ = v.adjudicate(r6)
    assert v6 == "PROMOTE", "成本后间隙>20bp 应 PROMOTE: %s" % v6
    return ("门 B 加严：DSR<0.95 / WhiteRC p>=0.10 / 成本后间隙<=20bp 均 REJECT；"
            "DSR>=0.95 / WhiteRC p<0.10 / 成本后间隙>20bp 仍 PROMOTE；无新增字段向后兼容 PROMOTE")


def t11():
    """T11 门 B 完整化(收口)：Purged-CV 接入为可选加严 AND，向后兼容。
    单元：purged_cv_stats 正向折占比口径正确；集成：报告 7 因子 H5 含 purged_cv_* 字段，
    且 Purged-CV 不翻转既有 REJECT（仅加严）。只读报告。"""
    # 单元：恒定正序列 → 全部折正向
    pos = np.full(250, 0.001)
    r_pos = v.purged_cv_stats(pos, 5, 5, 4)
    assert r_pos["pos_frac"] == 1.0, "恒定正序列 pos_frac 应=1.0: %r" % r_pos["pos_frac"]
    # 单元：仅在少数折正向（前 2 折正、后 3 折负）→ 时序不稳应被检出 pos_frac<0.60
    arr = np.concatenate([np.full(100, 0.01), np.full(100, 0.01),
                          np.full(150, -0.01), np.full(150, -0.01), np.full(100, -0.01)])
    r_mix = v.purged_cv_stats(arr, 5, 5, 4)
    assert math.isfinite(r_mix["pos_frac"]) and r_mix["pos_frac"] < v.PURGED_CV_POS_FRAC_MIN, \
        "不稳序列 pos_frac 应 <%.2f: %r" % (v.PURGED_CV_POS_FRAC_MIN, r_mix["pos_frac"])
    # 单元：极小输入 → nan（不抛）
    r_tiny = v.purged_cv_stats(np.full(8, 0.001), 5, 5, 4)
    assert not math.isfinite(r_tiny["pos_frac"]), "极小输入应返回 nan: %r" % r_tiny["pos_frac"]
    # 集成：报告 7 因子 H5 含 purged_cv 字段，且不翻转既有 REJECT
    assert REPORT.exists(), "缺 backtest_cache/oospf_report.json"
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    fac = ["R31_breakout", "ylim", "R32_d2", "random", "oversold_sig", "oversold_shallow", "oversold_deep"]
    n_reject = 0
    for f in fac:
        h5 = rep["factors"][f]["horizons"]["5"]
        for k in ("purged_cv_pos_frac", "purged_cv_frac_pos_t", "purged_cv_fold_means",
                  "purged_cv_fold_ts", "purged_cv_n"):
            assert k in h5, "因子 %s H5 缺 Purged-CV 字段 %s" % (f, k)
        if str(rep["factors"][f]["verdict_H5"]).startswith("REJECT"):
            n_reject += 1
    assert n_reject == len(fac), "存在非 REJECT 裁定（Purged-CV 不应翻转）: %d/%d" % (n_reject, len(fac))
    return ("Purged-CV 单元：恒定正 pos_frac=1.0；不稳序列 pos_frac=%.2f<%.2f(被检出)；极小输入 nan 不抛；"
            "集成：7 因子 H5 含 purged_cv_* 字段、裁定全 REJECT（仅加严不翻转）；只读未修改"
            % (r_mix["pos_frac"], v.PURGED_CV_POS_FRAC_MIN))


def main():
    tests = [("T1", t1), ("T2", t2), ("T3", t3), ("T4", t4),
             ("T5", t5), ("T6", t6), ("T7", t7), ("T8", t8), ("T9", t9), ("T10", t10), ("T11", t11)]
    results = []
    for name, fn in tests:
        try:
            detail = fn()
            print("[%s] PASS | %s" % (name, detail))
            results.append(True)
        except AssertionError as e:
            print("[%s] FAIL | %s" % (name, e))
            results.append(False)
        except Exception as e:                       # noqa: BLE001 — 验收脚本需捕获一切异常
            print("[%s] FAIL | 未预期异常 %r" % (name, e))
            results.append(False)
    if all(results):
        print("=== ALL PASS ===")
        return 0
    failed = ", ".join(n for (n, _), ok in zip(tests, results) if not ok)
    print("=== FAILED: %s ===" % failed)
    return 1


if __name__ == "__main__":
    sys.exit(main())
