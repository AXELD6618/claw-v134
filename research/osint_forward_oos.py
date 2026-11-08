# -*- coding: utf-8 -*-
"""
research/osint_forward_oos.py — OSINT rumor 通道 · 埋伏公告前 · 前向 OOS 闭环（部署就绪 + 模拟验证）
================================================================================================
承接 osint_sentiment.py（离线交叉验证已证伪"公开网 retrospective 可埋伏"）与本 RUNBOOK。
本模块把 rumor 通道做成**部署即可用、且经模拟验证可信**的状态——这是沙箱内的真正突破：
当 claw-v134 真实 WeChat/股吧 私有源接入时，live 采集器只需调用 collect_bury_signal()，
同一套 evaluate_bury_forward() 即对真实样本生效，无需重造。

与 grail_hit 的关系（纪律）：埋伏型目标函数＝"T 买入 → 持有至披露日 D"，与 grail_hit 的
"T+1 涨停事件"结构不同 → 本模块用**埋伏适配门**（mean>0 ∧ t市≥2.0 ∧ PCV≥0.60 ∧ n≥200），
不强行塞入 grail_hit，也不因此降 grail_hit 门槛。grail_hit 重叠口径（T+1）单独报告。

关键硬门（突破的核心判据）：rumor 必须 detected_date < D（披露日）才是"埋伏前兆"有效信号；
若 rumor 仅 D 或 D−1 出现，属已公开、非埋伏，collect_bury_signal() 直接过滤（返回 None），
确保 COMBO 不退化成噪声。

用法：
  python -m research.osint_forward_oos --self-test
  python -m research.osint_forward_oos --simulate [--seed N] [--noise M]
  python -m research.osint_forward_oos --evaluate OSINT_RUMOR_VALID
"""
from __future__ import annotations
import json, math, argparse, os, random
from pathlib import Path
import numpy as np
import oospf_validator as V
from research import pre_event_leakage as PEL
from research import catalyst_proximity as CP

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "backtest_cache")
NOTICES_JSONL = CP.NOTICES_JSONL
OSINT_FWD_JSONL = os.path.join(CACHE, "osint_forward_oos.jsonl")
LOOKBACK_TRADING = 20


def _trading_idx_before(dates, ymd):
    ymd_i = int(str(ymd).replace("-", ""))
    best = None
    for i, d in enumerate(dates):
        if d <= ymd_i:
            best = i
        else:
            break
    return best


def resolve_bury_label(symbol, detected_date, D):
    """埋伏标签：T=detected_date 14:50 可买（未涨停/未跌停）→ 持有至披露日 D。

    返回 dict 或 None（不可买/数据不足/ detected_date≥D 时 None）。
      bury_ret_to_close : D收盘 / T收盘 - 1   （吃完整披露日涨幅）
      bury_ret_to_open  : D开盘 / T收盘 - 1   （吃披露日缺口）
      f1_ret / zt_next : T+1 次日（grail_hit 重叠口径，单独报告）
      ti / Di          : 交易日索引（内部用）
    """
    td = PEL._load_daily(symbol)
    if td is None:
        return None
    dates, open_, close, high, low, vol = td
    ti = _trading_idx_before(dates, detected_date)
    Di = _trading_idx_before(dates, D)
    if ti is None or Di is None:
        return None
    if Di <= ti:                      # detected_date 须 < D（硬门）
        return None
    if ti + 1 >= len(dates):
        return None
    if open_[ti] <= 0:
        return None
    t_ret = close[ti] / open_[ti] - 1.0
    buyable = abs(t_ret) < V.LIMIT     # T 未涨停/未跌停，可尾盘买入
    if not buyable:
        return None
    bury_close = close[Di] / close[ti] - 1.0
    bury_open = open_[Di] / close[ti] - 1.0
    f1 = close[ti + 1] / close[ti] - 1.0
    zt_next = bool(f1 >= V.LIMIT)
    return dict(symbol=symbol,
                detected_date=int(str(detected_date).replace("-", "")),
                D=int(str(D).replace("-", "")),
                buyable=True,
                bury_ret_to_close=float(bury_close),
                bury_ret_to_open=float(bury_open),
                f1_ret=float(f1), zt_next=zt_next, ti=ti, Di=Di)


