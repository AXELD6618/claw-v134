#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.1 圣杯增强集成层 — HolyGrailIntegration                       ║
║  =====================================================             ║
║  将M56+M57+M59集成到V13.0 Orchestrator流水线                       ║
║                                                                      ║
║  升级流水线：                                                        ║
║  step_0   A股微观结构适配 (M59)        ← 新增：宇宙过滤+板别识别   ║
║  step_1   市场环境感知 (V13.0)                                     ║
║  step_2   TDX候选筛选 (V13.0)                                      ║
║  step_3   逐股数据采集 (V13.0)                                      ║
║  step_3.5 尾盘30分钟异动 (M56)         ← 新增：黄金半小时捕获      ║
║  step_4   四层递进流水线 (V13.0)                                   ║
║  step_5   贝叶斯概率计算 (V13.0)                                   ║
║  step_5.5 隔夜Alpha因子融合 (M57)      ← 新增：T→T+1专属因子       ║
║  step_6   主力意图过滤 (V13.0)                                     ║
║  step_7   7权重融合评分 (V13.0)                                    ║
║  step_8   仓位决策 (V13.0)                                          ║
║  step_9   日频参数校准 (V13.0)                                     ║
║  step_10  决策持久化                                                ║
║  step_11  结果推送                                                  ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Union


# ═══════════════════════════════════════════════════════════════
# 动态导入V13.1新模块
# ═══════════════════════════════════════════════════════════════

def _safe_import_v131(module_name: str, class_name: str):
    try:
        mod = __import__(module_name, fromlist=[class_name])
        return getattr(mod, class_name, None)
    except (ImportError, AttributeError) as e:
        print(f"⚠️ [V13.1] 模块 {module_name}.{class_name} 导入失败: {e}")
        return None


# 懒加载V13.1新模块
_Tail30MinEngine = None
_AShareMicrostructureEngine = None
_OvernightAlphaEngine = None
_BoardClassifier = None
_UniverseFilter = None


def _init_v131_modules():
    global _Tail30MinEngine, _AShareMicrostructureEngine, _OvernightAlphaEngine
    global _BoardClassifier, _UniverseFilter

    _Tail30MinEngine = _safe_import_v131('V13_1_M56_Tail30MinEngine', 'Tail30MinEngine')
    _AShareMicrostructureEngine = _safe_import_v131(
        'V13_1_M59_AShareMicrostructure', 'AShareMicrostructureEngine')
    _OvernightAlphaEngine = _safe_import_v131(
        'V13_1_M57_OvernightAlphaEngine', 'OvernightAlphaEngine')
    _BoardClassifier = _safe_import_v131(
        'V13_1_M59_AShareMicrostructure', 'BoardClassifier')
    _UniverseFilter = _safe_import_v131(
        'V13_1_M59_AShareMicrostructure', 'UniverseFilter')


@dataclass
class V131StockResult:
    """V13.1增强版单股分析结果"""
    code: str
    name: str = ''

    # M59 微观结构
    board: str = ''
    limit_up_pct: float = 0.10
    pass_filter: bool = False
    filter_details: List[str] = field(default_factory=list)

    # M56 尾盘30分钟
    tail_pattern: str = ''
    tail_grade: str = ''
    surge_score: float = 0.0
    gap_up_prob: float = 0.0
    limit_up_prob: float = 0.0
    sector_resonance: float = 0.0
    wash_trade_risk: float = 0.0

    # M57 隔夜Alpha
    alpha_composite: float = 0.0
    t1_return_forecast: float = 0.0

    # V13.0 原有评分
    v13_score: float = 0.0

    # 圣杯综合评分
    holy_grail_score: float = 0.0
    recommendation: str = ''

    # 详情
    warnings: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


