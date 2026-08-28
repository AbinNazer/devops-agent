import sqlite3
import json
from .models import Memory

class MemoryStore:
    def __init__(self, db_path="memory.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, type TEXT, title TEXT, content TEXT,
                    environment TEXT, component TEXT, tags TEXT, timestamp REAL,
                    confidence TEXT, importance REAL, source TEXT, outcome TEXT, status TEXT
                )
            """)

    def save(self, memory: Memory):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory.id, memory.type, memory.title, memory.content,
                memory.environment, memory.component, json.dumps(memory.tags),
                memory.timestamp, memory.confidence, memory.importance,
                memory.source, memory.outcome, memory.status
            ))

    def list_all(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM memories").fetchall()
            return [Memory(
                id=r["id"], type=r["type"], title=r["title"], content=r["content"],
                environment=r["environment"], component=r["component"], tags=json.loads(r["tags"]),
                timestamp=r["timestamp"], confidence=r["confidence"], importance=r["importance"],
                source=r["source"], outcome=r["outcome"], status=r["status"]
            ) for r in rows]

    def update_confidence(self, mem_id, conf):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE memories SET confidence=? WHERE id=?", (conf, mem_id))

    def update_outcome(self, mem_id, outcome):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE memories SET outcome=? WHERE id=?", (outcome, mem_id))
