# -*- coding: utf-8 -*-
"""
tail_pick_outcome.py — 尾盘推送标的的次日核验（独立验收环节）
=============================================================================
为什么必须存在
--------------
系统每天 14:50 向用户推送 N 只标的，但在本脚本出现之前，**没有任何代码回看
这些票次日涨没涨**。唯一的台账 data/candidates_ledger.json 只跟踪「发现层候选」，
不是「已推送标的」。也就是说：选股引擎既是生产者，又是唯一的质量判断者，
等于没有验收 —— 直接违反项目核心原则「生产者不得自证」。

本脚本只做验收，不改任何选股逻辑，不写 buy_score，不碰 holdings
------------------------------------------------------------------
  1. 读 backtest_cache/ 下所有推送产出：
       - unified_tail_pick_*.json        14:50 尾盘主推
       - selection_push_*.json           盘中/盘前监测层推送
         （形如 selection_push_intraday_2026-10-28.json /
                selection_push_preopen_2026-10-28.json）
     取其中的 push 列表（真正推出去的那批）
     —— 没有 push 字段时回退到 picks；并记录该次产出是否真的送达（push_ok）
  2. 用 backtest_cache/daily/{code}.csv 取 T+1 的 OHLC
  3. 算 open / close / high / low 相对 T 日收盘的收益，以及 T+1 是否涨停
  4. 追加落盘 data/tail_pick_outcome.jsonl
     幂等：同 (date, code) 只保留最新一次「非 pending」结果
  5. 汇总 data/tail_pick_outcome_summary.json
     —— 按 verify_tier / sub_strategy / 全量三个维度

纪律（硬约束，不可绕过）
------------------------
  - 样本 < 30 只 **不得** 输出任何 edge 结论，报告只打印「样本不足，仅累积」
  - pending（次日数据未到）不参与统计，下次运行自动补齐
  - dry / 未送达（push_ok=False）的产出单独计数，不与真实送达混算
    理由：2026-10-26 曾用 dry 演练覆盖正式产出，若混算会虚增样本

用法
----
  python research/tail_pick_outcome.py                    # 核验所有已有产出
  python research/tail_pick_outcome.py --date 2026-10-26  # 只核验某一天
  python research/tail_pick_outcome.py --selftest         # 自检（涨停价与收益算法）
"""
from __future__ import annotations

import os
import sys
import json
import glob
import argparse
import datetime
import re
from research.board_limit import board_limit_pct, is_limit_up

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "backtest_cache")
DAILY = os.path.join(CACHE, "daily")
DATA = os.path.join(HERE, "data")
OUT_JSONL = os.path.join(DATA, "tail_pick_outcome.jsonl")
OUT_SUM = os.path.join(DATA, "tail_pick_outcome_summary.json")

MIN_SAMPLE = 30   # 少于这个只数不做任何结论
MIN_DAYS = 10     # 少于这个交易日数不做任何结论（同一天标的高度相关）

# 会被纳入核验的推送产出的文件名前缀。两组前缀代表两个不同时段的推送通道，
# 缺任何一个都会导致该通道的标的永远进不了次日核验（样本不会累积）。
PUSH_PREFIXES = (
    "unified_tail_pick_",   # 14:50 尾盘主推
    "selection_push_",      # 盘中 / 盘前监测层推送
)

# 中间件 / 备份件标记：这些不是「最终推出去的那批」，只是管线中途落盘的快照。
# 2026-11-05 事故：unified_tail_pick_2026-11-05.pre_selfheal.json 是 cap 聚合剔除「之前」
# 的 picks 全量（92 只），文件名不以 .dry.json 结尾、内容 dry=True 却不被跳过，
# 且当日 delivered_14h50 回执为 True → 92 只根本没推出去的票被整批标成已送达。
# 教训：送达回执是「日期级」事实，不能反推「这批 picks 都推了」。
INTERMEDIATE_MARKERS = (".dry.", ".pre_selfheal", ".pre_", ".bak", ".tmp", ".partial", ".bak_")

# 文件名里日期的两类写法：YYYY-MM-DD 与 YYYYMMDD
_DATE_TOKEN_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{8}")


def _is_intermediate(base: str) -> bool:
    """中间件 / 备份件判定：这类文件不是「最终推出去的那批」，不得进入核验。

    注意选择排除标记而不是列举白名单：canonical 名可能有未来新增形态，
    白名单一旦漏项会静默丢样本；中间件标记一旦漏项会静默污染样本。
    两者都会错，但污染更难发现，故用「标记排除 + 自检覆盖」。
    """
    return base.endswith(".dry.json") or any(m in base for m in INTERMEDIATE_MARKERS)


def resolve_delivered(obj, src: str, receipt: bool) -> bool:
    """送达判定（纯函数，可单测）。

    事实来源：文件自身 push_ok=True 且 non-dry —— 这是「这批确实推了」的直接证据。
    日期级回执 delivered_14h50_{date}.json 只证明当天有一次推送成功，
    不能反推同一文件里的 picks 全量都推了，故仅允许升级 src=="push" 的批次。
    """
    if bool(obj.get("push_ok")) and not obj.get("dry", False):
        return True
    return bool(receipt) and src == "push"


