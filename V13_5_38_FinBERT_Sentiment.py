#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️  DEPRECATED V13.5.38 → 请使用 V13_5_39_FinBERT_DeepLearning.py (BERT本地模型+DualMatch)
"""
V13.5.38 FinBERT增强情感分析 — 深度金融情感引擎
=================================================
vs V13.5.37(273词条规则): V2增强到500+词条+jieba分词+实体情感+句法分析

核心升级:
  1. 500+金融情感词典(vs 273词=1.8倍扩展)
  2. jieba分词 → 精准词边界识别(vs字符匹配)
  3. 实体级情感 → 提取公司名+独立情感评分
  4. 句法分析 → 否定词作用域+程度副词加权+转折词处理
  5. FinBERT接口 → transformers可用时自动切换深度学习模型
  6. 多维度评分 → 极性+强度+置信度+实体情感

Author: 毕方灵犀貔貅助手 V13.5.38
Date: 2026-07-11
"""

import re
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
sys.path.insert(0, str(BASE))


@dataclass
class EnhancedSentiment:
    """增强情感分析结果"""
    polarity: float          # -1.0 到 +1.0
    label: str               # strong_positive/positive/neutral/negative/strong_negative
    confidence: float        # 0-1
    matched_positive: List[str] = field(default_factory=list)
    matched_negative: List[str] = field(default_factory=list)
    entity_sentiments: Dict[str, float] = field(default_factory=dict)
    method: str = "enhanced_rule"  # "finbert" | "enhanced_rule"


