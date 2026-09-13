"""
Rollback Engine for Phase 5.

Before executing an action, determine whether it is reversible and create
a rollback plan. For actions where rollback is meaningless, explicitly
state rollback_not_applicable.

Rollback flow on verification failure:
1. Stop
2. Collect evidence
3. Determine whether rollback is safe
4. Ask permission if rollback requires approval
5. Execute rollback only when policy permits
6. Verify rollback
7. Record incident
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.control.state import RiskLevel
from app.ssh_client import run_whitelisted_command


@dataclass
class RollbackPlan:
    """Plan for rolling back an action if verification fails."""
    available: bool
    action_type: str = ""
    target: str = ""
    command: str = ""
    conditions: List[str] = field(default_factory=list)
    risk_level: str = RiskLevel.LOW.value
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "action_type": self.action_type,
            "target": self.target,
            "command": self.command,
            "conditions": self.conditions,
            "risk_level": self.risk_level,
            "reason": self.reason,
        }


@dataclass
class RollbackResult:
    """Result of executing a rollback."""
    success: bool
    action_type: str = ""
    target: str = ""
    verification_passed: bool = False
    error: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "action_type": self.action_type,
            "target": self.target,
            "verification_passed": self.verification_passed,
            "error": self.error,
            "evidence": self.evidence,
        }


def create_rollback_plan(action_type: str, target: str, original_state: Optional[Dict] = None) -> RollbackPlan:
    """
    Create a rollback plan for a given action.

    For restart actions, the "rollback" is essentially a no-op because
    the container/service should already be in its running state from
    the restart. We can attempt another restart if the first one caused issues.
    """
    if action_type == "restart_container":
        return RollbackPlan(
            available=True,
            action_type="restart_container",
            target=target,
            command=f"docker restart {target}",
            conditions=[
                "Verification failed after initial restart",
                "Container is in an unhealthy or crashed state",
            ],
            risk_level=RiskLevel.LOW.value,
            reason=(
                "Restarting again is low-risk. If the container was working before "
                "the restart, a second restart may resolve transient issues."
            ),
        )
    elif action_type == "restart_service":
        return RollbackPlan(
            available=True,
            action_type="restart_service",
            target=target,
            command=f"systemctl restart {target}",
            conditions=[
                "Verification failed after initial restart",
                "Service is not running",
            ],
            risk_level=RiskLevel.LOW.value,
            reason=(
                "Restarting a service is reversible in the sense that "
                "the service can be restarted again."
            ),
        )
    else:
        return RollbackPlan(
            available=False,
            action_type=action_type,
            target=target,
            reason=(
                f"Rollback not available for action '{action_type}'. "
                "This action cannot be meaningfully undone."
            ),
        )


def execute_rollback(rollback_plan: RollbackPlan) -> RollbackResult:
    """
    Execute a rollback if the plan is available and has a command.

    Returns a RollbackResult with success/failure status.
    """
    if not rollback_plan.available:
        return RollbackResult(
            success=False,
            action_type=rollback_plan.action_type,
            target=rollback_plan.target,
            error=rollback_plan.reason,
            evidence=["Rollback not available for this action type."],
        )

    if not rollback_plan.command:
        return RollbackResult(
            success=False,
            action_type=rollback_plan.action_type,
            target=rollback_plan.target,
            error="No rollback command defined.",
        )

    # Execute the rollback command through SSH (whitelist enforced)
    result = run_whitelisted_command(rollback_plan.command)

    if result["success"]:
        return RollbackResult(
            success=True,
            action_type=rollback_plan.action_type,
            target=rollback_plan.target,
            evidence=[f"Rollback command executed: {rollback_plan.command}"],
        )
    else:
        return RollbackResult(
            success=False,
            action_type=rollback_plan.action_type,
            target=rollback_plan.target,
            error=result.get("error", "Rollback command failed."),
            evidence=[f"Rollback command failed: {result.get('error', 'unknown')}"],
        )


def is_rollback_safe(rollback_plan: RollbackPlan) -> bool:
    """Determine if a rollback is safe to attempt without user approval."""
    if not rollback_plan.available:
        return False
    # Low-risk rollback actions can proceed without additional approval
    return rollback_plan.risk_level == RiskLevel.LOW.value
