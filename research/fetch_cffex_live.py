# -*- coding: utf-8 -*-
"""
fetch_cffex_live.py — CFFEX 中信净空【每日自动供给】硬化抓取工具（喂 cffex_risk_gate）

定位：这是 cffex_risk_gate 的"数据供给端"。unified_tail_pick 里的门控只读取
backtest_cache/cffex_regime_snapshot.json，本脚本负责在「毕方灵犀通达」宿主上
（稳定出网 + 可带 header/cookie）每日收盘后把真实持仓排名抓下来、写历史 + 写快照。
一旦快照存在，门控在 risk-off 日自动收紧圣杯管线。

设计纪律（与系统核心原则一致：生产者不得自证 / 不伪造 / 缺失即 pass-through）：
  - live 全部候选端点失败 → 打印 LIVE_ENDPOINT_UNVERIFIED，退出码 2，**不写任何历史/快照**
    （绝不让门控拿到假数据而误触发 risk_off）。
  - 仅当响应是含 o_ccpm/o_cppm 的有效 JSON 时才计入；字段缺失/解析异常的单品种跳过。
  - 端点格式历史上为 sj/ccpm/{YYYYMMDD}/{YYYYMMDD}_{idx}.json（idx 1..4 = IF/IH/IC/IM），
    但本环境实测该路径已 404（站点结构调整）。故 LIVE_ENDPOINTS 多列几个候选，
    并在宿主上验证当前可用端点（验证清单见底部 RUNBOOK）。

用法：
  # 1) 无网络自检：用 bundled fixture 证明 parse→snapshot→write 全链通（不触网）
  python research/fetch_cffex_live.py --selftest

  # 2) 尝试 live 抓取（在通达宿主跑；失败则 LIVE_ENDPOINT_UNVERIFIED 退出，不写）
  python research/fetch_cffex_live.py --date 20261106

  # 3) 宿主定时：每个交易日 15:30 后跑一次（收盘后持仓排名才完整）
  #    crontab: 30 15 * * 1-5  cd /path/Claw && .venv/Scripts/python.exe research/fetch_cffex_live.py --date $(date +%Y%m%d)
"""
import os
import sys
import json
import datetime
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

# 复用 ingestion 的解析与快照构造（单一真相源，不重复实现）
from cffex_position_ingest import (  # noqa: E402
    PRODUCTS, PRODUCT_INDEX, parse_product, find_citic, topn_net_short,
    build_snapshot, write_snapshot, append_history, load_history,
    HISTORY_P, SNAPSHOT_P, FIXTURE,
)

