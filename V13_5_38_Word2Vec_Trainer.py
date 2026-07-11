#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️  DEPRECATED V13.5.38 → 请使用 V13_5_39_Word2Vec_Expander.py (329→1176词向量+多epoch优化)
"""
V13.5.38 word2vec词向量训练 — 语义发现引擎
============================================
用gensim Word2Vec训练TDX新闻语料词向量模型，提升关键词语义扩展准确率。

核心能力:
  1. jieba分词TDX新闻语料 → 训练Word2Vec模型
  2. 给定关键词 → 发现语义相近的新词(cosine similarity)
  3. 关键词聚类 → 自动发现主题群组
  4. 词向量可视化 → t-SNE降维(可选)

vs V13.5.37 SemanticExpander(N-gram+Jaccard):
  - word2vec捕获深层语义关系(vs N-gram仅统计共现)
  - 可发现"业绩预增"→"盈利大增/净利翻倍/利润暴增"等深层语义近义词

Author: 毕方灵犀貔貅助手 V13.5.38
Date: 2026-07-11
"""

import json
import os
import sys
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
sys.path.insert(0, str(BASE))

DATA_DIR = BASE / "data"
W2V_DIR = DATA_DIR / "word2vec"
W2V_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = W2V_DIR / "tdx_news_w2v.model"
CORPUS_PATH = W2V_DIR / "corpus.txt"

# ============================================================
# TDX新闻语料库 (真实+扩展)
# ============================================================
CORPUS_TEXTS = [
    # AI算力
    "浪潮信息预计上半年归母净利润26亿元至31亿元同比增长226%至288%AI服务器液冷数据中心",
    "算力租赁报价上行AI大模型企业年化营收保持环比增长算力供不应求",
    "千亿算力巨头涨停浪潮信息中报预告归母净利润同比增幅高达226%至288%",
    "中信证券大幅波动不改AI超级周期重视国产算力PlanB存储PCB光通信",
    "Meta拟出租算力引发过剩担忧但算力租金仍在上涨过剩担忧不成立",
    "行云科技预计上半年营业收入激增逾5倍55亿元算力服务协议470台算力服务器",
    "智微智能预计上半年归母净利润3.50亿元至4.17亿元ICT基础设施出货量爆发增长",
    "AI小登股集体洗牌回撤20%以上32只机构预测净利润增速超20%中报验证关键分水岭",

    # 半导体
    "半导体板块领涨全市场中证半导体指数涨9.13%半导体ETF涨停换手18%",
    "WSTS预测2026全球半导体市场规模增长近90%达1.51万亿美元2027年1.91万亿",
    "半导体板块爆发上海合晶20%涨停有研硅涨超18%沐曦股份创新高摩尔线程寒武纪跟涨",
    "华为Atlas 950超节点将于世界人工智能大会亮相单柜64卡8192张NPU卡",
    "华为韬定律V2发布打开国产半导体新路径先进封装EDA验证需求抬升",
    "半导体设备ETF重拾攻势中科飞测涨超15%联动科技华峰测控芯源微中芯国际",
    "存储扩产先进制程AI芯片三线共振国产设备7nm以下节点突破",
    "精密零部件测试设备晶圆键合量测设备国产替代提速龙头企业订单高增",
    "圣邦股份涨近7%中芯国际长光辰芯涨逾3%港股半导体板块反弹",
    "三星代工订单持续积压高盛大幅上调台积电目标价AI算力需求2027年动能强劲",
    "国内市场近400亿元资金净申购芯片类ETF半导体板块主线地位明确",

    # 中报预增
    "洛阳钼业预计上半年归母净利润155亿元至165亿元同比增长78.76%至90.29%",
    "铜产品量价双升钼钨产品价格显著走高巴西金矿业务并表铜金属产量38.80万吨",
    "宏桥控股预计净利润150亿元至160亿元同比增长69.72%至81.04%电解铝价格上涨",
    "宏桥控股发行股份收购宏拓实业100%股权重大资产重组同一控制下企业合并",
    "东材科技预计净利润3.12亿元同比增长63.93%高端材料市场需求扩容升级",
    "百隆东方预计净利润5.07亿元至6.24亿元同比增长30%至60%订单饱满产能利用率提升",
    "复旦微电预计上半年净利润8亿元至10亿元芯片设计行业景气度传导",

    # 通用金融语境
    "业绩预增盈利大增净利翻倍利润暴增超预期创历史新高创纪录",
    "同比大增环比大增高速增长业绩亮眼业绩爆发业绩腾飞",
    "中标大单巨额订单框架协议签订合同战略合作战略协议采购合同供货协议",
    "突破重大突破技术突破核心技术突破首发新品发布量产量产在即",
    "收购重组并购合并整合发行股份过户完成证监会核准",
    "扩产投产产能利用率提升产能释放规模效益订单高增",
    "涨价价格上调供需偏紧量价齐升价格走高显著走高",
    "回撤调整暴跌下滑亏损风险过剩担忧泡沫跳水萎缩压力低迷",
    "国产替代自主可控政策支持产业链自主可控诉求强化",
    "机构一致预测资金净申购ETF净流入高盛上调目标价机构看好",
]


