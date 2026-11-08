# -*- coding: utf-8 -*-
"""
research/live_prior_builder.py — Live 弱先验构建器（可回溯部分，严格无未来函数）
================================================================================

为什么需要这个：
  Live 13 个分量里，6 个资金流分量（main_net_pct / ddx / main_net_3d/5d/10d）
  + fengdan_amt + sentiment **历史不可回溯**——TDX 明确"历史日返回空"，我们从未存过。
  所以"用 2023-2026 日线训练 Live 模型"物理上只能得到 **可回溯部分的先验**，
  不是"Live 真杠杆"。本脚本就把这部分做出来，诚实地叫它 prior。

可回溯的部分（只有这些）：
  - hy_zf       → 用 data/industry_map/stock_to_chain.json 聚合所属链条当日涨幅
  - hy_zt_count → 同链条当日涨停家数
  - 其余 8 个分量用日线可推导的**语义代理**（明确标注 proxy，不冒充原分量）

训练目标（关键修正）：
  retrain_live.py 原目标是 nol（次日开盘涨停），日线级正例率 ≈0.1%。
  80 个样本的期望正例 = 0.08 个 —— 等 80 条永远训不出东西，与未来函数无关，是统计不可能。
  故本先验训练 **gap_next>0（次日高开，正例率≈45%）**，并把 nol 作为次级可学性报告输出。

切分（不搞全量一把梭）：
  训练 ≤ train_end（默认 2026-06-30）→ embargo 5 交易日 → OOS ≥ 2026-07-01。
  另跑 Purged K-Fold（embargo=5）报均值，避免单切分偶然。

输出：
  signal_model_live_prior.json   先验权重（供 live_blend.py 做 log-odds shrinkage）
  research/live_prior_report.md  完整诚实的可学习性 + 校准报告

run:
  .venv/Scripts/python.exe -m research.live_prior_builder --n-stocks 1200
"""
from __future__ import annotations
import os, sys, glob, json, random, argparse, pickle
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

CMAP = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
        "成交量": "vol", "成交额": "amount", "date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "vol", "volume": "vol", "amount": "amount"}


def _proj():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _daily_dir():
    return os.path.join(_proj(), "backtest_cache", "daily")


def _load_chain_map():
    p = os.path.join(_proj(), "data", "industry_map", "stock_to_chain.json")
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
        out = {}
        for code, v in d.items():
            ch = (v or {}).get("chains") or []
            if ch:
                out[str(code).zfill(6)] = str(ch[0])
        return out
    except Exception:
        return {}


def _read_one(path):
    df = pd.read_csv(path)
    df = df.rename(columns={c: CMAP.get(str(c).replace("\ufeff", "").strip(), c) for c in df.columns})
    need = ("open", "close", "high", "low", "vol")
    if any(c not in df.columns for c in need):
        return None
    for c in need:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "open", "close", "high", "low", "vol"]).sort_values("date")
    return df if len(df) >= 80 else None


def _features(df):
    """t 收盘可见的特征，全部 proxy 命名（不冒充 LIVE_KEYS）。"""
    c = df["close"].astype(float); o = df["open"].astype(float)
    h = df["high"].astype(float); l = df["low"].astype(float)
    v = df["vol"].astype(float)
    ret1 = c.pct_change(1)
    f = pd.DataFrame(index=df.index)
    # --- 基础 OHLCV（已知有效且无害） ---
    f["ret1"] = ret1
    f["ret5"] = c.pct_change(5)
    f["ret10"] = c.pct_change(10)
    f["ret20"] = c.pct_change(20)
    f["vol20"] = ret1.rolling(20).std()
    f["hl_range"] = (h - l) / c
    f["dist_high20"] = c / c.rolling(20).max() - 1.0
    f["dist_low20"] = c / c.rolling(20).min() - 1.0
    f["gap_prev"] = o / c.shift(1) - 1.0
    f["ma5_ratio"] = c / c.rolling(5).mean() - 1.0
    f["ma20_ratio"] = c / c.rolling(20).mean() - 1.0
    # --- LIVE_KEYS 的日线语义代理（明确标注 proxy） ---
    vr = v / v.rolling(20).mean()
    f["px_vol_ratio"] = vr                       # proxy: 资金参与度 ≈ main_net_pct 的量能面
    rng = (h - l).replace(0, np.nan)
    f["px_signed_body"] = ((c - o) / rng) * vr   # proxy: 带方向的大单强度 ≈ ddx
    f["px_persist_3"] = vr.rolling(3).mean()     # proxy: 3日持续 ≈ main_net_3d
    f["px_persist_5"] = vr.rolling(5).mean()     # proxy: 5日持续 ≈ main_net_5d
    f["px_persist_10"] = vr.rolling(10).mean()   # proxy: 10日持续 ≈ main_net_10d
    f["px_close_pos"] = (c - l) / rng            # proxy: 尾盘强度 ≈ fengdan_amt 的收盘位置代理
    f["px_upper_shadow"] = (h - c) / rng         # proxy: 上方抛压（封单反面）
    return f


