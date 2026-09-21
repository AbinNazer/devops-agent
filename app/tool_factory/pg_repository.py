"""PostgreSQL adapter for the Tool Factory.

Only attached when DATABASE_ENABLED=true and DATABASE_URL is set; the
in-memory adapter remains the default so the application runs without a
database (matching the existing repositories/postgres conventions).
"""
from __future__ import annotations

import json
from typing import Any

from app.tool_factory.models import (
    ToolDefinition, ToolVersion, ToolActivationRecord, ToolAuditRecord, utcnow,
)


def _row_to_definition(row: tuple) -> ToolDefinition:
    (name, display_name, description, category, version, author, input_schema,
     output_schema, execution_mode, command_template, allowed_paths,
     timeout_seconds, max_output_bytes, required_permission, read_only,
     enabled, status, provenance, checksum, created_at, updated_at) = row
    return ToolDefinition(
        name=name, display_name=display_name, description=description,
        category=category, version=version, author=author,
        input_schema=input_schema if isinstance(input_schema, dict) else json.loads(input_schema or "{}"),
        output_schema=output_schema if isinstance(output_schema, dict) else json.loads(output_schema or "{}"),
        execution_mode=execution_mode, command_template=command_template,
        allowed_paths=allowed_paths if isinstance(allowed_paths, list) else json.loads(allowed_paths or "[]"),
        timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes,
        required_permission=required_permission, read_only=read_only,
        enabled=enabled, status=status, provenance=provenance, checksum=checksum,
        created_at=created_at, updated_at=updated_at,
    )


