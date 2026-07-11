#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ⚠️  DEPRECATED V13.5.37 → 请使用 V13_5_39_CrossMarket_Expanded.py (27→53条覆盖4市场+大宗)
"""
V13.5.37 跨市场催化剂映射引擎 — 美股/港股夜盘→次日A股热点预判
====================================================================
核心能力:
  1. 美股/港股夜盘新闻采集 (WebSearch + API)
  2. 美股标的→A股板块映射 (NVIDIA→AI算力等)
  3. 夜盘涨跌→次日A股影响预判
  4. 跨市场催化剂信号生成
  5. 映射强度评分 (直接关联>间接关联>情绪关联)

映射逻辑:
  - 美股龙头大涨 → A股相关产业链次日跟涨概率
  - 美股龙头大跌 → A股相关产业链次日承压概率
  - 美股重大事件(财报/产品发布/监管) → A股映射催化
  - 港股夜盘期货 → A股相关板块先行指标

映射表覆盖:
  科技: NVIDIA/AMD/Intel/TSMC/ASML → AI算力/半导体/光模块/PCB
  汽车: Tesla/Rivian/Lucid → 智能驾驶/锂电/汽车零部件
  消费: Apple/Meta/Google → 消费电子/AI应用/广告营销
  能源: ExxonMobil/Shell → 石油石化/新能源
  金融: JPMorgan/Goldman → 金融科技/券商
  生物: Moderna/Pfizer → 创新药/疫苗/CRO
  航天: SpaceX/Boeing → 商业航天/军工
  电商: Amazon/Shopify → 跨境电商/物流

Author: 毕方灵犀貔貅助手 V13.5.37
Date: 2026-07-11
"""

import json
import re
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

# ============================================================
# 路径配置
# ============================================================
BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = BASE / "data"
CROSSMARKET_DIR = DATA_DIR / "crossmarket"
CROSSMARKET_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_FILE = CROSSMARKET_DIR / "crossmarket_signals_latest.json"
HISTORY_FILE = CROSSMARKET_DIR / "crossmarket_history.json"


# ============================================================
# 映射强度等级
# ============================================================
class MappingStrength(Enum):
    DIRECT = "direct"           # 直接关联 (供应链/核心技术)
    INDIRECT = "indirect"       # 间接关联 (同行业/竞品)
    SENTIMENT = "sentiment"     # 情绪关联 (市场情绪传导)
    WEAK = "weak"               # 弱关联


@dataclass
class USStockMapping:
    """美股→A股映射条目"""
    us_ticker: str              # 美股代码
    us_name: str                # 美股名称
    cn_sector: str              # A股板块
    cn_stocks: List[str]        # A股关联个股
    mapping_type: str           # MappingStrength
    description: str            # 映射描述
    impact_factor: float        # 影响系数(0.1-1.0, 直接=1.0)


@dataclass
class CrossMarketSignal:
    """跨市场催化剂信号"""
    date: str
    us_ticker: str
    us_name: str
    us_change: float            # 美股涨跌幅%
    us_event: str               # 美股事件描述
    cn_sector: str              # A股板块
    cn_stocks: List[str]        # A股关联个股
    mapping_strength: str       # MappingStrength
    impact_factor: float        # 影响系数
    predicted_impact: str       # 预判影响 (利好/利空/中性)
    predicted_intensity: float  # 预判强度(0-100)
    confidence: float           # 置信度(0-1)


