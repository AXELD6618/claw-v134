"""
V13.4 Cloud Holy Grail Engine
Runs in GitHub Actions — 14:30 CST T5 execution.
Loads candidate pool → applies 8-factor scoring → outputs ranked signals.

Design principles:
1. Zero MCP/TDX dependency — pure Python + akshare
2. File-driven state — reads cloud_outputs/candidate_pool_latest.json → writes holy_grail_signals.json
3. 4-level degradation: full_akshare → cached_akshare → static_factors → empty_marker
"""
import os, sys, json, time, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

CLOUD_ROOT = Path(__file__).parent
OUTPUT_DIR = CLOUD_ROOT / "cloud_outputs"
STATE_FILE = OUTPUT_DIR / "holy_grail_state.json"
SIGNALS_FILE = OUTPUT_DIR / "holy_grail_signals.json"
CANDIDATE_FILE = OUTPUT_DIR / "candidate_pool_latest.json"
CACHE_DIR = CLOUD_ROOT / "cloud_cache"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False


class HolyGrailEngine:
    """Cloud-native holy grail stock selector."""

    # === 8-Factor Scoring Weights ===
    FACTOR_WEIGHTS = {
        "f1_momentum":      0.20,   # Price momentum (14:00-14:30)
        "f2_volume_surge":  0.18,   # Volume surge vs avg
        "f3_turnover":      0.12,   # Turnover rate quality
        "f4_sector_strength": 0.10, # Sector/industry ranking
        "f5_market_cap":    0.08,   # Float market cap sweet spot
        "f6_amplitude":     0.10,   # Intraday amplitude
        "f7_limit_proximity": 0.12, # Distance to limit-up (near but not at)
        "f8_volume_ratio":  0.10,   # Volume vs 5-day average
    }

    # Market cap sweet spot: small/mid cap A-shares (in RMB)
    CAP_SWEET_MIN = 5e8     # 5亿 (exclude micro-cap junk)
    CAP_SWEET_MAX = 2e10    # 200亿
    CAP_OK_MAX = 1e11       # 1000亿 (large cap OK but lower score)

    def __init__(self):
        self.state = self._load_state()
        self.today = datetime.now().strftime("%Y%m%d")
        self.now = datetime.now()

    def _load_state(self) -> Dict:
        if STATE_FILE.exists():
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        return {"runs": [], "total_signals": 0, "degradation_level": 0}

    def _save_state(self):
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _save_signals(self, signals: List[Dict], level: int):
        """Save holy grail signals to JSON file."""
        output = {
            "timestamp": datetime.now().isoformat(),
            "date": self.today,
            "degradation_level": level,
            "total_signals": len(signals),
            "signals": signals,
            "checksum": hashlib.md5(json.dumps(signals, sort_keys=True, default=str).encode()).hexdigest(),
        }
        with open(SIGNALS_FILE, "w") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # Update state
        self.state["runs"].append({
            "timestamp": output["timestamp"],
            "signals": len(signals),
            "level": level,
        })
        self.state["total_signals"] += len(signals)
        self._save_state()

        # Also save dated copy
        dated = OUTPUT_DIR / f"holy_grail_{self.today}.json"
        with open(dated, "w") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

    def load_candidates(self) -> List[Dict]:
        """Load candidate pool from previous pipeline steps."""
        if CANDIDATE_FILE.exists():
            with open(CANDIDATE_FILE, "r") as f:
                data = json.load(f)
            candidates = data.get("stocks", [])
            print(f"[HG] Loaded {len(candidates)} candidates from {data.get('timestamp')}")
            return candidates
        print("[HG] No candidate pool found")
        return []

    def _score_f1_momentum(self, stock: Dict) -> Tuple[float, str]:
        """Price momentum — positive change with acceleration preference."""
        pct = stock.get("pct_chg") or 0
        if 5 <= pct <= 8:
            return 1.0, "STRONG_MOMENTUM"
        elif 3 <= pct < 5:
            return 0.8, "GOOD_MOMENTUM"
        elif 1 <= pct < 3:
            return 0.5, "WEAK_MOMENTUM"
        elif 8 < pct < 9.5:  # Approaching limit-up
            return 0.9, "NEAR_LIMIT"
        elif pct >= 9.5:
            return 0.3, "ALREADY_LIMIT"  # Already limit-up, can't buy
        elif 0 <= pct < 1:
            return 0.2, "FLAT"
        else:
            return 0.0, "NEGATIVE"

    def _score_f2_volume_surge(self, stock: Dict) -> Tuple[float, str]:
        """Volume surge — current volume vs average."""
        vol_ratio = stock.get("volume_ratio") or 1
        turnover = stock.get("turnover") or 0

        if vol_ratio > 3 and turnover > 5:
            return 1.0, "VOL_SURGE_STRONG"
        elif vol_ratio > 2:
            return 0.7, "VOL_SURGE_GOOD"
        elif vol_ratio > 1.5:
            return 0.5, "VOL_SURGE_OK"
        elif vol_ratio > 1.0:
            return 0.3, "VOL_NORMAL"
        else:
            return 0.1, "VOL_WEAK"

    def _score_f3_turnover(self, stock: Dict) -> Tuple[float, str]:
        """Turnover rate quality."""
        turnover = stock.get("turnover") or 0
        if 8 <= turnover <= 25:
            return 1.0, "TURNOVER_IDEAL"
        elif 5 <= turnover < 8:
            return 0.8, "TURNOVER_ACTIVE"
        elif 3 <= turnover < 5:
            return 0.5, "TURNOVER_OK"
        elif turnover > 30:
            return 0.3, "TURNOVER_EXCESSIVE"  # Too high, possible manipulation
        elif turnover > 0:
            return 0.2, "TURNOVER_LOW"
        else:
            return 0.0, "NO_DATA"

    def _score_f4_sector_strength(self, stock: Dict) -> Tuple[float, str]:
        """Sector/industry strength (proxy via stock's own performance)."""
        # Cloud mode: use stock's pct_chg as proxy for sector strength
        # In full TDX mode, we'd cross-reference with sector index
        pct = stock.get("pct_chg") or 0
        if pct > 4:
            return 0.8, "SECTOR_PROXY_STRONG"
        elif pct > 2:
            return 0.6, "SECTOR_PROXY_OK"
        elif pct > 0:
            return 0.4, "SECTOR_PROXY_WEAK"
        else:
            return 0.1, "SECTOR_PROXY_NEGATIVE"

    def _score_f5_market_cap(self, stock: Dict) -> Tuple[float, str]:
        """Float market cap sweet spot."""
        fmv = stock.get("float_mv") or 0
        if self.CAP_SWEET_MIN <= fmv <= self.CAP_SWEET_MAX:
            return 1.0, "CAP_SWEET"
        elif self.CAP_SWEET_MAX < fmv <= self.CAP_OK_MAX:
            return 0.7, "CAP_OK"
        elif fmv > self.CAP_OK_MAX:
            return 0.3, "CAP_LARGE"
        elif fmv > 0:
            return 0.4, "CAP_MICRO"
        else:
            return 0.2, "CAP_UNKNOWN"

    def _score_f6_amplitude(self, stock: Dict) -> Tuple[float, str]:
        """Intraday amplitude — active price discovery."""
        amp = stock.get("amplitude") or 0
        if 5 <= amp <= 10:
            return 1.0, "AMP_ACTIVE"
        elif 3 <= amp < 5:
            return 0.7, "AMP_MODERATE"
        elif amp > 12:
            return 0.4, "AMP_EXCESSIVE"  # Too volatile
        elif amp > 1:
            return 0.3, "AMP_LOW"
        else:
            return 0.1, "AMP_FLAT"

    def _score_f7_limit_proximity(self, stock: Dict) -> Tuple[float, str]:
        """Distance to limit-up — near but not at it."""
        pct = stock.get("pct_chg") or 0
        # For A-shares: 10% main board, 20% ChiNext/STAR
        # Simplified: assume 10% limit
        distance_to_limit = 10 - pct
        if 1 <= distance_to_limit <= 3:
            return 1.0, "NEAR_LIMIT_IDEAL"
        elif 3 < distance_to_limit <= 5:
            return 0.8, "NEAR_LIMIT_GOOD"
        elif 0.5 <= distance_to_limit < 1:
            return 0.6, "VERY_NEAR_LIMIT"
        elif pct >= 9.8:
            return 0.0, "AT_LIMIT"
        else:
            return 0.3, "FAR_FROM_LIMIT"

    def _score_f8_volume_ratio(self, stock: Dict) -> Tuple[float, str]:
        """Volume ratio vs 5-day average."""
        vr = stock.get("volume_ratio") or 1
        if vr > 3:
            return 1.0, "VR_SURGE"
        elif vr > 2:
            return 0.8, "VR_HIGH"
        elif vr > 1.5:
            return 0.6, "VR_ELEVATED"
        elif vr > 1.0:
            return 0.4, "VR_NORMAL"
        else:
            return 0.2, "VR_LOW"

    def score_stock(self, stock: Dict) -> Dict:
        """Apply all 8 factors and compute weighted score."""
        scores = {}
        details = {}
        total = 0.0

        for factor, weight in self.FACTOR_WEIGHTS.items():
            method = getattr(self, f"_score_{factor}")
            score, label = method(stock)
            scores[factor] = score
            details[factor] = {"score": score, "label": label, "weight": weight}
            total += score * weight

        stock["hg_score"] = round(total, 4)
        stock["hg_factors"] = details
        stock["hg_signals"] = [d["label"] for d in details.values() if d["score"] >= 0.7]
        return stock

    def run_level_1_full(self) -> List[Dict]:
        """Level 1: Full akshare + candidate pool scoring."""
        print("[HG:L1] Running full akshare pipeline...")

        # Try to refresh data
        refresh_success = False
        if HAS_AKSHARE:
            try:
                import cloud_data_fetcher as cdf
                data = cdf.fetch_full_pipeline_data()
                candidates = cdf.export_candidate_pool(data, min_score=4, top_n=100)
                refresh_success = True
                print(f"[HG:L1] Fresh data: {len(candidates)} candidates")
            except Exception as e:
                print(f"[HG:L1] Refresh failed: {e}")

        if not refresh_success:
            candidates = self.load_candidates()

        # Score all candidates
        for stock in candidates:
            self.score_stock(stock)

        # Filter and rank
        signals = [s for s in candidates if s.get("hg_score", 0) >= 0.45]
        signals.sort(key=lambda x: x.get("hg_score", 0), reverse=True)

        return signals[:20]  # Top 20 holy grail signals

    def run_level_2_cached(self) -> List[Dict]:
        """Level 2: Use cached candidate pool + score only."""
        print("[HG:L2] Using cached candidate pool...")
        candidates = self.load_candidates()
        if not candidates:
            print("[HG:L2] No cached candidates, falling to L3")
            return []

        for stock in candidates:
            self.score_stock(stock)

        signals = [s for s in candidates if s.get("hg_score", 0) >= 0.4]
        signals.sort(key=lambda x: x.get("hg_score", 0), reverse=True)
        return signals[:15]

    def run_level_3_static(self) -> List[Dict]:
        """Level 3: Static snapshot from market snapshot cache."""
        print("[HG:L3] Static scoring from market snapshot...")
        snapshot_file = CACHE_DIR / "market_snapshot_latest.json"
        if not snapshot_file.exists():
            print("[HG:L3] No snapshot cache available")
            return []

        with open(snapshot_file, "r") as f:
            data = json.load(f)

        stocks = data.get("stocks", [])
        for stock in stocks:
            self.score_stock(stock)

        signals = [s for s in stocks if s.get("hg_score", 0) >= 0.35]
        signals.sort(key=lambda x: x.get("hg_score", 0), reverse=True)
        return signals[:10]

    def run_level_4_empty(self) -> List[Dict]:
        """Level 4: Empty marker — system down gracefully."""
        print("[HG:L4] EMPTY MARKER — all data sources unavailable")
        return [{
            "code": "000000",
            "name": "SYSTEM_DOWN",
            "hg_score": -1,
            "hg_signals": ["ALL_SOURCES_UNAVAILABLE"],
            "message": "No data available at this time. System will retry next cycle.",
        }]

    def execute(self) -> Dict:
        """Execute holy grail pipeline with 4-level degradation."""
        t0 = time.time()
        print("=" * 60)
        print(f"  V13.4 CLOUD HOLY GRAIL — {self.now}")
        print("=" * 60)

        signals = []
        level = 4

        # Level 1: Full akshare
        signals = self.run_level_1_full()
        level = 1 if signals else 4

        # Level 2: Cached
        if not signals:
            signals = self.run_level_2_cached()
            level = 2 if signals else 4

        # Level 3: Static
        if not signals:
            signals = self.run_level_3_static()
            level = 3 if signals else 4

        # Level 4: Empty
        if not signals:
            signals = self.run_level_4_empty()

        # Save
        self._save_signals(signals, level)

        elapsed = time.time() - t0
        print(f"\n[HG] Done in {elapsed:.1f}s | Level={level} | Signals={len(signals)}")

        # Print summary
        print("\n--- HOLY GRAIL SIGNALS ---")
        for i, s in enumerate(signals[:10]):
            code = s.get("code", "?")
            name = s.get("name", "?")
            score = s.get("hg_score", 0)
            pct = s.get("pct_chg", "?")
            sigs = ", ".join(s.get("hg_signals", [])[:4])
            print(f"  #{i+1} {code} {name} | Score:{score:.3f} | {pct}% | {sigs}")

        return {
            "level": level,
            "signals": len(signals),
            "elapsed": elapsed,
            "file": str(SIGNALS_FILE),
        }


# === CLI ===
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="V13.4 Cloud Holy Grail Engine")
    ap.add_argument("--level", type=int, choices=[1, 2, 3, 4], default=0, help="Force degradation level (0=auto)")
    args = ap.parse_args()

    engine = HolyGrailEngine()

    if args.level == 1:
        result = engine.run_level_1_full()
        engine._save_signals(result, 1)
    elif args.level == 2:
        result = engine.run_level_2_cached()
        engine._save_signals(result, 2)
    elif args.level == 3:
        result = engine.run_level_3_static()
        engine._save_signals(result, 3)
    elif args.level == 4:
        result = engine.run_level_4_empty()
        engine._save_signals(result, 4)
    else:
        result = engine.execute()

    print(f"\n[HG] Complete: Level={result.get('level')} Signals={result.get('signals')}")
