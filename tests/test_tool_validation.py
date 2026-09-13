"""
Regression tests for tool-name validation.

Proves that:
- The LLM cannot request a tool that isn't in TOOL_NAMES
- Every tool advertised in TOOL_SCHEMAS is present in TOOL_NAMES and _DISPATCH
- Hallucinated names (e.g. "container_stats") are rejected before execution
- All existing registered tools remain callable
"""
import pytest

from app.tool_registry import TOOL_NAMES, TOOL_SCHEMAS, _DISPATCH, execute_tool
from app.agent import Agent


# ── Helpers ──


class _FakeProvider:
    """Deterministic fake LLM provider for testing tool-call validation."""

    def __init__(self, tool_calls_to_return):
        """
        tool_calls_to_return: list of dicts, each with "name" and optional "arguments".
        The provider returns these as tool calls on the FIRST chat call,
        then returns plain text on subsequent calls.
        """
        self._tool_calls = list(tool_calls_to_return)
        self._call_count = 0

    def chat(self, history, tool_schemas):
        self._call_count += 1
        if self._call_count == 1 and self._tool_calls:
            tc_list = self._tool_calls
            self._tool_calls = []  # only first call returns tool calls
            return {"content": "", "tool_calls": tc_list}
        return {"content": "Done.", "tool_calls": []}

    def assistant_message(self, content, tool_calls):
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    def tool_result_message(self, tool_call, result):
        return {
            "role": "tool",
            "tool_call_id": tool_call.get("id", "call_0"),
            "content": str(result),
        }

    @property
    def name(self):
        return "fake"


# ── TOOL_NAMES consistency ──


class TestToolNamesConsistency:
    """Every advertised tool must be in TOOL_NAMES, and every TOOL_NAMES entry
    must appear in both TOOL_SCHEMAS and _DISPATCH."""

    def test_all_schema_names_in_tool_names(self):
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        missing = schema_names - TOOL_NAMES
        assert not missing, f"Schemas advertise tools missing from TOOL_NAMES: {missing}"

    def test_all_tool_names_in_schemas(self):
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        extra = TOOL_NAMES - schema_names
        assert not extra, f"TOOL_NAMES contains tools not in TOOL_SCHEMAS: {extra}"

    def test_all_tool_names_in_dispatch(self):
        missing = TOOL_NAMES - set(_DISPATCH.keys())
        assert not missing, f"TOOL_NAMES entries missing from _DISPATCH: {missing}"

    def test_all_dispatch_keys_in_tool_names(self):
        extra = set(_DISPATCH.keys()) - TOOL_NAMES
        assert not extra, f"_DISPATCH has keys not in TOOL_NAMES: {extra}"

    def test_tool_names_is_frozen(self):
        assert isinstance(TOOL_NAMES, frozenset)

    def test_no_container_stats_in_any_register(self):
        """The hallucinated name 'container_stats' must not exist anywhere."""
        assert "container_stats" not in TOOL_NAMES
        assert "container_stats" not in _DISPATCH
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "container_stats" not in schema_names

    def test_docker_stats_exists(self):
        """The correct tool name 'docker_stats' must exist everywhere."""
        assert "docker_stats" in TOOL_NAMES
        assert "docker_stats" in _DISPATCH
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "docker_stats" in schema_names


# ── Agent rejection of unregistered tools ──


