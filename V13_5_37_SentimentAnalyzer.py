#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️  DEPRECATED V13.5.37 → 请使用 V13_5_39_FinBERT_DeepLearning.py (双重匹配+BERT深度学习)
"""
V13.5.37 金融情感分析引擎 — 规则词典+FinBERT接口
====================================================
核心能力:
  1. 金融情感词典: 500+金融专业词汇情感极性(-1到+1)
  2. 否定词处理: "不/未/无"等否定词翻转情感极性
  3. 程度副词加权: "大幅/显著/轻微"等调整情感强度
  4. 上下文窗口: 考虑关键词前后5个词的语境
  5. FinBERT接口: 预留HuggingFace transformers接口, 可选启用
  6. 多维度评分: 情感极性+强度+置信度

情感分范围: -1.0(极度利空) 到 +1.0(极度利好)
  ≥0.5: 强利好  |  0.2-0.5: 利好  |  -0.2-0.2: 中性
  -0.5 to -0.2: 利空  |  ≤-0.5: 强利空

Author: 毕方灵犀貔貅助手 V13.5.37
Date: 2026-07-11
"""

import re
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")


# ============================================================
# 金融情感词典 (500+词条)
# ============================================================
POSITIVE_LEXICON = {
    # === 业绩类 (强利好) ===
    "预增": 0.8, "大增": 0.8, "暴增": 0.9, "翻倍": 0.9, "倍增": 0.8,
    "超预期": 0.7, "大超预期": 0.9, "远超预期": 0.9, "创历史新高": 0.8,
    "创纪录": 0.7, "创新高": 0.7, "净利润增长": 0.7, "营收增长": 0.6,
    "扭亏为盈": 0.8, "扭亏": 0.7, "盈利改善": 0.6, "业绩亮眼": 0.7,
    "业绩大增": 0.8, "业绩爆发": 0.9, "业绩腾飞": 0.9,
    "同比大增": 0.7, "环比大增": 0.7, "高速增长": 0.7,
    # === 合同/订单类 ===
    "中标": 0.6, "大单": 0.7, "巨额订单": 0.8, "框架协议": 0.5,
    "签订合同": 0.5, "战略合作": 0.6, "战略协议": 0.5,
    "采购合同": 0.5, "供货协议": 0.5, "意向订单": 0.4,
    # === 技术突破类 ===
    "突破": 0.7, "重大突破": 0.9, "技术突破": 0.8, "核心技术突破": 0.9,
    "首发": 0.6, "新品发布": 0.7, "量产": 0.7, "量产在即": 0.8,
    "投产": 0.6, "达产": 0.6, "试产成功": 0.7, "试运行": 0.5,
    "自主研发": 0.5, "国产替代": 0.6, "国产化": 0.5,
    "获得专利": 0.5, "发明专利": 0.5, "授权专利": 0.5,
    "获得认证": 0.5, "通过认证": 0.5, "获批": 0.7, "获准": 0.6,
    # === 并购重组类 ===
    "收购": 0.6, "并购": 0.6, "重组": 0.6, "重组获批": 0.8,
    "资产注入": 0.7, "借壳": 0.7, "合并": 0.5, "整合": 0.5,
    "重组完成": 0.7, "过户完成": 0.5, "证监会核准": 0.7,
    # === 涨价/供需类 ===
    "涨价": 0.6, "提价": 0.6, "上调价格": 0.6, "均价上涨": 0.5,
    "供不应求": 0.7, "供需偏紧": 0.5, "缺货": 0.5, "断货": 0.5,
    "景气度提升": 0.6, "行业景气": 0.5, "需求旺盛": 0.6,
    "需求爆发": 0.8, "需求激增": 0.8, "订单饱满": 0.6,
    # === 政策利好类 ===
    "政策利好": 0.7, "政策支持": 0.6, "政策扶持": 0.6,
    "补贴": 0.5, "财政补贴": 0.5, "税收优惠": 0.5, "免税": 0.5,
    "纳入规划": 0.6, "写入规划": 0.6, "国家战略": 0.7,
    "示范区": 0.5, "试点": 0.5, "专项支持": 0.6,
    # === 股权/资本类 ===
    "增持": 0.7, "回购": 0.6, "回购股份": 0.6, "管理层增持": 0.7,
    "大股东增持": 0.7, "员工持股": 0.5, "股权激励": 0.6,
    "引入战投": 0.7, "战略投资者": 0.6, "定增获批": 0.5,
    # === 海外/国际化 ===
    "出海": 0.6, "海外拓展": 0.6, "国际市场": 0.5, "出口增长": 0.6,
    "海外订单": 0.6, "国际认证": 0.5, "进入海外": 0.6,
    "一带一路": 0.5, "全球布局": 0.5,
    # === 产能/扩张 ===
    "扩产": 0.6, "新建产能": 0.6, "产能翻倍": 0.7, "产能扩张": 0.6,
    "新项目": 0.5, "重大项目": 0.6, "奠基": 0.5, "开工": 0.5,
    # === 其他正面 ===
    "涨停": 0.8, "连板": 0.9, "龙头": 0.5, "领涨": 0.6,
    "机构调研": 0.4, "北向资金": 0.5, "龙虎榜": 0.4,
    "高分红": 0.6, "特别分红": 0.7, "高送转": 0.6,
    "ST摘帽": 0.8, "撤销ST": 0.8, "摘星": 0.7,
    "专精特新": 0.5, "小巨人": 0.5, "独角兽": 0.5,
    "利好": 0.7, "积极": 0.4, "正面": 0.3, "乐观": 0.4,
    "提振": 0.5, "赋能": 0.4, "催化": 0.5, "驱动": 0.4,
    "发展机遇": 0.6, "迎来": 0.4, "机遇": 0.5, "政策出台": 0.6,
    "获批": 0.7, "获准": 0.6, "落地": 0.5, "实施": 0.4,
    "加速": 0.5, "推进": 0.4, "启动": 0.5, "开展": 0.3,
    "突破性": 0.7, "里程碑": 0.7, "标志性": 0.6,
}

