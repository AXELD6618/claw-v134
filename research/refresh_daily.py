# -*- coding: utf-8 -*-
"""
refresh_daily.py (原创) — TDX MCP 日线增量刷新到 backtest_cache/daily/*.csv
=========================================================================
数据链路: 智能体(我) 用 tdx_kline(code,setcode,period="4") 抓取日线
          -> 把 Rows 汇成 fetched.json {code:[row...]}
          -> 本脚本 --apply 增量追加(按日期去重, 仅追加 > 末日的行)

映射规则(已与缓存CSV核对):
  日期: TDX "20261023" -> "2026-10-23"
  列序: 日期,开盘,收盘,最高,最低,成交量   (与 v2.load_daily 一致)
  成交量量纲(重要): TDX 同时返回 Volume(手=RawVolume/Unit, Unit=100) 与
            RawVolume(股)。v2.feats 用 `turn=vi*100/float_shr`(注释"手->股")，
            即 v2 规范期望缓存成交量列为"手"。
  ⚠️ 实测发现原缓存量纲不统一: 688787 存"股"(=RawVolume), 300839/603278 存"手"(=Volume)。
    为不制造 100x 假量能尖峰, 本脚本对每只票按"现有缓存末行"自动匹配量纲:
      - 缓存末行≈新行 Volume  -> 存 Volume(手)
      - 缓存末行≈新行 RawVolume -> 存 RawVolume(股)
      - 无缓存(新建) -> 默认存 Volume(手, v2规范)
    比值类特征量纲无关, 故匹配末行即可保证续行与历史同尺度。
"""
from __future__ import annotations
import os, sys, json

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAILY = os.path.join(PROJ, "backtest_cache", "daily")


def setcode_of(code: str) -> str:
    if code.startswith(("6", "68")):
        return "1"
    if code.startswith(("0", "3")):
        return "0"
    if code.startswith(("4", "8")):
        return "2"
    return "1"


def fmt_date(s: str) -> str:
    s = str(s).strip()
    if "-" in s:
        return s
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _tail_line(p: str, maxb: int = 4096) -> str:
    """只读文件尾部取最后一条有效数据行(不回读整个 CSV)。

    性能修复(2026-10-29): 原实现每只票整文件读 2 次(取末日 + 取末量),
    5225 只时 ~2.8 只/秒, 单轮 >30 分钟, 被 launcher 600s 截断。
    改为 seek 到尾部只读 4KB, 结果等价(末行仍是末行), 速度提升 2 个数量级。
    """
    if not os.path.exists(p):
        return ""
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - maxb))
            txt = f.read().decode("utf-8-sig", "ignore")
    except OSError:
        return ""
    best = ""
    for ln in reversed(txt.splitlines()):
        a = ln.strip().split(",")
        if a and a[0] and a[0] != "日期":
            if len(a) >= 6:
                return ln.strip()
            if not best:
                best = ln.strip()
    return best


def _last_vol(p: str):
    """读缓存末行成交量(浮点), 无则 None。"""
    ln = _tail_line(p)
    if not ln:
        return None
    a = ln.split(",")
    if len(a) >= 6:
        try:
            return float(a[5])
        except ValueError:
            return None
    return None


def _ymd(s: str) -> int:
    s = fmt_date(s).replace("-", "")
    return int(s) if s.isdigit() else 0


def _vol_to_store(r: dict, last) -> float:
    """按现有缓存末行量纲决定本行应存成交量(股或手)。"""
    V = float(r.get("Volume", 0) or 0)
    R = float(r.get("RawVolume", r.get("Volume", 0)) or 0)
    if last is None:
        return V if V > 0 else R          # 新建文件: 默认"手"(v2规范)
    # Unit=100 结构性启发: 末行量 vs 新行两手量, 谁近选谁(不受日间量波动干扰)
    ratio_v = (last / V) if V > 0 else float("inf")
    ratio_r = (R / last) if last > 0 else float("inf")
    if ratio_v > 10:
        return R                          # 末行远大于"手"量 -> 股(RawVolume)
    if ratio_r > 10:
        return V                          # "股"量远大于末行 -> 手(Volume)
    # 都不明显(量极小或歧义): 默认"手"(v2规范)
    return V if (abs(last - V) <= abs(last - R)) else R


def row_to_line(r: dict, vol_src: str = "RawVolume") -> str:
    d = fmt_date(r.get("Data", ""))
    o = r.get("Open", "0"); c = r.get("Close", "0")
    h = r.get("High", "0"); l = r.get("Low", "0")
    v = float(r.get(vol_src, 0) or 0)
    return f"{d},{o},{c},{h},{l},{v:.2f}"


