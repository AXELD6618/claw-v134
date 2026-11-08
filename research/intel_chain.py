# -*- coding: utf-8 -*-
"""
事件时序链 / 算计谋略 检验（Round35）

投资者原话：「包括他们的策略或由策划/事件进行的算计谋略安排计划」。
单看一个事件（有没有增持、有没有调研）此前都是弱信号或负信号。
但「先做什么、后做什么」是**另一维信息**——同样的两个事件，顺序不同含义不同。

本脚本检验 4 条时序链（前后间隔 <= 30 个交易日）：

  链A 调研→增持 : 机构先来调研，随后股东真金白银买   ← 「先验证再下注」
  链B 增持→调研 : 先增持，之后机构来调研            ← 「买完再找人接」
  链C 大宗溢价→增持 : 有人溢价接货，随后股东增持      ← 「先收货再加码」
  链D 增持→大宗溢价 : 增持之后出现溢价大宗            ← 「增持后对倒拉抬」

对照组：单个事件本身（只增持 / 只调研 / 只大宗溢价）。

验收铁律不变：hit_multiple >= 1.15 且 PF >= 1.30 且优于随机基线。

用法：python research/intel_chain.py
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
GAP = 30  # 链内最大间隔（交易日）

sys.path.insert(0, os.path.join(PROJ, "research"))


def load_gz(p):
    if not os.path.exists(p):
        return pd.DataFrame()
    try:
        return pd.read_csv(gzip.open(p, "rt", encoding="utf-8"), dtype={"code": str})
    except Exception:
        return pd.DataFrame()


def trading_calendar():
    """用 000001 的日线日期当交易日历；没有就退化为全市场日期并集里最常见的。"""
    p = os.path.join(PROJ, "backtest_cache", "daily", "000001.csv")
    if os.path.exists(p):
        d = pd.read_csv(p, usecols=["日期"])["日期"].astype(str).str.slice(0, 10)
        return np.array(sorted(set(d.values)))
    return None


def load_events(cal):
    """→ dict[src] = DataFrame(code, idx)  其中 idx = 事件在交易日历上的位置"""
    pos = {d: i for i, d in enumerate(cal)}

    def to_idx(s):
        return pd.to_numeric(pd.Series(s).astype(str).str.slice(0, 10).map(pos), errors="coerce")

    out = {}
    ev = load_gz(os.path.join(INTEL, "intel_pool_events.csv.gz"))
    if not ev.empty:
        ev["code"] = ev["code"].astype(str).str.zfill(6)
        inc = ev[ev["src"] == "inc"].copy()
        inc["idx"] = to_idx(inc["date"]).values
        out["inc"] = inc[["code", "idx"]].dropna()
        blk = ev[ev["src"] == "blk"].copy()
        if "avg_px" in blk.columns:
            blk = blk[pd.to_numeric(blk["avg_px"], errors="coerce") > 0]
        blk["idx"] = to_idx(blk["date"]).values
        out["blk"] = blk[["code", "idx"]].dropna()

    sv = load_gz(os.path.join(INTEL, "intel_survey_events.csv.gz"))
    if not sv.empty:
        sv["code"] = sv["code"].astype(str).str.zfill(6)
        q = pd.to_numeric(sv["n_inst"], errors="coerce")
        sv = sv[q >= 5]                     # 至少 5 家机构才算「一次像样的调研」
        sv["idx"] = to_idx(sv["avail_date"]).values
        out["sv"] = sv[["code", "idx"]].dropna()

    for k, v in out.items():
        v["idx"] = v["idx"].astype(int)
    return out


def chain_anchor(ev, first, second):
    """返回 (code, idx) 表示「first 事件之后 GAP 个交易日内出现 second」的锚点 = first 的位置。"""
    if first not in ev or second not in ev:
        return pd.DataFrame(columns=["code", "idx"])
    a = ev[first].rename(columns={"idx": "ia"})
    b = ev[second].rename(columns={"idx": "ib"})
    m = a.merge(b, on="code", how="inner")
    m = m[(m["ib"] >= m["ia"]) & (m["ib"] - m["ia"] <= GAP)]
    return m[["code", "ia"]].drop_duplicates().rename(columns={"ia": "idx"})


def expand(p, an, col, win, cal):
    """锚点(日历下标) → 换算成日期 → 在该票自己的交易日序列上定位 → win 日打标。

    ★ 不能用日历下标直接与面板里的日期字符串比较（类型不同，静默出错）。
    """
    if an.empty:
        return 0
    ps = p.sort_values(["code", "date"])
    idx = ps.groupby("code").indices
    dorder = ps["date"].values
    hit = 0
    for code, g in an.groupby("code"):
        rows = idx.get(code)
        if rows is None:
            continue
        rd = dorder[rows]
        for i in g["idx"].values:
            d = cal[int(i)]
            j = np.searchsorted(rd, d, side="left")
            if j >= len(rd):
                continue
            p.loc[p.index[rows[j:j + win]], col] = 1
            hit += 1
    return hit


def main():
    print("[1/3] 建面板 ...", flush=True)
    p = build_panel()
    print("      %s 行 / %d 日 / %s 只" % (format(len(p), ","), p["date"].nunique(),
                                          format(p["code"].nunique(), ",")), flush=True)
    cal = trading_calendar()
    print("[2/3] 载入事件（日历 %d 日）" % len(cal), flush=True)
    ev = load_events(cal)
    for k, v in ev.items():
        print("   %-4s %s 条 / %s 只" % (k, format(len(v), ","), format(v["code"].nunique(), ",")))

    chains = {
        "A_调研→增持": chain_anchor(ev, "sv", "inc"),
        "B_增持→调研": chain_anchor(ev, "inc", "sv"),
        "C_大宗溢价→增持": chain_anchor(ev, "blk", "inc"),
        "D_增持→大宗溢价": chain_anchor(ev, "inc", "blk"),
        "E_调研→大宗溢价": chain_anchor(ev, "sv", "blk"),
    }
    print("[3/3] 展开 ...", flush=True)
    for name, an in chains.items():
        k = expand(p, an, name, 20, cal)
        print("   %-16s 锚点 %s → 打标 %d" % (name, format(len(an), ","), k))

    p = p.sort_values("date", kind="mergesort").reset_index(drop=True)
    base = p["buy"].mean()
    rng = np.random.default_rng(20261019)
    rand_pf = float(np.mean([pf(p["t1"].values[rng.choice(len(p), 200000, replace=False)])
                             for _ in range(5)]))
    base_t5 = float(p["t5"].mean())
    base_t5_pf = float(pf(p["t5"].values))
    print("\n基线 BUY %.4f | 随机 t1-PF %.3f | 全市场 t5 均值 %+.3f%%（t5-PF %.3f）| 可买 %s 行\n"
          % (base, rand_pf, base_t5 * 100, base_t5_pf, format(len(p), ",")))
    print("%-18s %8s %8s %6s %8s %6s %8s %10s" % ("链", "n", "命中率", "倍数", "t1均值", "PF", "t5均值", "裁决"))
    rows = []
    for name in chains:
        mk = p[name] == 1
        s = p[mk]
        if len(s) < 300:
            print("%-18s %8s  (样本不足)" % (name, format(len(s), ",")))
            rows.append({"chain": name, "n": int(len(s)), "verdict": "INSUFFICIENT"})
            continue
        hr = s["buy"].mean()
        hm = hr / base
        f1 = pf(s["t1"].values)
        ok = (hm >= 1.15) and (f1 >= 1.30) and (f1 > rand_pf)
        v = "PROMOTE?" if ok else "REJECT"
        print("%-18s %8s %8.4f %6.3f %+7.3f%% %6.3f %+7.3f%% %10s"
              % (name, format(len(s), ","), hr, hm, s["t1"].mean() * 100, f1,
                 s["t5"].mean() * 100, v))
        assert True
        rows.append({"chain": name, "n": int(len(s)), "hit": float(hr), "hit_multiple": float(hm),
                     "t1_mean": float(s["t1"].mean()), "pf": float(f1),
                     "t5_mean": float(s["t5"].mean()), "verdict": v})

    json.dump({"base": float(base), "rand_pf": rand_pf, "gap": GAP, "rows": rows,
               "base_t5": base_t5, "base_t5_pf": base_t5_pf},
              open(os.path.join(PROJ, "research", "intel_chain.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n[OK] research/intel_chain.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