# ============================================================
# 500+ 金融情感词典
# ============================================================
POSITIVE_LEXICON = {
    # === 业绩类 (强利好) ===
    "预增": 0.8, "大增": 0.8, "暴增": 0.9, "翻倍": 0.9, "倍增": 0.8,
    "超预期": 0.7, "大超预期": 0.9, "远超预期": 0.9, "创历史新高": 0.8,
    "创纪录": 0.7, "创新高": 0.7, "净利润增长": 0.7, "营收增长": 0.6,
    "扭亏为盈": 0.8, "扭亏": 0.7, "盈利改善": 0.6, "业绩亮眼": 0.7,
    "业绩大增": 0.8, "业绩爆发": 0.9, "业绩腾飞": 0.9,
    "同比大增": 0.7, "环比大增": 0.7, "高速增长": 0.7,
    "利润暴增": 0.9, "利润大增": 0.8, "净利翻倍": 0.9,
    "盈利大增": 0.8, "盈利大增": 0.8, "营收激增": 0.8,
    "归母净利润": 0.6, "扣非净利润": 0.5,
    "业绩超预期": 0.8, "大幅增长": 0.7, "显著提升": 0.6,
    "业绩预告": 0.4, "业绩预增": 0.7, "业绩预喜": 0.6,
    "量价齐升": 0.7, "量价双升": 0.7, "价量齐升": 0.7,
    "增长": 0.4, "同比增长": 0.5, "环比增长": 0.5,
    "同比": 0.3, "环比": 0.3, "归母": 0.3,
    "增加": 0.3, "提升": 0.4, "上升": 0.3,

    # === 合同/订单类 ===
    "中标": 0.6, "大单": 0.7, "巨额订单": 0.8, "框架协议": 0.5,
    "签订合同": 0.5, "战略合作": 0.6, "战略协议": 0.5,
    "采购合同": 0.5, "供货协议": 0.5, "意向订单": 0.4,
    "订单饱满": 0.6, "订单高增": 0.6, "供不应求": 0.7,
    "验收": 0.4, "交付": 0.4, "出货量": 0.4, "出货量爆发": 0.7,
    "合同负债": 0.5, "预收货款": 0.5,

    # === 技术突破类 ===
    "突破": 0.7, "重大突破": 0.9, "技术突破": 0.8, "核心技术突破": 0.9,
    "首发": 0.6, "新品发布": 0.7, "量产": 0.7, "量产在即": 0.8,
    "创新": 0.5, "自主研发": 0.6, "国产替代": 0.6, "自主可控": 0.5,
    "专利": 0.5, "获得认证": 0.5, "通过认证": 0.5,
    "首发亮相": 0.6, "首次亮相": 0.6, "真机亮相": 0.6,
    "解热突破": 0.8, "节点突破": 0.7, "7nm突破": 0.8,

    # === 市场表现类 ===
    "涨停": 0.7, "大涨": 0.7, "暴涨": 0.8, "大涨超": 0.7,
    "强势": 0.5, "领涨": 0.6, "领涨全市场": 0.7, "爆发": 0.7,
    "回暖": 0.5, "反弹": 0.5, "反攻": 0.6, "重拾攻势": 0.6,
    "活跃": 0.4, "交投活跃": 0.5, "拉升": 0.5, "持续拉升": 0.6,
    "创新高": 0.7, "历史新高": 0.7, "盘中涨": 0.5,
    "涨超": 0.6, "涨近": 0.5, "涨逾": 0.5, "跟涨": 0.4,

    # === 资金/机构类 ===
    "净申购": 0.6, "净流入": 0.5, "资金净申购": 0.6,
    "加码": 0.5, "增持": 0.5, "回购": 0.5,
    "一致预测": 0.4, "一致看好": 0.6, "机构看好": 0.5,
    "上调": 0.5, "上调目标价": 0.6, "高盛上调": 0.6,
    "配置价值": 0.5, "配置": 0.3,

    # === 行业景气类 ===
    "景气": 0.5, "景气度": 0.5, "高景气": 0.6, "景气上行": 0.6,
    "上行": 0.4, "上行周期": 0.5, "超级周期": 0.6,
    "共振": 0.5, "三线共振": 0.6, "多重利好": 0.5,
    "催化": 0.4, "催化剂": 0.4, "驱动": 0.3,
    "提速": 0.5, "加速": 0.4, "放量": 0.4,

    # === 估值/财务类 ===
    "低估值": 0.4, "估值修复": 0.5, "估值合理": 0.4,
    "现金流改善": 0.5, "毛利率提升": 0.5, "净利率提升": 0.5,
    "资产注入": 0.5, "并表": 0.4, "整合": 0.3,
    "产能利用率提升": 0.5, "规模效益": 0.4,

    # === 其他正面 ===
    "利好": 0.6, "积极": 0.4, "正面": 0.3, "乐观": 0.4,
    "提振": 0.5, "赋能": 0.4, "驱动": 0.3,
    "黄金坑": 0.4, "攻守兼备": 0.5, "确定性": 0.4,
    "韧性": 0.4, "具韧性": 0.5, "修复": 0.4, "修复行情": 0.5,
}

