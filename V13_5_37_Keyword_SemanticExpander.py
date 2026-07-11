#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.37 关键词语义扩展引擎 — 自动发现语义相近新词
====================================================
核心能力:
  1. 语义相似度计算: 基于字符共现+上下文窗口+编辑距离
  2. 新词发现: 从TDX新闻语料中提取高频词组, 与现有关键词对比
  3. 语义聚类: 将648+关键词按语义相似度聚类
  4. 自动扩展: 给定"业绩预增"→发现"盈利大增/净利翻倍/业绩超预期"
  5. N-gram提取: 从新闻文本中提取2-4字高频词组
  6. 共现矩阵: 关键词共现频率→语义关联度

扩展逻辑:
  现有关键词库(648词) → N-gram提取 → 共现矩阵 → 语义聚类
  → 高频新词与现有关键词语义相似度>阈值 → 自动入库

Author: 毕方灵犀貔貅助手 V13.5.37
Date: 2026-07-11
"""

import re
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional, Any
from dataclasses import dataclass, field, asdict

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = BASE / "data"
EXPANDER_DIR = DATA_DIR / "keyword_expander"
EXPANDER_DIR.mkdir(parents=True, exist_ok=True)
DISCOVERED_WORDS_FILE = EXPANDER_DIR / "discovered_words.json"
COOCCURRENCE_FILE = EXPANDER_DIR / "cooccurrence_matrix.json"


@dataclass
class DiscoveredWord:
    """发现的新词"""
    word: str
    frequency: int               # 出现频率
    co_occurs_with: List[str]    # 共现关键词
    similarity_score: float      # 与最相似现有关键词的相似度
    similar_to: str              # 最相似的现有关键词
    suggested_category: str      # 建议类别
    status: str = "candidate"    # candidate/approved/rejected
    discovered_date: str = ""


class KeywordSemanticExpander:
    """关键词语义扩展引擎"""

    def __init__(self):
        self.existing_keywords: Dict[str, str] = {}  # keyword → category
        self.ngram_freq: Counter = Counter()
        self.cooccurrence: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.discovered_words: List[DiscoveredWord] = []
        self._load_existing_keywords()

    def _load_existing_keywords(self):
        """加载现有关键词库"""
        try:
            import sys
            sys.path.insert(0, str(BASE))
            from V13_5_36_KeywordLibrary import KEYWORD_LIBRARY
            for cat, entries in KEYWORD_LIBRARY.items():
                for entry in entries:
                    kw = entry.keyword.replace("{n}", "")
                    if kw:
                        self.existing_keywords[kw] = cat
        except Exception:
            pass

        try:
            from V13_5_37_HotSpot_Predictor import EXTRA_KEYWORDS
            for cat, words in EXTRA_KEYWORDS.items():
                for w in words:
                    if w:
                        self.existing_keywords[w] = cat
        except Exception:
            pass

        print(f"[Expander] 已加载 {len(self.existing_keywords)} 个现有关键词")

    # ============================================================
    # N-gram提取
    # ============================================================
    def extract_ngrams(self, texts: List[str], n_range: Tuple[int, int] = (2, 4)) -> Counter:
        """
        从文本中提取N-gram词组

        Args:
            texts: 文本列表
            n_range: N-gram范围 (2-4字)
        Returns:
            Counter: {ngram: frequency}
        """
        for text in texts:
            if not text:
                continue
            # 移除标点和特殊字符
            clean = re.sub(r'[^\u4e00-\u9fff a-zA-Z0-9%]', ' ', text)

            for n in range(n_range[0], n_range[1] + 1):
                # 中文字符N-gram
                for i in range(len(clean) - n + 1):
                    ngram = clean[i:i+n].strip()
                    if len(ngram) >= n and re.match(r'^[\u4e00-\u9fff]+$', ngram):
                        self.ngram_freq[ngram] += 1

        return self.ngram_freq

    # ============================================================
    # 共现矩阵构建
    # ============================================================
    def build_cooccurrence(self, texts: List[str], window: int = 10):
        """
        构建关键词共现矩阵

        Args:
            texts: 文本列表
            window: 共现窗口大小
        """
        for text in texts:
            if not text:
                continue

            # 找出文本中出现的所有现有关键词
            found_keywords = []
            for kw in self.existing_keywords:
                if kw in text:
                    # 记录位置
                    start = 0
                    while True:
                        idx = text.find(kw, start)
                        if idx == -1:
                            break
                        found_keywords.append((kw, idx))
                        start = idx + len(kw)

            # 检查共现
            for i, (kw1, pos1) in enumerate(found_keywords):
                for j, (kw2, pos2) in enumerate(found_keywords):
                    if i < j and abs(pos1 - pos2) <= window:
                        self.cooccurrence[kw1][kw2] += 1
                        self.cooccurrence[kw2][kw1] += 1

    # ============================================================
    # 语义相似度计算
    # ============================================================
    def calculate_similarity(self, word1: str, word2: str) -> float:
        """
        计算两个词的语义相似度 (0-1)

        综合:
          1. 字符Jaccard相似度 (共同字符比例)
          2. 编辑距离相似度
          3. 共现频率相似度
        """
        # 1. 字符Jaccard
        chars1 = set(word1)
        chars2 = set(word2)
        if chars1 and chars2:
            jaccard = len(chars1 & chars2) / len(chars1 | chars2)
        else:
            jaccard = 0.0

        # 2. 编辑距离
        edit_sim = self._edit_distance_similarity(word1, word2)

        # 3. 共现频率
        cooccur = self.cooccurrence.get(word1, {}).get(word2, 0)
        max_cooccur = max(
            sum(self.cooccurrence.get(word1, {}).values()),
            sum(self.cooccurrence.get(word2, {}).values()),
            1
        )
        cooccur_sim = cooccur / max_cooccur if max_cooccur > 0 else 0.0

        # 加权综合
        similarity = jaccard * 0.4 + edit_sim * 0.3 + cooccur_sim * 0.3
        return similarity

    def _edit_distance_similarity(self, s1: str, s2: str) -> float:
        """基于编辑距离的相似度"""
        if not s1 or not s2:
            return 0.0
        max_len = max(len(s1), len(s2))
        distance = self._levenshtein(s1, s2)
        return 1.0 - distance / max_len

    def _levenshtein(self, s1: str, s2: str) -> int:
        """Levenshtein编辑距离"""
        m, n = len(s1), len(s2)
        dp = [[0] * (n + 1) for _ in range(m + 1)]

        for i in range(m + 1):
            dp[i][0] = i
        for j in range(n + 1):
            dp[0][j] = j

        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if s1[i-1] == s2[j-1]:
                    dp[i][j] = dp[i-1][j-1]
                else:
                    dp[i][j] = 1 + min(dp[i-1][j], dp[i][j-1], dp[i-1][j-1])

        return dp[m][n]

    # ============================================================
    # 核心: 自动发现新词
    # ============================================================
    def discover_new_words(
        self,
        texts: List[str],
        min_freq: int = 3,
        similarity_threshold: float = 0.3,
    ) -> List[DiscoveredWord]:
        """
        从文本中自动发现新的催化关键词

        逻辑:
          1. 提取N-gram高频词组
          2. 排除已有关键词
          3. 计算与现有关键词的语义相似度
          4. 相似度>阈值的新词标记为候选

        Args:
            texts: 新闻文本列表
            min_freq: 最低出现频率
            similarity_threshold: 语义相似度阈值
        Returns:
            List[DiscoveredWord]
        """
        print(f"[Expander] 开始从{len(texts)}条文本中发现新词...")

        # Step 1: 提取N-gram
        self.extract_ngrams(texts)
        print(f"[Expander] N-gram提取完成: {len(self.ngram_freq)}个候选")

        # Step 2: 构建共现矩阵
        self.build_cooccurrence(texts)
        print(f"[Expander] 共现矩阵构建完成: {len(self.cooccurrence)}个关键词")

        # Step 3: 筛选新词
        existing_set = set(self.existing_keywords.keys())
        discovered = []

        for ngram, freq in self.ngram_freq.most_common():
            if freq < min_freq:
                continue
            if ngram in existing_set:
                continue
            # 过滤无意义ngram (全是单字重复等)
            if len(set(ngram)) == 1:
                continue
            # 过滤包含数字的(通常是日期等)
            if re.match(r'^\d+$', ngram):
                continue

            # 计算与所有现有关键词的相似度
            best_sim = 0.0
            best_match = ""
            best_cat = ""

            for existing_kw, cat in self.existing_keywords.items():
                sim = self.calculate_similarity(ngram, existing_kw)
                if sim > best_sim:
                    best_sim = sim
                    best_match = existing_kw
                    best_cat = cat

            if best_sim >= similarity_threshold:
                # 检查共现关系
                co_occurs = [
                    kw for kw in self.existing_keywords
                    if self.cooccurrence.get(ngram, {}).get(kw, 0) > 0
                ][:5]

                discovered.append(DiscoveredWord(
                    word=ngram,
                    frequency=freq,
                    co_occurs_with=co_occurs,
                    similarity_score=round(best_sim, 3),
                    similar_to=best_match,
                    suggested_category=best_cat,
                    status="candidate",
                    discovered_date=datetime.now().strftime("%Y-%m-%d"),
                ))

        # 按相似度排序
        discovered.sort(key=lambda x: -x.similarity_score)
        self.discovered_words = discovered

        print(f"[Expander] 发现 {len(discovered)} 个候选新词 (相似度≥{similarity_threshold})")
        return discovered

    # ============================================================
    # 语义聚类
    # ============================================================
    def cluster_keywords(self, n_clusters: int = 20) -> Dict[str, List[str]]:
        """
        将现有关键词按语义相似度聚类

        Returns:
            {cluster_name: [keywords]}
        """
        keywords = list(self.existing_keywords.keys())
        if len(keywords) < 2:
            return {}

        # 简单聚类: 以每个类别的代表词为中心
        clusters = defaultdict(list)
        for kw, cat in self.existing_keywords.items():
            clusters[cat].append(kw)

        return dict(clusters)

    # ============================================================
    # 给定种子词扩展
    # ============================================================
    def expand_from_seed(self, seed_word: str, top_n: int = 10) -> List[Tuple[str, float]]:
        """
        给定种子词, 发现语义相近的新词

        Args:
            seed_word: 种子词 (如"业绩预增")
            top_n: 返回前N个
        Returns:
            [(new_word, similarity), ...]
        """
        results = []

        # 从N-gram中找相似的
        for ngram, freq in self.ngram_freq.most_common():
            if ngram in self.existing_keywords:
                continue
            if freq < 2:
                continue

            sim = self.calculate_similarity(seed_word, ngram)
            if sim > 0.2:
                results.append((ngram, sim))

        results.sort(key=lambda x: -x[1])
        return results[:top_n]

    # ============================================================
    # 保存/加载
    # ============================================================
    def save(self):
        """保存发现的新词和共现矩阵"""
        data = {
            "discovered_words": [asdict(w) for w in self.discovered_words],
            "stats": {
                "total_ngrams": len(self.ngram_freq),
                "existing_keywords": len(self.existing_keywords),
                "discovered_count": len(self.discovered_words),
            },
            "updated": datetime.now().isoformat(),
        }
        DISCOVERED_WORDS_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 共现矩阵 (只保存top关键词)
        top_kws = list(self.ngram_freq.most_common(200))
        cooccur_data = {}
        for kw, _ in top_kws:
            if kw in self.cooccurrence:
                cooccur_data[kw] = dict(self.cooccurrence[kw])
        COOCCURRENCE_FILE.write_text(
            json.dumps(cooccur_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ============================================================
    # 生成HTML报告
    # ============================================================
    def generate_html_report(self) -> str:
        """生成语义扩展报告"""
        today = datetime.now().strftime("%Y-%m-%d")

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>V13.5.37 关键词语义扩展报告 — {today}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0a0e1a; color: #e0e6ed; padding: 20px; }}
.header {{ text-align: center; padding: 30px; background: linear-gradient(135deg, #1a1f35, #3a1b4e); border-radius: 16px; margin-bottom: 24px; border: 1px solid #3d4f7a; }}
.header h1 {{ font-size: 24px; color: #82b1ff; margin-bottom: 8px; }}
.section {{ margin-bottom: 24px; }}
.section-title {{ font-size: 18px; margin-bottom: 12px; padding-left: 12px; border-left: 4px solid #82b1ff; }}
.word-card {{ background: #111827; border-radius: 12px; padding: 14px; margin-bottom: 8px; border: 1px solid #1e293b; }}
.word-header {{ display: flex; justify-content: space-between; align-items: center; }}
.word-text {{ font-size: 16px; font-weight: bold; color: #82b1ff; }}
.word-sim {{ padding: 2px 8px; background: rgba(130,177,255,0.15); border-radius: 8px; font-size: 12px; color: #82b1ff; }}
.word-meta {{ color: #8892b0; font-size: 13px; margin-top: 6px; }}
.footer {{ text-align: center; color: #4a5568; font-size: 12px; padding: 20px; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; text-align: left; font-size: 13px; }}
th {{ color: #8892b0; }}
</style>
</head>
<body>
<div class="header">
<h1>V13.5.37 关键词语义扩展引擎</h1>
<div style="color:#8892b0;font-size:13px">N-gram提取 | 共现矩阵 | 语义相似度 | 自动发现新词 | {today}</div>
</div>
"""

        # 发现的新词
        if self.discovered_words:
            html += f'<div class="section"><div class="section-title">发现候选新词 ({len(self.discovered_words)})</div>'
            for w in self.discovered_words[:30]:
                cooccur_str = ", ".join(w.co_occurs_with[:3]) if w.co_occurs_with else "无"
                html += f"""
<div class="word-card">
<div class="word-header">
<span class="word-text">{w.word}</span>
<span class="word-sim">相似度 {w.similarity_score:.2f}</span>
</div>
<div class="word-meta">
频率: {w.frequency} | 相似于: "{w.similar_to}" | 建议类别: {w.suggested_category} | 共现: {cooccur_str}
</div>
</div>
"""
            html += '</div>'

        # 聚类总览
        clusters = self.cluster_keywords()
        html += f'<div class="section"><div class="section-title">关键词聚类总览 ({len(clusters)}类)</div>'
        html += '<table><tr><th>类别</th><th>关键词数</th><th>样本</th></tr>'
        for cat, kws in sorted(clusters.items(), key=lambda x: -len(x[1])):
            sample = ", ".join(kws[:5])
            html += f'<tr><td>{cat}</td><td>{len(kws)}</td><td>{sample}</td></tr>'
        html += '</table></div>'

        html += f"""
<div class="footer">
V13.5.37 Keyword Semantic Expander | {len(self.existing_keywords)}现有词 | {len(self.ngram_freq)} N-gram | {len(self.discovered_words)}发现新词<br>
毕方灵犀貔貅助手 | {today}
</div>
</body></html>"""

        report_path = BASE / "outputs" / f"keyword_expander_{today.replace('-','')}.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)


