#!/usr/bin/env python3
"""
V13.0 ML智能辅助引擎
=====================
A-2 解决：关键检测器引入ML辅助，降低纯规则引擎僵化风险

核心能力：
  1. IndustryMomentumPredictor  — 行业动量线性回归预测（自适应滑动窗口）
  2. PatternEnsembleScorer       — 形态检测加权集成分数（历史绩效加权）
  3. AdaptiveThresholdCalibrator — 自适应阈值学习（从回测数据学习最优阈值）
  4. SectorRotationDetector      — 板块轮动识别（增量相关分析）
  5. AnomalyScorer               — 异常检测（Z-score/孤立特征）

集成方式：
  与 PatternDetector 并行运行，输出 ML增强信号注入 Layer 2 和 Layer 4
"""

import math
import random
import statistics
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from collections import deque


# ═══════════════════════════════════════════════
# 1. 行业动量预测器
# ═══════════════════════════════════════════════

@dataclass
class MomentumConfig:
    lookback: int = 20          # 滑动窗口
    min_r2: float = 0.3         # 最低R²（拟合可信度）
    decay_alpha: float = 0.85   # EMA衰减
    breakout_std: float = 2.0   # 突破阈值（标准差倍数）


class IndustryMomentumPredictor:
    """
    行业动量线性回归预测器

    对每个行业/板块的价格序列做滑动窗口线性回归，
    预测未来1-3日的动量方向与强度。

    ML价值：替代硬编码的"连续X日放量"规则，
    自适应学习各行业的动量特征。
    """

    def __init__(self, config: MomentumConfig = None):
        self.config = config or MomentumConfig()
        self.history: Dict[str, deque] = {}        # code → EMA序列
        self.predictions: Dict[str, dict] = {}     # 缓存最近预测结果

    def _linear_regression(self, x: List[float], y: List[float]) -> Tuple[float, float, float]:
        """
        简单线性回归：y = slope * x + intercept，返回(slope, intercept, r²)
        """
        n = len(x)
        if n < 3:
            return 0.0, y[-1] if y else 0.0, 0.0

        mean_x, mean_y = sum(x) / n, sum(y) / n
        ss_xy = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        ss_xx = sum((xi - mean_x) ** 2 for xi in x)
        ss_yy = sum((yi - mean_y) ** 2 for yi in y)

        if ss_xx == 0:
            return 0.0, mean_y, 0.0

        slope = ss_xy / ss_xx
        intercept = mean_y - slope * mean_x
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy > 0 else 0.0

        return slope, intercept, r_squared

    def _ema(self, values: List[float], alpha: float = None) -> float:
        """指数移动平均"""
        if not values:
            return 0.0
        alpha = alpha or self.config.decay_alpha
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return ema

    def predict_industry_momentum(self, code: str, prices: List[float],
                                  volumes: List[float] = None) -> dict:
        """
        预测行业/个股未来1-3日动量

        参数：
          code: 行业/板块代码
          prices: 价格序列（最新在末尾）
          volumes: 成交量序列（可选）

        返回：
          {direction, strength, confidence, forecast_1d, forecast_3d, trend_r2}
        """
        cfg = self.config
        n = min(len(prices), cfg.lookback)

        # 取最近N个数据点
        recent_prices = prices[-n:]
        x = list(range(n))

        # 回归
        slope, intercept, r2 = self._linear_regression(x, recent_prices)

        # 预测
        forecast_1d = slope * (n + 1) + intercept  # T+1
        forecast_3d = slope * (n + 3) + intercept  # T+3

        current = recent_prices[-1]
        pct_change_1d = (forecast_1d - current) / current if current > 0 else 0.0
        pct_change_3d = (forecast_3d - current) / current if current > 0 else 0.0

        # 置信度：R² × (趋势方向一致性)
        direction_consistent = (slope > 0) == (prices[-1] > prices[-n]) if n > 1 else True
        confidence = r2 * (1.0 if direction_consistent else 0.5)

        # 方向
        if pct_change_3d > 0.02:
            direction = 'strong_up'
        elif pct_change_3d > 0:
            direction = 'weak_up'
        elif pct_change_3d > -0.02:
            direction = 'flat'
        elif pct_change_3d > -0.05:
            direction = 'weak_down'
        else:
            direction = 'strong_down'

        # 量价配合
        vol_confirmation = 0.5
        if volumes and len(volumes) >= 5:
            recent_vol = sum(volumes[-5:]) / 5
            all_vol = sum(volumes) / len(volumes)
            if recent_vol > all_vol * 1.2 and direction.startswith('strong'):
                vol_confirmation = 1.0  # 放量+强势 = 强确认
            elif recent_vol < all_vol * 0.8 and direction.endswith('down'):
                vol_confirmation = 0.8  # 缩量下跌 = 部分确认

        # 强度归一化
        strength = min(1.0, abs(pct_change_3d) * 10)
        if direction.endswith('down'):
            strength = -strength

        result = {
            'code': code,
            'direction': direction,
            'strength': round(strength, 4),
            'confidence': round(confidence * vol_confirmation, 4),
            'forecast_1d': round(forecast_1d, 2),
            'forecast_3d': round(forecast_3d, 2),
            'pct_change_1d': round(pct_change_1d, 4),
            'pct_change_3d': round(pct_change_3d, 4),
            'trend_r2': round(r2, 4),
            'vol_confirmation': round(vol_confirmation, 2),
            'ml_signal': confidence * vol_confirmation > 0.5,
        }

        self.predictions[code] = result
        return result

    def predict_batch(self, codes: List[str], price_dict: Dict[str, List[float]],
                      volume_dict: Dict[str, List[float]] = None) -> Dict[str, dict]:
        """批量预测"""
        results = {}
        for code in codes:
            prices = price_dict.get(code, [])
            volumes = volume_dict.get(code) if volume_dict else None
            if len(prices) >= 10:
                results[code] = self.predict_industry_momentum(code, prices, volumes)
        return results

    def get_top_momentum(self, n: int = 5) -> List[dict]:
        """获取动量最强的前N个"""
        ranked = sorted(
            self.predictions.values(),
            key=lambda x: (x['strength'] * x['confidence']),
            reverse=True
        )
        return [r for r in ranked if r['direction'].endswith('up')][:n]


