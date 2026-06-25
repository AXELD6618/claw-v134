#!/usr/bin/env python3
"""
V13.0 SQLite 决策持久化层
========================
S-4 阻塞项解决：所有选股决策、四层评分、回测结果全量持久化

表结构：
  1. decisions          — 每日选股决策快照（主力表）
  2. pattern_signals    — 14项形态检测详细信号
  3. trap_records       — 排雷检测记录
  4. dimension_scores   — 十二维终审评分分解
  5. daily_summary      — 每日选股汇总统计
  6. backtest_runs      — 回测运行记录
  7. calibration_log    — M55参数校准历史
  8. market_snapshots   — 市场环境快照

特性：
  - WAL模式高性能并发写入
  - 自动创建数据库和表
  - 批量写入+事务
  - JSON字段存嵌套数据
  - 时间范围查询+多维度聚合
"""

import sqlite3
import json
import os
import time
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Tuple
from contextlib import contextmanager


# ═══════════════════════════════════════════════
# 数据库常量
# ═══════════════════════════════════════════════

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'v13_decisions.db')

SCHEMA_VERSION = 1

TABLE_DEFS = {
    'schema_version': '''
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            description TEXT
        )
    ''',

    'decisions': '''
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,                  -- 交易日期 YYYY-MM-DD
            timestamp TEXT NOT NULL,             -- 决策时间戳 ISO格式
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            market TEXT DEFAULT 'A',             -- A/B/H等市场
            setcode TEXT DEFAULT '0',            -- 市场代码 0深1沪2北

            -- 第1层：T-1初筛
            l1_passed INTEGER DEFAULT 0,
            l1_score REAL DEFAULT 0.0,
            l1_details TEXT,                     -- JSON

            -- 第2层：形态共振
            l2_passed INTEGER DEFAULT 0,
            l2_resonance_count INTEGER DEFAULT 0,
            l2_strong_count INTEGER DEFAULT 0,
            l2_score REAL DEFAULT 0.0,
            l2_top_patterns TEXT,                -- JSON: 前三强形态

            -- 第3层：排雷
            l3_passed INTEGER DEFAULT 0,
            l3_risk_level TEXT DEFAULT '安全',   -- 安全/警戒/危险/黑名单
            l3_trap_score REAL DEFAULT 0.0,
            l3_triggered_traps TEXT,             -- JSON: 触发的地雷类型

            -- 第4层：12维共振终审
            l4_resonance_count INTEGER DEFAULT 0,
            l4_total_score REAL DEFAULT 0.0,
            l4_dimension_breakdown TEXT,         -- JSON: 各维度得分
            l4_verdict TEXT,                     -- ✅买入/⏳观察/❌放弃/🚫排除
            l4_action TEXT DEFAULT 'pass',       -- buy/watch/pass/reject

            -- M46贝叶斯概率
            m46_base_prob REAL DEFAULT 0.0,
            m46_prior REAL DEFAULT 0.0,
            m46_posterior REAL DEFAULT 0.0,
            m46_final_prob REAL DEFAULT 0.0,
            m46_confidence TEXT DEFAULT '低',
            m46_resonance INTEGER DEFAULT 0,
            m46_resonance_strength REAL DEFAULT 0.0,

            -- M51主力意图
            m51_intent_strength REAL DEFAULT 0.0,
            m51_direction TEXT DEFAULT 'neutral',
            m51_big_order_ratio REAL DEFAULT 0.0,
            m51_noise_score REAL DEFAULT 0.0,
            m51_filter_passed INTEGER DEFAULT 0,

            -- M54仓位决策
            m54_position_pct REAL DEFAULT 0.0,
            m54_stop_loss REAL DEFAULT 0.0,
            m54_take_profit_1 REAL DEFAULT 0.0,
            m54_take_profit_2 REAL DEFAULT 0.0,
            m54_estimated_plr REAL DEFAULT 0.0,
            m54_risk_level TEXT DEFAULT '安全',

            -- 7权重融合
            fusion_total REAL DEFAULT 0.0,
            fusion_w1_catalyst REAL DEFAULT 0.0,
            fusion_w2_policy REAL DEFAULT 0.0,
            fusion_w3_sector REAL DEFAULT 0.0,
            fusion_w4_momentum REAL DEFAULT 0.0,
            fusion_w5_capital REAL DEFAULT 0.0,
            fusion_w6_sentiment REAL DEFAULT 0.0,
            fusion_w7_technical REAL DEFAULT 0.0,

            -- 市场快照
            current_price REAL DEFAULT 0.0,
            daily_change_pct REAL DEFAULT 0.0,
            turnover_rate REAL DEFAULT 0.0,
            volume_ratio REAL DEFAULT 0.0,
            market_cap REAL DEFAULT 0.0,
            tail_volume_ratio REAL DEFAULT 0.0,
            market_volatility REAL DEFAULT 0.02,
            market_trend REAL DEFAULT 0.0,
            market_sentiment TEXT DEFAULT 'neutral',

            -- 执行结果（事后回填）
            t_day_high REAL,                     -- T日最高价
            t_day_close REAL,                    -- T日收盘价
            t_day_change_pct REAL,               -- T日涨跌幅
            t_day_hit_limit INTEGER DEFAULT 0,   -- 是否涨停
            t1_day_close REAL,                   -- T+1收盘价
            t1_day_change_pct REAL,              -- T+1涨跌幅
            actual_plr REAL,                     -- 实际盈亏比
            actual_result TEXT,                  -- 'hit'/'partial'/'miss'/'trap'

            -- 元数据
            engine_version TEXT DEFAULT 'V13.0',
            pipeline_time_ms REAL DEFAULT 0.0,   -- 流水线总耗时(ms)
            notes TEXT,                          -- 备注

            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    ''',

    'pattern_signals': '''
        CREATE TABLE IF NOT EXISTS pattern_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            pattern_name TEXT NOT NULL,          -- 老鸭头/2560/擒龙/...
            signal_strength TEXT NOT NULL,       -- 强/中/弱/无
            score REAL DEFAULT 0.0,
            key_metrics TEXT,                    -- JSON: 关键指标值
            pass_threshold INTEGER DEFAULT 0,
            details TEXT,                        -- JSON
            FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
        )
    ''',

    'trap_records': '''
        CREATE TABLE IF NOT EXISTS trap_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            trap_category TEXT NOT NULL,         -- 技术诱多/舆情诱多/事件地雷/估值陷阱
            risk_level TEXT NOT NULL,            -- 低/中/高/严重
            trap_score REAL DEFAULT 0.0,
            triggered_fields TEXT,               -- JSON: 触发字段
            mitigation TEXT,                     -- 缓解措施
            FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
        )
    ''',

    'dimension_scores': '''
        CREATE TABLE IF NOT EXISTS dimension_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id INTEGER NOT NULL,
            dimension_key TEXT NOT NULL,         -- D1_T1初筛/D2_老鸭头/...
            score REAL DEFAULT 0.0,
            weight REAL DEFAULT 0.0,
            weighted_score REAL DEFAULT 0.0,
            passed INTEGER DEFAULT 0,
            details TEXT,                        -- JSON
            FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
        )
    ''',

    'daily_summary': '''
        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_screened INTEGER DEFAULT 0,
            l1_passed INTEGER DEFAULT 0,
            l2_passed INTEGER DEFAULT 0,
            l3_passed INTEGER DEFAULT 0,
            l4_buy_signals INTEGER DEFAULT 0,
            l4_watch_signals INTEGER DEFAULT 0,
            l4_rejected INTEGER DEFAULT 0,
            avg_fusion_score REAL DEFAULT 0.0,
            avg_m46_prob REAL DEFAULT 0.0,
            avg_m54_plr REAL DEFAULT 0.0,
            top_pick_code TEXT,
            top_pick_name TEXT,
            top_pick_score REAL DEFAULT 0.0,
            market_volatility REAL DEFAULT 0.0,
            market_trend REAL DEFAULT 0.0,
            estimated_hit_rate REAL DEFAULT 0.0,
            metrics_json TEXT,                   -- JSON: 扩展指标
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    ''',

    'backtest_runs': '''
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            total_days INTEGER DEFAULT 0,
            total_signals INTEGER DEFAULT 0,
            hit_count INTEGER DEFAULT 0,
            partial_count INTEGER DEFAULT 0,
            miss_count INTEGER DEFAULT 0,
            trap_count INTEGER DEFAULT 0,
            hit_rate REAL DEFAULT 0.0,
            avg_plr REAL DEFAULT 0.0,
            max_plr REAL DEFAULT 0.0,
            avg_return REAL DEFAULT 0.0,
            max_drawdown REAL DEFAULT 0.0,
            sharpe_ratio REAL DEFAULT 0.0,
            settings_json TEXT,                  -- JSON: 回测参数
            summary_json TEXT,                   -- JSON: 详细统计
            started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            completed_at TEXT
        )
    ''',

    'backtest_signals': '''
        CREATE TABLE IF NOT EXISTS backtest_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            entry_price REAL DEFAULT 0.0,
            exit_price REAL DEFAULT 0.0,
            return_pct REAL DEFAULT 0.0,
            plr REAL DEFAULT 0.0,
            hold_days INTEGER DEFAULT 1,
            hit_limit INTEGER DEFAULT 0,
            result TEXT DEFAULT 'pending',
            pipeline_json TEXT,                  -- JSON: 完整流水线输出
            FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
        )
    ''',

    'calibration_log': '''
        CREATE TABLE IF NOT EXISTS calibration_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            module TEXT NOT NULL,                -- M55/M46/M51/M54
            param_name TEXT NOT NULL,
            old_value REAL,
            new_value REAL,
            deviation_pct REAL DEFAULT 0.0,
            reason TEXT,
            source_data TEXT,                    -- JSON
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        )
    ''',

    'market_snapshots': '''
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            index_code TEXT NOT NULL,            -- 000001/399001/399006等
            index_value REAL DEFAULT 0.0,
            change_pct REAL DEFAULT 0.0,
            total_volume REAL DEFAULT 0.0,
            up_count INTEGER DEFAULT 0,
            down_count INTEGER DEFAULT 0,
            limit_up_count INTEGER DEFAULT 0,
            limit_down_count INTEGER DEFAULT 0,
            hot_sectors TEXT,                    -- JSON: 热门板块
            market_sentiment TEXT DEFAULT 'neutral',
            volatility_20d REAL DEFAULT 0.0,
            extra_json TEXT                      -- JSON
        )
    ''',
}

