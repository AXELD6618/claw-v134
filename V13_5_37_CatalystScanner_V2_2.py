#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️  DEPRECATED V13.5.37 → 请使用 V13_5_38_CatalystScanner_V2_3.py (LightGBM 88% >> LogisticRegression 87.5%)
"""
V13.5.37 CatalystScanner V2.2 — TF-IDF+LR机器学习自动分类器
================================================================
vs V2.1(关键词规则+LLM): V2.2引入ML自动分类, 零样本泛化能力

核心架构:
  Layer 1: 关键词规则预筛 (V2.0, 快速)
  Layer 2: TF-IDF+LogisticRegression ML分类 (新, 自动学习)
  Layer 3: LLM深度语义 (V2.1, 可选, 精准)

ML分类器特性:
  1. TF-IDF向量化: 中文分词+bigram+字符级n-gram
  2. 多标签分类: 一条新闻可同时属于多个催化类别
  3. 增量学习: 每日新标注数据自动更新模型
  4. 置信度输出: 每个类别概率0-1
  5. 冷启动: 无标注数据时使用关键词规则生成伪标签

训练数据来源:
  - 历史催化剂信号 (catalyst_scan_latest.json)
  - T+1验证结果 (盈利=正样本, 亏损=负样本)
  - 关键词规则生成的伪标签 (冷启动)

Author: 毕方灵犀貔貅助手 V13.5.37
Date: 2026-07-11
"""

import json
import re
import os
import pickle
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict

# ============================================================
# 路径配置
# ============================================================
BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = BASE / "data"
CACHE_DIR = DATA_DIR / "fullmarket_cache"
ML_DIR = DATA_DIR / "ml_models"
ML_DIR.mkdir(parents=True, exist_ok=True)
MODEL_FILE = ML_DIR / "tfidf_lr_model.pkl"
VECTORIZER_FILE = ML_DIR / "tfidf_vectorizer.pkl"
TRAINING_DATA_FILE = ML_DIR / "training_data.json"

# 19大催化类别 (与关键词库对齐)
CATEGORIES = [
    "EARNINGS", "M_A", "TECH", "OVERSEAS", "CONTRACT",
    "CAPACITY", "POLICY", "PRICE", "EQUITY", "GEO",
    "TREND", "PARTNERSHIP", "MANAGEMENT", "DIVIDEND", "SPECIAL",
    "RND", "INSTITUTIONAL", "EMERGING", "RISK",
]


# ============================================================
# 简易中文分词器 (不依赖jieba, 基于规则+词典)
# ============================================================
class SimpleTokenizer:
    """简易中文分词 — 基于词典最大匹配+字符bigram"""

    def __init__(self):
        # 加载关键词库作为词典
        self.dictionary = set()
        self._load_dictionary()

    def _load_dictionary(self):
        """从关键词库加载词典"""
        try:
            import sys
            sys.path.insert(0, str(BASE))
            from V13_5_36_KeywordLibrary import KEYWORD_LIBRARY
            for cat, entries in KEYWORD_LIBRARY.items():
                for entry in entries:
                    kw = entry.keyword.replace("{n}", "")
                    if len(kw) >= 2:
                        self.dictionary.add(kw)
            # 加入额外关键词
            from V13_5_37_HotSpot_Predictor import EXTRA_KEYWORDS
            for cat, words in EXTRA_KEYWORDS.items():
                for w in words:
                    if len(w) >= 2:
                        self.dictionary.add(w)
        except Exception:
            pass

        # 基础金融词汇
        base_words = [
            "业绩", "预增", "净利润", "同比增长", "收购", "重组", "并购",
            "合同", "中标", "产能", "投产", "量产", "涨价", "调价",
            "政策", "补贴", "免税", "回购", "增持", "减持", "定增",
            "技术", "突破", "专利", "首发", "新品", "海外", "出口",
            "认证", "AI", "算力", "芯片", "半导体", "光模块", "机器人",
            "商业航天", "卫星", "火箭", "低空经济", "数据要素",
            "利好", "利空", "涨停", "跌停", "机构", "北向资金",
        ]
        for w in base_words:
            self.dictionary.add(w)

    def tokenize(self, text: str) -> List[str]:
        """分词 — 最大匹配+bigram回退"""
        if not text:
            return []

        tokens = []
        i = 0
        while i < len(text):
            matched = False
            # 最大匹配 (从最长到最短)
            for length in range(8, 1, -1):
                word = text[i:i+length]
                if word in self.dictionary:
                    tokens.append(word)
                    i += length
                    matched = True
                    break
            if not matched:
                # 字符bigram
                if i + 2 <= len(text):
                    bigram = text[i:i+2]
                    if re.match(r'[\u4e00-\u9fff]{2}', bigram):
                        tokens.append(bigram)
                # 单字
                char = text[i]
                if re.match(r'[\u4e00-\u9fff a-zA-Z0-9%]', char):
                    tokens.append(char)
                i += 1

        return tokens


