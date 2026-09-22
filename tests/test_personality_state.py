"""Tests for conversation state tracking and referent resolution support."""
from app.personality.state import (
    ConversationState, get_conversation_state, reset_conversation_state,
)


class TestConversationState:
    def test_observe_detects_component(self):
        s = ConversationState()
        s.observe("check the redis container")
        assert s.last_subject == "redis"

    def test_observe_sets_intent(self):
        s = ConversationState()
        s.observe("why is billing crashing?")
        assert "billing" in s.recent_user_intent

    def test_observe_tool_result_records_findings(self):
        s = ConversationState()
        s.observe_tool_result("docker_ps", "5 running containers")
        assert any("docker_ps" in f for f in s.recent_findings)

    def test_observe_tool_result_records_actions(self):
        s = ConversationState()
        s.observe_tool_result("run_diagnostic", "restart proposed for redis")
        assert any("run_diagnostic" in a for a in s.recent_actions)

    def test_findings_bounded_to_five(self):
        s = ConversationState()
        for i in range(10):
            s.observe_tool_result(f"tool_{i}", "result")
        assert len(s.recent_findings) == 5

    def test_pending_question(self):
        s = ConversationState()
        s.set_pending_question("which container?")
        assert s.pending_question == "which container?"

    def test_summary_only_includes_set_fields(self):
        s = ConversationState()
        assert s.summary() == {}
        s.observe("restart redis please")
        snap = s.summary()
        assert "last_subject" in snap
        assert "current_incident_id" not in snap

    def test_summary_bounds_lists(self):
        s = ConversationState()
        for i in range(8):
            s.observe_tool_result(f"t{i}", "x")
        snap = s.summary()
        assert snap["recent_findings"].count("|") == 2  # last 3 joined


class TestReferentResolution:
    def test_it_refers_to_last_subject_via_summary(self):
        """The "restart it" flow: state must surface the prior subject so the
        LLM can resolve "it" without the user repeating the name."""
        s = ConversationState()
        s.observe("check the billing container")
        s.observe_tool_result("docker_ps", "billing is unhealthy")
        snap = s.summary()
        # The directive includes the state so the follow-up has a referent.
        assert snap.get("last_subject") == "billing"
        assert "billing is unhealthy" in snap.get("recent_findings", "")

    def test_state_registry_per_conversation(self):
        reset_conversation_state("conv-a")
        reset_conversation_state("conv-b")
        a = get_conversation_state("conv-a")
        b = get_conversation_state("conv-b")
        a.observe("check nginx")
        b.observe("check jenkins")
        assert a.last_subject == "nginx"
        assert b.last_subject == "jenkins"
        reset_conversation_state("conv-a")
        reset_conversation_state("conv-b")

    def test_registry_returns_same_instance(self):
        reset_conversation_state("conv-c")
        s1 = get_conversation_state("conv-c")
        s2 = get_conversation_state("conv-c")
        assert s1 is s2
        reset_conversation_state("conv-c")

    def test_empty_input_is_ignored(self):
        s = ConversationState()
        s.observe("   ")
        assert s.recent_user_intent is None
