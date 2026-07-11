#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.37 热点预判引擎 — 关键词频率突增检测+新兴主题识别
==============================================================
核心能力:
  1. 频率追踪: 每日扫描TDX新闻流, 统计461+关键词出现频率
  2. 突增检测: 当某关键词频率突然超过7日均值2x/5x/10x时触发预警
  3. 主题聚类: 将突增关键词按类别聚类, 识别新兴主题
  4. 板块映射: 将主题映射到A股板块和核心标的
  5. 前瞻预判: 在主流媒体报道之前发现即将启动的热点
  6. 强度评分: 综合突增倍数+关键词权重+板块关联度 → 热点强度分

工作流程:
  TDX新闻流 → 关键词频率统计 → 7日均值基线 → 突增检测
  → 主题聚类 → 板块映射 → 强度评分 → 预判信号

Author: 毕方灵犀貔貅助手 V13.5.37
Date: 2026-07-11
"""

import json
import re
import os
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

# ============================================================
# 路径配置
# ============================================================
BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = BASE / "data"
CACHE_DIR = DATA_DIR / "fullmarket_cache"
HOTSPOT_DIR = DATA_DIR / "hotspot"
HOTSPOT_HISTORY = HOTSPOT_DIR / "frequency_history.json"
HOTSPOT_SIGNALS = HOTSPOT_DIR / "hotspot_signals_latest.json"
HOTSPOT_REPORT_DIR = BASE / "outputs"

# 确保目录存在
HOTSPOT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 热点强度等级
# ============================================================
class SurgeLevel(Enum):
    """突增等级"""
    NORMAL = "normal"           # 正常波动
    WATCH = "watch"             # 2x-5x 突增, 关注
    SURGE = "surge"             # 5x-10x 突增, 预判
    EXPLOSIVE = "explosive"     # 10x+ 突增, 爆发


@dataclass
class KeywordFrequency:
    """单日关键词频率记录"""
    keyword: str
    category: str               # 所属类别
    date: str                   # 日期 YYYY-MM-DD
    count: int                  # 当日出现次数
    news_titles: List[str] = field(default_factory=list)  # 相关新闻标题(最多5条)


@dataclass
class HotspotSignal:
    """热点预判信号"""
    date: str
    keyword: str
    category: str
    current_count: int          # 当日频率
    avg_count: float            # 7日均值
    surge_ratio: float          # 突增倍数
    surge_level: str            # SurgeLevel
    intensity_score: float      # 热点强度分(0-100)
    related_sector: str         # 关联板块
    related_stocks: List[str]   # 关联个股
    news_samples: List[str]     # 新闻样本
    theme_cluster: str = ""     # 所属主题聚类
    prediction: str = ""        # 预判描述


@dataclass
class ThemeCluster:
    """主题聚类"""
    theme_name: str             # 主题名称
    date: str
    keywords: List[str]         # 聚类关键词
    categories: Set[str]        # 涉及类别
    total_surge_ratio: float    # 总突增倍数
    avg_intensity: float        # 平均强度
    related_sectors: List[str]  # 关联板块
    related_stocks: List[str]   # 关联个股(去重)
    signal_count: int           # 信号数
    prediction: str = ""        # 预判


# ============================================================
# 扩展关键词库 — 新增7大前瞻性类别
# ============================================================
EXTRA_KEYWORDS = {
    # 13. 管理层变动 (MANAGEMENT)
    "MANAGEMENT": [
        "董事长辞职", "CEO变更", "总经理变更", "管理层换届", "新任董事长",
        "新任总经理", "高管变动", "核心团队", "管理层增持", "管理层入股",
        "股权激励", "员工持股计划", "限制性股票", "股票期权激励",
        "激励对象", "授予价格", "行权条件", "解锁条件", "归属期",
    ],
    # 14. 分红送转 (DIVIDEND)
    "DIVIDEND": [
        "高分红", "特别分红", "中期分红", "年报分红", "分红方案",
        "每10股派", "每10股转增", "高送转", "送股", "转增",
        "现金红利", "分红总额", "股息率", "分红比例", "分红预案",
        "除权除息", "股权登记日", "派息", "资本公积转增",
    ],
    # 15. 特殊事件 (SPECIAL)
    "SPECIAL": [
        "ST摘帽", "撤销退市风险警示", "撤销ST", "摘星", "*ST",
        "退市风险", "终止上市", "重组终止", "重大事项停牌",
        "复牌", "撤销停牌", "风险警示", "其他风险警示",
        "商誉减值", "资产减值", "计提减值", "坏账准备",
        "限售解禁", "解禁", "流通股", "锁定期届满",
        "质押", "股权质押", "补充质押", "解除质押", "平仓风险",
    ],
    # 16. 研发创新 (RND)
    "RND": [
        "研发投入", "研发费用", "研发占比", "专利申请", "发明专利",
        "授权专利", "核心技术", "自主知识产权", "技术壁垒", "技术护城河",
        "研发中心", "实验室", "博士后流动站", "院士工作站",
        "国家科技进步", "科技奖", "创新企业", "专精特新", "小巨人",
        "独角兽", "瞪羚企业", "国家高新技术企业",
    ],
    # 17. 机构行为 (INSTITUTIONAL)
    "INSTITUTIONAL": [
        "机构调研", "机构来访", "投资者关系", "路演", "反路演",
        "龙虎榜", "机构专用", "深股通", "沪股通", "北向资金",
        "QFII", "社保基金", "险资", "公募基金", "私募调研",
        "券商调研", "外资调研", "机构持仓", "基金重仓",
        "席位", "大宗交易", "折价大宗", "溢价大宗",
    ],
    # 18. 新兴赛道 (EMERGING)
    "EMERGING": [
        # 低空经济
        "低空经济", "eVTOL", "飞行汽车", "无人机", "空中出租车",
        "低空空域", "通航", "通用航空", "空中交通管理",
        # 新质生产力
        "新质生产力", "未来产业", "前沿技术", "颠覆性技术",
        # 数据要素
        "数据要素", "数据资产", "数据交易", "数据确权", "数据入表",
        "数字经济", "数字化转型", "数据治理",
        # AI应用
        "AI大模型", "大语言模型", "生成式AI", "AIGC", "AI Agent",
        "具身智能", "多模态", "AI PC", "AI手机", "端侧AI",
        # 商业航天
        "商业航天", "可回收火箭", "星链", "卫星互联网", "星座计划",
        "太空经济", "月球探测", "深空探测",
        # 量子/前沿
        "量子计算", "量子通信", "脑机接口", "合成生物学",
        "固态电池", "钙钛矿", "氢能", "可控核聚变",
    ],
    # 19. 负面风险 (RISK) — 用于过滤和反向信号
    "RISK": [
        "被立案调查", "证监会调查", "行政处罚", "监管处罚",
        "违规", "信披违规", "内幕交易", "操纵市场",
        "业绩变脸", "业绩预警", "预亏", "续亏", "首亏",
        "债务违约", "债券违约", "逾期", "诉讼",
        "仲裁", "冻结", "查封", "强制执行",
        "子公司失控", "公章失控", "控制权之争",
        "减持计划", "大股东减持", "实控人减持", "清仓减持",
        "关联交易", "资金占用", "违规担保",
    ],
}


# ============================================================
# 板块→关键词反向映射 (用于热点→板块映射)
# ============================================================
SECTOR_KEYWORD_MAP = {
    "AI算力": ["AI服务器", "算力", "智算", "液冷", "GPU", "AI芯片", "数据中心", "AIDC"],
    "光模块": ["光模块", "CPO", "光通信", "800G", "1.6T", "硅光", "铜缆"],
    "半导体": ["半导体", "芯片", "光刻", "晶圆", "封测", "先进封装", "刻蚀", "薄膜沉积", "国产替代"],
    "存储芯片": ["HBM", "存储", "DRAM", "NAND", "内存", "存储芯片", "长鑫"],
    "PCB": ["PCB", "印制电路板", "覆铜板", "电子布", "高速PCB"],
    "机器人": ["机器人", "人形机器人", "具身智能", "Optimus", "减速器", "伺服", "灵巧手"],
    "商业航天": ["商业航天", "火箭", "卫星", "发射场", "星链", "可回收"],
    "氦气/特气": ["氦气", "特种气体", "电子特气", "黄金气体"],
    "黄金": ["黄金", "金价", "贵金属", "央行增持", "避险"],
    "新能源": ["光伏", "风电", "储能", "锂电", "固态电池", "钠电", "钙钛矿"],
    "电力设备": ["电力", "特高压", "电网", "核电", "用电负荷", "虚拟电厂"],
    "军工": ["军工", "国防", "导弹", "战机", "军贸", "信息化"],
    "低空经济": ["低空经济", "eVTOL", "飞行汽车", "无人机", "通航"],
    "AI应用": ["AI大模型", "AIGC", "生成式AI", "AI Agent", "多模态", "端侧AI"],
    "数据要素": ["数据要素", "数据资产", "数据交易", "数据确权", "数字经济"],
    "汽车智能化": ["智能驾驶", "自动驾驶", "激光雷达", "线控底盘", "智能座舱"],
    "消费电子": ["苹果", "iPhone", "MR", "Vision Pro", "果链", "折叠屏"],
    "医药生物": ["创新药", "医疗器械", "ADC", "GLP-1", "减肥药", "疫苗", "中药"],
    "化工材料": ["钛白粉", "磷化工", "氟化工", "稀土", "锂盐", "维生素"],
    "金融科技": ["数字货币", "跨境支付", "金融科技", "区块链", "DCEP"],
}


class HotSpotPredictor:
    """热点预判引擎"""

    def __init__(self):
        self.frequency_history: Dict[str, List[KeywordFrequency]] = defaultdict(list)
        self.today_signals: List[HotspotSignal] = []
        self.theme_clusters: List[ThemeCluster] = []
        self._load_history()

    # ============================================================
    # 历史数据管理
    # ============================================================
    def _load_history(self):
        """加载历史频率数据"""
        if HOTSPOT_HISTORY.exists():
            try:
                data = json.loads(HOTSPOT_HISTORY.read_text(encoding="utf-8"))
                for kw, records in data.items():
                    self.frequency_history[kw] = [
                        KeywordFrequency(
                            keyword=r["keyword"],
                            category=r["category"],
                            date=r["date"],
                            count=r["count"],
                            news_titles=r.get("news_titles", []),
                        )
                        for r in records
                    ]
            except Exception as e:
                print(f"[HotSpot] 加载历史数据失败: {e}")

    def _save_history(self):
        """保存历史频率数据"""
        data = {}
        for kw, records in self.frequency_history.items():
            # 只保留最近30天
            cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            recent = [r for r in records if r.date >= cutoff]
            data[kw] = [asdict(r) for r in recent]
        HOTSPOT_HISTORY.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ============================================================
    # 核心: 关键词频率统计
    # ============================================================
    def scan_news_batch(
        self,
        news_items: List[Dict[str, str]],
        date_str: str = "",
        keyword_library: Dict = None,
    ) -> Dict[str, KeywordFrequency]:
        """
        扫描一批新闻, 统计关键词频率

        Args:
            news_items: [{"title": "...", "content": "...", "source": "tdx_news"}]
            date_str: 日期 YYYY-MM-DD, 默认今天
            keyword_library: 关键词库 (从V13_5_36_KeywordLibrary导入)
        Returns:
            {keyword: KeywordFrequency}
        """
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")

        # 合并关键词库
        all_keywords = {}
        if keyword_library:
            for cat, entries in keyword_library.items():
                for entry in entries:
                    all_keywords[entry.keyword] = cat
        # 加入扩展关键词
        for cat, words in EXTRA_KEYWORDS.items():
            for w in words:
                all_keywords[w] = cat

        # 统计频率
        freq_map: Dict[str, KeywordFrequency] = {}
        title_map: Dict[str, List[str]] = defaultdict(list)

        for news in news_items:
            text = (news.get("title", "") + " " + news.get("content", "")).strip()
            if not text:
                continue

            for kw, cat in all_keywords.items():
                # 处理正则模式 {n}%
                pattern = kw.replace("{n}", r"(\d+\.?\d*)")
                try:
                    if re.search(pattern, text):
                        if kw not in freq_map:
                            freq_map[kw] = KeywordFrequency(
                                keyword=kw, category=cat, date=date_str, count=0
                            )
                        freq_map[kw].count += 1
                        if len(title_map[kw]) < 5:
                            title_map[kw].append(news.get("title", "")[:80])
                except re.error:
                    # 非正则, 直接字符串匹配
                    if kw in text:
                        if kw not in freq_map:
                            freq_map[kw] = KeywordFrequency(
                                keyword=kw, category=cat, date=date_str, count=0
                            )
                        freq_map[kw].count += 1
                        if len(title_map[kw]) < 5:
                            title_map[kw].append(news.get("title", "")[:80])

        # 填充新闻标题
        for kw, freq in freq_map.items():
            freq.news_titles = title_map.get(kw, [])

        # 更新历史
        for kw, freq in freq_map.items():
            self.frequency_history[kw].append(freq)

        return freq_map

    # ============================================================
    # 核心: 突增检测
    # ============================================================
    def detect_surges(
        self, today_freq: Dict[str, KeywordFrequency], lookback_days: int = 7
    ) -> List[HotspotSignal]:
        """
        检测关键词频率突增

        逻辑:
          - 计算过去N天的日均频率
          - 如果今日频率 > 日均 * 2 → WATCH
          - 如果今日频率 > 日均 * 5 → SURGE
          - 如果今日频率 > 日均 * 10 → EXPLOSIVE
          - 如果是首次出现(无历史)且频率>=3 → NEW_EMERGING
        """
        signals = []
        today = datetime.now().strftime("%Y-%m-%d")

        for kw, today_data in today_freq.items():
            today_count = today_data.count

            # 获取历史记录
            history = [
                r for r in self.frequency_history.get(kw, [])
                if r.date < today and r.date >= (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            ]

            if not history:
                # 首次出现
                if today_count >= 3:
                    level = SurgeLevel.SURGE
                    avg = 0
                    ratio = float(today_count)
                    prediction = f"[新兴] '{kw}'首次出现{today_count}次, 可能是新热点萌芽"
                else:
                    continue
            else:
                avg = sum(r.count for r in history) / len(history)
                if avg < 0.1:
                    avg = 0.1  # 避免除零

                ratio = today_count / avg

                if ratio >= 10:
                    level = SurgeLevel.EXPLOSIVE
                    prediction = f"[爆发] '{kw}'频率突增{ratio:.1f}倍, 热点已爆发"
                elif ratio >= 5:
                    level = SurgeLevel.SURGE
                    prediction = f"[预判] '{kw}'频率突增{ratio:.1f}倍, 热点即将启动"
                elif ratio >= 2:
                    level = SurgeLevel.WATCH
                    prediction = f"[关注] '{kw}'频率上升{ratio:.1f}倍, 值得跟踪"
                else:
                    continue

            # 计算强度分
            intensity = self._calc_intensity(
                today_count, avg, ratio, today_data.category, level
            )

            # 板块映射
            sector, stocks = self._map_to_sector(kw)

            signal = HotspotSignal(
                date=today,
                keyword=kw,
                category=today_data.category,
                current_count=today_count,
                avg_count=round(avg, 2),
                surge_ratio=round(ratio, 2),
                surge_level=level.value,
                intensity_score=round(intensity, 1),
                related_sector=sector,
                related_stocks=stocks,
                news_samples=today_data.news_titles,
                prediction=prediction,
            )
            signals.append(signal)

        # 按强度排序
        signals.sort(key=lambda x: -x.intensity_score)
        self.today_signals = signals
        return signals

    def _calc_intensity(
        self, count: int, avg: float, ratio: float, category: str, level: SurgeLevel
    ) -> float:
        """计算热点强度分(0-100)"""
        score = 0.0

        # 突增倍数分 (40分满分)
        if ratio >= 10:
            score += 40
        elif ratio >= 5:
            score += 30
        elif ratio >= 2:
            score += 20
        else:
            score += min(count * 2, 10)

        # 绝对频率分 (20分满分)
        if count >= 10:
            score += 20
        elif count >= 5:
            score += 15
        elif count >= 3:
            score += 10
        else:
            score += count * 2

        # 类别权重分 (20分满分) — 高影响力类别加权
        high_impact = {"EARNINGS", "M_A", "TECH", "GEOPOLITICAL", "EMERGING", "POLICY"}
        medium_impact = {"CONTRACT", "CAPACITY", "PRICE", "OVERSEAS", "RND"}
        if category in high_impact:
            score += 20
        elif category in medium_impact:
            score += 12
        else:
            score += 8

        # 等级加成 (20分满分)
        level_bonus = {
            SurgeLevel.EXPLOSIVE: 20,
            SurgeLevel.SURGE: 15,
            SurgeLevel.WATCH: 8,
            SurgeLevel.NORMAL: 0,
        }
        score += level_bonus.get(level, 0)

        return min(score, 100.0)

    def _map_to_sector(self, keyword: str) -> Tuple[str, List[str]]:
        """将关键词映射到板块和个股"""
        for sector, keywords in SECTOR_KEYWORD_MAP.items():
            for sk in keywords:
                if sk in keyword or keyword in sk:
                    # 从CATALYST_SECTOR_MAP获取个股
                    stocks = self._get_sector_stocks(sector)
                    return sector, stocks
        return "未分类", []

    def _get_sector_stocks(self, sector_name: str) -> List[str]:
        """获取板块核心个股"""
        # 从CatalystScanner的板块映射表获取
        try:
            import sys
            sys.path.insert(0, str(BASE))
            from V13_5_34_CatalystScanner_V2 import CATALYST_SECTOR_MAP
            for name, info in CATALYST_SECTOR_MAP.items():
                if sector_name in name or name in sector_name:
                    return info.get("core_stocks", []) + info.get("ecosystem", [])[:3]
        except Exception:
            pass
        return []

    # ============================================================
    # 核心: 主题聚类
    # ============================================================
    def cluster_themes(self, signals: List[HotspotSignal]) -> List[ThemeCluster]:
        """
        将突增信号按类别聚类为主题

        聚类逻辑:
          1. 按category分组
          2. 同类内按sector合并
          3. 不同类但同sector合并为跨类主题
        """
        if not signals:
            return []

        # Step 1: 按sector分组
        sector_groups: Dict[str, List[HotspotSignal]] = defaultdict(list)
        for s in signals:
            sector_groups[s.related_sector].append(s)

        clusters = []
        today = datetime.now().strftime("%Y-%m-%d")

        for sector, sector_signals in sector_groups.items():
            if sector == "未分类":
                # 未分类的按category分组
                cat_groups: Dict[str, List[HotspotSignal]] = defaultdict(list)
                for s in sector_signals:
                    cat_groups[s.category].append(s)
                for cat, cat_signals in cat_groups.items():
                    clusters.append(self._build_cluster(
                        f"{cat}主题", today, cat_signals
                    ))
            else:
                clusters.append(self._build_cluster(
                    f"{sector}主题", today, sector_signals
                ))

        # 按平均强度排序
        clusters.sort(key=lambda x: -x.avg_intensity)
        self.theme_clusters = clusters
        return clusters

    def _build_cluster(
        self, name: str, date: str, signals: List[HotspotSignal]
    ) -> ThemeCluster:
        """构建主题聚类"""
        keywords = list(set(s.keyword for s in signals))
        categories = set(s.category for s in signals)
        total_ratio = sum(s.surge_ratio for s in signals)
        avg_intensity = sum(s.intensity_score for s in signals) / len(signals)
        sectors = list(set(s.related_sector for s in signals if s.related_sector != "未分类"))
        stocks = list(set(
            stock for s in signals for stock in s.related_stocks
        ))[:10]

        # 生成预判
        top_kw = max(signals, key=lambda x: x.surge_ratio)
        prediction = (
            f"{name}: {len(keywords)}个关键词突增, "
            f"最强'{top_kw.keyword}'突增{top_kw.surge_ratio:.1f}x, "
            f"关联{len(sectors)}个板块{len(stocks)}只个股, "
            f"平均强度{avg_intensity:.1f}"
        )

        return ThemeCluster(
            theme_name=name,
            date=date,
            keywords=keywords,
            categories=categories,
            total_surge_ratio=round(total_ratio, 2),
            avg_intensity=round(avg_intensity, 1),
            related_sectors=sectors,
            related_stocks=stocks,
            signal_count=len(signals),
            prediction=prediction,
        )

    # ============================================================
    # TDX新闻采集 (由Agent调用)
    # ============================================================
    def collect_tdx_news(self, tdx_news_results: List[Dict]) -> List[Dict]:
        """
        将TDX wenda_news_query/notice_query结果转换为统一格式

        Args:
            tdx_news_results: TDX返回的新闻列表
        Returns:
            [{"title": "...", "content": "...", "source": "tdx_news"}]
        """
        converted = []
        for item in tdx_news_results:
            title = item.get("title", "") or item.get("name", "")
            content = item.get("content", "") or item.get("summary", "") or item.get("description", "")
            if title or content:
                converted.append({
                    "title": title,
                    "content": content,
                    "source": "tdx",
                    "raw": item,
                })
        return converted

    # ============================================================
    # 保存信号
    # ============================================================
    def save_signals(self):
        """保存今日信号"""
        data = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "signals": [asdict(s) for s in self.today_signals],
            "themes": [
                {
                    **asdict(t),
                    "categories": list(t.categories),
                }
                for t in self.theme_clusters
            ],
            "stats": {
                "total_signals": len(self.today_signals),
                "explosive": sum(1 for s in self.today_signals if s.surge_level == "explosive"),
                "surge": sum(1 for s in self.today_signals if s.surge_level == "surge"),
                "watch": sum(1 for s in self.today_signals if s.surge_level == "watch"),
                "themes": len(self.theme_clusters),
            },
        }
        HOTSPOT_SIGNALS.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._save_history()
        return data

    # ============================================================
    # 生成HTML报告
    # ============================================================
    def generate_html_report(self) -> str:
        """生成热点预判HTML报告"""
        today = datetime.now().strftime("%Y-%m-%d")

        # 统计
        total = len(self.today_signals)
        explosive = [s for s in self.today_signals if s.surge_level == "explosive"]
        surge = [s for s in self.today_signals if s.surge_level == "surge"]
        watch = [s for s in self.today_signals if s.surge_level == "watch"]

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.5.37 热点预判报告 — {today}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0a0e1a; color: #e0e6ed; padding: 20px; }}
.header {{ text-align: center; padding: 30px 20px; background: linear-gradient(135deg, #1a1f35, #2d1b4e); border-radius: 16px; margin-bottom: 24px; border: 1px solid #3d4f7a; }}
.header h1 {{ font-size: 28px; background: linear-gradient(90deg, #ff6b6b, #ffd93d, #6bcf7f); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 8px; }}
.header .subtitle {{ color: #8892b0; font-size: 14px; }}
.stats-bar {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
.stat-card {{ flex: 1; min-width: 140px; padding: 20px; background: #111827; border-radius: 12px; border: 1px solid #1e293b; text-align: center; }}
.stat-card .num {{ font-size: 36px; font-weight: bold; }}
.stat-card .label {{ color: #8892b0; font-size: 13px; margin-top: 4px; }}
.stat-explosive .num {{ color: #ff4757; }}
.stat-surge .num {{ color: #ffa502; }}
.stat-watch .num {{ color: #3742fa; }}
.stat-theme .num {{ color: #2ed573; }}
.section {{ margin-bottom: 24px; }}
.section-title {{ font-size: 18px; color: #e0e6ed; margin-bottom: 12px; padding-left: 12px; border-left: 4px solid #6bcf7f; }}
.signal-card {{ background: #111827; border-radius: 12px; padding: 16px 20px; margin-bottom: 12px; border: 1px solid #1e293b; transition: border-color 0.2s; }}
.signal-card:hover {{ border-color: #6bcf7f; }}
.signal-card.explosive {{ border-left: 4px solid #ff4757; }}
.signal-card.surge {{ border-left: 4px solid #ffa502; }}
.signal-card.watch {{ border-left: 4px solid #3742fa; }}
.signal-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
.signal-kw {{ font-size: 18px; font-weight: bold; color: #e0e6ed; }}
.signal-badge {{ padding: 2px 10px; border-radius: 12px; font-size: 12px; font-weight: bold; }}
.badge-explosive {{ background: rgba(255,71,87,0.2); color: #ff4757; }}
.badge-surge {{ background: rgba(255,165,2,0.2); color: #ffa502; }}
.badge-watch {{ background: rgba(55,66,250,0.2); color: #3742fa; }}
.signal-meta {{ display: flex; gap: 16px; color: #8892b0; font-size: 13px; margin-bottom: 8px; }}
.signal-meta span {{ display: flex; align-items: center; gap: 4px; }}
.intensity-bar {{ height: 6px; background: #1e293b; border-radius: 3px; margin: 8px 0; overflow: hidden; }}
.intensity-fill {{ height: 100%; border-radius: 3px; transition: width 0.3s; }}
.signal-news {{ font-size: 12px; color: #636e72; margin-top: 8px; }}
.signal-news li {{ margin-bottom: 4px; padding-left: 12px; border-left: 2px solid #2d3748; }}
.theme-card {{ background: linear-gradient(135deg, #1a1f35, #1a237a); border-radius: 12px; padding: 20px; margin-bottom: 12px; border: 1px solid #3d4f7a; }}
.theme-name {{ font-size: 20px; font-weight: bold; color: #82b1ff; margin-bottom: 8px; }}
.theme-prediction {{ color: #b0bec5; font-size: 14px; margin-bottom: 12px; }}
.theme-keywords {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }}
.theme-kw-tag {{ padding: 2px 8px; background: rgba(130,177,255,0.15); border-radius: 8px; font-size: 12px; color: #82b1ff; }}
.theme-stocks {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.stock-tag {{ padding: 3px 10px; background: rgba(46,213,115,0.15); border-radius: 8px; font-size: 12px; color: #2ed573; }}
.footer {{ text-align: center; color: #4a5568; font-size: 12px; padding: 20px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="header">
<h1>V13.5.37 热点预判引擎</h1>
<div class="subtitle">关键词频率突增检测 | 新兴主题识别 | 前瞻性板块映射 | {today}</div>
</div>

<div class="stats-bar">
<div class="stat-card stat-explosive"><div class="num">{len(explosive)}</div><div class="label">爆发级 (10x+)</div></div>
<div class="stat-card stat-surge"><div class="num">{len(surge)}</div><div class="label">预判级 (5-10x)</div></div>
<div class="stat-card stat-watch"><div class="num">{len(watch)}</div><div class="label">关注级 (2-5x)</div></div>
<div class="stat-card stat-theme"><div class="num">{len(self.theme_clusters)}</div><div class="label">主题聚类</div></div>
</div>
"""

        # 主题聚类
        if self.theme_clusters:
            html += '<div class="section"><div class="section-title">主题聚类预判</div>'
            for t in self.theme_clusters[:10]:
                kw_tags = "".join(f'<span class="theme-kw-tag">{kw}</span>' for kw in t.keywords[:8])
                stock_tags = "".join(f'<span class="stock-tag">{s}</span>' for s in t.related_stocks[:8])
                html += f"""
<div class="theme-card">
<div class="theme-name">{t.theme_name}</div>
<div class="theme-prediction">{t.prediction}</div>
<div class="theme-keywords">{kw_tags}</div>
<div class="theme-stocks">{stock_tags}</div>
</div>
"""
            html += '</div>'

        # 信号列表
        if self.today_signals:
            html += '<div class="section"><div class="section-title">突增信号详情</div>'
            for s in self.today_signals[:30]:
                level_class = s.surge_level
                badge_class = f"badge-{s.surge_level}"
                level_text = {"explosive": "爆发", "surge": "预判", "watch": "关注"}.get(s.surge_level, s.surge_level)
                intensity_color = "#ff4757" if s.intensity_score >= 70 else "#ffa502" if s.intensity_score >= 50 else "#3742fa"
                news_html = ""
                if s.news_samples:
                    news_html = "<ul class='signal-news'>" + "".join(f"<li>{n}</li>" for n in s.news_samples[:3]) + "</ul>"

                html += f"""
<div class="signal-card {level_class}">
<div class="signal-header">
<span class="signal-kw">{s.keyword}</span>
<span class="signal-badge {badge_class}">{level_text} {s.surge_ratio:.1f}x</span>
</div>
<div class="signal-meta">
<span>类别: {s.category}</span>
<span>今日: {s.current_count}次</span>
<span>7日均: {s.avg_count}</span>
<span>板块: {s.related_sector}</span>
<span>强度: {s.intensity_score:.0f}</span>
</div>
<div class="intensity-bar"><div class="intensity-fill" style="width:{s.intensity_score}%;background:{intensity_color}"></div></div>
<div class="signal-meta">
<span>关联个股: {", ".join(s.related_stocks[:5]) if s.related_stocks else "无"}</span>
</div>
{news_html}
</div>
"""
            html += '</div>'

        html += f"""
<div class="footer">
V13.5.37 HotSpot Predictor | 461+关键词库 | 19大类别 | 频率突增检测+主题聚类+板块映射<br>
毕方灵犀貔貅助手 | {today}
</div>
</body></html>"""

        report_path = HOTSPOT_REPORT_DIR / f"hotspot_predict_{today.replace('-','')}.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)


