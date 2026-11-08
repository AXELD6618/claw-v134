#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""intraday_universe.py — 生成盘中统一监控池（确定性合并，替代"每次让 agent 自己拼四路"）

为什么需要它（2026-10-27 事故）：
  三个盘中 agentic 任务的 prompt 写着「四路合并约 115-130 只」，但当日实际落盘的
  tail_raw/2026-10-27_quotes.json 只有 **17 只**（连板台账 15 + 持仓 2），
  T1(30) / tier2(73) / T2观察(50) / 发现层(78) **一只都没扫**。
  当日 002249 大洋电机（在 T1）10:00 就封板、002708 光洋股份（在发现层）高开秒板，
  都不是"没被识别"，而是**根本不在监控范围内**。
  让 LLM 每次自己读五个格式各异的 JSON 去合并，天然会退化成"只读有现成结构的那一个"。
  合并逻辑必须固化在代码里，agent 只消费结果。

用法：
  .venv/Scripts/python.exe research/intraday_universe.py [--date YYYY-MM-DD] [--min-n 80]

输出：data/intraday_universe.json
  {"as_of":..., "n":N, "sources":{...}, "codes":[{"code","name","src":[...]}]}
退出码：0 正常；2 = 池子规模低于 --min-n（覆盖不足，需告警）

