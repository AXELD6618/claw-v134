#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · Layer D 引信触发 → 微信推送。
只组装 Layer D 专属标题/正文，统一交给「永远通」通道 push_channel.send() 发送。
不另写发送逻辑（符合 push_channel / push_resend 的设计原则）。
"""
import sys, os, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))          # research/
ROOT = os.path.dirname(HERE)                                # Claw 根（push_channel.py 所在）
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import push_channel as pc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report",
                    default=os.path.join(ROOT, "backtest_cache", "spring_soe_layerD_report.txt"),
                    help="watch 生成的报告文件")
    ap.add_argument("--note", default="弹簧压到底·LayerD引信监控")
    a = ap.parse_args()

    body = open(a.report, encoding="utf-8").read().strip()
    today = datetime.date.today().strftime("%Y-%m-%d")
    title = "【弹簧压到底 · Layer D 引信监控】%s" % today
    ok, channel, detail, n_try = pc.send(title, body, a.note)
    print("PUSH ok=%s channel=%s tries=%d detail=%s" % (ok, channel, n_try, detail))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
