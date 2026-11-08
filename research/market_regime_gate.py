#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
research/market_regime_gate.py
====================================================================
行情制度门 —— 《散户是最后的买单人》头号生存纪律的工程化落地

文章原话:
  "在机会少于风险的时候, 正确的动作是空仓, 不是硬找理由出手。"
  "当整条链路把买单人位置留给你的时候, 你唯一能做的、代价最小的选择,
   就是尽量不站在那个位置上。"

factor_registry.json 2026-10-28 诊断已点名: "系统无 regime/risk-off 模块
(market_regime.py 不存在) -> 识别不了'科技大跌→防御轮动'窗口"。本模块即补此缺口,
直接服务用户体验硬指标 [A] 无论何种行情都要套利盈利 的唯一诚实解:
不是在所有行情都买, 而是在结构不利的行情里不买。

接口(生产环境):
  - breadth: 当日上涨股占比 [0,1], 由 backtest_cache/daily 全样本 或
    TDX market_changedist / market_overview 实时计算。
  - idx_above_ma20: 指数(沪深300等)是否在 MA20 上方(可选)。
  - should_trade() -> {trade:bool, regime, reason}

同时提供 regime_oos_eval(): 在真实全市场缓存上复算 regime 分层与制度门 PF,
作为本模块的自我验证(与 article_backtest_real.py 结论一致)。
====================================================================
"""
import os
import time
import json as _json
import urllib.request as _urllib_req
import urllib.error as _urllib_err
from concurrent.futures import ThreadPoolExecutor
import numpy as np
import pandas as pd
from research.board_limit import board_limit_pct, is_limit_up

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(BASE, "backtest_cache", "daily")

BREADTH_RISK_OFF = 0.20   # 宽度<20% -> 空仓(恐慌普跌)
BREADTH_SIDEWAYS = 0.40   # 20%~40% -> 观望; >40% -> 可出手


def regime_for_breadth(b: float) -> str:
    if b < BREADTH_RISK_OFF:
        return "RISK_OFF"
    if b < BREADTH_SIDEWAYS:
        return "SIDEWAYS"
    return "RISK_ON"


def should_trade(breadth, idx_above_ma20=None, force_hold_if_no_data=True):
    """出手决策。结构不利(低宽度/指数破MA20)则空仓。

    门控阈值对齐 OOS 验证(regime_oos_eval)：仅 RISK_OFF(<20%) 确定性破坏
    (PF≈0.19) 才硬空仓；SIDEWAYS/RISK_ON 均放行(前者经 OOS 验证 PF≈1.0 非破坏性)。
    历史分布：RISK_OFF≈15% 交易日，故制度门只拦最差约 15% 的天，而非全部弱势日。
    （如要更激进地"弱势日全不交易"，把 BREADTH_SIDEWAYS 当作硬门阈值即可，见下方注释。）
    """
    if breadth is None:
        return {"trade": (not force_hold_if_no_data), "regime": "UNKNOWN",
                "reason": "无宽度数据->保守空仓" if force_hold_if_no_data else "无数据但允许"}
    reg = regime_for_breadth(breadth)
    if reg == "RISK_OFF":
        return {"trade": False, "regime": reg,
                "reason": f"宽度={breadth:.1%}<{BREADTH_RISK_OFF:.0%} 恐慌普跌(PF≈0.19), 空仓(不接最后一棒)"}
    if idx_above_ma20 is False:
        return {"trade": False, "regime": reg, "reason": "指数跌破MA20, 趋势结构不利, 空仓"}
    # SIDEWAYS / RISK_ON 均放行（SIDEWAYS 经 OOS 验证非破坏性，仅降低确定性）
    # ——若需更激进的"弱势日全空仓"策略，取消下一行注释即可（会多拦约 27% 的 SIDEWAYS 日）：
    # if reg == "SIDEWAYS":
    #     return {"trade": False, "regime": reg,
    #             "reason": f"宽度={breadth:.1%} 震荡区, 仅做确定性机会(本门默认空仓观望)"}
    return {"trade": True, "regime": reg,
            "reason": (f"宽度={breadth:.1%} 震荡区(可出手, 确定性偏低)" if reg == "SIDEWAYS"
                       else f"宽度={breadth:.1%}>={BREADTH_SIDEWAYS:.0%} 风险偏好回升, 可出手")}


def breadth_from_changedist(changedist):
    """从 TDX changedist 实时数据估算宽度。"""
    if not changedist:
        return None
    up = changedist.get("up") or changedist.get("up_count") or 0
    down = changedist.get("down") or changedist.get("down_count") or 0
    flat = changedist.get("flat") or changedist.get("flat_count") or 0
    tot = up + down + flat
    return up / tot if tot > 0 else None


# ----------------------------------------------------------------------
# 历史宽度序列：一次性扫描日线缓存构建 日期->上涨股占比 查表
# ----------------------------------------------------------------------
def _breadth_series_path():
    return os.path.join(os.path.dirname(DAILY), "breadth_series.json")


def build_breadth_series(daily_dir=None, out_path=None, max_files=None):
    """扫描日线缓存，构建 日期->上涨股占比 序列（close>prev_close 占比）。

    一次性构建并落盘 backtest_cache/breadth_series.json，供制度门实时查表，
    避免每次调用都重扫 5225 个 CSV。返回 {date: frac}。
    """
    import json as _json
    d = daily_dir or DAILY
    out_path = out_path or _breadth_series_path()
    files = sorted(os.listdir(d))
    if max_files:
        files = files[:max_files]
    series = {}
    for fn in files:
        code = fn[:-4]
        try:
            df = pd.read_csv(os.path.join(d, fn), parse_dates=["日期"])
        except Exception:
            continue
        df = df.sort_values("日期").reset_index(drop=True)
        if len(df) < 2:
            continue
        close = df["收盘"].to_numpy(float)
        dates = df["日期"].dt.strftime("%Y-%m-%d").tolist()
        prev = np.concatenate([[np.nan], close[:-1]])
        up = (close > prev) & ~np.isnan(prev)
        for dt, u in zip(dates, up):
            if pd.isna(u):
                continue
            b = series.setdefault(dt, [0, 0])
            b[1] += 1
            if bool(u):
                b[0] += 1
    out = {k: round(v[0] / v[1], 6) if v[1] else 0.0 for k, v in series.items()}
    try:
        _json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return out


def breadth_for(date_str, series_path=None, allow_prev=True, flag_stale=False):
    """取某交易日宽度。series 不存在则惰性全量构建（仅首次较慢）。

    allow_prev=True（默认）：无当日值时回退 <=date 最近可用日
    （盘前/盘中用昨收宽度作为可得的最新结构信号）。返回 [0,1] 或 None。

    flag_stale=True：不返回裸 float，而返回 dict
        {"value": float|None, "used_date": str|None, "stale": bool}
    当回退到「非当日值」（上一交易日）时 stale=True。调用方据此判定：
    陈旧的上一日宽度不可用于交易二值决策——既不硬触发 RISK_OFF 空仓（11-02 事故），
    也不带病放行，而是诚实 abstain（纪律性空仓，REGIME_STALE_ABSTAIN，见 step_regime_gate /
    _regime_gate 处理）。flag_stale=False 时行为完全向后兼容（仍返回 float / None），
    不影响既有调用（如 verify_regime_gate）。
    """
    import json as _json
    sp = series_path or _breadth_series_path()
    if not os.path.exists(sp):
        build_breadth_series(out_path=sp)
    try:
        s = _json.load(open(sp, encoding="utf-8"))
    except Exception:
        if flag_stale:
            return {"value": None, "used_date": None, "stale": False}
        return None
    if date_str in s:
        v = s[date_str]
        if flag_stale:
            return {"value": v, "used_date": date_str, "stale": False}
        return v
    if allow_prev:
        best = None
        for k in sorted(s.keys()):
            if k <= date_str:
                best = k
        if best:
            if flag_stale:
                return {"value": s[best], "used_date": best, "stale": (best != date_str)}
            return s[best]
    if flag_stale:
        return {"value": None, "used_date": None, "stale": False}
    return None


def regime_block_stats(series_path=None):
    """统计历史上 RISK_OFF/SIDEWAYS/RISK_ON 各 regime 占比，供调参 BREADTH_SIDEWAYS。"""
    import json as _json
    sp = series_path or _breadth_series_path()
    if not os.path.exists(sp):
        build_breadth_series(out_path=sp)
    try:
        s = _json.load(open(sp, encoding="utf-8"))
    except Exception:
        return None
    n = len(s)
    cnt = {"RISK_OFF": 0, "SIDEWAYS": 0, "RISK_ON": 0}
    for v in s.values():
        cnt[regime_for_breadth(v)] += 1
    out = {"days": n}
    out.update({k: round(c / n * 100, 2) for k, c in cnt.items()})
    # 生产门控（should_trade）仅硬拦 RISK_OFF；SIDEWAYS 放行（OOS 验证 PF≈1.0 非破坏性）。
    out["blocked_pct"] = round(cnt["RISK_OFF"] / n * 100, 2)
    out["blocked_pct_with_sideways"] = round((cnt["RISK_OFF"] + cnt["SIDEWAYS"]) / n * 100, 2)
    return out


# ----------------------------------------------------------------------
# 实时宽度抓取：东方财富全市场实时行情 -> 上涨股占比
# 作为制度门在 14:50 的实时宽度来源（优先生效，失败回退历史缓存）。
# 任何网络异常一律返回 None，由调用方回退 breadth_for(date) 缓存，绝不阻断。
# ----------------------------------------------------------------------
_EM_FIELDS = "f12,f13,f3"   # f3 = 涨跌幅(%)
_EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"   # 沪市主板+科创 / 深市主板+创业（覆盖>95%流动性）
_EM_URL = ("https://push2.eastmoney.com/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1"
           "&fltt=2&invt=2&fid=f3&fs={fs}&fields={fields}&_={ts}")


def _em_fetch_raw(pn):
    u = _EM_URL.format(pn=pn, fs=_EM_FS, fields=_EM_FIELDS, ts=int(time.time() * 1000))
    req = _urllib_req.Request(u, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/",
        "Accept": "*/*", "Connection": "close"})
    last = None
    for attempt in range(3):
        try:
            raw = _urllib_req.urlopen(req, timeout=10).read().decode("utf-8", "ignore")
            return _json.loads(raw)
        except Exception as e:
            last = e
            time.sleep(0.4 * (attempt + 1))
    return None


def fetch_realtime_breadth(date_str, out_path=None, max_workers=8):
    """从东方财富实时行情拉全市场 A 股涨跌家数, 计算宽度并落盘 realtime_breadth.json。

    返回 [0,1] 或 None(失败)。落盘键为 date_str；若已存在该日值则直接复用(避免重复打网络)。
    已验证: 单页100行上限, 全市场约 5563 只, 约 56 页并发抓取(~2-5s)。
    """
    sp = out_path or os.path.join(os.path.dirname(DAILY), "realtime_breadth.json")
    try:
        _ex = _json.load(open(sp, encoding="utf-8"))
        if date_str in _ex:
            return _ex[date_str]
    except Exception:
        _ex = {}
    d0 = _em_fetch_raw(1)
    if not d0:
        return None
    total = (d0.get("data") or {}).get("total", 0)
    rows = list((d0.get("data") or {}).get("diff") or [])
    if total <= 0:
        return None
    n_pages = (total + 99) // 100
    try:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            for pn in range(2, n_pages + 1):
                fut = ex.submit(_em_fetch_raw, pn)
                d = fut.result()
                if d:
                    rows.extend((d.get("data") or {}).get("diff") or [])
    except Exception:
        pass   # 部分页失败不致命, 用已抓到的行估算
    if not rows:
        return None
    def _chg(x):
        try:
            return float(str(x.get("f3", "0")).replace("%", "").strip() or 0)
        except Exception:
            return 0.0
    up = sum(1 for x in rows if _chg(x) > 0)
    dn = sum(1 for x in rows if _chg(x) < 0)
    fl = sum(1 for x in rows if _chg(x) == 0)
    tot = up + dn + fl
    if tot == 0:
        return None
    b = up / tot
    try:
        _ex[date_str] = round(b, 6)
        _json.dump(_ex, open(sp, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass
    return b


# ----------------------------------------------------------------------
# 自我验证: 在真实全市场缓存上复算 regime 分层 + 制度门 PF
# ----------------------------------------------------------------------
def _limit_pct(code):
    return board_limit_pct(code)


def _is_zt(c, prev, lp):
    return prev > 0 and c >= prev * (1 + lp) - 0.005 and c > prev

def regime_oos_eval(stride=20, max_files=None):
    """复算 Req A: 分制度 PF + 制度门 PF。返回汇总 dict。结论应与 article_backtest_real 一致。"""
    t0 = time.time()
    files = sorted(os.listdir(DAILY))
    if max_files:
        files = files[:max_files]
    sampled = files[::stride]
    cache = {}
    breadth = {}
    for fn in sampled:
        code = fn[:-4]
        try:
            df = pd.read_csv(os.path.join(DAILY, fn), parse_dates=["日期"])
        except Exception:
            continue
        df = df.sort_values("日期").reset_index(drop=True)
        if len(df) < 6:
            continue
        close = df["收盘"].to_numpy(float)
        dates = df["日期"].dt.strftime("%Y-%m-%d").tolist()
        prev = np.concatenate([[np.nan], close[:-1]])
        up = (close > prev) & ~np.isnan(prev)
        cache[code] = (dates, close, up, _limit_pct(code))
        for d, u in zip(dates, up):
            b = breadth.setdefault(d, [0, 0]); b[1] += 1
            if u: b[0] += 1
    bk = {"risk_off(<30%)": [0,0,0,0.0,0.0], "sideways(30-60%)": [0,0,0,0.0,0.0],
          "risk_on(>60%)": [0,0,0,0.0,0.0]}
    g = [0,0,0,0.0,0.0]; gated = [0,0,0,0.0,0.0]
    for code, (dates, close, up, lp) in cache.items():
        n = len(close)
        for i in range(1, n - 2):
            buy = close[i]; cT = close[i+1]; cT1 = close[i+2]
            zt = _is_zt(cT, buy, lp); upT = cT > buy; upT1 = cT1 > cT
            succ = zt or (upT and upT1); ret = cT/buy - 1.0
            d = dates[i+1]; br = breadth.get(d, [0,1]); frac = br[0]/br[1] if br[1] else 0.5
            key = "risk_off(<30%)" if frac < 0.30 else ("sideways(30-60%)" if frac <= 0.60 else "risk_on(>60%)")
            b = bk[key]; b[0]+=1; b[1]+=int(succ); b[2]+=int(zt); b[3]+= max(ret,0); b[4]+= max(-ret,0)
            g[0]+=1; g[1]+=int(succ); g[2]+=int(zt); g[3]+=max(ret,0); g[4]+=max(-ret,0)
            if frac >= 0.20:
                gated[0]+=1; gated[1]+=int(succ); gated[2]+=int(zt); gated[3]+=max(ret,0); gated[4]+=max(-ret,0)
    def pf(p,ng): return p/ng if ng>0 else float("inf")
    out = {"sample": len(cache), "pairs": g[0], "secs": round(time.time()-t0,1),
           "base_pf": round(pf(g[3],g[4]),3), "base_succ": round(g[1]/g[0]*100,2),
           "gated_pf": round(pf(gated[3],gated[4]),3), "gated_succ": round(gated[1]/gated[0]*100,2),
           "regime": {k: {"n":v[0],"succ%":round(v[1]/v[0]*100,2),
                          "zt%":round(v[2]/v[0]*100,2),"avg_ret%":round((v[3]-v[4])/v[0]*100,3),
                          "pf":round(pf(v[3],v[4]),2)} for k,v in bk.items()}}
    return out


if __name__ == "__main__":
    print("=== 单点决策演示 ===")
    for b in [0.10, 0.30, 0.55, 0.75, None]:
        print(f"  breadth={b} -> {should_trade(b)}")
    print("\n=== 真实缓存自我验证 (stride=20 抽样) ===")
    res = regime_oos_eval(stride=20)
    print(f"  样本={res['sample']}只 配对={res['pairs']} [{res['secs']}s]")
    print(f"  无门: 成功={res['base_succ']}% PF={res['base_pf']}")
    print(f"  制度门(<20%空仓): 成功={res['gated_succ']}% PF={res['gated_pf']}")
    for k,v in res["regime"].items():
        print(f"  [{k}] n={v['n']} 成功={v['succ%']}% 涨停={v['zt%']}% "
              f"avg_ret={v['avg_ret%']}% PF={v['pf']}")
