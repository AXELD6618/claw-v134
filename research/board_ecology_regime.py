# -*- coding: utf-8 -*-
"""
research/board_ecology_regime.py — 连板生态 / 资金轮动 regime 与圣杯 edge 的 regime 条件化回测
================================================================================================
目的（突破战役 #5 衍生，§12 "板块轮动时点" 方向的具体化）：
  一段口语化"板学"心智模型（龙头吸血≈美联储周期、冰点=中位补跌、冰点后新热点出水）提出一个
  **可证伪假设**：圣杯尾盘信号（T 日买非涨停 → T+1 涨停事件）的 edge，是否在"连板生态冰点"
  状态下显著更高？本模块把它落成**只读回测**，用系统已有的 2018–2026 日线 limit-up 数据验证。

方法论纪律（与 oos_eval_harness / forward_oos 完全一致，绝不降门）：
  - 复用 oos_eval_harness.CANDIDATES 的候选信号函数与 buyable(未涨停/未跌停) 前置。
  - 复用 forward_oos.eval_records + adjudicate_grail：次日涨停 lift≥1.5 ∧ 信号日超额>0 ∧
    t_vs市≥2.0 ∧ t_vs空≥2.0 ∧ PurgedCV≥0.60 ∧ n≥200 —— 闸门一字不改。
  - 基准涨停率用**同 regime 条件**的可买宇宙次日涨停率（ice/non-ice 分别计），避免 flat 基准混淆。
  - 仅 WATCH 层诊断；不写 core_factor_model / buy_score；不调 cap_override；不覆盖任何生产产物。

输出：
  backtest_cache/board_breadth.json      每日连板广度（b2/b3/b4/zt）
  backtest_cache/board_market_acc.npz    按日市场累加器（用于 regime 条件基准率）
  backtest_cache/board_ecology_events.jsonl  每个候选信号的(test期)触发事件（含信号日市场T+1收益）

用法：
  python -m research.board_ecology_regime            # 构建+分析（命中缓存则复用）
  python -m research.board_ecology_regime --rebuild  # 强制重算
"""
from __future__ import annotations
import json, time, argparse
from pathlib import Path
import numpy as np
import pandas as pd
import oospf_validator as V
from research.oos_eval_harness import load_master, CANDIDATES
from research.forward_oos import eval_records, make_record

LIMIT = V.LIMIT
DAILY = V.DAILY
MIN_LEN = V.MIN_LEN
CACHE = V.CACHE
BREADTH_CACHE = CACHE / "board_breadth.json"
EVENTS_CACHE = CACHE / "board_ecology_events.jsonl"
MK_CACHE = CACHE / "board_market_acc.npz"

NAMES = list(CANDIDATES)


def _consecutive_count(flags: np.ndarray) -> np.ndarray:
    """返回每个 True 在其连续 True 段中的 1-based 位置（连板计数）；False→0。向量化。"""
    n = len(flags)
    out = np.zeros(n, dtype=int)
    if n == 0:
        return out
    reset = ~flags
    last = np.maximum.accumulate(np.where(reset, np.arange(n), -1))
    order = np.arange(n) - last
    out = np.where(flags, order, 0)
    return out


