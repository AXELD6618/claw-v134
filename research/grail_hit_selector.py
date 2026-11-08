# -*- coding: utf-8 -*-
"""
research/grail_hit_selector.py — 可运行 grail_hit 候选选择器（圣杯口径，CAT_PROXIMITY SUPPLEMENTARY 集成）
========================================================================================
定位：把已验证的 CAT_PROXIMITY edge（gate doc §18.9/§18.11：harness adjudicate_grail 唯一
      PROMOTE_GRAIL）落地为**可运行**的每日 grail_hit 候选选择器，作为生产选股管线的
      SUPPLEMENTARY 重排序/解释层。封闭"已验证研究 edge ↔ 部署系统能力"缺口（生产 T5 选择器
      本地缺失/DEGRADED，RUNBOOK §12.3 集成缝的具体实现）。

输入：backtest_cache/daily 全市场日线 + catalyst_notices.jsonl 披露日历
输出：T 日（默认=最新可用交易日）可买候选，按圣杯全口径 edge（CAT_PROXIMITY 品类优先级 + 邻近度）排序。

纪律（不变）：
  - CAT_PROXIMITY 仅作 SUPPLEMENTARY 解释层/重排序，不进 buy_score / core_factor_model；
  - 不修改任何闸门（adjudicate_grail / oospf_validator 七闸门）；（本模块只读消费，零闸门改动）
  - 本模块为 dry-run 研究交付，真实部署须 claw-v134 live 验证（RUNBOOK §12 / §18.8）。
  - 七闸门 / GOALS sha256 / n_factors=0 / mining_frozen / cap_override 全未动。

  - 2026-11-08 校正（live 可达性）：公开公告是历史的(D≤今日)，live 无法预知"未来披露"，
    原严格 `as_of < D` 窗口在实盘恒为 0 候选（结构性缺陷，非 staleness）。故新增**对称窗口**：
    --include-recent 捕获"披露落地后 ±7 日内的滞后 drift"(resume 子集)，这是 CAT_PROXIMITY
    唯一实盘可达子集；其真实 lift 低于 runup 子集(5.57×/3.66×/2.24×)，须由
    grail_hit_forward_oos 前向实测验证（不填 expected_lift 虚假置信）。默认关闭以保持历史
    验证口径；仅 live/automation 显式开启。

品种优先级与实证 lift（§18.9，K=10）：MAJOR_ASSET 5.57× > CONTROL_CHANGE 3.66× > REFINANCE 2.24×。

用法：
  python -m research.grail_hit_selector --self-test
  python -m research.grail_hit_selector [--as-of YYYYMMDD] [--K 10] [--top 20] [--no-boost-filter]
  python -m research.grail_hit_selector --feed-status            # 报告 feed 覆盖度(前瞻+近期, 新鲜度)
  python -m research.grail_hit_selector --include-recent [...]   # 开启近期落地披露窗口(resume 子集, 须前向验证)

性能与纪律：
  - 全市场扫描并行化（concurrent.futures.ProcessPoolExecutor，按 CPU 核数；spawn 失败时回退串行，
    结果一致）。frontier(as_of=None) 的最新交易日由 64 文件均匀样本估计，避免二次全量扫描。
  - ⚠️ Windows 下 ProcessPoolExecutor 用 spawn 启动子进程会重入 __main__：任何"直接以脚本方式调用
    select_candidates"的入口脚本必须用 `if __name__ == "__main__":` 守卫，否则子进程递归重入。
    本模块作为库被 cloud_holy_grail 等导入时安全；CLI 入口(__main__)已含守卫。
  - 不修改任何闸门 / GOALS sha256 / n_factors=0 / mining_frozen；CAT_PROXIMITY 仍 SUPPLEMENTARY。
"""
from __future__ import annotations
import json, argparse, os
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import oospf_validator as V
from research.catalyst_supplementary import _load_notices, DEFAULT_BOOST_CATS

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = V.CACHE
DAILY = V.DAILY
LIMIT = V.LIMIT
MIN_LEN = V.MIN_LEN

# 品类优先级与实证 lift（§18.9，K=10）
CAT_PRIORITY = {"MAJOR_ASSET": 3, "CONTROL_CHANGE": 2, "REFINANCE": 1}
CAT_EXPECTED_LIFT = {"MAJOR_ASSET": 5.57, "CONTROL_CHANGE": 3.66, "REFINANCE": 2.24}

