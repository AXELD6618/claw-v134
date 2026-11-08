#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
弹簧压到底 · 引信触发研判预判（两模式）
==================================================================
当 Layer D 检测到「新引信」（弹簧池个股出现资产重组/资产注入/国企改革等催化剂），
本模块产出研判预判，分两种运行模式：

模式 A · realtime（交易时段实时监测预判）
  给 (code, catalyst[, 实时行情]) → 计算
    引信强度(催化剂类型权重) × 弹簧紧度(scr_pctl) × 实时确认(量能/突破)
  → 分级预判（强启动 / 弱确认 / 观望）+ 文字研判，并推送微信。
  由「交易时段 LayerD 实时研判」自动化在盘中触发时调用（行情由 agent 经 tdx_quotes 注入）。

模式 B · review（非交易时段固定复盘与更新）
  读取引信触发历史（spring_soe_trigger_history.jsonl，由 watch.py 累积）→
  用日线 CSV 回填每只触发后的 T+1/T+5 表现 → 分催化剂类型统计命中率与平均收益 →
  产出复盘报告并推送。
  由「弹簧池周度刷新」自动化（周日，非交易时段）调用。

用法：
  .venv/Scripts/python.exe research/spring_soe_research.py realtime --code 600717 --catalyst 资产注入 [--quote-json "{...}"]
  .venv/Scripts/python.exe research/spring_soe_research.py review
