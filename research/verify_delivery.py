#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
独立投递验收器 —— 「生产者不得自证」在推送层的落地。

背景（2026-10-27 事故）：
  push_channel.send() 的"对话框主通道"实为 print 到自动化 stdout + 写一行
  backtest_cache/push_dialog_{date}.jsonl 凭据。写文件必然成功，于是
  delivered=true / channel=DIALOG 恒定成立，而用户实际零收到。
  当日 14:44 选出 600838/603028，日志宣称已送达，微信一条未发 → 一个交易日的
  可执行买点白丢。根因不是通道坏了（微信 iLink 当日 5 次保活全成功），
  而是"投递方自己判定投递成功"。

本验收器独立于 push_channel：
  - 只看两份落盘凭据的交叉一致性，不读 push_channel 的返回码；
  - 只有「存在真实的 ILINK 成功记录且时间差在阈值内」才认定 DELIVERED。

判定口径：
  NO_TAIL_PUSH       今日无尾盘类推送记录（可能是合法降级，见 degraded_push_{date}.json）
  DELIVERED          有尾盘记录，且 <=LATE_MIN 分钟内存在 ILINK ok=True
  LATE_DELIVERED     有尾盘记录，ILINK 成功但迟到 >LATE_MIN 分钟（仍记 fault）
  SELF_ATTESTED_ONLY 有尾盘记录，全天无任何 ILINK 成功 —— 自证式投递，必然用户未收到

退出码：0 = DELIVERED / NO_TAIL_PUSH（后者需人工确认非静默失败）
        1 = SELF_ATTESTED_ONLY / LATE_DELIVERED

用法：
  .venv/Scripts/python.exe research/verify_delivery.py                 # 默认今天
  .venv/Scripts/python.exe research/verify_delivery.py --date 2026-10-27
  .venv/Scripts/python.exe research/verify_delivery.py --json
"""
import os
import sys
import json
import argparse
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "backtest_cache")

# 视为"尾盘决策类"的标题关键词（与 channel_policy.json 的 mirror_titles 对齐）
KEY_TITLES = ("统一选股", "尾盘决策", "尾盘选股", "终极兜底")
LATE_MIN = 10          # 允许的迟到分钟数；14:50 硬墙钟，超过这个等于当日作废


def _read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def _ts(s):
    try:
        return datetime.datetime.fromisoformat(str(s))
    except Exception:
        return None


def verify(date):
    d8 = date.replace("-", "")
    dialog = _read_jsonl(os.path.join(CACHE, "push_dialog_%s.jsonl" % d8))
    channel = _read_jsonl(os.path.join(CACHE, "push_channel_%s.jsonl" % d8))

    # 尾盘类推送凭据
    tails = [r for r in dialog
             if any(k in str(r.get("title") or "") for k in KEY_TITLES)]
    # 真实送达凭据：ILINK 且 ok=True
    ilink = [r for r in channel
             if str(r.get("channel") or "").upper() == "ILINK" and r.get("ok")]

    # 14:40 引擎交付凭据（可能自称 delivered=true）
    dlv_path = os.path.join(CACHE, "delivered_14h50_%s.json" % date)
    claimed = {}
    if os.path.exists(dlv_path):
        try:
            claimed = json.load(open(dlv_path, encoding="utf-8"))
        except Exception:
            claimed = {}

    detail = []
    if not tails:
        verdict = "NO_TAIL_PUSH"
        detail.append("今日 push_dialog 中无尾盘类标题记录（关键词：%s）" % "/".join(KEY_TITLES))
        # 检查是否为合法降级
        deg = os.path.join(CACHE, "degraded_push_%s.json" % date)
        if os.path.exists(deg):
            try:
                gj = json.load(open(deg, encoding="utf-8"))
                detail.append("存在降级告知记录：reason=%s ok=%s channel=%s" % (
                    gj.get("reason"), gj.get("ok"), gj.get("channel")))
            except Exception:
                pass
        else:
            detail.append("且无 degraded_push 记录 → 需排查是否为静默失败")
    else:
        worst = None
        for t in tails:
            tts = _ts(t.get("ts"))
            best = None
            for r in ilink:
                rts = _ts(r.get("ts"))
                if not tts or not rts:
                    continue
                gap = (rts - tts).total_seconds() / 60.0
                if best is None or abs(gap) < abs(best):
                    best = gap
            detail.append("尾盘凭据 %s title=%s → 最近 ILINK %s" % (
                t.get("ts"), str(t.get("title"))[:40],
                ("无" if best is None else "差 %.0f 分钟" % best)))
            if best is None:
                lvl = 3
            elif best <= LATE_MIN:
                lvl = 1
            else:
                lvl = 2
            if worst is None or lvl > worst:
                worst = lvl
        verdict = {1: "DELIVERED", 2: "LATE_DELIVERED", 3: "SELF_ATTESTED_ONLY"}[worst or 3]

    # 假阳性识别：系统自称已交付，但验收判定未真实送达
    false_positive = bool(claimed.get("delivered")) and verdict in (
        "SELF_ATTESTED_ONLY", "NO_TAIL_PUSH", "LATE_DELIVERED")

    return {
        "date": date,
        "verdict": verdict,
        "false_positive_claim": false_positive,
        "claimed": {"delivered": claimed.get("delivered"), "channel": claimed.get("channel"),
                    "detail": claimed.get("detail"), "ts": claimed.get("ts")} if claimed else None,
        "tail_records": len(tails),
        "ilink_ok_records": len(ilink),
        "detail": detail,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = verify(a.date)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print("=" * 56)
        print("投递独立验收 · %s" % r["date"])
        print("=" * 56)
        print("判定        : %s" % r["verdict"])
        print("宣称已交付  : %s" % (r["claimed"] or "无记录"))
        print("假阳性认定  : %s" % ("是 —— 生产者自证，用户实际未收到" if r["false_positive_claim"] else "否"))
        print("尾盘凭据数  : %d    ILINK成功数: %d" % (r["tail_records"], r["ilink_ok_records"]))
        for line in r["detail"]:
            print("  · %s" % line)
        print("=" * 56)
    return 0 if r["verdict"] in ("DELIVERED", "NO_TAIL_PUSH") else 1


if __name__ == "__main__":
    sys.exit(main())
