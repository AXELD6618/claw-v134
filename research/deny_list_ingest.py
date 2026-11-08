#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
deny_list_ingest.py — R1 公告 ingestion：异动公告否认基本面 → 强制 C 级 5% 仓。

数据来源：TDX MCP `wenda_notice_query`（agent 层取数，Claw 自主运行时无 MCP 通道）。
本脚本只做「检测 + 写盘」，确保 verified + 可复核，不直连任何外部源。

数据流：
  agent: wenda_notice_query(symbol, bdate, edate, keywords="异动,异常波动,机器人")
        → 把返回落盘为 data/_notices_batch.json = [{code,name,date,title,body}, ...]
  本脚本: python research/deny_list_ingest.py --notices data/_notices_batch.json [--dry]
        → 检测命中 → 合并写入 data/position_deny_list.json（OOS 门控，空表不误杀）

检测逻辑（防误杀）：
  1) 公告必须是「异动 / 股票交易异常波动」类（带 ANOMALY_MARKERS）；
  2) 正文含主题关键词（机器人/灵巧手/丝杠/力矩电机…）；
  3) 且含否认基本面短语（收入占比小/未形成/不构成/不涉及…）。
  三者同时成立才判定 R1 命中，避免把「无未披露信息」式常规异动公告误杀。
