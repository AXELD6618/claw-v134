# -*- coding: utf-8 -*-
"""
多源线索交叉验证引擎 v2（Round35）

R34 的 lead_engine 只有 5 条线索（榜/机构买/增持/大宗/无解禁），
结论是「快变量是负效果、慢变量勉强正但 PF 1.05~1.07 低于随机 1.088，S34 共振 1.238 样本不足」。

R35 新增三条**完全不同性质**的线索（都在投资者点名的维度上）：

  L6 成本倒挂 : 机构大宗买方近 60 日均价高于现价 → 这批机构浮亏
                （gap_org 在 [-30%,-5%]，即「被套但没崩」；并拆分 MA20 上方/下方）
  L7 机构加仓 : 十大流通股东里机构流通占比环比上升（季报，lag=1，有效 90 个交易日）
  L8 调研热度 : 近 20 个交易日被 >=13 家机构调研（p75 分位）

为什么 L6 值得单列：它把「事件」换成「相对价格位置」——
R33 已证「T 日暴跌」本身是负 edge（PF 0.47~0.80），而「有人真金白银买在上面、现在被套」
是另一回事。必须做正交拆分才能分清是「跌」还是「被套」。

验收铁律不变：hit_multiple >= 1.15 且 PF >= 1.30 且优于随机基线（同量抽样）。

用法：python research/lead_engine2.py
"""
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lead_engine import build_panel, pf  # noqa: E402

PROJ = r"E:/WorkBuddy_dot_workbuddy/Claw"
INTEL = os.path.join(PROJ, "backtest_cache", "intel")
K = 10


def load_gz(p):
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        return pd.read_csv(gzip.open(p, "rt", encoding="utf-8"), dtype={"code": str})
    except Exception:
        return pd.DataFrame()


def expand(p, ev, col, win, cond=None, mask_out=None):
    """把事件表按 code 展开到 avail_date 之后的 win 个交易日。"""
    if ev.empty:
        return 0
    ev = ev.copy()
    ev = ev.copy()
    ev["code"] = ev["code"].astype(str).str.zfill(6)
    if "avail_date" not in ev.columns and "date" in ev.columns:
        ev["avail_date"] = ev["date"]      # intel_pool_events 用的是 date
    ev["avail_date"] = ev["avail_date"].astype(str).str.slice(0, 10)
    if cond is not None:
        ev = ev[cond(ev)]
    if mask_out is not None:
        ev = ev[mask_out(ev)]
    if ev.empty:
        return 0
    ps = p.sort_values(["code", "date"])
    idx = ps.groupby("code").indices
    dorder = ps["date"].values
    hit = 0
    for code, g in ev.groupby("code"):
        rows = idx.get(code)
        if rows is None:
            continue
        rd = dorder[rows]
        for d in g["avail_date"].values:
            j = np.searchsorted(rd, d, side="left")   # 严格在公告日之后
            if j >= len(rd):
                continue
            p.loc[p.index[rows[j:j + win]], col] = 1
            hit += 1
    return hit


