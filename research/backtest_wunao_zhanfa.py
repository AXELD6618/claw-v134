#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
无脑战法 机械版 OOS 回测（科学严谨评估，非投资建议）

作者原始思路（4 步）：
  1. 筛涨幅：当天涨幅 5%~10%（主力刚启动）
  2. 看周线：MACD 零轴上方 + 趋势向上（过滤短期炒作）
  3. 等日线：回调到 20 日均线附近 + 放量阳线 买入；线上拿、线下走
  4. 止盈止损：+30% 卖 1/3，+50% 再减一半，剩余让利润奔跑；收盘跌破 20 日线离场

机械复刻（本脚本，全部用日线代理周线以对齐到交易日级信号，见下）：
  - 锚定日 T：当日涨幅 r_T ∈ [5%,10%] ∧ 周线 MACD>0 ∧ 周线趋势向上(周收盘>周MA20>周MA60)
  - 入场日 T'：T 之后 20 交易日内，首个满足 |close-MA20|/MA20 ≤ 3% ∧ 阳线(close>open)
    ∧ 量>1.5×MA20量 的交易日；以 T'+1 开盘价买入（防前视）
  - 退出：自上而下分层——首次触 +30% 卖 1/3、+50% 再卖剩余一半、余仓在 收盘<MA20 时离场；
    最长持有 60 交易日超时离场。
  - 周线状态用 pandas 对日线收盘重采样到 W-FRI 计算 EMA12/26/9，前向填充回日线（忠实于"周线"口径）。

评估口径（沿用系统框架）：
  - 主指标 = 分层持有收益（入场→退出），样本外全窗口。
  - 配套固定 horizon T+5/T+10/T+20 收益（与系统 T+5 门控 PF≥1.259 可比）。
  - 基准 = 同宇宙随机配对（每只信号交易配对一只随机同持有期交易），算 paired excess 与 t。
  - 判定：paired_excess>0 ∧ |t|≥2.0 ∧ PF>1.0 → 有 edge；否则 REJECTED。
