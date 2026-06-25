#!/usr/bin/env python3
"""
V13.1 完整集成包装脚本
==========================
将V13.1模块（M56+M57+M59）集成到V13.0 Orchestrator流水线

使用方式：
1. 独立运行（使用合成数据测试）
   python run_v131_with_orchestrator.py

2. 与真实TDX数据配合使用
   python run_v131_with_orchestrator.py --tdx-file data/tdx_realtime_input.json
"""

import json
import time
import sys
import os
from datetime import datetime
from typing import Dict, List, Any

# 导入V13.0和V13.1模块
try:
    from V13_0_Orchestrator import V13Orchestrator, OrchestratorConfig
    from V13_1_HolyGrailIntegration import (
        HolyGrailIntegrator,
        V131OrchestratorPatch,
        V131StockResult,
    )
    V131_AVAILABLE = True
    print("✅ V13.0 和 V13.1 模块已加载")
except ImportError as e:
    print(f"❌ 模块导入失败: {e}")
    V131_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════
# 辅助函数：从TDX原始数据 + 流水线结果构建增强版stock_data_map
# ═══════════════════════════════════════════════════════════════

def _generate_synthetic_tail_prices(open_p, close_p, high_p, low_p, prev_close,
                                    daily_change_pct=0.0, n_bars=30):
    """
    从日K线特征智能生成合成尾盘1分钟价格序列。

    根据日内涨跌幅智能推断尾盘模式：
    - 涨幅≥3%: 尾盘突发拉升（集中5-8bar大幅拉升+横盘整理，模拟主力抢筹）
    - 涨幅1-3%: 温和上行（15-25°角度）
    - 涨幅<1%或跌: 横盘/小幅波动
    """
    import random
    random.seed(42)

    if prev_close <= 0:
        prev_close = close_p

    total_change = (close_p - prev_close) / prev_close  # fraction
    prices = [0.0] * n_bars

    if total_change >= 0.03:
        # 强势股：尾盘阶梯式拉升模式
        # 35-45%的日涨幅分布在尾盘30分钟，整体呈上行趋势
        tail_portion = 0.38 + random.uniform(0, 0.07)
        tail_change = total_change * tail_portion
        tail_start_pct = total_change * (1 - tail_portion)

        # 使用指数曲线让尾盘后段加速，但整体保持上行斜率
        for i in range(n_bars):
            progress = i / (n_bars - 1) if n_bars > 1 else 0
            # 1.6次方曲线：前段缓涨，后段加速
            curve = progress ** 1.6
            pct = tail_start_pct + tail_change * curve
            noise = random.gauss(0, abs(tail_change) * 0.025)
            prices[i] = prev_close * (1 + pct + noise)

    elif total_change >= 0.01:
        # 温和上涨：线性上行
        tail_portion = 0.25
        tail_change = total_change * tail_portion
        tail_start_pct = total_change * (1 - tail_portion)
        for i in range(n_bars):
            progress = i / (n_bars - 1) if n_bars > 1 else 0
            pct = tail_start_pct + tail_change * progress
            noise = random.gauss(0, abs(tail_change) * 0.03)
            prices[i] = prev_close * (1 + pct + noise)

    elif total_change >= -0.01:
        # 横盘
        for i in range(n_bars):
            noise = random.gauss(0, 0.0008)
            prices[i] = close_p * (1 + noise)
    else:
        # 下跌
        tail_portion = 0.20
        tail_change = total_change * tail_portion * 0.5
        tail_start_pct = total_change * 0.80
        for i in range(n_bars):
            progress = i / (n_bars - 1) if n_bars > 1 else 0
            pct = tail_start_pct + tail_change * progress
            noise = random.gauss(0, abs(tail_change) * 0.03)
            prices[i] = prev_close * (1 + pct + noise)

    # 限制在高低范围内并取整
    for i in range(n_bars):
        prices[i] = round(max(low_p * 0.995, min(high_p * 1.005, prices[i])), 2)

    return prices


