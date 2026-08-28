"""
Tests for ssh_client.run_whitelisted_command(). All paramiko interaction
is mocked — none of these tests touch a real network or a real VPS.

Covers: connection reuse across calls, automatic reconnect when the
connection has dropped, retry-once on transient failures, host key
rejection, and that every failure mode returns a clean, safe error
message with no leaked exception internals.
"""
import socket
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from app import ssh_client


def _make_mock_client(stdout_text="", stderr_text="", exit_code=0, alive=True):
    """Build a mock paramiko.SSHClient whose exec_command returns canned output."""
    mock_client = MagicMock()

    mock_stdout = MagicMock()
    mock_stdout.read.return_value = stdout_text.encode()
    mock_stdout.channel.recv_exit_status.return_value = exit_code

    mock_stderr = MagicMock()
    mock_stderr.read.return_value = stderr_text.encode()

    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

    mock_transport = MagicMock()
    mock_transport.is_active.return_value = alive
    mock_client.get_transport.return_value = mock_transport

    return mock_client


@pytest.fixture(autouse=True)
def fake_vps_config(monkeypatch):
    """Ensure Config always looks configured, regardless of the real .env."""
    monkeypatch.setattr(ssh_client.Config, "VPS_HOST", "1.2.3.4")
    monkeypatch.setattr(ssh_client.Config, "VPS_SSH_USER", "root")
    monkeypatch.setattr(ssh_client.Config, "VPS_SSH_KEY_PATH", "/fake/key")
    monkeypatch.setattr(ssh_client.Config, "VPS_SSH_PORT", 22)
    monkeypatch.setattr(ssh_client.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts")


@pytest.fixture(autouse=True)
def reset_connection_manager():
    """
    The connection manager is a module-level singleton so it can be reused
    across real calls — but that means tests MUST reset it, or a mock
    client cached by one test would leak into the next.
    """
    ssh_client.close_connection()
    yield
    ssh_client.close_connection()


# --- whitelist gate ---

def test_rejected_command_never_attempts_connection():
    """The whitelist check must happen BEFORE any SSHClient is constructed."""
    with patch("app.ssh_client.paramiko.SSHClient") as mock_ssh_cls:
        result = ssh_client.run_whitelisted_command("rm -rf /")
        assert result["success"] is False
        assert "not permitted" in result["error"]
        mock_ssh_cls.assert_not_called()


# --- basic success path ---

def test_successful_command_execution():
    mock_client = _make_mock_client(stdout_text="hello\n", exit_code=0)
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is True
    assert result["stdout"] == "hello\n"
    assert result["exit_code"] == 0
    mock_client.connect.assert_called_once()


def test_host_key_policy_is_reject_not_autoadd():
    """Verifies the security fix: unknown host keys must be rejected, not auto-trusted."""
    mock_client = _make_mock_client()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        ssh_client.run_whitelisted_command("uptime")

    policy_used = mock_client.set_missing_host_key_policy.call_args[0][0]
    assert isinstance(policy_used, paramiko.RejectPolicy)


def test_known_hosts_loaded_when_file_exists(monkeypatch):
    monkeypatch.setattr(ssh_client.os.path, "exists", lambda path: True)
    mock_client = _make_mock_client()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        ssh_client.run_whitelisted_command("uptime")

    mock_client.load_host_keys.assert_called_once_with("/fake/known_hosts")


# --- connection reuse ---

def test_second_call_reuses_existing_connection():
    mock_client = _make_mock_client(alive=True)
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client) as mock_ssh_cls:
        ssh_client.run_whitelisted_command("uptime")
        ssh_client.run_whitelisted_command("df -h")

    # SSHClient() constructed and connected only ONCE, despite two commands
    mock_ssh_cls.assert_called_once()
    mock_client.connect.assert_called_once()
    assert mock_client.exec_command.call_count == 2


def test_reconnects_automatically_when_connection_has_dropped():
    dead_client = _make_mock_client(alive=False)
    fresh_client = _make_mock_client(alive=True)
    with patch("app.ssh_client.paramiko.SSHClient", side_effect=[dead_client, fresh_client]):
        # first call establishes the connection, then we simulate it dying
        ssh_client.run_whitelisted_command("uptime")
        ssh_client._manager._client.get_transport.return_value.is_active.return_value = False
        result = ssh_client.run_whitelisted_command("df -h")

    assert result["success"] is True
    fresh_client.connect.assert_called_once()


# --- permanent failures: no retry ---

def test_authentication_failure_does_not_retry():
    mock_client = MagicMock()
    mock_client.connect.side_effect = paramiko.AuthenticationException()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client) as mock_ssh_cls:
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "authentication failed" in result["error"].lower()
    mock_ssh_cls.assert_called_once()  # only tried once — retrying can't fix bad credentials


