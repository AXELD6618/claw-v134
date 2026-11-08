#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""催化/事件可见性层（预点火系列结论的落实 · 运营级，非收益 edge）。

科学结论(已三重证伪, 见 backtest_preignition / backtest_sector_spillover /
backtest_strong_breakout)：
  - 纯价量、板块动量溢出、强突破近涨停 —— 均无法以正期望收益预测"T 开启连续涨停"。
  - 连续涨停启动属外生催化 + 突破后均值回归，市场数据 ex-ante 不可预测。
故催化层不做"预测 edge"，只做【可见性扩展 + 早期预警】：
  - 接入实时新闻/公告/调研催化源(wenda_news_query 等, 运营每日跑)，标记"新鲜催化"票；
  - 写入 backtest_cache/catalyst_watchlist.json；
  - 下游 14:50 流水线将其作为候选宇宙叠加层(让已验证 grail/NOL 边缘评估这些票) +
    推送【催化观测·非买入建议】早期预警。
  - 绝不新增模型因子、绝不以结构性"近涨停"信号充当买入(已被证伪为均值回归输家)。

用法：
  python catalyst_watch.py --date 2026-11-05            # 仅结构层(占位/集成测试)
  python catalyst_watch.py --date 2026-11-05 --feed feed.json   # 合并外部催化源
feed.json 格式：{ "301697": {"score": 0.8, "text": "...", "src": "wenda"}, ... }
"""
import os, json, argparse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "backtest_cache", "catalyst_watchlist.json")

def load_existing():
    try:
        return json.load(open(OUT, encoding="utf-8"))
    except Exception:
        return {}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--feed", default="")
    a = ap.parse_args()
    wl = load_existing()
    added = []
    if a.feed:
        try:
            feed = json.load(open(a.feed, encoding="utf-8"))
            for code, info in feed.items():
                if not isinstance(info, dict):
                    info = {"text": str(info)}
                info.setdefault("src", "feed")
                info["asof"] = a.date
                wl[code] = info
                added.append(code)
        except Exception as e:
            print("feed 读取失败: %s" % e)
    # 仅保留与 asof 同日的外部催化项(过期清理)；结构项(ignition)由 ignition_watchlist 单独管
    fresh = {k: v for k, v in wl.items() if (not a.date) or v.get("asof") == a.date}
    json.dump(fresh, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("asof=%s 催化观测表: 外部新增=%d 在榜=%d → %s" % (a.date or "ALL", len(added), len(fresh), OUT))
    for c in added[:20]:
        print("  + %s src=%s score=%s" % (c, fresh[c].get("src"), fresh[c].get("score")))
    return len(fresh)

if __name__ == "__main__":
    main()
