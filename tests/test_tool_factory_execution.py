"""Execution service tests: the security chain in action."""
import pytest

from app.tool_factory.service import ToolFactoryService
from app.tool_factory.execution import ToolExecutionService, ToolExecutionError
from app.tenancy import TenantContext, Organization, Membership, Role


@pytest.fixture
def service():
    return ToolFactoryService()


def make_tool(service, org="org", name="reader_tool", template="df -h",
              permission="infrastructure.read", schema=None, mode="both"):
    payload = {
        "name": name, "description": "reader", "category": "system",
        "version": "1.0.0", "execution_mode": mode, "command_template": template,
        "input_schema": schema or {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
        "required_permission": permission,
    }
    service.create_tool(org, payload, actor="alice")
    service.approve_tool_definition(org, name, actor="bob")
    service.activate_tool_version(org, name, "1.0.0", actor="bob")
    return service.get_tool(org, name)


def ok_runner(output="filesystem output"):
    return lambda command: {"success": True, "stdout": output, "stderr": "", "exit_code": 0}


@pytest.fixture
def owner():
    org = Organization("org", "Org")
    return TenantContext("u1", org, Membership("u1", "org", Role.OWNER))


@pytest.fixture
def viewer():
    org = Organization("org", "Org")
    return TenantContext("u2", org, Membership("u2", "org", Role.VIEWER))


class TestHappyPath:
    def test_executes_and_audits(self, service, owner):
        make_tool(service)
        execution = ToolExecutionService(service, runner=ok_runner())
        result = execution.execute_tool("reader_tool", {}, "u1", "org", owner.can)
        assert result["success"] is True
        assert result["audit_id"].startswith("audit_")
        assert result["tool_version"] == "1.0.0"
        audits = service.list_audit("org", "reader_tool")
        assert len(audits) == 1
        assert audits[0].success is True
        assert audits[0].arguments_hash  # hashed, never raw args

    def test_arguments_substituted(self, service, owner):
        make_tool(service, name="log_reader",
                  template="docker logs {container} --tail {n}",
                  schema={"type": "object",
                          "properties": {"container": {"type": "string"}, "n": {"type": "integer"}},
                          "required": ["container", "n"]})
        execution = ToolExecutionService(service, runner=ok_runner())
        result = execution.execute_tool("log_reader", {"container": "backend", "n": 50}, "u1", "org", owner.can)
        assert result["success"]


class TestGates:
    def test_unknown_tool(self, service, owner):
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("no_such_tool", {}, "u1", "org", owner.can)
        assert excinfo.value.error_code == "tool_not_found"

    def test_inactive_tool_blocked(self, service, owner):
        make_tool(service)
        service.deactivate_tool_version("org", "reader_tool", actor="bob")
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("reader_tool", {}, "u1", "org", owner.can)
        assert excinfo.value.error_code == "tool_not_active"

    def test_permission_denied(self, service, owner, viewer):
        # A tool that requires a permission viewer lacks.
        make_tool(service, name="admin_tool", permission="tools.manage")
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("admin_tool", {}, "u2", "org", viewer.can)
        assert excinfo.value.error_code == "permission_denied"

    def test_invalid_schema_input_blocked(self, service, owner):
        make_tool(service, name="tailer", template="docker logs {container} --tail 10",
                  schema={"type": "object",
                          "properties": {"container": {"type": "string", "pattern": "^[a-z]+$"}},
                          "required": ["container"]})
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("tailer", {"container": "UPPER; rm"}, "u1", "org", owner.can)
        assert excinfo.value.error_code == "invalid_input"

    def test_missing_required_argument_blocked(self, service, owner):
        make_tool(service, name="tailer", template="docker logs {container} --tail 10",
                  schema={"type": "object", "properties": {"container": {"type": "string"}},
                          "required": ["container"]})
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("tailer", {}, "u1", "org", owner.can)
        assert excinfo.value.error_code == "invalid_input"

    def test_whitelist_final_gate(self, service, owner):
        # A tool whose template is NOT in the ssh_whitelist is rejected at
        # execution time even though factory validation allowed the binary.
        make_tool(service, name="find_files", template="find /var -maxdepth 1")
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("find_files", {}, "u1", "org", owner.can)
        assert excinfo.value.error_code == "command_rejected"

    def test_ssh_mode_requires_config(self, service, owner, monkeypatch):
        from app.config import Config
        make_tool(service, name="ssh_only", mode="ssh")
        monkeypatch.setattr(Config, "VPS_HOST", "")
        monkeypatch.setattr(Config, "VPS_SSH_USER", "")
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("ssh_only", {}, "u1", "org", owner.can)
        assert excinfo.value.error_code == "mode_unavailable"


class TestOutputSafety:
    def test_secrets_redacted(self, service, owner):
        make_tool(service)
        secret_output = "password=hunter2\ntoken=abc123\ncolor=blue"
        execution = ToolExecutionService(service, runner=ok_runner(secret_output))
        result = execution.execute_tool("reader_tool", {}, "u1", "org", owner.can)
        assert "hunter2" not in result["output"]
        assert "abc123" not in result["output"]
        assert "color=blue" in result["output"]

    def test_output_truncated_to_limit(self, service, owner):
        make_tool(service)
        big = "x" * 100_000
        execution = ToolExecutionService(service, runner=ok_runner(big))
        result = execution.execute_tool("reader_tool", {}, "u1", "org", owner.can)
        assert len(result["output"].encode()) <= 16384 + 64  # definition limit + truncation marker

    def test_failure_recorded_with_error(self, service, owner):
        make_tool(service)
        execution = ToolExecutionService(service, runner=lambda cmd: {"success": True, "stdout": "", "stderr": "boom", "exit_code": 2})
        result = execution.execute_tool("reader_tool", {}, "u1", "org", owner.can)
        assert result["success"] is False
        audits = service.list_audit("org", "reader_tool")
        assert audits[0].success is False
        assert audits[0].exit_code == 2


class TestOrgIsolation:
    def test_execution_is_org_scoped(self, service, owner, viewer):
        make_tool(service, org="org")
        other = TenantContext("u3", Organization("org_other", "O"), Membership("u3", "org_other", Role.OWNER))
        execution = ToolExecutionService(service, runner=ok_runner())
        with pytest.raises(ToolExecutionError) as excinfo:
            execution.execute_tool("reader_tool", {}, "u3", "org_other", other.can)
        assert excinfo.value.error_code == "tool_not_found"