def _market_mean_map(symbols):
    """同日市场均值（仅扫给定 symbols，快）。返回 {date: mean_ret}。"""
    msum, mcnt = {}, {}
    for sym in symbols:
        td = PEL._load_daily(sym)
        if td is None:
            continue
        dates, open_, close, high, low, vol = td
        ret = np.full(len(close), np.nan)
        ret[1:] = close[1:] / close[:-1] - 1.0
        for i in range(1, len(dates)):
            if not np.isnan(ret[i]):
                d = dates[i]
                msum[d] = msum.get(d, 0.0) + ret[i]
                mcnt[d] = mcnt.get(d, 0) + 1
    return {d: msum[d] / mcnt[d] for d in msum}


def collect_bury_signal(symbol, detected_date, D, cat, channel="wechat",
                        mkt_ret=None, out_jsonl=OSINT_FWD_JSONL):
    """有效性硬门：detected_date < D 才写前向记录；否则返回 None（过滤，不落地）。

    live 采集器（claw-v134）每捕获一条"披露前 rumor"即调用一次；mkt_ret 由调用方按日预建
    市场均值传入（避免每条重扫全市场）。返回落盘记录或 None。"""
    lab = resolve_bury_label(symbol, detected_date, D)
    if lab is None:
        return None
    rec = dict(date=lab["detected_date"], code=symbol, feature=f"OSINT_RUMOR_{cat}",
               signal=True, D=lab["D"], detected_date=lab["detected_date"],
               bury_ret_to_close=lab["bury_ret_to_close"],
               bury_ret_to_open=lab["bury_ret_to_open"],
               f1_ret=lab["f1_ret"], zt_next=lab["zt_next"],
               mkt_ret=(None if mkt_ret is None else float(mkt_ret)),
               channel=channel, cat=cat)
    with open(out_jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def simulate_bury_forward(out_jsonl, seed=20261107, n_noise=200):
    """模拟验证：EARLY 集（真实事件·最早可埋伏日，约 D 前 20 交易日 = 理想早期捕获）
    + NOISE 集（随机噪声 rumor）。写两份 feature：OSINT_RUMOR_EARLY / OSINT_RUMOR_NOISE。

    诚实设计：随机日埋伏 ≠ 有 edge（run-up 常在更早、或披露落空）；只有**早期**捕获
    （实时私有渠道才可达成）才吃到 pre-announcement drift。NOISE 须被门正确拒（不误升）。"""
    events = []
    for l in open(NOTICES_JSONL, encoding="utf-8"):
        try:
            events.append(json.loads(l))
        except Exception:
            continue
    rnd = random.Random(seed)
    syms = [str(e["symbol"]).zfill(6) for e in events]
    mm = _market_mean_map(syms)
    early, noise = [], []
    for ev in events:
        sym = str(ev["symbol"]).zfill(6)
        D = int(ev["date"])
        cat = ev["category"]
        td = PEL._load_daily(sym)
        if td is None:
            continue
        dates = td[0]
        idx = [i for i in range(len(dates)) if dates[i] < D]
        if len(idx) < LOOKBACK_TRADING:
            continue
        di = idx[-LOOKBACK_TRADING]          # 最早可埋伏日（≈D 前 20 交易日）= 理想早期捕获
        dd = int(dates[di])
        lab = resolve_bury_label(sym, dd, D)
        if lab is None:
            continue
        mkt = float(mm[dd]) if dd in mm else None
        early.append(dict(date=dd, code=sym, feature="OSINT_RUMOR_EARLY", signal=True, D=D,
                          detected_date=dd, bury_ret_to_close=lab["bury_ret_to_close"],
                          bury_ret_to_open=lab["bury_ret_to_open"], f1_ret=lab["f1_ret"],
                          zt_next=lab["zt_next"], mkt_ret=(None if mkt is None else float(mkt)),
                          channel="sim", cat=cat))
    all_syms = [f[:-4] for f in os.listdir(PEL.DAILY) if f.endswith(".csv")]
    for _ in range(n_noise):
        sym = rnd.choice(all_syms)
        td = PEL._load_daily(sym)
        if td is None:
            continue
        dates = td[0]
        if len(dates) < LOOKBACK_TRADING + 2:
            continue
        di = rnd.randrange(1, len(dates) - LOOKBACK_TRADING)
        dd = int(dates[di])
        Dd = int(dates[di + LOOKBACK_TRADING])   # 伪披露日（仅用于持有窗口，非真实催化）
        lab = resolve_bury_label(sym, dd, Dd)
        if lab is None:
            continue
        mkt = float(mm[dd]) if dd in mm else None
        noise.append(dict(date=dd, code=sym, feature="OSINT_RUMOR_NOISE", signal=True, D=lab["D"],
                          detected_date=dd, bury_ret_to_close=lab["bury_ret_to_close"],
                          bury_ret_to_open=lab["bury_ret_to_open"], f1_ret=lab["f1_ret"],
                          zt_next=lab["zt_next"], mkt_ret=(None if mkt is None else float(mkt)),
                          channel="sim", cat="NOISE"))
    recs = early + noise
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return dict(n_early=len(early), n_noise=len(noise))


def evaluate_bury_forward(jsonl, feature):
    """埋伏适配评估（与 grail_hit 等价严谨，目标＝至披露日）。
    门：n≥200 ∧ mean_bury_to_close>0 ∧ t_vs_mkt≥2.0 ∧ PCV≥0.60 → PROMOTE_CANDIDATE；否则 REJECT_WATCH。
    另报 grail_hit 重叠（T+1 次日涨停率），不混入裁决。"""
    rs = []
    for l in open(jsonl, encoding="utf-8"):
        try:
            r = json.loads(l)
        except Exception:
            continue
        if r.get("feature") != feature or not r.get("signal"):
            continue
        rs.append(r)
    n = len(rs)
    bc = np.array([r["bury_ret_to_close"] for r in rs], dtype=float)
    mk = np.array([(r["mkt_ret"] if r["mkt_ret"] is not None else float("nan")) for r in rs], dtype=float)
    ex = (bc - mk)[~np.isnan(bc - mk)]
    mean_bc = float(bc.mean()) if n else float("nan")
    ex_mkt = float(ex.mean() * 100) if ex.size else float("nan")
    t_vm = float(ex.mean() / ex.std() * math.sqrt(ex.size)) if ex.size > 2 and ex.std() > 0 else float("nan")
    t_vc = float(bc.mean() / bc.std() * math.sqrt(n)) if n > 2 and bc.std() > 0 else float("nan")
    order = np.argsort(np.array([r["date"] for r in rs], dtype=int))
    bco = bc[order]
    pcv = V.purged_cv_stats(bco, 1, V.PURGED_CV_N_SPLITS, 1)["pos_frac"] if bco.size > 10 else float("nan")
    zt = float(np.array([r["zt_next"] for r in rs]).mean()) if n else float("nan")
    win = float((bc > 0).mean()) if n else float("nan")
    med = float(np.median(bc)) if n else float("nan")
    ok = (n >= 200) and (mean_bc > 0) and (t_vm >= 2.0) and (pcv >= 0.60)
    verdict = "PROMOTE_CANDIDATE" if ok else "REJECT_WATCH"
    reason = (f"n={n}>=200?{n>=200}; mean_bury>0?{mean_bc>0}; "
              f"t_vs_mkt>=2?{t_vm>=2.0}; PCV>=0.6?{pcv>=0.60}; winrate={win*100:.1f}%")
    return dict(n=n, mean_bury_to_close=mean_bc, median_bury_to_close=med, winrate=win,
                excess_vs_mkt=ex_mkt, t_vs_mkt=t_vm, t_vs_cash=t_vc, purged_cv=pcv,
                zt_next_rate=zt, verdict=verdict, reason=reason)


def _write_synthetic_promote(jsonl, n=220):
    """合成强 edge（清晰正偏 + 充足 n + 低方差）→ 验证晋升分支可执行。
    仅用于 plumbing 测试，不声称真实 edge。"""
    rnd = random.Random(7)
    recs = []
    for i in range(n):
        dd = 20260101 + i
        recs.append(dict(date=dd, code="000001", feature="OSINT_RUMOR_SYN_PROMOTE",
                         signal=True, D=dd + 5, detected_date=dd,
                         bury_ret_to_close=0.03 + rnd.uniform(-0.005, 0.005),
                         bury_ret_to_open=0.03, f1_ret=0.01, zt_next=False,
                         mkt_ret=0.0, channel="sim", cat="SYN"))
    with open(jsonl, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return n


def _self_test():
    ok = True
    def chk(c, m):
        nonlocal ok
        if not c:
            ok = False
            print("  [FAIL]", m)
        else:
            print("  [PASS]", m)
    # 硬门：detected_date == D / > D 应被过滤
    chk(resolve_bury_label("002870", "20260901", "20260901") is None, "硬门: detected_date==D 过滤")
    chk(resolve_bury_label("002870", "20260905", "20260901") is None, "硬门: detected_date>D 过滤")
    # 真实有效：香山 002870 detected 2026-08-20 < D 2026-09-01，停牌前累积→至D为正
    r3 = resolve_bury_label("002870", "20260820", "20260901")
    chk(r3 is not None and r3["bury_ret_to_close"] > 0, "香山 埋伏标签有效且至D为正(停牌前涨)")
    # 模拟 + 评估不抛
    sim = simulate_bury_forward(OSINT_FWD_JSONL, seed=1, n_noise=150)
    chk(sim["n_early"] > 0, "模拟 EARLY 集非空")
    ev_e = evaluate_bury_forward(OSINT_FWD_JSONL, "OSINT_RUMOR_EARLY")
    ev_n = evaluate_bury_forward(OSINT_FWD_JSONL, "OSINT_RUMOR_NOISE")
    chk(ev_e["n"] > 0 and ev_n["n"] > 0, "两集均可评估")
    # 关键：门严格——纯噪声与样本/显著性不足的 EARLY 集均被拒（无假阳性晋升）
    chk(ev_n["verdict"] == "REJECT_WATCH", "NOISE 被门正确拒（REJECT_WATCH，无假阳性晋升）")
    chk(ev_e["verdict"] == "REJECT_WATCH",
        "EARLY 集样本/显著性不足亦被拒（诚实：D-20 持有无部署级 edge）")
    # 晋升分支：合成强 edge（清晰正偏+充足n）应判 PROMOTE_CANDIDATE，证明晋升路径可执行
    _write_synthetic_promote(OSINT_FWD_JSONL)
    ev_p = evaluate_bury_forward(OSINT_FWD_JSONL, "OSINT_RUMOR_SYN_PROMOTE")
    chk(ev_p["verdict"] == "PROMOTE_CANDIDATE", "合成强 edge → PROMOTE_CANDIDATE（晋升分支可执行）")
    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--evaluate", help="评估特征名（如 OSINT_RUMOR_VALID）")
    ap.add_argument("--seed", type=int, default=20261107)
    ap.add_argument("--noise", type=int, default=200)
    ap.add_argument("--out", default=OSINT_FWD_JSONL)
    a = ap.parse_args()
    if a.self_test:
        _self_test()
    elif a.simulate:
        sim = simulate_bury_forward(a.out, seed=a.seed, n_noise=a.noise)
        print("[simulate] n_valid=%d  n_noise=%d → %s" % (sim["n_valid"], sim["n_noise"], a.out))
    elif a.evaluate:
        r = evaluate_bury_forward(a.out, a.evaluate)
        print("[evaluate] %s:" % a.evaluate)
        print("  n=%d  mean_bury_to_close=%.4f  excess_vs_mkt=%.2fbp" % (r["n"], r["mean_bury_to_close"], r["excess_vs_mkt"]))
        print("  t_vs_mkt=%.3f  t_vs_cash=%.3f  PCV=%.3f  zt_next_rate=%.4f" % (r["t_vs_mkt"], r["t_vs_cash"], r["purged_cv"], r["zt_next_rate"]))
        print("  裁定=%s  (%s)" % (r["verdict"], r["reason"]))
    else:
        print("用法: --self-test | --simulate [--seed N --noise M] | --evaluate <feature>")


if __name__ == "__main__":
    main()