# 索引定义
INDEX_DEFS = [
    'CREATE INDEX IF NOT EXISTS idx_decisions_date ON decisions(date)',
    'CREATE INDEX IF NOT EXISTS idx_decisions_code ON decisions(code)',
    'CREATE INDEX IF NOT EXISTS idx_decisions_verdict ON decisions(l4_verdict)',
    'CREATE INDEX IF NOT EXISTS idx_decisions_date_code ON decisions(date, code)',
    'CREATE INDEX IF NOT EXISTS idx_decisions_actual ON decisions(actual_result)',
    'CREATE INDEX IF NOT EXISTS idx_pattern_date ON pattern_signals(date)',
    'CREATE INDEX IF NOT EXISTS idx_pattern_decision ON pattern_signals(decision_id)',
    'CREATE INDEX IF NOT EXISTS idx_trap_decision ON trap_records(decision_id)',
    'CREATE INDEX IF NOT EXISTS idx_dimension_decision ON dimension_scores(decision_id)',
    'CREATE INDEX IF NOT EXISTS idx_backtest_run_id ON backtest_signals(run_id)',
    'CREATE INDEX IF NOT EXISTS idx_backtest_result ON backtest_signals(result)',
    'CREATE INDEX IF NOT EXISTS idx_calibration_date ON calibration_log(date, module)',
    'CREATE INDEX IF NOT EXISTS idx_snapshot_timestamp ON market_snapshots(timestamp)',
]


