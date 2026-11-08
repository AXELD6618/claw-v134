# -*- coding: utf-8 -*-
"""银行三季报真实反应回填（2026-11-02 收盘后跑）。
计算每只重点银行 11-02 收盘 vs 披露前基准(10-30)的涨跌幅，回填报告第5列。
数据源：backtest_cache/daily/*.csv（16:10 底座刷新后含 11-02）。
"""
import csv, os, sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")
REPORT = os.path.join(PROJ, "reports", "bank_fix_2026-11-02.md")

# (code, 名称, 你的档位, 披露日)
BANKS = [
    ("600036", "招商银行", "①技术+基本面", "10-31"),
    ("600926", "杭州银行", "②股息+弹性", "10-28/29"),
    ("601398", "工商银行", "③底仓", "10-31"),
    ("600919", "江苏银行", "④资金强", "10-30"),
    ("601288", "农业银行", "⑤分红", "10-31"),
    ("600000", "浦发银行", "⑥最低PB", "10-31"),
    ("601166", "兴业银行", "观察", "10-30"),
]
BASE_D = "2026-10-30"   # 披露前基准收盘日（10-31 周六，披露后首交易日=11-02）
REACT_D = "2026-11-02"  # 财报窗口反应日


def close_on(c, d):
    p = os.path.join(DAILY, c + ".csv")
    if not os.path.exists(p):
        return None
    for r in csv.reader(open(p, encoding="utf-8-sig")):
        if r and r[0] == d:
            try:
                return float(r[2])
            except ValueError:
                return None
    return None


def main():
    print("=== 银行三季报 11-02 收盘真实反应（vs 基准 %s）===" % BASE_D)
    print("%-7s %-7s %-12s %-10s %-10s %8s  %s" %
          ("代码", "名称", "档位", "基准收", "11-02收", "反应%", "披露日"))
    print("-" * 78)
    rows = []
    missing = []
    for c, nm, tier, disc in BANKS:
        base = close_on(c, BASE_D)
        react = close_on(c, REACT_D)
        if base is None:
            missing.append((c, nm, "基准"))
            continue
        if react is None:
            missing.append((c, nm, "11-02"))
            print("%-7s %-7s %-12s %10.2f %10s %8s  %s" %
                  (c, nm, tier, base, "暂无", "—", disc))
            continue
        pct = (react / base - 1) * 100
        rows.append((c, nm, tier, base, react, pct, disc))
        print("%-7s %-7s %-12s %10.2f %10.2f %+7.2f%%  %s" %
              (c, nm, tier, base, react, pct, disc))

    if missing:
        print("\n⚠ 暂无数据（底座未刷新或不交易）：%s" %
              ", ".join("%s(%s)" % (m[1], m[2]) for m in missing))
        print("若 16:10 底座已刷仍缺，说明该票 11-02 停牌或未收录。")
        return 0

    # 写回填段落到报告
    section = "\n## 五、11-02 收盘真实反应（回填）\n\n"
    section += "基准=%s（10-31 周六，披露后首交易日=11-02）。\n\n" % BASE_D
    section += "| 银行 | 档位 | 披露日 | 基准收 | 11-02收 | 反应% | 解读 |\n|---|---|---|---|---|---|---|\n"
    for c, nm, tier, base, react, pct, disc in rows:
        if pct >= 1.0:
            verdict = "超预期/强"
        elif pct <= -1.0:
            verdict = "不及预期/弱"
        else:
            verdict = "中性"
        section += "| %s | %s | %s | %.2f | %.2f | %+.2f%% | %s |\n" % (
            nm, tier, disc, base, react, pct, verdict)
    section += "\n> 读法：4 只 10-31 披露（招行/工行/农行/浦发）若反应为负，即\"财报兑现即见顶\"；\n> 江苏/兴业（10-30 披露）看是否延续超买后的派发。\n"

    txt = open(REPORT, encoding="utf-8").read()
    # 去掉旧回填段（若有）
    if "## 五、11-02 收盘真实反应（回填）" in txt:
        txt = txt.split("## 五、11-02 收盘真实反应（回填）")[0].rstrip() + "\n"
    open(REPORT, "w", encoding="utf-8").write(txt + section)
    print("\n✅ 已回填 reports/bank_fix_2026-11-02.md 第五节（%d 只）" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
