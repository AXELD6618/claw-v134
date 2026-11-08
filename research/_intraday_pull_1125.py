# -*- coding: utf-8 -*-
import json, urllib.request, os, sys, time
from research.board_limit import board_limit_pct  # 涨跌停幅度单一真相源

DATE = "2026-11-02"
DATA_ROOT = r"E:/WorkBuddy_dot_workbuddy/Claw"
UNIV = os.path.join(DATA_ROOT, "data/intraday_universe.json")
QUOTE_DIR = os.path.join(DATA_ROOT, "tail_raw")
os.makedirs(QUOTE_DIR, exist_ok=True)
DAILY_DIR = os.path.join(DATA_ROOT, "backtest_cache/daily")

def prefix(code):
    c = code[0]
    if c in ('5','6','9'): return 'sh'+code
    if c in ('8','4'): return 'bj'+code
    return 'sz'+code

def limit_info(code, name, prev_close):
    if prev_close <= 0: return None, 0.0
    name = name or ''
    nu = name.upper()
    # 2026-11-06 修复：原逻辑漏北交所 920/921(9开头)→误判0.10。统一走单一真相源，
    # 其 is_st 规则已实现"双创/北交所 ST 不降板、主板 ST 5%"，与下方原注释意图一致。
    lp = board_limit_pct(code, is_st=('ST' in nu))
    return round(prev_close*(1+lp), 2), lp

# ---- read universe ----
univ = json.load(open(UNIV, encoding='utf-8'))
codes = univ['codes']
print("universe n=", len(codes))

# ---- pull quotes in batches of 60 ----
def pull(batch):
    url = 'https://qt.gtimg.cn/q=' + ','.join(prefix(c['code']) for c in batch)
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    raw = urllib.request.urlopen(req, timeout=20).read().decode('gbk')
    out = {}
    for line in raw.strip().split(';'):
        line = line.strip()
        if '=' not in line: continue
        _, val = line.split('=',1)
        val = val.strip().strip('"')
        if not val: continue
        f = val.split('~')
        if len(f) < 47: continue
        code = f[2]
        try:
            price = float(f[3]); prev = float(f[4]); op = float(f[5])
            vol = float(f[6]); t = f[30]; chg = float(f[32])
            hi = float(f[33]); lo = float(f[34]); vr = float(f[46])
        except: continue
        out[code] = dict(name=f[1], price=price, prev_close=prev, open=op,
                         vol=vol, time=t, chg=chg, high=hi, low=lo, vol_ratio=vr)
    return out

allq = {}
batch = []
for c in codes:
    batch.append(c)
    if len(batch) >= 60:
        allq.update(pull(batch)); batch=[]
if batch: allq.update(pull(batch))
print("pulled:", len(allq), "of", len(codes))

# ---- miss check ----
miss = [c['code'] for c in codes if c['code'] not in allq]
print("missing:", len(miss), miss[:30])

# ---- load daily csv stats (T-1 + 120d limit count + T-1 vol ratio) ----
def daily_stats(code):
    p = os.path.join(DAILY_DIR, code + '.csv')
    if not os.path.exists(p): return None
    try:
        rows = []
        with open(p, encoding='utf-8') as fh:
            next(fh)
            for ln in fh:
                ln = ln.strip()
                if not ln: continue
                parts = ln.split(',')
                if len(parts) < 6: continue
                try:
                    d = parts[0]; o=float(parts[1]); c=float(parts[2]); h=float(parts[3]); l=float(parts[4]); v=float(parts[5])
                except: continue
                rows.append((d,o,c,h,l,v))
        if len(rows) < 2: return None
        last = rows[-1]; prev = rows[-2]
        t1_close, t2_close = last[2], prev[2]
        # T-1 涨跌幅 (prev trading day return)
        t1_ret = (t1_close - t2_close)/t2_close*100 if t2_close>0 else 0.0
        # T-1 limit-up?
        nu = ''  # unknown name here
        lp_t1 = 0.10
        if code[:2] in ('30','68'): lp_t1=0.20
        elif code[0] in ('8','4'): lp_t1=0.30
        t1_limit = round(t2_close*(1+lp_t1),2)
        t1_is_limit = t1_close >= t1_limit - 0.005
        # 120-day limit count
        win = rows[-121:-1] if len(rows)>=121 else rows[:-1]
        cnt = 0
        for i in range(1, len(win)):
            pc = win[i-1][2]; cc = win[i][2]
            if pc<=0: continue
            llp = 0.10
            if code[:2] in ('30','68'): llp=0.20
            elif code[0] in ('8','4'): llp=0.30
            lim = round(pc*(1+llp),2)
            if cc >= lim - 0.005: cnt += 1
        # T-1 vol ratio vs 20d avg
        vwin = [r[5] for r in rows[-21:-1]]  # last 20 days ending at T-1
        avg20 = sum(vwin)/len(vwin) if vwin else 0
        t1_vr = last[5]/avg20 if avg20>0 else 0.0
        return dict(t1_ret=t1_ret, t1_is_limit=t1_is_limit, limit120=cnt, t1_vol_ratio=t1_vr)
    except Exception as e:
        return None