class TestAgentRejectsUnregisteredTools:
    """The Agent must never execute a tool whose name is not in allowed_tool_names."""

    def _make_agent(self, allowed_names=None, tool_schemas=None):
        provider = _FakeProvider([])
        return Agent(
            provider=provider,
            tool_schemas=tool_schemas or [],
            execute_tool_fn=lambda n, a: {"success": True, "called": True},
            allowed_tool_names=allowed_names,
        )

    def test_hallucinated_tool_rejected(self):
        """Agent rejects 'container_stats' when only 'docker_stats' is allowed."""
        agent = self._make_agent(allowed_names=frozenset({"docker_stats"}))
        # Simulate the LLM requesting the hallucinated tool
        history = []
        provider = _FakeProvider([{"name": "container_stats", "arguments": {}}])
        agent.provider = provider
        agent.allowed_tool_names = frozenset({"docker_stats"})

        result = agent.run("show me container stats", history)

        # The tool should NOT have been executed
        # Check that the history contains an error message about the unknown tool
        tool_results = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_results) >= 1
        assert "not a registered tool" in tool_results[0]["content"]
        assert "container_stats" in tool_results[0]["content"]

    def test_registered_tool_not_rejected(self):
        """Agent allows 'docker_stats' when it's in allowed_tool_names."""
        agent = self._make_agent(allowed_names=frozenset({"docker_stats"}))
        history = []
        provider = _FakeProvider([{"name": "docker_stats", "arguments": {}}])
        agent.provider = provider

        result = agent.run("show me docker stats", history)

        # The tool should have been executed (success response in history)
        tool_results = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_results) >= 1
        assert "not a registered tool" not in tool_results[0]["content"]

    def test_no_validation_when_allowed_tool_names_is_none(self):
        """When allowed_tool_names is None, the Agent does not validate (backward compat)."""
        agent = self._make_agent(allowed_names=None)
        history = []
        provider = _FakeProvider([{"name": "anything_goes", "arguments": {}}])
        agent.provider = provider

        result = agent.run("do something", history)

        # Tool was executed (no rejection), execute_tool_fn returns success
        tool_results = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_results) >= 1
        assert "not a registered tool" not in tool_results[0]["content"]

    def test_multiple_hallucinated_tools_rejected(self):
        """Multiple hallucinated tools in one turn are all rejected."""
        agent = self._make_agent(allowed_names=frozenset({"docker_stats", "docker_logs"}))
        history = []
        provider = _FakeProvider([
            {"name": "container_stats", "arguments": {}},
            {"name": "container_logs", "arguments": {}},
        ])
        agent.provider = provider

        result = agent.run("check containers", history)

        tool_results = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_results) == 2
        for tr in tool_results:
            assert "not a registered tool" in tr["content"]

    def test_mixed_valid_and_invalid_tools(self):
        """Valid tool is executed, invalid tool is rejected, in the same turn."""
        call_log = []

        def track_execute(name, args):
            call_log.append(name)
            return {"success": True}

        agent = self._make_agent(allowed_names=frozenset({"docker_stats"}))
        agent.execute_tool_fn = track_execute
        history = []
        provider = _FakeProvider([
            {"name": "docker_stats", "arguments": {}},
            {"name": "container_stats", "arguments": {}},
        ])
        agent.provider = provider

        result = agent.run("show stats", history)

        # Only docker_stats should have been executed
        assert call_log == ["docker_stats"]
        tool_results = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_results) == 2
        # First result is success (from execute_tool_fn)
        assert "not a registered tool" not in tool_results[0]["content"]
        # Second result is rejection
        assert "not a registered tool" in tool_results[1]["content"]

    def test_error_message_lists_available_tools(self):
        """The rejection error should help the LLM by listing available tools."""
        agent = self._make_agent(allowed_names=frozenset({"docker_stats", "docker_logs"}))
        history = []
        provider = _FakeProvider([{"name": "container_stats", "arguments": {}}])
        agent.provider = provider

        result = agent.run("check containers", history)

        tool_results = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
        assert len(tool_results) >= 1
        error_content = tool_results[0]["content"]
        assert "docker_logs" in error_content
        assert "docker_stats" in error_content


# ── Execute_tool rejection ──


class TestExecuteToolRejection:
    """The execute_tool function must reject unknown tool names at the dispatch level."""

    def test_unknown_tool_returns_error(self):
        result = execute_tool("container_stats", {})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_registered_tool_dispatches(self):
        # docker_stats is registered; calling it with a mock won't actually SSH
        # but we can verify it's not "Unknown tool"
        result = execute_tool("nonexistent_xyz", {})
        assert result["success"] is False
        assert "Unknown tool" in result["error"]

    def test_empty_name_returns_error(self):
        result = execute_tool("", {})
        assert result["success"] is False
