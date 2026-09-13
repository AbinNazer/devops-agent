"""
Verification Engine for Phase 5.

Every action MUST have a verification strategy. Success means the intended
postcondition was verified — NOT just that the command returned exit code 0.

Verification strategies per action type:
- restart_container: check container state → check health → check logs
- restart_service: check service status → check process running

Verification result tracks:
- expected state vs observed state
- checks performed
- attempts made
- overall status
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.ssh_client import run_whitelisted_command, truncate
from app.ssh_whitelist import (
    build_docker_logs_command,
    build_docker_inspect_command,
    build_service_status_command,
    is_command_allowed,
)


class VerificationStatus(str):
    SUCCESS = "success"
    FAILED = "failed"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class VerificationCheck:
    """A single verification check."""
    name: str
    expected: str
    observed: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationResult:
    """Result of verifying an action's outcome."""
    action_type: str
    target: str
    status: str = VerificationStatus.UNKNOWN
    checks: List[VerificationCheck] = field(default_factory=list)
    attempts: int = 0
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "status": self.status,
            "checks": [
                {"name": c.name, "expected": c.expected, "observed": c.observed, "passed": c.passed}
                for c in self.checks
            ],
            "attempts": self.attempts,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


def _check_container_running(target: str) -> VerificationCheck:
    """Check if a Docker container is in running state."""
    command = f"docker ps -a --format '{{{{.Names}}}}|{{{{.Status}}}}|{{{{.State}}}}'"
    if not is_command_allowed(command):
        return VerificationCheck(
            name="container_state", expected="running", observed="unknown",
            passed=False, detail="Cannot verify: command not whitelisted.",
        )

    result = run_whitelisted_command(command)
    if not result["success"]:
        return VerificationCheck(
            name="container_state", expected="running", observed="error",
            passed=False, detail=f"Command failed: {result.get('error', 'unknown')}",
        )

    for line in result["stdout"].splitlines():
        parts = line.split("|")
        if len(parts) >= 3 and parts[0].strip() == target:
            state = parts[2].strip().lower()  # .State is the 3rd field
            return VerificationCheck(
                name="container_state", expected="running", observed=state,
                passed=state == "running",
                detail=f"Container '{target}' state: {state}",
            )

    return VerificationCheck(
        name="container_state", expected="running", observed="not_found",
        passed=False, detail=f"Container '{target}' not found in docker ps output.",
    )


def _check_container_healthy(target: str) -> VerificationCheck:
    """Check if a Docker container reports healthy (if it has a healthcheck)."""
    command = build_docker_inspect_command(target)
    if not is_command_allowed(command):
        return VerificationCheck(
            name="container_health", expected="healthy or no_healthcheck", observed="unknown",
            passed=False, detail="Cannot verify: inspect command not whitelisted.",
        )

    result = run_whitelisted_command(command)
    if not result["success"]:
        return VerificationCheck(
            name="container_health", expected="healthy or no_healthcheck", observed="error",
            passed=False, detail=f"Inspect failed: {result.get('error', 'unknown')}",
        )

    stdout = result["stdout"].lower()
    if "unhealthy" in stdout:
        return VerificationCheck(
            name="container_health", expected="healthy or no_healthcheck", observed="unhealthy",
            passed=False, detail=f"Container '{target}' is unhealthy.",
        )
    if '"health": ""' in stdout or '"health":' not in stdout:
        return VerificationCheck(
            name="container_health", expected="healthy or no_healthcheck", observed="no_healthcheck",
            passed=True, detail=f"Container '{target}' has no healthcheck defined.",
        )
    return VerificationCheck(
        name="container_health", expected="healthy or no_healthcheck", observed="healthy",
        passed=True, detail=f"Container '{target}' is healthy.",
    )


