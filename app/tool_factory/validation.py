"""Tool definition validation — the gate that keeps the factory safe.

A definition only becomes a tool if it passes every rule here:
strict name, valid schemas, a command template that cannot smuggle shell
syntax, an explicit binary allowlist, bounded execution, and read-only
enforced. Anything else is rejected with structured issues.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.tool_factory.models import ToolDefinition, ValidationIssue, ExecutionMode
from app.tool_factory import schema as schema_validator

# Strict safe tool name: lowercase snake_case, 3-64 chars.
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")

# Safe argument value pattern (mirrors ssh_whitelist._NAME_RE discipline).
_SAFE_VALUE_RE = re.compile(r"^[\w.\-]{1,128}$")

# Binaries a generated tool may invoke, with allowed argument prefixes.
ALLOWED_BINARIES: dict[str, tuple[str, ...]] = {
    "docker": ("ps", "images", "inspect", "stats", "logs", "version"),
    "kubectl": ("get", "cluster-info", "version",),
    "systemctl": ("status",),
    "grep": (),
    "head": (),
    "tail": (),
    "cat": (),
    "find": (),
    "stat": (),
    "df": (),
    "free": (),
    "uptime": (),
    "nproc": (),
    "ps": (),
    "wc": (),
    "du": (),
}

# Tokens that must never appear anywhere in a definition's command template.
DENIED_TOKENS = (
    "rm", "mv", "cp", "chmod", "chown", "mkfs", "shutdown", "reboot", "dd",
    "sudo", "su ", "curl", "wget", "bash", "sh ", "zsh", "python", "perl",
    "ruby", "eval", "exec ", "xargs", "tee ", "apt", "apt-get", "yum", "pip",
    "npm", "docker exec", "docker rm", "docker kill", "docker prune",
    "docker system", "docker stop", "docker start", "docker restart",
    "docker create", "docker run", "docker commit", "docker cp",
    "kubectl delete", "kubectl apply", "kubectl exec", "kubectl create",
    "kubectl patch", "kubectl edit", "kubectl scale", "kubectl rollout",
    "kubectl run", "systemctl stop", "systemctl disable", "systemctl mask",
    "systemctl restart", "systemctl start", "systemctl enable",
    "iptables", "ufw", "firewall", "useradd", "userdel", "passwd ",
    "history -c", "base64 ", "nc ", "ncat", "socat", "ssh ", "scp ",
)

# Shell metacharacters — the command template is a plain word+placeholder
# sequence, never a shell expression.
_SHELL_META_RE = re.compile(r"[;|&$`()<>{}\[\]!\\\\*?~\n\r\t]")

MAX_TIMEOUT_SECONDS = 60
MAX_OUTPUT_BYTES = 65536
MAX_PATHS = 10
MAX_TEMPLATE_WORDS = 24

_PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
_ABSOLUTE_PATH_RE = re.compile(r"^/[\w./\-]*$")


class ToolValidationError(ValueError):
    """Raised by create-time helpers when a definition is rejected."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        summary = "; ".join(f"{issue.field}: {issue.message}" for issue in issues)
        super().__init__(summary or "tool definition rejected")


def validate_tool_name(name: object) -> list[ValidationIssue]:
    if not isinstance(name, str) or not TOOL_NAME_RE.match(name or ""):
        return [ValidationIssue("name", "invalid_name",
                "must be lowercase snake_case, 3-64 characters (^[a-z][a-z0-9_]{2,63}$)")]
    return []


def validate_tool_schema(input_schema: object, output_schema: object) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(input_schema, dict):
        issues.append(ValidationIssue("input_schema", "not_object", "input schema must be an object"))
    else:
        for problem in schema_validator.validate_schema(input_schema, "input_schema"):
            issues.append(ValidationIssue("input_schema", "invalid_schema", problem))
    if not isinstance(output_schema, dict):
        issues.append(ValidationIssue("output_schema", "not_object", "output schema must be an object"))
    else:
        for problem in schema_validator.validate_schema(output_schema, "output_schema"):
            issues.append(ValidationIssue("output_schema", "invalid_schema", problem))
    return issues