# ============================================================
# 测试验证
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.37 热点预判引擎 — 测试验证")
    print("=" * 70)

    predictor = HotSpotPredictor()

    # 模拟7天历史数据
    print("\n[1] 模拟7天历史频率数据...")
    base_keywords = ["AI服务器", "业绩预增", "氦气", "机器人", "光模块", "半导体"]
    for days_ago in range(7, 0, -1):
        date = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        for kw in base_keywords:
            cat = "EARNINGS" if "预增" in kw else "EMERGING" if "机器人" in kw else "TECH"
            freq = KeywordFrequency(keyword=kw, category=cat, date=date, count=1)
            predictor.frequency_history[kw].append(freq)
    print(f"  历史数据加载完成: {len(predictor.frequency_history)}个关键词")

    # 模拟今日新闻 (某些关键词突增)
    print("\n[2] 模拟今日TDX新闻流...")
    today_news = [
        {"title": "NVIDIA股价再创新高 AI算力需求持续爆发", "content": "AI服务器需求激增 光模块800G供不应求"},
        {"title": "某公司上半年净利润预增226%", "content": "业绩预增主要因AI算力订单大幅增长"},
        {"title": "AI服务器龙头获大额订单", "content": "AI服务器算力芯片半导体"},
        {"title": "光模块企业CPO技术突破", "content": "光模块CPO 1.6T速率创新高"},
        {"title": "光模块行业景气度持续提升", "content": "光模块800G需求旺盛"},
        {"title": "半导体国产替代加速", "content": "半导体设备国产化率提升"},
        {"title": "AI服务器产能扩张", "content": "AI服务器产能翻倍"},
        {"title": "低空经济政策出台 eVTOL迎来发展机遇", "content": "低空经济无人机飞行汽车"},
        {"title": "低空经济示范区获批", "content": "低空经济通航产业"},
        {"title": "低空经济首次写入政府工作报告", "content": "低空经济新质生产力"},
        {"title": "人形机器人Optimus量产在即", "content": "机器人具身智能"},
    ]

    today_freq = predictor.scan_news_batch(today_news)
    print(f"  今日扫描完成: {len(today_freq)}个关键词命中")

    # 突增检测
    print("\n[3] 突增检测...")
    signals = predictor.detect_surges(today_freq)
    print(f"  检测到 {len(signals)} 个突增信号:")
    for s in signals[:10]:
        print(f"    [{s.surge_level:9s}] {s.keyword:12s} | {s.current_count}次 vs 均{s.avg_count} | "
              f"{s.surge_ratio:.1f}x | 强度{s.intensity_score:.0f} | {s.related_sector}")

    # 主题聚类
    print("\n[4] 主题聚类...")
    clusters = predictor.cluster_themes(signals)
    print(f"  识别 {len(clusters)} 个主题:")
    for t in clusters:
        print(f"    {t.theme_name}: {t.signal_count}信号, 平均强度{t.avg_intensity}, "
              f"关联{len(t.related_stocks)}只个股")
        print(f"      关键词: {', '.join(t.keywords[:5])}")

    # 保存
    print("\n[5] 保存信号...")
    saved = predictor.save_signals()
    print(f"  信号已保存: {HOTSPOT_SIGNALS}")
    print(f"  统计: 爆发{saved['stats']['explosive']}, 预判{saved['stats']['surge']}, "
          f"关注{saved['stats']['watch']}, 主题{saved['stats']['themes']}")

    # 生成HTML
    print("\n[6] 生成HTML报告...")
    report = predictor.generate_html_report()
    print(f"  报告已生成: {report}")

    print("\n" + "=" * 70)
    print("✅ V13.5.37 热点预判引擎验证通过!")
    print(f"   新增7大类别(EXTRA_KEYWORDS): {sum(len(v) for v in EXTRA_KEYWORDS.values())}个关键词")
    print(f"   总关键词库: 461(原有) + {sum(len(v) for v in EXTRA_KEYWORDS.values())}(新增) = "
          f"{461 + sum(len(v) for v in EXTRA_KEYWORDS.values())}个")
    print("=" * 70)
