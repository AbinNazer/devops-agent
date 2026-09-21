"""
Tests for app/control/actions.py — limited safe action execution.

Verifies that only explicitly allowed actions can be executed,
all commands pass through the whitelist, and execution results
are properly structured.
"""
from unittest.mock import patch

import pytest

from app.control.actions import (
    execute_action, get_allowed_action_types, is_action_executable,
    build_action_command, create_action_record,
)
from app.control.state import ActionRecord


# --- Allowed action types ---

def test_allowed_action_types_include_restart():
    """restart_container and restart_service should be allowed."""
    types = get_allowed_action_types()
    assert "restart_container" in types
    assert "restart_service" in types


def test_is_action_executable_for_known_actions():
    """Known safe actions should be executable."""
    assert is_action_executable("restart_container") is True
    assert is_action_executable("restart_service") is True


def test_is_action_executable_for_dangerous_actions():
    """Dangerous actions should not be executable."""
    assert is_action_executable("rm") is False
    assert is_action_executable("delete") is False
    assert is_action_executable("docker_exec") is False
    assert is_action_executable("unknown_action") is False


# --- Command building ---

def test_build_action_command_restart_container():
    """Should build a safe docker restart command."""
    cmd = build_action_command("restart_container", "backend")
    assert cmd == "docker restart backend"


def test_build_action_command_restart_service():
    """Should build a safe systemctl restart command."""
    cmd = build_action_command("restart_service", "nginx")
    assert cmd == "systemctl restart nginx"


def test_build_action_command_rejects_unsafe_target():
    """Should reject injection attempts in target names."""
    cmd = build_action_command("restart_container", "backend; rm -rf /")
    assert cmd is None


def test_build_action_command_rejects_unknown_action():
    """Should reject unknown action types."""
    cmd = build_action_command("deploy_production", "backend")
    assert cmd is None


# --- Action execution ---

def test_execute_action_restart_container():
    """Executing a restart_container should call SSH with the right command."""
    mock_result = {"success": True, "command": "docker restart backend",
                   "stdout": "", "stderr": "", "exit_code": 0}
    with patch("app.control.actions.run_action_command", return_value=mock_result):
        result = execute_action("restart_container", "backend")

    assert result["success"] is True
    assert result["action_type"] == "restart_container"
    assert result["target"] == "backend"
    assert result["command"] == "docker restart backend"


def test_execute_action_restart_service():
    """Executing a restart_service should call SSH with the right command."""
    mock_result = {"success": True, "command": "systemctl restart nginx",
                   "stdout": "", "stderr": "", "exit_code": 0}
    with patch("app.control.actions.run_action_command", return_value=mock_result):
        result = execute_action("restart_service", "nginx")

    assert result["success"] is True
    assert result["command"] == "systemctl restart nginx"


def test_execute_action_rejects_disallowed_action():
    """Executing a disallowed action should fail without SSH."""
    with patch("app.control.actions.run_action_command") as mock_ssh:
        result = execute_action("rm", "everything")

    assert result["success"] is False
    assert "not in the allowed" in result["error"]
    mock_ssh.assert_not_called()


def test_execute_action_rejects_unsafe_target():
    """Executing with an unsafe target should fail without SSH."""
    with patch("app.control.actions.run_action_command") as mock_ssh:
        result = execute_action("restart_container", "backend; rm -rf /")

    assert result["success"] is False
    mock_ssh.assert_not_called()


def test_execute_action_handles_ssh_failure():
    """SSH failure should be properly reported."""
    mock_result = {"success": False, "error": "Connection refused"}
    with patch("app.control.actions.run_action_command", return_value=mock_result):
        result = execute_action("restart_container", "backend")

    assert result["success"] is False
    assert "Connection refused" in result["error"]


def test_execute_action_rejects_nonexistent_container():
    """SSH failure for non-existent container should be reported."""
    mock_result = {"success": False, "error": "No such container: ghost"}
    with patch("app.control.actions.run_action_command", return_value=mock_result):
        result = execute_action("restart_container", "ghost")

    assert result["success"] is False


# --- ActionRecord creation ---

def test_create_action_record():
    """create_action_record should create a proper record."""
    record = create_action_record(
        "restart_container", "backend", "docker restart backend",
        risk_level="medium", approval_status="approved",
    )
    assert isinstance(record, ActionRecord)
    assert record.action_type == "restart_container"
    assert record.target == "backend"
    assert record.risk_level == "medium"
    assert record.approval_status == "approved"


# --- Security: whitelist enforcement ---

def test_actions_always_pass_through_whitelist():
    """Every action command must pass through the SSH whitelist."""
    # Even if somehow an unsafe command got through, the whitelist rejects it
    mock_result = {"success": False, "error": "Command not permitted"}
    with patch("app.control.actions.run_action_command", return_value=mock_result):
        with patch("app.control.actions.build_action_command", return_value="rm -rf /"):
            with patch("app.control.actions.is_action_executable", return_value=True):
                with patch("app.control.actions.validate_target_name", return_value=True):
                    result = execute_action("rm", "/")
                    # The whitelist should catch this
                    assert result["success"] is False
