#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
妖股特征工程 · 个股→板块映射构建器（①全市场行业/题材映射，解锁板块联动 4.7×）

架构：agent 侧用 tdx_indicator_select(message="{名称} 概念板块和产业链") 拉原始数据，
      落 research/raw_sectors/{code}.json（即 tdx_indicator_select 返回列表）；
      本脚本聚合为 data/industry_map/stock_to_sector.json：
        {code: {"name":..., "sectors":[概念板块..., 产业链板块...]}}

为什么走逐股 tdx_indicator_select 而非板块成分枚举：
  tdx_api_data 的板块成分 entry(TdxSharePCCW.tdxf10_bk_cpbd) 在本端点返回
  503「功能未注册」；而逐股 sector 标签稳定可用，且恰好够算「板块内涨停家数」
  （已知每只票的板块 -> 某日 t-1 统计同板块其他票涨停数）。

复用：通达信 tdx_indicator_select。无需自建行业表。
"""
import json, os, glob

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(PROJ, "research", "raw_sectors")
OUT = os.path.join(PROJ, "data", "industry_map", "stock_to_sector.json")


def parse(resp):
    items = resp if isinstance(resp, list) else [resp]
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or it.get("sec_code") or "")
        name = it.get("sec_name") or it.get("name") or ""
        cons = it.get("所属概念板块") or ""
        chains = it.get("所属产业链板块") or ""
        sectors = [s.strip() for s in (cons.split(",") if cons else [])]
        sectors += [s.strip() for s in (chains.split(",") if chains else [])]
        sectors = [s for s in sectors if s]
        if code:
            out[code] = {"name": name, "sectors": sectors}
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    agg = {}
    # 合并已存在的映射（支持增量回填）
    if os.path.exists(OUT):
        try:
            agg = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            agg = {}
    files = sorted(glob.glob(os.path.join(RAW, "*.json")))
    print(f"[sector] {len(files)} raw files in {RAW}")
    n_ok = 0
    for fp in files:
        code = os.path.splitext(os.path.basename(fp))[0]
        try:
            resp = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            print(f"  skip {code}: {e}")
            continue
        if isinstance(resp, dict) and "resp" in resp:
            resp = resp["resp"]
        got = parse(resp)
        if code in got:
            agg[code] = got[code]
            n_ok += 1
        else:
            print(f"  skip {code}: no sector tag")
    json.dump(agg, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[sector] merged {n_ok}/{len(files)} -> {OUT} (total {len(agg)} stocks)")


if __name__ == "__main__":
    main()
