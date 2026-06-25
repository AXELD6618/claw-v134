#!/usr/bin/env python3
"""
V13.0 P0 全流程编排脚本
========================
三线并行执行框架：
  P0-1: 50+股真实K线回测 + M46精度校准
  P0-2: 14:30 TDX实盘数据注入
  P0-3: 监控池 301只行业分层轮换

数据流:
  PoolManager(P0-3) → dynamic_watchlist.json
         ↓
  TDX MCP → TdxInjector(P0-2) → tdx_realtime_input.json
         ↓
  run_tail_market_1430.py (L1→L4, M46/M51/M54)
         ↓
  BulkBacktest(P0-1) → 命中率/盈亏比/踩雷率 + M46校准

自动化调度建议:
  08:30 P0-3: python run_p0_all.py --phase 3    (生成当日监控池)
  14:20 P0-2: python run_p0_all.py --phase 2    (拉取TDX实时数据)
  14:30 P0-1: python run_p0_all.py --phase 1    (尾盘猎手+回测)

使用方式:
  python run_p0_all.py                  # 全流程
  python run_p0_all.py --phase 3        # 仅P0-3
  python run_p0_all.py --phase 2        # 仅P0-2
  python run_p0_all.py --phase 1        # 仅P0-1
  python run_p0_all.py --pool-size 200   # 自定义池大小
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime
from typing import Dict, List, Optional

# ═══════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════

DATA_DIR = 'data'
OUTPUT_DIR = 'outputs'


class P0Orchestrator:
    """P0三线编排器"""

    def __init__(self, pool_size: int = 150, verbose: bool = True):
        self.pool_size = pool_size
        self.verbose = verbose
        self.pool_data: Optional[dict] = None
        self.tdx_data: Optional[dict] = None
        self.backtest_report: Optional[dict] = None
        self.calibration: Optional[dict] = None
        self.start_time = time.time()
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    def _log(self, msg: str, level: str = "INFO"):
        if self.verbose:
            elapsed = time.time() - self.start_time
            print(f"[P0 {level}] [+{elapsed:5.1f}s] {msg}")

    # ═══════════════════════════════════════════════
    # Phase 3: 监控池分层轮换 (每日08:30)
    # ═══════════════════════════════════════════════

    def run_phase_3(self) -> dict:
        """
        P0-3: 300只行业分层监控池日频生成

        Returns:
            {pool, json_path, rotation_group, pool_size}
        """
        self._log("=" * 50)
        self._log("P0-3: 监控池日频轮换 → 开始")

        try:
            from V13_0_P0_PoolManager import PoolManager

            mgr = PoolManager()
            mgr.load_all_stocks()
            pool = mgr.build_daily_pool(rotate=True, pool_size=self.pool_size)
            json_path = mgr.export_to_json(pool)

            self.pool_data = {
                'pool': pool,
                'json_path': json_path,
                'rotation_group': mgr.current_group,
                'pool_size': len(pool),
                'industry_summary': mgr.get_industry_summary(),
            }

            self._log(f"P0-3: ✅ 完成 | 轮换组={mgr.current_group} | {len(pool)}只")
            return self.pool_data

        except Exception as e:
            self._log(f"P0-3: ❌ 失败: {e}", "ERROR")
            return {'error': str(e)}

    # ═══════════════════════════════════════════════
    # Phase 2: TDX实盘数据注入 (每日14:20)
    # ═══════════════════════════════════════════════

    def run_phase_2(self) -> dict:
        """
        P0-2: TDX实时数据注入

        注意：在WorkBuddy环境中，实际TDX数据由Agent通过MCP工具拉取。
        此方法准备注入框架并尝试读取已有缓存。

        Returns:
            {injector, tasks, cache_exists, ...}
        """
        self._log("=" * 50)
        self._log("P0-2: TDX实盘数据注入 → 开始")

        try:
            from V13_0_P0_TDXInjector import TdxRealtimeInjector

            injector = TdxRealtimeInjector()

            # 检查是否已有TDX缓存
            cache_path = os.path.join(DATA_DIR, 'tdx_realtime_input.json')
            cache_exists = os.path.exists(cache_path)
            cache_age = None
            if cache_exists:
                cache_age = time.time() - os.path.getmtime(cache_path)

            # 获取需要拉取的任务列表
            tasks = injector.get_watchlist_tasks(top_n=60)

            self.tdx_data = {
                'injector': injector,
                'tasks': tasks,
                'task_count': len(tasks),
                'cache_exists': cache_exists,
                'cache_age_sec': round(cache_age, 0) if cache_age else None,
                'cache_path': cache_path,
            }

            if cache_exists and cache_age and cache_age < 300:
                self._log(f"P0-2: ✅ 缓存有效 (age={cache_age:.0f}s), 跳过注入")
            elif cache_exists:
                self._log(f"P0-2: ⚠️ 缓存过期 (age={cache_age:.0f}s), 需要刷新")
            else:
                self._log(f"P0-2: ⚠️ 无缓存, 需要Agent调用TDX MCP拉取数据")

            # 输出任务供Agent消费
            if not (cache_exists and cache_age and cache_age < 300):
                self._log(f"P0-2: 📋 待TDX拉取: {len(tasks)}只")
                print(f"\n  ═══ Agent操作指南 ═══")
                print(f"  from V13_0_P0_TDXInjector import TdxRealtimeInjector")
                print(f"  injector = TdxRealtimeInjector()")
                print(f"  # Agent遍历tasks调用TDX MCP:")
                print(f"  for code, setcode, name, ind in tasks:")
                print(f"      quote = tdx_quotes(code=code, setcode=setcode)")
                print(f"      kline = tdx_kline(code=code, setcode=setcode, period='4', wantNum='60')")
                print(f"      injector.inject_stock_complete(code, setcode, name, ind, quote, kline)")
                print(f"  injector.save_cache()")
                print(f"  ═══════════════════════\n")

            return self.tdx_data

        except Exception as e:
            self._log(f"P0-2: ❌ 失败: {e}", "ERROR")
            return {'error': str(e)}

    # ═══════════════════════════════════════════════
    # Phase 1: 尾盘猎手 + 回测 (每日14:30)
    # ═══════════════════════════════════════════════

    def run_phase_1(self) -> dict:
        """
        P0-1: 14:30尾盘猎手 + 真实K线回测

        1. 触发 run_tail_market_1430.py (消费 tdx_realtime_input.json)
        2. 运行真实K线回测 (消费TDX K线缓存)
        3. M46参数校准
        """
        self._log("=" * 50)
        self._log("P0-1: 14:30尾盘猎手 + 回测 → 开始")

        result = {
            'tail_hunter': None,
            'backtest': None,
            'calibration': None,
            'unified_score': 0,
        }

        # ── Step 1: 14:30尾盘猎手 ──
        cache_path = os.path.join(DATA_DIR, 'tdx_realtime_input.json')
        if os.path.exists(cache_path):
            try:
                self._log("执行14:30尾盘猎手 (run_tail_market_1430.py)")
                import subprocess
                proc = subprocess.run(
                    [sys.executable, 'run_tail_market_1430.py'],
                    capture_output=True, text=True, timeout=120,
                )
                result['tail_hunter'] = {
                    'success': proc.returncode == 0,
                    'stdout': proc.stdout[-2000:] if proc.stdout else '',
                    'stderr': proc.stderr[-500:] if proc.stderr else '',
                }

                # 读取JSON输出
                today = datetime.now().strftime('%Y-%m-%d')
                tail_path = os.path.join(DATA_DIR, f'tail_market_{today}.json')
                if os.path.exists(tail_path):
                    with open(tail_path, 'r', encoding='utf-8') as f:
                        tail_data = json.load(f)
                    buy_count = tail_data.get('buy_signals', 0)
                    self._log(f"14:30猎手完成: {buy_count}条买入信号")
                    result['tail_hunter']['buy_signals'] = buy_count
                    result['tail_hunter']['json_path'] = tail_path
                else:
                    self._log("⚠️ 14:30猎手未产出JSON文件", "WARN")

            except Exception as e:
                self._log(f"14:30猎手执行失败: {e}", "ERROR")
                result['tail_hunter'] = {'success': False, 'error': str(e)}
        else:
            self._log("⚠️ 无TDX数据缓存，跳过14:30猎手", "WARN")
            result['tail_hunter'] = {'success': False, 'reason': 'no_tdx_cache'}

        # ── Step 2: 真实K线回测 ──
        try:
            from V13_0_P0_BulkBacktest import RealBacktestEngine, P0BacktestConfig

            engine = RealBacktestEngine(P0BacktestConfig(num_stocks=50))

            # 尝试从TDX缓存加载K线数据
            if os.path.exists(cache_path):
                with open(cache_path, 'r', encoding='utf-8') as f:
                    tdx_cache = json.load(f)
                stocks = tdx_cache.get('stocks', {})

                # 构造K线数据注入
                loaded = 0
                for code, sdata in stocks.items():
                    klines = sdata.get('daily_klines', [])
                    if len(klines) < 20:
                        continue

                    # 构造TDX兼容格式
                    kline_obj = {
                        'Code': code,
                        'ListHead': {'ItemHead': ['Data','Second','Open','High','Low','Close','Amount','VolInStock','Volume','Settle','up','down']},
                        'ListItem': [
                            {'Item': [
                                f'202606{20+i//10}{i%10:02d}', '0',
                                str(k.get('o', 0)), str(k.get('h', 0)),
                                str(k.get('l', 0)), str(k.get('c', 0)),
                                str(k.get('a', 0)), str(k.get('a', 0)),
                                str(k.get('v', 0)), '0', '0', '0'
                            ]}
                            for i, k in enumerate(klines)
                        ],
                        'AttachInfo': {
                            'Name': sdata.get('name', code),
                            'Now': sdata.get('now', klines[-1].get('c', 0) if klines else 0),
                            'Close': sdata.get('prev_close', 0),
                        }
                    }

                    ind = sdata.get('industry', '通用')
                    engine.load_stock(code, kline_obj, ind)
                    loaded += 1
                    if loaded >= 50:
                        break

                self._log(f"回测加载: {loaded}只实盘K线数据")

                # 运行回测
                report = engine.run(verbose=False)
                report_path = engine.export_report(report)

                result['backtest'] = {
                    'loaded': loaded,
                    'total_signals': report.get('total_signals', 0),
                    'hit_rate': report.get('hit_rate', 0),
                    'hit_count': report.get('hit_count', 0),
                    'trap_rate': report.get('trap_rate', 0),
                    'avg_plr': report.get('avg_positive_plr', 0),
                    'report_path': report_path,
                }

                self._log(f"回测完成: {result['backtest']['total_signals']}信号 "
                         f"命中率={result['backtest']['hit_rate']:.1%}")

                # ── Step 3: M46校准 ──
                if result['backtest']['total_signals'] >= 20:
                    self._log("运行M46参数校准...")
                    calibration = engine.run_calibration()
                    if calibration and calibration.get('best_params'):
                        result['calibration'] = {
                            'best_params': calibration['best_params'],
                            'best_score': calibration['best_score'],
                            'hit_rate_after': calibration.get('best_detail', {}).get('hit_rate_overall', 0),
                        }
                        self._log(f"M46校准完成: 最优命中率={result['calibration']['hit_rate_after']:.1%}")

            else:
                self._log("无TDX缓存，跳过回测", "WARN")
                result['backtest'] = {'loaded': 0, 'reason': 'no_tdx_cache'}

        except Exception as e:
            self._log(f"回测执行失败: {e}", "ERROR")
            result['backtest'] = {'error': str(e)}

        # ── 统一评分 ──
        hit_rate = result.get('backtest', {}).get('hit_rate', 0)
        trap_rate = result.get('backtest', {}).get('trap_rate', 0)
        plr = result.get('backtest', {}).get('avg_plr', 0)

        result['unified_score'] = round(
            hit_rate * 40 + (1 - trap_rate) * 30 + min(plr, 10) / 10 * 30, 1
        )

        self._log(f"P0-1: ✅ 完成 | 统一评分={result['unified_score']:.1f}")
        return result

    # ═══════════════════════════════════════════════
    # 全流程
    # ═══════════════════════════════════════════════

    def run_all(self) -> dict:
        """执行全流程 P0-3 → P0-2 → P0-1"""
        self._log("=" * 60)
        self._log("V13.0 P0 全流程启动")
        self._log("=" * 60)

        # P0-3: 监控池
        phase3 = self.run_phase_3()

        # P0-2: TDX注入
        phase2 = self.run_phase_2()

        # P0-1: 尾盘猎手+回测
        phase1 = self.run_phase_1()

        # 汇总报告
        summary = {
            'timestamp': datetime.now().isoformat(),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'elapsed_sec': round(time.time() - self.start_time, 1),
            'phases': {
                'P0-3_监控池': {
                    'status': 'completed' if phase3 and 'error' not in phase3 else 'failed',
                    'pool_size': phase3.get('pool_size', 0) if phase3 else 0,
                    'rotation_group': phase3.get('rotation_group', 'N/A') if phase3 else 'N/A',
                },
                'P0-2_TDX注入': {
                    'status': 'completed' if phase2 and 'error' not in phase2 else 'pending',
                    'cache_ready': phase2.get('cache_exists', False) if phase2 else False,
                },
                'P0-1_回测': {
                    'status': 'completed' if phase1 and 'error' not in phase1.get('backtest', {}) else 'pending',
                    'hit_rate': phase1.get('backtest', {}).get('hit_rate', 0) if phase1 else 0,
                    'unified_score': phase1.get('unified_score', 0) if phase1 else 0,
                },
            },
            'KPI': {
                'hit_rate_target': 0.70,
                'hit_rate_actual': phase1.get('backtest', {}).get('hit_rate', 0) if phase1 else 0,
                'trap_rate_actual': phase1.get('backtest', {}).get('trap_rate', 0) if phase1 else 0,
                'plr_actual': phase1.get('backtest', {}).get('avg_plr', 0) if phase1 else 0,
                'unified_score': phase1.get('unified_score', 0) if phase1 else 0,
            },
        }

        # 导出汇总
        summary_path = os.path.join(OUTPUT_DIR, f'p0_summary_{datetime.now().strftime("%Y-%m-%d")}.json')
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

        # 打印汇总
        self._print_summary(summary, summary_path)

        return summary

    def _print_summary(self, summary: dict, path: str):
        """打印全流程汇总"""
        print("\n" + "=" * 70)
        print("  📊 V13.0 P0 全流程执行报告")
        print("=" * 70)
        print(f"  日期: {summary['date']} | 耗时: {summary['elapsed_sec']:.0f}秒")
        print("-" * 70)

        for phase_name, phase_info in summary['phases'].items():
            status_icon = "✅" if phase_info['status'] == 'completed' else "⏳"
            details = ", ".join(f"{k}={v}" for k, v in phase_info.items() if k != 'status')
            print(f"  {status_icon} {phase_name}: {phase_info['status']}" +
                  (f" ({details})" if details else ""))

        print("-" * 70)
        kpi = summary.get('KPI', {})
        print(f"  🎯 命中率: {kpi.get('hit_rate_actual', 0):.1%} / 目标 70%")
        print(f"  🚫 踩雷率: {kpi.get('trap_rate_actual', 0):.1%}")
        print(f"  💰 盈亏比: {kpi.get('plr_actual', 0):.1f}")
        print(f"  📊 综合评分: {kpi.get('unified_score', 0):.1f}")
        print("-" * 70)
        print(f"  📁 汇总报告: {path}")
        print("=" * 70)


# ═══════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description='V13.0 P0 全流程编排')
    parser.add_argument('--phase', type=int, choices=[1, 2, 3], default=123,
                        help='指定执行阶段 (1=P0-1, 2=P0-2, 3=P0-3, 默认全流程)')
    parser.add_argument('--pool-size', type=int, default=150,
                        help='监控池大小 (默认150)')
    parser.add_argument('--quiet', action='store_true', help='静默模式')

    args = parser.parse_args()

    orch = P0Orchestrator(pool_size=args.pool_size, verbose=not args.quiet)

    if args.phase == 3:
        orch.run_phase_3()
    elif args.phase == 2:
        orch.run_phase_2()
    elif args.phase == 1:
        orch.run_phase_1()
    else:
        # 全流程
        orch.run_all()


if __name__ == '__main__':
    main()
