# -*- coding: utf-8 -*-
"""
cffex_position_ingest.py — 中金所(会员持仓排名)抓取 + 中信净空解析 + 分位统计

2026-11-04 落地（用户选 (a)：先写 ingestion，dry-run 验证解析）。

目的（给后台 regime 门控喂数据）：
  每日 ingest 中金所「会员持仓排名」，解析出：
    - 中信期货(中信席位) 各品种 净空 = 持卖仓 - 持买仓
    - 前20会员合计净空（用于判别「中信独加 vs 全市场加」）
    - 中信净空相对历史的分位（判别「噪声 vs 极值」）
    - 连续性 / 跨品种分化（共振指标的两条，升贴水另需现货数据，标 TODO）

数据源：中金所公开「每日持仓排名」。CFFEX 该接口为动态数据，URL 拼装规则
  历史上为 http://www.cffex.com.cn/sj/ccpm/{YYYYMMDD}/{YYYYMMDD}_{idx}.json
  （idx 1..4 对应 IF/IH/IC/IM），返回 JSON 含 o_cpm(成交量)/o_ccpm(持买)/o_cppm(持卖)。
  ⚠️ 本环境(沙箱)实测该路径现返回 404（站点结构/referer/cookie 规则变化），
  故 live 抓取函数保留但 **未经验证不写历史、不谎报成功**，明确标
  LIVE_ENDPOINT_UNVERIFIED，待「毕方灵犀通达」宿主（稳定出网+可带 header）落地验证。

设计纪律（与系统核心原则一致 —— 生产者不得自证 / 不伪造数据）：
  - 解析对字段名做别名容错，但不臆造来源；live 失败即报错退出，不降级成假数据。
  - 所有数字必须可复核：历史落在 backtest_cache/cffex_net_short_history.jsonl，
    快照落在 backtest_cache/cffex_regime_snapshot.json，self-test 用 bundled fixture。

用法：
  python research/cffex_position_ingest.py --selftest
        # 纯 fixture，无网络，断言解析/中信净空/分位正确
  python research/cffex_position_ingest.py --dry
        # 同 selftest，语义别名（dry-run 演示）
  python research/cffex_position_ingest.py --date 20261104
        # 尝试 live 抓取；失败则打印 LIVE_ENDPOINT_UNVERIFIED 并退出，不写历史
"""
import os, sys, json, datetime, argparse, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CACHE = os.path.join(ROOT, "backtest_cache")
FIXTURE = os.path.join(HERE, "fixtures", "cffex_ccpm_sample_IF.json")
HISTORY_P = os.path.join(CACHE, "cffex_net_short_history.jsonl")
SNAPSHOT_P = os.path.join(CACHE, "cffex_regime_snapshot.json")

PRODUCTS = ["IF", "IH", "IC", "IM"]
# idx 映射为历史惯例，已标 UNVERIFIED（live 落地时核对）
PRODUCT_INDEX = {"IF": 1, "IH": 2, "IC": 3, "IM": 4}
CITIC_NAMES = ["中信期货", "中信期货有限公司"]  # 席位名别名
TOP_N = 20  # 持仓排名只公布前20

# 字段别名（CFFEX 不同版本字段名有出入，容错解析）
NAME_ALIASES = ["PARTICIPANTABBR1", "PARTICIPANTABBR", "MEMBER_NAME", "SHORTNAME", "ABBR", "name", "member"]
QTY_ALIASES = ["VOLUME", "POSITION", "LONG_VOLUME", "QTY", "volume", "position"]
VAR_ALIASES = ["VARVOLUME", "CHG", "CHANGE", "VAR", "varVolume"]

# live 端点候选（按疑似优先级；均未在本环境验证）
LIVE_ENDPOINTS = [
    "http://www.cffex.com.cn/sj/ccpm/{date}/{date}_{idx}.json",
    "https://www.cffex.com.cn/sj/ccpm/{date}/{date}_{idx}.json",
    "http://www.cffex.com.cn/sj/ccpm/{yyyymm}/{date}_{idx}.json",
]


