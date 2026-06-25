"""Temporary script to update kline data in tdx_realtime_input.json"""
import json

# Read current file
with open('tdx_realtime_input.json', 'r') as f:
    data = json.load(f)

print("Current kline status:")
for c in data['candidates']:
    items = c['kline']['ListItem']
    print(f"  {c['code']} {c['name']}: kline items={len(items)}")