def test_host_unresolvable_does_not_retry():
    mock_client = MagicMock()
    mock_client.connect.side_effect = socket.gaierror()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client) as mock_ssh_cls:
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "resolve" in result["error"].lower()
    mock_ssh_cls.assert_called_once()


def test_missing_ssh_key_does_not_retry():
    mock_client = MagicMock()
    mock_client.connect.side_effect = FileNotFoundError()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client) as mock_ssh_cls:
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "key file not found" in result["error"].lower()
    mock_ssh_cls.assert_called_once()


def test_bad_host_key_returns_clean_error_and_does_not_retry():
    mock_client = MagicMock()
    bad_key_error = paramiko.BadHostKeyException.__new__(paramiko.BadHostKeyException)  # bypass real __init__
    mock_client.connect.side_effect = bad_key_error
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client) as mock_ssh_cls:
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "host key verification failed" in result["error"].lower()
    mock_ssh_cls.assert_called_once()


# --- transient failures: retry once, then give up cleanly ---

def test_transient_timeout_retries_once_then_succeeds():
    mock_client = MagicMock()
    ok_stdout = MagicMock()
    ok_stdout.read.return_value = b"recovered\n"
    ok_stdout.channel.recv_exit_status.return_value = 0
    ok_stderr = MagicMock()
    ok_stderr.read.return_value = b""

    mock_client.exec_command.side_effect = [
        socket.timeout(),
        (MagicMock(), ok_stdout, ok_stderr),
    ]
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport

    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is True
    assert result["stdout"] == "recovered\n"
    assert mock_client.exec_command.call_count == 2


def test_transient_timeout_exhausts_retry_and_fails_cleanly():
    mock_client = MagicMock()
    mock_client.exec_command.side_effect = socket.timeout()
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport

    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "after retry" in result["error"].lower()
    assert mock_client.exec_command.call_count == 2


def test_connection_refused_retries_once_then_fails():
    mock_client = MagicMock()
    mock_client.connect.side_effect = ConnectionRefusedError()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client) as mock_ssh_cls:
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "refused" in result["error"].lower()
    assert mock_ssh_cls.call_count == 2  # retried once (new connection attempt)


def test_generic_ssh_exception_does_not_leak_details():
    mock_client = MagicMock()
    mock_client.connect.side_effect = paramiko.SSHException("some internal detail")
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        result = ssh_client.run_whitelisted_command("uptime")

    assert result["success"] is False
    assert "some internal detail" not in result["error"]


# --- config / misc ---

def test_unconfigured_vps_returns_clean_error(monkeypatch):
    monkeypatch.setattr(ssh_client.Config, "VPS_HOST", "")
    result = ssh_client.run_whitelisted_command("uptime")
    assert result["success"] is False
    assert "not configured" in result["error"].lower()


def test_nonzero_exit_code_still_returns_success_with_details():
    # e.g. `systemctl status` on a stopped service exits non-zero but the
    # command itself succeeded — that distinction is left to the caller
    mock_client = _make_mock_client(stdout_text="inactive (dead)", exit_code=3)
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        result = ssh_client.run_whitelisted_command("systemctl status nginx")

    assert result["success"] is True
    assert result["exit_code"] == 3
    assert "inactive" in result["stdout"]


def test_output_is_truncated_when_too_long():
    huge_output = "x" * (ssh_client.MAX_OUTPUT_CHARS + 500)
    mock_client = _make_mock_client(stdout_text=huge_output, exit_code=0)
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        result = ssh_client.run_whitelisted_command("docker ps -a")

    assert len(result["stdout"]) < len(huge_output)
    assert "truncated" in result["stdout"]


def test_close_connection_clears_cached_client():
    mock_client = _make_mock_client()
    with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
        ssh_client.run_whitelisted_command("uptime")
        assert ssh_client._manager._client is not None
        ssh_client.close_connection()
        assert ssh_client._manager._client is None


# --- audit log ---

def test_executed_command_is_audit_logged(caplog):
    mock_client = _make_mock_client(stdout_text="ok", exit_code=0)
    with caplog.at_level("INFO", logger="ssh_audit"):
        with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
            ssh_client.run_whitelisted_command("uptime")

    assert any("EXECUTED" in r.message and "uptime" in r.message for r in caplog.records)


def test_rejected_command_is_audit_logged(caplog):
    with caplog.at_level("WARNING", logger="ssh_audit"):
        ssh_client.run_whitelisted_command("rm -rf /")

    assert any("REJECTED" in r.message for r in caplog.records)


def test_auth_failure_is_audit_logged(caplog):
    mock_client = MagicMock()
    mock_client.connect.side_effect = paramiko.AuthenticationException()
    with caplog.at_level("ERROR", logger="ssh_audit"):
        with patch("app.ssh_client.paramiko.SSHClient", return_value=mock_client):
            ssh_client.run_whitelisted_command("uptime")

    assert any("AUTH_FAILED" in r.message for r in caplog.records)