# -*- coding: utf-8 -*-
"""
编译 2026 真实重大事件（重组/并购/定增/控制权变更/重大合同）到 catalyst_notices.jsonl，
并跑 cross_validate_leakage_vs_catalyst(lookback=20)。
事件日期 D = 市场首次知悉日（停牌公告日 / 预案披露日 / 中标公告日），用于检验 Leg-A 量能异动是否真在公告前频现。
纪律：只作 WATCH / 解释层，不进 buy_score / core_factor_model；不降任何门。
"""
from __future__ import annotations
import json, sys, importlib.util, datetime
from pathlib import Path

CLAU = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DAILY = CLAU / "backtest_cache" / "daily"
NOTICES = CLAU / "backtest_cache" / "catalyst_notices.jsonl"

# ── 主事件池（已逐条核对交易所/证券时报披露日）──
# (symbol, date_int, category, keyword, title)
E = []
def add(sym, d, cat, kw, title): E.append((str(sym), int(d), cat, kw, title))

# ===== MAJOR_ASSET / RESTRUCTURE（重组/并购/收购/资产注入/投建）=====
add("601326",20261021,"MAJOR_ASSET","重组","秦港股份:发行股份购买曹妃甸港口等股权 复牌")
add("000831",20261030,"MAJOR_ASSET","重组","中国稀土:收到重组通知 拟购中稀凉山股权 停牌")
add("002129",20261030,"MAJOR_ASSET","重组","TCL中环:筹划发行股份购中环领先少数股权 停牌")
add("000037",20261030,"MAJOR_ASSET","现金收购","深南电A:现金购天合跟踪≥51%股权(不停牌)")
add("600734",20261030,"MAJOR_ASSET","现金收购","*ST实达:现金购凯特科技≥51%股权(不停牌)")
add("002827",20261026,"MAJOR_ASSET","重组","高争民爆:发行股份购紫源铜业51%+晟源矿业100% 停牌")
add("600293",20261019,"MAJOR_ASSET","收购","三峡新材:发行股份及现金购珠海赛纬100% 复牌")
add("605303",20260914,"MAJOR_ASSET","收购","园林股份:发行股份购华澜微93.5%控股权 停牌")
add("002388",20261007,"MAJOR_ASSET","重组","新亚制程:筹划购启元气体控股权 停牌")
add("301314",20261025,"MAJOR_ASSET","收购","科瑞思:发行股份及现金购宏钢光电100% 复牌")
add("300332",20261027,"RESTRUCTURE","终止","天壕能源:终止购买天壕新能源100%")
add("688277",20260715,"MAJOR_ASSET","重组","天智航:发行股份购上海微创骨科62%股权 停牌")
add("001360",20261022,"MAJOR_ASSET","收购","南矿集团:1275万美元购BDLP 51%股权")
add("300067",20260408,"MAJOR_ASSET","重组","安诺其:发行股份及现金购广州烽云信息100% 停牌")
add("688072",20260629,"MAJOR_ASSET","重组","拓荆科技:发行股份购无锡尚积控股权 停牌")
add("601369",20260629,"MAJOR_ASSET","收购","陕鼓动力:发行股份购秦风气体36.06%股权 停牌")
add("688432",20260831,"MAJOR_ASSET","重组","有研硅:发行股份购山东有研艾斯71.89%+半导体14.98% 停牌")
add("600929",20260831,"MAJOR_ASSET","重组","雪天盐业:发行股份购河北坤天新能源100% 停牌")
add("301269",20261101,"MAJOR_ASSET","收购","华大九天:不超2.2亿购奇捷科技≤51%股权")
add("301327",20261030,"MAJOR_ASSET","收购","华宝新能:1250万欧元购Yuma GmbH 100%股权")
add("000670",20260106,"MAJOR_ASSET","重组","盈方微:发行股份购资产 复牌")
add("603616",20260122,"MAJOR_ASSET","收购","韩建河山:发行股份购辽宁兴福新材料99.9978% 复牌")
add("300912",20260121,"MAJOR_ASSET","重组","凯龙高科:发行股份购资产 复牌")
add("603933",20260326,"MAJOR_ASSET","收购","睿能科技:发行股份购博泰智能装备控制权 停牌")
add("000524",20260624,"MAJOR_ASSET","重组","岭南控股:发行股份购广电城市服务85% 停牌")
add("002870",20260901,"MAJOR_ASSET","收购","香山股份:筹划发行股份购浙江武珞智慧城市 停牌")
add("300333",20260806,"MAJOR_ASSET","收购","兆日科技:筹划发行股份购佰内科技 停牌")
add("300862",20260807,"MAJOR_ASSET","收购","蓝盾光电:发行股份购岚创科技控股权 复牌")
add("688291",20260908,"RESTRUCTURE","终止","金橙子:终止筹划购智博泰克 复牌")
add("300807",20260420,"MAJOR_ASSET","重组","天迈科技:发行股份购资产 复牌")
add("300828",20260205,"MAJOR_ASSET","重组","锐新科技:发行股份购资产 复牌")
add("601003",20260423,"MAJOR_ASSET","重组","柳钢股份:发行股份购广西钢铁集团股权 复牌")
add("300082",20260921,"MAJOR_ASSET","收购","奥克股份:筹划购苏州祺添控制权 停牌")
add("002860",20260922,"MAJOR_ASSET","收购","星帅尔:筹划购梵宇科技+湘鹰新材料100% 停牌")
add("002155",20260820,"MAJOR_ASSET","重组","湖南黄金:调整重大资产重组(购资产) 停牌")
add("600984",20260811,"MAJOR_ASSET","收购","建设机械:拟购蒲城清洁能源100% 停牌")
add("600825",20260907,"MAJOR_ASSET","收购","新华传媒:发行股份购界面财联社控股权 停牌")
add("688689",20260629,"MAJOR_ASSET","收购","银河微电:拟发行股份购恒泰柯半导体100%")
add("603137",20260630,"MAJOR_ASSET","收购","恒尚节能:发行股份购金胜电子100%")
add("300806",20260923,"MAJOR_ASSET","投建","斯迪克:拟16.52亿投建高端BOPET基膜扩产")
add("600438",20260225,"MAJOR_ASSET","重组","通威股份:筹划发行股份购青海丽豪清能100% 停牌")
add("600962",20260922,"MAJOR_ASSET","重组","国投中鲁:发行股份购电子院100% 实拟定增60.26亿")

