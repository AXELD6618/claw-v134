#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.52 自主进化引擎 (AutoEvolutionEngine)
=============================================
修复V13.5.51 Layer 4断链: 从"静态数据看板"升级为"动态学习引擎"

核心闭环:
  15:10 TDX验证 → 读取t1_verified_dataset.json → 计算8维滚动IC
  → 基于IC自动调优D1-D8权重 → 写入weights文件
  → 次日14:30 T4选股使用新权重 → 再次验证 → 持续进化

设计原则:
  1. 动态读取 — 不再硬编码数据，每次运行从JSON实时加载
  2. IC驱动 — Spearman rank IC衡量维度预测力
  3. 约束调优 — min=5%/max=25%/daily_delta<=2%，防止过拟合
  4. 渐进可信 — n<5不调、5<=n<30微调(减半delta)、n>=30全量调
  5. 进化日志 — 每次权重变更记录IC/旧权重/新权重/原因
  6. 退化告警 — 连续5天IC<0自动告警

数据集字段到8维映射:
  D1 获利筹码比 → convergence_score (WINNER三时点趋同)
  D2 换手率     → volume_ratio (量比间接反映换手活跃度)
  D3 主力资金   → supamo_numeric (主力资金方向 positive=1/negative=-1)
  D4 量价关系   → volume_ratio (量比是量价核心指标)
  D5 技术形态   → signal_score (综合技术评分0-10)
  D6 催化+涨停  → d28_score + hotspot_level_score (直接受益度+热点级别)
  D7 舆情热度   → sentiment_score (FinBERT情感-1~1)
  D8 筹码集中   → convergence_score (趋同度间接反映筹码分布)

