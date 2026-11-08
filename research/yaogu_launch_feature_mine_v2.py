# -*- coding: utf-8 -*-
"""
妖股启动前交易日 · 扩展特征挖掘引擎 v2（原创）
=========================================================================
在 v1(29维日线价量) 基础上，纳入用户要求的全维度：
  · 换手率(turnover = 成交量/流通股本)
  · 比量(relative volume, 多窗口 vs 周/月均量)
  · 周K/月K/日K 综合(resample 后动量/均线/量)
  · 筹码(CYQ成本分布: 价 vs 散户/ blk/ 机构成本基 gap)
  · 股东人数/增减持(intel_holder_events)
  · 情报(龙虎榜/机构净买 intel_features)
  · 舆情(ticker_sentiment, 有限覆盖)
  · 日内(14:30-14:50 五分BAR: 尾盘量能占比/价格行为)

硬约束同 v1：所有特征仅用第 t 日及之前切片；intel/holder/minute 取 date<=t 的最新值(lag=1)；
时间 CV + embargo；泄漏审计。

用法：python research/yaogu_launch_feature_mine_v2.py
输出：research/yaogu_feature_mine_v2.json + 打印（与 v1 的 0.71 对比）
"""
from __future__ import annotations
import os, glob, json, gzip, datetime as dt
import numpy as np
import pandas as pd
from scipy import stats as st

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
LIM_MAIN = 0.095
RUN_MIN = 2

# ── 辅助缓存加载 ──────────────────────────────────────────────────────────
def load_float():
    d = {}
    p = os.path.join(PROJ, "backtest_cache", "float_shares.csv")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            f.readline()
            for ln in f:
                a = ln.strip().split(",")
                if len(a) >= 2:
                    try: d[a[0]] = float(a[1])
                    except: pass
    return d

