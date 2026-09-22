"""Integration tests: personality wiring through the Agent class."""
from unittest.mock import MagicMock

from app.agent import Agent
from app.personality.state import ConversationState, reset_conversation_state


def _make_agent(state=None, preferences=None):
    """Agent with mocked LLM provider and tool executor."""
    provider = MagicMock()
    provider.name = "mock"
    # The agent loop reads result["content"] and result["tool_calls"] —
    # match the real provider contract.
    provider.chat.return_value = {"role": "assistant", "content": "Yep. Docker's up.", "tool_calls": []}
    executed = []

    def execute_tool(name, args, **kwargs):
        executed.append((name, args))
        return {"status": "ok", "summary": "5 containers running"}

    agent = Agent(
        provider=provider,
        tool_schemas=[{"type": "function", "function": {"name": "docker_ps",
                       "description": "list", "parameters": {"type": "object", "properties": {}}}}],
        execute_tool_fn=execute_tool,
        allowed_tool_names={"docker_ps"},
        conversation_state=state,
        preference_loader=(lambda: preferences) if preferences is not None else None,
    )
    return agent, provider, executed


def _fresh_history():
    return [{"role": "system", "content": "base system prompt"}]


class TestAgentPersonalityWiring:
    def test_state_observes_user_input(self):
        state = ConversationState()
        agent, provider, _ = _make_agent(state=state)
        agent.run("check the redis container", history=_fresh_history())
        assert state.last_subject == "redis"

    def test_system_prompt_rebuilt_with_personality(self):
        agent, provider, _ = _make_agent(state=ConversationState())
        agent.run("hello", history=_fresh_history())
        sent = provider.chat.call_args
        # First message is the system prompt; must contain personality.
        first = sent[0][0][0]["content"] if sent and sent[0] else ""
        assert "How you communicate" in first

    def test_state_observes_tool_results(self):
        state = ConversationState()
        agent, provider, executed = _make_agent(state=state)
        # Force the tool path: provider asks for a tool call, then finishes.
        provider.chat.side_effect = [
            {"role": "assistant", "content": None,
             "tool_calls": [{"name": "docker_ps", "arguments": {}}]},
            {"role": "assistant", "content": "5 running.", "tool_calls": []},
        ]
        agent.run("check docker", history=_fresh_history())
        assert executed and executed[0][0] == "docker_ps"
        assert any("docker_ps" in f for f in state.recent_findings)

    def test_preference_loader_feeds_prompt(self):
        agent, provider, _ = _make_agent(
            preferences={"response_style": "very short"})
        agent.run("hello", history=_fresh_history())
        first = provider.chat.call_args[0][0][0]["content"]
        assert "very short" in first

    def test_preference_loader_failure_degrades_gracefully(self):
        def boom():
            raise RuntimeError("db unavailable")

        provider = MagicMock()
        provider.name = "mock"
        provider.chat.return_value = {"role": "assistant", "content": "ok", "tool_calls": []}
        agent = Agent(provider=provider, tool_schemas=[], execute_tool_fn=lambda n, a: {},
                      allowed_tool_names=set(), conversation_state=ConversationState(),
                      preference_loader=boom)
        # Must not raise.
        answer = agent.run("hello", history=_fresh_history())
        assert answer == "ok"


class TestAgentBackwardCompat:
    def test_agent_works_without_state(self):
        """Existing callers that pass no conversation_state keep working."""
        agent, provider, _ = _make_agent(state=None)
        answer = agent.run("hello", history=_fresh_history())
        assert answer == "Yep. Docker's up."

    def test_agent_works_without_preference_loader(self):
        agent, _, _ = _make_agent()
        answer = agent.run("what's the status", history=_fresh_history())
        assert answer == "Yep. Docker's up."