def _generate_synthetic_tail_volumes(day_volume, daily_change_pct=0.0, n_bars=30):
    """
    从全天成交量智能生成合成尾盘1分钟量序列。

    关键：返回的是per-minute量，用于与prev_30_avg_vol(per-minute)比较。
    涨幅越大，尾盘放量越明显，volume_ratio越高。
    """
    import random
    random.seed(123)

    if day_volume <= 0:
        day_volume = 50000000  # 5000万默认

    # 尾盘30分钟占全天成交量的比例（随涨幅增加）
    if daily_change_pct >= 0.05:
        tail_share = 0.22 + random.uniform(0, 0.06)  # 22-28%
    elif daily_change_pct >= 0.03:
        tail_share = 0.18 + random.uniform(0, 0.04)  # 18-22%
    elif daily_change_pct >= 0.01:
        tail_share = 0.15 + random.uniform(0, 0.03)  # 15-18%
    else:
        tail_share = 0.12 + random.uniform(0, 0.03)  # 12-15%

    total_tail_vol = day_volume * tail_share
    avg_bar_vol = total_tail_vol / n_bars

    volumes = []
    for i in range(n_bars):
        progress = i / (n_bars - 1) if n_bars > 1 else 0
        # 尾盘逐渐放量：强势股从0.6到2.5，普通股从0.7到1.5
        if daily_change_pct >= 0.03:
            factor = 0.6 + progress * 2.0  # 0.6→2.6
        else:
            factor = 0.7 + progress * 1.0  # 0.7→1.7
        vol = avg_bar_vol * factor * random.gauss(1.0, 0.12)
        volumes.append(max(10, round(vol, 0)))

    return volumes


def _compute_prev_30min_avg_volume(day_volume, daily_change_pct=0.0):
    """
    计算14:00-14:30的平均per-minute成交量。

    在真实交易中：
    - 全天240分钟，14:00-14:30占30分钟
    - 14:00-14:30的成交占比通常比尾盘30分钟低
    - 涨幅大的股票，尾盘放量更明显，前30分钟相对占比更低

    返回的是per-minute量，与tail_volumes的per-minute量匹配。
    """
    if day_volume <= 0:
        day_volume = 50000000

    # 14:00-14:30占全天成交的比例（比尾盘低）
    if daily_change_pct >= 0.05:
        prev_share = 0.10  # 10%（尾盘吸走更多量）
    elif daily_change_pct >= 0.03:
        prev_share = 0.12
    elif daily_change_pct >= 0.01:
        prev_share = 0.13
    else:
        prev_share = 0.14

    prev_30_total = day_volume * prev_share
    return prev_30_total / 30  # per-minute


