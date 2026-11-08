#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
research/avoid_signals.py — 冲高回落型(moves)回避信号护栏

背景（已 OOS 实证，2026-10-28）：
  moves_sig（盘中最高涨幅 high_pct>=5% 且 收盘涨幅 pct<2%）经 moves_oversold_oos 实证为
  **反向回避信号**：PF_t5=0.825(<随机真及格线 1.259)、t=-20.13(显著跑输)、二元方向反
  （有信号箱 -0.0075 vs 无信号箱 +0.0024）、严重度反序(monoS=-0.96)。
  本质 = 派发/走弱信号：冲高回落预示后续走弱。用户确认"不该选、漏选是对的"。

本模块把 moves 固化为**回避信号**，供 selection_push 在盘前/盘中/尾盘统一防误选：
  任何候选若命中 moves_sig（尤其 clo_pos 低=高危派发），从推送池剔除。

纯函数、可单测、零副作用。不修改 buy_score（仅作排除护栏）。
"""
from __future__ import annotations


def moves_signal(snap) -> bool:
    """判定单只 live snapshot 是否命中 moves_sig（冲高回落）。

    snap 字段（来自 gtimg，见 p1_discovery._gtimg_batch）：
      prev_close(float), open(float), high(float), pct(float, 当日涨跌幅%),
      name(str, 用于退市/ST 硬审查)。
    返回 True = 命中冲高回落（高>=5% 且 收<2%）。
    """
    if not isinstance(snap, dict):
        return False
    prev = snap.get("prev_close") or 0.0
    high = snap.get("high") or 0.0
    # 兼容两种涨跌幅字段：gtimg 用 pct、盘中快照用 chg；两者均为百分数（如 -1.5 / 3.2）。
    pct = snap.get("pct")
    if pct is None:
        pct = snap.get("chg")
    if not prev or pct is None:
        return False
    try:
        pct = float(pct)
        if abs(pct) > 1.0:           # 百分数（如 -1.5）→ 转分数 0.02 阈值才对
            pct = pct / 100.0
        high_pct = (float(high) - float(prev)) / float(prev)
    except Exception:
        return False
    if high_pct >= 0.05 and pct < 0.02:
        return True
    return False


def clo_pos(snap) -> float:
    """收盘位置 = close/high（1=收在最高，0=收在最低）。低值=派发特征更强。"""
    if not isinstance(snap, dict):
        return 0.0
    close = snap.get("price") or snap.get("close") or 0.0
    high = snap.get("high") or 0.0
    try:
        close = float(close); high = float(high)
    except Exception:
        return 0.0
    if high <= 0:
        return 0.0
    return max(0.0, min(1.0, close / high))


def is_moves_avoid(code, snap) -> bool:
    """returns True = 该候选应被回避（命中冲高回落反向信号）。

    命中 moves_sig 即回避；clo_pos<0.7 时标记为"高危派发"等级（强回避）。
    用法（selection_push）：对每只候选取其 live snapshot 调本函数，True 则剔除。
    """
    if moves_signal(snap):
        return True
    return False


def moves_reverse_strength(snap) -> float:
    """0~1 的回避强度：命中 moves_sig 且 clo_pos 越低越强（派发概率越高）。"""
    if not moves_signal(snap):
        return 0.0
    cp = clo_pos(snap)
    # clo_pos 低 → 强度高；0.7 以下线性升到 1.0
    return max(0.0, min(1.0, (0.7 - cp) / 0.7)) if cp < 0.7 else 0.2


if __name__ == "__main__":
    # 自验：300291-type（高+7.4% 收-1.5%，clo_pos 高→弱回避）、豪恩-type（高+7.83% 收-15.91%，clo_pos 低→强回避）
    s_300291 = {"prev_close": 5.20, "open": 5.47, "high": 5.58, "price": 5.12, "pct": -1.5, "name": "百纳千成"}
    s_haen = {"prev_close": 30.0, "open": 28.0, "high": 32.35, "price": 25.23, "pct": -15.91, "name": "豪恩汽电"}
    s_flat = {"prev_close": 10.0, "open": 10.0, "high": 10.1, "price": 10.0, "pct": 0.0, "name": "X"}
    print("300291-type moves_signal=", moves_signal(s_300291), "avoid=", is_moves_avoid("300291", s_300291), "strength=", round(moves_reverse_strength(s_300291), 2))
    print("豪恩-type  moves_signal=", moves_signal(s_haen), "avoid=", is_moves_avoid("301488", s_haen), "strength=", round(moves_reverse_strength(s_haen), 2))
    print("flat-type   moves_signal=", moves_signal(s_flat), "avoid=", is_moves_avoid("000000", s_flat))
    # 单位修复验证：高开冲高、收 +1.5%（弱收<2%）的票必须被回避（百分数 chg/pct 须转分数比对）
    s_pos15 = {"prev_close": 10.0, "open": 10.6, "high": 10.8, "price": 10.15, "chg": 1.5, "name": "X"}
    print("收+1.5%-type moves_signal=", moves_signal(s_pos15), "avoid=", is_moves_avoid("600000", s_pos15),
          "(修复前会因 1.5<0.02 误判为 False 漏回避)")
