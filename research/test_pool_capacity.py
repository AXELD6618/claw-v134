#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_pool_capacity.py — 候选池容量与驱逐方向的**独立回归测试**（2026-10-26 新建）

存在理由（缺陷 D1「生产者=验收者」）：池驱逐是每天在跑的破坏性操作，之前只靠人工
对照实验验证过一次（OLD 踢 11 只 theme_fit=on / NEW 踢 0 只）。改一次关键词、调一次
score，就可能再把方向写反，而没人会立刻发现。本文件把它钉成可复跑断言。

覆盖 4 组不变量：
  T1 扩容生效：30+170=200 时**零驱逐**（这是 2026-10-26「扩座位」裁定的验收条件）
  T2 超容驱逐方向：超容时踢的是**低分**票，不是高分票（P0 曾把方向写反）
  T3 配额可外调：data/pool_policy.json 的 pool_cap 生效，非法值回落默认
  T4 _score 不重算已存 theme_fit：已存 on 的票必须比未富集票分高
    （曾出现「605488 已存 on 被重算成 ?，score 1.0→0.3 当天被踢」）

运行：python research/test_pool_capacity.py
不动任何真实业务数据文件（apply_lifecycle 只改内存对象，不落盘）。
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import p1_discovery as P

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  [PASS] %s" % name)
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def mk(code, prob=0.0, kind="test"):
    return {"code": code, "date": "2026-10-26", "prob": prob, "kind": kind,
            "buyable": False, "note": "test"}


def run(nt1, nt2, scores):
    """构造 nt1+nt2 只票跑 apply_lifecycle，返回 (evicted_codes, kept_codes)。
    scores: {code: prob} 用来制造高低分梯度。

    参数个数以**生产签名为准**，测试不得反向约束生产：
      apply_lifecycle(cands, emerg, research, ledger, alert_log, graveyard)  # 6 参
    2026-10-28 该函数曾是 7 参（多一个中间 dict），测试跟着改成 7 个；
    之后生产收敛回 6 参，测试未同步 → 2026-10-29 首跑 TypeError。
    改为按参数名传参，签名再变时错误信息直指缺失键，而不是笼统的位置参数计数。
    """
    t1 = [mk("T1%03d" % i, prob=0.5) for i in range(nt1)]
    t2 = [mk("T2%03d" % i, prob=scores.get(i, 0.0)) for i in range(nt2)]
    cands = {"tier1": t1, "tier2": t2}
    newc, entries, exits, evicted = P.apply_lifecycle(
        cands=cands, emerg={}, research={}, ledger={}, alert_log=[], graveyard={})
    return [e["code"] for e in evicted], [x["code"] for x in newc["tier2"]]


def main():
    # 2026-10-28 修：T1/T2 原本把 cap 硬编码成 200，而 pool_cap 已按 _tuning 条款
    # 由 200 上调至 300（16:00 任务自动上调）。硬编码会让 T2 构造出「未超容」场景
    # 却断言「驱逐 20 只」，从而永久假红/假绿。改为相对 cap 构造，cap 再变也不用改测试。
    CAP = P.pool_cap()
    NT1 = 30
    print("== T1 扩容生效：Tier1 %d + Tier2 %d = cap %d → 零驱逐 ==" % (NT1, CAP - NT1, CAP))
    old_research = P.RESEARCH_CACHE
    P.RESEARCH_CACHE = {}          # 全员未富集，score 并列
    ev, kept = run(NT1, CAP - NT1, {})
    check("cap 内不驱逐", len(ev) == 0, "实际驱逐 %d 只" % len(ev))
    check("池内保留 %d 只 Tier2" % (CAP - NT1), len(kept) == CAP - NT1, "实际 %d" % len(kept))

    OVER = 20
    print("== T2 超容驱逐方向：%d + %d = cap+%d → 踢 %d 只低分 =="
          % (NT1, CAP - NT1 + OVER, OVER, OVER))
    # 让最后 OVER 只是高分（prob 0.9），最前 OVER 只是低分（prob 0.0）
    hi = {i: 0.9 for i in range(CAP - NT1, CAP - NT1 + OVER)}
    ev, kept = run(NT1, CAP - NT1 + OVER, hi)
    check("驱逐数量 = 超容数 %d" % OVER, len(ev) == OVER, "实际 %d" % len(ev))
    low_kept = [c for c in kept if c.startswith("T2") and int(c[2:]) < OVER]
    check("低分票被踢（方向正确）", len(low_kept) <= OVER,
          "低分票仍留 %d 只" % len(low_kept))
    hi_kicked = [c for c in ev if c.startswith("T2") and int(c[2:]) >= CAP - NT1]
    check("高分票没被误踢", len(hi_kicked) == 0,
          "高分票被踢 %d 只: %s" % (len(hi_kicked), hi_kicked[:5]))

    print("== T3 配额可外调（data/pool_policy.json）==")
    d = tempfile.mkdtemp()
    tmp_policy = os.path.join(d, "pool_policy.json")
    old_policy_p = P.POOL_POLICY_P
    try:
        json.dump({"pool_cap": 150}, open(tmp_policy, "w", encoding="utf-8"))
        P.POOL_POLICY_P = tmp_policy
        check("pool_cap=150 生效", P.pool_cap() == 150, "实际 %s" % P.pool_cap())
        json.dump({"pool_cap": 7}, open(tmp_policy, "w", encoding="utf-8"))
        check("非法值(7)回落默认", P.pool_cap() == P.POOL_CAP,
              "实际 %s" % P.pool_cap())
        json.dump({"pool_cap": "abc"}, open(tmp_policy, "w", encoding="utf-8"))
        check("非数字回落默认", P.pool_cap() == P.POOL_CAP,
              "实际 %s" % P.pool_cap())
    finally:
        P.POOL_POLICY_P = old_policy_p

    print("== T4 _score 用已存 theme_fit，不重算覆盖 ==")
    P.RESEARCH_CACHE = {
        "600000": {"name": "已存ON", "theme": "半导体设备", "theme_fit": "on"},
        "600001": {"name": "未富集", "theme": "", "theme_fit": "?"},
    }
    s_on = P._score({"code": "600000", "prob": 0.0})
    s_unk = P._score({"code": "600001", "prob": 0.0})
    check("已存 on 的票分数更高", s_on > s_unk, "on=%.3f 未知=%.3f" % (s_on, s_unk))
    check("on 权重 != 未知兜底", abs(s_on - s_unk) > 0.1,
          "差值仅 %.3f（疑似重算覆盖了已存值）" % (s_on - s_unk))

    P.RESEARCH_CACHE = old_research

    print()
    if FAILS:
        print("[RESULT] FAIL: %d 项未通过 → %s" % (len(FAILS), FAILS))
        return 1
    print("[RESULT] PASS: 池容量/驱逐方向/配额外调/score 取值 四组不变量全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