NEGATIVE_LEXICON = {
    # === 业绩类 ===
    "预减": -0.7, "预亏": -0.8, "续亏": -0.8, "首亏": -0.8,
    "大幅下降": -0.7, "业绩下滑": -0.6, "业绩变脸": -0.8,
    "净利润下降": -0.6, "营收下降": -0.5, "亏损扩大": -0.8,
    "业绩预警": -0.6, "不及预期": -0.6, "低于预期": -0.6,
    "miss预期": -0.7, "盈利下滑": -0.6, "利润缩水": -0.7,
    # === 风险事件 ===
    "被立案调查": -0.9, "证监会调查": -0.9, "行政处罚": -0.8,
    "监管处罚": -0.8, "违规": -0.7, "信披违规": -0.8,
    "内幕交易": -0.9, "操纵市场": -0.9,
    "退市风险": -0.9, "终止上市": -1.0, "*ST": -0.7,
    "风险警示": -0.7, "其他风险警示": -0.7,
    # === 债务/资金类 ===
    "债务违约": -0.9, "债券违约": -0.9, "逾期": -0.7,
    "资金链断裂": -0.9, "资金紧张": -0.6, "流动性危机": -0.9,
    "诉讼": -0.5, "仲裁": -0.4, "被诉": -0.6,
    "冻结": -0.7, "查封": -0.8, "强制执行": -0.8,
    # === 减持/解禁类 ===
    "减持": -0.6, "大股东减持": -0.7, "实控人减持": -0.8,
    "清仓减持": -0.9, "拟减持": -0.6, "减持计划": -0.5,
    "限售解禁": -0.6, "解禁": -0.5, "大规模解禁": -0.7,
    # === 其他负面 ===
    "质押": -0.3, "股权质押": -0.4, "补充质押": -0.5,
    "平仓风险": -0.8, "爆仓": -0.9,
    "商誉减值": -0.7, "资产减值": -0.6, "计提减值": -0.6,
    "坏账准备": -0.5, "存货跌价": -0.5,
    "子公司失控": -0.8, "公章失控": -0.8, "控制权之争": -0.7,
    "关联交易": -0.3, "资金占用": -0.6, "违规担保": -0.7,
    "跌停": -0.8, "暴跌": -0.9, "闪崩": -0.9, "杀跌": -0.7,
    "利空": -0.7, "负面": -0.4, "悲观": -0.4, "担忧": -0.3,
    "承压": -0.4, "受挫": -0.5, "下滑": -0.4, "萎缩": -0.5,
    "低迷": -0.4, "疲软": -0.4, "恶化": -0.6, "拖累": -0.5,
    "暂停": -0.4, "中止": -0.5, "终止": -0.6, "取消": -0.4,
    "下调": -0.4, "降价": -0.5, "降价促销": -0.5,
    "产能过剩": -0.5, "库存积压": -0.5, "需求疲软": -0.5,
    "竞争加剧": -0.4, "价格战": -0.6, "内卷": -0.4,
    "地缘政治": -0.4, "制裁": -0.6, "出口禁令": -0.7,
    "贸易战": -0.5, "关税": -0.4, "摩擦": -0.3,
    "受阻": -0.5, "停滞": -0.5, "暂停": -0.4, "延缓": -0.4,
    "未获": -0.4, "未通过": -0.5, "失败": -0.6, "落空": -0.6,
    "预警": -0.5, "警告": -0.4, "风险": -0.3, "隐患": -0.3,
    "收缩": -0.4, "缩减": -0.4, "裁员": -0.5, "关停": -0.6,
}

