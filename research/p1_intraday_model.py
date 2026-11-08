#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日内择时 ML (严格样本外验证, numpy-only 零依赖)。

突破指令背景:
  原结论 "深度学习日内模型不可行" 基于两个错误前提:
   (1) "无日内数据" -> 错. TDX tdx_kline period=7 提供真实 1 分钟 OHLCV 棒.
   (2) 日线信号 rho=-0.061 证的是 "次日涨停(日级选股)", 不证 "日内(分钟级)也是抛硬币".
  本模型只做 "择时" (对已由 连板惯性/断层反包 选出的标的优化买点卖点), 不做 "选股",
  故最坏 ML 失效也不伤 base edge (safety-by-construction).

数据质量铁律 (实测发现, 必须写明):
  TDX 分钟棒 up/down 字段在所有 240 根棒上为 "当日累计值", 非逐棒主动买卖量.
  -> 逐棒 order-flow imbalance (OFI) 不可得, 本模型不使用该特征, 仅用 OHLCV 微观结构.

目标: 预测未来 H 分钟 (默认 H=5) close 收益方向 (涨=1/跌=0).
验证: 全局按时间前 70% 训练 / 后 30% 测试 (walk-forward, 无未来泄漏).
      OOS 准确率 vs 0.5 的单样本比例 z 检验 (one-sided) + AUC + 多数类基线.
      numpy-only; 不显著不 ship.