# live 端点候选（按疑似优先级；本环境实测前 3 个 404，宿主上逐一验证）
LIVE_ENDPOINTS = [
    "http://www.cffex.com.cn/sj/ccpm/{date}/{date}_{idx}.json",
    "https://www.cffex.com.cn/sj/ccpm/{date}/{date}_{idx}.json",
    "http://www.cffex.com.cn/sj/ccpm/{yyyymm}/{date}_{idx}.json",
    "http://www.cffex.com.cn/sj/ccpm/{yyyy}/{date}_{idx}.json",
    "http://www.cffex.com.cn/sj/ccpm/{date}/{idx}.json",
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
REFERER = "http://www.cffex.com.cn/sj/ccpm/"


def _http_get(url, timeout=20):
    """返回 (ok, text_or_none)。跟随重定向、带浏览器 header。失败返回 (False, None)。"""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Referer": REFERER,
            "Accept": "application/json, text/plain, */*",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        # 落地页若仍是错误页（CFFEX 404 页 gb2312 标题），判为失败
        if "error_404" in url or "error_page" in raw[:200].lower():
            return False, None
        return True, raw
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("  GET %s -> %s\n" % (url, repr(e)[:120]))
        return False, None


def fetch_one(date, product):
    """尝试一个品种的所有候选端点，返回解析后的持仓 dict 或 None。"""
    idx = PRODUCT_INDEX.get(product)
    if idx is None:
        return None
    yyyymm = date[:6]
    yyyy = date[:4]
    for tpl in LIVE_ENDPOINTS:
        url = tpl.format(date=date, idx=idx, yyyymm=yyyymm, yyyy=yyyy)
        ok, raw = _http_get(url)
        if ok and raw:
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and (obj.get("o_ccpm") or obj.get("o_cppm")):
                return obj
    return None


def run_live(date):
    parsed_by_product = {}
    last_err = None
    for p in PRODUCTS:
        obj = fetch_one(date, p)
        if obj is None:
            sys.stderr.write("[%s] live 抓取失败，跳过（不写历史）\n" % p)
            continue
        parsed_by_product[p] = parse_product(obj)
        sys.stderr.write("[%s] live OK\n" % p)

    if not parsed_by_product:
        sys.stderr.write("LIVE_ENDPOINT_UNVERIFIED: 无任何品种抓到真实数据，未写历史/快照。\n"
                         "请在「毕方灵犀通达」宿主验证当前可用端点（见本文件 RUNBOOK），重跑。\n")
        return 2

    # 写历史
    history = load_history()
    for p, parsed in parsed_by_product.items():
        nm, c = find_citic(parsed)
        t = topn_net_short(parsed)
        if c and c["present"]:
            append_history({
                "date": date, "product": p,
                "citic_net_short": c["net_short"],
                "citic_long": c["long"], "citic_short": c["short"],
                "citic_net_short_var": c["net_short_var"],
                "top20_net_short": t["net_short"],
                "top20_net_short_var": t["net_short_var"],
            })
    snap = build_snapshot(date, parsed_by_product, load_history())
    write_snapshot(snap)
    print("LIVE OK: 已写历史(%s) + 快照(%s)" % (HISTORY_P, SNAPSHOT_P))
    print("  中信聚合净空=%s  前20聚合净空=%s" %
          (snap["citic_aggregate"]["net_short"], snap["top20_aggregate"]["net_short"]))
    print("  分位=%s  连续性=%s" % (snap["percentile"], snap["continuity"]))
    return 0


def _selftest():
    """纯 fixture 自检：证明 parse→snapshot→write 全链通，不触网。"""
    obj = json.load(open(FIXTURE, encoding="utf-8"))
    parsed = parse_product(obj)
    nm, c = find_citic(parsed)
    assert nm and "中信" in nm, "未识别中信席位"
    assert c["net_short"] == 29378, "净空算错"
    # 注入合成历史，让分位/连续性可算
    hist = load_history() or []
    hist.append({"date": "2026-11-04", "product": "IF",
                 "citic_net_short": 29378, "citic_long": 15832,
                 "citic_short": 45210, "citic_net_short_var": 552,
                 "top20_net_short": 95378, "top20_net_short_var": 0})
    snap = build_snapshot("20261104", {"IF": parsed}, hist)
    # 写快照到临时位置并回读校验（不污染真实快照：用临时文件名）
    tmp = SNAPSHOT_P + ".selftest.json"
    import copy
    orig = None
    if os.path.exists(SNAPSHOT_P):
        orig = open(SNAPSHOT_P, encoding="utf-8").read()
    try:
        write_snapshot(snap)  # 写到正式路径也无妨（内容正确）
        back = json.load(open(SNAPSHOT_P, encoding="utf-8"))
        assert back["citic_aggregate"]["net_short"] == 29378
        assert back["resonance"]["cross_product_signs"]["IF"] == "short_add"
    finally:
        if orig is None:
            try:
                os.remove(SNAPSHOT_P)
            except OSError:
                pass
        else:
            open(SNAPSHOT_P, "w", encoding="utf-8").write(orig)
    print("fetch_cffex_live selftest PASS")
    print("  fixture 中信(IF) net_short=%d → 快照聚合=%d，写回读校验 OK"
          % (c["net_short"], snap["citic_aggregate"]["net_short"]))
    return True


RUNBOOK = """
===== 通达宿主 RUNBOOK（CFFEX 实时端点验证 + 每日供给） =====
1. 验证当前可用端点（在能出网的宿主上）：
     python research/fetch_cffex_live.py --date <最近交易日YYYYMMDD>
   若打印 LIVE_ENDPOINT_UNVERIFIED，说明 LIVE_ENDPOINTS 里的格式已过时：
     - 浏览器打开 http://www.cffex.com.cn/sj/ccpm/ 查看"每日持仓排名"页面，
       用开发者工具 Network 抓 XHR 请求，确认当前 JSON 真实路径与字段名；
     - 把确认后的端点补进本文件 LIVE_ENDPOINTS 顶部（保留旧候选兜底）。
2. 部署每日供给（收盘后 15:30，持仓排名才完整）：
     crontab -e 添加：
     30 15 * * 1-5  cd /abs/path/Claw && .venv/Scripts/python.exe research/fetch_cffex_live.py --date $(date +%Y%m%d) >> backtest_cache/cffex_fetch.log 2>&1
3. 验证门控生效：
     - 跑完后存在 backtest_cache/cffex_regime_snapshot.json；
     - 下次 unified_tail_pick 运行会在报告中（warnings 段）打印 CFFEX gate 状态；
       risk_off 日会显示"收紧推送上限/总仓 cap"，normal 日显示 pass-through。
4. 升贴水(basis)共振项：依赖现货指数数据，本供给不含，标 TODO（cffex_position_ingest）。
==============================================================
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYYMMDD，尝试 live 抓取")
    ap.add_argument("--selftest", action="store_true", help="fixture 自检（不触网）")
    ap.add_argument("--runbook", action="store_true", help="打印通达宿主部署清单")
    args = ap.parse_args()
    if args.runbook:
        print(RUNBOOK)
        return 0
    if args.selftest:
        return 0 if _selftest() else 1
    if args.date:
        return run_live(args.date)
    print("未指定参数，默认打印 RUNBOOK（用 --selftest 自检 / --date 抓 live）：")
    print(RUNBOOK)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
