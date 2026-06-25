#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  V13.2 TDX 批量回测引擎 — TDXBatchBacktest                           ║
║  ================================================================    ║
║  使用tdx_screener获取全市场涨停股，批量运行V13.2圣杯分析              ║
║                                                                      ║
║  核心功能：                                                           ║
║  1. 从tdx_screener结果提取涨停股列表（100+只）                       ║
║  2. 批量运行V13.2完整分析管线                                        ║
║  3. T+1验证：对比T日选股结果与T+1实际涨跌                            ║
║  4. KPI计算：涨停命中率/盈亏比/最大回撤/夏普比率                     ║
║  5. 生成回测报告（HTML交互式）                                       ║
║                                                                      ║
║  使用方式：                                                           ║
║  # 方式1: 从screener缓存文件加载                                     ║
║  python V13_2_TDX_BatchBacktest.py --screener-cache screener.json    ║
║                                                                      ║
║  # 方式2: 从TDX实时输入文件回测                                      ║
║  python V13_2_TDX_BatchBacktest.py --tdx-cache tdx_realtime_input.json║
║                                                                      ║
║  # 方式3: 生成Agent采集指令                                          ║
║  python V13_2_TDX_BatchBacktest.py --generate-instructions           ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import json
import os
import math
import sys
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════
# SECTION 0: 回测数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    """单只股票的回测结果"""
    code: str
    name: str = ''
    # T日数据
    t_change_pct: float = 0.0
    t_v132_score: float = 0.0
    t_m46_confidence: float = 0.0
    t_m57_composite: float = 0.0
    t_recommendation: str = ''
    # T+1数据
    t1_change_pct: float = 0.0  # T+1实际涨幅
    t1_open_pct: float = 0.0    # T+1开盘涨幅
    t1_high_pct: float = 0.0    # T+1最高涨幅
    t1_low_pct: float = 0.0     # T+1最低涨幅
    t1_close_pct: float = 0.0   # T+1收盘涨幅
    has_t1_data: bool = False
    # 结果判定
    is_limit_up: bool = False   # T+1是否涨停
    is_profitable: bool = False # T+1是否盈利（收盘>开盘）
    is_correct: bool = False    # 推荐BUY且T+1上涨
    profit_pct: float = 0.0     # T+1收益（开盘买入，收盘卖出）
    # 因子详情
    factors: Dict = field(default_factory=dict)


@dataclass
class BacktestStats:
    """回测统计"""
    total_stocks: int = 0
    recommended_count: int = 0       # 推荐BUY/STRONG_BUY的数量
    limit_up_count: int = 0          # T+1涨停数
    profitable_count: int = 0        # T+1盈利数
    correct_count: int = 0           # 正确推荐数
    # KPI
    hit_rate: float = 0.0            # 涨停命中率
    win_rate: float = 0.0            # 盈利率
    precision: float = 0.0           # 推荐精度
    avg_profit: float = 0.0          # 平均收益
    avg_loss: float = 0.0            # 平均亏损
    profit_loss_ratio: float = 0.0   # 盈亏比
    max_drawdown: float = 0.0        # 最大回撤
    sharpe_ratio: float = 0.0        # 夏普比率
    # 因子有效性
    factor_ic: Dict = field(default_factory=dict)  # 各因子的IC值


# ═══════════════════════════════════════════════════════════════
# SECTION 1: 批量回测引擎
# ═══════════════════════════════════════════════════════════════

