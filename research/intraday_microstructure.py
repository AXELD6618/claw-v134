# -*- coding: utf-8 -*-
"""
research/intraday_microstructure.py — 14:50 盘口/委托流微观结构特征（圣杯突破特征类）
================================================================================
为什么存在：突破战役 §11/§12 诊断——圣杯级 edge 不在历史日线/薄关系数据里；唯一可能承载它的
特征类是 T 日 14:50 可观测的"盘口/委托流微观结构"。实测 qt.gtimg.cn 快照（~ 分隔，88 字段）
即包含这些字段，但旧解析器（_intraday_pull_1125.py）把它们丢弃了。本模块把它们解析出来。

已用真实 payload 钉死的字段索引（2026-11-06 实测 sh600519）：
  f[3]=当前价  f[4]=昨收  f[5]=今开  f[6]=成交量(手)
  f[7]=外盘(主动买,手)  f[8]=内盘(主动卖,手)  —— 校验：f[7]+f[8]==f[6] 精确成立
  f[9..28]=五档(买一~买五价/量、卖一~卖五价/量，交替)
  f[30]=时间  f[31]=涨跌  f[32]=涨跌%  f[33]=最高  f[34]=最低
  f[37]=委差(手)  f[38]=委比(比例 -1..1 或 百分比)  f[46]=量比

注意：盘后(16:14)快照五档会被清空/打乱（实测买一>卖一，不可能），故五档深度仅盘中有效；
      本模块对五档"尽力而为"，主特征（外盘/内盘比、委比、量比、尾盘位置）盘中稳健。

特征（均 point-in-time，14:50 可见）：
  active_buy_ratio = 外盘/(外盘+内盘)        主动买占比（收集强度，>0.56 偏强）
  weibi            = 委比（归一化到 -1..1）   盘口净偏向
  weicha           = 委差（手）               未成交净买盘体量
  vol_ratio        = 量比                     是否异常活跃（>=1.2 确认有资金）
  tail_position    = (价-最低)/(最高-最低)    尾盘相对当日振幅位置（>=0.55 偏强/尾盘拉升）
  chg_pct          = (价-昨收)/昨收           当日涨幅（已在可买宇宙内：未涨停）

accumulation_gate：上述齐备 + active_buy_ratio>=0.56 + weibi>=0.05 + vol_ratio>=1.2
                   + tail_position>=0.55 + 未涨停 → 判定"聪明钱埋伏+尾盘强势收集"（MICRO_accum）。
                   这是把龙虎榜(3.5× 涨停率 lift、负期望彩票型) 提升为"正期望+方向性"的精度过滤器。

用法：python -m research.intraday_microstructure   （内置单元自测：合成 14:50 payload + 可选真抓取）
"""
from __future__ import annotations
import sys, math


# ── gtimg 字段索引（实测钉死）──
F_PRICE = 3; F_PREV = 4; F_OPEN = 5; F_VOL = 6
F_ACTIVE_BUY = 7; F_ACTIVE_SELL = 8          # 外盘 / 内盘
F_TIME = 30; F_CHG = 31; F_CHG_PCT = 32; F_HIGH = 33; F_LOW = 34
F_WEI_CHA = 37; F_WEI_BI = 38               # 委差(手) / 委比(比例或百分比)
F_VOL_RATIO = 46
# 五档：f[9]=买一价 f[10]=买一量 f[11]=卖一价 f[12]=卖一量 … 交替（买二/卖二…买五/卖五）


def parse_gtimg_line(line: str) -> dict or None:
    """解析单行 qt.gtimg.cn 响应（v_xxx="..."）。返回原始字段 dict 或 None。"""
    if "=" not in line:
        return None
    val = line.split("=", 1)[1].strip().strip('"')
    if not val:
        return None
    f = val.split("~")
    if len(f) < 47:
        return None
    try:
        code = f[2]
        price = float(f[F_PRICE]); prev = float(f[F_PREV]); op = float(f[F_OPEN])
        vol = float(f[F_VOL]); ab = float(f[F_ACTIVE_BUY]); asl = float(f[F_ACTIVE_SELL])
        hi = float(f[F_HIGH]); lo = float(f[F_LOW])
        wb_raw = float(f[F_WEI_BI]); wc = float(f[F_WEI_CHA]); vr = float(f[F_VOL_RATIO])
        tm = f[F_TIME]
        chg = float(f[F_CHG_PCT])
    except Exception:
        return None
    # 五档尽力而为（盘中才有效）
    depths = {}
    for idx, key in [(9, "b1"), (11, "s1"), (13, "b2"), (15, "s2"), (17, "b3"),
                    (19, "s3"), (21, "b4"), (23, "s4"), (25, "b5"), (27, "s5")]:
        try:
            if idx + 1 < len(f):
                depths[key + "p"] = float(f[idx])
                depths[key + "v"] = float(f[idx + 1])
        except Exception:
            pass
    return dict(code=code, price=price, prev=prev, open=op, vol=vol,
                active_buy=ab, active_sell=asl, high=hi, low=lo,
                weibi_raw=wb_raw, weicha=wc, vol_ratio=vr, time=tm, chg_pct=chg,
                depth=depths)


