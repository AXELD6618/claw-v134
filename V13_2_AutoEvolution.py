#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 自主进化引擎 — AutoEvolution                                  ║
║  ================================================================    ║
║  核心理念：奖惩驱动的自主督促学习进化                                 ║
║                                                                      ║
║  进化循环（每日T+1验证后触发）：                                       ║
║  1. 弱点诊断 → 从奖惩记录中识别系统性缺陷                             ║
║  2. 知识缺口检测 → 识别"不知道什么"并生成研究课题                     ║
║  3. 参数自适应调优 → RL风格reward梯度下降                            ║
║  4. 数据ROI追踪 → 哪些数据源投入产出比最高                           ║
║  5. 主动资源搜寻 → 生成Agent指令获取缺失数据/知识                    ║
║  6. 进化日志 → 记录每次进化迭代                                      ║
║                                                                      ║
║  目标：T日尾盘选股买入 → T+1上涨/涨停 → 启动连续上涨趋势             ║
║  → 持续盈利 → 逼近圣杯级能力                                        ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
import sqlite3
import random
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict, Counter
from enum import Enum

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'holy_grail.db')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

# 参数调优配置
MIN_EVOLUTION_SAMPLES = 3        # 至少3天数据才启动调优
MAX_PARAM_SHIFT = 0.05           # 单次参数最大偏移5%
EXPLORATION_EPSILON = 0.15       # 15%概率随机探索
LEARNING_RATE = 0.02             # 学习率
REWARD_GRADIENT_WINDOW = 5       # 奖励梯度窗口

# 参数边界
PARAM_BOUNDS = {
    'm46_threshold': (0.40, 0.80),
    'm57_weight': (0.10, 0.50),
    'm46_weight': (0.10, 0.50),
    'data_quality_weight': (0.05, 0.35),
    'buy_threshold': (0.45, 0.75),
    'watch_threshold': (0.30, 0.55),
    'm56_weight': (0.10, 0.40),
    'v130_weight': (0.20, 0.50),
}


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

class WeaknessType(Enum):
    """弱点类型"""
    LOW_PRECISION = "low_precision"           # 命中率低（选出但不涨）
    LOW_RECALL = "low_recall"                 # 召回率低（涨了但没选出）
    SECTOR_BLIND = "sector_blind"             # 行业盲区（某行业总是漏选）
    BOARD_BLIND = "board_blind"               # 板别盲区
    PRICE_BLIND = "price_blind"               # 价格区间盲区
    TIME_BLIND = "time_blind"                 # 时间模式盲区
    FACTOR_DORMANT = "factor_dormant"         # 因子休眠
    TREND_MISS = "trend_miss"                 # 趋势启动漏判
    FALSE_POSITIVE = "false_positive"         # 误判（选出但跌）
    DATA_GAP = "data_gap"                     # 数据缺口


@dataclass
class Weakness:
    """系统弱点"""
    weakness_type: str
    severity: float              # 严重程度 0-1
    description: str
    evidence: List[str]          # 证据
    affected_stocks: List[str]   # 受影响的股票
    suggested_fix: str           # 建议修复方案
    research_query: str          # 研究课题


@dataclass
class KnowledgeGap:
    """知识缺口"""
    gap_id: str
    topic: str                   # 缺口主题
    current_understanding: str   # 当前理解
    needed_understanding: str    # 需要的理解
    data_source_hint: str        # 数据源提示
    agent_instructions: str      # Agent获取指令
    priority: int                # 优先级 1-5
    expected_reward_lift: float  # 预期奖励提升


@dataclass
class ParamAdjustment:
    """参数调整"""
    param_name: str
    old_value: float
    new_value: float
    direction: str               # "increase" / "decrease" / "explore"
    reason: str
    expected_effect: str
    confidence: float            # 置信度 0-1


@dataclass
class EvolutionRecord:
    """进化记录"""
    evolution_date: str
    trigger: str                 # 触发原因
    weaknesses_found: List[Dict]
    knowledge_gaps: List[Dict]
    param_adjustments: List[Dict]
    data_roi_analysis: Dict
    agent_instructions: List[str]
    expected_improvement: float  # 预期改善
    evolution_phase: str         # "exploitation" / "exploration" / "hybrid"


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 弱点诊断器
# ═══════════════════════════════════════════════════════════════

