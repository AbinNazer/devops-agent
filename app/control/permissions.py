"""
Permission / Approval Engine for Phase 5.

Implements the explicit permission handling between action proposal and
execution. Read-only diagnostics are auto-approved. Low-risk reversible
actions ask the user. Critical actions are blocked by default.
"""
from dataclasses import dataclass
from typing import Dict, Optional

from app.control.state import ApprovalStatus, RiskLevel


@dataclass
class ApprovalRequest:
    """A request for permission to execute an action."""
    action_type: str
    target: str
    command: str
    risk_level: str
    reason: str
    evidence: str
    expected_impact: str
    rollback_plan: str
    reversible: bool = True

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "command": self.command,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "evidence": self.evidence,
            "expected_impact": self.expected_impact,
            "rollback_plan": self.rollback_plan,
            "reversible": self.reversible,
        }

    def format_approval_prompt(self) -> str:
        """Format a clear approval prompt for the user."""
        risk_label = self.risk_level.upper()
        lines = [
            "=" * 50,
            "ACTION PROPOSED",
            "=" * 50,
            f"Action:   {self.action_type}",
            f"Target:   {self.target}",
            f"Command:  {self.command}",
            f"Risk:     {risk_label}",
            "",
            f"Reason:   {self.reason}",
            f"Evidence: {self.evidence}",
            f"Impact:   {self.expected_impact}",
            f"Rollback: {self.rollback_plan}",
            "",
            f"Reversible: {'Yes' if self.reversible else 'No'}",
            "",
            "Proceed? [y/N]",
        ]
        return "\n".join(lines)


def determine_approval_requirement(risk_level: str) -> str:
    """
    Determine what kind of approval is needed for a given risk level.

    Returns one of:
    - "auto"     → auto-approved (read-only diagnostics)
    - "ask"      → ask user with simple yes/no
    - "ask_explain" → ask user with detailed explanation
    - "block"    → blocked by default, never auto-execute
    """
    if risk_level == RiskLevel.LOW.value:
        return "auto"
    elif risk_level == RiskLevel.MEDIUM.value:
        return "ask"
    elif risk_level == RiskLevel.HIGH.value:
        return "ask_explain"
    elif risk_level == RiskLevel.CRITICAL.value:
        return "block"
    return "ask"


def evaluate_approval(request: ApprovalRequest) -> Dict:
    """
    Evaluate an approval request and return the approval decision.

    This is the decision point — the LLM can only REQUEST, this function
    DECIDES whether execution is permitted.
    """
    requirement = determine_approval_requirement(request.risk_level)

    if requirement == "auto":
        return {
            "status": ApprovalStatus.AUTO_APPROVED.value,
            "requirement": requirement,
            "reason": "Low-risk read-only action auto-approved.",
        }
    elif requirement == "block":
        return {
            "status": ApprovalStatus.BLOCKED.value,
            "requirement": requirement,
            "reason": (
                f"CRITICAL action '{request.action_type}' on '{request.target}' "
                f"is blocked by default. Manual intervention required."
            ),
        }
    elif requirement in ("ask", "ask_explain"):
        return {
            "status": ApprovalStatus.PENDING.value,
            "requirement": requirement,
            "prompt": request.format_approval_prompt(),
            "reason": "User approval required.",
        }
    else:
        return {
            "status": ApprovalStatus.BLOCKED.value,
            "requirement": "unknown",
            "reason": f"Unknown approval requirement: {requirement}",
        }


def simulate_user_approval(approved: bool, approver: str = "user") -> Dict:
    """
    Record the user's approval decision.

    In a real CLI, this would be called after the user responds to the
    approval prompt. For testing/automation, this simulates the response.
    """
    if approved:
        return {
            "status": ApprovalStatus.APPROVED.value,
            "approved_by": approver,
            "reason": "User approved the action.",
        }
    else:
        return {
            "status": ApprovalStatus.DENIED.value,
            "approved_by": approver,
            "reason": "User denied the action.",
        }
