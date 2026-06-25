#!/usr/bin/env python3
"""
V13.4 全市场实时盯盘引擎 — 圣杯级全覆盖
==========================================
升级: 从"跌幅前50" → 全市场(A股~5000只, 排除ST/新三板)
策略: TDX Screener分页深挖 + 三路并行查询 + 三档分层

扫描策略:
  1. 跌幅排序 (主渠道) — 分页到300只覆盖全市场跌幅≥3%
  2. 放量下跌 (量能渠道) — 捕捉放量超跌反转候选 
  3. 换手率异常 (活跃度渠道) — 捕捉高换手异动

三档分层:
  🅰️ A档 (跌幅≥5%): 全量监控 → 每只都做M46+M57+M64评分
  🅱️ B档 (跌幅3-5%): 核心监控 → 前50只评分
  ©️ C档 (跌幅1-3%): 抽样监控 → 前30只评分(优先换手率高的)

排除规则:
  - ST / *ST 股票 (名称/代码含ST)
  - 新三板 (代码以8或4开头, 部分4xxxxx)
  - 退市整理期 (名称含"退")
  - 上市不足60个交易日(新股)
  - 北交所(代码以8开头, setcode=2, 可选排除)

六时段扫描:
  T0 10:30 A+B+C全量 | T1 11:30 A+B | T2 13:30 A+B 
  T3 14:00 A+B+C精校 | T4 14:15 A全量+精筛 | T5 14:30 终极S级

时间: 2026-06-24 16:38 自主进化 V13.4
"""

import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Set
from collections import defaultdict, Counter
import hashlib

# M57因子增强器
from V13_2_M57_FactorEnhancer import M57FactorEnhancer

# 市场异常检测器
try:
    from market_anomaly_detector import MarketAnomalyDetector
    HAS_ANOMALY_DETECTOR = True
except ImportError:
    HAS_ANOMALY_DETECTOR = False
    print("[V13.4] ⚠️ market_anomaly_detector 未找到，异常检测功能禁用")


# ═══════════════════════════════════════════════════════════
# SECTION 1: 全市场扫描配置
# ═══════════════════════════════════════════════════════════

class FullMarketConfig:
    """V13.4 全市场配置"""
    
    # 三档分层阈值
    TIER_A_THRESHOLD = 5.0    # 跌幅≥5%
    TIER_B_THRESHOLD = 3.0    # 跌幅≥3%
    TIER_C_THRESHOLD = 1.0    # 跌幅≥1%
    
    # 各时段扫描规模
    SCAN_CONFIG = {
        '10:30': {'tier_a': 999, 'tier_b': 100, 'tier_c': 50,  'screener_pages': 3, 'weight': 0.25},
        '11:30': {'tier_a': 999, 'tier_b': 100, 'tier_c': 30,  'screener_pages': 3, 'weight': 0.15},
        '13:30': {'tier_a': 999, 'tier_b': 80,  'tier_c': 0,   'screener_pages': 2, 'weight': 0.15},
        '14:00': {'tier_a': 999, 'tier_b': 120, 'tier_c': 80,  'screener_pages': 4, 'weight': 0.20},
        '14:15': {'tier_a': 999, 'tier_b': 50,  'tier_c': 0,   'screener_pages': 2, 'weight': 0.25},
        '14:30': {'tier_a': 50,  'tier_b': 0,   'tier_c': 0,   'screener_pages': 1, 'weight': 0.00},
    }
    
    # TDX Screener查询策略 (三路并行)
    SCREENER_QUERIES = [
        ("跌幅排序", 100, 4),      # (query, pageSize, maxPages) — 主渠道
        ("放量下跌", 50, 3),       # 量能渠道
        ("换手率排序 跌幅大于1%", 50, 3),  # 活跃度渠道
    ]
    
    # 排除规则 (正则)
    EXCLUDE_PATTERNS = {
        'st': re.compile(r'(ST|\*ST|S\*ST|S ST|退)', re.IGNORECASE),
        'newthird': re.compile(r'^(8[3-9]|4[0-3])\d{4}$'),  # 新三板代码
        'delist': re.compile(r'退$'),
    }
    
    # 排除B股 (200xxx, 900xxx)
    EXCLUDE_CODE_PREFIXES = {'200', '900', '201', '202'}
    
    @classmethod
    def should_exclude(cls, code: str, name: str, market: str = '') -> bool:
        """判断是否应排除"""
        # 1. ST/*ST
        if cls.EXCLUDE_PATTERNS['st'].search(name):
            return True
        
        # 2. 新三板 (8xxxxx, 4xxxxx)
        if cls.EXCLUDE_PATTERNS['newthird'].match(code):
            return True
        
        # 3. B股
        if any(code.startswith(p) for p in cls.EXCLUDE_CODE_PREFIXES):
            return True
        
        # 4. 北交所 (8开头, 可选)
        # if code.startswith('8'):
        #     return True
        
        # 5. ETF/LOF (沪市51xxxx 深市159xxx)
        if code.startswith(('51', '56', '58')) or (code.startswith('159') and len(code) == 6):
            return True  # ETF
        
        return False
    
    @classmethod
    def classify_tier(cls, decline_pct: float) -> str:
        """三档分层"""
        if abs(decline_pct) >= cls.TIER_A_THRESHOLD:
            return 'A'
        elif abs(decline_pct) >= cls.TIER_B_THRESHOLD:
            return 'B'
        elif abs(decline_pct) >= cls.TIER_C_THRESHOLD:
            return 'C'
        return 'D'  # 不入池


# ═══════════════════════════════════════════════════════════
# SECTION 2: 数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class FullMarketStock:
    """全市场股票记录"""
    code: str
    name: str
    decline_pct: float      # 当日涨跌幅 (%)
    amplitude: float = 0.0   # 振幅
    hsl: float = 0.0         # 换手率
    volume_ratio: float = 1.0 # 量比
    price: float = 0.0
    market: str = ''          # sh/sz/bj
    sector: str = ''          # 板块
    tier: str = 'D'           # A/B/C/D
    
    # 评分 (后续计算)
    m46_score: float = 0.0
    m57_score: float = 0.0
    m64_score: float = 0.0
    v132_score: float = 0.0
    alert_level: str = ''
    recommendation: str = ''
    
    # 跨时段追踪
    hit_count: int = 0
    cumulative_weight: float = 0.0
    first_seen: str = ''
    last_seen: str = ''
    
    def to_dict(self) -> Dict:
        return {
            'code': self.code, 'name': self.name,
            'decline_pct': self.decline_pct, 'amplitude': self.amplitude,
            'hsl': self.hsl, 'volume_ratio': self.volume_ratio,
            'price': self.price, 'market': self.market,
            'sector': self.sector, 'tier': self.tier,
            'm46_score': self.m46_score, 'm57_score': self.m57_score,
            'm64_score': self.m64_score, 'v132_score': self.v132_score,
            'alert_level': self.alert_level, 'recommendation': self.recommendation,
            'hit_count': self.hit_count, 'cumulative_weight': self.cumulative_weight,
        }


@dataclass 
class ScanSnapshot:
    """单次扫描快照"""
    period: str
    timestamp: str
    total_scanned: int       # 扫描总数
    excluded: int             # 排除数(ST/新三板等)
    tier_a: int              # A档数量
    tier_b: int              # B档数量
    tier_c: int              # C档数量
    stocks: List[FullMarketStock]
    market_index: Dict = field(default_factory=dict)
    
    @property
    def valid_count(self) -> int:
        return self.tier_a + self.tier_b + self.tier_c


# ═══════════════════════════════════════════════════════════
# SECTION 3: 全市场扫描引擎
# ═══════════════════════════════════════════════════════════