# ═══════════════════════════════════════════════
# 2. 形态检测加权集成分数
# ═══════════════════════════════════════════════

@dataclass
class EnsembleConfig:
    learning_rate: float = 0.01         # 绩效反馈学习率
    min_weight: float = 0.3             # 最低权重
    max_weight: float = 3.0             # 最高权重
    initial_weight: float = 1.0         # 初始权重
    performance_window: int = 20        # 绩效观察窗口


class PatternEnsembleScorer:
    """
    形态检测加权集成分数

    核心思路：不是所有检测器在当前市场都同样有效。
    根据各检测器近20次预测的实际表现（命中率），
    动态调整其在最终投票中的权重。

    这与硬编码的 pattern_weights 并行运行，提供 ML 增强。
    """

    def __init__(self, config: EnsembleConfig = None):
        self.config = config or EnsembleConfig()
        # 检测器权重（动态学习）
        self.weights: Dict[str, float] = {}
        # 各检测器历史绩效
        self.performance: Dict[str, deque] = {}  # 最近N次的命中/未命中
        # 默认检测器列表
        self.default_detectors = [
            '老鸭头', '擒龙战法', '主力信号', '主升浪买点',
            '2560战法', '底量超顶量', '二板定龙头',
            '筹码擒龙', '月线MACD擒牛', '暗盘资金',
            '开盘溢价率', '委比量比', '分时图黄白线', '时间窗口',
        ]

    def _init_detector(self, name: str):
        if name not in self.weights:
            self.weights[name] = self.config.initial_weight
        if name not in self.performance:
            self.performance[name] = deque(maxlen=self.config.performance_window)

    def get_weight(self, detector_name: str) -> float:
        """获取检测器当前权重"""
        self._init_detector(detector_name)
        return self.weights[detector_name]

    def compute_ensemble_score(self, pattern_results: Dict[str, dict],
                                individual_scores: Dict[str, float] = None) -> dict:
        """
        计算加权集成分数

        参数：
          pattern_results: {检测器名: {score, grade, ...}}
          individual_scores: 可选的独立分数映射

        返回：
          {ensemble_score, weighted_sum, detector_contributions, ...}
        """
        weighted_sum = 0.0
        weight_total = 0.0
        contributions = []
        best_detector = ('', 0.0)

        for name, result in pattern_results.items():
            score = individual_scores.get(name, result.get('score', 0)) if individual_scores else result.get('score', 0)
            weight = self.get_weight(name)
            weighted_contribution = score * weight

            weighted_sum += weighted_contribution
            weight_total += weight

            contributions.append({
                'detector': name,
                'raw_score': round(score, 3),
                'weight': round(weight, 3),
                'contribution': round(weighted_contribution, 3),
            })

            if weighted_contribution > best_detector[1]:
                best_detector = (name, weighted_contribution)

        ensemble_score = weighted_sum / max(weight_total, 0.001)
        ensemble_score = max(0.0, min(1.0, ensemble_score / 5.0))

        return {
            'ensemble_score': round(ensemble_score, 4),
            'weighted_sum': round(weighted_sum, 2),
            'active_detectors': len(contributions),
            'best_detector': best_detector[0],
            'contributions': contributions,
            'ml_enhanced': True,
        }

    def feedback(self, detector_name: str, was_correct: bool):
        """
        绩效反馈：更新检测器权重

        调用时机：回测或实盘确认后
        """
        self._init_detector(detector_name)
        self.performance[detector_name].append(1.0 if was_correct else 0.0)

        # 计算近期命中率
        perf = list(self.performance[detector_name])
        if len(perf) >= 5:
            hit_rate = sum(perf) / len(perf)
            # 动态调整：命中率高→加权重，低→减权重
            adjustment = (hit_rate - 0.5) * self.config.learning_rate * 2
            self.weights[detector_name] = max(
                self.config.min_weight,
                min(self.config.max_weight,
                    self.weights[detector_name] + adjustment)
            )

    def get_active_detectors(self, min_weight: float = 0.5) -> List[str]:
        """获取当前活跃（权重>阈值）的检测器"""
        return [name for name, w in self.weights.items() if w >= min_weight]

    def get_weight_report(self) -> dict:
        """权重报告"""
        items = []
        for name in sorted(self.weights.keys()):
            perf = list(self.performance.get(name, []))
            hit_rate = sum(perf) / len(perf) if perf else 0.5
            items.append({
                'detector': name,
                'weight': round(self.weights[name], 3),
                'samples': len(perf),
                'recent_hit_rate': round(hit_rate, 3),
            })
        return {
            'detectors': len(items),
            'active': len([x for x in items if x['weight'] >= 0.5]),
            'items': items,
        }


