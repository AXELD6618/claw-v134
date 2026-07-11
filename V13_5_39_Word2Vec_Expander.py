#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.39 word2vec语料扩展 — 329词→1000+词向量
================================================
将TDX实时新闻(31条)+训练数据(97条)追加到word2vec语料
重新训练gensim Word2Vec模型

Author: 毕方灵犀貔貅助手 V13.5.39
Date: 2026-07-11
"""

import sys
import os
from pathlib import Path

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
sys.path.insert(0, str(BASE))

DATA_DIR = BASE / "data"
W2V_DIR = DATA_DIR / "word2vec"
MODEL_PATH = W2V_DIR / "tdx_news_w2v_v39.model"
CORPUS_PATH = W2V_DIR / "corpus_v39.txt"
REPORT_PATH = BASE / "outputs" / "V13_5_39_Word2Vec_Expanded_Report.html"

# 导入V13.5.38原有语料
from V13_5_38_Word2Vec_Trainer import CORPUS_TEXTS as V38_CORPUS

# 新增TDX实时新闻语料 (7/1-7/11, 来自TDX wenda_news_query)
NEW_TDX_CORPUS = [
    # AI算力 (7/8-7/11)
    "MLCC涨价潮华强北结构性紧缺AI服务器GB300用量3万颗村田三星砍产能风华高科三环集团国产突破介质层0.6微米堆叠1000层",
    "工业富联业绩预增上半年归母净利润234亿至244亿同比增长93%至101%AI服务器营收增长超230%云服务商资本开支",
    "利润暴增千亿算力巨头涨停浪潮信息归母净利润26亿至31亿同比增226%至288%合同负债195亿预收货款AI服务器供不应求",
    "算力企业中报业绩喜人科创创业人工智能ETF连续4日资金净流入浪潮信息复旦微电业绩大增算力产业链强业绩弹性",
    "粤港湾智算AI算力云服务订单逾150亿对应35000PFLOPS已交付20亿净利润不低于2.40亿长期合同五年期",
    # 半导体 (7/8-7/9)
    "半导体板块领涨中证半导体指数涨9.13%ETF涨停WSTS预测2026年全球半导体增长90%达1.51万亿2027年1.9万亿",
    "半导体板块爆发上海合晶20%涨停有研硅涨超18%沐曦股份创历史新高摩尔线程寒武纪跟涨AI需求核心驱动",
    "半导体设备国产算力走强中科飞测精测电子华峰测控大涨中芯国际华虹宏力创新高华为韬定律逻辑折叠EDA",
    "华为算力巨兽首秀Atlas950超节点真机亮相WAIC港股半导体芯片股反弹圣邦股份中芯国际长光辰芯涨",
    # 机器人 (7/1-7/9)
    "机器人ETF易方达涨2.18%资金净流入超4亿1至5月具身智能产业企业销售收入同比增长22.4%机器人本体制造增长30%",
    "飞龙股份液冷人形机器人资产注入预期液冷泵产品覆盖8W至40kW机器人关节液冷散热新能源热管理",
    "五一视界配售546万股净筹3.95亿港元物理直觉世界模型51WORLD MODEL具身智能商业化落地Sim2Real",
    "2026世界机器人大会8月19日召开人形机器人出货量占全球90%1至5月机器人规上企业营收900亿同比增长26.9%",
    # 商业航天 (7/1-7/10)
    "商业航天概念爆发长十乙火箭成功实现可控回收长征十号乙海上回收平台中国全球第二掌握大运力可回收火箭技术",
    "国内可回收火箭进入技术验证期朱雀三号静态点火星河动力苍穹50发动机163次热试车首飞在即",
    "蓝箭航天冲刺IPO科创板营收暴增11倍朱雀二号量产商用朱雀三号可重复使用募资75亿GW星座千帆星座",
    "7月发射窗口将至商业航天板块走强航天电子中国卫星航发动力涨超3%航空航天ETF连续吸金4.67亿",
    "商业航天IPO提速蓝箭航天中科宇航微纳星空天兵科技星际荣耀超捷股份广联航空智明达机构调研融资余额增长",
    # MLCC涨价 (7/11)
    "MLCC电容电阻电感被动电子元件涨价华强北渠道躁动AI服务器高容缺货一天一价村田1206单日涨5%",
    "三环集团港交所上市高端MLCC介电层1微米堆叠1000层以上AI数据中心汽车电子车规AEC-Q200认证",
    "微容科技IPO深交所受理1206尺寸220μF批量供应AI服务器叠层超1200层填补国内空白车规级1210",
    "国瓷材料高端粉体水热法纳米级高纯钛酸钡产能5000吨AI服务器车规级MLCC粉体国产替代打破日企垄断",
    # 政策/行业
    "世界半导体贸易统计组织预测2026年全球半导体市场增长90%达1.51万亿2027年1.9万亿AI应用高性能半导体需求激增",
    "华为韬定律V2发布逻辑折叠多层级电子系统EDA先进封装晶圆测试设备增量机遇Chiplet 2.5D 3D混合键合TSV",
    "创新药BD交易额破千亿美元2026年1至6月中国创新药商业发展交易总金额1036亿同比增长48%",
    # 资金/机构
    "400亿资金净申购芯片ETF机构一致预测32股净利润增速超20%高盛上调台积电目标价AI算力需求2027年动能强劲",
    "机器人ETF资金净流入超4亿元国证机器人产业指数聚焦核心零部件人形机器人权重80%特斯拉Optimus量产",
    # 涨价
    "圣泉PPO聚苯醚系列产品涨价15%至20%科威尔电源价格上调5%至10%建滔涨价函存储芯片DRAM NAND涨价周期",
    "稀土永磁材料价格上调新能源车风电需求旺盛供需缺口扩大电解铝价格创年内新高云南限电减产供给收缩",
    # 海外
    "三星电子业绩暴增1810%营业利润创新高存储芯片需求旺盛股价重挫韩国熔断机构资金从涨幅过大科技股撤出",
    "亚马逊AWS上调Q3 ASIC服务器出货量预测20%至30%英伟达否认延期传闻Rubin路线图保持不变博通苹果合作至2031",
    "Meta拟出租算力引发过剩担忧算力资产商业化基础设施AI基建利润导向并非产能过剩腾讯混元Hy3正式发布",
]

# 训练数据中的文本也加入语料
from V13_5_39_TrainingData_Builder import TDX_NEWS_CORPUS as TRAIN_CORPUS
from V13_5_39_TrainingData_Builder import SYNTHETIC_DATA


def expand_and_retrain():
    """扩展语料并重新训练word2vec"""
    print("=" * 60)
    print("V13.5.39 word2vec语料扩展训练")
    print("=" * 60)

    # 合并所有语料
    all_texts = list(V38_CORPUS) + list(NEW_TDX_CORPUS) + list(TRAIN_CORPUS)
    # 添加合成数据的文本
    for text, _ in SYNTHETIC_DATA:
        all_texts.append(text)

    # 去重
    seen = set()
    unique_texts = []
    for t in all_texts:
        if t not in seen:
            seen.add(t)
            unique_texts.append(t)

    print(f"\n语料合并:")
    print(f"  V13.5.38原有: {len(V38_CORPUS)}条")
    print(f"  新增TDX新闻: {len(NEW_TDX_CORPUS)}条")
    print(f"  训练数据: {len(TRAIN_CORPUS) + len(SYNTHETIC_DATA)}条")
    print(f"  去重后总计: {len(unique_texts)}条")

    try:
        import jieba
        from gensim.models import Word2Vec

        # 分词
        print("\n分词中...")
        sentences = []
        for text in unique_texts:
            words = list(jieba.cut(text))
            words = [w for w in words if len(w) >= 2 or w.isdigit()]
            if words:
                sentences.append(words)

        # 保存语料
        with open(CORPUS_PATH, "w", encoding="utf-8") as f:
            for sent in sentences:
                f.write(" ".join(sent) + "\n")
        print(f"语料保存: {CORPUS_PATH} ({len(sentences)}句)")

        # 训练Word2Vec
        print("训练Word2Vec模型 (Skip-gram, dim=100)...")
        model = Word2Vec(
            sentences=sentences,
            vector_size=100,
            window=5,
            min_count=1,
            workers=1,
            sg=1,  # Skip-gram
            epochs=20,
        )

        # 保存模型
        model.save(str(MODEL_PATH))
        vocab_size = len(model.wv)
        print(f"模型保存: {MODEL_PATH}")
        print(f"词表大小: {vocab_size} (vs V13.5.38: 329 = {vocab_size/329:.1f}x)")

        # 测试语义发现
        print("\n语义发现测试:")
        test_words = ["算力", "业绩", "半导体", "涨停", "突破", "量产", "涨价", "收购"]
        discoveries = {}
        for word in test_words:
            try:
                similar = model.wv.most_similar(word, topn=5)
                discoveries[word] = [(w, round(s, 3)) for w, s in similar]
                print(f"  {word} → {', '.join(f'{w}({s:.2f})' for w, s in similar)}")
            except KeyError:
                print(f"  {word} → 不在词表中")

        # 生成报告
        _generate_report(unique_texts, vocab_size, discoveries)

        return vocab_size, discoveries

    except Exception as e:
        print(f"训练失败: {e}")
        import traceback
        traceback.print_exc()
        return 0, {}


def _generate_report(texts, vocab_size, discoveries):
    """生成HTML报告"""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.5.39 word2vec语料扩展报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #0d1117; color: #c9d1d9; }}
h1 {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; }}
h2 {{ color: #79c0ff; margin-top: 30px; }}
.metric {{ display: inline-block; background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px 25px; margin: 5px; }}
.metric-value {{ font-size: 28px; font-weight: bold; color: #3fb950; }}
.metric-label {{ color: #8b949e; font-size: 12px; }}
table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
th, td {{ border: 1px solid #30363d; padding: 8px 12px; text-align: left; }}
th {{ background: #161b22; color: #58a6ff; }}
tr:nth-child(even) {{ background: #161b22; }}
.word {{ color: #f0883e; font-weight: bold; }}
.similar {{ color: #3fb950; }}
.score {{ color: #d2a8ff; }}
</style>
</head>
<body>
<h1>V13.5.39 word2vec语料扩展报告</h1>

<div>
<div class="metric"><div class="metric-value">{len(texts)}</div><div class="metric-label">语料条数</div></div>
<div class="metric"><div class="metric-value">{vocab_size}</div><div class="metric-label">词表大小</div></div>
<div class="metric"><div class="metric-value">{vocab_size/329:.1f}x</div><div class="metric-label">vs V13.5.38</div></div>
<div class="metric"><div class="metric-value">100</div><div class="metric-label">向量维度</div></div>
</div>

<h2>语义发现结果</h2>
<table>
<tr><th>目标词</th><th>语义相近词 (Top 5)</th></tr>
"""
    for word, similar in discoveries.items():
        similar_html = " ".join(f'<span class="similar">{w}</span><span class="score">({s})</span> ' for w, s in similar)
        html += f"<tr><td class='word'>{word}</td><td>{similar_html}</td></tr>\n"

    html += f"""
</table>

<h2>语料来源</h2>
<table>
<tr><th>来源</th><th>条数</th><th>内容</th></tr>
<tr><td>V13.5.38原有</td><td>{len(V38_CORPUS)}</td><td>AI算力/半导体/中报预增/金融语境</td></tr>
<tr><td>新增TDX新闻</td><td>{len(NEW_TDX_CORPUS)}</td><td>7/1-7/11 TDX实时新闻(AI算力/半导体/机器人/商业航天/MLCC/政策)</td></tr>
<tr><td>训练数据</td><td>{len(TRAIN_CORPUS) + len(SYNTHETIC_DATA)}</td><td>TDX语料+合成训练数据</td></tr>
<tr><td><b>去重后总计</b></td><td><b>{len(texts)}</b></td><td></td></tr>
</table>

<h2>模型参数</h2>
<ul>
<li>算法: Skip-gram</li>
<li>向量维度: 100</li>
<li>窗口大小: 5</li>
<li>最小词频: 1</li>
<li>训练轮数: 20 epochs</li>
<li>模型保存: data/word2vec/tdx_news_w2v_v39.model</li>
</ul>

</body>
</html>"""

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n报告保存: {REPORT_PATH}")


if __name__ == "__main__":
    expand_and_retrain()
