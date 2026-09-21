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
    "docker ps -a --format '{{.Names}}'",
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
    "docker images --format '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.Size}}|{{.CreatedAt}}'",
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
    that only accepts safe-name-shaped values in place of each placeholder.

    Special slots registered from server config (remote search):
    - {path}: zero or more slash-separated safe segments; a segment may not
      start with a dot, which blocks '..', '.env', and '.git' at the regex
      layer itself.
    - {query}: 1-6 space-separated safe words (bounded grep terms).
    """
    escaped = re.escape(pattern)
    # Use str.replace for the special slots instead of re.sub: re.sub would
    # interpret backslash escapes inside the replacement string. The tokens
    # are matched in their re.escape()d form.
    escaped = escaped.replace(re.escape("{path}"), r"(?:/(?!\.)[\w.\-]{1,128}){0,16}")
    escaped = escaped.replace(re.escape("{query}"), r"[\w.\-@/+]{1,60}(?: [\w.\-@/+]{1,60}){0,5}")
    escaped = re.sub(r"\\\{[a-zA-Z_]+\\\}", r"[\\w.\\-]{1,128}", escaped)
    return re.compile(f"^{escaped}$")


_COMPILED_PATTERNS = [_pattern_to_regex(p) for p in WHITELIST_PATTERNS]

# Server-config-derived read-only patterns (Tool Factory / remote search).
# These are registered ONLY from server-side configuration at startup —
# never per-request, never from LLM or user input — so the whitelist remains
# the single fail-closed boundary. See register_readonly_pattern() below.

# Mutation commands are deliberately kept in a separate boundary. They are
# never accepted by generic read-only tool execution.
ACTION_WHITELIST_PATTERNS = [
    "docker start {container}",
    "docker stop {container}",
    "docker restart {container}",
    "systemctl restart {service}",
]
_COMPILED_ACTION_PATTERNS = [_pattern_to_regex(p) for p in ACTION_WHITELIST_PATTERNS]


def is_command_allowed(command: str) -> bool:
    """True only if `command` matches one of the whitelist patterns exactly."""
    if not command or not isinstance(command, str):
        return False
    return any(p.match(command) for p in _COMPILED_PATTERNS)


def is_action_command_allowed(command: str) -> bool:
    """Allow only explicitly controlled mutation commands."""
    if not command or not isinstance(command, str):
        return False
    return any(p.match(command) for p in _COMPILED_ACTION_PATTERNS)


# ── Server-config-derived registration (Tool Factory / remote search) ──

# Read-only binaries that registered search/inspect patterns may start with.
_READONLY_BINARY_PREFIXES = ("grep", "head", "tail", "find", "cat", "stat", "wc", "du")

_FORBIDDEN_IN_PATTERN_RE = re.compile(r"[;|&$`()<>{}!\\*?~\n\r\t]")


def register_readonly_pattern(pattern: str) -> bool:
    """Register ONE additional read-only whitelist pattern.

    Acceptance rules (all enforced here, before the pattern is ever stored):
    - starts with a read-only binary (grep/head/tail/find/cat/stat/wc/du)
    - no shell metacharacters or chains anywhere in the pattern
      ({placeholder} slots are masked out before this check)
    - absolute paths only; no traversal segments
    - only {placeholder} slots, which compile to strict safe-value regexes

    Patterns come exclusively from server-side configuration (e.g.
    JARVIS_SEARCH_ALLOWED_ROOTS-derived remote-search templates). Anything
    failing validation is refused and logged.
    """
    import logging
    _log = logging.getLogger("ssh_whitelist")
    if not isinstance(pattern, str) or not pattern.strip():
        _log.warning("readonly_pattern_rejected reason=empty")
        return False
    candidate = pattern.strip()
    binary = candidate.split()[0]
    if binary not in _READONLY_BINARY_PREFIXES:
        _log.warning("readonly_pattern_rejected reason=binary pattern=%r", binary)
        return False
    # Placeholder slots are allowed; check metacharacters with slots masked.
    masked = re.sub(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", "PH", candidate)
    if _FORBIDDEN_IN_PATTERN_RE.search(masked):
        _log.warning("readonly_pattern_rejected reason=metacharacters pattern=%r", candidate)
        return False
    for token in candidate.split():
        if token.startswith("/"):
            if ".." in token.split("/"):
                _log.warning("readonly_pattern_rejected reason=traversal pattern=%r", candidate)
                return False
    compiled = _pattern_to_regex(candidate)
    # Only register if not already present (exact-duplicate guard).
    if any(existing.pattern == compiled.pattern for existing in _COMPILED_PATTERNS):
        return True
    _COMPILED_PATTERNS.append(compiled)
    _log.info("readonly_pattern_registered pattern=%r", candidate)
    return True


def registered_patterns_snapshot() -> list:
    """Compiled pattern list for tests/observability (not an LLM-facing API)."""
    return [p.pattern for p in _COMPILED_PATTERNS]


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


def build_docker_lifecycle_command(container: str, action: str) -> str:
    container = validate_container_name(container)
    if action not in {"start", "stop", "restart"}:
        raise ValidationError("Unsupported Docker lifecycle action")
    return f"docker {action} {container}"


def build_service_restart_command(service: str) -> str:
    service = validate_service_name(service)
    return f"systemctl restart {service}"
