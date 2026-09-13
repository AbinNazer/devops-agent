"""
Tests for app/control/rollback.py — rollback planning and execution.

Verifies that rollback plans are correctly created for different action
types, that rollback execution works, and that non-rollbackable actions
are properly handled.
"""
from unittest.mock import patch

import pytest

from app.control.rollback import (
    create_rollback_plan, execute_rollback, is_rollback_safe,
    RollbackPlan, RollbackResult,
)


# --- Rollback plan creation ---

def test_rollback_plan_for_restart_container():
    """Restart container should have a rollback plan."""
    plan = create_rollback_plan("restart_container", "backend")
    assert plan.available is True
    assert plan.target == "backend"
    assert plan.command == "docker restart backend"
    assert plan.risk_level == "low"


def test_rollback_plan_for_restart_service():
    """Restart service should have a rollback plan."""
    plan = create_rollback_plan("restart_service", "nginx")
    assert plan.available is True
    assert plan.target == "nginx"
    assert plan.command == "systemctl restart nginx"


def test_rollback_plan_for_unknown_action():
    """Unknown actions should not have rollback plans."""
    plan = create_rollback_plan("delete_resource", "database")
    assert plan.available is False
    assert "not available" in plan.reason.lower()


def test_rollback_plan_has_conditions():
    """Rollback plan should have conditions for when to rollback."""
    plan = create_rollback_plan("restart_container", "backend")
    assert len(plan.conditions) > 0
    assert any("verification" in c.lower() or "failed" in c.lower() for c in plan.conditions)


# --- Rollback plan serialization ---

def test_rollback_plan_to_dict():
    """RollbackPlan.to_dict should serialize properly."""
    plan = create_rollback_plan("restart_container", "backend")
    d = plan.to_dict()
    assert d["available"] is True
    assert d["target"] == "backend"
    assert "conditions" in d


# --- Rollback safety ---

def test_rollback_safe_for_low_risk():
    """Low-risk rollbacks should be considered safe."""
    plan = create_rollback_plan("restart_container", "backend")
    assert is_rollback_safe(plan) is True


def test_rollback_not_safe_when_unavailable():
    """Unavailable rollbacks should not be safe."""
    plan = create_rollback_plan("delete_resource", "database")
    assert is_rollback_safe(plan) is False


# --- Rollback execution ---

def test_execute_rollback_success():
    """Executing a rollback should succeed when the command succeeds."""
    plan = create_rollback_plan("restart_container", "backend")
    mock_result = {"success": True, "stdout": "", "stderr": "", "exit_code": 0}

    with patch("app.control.rollback.run_whitelisted_command", return_value=mock_result):
        result = execute_rollback(plan)

    assert result.success is True
    assert result.action_type == "restart_container"
    assert result.target == "backend"


def test_execute_rollback_failure():
    """Executing a rollback should fail when the command fails."""
    plan = create_rollback_plan("restart_container", "backend")
    mock_result = {"success": False, "error": "Connection refused"}

    with patch("app.control.rollback.run_whitelisted_command", return_value=mock_result):
        result = execute_rollback(plan)

    assert result.success is False
    assert "Connection refused" in result.error


def test_execute_rollback_unavailable():
    """Executing an unavailable rollback should fail."""
    plan = create_rollback_plan("delete_resource", "database")
    result = execute_rollback(plan)

    assert result.success is False
    assert "not available" in result.error.lower()


# --- Rollback result ---

def test_rollback_result_to_dict():
    """RollbackResult.to_dict should serialize properly."""
    result = RollbackResult(
        success=True, action_type="restart_container", target="backend",
        evidence=["Rollback command executed"],
    )
    d = result.to_dict()
    assert d["success"] is True
    assert d["action_type"] == "restart_container"
    assert len(d["evidence"]) == 1


def test_rollback_result_with_error():
    """RollbackResult should include error when present."""
    result = RollbackResult(
        success=False, error="Connection refused",
    )
    d = result.to_dict()
    assert d["error"] == "Connection refused"
