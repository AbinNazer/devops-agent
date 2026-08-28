"""
SSH abstraction. This is the ONLY place that opens a network connection to
the VPS or executes anything on it. Every tool routes through
run_whitelisted_command() here, which checks the whitelist BEFORE
attempting any connection — a rejected command never even reaches
paramiko.

Features beyond the Phase 2 baseline:
- Connection reuse: one SSH connection is kept alive and reused across
  calls within a process (instead of a full handshake per tool call),
  reconnecting automatically if it's dropped.
- Retry-once on transient failures (timeout, connection refused, generic
  SSH protocol errors) — safe here because every whitelisted command is
  read-only, so retrying has no side effects.
- Host key verification: the VPS's key must already be in
  VPS_KNOWN_HOSTS_PATH. Unknown or mismatched keys are rejected, not
  silently trusted (the old AutoAddPolicy default was a MITM gap).
- A dedicated audit log (via the "ssh_audit" logger, configured in
  logging_config.py) records every command actually sent to the VPS,
  executed or rejected.

Never logs SSH key contents, passwords, or full exception details that
might include them — only safe, generic descriptions of what went wrong.
"""
import logging
import os
import socket
import time
from typing import Optional

import paramiko

from app.config import Config
from app.ssh_whitelist import is_command_allowed

logger = logging.getLogger("ssh_client")
audit_logger = logging.getLogger("ssh_audit")

CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 15
MAX_OUTPUT_CHARS = 4000  # keep any single command's output from flooding the LLM's context
RETRY_DELAY_SECONDS = 2

# Exceptions worth retrying once — all read-only commands, so a retry has
# no side effects. Auth failures, missing keys, and host-key mismatches are
# NOT retried since a second attempt can't succeed differently.
_TRANSIENT_EXCEPTIONS = (socket.timeout, ConnectionRefusedError, paramiko.SSHException)


def truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if text and len(text) > max_chars:
        return text[:max_chars] + f"\n...[truncated, {len(text)} chars total]"
    return text or ""


class SSHConnectionManager:
    """
    Keeps a single reusable SSH connection open across multiple commands
    (e.g. several tool calls in one agent turn), instead of a fresh
    handshake per command. Reconnects automatically if the connection has
    dropped.
    """

    def __init__(self):
        self._client: Optional[paramiko.SSHClient] = None

    def _is_alive(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def _connect(self) -> None:
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        known_hosts = Config.VPS_KNOWN_HOSTS_PATH
        if known_hosts and os.path.exists(known_hosts):
            client.load_host_keys(known_hosts)
        # Reject unknown/mismatched host keys instead of silently trusting
        # them. The VPS's key must already be known (ssh-keyscan, or one
        # prior manual `ssh` login that accepted and saved it).
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=Config.VPS_HOST,
            username=Config.VPS_SSH_USER,
            key_filename=Config.VPS_SSH_KEY_PATH,
            port=Config.VPS_SSH_PORT,
            timeout=CONNECT_TIMEOUT,
        )
        self._client = client

    def get_client(self) -> paramiko.SSHClient:
        if not self._is_alive():
            self.close()
            self._connect()
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


_manager = SSHConnectionManager()


def close_connection() -> None:
    """Explicitly close the reused SSH connection (e.g. on CLI exit)."""
    _manager.close()


def _execute_once(command: str) -> dict:
    client = _manager.get_client()
    stdin, stdout, stderr = client.exec_command(command, timeout=COMMAND_TIMEOUT)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    return {
        "success": True,
        "command": command,
        "stdout": truncate(out),
        "stderr": truncate(err),
        "exit_code": exit_code,
    }


def run_whitelisted_command(command: str) -> dict:
    """
    Execute a single command on the VPS over SSH — ONLY if it matches an
    entry in the whitelist. Returns a structured dict; never raises.

    Success:  {"success": True, "command": ..., "stdout": ..., "stderr": ..., "exit_code": ...}
    Failure:  {"success": False, "error": "<safe, user-facing message>"}
    """
    if not is_command_allowed(command):
        logger.warning("ssh_command_rejected command=%r", command)
        audit_logger.warning("REJECTED command=%r", command)
        return {"success": False, "error": f"Command not permitted: '{command}'"}

    if not Config.VPS_HOST or not Config.VPS_SSH_USER:
        return {"success": False, "error": "VPS is not configured. Set VPS_HOST and VPS_SSH_USER in .env."}

    start = time.time()

    for attempt in (1, 2):
        try:
            result = _execute_once(command)
            duration = round(time.time() - start, 2)
            logger.info("ssh_command_executed command=%r exit_code=%s duration=%ss attempt=%d",
                        command, result["exit_code"], duration, attempt)
            audit_logger.info("EXECUTED command=%r exit_code=%s duration=%ss attempt=%d",
                               command, result["exit_code"], duration, attempt)
            return result

        except paramiko.AuthenticationException:
            logger.error("ssh_auth_failed host=%s user=%s", Config.VPS_HOST, Config.VPS_SSH_USER)
            audit_logger.error("AUTH_FAILED command=%r", command)
            return {"success": False, "error": "SSH authentication failed — check your SSH key and username."}

        except socket.gaierror:
            logger.error("ssh_host_unresolvable host=%s", Config.VPS_HOST)
            audit_logger.error("HOST_UNRESOLVABLE command=%r", command)
            return {"success": False, "error": f"Couldn't resolve host '{Config.VPS_HOST}'."}

        except FileNotFoundError:
            logger.error("ssh_key_not_found")
            audit_logger.error("KEY_NOT_FOUND command=%r", command)
            return {"success": False, "error": "SSH key file not found. Check VPS_SSH_KEY_PATH in your .env."}

        except paramiko.BadHostKeyException:
            logger.error("ssh_host_key_mismatch host=%s", Config.VPS_HOST)
            audit_logger.error("HOST_KEY_MISMATCH command=%r", command)
            _manager.close()
            return {
                "success": False,
                "error": (
                    "Host key verification failed — the VPS's key doesn't match "
                    "what's saved in known_hosts. This could mean the server was "
                    "rebuilt, or something is intercepting the connection. "
                    "Verify manually before proceeding."
                ),
            }

        except _TRANSIENT_EXCEPTIONS as e:
            _manager.close()
            if attempt == 1:
                logger.warning("ssh_transient_error error_type=%s attempt=%d — retrying", type(e).__name__, attempt)
                time.sleep(RETRY_DELAY_SECONDS)
                continue
            logger.error("ssh_transient_error_exhausted error_type=%s", type(e).__name__)
            audit_logger.error("FAILED command=%r error_type=%s (after retry)", command, type(e).__name__)
            if isinstance(e, socket.timeout):
                return {"success": False, "error": "Connection to the VPS timed out (after retry)."}
            if isinstance(e, ConnectionRefusedError):
                return {"success": False, "error": "Connection refused (after retry) — SSH may not be running on that host/port."}
            return {"success": False, "error": "Couldn't reach the VPS over SSH (after retry). The server may be offline or SSH may be unavailable."}

        except Exception as e:
            logger.error("ssh_unexpected_error error_type=%s", type(e).__name__)
            audit_logger.error("UNEXPECTED_ERROR command=%r error_type=%s", command, type(e).__name__)
            _manager.close()
            return {"success": False, "error": "Unexpected error while connecting to the VPS."}

    return {"success": False, "error": "Unexpected error: exhausted retries without a result."}
