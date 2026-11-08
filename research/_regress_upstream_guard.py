#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""回归测试：上游守卫 step_upstream_fresh 分层校验。

锁死两次修复（防止回归）：
  2026-10-27 — 可选上游 V14 tail_pick 缺失 → 只跳过 HOLD5_GRAIL，**不得**拦主线真 edge。
  2026-10-29 — 静态参考集 spring_soe_layerB.json（周度刷新的国资实控人宇宙，仅作
               COILED_SPRING 成员资格过滤）不得按日频容差 >3 天硬拦整日。
               实测事故：该文件 4 天未更新（周度节奏正常）→ 主线 LIANBAN A 级已推送后，
               守卫又补推一条"今日不推"，用户同时收到两条矛盾消息。

判定表：
  A 关键齐全新鲜 + V14 缺失            → 放行
  B 核心上游缺失                        → 拦截
  C 核心上游陈旧(>3天)                  → 拦截
  D 弹簧静态宇宙 5 天未更新             → 放行（周度节奏，不得拦）
  E 弹簧静态宇宙 60 天未更新            → 拦截（>45天容差＝周度任务彻底坏了）

用法：.venv/Scripts/python.exe research/_regress_upstream_guard.py
"""
import importlib.util, os, sys, tempfile, datetime, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "run_unified_14h50.py")

TODAY = datetime.date(2026, 10, 27)
# 全部上游（建文件用）；前 3 项为日频核心，第 4 项为静态/周度参考集
FILES = [
    "data/discovery_candidates.json",
    "backtest_cache/lianban_watchlist.json",
    "data/pool_lifecycle.json",
    "backtest_cache/spring_soe_layerB.json",
]
STATIC_SET = "backtest_cache/spring_soe_layerB.json"


def load_mod():
    spec = importlib.util.spec_from_file_location("ruh_test", SPEC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _write(p, content="{}"):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def _set_mtime(p, d):
    ts = datetime.datetime(d.year, d.month, d.day, 12, 0, 0).timestamp()
    os.utime(p, (ts, ts))


def _setup(tmp, stale_days=None):
    """stale_days: {相对路径: 天数}，未列出的按今天算新鲜。"""
    stale_days = stale_days or {}
    for rel in FILES:
        p = os.path.join(tmp, rel)
        _write(p)
        d = TODAY - datetime.timedelta(days=stale_days.get(rel, 0))
        _set_mtime(p, d)
    # 不创建 tail_raw / tail_pick → 模拟 V14 缺失
    os.makedirs(os.path.join(tmp, "backtest_cache"), exist_ok=True)


def run_case(name, mutate=None, stale_days=None, expect_ok=None, expect_kw=None):
    tmp = tempfile.mkdtemp()
    try:
        _setup(tmp, stale_days)
        if mutate:
            mutate(tmp)
        m = load_mod()
        m.HERE = tmp
        m.CACHE = os.path.join(tmp, "backtest_cache")
        r = m.step_upstream_fresh("2026-10-27")
        good = r[0] is expect_ok and (expect_kw is None or expect_kw in r[1])
        print("[test] %s %s 期望ok=%s 实得ok=%s | %s"
              % ("OK  " if good else "FAIL", name, expect_ok, r[0], r[1]))
        return good
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ok = True
    # A: 关键齐全新鲜 + V14 缺失 → 放行
    ok &= run_case("A  全部新鲜+V14缺失→放行", expect_ok=True)
    # B: 核心上游缺失 → 拦截
    ok &= run_case("B  发现层缺失→拦截",
                   mutate=lambda t: os.remove(os.path.join(t, "data/discovery_candidates.json")),
                   expect_ok=False, expect_kw="缺失")
    # C: 核心上游陈旧 → 拦截
    ok &= run_case("C  连板台账陈旧6天→拦截",
                   stale_days={"backtest_cache/lianban_watchlist.json": 6},
                   expect_ok=False, expect_kw="陈旧")
    # D: 弹簧静态宇宙 5 天未更新 → 放行（本次修复核心）
    ok &= run_case("D  弹簧宇宙5天未更新→放行", stale_days={STATIC_SET: 5},
                   expect_ok=True)
    # E: 弹簧静态宇宙 60 天未更新 → 拦截（真坏）
    ok &= run_case("E  弹簧宇宙60天未更新→拦截", stale_days={STATIC_SET: 60},
                   expect_ok=False, expect_kw="陈旧")
    print("[regress_upstream_guard] %s" % ("ALL PASS" if ok else "HAS FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
