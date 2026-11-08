# -*- coding: utf-8 -*-
"""
P0 门槛测试 (原创) — 严格妖股起爆前日信号 AUC 能否≥0.78
=========================================================================
复用 yaogu_launch_feature_mine_v2 的纯函数(load_daily/feats/launch_pres/dn 等)，
只替换「正样本标签」：在原 v2 口径(≥2连板 / 3日内2涨停首日前一日)基础上，
新增：①可买性过滤(前日非涨停, 尾盘买得进)；②强波段口径(T+1~T+3累计≥15%且含≥1涨停)。

保持与 v2 完全一致的方法论：420 抽样(rng 20261024)、覆盖率≥30% 特征保留、
按日期 70% 分位时间 OOS 切分、LogisticRegression + StandardScaler、AUC/Top10%倍数。

用法：python research/p0_gate_test.py
输出：research/p0_gate_test.json
"""
from __future__ import annotations
import os, sys, json
import numpy as np, pandas as pd
from scipy import stats as st

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJ, "research"))
import yaogu_launch_feature_mine_v2 as V2

LIM = V2.LIM_MAIN
DAILY = V2.DAILY
SEED = 20261024


def monster_pre(c, zt):
    """严格妖股起爆前一日 (用户目标口径):
    (a) ≥2 连板首日前一日, 且前日可买(非涨停);
    (b) 强波段: T+1..T+3 累计≥15% 且含≥1涨停, 且前日可买(非涨停)。
    返回 (pres集合, launch集合) 用于事件标注与控制排除。"""
    n = len(zt); pres = set(); launch = set()
    i = 0
    while i < n:
        if zt[i]:
            j = i; rl = 0
            while j < n and zt[j]:
                rl += 1; j += 1
            if rl >= 2:
                launch.update(range(i, j))
                pre = i - 1
                if pre >= 0 and not zt[pre]:
                    pres.add(pre)
            i = j
        else:
            i += 1
    # 强波段: 覆盖非连板但连续大涨的妖股(如高新发展/大业股份型主升)
    for i in range(n - 3):
        if zt[i]:
            continue  # 前日必须可买(非涨停)
        w = range(i + 1, min(i + 4, n))
        if not any(zt[k] for k in w):
            continue
        cum = c[min(i + 3, n - 1)] / c[i] - 1
        if cum >= 0.15:
            pres.add(i)
            launch.update(w)
    pres.discard(-1)
    return pres, launch


def build_panel(label_fn, seed=SEED):
    rng = np.random.default_rng(seed)
    files = rng.choice(glob_files(), 420, replace=False)
    DL = {}
    for f in files:
        code = os.path.basename(f).split(".")[0]
        res = V2.load_daily(code)
        if not res:
            continue
        d, o, c, h, l, v = res
        n = len(c)
        if n < 120:
            continue
        pct = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, n)]
        zt = [p >= LIM for p in pct]
        DL[code] = (d, o, c, h, l, v, pct, zt)
    # 板块联动(复用 52-key 映射)
    sectors = V2.load_sectors()
    sm = {}
    for cc, vv in sectors.items():
        for s in vv.get("sectors", []):
            sm.setdefault(s, set()).add(cc)
    ZT = {cc: set(i for i in range(len(dd[7])) if dd[7][i]) for cc, dd in DL.items()}

    def heat(code, tprev):
        if code not in sectors:
            return 0.0
        cnt = 0.0
        seen = set()
        for s in sectors[code].get("sectors", []):
            for m in sm.get(s, ()):
                if m == code or m in seen:
                    continue
                seen.add(m)
                if tprev in ZT.get(m, ()):
                    cnt += 1.0
        return cnt

    float_shr = V2.load_float()
    events, controls = [], []
    for code, (d, o, c, h, l, v, pct, zt) in DL.items():
        n = len(c)
        out = label_fn(c, zt)
        pres, launch = out if isinstance(out, tuple) else (out, out)
        for pre in sorted(pres):
            if pre < 61:
                continue
            fv = V2.feats(np.array(c[:pre + 1]), np.array(o[:pre + 1]), np.array(h[:pre + 1]),
                         np.array(l[:pre + 1]), np.array(v[:pre + 1]), zt[:pre + 1],
                         float_shr.get(code), None, None, None, None,
                         day_str=d[pre], sector_heat=heat(code, d[pre - 1]))
            if fv:
                events.append({**fv, "code": code, "date": d[pre]})
        cand = [k for k in range(61, n - 1)
                if k not in launch and (k + 1) not in launch
                and (k + 2) not in launch and (k - 1) not in launch and (k - 2) not in launch]
        if cand:
            for k in rng.choice(cand, min(60, len(cand)), replace=False):
                fv = V2.feats(np.array(c[:k + 1]), np.array(o[:k + 1]), np.array(h[:k + 1]),
                             np.array(l[:k + 1]), np.array(v[:k + 1]), zt[:k + 1],
                             float_shr.get(code), None, None, None, None,
                             day_str=d[k], sector_heat=heat(code, d[k - 1]))
                if fv:
                    controls.append({**fv, "code": code, "date": d[k]})
    return events, controls


