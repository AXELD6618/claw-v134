#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""活题材登记表刷新：读取情报研判 delta json，合并进 data/theme_registry.json。

由每日 16:30 主题情报自动化调用。agent 经 WebSearch 产出 themes_delta json：
  {theme_key: {status, weight, evidence, keywords?, name?}, ...}
支持：升温(hot)/降温(cold)/新增 emerging 题材。

绝不删除已有主题；降温仅改 status=warm/cold，并记入 demoted 列表（供 lifecycle 降级保护用）。
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import theme_intel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta-json", required=True, help="情报研判 delta {key: {status,weight,evidence,...}}")
    ap.add_argument("--note", default=None, help="覆盖 policy_note")
    args = ap.parse_args()

    delta = json.load(open(args.delta_json, encoding="utf-8"))
    reg = theme_intel.update_registry(delta, note=args.note)
    print("[registry] 刷新完成: 主题数=%d 降级列表=%s 点名boost=%s" %
          (len(reg["themes"]), reg.get("demoted"), reg.get("policy_boost")))
    for k, t in reg["themes"].items():
        print("  %-20s %-8s w=%.2f" % (k, t.get("status"), t.get("weight")))


if __name__ == "__main__":
    main()
