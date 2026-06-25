#!/usr/bin/env python3
"""
V13.0 统一调度器（Orchestrator）
================================
S-1 阻塞项解决：串联全部10+模块，端到端自动化选股流水线

架构：
  ┌──────────────────────────────────────────────────┐
  │                  V13.0 Orchestrator               │
  │                                                   │
  │  step_1  市场环境感知 (MarketEnv)                  │
  │  step_2  TDX候选筛选 (DataPipeline→TdxScreener)   │
  │  step_3  逐股数据采集 (DataPipeline→per-stock)    │
  │  step_4  四层递进流水线 (TailMarketUltimate)      │
  │  step_5  贝叶斯概率计算 (M46)                     │
  │  step_6  主力意图过滤 (M51)                       │
  │  step_7  7权重融合评分 (7WeightFusion)            │
  │  step_8  仓位决策 (M54)                           │
  │  step_9  日频参数校准 (M55)                       │
  │  step_10 决策持久化 (SQLite)                      │
  │  step_11 结果推送 (WeChat/终端)                   │
  │                                                   │
  │  + 回测验证模式 (BacktestMode)                    │
  │  + 每日汇总生成 (DailySummary)                    │
  └──────────────────────────────────────────────────┘

使用方式：
  # 每日自动选股（供14:30自动化调用）
  orchestrator = V13Orchestrator()
  report = orchestrator.run_daily_tail_market()

  # 周末回测验证
  orchestrator.run_weekly_backtest()

  # 单股诊断
  report = orchestrator.diagnose_stock('002415', '海康威视')
"""

import json
import time
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum


# 动态导入模块（允许部分缺失）
def _safe_import(module_name: str, class_name: str):
    try:
        mod = __import__(module_name, fromlist=[class_name])
        return getattr(mod, class_name, None)
    except (ImportError, AttributeError) as e:
        print(f"⚠️ 模块 {module_name}.{class_name} 导入失败: {e}")
        return None


# 各模块懒加载
TailMarketUltimate = None
T1TailScreener = None
PatternDetector = None
TrapDetector = None
M46BayesianEngine = None
M51NoiseFilter = None
SevenWeightFusionV2 = None
M55DailyCalibrator = None
M54PositionEngine = None
DataPipeline = None
PersistenceManager = None
BacktestEngine = None
MLEngine = None
WeChatPusher = None
WatchdogDaemon = None
AltDataSource = None


def _init_modules():
    global TailMarketUltimate, T1TailScreener, PatternDetector, TrapDetector
    global M46BayesianEngine, M51NoiseFilter, SevenWeightFusionV2
    global M55DailyCalibrator, M54PositionEngine
    global DataPipeline, PersistenceManager, BacktestEngine
    global MLEngine, WeChatPusher, WatchdogDaemon, AltDataSource

    TailMarketUltimate = _safe_import('V13_0_TailMarket_Ultimate', 'TailMarketUltimate')
    T1TailScreener = _safe_import('V13_0_TailMarket_Ultimate', 'T1TailScreener')
    PatternDetector = _safe_import('V13_0_TailMarket_Ultimate', 'PatternDetector')
    TrapDetector = _safe_import('V13_0_TailMarket_Ultimate', 'TrapDetector')
    M46BayesianEngine = _safe_import('V13_0_M46_BayesianEngine', 'M46BayesianEngine')
    M51NoiseFilter = _safe_import('V13_0_M51_IntentInference', 'M51NoiseFilter')
    SevenWeightFusionV2 = _safe_import('V13_0_7WeightFusion', 'SevenWeightFusionV2')
    M55DailyCalibrator = _safe_import('V13_0_M55_DailyCalibrator', 'M55DailyCalibrator')
    M54PositionEngine = _safe_import('V13_0_M54_PositionEngine', 'M54PositionEngine')
    PipelineCls = _safe_import('V13_0_DataPipeline', 'DataPipeline')
    if PipelineCls:
        DataPipeline = PipelineCls
    PersistenceCls = _safe_import('V13_0_Persistence', 'PersistenceManager')
    if PersistenceCls:
        PersistenceManager = PersistenceCls
    BackendCls = _safe_import('V13_0_BacktestEngine', 'BacktestEngine')
    if BackendCls:
        BackendClsCls = BackendCls
        BacktestEngine = BackendCls
    else:
        BacktestEngine = None
    MLCls = _safe_import('V13_0_ML_Engine', 'MLEngine')
    if MLCls:
        MLEngine = MLCls
    WeChatCls = _safe_import('V13_0_WeChatPusher', 'WeChatPusher')
    if WeChatCls:
        WeChatPusher = WeChatCls
    WatchdogCls = _safe_import('V13_0_Watchdog', 'WatchdogDaemon')
    if WatchdogCls:
        WatchdogDaemon = WatchdogCls
    AltDataCls = _safe_import('V13_0_AltDataSources', 'AltDataSource')
    if AltDataCls:
        AltDataSource = AltDataCls


# ═══════════════════════════════════════════════
# 运行模式
# ═══════════════════════════════════════════════

class RunMode(Enum):
    DAILY_TAIL = 'daily_tail'           # 每日14:30尾盘选股
    MORNING_REVIEW = 'morning_review'    # 11:30午间速报
    WEEKLY_BACKTEST = 'weekly_backtest'  # 周末回测
    SINGLE_DIAGNOSE = 'single_diagnose'  # 单股诊断
    FULL_SCAN = 'full_scan'              # 全市场扫描
    PARAM_GRID_SEARCH = 'param_grid'     # 参数网格搜索


@dataclass
class OrchestratorConfig:
    """调度器配置"""
    # 模块开关
    enable_tail_market: bool = True      # 尾盘选股主引擎
    enable_m46: bool = True              # 贝叶斯概率
    enable_m51: bool = True              # 主力意图
    enable_m54: bool = True              # 仓位决策
    enable_m55: bool = True              # 日频校准
    enable_persistence: bool = True      # 持久化
    enable_wechat_push: bool = True      # 微信推送（兜底模式可用）
    enable_ml_engine: bool = True        # ML智能辅助
    enable_alt_data: bool = True         # 另类数据因子
    enable_watchdog: bool = True         # 看门狗守护

    # ── 数据模式（★ 实盘优先）──
    #   "tdx_real"   — 优先TDX实盘数据，无缓存时输出采集计划+临时降级合成
    #   "synthetic"  — 纯合成数据演练
    #   "auto"       — 有缓存用缓存，无缓存直接用合成（不阻塞）
    data_mode: str = 'tdx_real'
    tdx_cache_dir: str = 'data'              # TDX缓存文件目录
    tdx_cache_ttl_sec: int = 900             # 缓存有效期（15分钟）
    tdx_cache_file: str = 'tdx_realtime_input.json'

    # 选股参数
    max_candidates: int = 50             # 最大候选数
    max_buy_signals: int = 5             # 最大买入信号数
    min_total_score: float = 0.50        # 最低综合评分
    min_m46_confidence: str = '中'      # 最低贝叶斯置信度

    # 回测参数
    backtest_lookback_days: int = 60
    backtest_synthetic_stocks: int = 50

    # 数据
    data_dir: str = 'data'

    # 日志
    verbose: bool = True
    log_file: str = None


