"""Tool Factory repository contract + in-memory implementation.

Follows the project's existing pattern (app/repositories.py): a Protocol
boundary with an in-memory default adapter. A PostgreSQL adapter attaches
without changing callers. All queries are organization-scoped so tools
belong to the organization that created them.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Protocol

from app.tool_factory.models import ToolDefinition, ToolVersion, ToolActivationRecord, ToolAuditRecord


class ToolFactoryRepository(Protocol):
    def save_definition(self, organization_id: str, definition: ToolDefinition) -> None: ...
    def get_definition(self, organization_id: str, name: str) -> ToolDefinition | None: ...
    def list_definitions(self, organization_id: str) -> list[ToolDefinition]: ...
    def delete_definition(self, organization_id: str, name: str) -> bool: ...
    def save_version(self, organization_id: str, version: ToolVersion) -> None: ...
    def list_versions(self, organization_id: str, tool_name: str) -> list[ToolVersion]: ...
    def get_version(self, organization_id: str, tool_name: str, version: str) -> ToolVersion | None: ...
    def set_active_version(self, organization_id: str, tool_name: str, version: str) -> None: ...
    def get_active_version(self, organization_id: str, tool_name: str) -> ToolVersion | None: ...
    def record_activation(self, organization_id: str, record: ToolActivationRecord) -> None: ...
    def list_activations(self, organization_id: str, tool_name: str) -> list[ToolActivationRecord]: ...
    def record_audit(self, organization_id: str, record: ToolAuditRecord) -> None: ...
    def list_audits(self, organization_id: str, tool_name: str, limit: int = 50) -> list[ToolAuditRecord]: ...


class InMemoryToolFactoryRepository:
    """Thread-safe reference adapter; mirrored by the PostgreSQL adapter."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # {org_id: {tool_name: ToolDefinition}}
        self._definitions: dict[str, dict[str, ToolDefinition]] = {}
        # {org_id: {tool_name: {version: ToolVersion}}}
        self._versions: dict[str, dict[str, dict[str, ToolVersion]]] = {}
        self._activations: dict[str, dict[str, list[ToolActivationRecord]]] = {}
        self._audits: dict[str, dict[str, list[ToolAuditRecord]]] = {}

    # ── definitions ──────────────────────────────────────────────
    def save_definition(self, organization_id: str, definition: ToolDefinition) -> None:
        with self._lock:
            self._definitions.setdefault(organization_id, {})[definition.name] = definition

    def get_definition(self, organization_id: str, name: str) -> ToolDefinition | None:
        with self._lock:
            return self._definitions.get(organization_id, {}).get(name)

    def list_definitions(self, organization_id: str) -> list[ToolDefinition]:
        with self._lock:
            return list(self._definitions.get(organization_id, {}).values())

    def delete_definition(self, organization_id: str, name: str) -> bool:
        with self._lock:
            org_tools = self._definitions.get(organization_id, {})
            if name in org_tools:
                del org_tools[name]
                return True
            return False

    # ── versions ─────────────────────────────────────────────────
    def save_version(self, organization_id: str, version: ToolVersion) -> None:
        with self._lock:
            tool_versions = self._versions.setdefault(organization_id, {}).setdefault(version.tool_name, {})
            tool_versions[version.version] = version

    def list_versions(self, organization_id: str, tool_name: str) -> list[ToolVersion]:
        with self._lock:
            return list(self._versions.get(organization_id, {}).get(tool_name, {}).values())

    def get_version(self, organization_id: str, tool_name: str, version: str) -> ToolVersion | None:
        with self._lock:
            return self._versions.get(organization_id, {}).get(tool_name, {}).get(version)

    def set_active_version(self, organization_id: str, tool_name: str, version: str) -> None:
        """Enforce single-active-version within the repository lock."""
        with self._lock:
            tool_versions = self._versions.get(organization_id, {}).get(tool_name, {})
            if version not in tool_versions:
                raise KeyError(f"version '{version}' not found for tool '{tool_name}'")
            for candidate in tool_versions.values():
                candidate.active = candidate.version == version
            definition = self._definitions.get(organization_id, {}).get(tool_name)
            if definition is not None:
                definition.version = version

    def get_active_version(self, organization_id: str, tool_name: str) -> ToolVersion | None:
        with self._lock:
            for candidate in self._versions.get(organization_id, {}).get(tool_name, {}).values():
                if candidate.active:
                    return candidate
            return None

    # ── activation history ───────────────────────────────────────
    def record_activation(self, organization_id: str, record: ToolActivationRecord) -> None:
        with self._lock:
            self._activations.setdefault(organization_id, {}).setdefault(record.tool_name, []).append(record)

    def list_activations(self, organization_id: str, tool_name: str) -> list[ToolActivationRecord]:
        with self._lock:
            return list(self._activations.get(organization_id, {}).get(tool_name, []))

    # ── execution audit ──────────────────────────────────────────
    def record_audit(self, organization_id: str, record: ToolAuditRecord) -> None:
        with self._lock:
            records = self._audits.setdefault(organization_id, {}).setdefault(record.tool_name, [])
            records.append(record)
            # Bounded audit history per tool: keep the most recent 200.
            if len(records) > 200:
                del records[:-200]

    def list_audits(self, organization_id: str, tool_name: str, limit: int = 50) -> list[ToolAuditRecord]:
        with self._lock:
            records = self._audits.get(organization_id, {}).get(tool_name, [])
            return list(reversed(records[-max(1, min(limit, 200)):]))


def new_tool_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def dump_public(value: Any) -> Any:
    """Best-effort JSON-safe view of model objects for API responses."""
    if hasattr(value, "to_public"):
        return value.to_public()
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(value)
    return value
