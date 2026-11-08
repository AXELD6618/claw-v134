# -*- coding: utf-8 -*-
"""
妖股启动倾向 · 日频评分器 + 推送 + 自我学习闭环（原创，步骤②③落地）
=========================================================================
用法：
  python research/yaogu_scorer.py            # 训练+对全缓存最新交易日打分+推送Top-N
  python research/yaogu_scorer.py --verify   # 额外校验：被打分日后1~2日是否真启动连板
依赖：research/yaogu_launch_feature_mine_v2.py（特征与缓存加载器）
推送：push_channel.send（默认 DIALOG，仅 backup=True 走微信）
"""
from __future__ import annotations
import os, glob, json, argparse, datetime as dt
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import research.yaogu_launch_feature_mine_v2 as V2
PROJ = V2.PROJ
DAILY = V2.DAILY
LIM_MAIN = V2.LIM_MAIN

def train_model():
    """复用 v2 的面板构建+训练，返回 (scaler, coef数组, keep特征列表)。"""
    float_shr = V2.load_float()
    cost = V2.load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_cost_basis.csv.gz"))
    holder_all = V2.load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_holder_events.csv.gz"))
    feat_all = V2.load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_features.csv.gz"))
    cost_i=V2.index_by_code(cost); holder_i=V2.index_by_code(holder_all); feat_i=V2.index_by_code(feat_all)
    survey=V2.load_survey(); survey_i=V2.index_by_code(survey)
    files=glob.glob(os.path.join(DAILY,"*.csv")); rng=np.random.default_rng(20261024)
    files=rng.choice(files, min(420,len(files)), replace=False)
    events,controls=[],[]
    for f in files:
        code=os.path.basename(f).split(".")[0]
        res=V2.load_daily(code)
        if not res: continue
        d,o,c,h,l,v=res; n=len(c)
        if n<120: continue
        pct=[0.0]+[c[i]/c[i-1]-1 for i in range(1,n)]
        zt=[pct[i]>=LIM_MAIN for i in range(n)]
        pres=V2.launch_pres(c, zt)
        launch=pres
        minute=V2.load_minute(code)
        for pre in sorted(pres):
            if pre<61: continue
            tdn=V2.dn(d[pre]); cv=V2.latest(cost_i,code,tdn); hv=V2.latest(holder_i,code,tdn); fv=V2.latest(feat_i,code,tdn)
            fv2=V2.feats(np.array(c[:pre+1]),np.array(o[:pre+1]),np.array(h[:pre+1]),
                         np.array(l[:pre+1]),np.array(v[:pre+1]),zt[:pre+1],float_shr.get(code),cv,hv,fv,minute,day_str=d[pre])
            if fv2: events.append({**fv2,"code":code,"date":d[pre]})
        cand=[k for k in range(61,n-1) if k not in launch and (k+1) not in launch and (k+2) not in launch and (k-1) not in launch and (k-2) not in launch]
        if cand:
            for k in rng.choice(cand, min(60,len(cand)), replace=False):
                tdn=V2.dn(d[k]); cv=V2.latest(cost_i,code,tdn); hv=V2.latest(holder_i,code,tdn); fv=V2.latest(feat_i,code,tdn)
                fv2=V2.feats(np.array(c[:k+1]),np.array(o[:k+1]),np.array(h[:k+1]),
                            np.array(l[:k+1]),np.array(v[:k+1]),zt[:k+1],float_shr.get(code),cv,hv,fv,minute,day_str=d[k])
                if fv2: controls.append({**fv2,"code":code,"date":d[k]})
    E=pd.DataFrame(events); C=pd.DataFrame(controls)
    FEAT_KEYS=[c for c in E.columns if c not in ("code","date")]
    cov={k:E[k].notna().mean() for k in FEAT_KEYS}
    keep=[k for k in FEAT_KEYS if cov[k]>=0.3]
    allf=pd.concat([E.assign(y=1),C.assign(y=0)],ignore_index=True).dropna(subset=keep+["y","date"])
    allf["dn"]=allf["date"].map(V2.dn); cut=int(allf["dn"].quantile(0.7))
    tr=allf[allf["dn"]<=cut]; te=allf[allf["dn"]>cut]
    sc=StandardScaler().fit(tr[keep].values)
    clf=LogisticRegression(max_iter=1000,C=0.5).fit(sc.transform(tr[keep].values),tr["y"].values)
    return sc, clf.coef_[0], keep