def classify_delivery_status(obj):
    """缺陷#1 修复的核验层消费端：把「送达事实」拆成可追溯三类（纯函数，可单测）。

    优先读 selection_push 新字段 delivery_status / deliberate_hold / hold_reason；
    老记录（无新字段）回退到 resolve_delivered 近似，避免破坏既有口径。
    返回 dict: {delivery_status, deliberate_hold, hold_reason, delivered(bool), legacy(bool)}。

    delivered 仅当「push_ok 且 non-dry 且 实推 >0 只买点」——与 resolve_delivered 严格对齐。
    deliberate_hold=True 表示本次空仓是系统主动决策（制度门/防御轮动），不是发送失败/无信号，
    使核验侧能明确区分「主动零名额」与「无信号」与「发送失败」三类，日期级送达事实可追溯。
    """
    push_ok = bool(obj.get("push_ok"))
    dry = bool(obj.get("dry", False))
    push_n = len(obj.get("push") or [])
    if "delivery_status" in obj:
        return {
            "delivery_status": obj.get("delivery_status"),
            "deliberate_hold": bool(obj.get("deliberate_hold")),
            "hold_reason": obj.get("hold_reason") or "",
            "delivered": push_ok and not dry and push_n > 0,
            "legacy": False,
        }
    # 回退：老记录无新字段 → 近似（无法区分主动零名额/无信号，仅标记 legacy）
    if dry:
        return {"delivery_status": "dry", "deliberate_hold": False,
                "hold_reason": "dry_run", "delivered": False, "legacy": True}
    if not push_ok:
        return {"delivery_status": "failed", "deliberate_hold": False,
                "hold_reason": "push_send_failed", "delivered": False, "legacy": True}
    if push_n > 0:
        return {"delivery_status": "delivered", "deliberate_hold": False,
                "hold_reason": "", "delivered": True, "legacy": True}
    return {"delivery_status": "delivered_empty", "deliberate_hold": False,
            "hold_reason": "no_signal_legacy", "delivered": False, "legacy": True}


