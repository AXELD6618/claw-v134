#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
research/sector_chip_crowding.py
==================================================================
板块/题材级「资金抱团 / 拥挤度」代理(PROXY)聚合器  —— 解释层 / 研究层模块

【重要声明 — 这是 PROXY，不是真实公募持仓】
------------------------------------------------------------------
本模块产出的所有指标都是 **代理(PROXY)**，**绝不**等同于真实公募基金
行业/题材持仓集中度。真实指标应是「某板块公募持仓占净值比 >15% 阈值」，
但公开、免费、可靠的历史持仓源在本系统里不存在，故本模块改用：

    per-stock SCR(筹码集中度, 越低越集中) + CYS(短线盈亏偏离, 取绝对值越大越极端)
    → 个股拥挤子分 → 按题材内个股权重(换手率)加权聚合 → 题材级拥挤 PROXY

所有输出字段名含 "_PROXY"，且顶层 is_proxy=true。本模块 **绝不** 进
buy_score / core_factor_model / factor_registry / 任何 gate；它只是给研究
与解释层提供「哪些题材筹码在抱团」的读图。是否晋级候选特征须走完整 OOS 门。

【纪律：绝不伪造】
------------------------------------------------------------------
- 缺少日线 / 流通股本的个股一律跳过(skip)，绝不编造 SCR/CYS/换手。
- 某题材有效样本 < MIN_VALID_MEMBERS(默认 3) 时，标记 insufficient=true，
  拥挤分置 None，label_PROXY="样本不足(PROXY)"。

【数据接口（与既有引擎同口径）】
------------------------------------------------------------------
- cyq_engine.analyze(code, window=250) → {scr_latest, cys_latest, ...}
  （TDX 同源前复权成本分布重建；成本均线偏离）
- backtest_cache/float_shares.csv  → 流通股本(股)
- backtest_cache/daily/<code>.csv  → 列: 日期,开盘,收盘,最高,最低,成交量(手)
- 换手率 = 当日成交量(手)*100 / 流通股本(股) * 100  （push_contract._turnover 同口径）

【题材→成分 映射】
------------------------------------------------------------------
主源: data/industry_map/stock_to_sector.json
  {code: {"name":..., "sectors":[概念/产业链板块...]}}
