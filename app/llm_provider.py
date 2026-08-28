"""
LLM provider abstraction.

The Agent only ever talks to this interface — it never imports ollama or
groq directly. This is what lets you swap providers without touching
agent.py at all.

Every provider's chat() returns the SAME shape:
    {"content": str, "tool_calls": [{"id": str|None, "name": str, "arguments": dict}]}

And every provider knows how to format messages for its own history format
via assistant_message() / tool_result_message() — because Ollama and Groq
disagree on exactly how tool calls should look in conversation history.
"""
import json
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    name: str

    @abstractmethod
    def check_alive(self) -> None:
        """Raise if the provider can't be reached (bad key, server down, etc)."""

    @abstractmethod
    def chat(self, messages: list, tools: list) -> dict:
        """Send messages + tool schema, get back {content, tool_calls}."""

    @abstractmethod
    def assistant_message(self, content: str, tool_calls: list) -> dict:
        """Build the assistant-turn message to append to history."""

    @abstractmethod
    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        """Build the tool-result message to append to history."""


class OllamaProvider(LLMProvider):
    def __init__(self, host: str, model: str):
        import ollama  # imported lazily so tests don't need this installed
        self.client = ollama.Client(host=host)
        self.model = model
        self.name = f"Ollama ({model}, local)"

    def check_alive(self) -> None:
        self.client.list()

    def chat(self, messages: list, tools: list) -> dict:
        resp = self.client.chat(model=self.model, messages=messages, tools=tools)
        msg = resp["message"]
        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc["function"]
            args = fn["arguments"]
            if isinstance(args, str):
                args = json.loads(args)
            tool_calls.append({"id": None, "name": fn["name"], "arguments": args})
        return {"content": msg.get("content", ""), "tool_calls": tool_calls}

    def assistant_message(self, content: str, tool_calls: list) -> dict:
        raw = [{"function": {"name": tc["name"], "arguments": tc["arguments"]}} for tc in tool_calls]
        return {"role": "assistant", "content": content, "tool_calls": raw}

    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        return {"role": "tool", "content": json.dumps(result)}


class GroqProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        from groq import Groq  # imported lazily so tests don't need this installed
        self.client = Groq(api_key=api_key)
        self.model = model
        self.name = f"Groq ({model}, hosted)"

    def check_alive(self) -> None:
        self.client.models.list()

    def chat(self, messages: list, tools: list) -> dict:
        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools,
        )
        msg = resp.choices[0].message
        tool_calls = []
        for tc in (msg.tool_calls or []):
            args = json.loads(tc.function.arguments)
            tool_calls.append({"id": tc.id, "name": tc.function.name, "arguments": args})
        return {"content": msg.content or "", "tool_calls": tool_calls}

    def assistant_message(self, content: str, tool_calls: list) -> dict:
        raw = [{
            "id": tc["id"], "type": "function",
            "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
        } for tc in tool_calls]
        m = {"role": "assistant", "content": content}
        if raw:
            m["tool_calls"] = raw
        return m

    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        return {"role": "tool", "tool_call_id": tool_call["id"], "content": json.dumps(result)}


def build_provider(config) -> LLMProvider:
    if config.LLM_PROVIDER == "groq":
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set.")
        return GroqProvider(api_key=config.GROQ_API_KEY, model=config.GROQ_MODEL)
    return OllamaProvider(host=config.OLLAMA_HOST, model=config.OLLAMA_MODEL)