def score_latest(sc, coef, keep):
    """对每支缓存股票的最新交易日算启动倾向分。"""
    float_shr=V2.load_float()
    cost=V2.load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_cost_basis.csv.gz"))
    holder_all=V2.load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_holder_events.csv.gz"))
    feat_all=V2.load_gz_csv(os.path.join(PROJ,"backtest_cache","intel","intel_features.csv.gz"))
    cost_i=V2.index_by_code(cost); holder_i=V2.index_by_code(holder_all); feat_i=V2.index_by_code(feat_all)
    survey=V2.load_survey(); survey_i=V2.index_by_code(survey)
    rows=[]
    for f in glob.glob(os.path.join(DAILY,"*.csv")):
        code=os.path.basename(f).split(".")[0]
        res=V2.load_daily(code)
        if not res: continue
        d,o,c,h,l,v=res; n=len(c)
        if n<61: continue
        pct=[0.0]+[c[i]/c[i-1]-1 for i in range(1,n)]
        zt=[pct[i]>=LIM_MAIN for i in range(n)]
        k=n-1  # 最新交易日
        tdn=V2.dn(d[k]); cv=V2.latest(cost_i,code,tdn); hv=V2.latest(holder_i,code,tdn); fv=V2.latest(feat_i,code,tdn)
        minute=V2.load_minute(code)
        fv2=V2.feats(np.array(c[:k+1]),np.array(o[:k+1]),np.array(h[:k+1]),
                     np.array(l[:k+1]),np.array(v[:k+1]),zt[:k+1],float_shr.get(code),cv,hv,fv,minute,day_str=d[k])
        if not fv2: continue
        x=np.array([[fv2.get(kk,np.nan) for kk in keep]])
        if np.isnan(x).any(): continue
        score=float(sc.transform(x)[0][0])  # 线性打分(未走sigmoid, 用于排序)
        rows.append({"code":code,"date":d[k],"score":score})
    df=pd.DataFrame(rows).sort_values("score",ascending=False).reset_index(drop=True)
    return df

def load_intel_hint(date_str):
    """加载某交易日的情报线索 hint(龙虎榜/机构净买/股东环比), 用于实时线索层(非训练特征)。"""
    if not date_str: return {}
    p=os.path.join(PROJ,"backtest_cache",f"intel_hint_{str(date_str).replace('-','')}.json")
    if not os.path.exists(p): return {}
    try:
        d=json.load(open(p,encoding="utf-8")); return {it['code']:it for it in d.get('items',[])}
    except: return {}

def load_sentiment():
    """加载 ticker_sentiment(舆情分, 仅覆盖9只焦点股的历史快照)。"""
    p=os.path.join(PROJ,"backtest_cache","ticker_sentiment.json")
    if not os.path.exists(p): return {}
    try:
        d=json.load(open(p,encoding="utf-8")); return d.get('sentiment',{}) or {}
    except: return {}

