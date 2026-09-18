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


def close_connection():
    """Close the executor (backward-compatible entry point)."""
    from app.executor import close_executor
    close_executor()


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
    Execute a whitelisted command — delegates to the unified executor.
    In SSH mode: runs on the remote VPS via SSH.
    In local mode: runs directly on this machine.
    Preserved for backward compatibility with all existing tool imports.
    """
    from app.executor import run_command
    return run_command(command)


def run_whitelisted_commands(commands: list[str], max_workers: int = 4) -> list[dict]:
    """Batch read-only checks over one reused execution connection."""
    from app.executor import run_commands_batch
    return run_commands_batch(commands, max_workers=max_workers)