# ═══════════════════════════════════════════════
# 3. 自适应阈值校准器
# ═══════════════════════════════════════════════

class AdaptiveThresholdCalibrator:
    """
    从回测/实盘数据学习最优阈值

    核心思路：
    - 传统阈值是硬编码的（如涨幅3-5%、换手5-10%）
    - ML辅助：根据近期市场风格自适应调整阈值范围
    - 乐观市场→放宽阈值（更多候选）
    - 悲观市场→收紧阈值（更少但更精）

    输入：近期成功/失败选股的特征分布
    输出：调整后的阈值建议
    """

    # 可调整的阈值参数及其默认值
    ADJUSTABLE_THRESHOLDS = {
        'gain_min': 0.03,       # 最低涨幅
        'gain_max': 0.05,       # 最高涨幅
        'turnover_min': 0.05,   # 最低换手率
        'turnover_max': 0.10,   # 最高换手率
        'volume_ratio_min': 1.2,# 最低量比
        'volume_ratio_max': 2.5,# 最高量比
        'market_cap_max': 2e11, # 最高市值
        'tail_volume_ratio': 0.25, # 尾盘占比
        'buy_score_min': 0.50,  # 最低买入分
    }

    def __init__(self):
        self.successful_features: deque = deque(maxlen=50)
        self.failed_features: deque = deque(maxlen=50)
        self.adjustment_history: deque = deque(maxlen=30)

    def record_outcome(self, features: dict, was_success: bool):
        """记录选股结果"""
        target = self.successful_features if was_success else self.failed_features
        target.append(features)

    def calibrate(self, market_mood: str = 'neutral') -> dict:
        """
        根据近期数据校准阈值

        参数：
          market_mood: 'bullish'/'neutral'/'bearish'

        返回：
          {调整后的阈值字典, 调整方向, 置信度}
        """
        successful = list(self.successful_features)
        failed = list(self.failed_features)

        adjustments = {}
        confidence = 1.0

        # 样本量检查
        if len(successful) < 5 or len(failed) < 5:
            return {
                'thresholds': dict(self.ADJUSTABLE_THRESHOLDS),
                'adjustment': 'insufficient_data',
                'confidence': 0.3,
                'detail': f'成功样本{len(successful)}/失败样本{len(failed)}不足',
            }

        for key, default in self.ADJUSTABLE_THRESHOLDS.items():
            # 提取成功/失败样本的特征值
            success_vals = [f.get(key, default) for f in successful if key in f]
            fail_vals = [f.get(key, default) for f in failed if key in f]

            if len(success_vals) < 3 or len(fail_vals) < 3:
                adjustments[key] = default
                continue

            # 区分"向下收紧"和"向上放宽"的参数
            # 下限参数（min_）→ 成功值低则提高下限（收紧）
            # 上限参数（max_）→ 成功值低则降低上限（收紧）
            avg_success = sum(success_vals) / len(success_vals)

            if key.endswith('_min') or key == 'tail_volume_ratio':
                # 下限：成功值高 → 保持；成功值低 → 提高门槛
                adjusted = avg_success * 1.1 if 'min' in key else avg_success
                adjustments[key] = round(max(default * 0.8, min(default * 1.3, adjusted)), 4)
            elif key.endswith('_max'):
                # 上限：成功值低 → 收紧上限
                adjusted = avg_success * 1.05
                adjustments[key] = round(max(default * 0.7, min(default * 1.2, adjusted)), 4)
            else:
                adjustments[key] = round(default, 4)

        # 市场情绪修正
        mood_factor = {'bullish': 1.15, 'neutral': 1.0, 'bearish': 0.85}.get(market_mood, 1.0)
        for key in adjustments:
            if key.endswith('_min'):
                adjustments[key] = round(adjustments[key] * (1.0 / mood_factor), 4)
            elif key.endswith('_max'):
                adjustments[key] = round(adjustments[key] * mood_factor, 4)

        # 样本质量
        confidence = min(1.0, (len(successful) + len(failed)) / 30)

        result = {
            'thresholds': adjustments,
            'adjustment': 'calibrated',
            'confidence': round(confidence, 3),
            'market_mood': market_mood,
            'samples': f'{len(successful)}成功/{len(failed)}失败',
        }

        self.adjustment_history.append(result)
        return result

    def get_current_thresholds(self) -> dict:
        """获取最近校准后的阈值（如果没有则返回默认）"""
        if self.adjustment_history:
            return self.adjustment_history[-1].get('thresholds', dict(self.ADJUSTABLE_THRESHOLDS))
        return dict(self.ADJUSTABLE_THRESHOLDS)