# 实盘可达子集窗口（2026-11-08 校正，诚实边界）：公开公告是历史的(D≤今日)，live 无法预知"未来披露"。
# 故 live 仅能捕获"披露落地后的滞后 drift"——D 落在 as_of 之前 RECENT_DAYS 日内（含 D==as_of 当日披露）。
# 这是 resume 子集（真实 lift 低于 runup 子集 5.57×/3.66×/2.24×），须 grail_hit_forward_oos 前向验证。
# 默认关闭(recent_days=0)以保持与历史验证口径完全一致；仅 live/automation 显式开启 --include-recent。
RECENT_DAYS = 7

OUTPUT_JSON = os.path.join(CACHE, "grail_hit_selector_output.json")


def _file_max_date(f):
    """单文件最新交易日（轻量并行求 frontier as_of 用）。"""
    try:
        d = pd.read_csv(f, usecols=["日期"]).iloc[-1]
        return int(str(d["日期"]).replace("-", ""))
    except Exception:
        return 0


# 进程内 notices 缓存（每个子进程加载一次，避免随任务重复 pickle）
_NOTICES_CACHE = None


def _get_notices():
    global _NOTICES_CACHE
    if _NOTICES_CACHE is None:
        _NOTICES_CACHE = _load_notices()
    return _NOTICES_CACHE


def _noticed_symbols_in_window(as_of, K, recent_days):
    """返回在"可被 _select_boost 匹配"的窗口内具有效品类披露的 symbol 集合。

    供 require_boost=True 时预筛 files —— 把 5225 全扫降为仅扫"窗口内命中披露"的标的，
    是 scan 性能突破（2026-11-08 阶段四）。语义不变：无披露标的本就被 require_boost 过滤，
    故预筛不丢任何候选。窗口判定必须与 _select_boost 完全一致（用 _ymd_delta 日历差，
    不能用整数减法，否则跨月边界如 as_of-7 会算成 20261099 误杀 dd=-7 的披露）。"""
    notices = _load_notices()
    cal_win = int(K * 1.5) + 2
    out = set()
    for code, evs in notices.items():
        for (D, cat) in evs:
            if cat not in CAT_PRIORITY:
                continue
            dd = _ymd_delta(as_of, D)
            # 镜像 _select_boost：pre(dd>0 且 D<=as_of+cal_win) 或 post(dd∈[-recent_days,0])
            if (dd > 0 and D <= as_of + cal_win) or (recent_days > 0 and -recent_days <= dd <= 0):
                out.add(code)
                break
    return out


def _select_boost(ds, as_of, K, ti, n, dates, recent_days):
    """从 (date,cat) 列表选 as_of 附近最近的有效品类披露（对称窗口）。

    返回 (boost, category, days_to_D, timing)：
      - timing="pre"  ：D 在 as_of 之后（前瞻/已排期事件）——历史验证口径(D<T 的 runup 子集)，
                        需未来披露，live 公开数据通常不可得；expected_lift 沿用实证值。
      - timing="post" ：D 在 as_of 之前 RECENT_DAYS 日内（含当日）——live 可达的 resume 子集
                        （披露落地后的滞后 drift）；真实 lift 低于 runup，须前向验证 → expected_lift=None。
      - 两者都不满足 → (False,None,None,None)。
    选距 as_of 绝对天数最小者；优先捕获刚落地的披露(dd≈0)。"""
    cal_win = int(K * 1.5) + 2
    best = None
    for (D, cat) in ds:
        if cat not in CAT_PRIORITY:
            continue
        dd = _ymd_delta(as_of, D)            # >0 未来 / <0 历史 / ==0 当日披露
        if dd > 0:
            hi = dates[ti + K] if ti + K < n else as_of + cal_win
            if not (as_of < D <= hi):
                continue
            timing = "pre"
        else:
            if recent_days <= 0 or dd < -recent_days:
                continue
            timing = "post"
        ad = abs(dd)
        if best is None or ad < best[0]:
            best = (ad, D, cat, timing, dd)
    if best is None:
        return (False, None, None, None)
    _, D, cat, timing, dd = best
    return (True, cat, dd, timing)