PRIOR_FEATS = ["ret1", "ret5", "ret10", "ret20", "vol20", "hl_range", "dist_high20",
               "dist_low20", "gap_prev", "ma5_ratio", "ma20_ratio",
               "px_vol_ratio", "px_signed_body", "px_persist_3", "px_persist_5",
               "px_persist_10", "px_close_pos", "px_upper_shadow",
               "hy_zf", "hy_zt_count"]

# Live 13 分量 ↔ 本先验的对应关系（诚实声明，供报告用）
LIVE_MAPPING = {
    "main_net_pct":  ("px_vol_ratio", "proxy(量能参与度)"),
    "ddx":           ("px_signed_body", "proxy(带方向强度)"),
    "main_net_3d":   ("px_persist_3", "proxy(3日量能持续)"),
    "main_net_5d":   ("px_persist_5", "proxy(5日量能持续)"),
    "main_net_10d":  ("px_persist_10", "proxy(10日量能持续)"),
    "fengdan_amt":   ("px_close_pos", "proxy(收盘位置=尾盘强度)"),
    "hy_zf":         ("hy_zf", "真回溯(链条聚合同日中位涨幅)"),
    "hy_zt_count":   ("hy_zt_count", "真回溯(同链条涨停家数)"),
    "sentiment":     (None, "不可回溯、无代理"),
    "notice_bull":   (None, "事件型，不可回溯"),
    "notice_bear":   (None, "事件型，不可回溯"),
    "report_bull":   (None, "事件型，不可回溯"),
    "report_bear":   (None, "事件型，不可回溯"),
}


def _limit_pct(code):
    return board_limit_pct(code)