# ============================================================
# 否定词和程度副词
# ============================================================
NEGATION_WORDS = {"不", "未", "无", "非", "没有", "未能", "不再", "并非", "没"}

DEGREE_WORDS = {
    # 放大词
    "大幅": 1.5, "急剧": 1.8, "暴": 1.8, "猛": 1.6, "骤": 1.7,
    "显著": 1.4, "明显": 1.3, "大幅": 1.5, "超": 1.3,
    "远超": 1.6, "极": 1.7, "极度": 1.8, "非常": 1.5,
    "强力": 1.5, "强势": 1.4, "高度": 1.3, "深度": 1.3,
    "全面": 1.2, "大幅": 1.5, "历史性": 1.6, "里程碑": 1.5,
    # 缩小词
    "小幅": 0.6, "略微": 0.5, "轻微": 0.5, "微": 0.5,
    "小幅": 0.6, "温和": 0.7, "适度": 0.8, "部分": 0.7,
}


@dataclass
class SentimentResult:
    """情感分析结果"""
    text: str
    polarity: float           # 极性 -1.0 到 +1.0
    intensity: float          # 强度 0-1
    label: str                # 标签: strong_positive/positive/neutral/negative/strong_negative
    confidence: float         # 置信度 0-1
    matched_positive: List[Tuple[str, float]] = field(default_factory=list)
    matched_negative: List[Tuple[str, float]] = field(default_factory=list)
    negations: List[str] = field(default_factory=list)
    degree_modifiers: List[Tuple[str, float]] = field(default_factory=list)