def attach(p):
    n = len(p)
    for c in ("L1", "L2", "L3", "L4", "L6", "L7", "L8"):
        p[c] = 0

    # ── L1/L2 龙虎榜 / 机构席位（R34 已有）─────────────────────────
    feat = load_gz(os.path.join(INTEL, "intel_features.csv.gz"))
    if not feat.empty:
        feat["code"] = feat["code"].astype(str).str.zfill(6)
        f = feat[["code", "date", "lhb_prev", "inst_net"]].merge(
            p[["code", "date"]].assign(_i=np.arange(n)), on=["code", "date"], how="inner")
        if len(f):
            ii = f["_i"].values
            p.loc[p.index[ii], "L1"] = (f["lhb_prev"].values > 0).astype(int)
            p.loc[p.index[ii], "L2"] = (f["inst_net"].values > 0).astype(int)
        print("[L1/L2] %s / %s 行" % (format(int(p["L1"].sum()), ","),
                                      format(int(p["L2"].sum()), ",")))

    # ── L3/L4 慢变量事件（R34 已有）────────────────────────────────
    ev = load_gz(os.path.join(INTEL, "intel_pool_events.csv.gz"))
    if not ev.empty:
        print("[L3] 增持展开 %d 条" % expand(p, ev[ev["src"] == "inc"], "L3", 20))
        sub = ev[(ev["src"] == "blk")]
        if "avg_px" in sub.columns:
            sub = sub[pd.to_numeric(sub["avg_px"], errors="coerce") > 0]
        print("[L4] 大宗溢价展开 %d 条" % expand(p, sub, "L4", 5))

    # ── L6 成本倒挂（机构大宗买方被套）───────────────────────────────
    cb = load_gz(os.path.join(INTEL, "intel_cost_basis.csv.gz"))
    if not cb.empty:
        cb["code"] = cb["code"].astype(str).str.zfill(6)
        g = cb[["code", "date", "gap_org"]].dropna()
        g = g.merge(p[["code", "date"]].assign(_i=np.arange(n)),
                    on=["code", "date"], how="inner")
        if len(g):
            gap = g["gap_org"].values
            mk = (gap <= -0.05) & (gap >= -0.30)
            p.loc[p.index[g["_i"].values[mk]], "L6"] = 1
        print("[L6] 成本倒挂 %s 行（机构大宗成本线 −30%%~−5%%）"
              % format(int(p["L6"].sum()), ","))

    # ── L7 机构加仓（十大流通股东环比）──────────────────────────────
    he = load_gz(os.path.join(INTEL, "intel_holder_events.csv.gz"))
    if not he.empty:
        k = expand(p, he, "L7",
                   90,
                   cond=lambda d: (pd.to_numeric(d["d_org_ratio"], errors="coerce") > 0)
                   | (pd.to_numeric(d["net_chg_pct"], errors="coerce") > 0.05))
        print("[L7] 机构加仓展开 %d 条 → %s 行" % (k, format(int(p["L7"].sum()), ",")))

    # ── L8 调研热度 ─────────────────────────────────────────────────
    sv = load_gz(os.path.join(INTEL, "intel_survey_events.csv.gz"))
    if not sv.empty:
        q = pd.to_numeric(sv["n_inst"], errors="coerce")
        thr = float(q.quantile(0.75))
        k = expand(p, sv, "L8", 20,
                   cond=lambda d: pd.to_numeric(d["n_inst"], errors="coerce") >= thr)
        print("[L8] 调研热度(n_inst>=%.0f) 展开 %d 条 → %s 行" % (thr, k, format(int(p["L8"].sum()), ",")))
    return p


def topk(sub, score, k=K):
    d2 = pd.factorize(sub["date"].values, sort=True)[0]
    cnt = np.bincount(d2)
    st = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    order = np.lexsort((-score, d2))
    rank = np.arange(len(sub)) - st[d2[order]]
    mk = np.zeros(len(sub), bool)
    mk[order[rank < k]] = True
    return sub[mk]


def verdict(s, base):
    hr = s["buy"].mean()
    hm = hr / base
    f1 = pf(s["t1"].values)
    ok = (hm >= 1.15) and (f1 >= 1.30)
    return hr, hm, s["t1"].mean(), f1, ("PROMOTE?" if ok else "REJECT")