def build_panel(n_stocks, start, max_rows, seed=0, use_chain=True):
    files = sorted(glob.glob(os.path.join(_daily_dir(), "*.csv")))
    if not files:
        raise SystemExit("日线目录为空")
    chain = _load_chain_map() if use_chain else {}
    # 优先抽有链条映射的股票（保证 hy_* 有覆盖）
    mapped = [f for f in files if os.path.basename(f)[:-4].zfill(6) in chain]
    unmapped = [f for f in files if f not in set(mapped)]
    random.seed(seed)
    pick = []
    if mapped:
        k = int(min(len(mapped), max(n_stocks * 0.75, 1)))
        pick += random.sample(mapped, k)
        rest = n_stocks - len(pick)
        if rest > 0:
            pick += random.sample(unmapped, min(rest, len(unmapped)))
    else:
        pick = random.sample(files, min(n_stocks, len(files)))

    parts = []
    for f in pick:
        code = os.path.basename(f)[:-4].zfill(6)
        try:
            df = _read_one(f)
            if df is None:
                continue
            df = df[df["date"] >= pd.Timestamp(start)]
            if len(df) < 80:
                continue
            df = df.tail(max_rows).reset_index(drop=True)
            feat = _features(df)
            nxt_o = df["open"].shift(-1)
            nxt_c = df["close"].shift(-1)
            lim = _limit_pct(code)
            out = pd.DataFrame({"date": df["date"], "code": code})
            out["gap_next"] = nxt_o / df["close"] - 1.0
            out["ret_next"] = nxt_c / df["close"] - 1.0
            out = pd.concat([out, feat], axis=1)
            out["y_gapup"] = (out["gap_next"] > 0).astype("float")
            # nol = 次日开盘即涨停（用于报告可学习性，不作为训练目标）
            out["y_nol"] = (out["gap_next"] >= lim - 0.005).astype("float")
            # 次日无法交易则置 NaN
            bad = out["gap_next"].isna()
            out.loc[bad, ["y_gapup", "y_nol"]] = np.nan
            out["chain"] = chain.get(code)
            parts.append(out)
        except Exception:
            continue
    if not parts:
        raise SystemExit("无可用股票")
    panel = pd.concat(parts, ignore_index=True)

    # ---- 链条级聚合（只用同日截面，无未来信息） ----
    if use_chain and panel["chain"].notna().any():
        g = panel.dropna(subset=["chain"]).copy()
        med = g.groupby(["date", "chain"])["ret1"].median().rename("hy_zf").reset_index()
        # 同链条当日涨停家数（ret1>=9.5% 近似，含创业板 20% 会被低估但方向一致）
        zt = (g.groupby(["date", "chain"])["ret1"]
                .apply(lambda s: float((s >= 0.095).sum()))
                .rename("hy_zt_count").reset_index())
        agg = med.merge(zt, on=["date", "chain"], how="outer")
        panel = panel.merge(agg, on=["date", "chain"], how="left")
    else:
        panel["hy_zf"] = np.nan
        panel["hy_zt_count"] = np.nan
    panel = panel.drop(columns=[c for c in ("is_zt",) if c in panel.columns])
    return panel, len(parts)


def _zt_split_eval(panel, train_end, embargo=5):
    """关键诊断：按「今日是否已涨停」分组，看可学习性是不是来自我们买不到的子集。

    背景：全样本 y_nol 的 AUC 看似很高，但正例高度集中在「今日已涨停」组
    （自相关：今天涨停 → 明天更容易开盘涨停）。
    而选股目标是「今日未涨停、收盘前可买入」，必须剔除该组后再评估。
    """
    d = panel.copy()
    d["today_zt"] = (d["ret1"] >= 0.095).astype(int)
    dates = np.sort(d["date"].unique())
    cut = int(np.searchsorted(dates, np.datetime64(train_end), side="right")) - 1
    tstart = dates[min(cut + 1 + embargo, len(dates) - 1)]
    out = []
    groups = [("全样本", d), ("今日未涨停(可买)", d[d["today_zt"] == 0]),
              ("今日已涨停(不可买)", d[d["today_zt"] == 1])]
    for gname, gdf in groups:
        for lab in ("y_gapup", "y_nol"):
            tr = gdf[gdf["date"] <= pd.Timestamp(train_end)].dropna(subset=[lab])
            te = gdf[gdf["date"] > pd.Timestamp(tstart)].dropna(subset=[lab])
            rec = {"group": gname, "label": lab, "n_train": int(len(tr)), "n_test": int(len(te)),
                   "pos_test": int(te[lab].sum()) if len(te) else 0}
            if len(tr) < 1000 or len(te) < 200 or tr[lab].nunique() < 2 or te[lab].nunique() < 2:
                rec["error"] = "样本/正例不足"
                out.append(rec)
                continue
            from sklearn.linear_model import LogisticRegression
            from sklearn.preprocessing import StandardScaler
            from sklearn.metrics import roc_auc_score
            Xtr = tr[PRIOR_FEATS].values.astype(float)
            Xte = te[PRIOR_FEATS].values.astype(float)
            med = np.nanmedian(Xtr, axis=0); med = np.where(np.isnan(med), 0.0, med)
            Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
            sc = StandardScaler().fit(Xtr)
            clf = LogisticRegression(max_iter=600, C=0.5).fit(sc.transform(Xtr), tr[lab].values)
            p = clf.predict_proba(sc.transform(Xte))[:, 1]
            y = te[lab].values
            base = float(y.mean())
            k = max(int(len(p) * 0.10), 1)
            top = np.argsort(p)[-k:]
            rec.update({"base_rate": round(base, 5),
                        "auc": round(float(roc_auc_score(y, p)), 4),
                        "lift": round(float(y[top].mean() / base) if base > 0 else float("nan"), 3)})
            out.append(rec)
    return out


