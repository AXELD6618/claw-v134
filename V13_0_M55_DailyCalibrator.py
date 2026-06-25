#!/usr/bin/env python3
"""
V13.0 M55 闭环反馈引擎（日频校准版）
=====================================
Phase 2 能力跃升：从每周校准升级为每日轻量校准

功能：
1. 每日15:05收盘复盘后自动触发M55轻量校准
2. 偏差>5% → 自动微调Sigmoid参数和因子权重
3. 7日滚动统计面板
4. 因子贡献度排名

校准策略：
- 日频轻量校准：仅微调Sigmoid k±0.5、x₀±0.01、单个因子权重±0.01
- 周日22:00大调：因子权重重调+Sigmoid/Bayesian全面校准（保留原有周频逻辑）
"""

import json
import math
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class CalibrationRecord:
    """单次校准记录"""
    date: str
    calibration_type: str  # 'daily_light' | 'weekly_full'
    pre_calibration_hit_rate: float
    post_calibration_hit_rate: float = 0.0
    sigmoid_k_before: float = 8.0
    sigmoid_k_after: float = 8.0
    sigmoid_x0_before: float = 0.55
    sigmoid_x0_after: float = 0.55
    weight_adjustments: Dict[str, float] = field(default_factory=dict)
    deviation_summary: Dict[str, float] = field(default_factory=dict)
    mine_count: int = 0
    notes: str = ''