@dataclass
class PipelineStats:
    """流水线执行统计"""
    total_time_ms: float = 0.0
    step_times: Dict[str, float] = field(default_factory=dict)
    stocks_processed: int = 0
    stocks_passed_l1: int = 0
    stocks_passed_l2: int = 0
    stocks_passed_l3: int = 0
    stocks_buy_signal: int = 0
    errors: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════
# 统一调度器核心
# ═══════════════════════════════════════════════

class V13Orchestrator:
    """
    V13.0 统一调度器

    串联全模块，提供统一的端到端选股接口。
    设计目标：一个 run_daily_tail_market() 方法完成全部流程。
    """

    def __init__(self, config: OrchestratorConfig = None):
        self.config = config or OrchestratorConfig()
        _init_modules()
        self._init_engines()
        self.stats = PipelineStats()

    def _init_engines(self):
        """初始化各引擎实例"""
        # 主引擎
        self.tail_engine = None
        if TailMarketUltimate and self.config.enable_tail_market:
            self.tail_engine = TailMarketUltimate()

        # 贝叶斯
        self.m46_engine = None
        if M46BayesianEngine and self.config.enable_m46:
            self.m46_engine = M46BayesianEngine()

        # 主力意图
        self.m51_engine = None
        if M51NoiseFilter and self.config.enable_m51:
            self.m51_engine = M51NoiseFilter()

        # 7权重
        self.fusion_engine = None
        if SevenWeightFusionV2:
            self.fusion_engine = SevenWeightFusionV2()

        # 仓位
        self.m54_engine = None
        if M54PositionEngine and self.config.enable_m54:
            self.m54_engine = M54PositionEngine()

        # 校准
        self.m55_engine = None
        if M55DailyCalibrator and self.config.enable_m55:
            self.m55_engine = M55DailyCalibrator()

        # 数据管线
        self.data_pipeline = None
        if DataPipeline:
            self.data_pipeline = DataPipeline()

        # 持久化
        self.persistence = None
        if PersistenceManager and self.config.enable_persistence:
            db_path = os.path.join(self.config.data_dir, 'v13_decisions.db')
            self.persistence = PersistenceManager(db_path)

        # 回测引擎（延迟初始化）
        self.backtest_engine = None

        # ML智能辅助
        self.ml_engine = None
        if MLEngine and self.config.enable_ml_engine:
            self.ml_engine = MLEngine()

        # 微信推送
        self.wechat_pusher = None
        if WeChatPusher and self.config.enable_wechat_push:
            from V13_0_WeChatPusher import PushConfig, WeChatPusher as WCP
            self.wechat_pusher = WCP(PushConfig())

        # 看门狗
        self.watchdog = None
        if WatchdogDaemon and self.config.enable_watchdog:
            self.watchdog = WatchdogDaemon(
                push_callback=self.wechat_pusher.push if self.wechat_pusher else None
            )

        # 另类数据
        self.alt_data = None
        if AltDataSource and self.config.enable_alt_data:
            self.alt_data = AltDataSource()

    def _log(self, msg: str):
        if self.config.verbose:
            print(f"[V13.O] {msg}")
        if self.config.log_file:
            os.makedirs(os.path.dirname(self.config.log_file) or '.', exist_ok=True)
            with open(self.config.log_file, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now().isoformat()} {msg}\n")

    # ═══════════════════════════════════════════════
    # 主入口：每日尾盘选股
    # ═══════════════════════════════════════════════

    def run_daily_tail_market(
        self,
        candidates: List[dict] = None,
        market_env: dict = None,
    ) -> dict:
        """
        执行完整的每日尾盘选股流程

        输入 candidates（可选）:
        [{code, name, quote, kline, weekly_kline, indicator, ...}]

        如果 candidates=None，使用合成数据做演练。
        实盘模式下，candidates 由外部 TDX MCP 调用方注入。

        返回: 完整执行报告
        """
        t_start = time.time()
        self._log("=" * 50)
        self._log(f"🔄 V13.0 尾盘选股启动 — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._log("=" * 50)

        self.stats = PipelineStats()

        # ── Step 1: 市场环境感知 ──
        t1 = time.time()
        env = self._step1_market_env(market_env)
        self.stats.step_times['1_market_env'] = round((time.time() - t1) * 1000, 0)

        # ── Step 2: 候选准备（★ TDX实盘优先）──
        t2 = time.time()
        stocks, tdx_status = self._step2_prepare_candidates(candidates)
        self.stats.step_times['2_prepare_candidates'] = round((time.time() - t2) * 1000, 0)
        self._last_data_source = tdx_status

        # ── Step 3: 四层递进流水线 ──
        t3 = time.time()
        pipeline_results = self._step3_pipeline(stocks, env)
        self.stats.step_times['3_pipeline'] = round((time.time() - t3) * 1000, 0)

        # ── Step 4: M46 贝叶斯概率 ──
        t4 = time.time()
        pipeline_results = self._step4_bayesian(pipeline_results, env)
        self.stats.step_times['4_m46_bayesian'] = round((time.time() - t4) * 1000, 0)

        # ── Step 5: M51 主力意图过滤 ──
        t5 = time.time()
        pipeline_results = self._step5_intent_filter(pipeline_results)
        self.stats.step_times['5_m51_intent'] = round((time.time() - t5) * 1000, 0)

        # ── Step 6: 7权重融合 ──
        t6 = time.time()
        pipeline_results = self._step6_fusion(pipeline_results, env)
        self.stats.step_times['6_7weight_fusion'] = round((time.time() - t6) * 1000, 0)

        # ── Step 7: M54 仓位决策 ──
        t7 = time.time()
        pipeline_results = self._step7_position(pipeline_results)
        self.stats.step_times['7_m54_position'] = round((time.time() - t7) * 1000, 0)

        # ── Step 8: M55 日频校准(仅记录偏差) ──
        t8 = time.time()
        if self.m55_engine:
            try:
                calibration_note = self.m55_engine.calibrate(
                    [r for r in pipeline_results if (r.get('action') or '').startswith('buy')]
                ) if hasattr(self.m55_engine, 'calibrate') else {}
            except Exception as e:
                self._log(f"   ⚠️ M55校准失败: {e}")
                calibration_note = {}
        else:
            calibration_note = {}
        self.stats.step_times['8_m55_calibration'] = round((time.time() - t8) * 1000, 0)

        # ── Step 9: 持久化 ──
        t9 = time.time()
        if self.persistence:
            try:
                for r in pipeline_results:
                    if (r.get('action') or '').startswith('buy') or r.get('action') == 'watch':
                        self.persistence.save_decision(r, env)
                self.persistence.compute_daily_summary(datetime.now().strftime('%Y-%m-%d'))
            except Exception as e:
                self._log(f"   ⚠️ 持久化失败: {e}")
        self.stats.step_times['9_persistence'] = round((time.time() - t9) * 1000, 0)

        # ── Step 10: 结果展示 ──
        t10 = time.time()
        report = self._step10_finalize(pipeline_results, env, t_start)
        self.stats.step_times['10_finalize'] = round((time.time() - t10) * 1000, 0)

        self.stats.total_time_ms = round((time.time() - t_start) * 1000, 0)

        return report

    # ═══════════════════════════════════════════════
    # 各步骤实现
    # ═══════════════════════════════════════════════

    def _step1_market_env(self, external_env: dict = None) -> dict:
        """Step 1: 市场环境感知"""
        if external_env:
            return external_env

        return {
            'volatility': 0.02,
            'trend': 0.0,
            'sentiment': 'neutral',
            'limit_up_count': 40,
            'limit_down_count': 5,
            'total_volume': 8.5e11,
            'hot_sectors': ['AI算力', '半导体'],
        }

    def _step2_prepare_candidates(self, candidates: List[dict] = None) -> Tuple[List[dict], str]:
        """
        Step 2: 候选准备（★ TDX实盘优先）

        优先级链路：
        1. 外部直接传入candidates → 用DataPipeline标准化 → 实盘模式
        2. TDX缓存文件存在且新鲜 → 加载标准化 → 实盘模式
        3. data_mode='tdx_real' 无缓存 → 输出采集计划 → 临时降级合成
        4. data_mode='synthetic' → 纯合成数据
        5. data_mode='auto' → 有缓存用缓存，无缓存用合成

        返回: (stocks列表, 数据来源状态)
              状态: "tdx_real" | "tdx_cached" | "external" | "synthetic_fallback" | "synthetic"
        """
        import json as _json

        # ── 优先级1: 外部传入实盘数据 ──
        if candidates is not None and len(candidates) > 0:
            if self.data_pipeline:
                stocks = self.data_pipeline.prepare_all(candidates)
                self._log(f"   ✅ 实盘候选 {len(stocks)}只 (外部注入→DataPipeline标准化)")
                return stocks, "external"
            else:
                self._log(f"   ⚠️ DataPipeline不可用，直接使用原始数据 {len(candidates)}只")
                return candidates, "external"

        # ── 优先级2: TDX缓存文件 ──
        tdx_cache_path = os.path.join(
            self.config.tdx_cache_dir,
            self.config.tdx_cache_file
        )
        tdx_cache_loaded = False
        if os.path.exists(tdx_cache_path):
            cache_age = time.time() - os.path.getmtime(tdx_cache_path)
            if cache_age < self.config.tdx_cache_ttl_sec:
                try:
                    with open(tdx_cache_path, 'r', encoding='utf-8') as f:
                        tdx_raw = _json.load(f)
                    cached_data = tdx_raw.get('candidates', [])
                    if cached_data and self.data_pipeline:
                        stocks = self.data_pipeline.prepare_all(cached_data)
                        self._log(f"   ✅ TDX实盘缓存 {len(stocks)}只 "
                                  f"(缓存{int(cache_age)}秒前, {tdx_raw.get('date','?')} "
                                  f"{tdx_raw.get('time','?')})")
                        return stocks, "tdx_cached"
                    else:
                        self._log(f"   ⚠️ TDX缓存存在但数据为空，跳过")
                except Exception as e:
                    self._log(f"   ⚠️ TDX缓存读取失败: {e}")
            else:
                self._log(f"   ⚠️ TDX缓存过期({int(cache_age)}s > {self.config.tdx_cache_ttl_sec}s)，需要刷新")
        else:
            self._log(f"   📡 TDX缓存文件不存在: {tdx_cache_path}")

        # ── 优先级3: TDX实盘模式但无缓存 → 输出采集计划 ──
        if self.config.data_mode == 'tdx_real':
            self._print_tdx_acquisition_plan()
            self._log(f"   ⚠️ TDX实盘模式：无有效缓存，已输出数据采集计划")
            self._log(f"   ⚠️ 临时降级使用合成数据 {40}只——请先执行TDX数据采集后重新运行")
            return self._generate_synthetic_candidates(count=40), "synthetic_fallback"

        # ── 优先级4: 合成数据 ──
        self._log(f"   🔧 数据模式={self.config.data_mode}，使用合成数据 40只")
        return self._generate_synthetic_candidates(count=40), "synthetic"

    def _print_tdx_acquisition_plan(self):
        """输出TDX数据采集计划——供外部Agent执行MCP调用"""
        plan = {
            'plan_type': 'tdx_tail_market_screening',
            'timestamp': datetime.now().isoformat(),
            'steps': [
                {
                    'step': 1,
                    'description': '尾盘放量选股筛选',
                    'mcp_call': {
                        'tool': 'mcp__tdx-connector__tdx_screener',
                        'params': {
                            'message': '尾盘放量 涨幅2到6 换手率3到10 量比大于1.2',
                            'rang': 'AG', 'pageNo': '1', 'pageSize': '50'
                        }
                    },
                    'output_key': 'screening_results'
                },
                {
                    'step': 2,
                    'description': '主力净流入选股（补充候选）',
                    'mcp_call': {
                        'tool': 'mcp__tdx-connector__tdx_screener',
                        'params': {
                            'message': '主力净流入 放量上涨 MACD金叉',
                            'rang': 'AG', 'pageNo': '1', 'pageSize': '30'
                        }
                    },
                    'output_key': 'screening_results_2'
                },
                {
                    'step': 3,
                    'description': '北交所30cm候选（可选）',
                    'mcp_call': {
                        'tool': 'mcp__tdx-connector__tdx_screener',
                        'params': {
                            'message': '北交所 涨幅大于3 换手率大于5',
                            'rang': 'AG', 'pageNo': '1', 'pageSize': '20'
                        }
                    },
                    'output_key': 'bj_screening_results'
                },
                {
                    'step': 4,
                    'description': '对每只候选股获取实时行情+K线+财务指标',
                    'mcp_calls': [
                        {
                            'tool': 'mcp__tdx-connector__tdx_quotes',
                            'params': {
                                'code': '{{stock_code}}',
                                'setcode': '{{setcode}}',
                                'hasHQInfo': '1', 'hasExtInfo': '1', 'bspNum': '5'
                            },
                            'output_key': 'quote'
                        },
                        {
                            'tool': 'mcp__tdx-connector__tdx_kline',
                            'params': {
                                'code': '{{stock_code}}',
                                'setcode': '{{setcode}}',
                                'target': '0', 'period': '4',
                                'wantNum': '150', 'tqFlag': '11'
                            },
                            'output_key': 'kline_daily'
                        },
                        {
                            'tool': 'mcp__tdx-connector__tdx_kline',
                            'params': {
                                'code': '{{stock_code}}',
                                'setcode': '{{setcode}}',
                                'target': '0', 'period': '5',
                                'wantNum': '60', 'tqFlag': '11'
                            },
                            'output_key': 'kline_weekly'
                        },
                        {
                            'tool': 'mcp__tdx-connector__tdx_indicator_select',
                            'params': {
                                'message': '{{stock_code}}的市盈率、市净率、ROE、营收增长率、净利润增长率、毛利率、资产负债率、股东人数、流通市值、质押比例、商誉',
                                'rang': 'AG'
                            },
                            'output_key': 'indicator'
                        }
                    ],
                    'note': '对Step1+Step2+Step3的每只候选股执行以上4个MCP调用'
                },
                {
                    'step': 5,
                    'description': '将采集结果保存为缓存文件',
                    'save_to': f'data/tdx_realtime_input.json',
                    'format': {
                        'date': 'YYYY-MM-DD',
                        'time': 'HH:MM:SS',
                        'source': 'TDX_MCP',
                        'candidates': [
                            {
                                'code': '000001', 'name': '平安银行',
                                'quote': '{{tdx_quotes原始返回}}',
                                'kline': '{{tdx_kline_daily原始返回}}',
                                'weekly_kline': '{{tdx_kline_weekly原始返回}}',
                                'indicator': '{{tdx_indicator原始返回}}',
                                'news': '{{wenda_news可选}}',
                                'notice': '{{wenda_notice可选}}',
                                'sector': '行业/板块'
                            }
                        ]
                    }
                }
            ],
            'total_expected_queries': '3次筛选 + N×4次/股 (N=候选股数)',
            'cache_ttl': f'{self.config.tdx_cache_ttl_sec}秒',
        }

        print("\n" + "▬" * 65)
        print("▌  📡 TDX 实盘数据采集计划")
        print("▌" + "─" * 63)
        print("▌  Step 1: tdx_screener('尾盘放量 涨幅2到6 换手率3到10 量比>1.2')")
        print("▌  Step 2: tdx_screener('主力净流入 放量上涨 MACD金叉')")
        print("▌  Step 3: tdx_screener('北交所 涨幅>3 换手率>5') [可选]")
        print("▌  Step 4: 对每只候选: tdx_quotes + tdx_kline(日线150) + tdx_kline(周线60) + tdx_indicator_select")
        print("▌  Step 5: 保存至 {} 后重新运行".format(
            os.path.join(self.config.tdx_cache_dir, self.config.tdx_cache_file)))
        print("▌" + "─" * 63)
        print("▌  缓存有效期: {}分钟 | 当前模式: {} | 缓存状态: 无".format(
            self.config.tdx_cache_ttl_sec // 60, self.config.data_mode))
        print("▬" * 65 + "\n")

        # 同时保存为JSON供Agent读取
        plan_path = os.path.join(self.config.tdx_cache_dir, 'tdx_acquisition_plan.json')
        os.makedirs(self.config.tdx_cache_dir, exist_ok=True)
        with open(plan_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        self._log(f"   📋 数据采集计划已保存至 {plan_path}")

    def inject_tdx_data_and_run(self, tdx_raw_data: dict) -> dict:
        """
        注入TDX实盘数据并立即执行完整流水线

        Agent调用流程:
          1. 执行TDX MCP查询（tdx_screener → tdx_quotes/tdx_kline/tdx_indicator）
          2. 将原始MCP返回组装为 {candidates: [{code, name, quote, kline, weekly_kline, indicator, ...}]}
          3. 调用 orchestrator.inject_tdx_data_and_run(raw_data)

        tdx_raw_data格式:
        {
            "date": "2026-06-23",
            "time": "14:30:00",
            "source": "TDX_MCP",
            "candidates": [
                {
                    "code": "000001",
                    "name": "平安银行",
                    "quote": { ... tdx_quotes原始返回 ... },
                    "kline": { ... tdx_kline日线原始返回 ... },
                    "weekly_kline": { ... tdx_kline周线原始返回 ... },
                    "indicator": { ... tdx_indicator_select原始返回 ... },
                    "news": { ... wenda_news可选 ... },
                    "notice": { ... wenda_notice可选 ... },
                    "sector": "银行"
                },
                ...
            ]
        }
        """
        import json as _json

        # 1. 保存缓存
        cache_path = os.path.join(
            self.config.tdx_cache_dir,
            self.config.tdx_cache_file
        )
        os.makedirs(self.config.tdx_cache_dir, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            _json.dump(tdx_raw_data, f, ensure_ascii=False, indent=2)

        self._log(f"   💾 TDX数据已缓存: {cache_path} "
                  f"({len(tdx_raw_data.get('candidates', []))}只)")

        # 2. 标准化数据
        candidates = tdx_raw_data.get('candidates', [])
        if self.data_pipeline:
            stocks = self.data_pipeline.prepare_all(candidates)
            self._log(f"   ✅ DataPipeline标准化 {len(stocks)}只实盘候选")
        else:
            stocks = candidates
            self._log(f"   ⚠️ DataPipeline不可用，直接使用原始数据 {len(stocks)}只")

        # 3. 执行全链路
        self._log(f"   🚀 启动实盘全链路流水线...")
        return self.run_daily_tail_market(candidates=stocks)

    def _step3_pipeline(self, stocks: List[dict], env: dict) -> List[dict]:
        """Step 3-4: 对每只候选股运行四层流水线"""
        self._log(f"   🔄 四层递进筛选: {len(stocks)}只 → ...")

        results = []
        for stock in stocks:
            stock['date'] = stock.get('date', datetime.now().strftime('%Y-%m-%d'))
            stock['timestamp'] = datetime.now().isoformat()

            if self.tail_engine:
                result = self.tail_engine.run_full_pipeline(stock, env)
            else:
                result = self._fallback_pipeline(stock)

            self.stats.stocks_processed += 1
            pipeline = result.get('pipeline', {})

            if pipeline.get('L1_T1初筛', {}).get('passed'):
                self.stats.stocks_passed_l1 += 1
            if pipeline.get('L2_形态共振', {}).get('passed'):
                self.stats.stocks_passed_l2 += 1
            if pipeline.get('L3_排雷检测', {}).get('passed'):
                self.stats.stocks_passed_l3 += 1
            if result.get('action', '').startswith('buy'):
                self.stats.stocks_buy_signal += 1

            results.append(result)

        self._log(f"   ✅ 筛选完成: L1通过{self.stats.stocks_passed_l1}/L2通过{self.stats.stocks_passed_l2}/L3通过{self.stats.stocks_passed_l3}/买入{self.stats.stocks_buy_signal}")
        return results

    def _step4_bayesian(self, results: List[dict], env: dict) -> List[dict]:
        """Step 4: M46贝叶斯概率叠加"""
        if not self.m46_engine:
            return results

        passed = [r for r in results if (r.get('action') or '').startswith('buy') or r.get('action') == 'watch']
        if not passed:
            return results

        try:
            stocks_for_m46 = []
            for r in passed:
                pipeline = r.get('pipeline', {})
                l4 = pipeline.get('L4_12维终审', {})
                l2 = pipeline.get('L2_形态共振', {})
                stocks_for_m46.append({
                    'code': r['code'],
                    'name': r['name'],
                    'industry': r.get('industry', '通用'),
                    'sub_sector': r.get('sub_sector', ''),
                    'seven_weight_score': l4.get('total_score', r.get('fusion_total', 0.5)),
                    'm48_signal': 0.6 if r.get('tail_volume_ratio', 0) > 0.25 else 0.4,
                    'm49_signal': 0.6 if l2.get('resonance_count', 0) >= 3 else 0.4,
                    'm51_signal': r.get('m51_intent_strength', 0.5),
                })

            m46_results = self.m46_engine.batch_compute(
                stocks_for_m46,
                market_volatility=env.get('volatility', 0.02),
                market_trend=env.get('trend', 0.0),
            )

            # 回填结果
            for i, r in enumerate(passed):
                if i < len(m46_results):
                    mr = m46_results[i]
                    r['m46_base_prob'] = mr['base_probability']
                    r['m46_prior'] = mr['industry_prior']
                    r['m46_posterior'] = mr['posterior_probability']
                    r['m46_final_prob'] = mr['final_probability']
                    r['m46_confidence'] = mr['confidence']
                    r['m46_resonance'] = mr['is_resonance']
                    r['m46_resonance_strength'] = mr['resonance_strength']
        except Exception as e:
            self._log(f"   ⚠️ M46处理异常: {e}")

        return results

    def _step5_intent_filter(self, results: List[dict]) -> List[dict]:
        """Step 5: M51主力意图过滤"""
        if not self.m51_engine:
            return results

        for r in results:
            try:
                from V13_0_M51_IntentInference import IntentSignal
                # 轻量级检查
                big_ratio = r.get('big_order_ratio', 0)
                if big_ratio >= 0.30:
                    r['m51_intent_strength'] = min(1.0, big_ratio * 1.5)
                    r['m51_direction'] = 'bullish' if r.get('big_order_net', 0) > 0 else 'bearish'
                    r['m51_big_order_ratio'] = big_ratio
                    r['m51_noise_score'] = 0.05
                    r['m51_filter_passed'] = True
                else:
                    r['m51_filter_passed'] = False
                    r['m51_noise_score'] = max(0.85, 1.0 - big_ratio * 2)
            except Exception:
                pass

        return results

    def _step6_fusion(self, results: List[dict], env: dict) -> List[dict]:
        """Step 6: 7权重融合评分"""
        if not self.fusion_engine:
            return results

        sentiment = env.get('sentiment', 'neutral')
        try:
            self.fusion_engine.adjust_weights_by_sentiment(sentiment)
        except Exception:
            pass

        for r in results:
            if not (r.get('action') or '').startswith('buy') and r.get('action') != 'watch':
                continue
            try:
                score = self.fusion_engine.compute_score(
                    catalyst_score=r.get('catalyst_score', 0.5),
                    policy_score=r.get('policy_score', 0.5),
                    sector_score=r.get('sector_score', 0.5),
                    momentum_score=r.get('momentum_score', 0.5),
                    capital_score=r.get('capital_score', 0.5),
                    sentiment_score=r.get('sentiment_score', 0.5),
                    technical_score=r.get('technical_score', 0.5),
                )
                r['fusion_total'] = score['total_score']
                r['fusion_w1_catalyst'] = score['dimensions']['W1_催化']
                r['fusion_w2_policy'] = score['dimensions']['W2_政策']
                r['fusion_w3_sector'] = score['dimensions']['W3_板块']
                r['fusion_w4_momentum'] = score['dimensions']['W4_动量']
                r['fusion_w5_capital'] = score['dimensions']['W5_资金']
                r['fusion_w6_sentiment'] = score['dimensions']['W6_舆情']
                r['fusion_w7_technical'] = score['dimensions']['W7_技术']
            except Exception as e:
                self._log(f"   ⚠️ 融合评分异常 {r.get('code')}: {e}")

        return results

    def _step7_position(self, results: List[dict]) -> List[dict]:
        """Step 7: M54仓位决策"""
        if not self.m54_engine:
            return results

        for r in results:
            if r.get('action') != 'buy':
                continue
            try:
                decision = self.m54_engine.position_decision(
                    code=r['code'], name=r['name'],
                    current_price=r.get('current_price', 0),
                    entry_price=0,
                    m46_probability=r.get('m46_final_prob', 0.5),
                    m46_confidence=r.get('m46_confidence', '中'),
                    high_prices=r.get('highs'),
                    low_prices=r.get('lows'),
                    close_prices=r.get('prices'),
                    current_qty=0,
                    is_new_position=True,
                    risk_level=r.get('m54_risk_level', '安全') if 'L3_排雷检测' not in str(r.get('pipeline',{})) else '安全',
                )
                r['m54_position_pct'] = decision.get('position_pct', 0.20)
                r['m54_stop_loss'] = decision.get('stop_loss', 0)
                r['m54_take_profit_1'] = decision.get('take_profit_tiers', [{}])[0].get('price', 0) if decision.get('take_profit_tiers') else 0
                r['m54_take_profit_2'] = decision.get('take_profit_tiers', [{},{}])[1].get('price', 0) if len(decision.get('take_profit_tiers', [])) >= 2 else 0
                r['m54_estimated_plr'] = decision.get('estimated_profit_loss_ratio', 0)
                r['m54_risk_level'] = decision.get('risk_level', '安全')
            except Exception as e:
                self._log(f"   ⚠️ M54仓位异常 {r.get('code')}: {e}")

        return results

    def _step10_finalize(self, results: List[dict], env: dict, t_start: float) -> dict:
        """Step 10: 结果汇总和展示"""
        buys = sorted(
            [r for r in results if (r.get('action') or '').startswith('buy')],
            key=lambda x: x.get('fusion_total', 0), reverse=True
        )[:self.config.max_buy_signals]
        watches = [r for r in results if r.get('action') == 'watch']
        rejected = [r for r in results if r.get('action') in ('pass', 'reject')]

        report = {
            'report_type': 'daily_tail_market',
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'data_source': getattr(self, '_last_data_source', 'unknown'),
            'market_env': env,
            'summary': {
                'total_candidates': len(results),
                'l1_passed': self.stats.stocks_passed_l1,
                'l2_passed': self.stats.stocks_passed_l2,
                'l3_passed': self.stats.stocks_passed_l3,
                'buy_signals': len(buys),
                'watch_signals': len(watches),
                'rejected': len(rejected),
            },
            'buy_signals': [self._signal_summary(r) for r in buys],
            'watch_signals': [self._signal_summary(r) for r in watches[:10]],
            'pipeline_stats': {
                'total_time_ms': self.stats.total_time_ms,
                'step_times': self.stats.step_times,
                'errors': self.stats.errors,
            },
        }

        # 终端友好输出
        self._print_daily_report(report)

        return report

    def _signal_summary(self, r: dict) -> dict:
        """提取信号关键字段"""
        return {
            'code': r.get('code', ''),
            'name': r.get('name', ''),
            'current_price': r.get('current_price', 0),
            'daily_change_pct': r.get('daily_change_pct', 0),
            'turnover_rate': r.get('turnover_rate', 0),
            'volume_ratio': r.get('volume_ratio', 1.0),
            'l4_total_score': r.get('pipeline', {}).get('L4_12维终审', {}).get('total_score',
                              r.get('total_score', 0)),
            'fusion_total': r.get('fusion_total', 0),
            'm46_final_prob': r.get('m46_final_prob', 0),
            'm46_confidence': r.get('m46_confidence', '低'),
            'm51_direction': r.get('m51_direction', 'neutral'),
            'm54_position_pct': r.get('m54_position_pct', 0),
            'm54_estimated_plr': r.get('m54_estimated_plr', 0),
            'verdict': r.get('verdict', ''),
            'action': r.get('action', 'pass'),
            'top_patterns': r.get('pipeline', {}).get('L2_形态共振', {}).get('top_patterns', []),
        }

    def _print_daily_report(self, report: dict):
        """终端友好输出每日报告"""
        s = report['summary']
        env = report['market_env']
        stats = report['pipeline_stats']
        data_src = report.get('data_source', 'unknown')

        data_icons = {
            'tdx_real': '🟢 TDX实盘', 'tdx_cached': '🟢 TDX缓存',
            'external': '🟡 外部注入', 'synthetic_fallback': '🟠 合成(降级)',
            'synthetic': '🔴 纯合成',
        }
        data_label = data_icons.get(data_src, f'⚪ {data_src}')

        print("\n" + "█" * 65)
        print(f"█  V13.0 每日尾盘选股报告 — {report['date']}")
        print("█" + "─" * 63)
        print(f"█  数据源: {data_label}"
              f"  |  市场: {env.get('sentiment','neutral').upper()}"
              f"  |  波动率={env.get('volatility',0):.1%}"
              f"  |  涨停数={env.get('limit_up_count',0)}")
        print(f"█  候选: {s['total_candidates']}只 → "
              f"L1={s['l1_passed']} → L2={s['l2_passed']} → "
              f"L3={s['l3_passed']} → 买入={s['buy_signals']}")
        print(f"█  耗时: {stats['total_time_ms']:.0f}ms")

        if report['buy_signals']:
            print("█" + "─" * 63)
            print(f"█  📈 买入信号 ({len(report['buy_signals'])}只):")
            for s in report['buy_signals']:
                print(f"█    {s['code']} {s['name']:<8s} "
                      f"¥{s['current_price']:.2f} "
                      f"涨{s['daily_change_pct']:.1%} "
                      f"融合={s['fusion_total']:.2f} "
                      f"贝叶斯={s['m46_final_prob']:.2f}({s['m46_confidence']}) "
                      f"PLR={s['m54_estimated_plr']:.1f}")

        if report['watch_signals']:
            print(f"█  ⏳ 观察信号 ({len(report['watch_signals'])}只):")
            for s in report['watch_signals'][:5]:
                print(f"█    {s['code']} {s['name']:<8s} "
                      f"¥{s['current_price']:.2f} "
                      f"融合={s['fusion_total']:.2f} "
                      f"形态={s['top_patterns'][:2]}")

        print("█" + "─" * 63)
        print(f"█  ✅ 报告完成 | {datetime.now().strftime('%H:%M:%S')}")
        print("█" * 65 + "\n")

    def _generate_synthetic_candidates(self, count: int = 40) -> List[dict]:
        """生成合成候选数据（无实盘时的演练模式）"""
        import random
        random.seed(int(time.time()) % 10000)

        names = [
            '思源电气', '中科曙光', '寒武纪', '海康威视', '宁德时代',
            '科大讯飞', '三安光电', '北方华创', '韦尔股份', '中际旭创',
            '浪潮信息', '工业富联', '天孚通信', '新易盛', '中芯国际',
            '拓荆科技', '盛美上海', '华大九天', '广立微', '概伦电子',
            '江波龙', '佰维存储', '德明利', '机器人', '埃斯顿',
            '绿的谐波', '汇川技术', '鸣志电器', '恒瑞医药', '迈瑞医疗',
            '药明康德', '百济神州', '中国船舶', '中航沈飞', '航发动力',
            '比亚迪', '长城汽车', '贵州茅台', '五粮液', '泸州老窖',
        ]

        industries = []
        for i in range(count):
            if i < 10: ind = 'AI算力/服务器'
            elif i < 20: ind = '半导体设备/材料'
            elif i < 25: ind = '人形机器人'
            elif i < 30: ind = '医药/创新药'
            elif i < 35: ind = '军工/航天'
            else: ind = '通用'
            industries.append(ind)

        # 生成合理的日内数据
        candidates = []
        for i in range(count):
            base_price = random.uniform(15, 120)
            change_pct = random.uniform(0.01, 0.06) if random.random() < 0.6 else random.uniform(-0.03, 0.01)
            current_price = base_price * (1 + change_pct)

            # 生成简单K线（用于形态检测）
            prices = []
            p = base_price * 0.85
            trend = 1 if change_pct > 0 else -1
            for d in range(150):
                p *= (1 + random.gauss(0.0005 * trend, 0.018))
                prices.append(p)
            if abs(prices[-1] - current_price) / current_price > 0.02:
                prices[-1] = current_price

            volumes = [random.uniform(2e7, 8e7) for _ in range(150)]

            # 计算简单均线
            def _sma(arr, period):
                result = []
                for i in range(len(arr)):
                    if i+1 < period:
                        result.append(sum(arr[:i+1])/(i+1))
                    else:
                        result.append(sum(arr[i-period+1:i+1])/period)
                return result

            stock = {
                'code': f'{600000+i+100:06d}' if i < 25 else f'{200000+i-25+100:06d}',
                'name': names[i % len(names)],
                'industry': industries[i],
                'sub_sector': '',
                'current_price': current_price,
                'daily_change_pct': change_pct,
                'turnover_rate': random.uniform(0.03, 0.09),
                'volume_ratio': random.uniform(1.0, 2.8),
                'market_cap': random.uniform(2e9, 1.5e11),
                'tail_volume_ratio': random.uniform(0.20, 0.35),
                'above_avg_line': random.random() > 0.3,
                'prices': prices,
                'highs': [p * random.uniform(1.005, 1.03) for p in prices],
                'lows': [p * random.uniform(0.97, 0.995) for p in prices],
                'opens': [p * random.uniform(0.98, 1.02) for p in prices],
                'volumes': volumes,
                'ma5': _sma(prices, 5),
                'ma10': _sma(prices, 10),
                'ma20': _sma(prices, 20),
                'ma25': _sma(prices, 25),
                'ma60': _sma(prices, 60),
                'ma120': _sma(prices, 120),
                'macd_dif': [_sma(prices,12)[i] - _sma(prices,26)[i] for i in range(len(prices))],
                'macd_dea': [_sma(prices,12)[i] - _sma(prices,26)[i] for i in range(len(prices))],  # simplified
                'macd_hist': [0] * len(prices),
                'vol_ma5': _sma(volumes, 5),
                'vol_ma60': _sma(volumes, 60),
                'atr_14': [p*0.02 for p in prices],
                'price_position': '中位',
                'big_order_ratio': random.uniform(0.20, 0.45),
                'big_order_net': random.uniform(-5e6, 2e7),
                'sentiment_score': random.uniform(0.4, 0.8),
                'capital_score': random.uniform(0.3, 0.7),
                'winner_ratio': random.uniform(0.3, 0.8),
                'chip_concentration': random.uniform(0.1, 0.6),
                'open_fund_flow': random.uniform(-1e8, 5e8),
                'dark_pool_flow': random.uniform(-5e7, 2e8),
                'cumulative_gain': 0,
                'seven_weight_score': 0.5,
                'has_reduction': random.random() < 0.05,
                'has_unlock': random.random() < 0.03,
                'has_regulatory_warning': random.random() < 0.01,
                'st_risk': False,
                'earnings_cliff': random.random() < 0.02,
                'pe': random.uniform(15, 80),
                'sector_pe': random.uniform(20, 60),
                'earnings_growth': random.uniform(-0.1, 0.4),
            }
            candidates.append(stock)

        return candidates

    def _fallback_pipeline(self, stock: dict) -> dict:
        """无TailMarketUltimate时的降级流水线"""
        code = stock.get('code', '')
        name = stock.get('name', '')
        prices = stock.get('prices', [])
        change_pct = stock.get('daily_change_pct', 0)

        if not prices or len(prices) < 50:
            return {'code': code, 'name': name, 'verdict': '❌ 数据不足', 'action': 'pass',
                    'pipeline': {'L1_T1初筛': {'passed': False, 'score': 0}},
                    'total_score': 0, 'm46_final_prob': 0, 'm46_confidence': '低',
                    'm54_estimated_plr': 0, 'fusion_total': 0}

        # 简易条件
        ma60 = stock.get('ma60', [0])[-1]
        l1_ok = (
            0.02 <= abs(change_pct) <= 0.06 and
            stock.get('turnover_rate', 0) >= 0.04 and
            stock.get('volume_ratio', 1.0) >= 1.2 and
            stock.get('market_cap', 0) >= 5e9 and
            stock.get('tail_volume_ratio', 0.20) >= 0.22 and
            prices[-1] > ma60
        )

        traps = sum([
            stock.get('has_reduction', False),
            stock.get('has_unlock', False),
            stock.get('has_regulatory_warning', False),
            stock.get('st_risk', False),
            stock.get('earnings_cliff', False),
        ])

        if not l1_ok:
            return {'code': code, 'name': name, 'verdict': '❌ T-1初筛未通过', 'action': 'pass',
                    'pipeline': {'L1_T1初筛': {'passed': False, 'score': 0}},
                    'total_score': 0, 'm46_final_prob': 0, 'm46_confidence': '低',
                    'm54_estimated_plr': 0, 'fusion_total': 0}

        if traps > 0:
            return {'code': code, 'name': name, 'verdict': '🚫 排雷未通过', 'action': 'reject',
                    'pipeline': {'L1_T1初筛': {'passed': True, 'score': 0.7},
                                 'L3_排雷检测': {'passed': False, 'risk_level': '危险', 'trap_score': traps*0.2}},
                    'total_score': 0, 'm46_final_prob': 0, 'm46_confidence': '低',
                    'm54_estimated_plr': 0, 'fusion_total': 0}

        total = 0.65
        return {
            'code': code, 'name': name,
            'verdict': '✅ 高置信度买入' if total >= 0.60 else '⏳ 观察',
            'action': 'buy' if total >= 0.60 else 'watch',
            'pipeline': {
                'L1_T1初筛': {'passed': True, 'score': 0.70, 'details': {'gain': change_pct}},
                'L2_形态共振': {'passed': True, 'resonance_count': 2, 'score': 0.60,
                    'patterns': {}, 'top_patterns': ['均线多头', '量能配合']},
                'L3_排雷检测': {'passed': True, 'risk_level': '安全', 'trap_score': 0,
                    'traps': []},
                'L4_12维终审': {'resonance_count': 2, 'total_score': total,
                    'verdict': '✅ 高置信度买入', 'dimensions': {}, 'weights': {}}
            },
            'total_score': total, 'action': 'buy' if total >= 0.60 else 'watch',
            'm46_final_prob': total * 0.85, 'm46_confidence': '中',
            'm51_intent_strength': 0.55, 'm51_direction': 'bullish',
            'm54_estimated_plr': 2.8, 'fusion_total': total,
            'current_price': prices[-1], 'daily_change_pct': change_pct,
            'turnover_rate': stock.get('turnover_rate', 0),
            'volume_ratio': stock.get('volume_ratio', 1.0),
            'market_cap': stock.get('market_cap', 0),
            'tail_volume_ratio': stock.get('tail_volume_ratio', 0.28),
        }

    # ═══════════════════════════════════════════════
    # 单股诊断
    # ═══════════════════════════════════════════════

    def diagnose_stock(self, code: str, name: str,
                       quote: dict = None, kline: dict = None,
                       weekly_kline: dict = None, indicator: dict = None,
                       news: dict = None, notice: dict = None,
                       sector: str = '') -> dict:
        """
        单股全面诊断

        使用数据管线准备数据后，跑完整流水线+所有模块
        """
        self._log(f"🔍 单股诊断: {code} {name}")

        # 准备数据
        if self.data_pipeline and any([quote, kline, indicator]):
            stock = self.data_pipeline.prepare_candidate(
                code, name, quote, kline, weekly_kline,
                indicator, news, notice, sector
            )
        else:
            stock = {
                'code': code, 'name': name,
                'current_price': 30.0,
                'daily_change_pct': 0.035,
                'turnover_rate': 0.065,
                'volume_ratio': 1.8,
                'market_cap': 1.2e11,
                'tail_volume_ratio': 0.28,
                'above_avg_line': True,
                'prices': [29 + i * 0.05 for i in range(120)],
                'volumes': [5e7 for _ in range(120)],
                'ma5': [29+i*0.05 for i in range(120)],
                'ma10': [28.5+i*0.05 for i in range(120)],
                'ma20': [28+i*0.05 for i in range(120)],
                'ma25': [27.8+i*0.05 for i in range(120)],
                'ma60': [27+i*0.05 for i in range(120)],
                'ma120': [26+i*0.05 for i in range(120)],
                'macd_dif': [0.05 for _ in range(120)],
                'macd_dea': [0.03 for _ in range(120)],
                'macd_hist': [0.04 for _ in range(120)],
                'vol_ma5': [5e7]*120, 'vol_ma60': [4.5e7]*120,
                'atr_14': [0.6]*120, 'price_position': '中位',
                'has_reduction': False, 'has_unlock': False,
                'has_regulatory_warning': False, 'st_risk': False,
                'earnings_cliff': False,
                'pe': 35, 'sector_pe': 40, 'earnings_growth': 0.20,
            }

        # 执行流水线
        env = self._step1_market_env()
        result = self._step3_pipeline([stock], env)[0] if self.tail_engine else self._fallback_pipeline(stock)
        result = self._step4_bayesian([result], env)[0]
        result = self._step5_intent_filter([result])[0]
        result = self._step6_fusion([result], env)[0]
        result = self._step7_position([result])[0]

        # 持久化
        if self.persistence:
            try:
                self.persistence.save_decision(result, env)
            except Exception as e:
                self._log(f"   ⚠️ 诊断持久化失败: {e}")

        return result

    # ═══════════════════════════════════════════════
    # 周末回测
    # ═══════════════════════════════════════════════

    def run_weekly_backtest(self) -> dict:
        """执行周末回测验证"""
        self._log("📊 启动周末回测验证...")

        try:
            from V13_0_BacktestEngine import BacktestEngine, BacktestConfig
            config = BacktestConfig(
                lookback_days=self.config.backtest_lookback_days,
                min_buy_score=self.config.min_total_score,
            )
            engine = BacktestEngine(config)
            engine.load_synthetic_universe(
                num_stocks=self.config.backtest_synthetic_stocks,
                days=self.config.backtest_lookback_days + 5
            )

            # 注入当前流水线函数
            def pipeline_wrapper(stock, env):
                if self.tail_engine:
                    return self.tail_engine.run_full_pipeline(stock, env)
                return self._fallback_pipeline(stock)

            engine.set_pipeline_func(pipeline_wrapper)
            report = engine.run()

            engine.print_report(report)
            engine.export_report(report,
                os.path.join(self.config.data_dir, 'backtest_weekly.json'))

            if self.persistence:
                engine.save_to_db(report)

            return {
                'success': True,
                'hit_rate': report.hit_rate,
                'win_rate': report.win_rate,
                'trap_rate': report.trap_rate,
                'positive_plr': report.positive_plr,
                'sharpe_ratio': report.sharpe_ratio,
                'max_drawdown': report.max_drawdown,
                'suggestions': report.optimization_suggestions,
            }
        except Exception as e:
            self._log(f"❌ 回测失败: {e}")
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════
    # 模块连通性自检
    # ═══════════════════════════════════════════════

    def run_connectivity_check(self) -> dict:
        """检查所有模块连通性"""
        _init_modules()
        status = {}
        modules = {
            'TailMarketUltimate': TailMarketUltimate,
            'M46BayesianEngine': M46BayesianEngine,
            'M51NoiseFilter': M51NoiseFilter,
            'SevenWeightFusionV2': SevenWeightFusionV2,
            'M54PositionEngine': M54PositionEngine,
            'M55DailyCalibrator': M55DailyCalibrator,
            'DataPipeline': DataPipeline,
            'PersistenceManager': PersistenceManager,
            'BacktestEngine': BacktestEngine,
        }

        for name, cls in modules.items():
            status[name] = '✅ 可用' if cls is not None else '❌ 缺失'

        return status


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def run_daily_tail_market(candidates: List[dict] = None) -> dict:
    """快捷每日尾盘选股"""
    orch = V13Orchestrator()
    return orch.run_daily_tail_market(candidates)


def diagnose_stock(code: str, name: str, **kwargs) -> dict:
    """快捷单股诊断"""
    orch = V13Orchestrator()
    return orch.diagnose_stock(code, name, **kwargs)


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='V13.0 统一调度器')
    parser.add_argument('--tdx-file', type=str, default=None,
                        help='TDX实盘数据JSON文件路径')
    parser.add_argument('--data-mode', type=str, default='tdx_real',
                        choices=['tdx_real', 'synthetic', 'auto'],
                        help='数据模式 (default: tdx_real)')
    parser.add_argument('--diagnose', type=str, default=None,
                        help='单股诊断: 代码,名称 (如: "002415,海康威视")')
    parser.add_argument('--skip-backtest', action='store_true',
                        help='跳过回测')
    parser.add_argument('--quiet', action='store_true',
                        help='静默模式')
    args = parser.parse_args()

    config = OrchestratorConfig(
        verbose=not args.quiet,
        data_mode=args.data_mode,
    )

    print("=" * 60)
    print(f"V13.0 统一调度器 | 数据模式: {args.data_mode}")
    print("=" * 60)

    orch = V13Orchestrator(config)

    # 1. 连通性检查
    print("\n📡 模块连通性:")
    conn = orch.run_connectivity_check()
    for name, status in conn.items():
        print(f"   {name}: {status}")

    # 2. TDX实盘数据注入模式
    if args.tdx_file:
        print(f"\n📡 加载TDX实盘数据: {args.tdx_file}")
        import json as _json
        try:
            with open(args.tdx_file, 'r', encoding='utf-8') as f:
                tdx_data = _json.load(f)
            report = orch.inject_tdx_data_and_run(tdx_data)
            print(f"\n✅ TDX实盘选股完成: {report['summary']}")
        except FileNotFoundError:
            print(f"   ❌ 文件不存在: {args.tdx_file}")
        except Exception as e:
            print(f"   ❌ 加载失败: {e}")

    # 3. 单股诊断
    elif args.diagnose:
        parts = args.diagnose.split(',')
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else ''
        print(f"\n🔍 单股诊断: {code} {name}")
        diag = orch.diagnose_stock(code, name)
        s = orch._signal_summary(diag)
        print(f"   代码: {s['code']} {s['name']}")
        print(f"   价格: ¥{s['current_price']:.2f}")
        print(f"   融合评分: {s['fusion_total']:.2f}")
        print(f"   贝叶斯概率: {s['m46_final_prob']:.2f} ({s['m46_confidence']})")
        print(f"   盈亏比: {s['m54_estimated_plr']:.1f}")
        print(f"   形态: {s['top_patterns']}")
        print(f"   动作: {s['action']} | 判决: {s['verdict']}")

    # 4. 每日尾盘选股
    else:
        print("\n🔄 每日尾盘选股:")
        report = orch.run_daily_tail_market()

    # 5. 回测(轻量)
    if not args.skip_backtest:
        print("\n📊 回测验证:")
        bt_result = orch.run_weekly_backtest()
        if bt_result.get('success'):
            print(f"   命中率={bt_result.get('hit_rate',0):.1%} "
                  f"盈亏比={bt_result.get('positive_plr',0):.1f} "
                  f"踩雷率={bt_result.get('trap_rate',0):.1%}")
            if bt_result.get('suggestions'):
                print(f"   优化建议: {bt_result['suggestions']}")

    print("\n🎉 V13.0 统一调度器 完成！")
    avail = sum(1 for v in conn.values() if '✅' in v)
    miss = sum(1 for v in conn.values() if '❌' in v)
    print(f"   {len(conn)}个模块 | {avail}可用 | {miss}缺失")
