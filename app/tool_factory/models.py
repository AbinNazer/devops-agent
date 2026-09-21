"""Tool Factory data models.

Every generated tool is a first-class record: validated, checksum-bound,
versioned, and auditable. Definitions are read-only by construction —
the factory cannot represent a mutating tool (validation.py enforces this
independently; models here simply carry the flag).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ExecutionMode(str, Enum):
    LOCAL = "local"
    SSH = "ssh"
    BOTH = "both"


class ToolStatus(str, Enum):
    DRAFT = "draft"            # created, not yet validated
    VALIDATED = "validated"    # passed validation, awaiting approval
    APPROVED = "approved"      # approved, may be activated
    REJECTED = "rejected"      # failed validation or review
    ACTIVE = "active"          # has an active version and is executable
    DISABLED = "disabled"      # active version exists but toggled off


@dataclass
class ToolDefinition:
    name: str
    display_name: str
    description: str
    category: str
    version: str
    author: str
    input_schema: dict
    output_schema: dict
    execution_mode: str                      # ExecutionMode value
    command_template: str                    # e.g. "docker inspect {container}"
    allowed_paths: list[str] = field(default_factory=list)
    timeout_seconds: int = 10
    max_output_bytes: int = 16384
    required_permission: str = "tools.execute"
    read_only: bool = True
    enabled: bool = False
    status: str = ToolStatus.DRAFT.value
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    checksum: str = ""
    provenance: str = "builtin"

    def to_public(self) -> dict:
        """Serializable view for APIs — never includes secrets (there are none by design)."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "version": self.version,
            "author": self.author,
            "execution_mode": self.execution_mode,
            "command_template": self.command_template,
            "allowed_paths": self.allowed_paths,
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "required_permission": self.required_permission,
            "read_only": self.read_only,
            "enabled": self.enabled,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "checksum": self.checksum,
            "provenance": self.provenance,
        }


@dataclass
class ToolVersion:
    """Immutable snapshot of a definition at a specific version."""
    tool_name: str
    version: str
    definition: dict                       # full ToolDefinition.to_public() snapshot
    checksum: str
    created_by: str
    created_at: datetime = field(default_factory=utcnow)
    previous_version: str = ""
    change_reason: str = ""
    active: bool = False


@dataclass
class ToolActivationRecord:
    tool_name: str
    version: str
    action: str                            # activated | deactivated | rolled_back
    actor: str
    reason: str
    previous_version: str
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class ToolAuditRecord:
    tool_name: str
    tool_version: str
    user_id: str
    organization_id: str
    arguments_hash: str                    # sha256 of arguments — never raw args
    execution_mode: str
    exit_code: int | None
    success: bool
    error_code: str
    duration_ms: int
    stdout_preview: str = ""               # redacted, truncated
    stderr_preview: str = ""               # redacted, truncated
    audit_id: str = ""
    timestamp: datetime = field(default_factory=utcnow)

    def to_public(self) -> dict:
        return {
            "audit_id": self.audit_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "arguments_hash": self.arguments_hash,
            "execution_mode": self.execution_mode,
            "exit_code": self.exit_code,
            "success": self.success,
            "error_code": self.error_code,
            "duration_ms": self.duration_ms,
            "stdout_preview": self.stdout_preview,
            "stderr_preview": self.stderr_preview,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ValidationIssue:
    field: str
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"field": self.field, "code": self.code, "message": self.message}
