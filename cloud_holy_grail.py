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

# SUPPLEMENTARY grail-hit 验证边产物（与 core_factor_model 严格分离）：
# 仅记录经圣杯全口径闸门 (adjudicate_grail) PROMOTE_GRAIL 的 CAT_PROXIMITY 增强，
# 作为"T日可买 + 邻近有效催化"的 grail_hit 重排序/候选层，绝不进入 buy_score / core_factor_model。
SUPPLEMENTARY_MODEL_FILE = CLOUD_ROOT / "grail_hit_supplementary_model.json"

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

    def _init_supplementary(self) -> Tuple[str, Optional[Dict]]:
        """加载 grail_hit SUPPLEMENTARY 验证边（CAT_PROXIMITY, adjudicate_grail PROMOTE_GRAIL）。

        与 core_factor_model 严格分离：本文件只记录"经圣杯全口径闸门验证的 SUPPLEMENTARY
        重排序/候选层"，绝不进入 buy_score / core_factor_model（n_factors 仍=0）。
        仅当 verdict=="PROMOTE_GRAIL" 且 feature=="CAT_PROXIMITY"（与 oos_eval_harness.adjudicate_grail
        同门、闸门一字未改）时，引擎才产出 SUPPLEMENTARY grail-hit 候选；否则返回 ("NONE", ...) 走既有诚实空仓逻辑。
        """
        if not SUPPLEMENTARY_MODEL_FILE.exists():
            return ("NONE", None)
        try:
            model = json.loads(SUPPLEMENTARY_MODEL_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            return ("MODEL_ERROR", {"error": str(e)})
        if model.get("verdict") != "PROMOTE_GRAIL" or model.get("feature") != "CAT_PROXIMITY":
            return ("NONE", model)  # 未通过圣杯全口径闸门 -> 不产出 SUPPLEMENTARY 信号
        return ("VALIDATED_SUPPLEMENTARY_GRAIL", model)

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
        # SUPPLEMENTARY grail-hit 验证边（与 core 严格分离）
        self.supplementary_status, self.supplementary_model = self._init_supplementary()
        # 龙头/追高 讲座缺口修复(SUPPLEMENTARY 候选质量改进, 非 buy_score/core):
        # 剔除弱势名(close<MA20 或 20日收益<0)。OOS 验证(grail_leader_strength): 弱势组
        # grail_full 2.00% vs 强势 7.34%(lift 3.67×, z~2); 剔除后 resume PCV 0.53→0.77(过稳门)。
        # 默认开; 闸门/GOALS/n_factors 全未动。
        self.grail_strong_only = True

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

        # 镜像到本地推送器读取路径 (data/pipeline/holy_grail_signals.json)，供 V13_4_HolyGrailPusher 消费
        try:
            pipeline_dir = CLOUD_ROOT / "data" / "pipeline"
            pipeline_dir.mkdir(parents=True, exist_ok=True)
            with open(pipeline_dir / "holy_grail_signals.json", "w") as f:
                json.dump(output, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[HG] ⚠️ pipeline 镜像失败(非致命): {e}")

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

    def _score_monitor_oversold_rebound(self, stock: Dict) -> Tuple[float, str]:
        """深跌放量反弹型(OOS PROMOTE, 2026-10-28 注入, oos_factor_id=oversold_sig)。

        事件型二元特征：stock 携带 oversold_signal=True 表示 T日深跌放量(收盘跌幅<=-4%
        且 量比>=1.5)，由 discovery 层 p1_discovery.oversold 监测列表盖章传入候选池。
        OOS 证明该事件 T+5 期望收益为正 edge(PF_t5=1.3826>随机真及格线1.259)、命中倍数>=1.15、
        PF_vs_bottom>=1.30、t>=2.0、DSR>=0.95、WhiteRC p<0.10、二元方向正。
        若候选未携带该特征(上游未盖章) → 0.0（不在本因子打分，避免误用其它字段）。"""
        if stock.get("oversold_signal"):
            return 1.0, "OVERSOLD_REBOUND"
        return 0.0, "NO_OVERSOLD_SIG"

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

    def _mf_hold_at(self, code, t_date, tau=0.30):
        """MF_HOLD_τ 主力持仓量代理 @ T_date (DISPLAY 解释层, 见 research/chip_position.py)。

        用户 lecture 测算手法: "获利 τ% 仍不肯卖的筹码≈主力仓位"。本函数取 T_date 当日
        MF_HOLD_τ 值(成本≤close/(1+τ) 的滚动成本占比)，并映射控盘等级。
        失败一律返回 (None, None)，绝不阻断信号产出。
        """
        try:
            from research import chip_position as CP
            d = CP.load_daily(str(code).zfill(6))
            if d is None:
                return None, None
            series = CP.mf_hold_series(d["close"], d["low"], d["high"], d["vol"],
                                       window=250, tau=tau)
            tdate = int(t_date)
            idx = None
            for i, dt in enumerate(d["date"]):
                if int(str(dt).replace("-", "").replace("/", "")) == tdate:
                    idx = i
                    break
            if idx is None or idx >= len(series) or series[idx] is None:
                return None, None
            val = float(series[idx])
            if val < 0.30:
                ctrl = "弱控盘"
            elif val < 0.50:
                ctrl = "中控盘"
            elif val < 0.70:
                ctrl = "高控盘"
            else:
                ctrl = "极高控盘"
            return val, ctrl
        except Exception:
            return None, None

    def _trend_strength_at(self, code, t_date):
        """趋势强度 @ T_date（DISPLAY 解释层，呼应"最大的风险是做弱势/只做龙头"讲座）。

        返回 dict: {ret20, ma_ratio, is_weak, label} 或 None。
        label: 强势平台(close≥MA20 且 20日收益≥0) / 弱势(close<MA20 或 20日收益<0) / 中性。
        失败静默返回 None，绝不阻断信号产出。
        """
        try:
            import numpy as np
            from research import chip_position as CP
            d = CP.load_daily(str(code).zfill(6))
            if d is None:
                return None
            dates = d["date"]; close = d["close"]
            idx = None
            for i, dt in enumerate(dates):
                if int(str(dt).replace("-", "").replace("/", "")) == int(t_date):
                    idx = i
                    break
            if idx is None or idx < 20:
                return None
            ma20 = float(np.mean(close[idx - 19: idx + 1]))
            price = float(close[idx]); ret20 = price / close[idx - 20] - 1.0
            ma_ratio = price / ma20 - 1.0
            is_weak = (price < ma20) or (ret20 < 0)
            if (price > ma20) and (ret20 > 0):
                label = "强势平台"
            elif is_weak:
                label = "弱势"
            else:
                label = "中性"
            return {"ret20": round(float(ret20), 4), "ma_ratio": round(float(ma_ratio), 4),
                    "is_weak": bool(is_weak), "label": label}
        except Exception:
            return None

    def run_grail_hit_supplementary(self) -> Dict:
        """SUPPLEMENTARY grail-hit 候选生成（CAT_PROXIMITY, 经 adjudicate_grail 验证）。

        调用 research.grail_hit_selector.select_candidates()（消费 backtest_cache/daily +
        catalyst_notices.jsonl）。产出"T日可买(非涨停/非跌停) + 邻近有效品类催化"的 grail_hit
        候选（即核心目标买点 + 验证增益），标注 signal_type=GRAIL_HIT_SUPPLEMENTARY。
        数据/依赖缺失时 available=False -> 调用方回退到既有诚实空仓逻辑（绝不伪造）。
        """
        try:
            from research import grail_hit_selector as GHS
            # 2026-11-08 阶段四：live 显式开启近期落地披露窗口(resume 子集, ±RECENT_DAYS日),
            # 使"公开数据可达的滞后 drift"候选真正流入实盘(此前默认 recent_days=0 → 前沿恒 0 候选)。
            # resume 子集 lift 未验证(expected_lift=None)，仍严格 SUPPLEMENTARY 解释层，不进 core/闸门。
            res = GHS.select_candidates(K=10, recent_days=GHS.RECENT_DAYS)
        except Exception as e:
            print(f"[HG:SUP] grail_hit_selector 不可用(缺数据/依赖): {e}")
            return {"signals": [], "available": False, "reason": f"unavailable: {e}"}
        cands = res.get("top", [])
        conf_map = (self.supplementary_model or {}).get("validated_confidence_map", {})
        sigs = []
        for c in cands:
            cat = c.get("category")
            conf = conf_map.get(cat, 0.70)
            s = {
                "code": c.get("symbol"),
                "name": c.get("symbol"),
                "hg_score": round(conf, 4),
                "v132_score": conf,
                "tier": "G1",
                "pct_chg": round((c.get("ret_T") or 0) * 100, 2),
                "change_pct": round((c.get("ret_T") or 0) * 100, 2),
                "signal_type": "GRAIL_HIT_SUPPLEMENTARY",
                "supplementary": True,
                "verdict_ref": "PROMOTE_GRAIL",
                "category": cat,
                "expected_lift": c.get("expected_lift"),
                "cat_timing": c.get("cat_timing"),
                "days_to_D": c.get("days_to_D"),
                "boost_tier": c.get("boost_tier"),
                "near_high": c.get("near_high"),
                "T_date": c.get("T_date"),
            }
            # === MF_HOLD 主力持仓量代理 · 推送卡"持仓量/分布风险"展示字段(DISPLAY 解释层) ===
            # 用户 lecture: 获利τ%不抛筹码≈主力仓位。OOS 已判 DISPLAY(对圣杯2日续涨逆向,
            # 仅分布风险视角), 绝不进 buy_score / core_factor_model。失败静默跳过, 不阻断信号。
            mf_val, mf_ctrl = self._mf_hold_at(c.get("symbol"), c.get("T_date"))
            if mf_val is not None:
                s["mf_hold_30"] = round(mf_val, 4)
                s["mf_hold_ctrl"] = mf_ctrl
                s["mf_hold_interp"] = (
                    f"分布风险视角[解释层·非买点]：获利≥30%仍持仓筹码占比{mf_val*100:.0f}%，"
                    f"主力{mf_ctrl}；OOS 显示该指标与圣杯T+1→T+3续涨呈逆向，仅作持仓结构/派发风险参考"
                )
            # === 趋势强度/非弱势 展示字段(DISPLAY 解释层, 呼应"最大的风险是做弱势/只做龙头") ===
            # OOS 验证: resume 子集 弱势名(close<MA20 或 20日收益<0) 显著拖累边缘
            # (grail_full 2.00% vs 强势 7.34%, lift 3.67×, z~2); 剔除弱势后 PCV 0.53→0.77(过稳门)。
            ts = self._trend_strength_at(c.get("symbol"), c.get("T_date"))
            if ts is not None:
                s["trend_strength"] = ts
                s["trend_label"] = ts["label"]
                s["is_weak"] = ts["is_weak"]
            # === 龙头/追高 讲座缺口修复: 剔除弱势(最大的风险是做弱势) ===
            # SUPPLEMENTARY 候选质量改进, 非 buy_score/core; 闸门/GOALS/n_factors 全未动。
            # 仅影响 SUPPLEMENTARY 候选的入选与前向台账累积口径, 使 live 累积测量的是干净边缘。
            if getattr(self, "grail_strong_only", True) and ts is not None and ts.get("is_weak"):
                continue
            sigs.append(s)
        # === 前向 OOS 累积闭环(运行时自提升链路) ===
        # 本函数为"真实 live 发射点": 把每个实推候选 append 到前向台账(force=True=权威发射),
        # 待 T+3 收盘后由 nightly/次日核验步骤 label_all 标注, n>=200 后 evaluate 复核晋升。
        # 回放/validate 路径不调用本函数, 故不会污染真实台账。失败绝不影响信号产出。
        if sigs:
            try:
                from research import grail_hit_forward_oos as FWD
                for s in sigs:
                    FWD.emit(s["code"], s["T_date"], s["category"],
                             s.get("expected_lift") or 0.0,
                             verdict_ref="PROMOTE_GRAIL", force=True)
            except Exception as e:
                print(f"[HG:SUP] 前向台账 emit 失败(不影响信号): {e}")
        return {
            "signals": sigs,
            "available": True,
            "n_scanned": res.get("n_total_scanned"),
            "n_candidates": res.get("n_candidates"),
            "reason": "OK" if sigs else "no candidate today (sparse signal)",
        }

    # ── 市场宽度闸门(八条心法·规则1/5: 主升浪重仓 / 退潮立刻空仓) ───────────────
    # 执行级保护叠加层(非模型特征, 不进 core_factor_model / n_factors); 仅当宽度证据
    # *明确*显示退潮(涨停家数低于主升波阈值)时强制诚实空仓、抑制发射。
    # 设计铁律: (a) 数据源不可达/空 -> fail-open(不抑制, 避免数据缺失误杀有效信号);
    #           (b) 绝不降低任何评估门槛(lift/t_z/PCV/n 一字未改) — 只降误发射、提准确率。
    # 依据: grail_hit 本质是"T+1 涨停事件"长偏多策略, 该事件强 regime 依赖; 普跌/退潮日
    #       涨停概率坍塌, 盲目发射会系统性亏损。锚定老乐心法"每天涨停超过50家"为主升波阈值。
    _REGIME_LIMIT_UP_MIN = 50

    def _market_regime_ok(self):
        """返回 (ok, reason)。ok=False => 退潮, 应强制空仓(抑制发射)。

        信号源: cloud_data_fetcher.fetch_limit_up_pool(today) 涨停家数(专用涨停池, 非 top500 样本)。
        退潮判定: 涨停家数 < _REGIME_LIMIT_UP_MIN。源缺失/空 -> (True, 'no_data_fail_open')。
        """
        try:
            from cloud_data_fetcher import fetch_limit_up_pool
            import datetime as _dt
            today = _dt.date.today().strftime("%Y%m%d")
            pool = fetch_limit_up_pool(date=today) or {}
            ups = pool.get("limit_up") or []
            if not ups:
                return True, "no_data_fail_open"  # 源缺失 -> 不抑制(诚实交给既有逻辑)
            n_up = len(ups)
            if n_up < self._REGIME_LIMIT_UP_MIN:
                return False, f"limit_up={n_up} < {self._REGIME_LIMIT_UP_MIN}(退潮/无主升波)"
            return True, f"limit_up={n_up} >= {self._REGIME_LIMIT_UP_MIN}(主升波健康)"
        except Exception as e:
            return True, f"exception_fail_open:{e}"

    def execute(self) -> Dict:
        """Execute holy grail pipeline with 4-level degradation."""
        t0 = time.time()
        print("=" * 60)
        print(f"  V13.4 CLOUD HOLY GRAIL — {self.now}")
        print("=" * 60)
        print(f"  [CORE] status={self.core_status} | active_factors={len(self.active_weights)} | {self.core_note}")
        print(f"  [SUP ] status={self.supplementary_status} | "
              f"{('CAT_PROXIMITY PROMOTE_GRAIL' if self.supplementary_status=='VALIDATED_SUPPLEMENTARY_GRAIL' else '无验证 SUPPLEMENTARY 边')}")

        # === SUPPLEMENTARY grail-hit 验证边（CAT_PROXIMITY, adjudicate_grail PROMOTE_GRAIL）===
        # 与 core buy_score 严格分离：仅当存在经圣杯全口径闸门验证的 SUPPLEMENTARY 边时，
        # 产出"T日可买 + 邻近有效催化"的 grail_hit 候选（核心目标买点 + 验证增益），绝不伪造。
        if self.supplementary_status == "VALIDATED_SUPPLEMENTARY_GRAIL":
            # === 市场宽度闸门(规则1/5: 退潮立刻空仓) — 发射前强制校验 ===
            regime_ok, regime_reason = self._market_regime_ok()
            if not regime_ok:
                self._save_signals([], 1)
                elapsed = time.time() - t0
                print(f"\n[HG] ⚠️ REGIME_RETREAT -> 诚实空仓(抑制发射): {regime_reason}")
                return {
                    "level": 1, "signals": [], "signal_count": 0,
                    "valid": True, "edge_status": self.supplementary_status,
                    "supplementary": True, "honest_empty": True, "regime_empty": True,
                    "edge_note": f"市场退潮闸门触发(规则1/5): {regime_reason} -> 强制空仓, "
                                 f"不发射 grail_hit 候选(绝不降低评估门槛 lift/t_z/PCV/n)",
                    "core_status": self.supplementary_status,
                    "core_note": regime_reason,
                    "active_factors": list(self.active_weights.keys()),
                    "elapsed": elapsed, "file": str(SIGNALS_FILE),
                }
            sup = self.run_grail_hit_supplementary()
            sup_sigs = sup.get("signals", [])
            if sup_sigs:
                self._save_signals(sup_sigs, 1)
                elapsed = time.time() - t0
                print(f"\n[HG] ✅ SUPPLEMENTARY_GRAIL 候选 {len(sup_sigs)} 只 "
                      f"(扫描{sup.get('n_scanned')} / 候选{sup.get('n_candidates')})")
                return {
                    "level": 1, "signals": sup_sigs, "signal_count": len(sup_sigs),
                    "valid": True, "edge_status": self.supplementary_status,
                    "supplementary": True, "honest_empty": False,
                    "edge_note": "SUPPLEMENTARY grail-hit (CAT_PROXIMITY, adjudicate_grail PROMOTE_GRAIL) — 验证增益重排序层, 非核心买点模型",
                    "core_status": self.supplementary_status,
                    "core_note": "CAT_PROXIMITY 经 adjudicate_grail 验证(PROMOTE_GRAIL): lift 3.31 / 事件二项z 9.66 / PCV 0.80 / n 1091",
                    "active_factors": list(self.active_weights.keys()),
                    "elapsed": elapsed, "file": str(SIGNALS_FILE),
                    "supplementary_model": self.supplementary_model,
                }
            elif sup.get("available"):
                # SUPPLEMENTARY 边激活但今日无候选（稀疏信号）-> 诚实空仓, 标注清楚
                self._save_signals([], 1)
                elapsed = time.time() - t0
                print(f"\n[HG] ⚠️ SUPPLEMENTARY_GRAIL 激活但今日无候选(稀疏信号) -> 空仓")
                return {
                    "level": 1, "signals": [], "signal_count": 0,
                    "valid": True, "edge_status": self.supplementary_status,
                    "supplementary": True, "honest_empty": True,
                    "supplementary_no_signal": True,
                    "edge_note": "SUPPLEMENTARY grail-hit 边已验证激活, 但今日无 T日可买+邻近有效催化候选 -> 诚实空仓",
                    "core_status": self.supplementary_status,
                    "core_note": sup.get("reason", ""),
                    "active_factors": list(self.active_weights.keys()),
                    "elapsed": elapsed, "file": str(SIGNALS_FILE),
                }
            # else: import/run 失败(数据缺失) -> 落到下方既有 HONEST_EMPTY 逻辑

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