def _check_container_recent_logs(target: str) -> VerificationCheck:
    """Check recent logs for crash/restart indicators."""
    command = build_docker_logs_command(target, 20)
    if not is_command_allowed(command):
        return VerificationCheck(
            name="recent_logs", expected="no crash indicators", observed="unknown",
            passed=False, detail="Cannot verify: logs command not whitelisted.",
        )

    result = run_whitelisted_command(command)
    if not result["success"]:
        return VerificationCheck(
            name="recent_logs", expected="no crash indicators", observed="error",
            passed=False, detail=f"Logs check failed: {result.get('error', 'unknown')}",
        )

    stdout = result["stdout"].lower()
    crash_indicators = ["panic", "fatal", "segfault", "killed", "oom"]
    found = [ind for ind in crash_indicators if ind in stdout]

    return VerificationCheck(
        name="recent_logs",
        expected="no crash indicators",
        observed=f"found: {', '.join(found)}" if found else "clean",
        passed=len(found) == 0,
        detail=f"Checked recent logs for '{target}': " + (f"found issues: {found}" if found else "no issues found"),
    )


def _check_service_running(target: str) -> VerificationCheck:
    """Check if a systemd service is active."""
    command = build_service_status_command(target)
    if not is_command_allowed(command):
        return VerificationCheck(
            name="service_status", expected="active (running)", observed="unknown",
            passed=False, detail="Cannot verify: systemctl status not whitelisted.",
        )

    result = run_whitelisted_command(command)
    if not result["success"]:
        return VerificationCheck(
            name="service_status", expected="active (running)", observed="error",
            passed=False, detail=f"Status check failed: {result.get('error', 'unknown')}",
        )

    stdout = result["stdout"].lower()
    if "active (running)" in stdout:
        return VerificationCheck(
            name="service_status", expected="active (running)", observed="active (running)",
            passed=True, detail=f"Service '{target}' is active and running.",
        )
    if "active (exited)" in stdout:
        return VerificationCheck(
            name="service_status", expected="active (running)", observed="active (exited)",
            passed=False, detail=f"Service '{target}' exited (not running).",
        )
    if "inactive" in stdout or "failed" in stdout:
        return VerificationCheck(
            name="service_status", expected="active (running)", observed=stdout[:50],
            passed=False, detail=f"Service '{target}' is not running.",
        )
    return VerificationCheck(
        name="service_status", expected="active (running)", observed="unknown",
        passed=False, detail=f"Could not determine status of '{target}'.",
    )


# Verification strategy registry
_VERIFICATION_STRATEGIES = {
    "restart_container": [
        _check_container_running,
        _check_container_healthy,
        _check_container_recent_logs,
    ],
    "restart_service": [
        _check_service_running,
    ],
}


def verify_action(action_type: str, target: str, max_attempts: int = 3) -> VerificationResult:
    """
    Verify the outcome of an executed action.

    Retries verification checks up to max_attempts if initial verification
    fails (the container/service may still be starting up).
    """
    strategies = _VERIFICATION_STRATEGIES.get(action_type, [])

    if not strategies:
        return VerificationResult(
            action_type=action_type, target=target,
            status=VerificationStatus.UNKNOWN,
            evidence=[f"No verification strategy defined for action '{action_type}'."],
        )

    result = VerificationResult(action_type=action_type, target=target)

    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        checks = []
        for strategy_fn in strategies:
            check = strategy_fn(target)
            checks.append(check)

        result.checks = checks
        result.evidence = [c.detail for c in checks if c.detail]

        all_passed = all(c.passed for c in checks)
        any_passed = any(c.passed for c in checks)

        if all_passed:
            result.status = VerificationStatus.SUCCESS
            result.confidence = 0.95
            return result
        elif any_passed and attempt < max_attempts:
            # Some checks passed — might still be starting up, retry
            import time
            time.sleep(2)
            continue
        elif any_passed:
            result.status = VerificationStatus.DEGRADED
            result.confidence = 0.5
            return result
        else:
            if attempt < max_attempts:
                import time
                time.sleep(2)
                continue
            result.status = VerificationStatus.FAILED
            result.confidence = 0.8
            return result

    result.status = VerificationStatus.FAILED
    result.confidence = 0.7
    return result
