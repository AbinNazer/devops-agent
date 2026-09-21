"""
Action Policy Firewall for Phase 5.

Hard boundary between LLM recommendation and actual execution.
The LLM must NOT be able to bypass the policy engine.

Architecture:
    LLM → ActionRequest → PolicyEngine → PermissionEngine → RiskEngine
    → ApprovedAction → Tool → SSH whitelist → VPS

Only explicitly allowed actions can pass through this firewall.
Everything else is blocked.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.control.state import RiskLevel


@dataclass
class ActionRequest:
    """An action requested by the LLM or control loop."""
    action_type: str
    target: str
    command: str = ""
    reason: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "command": self.command,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass
class ApprovedAction:
    """An action that has passed through all policy/permission/risk checks."""
    action_type: str
    target: str
    command: str
    risk_level: str
    approval_status: str
    approved_by: str = ""
    conditions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "command": self.command,
            "risk_level": self.risk_level,
            "approval_status": self.approval_status,
            "approved_by": self.approved_by,
            "conditions": self.conditions,
        }


# ── Allowed actions (explicitly whitelisted) ──

# Safe container actions — only restart of approved containers
ALLOWED_CONTAINER_ACTIONS = {"start_container", "stop_container", "restart_container"}

# Safe service actions — only restart of approved services
ALLOWED_SERVICE_ACTIONS = {"restart_service"}

# Read-only actions (always allowed)
READONLY_ACTIONS = {"check_status", "get_info", "diagnose"}

# Blocked actions — NEVER allowed regardless of context
BLOCKED_ACTIONS = {
    "rm", "delete", "destroy", "remove",
    "docker_system_prune", "docker_prune",
    "kubectl_delete", "kubectl_apply", "kubectl_exec",
    "kubectl_create", "kubectl_patch", "kubectl_edit",
    "package_install", "apt_install", "pip_install",
    "firewall_modify", "ufw", "iptables",
    "user_create", "user_delete", "user_modify",
    "ssh_config_modify",
    "database_drop", "database_delete", "database_truncate",
    "shell_execute", "arbitrary_command",
    "sudo",
}

# Blocked command patterns — regex patterns that must NEVER pass.
# NOTE: `docker restart` and `systemctl restart` are NOT blocked here —
# they are the specific safe actions allowed by the policy engine.
_BLOCKED_COMMAND_PATTERNS = [
    r"rm\s",
    r"rm\s+-",
    r"delete",
    r"destroy",
    r"curl\s.*\|\s*(ba)?sh",
    r"wget\s.*\|\s*(ba)?sh",
    r"sudo\s",
    r"docker\s+exec",
    r"kubectl\s+(delete|apply|create|patch|edit|exec)",
    r"systemctl\s+(stop|disable)",
    r"docker\s+(stop|rm|kill)",
]

# Safe container/service name pattern (matches whitelist)
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")

# Allowed restart command templates (must match whitelist patterns)
_ALLOWED_RESTART_COMMANDS = {
    "start_container": "docker start {target}",
    "stop_container": "docker stop {target}",
    "restart_container": "docker restart {target}",
    "restart_service": "systemctl restart {target}",
}


class PolicyViolation(Exception):
    """Raised when an action violates the policy firewall."""
    pass


def is_action_allowed(action_type: str) -> bool:
    """Check if an action type is in the allowed set."""
    if action_type in BLOCKED_ACTIONS:
        return False
    if action_type in READONLY_ACTIONS:
        return True
    if action_type in ALLOWED_CONTAINER_ACTIONS:
        return True
    if action_type in ALLOWED_SERVICE_ACTIONS:
        return True
    return False


def is_command_safe(command: str) -> bool:
    """
    Verify a command doesn't match any blocked pattern.
    This is a SECONDARY check — the SSH whitelist is the primary boundary.
    """
    if not command:
        return False
    for pattern in _BLOCKED_COMMAND_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return False
    return True


def validate_target_name(target: str) -> bool:
    """Validate that a target name is safe (no injection)."""
    if not target or not isinstance(target, str):
        return False
    return bool(_SAFE_NAME_RE.match(target))


def build_safe_command(action_type: str, target: str) -> Optional[str]:
    """
    Build a command from an action type and target.
    Returns None if the combination is not allowed.
    """
    if not validate_target_name(target):
        return None

    template = _ALLOWED_RESTART_COMMANDS.get(action_type)
    if not template:
        return None

    command = template.format(target=target)

    # Double-check the built command is safe
    if not is_command_safe(command):
        return None

    return command


def evaluate_action(request: ActionRequest) -> Dict:
    """
    Evaluate an action request against the policy firewall.

    Returns a dict with:
    - allowed: bool
    - reason: str
    - command: str (if allowed and command was built)
    - risk_level: str
    """
    # Step 1: Check action type
    if not is_action_allowed(request.action_type):
        return {
            "allowed": False,
            "reason": f"Action type '{request.action_type}' is not in the allowed actions list.",
        }

    # Step 2: Validate target name
    if not validate_target_name(request.target):
        return {
            "allowed": False,
            "reason": f"Target name '{request.target}' contains unsafe characters.",
        }

    # Step 3: Build command (for actions that need one)
    command = request.command
    if not command:
        command = build_safe_command(request.action_type, request.target)
        if not command:
            return {
                "allowed": False,
                "reason": f"Cannot build a safe command for action '{request.action_type}' on '{request.target}'.",
            }

    # Step 4: Verify command safety
    if not is_command_safe(command):
        return {
            "allowed": False,
            "reason": f"Command '{command}' matches a blocked pattern.",
        }

    # Step 5: Determine risk level based on action type
    if request.action_type in READONLY_ACTIONS:
        risk_level = RiskLevel.LOW.value
    elif request.action_type in ALLOWED_CONTAINER_ACTIONS or request.action_type in ALLOWED_SERVICE_ACTIONS:
        risk_level = RiskLevel.MEDIUM.value
    else:
        risk_level = RiskLevel.HIGH.value

    return {
        "allowed": True,
        "reason": "Action passed all policy checks.",
        "command": command,
        "risk_level": risk_level,
    }


def get_allowed_actions() -> Dict:
    """Return the complete list of allowed and blocked actions for documentation."""
    return {
        "allowed": {
            "read_only": sorted(READONLY_ACTIONS),
            "container": sorted(ALLOWED_CONTAINER_ACTIONS),
            "service": sorted(ALLOWED_SERVICE_ACTIONS),
        },
        "blocked": sorted(BLOCKED_ACTIONS),
        "blocked_patterns": _BLOCKED_COMMAND_PATTERNS,
    }
