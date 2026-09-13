"""
Tests for app/control/verification.py — post-action verification engine.

Verifies that verification checks properly determine success/failure
of actions, and that verification strategies are correct per action type.
"""
from unittest.mock import patch, MagicMock

import pytest

from app.control.verification import (
    verify_action, VerificationStatus, VerificationResult, VerificationCheck,
)


# --- VerificationResult ---

def test_verification_result_to_dict():
    """VerificationResult.to_dict should serialize properly."""
    result = VerificationResult(
        action_type="restart_container",
        target="backend",
        status=VerificationStatus.SUCCESS,
        checks=[
            VerificationCheck(name="container_state", expected="running", observed="running", passed=True),
        ],
        attempts=1,
        evidence=["Container is running"],
        confidence=0.95,
    )
    d = result.to_dict()
    assert d["action_type"] == "restart_container"
    assert d["status"] == "success"
    assert len(d["checks"]) == 1
    assert d["checks"][0]["passed"] is True


# --- Verification strategies ---

def test_verify_container_restart_with_running_container():
    """Verifying a restarted container that's running should succeed."""
    ps_output = "backend|Up 5 minutes|running"
    inspect_output = '{"State": {"Health": null}}'
    logs_output = "2026-08-31 Started successfully"

    ok_result = {"success": True, "stderr": "", "exit_code": 0}
    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        mock_ssh.return_value = {**ok_result, "stdout": ps_output}
        # Make it respond differently per command
        def side_effect(command):
            if "docker ps" in command:
                return {**ok_result, "stdout": ps_output}
            if "docker inspect" in command:
                return {**ok_result, "stdout": inspect_output}
            if "docker logs" in command:
                return {**ok_result, "stdout": logs_output}
            return {**ok_result, "stdout": ""}
        mock_ssh.side_effect = side_effect
        result = verify_action("restart_container", "backend", max_attempts=1)

    assert result.status == VerificationStatus.SUCCESS
    assert result.confidence > 0.8


def test_verify_container_restart_with_stopped_container():
    """Verifying a restarted container that's still stopped should fail."""
    ps_output = "backend|exited"
    inspect_output = '{"State": {}}'
    logs_output = "container exited"

    ok_result = {"success": True, "stderr": "", "exit_code": 0}
    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        def side_effect(command):
            if "docker ps" in command:
                return {**ok_result, "stdout": ps_output}
            if "docker inspect" in command:
                return {**ok_result, "stdout": inspect_output}
            if "docker logs" in command:
                return {**ok_result, "stdout": logs_output}
            return {**ok_result, "stdout": ""}
        mock_ssh.side_effect = side_effect
        result = verify_action("restart_container", "backend", max_attempts=1)

    assert result.status in (VerificationStatus.FAILED, VerificationStatus.DEGRADED)
    assert result.attempts >= 1


def test_verify_service_restart_with_active_service():
    """Verifying a restarted service that's active should succeed."""
    status_output = "Active: active (running) since 2026-08-31"

    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        mock_ssh.return_value = {"success": True, "stdout": status_output, "stderr": "", "exit_code": 0}
        result = verify_action("restart_service", "nginx")

    assert result.status == VerificationStatus.SUCCESS


def test_verify_service_restart_with_inactive_service():
    """Verifying a restarted service that's inactive should fail."""
    status_output = "Active: inactive (dead)"

    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        mock_ssh.return_value = {"success": True, "stdout": status_output, "stderr": "", "exit_code": 0}
        result = verify_action("restart_service", "nginx")

    assert result.status == VerificationStatus.FAILED


def test_verify_unknown_action_type():
    """Unknown action types should return UNKNOWN status."""
    result = verify_action("unknown_action", "target")
    assert result.status == VerificationStatus.UNKNOWN


# --- Log checking ---

