"""
Tests for app/ssh_session_manager.py.

Covers: scoped sessions, context manager, command execution, whitelist
enforcement, bounded commands, timeout handling, connection reuse,
cleanup, and audit logging. All paramiko interaction is mocked.
"""
import time
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from app.ssh_session_manager import (
    SSHSession, SessionConfig, CommandResult,
    create_session, session_scope,
)
from app import ssh_session_manager


def _make_mock_client(stdout_text="", stderr_text="", exit_code=0, alive=True):
    """Build a mock paramiko.SSHClient."""
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


# All tests must create sessions inside a patched context so _host is set
# to a fake value rather than a real VPS IP from the .env file.

def _patched_session(stdout_text="", exit_code=0, **kwargs):
    """Create a session with all Config patched and a mock client ready."""
    config = kwargs.pop("config", None)
    mock_client = _make_mock_client(stdout_text=stdout_text, exit_code=exit_code)

    # Return context-managed (config_patches, session, mock_client)
    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    session = SSHSession(config=config)
    return session, mock_client, patches


def _cleanup(patches, session=None):
    if session:
        session.close()
    for p in patches:
        p.stop()


# --- Basic session lifecycle ---

def test_session_context_manager_opens_and_closes():
    """Context manager should create and clean up the session."""
    session, mock_client, patches = _patched_session()
    try:
        with session:
            result = session.execute("uptime")
            assert result.success is True
        assert session._closed is True
    finally:
        _cleanup(patches)


def test_session_execute_whitelisted_command():
    """A whitelisted command should execute successfully."""
    session, mock_client, patches = _patched_session(stdout_text="up 5 days\n")
    try:
        result = session.execute("uptime")
        assert result.success is True
        assert result.stdout == "up 5 days\n"
        assert result.exit_code == 0
    finally:
        _cleanup(patches, session)


def test_session_rejects_non_whitelisted_command():
    """Non-whitelisted commands should be rejected without SSH connection."""
    session, mock_client, patches = _patched_session()
    try:
        result = session.execute("rm -rf /")
        assert result.success is False
        assert result.rejected is True
        assert "not permitted" in result.error
    finally:
        _cleanup(patches, session)


def test_session_rejects_docker_exec():
    """Docker exec must be rejected by whitelist."""
    session, mock_client, patches = _patched_session()
    try:
        result = session.execute("docker exec backend sh")
        assert result.success is False
        assert result.rejected is True
    finally:
        _cleanup(patches, session)


# --- Connection reuse ---

def test_session_reuses_connection_across_commands():
    """Multiple commands in one session should reuse the same SSH connection."""
    session, mock_client, patches = _patched_session()
    try:
        with patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client) as mock_cls:
            # Re-patch to track calls
            pass  # mock_client already patched in _patched_session
        session.execute("uptime")
        session.execute("free -h")
        session.execute("df -h")
        assert mock_client.connect.call_count == 1
        assert mock_client.exec_command.call_count == 3
        assert session.command_count == 3
    finally:
        _cleanup(patches, session)


def test_session_tracks_commands_executed():
    """Session should track all executed commands."""
    session, mock_client, patches = _patched_session()
    try:
        session.execute("uptime")
        session.execute("free -h")
        assert len(session.commands_executed) == 2
        assert session.commands_executed[0].command == "uptime"
        assert session.commands_executed[1].command == "free -h"
    finally:
        _cleanup(patches, session)


# --- Bounded commands ---

def test_session_respects_max_commands_limit():
    """Session should stop executing after max_commands is reached."""
    config = SessionConfig(max_commands=3)
    session, mock_client, patches = _patched_session(config=config)
    try:
        r1 = session.execute("uptime")
        r2 = session.execute("free -h")
        r3 = session.execute("df -h")
        r4 = session.execute("nproc")
        assert r1.success is True
        assert r2.success is True
        assert r3.success is True
        assert r4.success is False
        assert "command limit" in r4.error.lower()
    finally:
        _cleanup(patches, session)


# --- Timeout handling ---

def test_session_respects_session_lifetime():
    """Session should reject commands after max_session_lifetime."""
    config = SessionConfig(max_session_lifetime=1)
    session, mock_client, patches = _patched_session(config=config)
    try:
        session.execute("uptime")
        time.sleep(1.1)
        result = session.execute("free -h")
        assert result.success is False
        assert "lifetime" in result.error.lower()
    finally:
        _cleanup(patches, session)


def test_session_respects_idle_timeout():
    """Session should reject commands after idle_timeout."""
    config = SessionConfig(idle_timeout=1)
    session, mock_client, patches = _patched_session(config=config)
    try:
        session.execute("uptime")
        time.sleep(1.1)
        result = session.execute("free -h")
        assert result.success is False
        assert "idle" in result.error.lower()
    finally:
        _cleanup(patches, session)


# --- Error handling ---

def test_session_auth_failure():
    """Authentication failure should return clean error."""
    mock_client = MagicMock()
    mock_client.connect.side_effect = paramiko.AuthenticationException()

    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        session = SSHSession()
        result = session.execute("uptime")
        assert result.success is False
        assert "authentication" in result.error.lower()
        session.close()
    finally:
        for p in patches:
            p.stop()


def test_session_bad_host_key():
    """Bad host key should return clean error."""
    mock_client = MagicMock()
    bad_key = paramiko.BadHostKeyException.__new__(paramiko.BadHostKeyException)
    mock_client.connect.side_effect = bad_key

    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        session = SSHSession()
        result = session.execute("uptime")
        assert result.success is False
        assert "host key" in result.error.lower()
        session.close()
    finally:
        for p in patches:
            p.stop()