class WeaknessDiagnoser:
    """从奖惩记录中诊断系统弱点"""

    def diagnose(self, days: int = 7) -> List[Weakness]:
        """诊断近N天的弱点"""
        records = self._load_reward_records(days)
        if not records:
            return []

        weaknesses = []

        # 1. 命中率低
        w = self._check_low_precision(records)
        if w:
            weaknesses.append(w)

        # 2. 召回率低
        w = self._check_low_recall(records)
        if w:
            weaknesses.append(w)

        # 3. 行业盲区
        w = self._check_sector_blind(records)
        if w:
            weaknesses.append(w)

        # 4. 板别盲区
        w = self._check_board_blind(records)
        if w:
            weaknesses.append(w)

        # 5. 价格区间盲区
        w = self._check_price_blind(records)
        if w:
            weaknesses.append(w)

        # 6. 因子休眠
        w = self._check_factor_dormant(records)
        if w:
            weaknesses.append(w)

        # 7. 趋势启动漏判
        w = self._check_trend_miss(records)
        if w:
            weaknesses.append(w)

        # 8. 误判过多
        w = self._check_false_positive(records)
        if w:
            weaknesses.append(w)

        # 按严重程度排序
        weaknesses.sort(key=lambda w: w.severity, reverse=True)
        return weaknesses

    def _load_reward_records(self, days: int) -> List[Dict]:
        """从SQLite加载奖惩记录"""
        if not os.path.exists(DB_PATH):
            return []
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        c.execute("""
            SELECT code, name, pick_date, t1_date, tier, score,
                   t1_change_pct, t2_change_pct, trend_started,
                   was_picked, was_missed, reason, detail
            FROM reward_records
            WHERE t1_date >= ?
            ORDER BY t1_date DESC
        """, (cutoff,))
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]
        conn.close()
        return rows

    def _check_low_precision(self, records: List[Dict]) -> Optional[Weakness]:
        """命中率低"""
        picked = [r for r in records if r['was_picked']]
        if len(picked) < 3:
            return None
        hits = [r for r in picked if r['score'] > 0]
        precision = len(hits) / len(picked)
        if precision < 0.5:
            misses = [r for r in picked if r['score'] < 0]
            return Weakness(
                weakness_type=WeaknessType.LOW_PRECISION.value,
                severity=1.0 - precision,
                description=f"命中率仅{precision*100:.1f}%，选出的{len(picked)}只中仅{len(hits)}只T+1上涨",
                evidence=[f"误判{len(misses)}只，平均惩罚{sum(abs(r['score']) for r in misses)/max(len(misses),1):.1f}分" for _ in [1]],
                affected_stocks=[r['code'] for r in misses[:10]],
                suggested_fix="提高BUY阈值，加强排雷检测，或降低M46置信度阈值减少假阳性",
                research_query="为什么选出的股票T+1不涨？是买入信号过早、还是排雷不足？分析误判股的共同特征"
            )
        return None

    def _check_low_recall(self, records: List[Dict]) -> Optional[Weakness]:
        """召回率低"""
        missed = [r for r in records if r['was_missed'] and r['score'] < 0]
        if len(missed) < 3:
            return None
        total_miss_penalty = sum(abs(r['score']) for r in missed)
        avg_miss = total_miss_penalty / len(missed)
        if avg_miss > 10:
            return Weakness(
                weakness_type=WeaknessType.LOW_RECALL.value,
                severity=min(avg_miss / 25, 1.0),
                description=f"漏选{len(missed)}只上涨股，平均漏选惩罚{avg_miss:.1f}分/只",
                evidence=[f"漏选涨停{len([r for r in missed if r['tier']=='MISS' and '涨停' in r.get('reason','')])}只",
                          f"漏选大涨{len([r for r in missed if r['tier']=='MISS' and '大涨' in r.get('reason','')])}只"],
                affected_stocks=[r['code'] for r in missed[:10]],
                suggested_fix="扩大监控池，降低WATCH阈值，激活更多M57因子，或增加行业覆盖",
                research_query="漏选的股票有什么共同特征？是什么信号导致系统未将其选出？需要什么新数据源？"
            )
        return None

    def _check_sector_blind(self, records: List[Dict]) -> Optional[Weakness]:
        """行业盲区"""
        missed = [r for r in records if r['was_missed'] and r['score'] < 0]
        if len(missed) < 5:
            return None
        # 按行业统计漏选（需要从detail中获取）
        sector_misses = defaultdict(list)
        for r in missed:
            detail = json.loads(r.get('detail', '{}')) if r.get('detail') else {}
            sector = detail.get('industry', '未知')
            sector_misses[sector].append(r)

        worst_sector = max(sector_misses.items(), key=lambda x: len(x[1]))
        if len(worst_sector[1]) >= 3:
            return Weakness(
                weakness_type=WeaknessType.SECTOR_BLIND.value,
                severity=len(worst_sector[1]) / len(missed),
                description=f"行业'{worst_sector[0]}'漏选{len(worst_sector[1])}只，占总漏选的{len(worst_sector[1])/len(missed)*100:.0f}%",
                evidence=[f"漏选股票: {', '.join(r['code'] for r in worst_sector[1][:5])}"],
                affected_stocks=[r['code'] for r in worst_sector[1]],
                suggested_fix=f"增加{worst_sector[0]}行业的监控池权重，研究该行业尾盘买入信号特征",
                research_query=f"{worst_sector[0]}行业尾盘选股有什么特殊规律？需要什么行业专属因子？"
            )
        return None

    def _check_board_blind(self, records: List[Dict]) -> Optional[Weakness]:
        """板别盲区"""
        missed = [r for r in records if r['was_missed'] and r['score'] < 0]
        if len(missed) < 5:
            return None
        board_misses = defaultdict(list)
        for r in missed:
            code = r['code']
            if code.startswith('688'):
                board = '科创板'
            elif code.startswith('300'):
                board = '创业板'
            elif code.startswith(('8', '9')):
                board = '北交所'
            elif code.startswith('6'):
                board = '上海主板'
            else:
                board = '深圳主板'
            board_misses[board].append(r)

        worst_board = max(board_misses.items(), key=lambda x: len(x[1]))
        if len(worst_board[1]) >= 3:
            return Weakness(
                weakness_type=WeaknessType.BOARD_BLIND.value,
                severity=len(worst_board[1]) / len(missed),
                description=f"{worst_board[0]}漏选{len(worst_board[1])}只",
                evidence=[f"漏选: {', '.join(r['code'] for r in worst_board[1][:5])}"],
                affected_stocks=[r['code'] for r in worst_board[1]],
                suggested_fix=f"检查M59对{worst_board[0]}的过滤是否过严，或涨跌幅阈值需调整",
                research_query=f"{worst_board[0]}的尾盘选股逻辑与其他板别有何不同？20%/30%涨跌幅规则如何影响？"
            )
        return None

    def _check_price_blind(self, records: List[Dict]) -> Optional[Weakness]:
        """价格区间盲区"""
        missed = [r for r in records if r['was_missed'] and r['score'] < 0]
        if len(missed) < 5:
            return None
        # 按价格区间统计
        price_ranges = {
            '低价股(<10元)': [], '中价股(10-50元)': [],
            '高价股(50-100元)': [], '超高价(>100元)': []
        }
        for r in missed:
            detail = json.loads(r.get('detail', '{}')) if r.get('detail') else {}
            t1_close = detail.get('t1_close', 0)
            if t1_close == 0:
                continue
            if t1_close < 10:
                price_ranges['低价股(<10元)'].append(r)
            elif t1_close < 50:
                price_ranges['中价股(10-50元)'].append(r)
            elif t1_close < 100:
                price_ranges['高价股(50-100元)'].append(r)
            else:
                price_ranges['超高价(>100元)'].append(r)

        worst_range = max(price_ranges.items(), key=lambda x: len(x[1]))
        if len(worst_range[1]) >= 3:
            return Weakness(
                weakness_type=WeaknessType.PRICE_BLIND.value,
                severity=len(worst_range[1]) / len(missed),
                description=f"{worst_range[0]}漏选{len(worst_range[1])}只",
                evidence=[f"漏选: {', '.join(r['code'] for r in worst_range[1][:5])}"],
                affected_stocks=[r['code'] for r in worst_range[1]],
                suggested_fix=f"检查{worst_range[0]}的监控池覆盖是否充足",
                research_query=f"{worst_range[0]}的尾盘交易行为有什么特征？散户/机构占比如何影响？"
            )
        return None

    def _check_factor_dormant(self, records: List[Dict]) -> Optional[Weakness]:
        """因子休眠"""
        # 检查M57因子激活率
        detail_samples = [r for r in records if r.get('detail') and r['was_picked']]
        if len(detail_samples) < 3:
            return None

        dormant_factors = []
        for r in detail_samples[:20]:
            detail = json.loads(r['detail']) if isinstance(r['detail'], str) else r['detail']
            m57_alpha = detail.get('m57_alpha', 0)
            if m57_alpha == 0:
                dormant_factors.append(r['code'])

        if len(dormant_factors) > len(detail_samples) * 0.3:
            return Weakness(
                weakness_type=WeaknessType.FACTOR_DORMANT.value,
                severity=0.7,
                description=f"{len(dormant_factors)}/{len(detail_samples)}只选出股票M57 Alpha=0，因子处于休眠状态",
                evidence=[f"Alpha=0的股票: {', '.join(dormant_factors[:5])}"],
                affected_stocks=dormant_factors[:10],
                suggested_fix="获取Tier2/Tier3 TDX数据（资金流/龙虎榜/新闻），激活5个休眠因子",
                research_query="哪些M57因子仍然休眠？需要什么TDX数据激活？激活后预期Alpha区分度提升多少？"
            )
        return None

    def _check_trend_miss(self, records: List[Dict]) -> Optional[Weakness]:
        """趋势启动漏判"""
        hits = [r for r in records if r['was_picked'] and r['score'] > 0]
        if len(hits) < 3:
            return None
        trend_started = [r for r in hits if r.get('trend_started')]
        trend_rate = len(trend_started) / len(hits)
        if trend_rate < 0.3:
            return Weakness(
                weakness_type=WeaknessType.TREND_MISS.value,
                severity=1.0 - trend_rate,
                description=f"命中{len(hits)}只中仅{len(trend_started)}只启动连续上涨趋势(率={trend_rate*100:.0f}%)",
                evidence=[f"未启动趋势的命中: {len(hits)-len(trend_started)}只"],
                affected_stocks=[r['code'] for r in hits if not r.get('trend_started')][:10],
                suggested_fix="增加趋势延续因子权重，研究涨停后T+2续涨的预测特征",
                research_query="什么特征能预测T+1涨停后T+2继续上涨？连板预期/封单质量/题材持续性如何量化？"
            )
        return None

    def _check_false_positive(self, records: List[Dict]) -> Optional[Weakness]:
        """误判过多"""
        picked = [r for r in records if r['was_picked']]
        if len(picked) < 3:
            return None
        false_pos = [r for r in picked if r['score'] < -10]
        if len(false_pos) >= 2:
            return Weakness(
                weakness_type=WeaknessType.FALSE_POSITIVE.value,
                severity=min(len(false_pos) / len(picked), 1.0),
                description=f"严重误判{len(false_pos)}只(T+1大跌/跌停)",
                evidence=[f"{r['code']}: {r['reason']}" for r in false_pos[:5]],
                affected_stocks=[r['code'] for r in false_pos],
                suggested_fix="加强排雷检测，提高M46阈值，增加疲劳惩罚权重",
                research_query="误判股有什么共同特征？是否前期超买？是否有诱多信号？M46置信度是否虚高？"
            )
        return None


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 知识缺口检测器
# ═══════════════════════════════════════════════════════════════

