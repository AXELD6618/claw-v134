# -*- coding: utf-8 -*-
"""
research/grail_hit_forward_oos.py — grail_hit SUPPLEMENTARY 前向 OOS 累积闭环
============================================================================
承接 RUNBOOK §18.8 / gate doc §18.13 的"生产晋级须 forward_oos 真实样本累积>=200 + live 验证"。
把已验证(历史 OOS PROMOTE_GRAIL)的 CAT_PROXIMITY 边接入"实推 → 标注 → 累积 → 复核"前向闭环：

  emit(record)       : 每次 T5 实推 emit 一条 grail_hit 候选 → 追加到前向台账(append-only)。
                       仅当 T_date == 今日(真实 live 发射)才记录；历史/测试重跑不污染台账。
  label_record(rec)  : 从 backtest_cache/daily 取 T+1/T+2/T+3 实际结果标注(grail_hit 全口径)：
                        - T+1 涨停事件 (交易所口径: close>=round(prev*(1+lp),2)-0.005)
                        - T+2 涨 / T+3 涨 (后续>=2日上涨, 圣杯全口径)
  evaluate()         : 累积已标注 n>=200 后, 用 adjudicate_grail 同门口径(lift>=1.5 / 超额vs市>0&t>=2 /
                       事件率二项z>=2.0 / PCV>=0.60 / n>=200) 复核 LIVE 前向表现, 绝不降门。
  simulate_forward() : 历史回放验证(不污染真实台账)——证明标注+复核管线正确, 展示"若当日实推, 前向结果如何"。

涨停口径(与全系统一致)：close >= round(prev*(1+lp),2) - 0.005；lp 按板块：
  科创板 688 / 创业板 300·301 → 0.20；北交所 8·43·83·87 → 0.30；ST → 0.05；主板其余 → 0.10。

纪律(不变)：
  - 仅记录与标注, 不修改任何闸门/目标/GOALS sha256/n_factors=0/mining_frozen/cap_override；
  - 复核闸门一字未改(lift>=1.5 / 超额>0 / t>=2 / 二项z>=2.0 / PCV>=0.60 / n>=200)；
  - 台账 append-only, 可审计；晋升判定仅输出建议(SUPPLEMENTARY_LIVE_CONFIRMED / REVIEW / INSUFFICIENT)，
    是否晋级 buy_score/core 由独立流程决定, 本模块不做。
"""
from __future__ import annotations
import os, json, glob, math, datetime as dt
from pathlib import Path
import numpy as np
import oospf_validator as V

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = Path(os.path.dirname(HERE))
CACHE = V.CACHE
DAILY = V.DAILY
LIMIT = V.LIMIT  # 0.095 涨停代理(用于"可买"判定: |ret_T|<LIMIT)
LEDGER_PATH = CACHE / "grail_hit_forward_ledger.jsonl"
BASELINE_PATH = CACHE / "grail_hit_forward_baseline.json"

# 复核闸门(与 oos_eval_harness.adjudicate_grail 同门口径, 一字未改)
GATE = {"lift": 1.5, "excess": 0.0, "t_z": 2.0, "z_event": 2.0, "pcv": 0.60, "n": 200}


# --------------------------------------------------------------------------- #
# 板块涨停代理 lp
# --------------------------------------------------------------------------- #
def _lp(code: str) -> float:
    c = str(code)
    if c.startswith("688"):
        return 0.20                     # 科创板
    if c.startswith("300") or c.startswith("301"):
        return 0.20                     # 创业板
    if c.startswith("8") or c.startswith("43") or c.startswith("83") or c.startswith("87"):
        return 0.30                     # 北交所 / 老三板
    return 0.10                         # 主板(ST 未含名称, 0.10 偏保守, 不影响主样本)


def is_limit_up(code: str, prev_close: float, close: float) -> bool:
    if prev_close is None or close is None or prev_close <= 0:
        return False
    lp = _lp(code)
    lim = round(prev_close * (1 + lp), 2) - 0.005
    return close >= lim