def test_session_transient_error_retries():
    """Transient errors should be retried once."""
    mock_client = MagicMock()
    ok_stdout = MagicMock()
    ok_stdout.read.return_value = b"recovered\n"
    ok_stdout.channel.recv_exit_status.return_value = 0
    ok_stderr = MagicMock()
    ok_stderr.read.return_value = b""

    call_count = [0]
    def exec_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise paramiko.SSHException("connection reset")
        return (MagicMock(), ok_stdout, ok_stderr)

    mock_client.exec_command.side_effect = exec_side_effect
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport

    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        session = SSHSession()
        result = session.execute("uptime")
        assert result.success is True
        assert result.stdout == "recovered\n"
        session.close()
    finally:
        for p in patches:
            p.stop()


# --- Config error ---

def test_unconfigured_vps_returns_error():
    """Unconfigured VPS should return a clean error."""
    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", ""),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
    ]
    for p in patches:
        p.start()
    try:
        session = SSHSession()
        result = session.execute("uptime")
        assert result.success is False
        assert "not configured" in result.error.lower()
        session.close()
    finally:
        for p in patches:
            p.stop()


# --- Host key policy ---

def test_session_uses_reject_policy():
    """Session should use RejectPolicy for host keys."""
    mock_client = _make_mock_client()
    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        session = SSHSession()
        session.execute("uptime")
        policy = mock_client.set_missing_host_key_policy.call_args[0][0]
        assert isinstance(policy, paramiko.RejectPolicy)
        session.close()
    finally:
        for p in patches:
            p.stop()


# --- CommandResult ---

def test_command_result_to_dict():
    """CommandResult.to_dict() should return proper structure."""
    r = CommandResult(command="uptime", success=True, stdout="up", exit_code=0)
    d = r.to_dict()
    assert d["command"] == "uptime"
    assert d["success"] is True
    assert d["stdout"] == "up"
    assert d["exit_code"] == 0


def test_command_result_to_dict_with_error():
    """CommandResult.to_dict() should include error when present."""
    r = CommandResult(command="uptime", success=False, error="timeout")
    d = r.to_dict()
    assert d["error"] == "timeout"


# --- Session config ---

def test_session_config_defaults():
    """SessionConfig should have sensible defaults."""
    config = SessionConfig()
    assert config.connect_timeout == 10
    assert config.command_timeout == 15
    assert config.idle_timeout == 300
    assert config.max_session_lifetime == 600
    assert config.max_commands == 50


# --- create_session and session_scope ---

def test_create_session_returns_ssh_session():
    """create_session should return an SSHSession instance."""
    session = create_session()
    assert isinstance(session, SSHSession)
    session.close()


def test_session_scope_context_manager():
    """session_scope should yield a session and close it."""
    with session_scope() as session:
        assert isinstance(session, SSHSession)
        assert not session._closed
    assert session._closed


# --- Session properties ---

def test_session_duration():
    """Session should track its duration."""
    with SSHSession() as session:
        assert session.session_duration >= 0


def test_session_is_alive_false_when_no_client():
    """is_alive should be False when no connection exists."""
    with SSHSession() as session:
        assert session.is_alive is False


# --- Audit logging ---

def test_session_rejected_command_is_audit_logged(caplog):
    """Rejected commands should be audit logged."""
    with caplog.at_level("WARNING", logger="ssh_audit"):
        with SSHSession() as session:
            session.execute("rm -rf /")
    assert any("SESSION_REJECTED" in r.message for r in caplog.records)


def test_session_executed_command_is_audit_logged(caplog):
    """Executed commands should be audit logged."""
    mock_client = _make_mock_client()
    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        with caplog.at_level("INFO", logger="ssh_audit"):
            session = SSHSession()
            session.execute("uptime")
            session.close()
    finally:
        for p in patches:
            p.stop()
    assert any("SESSION_EXECUTED" in r.message for r in caplog.records)


# --- Simulated diagnostic workflow ---

def test_diagnostic_workflow_reuses_connection():
    """Simulate a multi-command diagnostic workflow using one session."""
    mock_client = _make_mock_client()
    commands_run = []

    def track_exec(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("command", "")
        commands_run.append(cmd)
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"ok\n"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_stderr = MagicMock()
        mock_stderr.read.return_value = b""
        return (MagicMock(), mock_stdout, mock_stderr)

    mock_client.exec_command.side_effect = track_exec

    patches = [
        patch.object(ssh_session_manager.Config, "VPS_HOST", "1.2.3.4"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_USER", "root"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_KEY_PATH", "/fake/key"),
        patch.object(ssh_session_manager.Config, "VPS_SSH_PORT", 22),
        patch.object(ssh_session_manager.Config, "VPS_KNOWN_HOSTS_PATH", "/fake/known_hosts"),
        patch("app.ssh_session_manager.paramiko.SSHClient", return_value=mock_client),
    ]
    for p in patches:
        p.start()
    try:
        session = SSHSession()
        session.execute("uptime")
        session.execute("nproc")
        session.execute("free -h")
        session.execute("docker ps")
        session.execute("docker stats --no-stream")
        assert len(commands_run) == 5
        assert mock_client.connect.call_count == 1
        assert session.command_count == 5
        session.close()
    finally:
        for p in patches:
            p.stop()