"""
import os
import re
import json
import argparse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENY_PATH = os.path.join(HERE, "data", "position_deny_list.json")
NOTICES_DEFAULT = os.path.join(HERE, "data", "_notices_batch.json")

ANOMALY_MARKERS = ["异动", "异常波动", "股票交易异常波动", "交易异常波动", "波动公告", "风险提示"]
THEME_KW = ["机器人", "人形机器人", "灵巧手", "减速器", "丝杠", "腱绳", "力矩电机", "空心杯",
            "电机", "模组", "自动化", "人工智能", "AI", "算力", "半导体", "智能", "具身"]
DENY_PHRASES = ["收入占比", "营业收入占比", "未形成", "未产生", "不构成", "不涉及", "暂无",
                "占比较小", "占比较低", "对公司业绩", "对公司经营业绩", "影响较小", "影响极小",
                "不会产生重大影响", "无相关业务", "尚未形成", "未实际应用", "营业收入较小",
                "占比不到", "占比不足", "占总营业收入的比例", "占比极小", "未开展",
                "未布局", "未产生业务收入", "未产生相关收入", "未涉及"]


def detect_deny(title, body=""):
    """返回命中原因字符串，否则 None。"""
    t = (title or "")
    text = t + "\n" + (body or "")
    is_anomaly = any(m in t for m in ANOMALY_MARKERS) or any(
        m in text for m in ("股票交易异常波动", "异常波动", "异动公告"))
    if not is_anomaly:
        return None
    if not any(k in text for k in THEME_KW):
        return None
    for ph in DENY_PHRASES:
        if ph in text:
            return _summarize(text, ph)
    return None


def _summarize(text, ph):
    # 锚定机器人主题上下文，只取该业务营收占比，避免误抓「控制电机业务占比88%」等其他业务
    pct = "?"
    # 容忍「营收占总营业收入的比例约0.1%」「对公司经营业绩影响极小」等变体：关键词后至多 20 个非数字非%字符内取首个比例
    _pat = r'(?:收入占比|营业收入|营收|占比)[^0-9%]{0,20}?([0-9]+(?:\.[0-9]+)?)\s*%'
    for kw in ("人形机器人", "机器人"):
        idx = text.find(kw)
        if idx >= 0:
            window = text[idx:idx + 200]
            m = re.search(_pat, window)
            if m:
                pct = m.group(1)
                break
    if pct == "?":
        m = re.search(_pat, text)
        if m:
            pct = m.group(1)
    return "异动公告否认基本面(主题收入占比%s%%)" % pct


def ingest(notices):
    """notices: list of {code, name, date, title, body/summary}。返回 {code: reason}。"""
    matches = {}
    for n in notices:
        code = str(n.get("code") or "").strip()
        if not code:
            continue
        r = detect_deny(n.get("title", ""), n.get("body") or n.get("summary") or "")
        if r:
            matches[code] = r
    return matches


def write_deny_list(matches, dry=False, rebuild=False):
    cur = {}
    if not rebuild:
        if os.path.exists(DENY_PATH):
            try:
                cur = json.load(open(DENY_PATH, encoding="utf-8")).get("codes") or {}
            except Exception:
                cur = {}
    # rebuild=True：cur 保持 {}，下面 update 即「以本次命中替换」（14日窗口 TTL）
    cur.update(matches)
    out = {
        "_doc": "R1 deny-list：异动公告否认基本面 → 强制 C 级 5% 仓。空表=不触发（OOS门控，不误杀）。"
                "写入须经 verified 流程（agent 经 TDX MCP wenda_notice_query 取数 → research/deny_list_ingest.py 检测），"
                "禁止凭叙事手改。格式：{\"codes\": {\"603XXX\": \"异动公告否认基本面(主题收入占比<1%)\"}}。"
                "【主机例外】取数为 MCP 依赖任务，按部署铁律例外在 WorkBuddy 会话运行（毕方灵犀通达无 MCP 通道）；"
                "产物写共享仓库 data/ 供 14:50 推送消费。推送侧 _load_deny_list 有 STALE_DAYS=2 新鲜度守卫，失联时告警不静默信任。",
        "codes": cur,
    }
    if not dry:
        json.dump(out, open(DENY_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notices", default=NOTICES_DEFAULT, help="MCP 取数落盘的公告批次 JSON")
    ap.add_argument("--dry", action="store_true", help="只检测不写盘")
    ap.add_argument("--rebuild", action="store_true", help="以本次命中重建 deny-list（替换而非累积；14日窗口 TTL）")
    ap.add_argument("--test", action="store_true", help="跑内置已知样本自检")
    a = ap.parse_args()

    if a.test:
        samples = [
            ("兆威机电机器人收入占比不足1%不构成业绩影响", "股票交易异常波动公告：公司机器人业务营业收入占比不足1%，未形成相关收入，对公司业绩无重大影响"),
            ("鸣志电器关于股票交易异常波动的公告", "公司机器人相关业务收入占比约9%，目前对该板块业务布局较小，不会产生重大影响"),
            ("赛福天股票交易异常波动公告", "公司未开展人形机器人相关业务，未形成腱绳产品收入"),
            ("某司异动公告", "经自查，公司无应披露而未披露的重大信息，经营正常"),  # 应不命中（无主题+无否认）
            ("某司关于签订大合同的公告", "公司与XX签订10亿元机器人产线订单，收入占比将显著提升"),  # 应不命中（非异动否认）
            ("模塑科技股票交易异常波动公告", "公司目前机器人外覆盖件业务营收占总营业收入的比例约0.1%，对公司经营业绩影响极小"),  # 应命中（变体短语）
        ]
        ok = True
        for title, body in samples:
            r = detect_deny(title, body)
            hit = r is not None
            exp = title.startswith(("兆威", "鸣志", "赛福", "模塑"))
            status = "OK" if hit == exp else "FAIL"
            if hit != exp:
                ok = False
            print("[test] %s 期望命中=%s 实得=%s | %s" % (status, exp, hit, r))
        print("[test] %s" % ("ALL PASS" if ok else "HAS FAIL"))
        return 0 if ok else 1

    if not os.path.exists(a.notices):
        print("[deny_ingest] 公告批次文件缺失: %s（先经 TDX MCP wenda_notice_query 取数落盘）" % a.notices)
        return 2
    notices = json.load(open(a.notices, encoding="utf-8"))
    matches = ingest(notices)
    out = write_deny_list(matches, dry=a.dry, rebuild=a.rebuild)
    print("[deny_ingest] 检测 %d 条公告 → 命中 %d 只：%s%s%s" % (
        len(notices), len(matches), matches, " (dry)" if a.dry else "",
        " [rebuild]" if a.rebuild else ""))
    if not a.dry:
        print("[deny_ingest] 已写入 %s" % DENY_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