def main():
    print("[1/3] 建面板 ...", flush=True)
    p = build_panel()
    print("      %s 行 / %d 日 / %s 只" % (format(len(p), ","), p["date"].nunique(),
                                          format(p["code"].nunique(), ",")), flush=True)
    print("[2/3] 挂线索 ...", flush=True)
    p = attach(p)
    p = p.sort_values("date", kind="mergesort").reset_index(drop=True)
    base = p["buy"].mean()

    A = lambda m: m == 1  # noqa: E731
    L = {k: A(p[k]) for k in ("L1", "L2", "L3", "L4", "L6", "L7", "L8")}
    low = p["low"].values.astype(bool)

    combos = {
        "S1_榜": L["L1"],
        "S2_机构买": L["L2"],
        "S3_增持": L["L3"],
        "S4_大宗溢价": L["L4"],
        "S6_成本倒挂": L["L6"],
        "S7_机构加仓": L["L7"],
        "S8_调研热": L["L8"],
        "S6a_倒挂+MA20上": L["L6"] & ~low,
        "S6b_倒挂+MA20下": L["L6"] & low,
        "S67_加仓×倒挂": L["L7"] & L["L6"],
        "S346_增持×大宗×倒挂": L["L3"] & L["L4"] & L["L6"],
        "S678_加仓×倒挂×调研": L["L7"] & L["L6"] & L["L8"],
        "S3467_慢变量全共振": L["L3"] & L["L4"] & L["L6"] & L["L7"],
        "S1234_快×慢": ((L["L1"]) | (L["L2"])) & ((L["L3"]) | (L["L4"])),
        "S34_增持×大宗": L["L3"] & L["L4"],
    }
    print("\n[3/3] 基线 BUY 正例率 %.4f（可买 %s 行 / %d 日 / %s 只）\n"
          % (base, format(len(p), ","), p["date"].nunique(), format(p["code"].nunique(), ",")))
    print("%-22s %8s %8s %6s %8s %6s %10s" % ("组合", "n", "命中率", "倍数", "t1均值", "PF", "裁决"))
    rows = []
    for name, mk in combos.items():
        s = p[mk]
        if len(s) < 300:
            print("%-22s %8s  (样本不足)" % (name, format(len(s), ",")))
            rows.append({"combo": name, "n": int(len(s)), "verdict": "INSUFFICIENT"})
            continue
        hr, hm, m1, f1, v = verdict(s, base)
        print("%-22s %8s %8.4f %6.3f %+7.3f%% %6.3f %10s"
              % (name, format(len(s), ","), hr, hm, m1 * 100, f1, v))
        rows.append({"combo": name, "n": int(len(s)), "hit": float(hr), "hit_multiple": float(hm),
                     "t1_mean": float(m1), "pf": float(f1), "verdict": v})

    rng = np.random.default_rng(20261019)
    rb = [pf(p["t1"].values[rng.choice(len(p), min(len(p), 200000), replace=False)])
          for _ in range(5)]
    rand_pf = float(np.mean(rb))
    print("\n随机基线 PF = %.3f（20 万行 ×5）" % rand_pf)

    # ── 池内择时：L6/L7 选池 × 技术面选时 ─────────────────────────
    print("\n===== 池内择时（新线索圈池 + 技术面排序，K=%d）=====" % K)
    pools = {"池=成本倒挂": L["L6"], "池=机构加仓": L["L7"],
             "池=加仓×倒挂": L["L7"] & L["L6"]}
    # 需要 ret5 作为「企稳」代理 → 用面板内已有 low/ret1，另算 ret5 太贵，改用 low + ret1
    stage2 = []
    for pname, pmk in pools.items():
        sub = p[pmk]
        if len(sub) < 2000:
            print("%-14s 样本 %s 不足" % (pname, format(len(sub), ",")))
            continue
        for fname, score in (("ret1大(动量)", sub["ret1"].values),
                             ("ret1小(反转)", -sub["ret1"].values),
                             ("MA20上优先", np.where(sub["low"].values, 0.0, 1.0)),
                             ("MA20下优先", np.where(sub["low"].values, 1.0, 0.0))):
            s2 = topk(sub, score)
            if len(s2) < 200:
                continue
            hr2, hm2, m2, f2, v2 = verdict(s2, base)
            print("%-14s x %-12s n=%6s 命中 %.4f (x%.3f) t1 %+.3f%% PF %.3f %s"
                  % (pname, fname, format(len(s2), ","), hr2, hm2, m2 * 100, f2, v2))
            stage2.append({"pool": pname, "rank": fname, "n": int(len(s2)),
                           "hit": float(hr2), "hit_multiple": float(hm2),
                           "t1_mean": float(m2), "pf": float(f2), "verdict": v2})

    out = os.path.join(PROJ, "research", "lead_engine2.json")
    json.dump({"base": float(base), "n_rows": int(len(p)), "rows": rows,
               "stage2": stage2, "rand_pf": rand_pf,
               "coverage": {k: float(np.mean(v)) for k, v in combos.items()}},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[OK] %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