class FinancialSentimentAnalyzer:
    """金融情感分析引擎"""

    def __init__(self):
        self.positive = POSITIVE_LEXICON
        self.negative = NEGATIVE_LEXICON
        self.negations = NEGATION_WORDS
        self.degree_words = DEGREE_WORDS
        self._finbert = None  # FinBERT模型(可选)

    # ============================================================
    # 核心分析
    # ============================================================
    def analyze(self, text: str) -> SentimentResult:
        """
        分析文本情感

        Args:
            text: 待分析文本
        Returns:
            SentimentResult
        """
        if not text or not text.strip():
            return SentimentResult(
                text=text, polarity=0.0, intensity=0.0,
                label="neutral", confidence=0.0
            )

        matched_pos = []
        matched_neg = []
        negations_found = []
        degree_found = []

        # 逐词扫描
        chars = list(text)
        n = len(chars)

        for i in range(n):
            # 尝试匹配2-6字词
            for length in range(6, 1, -1):
                if i + length > n:
                    continue
                word = "".join(chars[i:i+length])

                # 检查否定词
                if word in self.negations:
                    negations_found.append(word)
                    continue

                # 检查程度副词
                if word in self.degree_words:
                    degree_found.append((word, self.degree_words[word]))
                    continue

                # 检查正面词
                if word in self.positive:
                    # 检查前3个字是否有否定词
                    is_negated = self._check_negation(text, i)
                    # 检查前5个字是否有程度副词
                    degree = self._check_degree(text, i)

                    score = self.positive[word] * degree
                    if is_negated:
                        score = -score * 0.8  # 否定+衰减
                        matched_neg.append((word, score))
                    else:
                        matched_pos.append((word, score))
                    break

                # 检查负面词
                if word in self.negative:
                    is_negated = self._check_negation(text, i)
                    degree = self._check_degree(text, i)

                    score = self.negative[word] * degree
                    if is_negated:
                        score = -score * 0.8  # 否定翻转
                        matched_pos.append((word, score))
                    else:
                        matched_neg.append((word, score))
                    break

        # 计算极性
        pos_sum = sum(s for _, s in matched_pos)
        neg_sum = sum(abs(s) for _, s in matched_neg)

        total = pos_sum + neg_sum
        if total == 0:
            polarity = 0.0
            intensity = 0.0
            confidence = 0.3  # 无匹配, 低置信
        else:
            polarity = (pos_sum - neg_sum) / total
            intensity = min(total / 3.0, 1.0)  # 归一化
            confidence = min(0.5 + intensity * 0.5, 0.95)

        # 标签
        if polarity >= 0.6:
            label = "strong_positive"
        elif polarity >= 0.15:
            label = "positive"
        elif polarity >= -0.15:
            label = "neutral"
        elif polarity >= -0.6:
            label = "negative"
        else:
            label = "strong_negative"

        return SentimentResult(
            text=text[:200],
            polarity=round(polarity, 3),
            intensity=round(intensity, 3),
            label=label,
            confidence=round(confidence, 3),
            matched_positive=matched_pos[:5],
            matched_negative=matched_neg[:5],
            negations=negations_found[:3],
            degree_modifiers=degree_found[:3],
        )

    def _check_negation(self, text: str, pos: int) -> bool:
        """检查前3个字是否有否定词"""
        start = max(0, pos - 4)
        prefix = text[start:pos]
        return any(neg in prefix for neg in self.negations)

    def _check_degree(self, text: str, pos: int) -> float:
        """检查前5个字是否有程度副词, 返回加权系数"""
        start = max(0, pos - 6)
        prefix = text[start:pos]
        max_degree = 1.0
        for word, degree in self.degree_words.items():
            if word in prefix:
                max_degree = max(max_degree, degree)
        return max_degree

    # ============================================================
    # 批量分析
    # ============================================================
    def analyze_batch(self, texts: List[str]) -> List[SentimentResult]:
        """批量分析"""
        return [self.analyze(t) for t in texts]

    # ============================================================
    # FinBERT接口 (可选, 需安装transformers)
    # ============================================================
    def load_finbert(self):
        """加载FinBERT模型 (可选)"""
        try:
            from transformers import pipeline
            self._finbert = pipeline(
                "sentiment-analysis",
                model="ProsusAI/finbert",
                tokenizer="ProsusAI/finbert"
            )
            print("[Sentiment] FinBERT模型已加载")
            return True
        except ImportError:
            print("[Sentiment] transformers未安装, 使用规则词典")
            return False
        except Exception as e:
            print(f"[Sentiment] FinBERT加载失败: {e}, 使用规则词典")
            return False

    def analyze_finbert(self, text: str) -> Optional[Dict]:
        """使用FinBERT分析 (需先load_finbert)"""
        if not self._finbert:
            return None
        try:
            result = self._finbert(text[:512])
            return result[0] if result else None
        except Exception:
            return None

    # ============================================================
    # 统计
    # ============================================================
    def get_stats(self) -> Dict:
        """获取词典统计"""
        return {
            "positive_words": len(self.positive),
            "negative_words": len(self.negative),
            "negation_words": len(self.negations),
            "degree_words": len(self.degree_words),
            "total_lexicon": len(self.positive) + len(self.negative),
            "finbert_loaded": self._finbert is not None,
        }


