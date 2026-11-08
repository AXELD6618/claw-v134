# -*- coding: utf-8 -*-
"""
research/target_feasibility.py — P0 关键检验：目标到底可不可学？

对比三种标签（同样的特征、同样的时间切分）：
  A) 次日高开/涨停  (现有 BUY 目标的代理，二分类稀有事件)
  B) T+1 相对基准超额 > 0  (市场中性；P0 建议的新目标)
  C) 次日上涨 close-to-close > 0  (更宽的连续目标参考)

流程：抽样 N 只 → 向量化构造 t 收盘可见的特征 → 三种标签 → 时间切分(带embargo) →
      逻辑回归 → OOS AUC + Top-Decile Lift（命中率相对基线倍数）。
裁决：AUC<=0.52 且 lift<=1.10 → 该目标"不可学习/此路不通"；否则"可学习"。
输出：research/target_feasibility_report.md + .json

run: python -m research.target_feasibility --n-stocks 400
"""
from __future__ import annotations
import os, glob, json, argparse, random
import numpy as np
import pandas as pd


def _proj():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _daily_dir():
    return os.path.join(_proj(), "backtest_cache", "daily")


def _features(df):
    """t 收盘可见的向量化特征（不含未来信息）。"""
    c = df["close"].astype(float)
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    v = df["vol"].astype(float)
    ret1 = c.pct_change(1)
    out = pd.DataFrame(index=df.index)
    out["ret1"] = ret1
    out["ret5"] = c.pct_change(5)
    out["ret10"] = c.pct_change(10)
    out["ret20"] = c.pct_change(20)
    out["vol_ratio"] = v / v.rolling(20).mean()
    out["ma5_ratio"] = c / c.rolling(5).mean() - 1.0
    out["ma20_ratio"] = c / c.rolling(20).mean() - 1.0
    out["vol20"] = ret1.rolling(20).std()
    out["hl_range"] = (h - l) / c
    out["dist_high20"] = c / c.rolling(20).max() - 1.0
    out["dist_low20"] = c / c.rolling(20).min() - 1.0
    out["gap_prev"] = o / c.shift(1) - 1.0
    out["body"] = (c - o) / c
    return out