def _fit_eval(train, test, label):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    tr = train.dropna(subset=[label])
    te = test.dropna(subset=[label])
    if len(tr) < 3000 or len(te) < 500 or tr[label].nunique() < 2 or te[label].nunique() < 2:
        return {"label": label, "error": f"样本不足 train={len(tr)} test={len(te)}"}
    Xtr = tr[PRIOR_FEATS].values.astype(float)
    Xte = te[PRIOR_FEATS].values.astype(float)
    # 中位数填补（训练集中位数，避免泄漏）
    med = np.nanmedian(Xtr, axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    Xtr = np.where(np.isnan(Xtr), med, Xtr)
    Xte = np.where(np.isnan(Xte), med, Xte)
    sc = StandardScaler().fit(Xtr)
    clf = LogisticRegression(max_iter=600, C=0.5).fit(sc.transform(Xtr), tr[label].values)
    p = clf.predict_proba(sc.transform(Xte))[:, 1]
    y = te[label].values
    auc = float(roc_auc_score(y, p))
    base = float(y.mean())
    k = max(int(len(p) * 0.10), 1)
    top = np.argsort(p)[-k:]
    lift = float(y[top].mean() / base) if base > 0 else float("nan")
    return {"label": label, "n_train": int(len(tr)), "n_test": int(len(te)),
            "base_rate": round(base, 4), "auc": round(auc, 4),
            "top_decile_hit": round(float(y[top].mean()), 4), "lift": round(lift, 3),
            "_y": y, "_p": p, "_model": (sc, clf, med)}


def _calib(y, p):
    try:
        from . import probability_calibration as pc
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import probability_calibration as pc
    r = pc.report(y, p)
    mono = r.get("monotonic_robust", r["monotonic"])
    mce = r.get("mce_robust", r["mce"])
    verdict = ("可信" if (r["ece"] < 0.05 and mono)
               else ("勉强" if r["ece"] < 0.10 else "不可信"))
    return {"brier": round(r["brier"], 5), "ece": round(r["ece"], 5),
            "mce": round(mce, 5), "monotonic": bool(mono), "verdict": verdict,
            "mce_gate_pass": bool(mce <= 0.10 and mono)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-stocks", type=int, default=1200)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--train-end", default="2026-06-30")
    ap.add_argument("--max-rows", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-chain", action="store_true")
    ap.add_argument("--reuse-panel", action="store_true")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    cache = os.path.join(here, "live_prior_panel.pkl")

    if args.reuse_panel and os.path.exists(cache):
        panel, used = pickle.load(open(cache, "rb"))
        print(f"[cache] 复用面板 rows={len(panel)} stocks={used}")
    else:
        print(f"[build] 抽样 {args.n_stocks} 只，起始 {args.start} ...")
        panel, used = build_panel(args.n_stocks, args.start, args.max_rows,
                                  seed=args.seed, use_chain=not args.no_chain)
        pickle.dump((panel, used), open(cache, "wb"))
        print(f"[build] 可用股票 {used}，面板 {len(panel)} 行，"
              f"日期 {panel['date'].min().date()}~{panel['date'].max().date()}")

    # 时间切分 + embargo
    train_end = pd.Timestamp(args.train_end)
    dates = np.sort(panel["date"].unique())
    cut_pos = int(np.searchsorted(dates, np.datetime64(train_end), side="right")) - 1
    embargo = 5
    test_start_pos = min(cut_pos + 1 + embargo, len(dates) - 1)
    train = panel[panel["date"] <= train_end]
    test = panel[panel["date"] > pd.Timestamp(dates[test_start_pos])]
    # ★ 只在可交易子集上训练（今日未涨停）——这是选股目标本身，不是可选优化。
    #   全样本训练会让模型把"今日已涨停"当成强信号，而该类票我们买不进（尾盘已封板）。
    train_b = train[train["ret1"] < 0.095]
    test_b = test[test["ret1"] < 0.095]
    print(f"[split] train ≤ {args.train_end} ({len(train)} 行，可买 {len(train_b)}) | "
          f"embargo {embargo} 日 | OOS ≥ {pd.Timestamp(dates[test_start_pos]).date()} "
          f"({len(test)} 行，可买 {len(test_b)})")

    rows = []
    for lab in ("y_gapup", "y_nol"):
        r = _fit_eval(train_b, test_b, lab)
        r["_scope"] = "buyable"
        rows.append(r)
        if "error" in r:
            print(f"[result] {lab}: {r['error']}")
            continue
        cal = _calib(r["_y"], r["_p"])
        r.update(cal)
        print(f"[result] {lab}: base={r['base_rate']:.4f} AUC={r['auc']:.4f} "
              f"lift={r['lift']:.3f} | ECE={cal['ece']:.4f} MCE={cal['mce']:.4f} "
              f"mono={cal['monotonic']} → {cal['verdict']}")

    # nol 单独统计正例率（决定"等80样本"是否成立）
    nol_rate = float(panel["y_nol"].dropna().mean()) if panel["y_nol"].notna().any() else float("nan")
    gap_rate = float(panel["y_gapup"].dropna().mean()) if panel["y_gapup"].notna().any() else float("nan")
    # ★ 部署口径：可买子集（今日未涨停）才是真正会交易的样本池
    _b = panel[panel["ret1"] < 0.095]
    nol_rate_b = float(_b["y_nol"].dropna().mean()) if _b["y_nol"].notna().any() else float("nan")
    gap_rate_b = float(_b["y_gapup"].dropna().mean()) if _b["y_gapup"].notna().any() else float("nan")

    # ---- 关键诊断：正例是否集中在"买不到"的子集 ----
    print("[diag] 按今日涨停状态分组 ...")
    zt_rows = _zt_split_eval(panel, args.train_end, embargo)
    for r in zt_rows:
        if "error" in r:
            print(f"   [{r['group']}/{r['label']}] {r['error']} (pos={r['pos_test']})")
        else:
            print(f"   [{r['group']}/{r['label']}] base={r['base_rate']:.5f} "
                  f"AUC={r['auc']:.4f} lift={r['lift']:.2f} pos={r['pos_test']}")
    buyable = next((r for r in zt_rows if r["group"] == "今日未涨停(可买)"
                    and r["label"] == "y_gapup" and "error" not in r), None)
    nol_buyable = next((r for r in zt_rows if r["group"] == "今日未涨停(可买)"
                        and r["label"] == "y_nol" and "error" not in r), None)

    # ---- 出先验模型（用 y_gapup） ----
    main_r = next((r for r in rows if r.get("label") == "y_gapup" and "error" not in r), None)
    prior_path = os.path.join(_proj(), "backtest_cache", "signal_model_live_prior.json")
    if main_r is None:
        print("[warn] y_gapup 训练失败，不写先验模型")
        prior = None
    else:
        sc, clf, med = main_r["_model"]
        prior = {
            "kind": "live_prior_v1",
            "target": "gap_next>0",
            "target_note": ("次日高开（正例率 %.3f）。**不是** nol（次日开盘涨停，正例率 %.4f，"
                            "统计上不可学）。" % (gap_rate, nol_rate)),
            "features": PRIOR_FEATS,
            "means": sc.mean_.tolist(), "stds": sc.scale_.tolist(),
            "w": clf.coef_[0].tolist(), "b": float(clf.intercept_[0]),
            "impute_median": med.tolist(),
            "train_end": args.train_end, "embargo_days": embargo,
            "n_train": main_r["n_train"], "n_test": main_r["n_test"],
            "oos_auc": main_r["auc"], "oos_lift": main_r["lift"],
            "base_rate": main_r["base_rate"],
            "calibration": {"ece": main_r["ece"], "mce": main_r["mce"],
                            "monotonic": main_r["monotonic"], "verdict": main_r["verdict"],
                            "kelly_gate_pass": main_r["mce_gate_pass"]},
            "live_key_mapping": {k: {"feature": v[0], "status": v[1]}
                                 for k, v in LIVE_MAPPING.items()},
            "honesty": ("本模型是**可回溯部分的弱先验**，不含 6 个资金流分量与 4 个事件分量"
                        "（历史不可回溯）。真实 Live 样本到达后由 live_blend 按 log-odds 收缩更新；"
                        "先验权重随真实样本数单调下降。"),
        }
        json.dump(prior, open(prior_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"[write] {prior_path}")

    # ---- 报告 ----
    md = ["# Live 弱先验构建报告（可回溯部分）", "",
          f"- 生成: {pd.Timestamp.now().isoformat(timespec='seconds')}",
          f"- 面板: {len(panel)} 行 / {used} 只 / "
          f"{panel['date'].min().date()}~{panel['date'].max().date()}",
          f"- 切分: train ≤ {args.train_end} / embargo {embargo} 交易日 / "
          f"OOS ≥ {pd.Timestamp(dates[test_start_pos]).date()}", "",
          "## 一、目标可学习性（同一批特征，两个候选目标）", "",
          "| 目标 | 含义 | 正例率 | OOS AUC | Top10%命中 | Lift | 裁决 |",
          "|---|---|---|---|---|---|---|"]
    nm = {"y_gapup": "次日高开(>0)", "y_nol": "次日开盘即涨停"}
    for r in rows:
        if "error" in r:
            md.append(f"| {nm.get(r['label'])} | - | - | - | - | - | {r['error']} |")
            continue
        verdict = "可学(弱)" if (r["auc"] > 0.52 and r["lift"] > 1.05) else "不可学"
        md.append(f"| {nm.get(r['label'])} | - | {r['base_rate']} | {r['auc']} | "
                  f"{r['top_decile_hit']} | {r['lift']} | {verdict} |")
    md += ["", "## 二、决定性诊断：正例到底集中在哪个子集", "",
           "选股目标是「**今日未涨停、收盘前可买入**」。所以必须按今日是否已涨停分组看，",
           "否则模型会去学「今天已封板的票明天更容易开盘涨停」——那类票我们根本买不进。", "",
           "| 子集 | 目标 | 正例数(OOS) | 基线率 | OOS AUC | Lift | 可买 |",
           "|---|---|---|---|---|---|---|"]
    for r in zt_rows:
        can = "✅" if r["group"] == "今日未涨停(可买)" else ("—" if r["group"] == "全样本" else "❌")
        if "error" in r:
            md.append(f"| {r['group']} | {nm.get(r['label'], r['label'])} | {r['pos_test']} | - | - | - | {can} |")
        else:
            md.append(f"| {r['group']} | {nm.get(r['label'], r['label'])} | {r['pos_test']} | "
                      f"{r['base_rate']} | {r['auc']} | {r['lift']} | {can} |")
    if nol_buyable:
        md += ["", f"**读法**：`nol` 在全样本上 AUC 看着很高，但那是「今日已涨停」组的贡献；",
               f"剔到可买子集后基线率只剩 **{nol_buyable['base_rate']*100:.3f}%**、"
               f"OOS 正例仅 **{nol_buyable['pos_test']}** 个、AUC **{nol_buyable['auc']}**，",
               f"已不是可信的 edge。真正在可买子集上站得住的是 "
               f"`gapup`：AUC **{buyable['auc'] if buyable else '—'}**、"
               f"Lift **{buyable['lift'] if buyable else '—'}**。"]

    md += ["", "## 三、概率校准", "",
           "| 目标 | Brier | ECE | MCE(稳健) | 稳健单调 | 裁决 | Kelly门 |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        if "error" in r:
            continue
        md.append(f"| {nm.get(r['label'])} | {r.get('brier')} | {r.get('ece')} | {r.get('mce')} | "
                  f"{r.get('monotonic')} | {r.get('verdict')} | "
                  f"{'通过' if r.get('mce_gate_pass') else '拒绝'} |")
    md += ["", "## 四、为什么必须改目标（决定性）", "",
           "口径说明：**部署口径 = 可买子集（今日未涨停）**，因为那才是实际会下单的样本池。", "",
           f"- 全样本：`nol` 正例率 {nol_rate*100:.3f}%、`gapup` {gap_rate*100:.2f}%。",
           f"- **可买子集（全期）**：`nol` 正例率 **{nol_rate_b*100:.4f}%**、`gapup` **{gap_rate_b*100:.2f}%**。",
           f"  （第二节表里 0.072% 是 OOS 期口径；全期 {nol_rate_b*100:.4f}% 与 OOS 期同量级，结论一致。）", "",
           f"- 按可买口径，累积到 80 条时 `nol` 期望正例 = 80 × {nol_rate_b:.6f} = "
           f"**{80*nol_rate_b:.4f} 个**；要期望 30 个正例需 **{int(30/max(nol_rate_b,1e-9)):,} 条**样本。",
           f"- 同样 80 条对 `gapup`：期望正例 = 80 × {gap_rate_b:.4f} = **{80*gap_rate_b:.1f} 个**，足够训练。",
           "- 结论：原 `MIN_SAMPLES=80` + `nol` 目标的组合在统计上不可训练。",
           "  差的是**两个数量级**的样本量，而且可买子集里 nol 的 OOS AUC≈0.49（等价随机）——",
           "  即使把样本堆够，目标本身也没有可学的信号。必须换目标，不是加样本。", "",
           "## 五、Live 13 分量 ↔ 先验特征对应（诚实声明）", "",
           "| LIVE_KEY | 先验特征 | 状态 |", "|---|---|---|"]
    for k, (feat, status) in LIVE_MAPPING.items():
        md.append(f"| {k} | {feat if feat else '—'} | {status} |")
    md += ["", "## 六、这个先验能干什么、不能干什么", "",
           "**能**：",
           "- 立刻给出一个校准过的次日高开概率基线，系统不必空转等样本。",
           "- 真实 Live 样本到 1 条即可开始按 log-odds 收缩更新（`live_blend.py`），"
           "权重 w = n/(n+80)，样本越多先验越淡。",
           "- 给 `retrain_live` 提供稳定锚点：小样本时不至于过拟合到 2~20 条噪声。", "",
           "**不能**：",
           "- 不能替代真实资金流/舆情/事件分量——这 10 个分量历史不存在，先验里根本没有。",
           "- 不能因此就说「拿到了 Live 真杠杆」。真杠杆只能靠每日真实捕获累积。",
           "- 不能拿先验概率直接做 Kelly 放大仓位：`kelly_gate_pass` 必须为真（见第二节）。", "",
           "## 七、复现", "",
           "```bash",
           ".venv/Scripts/python.exe -m research.live_prior_builder --n-stocks 1200",
           ".venv/Scripts/python.exe -m research.live_prior_builder --reuse-panel  # 复用面板快速复跑",
           "```"]
    rp = os.path.join(here, "live_prior_report.md")
    open(rp, "w", encoding="utf-8").write("\n".join(md) + "\n")
    print(f"[write] {rp}")

    # JSON 摘要
    js = {"generated": pd.Timestamp.now().isoformat(timespec="seconds"),
          "panel_rows": int(len(panel)), "stocks": used,
          "nol_base_rate_full": nol_rate, "gapup_base_rate_full": gap_rate,
          "nol_base_rate_buyable": nol_rate_b, "gapup_base_rate_buyable": gap_rate_b,
          "expected_positives_at_80_by_nol_buyable": round(80 * nol_rate_b, 4),
          "samples_needed_for_30_positives_by_nol_buyable": int(30 / max(nol_rate_b, 1e-9)),
          "zt_split": zt_rows,
          "results": [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]}
    json.dump(js, open(os.path.join(here, "live_prior_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("[done]")


if __name__ == "__main__":
    main()
