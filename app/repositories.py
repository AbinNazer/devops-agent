"""Persistence contracts for future SaaS resources.

The in-memory adapter is deliberately the only implementation in Phase 3.
It provides ownership-safe contracts without creating a database or touching
memory.db or existing conversation storage.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
import uuid


@dataclass(frozen=True)
class UserRecord:
    id: str
    username: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OrganizationRecord:
    id: str
    name: str
    owner_user_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SettingsRecord:
    owner_id: str
    values: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class InfrastructureRecord:
    id: str
    organization_id: str
    name: str
    kind: str
    status: str = "unknown"
    capabilities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ResourceNotFound(LookupError):
    pass


class OwnershipError(PermissionError):
    pass


class SaaSRepository(Protocol):
    def get_user(self, user_id: str) -> UserRecord | None: ...
    def get_organization(self, organization_id: str) -> OrganizationRecord | None: ...
    def get_user_settings(self, user_id: str) -> SettingsRecord: ...
    def get_organization_settings(self, organization_id: str) -> SettingsRecord: ...
    def list_infrastructure(self, organization_id: str) -> list[InfrastructureRecord]: ...


class InMemorySaaSRepository:
    """Reference adapter; replace with PostgreSQL without changing callers."""

    def __init__(self) -> None:
        self.users: dict[str, UserRecord] = {}
        self.organizations: dict[str, OrganizationRecord] = {}
        self.user_settings: dict[str, SettingsRecord] = {}
        self.organization_settings: dict[str, SettingsRecord] = {}
        self.infrastructure: dict[str, InfrastructureRecord] = {}

    def add_user(self, username: str, user_id: str | None = None) -> UserRecord:
        record = UserRecord(user_id or f"user_{uuid.uuid4().hex[:16]}", username)
        self.users[record.id] = record
        return record

    def add_organization(self, name: str, owner_user_id: str, organization_id: str | None = None) -> OrganizationRecord:
        if owner_user_id not in self.users:
            raise ResourceNotFound("owner user not found")
        record = OrganizationRecord(organization_id or f"org_{uuid.uuid4().hex[:16]}", name, owner_user_id)
        self.organizations[record.id] = record
        return record

    def get_user(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id)

    def get_organization(self, organization_id: str) -> OrganizationRecord | None:
        return self.organizations.get(organization_id)

    def get_user_settings(self, user_id: str) -> SettingsRecord:
        if user_id not in self.users:
            raise ResourceNotFound("user not found")
        return self.user_settings.setdefault(user_id, SettingsRecord(user_id))

    def get_organization_settings(self, organization_id: str) -> SettingsRecord:
        if organization_id not in self.organizations:
            raise ResourceNotFound("organization not found")
        return self.organization_settings.setdefault(organization_id, SettingsRecord(organization_id))

    def update_settings(self, record: SettingsRecord, values: dict[str, Any]) -> SettingsRecord:
        record.values.update(values)
        record.updated_at = datetime.now(timezone.utc)
        return record

    def add_infrastructure(self, organization_id: str, name: str, kind: str, **metadata: Any) -> InfrastructureRecord:
        if organization_id not in self.organizations:
            raise ResourceNotFound("organization not found")
        record = InfrastructureRecord(f"infra_{uuid.uuid4().hex[:16]}", organization_id, name, kind, metadata=metadata)
        self.infrastructure[record.id] = record
        return record

    def get_infrastructure(self, infrastructure_id: str, organization_id: str) -> InfrastructureRecord:
        record = self.infrastructure.get(infrastructure_id)
        if not record:
            raise ResourceNotFound("infrastructure not found")
        if record.organization_id != organization_id:
            raise OwnershipError("infrastructure belongs to another organization")
        return record

    def list_infrastructure(self, organization_id: str) -> list[InfrastructureRecord]:
        return [r for r in self.infrastructure.values() if r.organization_id == organization_id]


saas_repository = InMemorySaaSRepository()