def glob_files():
    import glob
    return glob.glob(os.path.join(DAILY, "*.csv"))


def evaluate(events, controls, tag):
    E = pd.DataFrame(events)
    C = pd.DataFrame(controls)
    FEAT = [c for c in E.columns if c not in ("code", "date")]
    cov = {k: E[k].notna().mean() for k in FEAT}
    keep = [k for k in FEAT if cov[k] >= 0.3]
    allf = pd.concat([E.assign(y=1), C.assign(y=0)], ignore_index=True).dropna(subset=keep + ["y", "date"])
    allf["dn"] = allf["date"].map(V2.dn)
    cut = int(allf["dn"].quantile(0.7))
    tr = allf[allf["dn"] <= cut]
    te = allf[allf["dn"] > cut]
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    sc = StandardScaler().fit(tr[keep].values)
    clf = LogisticRegression(max_iter=1000, C=0.5).fit(sc.transform(tr[keep].values), tr["y"].values)
    p = clf.predict_proba(sc.transform(te[keep].values))[:, 1]
    auc = roc_auc_score(te["y"].values, p)
    base = te["y"].mean()
    k = max(1, int(len(p) * 0.10))
    top = te["y"].values[np.argsort(p)[-k:]].mean()
    print(f"[{tag}] 事件{len(E)} 控制{len(C)} 特征{len(keep)} 测试{len(te)} "
          f"基线{base:.4f} AUC={auc:.4f} Top10%={top:.4f} 倍数={top / base:.3f}")
    # 特征组入模情况
    grp = {"换手率/比量": ["turnover", "turnover_ratio", "rv_w", "rv_m"],
           "周月综合": ["ret_wk", "ret_mo", "ma_wk_ratio", "ma_mo_ratio", "vol_wk_ratio", "vol_mo_ratio"],
           "筹码": ["cyq_gap_inc", "cyq_w60_inc", "cyq_gap_blk", "cyq_w60_blk", "cyq_gap_org", "cyq_w60_org", "cyq_above_org"],
           "洗盘反转": ["zt_mem_pullback", "lowclose_pullback"],
           "板块联动": ["sector_limitup_heat"],
           "日内": ["intraday_tailvol_share", "intraday_tail_ret", "intraday_tail_amp", "intraday_tail_rebound"]}
    for nm, ks in grp.items():
        ins = [x for x in ks if x in keep]
        print(f"  {nm}: {len(ins)}/{len(ks)} 入模")
    return {"tag": tag, "n_event": len(E), "n_ctrl": len(C), "n_feat": len(keep),
            "auc": round(float(auc), 4), "base": round(float(base), 4),
            "top10": round(float(top), 4), "lift": round(float(top / base), 3)}


if __name__ == "__main__":
    print("=== P0 门槛测试: 严格妖股起爆前日信号 AUC 能否≥0.78 ===")
    rS = evaluate(*build_panel(monster_pre), "严格妖股 Def-S(可买+强波段)")
    rV = evaluate(*build_panel(V2.launch_pres), "原v2 Def(≥2连板/3日2涨停)")
    print("\n=== 门槛裁决 (gate=0.78) ===")
    print(f"Def-S AUC={rS['auc']} -> {'PASS' if rS['auc'] >= 0.78 else 'FAIL'}")
    print(f"Def-v2 AUC={rV['auc']} (参照 0.7317)")
    json.dump({"gate": 0.78, "strict": rS, "v2baseline": rV},
              open(os.path.join(PROJ, "research", "p0_gate_test.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("[OK] research/p0_gate_test.json")
