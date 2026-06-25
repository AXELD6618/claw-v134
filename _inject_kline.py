"""
K-line data injector: reads TDX MCP kline responses from temp files
and updates tdx_realtime_input.json
"""
import json
import sys
import os

def main():
    json_path = 'tdx_realtime_input.json'
    
    # Read current file
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Process each candidate
    updated = 0
    for c in data['candidates']:
        code = c['code']
        temp_file = f'_kline_{code}.json'
        
        if os.path.exists(temp_file):
            with open(temp_file, 'r', encoding='utf-8') as f:
                kline_data = json.load(f)
            
            # Replace kline section
            c['kline'] = kline_data
            updated += 1
            print(f"  ✅ {code} {c['name']}: {len(kline_data.get('ListItem', []))} bars")
        else:
            print(f"  ⚠️ {code} {c['name']}: no temp file found")
    
    # Save updated JSON
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n✅ Updated {updated}/5 stocks")

if __name__ == '__main__':
    main()
