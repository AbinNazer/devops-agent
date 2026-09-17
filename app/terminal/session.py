"""
Terminal session backends.

TerminalSession   – SSH PTY session to a remote VPS (via Paramiko).
LocalTerminalSession – Local PTY session using /bin/bash (WSL / local mode).

Both implement the same interface:
  connect()         – async, sets up the PTY / SSH channel.
  read_output()     – async, blocks until data is available (event-driven).
  write_input(data) – async, sends bytes into the shell.
  resize(rows, cols)
  is_alive()        – synchronous liveness probe.
  close()           – synchronous teardown.

Key design:
  LocalTerminalSession uses loop.add_reader() on the master PTY fd so the
  event loop wakes on actual data instead of spinning a thread that polls
  O_NONBLOCK every 10 ms.  Data is pushed into an asyncio.Queue; read_output
  awaits the queue — zero CPU when the terminal is idle.

  TerminalSession (SSH) uses a dedicated daemon thread that calls
  channel.recv() (which blocks efficiently inside paramiko/libssh2).  Data
  is put onto the same asyncio.Queue pattern via call_soon_threadsafe.
"""
import asyncio
import logging
import os
import subprocess
import threading
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


# ─────────────────────────────────────────────────────────────
# SSH session (VPS mode)
# ─────────────────────────────────────────────────────────────

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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._reader_thread: threading.Thread | None = None

    async def connect(self):
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        try:
            self.ssh_client = await self._loop.run_in_executor(None, self._connect_ssh)
            transport = self.ssh_client.get_transport()
            if transport is None:
                raise ConnectionError("No SSH transport")
            self.ssh_channel = await self._loop.run_in_executor(
                None, lambda: transport.open_session()
            )
            self.ssh_channel.get_pty("xterm-256color", self.cols, self.rows)
            self.ssh_channel.invoke_shell()
            self.state = SessionState.CONNECTED
            self.last_activity = time.time()

            # Dedicated reader thread — channel.recv() blocks efficiently
            self._reader_thread = threading.Thread(
                target=self._ssh_reader, daemon=True, name=f"ssh-reader-{self.id}"
            )
            self._reader_thread.start()
            logger.info("terminal_connected session=%s host=%s", self.id, self.host)
        except Exception as e:
            self.state = SessionState.DISCONNECTED
            logger.error("terminal_connect_failed session=%s error=%s", self.id, e)
            raise

    def _ssh_reader(self):
        """Background thread: read from SSH channel, push to asyncio queue."""
        while self.state == SessionState.CONNECTED:
            ch = self.ssh_channel
            if ch is None or ch.closed:
                break
            try:
                data = ch.recv(65536)
                if not data:
                    break  # EOF / channel closed
                if self._loop and not self._loop.is_closed() and self._queue is not None:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
                    # Update activity timestamp (best-effort, no lock needed)
                    self.last_activity = time.time()
            except Exception:
                break
        # Signal EOF to any waiter
        self.state = SessionState.DISCONNECTED
        if self._loop and not self._loop.is_closed() and self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

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
            try:
                self.ssh_channel.resize_pty(width=cols, height=rows)
            except Exception:
                pass
            self.last_activity = time.time()

    async def read_output(self):
        """Block until data arrives from the SSH channel (queue-backed, no polling)."""
        if self._queue is None or self.state not in (
            SessionState.CONNECTED, SessionState.DISCONNECTED
        ):
            return None
        try:
            data = await self._queue.get()
            if data is None:
                return None  # EOF sentinel
            self.last_activity = time.time()
            return data.decode("utf-8", errors="replace")
        except Exception:
            self.state = SessionState.DISCONNECTED
            return None

    async def write_input(self, data):
        if not self.ssh_channel or self.state != SessionState.CONNECTED:
            return False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, lambda: self.ssh_channel.send(data.encode("utf-8"))
            )
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
        # Send EOF sentinel so read_output() unblocks
        if self._loop and not self._loop.is_closed() and self._queue is not None:
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
            except Exception:
                pass
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


# ─────────────────────────────────────────────────────────────
# Local PTY session (local / WSL mode)
# ─────────────────────────────────────────────────────────────