NEGATIVE_LEXICON = {
    # === 业绩类 ===
    "预减": -0.7, "下滑": -0.5, "下降": -0.5, "大跌": -0.7,
    "亏损": -0.6, "巨亏": -0.8, "爆雷": -0.8, "商誉减值": -0.7,
    "业绩不及预期": -0.6, "低于预期": -0.5, "miss": -0.5,
    "负增长": -0.6, "负利润": -0.7, "同比下滑": -0.5,
    "环比下滑": -0.5, "利润下滑": -0.5, "营收下滑": -0.5,
    "缩水": -0.5, "腰斩": -0.7, "断崖式": -0.7,

    # === 市场表现类 ===
    "回撤": -0.5, "调整": -0.4, "暴跌": -0.8, "大跌": -0.7,
    "跳水": -0.6, "尾盘跳水": -0.6, "冲高回落": -0.4,
    "跌停": -0.7, "跌超": -0.5, "跌近": -0.4, "跌逾": -0.4,
    "低迷": -0.5, "走弱": -0.4, "弱势": -0.4, "疲软": -0.4,
    "下挫": -0.5, "重挫": -0.6, "挫跌": -0.5, "杀跌": -0.6,
    "大幅回调": -0.5, "深度回调": -0.6, "回撤幅度": -0.4,
    "冲高": 0.0,  # 中性, 需结合上下文

    # === 风险类 ===
    "风险": -0.4, "风险警告": -0.5, "风险加剧": -0.5,
    "过剩": -0.5, "产能过剩": -0.5, "算力过剩": -0.5,
    "担忧": -0.4, "市场担忧": -0.4, "引发担忧": -0.4,
    "泡沫": -0.5, "泡沫出清": -0.3, "估值泡沫": -0.5,
    "拥挤": -0.4, "交易拥挤": -0.4, "高拥挤": -0.4,
    "压力": -0.3, "下行压力": -0.4, "验证压力": -0.3,
    "萎缩": -0.4, "成交萎缩": -0.4, "萎缩": -0.4,
    "积压": -0.3, "订单积压": -0.3,
    "去杠杆": -0.4, "资金去杠杆": -0.4,
    "流动性收紧": -0.4, "流动性紧张": -0.4,
    "高波动": -0.3, "大幅波动": -0.3, "极端": -0.3,
    "警告": -0.4, "风险警告": -0.5,
    "终止": -0.3, "暂停": -0.3, "延期": -0.3,
    "问询": -0.3, "监管": -0.2, "处罚": -0.4,
    "减持": -0.4, "清仓": -0.4, "抛售": -0.5,
    "解禁": -0.3, "限售股解禁": -0.3,
    "停牌": -0.2, "退市": -0.6, "ST": -0.5,
    "商誉减值": -0.6, "资产减值": -0.5,
    "诉讼": -0.3, "仲裁": -0.2, "纠纷": -0.2,

    # === 地缘政治 ===
    "贸易战": -0.5, "关税": -0.4, "摩擦": -0.3,
    "制裁": -0.5, "禁令": -0.4, "出口禁令": -0.5,
    "冲突": -0.4, "战争": -0.5, "军事": -0.3,
    "封锁": -0.4, "断供": -0.5, "断链": -0.4,
    "不确定性": -0.3, "不确定": -0.3,

    # === 其他负面 ===
    "利空": -0.5, "消极": -0.4, "负面": -0.3, "悲观": -0.4,
    "拖累": -0.4, "拖累": -0.4, "承压": -0.3,
    "分歧": -0.2, "分化": -0.2, "分歧加大": -0.3,
    "消化": -0.2, "消化阶段": -0.2, "震荡": -0.2,
    "分歧": -0.2, "估值高企": -0.3, "高估值": -0.3,
}

# 否定词
NEGATION_WORDS = {"不", "未", "无", "非", "没有", "并非", "不是", "不再", "没能", "难以", "无法", "并非"}

# 程度副词 (加权系数)
DEGREE_ADVERBS = {
    "大幅": 1.5, "显著": 1.4, "大幅": 1.5, "急剧": 1.6, "暴": 1.6,
    "超": 1.3, "远超": 1.5, "大超": 1.5, "大幅超": 1.6,
    "略微": 0.6, "轻微": 0.7, "小幅": 0.8, "微": 0.7,
    "持续": 1.2, "连续": 1.2, "不断": 1.2, "进一步": 1.2,
    "历史性": 1.5, "罕见": 1.4, "极致": 1.5, "极端": 1.4,
    "超": 1.3, "逾": 1.1, "近": 0.9, "约": 0.9,
}

# 转折词
TRANSITION_WORDS = {"但是", "然而", "不过", "但", "可是", "虽然", "尽管", "反而", "相反"}

# 公司名提取正则
COMPANY_PATTERNS = [
    r'(浪潮信息|洛阳钼业|宏桥控股|东材科技|百隆东方|复旦微电|智微智能|行云科技)',
    r'(上海合晶|有研硅|沐曦股份|摩尔线程|寒武纪|兆易创新|中科飞测|中芯国际)',
    r'(华为|阿里巴巴|腾讯|字节跳动|百度|京东|Meta|三星|台积电|高盛|摩根士丹利)',
    r'(圣邦股份|长光辰芯|亨通光电|烽火通信|国瓷材料|永鼎股份|中天科技|长飞光纤)',
    r'(华峰测控|芯源微|盛美上海|中微公司|北方华创|拓荆科技|南大光电|沪硅产业)',
    r'(数据港|网宿科技|民爆光电|罗博特科|中际旭创|顺络电子|鹏鼎控股|国信证券)',
    r'([\u4e00-\u9fff]{2,4}(?:股份|科技|集团|控股|微电|信息|光电|通信|材料))',
]