# ===== REFINANCE（定增/配融，独立募资披露）=====
add("600884",20261021,"REFINANCE","定增","杉杉股份:40亿定增预案(海螺集团全额认购)")
add("600538",20261020,"REFINANCE","定增","国发股份:简易程序定增不超1.2亿")
add("601991",20260709,"REFINANCE","定增","大唐发电:定增不超80亿扩建电厂")
add("002902",20260731,"REFINANCE","定增","铭普光磁:定增不超12.83亿投光模块/光芯片")
add("002065",20260720,"REFINANCE","定增","东华软件:定增不超20.29亿投智算/AI")
add("603002",20260911,"REFINANCE","定增","宏昌电子:定增不超18亿投高端覆铜板")
add("000977",20260911,"REFINANCE","定增","浪潮信息:定增不超90亿加码AI基础设施")
add("688350",20260911,"REFINANCE","定增","富淼科技:定增6-6.6亿补流/还债")
add("688147",20261012,"REFINANCE","定增","微导纳米:定增不超49.44亿投半导体设备")
add("001359",20261014,"REFINANCE","定增","平安电工:定增不超13亿投电子布/云母")
add("300857",20260626,"REFINANCE","定增","协创数据:定增不超80亿投AI算力/存储")
add("300035",20260706,"REFINANCE","定增","德福科技:定增不超28亿投AI电子电路铜箔")
add("688313",20260716,"REFINANCE","定增","仕佳光子:定增不超28亿投高速光芯片")
add("601995",20260924,"REFINANCE","定增","中金公司:股权定增1138.59亿")
add("002386",20261028,"REFINANCE","定增","天原股份:现金定增4.79亿")
add("300018",20261021,"REFINANCE","定增","中元股份:现金定增5亿")
add("002108",20261019,"REFINANCE","定增","沧州明珠:现金定增6亿")
add("000829",20261016,"REFINANCE","定增","天音控股:现金定增17.08亿")
add("002185",20261016,"REFINANCE","定增","华天科技:股权定增21.17亿")
add("688345",20261013,"REFINANCE","定增","博力威:现金定增6.11亿")
add("920982",20261013,"REFINANCE","定增","锦波生物:现金定增10亿")
add("300327",20261008,"REFINANCE","定增","中颖电子:现金定增9.91亿")
add("300691",20261104,"REFINANCE","定增","联合光电:定增2.41亿")
add("000859",20261104,"REFINANCE","定增","国风新材:定增3.36亿")
add("301082",20261104,"REFINANCE","定增","久盛电气:定增4.27亿")
add("002106",20261105,"REFINANCE","定增","莱宝高科:定增25亿")
add("001306",20261105,"REFINANCE","定增","夏厦精密:定增8亿")
add("688295",20261105,"REFINANCE","定增","中复神鹰:定增38.93亿")
add("688167",20261105,"REFINANCE","定增","炬光科技:定增10.21亿")
add("688690",20261105,"REFINANCE","定增","纳微科技:定增7.10亿")
add("688766",20261105,"REFINANCE","定增","普冉股份:定增1.07亿")
add("300410",20261105,"REFINANCE","定增","正业科技:定增4亿")
add("000881",20261105,"REFINANCE","定增","中广核技:定增12.50亿")
add("603150",20261105,"REFINANCE","定增","万朗磁塑:定增5.64亿")
add("603124",20261106,"REFINANCE","定增","江南新材:定增16亿")