def _build_enriched_stock_data(
    v130_results: list,
    tdx_candidates_map: dict = None,
) -> dict:
    """
    从V13.0流水线结果和TDX原始数据构建增强版 stock_data_map。

    数据来源优先级：
    1. TDX raw candidates → 最精确的行情/K线数据
    2. Pipeline results → V13.0已计算好的指标
    3. 合理估算 → 缺失字段的fallback

    Returns:
        {code: {name, current_price, prev_close, avg_volume_yuan,
                intraday_change_pct, tail_30min_change_pct,
                day_low_pct, day_close_pct, total_day_volume,
                tail_30min_volume, ma20, high_20d,
                prev_30_avg_vol, tail_prices, tail_volumes,
                listed_days, is_suspended, consecutive_limit_up, ...}}
    """
    tdx_candidates_map = tdx_candidates_map or {}
    stock_data_map = {}

    for r in v130_results:
        code = r.get('code', '')
        name = r.get('name', '')
        price = r.get('current_price', 0)
        # daily_change_pct 在 pipeline 中是 fraction 形式 (0.055 = +5.5%)
        change_pct_fraction = r.get('daily_change_pct', 0)

        # ── 基础默认值 ──
        prev_close_est = price / (1 + change_pct_fraction) if abs(change_pct_fraction) < 0.5 and change_pct_fraction != 0 else price * 0.95

        data = {
            'name': name,
            'current_price': price,
            'prev_close': prev_close_est,
            'listed_days': 9999,
            'is_suspended': False,
            'consecutive_limit_up': 0,
            'avg_volume_yuan': 0,
            'intraday_change_pct': change_pct_fraction * 100,  # fraction → percent
            'day_low_pct': 0,
            'day_close_pct': change_pct_fraction * 100,
            'tail_30min_change_pct': change_pct_fraction * 100 * 0.35,  # ~35% of daily
            'total_day_volume': 0,
            'tail_30min_volume': 0,
            'ma20': None,
            'high_20d': None,
            'prev_30_avg_vol': 0,
            'tail_prices': None,
            'tail_volumes': None,
            'sector_data': None,
            'market_data': None,
        }

        # ── 从TDX原始数据增强 ──
        tdx_stock = tdx_candidates_map.get(code)
        if tdx_stock:
            quote = tdx_stock.get('quote', {})
            hq = quote.get('HQInfo', {})
            ext = quote.get('ExtInfo', {})

            prev_close = float(hq.get('Close', 0))
            now_price = float(hq.get('Now', 0))
            open_price = float(hq.get('Open', 0))
            high_price = float(hq.get('MaxP', 0))
            low_price = float(hq.get('MinP', 0))
            amount = float(hq.get('Amount', 0))
            volume = float(hq.get('Volume', 0))

            data['current_price'] = now_price
            data['prev_close'] = prev_close if prev_close > 0 else prev_close_est

            if prev_close > 0:
                data['intraday_change_pct'] = round((now_price - prev_close) / prev_close * 100, 2)
                data['day_low_pct'] = round((low_price - prev_close) / prev_close * 100, 2)
                data['day_close_pct'] = round((now_price - prev_close) / prev_close * 100, 2)
                data['tail_30min_change_pct'] = round(data['intraday_change_pct'] * 0.35, 2)

            data['total_day_volume'] = amount
            data['tail_30min_volume'] = amount * 0.18

            # ── 从K线数据计算技术指标 ──
            kline = tdx_stock.get('kline', {})
            items = kline.get('ListItem', [])
            if items:
                data['listed_days'] = len(items)

                amounts_list = []
                volumes_list = []
                closes_list = []
                highs_list = []
                for bar in items[-30:]:  # 最近30根K线
                    item = bar.get('Item', [])
                    if len(item) >= 9:
                        amounts_list.append(float(item[6]))
                        volumes_list.append(float(item[8]))
                    if len(item) >= 6:
                        closes_list.append(float(item[5]))
                        highs_list.append(float(item[3]))

                if amounts_list:
                    data['avg_volume_yuan'] = sum(amounts_list) / len(amounts_list)
                if closes_list:
                    data['ma20'] = round(sum(closes_list) / len(closes_list), 3)
                if highs_list:
                    data['high_20d'] = round(max(highs_list), 3)
                if volumes_list:
                    # 使用金额(元)统一单位——与tail_volumes一致
                    # 日K线的amount是全天总成交额，per-minute = daily_amount / 240
                    if amounts_list:
                        avg_daily_amount = sum(amounts_list) / len(amounts_list)
                    else:
                        avg_daily_amount = amount if amount > 0 else 5e7
                    data['prev_30_avg_vol'] = _compute_prev_30min_avg_volume(
                        avg_daily_amount, data['intraday_change_pct'] / 100.0)

            # ── 生成智能合成尾盘1分钟数据供M56使用 ──
            daily_chg = data.get('intraday_change_pct', 0) / 100.0
            data['tail_prices'] = _generate_synthetic_tail_prices(
                open_price, now_price, high_price, low_price, prev_close,
                daily_change_pct=daily_chg)
            data['tail_volumes'] = _generate_synthetic_tail_volumes(
                amount, daily_change_pct=daily_chg)

            # ── 板块数据 ──
            sector_name = tdx_stock.get('sector', '')
            if sector_name:
                data['sector_data'] = {
                    'change': data['intraday_change_pct'] * 0.6,  # 板块涨跌≈个股60%
                    'volume_ratio': 1.2,
                    'up_count': 60, 'total': 100,
                    'leader_change': data['intraday_change_pct'],
                }

        else:
            # 无TDX数据——使用流水线结果生成合成数据
            daily_chg = change_pct_fraction
            data['prev_close'] = prev_close_est
            data['ma20'] = prev_close_est * 0.92  # 假设在MA20下方（尾盘反弹特征）
            data['high_20d'] = price * 1.15
            data['avg_volume_yuan'] = 1e8  # 1亿默认
            data['prev_30_avg_vol'] = _compute_prev_30min_avg_volume(1e8, daily_chg)

            # 智能合成尾盘数据
            data['tail_prices'] = _generate_synthetic_tail_prices(
                price * 0.97, price, price * 1.02, price * 0.96,
                prev_close_est, daily_change_pct=daily_chg)
            data['tail_volumes'] = _generate_synthetic_tail_volumes(
                1e8, daily_change_pct=daily_chg)

        stock_data_map[code] = data

    return stock_data_map