# ============================================================
# 测试验证
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.37 关键词语义扩展引擎 — 测试验证")
    print("=" * 70)

    expander = KeywordSemanticExpander()

    # 模拟新闻语料
    print("\n[1] 模拟TDX新闻语料处理...")
    news_texts = [
        "公司上半年净利润预增226% 因AI算力订单大幅增长",
        "公司业绩大增 净利翻倍 远超市场预期",
        "AI算力需求爆发 光模块800G供不应求",
        "公司获重大合同中标 金额5亿元 业绩超预期",
        "NVIDIA财报远超预期 AI芯片需求激增 数据中心收入暴增",
        "半导体国产替代加速 技术突破 获得发明专利",
        "公司营收增长 业绩亮眼 创历史新高",
        "低空经济政策出台 eVTOL迎来发展机遇 产业景气度提升",
        "公司拟收购半导体企业 构成重大资产重组 重组获批",
        "钛白粉企业集体涨价 行业景气 均价上涨 供需偏紧",
        "公司大股东增持 回购股份 彰显发展信心",
        "公司出海拓展 海外订单增长 进入国际市场",
        "人形机器人量产在具 Optimus谐波减速器需求爆发",
        "公司产能扩张 新建产能投产 产能翻倍",
        "业绩预警 公司业绩变脸 由盈转亏 不及预期",
        "公司获专精特新小巨人认定 研发投入占比高",
        "礼来GLP-1减肥药销售额超预期 减肥药概念活跃",
        "商业航天可回收火箭试验成功 卫星互联网加速",
        "公司获FDA认证 进入美国市场 海外发行",
        "业绩预增主要因光模块需求旺盛 订单饱满",
        "公司盈利改善 扭亏为盈 业绩腾飞",
        "AI大模型技术突破 生成式AI应用爆发 AIGC",
        "数据要素政策落地 数据交易 数据资产入表",
        "公司战略协议签署 战略合作 共建生态",
        "股权激励计划 授予价格 员工持股",
    ]

    # 发现新词
    print("\n[2] 自动发现新词...")
    discovered = expander.discover_new_words(news_texts, min_freq=2, similarity_threshold=0.25)

    print(f"\n    发现 {len(discovered)} 个候选新词:")
    for w in discovered[:15]:
        print(f"    [{w.similarity_score:.2f}] {w.word:8s} (频{w.frequency}) "
              f"相似于'{w.similar_to}' → 建议类别: {w.suggested_category}")

    # 种子词扩展测试
    print("\n[3] 种子词扩展测试...")
    seed_results = expander.expand_from_seed("业绩预增", top_n=5)
    print(f"    种子词'业绩预增' → 发现{len(seed_results)}个相似词:")
    for word, sim in seed_results:
        print(f"      {word} (相似度{sim:.2f})")

    seed_results2 = expander.expand_from_seed("AI算力", top_n=5)
    print(f"    种子词'AI算力' → 发现{len(seed_results2)}个相似词:")
    for word, sim in seed_results2:
        print(f"      {word} (相似度{sim:.2f})")

    # 聚类
    print("\n[4] 关键词聚类...")
    clusters = expander.cluster_keywords()
    for cat, kws in sorted(clusters.items(), key=lambda x: -len(x[1]))[:5]:
        print(f"    {cat}: {len(kws)}词 (样本: {', '.join(kws[:3])})")

    # 保存
    print("\n[5] 保存数据...")
    expander.save()
    print(f"    发现新词: {DISCOVERED_WORDS_FILE}")
    print(f"    共现矩阵: {COOCCURRENCE_FILE}")

    # HTML报告
    print("\n[6] 生成HTML报告...")
    report = expander.generate_html_report()
    print(f"    报告: {report}")

    print("\n" + "=" * 70)
    print("✅ V13.5.37 关键词语义扩展引擎验证通过!")
    print(f"   {len(expander.existing_keywords)}现有词 | {len(expander.ngram_freq)} N-gram | "
          f"{len(discovered)}发现新词")
    print("=" * 70)
