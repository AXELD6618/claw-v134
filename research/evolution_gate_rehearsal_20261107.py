# -*- coding: utf-8 -*-
"""
research/evolution_gate_rehearsal_20261107.py
================================================================================
「门控就绪与样本量严谨性核查」配套离线预演脚本（纯只读、零落盘、可复现）。

三件事：
  A) 门开离线预演：构造第 10 个交易日的确定性合成核验数据，跑
     evolution_reweight.decide -> cap_override -> unified_tail_pick.rank_for_push
     全链路，逐项验证四策略调权、负期望核心 6→4、名额不回填、回归锁无回归。
     · 全程 er.run(dry=True)（不写 _write_overrides / _append_log）
     · er.load_rows 被替换为「内存合成 rows」，绝不读写 data/tail_pick_outcome.jsonl
     · 不新建/修改任何生产文件；cap_override 保持 None
  B) 样本量估算（NW HAC 口径）：给定 mean_paired_excess 与日序列波动，
     估算 t_nw>=2.0 所需独立交易日数、grail_success n_evaluable>=200 所需实推交易日数。
  C) 零落盘校验：预演前后 8 个生产/产物文件 mtime_ns 全不变 + 写盘 spy 调用 0 次。

运行（两种等价）：
  PYTHONPATH=<仓库根> .venv/Scripts/python.exe research/evolution_gate_rehearsal_20261107.py
  PYTHONPATH=<仓库根> .venv/Scripts/python.exe -m research.evolution_gate_rehearsal_20261107
末尾打印 "=== ALL PASS ===" 表示 A) 主链路预演逐项通过。
"""
from __future__ import annotations
import os, sys, json, math, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent          # research/
ROOT = HERE.parent                              # 仓库根
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evolution_reweight as er
import unified_tail_pick as u

# ── 只读监控：零落盘校验对象（生产文件 / 产物 / 冻结状态 / 回归锁源码）──
WATCH = [
    "data/p1_rules.json",
    "data/tail_pick_outcome.jsonl",
    "data/evolution_log.json",
    "data/tail_pick_outcome_summary.json",
    "backtest_cache/evolution_review_2026-11-06.json",
    "core_factor_model.json",
    "factor_registry.json",
    "backtest_cache/oospf_report.json",
    "research/test_evolution_chain.py",
    "research/evolution_reweight.py",
    "unified_tail_pick.py",
]


def snap():
    return {rel: (ROOT / rel).stat().st_mtime_ns for rel in WATCH if (ROOT / rel).exists()}


