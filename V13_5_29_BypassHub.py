#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.30_P9 BypassHub — ★9旁路强制调度中心 (P8+P9已升级)
==================================================================
创建: 2026-07-09 | P8新增: 11:33 (浪潮信息000977) | P9新增: 11:53 (贝斯特300580)
根因: D49/D50/7旁路仅作为独立文件存在, 主流程从未导入 | P8: 中报事件盲区 | P9: 极端超跌单路径盲区
目标: 蒸馏为一个强制调度模块, T4自动化强制执行, 杜绝Agent手动调用遗漏

9条旁路路径 (≥1条激活 = 绕过F3五确认≥3):
  P1: D49v2_A ≥ 8 + D29 ≥ 6  → 长下影线日内反转 (底部反转型, 需洗盘确认)
  P2: D49v2_B ≥ 6 + D28 ≥ 6  → 大实体阳线+催化反转 (趋势延续型, 需催化确认)
  P3: D51     ≥ 8 + D28 ≥ 6  → 趋势延续+催化 (MA20上升+回调+催化)
  P4: D50     ≥ 7 + D29 ≥ 6  → 尾盘放量反转 (底部反转, 需洗盘确认)
  P5: D57     ≥ 7 + D29 ≥ 6  → 突破后缩量回踩 (突破回踩确认)
  P6: 反接飞刀V2 缩量旁路   → 跌>5%但缩量非跌停→PASS不降权
  P7: MEG-F6 板块Override  → D52≥8+板块涨+MA5上升→ORANGE→x0.5
  P8: 中报事件预判         → E1-E5≥3 中报窗+Q1高增+赛道+回撤+缩量→D29门槛降至3
  P9: 投降衰竭单路径覆盖   → D59≥16(≥2/4: ≥5连跌+M46≥0.85+缩量+近底) + M46激活→单路径超信

集成方式:
  from V13_5_29_BypassHub import BypassHub
  verdict = BypassHub.check_all(code, klines_daily, quote_data, scores)
  if verdict["bypass"]:
      stock["recommendation"] = promote(stock["recommendation"])
