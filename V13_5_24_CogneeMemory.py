"""
V13.5.24 Cognee 长期记忆模块
============================
为毕方灵犀貔貅助手构建决策知识图谱，实现跨会话学习与 T+1 复盘召回。

设计原则：
  1. 与现有 .workbuddy/memory 系统互补，不替代。
  2. 仅把“高价值决策信息”写入 Cognee，避免无差别灌入日志。
  3. 所有 cognee 操作异步，失败时降级到 SQLite 本地日志。
  4. 无 LLM 配置时优雅跳过，不阻塞主流程。

依赖：
  - cognee (已安装到 managed venv)
  - 环境变量：OPENAI_API_KEY 或 LLM_API_KEY（或 LLM_PROVIDER=ollama 等）

用法：
    from V13_5_24_CogneeMemory import CogneeMemoryManager
    mgr = CogneeMemoryManager()
    await mgr.ingest_signal({...})
    results = await mgr.recall_similar_signals({"defcon": "ORANGE", "pattern": "D49"})
"""
import os
import sys
import json
import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

# 强制使用 managed venv 的包路径
VENV_SITE = Path("E:/WorkBuddy_dot_workbuddy/binaries/python/envs/default/Lib/site-packages")
if str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

PROJECT_ROOT = Path("E:/WorkBuddy_dot_workbuddy/Claw")
DATA_DIR = PROJECT_ROOT / ".workbuddy" / "cognee_data"
FALLBACK_DB = PROJECT_ROOT / ".workbuddy" / "cognee_fallback.db"