# ═══════════════════════════════════════════════
# 4. 板块轮动检测器
# ═══════════════════════════════════════════════

class SectorRotationDetector:
    """
    板块轮动识别

    通过增量相关分析检测资金在板块间的流动方向。
    帮助识别"谁的动量在加速"和"谁的动量在衰减"。
    """

    def __init__(self, window: int = 10):
        self.window = window
        self.sector_prices: Dict[str, deque] = {}  # sector → 价格滑动窗口
        self.rotation_signals: deque = deque(maxlen=20)

    def update_sector(self, sector: str, price: float):
        """更新板块价格"""
        if sector not in self.sector_prices:
            self.sector_prices[sector] = deque(maxlen=self.window)
        self.sector_prices[sector].append(price)

    def update_batch(self, sector_prices: Dict[str, float]):
        """批量更新"""
        for sector, price in sector_prices.items():
            self.update_sector(sector, price)

    def detect_rotation(self) -> dict:
        """
        检测当前板块轮动方向

        比较各板块近期动量，找出加速和衰减的板块
        """
        if len(self.sector_prices) < 2:
            return {'rotation_detected': False, 'reason': 'insufficient_sectors'}

        momentums = {}
        for sector, prices in self.sector_prices.items():
            if len(prices) < 3:
                continue
            vals = list(prices)
            # 简单动量：最新/最旧 - 1
            momentum = (vals[-1] / vals[0] - 1) if vals[0] > 0 else 0.0
            momentums[sector] = momentum

        if len(momentums) < 2:
            return {'rotation_detected': False, 'reason': 'insufficient_data'}

        # 排名
        ranked = sorted(momentums.items(), key=lambda x: x[1], reverse=True)

        top_3 = ranked[:3]
        bottom_3 = ranked[-3:]

        # 如果前3的动量远大于后3 → 轮动明确
        rotation_strength = (sum(x[1] for x in top_3) - sum(x[1] for x in bottom_3)) / max(len(top_3), 1)

        return {
            'rotation_detected': abs(rotation_strength) > 0.02,
            'rotation_strength': round(rotation_strength, 4),
            'leading_sectors': [{'sector': s, 'momentum': round(m, 4)} for s, m in top_3],
            'lagging_sectors': [{'sector': s, 'momentum': round(m, 4)} for s, m in bottom_3],
            'all_momentums': {s: round(m, 4) for s, m in momentums.items()},
            'interpretation': (
                f'资金流向 {top_3[0][0]}（{top_3[0][1]:+.2%}），'
                f'流出 {bottom_3[0][0]}（{bottom_3[0][1]:+.2%}）'
            ) if ranked else '',
        }

    def get_rotation_score(self, sector: str) -> float:
        """获取某板块的轮动评分（0-1），用于注入7权重融合引擎"""
        rotation = self.detect_rotation()
        if not rotation.get('rotation_detected'):
            return 0.5

        leading = {s['sector'] for s in rotation.get('leading_sectors', [])}
        lagging = {s['sector'] for s in rotation.get('lagging_sectors', [])}

        if sector in leading:
            return 0.75 + 0.25 * min(1.0, rotation['rotation_strength'] * 5)
        elif sector in lagging:
            return 0.25 - 0.25 * min(1.0, abs(rotation['rotation_strength']) * 5)
        return 0.5