# --------------------------------------------------------------------------- #
# 台账读写
# --------------------------------------------------------------------------- #
def _load_ledger(path=LEDGER_PATH):
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def emit(symbol, T_date, category, expected_lift, verdict_ref="PROMOTE_GRAIL",
         force=False, path=None):
    """记录一次真实实推发射。仅当 T_date==今日(真实 live)才写(除非 force)。

    返回 True(已记录) / False(跳过, 非 live)。
    path=None 时取模块全局 LEDGER_PATH(调用时解析, 支持运行时重定向/测试)。
    """
    if path is None:
        path = LEDGER_PATH
    today = int(dt.date.today().strftime("%Y%m%d"))
    if (not force) and int(T_date) != today:
        return False  # 历史/测试重跑不污染真实台账
    # 幂等去重：同一 (symbol, T_date) 仅记录一次，避免同交易日重跑 execute() 重复 append
    # 虚增 n、污染 n>=200 晋升门与 evaluate 复核口径（运行时严谨性，零闸门/目标语义改动）。
    _key = (str(symbol).zfill(6), int(T_date))
    try:
        _seen = {(str(r.get("symbol")).zfill(6), int(r.get("T_date")))
                 for r in _load_ledger(path) if r.get("T_date") is not None}
    except Exception:
        _seen = set()
    if _key in _seen:
        return False  # 已记录，跳过（幂等）
    rec = {
        "symbol": str(symbol).zfill(6),
        "T_date": int(T_date),
        "category": category,
        "expected_lift": expected_lift,
        "verdict_ref": verdict_ref,
        "emitted_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": "EMITTED",
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def _read_daily(code, require_forward=3):
    """统一用 pandas 读取日线并规范化: 日期→int(YYYYMMDD), 收盘/开盘→float。

    返回 (path, df) 或 None(文件不存在 / 列不可用 / 长度不足 require_forward+1)。
    df 列: 日期(int), 开盘(float), 收盘(float), 最高(float), 最低(float), 成交量(float)。
    健壮处理: 缺表头 / 中文或英文列名 / 日期含 '-' 或 '/' / 非数值收盘价。
    """
    p = DAILY / f"{str(code).zfill(6)}.csv"
    if not p.exists():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(p)
    except Exception:
        return None
    cols = list(df.columns)
    # 兼容缺表头: 用标准中文列名按位置补齐
    if "日期" not in cols:
        std = ["日期", "开盘", "收盘", "最高", "最低", "成交量"]
        rename = {cols[i]: std[i] for i in range(min(len(cols), len(std)))}
        df = df.rename(columns=rename)
    try:
        df["日期"] = df["日期"].astype(str).str.replace("-", "").str.replace("/", "").astype(int)
        df["收盘"] = pd.to_numeric(df["收盘"], errors="coerce")
        df["开盘"] = pd.to_numeric(df["开盘"], errors="coerce")
    except Exception:
        return None
    df = df.dropna(subset=["日期", "收盘"])
    if len(df) < (require_forward + 1):
        return None
    df = df.sort_values("日期").reset_index(drop=True)
    return p, df


def label_record(rec, max_lookforward=3):
    """给定 EMITTED 记录, 从日线取 T+1..T+max_lookforward 实际结果标注。

    返回标注后的 rec(新增 ret_T1/is_zt_T1/ret_T2/is_up_T2/ret_T3/is_up_T3/grail_full/label_status)。
    若日线缺失或 T+3 不可用 → label_status='PENDING'(尚未可标注)。
    """
    code = rec["symbol"]
    T_date = int(rec["T_date"])
    r = _read_daily(code, require_forward=max_lookforward)
    if r is None:
        rec = dict(rec); rec["label_status"] = "NO_DATA"; return rec
    _, df = r
    dates = df["日期"].to_numpy()
    close = df["收盘"].to_numpy(dtype=float)
    idx = np.where(dates == T_date)[0]
    if idx.size == 0:
        rec = dict(rec); rec["label_status"] = "T_NOT_FOUND"; return rec
    ti = int(idx[-1])
    n = len(close)
    if ti + max_lookforward >= n:
        rec = dict(rec); rec["label_status"] = "PENDING"; return rec  # T+3 尚未发生
    prev = close[ti]
    r1 = close[ti + 1] / prev - 1.0 if prev > 0 else float("nan")
    r2 = close[ti + 2] / close[ti + 1] - 1.0 if close[ti + 1] > 0 else float("nan")
    r3 = close[ti + 3] / close[ti + 2] - 1.0 if close[ti + 2] > 0 else float("nan")
    zt1 = is_limit_up(code, prev, close[ti + 1])
    up2 = (r2 == r2) and (r2 > 0)
    up3 = (r3 == r3) and (r3 > 0)
    rec = dict(rec)
    rec["ret_T1"] = None if r1 != r1 else round(float(r1), 6)
    rec["is_zt_T1"] = bool(zt1)
    rec["ret_T2"] = None if r2 != r2 else round(float(r2), 6)
    rec["is_up_T2"] = bool(up2)
    rec["ret_T3"] = None if r3 != r3 else round(float(r3), 6)
    rec["is_up_T3"] = bool(up3)
    rec["grail_full"] = bool(zt1 and up2 and up3)
    rec["label_status"] = "LABELED"
    rec["status"] = "LABELED"
    return rec


def label_all(path=LEDGER_PATH, max_lookforward=3):
    """标注台账中所有尚未标注(LABELED/终态)的记录; 原地写回 path(append-only 精神: 仅补 label 字段)。"""
    recs = _load_ledger(path)
    out = []
    changed = False
    for rec in recs:
        st = rec.get("label_status")
        if st in ("LABELED", "NO_DATA", "PARSE_ERR", "T_NOT_FOUND"):
            out.append(rec); continue
        lab = label_record(rec, max_lookforward=max_lookforward)
        out.append(lab)
        if lab.get("label_status") == "LABELED":
            changed = True
    # 写回(保留全部字段, 仅更新 label)
    with open(path, "w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out, changed


# --------------------------------------------------------------------------- #
# 基准(可买宇宙次日涨停率 + 次日均值收益) —— 懒计算 + 缓存
# --------------------------------------------------------------------------- #
def compute_baseline(force=False):
    if (not force) and os.path.exists(BASELINE_PATH):
        try:
            return json.loads(Path(BASELINE_PATH).read_text(encoding="utf-8"))
        except Exception:
            pass
    files = sorted(glob.glob(str(DAILY / "*.csv")))
    zt = 0
    buy_n = 0
    rets = []
    for fp in files:
        code = os.path.basename(fp)[:-4]
        r = _read_daily(code, require_forward=1)
        if r is None:
            continue
        _, df = r
        close = df["收盘"].to_numpy(dtype=float)
        n = len(close)
        if n < 5:
            continue
        ret = np.full(n, np.nan); ret[1:] = close[1:] / close[:-1] - 1.0
        for i in range(1, n - 1):
            if abs(ret[i]) >= LIMIT:
                continue  # T 已涨停/已跌停 → 非可买
            buy_n += 1
            if is_limit_up(code, close[i], close[i + 1]):
                zt += 1
            rets.append(ret[i + 1])
    base_zt = (zt / buy_n) if buy_n else float("nan")
    base_ret = float(np.mean(rets)) if rets else float("nan")
    base_std = float(np.std(rets)) if rets else float("nan")
    base = {"base_next_day_limitup_rate": base_zt, "base_next_day_mean_ret": base_ret,
            "base_next_day_std": base_std, "buyable_n": buy_n, "computed_at":
            dt.datetime.now().isoformat(timespec="seconds")}
    try:
        Path(BASELINE_PATH).write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return base


# --------------------------------------------------------------------------- #
# 复盘评估(与 adjudicate_grail 同门口径)
# --------------------------------------------------------------------------- #
def evaluate(path=LEDGER_PATH, force_baseline=False):
    recs = _load_ledger(path)
    lab = [r for r in recs if r.get("label_status") == "LABELED"]
    n = len(lab)
    base = compute_baseline(force=force_baseline)
    bz = base.get("base_next_day_limitup_rate")
    bm = base.get("base_next_day_mean_ret")
    bs = base.get("base_next_day_std")
    if n < GATE["n"] or bz is None or bm is None:
        return {
            "verdict": "INSUFFICIENT",
            "n_labeled": n, "n_required": GATE["n"],
            "base_next_day_limitup_rate": bz,
            "note": "标注样本不足 n>=200, 暂缓晋升判定(闸门未开)",
        }
    sig_zt = sum(1 for r in lab if r.get("is_zt_T1")) / n
    sig_ret = np.nanmean([r["ret_T1"] for r in lab if r.get("ret_T1") is not None])
    sig_std = np.nanstd([r["ret_T1"] for r in lab if r.get("ret_T1") is not None])
    sig_grail_full = sum(1 for r in lab if r.get("grail_full")) / n
    lift = sig_zt / bz if bz else float("nan")
    excess = sig_ret - bm
    se = math.sqrt(bz * (1 - bz) / n) if bz is not None else float("nan")
    z_event = ((sig_zt - bz) / se) if se else float("nan")
    t_excess = ((sig_ret - bm) / math.sqrt((bs ** 2 + sig_std ** 2) / n)) if (bs and n) else float("nan")
    # PCV(时序稳定性代理): 前后半样本 lift 一致性
    half = n // 2
    early = lab[:half]; late = lab[half:]
    def _zt_rate(sub):
        if not sub:
            return float("nan")
        return sum(1 for r in sub if r.get("is_zt_T1")) / len(sub)
    lift_e = _zt_rate(early) / bz if bz else float("nan")
    lift_l = _zt_rate(late) / bz if bz else float("nan")
    pcv = (min(lift_e, lift_l) / lift) if (lift and lift > 0) else 0.0
    pcv = max(0.0, min(1.0, pcv))
    checks = {
        "lift>=1.5": lift >= GATE["lift"],
        "excess>0": excess > GATE["excess"],
        "t_excess>=2": (t_excess == t_excess) and (t_excess >= GATE["t_z"]),
        "z_event>=2": (z_event == z_event) and (z_event >= GATE["z_event"]),
        "PCV>=0.60": pcv >= GATE["pcv"],
        "n>=200": n >= GATE["n"],
    }
    verdict = "SUPPLEMENTARY_LIVE_CONFIRMED" if all(checks.values()) else "REVIEW"
    return {
        "verdict": verdict,
        "n_labeled": n,
        "signal_next_day_limitup_rate": round(sig_zt, 5),
        "next_day_lift": round(lift, 4),
        "signal_next_day_mean_ret": round(sig_ret, 6),
        "excess_vs_market": round(excess, 6),
        "t_excess": round(t_excess, 4) if t_excess == t_excess else None,
        "event_binomial_z": round(z_event, 4) if z_event == z_event else None,
        "grail_full_rate": round(sig_grail_full, 5),
        "pcv": round(pcv, 4),
        "base_next_day_limitup_rate": round(bz, 5) if bz == bz else None,
        "checks": checks,
    }


# --------------------------------------------------------------------------- #
# 历史回放验证(不污染真实台账): 证明标注+复核管线正确
# --------------------------------------------------------------------------- #
def simulate_forward(as_of_pivot, K=10, top_n=20, max_files=None, write_sim=None):
    """回放: 在 as_of_pivot 用 grail_hit_selector 取当日候选(视为当日实推),
    用真实后续日线标注 T+1/T+2/T+3, 再跑 evaluate, 证明闭环管线正确。
    结果写入 write_sim(否则仅返回), 不触碰真实台账。"""
    from research.grail_hit_selector import select_candidates
    res = select_candidates(as_of=as_of_pivot, K=K, top_n=top_n, max_files=max_files, require_boost=True)
    recs = []
    for c in res.get("top", []):
        rec = {
            "symbol": str(c.get("symbol")).zfill(6), "T_date": int(c.get("T_date")),
            "category": c.get("category"), "expected_lift": c.get("expected_lift"),
            "verdict_ref": "PROMOTE_GRAIL", "emitted_at": "SIM", "status": "EMITTED",
        }
        recs.append(label_record(rec, max_lookforward=3))
    labeled = [r for r in recs if r.get("label_status") == "LABELED"]
    # 临时台账评估
    tmp = write_sim or (str(CACHE / "_grail_hit_sim_ledger.jsonl"))
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ev = evaluate(path=tmp, force_baseline=False)
    return {"pivot": as_of_pivot, "n_candidates": len(res.get("top", [])),
            "n_labeled": len(labeled), "eval": ev}


def report(path=LEDGER_PATH):
    recs = _load_ledger(path)
    emitted = sum(1 for r in recs if r.get("status") == "EMITTED")
    labeled = [r for r in recs if r.get("label_status") == "LABELED"]
    print("=" * 60)
    print("  grail_hit 前向 OOS 累积台账")
    print("=" * 60)
    print(f"  已 emit: {emitted}  | 已标注: {len(labeled)}  | 台账总行: {len(recs)}")
    ev = evaluate(path=path)
    print("  晋升复核:", json.dumps(ev, ensure_ascii=False, default=str)[:800])
    return ev


SUPPLEMENTARY_MODEL_FILE = ROOT / "grail_hit_supplementary_model.json"


def apply_live_confirmation(model_path=SUPPLEMENTARY_MODEL_FILE, path=LEDGER_PATH,
                            force_baseline=False):
    """闭环末端反馈（运行时自提升关键一环）：若 n>=200 且 SUPPLEMENTARY_LIVE_CONFIRMED，
    将活体验证结果写回验证边 manifest（live_confirmed + live_metrics），使系统自我强化——
    引擎/推送据此提升该 SUPPLEMENTARY 边的运行时置信度与可解释性。
    纪律：绝不改动闸门/目标/core_factor_model；仅补充 append-only 活体验证字段；
    未达晋升(n<200 或其它)则一律不写回（保持诚实，不伪造确认）。"""
    ev = evaluate(path=path, force_baseline=force_baseline)
    if ev.get("verdict") != "SUPPLEMENTARY_LIVE_CONFIRMED":
        return ev  # 未达晋升：不写回
    try:
        m = json.loads(Path(model_path).read_text(encoding="utf-8"))
        m["live_confirmed"] = True
        m["live_confirmed_at"] = dt.datetime.now().isoformat(timespec="seconds")
        m["live_metrics"] = {k: ev.get(k) for k in (
            "n_labeled", "signal_next_day_limitup_rate", "next_day_lift",
            "signal_next_day_mean_ret", "excess_vs_market", "t_excess",
            "event_binomial_z", "grail_full_rate", "pcv")}
        Path(model_path).write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[apply_live_confirmation] ✅ 写回 live_confirmed + live_metrics → {model_path}")
    except Exception as e:
        print(f"[apply_live_confirmation] 写回失败(不影响评估): {e}")
    return ev


def simulate_resume(K=10, top_n=None, write_sim=None, strong_only=False):
    """resume 子集前向验证（快进代理，不污染真实台账）：
    对静态披露快照(catalyst_notices.jsonl)覆盖的历史区间，逐个 disclosure 取其落地后 7 日窗口内的
    每个交易日 pivot（D∈[p-7,p]，且 D<=p 不泄未来），把"T日可买(非涨停/非跌停) + resume 披露"的
    候选当日实推并标注 T+1/T+2/T+3 真实结果，聚合后用 evaluate() 同门口径复核。

    为什么能快进： live 路径靠每日 emit→nightly label 累积到 n>=200 才评估；本函数在静态快照上把
    历史所有 resume 候选一次性聚合，立即给出"resume 子集是否达门"的首次读数。两者口径完全一致
    （同 select_candidates 的 resume 窗口 / 同 label_record / 同 evaluate 闸门），故可直接类比。
    与 runup 子集的区别：runup 需未来披露(D>p)无法诚实回测；resume 仅用已发生披露(D<=p)，无前视。

    性能：仅预载 feed 提及的 ~108 只标的日线（而非 5225 全扫），避免每日全市场扫描。
    纪律：仅聚合模拟台账评估，绝不写回真实台账 / 验证边 manifest / core_factor_model。
    """
    from research.catalyst_supplementary import _load_notices
    from research import grail_hit_selector as GHS
    VALID = set(GHS.CAT_PRIORITY)
    notices = _load_notices()
    by_code = {}
    for code, evs in notices.items():
        lst = [(int(d), c) for (d, c) in evs if c in VALID]
        if lst:
            by_code[code] = lst
    daily_cache = {}
    for code in by_code:
        r = _read_daily(code, require_forward=3)
        if r:
            daily_cache[code] = r[1]
    recs = []
    for code, df in daily_cache.items():
        dates = df["日期"].to_numpy()
        close = df["收盘"].to_numpy(dtype=float)
        n = len(close)
        dpos = {int(d): i for i, d in enumerate(dates)}
        # pivot -> 最近披露(D,cat)，避免同窗口多披露重复计数
        pmap = {}
        for (D, cat) in by_code[code]:
            for p in range(D - 7, D + 1):
                ti = dpos.get(p)
                if ti is None or ti < 1 or ti + 3 >= n:
                    continue
                ad = abs(p - D)
                if p not in pmap or ad < pmap[p][0]:
                    pmap[p] = (ad, D, cat)
        for p, (ad, D, cat) in pmap.items():
            ti = dpos[p]
            prev = close[ti]
            if prev <= 0:
                continue
            rT = close[ti] / prev - 1.0
            if abs(rT) >= LIMIT:          # T 涨停/跌停 → 非可买
                continue
            r1 = close[ti + 1] / prev - 1.0
            r2 = close[ti + 2] / close[ti + 1] - 1.0 if close[ti + 1] > 0 else float("nan")
            r3 = close[ti + 3] / close[ti + 2] - 1.0 if close[ti + 2] > 0 else float("nan")
            zt1 = is_limit_up(code, prev, close[ti + 1])
            up2 = (r2 == r2) and (r2 > 0)
            up3 = (r3 == r3) and (r3 > 0)
            grail_full = bool(zt1 and up2 and up3)
            rec = dict(symbol=code.zfill(6), T_date=p, category=cat,
                       expected_lift=None, verdict_ref="PROMOTE_GRAIL",
                       emitted_at="SIM_RESUME", status="EMITTED",
                       ret_T1=None if r1 != r1 else round(float(r1), 6),
                       is_zt_T1=bool(zt1),
                       ret_T2=None if r2 != r2 else round(float(r2), 6),
                       is_up_T2=bool(up2),
                       ret_T3=None if r3 != r3 else round(float(r3), 6),
                       is_up_T3=bool(up3),
                       grail_full=grail_full, label_status="LABELED")
            recs.append(rec)
    # === 龙头/追高 讲座缺口验证: 仅保留"强势"(close≥MA20 且 20日收益≥0) 候选 ===
    # 弱势名(close<MA20 或 20日收益<0) 是 resume 边缘 PCV 失败的主因(诊断 lift 3.67×, z~2)。
    # 该过滤属 SUPPLEMENTARY 候选质量改进(非 buy_score)，不降门；n 过小(<200)仅作 PCV 方向读数。
    if strong_only:
        from research.grail_leader_strength import _load_daily, _ma20_strength
        kept = []
        for r in recs:
            d = _load_daily(r["symbol"])
            if d is None:
                continue
            res_ = _ma20_strength(d[0], d[1], r["T_date"])
            if res_ is None:
                continue
            _, _, _, is_weak, _ = res_
            if not is_weak:
                kept.append(r)
        recs = kept
    if strong_only:
        tmp = write_sim or str(CACHE / "_grail_hit_resume_sim_ledger_strong.jsonl")
    else:
        tmp = write_sim or str(CACHE / "_grail_hit_resume_sim_ledger.jsonl")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ev = evaluate(path=tmp, force_baseline=False)
    # 附加 resume 专属统计（与 baseline 对照）
    lab = [r for r in recs if r.get("label_status") == "LABELED"]
    base = compute_baseline(force=False)
    n = len(lab)
    up2_rate = (sum(1 for r in lab if r.get("is_up_T2")) / n) if n else float("nan")
    res = {"n_candidates": n, "resume_T2_up_rate": round(up2_rate, 5),
           "baseline_T1_limitup_rate": base.get("base_next_day_limitup_rate"),
           "eval": ev}
    return res


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit", nargs=4, metavar=("SYM", "T", "CAT", "LIFT"), help="记录一次实推发射")
    ap.add_argument("--label-all", action="store_true")
    ap.add_argument("--evaluate", action="store_true")
    ap.add_argument("--simulate", type=int, help="历史回放验证 pivot 日期 YYYYMMDD")
    ap.add_argument("--simulate-resume", action="store_true",
                   help="resume 子集前向验证(快进代理): 聚合静态披露快照历史区间全部 resume 候选并评估达门")
    ap.add_argument("--simulate-resume-strong", action="store_true",
                   help="同 --simulate-resume 但仅保留'强势'(close≥MA20 且 20日收益≥0) 候选(龙头/追高 讲座缺口验证)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--apply-confirm", action="store_true",
                   help="若 n>=200 且 SUPPLEMENTARY_LIVE_CONFIRMED, 写回验证边 manifest(live_confirmed)")
    a = ap.parse_args()
    if a.emit:
        ok = emit(a.emit[0], a.emit[1], a.emit[2], float(a.emit[3]), force=True)
        print("emit recorded:", ok)
    if a.label_all:
        _, ch = label_all()
        print("label_all done, changed:", ch)
    if a.simulate:
        r = simulate_forward(a.simulate)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if a.simulate_resume:
        r = simulate_resume()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if a.simulate_resume_strong:
        r = simulate_resume(strong_only=True)
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if a.report or a.evaluate:
        report()
    if a.apply_confirm:
        # 闭环末端运行时自提升：仅当 n>=200 且 SUPPLEMENTARY_LIVE_CONFIRMED 才写回
        # 验证边 manifest(live_confirmed + live_metrics)；未达晋升一律不写回(诚实)。
        ev = apply_live_confirmation()
        print("apply_live_confirmation verdict:", ev.get("verdict"))