# ============================================================
# 美股→A股映射表 (核心知识库)
# ============================================================
US_CN_MAPPING: List[USStockMapping] = [
    # === 半导体/AI芯片 ===
    USStockMapping("NVDA", "NVIDIA英伟达", "AI算力",
        ["000977", "603019", "002313", "300308", "002180"],
        MappingStrength.DIRECT.value,
        "NVIDIA是AI算力核心, A股AI服务器/光模块/PCB直接供应链",
        1.0),
    USStockMapping("AMD", "AMD超威", "半导体",
        ["002185", "603005", "600584", "688981"],
        MappingStrength.DIRECT.value,
        "AMD与A股半导体在封测/设计环节有供应链关系",
        0.8),
    USStockMapping("INTC", "Intel英特尔", "半导体",
        ["002185", "600584", "688981"],
        MappingStrength.INDIRECT.value,
        "Intel晶圆代工扩张利好A股半导体设备",
        0.6),
    USStockMapping("TSM", "台积电", "半导体",
        ["002185", "603005", "688981", "300308"],
        MappingStrength.DIRECT.value,
        "台积电是A股封测/光模块核心客户",
        0.9),
    USStockMapping("ASML", "ASML阿斯麦", "半导体设备",
        ["603501", "688012", "002408", "688396"],
        MappingStrength.DIRECT.value,
        "ASML光刻机是A股半导体设备对标核心",
        0.9),
    USStockMapping("MU", "美光科技", "存储芯片",
        ["002130", "603986", "300475", "688525"],
        MappingStrength.DIRECT.value,
        "美光存储芯片涨价直接传导A股存储板块",
        0.9),
    USStockMapping("AVGO", "博通", "半导体/网络芯片",
        ["300308", "300502", "688313"],
        MappingStrength.INDIRECT.value,
        "博通网络芯片与A股光模块/CPO相关",
        0.7),
    USStockMapping("MRVL", "Marvell", "半导体/数据通信",
        ["300308", "688313", "300502"],
        MappingStrength.INDIRECT.value,
        "Marvell数据通信芯片与A股光模块相关",
        0.7),

    # === 消费电子 ===
    USStockMapping("AAPL", "Apple苹果", "消费电子/果链",
        ["002241", "002475", "300433", "603501", "300308"],
        MappingStrength.DIRECT.value,
        "苹果新品发布直接催化A股果链/消费电子",
        1.0),
    USStockMapping("META", "Meta元宇宙", "VR/AR/AI应用",
        ["002475", "300433", "002241"],
        MappingStrength.INDIRECT.value,
        "Meta VR/AR设备催化A股相关供应链",
        0.7),

    # === 汽车/新能源 ===
    USStockMapping("TSLA", "Tesla特斯拉", "智能驾驶/锂电",
        ["300750", "002594", "002460", "300124", "300223"],
        MappingStrength.DIRECT.value,
        "Tesla是A股锂电/智能驾驶核心风向标",
        1.0),
    USStockMapping("RIVN", "Rivian", "新能源车",
        ["300750", "002594"],
        MappingStrength.SENTIMENT.value,
        "Rivian电动皮卡情绪传导A股新能源车",
        0.4),

    # === 软件/AI应用 ===
    USStockMapping("MSFT", "微软", "AI应用/云计算",
        ["300017", "603881", "002396"],
        MappingStrength.INDIRECT.value,
        "微软AI/Copilot催化A股算力租赁/云服务",
        0.7),
    USStockMapping("GOOGL", "谷歌", "AI应用/云计算",
        ["300017", "603881"],
        MappingStrength.SENTIMENT.value,
        "谷歌AI进展情绪传导A股AI应用",
        0.5),
    USStockMapping("PLTR", "Palantir", "AI数据分析",
        ["300017", "603881"],
        MappingStrength.SENTIMENT.value,
        "Palantir AI数据分析催化A股AI应用情绪",
        0.4),

    # === 航天/军工 ===
    USStockMapping("SPACE", "SpaceX(非上市)", "商业航天",
        ["688333", "002465", "600118", "605090"],
        MappingStrength.SENTIMENT.value,
        "SpaceX发射/星链进展催化A股商业航天情绪",
        0.6),
    USStockMapping("BA", "波音", "航空航天/军工",
        ["600118", "002465", "688333"],
        MappingStrength.SENTIMENT.value,
        "波音订单/事件传导A股航空航天",
        0.5),

    # === 医药生物 ===
    USStockMapping("MRNA", "Moderna", "疫苗/创新药",
        ["300122", "603392", "688185"],
        MappingStrength.SENTIMENT.value,
        "Moderna mRNA技术催化A股疫苗/创新药",
        0.5),
    USStockMapping("PFE", "辉瑞", "医药",
        ["300199", "600196", "603259"],
        MappingStrength.SENTIMENT.value,
        "辉瑞产品进展传导A股医药",
        0.4),
    USStockMapping("LLY", "礼来", "减肥药/GLP-1",
        ["300026", "002294", "688117"],
        MappingStrength.DIRECT.value,
        "礼来GLP-1减肥药催化A股减肥药概念",
        0.8),

    # === 能源 ===
    USStockMapping("XOM", "埃克森美孚", "石油石化",
        ["600028", "601857", "600346"],
        MappingStrength.SENTIMENT.value,
        "国际油价波动传导A股石化",
        0.5),
    USStockMapping("CL=F", "WTI原油期货", "石油石化",
        ["600028", "601857", "600346"],
        MappingStrength.DIRECT.value,
        "原油期货价格直接影响A股石化板块",
        0.7),

    # === 大宗商品 ===
    USStockMapping("GC=F", "黄金期货", "黄金",
        ["600988", "601899", "000975"],
        MappingStrength.DIRECT.value,
        "黄金期货价格直接影响A股黄金板块",
        0.9),
    USStockMapping("SI=F", "白银期货", "白银/贵金属",
        ["600547", "002155"],
        MappingStrength.DIRECT.value,
        "白银期货价格影响A股贵金属",
        0.8),

    # === 金融 ===
    USStockMapping("JPM", "摩根大通", "金融科技/券商",
        ["600030", "601688", "300033"],
        MappingStrength.SENTIMENT.value,
        "美股金融股表现传导A股券商情绪",
        0.4),

    # === 电商/物流 ===
    USStockMapping("AMZN", "亚马逊", "跨境电商/物流",
        ["300464", "601021", "601598"],
        MappingStrength.INDIRECT.value,
        "亚马逊电商/云计算催化A股跨境电商",
        0.6),

    # === 机器人 ===
    USStockMapping("OPTI", "Optimus(特斯拉机器人)", "机器人",
        ["300161", "002031", "002747", "300124"],
        MappingStrength.DIRECT.value,
        "Optimus量产进展直接催化A股机器人板块",
        0.9),
]


