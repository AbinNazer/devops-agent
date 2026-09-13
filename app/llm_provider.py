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
import uuid
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
            tool_calls.append({"id": f"call_{uuid.uuid4().hex[:12]}", "name": fn["name"], "arguments": args})
        return {"content": msg.get("content", ""), "tool_calls": tool_calls}

    def assistant_message(self, content: str, tool_calls: list) -> dict:
        raw = [{"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}} for tc in tool_calls]
        return {"role": "assistant", "content": content, "tool_calls": raw}

    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        return {"role": "tool", "tool_call_id": tool_call["id"], "content": json.dumps(result)}


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


class OpenAIProvider(LLMProvider):
    """OpenAI API provider."""

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI  # lazy import
        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.name = f"OpenAI ({model}, hosted)"

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


class AnthropicProvider(LLMProvider):
    """Anthropic Claude API provider.

    Anthropic uses a different API shape:
    - System prompt goes as a top-level 'system' parameter, not a message.
    - Tool definitions use a different schema format.
    - Tool results use 'tool_use_id' instead of 'tool_call_id'.
    - Tool calls are returned as content blocks, not as a top-level field.
    """

    def __init__(self, api_key: str, model: str):
        from anthropic import Anthropic  # lazy import
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.name = f"Anthropic ({model}, hosted)"

    def check_alive(self) -> None:
        # Anthropic doesn't have a models.list; use a minimal messages call
        # to verify the key works.
        self.client.messages.create(
            model=self.model,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )

    def _convert_tools(self, tools: list) -> list:
        """Convert OpenAI-style tool schemas to Anthropic format."""
        converted = []
        for tool in tools:
            fn = tool.get("function", {})
            converted.append({
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return converted

    def _convert_messages(self, messages: list) -> tuple:
        """Convert conversation history to Anthropic format.

        Returns (system_text, messages_list). Anthropic requires:
        - System as a top-level parameter, not a message
        - No assistant messages with tool_use blocks as raw dicts (must be
          converted to Anthropic content block format)
        - Tool results as 'tool_result' content blocks
        """
        system_text = ""
        converted = []

        for msg in messages:
            role = msg.get("role", "")

            if role == "system":
                system_text = msg.get("content", "")
                continue

            if role == "assistant":
                # Check if this assistant message has tool_calls in OpenAI format
                tool_calls = msg.get("tool_calls", [])
                content = msg.get("content", "")

                if tool_calls:
                    # Build Anthropic content blocks
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": content})
                    for tc in tool_calls:
                        if isinstance(tc, dict) and "function" in tc:
                            fn = tc["function"]
                            name = fn.get("name", "")
                            args = fn.get("arguments", "{}")
                            if isinstance(args, str):
                                blocks.append({
                                    "type": "tool_use",
                                    "id": tc.get("id", f"toolu_{name}"),
                                    "name": name,
                                    "input": json.loads(args),
                                })
                            else:
                                blocks.append({
                                    "type": "tool_use",
                                    "id": tc.get("id", f"toolu_{name}"),
                                    "name": name,
                                    "input": args,
                                })
                        elif isinstance(tc, dict) and "name" in tc:
                            blocks.append({
                                "type": "tool_use",
                                "id": tc.get("id", f"toolu_{tc['name']}"),
                                "name": tc["name"],
                                "input": tc.get("arguments", {}),
                            })
                    converted.append({"role": "assistant", "content": blocks})
                else:
                    converted.append({"role": "assistant", "content": content or ""})

            elif role == "tool":
                # Convert tool result to Anthropic format
                tool_call_id = msg.get("tool_call_id", msg.get("name", ""))
                content = msg.get("content", "")
                if isinstance(content, dict):
                    content = json.dumps(content)
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content,
                    }],
                })

            else:
                converted.append(msg)

        return system_text, converted

    def chat(self, messages: list, tools: list) -> dict:
        system_text, converted_messages = self._convert_messages(messages)
        anthropic_tools = self._convert_tools(tools) if tools else []

        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": converted_messages,
        }
        if system_text:
            kwargs["system"] = system_text
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        resp = self.client.messages.create(**kwargs)

        # Extract content and tool calls from response
        text_content = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text_content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {"content": text_content, "tool_calls": tool_calls}

    def assistant_message(self, content: str, tool_calls: list) -> dict:
        """Build assistant message in a format that our _convert_messages can handle."""
        # Store in OpenAI-compatible format so _convert_messages can convert it
        raw = []
        for tc in tool_calls:
            raw.append({
                "id": tc.get("id", f"toolu_{tc['name']}"),
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("arguments", tc.get("input", {})))},
            })
        m = {"role": "assistant", "content": content}
        if raw:
            m["tool_calls"] = raw
        return m

    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id", tool_call.get("name", "")),
            "name": tool_call.get("name", ""),
            "content": json.dumps(result),
        }

class GeminiProvider(LLMProvider):
    """Google Gemini API provider."""

    def __init__(self, api_key: str, model: str):
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.name = f"Gemini ({model}, hosted)"

    def check_alive(self) -> None:
        self.client.models.get(model=self.model)

    def chat(self, messages: list, tools: list) -> dict:
        from google.genai import types

        system_instruction = None
        contents = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_instruction = content
                continue

            if role == "user":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=content or "")],
                    )
                )

            elif role == "assistant":
                contents.append(
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(text=content or "")],
                    )
                )

            elif role == "tool":
                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=content or "")],
                    )
                )

        config_kwargs = {}

        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        if tools:
            function_declarations = []

            for tool in tools:
                fn = tool.get("function", {})
                function_declarations.append(
                    types.FunctionDeclaration(
                        name=fn.get("name", ""),
                        description=fn.get("description", ""),
                        parameters=fn.get(
                            "parameters",
                            {"type": "object", "properties": {}},
                        ),
                    )
                )

            config_kwargs["tools"] = [
                types.Tool(function_declarations=function_declarations)
            ]

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )

        text_content = ""
        tool_calls = []

        for candidate in response.candidates or []:
            if not candidate.content:
                continue

            for part in candidate.content.parts or []:
                if getattr(part, "text", None):
                    text_content += part.text

                if getattr(part, "function_call", None):
                    fc = part.function_call

                    tool_calls.append({
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "name": fc.name,
                        "arguments": dict(fc.args or {}),
                    })

        return {
            "content": text_content,
            "tool_calls": tool_calls,
        }

    def assistant_message(self, content: str, tool_calls: list) -> dict:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        }

    def tool_result_message(self, tool_call: dict, result: dict) -> dict:
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": json.dumps(result),
        }

def build_provider(config) -> LLMProvider:
    if config.LLM_PROVIDER == "groq":
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set.")
        return GroqProvider(
            api_key=config.GROQ_API_KEY,
            model=config.GROQ_MODEL,
        )

    if config.LLM_PROVIDER == "openai":
        if not config.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        return OpenAIProvider(
            api_key=config.OPENAI_API_KEY,
            model=config.OPENAI_MODEL,
        )

    if config.LLM_PROVIDER == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        return AnthropicProvider(
            api_key=config.ANTHROPIC_API_KEY,
            model=config.ANTHROPIC_MODEL,
        )

    if config.LLM_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        return GeminiProvider(
            api_key=config.GEMINI_API_KEY,
            model=config.GEMINI_MODEL,
        )

    if config.LLM_PROVIDER == "ollama":
        return OllamaProvider(
            host=config.OLLAMA_HOST,
            model=config.OLLAMA_MODEL,
        )

    raise RuntimeError(
        f"Unsupported LLM_PROVIDER: {config.LLM_PROVIDER}"
    )
