# -*- coding: utf-8 -*-
"""情报源深挖 v2：解析字段、试探游资营业部/北向/ETF/基金到期报表。"""
import json
import ssl
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, ref="https://data.eastmoney.com/", timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def em(report, extra="", pz=2):
    return ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=" + report +
            "&columns=ALL&pageNumber=1&pageSize=%d&source=WEB&client=WEB%s" % (pz, extra))


CASES = [
    ("龙虎榜个股明细", em("RPT_DAILYBILLBOARD_DETAILSNEW",
                     "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("机构专用席位", em("RPT_ORGANIZATION_TRADE_DETAILS",
                    "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("营业部买入明细(游资)", em("RPT_BILLBOARD_DAILYDETAILSBUY",
                        "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("营业部卖出明细(游资)", em("RPT_BILLBOARD_DAILYDETAILSSELL",
                        "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("北向资金个股", em("RPT_MUTUAL_STOCK_NORTHBOUND", "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("融资融券个股", em("RPT_MARGIN_DAILY_NEW", "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("ETF 基金规模", em("RPT_FUND_ETF_SCALE", "&sortColumns=END_DATE&sortTypes=-1")),
    ("ETF 份额变动", em("RPT_FUND_ETF_SHARECHANGE", "&sortColumns=TRADE_DATE&sortTypes=-1")),
    ("基金到期清算", em("RPT_FUND_LIQUIDATION", "")),
    ("基金基本概况", em("RPT_FUND_BASICINFO", "")),
]

print("=" * 70)
ok = []
for name, url in CASES:
    try:
        raw = get(url)
        j = json.loads(raw)
        res = j.get("result")
        if not res:
            print(f"[EMPTY] {name}  keys={list(j.keys())[:4]}")
            continue
        data = res.get("data") or []
        if not data:
            print(f"[NO-ROW] {name}")
            continue
        cols = list(data[0].keys())
        print(f"[OK ] {name}  pages={res.get('pages')} cols={len(cols)}")
        print(f"      {cols}")
        print(f"      sample: { {k: data[0][k] for k in cols[:8]} }")
        ok.append((name, res.get("pages"), cols))
    except Exception as e:
        print(f"[FAIL] {name} | {type(e).__name__}: {str(e)[:90]}")
    print()

print("=" * 70)
print("深交所（加 Referer 重试）")
for name, url in [
    ("深交所·龙虎榜", "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1815_stock_snapshot&TABKEY=tab1&PAGENO=1"),
    ("深交所·融资融券", "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1837_xxpl&TABKEY=tab1&PAGENO=1"),
    ("深交所·基金份额", "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1945_fund_scale&TABKEY=tab1&PAGENO=1"),
]:
    try:
        raw = get(url, ref="https://www.szse.cn/disclosure/marketdata/index.html")
        print(f"[OK ] {name} {len(raw)}B :: {raw[:130]}")
    except Exception as e:
        print(f"[FAIL] {name} | {type(e).__name__}: {str(e)[:90]}")

print("\n" + "=" * 70)
print("可用报表:", [x[0] for x in ok])