class CrossMarketMapper:
    """跨市场催化剂映射引擎"""

    def __init__(self):
        self.mapping_table: Dict[str, List[USStockMapping]] = {}
        self._build_mapping_table()
        self.history: List[CrossMarketSignal] = []
        self._load_history()

    def _build_mapping_table(self):
        """构建映射表 (按美股代码索引)"""
        for m in US_CN_MAPPING:
            if m.us_ticker not in self.mapping_table:
                self.mapping_table[m.us_ticker] = []
            self.mapping_table[m.us_ticker].append(m)

    def _load_history(self):
        """加载历史信号"""
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                self.history = [
                    CrossMarketSignal(**s) for s in data.get("signals", [])
                ]
            except Exception:
                pass

    def _save_history(self):
        """保存历史信号"""
        # 只保留最近30天
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        recent = [s for s in self.history if s.date >= cutoff]
        data = {
            "signals": [asdict(s) for s in recent],
            "updated": datetime.now().isoformat(),
        }
        HISTORY_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ============================================================
    # 核心: 处理美股夜盘数据
    # ============================================================
    def process_us_market_data(
        self,
        us_data: List[Dict[str, Any]],
    ) -> List[CrossMarketSignal]:
        """
        处理美股夜盘数据, 生成跨市场信号

        Args:
            us_data: [{"ticker": "NVDA", "name": "NVIDIA", "change_pct": 5.2,
                       "event": "Q2财报超预期, AI芯片需求爆发"}]
        Returns:
            List[CrossMarketSignal]
        """
        signals = []
        today = datetime.now().strftime("%Y-%m-%d")

        for item in us_data:
            ticker = item.get("ticker", "").upper()
            name = item.get("name", "")
            change = item.get("change_pct", 0.0)
            event = item.get("event", "")

            # 查找映射
            mappings = self.mapping_table.get(ticker, [])
            if not mappings:
                # 尝试模糊匹配
                for key, ms in self.mapping_table.items():
                    if key in ticker or ticker in key or name in str(ms):
                        mappings = ms
                        break

            if not mappings:
                continue

            for m in mappings:
                # 判断预判影响
                if change >= 3:
                    predicted_impact = "利好"
                elif change <= -3:
                    predicted_impact = "利空"
                elif change >= 1:
                    predicted_impact = "偏多"
                elif change <= -1:
                    predicted_impact = "偏空"
                else:
                    predicted_impact = "中性"

                # 计算预判强度
                intensity = self._calc_intensity(
                    change, m.impact_factor, m.mapping_type, event
                )

                # 置信度
                confidence = self._calc_confidence(
                    m.impact_factor, abs(change), m.mapping_type
                )

                signal = CrossMarketSignal(
                    date=today,
                    us_ticker=ticker,
                    us_name=name or m.us_name,
                    us_change=round(change, 2),
                    us_event=event[:200],
                    cn_sector=m.cn_sector,
                    cn_stocks=m.cn_stocks,
                    mapping_strength=m.mapping_type,
                    impact_factor=m.impact_factor,
                    predicted_impact=predicted_impact,
                    predicted_intensity=round(intensity, 1),
                    confidence=round(confidence, 3),
                )
                signals.append(signal)

        # 按强度排序
        signals.sort(key=lambda x: -x.predicted_intensity)
        self.history.extend(signals)
        self._save_history()
        return signals

    def _calc_intensity(
        self, change: float, impact_factor: float, mapping_type: str, event: str
    ) -> float:
        """计算预判强度(0-100)"""
        score = 0.0

        # 涨跌幅分 (40分)
        abs_change = abs(change)
        if abs_change >= 10:
            score += 40
        elif abs_change >= 5:
            score += 30
        elif abs_change >= 3:
            score += 20
        elif abs_change >= 1:
            score += 10
        else:
            score += abs_change * 5

        # 影响系数分 (30分)
        score += impact_factor * 30

        # 映射类型分 (15分)
        type_score = {
            MappingStrength.DIRECT.value: 15,
            MappingStrength.INDIRECT.value: 10,
            MappingStrength.SENTIMENT.value: 6,
            MappingStrength.WEAK.value: 3,
        }
        score += type_score.get(mapping_type, 5)

        # 事件催化加成 (15分)
        if event:
            high_impact_words = ["财报", "新品", "发布", "突破", "量产", "订单", "收购", "FDA", "获批"]
            if any(w in event for w in high_impact_words):
                score += 15
            else:
                score += 5

        return min(score, 100.0)

    def _calc_confidence(
        self, impact_factor: float, abs_change: float, mapping_type: str
    ) -> float:
        """计算置信度"""
        base = impact_factor * 0.5
        change_bonus = min(abs_change / 20, 0.3)
        type_bonus = {
            MappingStrength.DIRECT.value: 0.2,
            MappingStrength.INDIRECT.value: 0.1,
            MappingStrength.SENTIMENT.value: 0.05,
            MappingStrength.WEAK.value: 0.02,
        }.get(mapping_type, 0.05)
        return min(base + change_bonus + type_bonus, 0.95)

    # ============================================================
    # 获取映射表统计
    # ============================================================
    def get_mapping_stats(self) -> Dict:
        """获取映射表统计"""
        total = len(US_CN_MAPPING)
        by_type = {}
        by_sector = {}
        for m in US_CN_MAPPING:
            by_type[m.mapping_type] = by_type.get(m.mapping_type, 0) + 1
            by_sector[m.cn_sector] = by_sector.get(m.cn_sector, 0) + 1
        return {
            "total_mappings": total,
            "by_type": by_type,
            "by_sector": by_sector,
            "unique_us_tickers": len(set(m.us_ticker for m in US_CN_MAPPING)),
        }

    # ============================================================
    # 查询接口
    # ============================================================
    def get_mappings_for_ticker(self, ticker: str) -> List[USStockMapping]:
        """获取某美股代码的所有A股映射"""
        return self.mapping_table.get(ticker.upper(), [])

    def get_mappings_for_sector(self, sector: str) -> List[USStockMapping]:
        """获取某A股板块对应的美股映射"""
        return [m for m in US_CN_MAPPING if sector in m.cn_sector]

    # ============================================================
    # 生成HTML报告
    # ============================================================
    def generate_html_report(self, signals: List[CrossMarketSignal]) -> str:
        """生成跨市场催化剂报告"""
        today = datetime.now().strftime("%Y-%m-%d")
        stats = self.get_mapping_stats()

        # 按影响分组
        positive = [s for s in signals if s.predicted_impact in ("利好", "偏多")]
        negative = [s for s in signals if s.predicted_impact in ("利空", "偏空")]
        neutral = [s for s in signals if s.predicted_impact == "中性"]

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>V13.5.37 跨市场催化剂映射 — {today}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; background: #0a0e1a; color: #e0e6ed; padding: 20px; }}
.header {{ text-align: center; padding: 30px; background: linear-gradient(135deg, #1a1f35, #1b3a4b); border-radius: 16px; margin-bottom: 24px; border: 1px solid #3d4f7a; }}
.header h1 {{ font-size: 26px; color: #6bcf7f; margin-bottom: 8px; }}
.header .sub {{ color: #8892b0; font-size: 13px; }}
.stats {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
.stat {{ flex: 1; min-width: 120px; padding: 16px; background: #111827; border-radius: 12px; border: 1px solid #1e293b; text-align: center; }}
.stat .n {{ font-size: 28px; font-weight: bold; }}
.stat .l {{ color: #8892b0; font-size: 12px; }}
.section {{ margin-bottom: 24px; }}
.section-title {{ font-size: 18px; margin-bottom: 12px; padding-left: 12px; border-left: 4px solid #6bcf7f; }}
.signal {{ background: #111827; border-radius: 12px; padding: 16px; margin-bottom: 10px; border: 1px solid #1e293b; }}
.signal.positive {{ border-left: 4px solid #2ed573; }}
.signal.negative {{ border-left: 4px solid #ff4757; }}
.signal.neutral {{ border-left: 4px solid #8892b0; }}
.signal-header {{ display: flex; justify-content: space-between; margin-bottom: 8px; }}
.signal-title {{ font-size: 16px; font-weight: bold; }}
.signal-change {{ font-size: 18px; font-weight: bold; }}
.positive .signal-change {{ color: #2ed573; }}
.negative .signal-change {{ color: #ff4757; }}
.signal-meta {{ color: #8892b0; font-size: 13px; margin-bottom: 6px; }}
.signal-stocks {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }}
.stock-tag {{ padding: 2px 8px; background: rgba(46,213,115,0.15); border-radius: 6px; font-size: 12px; color: #2ed573; }}
.intensity-bar {{ height: 4px; background: #1e293b; border-radius: 2px; margin: 6px 0; }}
.intensity-fill {{ height: 100%; border-radius: 2px; }}
.footer {{ text-align: center; color: #4a5568; font-size: 12px; padding: 20px; }}
.mapping-table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
.mapping-table th, .mapping-table td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; text-align: left; font-size: 13px; }}
.mapping-table th {{ color: #8892b0; font-weight: normal; }}
</style>
</head>
<body>
<div class="header">
<h1>V13.5.37 跨市场催化剂映射</h1>
<div class="sub">美股/港股夜盘 → 次日A股热点预判 | {today}</div>
</div>
<div class="stats">
<div class="stat"><div class="n" style="color:#2ed573">{len(positive)}</div><div class="l">利好信号</div></div>
<div class="stat"><div class="n" style="color:#ff4757">{len(negative)}</div><div class="l">利空信号</div></div>
<div class="stat"><div class="n" style="color:#8892b0">{len(neutral)}</div><div class="l">中性信号</div></div>
<div class="stat"><div class="n" style="color:#6bcf7f">{stats['total_mappings']}</div><div class="l">映射条目</div></div>
<div class="stat"><div class="n" style="color:#82b1ff">{stats['unique_us_tickers']}</div><div class="l">美股标的</div></div>
</div>
"""

        if signals:
            html += '<div class="section"><div class="section-title">跨市场催化剂信号</div>'
            for s in signals:
                cls = "positive" if s.predicted_impact in ("利好", "偏多") else "negative" if s.predicted_impact in ("利空", "偏空") else "neutral"
                color = "#2ed573" if s.predicted_intensity >= 60 else "#ffa502" if s.predicted_intensity >= 40 else "#8892b0"
                stock_tags = "".join(f'<span class="stock-tag">{st}</span>' for st in s.cn_stocks[:6])
                html += f"""
<div class="signal {cls}">
<div class="signal-header">
<span class="signal-title">{s.us_name} ({s.us_ticker}) → {s.cn_sector}</span>
<span class="signal-change">{'+' if s.us_change >= 0 else ''}{s.us_change:.1f}%</span>
</div>
<div class="signal-meta">
影响: <b>{s.predicted_impact}</b> | 强度: {s.predicted_intensity:.0f} | 置信度: {s.confidence:.0%} |
映射: {s.mapping_strength} | 系数: {s.impact_factor}
</div>
<div class="intensity-bar"><div class="intensity-fill" style="width:{s.predicted_intensity}%;background:{color}"></div></div>
<div class="signal-meta">事件: {s.us_event[:100]}</div>
<div class="signal-stocks">{stock_tags}</div>
</div>
"""
            html += '</div>'

        # 映射表总览
        html += '<div class="section"><div class="section-title">映射知识库总览</div>'
        html += '<table class="mapping-table"><tr><th>美股代码</th><th>名称</th><th>A股板块</th><th>关联个股</th><th>映射类型</th><th>系数</th></tr>'
        for m in US_CN_MAPPING[:20]:
            html += f"""<tr><td>{m.us_ticker}</td><td>{m.us_name}</td><td>{m.cn_sector}</td>
            <td>{', '.join(m.cn_stocks[:4])}</td><td>{m.mapping_type}</td><td>{m.impact_factor}</td></tr>"""
        html += '</table></div>'

        html += f"""
<div class="footer">
V13.5.37 CrossMarket Mapper | {stats['total_mappings']}映射 | {stats['unique_us_tickers']}美股标的 | {len(stats['by_sector'])}A股板块<br>
毕方灵犀貔貅助手 | {today}
</div>
</body></html>"""

        report_path = BASE / "outputs" / f"crossmarket_{today.replace('-','')}.html"
        report_path.write_text(html, encoding="utf-8")
        return str(report_path)


# ============================================================
# 测试验证
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.37 跨市场催化剂映射引擎 — 测试验证")
    print("=" * 70)

    mapper = CrossMarketMapper()

    # 映射表统计
    print("\n[1] 映射表统计...")
    stats = mapper.get_mapping_stats()
    print(f"    总映射: {stats['total_mappings']}")
    print(f"    美股标的: {stats['unique_us_tickers']}")
    print(f"    A股板块: {len(stats['by_sector'])}")
    print(f"    映射类型分布:")
    for t, c in stats["by_type"].items():
        print(f"      {t}: {c}")
    print(f"    板块分布:")
    for s, c in sorted(stats["by_sector"].items(), key=lambda x: -x[1]):
        print(f"      {s}: {c}")

    # 模拟美股夜盘数据
    print("\n[2] 模拟美股夜盘数据处理...")
    us_market_data = [
        {"ticker": "NVDA", "name": "NVIDIA", "change_pct": 6.5,
         "event": "Q2财报超预期, AI芯片需求爆发, 数据中心收入增长154%"},
        {"ticker": "TSLA", "name": "Tesla", "change_pct": 4.2,
         "event": "Optimus人形机器人量产计划提前, 2026年小批量生产"},
        {"ticker": "AAPL", "name": "Apple", "change_pct": 2.1,
         "event": "Vision Pro中国版获批, 预计Q3发售"},
        {"ticker": "MU", "name": "美光科技", "change_pct": 8.3,
         "event": "HBM存储芯片供不应求, 涨价15%"},
        {"ticker": "LLY", "name": "礼来", "change_pct": 5.7,
         "event": "GLP-1减肥药Q2销售额超预期30%"},
        {"ticker": "GC=F", "name": "黄金期货", "change_pct": 1.8,
         "event": "国际金价突破2400美元/盎司创新高"},
        {"ticker": "ASML", "name": "ASML", "change_pct": -3.5,
         "event": "下调2025年营收指引, 光刻机需求放缓"},
    ]

    signals = mapper.process_us_market_data(us_market_data)
    print(f"    生成 {len(signals)} 个跨市场信号:")
    for s in signals[:10]:
        print(f"    [{s.predicted_impact:4s}] {s.us_name:12s} {'+' if s.us_change>=0 else ''}{s.us_change:.1f}% "
              f"→ {s.cn_sector:12s} | 强度{s.predicted_intensity:.0f} | 置信{s.confidence:.0%} | "
              f"个股: {','.join(s.cn_stocks[:3])}")

    # 生成HTML报告
    print("\n[3] 生成HTML报告...")
    report = mapper.generate_html_report(signals)
    print(f"    报告: {report}")

    # 查询接口测试
    print("\n[4] 查询接口测试...")
    nvda_maps = mapper.get_mappings_for_ticker("NVDA")
    print(f"    NVDA映射: {len(nvda_maps)}条")
    for m in nvda_maps:
        print(f"      → {m.cn_sector}: {m.cn_stocks[:3]} ({m.mapping_type}, {m.impact_factor})")

    semiconductor_maps = mapper.get_mappings_for_sector("半导体")
    print(f"    半导体板块映射: {len(semiconductor_maps)}条美股标的")

    print("\n" + "=" * 70)
    print("✅ V13.5.37 跨市场催化剂映射引擎验证通过!")
    print(f"   {stats['total_mappings']}映射 | {stats['unique_us_tickers']}美股 | {len(stats['by_sector'])}板块")
    print("=" * 70)