class HolyGrailIntegrator:
    """
    V13.1 圣杯增强集成器

    整合M56+M57+M59，产出统一的圣杯评分
    """

    def __init__(self):
        _init_v131_modules()

        self.micro_engine = None
        self.tail_engine = None
        self.alpha_engine = None

        if _AShareMicrostructureEngine:
            self.micro_engine = _AShareMicrostructureEngine()
            print("✅ [V13.1] M59 微观结构引擎已加载")

        if _Tail30MinEngine:
            self.tail_engine = _Tail30MinEngine()
            print("✅ [V13.1] M56 尾盘30分钟引擎已加载")

        if _OvernightAlphaEngine:
            self.alpha_engine = _OvernightAlphaEngine()
            print("✅ [V13.1] M57 隔夜Alpha引擎已加载")

    # ── 单股完整分析（保留兼容性，per-stock evaluate） ──
    def analyze_stock(
        self,
        code: str,
        name: str = '',
        # V13.0评分（由Orchestrator提供）
        v13_score: float = 0.0,
        # M59所需
        listed_days: int = 9999,
        is_suspended: bool = False,
        avg_volume_yuan: float = 0,
        current_price: float = 0,
        prev_close: float = 0,
        has_data: bool = True,
        consecutive_limit_up: int = 0,
        # M56所需
        tail_1min_prices: List[float] = None,
        tail_1min_volumes: List[float] = None,
        prev_30_avg_volume: float = 0,
        ma20_price: float = None,
        high_20d_price: float = None,
        sector_data: Dict = None,
        # M57所需
        intraday_change_pct: float = 0,
        day_low_pct: float = 0,
        day_close_pct: float = 0,
        tail_30min_change_pct: float = 0,
        total_day_volume: float = 0,
        tail_30min_volume: float = 0,
        market_data: Dict = None,
    ) -> V131StockResult:
        """完整的V13.1增强分析（per-stock evaluate模式，兼容旧接口）"""

        # Phase 1: 计算M59+M56+M57因子
        result, factors = self._compute_factors(
            code, name, v13_score, listed_days, is_suspended,
            avg_volume_yuan, current_price, prev_close, has_data,
            consecutive_limit_up, tail_1min_prices, tail_1min_volumes,
            prev_30_avg_volume, ma20_price, high_20d_price, sector_data,
            intraday_change_pct, day_low_pct, day_close_pct,
            tail_30min_change_pct, total_day_volume, tail_30min_volume,
            market_data,
        )

        # 如果未通过M59过滤，直接返回
        if factors is None:
            return result

        # Phase 2: per-stock evaluate（无截面归一化）
        if self.alpha_engine:
            self.alpha_engine.evaluate(factors)
            result.alpha_composite = factors.composite_score
            result.t1_return_forecast = factors.t1_return_forecast

        # Phase 3: 圣杯评分
        result.v13_score = v13_score
        result.holy_grail_score = self._compute_holy_grail_score(result)
        result.recommendation = self._make_recommendation(result)

        return result

    # ── Phase 1: 因子计算（不evaluate） ──
    def _compute_factors(
        self,
        code: str,
        name: str,
        v13_score: float,
        listed_days: int,
        is_suspended: bool,
        avg_volume_yuan: float,
        current_price: float,
        prev_close: float,
        has_data: bool,
        consecutive_limit_up: int,
        tail_1min_prices: List[float],
        tail_1min_volumes: List[float],
        prev_30_avg_volume: float,
        ma20_price: float,
        high_20d_price: float,
        sector_data: Dict,
        intraday_change_pct: float,
        day_low_pct: float,
        day_close_pct: float,
        tail_30min_change_pct: float,
        total_day_volume: float,
        tail_30min_volume: float,
        market_data: Dict,
    ) -> Tuple[V131StockResult, Any]:
        """计算M59+M56+M57因子，返回(result, factors)。如未通过M59返回(result, None)"""

        result = V131StockResult(code=code, name=name)
        factors = None

        # ── Step 0: M59 微观结构适配 ──
        if self.micro_engine:
            micro_info = self.micro_engine.build_stock_info(
                code=code, name=name,
                listed_days=listed_days,
                is_suspended=is_suspended,
                avg_volume_yuan=avg_volume_yuan,
                current_price=current_price,
                prev_close=prev_close,
                has_data=has_data,
                consecutive_limit_up=consecutive_limit_up,
            )
            result.board = micro_info.board.value[0]
            result.limit_up_pct = micro_info.limit_up_pct
            result.pass_filter = micro_info.pass_filter
            result.filter_details = micro_info.filter_details

            if not micro_info.pass_filter:
                result.warnings.append('⚠️ 未通过宇宙过滤')
                result.recommendation = 'REJECT_宇宙过滤'
                return result, None

        # ── Step 3.5: M56 尾盘30分钟异动 ──
        if self.tail_engine and tail_1min_prices:
            tail_signal = self.tail_engine.analyze(
                code=code, name=name,
                tail_1min_prices=tail_1min_prices,
                tail_1min_volumes=tail_1min_volumes,
                prev_30_avg_volume=prev_30_avg_volume,
                ma20_price=ma20_price,
                high_20d_price=high_20d_price,
                sector_data=sector_data,
                market_data=market_data,
            )
            result.tail_pattern = tail_signal.pattern.value[0]
            result.tail_grade = tail_signal.grade.value[0]
            result.surge_score = tail_signal.surge_score
            result.gap_up_prob = tail_signal.gap_up_prob
            result.limit_up_prob = tail_signal.limit_up_prob
            result.sector_resonance = tail_signal.sector_resonance
            result.wash_trade_risk = tail_signal.wash_trade_risk
            result.warnings.extend(tail_signal.warnings)
            result.details.extend(tail_signal.details)

        # ── Step 5.5: M57 隔夜Alpha因子（仅计算，不evaluate） ──
        if self.alpha_engine:
            factors = self.alpha_engine.compute_all_factors(
                code=code,
                date=datetime.now().strftime('%Y-%m-%d'),
                intraday_change_pct=intraday_change_pct,
                tail_30min_change_pct=tail_30min_change_pct,
                day_low_pct=day_low_pct,
                day_close_pct=day_close_pct,
                tail_30min_volume=tail_30min_volume,
                total_day_volume=total_day_volume,
                tail_volumes=tail_1min_volumes,
                tail_prices=tail_1min_prices,
                sector_intraday_change=sector_data.get('change', 0) if sector_data else 0,
                consecutive_limit_up=consecutive_limit_up,
                market_tail_change_pct=market_data.get('tail_change', 0) if market_data else 0,
            )

        return result, factors

    # ── Phase 3: 圣杯评分 ──
    def _finalize_score(
        self,
        result: V131StockResult,
        factors: Any,
        v13_score: float,
    ) -> V131StockResult:
        """使用已evaluate的factors完成圣杯评分"""
        if factors is not None:
            result.alpha_composite = factors.composite_score
            result.t1_return_forecast = factors.t1_return_forecast

        result.v13_score = v13_score
        result.holy_grail_score = self._compute_holy_grail_score(result)
        result.recommendation = self._make_recommendation(result)
        return result

    def _compute_holy_grail_score(self, r: V131StockResult) -> float:
        """
        圣杯综合评分 — 融合V13.0 + M56 + M57 + M59

        权重分配（V13.1校准版）：
        - V13.0七权重评分（含D13反转突破）：35%
        - M56尾盘异动surge_score：30%（尾盘信号是T+1最强预测器）
        - M57隔夜Alpha：25%（Alpha因子截面区分度高）
        - M59微观结构扣分：-10%
        """
        score = 0.0

        # V13.0基础分
        score += r.v13_score * 0.35

        # M56尾盘分
        score += r.surge_score * 0.30

        # M57 Alpha分（归一化到0~1）
        alpha_normalized = (r.alpha_composite + 1) / 2  # [-1,1] → [0,1]
        score += alpha_normalized * 0.25

        # M59微观扣分
        micro_penalty = 0.10
        if not r.pass_filter:
            micro_penalty = 0.10
        else:
            micro_penalty = 0.0
        score -= micro_penalty

        # 对倒扣分
        score -= r.wash_trade_risk * 0.10

        return round(max(0.0, min(1.0, score)), 4)

    def _make_recommendation(self, r: V131StockResult) -> str:
        """交易建议 — V13.2召回率优化版
        
        阈值调整（P1-6）:
        原阈值: STRONG_BUY≥0.80 / BUY≥0.65 / WATCH≥0.45 / HOLD≥0.30
        新阈值: STRONG_BUY≥0.65 / BUY≥0.50 / WATCH≥0.35 / HOLD≥0.20
        目标: 召回率18%→50%+，F1→0.50+
        """
        if not r.pass_filter:
            return 'REJECT_过滤'

        # V13.2动态阈值（根据市场情绪调整）
        # 市场情绪高（涨停股>50只）→ 阈值提高5% → 更严格
        # 市场情绪低（涨停股<10只）→ 阈值降低5% → 更宽松
        market_sentiment = self._get_market_sentiment()
        threshold_adjust = 0.0
        if market_sentiment == 'high':
            threshold_adjust = 0.03   # 更严格
        elif market_sentiment == 'low':
            threshold_adjust = -0.03  # 更宽松

        base = r.holy_grail_score
        if base >= 0.65 + threshold_adjust:
            return 'STRONG_BUY_圣杯级别'
        elif base >= 0.50 + threshold_adjust:
            return 'BUY_高置信度'
        elif base >= 0.35 + threshold_adjust:
            return 'WATCH_可关注'
        elif base >= 0.20 + threshold_adjust:
            return 'HOLD_观望'
        else:
            return 'REJECT_评分不足'

    def _get_market_sentiment(self) -> str:
        """获取市场情绪（简化版）"""
        # TODO: 实际实现需要从TDX获取今日涨停股数量
        # 这里用模拟值
        return 'normal'

    # ── 批量分析（per-stock evaluate，旧版兼容） ──
    def batch_analyze(
        self,
        stock_list: List[Dict],
    ) -> List[V131StockResult]:
        """批量V13.1增强分析（per-stock evaluate，无截面归一化）"""
        results = []
        for stock in stock_list:
            try:
                r = self.analyze_stock(**stock)
                results.append(r)
            except Exception as e:
                print(f"⚠️ [V13.1] {stock.get('code', '?')} 分析失败: {e}")
        return results

    # ── 批量分析V2（截面归一化Alpha） ──
    def batch_analyze_v2(
        self,
        stock_list: List[Dict],
    ) -> List[V131StockResult]:
        """
        V13.1批量增强分析（截面归一化版）

        三阶段流程:
        Phase 1: 逐股计算M59+M56+M57因子（不evaluate）
        Phase 2: 对全部M57因子做截面归一化(Winsorize+Z-score) → batch_evaluate
        Phase 3: 用归一化后的Alpha完成圣杯评分

        相比batch_analyze的优势:
        - Alpha因子做截面排名，消除同涨同跌偏差
        - 涨停股Alpha不再全部饱和=1.0，实现有效区分
        - 真正实现"A股截面选股"而非"绝对评分"
        """
        # Phase 1: 逐股计算因子
        phase1_results = []  # [(result, factors, v13_score, stock_dict)]
        for stock in stock_list:
            try:
                result, factors = self._compute_factors(
                    code=stock.get('code', ''),
                    name=stock.get('name', ''),
                    v13_score=stock.get('v13_score', 0.0),
                    listed_days=stock.get('listed_days', 9999),
                    is_suspended=stock.get('is_suspended', False),
                    avg_volume_yuan=stock.get('avg_volume_yuan', 0),
                    current_price=stock.get('current_price', 0),
                    prev_close=stock.get('prev_close', 0),
                    has_data=stock.get('has_data', True),
                    consecutive_limit_up=stock.get('consecutive_limit_up', 0),
                    tail_1min_prices=stock.get('tail_1min_prices'),
                    tail_1min_volumes=stock.get('tail_1min_volumes'),
                    prev_30_avg_volume=stock.get('prev_30_avg_volume', 0),
                    ma20_price=stock.get('ma20_price'),
                    high_20d_price=stock.get('high_20d_price'),
                    sector_data=stock.get('sector_data'),
                    intraday_change_pct=stock.get('intraday_change_pct', 0),
                    day_low_pct=stock.get('day_low_pct', 0),
                    day_close_pct=stock.get('day_close_pct', 0),
                    tail_30min_change_pct=stock.get('tail_30min_change_pct', 0),
                    total_day_volume=stock.get('total_day_volume', 0),
                    tail_30min_volume=stock.get('tail_30min_volume', 0),
                    market_data=stock.get('market_data'),
                )
                phase1_results.append((result, factors, stock.get('v13_score', 0.0)))
            except Exception as e:
                print(f"⚠️ [V13.1] {stock.get('code', '?')} Phase1失败: {e}")
                # 创建一个REJECT结果
                r = V131StockResult(code=stock.get('code', ''), name=stock.get('name', ''))
                r.recommendation = 'REJECT_异常'
                r.warnings.append(f'Phase1异常: {e}')
                phase1_results.append((r, None, 0.0))

        # Phase 2: 截面归一化 + batch_evaluate
        valid_factors = [f for _, f, _ in phase1_results if f is not None]
        if valid_factors and self.alpha_engine:
            print(f"  📊 [V13.1] 截面归一化: {len(valid_factors)}个Alpha因子 → Winsorize+Z-score")
            self.alpha_engine.batch_evaluate(valid_factors)

        # Phase 3: 圣杯评分
        results = []
        for result, factors, v13_score in phase1_results:
            if factors is not None:
                self._finalize_score(result, factors, v13_score)
            results.append(result)

        return results

    # ── 圣杯Top-N报告 ──
    def generate_holy_grail_report(
        self,
        results: List[V131StockResult],
        top_n: int = 20,
    ) -> str:
        """生成圣杯选股报告"""
        # 按圣杯评分排序
        sorted_results = sorted(results, key=lambda r: r.holy_grail_score, reverse=True)

        # 统计
        strong_buy = sum(1 for r in sorted_results if r.recommendation == 'STRONG_BUY_圣杯级别')
        buy = sum(1 for r in sorted_results if r.recommendation == 'BUY_高置信度')
        watch = sum(1 for r in sorted_results if r.recommendation == 'WATCH_可关注')

        lines = [
            '=' * 70,
            '  🏆 V13.1 圣杯选股报告 — Holy Grail Stock Selection',
            f'  生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}',
            f'  覆盖股票：{len(results)} | 圣杯级别：{strong_buy} | 高置信：{buy} | 可关注：{watch}',
            '=' * 70,
        ]

        for rank, r in enumerate(sorted_results[:top_n], 1):
            emoji = '🏆' if r.recommendation == 'STRONG_BUY_圣杯级别' else \
                    '🔥' if r.recommendation == 'BUY_高置信度' else \
                    '👀' if r.recommendation == 'WATCH_可关注' else '  '

            lines.append(f'\n{emoji} #{rank} {r.code} {r.name}')
            lines.append(f'   圣杯评分：{r.holy_grail_score:.4f} | 建议：{r.recommendation}')
            lines.append(f'   板别：{r.board} | 涨跌停：±{r.limit_up_pct*100:.0f}%')
            lines.append(f'   V13.0评分：{r.v13_score:.3f} | M56尾盘：{r.surge_score:.3f} '
                         f'| M57 Alpha：{r.alpha_composite:+.3f}')
            lines.append(f'   尾盘模式：{r.tail_pattern} | 等级：{r.tail_grade}')
            lines.append(f'   高开概率：{r.gap_up_prob:.1%} | 涨停概率：{r.limit_up_prob:.1%}')
            lines.append(f'   T+1预期：{r.t1_return_forecast:+.3f}% | 共振：{r.sector_resonance:.2f}')
            if r.warnings:
                for w in r.warnings[:2]:
                    lines.append(f'   {w}')

        lines.append(f'\n{"=" * 70}')
        lines.append(f'  📊 统计摘要')
        lines.append(f'  圣杯级别(≥0.80)：{strong_buy} 只')
        lines.append(f'  高置信度(≥0.65)：{buy} 只')
        lines.append(f'  可关注(≥0.45)：{watch} 只')
        lines.append(f'{"=" * 70}')

        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════════════