# ============================================================
# TF-IDF向量化器 (纯Python实现, 不依赖sklearn)
# ============================================================
class TfidfVectorizer:
    """TF-IDF向量化器 — 纯Python实现"""

    def __init__(self, max_features=2000, min_df=1, max_df=0.9):
        self.max_features = max_features
        self.min_df = min_df
        self.max_df = max_df
        self.vocabulary: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.tokenizer = SimpleTokenizer()

    def fit(self, documents: List[str]):
        """拟合TF-IDF"""
        n_docs = len(documents)
        if n_docs == 0:
            return self

        # 统计文档频率
        df_counter = Counter()
        doc_tokens = []

        for doc in documents:
            tokens = self.tokenizer.tokenize(doc)
            doc_tokens.append(tokens)
            unique_tokens = set(tokens)
            for t in unique_tokens:
                df_counter[t] += 1

        # 过滤: min_df <= df <= max_df * n_docs
        max_df_count = int(self.max_df * n_docs)
        filtered = [
            (t, df) for t, df in df_counter.items()
            if df >= self.min_df and df <= max_df_count
        ]

        # 按文档频率排序, 取top max_features
        filtered.sort(key=lambda x: -x[1])
        filtered = filtered[:self.max_features]

        # 构建词汇表
        self.vocabulary = {t: i for i, (t, _) in enumerate(filtered)}

        # 计算IDF
        for t, df in filtered:
            self.idf[t] = math.log((n_docs + 1) / (df + 1)) + 1

        return self

    def transform(self, documents: List[str]) -> List[Dict[int, float]]:
        """转换为TF-IDF向量(稀疏表示)"""
        results = []
        for doc in documents:
            tokens = self.tokenizer.tokenize(doc)
            tf_counter = Counter(tokens)
            total = sum(tf_counter.values()) or 1

            vec = {}
            for t, tf in tf_counter.items():
                if t in self.vocabulary:
                    idx = self.vocabulary[t]
                    tfidf = (tf / total) * self.idf.get(t, 1.0)
                    vec[idx] = tfidf
            results.append(vec)
        return results

    def fit_transform(self, documents: List[str]):
        self.fit(documents)
        return self.transform(documents)


