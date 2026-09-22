"""Provider-neutral embedding adapters."""
from abc import ABC, abstractmethod

class EmbeddingProvider(ABC):
    name = "unknown"
    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai"
    def __init__(self, api_key, model="text-embedding-3-small", base_url="", client=None):
        if not api_key and client is None: raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url or None)
        self.client, self.model = client, model
    def embed(self, text):
        response = self.client.embeddings.create(model=self.model, input=text)
        return [float(value) for value in response.data[0].embedding]

class GeminiEmbeddingProvider(EmbeddingProvider):
    name = "gemini"
    def __init__(self, api_key, model="text-embedding-004", client=None):
        if not api_key and client is None: raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")
        if client is None:
            from google import genai
            client = genai.Client(api_key=api_key)
        self.client, self.model = client, model
    def embed(self, text):
        response = self.client.models.embed_content(model=self.model, contents=text)
        embedding = getattr(response, "embeddings", None) or getattr(response, "embedding", None)
        if isinstance(embedding, list): embedding = embedding[0] if embedding and not isinstance(embedding[0], (int, float)) else embedding
        values = getattr(embedding, "values", embedding)
        return [float(value) for value in values]

class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"
    def __init__(self, host="http://localhost:11434", model="nomic-embed-text", client=None):
        if client is None:
            import ollama
            client = ollama.Client(host=host)
        self.client, self.model = client, model
    def embed(self, text):
        if hasattr(self.client, "embed"):
            response = self.client.embed(model=self.model, input=text)
            values = response.get("embeddings", [])[0] if isinstance(response, dict) else response.embeddings[0]
        else:
            response = self.client.embeddings(model=self.model, prompt=text)
            values = response.get("embedding", [])
        return [float(value) for value in values]

def build_embedding_provider(config=None):
    from app.config import Config
    config = config or Config
    provider = getattr(config, "EMBEDDING_PROVIDER", "hashing").lower()
    if provider in {"hash", "hashing", "deterministic", "none"}: return None
    if provider == "openai": return OpenAIEmbeddingProvider(config.OPENAI_API_KEY, config.OPENAI_EMBEDDING_MODEL, config.OPENAI_BASE_URL)
    if provider == "gemini": return GeminiEmbeddingProvider(config.GEMINI_API_KEY, config.GEMINI_EMBEDDING_MODEL)
    if provider == "ollama": return OllamaEmbeddingProvider(config.OLLAMA_HOST, config.OLLAMA_EMBEDDING_MODEL)
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {provider}")