def build_breadth_and_events(files, master, mpos, is_test, force=False):
    if (not force) and BREADTH_CACHE.exists() and EVENTS_CACHE.exists() and MK_CACHE.exists():
        print("[cache] 命中 breadth/events/market 缓存，跳过重建（--rebuild 强制）")
        return
    M = len(master)
    b2 = np.zeros(M); b3 = np.zeros(M); b4 = np.zeros(M); zt = np.zeros(M)
    mkt_f1_sum = np.zeros(M); mkt_f1_cnt = np.zeros(M)
    buy_cnt = np.zeros(M); buy_zt = np.zeros(M)
    with EVENTS_CACHE.open("w", encoding="utf-8") as ef:
        for fi, f in enumerate(files):
            try:
                df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
            except Exception:
                continue
            if len(df) < MIN_LEN:
                continue
            close = df["收盘"].to_numpy(float)
            openp = df["开盘"].to_numpy(float)
            high = df["最高"].to_numpy(float)
            low = df["最低"].to_numpy(float)
            vol = df["成交量"].to_numpy(float)
            n = len(close)
            dates_i = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
            keys = np.array([mpos[d] for d in dates_i], dtype=np.int64)
            ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
            f1 = np.full(n, np.nan); f1[:-1] = close[1:] / close[:-1] - 1.0
            is_zt = np.zeros(n, bool); is_zt[1:] = (~np.isnan(ret[1:])) & (ret[1:] >= LIMIT)
            clu = _consecutive_count(is_zt)
            # —— 连板广度 + 市场累加器（向量化 scatter-add）——
            zt_mask = is_zt[1:]
            np.add.at(zt, keys[1:][zt_mask], 1)
            m = clu >= 2; np.add.at(b2, keys[m], 1)
            m = clu >= 3; np.add.at(b3, keys[m], 1)
            m = clu >= 4; np.add.at(b4, keys[m], 1)
            vf = ~np.isnan(f1[1:])
            np.add.at(mkt_f1_sum, keys[1:][vf], f1[1:][vf])
            np.add.at(mkt_f1_cnt, keys[1:][vf], 1)
            buyable = (~np.isnan(ret[1:])) & (ret[1:] < LIMIT) & (ret[1:] > -LIMIT)
            np.add.at(buy_cnt, keys[1:][buyable], 1)
            np.add.at(buy_zt, keys[1:][buyable & (f1[1:] >= LIMIT)], 1)
            # —— 候选信号事件（test 期、buyable、T+1 有效）——
            sigs = {nm: CANDIDATES[nm](close, openp, high, low, vol) for nm in NAMES}
            buyable_t = (~np.isnan(ret)) & (ret < LIMIT) & (ret > -LIMIT)
            validf1_t = ~np.isnan(f1)
            testk = is_test[keys]
            for nm in NAMES:
                m2 = sigs[nm] & buyable_t & validf1_t & testk
                idx = np.where(m2)[0]
                if idx.size == 0:
                    continue
                for i in idx:
                    d = keys[i]
                    mc = mkt_f1_cnt[d]
                    mkt = (mkt_f1_sum[d] / mc) if mc > 0 else float("nan")
                    rec = make_record(dates_i[i], f.name[:-4], nm, True,
                                      float(f1[i]), bool(f1[i] >= LIMIT), mkt)
                    ef.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if (fi + 1) % 1000 == 0:
                print(f"  [load] {fi+1}/{len(files)}")
    breadth = {int(master[d]): {"b2": int(b2[d]), "b3": int(b3[d]),
                                "b4": int(b4[d]), "zt": int(zt[d])} for d in range(M)}
    json.dump(breadth, open(BREADTH_CACHE, "w"))
    np.savez(MK_CACHE, mkt_f1_sum=mkt_f1_sum, mkt_f1_cnt=mkt_f1_cnt,
             buy_cnt=buy_cnt, buy_zt=buy_zt)
    print(f"[cache] 写出 breadth({BREADTH_CACHE.name}) + market_acc + events({EVENTS_CACHE.name})")


def ice_masks(master, breadth):
    """基于连板广度 b2 的 regime 定义（稳健、非过拟合）：
       DEF-A 近期崩塌（字面"冰点"）：b2[d] <= 0.5 * trailing中位数(b2,[d-20,d-1]) 且 该中位数>=10
         （曾活跃、腰斩；用中位数而非 max 以免疫极端单日 surge 离群值）。
       COLD/HOT 三分层在 analyze 中按 test 期 b2 分布计算（下/上 33 分位），用于直接检验
       "edge 在低广度(冰点)regime 更高"的假说。
       返回 (arr, ice_A) —— arr 为全 master 的 b2 数组，供 analyze 计算 tercile。"""
    M = len(master)
    arr = np.array([breadth.get(str(int(master[d])), {}).get("b2", 0) for d in range(M)], dtype=float)
    ice_A = np.zeros(M, bool)
    for d in range(M):
        lo = max(0, d - 20)
        win = arr[lo:d]                      # [d-20, d-1]，避免前视
        if win.size >= 10:
            tmed = np.median(win)
            if tmed >= 10 and arr[d] <= 0.5 * tmed:
                ice_A[d] = True
    return arr, ice_A


