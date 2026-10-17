"""
V13.4 Cloud Holy Grail Engine
Runs in GitHub Actions — 14:30 CST T5 execution.
Loads candidate pool → applies 8-factor scoring → outputs ranked signals.

Design principles:
1. Zero MCP/TDX dependency — pure Python + akshare
2. File-driven state — reads cloud_outputs/candidate_pool_latest.json → writes holy_grail_signals.json
3. 4-level degradation: full_akshare → cached_akshare → static_factors → empty_marker

⚠️ HONESTY NOTICE (V13.4.2 调优补丁):
本引擎是 手工调参的 8 因子启发式打分器，并非贝叶斯概率引擎，也未经任何历史回测 /
OOS 验证。因子权重与分段阈值为经验设定。2026-10 在 limitup_base 子项目中以 OOS
Top-K/PF 验证证明：涨停邻近 / 打板类形态（即 f7 原先奖励的"接近涨停"区间）具有显著
负向 edge（次日均值 −1.24%，t≈−22）。故本补丁将 f7 由"奖励接近涨停"改为"惩罚追板"，
并下调动量因子 f1 权重，降低已知亏损行为被推高的概率。输出仅供研究假设，不构成交易建议。

V13.4.3 Core 诚实重建：引擎在运行时加载 core_factor_model.json（由 core_factor_rebuild.py
经 oospf_validator 门控产出）。仅当存在 OOS PROMOTE 因子且其 _score_ 打分器可用时，采用
数据驱动权重（core_status=VALIDATED_EDGE）；否则回退到上述手写基线并显式声明
core_status=NO_VALIDATED_EDGE，绝不假装这是交易信号。
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
# Core 因子诚实重建产物：仅含 OOS 验证 PROMOTE 因子、权重数据驱动；
# 当 n_factors==0 (NO_VALIDATED_EDGE) 时引擎回退到 UNVALIDATED 手写基线。
CORE_MODEL_FILE = CLOUD_ROOT / "core_factor_model.json"

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
    # V13.4.2 调优补丁：下调 f1_momentum 主导权重（原 0.20，单一因子权重过高且捕捉"追涨"
    # 风险），并下调 f7_limit_proximity（原 0.12，已改为惩罚项，权重不应再偏高）。
    # 重新分配后 f1 不再主导，量能/换手/板块权重上升。权重之和 = 1.0。
    FACTOR_WEIGHTS = {
        "f1_momentum":      0.15,   # Price momentum (14:00-14:30)
        "f2_volume_surge":  0.16,   # Volume surge vs avg
        "f3_turnover":      0.15,   # Turnover rate quality
        "f4_sector_strength": 0.14, # Sector/industry ranking
        "f5_market_cap":    0.10,   # Float market cap sweet spot
        "f6_amplitude":     0.12,   # Intraday amplitude
        "f7_limit_proximity": 0.08, # Penalty for chasing limit-up (anti-edge, V13.4.2)
        "f8_volume_ratio":  0.10,   # Volume vs 5-day average
    }

    # 诚实声明：引擎未经验证，仅作研究假设筛选用
    ENGINE_HONESTY_NOTE = (
        "heuristic 8-factor, NOT backtested, NOT Bayesian; "
        "outputs are research hypotheses, not trading signals"
    )

    # ------------------------------------------------------------------ #
    # Core 因子诚实重建集成（Task #94）
    # ------------------------------------------------------------------ #
    def _init_core_weights(self) -> Tuple[Dict, str, str]:
        """在运行时加载 core_factor_model.json，决定实际使用的因子权重。

        返回 (active_weights, core_status, core_note)：
          - core_status="VALIDATED_EDGE"：core_factor_model 含 PROMOTE 因子，
            且每个 PROMOTE 因子都能映射到引擎内 _score_<id> 方法 → 用其数据驱动权重。
          - core_status="NO_VALIDATED_EDGE"：模型无 PROMOTE 因子（当前状态）→
            回退到手写 FACTOR_WEIGHTS，作为显式 UNVALIDATED 研究基线。
          - core_status="CORE_MODEL_MISSING"：core_factor_model.json 不存在 →
            同样回退到 UNVALIDATED 基线，提示先跑 core_factor_rebuild。
          - 若 PROMOTE 因子存在但没有任何一个能映射 _score_ 方法 → 仍回退基线，
            并在 note 中标注"因子已验证但尚无打分器实现"，绝不假装可用。
        """
        if not CORE_MODEL_FILE.exists():
            return (
                dict(self.FACTOR_WEIGHTS),
                "CORE_MODEL_MISSING",
                "core_factor_model.json 缺失 → 使用 UNVALIDATED 手写基线（先跑 core_factor_rebuild）",
            )
        try:
            model = json.loads(CORE_MODEL_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            return (
                dict(self.FACTOR_WEIGHTS),
                "CORE_MODEL_ERROR",
                f"core_factor_model.json 解析失败({e}) → 使用 UNVALIDATED 手写基线",
            )

        factors = model.get("factors", [])
        n = model.get("n_factors", len(factors))
        if n <= 0:
            return (
                dict(self.FACTOR_WEIGHTS),
                "NO_VALIDATED_EDGE",
                "NO_VALIDATED_EDGE → 当前无任何因子经 OOS 门控 PROMOTE，使用 UNVALIDATED 手写基线，输出非交易信号",
            )

        # 仅保留能映射到 _score_<id> 的 PROMOTE 因子
        mapped = {}
        unmapped = []
        for f in factors:
            fid = f.get("id")
            w = f.get("weight", 0.0)
            if fid and hasattr(self, f"_score_{fid}") and w > 0:
                mapped[fid] = w
            else:
                unmapped.append(fid)

        if not mapped:
            return (
                dict(self.FACTOR_WEIGHTS),
                "NO_VALIDATED_EDGE",
                "PROMOTE 因子无可用打分器(_score_ 方法缺失) → 仍使用 UNVALIDATED 手写基线（待补打分器）",
            )

        # 归一化 mapped 权重
        tot = sum(mapped.values()) or 1.0
        active = {k: round(v / tot, 4) for k, v in mapped.items()}
        return (
            active,
            "VALIDATED_EDGE",
            f"VALIDATED_EDGE → 使用 OOS PROMOTE 因子数据驱动权重（{len(active)} 项）"
            + (f"; 未映射 {unmapped}" if unmapped else ""),
        )

    # Market cap sweet spot: small/mid cap A-shares (in RMB)
    CAP_SWEET_MIN = 5e8     # 5亿 (exclude micro-cap junk)
    CAP_SWEET_MAX = 2e10    # 200亿
    CAP_OK_MAX = 1e11       # 1000亿 (large cap OK but lower score)

    def __init__(self):
        self.state = self._load_state()
        self.today = datetime.now().strftime("%Y%m%d")
        self.now = datetime.now()
        # Core 因子诚实重建：load core_factor_model.json（若 PROMOTE 全可映射到 _score_ 方法则用
        # 其数据驱动权重；否则回退到手写 FACTOR_WEIGHTS 作为 UNVALIDATED 研究基线）。
        self.active_weights, self.core_status, self.core_note = self._init_core_weights()

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
        """Price momentum — V13.4.2 RE-ANCHORED.

        与 f7 一致的"反追板"立场：8%~9.5% 的接近涨停区间由原先奖励 0.9 改为惩罚 0.2，
        ≥9.5% 由 0.3 降为 0.1（已涨停无法买入且属追板）。保留 1%~8% 的"未涨停但强势"
        安全区作为主评分带。
        """
        pct = stock.get("pct_chg") or 0
        if 5 <= pct <= 8:
            return 0.9, "STRONG_MOMENTUM"
        elif 3 <= pct < 5:
            return 0.7, "GOOD_MOMENTUM"
        elif 1 <= pct < 3:
            return 0.5, "WEAK_MOMENTUM"
        elif 8 < pct < 9.5:  # Approaching limit-up — anti-edge chase band
            return 0.2, "NEAR_LIMIT_CHASE"
        elif pct >= 9.5:
            return 0.1, "ALREADY_LIMIT"  # Already limit-up, can't buy
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
        """Distance to limit-up — V13.4.2 RE-ANCHORED.

        OOS 验证 (limitup_base, 2026-10) 证明"涨停邻近 / 打板"区间具有显著负向 edge
        （次日均值 −1.24%, t≈−22）。因此本因子由"奖励接近涨停"改为"惩罚追板"：
        - 已涨停（无法买入）→ 0.0
        - 7%~9.8%（追板区，已知亏损行为）→ 0.1
        - 1%~7%（未涨停但强势，符合本系统"T日未涨停→T+1高开"假设的安全区）→ 0.7
        - 0%~1%（横盘）→ 0.5
        - <0（下跌）→ 0.3
        """
        pct = stock.get("pct_chg") or 0
        if pct >= 9.8:
            return 0.0, "AT_LIMIT_UNBUYABLE"
        elif 7 <= pct < 9.8:
            return 0.1, "CHASE_LIMIT_NEG_EDGE"
        elif 1 <= pct < 7:
            return 0.7, "MODERATE_UPTREND"
        elif 0 <= pct < 1:
            return 0.5, "FLAT_NEUTRAL"
        else:
            return 0.3, "DOWN_NEUTRAL"

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
        """Apply active factors and compute weighted score."""
        scores = {}
        details = {}
        total = 0.0

        for factor, weight in self.active_weights.items():
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
        """Level 1: Full data pipeline + candidate pool scoring."""
        print("[HG:L1] Running full data pipeline...")

        # Always try data fetcher — it has multi-source fallback (Eastmoney > akshare > cache)
        refresh_success = False
        try:
            import cloud_data_fetcher as cdf
            data = cdf.fetch_full_pipeline_data()
            candidates = cdf.export_candidate_pool(data, min_score=4, top_n=100)
            refresh_success = True
            print(f"[HG:L1] Fresh data: {len(candidates)} candidates (source={data.get('snapshot',{}).get('source','?')})")
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
        print(f"  [CORE] status={self.core_status} | active_factors={len(self.active_weights)} | {self.core_note}")

        # === 诚实重建铁律（Task #94 / 用户指令）===
        # 无经 OOS 验证的可部署因子时，绝不把 UNVALIDATED 手写基线挑出的标的伪装成"买入信号"推送。
        # 直接返回诚实空结果：cloud_pipeline 判为 OK_NO_SIGNAL(空仓，非失败)；
        # cloud_notify 推送"系统在线 + 无验证edge + 空仓提示"，不误导交易。
        if self.core_status in ("NO_VALIDATED_EDGE", "CORE_MODEL_MISSING", "CORE_MODEL_PARSE_FAIL"):
            self._save_signals([], 1)
            elapsed = time.time() - t0
            print(f"\n[HG] ⚠️ HONEST_EMPTY (core_status={self.core_status}) | 不输出任何买入信号")
            return {
                "level": 1,
                "signals": [],
                "signal_count": 0,
                "valid": True,
                "edge_status": self.core_status,
                "honest_empty": True,
                "edge_note": self.ENGINE_HONESTY_NOTE,
                "core_status": self.core_status,
                "core_note": self.core_note,
                "active_factors": list(self.active_weights.keys()),
                "elapsed": elapsed,
                "file": str(SIGNALS_FILE),
                "message": "系统在线，但当前无任何因子经样本外(OOS)验证 PROMOTE；"
                           "按诚实重建铁律不输出任何买入信号，本推送仅作在线/空仓提示。",
            }

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

        # 有效性判定：level==4 表示所有数据源不可用 / 空标记 → 结果无效
        valid = (level != 4)
        if not valid:
            print("[HG] ⚠️ WARNING: result is INVALID (all sources down, empty marker). "
                  "Do NOT treat as a real signal set.")

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
            "signals": signals,            # 列表（契约：下游 local_sync/AutoPilot/guardian 均按 list 消费）
            "signal_count": len(signals),  # 数量单独字段，避免返回 int 触发 len(int) 崩溃
            "elapsed": elapsed,
            "file": str(SIGNALS_FILE),
            "valid": valid,                # V13.4.2: 结果有效性标记，供 T5/GUARDIAN 联动
            "edge_note": self.ENGINE_HONESTY_NOTE,
            "core_status": self.core_status,        # Task #94: 诚实重建状态
            "core_note": self.core_note,
            "active_factors": list(self.active_weights.keys()),
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

    print(f"\n[HG] Complete: Level={result.get('level')} Signals={result.get('signal_count', result.get('signals'))}")