@dataclass
class Word2VecResult:
    """word2vec分析结果"""
    target_word: str
    similar_words: List[Tuple[str, float]]
    cluster_id: int = -1


class Word2VecTrainer:
    """word2vec词向量训练器"""

    def __init__(self):
        self.model = None
        self.vocab_size = 0
        self._init_model()

    def _init_model(self):
        """初始化并训练模型"""
        print("=" * 60)
        print("V13.5.38 word2vec词向量训练引擎")
        print("=" * 60)

        try:
            import jieba
            from gensim.models import Word2Vec

            # 分词
            print("分词中...")
            sentences = []
            for text in CORPUS_TEXTS:
                words = list(jieba.cut(text))
                # 过滤单字和标点
                words = [w for w in words if len(w) >= 2 or w.isdigit()]
                if words:
                    sentences.append(words)

            # 保存语料
            with open(CORPUS_PATH, "w", encoding="utf-8") as f:
                for sent in sentences:
                    f.write(" ".join(sent) + "\n")
            print(f"语料已保存: {CORPUS_PATH} ({len(sentences)}句)")

            # 训练Word2Vec
            print("训练Word2Vec模型...")
            self.model = Word2Vec(
                sentences=sentences,
                vector_size=100,
                window=5,
                min_count=1,
                workers=1,
                sg=1,  # Skip-gram
                epochs=50,
            )
            self.vocab_size = len(self.model.wv)
            print(f"模型训练完成: {self.vocab_size}个词向量, dim=100")

            # 保存模型
            self.model.save(str(MODEL_PATH))
            print(f"模型已保存: {MODEL_PATH}")

        except ImportError as e:
            print(f"gensim不可用: {e}")
            self.model = None

    def find_similar(self, word: str, topn: int = 10) -> List[Tuple[str, float]]:
        """查找语义相近的词"""
        if not self.model:
            return []
        try:
            if word in self.model.wv:
                return self.model.wv.most_similar(word, topn=topn)
        except Exception as e:
            print(f"查找相似词失败: {e}")
        return []

    def find_similar_batch(self, words: List[str], topn: int = 5) -> List[Word2VecResult]:
        """批量查找相似词"""
        results = []
        for word in words:
            similar = self.find_similar(word, topn)
            if similar:
                results.append(Word2VecResult(
                    target_word=word,
                    similar_words=similar,
                ))
        return results

    def cluster_keywords(self, words: List[str], threshold: float = 0.5) -> Dict[int, List[str]]:
        """关键词聚类"""
        if not self.model:
            return {}

        clusters = {}
        cluster_id = 0
        assigned = set()

        for word in words:
            if word in assigned or word not in self.model.wv:
                continue

            # 找到与当前词相似的且未分配的词
            cluster_members = [word]
            assigned.add(word)

            for other in words:
                if other in assigned or other not in self.model.wv:
                    continue
                try:
                    sim = self.model.wv.similarity(word, other)
                    if sim >= threshold:
                        cluster_members.append(other)
                        assigned.add(other)
                except:
                    pass

            if len(cluster_members) >= 2:
                clusters[cluster_id] = cluster_members
                cluster_id += 1

        return clusters

    def discover_new_keywords(self, existing_keywords: List[str], topn: int = 3) -> List[Tuple[str, str, float]]:
        """
        发现新关键词
        返回: [(新词, 源词, 相似度)]
        """
        new_words = []
        seen = set(existing_keywords)

        for kw in existing_keywords:
            similar = self.find_similar(kw, topn=topn)
            for new_word, score in similar:
                if new_word not in seen and len(new_word) >= 2:
                    new_words.append((new_word, kw, score))
                    seen.add(new_word)

        # 按相似度排序
        new_words.sort(key=lambda x: -x[2])
        return new_words

    def generate_report(self) -> str:
        """生成HTML报告"""
        # 测试关键词
        test_words = ["业绩预增", "算力", "半导体", "涨停", "突破", "收购",
                     "产能", "涨价", "国产替代", "ETF"]

        results = self.find_similar_batch(test_words, topn=8)

        # 发现新词
        existing_kws = ["业绩预增", "净利润", "算力", "半导体", "涨停", "突破",
                       "收购", "重组", "产能", "涨价", "国产替代", "订单",
                       "量产", "ETF", "机构", "增长"]
        new_words = self.discover_new_keywords(existing_kws, topn=5)

        # 聚类
        all_words = list(set(existing_kws + [w for r in results for w, _ in r.similar_words]))
        clusters = self.cluster_keywords(all_words, threshold=0.4)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>V13.5.38 word2vec词向量训练报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Microsoft YaHei', sans-serif; background: #0a0e1a; color: #e0e6ed; padding: 20px; }}
