#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""蒙特卡洛仓位压力测试（XTX 极端损失评估思想 → A股可执行 edge）。

仅基于已验证、100%可成交的 edge：断层反包（T+1 均值 +0.53%、胜率 47.1%、扣成本净 +0.43%）。
连板惯性(≥2连板→49%次日涨停) 买不进(buyable=False) → 不参与实盘 sizing，仅作监控。

不入金、不改规则文件；只输出风险数字供人工回填 p1_rules.json。
单笔分布用已验证聚合反推（近似，已在输出标注）：胜率 WIN、盈利腿 MWIN、亏损腿 MLOSS。
"""
import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
RULES_P = os.path.join(ROOT, "data", "p1_rules.json")

# 断层反包可执行 edge（锚定已验证聚合: T+1均值+0.53% 胜率47.1% 净+0.43%）
WIN = 0.471
MWIN = 0.025    # 盈利腿近似(部分反包, 锚定 +0.53% 均值反推)
MLOSS = -0.015  # 亏损腿近似(未反包 typical A股回落)
N_PATH = 20000
HORIZON_D = 5   # 评估窗口(交易日)


def main():
    rules = json.load(open(RULES_P, encoding="utf-8")) if os.path.exists(RULES_P) else {}
    risk = rules.get("risk", {})
    single = float(risk.get("single_trade_loss_cap_pct", 3.0)) / 100.0
    daily_halt = float(risk.get("daily_loss_halt_pct", 5.0)) / 100.0
    theme_cap = float(risk.get("per_theme_cap_pct", 40.0)) / 100.0
    K = max(1, round(theme_cap / single))   # 同题材并发仓位数

    rng = np.random.default_rng(20261025)
    worst_days = np.empty(N_PATH)
    for i in range(N_PATH):
        w = 0.0
        for _ in range(HORIZON_D):
            draws = rng.choice([MWIN, MLOSS], size=K, p=[WIN, 1 - WIN])
            day_ret = single * draws.sum()      # 单仓权重 single × K 仓
            w = min(w, day_ret)
        worst_days[i] = w
    var5 = np.percentile(worst_days, 5)
    var1 = np.percentile(worst_days, 1)
    p_halt = float((worst_days < -daily_halt).mean())

    print("="*60)
    print("[MC 仓位压力测试] 断层反包可执行 edge (锚定 T+1 +0.53% / 胜率47.1%)")
    print("  假设: WIN=%.3f MWIN=%.3f MLOSS=%.3f (盈利/亏损腿为聚合反推近似)" % (WIN, MWIN, MLOSS))
    print("  配置: 单仓上限=%.1f%% 同题材并发K=%d仓 日亏熔断=%.1f%% 窗口=%d日 N=%d" %
          (single * 100, K, daily_halt * 100, HORIZON_D, N_PATH))
    print("  5%% VaR(最差日)=%.2f%%  1%% VaR=%.2f%%" % (var5 * 100, var1 * 100))
    print("  单日亏损突破熔断概率 P(loss>%.1f%%)=%.2f%%" % (daily_halt * 100, p_halt * 100))
    if p_halt > 0.02:
        sug = daily_halt / (K * abs(MLOSS))
        print("  ⚠️ 突破概率偏高 → 建议单仓上限降到 %.2f%% (使 P<halt)<2%%)" % (min(sug, single) * 100))
    else:
        print("  ✅ 当前配置下极端日亏可控(突破概率<2%%)")
    print("="*60)
    print("注: 本脚本只读规则、不改文件; 确认数字后人工回填 p1_rules.json 的 risk 块。")


if __name__ == "__main__":
    main()
