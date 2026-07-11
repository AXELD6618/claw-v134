#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.22 MEG 市场环境门控模块 (Market Environment Gate)
=========================================================
DEFCON四级市场环境门控 + 反接飞刀规则
T4选股前强制执行, 任一条件触发即降级, 不可被高M71分数覆盖

集成路径:
  T4选股流程 → MEG.evaluate() → DEFCON等级 → 决定是否允许买入
  MEG.GREEN  → 正常选股
  MEG.YELLOW → 仅Top信号, 仓位×0.5
  MEG.ORANGE → 仅五确认≥3, 仓位×0.3
  MEG.RED    → 禁止新买入, 仅允许卖出

数据源:
  - 上证指数(1A0001) + 深证成指(2A01) K线 → F1大盘趋势
  - 创业板指(399006) 实时行情 → F2创业板动量
  - 全市场涨跌统计 → F3市场宽度
  - 目标板块涨跌 → F4板块热度
  - 北向资金 → F5(如有数据)
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("MEG")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] MEG %(levelname)s: %(message)s"))
    logger.addHandler(h)

# DEFCON等级
DEFCON_GREEN = "GREEN"    # MES > 70, 正常选股
DEFCON_YELLOW = "YELLOW"  # MES 50-70, 减仓操作
DEFCON_ORANGE = "ORANGE"  # MES 30-50, 仅强确认信号
DEFCON_RED = "RED"        # MES < 30, 禁止买入

# DEFCON对应操作
DEFCON_ACTIONS = {
    DEFCON_GREEN: "正常选股, 全量信号有效",
    DEFCON_YELLOW: "减仓操作, 仅Top信号, 仓位x0.5",
    DEFCON_ORANGE: "仅五确认>=3且基本面合格, 仓位x0.3",
    DEFCON_RED: "禁止新买入, 仅允许卖出"
}

# 仓位系数
DEFCON_POSITION_FACTOR = {
    DEFCON_GREEN: 1.0,
    DEFCON_YELLOW: 0.5,
    DEFCON_ORANGE: 0.3,
    DEFCON_RED: 0.0
}


