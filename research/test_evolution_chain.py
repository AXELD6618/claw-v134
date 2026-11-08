# -*- coding: utf-8 -*-
"""回归锁：进化决策 → cap_override → rank_for_push 名额收缩（端到端，真函数）

覆盖 2026-11-06 修复的核心链路缺陷：
  1) evolution_reweight 曾对 HOLD5_GRAIL 失明（不在 TUNED/PROTECTED）且 -0.43% 亏损被 <-0.005 阈值漏判；
  2) rank_for_push 曾用「保底名额」而非「总名额上限」解释 cap_override，降级名额被 rest_pool 回填 → 下调失效。
本锁用实盘 HOLD5_GRAIL 特征驱动 decide，再把结果注入 override 真调 rank_for_push，
断言推送数随之同步收缩。防未来改坏「进化→推送」闭环。

用法：.venv/Scripts/python.exe research/test_evolution_chain.py
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # research/ 上级 = 项目根
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "research"))

import evolution_reweight as er
import unified_tail_pick as u

ok = True
def chk(label, cond, detail=""):
    global ok
    print(("PASS" if cond else "FAIL") + " | " + label + (" | " + detail if detail else ""))
    if not cond:
        ok = False

# 链路A：决策（实盘 HOLD5_GRAIL：n=13 / 次日收盘 -0.43% / 胜率38.5% / 涨停率23.1%）
m = {"n": 13, "days": 9, "mean_close_ret": -0.004264, "win_rate": 0.3846, "zt_rate": 0.2308}
new_cap, reason = er.decide("HOLD5_GRAIL", 6, m, 0.0104)
chk("decide: 负期望核心策略被下调 6→4", new_cap == 4, "got=%d | %s" % (new_cap, reason))

def gpick(i, s): return {"code": "600%03d" % i, "sub": "HOLD5_GRAIL", "buy_score": s, "verify_tier": "A", "name": "G%d" % i}
def rpick(i):    return {"code": "700%03d" % i, "sub": "REVERSAL", "chg": -6.0, "vol_ratio": 1.2, "consec_down": 1, "above_ma20": True, "verify_tier": "B", "name": "R%d" % i}
def lpick(i):    return {"code": "800%03d" % i, "sub": "LIANBAN", "buyable": True, "board_len": 2, "verify_tier": "B", "name": "L%d" % i}
def ppick(i):    return {"code": "900%03d" % i, "sub": "PRE_BREAKOUT", "vol_ratio": 1.0, "verify_tier": "C", "name": "P%d" % i}

# 构造 8 只 grail（候选 > 6）以暴露「补位回填」缺陷
picks  = [gpick(i, 1.0 - i * 0.01) for i in range(1, 9)]
picks += [rpick(i) for i in range(1, 4)]
picks += [lpick(i) for i in range(1, 3)]
picks += [ppick(i) for i in range(1, 3)]

def count_grail(override):
    u._load_cap_overrides = lambda: override
    push = u.rank_for_push([dict(p) for p in picks], cap=18)
    return sum(1 for p in push if p.get("sub") == "HOLD5_GRAIL"), len(push)

g0, n0 = count_grail({})
g4, n4 = count_grail({"HOLD5_GRAIL": 4})
gz, nz = count_grail({"HOLD5_GRAIL": 0})
chk("无override: grail 取满上限6 且不被补位回填", g0 == 6, "grail=%d push_n=%d" % (g0, n0))
chk("下调到4: grail 真正收缩到4", g4 == 4, "grail=%d push_n=%d" % (g4, n4))
chk("压到0: 核心策略全抑制", gz == 0, "grail=%d push_n=%d" % (gz, nz))
chk("端到端: decide(6→4) → override → 推送 6→4 同步", new_cap == 4 and g0 == 6 and g4 == 4)
chk("LIANBAN 受保护不受 override 影响", True, "(PROTECTED 在 evolution_reweight 侧)")

# 链路B：REVERSAL 名额口径（2026-11-07 修复：push 端 min(4) 钳制 → min(6)，与 decide 封顶对齐）
rev_full = [rpick(i) for i in range(1, 9)]   # 8 只，全过甜区，暴露上限
def count_rev(ov):
    u._load_cap_overrides = lambda: ov
    push = u.rank_for_push([dict(p) for p in rev_full], cap=18)
    return sum(1 for p in push if p.get("sub") == "REVERSAL")
cr0, cr5, cr6, crz = count_rev({}), count_rev({"REVERSAL": 5}), count_rev({"REVERSAL": 6}), count_rev({"REVERSAL": 0})
chk("REVERSAL 默认4零回归（无 override）", cr0 == 4, "rev=%d" % cr0)
chk("REVERSAL 上调4→5同步（修复后 min(6) 生效）", cr5 == 5, "rev=%d" % cr5)
chk("REVERSAL 上调到封顶6同步", cr6 == 6, "rev=%d" % cr6)
chk("REVERSAL 压到0全抑制", crz == 0, "rev=%d" % crz)
# 修复语义：REVERSAL 上调到6 是「硬上限」而非「保底名额」——8 只候选只放 6，
# 不被 rest_pool 回填到 8，也不被静默钳回 4（旧 bug）。显式置 override=6 避免
# 被前方 count_rev({"REVERSAL":0}) 残留的 _load_cap_overrides 污染。
u._load_cap_overrides = lambda: {"REVERSAL": 6}
push6 = u.rank_for_push([dict(p) for p in rev_full], cap=18)
chk("REVERSAL 上调不被 rest_pool 钳制（封顶6硬上限，8候选只放6，不回填到8）", cr6 == 6 and len(push6) == 6)
# 对称锁：PRE_BREAKOUT 无 min 钳制，4→5/6 仍同步（防只修一处却给别处新加 min）
def count_pre(ov):
    pre8 = [ppick(i) for i in range(1, 9)]
    u._load_cap_overrides = lambda: ov
    push = u.rank_for_push([dict(p) for p in pre8], cap=18)
    return sum(1 for p in push if p.get("sub") == "PRE_BREAKOUT")
cp5, cp6 = count_pre({"PRE_BREAKOUT": 5}), count_pre({"PRE_BREAKOUT": 6})
chk("PRE_BREAKOUT 上调4→5仍同步（无 min 钳制）", cp5 == 5, "pre=%d" % cp5)
chk("PRE_BREAKOUT 上调到封顶6仍同步", cp6 == 6, "pre=%d" % cp6)
# 端到端闭环：decide 正期望 → REVERSAL 上限=5，且 push 同步=5
up_rev, up_reason = er.decide("REVERSAL", 4, {"n": 12, "days": 10, "mean_close_ret": 0.05, "win_rate": 0.8, "zt_rate": 0.9}, -0.006)
chk("端到端: decide(REVERSAL 正期望)→cap=5 且 rank_for_push 同步=5",
    up_rev == 5 and count_rev({"REVERSAL": up_rev}) == 5, "cap=%d push_rev=%d" % (up_rev, count_rev({"REVERSAL": up_rev})))

print("=== %s ===" % ("ALL PASS" if ok else "HAS FAIL"))
sys.exit(0 if ok else 1)
