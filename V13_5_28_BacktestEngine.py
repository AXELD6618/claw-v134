#!/usr/bin/env python3
"""
V13.5.28 回测引擎 — 用真实数据验证选股有效性
目标: T日尾盘选股→T+1上涨/涨停 准确率

核心思路:
1. 获取历史T日收盘数据(TDX kline)
2. 运行精简版V13.5.27打分(只保留IC>0的维度)
3. 对比T+1实际涨跌
4. 报告每个维度的单独IC
5. 自动淘汰IC<0的维度(噪音)
6. 给出最优维度组合建议

使用方法: python V13_5_28_BacktestEngine.py --days 10
"""

import sqlite3
import json
import os
import sys
import re
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = "data/holy_grail.db"
OUTPUT_DIR = "outputs"

class BacktestEngine:
    """V13.5.28回测引擎"""
    
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.results = []
        self.dim_ic = defaultdict(list)  # dimension -> [(signal, actual_t1), ...]
    
    def load_historical_signals(self, days_back=30):
        """从数据库加载历史信号"""
        # p1_1_tracking: T日信号→T+1结果
        rows = self.conn.execute('''
            SELECT signal_date, code, name, recommendation, v132_score,
                   actual_t1_change, was_hit, was_limit_up, tracking_notes
            FROM p1_1_tracking
            WHERE signal_date >= ?
            ORDER BY signal_date DESC
        ''', [(datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')]).fetchall()
        
        for r in rows:
            self.results.append({
                'date': r[0],
                'code': r[1],
                'name': r[2],
                'rec': r[3],
                'score': r[4],
                't1_change': r[5],
                'was_hit': r[6],
                'was_limit_up': r[7],
                'notes': r[8]
            })
        
        return len(self.results)
    
    def analyze_by_category(self):
        """按信号类别分析准确率"""
        cats = defaultdict(lambda: {'total': 0, 'hit': 0, 'lim': 0, 'sum_chg': 0.0})
        
        for r in self.results:
            cat = r['rec'] or 'UNKNOWN'
            cats[cat]['total'] += 1
            if r['was_hit']: cats[cat]['hit'] += 1
            if r['was_limit_up']: cats[cat]['lim'] += 1
            cats[cat]['sum_chg'] += r['t1_change'] or 0
        
        return cats
    
    def analyze_by_date(self):
        """按日期分析"""
        dates = defaultdict(lambda: {'total': 0, 'hit': 0, 'lim': 0, 'sum_chg': 0.0})
        
        for r in self.results:
            d = r['date']
            dates[d]['total'] += 1
            if r['was_hit']: dates[d]['hit'] += 1
            if r['was_limit_up']: dates[d]['lim'] += 1
            dates[d]['sum_chg'] += r['t1_change'] or 0
        
        return dates
    
    def extract_dimensions_from_notes(self):
        """从tracking_notes中提取维度分数"""
        for r in self.results:
            notes = r['notes'] or ''
            # 提取 Dxx=yy 格式
            dims = re.findall(r'D(\d+)=(\d+\.?\d*)', notes)
            r['parsed_dims'] = {f'D{k}': float(v) for k, v in dims}
            
            # 提取 total=xx 格式
            total_match = re.search(r'total=(\d+\.?\d*)', notes)
            if total_match:
                r['parsed_total'] = float(total_match.group(1))
    
    def compute_dimension_ic(self):
        """计算每个维度的信息系数"""
        dim_values = defaultdict(list)
        
        for r in self.results:
            t1 = r['t1_change']
            if t1 is None:
                continue
            for dim, val in r.get('parsed_dims', {}).items():
                dim_values[dim].append((val, t1))
        
        ic_results = {}
        for dim, vals in dim_values.items():
            if len(vals) < 5:
                continue
            
            # 简单IC: 高于阈值 vs 低于阈值的T+1平均涨跌差
            avg_val = sum(v for v, _ in vals) / len(vals)
            hi = [(v, t) for v, t in vals if v >= avg_val]
            lo = [(v, t) for v, t in vals if v < avg_val]
            
            hi_avg = sum(t for _, t in hi) / len(hi) if hi else 0
            lo_avg = sum(t for _, t in lo) / len(lo) if lo else 0
            
            ic = hi_avg - lo_avg  # 正IC=维度有效
            accuracy = sum(1 for _, t in hi if t > 0) / len(hi) * 100 if hi else 0
            
            ic_results[dim] = {
                'IC': ic,
                'count': len(vals),
                'hi_avg': hi_avg,
                'lo_avg': lo_avg,
                'hi_accuracy': accuracy
            }
        
        return ic_results
    
    def find_optimal_combination(self, ic_results, top_n=5):
        """找最优维度组合"""
        sorted_dims = sorted(
            [(d, info) for d, info in ic_results.items() if info['IC'] > 0],
            key=lambda x: x[1]['IC'],
            reverse=True
        )
        
        return sorted_dims[:top_n]
    
    def simulate_strategy(self, top_dims):
        """模拟只用Top维度的策略"""
        top_dim_names = [d for d, _ in top_dims]
        
        results = {'total': 0, 'hit': 0, 'lim': 0, 'sum_chg': 0.0}
        
        for r in self.results:
            dims = r.get('parsed_dims', {})
            # 至少需要2个top维度
            active = sum(1 for d in top_dim_names if d in dims and dims[d] >= 5)
            if active < 2:
                continue
            
            results['total'] += 1
            if r['was_hit']: results['hit'] += 1
            if r['was_limit_up']: results['lim'] += 1
            results['sum_chg'] += r['t1_change'] or 0
        
        return results
    
    def generate_report(self, cats, dates, ic_results):
        """生成回测报告"""
        total = len(self.results)
        total_hit = sum(1 for r in self.results if r['was_hit'])
        total_lim = sum(1 for r in self.results if r['was_limit_up'])
        avg_chg = sum(r['t1_change'] or 0 for r in self.results) / max(total, 1)
        
        lines = []
        lines.append("=" * 80)
        lines.append("   V13.5.28 回测引擎 — 选股有效性分析报告")
        lines.append("=" * 80)
        lines.append(f"   总信号: {total} | 命中: {total_hit}({total_hit/max(total,1)*100:.1f}%)")
        lines.append(f"   涨停: {total_lim}({total_lim/max(total,1)*100:.1f}%) | 均T+1: {avg_chg:+.2f}%")
        lines.append("")
        
        # 按类别
        lines.append("--- 按信号类别 ---")
        for cat in sorted(cats.keys(), key=lambda c: -cats[c]['hit'] / max(cats[c]['total'], 1)):
            c = cats[cat]
            hit_rate = c['hit'] / max(c['total'], 1) * 100
            lim_rate = c['lim'] / max(c['total'], 1) * 100
            avg = c['sum_chg'] / max(c['total'], 1)
            stars = "★★★" if hit_rate >= 70 else ("★★" if hit_rate >= 50 else ("★" if hit_rate >= 40 else "  "))
            lines.append(f"  {stars} {cat:20s}: {c['total']:3d}信号 hit={c['hit']}/{c['total']}({hit_rate:.0f}%) lim={c['lim']}({lim_rate:.0f}%) avg={avg:+.2f}%")
        
        lines.append("")
        
        # 按日期
        lines.append("--- 按日期 ---")
        for d in sorted(dates.keys()):
            c = dates[d]
            hit_rate = c['hit'] / max(c['total'], 1) * 100
            avg = c['sum_chg'] / max(c['total'], 1)
            lines.append(f"  {d}: {c['total']:3d}信号 hit={c['hit']}({hit_rate:.0f}%) lim={c['lim']} avg={avg:+.2f}%")
        
        lines.append("")
        
        # 维度IC
        if ic_results:
            lines.append("--- 维度IC排名 (正IC=有效) ---")
            sorted_ic = sorted(ic_results.items(), key=lambda x: x[1]['IC'], reverse=True)
            for dim, info in sorted_ic[:15]:
                stars = "★★★" if info['IC'] > 2 else ("★★" if info['IC'] > 1 else "")
                lines.append(f"  {stars} {dim}: IC={info['IC']:+.2f}% count={info['count']} hi_acc={info['hi_accuracy']:.0f}% hi_avg={info['hi_avg']:+.2f}% lo_avg={info['lo_avg']:+.2f}%")
            
            # 噪音维度(IC<0)
            noise = [(d, i) for d, i in ic_results.items() if i['IC'] < 0]
            if noise:
                lines.append(f"\n  ❌ 噪音维度(IC<0, 建议删除):")
                for d, i in sorted(noise, key=lambda x: x[1]['IC']):
                    lines.append(f"     {d}: IC={i['IC']:+.2f}% (越打分越跌!)")
        
        lines.append("")
        
        # 最优策略
        if ic_results:
            top_dims = self.find_optimal_combination(ic_results, top_n=5)
            if top_dims:
                lines.append("--- 🏆 最优策略 (Top5维度且至少2个≥5) ---")
                for d, info in top_dims:
                    lines.append(f"  {d}: IC={info['IC']:+.2f}% 准确率={info['hi_accuracy']:.0f}%")
                
                sim = self.simulate_strategy(top_dims)
                if sim['total'] > 0:
                    sim_hit = sim['hit'] / sim['total'] * 100
                    sim_avg = sim['sum_chg'] / sim['total']
                    lines.append(f"\n  模拟结果: {sim['total']}信号 hit={sim_hit:.1f}% lim={sim['lim']} avg={sim_avg:+.2f}%")
                    lines.append(f"  对比全量: hit={total_hit/max(total,1)*100:.1f}%→{sim_hit:.1f}% (提升{sim_hit-total_hit/max(total,1)*100:+.1f}pp)")
        
        lines.append("")
        lines.append("=" * 80)
        lines.append("   🎯 行动建议")
        lines.append("=" * 80)
        
        if ic_results:
            noise_count = sum(1 for d, i in ic_results.items() if i['IC'] < 0)
            effective_count = sum(1 for d, i in ic_results.items() if i['IC'] > 0)
            lines.append(f"   有效维度: {effective_count} | 噪音维度: {noise_count}")
            if noise_count > effective_count:
                lines.append(f"   ⚠️ 噪音维度多于有效维度! 系统需要大幅精简!")
        
        if total_hit / max(total, 1) < 0.5:
            lines.append(f"   ⚠️ 总体命中率{total_hit/max(total,1)*100:.1f}%<50%, 系统需要根本性改进!")
        
        lines.append(f"   💡 建议: 只保留IC>0的维度, 每天最多输出5-8个信号, 聚焦高质量")
        
        return "\n".join(lines)
    
    def run(self, days_back=30):
        """执行完整回测"""
        print(f"📊 V13.5.28回测引擎启动, 回溯{days_back}天...")
        
        count = self.load_historical_signals(days_back)
        if count == 0:
            print("⚠️ 无历史信号数据! 请先运行V13.5.27生成信号")
            return None
        
        print(f"✅ 加载{count}条历史信号")
        
        # 提取维度
        self.extract_dimensions_from_notes()
        
        # 分析
        cats = self.analyze_by_category()
        dates = self.analyze_by_date()
        ic_results = self.compute_dimension_ic()
        
        # 生成报告
        report = self.generate_report(cats, dates, ic_results)
        
        # 保存
        today = datetime.now().strftime('%Y%m%d')
        report_path = os.path.join(OUTPUT_DIR, f'backtest_v13528_{today}.txt')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        
        print(f"\n✅ 报告已保存: {report_path}")
        print(report)
        
        return report


if __name__ == '__main__':
    engine = BacktestEngine()
    engine.run(days_back=30)
