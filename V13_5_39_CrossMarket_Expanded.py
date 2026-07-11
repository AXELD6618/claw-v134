#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.39 跨市场映射扩展 — 港股/日股/大宗商品
==============================================
在V13.5.37美股映射基础上扩展:
  1. 港股映射: 腾讯→游戏/美团→本地生活/比亚迪→新能源
  2. 日股映射: 索尼→CMOS/东京电子→半导体设备
  3. 大宗商品: 黄金→黄金股/原油→油气股/铜→铜矿股/锂→锂电

Author: 毕方灵犀貔貅助手 V13.5.39
Date: 2026-07-11
"""

import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = BASE / "data"
CROSSMARKET_DIR = DATA_DIR / "crossmarket"
CROSSMARKET_DIR.mkdir(parents=True, exist_ok=True)

EXPANDED_MAPPING_FILE = CROSSMARKET_DIR / "expanded_mapping.json"


@dataclass
class MarketMapping:
    """跨市场映射条目"""
    market: str                # US/HK/JP/COMMODITY
    ticker: str                # 标的代码
    name: str                  # 标的名称
    cn_sector: str             # A股板块
    cn_stocks: List[str]       # A股关联个股
    mapping_type: str          # direct/indirect/sentiment
    description: str           # 映射描述
    impact_factor: float       # 影响系数(0.1-1.0)
    signal_keywords: List[str] # 触发关键词


# ============================================================
# 扩展映射表 — 港股+日股+大宗商品
# ============================================================
EXPANDED_MAPPINGS: List[MarketMapping] = [
    # ==================== 港股 ====================
    MarketMapping("HK", "00700", "腾讯控股", "游戏/AI应用",
        ["002555", "002602", "300315", "300418", "600242"],
        "direct",
        "腾讯游戏龙头, A股游戏/IP/AI应用直接关联",
        0.9, ["腾讯", "游戏", "微信", "王者荣耀", "视频号"]),
    MarketMapping("HK", "03690", "美团点评", "本地生活/即时零售",
        ["002555", "603596", "605136", "002116"],
        "indirect",
        "美团本地生活龙头, A股即时零售/配送相关",
        0.6, ["美团", "本地生活", "即时零售", "外卖"]),
    MarketMapping("HK", "01211", "比亚迪股份", "新能源汽车",
        ["002594", "300750", "300014", "002460", "600089"],
        "direct",
        "比亚迪新能源车A+H, A股电池/电机/电控直接供应链",
        0.95, ["比亚迪", "新能源车", "刀片电池", "DM-i"]),
    MarketMapping("HK", "09888", "百度集团", "AI/自动驾驶",
        ["002405", "300024", "688181", "002916"],
        "indirect",
        "百度AI/萝卜快跑自动驾驶, A股导航/AI芯片关联",
        0.7, ["百度", "文心", "萝卜快跑", "Apollo", "自动驾驶"]),
    MarketMapping("HK", "09618", "京东集团", "电商/物流",
        ["002468", "601021", "600029", "002183"],
        "indirect",
        "京东电商物流龙头, A股物流/仓储关联",
        0.5, ["京东", "电商", "物流", "供应链"]),
    MarketMapping("HK", "09988", "阿里巴巴", "云计算/AI",
        ["603019", "002313", "300308", "300017"],
        "direct",
        "阿里云算力/AI大模型, A股云基建/算力直接关联",
        0.85, ["阿里", "阿里云", "通义", "淘宝", "云计算"]),
    MarketMapping("HK", "00388", "港交所", "金融/券商",
        ["601688", "600030", "601633", "000776"],
        "sentiment",
        "港交所交投活跃度→A股券商情绪传导",
        0.4, ["港交所", "成交额", "南下资金", "港股通"]),
    MarketMapping("HK", "01024", "快手科技", "短视频/AI",
        ["300024", "002555", "300468"],
        "indirect",
        "快手短视频/AI推荐, A股营销/内容相关",
        0.5, ["快手", "短视频", "直播", "AI推荐"]),

    # ==================== 日股 ====================
    MarketMapping("JP", "6758.T", "索尼集团", "CMOS图像传感器",
        ["603501", "688012", "002408", "300613"],
        "direct",
        "索尼CMOS传感器全球龙头, A股半导体设备/材料直接供应链",
        0.85, ["索尼", "Sony", "CMOS", "图像传感器", "CIS"]),
    MarketMapping("JP", "8035.T", "东京电子", "半导体设备",
        ["603501", "688012", "002408", "688396", "688082"],
        "direct",
        "东京电子半导体设备龙头, A股半导体设备直接对标",
        0.9, ["东京电子", "Tokyo Electron", "半导体设备", "刻蚀", "成膜"]),
    MarketMapping("JP", "6861.T", "基恩士", "机器视觉/传感器",
        ["002970", "688317", "300449", "300024"],
        "indirect",
        "基恩士机器视觉龙头, A股机器视觉/传感器间接关联",
        0.6, ["基恩士", "Keyence", "机器视觉", "传感器"]),
    MarketMapping("JP", "6501.T", "日立", "电力设备/核电",
        ["600406", "601179", "601727", "300040"],
        "indirect",
        "日立电力/核电设备, A股电力设备间接关联",
        0.5, ["日立", "Hitachi", "核电", "电力设备"]),
    MarketMapping("JP", "6702.T", "三菱电机", "功率半导体",
        ["600519", "603986", "600584", "688396"],
        "indirect",
        "三菱电机功率半导体/工控, A股功率器件间接关联",
        0.5, ["三菱电机", "Mitsubishi", "功率半导体", "IGBT"]),
    MarketMapping("JP", "7269.T", "丰田汽车", "汽车/氢能",
        ["600104", "601238", "601633", "300750"],
        "sentiment",
        "丰田汽车龙头, A股汽车产业链情绪传导",
        0.4, ["丰田", "Toyota", "氢能", "混动", "汽车"]),
    MarketMapping("JP", "7203.T", "本田汽车", "机器人/汽车",
        ["300024", "002747", "601238"],
        "sentiment",
        "本田机器人/汽车, A股机器人概念情绪传导",
        0.3, ["本田", "Honda", "ASIMO", "人形机器人"]),

    # ==================== 大宗商品 ====================
    MarketMapping("COMMODITY", "GOLD", "国际金价", "黄金",
        ["600547", "600916", "002155", "600385", "000975"],
        "direct",
        "国际金价→A股黄金矿企直接受益",
        0.95, ["黄金", "金价", "COMEX", "伦敦金", "避险"]),
    MarketMapping("COMMODITY", "BRENT", "布伦特原油", "石油石化",
        ["600028", "601857", "600688", "002207", "600346"],
        "direct",
        "国际油价→A股油气开采/炼化直接受益",
        0.9, ["原油", "油价", "布伦特", "OPEC", "WTI"]),
    MarketMapping("COMMODITY", "COPPER", "国际铜价", "铜矿",
        ["601899", "600362", "000630", "000878", "600259"],
        "direct",
        "国际铜价→A股铜矿/冶炼直接受益",
        0.9, ["铜", "铜价", "LME", "智利", "铜矿"]),
    MarketMapping("COMMODITY", "LITHIUM", "碳酸锂价格", "锂电",
        ["002460", "002466", "300750", "002340", "002842"],
        "direct",
        "碳酸锂价格→A股锂矿/锂电材料直接受益",
        0.95, ["锂", "碳酸锂", "锂矿", "锂电", "盐湖"]),
    MarketMapping("COMMODITY", "RE", "稀土价格", "稀土永磁",
        ["600111", "600392", "000831", "002057", "300127"],
        "direct",
        "稀土价格→A股稀土/永磁直接受益",
        0.95, ["稀土", "镨钕", "永磁", "氧化镨", "镝"]),
    MarketMapping("COMMODITY", "ALUMINUM", "国际铝价", "电解铝",
        ["601600", "000807", "002532", "600219"],
        "direct",
        "国际铝价→A股电解铝直接受益",
        0.85, ["铝", "电解铝", "氧化铝", "LME铝"]),
    MarketMapping("COMMODITY", "SILVER", "国际银价", "白银",
        ["600547", "000603", "002155"],
        "indirect",
        "银价→A股白银概念间接关联",
        0.7, ["白银", "银价", "COMEX白银"]),

    # ==================== 美股补充 (V13.5.37已有27条, 新增) ====================
    MarketMapping("US", "AMZN", "亚马逊", "云计算/AI",
        ["603019", "002313", "300308", "300017"],
        "direct",
        "亚马逊AWS云龙头, ASIC服务器上调→A股云基建受益",
        0.85, ["亚马逊", "AWS", "ASIC", "云计算", "云服务"]),
    MarketMapping("US", "GOOGL", "谷歌", "AI/云计算",
        ["300308", "002313", "603019", "300017"],
        "indirect",
        "谷歌AI/云/液冷, A股光模块/液冷间接关联",
        0.7, ["谷歌", "Google", "Gemini", "TPU", "液冷"]),
    MarketMapping("US", "PLTR", "Palantir", "大数据/AI应用",
        ["300024", "002230", "688561"],
        "sentiment",
        "Palantir大数据AI, A股AI应用情绪传导",
        0.4, ["Palantir", "大数据", "AI应用", "数据分析"]),
    MarketMapping("US", "SNOW", "Snowflake", "数据云",
        ["300024", "002230", "688561"],
        "sentiment",
        "Snowflake数据云, A股数据要素情绪传导",
        0.3, ["Snowflake", "数据云", "数据要素"]),
]


def get_expanded_mapping_stats() -> Dict:
    """获取扩展映射统计"""
    by_market = defaultdict(int)
    by_type = defaultdict(int)
    for m in EXPANDED_MAPPINGS:
        by_market[m.market] += 1
        by_type[m.mapping_type] += 1
    return {
        "total": len(EXPANDED_MAPPINGS),
        "by_market": dict(by_market),
        "by_type": dict(by_type),
    }


def save_expanded_mapping():
    """保存扩展映射表"""
    data = [asdict(m) for m in EXPANDED_MAPPINGS]
    with open(EXPANDED_MAPPING_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[CrossMarket] 扩展映射保存: {EXPANDED_MAPPING_FILE} ({len(data)}条)")


def main():
    print("=" * 60)
    print("V13.5.39 跨市场映射扩展 — 港股/日股/大宗商品")
    print("=" * 60)

    stats = get_expanded_mapping_stats()
    print(f"\n总映射条目: {stats['total']}")
    print(f"\n按市场分布:")
    for market, count in sorted(stats["by_market"].items()):
        labels = {"US": "美股", "HK": "港股", "JP": "日股", "COMMODITY": "大宗商品"}
        print(f"  {labels.get(market, market):8s}: {count:3d}条")
    print(f"\n按映射类型:")
    for mtype, count in sorted(stats["by_type"].items()):
        print(f"  {mtype:10s}: {count:3d}条")

    # 打印部分映射示例
    print(f"\n新增映射示例:")
    for m in EXPANDED_MAPPINGS[:5]:
        print(f"  [{m.market}] {m.name} → {m.cn_sector} (影响={m.impact_factor}, {m.mapping_type})")

    save_expanded_mapping()

    print(f"\n{'='*60}")
    print(f"V13.5.39 跨市场映射扩展完成!")
    print(f"  原有美股映射: 27条 (V13.5.37)")
    print(f"  新增映射: {stats['total']}条 (港股8+日股7+大宗7+美股补充3)")
    print(f"  总映射: {27 + stats['total']}条")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