class KnowledgeGapDetector:
    """识别知识缺口并生成研究课题"""

    GAP_TEMPLATES = [
        {
            'id': 'gap_t2_prediction',
            'topic': 'T+2连续上涨预测',
            'needed': '什么特征能预测T+1涨停后T+2继续上涨？封单质量/连板高度/题材热度如何量化？',
            'data_source': 'tdx_api_data(ztfx) + tdx_screener(涨停原因) + wenda_news_query',
            'priority': 1,
            'expected_lift': 30.0,
        },
        {
            'id': 'gap_sector_rotation',
            'topic': '行业轮动与尾盘选股',
            'needed': '不同行业的尾盘买入信号是否有差异？行业动量如何影响T+1表现？',
            'data_source': 'tdx_screener(板块分析) + tdx_api_data(板块资金流)',
            'priority': 2,
            'expected_lift': 15.0,
        },
        {
            'id': 'gap_auction_signal',
            'topic': '集合竞价信号深度解析',
            'needed': '14:57-15:00集合竞价的量价信号如何预测T+1开盘方向？',
            'data_source': 'tdx_kline(period=8, 1分钟K线 14:57-15:00)',
            'priority': 2,
            'expected_lift': 20.0,
        },
        {
            'id': 'gap_capital_flow_pattern',
            'topic': '主力资金流向与T+1表现',
            'needed': '尾盘主力净流入/大单买入与T+1涨跌的相关性如何？资金加速度因子的最优参数？',
            'data_source': 'tdx_api_data(zjlx) + tdx_quotes(委买委卖)',
            'priority': 1,
            'expected_lift': 25.0,
        },
        {
            'id': 'gap_dragon_tiger_effect',
            'topic': '龙虎榜机构/游资席位效应',
            'needed': '龙虎榜上榜股T+1表现如何？机构席位vs游资席位的差异化影响？',
            'data_source': 'tdx_api_data(jglhb)',
            'priority': 3,
            'expected_lift': 10.0,
        },
        {
            'id': 'gap_limit_up_quality',
            'topic': '涨停质量评估体系',
            'needed': '封单金额/封成比/打开次数/涨停时间如何组合评估涨停质量？高质量涨停T+1续涨率？',
            'data_source': 'tdx_api_data(ztfx) + tdx_screener(涨停)',
            'priority': 1,
            'expected_lift': 35.0,
        },
        {
            'id': 'gap_market_sentiment',
            'topic': '市场情绪传导与个股T+1',
            'needed': '大盘尾盘走势/指数情绪如何传导到个股T+1？情绪传导因子的最优Beta计算方法？',
            'data_source': 'tdx_quotes(指数) + wenda_macro_query',
            'priority': 3,
            'expected_lift': 12.0,
        },
        {
            'id': 'gap_news_decay',
            'topic': '新闻事件衰减效应',
            'needed': '不同类型新闻(利好/利空/中性)对T+1~T+5的衰减曲线如何？最优衰减常数τ？',
            'data_source': 'wenda_news_query + wenda_notice_query',
            'priority': 2,
            'expected_lift': 18.0,
        },
    ]

    def detect(self, weaknesses: List[Weakness]) -> List[KnowledgeGap]:
        """根据弱点识别知识缺口"""
        gaps = []

        for w in weaknesses:
            if w.weakness_type == WeaknessType.TREND_MISS.value:
                gaps.append(self._make_gap('gap_t2_prediction', w))
            elif w.weakness_type == WeaknessType.LOW_RECALL.value:
                gaps.append(self._make_gap('gap_sector_rotation', w))
                gaps.append(self._make_gap('gap_capital_flow_pattern', w))
            elif w.weakness_type == WeaknessType.FACTOR_DORMANT.value:
                gaps.append(self._make_gap('gap_auction_signal', w))
                gaps.append(self._make_gap('gap_dragon_tiger_effect', w))
                gaps.append(self._make_gap('gap_news_decay', w))
            elif w.weakness_type == WeaknessType.FALSE_POSITIVE.value:
                gaps.append(self._make_gap('gap_limit_up_quality', w))
            elif w.weakness_type == WeaknessType.SECTOR_BLIND.value:
                gaps.append(self._make_gap('gap_market_sentiment', w))

        # 去重
        seen_ids = set()
        unique_gaps = []
        for g in gaps:
            if g.gap_id not in seen_ids:
                seen_ids.add(g.gap_id)
                unique_gaps.append(g)

        # 按优先级排序
        unique_gaps.sort(key=lambda g: g.priority)
        return unique_gaps

    def _make_gap(self, gap_id: str, weakness: Weakness) -> KnowledgeGap:
        """从模板创建知识缺口"""
        template = next((t for t in self.GAP_TEMPLATES if t['id'] == gap_id), None)
        if not template:
            return KnowledgeGap(
                gap_id=gap_id, topic="未知缺口",
                current_understanding="无", needed_understanding="无",
                data_source_hint="无", agent_instructions="无",
                priority=5, expected_reward_lift=0
            )

        return KnowledgeGap(
            gap_id=gap_id,
            topic=template['topic'],
            current_understanding=f"当前状态: {weakness.description}",
            needed_understanding=template['needed'],
            data_source_hint=template['data_source'],
            agent_instructions=self._generate_agent_instructions(template),
            priority=template['priority'],
            expected_reward_lift=template['expected_lift'],
        )

    def _generate_agent_instructions(self, template: Dict) -> str:
        """生成Agent数据获取指令"""
        source = template['data_source']
        topic = template['topic']
        needed = template['needed']

        return f"""
## 研究课题: {topic}

### 目标
{needed}

### 数据获取指令
数据源: {source}

### 建议步骤
1. 通过TDX MCP获取上述数据源的数据
2. 对比T+1上涨股与未上涨股的数据差异
3. 寻找统计显著的预测特征
4. 将发现编码为新的因子或调整现有因子参数
5. 回测验证新因子对奖励得分的提升效果

### 预期收益
激活此知识缺口预计提升奖励得分 +{template['expected_lift']:.0f} 分/日
"""


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 参数自适应调优器
# ═══════════════════════════════════════════════════════════════