class FullMarketScanner:
    """
    V13.4 全市场实时盯盘引擎
    
    功能:
    - 多查询分页TDX Screener扫描
    - ST/新三板/B股自动排除
    - 三档分层 (A/B/C)
    - 跨时段信号累积
    - 五级预警 + 圣杯探测
    - 实时HTML全市场仪表盘
    """
    
    def __init__(self, data_dir: str = "data", output_dir: str = "outputs"):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.cache_dir = os.path.join(data_dir, "fullmarket_cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)
        
        # 全市场状态
        self.all_stocks: Dict[str, FullMarketStock] = {}  # code -> stock
        self.scan_history: List[ScanSnapshot] = []
        self.execution_log: List[str] = []
        self.market_index: Dict = {}
        
        # 排除统计
        self.exclude_log: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
        
        # M46归一化引擎 (延迟导入)
        self._m46_available = False
        try:
            from V13_2_M46_Normalized import normalize_m46_batch, get_m46_stats
            self._m46_normalize = normalize_m46_batch
            self._m46_stats = get_m46_stats
            self._m46_available = True
        except ImportError:
            self._m46_normalize = None
            self._m46_stats = None
    
    def log(self, msg: str, level: str = "INFO"):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] [V13.4] [{level}] {msg}"
        self.execution_log.append(entry)
        print(entry)
    
    # ─── TDX数据接入 (数据来源: TDX MCP) ───
    
    def ingest_screener_results(
        self, 
        screener_data: List[Dict], 
        query_name: str = "screener"
    ) -> int:
        """
        接入TDX Screener返回的原始数据
        
        预期数据格式 (来自mcp__tdx-connector__tdx_screener):
        [{code, name, changePct?, amplitude?, hsl?, volumeRatio?, ...}, ...]
        """
        ingested = 0
        excluded = 0
        
        for row in screener_data:
            code = str(row.get('code', '')).strip()
            name = str(row.get('name', '')).strip()
            
            if not code or not name:
                continue
            
            # 排除检查
            if FullMarketConfig.should_exclude(code, name):
                reason = 'ST' if 'ST' in name.upper() else 'NewThird' if code.startswith(('8','4')) else 'Other'
                self.exclude_log[reason].append((code, name))
                excluded += 1
                continue
            
            # 解析涨跌幅
            decline_pct = float(row.get('changePct', row.get('change_pct', 0)))
            
            # 只收跌幅股 (负值表示下跌)
            if decline_pct >= 0:
                continue  # 上涨股不入池
            
            # 三档分层
            tier = FullMarketConfig.classify_tier(decline_pct)
            if tier == 'D':
                continue  # 跌幅太小
            
            # 行情数据
            amplitude = float(row.get('amplitude', row.get('amplitudePct', 0)))
            hsl = float(row.get('hsl', row.get('turnoverRate', row.get('turnover_rate', 0))))
            vol_ratio = float(row.get('volumeRatio', row.get('volume_ratio', 1.0)))
            price = float(row.get('price', row.get('lastPrice', row.get('close', 0))))
            
            # 市场判断
            if code.startswith('6'):
                market = 'sh'
            elif code.startswith(('0', '3')):
                market = 'sz'
            elif code.startswith('8'):
                market = 'bj'
            else:
                market = 'unknown'
            
            # 板块
            sector = str(row.get('sector', row.get('industry', row.get('industryName', '')))).strip()
            
            # 去重 (如果已存在则更新)
            if code in self.all_stocks:
                existing = self.all_stocks[code]
                # 保留更大的跌幅绝对值
                if abs(decline_pct) > abs(existing.decline_pct):
                    existing.decline_pct = decline_pct
                if amplitude > existing.amplitude:
                    existing.amplitude = amplitude
                if hsl > existing.hsl:
                    existing.hsl = hsl
                if vol_ratio > existing.volume_ratio:
                    existing.volume_ratio = vol_ratio
                continue
            
            # 新建
            stock = FullMarketStock(
                code=code, name=name, decline_pct=decline_pct,
                amplitude=amplitude, hsl=hsl, volume_ratio=vol_ratio,
                price=price, market=market, sector=sector, tier=tier,
            )
            self.all_stocks[code] = stock
            ingested += 1
        
        self.log(f"  [{query_name}] 接入: {ingested}只 (排除{excluded}只) | 全量池: {len(self.all_stocks)}只")
        return ingested
    
    def ingest_quotes_data(self, quotes_data: Dict[str, Dict]) -> int:
        """
        接入TDX Quotes返回的详细行情，丰富已有股票数据
        
        quotes_data: {code: {price, amplitude, hsl, volume_ratio, ...}, ...}
        """
        updated = 0
        for code, data in quotes_data.items():
            if code not in self.all_stocks:
                continue
            
            stock = self.all_stocks[code]
            if p := data.get('price', data.get('lastPrice', 0)):
                stock.price = float(p)
            if a := data.get('amplitude', data.get('amplitudePct', 0)):
                stock.amplitude = max(stock.amplitude, float(a))
            if h := data.get('hsl', data.get('turnoverRate', 0)):
                stock.hsl = max(stock.hsl, float(h))
            if v := data.get('volumeRatio', data.get('volume_ratio', 0)):
                stock.volume_ratio = max(stock.volume_ratio, float(v))
            if d := data.get('changePct', data.get('change_pct', data.get('pctChg', 0))):
                if abs(float(d)) > abs(stock.decline_pct):
                    stock.decline_pct = float(d)
            
            updated += 1
        
        if updated:
            self.log(f"  [Quotes] 更新 {updated}只 行情数据")
        return updated
    
    # ─── 评分引擎 ───
    
    # ─── 市场异常检测 (P1-11) ───
    
    def _detect_market_anomaly(self) -> tuple:
        """
        检测市场异常状态（调用 market_anomaly_detector 模块）
        
        Returns:
            (anomaly_score, recommendation)
            recommendation: 'NORMAL' | 'REDUCE_POSITION' | 'SKIP_TRADING'
        """
        if not HAS_ANOMALY_DETECTOR:
            return 0.0, 'NORMAL'
        
        try:
            detector = MarketAnomalyDetector()
            
            # 1. 收集池内股票
            stocks = []
            for s in self.all_stocks.values():
                if s.v132_score > 0:
                    stocks.append({
                        'code': s.code,
                        'name': s.name,
                        'decline_pct': s.decline_pct,
                        'tier': s.tier,
                    })
            
            # 2. 检测异常
            anomaly_score, recommendation = detector.detect_anomaly(
                self.market_index, 
                stocks
            )
            
            return anomaly_score, recommendation
            
        except Exception as e:
            self.log(f"  [Anomaly] 检测失败: {e}", "WARN")
            return 0.0, 'NORMAL'
    

    def score_stocks(
        self, 
        tier_filter: List[str] = None,
        max_count: int = None,
        period: str = "manual"
    ) -> List[FullMarketStock]:
        """
        对池内股票进行M46+M57+M64评分
        
        新增: 市场异常检测 (P1-11)
        """
        if tier_filter is None:
            tier_filter = ['A', 'B', 'C']
        
        # [P1-11] 市场异常检测 🚨
        anomaly_score, recommendation = self._detect_market_anomaly()
        
        if recommendation == 'SKIP_TRADING':
            self.log(f"  [Score {period}] 🚨 市场异常，暂停交易 (anomaly={anomaly_score:.4f})")
            return []
        elif recommendation == 'REDUCE_POSITION':
            self.log(f"  [Score {period}] ⚠️ 市场异常，降低仓位 (anomaly={anomaly_score:.4f})")
            # 降低评分权重 (后续会在V13.2综合评分中调整)
        
        # 筛选
        candidates = [s for s in self.all_stocks.values() if s.tier in tier_filter]
        
        if max_count:
            # 按跌幅绝对值排序取前N
            candidates = sorted(candidates, key=lambda s: abs(s.decline_pct), reverse=True)[:max_count]
        
        total = len(candidates)
        if total == 0:
            self.log(f"  [Score {period}] 无候选股")
            return []
        
        scored = 0
        
        # M46归一化 (如果可用)
        if self._m46_available:
            try:
                # 构建M46输入格式
                m46_input = []
                for s in candidates:
                    m46_input.append({
                        'code': s.code, 'name': s.name,
                        'decline': s.decline_pct,
                        'amplitude': s.amplitude,
                        'hsl': s.hsl,
                        'sector': s.sector,
                    })
                
                m46_results = self._m46_normalize(m46_input)
                # M46NormalizedResult是dataclass，转为属性访问
                m46_map = {r.code: r for r in m46_results}
                
                for s in candidates:
                    if s.code in m46_map:
                        r = m46_map[s.code]
                        s.m46_score = r.m46_normalized
                        s.recommendation = r.recommendation
            except Exception as e:
                self.log(f"  M46归一化失败: {e}", "WARN")
        
        # M57 简易评分 (基于超跌反转逻辑)
        for s in candidates:
            abs_decline = abs(s.decline_pct)
            # M57: 隔夜Alpha简易版
            overnight = min(abs_decline / 10, 1.0) * 0.3
            intraday_rev = max(0, 1 - s.amplitude / 15) * 0.2
            flow_accel = min(s.volume_ratio / 3, 1.0) * 0.15 if s.volume_ratio > 1.0 else 0.05
            gap_fill = min(s.hsl / 15, 1.0) * 0.15
            sector_alpha = 0.10  # 默认
            streak_exp = 0.10  # 默认
            
            s.m57_score = overnight + intraday_rev + flow_accel + gap_fill + sector_alpha + streak_exp
            
            # [P1-9] M57增强: sentiment_trans (市场情绪传导) ✅
            if self.market_index:
                try:
                    sh_chg = self.market_index.get('000001', {}).get('chg', 0)
                    # 解析百分比 (支持float或string格式)
                    if isinstance(sh_chg, str):
                        index_chg = float(sh_chg.replace('%', '').strip())
                    else:
                        index_chg = float(sh_chg)
                    
                    # sentiment_trans逻辑: 个股弱于指数=超跌(情绪压制→弹簧效应)
                    stock_chg = s.decline_pct  # decline_pct就是涨跌幅(负=跌)
                    relative_strength = stock_chg - index_chg
                    
                    if relative_strength < -2.0:
                        # 情绪压制越强, 反弹预期越高 (权重0.1)
                        sentiment_trans = min(abs(relative_strength) / 5.0, 1.0) * 0.1
                        s.m57_score += sentiment_trans
                except Exception as e:
                    self.log(f"  [{s.code}] sentiment_trans失败: {e}", "WARN")
            
            # [P1-9] M57增强: lhb_effect (龙虎榜效应) ✅
            try:
                lhb_effect = self._compute_lhb_effect(s.code, s.decline_pct)
                if lhb_effect > 0:
                    s.m57_score += lhb_effect * 0.05
                    s.m57_breakdown = getattr(s, 'm57_breakdown', {})
                    s.m57_breakdown['lhb_effect'] = round(lhb_effect, 4)
            except Exception as e:
                self.log(f"  [{s.code}] lhb_effect失败: {e}", "WARN")
            
            # [P1-9] M57增强: event_decay (事件衰减) ✅
            try:
                event_decay = self._compute_event_decay(s.code)
                if abs(event_decay) > 0.01:  # 只在有显著影响时加分
                    s.m57_score += event_decay * 0.05
                    s.m57_breakdown = getattr(s, 'm57_breakdown', {})
                    s.m57_breakdown['event_decay'] = round(event_decay, 4)
            except Exception as e:
                self.log(f"  [{s.code}] event_decay失败: {e}", "WARN")
            
            # [P1-9] M57增强: tail_vol_struct (尾盘量能结构) ✅
            try:
                tail_vol = self._compute_tail_vol_struct(s.code, s.decline_pct)
                if tail_vol > 0.3:  # 只在有明显信号时加分
                    s.m57_score += tail_vol * 0.05
                    s.m57_breakdown = getattr(s, 'm57_breakdown', {})
                    s.m57_breakdown['tail_vol_struct'] = round(tail_vol, 4)
            except Exception as e:
                self.log(f"  [{s.code}] tail_vol_struct失败: {e}", "WARN")
            
            # M64: 超跌反转增强 (V2: 增加HSL/振幅/放量惩罚因子)
            # V13.4.2修复: 原公式仅2种输出(2.34/1.95), 根因=m64_raw恒定0.52
            # 修复: 5因子 → 量价收缩(5档) + 振幅反转 + HSL流动性 + 板块对齐 + 超跌指数
            abs_decline = abs(s.decline_pct)
            
            # 1. 量价收缩 (0.045~0.375) — 缩量下跌 > 放量下跌
            vol_contraction = 0.15
            if s.volume_ratio < 0.5:
                vol_contraction *= 2.5
            elif s.volume_ratio < 0.7:
                vol_contraction *= 1.8
            elif s.volume_ratio < 0.85:
                vol_contraction *= 1.2
            elif s.volume_ratio > 2.0:
                vol_contraction *= 0.3
            elif s.volume_ratio > 1.5:
                vol_contraction *= 0.6
            
            # 2. 反转强度 (0~0.30) — 振幅大=博弈激烈=反转潜力大
            reversal_strength = 0.15 * min(s.amplitude / 8.0, 2.0)
            
            # 3. 换手率信号 (0~0.24) — 高换手=筹码交换充分
            hsl_factor = 0.12 * min(s.hsl / 8.0, 2.0)
            
            # 4. 板块对齐 (0.03~0.08)
            sector_kw = ['能源', '材料', '军工', '半导体', '机械', '化工', '医药']
            sector_bonus = 0.8 if any(kw in (s.sector or '') for kw in sector_kw) else 0.3
            sector_align = 0.10 * sector_bonus
            
            # 5. 超跌指数 (0~0.12)
            decline_index = 0.08 * min(abs_decline / 10.0, 1.5)
            
            m64_raw = vol_contraction + reversal_strength + hsl_factor + sector_align + decline_index
            
            # M64放大器 (P0-1修复, V2: 增加>=5%档)
            m64_amp = 3.0
            if abs_decline >= 9: m64_amp *= 1.5
            elif abs_decline >= 7: m64_amp *= 1.25
            elif abs_decline >= 5: m64_amp *= 1.05
            
            s.m64_score = round(m64_raw * m64_amp, 4)
            
            # V13.2 综合评分
            s.v132_score = s.m46_score * 0.35 + s.m57_score * 0.35 + s.m64_score * 0.15
            
            # [P1-11] 市场异常调整 🚨
            if recommendation == 'REDUCE_POSITION':
                # 降低评分 (减少仓位)
                reduction = anomaly_score * 0.8  # 最多降低80%
                s.v132_score *= (1 - reduction)
                s.alert_level = "⚠️ 市场异常-降低仓位"
                self.log(f"  [{s.code}] 市场异常调整: {s.v132_score:.4f} → {s.v132_score:.4f} (降低{reduction:.1%})")
            
            # 预警级别
            from V13_3_IntradayMonitor import AlertLevel
            s.alert_level = AlertLevel.from_score(s.v132_score).label
            
            scored += 1
        
        self.log(f"  [Score {period}] {scored}/{total}只完成评分 | avg V13.2={sum(s.v132_score for s in candidates)/max(scored,1):.4f}")
        return candidates
    
    # ─── M57因子增强器 (P1-9) ───
    
    @property
    def m57_enhancer(self):
        """懒加载M57因子增强器"""
        if not hasattr(self, '_m57_enhancer'):
            try:
                self._m57_enhancer = M57FactorEnhancer()
            except Exception as e:
                self.log(f"  [M57Enhancer] 初始化失败: {e}", "WARN")
                self._m57_enhancer = None
        return self._m57_enhancer
    
    def _compute_lhb_effect(self, stock_code: str, decline_pct: float) -> float:
        """
        计算龙虎榜效应因子
        
        数据来源: data/lhb_YYYYMMDD.json (从TDX API获取)
        
        评分逻辑:
        1. 如果股票在LHB列表中 → 基础分0.5 (显著交易活动)
        2. 如果有买卖详情 → 使用完整的compute_lhb_effect()
        """
        if not self.m57_enhancer:
            return 0.0
        
        try:
            # 从本地缓存文件读取LHB数据
            lhb_file = f"data/lhb_{datetime.now().strftime('%Y%m%d')}.json"
            if not os.path.exists(lhb_file):
                # 尝试读取昨天的
                yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')
                lhb_file = f"data/lhb_{yesterday}.json"
            
            if not os.path.exists(lhb_file):
                return 0.0
            
            with open(lhb_file, 'r', encoding='utf-8') as f:
                lhb_data = json.load(f)
            
            # 查找该股票的LHB数据
            stock_lhb = None
            for item in lhb_data.get('stocks', []):
                if item.get('code') == stock_code:
                    stock_lhb = item
                    break
            
            if not stock_lhb:
                return 0.0
            
            # 检查是否有完整的买卖详情
            if 'buy_amount' in stock_lhb and 'sell_amount' in stock_lhb:
                # 有完整详情, 使用M57FactorEnhancer计算
                return self.m57_enhancer.compute_lhb_effect(stock_lhb, decline_pct)
            else:
                # 只有基本信息, 给予基础分数
                # LHB上榜 = 显著交易活动, 基础分0.5
                # 如果涨停, 额外加分
                base_score = 0.5
                if stock_lhb.get('change_pct', 0) >= 9.5:
                    base_score += 0.3
                return min(base_score, 1.0)
        
        except Exception as e:
            self.log(f"  [{stock_code}] LHB效应计算失败: {e}", "WARN")
            return 0.0
    
    def _compute_event_decay(self, stock_code: str) -> float:
        """
        计算事件衰减因子
        
        数据来源: data/sentiment_db.db 的 english_news 表
        """
        if not self.m57_enhancer:
            return 0.0
        
        try:
            # 获取新闻事件
            events = self.m57_enhancer.fetch_news_events(stock_code, days=7)
            
            if not events:
                return 0.0
            
            # 计算事件衰减
            return self.m57_enhancer.compute_event_decay(events, datetime.now().strftime('%Y-%m-%d'))
        
        except Exception as e:
            self.log(f"  [{stock_code}] 事件衰减计算失败: {e}", "WARN")
            return 0.0
    
    def _compute_tail_vol_struct(self, stock_code: str, decline_pct: float) -> float:
        """
        计算尾盘量能结构因子（使用1分钟K线，精度最大化）
        
        数据来源: 
        1. 优先: data/kline_1min_{code}.json (Sina Finance 免费API)
        2. 回退: data/kline_5min_{code}.json (TDX MCP)
        """
        if not self.m57_enhancer:
            return 0.0
        
        try:
            # 1. 尝试读取1分钟K线缓存
            kline_file = f"data/kline_1min_{stock_code}.json"
            if not os.path.exists(kline_file):
                # 2. 回退到5分钟K线
                kline_file = f"data/kline_5min_{stock_code}.json"
            
            if not os.path.exists(kline_file):
                # 3. 实时拉取（如果交易时间内）
                if self._should_fetch_realtime(stock_code):
                    self._fetch_1min_kline(stock_code)
                    kline_file = f"data/kline_1min_{stock_code}.json"
                
                if not os.path.exists(kline_file):
                    return 0.0
            
            with open(kline_file, 'r', encoding='utf-8') as f:
                kline_data = json.load(f)
            
            # 提取K线列表
            if isinstance(kline_data, dict):
                kline_list = kline_data.get('data', kline_data.get('kline', []))
            else:
                kline_list = kline_data
            
            if not kline_list:
                return 0.0
            
            # 3. 调用M57FactorEnhancer计算
            return self.m57_enhancer.compute_tail_vol_struct(kline_list, decline_pct)
        
        except Exception as e:
            self.log(f"  [{stock_code}] 尾盘量能结构计算失败: {e}", "WARN")
            return 0.0
    
    def _should_fetch_realtime(self, stock_code: str) -> bool:
        """
        判断是否应该实时拉取K线数据
        
        条件: 交易时间内（9:30-15:00，周末/节假日除外）
        """
        now = datetime.now()
        
        # 周末不拉取
        if now.weekday() >= 5:
            return False
        
        # 交易时间
        current_time = now.strftime('%H:%M')
        if '09:30' <= current_time <= '15:00':
            return True
        
        return False
    
    def _fetch_1min_kline(self, stock_code: str, market: str = 'sh') -> bool:
        """
        拉取并缓存1分钟K线数据
        
        使用: fetch_1min_kline.py 中的函数
        """
        try:
            # 动态导入fetch_1min_kline模块
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "fetch_1min_kline", 
                "fetch_1min_kline.py"
            )
            if spec is None:
                return False
            
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            
            # 确定market参数
            if stock_code.startswith('6'):
                mkt = 'sh'
            elif stock_code.startswith(('0', '3')):
                mkt = 'sz'
            else:
                mkt = 'bj'
            
            # 拉取并缓存
            return module.save_1min_kline_cache(stock_code, mkt, days=1)
        
        except Exception as e:
            self.log(f"  [{stock_code}] 拉取1分钟K线失败: {e}", "WARN")
            return False
    
    # ─── 跨时段累积 ───
    
    def accumulate_across_periods(
        self, 
        period: str, 
        newly_scored: List[FullMarketStock],
        period_weight: float = 0.20
    ) -> Dict[str, FullMarketStock]:
        """
        跨时段信号累积 (V13.4增强版)
        
        新增: 全市场覆盖统计 + 消失信号检测
        """
        now = datetime.now().isoformat()
        
        for s in newly_scored:
            if s.v132_score <= 0:
                continue
            
            if s.first_seen == '':
                s.first_seen = period
            s.last_seen = period
            
            # 检查历史
            existing = self.all_stocks.get(s.code)
            if existing and existing.hit_count > 0:
                s.hit_count = existing.hit_count + 1
                
                # 累积权重
                base = s.v132_score * period_weight
                repeat_bonus = 1 + (s.hit_count - 1) * 0.1
                
                if s.v132_score > existing.v132_score:
                    s.cumulative_weight = existing.cumulative_weight + base * repeat_bonus * 1.15
                elif s.v132_score > existing.v132_score * 0.85:
                    s.cumulative_weight = existing.cumulative_weight + base * repeat_bonus
                else:
                    s.cumulative_weight = existing.cumulative_weight + base * repeat_bonus * 0.7
            else:
                s.hit_count = 1
                s.cumulative_weight = s.v132_score * period_weight
            
            # 更新全局池
            self.all_stocks[s.code] = s
        
        return self.all_stocks
    
    # ─── 圣杯探测 ───
    
    def detect_holy_grail_signals(self) -> List[FullMarketStock]:
        """
        全市场圣杯级信号检测
        
        条件:
        1. V13.2 > 0.75
        2. 跨3+时段
        3. 累积权重 > 1.2
        4. A档 ≥ 跌幅5%
        5. M46 > 0.65
        """
        holy_grails = []
        
        for stock in self.all_stocks.values():
            score = 0
            checks = []
            
            if stock.v132_score >= 0.85:
                score += 4; checks.append("v132>0.85")
            elif stock.v132_score >= 0.75:
                score += 2; checks.append("v132>0.75")
            
            if stock.hit_count >= 3:
                score += 2; checks.append(f"跨{stock.hit_count}时段")
            
            if stock.cumulative_weight > 1.2:
                score += 2; checks.append("权重大")
            
            if stock.tier == 'A':
                score += 1; checks.append("A档")
            elif stock.tier == 'B':
                score += 0.5
            
            if stock.m46_score > 0.65:
                score += 1; checks.append("M46>0.65")
            
            if score >= 5:
                stock.alert_level = "⚡ 超级信号"
                holy_grails.append(stock)
                self.log(f"🏆 全市场圣杯! [{stock.tier}档] {stock.code} {stock.name} v132={stock.v132_score:.4f} ({', '.join(checks)})")
        
        holy_grails.sort(key=lambda s: s.cumulative_weight, reverse=True)
        return holy_grails
    
    # ─── 全市场仪表盘 ───
    
    def generate_dashboard(
        self, 
        period: str,
        tier_counts: Dict[str, int],
        market_index: Dict = None
    ) -> str:
        """V13.4 全市场实时仪表盘"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 统计
        total_scanned = len(self.all_stocks)
        stocks_a = [s for s in self.all_stocks.values() if s.tier == 'A']
        stocks_b = [s for s in self.all_stocks.values() if s.tier == 'B']
        stocks_c = [s for s in self.all_stocks.values() if s.tier == 'C']
        
        # 圣杯探测
        holy_grails = self.detect_holy_grail_signals()
        
        # 按累积权重排序
        all_scored = sorted(
            [s for s in self.all_stocks.values() if s.v132_score > 0],
            key=lambda s: s.cumulative_weight, reverse=True
        )
        
        # 预警统计
        alert_counts = Counter(s.alert_level for s in all_scored)
        
        # 板块统计
        sector_stats = defaultdict(lambda: {'count': 0, 'a_count': 0, 'avg_v132': 0.0, 'max_v132': 0.0, 'best': ''})
        for s in self.all_stocks.values():
            if s.v132_score <= 0:
                continue
            sec = s.sector or '未知'
            sector_stats[sec]['count'] += 1
            if s.tier == 'A':
                sector_stats[sec]['a_count'] += 1
            sector_stats[sec]['avg_v132'] += s.v132_score
            if s.v132_score > sector_stats[sec]['max_v132']:
                sector_stats[sec]['max_v132'] = s.v132_score
                sector_stats[sec]['best'] = f"{s.code} {s.name}"
        
        for sec in sector_stats:
            sector_stats[sec]['avg_v132'] /= max(sector_stats[sec]['count'], 1)
        
        # 市场指数
        sh_str = cy_str = ""
        if market_index:
            sh = market_index.get('000001', {})
            cy = market_index.get('399006', {})
            sh_str = f"上证 {sh.get('price','?')} ({sh.get('chg','?')}%)"
            cy_str = f"创业板 {cy.get('price','?')} ({cy.get('chg','?')}%)"
        
        # 排除统计
        total_excluded = sum(len(v) for v in self.exclude_log.values())
        
        # 圣杯卡片
        holy_html = ""
        if holy_grails:
            cards = []
            for hg in holy_grails[:5]:
                tier_color = {'A': '#ff3333', 'B': '#ff8800', 'C': '#ffcc00'}.get(hg.tier, '#888')
                cards.append(f"""
                <div class="holy-card">
                    <div class="holy-badge">🏆 圣杯</div>
                    <div class="holy-code">{hg.code}</div>
                    <div class="holy-name">{hg.name}</div>
                    <div class="holy-score">{hg.v132_score:.4f}</div>
                    <div class="holy-tier" style="color:{tier_color}">[{hg.tier}档] 跌幅{hg.decline_pct:+.2f}%</div>
                    <div class="holy-detail">M46={hg.m46_score:.3f} | M57={hg.m57_score:.3f} | M64={hg.m64_score:.3f}</div>
                    <div class="holy-detail">跨{hg.hit_count}时段 | 累积权重{hg.cumulative_weight:.2f}</div>
                </div>""")
            holy_html = f"""
            <div class="holy-section">
                <h2 class="holy-title">🏆 全市场圣杯信号 ({len(holy_grails)}只)</h2>
                <div class="holy-grid">{''.join(cards)}</div>
            </div>"""
        
        # Top50 表格行
        signal_rows = []
        for i, s in enumerate(all_scored[:50]):
            row_class = ""
            if '超级信号' in s.alert_level:
                row_class = 'row-holy'
            elif '买入' in s.alert_level:
                row_class = 'row-red'
            elif '预警' in s.alert_level:
                row_class = 'row-orange'
            elif '关注+' in s.alert_level:
                row_class = 'row-yellow'
            
            tier_badge = f'<span class="tier-badge tier-{s.tier.lower()}">{s.tier}档</span>'
            
            signal_rows.append(f"""
            <tr class="{row_class}">
                <td class="rank">{i+1}</td>
                <td>{tier_badge}</td>
                <td class="code">{s.code}</td>
                <td class="name">{s.name}</td>
                <td class="score">{s.v132_score:.4f}</td>
                <td class="subscore">M46={s.m46_score:.3f} M57={s.m57_score:.3f} M64={s.m64_score:.3f}</td>
                <td class="decline down">{s.decline_pct:+.2f}%</td>
                <td class="extra">振{s.amplitude:.1f}% 换{s.hsl:.1f}%</td>
                <td class="hits">{s.hit_count}次</td>
                <td class="cumulative">{s.cumulative_weight:.2f}</td>
                <td class="alert">{s.alert_level}</td>
                <td class="rec">{s.recommendation}</td>
            </tr>""")
        
        # 文件生成
        dashboard = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="90">
<title>V13.4 毕方灵犀·天眼 — 全市场实时盯盘</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#050515;color:#e0e0e0;font-family:'Microsoft YaHei',sans-serif;padding:15px;font-size:13px}}
.header{{text-align:center;margin-bottom:15px;padding:15px;background:linear-gradient(135deg,#0a0a2e,#050520);border-radius:12px;border:1px solid #2a2a4a}}
.header h1{{font-size:22px;background:linear-gradient(90deg,#ff6b35,#f7c948,#00d4aa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.header .sub{{font-size:11px;color:#888;margin-top:5px}}

.holy-section{{margin-bottom:15px}}
.holy-title{{color:#ff6b35;font-size:17px;margin-bottom:10px;text-align:center;animation:pulse 1.5s infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.4}}}}
.holy-grid{{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}}
.holy-card{{background:linear-gradient(135deg,#2a1000,#1a0500);border:2px solid #ff6b35;border-radius:12px;padding:12px;min-width:180px;text-align:center;animation:glow 2s infinite alternate}}
@keyframes glow{{from{{box-shadow:0 0 8px #ff6b35}}to{{box-shadow:0 0 25px #ff3300}}}}
.holy-badge{{font-size:18px;color:#ff6b35}}
.holy-code{{font-size:16px;font-weight:bold;color:#ffaa00;font-family:monospace}}
.holy-name{{font-size:13px;color:#ccc}}
.holy-score{{font-size:22px;color:#00ff88;font-weight:bold;margin:3px 0}}
.holy-tier{{font-size:12px;font-weight:bold;margin:3px 0}}
.holy-detail{{font-size:10px;color:#999}}

.coverage-row{{display:flex;gap:10px;margin-bottom:15px;flex-wrap:wrap}}
.cov-card{{flex:1;min-width:100px;background:#0a0a2e;border-radius:10px;padding:12px;text-align:center;border:1px solid #2a2a4a}}
.cov-card .label{{font-size:10px;color:#888;margin-bottom:4px}}
.cov-card .value{{font-size:22px;font-weight:bold}}
.cov-card .sub{{font-size:9px;color:#666}}
.cov-card.a .value{{color:#ff4444}}
.cov-card.b .value{{color:#ff8800}}
.cov-card.c .value{{color:#ffcc00}}
.cov-card.total .value{{color:#00d4aa}}
.cov-card.excluded .value{{color:#888}}

.stats-row{{display:flex;gap:10px;margin-bottom:15px;flex-wrap:wrap}}
.stat-card{{flex:1;min-width:80px;background:#0a0a2e;border-radius:8px;padding:10px;text-align:center;border:1px solid #2a2a4a}}
.stat-card .label{{font-size:10px;color:#888}}
.stat-card .value{{font-size:20px;font-weight:bold}}
.stat-card.flash .value{{color:#ff3300}}
.stat-card.red .value{{color:#ff5555}}
.stat-card.orange .value{{color:#ff8800}}
.stat-card.yellow .value{{color:#ffcc00}}
.stat-card.green .value{{color:#00ff88}}

.table-container{{overflow-x:auto;max-height:60vh;overflow-y:auto;border:1px solid #2a2a4a;border-radius:8px}}
table{{width:100%;border-collapse:collapse;font-size:11px}}
th{{background:#0a0a2e;padding:8px 5px;text-align:left;color:#ffaa00;border-bottom:2px solid #333;position:sticky;top:0;z-index:1;font-size:10px}}
td{{padding:6px 5px;border-bottom:1px solid #1a1a3a;white-space:nowrap}}
tr:hover{{background:#1a1a4e}}
.row-holy{{background:linear-gradient(90deg,#2a1000,#0a0a2e)}}
.row-red{{background:linear-gradient(90deg,#1a0505,#0a0a2e)}}
.row-orange{{background:linear-gradient(90deg,#1a1005,#0a0a2e)}}
.row-yellow{{background:linear-gradient(90deg,#1a1a05,#0a0a2e)}}
.rank{{width:25px;text-align:center;font-weight:bold}}
.code{{font-family:monospace;font-weight:bold;color:#00d4aa}}
.score{{font-size:14px;font-weight:bold;color:#00ff88}}
.subscore{{font-size:9px;color:#888}}
.decline{{font-weight:bold;font-size:11px}}
.decline.down{{color:#00ff88}}
.decline.up{{color:#ff5555}}
.extra{{font-size:9px;color:#aaa}}
.hits{{text-align:center;font-size:10px}}
.cumulative{{font-weight:bold;font-size:11px}}
.alert{{font-weight:bold;font-size:11px;white-space:nowrap}}
.rec{{font-size:9px}}
.tier-badge{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:9px;font-weight:bold}}
.tier-a{{background:#550000;color:#ff8888}}
.tier-b{{background:#553300;color:#ffaa00}}
.tier-c{{background:#555500;color:#ffdd00}}

.sector-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px;margin:10px 0}}
.sector-card{{background:#0a0a2e;border-radius:8px;padding:10px;border:1px solid #2a2a4a;font-size:11px}}
.sector-card .sec-name{{font-weight:bold;color:#00d4aa}}
.sector-card .sec-data{{color:#ccc;margin-top:3px}}
.sector-card .sec-best{{color:#ffaa00;font-size:10px;margin-top:2px}}

.footer{{text-align:center;margin-top:15px;padding:8px;font-size:9px;color:#444}}
.summary-text{{font-size:11px;color:#aaa;line-height:1.5;margin:10px 0;padding:10px;background:#0a0a2e;border-radius:8px;border:1px solid #2a2a4a}}
</style>
</head>
<body>
<div class="header">
    <h1>🦅 毕方灵犀·天眼 V13.4 全市场实时盯盘</h1>
    <div class="sub">
        扫描时段: {period} | {now} | ⏱ 自动刷新: 90秒 | {sh_str} | {cy_str}
    </div>
</div>

<div class="summary-text">
    📊 <b>全市场覆盖统计</b> | 
    扫描总数: {total_scanned + total_excluded} | 
    有效入池: <span style="color:#00d4aa">{total_scanned}只</span> | 
    自动排除: <span style="color:#888">{total_excluded}只</span> 
    (ST{sum(1 for k in self.exclude_log if 'ST' in k)}只 新三板{sum(1 for k in self.exclude_log if 'NewThird' in k)}只 其他{sum(1 for k in self.exclude_log if 'Other' in k)}只)
    | 引擎: M46归一化+M57简版+M64放大 | 
    M46: {'✅激活' if self._m46_available else '⚠️降级'}
</div>

{holy_html}

<div class="coverage-row">
    <div class="cov-card a"><div class="label">🅰️ A档(≥5%)</div><div class="value">{tier_counts.get('A', 0)}</div><div class="sub">全量监控</div></div>
    <div class="cov-card b"><div class="label">🅱️ B档(3-5%)</div><div class="value">{tier_counts.get('B', 0)}</div><div class="sub">核心监控</div></div>
    <div class="cov-card c"><div class="label">©️ C档(1-3%)</div><div class="value">{tier_counts.get('C', 0)}</div><div class="sub">抽样监控</div></div>
    <div class="cov-card total"><div class="label">📊 总入池</div><div class="value">{total_scanned}</div><div class="sub">有效品种</div></div>
    <div class="cov-card excluded"><div class="label">🚫 自动排除</div><div class="value">{total_excluded}</div><div class="sub">ST/新三板/B股/ETF</div></div>
</div>

<div class="stats-row">
    <div class="stat-card flash"><div class="label">⚡ 圣杯级</div><div class="value" style="color:#ff3300">{len(holy_grails)}</div></div>
    <div class="stat-card red"><div class="label">🔴 买入</div><div class="value">{alert_counts.get('🔴 买入', 0)}</div></div>
    <div class="stat-card orange"><div class="label">🟠 预警</div><div class="value">{alert_counts.get('🟠 预警', 0)}</div></div>
    <div class="stat-card yellow"><div class="label">🟡 关注+</div><div class="value">{alert_counts.get('🟡 关注+', 0)}</div></div>
    <div class="stat-card green"><div class="label">🟢 关注</div><div class="value">{alert_counts.get('🟢 关注', 0)}</div></div>
    <div class="stat-card"><div class="label">📊 已评分</div><div class="value" style="color:#00d4aa">{len(all_scored)}</div></div>
</div>

<h3 style="color:#ffaa00;margin:15px 0 8px">📋 全市场Top50信号 (跨时段累积权重排名)</h3>
<div class="table-container">
<table>
<thead><tr>
<th>#</th><th>档</th><th>代码</th><th>名称</th><th>V13.2</th><th>因子分解</th>
<th>涨跌</th><th>振幅/换手</th><th>命中</th><th>权重</th><th>预警</th><th>建议</th>
</tr></thead>
<tbody>
{''.join(signal_rows) if signal_rows else '<tr><td colspan="12" style="text-align:center;color:#888">🔄 等待全市场扫描数据...</td></tr>'}
</tbody>
</table>
</div>

<h3 style="color:#ffaa00;margin:15px 0 8px">📊 板块信号分布</h3>
<div class="sector-grid">
{''.join(f'''<div class="sector-card">
    <div class="sec-name">{sec} <span style="color:#ff4444;font-size:10px">({info['a_count']}只A档)</span></div>
    <div class="sec-data">{info['count']}只信号 | avg V13.2={info['avg_v132']:.3f}</div>
    <div class="sec-best">最强: {info['best']} v132={info['max_v132']:.3f}</div>
</div>''' for sec, info in sorted(sector_stats.items(), key=lambda x: x[1]['avg_v132'], reverse=True)[:15])}
</div>

<div class="footer">
    V13.4 毕方灵犀·天眼 全市场实时监控 | 圣杯引擎: M46归一化+M57Alpha+M64信号放大器+全市场三档分层 
    | 排除: ST/新三板/B股 | 亚瑟数字分身 | 终极目标: T日选股→T+1涨停→连续上涨
</div>

<script>
setInterval(()=>{{document.querySelectorAll('.holy-card').forEach(c=>{{c.style.opacity=c.style.opacity=='1'?'0.6':'1'}});}},800);
console.log('🦅 V13.4 全市场仪表盘就绪 | 池内{total_scanned}只 | 圣杯{len(holy_grails)}只');
</script>
</body>
</html>"""
        
        return dashboard
    
    # ─── 主运行流程 ───
    
    def run_full_scan(
        self, 
        period: str,
        screener_results: List[Dict],
        quotes_data: Dict[str, Dict] = None,
        market_index: Dict = None,
    ) -> Tuple[str, Dict]:
        """
        运行全市场扫描
        
        Args:
            period: 时段标识 (10:30/11:30/13:30/14:00/14:15/14:30)
            screener_results: TDX Screener返回的原始数据
            quotes_data: TDX Quotes详细行情
            market_index: 市场指数数据
        
        Returns:
            (dashboard_path, summary_dict)
        """
        self.log(f"{'='*60}")
        self.log(f"🦅 V13.4 全市场扫描 [{period}] 启动")
        self.log(f"{'='*60}")
        
        # 获取时段配置
        config = FullMarketConfig.SCAN_CONFIG.get(period, FullMarketConfig.SCAN_CONFIG['10:30'])
        period_weight = config['weight']
        
        # Step 1: 数据接入
        self.log(f"[Step 1] 接入Screener数据 ({len(screener_results)}条)")
        ingested = self.ingest_screener_results(screener_results, "screener")
        
        if quotes_data:
            self.ingest_quotes_data(quotes_data)
        
        if market_index:
            self.market_index = market_index
        
        # Step 2: 分档统计
        tier_counts = Counter(s.tier for s in self.all_stocks.values())
        self.log(f"[Step 2] 三档分布: A={tier_counts.get('A',0)} B={tier_counts.get('B',0)} C={tier_counts.get('C',0)} D={tier_counts.get('D',0)}")
        
        # Step 3: 分层评分
        self.log(f"[Step 3] 分层评分 (A档全量={config['tier_a']} B档核心={config['tier_b']} C档抽样={config['tier_c']})")
        
        all_scored = []
        
        # A档: 全量评分
        a_stocks = [s for s in self.all_stocks.values() if s.tier == 'A']
        a_scored = self.score_stocks(['A'], max_count=config['tier_a'], period=f"{period}/A")
        all_scored.extend(a_scored)
        
        # B档: 核心评分
        if config['tier_b'] > 0:
            b_scored = self.score_stocks(['B'], max_count=config['tier_b'], period=f"{period}/B")
            all_scored.extend(b_scored)
        
        # C档: 抽样评分 (优先换手率高的)
        if config['tier_c'] > 0:
            c_candidates = sorted(
                [s for s in self.all_stocks.values() if s.tier == 'C'],
                key=lambda s: s.hsl, reverse=True
            )[:config['tier_c']]
            c_scored = self.score_stocks(['C'], max_count=config['tier_c'], period=f"{period}/C")
            all_scored.extend(c_scored)
        
        # Step 4: 跨时段累积
        self.log(f"[Step 4] 跨时段累积 (权重={period_weight:.2f})")
        self.accumulate_across_periods(period, all_scored, period_weight)
        
        # Step 5: 生成仪表盘
        self.log("[Step 5] 生成全市场仪表盘")
        dashboard_html = self.generate_dashboard(period, tier_counts, market_index)
        dashboard_path = os.path.join(self.output_dir, "fullmarket_dashboard.html")
        with open(dashboard_path, 'w', encoding='utf-8') as f:
            f.write(dashboard_html)
        
        # Step 6: 保存快照
        snapshot = ScanSnapshot(
            period=period,
            timestamp=datetime.now().isoformat(),
            total_scanned=len(self.all_stocks),
            excluded=sum(len(v) for v in self.exclude_log.values()),
            tier_a=tier_counts.get('A', 0),
            tier_b=tier_counts.get('B', 0),
            tier_c=tier_counts.get('C', 0),
            stocks=[s for s in self.all_stocks.values() if s.v132_score > 0],
            market_index=market_index or {},
        )
        self.scan_history.append(snapshot)
        
        # 保存JSON快照
        snapshot_path = os.path.join(
            self.cache_dir, 
            f"fullmarket_{period.replace(':', '')}_{datetime.now().strftime('%Y%m%d')}.json"
        )
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump({
                'period': period, 'timestamp': snapshot.timestamp,
                'total_scanned': snapshot.total_scanned,
                'excluded': snapshot.excluded,
                'tier_a': snapshot.tier_a, 'tier_b': snapshot.tier_b, 'tier_c': snapshot.tier_c,
                'top10': [
                    {'code': s.code, 'name': s.name, 'v132': s.v132_score,
                     'tier': s.tier, 'alert': s.alert_level,
                     'cumulative_weight': s.cumulative_weight}
                    for s in sorted([s for s in self.all_stocks.values() if s.v132_score > 0],
                                   key=lambda x: x.cumulative_weight, reverse=True)[:10]
                ],
            }, f, ensure_ascii=False, indent=2)
        
        # 摘要
        holy_grails = self.detect_holy_grail_signals()
        top3 = sorted(
            [s for s in self.all_stocks.values() if s.v132_score > 0],
            key=lambda x: x.cumulative_weight, reverse=True
        )[:3]
        
        summary = {
            'period': period,
            'total_scanned': snapshot.total_scanned,
            'excluded': snapshot.excluded,
            'tier_a': snapshot.tier_a,
            'tier_b': snapshot.tier_b,
            'tier_c': snapshot.tier_c,
            'scored': len(all_scored),
            'holy_grail_count': len(holy_grails),
            'holy_grails': [{'code': hg.code, 'name': hg.name, 'v132': hg.v132_score, 'tier': hg.tier} for hg in holy_grails],
            'top3': [
                {'code': s.code, 'name': s.name, 'v132': s.v132_score, 'tier': s.tier, 'cum_weight': s.cumulative_weight}
                for s in top3
            ],
            'dashboard_path': dashboard_path,
        }
        
        self.log(f"\n{'='*60}")
        self.log(f"🦅 V13.4 [{period}] 全市场扫描完成")
        self.log(f"  扫描总数: {snapshot.total_scanned} | 排除: {snapshot.excluded}")
        self.log(f"  三档: A={snapshot.tier_a} B={snapshot.tier_b} C={snapshot.tier_c}")
        self.log(f"  已评分: {len(all_scored)} | 圣杯: {len(holy_grails)}")
        if holy_grails:
            for hg in holy_grails[:5]:
                self.log(f"    🏆 [{hg.tier}档] {hg.code} {hg.name} v132={hg.v132_score:.4f}")
        if top3:
            self.log(f"  Top3: {' | '.join(f'{s.code} {s.name}({s.tier}档) cumW={s.cumulative_weight:.2f}' for s in top3)}")
        self.log(f"  仪表盘: {dashboard_path}")
        self.log(f"{'='*60}")
        
        return dashboard_path, summary


