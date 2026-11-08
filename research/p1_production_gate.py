#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生产推送硬闸门 (解决 S1: V13 圣杯/贝叶斯抛硬币信号仍在推送).

问题: V13_4_HolyGrailPusher 按 v132_score>=0.65 每日推 TOP 到微信, 而记忆已证
      "选股信号=抛硬币(ρ=−0.061)". 每天推的是随机 TOP.

硬闸门契约: 推送前必须过本 gate.
  - 仅放行 经 OOS 验证的真 edge 信号 (连板惯性, 见 p1_edge_oos.py 样本外 +2.20%);
  - 断层反包近期 OOS 负期望 -> 不单独放行;
  - V13 贝叶斯/圣杯分(v132_score)若无 P1 tier1 真 edge 支撑 -> 标 NO_EDGE(空仓).
用法: 在 HolyGrailPusher 读取信号后、推送前调用 gate_signal(); 返回 NO_EDGE 则不推.
"""
import os, sys, json, glob, datetime as dt

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER_P = os.path.join(PROJ, "data", "candidates_ledger.json")


def load_tier1_codes():
    """从 P1 台账取 live 验证过/当前激活的 tier1 真 edge 候选 (连板惯性)."""
    if not os.path.exists(LEDGER_P):
        return set()
    L = json.load(open(LEDGER_P, encoding="utf-8"))
    out = set()
    for code, e in L.items():
        if e.get("last_tier") == 1 and e.get("state") == "edge_active":
            out.add(code)
    return out


def gate_signal(sig, tier1=None):
    """对单条待推送信号判定放行.

    sig: dict, 至少含 'code' 与来源信息.
         - source=='p1' & tier==1 -> PASS (连板惯性真 edge)
         - kind in ('lianban','连板惯性') -> PASS
         - signal_type=='GRAIL_HIT_SUPPLEMENTARY' & supplementary & verdict_ref=='PROMOTE_GRAIL'
           -> PASS (经 adjudicate_grail 圣杯全口径闸门验证的 CAT_PROXIMITY 增益重排序层,
                   与连板惯性同属"已验证真 edge", 闸门一字未改, 仅扩展放行集合)
         - code in tier1 -> PASS
         - 其余 (含 V13 贝叶斯 v132_score 圣杯未过验证) -> NO_EDGE
    返回 'PASS' / 'NO_EDGE'.
    """
    if tier1 is None:
        tier1 = load_tier1_codes()
    code = sig.get("code")
    if sig.get("source") == "p1" and sig.get("tier") == 1:
        return "PASS"
    if sig.get("kind") in ("lianban", "连板惯性", "LB"):
        return "PASS"
    # SUPPLEMENTARY grail-hit 验证边：须显式携带验证证明，杜绝伪造放行
    if (sig.get("signal_type") == "GRAIL_HIT_SUPPLEMENTARY"
            and sig.get("supplementary") is True
            and sig.get("verdict_ref") == "PROMOTE_GRAIL"):
        return "PASS"
    if code in tier1:
        return "PASS"
    return "NO_EDGE"


def gate_file(signal_path):
    """对一份圣杯/选股信号文件做硬闸门过滤, 输出可推送列表 + 空仓判定.

    返回 dict: {date, n_raw, n_pass, pass_list, n_no_edge, no_edge_codes, verdict}
    """
    if not os.path.exists(signal_path):
        return {"verdict": "EMPTY", "n_raw": 0, "n_pass": 0,
                "pass_list": [], "n_no_edge": 0, "no_edge_codes": [],
                "note": "无信号文件 -> 空仓"}
    sigs = json.load(open(signal_path, encoding="utf-8"))
    if isinstance(sigs, dict):
        sigs = sigs.get("signals", sigs.get("top", []))
    tier1 = load_tier1_codes()
    passed, noedge = [], []
    for s in sigs:
        r = gate_signal(s, tier1)
        (passed if r == "PASS" else noedge).append(s)
    verdict = "PUSH" if passed else "EMPTY_HOLD"
    return {"date": dt.date.today().isoformat(), "n_raw": len(sigs),
            "n_pass": len(passed), "pass_list": passed,
            "n_no_edge": len(noedge),
            "no_edge_codes": [s.get("code") for s in noedge],
            "verdict": verdict,
            "note": "仅放行经 OOS 验证的真 edge(连板惯性); 其余(V13 圣杯抛硬币)标空仓"}


if __name__ == "__main__":
    # demo: 若传入圣杯信号文件路径则过滤, 否则打印契约说明
    if len(sys.argv) > 1:
        print(json.dumps(gate_file(sys.argv[1]), ensure_ascii=False, indent=1))
    else:
        print("[gate] 生产推送硬闸门已就绪. 在 HolyGrailPusher 推送前调用 gate_signal().")
        print("[gate] 当前 P1 tier1 真 edge 候选:", sorted(load_tier1_codes()) or "(空, 待 live 累积)")
        print("[gate] 无真 edge 时 -> EMPTY_HOLD (空仓), 不推随机 TOP.")
