"""
V13.4 Cloud Data Fetcher — Multi-Source Edition
================================================
Pure Python, zero MCP/TDX dependency.
Designed for GitHub Actions (US-based IPs) and VPS (China-based IPs).

Data source priority:
  1. Eastmoney HTTP API (push2.eastmoney.com) — works from ANY IP
  2. akshare (if installed, works from China IPs)
  3. Sina API (hq.sinajs.cn) — backup
  4. Cached JSON in cloud_cache/ — last resort

Eastmoney API fields:
  f2=price, f3=pct_chg, f4=change, f5=volume, f6=amount,
  f7=amplitude, f8=turnover, f9=pe, f10=volume_ratio,
  f12=code, f14=name, f15=high, f16=low, f17=open, f18=prev_close,
  f20=total_mv, f21=float_mv, f23=pb
"""
import os, sys, json, time, urllib.request, urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

# === Path config ===
CLOUD_ROOT = Path(__file__).parent
CACHE_DIR = CLOUD_ROOT / "cloud_cache"
OUTPUT_DIR = CLOUD_ROOT / "cloud_outputs"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# === Safe imports ===
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False
    print("[WARN] pandas not installed")

try:
    import akshare as ak
    HAS_AKSHARE = True
except ImportError:
    HAS_AKSHARE = False

# === Eastmoney API Config ===
EM_BASE = "https://push2.eastmoney.com/api/qt/clist/get"
EM_INDEX_BASE = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}

# Market filters: SZ main + ChiNext + SH main + STAR
EM_MARKET_FILTER = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
EM_FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23"

# Major indices: 上证指数, 深证成指, 创业板指, 沪深300, 上证50, 科创50
EM_INDEX_CODES = "1.000001,0.399001,0.399006,0.000300,0.000016,0.000688"


# ═══════════════════════════════════════════════
# Cache helpers
# ═══════════════════════════════════════════════