class ParamOptimizer:
    """RL风格参数自适应调优"""

    def __init__(self):
        self.current_params = self._load_current_params()

    def _load_current_params(self) -> Dict[str, float]:
        """加载当前参数"""
        defaults = {
            'm46_threshold': 0.63,
            'm46_weight': 0.40,
            'm57_weight': 0.40,
            'data_quality_weight': 0.20,
            'buy_threshold': 0.55,
            'watch_threshold': 0.40,
            'm56_weight': 0.30,
            'v130_weight': 0.40,
        }

        # 从SQLite加载上次保存的参数
        if os.path.exists(DB_PATH):
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            try:
                c.execute("""
                    CREATE TABLE IF NOT EXISTS evolution_params (
                        param_name TEXT PRIMARY KEY,
                        current_value REAL NOT NULL,
                        previous_value REAL,
                        last_adjusted TEXT,
                        adjustment_history TEXT
                    )
                """)
                c.execute("SELECT param_name, current_value FROM evolution_params")
                for name, val in c.fetchall():
                    defaults[name] = val
            except Exception:
                pass
            conn.close()

        return defaults

    def optimize(self, weaknesses: List[Weakness], reward_history: List[Dict]) -> List[ParamAdjustment]:
        """
        基于弱点和奖励历史进行参数调优
        
        策略：
        1. Exploitation: 如果奖励在改善，继续当前方向
        2. Exploration: 如果奖励停滞，随机探索新参数
        3. Weakness-driven: 根据弱点类型定向调优
        """
        adjustments = []

        if len(reward_history) < MIN_EVOLUTION_SAMPLES:
            adjustments.append(ParamAdjustment(
                param_name='__info__',
                old_value=0, new_value=0,
                direction='wait',
                reason=f"数据不足({len(reward_history)}天)，需≥{MIN_EVOLUTION_SAMPLES}天才能调优",
                expected_effect="继续积累数据",
                confidence=0
            ))
            return adjustments

        # 计算奖励梯度
        recent_scores = [r.get('daily_total', 0) for r in reward_history[:REWARD_GRADIENT_WINDOW]]
        older_scores = [r.get('daily_total', 0) for r in reward_history[REWARD_GRADIENT_WINDOW:]]
        recent_avg = sum(recent_scores) / max(len(recent_scores), 1)
        older_avg = sum(older_scores) / max(len(older_scores), 1)
        gradient = recent_avg - older_avg

        # 决定进化阶段
        is_exploring = random.random() < EXPLORATION_EPSILON

        if is_exploring:
            # 探索模式：随机调整一个参数
            param = random.choice(list(PARAM_BOUNDS.keys()))
            lo, hi = PARAM_BOUNDS[param]
            current = self.current_params.get(param, (lo + hi) / 2)
            direction = random.choice(['increase', 'decrease'])
            shift = random.uniform(0.01, MAX_PARAM_SHIFT)
            new_val = current + shift if direction == 'increase' else current - shift
            new_val = max(lo, min(hi, new_val))

            adjustments.append(ParamAdjustment(
                param_name=param,
                old_value=round(current, 4),
                new_value=round(new_val, 4),
                direction='explore',
                reason=f"探索模式(ε={EXPLORATION_EPSILON}): 随机调整{param}测试新参数空间",
                expected_effect=f"可能发现更优的{param}值",
                confidence=0.3
            ))

        else:
            # 利用模式：基于弱点和梯度定向调优
            for w in weaknesses:
                if w.weakness_type == WeaknessType.LOW_PRECISION.value:
                    # 命中率低 → 提高BUY阈值
                    adj = self._adjust_param('buy_threshold', 0.02, 'increase',
                        "命中率低，提高BUY阈值减少假阳性",
                        "减少选出数量但提高命中质量",
                        0.7, w)
                    if adj:
                        adjustments.append(adj)

                elif w.weakness_type == WeaknessType.LOW_RECALL.value:
                    # 召回率低 → 降低WATCH阈值
                    adj = self._adjust_param('watch_threshold', 0.02, 'decrease',
                        "召回率低，降低WATCH阈值捕获更多上涨股",
                        "增加选出数量但可能降低精度",
                        0.6, w)
                    if adj:
                        adjustments.append(adj)

                elif w.weakness_type == WeaknessType.FALSE_POSITIVE.value:
                    # 误判多 → 提高M46阈值
                    adj = self._adjust_param('m46_threshold', 0.02, 'increase',
                        "误判过多，提高M46置信度阈值过滤低质量信号",
                        "减少误判但可能漏选边缘机会",
                        0.8, w)
                    if adj:
                        adjustments.append(adj)

                elif w.weakness_type == WeaknessType.TREND_MISS.value:
                    # 趋势漏判 → 增加M57权重
                    adj = self._adjust_param('m57_weight', 0.02, 'increase',
                        "趋势启动率低，增加M57 Alpha权重强化趋势预测",
                        "更重视隔夜Alpha因子的趋势预测能力",
                        0.65, w)
                    if adj:
                        adjustments.append(adj)

                elif w.weakness_type == WeaknessType.FACTOR_DORMANT.value:
                    # 因子休眠 → 暂不调参，需要数据而非调参
                    adjustments.append(ParamAdjustment(
                        param_name='__data__',
                        old_value=0, new_value=0,
                        direction='data_fetch',
                        reason="因子休眠需要数据而非调参：获取Tier2/Tier3 TDX数据激活休眠因子",
                        expected_effect="激活5个休眠因子，预计Alpha区分度提升40%",
                        confidence=0.9
                    ))

        # 如果梯度为正且无调整，继续当前方向
        if not adjustments and gradient > 5:
            adjustments.append(ParamAdjustment(
                param_name='__info__',
                old_value=0, new_value=0,
                direction='maintain',
                reason=f"奖励梯度+{gradient:.1f}(改善中)，维持当前参数",
                expected_effect="继续当前趋势",
                confidence=0.8
            ))

        # 如果梯度为负且无调整，发出警告
        if not adjustments and gradient < -10:
            adjustments.append(ParamAdjustment(
                param_name='__warning__',
                old_value=0, new_value=0,
                direction='alert',
                reason=f"奖励梯度{gradient:.1f}(退步中!)，建议人工审查参数",
                expected_effect="触发人工干预",
                confidence=0.9
            ))

        # 应用调参
        for adj in adjustments:
            if adj.param_name in PARAM_BOUNDS and adj.direction != 'wait':
                self.current_params[adj.param_name] = adj.new_value

        # 保存参数
        self._save_params(adjustments)

        return adjustments

    def _adjust_param(self, param_name: str, shift: float, direction: str,
                      reason: str, expected: str, confidence: float,
                      weakness: Weakness) -> Optional[ParamAdjustment]:
        """执行单个参数调整"""
        if param_name not in PARAM_BOUNDS:
            return None
        lo, hi = PARAM_BOUNDS[param_name]
        current = self.current_params.get(param_name, (lo + hi) / 2)

        if direction == 'increase':
            new_val = min(hi, current + shift)
            if new_val == current:
                return None  # 已到上限
        else:
            new_val = max(lo, current - shift)
            if new_val == current:
                return None  # 已到下限

        return ParamAdjustment(
            param_name=param_name,
            old_value=round(current, 4),
            new_value=round(new_val, 4),
            direction=direction,
            reason=f"{reason} (弱点: {weakness.description[:50]}...)",
            expected_effect=expected,
            confidence=confidence
        )

    def _save_params(self, adjustments: List[ParamAdjustment]):
        """保存参数到SQLite"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS evolution_params (
                param_name TEXT PRIMARY KEY,
                current_value REAL NOT NULL,
                previous_value REAL,
                last_adjusted TEXT,
                adjustment_history TEXT
            )
        """)

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for adj in adjustments:
            if adj.param_name not in PARAM_BOUNDS:
                continue
            c.execute("""
                INSERT OR REPLACE INTO evolution_params
                (param_name, current_value, previous_value, last_adjusted, adjustment_history)
                VALUES (?, ?, ?, ?, ?)
            """, (
                adj.param_name, adj.new_value, adj.old_value, now,
                json.dumps({'direction': adj.direction, 'reason': adj.reason,
                           'confidence': adj.confidence}, ensure_ascii=False)
            ))

        conn.commit()
        conn.close()


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 数据ROI追踪器
# ═══════════════════════════════════════════════════════════════