class MarketEnvGate:
    """V13.5.22 市场环境门控模块"""

    def __init__(self):
        self.mes_score = 100  # 满分开始, 逐项扣减
        self.gates: List[Dict] = []
        self.defcon = DEFCON_GREEN
        self.market_data: Dict = {}

    # ==================== 五道门控 ====================

    def gate_f1_index_trend(self, sh_ma5: float, sh_ma10: float,
                             sz_ma5: float, sz_ma10: float) -> bool:
        """F1: 大盘趋势 -- 上证MA5 vs MA10 + 深证MA5 vs MA10 (30分)"""
        fail_count = 0
        if sh_ma5 < sh_ma10:
            fail_count += 1
        if sz_ma5 < sz_ma10:
            fail_count += 1

        if fail_count == 2:
            self.mes_score -= 30
            self.gates.append({
                "gate": "F1", "status": "FAIL", "penalty": -30,
                "detail": f"双指数MA5<MA10 (上证{sh_ma5:.1f}<{sh_ma10:.1f}, 深证{sz_ma5:.1f}<{sz_ma10:.1f})"
            })
        elif fail_count == 1:
            self.mes_score -= 15
            self.gates.append({
                "gate": "F1", "status": "WARN", "penalty": -15,
                "detail": f"单指数MA5<MA10"
            })
        else:
            self.gates.append({
                "gate": "F1", "status": "PASS", "penalty": 0,
                "detail": f"双指数MA5>MA10 (上证{sh_ma5:.1f}>{sh_ma10:.1f}, 深证{sz_ma5:.1f}>{sz_ma10:.1f})"
            })

        self.market_data["sh_ma5"] = sh_ma5
        self.market_data["sh_ma10"] = sh_ma10
        self.market_data["sz_ma5"] = sz_ma5
        self.market_data["sz_ma10"] = sz_ma10
        return fail_count == 0

    def gate_f2_gem_change(self, gem_change_pct: float) -> bool:
        """F2: 创业板指当日涨跌幅 (25分, 跌>3%直接MES=0)"""
        if gem_change_pct < -3:
            self.mes_score = 0  # 直接归零
            self.gates.append({
                "gate": "F2", "status": "FAIL", "penalty": "MES=0",
                "detail": f"创业板跌{gem_change_pct:.2f}%, 超过-3%阈值, MES直接归零"
            })
        elif gem_change_pct < -2:
            self.mes_score -= 25
            self.gates.append({
                "gate": "F2", "status": "FAIL", "penalty": -25,
                "detail": f"创业板跌{gem_change_pct:.2f}%, 超过-2%阈值"
            })
        elif gem_change_pct < -1:
            self.mes_score -= 10
            self.gates.append({
                "gate": "F2", "status": "WARN", "penalty": -10,
                "detail": f"创业板跌{gem_change_pct:.2f}%"
            })
        else:
            self.gates.append({
                "gate": "F2", "status": "PASS", "penalty": 0,
                "detail": f"创业板{gem_change_pct:.2f}%"
            })

        self.market_data["gem_change"] = gem_change_pct
        return gem_change_pct >= -2

    def gate_f3_market_breadth(self, adv_count: int, dec_count: int) -> bool:
        """F3: 市场宽度 -- 涨跌比 (20分)"""
        total = adv_count + dec_count
        ratio = adv_count / total if total > 0 else 1.0

        if ratio < 0.3:
            self.mes_score -= 20
            self.gates.append({
                "gate": "F3", "status": "FAIL", "penalty": -20,
                "detail": f"涨跌比{ratio:.2f}极低 ({adv_count}涨/{dec_count}跌)"
            })
        elif ratio < 0.5:
            self.mes_score -= 15
            self.gates.append({
                "gate": "F3", "status": "WARN", "penalty": -15,
                "detail": f"涨跌比{ratio:.2f}偏低 ({adv_count}涨/{dec_count}跌)"
            })
        else:
            self.gates.append({
                "gate": "F3", "status": "PASS", "penalty": 0,
                "detail": f"涨跌比{ratio:.2f} ({adv_count}涨/{dec_count}跌)"
            })

        self.market_data["breadth_ratio"] = ratio
        return ratio >= 0.5

    def gate_f4_sector_heat(self, sector_change_pct: float, sector_name: str = "") -> bool:
        """F4: 目标板块热度 (15分)"""
        if sector_change_pct < -3:
            self.mes_score -= 15
            self.gates.append({
                "gate": "F4", "status": "FAIL", "penalty": -15,
                "detail": f"板块[{sector_name}]跌{sector_change_pct:.2f}%, 超过-3%, 该板块全部REJECT"
            })
        elif sector_change_pct < -1:
            self.mes_score -= 5
            self.gates.append({
                "gate": "F4", "status": "WARN", "penalty": -5,
                "detail": f"板块[{sector_name}]跌{sector_change_pct:.2f}%"
            })
        else:
            self.gates.append({
                "gate": "F4", "status": "PASS", "penalty": 0,
                "detail": f"板块[{sector_name}]{sector_change_pct:.2f}%"
            })

        self.market_data["sector_change"] = sector_change_pct
        return sector_change_pct >= -3

    def gate_f5_northbound(self, nb_net_flow: float) -> bool:
        """F5: 北向资金 (10分, 单位:亿元)"""
        if nb_net_flow < -50:
            self.mes_score -= 10
            self.gates.append({
                "gate": "F5", "status": "FAIL", "penalty": -10,
                "detail": f"北向净流出{abs(nb_net_flow):.0f}亿"
            })
        elif nb_net_flow < -20:
            self.mes_score -= 5
            self.gates.append({
                "gate": "F5", "status": "WARN", "penalty": -5,
                "detail": f"北向净流出{abs(nb_net_flow):.0f}亿"
            })
        else:
            self.gates.append({
                "gate": "F5", "status": "PASS", "penalty": 0,
                "detail": f"北向{nb_net_flow:+.0f}亿"
            })

        self.market_data["northbound"] = nb_net_flow
        return nb_net_flow >= -50

    # ==================== DEFCON判定 ====================

    def determine_defcon(self) -> str:
        """根据MES分数确定DEFCON等级"""
        self.mes_score = max(0, min(100, self.mes_score))
        if self.mes_score > 70:
            self.defcon = DEFCON_GREEN
        elif self.mes_score > 50:
            self.defcon = DEFCON_YELLOW
        elif self.mes_score > 30:
            self.defcon = DEFCON_ORANGE
        else:
            self.defcon = DEFCON_RED
        return self.defcon

    def get_position_factor(self) -> float:
        """获取当前DEFCON对应的仓位系数"""
        self.determine_defcon()
        return DEFCON_POSITION_FACTOR.get(self.defcon, 0.0)

    def can_buy(self) -> bool:
        """是否允许买入"""
        self.determine_defcon()
        return self.defcon in (DEFCON_GREEN, DEFCON_YELLOW, DEFCON_ORANGE)

    def requires_strong_confirm(self) -> bool:
        """是否需要强确认(五确认>=3)"""
        self.determine_defcon()
        return self.defcon in (DEFCON_ORANGE,)

    # ==================== 反接飞刀规则 ====================

    @staticmethod
    def anti_falling_knife(stock_change_pct: float, sector_change_pct: float,
                            d29_score: int, confirm_count: int) -> Dict:
        """
        反接飞刀规则 (Anti-Falling-Knife)
        判断个股下跌是"洗盘"还是"崩盘", 崩盘直接REJECT
        """
        if stock_change_pct < -5 and sector_change_pct < -3:
            return {
                "rule": "AFK", "verdict": "REJECT",
                "reason": f"崩盘非洗盘: 个股{stock_change_pct:.1f}%+板块{sector_change_pct:.1f}%"
            }
        elif stock_change_pct < -5 and sector_change_pct >= 0:
            if d29_score >= 8 and confirm_count >= 4:
                return {
                    "rule": "AFK", "verdict": "PASS",
                    "reason": f"强洗盘确认: D29={d29_score}+五确认={confirm_count}/5"
                }
            else:
                return {
                    "rule": "AFK", "verdict": "REJECT",
                    "reason": f"弱洗盘: D29={d29_score}<8或五确认={confirm_count}<4"
                }
        elif stock_change_pct < -3 and sector_change_pct < -2:
            if d29_score >= 6 and confirm_count >= 3:
                return {
                    "rule": "AFK", "verdict": "PASS",
                    "reason": f"洗盘确认: D29={d29_score}+五确认={confirm_count}/5"
                }
            else:
                return {
                    "rule": "AFK", "verdict": "REJECT",
                    "reason": f"弱信号: D29={d29_score}<6或五确认={confirm_count}<3"
                }
        else:
            return {
                "rule": "AFK", "verdict": "PASS",
                "reason": "正常波动"
            }

    # ==================== 完整评估 ====================

    def evaluate(self, sh_ma5: float, sh_ma10: float, sz_ma5: float, sz_ma10: float,
                 gem_change_pct: float, adv_count: int, dec_count: int,
                 sector_change_pct: float = 0, sector_name: str = "",
                 nb_net_flow: float = 0) -> Dict:
        """执行全部五道门控, 返回完整报告"""
        self.mes_score = 100
        self.gates = []

        self.gate_f1_index_trend(sh_ma5, sh_ma10, sz_ma5, sz_ma10)
        self.gate_f2_gem_change(gem_change_pct)
        self.gate_f3_market_breadth(adv_count, dec_count)
        self.gate_f4_sector_heat(sector_change_pct, sector_name)
        self.gate_f5_northbound(nb_net_flow)

        self.determine_defcon()

        return {
            "mes_score": self.mes_score,
            "defcon": self.defcon,
            "action": DEFCON_ACTIONS[self.defcon],
            "position_factor": DEFCON_POSITION_FACTOR[self.defcon],
            "can_buy": self.can_buy(),
            "requires_strong_confirm": self.requires_strong_confirm(),
            "gates": self.gates,
            "market_data": self.market_data,
            "timestamp": datetime.now().isoformat()
        }

    def evaluate_from_tdx(self, tdx_quotes_func, tdx_kline_func) -> Dict:
        """
        从TDX实时数据自动获取市场环境数据并评估
        tdx_quotes_func: 可调用的tdx_quotes函数
        tdx_kline_func: 可调用的tdx_kline函数
        """
        import statistics

        # 1. 获取上证指数K线 (1A0001) 计算MA5/MA10
        try:
            sh_klines = tdx_kline_func(code="1A0001", setcode="1", period="4",
                                        count=15, tqFlag="1")
            sh_closes = [k["close"] for k in sh_klines] if sh_klines else []
            sh_ma5 = statistics.mean(sh_closes[-5:]) if len(sh_closes) >= 5 else 0
            sh_ma10 = statistics.mean(sh_closes[-10:]) if len(sh_closes) >= 10 else 0
        except Exception as e:
            logger.warning(f"获取上证K线失败: {e}, 使用默认值")
            sh_ma5, sh_ma10 = 3000, 2980

        # 2. 获取深证成指K线 (2A01) 计算MA5/MA10
        try:
            sz_klines = tdx_kline_func(code="2A01", setcode="1", period="4",
                                        count=15, tqFlag="1")
            sz_closes = [k["close"] for k in sz_klines] if sz_klines else []
            sz_ma5 = statistics.mean(sz_closes[-5:]) if len(sz_closes) >= 5 else 0
            sz_ma10 = statistics.mean(sz_closes[-10:]) if len(sz_closes) >= 10 else 0
        except Exception as e:
            logger.warning(f"获取深证K线失败: {e}, 使用默认值")
            sz_ma5, sz_ma10 = 9200, 9350

        # 3. 获取创业板指(399006)实时行情
        try:
            gem_quote = tdx_quotes_func(code="399006", setcode="1")
            gem_change = float(gem_quote.get("changePct", 0)) if gem_quote else 0
        except Exception as e:
            logger.warning(f"获取创业板行情失败: {e}, 使用默认值")
            gem_change = 0

        # 4. 市场宽度 -- 从全市场缓存估算
        try:
            import os
            cache_path = f"data/fullmarket_cache/state_{datetime.now().strftime('%Y%m%d')}.json"
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                stocks = cache.get("stocks", {})
                adv = sum(1 for s in stocks.values() if float(s.get("changePct", 0)) > 0)
                dec = sum(1 for s in stocks.values() if float(s.get("changePct", 0)) < 0)
            else:
                adv, dec = 2500, 2500  # 默认均衡
        except Exception as e:
            logger.warning(f"市场宽度计算失败: {e}, 使用默认值")
            adv, dec = 2500, 2500

        # 5. 北向资金 (默认0, 如有数据源可补充)
        nb_flow = 0

        logger.info(f"MEG数据: SH MA5={sh_ma5:.1f}/MA10={sh_ma10:.1f}, "
                     f"SZ MA5={sz_ma5:.1f}/MA10={sz_ma10:.1f}, "
                     f"GEM={gem_change:.2f}%, "
                     f"涨跌={adv}/{dec}")

        return self.evaluate(
            sh_ma5=sh_ma5, sh_ma10=sh_ma10,
            sz_ma5=sz_ma5, sz_ma10=sz_ma10,
            gem_change_pct=gem_change,
            adv_count=adv, dec_count=dec,
            sector_change_pct=0, sector_name="",
            nb_net_flow=nb_flow
        )

    def print_report(self, report: Dict = None):
        """打印MEG报告"""
        if report is None:
            report = self.get_report()

        print(f"\n{'='*60}")
        print(f"  V13.5.22 MEG 市场环境门控报告")
        print(f"  时间: {report.get('timestamp', datetime.now().isoformat())}")
        print(f"{'='*60}")
        print(f"  MES Score: {report['mes_score']}/100")
        print(f"  DEFCON: {report['defcon']}")
        print(f"  Action: {report['action']}")
        print(f"  Position Factor: {report['position_factor']}")
        print(f"  Can Buy: {report['can_buy']}")
        print(f"  Requires Strong Confirm: {report['requires_strong_confirm']}")
        print(f"\n  --- 五道门控 ---")
        for g in report['gates']:
            icon = "PASS" if g['status'] == 'PASS' else ("WARN" if g['status'] == 'WARN' else "FAIL")
            print(f"  [{icon}] {g['gate']}: {g['detail']}")
        print(f"{'='*60}\n")

    def get_report(self) -> Dict:
        """获取完整MEG报告"""
        self.determine_defcon()
        return {
            "mes_score": self.mes_score,
            "defcon": self.defcon,
            "action": DEFCON_ACTIONS[self.defcon],
            "position_factor": DEFCON_POSITION_FACTOR[self.defcon],
            "can_buy": self.can_buy(),
            "requires_strong_confirm": self.requires_strong_confirm(),
            "gates": self.gates,
            "market_data": self.market_data,
            "timestamp": datetime.now().isoformat()
        }