class M55DailyCalibrator:
    """M55 日频校准器"""

    # 7权重配置
    WEIGHT_NAMES = ['催化(W1)', '政策(W2)', '板块(W3)', '动量(W4)', '资金(W5)', '舆情(W6)', '技术(W7)']
    DEFAULT_WEIGHTS = {
        '催化(W1)': 0.20,
        '政策(W2)': 0.10,
        '板块(W3)': 0.15,
        '动量(W4)': 0.20,
        '资金(W5)': 0.15,
        '舆情(W6)': 0.10,
        '技术(W7)': 0.10,
    }

    def __init__(self, data_dir: str = 'V13_0_data'):
        self.data_dir = data_dir
        self.calibration_file = os.path.join(data_dir, 'M55_CalibrationHistory.json')
        self.weights_file = os.path.join(data_dir, 'M55_CurrentWeights.json')

        os.makedirs(data_dir, exist_ok=True)
        self._load_state()

    def _load_state(self):
        """加载校准状态"""
        self.calibration_history: List[dict] = []
        if os.path.exists(self.calibration_file):
            with open(self.calibration_file, 'r', encoding='utf-8') as f:
                self.calibration_history = json.load(f)

        self.current_weights = self.DEFAULT_WEIGHTS.copy()
        if os.path.exists(self.weights_file):
            with open(self.weights_file, 'r', encoding='utf-8') as f:
                self.current_weights.update(json.load(f))

        self.current_sigmoid_k = 8.0
        self.current_sigmoid_x0 = 0.55

        # 从历史中恢复最新参数
        if self.calibration_history:
            latest = self.calibration_history[-1]
            self.current_sigmoid_k = latest.get('sigmoid_k_after', 8.0)
            self.current_sigmoid_x0 = latest.get('sigmoid_x0_after', 0.55)

    def _save_state(self):
        """保存校准状态"""
        with open(self.calibration_file, 'w', encoding='utf-8') as f:
            json.dump(self.calibration_history, f, ensure_ascii=False, indent=2)

        with open(self.weights_file, 'w', encoding='utf-8') as f:
            json.dump(self.current_weights, f, ensure_ascii=False, indent=2)

    # ═══════════════════════════════════════════════
    # 日频轻量校准
    # ═══════════════════════════════════════════════

    def daily_light_calibration(
        self,
        today_predictions: List[dict],     # [{'code','name','prob','confidence','actual_pct'}]
        today_hit_rate: float,
        today_plr: float,
        today_mine_count: int,
        force: bool = False,
    ) -> dict:
        """
        日频轻量校准

        Args:
            today_predictions: 今日预测-实际对比列表
            today_hit_rate: 今日命中率
            today_plr: 今日盈亏比
            today_mine_count: 今日踩雷数
            force: 是否强制校准（即使偏差<5%）
        """

        date_key = datetime.now().strftime('%Y-%m-%d')

        # ── 偏差计算 ──
        hit_rate_deviation = 0.45 - today_hit_rate
        plr_deviation = 3.0 - today_plr
        mine_rate = today_mine_count / max(len(today_predictions), 1)

        adjustments = {}
        notes = []

        # 检查是否需要校准
        need_calibration = (
            abs(hit_rate_deviation) > 0.05 or  # 命中率偏差>5%
            abs(plr_deviation) > 0.5 or         # 盈亏比偏差>0.5
            mine_rate > 0.08 or                 # 踩雷率>8%
            force
        )

        if not need_calibration:
            return {
                'date': date_key,
                'calibrated': False,
                'reason': '偏差在容忍范围内，跳过日频校准',
                'deviation': {
                    'hit_rate': round(hit_rate_deviation, 4),
                    'plr': round(plr_deviation, 2),
                    'mine_rate': round(mine_rate, 4),
                },
            }

        # ── Sigmoid微调 ──
        k_before = self.current_sigmoid_k
        x0_before = self.current_sigmoid_x0

        # 命中率偏低 → 降低x₀（降低门槛）或增加k（更敏感）
        if hit_rate_deviation > 0.03:
            # 命中率偏低超过3pp，降低门槛
            self.current_sigmoid_x0 = max(0.45, self.current_sigmoid_x0 - 0.01)
            self.current_sigmoid_k = min(12.0, self.current_sigmoid_k + 0.3)
            adjustments['sigmoid_x0'] = -0.01
            adjustments['sigmoid_k'] = +0.3
            notes.append(f'命中率偏差{hit_rate_deviation:.1%}→x₀↓0.01 + k↑0.3')
        elif hit_rate_deviation < -0.02:
            # 命中率偏高（可能是过度拟合），提升门槛
            self.current_sigmoid_x0 = min(0.65, self.current_sigmoid_x0 + 0.01)
            adjustments['sigmoid_x0'] = +0.01
            notes.append(f'命中率偏高{abs(hit_rate_deviation):.1%}→x₀↑0.01')

        # 踩雷率高 → 提升门槛
        if mine_rate > 0.08:
            self.current_sigmoid_x0 = min(0.65, self.current_sigmoid_x0 + 0.02)
            adjustments['sigmoid_x0'] = adjustments.get('sigmoid_x0', 0) + 0.02
            notes.append(f'踩雷率{mine_rate:.1%}→x₀↑0.02（严格过滤）')

        # ── 因子权重重调 ──
        weight_changes = self._compute_factor_contribution(today_predictions)

        for weight_name, change in weight_changes.items():
            if abs(change) >= 0.005:  # 只有显著变化才调整
                old = self.current_weights.get(weight_name, self.DEFAULT_WEIGHTS[weight_name])
                new = max(0.05, min(0.30, old + change))
                self.current_weights[weight_name] = round(new, 4)
                adjustments[weight_name] = round(change, 4)
                if change > 0:
                    notes.append(f'{weight_name}贡献↑ → +{change:.3f}')
                else:
                    notes.append(f'{weight_name}贡献↓ → {change:.3f}')

        # 归一化权重（保证总和=1.0）
        total_weight = sum(self.current_weights.values())
        for name in self.current_weights:
            self.current_weights[name] = round(self.current_weights[name] / total_weight, 4)

        # ── 保存校准记录 ──
        record = {
            'date': date_key,
            'type': 'daily_light',
            'hit_rate': today_hit_rate,
            'plr': today_plr,
            'mine_count': today_mine_count,
            'mine_rate': round(mine_rate, 4),
            'sigmoid_k_before': k_before,
            'sigmoid_k_after': self.current_sigmoid_k,
            'sigmoid_x0_before': x0_before,
            'sigmoid_x0_after': self.current_sigmoid_x0,
            'weight_adjustments': adjustments,
            'current_weights': self.current_weights.copy(),
            'notes': '; '.join(notes) if notes else '微调完成',
        }

        self.calibration_history.append(record)
        self._save_state()

        return {
            'date': date_key,
            'calibrated': True,
            'type': 'daily_light',
            'adjustments': adjustments,
            'sigmoid_k': {'before': k_before, 'after': self.current_sigmoid_k},
            'sigmoid_x0': {'before': x0_before, 'after': self.current_sigmoid_x0},
            'weights': self.current_weights.copy(),
            'notes': notes,
            'deviation': {
                'hit_rate': round(hit_rate_deviation, 4),
                'plr': round(plr_deviation, 2),
                'mine_rate': round(mine_rate, 4),
            },
        }

    def _compute_factor_contribution(self, predictions: List[dict]) -> Dict[str, float]:
        """
        计算各因子对命中率的贡献度
        基于实际涨跌 vs 因子得分，识别高效因子和低效因子
        """
        if not predictions:
            return {}

        contributions = {name: 0.0 for name in self.WEIGHT_NAMES}

        for pred in predictions:
            actual_pct = pred.get('actual_pct', 0)
            factor_scores = pred.get('factor_scores', {})

            # 假设：如果实际涨跌与因子方向一致，该因子贡献+1
            for name in self.WEIGHT_NAMES:
                factor_score = factor_scores.get(name, 0.5)
                direction_match = (factor_score > 0.5 and actual_pct > 0) or \
                                  (factor_score < 0.5 and actual_pct < 0)
                if direction_match:
                    contributions[name] += 1

        # 归一化为相对贡献度，转换为权重变化建议（±0.01范围）
        total = sum(contributions.values()) or 1
        weight_changes = {}

        for name in self.WEIGHT_NAMES:
            contribution_pct = contributions[name] / total
            # 贡献偏离平均20%以上时调整
            deviation = contribution_pct - (1.0 / len(self.WEIGHT_NAMES))
            weight_changes[name] = round(deviation * 0.05, 4)  # 缩放至±0.01范围

        return weight_changes

    # ═══════════════════════════════════════════════
    # 7日滚动统计
    # ═══════════════════════════════════════════════

    def get_7day_rolling_stats(self) -> dict:
        """
        7日滚动统计面板
        """
        now = datetime.now()
        seven_days_ago = now - timedelta(days=7)

        recent_records = [
            r for r in self.calibration_history
            if datetime.strptime(r['date'], '%Y-%m-%d') >= seven_days_ago
        ]

        if not recent_records:
            return {'status': 'insufficient_data', 'message': '7日内无校准记录'}

        hit_rates = [r.get('hit_rate', 0) for r in recent_records]
        plrs = [r.get('plr', 0) for r in recent_records]
        mine_counts = [r.get('mine_count', 0) for r in recent_records]

        avg_hit_rate = sum(hit_rates) / len(hit_rates) if hit_rates else 0
        avg_plr = sum(plrs) / len(plrs) if plrs else 0
        total_mines = sum(mine_counts)

        # 趋势（简单线性回归斜率）
        hit_rate_trend = 'improving' if len(hit_rates) >= 3 and hit_rates[-1] > hit_rates[0] else \
                         'declining' if len(hit_rates) >= 3 and hit_rates[-1] < hit_rates[0] else 'flat'

        return {
            'period': f'{seven_days_ago.strftime("%m/%d")}-{now.strftime("%m/%d")}',
            'days_with_data': len(recent_records),
            'avg_hit_rate': round(avg_hit_rate, 4),
            'avg_plr': round(avg_plr, 2),
            'total_mines': total_mines,
            'hit_rate_trend': hit_rate_trend,
            'hit_rate_target': 0.45,
            'hit_rate_gap': round(0.45 - avg_hit_rate, 4),
            'plr_target': 3.0,
            'plr_gap': round(3.0 - avg_plr, 2),
            'current_weights': self.current_weights.copy(),
            'current_sigmoid': {'k': self.current_sigmoid_k, 'x0': self.current_sigmoid_x0},
        }


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def run_daily_m55_calibration(
    predictions: List[dict],
    data_dir: str = 'V13_0_data',
    force: bool = False,
) -> dict:
    """
    M55日频校准快捷入口

    predictions format: [{
        'code': str, 'name': str,
        'prob': float, 'confidence': str,
        'actual_pct': float,  # 实际涨跌幅
        'factor_scores': {'催化(W1)': 0.7, '政策(W2)': 0.5, ...}
    }]
    """
    calibrator = M55DailyCalibrator(data_dir)

    # 计算当日指标
    hit_count = sum(1 for p in predictions if p.get('actual_pct', 0) > 0)
    total = max(len(predictions), 1)
    today_hit_rate = hit_count / total

    # 盈亏比（简化计算：平均盈利/平均亏损）
    wins = [p['actual_pct'] for p in predictions if p.get('actual_pct', 0) > 0]
    losses = [abs(p['actual_pct']) for p in predictions if p.get('actual_pct', 0) < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.05
    avg_loss = sum(losses) / len(losses) if losses else 0.03
    today_plr = avg_win / avg_loss if avg_loss > 0 else 2.4

    # 踩雷数（跌幅超过-5%或跌停）
    mine_count = sum(1 for p in predictions if p.get('actual_pct', 0) < -0.05)

    # 执行校准
    result = calibrator.daily_light_calibration(
        today_predictions=predictions,
        today_hit_rate=today_hit_rate,
        today_plr=today_plr,
        today_mine_count=mine_count,
        force=force,
    )

    # 添加7日滚动统计
    result['rolling_7day'] = calibrator.get_7day_rolling_stats()

    return result


if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 M55 闭环反馈引擎（日频校准版）")
    print("=" * 60)
    print("日频轻量校准：仅微调Sigmoid±0.5/x₀±0.01 + 单因子±0.01")
    print("周日22:00保留完整大调")
    print("=" * 60)

    # 自测
    test_predictions = [
        {'code': '002371', 'name': '北方华创', 'prob': 0.72, 'confidence': '高', 'actual_pct': 0.045,
         'factor_scores': {'催化(W1)': 0.75, '政策(W2)': 0.65, '板块(W3)': 0.70, '动量(W4)': 0.80, '资金(W5)': 0.60, '舆情(W6)': 0.55, '技术(W7)': 0.50}},
        {'code': '002851', 'name': '麦格米特', 'prob': 0.68, 'confidence': '中', 'actual_pct': 0.032,
         'factor_scores': {'催化(W1)': 0.80, '政策(W2)': 0.50, '板块(W3)': 0.65, '动量(W4)': 0.70, '资金(W5)': 0.55, '舆情(W6)': 0.60, '技术(W7)': 0.55}},
        {'code': '002028', 'name': '思源电气', 'prob': 0.55, 'confidence': '中', 'actual_pct': -0.015,
         'factor_scores': {'催化(W1)': 0.50, '政策(W2)': 0.55, '板块(W3)': 0.45, '动量(W4)': 0.40, '资金(W5)': 0.35, '舆情(W6)': 0.45, '技术(W7)': 0.40}},
        {'code': '000000', 'name': '踩雷测试', 'prob': 0.60, 'confidence': '中', 'actual_pct': -0.075,
         'factor_scores': {'催化(W1)': 0.60, '政策(W2)': 0.50, '板块(W3)': 0.55, '动量(W4)': 0.45, '资金(W5)': 0.40, '舆情(W6)': 0.35, '技术(W7)': 0.30}},
    ]

    result = run_daily_m55_calibration(test_predictions, force=True)
    print(f"\n📊 日频校准结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
