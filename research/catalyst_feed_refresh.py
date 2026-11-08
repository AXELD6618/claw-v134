# -*- coding: utf-8 -*-
"""
research/catalyst_feed_refresh.py — CAT_PROXIMITY feed 生产者（per-stock + 市场级批量采集，周期刷新）
================================================================================================
定位（延续 catalyst_proximity / catalyst_supplementary / catalyst_ingest_wenda 同范式）：
  TDX `wenda_notice_query` 是 MCP-only，真实抓取由持 TDX MCP 的 agent/automation 完成；
  本模块是**消费端 + 批次生成端**：
    ① --emit-batch  生成待查批次（全市场代码均匀抽样 + name 兜底），供 automation 逐批调 TDX；
    ② --merge       消费预抓响应 {code: wenda_resp} 或 {"MARKET": 市场级resp}，解析→写 catalyst_notices.jsonl；
    ③ --status      报告 feed 新鲜度（最新披露日 / 总数 / 近 N 日催化数 / frontier as_of / 是否 stale）。

数据真相与诚实边界（2026-11-08 校正）：
  - 公开公告是**历史**的：D（披露日）≤ 今日。圣杯 live 选股无法预知"未来披露"。
  - 故 CAT_PROXIMITY 的**可实盘子集** = 披露落地后的"滞后反应 drift"（D≈as_of，即公告日/次日的
    可买入日 T→T+1 续涨）——这是 grail_hit_selector 在 --include-recent 下捕获的 resume 子集，
    其真实 lift 低于历史 runup 子集(5.57×/3.66×/2.24×)，**须由 grail_hit_forward_oos 前向实测验证**。
  - 本模块只管"把真实公告灌进 feed 并保持新鲜"，不判边、不降门；SUPPLEMENTARY 纪律不变。

市场级刷新（关键效率）：wenda_notice_query 仅传 bdate/edate（无 symbol）即返回全市场近期公告，
  单次调用覆盖全市场；行内 PDF 链接含 6 位代码（如 600841_20261107_*.pdf）→ 可回溯归属股票。
  szse 链接不含代码者无法归属 → 诚实跳过（不伪造）。

用法：
  # 生成待查批次（automation 用，默认 200 只均匀抽样；或 --all-codes 全市场）
  python -m research.catalyst_feed_refresh --emit-batch 200 --batch-out /tmp/batch.json
  # 消费预抓响应（含市场级）
  python -m research.catalyst_feed_refresh --merge --raw /tmp/raw.json
  # 新鲜度
  python -m research.catalyst_feed_refresh --status
"""
from __future__ import annotations
import os, sys, json, argparse, re
from pathlib import Path
from datetime import date, timedelta

# —— 路径鲁棒性：无论从 cwd 还是 -m 调用，都保证能 import catalyst_proximity / oospf_validator ——
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import catalyst_proximity as CP  # noqa: E402

DAILY = CP.DAILY
NOTICES = CP.NOTICES_JSONL
NAME_MAP = ROOT / "data" / "name_map.json"
RECENT_DAYS = 7  # --status 新鲜度窗口；须与 grail_hit_selector.RECENT_DAYS 一致

_LINK_CODE_RE = re.compile(r"[^\d](\d{6})[_\.]")


def _code_from_link(link):
    """从公告 PDF 链接回溯 6 位代码（sse 链接形如 .../600841_20261107_*.pdf）。"""
    if not link:
        return None
    m = _LINK_CODE_RE.search(str(link))
    return m.group(1) if m else None


def load_codes():
    codes = []
    for f in sorted(DAILY.glob("*.csv")):
        if f.name.endswith(".bak"):
            continue
        codes.append(f.name[:-4])
    return codes


def load_names():
    try:
        return json.load(open(NAME_MAP, encoding="utf-8"))
    except Exception:
        return {}


