#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
_regress_r1_deny.py — R1 deny-list 降级回归验证。

验证：position_deny_list.json 命中项在 unified_tail_pick 推送链路中被强制降 C 级 5%，
且未命中项不受影响；空表/无关项不误杀。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import unified_tail_pick as U  # noqa: E402


def main():
    # 合成 picks：003021(A)、603728(B) 命中 deny-list；600838(A) 未命中
    picks = [
        {"code": "003021", "name": "兆威机电", "sub": "LIANBAN", "verify_tier": "A"},
        {"code": "603728", "name": "鸣志电器", "sub": "B", "verify_tier": "B"},
        {"code": "600838", "name": "上海九百", "sub": "A", "verify_tier": "A"},
    ]
    U._apply_deny_list(picks)

    ok = True
    # 1) 003021 强制 C + 标记 deny_reason
    if picks[0].get("verify_tier") != "C" or "deny_reason" not in picks[0]:
        print("[FAIL] 003021 应强制 C 且带 deny_reason，实得 %s" % picks[0])
        ok = False
    else:
        print("[OK] 003021 → C 级 | %s" % picks[0]["deny_reason"])
    # 2) 603728 强制 C + 标记 deny_reason
    if picks[1].get("verify_tier") != "C" or "deny_reason" not in picks[1]:
        print("[FAIL] 603728 应强制 C 且带 deny_reason，实得 %s" % picks[1])
        ok = False
    else:
        print("[OK] 603728 → C 级 | %s" % picks[1]["deny_reason"])
    # 3) 600838 不受影响
    if picks[2].get("verify_tier") != "A" or "deny_reason" in picks[2]:
        print("[FAIL] 600838 不应被降级，实得 %s" % picks[2])
        ok = False
    else:
        print("[OK] 600838 保持 A 级（未命中 deny-list）")

    # 4) _load_deny_list 返回非空且含 2 只
    dl = U._load_deny_list()
    if set(dl.keys()) != {"003021", "603728"}:
        print("[FAIL] _load_deny_list 内容异常: %s" % dl)
        ok = False
    else:
        print("[OK] _load_deny_list 含 %d 只: %s" % (len(dl), dl))

    print("[regress R1] %s" % ("ALL PASS" if ok else "HAS FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