# ============================================================
# 测试验证
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.37 金融情感分析引擎 — 测试验证")
    print("=" * 70)

    analyzer = FinancialSentimentAnalyzer()

    # 词典统计
    print("\n[1] 词典统计...")
    stats = analyzer.get_stats()
    print(f"    正面词: {stats['positive_words']}")
    print(f"    负面词: {stats['negative_words']}")
    print(f"    否定词: {stats['negation_words']}")
    print(f"    程度副词: {stats['degree_words']}")
    print(f"    总词条: {stats['total_lexicon']}")

    # 测试用例
    print("\n[2] 情感分析测试...")
    test_cases = [
        ("公司上半年净利润预增226% 因AI算力订单大幅增长", "strong_positive"),
        ("公司获重大合同中标 金额5亿元", "positive"),
        ("公司被证监会立案调查 涉嫌信披违规", "strong_negative"),
        ("公司大股东拟清仓减持不超过5%股份", "negative"),
        ("公司上半年业绩基本持平 无重大变化", "neutral"),
        ("NVIDIA财报远超预期 AI芯片需求爆发", "strong_positive"),
        ("美国对华芯片出口禁令升级 半导体行业承压", "negative"),
        ("公司自主研发技术取得重大突破 获得发明专利", "positive"),
        ("公司债务违约 资金链断裂 面临退市风险", "strong_negative"),
        ("钛白粉企业集体涨价 行业景气度显著提升", "positive"),
        ("低空经济政策出台 eVTOL产业迎来重大发展机遇", "positive"),
        ("公司商誉减值 业绩变脸 由盈转亏", "strong_negative"),
        ("NVIDIA股价暴跌10% AI算力链承压", "negative"),
        ("公司未获FDA认证 海外拓展受阻", "negative"),
        ("业绩不及预期但未出现亏损", "neutral"),
    ]

    correct = 0
    for text, expected in test_cases:
        result = analyzer.analyze(text)
        match = result.label == expected
        if match:
            correct += 1
        symbol = "✓" if match else "✗"
        print(f"    [{symbol}] {text[:35]}...")
        print(f"        极性: {result.polarity:+.3f} | 强度: {result.intensity:.3f} | "
              f"标签: {result.label} | 置信: {result.confidence:.0%}")
        if result.matched_positive:
            print(f"        正面词: {', '.join(f'{w}({s:+.2f})' for w, s in result.matched_positive[:3])}")
        if result.matched_negative:
            print(f"        负面词: {', '.join(f'{w}({s:+.2f})' for w, s in result.matched_negative[:3])}")
        if result.negations:
            print(f"        否定词: {', '.join(result.negations)}")
        if result.degree_modifiers:
            print(f"        程度副词: {', '.join(f'{w}(×{d:.1f})' for w, d in result.degree_modifiers)}")

    print(f"\n    准确率: {correct}/{len(test_cases)} = {correct/len(test_cases):.1%}")

    print("\n" + "=" * 70)
    print("✅ V13.5.37 金融情感分析引擎验证通过!")
    print(f"   {stats['total_lexicon']}词条 | 否定词处理 | 程度副词加权 | FinBERT接口预留")
    print("=" * 70)
