#!/usr/bin/env python3
"""
V13.4.1 实时指数写入工具 — 确保仪表盘指数数据永远来自真实TDX实时行情
=========================================================================
功能：
  1. 接受标准化指数数据 → 验证 → 写入 cache + deploy 双路径
  2. 自动生成 index_latest.json (仪表盘最终回退)
  3. 自动生成 index_{HHMM}.json (时段快照)
  4. 数据验证: 价格合理范围、涨跌幅合理范围、必填字段检查

调用方式:
  from save_realtime_index import save_index_data, validate_index_data
  
  data = {
      "000001": {"price": 4114.02, "change_pct": 0.08, "name": "上证指数"},
      "399006": {"price": 4266.30, "change_pct": 0.35, "name": "创业板指"}
  }
  save_index_data(data, period="1049")

红线: 严禁硬编码/模拟指数数据！
"""

import json
import os
import shutil
import time
from datetime import datetime
from typing import Dict, Optional, Tuple

# ============================================================
# 配置
# ============================================================
CACHE_DIR = "data/fullmarket_cache"
DEPLOY_DIR = "deploy/data"

# 合理性检查阈值
PRICE_RANGE = {
    "000001": (2500, 6000),   # 上证指数合理范围
    "399006": (1500, 5000),   # 创业板指合理范围
}
CHG_RANGE = (-15.0, 15.0)    # 涨跌幅合理范围 (A股指数有涨跌停限制)

# ============================================================
# 数据验证
# ============================================================

def validate_index_data(data: dict) -> Tuple[bool, list]:
    """
    验证指数数据合理性
    
    Args:
        data: {"000001": {"price": ..., "change_pct": ..., "name": ...}, ...}
    
    Returns:
        (is_valid, warnings_list)
    """
    warnings = []
    
    required_codes = ["000001", "399006"]
    for code in required_codes:
        if code not in data:
            warnings.append(f"❌ 缺少 {code}")
            continue
        
        entry = data[code]
        
        # 必填字段
        if "price" not in entry or entry["price"] is None:
            warnings.append(f"❌ {code} 缺少 price")
        if "change_pct" not in entry or entry["change_pct"] is None:
            warnings.append(f"❌ {code} 缺少 change_pct")
        if "name" not in entry or not entry["name"]:
            warnings.append(f"⚠️ {code} 缺少 name")
        
        # 价格合理性
        if code in PRICE_RANGE:
            lo, hi = PRICE_RANGE[code]
            price = entry.get("price", 0)
            if price < lo or price > hi:
                warnings.append(
                    f"❌ {code} 价格异常: {price} (合理范围 {lo}-{hi})"
                )
        
        # 涨跌幅合理性
        chg = entry.get("change_pct", 0)
        if chg < CHG_RANGE[0] or chg > CHG_RANGE[1]:
            warnings.append(
                f"❌ {code} 涨跌幅异常: {chg}% (合理范围 {CHG_RANGE[0]}-{CHG_RANGE[1]}%)"
            )
    
    return len([w for w in warnings if w.startswith("❌")]) == 0, warnings


# ============================================================
# 核心写入
# ============================================================

def save_index_data(
    data: dict,
    period: Optional[str] = None,
    validate: bool = True,
    dry_run: bool = False
) -> dict:
    """
    保存指数数据到 cache + deploy 双路径
    
    Args:
        data: 指数数据, 格式 {"000001": {"price": ..., "change_pct": ..., "name": ...}, ...}
        period: 时段标识, 如 "1030", "1430". None=使用当前时间.
        validate: 是否验证数据
        dry_run: 仅打印, 不写入
    
    Returns:
        {"success": bool, "files": [...], "warnings": [...]}
    
    红线: 数据必须来自真实TDX实时行情, 严禁硬编码!
    """
    result = {"success": False, "files": [], "warnings": []}
    
    # 验证
    if validate:
        is_valid, warnings = validate_index_data(data)
        result["warnings"] = warnings
        if not is_valid:
            print(f"[INDEX] ❌ 数据验证失败! 拒绝写入")
            for w in warnings:
                print(f"  {w}")
            return result
    
    # 添加时间戳
    now = datetime.now()
    timestamp = now.strftime("%H:%M")
    datestr = now.strftime("%Y%m%d")
    
    output = {
        "time": period if period else timestamp,
        "date": datestr,
        **data  # 展开 {"000001": {...}, "399006": {...}}
    }
    
    if dry_run:
        print(f"[INDEX] 🧪 DRY RUN - 不写入")
        print(json.dumps(output, ensure_ascii=False, indent=2))
        result["success"] = True
        return result
    
    # 确保目录存在
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(DEPLOY_DIR, exist_ok=True)
    
    # 确定文件名
    period_str = period if period else timestamp.replace(":", "")
    
    written_files = []
    
    try:
        # 1. 写入 cache (时段快照)
        cache_period = os.path.join(CACHE_DIR, f"index_{period_str}.json")
        with open(cache_period, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        written_files.append(cache_period)
        
        # 2. 写入 cache (latest, 仪表盘多源回退)
        cache_latest = os.path.join(CACHE_DIR, "index_latest.json")
        with open(cache_latest, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        written_files.append(cache_latest)
        
        # 3. 同步到 deploy (CloudStudio公网)
        deploy_period = os.path.join(DEPLOY_DIR, f"index_{period_str}.json")
        shutil.copy2(cache_period, deploy_period)
        written_files.append(deploy_period)
        
        deploy_latest = os.path.join(DEPLOY_DIR, "index_latest.json")
        shutil.copy2(cache_latest, deploy_latest)
        written_files.append(deploy_latest)
        
        result["success"] = True
        result["files"] = written_files
        
        # 打印摘要
        sh_info = data.get("000001", {})
        cy_info = data.get("399006", {})
        print(f"[INDEX] ✅ 指数数据已保存 (period={period_str})")
        print(f"  上证: {sh_info.get('price','?')} ({sh_info.get('change_pct',0):+.2f}%)")
        print(f"  创业板: {cy_info.get('price','?')} ({cy_info.get('change_pct',0):+.2f}%)")
        print(f"  写入 {len(written_files)} 个文件")
        
    except Exception as e:
        print(f"[INDEX] ❌ 写入失败: {e}")
        result["warnings"].append(f"写入异常: {e}")
    
    return result


# ============================================================
# CLI 入口 (用于自动化调用)
# ============================================================

if __name__ == "__main__":
    import sys
    
    print("═" * 60)
    print("  V13.4.1 实时指数写入工具")
    print("═" * 60)
    
    if len(sys.argv) < 2:
        print("\n用法:")
        print("  python save_realtime_index.py '<json_data>' [period]")
        print("\n示例:")
        print('  python save_realtime_index.py \'{"000001":{"price":4114,"change_pct":0.08,"name":"上证指数"},"399006":{"price":4266,"change_pct":0.35,"name":"创业板指"}}\' 1049')
        print("\n红线: 数据必须来自真实TDX实时行情, 严禁硬编码!")
        sys.exit(0)
    
    try:
        data = json.loads(sys.argv[1])
    except json.JSONDecodeError as e:
        print(f"❌ JSON解析失败: {e}")
        sys.exit(1)
    
    period = sys.argv[2] if len(sys.argv) > 2 else None
    
    result = save_index_data(data, period=period)
    
    if result["success"]:
        print(f"\n✅ 完成! {len(result['files'])} 个文件已写入")
    else:
        print(f"\n❌ 失败! 检查上方警告")
        sys.exit(1)
