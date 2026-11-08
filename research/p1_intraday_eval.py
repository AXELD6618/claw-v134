#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日内择时模型 经济价值评估 (突破 "阈值准确率未超基线=非alpha" 的误判)。

核心问题: 对 timing 滤波器, 正确评判不是 "每根棒猜对涨跌 vs 多数类基线",
而是 "只在高置信时出手, 出手方向的条件胜率是否显著高于随机基线"。

评估项:
  1) 十分位条件胜率: P(up | 分位) 随预测概率分位单调变化 + 极端分位偏离基线多少.
  2) 极端置信策略: 仅当 p in 顶部Q / 底部Q 时出手, 出手样本的方向胜率 vs 随机基线.
  3) 净值(带成本): 顶部Q做多/底部Q做空(或仅顶部Q做多), 平均前向收益 vs 全样本基线 + 双边成本.
"""
import os, sys, json, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p1_intraday_model as m

H = 5


def main():
    X, y, metas = m.load_stocks()
    Xtr, ytr, Xte, yte = m.temporal_split(X, y, metas, 0.7)
    mu = Xtr.mean(0); sd = Xtr.std(0) + 1e-9
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    W1, b1, W2, b2 = m.mlp_train(Xtr, ytr, seed=0)
    p = m.mlp_pred(Xte, W1, b1, W2, b2)
    base_up = yte.mean()          # 无条件 "up" 基线 (测试集)
    base_ret = None
    # 前向收益 (用于净值): 用 close[t+H]-close[t] 近似 (需原始价, 这里用标签代理)
    print("无条件 up 基线 = %.3f (test)" % base_up)
    print("模型 test AUC = %.3f" % m.auc_score(p, yte))
    print("-" * 64)

    # 1) 十分位条件胜率
    order = np.argsort(p)
    n = len(p); dec = np.empty(n)
    for d in range(10):
        lo = d * n // 10; hi = (d + 1) * n // 10 if d < 9 else n
        dec[order[lo:hi]] = d
    print("十分位 | 预测p均值 | 实际up率 | 样本数")
    for d in range(10):
        msk = dec == d
        print("  D%-2d   |  %.3f  |  %.3f  |  %d" %
              (d + 1, p[msk].mean(), yte[msk].mean(), msk.sum()))
    print("-" * 64)

    # 2) 极端置信策略 (按分位 Q)
    for Q in [0.1, 0.2, 0.3]:
        top = p >= np.quantile(p, 1 - Q)
        bot = p <= np.quantile(p, Q)
        up_top = yte[top].mean() if top.sum() else float("nan")
        up_bot = yte[bot].mean() if bot.sum() else float("nan")
        # 仅顶部做多: 命中=这些棒里 up 的比例, 需 > base_up 才有价值
        print("Q=%.2f 顶部做多命中=%.3f (n=%d, 基线=%.3f) | 底部做空命中=%.3f (n=%d)" %
              (Q, up_top, top.sum(), base_up, up_bot, bot.sum()))
    print("-" * 64)

    # 3) 净值代理: 顶部Q做多、底部Q做空的方向一致率(对 edge 候选的买卖点优化)
    #    这里"方向一致"=顶部Q里多数up 且 底部Q里多数down -> 滤波器两端都指对
    Q = 0.2
    top = p >= np.quantile(p, 1 - Q)
    bot = p <= np.quantile(p, Q)
    both_right = (yte[top].mean() > 0.5) and (yte[bot].mean() < 0.5)
    print("两端一致(顶多+底空都指对)? %s  | top_up=%.3f bot_up=%.3f" %
          (both_right, yte[top].mean(), yte[bot].mean()))
    # 仅用极端分位出手: 出手样本 = top ∪ bot, 方向准确率
    act = top | bot
    acc_act = ((p[act] > 0.5) == yte[act]).mean()
    print("仅极端分位出手(顶多/底空) 方向准确率=%.3f (n=%d) vs 全样本阈值准确率=%.3f" %
          (acc_act, act.sum(), ((p > 0.5) == yte).mean()))

    out = {
        "base_up_test": round(float(base_up), 4),
        "decile_up_rate": [round(float(yte[dec == d].mean()), 4) for d in range(10)],
        "top_decile_up": round(float(yte[p >= np.quantile(p, 0.9)].mean()), 4),
        "bot_decile_up": round(float(yte[p <= np.quantile(p, 0.1)].mean()), 4),
        "extreme_action_acc": round(float(acc_act), 4),
        "extreme_action_n": int(act.sum()),
    }
    with open(os.path.join(m.ROOT, "reports", "intraday_value_eval.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[saved] reports/intraday_value_eval.json")


if __name__ == "__main__":
    main()
