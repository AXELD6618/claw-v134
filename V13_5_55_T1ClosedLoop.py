#!/usr/bin/env python3
"""
V13.5.55 T+1 Closed-Loop Tracker — T日选股→T+1监控→反馈闭环
====================================================================
功能:
  1. 读取T日14:30选股结果(BUY候选)
  2. T+1日实时监控这些选股的涨跌表现
  3. 验证选股准确率(上涨/涨停=HIT, 下跌=MISS)
  4. 生成T+1验证数据→反馈V52进化引擎
  5. 同时监控当前持仓的T+1表现

闭环流程:
  T日14:30 选股 → data/v55_monitor/t_day_selection.json
  T+1 09:25-14:55 V55监控选股表现 → data/v55_monitor/t1_tracking.json
  T+1 15:10 V52进化 → data/evolution_v13552/t1_verified_dataset.json
"""

import os, sys, json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

class T1ClosedLoopTracker:
    """T+1闭环追踪器"""

    def __init__(self):
        self.monitor_dir = Path("data/v55_monitor")
        self.monitor_dir.mkdir(parents=True, exist_ok=True)
        self.selection_file = self.monitor_dir / "t_day_selection.json"
        self.tracking_file = self.monitor_dir / "t1_tracking.json"
        self.evolution_dataset = Path("data/evolution_v13552/t1_verified_dataset.json")

    def save_t_day_selection(self, buy_stocks: List[Dict]):
        """T日14:30选股后调用: 保存BUY候选股"""
        selection = {
            "selection_date": datetime.now().strftime("%Y-%m-%d"),
            "selection_time": datetime.now().isoformat(),
            "buy_count": len(buy_stocks),
            "stocks": buy_stocks,
        }
        with open(self.selection_file, "w", encoding="utf-8") as f:
            json.dump(selection, f, ensure_ascii=False, indent=2)
        print(f"[T1Tracker] Saved {len(buy_stocks)} BUY stocks for T+1 tracking")

    def load_t_day_selection(self) -> Optional[Dict]:
        """加载最近的T日选股结果"""
        if not self.selection_file.exists():
            return None
        with open(self.selection_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def track_t1_performance(self, realtime_quotes: Dict[str, Dict]) -> Dict:
        """
        T+1日实时追踪选股表现
        
        Args:
            realtime_quotes: {code: {price, zdf, inout, ...}}
        
        Returns:
            tracking result with HIT/MISS stats
        """
        selection = self.load_t_day_selection()
        if not selection:
            return {"error": "no_selection", "message": "No T-day selection found"}

        results = []
        hit_count = 0
        limit_up_count = 0

        for stock in selection.get("stocks", []):
            code = stock["code"]
            name = stock.get("name", code)
            score = stock.get("score", 0)
            signal = stock.get("signal", "BUY")

            quote = realtime_quotes.get(code, {})
            price = quote.get("Price", 0)
            zdf = quote.get("ZDF", 0)
            inout = quote.get("InOut", 0)

            # 判定HIT/MISS
            if zdf >= 9.9:
                status = "LIMIT_UP"
                limit_up_count += 1
                hit_count += 1
            elif zdf > 0:
                status = "UP"
                hit_count += 1
            elif zdf == 0:
                status = "FLAT"
            else:
                status = "DOWN"

            # 主买方向
            if inout > 0:
                main_buy = "POSITIVE"
            elif inout < 0:
                main_buy = "NEGATIVE"
            else:
                main_buy = "NEUTRAL"

            results.append({
                "code": code,
                "name": name,
                "t_day_score": score,
                "t_day_signal": signal,
                "t1_price": price,
                "t1_zdf": zdf,
                "t1_inout": inout,
                "t1_main_buy": main_buy,
                "status": status,
            })

        total = len(results)
        hit_rate = (hit_count / total * 100) if total > 0 else 0
        limit_up_rate = (limit_up_count / total * 100) if total > 0 else 0

        tracking_result = {
            "tracking_date": datetime.now().strftime("%Y-%m-%d"),
            "tracking_time": datetime.now().isoformat(),
            "selection_date": selection.get("selection_date"),
            "total_tracked": total,
            "hit_count": hit_count,
            "hit_rate": round(hit_rate, 1),
            "limit_up_count": limit_up_count,
            "limit_up_rate": round(limit_up_rate, 1),
            "results": results,
        }

        # 保存追踪结果
        with open(self.tracking_file, "w", encoding="utf-8") as f:
            json.dump(tracking_result, f, ensure_ascii=False, indent=2)

        print(f"[T1Tracker] T+1 Tracking: {hit_count}/{total} HIT ({hit_rate:.1f}%) | {limit_up_count} limit_up")
        return tracking_result

    def generate_evolution_feedback(self) -> List[Dict]:
        """生成V52进化引擎所需的T+1验证数据"""
        selection = self.load_t_day_selection()
        if not selection or not self.tracking_file.exists():
            return []

        with open(self.tracking_file, "r", encoding="utf-8") as f:
            tracking = json.load(f)

        feedback_data = []
        for r in tracking.get("results", []):
            feedback_data.append({
                "code": r["code"],
                "name": r["name"],
                "t_day_score": r["t_day_score"],
                "t_day_signal": r["t_day_signal"],
                "t1_zdf": r["t1_zdf"],
                "t1_status": r["status"],
                "t1_main_buy": r["t1_main_buy"],
                "hit": r["status"] in ("LIMIT_UP", "UP"),
                "date": tracking.get("tracking_date"),
            })

        # 追加到V52验证数据集
        if self.eolution_dataset.exists():
            with open(self.evolution_dataset, "r", encoding="utf-8") as f:
                existing = json.load(f)
        else:
            existing = []

        existing.extend(feedback_data)

        # 去重(同code+date只保留最新)
        seen = {}
        for item in existing:
            key = f"{item['code']}_{item.get('date', '')}"
            seen[key] = item
        deduped = list(seen.values())

        self.evolution_dataset.parent.mkdir(parents=True, exist_ok=True)
        with open(self.evolution_dataset, "w", encoding="utf-8") as f:
            json.dump(deduped, f, ensure_ascii=False, indent=2)

        print(f"[T1Tracker] Evolution feedback: {len(feedback_data)} records → {self.evolution_dataset}")
        return feedback_data

    def get_closed_loop_status(self) -> Dict:
        """获取闭环状态摘要"""
        selection = self.load_t_day_selection()
        tracking = None
        if self.tracking_file.exists():
            with open(self.tracking_file, "r", encoding="utf-8") as f:
                tracking = json.load(f)

        evolution_count = 0
        if self.evolution_dataset.exists():
            with open(self.evolution_dataset, "r", encoding="utf-8") as f:
                evolution_count = len(json.load(f))

        return {
            "selection_exists": selection is not None,
            "selection_date": selection.get("selection_date") if selection else None,
            "selection_count": selection.get("buy_count", 0) if selection else 0,
            "tracking_exists": tracking is not None,
            "tracking_date": tracking.get("tracking_date") if tracking else None,
            "hit_rate": tracking.get("hit_rate", 0) if tracking else 0,
            "limit_up_rate": tracking.get("limit_up_rate", 0) if tracking else 0,
            "evolution_dataset_count": evolution_count,
            "closed_loop_complete": selection is not None and tracking is not None,
        }


if __name__ == "__main__":
    tracker = T1ClosedLoopTracker()

    # 如果有选股结果, 生成进化反馈
    if tracker.load_t_day_selection():
        feedback = tracker.generate_evolution_feedback()
        status = tracker.get_closed_loop_status()
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        print("[T1Tracker] No T-day selection found. Run 14:30 selection first.")
        # 创建示例选股结果
        example_selection = [
            {"code": "688617", "name": "惠泰医疗", "score": 86.0, "signal": "BUY"},
            {"code": "301080", "name": "百普赛斯", "score": 89.8, "signal": "BUY"},
            {"code": "688050", "name": "爱博医疗", "score": 92.8, "signal": "BUY"},
        ]
        tracker.save_t_day_selection(example_selection)
        print("[T1Tracker] Created example selection for testing.")