def _base_for(mask, acc):
    bc = acc["buy_cnt"][mask].sum(); bz = acc["buy_zt"][mask].sum()
    return (float(bz) / float(bc)) if bc > 0 else float("nan")


def _summ(res):
    zt = res.get("sig_zt"); lift = res.get("lift"); xm = res.get("excess_vs_mkt")
    tvm = res.get("t_vs_mkt"); tvc = res.get("t_vs_cash")
    zts = f"{zt*100:.2f}%" if zt == zt else "nan"
    lf = f"{lift:.2f}" if lift == lift else "nan"
    xms = f"{xm:.1f}bp" if xm == xm else "nan"
    tv = f"{tvm:.2f}/{tvc:.2f}" if (tvm == tvm and tvc == tvc) else "nan"
    return f"n={res['n']:>5} zt={zts:>7} lift={lf:>5} xm={xms:>8} t市/空={tv:>11} -> {res['verdict']}"


def analyze(events_file, master, mpos, is_test, breadth, arr, ice_A, acc):
    recs = [json.loads(l) for l in open(events_file, encoding="utf-8")]
    M = len(master)
    base_all = _base_for(is_test, acc)
    # —— test 期 b2 三分层（COLD=下33分位 / HOT=上33分位），直接检验"冰点态 edge 更高" ——
    test_arr = arr[is_test]
    cold_thr = float(np.percentile(test_arr, 33)); hot_thr = float(np.percentile(test_arr, 67))
    mask_cold = is_test & (arr <= cold_thr)
    mask_hot = is_test & (arr >= hot_thr)
    mask_iceA = is_test & ice_A
    base_cold = _base_for(mask_cold, acc)
    base_hot = _base_for(mask_hot, acc)
    base_iceA = _base_for(mask_iceA, acc)
    cold_idx = set(int(master[d]) for d in np.where(mask_cold)[0])
    hot_idx = set(int(master[d]) for d in np.where(mask_hot)[0])
    iceA_idx = set(int(master[d]) for d in np.where(mask_iceA)[0])
    by_feat = {}
    for r in recs:
        by_feat.setdefault(r["feature"], []).append(r)
    # 覆盖度
    n_test_days = int(is_test.sum())
    n_cold = int(mask_cold.sum()); n_hot = int(mask_hot.sum()); n_iceA = int(mask_iceA.sum())
    print(f"\n[regime 覆盖] test 交易日={n_test_days} | COLD(下33分位,b2<={cold_thr:.0f})={n_cold}({100*n_cold/n_test_days:.1f}%) "
          f"| HOT(上33,b2>={hot_thr:.0f})={n_hot}({100*n_hot/n_test_days:.1f}%) | 冰点A(近期崩塌)={n_iceA}({100*n_iceA/n_test_days:.1f}%)")
    print(f"[基准率] 全test可买次日涨停={base_all*100:.3f}% | COLD态={base_cold*100:.3f}% | HOT态={base_hot*100:.3f}% | 冰点A态={base_iceA*100:.3f}%")

    print("\n=== 候选信号 × regime 条件化圣杯裁定（adjudicate_grail 同门，绝不降门槛）===")
    print("  (lift=次日涨停率/同regime基准率；xm=信号日超额vs同期市场；t市/空=t_vs市/t_vs空)")
    print("  注：regime 内 n<200 时裁定必为 REJECT_GRAIL（样本不足），仅看 raw 度量，不做终判。")
    concentrated = []
    cold_better = []
    for nm in NAMES:
        rs = by_feat.get(nm, [])
        if not rs:
            continue
        all_r = eval_records(rs, nm, base_all)
        cold_r = eval_records([r for r in rs if r["date"] in cold_idx], nm, base_cold)
        hot_r = eval_records([r for r in rs if r["date"] in hot_idx], nm, base_hot)
        iceA_r = eval_records([r for r in rs if r["date"] in iceA_idx], nm, base_iceA)
        print(f"\n[{nm}]")
        print(f"  ALL    : {_summ(all_r)}")
        print(f"  COLD   : {_summ(cold_r)}")
        print(f"  HOT    : {_summ(hot_r)}")
        print(f"  ICE_A  : {_summ(iceA_r)}")
        # 集中度判定（文本假说：edge 在冰点/低广度态更高）
        if cold_r["n"] >= 200 and hot_r["n"] >= 200:
            cold_edge = (cold_r["lift"] >= 1.5) and (cold_r["excess_vs_mkt"] > 0)
            hot_edge = (hot_r["lift"] >= 1.5) and (hot_r["excess_vs_mkt"] > 0)
            if cold_r["excess_vs_mkt"] > hot_r["excess_vs_mkt"] + 5:   # 冰点态超额明显更优
                cold_better.append((nm, cold_r["excess_vs_mkt"], hot_r["excess_vs_mkt"],
                                    cold_r["lift"], hot_r["lift"]))
            if cold_edge and not hot_edge:
                concentrated.append((nm, cold_r["lift"], cold_r["excess_vs_mkt"], hot_r["lift"], hot_r["excess_vs_mkt"]))

    print("\n=== 假说检验结论（文本：圣杯edge在连板生态冰点态更高）===")
    if concentrated:
        for nm, cli, cxm, hli, hxm in concentrated:
            print(f"  ✅ {nm}: COLD lift={cli:.2f}(xm={cxm:.1f}bp) vs HOT lift={hli:.2f}(xm={hxm:.1f}bp) "
                  f"→ edge 显著集中于低广度(冰点)态（可作 regime 条件层，WATCH）")
    else:
        print("  未发现任一候选在 COLD 态 n≥200 且 lift≥1.5∧超额>0、同时 HOT 态不满足。")
    if cold_better:
        print("  ⚠️ 边缘信号（冰点态超额优于热态≥5bp，但未达完整门）：")
        for nm, cxm, hxm, cli, hli in cold_better:
            print(f"     {nm}: COLD xm={cxm:.1f}bp(lift={cli:.2f}) vs HOT xm={hxm:.1f}bp(lift={hli:.2f})")
    print("  → 小结：本数据集（现有日线候选）上，文本'冰点态 edge 更高'假说以 raw 度量审视；")
    print("     与近四月一致，圣杯级 edge 不在历史日线简单形态。MICRO_accum（真实盘中微观结构）")
    print("     无法历史回测，须等 live 累积后才可做同样 regime 检验。本模块不新增 alpha、不进 buy_score。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="强制重算 breadth/events/市场累加器")
    a = ap.parse_args()
    t0 = time.time()
    print("[board_ecology_regime] 加载日线 master…")
    files, master, mpos, is_test = load_master()
    print(f"[board_ecology_regime] {len(files)} 只；master={len(master)} 交易日；TEST≥{V.TEST_START_I}")
    build_breadth_and_events(files, master, mpos, is_test, force=a.rebuild)
    breadth = json.load(open(BREADTH_CACHE))
    acc = np.load(MK_CACHE)
    arr, ice_A = ice_masks(master, breadth)
    analyze(EVENTS_CACHE, master, mpos, is_test, breadth, arr, ice_A, acc)
    print(f"\n[done] t={time.time()-t0:.0f}s ; 缓存: {BREADTH_CACHE.name} / {EVENTS_CACHE.name} / {MK_CACHE.name}")


if __name__ == "__main__":
    main()
