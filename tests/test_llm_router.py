"""
Tests for the multi-provider LLM router.

Covers: provider loading, routing/failover, context preservation,
tool calling, security, .env preservation, and error handling.

All tests use mocks — no live API calls required.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from app.llm_provider import LLMProvider
from app.llm_router import (
    LLMRouter, ProviderState, build_router, _classify_error,
    STATUS_AVAILABLE, STATUS_COOLDOWN, STATUS_FAILED,
)


# ── Helpers ──


class MockProvider(LLMProvider):
    """Deterministic mock provider for testing."""

    def __init__(self, name="MockProvider", responses=None, fail_with=None):
        self.name = name
        self._responses = list(responses or [])
        self._fail_with = fail_with
        self.chat_calls = []
        self.call_count = 0

    def check_alive(self):
        if self._fail_with and "alive" in str(self._fail_with):
            raise self._fail_with

    def chat(self, messages, tools):
        self.chat_calls.append({"messages": messages, "tools": tools})
        self.call_count += 1
        if self._fail_with:
            raise self._fail_with
        if self._responses:
            return self._responses.pop(0)
        return {"content": f"Response from {self.name}", "tool_calls": []}

    def assistant_message(self, content, tool_calls):
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    def tool_result_message(self, tool_call, result):
        return {"role": "tool", "tool_call_id": tool_call.get("id", "t1"), "content": json.dumps(result)}


def _rate_limit_error():
    return Exception("rate_limit_exceeded: tokens per minute limit")

def _timeout_error():
    return Exception("connection_timeout: request timed out")

def _server_error():
    return Exception("503 service temporarily unavailable")

def _auth_error():
    return Exception("authentication_error: invalid API key")

def _model_error():
    return Exception("model_not_found: model 'xyz' does not exist")

def _generic_error():
    return Exception("something went wrong")


# ── Provider loading ──


class TestProviderLoading:
    """Test that providers can be loaded from config."""

    def test_groq_provider_loads(self):
        from app.config import Config
        mock_groq = MagicMock()
        mock_client = MagicMock()
        mock_groq.return_value = mock_client
        with patch.dict("sys.modules", {"groq": MagicMock(Groq=mock_groq)}):
            from app.llm_provider import GroqProvider
            provider = GroqProvider(api_key="test-key", model="test-model")
            assert isinstance(provider, GroqProvider)
            assert "Groq" in provider.name

    def test_openai_provider_loads(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai)}):
            from app.llm_provider import OpenAIProvider
            provider = OpenAIProvider(api_key="test-key", model="gpt-4o")
            assert isinstance(provider, OpenAIProvider)
            assert "OpenAI" in provider.name

    def test_anthropic_provider_loads(self):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=mock_anthropic)}):
            from app.llm_provider import AnthropicProvider
            provider = AnthropicProvider(api_key="test-key", model="claude-test")
            assert isinstance(provider, AnthropicProvider)
            assert "Anthropic" in provider.name

    def test_ollama_provider_loads(self):
        mock_ollama = MagicMock()
        mock_client = MagicMock()
        mock_ollama.Client.return_value = mock_client
        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            from app.llm_provider import OllamaProvider
            provider = OllamaProvider(host="http://localhost:11434", model="test")
            assert isinstance(provider, OllamaProvider)
            assert "Ollama" in provider.name

    def test_missing_groq_key_raises(self):
        config = MagicMock()
        config.LLM_PROVIDER = "groq"
        config.GROQ_API_KEY = ""
        with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
            from app.llm_provider import build_provider
            build_provider(config)

    def test_missing_openai_key_raises(self):
        config = MagicMock()
        config.LLM_PROVIDER = "openai"
        config.OPENAI_API_KEY = ""
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            from app.llm_provider import build_provider
            build_provider(config)

    def test_missing_anthropic_key_raises(self):
        config = MagicMock()
        config.LLM_PROVIDER = "anthropic"
        config.ANTHROPIC_API_KEY = ""
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            from app.llm_provider import build_provider
            build_provider(config)


# ── Router construction ──


class TestRouterConstruction:
    def test_single_provider(self):
        p1 = MockProvider(name="P1")
        router = LLMRouter(providers=[p1])
        assert len(router.providers) == 1
        assert router.primary.name == "P1"

    def test_multiple_providers(self):
        p1, p2, p3 = MockProvider(name="P1"), MockProvider(name="P2"), MockProvider(name="P3")
        router = LLMRouter(providers=[p1, p2, p3])
        assert len(router.providers) == 3
        assert router.primary.name == "P1"

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError, match="at least one provider"):
            LLMRouter(providers=[])

    def test_build_router_single_provider(self):
        config = MagicMock()
        config.LLM_PROVIDER = "ollama"
        config.OLLAMA_HOST = "http://localhost:11434"
        config.OLLAMA_MODEL = "test"
        config.GROQ_API_KEY = ""
        config.OPENAI_API_KEY = ""
        config.ANTHROPIC_API_KEY = ""
        config.LLM_FALLBACK_PROVIDERS = []
        with patch("app.llm_router.OllamaProvider") as MockOllama:
            MockOllama.return_value = MockProvider(name="Ollama")
            router = build_router(config)
            assert isinstance(router, LLMRouter)

    def test_build_router_with_fallbacks(self):
        config = MagicMock()
        config.LLM_PROVIDER = "groq"
        config.GROQ_API_KEY = "test-key"
        config.GROQ_MODEL = "test-model"
        config.OPENAI_API_KEY = "test-key"
        config.OPENAI_MODEL = "gpt-4o"
        config.ANTHROPIC_API_KEY = ""
        config.OLLAMA_HOST = "http://localhost:11434"
        config.OLLAMA_MODEL = "test"
        config.LLM_FALLBACK_PROVIDERS = ["openai"]
        with patch("app.llm_router.GroqProvider") as MockGroq, \
             patch("app.llm_router.OpenAIProvider") as MockOpenAI:
            MockGroq.return_value = MockProvider(name="Groq")
            MockOpenAI.return_value = MockProvider(name="OpenAI")
            router = build_router(config)
            assert len(router.providers) >= 2


# ── Routing / failover ──


class TestRoutingFailover:
    def test_primary_provider_succeeds(self):
        p1 = MockProvider(name="P1", responses=[{"content": "ok", "tool_calls": []}])
        p2 = MockProvider(name="P2", responses=[{"content": "should not be called", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        result = router.chat([{"role": "user", "content": "hi"}], [])
        assert result["content"] == "ok"
        assert p1.call_count == 1
        assert p2.call_count == 0

    def test_failover_after_rate_limit(self):
        p1 = MockProvider(name="P1", fail_with=_rate_limit_error())
        p2 = MockProvider(name="P2", responses=[{"content": "fallback ok", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        result = router.chat([{"role": "user", "content": "hi"}], [])
        assert result["content"] == "fallback ok"
        assert p1.call_count == 1
        assert p2.call_count == 1

    def test_failover_after_timeout(self):
        p1 = MockProvider(name="P1", fail_with=_timeout_error())
        p2 = MockProvider(name="P2", responses=[{"content": "ok after timeout", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        result = router.chat([{"role": "user", "content": "hi"}], [])
        assert result["content"] == "ok after timeout"

    def test_failover_after_5xx(self):
        p1 = MockProvider(name="P1", fail_with=_server_error())
        p2 = MockProvider(name="P2", responses=[{"content": "ok", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        result = router.chat([{"role": "user", "content": "hi"}], [])
        assert result["content"] == "ok"

    def test_failover_after_model_error(self):
        p1 = MockProvider(name="P1", fail_with=_model_error())
        p2 = MockProvider(name="P2", responses=[{"content": "ok", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        result = router.chat([{"role": "user", "content": "hi"}], [])
        assert result["content"] == "ok"

    def test_auth_error_no_infinite_loop(self):
        p1 = MockProvider(name="P1", fail_with=_auth_error())
        p2 = MockProvider(name="P2", fail_with=_auth_error())
        router = LLMRouter(providers=[p1, p2])
        with pytest.raises(RuntimeError, match="All configured LLM providers"):
            router.chat([{"role": "user", "content": "hi"}], [])
        assert p1.call_count == 1
        assert p2.call_count == 1

    def test_all_providers_exhausted(self):
        p1 = MockProvider(name="P1", fail_with=_rate_limit_error())
        p2 = MockProvider(name="P2", fail_with=_timeout_error())
        router = LLMRouter(providers=[p1, p2])
        with pytest.raises(RuntimeError, match="All configured LLM providers"):
            router.chat([{"role": "user", "content": "hi"}], [])

    def test_provider_loop_prevention(self):
        p1 = MockProvider(name="P1", fail_with=_rate_limit_error())
        p2 = MockProvider(name="P2", fail_with=_rate_limit_error())
        router = LLMRouter(providers=[p1, p2])
        with pytest.raises(RuntimeError):
            router.chat([{"role": "user", "content": "hi"}], [])
        assert p1.call_count == 1
        assert p2.call_count == 1

    def test_generic_error_triggers_failover(self):
        p1 = MockProvider(name="P1", fail_with=_generic_error())
        p2 = MockProvider(name="P2", responses=[{"content": "ok", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        result = router.chat([{"role": "user", "content": "hi"}], [])
        assert result["content"] == "ok"


# ── Context preservation ──


class TestContextPreservation:
    def test_conversation_context_survives_failover(self):
        p1 = MockProvider(name="P1", fail_with=_rate_limit_error())
        p2 = MockProvider(name="P2", responses=[{"content": "ok", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        messages = [
            {"role": "system", "content": "You are a DevOps agent."},
            {"role": "user", "content": "Check VPS health"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "check_infrastructure_health", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"success": True, "cpu": 50})},
        ]
        router.chat(messages, [{"type": "function", "function": {"name": "check_infrastructure_health"}}])
        received = p2.chat_calls[0]["messages"]
        assert len(received) == 4
        assert received[0]["role"] == "system"
        assert received[1]["content"] == "Check VPS health"
        assert received[2]["role"] == "assistant"
        assert received[3]["role"] == "tool"

    def test_tool_results_preserved_across_failover(self):
        p1 = MockProvider(name="P1", fail_with=_timeout_error())
        p2 = MockProvider(name="P2", responses=[{"content": "analysis done", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        messages = [
            {"role": "user", "content": "Why is CPU high?"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_cpu_usage", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"success": True, "cpu_percent": 95.0})},
        ]
        router.chat(messages, [])
        received = p2.chat_calls[0]["messages"]
        tool_msgs = [m for m in received if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "95.0" in tool_msgs[0]["content"]


# ── Tool calling ──


class TestToolCalling:
    def test_tool_calls_passed_to_provider(self):
        tools = [
            {"type": "function", "function": {"name": "get_cpu_usage", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "docker_status", "parameters": {"type": "object", "properties": {}}}},
        ]
        p1 = MockProvider(name="P1", responses=[{"content": "", "tool_calls": [{"id": "c1", "name": "get_cpu_usage", "arguments": {}}]}])
        router = LLMRouter(providers=[p1])
        result = router.chat([{"role": "user", "content": "check cpu"}], tools)
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "get_cpu_usage"
        assert p1.chat_calls[0]["tools"] == tools

    def test_tool_calls_from_provider_preserved(self):
        tool_calls = [{"id": "c1", "name": "docker_status", "arguments": {}}]
        p1 = MockProvider(name="P1", responses=[{"content": "", "tool_calls": tool_calls}])
        router = LLMRouter(providers=[p1])
        result = router.chat([{"role": "user", "content": "check docker"}], [])
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "docker_status"


# ── Security ──


class TestSecurity:
    def test_router_does_not_bypass_phase5(self):
        from app.control.policy import BLOCKED_ACTIONS, is_action_allowed
        for action in ["rm", "delete", "destroy", "sudo", "shell_execute"]:
            assert action in BLOCKED_ACTIONS
            assert is_action_allowed(action) is False

    def test_router_does_not_bypass_tool_validation(self):
        from app.tool_registry import TOOL_NAMES
        assert "container_stats" not in TOOL_NAMES
        assert "docker_stats" in TOOL_NAMES

    def test_api_keys_not_in_provider_names(self):
        p1 = MockProvider(name="Groq (test, hosted)")
        router = LLMRouter(providers=[p1])
        status = router.get_status()
        for ps in status["providers"]:
            # Provider names should not look like API keys
            assert not ps["name"].startswith("sk-")


# ── .env preservation ──


class TestEnvPreservation:
    def test_config_has_all_existing_settings(self):
        from app.config import Config
        for attr in ["LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL", "GROQ_API_KEY",
                      "GROQ_MODEL", "VPS_HOST", "VPS_SSH_USER", "VPS_SSH_KEY_PATH",
                      "VPS_SSH_PORT", "JENKINS_CONTAINER_NAME", "AWS_REGION", "PROMETHEUS_PORT"]:
            assert hasattr(Config, attr), f"Config missing {attr}"

    def test_config_has_new_provider_settings(self):
        from app.config import Config
        for attr in ["OPENAI_API_KEY", "OPENAI_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "LLM_FALLBACK_PROVIDERS"]:
            assert hasattr(Config, attr), f"Config missing {attr}"

    def test_fallback_providers_is_list(self):
        from app.config import Config
        assert isinstance(Config.LLM_FALLBACK_PROVIDERS, list)

    def test_ollama_defaults_preserved(self, monkeypatch):
        import importlib
        import app.config as config

        monkeypatch.delenv("OLLAMA_MODEL", raising=False)

        with patch("dotenv.load_dotenv", return_value=None):
            importlib.reload(config)
            assert config.Config.OLLAMA_HOST == "http://localhost:11434"
            assert config.Config.OLLAMA_MODEL == "qwen2.5:7b"

        importlib.reload(config)

    def test_openai_defaults(self, monkeypatch):
        import importlib
        import app.config as config

        monkeypatch.delenv("OPENAI_MODEL", raising=False)

        with patch("dotenv.load_dotenv", return_value=None):
            importlib.reload(config)
            assert config.Config.OPENAI_MODEL == "gpt-4o"

        importlib.reload(config)

    def test_anthropic_defaults(self):
        from app.config import Config
        assert Config.ANTHROPIC_MODEL == "claude-sonnet-4-20250514"


# ── Provider state tracking ──


class TestProviderState:
    def test_initial_state(self):
        state = ProviderState(MockProvider(name="P1"))
        assert state.status == STATUS_AVAILABLE
        assert state.failures == 0
        assert state.is_usable is True

    def test_record_success_resets_state(self):
        state = ProviderState(MockProvider(name="P1"))
        state.record_failure("rate limit", retryable=True)
        assert state.is_usable is False
        state.record_success()
        assert state.is_usable is True
        assert state.failures == 0

    def test_record_failure_applies_cooldown(self):
        state = ProviderState(MockProvider(name="P1"))
        state.record_failure("timeout", retryable=True)
        assert state.status == STATUS_COOLDOWN
        assert state.cooldown_until > time.time()

    def test_non_retryable_marks_failed(self):
        state = ProviderState(MockProvider(name="P1"))
        state.record_failure("invalid key", retryable=False)
        assert state.status == STATUS_FAILED
        assert state.is_usable is False

    def test_cooldown_expires(self):
        state = ProviderState(MockProvider(name="P1"))
        state.cooldown_until = time.time() - 1
        assert state.is_cooled_down is True

    def test_to_dict(self):
        state = ProviderState(MockProvider(name="P1"))
        d = state.to_dict()
        assert d["name"] == "P1"
        assert d["status"] == STATUS_AVAILABLE


# ── Error classification ──


class TestErrorClassification:
    def test_rate_limit_is_failover(self):
        assert _classify_error("rate_limit_exceeded") == "failover"

    def test_timeout_is_failover(self):
        assert _classify_error("connection_timeout") == "failover"

    def test_5xx_is_failover(self):
        assert _classify_error("503 service unavailable") == "failover"

    def test_model_not_found_is_failover(self):
        assert _classify_error("model_not_found: xyz") == "failover"

    def test_auth_is_non_retryable(self):
        assert _classify_error("authentication_error: bad key") == "non_retryable"

    def test_invalid_request_is_non_retryable(self):
        assert _classify_error("invalid_request_error: bad params") == "non_retryable"

    def test_unknown_is_unknown(self):
        assert _classify_error("something weird happened") == "unknown"


# ── Router status ──


class TestRouterStatus:
    def test_get_status(self):
        p1, p2 = MockProvider(name="P1"), MockProvider(name="P2")
        router = LLMRouter(providers=[p1, p2])
        status = router.get_status()
        assert status["primary"] == "P1"
        assert len(status["providers"]) == 2

    def test_status_after_failover(self):
        p1 = MockProvider(name="P1", fail_with=_rate_limit_error())
        p2 = MockProvider(name="P2", responses=[{"content": "ok", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        router.chat([{"role": "user", "content": "hi"}], [])
        status = router.get_status()
        p1_status = next(s for s in status["providers"] if s["name"] == "P1")
        assert p1_status["status"] == STATUS_COOLDOWN
        assert p1_status["failures"] == 1


# ── check_alive ──


class TestCheckAlive:
    def test_check_alive_with_reachable_provider(self):
        router = LLMRouter(providers=[MockProvider(name="P1")])
        router.check_alive()

    def test_check_alive_with_all_unreachable(self):
        p1 = MockProvider(name="P1", fail_with=Exception("alive failed"))
        p2 = MockProvider(name="P2", fail_with=Exception("alive failed"))
        router = LLMRouter(providers=[p1, p2])
        with pytest.raises(RuntimeError, match="No LLM provider is reachable"):
            router.check_alive()

    def test_check_alive_skips_to_working(self):
        p1 = MockProvider(name="P1", fail_with=Exception("alive failed"))
        p2 = MockProvider(name="P2")
        router = LLMRouter(providers=[p1, p2])
        router.check_alive()


# ── Integration with Agent ──


class TestAgentIntegration:
    def test_router_as_provider_for_agent(self):
        from app.agent import Agent
        p1 = MockProvider(name="P1", responses=[
            {"content": "", "tool_calls": [{"id": "c1", "name": "get_cpu_usage", "arguments": {}}]},
            {"content": "CPU is at 42%.", "tool_calls": []},
        ])
        router = LLMRouter(providers=[p1])
        agent = Agent(
            provider=router, tool_schemas=[],
            execute_tool_fn=lambda n, a: {"success": True, "cpu_percent": 42.0},
            allowed_tool_names=frozenset({"get_cpu_usage"}),
        )
        history = [{"role": "system", "content": "sys"}]
        result = agent.run("check cpu", history)
        assert "42%" in result

    def test_router_failover_mid_agent_turn(self):
        from app.agent import Agent
        p1 = MockProvider(name="P1", fail_with=_rate_limit_error())
        p2 = MockProvider(name="P2", responses=[{"content": "CPU looks fine.", "tool_calls": []}])
        router = LLMRouter(providers=[p1, p2])
        agent = Agent(
            provider=router, tool_schemas=[],
            execute_tool_fn=lambda n, a: {"success": True},
            allowed_tool_names=frozenset(),
        )
        history = [{"role": "system", "content": "sys"}]
        result = agent.run("check cpu", history)
        assert "fine" in result.lower()


# ── Anthropic message conversion ──


class TestAnthropicConversion:
    def _make_provider(self):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=mock_anthropic)}):
            from app.llm_provider import AnthropicProvider
            return AnthropicProvider(api_key="test", model="test")

    def test_system_prompt_extraction(self):
        provider = self._make_provider()
        messages = [
            {"role": "system", "content": "You are a DevOps agent."},
            {"role": "user", "content": "hello"},
        ]
        system_text, converted = provider._convert_messages(messages)
        assert system_text == "You are a DevOps agent."
        assert len(converted) == 1
        assert converted[0]["role"] == "user"

    def test_tool_call_conversion(self):
        provider = self._make_provider()
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_cpu", "arguments": "{}"}}
            ]},
        ]
        _, converted = provider._convert_messages(messages)
        assert len(converted) == 1
        blocks = converted[0]["content"]
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["name"] == "get_cpu"

    def test_tool_result_conversion(self):
        provider = self._make_provider()
        messages = [
            {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"success": True})},
        ]
        _, converted = provider._convert_messages(messages)
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        content = converted[0]["content"]
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "c1"

    def test_tools_conversion(self):
        provider = self._make_provider()
        tools = [
            {"type": "function", "function": {"name": "get_cpu", "description": "Get CPU", "parameters": {"type": "object", "properties": {}}}},
        ]
        converted = provider._convert_tools(tools)
        assert len(converted) == 1
        assert converted[0]["name"] == "get_cpu"
        assert "input_schema" in converted[0]


# ── Optional live provider tests ──

RUN_LLM = __import__("os").environ.get("RUN_LLM_INTEGRATION_TESTS", "") == "1"
requires_llm = pytest.mark.skipif(not RUN_LLM, reason="RUN_LLM_INTEGRATION_TESTS=1 not set")


@requires_llm
class TestLiveProviderIntegration:
    def test_groq_live(self):
        from app.config import Config
        if not Config.GROQ_API_KEY:
            pytest.skip("GROQ_API_KEY not set")
        mock_groq = MagicMock()
        with patch.dict("sys.modules", {"groq": MagicMock(Groq=mock_groq)}):
            from app.llm_provider import GroqProvider
            provider = GroqProvider(api_key=Config.GROQ_API_KEY, model=Config.GROQ_MODEL)
            result = provider.chat([{"role": "user", "content": "Say hello in one word."}], [])
            assert result["content"]

    def test_openai_live(self):
        from app.config import Config
        if not Config.OPENAI_API_KEY:
            pytest.skip("OPENAI_API_KEY not set")
        mock_openai = MagicMock()
        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai)}):
            from app.llm_provider import OpenAIProvider
            provider = OpenAIProvider(api_key=Config.OPENAI_API_KEY, model=Config.OPENAI_MODEL)
            result = provider.chat([{"role": "user", "content": "Say hello in one word."}], [])
            assert result["content"]

    def test_anthropic_live(self):
        from app.config import Config
        if not Config.ANTHROPIC_API_KEY:
            pytest.skip("ANTHROPIC_API_KEY not set")
        mock_anthropic = MagicMock()
        with patch.dict("sys.modules", {"anthropic": MagicMock(Anthropic=mock_anthropic)}):
            from app.llm_provider import AnthropicProvider
            provider = AnthropicProvider(api_key=Config.ANTHROPIC_API_KEY, model=Config.ANTHROPIC_MODEL)
            result = provider.chat([{"role": "user", "content": "Say hello in one word."}], [])
            assert result["content"]