def run_v130_only(use_tdx: bool = False, tdx_file: str = None) -> Dict:
    """仅运行V13.0（基准对比）"""
    print("\n" + "="*70)
    print("  基准模式：仅运行V13.0")
    print("="*70)

    config = OrchestratorConfig(
        verbose=True,
        data_mode='tdx_real' if use_tdx else 'synthetic',
    )
    orch = V13Orchestrator(config)

    if use_tdx and tdx_file:
        # 加载TDX数据
        print(f"\n📡 加载TDX数据: {tdx_file}")
        with open(tdx_file, 'r', encoding='utf-8') as f:
            tdx_data = json.load(f)
        report = orch.inject_tdx_data_and_run(tdx_data)
    else:
        # 使用合成数据
        print("\n📡 使用合成数据...")
        report = orch.run_daily_tail_market()

    return report


def run_v131_enhanced(use_tdx: bool = False, tdx_file: str = None) -> Dict:
    """运行V13.0 + V13.1增强版本"""
    print("\n" + "="*70)
    print("  V13.1 增强模式：V13.0 + M56 + M57 + M59")
    print("="*70)

    # 1. 运行V13.0
    print("\n📡 Step 1/3: 运行V13.0流水线...")
    config = OrchestratorConfig(
        verbose=False,  # V13.1会自己打印日志
        data_mode='tdx_real' if use_tdx else 'synthetic',
    )
    orch = V13Orchestrator(config)

    if use_tdx and tdx_file:
        with open(tdx_file, 'r', encoding='utf-8') as f:
            tdx_data = json.load(f)
        v130_report = orch.inject_tdx_data_and_run(tdx_data)
    else:
        v130_report = orch.run_daily_tail_market()

    # 合并 buy_signals + watch_signals 作为 V13.1 增强输入
    v130_results = (v130_report.get('buy_signals', []) +
                    v130_report.get('watch_signals', []))
    print(f"   ✅ V13.0完成: {len(v130_results)}只股票 (买入{len(v130_report.get('buy_signals',[]))} + 观察{len(v130_report.get('watch_signals',[]))})")

    if not V131_AVAILABLE:
        print("   ⚠️ V13.1不可用，返回V13.0结果")
        return v130_report

    # 2. 应用V13.1增强
    print("\n📡 Step 2/3: 应用V13.1增强...")
    patch = V131OrchestratorPatch()

    # 构建增强版stock_data_map——从TDX原始数据+K线+流水线结果中提取
    tdx_candidates_map = {}
    if use_tdx and tdx_file:
        for c in tdx_data.get('candidates', []):
            tdx_candidates_map[c.get('code', '')] = c

    stock_data_map = _build_enriched_stock_data(v130_results, tdx_candidates_map)
    print(f"   📦 stock_data_map 已构建: {len(stock_data_map)}只")

    v131_results = patch.enhance(v130_results, stock_data_map)
    print(f"   ✅ V13.1增强完成: {len(v131_results)}只股票")

    # 3. 生成V13.1报告
    print("\n📡 Step 3/3: 生成V13.1圣杯报告...")
    integrator = HolyGrailIntegrator()
    holy_grail_report = integrator.generate_holy_grail_report(v131_results)

    # 打印报告
    print("\n" + holy_grail_report)

    # 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = f'v131_holy_grail_{timestamp}.txt'
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(holy_grail_report)
    print(f"\n✅ 圣杯报告已保存: {report_file}")

    # 返回增强后的报告
    return {
        'v130_report': v130_report,
        'v131_results': [
            {
                'code': r.code,
                'name': r.name,
                'holy_grail_score': r.holy_grail_score,
                'recommendation': r.recommendation,
                'board': r.board,
                'tail_pattern': r.tail_pattern,
                'alpha_composite': r.alpha_composite,
            }
            for r in v131_results
        ],
        'holy_grail_report': holy_grail_report,
    }