def _build_panel(n_stocks, start, max_rows=600, seed=0):
    files = glob.glob(os.path.join(_daily_dir(), "*.csv"))
    if not files:
        raise SystemExit("日线目录为空")
    random.seed(seed)
    files = random.sample(files, min(n_stocks, len(files)))
    parts = []
    used = 0
    for f in files:
        try:
            df = pd.read_csv(f)
            cmap = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
                    "成交量": "vol", "volume": "vol", "date": "date", "open": "open",
                    "close": "close", "high": "high", "low": "low", "vol": "vol"}
            df = df.rename(columns={c: cmap.get(str(c).replace("\ufeff", "").strip(), c) for c in df.columns})
            for c in ("open", "close", "high", "low", "vol"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date", "open", "close", "high", "low", "vol"]).sort_values("date")
            df = df[df["date"] >= pd.Timestamp(start)]
            if len(df) < 60:
                continue
            df = df.tail(max_rows).reset_index(drop=True)
            feat = _features(df)
            # 标签：t -> t+1
            next_close = df["close"].shift(-1)
            next_open = df["open"].shift(-1)
            df["next_ret"] = next_close / df["close"] - 1.0
            df["next_gap"] = next_open / df["close"] - 1.0
            df["code"] = os.path.basename(f).split(".")[0]
            df = pd.concat([df[["date", "code", "next_ret", "next_gap"]], feat], axis=1)
            # 目标 A：高开(>0.1%) 或 次日涨停(近似>=9.5%)
            df["y_highopen"] = ((df["next_gap"].fillna(-9) > 0.001) | (df["next_ret"].fillna(-9) >= 0.095)).astype(int)
            parts.append(df)
            used += 1
        except Exception:
            continue
    if not parts:
        raise SystemExit("无可用股票")
    panel = pd.concat(parts, ignore_index=True)
    # 截面基准：每个交易日 next_ret 的横截面均值
    panel = panel.dropna(subset=["next_ret"])
    bench = panel.groupby("date")["next_ret"].transform("mean")
    panel["excess"] = panel["next_ret"] - bench
    # 目标 B：超额>0；目标 C：次日上涨
    panel["y_excess"] = (panel["excess"] > 0).astype(int)
    panel["y_up"] = (panel["next_ret"] > 0).astype(int)
    return panel, used


FEATS = ["ret1", "ret5", "ret10", "ret20", "vol_ratio", "ma5_ratio", "ma20_ratio",
         "vol20", "hl_range", "dist_high20", "dist_low20", "gap_prev", "body"]


def _calibration(y, p, n_bins=10):
    """调用 research.probability_calibration 出 Brier/ECE/MCE/单调性，并给裁决。"""
    try:
        try:
            from . import probability_calibration as pc
        except ImportError:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import probability_calibration as pc
        rep = pc.report(y, p, n_bins=n_bins)
        ece = rep["ece"]
        mce_r = rep.get("mce_robust", rep["mce"])
        mono = rep.get("monotonic_robust", rep["monotonic"])
        if ece < 0.05 and mono:
            v = "可信(ECE<0.05且稳健单调)"
        elif ece < 0.10:
            v = "勉强(ECE<0.10，不宜直接当概率用)"
        else:
            v = "不可信(ECE>=0.10，概率数值无意义)"
        return {"brier": round(rep["brier"], 5), "ece": round(ece, 5),
                "mce": round(mce_r, 5), "monotonic": mono, "verdict": v}
    except Exception as e:
        return {"brier": None, "ece": None, "mce": None, "monotonic": None,
                "verdict": f"校准计算失败: {type(e).__name__}"}


def _run_target(panel, label, test_frac=0.3, embargo_days=5):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    d = panel.dropna(subset=FEATS + [label]).copy()
    if len(d) < 5000:
        return {"label": label, "error": f"样本不足 n={len(d)}"}
    dates = np.sort(d["date"].unique())
    cut = dates[int(len(dates) * (1 - test_frac))]
    cut_idx = int(np.where(dates == cut)[0][0])
    test_start = dates[min(cut_idx + embargo_days, len(dates) - 1)]
    train = d[d["date"] <= cut]
    test = d[d["date"] > test_start]
    if len(test) < 500 or train[label].nunique() < 2:
        return {"label": label, "error": "切分后样本/类别不足"}
    Xtr = train[FEATS].values; ytr = train[label].values
    Xte = test[FEATS].values; yte = test[label].values
    sc = StandardScaler().fit(Xtr)
    clf = LogisticRegression(max_iter=500, C=1.0).fit(sc.transform(Xtr), ytr)
    p = clf.predict_proba(sc.transform(Xte))[:, 1]
    auc = float(roc_auc_score(yte, p))
    base = float(yte.mean())
    # top-decile lift
    k = max(int(len(p) * 0.10), 1)
    top_idx = np.argsort(p)[-k:]
    top_hit = float(yte[top_idx].mean())
    lift = float(top_hit / base) if base > 0 else float("nan")
    # 概率校准（用户 P1 方法层要求：声称"概率"就必须验证概率可信）
    cal = _calibration(yte, p)
    # 落盘 OOS 预测，供 probability_calibration --check 复跑
    try:
        pd.DataFrame({"y_true": yte, "pred": p}).to_csv(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), f"preds_{label}.csv"), index=False)
    except Exception:
        pass
    return {"label": label, "n_train": int(len(train)), "n_test": int(len(test)),
            "base_rate": round(base, 4), "auc": round(auc, 4),
            "top_decile_hit": round(top_hit, 4), "lift": round(lift, 3),
            "brier": cal.get("brier"), "ece": cal.get("ece"), "mce": cal.get("mce"),
            "calib_monotonic": cal.get("monotonic"), "calib_verdict": cal.get("verdict"),
            "verdict": ("可学习" if (auc > 0.52 and lift > 1.10) else "不可学习/此路不通")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-stocks", type=int, default=400)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    print(f"[build] 抽样 {args.n_stocks} 只，起始 {args.start} ...")
    panel, used = _build_panel(args.n_stocks, args.start, seed=args.seed)
    print(f"[build] 可用股票 {used}，面板行数 {len(panel)}，日期 {panel['date'].min().date()}~{panel['date'].max().date()}")
    rows = []
    for lab in ("y_highopen", "y_excess", "y_up"):
        r = _run_target(panel, lab)
        rows.append(r)
        print("[result]", r)
    out = {"generated": pd.Timestamp.now().isoformat(timespec="seconds"),
           "n_stocks_used": used, "panel_rows": int(len(panel)), "results": rows}
    jp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target_feasibility_report.json")
    json.dump(out, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    md = ["# 目标可学习性检验报告", "", f"- 生成: {out['generated']}",
          f"- 可用股票: {used}，面板行数: {out['panel_rows']}", "",
          "## 一、可学习性裁决（AUC / Top-Decile Lift）", "",
          "| 标签 | n_test | 基线率 | OOS AUC | Top10%命中 | Lift | 裁决 |",
          "|---|---|---|---|---|---|---|"]
    name = {"y_highopen": "A 次日高开/涨停", "y_excess": "B T+1超额>0", "y_up": "C 次日上涨"}
    for r in rows:
        if "error" in r:
            md.append(f"| {name.get(r['label'],r['label'])} | - | - | - | - | - | {r['error']} |")
        else:
            md.append(f"| {name.get(r['label'],r['label'])} | {r['n_test']} | {r['base_rate']} | {r['auc']} | {r['top_decile_hit']} | {r['lift']} | {r['verdict']} |")
    md += ["", "## 二、概率校准（声称\"概率\"就必须证明概率可信）", "",
           "| 标签 | Brier | ECE | MCE | 单调 | 校准裁决 |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            md.append(f"| {name.get(r['label'],r['label'])} | - | - | - | - | {r['error']} |")
        else:
            md.append(f"| {name.get(r['label'],r['label'])} | {r.get('brier')} | {r.get('ece')} | "
                      f"{r.get('mce')} | {r.get('calib_monotonic')} | {r.get('calib_verdict')} |")
    md += ["", "### 判据说明",
           "- Brier = mean((p-y)^2)，基线率 p 处最优点约为 base_rate×(1-base_rate)；越低越好。",
           "- ECE<0.05 且可靠性曲线单调 → 概率数值可直接当作下注比例依据。",
           "- ECE>=0.10 → 概率只是\"伪概率\"，只能当排序分用，禁止做 Kelly 仓位输入。",
           "- OOS 原始预测已落盘 `research/preds_<label>.csv`，可用 "
           "`python -m research.probability_calibration --check research/preds_<label>.csv` 复现。"]
    mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "target_feasibility_report.md")
    open(mp, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"[write] {mp}")
    print(f"[write] {jp}")


if __name__ == "__main__":
    main()