def push_topn(df, N=30):
    """推送 TopN + 情报线索标注(调研/龙虎榜/机构净买/舆情)。线索层仅作研判, 不入训练。"""
    survey=V2.load_survey(); survey_i=V2.index_by_code(survey)
    hint=load_intel_hint(str(df.iloc[0]['date']) if len(df) else None)
    sent=load_sentiment()
    lines=[f"【妖股启动倾向·日频Top{N} + 情报线索】 {dt.date.today()}"]
    nhit=0
    for i,r in df.head(N).iterrows():
        tdn=V2.dn(r['date']); cnt,inst=V2.survey_counts(survey_i,r['code'],tdn)
        tag=[]
        if cnt>0:
            tag.append(f"调研{cnt}次/机构覆盖{inst:.0f}"); nhit+=1
        if r['code'] in hint:
            h=hint[r['code']]; nhit+=1
            if h.get('lhb'): tag.append("龙虎榜")
            if 'inst_net' in h: tag.append(f"机构净买{h['inst_net']:+.2f}")
            if 'd_org_ratio' in h: tag.append(f"股东环比{h['d_org_ratio']:+.2f}")
        if r['code'] in sent:
            tag.append(f"舆情{sent[r['code']]:+.2f}")
        tag_s=("  ["+", ".join(tag)+"]") if tag else ""
        lines.append(f"  {i+1:2d}. {r['code']}  分={r['score']:+.3f}  截至{r['date']}{tag_s}")
    title=f"妖股启动倾向日频扫描 Top{N} · {dt.date.today()}"
    try:
        import push_channel as pc
        ok,ch,det,nt=pc.send(title,"\n".join(lines),"妖股启动倾向评分器")
        print(f"[push] ok={ok} channel={ch} tries={nt}  线索命中 {nhit}/{min(N,len(df))}")
    except Exception as e:
        print(f"[push] 跳过(非部署环境): {e}")
        print("\n".join(lines))

def verify_recent(df, horizon=2):
    """自我学习：被高分后 horizon 日内是否真启动连板(用缓存回看)。"""
    ledger=os.path.join(PROJ,"data","yaogu_ledger.json")
    log=json.load(open(ledger,encoding="utf-8")) if os.path.exists(ledger) else {"records":[]}
    hits=0; checked=0
    for r in df.head(50).itertuples():
        res=V2.load_daily(r.code)
        if not res: continue
        d,o,c,h,l,v=res; n=len(c)
        pct=[0.0]+[c[i]/c[i-1]-1 for i in range(1,n)]
        idx={x:i for i,x in enumerate(d)}
        if r.date not in idx: continue
        i0=idx[r.date]
        launched=False
        for off in range(1,horizon+1):
            if i0+off<n and pct[i0+off]>=LIM_MAIN:
                # 检查是否连板(>=2)
                run=1; j=i0+off
                while j+1<n and pct[j+1]>=LIM_MAIN: run+=1; j+=1
                if run>=V2.RUN_MIN: launched=True; break
        checked+=1; hits+=1 if launched else 0
        log["records"].append({"code":r.code,"date":r.date,"score":round(r.score,3),"launched":launched})
    json.dump(log,open(ledger,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    if checked:
        print(f"[verify] Top50 打分后{horizon}日内真启动连板: {hits}/{checked} = {hits/checked*100:.1f}%")
    return hits,checked

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--verify",action="store_true"); ap.add_argument("--topn",type=int,default=30)
    args=ap.parse_args()
    print("[1/3] 训练模型(复用v2面板)...")
    sc,coef,keep=train_model()
    print(f"      特征 {len(keep)} 维")
    print("[2/3] 对全缓存最新交易日打分...")
    df=score_latest(sc,coef,keep)
    print(f"      打分 {len(df)} 支, Top5:")
    for i,r in df.head(5).iterrows():
        print(f"        {i+1}. {r['code']} 分={r['score']:+.3f} 截至{r['date']}")
    print(f"[3/3] 推送Top{args.topn} + (可选)验证")
    push_topn(df,args.topn)
    if args.verify:
        verify_recent(df,horizon=2)
    # 落盘
    df.head(args.topn).to_csv(os.path.join(PROJ,"data","yaogu_topN.csv"),index=False)
    print(f"[OK] data/yaogu_topN.csv ({args.topn} 支)")

if __name__=="__main__":
    main()
