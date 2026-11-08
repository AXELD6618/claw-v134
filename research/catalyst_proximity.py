# -*- coding: utf-8 -*-
"""
research/catalyst_proximity.py — 消息催化 proximity 特征（§12 build #3，WATCH-only）
====================================================================================
目的：闭合 11/6 诊断识别出的 capability gap——系统无公告/催化检测器，故无法覆盖
      "事件催化埋伏型" 机会（富奥000030 / 富维600742 一汽入主公告）。

数据来源与架构（关键约束）：
  - 实时公告来自 TDX `wenda_notice_query`（MCP，需登录）。直连 HTTP 返回 401 need login，
    故**真实采集必须由持 TDX MCP 的 agent/automation 完成**，写入
    `backtest_cache/catalyst_notices.jsonl`；本模块读取并前向评估。
  - 这与 forward_oos 的 LHB_pos 同一范式：真实特征须 agent 采集 + 前向 OOS，不做历史回测
    （§11.5：圣杯级 edge 不在历史日线，只能实时采集+前向实测）。

纪律（不可逾越）：
  - 不降任何闸门；只作 WATCH / 解释层，绝不进 buy_score / core_factor_model。
  - 评估口径 = forward_oos.eval_records → adjudicate_grail（lift>=1.5 ∧ 信号日超额>0 ∧
    t市>=2.0 ∧ t空>=2.0 ∧ PurgedCV>=0.60 ∧ n>=200）。

结构性诚实边界（11/7 校正）：
  - 富奥/富维公告的**交易所披露时间戳 = 2026-11-07 00:00:00**（董事会签署 11/6，但披露 11/7，
    恰为周六，首个交易日 11/9）。故 11/6 14:50 系统"未选"是正确行为：公告当时未公开；
    且即便公开，圣杯买"非涨停"票，无法进入披露日即一字板（缺口至涨停）的票。
  - "提前介入"型（富维 10/29 异常放量）属盘口微观结构提前泄露，由 §12 #1 MICRO_accum 覆盖，
    不在此模块重复建设——避免双重构造。
  - 本模块测量的是"公告披露后、首個可买入日(T, 非涨停)→ T+1 是否续涨/涨停"的**滞后反应 drift**，
    而非"公告前埋伏"。这是与圣杯策略结构兼容的唯一可测口径。

用法：
  python -m research.catalyst_proximity --self-test                 # 单元自检
  python -m research.catalyst_proximity --simulate --wstart W --wend W   # 用已采集公告做前向样本
  python -m research.catalyst_proximity --eval <jsonl> --base <rate>      # 评估前向累积
  （agent 采集：调用 collect_to_jsonl([(symbol, api_data), ...], out)）
"""
from __future__ import annotations
import json, math, time, argparse, re
from pathlib import Path

import numpy as np
import pandas as pd
import oospf_validator as V
from research.forward_oos import make_record, append_records, eval_records

LIMIT = V.LIMIT
MIN_LEN = V.MIN_LEN
DAILY = V.DAILY
NOTICES_JSONL = V.CACHE / "catalyst_notices.jsonl"


# ── 催化分类（优先级从高到低）──
# 规律：routine 报告（季报/半年报/年报/摘要/制度/董事会决议）一律 NON-catalyst。
_ROUTINE_RE = re.compile(r"(季度报告|半年度报告|年度报告|报告摘要|信息披露管理制度|董事会决议公告|监事"
                        r"会|内部控制|社会责任|可持续发展|问询函|澄清公告|致股东信)")