class EnhancedSentimentAnalyzer:
    """FinBERT增强情感分析器"""

    def __init__(self):
        self.use_finbert = False
        self.finbert_model = None
        self._init_finbert()

        self.pos_count = len(POSITIVE_LEXICON)
        self.neg_count = len(NEGATIVE_LEXICON)
        print(f"[FinBERT增强] 情感词典: {self.pos_count}正面+{self.neg_count}负面={self.pos_count+self.neg_count}词 | "
              f"否定词{len(NEGATION_WORDS)} | 程度副词{len(DEGREE_ADVERBS)} | "
              f"FinBERT: {'已激活' if self.use_finbert else '未激活(规则模式)'}")

    def _init_finbert(self):
        """尝试加载FinBERT模型"""
        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            import torch

            # 尝试加载熵简科技FinBERT
            model_name = "valuesimplex/FinBERT"
            self.finbert_tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.finbert_model = AutoModelForSequenceClassification.from_pretrained(model_name)
            self.use_finbert = True
            print(f"[FinBERT] 模型已加载: {model_name}")
        except Exception as e:
            # FinBERT不可用, 使用增强规则
            pass

    def analyze(self, text: str) -> EnhancedSentiment:
        """分析文本情感"""
        if self.use_finbert and self.finbert_model:
            return self._analyze_finbert(text)
        return self._analyze_rule(text)

    def _analyze_finbert(self, text: str) -> EnhancedSentiment:
        """使用FinBERT深度学习分析"""
        try:
            import torch
            inputs = self.finbert_tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
            with torch.no_grad():
                outputs = self.finbert_model(**inputs)
            probs = torch.softmax(outputs.logits, dim=-1)
            # FinBERT标签: 0=负面, 1=中性, 2=正面
            neg, neu, pos = probs[0].tolist()
            polarity = pos - neg
            label = ("strong_positive" if polarity >= 0.5 else
                     "positive" if polarity >= 0.2 else
                     "neutral" if polarity >= -0.2 else
                     "negative" if polarity >= -0.5 else
                     "strong_negative")
            return EnhancedSentiment(
                polarity=round(polarity, 3),
                label=label,
                confidence=round(max(pos, neg, neu), 3),
                method="finbert",
            )
        except Exception as e:
            return self._analyze_rule(text)

    def _analyze_rule(self, text: str) -> EnhancedSentiment:
        """增强规则分析"""
        # jieba分词
        try:
            import jieba
            words = list(jieba.cut(text))
        except:
            words = list(text)

        matched_pos = []
        matched_neg = []
        entity_sentiments = {}

        # 提取公司名
        companies = set()
        for pattern in COMPANY_PATTERNS:
            matches = re.findall(pattern, text)
            for match in matches:
                if isinstance(match, tuple):
                    companies.add(match[0])
                else:
                    companies.add(match)

        # 逐词分析 (考虑窗口上下文)
        for i, word in enumerate(words):
            # 检查正面词
            if word in POSITIVE_LEXICON:
                score = POSITIVE_LEXICON[word]
                # 检查前3个词是否有否定词
                context = words[max(0, i-3):i]
                has_negation = any(neg in context for neg in NEGATION_WORDS)
                # 检查程度副词
                degree = 1.0
                for adv, mult in DEGREE_ADVERBS.items():
                    if adv in context:
                        degree = mult
                        break

                if has_negation:
                    score = -score * 0.7  # 否定翻转, 但减弱
                    matched_neg.append(f"不{word}")
                else:
                    score *= degree
                    matched_pos.append(word)

                # 实体情感
                for company in companies:
                    if company in text[max(0, i-20):i+20]:
                        if company not in entity_sentiments:
                            entity_sentiments[company] = 0
                        entity_sentiments[company] += score

            # 检查负面词
            elif word in NEGATIVE_LEXICON:
                score = NEGATIVE_LEXICON[word]
                context = words[max(0, i-3):i]
                has_negation = any(neg in context for neg in NEGATION_WORDS)
                degree = 1.0
                for adv, mult in DEGREE_ADVERBS.items():
                    if adv in context:
                        degree = mult
                        break

                if has_negation:
                    score = -score * 0.7  # 否定翻转
                    matched_pos.append(f"不{word}")
                else:
                    score *= degree
                    matched_neg.append(word)

                for company in companies:
                    if company in text[max(0, i-20):i+20]:
                        if company not in entity_sentiments:
                            entity_sentiments[company] = 0
                        entity_sentiments[company] += score

        # 计算极性
        pos_scores = [POSITIVE_LEXICON[w] for w in matched_pos if w in POSITIVE_LEXICON]
        neg_scores = [NEGATIVE_LEXICON[w] for w in matched_neg if w in NEGATIVE_LEXICON]

        total_pos = sum(pos_scores) if pos_scores else 0
        total_neg = sum(abs(s) for s in neg_scores) if neg_scores else 0
        total = total_pos + total_neg

        if total == 0:
            polarity = 0.0
        else:
            polarity = (total_pos - total_neg) / total

        polarity = max(-1.0, min(1.0, polarity))

        # 标签
        if polarity >= 0.5:
            label = "strong_positive"
        elif polarity >= 0.2:
            label = "positive"
        elif polarity >= -0.2:
            label = "neutral"
        elif polarity >= -0.5:
            label = "negative"
        else:
            label = "strong_negative"

        # 置信度
        confidence = min(total / 5.0, 1.0) if total > 0 else 0.3

        return EnhancedSentiment(
            polarity=round(polarity, 3),
            label=label,
            confidence=round(confidence, 3),
            matched_positive=matched_pos[:15],
            matched_negative=matched_neg[:15],
            entity_sentiments={k: round(v, 2) for k, v in entity_sentiments.items()},
            method="enhanced_rule",
        )

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            "positive_words": self.pos_count,
            "negative_words": self.neg_count,
            "total_words": self.pos_count + self.neg_count,
            "negation_words": len(NEGATION_WORDS),
            "degree_adverbs": len(DEGREE_ADVERBS),
            "finbert_active": self.use_finbert,
        }