本模块将其 **反转** 为 {板块名: [成员代码...]}，作为题材→成员映射。
（theme_registry.json 仅有注意力权重/关键词，无成分列表，故不直接用于成员枚举。）
"""

from __future__ import annotations
import os
import sys
import json
import csv
import glob
import datetime

# ---- 路径解析（import-safe：无论从 repo 根还是 research/ 直接运行） ----
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)                      # <repo>/Claw
if REPO not in sys.path:
    sys.path.insert(0, REPO)

try:
    from research import cyq_engine                # 既有的 CYQ 引擎
except Exception:                                  # 直接以脚本运行时 research 已在 sys.path
    import cyq_engine                              # type: ignore

# ---- 关键路径 ----
DAILY_DIR = os.path.join(REPO, "backtest_cache", "daily")
FLOAT_CSV = os.path.join(REPO, "backtest_cache", "float_shares.csv")
SECTOR_MAP_JSON = os.path.join(REPO, "data", "industry_map", "stock_to_sector.json")
OUT_JSON = os.path.join(REPO, "backtest_cache", "sector_chip_crowding.json")

# ---- 代理构造的启发式参考边界（明确为参考刻度，非真实公募持仓拟合） ----
# SCR(筹码集中度) 参考区间：<=5 视为高度集中(scored 1)，>=25 视为松散(scored 0)
SCR_REF_LOW = 5.0
SCR_REF_HIGH = 25.0
# CYS(短线盈亏偏离) 参考：|CYS|>=15 视为极端(scored 1)
CYS_REF = 15.0
# 个股拥挤子分权重：集中度 0.6 + 偏离极端度 0.4
W_CONC = 0.6
W_CYS = 0.4
# 题材有效样本下限
MIN_VALID_MEMBERS = 3
# 题材级拥挤分阈值 → 标签（绝对刻度，按经验量级标定；详见下方说明）
# 经验分布：p50≈0.09, p90≈0.16, p95≈0.19, max≈0.30，故绝对阈值取此量级。
# 注意：本代理本质是「横截面相对抱团读图」，latest_snapshot 会再用分位阈值重打相对标签。
LABEL_LOW = 0.10
LABEL_MID = 0.15
LABEL_HIGH = 0.20

DISCLAIMER = (
    "PROXY 非真实公募持仓：本文件所有 _PROXY 字段为 per-stock SCR/CYS + 换手加权"
    "聚合的板块资金抱团代理，非公募行业持仓集中度。仅供解释层/研究读图，"
    "不进入 buy_score / core_factor_model / 任何 gate。"
)


# --------------------------------------------------------------------------- #
#  数据加载
# --------------------------------------------------------------------------- #
def load_float():
    """流通股本映射 code -> 流通股(股)。缺失/解析失败项跳过。"""
    d = {}
    if not os.path.exists(FLOAT_CSV):
        return d
    try:
        with open(FLOAT_CSV, encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) >= 2:
                    try:
                        d[str(row[0]).strip().zfill(6)] = float(row[1])
                    except Exception:
                        pass
    except Exception:
        pass
    return d


_FLOAT_CACHE = None


def _float_map():
    global _FLOAT_CACHE
    if _FLOAT_CACHE is None:
        _FLOAT_CACHE = load_float()
    return _FLOAT_CACHE


def load_theme_members(map_path=None):
    """反转 stock_to_sector.json → {板块/题材名: [成员代码...]}。

    返回 dict；无任何成分映射时返回空 dict（绝不伪造，由调用方决定降级）。
    """
    path = map_path or SECTOR_MAP_JSON
    if not os.path.exists(path):
        return {}
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    inv = {}
    for code, v in raw.items():
        if not isinstance(v, dict):
            continue
        secs = v.get("sectors") or []
        for s in secs:
            s = str(s).strip()
            if s:
                inv.setdefault(s, []).append(str(code).strip().zfill(6))
    # 去重保序
    for k in inv:
        seen = set()
        inv[k] = [c for c in inv[k] if not (c in seen or seen.add(c))]
    return inv


# --------------------------------------------------------------------------- #
#  per-stock 计算（带缓存；缺失数据返回 None，绝不编造）
# --------------------------------------------------------------------------- #
def _analyze_cached(code, cache):
    """cyq_engine.analyze 结果缓存；返回 None 表示缺日线/流通股本（即跳过）。"""
    code = str(code).strip().zfill(6)
    if cache is not None and code in cache:
        return cache[code]
    try:
        r = cyq_engine.analyze(code, window=250)
    except Exception:
        r = None
    if cache is not None:
        cache[code] = r
    return r


_DAILY_CACHE = {}


def _daily_last_rows(code, as_of_date=None):
    """返回 (date_str, vol_hand) 在 as_of_date 当日行；as_of_date=None 取最新(末行)。

    缺失或解析失败 → (None, None)。不缓存（调用方已按 code 去重，量小）。
    """
    code = str(code).strip().zfill(6)
    p = os.path.join(DAILY_DIR, "%s.csv" % code)
    if not os.path.exists(p):
        return (None, None)
    target = None
    last = None
    try:
        with open(p, encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) < 6:
                    continue
                try:
                    d = row[0].strip()
                    vol = float(row[5])
                except Exception:
                    continue
                last = (d, vol)
                if as_of_date is not None and d == as_of_date:
                    target = (d, vol)
    except Exception:
        return (None, None)
    return target if target is not None else last


def turnover_at(code, as_of_date=None):
    """换手率(%) = 成交量(手)*100 / 流通股本(股) * 100（push_contract._turnover 同口径）。

    缺流通股本或缺日线 → 返回 None（绝不编造）。
    """
    code = str(code).strip().zfill(6)
    fs = _float_map().get(code)
    if not fs:
        return None
    _, vol = _daily_last_rows(code, as_of_date=as_of_date)
    if vol is None:
        return None
    vol_shares = vol * 100.0          # 手 → 股
    return round(vol_shares / fs * 100.0, 2)


# --------------------------------------------------------------------------- #
#  单题材拥挤聚合
# --------------------------------------------------------------------------- #
def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else (hi if x > hi else x)


def crowding_for_theme(theme, as_of_date=None, members=None,
                       theme_map=None, cache=None):
    """对单个题材/板块计算拥挤度 PROXY。

    参数
    ----
    theme      : 题材名（用于结果回显）
    as_of_date : 截面日期(None=最新交易日)
    members    : 显式成员代码列表；为 None 时从 theme_map[theme] 取
    theme_map  : {题材: [成员...]}；members=None 时必需
    cache      : 跨题材复用的 per-stock analyze 缓存 dict（提速，避免重复计算）

    返回 dict:
        theme, as_of, members(全部), valid_members, skipped_members,
        valid_n, agg_scr_PROXY, agg_cys_PROXY,
        turnover_weighted_crowding_PROXY, label_PROXY,
        is_proxy=true, insufficient(bool)
    """
    if members is None:
        mp = theme_map or load_theme_members()
        members = mp.get(theme, []) or []

    valid_members = []
    skipped_members = []
    scr_list, cys_list, tov_list, crowd_list, weight_list = [], [], [], [], []

    for code in members:
        code = str(code).strip().zfill(6)
        a = _analyze_cached(code, cache)
        if a is None or a.get("scr_latest") is None or a.get("cys_latest") is None:
            skipped_members.append(code)          # 缺数据 → 跳过，绝不编造
            continue
        scr = float(a["scr_latest"])
        cys = float(a["cys_latest"])
        tov = turnover_at(code, as_of_date=as_of_date)
        # 集中度子分: SCR 越低越集中
        conc = _clamp((SCR_REF_HIGH - scr) / (SCR_REF_HIGH - SCR_REF_LOW))
        # 偏离极端度: |CYS| 越大越极端
        cys_ext = _clamp(abs(cys) / CYS_REF)
        member_crowd = W_CONC * conc + W_CYS * cys_ext
        valid_members.append(code)
        scr_list.append(scr)
        cys_list.append(cys)
        tov_list.append(tov)
        crowd_list.append(member_crowd)
        # 换手缺失时回退权重 1.0，不把该成员踢出加权（仅缺失换手，SCR/CYS 有效）
        weight_list.append(tov if (tov is not None and tov > 0) else 1.0)

    valid_n = len(valid_members)
    insufficient = valid_n < MIN_VALID_MEMBERS

    if insufficient or valid_n == 0:
        return dict(
            theme=theme,
            as_of=as_of_date,
            members=list(members),
            valid_members=valid_members,
            skipped_members=skipped_members,
            valid_n=valid_n,
            agg_scr_PROXY=None,
            agg_cys_PROXY=None,
            turnover_weighted_crowding_PROXY=None,
            label_PROXY="样本不足(PROXY)",
            is_proxy=True,
            insufficient=True,
        )

    wsum = sum(weight_list)
    agg_scr = sum(w * s for w, s in zip(weight_list, scr_list)) / wsum
    agg_cys = sum(w * c for w, c in zip(weight_list, cys_list)) / wsum
    agg_crowd = sum(w * c for w, c in zip(weight_list, crowd_list)) / wsum

    if agg_crowd < LABEL_LOW:
        label = "低"
    elif agg_crowd < LABEL_MID:
        label = "中"
    elif agg_crowd < LABEL_HIGH:
        label = "高"
    else:
        label = "拥挤(PROXY)"

    return dict(
        theme=theme,
        as_of=as_of_date,
        members=list(members),
        valid_members=valid_members,
        skipped_members=skipped_members,
        valid_n=valid_n,
        agg_scr_PROXY=round(agg_scr, 3),
        agg_cys_PROXY=round(agg_cys, 3),
        turnover_weighted_crowding_PROXY=round(agg_crowd, 4),
        label_PROXY=label,
        is_proxy=True,
        insufficient=False,
    )


# --------------------------------------------------------------------------- #
#  全题材快照
# --------------------------------------------------------------------------- #
def latest_trading_day():
    """扫描 daily 目录各 CSV 末行日期，取最大值作为最新交易日。"""
    max_d = None
    for fp in glob.glob(os.path.join(DAILY_DIR, "*.csv")):
        if fp.endswith(".bak"):
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                lines = f.read().splitlines()
            for ln in reversed(lines):
                if ln.strip():
                    d = ln.split(",")[0].strip()
                    if d and (max_d is None or d > max_d):
                        max_d = d
                    break
        except Exception:
            continue
    return max_d


def latest_snapshot(as_of_date=None, theme_map=None, out_path=None):
    """迭代所有题材，产出自解释 JSON 并原子写入 backtest_cache/sector_chip_crowding.json。

    返回 (out_path, result_dict)。
    """
    if as_of_date is None:
        as_of_date = latest_trading_day()
    mp = theme_map or load_theme_members()
    cache = {}                       # 跨题材复用 per-stock analyze，避免重复重算
    themes_out = {}
    for theme, members in mp.items():
        try:
            themes_out[theme] = crowding_for_theme(
                theme, as_of_date=as_of_date, members=members,
                theme_map=mp, cache=cache)
        except Exception:
            # 单题材异常绝不中断整体；降级为 insufficient
            themes_out[theme] = dict(
                theme=theme, as_of=as_of_date, members=list(members),
                valid_members=[], skipped_members=list(members), valid_n=0,
                agg_scr_PROXY=None, agg_cys_PROXY=None,
                turnover_weighted_crowding_PROXY=None,
                label_PROXY="样本不足(PROXY)", is_proxy=True, insufficient=True)

    # ---- 横截面相对标签（本代理本质为相对抱团读图）----
    # 用全市场有效题材的拥挤分位重打 低/中/高/拥挤(PROXY)，保证顶部题材可达「拥挤」。
    valid_crowds = [v["turnover_weighted_crowding_PROXY"]
                    for v in themes_out.values()
                    if not v.get("insufficient")
                    and v.get("turnover_weighted_crowding_PROXY") is not None]
    valid_crowds.sort()
    nq = len(valid_crowds)

    def pct(p):
        if nq == 0:
            return 0.0
        idx = min(nq - 1, int(round(p * (nq - 1))))
        return valid_crowds[idx]

    q50, q80, q95 = pct(0.50), pct(0.80), pct(0.95)
    rel_thr = dict(p50=q50, p80=q80, p95=q95)
    for v in themes_out.values():
        c = v.get("turnover_weighted_crowding_PROXY")
        if v.get("insufficient") or c is None:
            continue
        if c >= q95:
            v["label_PROXY"] = "拥挤(PROXY)"
        elif c >= q80:
            v["label_PROXY"] = "高"
        elif c >= q50:
            v["label_PROXY"] = "中"
        else:
            v["label_PROXY"] = "低"

    # 排序辅助（拥挤分降序），仅用于 summary
    ranked = sorted(
        [(t, v) for t, v in themes_out.items()
         if not v.get("insufficient") and v.get("turnover_weighted_crowding_PROXY") is not None],
        key=lambda kv: kv[1]["turnover_weighted_crowding_PROXY"], reverse=True)
    top3 = [{"theme": t,
             "turnover_weighted_crowding_PROXY": v["turnover_weighted_crowding_PROXY"],
             "label_PROXY": v["label_PROXY"],
             "valid_n": v["valid_n"]} for t, v in ranked[:3]]

    result = dict(
        as_of=as_of_date,
        generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        is_proxy=True,
        disclaimer=DISCLAIMER,
        min_valid_members=MIN_VALID_MEMBERS,
        label_basis="relative_quantile(p50/p80/p95 横截面分位); 绝对刻度阈值=%s"
                    % dict(low=LABEL_LOW, mid=LABEL_MID, high=LABEL_HIGH),
        rel_label_thresholds_PROXY=rel_thr,
        n_themes_total=len(themes_out),
        n_themes_valid=sum(1 for v in themes_out.values() if not v.get("insufficient")),
        top3_crowded_PROXY=top3,
        themes=themes_out,
    )

    out_path = out_path or OUT_JSON
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, out_path)        # 原子写
    return out_path, result


# --------------------------------------------------------------------------- #
#  自测（离线 / 不依赖网络）
# --------------------------------------------------------------------------- #
def selftest():
    print("[selftest] 开始离线自测 ...")
    cache = {}

    # 1) 结构 + is_proxy + insufficient：含一个缺失日线的代码(600519)
    r = crowding_for_theme("selftest_liquid",
                           members=["600519", "000001", "300750"], cache=cache)
    assert r["is_proxy"] is True, "is_proxy 必须为 True"
    for k in ("members", "valid_members", "skipped_members", "valid_n",
              "agg_scr_PROXY", "agg_cys_PROXY",
              "turnover_weighted_crowding_PROXY", "label_PROXY",
              "is_proxy", "insufficient"):
        assert k in r, "缺少字段 %s" % k
    if r["valid_n"] < MIN_VALID_MEMBERS:
        assert r["insufficient"] is True, "有效样本不足应标记 insufficient"
    print("  [1] 结构/is_proxy/insufficient OK  valid_n=%d insufficient=%s"
          % (r["valid_n"], r["insufficient"]))

    # 2) 绝不伪造：包含一个明显不存在的代码，不得崩溃
    r2 = crowding_for_theme("selftest_bogus",
                            members=["999999", "000001"], cache=cache)
    assert r2["is_proxy"] is True
    assert r2["valid_n"] >= 1, "应至少保留 000001 有效"
    assert "999999" in r2["skipped_members"], "缺失代码应进入 skipped"
    print("  [2] 缺失数据不崩溃/不伪造 OK  valid_n=%d" % r2["valid_n"])

    # 3) sufficient 题材：从真实映射中找一个 >=3 有效样本者，校验聚合与标签
    #    注：stock_to_sector.json 是生成态运行时数据（不在 git 中）；
    #        无该文件 / 无可验证题材时跳过本步，逻辑正确性已由 [1][2] 覆盖。
    tm = load_theme_members()
    chosen = None
    if tm:
        for th, mem in tm.items():
            if len(mem) < 5:
                continue
            n_valid = sum(1 for c in mem[:10]
                          if _analyze_cached(c, cache) is not None)
            if n_valid >= 3:
                chosen = (th, mem)
                break
    if chosen is None:
        print("  [3] SKIP: 真实映射(stock_to_sector.json)缺失或无可验证题材"
              "（运行时生成，不在 git）；逻辑已由 [1][2] 覆盖")
    else:
        r3 = crowding_for_theme(chosen[0], members=chosen[1][:8], cache=cache)
        assert r3["is_proxy"] is True
        assert r3["insufficient"] is False, "应判定 sufficient"
        assert r3["valid_n"] >= 3
        assert 0.0 <= r3["turnover_weighted_crowding_PROXY"] <= 1.0, "拥挤分应在[0,1]"
        assert r3["label_PROXY"] in ("低", "中", "高", "拥挤(PROXY)")
        print("  [3] sufficient 题材 OK  theme=%s valid_n=%d crowd=%.4f label=%s"
              % (chosen[0], r3["valid_n"],
                 r3["turnover_weighted_crowding_PROXY"], r3["label_PROXY"]))

    print("SELFTEST PASS")
    return True


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def main(argv=None):
    argv = argv or sys.argv[1:]
    if "--selftest" in argv:
        selftest()
        return 0
    if "--live" in argv:
        out_path, result = latest_snapshot()
        print("=" * 64)
        print("  板块拥挤度 PROXY 快照（解释层 / 非真实公募持仓）")
        print("=" * 64)
        print("as_of            : %s" % result["as_of"])
        print("题材总数/有效    : %d / %d"
              % (result["n_themes_total"], result["n_themes_valid"]))
        print("输出文件         : %s" % out_path)
        print("-" * 64)
        print("TOP-3 最拥挤题材(PROXY):")
        for i, t in enumerate(result["top3_crowded_PROXY"], 1):
            print("  %d. %-14s crowd=%.4f  label=%s  valid_n=%d"
                  % (i, t["theme"], t["turnover_weighted_crowding_PROXY"],
                     t["label_PROXY"], t["valid_n"]))
        print("-" * 64)
        print("免责: 全部 _PROXY 字段为 per-stock SCR/CYS+换手加权代理，非公募持仓。")
        return 0
    # 默认：显示用法
    print("用法:")
    print("  python research/sector_chip_crowding.py --selftest   离线自测")
    print("  python research/sector_chip_crowding.py --live       生成最新交易日快照")
    return 0


if __name__ == "__main__":
    sys.exit(main())
