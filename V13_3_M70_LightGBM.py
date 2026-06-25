#!/usr/bin/env python3
"""
V13.3 M70 LightGBM自适应权重引擎 — 机器学习持续进化模块
=========================================================
灵感: Microsoft Qlib 158+ Alpha因子 + BigQuant超跌反弹回测

核心创新: 用LightGBM自动学习M46/M57/M64最优组合权重
替代固定V13.2公式: v132=0.35*M46+0.35*M57+0.15*M64+0.10*M56+0.05*Q

工作流:
  1. 收集T日信号特征 (M46/M57/M64/sector_heat/等12维)
  2. 收集T+1日实际涨跌 (标签)
  3. LightGBM增量训练 → 学习最优权重
  4. 预测T日新信号 → 输出ML增强评分
  5. 每日T+1验证后自动更新模型

时间: 2026-06-24 自主进化
"""

import json
import os
import math
import sqlite3
import pickle
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import warnings
warnings.filterwarnings('ignore')


# ═══════════════════════════════════════════════════════════
# SECTION 1: 配置
# ═══════════════════════════════════════════════════════════

@dataclass
class M70Config:
    """M70 LightGBM引擎配置"""
    model_path: str = "data/M70_lgb_model.pkl"
    feature_dim: int = 16          # 输入特征维度
    min_samples_train: int = 30    # 最少训练样本
    update_frequency: str = "daily" # 每日更新
    learning_rate: float = 0.05    # 初始学习率
    num_boost_round: int = 100     # 提升轮数
    early_stopping: int = 20        # 早停轮数
    validation_ratio: float = 0.2   # 验证集比例
    
    # 特征定义 (16维)
    feature_names: List[str] = field(default_factory=lambda: [
        'm46_normalized',        # M46归一化置信度
        'm57_composite',          # M57复合评分
        'm57_tail_rs',           # M57尾盘相对强度
        'm57_overnight_mom',     # M57隔夜动量
        'm57_intraday_rev',      # M57日内反转
        'm57_flow_accel',        # M57资金流加速
        'm57_gap_fill_prob',     # M57缺口回补概率
        'm57_sector_alpha',      # M57板块Alpha
        'm57_streak_exp',        # M57连续下跌衰减
        'm57_auction_sig',       # M57集合竞价信号
        'm64_score',             # M64超跌反转
        'm64_volume_contraction',# M64缩量信号
        'm64_reversal_strength', # M64反转强度
        'm64_sector_align',      # M64板块共振
        'decline_pct',           # 当日跌幅
        'sector_heat_coeff',     # 板块热度系数
    ])
    
    # 目标标签
    target_name: str = 't1_return'  # T+1收益率


# ═══════════════════════════════════════════════════════════
# SECTION 2: LightGBM引擎
# ═══════════════════════════════════════════════════════════

