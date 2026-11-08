#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""short_pressure.py — 卖压预警画像（解释层 / 风险层，非 alpha 因子）
=============================================================================
将用户论述的 A股做空机制，转化为**个股级可计算卖压画像**。

数据可行性（已尽调，见 factor_registry 决策）：
  - 转融通/融券余额/公募出借/量化融券做空/中证金融股东结构：本地**无**可回测
    时点数据 → 只能作解释文字，不可作为特征。
  - 限售股解禁节奏：intel_pool_lift.csv.gz（解禁日/市值/占流通比），PIT → 可计算。
  - 龙虎榜机构席位净买卖：intel_features.csv.gz（inst_net/inst_net_cap），PIT(T+1) → 可计算。
  - 月度交割周：由 000001 日历推导第3周五（expiry_calendar） → 可计算（市场级）。
  - 大宗交易(大股东/实控人变现代理)：intel_pool_events.csv.gz（src=='blk'，notional 成交金额+
    avg_px 折价比例(=block_px/close-1，已确证负=折价→急于变现)，PIT(按事件日 d<=回测日)）→ 可计算。
  - 机构派发代理：holdernum 户数环比 + cyq SCR 分散（慢变量，仅在 profile 中作旁证）。

税务驱动抛售（用户 21号公告/离岸信托论述）融合边界
-------------------------------------------------
  - 21号公告「装入信托视同转让征20%财产转让税」驱动的是**港股/美股**上市实控人
    (海底捞/泡泡玛特/BOSS直聘/趣店/网易/拼多多/小米等) 的离岸信托筹码；这些代码
    (.HK/.US) **完全不在本引擎覆盖内**（backtest_cache/daily 仅 6位 A股代码）→ 只能
    进「解释层/市场背景文字」，不可作为特征。
  - 对 A股的**可转移机制**：实控人/大股东面临税务或流动性压力时，折价大宗交易是
    其变现的主要通道 → 以「近90日大宗成交占流通市值比 + 折价比例」作代理信号。
  - 减持预披露/股权质押本地**无数据** → 仍只作解释文字，不进特征。

设计原则（铁律）
--------------
  - 本模块**只产出解释/风险信号**，绝不写 core_factor_model、绝不进 buy_score。
  - 是否可作为可部署特征，须经 short_pressure_probe.py 的「按日配对超额」OOS 门
    裁定；当前注册为 WATCH（oos_factor_id=null），core_factor_model.n_factors=0 不变。
  - profile() 纯函数、永不抛异常；缺失数据显式标 None/NA，绝不伪造。

卖压分 = 解禁供给 + 机构净卖 + 交割周 的加权组合（**大宗仅展示/解释层，不进组合分**）；
  组合分 = 0.5*解禁 + 0.4*机构 + 0.1*交割。
  （大宗变现 OOS 隔离 = −9.75bp / NW t=−0.79 → REJECT：折价大宗实为机构折价吸筹，
    非困境抛售，作卖压信号反而略反号 → 维持 DISPLAY+解释层，不进 scored 规避信号）