def _resolve(d, aliases):
    """从 dict 里按别名取第一个存在的键。"""
    if not isinstance(d, dict):
        return None
    for k in aliases:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_product(obj):
    """解析单品种持仓排名 JSON -> {member: {long, long_var, short, short_var, rank_long, rank_short}}。

    输入约定（CFFEX 公开结构）：
      obj['o_ccpm'] = 持买单量排名 列表
      obj['o_cppm'] = 持卖单量排名 列表
    每个元素含 会员名 + VOLUME(持仓量) + VARVOLUME(增减)。
    """
    out = {}
    long_list = obj.get("o_ccpm") or []
    short_list = obj.get("o_cppm") or []

    def _ingest(lst, side):
        for i, e in enumerate(lst):
            if not isinstance(e, dict):
                continue
            name = _resolve(e, NAME_ALIASES)
            if name is None:
                continue
            qty = _to_int(_resolve(e, QTY_ALIASES))
            var = _to_int(_resolve(e, VAR_ALIASES)) or 0
            rec = out.setdefault(name, {"long": None, "short": None,
                                        "long_var": 0, "short_var": 0,
                                        "rank_long": None, "rank_short": None})
            if side == "long":
                rec["long"] = qty
                rec["long_var"] = var
                rec["rank_long"] = e.get("RANK") or (i + 1)
            else:
                rec["short"] = qty
                rec["short_var"] = var
                rec["rank_short"] = e.get("RANK") or (i + 1)

    _ingest(long_list, "long")
    _ingest(short_list, "short")
    return out


def member_net_short(parsed, name):
    """某会员净空 = 持卖 - 持买；净空增减 = 卖增减 - 买增减。"""
    rec = parsed.get(name)
    if rec is None:
        return None
    long = rec["long"] or 0
    short = rec["short"] or 0
    return {
        "member": name,
        "long": long,
        "short": short,
        "net_short": short - long,
        "long_var": rec["long_var"],
        "short_var": rec["short_var"],
        "net_short_var": rec["short_var"] - rec["long_var"],
        "present": (rec["long"] is not None) or (rec["short"] is not None),
    }


def topn_net_short(parsed, n=TOP_N):
    """前 n 会员合计净空（判别中信独加 vs 全市场加）。"""
    members = list(parsed.items())[:n]
    long_sum = sum((r["long"] or 0) for _, r in members)
    short_sum = sum((r["short"] or 0) for _, r in members)
    long_var_sum = sum(r["long_var"] for _, r in members)
    short_var_sum = sum(r["short_var"] for _, r in members)
    return {
        "long": long_sum,
        "short": short_sum,
        "net_short": short_sum - long_sum,
        "long_var": long_var_sum,
        "short_var": short_var_sum,
        "net_short_var": short_var_sum - long_var_sum,
        "n": len(members),
    }


def find_citic(parsed):
    for nm in CITIC_NAMES:
        if nm in parsed:
            return nm, member_net_short(parsed, nm)
    # 模糊匹配（防席位名带后缀）
    for nm in parsed:
        if "中信" in nm:
            return nm, member_net_short(parsed, nm)
    return None, None