"""
import os, sys, json, glob, math
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INTRADAY = os.path.join(ROOT, "backtest_cache", "intraday")
H = 5  # 预测 horizon (分钟)


def _sec_of(dt):
    # "YYYYMMDD HH:MM:SS" -> seconds since midnight
    t = dt.split(" ")[1]
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def load_stocks(by_stock=False):
    """读每只股票 CSV, 逐股计算微观结构特征 (lookback 不跨股).

    by_stock=False -> 返回 pooled (X, y, metas, yret)  (主流水线用)
    by_stock=True  -> 返回 [(code, X, y, yret, dts), ...]  (cross-stock 泛化检验用)
    """
    per = []
    for csvp in sorted(glob.glob(os.path.join(INTRADAY, "*.csv"))):
        if "_raw" in os.path.basename(csvp):
            continue
        code = os.path.basename(csvp).split("_")[0]
        rows = np.genfromtxt(csvp, delimiter=",", skip_header=1,
                             dtype=None, encoding="utf-8")
        if rows.ndim == 0:
            continue
        # 列: datetime,open,high,low,close,vol,amount,buy_vol,sell_vol
        dts = [r[0] for r in rows]
        O = np.array([float(r[1]) for r in rows])
        Hh = np.array([float(r[2]) for r in rows])
        L = np.array([float(r[3]) for r in rows])
        C = np.array([float(r[4]) for r in rows])
        V = np.array([float(r[5]) for r in rows])
        A = np.array([float(r[6]) for r in rows])
        n = len(C)
        sec = np.array([_sec_of(d) for d in dts])
        # 收益
        ret1 = np.zeros(n); ret1[1:] = C[1:] / C[:-1] - 1
        # 特征 (需 lookback, 逐股独立)
        feat = np.zeros((n, 9))
        for t in range(n):
            lo = max(0, t - 20)
            vol_win = V[lo:t + 1]
            ret_win = ret1[lo:t + 1]
            vmean = vol_win.mean() if len(vol_win) else 0.0
            rmean = ret_win.mean() if len(ret_win) else 0.0
            rstd = ret_win.std() if len(ret_win) > 1 else 0.0
            feat[t, 0] = ret1[t]                                  # 1-min 收益
            feat[t, 1] = C[t] / C[t - 5] - 1 if t >= 5 else 0.0   # 5-min 动量
            feat[t, 2] = rstd                                      # 短期波动
            feat[t, 3] = C[t] / C[t - 20] - 1 if t >= 20 else 0.0  # 20-min 动量
            feat[t, 4] = (sec[t] - 34200) / (54000 - 34200)        # 日内时间归一
            feat[t, 5] = math.log(V[t] + 1.0)                      # log 量
            feat[t, 6] = V[t] / (vmean + 1e-9)                     # 相对量比
            feat[t, 7] = A[t] / (A[lo:t + 1].mean() + 1e-9) if t > lo else 1.0  # 金额比
            feat[t, 8] = (Hh[t] - L[t]) / (C[t] + 1e-9)            # 棒内振幅
        # 标签: 未来 H 分钟 close 方向 + 前向收益幅度 (净值分析用)
        y = np.zeros(n)
        yret = np.zeros(n)
        y[:n - H] = (C[H:n] > C[:n - H]).astype(float)
        yret[:n - H] = C[H:n] / C[:n - H] - 1.0
        # 有效样本: t>=20 且 t+H < n
        mask = np.arange(n) >= 20
        mask &= np.arange(n) < n - H
        per.append((code, feat[mask], y[mask], yret[mask],
                    [dts[i] for i in range(n) if mask[i]]))
    if by_stock:
        return per
    Xs = [p[1] for p in per]; ys = [p[2] for p in per]
    yrets = [p[3] for p in per]; metas = [(p[0], p[4]) for p in per]
    if not Xs:
        return np.zeros((0, 9)), np.zeros(0), [], np.zeros(0)
    return np.vstack(Xs), np.concatenate(ys), metas, np.concatenate(yrets)


def temporal_split(X, y, metas, frac=0.7, extra=None):
    """全局按时间排序前 frac 训练 / 后 1-frac 测试. metas 提供时间用于排序."""
    times = []
    for code, dts in metas:
        for d in dts:
            times.append(d)
    order = np.argsort(times)
    k = int(len(order) * frac)
    tr = order[:k]; te = order[k:]
    if extra is not None:
        return X[tr], y[tr], X[te], y[te], extra[tr], extra[te]
    return X[tr], y[tr], X[te], y[te]


def mlp_train(X, y, hidden=16, lr=0.05, epochs=400, seed=0):
    rng = np.random.default_rng(seed)
    n, d = X.shape
    W1 = rng.normal(0, 0.1, (d, hidden))
    b1 = np.zeros(hidden)
    W2 = rng.normal(0, 0.1, (hidden, 1))
    b2 = np.zeros(1)
    for _ in range(epochs):
        z1 = np.maximum(0, X @ W1 + b1)
        z2 = z1 @ W2 + b2
        p = 1.0 / (1.0 + np.exp(-z2))
        # BCE grad
        dz2 = (p - y.reshape(-1, 1)) / n
        dW2 = z1.T @ dz2
        db2 = dz2.sum(0)
        dz1 = (dz2 @ W2.T) * (z1 > 0)
        dW1 = X.T @ dz1
        db1 = dz1.sum(0)
        W2 -= lr * dW2; b2 -= lr * db2
        W1 -= lr * dW1; b1 -= lr * db1
    return W1, b1, W2, b2


def mlp_pred(X, W1, b1, W2, b2):
    z1 = np.maximum(0, X @ W1 + b1)
    return 1.0 / (1.0 + np.exp(-(z1 @ W2 + b2))).ravel()


def auc_score(p, y):
    """Mann-Whitney AUC (numpy)."""
    pos = p[y == 1]; neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(p)
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    s = ranks[y == 1].sum()
    return (s - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def prop_z(acc, n):
    """单侧比例检验 H1: acc>0.5. 返回 z 与 p(正态分布近似)."""
    se = math.sqrt(0.25 / n)
    z = (acc - 0.5) / se
    p = 0.5 * (1.0 - math.erf(z / math.sqrt(2.0)))
    return z, p


def main():
    global H
    if len(sys.argv) > 1:
        H = int(sys.argv[1])
    X, y, metas, yret = load_stocks()
    print("[data] pooled bars=%d  positives=%.1f%%  stocks=%d" %
          (len(y), 100 * y.mean(), len(metas)))
    if len(y) < 120:
        print("[warn] 样本 < 120, 先累积多交易日再下结论 (seed 仅可行性).")
    Xtr, ytr, Xte, yte, yretr, yrete = temporal_split(X, y, metas, 0.7, yret)
    # 标准化用 TRAIN 统计量 (防泄漏)
    mu = Xtr.mean(0); sd = Xtr.std(0) + 1e-9
    Xtr = (Xtr - mu) / sd; Xte = (Xte - mu) / sd
    print("[split] train=%d  test=%d  (walk-forward 时间前70%%/后30%%)" %
          (len(ytr), len(yte)))
    W1, b1, W2, b2 = mlp_train(Xtr, ytr)
    ptr = mlp_pred(Xtr, W1, b1, W2, b2)
    pte = mlp_pred(Xte, W1, b1, W2, b2)
    acc_tr = ((ptr > 0.5) == ytr).mean()
    acc_te = ((pte > 0.5) == yte).mean()
    auc_te = auc_score(pte, yte)
    # 基线: 永远预测多数类 (类别不平衡下必须比较它, 而非 0.5)
    base = max(yte.mean(), 1 - yte.mean())
    z, pval = prop_z(acc_te, len(yte))          # 仅作参考: vs 0.5
    # AUC bootstrap 95% CI (dependency-free)
    rng = np.random.default_rng(12345)
    nb = 1000; aucs = np.empty(nb)
    idx = np.arange(len(yte))
    for i in range(nb):
        s = rng.choice(idx, len(idx), replace=True)
        aucs[i] = auc_score(pte[s], yte[s])
    auc_lo, auc_hi = float(np.percentile(aucs, 2.5)), float(np.percentile(aucs, 97.5))
    # 阈值准确率 vs 多数类基线 的比例检验
    z_base = (acc_te - base) / math.sqrt(base * (1 - base) / len(yte))
    p_base = 0.5 * (1.0 - math.erf(z_base / math.sqrt(2.0)))  # one-sided
    # 排名信号 vs 0.5: AUC CI 下界是否 > 0.5
    rank_signal = auc_lo > 0.5
    # 击败朴素基线: 阈值准确率是否显著 > 多数类基线
    beats_baseline = (p_base < 0.05) and (acc_te > base)
    # === 择时闸门经济价值 (突破 "阈值准确率未超基线=非alpha" 误判) ===
    COST = 0.0015  # 双边成本 ~0.15% (佣金+滑点)
    # 滚动窗口分位闸门: 阈值用"截至当前时刻之前的近期概率分布"分位校准
    # (部署时同理: 用当日已有 intraday 概率校准, 而非 stale 全量 train 绝对概率)。
    # 早起无历史则回退 train 分位。无未来泄漏 (只用 past 决策概率)。
    tu0, td0 = float(np.quantile(ptr, 0.8)), float(np.quantile(ptr, 0.2))
    MINH = 30
    enter_idx, avoid_idx = [], []
    for i in range(len(pte)):
        if i >= MINH:
            tu = float(np.quantile(pte[:i], 0.8)); td = float(np.quantile(pte[:i], 0.2))
        else:
            tu, td = tu0, td0
        if pte[i] >= tu:
            enter_idx.append(i)
        elif pte[i] <= td:
            avoid_idx.append(i)
    enter_idx = np.array(enter_idx); avoid_idx = np.array(avoid_idx)
    top = np.zeros(len(pte), bool); top[enter_idx] = True
    bot = np.zeros(len(pte), bool); bot[avoid_idx] = True
    enter_hit = float(yte[enter_idx].mean()) if len(enter_idx) else float("nan")
    avoid_down = 1.0 - (float(yte[avoid_idx].mean()) if len(avoid_idx) else float("nan"))
    net_gate = float(yrete[enter_idx].mean() - COST) if len(enter_idx) else float("nan")
    net_always = float(yrete.mean() - COST)
    print("-" * 56)
    print("gate: 滚动分位(顶20%%/底20%%) ENTER n=%d AVOID n=%d (回退train前%d根)" %
          (len(enter_idx), len(avoid_idx), MINH))
    print("ENTER命中=%.3f (基线%.3f, %.2fx) | AVOID避多命中=%.3f" %
          (enter_hit, yte.mean(), enter_hit / max(yte.mean(), 1e-9), avoid_down))
    print("净值: 闸门做多净=%.4f  总是做多净=%.4f  (改善=%.4f)" %
          (net_gate, net_always, net_gate - net_always))
    print("train acc = %.3f   test acc = %.3f   (gap=%.3f)" %
          (acc_tr, acc_te, acc_tr - acc_te))
    print("train pos%% = %.1f   test pos%% = %.1f" %
          (100 * ytr.mean(), 100 * yte.mean()))
    print("test AUC = %.3f   bootstrap95%%CI=[%.3f, %.3f]   (vs 0.5)" %
          (auc_te, auc_lo, auc_hi))
    print("majority baseline acc = %.3f   (acc vs base: z=%.2f p=%.4f)" %
          (base, z_base, p_base))
    print("OOS vs 0.5 (参考):  acc=%.3f  n=%d  z=%.2f  p=%.4f" %
          (acc_te, len(yte), z, pval))
    print("-" * 56)
    print("RANK SIGNAL (AUC>0.5, CI下界>0.5): %s" % rank_signal)
    print("BEATS NAIVE BASELINE (acc>majority, p<0.05): %s" % beats_baseline)
    # 闸门可用性: 顶部做多命中 >=1.5x基线 且 净值改善 -> 作为择时闸门有真实经济价值
    gate_usable = (enter_hit > yte.mean() * 1.5) and (net_gate > net_always)
    if rank_signal and gate_usable:
        verd = "TIMING EDGE 可用: 闸门做多命中%.2fx基线, 净值改善 -> 可作择时层接入 p1; 需多日确认" % (enter_hit / max(yte.mean(), 1e-9))
    elif rank_signal and not gate_usable:
        verd = "RANKING SIGNAL 存在, 但闸门经济价值不足 -> 继续累积/改进特征"
    else:
        verd = "NO SIGNAL in seed -> 继续累积多交易日"
    print("GATE USABLE (顶多命中>=1.5x基线 且 净值改善): %s" % gate_usable)
    print("VERDICT:", verd)
    print("NOTE: up/down 为当日累计, OFI 不可用, 本模型仅用 OHLCV 微观结构特征.")
    # 输出机器可读结论
    out = {
        "horizon_min": H, "n_pooled": int(len(y)), "n_train": int(len(ytr)),
        "n_test": int(len(yte)), "train_pos": round(float(ytr.mean()), 4),
        "test_pos": round(float(yte.mean()), 4),
        "train_acc": round(float(acc_tr), 4), "test_acc": round(float(acc_te), 4),
        "test_auc": round(float(auc_te), 4), "auc_ci95": [round(auc_lo, 4), round(auc_hi, 4)],
        "majority_baseline": round(float(base), 4),
        "z_vs_base": round(float(z_base), 3), "p_vs_base": round(float(p_base), 5),
        "rank_signal": bool(rank_signal), "beats_baseline": bool(beats_baseline),
        "gate_method": "rolling_quantile", "gate_thr_up_fallback": round(tu0, 4),
        "gate_thr_down_fallback": round(td0, 4),
        "enter_n": int(len(enter_idx)), "avoid_n": int(len(avoid_idx)),
        "enter_hit": round(enter_hit, 4), "avoid_down_hit": round(avoid_down, 4),
        # 2026-10-26 新增：ENTER 命中率相对基线（上涨占比）的倍数。
        # 依据：两日池 AUC 的 95% 噪声带太宽，AUC 单点无法判死；lift < 1 意味着
        # 「进场的票上涨概率还不如随机」，这是比 AUC 更早、更硬的负面证据。
        "base_pos_rate": round(float(yte.mean()), 4),
        "enter_lift": (round(float(enter_hit / max(float(yte.mean()), 1e-9)), 4)
                       if len(enter_idx) else None),
        "net_gate": round(net_gate, 5), "net_always": round(net_always, 5),
        "gate_usable": bool(gate_usable), "ofi_available": False,
        "gate_verdict": ("GATE_DISABLED(net_gate<=net_always, 不得参与任何择时决策)"
                         if not (net_gate > net_always) else
                         ("GATE_OK" if gate_usable else "GATE_WEAK(仅观察)")),
    }
    with open(os.path.join(ROOT, "reports", "intraday_oos_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[saved] reports/intraday_oos_result.json")


def apply_gate(p, tu, td, hist_len=0, min_hist=30):
    """把单根棒的预测概率映射为择时闸门决策 (供 p1 闭环实时调用).

    p  >= tu -> ENTER  (顶部置信, 做多/加仓)
    p  <= td -> AVOID  (底部置信, 避多/减仓/退出)
    其余      -> HOLD   (不动)

    🔴 冷启动保护: 若截至当前的历史概率样本 < min_hist 根 (live 首日/新票上市初),
    强制 HOLD —— 此时滚动分位闸门尚未积累足够分布, 用 stale train 阈值会误判.
    这是上一轮遗漏的工程缺陷修复.
    """
    if hist_len < min_hist:
        return "HOLD"
    if p >= tu:
        return "ENTER"
    if p <= td:
        return "AVOID"
    return "HOLD"


def fit_gate_thresholds(Xtr, ytr, q_up=0.8, q_dn=0.2, seed=0):
    """训练后返回 (tu, td) 校准闸门阈值 (基于训练集概率分布)."""
    W1, b1, W2, b2 = mlp_train(Xtr, ytr, seed=seed)
    ptr = mlp_pred(Xtr, W1, b1, W2, b2)
    return float(np.quantile(ptr, q_up)), float(np.quantile(ptr, q_dn)), (W1, b1, W2, b2)


if __name__ == "__main__":
    main()
