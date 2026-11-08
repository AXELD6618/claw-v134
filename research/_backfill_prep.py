# -*- coding: utf-8 -*-
"""
回填准备（修正版）
=========================================================================
修正上一版 bug：上一版从 yaogu_feature_mine_v2.json 读 code 列表，但该 json 只存
模型结果(n_event/n_ctrl/coef...)，不含原始样本 code -> 提取到 0 个。

正确口径：
  · 全宇宙 = backtest_cache/daily/*.csv 的所有 code（当前 5225）
  · v2 建模抽样 = main() 中 rng.choice(files, 420, replace=False) seed=20261024
    -> 复现该抽样得到 v2 实际使用的 420 只（sector_heat/ZT/intraday 都只在这 420 内）
  · 事件股宇宙 = 日线中满足 launch_pres(低收回调+涨停) 的 code（用于③分钟优先回填）

产出：
  research/backfill_all_codes.csv      全宇宙 5225（用于①全市场板块映射，后期阶段）
  research/backfill_target_420.csv     v2 建模抽样 420（首验：sector+minute 回填）
  research/backfill_event_stocks.csv   事件股（③分钟优先）
  research/backfill_queue.csv          回填队列（首验用 420；含 type 列）
"""
import os, glob, json
import numpy as np

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
MIN   = os.path.join(PROJ, "backtest_cache", "minute_5m")
SECT  = os.path.join(PROJ, "data", "industry_map", "stock_to_sector.json")
LIM_MAIN = 0.095
RUN_MIN = 2

def dn(s):
    try: return int(s.replace("-","").replace(" ","").replace(":","")[:12])
    except: return 0

def launch_pres(zt):
    n=len(zt); pres=set(); i=0
    while i<n:
        if zt[i]:
            j=i; rl=0
            while j<n and zt[j]: rl+=1; j+=1
            if rl>=RUN_MIN: pres.add(i-1)
            i=j
        else: i+=1
    for i in range(n):
        if zt[i] and not (i>0 and zt[i-1]):
            cnt=sum(zt[i:i+3])
            if cnt>=2 and not (i+1<n and zt[i+1]): pres.add(i-1)
    pres.discard(-1)
    return pres

# 1) 全宇宙 code
all_files = sorted(glob.glob(os.path.join(DAILY, "*.csv")))
all_codes = [os.path.basename(f).split(".")[0] for f in all_files]
print(f"[全宇宙] daily CSV = {len(all_codes)} 只 (口径: backtest_cache/daily/*.csv 文件名去扩展名)")

# 2) 复现 v2 的 420 抽样
rng = np.random.default_rng(20261024)
sampled_files = rng.choice(all_files, min(420, len(all_files)), replace=False)
sampled_420 = sorted(os.path.basename(f).split(".")[0] for f in sampled_files)
print(f"[v2抽样] 建模用 {len(sampled_420)} 只 (口径: 复现 main() rng.choice seed=20261024)")

# 3) 事件股宇宙（扫日线，launch_pres）
event_codes = set()
for code in all_codes:
    p = os.path.join(DAILY, f"{code}.csv")
    try:
        c = []
        with open(p, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                a = ln.strip().split(",")
                if len(a) < 3: continue
                try: c.append(float(a[2]))
                except: pass
        n = len(c)
        if n < 61: continue
        pct = [0.0] + [c[i]/c[i-1]-1 for i in range(1, n)]
        zt = [pct[i] >= LIM_MAIN for i in range(n)]
        if launch_pres(zt): event_codes.add(code)
    except Exception:
        pass
event_codes = sorted(event_codes)
print(f"[事件股] 满足 launch_pres 的 code = {len(event_codes)} 只 (口径: 日线≥61行且 launch_pres 非空)")

# 4) 已映射 / 已有分钟
have_sec = set()
if os.path.exists(SECT):
    have_sec = set(json.load(open(SECT, encoding="utf-8")).keys())
have_min = set(f[:-5] for f in os.listdir(MIN)) if os.path.isdir(MIN) else set()
print(f"[已有] 板块映射 {len(have_sec)} / 分钟 {len(have_min)}")

# 5) 写出清单
def w(name, codes):
    out = os.path.join(PROJ, "research", name)
    with open(out, "w", encoding="utf-8") as f:
        f.write("code\n")
        for c in codes: f.write(c + "\n")
    return out

w("backfill_all_codes.csv", all_codes)
w("backfill_target_420.csv", sampled_420)
w("backfill_event_stocks.csv", event_codes)

# 回填队列（首验）：420 抽样中 未映射 或 无分钟 的
queue = [c for c in sampled_420 if c not in have_sec or c not in have_min]
qout = os.path.join(PROJ, "research", "backfill_queue.csv")
with open(qout, "w", encoding="utf-8") as f:
    f.write("code,need_sector,need_minute\n")
    for c in queue:
        f.write(f"{c},{'0' if c in have_sec else '1'},{'0' if c in have_min else '1'}\n")
print(f"[队列·首验] 420抽样中待回填 {len(queue)} 只 -> {qout}")
print(f"  其中需板块 {sum(1 for c in queue if c not in have_sec)} / 需分钟 {sum(1 for c in queue if c not in have_min)}")

# 全市场板块映射缺口（后期阶段）
pending_all_sec = [c for c in all_codes if c not in have_sec]
print(f"[缺口·全市场板块] 待映射 {len(pending_all_sec)} / {len(all_codes)}")
pending_all_min = [c for c in event_codes if c not in have_min]
print(f"[缺口·事件股分钟] 待分钟 {len(pending_all_min)} / {len(event_codes)}")
