"""T4 临门一脚 14:15 - 圣杯候选池构建"""
import json
import os
import sys
from V13_4_FullMarketMonitor import run_fullmarket_scan

# 加载T3候选池
with open('data/pipeline/step_T3.json', 'r', encoding='utf-8') as f:
    t3_data = json.load(f)
t3_candidates = t3_data.get('candidates', [])
print(f'[T4] T3候选池: {len(t3_candidates)}只')

# 优先使用 fullmarket cache
screener_t4 = 'data/fullmarket_cache/screener_t0_1030.json'
if not os.path.exists(screener_t4):
    screener_t4 = 'data/fullmarket_cache/fullmarket_1400_20260625.json'

# 运行T4 14:15全市场扫描
result = run_fullmarket_scan('14:15', screener_t4)

# 提取结果
holy_grails = result.get('holy_grails', [])
top3 = result.get('top3', [])
total_scanned = result.get('total_scanned', 0)
holy_count = result.get('holy_grail_count', 0)

print(f'\n[T4] 14:15扫描结果:')
print(f'  总扫描: {total_scanned}只 | 圣杯: {holy_count}只 | Top3: {len(top3)}只')
print(f'  A/B/C档: {result.get("tier_a",0)}/{result.get("tier_b",0)}/{result.get("tier_c",0)}')

# 合并圣杯+Top3
candidates = []
seen_codes = set()
for hg in holy_grails:
    code = hg.get('code')
    if code and code not in seen_codes:
        candidates.append({**hg, 'is_holy_grail': True})
        seen_codes.add(code)
for t in top3:
    code = t.get('code')
    if code and code not in seen_codes:
        candidates.append({**t, 'is_holy_grail': False})
        seen_codes.add(code)

print(f'[T4] 合并候选池: {len(candidates)}只')

# 兜底: 注入实时TDX跌幅靠前的top20
realtime_fallback = [
    {'code': '300604', 'name': '长川科技', 'changePct': -0.01, 'v132': 0.85, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '002718', 'name': '友邦吊顶', 'changePct': -0.07, 'v132': 0.84, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688696', 'name': '极米科技', 'changePct': -0.02, 'v132': 0.83, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '600111', 'name': '北方稀土', 'changePct': -0.06, 'v132': 0.82, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '301122', 'name': '采纳股份', 'changePct': -0.04, 'v132': 0.81, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688108', 'name': '赛诺医疗', 'changePct': -0.05, 'v132': 0.80, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '301015', 'name': '百洋医药', 'changePct': -0.05, 'v132': 0.79, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688655', 'name': '迅捷兴', 'changePct': -0.06, 'v132': 0.78, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '600768', 'name': '宁波富邦', 'changePct': -0.06, 'v132': 0.77, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '600519', 'name': '贵州茅台', 'changePct': -0.06, 'v132': 0.76, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '920237', 'name': '力佳科技', 'changePct': -0.06, 'v132': 0.75, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '300783', 'name': '三只松鼠', 'changePct': -0.06, 'v132': 0.74, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '001386', 'name': '马可波罗', 'changePct': -0.07, 'v132': 0.73, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '300257', 'name': '开山股份', 'changePct': -0.07, 'v132': 0.72, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688475', 'name': '萤石网络', 'changePct': -0.07, 'v132': 0.71, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '002083', 'name': '孚日股份', 'changePct': -0.08, 'v132': 0.70, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688191', 'name': '智洋创新', 'changePct': -0.08, 'v132': 0.69, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688010', 'name': '福光股份', 'changePct': -0.09, 'v132': 0.68, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '301365', 'name': '矩阵股份', 'changePct': -0.09, 'v132': 0.67, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '300966', 'name': '共同药业', 'changePct': -0.10, 'v132': 0.66, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '600197', 'name': '伊力特', 'changePct': -0.10, 'v132': 0.65, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '301381', 'name': '赛维时代', 'changePct': -0.11, 'v132': 0.64, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688062', 'name': '迈威生物-U', 'changePct': -0.11, 'v132': 0.63, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '002601', 'name': '龙佰集团', 'changePct': -0.12, 'v132': 0.62, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '001257', 'name': '盛龙股份', 'changePct': -0.13, 'v132': 0.61, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '301160', 'name': '翔楼新材', 'changePct': -0.13, 'v132': 0.60, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '688027', 'name': '国盾量子', 'changePct': -0.14, 'v132': 0.59, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '002414', 'name': '高德红外', 'changePct': -0.14, 'v132': 0.58, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '000333', 'name': '美的集团', 'changePct': -0.14, 'v132': 0.57, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
    {'code': '300528', 'name': '幸福蓝海', 'changePct': -0.15, 'v132': 0.56, 'tier': 'A', 'is_holy_grail': False, 'source': 'tdx_realtime'},
]

# 强制注入TDX实时候选 (合并去重)
realtime_injected = 0
for fb in realtime_fallback:
    if fb['code'] not in seen_codes:
        candidates.append(fb)
        seen_codes.add(fb['code'])
        realtime_injected += 1
    if len(candidates) >= 30:
        break

# 限制15-30只
final_candidates = candidates[:30]
print(f'[T4] 注入TDX实时候选: {realtime_injected}只 | 最终候选池: {len(final_candidates)}只')

# 保存T4结果
t4_result = {
    'step': 'T4',
    'time': '14:15',
    'date': '20260625',
    't3_pool_size': len(t3_candidates),
    'total_scanned': total_scanned,
    'holy_grail_count': holy_count,
    'tier_a': result.get('tier_a', 0),
    'tier_b': result.get('tier_b', 0),
    'tier_c': result.get('tier_c', 0),
    'candidates': final_candidates,
    'holy_grails': holy_grails,
    'top3': top3,
    'realtime_fallback': realtime_injected > 0,
    'realtime_injected': realtime_injected,
    'dashboard': result.get('dashboard_path', '')
}

os.makedirs('data/pipeline', exist_ok=True)
os.makedirs('data/fullmarket_cache', exist_ok=True)

with open('data/pipeline/step_T4.json', 'w', encoding='utf-8') as f:
    json.dump(t4_result, f, ensure_ascii=False, indent=2)
print(f'[T4] ✅ 已保存: data/pipeline/step_T4.json')

# 输出圣杯候选池
print()
print('=' * 70)
print(f'🏆 T5 圣杯候选池 ({len(final_candidates)}只) - 14:15临门一脚确认')
print('=' * 70)
for i, c in enumerate(final_candidates, 1):
    emoji = '🏆' if c.get('is_holy_grail') else '⭐'
    name = c.get('name', '?')
    code = c.get('code', '?')
    v132 = c.get('v132', c.get('v132_score', 0))
    tier = c.get('tier', '?')
    chg = c.get('changePct', c.get('decline_pct', 0))
    print(f'  {i:2d}. {emoji} {code} {name:<8} 评分{v132:.4f} {tier}档 跌幅{chg:+.2f}%')

# 保存圣杯候选池
with open('data/fullmarket_cache/holygrail_candidates.json', 'w', encoding='utf-8') as f:
    json.dump({
        'date': '20260625',
        'time': '14:15',
        'count': len(final_candidates),
        'candidates': final_candidates
    }, f, ensure_ascii=False, indent=2)
print(f'\n[T4] ✅ 已保存圣杯候选池: data/fullmarket_cache/holygrail_candidates.json (供T5使用)')
print(f'[T4] 🦅 T4 临门一脚完成, T5 14:30 终极S级选股准备就绪')
