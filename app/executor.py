"""
Unified execution abstraction for JARVIS.

Supports two modes:
  EXECUTION_MODE=ssh   — execute commands on a remote VPS via SSH (laptop dev)
  EXECUTION_MODE=local — execute commands on the local machine (VPS deployment)

All existing tools call run_command() from this module. The AI does not need
to know whether execution is local or remote.

The SSH whitelist is enforced in BOTH modes — it's a security boundary,
not an SSH feature.
"""
import logging
import os
import subprocess
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import paramiko

from app.config import Config
from app.ssh_whitelist import is_command_allowed, is_action_command_allowed

logger = logging.getLogger("executor")
audit_logger = logging.getLogger("ssh_audit")

# ── Constants ──────────────────────────────────────────────────

CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 15
MAX_OUTPUT_CHARS = 4000
RETRY_DELAY_SECONDS = 2

_TRANSIENT_EXCEPTIONS = (socket.timeout, ConnectionRefusedError, paramiko.SSHException)


def truncate(text: str, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    if text and len(text) > max_chars:
        return text[:max_chars] + f"\n...[truncated, {len(text)} chars total]"
    return text or ""


# ── Executor Interface ─────────────────────────────────────────

class Executor:
    """Base class for command execution backends."""

    def execute(self, command: str) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def execute_many(self, commands: list[str], max_workers: int = 4) -> list[dict]:
        """Run independent read-only commands concurrently."""
        with ThreadPoolExecutor(max_workers=min(max_workers, len(commands) or 1)) as pool:
            futures = {pool.submit(self.execute, command): index for index, command in enumerate(commands)}
            results = [None] * len(commands)
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results


# ── SSH Executor ───────────────────────────────────────────────

class SSHExecutor(Executor):
    """
    Execute commands on a remote VPS via SSH.
    Reuses a single connection across calls (like the old SSHConnectionManager).
    Thread-safe: _lock guards the shared paramiko client so concurrent
    read-only health checks cannot race.
    """

    def __init__(self):
        self._client: Optional[paramiko.SSHClient] = None
        self._lock = threading.RLock()  # Re-entrant so retry works within same thread

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
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=Config.VPS_HOST,
            username=Config.VPS_SSH_USER,
            key_filename=Config.VPS_SSH_KEY_PATH,
            port=Config.VPS_SSH_PORT,
            timeout=CONNECT_TIMEOUT,
        )
        self._client = client

    def _get_client(self) -> paramiko.SSHClient:
        if not self._is_alive():
            self.close()
            self._connect()
        return self._client

    def execute(self, command: str) -> dict:
        start = time.time()
        with self._lock:
            for attempt in (1, 2):
                try:
                    client = self._get_client()
                    stdin, stdout, stderr = client.exec_command(command, timeout=COMMAND_TIMEOUT)
                    exit_code = stdout.channel.recv_exit_status()
                    out = stdout.read().decode(errors="replace")
                    err = stderr.read().decode(errors="replace")
                    duration = round(time.time() - start, 2)
                    result = {
                        "success": True,
                        "command": command,
                        "stdout": truncate(out),
                        "stderr": truncate(err),
                        "exit_code": exit_code,
                    }
                    logger.info("ssh_executed command=%r exit_code=%s duration=%ss attempt=%d",
                                command, exit_code, duration, attempt)
                    audit_logger.info("EXECUTED command=%r exit_code=%s duration=%ss attempt=%d",
                                       command, exit_code, duration, attempt)
                    return result

                except paramiko.AuthenticationException:
                    audit_logger.error("AUTH_FAILED command=%r", command)
                    return {"success": False, "error": "SSH authentication failed — check your SSH key and username."}

                except socket.gaierror:
                    audit_logger.error("HOST_UNRESOLVABLE command=%r", command)
                    return {"success": False, "error": f"Couldn't resolve host '{Config.VPS_HOST}'."}

                except FileNotFoundError:
                    audit_logger.error("KEY_NOT_FOUND command=%r", command)
                    return {"success": False, "error": "SSH key file not found. Check VPS_SSH_KEY_PATH in your .env."}

                except paramiko.BadHostKeyException:
                    audit_logger.error("HOST_KEY_MISMATCH command=%r", command)
                    self.close()
                    return {"success": False, "error": "Host key verification failed — the VPS key doesn't match known_hosts."}

                except _TRANSIENT_EXCEPTIONS as e:
                    self.close()
                    if attempt == 1:
                        logger.warning("ssh_transient_error type=%s retrying", type(e).__name__)
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue
                    audit_logger.error("FAILED command=%r type=%s (after retry)", command, type(e).__name__)
                    if isinstance(e, socket.timeout):
                        return {"success": False, "error": "Connection to the VPS timed out (after retry)."}
                    if isinstance(e, ConnectionRefusedError):
                        return {"success": False, "error": "Connection refused (after retry)."}
                    return {"success": False, "error": "Couldn't reach the VPS over SSH (after retry)."}

                except Exception as e:
                    audit_logger.error("UNEXPECTED_ERROR command=%r type=%s", command, type(e).__name__)
                    self.close()
                    return {"success": False, "error": "Unexpected error while connecting to the VPS."}

        return {"success": False, "error": "Unexpected error: exhausted retries."}

    def execute_many(self, commands: list[str], max_workers: int = 4) -> list[dict]:
        """Open parallel channels on the one reused SSH transport.

        The connection lifecycle remains protected; only independent command
        channels run concurrently. Callers must pass read-only whitelisted
        commands through run_commands_batch().
        """
        if not commands:
            return []
        with self._lock:
            client = self._get_client()
            def one(command: str) -> dict:
                try:
                    stdin, stdout, stderr = client.exec_command(command, timeout=COMMAND_TIMEOUT)
                    exit_code = stdout.channel.recv_exit_status()
                    return {"success": True, "command": command, "stdout": truncate(stdout.read().decode(errors="replace")), "stderr": truncate(stderr.read().decode(errors="replace")), "exit_code": exit_code}
                except Exception:
                    return {"success": False, "command": command, "error": "Diagnostic command failed."}
            with ThreadPoolExecutor(max_workers=min(max_workers, len(commands))) as pool:
                futures = {pool.submit(one, command): index for index, command in enumerate(commands)}
                results = [None] * len(commands)
                for future in as_completed(futures):
                    results[futures[future]] = future.result()
            return results

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


