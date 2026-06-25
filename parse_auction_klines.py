#!/usr/bin/env python3
"""解析5只股票的TDX 5分钟K线，提取20260623集合竞价数据"""
import json
import re
import os

TOOL_DIR = r"C:\Users\SGM-AXELD\.workbuddy\projects\e-WorkBuddy_dot_workbuddy-Claw\a70385dc-018c-4460-80d9-5e8d7ad5891f\tool-results"

FILE_MAP = {
    "002080": "mcp-connector-proxy-tdx-connector_tdx_kline-1782275419585-3c688b.txt",
    "600667": "mcp-connector-proxy-tdx-connector_tdx_kline-1782275419708-70237c.txt",
    "002254": "mcp-connector-proxy-tdx-connector_tdx_kline-1782275419812-41f89c.txt",
    "002051": "mcp-connector-proxy-tdx-connector_tdx_kline-1782275419879-a14059.txt",
    "002046": "mcp-connector-proxy-tdx-connector_tdx_kline-1782275419942-be2ef2.txt",
}

BAR_1455 = 53700  # 14:55
BAR_1500 = 54000  # 15:00
TARGET_DATE = "20260623"


def parse_kline_file(filepath):
    """从文件中提取JSON K线数据"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 找到JSON起始位置
    json_start = content.find('{')
    if json_start == -1:
        # 尝试找 "详细K线数据:" 后面的内容
        detail_idx = content.find("详细K线数据:")
        if detail_idx > 0:
            json_start = content.find('{', detail_idx)
    if json_start == -1:
        print(f"  ERROR: Cannot find JSON in {filepath}")
        return None

    json_str = content[json_start:].strip()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # 尝试截断到最后一个完整JSON
        for end_char in range(len(json_str), 0, -1):
            try:
                return json.loads(json_str[:end_char])
            except json.JSONDecodeError:
                continue
        print(f"  ERROR: Failed to parse JSON from {filepath}")
        return None


def process_stock(code, filename):
    """处理单只股票的K线数据"""
    filepath = os.path.join(TOOL_DIR, filename)
    print(f"\n{'='*60}")
    print(f"  Processing: {code} ({filename})")
    print(f"{'='*60}")

    data = parse_kline_file(filepath)
    if not data:
        print(f"  ❌ Failed to parse data")
        return None

    code_in_data = data.get('Code', '')
    print(f"  Code in data: {code_in_data}, Items: {len(data.get('ListItem', []))}")

    # 按日期分组bars
    items = data.get('ListItem', [])
    bars_by_date = {}
    for item in items:
        fields = item.get('Item', [])
        if len(fields) < 6:
            continue
        date = fields[0]
        second = int(fields[1])
        close = float(fields[4])
        volume = float(fields[8]) if len(fields) > 8 else 0
        open_p = float(fields[2]) if len(fields) > 2 else 0

        if date not in bars_by_date:
            bars_by_date[date] = []
        bars_by_date[date].append({
            'second': second,
            'open': open_p,
            'close': close,
            'volume': volume,
        })

    print(f"  Available dates: {sorted(bars_by_date.keys())[-7:]}")

    if TARGET_DATE not in bars_by_date:
        print(f"  ❌ Target date {TARGET_DATE} not found!")
        return None

    target_bars = bars_by_date[TARGET_DATE]
    print(f"  Bars on {TARGET_DATE}: {len(target_bars)}")

    # Find 14:55 and 15:00 bars
    bar_1455 = None
    bar_1500 = None
    for b in target_bars:
        if b['second'] == BAR_1455:
            bar_1455 = b
        elif b['second'] == BAR_1500:
            bar_1500 = b

    if not bar_1455:
        print(f"  ❌ 14:55 bar (53700) not found!")
        # 列出所有bar的second值
        seconds = sorted(set(b['second'] for b in target_bars))
        print(f"  Available seconds: {seconds[-10:]}")
        return None
    if not bar_1500:
        print(f"  ❌ 15:00 bar (54000) not found!")
        seconds = sorted(set(b['second'] for b in target_bars))
        print(f"  Available seconds: {seconds[-10:]}")
        return None

    # Compute avg volume of previous 5 bars
    bar_1455_idx = None
    for i, b in enumerate(target_bars):
        if b['second'] == BAR_1455:
            bar_1455_idx = i
            break

    prev_5_vols = []
    if bar_1455_idx and bar_1455_idx >= 5:
        for b in target_bars[bar_1455_idx - 5:bar_1455_idx]:
            if b['volume'] > 0:
                prev_5_vols.append(b['volume'])
    avg_5_vol = sum(prev_5_vols) / len(prev_5_vols) if prev_5_vols else 0

    print(f"  ✅ 14:55 bar: close={bar_1455['close']:.2f}, vol={bar_1455['volume']:.0f}")
    print(f"  ✅ 15:00 bar: close={bar_1500['close']:.2f}, vol={bar_1500['volume']:.0f}")
    print(f"  Avg prev 5 vol: {avg_5_vol:.0f} (from {len(prev_5_vols)} bars)")

    # Get prev close (from previous trading day)
    prev_close = 0
    prev_date = None
    sorted_dates = sorted(bars_by_date.keys())
    target_idx = sorted_dates.index(TARGET_DATE)
    if target_idx > 0:
        prev_date = sorted_dates[target_idx - 1]
        prev_bars = bars_by_date[prev_date]
        # Find 15:00 bar of previous day
        for b in prev_bars:
            if b['second'] == BAR_1500:
                prev_close = b['close']
                break
        if prev_close == 0 and prev_bars:
            prev_close = prev_bars[-1]['close']

    print(f"  Prev date: {prev_date}, prev_close: {prev_close:.2f}" if prev_close else f"  Prev date: {prev_date}, prev_close: N/A")

    # Compute auction elements
    close_1455 = bar_1455['close']
    close_1500 = bar_1500['close']
    auction_vol = bar_1500['volume']

    result = {
        'code': code,
        'date': TARGET_DATE,
        'close_1455': close_1455,
        'close_1500': close_1500,
        'prev_close': prev_close,
        'auction_vol': auction_vol,
        'avg_5_vol': avg_5_vol,
    }

    # Compute derivatives
    if close_1455 > 0 and prev_close > 0:
        result['price_before_pct'] = round((close_1455 / prev_close - 1) * 100, 4)
    else:
        result['price_before_pct'] = 0

    if close_1455 > 0:
        result['price_change_pct'] = round((close_1500 / close_1455 - 1) * 100, 4)
    else:
        result['price_change_pct'] = 0

    if avg_5_vol > 0:
        result['volume_ratio'] = round(auction_vol / avg_5_vol, 4)
    else:
        result['volume_ratio'] = 0

    print(f"  📊 Results:")
    print(f"     price_before_pct:  {result['price_before_pct']:+.4f}%")
    print(f"     price_change_pct:  {result['price_change_pct']:+.4f}%")
    print(f"     volume_ratio:       {result['volume_ratio']:.4f}")

    return result


def main():
    all_results = {}
    for code in ["002080", "600667", "002046", "002254", "002051"]:
        filename = FILE_MAP[code]
        result = process_stock(code, filename)
        if result:
            all_results[code] = result

    print(f"\n\n{'='*60}")
    print(f"  SUMMARY: {len(all_results)} / 5 stocks processed successfully")
    print(f"{'='*60}")

    # Save to JSON for next step
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auction_5stock_data.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n✅ Results saved to: {output_path}")

    # Print summary table
    print(f"\n{'Code':<8} {'PriceBefore%':>10} {'PriceChg%':>10} {'VolRatio':>8} {'C_1455':>8} {'C_1500':>8} {'PrevClose':>8}")
    print("-" * 68)
    for code in all_results:
        r = all_results[code]
        print(f"{code:<8} {r['price_before_pct']:+9.2f}% {r['price_change_pct']:+9.4f}% {r['volume_ratio']:8.4f} {r['close_1455']:8.2f} {r['close_1500']:8.2f} {r['prev_close']:8.2f}")


if __name__ == '__main__':
    main()
