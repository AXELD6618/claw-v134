#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
evolution_reweight.py — 进化闭环唯一入口（融合 15:35 每日核验 + 周日 OOS 调优回路）

2026-10-26 第五轮收口：
  此前系统「只测不改」——15:35 测出次日结果，但全库无消费者去调权；
  而 autonomous_evolution_engine.py 写回的是已被淘汰的 legacy m46/m64 模块（死代码）。
  本模块取代之，成为进化闭环的真实、可验证入口。

两个调度点共用本模块（融合思路）：
  - 15:35 尾盘推送结果核验 之后调用 → 每日轻量检查，样本不足时只记日志不动作
  - 周日 02:00 OOS 调优回路 调用本模块 → 作为「每周深度进化」唯一入口
  二者都读 data/tail_pick_outcome.jsonl（15:35 写），做阈值门控的调权。

阈值门控（防小样本过拟合，核心纪律，绝不破）：
  MIN_N = 30      已送达且可测的样本只数
  MIN_DAYS = 10   独立交易日数
  未同时达标 → 只输出「样本不足」，绝不调权。

调权逻辑（保守，保护真 edge）：
  - LIANBAN 是唯一 OOS 验证真 edge，永不被自动下调（PROTECTED）。
  - MONITORED = TUNED + ["HOLD5_GRAIL"]：HOLD5_GRAIL 是圣杯主推承载层，必须纳入监控——
    此前它既不在 TUNED 也不在 PROTECTED，进化闭环对其完全失明，亏损也无法被抑制。
  - 负期望下调阈值：mean_close_ret < 0 且 win_rate < 0.5（原 NEG_EV=-0.005 会让 -0.43% 这类
    亏损策略因 -0.004264 > -0.005 而漏判逃逸，已修正）。
  - 上调（mean_close_ret>0 且 zt_rate 高于基线）仅限 TUNED 子集，HOLD5_GRAIL 不凭薄样本被抬升。
  - 写入 data/p1_rules.json['evolution']['cap_override']，unified_tail_pick 次日读取生效
    （rank_for_push 的保底名额已接通该覆盖，见 unified_tail_pick.py）。
  - 追加 data/evolution_log.json（不可变演进史）。

用法：
  python research/evolution_reweight.py            # 达标则调权，否则只报告
  python research/evolution_reweight.py --dry      # 只算不写
  python research/evolution_reweight.py --selftest # 自检