class PersistenceManager:
    """V13.0 SQLite持久化管理器"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._ensure_db_dir()
        self._init_schema()

    def _ensure_db_dir(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA foreign_keys=ON')
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._get_conn() as conn:
            for name, ddl in TABLE_DEFS.items():
                conn.execute(ddl)
            for idx in INDEX_DEFS:
                conn.execute(idx)
            conn.execute(
                'INSERT OR IGNORE INTO schema_version (version, description) VALUES (?, ?)',
                (SCHEMA_VERSION, 'V13.0 initial schema')
            )

    # ═══════════════════════════════════════════════
    # 决策写入
    # ═══════════════════════════════════════════════

    def save_decision(self, result: dict, market_env: dict = None) -> int:
        """
        保存单条选股决策

        result: TailMarketUltimate.run_full_pipeline() 的返回值
        market_env: 市场环境快照
        返回：自增ID
        """
        pipeline = result.get('pipeline', {})
        l1 = pipeline.get('L1_T1初筛', {})
        l2 = pipeline.get('L2_形态共振', {})
        l3 = pipeline.get('L3_排雷检测', {})
        l4 = pipeline.get('L4_12维终审', {})

        with self._get_conn() as conn:
            cursor = conn.execute('''
                INSERT INTO decisions (
                    date, timestamp, code, name, market, setcode,
                    l1_passed, l1_score, l1_details,
                    l2_passed, l2_resonance_count, l2_strong_count,
                    l2_score, l2_top_patterns,
                    l3_passed, l3_risk_level, l3_trap_score, l3_triggered_traps,
                    l4_resonance_count, l4_total_score,
                    l4_dimension_breakdown, l4_verdict, l4_action,
                    m46_base_prob, m46_prior, m46_posterior, m46_final_prob,
                    m46_confidence, m46_resonance, m46_resonance_strength,
                    m51_intent_strength, m51_direction, m51_big_order_ratio,
                    m51_noise_score, m51_filter_passed,
                    m54_position_pct, m54_stop_loss, m54_take_profit_1,
                    m54_take_profit_2, m54_estimated_plr, m54_risk_level,
                    fusion_total, fusion_w1_catalyst, fusion_w2_policy,
                    fusion_w3_sector, fusion_w4_momentum, fusion_w5_capital,
                    fusion_w6_sentiment, fusion_w7_technical,
                    current_price, daily_change_pct, turnover_rate,
                    volume_ratio, market_cap, tail_volume_ratio,
                    market_volatility, market_trend, market_sentiment,
                    engine_version, pipeline_time_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                result.get('date', datetime.now().strftime('%Y-%m-%d')),
                result.get('timestamp', datetime.now().isoformat()),
                result.get('code', ''),
                result.get('name', ''),
                result.get('market', 'A'),
                result.get('setcode', '0'),

                int(l1.get('passed', False)), l1.get('score', 0) or 0,
                json.dumps(l1.get('details', {}), ensure_ascii=False),

                int(l2.get('passed', False)), l2.get('resonance_count', 0),
                l2.get('strong_count', 0), l2.get('score', 0) or 0,
                json.dumps(l2.get('top_patterns', []), ensure_ascii=False),

                int(l3.get('passed', True)), l3.get('risk_level', '安全'),
                l3.get('trap_score', 0) or 0,
                json.dumps(l3.get('triggered_traps', []), ensure_ascii=False),

                l4.get('resonance_count', 0), l4.get('total_score', 0) or 0,
                json.dumps(l4.get('dimensions', {}), ensure_ascii=False),
                l4.get('verdict', result.get('verdict', '')),
                result.get('action', 'pass'),

                result.get('m46_base_prob', 0) or 0,
                result.get('m46_prior', 0) or 0,
                result.get('m46_posterior', 0) or 0,
                result.get('m46_final_prob', 0) or 0,
                result.get('m46_confidence', '低'),
                int(result.get('m46_resonance', False)),
                result.get('m46_resonance_strength', 0) or 0,

                result.get('m51_intent_strength', 0) or 0,
                result.get('m51_direction', 'neutral'),
                result.get('m51_big_order_ratio', 0) or 0,
                result.get('m51_noise_score', 0) or 0,
                int(result.get('m51_filter_passed', False)),

                result.get('m54_position_pct', 0) or 0,
                result.get('m54_stop_loss', 0) or 0,
                result.get('m54_take_profit_1', 0) or 0,
                result.get('m54_take_profit_2', 0) or 0,
                result.get('m54_estimated_plr', 0) or 0,
                result.get('m54_risk_level', '安全'),

                result.get('fusion_total', 0) or 0,
                result.get('fusion_w1_catalyst', 0) or 0,
                result.get('fusion_w2_policy', 0) or 0,
                result.get('fusion_w3_sector', 0) or 0,
                result.get('fusion_w4_momentum', 0) or 0,
                result.get('fusion_w5_capital', 0) or 0,
                result.get('fusion_w6_sentiment', 0) or 0,
                result.get('fusion_w7_technical', 0) or 0,

                result.get('current_price', 0) or 0,
                result.get('daily_change_pct', 0) or 0,
                result.get('turnover_rate', 0) or 0,
                result.get('volume_ratio', 0) or 0,
                result.get('market_cap', 0) or 0,
                result.get('tail_volume_ratio', 0) or 0,

                (market_env or {}).get('volatility', 0.02),
                (market_env or {}).get('trend', 0.0),
                (market_env or {}).get('sentiment', 'neutral'),

                result.get('engine_version', 'V13.0'),
                result.get('pipeline_time_ms', 0) or 0,
            ))
            decision_id = cursor.lastrowid

            # 形态信号
            patterns = l2.get('patterns', {})
            if patterns:
                self._save_patterns(conn, decision_id, result['date'] if 'date' in result else datetime.now().strftime('%Y-%m-%d'), result['code'], result['name'], patterns)

            # 排雷记录
            traps = l3.get('traps', [])
            if traps:
                self._save_traps(conn, decision_id, result['date'] if 'date' in result else datetime.now().strftime('%Y-%m-%d'), result['code'], traps)

            # 维度得分
            dimensions = l4.get('dimensions', {})
            dimension_weights = l4.get('weights', {})
            if dimensions:
                self._save_dimensions(conn, decision_id, dimensions, dimension_weights)

            return decision_id

    def _save_patterns(self, conn, decision_id: int, date: str, code: str, name: str, patterns: dict):
        for pattern_name, detail in patterns.items():
            if isinstance(detail, dict):
                signal_strength = detail.get('strength', '无')
                score = detail.get('score', 0) or 0
                passed = int(bool(detail.get('passed', False) or signal_strength in ('强', '中')))
                key_metrics = json.dumps(detail.get('metrics', {}), ensure_ascii=False)
                details_json = json.dumps({k: v for k, v in detail.items() if k not in ('strength', 'score', 'passed', 'metrics')}, ensure_ascii=False, default=str)
            else:
                signal_strength = str(detail)
                score = 0.0
                passed = 0
                key_metrics = '{}'
                details_json = '{}'

            conn.execute('''
                INSERT INTO pattern_signals
                (decision_id, date, code, pattern_name, signal_strength, score, key_metrics, pass_threshold, details)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (decision_id, date, code, pattern_name, signal_strength, score, key_metrics, passed, details_json))

    def _save_traps(self, conn, decision_id: int, date: str, code: str, traps: list):
        for trap in traps:
            conn.execute('''
                INSERT INTO trap_records
                (decision_id, date, code, trap_category, risk_level, trap_score, triggered_fields, mitigation)
                VALUES (?,?,?,?,?,?,?,?)
            ''', (
                decision_id, date, code,
                trap.get('category', '未知'),
                trap.get('risk_level', '低'),
                trap.get('score', 0) or 0,
                json.dumps(trap.get('triggered', []), ensure_ascii=False),
                trap.get('mitigation', ''),
            ))

    def _save_dimensions(self, conn, decision_id: int, dimensions: dict, weights: dict):
        for dim_key, score in dimensions.items():
            weight = weights.get(dim_key, 0)
            weighted = score * weight
            passed = int(score >= 0.5)
            conn.execute('''
                INSERT INTO dimension_scores
                (decision_id, dimension_key, score, weight, weighted_score, passed, details)
                VALUES (?,?,?,?,?,?,?)
            ''', (
                decision_id, dim_key,
                round(score, 4) if isinstance(score, (int, float)) else 0.0,
                round(weight, 4) if isinstance(weight, (int, float)) else 0.0,
                round(weighted, 4),
                passed,
                '{}',
            ))

    def save_decisions_batch(self, results: List[dict], market_env: dict = None) -> List[int]:
        """批量保存决策（事务）"""
        ids = []
        with self._get_conn() as conn:
            for result in results:
                # 简化版批量保存，跳过子表
                pipeline = result.get('pipeline', {})
                l1 = pipeline.get('L1_T1初筛', {})
                l2 = pipeline.get('L2_形态共振', {})
                l3 = pipeline.get('L3_排雷检测', {})
                l4 = pipeline.get('L4_12维终审', {})

                cursor = conn.execute('''
                    INSERT INTO decisions (
                        date, timestamp, code, name,
                        l1_passed, l1_score,
                        l2_passed, l2_resonance_count, l2_score,
                        l3_passed, l3_risk_level, l3_trap_score,
                        l4_resonance_count, l4_total_score, l4_verdict, l4_action,
                        m46_base_prob, m46_prior, m46_posterior, m46_final_prob, m46_confidence,
                        m51_intent_strength, m51_direction, m51_big_order_ratio, m51_noise_score,
                        m54_position_pct, m54_estimated_plr,
                        fusion_total,
                        current_price, daily_change_pct, turnover_rate, volume_ratio,
                        engine_version
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    result.get('date', datetime.now().strftime('%Y-%m-%d')),
                    result.get('timestamp', datetime.now().isoformat()),
                    result.get('code', ''), result.get('name', ''),
                    int(l1.get('passed', False)), l1.get('score', 0) or 0,
                    int(l2.get('passed', False)), l2.get('resonance_count', 0), l2.get('score', 0) or 0,
                    int(l3.get('passed', True)), l3.get('risk_level', '安全'), l3.get('trap_score', 0) or 0,
                    l4.get('resonance_count', 0), l4.get('total_score', 0) or 0,
                    l4.get('verdict', ''), result.get('action', 'pass'),
                    result.get('m46_base_prob', 0) or 0, result.get('m46_prior', 0) or 0,
                    result.get('m46_posterior', 0) or 0, result.get('m46_final_prob', 0) or 0,
                    result.get('m46_confidence', '低'),
                    result.get('m51_intent_strength', 0) or 0, result.get('m51_direction', 'neutral'),
                    result.get('m51_big_order_ratio', 0) or 0, result.get('m51_noise_score', 0) or 0,
                    result.get('m54_position_pct', 0) or 0, result.get('m54_estimated_plr', 0) or 0,
                    result.get('fusion_total', 0) or 0,
                    result.get('current_price', 0) or 0, result.get('daily_change_pct', 0) or 0,
                    result.get('turnover_rate', 0) or 0, result.get('volume_ratio', 0) or 0,
                    result.get('engine_version', 'V13.0'),
                ))
                ids.append(cursor.lastrowid)
        return ids

    # ═══════════════════════════════════════════════
    # 事后回填
    # ═══════════════════════════════════════════════

    def update_execution_result(
        self,
        decision_id: int,
        t_day_high: float = None,
        t_day_close: float = None,
        t_day_change_pct: float = None,
        t_day_hit_limit: bool = False,
        t1_day_close: float = None,
        t1_day_change_pct: float = None,
        actual_plr: float = None,
        actual_result: str = None,
    ):
        """回填执行结果"""
        with self._get_conn() as conn:
            updates = []
            params = []

            if t_day_high is not None:
                updates.append('t_day_high=?'); params.append(t_day_high)
            if t_day_close is not None:
                updates.append('t_day_close=?'); params.append(t_day_close)
            if t_day_change_pct is not None:
                updates.append('t_day_change_pct=?'); params.append(t_day_change_pct)
            updates.append('t_day_hit_limit=?'); params.append(int(t_day_hit_limit))
            if t1_day_close is not None:
                updates.append('t1_day_close=?'); params.append(t1_day_close)
            if t1_day_change_pct is not None:
                updates.append('t1_day_change_pct=?'); params.append(t1_day_change_pct)
            if actual_plr is not None:
                updates.append('actual_plr=?'); params.append(actual_plr)
            if actual_result is not None:
                updates.append('actual_result=?'); params.append(actual_result)

            updates.append('updated_at=datetime(\'now\',\'localtime\')')

            if updates:
                sql = f"UPDATE decisions SET {', '.join(updates)} WHERE id=?"
                params.append(decision_id)
                conn.execute(sql, params)

    def update_execution_by_code_date(self, date: str, code: str, **kwargs):
        """按日期+代码回填"""
        with self._get_conn() as conn:
            row = conn.execute('SELECT id FROM decisions WHERE date=? AND code=? ORDER BY id DESC LIMIT 1', (date, code)).fetchone()
            if row:
                self.update_execution_result(row['id'], **kwargs)

    # ═══════════════════════════════════════════════
    # 每日汇总
    # ═══════════════════════════════════════════════

    def save_daily_summary(self, date: str, summary: dict):
        """保存或更新每日汇总"""
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO daily_summary (
                    date, total_screened, l1_passed, l2_passed, l3_passed,
                    l4_buy_signals, l4_watch_signals, l4_rejected,
                    avg_fusion_score, avg_m46_prob, avg_m54_plr,
                    top_pick_code, top_pick_name, top_pick_score,
                    market_volatility, market_trend, estimated_hit_rate, metrics_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                    total_screened=excluded.total_screened,
                    l1_passed=excluded.l1_passed,
                    l2_passed=excluded.l2_passed,
                    l3_passed=excluded.l3_passed,
                    l4_buy_signals=excluded.l4_buy_signals,
                    l4_watch_signals=excluded.l4_watch_signals,
                    l4_rejected=excluded.l4_rejected,
                    avg_fusion_score=excluded.avg_fusion_score,
                    avg_m46_prob=excluded.avg_m46_prob,
                    avg_m54_plr=excluded.avg_m54_plr,
                    top_pick_code=excluded.top_pick_code,
                    top_pick_name=excluded.top_pick_name,
                    top_pick_score=excluded.top_pick_score,
                    estimated_hit_rate=excluded.estimated_hit_rate,
                    metrics_json=excluded.metrics_json
            ''', (
                date,
                summary.get('total_screened', 0), summary.get('l1_passed', 0),
                summary.get('l2_passed', 0), summary.get('l3_passed', 0),
                summary.get('l4_buy_signals', 0), summary.get('l4_watch_signals', 0),
                summary.get('l4_rejected', 0),
                summary.get('avg_fusion_score', 0) or 0,
                summary.get('avg_m46_prob', 0) or 0,
                summary.get('avg_m54_plr', 0) or 0,
                summary.get('top_pick_code', ''), summary.get('top_pick_name', ''),
                summary.get('top_pick_score', 0) or 0,
                summary.get('market_volatility', 0) or 0,
                summary.get('market_trend', 0) or 0,
                summary.get('estimated_hit_rate', 0) or 0,
                json.dumps(summary.get('metrics', {}), ensure_ascii=False),
            ))

    def compute_daily_summary(self, date: str) -> dict:
        """从已有决策自动计算每日汇总"""
        with self._get_conn() as conn:
            # 基础统计
            stats = conn.execute('''
                SELECT
                    COUNT(*) as total,
                    SUM(l1_passed) as l1,
                    SUM(l2_passed) as l2,
                    SUM(CASE WHEN l3_risk_level != '黑名单' THEN 1 ELSE 0 END) as l3,
                    SUM(CASE WHEN l4_action = 'buy' THEN 1 ELSE 0 END) as buys,
                    SUM(CASE WHEN l4_action = 'watch' THEN 1 ELSE 0 END) as watches,
                    SUM(CASE WHEN l4_action IN ('pass', 'reject') THEN 1 ELSE 0 END) as rejected,
                    AVG(fusion_total) as avg_fusion,
                    AVG(m46_final_prob) as avg_m46,
                    AVG(m54_estimated_plr) as avg_plr
                FROM decisions WHERE date = ?
            ''', (date,)).fetchone()

            top = conn.execute('''
                SELECT code, name, fusion_total FROM decisions
                WHERE date = ? AND l4_action = 'buy'
                ORDER BY fusion_total DESC LIMIT 1
            ''', (date,)).fetchone()

            summary = {
                'total_screened': stats['total'] or 0,
                'l1_passed': stats['l1'] or 0,
                'l2_passed': stats['l2'] or 0,
                'l3_passed': stats['l3'] or 0,
                'l4_buy_signals': stats['buys'] or 0,
                'l4_watch_signals': stats['watches'] or 0,
                'l4_rejected': stats['rejected'] or 0,
                'avg_fusion_score': round(stats['avg_fusion'] or 0, 4),
                'avg_m46_prob': round(stats['avg_m46'] or 0, 4),
                'avg_m54_plr': round(stats['avg_plr'] or 0, 2),
            }
            if top:
                summary['top_pick_code'] = top['code']
                summary['top_pick_name'] = top['name']
                summary['top_pick_score'] = top['fusion_total']

            self.save_daily_summary(date, summary)
            return summary

    # ═══════════════════════════════════════════════
    # 回测存储
    # ═══════════════════════════════════════════════

    def save_backtest_run(self, run: dict) -> str:
        """保存回测运行记录，返回run_id"""
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO backtest_runs (
                    run_id, start_date, end_date, total_days, total_signals,
                    hit_count, partial_count, miss_count, trap_count,
                    hit_rate, avg_plr, max_plr, avg_return, max_drawdown, sharpe_ratio,
                    settings_json, summary_json, completed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                run['run_id'], run['start_date'], run['end_date'],
                run.get('total_days', 0), run.get('total_signals', 0),
                run.get('hit_count', 0), run.get('partial_count', 0),
                run.get('miss_count', 0), run.get('trap_count', 0),
                run.get('hit_rate', 0) or 0, run.get('avg_plr', 0) or 0,
                run.get('max_plr', 0) or 0, run.get('avg_return', 0) or 0,
                run.get('max_drawdown', 0) or 0, run.get('sharpe_ratio', 0) or 0,
                json.dumps(run.get('settings', {}), ensure_ascii=False),
                json.dumps(run.get('summary', {}), ensure_ascii=False),
                run.get('completed_at', datetime.now().isoformat()),
            ))
            return run['run_id']

    def save_backtest_signals_batch(self, signals: List[dict]):
        """批量保存回测信号"""
        with self._get_conn() as conn:
            conn.executemany('''
                INSERT INTO backtest_signals (
                    run_id, date, code, name, entry_price, exit_price,
                    return_pct, plr, hold_days, hit_limit, result, pipeline_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ''', [
                (
                    s['run_id'], s['date'], s['code'], s['name'],
                    s.get('entry_price', 0), s.get('exit_price', 0),
                    s.get('return_pct', 0), s.get('plr', 0),
                    s.get('hold_days', 1), int(s.get('hit_limit', False)),
                    s.get('result', 'pending'),
                    json.dumps(s.get('pipeline', {}), ensure_ascii=False, default=str),
                )
                for s in signals
            ])

    # ═══════════════════════════════════════════════
    # 校准记录
    # ═══════════════════════════════════════════════

    def log_calibration(self, date: str, module: str, param_name: str,
                        old_value: float, new_value: float,
                        deviation_pct: float = 0.0, reason: str = '',
                        source_data: dict = None):
        """记录参数校准"""
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO calibration_log
                (date, module, param_name, old_value, new_value, deviation_pct, reason, source_data)
                VALUES (?,?,?,?,?,?,?,?)
            ''', (
                date, module, param_name, old_value, new_value, deviation_pct, reason,
                json.dumps(source_data or {}, ensure_ascii=False),
            ))

    # ═══════════════════════════════════════════════
    # 市场快照
    # ═══════════════════════════════════════════════

    def save_market_snapshot(self, snapshot: dict):
        """保存市场快照"""
        with self._get_conn() as conn:
            conn.execute('''
                INSERT INTO market_snapshots (
                    timestamp, index_code, index_value, change_pct,
                    total_volume, up_count, down_count,
                    limit_up_count, limit_down_count,
                    hot_sectors, market_sentiment, volatility_20d
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                snapshot.get('timestamp', datetime.now().isoformat()),
                snapshot.get('index_code', '000001'),
                snapshot.get('index_value', 0) or 0,
                snapshot.get('change_pct', 0) or 0,
                snapshot.get('total_volume', 0) or 0,
                snapshot.get('up_count', 0),
                snapshot.get('down_count', 0),
                snapshot.get('limit_up_count', 0),
                snapshot.get('limit_down_count', 0),
                json.dumps(snapshot.get('hot_sectors', []), ensure_ascii=False),
                snapshot.get('market_sentiment', 'neutral'),
                snapshot.get('volatility_20d', 0) or 0,
            ))

    # ═══════════════════════════════════════════════
    # 查询接口
    # ═══════════════════════════════════════════════

    def get_decisions_by_date(self, date: str, action: str = None) -> List[dict]:
        """按日期查询决策"""
        with self._get_conn() as conn:
            if action:
                rows = conn.execute(
                    'SELECT * FROM decisions WHERE date=? AND l4_action=? ORDER BY fusion_total DESC',
                    (date, action)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM decisions WHERE date=? ORDER BY fusion_total DESC',
                    (date,)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_decision_history(self, code: str, days: int = 30) -> List[dict]:
        """查询某股票历史决策"""
        with self._get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM decisions WHERE code=? AND date >= date(?, ?) ORDER BY date DESC',
                (code, 'now', f'-{days} days')
            ).fetchall()
            return [dict(r) for r in rows]

    def get_top_picks(self, date: str = None, limit: int = 10) -> List[dict]:
        """获取得分最高的选股"""
        with self._get_conn() as conn:
            if date:
                rows = conn.execute(
                    'SELECT code, name, fusion_total, m46_final_prob, l4_verdict, l4_action FROM decisions WHERE date=? AND l4_action IN (?,?) ORDER BY fusion_total DESC LIMIT ?',
                    (date, 'buy', 'watch', limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT code, name, fusion_total, m46_final_prob, l4_verdict, l4_action FROM decisions WHERE l4_action IN (?,?) ORDER BY fusion_total DESC LIMIT ?',
                    ('buy', 'watch', limit)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_performance_stats(self, days: int = 30) -> dict:
        """获取绩效统计"""
        with self._get_conn() as conn:
            stats = conn.execute('''
                SELECT
                    COUNT(*) as total_decisions,
                    AVG(fusion_total) as avg_fusion,
                    AVG(m46_final_prob) as avg_m46,
                    MAX(m46_final_prob) as max_m46,
                    AVG(m54_estimated_plr) as avg_plr,
                    MAX(m54_estimated_plr) as max_plr,
                    SUM(CASE WHEN l4_action='buy' THEN 1 ELSE 0 END) as buy_count,
                    SUM(CASE WHEN actual_result='hit' THEN 1 ELSE 0 END) as hits,
                    SUM(CASE WHEN actual_result='trap' THEN 1 ELSE 0 END) as traps,
                    AVG(CASE WHEN actual_result IS NOT NULL THEN actual_plr END) as actual_avg_plr
                FROM decisions
                WHERE date >= date('now', ?)
            ''', (f'-{days} days',)).fetchone()

            return {
                'total_decisions': stats['total_decisions'] or 0,
                'avg_fusion_score': round(stats['avg_fusion'] or 0, 4),
                'avg_m46_prob': round(stats['avg_m46'] or 0, 4),
                'max_m46_prob': round(stats['max_m46'] or 0, 4),
                'avg_estimated_plr': round(stats['avg_plr'] or 0, 2),
                'max_estimated_plr': round(stats['max_plr'] or 0, 2),
                'buy_signals': stats['buy_count'] or 0,
                'verified_hits': stats['hits'] or 0,
                'verified_traps': stats['traps'] or 0,
                'actual_avg_plr': round(stats['actual_avg_plr'] or 0, 2),
            }

    def get_daily_summaries(self, days: int = 30) -> List[dict]:
        """获取每日汇总列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                'SELECT * FROM daily_summary WHERE date >= date(\'now\', ?) ORDER BY date DESC',
                (f'-{days} days',)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_backtest_runs(self, limit: int = 10) -> List[dict]:
        """获取回测运行记录"""
        with self._get_conn() as conn:
            rows = conn.execute(
                'SELECT run_id, start_date, end_date, total_signals, hit_rate, avg_plr, sharpe_ratio, started_at, completed_at FROM backtest_runs ORDER BY started_at DESC LIMIT ?',
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_recent_calibrations(self, module: str = None, days: int = 7) -> List[dict]:
        """获取最近的校准记录"""
        with self._get_conn() as conn:
            if module:
                rows = conn.execute(
                    'SELECT * FROM calibration_log WHERE module=? AND date >= date(\'now\', ?) ORDER BY date DESC, module',
                    (module, f'-{days} days')
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM calibration_log WHERE date >= date(\'now\', ?) ORDER BY date DESC, module',
                    (f'-{days} days',)
                ).fetchall()
            return [dict(r) for r in rows]

    def get_market_snapshots(self, hours: int = 24) -> List[dict]:
        """获取市场快照"""
        with self._get_conn() as conn:
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
            rows = conn.execute(
                'SELECT * FROM market_snapshots WHERE timestamp >= ? ORDER BY timestamp DESC LIMIT 50',
                (cutoff,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ═══════════════════════════════════════════════
    # 维护
    # ═══════════════════════════════════════════════

    def vacuum(self):
        """数据库压缩"""
        with self._get_conn() as conn:
            conn.execute('VACUUM')

    def get_table_stats(self) -> dict:
        """获取各表行数统计"""
        tables = ['decisions', 'pattern_signals', 'trap_records', 'dimension_scores',
                  'daily_summary', 'backtest_runs', 'calibration_log', 'market_snapshots']
        stats = {}
        with self._get_conn() as conn:
            for table in tables:
                row = conn.execute(f'SELECT COUNT(*) as cnt FROM {table}').fetchone()
                stats[table] = row['cnt']
            total_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
            stats['db_size_mb'] = round(total_size / (1024 * 1024), 2)
        return stats


# ═══════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════

_persistence_instance: Optional[PersistenceManager] = None


def get_persistence(db_path: str = None) -> PersistenceManager:
    """获取全局持久化管理器单例"""
    global _persistence_instance
    if _persistence_instance is None:
        _persistence_instance = PersistenceManager(db_path)
    return _persistence_instance


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def save_pipeline_result(result: dict, market_env: dict = None) -> int:
    """快捷保存完整流水线结果"""
    return get_persistence().save_decision(result, market_env)


def fill_execution_result(date: str, code: str, **kwargs):
    """快捷回填执行结果"""
    get_persistence().update_execution_by_code_date(date, code, **kwargs)


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 SQLite 持久化层 自测")
    print("=" * 60)

    pm = PersistenceManager()

    # 测试写入
    test_result = {
        'date': '2026-06-23',
        'timestamp': datetime.now().isoformat(),
        'code': '002415',
        'name': '海康威视',
        'pipeline': {
            'L1_T1初筛': {'passed': True, 'score': 0.78, 'details': {'gain': 0.038, 'turnover': 0.065}},
            'L2_形态共振': {'passed': True, 'resonance_count': 4, 'strong_count': 2, 'score': 0.72,
                'patterns': {'老鸭头': {'strength': '强', 'score': 0.85, 'passed': True, 'metrics': {'鸭头位置': 0.65}},
                             '2560战法': {'strength': '中', 'score': 0.62, 'passed': True, 'metrics': {'量能比': 1.45}}},
                'top_patterns': ['老鸭头(强)', '2560战法(中)']},
            'L3_排雷检测': {'passed': True, 'risk_level': '安全', 'trap_score': 0.12,
                'traps': [{'category': '技术诱多', 'risk_level': '低', 'score': 0.20, 'triggered': ['尾盘急拉'], 'mitigation': '涨幅未超阈值'}]},
            'L4_12维终审': {'resonance_count': 5, 'total_score': 0.72, 'verdict': '✅ 高置信度买入',
                'weights': {'D1_T1初筛': 0.12, 'D2_老鸭头': 0.12, 'D3_擒龙战法': 0.10},
                'dimensions': {'D1_T1初筛': 0.78, 'D2_老鸭头': 0.85}}
        },
        'action': 'buy',
        'm46_base_prob': 0.72, 'm46_prior': 0.32, 'm46_posterior': 0.68, 'm46_final_prob': 0.71,
        'm46_confidence': '高', 'm46_resonance': True, 'm46_resonance_strength': 0.75,
        'm51_intent_strength': 0.68, 'm51_direction': 'bullish', 'm51_big_order_ratio': 0.35,
        'm51_noise_score': 0.08, 'm51_filter_passed': True,
        'm54_position_pct': 0.15, 'm54_stop_loss': 27.5, 'm54_take_profit_1': 32.0,
        'm54_take_profit_2': 35.0, 'm54_estimated_plr': 3.2,
        'fusion_total': 0.72, 'fusion_w1_catalyst': 0.14, 'fusion_w2_policy': 0.07,
        'fusion_w3_sector': 0.11, 'fusion_w4_momentum': 0.15, 'fusion_w5_capital': 0.10,
        'fusion_w6_sentiment': 0.08, 'fusion_w7_technical': 0.07,
        'current_price': 29.5, 'daily_change_pct': 0.038, 'turnover_rate': 0.065,
        'volume_ratio': 1.8, 'market_cap': 1.2e11, 'tail_volume_ratio': 0.28,
        'pipeline_time_ms': 125.0,
    }

    decision_id = pm.save_decision(test_result)
    print(f"✅ 写入决策 ID={decision_id}")

    # 查询
    decisions = pm.get_decisions_by_date('2026-06-23')
    print(f"✅ 查询今日决策: {len(decisions)}条")
    for d in decisions:
        print(f"   {d['code']} {d['name']} 融合={d['fusion_total']:.2f} M46={d['m46_final_prob']:.2f} 动作={d['l4_action']}")

    # 回填
    pm.update_execution_result(decision_id, actual_result='hit', actual_plr=3.5, t_day_hit_limit=True)
    print(f"✅ 回填执行结果")

    # 汇总
    summary = pm.compute_daily_summary('2026-06-23')
    print(f"✅ 每日汇总: {summary}")

    # 统计
    stats = pm.get_performance_stats(30)
    print(f"✅ 30日绩效: 总决策={stats['total_decisions']} 买入信号={stats['buy_signals']} 验证命中={stats['verified_hits']}")

    # 表统计
    table_stats = pm.get_table_stats()
    print(f"✅ 数据库统计: {table_stats}")

    print("\n🎉 V13.0 SQLite持久化层 自测通过！")