class LocalTerminalSession:
    """Terminal session using a local PTY (no SSH).

    Uses loop.add_reader() so the event loop wakes only when data is
    available on the master PTY fd — eliminates the 100 calls/sec thread
    pool pattern of the previous O_NONBLOCK polling approach.
    """

    def __init__(self):
        self.id = str(uuid.uuid4())[:12]
        self.state = SessionState.CONNECTING
        self.created_at = time.time()
        self.last_activity = time.time()
        self.rows = 24
        self.cols = 80
        self._process = None
        self._master_fd: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None

    async def connect(self):
        import pty
        import fcntl
        import struct
        import termios

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        try:
            self._master_fd, slave_fd = pty.openpty()

            # Set initial window size
            winsize = struct.pack("HHHH", self.rows, self.cols, 0, 0)
            fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)

            # Launch shell with slave as its controlling terminal
            self._process = subprocess.Popen(
                ["/bin/bash"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=os.setsid,
                close_fds=True,
            )
            os.close(slave_fd)

            # Keep fd in blocking mode — add_reader handles readiness.
            # (O_NONBLOCK is NOT set here; the reader callback does a single
            #  non-failing read because the fd is already readable when called.)
            # However, we still set NONBLOCK so reads inside the callback cannot
            # ever block if the kernel reports false readiness (rare but possible).
            flags = fcntl.fcntl(self._master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self._master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # Register an event-loop reader: called exactly when data is ready
            def _on_readable():
                fd = self._master_fd
                if fd is None:
                    return
                try:
                    data = os.read(fd, 65536)
                    if data:
                        self._queue.put_nowait(data)
                        self.last_activity = time.time()
                    else:
                        # EOF (shell exited)
                        self._set_disconnected()
                except BlockingIOError:
                    pass  # Spurious wakeup — ignore
                except OSError:
                    self._set_disconnected()

            self._loop.add_reader(self._master_fd, _on_readable)
            self.state = SessionState.CONNECTED
            self.last_activity = time.time()
            logger.info("local_terminal_connected session=%s", self.id)
        except Exception as e:
            self.state = SessionState.DISCONNECTED
            logger.error("local_terminal_connect_failed session=%s error=%s", self.id, e)
            raise

    def _set_disconnected(self):
        """Called from the reader callback when the PTY closes."""
        self.state = SessionState.DISCONNECTED
        if self._queue is not None:
            try:
                self._queue.put_nowait(None)  # EOF sentinel
            except Exception:
                pass

    def resize(self, rows, cols):
        import fcntl
        import struct
        import termios

        self.rows = rows
        self.cols = cols
        if self._master_fd is not None and self.state == SessionState.CONNECTED:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            try:
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass
            self.last_activity = time.time()

    async def read_output(self):
        """Block until data is ready (event-loop driven, no polling)."""
        if self._queue is None or self.state not in (
            SessionState.CONNECTED, SessionState.DISCONNECTED
        ):
            return None
        try:
            data = await self._queue.get()
            if data is None:
                return None  # EOF sentinel
            self.last_activity = time.time()
            return data.decode("utf-8", errors="replace")
        except Exception:
            self.state = SessionState.DISCONNECTED
            return None

    async def write_input(self, data):
        if self._master_fd is None or self.state != SessionState.CONNECTED:
            return False
        try:
            os.write(self._master_fd, data.encode("utf-8"))
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

        # Unregister reader BEFORE closing fd to avoid use-after-close
        if self._loop is not None and not self._loop.is_closed() and self._master_fd is not None:
            try:
                self._loop.remove_reader(self._master_fd)
            except Exception:
                pass

        # Send EOF sentinel so any awaiting read_output() returns
        if self._queue is not None:
            try:
                if self._loop and not self._loop.is_closed():
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
                else:
                    self._queue.put_nowait(None)
            except Exception:
                pass

        if self._master_fd is not None:
            try:
                os.close(self._master_fd)
            except Exception:
                pass
            self._master_fd = None

        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None

        logger.info("local_terminal_closed session=%s", self.id)