# ── Local Executor ─────────────────────────────────────────────

class LocalExecutor(Executor):
    """
    Execute commands directly on the local machine.
    No SSH involved — runs subprocess directly.
    """

    def execute(self, command: str) -> dict:
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                env=os.environ.copy(),
            )
            duration = round(time.time() - start, 2)
            result = {
                "success": True,
                "command": command,
                "stdout": truncate(proc.stdout),
                "stderr": truncate(proc.stderr),
                "exit_code": proc.returncode,
            }
            logger.info("local_executed command=%r exit_code=%s duration=%ss",
                        command, proc.returncode, duration)
            audit_logger.info("EXECUTED command=%r exit_code=%s duration=%ss (local)",
                               command, proc.returncode, duration)
            return result

        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Command timed out after {COMMAND_TIMEOUT}s."}
        except FileNotFoundError:
            return {"success": False, "error": f"Command not found: '{command.split()[0]}'."}
        except Exception as e:
            logger.error("local_error command=%r type=%s", command, type(e).__name__)
            return {"success": False, "error": f"Local execution error: {e}"}


# ── Singleton ──────────────────────────────────────────────────

_executor: Optional[Executor] = None


def get_executor() -> Executor:
    """Get the appropriate executor based on EXECUTION_MODE."""
    global _executor
    if _executor is None:
        mode = Config.EXECUTION_MODE.lower()
    
        if mode == "local":
            logger.info("executor_mode=local")
            _executor = LocalExecutor()
        else:
            if not Config.VPS_HOST or not Config.VPS_SSH_USER:
                logger.warning("executor_mode=ssh but VPS_HOST/VPS_SSH_USER not set")
            logger.info("executor_mode=ssh host=%s", Config.VPS_HOST)
            _executor = SSHExecutor()
    return _executor


def close_executor() -> None:
    global _executor
    if _executor is not None:
        _executor.close()
        _executor = None


def run_command(command: str) -> dict:
    if not is_command_allowed(command):
        logger.warning("command_rejected command=%r", command)
        audit_logger.warning("REJECTED command=%r", command)
        return {"success": False, "error": f"Command not permitted: '{command}'"}

    mode = Config.EXECUTION_MODE.lower()
    if mode == "ssh" and (not Config.VPS_HOST or not Config.VPS_SSH_USER):
        return {"success": False, "error": "VPS is not configured. Set VPS_HOST and VPS_SSH_USER in .env."}

    executor = get_executor()
    return executor.execute(command)


def run_action_command(command: str) -> dict:
    """Execute a policy-approved mutation through the selected local/SSH executor."""
    if not is_action_command_allowed(command):
        return {"success": False, "error": "Action command is not whitelisted"}
    return get_executor().execute(command)


def run_commands_batch(commands: list[str], max_workers: int = 4) -> list[dict]:
    """Run multiple independent, read-only whitelisted commands in parallel.

    This is deliberately separate from run_command so mutation paths remain
    serialized and retain the existing audit/approval behavior.
    """
    accepted = []
    results = [None] * len(commands)
    for index, command in enumerate(commands):
        if is_command_allowed(command):
            accepted.append((index, command))
        else:
            results[index] = {"success": False, "command": command, "error": f"Command not permitted: '{command}'"}
    if not accepted:
        return results
    mode = Config.EXECUTION_MODE.lower()
    if mode == "ssh" and (not Config.VPS_HOST or not Config.VPS_SSH_USER):
        return [{"success": False, "command": command, "error": "VPS is not configured."} if item is None else item for command, item in zip(commands, results)]
    batch = get_executor().execute_many([command for _, command in accepted], max_workers=max_workers)
    for (index, _), result in zip(accepted, batch):
        results[index] = result
    return results
