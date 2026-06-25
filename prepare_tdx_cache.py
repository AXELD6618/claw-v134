#!/usr/bin/env python3
"""
V13.0 TDX实时数据缓存注入器
============================
职责：
  1. 加载动态监控池（data/dynamic_watchlist.json）
  2. 为 run_tail_market_1430.py 准备数据格式模板
  3. 验证缓存数据完整性和时效性

使用方式（在WorkBuddy自动化中）：
  步骤1: WorkBuddy使用TDX MCP工具批量查询监控池股票实时行情+K线
  步骤2: 将查询结果按格式写入 data/tdx_realtime_input.json
  步骤3: 执行 python run_tail_market_1430.py（自动读取缓存）

缓存格式要求（tdx_realtime_input.json）：
{
  "fetched_at": "2026-06-23T14:30:00",
  "stocks": {
    "000001": {
      "name": "平安银行", "market": "0", "industry": "银行",
      "now": 12.50, "change_pct": 2.3, "volume": 15000000,
      "turnover": 1.2, "pe": 6.5,
      "daily_klines": [
        {"date":"2026-06-23","o":12.30,"h":12.60,"l":12.20,"c":12.50,"v":15000000},
        ...
      ]
    }
  }
}
"""

import json, os, sys
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

CACHE_PATH = os.path.join(os.path.dirname(__file__) or ".", "data", "tdx_realtime_input.json")
WATCHLIST_PATH = os.path.join(os.path.dirname(__file__) or ".", "data", "dynamic_watchlist.json")

def load_watchlist() -> List[Dict]:
    """加载动态监控池"""
    if os.path.exists(WATCHLIST_PATH):
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("stocks", [])
    return []

def validate_cache() -> Dict:
    """
    验证缓存文件完整性和时效性。
    返回: {valid: bool, issues: [str], stock_count: int, age_minutes: float}
    """
    result = {"valid": True, "issues": [], "stock_count": 0, "age_minutes": 0}

    if not os.path.exists(CACHE_PATH):
        result["valid"] = False
        result["issues"].append("缓存文件不存在")
        return result

    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        result["valid"] = False
        result["issues"].append(f"缓存文件损坏: {e}")
        return result

    # 检查时效性（缓存不应超过10分钟）
    fetched = data.get("fetched_at", "")
    if fetched:
        try:
            ft = datetime.fromisoformat(fetched)
            age = (datetime.now() - ft).total_seconds() / 60
            result["age_minutes"] = round(age, 1)
            if age > 10:
                result["issues"].append(f"缓存过期({age:.1f}分钟前)")
                result["valid"] = False
        except ValueError:
            result["issues"].append("缓存时间格式异常")

    # 检查数据完整性
    stocks = data.get("stocks", {})
    result["stock_count"] = len(stocks)
    
    if len(stocks) < 3:
        result["valid"] = False
        result["issues"].append(f"股票数量不足({len(stocks)}<3)")
    
    # 检查每条数据完整性
    incomplete = []
    for code, s in stocks.items():
        missing = []
        if not s.get("name"): missing.append("名称")
        if not s.get("now"): missing.append("现价")
        klines = s.get("daily_klines", [])
        if len(klines) < 20: missing.append(f"K线不足({len(klines)}<20)")
        if missing:
            incomplete.append(f"{code}({s.get('name','?')}): 缺{','.join(missing)}")
    
    if incomplete:
        result["issues"].extend(incomplete[:5])  # 只展示前5个
        if len(incomplete) > 5:
            result["issues"].append(f"...还有{len(incomplete)-5}只")
        if len(incomplete) > len(stocks) * 0.5:
            result["valid"] = False

    return result