def emit_batch(n, out=None, all_codes=False):
    codes = load_codes()
    names = load_names()
    if all_codes or n >= len(codes):
        picked = codes
    elif len(codes) > n:
        step = len(codes) / n
        picked = [codes[int(i * step)] for i in range(n)]
    else:
        picked = codes
    batch = [{"code": c, "name": names.get(c, "")} for c in picked]
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        json.dump(batch, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("emit-batch: %d 只 → %s" % (len(batch), out))
    else:
        print(json.dumps(batch, ensure_ascii=False, indent=2))
    return batch


def _resp_data(resp):
    """兼容多种包装：{ok,data} / 直接 2D list / {data:2D}。"""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        d = resp.get("data")
        if isinstance(d, list):
            return d
    return None


def _split_market(resp):
    """市场级响应 → [(code, 2D_api_data)]，按链接回溯代码归属；无法归属者跳过。"""
    data = _resp_data(resp)
    if not data or len(data) < 2:
        return []
    header = data[0]
    by_code = {}
    unattributed = 0
    for row in data[1:]:
        if not isinstance(row, list) or len(row) < 2:
            continue
        link = row[2] if len(row) > 2 else ""
        code = _code_from_link(link)
        if not code:
            unattributed += 1
            continue
        by_code.setdefault(code, []).append(row)
    out = [(c, [header] + rows) for c, rows in by_code.items()]
    if unattributed:
        print("  [market] 跳过无法归属行 %d（链接无 6 位代码，多为 szse）" % unattributed)
    return out


def merge(raw_path):
    raw = json.load(open(raw_path, encoding="utf-8"))
    items = []
    for key, resp in raw.items():
        if re.fullmatch(r"\d{6}", str(key)):
            data = _resp_data(resp)
            if data:
                items.append((str(key), data))
        elif str(key).upper() == "MARKET":
            items.extend(_split_market(resp))
        else:
            # 兜底：尝试从链接回溯（某些调用方直接用 code 字符串但带前缀）
            data = _resp_data(resp)
            code = _code_from_link(str(key))
            if data and code:
                items.append((code, data))
    if not items:
        print("[merge] 无有效响应条目；检查 raw 结构（应为 {code: resp} 或 {'MARKET': resp}）")
        return 0
    n = CP.collect_to_jsonl(items, NOTICES)
    return n


def _frontier_as_of():
    files = sorted(f for f in DAILY.glob("*.csv") if not f.name.endswith(".bak"))
    if not files:
        return 0
    mx = 0
    for f in files[:64]:
        try:
            d = CP.pd.read_csv(f, usecols=["日期"]).iloc[-1]
            v = int(str(d["日期"]).replace("-", ""))
            mx = max(mx, v)
        except Exception:
            pass
    return mx


def status(recent_days=RECENT_DAYS):
    as_of = _frontier_as_of()
    total = 0
    max_d = 0
    recent = 0
    try:
        for line in open(NOTICES, encoding="utf-8"):
            try:
                ev = json.loads(line)
            except Exception:
                continue
            total += 1
            d = int(ev.get("date", 0))
            max_d = max(max_d, d)
            if as_of and 0 <= (as_of - d) <= recent_days:
                recent += 1
    except FileNotFoundError:
        print("[status] feed 文件不存在：%s" % NOTICES)
        return
    stale = (as_of - max_d) > recent_days if as_of and max_d else True
    print("[status] frontier as_of=%d  总披露=%d  最新披露=%d  近%d日催化=%d"
          % (as_of, total, max_d, recent_days, recent))
    print("  feed %s（最新披露距今 %d 日）"
          % ("STALE ⚠️" if stale else "新鲜 ✓", (as_of - max_d) if as_of and max_d else -1))
    if recent == 0:
        print("  ⚠️ 窗口内零近期催化：可能确无近期重大催化，或 feed 未刷新（运行 --merge 或 automation）")
    return dict(as_of=as_of, total=total, max_d=max_d, recent=recent, stale=stale)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-batch", type=int, default=0, help="生成待查批次大小（均匀抽样）")
    ap.add_argument("--all-codes", action="store_true", help="emit 全市场 5225 代码")
    ap.add_argument("--batch-out", default="")
    ap.add_argument("--merge", action="store_true", help="消费 raw JSON 写 feed")
    ap.add_argument("--raw", default="", help="预抓响应 {code: resp} 或 {'MARKET': resp}")
    ap.add_argument("--status", action="store_true", help="报告新鲜度")
    ap.add_argument("--recent-days", type=int, default=RECENT_DAYS)
    a = ap.parse_args()

    if a.emit_batch:
        emit_batch(a.emit_batch, a.batch_out or None, a.all_codes)
        return
    if a.merge:
        if not a.raw:
            print("需指定 --raw（预抓响应文件）")
            return 1
        n = merge(a.raw)
        print("[merge] 本次新增催化事件 %d 条 → %s" % (n, NOTICES))
        return
    if a.status:
        status(a.recent_days)
        return
    print("用法: --emit-batch N [--all-codes] [--batch-out F] | --merge --raw F | --status")


if __name__ == "__main__":
    sys.exit(main())