# 注意：_EARNINGS_RE 保持严格（仅 预告/快报/扭亏/大幅预增预减），否则会把 routine 季报误判为催化事件
# （self-test 要求 "2026年第三季度报告" + "净利润同比-30%" 仍判 NON-catalyst）。
_EARNINGS_RE = re.compile(r"(业绩预告|业绩快报|扭亏|大幅(预增|预减|变动)|利润分配)")
# 11/8 校正：原表漏掉 plain "收购/并购/重组/投建/扩产/股权转让"——这些恰是"信息提前泄露"最高频的埋伏型事件，
# 必须纳入，否则 cross_validate 的 material 事件集过小、能力无法被证伪。
_CAT_KEYWORDS = [
    ("CONTROL_CHANGE", ["控股股东", "实际控制人", "控制权变更", "控制权", "股权无偿划转", "间接收购",
                        "要约收购", "表决权", "实控人", "权益变动", "收购报告书", "股权转让"]),
    ("RESTRUCTURE",    ["重大资产重组", "重大资产出售", "吸收合并", "分拆上市", "借壳", "重组", "资产置换"]),
    ("REFINANCE",      ["非公开发行", "定向增发", "公开增发", "配股", "可转债", "向特定对象发行", "定增", "募资"]),
    ("MAJOR_CONTRACT", ["重大合同", "重大中标", "中标(金额|项目)", "框架协议(重大|战略)", "重大订单",
                        "签订(重大|战略)协议"]),
    ("MAJOR_ASSET",    ["资产收购", "资产出售(重大)", "股权转让(重大)", "增资(重大|控股)", "收购", "并购",
                        "资产注入", "投建", "扩产"]),
    ("HALT_RESUME",    ["停牌", "复牌", "停牌核查", "暂停上市", "终止上市"]),
    ("EARNINGS",       ["业绩预告", "业绩快报", "扭亏", "大幅(预增|预减|变动)"]),
]


def classify_catalyst(title, summary=""):
    """返回 (category, matched_keyword) 或 (None, None)。纯函数，可单测。"""
    text = f"{title or ''} {summary or ''}"
    if _ROUTINE_RE.search(text):
        # 但"业绩预告/快报/扭亏/大幅变动"即使在报告语境下也算催化
        if _EARNINGS_RE.search(text):
            return ("EARNINGS", "earnings_surprise")
        return (None, None)
    for cat, kws in _CAT_KEYWORDS:
        for kw in kws:
            if kw in text:
                return (cat, kw)
    return (None, None)


def disclosure_date_of(row):
    """从 [title, time, link, source, summary] 解析披露日 → int(YYYYMMDD)。"""
    t = row[1] if len(row) > 1 else ""
    m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", t or "")
    if not m:
        return None
    return int(f"{m.group(1)}{m.group(2)}{m.group(3)}")


def parse_notice_rows(api_data):
    """api_data = 接口返回的 data 字段（list，首行为表头）。返回事件 dict 列表。"""
    if not api_data or not isinstance(api_data, list):
        return []
    rows = [r for r in api_data[1:] if isinstance(r, list) and len(r) >= 2]
    out = []
    for r in rows:
        title = r[0] if len(r) > 0 else ""
        time_ = r[1] if len(r) > 1 else ""
        link = r[2] if len(r) > 2 else ""
        source = r[3] if len(r) > 3 else ""
        summary = r[4] if len(r) > 4 else ""
        cat, kw = classify_catalyst(title, summary)
        if cat is None:
            continue
        d = disclosure_date_of(r)
        if d is None:
            continue
        out.append({"date": d, "category": cat, "keyword": kw, "title": title,
                    "time": time_, "link": link, "source": source})
    return out


def collect_to_jsonl(items, out_jsonl=NOTICES_JSONL):
    """agent 侧：items=[(symbol, api_data), ...] → 写入 catalyst_notices.jsonl。
    每条：{symbol, date, category, keyword, title, link, source}。幂等按 (symbol,date,title) 去重。"""
    p = Path(out_jsonl)
    existing = set()
    if p.exists():
        for line in p.open(encoding="utf-8"):
            try:
                o = json.loads(line)
                existing.add((o.get("symbol"), o.get("date"), o.get("title")))
            except Exception:
                pass
    n = 0
    with p.open("a", encoding="utf-8") as f:
        for symbol, api_data in items:
            for ev in parse_notice_rows(api_data):
                key = (str(symbol), ev["date"], ev["title"])
                if key in existing:
                    continue
                existing.add(key)
                rec = {"symbol": str(symbol), "date": ev["date"], "category": ev["category"],
                       "keyword": ev["keyword"], "title": ev["title"], "link": ev["link"],
                       "source": ev["source"]}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    print(f"[collect] 新增催化事件 {n} 条 → {p}")
    return n