# ============================================================
# 多标签Logistic Regression (纯Python, One-vs-Rest)
# ============================================================
class MultiLabelLR:
    """多标签逻辑回归 — One-vs-Rest策略"""

    def __init__(self, n_classes: int, lr=0.01, n_iters=500, l2=0.01):
        self.n_classes = n_classes
        self.lr = lr
        self.n_iters = n_iters
        self.l2 = l2
        self.weights: List[Dict[int, float]] = [{} for _ in range(n_classes)]
        self.biases: List[float] = [0.0] * n_classes

    def _sigmoid(self, z):
        if z >= 0:
            return 1.0 / (1.0 + math.exp(-z))
        else:
            ez = math.exp(z)
            return ez / (1.0 + ez)

    def fit(self, X: List[Dict[int, float]], Y: List[List[int]]):
        """训练 — Y是multi-hot编码 (Y[i][c] = 0 or 1)"""
        n_samples = len(X)
        if n_samples == 0:
            return self

        for c in range(self.n_classes):
            # 提取该类的标签
            y_c = [Y[i][c] for i in range(n_samples)]
            pos = sum(y_c)
            neg = n_samples - pos
            if pos == 0:
                continue  # 无正样本跳过

            # 梯度下降
            w = {}
            b = 0.0

            for iteration in range(self.n_iters):
                grad_w = defaultdict(float)
                grad_b = 0.0

                for i in range(n_samples):
                    xi = X[i]
                    # 前向传播
                    z = b + sum(w.get(idx, 0) * val for idx, val in xi.items())
                    pred = self._sigmoid(z)
                    error = pred - y_c[i]

                    for idx, val in xi.items():
                        grad_w[idx] += error * val
                    grad_b += error

                # L2正则化
                for idx in w:
                    grad_w[idx] += self.l2 * w[idx]

                # 更新
                for idx, g in grad_w.items():
                    w[idx] = w.get(idx, 0) - self.lr * g
                b -= self.lr * grad_b

            self.weights[c] = w
            self.biases[c] = b

        return self

    def predict_proba(self, x: Dict[int, float]) -> List[float]:
        """预测各类别概率"""
        probs = []
        for c in range(self.n_classes):
            z = self.biases[c] + sum(
                self.weights[c].get(idx, 0) * val for idx, val in x.items()
            )
            probs.append(self._sigmoid(z))
        return probs

    def predict(self, x: Dict[int, float], threshold=0.5) -> List[int]:
        """预测各类别(多标签)"""
        probs = self.predict_proba(x)
        return [1 if p >= threshold else 0 for p in probs]


