"""
Tests for app/control/permissions.py — the approval system.

Verifies that different risk levels require different approval types,
and that the approval flow works correctly.
"""
import pytest

from app.control.permissions import (
    ApprovalRequest, evaluate_approval, determine_approval_requirement,
    simulate_user_approval,
)
from app.control.state import ApprovalStatus, RiskLevel


# --- Approval requirement determination ---

def test_low_risk_auto_approved():
    """Low-risk actions should be auto-approved."""
    result = determine_approval_requirement(RiskLevel.LOW.value)
    assert result == "auto"


def test_medium_risk_asks_user():
    """Medium-risk actions should ask the user."""
    result = determine_approval_requirement(RiskLevel.MEDIUM.value)
    assert result == "ask"


def test_high_risk_asks_with_explanation():
    """High-risk actions should ask with detailed explanation."""
    result = determine_approval_requirement(RiskLevel.HIGH.value)
    assert result == "ask_explain"


def test_critical_risk_blocked():
    """Critical-risk actions should be blocked."""
    result = determine_approval_requirement(RiskLevel.CRITICAL.value)
    assert result == "block"


# --- Approval evaluation ---

def test_low_risk_auto_approved():
    """Low-risk actions should be auto-approved."""
    request = ApprovalRequest(
        action_type="check_status",
        target="backend",
        command="uptime",
        risk_level=RiskLevel.LOW.value,
        reason="Checking status",
        evidence="User asked about health",
        expected_impact="None",
        rollback_plan="N/A",
    )
    result = evaluate_approval(request)
    assert result["status"] == ApprovalStatus.AUTO_APPROVED.value
    assert "auto-approved" in result["reason"].lower()


def test_critical_risk_blocked():
    """Critical-risk actions should be blocked."""
    request = ApprovalRequest(
        action_type="delete_resource",
        target="production-database",
        command="rm -rf /data",
        risk_level=RiskLevel.CRITICAL.value,
        reason="User asked to delete",
        evidence="User explicitly requested deletion",
        expected_impact="Total data loss",
        rollback_plan="Restore from backup (if available)",
    )
    result = evaluate_approval(request)
    assert result["status"] == ApprovalStatus.BLOCKED.value
    assert "blocked" in result["reason"].lower()


def test_medium_risk_pending_approval():
    """Medium-risk actions should be pending user approval."""
    request = ApprovalRequest(
        action_type="restart_container",
        target="backend",
        command="docker restart backend",
        risk_level=RiskLevel.MEDIUM.value,
        reason="Container is unhealthy",
        evidence="Docker health check shows unhealthy",
        expected_impact="~5-20 seconds service interruption",
        rollback_plan="Restart again if needed",
    )
    result = evaluate_approval(request)
    assert result["status"] == ApprovalStatus.PENDING.value
    assert "prompt" in result


def test_high_risk_pending_with_explanation():
    """Restart-class actions always require explicit approval, even at high risk.

    evaluate_approval deliberately short-circuits restart-class actions to
    requirement="ask": a write action must never be auto-approved (or
    auto-explained into approval) based on risk level alone.
    """
    request = ApprovalRequest(
        action_type="restart_service",
        target="postgresql",
        command="systemctl restart postgresql",
        risk_level=RiskLevel.HIGH.value,
        reason="Database is not responding",
        evidence="Connection timeout in application logs",
        expected_impact="Database downtime during restart",
        rollback_plan="Restart service again",
    )
    result = evaluate_approval(request)
    assert result["status"] == ApprovalStatus.PENDING.value
    assert result["requirement"] == "ask"


# --- User approval simulation ---

def test_user_approval_approved():
    """User approval should return approved status."""
    result = simulate_user_approval(True, "admin")
    assert result["status"] == ApprovalStatus.APPROVED.value
    assert result["approved_by"] == "admin"


def test_user_approval_denied():
    """User denial should return denied status."""
    result = simulate_user_approval(False, "admin")
    assert result["status"] == ApprovalStatus.DENIED.value


# --- Approval request formatting ---

def test_approval_request_format_prompt():
    """Approval prompt should be human-readable."""
    request = ApprovalRequest(
        action_type="restart_container",
        target="backend",
        command="docker restart backend",
        risk_level="medium",
        reason="Container is unhealthy",
        evidence="Docker health check failed",
        expected_impact="Brief service interruption",
        rollback_plan="Restart again",
    )
    prompt = request.format_approval_prompt()
    assert "restart_container" in prompt
    assert "backend" in prompt
    assert "docker restart backend" in prompt
    assert "MEDIUM" in prompt
    assert "[y/N]" in prompt


def test_approval_request_to_dict():
    """to_dict should serialize the request properly."""
    request = ApprovalRequest(
        action_type="restart_container",
        target="backend",
        command="docker restart backend",
        risk_level="medium",
        reason="unhealthy",
        evidence="health check failed",
        expected_impact="brief outage",
        rollback_plan="restart again",
    )
    d = request.to_dict()
    assert d["action_type"] == "restart_container"
    assert d["target"] == "backend"
    assert d["risk_level"] == "medium"


# --- Full approval flow ---

def test_full_approval_flow_auto():
    """Low-risk should go straight to auto-approved."""
    request = ApprovalRequest(
        action_type="check_status", target="", command="uptime",
        risk_level="low", reason="check", evidence="none",
        expected_impact="none", rollback_plan="n/a",
    )
    result = evaluate_approval(request)
    assert result["status"] == ApprovalStatus.AUTO_APPROVED.value


def test_full_approval_flow_blocked():
    """Critical-risk should be blocked."""
    request = ApprovalRequest(
        action_type="delete_resource", target="db", command="rm -rf /data",
        risk_level="critical", reason="delete", evidence="none",
        expected_impact="total loss", rollback_plan="backup",
    )
    result = evaluate_approval(request)
    assert result["status"] == ApprovalStatus.BLOCKED.value


def test_full_approval_flow_user_approves():
    """Medium-risk with user approval should be approved."""
    request = ApprovalRequest(
        action_type="restart_container", target="backend",
        command="docker restart backend",
        risk_level="medium", reason="unhealthy", evidence="checks",
        expected_impact="brief outage", rollback_plan="restart",
    )
    approval = evaluate_approval(request)
    assert approval["status"] == ApprovalStatus.PENDING.value

    user_decision = simulate_user_approval(True)
    assert user_decision["status"] == ApprovalStatus.APPROVED.value


def test_full_approval_flow_user_denies():
    """Medium-risk with user denial should be denied."""
    request = ApprovalRequest(
        action_type="restart_container", target="backend",
        command="docker restart backend",
        risk_level="medium", reason="unhealthy", evidence="checks",
        expected_impact="brief outage", rollback_plan="restart",
    )
    approval = evaluate_approval(request)
    assert approval["status"] == ApprovalStatus.PENDING.value

    user_decision = simulate_user_approval(False)
    assert user_decision["status"] == ApprovalStatus.DENIED.value