"""
import os, sys, json, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
OUT_JSONL = os.path.join(DATA, "tail_pick_outcome.jsonl")
RULES_P = os.path.join(DATA, "p1_rules.json")
EVOL_LOG = os.path.join(DATA, "evolution_log.json")

if ROOT not in sys.path:            # 允许 `python research/evolution_reweight.py` 直接运行
    sys.path.insert(0, ROOT)
from research.board_limit import board_limit_pct  # 涨跌停幅度单一真相源（2026-11-06 收口）

MIN_N = 30        # 总样本门槛
MIN_DAYS = 10     # 独立交易日门槛
ROLL = 40         # 决策用滚动窗口（允许策略恢复）
NEG_EV = -0.005   # 负期望阈值（次日收盘收益均值）
DEFAULT_CAP = {"LIANBAN": 12, "HOLD5_GRAIL": 6, "COILED_SPRING": 5,
               "REVERSAL": 4, "PRE_BREAKOUT": 4, "EVENT_CATALYST": 4}
PROTECTED = {"LIANBAN"}   # 永不被自动下调
TUNED = ["PRE_BREAKOUT", "EVENT_CATALYST", "REVERSAL"]  # 可被进化调权(含上调)的 C 级
# 监控集 = TUNED + 圣杯主推承载层。HOLD5_GRAIL 此前既不在 TUNED 也不在 PROTECTED，
# 进化闭环对其完全失明——亏损也无法被抑制。现纳入监控，仅允许下调(负期望)，禁止凭薄样本上调。
MONITORED = TUNED + ["HOLD5_GRAIL"]

# 涨停价口径：统一委托单一真相源（name 形参保留以兼容既有调用）
def _limit_pct(code, name=""):
    return board_limit_pct(code)


def load_rows():
    if not os.path.exists(OUT_JSONL):
        return []
    rows = []
    with open(OUT_JSONL, encoding="utf-8") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("status") == "ok" and r.get("delivered"):
                rows.append(r)
    return rows


def aggregate(rows):
    by_sub = collections.defaultdict(list)
    for r in rows:
        by_sub[r.get("sub")].append(r)
    return by_sub


def _metrics(sub_rows):
    n = len(sub_rows)
    if n == 0:
        return None
    days = len(set(r.get("date") for r in sub_rows))
    cr = [r.get("close_ret") for r in sub_rows if r.get("close_ret") is not None]
    if not cr:
        return None
    mean_cr = sum(cr) / len(cr)
    win = sum(1 for r in sub_rows if r.get("win"))
    zt = sum(1 for r in sub_rows
             if r.get("hit_zt") or
             (r.get("close_ret") is not None and r.get("limit_pct") and
              r["close_ret"] >= (r.get("limit_pct") or 0.10) - 0.002))
    return {"n": n, "days": days, "mean_close_ret": mean_cr,
            "win_rate": win / n, "zt_rate": zt / n}


def decide(sub, cur_cap, m, baseline_cr):
    """返回 (new_cap, reason)。保守、带滞回。"""
    if sub in PROTECTED:
        return cur_cap, "受保护真edge，不自动调"
    if m is None or m["n"] < 10:
        return cur_cap, "该策略样本<10，维持"
    # 负期望 → 逐级下调（阈值用 <0 而非 <NEG_EV，避免 -0.43% 这类亏损因 -0.004264>-0.005 漏判）
    if m["mean_close_ret"] < 0 and m["win_rate"] < 0.5:
        new = max(0, cur_cap - 2)
        if new != cur_cap:
            return new, ("负期望(mean_close_ret=%.4f, win=%.2f)→下调 %d→%d"
                         % (m["mean_close_ret"], m["win_rate"], cur_cap, new))
        return cur_cap, "已压至下限0"
    # 正期望且涨停率高于基线 → 逐级上调（封顶6）。仅限 TUNED 子集，
    # HOLD5_GRAIL 作为圣杯主推不凭薄样本被自动抬升（其名额由负期望路径下调即可）。
    if sub in TUNED and m["mean_close_ret"] > 0 and m["zt_rate"] > baseline_cr + 0.02:
        new = min(6, cur_cap + 1)
        if new != cur_cap:
            return new, ("正期望+高涨停率→上调 %d→%d" % (cur_cap, new))
    return cur_cap, "维持（无显著偏离）"


def run(dry=False):
    rows = load_rows()
    total_n = len(rows)
    total_days = len(set(r.get("date") for r in rows))
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print("[evolution] 已送达可测样本 %d 只 / %d 个交易日" % (total_n, total_days))

    if total_n < MIN_N or total_days < MIN_DAYS:
        msg = ("样本不足（门槛 %d 只 & %d 日，现 %d 只 & %d 日）→ 不调权，继续累积"
               % (MIN_N, MIN_DAYS, total_n, total_days))
        print("[evolution] " + msg)
        entry = {"ts": ts, "action": "NO_OP_INSUFFICIENT", "reason": msg,
                 "total_n": total_n, "total_days": total_days}
        if not dry:
            _append_log(entry)
        return entry

    by_sub = aggregate(rows)
    # 组合基线（用于正期望比较）：全样本 close_ret 均值
    baseline_cr = (sum(r["close_ret"] for r in rows if r.get("close_ret") is not None)
                   / max(1, sum(1 for r in rows if r.get("close_ret") is not None)))

    # 当前 cap_override
    cur = _load_overrides()
    new_cap = dict(DEFAULT_CAP)
    new_cap.update({k: cur.get(k, DEFAULT_CAP[k]) for k in DEFAULT_CAP})

    changes = {}
    detail = {}
    for sub in MONITORED:
        sub_rows = sorted(by_sub.get(sub, []), key=lambda r: r.get("date", ""))
        window = sub_rows[-ROLL:] if len(sub_rows) > ROLL else sub_rows
        m = _metrics(window)
        nc, reason = decide(sub, new_cap.get(sub, DEFAULT_CAP[sub]), m, baseline_cr)
        new_cap[sub] = nc
        detail[sub] = {"metrics": m, "reason": reason}
        if nc != cur.get(sub, DEFAULT_CAP[sub]):
            changes[sub] = nc
        print("[evolution] %-14s n=%s days=%s close=%.4f win=%.2f zt=%.2f → cap=%d (%s)"
              % (sub, m["n"] if m else 0, m["days"] if m else 0,
                 m["mean_close_ret"] if m else 0, m["win_rate"] if m else 0,
                 m["zt_rate"] if m else 0, nc, reason))

    entry = {"ts": ts, "action": "TUNE" if changes else "NO_CHANGE",
             "total_n": total_n, "total_days": total_days,
             "baseline_close_ret": round(baseline_cr, 6),
             "cap_before": cur, "cap_after": new_cap,
             "changes": changes, "detail": {k: v["reason"] for k, v in detail.items()}}
    print("[evolution] 调权结果: %s" % (changes if changes else "无变化"))
    if not dry:
        _write_overrides(new_cap)
        _append_log(entry)
    else:
        print("[evolution] --dry 未写入")
    return entry


def _load_overrides():
    try:
        d = json.load(open(RULES_P, encoding="utf-8"))
        return (d.get("evolution") or {}).get("cap_override") or {}
    except Exception:
        return {}


def _write_overrides(cap):
    d = {}
    if os.path.exists(RULES_P):
        try:
            d = json.load(open(RULES_P, encoding="utf-8"))
        except Exception:
            d = {}
    ev = d.get("evolution") or {}
    ev["cap_override"] = {k: int(v) for k, v in cap.items()}
    ev["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    ev["note"] = ("由 research/evolution_reweight.py 产出；阈值门控 MIN_N=%d MIN_DAYS=%d；"
                  "LIANBAN 受保护永不下调" % (MIN_N, MIN_DAYS))
    d["evolution"] = ev
    json.dump(d, open(RULES_P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _append_log(entry):
    hist = []
    if os.path.exists(EVOL_LOG):
        try:
            hist = json.load(open(EVOL_LOG, encoding="utf-8"))
        except Exception:
            hist = []
    if not isinstance(hist, list):
        hist = []
    hist.append(entry)
    json.dump(hist, open(EVOL_LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def selftest():
    ok = True
    # 1) 阈值门控：空样本 → NO_OP
    e = run.__wrapped__ if hasattr(run, "__wrapped__") else None
    # 直接构造场景：临时清空 jsonl 不可取，改用聚合单测
    # 2) decide 单测
    cases = [
        ("PRE_BREAKOUT", 4, {"n": 12, "days": 8, "mean_close_ret": -0.03, "win_rate": 0.33, "zt_rate": 0.0},
         -0.005, 2, "负期望下调"),
        ("PRE_BREAKOUT", 2, {"n": 12, "days": 8, "mean_close_ret": -0.03, "win_rate": 0.33, "zt_rate": 0.0},
         -0.005, 0, "继续下调至0"),
        ("PRE_BREAKOUT", 4, {"n": 12, "days": 8, "mean_close_ret": 0.02, "win_rate": 0.6, "zt_rate": 0.15},
         0.01, 5, "正期望上调"),
        ("LIANBAN", 4, {"n": 12, "days": 8, "mean_close_ret": -0.5, "win_rate": 0.1, "zt_rate": 0.0},
         -0.005, 4, "受保护不下调"),
        ("PRE_BREAKOUT", 4, {"n": 5, "days": 3, "mean_close_ret": -0.03, "win_rate": 0.3, "zt_rate": 0.0},
         -0.005, 4, "样本<10维持"),
        # 核心圣杯策略：原先既不在 TUNED 也不在 PROTECTED，进化对其失明；
        # 且旧阈值 <-0.005 会让 -0.004264 这类亏损漏判。现应被下调。
        ("HOLD5_GRAIL", 6, {"n": 13, "days": 9, "mean_close_ret": -0.004264, "win_rate": 0.3846, "zt_rate": 0.2308},
         0.01, 4, "圣杯负期望下调(原阈值漏判)"),
        # 圣杯即便正期望也不得凭薄样本被自动抬升（仅 TUNED 可上调）。
        ("HOLD5_GRAIL", 4, {"n": 12, "days": 8, "mean_close_ret": 0.02, "win_rate": 0.6, "zt_rate": 0.2},
         0.01, 4, "圣杯不凭薄样本上调"),
    ]
    for sub, cap, m, base, exp, label in cases:
        got, _ = decide(sub, cap, m, base)
        if got != exp:
            print("[selftest] FAIL: %s 期望 %d 实得 %d" % (label, exp, got))
            ok = False
        else:
            print("[selftest] PASS: %s → %d" % (label, got))
    # 3) 聚合单测
    rows = [{"sub": "PRE_BREAKOUT", "date": "2026-10-%02d" % (i % 28 + 1),
             "status": "ok", "delivered": True, "close_ret": -0.02, "win": False, "hit_zt": False}
            for i in range(15)]
    by = aggregate(rows)
    assert len(by["PRE_BREAKOUT"]) == 15, "聚合失败"
    print("[selftest] PASS: 聚合 15 行")
    # 4) 阈值门控逻辑（不写文件）
    import tempfile
    global OUT_JSONL
    old = OUT_JSONL
    OUT_JSONL = os.path.join(tempfile.gettempdir(), "no_such_outcome.jsonl")
    e = run(dry=True)
    assert e["action"] == "NO_OP_INSUFFICIENT", "空样本应 NO_OP"
    OUT_JSONL = old
    print("[selftest] PASS: 空样本→NO_OP_INSUFFICIENT")
    print("[selftest] %s" % ("ALL PASS" if ok else "HAS FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest())
    dry = len(sys.argv) > 1 and sys.argv[1] == "--dry"
    run(dry=dry)
