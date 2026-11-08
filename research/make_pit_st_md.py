# -*- coding: utf-8 -*-
"""读取 feature_eligibility_matrix_pit_st.json，生成对齐原格式的 .md 报告。"""
import json, os
BC = r"E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"
out = json.load(open(os.path.join(BC, "feature_eligibility_matrix_pit_st.json"), encoding="utf-8"))
meta = out["meta"]; feats = out["features"]

def fmt(v, nd=4, na="NA"):
    if v is None: return na
    if isinstance(v, float): return (f"{v:.{nd}f}")
    return str(v)

lines = []
lines.append("# Feature Eligibility Matrix (PIT + ST 修正版) — 毕方灵犀圣杯")
lines.append("")
lines.append("**与旧矩阵的差异**：本表在「剔除 ST/退 样本」+「Point-in-Time 正确口径」下重跑，")
lines.append("用于验证旧矩阵 0/12 REJECT 是否为 ST 污染 / 前视泄露造成的假阴性。")
lines.append("")
lines.append(f"- Universe: demon_universe 原始 **{meta['demon_n_before']:,}** 条 → 剔除名称含 ST/退 的 code 共 **{meta['st_excluded_n']:,}** 条 → 保留 **{meta['demon_n_after']:,}** 条。")
lines.append(f"- Split: TRAIN = date <= {meta['split']} ; OOS = date > {meta['split']} (OOS n={meta['oos_rows']:,})。")
lines.append(f"- Within-hot baseline **CONT_T1**(全保留样本, T+1 可观测) = **{meta['baseline_cont_t1']:.4f}** (n={meta['baseline_cont_t1_n']:,})。")
lines.append(f"- Full-market random T+5 累计 = **{meta['random_t5']}** (PF 分母)。n_trials(gate)={meta['n_trials']}。")
lines.append("")
lines.append("## Eligibility table")
lines.append("")
header = (f"| feature | direction | pit_safe | st_excluded | coverage | hit_mult | "
          f"PF_t5_vs_random | t | DSR | WhiteRC | promote | note |")
lines.append(header)
lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
for o in feats:
    note = o.get("note", "").replace("|", "\\|")
    lines.append(
        f"| {o['feature']} | {o['direction']} | {o['pit_safe']} | {o['st_excluded']} | "
        f"{o['coverage_n']} | {fmt(o['hit_mult'])} | {fmt(o['PF_t5_vs_random'])} | "
        f"{fmt(o['t'],3)} | {fmt(o['DSR'])} | {fmt(o['WhiteRC'])} | "
        f"{'YES' if o['promote'] else 'no'} | {note} |")
lines.append("")
lines.append("## Methods（同原矩阵口径，附 PIT/ST 修正说明）")
lines.append("- OOS 十分位(10 组) 按特征值分箱；high/low 两方向都测，取「最优十分位均值 ret_T5」更好的方向。")
lines.append("- `hit_mult = 最优十分位 CONT_T1 命中率 / 全样本基线 CONT_T1`；`CONT_T1` = as-of T+1 可观测的「次日继续涨停/连板」(close[T+1]/close[T]-1 >= limit_pct)。")
lines.append("- `PF_t5_vs_random = 最优十分位均值 ret_T5 / 1.259`；`PF_t5_vs_bottom = 最优十分位均值 ret_T5 / 反向极端十分位均值 ret_T5`。")
lines.append("- `ret_T5 = close(T+5)/close(T)-1`（交易日历连续；T+5 缺失则跳过）。`t` = 最优 vs 反向极端 均值差 / 不等方差 SE。")
lines.append("- `sr_per_period = mean(best r5)/std(best r5)`；`gate()` = deflated-Sharpe 多重检验：promote iff hit>=1.15 AND PF>=1.30 AND t>=2.0 AND DSR>=0.95。")
lines.append("- **ST 剔除**：universe.csv 中名称含 `ST`/`退` 的 code（共 199 个），其 demon 样本全部剔除（剔除 1,376 条，占 6.87%）。")
lines.append("- **PIT 修正**：`chip_scr` 用 cyq_engine.cyq_scr 在截断到 T 日的 daily 历史上逐点计算（消除旧版 scr_latest 全局快照前视）；其余 as-of-T 价格/股东户数特征在剔除 ST 后的样本上重算。")
lines.append("- **EXCLUDED（非 PIT 可化）**：inst_org_ratio / inst_n_org / youzi_yl_net / youzi_lhb_cnt_5d / news_hit 原源为 smartmoney.context / 龙虎榜快照 / intel_board 的「当前快照」，无法在完整 demon 历史(~2024-07~2026-10)内可靠 point-in-time 化，按任务要求显式标记 EXCLUDED，不做伪造。")
lines.append("")
lines.append("## 稳健性结论")
promoted = [o['feature'] for o in feats if o['promote']]
lines.append(f"- **promote = {len(promoted)} / 12**（{', '.join(promoted) if promoted else 'NONE'}）。")
lines.append("- 去掉 ST 代理 + 修正前视泄露后，**0/12 REJECT 的结论仍然成立**：没有任何特征翻盘为可晋级。")
lines.append("- 7 个可在 ST 剔除后 PIT/as-of-T 重算的特征，其绑定约束主要是 **PF_t5_vs_random 远低于 1.30**（随机 T+5=+25.9% 是极强基准，最优十分位 5 日收益需约 +163% 才达标），叠加多数特征 hit_mult<1.15，故全部 REJECT。")
lines.append("- 唯一做了真正 PIT 重算的 `chip_scr`：即便用 as-of-T 历史筹码集中度（无前视），其在 OOS 最优十分位的表现仍不足以晋级（见上表 DSR 理由），说明旧版 0/12 并非 scr_latest 前视所致。")
lines.append("- 5 个快照类特征因无法 PIT 化而 EXCLUDED；其旧结论不可信也不可直接沿用，需先补齐 point-in-time 源（smartmoney.context / LHB / intel_board 的历史回填）再重评。")
lines.append("")
lines.append("---")
lines.append("**Promoted candidates (promote=YES): %d / 12**" % len(promoted))
open(os.path.join(BC, "feature_eligibility_matrix_pit_st.md"), "w", encoding="utf-8").write("\n".join(lines))
print("[OK] feature_eligibility_matrix_pit_st.md written")
