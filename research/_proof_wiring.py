import importlib.util, numpy as np
spec = importlib.util.spec_from_file_location("v2", r"e:\WorkBuddy_dot_workbuddy\Claw\research\yaogu_launch_feature_mine_v2.py")
V2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(V2)

# ③ 分钟数据
mn = V2.load_minute("603278")
print("③ minute bars(603278):", len(mn) if mn else 0)
date = "20261023"
bars = [b for b in mn if str(b.get("day", "")).startswith(date)] if mn else []
print("   bars on", date, ":", len(bars))

# ① 板块映射
sec = V2.load_sectors()
print("① mapped stocks:", len(sec), "->", list(sec.keys()))

# 日线切片
res = V2.load_daily("603278")
d, o, c, h, l, v = res
n = len(c)
pct = [0.0] + [c[i] / c[i - 1] - 1 for i in range(1, n)]
zt = [p >= V2.LIM_MAIN for p in pct]
pre = d.index(date) if date in d else n - 2

# 板块联动 heat（本快照仅 603278 入 ZT，故多为 0；验证接线）
sector_members = {}
for cc, vv in sec.items():
    for s in vv.get("sectors", []):
        sector_members.setdefault(s, set()).add(cc)
ZT = {"603278": set(d[i] for i in range(n) if zt[i])}
def heat(code, tprev):
    if code not in sec: return 0.0
    cnt = 0.0; seen = set()
    for s in sec[code].get("sectors", []):
        for m in sector_members.get(s, ()):
            if m == code or m in seen: continue
            seen.add(m)
            if tprev in ZT.get(m, ()): cnt += 1.0
    return cnt
hval = heat("603278", d[pre - 1])
print("① sector_limitup_heat(603278,t-1):", hval)

f = V2.feats(np.array(c[:pre + 1]), np.array(o[:pre + 1]), np.array(h[:pre + 1]),
             np.array(l[:pre + 1]), np.array(v[:pre + 1]), zt[:pre + 1],
             None, None, None, None, mn, day_str=date, sector_heat=hval)
print("--- feats 关键字段 ---")
for k in ("intraday_tail_rebound", "intraday_max_upbar", "intraday_limit_tested",
          "intraday_tailvol_share", "sector_limitup_heat", "zt_mem_pullback", "lowclose_pullback"):
    print(f"  {k}: {f.get(k)}")
print("OK: ③分钟 + ①板块 已接入 feats")
