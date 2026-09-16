import pytest

from app.repositories import InMemorySaaSRepository, OwnershipError, ResourceNotFound


def test_settings_are_owned_by_existing_resources():
    repo = InMemorySaaSRepository()
    user = repo.add_user("alice")
    org = repo.add_organization("Alice Inc", user.id)
    assert repo.get_user_settings(user.id).owner_id == user.id
    assert repo.get_organization_settings(org.id).owner_id == org.id


def test_infrastructure_isolation_blocks_cross_organization_access():
    repo = InMemorySaaSRepository()
    alice = repo.add_user("alice")
    bob = repo.add_user("bob")
    org_a = repo.add_organization("A", alice.id)
    org_b = repo.add_organization("B", bob.id)
    infra = repo.add_infrastructure(org_a.id, "Production VPS", "vps")
    assert repo.list_infrastructure(org_b.id) == []
    with pytest.raises(OwnershipError):
        repo.get_infrastructure(infra.id, org_b.id)


def test_unknown_owners_and_resources_fail_closed():
    repo = InMemorySaaSRepository()
    with pytest.raises(ResourceNotFound):
        repo.add_organization("Invalid", "missing-user")
    with pytest.raises(ResourceNotFound):
        repo.get_user_settings("missing-user")
