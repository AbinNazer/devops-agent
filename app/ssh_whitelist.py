"""
SSH command whitelist and parameter validation.
 
This is the single security boundary between "the agent wants to run X"
and "X actually runs on the VPS." Enforcement happens here, in Python,
BEFORE ssh_client ever opens a connection. The LLM never constructs or
sees a raw shell string — it only ever picks a tool and passes typed
arguments (a container name, a line count), which get validated here
against a strict pattern before any command string is even built.
 
No arbitrary command execution is possible through this module by design:
is_command_allowed() only returns True for an exact match against one of
the fixed WHITELIST_PATTERNS below. Everything else — rm, curl, bash,
docker exec, systemctl restart, anything not listed — is rejected.
"""
import re
from typing import Union
 
# Exact whitelist. {placeholders} mark the only parameterized commands.
WHITELIST_PATTERNS = [
    "docker ps",
    "docker ps -a",
    "docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'",
    "df -h",
    "free -h",
    "docker stats --no-stream",
    "docker logs {container} --tail {n}",
    "docker inspect {container}",
    "uptime",
    "nproc",
    "systemctl status {service}",
    # Phase 5: safe, explicitly approved write actions
    "docker restart {container}",
    "systemctl restart {service}",
    "ps aux --sort=-%cpu",
    # K3s/Kubernetes — read-only "get"/"cluster-info" only. No delete, apply,
    # create, patch, edit, rollout, scale, or exec — those are never listed
    # here, so is_command_allowed() rejects them regardless of what the LLM asks for.
    "kubectl get nodes",
    "kubectl get pods -A",
    "kubectl get deployments -A",
    "kubectl get services -A",
    "kubectl get events -A",
    "kubectl cluster-info",
]
 
# Safe name pattern for container/service names: alphanumeric, dash,
# underscore, dot only. No spaces, no shell metacharacters, no path
# traversal — this alone blocks injection attempts like "x; rm -rf /".
_NAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{1,128}$")
 
 
class ValidationError(ValueError):
    """Raised when a tool argument fails safety validation."""
 
 
def validate_container_name(name: Union[str, None]) -> str:
    if not name or not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValidationError(f"Invalid container name: {name!r}")
    return name
 
 
def validate_service_name(name: Union[str, None]) -> str:
    if not name or not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValidationError(f"Invalid service name: {name!r}")
    return name
 
 
def validate_tail_lines(n: Union[int, str, None]) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        raise ValidationError(f"Invalid line count: {n!r}")
    if n < 1 or n > 1000:
        raise ValidationError("Line count must be between 1 and 1000")
    return n
 
 
def _pattern_to_regex(pattern: str) -> "re.Pattern":
    """Turn 'docker logs {container} --tail {n}' into a matching regex
    that only accepts safe-name-shaped values in place of each placeholder."""
    escaped = re.escape(pattern)
    escaped = re.sub(r"\\\{[a-zA-Z_]+\\\}", r"[\\w.\\-]{1,128}", escaped)
    return re.compile(f"^{escaped}$")
 
 
_COMPILED_PATTERNS = [_pattern_to_regex(p) for p in WHITELIST_PATTERNS]
 
 
def is_command_allowed(command: str) -> bool:
    """True only if `command` matches one of the whitelist patterns exactly."""
    if not command or not isinstance(command, str):
        return False
    return any(p.match(command) for p in _COMPILED_PATTERNS)
 
 
def build_docker_logs_command(container: str, lines: Union[int, str]) -> str:
    container = validate_container_name(container)
    lines = validate_tail_lines(lines)
    return f"docker logs {container} --tail {lines}"
 
 
def build_docker_inspect_command(container: str) -> str:
    container = validate_container_name(container)
    return f"docker inspect {container}"
 
 
def build_service_status_command(service: str) -> str:
    service = validate_service_name(service)
    return f"systemctl status {service}"


def build_docker_restart_command(container: str) -> str:
    container = validate_container_name(container)
    return f"docker restart {container}"


def build_service_restart_command(service: str) -> str:
    service = validate_service_name(service)
    return f"systemctl restart {service}"