# SECTION 2: V13.0 Orchestrator 补丁
# ═══════════════════════════════════════════════════════════════

class V131OrchestratorPatch:
    """
    V13.0 → V13.1 调度器升级补丁

    在V13Orchestrator.run_daily_tail_market()中插入M56+M57+M59

    使用方式：
        # 原V13.0调用
        orchestrator = V13Orchestrator()
        report = orchestrator.run_daily_tail_market()

        # V13.1增强调用
        patch = V131OrchestratorPatch()
        enhanced_results = patch.enhance(original_results)
    """

    def __init__(self):
        self.integrator = HolyGrailIntegrator()

    def enhance(
        self,
        original_results: List[Dict],
        stock_data_map: Dict[str, Dict] = None,
        use_cross_section: bool = True,
    ) -> List[V131StockResult]:
        """
        对V13.0原始结果进行V13.1增强

        Args:
            original_results: V13.0分析结果列表 [{code, name, v13_score, ...}]
            stock_data_map: {code: {tail_prices, tail_volumes, ...}} 原始数据映射
            use_cross_section: True=使用截面归一化(batch_analyze_v2),
                              False=per-stock evaluate(batch_analyze)

        Returns:
            V131StockResult列表
        """
        # 构建stock_list供batch分析
        stock_list = []
        for r in original_results:
            code = r.get('code', '')
            name = r.get('name', '')
            stock_data = stock_data_map.get(code, {}) if stock_data_map else {}

            stock_params = {
                'code': code,
                'name': name,
                'v13_score': r.get('score', r.get('v13_score', 0.5)),
                'listed_days': stock_data.get('listed_days', 9999),
                'is_suspended': stock_data.get('is_suspended', False),
                'avg_volume_yuan': stock_data.get('avg_volume_yuan', 0),
                'current_price': stock_data.get('current_price', r.get('current_price', 0)),
                'prev_close': stock_data.get('prev_close', 0),
                'has_data': stock_data.get('has_data', True),
                'consecutive_limit_up': stock_data.get('consecutive_limit_up', 0),
                'tail_1min_prices': stock_data.get('tail_prices'),
                'tail_1min_volumes': stock_data.get('tail_volumes'),
                'prev_30_avg_volume': stock_data.get('prev_30_avg_vol', 0),
                'ma20_price': stock_data.get('ma20'),
                'high_20d_price': stock_data.get('high_20d'),
                'sector_data': stock_data.get('sector_data'),
                'intraday_change_pct': stock_data.get('intraday_change_pct', 0),
                'day_low_pct': stock_data.get('day_low_pct', 0),
                'day_close_pct': stock_data.get('day_close_pct', 0),
                'tail_30min_change_pct': stock_data.get('tail_30min_change_pct', 0),
                'total_day_volume': stock_data.get('total_day_volume', 0),
                'tail_30min_volume': stock_data.get('tail_30min_volume', 0),
                'market_data': stock_data.get('market_data'),
            }
            stock_list.append(stock_params)

        if use_cross_section:
            return self.integrator.batch_analyze_v2(stock_list)
        else:
            return self.integrator.batch_analyze(stock_list)

    def run_standalone_14_30(self, stock_data_map: Dict[str, Dict]) -> List[V131StockResult]:
        """
        独立运行14:30尾盘选股（不依赖V13.0 Orchestrator）

        用于快速验证或自动化定时任务
        """
        stock_list = []
        for code, data in stock_data_map.items():
            stock_list.append({
                'code': code,
                'name': data.get('name', ''),
                'v13_score': data.get('v13_score', 0.5),
                'listed_days': data.get('listed_days', 9999),
                'is_suspended': data.get('is_suspended', False),
                'avg_volume_yuan': data.get('avg_volume_yuan', 0),
                'current_price': data.get('current_price', data.get('price', 0)),
                'prev_close': data.get('prev_close', 0),
                'has_data': data.get('has_data', True),
                'consecutive_limit_up': data.get('consecutive_limit_up', 0),
                'tail_1min_prices': data.get('tail_prices'),
                'tail_1min_volumes': data.get('tail_volumes'),
                'prev_30_avg_volume': data.get('prev_30_avg_vol', 0),
                'ma20_price': data.get('ma20'),
                'high_20d_price': data.get('high_20d'),
                'sector_data': data.get('sector_data'),
                'intraday_change_pct': data.get('intraday_change_pct', 0),
                'day_low_pct': data.get('day_low_pct', 0),
                'day_close_pct': data.get('day_close_pct', 0),
                'tail_30min_change_pct': data.get('tail_30min_change_pct', 0),
                'total_day_volume': data.get('total_day_volume', 0),
                'tail_30min_volume': data.get('tail_30min_volume', 0),
                'market_data': data.get('market_data'),
            })

        return self.integrator.batch_analyze(stock_list)


