#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · Layer B 收口脚本
==================================================================
输入:
  - 7 个 TDX screener 落盘文件 (pageSize=100, pageNo=1..7)  -> 700 只
  - 1 个内联 page 8 (pageNo=8, 14 只) -> 硬编码补录 (控股权字段留空, 供 Layer D 按需拉取)
  - Layer A: backtest_cache/spring_soe_layerA.json (安静压缩池, 407 只)

输出:
  - backtest_cache/spring_soe_layerB.json  : 714 只国资中小盘宇宙 (全量)
  - backtest_cache/spring_soe_layerAB.json : LayerA ∩ LayerB (中小盘安静国资弹簧池)

约定:
  - market: "1"=沪  "0"=深  "2"=北交所
  - 实际控制人类型/实际控制人 去掉 【@ ... @】 包裹
  - 总市值原值(元) -> 保留 int
  - 只做数据收口, 不编造任何无源字段
"""
import re, json, os

BASE = "C:/Users/SGM-AXELD/.workbuddy/projects/e-WorkBuddy_dot_workbuddy-毕方灵犀通达/0e7ba300-467c-4699-9596-2db7e5f65d7b/tool-results"
OUT  = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"

PAGE_FILES = [
    "chatcmpl-tool-a3d581c2d096471f.txt",  # p1
    "chatcmpl-tool-af79a2931a8c55f1.txt",  # p2
    "chatcmpl-tool-b81939b41c619577.txt",  # p3
    "chatcmpl-tool-bb2f5867c8139d3e.txt",  # p4
    "chatcmpl-tool-afec8bf8cd3e4106.txt",  # p5
    "chatcmpl-tool-95741cf49eafa1ff.txt",  # p6
    "chatcmpl-tool-9d9968d863d601d0.txt",  # p7
]

# page 8 (POS 701-714) 内联直录 - 短字段可靠, 控股关系留空供 Layer D 按需拉取
PAGE8 = [
    ("600561","1","江西长运","地方国资委","南昌市国有资产监督管理委员会",1621517696,5.70,0.88),
    ("920489","2","佳先股份","地方国资委","蚌埠市人民政府国有资产监督管理委员会",1499686528,10.99,2.33),
    ("920837","2","华原股份","地方国资委","广西壮族自治区人民政府国有资产监督管理委员会",1484503552,9.72,2.75),
    ("002207","0","*ST准油","地方国资委","克拉玛依市国有资产监督管理委员会",1446545792,5.52,-0.18),
    ("301167","0","建研设计","地方国资委","安徽省人民政府国有资产监督管理委员会",1420160000,12.68,0.63),
    ("920202","2","安达股份","地方国资委","湖州市人民政府国有资产监督管理委员会",1413440000,12.62,4.13),
    ("300621","0","维业股份","地方国资委","珠海市人民政府国有资产监督管理委员会",1356529664,6.52,0.93),
    ("002883","0","*ST中设","地方国资委","无锡市人民政府国有资产监督管理委员会",1336705664,8.56,0.94),
    ("920274","2","宏裕包材","地方国资委","宜昌市人民政府国有资产监督管理委员会",1220812928,15.01,3.88),
    ("600543","1","*ST莫高","地方国资委","甘肃省人民政府国有资产监督管理委员会",1220256000,3.80,0.53),
    ("920198","2","微创光电","地方国资委","湖北省人民政府国有资产监督管理委员会",1211842944,7.51,2.60),
    ("920753","2","天纺标","地方国资委","天津市人民政府国有资产监督管理委员会",1195016576,14.68,2.37),
    ("920964","2","润农节水","地方国资委","湖北省人民政府国有资产监督管理委员会",1191108480,4.56,2.47),
    ("920992","2","中科美菱","地方国资委","绵阳市国有资产监督管理委员会",1178182400,12.18,1.25),
]

def clean(s):
    if s is None: return None
    return re.sub(r'【@|@】', '', str(s)).strip()

def parse_block(txt):
    m = re.search(r'```json\s*(.*?)\s*```', txt, re.S)
    if not m: return []
    obj = json.loads(m.group(1))
    return obj.get("data", [])

def main():
    recs = []
    for fn in PAGE_FILES:
        p = os.path.join(BASE, fn)
        if not os.path.exists(p):
            print("MISSING", fn); continue
        data = parse_block(open(p, encoding="utf-8", errors="replace").read())
        recs.extend(data)
    print("parsed pages1-7 rows:", len(recs))

    # dedup pages 1-7 by sec_code
    by_code = {}
    for r in recs:
        c = r.get("sec_code")
        if c in by_code: continue
        mk = [k for k in r if "总市值" in k]
        mktcap = r.get(mk[0]) if mk else None
        by_code[c] = {
            "code": c,
            "market": r.get("market"),
            "sec_name": clean(r.get("sec_name")),
            "controller_type": clean(r.get("实际控制人类型")),
            "controller": clean(r.get("实际控制人")),
            "hold_rel": r.get("控股关系"),
            "mktcap": int(mktcap) if isinstance(mktcap,(int,float)) else None,
            "now_price": float(r.get("now_price")) if r.get("now_price") not in (None,"") else None,
            "chg": float(r.get("chg")) if r.get("chg") not in (None,"") else None,
        }
    print("unique pages1-7:", len(by_code))

    # append page 8
    added = 0
    for code, market, name, ctype, ctrl, mktcap, price, chg in PAGE8:
        if code in by_code:
            continue
        by_code[code] = {
            "code": code, "market": market, "sec_name": name,
            "controller_type": ctype, "controller": ctrl,
            "hold_rel": None, "mktcap": mktcap,
            "now_price": price, "chg": chg,
        }
        added += 1
    print("page8 added:", added)

    layerB = list(by_code.values())
    layerB.sort(key=lambda x: int(x["code"]))
    print("TOTAL LayerB:", len(layerB))

    # ---- Layer A ∩ Layer B ----
    la_path = os.path.join(OUT, "spring_soe_layerA.json")
    la = json.load(open(la_path, encoding="utf-8"))
    la_codes = {x["code"] for x in la}
    print("LayerA count:", len(la_codes))

    intersect = []
    for rec in layerB:
        if rec["code"] in la_codes:
            # merge Layer A metrics onto the SOE record
            am = next((x for x in la if x["code"] == rec["code"]), None)
            merged = dict(rec)
            merged["layerA"] = {k: am[k] for k in ("n","close","mean_c","turnover","vola","amp","drift","hi","lo","near_high") if k in am}
            intersect.append(merged)
    intersect.sort(key=lambda x: x["layerA"]["turnover"])
    print("INTERSECT (中小盘安静国资):", len(intersect))

    # persist
    json.dump(layerB, open(os.path.join(OUT,"spring_soe_layerB.json"),"w",encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(intersect, open(os.path.join(OUT,"spring_soe_layerAB.json"),"w",encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("WROTE spring_soe_layerB.json / spring_soe_layerAB.json")

    # quick preview of intersect (top 15 by lowest turnover)
    print("\n=== 中小盘安静国资弹簧池 (前 15) ===")
    for m in intersect[:15]:
        la = m["layerA"]
        print(f"{m['code']} {m['sec_name']:8s} 换手{la['turnover']:.3f}% 振幅{la['amp']:.3f} 偏离{la['drift']:.3f} 市值{ (m['mktcap'] or 0)/1e8:.1f}亿 {m['controller']}")

if __name__ == "__main__":
    main()
