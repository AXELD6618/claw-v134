# -*- coding: utf-8 -*-
"""病例研究：利亚德/漫步者/大业股份 的连板启动前一日(t-1)特征签名。
目的：定位当前模型「抓得住谁、漏了谁」，为补异动/催化剂特征定向。
"""
import os, numpy as np
import research.yaogu_launch_feature_mine_v2 as V2
from research.yaogu_launch_feature_mine_v2 import feats, load_daily, load_minute, load_float, LIM_MAIN, RUN_MIN

codes = {"利亚德":"300296", "漫步者":"002351", "大业股份":"603278"}
for nm, code in codes.items():
    res = load_daily(code)
    if not res:
        print(f"{nm}({code}) 无缓存"); continue
    d,o,c,h,l,v = res; n=len(c)
    pct=[0.0]+[c[i]/c[i-1]-1 for i in range(1,n)]
    zt=[pct[i]>=LIM_MAIN for i in range(n)]
    runs=[]; i=0
    while i<n:
        if zt[i]:
            j=i; rl=0
            while j<n and zt[j]: rl+=1; j+=1
            if rl>=RUN_MIN: runs.append((i,rl))
            i=j
        else: i+=1
    print(f"\n=== {nm}({code}) 缓存{n}日, 连板运行(>=2) {len(runs)} 段 ===")
    for (s,rl) in runs[-4:]:
        pre=s-1
        if pre<61: continue
        fv = feats(np.array(c[:pre+1]), np.array(o[:pre+1]), np.array(h[:pre+1]),
                   np.array(l[:pre+1]), np.array(v[:pre+1]), zt[:pre+1],
                   load_float().get(code), None, None, None, load_minute(code))
        print(f"  启动@{d[s]} 前一日{d[pre]} 连板{rl}日 | "
              f"ret1={fv.get('ret1'):+.3f} ret5={fv.get('ret5'):+.3f} ret10={fv.get('ret10'):+.3f} ret20={fv.get('ret20'):+.3f} "
              f"ma20_ratio={fv.get('ma20_ratio'):+.3f} vol_ratio={fv.get('vol_ratio'):.2f} amp={fv.get('amp'):.3f} "
              f"close_pos={fv.get('close_pos'):.2f} days_since_zt={fv.get('days_since_zt'):.0f} cnt_zt_60={fv.get('cnt_zt_60'):.0f} "
              f"yr_pctile={fv.get('yr_pctile'):.2f} | tailvol={fv.get('intraday_tailvol_share')} tailret={fv.get('intraday_tail_ret')}")