def _ensure_cognee_env():
    """设置 Cognee 默认环境变量。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (PROJECT_ROOT / ".workbuddy" / "cognee_system").mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DATA_ROOT_DIRECTORY", str(DATA_DIR))
    os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(PROJECT_ROOT / ".workbuddy" / "cognee_system"))
    os.environ.setdefault("COGNEE_LOG_LEVEL", "WARNING")
    # Ollama 默认配置兜底
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "ollama":
        os.environ.setdefault("LLM_API_BASE", "http://localhost:11434")
        os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
        # Cognee v1.2.2 要求 LLM_ENDPOINT + LLM_API_KEY 同时存在
        os.environ.setdefault("LLM_ENDPOINT", "http://localhost:11434/v1")
        os.environ.setdefault("LLM_API_KEY", "ollama")
        os.environ.setdefault("LLM_MODEL", os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"))
        os.environ.setdefault("OLLAMA_MODEL", os.environ.get("LLM_MODEL", "qwen2.5:7b"))
        # Cognee Embedding 默认走 OpenAI，本地 Ollama 需显式覆盖
        os.environ.setdefault("EMBEDDING_PROVIDER", "ollama")
        os.environ.setdefault("EMBEDDING_MODEL", "nomic-embed-text")
        os.environ.setdefault("EMBEDDING_ENDPOINT", "http://localhost:11434/api/embed")
        os.environ.setdefault("EMBEDDING_DIMENSIONS", "768")
        # OllamaEmbeddingEngine 需要 HuggingFace tokenizer 做 token 计数；国内走镜像
        os.environ.setdefault("HUGGINGFACE_TOKENIZER", "bert-base-uncased")
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


class CogneeMemoryManager:
    """Cognee 记忆管理器，负责把交易决策写入知识图谱并支持语义召回。"""

    def __init__(self, dataset_name: str = "claw_trading_memory"):
        _ensure_cognee_env()
        self.dataset_name = dataset_name
        self._cognee = None
        self._llm_ready = False
        self._import_error = None
        self._init_fallback_db()

    # ------------------------------------------------------------------
    # 初始化与降级
    # ------------------------------------------------------------------
    def _init_fallback_db(self):
        """创建本地 SQLite 降级表，Cognee 失败时仍可记录。"""
        FALLBACK_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(FALLBACK_DB)) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_type TEXT NOT NULL,
                    record_date TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_type_date ON memory_records(record_type, record_date);
                """
            )

    async def _lazy_import_cognee(self):
        """延迟导入 cognee，避免主流程因未安装而崩溃。"""
        if self._cognee is not None:
            return self._cognee
        try:
            import cognee
            self._cognee = cognee
            self._llm_ready = self._check_llm_config()
            return self._cognee
        except Exception as e:
            self._import_error = str(e)
            return None

    @staticmethod
    def _check_llm_config() -> bool:
        """检查是否配置了 LLM。"""
        openai_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        provider = os.environ.get("LLM_PROVIDER", "openai").lower()
        if provider == "openai" and openai_key:
            return True
        if provider == "ollama":
            model = os.environ.get("LLM_MODEL") or os.environ.get("OLLAMA_MODEL")
            return bool(model)
        if provider in {"anthropic", "gemini", "groq", "mistral"}:
            return True
        return False

    async def _fallback_write(self, record_type: str, payload: Dict[str, Any]):
        """Cognee 不可用时写入 SQLite 降级表。"""
        with sqlite3.connect(str(FALLBACK_DB)) as conn:
            conn.execute(
                "INSERT INTO memory_records (record_type, record_date, payload) VALUES (?, ?, ?)",
                (record_type, payload.get("date", datetime.now().strftime("%Y-%m-%d")), json.dumps(payload, ensure_ascii=False)),
            )

    # ------------------------------------------------------------------
    # 公开 API：写入
    # ------------------------------------------------------------------
    async def ingest_signal(self, signal: Dict[str, Any]):
        """
        写入一条选股信号。

        signal 示例：
            {
                "date": "2026-07-06",
                "code": "600719",
                "name": "大连热电",
                "sector": "电力",
                "m71_score": 72,
                "defcon": "ORANGE",
                "five_confirm": 3,
                "d29": 6, "d31": 7, "d32": 5, "d33": 2, "d34": 3,
                "pattern": "D49长下影线反转",
                "price": 7.76,
            }
        """
        text = self._signal_to_text(signal)
        cognee = await self._lazy_import_cognee()
        if not cognee or not self._llm_ready:
            await self._fallback_write("signal", signal)
            return {"status": "fallback", "reason": "cognee or llm not ready"}

        try:
            await cognee.remember(text, dataset_name=self.dataset_name)
            return {"status": "ok"}
        except Exception as e:
            await self._fallback_write("signal", signal)
            return {"status": "fallback", "reason": str(e)}

    async def ingest_outcome(self, outcome: Dict[str, Any]):
        """
        写入信号结果（T+1 收盘后）。

        outcome 示例：
            {
                "date": "2026-07-07",
                "code": "600719",
                "signal_date": "2026-07-06",
                "t1_return_pct": 1.2,
                "hit": true,
                "limit_up": false,
                "root_cause": "F1-F4通过+电力板块回暖",
            }
        """
        text = self._outcome_to_text(outcome)
        cognee = await self._lazy_import_cognee()
        if not cognee or not self._llm_ready:
            await self._fallback_write("outcome", outcome)
            return {"status": "fallback"}
        try:
            await cognee.remember(text, dataset_name=self.dataset_name)
            return {"status": "ok"}
        except Exception as e:
            await self._fallback_write("outcome", outcome)
            return {"status": "fallback", "reason": str(e)}

    async def ingest_model_version(self, version: str, changelog: str, active_date: str):
        """写入模型版本变更。"""
        payload = {
            "type": "model_version",
            "version": version,
            "changelog": changelog,
            "active_date": active_date,
        }
        text = f"模型版本 {version} 于 {active_date} 上线。变更内容：{changelog}"
        cognee = await self._lazy_import_cognee()
        if not cognee or not self._llm_ready:
            await self._fallback_write("model_version", payload)
            return {"status": "fallback"}
        try:
            await cognee.remember(text, dataset_name=self.dataset_name)
            return {"status": "ok"}
        except Exception as e:
            await self._fallback_write("model_version", payload)
            return {"status": "fallback", "reason": str(e)}

    async def ingest_market_regime(self, regime: Dict[str, Any]):
        """写入市场环境（MEG评估）。"""
        text = (
            f"{regime.get('date')} 市场环境：DEFCON={regime.get('defcon')}, "
            f"MES={regime.get('mes')}, 上证涨跌幅={regime.get('index_chg_pct')}%, "
            f"涨跌比={regime.get('breadth')}, 创业板={regime.get('cy_chg_pct')}%。"
        )
        cognee = await self._lazy_import_cognee()
        if not cognee or not self._llm_ready:
            await self._fallback_write("market_regime", regime)
            return {"status": "fallback"}
        try:
            await cognee.remember(text, dataset_name=self.dataset_name)
            return {"status": "ok"}
        except Exception as e:
            await self._fallback_write("market_regime", regime)
            return {"status": "fallback", "reason": str(e)}

    # ------------------------------------------------------------------
    # 公开 API：召回
    # ------------------------------------------------------------------
    async def recall_similar_signals(self, current_conditions: Dict[str, Any], top_k: int = 5) -> List[str]:
        """
        召回与当前条件相似的历史信号。
        current_conditions 示例：{"defcon": "ORANGE", "pattern": "D49", "sector": "电力"}
        """
        query = self._build_recall_query(current_conditions)
        return await self._recall(query, top_k)

    async def recall_pattern_outcomes(self, pattern_name: str, top_k: int = 5) -> List[str]:
        """召回某模式（如 D49）的历史表现。"""
        query = f"{pattern_name} 模式在历史信号中的 T+1 表现和成功案例"
        return await self._recall(query, top_k)

    async def recall_sector_under_regime(self, sector: str, defcon: str, top_k: int = 5) -> List[str]:
        """召回某板块在特定市场环境下的表现。"""
        query = f"DEFCON={defcon} 时，{sector} 板块哪些个股成功上涨"
        return await self._recall(query, top_k)

    async def _recall(self, query: str, top_k: int, query_type: Any = None) -> List[str]:
        """底层 recall。默认使用 CHUNKS 向量召回，避免 LLM 生成延迟。"""
        cognee = await self._lazy_import_cognee()
        if not cognee or not self._llm_ready:
            return [f"[Cognee unavailable] {query}"]
        try:
            from cognee.modules.search.types import SearchType

            if query_type is None:
                query_type = SearchType.CHUNKS
            results = await cognee.recall(
                query_text=query,
                query_type=query_type,
                datasets=[self.dataset_name],
                top_k=top_k,
            )
            return [r.text for r in results[:top_k]] if results else []
        except Exception as e:
            return [f"[Cognee recall error] {e}"]

    # ------------------------------------------------------------------
    # 文本生成
    # ------------------------------------------------------------------
    @staticmethod
    def _signal_to_text(signal: Dict[str, Any]) -> str:
        parts = [
            f"{signal.get('date')} 选股信号：{signal.get('code')} {signal.get('name')}，",
            f"板块{signal.get('sector')}，M71评分{signal.get('m71_score')}，",
            f"市场环境{signal.get('defcon')}，五确认{signal.get('five_confirm')}个，",
            f"D29={signal.get('d29')} D31={signal.get('d31')} D32={signal.get('d32')} D33={signal.get('d33')} D34={signal.get('d34')}，",
            f"触发形态{signal.get('pattern')}，当前价格{signal.get('price')}。",
        ]
        return "".join(parts)

    @staticmethod
    def _outcome_to_text(outcome: Dict[str, Any]) -> str:
        parts = [
            f"{outcome.get('code')} 在 {outcome.get('signal_date')} 买入后，",
            f"T+1 ({outcome.get('date')}) 涨跌幅 {outcome.get('t1_return_pct')}%，",
            f"{'涨停' if outcome.get('limit_up') else '未涨停'}，",
            f"{'命中' if outcome.get('hit') else '未命中'}，",
            f"根因：{outcome.get('root_cause')}。",
        ]
        return "".join(parts)

    @staticmethod
    def _build_recall_query(conditions: Dict[str, Any]) -> str:
        parts = ["历史选股信号"]
        if "defcon" in conditions:
            parts.append(f"DEFCON={conditions['defcon']}")
        if "pattern" in conditions:
            parts.append(f"形态{conditions['pattern']}")
        if "sector" in conditions:
            parts.append(f"板块{conditions['sector']}")
        if "five_confirm" in conditions:
            parts.append(f"五确认≥{conditions['five_confirm']}")
        parts.append("的T+1表现和成功案例")
        return "，".join(parts)

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    async def health(self) -> Dict[str, Any]:
        """返回当前记忆系统健康状态。"""
        cognee = await self._lazy_import_cognee()
        return {
            "cognee_imported": cognee is not None,
            "import_error": self._import_error,
            "llm_ready": self._llm_ready,
            "dataset": self.dataset_name,
            "data_dir": str(DATA_DIR),
            "fallback_db": str(FALLBACK_DB),
        }


# ------------------------------------------------------------------
# 同步包装（方便非异步调用方）
# ------------------------------------------------------------------
def ingest_signal_sync(signal: Dict[str, Any]) -> Dict[str, Any]:
    return asyncio.run(CogneeMemoryManager().ingest_signal(signal))


def ingest_outcome_sync(outcome: Dict[str, Any]) -> Dict[str, Any]:
    return asyncio.run(CogneeMemoryManager().ingest_outcome(outcome))


def recall_similar_signals_sync(conditions: Dict[str, Any], top_k: int = 5) -> List[str]:
    return asyncio.run(CogneeMemoryManager().recall_similar_signals(conditions, top_k))


def health_sync() -> Dict[str, Any]:
    return asyncio.run(CogneeMemoryManager().health())


if __name__ == "__main__":
    # 运行诊断
    print(json.dumps(health_sync(), ensure_ascii=False, indent=2))