# ═══════════════════════════════════════════════
# 5. 异常检测器
# ═══════════════════════════════════════════════

class AnomalyScorer:
    """
    异常检测

    使用Z-score方法检测价格/成交量的异常波动，
    辅助识别潜在的"假突破"或"诱多陷阱"。
    """

    def __init__(self, window: int = 20, z_threshold: float = 3.0):
        self.window = window
        self.z_threshold = z_threshold
        self.price_history: Dict[str, deque] = {}
        self.vol_history: Dict[str, deque] = {}

    def _compute_zscore(self, value: float, history: List[float]) -> float:
        """计算Z-score"""
        if len(history) < 5:
            return 0.0
        mean = sum(history) / len(history)
        variance = sum((x - mean) ** 2 for x in history) / len(history)
        std = math.sqrt(variance)
        if std == 0:
            return 0.0
        return (value - mean) / std

    def check_anomaly(self, code: str, price: float, volume: float = None) -> dict:
        """
        检查价格/成交量是否异常

        返回：
          {is_anomalous, price_z, volume_z, anomaly_type, risk_level}
        """
        # 更新历史
        if code not in self.price_history:
            self.price_history[code] = deque(maxlen=self.window)
        if volume and code not in self.vol_history:
            self.vol_history[code] = deque(maxlen=self.window)

        p_history = list(self.price_history[code])
        v_history = list(self.vol_history.get(code, [])) if volume else []

        # 计算Z-scores
        price_z = self._compute_zscore(price, p_history) if len(p_history) >= 5 else 0.0
        volume_z = self._compute_zscore(volume, v_history) if volume and len(v_history) >= 5 else 0.0

        # 更新
        self.price_history[code].append(price)
        if volume:
            self.vol_history[code].append(volume)

        # 判断
        is_anomalous = abs(price_z) > self.z_threshold
        if volume and abs(volume_z) > self.z_threshold:
            is_anomalous = True

        # 异常类型
        anomaly_type = 'normal'
        if abs(price_z) > self.z_threshold:
            anomaly_type = 'price_spike' if price_z > 0 else 'price_crash'
        elif abs(volume_z) > self.z_threshold:
            anomaly_type = 'volume_surge' if volume_z > 0 else 'volume_drop'

        # 风险等级
        risk_level = 'low'
        if abs(price_z) > 4.0 or abs(volume_z) > 4.0:
            risk_level = 'high'
        elif is_anomalous:
            risk_level = 'medium'

            # 量价背离：价格涨但量缩 → 高风险的诱多信号
        if price_z > 1.5 and volume_z < -1.0:
            anomaly_type = 'bull_trap_signal'
            risk_level = 'high'

        return {
            'code': code,
            'is_anomalous': is_anomalous,
            'price_z': round(price_z, 3),
            'volume_z': round(volume_z, 3),
            'anomaly_type': anomaly_type,
            'risk_level': risk_level,
            'confidence': round(min(1.0, abs(price_z) / 5.0 + abs(volume_z) / 5.0), 3),
        }


# ═══════════════════════════════════════════════
# ML引擎门面
# ═══════════════════════════════════════════════

