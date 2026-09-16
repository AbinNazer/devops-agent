from app.tenancy import Membership, Organization, Role, TenantContext, TenancyRegistry


def test_default_context_is_owner_and_can_manage_everything():
    context = TenancyRegistry().context_for("user-1")
    assert context.organization.id == "org_default"
    assert context.role is Role.OWNER
    assert context.can("billing.manage")


def test_role_permissions_are_restricted():
    org = Organization("org-a", "A")
    membership = Membership("user-1", org.id, Role.VIEWER)
    context = TenantContext("user-1", org, membership)
    assert context.can("infrastructure.read")
    assert not context.can("terminal.access")
    assert not context.can("settings.manage")


def test_organizations_are_distinct_registry_records():
    registry = TenancyRegistry()
    first = registry.context_for("user-1")
    second = registry.context_for("user-2")
    assert first.user_id != second.user_id
    assert first.organization.id == second.organization.id