# ==================== T4集成接口 ====================

def meg_gate_for_t4(meg_report: Dict) -> Tuple[bool, str, float]:
    """
    T4选股流程调用: 根据MEG报告决定是否允许选股
    返回: (allow_select, reason, position_factor)
    """
    defcon = meg_report.get("defcon", DEFCON_GREEN)
    mes = meg_report.get("mes_score", 100)

    if defcon == DEFCON_RED:
        return False, f"MEG.RED: MES={mes}/100, 市场环境恶劣, 禁止买入", 0.0
    elif defcon == DEFCON_ORANGE:
        return True, f"MEG.ORANGE: MES={mes}/100, 仅允许五确认>=3的强信号, 仓位x0.3", 0.3
    elif defcon == DEFCON_YELLOW:
        return True, f"MEG.YELLOW: MES={mes}/100, 仅Top信号, 仓位x0.5", 0.5
    else:
        return True, f"MEG.GREEN: MES={mes}/100, 市场正常, 全量选股", 1.0


# ==================== 验证用例 ====================

def demo_72_crash():
    """验证: 模拟7/2 SECTOR_CRASH市场环境"""
    print("\n" + "=" * 60)
    print("  MEG验证: 模拟7/2 SECTOR_CRASH市场环境")
    print("=" * 60)

    meg = MarketEnvGate()
    report = meg.evaluate(
        sh_ma5=2950, sh_ma10=2980,    # 上证MA5<MA10
        sz_ma5=9200, sz_ma10=9350,    # 深证MA5<MA10
        gem_change_pct=-5.71,          # 创业板暴跌-5.71%
        adv_count=800, dec_count=4500, # 涨跌比极低
        sector_change_pct=-6.0,        # 半导体板块-6%
        sector_name="半导体",
        nb_net_flow=-120               # 北向流出120亿
    )
    meg.print_report(report)

    allow, reason, pos = meg_gate_for_t4(report)
    print(f"  T4选股决策: allow={allow}, reason={reason}, pos_factor={pos}")
    assert not allow, "7/2环境应禁止买入"
    print("  PASS: 7/2 CRASH环境正确阻止买入")

    # 反接飞刀验证
    afk1 = meg.anti_falling_knife(-7.0, -6.0, d29_score=8, confirm_count=5)
    print(f"\n  AFK: 个股-7%+板块-6%+D29=8+五确认=5 => {afk1['verdict']} ({afk1['reason']})")
    assert afk1['verdict'] == 'REJECT'

    afk2 = meg.anti_falling_knife(-3.0, 0.5, d29_score=7, confirm_count=3)
    print(f"  AFK: 个股-3%+板块+0.5%+D29=7+五确认=3 => {afk2['verdict']} ({afk2['reason']})")
    assert afk2['verdict'] == 'PASS'

    print("  PASS: 反接飞刀规则验证通过")


