"""保存T4 14:15 大盘指数快照"""
import json
from datetime import datetime

market_index = {
    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'phase': 'T4_14:15',
    'indexes': {
        '000001.SH': {
            'name': '上证指数',
            'now': 4114.71484,
            'close': 4110.81348,
            'open': 4103.48193,
            'high': 4133.09766,
            'low': 4093.00879,
            'change_pct': 0.09,
            'volume_lots': 585516671,
            'amount': 1421431540000,
            'hsl': 1.21
        },
        '399001.SZ': {
            'name': '深证成指',
            'now': 16328.4375,
            'close': 16051.3193,
            'open': 16099.7627,
            'high': 16339.9521,
            'low': 16058.8506,
            'change_pct': 1.73,
            'volume_lots': 729493651,
            'amount': 1735280950000,
            'hsl': 2.99
        }
    },
    'breadth': {
        'sh_total': 4010,
        'sz_total': 4012,
        'note': '深强沪弱分化'
    }
}

with open('data/fullmarket_cache/index_1415.json', 'w', encoding='utf-8') as f:
    json.dump(market_index, f, ensure_ascii=False, indent=2)

print(f'上证: 4114.71 (+0.09%) | 深成: 16328.44 (+1.73%)')
print(f'深强沪弱分化 (深成涨幅超上证19倍)')
print('OK saved to data/fullmarket_cache/index_1415.json')
