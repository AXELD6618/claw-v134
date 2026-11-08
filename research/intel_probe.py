# -*- coding: utf-8 -*-
"""开源情报源可达性探针：机构/游资/基金/ETF 四类。只测通不通，不写盘。"""
import json
import ssl
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PROBES = [
    ("东财·龙虎榜明细", "https://datacenter-web.eastmoney.com/api/data/v1/get"
     "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageNumber=1&pageSize=3"
     "&source=WEB&client=WEB&sortColumns=TRADE_DATE&sortTypes=-1"),
    ("东财·龙虎榜机构席位", "https://datacenter-web.eastmoney.com/api/data/v1/get"
     "?reportName=RPT_ORGANIZATION_TRADE_DETAILS&columns=ALL&pageNumber=1&pageSize=3"
     "&source=WEB&client=WEB&sortColumns=TRADE_DATE&sortTypes=-1"),
    ("东财·主力资金流排行", "https://push2.eastmoney.com/api/qt/clist/get"
     "?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f62&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
     "&fields=f12,f14,f62,f184,f66,f69"),
    ("东财·个股资金流历史", "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
     "?lmt=0&klt=101&secid=0.000001&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57"),
    ("东财·ETF份额/规模", "https://push2.eastmoney.com/api/qt/clist/get"
     "?pn=1&pz=3&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:MK0021,b:MK0022,b:MK0023,b:MK0024"
     "&fields=f12,f14,f2,f3,f5,f6"),
    ("东财·基金净值/到期", "https://fund.eastmoney.com/data/rankhandler.aspx"
     "?op=ph&dt=kf&ft=all&rs=&gs=0&sc=1nzf&st=desc&sd=2026-01-01&ed=2026-10-18&qdii=&tabSubtype=,,,,,&pi=1&pn=3&dx=1"),
    ("深交所·龙虎榜", "https://www.szse.cn/api/report/ShowReport/data"
     "?SHOWTYPE=JSON&CATALOGID=1815_stock_snapshot&TABKEY=tab1&PAGENO=1"),
    ("深交所·融资融券", "https://www.szse.cn/api/report/ShowReport/data"
     "?SHOWTYPE=JSON&CATALOGID=1837_xxpl&TABKEY=tab1&PAGENO=1"),
    ("上交所·龙虎榜", "https://query.sse.com.cn/commonSoaQuery.do"
     "?sqlId=COMMON_SSE_ZQPZ_GPLB_MCJS_LHB_L&isPagination=true&pageHelp.pageSize=3"),
    ("新浪·个股资金流", "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php"
     "/MoneyFlow.ssi_ssfx_flzjtj?daima=sh600000"),
    ("新浪·ETF行情列表", "https://hq.sinajs.cn/list=sh510300,sh588000,sz159915"),
    ("巨潮·机构调研", "https://www.cninfo.com.cn/data20/companyOverview/getCompanyOverview"
     "?scode=000001"),
    ("腾讯·ETF快照", "https://qt.gtimg.cn/q=sh510300,sh588000"),
]


def probe(name, url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://data.eastmoney.com/",
        "Accept": "*/*",
    })
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            raw = r.read()
            txt = raw.decode("utf-8", "ignore") or raw.decode("gbk", "ignore")
            head = txt.strip().replace("\n", " ")[:170]
            ok = ("result" in txt and "null" not in head[:40]) or "json" in r.headers.get("Content-Type", "")
            print(f"[{'OK ' if r.status == 200 else 'HTTP'}] {name} | {r.status} | {len(raw)}B")
            print(f"       {head}")
            return r.status == 200 and len(raw) > 30
    except Exception as e:
        print(f"[FAIL] {name} | {type(e).__name__}: {str(e)[:110]}")
        return False


if __name__ == "__main__":
    res = {}
    for n, u in PROBES:
        res[n] = probe(n, u)
        print()
    print("=" * 60)
    print("可用:", [k for k, v in res.items() if v])
    print("不可用:", [k for k, v in res.items() if not v])