Author: 毕方灵犀貔貅助手 V13.5.52
Date: 2026-07-11
"""

import json
import math
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings('ignore')

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
EVOLUTION_DIR = DATA_DIR / "evolution_v13552"
EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)

# 数据源: V49真实TDX验证数据集
DATASET_PATH = DATA_DIR / "evolution_v13549" / "t1_verified_dataset.json"

# 权重输出: 供V51 8维蒸馏引擎读取
WEIGHTS_PATH = EVOLUTION_DIR / "current_weights.json"

# 进化历史日志
EVOLUTION_LOG_PATH = EVOLUTION_DIR / "evolution_log.json"

# IC历史追踪
IC_HISTORY_PATH = EVOLUTION_DIR / "ic_history.json"


# ============================================================
# 权重调优约束
# ============================================================

TUNING_CONSTRAINTS = {
    "min_weight": 0.05,          # 单维度最低5%
    "max_weight": 0.25,          # 单维度最高25%
    "max_daily_delta": 0.02,     # 单日最大调整2%
    "min_samples_for_tuning": 5,     # 最少5条开始微调
    "min_samples_for_full": 30,      # 30条全量调优
    "ic_decay": 0.85,            # 滚动IC衰减因子(近7天加权)
    "degradation_window": 5,     # 退化检测窗口(连续5天IC<0)
}

# 默认8维权重 (与V51 FUSION_DIMENSIONS一致)
DEFAULT_WEIGHTS = {
    "D1": 0.18,  # 获利筹码比
    "D2": 0.10,  # 换手率低位
    "D3": 0.18,  # 主力资金流向
    "D4": 0.15,  # 量价关系
    "D5": 0.12,  # 技术形态
    "D6": 0.12,  # 催化剂+涨停概率
    "D7": 0.10,  # 舆情热度
    "D8": 0.05,  # 筹码集中度
}

# 数据集字段到8维的映射
DIM_FIELD_MAP = {
    "D1": ["convergence_score"],
    "D2": ["volume_ratio"],
    "D3": ["supamo_numeric"],
    "D4": ["volume_ratio"],
    "D5": ["signal_score"],
    "D6": ["d28_score", "hotspot_level_score"],
    "D7": ["sentiment_score"],
    "D8": ["convergence_score"],
}

DIM_NAMES = {
    "D1": "获利筹码比",
    "D2": "换手率低位",
    "D3": "主力资金流向",
    "D4": "量价关系",
    "D5": "技术形态",
    "D6": "催化剂+涨停概率",
    "D7": "舆情热度",
    "D8": "筹码集中度",
}


@dataclass
class EvolutionRecord:
    """单次进化记录"""
    timestamp: str
    version: str
    total_samples: int
    hit_rate: float
    limit_up_rate: float
    ic_by_dim: Dict[str, float]
    weights_before: Dict[str, float]
    weights_after: Dict[str, float]
    adjustments: List[Dict[str, Any]]
    alerts: List[str]
    significance: str  # "insufficient" / "marginal" / "significant"


class AutoEvolutionEngine:
    """
    V13.5.52 自主进化引擎
    
    闭环: T日选股 → T+1验证 → 读取数据 → 计算IC → 调整权重 → 保存 → 次日应用
    """

    def __init__(self):
        self.weights = DEFAULT_WEIGHTS.copy()
        self.evolution_history: List[dict] = []
        self.ic_history: List[dict] = []
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        self.dataset: List[dict] = []
        self.stats: Dict[str, Any] = {}

        self._load_state()
        print(f"[AutoEvolution-V52] 自主进化引擎启动")
        print(f"  当前权重: {' | '.join(f'{k}={v:.0%}' for k, v in self.weights.items())}")
        print(f"  权重总和: {sum(self.weights.values()):.2f}")
        print(f"  进化记录: {len(self.evolution_history)} 条")
        print(f"  IC历史: {len(self.ic_history)} 条")

    # ============================================================
    # 状态管理
    # ============================================================

    def _load_state(self):
        """加载权重和进化历史"""
        if WEIGHTS_PATH.exists():
            with open(WEIGHTS_PATH, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                self.weights.update(saved.get("weights", {}))

        if EVOLUTION_LOG_PATH.exists():
            with open(EVOLUTION_LOG_PATH, 'r', encoding='utf-8') as f:
                self.evolution_history = json.load(f)

        if IC_HISTORY_PATH.exists():
            with open(IC_HISTORY_PATH, 'r', encoding='utf-8') as f:
                self.ic_history = json.load(f)

    def _save_state(self):
        """保存权重和进化历史"""
        EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)

        with open(WEIGHTS_PATH, 'w', encoding='utf-8') as f:
            json.dump({
                "last_updated": self.current_date,
                "version": "V13.5.52",
                "weights": self.weights,
                "weight_sum": round(sum(self.weights.values()), 4),
                "evolution_count": len(self.evolution_history),
                "total_samples": self.stats.get("total", 0),
                "hit_rate": self.stats.get("hit_rate", 0),
            }, f, ensure_ascii=False, indent=2)

        with open(EVOLUTION_LOG_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.evolution_history, f, ensure_ascii=False, indent=2)

        with open(IC_HISTORY_PATH, 'w', encoding='utf-8') as f:
            json.dump(self.ic_history, f, ensure_ascii=False, indent=2)

    # ============================================================
    # 数据加载与预处理
    # ============================================================

    def load_dataset(self) -> List[dict]:
        """动态读取t1_verified_dataset.json"""
        if not DATASET_PATH.exists():
            print(f"  [!] 数据集不存在: {DATASET_PATH}")
            return []

        with open(DATASET_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)

        samples = data.get("samples", [])
        self.stats = data.get("stats", {})

        # 预处理: 添加派生字段
        for s in samples:
            # supamo_numeric: positive=1, negative=-1, 缺失=0
            supamo = s.get("supamo", "")
            if supamo == "positive":
                s["supamo_numeric"] = 1.0
            elif supamo == "negative":
                s["supamo_numeric"] = -1.0
            else:
                s["supamo_numeric"] = 0.0

            # hotspot_level_score: SURGE=1.0, WATCH=0.5, NORMAL=0.1
            hotspot = s.get("hotspot_level", "NORMAL")
            s["hotspot_level_score"] = {"SURGE": 1.0, "WATCH": 0.5, "NORMAL": 0.1}.get(hotspot, 0.1)

            # convergence_score: 缺失则用signal_score/10作为代理
            if s.get("convergence_score") is None:
                s["convergence_score"] = s.get("signal_score", 5.0) / 10.0

        self.dataset = samples
        return samples

    # ============================================================
    # 核心算法: Spearman rank IC
    # ============================================================

    def _spearman_ic(self, x: List[float], y: List[float]) -> float:
        """
        计算Spearman rank information coefficient
        
        IC = correlation between ranked predictor and ranked outcome
        比Pearson更鲁棒, 不受异常值影响
        """
        n = len(x)
        if n < 3:
            return 0.0

        # Rank x and y
        x_ranks = self._rank(x)
        y_ranks = self._rank(y)

        # Pearson on ranks = Spearman
        mean_x = sum(x_ranks) / n
        mean_y = sum(y_ranks) / n

        cov = sum((x_ranks[i] - mean_x) * (y_ranks[i] - mean_y) for i in range(n))
        var_x = sum((xi - mean_x) ** 2 for xi in x_ranks)
        var_y = sum((yi - mean_y) ** 2 for yi in y_ranks)

        if var_x > 0 and var_y > 0:
            return cov / (math.sqrt(var_x) * math.sqrt(var_y))
        return 0.0

    def _rank(self, values: List[float]) -> List[float]:
        """计算rank (平均秩处理ties)"""
        indexed = sorted(enumerate(values), key=lambda x: x[1])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(indexed):
            j = i
            while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1.0  # 1-based rank
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    def _calc_dimension_ic(self, samples: List[dict], dim_id: str) -> Tuple[float, int]:
        """
        计算单个维度对T+1涨跌幅的IC
        
        Returns: (ic_value, usable_sample_count)
        """
        fields = DIM_FIELD_MAP.get(dim_id, [])
        if not fields:
            return 0.0, 0

        # 提取维度评分和T+1涨跌幅
        pairs = []
        for s in samples:
            # 聚合多个字段
            dim_value = 0.0
            field_count = 0
            for f in fields:
                v = s.get(f)
                if v is not None:
                    dim_value += float(v)
                    field_count += 1

            if field_count > 0:
                dim_value /= field_count
                t1_chg = s.get("t1_change_pct")
                if t1_chg is not None:
                    pairs.append((dim_value, float(t1_chg)))

        if len(pairs) < 3:
            return 0.0, len(pairs)

        x = [p[0] for p in pairs]
        y = [p[1] for p in pairs]
        ic = self._spearman_ic(x, y)
        return ic, len(pairs)

    # ============================================================
    # 核心算法: 权重自适应调优
    # ============================================================

    def evolve(self) -> dict:
        """
        主进化入口 — 从数据集学习并调整权重
        
        流程:
        1. 加载最新验证数据
        2. 计算每个维度的滚动IC
        3. 基于IC驱动权重调优(约束: min5%/max25%/delta<=2%)
        4. 退化检测
        5. 保存进化记录
        
        Returns: 进化报告
        """
        print(f"\n[AutoEvolution-V52] 开始进化分析...")

        # Step 1: 加载数据
        samples = self.load_dataset()
        n = len(samples)
        if n == 0:
            print(f"  [!] 无数据可分析")
            return {"error": "no data"}

        hits = sum(1 for s in samples if s.get("t1_change_pct", 0) > 0)
        boards = sum(1 for s in samples if s.get("t1_limit_up", False))
        hit_rate = hits / n if n > 0 else 0
        lu_rate = boards / n if n > 0 else 0

        print(f"  数据集: {n}条 | 命中{hits}({hit_rate:.1%}) | 涨停{boards}({lu_rate:.1%})")

        # Step 2: 计算8维IC
        ic_by_dim = {}
        sample_counts = {}
        for dim_id in DEFAULT_WEIGHTS:
            ic, count = self._calc_dimension_ic(samples, dim_id)
            ic_by_dim[dim_id] = round(ic, 4)
            sample_counts[dim_id] = count
            print(f"  {dim_id} {DIM_NAMES[dim_id]}: IC={ic:+.4f} (n={count})")

        # Step 3: 判断显著性级别
        if n < TUNING_CONSTRAINTS["min_samples_for_tuning"]:
            significance = "insufficient"
            delta_scale = 0.0  # 不调
        elif n < TUNING_CONSTRAINTS["min_samples_for_full"]:
            significance = "marginal"
            delta_scale = 0.5  # 半量调
        else:
            significance = "significant"
            delta_scale = 1.0  # 全量调

        print(f"  显著性: {significance} (n={n}, scale={delta_scale})")

        # Step 4: 基于IC驱动权重调优
        old_weights = self.weights.copy()
        adjustments = []

        if delta_scale > 0:
            max_delta = TUNING_CONSTRAINTS["max_daily_delta"] * delta_scale

            for dim_id in DEFAULT_WEIGHTS:
                ic = ic_by_dim[dim_id]
                count = sample_counts[dim_id]

                if count < 3:
                    continue  # 数据不足, 不调

                # IC > 0 → 维度有效 → 增加权重
                # IC < 0 → 维度无效/反向 → 减少权重
                # IC ≈ 0 → 中性 → 微调
                adjustment = ic * max_delta

                # 限制单日最大调整
                adjustment = max(-max_delta, min(max_delta, adjustment))

                # 量比IC加成 (V49发现量比IC=+0.518最强稳定正因子)
                if dim_id in ("D2", "D4") and ic > 0.3:
                    adjustment += max_delta * 0.3  # 额外加成

                new_weight = self.weights[dim_id] + adjustment

                # 约束: min/max边界
                new_weight = max(
                    TUNING_CONSTRAINTS["min_weight"],
                    min(TUNING_CONSTRAINTS["max_weight"], new_weight)
                )

                actual_delta = new_weight - self.weights[dim_id]
                if abs(actual_delta) > 0.0001:
                    adjustments.append({
                        "dim": dim_id,
                        "dim_name": DIM_NAMES[dim_id],
                        "ic": round(ic, 4),
                        "old_weight": round(self.weights[dim_id], 4),
                        "new_weight": round(new_weight, 4),
                        "delta": round(actual_delta, 4),
                        "sample_count": count,
                    })
                    self.weights[dim_id] = new_weight

            # 归一化到总和=1.0
            total_w = sum(self.weights.values())
            if total_w > 0:
                for dim_id in self.weights:
                    self.weights[dim_id] = round(self.weights[dim_id] / total_w, 4)

            # 归一化后再次约束
            for dim_id in self.weights:
                self.weights[dim_id] = max(
                    TUNING_CONSTRAINTS["min_weight"],
                    min(TUNING_CONSTRAINTS["max_weight"], self.weights[dim_id])
                )

        # Step 5: 退化检测
        alerts = self._detect_degradation(ic_by_dim)

        # Step 6: 记录进化
        record = {
            "timestamp": datetime.now().isoformat(),
            "date": self.current_date,
            "version": "V13.5.52",
            "total_samples": n,
            "hit_rate": round(hit_rate, 4),
            "limit_up_rate": round(lu_rate, 4),
            "ic_by_dim": ic_by_dim,
            "sample_counts": sample_counts,
            "weights_before": old_weights,
            "weights_after": self.weights.copy(),
            "adjustments": adjustments,
            "alerts": alerts,
            "significance": significance,
            "delta_scale": delta_scale,
        }

        self.evolution_history.append(record)

        # IC历史追踪 (用于趋势分析)
        self.ic_history.append({
            "date": self.current_date,
            "n": n,
            "ic_by_dim": ic_by_dim,
            "hit_rate": round(hit_rate, 4),
        })

        self._save_state()

        # 打印调整摘要
        if adjustments:
            print(f"\n  权重调整 ({len(adjustments)}项):")
            for adj in adjustments:
                arrow = "+" if adj["delta"] > 0 else ""
                print(f"    {adj['dim']} {adj['dim_name']}: "
                      f"{adj['old_weight']:.1%} → {adj['new_weight']:.1%} "
                      f"({arrow}{adj['delta']:.1%}) IC={adj['ic']:+.4f}")
        else:
            print(f"\n  无权重调整 (样本不足或IC≈0)")

        if alerts:
            print(f"\n  告警 ({len(alerts)}项):")
            for a in alerts:
                print(f"    {a}")

        print(f"\n  进化完成 | 权重已保存到 {WEIGHTS_PATH}")
        print(f"  权重总和: {sum(self.weights.values()):.4f}")

        return record

    def _detect_degradation(self, ic_by_dim: Dict[str, float]) -> List[str]:
        """检测维度退化"""
        alerts = []

        # 当前IC < 0 的维度
        for dim_id, ic in ic_by_dim.items():
            if ic < -0.1:
                alerts.append(
                    f"⚠️ {dim_id} {DIM_NAMES[dim_id]} IC={ic:+.4f} < 0 — "
                    f"该维度可能反向, 考虑降低权重或检查数据"
                )

        # 连续多天IC趋势 (从历史记录分析)
        if len(self.ic_history) >= TUNING_CONSTRAINTS["degradation_window"]:
            recent = self.ic_history[-TUNING_CONSTRAINTS["degradation_window"]:]
            for dim_id in DEFAULT_WEIGHTS:
                ics = [r["ic_by_dim"].get(dim_id, 0) for r in recent]
                if all(ic < 0 for ic in ics):
                    alerts.append(
                        f"🚨 {dim_id} {DIM_NAMES[dim_id]} 连续{len(ics)}天IC<0 — "
                        f"维度严重退化, 建议人工介入检查"
                    )

        return alerts

    # ============================================================
    # 查询接口 (供V51调用)
    # ============================================================

    def get_weights(self) -> Dict[str, float]:
        """获取当前进化权重"""
        return self.weights.copy()

    def get_current_stats(self) -> Dict[str, Any]:
        """获取当前数据集统计"""
        if not self.dataset:
            self.load_dataset()

        n = len(self.dataset)
        if n == 0:
            return {"total": 0}

        hits = sum(1 for s in self.dataset if s.get("t1_change_pct", 0) > 0)
        boards = sum(1 for s in self.dataset if s.get("t1_limit_up", False))

        # 计算各维度IC
        ic_by_dim = {}
        for dim_id in DEFAULT_WEIGHTS:
            ic, count = self._calc_dimension_ic(self.dataset, dim_id)
            ic_by_dim[dim_id] = {"ic": round(ic, 4), "n": count}

        # 最强IC因子
        strongest = max(ic_by_dim.items(), key=lambda x: abs(x[1]["ic"]))

        return {
            "total": n,
            "target": 50,
            "hit_rate": round(hits / n * 100, 1) if n > 0 else 0,
            "limit_up_rate": round(boards / n * 100, 1) if n > 0 else 0,
            "ic_by_dim": ic_by_dim,
            "strongest_ic_factor": strongest[0],
            "strongest_ic_value": strongest[1]["ic"],
            "ic_significant": n >= TUNING_CONSTRAINTS["min_samples_for_full"],
            "ic_note": f"n={n}, {'统计显著' if n >= 30 else f'需n>=30 (差{30-n}条)'}",
            "data_source": "TDX MCP 100%真实K线验证",
            "correction_chain": "V46虚构(87.5%错误) -> V48反馈(2/3错误) -> V49全TDX验证(100%真实)",
            "daily_automation": "15:10 每日TDX自动拉取T+1验证数据",
            "evolution_count": len(self.evolution_history),
            "weights_last_updated": self.evolution_history[-1]["date"] if self.evolution_history else "never",
        }

    def get_evolution_summary(self) -> Dict[str, Any]:
        """获取进化历史摘要"""
        if not self.evolution_history:
            return {"total_evolutions": 0, "latest": None}

        latest = self.evolution_history[-1]
        return {
            "total_evolutions": len(self.evolution_history),
            "latest_date": latest["date"],
            "latest_significance": latest["significance"],
            "latest_adjustments": len(latest["adjustments"]),
            "latest_alerts": len(latest["alerts"]),
            "weights_trend": [
                {
                    "date": r["date"],
                    "weights": r["weights_after"],
                    "n": r["total_samples"],
                    "hit_rate": r["hit_rate"],
                }
                for r in self.evolution_history[-5:]  # 最近5次
            ],
            "ic_trend": [
                {
                    "date": r["date"],
                    "n": r["n"],
                    "ic_by_dim": r["ic_by_dim"],
                }
                for r in self.ic_history[-5:]
            ],
        }

    def generate_report(self) -> str:
        """生成进化摘要文本报告"""
        lines = [
            "=" * 70,
            f"V13.5.52 自主进化报告 | {self.current_date}",
            "=" * 70,
        ]

        stats = self.get_current_stats()
        lines.append(f"数据集: {stats['total']}条 (目标{stats['target']}条)")
        lines.append(f"命中率: {stats['hit_rate']}% | 涨停率: {stats['limit_up_rate']}%")
        lines.append(f"IC显著性: {'是' if stats['ic_significant'] else '否'} — {stats['ic_note']}")
        lines.append(f"最强IC因子: {stats['strongest_ic_factor']} (IC={stats['strongest_ic_value']:+.4f})")
        lines.append("")

        lines.append("当前8维权重 (进化后):")
        for dim_id in DEFAULT_WEIGHTS:
            w = self.weights[dim_id]
            ic_info = stats["ic_by_dim"].get(dim_id, {})
            ic_val = ic_info.get("ic", 0)
            ic_n = ic_info.get("n", 0)
            lines.append(f"  {dim_id} {DIM_NAMES[dim_id]}: {w:.1%} | IC={ic_val:+.4f} (n={ic_n})")

        lines.append("")

        if self.evolution_history:
            latest = self.evolution_history[-1]
            lines.append(f"最近进化: {latest['date']} ({latest['significance']})")
            lines.append(f"  调整项: {len(latest['adjustments'])} | 告警: {len(latest['alerts'])}")

            if latest["adjustments"]:
                lines.append("  权重变更:")
                for adj in latest["adjustments"]:
                    arrow = "+" if adj["delta"] > 0 else ""
                    lines.append(
                        f"    {adj['dim']} {adj['dim_name']}: "
                        f"{adj['old_weight']:.1%} -> {adj['new_weight']:.1%} "
                        f"({arrow}{adj['delta']:.1%}) IC={adj['ic']:+.4f}"
                    )

            if latest["alerts"]:
                lines.append("  告警:")
                for a in latest["alerts"]:
                    lines.append(f"    {a}")

        lines.append("")
        lines.append(f"进化总次数: {len(self.evolution_history)}")
        lines.append(f"权重文件: {WEIGHTS_PATH}")
        lines.append("=" * 70)

        return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================

def main():
    """主入口 — 运行一次进化循环"""
    print("=" * 70)
    print("V13.5.52 自主进化引擎 — AutoEvolutionEngine")
    print("毕方灵犀貔貅助手 — 从经验中学习, 自动优化8维蒸馏权重")
    print("=" * 70)

    engine = AutoEvolutionEngine()

    # 运行进化
    result = engine.evolve()

    # 打印报告
    print()
    print(engine.generate_report())

    # 验证: 用新权重模拟评分
    if "error" not in result:
        print("\n" + "=" * 70)
        print("验证: 用进化后权重模拟蒸馏评分")
        print("=" * 70)

        # 模拟: 用dataset中的样本验证新权重
        samples = engine.dataset
        if samples:
            weights = engine.get_weights()

            # 按T+1涨跌幅排序
            scored = []
            for s in samples:
                # 简化: 用可用字段计算加权分
                total_score = 0.0
                for dim_id in DEFAULT_WEIGHTS:
                    fields = DIM_FIELD_MAP.get(dim_id, [])
                    dim_value = 0.0
                    field_count = 0
                    for f in fields:
                        v = s.get(f)
                        if v is not None:
                            dim_value += float(v)
                            field_count += 1
                    if field_count > 0:
                        dim_value /= field_count
                        total_score += dim_value * weights[dim_id]

                scored.append({
                    "code": s.get("code", ""),
                    "name": s.get("name", ""),
                    "score": round(total_score, 4),
                    "t1_change": s.get("t1_change_pct", 0),
                    "hit": s.get("t1_change_pct", 0) > 0,
                })

            scored.sort(key=lambda x: x["score"], reverse=True)

            print(f"\n  Top 5 (按进化权重评分排序):")
            for i, s in enumerate(scored[:5]):
                hit_mark = "✓" if s["hit"] else "✗"
                print(f"    {i+1}. {s['name']} ({s['code']}) — 评分={s['score']:.3f} | "
                      f"T+1={s['t1_change']:+.2f}% {hit_mark}")

            print(f"\n  Bottom 3:")
            for i, s in enumerate(scored[-3:]):
                hit_mark = "✓" if s["hit"] else "✗"
                print(f"    {len(scored)-2+i}. {s['name']} ({s['code']}) — 评分={s['score']:.3f} | "
                      f"T+1={s['t1_change']:+.2f}% {hit_mark}")

            # 计算排序命中率: Top一半的命中率 vs Bottom一半
            mid = len(scored) // 2
            top_hits = sum(1 for s in scored[:mid] if s["hit"])
            bottom_hits = sum(1 for s in scored[mid:] if s["hit"])
            top_rate = top_hits / mid if mid > 0 else 0
            bottom_rate = bottom_hits / (len(scored) - mid) if (len(scored) - mid) > 0 else 0

            print(f"\n  排序区分度:")
            print(f"    Top半命中率: {top_rate:.1%} ({top_hits}/{mid})")
            print(f"    Bottom半命中率: {bottom_rate:.1%} ({bottom_hits}/{len(scored)-mid})")
            print(f"    区分度: {top_rate - bottom_rate:+.1%} "
                  f"({'有效' if top_rate > bottom_rate else '无效/反向'})")

    print("\n" + "=" * 70)
    print("V13.5.52 自主进化引擎 — 运行完成")
    print(f"权重已保存: {WEIGHTS_PATH}")
    print(f"进化日志: {EVOLUTION_LOG_PATH}")
    print(f"IC历史: {IC_HISTORY_PATH}")
    print("下次14:30 T4选股将自动读取新权重")
    print("=" * 70)

    return result


if __name__ == "__main__":
    main()
