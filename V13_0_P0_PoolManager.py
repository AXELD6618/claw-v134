#!/usr/bin/env python3
"""
V13.0 P0-3: 300只行业分层监控池管理器
======================================
将固定4-5只监控池扩展至301只（+活跃题材），
实现按申万31个一级行业分层选择 + 每日轮换机制。

核心能力：
  1. 加载301只行业分层标的（来自 data/dynamic_watchlist.py）
  2. 每日按成交额活跌度排序，每行业取Top-N
  3. 行业轮换：31行业分3组，每日轮换保证覆盖
  4. 活跃题材补位：AI/机器人/低空经济等热门赛道常驻
  5. 输出 dynamic_watchlist.json 供 run_tail_market_1430.py 消费
  6. 支持TDX实时成交额数据刷新排序

使用方式：
  python V13_0_P0_PoolManager.py                    # 生成今日池
  python V13_0_P0_PoolManager.py --rotate             # 行业轮换
  python V13_0_P0_PoolManager.py --output 150          # 自定义输出数量
  python V13_0_P0_PoolManager.py --with-tdx-amounts    # 使用TDX成交额排序

行业分组：
  组A (β-进攻): AI/半导体/通信/机器人/军工/计算机/电子/电力设备/汽车/传媒
  组B (α-均衡): 医药/食品饮料/有色金属/化工/机械/家电/商贸/电力/新能源
  组C (γ-防御): 银行/非银/地产/建筑/交运/公用/钢铁/煤炭/石油/农林/环保

每日轮换: A→B→C→A, 未轮到的组保留核心代表(每行业1-2只)
"""

import json
import os
import sys
import random
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════
# 行业分组（申万31个一级行业）
# ═══════════════════════════════════════════════

INDUSTRY_GROUPS = {
    'A': {  # β-进攻组 (高波动/高弹性)
        'name': '进攻组',
        'industries': [
            '电子', '计算机', '通信', 'AI算力', '半导体',
            '机器人', '国防军工', '电力设备', '汽车', '传媒',
        ],
        'per_industry_top': 4,  # 每行业Top-4
        'description': '高β赛道：AI/半导体/机器人/军工/新能源',
    },
    'B': {  # α-均衡组 (中等波动)
        'name': '均衡组',
        'industries': [
            '医药生物', '食品饮料', '有色金属', '基础化工',
            '机械设备', '家用电器', '商贸零售', '新能源',
            '美容护理', '社会服务',
        ],
        'per_industry_top': 3,
        'description': '中β赛道：医药/消费/有色/化工',
    },
    'C': {  # γ-防御组 (低波动/价值)
        'name': '防御组',
        'industries': [
            '银行', '非银金融', '房地产', '建筑装饰',
            '交通运输', '公用事业', '钢铁', '煤炭',
            '石油石化', '农林牧渔', '环保',
        ],
        'per_industry_top': 2,
        'description': '低β赛道：银行/公用/交运/煤炭',
    },
}

# 活跃题材常驻 (不参与轮换，始终保留在池中)
PERMANENT_THEMES = [
    'AI算力', 'AI芯片', '金融科技', '创新药', '低空经济',
    '机器人', '半导体',
]

# 每日基准保留数
DAILY_BASE_POOL = 100   # 每日基础池大小
DAILY_EXTENDED_POOL = 200  # 扩展池大小

# ═══════════════════════════════════════════════
# 加载监控池
# ═══════════════════════════════════════════════

def load_watchlist() -> List[Tuple[str, str, str, str]]:
    """加载301只完整监控池"""
    try:
        from data.dynamic_watchlist import DYNAMIC_WATCHLIST
        return list(DYNAMIC_WATCHLIST)
    except ImportError:
        # 兜底
        from V13_0_P0_TDXInjector import DYNAMIC_WATCHLIST as fallback
        return list(fallback)


# ═══════════════════════════════════════════════
# 池管理器
# ═══════════════════════════════════════════════