def demo_normal_market():
    """验证: 模拟正常市场环境"""
    print("\n" + "=" * 60)
    print("  MEG验证: 模拟正常市场环境 (6/29强市)")
    print("=" * 60)

    meg = MarketEnvGate()
    report = meg.evaluate(
        sh_ma5=3050, sh_ma10=3000,    # 上证MA5>MA10
        sz_ma5=9500, sz_ma10=9400,    # 深证MA5>MA10
        gem_change_pct=1.5,            # 创业板涨1.5%
        adv_count=3500, dec_count=1500, # 涨多跌少
        sector_change_pct=2.0,         # 板块涨2%
        sector_name="电子",
        nb_net_flow=30                 # 北向流入30亿
    )
    meg.print_report(report)

    allow, reason, pos = meg_gate_for_t4(report)
    print(f"  T4选股决策: allow={allow}, reason={reason}, pos_factor={pos}")
    assert allow, "正常环境应允许买入"
    assert pos == 1.0, "正常环境仓位系数应为1.0"
    print("  PASS: 正常环境正确允许买入")


def demo_mild_weak():
    """验证: 模拟温和弱势环境"""
    print("\n" + "=" * 60)
    print("  MEG验证: 模拟温和弱势环境")
    print("=" * 60)

    meg = MarketEnvGate()
    report = meg.evaluate(
        sh_ma5=3000, sh_ma10=3020,    # 上证MA5略<MA10
        sz_ma5=9400, sz_ma10=9450,    # 深证MA5略<MA10
        gem_change_pct=-0.8,           # 创业板小跌
        adv_count=2200, dec_count=2800, # 跌略多
        sector_change_pct=-0.5,
        sector_name="通用",
        nb_net_flow=-10
    )
    meg.print_report(report)

    allow, reason, pos = meg_gate_for_t4(report)
    print(f"  T4选股决策: allow={allow}, reason={reason}, pos_factor={pos}")
    print("  (温和弱势应减仓但不禁止)")


