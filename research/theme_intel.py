#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
theme_intel.py — 情报驱动的题材契合判定器 + 活题材登记表刷新

指令 D (2026-10-25) 交付物 #2。解决：主题门控静态/单一 → 漏选妖股。

核心原则（主理人裁定，来自 instruction D）：
  主题只管「注意力分配 / 优先级」，绝不硬排除一只走出验证模式的票。
  系统真 edge = 模式跟随（连板惯性 / 断层反包），题材无关，是反盲底层安全网。

判定契约（decide_theme_fit 输出域）：
  on       命中 hot 主题                       → 硬核票，进 Tier2 埋伏，升优先级
  weak     命中 warm 主题，或 命中 cold 主题    → warm 进观察，cold 降级但不踢
  emerging 未知题材但有催化事件                 → 登记为新兴题材，跟踪，绝不 off、绝不墓地
  ?        未知题材且无明确催化但有价格信号     → NEEDS_RESEARCH，跟踪，绝不 off
  off      仅：退市/ST/*退 硬审查，或
              （无价格信号 且 无主题命中 且 零催化纯无关）
            —— discovery 命中票必有价格信号(涨停/放量突破)，故 discovery 路径永不 off

数字契约（改动须报 old→new→影响面）：
  HOT_W=1.0 / WARM_W=0.7 / COLD_W=0.4 / EMERGING_W=0.6 / UNKNOWN_W=0.3  各状态生命周期优先级权重
  POLICY_BOOST_W=0.1   国办发29号等点名题材额外加权
  CATALYST_KW          催化事件关键词表（业绩/订单/并购/量产/首发/签约/中标/政策/重组/上市…）
"""
import os, sys, json, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

REGISTRY_P = os.path.join(ROOT, "data", "theme_registry.json")

# 状态 → 生命周期容量驱逐优先级权重（越大越该保留）
HOT_W = 1.0
WARM_W = 0.7
COLD_W = 0.4
EMERGING_W = 0.6
UNKNOWN_W = 0.3
POLICY_BOOST_W = 0.1

CATALYST_KW = ["业绩", "营收", "归母", "订单", "中标", "签约", "并购", "重组",
               "量产", "交付", "首发", "上市", "定增", "政策", "点名", "获批",
               "扭亏", "突破", "合作", "送样", "验证", "客户", "独供", "独家"]

CENSORED_KW = ["退", "ST", "*ST", "★", "N "]

# ==================== 匹配契约（2026-10-26 修复误配，三层保护）====================
# 背景：2026-10-26 出现 5 例误配 —— 金丹科技"3D打印设备"、山外山"血液净化设备"被挂
# semiconductor_local；德尔股份"电机电泵"被挂 robot_components；盛天网络"算力带宽"被挂
# ai_compute；百洋医药"数据集"被挂 data_label。根因不是"某个关键词写太宽"，而是匹配层
# 缺三层保护：①无泛词否决 ②无特异性门槛 ③无主题级负向否决。改措辞绕过只是掩盖。
#
# 保护 1：GENERIC_KW —— 泛词硬黑名单。无论登记表里怎么写，这些词一律不参与匹配。
#         即使未来 enrich agent 往登记表写回泛词，也不会再造成误配（防未来）。
# 保护 2：ON_MIN_LEN —— 'on' 门槛要求关键词 ≥4 字（或在该主题 on_kw 白名单内）。
#         避免"关节/减速器/半导体"这类 2-3 字泛词直接把票拔到 on（高权重、驱逐保护）。
# 保护 3：主题级 veto —— 命中否决词则该主题整体不参与匹配（如 semiconductor_local 遇
#         "3D打印/血液净化/医疗"直接否决）。
GENERIC_KW = {
    "设备", "材料", "电机", "泵", "电泵", "算力", "数据集", "芯片", "软件", "系统",
    "组件", "零件", "器件", "装置", "部件", "产品", "技术", "服务", "制造", "整机",
    "模组", "模块", "半导体", "减速器", "轴承", "丝杠", "光纤", "卫星", "低空", "通航",
    "仿真", "关节", "触觉", "语料", "零售", "白酒", "乳业", "免税", "游戏", "版号",
    "电竞", "封测", "光刻", "智算", "带宽", "服务器", "云计算", "数据中心",
}
ON_MIN_LEN = 4          # 'on' 门槛：关键词最小字符数

# 登记表读取缓存（按 mtime 失效，避免每票重读）
_REG_CACHE = {"reg": None, "m": 0}


def load_registry():
    if not os.path.exists(REGISTRY_P):
        return {"themes": {}, "demoted": [], "policy_boost": []}
    return json.load(open(REGISTRY_P, encoding="utf-8"))


def get_registry():
    """带 mtime 缓存的登记表读取。"""
    m = os.path.getmtime(REGISTRY_P) if os.path.exists(REGISTRY_P) else 0
    if _REG_CACHE["m"] != m or _REG_CACHE["reg"] is None:
        _REG_CACHE["reg"] = load_registry()
        _REG_CACHE["m"] = m
    return _REG_CACHE["reg"]


def _is_censored(name):
    if not name:
        return False
    return any(k in name for k in CENSORED_KW)


def match_theme(text, registry, min_len=0):
    """在 text 中匹配登记表关键词，返回 [(theme_key, status, weight, hit_kw)]，按 weight 降序。

    三层保护（见文件头 GENERIC_KW 注释）：
      · GENERIC_KW 泛词一律不参与匹配（无论登记表怎么写）
      · veto 命中则整主题跳过
      · min_len>0 时，短于 min_len 的词须在该主题 on_kw 白名单内才计入（用于 'on' 门槛）
    """
    if not text:
        return []
    hits = []
    for key, t in registry.get("themes", {}).items():
        veto = t.get("veto") or []
        if veto and any(v and v in text for v in veto):
            continue                                  # 保护 3：主题级负向否决
        on_kw = set(t.get("on_kw") or [])
        hit = []
        for k in t.get("keywords", []):
            k = (k or "").strip()
            if not k:
                continue
            if k in GENERIC_KW:                        # 保护 1：泛词硬否决
                continue
            if min_len and len(k) < min_len and k not in on_kw:
                continue                               # 保护 2：特异性门槛
            if k in text:
                hit.append(k)
        if hit:
            hits.append((key, t.get("status", "warm"), float(t.get("weight", 0.5)), hit))
    hits.sort(key=lambda x: x[2], reverse=True)
    return hits


def has_catalyst(text):
    if not text:
        return False
    return any(k in text for k in CATALYST_KW)


def decide_theme_fit(code, research_entry, has_price_signal=False, registry=None):
    """判定 theme_fit。返回 (fit, info_dict)。
    research_entry: dict，含 name/theme/catalyst/fundamental/agent_judgment 等文本字段。
    has_price_signal: 该票是否经 discovery 量化初筛（涨停/放量突破），默认 False。

    匹配分两级严格度：
      - 一级（on 门槛）：hot 关键词须出现在研究者标注的显式 theme 字段 → 硬核强相关才 on
      - 二级（weak 门槛）：warm/cold 关键词出现在 theme 字段，或 hot/warm/cold 关键词仅散落在论述 → weak
    这样避免「论述里提了一句机器人」就把边角票拔到 on。
    """
    if registry is None:
        registry = get_registry()
    name = (research_entry or {}).get("name", "") or ""
    if _is_censored(name):
        return "off", {"reason": "censored", "matched": []}
    theme_field = (research_entry or {}).get("theme", "") or ""
    blob = " ".join(str(v) for v in (research_entry or {}).values()
                    if isinstance(v, (str, int, float)))

    # 一级（on 门槛）：theme 字段命中 hot，且关键词达到特异性要求 → 才 on
    # 2026-10-26：原实现用全量关键词命中 hot 就 on，导致"关节/减速器/半导体/数据集"这类
    # 2-3 字泛词把边角票直接拔到 on。现要求 ≥ON_MIN_LEN 字或在该主题 on_kw 白名单内。
    on_hits = match_theme(theme_field, registry, min_len=ON_MIN_LEN)
    if on_hits and on_hits[0][1] == "hot":
        return "on", {"reason": "hit_hot_in_theme", "theme": on_hits[0][0], "kw": on_hits[0][3]}

    # 二级：theme 字段命中任意已登记关键词（泛词已由 GENERIC_KW 否决）→ weak
    theme_hits = match_theme(theme_field, registry)
    if theme_hits:
        top = theme_hits[0]
        return "weak", {"reason": "hit_%s_in_theme" % top[1], "theme": top[0],
                        "kw": top[3], "demoted": top[1] == "cold"}

    # 兜底：关键词仅散落在论述（blob） → 最多 weak，不升 on
    blob_hits = match_theme(blob, registry)
    if blob_hits:
        top = blob_hits[0]
        return "weak", {"reason": "hit_%s_in_prose" % top[1], "theme": top[0],
                        "kw": top[3], "demoted": top[1] == "cold",
                        "note": "关键词仅在论述, 不升on"}

    # 无主题命中
    if has_catalyst(blob):
        cand_theme = theme_field or "新兴题材"
        return "emerging", {"reason": "unknown_with_catalyst",
                            "candidate_theme": cand_theme,
                            "note": "登记为新兴题材, 跟踪绝不off/绝不墓地"}
    if has_price_signal:
        # 有价格信号(涨停/放量突破) = 已「走出模式」→ 不盲 off
        return "?", {"reason": "price_signal_no_theme",
                     "note": "NEEDS_RESEARCH, 跟踪绝不off/绝不墓地"}
    # 无价格信号 + 无主题命中 + 零催化 → 唯一合理 off
    return "off", {"reason": "no_signal_no_theme_no_catalyst"}


def priority_from_fit(fit, info=None, registry=None):
    """由**已存** theme_fit 直接映射权重。优先用这个，不要在 _score 里重算 fit ——
    重算会与 enrich 阶段写入的 theme_fit 形成「双判据冲突」（2026-10-26 实测：
    605488 已存 theme_fit=on，但重算被判 ?，score 从 1.0 掉到 0.3）。"""
    if registry is None:
        registry = get_registry()
    mapping = {"on": HOT_W, "weak": WARM_W, "emerging": EMERGING_W,
               "?": UNKNOWN_W, "off": 0.0}
    base = mapping.get(fit, UNKNOWN_W)
    theme_hit = (info or {}).get("theme")
    return base + (POLICY_BOOST_W if theme_hit in registry.get("policy_boost", []) else 0.0)


def priority_for(code, research_entry, registry=None):
    """返回生命周期容量驱逐优先级权重（越大越该保留）。热题材被保护、冷题材降级。

    优先级：① 已存 theme_fit（enrich 的权威判定）→ ② 现算 decide_theme_fit。
    """
    if registry is None:
        registry = get_registry()
    stored = (research_entry or {}).get("theme_fit")
    if stored in ("on", "weak", "emerging", "?", "off"):
        return priority_from_fit(stored, {"theme": (research_entry or {}).get("theme")}, registry)
    fit, info = decide_theme_fit(code, research_entry, has_price_signal=True, registry=registry)
    return priority_from_fit(fit, info, registry)


def update_registry(themes_delta, note=None):
    """合并情报研判到登记表。themes_delta: {key: {status, weight, evidence, keywords?, name?}}
    支持新增 emerging 主题（原登记表无该 key 则追加）。返回更新后的 registry。"""
    reg = load_registry()
    reg.setdefault("themes", {})
    reg.setdefault("demoted", [])
    reg.setdefault("policy_boost", [])
    for key, d in (themes_delta or {}).items():
        if key in reg["themes"]:
            t = reg["themes"][key]
            if "status" in d:
                t["status"] = d["status"]
            if "weight" in d:
                t["weight"] = d["weight"]
            if "evidence" in d:
                t["evidence"] = d["evidence"]
            if "keywords" in d:
                t["keywords"] = d["keywords"]
            if "name" in d:
                t["name"] = d["name"]
        else:
            reg["themes"][key] = {
                "name": d.get("name", key),
                "status": d.get("status", "emerging"),
                "weight": d.get("weight", EMERGING_W),
                "evidence": d.get("evidence", "情报研判新增"),
                "keywords": d.get("keywords", []),
            }
        if reg["themes"].get(key, {}).get("status") == "cold" and key not in reg["demoted"]:
            reg["demoted"].append(key)
    reg["updated"] = datetime.date.today().isoformat()
    if note:
        reg["policy_note"] = note
    json.dump(reg, open(REGISTRY_P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return reg


# ==================== 误配回归测试（2026-10-26）====================
# 每个 case = (说明, theme 字段文本, 禁止命中的主题key, 是否要求反例必须存在)
# 这是把当天 5 例真实误配固化成断言：以后改关键词/改匹配逻辑，跑一遍就知道有没有回退。
# 「不得自证」原则：生产者（改关键词的人）无权自判合格，必须由这份可复跑测试裁定。
CONFUSION_CASES = [
    ("金丹科技 3D打印设备", "3D打印设备", "semiconductor_local"),
    ("山外山 血液净化设备", "血液净化设备", "semiconductor_local"),
    ("德尔股份 电机电泵", "电机电泵", "robot_components"),
    ("盛天网络 算力带宽", "算力带宽", "ai_compute"),
    ("百洋医药 数据集", "数据集", "data_label"),
    ("风电减速器(非机器人)", "风电减速器", "harmonic_reducer"),
    ("骨科关节(非人形)", "骨科关节 人工关节", "humanoid_robot"),
]

# 正例：收紧关键词后必须仍然命中，否则说明修过头了（过度收紧=新缺陷）
POSITIVE_CASES = [
    ("人形机器人本体", "人形机器人 具身智能", "humanoid_robot"),
    ("谐波减速器", "谐波减速器 丝杆", "harmonic_reducer"),
    ("半导体设备国产替代", "半导体设备 国产替代", "semiconductor_local"),
    ("商业航天", "商业航天 卫星互联网", "commercial_aerospace"),
    ("数据要素", "数据要素 公共数据授权运营", "data_label"),
    ("光模块", "光模块 CPO", "optical_comm"),
]


def selftest_confusion():
    """返回 (passed, failed_list)。不写任何文件，纯判定。"""
    reg = get_registry()
    fails = []
    for name, text, forbid in CONFUSION_CASES:
        hits = [h[0] for h in match_theme(text, reg)]
        on_hits = [h[0] for h in match_theme(text, reg, min_len=ON_MIN_LEN)]
        if forbid in hits or forbid in on_hits:
            fit, info = decide_theme_fit("-", {"theme": text}, has_price_signal=True, registry=reg)
            fails.append("误配未修: %s → 命中 %s (fit=%s, %s)"
                         % (name, forbid, fit, info.get("reason")))
    for name, text, expect in POSITIVE_CASES:
        hits = [h[0] for h in match_theme(text, reg)]
        if expect not in hits:
            fails.append("过度收紧: %s → 期望命中 %s 但未命中（新缺陷）" % (name, expect))
    total = len(CONFUSION_CASES) + len(POSITIVE_CASES)
    return (total - len(fails), total), fails


if __name__ == "__main__":
    if "--selftest-confusion" in sys.argv:
        (ok, total), fails = selftest_confusion()
        print("[theme_intel] 误配回归测试: %d/%d 通过" % (ok, total))
        for f in fails:
            print("  ✗ %s" % f)
        sys.exit(0 if not fails else 1)
    # 自测：用 candidate_research.json 现有 70 条回灌判定，验证「discovery 路径永不 off」安全网
    RESEARCH_P = os.path.join(ROOT, "data", "candidate_research.json")
    research = json.load(open(RESEARCH_P, encoding="utf-8")) if os.path.exists(RESEARCH_P) else {}
    reg = get_registry()
    dist = {}
    protected = []  # 手动判 off，但新逻辑会保护（emerging/?）的票
    for code, e in research.items():
        fit, info = decide_theme_fit(code, e, has_price_signal=True, registry=reg)
        old = e.get("theme_fit")
        dist[fit] = dist.get(fit, 0) + 1
        if old == "off" and fit != "off":
            protected.append((code, e.get("name"), old, fit, info.get("reason")))
    print("[theme_intel] 自测 %d 条已存研判，新 fit 分布: %s" % (len(research), dist))
    print("[theme_intel] 被安全网保护的原 off 票(共 %d 只, 这些正是过去会漏掉的):" % len(protected))
    for p in protected:
        print("  %s %s 旧=off -> 新=%s (%s)" % (p[0], p[1], p[3], p[4]))
