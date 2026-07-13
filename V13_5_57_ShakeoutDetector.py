#!/usr/bin/env python3
"""
V13.5.57 盘中洗盘vs出货智能识别引擎 (Shakeout vs Distribution Detector)

核心使命:
  区分主力机构的真实意图:
  - 洗盘 (SHAKEOUT): 开盘砸→V型回收→InOut由负转正 → HOLD/加仓
  - 出货 (DISTRIBUTION): 高开低走→InOut持续为负→尾盘跳水 → SELL

检测维度:
  D1: V型形态检测 — 开盘跳水≥3% → 回收到开盘价附近 → V型得分
  D2: InOut方向反转 — 15分钟负 → 30分钟内转正 → 洗盘确认
  D3: 量价关系 — 底部分时放量(恐慌抛售) → 反弹缩量(悄然吸筹)
  D4: 多级底确认 — 86→87→88 逐级抬高低点 → 非出货
  D5: 内外盘方向 — 外盘逐步超过内盘 → 买方重掌主动权
  D6: 时间窗口 — 09:30-10:30关键洗盘窗口 vs 14:00-15:00出货窗口

置信度规则:
  总分≥70 → SHAKEOUT (坚决持有)
  45-69 → POSSIBLE_SHAKEOUT (谨慎持有,观察)
  25-44 → UNCERTAIN (维持原信号)
  <25 → DISTRIBUTION (出货确认,执行SELL)

关键教训: 600118 2026-07-13
  09:30 开93.50 → 09:51 低86.00(-8%) → 09:56 回90.40 → 11:24 至94.60 → 午收97.08
  V55 09:41用昨日zjlx(InOut=-12亿)判定SELL → 11:24 InOut转正才修正STRONG_HOLD
  这是典型的V型洗盘 → V57应提前在09:56检测到并发出HOLD信号
"""

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta


@dataclass
class MinBar:
    """5分钟K线"""
    time: str          # "HH:MM"
    open: float
    high: float
    low: float
    close: float
    volume: int        # 手
    amount: float      # 元


@dataclass
class InOutSnapshot:
    """InOut快照"""
    time: str
    in_out: float       # 主买净额(元)
    outside: int        # 外盘
    inside: int         # 内盘
    price: float


@dataclass
class ShakeoutResult:
    """洗盘检测结果"""
    code: str
    name: str
    score: int                    # 0-100
    pattern: str                  # SHAKEOUT / POSSIBLE_SHAKEOUT / UNCERTAIN / DISTRIBUTION
    confidence: float             # 0.0-1.0
    v_shape_score: int            # D1: V型形态分
    inout_reverse_score: int      # D2: InOut反转分
    volume_score: int             # D3: 量价关系分
    bottom_structure_score: int   # D4: 多级底分
    outside_inside_score: int     # D5: 内外盘分
    time_window_score: int        # D6: 时间窗口分
    signal: str                   # HOLD / WATCH / SELL
    reason: str
    details: List[str] = field(default_factory=list)
    bottom_prices: List[float] = field(default_factory=list)
    recovery_pct: float = 0.0     # 回收百分比