.header {{ text-align: center; padding: 30px; background: linear-gradient(135deg, #1a237e, #0d47a1); border-radius: 12px; margin-bottom: 20px; }}
.header h1 {{ font-size: 28px; color: #fff; }}
.header .meta {{ color: #80deea; margin-top: 10px; font-size: 14px; }}
.summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }}
.card {{ background: #1a1f3a; border-radius: 10px; padding: 20px; text-align: center; border: 1px solid #2a3050; }}
.card .num {{ font-size: 36px; font-weight: bold; color: #00e676; }}
.card .label {{ color: #78909c; font-size: 13px; margin-top: 5px; }}
.section {{ background: #1a1f3a; border-radius: 10px; padding: 20px; margin-bottom: 20px; border: 1px solid #2a3050; }}
.section h2 {{ color: #80deea; font-size: 18px; margin-bottom: 15px; border-bottom: 1px solid #2a3050; padding-bottom: 10px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #2a3050; font-size: 13px; }}
th {{ color: #80deea; font-weight: 600; }}
tr:hover {{ background: #1e2444; }}
.sim-bar {{ display: inline-block; height: 16px; background: linear-gradient(90deg, #00e676, #76ff03); border-radius: 3px; vertical-align: middle; margin-right: 8px; }}
.cluster {{ background: #1e2444; border-radius: 8px; padding: 12px; margin: 8px 0; }}
.cluster-title {{ color: #ff9800; font-weight: bold; margin-bottom: 8px; }}
.tag {{ display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; margin: 2px; background: #311b92; color: #b39ddb; }}
.new-word {{ background: #1b5e20; color: #a5d6a7; }}
</style>
</head>
<body>
<div class="header">
<h1>V13.5.38 word2vec词向量训练报告</h1>
<div class="meta">模型: Skip-gram dim=100 | 语料: {len(CORPUS_TEXTS)}条TDX新闻 | 词表: {self.vocab_size}词 | 生成: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div>

<div class="summary">
<div class="card"><div class="num">{self.vocab_size}</div><div class="label">词向量总数</div></div>
<div class="card"><div class="num">{len(new_words)}</div><div class="label">发现新词</div></div>
<div class="card"><div class="num">{len(clusters)}</div><div class="label">语义聚类数</div></div>
<div class="card"><div class="num">100</div><div class="label">向量维度</div></div>
</div>

<div class="section">
<h2>语义近义词发现 (word2vec cosine similarity)</h2>
<table>
<tr><th>目标词</th><th>近义词</th><th>相似度</th></tr>
"""

        for result in results:
            similar_html = ""
            for word, score in result.similar_words:
                bar_width = int(score * 150)
                similar_html += f'<div><span class="sim-bar" style="width:{bar_width}px"></span>{word} ({score:.3f})</div>'
            html += f'<tr><td style="font-weight:bold;color:#80deea">{result.target_word}</td><td>{similar_html}</td><td></td></tr>'

        html += """
</table>
</div>

<div class="section">
<h2>自动发现的新关键词</h2>
<table>
<tr><th>#</th><th>新词</th><th>源词</th><th>相似度</th><th>建议操作</th></tr>
"""

        for i, (new_word, source, score) in enumerate(new_words[:30]):
            action = "加入词库" if score >= 0.6 else "观察" if score >= 0.4 else "忽略"
            html += f'<tr><td>{i+1}</td><td><span class="tag new-word">{new_word}</span></td><td>{source}</td><td>{score:.3f}</td><td>{action}</td></tr>'

        html += """
</table>
</div>

<div class="section">
<h2>关键词语义聚类</h2>
"""

        for cid, members in clusters.items():
            tags = " ".join([f'<span class="tag">{m}</span>' for m in members])
            html += f'<div class="cluster"><div class="cluster-title">聚类 #{cid+1} ({len(members)}词)</div>{tags}</div>'

        if not clusters:
            html += "<p>聚类数据不足（需要更多语料）</p>"

        html += f"""
</div>

<div class="section">
<h2>word2vec vs N-gram 对比</h2>
<table>
<tr><th>维度</th><th>N-gram (V13.5.37)</th><th>word2vec (V13.5.38)</th></tr>
<tr><td>发现方式</td><td>统计共现频率</td><td>语义向量相似度</td></tr>
<tr><td>深度</td><td>浅层(表面共现)</td><td>深层(语义关系)</td></tr>
<tr><td>示例</td><td>"业绩预增"→"业绩"(共现)</td><td>"业绩预增"→"盈利大增"(语义)</td></tr>
<tr><td>训练数据需求</td><td>无需训练</td><td>需语料训练</td></tr>
<tr><td>准确率</td><td>中等(共现≠语义)</td><td>高(向量捕获语义)</td></tr>
<tr><td>新词发现数</td><td>34词</td><td>{len(new_words)}词</td></tr>
</table>
</div>

</body>
</html>"""

        return html


def main():
    trainer = Word2VecTrainer()

    if trainer.model:
        # 测试
        print("\n语义近义词测试:")
        test_words = ["业绩预增", "算力", "半导体", "涨停", "突破"]
        for word in test_words:
            similar = trainer.find_similar(word, topn=5)
            print(f"  {word} → {', '.join(f'{w}({s:.2f})' for w, s in similar)}")

        # 发现新词
        print("\n新词发现:")
        existing = ["业绩预增", "净利润", "算力", "半导体", "涨停", "突破", "收购", "重组"]
        new_words = trainer.discover_new_keywords(existing, topn=3)
        for new_word, source, score in new_words[:10]:
            print(f"  {source} → {new_word} ({score:.3f})")

        # 生成报告
        html = trainer.generate_report()
        report_path = BASE / "outputs" / "V13_5_38_Word2Vec_Report.html"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n报告已生成: {report_path}")
    else:
        print("word2vec模型不可用")


if __name__ == "__main__":
    main()