class M70LightGBMEngine:
    """
    M70 LightGBM自适应权重引擎
    
    优势:
    - 自动学习最优因子权重 (替代人工配置)
    - 每日增量训练 (T+1验证数据自动进入训练集)
    - 处理非线性因子交互
    - 特征重要性排序 (反哺因子研发)
    """
    
    def __init__(self, config: M70Config = None):
        self.config = config or M70Config()
        self.model = None
        self.training_samples: List[Dict] = []
        self.feature_importance: Dict[str, float] = {}
        self._lgb_available = False
        
        # 尝试导入LightGBM
        try:
            import lightgbm as lgb
            self._lgb = lgb
            self._lgb_available = True
            print("✅ LightGBM已加载 — M70自适应引擎就绪")
        except ImportError:
            print("⚠️ LightGBM未安装 — 回退到Scikit-Learn RandomForest")
            try:
                from sklearn.ensemble import RandomForestRegressor
                self._rf = RandomForestRegressor
                self._lgb_available = False
            except ImportError:
                print("❌ 无ML库可用 — M70降级为等权重平均")
                self._rf = None
        
        # 加载已有模型
        self._load_model()
    
    def _load_model(self):
        """加载已有模型"""
        if os.path.exists(self.config.model_path):
            try:
                with open(self.config.model_path, 'rb') as f:
                    saved = pickle.load(f)
                self.model = saved.get('model')
                self.training_samples = saved.get('samples', [])
                self.feature_importance = saved.get('importance', {})
                print(f"  📦 M70模型已加载: {len(self.training_samples)} 训练样本")
            except Exception as e:
                print(f"  ⚠️ M70模型加载失败: {e}")
    
    def _save_model(self):
        """保存模型"""
        saved = {
            'model': self.model,
            'samples': self.training_samples,
            'importance': self.feature_importance,
            'config': {k: v for k, v in self.config.__dict__.items() if not k.startswith('_')},
            'updated_at': datetime.now().isoformat(),
        }
        os.makedirs(os.path.dirname(self.config.model_path) or '.', exist_ok=True)
        with open(self.config.model_path, 'wb') as f:
            pickle.dump(saved, f)
    
    def build_features(self, signal: Dict) -> List[float]:
        """
        从信号字典构建14维特征向量
        
        参数:
            signal: P1-1/daily_signals信号记录
                {
                  m46_normalized, m57_composite, m57_factors: {tail_rs, overnight_mom, ...},
                  m64_score, m64_signals: {volume_contraction, reversal_strength, sector_align},
                  decline_pct, sector_heat_coeff
                }
        """
        features = []
        for name in self.config.feature_names:
            if name == 'm46_normalized':
                val = signal.get('m46_normalized', signal.get('m46_confidence', 0))
            elif name == 'm57_composite':
                val = signal.get('m57_composite', 0)
            elif name.startswith('m57_') and 'factors' in signal:
                factor_name = name.replace('m57_', '')
                val = signal['m57_factors'].get(factor_name, 0) if isinstance(signal.get('m57_factors'), dict) else 0
            elif name == 'm64_score':
                val = signal.get('m64_score', 0)
            elif name.startswith('m64_') and 'signals' in signal:
                sig_name = name.replace('m64_', '')
                val = signal['m64_signals'].get(sig_name, 0) if isinstance(signal.get('m64_signals'), dict) else 0
            elif name == 'decline_pct':
                val = abs(signal.get('decline_pct', signal.get('change_pct', 0)))
            elif name == 'sector_heat_coeff':
                val = signal.get('sector_heat_coeff', 1.0)
            else:
                val = signal.get(name, 0)
            
            # 异常值处理
            if val is None or math.isnan(val) or math.isinf(val):
                val = 0.0
            features.append(float(val))
        
        return features
    
    def add_training_sample(self, signal: Dict, t1_return: float):
        """
        添加T+1验证样本
        
        signal: T日信号特征
        t1_return: T+1日实际收益率 (%)
        """
        features = self.build_features(signal)
        if len(features) != self.config.feature_dim:
            print(f"  ⚠️ 特征维度不匹配: {len(features)} vs {self.config.feature_dim}")
            return
        
        sample = {
            'features': features,
            'label': t1_return,
            'code': signal.get('code', ''),
            'name': signal.get('name', ''),
            'date': signal.get('signal_date', date.today().isoformat()),
            'v132_score': signal.get('v132_score', 0),
        }
        self.training_samples.append(sample)
    
    def train(self, force_retrain: bool = False) -> Dict:
        """
        训练/增量更新模型
        
        Returns: 训练统计
        """
        n = len(self.training_samples)
        if n < self.config.min_samples_train:
            return {
                'status': 'insufficient',
                'samples': n,
                'required': self.config.min_samples_train,
                'message': f'样本不足 ({n}/{self.config.min_samples_train})',
            }
        
        # 准备训练数据
        X = [s['features'] for s in self.training_samples]
        y = [s['label'] for s in self.training_samples]
        
        # 训练/验证集分割
        n_val = max(1, int(n * self.config.validation_ratio))
        X_train, X_val = X[:-n_val], X[-n_val:]
        y_train, y_val = y[:-n_val], y[-n_val:]
        
        try:
            if self._lgb_available:
                return self._train_lgb(X_train, y_train, X_val, y_val, X, y)
            elif self._rf:
                return self._train_rf(X_train, y_train, X_val, y_val, X, y)
            else:
                return self._train_simple(X, y)
        except Exception as e:
            print(f"  ❌ M70训练失败: {e}")
            return {'status': 'error', 'error': str(e)}
    
    def _train_lgb(self, X_train, y_train, X_val, y_val, X_all, y_all) -> Dict:
        """LightGBM训练"""
        import lightgbm as lgb
        
        train_data = lgb.Dataset(X_train, label=y_train)
        val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)
        
        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': self.config.learning_rate,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1,
            'seed': 42,
        }
        
        self.model = lgb.train(
            params,
            train_data,
            num_boost_round=self.config.num_boost_round,
            valid_sets=[val_data],
            callbacks=[lgb.early_stopping(self.config.early_stopping)],
        )
        
        # 特征重要性
        importance = self.model.feature_importance(importance_type='gain')
        self.feature_importance = {
            name: float(imp) 
            for name, imp in zip(self.config.feature_names, importance)
        }
        
        # 全部样本预测 (用于IC计算)
        preds_all = self.model.predict(X_all)
        
        # IC计算
        ic = self._pearson_corr(preds_all, y_all)
        
        # 训练集RMSE
        preds_train = self.model.predict(X_train)
        rmse_train = math.sqrt(sum((p - a)**2 for p, a in zip(preds_train, y_train)) / len(y_train))
        rmse_val = math.sqrt(sum((p - a)**2 for p, a in zip(self.model.predict(X_val), y_val)) / len(y_val))
        
        self._save_model()
        
        # 特征重要性Top5
        sorted_imp = sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'status': 'success',
            'algorithm': 'LightGBM',
            'train_samples': len(y_train),
            'val_samples': len(y_val),
            'rmse_train': round(rmse_train, 4),
            'rmse_val': round(rmse_val, 4),
            'factor_ic': round(ic, 4),
            'best_iteration': self.model.best_iteration,
            'top5_features': sorted_imp[:5],
        }
    
    def _train_rf(self, X_train, y_train, X_val, y_val, X_all, y_all) -> Dict:
        """RandomForest训练 (LightGBM不可用时的回退)"""
        model = self._rf(n_estimators=100, max_depth=10, random_state=42, n_jobs=-1)
        model.fit(X_train, y_train)
        self.model = model
        
        importance = model.feature_importances_
        self.feature_importance = {
            name: float(imp) 
            for name, imp in zip(self.config.feature_names, importance)
        }
        
        preds_all = model.predict(X_all)
        ic = self._pearson_corr(preds_all, y_all)
        
        rmse_train = math.sqrt(sum((p - a)**2 for p, a in zip(model.predict(X_train), y_train)) / len(y_train))
        rmse_val = math.sqrt(sum((p - a)**2 for p, a in zip(model.predict(X_val), y_val)) / len(y_val))
        
        self._save_model()
        
        sorted_imp = sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)
        
        return {
            'status': 'success',
            'algorithm': 'RandomForest',
            'train_samples': len(y_train),
            'val_samples': len(y_val),
            'rmse_train': round(rmse_train, 4),
            'rmse_val': round(rmse_val, 4),
            'factor_ic': round(ic, 4),
            'top5_features': sorted_imp[:5],
        }
    
    def _train_simple(self, X, y) -> Dict:
        """无ML库时的简单线性回归回退"""
        n = len(X)
        if n < 3:
            return {'status': 'insufficient', 'samples': n}
        
        # 简单OLS: 每个特征与y的相关系数作为权重
        weights = []
        for j in range(len(X[0])):
            xj = [x[j] for x in X]
            corr = self._pearson_corr(xj, y)
            weights.append(corr)
        
        # 归一化权重
        total = sum(abs(w) for w in weights) or 1
        weights = [w / total for w in weights]
        
        self.model = {'type': 'linear', 'weights': weights}
        self._save_model()
        
        return {
            'status': 'success',
            'algorithm': 'LinearCorrelation',
            'samples': n,
            'factor_ic': max(weights) if weights else 0,
            'top5_features': sorted(
                zip(self.config.feature_names, weights),
                key=lambda x: abs(x[1]), reverse=True
            )[:5],
        }
    
    def predict(self, signal: Dict) -> float:
        """
        预测T+1收益率 (ML增强评分)
        
        Returns: 预期T+1收益率 (%)
        """
        if self.training_samples and not self.model:
            # 有样本但无模型 → 训练
            result = self.train()
            if result.get('status') != 'success':
                return self._fallback_predict(signal)
        
        if not self.model:
            return self._fallback_predict(signal)
        
        features = self.build_features(signal)
        
        try:
            if self._lgb_available:
                pred = float(self.model.predict([features])[0])
            elif self._rf:
                pred = float(self.model.predict([features])[0])
            elif isinstance(self.model, dict) and self.model.get('type') == 'linear':
                pred = sum(w * f for w, f in zip(self.model['weights'], features))
            else:
                pred = self._fallback_predict(signal)
        except Exception:
            pred = self._fallback_predict(signal)
        
        return round(pred, 4)
    
    def _fallback_predict(self, signal: Dict) -> float:
        """模型未就绪时的降级预测"""
        m46 = signal.get('m46_normalized', signal.get('m46_confidence', 0))
        decline = abs(signal.get('decline_pct', signal.get('change_pct', 0)))
        # 简单超跌反弹模型: 深度超跌+高M46 → 预期反弹高
        return round(decline * 0.15 * m46 * 3, 4)
    
    def predict_batch(self, signals: List[Dict]) -> List[Dict]:
        """批量预测"""
        results = []
        for sig in signals:
            pred = self.predict(sig)
            sig_copy = dict(sig)
            sig_copy['m70_forecast'] = pred
            sig_copy['m70_confidence'] = 1.0 / (1.0 + math.exp(-pred * 5))  # sigmoid映射
            results.append(sig_copy)
        return results
    
    @staticmethod
    def _pearson_corr(x, y):
        """Pearson相关系数"""
        n = len(x)
        if n < 2:
            return 0
        mx = sum(x) / n
        my = sum(y) / n
        sx = math.sqrt(sum((xi - mx)**2 for xi in x) / (n-1)) or 1e-10
        sy = math.sqrt(sum((yi - my)**2 for yi in y) / (n-1)) or 1e-10
        r = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / ((n-1) * sx * sy)
        return max(-1.0, min(1.0, r))
    
    def get_status(self) -> Dict:
        """获取引擎状态"""
        return {
            'model_loaded': self.model is not None,
            'lgb_available': self._lgb_available,
            'training_samples': len(self.training_samples),
            'min_required': self.config.min_samples_train,
            'ready': len(self.training_samples) >= self.config.min_samples_train and self.model is not None,
            'top_features': sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)[:5],
        }