class DataROITracker:
    """追踪各数据源对奖励得分的投入产出比"""

    DATA_SOURCES = {
        'tdx_quotes': {'name': '实时行情', 'tier': 1, 'cost': 1},
        'tdx_kline_daily': {'name': '日K线', 'tier': 1, 'cost': 1},
        'tdx_kline_1min': {'name': '1分钟K线', 'tier': 1, 'cost': 2},
        'tdx_screener': {'name': '选股器', 'tier': 1, 'cost': 1},
        'tdx_zjlx': {'name': '资金流向', 'tier': 2, 'cost': 2},
        'tdx_ztfx': {'name': '涨停分析', 'tier': 2, 'cost': 2},
        'tdx_jglhb': {'name': '龙虎榜', 'tier': 2, 'cost': 3},
        'tdx_ltgd': {'name': '十大股东', 'tier': 2, 'cost': 3},
        'wenda_news': {'name': '新闻', 'tier': 3, 'cost': 1},
        'wenda_notice': {'name': '公告', 'tier': 3, 'cost': 1},
        'wenda_report': {'name': '研报', 'tier': 3, 'cost': 2},
    }

    def analyze(self, days: int = 7) -> Dict:
        """分析数据ROI"""
        # 获取奖惩记录中的数据使用情况
        if not os.path.exists(DB_PATH):
            return {'status': 'no_data'}

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        c.execute("""
            SELECT detail, score, was_picked, was_missed
            FROM reward_records
            WHERE t1_date >= ?
        """, (cutoff,))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return {'status': 'no_data'}

        # 分析每条记录使用了哪些数据源
        source_stats = defaultdict(lambda: {'used': 0, 'hit': 0, 'miss': 0, 'penalty': 0})
        for detail_str, score, was_picked, was_missed in rows:
            detail = json.loads(detail_str) if detail_str else {}
            data_quality = detail.get('tdx_data_quality', {})

            for source_id in self.DATA_SOURCES:
                if data_quality.get(source_id) or detail.get(f'has_{source_id}'):
                    source_stats[source_id]['used'] += 1
                    if score > 0:
                        source_stats[source_id]['hit'] += 1
                    elif score < 0:
                        source_stats[source_id]['penalty'] += abs(score)

        # 计算ROI
        roi_results = {}
        for source_id, info in self.DATA_SOURCES.items():
            stats = source_stats[source_id]
            if stats['used'] == 0:
                roi_results[source_id] = {
                    'name': info['name'],
                    'tier': info['tier'],
                    'status': 'unused',
                    'recommendation': '尚未使用此数据源，建议获取以激活相关因子',
                }
            else:
                hit_rate = stats['hit'] / stats['used']
                roi = hit_rate / info['cost']
                roi_results[source_id] = {
                    'name': info['name'],
                    'tier': info['tier'],
                    'used_count': stats['used'],
                    'hit_rate': round(hit_rate, 4),
                    'roi': round(roi, 4),
                    'status': 'active' if roi > 0.1 else 'low_roi',
                    'recommendation': self._recommend(info, hit_rate, roi),
                }

        return {
            'status': 'ok',
            'days_analyzed': days,
            'total_records': len(rows),
            'sources': roi_results,
            'unused_sources': [s for s, r in roi_results.items() if r['status'] == 'unused'],
        }

    def _recommend(self, info: Dict, hit_rate: float, roi: float) -> str:
        if roi > 0.3:
            return f"高ROI数据源，继续使用"
        elif roi > 0.1:
            return f"中等ROI，可考虑优化使用方式"
        else:
            return f"低ROI，检查数据质量或调整因子参数"


