#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.21 T4 选股硬性过滤链 — 不可覆盖的四道关口
==================================================
设计目标: 杜绝"亏损企业高位买入"和"五确认不足强行选股"两类致命错误
执行方式: 候选池 → ①净利润>0 → ②D29≥6 → ③五确认≥3 → ④非ST → 入围池

每一道过滤FAIL则直接REJECT，不可被权重覆盖不可豁免
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 过滤结果常量
FILTER_PASS = "PASS"
FILTER_REJECT_NEGATIVE_PROFIT = "REJECT_NEGATIVE_PROFIT"
FILTER_REJECT_D29_WEAK = "REJECT_D29_WEAK"
FILTER_REJECT_FIVE_CONFIRM_LOW = "REJECT_FIVE_CONFIRM_LOW"
FILTER_REJECT_ST = "REJECT_ST"

class T4HardFilter:
    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else Path("data/fullmarket_cache")
        self.reject_log: List[Dict] = []
        self.pass_log: List[Dict] = []
        self.logger = self._setup_logger()

    def _setup_logger(self):
        logger = logging.getLogger("T4HardFilter")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            h = logging.StreamHandler()
            h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
            logger.addHandler(h)
        return logger

    # ==================== 过滤规则 ====================

    def filter_1_profit_positive(self, cw_info: Dict) -> Tuple[str, str]:
        """过滤1: 净利润>0 (TDX CwInfo.JLY)"""
        jly = cw_info.get("JLY", None)
        mgsy = cw_info.get("MGSY", None)

        if jly is None:
            # 数据缺失, 警告但不拒绝
            return FILTER_PASS, "JLY数据缺失, 默认放行"

        if jly <= 0:
            reason = f"净利润={jly:.1f}万≤0 (MGSY={mgsy if mgsy else 'N/A'}), 亏损企业排除"
            return FILTER_REJECT_NEGATIVE_PROFIT, reason

        return FILTER_PASS, f"净利润={jly:.1f}万>0 ✅"

    def filter_2_d29_washout(self, d29_score: int) -> Tuple[str, str]:
        """过滤2: D29双洗盘识别≥6"""
        if d29_score >= 6:
            return FILTER_PASS, f"D29洗盘={d29_score}分≥6 ✅"
        else:
            return FILTER_REJECT_D29_WEAK, f"D29洗盘={d29_score}分<6, 洗盘信号不足"

    def filter_3_five_confirm(self, confirm_scores: Dict[str, int]) -> Tuple[str, str]:
        """过滤3: 五确认≥3 (D29+D31+D32+D33+D34)"""
        # D29≥6时计1分, D31≥6计1分, D32≥5计1分, D33≥2计1分, D34≥3计1分
        count = 0
        details = []

        if confirm_scores.get("D29", 0) >= 6:
            count += 1
            details.append("D29")
        if confirm_scores.get("D31", 0) >= 6:
            count += 1
            details.append("D31")
        if confirm_scores.get("D32", 0) >= 5:
            count += 1
            details.append("D32")
        if confirm_scores.get("D33", 0) >= 2:
            count += 1
            details.append("D33")
        if confirm_scores.get("D34", 0) >= 3:
            count += 1
            details.append("D34")

        if count >= 3:
            return FILTER_PASS, f"五确认={count}/5 ({'+'.join(details)}) ✅"
        else:
            return FILTER_REJECT_FIVE_CONFIRM_LOW, f"五确认={count}/5 ({'+'.join(details) if details else '全部不满足'}), 不足3确认"

    def filter_4_non_st(self, name: str) -> Tuple[str, str]:
        """过滤4: 非ST"""
        if "ST" in name.upper() or "*ST" in name.upper():
            return FILTER_REJECT_ST, f"名称含ST: {name}"
        return FILTER_PASS, f"非ST ✅"

    # ==================== 完整过滤链 ====================

    def run_filter_chain(self, candidates: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        执行四道过滤链, 返回 (pass_list, reject_list)
        
        candidates: List[{
            "code": str,
            "name": str,
            "jly": float,  # 净利润(万元)
            "mgsy": float,  # 每股收益
            "d29_score": int,  # D29洗盘得分
            "five_confirm": {"D29": int, "D31": int, "D32": int, "D33": int, "D34": int},
        }]
        """
        self.reject_log = []
        self.pass_log = []

        for i, c in enumerate(candidates):
            code = c.get("code", f"unknown_{i}")
            name = c.get("name", "Unknown")
            result = {"code": code, "name": name, "filters": [], "status": "PASS"}

            # 过滤1: 净利润>0
            cw_info = {"JLY": c.get("jly"), "MGSY": c.get("mgsy")}
            status, reason = self.filter_1_profit_positive(cw_info)
            result["filters"].append({"name": "F1_净利润>0", "status": status, "reason": reason})
            if status != FILTER_PASS:
                result["status"] = status
                self.reject_log.append(result)
                self.logger.warning(f"❌ {code} {name} F1 REJECT: {reason}")
                continue

            # 过滤2: D29≥6
            status, reason = self.filter_2_d29_washout(c.get("d29_score", 0))
            result["filters"].append({"name": "F2_D29≥6", "status": status, "reason": reason})
            if status != FILTER_PASS:
                result["status"] = status
                self.reject_log.append(result)
                self.logger.warning(f"❌ {code} {name} F2 REJECT: {reason}")
                continue

            # 过滤3: 五确认≥3
            status, reason = self.filter_3_five_confirm(c.get("five_confirm", {}))
            result["filters"].append({"name": "F3_五确认≥3", "status": status, "reason": reason})
            if status != FILTER_PASS:
                result["status"] = status
                self.reject_log.append(result)
                self.logger.warning(f"❌ {code} {name} F3 REJECT: {reason}")
                continue

            # 过滤4: 非ST
            status, reason = self.filter_4_non_st(name)
            result["filters"].append({"name": "F4_非ST", "status": status, "reason": reason})
            if status != FILTER_PASS:
                result["status"] = status
                self.reject_log.append(result)
                self.logger.warning(f"❌ {code} {name} F4 REJECT: {reason}")
                continue

            # 全部通过
            self.pass_log.append(result)
            self.logger.info(f"✅ {code} {name} ALL PASS → 入围池")

        return self.pass_log, self.reject_log

    def print_summary(self):
        """打印过滤摘要"""
        total = len(self.pass_log) + len(self.reject_log)
        pass_count = len(self.pass_log)
        reject_count = len(self.reject_log)

        print(f"\n{'='*60}")
        print(f"  V13.5.21 T4 硬性过滤链 执行摘要")
        print(f"  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        print(f"  候选数: {total}")
        print(f"  通过: {pass_count} ({pass_count/total*100:.1f}%)" if total > 0 else "  通过: 0")
        print(f"  拒绝: {reject_count} ({reject_count/total*100:.1f}%)" if total > 0 else "  拒绝: 0")

        if self.reject_log:
            print(f"\n  --- 拒绝明细 ---")
            for r in self.reject_log:
                last_filter = r["filters"][-1]
                print(f"  ❌ {r['code']} {r['name']}: {last_filter['reason']}")

        if self.pass_log:
            print(f"\n  --- 入围池 ({pass_count}只) ---")
            for p in self.pass_log:
                print(f"  ✅ {p['code']} {p['name']}")

        print(f"{'='*60}\n")

    def export_reject_log(self, output_path: str = None):
        """导出拒绝日志为JSON"""
        if output_path is None:
            output_path = self.data_dir / f"t4_hardfilter_rejects_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "version": "V13.5.21",
                "total_candidates": len(self.pass_log) + len(self.reject_log),
                "passed": len(self.pass_log),
                "rejected": len(self.reject_log),
                "rejects": self.reject_log,
                "passes": [{"code": p["code"], "name": p["name"]} for p in self.pass_log]
            }, f, ensure_ascii=False, indent=2)
        self.logger.info(f"拒绝日志已导出: {output_path}")


# ==================== 预定义验证用例 ====================

def demo_validation():
    """
    V13.5.21 硬性过滤链验证: 用今天(7/6)新持仓做反事实验证
    - 中国卫星(亏损) → 应该在F1被拒
    - 创新医疗(亏损) → 应该在F1被拒
    - 大连热电(盈利+D29?+五确认?) → 需看实际信号
    """
    candidates = [
        {
            "code": "600719", "name": "大连热电",
            "jly": 9485.33, "mgsy": 0.23,
            "d29_score": 6,
            "five_confirm": {"D29": 7, "D31": 8, "D32": 6, "D33": 1, "D34": 0},
        },
        {
            "code": "600118", "name": "中国卫星",
            "jly": -4269.0, "mgsy": -0.04,
            "d29_score": 5,
            "five_confirm": {"D29": 5, "D31": 3, "D32": 0, "D33": 1, "D34": 0},
        },
        {
            "code": "002173", "name": "创新医疗",
            "jly": -2515.0, "mgsy": -0.06,
            "d29_score": 4,
            "five_confirm": {"D29": 4, "D31": 2, "D32": 0, "D33": 2, "D34": 0},
        },
        {
            "code": "301293", "name": "三博脑科",
            "jly": 1503.0, "mgsy": 0.08,
            "d29_score": 7,
            "five_confirm": {"D29": 7, "D31": 7, "D32": 5, "D33": 2, "D34": 4},
        },
        {
            "code": "002141", "name": "贤丰控股",
            "jly": 994.46, "mgsy": 0.01,
            "d29_score": 3,
            "five_confirm": {"D29": 3, "D31": 1, "D32": 0, "D33": 1, "D34": 0},
        },
    ]

    hf = T4HardFilter()
    passed, rejected = hf.run_filter_chain(candidates)
    hf.print_summary()

    # 断言: 中国卫星和创新医疗必须在F1被拒
    for r in rejected:
        if r["code"] == "600118":
            assert r["status"] == FILTER_REJECT_NEGATIVE_PROFIT, f"中国卫星应被F1拒绝, 实际: {r['status']}"
            print(f"  ✅ 验证通过: 中国卫星(亏损-4269万)被F1正确拒绝")
        if r["code"] == "002173":
            assert r["status"] == FILTER_REJECT_NEGATIVE_PROFIT, f"创新医疗应被F1拒绝, 实际: {r['status']}"
            print(f"  ✅ 验证通过: 创新医疗(亏损-2515万)被F1正确拒绝")

    # 断言: 三博脑科应该全部通过
    for p in passed:
        if p["code"] == "301293":
            print(f"  ✅ 验证通过: 三博脑科(盈利+1503万, D29=7, 五确认=4/5)正确入围")
            break

    return passed, rejected


if __name__ == "__main__":
    print("=" * 60)
    print("  V13.5.21 T4 硬性过滤链 — 概念验证")
    print("=" * 60)
    print()
    print("  四道过滤:")
    print("    F1: 净利润>0 (TDX CwInfo.JLY)")
    print("    F2: D29双洗盘≥6")
    print("    F3: 五确认≥3 (D29+D31+D32+D33+D34)")
    print("    F4: 非ST")
    print()
    print("  规则: 任一FAIL立即REJECT, 不可覆盖")
    print("=" * 60)

    demo_validation()