"""
import json, os, sys, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
ROOT = os.path.dirname(HERE)  # Claw 根目录
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
OUT = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache"
DAILY = "E:/WorkBuddy_dot_workbuddy/Claw/backtest_cache/daily"
HISTORY = os.path.join(OUT, "spring_soe_trigger_history.jsonl")
FINAL = os.path.join(OUT, "spring_soe_final.json")

# 催化剂类型 → 引信强度权重（资产注入/整体上市/并购重组最强，国企改革系中等）
CATALYST_WEIGHT = {
    "资产注入": 1.0, "整体上市": 1.0, "并购重组预案": 0.95, "资产重组": 0.9,
    "定增预案": 0.7, "借壳": 0.95, "混改": 0.8, "国有資本": 0.7, "注入": 0.85, "并购": 0.85,
    "国企改革": 0.6, "广东国资改革": 0.6, "上海国资改革": 0.6, "北京国资改革": 0.6,
    "浙江国资改革": 0.6, "深圳国资改革": 0.6, "活跃小盘国企": 0.55, "双百企业": 0.65,
    "停牌": 0.5, "筹划重大事项": 0.75,
}


def _read_daily(code):
    p = os.path.join(DAILY, "%s.csv" % code)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8", errors="ignore") as f:
        next(f, None)
        for line in f:
            parts = line.strip().split(",")
            if len(parts) < 6:
                continue
            try:
                out.append({"date": parts[0], "open": float(parts[1]), "close": float(parts[2]),
                            "high": float(parts[3]), "low": float(parts[4]), "vol": float(parts[5])})
            except Exception:
                continue
    return out


def _final_map():
    try:
        return {x["code"]: x for x in json.load(open(FINAL, encoding="utf-8"))}
    except Exception:
        return {}


def realtime(code, catalyst, quote_json=None):
    cats = catalyst if isinstance(catalyst, list) else [catalyst]
    cw = max([CATALYST_WEIGHT.get(c, 0.5) for c in cats])
    fm = _final_map()
    m = fm.get(code)
    spring = 1 - m["scr_pctl"] if (m and m.get("scr_pctl") is not None) else 0.5
    confirm = 0.5
    note = "（无实时行情，按引信+弹簧估算）"
    if quote_json:
        try:
            q = json.loads(quote_json) if isinstance(quote_json, str) else quote_json
            vs = (q.get("vol", 0) or 0) / (q.get("v5avg", 0) or 1)
            confirm = min(1.0, 0.5 + 0.5 * max(0.0, (vs - 1) / 2.0))
            note = "量比%.2f / 现价%.2f" % (vs, q.get("price", 0))
        except Exception as e:
            note = "实时行情解析失败:%s" % e
    score = round(0.4 * cw + 0.3 * spring + 0.3 * confirm, 3)
    if score >= 0.75:
        grade, g = "强启动", "高概率启动，关注盘中放量突破"
    elif score >= 0.55:
        grade, g = "弱确认", "引信已搭但量能/突破待确认，列入重点观察"
    else:
        grade, g = "观望", "引信强度或弹簧紧度不足，暂观望"
    text = ("【弹簧压到底 · 引信研判 · 实时】%s %s\n" % (code, m["sec_name"] if m else "")) + \
           "引信:%s(强度%.2f) 弹簧紧度%.2f 实时确认%.2f\n" % (",".join(cats), cw, spring, confirm) + \
           "综合预判 %.2f → %s：%s\n%s" % (score, grade, g, note)
    return {"code": code, "score": score, "grade": grade, "text": text}


def review():
    if not os.path.exists(HISTORY):
        return "【弹簧压到底 · 引信复盘】暂无触发历史（Layer D 监控运行后累积）"
    rows = []
    for l in open(HISTORY, encoding="utf-8"):
        l = l.strip()
        if l:
            try:
                rows.append(json.loads(l))
            except Exception:
                pass
    if not rows:
        return "【弹簧压到底 · 引信复盘】历史为空"
    # 数据准备度：让用户清楚「何时能看到首份命中率统计」
    tdates = sorted(set(r.get("trigger_date") for r in rows if r.get("trigger_date")))
    earliest, latest = (tdates[0], tdates[-1]) if tdates else ("?", "?")
    n_total = len(rows)
    per_cat = {}
    detail = []
    n_eligible = 0   # 已满 ≥5 交易日回填窗口、可纳入统计
    n_pending = 0    # 尚在窗口内待回填
    for r in rows:
        code = r["code"]
        td = r["trigger_date"]
        cats = r.get("new_catalyst", [])
        bars = _read_daily(code)
        if len(bars) < 7:
            n_pending += 1
            continue
        idx = next((i for i, b in enumerate(bars) if b["date"] == td), None)
        if idx is None or idx + 5 >= len(bars):
            n_pending += 1
            continue
        n_eligible += 1
        trig_close = bars[idx]["close"]
        n1 = bars[idx + 1]
        n5 = bars[idx + 5]
        gap1 = 1 if n1["open"] > trig_close else 0
        ret1 = n1["close"] / trig_close - 1 if trig_close else 0
        ret5 = n5["close"] / trig_close - 1 if trig_close else 0
        for c in cats:
            s = per_cat.setdefault(c, [0, 0, 0.0, 0.0])
            s[0] += 1
            s[1] += gap1
            s[2] += ret1
            s[3] += ret5
        detail.append((code, td, cats, gap1, ret1, ret5))
    today = datetime.date.today().strftime("%Y-%m-%d")
    L = ["【弹簧压到底 · 引信复盘】%s" % today,
         "数据准备度: 触发历史 %d 条（区间 %s ~ %s）" % (n_total, earliest, latest),
         "  已满 ≥5 交易日回填窗口(纳入统计): %d 条" % n_eligible,
         "  尚在窗口内待回填: %d 条" % n_pending,
         "-" * 40]
    if per_cat:
        L.append("★ 首份命中率统计（分催化剂类型，T+1 高开率 / T+1 均值 / T+5 均值）:")
        for c, s in sorted(per_cat.items(), key=lambda kv: kv[1][2], reverse=True):
            n = s[0]
            gr = s[1] / n if n else 0
            r1 = s[2] / n if n else 0
            r5 = s[3] / n if n else 0
            L.append("  %s: n=%d 高开率%.0f%% T+1均%.2f%% T+5均%.2f%%" % (c, n, gr * 100, r1 * 100, r5 * 100))
        L.append("（样本随触发累积自动扩展；可读 backtest_cache/spring_soe_trigger_history.jsonl 核对）")
    else:
        L.append("暂无足够样本回填（需触发后 ≥5 交易日）；待 %d 条窗口内触发到期后自动纳入。" % n_pending)
    L.append("-" * 40)
    L.append("最近明细:")
    for code, td, cats, gap1, r1, r5 in detail[-10:]:
        L.append("  %s %s 高开%d T+1%.2f%% T+5%.2f%%" % (code, td, gap1, r1 * 100, r5 * 100))
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode")
    pr = sub.add_parser("realtime")
    pr.add_argument("--code", required=True)
    pr.add_argument("--catalyst", required=True)
    pr.add_argument("--quote-json", default=None)
    sub.add_parser("review")
    a = ap.parse_args()

    import push_channel as pc
    if a.mode == "realtime":
        res = realtime(a.code, a.catalyst, a.quote_json)
        print(res["text"])
        ok, ch, det, nt = pc.send("【弹簧研判·实时】%s" % a.code, res["text"], "弹簧压到底")
        print("PUSH ok=%s channel=%s tries=%d" % (ok, ch, nt))
    elif a.mode == "review":
        txt = review()
        print(txt)
        # 复盘模式：非交易时段回溯统计，非可执行交易信号 → 严格走对话框主通道，
        # 显式 wechat=False 压制 channel_policy.json 的 mirror_titles("引信") 镜像，
        # 符合「通道铁律：复盘报告一律进对话框、严禁主动推微信」。
        ok, ch, det, nt = pc.send("【弹簧引信复盘】", txt, "弹簧压到底", wechat=False)
        print("PUSH ok=%s channel=%s tries=%d" % (ok, ch, nt))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
