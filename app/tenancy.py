"""Small in-memory tenancy and RBAC foundation.

This is intentionally non-persistent in Phase 2. It provides explicit
ownership/permission contracts without migrating memory.db or web sessions.
Replace the repository with PostgreSQL later without changing route callers.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Set
import uuid


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    ENGINEER = "engineer"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.OWNER: frozenset({"*"}),
    Role.ADMIN: frozenset({"infrastructure.read", "infrastructure.manage", "terminal.access", "terminal.admin", "incident.manage", "settings.manage", "team.manage", "tools.read", "tools.manage", "tools.execute"}),
    Role.ENGINEER: frozenset({"infrastructure.read", "terminal.access", "incident.manage", "tools.read", "tools.execute"}),
    Role.VIEWER: frozenset({"infrastructure.read", "tools.read"}),
}


@dataclass(frozen=True)
class Organization:
    id: str
    name: str


@dataclass(frozen=True)
class Membership:
    user_id: str
    organization_id: str
    role: Role


@dataclass
class TenantContext:
    user_id: str
    organization: Organization
    membership: Membership

    @property
    def role(self) -> Role:
        return self.membership.role

    def can(self, permission: str) -> bool:
        permissions = ROLE_PERMISSIONS[self.role]
        return "*" in permissions or permission in permissions


class TenancyRegistry:
    """Replaceable registry for the current single-installation phase."""

    def __init__(self) -> None:
        self.organizations: Dict[str, Organization] = {}
        self.memberships: Dict[tuple[str, str], Membership] = {}

    def ensure_default(self, user_id: str) -> TenantContext:
        org_id = "org_default"
        org = self.organizations.setdefault(org_id, Organization(org_id, "Default Organization"))
        key = (user_id, org_id)
        membership = self.memberships.setdefault(key, Membership(user_id, org_id, Role.OWNER))
        return TenantContext(user_id, org, membership)

    def context_for(self, user_id: str) -> TenantContext:
        return self.ensure_default(user_id)


registry = TenancyRegistry()


def new_user_id(username: str) -> str:
    return f"user_{uuid.uuid5(uuid.NAMESPACE_URL, username).hex[:16]}"