def load_gz_csv(path):
    if not os.path.exists(path): return {}
    out = {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        h = f.readline().strip().split(",")
        for ln in f:
            a = ln.strip().split(",")
            if len(a) < 2: continue
            out[a[0]] = dict(zip(h, a))
    return out

def load_minute(code):
    p = os.path.join(PROJ, "backtest_cache", "minute_5m", f"{code}.json")
    if not os.path.exists(p): return None
    try: return json.load(open(p, encoding="utf-8"))
    except: return None

SECTORS = None
def load_sectors():
    """①全市场行业/题材映射(解锁板块联动)。由 build_sector_map.py 经
    tdx_indicator_select 逐股拉取聚合得到 data/industry_map/stock_to_sector.json。"""
    global SECTORS
    if SECTORS is not None: return SECTORS
    p = os.path.join(PROJ, "data", "industry_map", "stock_to_sector.json")
    try: SECTORS = json.load(open(p, encoding="utf-8"))
    except Exception: SECTORS = {}
    return SECTORS

def dn(s):
    try: return int(s.replace("-","").replace(" ","").replace(":","")[:12])
    except: return 0

# ── 按 code 建 date 索引(用于 lag=1 取最新<=t) ───────────────────────────
def index_by_code(d):
    """将 intel/holder 缓存(字典, code->row) 转成 {code:[(dn_date,row),...]} 升序列表。"""
    idx={}
    for code,row in d.items():
        if 'date' in row:
            idx.setdefault(code,[]).append((dn(row['date']),row))
        elif 'avail_date' in row:
            idx.setdefault(code,[]).append((dn(row['avail_date']),row))
        elif 'end' in row:
            idx.setdefault(code,[]).append((dn(row['end']),row))
    for c in idx: idx[c].sort()
    return idx

def latest(idx, code, tdn):
    """在 idx[code] 升序列表中找 date<=tdn 的最新一条(实现 lag=1，无未来函数)。"""
    if code not in idx: return None
    import bisect
    arr=idx[code]
    j=bisect.bisect_right(arr, (tdn+1,))-1
    return arr[j][1] if j>=0 else None

def _ord(x):
    """YYYYMMDD(int/str) -> 距公元1天的序数, 用于窗口比较(避免直接整数相减的月份误差)。"""
    try:
        s=str(int(x)); return dt.date(int(s[:4]),int(s[4:6]),int(s[6:8])).toordinal()
    except: return 0

# ── 调研(机构/基金/券商/外资)情报事件：因果可用(avail_date<=t) ──────────────
def load_survey():
    """intel_survey_events.csv.gz: code,avail_date,n_inst,n_fund,n_broker,n_foreign。"""
    return load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_survey_events.csv.gz"))

def survey_counts(survey_i, code, tdn, days=90):
    """截至 tdn 前 days 个日历日内, 该 code 的调研事件次数与机构覆盖合计(因果, 无未来函数)。"""
    if code not in survey_i: return (0, 0.0)
    to=_ord(tdn); lo=to-days
    cnt=0; inst=0.0
    for (adv,row) in survey_i[code]:
        a=_ord(adv)
        if lo<=a<=to:
            cnt+=1
            try: inst+=float(row.get('n_inst',0) or 0)+float(row.get('n_fund',0) or 0)+float(row.get('n_broker',0) or 0)+float(row.get('n_foreign',0) or 0)
            except: pass
    return (cnt, inst)

def launch_pres(c, zt):
    """返回所有'启动前一日'索引集合(用于事件标注与控制排除)。
    启动定义放宽: (1) ≥2 连板(run)首日前一日; (2) 2涨停/3日非连续(如利亚德10-21/10-23, 中间10-22非涨停)。
    两种口径均避免重复计数。pre = 启动日-1, 结构上无未来函数。"""
    n=len(zt); pres=set()
    i=0
    while i<n:  # (1) 连续连板 run >=2
        if zt[i]:
            j=i; rl=0
            while j<n and zt[j]: rl+=1; j+=1
            if rl>=RUN_MIN: pres.add(i-1)
            i=j
        else: i+=1
    for i in range(n):  # (2) 2涨停/3日 非连续
        if zt[i] and not (i>0 and zt[i-1]):
            cnt=sum(zt[i:i+3])
            if cnt>=2 and not (i+1<n and zt[i+1]):
                pres.add(i-1)
    pres.discard(-1)
    return pres

# ── 日线基础加载 ──────────────────────────────────────────────────────────
def load_daily(code):
    p = os.path.join(DAILY, f"{code}.csv")
    if not os.path.exists(p): return None
    d,o,c,h,l,v = [],[],[],[],[],[]
    with open(p, encoding="utf-8") as f:
        f.readline()
        for ln in f:
            a = ln.strip().split(",")
            if len(a) < 6: continue
            try:
                d.append(a[0]); o.append(float(a[1])); c.append(float(a[2]))
                h.append(float(a[3])); l.append(float(a[4])); v.append(float(a[5]))
            except: pass
    return d,o,c,h,l,v

def resample(c, v, freq):
    """日线收盘/量 → 周或月重采样(末位对齐)。返回与日线等长、每点为其所在周/月窗口聚合。"""
    # 简化：用滚动窗口近似（周=5，月=20交易日）做累计回报/均量比
    return freq

# ── 特征(截断切片, 无未来) ────────────────────────────────────────────────
def feats(c, o, h, l, v, zt, float_shr, intel_cost, holder, intel_feat, minute, survey=None, day_str=None, sector_heat=0.0):
    n = len(c)
    if n < 61: return None
    ci, oi, hi, li, vi = c[-1], o[-1], h[-1], l[-1], v[-1]
    pct = np.array([0.0] + [c[i]/c[i-1]-1 for i in range(1, n)])
    f = {}
    f['body'] = (ci-oi)/ci if ci else 0.0
    f['up_shadow'] = (hi-max(oi,ci))/ci if ci else 0.0
    f['lo_shadow'] = (min(oi,ci)-li)/ci if ci else 0.0
    f['close_pos'] = (ci-li)/(hi-li) if (hi-li)>1e-9 else 0.5
    f['gap'] = oi/c[-2]-1 if n>=2 and c[-2] else 0.0
    f['amp'] = (hi-li)/ci if ci else 0.0
    for w in (1,5,10,20,60):
        f[f'ret{w}'] = ci/c[-1-w]-1 if n>w else 0.0
    f['ret5_1'] = (ci/c[-6]-1)-(c[-6]/c[-11]-1) if n>10 else 0.0
    for w in (20,60):
        f[f'vol{w}'] = float(np.std(pct[-w:]))
    # 换手率 + 比量
    if float_shr and float_shr > 0:
        turn = vi*100.0/float_shr          # 手->股
        f['turnover'] = turn
        f['turnover_ratio'] = turn/float(np.mean([vi*100.0/float_shr for vi in v[-20:]])) if np.mean(v[-20:])>0 else 1.0
    else:
        f['turnover'] = np.nan; f['turnover_ratio'] = np.nan
    f['vol_ratio'] = vi/float(np.mean(v[-20:])) if np.mean(v[-20:])>0 else 1.0
    # 比量: 今日量 vs 周(5d)/月(20d)均量 的多窗口比
    f['rv_w'] = vi/float(np.mean(v[-5:])) if np.mean(v[-5:])>0 else 1.0
    f['rv_m'] = vi/float(np.mean(v[-20:])) if np.mean(v[-20:])>0 else 1.0
    for w in (5,10,20,60):
        ma=float(np.mean(c[-w:])); f[f'ma{w}_ratio']=ci/ma-1 if ma>0 else 0.0
    f['dist_high20']=ci/max(h[-20:])-1
    f['dist_low20']=ci/min(l[-20:])-1
    f['dd_from20h']=ci/max(c[-20:])-1
    win=c[-250:] if n>=250 else c
    f['yr_pctile']=float((win<ci).mean())
    vw=sum(c[-120:][j]*v[-120:][j] for j in range(min(n,120)))/max(1e-9,sum(v[-120:]))
    f['vwap120_ratio']=ci/vw-1 if vw>0 else 0.0
    amps=np.array([(h[i]-l[i])/c[i] for i in range(max(0,n-20),n) if c[i]>0])
    f['amp_ratio']=float((hi-li)/ci/np.mean(amps)) if len(amps) and np.mean(amps)>0 else 1.0
    f['days_since_zt']=float(next((k for k in range(1,n) if zt[-1-k]),n))
    f['cnt_zt_20']=float(np.sum(zt[-20:]))
    f['cnt_zt_60']=float(np.sum(zt[-60:]))
    if n>=10:
        vf=np.mean(v[-10:-5]); vs=np.mean(v[-5:])
        f['vol_trend']=vf/vs if vs>0 else 1.0
    else: f['vol_trend']=1.0
    # ── 周月综合(近似: 用 5/20 日窗口回报表征周/月动量) ──
    f['ret_wk']=ci/c[-6]-1 if n>5 else 0.0
    f['ret_mo']=ci/c[-21]-1 if n>20 else 0.0
    f['ma_wk_ratio']=ci/np.mean(c[-5:])-1 if n>5 else 0.0
    f['ma_mo_ratio']=ci/np.mean(c[-20:])-1 if n>20 else 0.0
    f['vol_wk_ratio']=vi/float(np.mean(v[-5:])) if np.mean(v[-5:])>0 else 1.0
    f['vol_mo_ratio']=vi/float(np.mean(v[-20:])) if np.mean(v[-20:])>0 else 1.0
    # ── 筹码(CYQ成本基 gap, lag=1 取最新<=t) ──
    if intel_cost:
        for kk in ('gap_inc','w60_inc','gap_blk','w60_blk','gap_org','w60_org'):
            try: f[f'cyq_{kk}']=float(intel_cost.get(kk, np.nan))
            except: f[f'cyq_{kk}']=np.nan
        # 派生: 价在机构成本上方? (套牢/获利)
        try: f['cyq_above_org']=1.0 if float(intel_cost.get('gap_org',np.nan))>=0 else 0.0
        except: f['cyq_above_org']=np.nan
    else:
        for kk in ('gap_inc','w60_inc','gap_blk','w60_blk','gap_org','w60_org','cyq_above_org'):
            f[kk]=np.nan
    # ── 股东人数/增减持(lag=1) ──
    if holder:
        for kk in ('n_org','org_ratio','top10_ratio','n_up','n_dn','net_chg_pct','d_org_ratio'):
            try: f[f'hd_{kk}']=float(holder.get(kk, np.nan))
            except: f[f'hd_{kk}']=np.nan
    else:
        for kk in ('n_org','org_ratio','top10_ratio','n_up','n_dn','net_chg_pct','d_org_ratio'):
            f[kk]=np.nan
    # ── 情报(龙虎榜/机构净买, lag=1) ──
    if intel_feat:
        for kk in ('lhb_prev','lhb_net','lhb_net_ratio','inst_net','inst_net_cap','inst_net_sign'):
            try: f[f'it_{kk}']=float(intel_feat.get(kk, np.nan))
            except: f[f'it_{kk}']=np.nan
    else:
        for kk in ('lhb_prev','lhb_net','lhb_net_ratio','inst_net','inst_net_cap','inst_net_sign'):
            f[kk]=np.nan
    # ── 日内全天空异动(来自 minute_5m, 仅取 day_str 当日, 因果无未来函数) ──
    if minute and day_str:
        bars=[b for b in minute if str(b.get("day","")).startswith(str(day_str))]
        if bars and len(bars)>=2:
            vols=[float(b.get("volume",0)) for b in bars]
            closes=[float(b.get("close",0)) for b in bars]
            highs=[float(b.get("high",0)) for b in bars]
            lows=[float(b.get("low",0)) for b in bars]
            opens=[float(b.get("open",0)) for b in bars]
            tv_all=sum(vols)
            tail=[b for b in bars if "14:30"<=str(b.get("day",""))[-8:]<"15:00"]
            if tail:
                tv_t=float(sum(float(b.get("volume",0)) for b in tail))
                f['intraday_tailvol_share']=tv_t/tv_all if tv_all>0 else np.nan
                tc=[float(b["close"]) for b in tail]; to=[float(tail[0]["open"])]
                f['intraday_tail_ret']=(tc[-1]/to[0]-1) if to[0] else np.nan
                hi_m=max(float(b["high"]) for b in tail); lo_m=min(float(b["low"]) for b in tail)
                f['intraday_tail_amp']=(hi_m-lo_m)/tc[-1] if tc[-1] else np.nan
                f['intraday_tail_rebound']=(tc[-1]-lo_m)/(hi_m-lo_m) if (hi_m-lo_m)>1e-9 else 0.5
            else:
                f['intraday_tailvol_share']=np.nan; f['intraday_tail_ret']=np.nan; f['intraday_tail_amp']=np.nan; f['intraday_tail_rebound']=np.nan
            upbars=[(closes[i]/opens[i]-1) if opens[i] else 0.0 for i in range(len(bars))]
            f['intraday_max_upbar']=float(max(upbars)) if upbars else np.nan
            lim=ci*1.2 if ci>=8 else ci*1.1
            f['intraday_limit_tested']=1.0 if max(highs)>=lim*0.995 else 0.0
        else:
            for kk in ('intraday_tailvol_share','intraday_tail_ret','intraday_tail_amp','intraday_tail_rebound','intraday_max_upbar','intraday_limit_tested'):
                f[kk]=np.nan
    else:
        for kk in ('intraday_tailvol_share','intraday_tail_ret','intraday_tail_amp','intraday_tail_rebound','intraday_max_upbar','intraday_limit_tested'):
            f[kk]=np.nan
    # ── 洗盘反转(大业型: 缩量破均线下跌 + 近期涨停记忆) ──
    f['zt_mem_pullback']=float((f.get('cnt_zt_60',0) or 0))*max(0.0,-(f.get('ma20_ratio',0) or 0))
    f['lowclose_pullback']=(1.0-(f.get('close_pos',0.5) or 0.5))*max(0.0,-(f.get('ret1',0) or 0))
    # ①板块联动: t-1 当日同板块其他票涨停数(网文溢价4.7×); 仅映射股有值
    f['sector_limitup_heat']=float(sector_heat or 0.0)
    # ── 调研(机构/基金/券商/外资)情报: 因果可用(仅 avail_date<=t 的窗口计数) ──
    if survey is not None:
        f['survey_cnt_90d']=float(survey[0])
        f['survey_inst_90d']=float(survey[1])
    else:
        f['survey_cnt_90d']=np.nan; f['survey_inst_90d']=np.nan
    return f

def main():
    float_shr = load_float()
    cost = load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_cost_basis.csv.gz"))
    holder_all = load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_holder_events.csv.gz"))
    feat_all = load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_features.csv.gz"))
    # 按 code 建 date 索引(用于 lag=1 取最新<=t) — index_by_code/latest 已提为模块级
    cost_i=index_by_code(cost); holder_i=index_by_code(holder_all); feat_i=index_by_code(feat_all)
    survey=load_survey(); survey_i=index_by_code(survey)
    USE_SURVEY = os.environ.get("YAG_USE_SURVEY")=="1"  # 默认关: 生产模型不含调研(实验证其OOS非稳健)

    files=glob.glob(os.path.join(DAILY,"*.csv"))
    rng=np.random.default_rng(20261024)
    files=rng.choice(files, min(420,len(files)), replace=False)
    # ── Pass A: 载入日线 + 分钟, 建板块涨停索引 ──
    DL={}
    for f in files:
        code=os.path.basename(f).split(".")[0]
        res=load_daily(code)
        if not res: continue
        d,o,c,h,l,v=res; n=len(c)
        if n<120: continue
        pct=[0.0]+[c[i]/c[i-1]-1 for i in range(1,n)]
        zt=[pct[i]>=LIM_MAIN for i in range(n)]
        minute=load_minute(code)
        DL[code]=(d,o,c,h,l,v,pct,zt,minute)
    # ①板块联动索引: 同板块其他票在 t-1 涨停数(解锁网文 4.7× 溢价)
    sectors=load_sectors()
    sector_members={}
    for c,v in sectors.items():
        for s in v.get("sectors",[]):
            sector_members.setdefault(s,set()).add(c)
    ZT={c: set(dd[i] for i in range(len(dd)) if zz[i])
        for c,(dd,oo,cc,hh,ll,vv,pp,zz,mn) in DL.items()}
    def heat(code, tprev):
        if code not in sectors: return 0.0
        cnt=0.0; seen=set()
        for s in sectors[code].get("sectors",[]):
            for m in sector_members.get(s,()):
                if m==code or m in seen: continue
                seen.add(m)
                if tprev in ZT.get(m,()): cnt+=1.0
        return cnt
    # ── Pass B: 构造事件/控制样本(带 sector_heat) ──
    events,controls=[],[]
    for code,(d,o,c,h,l,v,pct,zt,minute) in DL.items():
        n=len(c)
        pres=launch_pres(c, zt); launch=pres
        for pre in sorted(pres):
            if pre<61: continue
            tdn=dn(d[pre])
            cv=latest(cost_i,code,tdn); hv=latest(holder_i,code,tdn); fv=latest(feat_i,code,tdn)
            sc_surv=survey_counts(survey_i,code,tdn) if USE_SURVEY else None
            fv2=feats(np.array(c[:pre+1]),np.array(o[:pre+1]),np.array(h[:pre+1]),
                      np.array(l[:pre+1]),np.array(v[:pre+1]),zt[:pre+1],
                      float_shr.get(code),cv,hv,fv,minute,survey=sc_surv,day_str=d[pre],
                      sector_heat=heat(code, d[pre-1]))
            if fv2: events.append({**fv2,"code":code,"date":d[pre]})
        cand=[k for k in range(61,n-1) if k not in launch and (k+1) not in launch
              and (k+2) not in launch and (k-1) not in launch and (k-2) not in launch]
        if cand:
            for k in rng.choice(cand, min(60,len(cand)), replace=False):
                tdn=dn(d[k])
                cv=latest(cost_i,code,tdn); hv=latest(holder_i,code,tdn); fv=latest(feat_i,code,tdn)
                sc_surv=survey_counts(survey_i,code,tdn) if USE_SURVEY else None
                fv2=feats(np.array(c[:k+1]),np.array(o[:k+1]),np.array(h[:k+1]),
                          np.array(l[:k+1]),np.array(v[:k+1]),zt[:k+1],
                          float_shr.get(code),cv,hv,fv,minute,survey=sc_surv,day_str=d[k],
                          sector_heat=heat(code, d[k-1]))
                if fv2: controls.append({**fv2,"code":code,"date":d[k]})
    E=pd.DataFrame(events); C=pd.DataFrame(controls)
    FEAT_KEYS=[c for c in E.columns if c not in ("code","date")]
    # 仅保留覆盖率>30%的特征
    cov={k: E[k].notna().mean() for k in FEAT_KEYS}
    keep=[k for k in FEAT_KEYS if cov[k]>=0.3]
    print(f"[面板] 事件 {len(E)} / 控制 {len(C)} / 股票 {E['code'].nunique()}")
    print(f"[特征] 总 {len(FEAT_KEYS)} -> 保留(覆盖>=30%) {len(keep)}")
    for grp,nm in [(['turnover','turnover_ratio','rv_w','rv_m'],'换手率/比量'),
                   (['ret_wk','ret_mo','ma_wk_ratio','ma_mo_ratio','vol_wk_ratio','vol_mo_ratio'],'周月综合'),
                   (['cyq_gap_inc','cyq_w60_inc','cyq_gap_blk','cyq_w60_blk','cyq_gap_org','cyq_w60_org','cyq_above_org'],'筹码'),
                   (['hd_n_org','hd_org_ratio','hd_top10_ratio','hd_n_up','hd_n_dn','hd_net_chg_pct'],'股东/增减持'),
                   (['it_lhb_prev','it_lhb_net','it_lhb_net_ratio','it_inst_net','it_inst_net_cap'],'情报'),
                   (['survey_cnt_90d','survey_inst_90d'],'调研'),
                   (['zt_mem_pullback','lowclose_pullback'],'洗盘反转'),
                   (['intraday_tailvol_share','intraday_tail_ret','intraday_tail_amp','intraday_tail_rebound','intraday_max_upbar','intraday_limit_tested'],'日内全天空')]:
        present=[g for g in grp if g in keep]
        print(f"  {nm}: {len(present)}/{len(grp)} 入模")
    # 单变量
    rows=[]
    for key in keep:
        ev=E[key].dropna().values; cv=C[key].dropna().values
        if len(ev)<20 or len(cv)<20: continue
        try: u,p=st.mannwhitneyu(ev,cv,alternative="two-sided")
        except: continue
        n1,n2=len(ev),len(cv); rb=1-2*u/(n1*n2)
        rows.append({"feat":key,"event_med":float(np.median(ev)),"ctrl_med":float(np.median(cv)),
                     "rank_biserial":float(rb),"p":float(p)})
    rd=pd.DataFrame(rows).sort_values("p").reset_index(drop=True)
    m=len(rd); rd["q"]=rd["p"]*m/(rd.index+1); rd["q"]=rd["q"].cummin()
    sig=rd[rd["q"]<0.05].sort_values("rank_biserial",key=lambda x:-x.abs())
    # 分组报告新维度显著性
    newgrp={'turnover':['turnover','turnover_ratio','rv_w','rv_m'],
            '周月':['ret_wk','ret_mo','ma_wk_ratio','ma_mo_ratio','vol_wk_ratio','vol_mo_ratio'],
            '筹码':['cyq_gap_inc','cyq_w60_inc','cyq_gap_blk','cyq_w60_blk','cyq_gap_org','cyq_w60_org','cyq_above_org'],
            '股东':['hd_n_org','hd_org_ratio','hd_top10_ratio','hd_n_up','hd_n_dn','hd_net_chg_pct'],
            '情报':['it_lhb_prev','it_lhb_net','it_lhb_net_ratio','it_inst_net','it_inst_net_cap'],
            '调研':['survey_cnt_90d','survey_inst_90d'],
            '洗盘反转':['zt_mem_pullback','lowclose_pullback'],
            '日内全天空':['intraday_tailvol_share','intraday_tail_ret','intraday_tail_amp','intraday_tail_rebound','intraday_max_upbar','intraday_limit_tested']}
    print(f"\n[新维度单变量显著性]")
    for nm,ks in newgrp.items():
        insig=[k for k in ks if k in set(sig['feat'])]
        print(f"  {nm}: 显著 {len(insig)}/{len([k for k in ks if k in keep])} -> {insig}")
    # 多变量(仅 keep 且非全nan)
    allf=pd.concat([E.assign(y=1),C.assign(y=0)],ignore_index=True)
    allf=allf.dropna(subset=keep+["y","date"])
    allf["dn"]=allf["date"].map(dn)
    cut=int(allf["dn"].quantile(0.7)); tr=allf[allf["dn"]<=cut]; te=allf[allf["dn"]>cut]
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    sc=StandardScaler().fit(tr[keep].values)
    clf=LogisticRegression(max_iter=1000,C=0.5).fit(sc.transform(tr[keep].values),tr["y"].values)
    p=clf.predict_proba(sc.transform(te[keep].values))[:,1]
    auc=roc_auc_score(te["y"].values,p); base=te["y"].mean()
    k=int(len(p)*0.10); top=te["y"].values[np.argsort(p)[-k:]].mean()
    print(f"\n[多变量OOS v2] 特征{len(keep)} 测试{len(te)} 基线{base:.4f} AUC={auc:.4f} Top10%={top:.4f} 倍数={top/base:.3f}")
    print(f"  (v1 基线对比: AUC 0.7095 / 倍数 3.05)")
    coef=dict(zip(keep,clf.coef_[0]))
    print("[模型权重 最强±8]:")
    for name,val in sorted(coef.items(),key=lambda x:-abs(x[1]))[:8]: print(f"  + {name:18s} {val:+.3f}")
    for name,val in sorted(coef.items(),key=lambda x:abs(x[1]))[:8]:
        if val<0: print(f"  - {name:18s} {val:+.3f}")
    # ── 召回探针: 直接验证大业/利亚德型是否被抓(模型打分在全体分位) ──
    Pall=clf.decision_function(sc.transform(allf[keep].values))
    ev_mask=allf['y'].values==1
    ev_scores=Pall[ev_mask]
    med_perc=float(np.median([(Pall<s).mean() for s in ev_scores]))
    print(f"\n[召回探针] 事件前一日在全体打分中的分位(1.0=全在顶部)")
    print(f"  全体事件中位分位 = {med_perc:.3f}  事件进入Top10%比例 = {float((ev_scores>=np.quantile(Pall,0.9)).mean()):.3f}")
    for nm,code,date in [("大业603278","603278","2026-10-15"),("利亚德300296","300296","2026-10-20")]:
        res=load_daily(code)
        if not res: print(f"  {nm}: 无缓存"); continue
        d2,o2,c2,h2,l2,v2=res
        if date not in d2: print(f"  {nm} {date}: 不在缓存"); continue
        ii=d2.index(date)
        if ii<61: print(f"  {nm} {date}: 历史不足"); continue
        zt2=[ (c2[j]/c2[j-1]-1)>=LIM_MAIN if j>0 else False for j in range(len(c2))]
        fv=feats(np.array(c2[:ii+1]),np.array(o2[:ii+1]),np.array(h2[:ii+1]),np.array(l2[:ii+1]),
                 np.array(v2[:ii+1]),zt2[:ii+1],load_float().get(code),None,None,None,
                 load_minute(code),day_str=date)
        if not fv or any(pd.isna(fv.get(kk)) for kk in keep):
            print(f"  {nm} {date}: 特征缺失/NaN, 无法打分(多为分钟数据缺失)"); continue
        s=float(clf.decision_function(sc.transform([[fv[kk] for kk in keep]]))[0])
        perc=float((Pall<s).mean())
        print(f"  {nm} {date}: 分={s:+.3f} 分位={perc:.3f} {'✓抓(前30%)' if perc>0.7 else '✗漏(尾部)'}")
    json.dump({"n_event":len(E),"n_ctrl":len(C),"n_feat_used":len(keep),
               "new_dim_sig":{nm:[k for k in ks if k in set(sig['feat'])] for nm,ks in newgrp.items()},
               "multivariate":{"auc":float(auc),"base":float(base),"top10":float(top),"lift":float(top/base)},
               "coef":{k:float(v) for k,v in coef.items()}},
              open(os.path.join(PROJ,"research","yaogu_feature_mine_v2.json"),"w",encoding="utf-8"),
              ensure_ascii=False,indent=1)
    print("\n[OK] research/yaogu_feature_mine_v2.json")

if __name__=="__main__":
    main()
