"""Secure Tool Factory execution service.

Chain for every execution:
  active+enabled tool → RBAC → schema-validated input → safe substitution
  → ssh_whitelist final gate → unified executor (local/SSH) → timeout +
  truncation + secret redaction → audit record.

No path here can bypass the whitelist or execute an unregistered command.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from app.tool_factory.models import ToolDefinition, ToolAuditRecord, ToolStatus, utcnow
from app.tool_factory import schema as schema_validator
from app.tool_factory.validation import validate_argument_value, ToolValidationError
from app.tool_factory.redaction import redact
from app.tool_factory.repository import new_tool_id

logger = logging.getLogger("tool_factory.execution")


class ToolExecutionError(Exception):
    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        super().__init__(message)


class ToolExecutionService:
    """Executes factory tools under the full security chain."""

    def __init__(self, service, runner=None):
        self.service = service
        # runner is injectable for tests; production uses executor.run_command.
        self._runner = runner

    # ── public ───────────────────────────────────────────────────
    def execute_tool(self, name: str, arguments: dict, user_id: str,
                     organization_id: str, role_can) -> dict:
        """Execute a factory tool. `role_can` is a callable(permission) -> bool
        supplied by the caller from a TenantContext, keeping this module
        decoupled from tenancy internals."""
        return self._execute_checked(name, arguments, user_id, organization_id, role_can)

    # ── chain ────────────────────────────────────────────────────
    def _execute_checked(self, name: str, arguments: dict, user_id: str,
                         organization_id: str, role_can) -> dict:
        arguments = arguments or {}
        audit = None
        try:
            definition = self.service.get_tool(organization_id, name)
        except KeyError:
            raise ToolExecutionError("tool_not_found", f"tool '{name}' does not exist")

        active_version = self.service.repository.get_active_version(organization_id, name)
        version_label = active_version.version if active_version else ""

        # 1. lifecycle gates
        if definition.status != ToolStatus.ACTIVE.value or not definition.enabled:
            raise ToolExecutionError("tool_not_active",
                                     f"tool '{name}' is not active (status={definition.status})")
        if active_version is None:
            raise ToolExecutionError("tool_not_active", f"tool '{name}' has no active version")

        # 2. RBAC
        if not role_can(definition.required_permission):
            raise ToolExecutionError("permission_denied",
                                     f"missing permission '{definition.required_permission}'")

        # 3. input schema validation
        problems = schema_validator.validate_input(definition.input_schema, arguments)
        if problems:
            raise ToolExecutionError("invalid_input", "; ".join(problems[:5]))

        # 4. build command from the template with validated substitution
        command = self._build_command(definition, arguments)

        # 5. whitelist final gate (same boundary all JARVIS commands pass)
        from app.ssh_whitelist import is_command_allowed
        if not is_command_allowed(command):
            logger.warning("factory_command_rejected tool=%s command=%r", name, command)
            raise ToolExecutionError("command_rejected",
                                     "resolved command is not permitted by the whitelist")

        # 6. execution mode consistency
        mode = definition.execution_mode
        if mode == "ssh" and not self._ssh_configured():
            raise ToolExecutionError("mode_unavailable",
                                     "tool requires SSH mode but the VPS is not configured")

        # 7. execute through the unified executor — never direct subprocess
        runner = self._runner or self._default_runner
        started = time.monotonic()
        raw = runner(command)
        duration_ms = int((time.monotonic() - started) * 1000)

        success = bool(raw.get("success")) and raw.get("exit_code") == 0
        stdout = redact(str(raw.get("stdout", "")), max_bytes=definition.max_output_bytes)
        stderr = redact(str(raw.get("stderr", "")), max_bytes=min(2048, definition.max_output_bytes))

        error_code = "" if success else str(raw.get("error", "") or "execution_failed")[:120]

        # 8. audit (hash of arguments — never the raw values)
        audit = ToolAuditRecord(
            tool_name=name,
            tool_version=version_label,
            user_id=user_id,
            organization_id=organization_id,
            arguments_hash=hashlib.sha256(
                json.dumps(arguments, sort_keys=True, default=str).encode()
            ).hexdigest()[:32],
            execution_mode=mode if mode != "both" else self._effective_mode(),
            exit_code=raw.get("exit_code"),
            success=success,
            error_code=error_code,
            duration_ms=duration_ms,
            stdout_preview=stdout[:200],
            stderr_preview=stderr[:200],
            audit_id=new_tool_id("audit"),
        )
        self.service.repository.record_audit(organization_id, audit)

        return {
            "success": success,
            "tool": name,
            "tool_version": version_label,
            "execution_mode": audit.execution_mode,
            "duration_ms": duration_ms,
            "output": stdout,
            "stderr": stderr,
            "exit_code": raw.get("exit_code"),
            "error": None if success else error_code,
            "error_code": error_code,
            "audit_id": audit.audit_id,
        }

    # ── helpers ──────────────────────────────────────────────────
    def _build_command(self, definition: ToolDefinition, arguments: dict) -> str:
        """Substitute validated values into the template.

        Placeholders are replaced one at a time using a two-pass guard: each
        substituted value must pass validate_argument_value() first, and the
        final string must still contain no shell metacharacters.
        """
        import re
        command = definition.command_template
        placeholders = re.findall(r"\{([a-z_][a-z0-9_]*)\}", command)
        for placeholder in placeholders:
            if placeholder not in arguments:
                raise ToolExecutionError("invalid_input", f"missing argument '{placeholder}'")
            try:
                value = validate_argument_value(arguments[placeholder])
            except ValueError as exc:
                raise ToolExecutionError("invalid_input", str(exc))
            command = command.replace("{" + placeholder + "}", value)
        # Post-substitution paranoia: the resolved command must be metachar-free.
        if re.search(r"[;|&$`()<>{}!\\\\\n\r\t]", command):
            raise ToolExecutionError("invalid_input", "resolved command contains forbidden characters")
        return command

    def _effective_mode(self) -> str:
        from app.config import Config
        return "local" if Config.EXECUTION_MODE.lower() == "local" else "ssh"

    def _ssh_configured(self) -> bool:
        from app.config import Config
        return bool(Config.VPS_HOST and Config.VPS_SSH_USER)

    def _default_runner(self, command: str) -> dict:
        from app.executor import run_command
        return run_command(command)


# ── Accessor ─────────────────────────────────────────────────────

_execution_service = None


def get_tool_execution_service():
    global _execution_service
    if _execution_service is None:
        from app.tool_factory.service import get_tool_factory_service
        _execution_service = ToolExecutionService(get_tool_factory_service())
    return _execution_service


def reset_tool_execution_service() -> None:
    global _execution_service
    _execution_service = None
