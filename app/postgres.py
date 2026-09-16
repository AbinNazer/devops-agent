"""Opt-in PostgreSQL persistence for SaaS resources.

This module is not connected during normal application startup. Existing
memory.db and JSON sessions use separate storage.
"""
from pathlib import Path
from typing import Any

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "db" / "migrations"


def connect(database_url: str):
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary] before using PostgreSQL persistence") from exc
    return psycopg.connect(database_url)


def run_migrations(database_url: str) -> list[str]:
    applied: list[str] = []
    with connect(database_url) as connection:
        with connection.cursor() as cursor:
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cursor.execute(path.read_text(encoding="utf-8"))
                applied.append(path.name)
        connection.commit()
    return applied


class PostgresSaaSRepository:
    """Adapter for users, organizations, settings, and infrastructure."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def _fetchone(self, query: str, params: tuple[Any, ...] = ()):
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

    def list_infrastructure(self, organization_id: str) -> list[dict[str, Any]]:
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, organization_id, name, kind, status, capabilities, metadata, created_at FROM infrastructure WHERE organization_id = %s ORDER BY created_at DESC", (organization_id,))
                columns = [item.name for item in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_user_settings(self, user_id: str) -> dict[str, Any]:
        row = self._fetchone("SELECT user_id, values, updated_at FROM user_settings WHERE user_id = %s", (user_id,))
        if not row:
            raise LookupError("user settings not found")
        return {"user_id": row[0], "values": row[1], "updated_at": row[2]}

    def get_organization_settings(self, organization_id: str) -> dict[str, Any]:
        row = self._fetchone("SELECT organization_id, values, updated_at FROM organization_settings WHERE organization_id = %s", (organization_id,))
        if not row:
            raise LookupError("organization settings not found")
        return {"organization_id": row[0], "values": row[1], "updated_at": row[2]}
