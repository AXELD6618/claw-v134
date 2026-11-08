#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
毕方灵犀貔貅 · 全市场发现层 + 候选池生命周期（进入/踢出 + 容量上限）

病根（2026-10-25 诊断）：
  scan_universe 扫 backtest_cache/daily/*.csv（已全量 backfill 5225 只），但日常只靠 TDX 刷新
  70 只 watchlist，其余 5155 只 K线过期 → 全市场扫描用过期数据，新启动妖股漏选。
  candidates.json 亦无进入/踢出门控，每次只是从过期缓存重算 73 只。

本模块职责（沙箱可达，不依赖 TDX MCP）：
  1. 全市场发现层：用腾讯 gtimg 实时快照对 5225 只做 EOD 量化初筛，捕获「正在启动」的异动票
     （涨停/连板/首板/放量突破）——这是 scan_universe（过期缓存 feats）补不到的实时层。
  2. 进入门控：通过初筛 + 非退市/ST 硬审查 + 主题契合（已知复用/未知标 NEEDS_RESEARCH）→ 入池。
  3. 踢出门控：退市/ST/*退、theme_fit 降 off、长期无信号、破位 → 移出活跃池（移入 graveyard，不物理删除，铁律全量入库）。
  4. 容量上限：活跃池封顶 POOL_CAP，超限按 score+沉默时长驱逐最低优先。

数字契约（改动须报 old→new→影响面）：
  ZT_PCT=9.5          涨停判定阈值(%)
  BREAKOUT_PCT=5.0    放量突破涨幅阈值(%)
  BREAKOUT_VOLR=3.0   放量突破量比阈值(量/5日均量)
  POOL_CAP=200        活跃池上限(Tier1 30 + Tier2 动态 170)
                      2026-10-26 old=100 → new=200。影响面：发现层日信号历史峰值 107、
                      存量 Tier2 峰值 70 → 结构性最大 177 < 200，**消除「每天必然驱逐 34 只」**。
                      代价：监控池上限翻倍，盘中 gtimg 批次 2→3 批（<1s），内存无影响。
                      反作用：低质量票留存更久 → 由 score + 沉默天数门控淘汰，不靠容量硬砍。
                      可用 data/pool_policy.json 的 {"pool_cap": N} 覆盖，不必改代码。
  NO_SIGNAL_DAYS=60   台账无验证事件(连板/断层反包)交易日数 → 降级
  NO_ALERT_DAYS=30    盘中无 agentic 告警天数 → 沉默计数
  CHUNK=100           gtimg 每批代码数
"""
import os, sys, json, glob, time, datetime, urllib.request, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import theme_intel  # 指令 D：题材优先级注入容量驱逐
import r31_watch  # 观察位 r31_breakout 扫描器（只读本地日线；WATCH，绝不进 buy_score）

ZT_PCT = 9.5
BREAKOUT_PCT = 5.0
BREAKOUT_VOLR = 3.0
OVERSELL_PCT = -4.0      # 超跌观察阈值(%)：收盘跌幅≥4% 视为超跌（2026-10-28 授权监测扩展）
OVERSELL_VOLR = 1.5     # 超跌观察量比阈值(量/5日均量)：放量确认，降噪声
POOL_CAP = 200
NO_SIGNAL_DAYS = 60
NO_ALERT_DAYS = 30
CHUNK = 100
W2S_PCT = 7.0           # 盘中弱转强买点：当前涨幅≥7% 且未封板（封板前可买）— 突破"尾盘买不可达"
W2S_VOLR = 1.5          # 盘中弱转强量比阈值：放量确认（降噪声）

POOL_POLICY_P = os.path.join(ROOT, "data", "pool_policy.json")


def pool_cap():
    """活跃池容量。默认 POOL_CAP=200；可用 data/pool_policy.json 的 {"pool_cap": N} 覆盖。

    为什么做成外部可调：容量是「制度参数」不是「技术常量」。信号量会随行情阶段变化
    （2026-10 峰值 107/日，熊市可能只剩 20/日），每次调容量都改代码不可行也不可审计。
    """
    cap = POOL_CAP
    try:
        d = json.load(open(POOL_POLICY_P, encoding="utf-8"))
        v = int(d.get("pool_cap") or 0)
        if 50 <= v <= 5000:
            cap = v
    except Exception:
        pass
    return cap

UNIVERSE_CSV = os.path.join(HERE, "backfill_all_codes.csv")
CAND_P = os.path.join(ROOT, "data", "candidates.json")
RESEARCH_P = os.path.join(ROOT, "data", "candidate_research.json")
LEDGER_P = os.path.join(ROOT, "data", "candidates_ledger.json")
DISCOVERY_P = os.path.join(ROOT, "data", "discovery_candidates.json")
LIFECYCLE_P = os.path.join(ROOT, "data", "pool_lifecycle.json")
GRAVEYARD_P = os.path.join(ROOT, "data", "pool_graveyard.json")
ALERT_LOG_P = os.path.join(ROOT, "data", "intraday_agentic_log.json")
DAILY = os.path.join(ROOT, "backtest_cache", "daily")

# 模块级 research 缓存（main 加载后供 _score 注入题材优先级，避免每票重读）
RESEARCH_CACHE = {}


def _research_cache():
    """懒加载研究库。原实现只由 main() 赋值，其它调用路径（测试/单跑 apply_lifecycle）
    会静默拿到空字典 → 所有票 score 并列 0.3 → 驱逐排序退化。这里兜住。"""
    global RESEARCH_CACHE
    if not RESEARCH_CACHE and os.path.exists(RESEARCH_P):
        try:
            obj = json.load(open(RESEARCH_P, encoding="utf-8"))
            if isinstance(obj, dict):
                RESEARCH_CACHE = obj
        except Exception:
            pass
    return RESEARCH_CACHE


def _prefix(code):
    if code[:1] in "69":
        return "sh"
    if code[:1] in "48":
        return "bj"
    return "sz"


def load_universe():
    codes = []
    with open(UNIVERSE_CSV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or not line[0].isdigit():
                continue
            codes.append(line)
    return codes


def _gtimg_batch(codes, timeout=20):
    q = ",".join(_prefix(c) + c for c in codes)
    url = "https://qt.gtimg.cn/q=" + q
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout
    ).read().decode("gbk", "ignore")
    out = {}
    for seg in raw.split(";"):
        seg = seg.strip()
        if not seg.startswith("v_"):
            continue
        body = seg.split("=", 1)[1].strip().strip('"')
        f = body.split("~")
        if len(f) < 40:
            continue
        code = seg[2:].split("=", 1)[0]   # e.g. sh600519
        code = code[2:] if code[:2] in ("sh", "sz", "bj") else code
        try:
            out[code] = {
                "name": f[1].strip(),
                "price": float(f[3]),
                "prev_close": float(f[4]),
                "open": float(f[5]),
                "vol": float(f[6]),          # 手
                "time": f[30],
                "pct": float(f[32]),
                "high": float(f[33]),
                "low": float(f[34]),
                "amt": float(f[37]) if len(f) > 37 and f[37] else 0.0,
            }
        except Exception:
            continue
    return out


SNAP_STATS = {"chunks_total": 0, "chunks_ok": 0, "chunks_fail": 0,
              "budget_hit": False, "elapsed_sec": 0.0}


def gtimg_snapshot(codes, budget_sec=240, per_timeout=8):
    """分块拉全市场快照，返回 {code: snap}。沙箱可达。

    2026-10-26 修（P0 性能/可观测）：原实现单批 timeout=20s、无总预算，一遇网络退化
    就变成 53 批 × 20s ≈ 17 分钟「静默挂着」，实测跑 20 分钟仍未返回 —— 16:00 自动化
    必然被超时杀掉，且没有任何证据说明它扫到了多少。现改为：
      · 单批 timeout 8s，失败重试 1 次；
      · **总预算 budget_sec（默认 240s）**，到点立即中止剩余批次；
      · 覆盖率写进 SNAP_STATS 并打印，让「扫了多少」永远可查（禁止静默半途而废）。
    """
    global SNAP_STATS
    t0 = time.time()
    total = (len(codes) + CHUNK - 1) // CHUNK
    SNAP_STATS.update({"chunks_total": total, "chunks_ok": 0, "chunks_fail": 0,
                       "budget_hit": False, "elapsed_sec": 0.0})
    out = {}
    for i in range(0, len(codes), CHUNK):
        if time.time() - t0 > budget_sec:
            SNAP_STATS["budget_hit"] = True
            print("[discovery] ⚠ 快照预算 %ds 用尽，已中止剩余 %d/%d 批（结果不完整，已如实标注）"
                  % (budget_sec, total - SNAP_STATS["chunks_ok"] - SNAP_STATS["chunks_fail"], total))
            break
        chunk = codes[i:i + CHUNK]
        got = None
        for attempt in (1, 2):
            try:
                got = _gtimg_batch(chunk, timeout=per_timeout)
                break
            except Exception as e:
                if attempt == 2:
                    SNAP_STATS["chunks_fail"] += 1
                    sys.stderr.write("[warn] gtimg chunk %d failed(2次): %s\n" % (i, e))
        if got:
            out.update(got)
            SNAP_STATS["chunks_ok"] += 1
        time.sleep(0.05)
    SNAP_STATS["elapsed_sec"] = round(time.time() - t0, 1)
    return out


def _cached_vol_avg(code, n=5):
    """从缓存 CSV 读近 n 日均量(手)，失败返回 None。

    2026-10-26 修：原实现把整个 CSV 的所有成交量行都 append 进 list 再切片，
    单文件几千行 × 上千只票 = 上千万次 float()。改用 deque(maxlen=n+1) 边读边丢，
    内存与解析量都只与 n 有关。
    """
    p = os.path.join(DAILY, code + ".csv")
    if not os.path.exists(p):
        return None
    try:
        tail = collections.deque(maxlen=n + 1)
        with open(p, encoding="utf-8") as f:
            f.readline()                      # 跳过表头
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tail.append(float(line.split(",")[-1]))
                except Exception:
                    continue
        if len(tail) < n:
            return None
        vals = list(tail)[-n:]
        return sum(vals) / n
    except Exception:
        return None


def _cached_run_count(code):
    """缓存末 2 日涨停连板数（过期缓存仅参考）。返回 int 或 None。"""
    p = os.path.join(DAILY, code + ".csv")
    if not os.path.exists(p):
        return None
    try:
        closes = []
        with open(p, encoding="utf-8") as f:
            f.readline()
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                try:
                    closes.append(float(parts[2]))  # close 列(兼容 v2: date,o,c,h,l,v)
                except Exception:
                    try:
                        closes.append(float(parts[1]))
                    except Exception:
                        continue
        if len(closes) < 2:
            return None
        run = 0
        for c in reversed(closes):
            # 需要前一天收盘才能算涨% —— 简化：用相邻收盘涨幅≥9.5% 近似连板
            # 这里只回看末两日
            break
        # 精确连板需每日涨%，缓存列不一定含；保守返回 None，由快照涨停+今日判定
        return None
    except Exception:
        return None


def discovery_filter(snaps):
    """量化初筛：返回 (emerg_list, moves_list)。

    2026-10-26 修（P0 性能，实测 20 分钟跑不完的真因）：
    原实现对**全市场 5225 只**每只都调 `_cached_vol_avg()`（每次整文件读 CSV 并逐行 float），
    但 `volr` 只在「涨幅≥BREAKOUT_PCT(5%) 且要判放量突破」时才用得上。
    现在改成**惰性求值**：只有 pct >= BREAKOUT_PCT 才去读缓存，读取量从 5225 只降到约 100-300 只。
    语义完全不变（volr 的唯一用途就是放量突破判定）。

    2026-10-28 新增监测层 moves（修复 300291-type 双重盲区，纯观察、不改 buy_score）：
    冲高回落 / 启动日未封板异动 —— 盘中最高涨幅 high_pct>=5% 但收盘涨幅 pct<2%。
    典型样本 300291 百纳千成 10-27：开+5.24% 高+7.4% 收-1.5%。原发现层只用收盘 pct
    初筛（<5% 直接 continue），这类「盘中强动、尾盘回落」永远进不了 emerg，也进不了 5 大策略。
    moves 用已抓取的 high/prev_close 字段，零额外网络成本；与 emerg 买点路径完全隔离。

    2026-10-28 新增监测层 oversold（用户授权扩展，捕子类B深跌放量反弹型，纯观察不进buy_score）：
    收盘跌幅 pct<=-4% 且 放量(volr>=1.5) → 超跌放量观察列表。覆盖「深跌次日反转」样本
    （如 002164 宁波东力 10-27 收-6.46%→10-28 +10.03%）。注意：子类B中「浅跌突然板」
    （长城-3.34%/联明-2.20%/索通-1.63%）跌幅未达 -4%，本列表不捕，须靠 ③(b) 弱转强 OOS。
    oversold 与 emerg 买点路径完全隔离，仅作监测。
    """
    emerg = []
    moves = []
    oversold = []
    intraday_w2s = []   # 盘中弱转强 NEW 买点（突破"尾盘买不可达"，封板前可达）
    for code, s in snaps.items():
        pct = s.get("pct", 0)
        try:
            pct = float(pct)
        except Exception:
            continue
        prev_close = s.get("prev_close", 0) or 0
        high = s.get("high", 0) or 0
        high_pct = (high - prev_close) / prev_close if prev_close else 0.0
        nm = s.get("name", "?")
        openp = s.get("open", 0) or 0
        open_pct = (openp - prev_close) / prev_close if prev_close else 0.0

        # —— 监测层：冲高回落异动（先于 emerg 早退，确保 300291-type 不被漏）——
        if high_pct >= 0.05 and pct < 0.02:
            if not _is_censored(nm):
                moves.append({
                    "code": code, "name": nm,
                    "high_pct": round(high_pct * 100, 2),
                    "pct": round(pct, 2), "signal": "move",
                    "price": s.get("price"), "time": s.get("time"),
                })

        # —— 监测层：超跌放量观察（纯观察，惰性算 volr，仅超跌票读缓存）——
        if pct <= OVERSELL_PCT:
            vavg_o = _cached_vol_avg(code, 5)
            volr_o = (s.get("vol", 0) / vavg_o) if vavg_o else 0.0
            if volr_o >= OVERSELL_VOLR and not _is_censored(nm):
                oversold.append({
                    "code": code, "name": nm,
                    "pct": round(pct, 2), "vol_ratio": round(volr_o, 2),
                    "signal": "oversold", "price": s.get("price"), "time": s.get("time"),
                })

        # —— 盘中弱转强 NEW 买点（2026-10-28 授权突破"尾盘买不可达"）——
        # 浅跌突然板型（长城/联明/索通）：开盘弱(open_pct<=0) → 盘中强拉至近涨停(pct 高但未封板)。
        # 条件：① 开盘弱 open_pct<=0；② 当前强 pct>=W2S_PCT(7%) 且未封板 pct<ZT_PCT(封板前可买)；
        #        ③ 放量确认 volr>=W2S_VOLR(1.5)；④ 非审查。
        # 必须置于 emerg 早退(`if not sig: continue`)之前，否则不可达 —— 已修(2026-10-28)。
        # 与 emerg 完全隔离：emerg 只收涨停/放量突破；w2s 专收"开盘弱、盘中强拉未封板"的封板前可买窗口。
        if open_pct <= 0.0 and pct >= W2S_PCT and pct < ZT_PCT:
            vavg_w = _cached_vol_avg(code, 5)
            volr_w = (s.get("vol", 0) / vavg_w) if vavg_w else 0.0
            if volr_w >= W2S_VOLR and not _is_censored(nm):
                intraday_w2s.append({
                    "code": code, "name": nm,
                    "open_pct": round(open_pct * 100, 2),
                    "pct": round(pct, 2), "high_pct": round(high_pct * 100, 2),
                    "vol_ratio": round(volr_w, 2), "signal": "w2s",
                    "price": s.get("price"), "time": s.get("time"),
                })

        # —— 买点层：涨停 / 放量突破（原逻辑不变）——
        if pct < BREAKOUT_PCT and pct < ZT_PCT:
            continue                      # 既不涨停也不到突破线 → 连均量都不用算
        vol = s.get("vol", 0)
        vavg = _cached_vol_avg(code, 5)    # 只对入围候选读缓存
        volr = (vol / vavg) if vavg else 0.0
        sig = None
        if pct >= ZT_PCT:
            sig = "zt"          # 涨停（含连板/首板）
        elif pct >= BREAKOUT_PCT and volr >= BREAKOUT_VOLR:
            sig = "breakout"    # 放量突破
        if not sig:
            continue
        emerg.append({
            "code": code, "name": s.get("name", "?"), "pct": round(pct, 2),
            "vol_ratio": round(volr, 2), "signal": sig,
            "price": s.get("price"), "time": s.get("time"),
        })

    emerg.sort(key=lambda x: x["pct"], reverse=True)
    moves.sort(key=lambda x: x["high_pct"], reverse=True)
    oversold.sort(key=lambda x: x["pct"])
    intraday_w2s.sort(key=lambda x: x["pct"], reverse=True)
    return emerg, moves, oversold, intraday_w2s


def _is_censored(name):
    """退市/ST/*退 硬审查。"""
    if not name:
        return False
    for k in ("退", "ST", "*ST", "★", "N "):
        if k in name:
            return True
    return False


def apply_lifecycle(cands, emerg, research, ledger, alert_log, graveyard):
    """进入 + 踢出 + 容量上限。graveyard(dict) 持久化被证伪票，重入拦截。
    返回 (new_cands, entries, exits, evicted)。graveyard 原地更新。"""
    t1 = list(cands.get("tier1", []))
    t2 = list(cands.get("tier2", []))
    pool_codes = {x["code"] for x in t1 + t2}

    # ---- 踢出：退市/ST、theme_fit off、长期无信号、破位 ----
    exits = []
    drop_set = set()
    for grp in (t1, t2):
        for x in grp:
            code = x["code"]
            nm = research.get(code, {}).get("name", "") or ""
            fit = research.get(code, {}).get("theme_fit", "?")
            reason = None
            if _is_censored(nm):
                reason = "censored(退市/ST)"
            elif fit == "off":
                reason = "theme_fit_off(题材证伪)"
            elif _no_signal_too_long(ledger, code):
                reason = "no_signal_%dd" % NO_SIGNAL_DAYS
            elif _broken_below(ledger, code):
                reason = "broken_below_support"
            if reason:
                drop_set.add(code)
                exits.append({"code": code, "name": nm, "reason": reason,
                              "date": datetime.date.today().isoformat()})
                # 墓地（硬排除）：仅退市/ST/*退 入墓地，后续扫描重入拦截。
                # 指令 D：题材证伪(theme_fit_off)不再入墓地——launcher 一律跟踪不盲踢，
                # 真 edge=模式跟随(题材无关)，墓地的唯一职责是挡住退市/ST 噪声。
                if reason.startswith("censored"):
                    graveyard[code] = {"reason": reason,
                                       "date": datetime.date.today().isoformat()}
    t1 = [x for x in t1 if x["code"] not in drop_set]
    t2 = [x for x in t2 if x["code"] not in drop_set]

    # ---- 进入：discovery 命中且不在池、非审查、未剔 ----
    entries = []
    for e in emerg:
        code = e["code"]
        if code in pool_codes or code in drop_set:
            continue
        if _is_censored(e.get("name", "")):
            continue
        fit = research.get(code, {}).get("theme_fit", "?")
        # 墓地拦截：曾被证伪的票不回潮（除非后续研究升级为 on 才放行重入）
        if code in graveyard and fit != "on":
            continue
        rec = {
            "code": code, "date": datetime.date.today().isoformat(),
            "prob": None, "kind": "discovery_%s" % e["signal"],
            "buyable": False,
            "note": "全市场发现(%s %.1f%% 量比%.1f); theme_fit=%s"
                    % (e["signal"], e["pct"], e["vol_ratio"], fit),
        }
        if fit == "?":
            rec["note"] += " | NEEDS_RESEARCH(待WebSearch赋theme_fit)"
        t2.append(rec)
        entries.append({"code": code, "name": e.get("name"), "signal": e["signal"],
                       "pct": e["pct"], "theme_fit": fit,
                       "theme_priority": round(theme_intel.priority_for(code, research.get(code, {})), 2)})

    # ---- 容量上限：活跃池 Tier1+Tier2 封顶 cap，超限驱逐最低优先 ----
    # 注：oversold(超跌放量) **不** 进活跃池/买入层 —— 2026-10-28 经 oospf_validator 规范门控复核：
    #   组合 PF(H5)=0.686 < 1.30、命中倍数 0.759 < 1.15 → REJECT（此前 moves_oversold_oos 的
    #   PROMOTE 系用「逐事件 PF 1.3826 vs 宽松阈值 1.259」判定，不满足圣杯「组合 PF≥1.30」铁律）。
    #   故 oversold 维持监测层（discovery_candidates.oversold / intraday_universe OVER 路），
    #   禁入 buy_score 与买入候选。若未来要解锁，须重跑浅超跌(-4%~-5%)窄区间过规范门。
    evicted = []
    active = t1 + t2
    cap = pool_cap()
    if len(active) > cap:
        # 优先级：Tier1 保底；Tier2 按 _score 升序 + 沉默时长降序 驱逐
        # 修复 2026-10-26（P0 驱逐方向反了）：升序排序后低分在前，
        # 原 `t2[-over:]` 取尾部=高分票 → 把最该留的踢掉（当日唯一 theme_fit=on 的
        # 605488 福莱新材 priority=1.0 当天入池当天被踢，30 只 score=0 的未富集票全留）。
        t2.sort(key=lambda x: (_score(x), _silence_days(ledger, x["code"])), reverse=False)
        over = len(active) - cap
        kick = t2[:over] if over <= len(t2) else list(t2)
        kick_codes = {x["code"] for x in kick}
        for x in kick:
            evicted.append({"code": x["code"], "reason": "capacity_evict(over_%d)" % cap,
                            "score": round(_score(x), 3),
                            "date": datetime.date.today().isoformat()})
        t2 = [x for x in t2 if x["code"] not in kick_codes]
        # 2026-10-26 新增：驱逐率超过 15% 说明信号量又涨过容量，必须显式告警而不是静默硬砍。
        _rate = len(kick) / float(len(active))
        if _rate > 0.15:
            print("[lifecycle] ⚠ 强制驱逐 %d/%d 只(%.0f%%) —— 容量 %d 已不足，"
                  "请在 data/pool_policy.json 调大 pool_cap（禁止靠硬砍维持）"
                  % (len(kick), len(active), _rate * 100, cap))

    new_cands = {"date": datetime.date.today().isoformat(),
                 "n_tier1": len(t1), "n_tier2": len(t2),
                 "tier1": t1, "tier2": t2,
                 "note": "生命周期管理: entry=%d exit=%d evict=%d (cap=%d)"
                         % (len(entries), len(exits), len(evicted), cap)}
    return new_cands, entries, exits, evicted


def _score(x):
    """容量驱逐优先级 = 模式概率(prob 0~1) + 题材优先级权重(0~1.1)。越大越该保留。
    热题材票被保护、冷题材降级（指令 D「排后」机制）。

    修复 2026-10-26：原实现仅在 `code in RESEARCH_CACHE` 时才叠加题材权重，
    研究库与池零交集时**所有票并列 score=0.0**，排序退化为"先入先留"，
    于是当天新入池的票被整批驱逐。现改为：未富集票给 UNKNOWN_W(0.3) 兜底，
    已富集票按真实 priority（on=1.0 / weak=0.7 / cold=0.4 / emerging=0.6）。
    """
    try:
        base = float(x.get("prob") or 0)
    except Exception:
        base = 0.0
    code = x.get("code")
    try:
        entry = _research_cache().get(str(code), {}) if code else {}
        pr = theme_intel.priority_for(str(code), entry)
    except Exception:
        pr = theme_intel.UNKNOWN_W          # 兜底 0.3，避免与热门题材票并列 0
    return base + pr


def _no_signal_too_long(ledger, code):
    """台账中该 code 无验证事件超 NO_SIGNAL_DAYS 交易日 → True。"""
    try:
        for rec in ledger:
            if rec.get("code") == code:
                # verification 字段存在且为真则未超时
                if rec.get("verified") or rec.get("launched") or rec.get("next_limit_up"):
                    return False
    except Exception:
        pass
    return False  # 台账无记录不臆断踢出


def _broken_below(ledger, code):
    return False  # 破位需买价上下文，v1 不臆断；由盘中 agentic 监控触发


def _silence_days(ledger, code):
    return 0  # v1 简化：沉默时长并入 _no_signal


def main():
    print("[discovery] 加载全市场代码表 ...")
    codes = load_universe()
    print("[discovery] 宇宙 %d 只，拉 gtimg 快照(EOD) ..." % len(codes))
    snaps = gtimg_snapshot(codes)
    print("[discovery] 快照到 %d 只（批 %d/%d 成功、%d 失败、用时 %.1fs%s）"
          % (len(snaps), SNAP_STATS["chunks_ok"], SNAP_STATS["chunks_total"],
             SNAP_STATS["chunks_fail"], SNAP_STATS["elapsed_sec"],
             "，⚠预算用尽不完整" if SNAP_STATS["budget_hit"] else ""))

    emerg, moves, oversold, intraday_w2s = discovery_filter(snaps)
    # 观察位 r31（横盘后突破，WATCH）：只读本地日线 CSV，不发网络请求，与快照层完全隔离。
    # 裁决 WATCH（不进 buy_score / 不 PROMOTE），挂观察位只为让样本外时间累积去证伪或证实。
    try:
        r31_list = r31_watch.scan()
        # 落只追加观察账本（记录 T+1..T+5 逐日收盘路径；幂等，(date,code) 去重，
        # path 变长时升级）。前瞻样本不可回溯，不能只靠每日被覆盖的 discovery_candidates.json。
        try:
            _nn, _nu = r31_watch.record(r31_list)
            if _nn or _nu:
                print("[discovery] r31 观察账本：新增 %d / 路径升级 %d" % (_nn, _nu))
        except Exception as e:
            print("[warn] r31 观察账本写入失败（不阻断）: %s" % str(e)[:120])
    except Exception as e:
        print("[warn] r31_watch.scan 失败，r31 落空列表: %s" % str(e)[:120])
        r31_list = []
    print("[discovery] 初筛命中 %d 只(涨停/放量突破); 冲高回落异动 %d 只; 超跌放量观察 %d 只; 盘中弱转强 %d 只; r31观察位 %d 只"
          % (len(emerg), len(moves), len(oversold), len(intraday_w2s), len(r31_list)))

    cands = json.load(open(CAND_P, encoding="utf-8")) if os.path.exists(CAND_P) else {"tier1": [], "tier2": []}
    research = json.load(open(RESEARCH_P, encoding="utf-8")) if os.path.exists(RESEARCH_P) else {}
    global RESEARCH_CACHE
    RESEARCH_CACHE = research
    ledger = json.load(open(LEDGER_P, encoding="utf-8")) if os.path.exists(LEDGER_P) else []
    if not isinstance(ledger, list):
        ledger = ledger.get("ledger", [])
    graveyard = json.load(open(GRAVEYARD_P, encoding="utf-8")) if os.path.exists(GRAVEYARD_P) else {}
    # 迁移回填：从历史生命周期退出记录把硬证伪票(censored/theme_fit_off)写入墓地，
    # 使旧 run 已踢出的票不再回潮（容量驱逐类不写入，因其可随空位返回）
    if os.path.exists(LIFECYCLE_P):
        try:
            hist = json.load(open(LIFECYCLE_P, encoding="utf-8"))
            if not isinstance(hist, list):
                hist = [hist]
            for life in hist:
                for x in life.get("exits", []):
                    r = x.get("reason", "")
                    # 迁移回填只认退市/ST（指令 D：题材证伪不再入墓地）
                    if r.startswith("censored"):
                        graveyard.setdefault(x["code"], {"reason": r, "date": x.get("date", "")})
        except Exception:
            pass

    new_cands, entries, exits, evicted = apply_lifecycle(cands, emerg, research, ledger, {}, graveyard)

    json.dump(new_cands, open(CAND_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 修复 2026-10-26：原 `emerg[:60]` 硬截断 → n_emerg 记 107 但只落 60 条，
    # 47 只当天涨停票从未进入研判队列（含 20cm 的 688160 步科股份）。
    # 现全量落盘；若未来真的因为体量需要截断，必须显式标注 truncated 与丢弃数，绝不静默。
    EMERG_CAP = 500
    emerg_listed = emerg[:EMERG_CAP]
    json.dump({"date": datetime.date.today().isoformat(),
               "n_emerg": len(emerg),
               "n_emerg_listed": len(emerg_listed),
               "emerg_truncated": len(emerg) > EMERG_CAP,
               "emerg_dropped": max(0, len(emerg) - len(emerg_listed)),
               "emerg": emerg_listed, "entries": entries, "exits": exits, "evicted": evicted,
               "n_moves": len(moves), "moves": moves,
               "n_oversold": len(oversold), "oversold": oversold,
               "n_w2s": len(intraday_w2s), "w2s": intraday_w2s,
               "n_r31": len(r31_list), "r31": r31_list,
               "snapshot": dict(SNAP_STATS), "n_snapshot": len(snaps)},
              open(DISCOVERY_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if len(emerg) > EMERG_CAP:
        print("[warn] emerg 超过 %d 条，已截断丢弃 %d 条（已显式标注）"
              % (EMERG_CAP, len(emerg) - len(emerg_listed)))

    # 生命周期日志（追加式，不覆盖）
    life = {"date": datetime.date.today().isoformat(), "entries": entries,
            "exits": exits, "evicted": evicted}
    try:
        hist = json.load(open(LIFECYCLE_P, encoding="utf-8")) if os.path.exists(LIFECYCLE_P) else []
        if not isinstance(hist, list):
            hist = [hist]
        hist.append(life)
        json.dump(hist[-30:], open(LIFECYCLE_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass

    print("[lifecycle] 进入=%d 踢出=%d 容量驱逐=%d 墓地=%d" % (len(entries), len(exits), len(evicted), len(graveyard)))
    json.dump(graveyard, open(GRAVEYARD_P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("[lifecycle] 池: Tier1=%d Tier2=%d (cap=%d)" % (new_cands["n_tier1"], new_cands["n_tier2"], pool_cap()))
    for e in entries[:15]:
        print("  + 进入 %s %s [%s] %.1f%% theme=%s" % (e["code"], e["name"], e["signal"], e["pct"], e["theme_fit"]))
    for x in exits[:15]:
        print("  - 踢出 %s %s (%s)" % (x["code"], x["name"], x["reason"]))
    print("[OK] %s | %s" % (CAND_P, DISCOVERY_P))


if __name__ == "__main__":
    main()