class TDXBatchBacktest:
    """
    V13.2 TDX批量回测引擎

    从TDX缓存数据批量运行V13.2分析，并进行T+1验证
    """

    def __init__(self):
        self.results: List[BacktestResult] = []
        self.stats: BacktestStats = BacktestStats()

    def run_backtest(
        self,
        tdx_cache_path: str,
        t1_cache_path: str = None,
        stock_list: List[Dict] = None,
        verbose: bool = True,
    ) -> BacktestStats:
        """
        运行批量回测

        Args:
            tdx_cache_path: T日TDX数据缓存JSON
            t1_cache_path: T+1日TDX数据缓存JSON（用于验证）
            stock_list: 指定股票列表（None=使用缓存中所有股票）
            verbose: 是否打印详细日志

        Returns:
            BacktestStats
        """
        if verbose:
            print("=" * 70)
            print("  V13.2 TDX 批量回测引擎")
            print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"  T日数据: {tdx_cache_path}")
            if t1_cache_path:
                print(f"  T+1数据: {t1_cache_path}")
            print("=" * 70)

        # Step 1: 加载T日数据并运行V13.2分析
        from V13_2_TDX_RealtimeFeed import TDXRealtimeFeed

        feed = TDXRealtimeFeed()
        if not feed.load_from_cache(tdx_cache_path):
            print("[Backtest] T日数据加载失败")
            return self.stats

        if stock_list is None:
            stock_list = [{'code': c, 'name': s.name} for c, s in feed.stocks.items()]

        if verbose:
            print(f"\n[Step 1] T日数据加载完成: {len(stock_list)} 只")

        # 运行V13.2分析
        analysis = feed.run_full_analysis(stock_list, verbose=verbose)

        # Step 2: 加载T+1数据（如果有）
        t1_data = {}
        if t1_cache_path and os.path.exists(t1_cache_path):
            t1_feed = TDXRealtimeFeed()
            if t1_feed.load_from_cache(t1_cache_path):
                t1_data = t1_feed.stocks
                if verbose:
                    print(f"\n[Step 2] T+1数据加载完成: {len(t1_data)} 只")

        # Step 3: 构建回测结果
        self.results = []
        for r in analysis['results']:
            br = BacktestResult(
                code=r['code'],
                name=r.get('name', ''),
                t_change_pct=r.get('change_pct', 0),
                t_v132_score=r.get('v132_score', 0),
                t_m46_confidence=r.get('m46_confidence', 0),
                t_m57_composite=r.get('m57_composite', 0),
                t_recommendation=r.get('recommendation', ''),
                factors={k: v for k, v in r.items()
                        if k.startswith(('tail_', 'overnight_', 'intraday_', 'auction_',
                                        'sector_', 'streak_', 'flow_', 'gap_', 'event_',
                                        'lhb_', 'sentiment_'))},
            )

            # T+1验证
            if t1_data:
                t1_stock = t1_data.get(r['code'])
                if t1_stock:
                    t1_bars = t1_feed.get_daily_klines(r['code'], n=2)
                    if len(t1_bars) >= 2:
                        t_close = t1_bars[-2].get('close', 0)
                        t1_open = t1_bars[-1].get('open', 0)
                        t1_high = t1_bars[-1].get('high', 0)
                        t1_low = t1_bars[-1].get('low', 0)
                        t1_close = t1_bars[-1].get('close', 0)

                        if t_close > 0:
                            br.t1_open_pct = (t1_open / t_close - 1) * 100
                            br.t1_high_pct = (t1_high / t_close - 1) * 100
                            br.t1_low_pct = (t1_low / t_close - 1) * 100
                            br.t1_close_pct = (t1_close / t_close - 1) * 100
                            br.t1_change_pct = br.t1_close_pct
                            br.has_t1_data = True

                            # 判定
                            from V13_2_TDX_RealtimeFeed import infer_board_tier, get_limit_up_pct
                            board = infer_board_tier(r['code'])
                            limit_pct = get_limit_up_pct(board) * 100
                            br.is_limit_up = br.t1_close_pct >= limit_pct * 0.95
                            br.is_profitable = br.t1_close_pct > 0
                            br.profit_pct = br.t1_close_pct  # 简化：T日收盘买入，T+1收盘卖出
                            br.is_correct = br.t_recommendation in ('BUY', 'STRONG_BUY') and br.t1_close_pct > 0

            self.results.append(br)

        # Step 4: 计算统计
        self._compute_stats(verbose)

        return self.stats

    def run_backtest_from_screener(
        self,
        screener_cache_path: str,
        tdx_cache_path: str = None,
        verbose: bool = True,
    ) -> BacktestStats:
        """
        从tdx_screener结果运行回测

        Args:
            screener_cache_path: tdx_screener结果缓存JSON
            tdx_cache_path: 可选的TDX行情数据缓存
        """
        if not os.path.exists(screener_cache_path):
            print(f"[Backtest] screener缓存不存在: {screener_cache_path}")
            return self.stats

        with open(screener_cache_path, 'r', encoding='utf-8') as f:
            screener = json.load(f)

        # 提取涨停股列表
        stocks = []
        if isinstance(screener, dict):
            stock_list = screener.get('stocks', screener.get('data', []))
        elif isinstance(screener, list):
            stock_list = screener
        else:
            stock_list = []

        for s in stock_list:
            if isinstance(s, dict):
                code = str(s.get('code', s.get('Code', '')))
                if code:
                    stocks.append({
                        'code': code,
                        'name': s.get('name', s.get('Name', '')),
                        'consecutive_limit_up': s.get('consecutive_days', s.get('cts', 0)),
                        'seal_amount': s.get('seal_amount', s.get('fbc', 0)),
                        'reason': s.get('reason', s.get('ztreason', '')),
                    })

        if verbose:
            print(f"[Backtest] 从screener提取 {len(stocks)} 只涨停股")

        # 如果有TDX行情缓存，使用它
        if tdx_cache_path and os.path.exists(tdx_cache_path):
            return self.run_backtest(tdx_cache_path, stock_list=stocks, verbose=verbose)
        else:
            # 仅使用screener数据
            return self._run_screener_only(stocks, verbose)

    def _run_screener_only(self, stock_list: List[Dict], verbose: bool) -> BacktestStats:
        """仅使用screener数据运行简化回测"""
        if verbose:
            print(f"\n[Mode] 仅screener数据模式（无行情数据）")

        self.results = []
        for s in stock_list:
            br = BacktestResult(
                code=s['code'],
                name=s.get('name', ''),
                t_change_pct=10.0,  # 涨停股默认10%
                t_recommendation='BUY',  # 涨停股默认推荐
                factors={'streak_exp': s.get('consecutive_limit_up', 0)},
            )
            self.results.append(br)

        self._compute_stats(verbose)
        return self.stats

    def _compute_stats(self, verbose: bool = True):
        """计算回测统计"""
        s = self.stats
        s.total_stocks = len(self.results)

        # 推荐统计
        recommended = [r for r in self.results if r.t_recommendation in ('BUY', 'STRONG_BUY')]
        s.recommended_count = len(recommended)

        # T+1验证统计
        t1_verified = [r for r in self.results if r.has_t1_data]
        if t1_verified:
            s.limit_up_count = sum(1 for r in t1_verified if r.is_limit_up)
            s.profitable_count = sum(1 for r in t1_verified if r.is_profitable)
            s.correct_count = sum(1 for r in t1_verified if r.is_correct)

            # KPI
            s.hit_rate = s.limit_up_count / len(t1_verified)
            s.win_rate = s.profitable_count / len(t1_verified)
            s.precision = s.correct_count / max(1, len([r for r in t1_verified if r.t_recommendation in ('BUY', 'STRONG_BUY')]))

            # 盈亏比
            profits = [r.profit_pct for r in t1_verified if r.profit_pct > 0]
            losses = [abs(r.profit_pct) for r in t1_verified if r.profit_pct < 0]
            s.avg_profit = sum(profits) / len(profits) if profits else 0
            s.avg_loss = sum(losses) / len(losses) if losses else 0
            s.profit_loss_ratio = s.avg_profit / s.avg_loss if s.avg_loss > 0 else 0

            # 夏普比率
            returns = [r.profit_pct for r in t1_verified]
            if len(returns) >= 2:
                mean_ret = sum(returns) / len(returns)
                std_ret = math.sqrt(sum((r - mean_ret)**2 for r in returns) / len(returns))
                s.sharpe_ratio = mean_ret / std_ret if std_ret > 0 else 0

            # 最大回撤
            cum = 0
            peak = 0
            max_dd = 0
            for r in returns:
                cum += r
                if cum > peak:
                    peak = cum
                dd = peak - cum
                if dd > max_dd:
                    max_dd = dd
            s.max_drawdown = max_dd

        # 因子IC计算
        if t1_verified:
            for factor_name in ['t_v132_score', 't_m46_confidence', 't_m57_composite']:
                factor_vals = [getattr(r, factor_name) for r in t1_verified]
                ret_vals = [r.t1_change_pct for r in t1_verified]
                ic = self._compute_ic(factor_vals, ret_vals)
                s.factor_ic[factor_name] = ic

        if verbose:
            self._print_stats()

    def _compute_ic(self, factors: List[float], returns: List[float]) -> float:
        """计算因子IC（Spearman Rank相关系数的简化版）"""
        if len(factors) < 3:
            return 0.0

        # Pearson相关系数
        n = len(factors)
        mean_f = sum(factors) / n
        mean_r = sum(returns) / n

        cov = sum((f - mean_f) * (r - mean_r) for f, r in zip(factors, returns))
        var_f = sum((f - mean_f)**2 for f in factors)
        var_r = sum((r - mean_r)**2 for r in returns)

        if var_f == 0 or var_r == 0:
            return 0.0

        return round(cov / math.sqrt(var_f * var_r), 4)

    def _print_stats(self):
        """打印统计"""
        s = self.stats
        print(f"\n{'=' * 70}")
        print(f"  V13.2 批量回测结果")
        print(f"{'=' * 70}")
        print(f"\n  总股票数: {s.total_stocks}")
        print(f"  推荐BUY: {s.recommended_count}")

        t1_count = sum(1 for r in self.results if r.has_t1_data)
        if t1_count > 0:
            print(f"\n  T+1验证: {t1_count} 只有T+1数据")
            print(f"  ── KPI ──")
            print(f"  涨停命中率: {s.hit_rate*100:.1f}% ({s.limit_up_count}/{t1_count})")
            print(f"  盈利率:     {s.win_rate*100:.1f}% ({s.profitable_count}/{t1_count})")
            print(f"  推荐精度:   {s.precision*100:.1f}%")
            print(f"  平均盈利:   {s.avg_profit:.2f}%")
            print(f"  平均亏损:   {s.avg_loss:.2f}%")
            print(f"  盈亏比:     {s.profit_loss_ratio:.2f}")
            print(f"  最大回撤:   {s.max_drawdown:.2f}%")
            print(f"  夏普比率:   {s.sharpe_ratio:.4f}")

            if s.factor_ic:
                print(f"\n  ── 因子IC ──")
                for fname, ic in s.factor_ic.items():
                    print(f"  {fname:25s}: {ic:+.4f}")

        print(f"\n{'=' * 70}")

    # ═══════════════════════════════════════════════════════════════
    # SECTION 2: HTML报告生成
    # ═══════════════════════════════════════════════════════════════

    def generate_html_report(self, output_path: str = None) -> str:
        """生成交互式HTML回测报告"""
        if not output_path:
            output_path = f'V13_2_批量回测报告_{datetime.now().strftime("%Y%m%d_%H%M")}.html'

        s = self.stats

        # 准备数据
        top_results = sorted(self.results, key=lambda r: r.t_v132_score, reverse=True)[:20]

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>V13.2 TDX批量回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
       background: #0f1117; color: #e0e0e0; padding: 20px; }}
