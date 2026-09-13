import asyncio
import logging
import os
import subprocess
import time
import uuid
from enum import Enum

import paramiko

logger = logging.getLogger("terminal")


class SessionState(str, Enum):
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CLOSED = "closed"


class TerminalSession:
    def __init__(self, host, port, username, key_path, known_hosts_path=None):
        self.id = str(uuid.uuid4())[:12]
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self.known_hosts_path = known_hosts_path
        self.state = SessionState.CONNECTING
        self.created_at = time.time()
        self.last_activity = time.time()
        self.ssh_client = None
        self.ssh_channel = None
        self.rows = 24
        self.cols = 80

    async def connect(self):
        loop = asyncio.get_event_loop()
        try:
            self.ssh_client = await loop.run_in_executor(None, self._connect_ssh)
            transport = self.ssh_client.get_transport()
            if transport is None:
                raise ConnectionError("No SSH transport")
            self.ssh_channel = await loop.run_in_executor(None, lambda: transport.open_session())
            self.ssh_channel.get_pty("xterm-256color", self.cols, self.rows)
            self.ssh_channel.invoke_shell()
            self.state = SessionState.CONNECTED
            self.last_activity = time.time()
            logger.info("terminal_connected session=%s host=%s", self.id, self.host)
        except Exception as e:
            self.state = SessionState.DISCONNECTED
            logger.error("terminal_connect_failed session=%s error=%s", self.id, e)
            raise

    def _connect_ssh(self):
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        if self.known_hosts_path and os.path.exists(self.known_hosts_path):
            client.load_host_keys(self.known_hosts_path)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            key_filename=self.key_path,
            timeout=10,
        )
        return client

    def resize(self, rows, cols):
        self.rows = rows
        self.cols = cols
        if self.ssh_channel and self.state == SessionState.CONNECTED:
            self.ssh_channel.resize_pty(width=cols, height=rows)
            self.last_activity = time.time()

    async def read_output(self):
        if not self.ssh_channel or self.state != SessionState.CONNECTED:
            return None
        loop = asyncio.get_event_loop()
        try:
            data = await loop.run_in_executor(None, lambda: self.ssh_channel.read(65536))
            if data:
                self.last_activity = time.time()
                return data.decode("utf-8", errors="replace")
        except Exception:
            self.state = SessionState.DISCONNECTED
        return None

    async def write_input(self, data):
        if not self.ssh_channel or self.state != SessionState.CONNECTED:
            return False
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: self.ssh_channel.send(data.encode("utf-8")))
            self.last_activity = time.time()
            return True
        except Exception:
            self.state = SessionState.DISCONNECTED
            return False

    def is_alive(self):
        if self.ssh_channel and self.state == SessionState.CONNECTED:
            return not self.ssh_channel.closed
        return False

    def close(self):
        self.state = SessionState.CLOSED
        try:
            if self.ssh_channel:
                self.ssh_channel.close()
        except Exception:
            pass
        try:
            if self.ssh_client:
                self.ssh_client.close()
        except Exception:
            pass
        self.ssh_channel = None
        self.ssh_client = None
        logger.info("terminal_closed session=%s", self.id)


class LocalTerminalSession:
    """Terminal session using a local PTY (no SSH)."""

    def __init__(self):
        self.id = str(__import__("uuid").uuid4())[:12]
        self.state = SessionState.CONNECTING
        self.created_at = time.time()
        self.last_activity = time.time()
        self.rows = 24
        self.cols = 80
        self._process = None
        self._master_fd = None

    async def connect(self):
        import pty
        import fcntl
        loop = asyncio.get_event_loop()
        try:
            self._master_fd, slave_fd = pty.openpty()
            # Set initial size
            import struct, termios
            winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
            # Start shell
            self._process = await loop.run_in_executor(None, lambda: subprocess.Popen(
                ["/bin/bash"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                close_fds=True,
            ))
            os.close(slave_fd)
            # Set non-blocking
            import fcntl
            flags = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            self.state = SessionState.CONNECTED
            self.last_activity = time.time()
            logger.info("local_terminal_connected session=%s", self.id)
        except Exception as e:
            self.state = SessionState.DISCONNECTED
            logger.error("local_terminal_connect_failed session=%s error=%s", self.id, e)
            raise

    def resize(self, rows, cols):
        import fcntl, struct, termios
        self.rows = rows
        self.cols = cols
        if self._master_fd is not None and self.state == SessionState.CONNECTED:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            self.last_activity = time.time()

    async def read_output(self):
        if self._master_fd is None or self.state != SessionState.CONNECTED:
            return None
        loop = asyncio.get_event_loop()
        try:
            def _read():
                try:
                    return os.read(self._master_fd, 65536)
                except OSError:
                    return b""
            data = await loop.run_in_executor(None, _read)
            if data:
                self.last_activity = time.time()
                return data.decode("utf-8", errors="replace")
        except Exception:
            self.state = SessionState.DISCONNECTED
        return None

    async def write_input(self, data):
        if self._master_fd is None or self.state != SessionState.CONNECTED:
            return False
        loop = asyncio.get_event_loop()
        try:
            def _write():
                os.write(self._master_fd, data.encode("utf-8"))
            await loop.run_in_executor(None, _write)
            self.last_activity = time.time()
            return True
        except Exception:
            self.state = SessionState.DISCONNECTED
            return False

    def is_alive(self):
        if self._process and self.state == SessionState.CONNECTED:
            return self._process.poll() is None
        return False

    def close(self):
        self.state = SessionState.CLOSED
        try:
            if self._master_fd is not None:
                os.close(self._master_fd)
        except Exception:
            pass
        try:
            if self._process:
                self._process.terminate()
        except Exception:
            pass
        self._master_fd = None
        self._process = None
        logger.info("local_terminal_closed session=%s", self.id)
