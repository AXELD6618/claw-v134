#!/usr/bin/env python3
"""
V13.5.16 M71 全市场反转预测集成层
====================================
将M71反转预测引擎(31维度V3.6)融合到V13_4_FullMarketMonitor的尾盘选股管道中。

V13.5.18升级 (34维度 — 蜀道装备模式全面升级):
  1. D29双洗盘识别(10→12分): 新增洗盘日主力微正+2分(蜀道6/24暴跌-8.97%但主力+169万=洗盘铁证)
  2. D31出货否决降权: 催化(D28≥8)>出货时降权50%(-5→-2.5), 埃斯顿7/2验证
  3. D32暴跌日DDX×0.5修正: 暴跌日DDX估算不可靠(蜀道6/24DDX偏负但真实主力微正)
  4. D30交叉确认+0.5分: D29≥6 AND D31≥6 → D30额外+0.5(三确认体系)
  5. D33外盘/内盘比率(3分): 外盘>内盘1.5倍=强势主动买入(TDX实时行情)
  
核心逻辑:
  1. 从全市场扫描结果中提取Top N候选(按v132_score排序)
  2. 对每只候选股调用M71的31维度反转信号评分
  3. 根据M71评分调整v132_score:
     - STRONG_REVERSAL(≥65): v132 += 0.15 (上限0.95)
     - REVERSAL(45-64):      v132 += 0.08
     - WATCH(30-44):         v132 不变
     - NO_SIGNAL(<30):       v132 -= 0.05
  4. M71评分≥45的股票标记为 "⚡ M71反转确认"
  5. 保存M71预测到m71_reversal_predictions表

调用方式 (自动化内):
  from V13_5_M71_FullMarketIntegration import enhance_with_m71
  enhanced = enhance_with_m71(scanner, period='14:15', top_n=20)

V3.1更新:
  - enhance()接受quote_data和sentiment_data参数
  - 传递实时行情和舆情数据给M71 predict()

作者: 毕方灵犀貔貅助手 (亚瑟数字分身)
版本: V13.5.19 (V3.9 37维度五确认体系 — D34拆单识别+D35庄成本线+D36委比异动+五确认交叉)
日期: 2026-07-05
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import asdict

# M71 反转预测引擎
from V13_5_M71_ReversalPredictor import (
    ReversalPredictor, TDXDataAdapter,
    KlineBar, DragonTigerEntry, CapitalFlow,
    ReversalGrade, prediction_to_dict
)


# ═══════════════════════════════════════════════════════════
# M71 增强配置
# ═══════════════════════════════════════════════════════════

class M71EnhanceConfig:
    """M71增强配置"""

    # 对Top N只候选股运行M71 (控制API调用次数)
    TOP_N = 20

    # v132_score 调整幅度
    BOOST_STRONG = 0.15     # STRONG_REVERSAL(≥80) → +0.15
    BOOST_REVERSAL = 0.08   # REVERSAL(60-79) → +0.08
    BOOST_WATCH = 0.0       # WATCH(40-59) → 0
    PENALTY_NO_SIGNAL = -0.05  # NO_SIGNAL(<40) → -0.05

    # v132_score 上限
    V132_MAX = 0.95

    # M71评分≥此值时标记为"反转确认"
    M71_CONFIRM_THRESHOLD = 60.0

    # T4时段(14:15)运行完整M71, 其他时段可选运行
    FULL_RUN_PERIODS = {'14:15', '14:30'}

    # DB路径
    DB_PATH = 'data/holy_grail.db'


# ═══════════════════════════════════════════════════════════
# M71 集成核心
# ═══════════════════════════════════════════════════════════

class M71Enhancer:
    """M71全市场反转增强器 V3.6 — 31维度(D25三路+D28催化+D29洗盘+D30尾盘) + SECTOR_CRASH洗盘豁免 + 仓位集中度控制"""

    # V3.2: 仓位管理
    MAX_SINGLE_STOCK_PCT = 0.30  # 单只股票最大仓位30%
    MAX_SINGLE_STOCK_WARN = 0.25  # 单只股票仓位>25%时警告

    def __init__(self, db_path: str = None):
        self.predictor = ReversalPredictor()
        self.adapter = TDXDataAdapter()
        self.db_path = db_path or M71EnhanceConfig.DB_PATH
        self.results: List[Dict] = []
        self.enhanced_count = 0
        self.strong_reversal_count = 0
        self.position_warnings = []  # V3.2: 仓位警报

    def _detect_market_state(self, scanner) -> Dict:
        """
        V3.2: 检测市场状态（SECTOR_CRASH探测）
        
        从全市场扫描结果中统计涨跌比:
          - decline_ratio > 75%: SECTOR_CRASH
          - decline_ratio > 60%: MARKET_WEAK
        """
        all_stocks = scanner.all_stocks if scanner else {}
        if not all_stocks:
            return {'sector_crash': False, 'decline_ratio': 0, 'total_scanned': 0}

        up_count = 0
        down_count = 0
        for s in all_stocks.values():
            if hasattr(s, 'chg_pct') and s.chg_pct is not None:
                if s.chg_pct > 0:
                    up_count += 1
                elif s.chg_pct < 0:
                    down_count += 1

        total = up_count + down_count
        if total == 0:
            return {'sector_crash': False, 'decline_ratio': 0, 'total_scanned': 0}

        decline_ratio = down_count / total
        sector_crash = decline_ratio > 0.75
        market_weak = decline_ratio > 0.60

        return {
            'sector_crash': sector_crash,
            'market_weak': market_weak,
            'decline_ratio': round(decline_ratio, 4),
            'up_count': up_count,
            'down_count': down_count,
            'total_scanned': total,
        }

    def _check_position_concentration(self, stock, current_positions: Dict) -> List[str]:
        """
        V3.2: 仓位集中度检查

        Args:
            stock: 选出的股票
            current_positions: 当前持仓 {code: {'pct': 0.82, 'name': '高特电子'}}

        Returns:
            warnings: 持仓警报列表
        """
        warnings = []

        # 检查是否已有该股票持仓
        if stock.code in current_positions:
            existing = current_positions[stock.code]
            existing_pct = existing.get('pct', 0)
            if existing_pct > self.MAX_SINGLE_STOCK_PCT:
                warnings.append(
                    f'🚨 仓位超标: {stock.name}({stock.code}) 已占{existing_pct*100:.0f}%总仓位, '
                    f'超过单只上限{self.MAX_SINGLE_STOCK_PCT*100:.0f}%, 禁止加仓!'
                )
            elif existing_pct > self.MAX_SINGLE_STOCK_WARN:
                warnings.append(
                    f'⚠️ 仓位警告: {stock.name}({stock.code}) 已占{existing_pct*100:.0f}%总仓位, '
                    f'接近上限{self.MAX_SINGLE_STOCK_PCT*100:.0f}%'
                )

        # 检查整体持仓集中度
        for pos_code, pos_info in current_positions.items():
            if pos_code != stock.code and pos_info.get('pct', 0) > self.MAX_SINGLE_STOCK_PCT:
                warnings.append(
                    f'⚠️ 持仓集中度过高: {pos_info["name"]}({pos_code}) 占{pos_info["pct"]*100:.0f}%总仓位'
                )

        return warnings

    def enhance(
        self,
        scanner,
        period: str = '14:15',
        top_n: int = None,
        kline_fetcher=None,
        lhb_fetcher=None,
        flow_fetcher=None,
        quote_data: Dict = None,
        sentiment_data: Dict = None,
        current_positions: Dict = None,
        catalyst_data: Dict = None,  # V13.5.14: D28催化强度 (V13.5.18: wenda_notice_query)
        intraday_data: Dict = None,  # V13.5.16: D30尾盘量价
        capital_flow_history_data: Dict = None,  # V13.5.17: D31主力资金意图
        fundamental_data: Dict = None,  # V13.5.18: tdx_indicator_select基本面
        skill_enhancement_data: Dict = None,  # V13.5.18: Skills→M71集成增强数据
    ) -> Dict:
        """
        对全市场扫描结果执行M71反转信号增强 (V13.5.17 33维度)

        Args:
            scanner: FullMarketScanner 实例
            period: 时段标识
            top_n: 处理前N只候选股 (默认M71EnhanceConfig.TOP_N)
            kline_fetcher: 可选的K线数据获取函数 (code, setcode) → List[Dict]
            lhb_fetcher: 可选的龙虎榜数据获取函数 (code) → List[Dict]
            flow_fetcher: 可选的资金流数据获取函数 (code) → Dict
            quote_data: 实时行情数据 {code: quote_dict} (用于D15精度提升)
            sentiment_data: 舆情数据 {code: sentiment_dict} (用于D18/D19)
            current_positions: 当前持仓 {code: {pct, name}} (V3.2: 仓位集中度控制)
            catalyst_data: 催化剂数据 {code: catalyst_dict} (V3.4: D28催化强度)
            intraday_data: 尾盘量价数据 {code: intraday_dict} (V3.6: D30尾盘信号)
            capital_flow_history_data: 资金流向历史 {code: [flow_dict, ...]} (V13.5.17: D31主力资金意图)

        Returns:
            enhancement_summary: {enhanced, strong_reversal, reversals, top_m71, market_state, position_warnings}
        """
        if top_n is None:
            top_n = M71EnhanceConfig.TOP_N

        # 获取Top N候选 (按v132_score排序)
        all_scored = [
            s for s in scanner.all_stocks.values()
            if s.v132_score > 0
        ]
        all_scored.sort(key=lambda s: s.v132_score, reverse=True)
        candidates = all_scored[:top_n]

        if not candidates:
            scanner.log(f"  [M71] 无候选股可增强")
            return {'enhanced': 0, 'strong_reversal': 0, 'reversals': []}

        scanner.log(f"  [M71] 开始增强 Top {len(candidates)} 候选股 (period={period})")

        # V3.2: 检测市场状态
        market_state = self._detect_market_state(scanner)
        if market_state['sector_crash']:
            scanner.log(
                f"  🚨 [M71] SECTOR_CRASH模式: "
                f"全市场{market_state['decline_ratio']*100:.0f}%下跌, "
                f"启用智能降权(阈值提升+超跌打折)"
            )

        self.results = []
        self.enhanced_count = 0
        self.strong_reversal_count = 0
        self.position_warnings = []

        # V2.9: 预计算板块统计 (从scanner所有股票中)
        sector_stats = self._compute_sector_stats(scanner)

        for stock in candidates:
            try:
                # 构造M71输入数据
                klines_raw = self._get_klines(stock, kline_fetcher)
                lhb_raw = self._get_lhb(stock, lhb_fetcher)
                flow_raw = self._get_flow(stock, flow_fetcher)
                sector_raw = self._get_sector(stock, sector_stats)

                # 跳过数据不足的
                if not klines_raw or len(klines_raw) < 5:
                    # 用当前扫描数据构造简化K线
                    klines_raw = self._build_proxy_klines(stock)

                # 运行M71预测
                # V3.1: 传入实时行情和舆情数据
                stock_quote = quote_data.get(stock.code, {}) if quote_data else {}
                stock_sentiment = sentiment_data.get(stock.code, {}) if sentiment_data else {}

                pred = self.predictor.predict(
                    code=stock.code,
                    name=stock.name,
                    klines=self.adapter.parse_kline({'klines': klines_raw}),
                    lhb_data=self._parse_lhb_safe(lhb_raw),
                    capital_flow=self.adapter.parse_capital_flow(flow_raw) if flow_raw else CapitalFlow(),
                    sector_data=sector_raw,
                    quote_data=stock_quote,  # V3.1: 实时行情
                    sentiment_data=stock_sentiment,  # V3.1: 舆情数据 (V13.5.18: tdx_ai_listening)
                    market_state=market_state,  # V3.2: SECTOR_CRASH降权 (V13.5.18: wenda_macro_query)
                    catalyst_data=catalyst_data.get(stock.code, {}) if catalyst_data else None,  # V3.4: D28催化强度 (V13.5.18: wenda_notice_query)
                    intraday_data=intraday_data.get(stock.code, {}) if intraday_data else None,  # V3.6: D30尾盘量价
                    capital_flow_history=capital_flow_history_data.get(stock.code, []) if capital_flow_history_data else None,  # V13.5.17: D31主力资金意图
                    fundamental_data=fundamental_data.get(stock.code, {}) if fundamental_data else None,  # V13.5.18: tdx_indicator_select
                    skill_enhancement=skill_enhancement_data.get(stock.code, {}) if skill_enhancement_data else None,  # V13.5.18: Skills→M71集成增强
                )

                # V3.2: 仓位集中度检查
                if current_positions:
                    pos_warnings = self._check_position_concentration(stock, current_positions)
                    self.position_warnings.extend(pos_warnings)

                # 调整v132_score
                old_v132 = stock.v132_score
                boost = self._calc_boost(pred.total_score)
                new_v132 = min(
                    M71EnhanceConfig.V132_MAX,
                    max(0.01, old_v132 + boost)
                )
                stock.v132_score = round(new_v132, 4)

                # 标记M71结果
                if pred.total_score >= M71EnhanceConfig.M71_CONFIRM_THRESHOLD:
                    stock.alert_level = f"⚡ M71反转({pred.total_score:.0f}分)"
                    self.enhanced_count += 1

                if pred.grade == ReversalGrade.STRONG_REVERSAL:
                    stock.alert_level = f"🔥 M71强反转({pred.total_score:.0f}分)"
                    self.strong_reversal_count += 1

                # 存储M71结果到stock对象
                stock.m71_score = round(pred.total_score, 1)
                stock.m71_grade = pred.grade.value
                stock.m71_t1_upside = pred.t1_upside_pct
                stock.m71_action = pred.action
                stock.m71_similarity = pred.similarity
                stock.m71_confidence = pred.confidence

                # 收集结果
                result = prediction_to_dict(pred)
                result['old_v132'] = round(old_v132, 4)
                result['new_v132'] = round(new_v132, 4)
                result['boost'] = round(boost, 4)
                self.results.append(result)

                scanner.log(
                    f"  [M71] {stock.code} {stock.name}: "
                    f"M71={pred.total_score:.0f}({pred.grade.value}) "
                    f"v132 {old_v132:.3f}→{new_v132:.3f} "
                    f"相似度={pred.similarity:.0f}% "
                    f"动作={pred.action}"
                )

            except Exception as e:
                scanner.log(f"  [M71] {stock.code} {stock.name} 增强失败: {e}", "WARN")

        # 保存到DB
        self._save_to_db()

        # 按M71评分排序
        self.results.sort(key=lambda x: x['total_score'], reverse=True)

        # V13.5.19: 从dimensions提取D25-D36分数和五确认计数
        def _dim_score(r, name_keyword):
            for d in r.get('dimensions', []):
                if name_keyword in d.get('name', ''):
                    return d.get('actual_score', 0)
            return 0

        summary = {
            'enhanced': self.enhanced_count,
            'strong_reversal': self.strong_reversal_count,
            'total_processed': len(candidates),
            'market_state': market_state,  # V3.2: 市场状态
            'position_warnings': list(set(self.position_warnings)),  # V3.2: 去重仓位警报
            'reversals': [
                {
                    'code': r['code'],
                    'name': r['name'],
                    'm71_score': r['total_score'],
                    'grade': r['grade'],
                    'v132_boost': r['boost'],
                    'action': r['action'],
                    'similarity': r['similarity'],
                    't1_upside': r['t1_prediction']['upside_pct'],
                    'd25_score': _dim_score(r, '放量启动'),  # D25
                    'd26_score': _dim_score(r, '趋势延续'),  # D26
                    'd27_score': _dim_score(r, '低位蓄势'),  # D27
                    'd28_score': _dim_score(r, '催化'),  # D28
                    'd29_score': _dim_score(r, '洗盘'),  # D29
                    'd30_score': _dim_score(r, '尾盘'),  # D30
                    'd31_score': _dim_score(r, '主力'),  # D31
                    'd32_score': _dim_score(r, 'DDX'),  # D32
                    'd33_score': _dim_score(r, '外盘内盘'),  # D33
                    'd34_score': _dim_score(r, '拆单识别'),  # V13.5.19
                    'd35_score': _dim_score(r, '庄成本线'),  # V13.5.19
                    'd36_score': _dim_score(r, '委比异动'),  # V13.5.19
                    'five_confirm_count': r.get('five_confirm_count', 0),  # V13.5.19
                }
                for r in self.results if r['total_score'] >= 40
            ],
            'top_m71': self.results[:5] if self.results else [],
            'five_confirm_candidates': [  # V13.5.19: 五确认>=4的候选
                {
                    'code': r['code'],
                    'name': r['name'],
                    'm71_score': r['total_score'],
                    'five_confirm_count': r.get('five_confirm_count', 0),
                    'grade': r['grade'],
                }
                for r in self.results if r.get('five_confirm_count', 0) >= 4
            ],
        }

        # V3.2: 市场状态 + 仓位警告日志
        crash_info = ''
        if market_state['sector_crash']:
            crash_info = f' [🚨SECTOR_CRASH: {market_state["decline_ratio"]*100:.0f}%下跌]'
        pos_info = ''
        if self.position_warnings:
            pos_info = f' [⚠️仓位警告:{len(self.position_warnings)}条]'

        scanner.log(
            f"  [M71] 增强完成: {self.enhanced_count}只反转确认, "
            f"{self.strong_reversal_count}只强反转, "
            f"共{len(candidates)}只处理{crash_info}{pos_info}"
        )

        return summary

    def _calc_boost(self, m71_score: float) -> float:
        """根据M71评分计算v132调整量"""
        if m71_score >= 80:
            return M71EnhanceConfig.BOOST_STRONG
        elif m71_score >= 60:
            return M71EnhanceConfig.BOOST_REVERSAL
        elif m71_score >= 40:
            return M71EnhanceConfig.BOOST_WATCH
        else:
            return M71EnhanceConfig.PENALTY_NO_SIGNAL

    def _get_klines(self, stock, fetcher=None) -> List[Dict]:
        """获取K线数据"""
        if fetcher:
            try:
                return fetcher(stock.code, stock.market)
            except:
                pass

        # 从缓存文件加载
        kline_file = f"data/fullmarket_cache/kline_{stock.code}.json"
        if os.path.exists(kline_file):
            try:
                with open(kline_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

        return []

    def _get_lhb(self, stock, fetcher=None) -> List[Dict]:
        """获取龙虎榜数据 V3.0 — 优先读取TDX龙虎榜API缓存"""
        if fetcher:
            try:
                return fetcher(stock.code)
            except:
                pass

        # V3.0: 优先读取TDX龙虎榜API缓存 (由AI预获取)
        lhb_file = f"data/fullmarket_cache/lhb_{stock.code}.json"
        if os.path.exists(lhb_file):
            try:
                with open(lhb_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

        return []

    def _parse_lhb_safe(self, lhb_raw) -> list:
        """V3.0: 安全解析龙虎榜数据 — 兼容TDX表格格式 + 传统list格式"""
        if not lhb_raw:
            return []
        try:
            if isinstance(lhb_raw, dict) and 'tables' in lhb_raw:
                return self.adapter.parse_lhb(lhb_raw)
            elif isinstance(lhb_raw, list):
                return self.adapter.parse_lhb({'data': lhb_raw})
            elif isinstance(lhb_raw, dict):
                return self.adapter.parse_lhb(lhb_raw)
            else:
                return []
        except:
            return []

    def _get_flow(self, stock, fetcher=None) -> Dict:
        """获取资金流数据 V2.8 — 优先读取TDX资金流API缓存"""
        if fetcher:
            try:
                return fetcher(stock.code)
            except:
                pass

        # V2.8: 优先读取TDX资金流API缓存 (由AI预获取)
        flow_file = f"data/fullmarket_cache/flow_{stock.code}.json"
        if os.path.exists(flow_file):
            try:
                with open(flow_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

        # 从quotes缓存中提取资金流 (传统方式)
        quotes_file = f"data/fullmarket_cache/quotes_{stock.code}.json"
        if os.path.exists(quotes_file):
            try:
                with open(quotes_file, 'r', encoding='utf-8') as f:
                    q = json.load(f)
                    stat = q.get('StatInfo', {})
                    return {
                        'ddx': stat.get('DDX', 0),
                        'ddy': stat.get('DDY', 0),
                        'ddf': stat.get('DDF', 0),
                        'mainlx': stat.get('Mainlx', 0),
                        'main_10d': stat.get('Mainlx10day', 0),
                        'super_large_net': 0,
                    }
            except:
                pass

        return {}

    def _get_sector(self, stock, sector_stats: Dict = None) -> Dict:
        """
        V2.9获取板块数据 — 三级优先:
        1. TDX板块缓存文件 sector_{code}.json (AI预获取)
        2. scanner板块统计 sector_stats (从全市场扫描数据计算)
        3. stock对象属性 (兜底)
        """
        # V2.9 Priority 1: TDX板块缓存文件
        sector_file = f"data/fullmarket_cache/sector_{stock.code}.json"
        if os.path.exists(sector_file):
            try:
                with open(sector_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                    if cached and cached.get('total_count', 0) > 0:
                        return cached
            except:
                pass

        # V2.9 Priority 2: 从scanner统计计算
        if sector_stats:
            stock_sector = getattr(stock, 'sector', '') or '未知'
            stats = sector_stats.get(stock_sector, {})

            if stats:
                stock_decline = getattr(stock, 'decline_pct', 0)
                sector_avg = stats.get('avg_decline', 0)
                recovery_count = stats.get('recovery_count', 0)
                density = stats.get('count', 0)

                return {
                    'sector_chg': sector_avg,  # V2.9修复: 直接使用板块平均跌幅(负值=板块下跌)
                    'up_count': stats.get('up_count', 0),
                    'total_count': stats.get('count', 0),
                    # V2.9新增
                    'stock_decline': stock_decline,
                    'sector_avg_decline': sector_avg,
                    'recovery_count': recovery_count,
                    'sector_density': density,
                }

        # Priority 3: stock对象属性兜底
        return {
            'sector_chg': getattr(stock, 'sector_chg', 0),
            'up_count': getattr(stock, 'sector_up_count', 0),
            'total_count': getattr(stock, 'sector_total_count', 10),
        }

    def _compute_sector_stats(self, scanner) -> Dict:
        """
        V2.9: 从scanner.all_stocks计算板块统计
        - avg_decline: 板块平均跌幅
        - up_count: 跌幅<1%(接近持平或上涨)的股票数
        - recovery_count: 跌幅>-1%(已止跌)的股票数
        - count: 板块内股票总数
        """
        from collections import defaultdict

        sector_data = defaultdict(list)
        for s in scanner.all_stocks.values():
            sec = s.sector or '未知'
            sector_data[sec].append(s)

        stats = {}
        for sec, stocks in sector_data.items():
            declines = [s.decline_pct for s in stocks]
            avg_decline = sum(declines) / len(declines) if declines else 0
            up_count = sum(1 for d in declines if d > -0.5)  # 接近持平或上涨
            recovery_count = sum(1 for d in declines if d > -1.0)  # 跌幅<1%视为止跌

            stats[sec] = {
                'avg_decline': avg_decline,
                'up_count': up_count,
                'recovery_count': recovery_count,
                'count': len(stocks),
            }

        return stats

    def _build_proxy_klines(self, stock) -> List[Dict]:
        """
        当无K线数据时，用当前扫描数据构造代理K线
        (精度较低，但保证M71至少能运行部分维度)
        """
        today = datetime.now().strftime('%Y-%m-%d')
        decline = stock.decline_pct
        price = stock.price if stock.price > 0 else 10.0

        # 用跌幅反推近期K线 (粗略代理)
        proxy = []
        for i in range(10):
            d = decline * (1 - i * 0.1)  # 递减跌幅
            close = price / (1 + d / 100) if i > 0 else price
            vol_ratio = stock.volume_ratio * (0.8 + i * 0.03)

            proxy.append({
                'date': f'2026-06-{20+i:02d}',
                'open': close * 0.99,
                'high': close * 1.02,
                'low': close * 0.97,
                'close': close,
                'volume': 1000000 * vol_ratio,
                'chg_pct': d,
                'volume_ratio': vol_ratio,
            })

        return proxy

    def _build_proxy_klines_from_entry(self, entry: Dict) -> List[Dict]:
        """
        从state JSON的top_stocks条目构造代理K线
        (无真实K线时使用，精度较低)
        """
        decline = entry.get('decline_pct', -5.0)
        price = entry.get('price', 10.0)
        if price <= 0:
            price = 10.0
        vol_ratio = entry.get('volume_ratio', 1.0)
        amplitude = entry.get('amplitude', 8.0)

        proxy = []
        for i in range(10):
            # 递减跌幅模拟超跌过程
            d = decline * (1 - i * 0.08)
            close = price / (1 + d / 100) if i > 0 else price
            vr = max(0.3, vol_ratio * (0.7 + i * 0.04))

            proxy.append({
                'date': f'2026-06-{20+i:02d}',
                'open': close * 0.99,
                'high': close * (1 + amplitude / 200),
                'low': close * (1 - amplitude / 200),
                'close': close,
                'volume': 1000000 * vr,
                'chg_pct': d,
                'volume_ratio': vr,
            })

        return proxy

    def _save_to_db(self):
        """保存M71预测到数据库"""
        if not self.results:
            return

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS m71_reversal_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT,
                date TEXT NOT NULL,
                total_score REAL,
                grade TEXT,
                t1_price_low REAL,
                t1_price_mid REAL,
                t1_price_high REAL,
                t1_upside_pct REAL,
                t1_up_prob REAL,
                trend_3d_prob REAL,
                trend_5d_prob REAL,
                trend_7d_prob REAL,
                action TEXT,
                stop_loss REAL,
                target_price REAL,
                position_size TEXT,
                confidence REAL,
                pattern_match TEXT,
                similarity REAL,
                dimensions_json TEXT,
                risk_warnings_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        for r in self.results:
            t1 = r.get('t1_prediction', {})
            trend = r.get('trend_prediction', {})
            cursor.execute('''
                INSERT INTO m71_reversal_predictions
                (code, name, date, total_score, grade, t1_price_low, t1_price_mid,
                 t1_price_high, t1_upside_pct, t1_up_prob, trend_3d_prob, trend_5d_prob,
                 trend_7d_prob, action, stop_loss, target_price, position_size,
                 confidence, pattern_match, similarity, dimensions_json, risk_warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                r['code'], r['name'], r['date'], r['total_score'],
                r['grade'],
                t1.get('price_low', 0), t1.get('price_mid', 0), t1.get('price_high', 0),
                t1.get('upside_pct', 0), t1.get('up_prob', 0),
                trend.get('trend_3d_prob', 0), trend.get('trend_5d_prob', 0), trend.get('trend_7d_prob', 0),
                r['action'], r['stop_loss'], r['target_price'], r['position_size'],
                r['confidence'], r['pattern_match'], r['similarity'],
                json.dumps(r.get('dimensions', []), ensure_ascii=False),
                json.dumps(r.get('risk_warnings', []), ensure_ascii=False),
            ))

        conn.commit()
        conn.close()


# ═══════════════════════════════════════════════════════════
# 快捷入口 — 供自动化直接调用
# ═══════════════════════════════════════════════════════════

def enhance_with_m71(
    scanner,
    period: str = '14:15',
    top_n: int = None,
    kline_fetcher=None,
    lhb_fetcher=None,
    flow_fetcher=None,
    quote_data: Dict = None,
    sentiment_data: Dict = None,
    current_positions: Dict = None,
    catalyst_data: Dict = None,  # V13.5.14新增: 催化剂数据(D28) (V13.5.18: wenda_notice_query)
    intraday_data: Dict = None,  # V13.5.16新增: 尾盘量价数据(D30)
    capital_flow_history_data: Dict = None,  # V13.5.17新增: 资金流向历史(D31)
    fundamental_data: Dict = None,  # V13.5.18新增: tdx_indicator_select基本面
    skill_enhancement_data: Dict = None,  # V13.5.18新增: Skills→M71集成增强数据
) -> Dict:
    """
    M71增强快捷入口 — 在FullMarketScanner.score_stocks()之后调用

    V3.2新增参数:
        quote_data: {code: {open, close, ...}} 实时行情数据
        sentiment_data: {code: {d18_score, d19_score, ...}} 舆情数据
        current_positions: {code: {pct, name}} 当前持仓信息
    V3.4新增参数:
        catalyst_data: {code: {type, name, continuity_days, ...}} 催化剂数据(D28催化强度)
    V3.6新增参数:
        intraday_data: {code: {tail_volume_ratio, tail_bid_ratio, ...}} 尾盘量价数据(D30尾盘信号)
    V13.5.17新增参数:
        capital_flow_history_data: {code: [{date, main_net, main_pct, ...}, ...]} 资金流向多日历史(D31主力资金意图)

    用法 (在自动化prompt中):
    ```python
    from V13_4_FullMarketMonitor import get_global_scanner, run_fullmarket_scan, save_scanner_state
    from V13_5_M71_FullMarketIntegration import enhance_with_m71

    # 1. 正常运行全市场扫描
    summary = run_fullmarket_scan('14:15', 'data/fullmarket_cache/screener_t4_1415.json')

    # 2. M71增强 (含市场状态检测 + 仓位集中度)
    scanner = get_global_scanner()
    current_positions = {
        '301669': {'pct': 0.82, 'name': '高特电子'},
        '300024': {'pct': 0.15, 'name': '机器人'},
    }
    m71_summary = enhance_with_m71(scanner, period='14:15', top_n=20,
                                   current_positions=current_positions)

    # 3. 检查市场状态
    if m71_summary.get('market_state', {}).get('sector_crash'):
        print("🚨 崩盘日: M71信号已自动降权!")

    # 4. 检查仓位警告
    if m71_summary.get('position_warnings'):
        for w in m71_summary['position_warnings']:
            print(w)
    ```

    Returns:
        m71_summary: {enhanced, strong_reversal, reversals, top_m71, market_state, position_warnings}
    """
    enhancer = M71Enhancer()
    return enhancer.enhance(
        scanner=scanner,
        period=period,
        top_n=top_n,
        kline_fetcher=kline_fetcher,
        lhb_fetcher=lhb_fetcher,
        flow_fetcher=flow_fetcher,
        quote_data=quote_data,
        sentiment_data=sentiment_data,
        current_positions=current_positions,
        catalyst_data=catalyst_data,  # V13.5.14: D28催化强度 (V13.5.18: wenda_notice_query)
        intraday_data=intraday_data,  # V13.5.16: D30尾盘量价
        capital_flow_history_data=capital_flow_history_data,  # V13.5.17: D31主力资金意图
        fundamental_data=fundamental_data,  # V13.5.18: tdx_indicator_select基本面
        skill_enhancement_data=skill_enhancement_data,  # V13.5.18: Skills→M71集成增强
    )


# ═══════════════════════════════════════════════════════════
# V2.9: State文件板块统计辅助函数
# ═══════════════════════════════════════════════════════════

def _compute_state_sector_stats(top_stocks: List[Dict]) -> Dict:
    """V2.9: 从state的top_stocks计算板块统计"""
    from collections import defaultdict
    sector_data = defaultdict(list)
    for s in top_stocks:
        sec = s.get('sector', s.get('industry', '')) or '未知'
        sector_data[sec].append(s)

    stats = {}
    for sec, stocks in sector_data.items():
        declines = [s.get('decline_pct', 0) for s in stocks]
        avg_decline = sum(declines) / len(declines) if declines else 0
        up_count = sum(1 for d in declines if d > -0.5)
        recovery_count = sum(1 for d in declines if d > -1.0)

        stats[sec] = {
            'avg_decline': avg_decline,
            'up_count': up_count,
            'recovery_count': recovery_count,
            'count': len(stocks),
        }
    return stats


def _build_sector_from_state(stock_entry: Dict, sector_stats: Dict) -> Dict:
    """V2.9: 从state统计构建单只股票的板块数据"""
    stock_sector = stock_entry.get('sector', stock_entry.get('industry', '')) or '未知'
    stats = sector_stats.get(stock_sector, {})

    stock_decline = stock_entry.get('decline_pct', 0)
    sector_avg = stats.get('avg_decline', 0)

    return {
        'sector_chg': sector_avg,  # V2.9修复: 直接使用板块平均跌幅
        'up_count': stats.get('up_count', 0),
        'total_count': stats.get('count', 0),
        'stock_decline': stock_decline,
        'sector_avg_decline': sector_avg,
        'recovery_count': stats.get('recovery_count', 0),
        'sector_density': stats.get('count', 0),
    }


def enhance_state_file(
    state_file: str,
    top_n: int = 20,
    output_file: str = None,
) -> Dict:
    """
    从state JSON文件加载股票数据，执行M71增强，保存增强后的state

    适用于: 自动化任务中，先读取state_YYYYMMDD.json，再用M71增强

    Args:
        state_file: state_YYYYMMDD.json 路径
        top_n: 处理前N只
        output_file: 增强后的输出文件 (默认覆盖原文件)

    Returns:
        m71_summary
    """
    if not os.path.exists(state_file):
        print(f"[M71] State文件不存在: {state_file}")
        return {'enhanced': 0, 'strong_reversal': 0, 'reversals': []}

    with open(state_file, 'r', encoding='utf-8') as f:
        state = json.load(f)

    top_stocks = state.get('top_stocks', [])
    if not top_stocks:
        print(f"[M71] State中无top_stocks")
        return {'enhanced': 0, 'strong_reversal': 0, 'reversals': []}

    # 按v132排序取Top N
    top_stocks.sort(key=lambda x: x.get('v132_score', 0), reverse=True)
    candidates = top_stocks[:top_n]

    # V2.9: 从state计算板块统计
    state_sector_stats = _compute_state_sector_stats(top_stocks)

    enhancer = M71Enhancer()
    predictor = enhancer.predictor
    adapter = enhancer.adapter

    enhanced_count = 0
    strong_reversal_count = 0
    m71_results = []

    for stock_entry in candidates:
        code = stock_entry['code']
        name = stock_entry['name']

        try:
            # 构造代理K线 (从state数据) / V2.8: 优先读取kline缓存
            kline_file = f"data/fullmarket_cache/kline_{code}.json"
            if os.path.exists(kline_file):
                try:
                    with open(kline_file, 'r', encoding='utf-8') as f:
                        klines_raw = json.load(f)
                except:
                    klines_raw = enhancer._build_proxy_klines_from_entry(stock_entry)
            else:
                klines_raw = enhancer._build_proxy_klines_from_entry(stock_entry)
            klines = adapter.parse_kline({'klines': klines_raw})

            # V2.8: 优先读取TDX资金流API缓存
            flow_raw = None
            flow_file = f"data/fullmarket_cache/flow_{code}.json"
            if os.path.exists(flow_file):
                try:
                    with open(flow_file, 'r', encoding='utf-8') as f:
                        flow_raw = json.load(f)
                except:
                    pass
            if not flow_raw:
                flow_raw = {
                    'ddx': stock_entry.get('ddx', 0),
                    'ddf': stock_entry.get('ddf', 0),
                    'main_10d': stock_entry.get('main_10d', 0),
                }
            flow = adapter.parse_capital_flow(flow_raw)

            # V2.9: 板块数据 — 优先缓存文件, 其次state统计
            sector_file = f"data/fullmarket_cache/sector_{code}.json"
            if os.path.exists(sector_file):
                try:
                    with open(sector_file, 'r', encoding='utf-8') as f:
                        sector_raw = json.load(f)
                except:
                    sector_raw = _build_sector_from_state(stock_entry, state_sector_stats)
            else:
                sector_raw = _build_sector_from_state(stock_entry, state_sector_stats)

            # V3.0: 读取龙虎榜缓存文件 (TDX API表格格式)
            lhb_raw = None
            lhb_file = f"data/fullmarket_cache/lhb_{code}.json"
            if os.path.exists(lhb_file):
                try:
                    with open(lhb_file, 'r', encoding='utf-8') as f:
                        lhb_raw = json.load(f)
                except:
                    pass

            # V3.0: 解析龙虎榜数据 (兼容TDX表格 + 传统list)
            if lhb_raw:
                if isinstance(lhb_raw, dict) and 'tables' in lhb_raw:
                    lhb_entries = adapter.parse_lhb(lhb_raw)
                elif isinstance(lhb_raw, list):
                    lhb_entries = adapter.parse_lhb({'data': lhb_raw})
                else:
                    lhb_entries = adapter.parse_lhb(lhb_raw)
            else:
                lhb_entries = []

            pred = predictor.predict(
                code=code, name=name,
                klines=klines,
                lhb_data=lhb_entries,
                capital_flow=flow,
                sector_data=sector_raw,
            )

            # 调整v132
            old_v132 = stock_entry.get('v132_score', 0.5)
            boost = enhancer._calc_boost(pred.total_score)
            new_v132 = min(0.95, max(0.01, old_v132 + boost))
            stock_entry['v132_score'] = round(new_v132, 4)
            stock_entry['m71_score'] = round(pred.total_score, 1)
            stock_entry['m71_grade'] = pred.grade.value
            stock_entry['m71_action'] = pred.action
            stock_entry['m71_similarity'] = pred.similarity

            if pred.total_score >= 60:
                stock_entry['alert_level'] = f"⚡ M71反转({pred.total_score:.0f}分)"
                enhanced_count += 1

            if pred.grade == ReversalGrade.STRONG_REVERSAL:
                stock_entry['alert_level'] = f"🔥 M71强反转({pred.total_score:.0f}分)"
                strong_reversal_count += 1

            m71_results.append({
                'code': code, 'name': name,
                'm71_score': pred.total_score,
                'grade': pred.grade.value,
                'boost': round(boost, 4),
                'action': pred.action,
                'similarity': pred.similarity,
            })

        except Exception as e:
            print(f"[M71] {code} {name} 增强失败: {e}")

    # 保存M71结果到DB
    enhancer.results = m71_results
    # 保存增强后的state
    output_file = output_file or state_file
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    print(f"[M71] State增强完成: {enhanced_count}只反转确认, {strong_reversal_count}只强反转")
    print(f"[M71] 增强后state已保存: {output_file}")

    return {
        'enhanced': enhanced_count,
        'strong_reversal': strong_reversal_count,
        'total_processed': len(candidates),
        'reversals': [r for r in m71_results if r['m71_score'] >= 40],
        'top_m71': sorted(m71_results, key=lambda x: x['m71_score'], reverse=True)[:5],
    }


# ═══════════════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.5 M71 全市场反转预测集成层 — 自测")
    print("=" * 60)

    # 模拟state文件增强
    state_file = "data/fullmarket_cache/state_20260701.json"

    if os.path.exists(state_file):
        print(f"\n测试: 从 {state_file} 执行M71增强")
        summary = enhance_state_file(state_file, top_n=10)

        print(f"\n增强结果:")
        print(f"  反转确认: {summary['enhanced']}只")
        print(f"  强反转: {summary['strong_reversal']}只")
        print(f"  处理总数: {summary['total_processed']}只")

        if summary.get('top_m71'):
            print(f"\n  Top 5 M71:")
            for r in summary['top_m71']:
                print(f"    {r['code']} {r['name']}: M71={r['m71_score']:.0f} "
                      f"({r['grade']}) boost={r['boost']:+.3f} "
                      f"相似度={r['similarity']:.0f}%")
    else:
        print(f"State文件不存在: {state_file}")
        print("跳过自测 (需要全市场扫描state文件)")

    print(f"\n{'='*60}")
    print("M71 集成层自测完成")
    print(f"{'='*60}")