.header {{ text-align: center; padding: 30px 0; border-bottom: 1px solid #2a2d35; }}
.header h1 {{ font-size: 28px; color: #fff; margin-bottom: 8px; }}
.header .subtitle {{ color: #888; font-size: 14px; }}
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px; margin: 30px 0; }}
.kpi-card {{ background: #1a1d24; border: 1px solid #2a2d35; border-radius: 12px;
            padding: 20px; text-align: center; }}
.kpi-card .label {{ color: #888; font-size: 12px; text-transform: uppercase; margin-bottom: 8px; }}
.kpi-card .value {{ font-size: 32px; font-weight: 700; }}
.kpi-card .detail {{ color: #666; font-size: 11px; margin-top: 4px; }}
.kpi-red .value {{ color: #ef4444; }}
.kpi-green .value {{ color: #22c55e; }}
.kpi-blue .value {{ color: #3b82f6; }}
.kpi-yellow .value {{ color: #eab308; }}
.section {{ background: #1a1d24; border: 1px solid #2a2d35; border-radius: 12px;
           padding: 24px; margin: 20px 0; }}
.section h2 {{ font-size: 18px; color: #fff; margin-bottom: 16px; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; padding: 10px 12px; border-bottom: 2px solid #2a2d35;
     color: #888; font-size: 12px; text-transform: uppercase; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #1e2128; font-size: 13px; }}
tr:hover {{ background: #1e2128; }}
.positive {{ color: #ef4444; }}
.negative {{ color: #22c55e; }}
.recommendation {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.rec-strong-buy {{ background: #7f1d1d; color: #ef4444; }}
.rec-buy {{ background: #3b1d1d; color: #f87171; }}
.rec-watch {{ background: #1d2b1d; color: #fbbf24; }}
.rec-hold {{ background: #1d1d2b; color: #6b7280; }}
.chart-container {{ position: relative; height: 300px; margin: 20px 0; }}
</style>
</head>
<body>
<div class="header">
  <h1>V13.2 TDX 批量回测报告</h1>
  <div class="subtitle">
    生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
    总股票: {s.total_stocks} |
    推荐BUY: {s.recommended_count}
  </div>
</div>
"""

        # KPI卡片
        t1_count = sum(1 for r in self.results if r.has_t1_data)
        html += f"""
<div class="kpi-grid">
  <div class="kpi-card kpi-red"><div class="label">涨停命中率</div>
    <div class="value">{s.hit_rate*100:.1f}%</div>
    <div class="detail">{s.limit_up_count}/{t1_count} 只</div></div>
  <div class="kpi-card kpi-green"><div class="label">盈利率</div>
    <div class="value">{s.win_rate*100:.1f}%</div>
    <div class="detail">{s.profitable_count}/{t1_count} 只</div></div>
  <div class="kpi-card kpi-blue"><div class="label">盈亏比</div>
    <div class="value">{s.profit_loss_ratio:.2f}</div>
    <div class="detail">均盈{s.avg_profit:.1f}%/均亏{s.avg_loss:.1f}%</div></div>
  <div class="kpi-card kpi-yellow"><div class="label">夏普比率</div>
    <div class="value">{s.sharpe_ratio:.3f}</div>
    <div class="detail">最大回撤{s.max_drawdown:.1f}%</div></div>
</div>
"""

        # 推荐分布
        rec_counts = {'STRONG_BUY': 0, 'BUY': 0, 'WATCH': 0, 'HOLD': 0}
        for r in self.results:
            rec_counts[r.t_recommendation] = rec_counts.get(r.t_recommendation, 0) + 1

        html += f"""
<div class="section">
  <h2>推荐分布</h2>
  <div class="chart-container">
    <canvas id="recChart"></canvas>
  </div>
</div>
"""

        # 因子IC
        if s.factor_ic:
            ic_labels = list(s.factor_ic.keys())
            ic_values = list(s.factor_ic.values())
            html += f"""
<div class="section">
  <h2>因子IC（信息系数）</h2>
  <div class="chart-container">
    <canvas id="icChart"></canvas>
  </div>
</div>
"""

        # Top 20 结果表格
        html += """
<div class="section">
  <h2>Top 20 选股结果</h2>
  <table>
    <thead>
      <tr>
        <th>#</th><th>代码</th><th>名称</th><th>T日涨幅</th>
        <th>V13.2评分</th><th>M46</th><th>M57</th><th>推荐</th>
"""
        if t1_count > 0:
            html += "<th>T+1涨幅</th><th>结果</th>"
        html += """
      </tr>
    </thead>
    <tbody>
"""
        for i, r in enumerate(top_results, 1):
            change_class = 'positive' if r.t_change_pct > 0 else 'negative'
            rec_class = f'rec-{r.t_recommendation.lower().replace("_", "-")}'
            html += f"""
      <tr>
        <td>{i}</td>
        <td>{r.code}</td>
        <td>{r.name[:8]}</td>
        <td class="{change_class}">{r.t_change_pct:+.2f}%</td>
        <td>{r.t_v132_score:.4f}</td>
        <td>{r.t_m46_confidence:.4f}</td>
        <td>{r.t_m57_composite:+.4f}</td>
        <td><span class="recommendation {rec_class}">{r.t_recommendation}</span></td>
"""
            if t1_count > 0:
                if r.has_t1_data:
                    t1_class = 'positive' if r.t1_change_pct > 0 else 'negative'
                    result_text = '✅涨停' if r.is_limit_up else ('✅盈' if r.is_profitable else '❌亏')
                    html += f'<td class="{t1_class}">{r.t1_change_pct:+.2f}%</td><td>{result_text}</td>'
                else:
                    html += '<td>-</td><td>-</td>'
            html += "</tr>"

        html += """
    </tbody>
  </table>
</div>
"""

        # JavaScript
        html += f"""
<script>
const recData = {{
  labels: {list(rec_counts.keys())},
  datasets: [{{
    data: {list(rec_counts.values())},
    backgroundColor: ['#ef4444', '#f87171', '#fbbf24', '#6b7280'],
  }}]
}};
new Chart(document.getElementById('recChart'), {{
  type: 'doughnut',
  data: recData,
  options: {{ responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ position: 'right', labels: {{ color: '#e0e0e0' }} }} }} }}
}});

"""
        if s.factor_ic:
            html += f"""
const icData = {{
  labels: {ic_labels},
  datasets: [{{
    data: {ic_values},
    backgroundColor: {['#3b82f6' if v >= 0 else '#ef4444' for v in ic_values]},
  }}]
}};
new Chart(document.getElementById('icChart'), {{
  type: 'bar',
  data: icData,
  options: {{ responsive: true, maintainAspectRatio: false,
    scales: {{ y: {{ ticks: {{ color: '#888' }} }} }},
    plugins: {{ legend: {{ display: false }} }} }}
}});
"""
        html += "</script></body></html>"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        print(f"[Backtest] HTML报告已生成: {output_path}")
        return output_path


# ═══════════════════════════════════════════════════════════════
# SECTION 3: Agent回测指令生成
# ═══════════════════════════════════════════════════════════════

def generate_backtest_instructions(stock_count: int = 100) -> str:
    """生成Agent批量回测数据采集指令"""
    return f"""
# V13.2 批量回测 Agent数据采集指令

## 目标
获取 {stock_count}+ 只涨停股的T日和T+1完整TDX数据，用于V13.2批量回测验证

## Step 1: 获取涨停股列表
调用 tdx_screener(query="涨停", market="A股")
→ 预期返回 100+ 只涨停股，包含: 代码/名称/封单额/首次涨停时间/开板次数/涨停原因/连板天数/板别

## Step 2: 逐股获取T日数据（每只股）
对每只涨停股调用:
1. tdx_quotes(code="{code}", setcode="{setcode}") → 实时行情
2. tdx_kline(code="{code}", setcode="{setcode}", period=7, count=60) → 60日K线
3. tdx_kline(code="{code}", setcode="{setcode}", period=0, count=30) → 1分钟K线
4. tdx_api_data(code="{code}", setcode="{setcode}", fixedTag="zjlx") → 资金流向
5. tdx_api_data(code="{code}", setcode="{setcode}", fixedTag="jglhb") → 龙虎榜
6. tdx_api_data(code="{code}", setcode="{setcode}", fixedTag="ztfx") → 涨停分析

## Step 3: 保存为缓存JSON
将所有数据保存为以下格式:
```json
{{
  "fetch_time": "T日日期",
  "stocks": {{
    "CODE": {{
      "name": "名称",
      "setcode": "1",
      "quote": {{}},
      "kline": {{}},
      "kline_1min": {{}},
      "capital_flow": {{}},
      "dragon_tiger": {{}},
      "limit_up_analysis": {{}}
    }}
  }},
  "screener": {{}}
}}
```

## Step 4: T+1日获取验证数据
T+1日重复Step 2获取T+1日行情数据，保存为单独文件

## Step 5: 运行回测
python V13_2_TDX_BatchBacktest.py --tdx-cache t_day.json --t1-cache t1_day.json
"""


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='V13.2 TDX批量回测引擎')
    parser.add_argument('--tdx-cache', default='tdx_realtime_input.json', help='T日TDX缓存')
    parser.add_argument('--t1-cache', default=None, help='T+1日TDX缓存')
    parser.add_argument('--screener-cache', default=None, help='tdx_screener结果缓存')
    parser.add_argument('--html', action='store_true', help='生成HTML报告')
    parser.add_argument('--instructions', action='store_true', help='生成Agent采集指令')
    args = parser.parse_args()

    if args.instructions:
        print(generate_backtest_instructions())
        sys.exit(0)

    bt = TDXBatchBacktest()

    if args.screener_cache:
        bt.run_backtest_from_screener(args.screener_cache, args.tdx_cache)
    else:
        bt.run_backtest(args.tdx_cache, args.t1_cache)

    if args.html:
        bt.generate_html_report()