# ═══════════════════════════════════════════════════════════════
# SECTION 3: 快速验证
# ═══════════════════════════════════════════════════════════════

def quick_validation():
    """V13.1模块快速验证"""
    print("=" * 60)
    print("V13.1 模块验证")
    print("=" * 60)

    # M59 验证
    try:
        from V13_1_M59_AShareMicrostructure import BoardClassifier, UniverseFilter
        board = BoardClassifier.classify('300418', '昆仑万维')
        print(f"✅ M59 板别识别: 300418 → {board.value[0]}")
        board2 = BoardClassifier.classify('835185', '贝特瑞')
        print(f"✅ M59 板别识别: 835185 → {board2.value[0]}")
        board3 = BoardClassifier.classify('688111', '金山办公')
        print(f"✅ M59 板别识别: 688111 → {board3.value[0]}")
    except Exception as e:
        print(f"❌ M59 验证失败: {e}")

    # M56 验证
    try:
        from V13_1_M56_Tail30MinEngine import Tail30MinEngine, TailPattern, SignalGrade
        engine = Tail30MinEngine()
        # 模拟放量拉升
        prices = [10.0, 10.02, 10.03, 10.05, 10.06, 10.08, 10.09, 10.12, 10.13, 10.15,
                  10.17, 10.18, 10.19, 10.21, 10.22, 10.24, 10.25, 10.28, 10.30, 10.32,
                  10.33, 10.35, 10.37, 10.38, 10.40, 10.42, 10.44, 10.45, 10.48, 10.50]
        volumes = [100, 120, 110, 130, 140, 150, 160, 180, 170, 200,
                   220, 210, 230, 250, 260, 280, 290, 300, 320, 330,
                   350, 370, 380, 390, 410, 430, 440, 450, 470, 500]
        signal = engine.analyze(
            code='300418', name='昆仑万维',
            tail_1min_prices=prices, tail_1min_volumes=volumes,
            prev_30_avg_volume=120, ma20_price=9.8,
            high_20d_price=11.5,
            sector_data={'change': 1.5, 'volume_ratio': 1.8, 'up_count': 25, 'total': 40, 'leader_change': 3.0},
        )
        print(f"✅ M56 尾盘分析: 模式={signal.pattern.value[0]}, 等级={signal.grade.value[0]}")
        print(f"   高开概率={signal.gap_up_prob:.1%}, 预期收益={signal.expected_return:+.2f}%")
    except Exception as e:
        print(f"❌ M56 验证失败: {e}")

    # M57 验证
    try:
        from V13_1_M57_OvernightAlphaEngine import OvernightAlphaEngine
        alpha = OvernightAlphaEngine()
        factors = alpha.compute_all_factors(
            code='300418',
            date='2026-06-23',
            intraday_change_pct=3.5,
            tail_30min_change_pct=2.0,
            day_low_pct=-1.0,
            day_close_pct=3.5,
            today_gap_pct=0.5,
            gap_direction=1,
            tail_30min_volume=5000000,
            total_day_volume=30000000,
            tail_volumes=[100, 120, 130]*10,
            tail_prices=[p/10 for p in range(100, 130)],
            sector_intraday_change=1.5,
            consecutive_limit_up=0,
            market_tail_change_pct=0.5,
        )
        alpha.evaluate(factors)
        print(f"✅ M57 Alpha因子: 综合评分={factors.composite_score:+.4f}, T+1预期={factors.t1_return_forecast:+.3f}%")
        print(f"   尾盘RS={factors.tail_rs:+.3f}, 日内反转={factors.intraday_rev:+.3f}")
    except Exception as e:
        print(f"❌ M57 验证失败: {e}")

    # 集成验证
    try:
        integrator = HolyGrailIntegrator()
        result = integrator.analyze_stock(
            code='300418', name='昆仑万维',
            v13_score=0.72,
            listed_days=3650, current_price=10.50, prev_close=10.15,
            avg_volume_yuan=5e8, has_data=True,
            tail_1min_prices=[p/100 for p in range(1000, 1050)],
            tail_1min_volumes=[100]*30,
            prev_30_avg_volume=80,
            ma20_price=9.8, high_20d_price=12.0,
            sector_data={'change': 1.5, 'volume_ratio': 1.5, 'up_count': 30, 'total': 50, 'leader_change': 2.5},
            intraday_change_pct=3.5, tail_30min_change_pct=2.0,
            day_low_pct=-0.5, day_close_pct=3.5,
            total_day_volume=3e7, tail_30min_volume=5e6,
        )
        print(f"✅ 圣杯集成: 评分={result.holy_grail_score:.4f}, 建议={result.recommendation}")
    except Exception as e:
        print(f"❌ 集成验证失败: {e}")

    print("=" * 60)
    print("V13.1 模块验证完成")


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 导出
# ═══════════════════════════════════════════════════════════════

__all__ = [
    'V131StockResult', 'HolyGrailIntegrator', 'V131OrchestratorPatch',
    'quick_validation',
]


if __name__ == '__main__':
    quick_validation()