class MLEngine:
    """
    ML智能辅助引擎门面

    统一调度所有ML子模块，为V13.0流水线提供增强信号。
    """

    def __init__(self):
        self.momentum = IndustryMomentumPredictor()
        self.ensemble = PatternEnsembleScorer()
        self.threshold_calibrator = AdaptiveThresholdCalibrator()
        self.rotation = SectorRotationDetector()
        self.anomaly = AnomalyScorer()

    def enhance_layer2(self, stock: dict) -> dict:
        """
        Layer 2 增强：注入ML信号到形态检测

        返回：
          {ml_enhanced, momentum_signal, anomaly_check, rotation_score}
        """
        code = stock.get('code', 'unknown')
        prices = stock.get('prices', [])
        volumes = stock.get('volumes', None)

        # 动量预测
        momentum_signal = None
        if len(prices) >= 10:
            momentum_signal = self.momentum.predict_industry_momentum(code, prices, volumes)

        # 异常检测
        anomaly_check = None
        if len(prices) >= 2:
            current_price = prices[-1]
            current_vol = volumes[-1] if volumes else None
            anomaly_check = self.anomaly.check_anomaly(code, current_price, current_vol)

        # 行业轮动分
        sector = stock.get('sector', '')
        rotation_score = self.rotation.get_rotation_score(sector) if sector else 0.5

        # 综合ML增强信号
        ml_enhancement = 0.0
        if momentum_signal and momentum_signal.get('ml_signal'):
            ml_enhancement += 0.15
        if anomaly_check and anomaly_check['risk_level'] == 'high':
            ml_enhancement -= 0.20
        if anomaly_check and anomaly_check.get('anomaly_type') == 'bull_trap_signal':
            ml_enhancement -= 0.25
        ml_enhancement += (rotation_score - 0.5) * 0.2

        return {
            'ml_enhanced': True,
            'ml_enhancement': round(ml_enhancement, 4),
            'momentum_signal': momentum_signal,
            'anomaly_check': anomaly_check,
            'rotation_score': round(rotation_score, 3),
        }

    def enhance_layer4(self, pattern_results: dict) -> dict:
        """
        Layer 4 增强：集成学习加权投票

        返回：
          {ensemble_score, weighted_contributions}
        """
        return self.ensemble.compute_ensemble_score(pattern_results)

    def calibrate_thresholds(self, market_mood: str = 'neutral') -> dict:
        """校准阈值"""
        return self.threshold_calibrator.calibrate(market_mood)

    def record_feedback(self, detector_name: str, was_correct: bool):
        """记录检测器反馈"""
        self.ensemble.feedback(detector_name, was_correct)

    def update_market_data(self, sector_prices: Dict[str, float],
                           features: dict = None, was_success: bool = None):
        """更新市场数据"""
        self.rotation.update_batch(sector_prices)
        if features and was_success is not None:
            self.threshold_calibrator.record_outcome(features, was_success)


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 60)
    print("V13.0 ML智能辅助引擎 — 自测")
    print("=" * 60)

    # 模拟价格数据
    import random
    random.seed(42)

    # 上升趋势
    uptrend = [100.0 + i * 0.5 + random.uniform(-1, 2) for i in range(30)]
    # 盘整
    flat = [100.0 + random.uniform(-1.5, 1.5) for _ in range(30)]
    # 下降趋势
    downtrend = [100.0 - i * 0.3 + random.uniform(-1.5, 1) for i in range(30)]

    volumes = [100_000 + random.uniform(-20000, 50000) for _ in range(30)]

    # ── 1. 动量预测 ──
    print("\n📈 1. 行业动量预测")
    for name, data in [('上升', uptrend), ('盘整', flat), ('下降', downtrend)]:
        mp = IndustryMomentumPredictor()
        result = mp.predict_industry_momentum(name, data, volumes)
        print(f"  {name}: 方向={result['direction']}, 强度={result['strength']:.3f}, "
              f"置信={result['confidence']:.3f}, R²={result['trend_r2']:.3f}, "
              f"ML信号={'✅' if result['ml_signal'] else '❌'}")

    # ── 2. 集成分数 ──
    print("\n📊 2. 形态集成分数")
    es = PatternEnsembleScorer()
    # 模拟检测结果
    mock_patterns = {
        '老鸭头': {'score': 0.8, 'grade': 'strong'},
        '擒龙战法': {'score': 0.4, 'grade': 'weak'},
        '主力信号': {'score': 0.9, 'grade': 'strong'},
        '2560战法': {'score': 0.6, 'grade': 'medium'},
        '二板定龙头': {'score': 0.3, 'grade': 'none'},
        '暗盘资金': {'score': 0.7, 'grade': 'medium'},
    }
    ensemble = es.compute_ensemble_score(mock_patterns)
    print(f"  集成分数={ensemble['ensemble_score']:.3f}, 最佳检测器={ensemble['best_detector']}")

    # 模拟反馈
    for _ in range(20):
        es.feedback('老鸭头', random.random() > 0.3)
        es.feedback('主力信号', random.random() > 0.4)
        es.feedback('2560战法', random.random() > 0.5)
    report = es.get_weight_report()
    print(f"  活跃检测器: {report['active']}/{report['detectors']}")
    for item in report['items'][:3]:
        print(f"    {item['detector']}: w={item['weight']:.3f}, hit={item['recent_hit_rate']:.2f}")

    # ── 3. 阈值校准 ──
    print("\n🎯 3. 自适应阈值校准")
    tc = AdaptiveThresholdCalibrator()
    # 模拟成功样本
    for _ in range(15):
        tc.record_outcome({
            'gain_min': 0.035, 'gain_max': 0.048, 'turnover_min': 0.06,
            'volume_ratio_min': 1.3, 'tail_volume_ratio': 0.28,
        }, True)
    # 模拟失败样本
    for _ in range(10):
        tc.record_outcome({
            'gain_min': 0.02, 'gain_max': 0.06, 'turnover_min': 0.03,
            'volume_ratio_min': 0.8, 'tail_volume_ratio': 0.15,
        }, False)
    calib = tc.calibrate('bullish')
    print(f"  调整方向={calib['adjustment']}, 置信度={calib['confidence']:.2f}")
    for k, v in list(calib['thresholds'].items())[:4]:
        default = AdaptiveThresholdCalibrator.ADJUSTABLE_THRESHOLDS.get(k, 0)
        print(f"    {k}: {default:.4f} → {v:.4f} ({'宽松' if v > default else '收紧'})")

    # ── 4. 板块轮动 ──
    print("\n🔄 4. 板块轮动检测")
    srd = SectorRotationDetector()
    for day in range(10):
        srd.update_batch({
            'AI算力': 100 + day * 0.8,
            '机器人': 100 + day * 0.2,
            '新能源': 100 - day * 0.2,
            '医药': 100 + day * 0.1,
            '有色': 100 - day * 0.5,
        })
    rotation = srd.detect_rotation()
    print(f"  轮动检测={'✅' if rotation['rotation_detected'] else '❌'}, 强度={rotation['rotation_strength']:.4f}")
    for s in rotation['leading_sectors'][:2]:
        print(f"  领涨: {s['sector']} ({s['momentum']:+.3%})")
    print(f"  解读: {rotation['interpretation']}")

    # ── 5. 异常检测 ──
    print("\n⚠ 5. 异常检测")
    anom = AnomalyScorer()
    for _ in range(20):
        anom.check_anomaly('test', 100.0 + random.uniform(-1, 1), 100000.0)

    # 正常
    r1 = anom.check_anomaly('test', 101.0, 105000.0)
    print(f"  正常波动: 异常={'⚠' if r1['is_anomalous'] else '✅'}, z={r1['price_z']:.2f}")

    # 极端
    r2 = anom.check_anomaly('test', 115.0, 5000.0)
    print(f"  极端+缩量: 类型={r2['anomaly_type']}, 风险={r2['risk_level']}, z={r2['price_z']:.2f}")

    # ── 6. 综合引擎 ──
    print("\n🧠 6. ML引擎综合")
    engine = MLEngine()
    stock = {
        'code': '002415', 'name': '海康威视',
        'prices': uptrend, 'volumes': volumes,
        'sector': 'AI算力',
    }
    enhancement = engine.enhance_layer2(stock)
    print(f"  ML增强分={enhancement['ml_enhancement']:.3f}")
    if enhancement['momentum_signal']:
        print(f"  动量方向={enhancement['momentum_signal']['direction']}")
    print(f"  异常风险={enhancement['anomaly_check']['risk_level']}")
    print(f"  轮动分={enhancement['rotation_score']:.3f}")

    print("\n" + "=" * 60)
    print("✅ ML引擎全5子模块自检通过")
    print("=" * 60)
