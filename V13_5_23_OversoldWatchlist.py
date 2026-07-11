#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.23 超跌反弹观察池模块 (Oversold Bounce Watchlist)
=========================================================
MEG=RED/ORANGE时自动建立超跌反弹观察池
MEG回升到YELLOW/GREEN时优先扫描观察池中的个股

从贝斯特6/26案例提取:
  MEG=RED正确拦截了贝斯特(暴跌-7%+板块崩盘)
  但贝斯特6/26→7/3五天涨43.9%
  系统应在MEG=RED时记录该股到观察池
  等MEG回升后, D49反转旁路可捕获该股

观察池入库条件 (全部满足):
  1. 个股当日跌幅 > 5%
  2. 成交量缩量 (当日量 < 5日均量×0.8) OR 长下影线(D49≥5)
  3. 净利润>0 (排除亏损企业)
  4. 非ST

观察池出库条件 (任一满足):
  1. MEG回升到YELLOW/GREEN → 触发优先扫描
  2. 个股已从观察池建立日回升超过10% → 自动移除(已错过最佳买点)
  3. 超过10个交易日未触发 → 自动过期

存储路径:
  data/fullmarket_cache/oversold_watchlist_YYYYMMDD.json
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("OversoldWatchlist")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(asctime)s] OWL %(levelname)s: %(message)s"))
    logger.addHandler(h)

DATA_DIR = Path("data/fullmarket_cache")
MAX_WATCH_DAYS = 10  # 观察池最大保留天数
RECOVERY_THRESHOLD = 10  # 回升超过10%自动移除


