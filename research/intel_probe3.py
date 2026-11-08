# -*- coding: utf-8 -*-
"""情报源探针 v3：机构调研 / 增减持 / 十大流通股东 / ETF份额 / 基金到期。"""
import json
import ssl
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def em(report, page=1, pz=2, extra=""):
    q = {"reportName": report, "columns": "ALL", "pageNumber": str(page),
         "pageSize": str(pz), "source": "WEB", "client": "WEB"}
    return BASE + "?" + urllib.parse.urlencode(q, safe="") + extra


def get(url, ref="https://data.eastmoney.com/", timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": ref, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


CASES = [
    ("机构调研", em("RPT_ORG_SURVEY")),
    ("机构调研NEW", em("RPT_ORG_SURVEYNEW")),
    ("股东增持", em("RPT_SHARE_HOLDER_INCREASE")),
    ("股东减持", em("RPT_SHARE_HOLDER_DECREASE")),
    ("十大流通股东", em("RPT_F10_EH_FREEHOLDERS")),
    ("十大股东", em("RPT_F10_EH_HOLDERS")),
    ("股东户数", em("RPT_HOLDERNUMLATEST")),
    ("限售解禁", em("RPT_LIFT_STAGE")),
    ("ETF基金列表", em("RPT_FUND_ETF_BASICINFO")),
    ("ETF净值", em("RPT_FUND_ETF_NAV")),
    ("基金规模", em("RPT_FUND_SCALE")),
    ("基金清盘", em("RPT_FUND_CLEAR")),
    ("大宗交易", em("RPT_DATA_BLOCKTRADE")),
    ("融资融券汇总", em("RPT_MARGIN_MARKETSUM")),
    ("北向持股", em("RPT_MUTUAL_HOLD_DET")),
]

print("=" * 72)
oklist = []
for name, url in CASES:
    try:
        j = json.loads(get(url))
        res = j.get("result")
        if not res:
            print(f"[EMPTY] {name}")
            continue
        data = res.get("data") or []
        if not data:
            print(f"[NO-ROW] {name}")
            continue
        cols = list(data[0].keys())
        print(f"[OK  ] {name}  pages={res.get('pages')} cols={len(cols)}")
        print(f"        {cols[:14]}")
        oklist.append(name)
    except Exception as e:
        print(f"[FAIL] {name} | {type(e).__name__}: {str(e)[:70]}")

print("\n" + "=" * 72)
print("可用:", oklist)

# 基金/ETF 另寻：天天基金公开接口
print("\n--- 天天基金/东财基金接口 ---")
for name, url, ref in [
    ("天天基金全量列表", "https://fund.eastmoney.com/js/fundcode_search.js",
     "https://fund.eastmoney.com/"),
    ("ETF实时行情(东财)", "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3&po=1&np=1"
     "&fltt=2&invt=2&fid=f3&fs=b:MK0021&fields=f12,f14,f2,f3,f5,f6,f21", "https://quote.eastmoney.com/"),
    ("ETF行情(腾讯批量)", "https://qt.gtimg.cn/q=sh510300,sh510500,sh588000,sz159915", "https://gu.qq.com/"),
]:
    try:
        raw = get(url, ref=ref)
        txt = raw.decode("utf-8", "ignore")
        print(f"[OK  ] {name} {len(raw)}B :: {txt[:120]}")
    except Exception as e:
        print(f"[FAIL] {name} | {type(e).__name__}: {str(e)[:70]}")