"""

import json
import math
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger("V13_5_29_BypassHub")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[BypassHub] %(levelname)s: %(message)s"))
    logger.addHandler(h)

# ═══════════════════════════════════════════════════════════════
# 导入已有模块 (延迟导入, 避免循环依赖)
# ═══════════════════════════════════════════════════════════════

def _import_reversal_dims():
    """延迟导入 V13.5.23 反转维度"""
    try:
        from V13_5_23_ReversalDimensions import (
            calc_d47_capitulation_bottom,
            calc_d48_oversold_divergence,
            calc_d49_long_lower_shadow,
            calc_d50_late_surge,
            ReversalBypass,
        )
        return {
            "d47": calc_d47_capitulation_bottom,
            "d48": calc_d48_oversold_divergence,
            "d49": calc_d49_long_lower_shadow,
            "d50": calc_d50_late_surge,
            "ReversalBypass": ReversalBypass,
        }
    except ImportError as e:
        logger.warning(f"V13_5_23_ReversalDimensions 不可用: {e}")
        return None

def _import_trend_dims():
    """延迟导入 V13.5.25 趋势/反转V2维度"""
    try:
        from V13_5_25_TrendContinuation import (
            calc_d49v2_dual_mode_reversal,
            calc_d51_trend_continuation,
            calc_d52_price_hike_cycle,
            calc_meg_f6_sector_override,
            ReversalBypassV25,
        )
        return {
            "d49v2": calc_d49v2_dual_mode_reversal,
            "d51": calc_d51_trend_continuation,
            "d52": calc_d52_price_hike_cycle,
            "meg_f6": calc_meg_f6_sector_override,
            "ReversalBypassV25": ReversalBypassV25,
        }
    except ImportError as e:
        logger.warning(f"V13_5_25_TrendContinuation 不可用: {e}")
        return None

def _import_root_fixes():
    """延迟导入 V13.5.27 根因修复"""
    try:
        from V13_5_27_RootCause_Fixes import (
            calc_d57_breakout_pullback,
            anti_catch_falling_knife_v2,
        )
        return {
            "d57": calc_d57_breakout_pullback,
            "anti_knife_v2": anti_catch_falling_knife_v2,
        }
    except ImportError as e:
        logger.warning(f"V13_5_27_RootCause_Fixes 不可用: {e}")
        return None


# ═══════════════════════════════════════════════════════════════
# 核心: BypassHub 强制调度类
# ═══════════════════════════════════════════════════════════════

class BypassHub:
    """
    V13.5.30_P9 9旁路强制调度中心 (P8中报预判 + P9投降衰竭)
    
    使用方式:
        hub = BypassHub()
        verdict = hub.check_all(
            code="603137",
            klines_daily=klines,      # 日K线列表 [{open,high,low,close,vol}, ...] (最新=最后一个)
            d29_score=9,              # D29双洗盘得分
            d28_score=3,              # D28催化得分
            five_confirm_passed=1,    # 五确认通过数
            stock_decline_pct=-3.5,   # 当日涨跌幅(%)
            sector_change_pct=0.5,    # 板块涨跌幅(%)
            volume_vs_5d=0.52,        # 当日量/5日均量
            is_limit_down=False,      # 是否跌停
            position_from_20d_low=11.0, # 距20日低点距离(%)
            current_defcon="YELLOW",  # MEG DEFCON等级
            catalyst_data=None,       # 催化数据 {"type":"涨价","strength":7,"name":"..."}
            price_hike_data=None,     # 涨价周期数据
            sector_ma5_rising=False,  # 板块MA5是否上升
            stock_20d_pct=-12.0,      # 个股20日涨跌幅
            klines_5min=None,         # 5分钟K线 (可选, 用于D50)
            # === P9 投降衰竭参数 ===
            consecutive_decline_days=6,  # 连续下跌天数
            m46_score=0.90,           # M46超跌得分
            position_from_60d_low=0.0, # 距60日低点%(0=就在低点)
        )
    """
    
    # 9条旁路路径定义
    BYPASS_PATHS = {
        "P1_D49v2_A": {
            "name": "长下影线日内反转",
            "condition": "D49v2_A ≥ 8 + D29 ≥ 6",
            "type": "底部反转",
            "confirm_type": "洗盘",
        },
        "P2_D49v2_B": {
            "name": "大实体阳线催化反转",
            "condition": "D49v2_B ≥ 6 + D28 ≥ 6",
            "type": "趋势延续",
            "confirm_type": "催化",
        },
        "P3_D51": {
            "name": "趋势延续+催化",
            "condition": "D51 ≥ 8 + D28 ≥ 6",
            "type": "趋势延续",
            "confirm_type": "催化",
        },
        "P4_D50": {
            "name": "尾盘放量反转",
            "condition": "D50 ≥ 7 + D29 ≥ 6",
            "type": "底部反转",
            "confirm_type": "洗盘",
        },
        "P5_D57": {
            "name": "突破后缩量回踩",
            "condition": "D57 ≥ 7 + D29 ≥ 6",
            "type": "突破回踩",
            "confirm_type": "洗盘",
        },
        "P6_ANTI_KNIFE": {
            "name": "反接飞刀V2缩量旁路",
            "condition": "跌>5% + 缩量(<60%) + 非跌停 + 近底部",
            "type": "风险旁路",
            "confirm_type": "量价",
        },
        "P7_MEG_F6": {
            "name": "MEG-F6板块Override",
            "condition": "D52≥8 + 板块涨 + MA5上升 → ORANGE→x0.5",
            "type": "仓位Override",
            "confirm_type": "板块",
        },
        "P8_EARNINGS_SEASON": {
            "name": "中报事件预判旁路",
            "condition": "E1-E5≥3 + D29门槛3 + D49≥6 → 绕过F3",
            "type": "事件预判旁路",
            "confirm_type": "时序+基本面",
        },
        "P9_CAPITULATION": {
            "name": "投降衰竭单路径覆盖",
            "condition": "D59≥16(≥2/4: ≥5连跌+M46≥0.85+缩量+近底) + M46激活 → 单路径超信",
            "type": "单路径Override",
            "confirm_type": "极端超跌",
        },
    }
    
    def __init__(self):
        self._reversal = _import_reversal_dims()
        self._trend = _import_trend_dims()
        self._fixes = _import_root_fixes()
        
        self.available_paths = []
        if self._reversal:
            self.available_paths.extend(["P1_D49v2_A", "P4_D50"])
        if self._trend:
            self.available_paths.extend(["P1_D49v2_A", "P2_D49v2_B", "P3_D51", "P7_MEG_F6"])
        if self._fixes:
            self.available_paths.extend(["P5_D57", "P6_ANTI_KNIFE"])
        
        self.available_paths = list(set(self.available_paths))
        # P8 中报事件预判旁路 — 不依赖外部模块, 纯内部逻辑
        self.available_paths.append("P8_EARNINGS_SEASON")
        # P9 投降衰竭单路径覆盖 — 不依赖外部模块, 纯内部逻辑
        self.available_paths.append("P9_CAPITULATION")
        # P6 反接飞刀V2 — 确保始终可用(不依赖外部模块)
        if "P6_ANTI_KNIFE" not in self.available_paths:
            self.available_paths.append("P6_ANTI_KNIFE")
        logger.info(f"BypassHub V13.5.30_P9 就绪 | {len(self.available_paths)}/9 条旁路可用: {self.available_paths}")
    
    # ═══════════════════════════════════════════════════════════
    # 各旁路独立检查
    # ═══════════════════════════════════════════════════════════
    
    def _check_p1_d49v2_a(self, d49a_score: int, d29_score: int) -> Dict:
        """P1: D49v2模式A ≥ 8 + D29 ≥ 6 → 长下影线日内反转"""
        ok = d49a_score >= 8 and d29_score >= 6
        return {
            "path": "P1_D49v2_A",
            "active": ok,
            "scores": {"D49v2_A": d49a_score, "D29": d29_score},
            "reason": f"长下影线反转: D49v2-A={d49a_score}{'≥' if d49a_score>=8 else '<'}8 + D29={d29_score}{'≥' if d29_score>=6 else '<'}6" if ok else f"未满足: D49v2-A={d49a_score}(需≥8) D29={d29_score}(需≥6)",
        }
    
    def _check_p2_d49v2_b(self, d49b_score: int, d28_score: int) -> Dict:
        """P2: D49v2模式B ≥ 6 + D28 ≥ 6 → 大实体阳线+催化"""
        ok = d49b_score >= 6 and d28_score >= 6
        return {
            "path": "P2_D49v2_B",
            "active": ok,
            "scores": {"D49v2_B": d49b_score, "D28": d28_score},
            "reason": f"大实体阳线催化: D49v2-B={d49b_score}{'≥' if d49b_score>=6 else '<'}6 + D28={d28_score}{'≥' if d28_score>=6 else '<'}6" if ok else f"未满足: D49v2-B={d49b_score}(需≥6) D28={d28_score}(需≥6)",
        }
    
    def _check_p3_d51(self, d51_score: int, d28_score: int) -> Dict:
        """P3: D51 ≥ 8 + D28 ≥ 6 → 趋势延续+催化"""
        ok = d51_score >= 8 and d28_score >= 6
        return {
            "path": "P3_D51",
            "active": ok,
            "scores": {"D51": d51_score, "D28": d28_score},
            "reason": f"趋势延续催化: D51={d51_score}{'≥' if d51_score>=8 else '<'}8 + D28={d28_score}{'≥' if d28_score>=6 else '<'}6" if ok else f"未满足: D51={d51_score}(需≥8) D28={d28_score}(需≥6)",
        }
    
    def _check_p4_d50(self, d50_score: int, d29_score: int) -> Dict:
        """P4: D50 ≥ 7 + D29 ≥ 6 → 尾盘放量反转"""
        ok = d50_score >= 7 and d29_score >= 6
        return {
            "path": "P4_D50",
            "active": ok,
            "scores": {"D50": d50_score, "D29": d29_score},
            "reason": f"尾盘放量反转: D50={d50_score}{'≥' if d50_score>=7 else '<'}7 + D29={d29_score}{'≥' if d29_score>=6 else '<'}6" if ok else f"未满足: D50={d50_score}(需≥7) D29={d29_score}(需≥6)",
        }
    
    def _check_p5_d57(self, d57_score: float, d29_score: int) -> Dict:
        """P5: D57 ≥ 7 + D29 ≥ 6 → 突破后缩量回踩"""
        ok = d57_score >= 7.0 and d29_score >= 6
        return {
            "path": "P5_D57",
            "active": ok,
            "scores": {"D57": d57_score, "D29": d29_score},
            "reason": f"突破回踩确认: D57={d57_score:.1f}{'≥' if d57_score>=7 else '<'}7 + D29={d29_score}{'≥' if d29_score>=6 else '<'}6" if ok else f"未满足: D57={d57_score:.1f}(需≥7) D29={d29_score}(需≥6)",
        }
    
    def _check_p6_anti_knife(self, stock_pct: float, volume_vs_5d: float,
                              is_limit_down: bool, position_from_20d_low: float,
                              sector_pct: float = 0) -> Dict:
        """P6: 反接飞刀V2缩量旁路"""
        is_drying = volume_vs_5d < 0.60
        is_near_bottom = position_from_20d_low < 25.0
        
        # 核心缩量旁路: 跌>5% + 缩量 + 非跌停 + 近底部
        if stock_pct < -5.0 and is_drying and not is_limit_down and is_near_bottom:
            return {
                "path": "P6_ANTI_KNIFE",
                "active": True,
                "scores": {"跌%": round(stock_pct, 1), "量比": round(volume_vs_5d, 2), "距低%": round(position_from_20d_low, 1)},
                "reason": f"缩量洗盘旁路: 跌{stock_pct:.1f}% 量比{volume_vs_5d:.2f}(<0.6) 非跌停 距低{position_from_20d_low:.0f}% → PASS",
            }
        
        # 板块涨+个股跌+缩量 → 可能是洗盘
        if stock_pct < -5.0 and sector_pct > 0 and is_drying and not is_limit_down:
            return {
                "path": "P6_ANTI_KNIFE",
                "active": True,
                "scores": {"跌%": round(stock_pct, 1), "量比": round(volume_vs_5d, 2), "板块%": round(sector_pct, 1)},
                "reason": f"板块涨+个股跌缩量: 跌{stock_pct:.1f}% 板块{sector_pct:+.1f}% 量比{volume_vs_5d:.2f} → PASS(庄家洗盘)",
            }
        
        return {
            "path": "P6_ANTI_KNIFE",
            "active": False,
            "scores": {"跌%": round(stock_pct, 1), "量比": round(volume_vs_5d, 2)},
            "reason": f"未触发: 跌{stock_pct:.1f}% 量比{volume_vs_5d:.2f} 近底={is_near_bottom}",
        }
    
    def _check_p7_meg_f6(self, d52_score: int, sector_change_pct: float,
                          sector_ma5_rising: bool, current_defcon: str) -> Dict:
        """P7: MEG-F6 板块Override"""
        if current_defcon not in ("ORANGE", "YELLOW"):
            return {
                "path": "P7_MEG_F6",
                "active": False,
                "scores": {"D52": d52_score, "DEFCON": current_defcon},
                "reason": f"DEFCON={current_defcon}无需Override",
            }
        
        ok = d52_score >= 8 and sector_change_pct > 0 and sector_ma5_rising
        if ok:
            return {
                "path": "P7_MEG_F6",
                "active": True,
                "scores": {"D52": d52_score, "板块%": round(sector_change_pct, 1), "MA5↑": sector_ma5_rising},
                "reason": f"板块Override: D52={d52_score}≥8 + 板块涨{sector_change_pct:.1f}% + MA5上升 → {current_defcon}→x0.5",
                "new_position_factor": 0.5,
            }
        
        missing = []
        if d52_score < 8: missing.append(f"D52={d52_score}<8")
        if sector_change_pct <= 0: missing.append(f"板块跌{sector_change_pct:.1f}%")
        if not sector_ma5_rising: missing.append("MA5未上升")
        return {
            "path": "P7_MEG_F6",
            "active": False,
            "scores": {"D52": d52_score, "板块%": round(sector_change_pct, 1), "MA5↑": sector_ma5_rising},
            "reason": f"未满足: {', '.join(missing)}",
        }
    
    def _check_p8_earnings_season(self, klines_daily: List[Dict],
                                   q1_net_profit_yoy: Optional[float] = None,
                                   sector_name: str = "",
                                   market_cap: Optional[float] = None,
                                   d49_score: int = 0,
                                   d29_score: int = 0) -> Dict:
        """P8: 中报事件预判旁路
        
        根因: 浪潮信息6/12漏选 — 中报季AI龙头+Q1高增+深度回撤+缩量底
        = 机构预判催化剂提前吸筹经典模式
        
        E1-E5条件 (≥3条满足 + D49≥6 → D29门槛降至3):
        E1: 中报预增窗口期(6/15-7/15)
        E2: Q1净利YoY增长≥50%
        E3: 属于AI/半导体/新能源等业绩高增赛道
        E4: 从60日高点回撤≥20%
        E5: 近3日均量vs前5日缩量≥30%
        """
        today = datetime.now()
        MONTH, DAY = today.month, today.day
        
        # — E1: 中报预增窗口期 —
        e1 = (MONTH == 6 and DAY >= 15) or (MONTH == 7 and DAY <= 15)
        # 也支持1月年报预增窗口和10月三季报窗口
        if not e1:
            e1 = (MONTH == 1 and DAY >= 15 and DAY <= 31)  # 年报预增窗口
        if not e1:
            e1 = (MONTH == 10 and DAY >= 8 and DAY <= 25)   # 三季报窗口
        
        # — E2: Q1净利YoY —
        e2 = q1_net_profit_yoy is not None and q1_net_profit_yoy >= 50
        
        # — E3: 业绩高增赛道 —
        HIGH_GROWTH_SECTORS = [
            "AI服务器", "光模块", "存储芯片", "PCB", "半导体设备",
            "算力", "服务器", "人工智能", "数据中心", "液冷",
            "CPO", "HBM", "先进封装", "机器人", "无人驾驶",
            "新能源车", "光伏逆变器", "储能", "充电桩",
            "创新药", "CXO", "医疗器械",
        ]
        e3 = False
        if sector_name:
            for kw in HIGH_GROWTH_SECTORS:
                if kw in sector_name:
                    e3 = True
                    break
        # 补充: 流通市值≥100亿(排除小盘炒作)
        if market_cap and market_cap < 100:
            e3 = False  # 小盘股排除
        
        # — E4: 从60日高点回撤≥20% —
        e4 = False
        if klines_daily and len(klines_daily) >= 2:
            high_60d = max(k['high'] for k in klines_daily[-60:])
            close_now = klines_daily[-1]['close']
            drawdown = (high_60d - close_now) / high_60d * 100
            e4 = drawdown >= 20
        else:
            drawdown = 0
        
        # — E5: 近3日均量vs前5日均量缩量≥30% —
        e5 = False
        if klines_daily and len(klines_daily) >= 8:
            vols = [k['vol'] for k in klines_daily[-8:]]
            vol_3d_avg = sum(vols[-3:]) / 3
            vol_5d_prev_avg = sum(vols[:5]) / 5
            vol_contraction = (1 - vol_3d_avg / max(vol_5d_prev_avg, 1)) * 100
            e5 = vol_contraction >= 30
        else:
            vol_contraction = 0
        
        # — 计算总分 —
        earn_score = sum([e1, e2, e3, e4, e5])
        
        # — 判断旁路激活 —
        active = earn_score >= 3 and d49_score >= 6
        
        details = {
            "E1_窗口期": {"满足": e1, "值": f"{MONTH}/{DAY}"},
            "E2_Q1_YoY": {"满足": e2, "值": f"{q1_net_profit_yoy:.0f}%" if q1_net_profit_yoy else "N/A"},
            "E3_高增赛道": {"满足": e3, "值": sector_name or "N/A"},
            "E4_深度回撤": {"满足": e4, "值": f"-{drawdown:.1f}%"},
            "E5_缩量底": {"满足": e5, "值": f"-{vol_contraction:.0f}%"},
            "D49": d49_score,
            "D29_原始": d29_score,
            "D29_降低后": 3 if active else d29_score,
        }
        
        reason_parts = []
        if active:
            reason_parts.append(f"中报事件预判: E分数={earn_score}/5≥3 + D49={d49_score}≥6")
            reason_parts.append(f"D29门槛: {d29_score}(原始)→{3}(降低后) 通过")
            activated_checks = []
            if e1: activated_checks.append(f"窗口期{MONTH}/{DAY}")
            if e2: activated_checks.append(f"Q1+{q1_net_profit_yoy:.0f}%")
            if e3: activated_checks.append(sector_name)
            if e4: activated_checks.append(f"回撤-{drawdown:.1f}%")
            if e5: activated_checks.append(f"缩量-{vol_contraction:.0f}%")
            reason_parts.append(f"满足条件: {' + '.join(activated_checks)}")
        else:
            missing = []
            if earn_score < 3: missing.append(f"E分数={earn_score}/5<3")
            if d49_score < 6: missing.append(f"D49={d49_score}<6")
            reason_parts.append(f"未激活: {'; '.join(missing) if missing else '无'} | 详细: E1={e1} E2={e2} E3={e3} E4={e4} E5={e5}")
        
        return {
            "path": "P8_EARNINGS_SEASON",
            "active": active,
            "scores": details,
            "reason": " | ".join(reason_parts),
            "d29_effective": 3 if active else d29_score,
        }
    
    # ═══════════════════════════════════════════════════════════
    # 维度计算 (调用已有模块)
    # ═══════════════════════════════════════════════════════════
    
    def _calc_reversal_dims(self, klines_daily: List[Dict],
                            klines_5min: List[Dict] = None,
                            stock_20d_pct: float = 0,
                            sector_20d_pct: float = 0) -> Dict:
        """计算 D47-D50 反转维度 (V13.5.23)"""
        if not self._reversal:
            return {"D47": {"score": 0}, "D49": {"score": 0}, "D50": {"score": 0}, "D48": {"score": 0}}
        
        d47 = self._reversal["d47"](klines_daily) if self._reversal.get("d47") else {"score": 0, "max": 8}
        d48 = self._reversal["d48"](stock_20d_pct, sector_20d_pct) if self._reversal.get("d48") else {"score": 0, "max": 5}
        d49 = self._reversal["d49"](klines_daily) if self._reversal.get("d49") else {"score": 0, "max": 13}
        d50 = self._reversal["d50"](klines_5min) if (self._reversal.get("d50") and klines_5min) else {"score": 0, "max": 10}
        
        return {"D47": d47, "D48": d48, "D49": d49, "D50": d50}
    
    def _calc_trend_dims(self, klines_daily: List[Dict],
                         catalyst_data: Dict = None,
                         price_hike_data: Dict = None,
                         sector_change_pct: float = 0) -> Dict:
        """计算 D49v2/D51/D52 (V13.5.25)"""
        if not self._trend:
            return {"D49v2": {"mode_a": {"score": 0}, "mode_b": {"score": 0}, "score": 0},
                    "D51": {"score": 0}, "D52": {"score": 0}}
        
        d49v2 = self._trend["d49v2"](klines_daily, catalyst_data) if self._trend.get("d49v2") else {
            "mode_a": {"score": 0}, "mode_b": {"score": 0}, "score": 0, "best_mode": "NONE", "trigger": False
        }
        d51 = self._trend["d51"](klines_daily, catalyst_data, sector_change_pct) if self._trend.get("d51") else {
            "score": 0, "max": 12, "is_trend_continuation": False
        }
        d52 = self._trend["d52"](price_hike_data) if (self._trend.get("d52") and price_hike_data) else {
            "score": 0, "max": 15, "is_price_hike_cycle": False
        }
        
        return {"D49v2": d49v2, "D51": d51, "D52": d52}
    
    def _calc_fix_dims(self, klines_daily: List[Dict]) -> Dict:
        """计算 D57 突破回踩 (V13.5.27)"""
        if not self._fixes or not self._fixes.get("d57"):
            return {"D57": {"triggered": False, "score": 0.0}}
        
        if len(klines_daily) < 10:
            return {"D57": {"triggered": False, "score": 0.0, "detail": "K线不足"}}
        
        closes = [float(k["close"]) for k in klines_daily]
        volumes = [float(k.get("vol", 0)) for k in klines_daily]
        highs = [float(k["high"]) for k in klines_daily]
        lows = [float(k["low"]) for k in klines_daily]
        dates = [str(i) for i in range(len(klines_daily))]
        
        try:
            d57 = self._fixes["d57"](closes, volumes, highs, lows, dates)
            return {
                "D57": {
                    "triggered": d57.triggered,
                    "score": d57.score,
                    "detail": d57.detail,
                    "pattern_desc": d57.pattern_desc if hasattr(d57, 'pattern_desc') else "",
                }
            }
        except Exception as e:
            logger.warning(f"D57计算异常: {e}")
            return {"D57": {"triggered": False, "score": 0.0, "detail": str(e)}}
    
    # ═══════════════════════════════════════════════════════════
    # P9: 投降衰竭单路径覆盖 (CAPITULATION EXHAUSTION)
    # 根因: 贝斯特300580 6/26 — M46极端超跌但≥2路径规则一刀切杀
    # ═══════════════════════════════════════════════════════════
    
    @staticmethod
    def _check_p9_capitulation(
        consecutive_decline_days: int = 0,
        m46_score: float = 0.0,
        volume_vs_5d: float = 1.0,
        position_from_60d_low: float = 100.0,
        is_m46_active: bool = False,
    ) -> Dict:
        """
        P9: 投降衰竭单路径覆盖
        
        D59 = A(连跌) + B(M46) + C(缩量) + D(近底)
        当D59 ≥ 16 (≥2/4) 且M46已激活 → 单路径Override → 允许提升为超级信号
        
        触发条件: 多日连续下跌+极端超跌+缩量+近历史低点 = 卖盘力竭 → 反转在即
        
        Args:
            consecutive_decline_days: 连续下跌天数
            m46_score: M46超跌得分 (0-1)
            volume_vs_5d: 量比 (当日量/5日均量)
            position_from_60d_low: 距60日低点距离(%)
            is_m46_active: M46 Path1是否已激活 (M46≥0.70)
        
        Returns:
            dict with active, score, detail, override_type
        """
        result = {
            "path": "P9_CAPITULATION",
            "active": False,
            "score": 0,
            "detail": {},
            "override_type": "单路径覆盖",
        }
        
        # D59_A: 连续下跌天数
        a_score = 0
        if consecutive_decline_days >= 7:
            a_score = 10
        elif consecutive_decline_days >= 5:
            a_score = 8
        elif consecutive_decline_days >= 4:
            a_score = 5
        
        # D59_B: M46极端超跌
        b_score = 0
        if m46_score >= 0.90:
            b_score = 10
        elif m46_score >= 0.85:
            b_score = 8
        elif m46_score >= 0.80:
            b_score = 5
        
        # D59_C: 成交量萎缩 (量比越小越好, 表明卖盘力竭)
        c_score = 0
        if volume_vs_5d <= 0.60:
            c_score = 10
        elif volume_vs_5d <= 0.80:
            c_score = 8
        elif volume_vs_5d <= 1.00:
            c_score = 5
        
        # D59_D: 位置距离60日低点
        d_score = 0
        if position_from_60d_low <= 3.0:
            d_score = 10
        elif position_from_60d_low <= 5.0:
            d_score = 8
        elif position_from_60d_low <= 10.0:
            d_score = 5
        
        d59_total = a_score + b_score + c_score + d_score
        
        result["score"] = d59_total
        result["detail"] = {
            "D59_A_consecutive_days": {"value": consecutive_decline_days, "score": a_score},
            "D59_B_m46_extreme": {"value": round(m46_score, 3), "score": b_score},
            "D59_C_volume_shrink": {"value": round(volume_vs_5d, 3), "score": c_score},
            "D59_D_near_bottom": {"value": round(position_from_60d_low, 1), "score": d_score},
            "D59_total": d59_total,
            "m46_active": is_m46_active,
        }
        
        # P9激活: D59≥16(至少2/4满足) 且 M46已激活(≥0.70)
        if d59_total >= 16 and is_m46_active:
            result["active"] = True
            result["reason"] = (
                f"P9投降衰竭: D59={d59_total}/40 "
                f"(连跌{consecutive_decline_days}天/A={a_score} "
                f"M46={m46_score:.2f}/B={b_score} "
                f"量比={volume_vs_5d:.2f}/C={c_score} "
                f"距底={position_from_60d_low:.1f}%/D={d_score}) "
                f"→ 单路径覆盖! M46极端超跌+投降衰竭 → 允许单路径超级信号"
            )
        else:
            missing = []
            if d59_total < 16:
                missing.append(f"D59={d59_total}<16")
            if not is_m46_active:
                missing.append("M46未激活")
            result["reason"] = f"P9未激活: {', '.join(missing) if missing else '条件未满足'}"
        
        return result
    
    # ═══════════════════════════════════════════════════════════
    # 主入口: 一次性检查所有9条旁路
    # ═══════════════════════════════════════════════════════════
    
    def check_all(self,
                  code: str,
                  klines_daily: List[Dict],
                  d29_score: int = 0,
                  d28_score: int = 0,
                  five_confirm_passed: int = 0,
                  stock_decline_pct: float = 0,
                  sector_change_pct: float = 0,
                  volume_vs_5d: float = 1.0,
                  is_limit_down: bool = False,
                  position_from_20d_low: float = 100.0,
                  current_defcon: str = "YELLOW",
                  catalyst_data: Dict = None,
                  price_hike_data: Dict = None,
                  sector_ma5_rising: bool = False,
                  stock_20d_pct: float = 0,
                  sector_20d_pct: float = 0,
                  klines_5min: List[Dict] = None,
                  # === P8 中报事件预判参数 (V13.5.29_P8) ===
                  q1_net_profit_yoy: Optional[float] = None,
                  sector_name: str = "",
                  market_cap: Optional[float] = None,
                  # === P9 投降衰竭参数 (V13.5.30_P9) ===
                  consecutive_decline_days: int = 0,
                  m46_score: float = 0.0,
                  position_from_60d_low: float = 100.0,
                  is_m46_active: bool = False) -> Dict:
        """
        ★★★ BypassHub 主入口 — 强制检查所有9条旁路
        
        Args:
            code: 股票代码
            klines_daily: 日K线列表 (最新=last), 每个含 open/high/low/close/vol
            d29_score: D29双洗盘得分 (0-12)
            d28_score: D28催化得分 (0-10)
            five_confirm_passed: 五确认通过数 (0-5)
            stock_decline_pct: 当日涨跌幅(%)
            sector_change_pct: 板块涨跌幅(%)
            volume_vs_5d: 当日量/5日均量
            is_limit_down: 是否跌停
            position_from_20d_low: 距20日低点距离(%)
            current_defcon: MEG DEFCON等级
            catalyst_data: 催化数据
            price_hike_data: 涨价周期数据
            sector_ma5_rising: 板块MA5是否上升
            stock_20d_pct: 个股20日涨跌幅(%)
            sector_20d_pct: 板块20日涨跌幅(%)
            klines_5min: 5分钟K线 (可选)
        
        Returns:
            {
                "code": str,
                "bypass": bool,          # 是否有旁路激活
                "active_paths": [str],   # 激活的旁路列表
                "path_details": [Dict],  # 每条旁路的详细结果
                "dims": {                # 计算出的所有维度得分
                    "D47": int, "D48": int, "D49": int, "D49v2_A": int,
                    "D49v2_B": int, "D50": int, "D51": int, "D52": int, "D57": float,
                },
                "recommendation": str,   # "STRONG_BYPASS" / "BYPASS" / "NO_BYPASS"
                "reason": str,           # 综合判定理由
                "meg_f6_active": bool,   # MEG-F6是否激活
                "new_position_factor": float,  # 新仓位系数(如有Override)
                "timestamp": str,
            }
        """
        result = {
            "code": code,
            "bypass": False,
            "active_paths": [],
            "path_details": [],
            "dims": {},
            "recommendation": "NO_BYPASS",
            "reason": "",
            "meg_f6_active": False,
            "new_position_factor": None,
            "timestamp": datetime.now().isoformat(),
        }
        
        # ===== Step 1: 计算所有维度 =====
        reversal = self._calc_reversal_dims(klines_daily, klines_5min, stock_20d_pct, sector_20d_pct)
        trend = self._calc_trend_dims(klines_daily, catalyst_data, price_hike_data, sector_change_pct)
        fixes = self._calc_fix_dims(klines_daily)
        
        # 提取关键得分
        d49a_score = trend.get("D49v2", {}).get("mode_a", {}).get("score", 0)
        d49b_score = trend.get("D49v2", {}).get("mode_b", {}).get("score", 0)
        d49_score = reversal.get("D49", {}).get("score", 0)  # 原版D49
        d50_score = reversal.get("D50", {}).get("score", 0)
        d51_score = trend.get("D51", {}).get("score", 0)
        d52_score = trend.get("D52", {}).get("score", 0)
        d57_score = fixes.get("D57", {}).get("score", 0.0)
        d47_score = reversal.get("D47", {}).get("score", 0)
        d48_score = reversal.get("D48", {}).get("score", 0)
        
        result["dims"] = {
            "D47": d47_score, "D48": d48_score,
            "D49": d49_score, "D49v2_A": d49a_score, "D49v2_B": d49b_score,
            "D50": d50_score, "D51": d51_score, "D52": d52_score, "D57": d57_score,
        }
        
        # ===== Step 2: 逐一检查9条旁路 =====
        paths = []
        
        # P1: D49v2模式A (长下影线) + D29
        p1 = self._check_p1_d49v2_a(d49a_score, d29_score)
        paths.append(p1)
        
        # P2: D49v2模式B (大实体阳线) + D28
        p2 = self._check_p2_d49v2_b(d49b_score, d28_score)
        paths.append(p2)
        
        # P3: D51 (趋势延续) + D28
        p3 = self._check_p3_d51(d51_score, d28_score)
        paths.append(p3)
        
        # P4: D50 (尾盘放量) + D29
        p4 = self._check_p4_d50(d50_score, d29_score)
        paths.append(p4)
        
        # P5: D57 (突破回踩) + D29
        p5 = self._check_p5_d57(d57_score, d29_score)
        paths.append(p5)
        
        # P6: 反接飞刀V2 缩量旁路
        p6 = self._check_p6_anti_knife(stock_decline_pct, volume_vs_5d,
                                        is_limit_down, position_from_20d_low, sector_change_pct)
        paths.append(p6)
        
        # P7: MEG-F6 板块Override
        p7 = self._check_p7_meg_f6(d52_score, sector_change_pct, sector_ma5_rising, current_defcon)
        paths.append(p7)
        
        # P8: 中报事件预判 (V13.5.29_P8)
        # 使用最严格的反转维度得分: max(D49v2_A, D49原版)
        d49_best = max(d49a_score, d49_score)
        p8 = self._check_p8_earnings_season(
            klines_daily, q1_net_profit_yoy, sector_name, market_cap,
            d49_score=d49_best, d29_score=d29_score,
        )
        paths.append(p8)
        
        # P9: 投降衰竭单路径覆盖 (V13.5.30_P9)
        p9 = self._check_p9_capitulation(
            consecutive_decline_days=consecutive_decline_days,
            m46_score=m46_score,
            volume_vs_5d=volume_vs_5d,
            position_from_60d_low=position_from_60d_low,
            is_m46_active=is_m46_active,
        )
        paths.append(p9)
        
        # P8活跃时, D29有效值降低
        d29_effective = p8.get("d29_effective", d29_score) if p8["active"] else d29_score
        
        # 如果P8激活且D29降低, 重新评估P1/P4/P5
        if p8["active"] and d29_effective < d29_score:
            p1 = self._check_p1_d49v2_a(d49a_score, d29_effective)
            p4 = self._check_p4_d50(d50_score, d29_effective)
            p5 = self._check_p5_d57(d57_score, d29_effective)
            # 更新paths中的对应项
            for i, p in enumerate(paths):
                if p["path"] == "P1_D49v2_A":
                    paths[i] = p1
                elif p["path"] == "P4_D50":
                    paths[i] = p4
                elif p["path"] == "P5_D57":
                    paths[i] = p5
        
        result["path_details"] = paths
        
        # ===== Step 3: 汇总判定 =====
        active_paths = [p for p in paths if p["active"]]
        f3_bypass_paths = [p for p in active_paths if p["path"] in ("P1_D49v2_A", "P2_D49v2_B", "P3_D51", "P4_D50", "P5_D57", "P8_EARNINGS_SEASON")]
        # P9是单路径覆盖, 不是F3旁路 — 它直接允许M46单路径触发超级信号
        p9_active = p9["active"]
        meg_active = p7["active"]
        
        result["active_paths"] = [p["path"] for p in active_paths]
        result["meg_f6_active"] = meg_active
        result["new_position_factor"] = p7.get("new_position_factor") if meg_active else None
        
        # F3五确认旁路: 如果有≥1条有效旁路, 可绕过F3≥3
        has_f3_bypass = len(f3_bypass_paths) >= 1
        has_anti_knife = p6["active"]
        
        if has_f3_bypass and has_anti_knife:
            result["bypass"] = True
            result["recommendation"] = "STRONG_BYPASS"
            result["reason"] = (
                f"★双旁路激活: {', '.join([p['path'] for p in f3_bypass_paths])} 绕过F3 + "
                f"P6_ANTI_KNIFE 缩量旁路"
            )
        elif has_f3_bypass:
            result["bypass"] = True
            result["recommendation"] = "BYPASS"
            result["reason"] = (
                f"旁路激活: {', '.join([p['path'] for p in f3_bypass_paths])} → 绕过F3五确认≥3"
            )
        elif has_anti_knife:
            result["bypass"] = True
            result["recommendation"] = "BYPASS"
            result["reason"] = "P6反接飞刀V2缩量旁路: 跌>5%但缩量非跌停 → 不降权"
        elif meg_active:
            result["bypass"] = False  # MEG-F6不是F3旁路, 是仓位Override
            result["reason"] = f"MEG-F6板块Override: {current_defcon}→x0.5 (非F3旁路)"
        elif p9_active:
            # P9 投降衰竭单路径覆盖: M46极端超跌 + 投降衰竭 → 允许单路径触发
            result["bypass"] = True
            result["recommendation"] = "P9_SINGLE_PATH"
            result["reason"] = f"★P9投降衰竭单路径覆盖: {p9['reason']}"
            result["p9_detail"] = p9["detail"]
        else:
            # 检查五确认本身
            if five_confirm_passed >= 3:
                result["reason"] = f"五确认={five_confirm_passed}/5 ≥ 3, 无需旁路"
            else:
                result["reason"] = f"无旁路激活: 五确认={five_confirm_passed}/5 < 3, 且无反转/延续/回踩/缩量信号"
        
        # 日志
        if result["bypass"]:
            logger.info(f"[{code}] {'★' if result['recommendation']=='STRONG_BYPASS' else '✓'} {result['reason']}")
        else:
            logger.debug(f"[{code}] ✗ {result['reason']}")
        
        return result
    
    # ═══════════════════════════════════════════════════════════
    # 便捷方法: 应用旁路结果到选股推荐
    # ═══════════════════════════════════════════════════════════
    
    @staticmethod
    def apply_to_recommendation(original_rec: str, bypass_verdict: Dict) -> str:
        """
        根据旁路结果提升推荐等级
        
        规则:
          STRONG_BYPASS → 最低提升到 BUY
          BYPASS        → 提升一档
          P9_SINGLE_PATH → 提升一档 (投降衰竭单路径覆盖)
        
        STRONG_BUY > BUY > WATCH > HOLD > REJECT
        """
        levels = {"STRONG_BUY": 4, "BUY": 3, "WATCH": 2, "HOLD": 1, "REJECT": 0}
        reverse = {4: "STRONG_BUY", 3: "BUY", 2: "WATCH", 1: "HOLD", 0: "REJECT"}
        
        current_level = levels.get(original_rec, 1)
        
        if bypass_verdict["recommendation"] == "STRONG_BYPASS":
            new_level = max(current_level, 3)  # 最低BUY
        elif bypass_verdict["recommendation"] in ("BYPASS", "P9_SINGLE_PATH"):
            new_level = min(current_level + 1, 4)  # 提升一档
        else:
            return original_rec
        
        return reverse[new_level]
    
    def get_status_summary(self) -> Dict:
        """返回BypassHub当前状态摘要"""
        return {
            "version": "V13.5.30_P9",
            "available_paths": self.available_paths,
            "total_paths": 9,
            "modules": {
                "V13_5_23_Reversal": self._reversal is not None,
                "V13_5_25_Trend": self._trend is not None,
                "V13_5_27_Fixes": self._fixes is not None,
            },
            "timestamp": datetime.now().isoformat(),
        }


# ═══════════════════════════════════════════════════════════════
# 独立函数: 快速检查 (不依赖BypassHub实例)
# ═══════════════════════════════════════════════════════════════

def quick_bypass_check(code: str, klines_daily: List[Dict],
                       d29_score: int = 0, d28_score: int = 0,
                       five_confirm_passed: int = 0,
                       stock_decline_pct: float = 0,
                       sector_change_pct: float = 0,
                       **kwargs) -> Dict:
    """
    快速旁路检查 — 一行调用, 返回是否应绕过F3
    
    使用场景: 在T4选股循环中, 对每个F3被拒的候选调此函数
    
    Example:
        verdict = quick_bypass_check("603137", klines, d29_score=9, d28_score=3, five_confirm_passed=1)
        if verdict["bypass"]:
            print(f"旁路激活: {verdict['reason']}")
            stock["recommendation"] = BypassHub.apply_to_recommendation(stock["recommendation"], verdict)
    
    Returns:
        与 BypassHub.check_all() 相同格式的字典
    """
    hub = BypassHub()
    return hub.check_all(
        code=code,
        klines_daily=klines_daily,
        d29_score=d29_score,
        d28_score=d28_score,
        five_confirm_passed=five_confirm_passed,
        stock_decline_pct=stock_decline_pct,
        sector_change_pct=sector_change_pct,
        **kwargs
    )


# ═══════════════════════════════════════════════════════════════
# 验证用例: 恒尚节能603137 6/11 (应被BypassHub捕获)
# ═══════════════════════════════════════════════════════════════

def verify_hsjn_603137():
    """验证: BypassHub能否捕获恒尚节能603137 6/11"""
    print("\n" + "=" * 70)
    print("  BypassHub V13.5.29 验证: 恒尚节能(603137) 6月11日")
    print("=" * 70)
    
    # 构造6/11及前20日K线
    klines = []
    base = 12.0
    for i in range(20):
        pct = i / 20
        close = base * (1 - pct * 0.04)
        close = round(close, 2)
        klines.append({
            "open": round(close * 1.005, 2),
            "high": round(close * 1.01, 2),
            "low": round(close * 0.99, 2),
            "close": close,
            "vol": int(500 - i * 10),
        })
    # 6/11当日: 开11.44 / 低11.20(创新低) / 收11.54 (长下影线)
    klines.append({
        "open": 11.44, "high": 11.60, "low": 11.20, "close": 11.54, "vol": 420,
    })
    
    hub = BypassHub()
    verdict = hub.check_all(
        code="603137",
        klines_daily=klines,
        d29_score=9,              # D29双洗盘=9分
        d28_score=3,              # D28催化弱(无强催化)
        five_confirm_passed=1,    # 五确认仅1/5 (D29)
        stock_decline_pct=1.5,    # 当日微涨
        sector_change_pct=-0.5,   # 板块微跌
        volume_vs_5d=0.57,        # 缩量
        is_limit_down=False,
        position_from_20d_low=11.0,  # 近底部
        current_defcon="YELLOW",
        stock_20d_pct=-8.0,
        sector_20d_pct=-3.0,
    )
    
    print(f"  代码: {verdict['code']}")
    print(f"  五确认: 1/5 (仅D29)")
    print(f"  维度得分: {verdict['dims']}")
    print(f"  旁路激活: {verdict['bypass']}")
    print(f"  激活路径: {verdict['active_paths']}")
    print(f"  判定: {verdict['recommendation']}")
    print(f"  理由: {verdict['reason']}")
    
    for p in verdict["path_details"]:
        status = "✅激活" if p["active"] else "❌未激活"
        print(f"    {p['path']}: {status} | {p['reason']}")
    
    if verdict["bypass"]:
        new_rec = BypassHub.apply_to_recommendation("WATCH", verdict)
        print(f"\n  ★ 旁路生效: WATCH → {new_rec}")
        print(f"  ✅ 验证通过: BypassHub成功捕获恒尚节能6/11!")
    else:
        print(f"\n  ❌ 验证失败: 旁路未激活")
    
    return verdict


# ═══════════════════════════════════════════════════════════════
# 验证用例: 大恒科技7/2 (D57突破回踩)
# ═══════════════════════════════════════════════════════════════

def verify_dht_600288():
    """验证: BypassHub D57能否捕获大恒科技7/2"""
    print("\n" + "=" * 70)
    print("  BypassHub V13.5.29 验证: 大恒科技(600288) 7月2日")
    print("=" * 70)
    
    # 使用V13.5.27的验证数据
    closes =  [13.18, 13.14, 13.01, 13.12, 13.13, 12.79, 12.59, 12.36, 13.30, 13.05, 12.60]
    volumes = [70230, 62683, 103851, 90930, 96389, 75404, 81136, 78126, 161741, 106295, 114076]
    highs =   [13.33, 13.35, 13.50, 13.35, 13.41, 13.13, 12.95, 12.64, 13.36, 13.34, 13.23]
    lows =    [12.97, 13.01, 12.55, 12.89, 12.80, 12.71, 12.40, 12.16, 12.22, 13.01, 12.53]
    
    klines = []
    for i in range(len(closes)):
        klines.append({
            "open": closes[i] - 0.05,
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "vol": volumes[i],
        })
    
    # 计算量比
    avg_vol_5d = sum(volumes[-6:-1]) / 5  # 约96K
    today_vol = volumes[-1]  # 114076
    vol_ratio = today_vol / avg_vol_5d
    
    # 计算距20日低点
    low_20d = min(lows)  # 12.16
    dist_from_low = (closes[-1] - low_20d) / low_20d * 100
    
    hub = BypassHub()
    verdict = hub.check_all(
        code="600288",
        klines_daily=klines,
        d29_score=7,
        d28_score=6,
        five_confirm_passed=2,
        stock_decline_pct=-3.5,
        sector_change_pct=0.3,
        volume_vs_5d=vol_ratio,
        is_limit_down=False,
        position_from_20d_low=dist_from_low,
        current_defcon="YELLOW",
    )
    
    print(f"  D57得分: {verdict['dims']['D57']:.1f}/12")
    print(f"  旁路激活: {verdict['bypass']}")
    print(f"  激活路径: {verdict['active_paths']}")
    print(f"  判定: {verdict['recommendation']}")
    
    for p in verdict["path_details"]:
        status = "✅激活" if p["active"] else "❌未激活"
        print(f"    {p['path']}: {status}")
    
    if verdict["bypass"]:
        print(f"  ✅ D57突破回踩旁路验证通过!")
    else:
        print(f"  注意: 可能需D57≥7+D29≥6, 当前D57={verdict['dims']['D57']:.1f}")
    
    return verdict


if __name__ == "__main__":
    print("=" * 70)
    print("  V13.5.29 BypassHub — 7旁路强制调度中心")
    print("=" * 70)
    print()
    
    hub = BypassHub()
    status = hub.get_status_summary()
    print(f"  模块状态: {status['modules']}")
    print(f"  可用旁路: {status['available_paths']}")
    print()
    
    print("  7条旁路路径:")
    for pid, pinfo in BypassHub.BYPASS_PATHS.items():
        available = "✓" if pid in status['available_paths'] else "✗"
        print(f"    {pid}: {available} {pinfo['name']} ({pinfo['condition']})")
    
    # 验证
    verify_hsjn_603137()
    verify_dht_600288()
    
    print("\n" + "=" * 70)
    print("  BypassHub V13.5.29 验证完成")
    print("=" * 70)