class PoolManager:
    """
    300只行业分层监控池管理器

    使用模式:
      mgr = PoolManager()
      mgr.load_all_stocks()
      pool = mgr.build_daily_pool(rotate=True)
      mgr.export_to_json(pool)
    """

    def __init__(self, cache_dir: str = 'data'):
        self.cache_dir = cache_dir
        self.all_stocks: List[dict] = []          # [{code, setcode, name, industry}, ...]
        self.by_industry: Dict[str, List[dict]] = defaultdict(list)
        self.by_theme: Dict[str, List[dict]] = defaultdict(list)
        self.current_group: str = 'A'              # 当前轮换到的组
        self.rotation_day: int = 0
        os.makedirs(cache_dir, exist_ok=True)

    def load_all_stocks(self):
        """加载全部301只标的"""
        raw = load_watchlist()
        self.all_stocks = [
            {'code': code, 'setcode': setcode, 'name': name, 'industry': industry}
            for code, setcode, name, industry in raw
        ]

        # 按行业分组
        self.by_industry.clear()
        self.by_theme.clear()

        for stock in self.all_stocks:
            ind = stock['industry']

            # 活跃题材
            if ind in PERMANENT_THEMES:
                self.by_theme[ind].append(stock)
            else:
                self.by_industry[ind].append(stock)

        print(f"[PoolManager] 加载 {len(self.all_stocks)} 只标的 → {len(self.by_industry)}个行业 + {len(self.by_theme)}个主题")
        return len(self.all_stocks)

    def determine_rotation_group(self, date_override: str = None) -> str:
        """根据日期确定当前轮换组"""
        today = date_override or datetime.now().strftime('%Y-%m-%d')
        # 使用日期作为种子
        day_num = int(today.replace('-', ''))
        groups = ['A', 'B', 'C']
        idx = (day_num // 3) % 3  # 每3天轮换一次
        # 简化：直接用日期 % 3
        idx = int(today.replace('-', '')) % 3
        self.current_group = groups[idx]
        self.rotation_day = int(today.replace('-', '')) % 31
        return self.current_group

    def build_daily_pool(
        self,
        rotate: bool = True,
        pool_size: int = 150,
        with_amounts: Dict[str, float] = None,
    ) -> List[dict]:
        """
        构建每日监控池

        Args:
            rotate: 是否启用行业轮换
            pool_size: 输出池大小
            with_amounts: {code: amount} 今日成交额数据(用于活跃度排序)

        Returns:
            [{code, setcode, name, industry, amount, rank_in_group}, ...]
        """
        if not self.all_stocks:
            self.load_all_stocks()

        if rotate:
            group = self.determine_rotation_group()
        else:
            group = 'A'  # 默认进攻组

        group_info = INDUSTRY_GROUPS[group]
        primary_industries = set(group_info['industries'])
        per_industry = group_info['per_industry_top']

        selected = []
        seen_codes = set()

        # ── Step 1: 活跃题材常驻 ──
        for theme, stocks in self.by_theme.items():
            # 按成交额排序
            if with_amounts:
                sorted_stocks = sorted(
                    stocks,
                    key=lambda s: with_amounts.get(s['code'], 0),
                    reverse=True
                )
            else:
                sorted_stocks = stocks

            for stock in sorted_stocks[:min(4, len(sorted_stocks))]:
                if stock['code'] not in seen_codes:
                    stock_copy = dict(stock)
                    stock_copy['rank_in_group'] = '常驻'
                    stock_copy['amount'] = with_amounts.get(stock['code'], 0) if with_amounts else 0
                    selected.append(stock_copy)
                    seen_codes.add(stock['code'])

        # ── Step 2: 主轮换组行业 (Top per_industry) ──
        for ind in group_info['industries']:
            stocks = self.by_industry.get(ind, [])
            if not stocks:
                continue

            if with_amounts:
                sorted_stocks = sorted(
                    stocks, key=lambda s: with_amounts.get(s['code'], 0), reverse=True
                )
            else:
                sorted_stocks = stocks

            for stock in sorted_stocks[:per_industry]:
                if stock['code'] not in seen_codes:
                    stock_copy = dict(stock)
                    stock_copy['rank_in_group'] = f'主力·{group}组'
                    stock_copy['amount'] = with_amounts.get(stock['code'], 0) if with_amounts else 0
                    selected.append(stock_copy)
                    seen_codes.add(stock['code'])

        # ── Step 3: 非主力行业保留1-2只核心 ──
        all_industries = set(self.by_industry.keys())
        secondary_industries = all_industries - primary_industries

        for ind in secondary_industries:
            stocks = self.by_industry.get(ind, [])
            if not stocks:
                continue

            if with_amounts:
                sorted_stocks = sorted(
                    stocks, key=lambda s: with_amounts.get(s['code'], 0), reverse=True
                )
            else:
                sorted_stocks = stocks

            for stock in sorted_stocks[:2]:
                if stock['code'] not in seen_codes:
                    stock_copy = dict(stock)
                    stock_copy['rank_in_group'] = f'保留·{group}组'
                    stock_copy['amount'] = with_amounts.get(stock['code'], 0) if with_amounts else 0
                    selected.append(stock_copy)
                    seen_codes.add(stock['code'])

        # ── Step 4: 补充至目标池大小 ──
        # 从其他主力组行业补位 (每组2只，与保留量一致)
        other_groups = [g for g in ['A', 'B', 'C'] if g != group]
        for other_group in other_groups:
            other_info = INDUSTRY_GROUPS[other_group]
            for ind in other_info['industries']:
                if len(selected) >= pool_size:
                    break
                stocks = self.by_industry.get(ind, [])
                if not stocks:
                    continue

                if with_amounts:
                    sorted_stocks = sorted(
                        stocks, key=lambda s: with_amounts.get(s['code'], 0), reverse=True
                    )
                else:
                    sorted_stocks = stocks

                # 保留2只核心代表
                for stock in sorted_stocks[:2]:
                    if stock['code'] not in seen_codes:
                        stock_copy = dict(stock)
                        stock_copy['rank_in_group'] = '补位'
                        stock_copy['amount'] = with_amounts.get(stock['code'], 0) if with_amounts else 0
                        selected.append(stock_copy)
                        seen_codes.add(stock['code'])
            if len(selected) >= pool_size:
                break

        # Step 4b: 如果还不够，从剩余未取完的行业补充
        if len(selected) < pool_size:
            for ind, stocks in self.by_industry.items():
                if len(selected) >= pool_size:
                    break
                for stock in stocks:
                    if stock['code'] not in seen_codes:
                        stock_copy = dict(stock)
                        stock_copy['rank_in_group'] = '扩展'
                        stock_copy['amount'] = with_amounts.get(stock['code'], 0) if with_amounts else 0
                        selected.append(stock_copy)
                        seen_codes.add(stock['code'])
                        if len(selected) >= pool_size:
                            break

        # ── Step 5: 统计 ──
        self._print_pool_stats(selected, group)

        return selected[:pool_size]

    def _print_pool_stats(self, pool: List[dict], group: str):
        """打印池统计"""
        print(f"\n  📊 每日监控池 (轮换组: {group}·{INDUSTRY_GROUPS[group]['name']}):")
        print(f"     总数: {len(pool)}只")

        # 按rank分组统计
        from collections import Counter
        rank_counts = Counter(s.get('rank_in_group', '未知') for s in pool)
        for rank, count in sorted(rank_counts.items()):
            print(f"       {rank}: {count}只")

        # 行业分布
        ind_counts = Counter(s.get('industry', '未知') for s in pool)
        print(f"     行业覆盖: {len(ind_counts)}个")
        top_inds = sorted(ind_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        for ind, cnt in top_inds:
            print(f"       {ind}: {cnt}只")

    def export_to_json(self, pool: List[dict], filename: str = 'dynamic_watchlist.json') -> str:
        """导出池到JSON"""
        output = {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'rotation_group': self.current_group,
            'rotation_day': self.rotation_day,
            'total_stocks': len(pool),
            'description': f'行业分层轮换监控池 | 当日主力·{INDUSTRY_GROUPS.get(self.current_group, {}).get("name", "")}',
            'stocks': [
                {
                    'code': s['code'],
                    'market': s['setcode'],  # 兼容旧格式: '0'=深市, '1'=沪市
                    'name': s['name'],
                    'industry': s['industry'],
                    'rank_in_group': s.get('rank_in_group', ''),
                    'amount': s.get('amount', 0),
                }
                for s in pool
            ],
        }

        filepath = os.path.join(self.cache_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        print(f"\n  📁 监控池已导出: {filepath} ({len(pool)}只)")
        return filepath

    def get_industry_summary(self) -> dict:
        """获取全行业概览"""
        if not self.all_stocks:
            self.load_all_stocks()

        summary = {}
        for ind, stocks in sorted(self.by_industry.items()):
            summary[ind] = {
                'count': len(stocks),
                'tops': [s['name'] for s in stocks[:3]],
                'codes': [s['code'] for s in stocks[:3]],
            }

        for theme, stocks in sorted(self.by_theme.items()):
            summary[theme] = {
                'count': len(stocks),
                'tops': [s['name'] for s in stocks[:3]],
                'codes': [s['code'] for s in stocks[:3]],
                'permanent': True,
            }

        return summary

    def get_orchestrator_input(self, pool: List[dict] = None, pool_size: int = 150) -> dict:
        """
        生成Orchestrator统一调度器的输入格式

        输出兼容 V13_0_Orchestrator 的 input 格式
        """
        if pool is None:
            pool = self.build_daily_pool(rotate=True, pool_size=pool_size)

        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'rotation_group': self.current_group,
            'pool_size': len(pool),
            'watchlist': [
                {
                    'code': s['code'],
                    'market': s.get('setcode', '0'),
                    'name': s['name'],
                    'industry': s['industry'],
                }
                for s in pool
            ],
        }


# ═══════════════════════════════════════════════
# 快捷入口
# ═══════════════════════════════════════════════

def build_daily_pool(
    rotate: bool = True,
    pool_size: int = 150,
    with_amounts: Dict[str, float] = None,
    export: bool = True,
) -> dict:
    """
    一键构建每日监控池

    Args:
        rotate: 是否轮换
        pool_size: 池大小
        with_amounts: {code: amount} TDX成交额
        export: 是否导出JSON

    Returns:
        {pool, manager, json_path}
    """
    mgr = PoolManager()
    mgr.load_all_stocks()
    pool = mgr.build_daily_pool(
        rotate=rotate,
        pool_size=pool_size,
        with_amounts=with_amounts,
    )

    result = {'pool': pool, 'manager': mgr}

    if export:
        json_path = mgr.export_to_json(pool)
        result['json_path'] = json_path

    return result


# ═══════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='V13.0 P0-3 300只行业分层监控池管理器')
    parser.add_argument('--rotate', action='store_true', default=True, help='启用行业轮换')
    parser.add_argument('--no-rotate', action='store_false', dest='rotate', help='禁用轮换(全池输出)')
    parser.add_argument('--output', type=int, default=150, help='输出池大小(默认150)')
    parser.add_argument('--group', type=str, choices=['A', 'B', 'C'], help='强制指定轮换组')
    parser.add_argument('--summary', action='store_true', help='仅输出行业概览')
    parser.add_argument('--export', action='store_true', default=True, help='导出JSON')

    args = parser.parse_args()

    print("=" * 70)
    print("  V13.0 P0-3 300只行业分层监控池管理器")
    print("=" * 70)

    mgr = PoolManager()
    mgr.load_all_stocks()

    if args.summary:
        summary = mgr.get_industry_summary()
        print(f"\n  📊 行业概览 ({len(summary)}个):")
        for ind, info in sorted(summary.items()):
            perm_tag = '⭐' if info.get('permanent') else '  '
            print(f"    {perm_tag} {ind}: {info['count']}只 | Top: {', '.join(info['tops'])}")
        sys.exit(0)

    if args.group:
        mgr.current_group = args.group

    pool = mgr.build_daily_pool(
        rotate=args.rotate,
        pool_size=args.output,
    )

    if args.export:
        mgr.export_to_json(pool)

    print("\n✅ P0-3 监控池管理完成！")
    print(f"   今日轮换组: {mgr.current_group}·{INDUSTRY_GROUPS[mgr.current_group]['name']}")
    print("=" * 70)