if __name__ == "__main__":
    print("=" * 60)
    print("  V13.5.22 MEG 市场环境门控模块 -- 验证")
    print("=" * 60)
    print()
    print("  DEFCON四级:")
    print(f"    GREEN  (>70): {DEFCON_ACTIONS[DEFCON_GREEN]}")
    print(f"    YELLOW (50-70): {DEFCON_ACTIONS[DEFCON_YELLOW]}")
    print(f"    ORANGE (30-50): {DEFCON_ACTIONS[DEFCON_ORANGE]}")
    print(f"    RED    (<30): {DEFCON_ACTIONS[DEFCON_RED]}")
    print()
    print("  五道门控:")
    print("    F1: 大盘趋势 (30分) -- 上证+深证MA5 vs MA10")
    print("    F2: 创业板动量 (25分) -- 跌>3%直接MES=0")
    print("    F3: 市场宽度 (20分) -- 涨跌比")
    print("    F4: 板块热度 (15分) -- 目标板块涨跌")
    print("    F5: 北向资金 (10分) -- 净流出>50亿扣分")
    print()
    print("  反接飞刀规则:")
    print("    个股跌>5% + 板块跌>3% => REJECT (崩盘非洗盘)")
    print("    个股跌>5% + 板块涨 => 需D29>=8 + 五确认>=4")
    print("    个股跌3-5% + 板块跌>2% => 需D29>=6 + 五确认>=3")
    print("=" * 60)

    demo_72_crash()
    demo_normal_market()
    demo_mild_weak()

    print("\n" + "=" * 60)
    print("  全部验证通过! MEG模块就绪")
    print("=" * 60)
