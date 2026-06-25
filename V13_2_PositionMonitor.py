#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 自动盈亏监控模块 — PositionMonitor                             ║
║  ================================================================    ║
║  核心使命：实时追踪持仓盈亏 → 动态止损止盈 → T+1验证回环 → 奖惩联动   ║
║                                                                      ║
║  功能矩阵：                                                           ║
║  ├── 持仓管理：多账户/多标的持仓登记、成本计算、分批记录               ║
║  ├── 实时监控：TDX实时行情获取、浮动盈亏计算、换手率监控              ║
║  ├── 风险控制：ATR动态止损线、移动止盈线、最大回撤预警                ║
║  ├── T+1验证：买入决策→次日结果→自动奖惩→进化记录                    ║
║  ├── 仪表盘：交互式HTML盈亏仪表盘、交易日志、趋势图                   ║
║  └── 持久化：holy_grail.db新增positions/trades/pnl_snapshots表       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
import sqlite3
import time
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict
from enum import Enum


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 常量与配置
# ═══════════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'holy_grail.db')
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
OUTPUT_DIR = os.path.dirname(__file__)

# ATR参数
ATR_PERIOD = 14
STOP_LOSS_ATR_MULT = 2.0           # 止损 = 成本 - ATR×2
TRAILING_STOP_ATR_MULT = 1.5       # 移动止盈触发
TAKE_PROFIT_R1_MULT = 1.5           # 第一止盈目标 = ATR×1.5
TAKE_PROFIT_R2_MULT = 3.0           # 第二止盈目标 = ATR×3.0

# 风险阈值
MAX_DRAWDOWN_PCT = 8.0              # 单日最大回撤预警
MAX_POSITION_PCT = 50.0             # 单票仓位上限预警
HIGH_TURNOVER_THRESHOLD = 30.0      # 换手率过高预警
LIMIT_UP_OPEN_WARNING = True        # 涨停开板预警

# 奖惩联动
REWARD_THRESHOLD_PCT = 5.0          # 获利≥5%触发奖励
HOLY_GRAIL_PCT = 9.8                # 涨停触发圣杯奖励
MISTAKE_THRESHOLD_PCT = -5.0        # 亏损≥5%触发惩罚


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 数据结构
# ═══════════════════════════════════════════════════════════════

class TradeDirection(Enum):
    BUY = "BUY"
    SELL = "SELL"

class TradeStatus(Enum):
    OPEN = "OPEN"       # 持仓中
    CLOSED = "CLOSED"   # 已平仓
    PARTIAL = "PARTIAL" # 部分平仓

class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    HOLY_GRAIL = "HOLY_GRAIL"

@dataclass
class Position:
    """持仓记录"""
    position_id: str = ""
    stock_code: str = ""
    stock_name: str = ""
    market: str = ""            # SH/SZ/BJ
    shares: int = 0
    avg_cost: float = 0.0
    total_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    floating_pnl: float = 0.0
    floating_pnl_pct: float = 0.0
    entry_date: str = ""
    entry_time: str = ""
    status: str = "OPEN"
    max_price_since_entry: float = 0.0
    min_price_since_entry: float = 0.0
    max_drawdown_pct: float = 0.0
    atr_14: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    turnover_rate: float = 0.0
    turnover_warning: bool = False

@dataclass
class Trade:
    """交易记录"""
    trade_id: str = ""
    position_id: str = ""
    stock_code: str = ""
    stock_name: str = ""
    direction: str = ""         # BUY/SELL
    shares: int = 0
    price: float = 0.0
    amount: float = 0.0
    timestamp: str = ""
    reason: str = ""
    t_day: str = ""             # 决策日
    t_plus_1_result: str = ""   # T+1验证结果
    reward_points: int = 0

@dataclass
class PnLSnapshot:
    """盈亏快照"""
    snapshot_id: str = ""
    timestamp: str = ""
    total_market_value: float = 0.0
    total_cost: float = 0.0
    total_floating_pnl: float = 0.0
    total_floating_pnl_pct: float = 0.0
    total_realized_pnl: float = 0.0
    position_count: int = 0
    positions_detail: str = ""  # JSON

@dataclass  
class RiskAlert:
    """风险告警"""
    alert_id: str = ""
    timestamp: str = ""
    stock_code: str = ""
    stock_name: str = ""
    alert_type: str = ""
    level: str = ""
    message: str = ""
    suggested_action: str = ""
    ack: bool = False


# ═══════════════════════════════════════════════════════════════
# SECTION 2: 数据库管理
# ═══════════════════════════════════════════════════════════════