# ═══════════════════════════════════════════════════════════
# SECTION 3: T+1自动训练接口
# ═══════════════════════════════════════════════════════════

def auto_train_from_db(db_path: str = "data/holy_grail.db"):
    """
    从数据库自动收集T+1训练数据并更新M70模型
    
    查询 daily_signals (T日信号) + p1_1_tracking (T+1验证)
    自动构建训练集
    """
    engine = M70LightGBMEngine()
    
    if not os.path.exists(db_path):
        print(f"  ⚠️ 数据库不存在: {db_path}")
        return engine.get_status()
    
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    # 查询已有T+1验证样本
    try:
        rows = conn.execute("""
            SELECT d.*, t.actual_t1_change, t.was_hit, t.was_limit_up
            FROM daily_signals d
            LEFT JOIN p1_1_tracking t ON d.signal_date = t.signal_date AND d.code = t.code
            WHERE t.actual_t1_change IS NOT NULL
            ORDER BY d.signal_date DESC
        """).fetchall()
    except sqlite3.OperationalError:
        print("  ⚠️ 表不存在或无T+1数据")
        conn.close()
        return engine.get_status()
    finally:
        conn.close() if 'conn' in dir() else None
    
    if not rows:
        print("  ⚠️ 无T+1验证数据可用于训练")
        return engine.get_status()
    
    # 构建训练样本
    new_samples = 0
    for row in rows:
        d = dict(row)
        t1_return = d.get('actual_t1_change', 0)
        
        # 重建信号特征
        signal = {
            'code': d['code'],
            'name': d.get('name', ''),
            'signal_date': d['signal_date'],
            'm46_normalized': d.get('m46_normalized', d.get('m46_score', 0)),
            'm46_confidence': d.get('m46_confidence', d.get('m46_score', 0)),
            'm57_composite': d.get('m57_composite', d.get('m57_score', 0)),
            'm64_score': d.get('m64_score', 0),
            'decline_pct': d.get('decline_pct', d.get('change_pct', 0)),
            'sector_heat_coeff': d.get('sector_heat_coeff', d.get('sector_heat', 1.0)),
            'v132_score': d.get('v132_score', 0),
        }
        
        engine.add_training_sample(signal, t1_return)
        new_samples += 1
    
    print(f"  📊 M70训练数据: {new_samples} 条 (数据库)")
    
    # 训练
    result = engine.train()
    status = engine.get_status()
    status['train_result'] = result
    
    return status


