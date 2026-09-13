"""
Scoped SSH Session Manager (Phase 5).

Provides a safe, bounded SSH session that reuses a single connection for
multiple commands within a diagnostic/action workflow, then closes cleanly.

Design principles:
- ONE connection per session, reused for all commands in that workflow.
- Every command STILL passes through the whitelist (app/ssh_whitelist.py).
- Bounded: max commands, max duration, idle timeout, output size.
- Context-manager support: `with SSHSession(...) as session:`.
- Thread-safe: uses a threading.Lock around connection state.
- Audit-logged: every command executed or rejected is logged.
- Automatic reconnect on transient connection failure.
- Clean close: always closes the underlying paramiko connection.

This does NOT create a permanent connection or a pool. A CLI workflow
creates a session, runs several commands, and closes it. A small pool
can be added later if concurrent jobs are needed.
"""
import logging
import os
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

import paramiko

from app.config import Config
from app.ssh_whitelist import is_command_allowed

logger = logging.getLogger("ssh_session_manager")
audit_logger = logging.getLogger("ssh_audit")

# --- Defaults ---
DEFAULT_CONNECT_TIMEOUT = 10       # seconds to establish connection
DEFAULT_COMMAND_TIMEOUT = 15       # seconds per command
DEFAULT_IDLE_TIMEOUT = 300         # 5 min idle → auto-close
DEFAULT_MAX_SESSION_LIFETIME = 600 # 10 min max lifetime
DEFAULT_MAX_COMMANDS = 50          # bounded command count per session
DEFAULT_MAX_OUTPUT_CHARS = 4000    # per-command output limit
RETRY_DELAY_SECONDS = 2
MAX_RETRIES = 1                    # retry once on transient errors

_TRANSIENT_EXCEPTIONS = (socket.timeout, ConnectionRefusedError, paramiko.SSHException)


def _truncate(text: str, max_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> str:
    if text and len(text) > max_chars:
        return text[:max_chars] + f"\n...[truncated, {len(text)} chars total]"
    return text or ""


@dataclass
class SessionConfig:
    """Configuration for a scoped SSH session."""
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT
    idle_timeout: int = DEFAULT_IDLE_TIMEOUT
    max_session_lifetime: int = DEFAULT_MAX_SESSION_LIFETIME
    max_commands: int = DEFAULT_MAX_COMMANDS
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS


@dataclass
class CommandResult:
    """Result of a single command executed within a session."""
    command: str
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_seconds: float = 0.0
    error: str = ""
    rejected: bool = False

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "command": self.command,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }
        if self.error:
            d["error"] = self.error
        return d


