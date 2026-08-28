import sqlite3
import json
import time
from .models import Memory

class MemoryStore:
    def __init__(self, db_path="memory.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY, type TEXT, title TEXT, content TEXT,
                environment TEXT, component TEXT, tags TEXT, timestamp REAL,
                confidence TEXT, importance REAL, source TEXT, outcome TEXT, status TEXT,
                updated_at REAL, provenance TEXT)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
            if "updated_at" not in columns: conn.execute("ALTER TABLE memories ADD COLUMN updated_at REAL")
            if "provenance" not in columns: conn.execute("ALTER TABLE memories ADD COLUMN provenance TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_lookup ON memories(status, environment, component, type)")

    def save(self, memory: Memory):
        memory.updated_at = time.time()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""INSERT OR REPLACE INTO memories VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
                memory.id, memory.type, memory.title, memory.content, memory.environment,
                memory.component, json.dumps(memory.tags), memory.timestamp, memory.confidence,
                memory.importance, memory.source, memory.outcome, memory.status,
                memory.updated_at, json.dumps(memory.provenance)))

    def list_all(self, include_inactive=True, limit=None):
        sql = "SELECT * FROM memories" + ("" if include_inactive else " WHERE status='active'") + " ORDER BY timestamp DESC"
        if limit: sql += " LIMIT ?"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (int(limit),) if limit else ()).fetchall()
        return [Memory(id=r["id"], type=r["type"], title=r["title"], content=r["content"],
            environment=r["environment"], component=r["component"], tags=json.loads(r["tags"] or "[]"),
            timestamp=r["timestamp"], confidence=r["confidence"], importance=r["importance"],
            source=r["source"], outcome=r["outcome"], status=r["status"],
            updated_at=r["updated_at"] or r["timestamp"], provenance=json.loads(r["provenance"] or "[]")) for r in rows]

    def get(self, mem_id):
        return next((memory for memory in self.list_all() if memory.id == mem_id), None)

    def update_confidence(self, mem_id, conf):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE memories SET confidence=?, updated_at=? WHERE id=?", (conf, time.time(), mem_id))

    def update_outcome(self, mem_id, outcome):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE memories SET outcome=?, updated_at=? WHERE id=?", (outcome, time.time(), mem_id))

    def set_status(self, mem_id, status):
        if status not in {"active", "superseded", "invalid", "archived"}: raise ValueError("invalid memory status")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE memories SET status=?, updated_at=? WHERE id=?", (status, time.time(), mem_id))
