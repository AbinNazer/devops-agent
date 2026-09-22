"""SQLite-backed semantic vector index using a local sentence-transformers model."""
from __future__ import annotations
import hashlib, json, math, sqlite3

DEFAULT_MODEL = "all-MiniLM-L6-v2"

class VectorStore:
    def __init__(self, path="vectors.db", dimensions=128, model_name=DEFAULT_MODEL, embedder=None, provider=None):
        self.path, self.model_name, self._embedder, self.dimensions = path, model_name, embedder, dimensions
        self.provider = provider
        self.embedding_backend = "custom" if embedder else None
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, text TEXT NOT NULL, metadata TEXT NOT NULL, vector TEXT NOT NULL)")

    def _model(self):
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError("Install sentence-transformers to use semantic memory search") from exc
            self._embedder = SentenceTransformer(self.model_name)
        return self._embedder

    def _hash_embed(self, text):
        values = [0.0] * self.dimensions
        for token in str(text).lower().split():
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            values[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

    def embed(self, text):
        if self.provider is None and self._embedder is None:
            try:
                from app.embedding_provider import build_embedding_provider
                self.provider = build_embedding_provider()
            except Exception:
                self.provider = False
        if self.provider and self.provider is not False:
            try:
                values = self.provider.embed(str(text))
                self.embedding_backend = getattr(self.provider, "name", "configured")
                self.dimensions = len(values)
                return values
            except Exception:
                self.provider = False
        if self._embedder is not None:
            vector = self._embedder.encode(str(text), normalize_embeddings=True)
        else:
            self.embedding_backend = "hashing"
            return self._hash_embed(text)
        values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
        values = [float(value) for value in values]
        self.dimensions = len(values)
        return values

    def upsert(self, item_id, text, metadata=None):
        vector = self.embed(text)
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT OR REPLACE INTO vectors VALUES (?,?,?,?)", (item_id, text, json.dumps(metadata or {}), json.dumps(vector)))

    def query(self, text, limit=5):
        needle = self.embed(text)
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT id,text,metadata,vector FROM vectors").fetchall()
        scored = []
        for item_id, value, metadata, raw in rows:
            vector = json.loads(raw)
            if len(vector) != len(needle):
                continue
            score = sum(a * b for a, b in zip(needle, vector))
            scored.append({"id": item_id, "text": value, "metadata": json.loads(metadata), "score": score})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:max(1, min(int(limit), 50))]

    def delete(self, item_id):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM vectors WHERE id=?", (item_id,))