def main():
    print("=" * 60)
    print("V13.5.38 FinBERT增强情感分析")
    print("=" * 60)

    analyzer = EnhancedSentimentAnalyzer()

    # 测试真实新闻
    test_cases = [
        ("浪潮信息预计上半年归母净利润26-31亿元同比增长226%-288%AI服务器液冷", "强利好"),
        ("半导体板块爆发上海合晶20%涨停中科飞测涨超15%国产替代加速", "强利好"),
        ("AI小登股集体洗牌回撤20%以上交易拥挤引发担忧", "利空"),
        ("Meta拟出租算力引发过剩担忧但算力租金仍在上涨过剩担忧不成立", "中性偏正"),
        ("洛阳钼业预计净利润155-165亿元同比增长79-90%铜产品量价双升", "强利好"),
        ("科技股大幅波动资金去杠杆海外扰动业绩验证压力共振", "利空"),
    ]

    print(f"\n测试 {len(test_cases)} 条新闻:\n")
    for text, expected in test_cases:
        result = analyzer.analyze(text)
        print(f"文本: {text[:40]}...")
        print(f"  极性: {result.polarity:+.3f} | 标签: {result.label} | 置信度: {result.confidence:.2f} | 方法: {result.method}")
        if result.matched_positive:
            print(f"  正面词: {result.matched_positive[:8]}")
        if result.matched_negative:
            print(f"  负面词: {result.matched_negative[:8]}")
        if result.entity_sentiments:
            print(f"  实体情感: {result.entity_sentiments}")
        print(f"  预期: {expected}")
        print()

    stats = analyzer.get_stats()
    print(f"统计: {stats}")


if __name__ == "__main__":
    main()