class PostgresToolFactoryRepository:
    """Same contract as InMemoryToolFactoryRepository, backed by PostgreSQL.

    Every query is organization-scoped; there is no cross-tenant read path.
    """

    def __init__(self, database_url: str):
        from app.postgres import connect
        self._connect = connect
        self.database_url = database_url

    # ── definitions ──────────────────────────────────────────────
    def save_definition(self, organization_id: str, definition: ToolDefinition) -> None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tool_definitions (
                        organization_id, name, display_name, description, category, version,
                        author, input_schema, output_schema, execution_mode, command_template,
                        allowed_paths, timeout_seconds, max_output_bytes, required_permission,
                        read_only, enabled, status, provenance, checksum, created_at, updated_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (organization_id, name) DO UPDATE SET
                        display_name=EXCLUDED.display_name, description=EXCLUDED.description,
                        category=EXCLUDED.category, version=EXCLUDED.version, author=EXCLUDED.author,
                        input_schema=EXCLUDED.input_schema, output_schema=EXCLUDED.output_schema,
                        execution_mode=EXCLUDED.execution_mode, command_template=EXCLUDED.command_template,
                        allowed_paths=EXCLUDED.allowed_paths, timeout_seconds=EXCLUDED.timeout_seconds,
                        max_output_bytes=EXCLUDED.max_output_bytes,
                        required_permission=EXCLUDED.required_permission, read_only=EXCLUDED.read_only,
                        enabled=EXCLUDED.enabled, status=EXCLUDED.status, provenance=EXCLUDED.provenance,
                        checksum=EXCLUDED.checksum, updated_at=CURRENT_TIMESTAMP
                    """,
                    (organization_id, definition.name, definition.display_name, definition.description,
                     definition.category, definition.version, definition.author,
                     json.dumps(definition.input_schema), json.dumps(definition.output_schema),
                     definition.execution_mode, definition.command_template,
                     json.dumps(definition.allowed_paths), definition.timeout_seconds,
                     definition.max_output_bytes, definition.required_permission,
                     definition.read_only, definition.enabled, definition.status,
                     definition.provenance, definition.checksum, definition.created_at, definition.updated_at),
                )
            connection.commit()

    def get_definition(self, organization_id: str, name: str) -> ToolDefinition | None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name, display_name, description, category, version, author, input_schema, "
                    "output_schema, execution_mode, command_template, allowed_paths, timeout_seconds, "
                    "max_output_bytes, required_permission, read_only, enabled, status, provenance, "
                    "checksum, created_at, updated_at FROM tool_definitions "
                    "WHERE organization_id = %s AND name = %s",
                    (organization_id, name),
                )
                row = cursor.fetchone()
        return _row_to_definition(row) if row else None

    def list_definitions(self, organization_id: str) -> list[ToolDefinition]:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT name, display_name, description, category, version, author, input_schema, "
                    "output_schema, execution_mode, command_template, allowed_paths, timeout_seconds, "
                    "max_output_bytes, required_permission, read_only, enabled, status, provenance, "
                    "checksum, created_at, updated_at FROM tool_definitions "
                    "WHERE organization_id = %s ORDER BY name",
                    (organization_id,),
                )
                rows = cursor.fetchall()
        return [_row_to_definition(row) for row in rows]

    def delete_definition(self, organization_id: str, name: str) -> bool:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM tool_definitions WHERE organization_id = %s AND name = %s",
                    (organization_id, name),
                )
                deleted = cursor.rowcount > 0
            connection.commit()
        return deleted

    # ── versions ─────────────────────────────────────────────────
    def save_version(self, organization_id: str, version: ToolVersion) -> None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO tool_versions (organization_id, tool_name, version, definition, checksum,
                        created_by, previous_version, change_reason, active, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (organization_id, tool_name, version) DO UPDATE SET
                        definition=EXCLUDED.definition, checksum=EXCLUDED.checksum,
                        change_reason=EXCLUDED.change_reason, active=EXCLUDED.active
                    """,
                    (organization_id, version.tool_name, version.version,
                     json.dumps(version.definition, default=str), version.checksum,
                     version.created_by, version.previous_version, version.change_reason,
                     version.active, version.created_at),
                )
            connection.commit()

    def _row_to_version(self, row: tuple) -> ToolVersion:
        (tool_name, version, definition, checksum, created_by, previous_version,
         change_reason, active, created_at) = row
        return ToolVersion(
            tool_name=tool_name, version=version,
            definition=definition if isinstance(definition, dict) else json.loads(definition or "{}"),
            checksum=checksum, created_by=created_by, previous_version=previous_version,
            change_reason=change_reason, active=active, created_at=created_at,
        )

    _VERSION_COLUMNS = ("tool_name, version, definition, checksum, created_by, "
                        "previous_version, change_reason, active, created_at")

    def list_versions(self, organization_id: str, tool_name: str) -> list[ToolVersion]:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._VERSION_COLUMNS} FROM tool_versions "
                    "WHERE organization_id = %s AND tool_name = %s ORDER BY created_at DESC",
                    (organization_id, tool_name),
                )
                rows = cursor.fetchall()
        return [self._row_to_version(row) for row in rows]

    def get_version(self, organization_id: str, tool_name: str, version: str) -> ToolVersion | None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._VERSION_COLUMNS} FROM tool_versions "
                    "WHERE organization_id = %s AND tool_name = %s AND version = %s",
                    (organization_id, tool_name, version),
                )
                row = cursor.fetchone()
        return self._row_to_version(row) if row else None

    def set_active_version(self, organization_id: str, tool_name: str, version: str) -> None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM tool_versions WHERE organization_id=%s AND tool_name=%s AND version=%s",
                               (organization_id, tool_name, version))
                if cursor.fetchone() is None:
                    raise KeyError(f"version '{version}' not found for tool '{tool_name}'")
                cursor.execute(
                    "UPDATE tool_versions SET active = (version = %s) "
                    "WHERE organization_id = %s AND tool_name = %s",
                    (version, organization_id, tool_name),
                )
                cursor.execute(
                    "UPDATE tool_definitions SET version = %s, updated_at = CURRENT_TIMESTAMP "
                    "WHERE organization_id = %s AND name = %s",
                    (version, organization_id, tool_name),
                )
            connection.commit()

    def get_active_version(self, organization_id: str, tool_name: str) -> ToolVersion | None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {self._VERSION_COLUMNS} FROM tool_versions "
                    "WHERE organization_id = %s AND tool_name = %s AND active = TRUE",
                    (organization_id, tool_name),
                )
                row = cursor.fetchone()
        return self._row_to_version(row) if row else None

    # ── activation history ───────────────────────────────────────
    def record_activation(self, organization_id: str, record: ToolActivationRecord) -> None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO tool_activation_history (organization_id, tool_name, version, action, actor, reason, previous_version, timestamp) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (organization_id, record.tool_name, record.version, record.action,
                     record.actor, record.reason, record.previous_version, record.timestamp),
                )
            connection.commit()

    def list_activations(self, organization_id: str, tool_name: str) -> list[ToolActivationRecord]:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT tool_name, version, action, actor, reason, previous_version, timestamp "
                    "FROM tool_activation_history WHERE organization_id = %s AND tool_name = %s "
                    "ORDER BY timestamp DESC LIMIT 100",
                    (organization_id, tool_name),
                )
                rows = cursor.fetchall()
        return [ToolActivationRecord(*row) for row in rows]

    # ── execution audit ──────────────────────────────────────────
    def record_audit(self, organization_id: str, record: ToolAuditRecord) -> None:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO tool_execution_audits (audit_id, organization_id, tool_name, tool_version, user_id, "
                    "arguments_hash, execution_mode, exit_code, success, error_code, duration_ms, stdout_preview, stderr_preview, timestamp) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (record.audit_id, organization_id, record.tool_name, record.tool_version, record.user_id,
                     record.arguments_hash, record.execution_mode, record.exit_code, record.success,
                     record.error_code, record.duration_ms, record.stdout_preview, record.stderr_preview,
                     record.timestamp),
                )
            connection.commit()

    def list_audits(self, organization_id: str, tool_name: str, limit: int = 50) -> list[ToolAuditRecord]:
        with self._connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT audit_id, tool_name, tool_version, user_id, arguments_hash, execution_mode, "
                    "exit_code, success, error_code, duration_ms, stdout_preview, stderr_preview, timestamp "
                    "FROM tool_execution_audits WHERE organization_id = %s AND tool_name = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (organization_id, tool_name, max(1, min(limit, 200))),
                )
                rows = cursor.fetchall()
        return [ToolAuditRecord(*row) for row in rows]
