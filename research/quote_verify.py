#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""quote_verify.py — 用实时/收盘行情核对某份候选名单的真实表现

用途：把「规则在 T-1 收盘选出的名单」拉到 T 日真实成交价上，核对命中率。
这是唯一能检验统计结论能不能落地的环节 —— 回测说 16%，今天到底几个涨停。

用法：
  # 直接给代码
  .venv/Scripts/python.exe research/quote_verify.py 002249 002708

  # 从 check-date 落盘的名单里取某条规则
  .venv/Scripts/python.exe research/quote_verify.py --rule FIRSTBOARD_STRONG \
      --from backtest_cache/prelaunch_check_2026-10-26.json

  # 只输出汇总，不逐票列
  .venv/Scripts/python.exe research/quote_verify.py --rule WASHOUT --from ... --brief
"""
import io
import os
import sys
import json
import argparse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZT_TH = 9.8  # 近似涨停判定阈值（主板）；创/科板会被低估，故同时给出涨幅排序


def prefix(c):
    if c.startswith(("6", "9", "5")):
        return "sh" + c
    if c.startswith(("4", "8", "920")):
        return "bj" + c
    return "sz" + c


def fetch(codes, batch_size=40):
    out = []
    for i in range(0, len(codes), batch_size):
        batch = [prefix(c) for c in codes[i:i + batch_size]]
        url = "https://qt.gtimg.cn/q=" + ",".join(batch)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=20).read().decode("gbk", "ignore")
        for seg in raw.split(";"):
            seg = seg.strip()
            if not seg.startswith("v_"):
                continue
            try:
                body = seg.split('="', 1)[1].rstrip('"')
                f = body.split("~")
                out.append({
                    "code": f[2], "name": f[1],
                    "close": float(f[3]), "prev": float(f[4]), "open": float(f[5]),
                    "high": float(f[33]), "low": float(f[34]), "pct": float(f[32]),
                    "turn": float(f[38]) if len(f) > 38 and f[38] else None,
                    "vr": float(f[49]) if len(f) > 49 and f[49] else None,
                    "time": f[30] if len(f) > 30 else "",
                })
            except Exception:
                continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*", help="6 位股票代码，可多个")
    ap.add_argument("--from", dest="src", default=None, help="check-date 落盘的 json")
    ap.add_argument("--rule", default=None, help="从 json 里取哪条规则")
    ap.add_argument("--brief", action="store_true", help="只输出汇总")
    a = ap.parse_args()

    codes = list(a.codes)
    if a.src:
        d = json.load(open(a.src, encoding="utf-8"))
        rules = d.get("rules", {})
        if a.rule:
            if a.rule not in rules:
                sys.exit("规则 %s 不存在；可选：%s" % (a.rule, ", ".join(rules)))
            codes += [x["code"] for x in rules[a.rule]]
        else:
            for name, lst in rules.items():
                if name == "BASE_ALL":
                    continue
                codes += [x["code"] for x in lst]
    codes = [c.strip() for c in codes if c.strip().isdigit() and len(c.strip()) == 6]
    codes = list(dict.fromkeys(codes))
    if not codes:
        sys.exit("没有可用代码")

    out = fetch(codes)
    if not out:
        sys.exit("行情拉取失败")

    zt = [x for x in out if x["pct"] >= ZT_TH]
    up = [x for x in out if x["pct"] > 0]
    n = len(out)
    print("规则: %s | 数据源时间: %s" % (a.rule or "手工代码", out[0].get("time", "-")))
    print("样本 %d 只 | 涨停 %d 只 (%.1f%%) | 上涨 %d 只 (%.1f%%) | 均值 %+.2f%% | 中位数 %+.2f%%"
          % (n, len(zt), len(zt) * 100.0 / n, len(up), len(up) * 100.0 / n,
             sum(x["pct"] for x in out) / n,
             sorted(x["pct"] for x in out)[n // 2]))
    if not a.brief:
        print()
        for x in sorted(out, key=lambda y: -y["pct"]):
            print("%s %-8s %7.2f %+7.2f%%  开%7.2f 高%7.2f 低%7.2f 换手%s 量比%s"
                  % (x["code"], x["name"], x["close"], x["pct"], x["open"], x["high"], x["low"],
                     ("%.2f" % x["turn"]) if x["turn"] is not None else "  -",
                     ("%.2f" % x["vr"]) if x["vr"] is not None else "  -"))
    p = os.path.join(ROOT, "backtest_cache", "quote_verify_last.json")
    json.dump(out, io.open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n落盘: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