"""
import os, sys, json, glob, datetime
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
D = os.path.join(ROOT, "backtest_cache", "daily")

def load(code):
    p = os.path.join(D, code + ".csv")
    try:
        arr = np.loadtxt(p, delimiter=",", dtype=str, skiprows=1)
    except Exception:
        return None
    if arr.ndim != 2 or arr.shape[0] < 70:
        return None
    try:
        dates = arr[:, 0]
        op = arr[:, 1].astype(float)
        cl = arr[:, 2].astype(float)
        hi = arr[:, 3].astype(float)
        lo = arr[:, 4].astype(float)
        vo = arr[:, 5].astype(float)
    except Exception:
        return None
    return dates, op, cl, hi, lo, vo

def sma(x, n):
    if len(x) < n:
        return np.full(len(x), np.nan)
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = (c[n:] - c[:-n]) / n
    return np.concatenate([np.full(n - 1, np.nan), out])

def run():
    files = [os.path.basename(f)[:-4] for f in glob.glob(os.path.join(D, "*.csv"))]
    files = [f for f in files if f[:1].isdigit()]
    print("[WB] stocks=%d" % len(files))

    trades = []          # 分层持有收益（入场→退出）
    h5, h10, h20 = [], [], []
    entries_examined = 0
    anchors = 0

    for fi, code in enumerate(files):
        L = load(code)
        if L is None:
            continue
        dates, op, cl, hi, lo, vo = L
        n = len(cl)
        # 日线指标
        ma20 = sma(cl, 20)
        ma60 = sma(cl, 60)
        ma20v = sma(vo, 20)
        r = np.concatenate([[np.nan], cl[1:] / cl[:-1] - 1.0])
        # 周线 MACD（pandas 重采样）
        try:
            import pandas as pd
            s = pd.Series(cl, index=pd.to_datetime(dates))
            wk = s.resample("W-FRI").last()
            e12 = wk.ewm(span=12, adjust=False).mean()
            e26 = wk.ewm(span=26, adjust=False).mean()
            wmacd = (e12 - e26)
            wma20 = wk.rolling(20).mean()
            wma60 = wk.rolling(60).mean()
            wclose = wk
            dm = wmacd.reindex(s.index, method="ffill").values
            wu = (wclose.reindex(s.index, method="ffill").values > wma20.reindex(s.index, method="ffill").values) & \
                 (wma20.reindex(s.index, method="ffill").values > wma60.reindex(s.index, method="ffill").values) & \
                 (dm > 0)
        except Exception:
            # 退化：日线 MACD 代理
            e12 = pd.Series(cl).ewm(span=12, adjust=False).mean().values if 'pd' in dir() else np.nan
            dmacd = (pd.Series(cl).ewm(span=12, adjust=False).mean() - pd.Series(cl).ewm(span=26, adjust=False).mean()).values
            wu = (dmacd > 0) if dmacd is not None else np.zeros(n, dtype=bool)
            wu = np.asarray(wu, dtype=bool)
        wu = np.asarray(wu, dtype=bool)

        anchor = (r >= 0.05) & (r <= 0.10) & wu & (~np.isnan(ma60)) & (cl > ma60) & (ma20 > ma60)
        # 入场条件
        entry_ok = (~np.isnan(ma20)) & (ma20 > 0) & (np.abs(cl - ma20) / ma20 <= 0.03) & \
                   (cl > op) & (vo > 1.5 * ma20v)

        ai = np.where(anchor)[0]
        for i in ai:
            if i < 70 or i >= n - 2:
                continue
            anchors += 1
            # 在 [i+1, i+20] 找首个 entry_ok
            found = -1
            for j in range(i + 1, min(i + 21, n - 1)):
                if entry_ok[j]:
                    found = j
                    break
            if found < 0:
                continue
            entries_examined += 1
            j = found
            if j + 1 >= n:
                continue
            entry = op[j + 1]
            if entry <= 0:
                continue
            # 分层退出
            pos = 1.0
            cash = 0.0
            t1 = t2 = False
            exited = False
            for k in range(j + 1, min(j + 2 + 60, n)):
                px = cl[k]
                if not t1 and pos > 0 and px >= entry * 1.30:
                    cash += px * 0.3333; pos -= 0.3333; t1 = True
                if not t2 and pos > 0 and px >= entry * 1.50:
                    cash += px * 0.5 * pos; pos *= 0.5; t2 = True
                if pos > 0 and cl[k] < ma20[k]:
                    cash += px * pos; pos = 0.0; exited = True
                    ret = cash / entry - 1.0
                    trades.append((ret, k - (j + 1)))
                    break
            if not exited and pos > 0:
                px = cl[min(j + 2 + 60, n - 1)]
                cash += px * pos
                ret = cash / entry - 1.0
                trades.append((ret, min(j + 2 + 60, n - 1) - (j + 1)))
            # 固定 horizon
            for H, buf in ((5, h5), (10, h10), (20, h20)):
                if j + 1 + H < n:
                    buf.append(cl[j + 1 + H] / entry - 1.0)

    trades = np.array(trades) if trades else np.array([[np.nan, np.nan]])
    rets = trades[:, 0]
    rets = rets[~np.isnan(rets)]
    print("[WB] anchors=%d entries=%d trades=%d" % (anchors, entries_examined, len(rets)))

    def stats(v):
        v = np.asarray(v, dtype=float)
        v = v[~np.isnan(v)]
        if len(v) == 0:
            return dict(n=0)
        pos = v[v > 0]; neg = v[v < 0]
        pf = (pos.sum() / abs(neg.sum())) if neg.sum() != 0 else float('inf')
        return dict(n=len(v), win=len(pos) / len(v), mean=v.mean(),
                    median=np.median(v), pf=pf)

    s_t = stats(rets)
    s5, s10, s20 = stats(h5), stats(h10), stats(h20)
    out = {"strategy": "无脑战法(机械复刻)", "trades": s_t,
           "h5": s5, "h10": s10, "h20": s20,
           "anchors": anchors, "entries_examined": entries_examined}

    # 配对随机基准：逐笔按策略实际持有天数 H 配对一只随机股票的同持有期收益（真配对）
    if len(rets) > 0:
        rng = np.random.default_rng(20261103)
        files2 = [f for f in files if f[:1].isdigit()]
        rand_rets = []
        hold = trades[:, 1].astype(int)
        for i in range(len(rets)):
            H = int(hold[i]) if (i < len(hold) and hold[i] >= 1) else 3
            ok = False
            for _attempt in range(50):
                c2 = files2[rng.integers(0, len(files2))]
                L2 = load(c2)
                if L2 is None:
                    continue
                _, op2, cl2, _, _, _ = L2
                m2 = len(cl2)
                if m2 < 71 + H:          # 留足 H 天空间，杜绝 low>=high
                    continue
                d2 = rng.integers(70, m2 - H)   # 起点下限 70，上限 m2-H 保证 d2+1+H<m2
                e2 = op2[d2 + 1]
                if d2 + 1 + H < m2 and e2 > 0:
                    rand_rets.append(cl2[d2 + 1 + H] / e2 - 1.0)
                    ok = True
                    break
            if not ok:
                rand_rets.append(0.0)
        rand_rets = np.array(rand_rets)
        paired = rets - rand_rets
        pe = paired.mean()
        se = paired.std(ddof=1) / np.sqrt(len(paired))
        t = pe / se if se > 0 else 0.0
        out["random_mean"] = float(rand_rets.mean())
        out["paired_excess"] = float(pe)
        out["paired_t"] = float(t)
        # Welch t (signal vs random)
        sv = rets.std(ddof=1); rv = rand_rets.std(ddof=1)
        denom = np.sqrt(sv**2/len(rets) + rv**2/len(rand_rets))
        out["welch_t"] = float((rets.mean()-rand_rets.mean())/denom) if denom > 0 else 0.0
        edge = (pe > 0) and (abs(t) >= 2.0) and (s_t.get("pf", 0) > 1.0)
        out["verdict"] = "EDGE_CANDIDATE" if edge else "REJECTED"

    print(json.dumps(out, ensure_ascii=False, indent=2, default=float))
    with open(os.path.join(HERE, "wunao_zhanfa_result.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=float)

if __name__ == "__main__":
    run()
