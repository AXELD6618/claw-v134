# -*- coding: utf-8 -*-
"""
弹簧压到底 + 国资锁仓 + 低换手 + 强催化连板 —— 选股能力 (Layer A 本地预筛)

逻辑分层:
  Layer A (本脚本, 本地/廉价): 用 backtest_cache/daily/*.csv + float_shares.csv
           在 5225 只全量上计算近 250 交易日的结构指标, 预筛出
           "低换手 + 横盘压缩 + 无急涨/无深跌" 的安静压缩池.
  Layer B (外部/国资门): 用 tdx_screener("实际控制人为国资") 拉国资宇宙, 与本池相交.
  Layer C (TDX cyq, 昂贵/小集): 对相交后的少数票查 CYS/SCR, 卡 CYS∈[-5,+5] & SCR 历史偏低带.
  Layer D (事件触发): 监测 停牌/重组预案/国资资产注入 公告流, 作为触发层(另接).

本脚本只做 Layer A, 输出安静压缩池, 供后续层消费.
"""
import csv, os, glob, json, sys

DAILY_DIR = "backtest_cache/daily"
FLOAT_CSV = "backtest_cache/float_shares.csv"
OUT_JSON  = "backtest_cache/spring_soe_layerA.json"
OUT_CSV   = "backtest_cache/spring_soe_layerA.csv"

WINDOW = 250          # 近 250 交易日
TURNOVER_MAX = 1.5    # 日均换手率(%) 上限
VOLA_MAX = 38.0       # 年化波动率(%) 上限
AMP_MAX = 0.45        # 区间振幅 (high/low 跨度 / 均价) 上限
DRIFT_MAX = 0.18      # |收-均价|/均价 上限 (价格贴近自身均值=无大浮盈/深套)
NEAR_HIGH = 1.10      # 收 ≤ 区间最高*1.10 (非急涨在顶)

def load_float():
    d = {}
    with open(FLOAT_CSV, encoding="utf-8", errors="ignore") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) >= 2:
                try: d[row[0]] = float(row[1])
                except: pass
    return d

def metrics(path, float_shares):
    code = os.path.basename(path)[:-4]
    fs = float_shares.get(code)
    if fs is None or fs <= 0:
        return None
    closes, highs, lows, vols = [], [], [], []
    with open(path, encoding="utf-8", errors="ignore") as f:
        r = csv.reader(f); next(r, None)
        for row in r:
            if len(row) < 6: continue
            try:
                c = float(row[2]); h = float(row[3]); l = float(row[4]); v = float(row[5])
            except: continue
            closes.append(c); highs.append(h); lows.append(l); vols.append(v)
    n = len(closes)
    if n < WINDOW: return None
    c = closes[-WINDOW:]; h = highs[-WINDOW:]; l = lows[-WINDOW:]; v = vols[-WINDOW:]
    mean_c = sum(c)/len(c)
    # 日均换手率(%) = 成交量(手)*100/流通股本*100 = 成交量*10000/流通股本
    avg_vol_hands = sum(v)/len(v)
    turnover = avg_vol_hands * 10000.0 / fs
    # 年化波动率
    rets = [(c[i]/c[i-1]-1) for i in range(1,len(c)) if c[i-1] > 0]
    if len(rets) < 20: return None
    m = sum(rets)/len(rets)
    var = sum((x-m)**2 for x in rets)/len(rets)
    vola = (var**0.5) * (242**0.5) * 100.0
    # 区间振幅
    hi, lo = max(h), min(l)
    amp = (hi - lo) / mean_c if mean_c > 0 else 9e9
    # 压缩/漂移
    last = c[-1]
    drift = abs(last - mean_c) / mean_c if mean_c > 0 else 9e9
    near_high = last <= hi * NEAR_HIGH
    return dict(code=code, n=n, close=round(last,3), mean_c=round(mean_c,3),
                turnover=round(turnover,3), vola=round(vola,2), amp=round(amp,3),
                drift=round(drift,3), hi=round(hi,3), lo=round(lo,3), near_high=near_high)

def main():
    float_shares = load_float()
    files = glob.glob(os.path.join(DAILY_DIR, "*.csv"))
    print(f"[LayerA] 扫描 {len(files)} 只日线, 流通股本表 {len(float_shares)} 只", flush=True)
    pool = []
    skip = 0
    for i, p in enumerate(files):
        m = metrics(p, float_shares)
        if m is None:
            skip += 1; continue
        if (m["turnover"] <= TURNOVER_MAX and m["vola"] <= VOLA_MAX and
            m["amp"] <= AMP_MAX and m["drift"] <= DRIFT_MAX and m["near_high"]):
            pool.append(m)
        if (i+1) % 1000 == 0:
            print(f"  ...{i+1}/{len(files)} 当前池 {len(pool)}", flush=True)
    # 按换手率升序(最安静的排前)
    pool.sort(key=lambda x: x["turnover"])
    print(f"[LayerA] 预筛完成: 通过 {len(pool)} 只, 跳过(缺数据/不足250日) {skip} 只", flush=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(pool, f, ensure_ascii=False, indent=1)
    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code","close","turnover%","vola%","amp","drift","mean_c","hi","lo"])
        for m in pool:
            w.writerow([m["code"],m["close"],m["turnover"],m["vola"],m["amp"],m["drift"],m["mean_c"],m["hi"],m["lo"]])
    print(f"[LayerA] 已写出 {OUT_JSON} / {OUT_CSV}", flush=True)
    # 打印前 20 最安静样本
    print("\n前 20 最安静(换手最低)样本:")
    for m in pool[:20]:
        print(f"  {m['code']}  收{m['close']}  换手{m['turnover']}%  波动{m['vola']}%  振幅{m['amp']}  漂移{m['drift']}")

if __name__ == "__main__":
    main()
