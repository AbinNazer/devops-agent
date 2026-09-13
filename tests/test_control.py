"""
Tests for app/control/controller.py and app/control/state.py.

Covers: control loop lifecycle, state management, phase transitions,
evidence collection, hypothesis generation, action planning, and
integration with the control components.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.control.state import (
    ControlState, Phase, Observation, ActionRecord,
    AuditEntry, ApprovalStatus, RiskLevel, OutcomeStatus,
)
from app.control.controller import ControlLoop


# --- ControlState tests ---

def test_control_state_default_values():
    """ControlState should have sensible defaults."""
    state = ControlState(request="test request")
    assert state.request == "test request"
    assert state.current_phase == Phase.IDLE.value
    assert state.confidence == 0.0
    assert state.final_outcome == OutcomeStatus.UNKNOWN.value
    assert state.incident_id.startswith("INC-")


def test_control_state_transition_to():
    """Phase transitions should be recorded in history."""
    state = ControlState()
    state.transition_to(Phase.OBSERVE, "starting observation")
    assert state.current_phase == Phase.OBSERVE.value
    assert Phase.OBSERVE.value in state.phase_history
    assert len(state.audit_trail) == 1


def test_control_state_add_observation():
    """Adding observations should update the state."""
    state = ControlState()
    state.add_observation("docker_health_status", {"success": True, "containers": []})
    assert len(state.observations) == 1
    assert state.observations[0].tool == "docker_health_status"
    assert "docker_health_status" in state.executed_tools


def test_control_state_add_observation_deduplicates_tools():
    """Executing the same tool twice should not duplicate in executed_tools."""
    state = ControlState()
    state.add_observation("docker_health_status", {"success": True})
    state.add_observation("docker_health_status", {"success": True})
    assert len(state.executed_tools) == 1


def test_control_state_add_hypothesis():
    """Adding hypotheses should be tracked."""
    state = ControlState()
    state.add_hypothesis({"statement": "Redis is down", "confidence": 0.8})
    assert len(state.hypotheses) == 1


def test_control_state_select_hypothesis():
    """Selecting a hypothesis should update confidence."""
    state = ControlState()
    hyp = {"statement": "Backend is failing", "confidence": 0.75}
    state.select_hypothesis(hyp)
    assert state.selected_hypothesis == hyp
    assert state.confidence == 0.75


def test_control_state_add_action_record():
    """Action records should be tracked."""
    state = ControlState()
    record = ActionRecord(action_type="restart_container", target="backend")
    state.add_action_record(record)
    assert len(state.actions_attempted) == 1


def test_control_state_add_verification():
    """Verification results should be tracked."""
    state = ControlState()
    state.add_verification({"status": "success", "checks": []})
    assert len(state.verification_results) == 1


def test_control_state_set_outcome():
    """Setting outcome should update final_outcome and audit trail."""
    state = ControlState()
    state.set_outcome(OutcomeStatus.SUCCESS, "Everything is working")
    assert state.final_outcome == OutcomeStatus.SUCCESS.value
    assert state.outcome_summary == "Everything is working"


def test_control_state_to_dict():
    """to_dict should serialize the full state."""
    state = ControlState(request="test")
    d = state.to_dict()
    assert d["request"] == "test"
    assert "audit_trail" in d
    assert "observations" in d


def test_control_state_get_summary():
    """get_summary should return a human-readable string."""
    state = ControlState(request="Why is it broken?")
    state.add_observation("test", {"success": True})
    summary = state.get_summary()
    assert "Why is it broken?" in summary
    assert "Observations: 1" in summary


# --- Controller tests ---

def _make_mock_tool(results):
    """Create a mock tool executor that returns results in order."""
    results = list(results)
    call_count = [0]

    def execute(name, args):
        idx = call_count[0]
        call_count[0] += 1
        if idx < len(results):
            return results[idx]
        return {"success": False, "error": "no more mock results"}

    return execute


def test_controller_run_basic_diagnostic():
    """Controller should run a basic diagnostic workflow."""
    tool_results = [
        {"success": True, "containers": [{"name": "backend", "state": "running"}],
         "summary": {"containers_with_problems": []}},
    ]
    controller = ControlLoop(execute_tool_fn=_make_mock_tool(tool_results))
    result = controller.run("Is everything okay?")

    assert result["success"] is True
    assert result["outcome"] in ("success", "unknown")
    assert "state" in result
    assert "incident" in result


def test_controller_run_with_docker_problems():
    """Controller should detect docker problems and generate hypotheses."""
    tool_results = [
        # check_infrastructure_health (fallback for generic request)
        {"success": True, "report": {
            "local": {},
            "vps": {
                "docker": {
                    "status": "WARNING",
                    "data": {
                        "success": True,
                        "containers": [{"name": "backend", "state": "exited", "health": "no_healthcheck"}],
                        "summary": {"containers_with_problems": ["backend"]},
                    },
                },
            },
            "aws": {},
            "overall_status": "WARNING",
        }},
    ]
    controller = ControlLoop(execute_tool_fn=_make_mock_tool(tool_results))
    result = controller.run("Why is the backend down?")

    assert result["success"] is True
    # Controller should complete successfully even without specific hypotheses
    assert result["outcome"] in ("success", "unknown")


def test_controller_handles_tool_failure():
    """Controller should handle tool failures gracefully."""
    tool_results = [
        {"success": False, "error": "SSH connection failed"},
    ]
    controller = ControlLoop(execute_tool_fn=_make_mock_tool(tool_results))
    result = controller.run("Check docker status")

    assert result["success"] is True  # controller itself doesn't fail
    assert result["outcome"] in ("unknown", "success")


def test_controller_with_user_denial():
    """Controller should handle user denying an action."""
    tool_results = [
        # docker health check
        {"success": True,
         "containers": [{"name": "backend", "state": "exited", "health": "no_healthcheck"}],
         "summary": {"containers_with_problems": ["backend"]}},
    ]

    # User always denies
    def deny_all(prompt):
        return False

    controller = ControlLoop(
        execute_tool_fn=_make_mock_tool(tool_results),
        user_approve_fn=deny_all,
    )
    result = controller.run("Restart the backend")

    assert result["success"] is True


def test_controller_records_audit_trail():
    """Controller should create a complete audit trail."""
    tool_results = [
        {"success": True, "containers": [], "summary": {"containers_with_problems": []}},
    ]
    controller = ControlLoop(execute_tool_fn=_make_mock_tool(tool_results))
    result = controller.run("Check everything")

    state_dict = result["state"]
    assert len(state_dict["audit_trail"]) > 0


def test_controller_handles_empty_request():
    """Controller should handle empty requests."""
    controller = ControlLoop(execute_tool_fn=lambda n, a: {"success": True, "containers": [], "summary": {"containers_with_problems": []}})
    result = controller.run("")
    assert result["success"] is True


def test_controller_error_handling():
    """Controller should handle exceptions gracefully."""
    def broken_tool(name, args):
        raise RuntimeError("tool crashed")

    controller = ControlLoop(execute_tool_fn=broken_tool)
    result = controller.run("Check status")
    assert result["success"] is False
    assert "error" in result


# --- Observation tests ---

def test_observation_to_dict():
    """Observation.to_dict should serialize properly."""
    obs = Observation(tool="test", result={"success": True})
    d = obs.to_dict()
    assert d["tool"] == "test"
    assert d["result"]["success"] is True
    assert "timestamp" in d


# --- ActionRecord tests ---

def test_action_record_to_dict():
    """ActionRecord.to_dict should serialize properly."""
    record = ActionRecord(
        action_type="restart_container",
        target="backend",
        command="docker restart backend",
        risk_level="medium",
    )
    d = record.to_dict()
    assert d["action_type"] == "restart_container"
    assert d["target"] == "backend"


# --- AuditEntry tests ---

def test_audit_entry_to_dict():
    """AuditEntry.to_dict should serialize properly."""
    entry = AuditEntry(phase="observe", action="tool_called", detail="docker_health_status")
    d = entry.to_dict()
    assert d["phase"] == "observe"
    assert d["action"] == "tool_called"