def validate_command_template(template: object, input_schema: dict | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(template, str) or not template.strip():
        return [ValidationIssue("command_template", "empty", "command template is required")]

    template = template.strip()
    if len(template) > 500:
        issues.append(ValidationIssue("command_template", "too_long", "template exceeds 500 characters"))
    # Placeholder slots like {container} are checked separately (declaration +
    # strict substitution). Mask them before the shell-metacharacter scan.
    masked = _PLACEHOLDER_RE.sub("PH", template)
    if _SHELL_META_RE.search(masked):
        issues.append(ValidationIssue("command_template", "shell_metacharacters",
                      "pipe, redirect, substitution, glob, brace, or chain characters are forbidden"))
    lowered = template.lower()
    for token in DENIED_TOKENS:
        if token in lowered:
            issues.append(ValidationIssue("command_template", "denied_token",
                          f"'{token.strip()}' is not permitted in generated tools"))
    words = template.split()
    if len(words) > MAX_TEMPLATE_WORDS:
        issues.append(ValidationIssue("command_template", "too_complex",
                      f"template exceeds {MAX_TEMPLATE_WORDS} words"))
    if words:
        binary = words[0]
        if binary not in ALLOWED_BINARIES:
            issues.append(ValidationIssue("command_template", "binary_not_allowed",
                          f"binary '{binary}' is not on the allowed list"))
        else:
            allowed_prefixes = ALLOWED_BINARIES[binary]
            if allowed_prefixes:
                # The first non-placeholder, non-flag argument must be an allowed subcommand.
                subcommand = next((w for w in words[1:] if not w.startswith("-") and not _PLACEHOLDER_RE.fullmatch(w)), None)
                if subcommand is not None and subcommand not in allowed_prefixes:
                    issues.append(ValidationIssue("command_template", "subcommand_not_allowed",
                                  f"'{binary} {subcommand}' is not permitted; allowed: {', '.join(allowed_prefixes)}"))

    # Every placeholder must exist in the declared input schema properties.
    declared = set()
    if isinstance(input_schema, dict):
        declared = set((input_schema.get("properties") or {}).keys())
    for match in _PLACEHOLDER_RE.finditer(template):
        placeholder = match.group(1)
        if placeholder not in declared:
            issues.append(ValidationIssue("command_template", "undeclared_placeholder",
                          f"{{{placeholder}}} is not declared in the input schema"))
    return issues


def validate_allowed_paths(paths: object, command_template: str = "") -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if paths is None:
        paths = []
    if not isinstance(paths, list):
        return [ValidationIssue("allowed_paths", "not_list", "allowed_paths must be a list")]
    if len(paths) > MAX_PATHS:
        issues.append(ValidationIssue("allowed_paths", "too_many", f"at most {MAX_PATHS} paths are allowed"))
    for path in paths[:MAX_PATHS]:
        if not isinstance(path, str) or not _ABSOLUTE_PATH_RE.match(path or ""):
            issues.append(ValidationIssue("allowed_paths", "invalid_path",
                          f"'{path}' must be an absolute path of safe characters"))
        elif ".." in path.split("/"):
            issues.append(ValidationIssue("allowed_paths", "path_traversal",
                          f"'{path}' contains traversal segments"))
    if paths and command_template and not any(path in command_template for path in paths):
        # Paths must be baked into the template, never supplied as arguments.
        issues.append(ValidationIssue("allowed_paths", "path_not_in_template",
                      "allowed paths must appear literally in the command template, not be runtime arguments"))
    return issues


def validate_execution_mode(mode: object) -> list[ValidationIssue]:
    if not isinstance(mode, str) or mode not in {item.value for item in ExecutionMode}:
        return [ValidationIssue("execution_mode", "invalid_mode",
                "must be one of: local, ssh, both")]
    return []


def validate_bounds(timeout: object, max_output: object) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not (1 <= timeout <= MAX_TIMEOUT_SECONDS):
        issues.append(ValidationIssue("timeout_seconds", "invalid_timeout",
                      f"must be an integer between 1 and {MAX_TIMEOUT_SECONDS}"))
    if not isinstance(max_output, int) or isinstance(max_output, bool) or not (256 <= max_output <= MAX_OUTPUT_BYTES):
        issues.append(ValidationIssue("max_output_bytes", "invalid_limit",
                      f"must be an integer between 256 and {MAX_OUTPUT_BYTES}"))
    return issues


def validate_read_only(read_only: object) -> list[ValidationIssue]:
    if read_only is not True:
        return [ValidationIssue("read_only", "must_be_read_only",
                "the Tool Factory can only create read-only tools")]
    return []


def validate_tool_definition(definition: ToolDefinition) -> list[ValidationIssue]:
    """Full validation pass. Returns structured issues; empty list = valid."""
    issues: list[ValidationIssue] = []
    issues.extend(validate_tool_name(definition.name))
    issues.extend(validate_tool_schema(definition.input_schema, definition.output_schema))
    issues.extend(validate_command_template(definition.command_template, definition.input_schema))
    issues.extend(validate_allowed_paths(definition.allowed_paths, definition.command_template))
    issues.extend(validate_execution_mode(definition.execution_mode))
    issues.extend(validate_bounds(definition.timeout_seconds, definition.max_output_bytes))
    issues.extend(validate_read_only(definition.read_only))
    if not isinstance(definition.description, str) or not definition.description.strip():
        issues.append(ValidationIssue("description", "empty", "description is required"))
    if not isinstance(definition.category, str) or not definition.category.strip():
        issues.append(ValidationIssue("category", "empty", "category is required"))
    if not isinstance(definition.required_permission, str) or not definition.required_permission.strip():
        issues.append(ValidationIssue("required_permission", "empty", "required_permission is required"))
    return issues


def calculate_tool_checksum(definition: ToolDefinition) -> str:
    """Stable sha256 over the canonical JSON of the definition's semantic body."""
    body = {
        "name": definition.name,
        "display_name": definition.display_name,
        "description": definition.description,
        "category": definition.category,
        "version": definition.version,
        "input_schema": definition.input_schema,
        "output_schema": definition.output_schema,
        "execution_mode": definition.execution_mode,
        "command_template": definition.command_template,
        "allowed_paths": sorted(definition.allowed_paths),
        "timeout_seconds": definition.timeout_seconds,
        "max_output_bytes": definition.max_output_bytes,
        "required_permission": definition.required_permission,
        "read_only": definition.read_only,
        "author": definition.author,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_argument_value(value: object) -> str:
    """Validate and return a safe substitution value for a template placeholder.

    Raises ToolValidationError-shaped ValueError on anything unsafe. This is
    the per-execution defense that keeps runtime args from introducing shell
    syntax even if a schema pattern were mis-configured.
    """
    if isinstance(value, bool):
        raise ValueError("boolean arguments cannot be substituted into commands")
    if isinstance(value, int):
        if not (0 <= value <= 100000):
            raise ValueError("integer argument out of safe range")
        return str(value)
    if isinstance(value, str) and _SAFE_VALUE_RE.match(value):
        return value
    raise ValueError(f"unsafe argument value: {value!r}")