# ═══════════════════════════════════════════════════════════════
# SECTION 5: 自主进化引擎主类
# ═══════════════════════════════════════════════════════════════

class AutoEvolution:
    """
    自主进化引擎
    
    每日T+1验证后自动触发：
    1. 诊断弱点
    2. 检测知识缺口
    3. 调优参数
    4. 分析数据ROI
    5. 生成Agent指令
    6. 记录进化日志
    """

    def __init__(self):
        self.diagnoser = WeaknessDiagnoser()
        self.gap_detector = KnowledgeGapDetector()
        self.optimizer = ParamOptimizer()
        self.roi_tracker = DataROITracker()

    def evolve(self, days: int = 7) -> EvolutionRecord:
        """执行一次进化迭代"""
        now = datetime.now()

        # 1. 弱点诊断
        weaknesses = self.diagnoser.diagnose(days)

        # 2. 知识缺口检测
        knowledge_gaps = self.gap_detector.detect(weaknesses)

        # 3. 加载奖励历史
        reward_history = self._load_reward_history(days)

        # 4. 参数调优
        adjustments = self.optimizer.optimize(weaknesses, reward_history)

        # 5. 数据ROI分析
        roi_analysis = self.roi_tracker.analyze(days)

        # 6. 生成Agent指令
        agent_instructions = self._generate_agent_instructions(
            weaknesses, knowledge_gaps, roi_analysis
        )

        # 7. 判断进化阶段
        phase = self._determine_phase(adjustments, reward_history)

        # 8. 预期改善
        expected_lift = sum(g.expected_reward_lift for g in knowledge_gaps) / max(len(knowledge_gaps), 1)

        record = EvolutionRecord(
            evolution_date=now.strftime('%Y-%m-%d %H:%M:%S'),
            trigger=f"每日T+1验证后自动进化 (分析{days}天数据)",
            weaknesses_found=[asdict(w) for w in weaknesses],
            knowledge_gaps=[asdict(g) for g in knowledge_gaps],
            param_adjustments=[asdict(a) for a in adjustments],
            data_roi_analysis=roi_analysis,
            agent_instructions=agent_instructions,
            expected_improvement=round(expected_lift, 1),
            evolution_phase=phase,
        )

        # 保存进化记录
        self._save_evolution(record)

        return record

    def _load_reward_history(self, days: int) -> List[Dict]:
        """加载奖励历史"""
        if not os.path.exists(DB_PATH):
            return []
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        c.execute("""
            SELECT t1_date, daily_total, precision, recall, total_hits, total_misses
            FROM reward_summary
            WHERE t1_date >= ?
            ORDER BY t1_date DESC
        """, (cutoff,))
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]
        conn.close()
        return rows

    def _determine_phase(self, adjustments: List[ParamAdjustment], history: List[Dict]) -> str:
        """判断进化阶段"""
        has_explore = any(a.direction == 'explore' for a in adjustments)
        has_exploit = any(a.direction in ('increase', 'decrease') for a in adjustments)

        if has_explore and has_exploit:
            return 'hybrid'
        elif has_explore:
            return 'exploration'
        elif has_exploit:
            return 'exploitation'
        else:
            return 'monitoring'

    def _generate_agent_instructions(self, weaknesses: List[Weakness],
                                      gaps: List[KnowledgeGap],
                                      roi: Dict) -> List[str]:
        """生成Agent主动资源搜寻指令"""
        instructions = []

        # 数据获取指令
        unused = roi.get('unused_sources', [])
        if unused:
            source_names = [self.roi_tracker.DATA_SOURCES.get(s, {}).get('name', s) for s in unused]
            instructions.append(
                f"[数据获取] {len(unused)}个数据源尚未使用: {', '.join(source_names)}。"
                f"通过TDX MCP获取这些数据可激活休眠因子，预计提升奖励得分。"
            )

        # 知识研究指令
        for gap in gaps[:3]:  # Top 3优先级
            instructions.append(
                f"[知识研究] 优先级{gap.priority}: {gap.topic} — {gap.needed_understanding[:80]}..."
            )

        # 弱点修复指令
        for w in weaknesses[:3]:  # Top 3弱点
            instructions.append(
                f"[弱点修复] {w.weakness_type}(严重度{w.severity:.0%}): {w.suggested_fix}"
            )

        # 主动学习指令
        if weaknesses:
            top_w = weaknesses[0]
            instructions.append(
                f"[主动学习] 搜索网络资源研究: '{top_w.research_query}' "
                f"→ 寻找学术论文/量化博客/实战经验来优化此弱点"
            )

        return instructions

    def _save_evolution(self, record: EvolutionRecord):
        """保存进化记录"""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS evolution_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evolution_date TEXT NOT NULL,
                trigger TEXT,
                weaknesses_count INTEGER,
                knowledge_gaps_count INTEGER,
                param_adjustments_count INTEGER,
                expected_improvement REAL,
                evolution_phase TEXT,
                full_record TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            INSERT INTO evolution_records
            (evolution_date, trigger, weaknesses_count, knowledge_gaps_count,
             param_adjustments_count, expected_improvement, evolution_phase, full_record)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.evolution_date, record.trigger,
            len(record.weaknesses_found),
            len(record.knowledge_gaps),
            len(record.param_adjustments),
            record.expected_improvement,
            record.evolution_phase,
            json.dumps(asdict(record), ensure_ascii=False)
        ))
        conn.commit()
        conn.close()

    def format_evolution_report(self, record: EvolutionRecord) -> str:
        """格式化进化报告"""
        lines = []
        lines.append("=" * 70)
        lines.append("  V13.2 自主进化引擎报告")
        lines.append(f"  进化时间: {record.evolution_date}")
        lines.append(f"  进化阶段: {record.evolution_phase}")
        lines.append(f"  预期改善: +{record.expected_improvement:.1f} 分/日")
        lines.append("=" * 70)

        # 弱点诊断
        lines.append("")
        lines.append(f"【弱点诊断】发现{len(record.weaknesses_found)}个弱点:")
        for i, w in enumerate(record.weaknesses_found, 1):
            lines.append(f"  {i}. [{w['weakness_type']}] 严重度={w['severity']:.0%}")
            lines.append(f"     {w['description']}")
            lines.append(f"     修复: {w['suggested_fix']}")

        # 知识缺口
        lines.append("")
        lines.append(f"【知识缺口】发现{len(record.knowledge_gaps)}个研究课题:")
        for i, g in enumerate(record.knowledge_gaps, 1):
            lines.append(f"  {i}. [P{g['priority']}] {g['topic']}")
            lines.append(f"     需要: {g['needed_understanding'][:80]}...")
            lines.append(f"     数据源: {g['data_source_hint']}")
            lines.append(f"     预期提升: +{g['expected_reward_lift']:.0f} 分/日")

        # 参数调优
        lines.append("")
        lines.append(f"【参数调优】{len(record.param_adjustments)}项调整:")
        for adj in record.param_adjustments:
            name = adj['param_name']
            if name.startswith('__'):
                lines.append(f"  [{adj['direction']}] {adj['reason']}")
            else:
                lines.append(f"  {name}: {adj['old_value']:.4f} → {adj['new_value']:.4f} ({adj['direction']})")
                lines.append(f"    原因: {adj['reason'][:70]}...")
                lines.append(f"    置信度: {adj['confidence']:.0%}")

        # 数据ROI
        roi = record.data_roi_analysis
        if roi.get('status') == 'ok':
            lines.append("")
            lines.append("【数据ROI分析】")
            lines.append(f"  分析样本: {roi['total_records']}条记录 / {roi['days_analyzed']}天")
            unused = roi.get('unused_sources', [])
            if unused:
                lines.append(f"  未使用数据源: {len(unused)}个 (建议获取)")
                for s in unused:
                    info = self.roi_tracker.DATA_SOURCES.get(s, {})
                    lines.append(f"    - {info.get('name', s)} (Tier{info.get('tier','?')})")
            else:
                lines.append(f"  所有数据源均已使用 ✓")

        # Agent指令
        lines.append("")
        lines.append("【Agent自主行动指令】")
        for i, inst in enumerate(record.agent_instructions, 1):
            lines.append(f"  {i}. {inst}")

        lines.append("")
        lines.append("=" * 70)
        lines.append("  圣杯之路: 每日进化，逼近T日选股→T+1涨停→连续上涨趋势")
        lines.append("=" * 70)

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description='V13.2 自主进化引擎')
    parser.add_argument('--days', type=int, default=7, help='分析天数')
    parser.add_argument('--demo', action='store_true', help='演示模式')
    args = parser.parse_args()

    engine = AutoEvolution()

    if args.demo:
        _run_demo(engine)
    else:
        record = engine.evolve(args.days)
        print(engine.format_evolution_report(record))

        # 保存报告到文件
        report_file = os.path.join(DATA_DIR, f'evolution_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write(engine.format_evolution_report(record))
        print(f"\n[进化报告已保存] {report_file}")


def _run_demo(engine: AutoEvolution):
    """演示模式"""
    print("\n[演示模式] 自主进化引擎演示\n")

    # 先运行奖惩引擎demo生成数据
    from V13_2_RewardEngine import RewardEngine, StockPick, T1Outcome
    reward_engine = RewardEngine()

    # 检查是否已有demo数据
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM reward_records")
    count = c.fetchone()[0]
    conn.close()

    if count == 0:
        print("首次运行，先执行奖惩引擎demo生成数据...")
        pick_date = '2026-06-24'
        t1_date = '2026-06-25'

        picks = [
            StockPick('600519', '贵州茅台', '食品饮料', pick_date, 0.72, 'BUY', 1680, 2.5, False,
                      m46_confidence=0.68, m57_alpha=0.85),
            StockPick('300750', '宁德时代', '新能源', pick_date, 0.65, 'BUY', 210, 4.2, False,
                      m46_confidence=0.71, m57_alpha=0.72),
            StockPick('002230', '科大讯飞', 'AI算力', pick_date, 0.58, 'WATCH', 45, 3.8, False,
                      m46_confidence=0.55, m57_alpha=0.45),
            StockPick('688256', '寒武纪', 'AI芯片', pick_date, 0.62, 'BUY', 250, 6.5, False,
                      m46_confidence=0.66, m57_alpha=0.78),
            StockPick('300059', '东方财富', '券商', pick_date, 0.45, 'HOLD', 14, 1.2, False,
                      m46_confidence=0.42, m57_alpha=0.20),
        ]

        outcomes = [
            T1Outcome('600519', '贵州茅台', t1_date, 1685, 1720, 1735, 1695, 2.38, False, False,
                      t2_change_pct=0.8, t3_change_pct=1.2),
            T1Outcome('300750', '宁德时代', t1_date, 215, 231, 233, 212, 10.0, True, False,
                      t2_change_pct=3.5, t3_change_pct=1.8),
            T1Outcome('002230', '科大讯飞', t1_date, 45.5, 44.2, 46.0, 43.8, -1.78, False, False),
            T1Outcome('688256', '寒武纪', t1_date, 255, 275, 278, 252, 10.0, True, False,
                      t2_change_pct=5.2, t3_change_pct=2.1),
            T1Outcome('300059', '东方财富', t1_date, 14.1, 13.8, 14.3, 13.6, -1.43, False, False),
            T1Outcome('000725', '京东方A', t1_date, 4.2, 4.45, 4.48, 4.15, 5.95, False, False),
            T1Outcome('601012', '隆基绿能', t1_date, 18.5, 19.8, 20.0, 18.3, 7.03, False, False),
            T1Outcome('002594', '比亚迪', t1_date, 250, 265, 268, 248, 6.0, False, False),
            T1Outcome('300308', '中际旭创', t1_date, 160, 176, 178, 159, 10.0, True, False),
            T1Outcome('601899', '紫金矿业', t1_date, 12.5, 13.25, 13.4, 12.4, 6.0, False, False),
        ]

        reward_engine.record_picks(picks, pick_date)
        reward_engine.evaluate(picks, outcomes, 8, 3, t1_date)

    # 运行进化
    record = engine.evolve(7)
    print(engine.format_evolution_report(record))


if __name__ == '__main__':
    main()
