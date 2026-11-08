# -*- coding: utf-8 -*-
"""
research/forward_oos.py — 前向实测 OOS 闭环（圣杯对齐门，绝不降门）
================================================================================
目的：突破战役 §12 的核心基建。圣杯级 edge 不在历史数据里（已四轮证伪），只能在
      "实时微观结构/催化/席位级 lhb" 新特征上线后，以**真实前瞻样本**（非历史回测）诚实评估。

本模块提供：
  1. 记录结构 + JSONL 累积（信号 → T+1 结果），供 live 采集器写入。
  2. eval_records()：对同一特征的真实前瞻样本，按 adjudicate_grail 口径算
     lift / 超额vs市 / t_vs市 / t_vs空 / Purged-CV / n，并裁定（与 oos_eval_harness 同门）。
  3. --dryrun：用历史 lhb 信号生成"伪前瞻"样本，验证机械与 oos_lhb_signal 一致
     （lift 3.531 / REJECT），证明闭环可用。

用法：
  python -m research.forward_oos --dryrun            # 验证机械（生成伪样本并评估）
  python -m research.forward_oos --eval <jsonl>      # 评估真实累积样本（需 --base 基准涨停率）
  （live 采集器未来调用 append_records() 写入 jsonl）
"""
from __future__ import annotations
import json, math, time, argparse
from pathlib import Path
import numpy as np
import pandas as pd
import oospf_validator as V
from research.oos_eval_harness import adjudicate_grail

LHB_DIR = V.CACHE / "f10_cache"
DAILY = V.DAILY
LIMIT = V.LIMIT
MIN_LEN = V.MIN_LEN


# ── 记录结构 ──
def make_record(date, code, feature, signal, f1_ret, zt_next, mkt_ret):
    return {"date": int(date), "code": str(code), "feature": feature,
            "signal": bool(signal), "f1_ret": float(f1_ret),
            "zt_next": bool(zt_next), "mkt_ret": (None if mkt_ret != mkt_ret else float(mkt_ret))}


def append_records(jsonl_path, records):
    p = Path(jsonl_path)
    with p.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(records)


def eval_records(records, feature, base_rate):
    """对单个 feature 的真实前瞻样本评估（与 adjudicate_grail 同门槛）。"""
    rs = [r for r in records if r.get("feature") == feature and r.get("signal")]
    f1 = np.array([r["f1_ret"] for r in rs], dtype=float)
    mk = np.array([(r["mkt_ret"] if r["mkt_ret"] is not None else float("nan"))
                   for r in rs], dtype=float)
    nn = int(f1.size)
    sig_zt = float((np.array([r["zt_next"] for r in rs])).mean()) if nn else float("nan")
    lift = (sig_zt / base_rate) if (base_rate and base_rate > 0) else float("nan")
    mean_f1 = float(f1.mean()) if nn else float("nan")
    ex = (f1 - mk)[~np.isnan(f1 - mk)]
    ex_mkt = float(ex.mean() * 100) if ex.size else float("nan")
    t_vm = float(ex.mean() / ex.std() * math.sqrt(ex.size)) if ex.size > 2 and ex.std() > 0 else float("nan")
    t_vc = float(f1.mean() / f1.std() * math.sqrt(f1.size)) if nn > 2 and f1.std() > 0 else float("nan")
    order = np.argsort(np.array([r["date"] for r in rs], dtype=int))
    f1o = f1[order]
    pcv = V.purged_cv_stats(f1o, 1, V.PURGED_CV_N_SPLITS, 1)["pos_frac"] if f1o.size > 10 else float("nan")
    res = dict(nextlimit_lift=lift, excess_vs_mkt_sig_h1=ex_mkt, t_vs_mkt_h1=t_vm,
               t_vs_cash_h1=t_vc, purged_cv_signal_h1=pcv, n_sig_buyable=nn)
    verdict, reason = adjudicate_grail(res)
    return dict(n=nn, sig_zt=sig_zt, lift=lift, mean_f1=mean_f1, excess_vs_mkt=ex_mkt,
                t_vs_mkt=t_vm, t_vs_cash=t_vc, purged_cv=pcv, verdict=verdict, reason=reason)


