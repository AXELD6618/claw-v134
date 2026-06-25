#!/usr/bin/env python3
"""
V13.2 14:30 尾盘实战部署脚本 — TDX实时数据集成版
=================================================
基于V13.1 P0-2部署脚本升级，集成TDXRealtimeFeed数据层

升级要点：
  1. 使用TDXRealtimeFeed替代手动stock_data_map构建
  2. 集成M57 Alpha因子激活（TDX Tier2数据自动注入）
  3. V13.2综合评分 = M46(40%) + M57(40%) + 数据质量(20%)
  4. 因子激活报告 + 数据质量报告
  5. 向后兼容V13.1缓存格式

使用方式：
  # 方式1: Agent准备好TDX缓存后运行（推荐）
  python V13_2_1430_Deploy.py --cache data/tdx_1430_cache.json

  # 方式2: 使用已有实时输入文件
  python V13_2_1430_Deploy.py --tdx-file tdx_realtime_input.json

  # 方式3: 合成数据测试
  python V13_2_1430_Deploy.py --synthetic

  # 方式4: 指定监控池
  python V13_2_1430_Deploy.py --pool-file data/monitor_pool.json --cache data/tdx_1430_cache.json
"""

import json
import os
import sys
import time
import argparse
from datetime import datetime
from typing import Dict, List, Any, Optional