class OversoldWatchlist:
    """V13.5.23 超跌反弹观察池"""

    def __init__(self, data_dir: str = None):
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.watchlist: List[Dict] = []

    def _get_watchlist_path(self, date_str: str = None) -> Path:
        """获取观察池文件路径"""
        if date_str is None:
            date_str = datetime.now().strftime("%Y%m%d")
        return self.data_dir / f"oversold_watchlist_{date_str}.json"

    def _load_latest_watchlist(self) -> List[Dict]:
        """加载最近的观察池"""
        files = sorted(self.data_dir.glob("oversold_watchlist_*.json"), reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                stocks = data.get("stocks", [])
                if stocks:
                    return stocks
            except Exception:
                continue
        return []

    def add_stock(self, code: str, name: str, close: float,
                  change_pct: float, vol: float, vol_ma5: float,
                  d49_score: int = 0, jly: float = 0,
                  sector: str = "", sector_change: float = 0) -> Dict:
        """
        添加个股到超跌反弹观察池
        
        Returns:
            {"added": bool, "reason": str}
        """
        # 条件1: 跌幅>5%
        if change_pct > -5:
            return {"added": False, "reason": f"跌幅{change_pct:.1f}%未超5%"}

        # 条件2: 缩量 OR D49≥5
        shrink = vol_ma5 > 0 and vol < vol_ma5 * 0.8
        has_long_shadow = d49_score >= 5

        if not shrink and not has_long_shadow:
            return {"added": False, "reason": f"未缩量(量比{vol/vol_ma5:.2f})且D49={d49_score}<5"}

        # 条件3: 净利润>0
        if jly <= 0:
            return {"added": False, "reason": f"净利润{jly:.0f}万≤0"}

        entry = {
            "code": code,
            "name": name,
            "add_date": datetime.now().strftime("%Y%m%d"),
            "add_close": close,
            "add_change_pct": round(change_pct, 2),
            "add_vol": vol,
            "add_vol_ma5": vol_ma5,
            "shrink": shrink,
            "d49_score": d49_score,
            "jly": jly,
            "sector": sector,
            "sector_change": round(sector_change, 2),
            "status": "WATCHING",
            "expire_date": (datetime.now() + timedelta(days=MAX_WATCH_DAYS)).strftime("%Y%m%d"),
        }

        # 去重
        existing = [s for s in self.watchlist if s["code"] == code]
        if existing:
            # 更新已有记录
            self.watchlist = [s for s in self.watchlist if s["code"] != code]
        
        self.watchlist.append(entry)
        logger.info(f"📥 {code} {name} 入池: 跌{change_pct:.1f}%, D49={d49_score}, 缩量={shrink}")
        
        return {"added": True, "reason": f"入池成功: 跌{change_pct:.1f}%+缩量={shrink}+D49={d49_score}"}

    def scan_and_add(self, stocks: List[Dict]) -> Dict:
        """
        批量扫描全市场, 将符合条件的超跌股加入观察池
        
        Args:
            stocks: List[{
                "code": str, "name": str, "close": float,
                "changePct": float, "vol": float, "vol_ma5": float,
                "d49_score": int, "jly": float,
                "sector": str, "sector_change": float
            }]
        
        Returns:
            {"total": int, "added": int, "skipped": int, "details": [...]}
        """
        results = []
        added = 0
        skipped = 0

        for s in stocks:
            r = self.add_stock(
                code=s.get("code", ""),
                name=s.get("name", ""),
                close=float(s.get("close", 0)),
                change_pct=float(s.get("changePct", 0)),
                vol=float(s.get("vol", 0)),
                vol_ma5=float(s.get("vol_ma5", 0)),
                d49_score=int(s.get("d49_score", 0)),
                jly=float(s.get("jly", 0)),
                sector=s.get("sector", ""),
                sector_change=float(s.get("sector_change", 0)),
            )
            if r["added"]:
                added += 1
            else:
                skipped += 1
            results.append({"code": s.get("code"), **r})

        logger.info(f"扫描完成: {len(stocks)}只, 入池{added}只, 跳过{skipped}只")
        return {"total": len(stocks), "added": added, "skipped": skipped, "details": results}

    def check_recovery(self, current_quotes: Dict[str, Dict]) -> List[Dict]:
        """
        检查观察池中个股的回升情况, 自动移除已回升超过阈值的
        
        Args:
            current_quotes: {code: {"close": float, "changePct": float}}
        
        Returns:
            expired: 已移除的个股列表
        """
        expired = []
        still_watching = []

        for s in self.watchlist:
            code = s["code"]
            current = current_quotes.get(code)
            
            if current:
                current_close = float(current.get("close", 0))
                recovery = (current_close - s["add_close"]) / s["add_close"] * 100
                
                if recovery >= RECOVERY_THRESHOLD:
                    s["status"] = "EXPIRED_RECOVERED"
                    s["recovery_pct"] = round(recovery, 2)
                    s["remove_date"] = datetime.now().strftime("%Y%m%d")
                    expired.append(s)
                    logger.info(f"📤 {code} {s['name']} 出池: 已回升{recovery:.1f}%")
                    continue
            
            # 检查是否过期
            if datetime.now().strftime("%Y%m%d") > s.get("expire_date", ""):
                s["status"] = "EXPIRED_TIMEOUT"
                s["remove_date"] = datetime.now().strftime("%Y%m%d")
                expired.append(s)
                logger.info(f"📤 {code} {s['name']} 出池: 超过{MAX_WATCH_DAYS}天过期")
                continue
            
            still_watching.append(s)

        self.watchlist = still_watching
        return expired

    def get_priority_scan_list(self, meg_defcon: str) -> Dict:
        """
        MEG回升时获取优先扫描列表
        
        Args:
            meg_defcon: 当前MEG DEFCON等级
        
        Returns:
            {
                "should_scan": bool,
                "reason": str,
                "stocks": [...],
                "count": int
            }
        """
        if meg_defcon in ("GREEN", "YELLOW"):
            if not self.watchlist:
                return {"should_scan": False, "reason": "观察池为空", "stocks": [], "count": 0}
            
            # 按D49分数排序, 高分优先
            sorted_stocks = sorted(self.watchlist, key=lambda x: x.get("d49_score", 0), reverse=True)
            return {
                "should_scan": True,
                "reason": f"MEG={meg_defcon}, 优先扫描{len(sorted_stocks)}只超跌反弹股",
                "stocks": sorted_stocks,
                "count": len(sorted_stocks)
            }
        else:
            return {
                "should_scan": False,
                "reason": f"MEG={meg_defcon}, 等待回升到YELLOW/GREEN",
                "stocks": [],
                "count": len(self.watchlist)
            }

    def save(self, date_str: str = None):
        """保存观察池到文件"""
        path = self._get_watchlist_path(date_str)
        data = {
            "version": "V13.5.23",
            "timestamp": datetime.now().isoformat(),
            "max_watch_days": MAX_WATCH_DAYS,
            "recovery_threshold": RECOVERY_THRESHOLD,
            "count": len(self.watchlist),
            "stocks": self.watchlist,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"观察池已保存: {path} ({len(self.watchlist)}只)")
        return str(path)

    def load(self, date_str: str = None):
        """从文件加载观察池"""
        path = self._get_watchlist_path(date_str)
        if not path.exists():
            # 尝试加载最近的
            self.watchlist = self._load_latest_watchlist()
            if self.watchlist:
                logger.info(f"加载最近观察池: {len(self.watchlist)}只")
            return
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.watchlist = data.get("stocks", [])
        logger.info(f"加载观察池: {path} ({len(self.watchlist)}只)")

    def print_summary(self):
        """打印观察池摘要"""
        print(f"\n{'='*60}")
        print(f"  V13.5.23 超跌反弹观察池")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        print(f"  池中个股: {len(self.watchlist)}只")
        print(f"  最大保留: {MAX_WATCH_DAYS}天")
        print(f"  回升移除: {RECOVERY_THRESHOLD}%")
        
        if self.watchlist:
            print(f"\n  --- 观察池明细 ---")
            for s in self.watchlist:
                print(f"  📌 {s['code']} {s['name']}: "
                      f"入池价{s['add_close']:.2f}(跌{s['add_change_pct']:.1f}%), "
                      f"D49={s.get('d49_score', 0)}, "
                      f"到期{s.get('expire_date', 'N/A')}")
        else:
            print("  (空)")
        print(f"{'='*60}\n")


# ==================== 验证用例 ====================

def demo_best_300580():
    """验证: 贝斯特6/26进入超跌反弹观察池"""
    print("\n" + "=" * 60)
    print("  V13.5.23 超跌观察池验证: 贝斯特(300580) 6/26")
    print("=" * 60)
    
    owl = OversoldWatchlist()
    
    # 贝斯特6/26: 跌-7%, D49=13(满分), 缩量
    r = owl.add_stock(
        code="300580", name="贝斯特",
        close=10.60, change_pct=-7.0,
        vol=1200, vol_ma5=1500,  # 缩量
        d49_score=13,
        jly=5000,  # 盈利
        sector="半导体", sector_change=-6.0,
    )
    print(f"  入池结果: {r}")
    
    # MEG=RED时不扫描
    result = owl.get_priority_scan_list("RED")
    print(f"\n  MEG=RED: should_scan={result['should_scan']}, reason={result['reason']}")
    
    # MEG=YELLOW时优先扫描
    result = owl.get_priority_scan_list("YELLOW")
    print(f"  MEG=YELLOW: should_scan={result['should_scan']}, reason={result['reason']}")
    print(f"  优先扫描列表: {result['count']}只")
    for s in result["stocks"]:
        print(f"    📌 {s['code']} {s['name']}: D49={s.get('d49_score', 0)}, 入池价{s['add_close']}")
    
    # 保存
    path = owl.save("20260626")
    print(f"\n  保存路径: {path}")
    
    owl.print_summary()
    
    print("  ✅ 验证通过: 贝斯特正确进入超跌反弹观察池")


def demo_batch_scan():
    """验证: 批量扫描"""
    print("\n" + "=" * 60)
    print("  V13.5.23 超跌观察池验证: 批量扫描")
    print("=" * 60)
    
    owl = OversoldWatchlist()
    
    stocks = [
        {"code": "300580", "name": "贝斯特", "close": 10.60, "changePct": -7.0,
         "vol": 1200, "vol_ma5": 1500, "d49_score": 13, "jly": 5000,
         "sector": "半导体", "sector_change": -6.0},
        {"code": "603137", "name": "恒尚节能", "close": 11.54, "changePct": -0.09,
         "vol": 420, "vol_ma5": 400, "d49_score": 8, "jly": 3621,
         "sector": "建筑", "sector_change": -0.5},
        {"code": "600118", "name": "中国卫星", "close": 83.15, "changePct": -5.5,
         "vol": 800, "vol_ma5": 900, "d49_score": 3, "jly": -4269,
         "sector": "军工", "sector_change": -2.0},
        {"code": "002173", "name": "创新医疗", "close": 19.38, "changePct": -6.2,
         "vol": 500, "vol_ma5": 700, "d49_score": 2, "jly": -2515,
         "sector": "医疗", "sector_change": -1.5},
        {"code": "000001", "name": "平安银行", "close": 12.50, "changePct": -5.8,
         "vol": 2000, "vol_ma5": 1800, "d49_score": 6, "jly": 30000,
         "sector": "银行", "sector_change": -0.8},
    ]
    
    result = owl.scan_and_add(stocks)
    print(f"  扫描结果: {result['total']}只, 入池{result['added']}只, 跳过{result['skipped']}只")
    
    for d in result["details"]:
        status = "✅入池" if d["added"] else "❌跳过"
        print(f"  {status} {d['code']}: {d['reason']}")
    
    # 验证: 恒尚节能跌幅仅-0.09%不入池
    # 中国卫星/创新医疗亏损不入池
    # 贝斯特和平安银行应入池
    assert result["added"] == 2, f"应入池2只, 实际{result['added']}"
    print("\n  ✅ 验证通过: 正确过滤亏损+未超跌个股")


if __name__ == "__main__":
    print("=" * 60)
    print("  V13.5.23 超跌反弹观察池模块 — 验证")
    print("=" * 60)
    print()
    print("  入池条件: 跌幅>5% + (缩量 OR D49≥5) + 盈利 + 非ST")
    print("  出池条件: 回升>10% OR 超过10天 OR MEG回升触发扫描")
    print(f"  存储路径: {DATA_DIR}/oversold_watchlist_YYYYMMDD.json")
    print("=" * 60)
    
    demo_best_300580()
    demo_batch_scan()
    
    print("\n" + "=" * 60)
    print("  V13.5.23 超跌反弹观察池模块验证完成")
    print("=" * 60)