def _norm_weibi(raw):
    """委比归一化到 -1..1：若绝对值>1.5 视为百分比→/100；再 clamp。"""
    if raw != raw:
        return float("nan")
    v = raw / 100.0 if abs(raw) > 1.5 else raw
    return max(-1.0, min(1.0, v))


def compute_features(raw: dict, board_limit: float) -> dict:
    """从原始字段计算微观结构特征。返回 {valid, active_buy_ratio, weibi, weicha,
    vol_ratio, tail_position, chg_pct, depth_imbalance, is_limit, dist_to_limit}。"""
    price = raw["price"]; prev = raw["prev"]; hi = raw["high"]; lo = raw["low"]
    ab = raw["active_buy"]; asl = raw["active_sell"]; vr = raw["vol_ratio"]
    denom = ab + asl
    # 有效性：价格/昨收>0、在 [low,high] 内、量齐备、未涨停
    valid = (price > 0 and prev > 0 and hi >= lo and denom > 0
             and lo <= price <= hi + 1e-6)
    active_buy_ratio = (ab / denom) if denom > 0 else float("nan")
    weibi = _norm_weibi(raw["weibi_raw"])
    weicha = raw["weicha"]
    tail_position = ((price - lo) / (hi - lo)) if (hi - lo) > 1e-9 else float("nan")
    chg_pct = raw["chg_pct"]
    is_limit = board_limit == board_limit and price >= board_limit - 0.005 if board_limit == board_limit else False
    dist_to_limit = ((board_limit - price) / prev * 100) if (prev > 0 and board_limit == board_limit) else float("nan")
    # 五档深度不平衡（仅盘中有效；盘后打乱则不可用 → 标记 nan）
    d = raw.get("depth", {})
    bv = sum(d.get(k, 0.0) for k in ("b1v", "b2v", "b3v", "b4v", "b5v"))
    sv = sum(d.get(k, 0.0) for k in ("s1v", "s2v", "s3v", "s4v", "s5v"))
    depth_ok = bv > 0 and sv > 0 and d.get("b1p", 0) < d.get("s1p", 1e9)  # 买一<卖一 才可信
    depth_imbalance = ((bv - sv) / (bv + sv)) if (depth_ok and (bv + sv) > 0) else float("nan")
    return dict(valid=valid, active_buy_ratio=active_buy_ratio, weibi=weibi, weicha=weicha,
                vol_ratio=vr, tail_position=tail_position, chg_pct=chg_pct,
                depth_imbalance=depth_imbalance, is_limit=bool(is_limit),
                dist_to_limit=dist_to_limit)


def accumulation_gate(feats: dict) -> bool:
    """MICRO_accum：聪明钱埋伏 + 尾盘强势收集 的二元判定（精度过滤器）。"""
    if not feats.get("valid"):
        return False
    if feats.get("is_limit"):
        return False
    abr = feats.get("active_buy_ratio")
    wb = feats.get("weibi")
    vr = feats.get("vol_ratio")
    tp = feats.get("tail_position")
    if not (abr == abr and wb == wb and vr == vr and tp == tp):
        return False
    return (abr >= 0.56) and (wb >= 0.05) and (vr >= 1.2) and (tp >= 0.55)