# ---- build records + anomaly flags ----
records = []
for c in codes:
    code = c['code']; src = c['src']
    q = allq.get(code)
    if not q:
        continue
    name = (q.get('name') or '').strip() or c.get('name') or ''
    price, prev, op, vol, t, chg, hi, lo, vr = (
        q['price'], q['prev_close'], q['open'], q['vol'], q['time'],
        q['chg'], q['high'], q['low'], q['vol_ratio'])
    lp_price, lp_pct = limit_info(code, name, prev)
    rec = dict(sec_code=code, sec_name=name, price=price, prev_close=prev,
               open=op, high=hi, low=lo, vol=vol, vol_ratio=vr, chg=chg,
               time=t, src=','.join(src))
    flags = []
    board = ''
    # data consistency guard (suspension-resume glitch etc.)
    expect = prev*(1+chg/100) if prev>0 else 0
    suspect = abs(price - expect) > 0.05
    if lp_price is not None and not suspect:
        is_limit = price >= lp_price - 0.005
        low_limit = lo >= lp_price - 0.005
        dist = (lp_price - price)/prev*100 if prev>0 else 99
        if is_limit and low_limit:
            board = '一字板'; flags.append('连板一字')
        elif is_limit and not low_limit:
            board = '已封板'; flags.append('连板封板')
        elif dist <= 2.5:
            board = '临界'; flags.append('临界逼近涨停')
        rec['limit_price'] = lp_price
        rec['board'] = board
        rec['dist_to_limit'] = round(dist, 2)
    else:
        rec['limit_price'] = None; rec['board']=''; rec['dist_to_limit']=None
        if suspect: flags.append('数据异常(停牌复牌参考价)')
    # generic surge / breakdown
    if chg >= 5.0: flags.append('强势拉升')
    if chg <= -5.0: flags.append('破位杀跌')
    if vr >= 2.5 and vr < 50: flags.append('放量(量比≥2.5)')
    # 冲高回落: high>=+7% but now <+2%
    hi_chg = (hi - prev)/prev*100 if prev>0 else 0
    if hi_chg >= 7.0 and chg < 2.0: flags.append('冲高回落')
    rec['flags'] = flags
    # 首板/爆发: 今日涨停 且 T-1 未涨停
    ds = daily_stats(code)
    rec['daily'] = ds
    if lp_price is not None and (price >= lp_price - 0.005) and ds is not None and not ds['t1_is_limit']:
        rec['first_board'] = True
    else:
        rec['first_board'] = False
    records.append(rec)

# ---- sector linkage (keyword tag among target sectors) ----
SECTORS = {
 '人形机器人':['机器人','减速器','伺服','谐波','执行器'],
 '半导体':['半导体','芯片','集成电路','晶圆','封测','设备'],
 '商业航天':['航天','卫星','火箭','北斗','商用航天'],
 'AI算力':['算力','CPO','光模块','IDC','液冷','服务器'],
 '物理AI':['AI','人工智能','智能体','大模型'],
}
sec_up = {k:[] for k in SECTORS}
for r in records:
    nm = r['sec_name']
    for sec, kws in SECTORS.items():
        if any(k in nm for k in kws):
            if r['chg'] >= 3.0:
                sec_up[sec].append((r['sec_code'], r['sec_name'], r['chg']))
sector_linkage = {k: len(v) for k,v in sec_up.items() if len(v)>=3}
for k,v in sec_up.items():
    if len(v)>=3:
        flags_any = False
        for r in records:
            if r['sec_code'] in [x[0] for x in v] and not any('板块联动' in fl for fl in r['flags']):
                r['flags'].append('板块联动')

# ---- save quotes (required) ----
qj = {"data":[{"sec_code":r['sec_code'],"sec_name":r['sec_name'],"price":r['price'],
              "prev_close":r['prev_close'],"open":r['open'],"high":r['high'],
              "low":r['low'],"vol":r['vol']} for r in records]}
qpath = os.path.join(QUOTE_DIR, f"{DATE}_quotes.json")
with open(qpath,'w',encoding='utf-8') as fh: json.dump(qj, fh, ensure_ascii=False, indent=1)
zpath = os.path.join(QUOTE_DIR, f"{DATE}_zt.json")
with open(zpath,'w',encoding='utf-8') as fh: json.dump(qj, fh, ensure_ascii=False, indent=1)
print("quotes saved:", len(qj['data']), "->", qpath)

# ---- save analysis ----
anom = [r for r in records if r['flags']]
board_recs = [r for r in records if r['board'] in ('一字板','已封板','临界')]
first_recs = [r for r in records if r['first_board']]
analysis = dict(date=DATE, snapshot_time=records[0]['time'] if records else '',
                pool_n=len(codes), pulled_n=len(records), missing=miss,
                anomalies=anom, board=board_recs, first_board=first_recs,
                sector_linkage=sector_linkage)
with open(os.path.join(DATA_ROOT, f"_intraday_anoms_{DATE}_1125.json"),'w',encoding='utf-8') as fh:
    json.dump(analysis, fh, ensure_ascii=False, indent=1)

print("=== SUMMARY ===")
print("pool:", len(codes), "pulled:", len(records), "missing:", len(miss))
print("board hits:", len(board_recs), "first_board:", len(first_recs), "anom total:", len(anom))
print("sector linkage:", sector_linkage)
print("--- board list ---")
for r in sorted(board_recs, key=lambda x:-x['chg']):
    print(f"  {r['sec_code']} {r['sec_name']} {r['board']} chg={r['chg']:.2f}% px={r['price']} lim={r['limit_price']} src={r['src']}")
print("--- first_board list ---")
for r in sorted(first_recs, key=lambda x:-x['chg']):
    ds=r['daily']
    print(f"  {r['sec_code']} {r['sec_name']} chg={r['chg']:.2f}% T1ret={ds['t1_ret']:.2f} lim120={ds['limit120']} T1vr={ds['t1_vol_ratio']:.2f} src={r['src']}")
print("--- other anomalies (top 20 by |chg|) ---")
oth = [r for r in anom if r['board']=='' and not r['first_board']]
for r in sorted(oth, key=lambda x:-abs(x['chg']))[:20]:
    print(f"  {r['sec_code']} {r['sec_name']} chg={r['chg']:.2f}% vr={r['vol_ratio']:.2f} flags={r['flags']} src={r['src']}")