def _scan_one(args):
    """单文件扫描 worker（可在子进程并行）。返回候选 dict 或 None（被过滤/解析失败）。"""
    f, as_of, K, require_boost, recent_days = args
    notices = _get_notices()
    try:
        df = pd.read_csv(f, usecols=["日期", "收盘"]).sort_values("日期").reset_index(drop=True)
    except Exception:
        return None
    if len(df) < MIN_LEN + 10:
        return None
    code = f.name[:-4]
    dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
    close = df["收盘"].to_numpy(float)
    n = len(close)
    # 定位 as_of 行（仅取 ≤as_of 的最近行；不依赖未来价格，与历史/前沿双轨窗口兼容）
    aidx = np.where(dates == as_of)[0]
    if aidx.size:
        ti = int(aidx[-1])
    else:
        prev = dates[dates <= as_of]
        if prev.size:
            ti = int(np.where(dates == prev.max())[0][-1])
        else:
            return None
    if ti < 1:
        return None
    ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
    rT = ret[ti]
    if abs(rT) >= LIMIT:                 # T 已涨停/已跌停 → 圣杯口径不可尾盘买入
        return None
    vol10 = np.nanstd(ret[ti - 10:ti]) if ti >= 10 else np.nan
    hi20 = np.nanmax(close[ti - 20:ti]) if ti >= 20 else np.nan
    near_high = (close[ti] >= hi20 * 0.95) if hi20 == hi20 else False
    # CAT_PROXIMITY boost（对称窗口：前瞻 pre + 近期落地 post(含当日)）
    ds = notices.get(code, [])
    boost, category, days_to_D, cat_timing = _select_boost(ds, as_of, K, ti, n, dates, recent_days)
    if require_boost and not boost:
        return None
    if category is not None and category not in CAT_PRIORITY:   # 仅有效品类
        return None
    return dict(
        symbol=code, T_date=int(dates[ti]), ret_T=float(rT),
        vol10=(float(vol10) if vol10 == vol10 else None),
        near_high=bool(near_high),
        has_cat=boost, category=category, days_to_D=days_to_D, cat_timing=cat_timing,
        boost_tier=CAT_PRIORITY.get(category, 0),
        # post(resume) 子集真实 lift 低于 runup，须前向验证 → 不填实证值（避免虚假置信）
        expected_lift=(CAT_EXPECTED_LIFT.get(category) if cat_timing == "pre" else None),
    )


def _par(fn, items, ncpu, use_pool):
    """并行 map（spawn 失败时自动回退串行），保证可用性与结果一致。"""
    if not use_pool:
        return [fn(x) for x in items]
    try:
        with ProcessPoolExecutor(max_workers=ncpu) as ex:
            return list(ex.map(fn, items))
    except Exception:
        return [fn(x) for x in items]


