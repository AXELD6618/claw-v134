# -*- coding: utf-8 -*-
"""
research/label_engineering.py — 标签工程（比二分类"高开/涨停"更贴近损益）

提供：
  - excess_label: "T+1 相对基准超额 > thr" 的市场中性标签（P0 关键检验用）
  - triple_barrier_labels: 三重障碍法（止盈/止损/时间），标签 = +1/-1/0
  - meta_labels: 元标签（主模型方向对且盈利=1），用于二级过滤
run: python -m research.label_engineering --selftest
"""
from __future__ import annotations
import numpy as np


def excess_label(r_next, bench_next, thr=0.0):
    """超额标签：个股 T+1 收益 - 基准收益 > thr -> 1。bench 可为截面均值或指数收益。"""
    r = np.asarray(r_next, float); b = np.asarray(bench_next, float)
    return (r - b > thr).astype(int)


def triple_barrier_labels(close, events, pt=2.0, sl=2.0, vertical=5, vol=None):
    """三重障碍。close: 1D 价格序列；events: 入场索引数组。

    障碍：上=close[e]*(1+pt*vol)，下=close[e]*(1-sl*vol)，竖=垂直 vertical 根后按收盘定。
    vol: 每事件波动率（如滚动 std）；None 时用事件前 20 根对数收益 std。
    返回 (labels int[+1/-1/0], exit_idx)。注：用收盘近似触障（真实实现可用 high/low，本版简洁）。
    """
    close = np.asarray(close, float)
    n = len(close)
    events = np.asarray(events, int)
    if vol is None:
        lr = np.diff(np.log(close), prepend=np.log(close[0]))
        vol = np.array([np.std(lr[max(0, e - 20):e + 1]) if e > 1 else np.std(lr[:5]) for e in events])
    vol = np.asarray(vol, float)
    labels = np.zeros(len(events), int)
    exits = np.zeros(len(events), int)
    for k, e in enumerate(events):
        if e >= n - 1:
            exits[k] = e; continue
        p0 = close[e]
        up = p0 * (1 + pt * vol[k]); dn = p0 * (1 - sl * vol[k])
        end = min(e + vertical, n - 1)
        lab, exit_i = 0, end
        for t in range(e + 1, end + 1):
            if close[t] >= up:
                lab, exit_i = 1, t; break
            if close[t] <= dn:
                lab, exit_i = -1, t; break
        labels[k] = lab; exits[k] = exit_i
    return labels, exits


def meta_labels(primary_sign, realized_ret, deadband=0.0):
    """元标签：主模型方向与实际收益同向且幅度>deadband -> 1，否则 0。"""
    s = np.asarray(primary_sign, float); r = np.asarray(realized_ret, float)
    return (s * r > deadband).astype(int)


def _selftest():
    # 构造先涨后跌的序列，验证三重障碍方向
    up = np.concatenate([np.linspace(10, 11, 5), np.linspace(11, 10, 10)])
    lab, ex = triple_barrier_labels(up, events=np.array([0]), pt=1.0, sl=1.0, vertical=6, vol=np.array([0.02]))
    print("[selftest] 上涨触上障 label=", int(lab[0]), "(应为1)")
    assert lab[0] == 1
    dn = np.concatenate([np.linspace(10, 9, 5), np.linspace(9, 10, 10)])
    lab2, _ = triple_barrier_labels(dn, events=np.array([0]), pt=1.0, sl=1.0, vertical=6, vol=np.array([0.02]))
    print("[selftest] 下跌触下障 label=", int(lab2[0]), "(应为-1)")
    assert lab2[0] == -1
    print("[selftest] excess_label:", excess_label([0.03, -0.01, 0.005], [0.01, 0.01, 0.01]).tolist())
    print("[selftest] meta_labels:", meta_labels([1, 1, -1], [0.02, -0.01, -0.02]).tolist())
    print("[selftest] PASS")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print("usage: python -m research.label_engineering --selftest")