def _date_from_name(base: str):
    """从产出文件名里抽出推送日期，统一规范成 YYYY-MM-DD。

    两组前缀的日期都落在文件名的最后一段，但写法可能不同：
      unified_tail_pick_2026-10-28.json          -> 2026-10-28
      selection_push_intraday_2026-10-28.json    -> 2026-10-28
      selection_push_20261028.json（若将来出现）  -> 2026-10-28
    取最后一段日期 token（避免被名字里的其它数字干扰），解析不出来返回 None。
    """
    stem = base[:-len(".json")] if base.endswith(".json") else base
    hits = _DATE_TOKEN_RE.findall(stem)
    if not hits:
        return None
    tok = hits[-1].replace("-", "")
    try:
        return datetime.datetime.strptime(tok, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _limit_pct(code: str, name: str = "") -> float:
    return board_limit_pct(code)



def load_bars(code: str):
    """返回 [(date, open, close, high, low), ...]，按日期升序。读取失败返回 []。"""
    p = os.path.join(DAILY, "%s.csv" % code)
    if not os.path.exists(p):
        return []
    bars = []
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for ln in f:
                parts = ln.strip().split(",")
                if len(parts) < 6 or len(parts[0]) != 10 or parts[0][:2] != "20":
                    continue
                try:
                    bars.append((parts[0], float(parts[1]), float(parts[2]),
                                 float(parts[3]), float(parts[4])))
                except ValueError:
                    continue
    except Exception:
        return []
    bars.sort(key=lambda x: x[0])
    return bars


def next_bar_after(bars, date: str):
    """严格晚于 date 的第一根 bar；没有则 None（= 次日数据未到，pending）。"""
    for b in bars:
        if b[0] > date:
            return b
    return None


def forward_bars_after(bars, date: str, n: int = 3):
    """严格晚于 date 的前 n 根 bar（升序），不足 n 根时返回实际有多少根。

    核心业务目标要求「T+1 之后续涨≥2 日」，而本脚本历史上只取 T+1 一根 bar，
    核验台账里根本没有 T+2/T+3 的路径数据 —— 调权输入（T+1 单日收益）因此
    与核心目标（买入即盈利 + 后续≥2日上涨）不是同一个量。这里一次性取齐 3 根
    前向 bar，供 measure_one 落原始逐日收益（只出原料、不做成功判定；核心目标
    完整代理仍以 evolution_review.grail_success 为唯一真相源，避免口径重复实现）。
    """
    out = []
    for b in bars:
        if b[0] > date:
            out.append(b)
            if len(out) >= n:
                break
    return out


def bar_on(bars, date: str):
    for b in bars:
        if b[0] == date:
            return b
    return None


def prev_bar_before(bars, date: str):
    """严格早于 date 的最后一根 bar（= T-1 交易日收盘），用于判定 T 日是否封板。

    bars 已按日期升序（见 load_bars）。遇到第一根 >= date 即停，返回此前最后一根，
    避免把 T 日自身或 T+1 当成 T-1（off-by-one）。date 为首根时返回 None。
    """
    prev = None
    for b in bars:
        if b[0] >= date:
            break
        prev = b
    return prev


# --------------------------------------------------------------------------
# 单个产出文件 -> 记录列表
# --------------------------------------------------------------------------
def _pick_code(p):
    for k in ("code", "sec_code", "symbol"):
        v = p.get(k)
        if v:
            return str(v).zfill(6) if str(v).isdigit() else str(v)
    return None


def _pick_name(p):
    for k in ("name", "sec_name", "stock_name"):
        v = p.get(k)
        if v:
            return str(v)
    return ""


def extract_picks(obj):
    """从统一产出里取出「真正推出去的」那批。push 优先，否则 picks/results。"""
    for k in ("push", "picks", "results"):
        v = obj.get(k)
        if isinstance(v, list) and v:
            return v, k
    return [], ""


def compute_metrics(code: str, name: str, base: float, b1):
    """纯函数：给定 T 日收盘 base 与 T+1 的 bar，算收益与涨停判定。

    独立出来是为了可单测 —— 否则自检只能读真实日线，样本末端天然 pending。
    """
    lim = _limit_pct(code, name)
    open_ret = b1[1] / base - 1.0
    close_ret = b1[2] / base - 1.0
    high_ret = b1[3] / base - 1.0
    low_ret = b1[4] / base - 1.0
    # 缺陷 B 修复：涨停判定统一交给单一真相源 research/board_limit.py::is_limit_up
    # （Decimal ROUND_HALF_UP 交易所舍入口径），本文件不再手写任何等价式。
    # 修复前此处自算涨停价并手写比较：Python 内置 round 为 banker's rounding 且受
    # 二进制浮点影响，在 base*(1+lim) 恰落 x.xx5 时会把涨停价算低 1 分
    # （如 base=1.15 → 1.26 而非交易所的 1.27），从而把「未涨停但接近阈值」的票
    # 误判为涨停（假阳性）。替换前的独立等价性验证（base 1.00~200.00 步长 0.01
    # × 主板/低价股/双创/北交所 5 类）确认：现有 105 条台账 hit_zt 零漂移，
    # 出现的 1136 处差异全部是旧式假阳性被 is_limit_up 修正，无一例反向。
    hit_zt = is_limit_up(code, base, b1[2])
    # zt_price 仅为台账展示字段（历史口径，沿用修复前数值以保证既有字段零漂移），
    # 不再参与任何涨停判定。注意：它用 round 而非 Decimal 是刻意的 —— 台账里
    # 2026-10-29 / 000678 / base=18.75 一处 round 得 20.62 而 Decimal 得 20.63，
    # 若改口径会使该既有字段值漂移。判定权已完全移交 is_limit_up，此值只作可读性参考。
    zt_price = round(base * (1.0 + lim), 2) - 0.005
    return {
        "t1_date": b1[0],
        "base_close": round(base, 4),
        "t1_open": round(b1[1], 4),
        "t1_close": round(b1[2], 4),
        "t1_high": round(b1[3], 4),
        "t1_low": round(b1[4], 4),
        "open_ret": round(open_ret, 6),
        "close_ret": round(close_ret, 6),
        "high_ret": round(high_ret, 6),
        "low_ret": round(low_ret, 6),
        "limit_pct": lim,
        "zt_price": zt_price,
        "hit_zt": hit_zt,
        "win": bool(close_ret > 0),
    }


def measure_one(date: str, pick: dict, delivered: bool):
    code = _pick_code(pick)
    if not code:
        return None
    name = _pick_name(pick)
    bars = load_bars(code)
    rec = {
        "date": date,
        "code": code,
        "name": name,
        "sub": pick.get("sub") or pick.get("sub_strategy") or "?",
        "verify_tier": pick.get("verify_tier") or "?",
        "buyable": pick.get("buyable"),
        "delivered": bool(delivered),
        "deny_reason": pick.get("deny_reason") or None,
        "r1_forced": bool(pick.get("deny_reason")),
    }
    b0 = bar_on(bars, date)
    if b0 is None:
        rec.update({"status": "no_bar", "note": "本地无 %s 的日线" % date})
        return rec
    base = b0[2]  # 收盘价作为尾盘买入参考
    if base <= 0:
        rec.update({"status": "bad_base"})
        return rec
    # 缺陷 A 修复：核心业务目标是 T 日 14:50 买入「非涨停」股。若该票 T 日相对
    # T-1 收盘已封涨停，14:50 根本买不进（封板无卖单），计入统计会污染分母、
    # 扭曲 mean_close_ret。此处在纳入统计前显式判定并排除，且不静默丢样本：
    # 返回 status=excluded_t_limitup 的独立记录，由 summarize 计入
    # n_excluded_t_limitup，排除数量可审计、可追溯。
    # 判定用 T-1 收盘（严格早于 T 的最后一根 bar）与 T 日收盘，口径同
    # compute_metrics：单一真相源 is_limit_up(code, prev_close, price)。
    # 不传 is_st：measure_one 无可靠 ST 标记来源，且生产链路 ST 已剔除
    # （见 board_limit.py 说明）；ST 残留风险已在交付报告中单列。
    prev_b = prev_bar_before(bars, date)
    if prev_b is not None and is_limit_up(code, prev_b[2], base):
        rec.update({
            "status": "excluded_t_limitup",
            "base_close": round(base, 4),
            "t_prev_close": round(prev_b[2], 4),
            "t_close": round(base, 4),
            "t_prev_date": prev_b[0],
            "note": "T 日已涨停不可买（14:50 封板无成交），排除出统计",
        })
        return rec
    fwd = forward_bars_after(bars, date, 3)
    b1 = fwd[0] if fwd else None
    if b1 is None:
        rec.update({"status": "pending", "base_close": round(base, 4)})
        return rec

    lim = _limit_pct(code, name)
    rec.update(compute_metrics(code, name, base, b1))
    # 核心目标的「续涨」原料：T+2 / T+3 相对各自前一交易日的逐日收益。
    # 只落原始数值，不做成功判定、不参与任何门控 / 调权 / PROMOTE；核心目标
    # 完整代理仍以 evolution_review.grail_success 为唯一真相源，避免重复实现。
    # 前向不足 3 根时字段为 None 并标 no_forward_data，绝不臆造数据。
    if len(fwd) >= 3:
        rec["t2_close_ret"] = round(fwd[1][2] / fwd[0][2] - 1.0, 6)
        rec["t3_close_ret"] = round(fwd[2][2] / fwd[1][2] - 1.0, 6)
        rec["forward_status"] = "complete"
        rec["forward_dates"] = [fwd[1][0], fwd[2][0]]
    else:
        rec["t2_close_ret"] = None
        rec["t3_close_ret"] = None
        rec["forward_status"] = "no_forward_data"
        rec["forward_dates"] = None
    rec["status"] = "ok"
    rec["limit_pct"] = lim
    return rec


# --------------------------------------------------------------------------
# 落盘（幂等）
# --------------------------------------------------------------------------
def read_jsonl(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    return rows


def _merge_sources(keep_row, other_row):
    """保留 keep_row 的判定结论，把两边的来源产出文件并进 src_files（可追溯）。"""
    merged = dict(keep_row)
    files = set(keep_row.get("src_files") or [])
    files.update(other_row.get("src_files") or [])
    if files:
        merged["src_files"] = sorted(files)
    return merged


def revive_stale(old_rows, covered_keys):
    """台账里残留的 pending/no_bar，在源产出文件被轮转删除后永远等不到重算。

    2026-11-04 实例：002172 澳洋健康 2026-11-02 由 selection_push_intraday_2026-11-02.json
    记入推送，该文件已被轮转删除；T+1（11-03）日线明明在（收盘 6.80 / 基准 6.50 = +4.62%），
    但 run() 的 new_rows 只从「现存产出文件」生成，(2026-11-02, 002172) 不在任何 push 列表里
    → merge 只能保留旧 pending，样本永久卡死，与「幂等重算自动回填」的承诺不符。

    这里用台账自身的 (date, code) 回放 measure_one：base 仍取 T 日日线收盘（与原口径一致，
    不信任台账里可能残缺的 base_close），只有在能算出 ok 时才产出新行，否则原样不动。
    可追溯性：src_file/src_files 沿用原记录，并标 revived_from。
    """
    out = []
    for r in old_rows:
        k = (r.get("date"), r.get("code"))
        if k in covered_keys or r.get("status") not in ("pending", "no_bar"):
            continue
        pick = {"code": r.get("code"), "name": r.get("name"), "sub": r.get("sub"),
                "verify_tier": r.get("verify_tier"), "buyable": r.get("buyable")}
        nr = measure_one(r.get("date"), pick, bool(r.get("delivered")))
        if not nr or nr.get("status") != "ok":
            continue
        nr["src"] = r.get("src") or "push"
        nr["src_file"] = r.get("src_file")
        nr["src_files"] = r.get("src_files") or ([r["src_file"]] if r.get("src_file") else [])
        nr["revived_from"] = r.get("status")
        out.append(nr)
    return out


def merge_rows(old, new):
    """(date, code) 唯一。新的非 pending 结果覆盖旧的 pending；已 ok 的不被 pending 覆盖。

    同一 (date, code) 可能同时出现在多个时段的推送里（例如 2026-10-28 的 000678
    既有盘前 selection_push_preopen 又有尾盘 unified_tail_pick）。T+1 的收益是同一个
    客观事实，因此按 (date, code) 去重、只保留一条，**不重复计数** —— 否则同一只票
    会被算两次，虚增样本数并扭曲均值。可追溯性由 src_files（来源产出文件名并集）承担。
    冲突时以「已真实送达（delivered）」的那条为准，避免 dry/未送达版本稀释送达样本。
    """
    idx = {(r.get("date"), r.get("code")): i for i, r in enumerate(old)}
    out = list(old)
    for r in new:
        k = (r.get("date"), r.get("code"))
        if k in idx:
            prev = out[idx[k]]
            if prev.get("status") == "ok" and r.get("status") == "pending":
                continue
            if prev.get("delivered") and not r.get("delivered"):
                if r.get("status") == "ok" and prev.get("status") != "ok":
                    # 2026-11-03 修：已送达但仍是 pending 的记录，必须允许被「未送达版本」的
                    # ok 结果升级状态。原逻辑整条丢弃 → 一旦当日送达批次的产出文件被判
                    # 未送达（dry 标注失真）或已被轮转删除，该样本永远卡在 pending。
                    # 送达事实（delivered=True）沿用 prev，收益口径取 r，来源双向合并。
                    row = _merge_sources(r, prev)
                    row["delivered"] = True
                    row["delivered_src"] = prev.get("src_file")
                    out[idx[k]] = row
                    continue
                # 已送达的那版不被 dry/未送达版本覆盖，只追加来源文件
                out[idx[k]] = _merge_sources(prev, r)
                continue
            out[idx[k]] = _merge_sources(r, prev)
        else:
            idx[k] = len(out)
            out.append(r)
    out.sort(key=lambda x: (str(x.get("date")), str(x.get("code"))))
    return out


def rescan_excluded(rows):
    """汇总层一致性防线：复核所有 status=ok 记录的 T 日涨停状态。

    缺陷 A 的排除逻辑只在 measure_one 里，对「本轮新算 / revive」的行生效；但台账里
    status=ok 的历史行（尤其源文件已轮转删除、revive_stale 不回溯 ok 的那类）可能
    仍是「T 日已涨停、14:50 根本买不进」的污染样本，永远等不到 retrospective 排除。
    实测 2026-11-02 002172 澳洋健康 LIANBAN 即此例：台账 status=ok、源文件已删，
    缺陷 A 修复后 run(write=False) 仍把它算进 delivered_ok，导致台账独立复算 n=23
    与 run n=24 差 1。

    这里用日线 + is_limit_up 对每条 ok 记录复核一次，命中则把该行改标为
    excluded_t_limitup（保留原行全部字段、只追加 status/note/excluded_by 等可追溯
    标记，绝不删除任何行），由 summarize 计入 n_excluded_t_limitup。这样无论记录
    来自新算、revive 还是历史遗留，只要 T 日涨停就不能进统计，统计口径唯一。
    幂等：已 excluded 的行跳过；未涨停的行原样保留；重复运行不会累积。
    返回 (rows, n_rescanned)。
    """
    out, n_fixed = [], 0
    for r in rows:
        if r.get("status") != "ok":
            out.append(r)
            continue
        code, date = r.get("code"), r.get("date")
        bars = load_bars(code) if code else []
        b0 = bar_on(bars, date) if bars else None
        prev_b = prev_bar_before(bars, date) if bars else None
        if b0 and prev_b and b0[2] > 0 and is_limit_up(code, prev_b[2], b0[2]):
            rr = dict(r)
            rr["status"] = "excluded_t_limitup"
            rr["excluded_by"] = "rescan_excluded"
            rr["t_prev_close"] = round(prev_b[2], 4)
            rr["t_close"] = round(b0[2], 4)
            rr["t_prev_date"] = prev_b[0]
            rr["note"] = "汇总层复核：T 日已涨停不可买（历史 ok 行 retrospective 排除）"
            out.append(rr)
            n_fixed += 1
        else:
            out.append(r)
    return out, n_fixed


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------
def _agg(rows):
    n = len(rows)
    if n == 0:
        return {"n": 0}
    close_rets = [r["close_ret"] for r in rows]
    return {
        "n": n,
        "mean_open_ret": round(sum(r["open_ret"] for r in rows) / n, 6),
        "mean_close_ret": round(sum(close_rets) / n, 6),
        "median_close_ret": round(sorted(close_rets)[n // 2], 6),
        "mean_high_ret": round(sum(r["high_ret"] for r in rows) / n, 6),
        "mean_low_ret": round(sum(r["low_ret"] for r in rows) / n, 6),
        "win_rate": round(sum(1 for r in rows if r.get("win")) / n, 4),
        "zt_rate": round(sum(1 for r in rows if r.get("hit_zt")) / n, 4),
    }


def summarize(rows):
    ok = [r for r in rows if r.get("status") == "ok"]
    delivered_ok = [r for r in ok if r.get("delivered")]
    by_tier, by_sub, by_date = {}, {}, {}
    for r in delivered_ok:
        by_tier.setdefault(r.get("verify_tier") or "?", []).append(r)
        by_sub.setdefault(r.get("sub") or "?", []).append(r)
        by_date.setdefault(str(r.get("date")), []).append(r)
    by_r1 = {}
    for r in delivered_ok:
        by_r1.setdefault("R1" if r.get("r1_forced") else "base", []).append(r)
    n_days = len(by_date)
    summary = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_total_records": len(rows),
        "n_ok": len(ok),
        "n_pending": sum(1 for r in rows if r.get("status") == "pending"),
        # 缺陷 A 的显式排除计数：T 日已涨停、14:50 不可买、被剔除出统计的样本数。
        # 与 n_ok 分离，保证「排除」可审计、不是静默丢样本。
        "n_excluded_t_limitup": sum(1 for r in rows
                                    if r.get("status") == "excluded_t_limitup"),
        "n_delivered_ok": len(delivered_ok),
        "n_delivered_days": n_days,
        "independence_caveat": ("同一天的 N 只票高度相关（同一市场环境），"
                                "不能当作 N 次独立试验；判断 edge 至少需要 ≥10 个交易日"),
        "overall_delivered": _agg(delivered_ok),
        "by_verify_tier": {k: _agg(v) for k, v in sorted(by_tier.items())},
        "by_sub_strategy": {k: _agg(v) for k, v in sorted(by_sub.items())},
        "by_date": {k: _agg(v) for k, v in sorted(by_date.items())},
        "by_r1": {k: _agg(v) for k, v in sorted(by_r1.items())},
        "discipline": _discipline_text(len(delivered_ok), n_days),
        # 前向路径可观测性：核验台账历史上只到 T+1，无法承载核心目标的「续涨」部分。
        # 这里只报原料齐备度，不做成功判定、不参与任何门控 / 调权 / PROMOTE；
        # 核心目标完整代理仍以 evolution_review.grail_success 为唯一真相源。
        "forward_path_coverage": {
            "n_delivered_ok": len(delivered_ok),
            "n_complete": sum(1 for r in delivered_ok
                              if r.get("forward_status") == "complete"),
            "n_no_forward_data": sum(1 for r in delivered_ok
                                     if r.get("forward_status") == "no_forward_data"),
            "n_not_measured": sum(1 for r in delivered_ok
                                  if r.get("forward_status") is None),
            "note": ("只报原料齐备度，不判定续涨成功；n_not_measured 是新增字段前落盘的"
                     "历史行，下次 run 会自然补齐。不得与 grail_success 混用。"),
        },
    }
    return summary


def _discipline_text(n_picks, n_days):
    """口径必须同时看「只数」和「天数」。只看只数会把单日横截面误当成大样本。"""
    if n_picks < MIN_SAMPLE and n_days < MIN_DAYS:
        return ("样本不足（%d 只 / %d 个交易日，门槛 %d 只且 %d 天）："
                "仅累积观察，不构成任何 edge 结论" % (n_picks, n_days, MIN_SAMPLE, MIN_DAYS))
    if n_days < MIN_DAYS:
        return ("只数够但天数不够（%d 只仅来自 %d 个交易日，门槛 %d 天）："
                "同一天标的高度相关，暂不作 edge 结论" % (n_picks, n_days, MIN_DAYS))
    return ("样本达 %d 只 / %d 个交易日，可做初步统计判断（仍需继续累积至 %d 天以上再定权重）"
            % (n_picks, n_days, MIN_DAYS * 2))


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def _delivery_receipt(date: str) -> bool:
    """独立送达回执：run_unified_14h50.py 在真实推送成功后写 delivered_14h50_{date}.json。

    2026-11-03 修：wrapper 自 2026-10-31 起一律以 `unified_tail_pick.py --dry` 跑选股
    （推送责任上移到 wrapper），再把同一份 dict 落成正式 unified_tail_pick_{date}.json，
    导致正式文件里的 dry 恒为 true。若只信 `push_ok and not dry`，真实送达的整批会被
    永久判成「未送达」，可测样本永远攒不到 30 只 / 10 日。送达事实改以外置回执为准。
    """
    p = os.path.join(CACHE, "delivered_14h50_%s.json" % date)
    if not os.path.exists(p):
        return False
    try:
        o = json.load(open(p, "r", encoding="utf-8"))
    except Exception:
        return False
    return bool(o.get("delivered")) and str(o.get("channel")) in ("ILINK", "PUSHPLUS")


def run(dates=None, write=True, verbose=True):
    files = []
    for pref in PUSH_PREFIXES:
        files.extend(sorted(glob.glob(os.path.join(CACHE, pref + "*.json"))))
    files.sort(key=lambda p: os.path.basename(p))
    new_rows = []
    skipped_dry = 0
    for p in files:
        base = os.path.basename(p)
        if _is_intermediate(base):
            skipped_dry += 1
            continue
        d = _date_from_name(base)
        if not d:
            if verbose:
                print("[warn] 文件名里解析不出日期，跳过 %s" % base)
            continue
        if dates and d not in dates:
            continue
        try:
            obj = json.load(open(p, "r", encoding="utf-8"))
        except Exception as e:
            if verbose:
                print("[warn] 解析失败 %s: %s" % (base, e))
            continue
        picks, src = extract_picks(obj)
        if not picks:
            continue
        delivered = resolve_delivered(obj, src, _delivery_receipt(d))
        for pk in picks:
            if not isinstance(pk, dict):
                continue
            r = measure_one(d, pk, delivered)
            if r:
                r["src"] = src
                r["src_file"] = base
                r["src_files"] = [base]
                new_rows.append(r)

    old = read_jsonl(OUT_JSONL)
    covered = {(r.get("date"), r.get("code")) for r in new_rows}
    revived = revive_stale(old, covered)
    if revived:
        new_rows = new_rows + revived
    merged = merge_rows(old, new_rows)
    # 汇总层一致性防线：缺陷 A 的「T 日已涨停不可买」排除只在 measure_one 里生效，
    # 对台账中 status=ok 的历史行（源文件已轮转删除、revive_stale 不回溯 ok 的那类）
    # 无效 —— 实测 2026-11-02 002172 澳洋健康 LIANBAN 即残留污染样本。这里对所有
    # ok 行用日线 + is_limit_up 复核一次，命中即改标 excluded_t_limitup（保留原行
    # 全部字段、只追加可追溯标记，绝不删除），保证「T 日涨停不得进统计」口径唯一。
    # 幂等：已 excluded 跳过；write=False 离线预演同样生效且不污染台账。
    merged, n_rescanned = rescan_excluded(merged)
    summary = summarize(merged)
    if write:
        with open(OUT_JSONL, "w", encoding="utf-8") as f:
            for r in merged:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        json.dump(summary, open(OUT_SUM, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    if verbose:
        print("[info] 产出文件 %d 个，跳过 dry/中间件 %d 个，本轮测量 %d 条，累计 %d 条"
              % (len(files), skipped_dry, len(new_rows), len(merged)))
        print("[exclude] T 日已涨停不可买、排除出统计: %d 条（n_excluded_t_limitup，可审计；"
              "其中汇总层 retrospective 复核新增 %d 条）"
              % (summary.get("n_excluded_t_limitup", 0), n_rescanned))
        if revived:
            print("[revive] 源文件已轮转、靠台账+日线回放转 ok 的条目: %d 条 %s"
                  % (len(revived), [(r["date"], r["code"], r["name"]) for r in revived]))
        ov = summary["overall_delivered"]
        if ov.get("n"):
            print("[result] 已送达且 T+1 可测: n=%d 只 / %d 个交易日  次日收盘均值 %+.3f%%  胜率 %.1f%%  涨停率 %.1f%%"
                  % (ov["n"], summary["n_delivered_days"], ov["mean_close_ret"] * 100,
                     ov["win_rate"] * 100, ov["zt_rate"] * 100))
        for d, a in summary["by_date"].items():
            if a.get("n"):
                print("   日期 %s n=%-4d 次日收盘 %+.3f%%  胜率 %5.1f%%  涨停率 %5.1f%%"
                      % (d, a["n"], a["mean_close_ret"] * 100, a["win_rate"] * 100, a["zt_rate"] * 100))
        for t, a in summary["by_verify_tier"].items():
            if a.get("n"):
                print("   tier %-2s n=%-4d 次日收盘 %+.3f%%  胜率 %5.1f%%  涨停率 %5.1f%%"
                      % (t, a["n"], a["mean_close_ret"] * 100, a["win_rate"] * 100, a["zt_rate"] * 100))
        for s, a in summary["by_sub_strategy"].items():
            if a.get("n"):
                print("   %-15s n=%-4d 次日收盘 %+.3f%%  胜率 %5.1f%%  涨停率 %5.1f%%"
                      % (s, a["n"], a["mean_close_ret"] * 100, a["win_rate"] * 100, a["zt_rate"] * 100))
        for k, a in summary.get("by_r1", {}).items():
            if a.get("n"):
                print("   R1=%s n=%-4d 次日收盘 %+.3f%%  胜率 %5.1f%%  涨停率 %5.1f%%"
                      % (k, a["n"], a["mean_close_ret"] * 100, a["win_rate"] * 100, a["zt_rate"] * 100))
        print("[discipline] " + summary["discipline"])
        print("[pending] 待次日数据补齐: %d 条（下次运行自动补）" % summary["n_pending"])
    return summary


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
def selftest():
    fails = []

    def ck(name, cond, detail=""):
        if not cond:
            fails.append("%s %s" % (name, detail))
        print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else "  " + detail))

    print("[selftest] 涨停幅度")
    ck("主板 10%", abs(_limit_pct("600000") - 0.10) < 1e-9)
    ck("创业板 20%", abs(_limit_pct("300750") - 0.20) < 1e-9)
    ck("科创板 20%", abs(_limit_pct("688160") - 0.20) < 1e-9)
    ck("北交所 30%", abs(_limit_pct("830799") - 0.30) < 1e-9)
    ck("主板 ST 仍 10%", abs(_limit_pct("600537", "*ST亿晶") - 0.10) < 1e-9)
    ck("退市整理仍按板块", abs(_limit_pct("600000", "某某退") - 0.10) < 1e-9)

    print("[selftest] 次日定位与收益")
    bars = [("2026-10-23", 10.0, 10.0, 10.2, 9.9),
            ("2026-10-26", 11.0, 11.0, 11.0, 10.5),
            ("2026-10-27", 12.1, 12.1, 12.2, 11.5)]
    nb = next_bar_after(bars, "2026-10-26")
    ck("next_bar 取严格次日", nb is not None and nb[0] == "2026-10-27", str(nb))
    ck("末日无次日 -> None", next_bar_after(bars, "2026-10-27") is None)
    # 用纯函数测收益，不依赖真实日线（真实日线末端天然 pending）
    b1 = ("2026-10-27", 12.1, 12.1, 12.2, 11.5)
    m = compute_metrics("600000", "浦发银行", 11.0, b1)
    ck("close_ret 计算", abs(m["close_ret"] - 0.1) < 1e-6, str(m.get("close_ret")))
    ck("open_ret 计算", abs(m["open_ret"] - 0.1) < 1e-6, str(m.get("open_ret")))
    ck("主板 10% 判涨停", m["hit_zt"] is True, str(m.get("hit_zt")))
    m2 = compute_metrics("300750", "", 11.0, b1)
    ck("创业板 20% 不判涨停", m2["hit_zt"] is False, str(m2.get("hit_zt")))
    # 边界：刚好卡在涨停价上（浮点误差）也要判中
    m3 = compute_metrics("600000", "", 10.0, ("2026-10-27", 11.0, 11.0, 11.0, 10.0))
    ck("10% 涨停边界判中", m3["hit_zt"] is True, str(m3.get("hit_zt")))
    # 亏损样本
    m4 = compute_metrics("600000", "", 10.0, ("2026-10-27", 9.5, 9.4, 9.6, 9.3))
    ck("亏损不算 win", m4["win"] is False and m4["hit_zt"] is False)

    print("[selftest] 幂等合并")
    o = [{"date": "2026-10-26", "code": "600000", "status": "pending"}]
    n = [{"date": "2026-10-26", "code": "600000", "status": "ok", "close_ret": 0.05}]
    mm = merge_rows(o, n)
    ck("pending -> ok 被覆盖", len(mm) == 1 and mm[0]["status"] == "ok")
    mm2 = merge_rows(mm, [{"date": "2026-10-26", "code": "600000", "status": "pending"}])
    ck("ok 不被 pending 回退", mm2[0]["status"] == "ok")
    # 2026-11-03 新增：已送达的 pending 必须能被未送达版本的 ok 升级（送达事实沿用）
    o2 = [{"date": "2026-11-02", "code": "001219", "status": "pending", "delivered": True,
           "src_file": "selection_push_intraday_2026-11-02.json",
           "src_files": ["selection_push_intraday_2026-11-02.json"]}]
    n2 = [{"date": "2026-11-02", "code": "001219", "status": "ok", "close_ret": 0.0014,
           "delivered": False, "src_file": "unified_tail_pick_2026-11-02.json",
           "src_files": ["unified_tail_pick_2026-11-02.json"]}]
    mu = merge_rows(o2, n2)
    ck("已送达 pending 可升级为 ok",
       len(mu) == 1 and mu[0]["status"] == "ok" and abs(mu[0]["close_ret"] - 0.0014) < 1e-9, str(mu))
    ck("升级后 delivered 仍为 True", mu[0]["delivered"] is True, str(mu))
    ck("升级后来源双向合并",
       len(mu[0].get("src_files") or []) == 2, str(mu[0].get("src_files")))
    # 低价股舍入：5.54 -> 6.09 只有 +9.928%，仍是涨停
    m5 = compute_metrics("601956", "东贝集团", 5.54, ("2026-10-27", 6.05, 6.09, 6.09, 6.0))
    ck("低价股舍入涨停不漏判", m5["hit_zt"] is True, str(m5))

    print("[selftest] 源文件丢失的 pending 回放")
    bars = load_bars("600000")
    if len(bars) >= 3:
        d0, d1 = bars[-2], bars[-1]
        stale = [{"date": d0[0], "code": "600000", "name": "浦发银行", "status": "pending",
                  "delivered": True, "src": "push", "src_file": "gone_file.json",
                  "src_files": ["gone_file.json"]}]
        rv = revive_stale(stale, set())
        ck("无源文件也能靠日线回放转 ok", len(rv) == 1 and rv[0]["status"] == "ok", str(rv)[:200])
        if rv:
            ck("回放收益与日线一致",
               abs(rv[0]["close_ret"] - (d1[2] / d0[2] - 1.0)) < 1e-5, str(rv[0].get("close_ret")))
            ck("回放保留原来源与送达事实",
               rv[0].get("src_file") == "gone_file.json" and rv[0].get("delivered") is True
               and rv[0].get("revived_from") == "pending", str(rv[0])[:200])
        ck("已被现存文件覆盖的条目不再回放", revive_stale(stale, {(d0[0], "600000")}) == [])
        ck("ok 条目不回放",
           revive_stale([{"date": d0[0], "code": "600000", "status": "ok"}], set()) == [])
    else:
        print("  SKIP  本地无 600000 日线，跳过回放自检")

    print("[selftest] 中间件排除（2026-11-05 污染事故）")
    ck("pre_selfheal 中间快照被排除",
       _is_intermediate("unified_tail_pick_2026-11-05.pre_selfheal.json") is True)
    ck("dry+pre_selfheal 复合名被排除",
       _is_intermediate("unified_tail_pick_2026-11-05.dry.pre_selfheal.json") is True)
    ck("dry 演练件被排除",
       _is_intermediate("unified_tail_pick_2026-11-05.dry.json") is True)
    ck("正式产出不被误杀",
       _is_intermediate("unified_tail_pick_2026-11-05.json") is False)
    ck("盘前 preopen 不被 .pre_ 误杀",
       _is_intermediate("selection_push_preopen_2026-10-29.json") is False)
    ck("盘中 intraday 不被误杀",
       _is_intermediate("selection_push_intraday_2026-11-03.json") is False)

    print("[selftest] 送达判定边界")
    ck("自身已送达即送达",
       resolve_delivered({"push_ok": True, "dry": False}, "picks", False) is True)
    ck("wrapper 恒 dry 时靠回执升级 push 口径",
       resolve_delivered({"push_ok": True, "dry": True}, "push", True) is True)
    ck("回执不得把 picks 全量判成已送达（11-05 根因）",
       resolve_delivered({"push_ok": True, "dry": True}, "picks", True) is False)
    ck("无回执且未推送 -> 未送达",
       resolve_delivered({"push_ok": False}, "push", False) is False)

    print("[selftest] 结果: %s" % ("全部 PASS" if not fails else "%d 项 FAIL" % len(fails)))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="只核验某一天（YYYY-MM-DD）")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    dates = {a.date} if a.date else None
    run(dates=dates, write=not a.no_write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