# ── dry-run：用历史 lhb 信号生成伪前瞻样本，验证机械 ──
def dryrun_lhb(window_start, window_end, out_jsonl):
    import json as _json
    lhb = {}
    for f in sorted(LHB_DIR.glob("lhb_20*.json")):
        try:
            d = _json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        dt = int(d.get("date"))
        if not (window_start <= dt <= window_end):
            continue
        stocks = d.get("stocks", {})
        lhb[dt] = {c: v / 100.0 for c, v in stocks.items() if isinstance(v, (int, float))}
    lhb_dates = set(lhb.keys())
    w_start, w_end = min(lhb_dates), max(lhb_dates)
    base_tot = base_hit = 0
    recs = []
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float)
        n = len(close)
        dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        f1 = np.full(n, np.nan); f1[:-1] = close[1:] / close[:-1] - 1.0
        code = f.name[:-4]
        # 同日市场（窗口内）
        mkt_sum = {}; mkt_cnt = {}
        for i in range(1, n):
            di = dates[i]
            if w_start <= di <= w_end and not np.isnan(ret[i]):
                mkt_sum[di] = mkt_sum.get(di, 0.0) + ret[i]
                mkt_cnt[di] = mkt_cnt.get(di, 0) + 1
        for i in range(1, n - 1):
            di = dates[i]
            if not (w_start <= di <= w_end):
                continue
            if np.isnan(ret[i]) or np.isnan(f1[i]):
                continue
            buyable = (ret[i] < LIMIT) and (ret[i] > -LIMIT)
            if not buyable:
                continue
            zt_next = f1[i] >= LIMIT
            base_tot += 1
            if zt_next:
                base_hit += 1
            dim1 = dates[i - 1]
            lp = lhb.get(dim1)
            r = lp.get(code) if lp else None
            mc = mkt_cnt.get(di, 0)
            mkt = (mkt_sum.get(di, 0.0) / mc) if mc > 0 else float("nan")
            # 伪前瞻：把"T-1 龙虎榜上榜(收益>0)"作为一条真实可采集的 14:50 特征样本
            if r is not None and r > 0:
                recs.append(make_record(di, code, "LHB_pos", True, f1[i], zt_next, mkt))
        if (fi + 1) % 1000 == 0:
            print(f"  [dryrun load] {fi+1}/{len(files)}")
    base_rate = (base_hit / base_tot) if base_tot else float("nan")
    append_records(out_jsonl, recs)
    return base_rate, len(recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dryrun", action="store_true", help="用历史 lhb 生成伪前瞻样本验证机械")
    ap.add_argument("--eval", help="评估真实累积 jsonl")
    ap.add_argument("--base", type=float, help="基准可买宇宙次日涨停率（eval 用）")
    ap.add_argument("--out", default="backtest_cache/forward_oos_dryrun.jsonl")
    ap.add_argument("--wstart", type=int, default=20260107)
    ap.add_argument("--wend", type=int, default=20260525)
    a = ap.parse_args()

    if a.dryrun:
        t0 = time.time()
        print("[dryrun] 生成伪前瞻样本（历史 lhb 信号）…")
        base_rate, n = dryrun_lhb(a.wstart, a.wend, a.out)
        print(f"[dryrun] 基准可买次日涨停率={base_rate*100:.3f}%  生成样本 n={n}")
        recs = [json.loads(l) for l in open(a.out, encoding="utf-8")]
        r = eval_records(recs, "LHB_pos", base_rate)
        print(f"\n[评估] LHB_pos (前向 OOS 闭环)：")
        print(f"  n={r['n']}  次日涨停率={r['sig_zt']*100:.3f}%  lift={r['lift']:.3f}")
        print(f"  信号日均值={r['mean_f1']*100:.3f}%  超额vs市={r['excess_vs_mkt']:.2f}bp")
        print(f"  t_vs市={r['t_vs_mkt']}  t_vs空={r['t_vs_cash']}  PurgedCV={r['purged_cv']}")
        print(f"  裁定={r['verdict']}  ({r['reason']})")
        print(f"  [预期] 与 oos_lhb_signal 一致：lift≈3.53 / REJECT（彩票型，负期望）")
        print(f"  [done] t={time.time()-t0:.0f}s")
    elif a.eval:
        recs = [json.loads(l) for l in open(a.eval, encoding="utf-8")]
        feats = sorted(set(r["feature"] for r in recs))
        base_rate = a.base if a.base else float("nan")
        print(f"[eval] {a.eval}  特征={feats}  基准率={base_rate*100 if base_rate==base_rate else 'nan'}%")
        for ft in feats:
            r = eval_records(recs, ft, base_rate)
            print(f"  {ft}: n={r['n']} lift={r['lift']:.3f} 超额vs市={r['excess_vs_mkt']:.2f}bp "
                  f"t市={r['t_vs_mkt']} t空={r['t_vs_cash']} PCV={r['purged_cv']} -> {r['verdict']}")
    else:
        print("用法: --dryrun  |  --eval <jsonl> --base <rate>")


if __name__ == "__main__":
    main()