# ═══════════════════════════════════════════════════════════
# SECTION 4: 自测
# ═══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("╔══════════════════════════════════════════════════════╗")
    print("║  V13.3 M70 LightGBM自适应权重引擎 自测            ║")
    print("╚══════════════════════════════════════════════════════╝\n")
    
    engine = M70LightGBMEngine()
    
    # 模拟训练数据 (基于P1-1 30只信号结构)
    print("📊 生成模拟训练数据...")
    
    # 高M46+高M64 → 大概率涨停
    for i in range(15):
        signal = {
            'code': f'6000{i:02d}', 'name': f'测试股{i}',
            'm46_normalized': 0.6 + i * 0.015,  # 0.60-0.82
            'm57_composite': 0.2 + i * 0.01,     # 0.20-0.34
            'm57_factors': {'tail_rs': 0.3, 'overnight_mom': -0.1, 'intraday_rev': 0.5, 
                           'flow_accel': 0.1, 'gap_fill_prob': 0.4, 'sector_alpha': 0.1,
                           'streak_exp': 0.3, 'auction_sig': 0.2},
            'm64_score': 0.3 + i * 0.02,         # 0.30-0.58
            'm64_signals': {'volume_contraction': 0.2, 'reversal_strength': 0.3, 'sector_align': 0.1},
            'decline_pct': -8 - i * 0.2,
            'sector_heat_coeff': 1.0 + i * 0.02,
        }
        t1_return = 5 + i * 0.5  # 5%-12% (涨停)
        engine.add_training_sample(signal, t1_return)
    
    # 低M46 → 大概率续跌
    for i in range(15):
        signal = {
            'code': f'7000{i:02d}', 'name': f'测试股{i+15}',
            'm46_normalized': 0.3 + i * 0.01,    # 0.30-0.44
            'm57_composite': 0.05 + i * 0.01,     # 0.05-0.19
            'm57_factors': {'tail_rs': 0.1, 'overnight_mom': -0.3, 'intraday_rev': 0.2,
                           'flow_accel': -0.1, 'gap_fill_prob': 0.2, 'sector_alpha': -0.1,
                           'streak_exp': 0.1, 'auction_sig': -0.1},
            'm64_score': 0.1 + i * 0.01,
            'm64_signals': {'volume_contraction': 0.1, 'reversal_strength': 0.1, 'sector_align': -0.1},
            'decline_pct': -4 - i * 0.1,
            'sector_heat_coeff': 0.85,
        }
        t1_return = -3 - i * 0.3  # 继续下跌
        engine.add_training_sample(signal, t1_return)
    
    # 训练
    print(f"\n🚀 开始训练 ({len(engine.training_samples)} 样本)...")
    result = engine.train()
    
    print(f"\n{'='*50}")
    print(f"  M70 训练结果:")
    print(f"  算法: {result.get('algorithm', 'N/A')}")
    print(f"  训练样本: {result.get('train_samples', 0)}")
    print(f"  验证RMSE: {result.get('rmse_val', 'N/A')}")
    print(f"  因子IC: {result.get('factor_ic', 'N/A')}")
    print(f"\n  Top5 特征重要性:")
    for name, imp in result.get('top5_features', []):
        print(f"    {name}: {imp:.4f}")
    
    # 预测测试
    print(f"\n📈 预测测试:")
    test_signal = {
        'code': 'TEST01', 'name': '测试预测',
        'm46_normalized': 0.75,
        'm57_composite': 0.35,
        'm57_factors': {'tail_rs': 0.4, 'overnight_mom': 0.1, 'intraday_rev': 0.6,
                       'flow_accel': 0.3, 'gap_fill_prob': 0.5, 'sector_alpha': 0.2,
                       'streak_exp': 0.4, 'auction_sig': 0.3},
        'm64_score': 0.55,
        'm64_signals': {'volume_contraction': 0.4, 'reversal_strength': 0.5, 'sector_align': 0.3},
        'decline_pct': -9.5,
        'sector_heat_coeff': 1.15,
    }
    
    forecast = engine.predict(test_signal)
    print(f"  代码: {test_signal['code']}")
    print(f"  M70预测T+1收益: {forecast:.2f}%")
    print(f"  模型状态: {'✅ 就绪' if engine.get_status()['ready'] else '⏳ 训练中'}")
    print(f"{'='*50}")