2026-10-28 扩为十一路：新增 MOVES（发现层·冲高回落异动观察，修复 300291-type 双重盲区）、
OVER（发现层·超跌放量观察，用户授权监测扩展，捕子类B深跌反弹型）、
W2S（发现层·盘中弱转强观察，超圣杯纪律新买点，捕子类C浅跌突然板型）、
R31（观察位·横盘后突破 r31_breakout，只累积样本外时间，等证伪或证实）。
MOVES/OVER/W2S/R31 仅作监测层，来源 discovery_candidates.json.{moves,oversold,w2s,r31}，绝不进入 buy_score。
"""
import os
import sys
import json
import argparse
import datetime

# REJECTED 监控池（见 MEMORY.md §3 / §7）：moves_sig / oversold / weak_to_strong 均已证伪，
# 仅作监测层且不进 buy_score；列为废弃，默认不纳入盘中监控池，以省计算与覆盖监控噪声。
# 需审计时从本集合移除对应键即可恢复。
DISABLED_POOLS = {"MOVES", "OVER", "W2S"}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _iter_codes(rows, keys=("code", "sec_code")):
    for r in (rows or []):
        if isinstance(r, str):
            yield str(r), None
            continue
        if not isinstance(r, dict):
            continue
        c = None
        for k in keys:
            if r.get(k):
                c = str(r[k])
                break
        if c:
            yield c, (r.get("name") or r.get("sec_name"))


def collect(date=None):
    """十一路合并，返回 ({code: {name, src}}, 各路命中数)。"""
    pool = {}
    hits = {}

    def add(codes, tag):
        n = 0
        for c, nm in codes:
            c = c.strip()
            if not c or not c.isdigit() or len(c) != 6:
                continue
            e = pool.setdefault(c, {"code": c, "name": None, "src": []})
            if nm and not e["name"]:
                e["name"] = nm
            if tag not in e["src"]:
                e["src"].append(tag)
                n += 1
        # 2026-10-28 修复：同一 tag 可能被多次 add（如 DISC=emerg+entries 两路），
        # 原 `hits[tag] = n` 会覆盖前次计数 → DISC 命中恒显 0（实际已合并）。
        # 改为累加，使覆盖率监控数字真实（不影响合并结果、不改 buy_score）。
        hits[tag] = hits.get(tag, 0) + n

    # 1) 连板台账 —— 唯一 OOS 真 edge 的候选源
    wl = _load(os.path.join(ROOT, "backtest_cache", "lianban_watchlist.json"))
    add(_iter_codes((wl or {}).get("codes")), "LIANBAN")

    # 2) T1 关联池
    #    2026-11-04 修复：candidates.json.tier1 由 p1_discovery.apply_lifecycle 维护，
    #    该函数只踢不刷 —— tier1 的 date 恒为初次入池日（实测 30 只全停在 2026-10-23，
    #    8 个交易日零轮转），且新入池一律进 tier2，tier1 永不增员。
    #    而 p1_agent_loop.scan_universe 每日实时产出的 Tier1 与这份清单几乎无交集
    #    （实测 2026-11-04：实时 57 只 vs 清单 30 只，overlap=1）→ 盘中 T1 监控长期空转。
    #    改为「台账实时 Tier1 ∪ candidates.json.tier1」，只增不减，纯监控层，不入 buy_score。
    cand = _load(os.path.join(ROOT, "data", "candidates.json"))
    add(_iter_codes((cand or {}).get("tier1")), "T1")
    _day = date or datetime.date.today().isoformat()
    _led = _load(os.path.join(ROOT, "data", "candidates_ledger.json"))
    if isinstance(_led, dict):
        _live = [{"code": _c}
                 for _c, _e in _led.items()
                 if isinstance(_e, dict)
                 and _e.get("last_tier") == 1
                 and _e.get("state") == "edge_active"
                 and _e.get("last_flag_date") == _day]
        add(_iter_codes(_live), "T1")

    # 3) candidates.tier2（发现层的二级池，之前完全没被盘中监控引用）
    add(_iter_codes((cand or {}).get("tier2")), "T1B")

    # 4) T2 观察池（含 theme_fit 过滤）
    t2 = _load(os.path.join(ROOT, "data", "tier2_watchlist_filtered.json"))
    add(_iter_codes((t2 or {}).get("watchlist")), "T2")

    # 5) 发现层·当日紧急异动（新涨停的唯一入口）
    disc = _load(os.path.join(ROOT, "data", "discovery_candidates.json"))
    add(_iter_codes((disc or {}).get("emerg")), "DISC")

    # 6) 发现层·新入池
    add(_iter_codes((disc or {}).get("entries")), "DISC")

    # 8) 发现层·冲高回落异动观察（2026-10-28 新增，修复 300291-type 双重盲区）
    #    discovery_candidates.json.moves = 盘中最高涨幅≥5% 但收盘涨幅<2% 的异动票。
    #    纯监测层，绝不进入 buy_score / 买点路径。
    #    【REJECTED】moves_sig 已证伪（MEMORY.md §3）→ 默认关闭（见 DISABLED_POOLS）。
    if "MOVES" not in DISABLED_POOLS:
        add(_iter_codes((disc or {}).get("moves")), "MOVES")

    # 9) 发现层·超跌放量观察（2026-10-28 授权监测扩展，捕子类B深跌反弹型）
    #    discovery_candidates.json.oversold = 收盘跌幅≥4% 且 放量(volr≥1.5) 的超跌票。
    #    纯监测层，绝不进入 buy_score / 买点路径。
    #    【REJECTED】oversold 三口袋全 REJECT（MEMORY.md §3）→ 默认关闭（见 DISABLED_POOLS）。
    if "OVER" not in DISABLED_POOLS:
        add(_iter_codes((disc or {}).get("oversold")), "OVER")

    # 10) 发现层·盘中弱转强观察（2026-10-28 #3 新买点，超圣杯纪律）
    #     discovery_candidates.json.w2s = 开盘弱(open_pct<=0) → 盘中强拉未封板(pct≥7% 且<涨停) 的封板前可买窗口。
    #     纯监测层：供盘中 agentic 任务盯「浅跌突然板」型标的（长城/联明/索通），封板前可达。
    #    【REJECTED】weak_to_strong 已证伪（MEMORY.md §3）→ 默认关闭（见 DISABLED_POOLS）。
    if "W2S" not in DISABLED_POOLS:
        add(_iter_codes((disc or {}).get("w2s")), "W2S")

    # 11) 观察位·横盘后突破 r31_breakout（2026-10-28 裁决 WATCH）
    #     discovery_candidates.json.r31 = 前10日收益std<1%(横盘) → 当日涨幅>3%(突破) 且未涨停。
    #     全市场 OOS 按日配对超额 +98.67bp / t=3.659，但 Purged 5折仅 3/5 为正、2026Q3 -12.7bp
    #     → **WATCH，不是 PROMOTE**：不进 buy_score、不自动给仓位，只挂观察位等样本外时间累积。
    #     纯监测层，绝不进入 buy_score / 买点路径。
    add(_iter_codes((disc or {}).get("r31")), "R31")

    # 7) 持仓（必须盯，止损优先于一切）
    hd = _load(os.path.join(ROOT, "current_holdings.json"))
    add(_iter_codes((hd or {}).get("holdings")), "HOLD")

    return pool, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="交易日（默认当天）")
    ap.add_argument("--min-n", type=int, default=80,
                    help="低于此规模视为覆盖不足（退出码 2）")
    a = ap.parse_args()
    date = a.date or datetime.date.today().isoformat()

    pool, hits = collect(date)
    codes = sorted(pool.values(), key=lambda x: x["code"])
    out = {
        "as_of": date,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n": len(codes),
        "sources": hits,
        "note": ("盘中统一监控池：十一路去重合并（含 MOVES 冲高回落异动观察、OVER 超跌放量观察、"
                 "W2S 盘中弱转强观察、R31 横盘后突破观察位）。盘中任务必须以此为准拉行情，"
                 "不得只拉连板台账（2026-10-27 事故：只关注 15 只，T1/发现层全裸）"),
        "codes": codes,
    }
    outp = os.path.join(ROOT, "data", "intraday_universe.json")
    json.dump(out, open(outp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("[universe] date=%s 合计 %d 只" % (date, len(codes)))
    for tag, n in hits.items():
        print("    %-8s +%d" % (tag, n))
    print("    落盘: data/intraday_universe.json")
    for probe in ("002249", "002708"):
        e = pool.get(probe)
        print("    探针 %s -> %s" % (probe, ("在池内 src=%s" % e["src"]) if e else "不在池内"))
    if len(codes) < a.min_n:
        print("[ERROR] 池子仅 %d 只 < 下限 %d —— 覆盖不足，盘中任务必须报 COVERAGE_FAIL"
              % (len(codes), a.min_n))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