def generate_cache_template(watchlist: List[Dict] = None) -> Dict:
    """
    生成缓存模板（供WorkBuddy填充TDX MCP数据）。
    WorkBuddy使用此模板批量查询TDX MCP后填入数据。
    """
    if watchlist is None:
        watchlist = load_watchlist()
    
    template = {
        "fetched_at": datetime.now().isoformat(),
        "source": "TDX_MCP",
        "total_stocks": len(watchlist),
        "stocks": {}
    }
    
    for s in watchlist:
        template["stocks"][s["code"]] = {
            "name": s["name"],
            "market": s.get("market", "0" if s["code"].startswith(("000","002","300")) else "1"),
            "industry": s.get("industry", "未知"),
            "now": None,
            "change_pct": None,
            "volume": None,
            "turnover": None,
            "pe": None,
            "daily_klines": []
        }
    
    return template

def write_cache(data: Dict):
    """写入缓存文件"""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[TDX注入] ✅ 缓存已写入: {CACHE_PATH} ({len(data.get('stocks',{}))}只)")

def inject_quote_to_cache(code: str, quote_data: Dict, kline_data: List[Dict] = None):
    """
    将单只股票的TDX MCP查询结果注入缓存。
    
    Args:
        code: 股票代码
        quote_data: TDX MCP tdx_quotes 返回的 HQInfo/BaseInfo 数据
        kline_data: TDX MCP tdx_kline 返回的K线数据列表
    """
    # 加载或创建缓存
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    else:
        cache = {"fetched_at": datetime.now().isoformat(), "source": "TDX_MCP", "stocks": {}}
    
    cache["fetched_at"] = datetime.now().isoformat()
    
    # 提取行情数据
    hq = quote_data.get("HQInfo", {})
    base = quote_data.get("BaseInfo", {})
    
    stock_entry = cache["stocks"].get(code, {})
    stock_entry["name"] = base.get("Name", stock_entry.get("name", code))
    stock_entry["now"] = hq.get("Now", stock_entry.get("now"))
    
    if hq.get("Close") and hq.get("Now"):
        stock_entry["change_pct"] = round((hq["Now"] - hq["Close"]) / hq["Close"] * 100, 2)
    
    stock_entry["volume"] = hq.get("Volume", stock_entry.get("volume"))
    stock_entry["turnover"] = hq.get("HSL", stock_entry.get("turnover"))
    stock_entry["pe"] = quote_data.get("CalcInfo", {}).get("PE", stock_entry.get("pe"))
    
    # 注入K线数据
    if kline_data:
        daily_klines = []
        for k in kline_data:
            daily_klines.append({
                "date": k.get("date", ""),
                "o": k.get("open", k.get("o", 0)),
                "h": k.get("high", k.get("h", 0)),
                "l": k.get("low", k.get("l", 0)),
                "c": k.get("close", k.get("c", 0)),
                "v": k.get("volume", k.get("v", 0))
            })
        stock_entry["daily_klines"] = daily_klines
    
    cache["stocks"][code] = stock_entry
    write_cache(cache)
    return cache


# ═══════════════════════════════════════════════
# 命令行接口
# ═══════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="V13.0 TDX缓存注入器")
    parser.add_argument("--validate", action="store_true", help="验证缓存有效性")
    parser.add_argument("--template", action="store_true", help="生成缓存模板")
    parser.add_argument("--top-n", type=int, default=30, help="模板中包含TOP N只股票(默认30)")
    args = parser.parse_args()

    if args.validate:
        result = validate_cache()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1

    if args.template:
        watchlist = load_watchlist()
        if not watchlist:
            print("⚠️ 未找到动态监控池，使用默认模板")
            watchlist = [
                {"code":"603259","name":"药明康德","market":"1","industry":"医药"},
                {"code":"300750","name":"宁德时代","market":"0","industry":"新能源"},
                {"code":"002230","name":"科大讯飞","market":"0","industry":"AI"},
                {"code":"601899","name":"紫金矿业","market":"1","industry":"有色"},
                {"code":"688256","name":"寒武纪","market":"1","industry":"AI芯片"},
            ]
        else:
            watchlist = watchlist[:args.top_n]
        
        template = generate_cache_template(watchlist)
        write_cache(template)
        print(f"[TDX注入] 模板已生成: {len(watchlist)}只股票待填充")
        return 0

    print("用法: python prepare_tdx_cache.py --validate | --template [--top-n 30]")
    return 0

if __name__ == "__main__":
    sys.exit(main())