high_pressure = 组合分>=0.25 或 存在 fr>=5% 临近解禁 或 机构席位净卖。
大宗交易仅作卡片解释层文字（含折价=急于变现语境），不触发 high_pressure、不进 buy_score。
"""
from __future__ import annotations

import os
import sys
import datetime as _dt

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
INTEL = os.path.join(ROOT, "backtest_cache", "intel")
sys.path.insert(0, HERE)

from expiry_calendar import is_expiry_week  # noqa: E402

_FEAT = None
_LIFT = None
_EVENTS = None
_FLOAT = None

# 阈值（写在最前，便于复核）
UNLOCK_HORIZON_DAYS = 90     # 解禁日前 90 个日历日内视为供给压力窗口
UNLOCK_FR_FULL = 0.15        # 占流通比 15% → 解禁分满
INST_NETCAP_FULL = 0.01      # 机构净卖占流通市值 1% → 机构分满
COMBINED_THRESH = 0.25       # 组合分阈值，触发 high_pressure
BLOCK_HORIZON_DAYS = 90      # 近 90 个日历日的大宗交易计入变现压力窗口
BLOCK_RATIO_FULL = 0.03      # 近90日大宗成交占流通市值 3% → 大宗分满
DISCOUNT_FLAG = -0.03        # avg_px(折价比例) <=-3% 视为折价急于变现


def load_intel():
    global _FEAT, _LIFT
    if _FEAT is None:
        fp = os.path.join(INTEL, "intel_features.csv.gz")
        if os.path.exists(fp):
            _FEAT = pd.read_csv(fp, dtype={"code": str, "date": str})
            _FEAT["date"] = _FEAT["date"].astype(str).str.slice(0, 10)
        else:
            _FEAT = pd.DataFrame(columns=["code", "date", "inst_net", "inst_net_cap"])
    if _LIFT is None:
        fp = os.path.join(INTEL, "intel_pool_lift.csv.gz")
        if os.path.exists(fp):
            _LIFT = pd.read_csv(fp, dtype={"code": str, "d": str, "date": str})
            _LIFT["d"] = _LIFT["d"].astype(str).str.slice(0, 10)
        else:
            _LIFT = pd.DataFrame(columns=["code", "d", "cap", "fr"])
    return _FEAT, _LIFT


def _iso(d):
    return str(d)[:10]


def unlock_overhang(code, date):
    """未来 UNLOCK_HORIZON_DAYS 个日历日内解禁占流通比峰值。返回 (fr, d_near)。"""
    _, lift = load_intel()
    code = str(code).zfill(6)
    if lift.empty:
        return 0.0, None
    sub = lift[(lift["code"] == code) & (lift["d"] >= _iso(date))]
    if sub.empty:
        return 0.0, None
    try:
        base = _dt.date.fromisoformat(_iso(date))
        lim = (base + _dt.timedelta(days=UNLOCK_HORIZON_DAYS)).isoformat()
    except Exception:
        lim = "2999-12-31"
    sub = sub[sub["d"] <= lim]
    if sub.empty:
        return 0.0, None
    fr = float(sub["fr"].max())
    d_near = sub.sort_values("d").iloc[0]["d"]
    return fr, d_near


def inst_sell_signal(code, date):
    """最近一次龙虎榜(<=date)机构席位净买占流通市值比。返回 (inst_net_cap, inst_net)。"""
    feat, _ = load_intel()
    code = str(code).zfill(6)
    if feat.empty:
        return None, None
    sub = feat[(feat["code"] == code) & (feat["date"] <= _iso(date))]
    if sub.empty:
        return None, None
    last = sub.sort_values("date").iloc[-1]
    return float(last.get("inst_net_cap") or 0.0), float(last.get("inst_net") or 0.0)


def load_events():
    """大宗/增持事件表（intel_pool_events.csv.gz），缓存。"""
    global _EVENTS
    if _EVENTS is None:
        fp = os.path.join(INTEL, "intel_pool_events.csv.gz")
        if os.path.exists(fp):
            ev = pd.read_csv(fp, dtype={"code": str, "src": str, "d": str})
            ev["d"] = ev["d"].astype(str).str.slice(0, 10)
            ev["notional"] = pd.to_numeric(ev["notional"], errors="coerce").fillna(0.0)
            ev["avg_px"] = pd.to_numeric(ev["avg_px"], errors="coerce")
            _EVENTS = ev
        else:
            _EVENTS = pd.DataFrame(columns=["code", "src", "d", "notional", "avg_px"])
    return _EVENTS


def load_float():
    """流通股本（股）。float_shares.csv 两列：code, float_shares。"""
    global _FLOAT
    if _FLOAT is None:
        fp = os.path.join(ROOT, "backtest_cache", "float_shares.csv")
        d = {}
        if os.path.exists(fp):
            import csv as _csv
            with open(fp, encoding="utf-8") as f:
                r = _csv.reader(f)
                next(r, None)
                for row in r:
                    if len(row) >= 2:
                        try:
                            d[str(row[0]).strip().zfill(6)] = float(row[1])
                        except Exception:
                            pass
        _FLOAT = d
    return _FLOAT


def _last_close(code):
    """日线末行收盘（前复权）。profile 用于当前推送(date≈今日)，末收≈当日。"""
    p = os.path.join(ROOT, "backtest_cache", "daily", "%s.csv" % str(code).zfill(6))
    if not os.path.exists(p):
        return None
    last = None
    try:
        with open(p, encoding="utf-8") as f:
            for r in f:
                r = r.strip()
                if not r:
                    continue
                parts = r.split(",")
                if len(parts) >= 3:
                    try:
                        last = float(parts[2])
                    except Exception:
                        pass
    except Exception:
        return None
    return last


def _float_cap(code, date=None):
    """流通市值 = 流通股本 × 收盘。缺失任一项返 None（绝不强算）。

    date 仅用于审计标注；流通市值为当前快照近似（_last_close 取最新收盘），
    不按历史日重算——profile 只用于当前推送(date≈今日)，近似足够。
    """
    fs = load_float().get(str(code).zfill(6))
    if not fs:
        return None
    c = _last_close(code)
    if c is None:
        return None
    return fs * c


def block_trade_pressure(code, date):
    """近 BLOCK_HORIZON_DAYS 日内大宗交易(大股东/实控人变现代理)占流通市值比。

    返回 (ratio, n_events, discount_mean, discount_flag)。
      ratio          : 近90日大宗成交金额 / 流通市值（None=流通市值缺失，不伪造）
      n_events       : 窗口内大宗笔数
      discount_mean  : 窗口内 avg_px 均值（折价比例，负=折价）；None=无数据
      discount_flag  : discount_mean <= DISCOUNT_FLAG（折价急于变现）
    绝不逆向推断：notional 为成交金额(元)，用于量级；avg_px 为折价比例，用于定性。
    """
    code = str(code).zfill(6)
    ratio = None
    n_events = 0
    discount_mean = None
    discount_flag = False
    try:
        ev = load_events()
        blk = ev[(ev["src"] == "blk") & (ev["code"] == code)]
        if blk.empty:
            return 0.0, 0, None, False
        try:
            base = _dt.date.fromisoformat(_iso(date))
            lo = (base - _dt.timedelta(days=BLOCK_HORIZON_DAYS)).isoformat()
        except Exception:
            lo = "1970-01-01"
        win = blk[(blk["d"] >= lo) & (blk["d"] <= _iso(date))]
        if win.empty:
            return 0.0, 0, None, False
        n_events = int(len(win))
        fc = _float_cap(code, _iso(date))
        if fc is None or fc <= 0:
            # 流通市值缺失：ratio 不可得，但笔数/折价仍可作解释文字
            disc = win["avg_px"].dropna()
            discount_mean = float(disc.mean()) if len(disc) else None
            discount_flag = bool(discount_mean is not None and discount_mean <= DISCOUNT_FLAG)
            return None, n_events, discount_mean, discount_flag
        total = float(win["notional"].sum())
        ratio = min(total / fc, 1.0)
        disc = win["avg_px"].dropna()
        discount_mean = float(disc.mean()) if len(disc) else None
        discount_flag = bool(discount_mean is not None and discount_mean <= DISCOUNT_FLAG)
    except Exception:
        pass
    return ratio, n_events, discount_mean, discount_flag


def short_pressure_profile(code, date):
    """个股卖压画像（解释层）。永不抛异常。"""
    code = str(code).zfill(6)
    try:
        fr, d_near = unlock_overhang(code, date)
        inc_cap, inc_net = inst_sell_signal(code, date)
        ratio, n_blk, discount_mean, discount_flag = block_trade_pressure(code, date)
        exp = is_expiry_week(_iso(date))

        unlock_score = min(1.0, fr / UNLOCK_FR_FULL) if fr > 0 else 0.0
        inst_score = 0.0
        inst_selling = bool(inc_cap is not None and inc_cap < 0)
        if inst_selling:
            inst_score = min(1.0, abs(inc_cap) / INST_NETCAP_FULL)
        block_score = min(1.0, ratio / BLOCK_RATIO_FULL) if (ratio is not None and ratio > 0) else 0.0
        exp_score = 0.5 if exp else 0.0

        peak = max(unlock_score, inst_score, block_score, exp_score)
        # 大宗仅展示/解释层（OOS REJECT，不进组合分），组合分只用已验证的 解禁+机构+交割
        combined = 0.5 * unlock_score + 0.4 * inst_score + 0.1 * exp_score

        flags = []
        if unlock_score > 0:
            flags.append("临近解禁(占流通%.1f%%)" % (fr * 100))
        if inst_selling:
            flags.append("机构席位净卖")
        if block_score > 0:
            # 解释层文字（标注未验证），不触发 high_pressure、不进组合分
            if ratio is not None:
                flags.append("近期大宗交易活跃(占流通%.2f%%·解释层未验证)" % (ratio * 100))
            else:
                flags.append("近期大宗交易活跃(流通市值缺失·解释层)")
            if discount_flag and discount_mean is not None:
                flags.append("大宗折价%.1f%%成交(急于变现语境)" % (discount_mean * 100))
        if exp:
            flags.append("月度交割周")

        return {
            "code": code,
            "date": _iso(date),
            "score": round(float(combined), 4),
            "peak_score": round(float(peak), 4),
            "high_pressure": bool(
                combined >= COMBINED_THRESH
                or (unlock_score > 0 and fr >= 0.05)
                or inst_selling
            ),
            "parts": {
                "unlock_fr": round(fr, 4),
                "unlock_near": d_near,
                "inst_net_cap": round(inc_cap, 6) if inc_cap is not None else None,
                "inst_net": round(inc_net, 2) if inc_net is not None else None,
                "block_ratio": round(ratio, 4) if ratio is not None else None,
                "block_n": n_blk,
                "block_discount_mean": round(discount_mean, 4) if discount_mean is not None else None,
                "block_discount_flag": bool(discount_flag),
                "block_oos": "REJECT(-9.75bp,NW t=-0.79)",
                "expiry_week": bool(exp),
            },
            "flags": flags,
        }
    except Exception:
        return {
            "code": code, "date": _iso(date), "score": 0.0, "peak_score": 0.0,
            "high_pressure": False, "parts": {}, "flags": [],
        }


def short_pressure_text(profile: dict) -> str:
    """生成给推送卡片用的卖压预警文字（解释层）。"""
    if not profile or not profile.get("flags"):
        return "NA(无卖压信号)"
    p = profile
    s = "卖压%.2f｜%s" % (p.get("score", 0.0), "；".join(p.get("flags", [])))
    # 税务驱动变现解释层：折价大宗 = 大股东/实控人急于变现
    # （21号公告视同转让征20%财产转让税；港股/美股离岸信托筹码不在本引擎覆盖内→仅背景）
    if p.get("parts", {}).get("block_discount_flag"):
        s += " ｜监管税务驱动变现压力代理(21号公告视同转让征20%财产转让税；折价大宗=急于变现)"
    return s


if __name__ == "__main__":
    # 自检：对几只曾有大额解禁/机构净卖/大宗的票做一次画像
    for code, date in [("000002", "2026-09-01"), ("600519", "2026-09-21"),
                       ("300750", "2026-10-16"), ("600000", "2026-10-20")]:
        prof = short_pressure_profile(code, date)
        print(code, short_pressure_text(prof), "| parts=", prof.get("parts"))