def _ymd_delta(a: int, b: int) -> int:
    """两个 YYYYMMDD 整数间的真实日历天数差 (b - a)。"""
    try:
        da = date(a // 10000, (a // 100) % 100, a % 100)
        db = date(b // 10000, (b // 100) % 100, b % 100)
        return (db - da).days
    except Exception:
        return (b - a)  # 退化(跨月误差), 仅作兜底


def feed_coverage(as_of=None, K=10, recent_days=RECENT_DAYS):
    """feed 覆盖度（frontier 自感知，2026-11-08 校正）：
      - forward_n：as_of 之后、日历窗口(int(K*1.5)+2)内的披露数（前瞻/已排期事件；
                   live 公开数据通常为 0 —— 诚实可见，非逻辑缺陷）。
      - recent_n ：as_of 之前 recent_days 日内（含当日）的披露数（live 可达的 resume 子集；
                   由 catalyst_feed_refresh 周期刷新保证新鲜）。
    两者分别报告：forward 揭示"数据局限"，recent 揭示"实盘可触发信号量"。"""
    notices = _load_notices()
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    if as_of is None:
        step = max(1, len(files) // 64)
        sample = files[::step][:64] if len(files) > 64 else files
        mxs = [_file_max_date(f) for f in sample]
        as_of = max(mxs) if mxs else 0
    cal_win = int(K * 1.5) + 2
    fwd = []; recent = []
    for code, evs in notices.items():
        for (D, cat) in evs:
            if cat not in CAT_PRIORITY:
                continue
            dd = _ymd_delta(as_of, D)
            if as_of < D <= as_of + cal_win:
                fwd.append((code, D, cat))
            elif -recent_days <= dd <= 0:
                recent.append((code, D, cat))
    return {"as_of": as_of, "cal_win": cal_win, "recent_days": recent_days,
            "forward_n": len(fwd), "forward": sorted(fwd)[:50],
            "recent_n": len(recent), "recent": sorted(recent)[:50],
            "total_notices": sum(len(v) for v in notices.values())}


def feed_forward_coverage(as_of=None, K=10):
    """向后兼容封装：仅返回前瞻覆盖（旧调用方）。"""
    c = feed_coverage(as_of=as_of, K=K, recent_days=0)
    c.pop("recent_n", None); c.pop("recent", None); c.pop("recent_days", None)
    return c


def select_candidates(as_of=None, K=10, top_n=20, max_files=None, require_boost=True, recent_days=0):
    """扫描全宇宙，输出 T 日可买（未涨停/未跌停）且具 CAT_PROXIMITY 有效品类邻近催化的候选。

    返回 dict：as_of / n_total_scanned / n_candidates / top / all。
    排序键：品类优先级(降) → 邻近度 days_to_D(升) → near_high(降) → vol10(升)。
    """
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    ncpu = min(8, os.cpu_count() or 4)
    parallel = (ncpu > 1 and len(files) >= 200)
    if as_of is None:
        # frontier: 全市场最新交易日 = 各文件末行日期的全局最大。
        # 日线缓存按日整体刷新，所有文件共享同一最新交易日；取 64 个均匀样本即精准估计，
        # 避免 5225 文件二次扫描（文件打开 I/O 主导延迟）。小样本串行读即可，避免二次并行 spawn 开销。
        step = max(1, len(files) // 64)
        sample = files[::step][:64] if len(files) > 64 else files
        mxs = [_file_max_date(f) for f in sample]
        as_of = max(mxs) if mxs else 0
    # 效率突破（2026-11-08 阶段四）：require_boost=True 时仅扫"窗口内具有效品类披露"的标的，
    # 5225 全扫降为 ~窗口命中数（语义不变：无披露标的必被 require_boost 过滤，不丢候选）。
    if require_boost:
        wanted = _noticed_symbols_in_window(as_of, K, recent_days)
        files = [f for f in files if f.stem in wanted]
    if max_files:
        files = files[:max_files]
    args = [(f, as_of, K, require_boost, recent_days) for f in files]
    results = _par(_scan_one, args, ncpu, parallel)
    cands = [r for r in results if r is not None]
    def _key(c):
        tier = c["boost_tier"]
        dtd = c["days_to_D"] if c["days_to_D"] is not None else 9999
        vol = c["vol10"] if c["vol10"] is not None else 9.0
        return (-tier, dtd, -int(c["near_high"]), vol)
    cands.sort(key=_key)
    return dict(as_of=as_of, n_total_scanned=len(files), n_candidates=len(cands),
                top=cands[:top_n], all=cands)


def _self_test():
    ok = True
    def chk(c, m):
        nonlocal ok
        if not c:
            ok = False; print("  [FAIL]", m)
        else:
            print("  [PASS]", m)
    r = select_candidates(as_of=20261014, K=10, max_files=4000, require_boost=True)
    chk(r["n_candidates"] >= 0, "select 不抛且返回结构")
    chk(isinstance(r["as_of"], int), "as_of 为 int")
    syms = [c["symbol"] for c in r["all"]]
    chk("601326" in syms, "601326 T=1014→D=1021 命中候选")
    if "601326" in syms:
        c = next(x for x in r["all"] if x["symbol"] == "601326")
        chk(c["category"] == "MAJOR_ASSET" and c["has_cat"], "601326 标注 MAJOR_ASSET boost")
    # 非有效品类（如 MAJOR_CONTRACT）不应进入候选
    chk(all(c["category"] in CAT_PRIORITY for c in r["all"] if c["category"]), "仅有效品类候选")
    # —— 2026-11-08 对称窗口单元测试（纯函数，不依赖文件）——
    _dates = [20261004 + i for i in range(200)]   # 真实日期 int 序列，_select_boost 用其取 hi
    b = _select_boost([(20261021, "MAJOR_ASSET")], 20261014, 10, 100, 200, _dates, 0)
    chk(b[0] and b[1] == "MAJOR_ASSET" and b[3] == "pre", "pre 子集命中(timing=pre)")
    b2 = _select_boost([(20261010, "MAJOR_ASSET")], 20261014, 10, 100, 200, _dates, RECENT_DAYS)
    chk(b2[0] and b2[3] == "post" and b2[2] == -4, "post 子集命中(dd=-4, timing=post)")
    b4 = _select_boost([(20261001, "MAJOR_ASSET")], 20261014, 10, 100, 200, _dates, RECENT_DAYS)
    chk(not b4[0], "post 超出 recent_days 不命中")
    b5 = _select_boost([(20261010, "MAJOR_ASSET")], 20261014, 10, 100, 200, _dates, 0)
    chk(not b5[0], "默认 recent_days=0 不命中 post（保历史口径）")
    b6 = _select_boost([(20261010, "MAJOR_CONTRACT")], 20261014, 10, 100, 200, _dates, RECENT_DAYS)
    chk(not b6[0], "非有效品类(MAJOR_CONTRACT)过滤")
    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--as-of", type=int, default=None)
    ap.add_argument("--K", type=int, default=10)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--max-files", type=int, default=None)
    ap.add_argument("--all-candidates", action="store_true", help="输出全部候选而非仅 top")
    ap.add_argument("--no-boost-filter", action="store_true", help="不排除无 boost 候选（仅展示）")
    ap.add_argument("--include-recent", action="store_true",
                    help="开启近期落地披露窗口(±%d日, resume 子集, 须前向验证)" % RECENT_DAYS)
    ap.add_argument("--feed-status", action="store_true", help="仅报告 feed 覆盖度(前瞻+近期, 新鲜度)")
    a = ap.parse_args()
    if a.self_test:
        _self_test()
        return
    if a.feed_status:
        cov = feed_coverage(as_of=a.as_of, K=a.K, recent_days=RECENT_DAYS)
        print("[feed-status] as_of=%d  总披露=%d条" % (cov["as_of"], cov["total_notices"]))
        print("  前瞻(pre, 日历窗口±%d日)= %d 条  |  近期(resume, ±%d日)= %d 条"
              % (cov["cal_win"], cov["forward_n"], cov["recent_days"], cov["recent_n"]))
        for code, D, cat in cov["forward"][:20]:
            print("    [pre ] %s  D=%d  %s" % (code, D, cat))
        for code, D, cat in cov["recent"][:20]:
            print("    [post] %s  D=%d  %s" % (code, D, cat))
        if cov["forward_n"] == 0:
            print("[feed-status] ⚠️ 前瞻=0 属数据局限(公开数据无未来披露)，非逻辑缺陷")
        if cov["recent_n"] == 0:
            print("[feed-status] ⚠️ 近期=0 → 若确无近期重大催化则正常；否则运行 catalyst_feed_refresh --merge 刷新")
        return
    require_boost = not a.no_boost_filter
    rd = RECENT_DAYS if a.include_recent else 0
    res = select_candidates(as_of=a.as_of, K=a.K, top_n=a.top, max_files=a.max_files,
                           require_boost=require_boost, recent_days=rd)
    top = res["all"] if a.all_candidates else res["top"]
    print("[grail_hit_selector] as_of=%d  扫描=%d  候选=%d" %
          (res["as_of"], res["n_total_scanned"], res["n_candidates"]))
    print("  %-8s %-10s %8s %8s %6s %-14s %6s %8s" %
          ("symbol", "T_date", "ret_T%", "vol10%", "nearHi", "category", "dtd", "expLift"))
    for c in top:
        print("  %-8s %-10d %8.2f %8.2f %6s %-14s %6s %8s" % (
            c["symbol"], c["T_date"], (c["ret_T"] * 100 if c["ret_T"] == c["ret_T"] else 0),
            (c["vol10"] * 100 if c["vol10"] else 0), str(c["near_high"]),
            (c["category"] or "-"), (c["days_to_D"] if c["days_to_D"] is not None else "-"),
            (("%.2f" % c["expected_lift"]) if c["expected_lift"] else "-")))
    if not a.no_boost_filter:
        with open(OUTPUT_JSON, "w") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print("  已写 %s" % OUTPUT_JSON)
    # frontier 自感知：最新交易日(as_of=None 或显式前沿)且 0 候选时，显式报告 feed 新鲜度，
    # 让"feed 陈旧→安静 0 候选"可见而非沉默（诚实但不隐藏短板）。
    if (a.as_of is None or a.as_of >= res["as_of"]) and res["n_candidates"] == 0:
        cov = feed_coverage(as_of=res["as_of"], K=a.K, recent_days=RECENT_DAYS)
        print("[feed] 前瞻(pre)=%d 条(总披露 %d 条); 近期(resume,±%d日)=%d 条"
              % (cov["forward_n"], cov["total_notices"], cov["recent_days"], cov["recent_n"]))
        if cov["recent_n"] == 0:
            print("  → 近期窗口零候选：若确无近期重大催化则正常；否则刷新 feed 或开启 --include-recent")


if __name__ == "__main__":
    main()