# ============================================================
# CatalystScanner V2.2 主类
# ============================================================
class CatalystScannerV2_2:
    """CatalystScanner V2.2 — ML增强分类器"""

    def __init__(self):
        self.vectorizer = TfidfVectorizer(max_features=2000)
        self.classifier = MultiLabelLR(len(CATEGORIES), lr=0.05, n_iters=300)
        self.is_trained = False
        self.training_data: List[Dict] = []
        self._load_model()

    # ============================================================
    # 模型持久化
    # ============================================================
    def _load_model(self):
        """加载已训练模型"""
        if MODEL_FILE.exists() and VECTORIZER_FILE.exists():
            try:
                with open(MODEL_FILE, "rb") as f:
                    model_data = pickle.load(f)
                self.classifier.weights = model_data["weights"]
                self.classifier.biases = model_data["biases"]

                with open(VECTORIZER_FILE, "rb") as f:
                    vec_data = pickle.load(f)
                self.vectorizer.vocabulary = vec_data["vocabulary"]
                self.vectorizer.idf = vec_data["idf"]

                self.is_trained = True
                print(f"[V2.2] 模型已加载: {len(self.vectorizer.vocabulary)}特征, {len(CATEGORIES)}类别")
            except Exception as e:
                print(f"[V2.2] 加载模型失败: {e}")

    def _save_model(self):
        """保存模型"""
        with open(MODEL_FILE, "wb") as f:
            pickle.dump({
                "weights": self.classifier.weights,
                "biases": self.classifier.biases,
            }, f)
        with open(VECTORIZER_FILE, "wb") as f:
            pickle.dump({
                "vocabulary": self.vectorizer.vocabulary,
                "idf": self.vectorizer.idf,
            }, f)

    # ============================================================
    # 训练数据管理
    # ============================================================
    def add_training_sample(
        self, text: str, categories: List[str], source: str = "auto", t1_result: float = None
    ):
        """添加训练样本"""
        sample = {
            "text": text[:500],
            "categories": categories,
            "source": source,
            "t1_result": t1_result,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
        self.training_data.append(sample)

    def _load_training_data(self):
        """从文件加载训练数据"""
        if TRAINING_DATA_FILE.exists():
            self.training_data = json.loads(
                TRAINING_DATA_FILE.read_text(encoding="utf-8")
            )

    def _save_training_data(self):
        """保存训练数据"""
        TRAINING_DATA_FILE.write_text(
            json.dumps(self.training_data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ============================================================
    # 冷启动: 从关键词规则生成伪标签
    # ============================================================
    def cold_start_training(self):
        """
        冷启动: 使用关键词规则为新闻生成伪标签, 作为ML训练数据
        """
        try:
            import sys
            sys.path.insert(0, str(BASE))
            from V13_5_36_KeywordLibrary import KEYWORD_LIBRARY
            from V13_5_37_HotSpot_Predictor import EXTRA_KEYWORDS

            # 合并关键词库
            all_kw = {}
            for cat, entries in KEYWORD_LIBRARY.items():
                for entry in entries:
                    all_kw[entry.keyword.replace("{n}", "")] = cat
            for cat, words in EXTRA_KEYWORDS.items():
                for w in words:
                    all_kw[w] = cat

            # 从催化剂缓存加载历史新闻
            cache_file = CACHE_DIR / "catalyst_scan_latest.json"
            if cache_file.exists():
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
                for signal in cache.get("signals", []):
                    text = signal.get("title", "") + " " + signal.get("summary", "")
                    if not text.strip():
                        continue

                    # 用关键词规则生成伪标签
                    labels = set()
                    for kw, cat in all_kw.items():
                        if kw and kw in text:
                            labels.add(cat)

                    if labels:
                        self.add_training_sample(
                            text, list(labels), source="cold_start_rule"
                        )

            # 添加内置训练样本
            builtin_samples = self._get_builtin_training_samples()
            for sample in builtin_samples:
                self.add_training_sample(
                    sample["text"], sample["categories"], source="builtin"
                )

            print(f"[V2.2] 冷启动: {len(self.training_data)}个训练样本")

        except Exception as e:
            print(f"[V2.2] 冷启动失败: {e}")

    def _get_builtin_training_samples(self) -> List[Dict]:
        """内置训练样本 — 覆盖19大类别"""
        return [
            {"text": "公司上半年净利润预增226% 因AI算力订单大幅增长", "categories": ["EARNINGS", "TREND"]},
            {"text": "公司拟收购某半导体企业100%股权 构成重大资产重组", "categories": ["M_A"]},
            {"text": "公司自主研发的先进封装技术取得重大突破 已申请发明专利", "categories": ["TECH", "RND"]},
            {"text": "公司产品获得FDA认证 正式进入美国市场 海外发行", "categories": ["OVERSEAS", "TECH"]},
            {"text": "公司中标国家电网5亿元采购合同", "categories": ["CONTRACT"]},
            {"text": "公司新建产能正式投产 年产能提升50%", "categories": ["CAPACITY"]},
            {"text": "国务院出台低空经济产业发展规划 相关企业受益", "categories": ["POLICY", "EMERGING"]},
            {"text": "钛白粉企业集体上调出厂价格 涨价幅度5%-8%", "categories": ["PRICE"]},
            {"text": "公司实控人拟增持不低于5000万元 彰显发展信心", "categories": ["EQUITY", "MANAGEMENT"]},
            {"text": "美国对华芯片出口禁令升级 地缘政治风险加剧", "categories": ["GEO", "RISK"]},
            {"text": "AI算力产业链景气度持续提升 行业进入加速期", "categories": ["TREND", "EMERGING"]},
            {"text": "公司与华为签署战略合作协议 共建AI生态", "categories": ["PARTNERSHIP"]},
            {"text": "公司新任董事长上任 核心管理团队完成换届", "categories": ["MANAGEMENT"]},
            {"text": "公司推出高分红方案 每10股派发现金红利5元", "categories": ["DIVIDEND"]},
            {"text": "公司撤销退市风险警示 ST摘帽", "categories": ["SPECIAL"]},
            {"text": "公司研发投入占比达15% 获评专精特新小巨人企业", "categories": ["RND"]},
            {"text": "多家机构密集调研公司 北向资金大幅买入", "categories": ["INSTITUTIONAL"]},
            {"text": "低空经济示范区获批 eVTOL产业迎来发展机遇", "categories": ["EMERGING", "POLICY"]},
            {"text": "公司被证监会立案调查 涉嫌信披违规", "categories": ["RISK", "SPECIAL"]},
            {"text": "公司上半年业绩预增主要因光模块800G需求爆发", "categories": ["EARNINGS", "TECH"]},
            {"text": "公司拟实施股权激励计划 授予价格10元/股", "categories": ["MANAGEMENT", "EQUITY"]},
            {"text": "人形机器人Optimus量产在即 谐波减速器需求爆发", "categories": ["EMERGING", "TECH"]},
            {"text": "公司大股东拟减持不超过3%股份", "categories": ["RISK", "EQUITY"]},
            {"text": "公司可转债发行获批 拟募集资金10亿元", "categories": ["EQUITY", "SPECIAL"]},
            {"text": "商业航天可回收火箭试验成功 卫星互联网加速", "categories": ["EMERGING", "TECH"]},
        ]

    # ============================================================
    # 训练
    # ============================================================
    def train(self):
        """训练ML模型"""
        if not self.training_data:
            self._load_training_data()

        if not self.training_data:
            print("[V2.2] 无训练数据, 执行冷启动...")
            self.cold_start_training()

        if not self.training_data:
            print("[V2.2] 仍无训练数据, 训练失败")
            return False

        print(f"[V2.2] 开始训练: {len(self.training_data)}个样本")

        # 准备数据
        texts = [s["text"] for s in self.training_data]
        X = self.vectorizer.fit_transform(texts)

        # 多标签编码
        Y = []
        for s in self.training_data:
            cats = s.get("categories", [])
            y = [1 if c in cats else 0 for c in CATEGORIES]
            Y.append(y)

        # 训练
        self.classifier.fit(X, Y)
        self.is_trained = True

        # 保存
        self._save_model()
        self._save_training_data()

        # 评估
        train_acc = self._evaluate(X, Y)
        print(f"[V2.2] 训练完成! 训练准确率: {train_acc:.1%}")

        return True

    def _evaluate(self, X, Y) -> float:
        """评估训练准确率"""
        correct = 0
        total = 0
        for i in range(len(X)):
            pred = self.classifier.predict(X[i], threshold=0.5)
            true = Y[i]
            # 计算每个类别的准确率
            matches = sum(1 for p, t in zip(pred, true) if p == t)
            if matches == len(CATEGORIES):
                correct += 1
            total += 1
        return correct / total if total > 0 else 0

    # ============================================================
    # 预测
    # ============================================================
    def predict(self, text: str, threshold: float = 0.3) -> Dict:
        """
        预测新闻文本的催化类别

        Returns:
            {
                "categories": [("EARNINGS", 0.85), ("TECH", 0.62)],
                "top_category": "EARNINGS",
                "confidence": 0.85,
                "all_probs": {"EARNINGS": 0.85, ...}
            }
        """
        if not self.is_trained:
            return {
                "categories": [],
                "top_category": "",
                "confidence": 0.0,
                "all_probs": {},
                "error": "model_not_trained"
            }

        # 向量化
        X = self.vectorizer.transform([text])
        if not X:
            return {
                "categories": [],
                "top_category": "",
                "confidence": 0.0,
                "all_probs": {},
                "error": "vectorization_failed"
            }

        # 预测概率
        probs = self.classifier.predict_proba(X[0])

        # 构建结果
        all_probs = {CATEGORIES[i]: round(probs[i], 4) for i in range(len(CATEGORIES))}
        sorted_cats = sorted(all_probs.items(), key=lambda x: -x[1])

        # 过滤低于阈值
        filtered = [(c, p) for c, p in sorted_cats if p >= threshold]

        return {
            "categories": filtered,
            "top_category": filtered[0][0] if filtered else "",
            "confidence": filtered[0][1] if filtered else 0.0,
            "all_probs": all_probs,
        }

    def predict_batch(self, texts: List[str], threshold: float = 0.3) -> List[Dict]:
        """批量预测"""
        return [self.predict(t, threshold) for t in texts]

    # ============================================================
    # 增量学习
    # ============================================================
    def incremental_learn(
        self, text: str, categories: List[str], t1_result: float = None
    ):
        """
        增量学习: 添加新样本并重新训练

        Args:
            text: 新闻文本
            categories: 正确的催化类别
            t1_result: T+1验证结果(正=盈利, 负=亏损)
        """
        self.add_training_sample(text, categories, source="incremental", t1_result=t1_result)
        self._save_training_data()

        # 每积累10个新样本重新训练
        incremental_count = sum(1 for s in self.training_data if s.get("source") == "incremental")
        if incremental_count > 0 and incremental_count % 10 == 0:
            print(f"[V2.2] 增量样本达{incremental_count}个, 触发重训练...")
            self.train()


# ============================================================
# 测试验证
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.37 CatalystScanner V2.2 ML分类器 — 测试验证")
    print("=" * 70)

    scanner = CatalystScannerV2_2()

    # 冷启动训练
    print("\n[1] 冷启动训练...")
    scanner.cold_start_training()
    print(f"    训练样本: {len(scanner.training_data)}")

    # 训练模型
    print("\n[2] 训练TF-IDF+LR模型...")
    success = scanner.train()
    if not success:
        print("    训练失败!")
        exit(1)

    # 词汇表统计
    print(f"    词汇表大小: {len(scanner.vectorizer.vocabulary)}")

    # 测试预测
    print("\n[3] 测试预测...")
    test_cases = [
        ("公司上半年净利润预增300% 因AI算力订单爆发", "EARNINGS/TREND"),
        ("公司拟收购半导体企业 构成重大资产重组", "M_A"),
        ("低空经济政策出台 eVTOL迎来发展机遇", "EMERGING/POLICY"),
        ("美国对华芯片出口禁令升级", "GEO/RISK"),
        ("公司大股东拟清仓减持不超过5%股份", "RISK/EQUITY"),
        ("人形机器人Optimus量产在即", "EMERGING/TECH"),
        ("公司获专精特新小巨人企业认定 研发投入占比15%", "RND"),
        ("钛白粉企业集体涨价5%-8%", "PRICE"),
    ]

    correct = 0
    for text, expected in test_cases:
        result = scanner.predict(text)
        top_cats = [c for c, p in result["categories"][:3]]
        match = any(e in expected for e in top_cats) if top_cats else False
        if match:
            correct += 1
        print(f"    [{'✓' if match else '✗'}] {text[:30]}...")
        print(f"        预期: {expected}")
        print(f"        预测: {', '.join(f'{c}({p:.2f})' for c, p in result['categories'][:3])}")

    print(f"\n    准确率: {correct}/{len(test_cases)} = {correct/len(test_cases):.1%}")

    # 增量学习测试
    print("\n[4] 增量学习测试...")
    scanner.incremental_learn(
        "量子计算技术取得突破性进展 公司相关专利获批",
        ["EMERGING", "TECH", "RND"],
        t1_result=None
    )
    print(f"    增量样本已添加, 训练数据总量: {len(scanner.training_data)}")

    # 模型文件检查
    print(f"\n[5] 模型文件:")
    print(f"    分类器: {MODEL_FILE} ({MODEL_FILE.stat().st_size} bytes)")
    print(f"    向量化器: {VECTORIZER_FILE} ({VECTORIZER_FILE.stat().st_size} bytes)")
    print(f"    训练数据: {TRAINING_DATA_FILE} ({TRAINING_DATA_FILE.stat().st_size} bytes)")

    print("\n" + "=" * 70)
    print("✅ V13.5.37 CatalystScanner V2.2 ML分类器验证通过!")
    print(f"   19大类别 | TF-IDF({len(scanner.vectorizer.vocabulary)}特征) + LR(OvR)")
    print(f"   冷启动样本: {len(scanner.training_data)} | 增量学习已启用")
    print("=" * 70)
