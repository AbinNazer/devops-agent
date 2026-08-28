"""
Agent loop tests. Uses a FakeProvider that returns scripted responses
instead of calling a real LLM — this tests the Agent's control flow
(does it call the right tool, does it stop when it should, does it
handle failures) independent of any actual model's behavior.
"""
import pytest
from app.agent import Agent


class FakeProvider:
    """Scripted provider: returns each item in `script` in order, one per chat() call."""
    def __init__(self, script):
        self.script = list(script)
        self.name = "FakeProvider"
        self.calls = []

    def chat(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self.script:
            raise RuntimeError("FakeProvider script exhausted — agent called chat() more times than expected")
        return self.script.pop(0)

    def assistant_message(self, content, tool_calls):
        return {"role": "assistant", "content": content, "tool_calls": tool_calls}

    def tool_result_message(self, tool_call, result):
        return {"role": "tool", "name": tool_call["name"], "content": result}


def make_tool_call(name, arguments=None, call_id="call_1"):
    return {"id": call_id, "name": name, "arguments": arguments or {}}


def test_agent_answers_directly_with_no_tool_needed():
    provider = FakeProvider([
        {"content": "Hello! How can I help?", "tool_calls": []},
    ])
    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {"success": True})

    history = [{"role": "system", "content": "sys"}]
    answer = agent.run("hey", history)

    assert answer == "Hello! How can I help?"
    assert len(provider.calls) == 1


def test_agent_calls_single_tool_then_answers():
    provider = FakeProvider([
        {"content": "", "tool_calls": [make_tool_call("get_cpu_usage")]},
        {"content": "CPU is at 42%.", "tool_calls": []},
    ])
    executed = []

    def fake_execute(name, args):
        executed.append((name, args))
        return {"success": True, "cpu_percent": 42.0}

    agent = Agent(provider, tool_schemas=[], execute_tool_fn=fake_execute)
    history = [{"role": "system", "content": "sys"}]
    answer = agent.run("is my server healthy?", history)

    assert answer == "CPU is at 42%."
    assert executed == [("get_cpu_usage", {})]
    assert len(provider.calls) == 2


def test_agent_calls_multiple_sequential_tools():
    provider = FakeProvider([
        {"content": "", "tool_calls": [make_tool_call("get_cpu_usage")]},
        {"content": "", "tool_calls": [make_tool_call("get_memory_usage")]},
        {"content": "", "tool_calls": [make_tool_call("docker_status")]},
        {"content": "Everything looks healthy.", "tool_calls": []},
    ])
    executed_names = []

    def fake_execute(name, args):
        executed_names.append(name)
        return {"success": True}

    agent = Agent(provider, tool_schemas=[], execute_tool_fn=fake_execute)
    history = [{"role": "system", "content": "sys"}]
    answer = agent.run("check my server", history)

    assert answer == "Everything looks healthy."
    assert executed_names == ["get_cpu_usage", "get_memory_usage", "docker_status"]


def test_agent_passes_tool_arguments_through():
    provider = FakeProvider([
        {"content": "", "tool_calls": [make_tool_call("docker_logs", {"container_name": "backend"})]},
        {"content": "Backend shows Redis timeouts.", "tool_calls": []},
    ])
    seen_args = {}

    def fake_execute(name, args):
        seen_args.update(args)
        return {"success": True, "logs": "ERROR Redis connection timeout"}

    agent = Agent(provider, tool_schemas=[], execute_tool_fn=fake_execute)
    history = [{"role": "system", "content": "sys"}]
    agent.run("why is my backend slow?", history)

    assert seen_args == {"container_name": "backend"}


def test_agent_handles_tool_failure_without_crashing():
    provider = FakeProvider([
        {"content": "", "tool_calls": [make_tool_call("docker_status")]},
        {"content": "I couldn't retrieve Docker status.", "tool_calls": []},
    ])

    def failing_execute(name, args):
        return {"success": False, "error": "Unable to retrieve Docker status"}

    agent = Agent(provider, tool_schemas=[], execute_tool_fn=failing_execute)
    history = [{"role": "system", "content": "sys"}]
    answer = agent.run("check docker", history)

    assert "couldn't retrieve" in answer.lower()
    # confirm the failure was actually communicated back to the "model"
    tool_msgs = [m for m in history if m.get("role") == "tool"]
    assert tool_msgs[0]["content"]["success"] is False


def test_agent_stops_at_max_iterations_instead_of_looping_forever():
    # script an LLM that ALWAYS wants another tool call, never stops
    infinite_script = [
        {"content": "", "tool_calls": [make_tool_call("get_cpu_usage")]}
        for _ in range(20)
    ]
    provider = FakeProvider(infinite_script)
    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {"success": True})

    history = [{"role": "system", "content": "sys"}]
    answer = agent.run("keep going", history)

    assert len(provider.calls) <= 8  # MAX_TOOL_ITERATIONS safety valve
    assert isinstance(answer, str)


def test_agent_appends_user_message_to_history():
    provider = FakeProvider([{"content": "hi", "tool_calls": []}])
    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {})
    history = [{"role": "system", "content": "sys"}]

    agent.run("hello there", history)

    assert {"role": "user", "content": "hello there"} in history


def test_agent_raises_if_provider_exhausted_unexpectedly():
    # sanity check on the test harness itself: if the agent asks for more
    # chat() calls than scripted, that's a real bug we want surfaced, not hidden
    provider = FakeProvider([])
    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {})
    history = [{"role": "system", "content": "sys"}]

    with pytest.raises(RuntimeError):
        agent.run("anything", history)


def test_agent_invokes_on_tool_call_callback():
    provider = FakeProvider([
        {"content": "", "tool_calls": [make_tool_call("get_cpu_usage", {"x": 1})]},
        {"content": "done", "tool_calls": []},
    ])
    seen = []

    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {"success": True})
    history = [{"role": "system", "content": "sys"}]
    agent.run("go", history, on_tool_call=lambda name, args: seen.append((name, args)))

    assert seen == [("get_cpu_usage", {"x": 1})]


def test_agent_works_without_on_tool_call_callback():
    # on_tool_call is optional — omitting it should not break anything
    provider = FakeProvider([
        {"content": "", "tool_calls": [make_tool_call("get_cpu_usage")]},
        {"content": "done", "tool_calls": []},
    ])
    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {"success": True})
    history = [{"role": "system", "content": "sys"}]
    answer = agent.run("go", history)
    assert answer == "done"

def test_agent_handles_explicit_memory_command_without_provider():
    provider = FakeProvider([])
    agent = Agent(provider, tool_schemas=[], execute_tool_fn=lambda n, a: {}, memory_command_handler=lambda text: "Remembered it." if text.startswith("Remember") else None)
    history = [{"role": "system", "content": "sys"}]
    assert agent.run("Remember that ERP depends on Redis.", history) == "Remembered it."
    assert provider.calls == []