def feature_tags(raw: dict, board_limit: float) -> dict:
    """给定 14:50 原始字段 + 涨停价，返回 {feature_name: True} 该股票激活的特征标签。
    供 collector 写入 forward_oos（每个标签一条 record）。"""
    feats = compute_features(raw, board_limit)
    tags = {}
    if feats["valid"]:
        tags["MICRO_valid"] = True
        if accumulation_gate(feats):
            tags["MICRO_accum"] = True
        # 单特征（供独立评估）
        abr = feats["active_buy_ratio"]
        if abr == abr and abr >= 0.58:
            tags["MICRO_activebuy"] = True
        tp = feats["tail_position"]
        if tp == tp and tp >= 0.7:
            tags["MICRO_tailhigh"] = True
    return tags


# ── 单元自测 ──
def _self_test():
    print("[self-test] 合成 14:50 payload（强收集场景）")
    # 价格 10.50 / 昨收 10.00(+5%) / 开 9.90 / 高 10.80 / 低 9.85
    # 外盘 320000 / 内盘 180000 → active_buy_ratio=0.64；委比 0.35；委差 50000；量比 2.5
    f = [""] * 88
    f[1] = "测试股"; f[2] = "000001"; f[3] = "10.50"; f[4] = "10.00"; f[5] = "9.90"
    f[6] = "500000"; f[7] = "320000"; f[8] = "180000"
    f[9] = "10.49"; f[10] = "500"; f[11] = "10.50"; f[12] = "300"   # 买一<卖一 可信
    f[13] = "10.48"; f[14] = "400"; f[15] = "10.51"; f[16] = "200"
    f[17] = "10.47"; f[18] = "300"; f[19] = "10.52"; f[20] = "150"
    f[21] = "10.46"; f[22] = "200"; f[23] = "10.53"; f[24] = "100"
    f[25] = "10.45"; f[26] = "150"; f[27] = "10.54"; f[28] = "80"
    f[30] = "20261109145000"; f[31] = "0.50"; f[32] = "5.00"
    f[33] = "10.80"; f[34] = "9.85"; f[37] = "50000"; f[38] = "0.35"; f[46] = "2.5"
    line = 'v_000001="%s"' % "~".join(f)
    raw = parse_gtimg_line(line)
    assert raw is not None, "parse failed"
    feats = compute_features(raw, board_limit=11.00)   # 涨停价 11.00（10%）
    print("  active_buy_ratio=%.3f (期望 0.64)" % feats["active_buy_ratio"])
    print("  weibi=%.3f (期望 0.35)" % feats["weibi"])
    print("  vol_ratio=%.2f  tail_position=%.3f (期望 0.684)" % (feats["vol_ratio"], feats["tail_position"]))
    print("  depth_imbalance=%.3f" % feats["depth_imbalance"])
    assert abs(feats["active_buy_ratio"] - 0.64) < 1e-6
    assert abs(feats["weibi"] - 0.35) < 1e-6
    assert abs(feats["tail_position"] - (10.50 - 9.85) / (10.80 - 9.85)) < 1e-6
    assert feats["depth_imbalance"] == (500 + 400 + 300 + 200 + 150 - (300 + 200 + 150 + 100 + 80)) / (sum([500,400,300,200,150,300,200,150,100,80]))
    assert accumulation_gate(feats) is True, "accumulation_gate 应 True"
    tags = feature_tags(raw, board_limit=11.00)
    assert "MICRO_accum" in tags and "MICRO_activebuy" in tags, "强收集应激活 MICRO_accum/activebuy"
    print("  accumulation_gate=True；tags=", list(tags.keys()))
    # 反例：弱收集（外盘<内盘）
    f[7] = "180000"; f[8] = "320000"; f[38] = "-0.20"; f[46] = "0.8"
    raw2 = parse_gtimg_line('v_000001="%s"' % "~".join(f))
    feats2 = compute_features(raw2, board_limit=11.00)
    assert accumulation_gate(feats2) is False, "弱收集应 False"
    print("  [反例] 外盘<内盘 → accumulation_gate=False  ✓")
    # 涨停股应被排除
    f[3] = "11.00"; f[7] = "320000"; f[8] = "180000"; f[38] = "0.35"; f[46] = "2.5"
    raw3 = parse_gtimg_line('v_000001="%s"' % "~".join(f))
    assert accumulation_gate(compute_features(raw3, board_limit=11.00)) is False, "涨停应排除"
    print("  [反例] 已涨停 → accumulation_gate=False  ✓")
    print("[self-test] ALL PASS")


if __name__ == "__main__":
    unit_test = True
    for a in sys.argv[1:]:
        if a == "--no-test":
            unit_test = False
    if unit_test:
        _self_test()