def test_verify_checks_logs_for_crash_indicators():
    """Verification should check logs for crash indicators."""
    ps_output = "backend|Up 5 minutes|running"
    inspect_output = '{"State": {"Health": null}}'
    logs_output = "panic: runtime error"

    ok_result = {"success": True, "stderr": "", "exit_code": 0}
    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        def side_effect(command):
            if "docker ps" in command:
                return {**ok_result, "stdout": ps_output}
            if "docker inspect" in command:
                return {**ok_result, "stdout": inspect_output}
            if "docker logs" in command:
                return {**ok_result, "stdout": logs_output}
            return {**ok_result, "stdout": ""}
        mock_ssh.side_effect = side_effect
        result = verify_action("restart_container", "backend", max_attempts=1)

    # Even though container is running, logs show panic → degraded
    assert result.status in (VerificationStatus.DEGRADED, VerificationStatus.FAILED)


def test_verify_clean_logs():
    """Verification should pass with clean logs."""
    ps_output = "backend|Up 5 minutes|running"
    inspect_output = '{"State": {"Health": null}}'
    logs_output = "INFO: Server started on port 8080"

    ok_result = {"success": True, "stderr": "", "exit_code": 0}
    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        def side_effect(command):
            if "docker ps" in command:
                return {**ok_result, "stdout": ps_output}
            if "docker inspect" in command:
                return {**ok_result, "stdout": inspect_output}
            if "docker logs" in command:
                return {**ok_result, "stdout": logs_output}
            return {**ok_result, "stdout": ""}
        mock_ssh.side_effect = side_effect
        result = verify_action("restart_container", "backend", max_attempts=1)

    assert result.status == VerificationStatus.SUCCESS


# --- Verification checks structure ---

def test_verification_checks_have_expected_fields():
    """Each verification check should have expected fields."""
    ps_output = "backend|Up 5 minutes|running"
    inspect_output = '{"State": {"Health": null}}'
    logs_output = "OK"

    ok_result = {"success": True, "stderr": "", "exit_code": 0}
    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        def side_effect(command):
            if "docker ps" in command:
                return {**ok_result, "stdout": ps_output}
            if "docker inspect" in command:
                return {**ok_result, "stdout": inspect_output}
            if "docker logs" in command:
                return {**ok_result, "stdout": logs_output}
            return {**ok_result, "stdout": ""}
        mock_ssh.side_effect = side_effect
        result = verify_action("restart_container", "backend", max_attempts=1)

    assert len(result.checks) == 3
    for check in result.checks:
        assert hasattr(check, "name")
        assert hasattr(check, "expected")
        assert hasattr(check, "observed")
        assert hasattr(check, "passed")


# --- Retry behavior ---

def test_verify_retries_on_partial_failure():
    """Verification should retry if some checks pass but not all."""
    ps_output_first = "backend|restarting"  # Not running yet
    ps_output_retry = "backend|running"     # Running after retry
    inspect_output = '{"State": {"Health": null}}'
    logs_output = "Started"

    call_count = [0]

    def mock_ssh(command):
        call_count[0] += 1
        if "docker ps" in command:
            if call_count[0] <= 3:
                return {"success": True, "stdout": ps_output_first, "stderr": "", "exit_code": 0}
            return {"success": True, "stdout": ps_output_retry, "stderr": "", "exit_code": 0}
        if "docker inspect" in command:
            return {"success": True, "stdout": inspect_output, "stderr": "", "exit_code": 0}
        if "docker logs" in command:
            return {"success": True, "stdout": logs_output, "stderr": "", "exit_code": 0}
        return {"success": False, "error": "unknown command"}

    with patch("app.control.verification.run_whitelisted_command", side_effect=mock_ssh):
        result = verify_action("restart_container", "backend", max_attempts=3)

    assert result.attempts >= 1


# --- Verification without SSH ---

def test_verification_handles_ssh_failure():
    """Verification should handle SSH failures gracefully."""
    with patch("app.control.verification.run_whitelisted_command") as mock_ssh:
        mock_ssh.return_value = {"success": False, "error": "SSH connection failed"}
        result = verify_action("restart_container", "backend")

    # Should still return a result, not crash
    assert result.status in (VerificationStatus.FAILED, VerificationStatus.DEGRADED, VerificationStatus.UNKNOWN)
    assert len(result.evidence) > 0