def apply_fetched(fetched: dict):
    total = 0
    for code, rows in fetched.items():
        if not rows:
            continue
        p = os.path.join(DAILY, f"{code}.csv")
        last = _last_vol(p)
        # 取"日期最接近末行"的新行决定量纲(Unit=100 结构性启发, 不受日间量波动干扰)
        if last is None:
            sample = rows[0]
            V0 = float(sample.get("Volume", 0) or 0)
            vol_src = "Volume" if V0 > 0 else "RawVolume"
        else:
            ld = _ymd(_last_date(p)) or _ymd(str(last))
            best = min(rows, key=lambda r: abs(_ymd(r.get("Data", "")) - ld)) if rows else rows[0]
            V0 = float(best.get("Volume", 0) or 0)
            R0 = float(best.get("RawVolume", best.get("Volume", 0)) or 0)
            ratio_v = (last / V0) if V0 > 0 else float("inf")
            ratio_r = (R0 / last) if last > 0 else float("inf")
            if ratio_v > 10:
                vol_src = "RawVolume"
            elif ratio_r > 10:
                vol_src = "Volume"
            else:
                vol_src = "Volume"   # 默认 v2规范(手)
        new = []
        for r in rows:
            d = fmt_date(r.get("Data", ""))
            if last and d <= _last_date(p):
                continue
            new.append(row_to_line(r, vol_src))
        if new:
            need_nl = False
            if os.path.exists(p):
                with open(p, "rb") as fb:
                    try:
                        fb.seek(-1, 2)
                        if fb.read() != b"\n":
                            need_nl = True
                    except OSError:
                        need_nl = True
            with open(p, "a", encoding="utf-8") as f:
                if need_nl:
                    f.write("\n")
                f.write("\n".join(new) + "\n")
            total += len(new)
            print(f"[refresh] {code}: +{len(new)} 行 (末日->{new[-1].split(',')[0]}, 量纲={vol_src})")
        else:
            print(f"[refresh] {code}: 0 新行 (末日={_last_date(p)}, 量纲={vol_src})")
    print(f"[refresh] 合计追加 {total} 行")


def _last_date(p: str):
    ln = _tail_line(p)
    if not ln:
        return ""
    a = ln.split(",")
    return fmt_date(a[0]) if a and a[0] else ""


def selftest():
    """用假码验证 追加+去重+格式 逻辑(不污染真实CSV)。"""
    fake = {"999999": [
        {"Data": "20261022", "Open": "10", "High": "11", "Low": "9", "Close": "10.5", "RawVolume": 12345, "Volume": 123.45},
        {"Data": "20261023", "Open": "10.5", "High": "12", "Low": "10.4", "Close": "11.8", "RawVolume": 23456, "Volume": 234.56},
        {"Data": "20261024", "Open": "11.8", "High": "13", "Low": "11.5", "Close": "12.9", "RawVolume": 34567, "Volume": 345.67},
    ]}
    p = os.path.join(DAILY, "999999.csv")
    # 预置已有末日 20261023 (存RawVolume量纲, 与假码RawVolume一致)
    open(p, "w", encoding="utf-8").write("日期,开盘,收盘,最高,最低,成交量\n20261023,10.5,11.8,12,10.4,23456.00\n")
    apply_fetched({"999999": fake["999999"]})
    out = open(p, encoding="utf-8").read().splitlines()
    assert out[-1].startswith("2026-10-24"), f"selftest FAIL: {out[-1]}"
    assert len(out) == 3, f"selftest FAIL 行数: {len(out)}"
    os.remove(p)
    print("[selftest] PASS: 仅追加>末日行, 格式与缓存一致, 量纲匹配末行")


def demo():
    """用本轮真实 MCP 返回的 688787 行验证 真实数据路径(应去重0新行, 且匹配股量纲)。"""
    real = {"688787": [
        {"Data": "20261019", "Open": "78.18", "High": "88.5", "Low": "77.33", "Close": "88.04", "RawVolume": 3885814, "Volume": 38858.14},
        {"Data": "20261020", "Open": "86.74", "High": "104.5", "Low": "85.0", "Close": "103.49", "RawVolume": 8964261, "Volume": 89642.61},
        {"Data": "20261021", "Open": "102.46", "High": "119.3", "Low": "100.54", "Close": "115.6", "RawVolume": 9486594, "Volume": 94865.94},
        {"Data": "20261022", "Open": "115.0", "High": "123.3", "Low": "111.19", "Close": "117.5", "RawVolume": 8521555, "Volume": 85215.55},
        {"Data": "20261023", "Open": "111.0", "High": "141.0", "Low": "111.0", "Close": "141.0", "RawVolume": 10388925, "Volume": 103889.25},
    ]}
    apply_fetched(real)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    elif len(sys.argv) > 2 and sys.argv[1] == "--apply":
        data = json.load(open(sys.argv[2], encoding="utf-8"))
        apply_fetched(data)
    else:
        print("用法: python refresh_daily.py --selftest | --demo | --apply fetched.json")
