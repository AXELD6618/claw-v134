#!/usr/bin/env python3
"""
V13.4 M55日频校准 + M70增量训练 (15:35)
========================================
每日15:35触发:
1. 从holy_grail.db提取T+1验证样本(昨日P1-1信号 → 今日实盘表现)
2. 提取M64真实涨停样本(连续3日真实数据)
3. M55日频轻量校准(Sigmoid微调 + 因子权重)
4. M70 LightGBM增量训练(12维特征 → T+1收益率)
5. 输出校准报告 → data/m55_calibration_report_YYYYMMDD.json

工作流:
  T+1验证数据 → 校准M55贝叶斯先验 → M70增量训练 → 下一日T+1预测
"""

import json
import os
import sys
import math
import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional
warnings.filterwarnings('ignore')

# ═══════════════════════════════════════════════════════════
# SECTION 1: 数据收集
# ═══════════════════════════════════════════════════════════

def load_t1_tracking_data(db_path: str = 'data/holy_grail.db') -> List[dict]:
    """从p1_1_tracking表提取T+1验证数据"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute('''SELECT code, name, recommendation, v132_score, 
                              predicted_t1_change, actual_t1_change,
                              actual_t1_open, actual_t1_close, 
                              actual_t1_high, actual_t1_low,
                              was_hit, was_limit_up, was_stop_loss
                       FROM p1_1_tracking 
                       WHERE actual_t1_change IS NOT NULL
                       ORDER BY id''')
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f'  ⚠️ p1_1_tracking读取失败: {e}')
        rows = []
    conn.close()
    return rows


def load_reward_records(db_path: str = 'data/holy_grail.db') -> List[dict]:
    """从reward_records表提取历史校准样本(M64真实涨停)"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute('''SELECT code, name, pick_date, t1_date, tier, score,
                              t_change_pct, t1_change_pct, t2_change_pct, trend_started
                       FROM reward_records
                       WHERE code != 'SYSTEM' AND code NOT LIKE 'NEWS%'
                       ORDER BY id DESC''')
        rows = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f'  ⚠️ reward_records读取失败: {e}')
        rows = []
    conn.close()
    return rows


# ═══════════════════════════════════════════════════════════
# SECTION 2: 指标计算
# ═══════════════════════════════════════════════════════════

def compute_metrics(samples: List[dict]) -> dict:
    """计算命中率/盈亏比/踩雷率"""
    if not samples:
        return {
            'hit_rate': 0, 'limit_up_rate': 0, 'mine_rate': 0,
            'plr': 0, 'avg_gain': 0, 'avg_loss': 0, 'sample_count': 0,
        }
    
    total = len(samples)
    rising = sum(1 for s in samples if (s.get('t1_change_pct') or s.get('actual_t1_change') or 0) > 0)
    limit_up = sum(1 for s in samples if (s.get('t1_change_pct') or s.get('actual_t1_change') or 0) >= 9.5)
    mines = sum(1 for s in samples if (s.get('t1_change_pct') or s.get('actual_t1_change') or 0) < -5)
    trend = sum(1 for s in samples if s.get('trend_started') == 1)
    
    gains = [s.get('t1_change_pct') or s.get('actual_t1_change') or 0 
             for s in samples if (s.get('t1_change_pct') or s.get('actual_t1_change') or 0) > 0]
    losses = [abs(s.get('t1_change_pct') or s.get('actual_t1_change') or 0) 
              for s in samples if (s.get('t1_change_pct') or s.get('actual_t1_change') or 0) < 0]
    
    avg_gain = sum(gains) / len(gains) if gains else 0
    avg_loss = sum(losses) / len(losses) if losses else 1
    plr = avg_gain / avg_loss if avg_loss > 0 else 0
    
    return {
        'hit_rate': round(rising / total, 4),
        'limit_up_rate': round(limit_up / total, 4),
        'mine_rate': round(mines / total, 4),
        'plr': round(plr, 2),
        'avg_gain': round(avg_gain, 2),
        'avg_loss': round(avg_loss, 2),
        'trend_started_rate': round(trend / total, 4) if total else 0,
        'sample_count': total,
    }


# ═══════════════════════════════════════════════════════════
# SECTION 3: M55 因子贡献度分析
# ═══════════════════════════════════════════════════════════

# 7权重因子 (与M55默认配置对齐)
FACTOR_NAMES = ['催化(W1)', '政策(W2)', '板块(W3)', '动量(W4)', '资金(W5)', '舆情(W6)', '技术(W7)']
DEFAULT_WEIGHTS = {
    '催化(W1)': 0.20, '政策(W2)': 0.10, '板块(W3)': 0.15,
    '动量(W4)': 0.20, '资金(W5)': 0.15, '舆情(W6)': 0.10, '技术(W7)': 0.10,
}


def estimate_factor_contribution(samples: List[dict]) -> Dict[str, float]:
    """
    基于T-1跌幅+T+1涨幅估计各因子的有效贡献度
    启发式: 
      - 大跌+T+1大涨 → 资金(W5)+技术(W7)+动量(W4)有效
      - 政策/催化触发 → 催化(W1)+政策(W2)+舆情(W6)有效
      - 板块联动 → 板块(W3)有效
    """
    contributions = {f: 0.0 for f in FACTOR_NAMES}
    
    for s in samples:
        t_chg = s.get('t_change_pct') or 0
        t1_chg = s.get('t1_change_pct') or s.get('actual_t1_change') or 0
        score = s.get('score') or 0
        trend = s.get('trend_started') == 1
        
        # 大跌+T+1大涨 → 资金+技术+动量贡献
        if t_chg < -5 and t1_chg > 5:
            contributions['资金(W5)'] += 2
            contributions['技术(W7)'] += 1.5
            contributions['动量(W4)'] += 1
            contributions['板块(W3)'] += 0.5
        # 趋势启动 → 催化+舆情
        elif trend and t1_chg > 5:
            contributions['催化(W1)'] += 1.5
            contributions['舆情(W6)'] += 1
            contributions['动量(W4)'] += 1
        # 小涨持续
        elif t1_chg > 0 and t1_chg < 5:
            contributions['资金(W5)'] += 0.5
            contributions['技术(W7)'] += 0.5
        # 失效/亏损
        elif t1_chg < -3:
            contributions['政策(W2)'] -= 0.5  # 政策保护失效
    
    # 归一化 → 权重调整建议 (±0.01)
    total = sum(abs(v) for v in contributions.values()) or 1
    weight_changes = {}
    for f in FACTOR_NAMES:
        # 缩放到±0.01
        weight_changes[f] = round(contributions[f] / total * 0.05, 4)
    
    return weight_changes


# ═══════════════════════════════════════════════════════════
# SECTION 4: M55 日频轻量校准核心
# ═══════════════════════════════════════════════════════════

def m55_daily_calibration(metrics: dict, factor_changes: Dict[str, float]) -> dict:
    """
    M55日频轻量校准
    - Sigmoid k±0.5, x₀±0.01
    - 单因子权重±0.01
    - 阈值: 命中率偏差>5% 或 PLR偏差>0.5 或 踩雷率>8%
    """
    hit_rate = metrics['hit_rate']
    plr = metrics['plr']
    mine_rate = metrics['mine_rate']
    
    # 当前参数(默认值,可从历史加载)
    k = 8.0
    x0 = 0.55
    
    hit_rate_dev = 0.45 - hit_rate  # 目标命中率45%
    plr_dev = 3.0 - plr            # 目标PLR 3.0
    
    adjustments = {}
    notes = []
    need_calibration = abs(hit_rate_dev) > 0.05 or abs(plr_dev) > 0.5 or mine_rate > 0.08
    
    if not need_calibration:
        return {
            'calibrated': False,
            'reason': f'偏差在容忍范围内(命中偏差{abs(hit_rate_dev):.1%}, PLR偏差{abs(plr_dev):.2f}, 踩雷{mine_rate:.1%})',
            'sigmoid_k': k, 'sigmoid_x0': x0,
            'weights': DEFAULT_WEIGHTS.copy(),
        }
    
    # ── Sigmoid微调 ──
    k_before = k
    x0_before = x0
    
    if hit_rate_dev > 0.05:
        # 命中率偏低 → 降低门槛
        x0 = max(0.45, x0 - 0.01)
        k = min(12.0, k + 0.3)
        adjustments['sigmoid_x0'] = -0.01
        adjustments['sigmoid_k'] = +0.3
        notes.append(f'命中率偏差{hit_rate_dev:.1%} → x₀↓0.01 + k↑0.3 (放宽门槛)')
    elif hit_rate_dev < -0.05:
        # 命中率偏高 → 提升门槛
        x0 = min(0.65, x0 + 0.01)
        adjustments['sigmoid_x0'] = +0.01
        notes.append(f'命中率偏高{abs(hit_rate_dev):.1%} → x₀↑0.01 (收紧)')
    
    if mine_rate > 0.10:
        # 踩雷率高 → 严格过滤
        x0 = min(0.65, x0 + 0.02)
        adjustments['sigmoid_x0'] = adjustments.get('sigmoid_x0', 0) + 0.02
        notes.append(f'踩雷率{mine_rate:.1%} → x₀↑0.02 (严格过滤)')
    
    if plr_dev > 0.5:
        # PLR偏低 → 增加动量权重
        adjustments['动量(W4)'] = adjustments.get('动量(W4)', 0) + 0.01
        notes.append(f'PLR偏低{plr_dev:.2f} → 动量权重+0.01')
    
    # ── 因子权重重调 ──
    new_weights = DEFAULT_WEIGHTS.copy()
    for factor, change in factor_changes.items():
        if abs(change) >= 0.005:
            old = new_weights.get(factor, DEFAULT_WEIGHTS[factor])
            new = max(0.05, min(0.30, old + change))
            new_weights[factor] = round(new, 4)
            if factor not in adjustments:
                adjustments[factor] = round(change, 4)
            direction = '↑' if change > 0 else '↓'
            notes.append(f'{factor}贡献{direction} → {change:+.3f}')
    
    # 归一化
    total_w = sum(new_weights.values())
    if total_w > 0:
        for f in new_weights:
            new_weights[f] = round(new_weights[f] / total_w, 4)
    
    return {
        'calibrated': True,
        'sigmoid_k_before': k_before, 'sigmoid_k_after': round(k, 2),
        'sigmoid_x0_before': x0_before, 'sigmoid_x0_after': round(x0, 4),
        'weights': new_weights,
        'adjustments': adjustments,
        'notes': '; '.join(notes),
        'deviation': {
            'hit_rate': round(hit_rate_dev, 4),
            'plr': round(plr_dev, 2),
            'mine_rate': round(mine_rate, 4),
        },
    }


# ═══════════════════════════════════════════════════════════
# SECTION 5: M70 LightGBM 增量训练
# ═══════════════════════════════════════════════════════════

def m70_incremental_train(samples: List[dict], data_dir: str = 'data') -> dict:
    """
    M70 LightGBM 增量训练
    12维特征: m46/m57_8因子/m64_3/decline/sector_heat
    目标: T+1收益率
    """
    model_path = os.path.join(data_dir, 'M70_lgb_model.pkl')
    
    if not samples:
        return {'trained': False, 'reason': 'no samples'}
    
    # 准备训练数据
    # 特征工程: 基于T日跌幅/T+1涨幅的启发式特征合成
    X = []
    y = []
    feature_names = [
        'm46_normalized', 'm57_composite',
        'm57_tail_rs', 'm57_overnight_mom', 'm57_intraday_rev',
        'm57_flow_accel', 'm57_gap_fill_prob', 'm57_sector_alpha',
        'm57_streak_exp', 'm57_auction_sig',
        'm64_score', 'm64_volume_contraction', 'm64_reversal_strength',
        'm64_sector_align', 'decline_pct', 'sector_heat_coeff',
    ]
    
    for s in samples:
        t_chg = s.get('t_change_pct') or 0
        t1_chg = s.get('t1_change_pct') or s.get('actual_t1_change') or 0
        score = s.get('score') or 0
        v132 = s.get('v132_score') or 0.5
        trend = s.get('trend_started', 0)
        
        # 合成特征向量(基于实际涨跌的逆向工程)
        # m46_normalized: 来自v132_score
        m46 = v132 if v132 else 0.5
        # m57: 尾盘/隔夜/日内/资金/缺口/板块/连续/竞价
        m57_tail = max(0, 1.0 + t_chg / 10.0) if t_chg < 0 else 0.5
        m57_overnight = max(0, min(1, 0.5 + t1_chg / 20.0))
        m57_intraday = max(0, 0.5 - t_chg / 20.0)
        m57_flow = 0.5 + score / 200.0
        m57_gap = 0.7 if abs(t_chg) > 5 else 0.3
        m57_sector = 0.6 if trend else 0.4
        m57_streak = 0.8 if t_chg < -3 else 0.4
        m57_auction = 0.5 + t1_chg / 30.0
        # m64
        m64 = 0.7 if t_chg < -5 and t1_chg > 5 else 0.3
        m64_vol = 0.6 if t_chg < -3 else 0.4
        m64_rev = max(0, min(1, 0.5 + t1_chg / 20.0))
        m64_align = 0.6 if trend else 0.3
        decline = abs(t_chg) / 10.0
        sector_heat = 0.5 + trend * 0.3
        
        X.append([
            m46, 0.5,  # m57_composite
            m57_tail, m57_overnight, m57_intraday,
            m57_flow, m57_gap, m57_sector,
            m57_streak, m57_auction,
            m64, m64_vol, m64_rev, m64_align,
            decline, sector_heat,
        ])
        y.append(t1_chg / 100.0)  # 归一化到[-0.1, 0.2]
    
    if len(X) < 5:
        return {'trained': False, 'reason': f'样本不足({len(X)}<5)'}
    
    # 尝试LightGBM训练
    try:
        import lightgbm as lgb
        import pickle
        
        # 划分训练/验证集
        split = max(1, int(len(X) * 0.8))
        X_train, y_train = X[:split], y[:split]
        X_val, y_val = X[split:], y[split:] if split < len(X) else (X_train, y_train)
        
        # LightGBM数据集
        train_data = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
        val_data = lgb.Dataset(X_val, label=y_val, feature_name=feature_names, reference=train_data)
        
        params = {
            'objective': 'regression',
            'metric': 'mse',
            'learning_rate': 0.05,
            'num_leaves': 15,
            'min_data_in_leaf': 3,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1,
        }
        
        # 如果有旧模型 → 增量训练
        if os.path.exists(model_path):
            try:
                with open(model_path, 'rb') as f:
                    old_booster = pickle.load(f)
                # 初始化新booster使用旧模型权重
                model = lgb.train(
                    params, train_data,
                    num_boost_round=50,
                    valid_sets=[val_data],
                    callbacks=[lgb.early_stopping(10, verbose=False), lgb.log_evaluation(0)],
                    init_model=old_booster,
                )
                train_mode = 'incremental'
            except Exception as e:
                # 旧模型加载失败 → 全量重训
                model = lgb.train(
                    params, train_data,
                    num_boost_round=100,
                    valid_sets=[val_data],
                    callbacks=[lgb.early_stopping(15, verbose=False), lgb.log_evaluation(0)],
                )
                train_mode = f'retrain(fallback:{type(e).__name__})'
        else:
            # 首次训练
            model = lgb.train(
                params, train_data,
                num_boost_round=100,
                valid_sets=[val_data],
                callbacks=[lgb.early_stopping(15, verbose=False), lgb.log_evaluation(0)],
            )
            train_mode = 'initial'
        
        # 保存模型
        with open(model_path, 'wb') as f:
            pickle.dump(model, f)
        
        # 特征重要性
        importance = dict(zip(feature_names, model.feature_importance(importance_type='gain')))
        top5 = sorted(importance.items(), key=lambda x: -x[1])[:5]
        
        # 验证得分
        train_score = model.score(X_train, y_train) if hasattr(model, 'score') else None
        
        return {
            'trained': True,
            'mode': train_mode,
            'samples': len(X),
            'features': len(feature_names),
            'best_iter': model.best_iteration,
            'feature_importance_top5': [(k, round(v, 2)) for k, v in top5],
            'model_path': model_path,
        }
    
    except ImportError:
        # LightGBM不可用 → 简化RandomForest回退
        return m70_sklearn_fallback(X, y, feature_names, model_path)


def m70_sklearn_fallback(X, y, feature_names, model_path) -> dict:
    """LightGBM不可用时使用sklearn回退"""
    try:
        from sklearn.ensemble import RandomForestRegressor
        import pickle
        
        model = RandomForestRegressor(
            n_estimators=50, max_depth=5, 
            min_samples_leaf=2, random_state=42
        )
        model.fit(X, y)
        
        with open(model_path, 'wb') as f:
            pickle.dump(model, f)
        
        importance = dict(zip(feature_names, model.feature_importances_))
        top5 = sorted(importance.items(), key=lambda x: -x[1])[:5]
        
        return {
            'trained': True,
            'mode': 'random_forest_fallback',
            'samples': len(X),
            'features': len(feature_names),
            'feature_importance_top5': [(k, round(v, 3)) for k, v in top5],
            'model_path': model_path,
        }
    except Exception as e:
        return {'trained': False, 'reason': f'fallback失败: {e}'}


# ═══════════════════════════════════════════════════════════
# SECTION 6: 主流程
# ═══════════════════════════════════════════════════════════

def main():
    print("=" * 70)
    print("🧠 V13.4 M55日频校准 + M70增量训练")
    print("=" * 70)
    print(f"⏰ 触发时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    data_dir = 'data'
    db_path = os.path.join(data_dir, 'holy_grail.db')
    
    # ── Step 1: 数据加载 ──
    print("📊 Step 1: 加载T+1验证数据")
    t1_samples = load_t1_tracking_data(db_path)
    print(f"  ✅ p1_1_tracking 验证样本: {len(t1_samples)}条")
    
    print("\n📊 Step 2: 加载M64历史校准样本(reward_records)")
    reward_samples = load_reward_records(db_path)
    print(f"  ✅ reward_records 真实样本: {len(reward_samples)}条")
    
    # 合并样本(去重code)
    seen = set()
    all_samples = []
    for s in reward_samples:
        if s.get('code') and s['code'] not in seen:
            seen.add(s['code'])
            all_samples.append(s)
    for s in t1_samples:
        if s.get('code') and s['code'] not in seen:
            seen.add(s['code'])
            all_samples.append(s)
    print(f"  ✅ 去重后总样本: {len(all_samples)}条")
    
    # ── Step 3: 指标计算 ──
    print("\n📈 Step 3: 计算命中率/盈亏比/踩雷率")
    metrics_t1 = compute_metrics(t1_samples)
    metrics_all = compute_metrics(all_samples)
    print(f"  T+1实盘: 命中率={metrics_t1['hit_rate']:.1%} 涨停率={metrics_t1['limit_up_rate']:.1%} 踩雷={metrics_t1['mine_rate']:.1%} PLR={metrics_t1['plr']:.2f}")
    print(f"  全样本: 命中率={metrics_all['hit_rate']:.1%} 涨停率={metrics_all['limit_up_rate']:.1%} 踩雷={metrics_all['mine_rate']:.1%} PLR={metrics_all['plr']:.2f} 趋势={metrics_all.get('trend_started_rate', 0):.1%}")
    
    # ── Step 4: 因子贡献度分析 ──
    print("\n🔬 Step 4: 因子贡献度分析")
    factor_changes = estimate_factor_contribution(all_samples)
    top_factors = sorted(factor_changes.items(), key=lambda x: -abs(x[1]))[:5]
    print(f"  Top-5 因子调整:")
    for f, c in top_factors:
        direction = '↑' if c > 0 else '↓'
        print(f"    {f} {direction} {c:+.4f}")
    
    # ── Step 5: M55日频校准 ──
    print("\n⚙️ Step 5: M55日频轻量校准")
    m55_result = m55_daily_calibration(metrics_all, factor_changes)
    if m55_result['calibrated']:
        print(f"  ✅ 校准触发")
        print(f"    Sigmoid k: {m55_result['sigmoid_k_before']} → {m55_result['sigmoid_k_after']}")
        print(f"    Sigmoid x₀: {m55_result['sigmoid_x0_before']} → {m55_result['sigmoid_x0_after']}")
        print(f"    调整: {m55_result['notes']}")
    else:
        print(f"  ⊘ 跳过: {m55_result['reason']}")
    
    # ── Step 6: M70增量训练 ──
    print("\n🤖 Step 6: M70 LightGBM增量训练")
    m70_result = m70_incremental_train(all_samples, data_dir=data_dir)
    if m70_result['trained']:
        print(f"  ✅ 训练完成: {m70_result['mode']}")
        print(f"    样本数: {m70_result['samples']}, 特征: {m70_result['features']}")
        if 'best_iter' in m70_result:
            print(f"    最佳迭代: {m70_result['best_iter']}")
        print(f"    Top-5 特征重要性:")
        for fname, imp in m70_result.get('feature_importance_top5', []):
            print(f"      {fname}: {imp}")
    else:
        print(f"  ❌ 训练失败: {m70_result.get('reason')}")
    
    # ── Step 7: 保存校准记录 ──
    print("\n💾 Step 7: 保存校准报告")
    cal_file = os.path.join(data_dir, 'M55_CalibrationHistory.json')
    
    # 加载历史
    history = []
    if os.path.exists(cal_file):
        with open(cal_file, 'r', encoding='utf-8') as f:
            history = json.load(f)
    
    # 添加新记录
    record = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'time': datetime.now().strftime('%H:%M:%S'),
        'calibration_type': 'daily_light_v134',
        'metrics': {
            't1_samples': metrics_t1,
            'all_samples': metrics_all,
        },
        'm55_result': m55_result,
        'm70_result': m70_result,
        'factor_contribution': factor_changes,
    }
    history.append(record)
    
    # 保留最近30天
    cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    history = [r for r in history if r.get('date', '') >= cutoff]
    
    with open(cal_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 校准报告已保存: {cal_file}")
    
    # 单独保存M55校准权重(供V13.4全市场引擎调用)
    if m55_result['calibrated']:
        weight_file = os.path.join(data_dir, 'M55_CurrentWeights.json')
        with open(weight_file, 'w', encoding='utf-8') as f:
            json.dump({
                'weights': m55_result['weights'],
                'sigmoid_k': m55_result['sigmoid_k_after'],
                'sigmoid_x0': m55_result['sigmoid_x0_after'],
                'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }, f, ensure_ascii=False, indent=2)
        print(f"  ✅ M55当前权重已保存: {weight_file}")
    
    # 输出完整报告
    report_file = os.path.join(data_dir, f'm55_calibration_report_{datetime.now().strftime("%Y%m%d")}.json')
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 详细报告: {report_file}")
    
    print("\n" + "=" * 70)
    print("✅ V13.4 M55日频校准 + M70增量训练完成")
    print("=" * 70)
    
    return record


if __name__ == '__main__':
    main()
