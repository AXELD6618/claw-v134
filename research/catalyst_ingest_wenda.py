#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""催化/事件层 · wenda 新闻源摄入生产者（生产级）。

定位（与 catalyst_watch.py 同源，科学结论见预点火三重证伪）：
  连续涨停启动属外生催化 + 突破后均值回归，市场数据 ex-ante 不可预测。
  催化层只做【可见性扩展 + 早期预警】，绝不新增模型因子、绝不充当买入信号。

架构（遵循 live_tdx_sources.py 既有范式）：
  wenda_news_query 是 MCP-only 工具，无独立 Python SDK；故"实际抓取"由
  MCP-capable runner（agent / 自动化 agent 任务）完成，结果落为
  backtest_cache/catalyst_wenda_raw_{date}.json（结构：{code: wenda_resp}）；
  本模块消费该 raw 文件：解析 → 新鲜度过滤 → 打分 → 写入 feed → 合并进
  backtest_cache/catalyst_watchlist.json（asof=date，过期清理）。

wenda 返回结构：{"ok":true, "data":[[标题,...,摘要], ...]}，表头行=["标题",...,"摘要"]。

用法：
  # 1) 生成观测宇宙候选批次（供 agent 批量调 wenda 用，输出 code/name 列表）
  python research/catalyst_ingest_wenda.py --emit-batch 24 --date 2026-11-06 \
        --snapshot tail_raw/2026-11-06_quotes.json --batch-out batch.json
  # 2) agent 对 batch.json 每只调 wenda_news_query(bdate,edate)，结果存 raw.json
  # 3) 解析+合并
  python research/catalyst_ingest_wenda.py --date 2026-11-06 --raw catalyst_wenda_raw_2026-11-06.json --merge
