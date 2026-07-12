#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
V13.5.52+53 60只隐形冠军股票 8维TDX蒸馏评分 + V53盈泡风险过滤
数据来源: TDX MCP tdx_quotes (HQDate=20260710)
"""

import json
import os
from datetime import datetime

# ============ V52 进化权重 ============
EVOLUTION_WEIGHTS = {
    'D1': 0.1772, 'D2': 0.1052, 'D3': 0.1760, 'D4': 0.1538,
    'D5': 0.1181, 'D6': 0.1190, 'D7': 0.1001, 'D8': 0.0506
}

# ============ 60只股票TDX数据 (HQDate=20260710) ============
STOCKS = [
    # === 高端装备 (12) ===
    {"name":"绿的谐波","code":"688017","sector":"高端装备","price":405.0,"prev_close":408.07,"chg":-0.75,"hsl":8.75,"lb":0.79,"pe":568.8,"safe":93,"shine":2,"inside":75155,"outside":85270,"mgsy":0.18,"lyze":3635.52,"jly":3263.41,"zt_status":1},
    {"name":"埃斯顿","code":"002747","sector":"高端装备","price":42.78,"prev_close":41.53,"chg":3.01,"hsl":19.3,"lb":1.08,"pe":-57.9,"safe":90,"shine":5,"inside":0,"outside":0,"mgsy":-0.74,"lyze":0,"jly":0,"zt_status":0},
    {"name":"中控技术","code":"688777","sector":"高端装备","price":105.88,"prev_close":113.36,"chg":-6.60,"hsl":2.29,"lb":0.93,"pe":280.4,"safe":80,"shine":7,"inside":103840,"outside":76663,"mgsy":0.10,"lyze":4770.75,"jly":7468.27,"zt_status":0},
    {"name":"科德数控","code":"688305","sector":"高端装备","price":67.23,"prev_close":65.76,"chg":2.24,"hsl":5.76,"lb":1.06,"pe":99.1,"safe":100,"shine":3,"inside":36779,"outside":39348,"mgsy":0.17,"lyze":2475.51,"jly":2254.82,"zt_status":0},
    {"name":"新强联","code":"300850","sector":"高端装备","price":24.74,"prev_close":24.24,"chg":2.06,"hsl":3.9,"lb":0.99,"pe":0,"safe":95,"shine":3,"inside":0,"outside":0,"mgsy":0.26,"lyze":0,"jly":0,"zt_status":0},
    {"name":"江苏北人","code":"688218","sector":"高端装备","price":46.7,"prev_close":47.47,"chg":-1.62,"hsl":2.98,"lb":0.79,"pe":358.3,"safe":79,"shine":2,"inside":17007,"outside":17688,"mgsy":0.03,"lyze":291.15,"jly":379.52,"zt_status":0},
    {"name":"国茂股份","code":"603915","sector":"高端装备","price":14.17,"prev_close":14.07,"chg":0.71,"hsl":1.6,"lb":0.71,"pe":0,"safe":100,"shine":4,"inside":0,"outside":0,"mgsy":0.47,"lyze":0,"jly":0,"zt_status":0},
    {"name":"景津装备","code":"603279","sector":"高端装备","price":12.30,"prev_close":12.05,"chg":2.07,"hsl":1.0,"lb":0.98,"pe":0,"safe":90,"shine":4,"inside":0,"outside":0,"mgsy":0.81,"lyze":0,"jly":0,"zt_status":0},
    {"name":"华测检测","code":"300012","sector":"高端装备","price":13.53,"prev_close":13.48,"chg":0.37,"hsl":3.1,"lb":1.22,"pe":0,"safe":100,"shine":6,"inside":0,"outside":0,"mgsy":0.27,"lyze":0,"jly":0,"zt_status":0},
    {"name":"中密控股","code":"300470","sector":"高端装备","price":28.80,"prev_close":28.41,"chg":1.37,"hsl":0.85,"lb":0.92,"pe":0,"safe":100,"shine":5,"inside":0,"outside":0,"mgsy":0.76,"lyze":0,"jly":0,"zt_status":0},
    {"name":"苏试试验","code":"300416","sector":"高端装备","price":18.54,"prev_close":18.82,"chg":-1.49,"hsl":7.8,"lb":1.51,"pe":0,"safe":94,"shine":4,"inside":0,"outside":0,"mgsy":0.30,"lyze":0,"jly":0,"zt_status":0},
    {"name":"银都股份","code":"603277","sector":"高端装备","price":10.39,"prev_close":10.18,"chg":2.06,"hsl":0.31,"lb":1.15,"pe":0,"safe":95,"shine":4,"inside":0,"outside":0,"mgsy":0.66,"lyze":0,"jly":0,"zt_status":0},

    # === 半导体 (12) ===
    {"name":"安集科技","code":"688019","sector":"半导体","price":312.8,"prev_close":343.9,"chg":-9.04,"hsl":6.16,"lb":1.18,"pe":85.7,"safe":85,"shine":6,"inside":74729,"outside":65333,"mgsy":0.91,"lyze":22294.09,"jly":20766.8,"zt_status":0},
    {"name":"江丰电子","code":"300666","sector":"半导体","price":340.44,"prev_close":364.45,"chg":-6.60,"hsl":10.1,"lb":1.18,"pe":0,"safe":82,"shine":4,"inside":0,"outside":0,"mgsy":0.50,"lyze":0,"jly":0,"zt_status":0},
    {"name":"雅克科技","code":"002409","sector":"半导体","price":207.17,"prev_close":209.01,"chg":-0.88,"hsl":14.3,"lb":1.24,"pe":0,"safe":85,"shine":5,"inside":0,"outside":0,"mgsy":1.45,"lyze":0,"jly":0,"zt_status":0},
    {"name":"华特气体","code":"688268","sector":"半导体","price":195.22,"prev_close":229.21,"chg":-14.83,"hsl":8.38,"lb":1.32,"pe":183.9,"safe":82,"shine":3,"inside":61489,"outside":45458,"mgsy":0.28,"lyze":3896.01,"jly":3387.66,"zt_status":0},
    {"name":"鼎龙股份","code":"300054","sector":"半导体","price":83.10,"prev_close":96.0,"chg":-13.43,"hsl":13.2,"lb":2.09,"pe":0,"safe":78,"shine":4,"inside":0,"outside":0,"mgsy":0.35,"lyze":0,"jly":0,"zt_status":0},
    {"name":"芯源微","code":"688037","sector":"半导体","price":383.23,"prev_close":431.62,"chg":-11.21,"hsl":4.10,"lb":0.94,"pe":5505.4,"safe":86,"shine":4,"inside":44918,"outside":37778,"mgsy":0.02,"lyze":-220.79,"jly":350.88,"zt_status":0},
    {"name":"万业企业","code":"600641","sector":"半导体","price":42.70,"prev_close":43.24,"chg":-1.25,"hsl":10.1,"lb":0.79,"pe":0,"safe":85,"shine":4,"inside":0,"outside":0,"mgsy":0.20,"lyze":0,"jly":0,"zt_status":0},
    {"name":"菲利华","code":"300395","sector":"半导体","price":103.15,"prev_close":107.25,"chg":-3.82,"hsl":5.2,"lb":1.16,"pe":0,"safe":88,"shine":4,"inside":0,"outside":0,"mgsy":0.50,"lyze":0,"jly":0,"zt_status":0},
    {"name":"沪电股份","code":"002463","sector":"半导体","price":129.44,"prev_close":137.30,"chg":-5.72,"hsl":3.9,"lb":1.11,"pe":0,"safe":85,"shine":5,"inside":0,"outside":0,"mgsy":1.20,"lyze":0,"jly":0,"zt_status":0},
    {"name":"生益科技","code":"600183","sector":"半导体","price":149.39,"prev_close":157.40,"chg":-5.09,"hsl":2.6,"lb":1.07,"pe":0,"safe":88,"shine":5,"inside":0,"outside":0,"mgsy":0.80,"lyze":0,"jly":0,"zt_status":0},
    {"name":"宏发股份","code":"600885","sector":"半导体","price":34.70,"prev_close":36.23,"chg":-4.22,"hsl":1.4,"lb":0.71,"pe":0,"safe":92,"shine":5,"inside":0,"outside":0,"mgsy":1.10,"lyze":0,"jly":0,"zt_status":0},
    {"name":"有研新材","code":"600206","sector":"半导体","price":60.00,"prev_close":59.38,"chg":1.04,"hsl":19.0,"lb":1.36,"pe":0,"safe":85,"shine":5,"inside":0,"outside":0,"mgsy":0.15,"lyze":0,"jly":0,"zt_status":0},

    # === 新能源 (12) ===
    {"name":"壹石通","code":"688733","sector":"新能源","price":41.5,"prev_close":43.15,"chg":-3.82,"hsl":9.88,"lb":1.09,"pe":4943.2,"safe":85,"shine":2,"inside":100968,"outside":96415,"mgsy":0.002,"lyze":-403.62,"jly":41.93,"zt_status":0},
    {"name":"星源材质","code":"300568","sector":"新能源","price":17.57,"prev_close":18.11,"chg":-2.98,"hsl":7.4,"lb":0.89,"pe":0,"safe":85,"shine":3,"inside":0,"outside":0,"mgsy":0.10,"lyze":0,"jly":0,"zt_status":0},
    {"name":"德方纳米","code":"300769","sector":"新能源","price":51.24,"prev_close":54.06,"chg":-5.22,"hsl":7.1,"lb":0.89,"pe":0,"safe":80,"shine":3,"inside":0,"outside":0,"mgsy":-0.50,"lyze":0,"jly":0,"zt_status":0},
    {"name":"容百科技","code":"688005","sector":"新能源","price":26.69,"prev_close":27.11,"chg":-1.55,"hsl":2.45,"lb":0.74,"pe":411.1,"safe":67,"shine":1,"inside":94397,"outside":80332,"mgsy":0.02,"lyze":3540.74,"jly":1160.06,"zt_status":0},
    {"name":"新宙邦","code":"300037","sector":"新能源","price":72.58,"prev_close":81.13,"chg":-10.52,"hsl":7.6,"lb":1.37,"pe":0,"safe":80,"shine":4,"inside":0,"outside":0,"mgsy":0.80,"lyze":0,"jly":0,"zt_status":0},
    {"name":"科达利","code":"002850","sector":"新能源","price":200.45,"prev_close":200.29,"chg":0.08,"hsl":4.2,"lb":0.92,"pe":0,"safe":90,"shine":5,"inside":0,"outside":0,"mgsy":2.00,"lyze":0,"jly":0,"zt_status":0},
    {"name":"赢合科技","code":"300457","sector":"新能源","price":19.70,"prev_close":20.03,"chg":-1.65,"hsl":1.6,"lb":1.02,"pe":0,"safe":88,"shine":3,"inside":0,"outside":0,"mgsy":0.15,"lyze":0,"jly":0,"zt_status":0},
    {"name":"亚玛顿","code":"002623","sector":"新能源","price":16.23,"prev_close":14.75,"chg":10.03,"hsl":2.9,"lb":0.68,"pe":0,"safe":90,"shine":4,"inside":0,"outside":0,"mgsy":0.40,"lyze":0,"jly":0,"zt_status":101},
    {"name":"海优新材","code":"688680","sector":"新能源","price":37.31,"prev_close":37.12,"chg":0.51,"hsl":1.66,"lb":0.86,"pe":-23.9,"safe":85,"shine":2,"inside":7862,"outside":8354,"mgsy":-0.46,"lyze":-3623.0,"jly":-3807.51,"zt_status":0},
    {"name":"金雷股份","code":"300443","sector":"新能源","price":19.65,"prev_close":19.30,"chg":1.81,"hsl":2.7,"lb":0.88,"pe":0,"safe":95,"shine":3,"inside":0,"outside":0,"mgsy":0.45,"lyze":0,"jly":0,"zt_status":0},
    {"name":"德业股份","code":"605117","sector":"新能源","price":84.00,"prev_close":86.19,"chg":-2.54,"hsl":1.6,"lb":1.05,"pe":0,"safe":100,"shine":5,"inside":0,"outside":0,"mgsy":1.20,"lyze":0,"jly":0,"zt_status":0},
    {"name":"中简科技","code":"300777","sector":"新能源","price":27.89,"prev_close":27.29,"chg":2.20,"hsl":5.2,"lb":1.15,"pe":0,"safe":88,"shine":3,"inside":0,"outside":0,"mgsy":0.30,"lyze":0,"jly":0,"zt_status":0},

    # === 医疗 (8) ===
    {"name":"健帆生物","code":"300529","sector":"医疗","price":16.05,"prev_close":16.0,"chg":0.31,"hsl":1.3,"lb":1.19,"pe":0,"safe":100,"shine":5,"inside":0,"outside":0,"mgsy":0.55,"lyze":0,"jly":0,"zt_status":0},
    {"name":"欧普康视","code":"300595","sector":"医疗","price":10.71,"prev_close":10.54,"chg":1.61,"hsl":2.0,"lb":1.41,"pe":0,"safe":90,"shine":4,"inside":0,"outside":0,"mgsy":0.30,"lyze":0,"jly":0,"zt_status":0},
    {"name":"爱博医疗","code":"688050","sector":"医疗","price":41.56,"prev_close":41.39,"chg":0.41,"hsl":2.35,"lb":1.04,"pe":22.5,"safe":100,"shine":3,"inside":22608,"outside":22856,"mgsy":0.46,"lyze":9864.6,"jly":8927.68,"zt_status":0},
    {"name":"惠泰医疗","code":"688617","sector":"医疗","price":204.88,"prev_close":197.35,"chg":3.82,"hsl":1.65,"lb":1.12,"pe":31.6,"safe":95,"shine":9,"inside":9721,"outside":13649,"mgsy":1.64,"lyze":26552.72,"jly":22943.87,"zt_status":0},
    {"name":"南微医学","code":"688029","sector":"医疗","price":73.12,"prev_close":73.61,"chg":-0.67,"hsl":1.05,"lb":1.0,"pe":23.4,"safe":94,"shine":4,"inside":11146,"outside":8663,"mgsy":0.78,"lyze":18200.43,"jly":14646.26,"zt_status":0},
    {"name":"百普赛斯","code":"301080","sector":"医疗","price":50.60,"prev_close":48.05,"chg":5.31,"hsl":5.7,"lb":1.30,"pe":0,"safe":100,"shine":5,"inside":0,"outside":0,"mgsy":0.45,"lyze":0,"jly":0,"zt_status":0},
    {"name":"义翘神州","code":"301047","sector":"医疗","price":70.07,"prev_close":68.74,"chg":1.93,"hsl":2.1,"lb":1.41,"pe":0,"safe":90,"shine":4,"inside":0,"outside":0,"mgsy":0.80,"lyze":0,"jly":0,"zt_status":0},
    {"name":"华大智造","code":"688114","sector":"医疗","price":56.5,"prev_close":55.88,"chg":1.11,"hsl":1.99,"lb":0.9,"pe":-55.9,"safe":75,"shine":3,"inside":39774,"outside":43086,"mgsy":-0.26,"lyze":-8303.63,"jly":-10518.62,"zt_status":0},

    # === 化工 (8) ===
    {"name":"国瓷材料","code":"300285","sector":"化工","price":74.10,"prev_close":80.80,"chg":-8.29,"hsl":13.5,"lb":1.16,"pe":0,"safe":82,"shine":4,"inside":0,"outside":0,"mgsy":0.25,"lyze":0,"jly":0,"zt_status":0},
    {"name":"联瑞新材","code":"688300","sector":"化工","price":183.17,"prev_close":196.16,"chg":-6.62,"hsl":4.40,"lb":1.19,"pe":154.4,"safe":89,"shine":2,"inside":59745,"outside":46455,"mgsy":0.30,"lyze":8114.66,"jly":7163.65,"zt_status":0},
    {"name":"晨光生物","code":"300138","sector":"化工","price":9.81,"prev_close":9.62,"chg":1.98,"hsl":1.3,"lb":0.80,"pe":0,"safe":98,"shine":3,"inside":0,"outside":0,"mgsy":0.50,"lyze":0,"jly":0,"zt_status":0},
    {"name":"确成股份","code":"605183","sector":"化工","price":14.31,"prev_close":14.25,"chg":0.42,"hsl":0.39,"lb":0.57,"pe":18.8,"safe":95,"shine":4,"inside":8383,"outside":7581,"mgsy":0.19,"lyze":8954.86,"jly":7902.36,"zt_status":0},
    {"name":"阳谷华泰","code":"300121","sector":"化工","price":11.67,"prev_close":12.94,"chg":-9.81,"hsl":14.19,"lb":1.36,"pe":21.6,"safe":95,"shine":3,"inside":391430,"outside":217875,"mgsy":0.13,"lyze":8069.41,"jly":6004.22,"zt_status":0},
    {"name":"浙江龙盛","code":"600352","sector":"化工","price":11.92,"prev_close":11.59,"chg":2.85,"hsl":1.90,"lb":1.09,"pe":18.0,"safe":95,"shine":4,"inside":323214,"outside":294069,"mgsy":0.17,"lyze":70592.27,"jly":53713.22,"zt_status":0},
    {"name":"利安隆","code":"300596","sector":"化工","price":45.31,"prev_close":46.5,"chg":-2.56,"hsl":4.27,"lb":1.40,"pe":19.7,"safe":100,"shine":2,"inside":50165,"outside":45670,"mgsy":0.57,"lyze":14875.55,"jly":13200.16,"zt_status":0},
    {"name":"新和成","code":"002001","sector":"化工","price":28.70,"prev_close":28.69,"chg":0.03,"hsl":1.08,"lb":0.98,"pe":12.1,"safe":95,"shine":7,"inside":149446,"outside":177201,"mgsy":0.59,"lyze":213602.59,"jly":182722.77,"zt_status":0},

    # === 光通信 (8) ===
    {"name":"仕佳光子","code":"688313","sector":"光通信","price":147.63,"prev_close":154.56,"chg":-4.48,"hsl":5.11,"lb":1.14,"pe":143.6,"safe":93,"shine":4,"inside":123686,"outside":107108,"mgsy":0.26,"lyze":13264.82,"jly":11617.58,"zt_status":0},
    {"name":"腾景科技","code":"688195","sector":"光通信","price":159.51,"prev_close":162.7,"chg":-1.96,"hsl":3.94,"lb":1.01,"pe":500.3,"safe":93,"shine":1,"inside":34653,"outside":36745,"mgsy":0.08,"lyze":1820.46,"jly":1443.37,"zt_status":0},
    {"name":"天孚通信","code":"300394","sector":"光通信","price":271.12,"prev_close":271.5,"chg":-0.14,"hsl":5.94,"lb":1.42,"pe":150.2,"safe":74,"shine":10,"inside":328117,"outside":318114,"mgsy":0.45,"lyze":57438.38,"jly":49221.88,"zt_status":0},
    {"name":"中瓷电子","code":"003031","sector":"光通信","price":142.04,"prev_close":150.5,"chg":-5.62,"hsl":3.05,"lb":1.27,"pe":82.9,"safe":98,"shine":4,"inside":56137,"outside":47451,"mgsy":0.43,"lyze":23188.37,"jly":19330.34,"zt_status":0},
    {"name":"华工科技","code":"000988","sector":"光通信","price":158.07,"prev_close":159.0,"chg":-0.58,"hsl":7.34,"lb":1.42,"pe":62.2,"safe":92,"shine":9,"inside":396745,"outside":340869,"mgsy":0.64,"lyze":76713.26,"jly":63846.19,"zt_status":0},
    {"name":"锐科激光","code":"300747","sector":"光通信","price":40.77,"prev_close":41.57,"chg":-1.92,"hsl":6.03,"lb":1.38,"pe":136.8,"safe":100,"shine":2,"inside":162375,"outside":144753,"mgsy":0.07,"lyze":4866.51,"jly":4183.58,"zt_status":0},
    {"name":"炬光科技","code":"688167","sector":"光通信","price":282.1,"prev_close":289.0,"chg":-2.39,"hsl":6.50,"lb":1.36,"pe":-695.2,"safe":57,"shine":1,"inside":43593,"outside":40973,"mgsy":-0.10,"lyze":-1430.13,"jly":-1320.26,"zt_status":0},
    {"name":"英集芯","code":"688209","sector":"光通信","price":27.65,"prev_close":28.79,"chg":-3.96,"hsl":4.43,"lb":1.10,"pe":66.4,"safe":82,"shine":1,"inside":100558,"outside":91492,"mgsy":0.10,"lyze":4332.95,"jly":4513.01,"zt_status":0},
]

# ============ V53 盈泡风险 ============
SECTOR_BUBBLE_RISK = {
    "半导体": {"risk": "HIGH", "score": 55.6, "d6_penalty": 0.5, "global_penalty": -5},
    "光通信": {"risk": "MODERATE", "score": 42.3, "d6_penalty": 0.8, "global_penalty": 0},
    "新能源": {"risk": "MODERATE", "score": 38.5, "d6_penalty": 0.8, "global_penalty": 0},
    "化工": {"risk": "LOW", "score": 25.0, "d6_penalty": 1.0, "global_penalty": 0},
    "高端装备": {"risk": "LOW", "score": 20.0, "d6_penalty": 1.0, "global_penalty": 0},
    "医疗": {"risk": "LOW", "score": 15.0, "d6_penalty": 1.0, "global_penalty": 0},
}

# ============ D6 催化剂映射 ============
SECTOR_CATALYST = {
    "半导体": {"level": "STRONG", "score": 9, "theme": "存储涨价周期+半导体设备国产替代"},
    "光通信": {"level": "MEDIUM", "score": 7, "theme": "AI算力光互联+CPO共封装"},
    "新能源": {"level": "LONG-TERM", "score": 5, "theme": "锂电+光伏+储能长期成长"},
    "高端装备": {"level": "LONG-TERM", "score": 5, "theme": "工业自动化+机器人+数控机床"},
    "医疗": {"level": "LONG-TERM", "score": 5, "theme": "医疗器械国产替代+创新药"},
    "化工": {"level": "MEDIUM", "score": 6, "theme": "化工涨价周期+新材料"},
}


def score_d1(safe):
    """D1 获利筹码比 (SafeValue proxy)"""
    if safe >= 100: return 10
    if safe >= 95: return 9
    if safe >= 90: return 8
    if safe >= 85: return 7
    if safe >= 80: return 6
    if safe >= 75: return 5
    if safe >= 70: return 4
    if safe >= 60: return 3
    return 2

def score_d2(hsl):
    """D2 换手率低位"""
    if hsl < 0.5: return 10
    if hsl < 1.0: return 9
    if hsl < 2.0: return 8
    if hsl < 3.0: return 7
    if hsl < 5.0: return 6
    if hsl < 8.0: return 5
    if hsl < 10.0: return 4
    if hsl < 15.0: return 3
    return 2

def score_d3(inside, outside):
    """D3 主力资金 (外盘/内盘比)"""
    if inside == 0 or outside == 0: return 5  # 无数据时中性
    ratio = outside / inside if inside > 0 else 1.0
    if ratio > 1.15: return 10
    if ratio > 1.05: return 8
    if ratio > 1.0: return 7
    if ratio > 0.95: return 5
    if ratio > 0.85: return 3
    return 2

def score_d4(lb, chg):
    """D4 量价关系"""
    if chg > 0:
        if lb > 1.5: return 8  # 放量上涨
        if lb > 1.2: return 7
        if lb > 1.0: return 7
        if lb > 0.8: return 8  # 缩量上涨=主力控盘
        return 6
    else:
        if lb < 0.8: return 8  # 缩量下跌=洗盘
        if lb < 1.0: return 6
        if lb < 1.3: return 5  # 放量下跌
        return 4  # 暴跌放量

def score_d5(chg, zt_status):
    """D5 技术形态"""
    if zt_status == 101: return 6  # 涨停, T+1可能回调
    if chg > 5: return 8
    if chg > 2: return 7
    if chg > 0: return 6
    if chg > -3: return 7  # 小幅回调=买点
    if chg > -5: return 6
    if chg > -8: return 5
    if chg > -10: return 4  # 超跌
    return 5  # 暴跌反弹机会

def score_d6(sector, bubble_penalty=1.0):
    """D6 催化剂+涨停概率"""
    cat = SECTOR_CATALYST.get(sector, {"score": 5})
    return min(10, cat["score"] * bubble_penalty)

def score_d7(safe, shine):
    """D7 舆情热度"""
    if safe >= 95 and shine >= 5: return 10
    if safe >= 90 and shine >= 3: return 8
    if safe >= 85: return 7
    if safe >= 80: return 5
    if safe >= 70: return 4
    return 2

def score_d8(safe):
    """D8 筹码集中"""
    if safe >= 95: return 10
    if safe >= 85: return 7
    if safe >= 75: return 5
    return 3


def run_distillation():
    results = []
    for s in STOCKS:
        sector = s["sector"]
        bubble = SECTOR_BUBBLE_RISK.get(sector, {"risk":"LOW","score":0,"d6_penalty":1.0,"global_penalty":0})

        # 8维评分
        d1 = score_d1(s["safe"])
        d2 = score_d2(s["hsl"])
        d3 = score_d3(s["inside"], s["outside"])
        d4 = score_d4(s["lb"], s["chg"])
        d5 = score_d5(s["chg"], s["zt_status"])
        d6 = score_d6(sector, bubble["d6_penalty"])
        d7 = score_d7(s["safe"], s["shine"])
        d8 = score_d8(s["safe"])

        dims = {"D1":d1, "D2":d2, "D3":d3, "D4":d4, "D5":d5, "D6":d6, "D7":d7, "D8":d8}

        # 加权总分
        total = sum(dims[k] * EVOLUTION_WEIGHTS[k] for k in dims) * 10  # 0-100

        # V53全局惩罚
        total += bubble["global_penalty"]
        total = max(0, min(100, total))

        # 活跃维度计数 (>=6分为活跃)
        active = sum(1 for v in dims.values() if v >= 6)

        # 信号判定
        if total >= 70 and active >= 3:
            signal = "BUY"
        elif total >= 55 and active >= 2:
            signal = "WATCH"
        else:
            signal = "PASS"

        # T+1置信度
        confidence = 50 + active * 5
        if signal == "BUY": confidence += 10
        if s["safe"] >= 95: confidence += 5
        if s["chg"] < -5 and s["lb"] < 1.0: confidence += 7  # 缩量超跌
        confidence = min(95, confidence)

        results.append({
            "name": s["name"], "code": s["code"], "sector": sector,
            "price": s["price"], "chg": s["chg"], "hsl": s["hsl"], "lb": s["lb"],
            "pe": s["pe"], "safe": s["safe"], "shine": s["shine"],
            "mgsy": s["mgsy"], "zt_status": s["zt_status"],
            "bubble_risk": bubble["risk"], "bubble_score": bubble["score"],
            "dims": dims, "total_score": round(total, 1),
            "active_dims": active, "signal": signal,
            "confidence": confidence,
            "catalyst": SECTOR_CATALYST.get(sector, {}).get("theme", ""),
        })

    # 按总分排序
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results


def generate_html(results):
    buy_list = [r for r in results if r["signal"] == "BUY"]
    watch_list = [r for r in results if r["signal"] == "WATCH"]
    pass_list = [r for r in results if r["signal"] == "PASS"]

    # 涨跌幅颜色
    def chg_color(chg):
        if chg > 0: return "#e74c3c"  # 红(涨)
        if chg < 0: return "#27ae60"  # 绿(跌)
        return "#95a5a6"

    def signal_badge(sig):
        colors = {"BUY": "#e74c3c", "WATCH": "#f39c12", "PASS": "#7f8c8d"}
        return f'<span style="background:{colors[sig]};color:white;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:bold">{sig}</span>'

    def risk_badge(risk):
        colors = {"LOW": "#27ae60", "MODERATE": "#f39c12", "HIGH": "#e74c3c", "EXTREME": "#8e44ad"}
        return f'<span style="background:{colors.get(risk,"#95a5a6")};color:white;padding:1px 6px;border-radius:3px;font-size:10px">{risk}</span>'

    def dim_bar(val):
        color = "#e74c3c" if val >= 8 else "#f39c12" if val >= 6 else "#95a5a6"
        return f'<span style="display:inline-block;width:24px;text-align:center;color:{color};font-weight:bold">{val}</span>'

    rows = ""
    for i, r in enumerate(results):
        zt_tag = ' <span style="color:#e74c3c;font-size:10px">涨停</span>' if r["zt_status"] == 101 else ''
        rows += f"""
        <tr style="border-bottom:1px solid #2a2a2a">
            <td style="padding:6px 8px;color:#666">{i+1}</td>
            <td style="padding:6px 8px"><span style="color:#e8e8e8;font-weight:600">{r['name']}</span>{zt_tag}<br><span style="color:#666;font-size:11px">{r['code']}</span></td>
            <td style="padding:6px 8px"><span style="color:#888;font-size:11px">{r['sector']}</span></td>
            <td style="padding:6px 8px;color:#e8e8e8">{r['price']:.2f}</td>
            <td style="padding:6px 8px;color:{chg_color(r['chg'])};font-weight:600">{r['chg']:+.2f}%</td>
            <td style="padding:6px 8px;color:#aaa">{r['hsl']:.1f}%</td>
            <td style="padding:6px 8px;color:#aaa">{r['lb']:.2f}</td>
            <td style="padding:6px 8px;color:#aaa">{r['pe']:.1f}</td>
            <td style="padding:6px 8px;color:#aaa">{r['safe']}</td>
            <td style="padding:6px 8px">{dim_bar(r['dims']['D1'])}{dim_bar(r['dims']['D2'])}{dim_bar(r['dims']['D3'])}{dim_bar(r['dims']['D4'])}{dim_bar(r['dims']['D5'])}{dim_bar(r['dims']['D6'])}{dim_bar(r['dims']['D7'])}{dim_bar(r['dims']['D8'])}</td>
            <td style="padding:6px 8px"><span style="color:{'#e74c3c' if r['total_score']>=70 else '#f39c12' if r['total_score']>=55 else '#666'};font-weight:700;font-size:14px">{r['total_score']:.1f}</span></td>
            <td style="padding:6px 8px;color:#aaa">{r['active_dims']}/8</td>
            <td style="padding:6px 8px">{r['confidence']}%</td>
            <td style="padding:6px 8px">{risk_badge(r['bubble_risk'])}</td>
            <td style="padding:6px 8px">{signal_badge(r['signal'])}</td>
        </tr>"""

    # 赛道统计
    sector_stats = {}
    for r in results:
        s = r["sector"]
        if s not in sector_stats:
            sector_stats[s] = {"count":0, "buy":0, "watch":0, "pass":0, "avg":0, "max":0}
        sector_stats[s]["count"] += 1
        sector_stats[s][r["signal"].lower()] += 1
        sector_stats[s]["avg"] += r["total_score"]
        sector_stats[s]["max"] = max(sector_stats[s]["max"], r["total_score"])
    for s in sector_stats:
        sector_stats[s]["avg"] = round(sector_stats[s]["avg"] / sector_stats[s]["count"], 1)

    sector_rows = ""
    for s in ["高端装备","半导体","新能源","医疗","化工","光通信"]:
        st = sector_stats[s]
        sector_rows += f"""
        <tr style="border-bottom:1px solid #2a2a2a">
            <td style="padding:6px 8px;color:#e8e8e8">{s}</td>
            <td style="padding:6px 8px;color:#aaa">{st['count']}</td>
            <td style="padding:6px 8px;color:#e74c3c;font-weight:600">{st['buy']}</td>
            <td style="padding:6px 8px;color:#f39c12">{st['watch']}</td>
            <td style="padding:6px 8px;color:#666">{st['pass']}</td>
            <td style="padding:6px 8px;color:#e8e8e8;font-weight:600">{st['avg']}</td>
            <td style="padding:6px 8px;color:#e74c3c">{st['max']:.1f}</td>
        </tr>"""

    # Top 10 BUY/WATCH 候选详情
    top_candidates = [r for r in results if r["signal"] in ("BUY", "WATCH")][:10]
    top_cards = ""
    for r in top_candidates:
        card_color = "#e74c3c" if r["signal"] == "BUY" else "#f39c12"
        top_cards += f"""
        <div style="background:#1a1a1a;border:1px solid #333;border-left:3px solid {card_color};border-radius:6px;padding:12px;margin-bottom:8px">
            <div style="display:flex;justify-content:space-between;align-items:center">
                <div>
                    <span style="color:#e8e8e8;font-size:15px;font-weight:700">{r['name']}</span>
                    <span style="color:#666;font-size:12px;margin-left:6px">{r['code']}</span>
                    <span style="color:#888;font-size:11px;margin-left:8px">{r['sector']}</span>
                </div>
                <div style="text-align:right">
                    <span style="color:{card_color};font-size:20px;font-weight:700">{r['total_score']:.1f}</span>
                    {signal_badge(r['signal'])}
                </div>
            </div>
            <div style="display:flex;gap:12px;margin-top:6px;font-size:11px;color:#888">
                <span>价格: <b style="color:#e8e8e8">{r['price']:.2f}</b></span>
                <span>涨跌: <b style="color:{chg_color(r['chg'])}">{r['chg']:+.2f}%</b></span>
                <span>换手: <b style="color:#e8e8e8">{r['hsl']:.1f}%</b></span>
                <span>量比: <b style="color:#e8e8e8">{r['lb']:.2f}</b></span>
                <span>SafeValue: <b style="color:#e8e8e8">{r['safe']}</b></span>
                <span>活跃: <b style="color:#e8e8e8">{r['active_dims']}/8</b></span>
                <span>置信度: <b style="color:{card_color}">{r['confidence']}%</b></span>
                <span>盈泡: {risk_badge(r['bubble_risk'])}</span>
            </div>
            <div style="margin-top:4px;font-size:11px;color:#666">催化: {r['catalyst']}</div>
            <div style="margin-top:4px">
                D1:{dim_bar(r['dims']['D1'])} D2:{dim_bar(r['dims']['D2'])} D3:{dim_bar(r['dims']['D3'])} D4:{dim_bar(r['dims']['D4'])} D5:{dim_bar(r['dims']['D5'])} D6:{dim_bar(r['dims']['D6'])} D7:{dim_bar(r['dims']['D7'])} D8:{dim_bar(r['dims']['D8'])}
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.5.52+53 60只隐形冠军股票追踪看板</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:#0d0d0d; color:#e8e8e8; font-family:'Segoe UI','Microsoft YaHei',sans-serif; padding:16px; }}
.header {{ text-align:center; padding:20px 0; border-bottom:1px solid #333; margin-bottom:16px; }}
.header h1 {{ font-size:22px; color:#e8e8e8; }}
.header .sub {{ color:#666; font-size:12px; margin-top:4px; }}
.stats {{ display:flex; gap:12px; margin-bottom:16px; }}
.stat-card {{ flex:1; background:#1a1a1a; border:1px solid #333; border-radius:8px; padding:12px; text-align:center; }}
.stat-card .num {{ font-size:28px; font-weight:700; }}
.stat-card .label {{ color:#666; font-size:11px; margin-top:2px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ background:#1a1a1a; color:#888; padding:8px; text-align:center; font-weight:600; border-bottom:2px solid #333; white-space:nowrap; }}
td {{ text-align:center; }}
.section-title {{ color:#e8e8e8; font-size:15px; font-weight:600; margin:20px 0 8px; padding-left:8px; border-left:3px solid #e74c3c; }}
</style>
</head>
<body>
<div class="header">
    <h1>V13.5.52+53 60只隐形冠军股票追踪看板</h1>
    <div class="sub">数据: TDX MCP tdx_quotes (HQDate=20260710) | 8维TDX蒸馏+V52进化权重+V53盈泡风险 | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div>

<div class="stats">
    <div class="stat-card"><div class="num" style="color:#e74c3c">{len(buy_list)}</div><div class="label">BUY 候选</div></div>
    <div class="stat-card"><div class="num" style="color:#f39c12">{len(watch_list)}</div><div class="label">WATCH 观察</div></div>
    <div class="stat-card"><div class="num" style="color:#7f8c8d">{len(pass_list)}</div><div class="label">PASS 回避</div></div>
    <div class="stat-card"><div class="num" style="color:#e8e8e8">{len(results)}</div><div class="label">总计</div></div>
    <div class="stat-card"><div class="num" style="color:#e8e8e8">{sum(1 for r in results if r['chg']>0)}</div><div class="label">上涨</div></div>
    <div class="stat-card"><div class="num" style="color:#27ae60">{sum(1 for r in results if r['chg']<0)}</div><div class="label">下跌</div></div>
</div>

<div class="section-title">赛道统计</div>
<table>
<tr><th>赛道</th><th>数量</th><th>BUY</th><th>WATCH</th><th>PASS</th><th>平均分</th><th>最高分</th></tr>
{sector_rows}
</table>

<div class="section-title">Top 10 T+1涨停潜力候选</div>
{top_cards}

<div class="section-title">全量60只股票8维蒸馏评分</div>
<table>
<tr>
    <th>#</th><th>名称/代码</th><th>赛道</th><th>现价</th><th>涨跌%</th><th>换手%</th><th>量比</th><th>PE</th><th>Safe</th>
    <th>D1-D2-D3-D4-D5-D6-D7-D8</th><th>蒸馏分</th><th>活跃</th><th>置信度</th><th>盈泡</th><th>信号</th>
</tr>
{rows}
</table>

<div style="margin-top:20px;padding:12px;background:#1a1a1a;border-radius:8px;border:1px solid #333">
    <div style="color:#888;font-size:12px;line-height:1.8">
        <b style="color:#e8e8e8">评分说明:</b><br>
        D1获利筹码(17.7%) | D2换手率(10.5%) | D3主力资金(17.6%) | D4量价(15.4%) | D5技术(11.8%) | D6催化(11.9%) | D7舆情(10.0%) | D8筹码(5.1%)<br>
        <b style="color:#e8e8e8">信号判定:</b> BUY(>=70分+活跃>=3维) | WATCH(>=55分+活跃>=2维) | PASS(其他)<br>
        <b style="color:#e8e8e8">V53盈泡风险:</b> 半导体HIGH(D6惩罚0.5x+全局-5) | 光通信/新能源MODERATE(D6惩罚0.8x) | 化工/高端装备/医疗LOW<br>
        <b style="color:#e8e8e8">T+1置信度:</b> 基准50% + 活跃维x5% + BUY+10% + Safe>=95+5% + 缩量超跌+7% (max95%)<br>
        <b style="color:#e8e8e8">setcode修复:</b> 688xxx科创板setcode从"0"(深市)修正为"1"(沪市) → 19只股票数据全部恢复
    </div>
</div>
</body>
</html>"""
    return html


if __name__ == "__main__":
    results = run_distillation()

    # 保存JSON
    os.makedirs("data/evolution_v13552", exist_ok=True)
    with open("data/champion_60_scores.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 保存HTML
    html = generate_html(results)
    os.makedirs("outputs", exist_ok=True)
    with open("outputs/V13_5_52_Champion60_Tracker.html", "w", encoding="utf-8") as f:
        f.write(html)

    # 打印摘要
    buy = [r for r in results if r["signal"] == "BUY"]
    watch = [r for r in results if r["signal"] == "WATCH"]
    print(f"\n{'='*60}")
    print(f"60只隐形冠军股票 8维蒸馏评分完成")
    print(f"{'='*60}")
    print(f"BUY:   {len(buy)}只")
    for r in buy:
        print(f"  {r['name']:6s} ({r['code']}) {r['sector']:4s} {r['total_score']:5.1f}分 活跃{r['active_dims']}/8 置信{r['confidence']}%")
    print(f"\nWATCH: {len(watch)}只")
    for r in watch[:10]:
        print(f"  {r['name']:6s} ({r['code']}) {r['sector']:4s} {r['total_score']:5.1f}分 活跃{r['active_dims']}/8 置信{r['confidence']}%")
    if len(watch) > 10:
        print(f"  ... 及其余{len(watch)-10}只")
    print(f"\nPASS:  {len(results)-len(buy)-len(watch)}只")
    print(f"\n输出: outputs/V13_5_52_Champion60_Tracker.html")
    print(f"数据: data/champion_60_scores.json")