def compare_v130_vs_v131():
    """对比V13.0和V13.1的效果"""
    print("\n" + "="*70)
    print("  V13.0 vs V13.1 对比测试")
    print("="*70)

    # 运行V13.0
    print("\n🔬 运行V13.0...")
    v130_report = run_v130_only(use_tdx=False)

    # 运行V13.1
    print("\n🔬 运行V13.1...")
    v131_report = run_v131_enhanced(use_tdx=False)

    # 对比
    print("\n" + "="*70)
    print("  对比结果")
    print("="*70)

    v130_count = len(v130_report.get('results', []))
    v131_count = len(v131_report.get('v131_results', []))

    print(f"  V13.0结果数: {v130_count}")
    print(f"  V13.1结果数: {v131_count}")

    # 统计V13.1的推荐分布
    v131_results = v131_report.get('v131_results', [])
    strong_buy = sum(1 for r in v131_results if r['recommendation'] == 'STRONG_BUY_圣杯级别')
    buy = sum(1 for r in v131_results if r['recommendation'] == 'BUY_高置信度')
    watch = sum(1 for r in v131_results if r['recommendation'] == 'WATCH_可关注')

    print(f"\n  V13.1推荐分布:")
    print(f"    圣杯级别(≥0.80): {strong_buy}只")
    print(f"    高置信度(≥0.65): {buy}只")
    print(f"    可关注(≥0.50): {watch}只")

    print("\n✅ 对比测试完成！")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='V13.1 完整集成包装脚本')
    parser.add_argument('--tdx-file', type=str, default=None,
                        help='TDX实盘数据JSON文件路径')
    parser.add_argument('--mode', type=str, default='v131',
                        choices=['v130', 'v131', 'compare'],
                        help='运行模式: v130=仅V13.0, v131=V13.0+V13.1, compare=对比')
    parser.add_argument('--output-dir', type=str, default='.',
                        help='报告输出目录')
    args = parser.parse_args()

    print("="*70)
    print("  V13.1 完整集成包装脚本")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {args.mode}")
    print(f"  TDX数据: {'是' if args.tdx_file else '否（使用合成数据）'}")
    print("="*70)

    if args.mode == 'v130':
        run_v130_only(use_tdx=args.tdx_file is not None, tdx_file=args.tdx_file)
    elif args.mode == 'v131':
        run_v131_enhanced(use_tdx=args.tdx_file is not None, tdx_file=args.tdx_file)
    elif args.mode == 'compare':
        compare_v130_vs_v131()

    print("\n🎉 V13.1 集成脚本执行完成！")


if __name__ == '__main__':
    main()