def _save_cache(name: str, data: Any):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = CACHE_DIR / f"{name}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    latest = CACHE_DIR / f"{name}_latest.json"
    with open(latest, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    # Keep only last 10 cache files per name
    caches = sorted(CACHE_DIR.glob(f"{name}_*.json"), reverse=True)
    for old in caches[10:]:
        old.unlink(missing_ok=True)
    return path


def _load_cache(name: str) -> Optional[Any]:
    latest = CACHE_DIR / f"{name}_latest.json"
    if latest.exists():
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ═══════════════════════════════════════════════
# Source 1: Eastmoney Direct HTTP API (Primary)
# ═══════════════════════════════════════════════

def _em_fetch_json(url: str, timeout: int = 15) -> Optional[Dict]:
    """Fetch JSON from Eastmoney API with proper headers."""
    req = urllib.request.Request(url, headers=EM_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as e:
        print(f"[EM] HTTP error: {e}")
        return None


def _em_parse_stock(item: Dict) -> Dict:
    """Parse a single stock from Eastmoney API response."""
    def _num(v):
        if v is None or v == "-" or v == "":
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    code = str(item.get("f12", ""))
    name = str(item.get("f14", ""))

    # Determine exchange prefix
    if code.startswith("6"):
        full_code = f"sh{code}"
    elif code.startswith("0") or code.startswith("3"):
        full_code = f"sz{code}"
    elif code.startswith("8") or code.startswith("4"):
        full_code = f"bj{code}"
    else:
        full_code = code

    return {
        "code": code,
        "full_code": full_code,
        "name": name,
        "price": _num(item.get("f2")),
        "pct_chg": _num(item.get("f3")),
        "change": _num(item.get("f4")),
        "volume": _num(item.get("f5")),
        "amount": _num(item.get("f6")),
        "amplitude": _num(item.get("f7")),
        "turnover": _num(item.get("f8")),
        "pe": _num(item.get("f9")),
        "volume_ratio": _num(item.get("f10")),
        "high": _num(item.get("f15")),
        "low": _num(item.get("f16")),
        "open": _num(item.get("f17")),
        "prev_close": _num(item.get("f18")),
        "total_mv": _num(item.get("f20")),
        "float_mv": _num(item.get("f21")),
        "pb": _num(item.get("f23")),
    }


def fetch_via_eastmoney(page_size: int = 500) -> Optional[Dict]:
    """
    Fetch full A-share market snapshot via Eastmoney direct API.
    Works from ANY IP (US, China, etc.) — no geo-restriction.
    """
    print("[EM] Fetching from Eastmoney API...")

    # Paginate: fetch top stocks by turnover
    url = (
        f"{EM_BASE}?"
        f"pn=1&pz={page_size}&po=1&np=1&fltt=2&invt=2"
        f"&fs={EM_MARKET_FILTER}"
        f"&fields={EM_FIELDS}"
    )

    data = _em_fetch_json(url)
    if not data or not data.get("data"):
        print("[EM] No data returned")
        return None

    raw_stocks = data["data"].get("diff", [])
    total_count = data["data"].get("total", 0)

    if not raw_stocks:
        print("[EM] Empty stock list")
        return None

    stocks = []
    for item in raw_stocks:
        s = _em_parse_stock(item)
        # Filter out ST, 退市, N/C prefix
        name = s.get("name", "")
        if name and ("ST" in name or "退" in name):
            continue
        # Skip stocks with no price (suspended)
        if s.get("price") is None or s["price"] <= 0:
            continue
        stocks.append(s)

    # Fetch indices
    indices = []
    idx_url = (
        f"{EM_INDEX_BASE}?"
        f"fltt=2&fields=f2,f3,f4,f6,f12,f14"
        f"&secids={EM_INDEX_CODES}"
    )
    idx_data = _em_fetch_json(idx_url)
    if idx_data and idx_data.get("data"):
        for item in idx_data["data"].get("diff", []):
            indices.append({
                "code": str(item.get("f12", "")),
                "name": str(item.get("f14", "")),
                "price": item.get("f2"),
                "pct_chg": item.get("f3"),
                "change": item.get("f4"),
                "amount": item.get("f6"),
            })

    result = {
        "timestamp": datetime.now().isoformat(),
        "source": "eastmoney_api",
        "stocks": stocks,
        "indices": indices,
        "total_count": total_count,
    }

    _save_cache("market_snapshot", result)
    print(f"[EM] OK: {len(stocks)} stocks (total={total_count}), {len(indices)} indices")
    return result


# ═══════════════════════════════════════════════
# Source 2: akshare (fallback, works from China IPs)
# ═══════════════════════════════════════════════

def fetch_via_akshare() -> Optional[Dict]:
    """Fetch via akshare — works from China-based IPs."""
    if not HAS_AKSHARE:
        return None

    print("[AK] Trying akshare...")
    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or len(df) == 0:
            print("[AK] Empty result")
            return None

        col_map = {
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "pct_chg", "涨跌额": "change",
            "成交量": "volume", "成交额": "amount",
            "振幅": "amplitude", "最高": "high", "最低": "low",
            "今开": "open", "昨收": "prev_close",
            "量比": "volume_ratio", "换手率": "turnover",
            "市盈率-动态": "pe", "市净率": "pb",
            "总市值": "total_mv", "流通市值": "float_mv",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "name" in df.columns:
            df = df[~df["name"].str.contains("ST|退市|N |C ", na=False)]

        num_cols = ["price", "pct_chg", "volume", "amount", "turnover", "total_mv", "float_mv"]
        for c in num_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        if "turnover" in df.columns:
            df = df.sort_values("turnover", ascending=False)

        records = df.head(500).to_dict(orient="records")
        # Clean NaN
        for r in records:
            for k, v in list(r.items()):
                if pd.isna(v):
                    r[k] = None

        result = {
            "timestamp": datetime.now().isoformat(),
            "source": "akshare",
            "stocks": records,
            "indices": [],
            "total_count": len(df),
        }
        _save_cache("market_snapshot", result)
        print(f"[AK] OK: {len(records)} stocks")
        return result

    except Exception as e:
        print(f"[AK] Error: {e}")
        return None


# ═══════════════════════════════════════════════
# Unified fetch with multi-source fallback
# ═══════════════════════════════════════════════

def fetch_market_snapshot() -> Dict:
    """
    Fetch full A-share market snapshot with 3-level fallback:
    1. Eastmoney direct API (works from any IP)
    2. akshare (works from China IPs)
    3. Cached data
    """
    # Source 1: Eastmoney (primary — works from US IPs)
    result = fetch_via_eastmoney()
    if result and result["stocks"]:
        return result

    # Source 2: akshare
    result = fetch_via_akshare()
    if result and result["stocks"]:
        return result

    # Source 3: Cache
    cached = _load_cache("market_snapshot")
    if cached:
        print(f"[FALLBACK] Using cached snapshot from {cached.get('timestamp')}")
        cached["source"] = "cache_fallback"
        return cached

    print("[ERROR] All data sources failed!")
    return {
        "timestamp": datetime.now().isoformat(),
        "source": "all_failed",
        "stocks": [],
        "indices": [],
        "total_count": 0,
    }


# ═══════════════════════════════════════════════
# Sector / LHB / Limit-up (with Eastmoney fallback)
# ═══════════════════════════════════════════════

def fetch_sector_heat() -> Dict:
    """Fetch sector/industry heat map."""
    result = {"timestamp": datetime.now().isoformat(), "sectors": []}

    # Try Eastmoney sector API
    url = (
        f"{EM_BASE}?"
        f"pn=1&pz=100&po=1&np=1&fltt=2"
        f"&fs=m:90+t:2"  # 行业板块
        f"&fields=f2,f3,f4,f8,f12,f14"
    )
    data = _em_fetch_json(url)
    if data and data.get("data"):
        for item in data["data"].get("diff", []):
            result["sectors"].append({
                "code": str(item.get("f12", "")),
                "name": str(item.get("f14", "")),
                "pct_chg": item.get("f3"),
                "turnover": item.get("f8"),
                "amount": item.get("f6"),
            })
        if result["sectors"]:
            _save_cache("sector_heat", result)
            print(f"[EM] Sectors: {len(result['sectors'])} industries")
            return result

    # Try akshare
    if HAS_AKSHARE:
        try:
            df = ak.stock_sector_spot_indicator(symbol="申万一级")
            if df is not None and len(df) > 0:
                result["sectors"] = df.to_dict(orient="records")
                _save_cache("sector_heat", result)
                print(f"[AK] Sectors: {len(result['sectors'])} industries")
                return result
        except Exception as e:
            print(f"[WARN] Sector fetch failed: {e}")

    cached = _load_cache("sector_heat")
    return cached if cached else result


def fetch_lhb(date: Optional[str] = None) -> Dict:
    """Fetch Dragon-Tiger Board data."""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    result = {"timestamp": datetime.now().isoformat(), "date": date, "entries": []}

    if HAS_AKSHARE:
        try:
            df = ak.stock_lhb_detail_em(date=date)
            if df is not None and len(df) > 0:
                result["entries"] = df.to_dict(orient="records")
                _save_cache("lhb", result)
                print(f"[DATA] LHB: {len(result['entries'])} entries")
                return result
        except Exception as e:
            print(f"[WARN] LHB fetch: {e}")

    cached = _load_cache("lhb")
    return cached if cached else result


def fetch_limit_up_pool(date: Optional[str] = None) -> Dict:
    """Fetch limit-up pool."""
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    result = {"timestamp": datetime.now().isoformat(), "date": date, "limit_up": []}

    if HAS_AKSHARE:
        try:
            df = ak.stock_zt_pool_em(date=date)
            if df is not None and len(df) > 0:
                result["limit_up"] = df.to_dict(orient="records")
                _save_cache("limit_up", result)
                print(f"[DATA] Limit-up: {len(result['limit_up'])} stocks")
                return result
        except Exception as e:
            print(f"[WARN] Limit-up fetch: {e}")

    return result


# ═══════════════════════════════════════════════
# Stock Scoring (unchanged logic)
# ═══════════════════════════════════════════════

def compute_stock_scores(stocks: List[Dict]) -> List[Dict]:
    """Compute multi-factor cloud scores for each stock."""
    for s in stocks:
        score = 0
        signals = []

        pct = s.get("pct_chg") or 0
        turnover = s.get("turnover") or 0
        vol_ratio = s.get("volume_ratio") or 1
        float_mv = s.get("float_mv") or 0
        amplitude = s.get("amplitude") or 0

        # F1: momentum (3-9% ideal)
        if 3 <= pct <= 9:
            score += 2; signals.append("MOMENTUM_OK")
        elif 1 <= pct < 3:
            score += 1; signals.append("MOMENTUM_WEAK")

        # F2: turnover
        if turnover > 10:
            score += 2; signals.append("TURNOVER_HIGH")
        elif turnover > 3:
            score += 1; signals.append("TURNOVER_OK")

        # F3: volume expansion
        if vol_ratio and vol_ratio > 2:
            score += 2; signals.append("VOL_BOOM")
        elif vol_ratio and vol_ratio > 1.2:
            score += 1; signals.append("VOL_EXPAND")

        # F4: market cap (5-200B sweet spot)
        if float_mv and 5e8 <= float_mv <= 2e10:
            score += 2; signals.append("CAP_SWEET")
        elif float_mv and 2e10 < float_mv <= 1e11:
            score += 1; signals.append("CAP_OK")

        # F5: amplitude
        if amplitude > 5:
            score += 1; signals.append("AMPLITUDE_ACTIVE")

        s["cloud_score"] = score
        s["cloud_signals"] = signals

    stocks.sort(key=lambda x: x.get("cloud_score", 0), reverse=True)
    return stocks


# ═══════════════════════════════════════════════
# Pipeline aggregation
# ═══════════════════════════════════════════════

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

    if result["snapshot"]["stocks"]:
        result["snapshot"]["stocks"] = compute_stock_scores(result["snapshot"]["stocks"])
        result["candidates"] = [s for s in result["snapshot"]["stocks"] if s.get("cloud_score", 0) >= 4]
        print(f"[PIPELINE] Candidates (score>=4): {len(result['candidates'])}")

    _save_cache("pipeline_full", result)
    return result


def export_candidate_pool(data: Dict, min_score: int = 4, top_n: int = 100) -> List[Dict]:
    """Export ranked candidate pool."""
    candidates = data.get("candidates", [])
    if not candidates:
        candidates = data.get("snapshot", {}).get("stocks", [])

    pool = [s for s in candidates if s.get("cloud_score", 0) >= min_score]
    pool = pool[:top_n]

    output = {
        "timestamp": datetime.now().isoformat(),
        "min_score": min_score,
        "count": len(pool),
        "stocks": pool,
    }

    path = OUTPUT_DIR / f"candidate_pool_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, default=str)

    latest_path = OUTPUT_DIR / "candidate_pool_latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, default=str)

    print(f"[EXPORT] Candidate pool: {len(pool)} stocks")
    return pool


# ═══════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["snapshot", "pipeline", "lhb", "sectors", "export", "test"], default="pipeline")
    ap.add_argument("--min-score", type=int, default=4)
    ap.add_argument("--top-n", type=int, default=100)
    args = ap.parse_args()

    t0 = time.time()
    if args.mode == "snapshot":
        data = fetch_market_snapshot()
        print(f"Snapshot: {data['total_count']} stocks, source={data['source']} in {time.time()-t0:.1f}s")
    elif args.mode == "pipeline":
        data = fetch_full_pipeline_data()
        pool = export_candidate_pool(data, args.min_score, args.top_n)
        print(f"Pipeline done in {time.time()-t0:.1f}s, {len(pool)} candidates")
    elif args.mode == "test":
        print("Testing data sources...")
        em = fetch_via_eastmoney(page_size=10)
        print(f"Eastmoney: {'OK' if em and em['stocks'] else 'FAIL'} ({len(em['stocks']) if em else 0} stocks)")
    elif args.mode == "lhb":
        data = fetch_lhb()
        print(f"LHB: {len(data.get('entries', []))} entries")
    elif args.mode == "sectors":
        data = fetch_sector_heat()
        print(f"Sectors: {len(data.get('sectors', []))} industries")
    elif args.mode == "export":
        data = _load_cache("pipeline_full") or fetch_full_pipeline_data()
        pool = export_candidate_pool(data, args.min_score, args.top_n)
