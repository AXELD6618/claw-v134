#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""r31_watch.py — r31_breakout（横盘后突破）观察位 · 当日全市场扫描器
=============================================================================
为什么存在
----------
r31_breakout 全市场 OOS（2024-07-01 起、513 个交易日、5225 只）按日配对超额
日均 +98.67bp / t=3.659 / NW 2.482 / 单边 10bp 成本后 2.917，参数非孤岛
（trail 3%/5%/8% → t=3.229/3.659/3.268）。
但它**未达部署标准**：Purged 5 折仅 3/5 为正、最近完整季度 2026Q3 超额 -12.7bp。
裁决 = **WATCH**：不进 buy_score、不自动给仓位、不 PROMOTE，只挂观察位让
样本外时间累积去证伪或证实。

本模块职责（单一、最小）
------------------------
  · 只读 backtest_cache/daily/*.csv，算出**当日**满足 r31 信号的股票列表。
  · 不发任何网络请求，不写任何盘（落盘由调用方决定）。
  · 退出口径 trail5（移动止损 -5%）仅作**观察期记录**，T+5 未到则标 pending，
    绝不回填/不臆断。
  · **同时记录 T+1..T+5 逐日收盘路径**（path 字段）—— 前瞻样本不可回溯，只存最终
    收益会让日后任何改持有期/改止损的重算全部作废。见 fwd_path() 注释。
  · **前瞻停止规则写死在 STOP_RULES**：300 信号日或 2027-03-31 届满时，
    日均超额<=0 / 配对 t<1.5 / 连续两季为负 → 任一触发即下线（REJECTED）。

口径（与 holygrail_align_probe.build_signals 的 r31_breakout 逐字一致，勿改参数）
------------------------------------------------------------------------------
  ret  = close/prev_close - 1
  sd10 = sh(rolling(10, min_periods=10).std(ret), 1)     # 位移1位 → 窗口 T-10..T-1，无前视
  sig  = (~isnan(sd10)) & (sd10 < 0.01) & (ret > 0.03)
  BUY 前提：T 日未涨停（ret[T] < lim - 0.005）
  sh 为"前 k 位填 NaN"的位移，**绝不用 np.roll**（环绕会污染尾部）。

字段契约（本项目既有坑）
------------------------
  下游只认 **sec_code / sec_name**（写 code/name 无效）—— 见 tail_pick.py:154、:666。
  本模块一律输出 sec_code / sec_name；名称取不到就留空串，**绝不编造**。

用法
----
  .venv/Scripts/python.exe research/r31_watch.py --date 2026-10-28
  .venv/Scripts/python.exe research/r31_watch.py            # date 缺省=今天
"""
from __future__ import annotations

import os
import sys
import glob
import json
import argparse
import datetime

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 位移 / 涨停阈值口径的唯一来源：复用 holygrail_align_probe，禁止另起炉灶
from holygrail_align_probe import sh, limit_of

DAILY = os.path.join(ROOT, "backtest_cache", "daily")
NAME_CACHE = os.path.join(ROOT, "data", "name_map.json")      # unified_tail_pick.NAME_CACHE
NAME_GLOB = os.path.join(ROOT, "data", "_names_*.json")       # 每日行情快照片段（含 name）
OBS = os.path.join(ROOT, "backtest_cache", "r31_obs.jsonl")   # 观察账本（只追加，不可回溯资产）

SD10_MAX = 0.01     # 横盘：前 10 日收益标准差 < 1%
RET_MIN = 0.03      # 突破：当日涨幅 > 3%
TRAIL = 0.05        # 移动止损：自最高收盘回撤 5% 离场
HOLD_MAX = 5        # 最长持有 5 个交易日
LIMIT_EPS = 0.005   # 涨停判定容差：lim - 0.005

# --------------------------------------------------------------------------
# 前瞻停止规则（WATCH 期限的硬约束，2026-10-28 写死 → 防"观察位永久挂着"）
# --------------------------------------------------------------------------
# 观察期届满条件（先到先算）：累积 300 个信号日，或 2027Q1 结束（2027-03-31）。
# 届满时按下述任一条件触发 → 状态由 WATCH 改 REJECTED 并下线（移出第 11 路监控池
# 与 selection_push 第 ⑦ 子策略）；全部不触发 → 才允许提请 PROMOTE 审议。
# 口径必须是「按日配对超额」（信号股等权 − 当日可买子集等权），禁止用绝对收益：
# 绝对收益在大跌日天然为正（基准大跌日 PF_day=1.506 / 日均 +0.572%），会把 Beta 当 alpha。
STOP_RULES = {
    "min_signal_days": 300,          # (a) 样本不足则不下结论，继续观察
    "deadline": "2027-03-31",        # (b) 到期强制结算，不许无限期观望
    "min_mean_excess": 0.0,          # (c) 日均超额 <= 0 → 下线
    "min_paired_t": 1.5,             # (d) 配对 t < 1.5 → 下线
    "max_neg_quarters": 2,           # (e) 连续 2 个完整季度超额为负 → 下线
}

# 数据来源：selection_push 第 ⑦ 子策略的实推产物（push_ok=true 才会被
# tail_pick_outcome 收进 by_sub_strategy），字段含 path（T+1..T+5 逐日收盘）。


# --------------------------------------------------------------------------
# 名称（只读本地缓存，绝不发网络请求）
# --------------------------------------------------------------------------
def _load_name_map():
    """合并本地名称缓存。覆盖不全时返回空串由调用方兜底，绝不编造。"""
    m = {}

    def _put(c, v):
        c = str(c or "").strip()
        if len(c) != 6 or not c.isdigit():
            return
        if isinstance(v, str):
            nm = v
        elif isinstance(v, dict):
            nm = v.get("name") or v.get("sec_name") or ""
        else:
            return
        nm = str(nm).strip()
        if nm and c not in m:
            m[c] = nm

    for p in ([NAME_CACHE] + sorted(glob.glob(NAME_GLOB))):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if isinstance(d, dict):
            for k, v in d.items():
                _put(k, v)
        elif isinstance(d, list):
            for r in d:
                if isinstance(r, dict):
                    _put(r.get("code") or r.get("sec_code"), r.get("name") or r.get("sec_name"))
    return m


# --------------------------------------------------------------------------
# 信号 / 退出
# --------------------------------------------------------------------------
def r31_mask(ret):
    """T 日 r31 信号 bool 数组 + sd10 数组（与 holygrail_align_probe 逐字同口径）。"""
    sd10 = sh(pd.Series(ret).rolling(10, min_periods=10).std().to_numpy(), 1)
    sig = (~np.isnan(sd10)) & (sd10 < SD10_MAX) & (ret > RET_MIN)
    return sig, sd10


def trail5_exit(close, i, lim):
    """trail5 退出口径。返回 (ret, exit_kind)；T+5 未到返回 (None, 'PENDING')。

    T+1 涨停 → 当日收盘兑现；否则逐日跟踪最高收盘，回撤 5% → 当日收盘离场；
    5 日内未触发 → 第 5 日收盘离场。"""
    n = len(close)
    if i + HOLD_MAX >= n:
        return None, "PENDING"
    zt_thr = lim - LIMIT_EPS
    c0 = close[i]
    if close[i + 1] / c0 - 1.0 >= zt_thr:          # T+1 涨停 → 当日收盘兑现
        return float(close[i + 1] / c0 - 1.0), "T1_LIMITUP"
    pk, r = 0.0, None
    for j in range(1, 6):
        rj = float(close[i + j] / c0 - 1.0)
        pk = max(pk, rj)
        if rj <= pk - TRAIL:                        # 自最高收盘回撤 5%
            r, kind = rj, "TRAIL_T+%d" % j
            break
    if r is None:
        r, kind = float(close[i + HOLD_MAX] / c0 - 1.0), "HOLD5"
    return r, kind


def fwd_path(close, i, lim, k_max=HOLD_MAX):
    """记录 T+1..T+k_max **逐日收盘路径**，而不只是最终收益。

    为什么必须记路径（2026-10-28 自主补漏）
    --------------------------------------
    原实现只存 trail5_ret 一个标量。一旦日后想改持有期（k=1/3/10）或止损参数
    （3%/8%），历史观察样本**全部作废、无法重算** —— 而前瞻样本是不可回溯的稀缺资产
    （每天最多几十只、要攒够 300 个信号日）。故此处一次性把原始路径存下来，
    下游任何持有期/止损口径都能从路径离线重算，不需要重新等样本。

    返回 list[dict]（长度 = 已有 bar 数，最多 k_max）：
      k      : 1..k_max，相对 T 日的第 k 个交易日
      close  : 该日收盘价（原始价，便于核对）
      ret    : 该日收盘相对 T 日收盘的收益（小数）
      zt     : 该日是否涨停（收盘涨幅 >= lim - 0.005）
    T+k 尚未产生的日子不占位（不写 None 占位、不臆断）。
    """
    n = len(close)
    c0 = close[i]
    zt_thr = lim - LIMIT_EPS
    out = []
    for k in range(1, k_max + 1):
        j = i + k
        if j >= n:
            break                                   # 数据还没到 → 直接停，不补占位
        cj = float(close[j])
        rj = float(cj / c0 - 1.0)
        out.append({"k": k, "close": round(cj, 3), "ret": round(rj, 6),
                    "zt": bool(rj >= zt_thr)})
    return out


# --------------------------------------------------------------------------
# 前瞻停止判定（唯一口径：按日配对超额）
# --------------------------------------------------------------------------
def stop_verdict(day_excess, quarters=None, today=None):
    """按 STOP_RULES 判定 r31 观察位该继续 WATCH 还是下线。

    参数
    ----
    day_excess : dict{ 'YYYY-MM-DD': 超额小数 }，每个交易日一条
                 = mean(当日信号股收益) − mean(当日可买子集全股收益)
                 **不是**绝对收益（绝对收益会把市场 Beta 记成 alpha）。
    quarters   : dict{ 'YYYYQn': 该季超额合计 }，用于「连续两季为负」判定
    today      : 结算日（缺省今天）

    返回 dict: {verdict: 'WATCH'|'REJECTED'|'PROMOTE_CANDIDATE', ...}
    """
    import math
    ds = sorted(day_excess.items())
    n = len(ds)
    out = {"n_days": n, "rules": STOP_RULES, "verdict": "WATCH", "reasons": []}

    if n:
        xs = np.array([float(v) for _, v in ds], dtype=float)
        mean = float(xs.mean())
        sd = float(xs.std(ddof=1)) if n > 1 else 0.0
        # sd≈0（超额恒定）→ t 无定义（浮点下会算出 1e16 量级假 t），标记退化并不用 t 判死
        degenerate = sd <= 1e-12
        t = float(mean / (sd / math.sqrt(n))) if not degenerate else None
        out.update({"mean_excess_bp": round(mean * 1e4, 2),
                    "paired_t": (None if t is None else round(t, 3)),
                    "degenerate": bool(degenerate),
                    "first_day": ds[0][0], "last_day": ds[-1][0]})
    else:
        mean, t = None, None
        out.update({"mean_excess_bp": None, "paired_t": None,
                    "first_day": None, "last_day": None})

    # 数据不足 + 未到期 → 继续观察（不下任何结论）
    today = today or datetime.date.today().isoformat()
    expired = str(today) >= STOP_RULES["deadline"]
    if n < STOP_RULES["min_signal_days"] and not expired:
        out["reasons"].append(
            f"样本 {n}/{STOP_RULES['min_signal_days']} 个信号日、未到 {STOP_RULES['deadline']} → 继续 WATCH")
        return out

    bad = []
    if mean is not None and mean <= STOP_RULES["min_mean_excess"]:
        bad.append(f"日均超额 {mean*1e4:+.2f}bp <= {STOP_RULES['min_mean_excess']*1e4:.0f}bp")
    if t is not None and t < STOP_RULES["min_paired_t"]:
        bad.append(f"配对 t={t:.3f} < {STOP_RULES['min_paired_t']}")
    elif t is None and out.get("degenerate"):
        out["reasons"].append("配对 t 未计算（超额序列方差为 0，退化）→ t 门槛本项跳过")
    if quarters:
        qs = sorted(quarters.items())[-STOP_RULES["max_neg_quarters"]:]
        if len(qs) == STOP_RULES["max_neg_quarters"] and all(v < 0 for _, v in qs):
            bad.append(f"连续{len(qs)}季超额为负：" + ", ".join(f"{k}={v*1e4:+.0f}bp" for k, v in qs))

    if bad:
        out["verdict"] = "REJECTED"
        out["reasons"] = bad
    else:
        out["verdict"] = "PROMOTE_CANDIDATE"
        out["reasons"].append("停止条件全部未触发 → 可提请 PROMOTE 审议（仍需过完整 OOS gate）")
    return out


# --------------------------------------------------------------------------
# 观察账本（只追加，幂等，路径可升级）
# --------------------------------------------------------------------------
def record(rows, path=None):
    """把扫描结果写入只追加观察账本 r31_obs.jsonl。

    为什么不能只依赖 discovery_candidates.json
    ------------------------------------------
    该文件每天被整份覆盖（文件名不含日期），历史命中与 path 会随之消失；
    而前瞻样本是不可回溯资产，一旦丢就永远补不回来。故落独立只追加账本。

    幂等规则（(date, sec_code) 为键）
    --------------------------------
      · 账本已有且新 path **更短** → 保持旧记录（不降级，防 T+1 覆盖 T+5）
      · 账本已有且新 path **更长** → 升级 path/trail5 字段（T+5 逐日补齐）
      · 账本没有 → 追加
    返回 (新增条数, 升级条数)。
    """
    path = path or OBS
    rows = rows or []
    if not rows:
        return 0, 0

    old = {}
    if os.path.exists(path):
        for d, err in _iter_jsonl(path):
            if err is None and isinstance(d, dict):
                k = (str(d.get("date") or ""), str(d.get("sec_code") or ""))
                if k[0] and k[1]:
                    old[k] = d

    n_new = n_up = 0
    for r in rows:
        k = (str(r.get("date") or ""), str(r.get("sec_code") or ""))
        p_new = r.get("path") or []
        if k in old:
            p_old = old[k].get("path") or []
            if len(p_new) > len(p_old):
                old[k].update({"path": p_new, "trail5_ret": r.get("trail5_ret"),
                               "trail5_exit": r.get("trail5_exit")})
                n_up += 1
        else:
            old[k] = r
            n_new += 1

    if not (n_new or n_up):
        return 0, 0

    if n_up == 0:
        # 纯追加：不重写整份账本（账本会长期增长）；仅在原文件缺尾换行时补一个
        need_nl = False
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path, "rb") as fb:
                fb.seek(-1, 2)
                need_nl = fb.read(1) != b"\n"
        with open(path, "a", encoding="utf-8") as f:
            if need_nl:
                f.write("\n")
            for r in rows:
                k = (str(r.get("date") or ""), str(r.get("sec_code") or ""))
                if k in old and old[k] is r:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        # 有路径升级 → 整份重写（先写 .tmp 再原子替换，防半截文件）
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for d in old.values():
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    return n_new, n_up


def load_obs(path=None):
    """读观察账本 → dict{(date, code): 记录}。坏行跳过，不阻断。"""
    path = path or OBS
    out = {}
    if not os.path.exists(path):
        return out
    for d, err in _iter_jsonl(path):
        if err is None and isinstance(d, dict):
            k = (str(d.get("date") or ""), str(d.get("sec_code") or ""))
            if k[0] and k[1]:
                out[k] = d
    return out


def _iter_jsonl(path):
    """容错逐行解析 jsonl：坏行 yield (None, err) 且不中断（对齐 live_archive.iter_jsonl）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line), None
                except Exception as e:
                    yield None, "%s:%d %s" % (os.path.basename(path), ln, e)
    except FileNotFoundError:
        return


# --------------------------------------------------------------------------
# 主扫描
# --------------------------------------------------------------------------
def scan(date=None):
    """扫全市场日线 CSV，返回当日 r31 命中 list[dict]（按当日涨幅降序）。

    缺档（该日无 bar）/ 数据不足 / 解析失败一律跳过，不抛异常。
    date 缺省 = 今天（ISO）；CLI 一般显式传交易日。
    """
    date = date or datetime.date.today().isoformat()
    names = _load_name_map()
    out = []

    for fp in sorted(glob.glob(os.path.join(DAILY, "*.csv"))):
        code = os.path.splitext(os.path.basename(fp))[0]
        if len(code) != 6 or not code.isdigit():
            continue
        try:
            df = pd.read_csv(fp, dtype={"日期": str})
        except Exception:
            continue                                   # 缺档/损坏 → 跳过，不报错
        cols = set(df.columns)
        if not {"日期", "收盘"}.issubset(cols) or len(df) < 12:
            continue
        dcol = df["日期"].astype(str).str.slice(0, 10).to_numpy()
        pos = np.where(dcol == date)[0]
        if pos.size == 0:
            continue                                   # 该日无 bar（停牌/未更新）→ 跳过
        i = int(pos[-1])

        try:
            close = df["收盘"].to_numpy(float)
        except Exception:
            continue
        prev = np.concatenate([[np.nan], close[:-1]])
        with np.errstate(invalid="ignore", divide="ignore"):
            ret = close / prev - 1.0
        ret[0] = np.nan
        if not np.isfinite(ret[i]):
            continue

        sig, sd10 = r31_mask(ret)
        lim = limit_of(code)
        zt_thr = lim - LIMIT_EPS
        # BUY 前提：T 日未涨停
        if not (sig[i] and np.isfinite(ret[i]) and ret[i] < zt_thr):
            continue

        r5, kind = trail5_exit(close, i, lim)
        out.append({
            "sec_code": code,
            "sec_name": names.get(code, ""),
            "date": date,
            "chg": round(float(ret[i]) * 100.0, 2),     # 当日涨幅（百分数）
            "sd10": round(float(sd10[i]), 6),
            "ret": round(float(ret[i]), 6),
            "close": round(float(close[i]), 3),
            "limit_pct": lim,
            "signal": "r31",
            "trail5_ret": (None if r5 is None else round(float(r5), 6)),
            "trail5_exit": kind,
            # T+1..T+5 逐日收盘路径（不可回溯数据资产；样本不足时长度<5 即 pending）
            "path": fwd_path(close, i, lim),
        })

    out.sort(key=lambda x: x["ret"], reverse=True)
    return out


def main():
    ap = argparse.ArgumentParser(description="r31_breakout 观察位 · 当日全市场扫描")
    ap.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（缺省=今天）")
    ap.add_argument("--record", action="store_true",
                    help="把命中写入只追加观察账本 backtest_cache/r31_obs.jsonl（幂等）")
    a = ap.parse_args()
    date = a.date or datetime.date.today().isoformat()

    rows = scan(date)
    if a.record:
        n_new, n_up = record(rows)
        print("[r31_obs] 新增 %d 条 / 路径升级 %d 条 → %s" % (n_new, n_up, OBS))
    print("=" * 78)
    print("[r31_watch] date=%s  命中 %d 只（WATCH 观察位·不进 buy_score）" % (date, len(rows)))
    print("=" * 78)
    if not rows:
        print("  当日无 r31_breakout 命中（横盘 sd10<1% 且当日涨幅>3% 且未涨停）。")
        return 0
    print("%-8s %-10s %8s %10s %7s %6s %-12s" %
          ("sec_code", "sec_name", "chg%", "sd10", "close", "lim", "trail5"))
    for r in rows:
        t5 = "pending" if r["trail5_ret"] is None else \
             ("%+.2f%%(%s)" % (r["trail5_ret"] * 100.0, r["trail5_exit"]))
        print("%-8s %-10s %8.2f %10.6f %7.2f %6.2f %-12s" %
              (r["sec_code"], r["sec_name"] or "-", r["chg"], r["sd10"],
               r["close"], r["limit_pct"], t5))
    print("-" * 78)
    print("合计 %d 只" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
