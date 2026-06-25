"""
V13.4 Cloud Data Fetcher
Pure Python + akshare — zero MCP/TDX dependency.
Designed for GitHub Actions execution.

Data sources:
- akshare: A-share real-time quotes, sectors, indices, LHB
- Fallback: cached JSON in cloud_cache/ directory
"""
import os, sys, json, time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

import pandas as pd

# === Path config ===
CLOUD_ROOT = Path(__file__).parent
CACHE_DIR = CLOUD_ROOT / "cloud_cache"
OUTPUT_DIR = CLOUD_ROOT / "cloud_outputs"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# === Safe import ===
try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False
    print("[WARN] akshare not installed, using fallback mode")


def _save_cache(name: str, data: Any):
    """Save data to cache with timestamp."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CACHE_DIR / f"{name}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    # Also save as latest
    latest = CACHE_DIR / f"{name}_latest.json"
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    return path


def _load_cache(name: str) -> Optional[Any]:
    """Load latest cache if available."""
    latest = CACHE_DIR / f"{name}_latest.json"
    if latest.exists():
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def fetch_market_snapshot() -> Dict:
    """
    Fetch full A-share market snapshot: all stocks with price/volume/change%.
    Uses akshare stock_zh_a_spot_em() which is the most comprehensive free source.
    Returns: {stocks: [...], indices: [...], timestamp, total_count}
    """
    result = {
        "timestamp": datetime.now().isoformat(),
        "source": "akshare" if HAS_AKSHARE else "fallback",
        "stocks": [],
        "indices": [],
        "total_count": 0,
    }

    if HAS_AKSHARE:
        try:
            # Full A-share spot market
            df = ak.stock_zh_a_spot_em()
            if df is not None and len(df) > 0:
                # Rename columns to standard format
                col_map = {
                    "代码": "code", "名称": "name", "最新价": "price",
                    "涨跌幅": "pct_chg", "涨跌额": "change",
                    "成交量": "volume", "成交额": "amount",
                    "振幅": "amplitude", "最高": "high", "最低": "low",
                    "今开": "open", "昨收": "prev_close",
                    "量比": "volume_ratio", "换手率": "turnover",
                    "市盈率-动态": "pe", "市净率": "pb",
                    "总市值": "total_mv", "流通市值": "float_mv",
                    "60日涨跌幅": "pct_60d", "年初至今涨跌幅": "pct_ytd",
                }
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

                # Filter out ST/退市/新三板
                if "name" in df.columns:
                    df = df[~df["name"].str.contains("ST|退市|N |C ", na=False)]

                # Ensure numeric columns
                num_cols = ["price", "pct_chg", "volume", "amount", "turnover", "total_mv", "float_mv"]
                for c in num_cols:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors="coerce")

                # Sort by turnover descending for top activity
                if "turnover" in df.columns:
                    df = df.sort_values("turnover", ascending=False)

                records = df.head(500).to_dict(orient="records")  # Top 500 for efficiency
                result["stocks"] = records
                result["total_count"] = len(df)

                # Clean NaN values
                for r in result["stocks"]:
                    for k, v in list(r.items()):
                        if pd.isna(v):
                            r[k] = None

            # Market indices
            try:
                idx_df = ak.stock_zh_index_spot_em()
                if idx_df is not None:
                    idx_col_map = {
                        "代码": "code", "名称": "name", "最新价": "price",
                        "涨跌幅": "pct_chg", "涨跌额": "change",
                        "成交量": "volume", "成交额": "amount",
                    }
                    idx_df = idx_df.rename(columns={k: v for k, v in idx_col_map.items() if k in idx_df.columns})
                    result["indices"] = idx_df.head(20).to_dict(orient="records")
            except Exception:
                result["indices"] = []

            _save_cache("market_snapshot", result)
            print(f"[DATA] Market snapshot: {result['total_count']} stocks, {len(result['indices'])} indices")
            return result

        except Exception as e:
            print(f"[ERROR] akshare fetch failed: {e}")

    # Fallback: load latest cache
    cached = _load_cache("market_snapshot")
    if cached:
        print(f"[FALLBACK] Using cached snapshot from {cached.get('timestamp')}")
        cached["source"] = "cache_fallback"
        return cached

    print("[FALLBACK] No cache available, returning empty snapshot")
    return result


def fetch_lhb(date: Optional[str] = None) -> Dict:
    """Fetch 龙虎榜 (Dragon-Tiger Board) data."""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    result = {"timestamp": datetime.now().isoformat(), "date": date, "entries": []}

    if HAS_AKSHARE:
        try:
            df = ak.stock_lhb_detail_em(date=date)
            if df is not None and len(df) > 0:
                result["entries"] = df.to_dict(orient="records")
                _save_cache("lhb", result)
                print(f"[DATA] LHB: {len(result['entries'])} entries for {date}")
                return result
        except Exception as e:
            print(f"[WARN] LHB fetch failed: {e}")

    cached = _load_cache("lhb")
    return cached if cached else result


def fetch_sector_heat() -> Dict:
    """Fetch sector/industry heat map."""
    result = {"timestamp": datetime.now().isoformat(), "sectors": []}

    if HAS_AKSHARE:
        try:
            df = ak.stock_sector_spot_indicator(symbol="申万一级")
            if df is not None and len(df) > 0:
                result["sectors"] = df.to_dict(orient="records")
                _save_cache("sector_heat", result)
                print(f"[DATA] Sectors: {len(result['sectors'])} industries")
                return result
        except Exception as e:
            print(f"[WARN] Sector fetch failed: {e}")

    cached = _load_cache("sector_heat")
    return cached if cached else result


def fetch_limit_up_pool(date: Optional[str] = None) -> Dict:
    """Fetch 涨停板 pool for the day."""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")

    result = {"timestamp": datetime.now().isoformat(), "date": date, "limit_up": []}

    if HAS_AKSHARE:
        try:
            df = ak.stock_zt_pool_em(date=date)
            if df is not None and len(df) > 0:
                result["limit_up"] = df.to_dict(orient="records")
                _save_cache("limit_up", result)
                print(f"[DATA] Limit-up pool: {len(result['limit_up'])} stocks")
                return result
        except Exception as e:
            print(f"[WARN] Limit-up fetch failed: {e}")

    return result


def compute_stock_scores(stocks: List[Dict]) -> List[Dict]:
    """
    Compute multi-factor scores for each stock.
    Factors (simplified for cloud):
    1. 涨跌幅动量 (pct_chg) — positive momentum
    2. 换手率 (turnover) — active trading
    3. 量比 (volume_ratio > 1.5) — volume expansion
    4. 流通市值适中 (float_mv 10-200亿 preferred)
    5. 涨幅>3%且<9% (not yet limit-up)
    6. 振幅足够 (amplitude > 3%) — active price discovery
    """

    for s in stocks:
        score = 0
        signals = []

        pct = s.get("pct_chg") or 0
        turnover = s.get("turnover") or 0
        vol_ratio = s.get("volume_ratio") or 1
        float_mv = s.get("float_mv") or 0
        amplitude = s.get("amplitude") or 0

        # F1: momentum (3-9% ideal, not yet limit-up)
        if 3 <= pct <= 9:
            score += 2
            signals.append("MOMENTUM_OK")
        elif 1 <= pct < 3:
            score += 1
            signals.append("MOMENTUM_WEAK")

        # F2: turnover rate (>3% active)
        if turnover > 10:
            score += 2
            signals.append("TURNOVER_HIGH")
        elif turnover > 3:
            score += 1
            signals.append("TURNOVER_OK")

        # F3: volume expansion
        if vol_ratio and vol_ratio > 2:
            score += 2
            signals.append("VOL_BOOM")
        elif vol_ratio and vol_ratio > 1.2:
            score += 1
            signals.append("VOL_EXPAND")

        # F4: market cap (10-200B RMB sweet spot)
        if float_mv and 1e9 <= float_mv <= 2e10:
            score += 2
            signals.append("CAP_SWEET")
        elif float_mv and 2e10 < float_mv <= 1e11:
            score += 1
            signals.append("CAP_OK")

        # F5: amplitude (price discovery active)
        if amplitude > 5:
            score += 1
            signals.append("AMPLITUDE_ACTIVE")

        s["cloud_score"] = score
        s["cloud_signals"] = signals

    # Sort by score descending
    stocks.sort(key=lambda x: x.get("cloud_score", 0), reverse=True)
    return stocks


def fetch_full_pipeline_data() -> Dict:
    """Aggregate all data sources for pipeline steps."""
    print("=" * 50)
    print(f"[PIPELINE] Fetching full market data @ {datetime.now()}")
    print("=" * 50)

    result = {
        "timestamp": datetime.now().isoformat(),
        "snapshot": fetch_market_snapshot(),
        "lhb": fetch_lhb(),
        "sectors": fetch_sector_heat(),
    }

    # Compute scores on snapshot stocks
    if result["snapshot"]["stocks"]:
        result["snapshot"]["stocks"] = compute_stock_scores(result["snapshot"]["stocks"])
        result["candidates"] = [s for s in result["snapshot"]["stocks"] if s.get("cloud_score", 0) >= 5]
        print(f"[PIPELINE] Candidates (score>=5): {len(result['candidates'])}")

    _save_cache("pipeline_full", result)
    return result


def export_candidate_pool(data: Dict, min_score: int = 5, top_n: int = 80) -> List[Dict]:
    """Export ranked candidate pool for next pipeline step."""
    candidates = data.get("candidates", [])
    if not candidates:
        candidates = data.get("snapshot", {}).get("stocks", [])

    # Filter by score
    pool = [s for s in candidates if s.get("cloud_score", 0) >= min_score]
    pool = pool[:top_n]

    path = OUTPUT_DIR / f"candidate_pool_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "min_score": min_score, "count": len(pool), "stocks": pool}, f, ensure_ascii=False)

    # Write latest for pipeline consumption
    latest_path = OUTPUT_DIR / "candidate_pool_latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": datetime.now().isoformat(), "min_score": min_score, "count": len(pool), "stocks": pool}, f, ensure_ascii=False)

    print(f"[EXPORT] Candidate pool: {len(pool)} stocks -> {path}")
    return pool


# === CLI ===
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["snapshot", "pipeline", "lhb", "sectors", "export"], default="pipeline")
    ap.add_argument("--min-score", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=80)
    args = ap.parse_args()

    t0 = time.time()
    if args.mode == "snapshot":
        data = fetch_market_snapshot()
        print(f"Snapshot: {data['total_count']} stocks in {time.time()-t0:.1f}s")
    elif args.mode == "pipeline":
        data = fetch_full_pipeline_data()
        pool = export_candidate_pool(data, args.min_score, args.top_n)
        print(f"Pipeline done in {time.time()-t0:.1f}s")
    elif args.mode == "lhb":
        data = fetch_lhb()
        print(f"LHB: {len(data.get('entries',[]))} entries")
    elif args.mode == "sectors":
        data = fetch_sector_heat()
        print(f"Sectors: {len(data.get('sectors',[]))} industries")
    elif args.mode == "export":
        data = _load_cache("pipeline_full") or fetch_full_pipeline_data()
        pool = export_candidate_pool(data, args.min_score, args.top_n)
