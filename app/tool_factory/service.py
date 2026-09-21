"""Tool Factory service: creation, validation, approval, versioning.

Orchestrates the repository and validation layers. Every mutating
operation requires an actor with the tools.manage permission (checked by
the API layer); this module additionally records who did what and why.
"""
from __future__ import annotations

import logging
import re

from app.tool_factory.models import (
    ToolDefinition, ToolVersion, ToolActivationRecord, ToolStatus, utcnow,
)
from app.tool_factory.models import ValidationIssue
from app.tool_factory.validation import (
    validate_tool_definition, calculate_tool_checksum, ToolValidationError,
)
from app.tool_factory.repository import InMemoryToolFactoryRepository

logger = logging.getLogger("tool_factory")

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
MAX_VERSIONS_PER_TOOL = 50


class ToolFactoryService:
    """Creation + lifecycle orchestration. Storage-agnostic."""

    def __init__(self, repository=None):
        self.repository = repository or InMemoryToolFactoryRepository()

    # ── creation ─────────────────────────────────────────────────
    def create_tool(self, organization_id: str, payload: dict, actor: str) -> ToolDefinition:
        """Validate and store a new tool definition (status=draft until approved)."""
        definition = ToolDefinition(
            name=payload.get("name", ""),
            display_name=payload.get("display_name", "") or payload.get("name", ""),
            description=payload.get("description", ""),
            category=payload.get("category", "general"),
            version=payload.get("version", "1.0.0"),
            author=actor or payload.get("author", "unknown"),
            input_schema=payload.get("input_schema", {}),
            output_schema=payload.get("output_schema", {}),
            execution_mode=payload.get("execution_mode", "both"),
            command_template=payload.get("command_template", ""),
            allowed_paths=payload.get("allowed_paths", []),
            timeout_seconds=payload.get("timeout_seconds", 10),
            max_output_bytes=payload.get("max_output_bytes", 16384),
            required_permission=payload.get("required_permission", "tools.execute"),
            read_only=payload.get("read_only", True),
            provenance=payload.get("provenance", "user"),
        )
        issues = validate_tool_definition(definition)
        if issues:
            raise ToolValidationError(issues)
        existing = self.repository.get_definition(organization_id, definition.name)
        if existing is not None:
            raise ToolValidationError([
                ValidationIssue("name", "duplicate", "tool already exists"),
            ])
        definition.checksum = calculate_tool_checksum(definition)
        definition.status = ToolStatus.VALIDATED.value
        self.repository.save_definition(organization_id, definition)
        self._snapshot_version(organization_id, definition, actor, "initial version")
        logger.info("tool_created org=%s tool=%s version=%s actor=%s",
                    organization_id, definition.name, definition.version, actor)
        return definition

    def validate_existing(self, organization_id: str, name: str) -> list[dict]:
        """Re-run validation on a stored definition; returns issue dicts."""
        definition = self.repository.get_definition(organization_id, name)
        if definition is None:
            raise KeyError(f"tool '{name}' not found")
        return [issue.to_dict() for issue in validate_tool_definition(definition)]

    # ── approval ─────────────────────────────────────────────────
    def approve_tool_definition(self, organization_id: str, name: str, actor: str) -> ToolDefinition:
        definition = self._require(organization_id, name)
        issues = validate_tool_definition(definition)
        if issues:
            raise ToolValidationError(issues)
        if definition.status not in {ToolStatus.VALIDATED.value, ToolStatus.REJECTED.value}:
            raise ValueError(f"tool '{name}' in status '{definition.status}' cannot be approved")
        definition.status = ToolStatus.APPROVED.value
        definition.updated_at = utcnow()
        self.repository.save_definition(organization_id, definition)
        logger.info("tool_approved org=%s tool=%s actor=%s", organization_id, name, actor)
        return definition

    def reject_tool_definition(self, organization_id: str, name: str, actor: str, reason: str = "") -> ToolDefinition:
        definition = self._require(organization_id, name)
        definition.status = ToolStatus.REJECTED.value
        self.repository.save_definition(organization_id, definition)
        logger.info("tool_rejected org=%s tool=%s actor=%s reason=%s", organization_id, name, actor, reason)
        return definition

    # ── versioning ───────────────────────────────────────────────
    def create_tool_version(self, organization_id: str, name: str, payload: dict,
                            actor: str, change_reason: str = "") -> ToolVersion:
        current = self._require(organization_id, name)
        if not VERSION_RE.match(payload.get("version", "")):
            raise ValueError("version must be semantic (major.minor.patch)")
        updated = ToolDefinition(
            name=current.name,
            display_name=payload.get("display_name", current.display_name),
            description=payload.get("description", current.description),
            category=payload.get("category", current.category),
            version=payload["version"],
            author=actor or current.author,
            input_schema=payload.get("input_schema", current.input_schema),
            output_schema=payload.get("output_schema", current.output_schema),
            execution_mode=payload.get("execution_mode", current.execution_mode),
            command_template=payload.get("command_template", current.command_template),
            allowed_paths=payload.get("allowed_paths", list(current.allowed_paths)),
            timeout_seconds=payload.get("timeout_seconds", current.timeout_seconds),
            max_output_bytes=payload.get("max_output_bytes", current.max_output_bytes),
            required_permission=payload.get("required_permission", current.required_permission),
            read_only=payload.get("read_only", current.read_only),
            provenance=current.provenance,
        )
        issues = validate_tool_definition(updated)
        if issues:
            raise ToolValidationError(issues)
        if self.repository.get_version(organization_id, name, updated.version) is not None:
            raise ValueError(f"version '{updated.version}' already exists for tool '{name}'")
        updated.checksum = calculate_tool_checksum(updated)
        # A new version keeps the tool's lifecycle state: an ACTIVE tool stays
        # active on its current version (the new version is snapshotted but
        # not switched), while a draft/validated tool moves forward only via
        # the approval + activation gates. This prevents the version bump from
        # silently bypassing approval on an already-active tool: activation of
        # the NEW version still requires the tool to be approved, which it is,
        # and the version switch itself is the audited governance event.
        updated.status = current.status
        updated.enabled = current.enabled
        self.repository.save_definition(organization_id, updated)
        version = self._snapshot_version(organization_id, updated, actor, change_reason)
        logger.info("tool_version_created org=%s tool=%s version=%s actor=%s",
                    organization_id, name, updated.version, actor)
        return version

    def list_tool_versions(self, organization_id: str, name: str) -> list[ToolVersion]:
        self._require(organization_id, name)
        return self.repository.list_versions(organization_id, name)

    def get_version_detail(self, organization_id: str, name: str, version: str) -> ToolVersion:
        found = self.repository.get_version(organization_id, name, version)
        if found is None:
            raise KeyError(f"version '{version}' of tool '{name}' not found")
        return found

    def compare_tool_versions(self, organization_id: str, name: str,
                              version_a: str, version_b: str) -> dict:
        a = self.get_version_detail(organization_id, name, version_a).definition
        b = self.get_version_detail(organization_id, name, version_b).definition
        changed = {}
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                changed[key] = {"from": a.get(key), "to": b.get(key)}
        return {"tool": name, "version_a": version_a, "version_b": version_b, "changes": changed}

    # ── activation ───────────────────────────────────────────────
    def activate_tool_version(self, organization_id: str, name: str, version: str,
                              actor: str, reason: str = "") -> dict:
        definition = self._require(organization_id, name)
        target = self.repository.get_version(organization_id, name, version)
        if target is None:
            raise KeyError(f"version '{version}' of tool '{name}' not found")
        # Only approved definitions may be activated; approval requires a
        # fresh validation pass, so unvalidated tools are never executable.
        if definition.status not in {ToolStatus.APPROVED.value, ToolStatus.ACTIVE.value, ToolStatus.DISABLED.value}:
            raise PermissionError(f"tool '{name}' must be approved before activation (status={definition.status})")
        previous = self.repository.get_active_version(organization_id, name)
        previous_version = previous.version if previous else ""
        self.repository.set_active_version(organization_id, name, version)
        definition.status = ToolStatus.ACTIVE.value
        definition.enabled = True
        definition.updated_at = target.created_at or definition.updated_at
        self.repository.save_definition(organization_id, definition)
        self.repository.record_activation(organization_id, ToolActivationRecord(
            tool_name=name, version=version, action="activated", actor=actor,
            reason=reason, previous_version=previous_version,
        ))
        logger.info("tool_activated org=%s tool=%s version=%s actor=%s", organization_id, name, version, actor)
        return {"tool": name, "activated_version": version, "previous_version": previous_version}

    def deactivate_tool_version(self, organization_id: str, name: str, actor: str, reason: str = "") -> dict:
        definition = self._require(organization_id, name)
        active = self.repository.get_active_version(organization_id, name)
        if active is None:
            raise ValueError(f"tool '{name}' has no active version")
        # Deactivate by disabling the definition; the version record stays
        # for rollback history.
        definition.enabled = False
        definition.status = ToolStatus.DISABLED.value
        self.repository.save_definition(organization_id, definition)
        self.repository.record_activation(organization_id, ToolActivationRecord(
            tool_name=name, version=active.version, action="deactivated",
            actor=actor, reason=reason,
            previous_version=active.version,
        ))
        logger.info("tool_deactivated org=%s tool=%s actor=%s", organization_id, name, actor)
        return {"tool": name, "deactivated_version": active.version}

    def rollback_tool_version(self, organization_id: str, name: str,
                              actor: str, reason: str = "") -> dict:
        """Roll back to the most recent prior version with an activation record."""
        definition = self._require(organization_id, name)
        history = [record for record in self.repository.list_activations(organization_id, name)
                   if record.action in {"activated", "rolled_back"}]
        if not history:
            raise ValueError(f"no activation history for tool '{name}' to roll back to")
        # history is newest-first from the repository
        current_active = self.repository.get_active_version(organization_id, name)
        current_version = current_active.version if current_active else ""
        target_version = ""
        for record in history:
            if record.version != current_version:
                target_version = record.version
                break
        if not target_version:
            raise ValueError(f"no prior version of tool '{name}' available to roll back to")
        result = self.activate_tool_version(organization_id, name, target_version, actor,
                                            reason=f"rollback: {reason}" if reason else "rollback")
        self.repository.record_activation(organization_id, ToolActivationRecord(
            tool_name=name, version=target_version, action="rolled_back", actor=actor,
            reason=reason, previous_version=current_version,
        ))
        return {**result, "rolled_back_from": current_version}

    def set_enabled(self, organization_id: str, name: str, enabled: bool, actor: str) -> ToolDefinition:
        definition = self._require(organization_id, name)
        if enabled and self.repository.get_active_version(organization_id, name) is None:
            raise ValueError(f"tool '{name}' has no active version; activate one first")
        definition.enabled = enabled
        if enabled:
            definition.status = ToolStatus.ACTIVE.value
        elif definition.status == ToolStatus.ACTIVE.value:
            definition.status = ToolStatus.DISABLED.value
        self.repository.save_definition(organization_id, definition)
        logger.info("tool_enabled_state org=%s tool=%s enabled=%s actor=%s",
                    organization_id, name, enabled, actor)
        return definition

    # ── queries ──────────────────────────────────────────────────
    def get_tool(self, organization_id: str, name: str) -> ToolDefinition:
        return self._require(organization_id, name)

    def list_tools(self, organization_id: str) -> list[ToolDefinition]:
        return self.repository.list_definitions(organization_id)

    def list_audit(self, organization_id: str, name: str, limit: int = 50):
        self._require(organization_id, name)
        return self.repository.list_audits(organization_id, name, limit)

    def list_activation_history(self, organization_id: str, name: str):
        self._require(organization_id, name)
        return self.repository.list_activations(organization_id, name)

    # ── internals ────────────────────────────────────────────────
    def _require(self, organization_id: str, name: str) -> ToolDefinition:
        definition = self.repository.get_definition(organization_id, name)
        if definition is None:
            raise KeyError(f"tool '{name}' not found")
        return definition

    def _snapshot_version(self, organization_id: str, definition: ToolDefinition,
                          actor: str, change_reason: str) -> ToolVersion:
        existing = self.repository.list_versions(organization_id, definition.name)
        if len(existing) >= MAX_VERSIONS_PER_TOOL:
            raise ValueError(f"tool '{definition.name}' has reached the version limit ({MAX_VERSIONS_PER_TOOL})")
        previous = self.repository.get_active_version(organization_id, definition.name)
        version = ToolVersion(
            tool_name=definition.name,
            version=definition.version,
            definition=definition.to_public(),
            checksum=definition.checksum,
            created_by=actor,
            previous_version=previous.version if previous else "",
            change_reason=change_reason,
        )
        self.repository.save_version(organization_id, version)
        return version


# ── Accessor ─────────────────────────────────────────────────────

_service = None


def get_tool_factory_service():
    """Return the process-wide service; PostgreSQL-backed when DB is enabled."""
    global _service
    if _service is None:
        from app.config import Config
        if Config.DATABASE_ENABLED and Config.DATABASE_URL:
            from app.tool_factory.pg_repository import PostgresToolFactoryRepository
            _service = ToolFactoryService(PostgresToolFactoryRepository(Config.DATABASE_URL))
            logger.info("tool_factory_repository=postgres")
        else:
            _service = ToolFactoryService()
            logger.info("tool_factory_repository=memory")
    return _service


def reset_tool_factory_service() -> None:
    """Test hook: drop the singleton so a fresh repository is used."""
    global _service
    _service = None
