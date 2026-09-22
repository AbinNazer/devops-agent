from app.vector_store import VectorStore

class FakeEmbedder:
    def encode(self, text, normalize_embeddings=True):
        return [1.0, 0.0] if "docker" in text else [0.0, 1.0]

class FakeProvider:
    name = "fake"
    def embed(self, text):
        return [1.0, 0.0] if "docker" in text else [0.0, 1.0]

def test_vector_store_upsert_query(tmp_path):
    store = VectorStore(str(tmp_path / "vectors.db"), embedder=FakeEmbedder())
    store.upsert("a", "docker backend crashed", {"kind": "incident"})
    store.upsert("b", "frontend styling", {})
    result = store.query("docker backend error")
    assert result[0]["id"] == "a"
    assert result[0]["metadata"]["kind"] == "incident"

def test_vector_store_delete(tmp_path):
    store = VectorStore(str(tmp_path / "vectors.db"), embedder=FakeEmbedder())
    store.upsert("a", "test")
    store.delete("a")
    assert store.query("test")[0:1] == []

def test_configured_provider_is_used(tmp_path):
    store = VectorStore(str(tmp_path / "vectors.db"), provider=FakeProvider())
    assert store.embed("docker") == [1.0, 0.0]
    assert store.embedding_backend == "fake"

def test_provider_failure_falls_back_to_hashing(tmp_path):
    class Broken:
        name = "broken"
        def embed(self, text): raise RuntimeError("unavailable")
    store = VectorStore(str(tmp_path / "vectors.db"), provider=Broken())
    vector = store.embed("docker backend")
    assert len(vector) == 128
    assert store.embedding_backend == "hashing"