class SSHSession:
    """
    A scoped SSH session that reuses one connection for multiple commands.

    Usage as context manager (preferred):

        with SSHSession() as session:
            result1 = session.execute("uptime")
            result2 = session.execute("free -h")
            result3 = session.execute("docker ps")

    Or manual lifecycle:

        session = SSHSession()
        try:
            session.execute("uptime")
        finally:
            session.close()

    Every command passes through the whitelist before any SSH interaction.
    The session enforces max commands, idle timeout, and session lifetime.
    """

    def __init__(self, config: Optional[SessionConfig] = None, host: Optional[str] = None):
        self._config = config or SessionConfig()
        self._host = host or Config.VPS_HOST
        self._client: Optional[paramiko.SSHClient] = None
        self._lock = threading.RLock()  # reentrant: is_alive can be called while execute holds the lock
        self._command_count = 0
        self._session_start = time.time()
        self._last_activity = time.time()
        self._closed = False
        self._commands_executed: list = []

    def __enter__(self) -> "SSHSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    @property
    def is_alive(self) -> bool:
        with self._lock:
            if self._client is None:
                return False
            transport = self._client.get_transport()
            return transport is not None and transport.is_active()

    @property
    def command_count(self) -> int:
        return self._command_count

    @property
    def session_duration(self) -> float:
        return time.time() - self._session_start

    def _check_session_limits(self) -> Optional[str]:
        """Return an error string if session limits are exceeded, else None."""
        if self._closed:
            return "Session is closed."
        if self._command_count >= self._config.max_commands:
            return f"Session command limit reached ({self._config.max_commands})."
        if self.session_duration >= self._config.max_session_lifetime:
            return f"Session lifetime exceeded ({self._config.max_session_lifetime}s)."
        if time.time() - self._last_activity >= self._config.idle_timeout:
            return f"Session idle timeout ({self._config.idle_timeout}s)."
        return None

    def _connect(self) -> None:
        """Establish a new SSH connection. Must be called with _lock held."""
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        known_hosts = Config.VPS_KNOWN_HOSTS_PATH
        if known_hosts and os.path.exists(known_hosts):
            client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=self._host,
            username=Config.VPS_SSH_USER,
            key_filename=Config.VPS_SSH_KEY_PATH,
            port=Config.VPS_SSH_PORT,
            timeout=self._config.connect_timeout,
        )
        self._client = client
        self._last_activity = time.time()
        audit_logger.info("SESSION_CONNECTED host=%s user=%s", self._host, Config.VPS_SSH_USER)

    def _ensure_connected(self) -> None:
        """Ensure we have a live connection. Must be called with _lock held."""
        if not self.is_alive:
            self._close_client()
            self._connect()

    def _close_client(self) -> None:
        """Close the underlying paramiko client. Must be called with _lock held."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def execute(self, command: str) -> CommandResult:
        """
        Execute a whitelisted command on the VPS using this session's connection.

        Returns a CommandResult. Never raises.
        """
        # Whitelist check first — before any connection activity
        if not is_command_allowed(command):
            audit_logger.warning("SESSION_REJECTED command=%r", command)
            self._commands_executed.append(CommandResult(
                command=command, success=False, rejected=True,
                error=f"Command not permitted: '{command}'"
            ))
            return self._commands_executed[-1]

        # Validate VPS config
        if not self._host or not Config.VPS_SSH_USER:
            result = CommandResult(
                command=command, success=False,
                error="VPS is not configured. Set VPS_HOST and VPS_SSH_USER in .env."
            )
            self._commands_executed.append(result)
            return result

        # Check session limits
        limit_error = self._check_session_limits()
        if limit_error:
            result = CommandResult(command=command, success=False, error=limit_error)
            self._commands_executed.append(result)
            return result

        # Execute with connection management and retry
        start = time.time()
        for attempt in range(1, MAX_RETRIES + 2):  # 1..MAX_RETRIES+1
            with self._lock:
                try:
                    self._ensure_connected()
                    stdin, stdout, stderr = self._client.exec_command(
                        command, timeout=self._config.command_timeout
                    )
                    exit_code = stdout.channel.recv_exit_status()
                    out = stdout.read().decode(errors="replace")
                    err = stderr.read().decode(errors="replace")

                    duration = round(time.time() - start, 2)
                    self._command_count += 1
                    self._last_activity = time.time()

                    result = CommandResult(
                        command=command,
                        success=True,
                        stdout=_truncate(out, self._config.max_output_chars),
                        stderr=_truncate(err, self._config.max_output_chars),
                        exit_code=exit_code,
                        duration_seconds=duration,
                    )
                    self._commands_executed.append(result)

                    logger.info(
                        "session_command command=%r exit_code=%s duration=%ss "
                        "attempt=%d session_cmds=%d",
                        command, exit_code, duration, attempt, self._command_count,
                    )
                    audit_logger.info(
                        "SESSION_EXECUTED command=%r exit_code=%s duration=%ss "
                        "attempt=%d session_cmds=%d",
                        command, exit_code, duration, attempt, self._command_count,
                    )
                    return result

                except paramiko.AuthenticationException:
                    audit_logger.error("SESSION_AUTH_FAILED command=%r", command)
                    return CommandResult(
                        command=command, success=False,
                        error="SSH authentication failed — check your SSH key and username."
                    )

                except socket.gaierror:
                    audit_logger.error("SESSION_HOST_UNRESOLVABLE command=%r", command)
                    return CommandResult(
                        command=command, success=False,
                        error=f"Couldn't resolve host '{self._host}'."
                    )

                except FileNotFoundError:
                    audit_logger.error("SESSION_KEY_NOT_FOUND command=%r", command)
                    return CommandResult(
                        command=command, success=False,
                        error="SSH key file not found. Check VPS_SSH_KEY_PATH in your .env."
                    )

                except paramiko.BadHostKeyException:
                    self._close_client()
                    audit_logger.error("SESSION_HOST_KEY_MISMATCH command=%r", command)
                    return CommandResult(
                        command=command, success=False,
                        error=(
                            "Host key verification failed — the VPS's key doesn't match "
                            "what's saved in known_hosts."
                        ),
                    )

                except _TRANSIENT_EXCEPTIONS as e:
                    self._close_client()
                    if attempt <= MAX_RETRIES:
                        logger.warning(
                            "session_transient_error error_type=%s attempt=%d — retrying",
                            type(e).__name__, attempt,
                        )
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue
                    audit_logger.error(
                        "SESSION_FAILED command=%r error_type=%s (after retry)",
                        command, type(e).__name__,
                    )
                    if isinstance(e, socket.timeout):
                        return CommandResult(command=command, success=False,
                            error="Command timed out (after retry).")
                    if isinstance(e, ConnectionRefusedError):
                        return CommandResult(command=command, success=False,
                            error="Connection refused (after retry).")
                    return CommandResult(command=command, success=False,
                        error="Couldn't reach the VPS over SSH (after retry).")

                except Exception as e:
                    self._close_client()
                    audit_logger.error(
                        "SESSION_UNEXPECTED_ERROR command=%r error_type=%s",
                        command, type(e).__name__,
                    )
                    return CommandResult(command=command, success=False,
                        error="Unexpected error while executing command.")

        return CommandResult(command=command, success=False,
            error="Unexpected error: exhausted retries.")

    def close(self) -> None:
        """Close the SSH session and release the connection."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._close_client()
            audit_logger.info(
                "SESSION_CLOSED duration=%ss commands=%d",
                round(self.session_duration, 2), self._command_count,
            )
            logger.info(
                "session_closed duration=%ss commands=%d",
                round(self.session_duration, 2), self._command_count,
            )

    @property
    def commands_executed(self) -> list:
        """Return list of CommandResult objects for this session."""
        return list(self._commands_executed)


def create_session(config: Optional[SessionConfig] = None) -> SSHSession:
    """Create a new scoped SSH session. Caller is responsible for closing it."""
    return SSHSession(config=config)


@contextmanager
def session_scope(config: Optional[SessionConfig] = None):
    """Context manager that yields an SSHSession and ensures it is closed."""
    session = SSHSession(config=config)
    try:
        yield session
    finally:
        session.close()
