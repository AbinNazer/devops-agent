"""Provider/model catalog used by Settings; independent of runtime routing."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    provider: str
    capabilities: tuple[str, ...]


PROVIDER_MODELS = (
    ModelDefinition("groq/default", "groq", ("general_reasoning", "tool_use")),
    ModelDefinition("openai/default", "openai", ("general_reasoning", "coding", "tool_use")),
    ModelDefinition("gemini/default", "gemini", ("general_reasoning", "long_context")),
    ModelDefinition("anthropic/default", "anthropic", ("general_reasoning", "coding", "long_context")),
    ModelDefinition("ollama/default", "ollama", ("general_reasoning", "local")),
)


def catalog() -> list[dict]:
    providers: dict[str, dict] = {}
    for model in PROVIDER_MODELS:
        providers.setdefault(model.provider, {"id": model.provider, "models": []})["models"].append({"id": model.id, "capabilities": list(model.capabilities)})
    return list(providers.values())