def _trading_dates_of(code):
    """读 daily csv，返回 (dates int[], ret float[], close float[]) 且 len>=MIN_LEN。"""
    f = DAILY / f"{code}.csv"
    if not f.exists():
        return None
    try:
        df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
    except Exception:
        return None
    if len(df) < MIN_LEN:
        return None
    close = df["收盘"].to_numpy(float)
    n = len(close)
    dates = df["日期"].astype(str).str.replace("-", "").astype(int).to_numpy()
    ret = np.full(n, np.nan)
    ret[1:] = close[1:] / close[:-1] - 1.0
    return dates, ret, close


def simulate_catalyst_window(wstart, wend, notices_jsonl=NOTICES_JSONL, out_jsonl=None):
    """用已采集公告（agent 通过 MCP 拉取，可含历史窗口）生成前向样本：
    对每条催化事件(symbol, D, cat)，找 D 之后首个"可买入(非涨停)交易日 T"，
    记录 T→T+1 收益/涨停/同日市场均值，写 forward jsonl（feature=CATALYST_<cat>）。
    返回 base_rate（窗口内可买宇宙次日涨停率）。
    """
    out_jsonl = Path(out_jsonl) if out_jsonl else (V.CACHE / "forward_oos_catalyst.jsonl")
    notices = [json.loads(l) for l in open(notices_jsonl, encoding="utf-8")]
    notices = [x for x in notices if wstart <= int(x["date"]) <= wend]
    # 预建同日市场均值（窗口内）
    mkt_sum, mkt_cnt = {}, {}
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
        for i in range(1, n):
            di = dates[i]
            if wstart <= di <= wend and not np.isnan(ret[i]):
                mkt_sum[di] = mkt_sum.get(di, 0.0) + ret[i]
                mkt_cnt[di] = mkt_cnt.get(di, 0) + 1
        if (fi + 1) % 1000 == 0:
            print(f"  [sim load] {fi+1}/{len(files)}")
    base_tot = base_hit = 0
    recs = []
    for x in notices:
        sym = x["symbol"]; D = int(x["date"]); cat = x["category"]
        td = _trading_dates_of(sym)
        if td is None:
            continue
        dates, ret, close = td
        # 找 D 之后首个可买入交易日 T（ret[T] 在 (-LIMIT, LIMIT)）
        T_idx = None
        for i in range(len(dates)):
            if dates[i] <= D:
                continue
            if (-LIMIT < ret[i] < LIMIT) and (i + 1 < len(dates)):
                T_idx = i
                break
        if T_idx is None:
            continue
        di = dates[T_idx]
        f1 = ret[T_idx + 1]
        if np.isnan(f1):
            continue
        zt_next = f1 >= LIMIT
        mc = mkt_cnt.get(di, 0)
        mkt = (mkt_sum.get(di, 0.0) / mc) if mc > 0 else float("nan")
        base_tot += 1
        if zt_next:
            base_hit += 1
        recs.append(make_record(di, sym, f"CATALYST_{cat}", True, f1, zt_next, mkt))
    base_rate = (base_hit / base_tot) if base_tot else float("nan")
    append_records(str(out_jsonl), recs)
    print(f"[simulate] 事件窗口 {wstart}~{wend}  命中可测事件 {len(recs)}  "
          f"基准可买次日涨停率={base_rate*100:.3f}%  → {out_jsonl}")
    return base_rate, len(recs)


def eval_catalyst(jsonl, base_rate):
    recs = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
    feats = sorted(set(r["feature"] for r in recs if r.get("signal")))
    print(f"[eval] {jsonl}  特征={feats}  基准率={base_rate*100 if base_rate==base_rate else 'nan'}%")
    verdicts = {}
    for ft in feats:
        r = eval_records(recs, ft, base_rate)
        verdicts[ft] = r["verdict"]
        print(f"  {ft}: n={r['n']} lift={r['lift']:.3f} 次日涨停={r['sig_zt']*100:.3f}% "
              f"超额vs市={r['excess_vs_mkt']:.2f}bp t市={r['t_vs_mkt']} t空={r['t_vs_cash']} "
              f"PCV={r['purged_cv']} -> {r['verdict']}")
    return verdicts