# ===== CONTROL_CHANGE（控制权变更）=====
add("002729",20260629,"CONTROL_CHANGE","控制权变更","好利科技:筹划控制权变更 停牌")
add("603722",20260629,"CONTROL_CHANGE","控制权变更","阿科力:筹划控制权变更(股份转让+定增) 停牌")
add("301390",20260910,"CONTROL_CHANGE","控制权变更","经纬股份:实控人等筹划控制权变更 停牌")
add("600621",20261027,"CONTROL_CHANGE","控股股东变更","华鑫股份:控股股东拟变国盛集团 复牌")
add("603311",20260511,"CONTROL_CHANGE","控制权变更","金海高科:筹划控制权变更 复牌")
add("002175",20260623,"CONTROL_CHANGE","控制权变更","*ST东智:科翔高新拟转让14.76% 停牌")
add("300585",20260919,"CONTROL_CHANGE","控制权变更","奥联电子:筹划控制权变更 停牌")
add("002274",20260829,"CONTROL_CHANGE","控制权变更","华昌化工:华纳投资拟转让24% 复牌")
add("002828",20260731,"CONTROL_CHANGE","控制权变更","贝肯能源:陈平贵拟转让11.65% 复牌")
add("002906",20260821,"CONTROL_CHANGE","控制权变更","华阳集团:华越投资拟转让28.30% 复牌")

