# -*- coding: utf-8 -*-
"""缺陷#1 回归锁：送达事实可追溯化（delivery_status / deliberate_hold / hold_reason）。

验证：
  - selection_push.derive_delivery_status 六分支（dry / failed / delivered /
    delivered_empty×regime_abstain / delivered_empty×defensive_rotation / delivered_empty×no_signal）
  - tail_pick_outcome.classify_delivery_status 新字段优先 + 老记录回退
不依赖任何生产运行，纯函数断言。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import selection_push as SP
import tail_pick_outcome as TO


def main():
    ok = True
    def chk(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            print("  [FAIL] %s" % msg)
        else:
            print("  [PASS] %s" % msg)

    # ── derive_delivery_status ──
    chk(SP.derive_delivery_status(False, 0, True) == ("dry", False, "dry_run"),
        "dry 分支")
    chk(SP.derive_delivery_status(False, 0, False) == ("failed", False, "push_send_failed"),
        "failed 分支")
    chk(SP.derive_delivery_status(True, 3, False) == ("delivered", False, ""),
        "delivered 分支(push_n=3)")
    chk(SP.derive_delivery_status(True, 0, False, regime_blocked=True,
                                 regime_detail="REGIME_BLOCK:breadth=30%") ==
        ("delivered_empty", True, "regime_abstain:REGIME_BLOCK:breadth=30%"),
        "delivered_empty + regime_abstain（主动零名额·制度门）")
    chk(SP.derive_delivery_status(True, 0, False, dr_active=True) ==
        ("delivered_empty", True, "defensive_rotation_active"),
        "delivered_empty + defensive_rotation_active（主动零名额·防御轮动）")
    chk(SP.derive_delivery_status(True, 0, False) ==
        ("delivered_empty", False, "no_signal"),
        "delivered_empty + no_signal（真实无信号，非主动零名额）")

    # ── classify_delivery_status（新字段优先）──
    rec_new = {"push_ok": True, "dry": False, "push": [],
               "delivery_status": "delivered_empty", "deliberate_hold": True,
               "hold_reason": "regime_abstain:x"}
    c = TO.classify_delivery_status(rec_new)
    chk(c["delivery_status"] == "delivered_empty" and c["deliberate_hold"] is True
        and c["legacy"] is False and c["delivered"] is False,
        "classify 新字段优先：主动零名额可识别")
    rec_ok = {"push_ok": True, "dry": False, "push": [{"code": "000001"}],
              "delivery_status": "delivered", "deliberate_hold": False, "hold_reason": ""}
    c2 = TO.classify_delivery_status(rec_ok)
    chk(c2["delivered"] is True and c2["legacy"] is False,
        "classify 新字段：delivered 对齐 resolve_delivered")

    # ── classify_delivery_status（老记录回退）──
    rec_legacy_empty = {"push_ok": True, "dry": False, "push": []}
    c3 = TO.classify_delivery_status(rec_legacy_empty)
    chk(c3["delivery_status"] == "delivered_empty" and c3["legacy"] is True
        and c3["deliberate_hold"] is False,
        "classify 老记录回退：delivered_empty（legacy 标记，无法区分主动/无信号）")
    rec_legacy_fail = {"push_ok": False, "dry": False, "push": []}
    chk(TO.classify_delivery_status(rec_legacy_fail)["delivery_status"] == "failed",
        "classify 老记录回退：failed")
    rec_legacy_dry = {"push_ok": False, "dry": True, "push": []}
    chk(TO.classify_delivery_status(rec_legacy_dry)["delivery_status"] == "dry",
        "classify 老记录回退：dry")

    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