def main():
    parser = argparse.ArgumentParser(description='V13.2 14:30尾盘实战部署 — TDX实时数据集成')
    parser.add_argument('--cache', type=str, default=None,
                        help='TDX缓存JSON文件路径 (Agent通过MCP准备)')
    parser.add_argument('--tdx-file', type=str, default=None,
                        help='TDX实时输入JSON文件路径 (兼容旧格式)')
    parser.add_argument('--synthetic', action='store_true',
                        help='使用合成数据测试')
    parser.add_argument('--pool-file', type=str, default=None,
                        help='监控池JSON文件路径')
    parser.add_argument('--output-dir', type=str, default='data',
                        help='报告输出目录')
    parser.add_argument('--no-m57', action='store_true',
                        help='禁用M57 Alpha因子（降级为V13.1模式）')
    parser.add_argument('--alpha-report', action='store_true',
                        help='生成M57因子激活报告')
    parser.add_argument('--quiet', action='store_true',
                        help='静默模式')
    args = parser.parse_args()

    verbose = not args.quiet

    if verbose:
        print("=" * 70)
        print("  V13.2 14:30 尾盘实战部署 — TDX实时数据集成")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  M46校准: 阈值=0.63 | 命中率=71.1%")
        print(f"  M57 Alpha: {'启用' if not args.no_m57 else '禁用'}")
        print("=" * 70)

    # 1. 加载监控池
    pool = _load_monitor_pool()
    if args.pool_file and os.path.exists(args.pool_file):
        with open(args.pool_file, 'r', encoding='utf-8') as f:
            pool = json.load(f)
    if verbose:
        print(f"\n[1/5] 监控池: {len(pool)}只股票")

    # 2. 加载TDX数据
    from V13_2_TDX_RealtimeFeed import TDXRealtimeFeed

    feed = TDXRealtimeFeed(cache_dir=args.output_dir)

    cache_file = args.cache or args.tdx_file
    cache_loaded = False
    if cache_file and os.path.exists(cache_file):
        cache_loaded = feed.load_from_cache(cache_file)
        if verbose:
            n = len(feed.stocks)
            print(f"[2/5] TDX数据已加载: {cache_file} ({n}只)")
    elif args.synthetic:
        if verbose:
            print("[2/5] 使用合成数据模式")
    else:
        if verbose:
            print("[2/5] 无TDX缓存, 使用监控池+合成数据")

    # 如果缓存中没有足够的股票，补充监控池
    if len(feed.stocks) < len(pool):
        for s in pool:
            code = str(s.get('code', ''))
            if code and code not in feed.stocks:
                feed._add_stock_from_dict(code, s)
        if verbose:
            print(f"      补充监控池后: {len(feed.stocks)}只")

    # 3. 构建stock_data_map
    stock_list = [{'code': s.get('code', c), 'name': s.get('name', feed.stocks[c].name if c in feed.stocks else ''),
                   'industry': s.get('industry', '')}
                  for c, s in zip(feed.stocks.keys(), [{}]*len(feed.stocks))]
    # 使用监控池信息补充
    pool_map = {str(s.get('code', '')): s for s in pool}
    stock_list = []
    for code, tdx_stock in feed.stocks.items():
        pool_info = pool_map.get(code, {})
        stock_list.append({
            'code': code,
            'name': tdx_stock.name or pool_info.get('name', code),
            'industry': pool_info.get('industry', ''),
        })

    stock_data_map = feed.build_stock_data_map(stock_list)
    if verbose:
        m46_rec = sum(1 for d in stock_data_map.values() if d.get('m46_recommended'))
        q2_count = sum(1 for d in stock_data_map.values() if d.get('tdx_data_quality', 0) >= 2)
        print(f"[3/5] stock_data_map构建完成: {len(stock_data_map)}只")
        print(f"      M46推荐: {m46_rec}只 | 增强数据: {q2_count}只")

    # 4. 运行V13.2分析
    if verbose:
        print(f"[4/5] 运行V13.2分析...")

    t0 = time.time()
    result = feed.run_full_analysis(stock_list, verbose=verbose)
    elapsed = time.time() - t0

    if verbose:
        print(f"      分析完成: {elapsed:.1f}秒")

    # 5. M57因子激活报告（可选）
    if args.alpha_report and not args.no_m57:
        if verbose:
            print(f"\n[5/5] 生成M57因子激活报告...")
        try:
            from V13_2_TDX_AlphaEnricher import TDXAlphaEnricher
            enricher = TDXAlphaEnricher()
            enricher.feed = feed
            enricher_report = enricher.generate_enrichment_report(stock_list)
            print(enricher_report)
        except Exception as e:
            if verbose:
                print(f"      Alpha报告生成失败: {e}")

    # 6. 输出报告
    _print_results(result, verbose)

    # 7. 保存结果
    os.makedirs(args.output_dir, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(args.output_dir, f'holy_grail_v132_{timestamp}.json')

    output_data = {
        'timestamp': timestamp,
        'datetime': datetime.now().isoformat(),
        'version': 'V13.2 TDX',
        'm46_calibration': {
            'threshold': 0.63,
            'target_hit_rate': 0.711,
        },
        'stats': result['stats'],
        'pool_size': len(pool),
        'results': result['results'],
        'data_quality': feed.get_data_quality_report(),
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)

    # 保存最新报告路径 (供15:10学习回路读取)
    latest_file = os.path.join(args.output_dir, 'holy_grail_latest.json')
    with open(latest_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2, default=str)

    if verbose:
        print(f"\n[Deploy] 报告已保存: {output_file}")
        print(f"[Deploy] 最新报告: {latest_file}")
        print(f"\n{'=' * 70}")
        print(f"  V13.2 14:30 尾盘实战部署 完成!")
        print(f"  推荐: STRONG_BUY={result['stats']['strong_buy']} "
              f"BUY={result['stats']['buy']} "
              f"WATCH={result['stats']['watch']} "
              f"HOLD={result['stats']['hold']}")
        print(f"  M57激活: {result['stats']['m57_activated']}/{result['stats']['total_stocks']}")
        print(f"  平均V13.2评分: {result['stats']['avg_v132_score']}")
        print(f"{'=' * 70}")

    return output_data


def _load_monitor_pool(pool_size: int = 100) -> List[Dict]:
    """
    加载监控池 — V13.2 P1-6扩大版（30只→100只）
    优先：V13.1动态池 → 本地缓存monitor_pool_100.json → 默认100只
    """
    # 尝试V13.1动态池
    try:
        from V13_1_P0_DynamicPool import DynamicPoolManager
        mgr = DynamicPoolManager(top_n=min(100, pool_size), verbose=False)
        pool = mgr.get_monitor_pool_format()
        if len(pool) >= 60:
            print(f"[监控池] V13.1动态池: {len(pool)}只")
            return pool[:pool_size]
    except ImportError:
        pass

    # 尝试本地缓存（100只版）
    for fname in ['monitor_pool_100.json', 'monitor_pool.json']:
        pool_file = os.path.join('data', fname)
        if os.path.exists(pool_file):
            try:
                with open(pool_file, 'r', encoding='utf-8') as f:
                    pool = json.load(f)
                if isinstance(pool, list) and len(pool) > 0:
                    print(f"[监控池] 本地缓存 {fname}: {len(pool)}只")
                    return pool[:pool_size]
            except Exception:
                pass

    # 默认100只（P1-6扩大）
    print(f"[监控池] 使用默认100只池（P1-6扩大）")
    return _get_default_pool_100()[:pool_size]


def _get_default_pool_100() -> List[Dict]:
    """默认100只监控池（行业多元化 + 板别覆盖）"""
    base_30 = _get_default_pool()
    industry_templates = [
        ('AI算力',  ['601138','603019','688256','688111','688981']),
        ('通信',    ['300308','300502','300394','000063','600522']),
        ('电力设备', ['300750','601012','300274','002594','688981']),
        ('食品饮料', ['600519','000858','600809','603369','000568']),
        ('电子',    ['688981','002371','603501','000725','002475']),
        ('计算机',  ['002230','300033','002415','688111','300663']),
        ('医药生物', ['300760','600276','000661','300015','002007']),
        ('非银金融', ['300059','601318','600030','601688','600999']),
    ]
    full_pool = base_30[:]
    idx = 0
    while len(full_pool) < 100:
        ind_name, ind_codes = industry_templates[idx % len(industry_templates)]
        for base_code in ind_codes:
            if len(full_pool) >= 100:
                break
            suffix = f"{idx:02d}"
            code = base_code + suffix if idx > 0 else base_code
            code = code[:6]
            if any(s['code'] == code for s in full_pool):
                idx += 1
                continue
            full_pool.append({
                'code': code,
                'name': f"{ind_name}股{idx}",
                'setcode': '1' if code.startswith('6') else '0',
                'industry': ind_name,
            })
            idx += 1
        idx += 1
    return full_pool[:100]


def _get_default_pool() -> List[Dict]:
    """默认监控池"""
    return [
        {"code": "600519", "name": "贵州茅台", "setcode": "1", "industry": "食品饮料"},
        {"code": "300750", "name": "宁德时代", "setcode": "0", "industry": "电力设备"},
        {"code": "002230", "name": "科大讯飞", "setcode": "0", "industry": "AI"},
        {"code": "300418", "name": "昆仑万维", "setcode": "0", "industry": "AI"},
        {"code": "000063", "name": "中兴通讯", "setcode": "0", "industry": "通信"},
        {"code": "000725", "name": "京东方A", "setcode": "0", "industry": "电子"},
        {"code": "002415", "name": "海康威视", "setcode": "0", "industry": "计算机"},
        {"code": "002594", "name": "比亚迪", "setcode": "0", "industry": "汽车"},
        {"code": "300059", "name": "东方财富", "setcode": "0", "industry": "非银金融"},
        {"code": "300308", "name": "中际旭创", "setcode": "0", "industry": "通信"},
        {"code": "300502", "name": "新易盛", "setcode": "0", "industry": "通信"},
        {"code": "300760", "name": "迈瑞医疗", "setcode": "0", "industry": "医药生物"},
        {"code": "601012", "name": "隆基绿能", "setcode": "1", "industry": "电力设备"},
        {"code": "601138", "name": "工业富联", "setcode": "1", "industry": "AI算力"},
        {"code": "603019", "name": "中科曙光", "setcode": "1", "industry": "AI算力"},
        {"code": "688256", "name": "寒武纪", "setcode": "1", "industry": "AI芯片"},
        {"code": "688981", "name": "中芯国际", "setcode": "1", "industry": "电子"},
        {"code": "300394", "name": "天孚通信", "setcode": "0", "industry": "通信"},
        {"code": "300274", "name": "阳光电源", "setcode": "0", "industry": "电力设备"},
        {"code": "002371", "name": "北方华创", "setcode": "0", "industry": "电子"},
        {"code": "300033", "name": "同花顺", "setcode": "0", "industry": "金融科技"},
        {"code": "300124", "name": "汇川技术", "setcode": "0", "industry": "机械设备"},
        {"code": "601318", "name": "中国平安", "setcode": "1", "industry": "非银金融"},
        {"code": "601899", "name": "紫金矿业", "setcode": "1", "industry": "有色金属"},
        {"code": "603501", "name": "韦尔股份", "setcode": "1", "industry": "电子"},
        {"code": "688111", "name": "金山办公", "setcode": "1", "industry": "计算机"},
        {"code": "000858", "name": "五粮液", "setcode": "0", "industry": "食品饮料"},
        {"code": "600809", "name": "山西汾酒", "setcode": "1", "industry": "食品饮料"},
        {"code": "601919", "name": "中远海控", "setcode": "1", "industry": "交通运输"},
        {"code": "002475", "name": "立讯精密", "setcode": "0", "industry": "电子"},
    ]


def _print_results(result: Dict, verbose: bool):
    """打印分析结果"""
    stats = result['stats']
    results = result['results']

    print(f"\n{'=' * 70}")
    print(f"  V13.2 圣杯尾盘选股报告")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  M46阈值: 0.63 | M57 Alpha: 启用")
    print(f"{'=' * 70}")

    # 推荐
    recommended = [r for r in results if r.get('recommendation') in ('STRONG_BUY', 'BUY')]
    print(f"\n推荐: {len(recommended)}/{len(results)}只")

    if recommended:
        print(f"\n{'-' * 70}")
        print(f"{'代码':<8} {'名称':<10} {'涨幅%':>7} {'V13.2':>7} {'M46':>7} {'M57':>7} {'T+1':>7} {'建议'}")
        print(f"{'-' * 70}")
        for r in recommended[:20]:
            print(f"{r['code']:<8} {r.get('name','')[:8]:<10} "
                  f"{r.get('change_pct',0):>7.2f} {r.get('v132_score',0):>7.4f} "
                  f"{r.get('m46_confidence',0):>7.4f} {r.get('m57_composite',0):>+7.4f} "
                  f"{r.get('m57_t1_forecast',0):>+6.3f}% {r.get('recommendation','')}")
        print(f"{'-' * 70}")

    # M57因子激活汇总
    m57_active = stats.get('m57_activated', 0)
    total = stats.get('total_stocks', 0)
    print(f"\nM57因子激活: {m57_active}/{total} 只 ({m57_active/max(1,total)*100:.0f}%)")

    # 数据质量
    quality = result.get('stock_data_map', {})
    q1 = sum(1 for d in quality.values() if d.get('tdx_data_quality', 0) >= 1)
    q2 = sum(1 for d in quality.values() if d.get('tdx_data_quality', 0) >= 2)
    has_cf = sum(1 for d in quality.values() if d.get('has_capital_flow', False))
    has_lhb = sum(1 for d in quality.values() if d.get('has_dragon_tiger', False))
    print(f"数据质量: 基础{q1}只 | 增强{q2}只 | 资金流{has_cf}只 | 龙虎榜{has_lhb}只")

    print(f"\n{'=' * 70}")


if __name__ == '__main__':
    main()
