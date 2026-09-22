"""Provider-neutral LLM usage accounting backed by SQLite."""
from __future__ import annotations
import json, sqlite3, threading, time, uuid
from dataclasses import dataclass, asdict
from pathlib import Path

@dataclass
class UsageRecord:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider: str = "unknown"
    model: str = "unknown"
    request_id: str = ""
    conversation_id: str = ""
    timestamp: float = 0.0
    usage_known: bool = False

class UsageTracker:
    def __init__(self, path: str = "usage.db"):
        self.path = str(Path(path))
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS llm_usage (
                id TEXT PRIMARY KEY, input_tokens INTEGER, output_tokens INTEGER,
                total_tokens INTEGER, provider TEXT NOT NULL, model TEXT NOT NULL,
                request_id TEXT, conversation_id TEXT, timestamp REAL NOT NULL,
                usage_known INTEGER NOT NULL DEFAULT 0, raw_usage TEXT)""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_time ON llm_usage(timestamp)")
    def _connect(self):
        return sqlite3.connect(self.path, check_same_thread=False)
    def record(self, record: UsageRecord, raw_usage=None):
        record.timestamp = record.timestamp or time.time()
        record.total_tokens = record.total_tokens if record.total_tokens is not None else (
            (record.input_tokens or 0) + (record.output_tokens or 0) if record.usage_known else None)
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO llm_usage VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                str(uuid.uuid4()), record.input_tokens, record.output_tokens,
                record.total_tokens, record.provider, record.model, record.request_id,
                record.conversation_id, record.timestamp, int(record.usage_known),
                json.dumps(raw_usage, default=str) if raw_usage is not None else ""))
        return record
    def summary(self, since: float | None = None):
        since = since or 0
        with self._connect() as db:
            rows = db.execute("SELECT provider,model,input_tokens,output_tokens,total_tokens,usage_known FROM llm_usage WHERE timestamp>=?", (since,)).fetchall()
        known = [r for r in rows if r[5]]
        def total(index): return sum((r[index] or 0) for r in known)
        by_provider = {}
        for provider, model, inp, out, total_tokens, _ in known:
            item = by_provider.setdefault(provider, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "requests": 0})
            item["input_tokens"] += inp or 0; item["output_tokens"] += out or 0; item["total_tokens"] += total_tokens or 0; item["requests"] += 1
        return {"requests": len(rows), "known_requests": len(known), "unknown_requests": len(rows)-len(known), "input_tokens": total(2), "output_tokens": total(3), "total_tokens": total(4), "by_provider": by_provider}

def normalize_usage(raw) -> dict:
    """Normalize common OpenAI, Anthropic, Gemini, and Ollama usage shapes."""
    if raw is None: return {"usage_known": False}
    if isinstance(raw, dict):
        get = raw.get
        inp = get("input_tokens", get("prompt_tokens", get("prompt_eval_count")))
        out = get("output_tokens", get("completion_tokens", get("eval_count")))
        total = get("total_tokens")
    else:
        inp = getattr(raw, "input_tokens", getattr(raw, "prompt_tokens", getattr(raw, "prompt_eval_count", None)))
        out = getattr(raw, "output_tokens", getattr(raw, "completion_tokens", getattr(raw, "eval_count", None)))
        total = getattr(raw, "total_tokens", None)
    values = lambda x: int(x) if x is not None else None
    inp, out, total = values(inp), values(out), values(total)
    if total is None and inp is not None and out is not None: total = inp + out
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": total, "usage_known": any(x is not None for x in (inp, out, total))}

_tracker = None
SESSION_STARTED = time.time()
def get_usage_tracker(path="usage.db"):
    global _tracker
    if _tracker is None: _tracker = UsageTracker(path)
    return _tracker
