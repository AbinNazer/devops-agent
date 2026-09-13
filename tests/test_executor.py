"""
Tests for the unified execution abstraction (app.executor).
"""
from unittest.mock import patch

import pytest

from app import executor
from app.executor import LocalExecutor, SSHExecutor, run_command, get_executor, close_executor


@pytest.fixture(autouse=True)
def reset_executor():
    close_executor()
    yield
    close_executor()


class TestLocalExecutor:
    def test_executes_command_successfully(self):
        result = LocalExecutor().execute("uptime")
        assert result["success"] is True
        assert result["stdout"]
        assert result["exit_code"] == 0

    def test_returns_stderr(self):
        result = LocalExecutor().execute("ps aux --sort=-%cpu 2>/dev/null")
        assert result["success"] is True
        assert isinstance(result["stderr"], str)

    def test_handles_nonzero_exit(self):
        result = LocalExecutor().execute("ls /nonexistent_path_xyz_123")
        assert result["exit_code"] != 0

    def test_handles_timeout(self):
        with patch("app.executor.COMMAND_TIMEOUT", 1):
            result = LocalExecutor().execute("sleep 10")
        assert result["success"] is False
        assert "timed out" in result["error"].lower()

    def test_truncates_long_output(self):
        with patch("app.executor.MAX_OUTPUT_CHARS", 10):
            result = LocalExecutor().execute("ps aux --sort=-%cpu")
        assert isinstance(result["stdout"], str)

    def test_close_is_noop(self):
        LocalExecutor().close()


class TestRunCommand:
    def test_local_mode_executes_locally(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        monkeypatch.setattr(executor.Config, "VPS_HOST", "")
        monkeypatch.setattr(executor.Config, "VPS_SSH_USER", "")
        close_executor()
        result = run_command("uptime")
        assert result["success"] is True

    def test_rejected_command_not_executed(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        close_executor()
        result = run_command("rm -rf /")
        assert result["success"] is False
        assert "not permitted" in result["error"].lower()

    def test_ssh_mode_requires_vps_config(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "ssh")
        monkeypatch.setattr(executor.Config, "VPS_HOST", "")
        monkeypatch.setattr(executor.Config, "VPS_SSH_USER", "")
        close_executor()
        result = run_command("uptime")
        assert result["success"] is False
        assert "not configured" in result["error"].lower()

    def test_local_mode_does_not_require_ssh_config(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        monkeypatch.setattr(executor.Config, "VPS_HOST", "")
        monkeypatch.setattr(executor.Config, "VPS_SSH_USER", "")
        monkeypatch.setattr(executor.Config, "VPS_SSH_KEY_PATH", "")
        close_executor()
        result = run_command("uptime")
        assert result["success"] is True


class TestGetExecutor:
    def test_returns_local_in_local_mode(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        close_executor()
        assert isinstance(get_executor(), LocalExecutor)

    def test_returns_ssh_in_ssh_mode(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "ssh")
        monkeypatch.setattr(executor.Config, "VPS_HOST", "1.2.3.4")
        monkeypatch.setattr(executor.Config, "VPS_SSH_USER", "root")
        close_executor()
        assert isinstance(get_executor(), SSHExecutor)

    def test_singleton_reuse(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        close_executor()
        assert get_executor() is get_executor()

    def test_close_resets_singleton(self, monkeypatch):
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        close_executor()
        ex1 = get_executor()
        close_executor()
        ex2 = get_executor()
        assert ex1 is not ex2


class TestConfigIntegration:
    def test_execution_mode_default_is_ssh(self):
        from app.config import Config
        assert Config.EXECUTION_MODE in ("ssh", "local")

    def test_validate_skips_in_local_mode(self, monkeypatch):
        from app.config import validate_vps_config
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "local")
        monkeypatch.setattr(executor.Config, "VPS_HOST", "")
        monkeypatch.setattr(executor.Config, "VPS_SSH_USER", "")
        assert validate_vps_config() == []

    def test_validate_checks_in_ssh_mode(self, monkeypatch):
        from app.config import validate_vps_config
        monkeypatch.setattr(executor.Config, "EXECUTION_MODE", "ssh")
        monkeypatch.setattr(executor.Config, "VPS_HOST", "")
        monkeypatch.setattr(executor.Config, "VPS_SSH_USER", "")
        assert len(validate_vps_config()) > 0