def load_history():
    """返回 list[dict]，每行 {date, product, citic_net_short, citic_long, citic_short,
    citic_net_short_var, top20_net_short, top20_net_short_var}。"""
    recs = []
    if not os.path.exists(HISTORY_P):
        return recs
    with open(HISTORY_P, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def append_history(rec):
    os.makedirs(CACHE, exist_ok=True)
    with open(HISTORY_P, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def percentile_of(history, product, value):
    """value 在该品种历史净空分布中的分位 0..1（越小越靠近低位/越偏多，越大越偏空）。"""
    series = [r["citic_net_short"] for r in history if r.get("product") == product]
    if not series:
        return None
    below = sum(1 for x in series if x < value)
    return round(below / len(series), 4)


def continuity(history, product, value):
    """连续性：今日净空 vs 上一交易日（最近一条同品种记录）的增减。"""
    same = [r for r in history if r.get("product") == product]
    if len(same) < 1:
        return {"delta_vs_prev": None, "note": "INSUFFICIENT"}
    prev = same[-1]
    return {"delta_vs_prev": value - prev["citic_net_short"],
            "prev_date": prev.get("date"),
            "prev_value": prev["citic_net_short"]}


def fetch_live(date, product):
    """尝试 live 抓取单品种持仓排名。失败返回 None（绝不伪造）。

    ⚠️ LIVE_ENDPOINT_UNVERIFIED：本环境实测 CFFEX 接口返回 404，端点/header 规则
    待「毕方灵犀通达」宿主验证。此处仅做 best-effort 尝试。
    """
    import urllib.request
    idx = PRODUCT_INDEX.get(product)
    if idx is None:
        return None
    yyyymm = date[:6]
    last_err = None
    for tpl in LIVE_ENDPOINTS:
        url = tpl.format(date=date, idx=idx, yyyymm=yyyymm)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://www.cffex.com.cn/sj/ccpm/",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            obj = json.loads(raw)
            if isinstance(obj, dict) and (obj.get("o_ccpm") or obj.get("o_cppm")):
                return obj
        except Exception as e:  # noqa: BLE001
            last_err = "%s -> %s" % (url, repr(e)[:120])
            continue
    sys.stderr.write("LIVE_ENDPOINT_UNVERIFIED: 全部候选端点失败，最后错误: %s\n" % last_err)
    return None


def build_snapshot(date, parsed_by_product, history):
    """汇总多品种 -> regime 快照（供后台门控读取）。"""
    citic = {}
    top20 = {}
    for p, parsed in parsed_by_product.items():
        nm, c = find_citic(parsed)
        t = topn_net_short(parsed)
        if c:
            citic[p] = c
        top20[p] = t
    # 聚合（中信净空跨品种求和）
    agg_net = sum(c["net_short"] for c in citic.values())
    agg_var = sum(c["net_short_var"] for c in citic.values())
    top20_agg = sum(t["net_short"] for t in top20.values())
    top20_agg_var = sum(t["net_short_var"] for t in top20.values())

    # 分位（依赖历史；首次无历史则 None）
    pct = {p: percentile_of(history, p, c["net_short"]) for p, c in citic.items()}
    cont = {p: continuity(history, p, c["net_short"]) for p, c in citic.items()}

    # 共振判别（数据层，门控逻辑在 (b)）
    # 中信独加 vs 全市场加：中信净空增减 与 前20合计净空增减 同向且中信占比
    resonance = {
        "citic_dominant": None,   # True=中信独加(个体行为) False=全市场共识
        "cross_product_signs": {p: ("short_add" if c["net_short_var"] > 0 else "short_cut")
                                for p, c in citic.items()},
        "basis": "TODO",          # 升贴水需现货指数数据，另接 retrieval，标 TODO
    }
    if citic and top20:
        # 用聚合口径粗略判：中信增仓占比
        if top20_agg_var != 0:
            share = agg_var / top20_agg_var if top20_agg_var else None
            resonance["citic_dominant"] = (share is not None and abs(share) > 0.5)

    return {
        "date": date,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": "CFFEX_持仓排名",
        "citic_by_product": citic,
        "citic_aggregate": {"net_short": agg_net, "net_short_var": agg_var},
        "top20_by_product": top20,
        "top20_aggregate": {"net_short": top20_agg, "net_short_var": top20_agg_var},
        "percentile": pct,
        "continuity": cont,
        "resonance": resonance,
        "note": "basis(升贴水)待接现货数据；live 端点待通达宿主验证(LIVE_ENDPOINT_UNVERIFIED)。",
    }


def write_snapshot(snap):
    os.makedirs(CACHE, exist_ok=True)
    with open(SNAPSHOT_P, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)


def _selftest():
    """纯 fixture 自检：断言解析/中信净空/前20/分位 全部正确，不触网。"""
    obj = json.load(open(FIXTURE, encoding="utf-8"))
    parsed = parse_product(obj)
    nm, c = find_citic(parsed)
    assert nm and "中信" in nm, "未识别中信席位"
    # 中信: long=15832, short=45210, net=29378, var=552
    assert c["long"] == 15832, "long 解析错: %s" % c["long"]
    assert c["short"] == 45210, "short 解析错: %s" % c["short"]
    assert c["net_short"] == 29378, "net_short 算错: %s" % c["net_short"]
    assert c["net_short_var"] == 552, "net_short_var 算错: %s" % c["net_short_var"]

    t = topn_net_short(parsed)
    assert t["long"] == 55832, "top20 long 错: %s" % t["long"]
    assert t["short"] == 151210, "top20 short 错: %s" % t["short"]
    assert t["net_short"] == 95378, "top20 net 错: %s" % t["net_short"]

    # 分位：注入合成历史
    hist = [{"date": "2026-11-0%d" % i, "product": "IF", "citic_net_short": v}
            for i, v in enumerate([20000, 25000, 29000, 30000, 31000], 1)]
    pct = percentile_of(hist, "IF", 29378)
    assert pct is not None and 0.4 <= pct <= 0.8, "分位异常: %s" % pct

    # 连续性
    cont = continuity(hist, "IF", 29378)
    assert cont["delta_vs_prev"] == -1622, "连续性算错: %s" % cont

    # 快照构建不崩
    snap = build_snapshot("20261104", {"IF": parsed}, hist)
    assert snap["citic_aggregate"]["net_short"] == 29378
    assert snap["resonance"]["cross_product_signs"]["IF"] == "short_add"

    print("SELFTEST PASS")
    print("  中信(IF) long=%d short=%d net_short=%d net_short_var=%d" %
          (c["long"], c["short"], c["net_short"], c["net_short_var"]))
    print("  前20(IF) long=%d short=%d net_short=%d" % (t["long"], t["short"], t["net_short"]))
    print("  分位(IF, 29378 vs 合成历史)=%s  连续性 delta=%s" % (pct, cont["delta_vs_prev"]))
    print("  共振跨品种: %s" % snap["resonance"]["cross_product_signs"])
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="交易日 YYYYMMDD，尝试 live 抓取")
    ap.add_argument("--dry", action="store_true", help="dry-run（同 selftest）")
    ap.add_argument("--selftest", action="store_true", help="fixture 自检")
    args = ap.parse_args()

    if args.selftest or args.dry:
        return _selftest()

    if args.date:
        date = args.date
        parsed_by_product = {}
        for p in PRODUCTS:
            obj = fetch_live(date, p)
            if obj is None:
                sys.stderr.write("[%s] live 抓取失败，跳过（不写历史）\n" % p)
                continue
            parsed_by_product[p] = parse_product(obj)
        if not parsed_by_product:
            sys.stderr.write("LIVE_ENDPOINT_UNVERIFIED: 无任何品种抓到真实数据，"
                             "未写历史/快照。请在「毕方灵犀通达」宿主验证端点后重跑。\n")
            return 2
        # 写历史 + 快照
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
        print("LIVE OK: 已写历史+快照 -> %s" % SNAPSHOT_P)
        print(json.dumps(snap, ensure_ascii=False, indent=2)[:1500])
        return 0

    # 无参数：默认跑 selftest，避免无脑空跑
    print("未指定 --date/--selftest/--dry，默认执行 --selftest：")
    return _selftest()


if __name__ == "__main__":
    sys.exit(main() or 0)