class ShakeoutDetector:
    """V57 洗盘vs出货智能识别引擎"""

    # 评分权重
    WEIGHTS = {
        'v_shape': 30,         # V型形态(最重要)
        'inout_reverse': 25,   # InOut方向反转
        'volume': 15,          # 量价关系
        'bottom_structure': 12, # 多级底
        'outside_inside': 10,  # 内外盘
        'time_window': 8,      # 时间窗口
    }

    # 阈值
    OPEN_DROP_THRESHOLD = 0.03    # 开盘跌幅≥3%=潜在洗盘
    RECOVERY_THRESHOLD = 0.6      # 回收60%以上跌幅=V型确认
    INOUT_REVERSE_WINDOW = 30     # InOut反转窗口(分钟)
    BOTTOM_TICK_UP = 0.005        # 底部逐级抬高阈值

    def diagnose(self, bars_5min: List[MinBar],
                 inout_snapshots: List[InOutSnapshot],
                 code: str, name: str,
                 prev_day_inout: float = 0.0,
                 current_price: float = 0.0,
                 open_price: float = 0.0,
                 prev_close: float = 0.0) -> ShakeoutResult:
        """
        主诊断函数: 输入5分钟K线序列+InOut快照, 输出洗盘vs出货判定
        
        Args:
            bars_5min: 5分钟K线列表(按时间排序)
            inout_snapshots: InOut快照列表(按时间排序)
            prev_day_inout: 昨日InOut(亿元), 辅助参考
            current_price: 当前价格
            open_price: 开盘价
            prev_close: 昨收价
        """
        if not bars_5min:
            return self._empty_result(code, name, "无K线数据")

        details = []
        total_score = 0

        # D1: V型形态检测
        v_score, v_details, bottom_prices, recovery_pct = self._detect_v_shape(bars_5min, open_price)
        total_score += v_score * self.WEIGHTS['v_shape'] / 100
        details.extend(v_details)

        # D2: InOut方向反转检测
        io_score, io_details = self._detect_inout_reversal(inout_snapshots)
        total_score += io_score * self.WEIGHTS['inout_reverse'] / 100
        details.extend(io_details)

        # D3: 量价关系检测
        vol_score, vol_details = self._detect_volume_pattern(bars_5min, bottom_prices)
        total_score += vol_score * self.WEIGHTS['volume'] / 100
        details.extend(vol_details)

        # D4: 多级底结构检测
        bs_score, bs_details = self._detect_bottom_structure(bars_5min)
        total_score += bs_score * self.WEIGHTS['bottom_structure'] / 100
        details.extend(bs_details)

        # D5: 内外盘方向检测
        oi_score, oi_details = self._detect_outside_inside(inout_snapshots, bars_5min)
        total_score += oi_score * self.WEIGHTS['outside_inside'] / 100
        details.extend(oi_details)

        # D6: 时间窗口检测
        tw_score, tw_details = self._detect_time_window(bars_5min, current_price, open_price)
        total_score += tw_score * self.WEIGHTS['time_window'] / 100
        details.extend(tw_details)

        # 昨日InOut辅助调整
        if prev_day_inout < -5:  # 昨日大幅出货
            total_score -= 8
            details.append(f"昨日对倒(-{abs(prev_day_inout):.1f}亿)扣8分,需更强证据")
        elif prev_day_inout > 5:  # 昨日大幅流入
            total_score += 5
            details.append(f"昨日真实流入(+{prev_day_inout:.1f}亿)加5分")

        # 锁定得分范围
        total_score = max(0, min(100, total_score))
        confidence = total_score / 100.0

        # 判定模式
        if total_score >= 70:
            pattern = "SHAKEOUT"
            signal = "HOLD"
        elif total_score >= 45:
            pattern = "POSSIBLE_SHAKEOUT"
            signal = "HOLD"
        elif total_score >= 25:
            pattern = "UNCERTAIN"
            signal = "WATCH"
        else:
            pattern = "DISTRIBUTION"
            signal = "SELL"

        reason = self._build_reason(pattern, total_score, recovery_pct, prev_day_inout, details)

        return ShakeoutResult(
            code=code, name=name, score=total_score,
            pattern=pattern, confidence=confidence,
            v_shape_score=v_score, inout_reverse_score=io_score,
            volume_score=vol_score, bottom_structure_score=bs_score,
            outside_inside_score=oi_score, time_window_score=tw_score,
            signal=signal, reason=reason, details=details,
            bottom_prices=bottom_prices, recovery_pct=recovery_pct
        )

    def _detect_v_shape(self, bars: List[MinBar], open_price: float) -> Tuple[int, List[str], List[float], float]:
        """D1: 检测V型形态"""
        details = []
        if open_price <= 0:
            return 0, ["开盘价无效"], [], 0

        # 找最低点
        min_bar = min(bars, key=lambda b: b.low)
        min_idx = bars.index(min_bar)
        min_low = min_bar.low
        drop_pct = (min_low - open_price) / open_price

        # 找回收点(最低点之后的最高收盘)
        recovery_bars = bars[min_idx:]
        if not recovery_bars:
            return 0, ["无回收数据"], [], 0

        max_recovery = max(b.close for b in recovery_bars)
        recovery_idx = max(i for i, b in enumerate(bars) if b.close == max_recovery)

        recovery_pct = (max_recovery - min_low) / (open_price - min_low) if open_price > min_low else 0
        if recovery_pct > 1.0:
            recovery_pct = 1.0

        # 底部价格列表
        bottom_prices = [min_low]

        score = 0

        # 开盘跌幅评分
        if drop_pct <= -0.08:
            score += 40
            details.append(f"开盘急跌{abs(drop_pct)*100:.1f}%(满分洗盘幅度)")
        elif drop_pct <= -0.05:
            score += 30
            details.append(f"开盘跌幅{abs(drop_pct)*100:.1f}%(强烈洗盘)")
        elif drop_pct <= -0.03:
            score += 20
            details.append(f"开盘跌幅{abs(drop_pct)*100:.1f}%(中等洗盘)")
        else:
            score += 5
            details.append(f"跌幅不足{abs(drop_pct)*100:.1f}%(弱洗盘特征)")

        # 回收百分比评分
        if recovery_pct >= 0.8:
            score += 50
            details.append(f"V型回收{recovery_pct*100:.0f}%(满分回收)")
        elif recovery_pct >= 0.6:
            score += 35
            details.append(f"强势回收{recovery_pct*100:.0f}%")
        elif recovery_pct >= 0.4:
            score += 20
            details.append(f"中等回收{recovery_pct*100:.0f}%")
        else:
            score += 5
            details.append(f"弱回收{recovery_pct*100:.0f}%,非V型")

        # 时间结构
        if min_idx <= 5:  # 前5根K线(25分钟)就见底
            score += 10
            details.append("底部在25分钟内形成(快速洗盘特征)")

        return min(score, 100), details, bottom_prices, recovery_pct

    def _detect_inout_reversal(self, snapshots: List[InOutSnapshot]) -> Tuple[int, List[str]]:
        """D2: InOut方向反转检测"""
        details = []
        if len(snapshots) < 2:
            return 0, ["InOut快照不足,无法检测反转"]

        # 找最早的负InOut
        first_neg_idx = None
        for i, s in enumerate(snapshots):
            if s.in_out < 0:
                first_neg_idx = i
                break

        if first_neg_idx is None:
            return 70, ["InOut从未为负(强真实买入)"]

        first_neg_time = snapshots[first_neg_idx].time
        first_neg_val = snapshots[first_neg_idx].in_out

        # 找第一个转为正的InOut
        first_pos_idx = None
        for i in range(first_neg_idx + 1, len(snapshots)):
            if snapshots[i].in_out > 0:
                first_pos_idx = i
                break

        score = 0

        if first_pos_idx is None:
            # 检查是否InOut在改善(负值变小)
            current_neg = snapshots[-1].in_out
            if current_neg > first_neg_val:  # 负值在减少(趋向正)
                improvement = (current_neg - first_neg_val) / abs(first_neg_val) if first_neg_val != 0 else 0
                score = int(40 * min(improvement, 1.0))
                details.append(f"InOut改善中({first_neg_val/1e8:.1f}亿→{current_neg/1e8:.1f}亿, +{improvement*100:.0f}%)")
            else:
                score = 0
                details.append(f"InOut持续为负且无改善(恶意出货)")
        else:
            first_pos_time = snapshots[first_pos_idx].time
            first_pos_val = snapshots[first_pos_idx].in_out
            reversal_val = first_pos_val - first_neg_val

            # 反转幅度评分
            if reversal_val > 5e8:  # 超过5亿反转
                score = 90
                details.append(f"InOut强力反转({first_neg_val/1e8:.1f}→{first_pos_val/1e8:.1f}亿, +{reversal_val/1e8:.1f}亿)")
            elif reversal_val > 2e8:
                score = 70
                details.append(f"InOut中强反转(+{reversal_val/1e8:.1f}亿)")
            elif reversal_val > 5e7:
                score = 50
                details.append(f"InOut弱反转(+{reversal_val/1e8:.2f}亿)")
            else:
                score = 30
                details.append(f"InOut微弱反转")

            # 时间紧迫性评分
            try:
                neg_dt = datetime.strptime(first_neg_time, "%H:%M")
                pos_dt = datetime.strptime(first_pos_time, "%H:%M")
                reversal_minutes = (pos_dt - neg_dt).total_seconds() / 60
                if reversal_minutes <= 15:
                    score = min(score + 10, 100)
                    details.append(f"反转极快({reversal_minutes:.0f}分钟,强洗盘)")
                elif reversal_minutes <= 30:
                    score = min(score + 5, 100)
                    details.append(f"反转速度正常({reversal_minutes:.0f}分钟)")
            except:
                pass

        return min(score, 100), details

    def _detect_volume_pattern(self, bars: List[MinBar], bottom_prices: List[float]) -> Tuple[int, List[str]]:
        """D3: 量价关系检测"""
        details = []
        if not bars:
            return 0, ["无K线数据"]

        # 找底部放量段(最低价附近3根K线)
        min_low = min(b.low for b in bars)
        bottom_bars = sorted(bars, key=lambda b: b.low)[:4]  # 最低4根
        avg_bottom_vol = sum(b.volume for b in bottom_bars) / len(bottom_bars) if bottom_bars else 0

        # 所有K线平均成交量
        avg_vol = sum(b.volume for b in bars) / len(bars)

        score = 0

        # 底部放量检测
        if avg_bottom_vol > avg_vol * 1.5:
            score += 50
            details.append(f"底部放量{avg_bottom_vol/avg_vol:.1f}倍(恐慌抛售被接盘)")
        elif avg_bottom_vol > avg_vol * 1.2:
            score += 30
            details.append(f"底部轻微放量{avg_bottom_vol/avg_vol:.1f}倍")
        else:
            score += 10
            details.append("底部无明显放量")

        # 反弹缩量检测
        max_low = max(b.low for b in bottom_bars) if bottom_bars else min_low
        recovery_bars = [b for b in bars if b.low > max_low and b.close > b.open]
        if recovery_bars:
            avg_recovery_vol = sum(b.volume for b in recovery_bars) / len(recovery_bars)
            if avg_recovery_vol < avg_bottom_vol * 0.7:
                score += 40
                details.append("反弹缩量(悄然吸筹,不为市场所知)")
            elif avg_recovery_vol < avg_bottom_vol:
                score += 20
                details.append("反弹量能正常下降")
        else:
            score += 5

        return min(score, 100), details

    def _detect_bottom_structure(self, bars: List[MinBar]) -> Tuple[int, List[str]]:
        """D4: 多级底结构检测"""
        details = []
        if len(bars) < 6:
            return 0, ["K线不足无法检测底部结构"]

        # 找局部低点(前低后低)
        lows = [(i, b.low) for i, b in enumerate(bars)]
        local_bottoms = []
        for i in range(2, len(lows) - 2):
            if lows[i][1] <= lows[i-1][1] and lows[i][1] <= lows[i-2][1] and \
               lows[i][1] <= lows[i+1][1] and lows[i][1] <= lows[i+2][1]:
                local_bottoms.append((lows[i][0], lows[i][1]))

        score = 0

        if len(local_bottoms) >= 3:
            # 检查底部是否逐级抬高
            tick_up_count = 0
            for i in range(1, len(local_bottoms)):
                if local_bottoms[i][1] > local_bottoms[i-1][1] * (1 + self.BOTTOM_TICK_UP):
                    tick_up_count += 1

            if tick_up_count >= 2:
                score = 90
                details.append(f"底部逐级抬高({local_bottoms[0][1]:.2f}→{local_bottoms[-1][1]:.2f}, 经典洗盘)")
            elif tick_up_count >= 1:
                score = 50
                details.append("底部轻微抬高(弱洗盘特征)")
            else:
                score = 10
                details.append("底部逐级走低(出货特征)")
        elif len(local_bottoms) == 2:
            if local_bottoms[1][1] > local_bottoms[0][1]:
                score = 40
                details.append("双底抬高")
            else:
                score = 15
                details.append("双底走低")
        else:
            score = 5
            details.append("无明显底部结构")

        return min(score, 100), details

    def _detect_outside_inside(self, snapshots: List[InOutSnapshot], bars: List[MinBar]) -> Tuple[int, List[str]]:
        """D5: 内外盘方向检测"""
        details = []
        if not snapshots:
            return 30, ["无InOut数据"]

        # 检查外盘/内盘趋势
        early_snapshots = snapshots[:max(1, len(snapshots)//3)]
        late_snapshots = snapshots[2*len(snapshots)//3:]

        early_oi_ratio = sum(s.outside / max(s.inside, 1) for s in early_snapshots) / len(early_snapshots) if early_snapshots else 1.0
        late_oi_ratio = sum(s.outside / max(s.inside, 1) for s in late_snapshots) / len(late_snapshots) if late_snapshots else 1.0

        score = 0

        if late_oi_ratio > early_oi_ratio * 1.2:
            score = 80
            details.append(f"外盘占比转为压倒(早期{early_oi_ratio:.2f}→后期{late_oi_ratio:.2f})")
        elif late_oi_ratio > early_oi_ratio:
            score = 60
            details.append(f"外盘占比上升(买方重掌主动权)")
        elif late_oi_ratio > 1.0:
            score = 40
            details.append("外盘持续大于内盘")
        else:
            score = 15
            details.append("外盘弱势(卖方主导)")

        # 检查当前外盘/内盘
        if snapshots:
            last = snapshots[-1]
            if last.outside > last.inside * 1.2:
                score = min(score + 15, 100)
                details.append("当前外盘显著占优")

        return min(score, 100), details

    def _detect_time_window(self, bars: List[MinBar], current_price: float, open_price: float) -> Tuple[int, List[str]]:
        """D6: 时间窗口检测"""
        details = []
        if not bars:
            return 30, []

        # 判断当前时间窗口
        earliest_time = bars[0].time
        latest_time = bars[-1].time

        score = 0
        try:
            et = datetime.strptime(earliest_time, "%H:%M")
            lt = datetime.strptime(latest_time, "%H:%M")

            # 09:30-10:30 是经典洗盘窗口
            morning_start = datetime.strptime("09:30", "%H:%M")
            morning_end = datetime.strptime("10:30", "%H:%M")
            afternoon_start = datetime.strptime("13:30", "%H:%M")
            afternoon_end = datetime.strptime("14:55", "%H:%M")

            if morning_start <= lt <= morning_end:
                score = 80
                details.append("09:30-10:30经典洗盘窗口(非出货时间)")
            elif lt <= datetime.strptime("11:00", "%H:%M"):
                score = 60
                details.append("上午窗口(偏洗盘)")
            elif afternoon_start <= lt <= afternoon_end:
                # 下午窗口,检查走势
                if current_price > open_price:
                    score = 40
                    details.append("下午窗口但价格在开盘价上方(偏洗盘)")
                else:
                    score = 15
                    details.append("下午窗口且价格低于开盘(出货风险)")
            else:
                score = 50
        except:
            score = 50

        return min(score, 100), details

    def _build_reason(self, pattern: str, score: int, recovery_pct: float,
                      prev_day_inout: float, details: List[str]) -> str:
        """构建判定理由"""
        parts = []
        if pattern == "SHAKEOUT":
            parts.append(f"洗盘确认(得分{score})")
        elif pattern == "POSSIBLE_SHAKEOUT":
            parts.append(f"可能洗盘(得分{score})")
        elif pattern == "DISTRIBUTION":
            parts.append(f"出货确认(得分{score})")
        else:
            parts.append(f"不确定(得分{score})")

        parts.append(f"V型回收{recovery_pct*100:.0f}%")
        if prev_day_inout < -5:
            parts.append(f"昨日对倒{-prev_day_inout:.1f}亿需警惕")
        elif prev_day_inout > 5:
            parts.append(f"昨日流入+{prev_day_inout:.1f}亿支持")

        # 关键细节
        for d in details[:3]:
            if "V型" in d or "反转" in d or "底部" in d or "外盘" in d:
                parts.append(d.split("(")[0] if "(" in d else d)

        return "; ".join(parts[:5])

    def _empty_result(self, code: str, name: str, reason: str) -> ShakeoutResult:
        return ShakeoutResult(
            code=code, name=name, score=0, pattern="UNCERTAIN",
            confidence=0, v_shape_score=0, inout_reverse_score=0,
            volume_score=0, bottom_structure_score=0,
            outside_inside_score=0, time_window_score=0,
            signal="WATCH", reason=reason
        )

    def combine_with_v55(self, v55_signal: str, v55_wash: Dict,
                          shakeout_result: ShakeoutResult) -> Dict[str, Any]:
        """
        综合V55对倒检测 + V57洗盘识别 → 最终信号
        
        核心规则:
        1. V57=SHAKEOUT + V55=WASH_TRADE(old data) → 覆盖为HOLD(V57优先)
        2. V57=DISTRIBUTION + V55=GENUINE → 维持SELL(V57识别出货)
        3. V57=UNCERTAIN → 维持V55信号
        """
        final_signal = v55_signal
        override = False
        reason_override = ""

        if shakeout_result.pattern == "SHAKEOUT":
            if v55_signal in ("SELL", "WATCH"):
                final_signal = "HOLD"
                override = True
                reason_override = f"V57洗盘确认(得分{shakeout_result.score})覆盖V55出货信号(陈旧zjlx)"
        elif shakeout_result.pattern == "POSSIBLE_SHAKEOUT":
            if v55_signal == "SELL":
                final_signal = "WATCH"
                override = True
                reason_override = f"V57可能洗盘(得分{shakeout_result.score})降级V55 SELL为WATCH"
        elif shakeout_result.pattern == "DISTRIBUTION":
            final_signal = "SELL"
            if v55_signal != "SELL":
                override = True
                reason_override = f"V57出货确认(得分{shakeout_result.score})覆盖V55 {v55_signal}"

        return {
            "final_signal": final_signal,
            "v55_signal": v55_signal,
            "v57_pattern": shakeout_result.pattern,
            "v57_score": shakeout_result.score,
            "v55_wash": v55_wash,
            "v57_details": shakeout_result.details,
            "override": override,
            "override_reason": reason_override,
            "bottom_prices": shakeout_result.bottom_prices,
            "recovery_pct": shakeout_result.recovery_pct
        }


def test_600118_0713():
    """用今日600118真实数据测试V57"""
    # 模拟5分钟K线(基于今日TDX真实数据)
    bars = [
        MinBar("09:35", 93.50, 94.51, 86.98, 89.50, 353440, 3.258e9),
        MinBar("09:40", 89.60, 92.20, 89.60, 90.45, 122914, 1.119e9),
        MinBar("09:45", 90.34, 90.34, 87.81, 89.70, 83323, 7.401e8),
        MinBar("09:50", 89.70, 91.30, 88.81, 91.12, 47728, 4.304e8),
        MinBar("09:55", 91.07, 92.00, 90.04, 90.47, 56260, 5.141e8),
        MinBar("10:00", 90.47, 91.73, 90.20, 90.43, 39444, 3.588e8),
        MinBar("10:05", 90.35, 90.35, 88.80, 89.75, 27268, 2.439e8),
        MinBar("10:10", 89.75, 90.56, 89.39, 90.56, 14261, 1.278e8),
        MinBar("10:15", 90.63, 91.09, 90.00, 90.57, 27085, 2.458e8),
        MinBar("10:20", 90.53, 90.62, 89.73, 89.73, 11872, 1.071e8),
        MinBar("10:25", 89.70, 90.99, 89.50, 90.48, 12922, 1.168e8),
        MinBar("10:30", 90.41, 90.41, 88.54, 88.90, 24962, 2.227e8),
        MinBar("10:35", 88.87, 88.90, 87.00, 87.01, 37997, 3.329e8),
        MinBar("10:40", 87.01, 87.77, 86.38, 86.40, 26955, 2.351e8),
        MinBar("10:45", 86.36, 87.73, 86.00, 87.65, 22960, 1.993e8),
        MinBar("10:50", 87.65, 88.49, 86.99, 88.49, 13763, 1.206e8),
        MinBar("10:55", 88.59, 90.39, 88.59, 89.99, 27627, 2.482e8),
        MinBar("11:00", 90.00, 90.04, 89.11, 89.30, 13869, 1.246e8),
        MinBar("11:05", 89.54, 91.00, 89.30, 90.99, 32429, 2.938e8),
        MinBar("11:10", 90.99, 93.81, 90.97, 93.81, 47763, 4.398e8),
        MinBar("11:15", 93.93, 95.00, 92.55, 92.62, 73350, 6.917e8),
        MinBar("11:20", 92.83, 94.00, 92.83, 93.99, 31290, 2.932e8),
        MinBar("11:25", 93.99, 97.20, 93.80, 97.20, 40516, 3.841e8),
    ]

    # 模拟InOut快照
    snapshots = [
        InOutSnapshot("09:41", -1.5e8, 15000, 20000, 90.49),   # 初期负
        InOutSnapshot("09:57", -5.0e7, 30000, 35000, 90.40),   # 改善中
        InOutSnapshot("10:30", +1.5e8, 60000, 50000, 88.90),   # 转正!
        InOutSnapshot("11:00", +3.0e8, 80000, 70000, 89.30),
        InOutSnapshot("11:24", +5.56e8, 100000, 90000, 94.60), # 强势正
    ]

    detector = ShakeoutDetector()
    result = detector.diagnose(
        bars, snapshots, "600118", "中国卫星",
        prev_day_inout=-12.0,   # 7/10对倒-12亿
        current_price=97.08,
        open_price=93.50,
        prev_close=90.02
    )

    print("=" * 60)
    print(f"V57 洗盘vs出货检测: {result.name}({result.code})")
    print(f"判定: {result.pattern} | 得分: {result.score}/100 | 置信度: {result.confidence:.2f}")
    print(f"信号: {result.signal}")
    print(f"D1 V型: {result.v_shape_score} | D2 InOut反转: {result.inout_reverse_score}")
    print(f"D3 量价: {result.volume_score} | D4 多级底: {result.bottom_structure_score}")
    print(f"D5 内外盘: {result.outside_inside_score} | D6 时间窗: {result.time_window_score}")
    print(f"回收%: {result.recovery_pct:.1%}")
    print(f"底部: {result.bottom_prices}")
    print(f"理由: {result.reason}")
    print("细节:")
    for d in result.details:
        print(f"  {d}")

    # 综合V55
    combined = detector.combine_with_v55(
        "SELL",
        {"is_wash": True, "signal": "WASH_TRADE", "confidence": 0.889},
        result
    )
    print(f"\n综合V55+57: {combined['final_signal']}")
    if combined['override']:
        print(f"覆盖: {combined['override_reason']}")

    return result


if __name__ == "__main__":
    test_600118_0713()