# ===== MAJOR_CONTRACT（重大合同/中标）=====
add("002174",20261103,"MAJOR_CONTRACT","采购","游族网络:子公司签42.25亿GPU采购合同")
add("002236",20261102,"MAJOR_CONTRACT","中标","大华股份:子公司26.3亿中标中国移动超节点")
add("600869",20261102,"MAJOR_CONTRACT","中标","远东股份:10月中标/签约14.98亿订单")
add("603727",20261026,"MAJOR_CONTRACT","合同","博迈科:签24-30亿FPSO上部模块建造合同")
add("603139",20260916,"MAJOR_CONTRACT","合同","康惠股份:子公司签17.2亿算力+11.41亿采购")
add("688455",20261008,"MAJOR_CONTRACT","中标","科捷智能:中标约7500万波兰智能物流项目")
add("300209",20261015,"MAJOR_CONTRACT","合同","行云科技:重签33.57亿算力服务协议")
add("002169",20260831,"MAJOR_CONTRACT","合同","智光电气:斩获2.595亿独立储能EPC")
add("002973",20260831,"MAJOR_CONTRACT","中标","侨银股份:预中标5.28亿环卫项目")
add("600939",20260831,"MAJOR_CONTRACT","中标","重庆建工:中标两工程合计7.35亿")
add("601886",20260831,"MAJOR_CONTRACT","中标","江河集团:中标2.23亿沙特幕墙工程")
add("603990",20260907,"MAJOR_CONTRACT","中标","金冠电气:中标2044万输变电项目")
add("301085",20260831,"MAJOR_CONTRACT","合同","亚康股份:签17.45亿销售+17.03亿采购")
add("300393",20260831,"MAJOR_CONTRACT","中标","中来股份:子公司中标16.13亿户用光伏")
add("300062",20260831,"MAJOR_CONTRACT","中标","中能电气:中标中国能建3.84亿开关柜")
add("688009",20260924,"MAJOR_CONTRACT","中标","中国通号:7-8月中标9.76亿铁路项目")
add("300489",20261015,"MAJOR_CONTRACT","预中标","光智科技:子公司预中标11.14亿项目")
add("300880",20261030,"MAJOR_CONTRACT","预中标","迦南智能:预中标1.51亿国家电网")
add("688100",20261103,"MAJOR_CONTRACT","中标","威胜信息:10月合计中标2.31亿合同")
add("688616",20261103,"MAJOR_CONTRACT","中标","西力科技:中标1.75亿国家电网项目")
add("601179",20261103,"MAJOR_CONTRACT","中标","中国西电:子公司合计中标11.15亿国网")
add("600312",20261103,"MAJOR_CONTRACT","中标","平高电气:合计中标9.98亿国网项目")
add("603686",20261101,"MAJOR_CONTRACT","预中标","福龙马:10月预中标2个环卫项目2.24亿")

# ── 去重 + DAILY 覆盖校验 ──
def load_daily(code):
    f = DAILY / f"{code}.csv"
    if not f.exists(): return None
    try:
        import pandas as pd
        df = pd.read_csv(f).sort_values("日期").reset_index(drop=True)
    except Exception: return None
    if len(df) < 60: return None
    dates = df["日期"].astype(str).str.replace("-","").astype(int).to_numpy()
    close = df["收盘"].to_numpy(float); open_ = df["开盘"].to_numpy(float)
    high = df["最高"].to_numpy(float); low = df["最低"].to_numpy(float)
    vol = df["成交量"].to_numpy(float)
    return dates, open_, close, high, low, vol

seen = set(); kept=[]; dropped_cov=[]; dropped_dup=[]
for (sym,d,cat,kw,title) in E:
    key=(sym,d,title)
    if key in seen: dropped_dup.append((sym,d)); continue
    td = load_daily(sym)
    if td is None: dropped_cov.append((sym,d,cat)); continue
    seen.add(key); kept.append((sym,d,cat,kw,title))

print(f"[compile] 输入 {len(E)} 条；去重丢弃 {len(dropped_dup)}；无日线丢弃 {len(dropped_cov)}；保留 {len(kept)}")

# ── 写 catalyst_notices.jsonl（幂等覆盖）──
NOTICES.parent.mkdir(parents=True, exist_ok=True)
with open(NOTICES, "w", encoding="utf-8") as f:
    for (sym,d,cat,kw,title) in kept:
        f.write(json.dumps({"symbol":sym,"date":d,"category":cat,"keyword":kw,
                            "title":title,"source":"websearch-2026","link":""},
                           ensure_ascii=False)+"\n")
print(f"[compile] 已写 {NOTICES} ({len(kept)} 条)")

# ── 触发 cross_validate ──
sys.path.insert(0, str(CLAU))
sys.path.insert(0, str(CLAU/"research"))
from research import pre_event_leakage as PEL
print("\n=== CROSS VALIDATE (lookback=20) ===")
res = PEL.cross_validate_leakage_vs_catalyst(lookback=20)
print("\n[summary] total_valid=%s fired=%s recall=%.3f baseline=%.3f p=%.4f sig=%s"%(
    res["total"], res["fired_pre"], res["recall"], res["baseline"], res["p_value"], res["significant"]))