# ═══════════════════════════════════════════════════════════
# SECTION 4: 自动化入口
# ═══════════════════════════════════════════════════════════

# 全局扫描器实例 (跨时段持久化)
_GLOBAL_SCANNER = None

def get_global_scanner(data_dir="data", output_dir="outputs") -> FullMarketScanner:
    """获取全局单例扫描器"""
    global _GLOBAL_SCANNER
    if _GLOBAL_SCANNER is None:
        _GLOBAL_SCANNER = FullMarketScanner(data_dir, output_dir)
    return _GLOBAL_SCANNER


def run_fullmarket_scan(
    period: str,
    screener_data_file: str = None,
    use_global: bool = True,
) -> Dict:
    """
    V13.4 全市场扫描自动化入口
    
    由各时段自动化调用。
    
    参数:
        period: 时段 (10:30/11:30/13:30/14:00/14:15/14:30)
        screener_data_file: screener原始JSON文件路径
        use_global: 是否使用全局单例(跨时段累积)
    
    用法 (在自动化prompt中):
    ```python
    from V13_4_FullMarketMonitor import run_fullmarket_scan
    result = run_fullmarket_scan('10:30', 'data/fullmarket_cache/screener_t0.json')
    ```
    """
    scanner = get_global_scanner() if use_global else FullMarketScanner()
    
    # 加载screener数据
    screener_results = []
    if screener_data_file and os.path.exists(screener_data_file):
        try:
            with open(screener_data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            screener_results = data if isinstance(data, list) else data.get('results', data.get('data', []))
            scanner.log(f"从 {screener_data_file} 加载 {len(screener_results)} 条Screener数据")
        except Exception as e:
            scanner.log(f"加载screener数据失败: {e}", "ERROR")
    
    # 尝试加载quotes数据
    quotes_data = None
    quotes_file = screener_data_file.replace('screener', 'quotes') if screener_data_file else None
    if quotes_file and os.path.exists(quotes_file):
        try:
            with open(quotes_file, 'r', encoding='utf-8') as f:
                quotes_data = json.load(f)
        except:
            pass
    
    # 尝试加载市场指数
    market_index = None
    idx_file = f"data/fullmarket_cache/index_{period.replace(':', '')}.json"
    if os.path.exists(idx_file):
        try:
            with open(idx_file, 'r', encoding='utf-8') as f:
                market_index = json.load(f)
        except:
            pass
    
    # 运行全市场扫描
    dashboard_path, summary = scanner.run_full_scan(
        period=period,
        screener_results=screener_results,
        quotes_data=quotes_data,
        market_index=market_index,
    )
    
    return summary


# ═══════════════════════════════════════════════════════════
# SECTION 5: 数据缓存持久化 (跨会话恢复)
# ═══════════════════════════════════════════════════════════

def save_scanner_state(scanner: FullMarketScanner, state_file: str = None):
    """保存扫描器状态到文件，支持跨会话恢复"""
    if state_file is None:
        state_file = f"data/fullmarket_cache/state_{datetime.now().strftime('%Y%m%d')}.json"
    
    all_scored = {code: s for code, s in scanner.all_stocks.items() if s.v132_score > 0}
    holy_grails = scanner.detect_holy_grail_signals()
    hg_codes = set(hg.code for hg in holy_grails)
    
    # 构建 top_stocks 数组 (供仪表盘消费)
    top_stocks = []
    for code, s in sorted(all_scored.items(), key=lambda x: x[1].cumulative_weight, reverse=True):
        entry = {
            'code': code, 'name': s.name, 'tier': s.tier,
            'v132_score': s.v132_score,
            'm46_score': getattr(s, 'm46_score', 0.0),
            'm57_score': getattr(s, 'm57_score', 0.0),
            'm64_score': getattr(s, 'm64_score', 0.0),
            'decline_pct': s.decline_pct,
            'cumulative_weight': s.cumulative_weight,
            'alert_level': s.alert_level,
            'hit_count': s.hit_count,
        }
        # 标记圣杯
        if code in hg_codes:
            entry['alert_level'] = entry['alert_level'].replace('⚪ 无信号', '🏆 HOLY_GRAIL')
        top_stocks.append(entry)
    
    state = {
        'date': datetime.now().strftime('%Y%m%d'),
        'timestamp': datetime.now().isoformat(),
        'total_stocks': len(scanner.all_stocks),
        'holy_grail_count': len(holy_grails),
        'summary': {
            code: {
                'name': s.name, 'decline': s.decline_pct,
                'tier': s.tier, 'sector': s.sector,
                'v132': s.v132_score, 'hit_count': s.hit_count,
                'cumulative_weight': s.cumulative_weight,
                'alert_level': '🏆 HOLY_GRAIL' if code in hg_codes else s.alert_level,
            }
            for code, s in sorted(all_scored.items(),
                                  key=lambda x: x[1].cumulative_weight, reverse=True)
        },
        'top_stocks': top_stocks,
        'tier_counts': {
            'A': sum(1 for s in scanner.all_stocks.values() if s.tier == 'A'),
            'B': sum(1 for s in scanner.all_stocks.values() if s.tier == 'B'),
            'C': sum(1 for s in scanner.all_stocks.values() if s.tier == 'C'),
        },
    }
    
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    
    scanner.log(f"💾 状态保存: {state_file} ({len(state['summary'])}只有效信号)")
    
    # CloudStudio双写: 同步到deploy/data/供云端发布
    try:
        deploy_data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'deploy', 'data')
        os.makedirs(deploy_data, exist_ok=True)
        deploy_state = os.path.join(deploy_data, f"state_{datetime.now().strftime('%Y%m%d')}.json")
        with open(deploy_state, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        scanner.log(f"☁️ CloudStudio双写: {deploy_state}")
    except Exception as e:
        scanner.log(f"⚠️ CloudStudio双写失败: {e}")

def sync_index_to_cloudstudio(period: str = None):
    """
    同步市场指数文件到deploy/data/供CloudStudio发布
    
    在每次自动化获取市场指数后调用。
    """
    try:
        import shutil
        cache_dir = "data/fullmarket_cache"
        deploy_dir = "deploy/data"
        os.makedirs(deploy_dir, exist_ok=True)
        
        if period:
            # 同步特指时段
            idx_file = f"{cache_dir}/index_{period.replace(':', '')}.json"
            if os.path.exists(idx_file):
                shutil.copy2(idx_file, f"{deploy_dir}/index_{period.replace(':', '')}.json")
        else:
            # 同步所有index文件
            for f in os.listdir(cache_dir):
                if f.startswith('index_') and f.endswith('.json'):
                    shutil.copy2(f"{cache_dir}/{f}", f"{deploy_dir}/{f}")
        
        # 同时保存一个 index_latest.json 供通用回退
        latest = None
        for f in sorted(os.listdir(cache_dir), reverse=True):
            if f.startswith('index_') and f.endswith('.json'):
                latest = f
                break
        if latest:
            shutil.copy2(f"{cache_dir}/{latest}", f"{deploy_dir}/index_latest.json")
            print(f"[SYNC] ☁️ 指数同步: {latest} -> deploy/data/")
        
    except Exception as e:
        print(f"[SYNC] ⚠️ 指数同步失败: {e}")

# = ═══════════════════════════════════════════════════════════
# SECTION 6: 自测
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  V13.4 全市场实时盯盘引擎 自测                         ║")
    print("║  全市场扫描 | ST/新三板排除 | 三档分层 | 圣杯探测      ║")
    print("╚══════════════════════════════════════════════════════════╝\n")
    
    scanner = FullMarketScanner()
    
    # 模拟全市场Screener返回数据 (~200只, 含ST/新三板)
    import random
    random.seed(42)
    
    test_data = []
    # 正常股 ~180只
    sectors = ['信息技术', '计算机', '机械设备', '化工', '环保', '传媒', '农业', 
               '煤炭', '电气设备', '新能源', '氢能', '地产', '医药', '食品', '汽车',
               '军工', '通信', '金融', '建材', '纺织']
    
    for i in range(180):
        code = f"{random.choice(['600','601','603','688','000','002','300','301'])}{random.randint(100,999):03d}"
        name = f"测试股{i:03d}"
        decline = -random.uniform(1.0, 18.0)
        test_data.append({
            'code': code, 'name': name,
            'changePct': decline,
            'amplitude': random.uniform(2, 20),
            'hsl': random.uniform(1, 18),
            'volumeRatio': random.uniform(0.5, 4.0),
            'price': random.uniform(5, 80),
            'sector': random.choice(sectors),
        })
    
    # 插入ST股 (应被排除)
    for i in range(10):
        test_data.append({
            'code': f"600{random.randint(100, 999):03d}",
            'name': f'*ST测试{i}', 'changePct': -random.uniform(3, 8),
            'amplitude': 5, 'hsl': 3, 'volumeRatio': 1.0, 'price': 10,
            'sector': '未知',
        })
    
    # 插入新三板 (应被排除)
    for i in range(5):
        test_data.append({
            'code': f"83{random.randint(100000, 999999):05d}"[:6],
            'name': f'新三板测试{i}', 'changePct': -random.uniform(5, 12),
            'amplitude': 8, 'hsl': 5, 'volumeRatio': 1.2, 'price': 15,
            'sector': '未知',
        })
    
    print(f"总测试数据: {len(test_data)}条 (含{10}只ST + {5}只新三板)\n")
    
    # 运行扫描
    dashboard_path, summary = scanner.run_full_scan(
        period='14:00',
        screener_results=test_data,
    )
    
    print(f"\n{'='*60}")
    print(f"  📊 排除验证:")
    print(f"  自动排除: {summary['excluded']}只 (期望: {15}只)")
    print(f"  A档(≥5%): {summary['tier_a']}只")
    print(f"  B档(3-5%): {summary['tier_b']}只")
    print(f"  C档(1-3%): {summary['tier_c']}只")
    print(f"  已评分: {summary['scored']}只")
    print(f"  圣杯: {summary['holy_grail_count']}只")
    print(f"\n  Top3:")
    for s in summary['top3']:
        print(f"    {s['code']} {s['name']} [{s['tier']}档] v132={s['v132']:.4f} cumW={s['cum_weight']:.2f}")
    print(f"\n  📊 仪表盘: {dashboard_path}")
    print(f"{'='*60}")
    
    # 断言验证
    assert summary['excluded'] >= 15, f"排除{summary['excluded']} < 预期15!"
    assert summary['tier_a'] > 0, "A档为0!"
    print(f"\n✅ 所有断言通过!")