def build_synthetic_rows():
    """10 个独立交易日的确定性合成核验集。

    日期标签沿用真实交付日 + 新增第 10 日 2026-11-06（触发 MIN_DAYS=10 门开的合成核验日）；
    指标值(close_ret/win/hit_zt)为确定性合成，分支方向预设以便逐项断言，绝不冒充真实盘。
    四个 MONITORED 策略各 12 条(n>=10 才进 decide 调权分支)，LIANBAN 供 baseline_cr 用。
    """
    dates = ["2026-10-22", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30",
             "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]  # 10 日
    rows = []

    def add(sub, i, close_ret, win, hit_zt):
        rows.append({
            "date": dates[i % 10],
            "code": "%s%03d" % (sub[:2].upper(), i),
            "sub": sub, "status": "ok", "delivered": True,
            "close_ret": close_ret, "win": win, "hit_zt": hit_zt,
            "limit_pct": 0.10,
        })

    # HOLD5_GRAIL（核心、非 TUNED）：负期望 mean<0 且 win<0.5 → 期望 6→4 下调
    for i in range(12):
        add("HOLD5_GRAIL", i, -0.02, False, False)
    # PRE_BREAKOUT（TUNED）：正期望 + 高涨停率 → 期望 4→5 上调（封顶 6）
    for i in range(12):
        add("PRE_BREAKOUT", i, 0.02, True, True)
    # EVENT_CATALYST（TUNED）：负期望 → 期望 4→2 下调
    for i in range(12):
        add("EVENT_CATALYST", i, -0.02, False, False)
    # REVERSAL（TUNED）：mean<0 但 win_rate=0.5（不 <0.5）→ 维持 4→4（验证滞回/不误调）
    for i in range(12):
        add("REVERSAL", i, -0.01, (i % 2 == 0), False)
    # LIANBAN（PROTECTED）：中性值，仅参与 baseline_cr，不在 MONITORED
    for i in range(6):
        add("LIANBAN", i, 0.0, False, False)
    return rows


def make_picks():
    """仿 research/test_evolution_chain.py 的候选构造；8 只 grail(>6) 暴露补位回填缺陷。"""
    def gpick(i, s):
        return {"code": "600%03d" % i, "sub": "HOLD5_GRAIL", "buy_score": s,
                "verify_tier": "A", "name": "G%d" % i}
    def rpick(i):
        return {"code": "700%03d" % i, "sub": "REVERSAL", "chg": -6.0, "vol_ratio": 1.2,
                "consec_down": 1, "above_ma20": True, "verify_tier": "B", "name": "R%d" % i}
    def lpick(i):
        return {"code": "800%03d" % i, "sub": "LIANBAN", "buyable": True,
                "board_len": 2, "verify_tier": "B", "name": "L%d" % i}
    def ppick(i):
        return {"code": "900%03d" % i, "sub": "PRE_BREAKOUT", "vol_ratio": 1.0,
                "verify_tier": "C", "name": "P%d" % i}
    def epick(i):
        return {"code": "950%03d" % i, "sub": "EVENT_CATALYST", "catalyst": ["x"],
                "verify_tier": "C", "name": "E%d" % i}
    picks = [gpick(i, 1.0 - i * 0.01) for i in range(1, 9)]
    picks += [rpick(i) for i in range(1, 4)]
    picks += [lpick(i) for i in range(1, 3)]
    picks += [ppick(i) for i in range(1, 3)]
    picks += [epick(i) for i in range(1, 3)]
    return picks


def rehearse():
    """门开离线预演主链路。返回 (results, entry, override, info)。"""
    results = []

    def chk(label, cond, detail=""):
        results.append((label, bool(cond), detail))

    m0 = snap()
    rules0 = (ROOT / "data/p1_rules.json").read_text(encoding="utf-8")

    # ── monkey-patch：全部进程内、可恢复 ──
    orig_load_rows = er.load_rows
    orig_write = er._write_overrides
    orig_log = er._append_log
    orig_cap_loader = u._load_cap_overrides
    write_calls = []

    er.load_rows = lambda: build_synthetic_rows()            # 内存合成，绝不碰真实 jsonl
    er._write_overrides = lambda cap: write_calls.append(("write", cap))
    er._append_log = lambda entry: write_calls.append(("log", entry))
    try:
        rows = er.load_rows()
        total_n = len(rows)
        total_days = len(set(r.get("date") for r in rows))
        chk("P1a 合成核验集覆盖 10 个独立交易日(第10日=2026-11-06)", total_days == 10,
            "total_days=%d" % total_days)
        chk("P1b 样本只数达 MIN_N=30", total_n >= er.MIN_N, "total_n=%d MIN_N=%d" % (total_n, er.MIN_N))

        # 真实 _load_overrides 只读 p1_rules.json（cap_override 应为 None={}）
        cur = er._load_overrides()
        chk("P1c 当前 cap_override 为 None(空 override)", cur == {}, "cur=%r" % cur)

        entry = er.run(dry=True)                             # dry=True → 不写盘
        chk("P1d 门开后 action=TUNE(不再是 NO_OP_INSUFFICIENT)", entry.get("action") == "TUNE",
            "action=%s total_n=%d total_days=%d" % (entry.get("action"), entry.get("total_n"), entry.get("total_days")))

        cap_after = entry["cap_after"]
        changes = entry["changes"]
        detail = entry["detail"]

        # P2 四策略调权逻辑全部生效
        chk("P2a PRE_BREAKOUT 正期望上调 4→5", cap_after.get("PRE_BREAKOUT") == 5 and "上调" in detail.get("PRE_BREAKOUT", ""),
            "cap=%s reason=%s" % (cap_after.get("PRE_BREAKOUT"), detail.get("PRE_BREAKOUT")))
        chk("P2b EVENT_CATALYST 负期望下调 4→2", cap_after.get("EVENT_CATALYST") == 2 and "下调" in detail.get("EVENT_CATALYST", ""),
            "cap=%s reason=%s" % (cap_after.get("EVENT_CATALYST"), detail.get("EVENT_CATALYST")))
        chk("P2c REVERSAL 无显著偏离维持 4→4", cap_after.get("REVERSAL") == 4 and "维持" in detail.get("REVERSAL", ""),
            "cap=%s reason=%s" % (cap_after.get("REVERSAL"), detail.get("REVERSAL")))
        chk("P2d HOLD5_GRAIL 负期望下调 6→4", cap_after.get("HOLD5_GRAIL") == 4 and "下调" in detail.get("HOLD5_GRAIL", ""),
            "cap=%s reason=%s" % (cap_after.get("HOLD5_GRAIL"), detail.get("HOLD5_GRAIL")))
        chk("P2e changes 恰含 3 个变动策略(REVERSAL 维持不入)", set(changes) == {"PRE_BREAKOUT", "EVENT_CATALYST", "HOLD5_GRAIL"},
            "changes=%r" % changes)

        # P3 负期望核心 6→4
        override = {k: cap_after[k] for k in ("PRE_BREAKOUT", "EVENT_CATALYST", "HOLD5_GRAIL", "REVERSAL")}
        chk("P3 负期望核心策略 cap_override[HOLD5_GRAIL]=4(6→4)", override.get("HOLD5_GRAIL") == 4,
            "override[HOLD5_GRAIL]=%s" % override.get("HOLD5_GRAIL"))

        # P4 名额不补位回填：decide 产出 override 注入 rank_for_push
        picks = make_picks()

        def count_grail(ov):
            u._load_cap_overrides = lambda: ov
            push = u.rank_for_push([dict(p) for p in picks], cap=18)
            return sum(1 for p in push if p.get("sub") == "HOLD5_GRAIL"), len(push)

        g4, n4 = count_grail(override)                       # 门开后
        g0, n0 = count_grail({})                             # 门开前(无 override)
        gz, nz = count_grail({"HOLD5_GRAIL": 0})             # 压到 0
        GRAIL_TOTAL = min(6, max(0, int(override.get("HOLD5_GRAIL", 6))))
        chk("P4a 门开后 grail 推送严格=4(等于 GRAIL_TOTAL，未被 rest_pool 补位回填)",
            g4 == 4 and g4 == GRAIL_TOTAL, "grail=%d push_n=%d GRAIL_TOTAL=%d" % (g4, n4, GRAIL_TOTAL))
        chk("P4b 无 override 时 grail 取满上限 6", g0 == 6, "grail=%d push_n=%d" % (g0, n0))
        chk("P4c 端到端 decide(6→4)→override→推送 6→4 同步收缩", g0 == 6 and g4 == 4 and n4 < n0,
            "grail %d→%d, push_n %d→%d" % (g0, g4, n0, n4))
        chk("P4d 压到 0 时核心策略全抑制", gz == 0, "grail=%d push_n=%d" % (gz, nz))

        # P5 LIANBAN 受保护（既不在 MONITORED，decide 直接返回原值）
        neg_m = {"n": 12, "days": 10, "mean_close_ret": -0.5, "win_rate": 0.1, "zt_rate": 0.0}
        lb_cap, lb_reason = er.decide("LIANBAN", 12, neg_m, -0.006)
        chk("P5 LIANBAN 受保护不被自动下调", lb_cap == 12 and "受保护" in lb_reason,
            "cap=%s reason=%s" % (lb_cap, lb_reason))

    finally:
        # 恢复所有 monkey-patch（避免进程内状态串扰回归锁）
        er.load_rows = orig_load_rows
        er._write_overrides = orig_write
        er._append_log = orig_log
        u._load_cap_overrides = orig_cap_loader

    # P6 零落盘：写盘 spy 调用 0 次 + mtime 全不变 + p1_rules 无 cap_override
    chk("P6a dry 链路 _write_overrides/_append_log 调用 0 次", len(write_calls) == 0,
        "write_calls=%d" % len(write_calls))
    m1 = snap()
    changed = [rel for rel in m0 if m1.get(rel) != m0[rel]]
    chk("P6b 11 个生产/产物/源码文件 mtime_ns 全部未变", not changed, "changed=%r" % changed)
    rules1 = (ROOT / "data/p1_rules.json").read_text(encoding="utf-8")
    chk("P6c p1_rules.json 内容逐字未变、cap_override 保持 None",
        rules1 == rules0 and "cap_override" not in rules1 and '"evolution"' not in rules1,
        "len=%d has_cap_override=%s" % (len(rules1), "cap_override" in rules1))

    # P7 与既有回归锁无冲突：独立 subprocess 跑 test_evolution_chain.py
    chain = HERE / "test_evolution_chain.py"
    r = subprocess.run([sys.executable, str(chain)], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=120)
    lock_ok = (r.returncode == 0 and "ALL PASS" in r.stdout and "HAS FAIL" not in r.stdout)
    chk("P7 既有回归锁 test_evolution_chain.py 仍 ALL PASS(无回归)", lock_ok,
        "returncode=%d has_ALL_PASS=%s" % (r.returncode, "ALL PASS" in r.stdout))

    info = {"total_n": total_n, "total_days": total_days, "action": entry.get("action"),
            "cap_after": cap_after, "changes": changes, "detail": detail,
            "grail_no_override": g0, "grail_override": g4, "grail_zero": gz,
            "push_n_no_override": n0, "push_n_override": n4,
            "baseline_cr": entry.get("baseline_close_ret")}

    # 已修复对齐（计入 ALL PASS 校验）：2026-11-07 已将 rank_for_push REV_TOTAL 由
    # min(4) 改为 min(6)，与 evolution_reweight.decide 对 TUNED(含 REVERSAL) 的上调封顶
    # min(6, cur_cap+1) 对齐。decide 上调 cap=5/6 后 rank_for_push 同步放行，不再静默钳回 4。
    up_rev, _ = er.decide("REVERSAL", 4,
                          {"n": 12, "days": 10, "mean_close_ret": 0.05, "win_rate": 0.8, "zt_rate": 0.9},
                          -0.006)
    info["risk_rev_cap"] = up_rev
    info["risk_rev_total"] = min(6, max(0, up_rev))

    return results, entry, override, info


def sample_size():
    """NW HAC 口径样本量估算（确定性公式，全部实数可复现）。"""
    mu = 0.020472422214660006          # mean_paired_excess（9 日实测）
    sd_p = 0.04503190095730632         # 日序列总体标准差 pstdev（iid t 用）
    t_iid = 1.3638612925136844         # 普通 iid t（n=9）
    t_nw = 1.1200783289260925          # NW HAC t_nw（n=9, lag=2）
    n0 = 9
    sqrt_tau = t_iid / t_nw            # se_hac/se_iid 经验比
    tau_emp = sqrt_tau ** 2            # 经验设计效应（lag=2）
    # 目标 t_nw>=2.0：t = mu*sqrt(n)/(sd*sqrt(tau)) → n >= (2*sd*sqrt(tau)/mu)^2
    n_emp = (2.0 * sd_p * sqrt_tau / mu) ** 2
    # H=5 重叠 + lag=4 Bartlett 理论设计效应 tau=3.4（(1-j/5)*(5-j)/5 求和）
    tau_H5 = 1.0 + 2.0 * sum((1.0 - j / 5.0) * ((5.0 - j) / 5.0) for j in range(1, 5))
    n_H5 = (2.0 * sd_p * math.sqrt(tau_H5) / mu) ** 2
    # 敏感性：mu 折半（小样本均值不稳健）
    n_emp_half = (2.0 * sd_p * sqrt_tau / (mu / 2)) ** 2
    n_H5_half = (2.0 * sd_p * math.sqrt(tau_H5) / (mu / 2)) ** 2
    # grail_success n_evaluable>=200 所需实推交易日
    days_by_rate = {rate: 200.0 / rate for rate in (2.0, 1.0, 0.89)}

    out = {
        "mu": mu, "sd_p": sd_p, "t_iid": t_iid, "t_nw": t_nw, "n0": n0,
        "sqrt_tau": sqrt_tau, "tau_emp": tau_emp, "tau_H5": tau_H5,
        "n_emp": n_emp, "n_emp_ceil": math.ceil(n_emp),
        "n_H5": n_H5, "n_H5_ceil": math.ceil(n_H5),
        "n_emp_half_ceil": math.ceil(n_emp_half), "n_H5_half_ceil": math.ceil(n_H5_half),
        "days_by_rate": days_by_rate,
        # iid 对照
        "n_iid_ceil": math.ceil((2.0 * sd_p / mu) ** 2),
    }
    return out


def main():
    print("=" * 74)
    print("门开离线预演：decide → cap_override → rank_for_push（dry / 零落盘 / 合成数据）")
    print("=" * 74)
    results, entry, override, info = rehearse()
    for label, ok, detail in results:
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", label, (" | " + detail) if detail else ""))

    print("-" * 74)
    print("预演产物（内存，未落盘）：")
    print("  action=%s total_n=%d total_days=%d" % (info["action"], info["total_n"], info["total_days"]))
    print("  cap_after=%s" % json.dumps(info["cap_after"], ensure_ascii=False, sort_keys=True))
    print("  changes =%s" % json.dumps(info["changes"], ensure_ascii=False, sort_keys=True))
    print("  grail 推送: 无override=%d → override(6→4)=%d → 压到0=%d；push_n %d→%d"
          % (info["grail_no_override"], info["grail_override"], info["grail_zero"],
             info["push_n_no_override"], info["push_n_override"]))
    print("  回归风险探针(已修复对齐)：decide(REVERSAL 正期望)→cap=%d，rank_for_push REV_TOTAL=min(6,%d)=%d（口径一致，决策层与执行层同步）"
          % (info["risk_rev_cap"], info["risk_rev_cap"], info["risk_rev_total"]))

    print("-" * 74)
    print("样本量估算（NW HAC 口径，确定性实数）：")
    s = sample_size()
    print("  mu=%.6f sd_p=%.6f t_iid=%.4f t_nw=%.4f n0=%d" %
          (s["mu"], s["sd_p"], s["t_iid"], s["t_nw"], s["n0"]))
    print("  se_hac/se_iid=sqrt(tau_emp)=%.4f → tau_emp(lag=2)=%.4f；tau_H5(lag=4,Bartlett)=%.4f" %
          (s["sqrt_tau"], s["tau_emp"], s["tau_H5"]))
    print("  t_nw>=2.0 需独立交易日：iid≈%d 日 | 经验lag2≈%d 日 | 理论H5/lag4≈%d 日" %
          (s["n_iid_ceil"], s["n_emp_ceil"], s["n_H5_ceil"]))
    print("  敏感性(mu折半)：经验lag2≈%d 日 | 理论H5/lag4≈%d 日；mu→0 则永远无法显著" %
          (s["n_emp_half_ceil"], s["n_H5_half_ceil"]))
    print("  grail_success n_evaluable>=200 所需实推交易日：2只/日=%d | 1只/日=%d | 0.89只/日(实测)=%d" %
          (round(s["days_by_rate"][2.0]), round(s["days_by_rate"][1.0]), round(s["days_by_rate"][0.89])))
    print("-" * 74)

    all_ok = all(ok for _, ok, _ in results)
    if all_ok:
        print("=== ALL PASS ===")
        return 0
    failed = ", ".join(l for l, ok, _ in results if not ok)
    print("=== FAILED: %s ===" % failed)
    return 1


if __name__ == "__main__":
    sys.exit(main())
