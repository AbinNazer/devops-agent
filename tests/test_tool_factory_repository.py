"""Repository tests (in-memory adapter + migration DDL)."""
from app.tool_factory.repository import InMemoryToolFactoryRepository
from app.tool_factory.models import (
    ToolDefinition, ToolVersion, ToolActivationRecord, ToolAuditRecord,
)


def make_definition(name="repo_tool"):
    return ToolDefinition(
        name=name, display_name=name, description="d", category="c", version="1.0.0",
        author="a", input_schema={}, output_schema={}, execution_mode="both",
        command_template="df -h",
    )


class TestInMemoryRepository:
    def test_definition_crud(self):
        repo = InMemoryToolFactoryRepository()
        assert repo.get_definition("org", "repo_tool") is None
        repo.save_definition("org", make_definition())
        assert repo.get_definition("org", "repo_tool") is not None
        assert repo.list_definitions("org") and not repo.list_definitions("other")
        assert repo.delete_definition("org", "repo_tool") is True
        assert repo.delete_definition("org", "repo_tool") is False

    def test_version_single_active(self):
        repo = InMemoryToolFactoryRepository()
        definition = make_definition()
        repo.save_definition("org", definition)
        for version in ("1.0.0", "1.1.0"):
            repo.save_version("org", ToolVersion(
                tool_name="repo_tool", version=version, definition={}, checksum="x",
                created_by="a",
            ))
        repo.set_active_version("org", "repo_tool", "1.0.0")
        assert repo.get_active_version("org", "repo_tool").version == "1.0.0"
        repo.set_active_version("org", "repo_tool", "1.1.0")
        actives = [v for v in repo.list_versions("org", "repo_tool") if v.active]
        assert len(actives) == 1 and actives[0].version == "1.1.0"

    def test_set_active_unknown_version_fails(self):
        repo = InMemoryToolFactoryRepository()
        try:
            repo.set_active_version("org", "missing", "9.9.9")
        except KeyError:
            pass
        else:
            raise AssertionError("unknown version must fail")

    def test_audit_bounded_and_newest_first(self):
        repo = InMemoryToolFactoryRepository()
        for index in range(250):
            repo.record_audit("org", ToolAuditRecord(
                tool_name="t", tool_version="1.0.0", user_id="u", organization_id="org",
                arguments_hash="h", execution_mode="local", exit_code=0, success=True,
                error_code="", duration_ms=index, audit_id=f"audit_{index}",
            ))
        records = repo.list_audits("org", "t", limit=50)
        assert len(records) == 50
        assert records[0].audit_id == "audit_249"  # newest first

    def test_activation_history_scoped(self):
        repo = InMemoryToolFactoryRepository()
        repo.record_activation("org", ToolActivationRecord(
            tool_name="t", version="1.0.0", action="activated", actor="a",
            reason="r", previous_version=""))
        assert repo.list_activations("org", "t")
        assert not repo.list_activations("other", "t")


class TestMigration002:
    def test_migration_contains_factory_tables(self):
        from app.postgres import MIGRATIONS_DIR
        sql = (MIGRATIONS_DIR / "002_tool_factory.sql").read_text(encoding="utf-8")
        for table in ("tool_definitions", "tool_versions", "tool_permissions",
                      "tool_activation_history", "tool_execution_audits"):
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        # Tenancy isolation is mandatory on every table.
        assert sql.count("organization_id") >= 5