def _self_test():
    ok = True
    def chk(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            print(f"  [FAIL] {msg}")
    # 1) 真实一汽公告 → CONTROL_CHANGE
    c, kw = classify_catalyst("富奥股份：关于国有股权无偿划转暨控股股东及实际控制人拟变更的提示性公告", "")
    chk(c == "CONTROL_CHANGE", f"一汽划转公告应判 CONTROL_CHANGE, got {c}")
    c2, _ = classify_catalyst("关于国有股权无偿划转暨控股股东及实际控制人拟变更的提示性公告", "")
    chk(c2 == "CONTROL_CHANGE", f"富维公告应判 CONTROL_CHANGE, got {c2}")
    # 2) routine 报告 → None
    chk(classify_catalyst("2026年第三季度报告", "净利润同比-30%") == (None, None),
        "季报应 NON-catalyst")
    chk(classify_catalyst("信息披露管理制度（2026年8月修订）", "") == (None, None),
        "制度文件应 NON-catalyst")
    chk(classify_catalyst("第十一届董事会第二十八次会议决议公告", "") == (None, None),
        "董事会决议应 NON-catalyst")
    # 3) 业绩预告（虽在报告语境） → EARNINGS
    c3, _ = classify_catalyst("2026年度业绩预告", "预计扭亏为盈")
    chk(c3 == "EARNINGS", f"业绩预告应 EARNINGS, got {c3}")
    # 4) 重组/定增/重大合同
    chk(classify_catalyst("关于筹划重大资产重组暨停牌的公告", "") == ("RESTRUCTURE", "重大资产重组"),
        "重组应 RESTRUCTURE")
    chk(classify_catalyst("2026年度非公开发行A股股票预案", "") == ("REFINANCE", "非公开发行"),
        "定增应 REFINANCE")
    chk(classify_catalyst("关于中标重大合同的公告", "合同金额30亿元") == ("MAJOR_CONTRACT", "重大合同"),
        "重大合同应 MAJOR_CONTRACT")
    # 5) 披露日解析（关键：11/7 而非 11/6）
    row = ["关于国有股权无偿划转暨控股股东及实际控制人拟变更的提示性公告",
           "2026-11-07 00:00:00", "http://x", "深交所", "..."]
    chk(disclosure_date_of(row) == 20261107, "披露日应解析为 20261107（交易所时间戳）")
    # 6) parse_notice_rows 从真实 api_data 结构
    api_data = [["标题", "时间", "链接", "来源", "摘要"], row,
                ["2026年第三季度报告", "2026-10-29 00:00:00", "u", "上交所", "净利润-30%"]]
    evs = parse_notice_rows(api_data)
    chk(len(evs) == 1 and evs[0]["category"] == "CONTROL_CHANGE" and evs[0]["date"] == 20261107,
        f"parse 应只保留催化事件且日期=11/7, got {evs}")
    # 7) make_record 一致性
    rec = make_record(20261109, "000030", "CATALYST_CONTROL_CHANGE", True, 0.10, True, 0.01)
    chk(rec["feature"] == "CATALYST_CONTROL_CHANGE" and rec["zt_next"] is True, "make_record 结构")
    print("  ALL PASS" if ok else "  *** SOME FAILED ***")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--simulate", action="store_true")
    ap.add_argument("--eval")
    ap.add_argument("--base", type=float)
    ap.add_argument("--wstart", type=int, default=20260101)
    ap.add_argument("--wend", type=int, default=20261231)
    ap.add_argument("--out", default=None)
    ap.add_argument("--notices", default=str(NOTICES_JSONL))
    a = ap.parse_args()
    if a.self_test:
        _self_test()
    elif a.simulate:
        t0 = time.time()
        simulate_catalyst_window(a.wstart, a.wend, a.notices, a.out)
        print(f"  [done] t={time.time()-t0:.0f}s")
    elif a.eval:
        eval_catalyst(a.eval, a.base if a.base else float("nan"))
    else:
        print("用法: --self-test | --simulate --wstart W --wend W | --eval <jsonl> --base <rate>")


if __name__ == "__main__":
    main()
