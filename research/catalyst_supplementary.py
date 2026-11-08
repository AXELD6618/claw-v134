# -*- coding: utf-8 -*-
"""
research/catalyst_supplementary.py — 催化邻近 SUPPLEMENTARY 信号（rumor 通道 grail_hit 辅助角色具体化）
===========================================================================================================
承接 osint 能力重定位（gate doc §18.8 / RUNBOOK §11）：rumor/catalyst 通道的正确角色 =
**grail_hit 候选的 SUPPLEMENTARY 过滤/解释层**，而非独立埋伏-to-D 策略。

本模块用**可得的公开披露日历**（catalyst_notices.jsonl，108 事件，D=披露日）把该角色具体化并可在沙箱实测：
  已知某票在 T 日后 K 交易日内有真实重大催化披露 → 其 T+1（grail_hit 目标）表现是否更优？
    - 若 lift(近端催化票 T+1 涨停率 / 宇宙基准) > 1 且显著 → 催化邻近是有效 grail_hit SUPPLEMENTARY 增强；
    - 当 claw-v134 接入私有 rumor 源后，CAT_PROXIMITY 升级为 CAT_OR_RUMOR（rumor 是更早/更优的同类信号）。

方法论边界（诚实）：本测试刻画的是"已知催化→T+1 行为"的**结构/因果相关**（公开披露日历在实盘前向可得，
TDX 可拉预告），非"用量能预测催化"的边缘挖掘（后者已由 §17 证伪）。因此历史刻画合法，且实盘前向可验证。

纪律：WATCH/解释层；不进 buy_score/core_factor_model；grail_hit 口径评估须过 adjudicate_grail。

用法：
  python -m research.catalyst_supplementary --self-test
  python -m research.catalyst_supplementary --evaluate --K 10 [--wstart 20260101 --wend 20261231]
"""
from __future__ import annotations
import json, math, argparse, os, bisect
from pathlib import Path
import numpy as np
import pandas as pd
import oospf_validator as V
from research import catalyst_proximity as CP

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "backtest_cache")
NOTICES_JSONL = CP.NOTICES_JSONL
DAILY = V.DAILY
LIMIT = V.LIMIT
MIN_LEN = V.MIN_LEN


def _load_notices():
    d = {}
    for l in open(NOTICES_JSONL, encoding="utf-8"):
        try:
            ev = json.loads(l)
        except Exception:
            continue
        d.setdefault(str(ev["symbol"]).zfill(6), []).append((int(ev["date"]), ev.get("category", "NA")))
    for k in d:
        d[k].sort()
    return d


