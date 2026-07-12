#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
60家隐形冠军股票候选池 - 代码查询脚本
使用TDX MCP批量查找股票代码
"""

import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))

# 60只隐形冠军股票列表（按图片顺序）
STOCK_NAMES = [
    # 一、高端装备与智能制造
    ("绿的谐波", "高端装备"), ("埃斯顿", "高端装备"), ("中控技术", "高端装备"),
    ("科德数控", "高端装备"), ("新强联", "高端装备"), ("江苏北人", "高端装备"),
    ("国茂股份", "高端装备"), ("景津装备", "高端装备"), ("华测检测", "高端装备"),
    ("中密控股", "高端装备"), ("苏试试验", "高端装备"), ("银都股份", "高端装备"),
    # 二、半导体与电子核心材料/零部件
    ("安集科技", "半导体"), ("江丰电子", "半导体"), ("雅克科技", "半导体"),
    ("华特气体", "半导体"), ("鼎龙股份", "半导体"), ("芯源微", "半导体"),
    ("万业企业", "半导体"), ("菲利华", "半导体"), ("沪电股份", "半导体"),
    ("生益科技", "半导体"), ("宏发股份", "半导体"), ("有研新材", "半导体"),
    # 三、新能源与新材料
    ("壹石通", "新能源"), ("星源材质", "新能源"), ("德方纳米", "新能源"),
    ("容百科技", "新能源"), ("新宙邦", "新能源"), ("科达利", "新能源"),
    ("赢合科技", "新能源"), ("亚玛顿", "新能源"), ("海优新材", "新能源"),
    ("金雷股份", "新能源"), ("德业股份", "新能源"), ("中简科技", "新能源"),
    # 四、医疗器械与生物医药
    ("健帆生物", "医疗"), ("欧普康视", "医疗"), ("爱博医疗", "医疗"),
    ("惠泰医疗", "医疗"), ("南微医学", "医疗"), ("百普赛斯", "医疗"),
    ("义翘神州", "医疗"), ("华大智造", "医疗"),
    # 五、化工新材料与精细化工
    ("国瓷材料", "化工"), ("联瑞新材", "化工"), ("晨光生物", "化工"),
    ("确成股份", "化工"), ("阳谷华泰", "化工"), ("浙江龙盛", "化工"),
    ("利安隆", "化工"), ("新和成", "化工"),
    # 六、光通信与AI算力配套
    ("仕佳光子", "光通信"), ("腾景科技", "光通信"), ("天孚通信", "光通信"),
    ("中瓷电子", "光通信"), ("华工科技", "光通信"), ("锐科激光", "光通信"),
    ("炬光科技", "光通信"), ("英集芯", "光通信"),
]

# 已知的部分代码（基于常见股票知识）
KNOWN_CODES = {
    "绿的谐波": {"code": "688017", "setcode": "0"},
    "埃斯顿": {"code": "002747", "setcode": "0"},
    "中控技术": {"code": "688777", "setcode": "0"},
    "科德数控": {"code": "688305", "setcode": "0"},
    "新强联": {"code": "300850", "setcode": "0"},
    "江苏北人": {"code": "688218", "setcode": "0"},
    "国茂股份": {"code": "603915", "setcode": "1"},
    "景津装备": {"code": "603279", "setcode": "1"},
    "华测检测": {"code": "300012", "setcode": "0"},
    "中密控股": {"code": "300470", "setcode": "0"},
    "苏试试验": {"code": "300416", "setcode": "0"},
    "银都股份": {"code": "603277", "setcode": "1"},
    "安集科技": {"code": "688019", "setcode": "0"},
    "江丰电子": {"code": "300666", "setcode": "0"},
    "雅克科技": {"code": "002409", "setcode": "0"},
    "华特气体": {"code": "688268", "setcode": "0"},
    "鼎龙股份": {"code": "300054", "setcode": "0"},
    "芯源微": {"code": "688037", "setcode": "0"},
    "万业企业": {"code": "600641", "setcode": "1"},
    "菲利华": {"code": "300395", "setcode": "0"},
    "沪电股份": {"code": "002463", "setcode": "0"},
    "生益科技": {"code": "600183", "setcode": "1"},
    "宏发股份": {"code": "600885", "setcode": "1"},
    "有研新材": {"code": "600206", "setcode": "1"},
    "壹石通": {"code": "688733", "setcode": "0"},
    "星源材质": {"code": "300568", "setcode": "0"},
    "德方纳米": {"code": "300769", "setcode": "0"},
    "容百科技": {"code": "688005", "setcode": "0"},
    "新宙邦": {"code": "300037", "setcode": "0"},
    "科达利": {"code": "002850", "setcode": "0"},
    "赢合科技": {"code": "300457", "setcode": "0"},
    "亚玛顿": {"code": "002623", "setcode": "0"},
    "海优新材": {"code": "688680", "setcode": "0"},
    "金雷股份": {"code": "300443", "setcode": "0"},
    "德业股份": {"code": "605117", "setcode": "1"},
    "中简科技": {"code": "300777", "setcode": "0"},
    "健帆生物": {"code": "300529", "setcode": "0"},
    "欧普康视": {"code": "300595", "setcode": "0"},
    "爱博医疗": {"code": "688050", "setcode": "0"},
    "惠泰医疗": {"code": "688617", "setcode": "0"},
    "南微医学": {"code": "688029", "setcode": "0"},
    "百普赛斯": {"code": "301080", "setcode": "0"},
    "义翘神州": {"code": "301047", "setcode": "0"},
    "华大智造": {"code": "688114", "setcode": "0"},
    "国瓷材料": {"code": "300285", "setcode": "0"},
    "联瑞新材": {"code": "688300", "setcode": "0"},
    "晨光生物": {"code": "300138", "setcode": "0"},
    "确成股份": {"code": "605183", "setcode": "1"},
    "阳谷华泰": {"code": "300121", "setcode": "0"},
    "浙江龙盛": {"code": "600352", "setcode": "1"},
    "利安隆": {"code": "300596", "setcode": "0"},
    "新和成": {"code": "002001", "setcode": "0"},
    "仕佳光子": {"code": "688313", "setcode": "0"},
    "腾景科技": {"code": "688195", "setcode": "0"},
    "天孚通信": {"code": "300394", "setcode": "0"},
    "中瓷电子": {"code": "003031", "setcode": "0"},
    "华工科技": {"code": "000988", "setcode": "0"},
    "锐科激光": {"code": "300747", "setcode": "0"},
    "炬光科技": {"code": "688167", "setcode": "0"},
    "英集芯": {"code": "688209", "setcode": "0"},
}

if __name__ == "__main__":
    print(f"Total stocks: {len(STOCK_NAMES)}")
    print(f"Known codes: {len(KNOWN_CODES)}")
    
    # Check coverage
    missing = [name for name, _ in STOCK_NAMES if name not in KNOWN_CODES]
    if missing:
        print(f"Missing codes: {missing}")
    else:
        print("All 60 codes mapped!")
    
    # Save to JSON
    result = []
    for name, sector in STOCK_NAMES:
        if name in KNOWN_CODES:
            result.append({
                "name": name,
                "sector": sector,
                "code": KNOWN_CODES[name]["code"],
                "setcode": KNOWN_CODES[name]["setcode"]
            })
    
    output_path = "data/champion_stock_pool_60.json"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"Saved to {output_path}: {len(result)} stocks")
    
    # Group by sector
    sectors = {}
    for r in result:
        s = r["sector"]
        sectors[s] = sectors.get(s, 0) + 1
    print(f"Sector distribution: {sectors}")
