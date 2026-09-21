"""Versioning / activation / rollback tests."""
import pytest

from app.tool_factory.service import ToolFactoryService


@pytest.fixture
def service():
    return ToolFactoryService()


@pytest.fixture
def payload():
    return {
        "name": "versioned_tool", "description": "disk reader", "category": "system",
        "version": "1.0.0", "execution_mode": "both", "command_template": "df -h",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
    }


def _activate(service, payload, version="1.0.0", org="org"):
    service.create_tool(org, payload, actor="alice")
    service.approve_tool_definition(org, payload["name"], actor="bob")
    service.activate_tool_version(org, payload["name"], version, actor="bob")


class TestLifecycle:
    def test_create_starts_validated_not_active(self, service, payload):
        definition = service.create_tool("org", payload, actor="alice")
        assert definition.status == "validated"
        assert definition.enabled is False
        assert service.repository.get_active_version("org", "test_two_x") is None

    def test_activation_requires_approval(self, service, payload):
        service.create_tool("org", payload, actor="alice")
        with pytest.raises(PermissionError):
            service.activate_tool_version("org", payload["name"], "1.0.0", actor="bob")

    def test_full_activation(self, service, payload):
        _activate(service, payload)
        active = service.repository.get_active_version("org", "versioned_tool")
        assert active.version == "1.0.0"
        definition = service.get_tool("org", "versioned_tool")
        assert definition.status == "active" and definition.enabled

    def test_deactivation(self, service, payload):
        _activate(service, payload)
        service.deactivate_tool_version("org", "versioned_tool", actor="bob", reason="maintenance")
        definition = service.get_tool("org", "versioned_tool")
        assert definition.status == "disabled" and not definition.enabled

    def test_duplicate_name_rejected(self, service, payload):
        service.create_tool("org", payload, actor="alice")
        with pytest.raises(Exception):
            service.create_tool("org", payload, actor="alice")


class TestVersions:
    def test_new_version_and_activation(self, service, payload):
        _activate(service, payload)
        service.create_tool_version("org", "versioned_tool", {
            "version": "1.1.0", "command_template": "df -h",
        }, actor="alice", change_reason="refresh")
        service.activate_tool_version("org", "versioned_tool", "1.1.0", actor="bob")
        active = service.repository.get_active_version("org", "versioned_tool")
        assert active.version == "1.1.0"
        versions = service.list_tool_versions("org", "versioned_tool")
        assert {v.version for v in versions} == {"1.0.0", "1.1.0"}

    def test_only_one_active_version(self, service, payload):
        _activate(service, payload)
        service.create_tool_version("org", "versioned_tool", {"version": "1.1.0"}, actor="alice")
        service.activate_tool_version("org", "versioned_tool", "1.1.0", actor="bob")
        actives = [v for v in service.list_tool_versions("org", "versioned_tool") if v.active]
        assert len(actives) == 1 and actives[0].version == "1.1.0"

    def test_bad_version_format_rejected(self, service, payload):
        _activate(service, payload)
        with pytest.raises(ValueError):
            service.create_tool_version("org", "versioned_tool", {"version": "next"}, actor="alice")

    def test_duplicate_version_rejected(self, service, payload):
        _activate(service, payload)
        with pytest.raises(ValueError):
            service.create_tool_version("org", "versioned_tool", {"version": "1.0.0"}, actor="alice")

    def test_compare_versions(self, service, payload):
        _activate(service, payload)
        service.create_tool_version("org", "versioned_tool", {"version": "1.1.0", "description": "updated"}, actor="alice")
        comparison = service.compare_tool_versions("org", "versioned_tool", "1.0.0", "1.1.0")
        assert "version" in comparison["changes"]
        assert "description" in comparison["changes"]


class TestRollback:
    def test_rollback_to_previous(self, service, payload):
        _activate(service, payload)
        service.create_tool_version("org", "versioned_tool", {"version": "1.1.0"}, actor="alice")
        service.activate_tool_version("org", "versioned_tool", "1.1.0", actor="bob")
        result = service.rollback_tool_version("org", "versioned_tool", actor="carol", reason="regression")
        assert result["rolled_back_from"] == "1.1.0"
        assert service.repository.get_active_version("org", "versioned_tool").version == "1.0.0"

    def test_rollback_records_history(self, service, payload):
        _activate(service, payload)
        service.create_tool_version("org", "versioned_tool", {"version": "1.1.0"}, actor="alice")
        service.activate_tool_version("org", "versioned_tool", "1.1.0", actor="bob")
        service.rollback_tool_version("org", "versioned_tool", actor="carol")
        actions = [record.action for record in service.list_activation_history("org", "versioned_tool")]
        assert "rolled_back" in actions

    def test_rollback_without_history_fails(self, service, payload):
        service.create_tool("org", payload, actor="alice")
        with pytest.raises(ValueError):
            service.rollback_tool_version("org", "versioned_tool", actor="bob")


class TestOrganizationIsolation:
    def test_tools_are_org_scoped(self, service, payload):
        _activate(service, payload, org="org_a")
        with pytest.raises(KeyError):
            service.get_tool("org_b", payload["name"])
        assert service.list_tools("org_b") == []
        # Same tool name can exist in another org independently.
        service.create_tool("org_b", payload, actor="mallory")
        assert service.get_tool("org_b", payload["name"]) is not None