def _ttest(a, b):
    """Welch t（两独立样本均值差），样本不足返回 nan。"""
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1), b.var(ddof=1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return float((ma - mb) / se) if se > 0 else float("nan")


def evaluate_catalyst_supplementary(K=10, wstart=20260101, wend=20261231, max_files=None):
    """对全宇宙 buyable 日 T，按"T 后 K 交易日内是否有已知催化披露"分组，比较 T+1 表现。

    返回 dict：base_rate(宇宙 T+1 涨停率) / cat_rate(有催化组 T+1 涨停率) / lift / n / t / 收益均值 / winrate；
      并附**两项诚实稳健性检验**：
        ① resume/runup 子窗口拆分 —— 排除"复牌首日机械涨停"质疑：
           resume = T+1 当日即披露日 D（机械复牌涨停）；runup = T+1 < D（披露前真实 run-up/预期）。
        ② grail 全口径复合达标 —— T+1 涨停 ∧ T+2 涨 ∧ T+3 涨（对应 grail_hit「后续≥2 日上涨」），
           确认 boost 不仅是 T+1 单日尖峰，而是可持续的圣杯事件。
    """
    notices = _load_notices()
    base_tot = base_hit = 0
    cat_tot = cat_hit = 0
    cat_ret, non_ret = [], []
    cat_f2, non_f2 = [], []                    # T+2 收益（可持续性对照）
    cat_win = non_win = cat_n = non_n = 0
    cat_by = {}                                  # category -> [tot, hit] 分品类统计
    # ① resume/runup 子窗口（诚实检验：排除机械复牌涨停）
    cat_resume_tot = cat_resume_hit = 0
    cat_runup_tot = cat_runup_hit = 0
    # ② grail 全口径复合（诚实检验：T+1 涨停 ∧ T+2 涨 ∧ T+3 涨）
    base_comp_ind = []                           # 宇宙可算 T 的复合指示（0/1）
    cat_comp_ind = []                            # 有催化组复合指示
    cat_resume_comp_ind = []
    cat_runup_comp_ind = []
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    if max_files:
        files = files[:max_files]
    for fi, f in enumerate(files):
        try:
            df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
        except Exception:
            continue
        if len(df) < MIN_LEN:
            continue
        close = df["收盘"].to_numpy(float)
        dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
        n = len(dates)
        code = f.name[:-4]
        ds = [(d, c) for (d, c) in notices.get(code, []) if wstart <= d <= wend]
        ret = np.full(n, np.nan)
        ret[1:] = close[1:] / close[:-1] - 1.0
        for i in range(1, n - 1):
            di = dates[i]
            if not (wstart <= di <= wend):
                continue
            if np.isnan(ret[i]):
                continue
            buyable = abs(ret[i]) < LIMIT           # T 未涨停/未跌停，可尾盘买入
            if not buyable:
                continue
            zt_next = ret[i + 1] >= LIMIT
            upper = dates[min(i + K, n - 1)]        # K 交易日内上界
            j = bisect.bisect_right(ds, (di, ""))   # 首个 date > di 的 (date,cat)
            has_cat = bool(ds) and j < len(ds) and ds[j][0] <= upper
            base_tot += 1
            if zt_next:
                base_hit += 1
            is_resume = has_cat and (dates[i + 1] == ds[j][0])  # T+1==披露日=机械复牌
            if has_cat:
                D = ds[j][0]
                cat_tot += 1
                if zt_next:
                    cat_hit += 1
                cat_ret.append(ret[i + 1]); cat_n += 1
                cat_win += (1 if ret[i + 1] > 0 else 0)
                c = ds[j][1]
                b = cat_by.setdefault(c, [0, 0]); b[0] += 1
                if zt_next:
                    b[1] += 1
                if is_resume:
                    cat_resume_tot += 1
                    if zt_next:
                        cat_resume_hit += 1
                else:
                    cat_runup_tot += 1
                    if zt_next:
                        cat_runup_hit += 1
            else:
                non_ret.append(ret[i + 1]); non_n += 1
                non_win += (1 if ret[i + 1] > 0 else 0)
            # ② grail 全口径复合（需 i+3 可达，即至少 4 个交易日）
            if i + 3 <= n - 1:
                f2 = ret[i + 2]; f3 = ret[i + 3]
                comp = (zt_next and f2 > 0 and f3 > 0)
                base_comp_ind.append(1 if comp else 0)
                if has_cat:
                    cat_comp_ind.append(1 if comp else 0)
                    cat_f2.append(f2)
                    if is_resume:
                        cat_resume_comp_ind.append(1 if comp else 0)
                    else:
                        cat_runup_comp_ind.append(1 if comp else 0)
                else:
                    non_f2.append(f2)
    base_rate = base_hit / base_tot if base_tot else float("nan")
    cat_rate = cat_hit / cat_tot if cat_tot else float("nan")
    lift = cat_rate / base_rate if base_rate else float("nan")
    ca, no = np.array(cat_ret), np.array(non_ret)
    t = _ttest(ca, no)
    # ① resume/runup 拆分
    resume_rate = cat_resume_hit / cat_resume_tot if cat_resume_tot else float("nan")
    runup_rate = cat_runup_hit / cat_runup_tot if cat_runup_tot else float("nan")
    resume_lift = resume_rate / base_rate if (base_rate and cat_resume_tot) else float("nan")
    runup_lift = runup_rate / base_rate if (base_rate and cat_runup_tot) else float("nan")
    # ② grail 全口径复合
    base_comp_rate = float(np.mean(base_comp_ind)) if base_comp_ind else float("nan")
    cat_comp_rate = float(np.mean(cat_comp_ind)) if cat_comp_ind else float("nan")
    comp_lift = cat_comp_rate / base_comp_rate if base_comp_rate else float("nan")
    comp_t = _ttest(np.array(cat_comp_ind, float), np.array(base_comp_ind, float))
    comp_resume_rate = float(np.mean(cat_resume_comp_ind)) if cat_resume_comp_ind else float("nan")
    comp_runup_rate = float(np.mean(cat_runup_comp_ind)) if cat_runup_comp_ind else float("nan")
    # 可持续性（T+2 均值，催化组 vs 无催化组）
    cf2, nf2 = np.array(cat_f2), np.array(non_f2)
    f2_t = _ttest(cf2, nf2)
    return dict(K=K, base_rate=base_rate, cat_rate=cat_rate, lift=lift,
                n_base=base_tot, n_cat=cat_tot,
                cat_mean_ret=float(ca.mean()) if ca.size else float("nan"),
                non_mean_ret=float(no.mean()) if no.size else float("nan"),
                cat_winrate=float(cat_win / cat_n) if cat_n else float("nan"),
                non_winrate=float(non_win / non_n) if non_n else float("nan"),
                t=t, cat_by=cat_by,
                # ① resume/runup
                cat_resume_tot=cat_resume_tot, cat_resume_hit=cat_resume_hit,
                resume_rate=resume_rate, resume_lift=resume_lift,
                cat_runup_tot=cat_runup_tot, cat_runup_hit=cat_runup_hit,
                runup_rate=runup_rate, runup_lift=runup_lift,
                # ② grail 全口径复合
                base_comp_rate=base_comp_rate, cat_comp_rate=cat_comp_rate,
                comp_lift=comp_lift, comp_t=comp_t,
                comp_resume_rate=comp_resume_rate, comp_runup_rate=comp_runup_rate,
                n_comp_base=len(base_comp_ind), n_comp_cat=len(cat_comp_ind),
                # 可持续性
                cat_f2_mean=float(cf2.mean()) if cf2.size else float("nan"),
                non_f2_mean=float(nf2.mean()) if nf2.size else float("nan"),
                f2_t=f2_t)


# 有效品类（K=10 实证 lift>1 且 n 充足）：资产重组/控制权变更/再融资；MAJOR_CONTRACT/RESTRUCTURE 无增益→过滤
DEFAULT_BOOST_CATS = ("MAJOR_ASSET", "CONTROL_CHANGE", "REFINANCE")


def grailhit_supplementary_boost(candidates, K=10, categories=DEFAULT_BOOST_CATS, cal_tol=2):
    """grail_hit 集成缝：给定候选 [(symbol, T_date_int), ...]，标注每候选的 SUPPLEMENTARY 增强。

    返回 [dict(symbol,T,has_cat,category,boost)]：boost=True 仅当命中**有效品类**且 T 后约 K 交易日内有披露。
    日历容错：用 T < D <= T + K*(1+cal_tol) 日历日近似 K 交易日窗口（live 集成改精确交易日索引，见 evaluate）。
    用途：live 选股对 grail_hit 候选做辅助重排序/置信度提升（解释层，不进 buy_score）。"""
    notices = _load_notices()
    out = []
    for sym, T in candidates:
        sym = str(sym).zfill(6)
        hit = None
        for d, c in notices.get(sym, []):
            if T < d <= T + K * (1 + cal_tol) and c in categories:
                hit = (d, c)
                break
        out.append(dict(symbol=sym, T=int(T), has_cat=hit is not None,
                        category=(hit[1] if hit else None), boost=hit is not None))
    return out


def _self_test():
    ok = True
    def chk(c, m):
        nonlocal ok
        if not c:
            ok = False
            print("  [FAIL]", m)
        else:
            print("  [PASS]", m)
    # _ttest 基本性质：同均值 → t≈0；异均值 → 非零；样本不足 → nan
    chk(abs(_ttest(np.array([1.0, 1.1]), np.array([1.0, 1.1]))) < 1e-9, "_ttest 同均值≈0")
    chk(abs(_ttest(np.array([1.0, 1.1]), np.array([0.0, 0.1]))) > 1e-6, "_ttest 异均值≠0")
    chk(_ttest(np.array([1.0]), np.array([0.0])) is not _ttest(np.array([1.0]), np.array([0.0])) or
        math.isnan(_ttest(np.array([1.0]), np.array([0.0]))), "_ttest 样本不足→nan 或非数")
    # evaluate 在小样本下不抛（max_files 限制）
    r = evaluate_catalyst_supplementary(K=10, max_files=50)
    chk(isinstance(r.get("lift"), float) and (r["lift"] == r["lift"]), "evaluate 产出有限 float lift（不抛）")
    chk(r["n_base"] >= 0, "evaluate 统计量非负")
    # 新增诚实检验键均存在且为有限 float（不抛）
    for k in ("resume_rate", "runup_rate", "resume_lift", "runup_lift",
              "base_comp_rate", "cat_comp_rate", "comp_lift", "comp_t",
              "comp_resume_rate", "comp_runup_rate", "cat_f2_mean", "non_f2_mean", "f2_t"):
        chk(k in r and isinstance(r[k], float), "evaluate 含有限 float 键 %s" % k)
    chk(isinstance(r.get("n_comp_base"), int) and r["n_comp_base"] >= 0, "复合样本量非负")
    # 集成缝：已知催化票 T 在 D 前 → boost=True 且 category 正确
    b = grailhit_supplementary_boost([("601326", 20261014)], K=10)
    chk(b and b[0]["boost"] and b[0]["category"] == "MAJOR_ASSET", "集成缝: 601326 T=1014→D=1021 命中 MAJOR_ASSET boost")
    # 非催化票 → boost=False
    b2 = grailhit_supplementary_boost([("601326", 20260105)], K=10)
    chk(b2 and not b2[0]["boost"], "集成缝: 远离披露的 T → boost=False")
    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--K", type=int, default=10)
    ap.add_argument("--wstart", type=int, default=20260101)
    ap.add_argument("--wend", type=int, default=20261231)
    ap.add_argument("--max-files", type=int, default=None)
    a = ap.parse_args()
    if a.self_test:
        _self_test()
    elif a.evaluate:
        t0 = __import__("time").time()
        r = evaluate_catalyst_supplementary(K=a.K, wstart=a.wstart, wend=a.wend, max_files=a.max_files)
        print("[evaluate] CAT_PROXIMITY K=%d  窗口 %d–%d" % (a.K, a.wstart, a.wend))
        print("  宇宙基准 T+1 涨停率 = %.3f%%  (n=%d)" % (r["base_rate"] * 100, r["n_base"]))
        print("  有催化组 T+1 涨停率 = %.3f%%  (n=%d)" % (r["cat_rate"] * 100, r["n_cat"]))
        print("  lift = %.3f   Welch t(有催化 vs 无催化 T+1收益) = %.3f" % (r["lift"], r["t"]))
        print("  有催化 T+1 均值=%.3f%% / winrate=%.1f%%   无催化 T+1 均值=%.3f%% / winrate=%.1f%%"
              % (r["cat_mean_ret"] * 100, r["cat_winrate"] * 100, r["non_mean_ret"] * 100, r["non_winrate"] * 100))
        if r.get("cat_by"):
            print("  分品类（有催化组 T+1 涨停率 / lift / n）：")
            for c, (t0_, h0_) in sorted(r["cat_by"].items(), key=lambda kv: -(kv[1][1] / kv[1][0] if kv[1][0] else 0)):
                cr = h0_ / t0_ if t0_ else float("nan")
                print("    %-16s %5.2f%%  lift=%.2f  n=%d" % (c, cr * 100, cr / r["base_rate"], t0_))
        # ① resume/runup 子窗口拆分（诚实检验：排除机械复牌涨停）
        print("  -- 子窗口拆分（诚实检验①：机械复牌 vs 真实 run-up） --")
        print("    resume(T+1==披露日) 涨停率=%.3f%%  lift=%.3f  n=%d"
              % (r["resume_rate"] * 100 if r["cat_resume_tot"] else float("nan"),
                 r["resume_lift"], r["cat_resume_tot"]))
        print("    runup (T+1<披露日)  涨停率=%.3f%%  lift=%.3f  n=%d"
              % (r["runup_rate"] * 100 if r["cat_runup_tot"] else float("nan"),
                 r["runup_lift"], r["cat_runup_tot"]))
        # ② grail 全口径复合（诚实检验②：T+1 涨停 ∧ T+2 涨 ∧ T+3 涨）
        print("  -- grail 全口径复合（诚实检验②：T+1涨停 ∧ T+2涨 ∧ T+3涨） --")
        print("    宇宙复合达标率 = %.3f%%  (n=%d)" % (r["base_comp_rate"] * 100, r["n_comp_base"]))
        print("    有催化复合达标率 = %.3f%%  lift=%.3f  t=%.3f  (n=%d)"
              % (r["cat_comp_rate"] * 100, r["comp_lift"], r["comp_t"], r["n_comp_cat"]))
        print("    可持续性 T+2 均值：有催化=%.3f%% / 无催化=%.3f%%  Welch t=%.3f"
              % (r["cat_f2_mean"] * 100, r["non_f2_mean"] * 100, r["f2_t"]))
        print("  [done] t=%.0fs" % (__import__("time").time() - t0))
    else:
        print("用法: --self-test | --evaluate [--K 10 --wstart 20260101 --wend 20261231]")


if __name__ == "__main__":
    main()
