from types import SimpleNamespace
from app.embedding_provider import OpenAIEmbeddingProvider, GeminiEmbeddingProvider, OllamaEmbeddingProvider, build_embedding_provider

def test_openai_adapter():
    client = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(data=[SimpleNamespace(embedding=[1, 2])])) )
    assert OpenAIEmbeddingProvider("key", client=client).embed("x") == [1.0, 2.0]

def test_gemini_adapter():
    client = SimpleNamespace(models=SimpleNamespace(embed_content=lambda **kwargs: SimpleNamespace(embedding=SimpleNamespace(values=[3, 4]))))
    assert GeminiEmbeddingProvider("key", client=client).embed("x") == [3.0, 4.0]

def test_ollama_adapter():
    client = SimpleNamespace(embed=lambda **kwargs: {"embeddings": [[5, 6]]})
    assert OllamaEmbeddingProvider(client=client).embed("x") == [5.0, 6.0]

def test_provider_selection(monkeypatch):
    config = SimpleNamespace(EMBEDDING_PROVIDER="hashing")
    assert build_embedding_provider(config) is None
