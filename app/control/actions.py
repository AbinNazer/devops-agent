"""
Safe Actions module for Phase 5.

Initially supports ONLY a small set of reversible, explicitly approved actions:
- Restart an approved Docker container
- Restart an approved systemd service

Blocked actions:
- rm, delete, destroy, docker system prune
- kubectl delete/apply/exec
- package installation, firewall modification
- user creation/modification
- arbitrary shell commands

All actions are configurable and pass through the whitelist.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

from app.control.state import ActionRecord
from app.control.policy import (
    evaluate_action, build_safe_command, validate_target_name,
    is_action_allowed, ActionRequest,
)
from app.ssh_whitelist import is_action_command_allowed
from app.executor import run_action_command


# Configurable allowed actions — only these can be executed
CONFIGURABLE_ALLOWED_ACTIONS = {
    "start_container": {
        "description": "Start a stopped Docker container",
        "command_template": "docker start {target}",
        "reversible": True, "requires_approval": True,
    },
    "stop_container": {
        "description": "Stop a running Docker container",
        "command_template": "docker stop {target}",
        "reversible": True, "requires_approval": True,
    },
    "restart_container": {
        "description": "Restart a Docker container",
        "command_template": "docker restart {target}",
        "reversible": True,
        "requires_approval": True,
    },
    "restart_service": {
        "description": "Restart a systemd service",
        "command_template": "systemctl restart {target}",
        "reversible": True,
        "requires_approval": True,
    },
}


def get_allowed_action_types() -> List[str]:
    """Return the list of action types that can be executed."""
    return list(CONFIGURABLE_ALLOWED_ACTIONS.keys())


def is_action_executable(action_type: str) -> bool:
    """Check if a specific action type is in the allowed list."""
    return action_type in CONFIGURABLE_ALLOWED_ACTIONS


def build_action_command(action_type: str, target: str) -> Optional[str]:
    """Build a safe command for an allowed action."""
    action_config = CONFIGURABLE_ALLOWED_ACTIONS.get(action_type)
    if not action_config:
        return None
    if not validate_target_name(target):
        return None
    command = action_config["command_template"].format(target=target)
    # Verify it passes the SSH whitelist
    if not is_action_command_allowed(command):
        return None
    return command


def execute_action(action_type: str, target: str) -> Dict:
    """
    Execute an approved action on the VPS.

    This is the ONLY place where action commands are actually sent.
    Every command must pass through:
    1. Policy firewall (is_action_executable)
    2. SSH whitelist (is_command_allowed)
    3. SSH client (run_whitelisted_command)
    """
    # Step 1: Check action is allowed
    if not is_action_executable(action_type):
        return {
            "success": False,
            "error": f"Action '{action_type}' is not in the allowed actions list.",
        }

    # Step 2: Build command
    command = build_action_command(action_type, target)
    if not command:
        return {
            "success": False,
            "error": f"Cannot build a safe command for '{action_type}' on '{target}'.",
        }

    # Step 3: Execute through SSH (whitelist is enforced inside run_whitelisted_command)
    result = run_action_command(command)

    if result["success"]:
        return {
            "success": True,
            "action_type": action_type,
            "target": target,
            "command": command,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code", -1),
        }
    else:
        return {
            "success": False,
            "action_type": action_type,
            "target": target,
            "command": command,
            "error": result.get("error", "Unknown execution error."),
        }


def create_action_record(
    action_type: str, target: str, command: str,
    risk_level: str = "", approval_status: str = "",
) -> ActionRecord:
    """Create an ActionRecord for tracking."""
    return ActionRecord(
        action_type=action_type,
        target=target,
        command=command,
        risk_level=risk_level,
        approval_status=approval_status,
    )