"""
import os, sys, json, argparse, datetime, re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "backtest_cache")
OUT = os.path.join(CACHE, "catalyst_watchlist.json")

# —— 新鲜催化方向词典（仅用于观察分级，不构成买卖建议）——
BULL = ["中标", "签约", "订单", "扩产", "投产", "量产", "回购", "增持", "预增", "业绩预增",
        "扭亏", "合作", "获批", "认证", "突破", "新产品", "涨价", "提价", "中标公告", "项目中标",
        "签订", "斩获", "利好", "补贴", "政策", "规划", "定点", "供货", "独家", "首条"]
BEAR = ["减持", "解禁", "诉讼", "预减", "亏损", "停牌", "警示", "处罚", "商誉减值", "暴雷",
        "下滑", "下调", "降级", "问询", "立案", "违约", "终止", "撤回", "风险", "利空", "停产"]


def _extract_rows(data2d):
    """从 wenda 的 data（2D 数组）提取 (标题, 摘要, 日期串) 行，跳过表头行。

    wenda 行结构固定为 [标题, 时间, 链接, 来源, 摘要]，日期在【时间列(索引1)】，
    必须从此处解析新鲜度，而非从标题/摘要（多数标题不含日期）。
    """
    if data2d is None:
        return []
    if isinstance(data2d, dict):
        data2d = data2d.get("data", data2d.get("rows", []))
    rows = []
    for r in data2d:
        if not isinstance(r, (list, tuple)) or len(r) < 5:
            continue
        if r[0] == "标题" and r[4] == "摘要":
            continue
        rows.append((str(r[0] or ""), str(r[4] or ""), str(r[1] or "")))
    return rows


def _parse_date(s):
    m = re.search(r"(\d{4}-\d{2}-\d{2})", str(s or ""))
    if m:
        try:
            return datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except Exception:
            return None
    return None


def parse_wenda_response(resp, code, date, days=5, name=""):
    """解析单只票的 wenda 响应，返回新鲜催化项 dict 或 None。

    返回：{"code","name","score","text","src","items":[{headline,dir,bdate}]}
    score：净方向分 = (利好命中 - 利空命中) 经 tanh 压到 [-1,1]，仅分级用。
    items：距交易日 <=days 的新鲜条目（陈旧公告不触发当日观测）。
    """
    if not isinstance(resp, dict):
        return None
    if not resp.get("ok", True):
        return None
    rows = _extract_rows(resp.get("data"))
    if not rows:
        return None
    try:
        anchor = datetime.date.fromisoformat(date)
    except Exception:
        anchor = datetime.date.today()
    fresh = []
    for title, summary, datestr in rows:
        blob = title + " " + summary
        dt = _parse_date(datestr) or _parse_date(title) or _parse_date(summary)
        if dt is None or (anchor - dt).days <= days:
            bull = sum(1 for t in BULL if t in blob)
            bear = sum(1 for t in BEAR if t in blob)
            if bull == 0 and bear == 0:
                # 无明确方向词但也算"有新闻催化"，记为中性观察
                d_ = 0
            else:
                d_ = 1 if bull >= bear else -1
            fresh.append({"headline": title[:60], "dir": d_,
                          "bdate": dt.isoformat() if dt else ""})
    if not fresh:
        return None
    bull_n = sum(1 for x in fresh if x["dir"] > 0)
    bear_n = sum(1 for x in fresh if x["dir"] < 0)
    net = bull_n - bear_n
    score = float(round(net / (1 + abs(net)), 3))  # tanh-like 压到 (-1,1)
    # text：优先取最新利好头条，否则取首条
    headline = ""
    for x in fresh:
        if x["dir"] > 0:
            headline = x["headline"]; break
    if not headline:
        headline = fresh[0]["headline"]
    return {"code": str(code), "name": name, "score": score,
            "text": headline, "src": "wenda_news",
            "bull": bull_n, "bear": bear_n, "items": fresh}


def build_feed(raw_map, date, names=None, days=7):
    """消费 {code: wenda_resp} → {code: parsed}。"""
    names = names or {}
    feed = {}
    for code, resp in raw_map.items():
        nm = names.get(str(code), "")
        p = parse_wenda_response(resp, code, date, days=days, name=nm)
        if p:
            p["asof"] = date
            feed[str(code)] = p
    return feed


def merge_to_watchlist(date, feed, out_path=OUT):
    """合并 feed 进 catalyst_watchlist.json。

    语义：feed 是当日(asof==date)催化观测的【权威来源】——先移除所有旧 asof==date 项
    （含历史脏数据/重复运行残留），再整体写入 feed，实现当日全量刷新而非累加。
    非当日项（理论上不存在，ignition 由 ignition_watchlist 单独管）原样保留。
    """
    try:
        wl = json.load(open(out_path, encoding="utf-8"))
    except Exception:
        wl = {}
    # 移除当日旧项（全量刷新，避免脏数据累加）
    wl = {k: v for k, v in wl.items() if v.get("asof") != date}
    for code, info in feed.items():
        info = dict(info)
        info["asof"] = date
        wl[code] = info
    json.dump(wl, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return wl


def emit_batch(n, snapshot_path, date, exclude_limit_up=True):
    """从快照选 n 只作为 wenda 查询批次：优先非涨停且近期有活跃度（|chg|*量比）的票。"""
    try:
        d = json.load(open(snapshot_path, encoding="utf-8"))
    except Exception as e:
        print("快照读取失败: %s" % e); return []
    arr = d.get("data") or d.get("stocks") or (d if isinstance(d, list) else [])
    cand = []
    for r in arr:
        code = str(r.get("sec_code") or r.get("code") or "")
        name = r.get("sec_name") or r.get("name") or ""
        if not code:
            continue
        chg = float(r.get("chg_pct") or 0)
        vr = float(r.get("vol_ratio") or 1)
        if exclude_limit_up and abs(chg) >= 9.5:
            continue
        cand.append((code, name, abs(chg) * vr))
    cand.sort(key=lambda x: -x[2])
    # 均匀抽取 n 只，避免全挤在头部
    if len(cand) > n:
        step = len(cand) / n
        picked = [cand[int(i * step)] for i in range(n)]
    else:
        picked = cand
    return [{"code": c, "name": nm} for c, nm, _ in picked]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--raw", default="", help="预抓的 wenda 原始响应 {code: resp}")
    ap.add_argument("--snapshot", default="", help="快照路径（取 name 兜底）")
    ap.add_argument("--merge", action="store_true", help="解析后合并进 catalyst_watchlist.json")
    ap.add_argument("--days", type=int, default=7, help="新鲜度窗口（距交易日 <=days 的条目保留）")
    ap.add_argument("--emit-batch", type=int, default=0, help="生成 wenda 查询批次（只输出 code/name）")
    ap.add_argument("--batch-out", default="", help="批次输出路径")
    a = ap.parse_args()

    if a.emit_batch:
        sp = a.snapshot or os.path.join(HERE, "tail_raw", "%s_quotes.json" % a.date)
        batch = emit_batch(a.emit_batch, sp, a.date)
        if a.batch_out:
            json.dump(batch, open(a.batch_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            print("emit-batch: %d 只 → %s" % (len(batch), a.batch_out))
        else:
            print(json.dumps(batch, ensure_ascii=False, indent=2))
        return len(batch)

    if not a.raw:
        print("需指定 --raw（预抓 wenda 响应）或 --emit-batch")
        return 1
    raw = json.load(open(a.raw, encoding="utf-8"))
    names = {}
    if a.snapshot:
        try:
            sd = json.load(open(a.snapshot, encoding="utf-8"))
            sarr = sd.get("data") or sd.get("stocks") or (sd if isinstance(sd, list) else [])
            for r in sarr:
                names[str(r.get("sec_code") or r.get("code") or "")] = r.get("sec_name") or r.get("name") or ""
        except Exception:
            pass
    feed = build_feed(raw, a.date, names, days=a.days)
    print("date=%s 解析出新鲜催化票 %d / 查询 %d (days=%d)" % (a.date, len(feed), len(raw), a.days))
    for c, v in list(feed.items())[:30]:
        print("  %s %s score=%s bull=%s bear=%s | %s" % (
            c, v.get("name", ""), v.get("score"), v.get("bull"), v.get("bear"), v.get("text")))
    if a.merge:
        wl = merge_to_watchlist(a.date, feed)
        print("已合并 → %s（在榜 %d 只）" % (OUT, len(wl)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