class PositionDB:
    """持仓数据库管理"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_tables()
    
    def _init_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    market TEXT DEFAULT 'SZ',
                    shares INTEGER NOT NULL DEFAULT 0,
                    avg_cost REAL NOT NULL DEFAULT 0.0,
                    total_cost REAL NOT NULL DEFAULT 0.0,
                    current_price REAL DEFAULT 0.0,
                    market_value REAL DEFAULT 0.0,
                    floating_pnl REAL DEFAULT 0.0,
                    floating_pnl_pct REAL DEFAULT 0.0,
                    entry_date TEXT NOT NULL,
                    entry_time TEXT,
                    status TEXT DEFAULT 'OPEN',
                    max_price_since_entry REAL DEFAULT 0.0,
                    min_price_since_entry REAL DEFAULT 0.0,
                    max_drawdown_pct REAL DEFAULT 0.0,
                    atr_14 REAL DEFAULT 0.0,
                    stop_loss_price REAL DEFAULT 0.0,
                    take_profit_price REAL DEFAULT 0.0,
                    turnover_rate REAL DEFAULT 0.0,
                    turnover_warning INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime')),
                    updated_at TEXT DEFAULT (datetime('now','localtime'))
                );
                
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    position_id TEXT,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    shares INTEGER NOT NULL,
                    price REAL NOT NULL,
                    amount REAL NOT NULL,
                    timestamp TEXT NOT NULL,
                    reason TEXT,
                    t_day TEXT,
                    t_plus_1_result TEXT,
                    reward_points INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                
                CREATE TABLE IF NOT EXISTS pnl_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    total_market_value REAL NOT NULL,
                    total_cost REAL NOT NULL,
                    total_floating_pnl REAL NOT NULL,
                    total_floating_pnl_pct REAL NOT NULL,
                    total_realized_pnl REAL NOT NULL,
                    position_count INTEGER NOT NULL,
                    positions_detail TEXT,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                
                CREATE TABLE IF NOT EXISTS risk_alerts (
                    alert_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    suggested_action TEXT,
                    ack INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                
                CREATE TABLE IF NOT EXISTS holy_grail_analysis (
                    analysis_id TEXT PRIMARY KEY,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    t_day TEXT NOT NULL,
                    t_plus_1_day TEXT NOT NULL,
                    t_day_close REAL,
                    t_plus_1_open REAL,
                    t_plus_1_high REAL,
                    t_plus_1_low REAL,
                    t_plus_1_close REAL,
                    t_day_volume REAL,
                    t_plus_1_volume REAL,
                    volume_ratio REAL,
                    entry_price REAL,
                    exit_price REAL,
                    total_pnl REAL,
                    total_pnl_pct REAL,
                    pattern_type TEXT,
                    m46_score REAL,
                    m57_score REAL,
                    holy_grail_score REAL,
                    key_signals TEXT,
                    lessons_learned TEXT,
                    reward_sent INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now','localtime'))
                );
                
                CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
                CREATE INDEX IF NOT EXISTS idx_positions_stock ON positions(stock_code);
                CREATE INDEX IF NOT EXISTS idx_trades_stock ON trades(stock_code);
                CREATE INDEX IF NOT EXISTS idx_trades_t_day ON trades(t_day);
                CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON pnl_snapshots(timestamp);
                CREATE INDEX IF NOT EXISTS idx_alerts_stock ON risk_alerts(stock_code);
                CREATE INDEX IF NOT EXISTS idx_holy_grail_stock ON holy_grail_analysis(stock_code);
            """)
    
    # ── 持仓操作 ──
    
    def add_position(self, pos: Position) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO positions 
                (position_id, stock_code, stock_name, market, shares, avg_cost, total_cost,
                 current_price, market_value, floating_pnl, floating_pnl_pct,
                 entry_date, entry_time, status, max_price_since_entry, min_price_since_entry,
                 max_drawdown_pct, atr_14, stop_loss_price, take_profit_price, turnover_rate, turnover_warning)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (pos.position_id, pos.stock_code, pos.stock_name, pos.market, pos.shares,
                  pos.avg_cost, pos.total_cost, pos.current_price, pos.market_value,
                  pos.floating_pnl, pos.floating_pnl_pct, pos.entry_date, pos.entry_time,
                  pos.status, pos.max_price_since_entry, pos.min_price_since_entry,
                  pos.max_drawdown_pct, pos.atr_14, pos.stop_loss_price, pos.take_profit_price,
                  pos.turnover_rate, pos.turnover_warning))
        return True
    
    def update_position_price(self, position_id: str, current_price: float, 
                              turnover_rate: float = 0.0) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE positions SET current_price=?, turnover_rate=?,
                market_value=shares*?, floating_pnl=(shares*?)-total_cost,
                floating_pnl_pct=CASE WHEN total_cost>0 THEN ((shares*?)-total_cost)/total_cost*100 ELSE 0 END,
                turnover_warning=CASE WHEN ?>? THEN 1 ELSE 0 END,
                max_price_since_entry=MAX(max_price_since_entry, ?),
                min_price_since_entry=MIN(min_price_since_entry, ?),
                max_drawdown_pct=CASE WHEN min_price_since_entry>0 THEN 
                    (avg_cost-MIN(min_price_since_entry,?))/avg_cost*100 ELSE 0 END,
                updated_at=datetime('now','localtime')
                WHERE position_id=?
            """, (current_price, turnover_rate, current_price, current_price, current_price,
                  turnover_rate, HIGH_TURNOVER_THRESHOLD, current_price, current_price,
                  current_price, position_id))
        return True
    
    def get_open_positions(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM positions WHERE status='OPEN' AND shares>0 ORDER BY floating_pnl_pct DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    
    def get_position(self, stock_code: str) -> Optional[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM positions WHERE stock_code=? AND status='OPEN' ORDER BY entry_date DESC LIMIT 1",
                (stock_code,)
            ).fetchone()
        return dict(row) if row else None
    
    def close_position(self, position_id: str, exit_price: float, exit_date: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE positions SET status='CLOSED', current_price=?, 
                market_value=shares*?, floating_pnl=(shares*?)-total_cost,
                floating_pnl_pct=CASE WHEN total_cost>0 THEN ((shares*?)-total_cost)/total_cost*100 ELSE 0 END,
                updated_at=datetime('now','localtime')
                WHERE position_id=?
            """, (exit_price, exit_price, exit_price, exit_price, position_id))
    
    # ── 交易操作 ──
    
    def add_trade(self, trade: Trade) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO trades
                (trade_id, position_id, stock_code, stock_name, direction, shares, 
                 price, amount, timestamp, reason, t_day, t_plus_1_result, reward_points)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (trade.trade_id, trade.position_id, trade.stock_code, trade.stock_name,
                  trade.direction, trade.shares, trade.price, trade.amount, trade.timestamp,
                  trade.reason, trade.t_day, trade.t_plus_1_result, trade.reward_points))
        return True
    
    def get_t_day_trades(self, t_day: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE t_day=? AND direction='BUY' ORDER BY timestamp",
                (t_day,)
            ).fetchall()
        return [dict(r) for r in rows]
    
    def get_total_realized_pnl(self) -> float:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("""
                SELECT COALESCE(SUM(
                    CASE WHEN direction='SELL' THEN amount - (shares*(SELECT avg_cost FROM positions WHERE position_id=trades.position_id)) 
                    ELSE 0 END
                ), 0) as total FROM trades WHERE direction='SELL'
            """).fetchone()
        return row[0] if row else 0.0
    
    # ── 快照操作 ──
    
    def save_snapshot(self, snap: PnLSnapshot) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO pnl_snapshots
                (snapshot_id, timestamp, total_market_value, total_cost, total_floating_pnl,
                 total_floating_pnl_pct, total_realized_pnl, position_count, positions_detail)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (snap.snapshot_id, snap.timestamp, snap.total_market_value, snap.total_cost,
                  snap.total_floating_pnl, snap.total_floating_pnl_pct, snap.total_realized_pnl,
                  snap.position_count, snap.positions_detail))
        return True
    
    def get_snapshot_history(self, limit: int = 50) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pnl_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]
    
    # ── 告警操作 ──
    
    def add_alert(self, alert: RiskAlert) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO risk_alerts (alert_id, timestamp, stock_code, stock_name, 
                alert_type, level, message, suggested_action)
                VALUES (?,?,?,?,?,?,?,?)
            """, (alert.alert_id, alert.timestamp, alert.stock_code, alert.stock_name,
                  alert.alert_type, alert.level, alert.message, alert.suggested_action))
        return True
    
    def get_active_alerts(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM risk_alerts WHERE ack=0 ORDER BY timestamp DESC LIMIT 20"
            ).fetchall()
        return [dict(r) for r in rows]
    
    # ── 圣杯分析 ──
    
    def add_holy_grail_analysis(self, analysis: Dict) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO holy_grail_analysis
                (analysis_id, stock_code, stock_name, t_day, t_plus_1_day,
                 t_day_close, t_plus_1_open, t_plus_1_high, t_plus_1_low, t_plus_1_close,
                 t_day_volume, t_plus_1_volume, volume_ratio,
                 entry_price, exit_price, total_pnl, total_pnl_pct,
                 pattern_type, m46_score, m57_score, holy_grail_score,
                 key_signals, lessons_learned, reward_sent)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (analysis['analysis_id'], analysis['stock_code'], analysis['stock_name'],
                  analysis['t_day'], analysis['t_plus_1_day'],
                  analysis['t_day_close'], analysis['t_plus_1_open'], 
                  analysis['t_plus_1_high'], analysis['t_plus_1_low'], 
                  analysis['t_plus_1_close'], analysis['t_day_volume'],
                  analysis['t_plus_1_volume'], analysis['volume_ratio'],
                  analysis['entry_price'], analysis['exit_price'],
                  analysis['total_pnl'], analysis['total_pnl_pct'],
                  analysis['pattern_type'], analysis['m46_score'], 
                  analysis['m57_score'], analysis['holy_grail_score'],
                  analysis['key_signals'], analysis['lessons_learned'],
                  analysis['reward_sent']))
        return True


# ═══════════════════════════════════════════════════════════════
# SECTION 3: ATR计算器
# ═══════════════════════════════════════════════════════════════

class ATRCalculator:
    """ATR计算器 - 从K线数据计算ATR"""
    
    @staticmethod
    def calc_atr(kline_data: List[Dict], period: int = ATR_PERIOD) -> float:
        """从日K线数据计算ATR"""
        if len(kline_data) < period + 1:
            return 0.0
        
        tr_list = []
        for i in range(1, len(kline_data)):
            high = float(kline_data[i].get('high', 0))
            low = float(kline_data[i].get('low', 0))
            prev_close = float(kline_data[i-1].get('close', 0))
            
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        
        if len(tr_list) < period:
            return 0.0
        
        # Wilder's ATR with RMA-style smoothing
        atr = sum(tr_list[-period:]) / period
        for tr in tr_list[period:]:
            atr = (atr * (period - 1) + tr) / period
        
        return atr
    
    @staticmethod
    def calc_atr_from_simple(prices: List[Dict]) -> float:
        """简化的ATR计算"""
        if len(prices) < 2:
            return 0.0
        
        tr_sum = 0.0
        for i in range(-ATR_PERIOD, 0):
            if abs(i) <= len(prices):
                high = float(prices[i].get('High', prices[i].get('high', 0)))
                low = float(prices[i].get('Low', prices[i].get('low', 0)))
                tr_sum += abs(high - low)
        
        count = min(ATR_PERIOD, len(prices))
        return tr_sum / count if count > 0 else 0.0


# ═══════════════════════════════════════════════════════════════
# SECTION 4: 核心监控引擎
# ═══════════════════════════════════════════════════════════════

class PositionMonitor:
    """持仓监控主引擎"""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db = PositionDB(db_path)
        self.alerts: List[RiskAlert] = []
    
    def register_position(self, stock_code: str, stock_name: str, market: str,
                          shares: int, avg_cost: float, entry_date: str,
                          entry_time: str = "", atr: float = 0.0) -> Position:
        """注册新持仓"""
        pid = f"POS_{stock_code}_{entry_date.replace('-','')}_{int(time.time()) % 100000}"
        total_cost = shares * avg_cost
        
        pos = Position(
            position_id=pid,
            stock_code=stock_code,
            stock_name=stock_name,
            market=market,
            shares=shares,
            avg_cost=avg_cost,
            total_cost=total_cost,
            current_price=avg_cost,
            market_value=total_cost,
            floating_pnl=0.0,
            floating_pnl_pct=0.0,
            entry_date=entry_date,
            entry_time=entry_time,
            max_price_since_entry=avg_cost,
            min_price_since_entry=avg_cost,
            max_drawdown_pct=0.0,
            atr_14=atr,
            stop_loss_price=avg_cost - atr * STOP_LOSS_ATR_MULT if atr > 0 else avg_cost * 0.93,
            take_profit_price=avg_cost + atr * TAKE_PROFIT_R2_MULT if atr > 0 else avg_cost * 1.15,
        )
        
        self.db.add_position(pos)
        return pos
    
    def register_buy_trade(self, position_id: str, stock_code: str, stock_name: str,
                           shares: int, price: float, timestamp: str,
                           reason: str = "", t_day: str = "") -> Trade:
        """记录买入交易"""
        trade = Trade(
            trade_id=f"TRD_{stock_code}_{timestamp.replace(':','').replace(' ','_')}",
            position_id=position_id,
            stock_code=stock_code,
            stock_name=stock_name,
            direction="BUY",
            shares=shares,
            price=price,
            amount=shares * price,
            timestamp=timestamp,
            reason=reason,
            t_day=t_day or timestamp[:10],
        )
        self.db.add_trade(trade)
        return trade
    
    def register_sell_trade(self, position_id: str, stock_code: str, stock_name: str,
                            shares: int, price: float, timestamp: str,
                            t_plus_1_result: str = "", reason: str = "") -> Trade:
        """记录卖出交易"""
        trade = Trade(
            trade_id=f"TRD_{stock_code}_{timestamp.replace(':','').replace(' ','_')}",
            position_id=position_id,
            stock_code=stock_code,
            stock_name=stock_name,
            direction="SELL",
            shares=shares,
            price=price,
            amount=shares * price,
            timestamp=timestamp,
            reason=reason,
            t_plus_1_result=t_plus_1_result,
        )
        self.db.add_trade(trade)
        
        # 更新或关闭持仓
        pos = self.db.get_position(stock_code)
        if pos:
            remaining = pos['shares'] - shares
            if remaining <= 0:
                self.db.close_position(position_id, price, timestamp[:10])
            else:
                # 部分平仓 - 更新股数但不改变成本
                with sqlite3.connect(self.db.db_path) as conn:
                    conn.execute("""
                        UPDATE positions SET shares=?, total_cost=shares*avg_cost,
                        market_value=shares*?, floating_pnl=(shares*?)-total_cost,
                        updated_at=datetime('now','localtime')
                        WHERE position_id=?
                    """, (remaining, price, price, position_id))
        
        return trade
    
    def update_all_prices(self, quotes: Dict[str, Dict]) -> List[RiskAlert]:
        """批量更新所有持仓价格并生成告警"""
        self.alerts = []
        positions = self.db.get_open_positions()
        
        for pos in positions:
            code = pos['stock_code']
            if code in quotes:
                q = quotes[code]
                current_price = q.get('price', q.get('Now', 0))
                turnover = q.get('turnover', q.get('HSL', 0))
                
                self.db.update_position_price(pos['position_id'], current_price, turnover)
                
                # 风险检查
                self._check_risk(pos, current_price, turnover)
        
        return self.alerts
    
    def _check_risk(self, pos: Dict, current_price: float, turnover: float):
        """风险检查逻辑"""
        code = pos['stock_code']
        name = pos['stock_name']
        cost = pos['avg_cost']
        pnl_pct = ((current_price - cost) / cost * 100) if cost > 0 else 0
        
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # 1. 止损线检查
        if pos['stop_loss_price'] > 0 and current_price <= pos['stop_loss_price']:
            self.alerts.append(RiskAlert(
                alert_id=f"ALT_{code}_{int(time.time())}_SL",
                timestamp=now, stock_code=code, stock_name=name,
                alert_type="STOP_LOSS_HIT",
                level=AlertLevel.CRITICAL.value,
                message=f"触发止损线! 现价¥{current_price:.2f} ≤ 止损¥{pos['stop_loss_price']:.2f}",
                suggested_action=f"立即卖出{name}({code})，当前亏损{pnl_pct:.2f}%"
            ))
        
        # 2. 单日跌幅过大
        if pnl_pct <= -MAX_DRAWDOWN_PCT:
            self.alerts.append(RiskAlert(
                alert_id=f"ALT_{code}_{int(time.time())}_DD",
                timestamp=now, stock_code=code, stock_name=name,
                alert_type="LARGE_DRAWDOWN",
                level=AlertLevel.WARNING.value,
                message=f"单日浮亏{pnl_pct:.2f}%超过{MAX_DRAWDOWN_PCT}%阈值",
                suggested_action=f"关注{name}，考虑减仓或止损"
            ))
        
        # 3. 换手率过高
        if turnover >= HIGH_TURNOVER_THRESHOLD:
            self.alerts.append(RiskAlert(
                alert_id=f"ALT_{code}_{int(time.time())}_TO",
                timestamp=now, stock_code=code, stock_name=name,
                alert_type="HIGH_TURNOVER",
                level=AlertLevel.WARNING.value,
                message=f"换手率{turnover:.1f}%过高，筹码松动风险",
                suggested_action=f"换手率>30%显示筹码松动，考虑止盈部分仓位"
            ))
        
        # 4. 涨停开板检测
        if current_price >= cost * 1.19 and turnover > 40:
            self.alerts.append(RiskAlert(
                alert_id=f"ALT_{code}_{int(time.time())}_LU",
                timestamp=now, stock_code=code, stock_name=name,
                alert_type="LIMIT_UP_HIGH_TO",
                level=AlertLevel.WARNING.value,
                message=f"涨停+换手率{turnover:.1f}%，封板不稳",
                suggested_action=f"涨停板换手率超40%建议减仓50%锁定利润"
            ))
        
        # 5. 获利丰厚提醒
        if pnl_pct >= 15:
            self.alerts.append(RiskAlert(
                alert_id=f"ALT_{code}_{int(time.time())}_TP",
                timestamp=now, stock_code=code, stock_name=name,
                alert_type="BIG_PROFIT",
                level=AlertLevel.INFO.value,
                message=f"浮盈{pnl_pct:.1f}%，已达丰厚获利区间",
                suggested_action=f"建议设置移动止盈线，保护已有利润"
            ))
    
    def save_snapshot(self) -> PnLSnapshot:
        """保存盈亏快照"""
        positions = self.db.get_open_positions()
        
        total_mv = sum(p['market_value'] for p in positions)
        total_cost = sum(p['total_cost'] for p in positions)
        total_pnl = total_mv - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        
        snap = PnLSnapshot(
            snapshot_id=f"SNAP_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            total_market_value=total_mv,
            total_cost=total_cost,
            total_floating_pnl=total_pnl,
            total_floating_pnl_pct=total_pnl_pct,
            total_realized_pnl=self._calc_realized_pnl(),
            position_count=len(positions),
            positions_detail=json.dumps([{
                'code': p['stock_code'], 'name': p['stock_name'],
                'pnl_pct': p['floating_pnl_pct'], 'pnl': p['floating_pnl']
            } for p in positions], ensure_ascii=False)
        )
        
        self.db.save_snapshot(snap)
        return snap
    
    def _calc_realized_pnl(self) -> float:
        with sqlite3.connect(self.db.db_path) as conn:
            rows = conn.execute("""
                SELECT t.stock_code, t.shares, t.price, t.direction,
                       COALESCE((SELECT avg_cost FROM positions WHERE position_id=t.position_id), t.price) as cost
                FROM trades t WHERE t.direction='SELL'
            """).fetchall()
        
        total = 0.0
        for code, shares, price, direction, cost in rows:
            total += shares * (price - cost)
        return total
    
    def verify_t_plus_1(self, t_day_trades: List[Dict], t_plus_1_data: Dict[str, Dict]) -> List[Dict]:
        """T+1验证：对比T日买入决策与T+1实际结果"""
        results = []
        
        for trade in t_day_trades:
            code = trade['stock_code']
            entry_price = trade['price']
            
            if code not in t_plus_1_data:
                continue
            
            t1 = t_plus_1_data[code]
            t1_close = t1.get('close', t1.get('Close', 0))
            t1_high = t1.get('high', t1.get('High', 0))
            t1_low = t1.get('low', t1.get('Low', 0))
            t1_open = t1.get('open', t1.get('Open', 0))
            
            pnl_pct = ((t1_close - entry_price) / entry_price * 100) if entry_price > 0 else 0
            max_pnl_pct = ((t1_high - entry_price) / entry_price * 100) if entry_price > 0 else 0
            max_loss_pct = ((t1_low - entry_price) / entry_price * 100) if entry_price > 0 else 0
            
            # 判定结果
            is_holy_grail = pnl_pct >= HOLY_GRAIL_PCT  # 涨停
            is_big_win = pnl_pct >= BIG_RISE_PCT and not is_holy_grail
            is_win = pnl_pct >= 0.1
            is_loss = pnl_pct <= -5.0
            
            result = {
                'stock_code': code,
                'stock_name': trade['stock_name'],
                't_day': trade['t_day'],
                't_plus_1_day': t1.get('date', ''),
                'entry_price': entry_price,
                't1_open': t1_open,
                't1_close': t1_close,
                't1_high': t1_high,
                't1_low': t1_low,
                'pnl_pct': round(pnl_pct, 2),
                'max_pnl_pct': round(max_pnl_pct, 2),
                'max_loss_pct': round(max_loss_pct, 2),
                'verdict': 'HOLY_GRAIL' if is_holy_grail else (
                    'BIG_WIN' if is_big_win else ('WIN' if is_win else 'LOSS')
                ),
                'holy_grail': is_holy_grail,
            }
            results.append(result)
        
        return results
    
    def calc_reward_for_verification(self, results: List[Dict]) -> List[Dict]:
        """根据T+1验证结果计算奖惩分数"""
        for r in results:
            verdict = r['verdict']
            pnl = r['pnl_pct']
            
            if verdict == 'HOLY_GRAIL':
                # 涨停需要判断是否T+2续涨（这里标记为待验证）
                r['reward_points'] = 50     # A级：T+1涨停
                r['reward_tier'] = 'A_LIMIT_UP'
                r['pending_s_level'] = True  # 等待T+2验证S级
            elif verdict == 'BIG_WIN':
                r['reward_points'] = 20
                r['reward_tier'] = 'B_BIG_RISE'
            elif verdict == 'WIN':
                r['reward_points'] = 5
                r['reward_tier'] = 'C_SMALL_RISE'
            elif verdict == 'LOSS':
                if pnl <= -9.8:
                    r['reward_points'] = -50
                    r['reward_tier'] = 'P2_LIMIT_DOWN'
                else:
                    r['reward_points'] = -20
                    r['reward_tier'] = 'P1_DROP'
            else:
                r['reward_points'] = 0
                r['reward_tier'] = 'D_FLAT'
        
        return results


# ═══════════════════════════════════════════════════════════════
# SECTION 5: HTML仪表盘生成器
# ═══════════════════════════════════════════════════════════════

class DashboardGenerator:
    """生成交互式HTML盈亏仪表盘"""
    
    @staticmethod
    def generate(monitor: PositionMonitor, output_path: str = None) -> str:
        if output_path is None:
            output_path = os.path.join(OUTPUT_DIR, 'position_dashboard.html')
        
        positions = monitor.db.get_open_positions()
        snapshots = monitor.db.get_snapshot_history(50)
        alerts = monitor.db.get_active_alerts()
        
        total_mv = sum(p['market_value'] for p in positions)
        total_cost = sum(p['total_cost'] for p in positions)
        total_pnl = total_mv - total_cost
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        
        # 生成持仓行
        pos_rows = ""
        for i, p in enumerate(positions):
            pnl_color = '#ef4444' if p['floating_pnl'] >= 0 else '#10b981'
            pnl_bg = 'rgba(239,68,68,0.08)' if p['floating_pnl'] >= 0 else 'rgba(16,185,129,0.08)'
            to_warn = '⚠️' if p['turnover_warning'] else ''
            alert_class = 'alert-cell' if p['turnover_warning'] else ''
            
            pos_rows += f"""
            <tr style="background: {pnl_bg}">
                <td><span class="stock-badge">{p['stock_code']}</span></td>
                <td><strong>{p['stock_name']}</strong></td>
                <td class="num">{p['shares']:,}</td>
                <td class="num">¥{p['avg_cost']:.3f}</td>
                <td class="num">¥{p['current_price']:.2f}</td>
                <td class="num" style="color:{pnl_color};font-weight:700">
                    {p['floating_pnl_pct']:+.2f}%
                </td>
                <td class="num" style="color:{pnl_color};font-weight:700">
                    ¥{p['floating_pnl']:+,.2f}
                </td>
                <td class="num">¥{p['market_value']:,.2f}</td>
                <td class="{alert_class}">{to_warn}{p['turnover_rate']:.1f}%</td>
                <td class="num">¥{p['stop_loss_price']:.2f}</td>
            </tr>
            """
        
        # 告警行
        alert_rows = ""
        for a in alerts:
            lvl_color = {'CRITICAL': '#ef4444', 'WARNING': '#f59e0b', 'INFO': '#3b82f6', 'HOLY_GRAIL': '#8b5cf6'}
            a_color = lvl_color.get(a['level'], '#6b7280')
            alert_rows += f"""
            <tr>
                <td><span class="badge" style="background:{a_color}">{a['level']}</span></td>
                <td>{a['stock_name']}({a['stock_code']})</td>
                <td>{a['alert_type']}</td>
                <td>{a['message']}</td>
                <td class="suggest">{a['suggested_action']}</td>
            </tr>
            """
        
        # 快照趋势数据（用于Chart.js）
        snap_labels = json.dumps([s['timestamp'][-8:] for s in reversed(snapshots[-20:])])
        snap_values = json.dumps([round(s['total_floating_pnl'], 2) for s in reversed(snapshots[-20:])])
        
        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.2 持仓盈亏仪表盘 | 毕方灵犀·天眼</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }}
.header {{ text-align: center; padding: 24px 0; border-bottom: 1px solid #1e293b; margin-bottom: 24px; }}
.header h1 {{ font-size: 28px; background: linear-gradient(135deg, #8b5cf6, #3b82f6); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
.header .subtitle {{ color: #64748b; font-size: 14px; margin-top: 4px; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.kpi-card {{ background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }}
.kpi-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; }}
.kpi-value {{ font-size: 28px; font-weight: 700; margin: 8px 0; }}
.kpi-sub {{ font-size: 13px; color: #94a3b8; }}
.red {{ color: #ef4444; }} .green {{ color: #10b981; }}
.section {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 20px; border: 1px solid #334155; }}
.section h2 {{ font-size: 18px; margin-bottom: 16px; color: #94a3b8; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 10px 8px; border-bottom: 2px solid #334155; color: #64748b; font-weight: 600; }}
td {{ padding: 8px; border-bottom: 1px solid #1e293b; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.stock-badge {{ background: #334155; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 12px; }}
.badge {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; color: white; }}
.suggest {{ font-size: 12px; color: #f59e0b; max-width: 300px; }}
.alert-cell {{ background: rgba(245,158,11,0.12) !important; }}
.chart-container {{ height: 300px; margin-top: 16px; }}
.status-bar {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
.status-item {{ display: flex; align-items: center; gap: 8px; background: #1e293b; padding: 10px 16px; border-radius: 8px; border: 1px solid #334155; }}
.status-dot {{ width: 10px; height: 10px; border-radius: 50%; }}
.status-dot.green {{ background: #10b981; }}
.status-dot.yellow {{ background: #f59e0b; }}
.status-dot.red {{ background: #ef4444; }}
.footer {{ text-align: center; padding: 16px; color: #475569; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="header">
    <h1>V13.2 持仓盈亏实时仪表盘</h1>
    <div class="subtitle">毕方灵犀·天眼贝叶斯概率交易系统 | 更新于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
</div>

<div class="status-bar">
    <div class="status-item">
        <div class="status-dot green"></div>
        <span>持仓数：{len(positions)}</span>
    </div>
    <div class="status-item">
        <div class="status-dot {'green' if total_pnl >= 0 else 'red'}"></div>
        <span>浮动盈亏：<strong class="{'red' if total_pnl >= 0 else 'green'}">¥{total_pnl:+,.2f}</strong></span>
    </div>
    <div class="status-item">
        <div class="status-dot {'yellow' if alerts else 'green'}"></div>
        <span>活跃告警：{len(alerts)}</span>
    </div>
</div>

<div class="kpis">
    <div class="kpi-card">
        <div class="kpi-label">总市值</div>
        <div class="kpi-value">¥{total_mv:,.0f}</div>
        <div class="kpi-sub">{len(positions)}只持仓</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">总成本</div>
        <div class="kpi-value">¥{total_cost:,.0f}</div>
        <div class="kpi-sub">平均成本</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">浮动盈亏</div>
        <div class="kpi-value {'red' if total_pnl >= 0 else 'green'}">¥{total_pnl:+,.0f}</div>
        <div class="kpi-sub {'red' if total_pnl_pct >= 0 else 'green'}">{total_pnl_pct:+.2f}%</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">已实现盈亏</div>
        <div class="kpi-value">¥{(monitor._calc_realized_pnl()):+,.0f}</div>
        <div class="kpi-sub">累计已平仓</div>
    </div>
</div>

<div class="section">
    <h2>持仓明细</h2>
    <table>
        <thead>
            <tr>
                <th>代码</th><th>名称</th><th>股数</th><th>成本</th>
                <th>现价</th><th>盈亏%</th><th>盈亏额</th><th>市值</th>
                <th>换手率</th><th>止损线</th>
            </tr>
        </thead>
        <tbody>{pos_rows}</tbody>
    </table>
</div>

{('<div class="section"><h2>活跃告警</h2><table><thead><tr><th>级别</th><th>标的</th><th>类型</th><th>消息</th><th>建议操作</th></tr></thead><tbody>' + alert_rows + '</tbody></table></div>') if alerts else ''}

<div class="section">
    <h2>盈亏趋势（最近20次快照）</h2>
    <div class="chart-container">
        <canvas id="pnlChart"></canvas>
    </div>
</div>

<div class="footer">
    毕方灵犀·天眼 V13.2 PositionMonitor | 圣杯使命：T日尾盘选股 → T+1涨停 → T+2续涨
</div>

<script>
const ctx = document.getElementById('pnlChart').getContext('2d');
new Chart(ctx, {{
    type: 'line',
    data: {{
        labels: {snap_labels},
        datasets: [{{
            label: '浮动盈亏 (¥)',
            data: {snap_values},
            borderColor: '#8b5cf6',
            backgroundColor: 'rgba(139,92,246,0.1)',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
            x: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }} }}, grid: {{ color: '#1e293b' }} }},
            y: {{ ticks: {{ color: '#64748b', font: {{ size: 10 }}, callback: v => '¥' + v.toLocaleString() }}, grid: {{ color: '#1e293b' }} }}
        }}
    }}
}});
</script>
</body>
</html>'''
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return output_path


# ═══════════════════════════════════════════════════════════════
# SECTION 6: 实战初始化 - 加载当前持仓
# ═══════════════════════════════════════════════════════════════

def init_current_positions(monitor: PositionMonitor) -> Dict:
    """初始化韩进薇6月24日实际持仓"""
    
    # ═══ 高特电子 301669 ═══
    # 两层结构：
    #  A层: 长期底仓 600股@¥6.94（极早期入场，浮盈+551%）
    #  B层: T+0交易 6/23买入3700股@avg¥38.137 → 6/24卖出3100股@avg¥44.21
    #      已实现利润: +¥18,839.18
    
    # A层: 注册底仓（600股@¥6.94，不参与卖出扣减）
    pos_gt_long = monitor.register_position(
        stock_code="301669_A", stock_name="高特电子[底仓]", market="SZ",
        shares=600, avg_cost=6.94, 
        entry_date="2026-06-01", entry_time="09:30",
        atr=3.85
    )
    
    # B层T+0交易: 单独记录（不绑定position，用独立trade记录）
    # 6/23买入: 600@38.220 + 1300@38.350 + 900@38.020 + 900@37.890 = 3700股
    buys_gt = [
        ("2026-06-23 10:45", 600, 38.220),
        ("2026-06-23 10:48", 1300, 38.350),
        ("2026-06-23 13:54", 900, 38.020),
        ("2026-06-23 14:05", 900, 37.890),
    ]
    for ts, sh, pr in buys_gt:
        monitor.register_buy_trade(
            pos_gt_long.position_id, "301669", "高特电子",
            sh, pr, ts, reason="T日尾盘超跌买入", t_day="2026-06-23"
        )
    
    # 6/24卖出: 900@42.600 + 500@43.750 + 1700@45.200 = 3100股, 均价¥44.21
    sells_gt = [
        ("2026-06-24 09:48", 900, 42.600, "首波卖出-封板前获利"),
        ("2026-06-24 10:48", 500, 43.750, "突破加仓卖出"),
        ("2026-06-24 11:03", 1700, 45.200, "涨停板清仓-完美"),
    ]
    for ts, sh, pr, reason in sells_gt:
        monitor.register_sell_trade(
            pos_gt_long.position_id, "301669", "高特电子",
            sh, pr, ts, 
            t_plus_1_result="HOLY_GRAIL",
            reason=reason
        )
    
    # ═══ 蜀道装备 300540 ═══
    pos_sd = monitor.register_position(
        stock_code="300540", stock_name="蜀道装备", market="SZ",
        shares=1300, avg_cost=27.66,
        entry_date="2026-06-23", entry_time="14:30",
        atr=2.15
    )
    
    monitor.register_buy_trade(
        pos_sd.position_id, "300540", "蜀道装备",
        1300, 27.66, "2026-06-23 14:30",
        reason="板块轮动买入", t_day="2026-06-23"
    )
    
    # ═══ 创远信科 920961 ═══
    pos_cy = monitor.register_position(
        stock_code="920961", stock_name="创远信科", market="BJ",
        shares=100, avg_cost=21.132,
        entry_date="2026-06-23", entry_time="14:45",
        atr=1.85
    )
    
    monitor.register_buy_trade(
        pos_cy.position_id, "920961", "创远信科",
        100, 21.132, "2026-06-23 14:45",
        reason="北交所观察仓", t_day="2026-06-23"
    )
    
    return {
        '301669': pos_gt_long,
        '300540': pos_sd,
        '920961': pos_cy,
    }


if __name__ == "__main__":
    """主入口：初始化持仓 + 注册交易 + 生成仪表盘"""
    print("╔══════════════════════════════════════════════════════╗")
    print("║  V13.2 PositionMonitor — 持仓盈亏监控初始化           ║")
    print("╚══════════════════════════════════════════════════════╝")
    
    monitor = PositionMonitor()
    
    # Step 1: 初始化持仓
    print("\n[1/4] 初始化当前持仓...")
    positions = init_current_positions(monitor)
    for code, pos in positions.items():
        print(f"  ✅ {pos.stock_name}({code}) {pos.shares}股 成本¥{pos.avg_cost:.3f}")
    
    # Step 2: 更新最新价格
    print("\n[2/4] 更新实时价格...")
    latest_prices = {
        "301669": {"price": 45.20, "turnover": 44.04},
        "301669_A": {"price": 45.20, "turnover": 44.04},  # 底仓同一价格
        "300540": {"price": 25.67, "turnover": 7.77},
        "920961": {"price": 21.81, "turnover": 3.10},
    }
    alerts = monitor.update_all_prices(latest_prices)
    if alerts:
        print(f"  ⚠️ {len(alerts)}条风险告警:")
        for a in alerts:
            print(f"    {a.level}: {a.message}")
    
    # Step 3: 保存快照
    print("\n[3/4] 保存盈亏快照...")
    snap = monitor.save_snapshot()
    print(f"  ✅ 快照: 总市值¥{snap.total_market_value:,.2f} | 浮动盈亏¥{snap.total_floating_pnl:+,.2f} ({snap.total_floating_pnl_pct:+.2f}%)")
    
    # Step 4: 生成仪表盘
    print("\n[4/4] 生成HTML仪表盘...")
    dashboard_path = DashboardGenerator.generate(monitor)
    print(f"  ✅ 仪表盘已生成: {dashboard_path}")
    
    # 统计摘要
    print("\n" + "=" * 60)
    print("📊 持仓盈亏摘要")
    print("=" * 60)
    open_positions = monitor.db.get_open_positions()
    for p in open_positions:
        pnl_color = "🔴" if p['floating_pnl'] >= 0 else "🟢"  # 中国习惯:红涨绿跌
        print(f"  {pnl_color} {p['stock_name']}({p['stock_code']}) | "
              f"{p['shares']}股 | 成本¥{p['avg_cost']:.3f} | 现价¥{p['current_price']:.2f} | "
              f"浮盈亏{p['floating_pnl_pct']:+.2f}% | ¥{p['floating_pnl']:+,.2f}")
    
    total_cost = sum(p['total_cost'] for p in open_positions)
    total_mv = sum(p['market_value'] for p in open_positions)
    total_pnl = total_mv - total_cost
    print(f"\n  💰 总市值: ¥{total_mv:,.2f}")
    print(f"  📋 总成本: ¥{total_cost:,.2f}")
    print(f"  {'🔴' if total_pnl >= 0 else '🟢'} 浮动盈亏: ¥{total_pnl:+,.2f} ({(total_pnl/total_cost*100):+.2f}%)")
    
    print("\n✅ PositionMonitor初始化完成！圣杯之路，每一步都在逼近目标。")
