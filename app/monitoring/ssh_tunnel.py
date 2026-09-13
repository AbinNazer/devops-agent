"""
Scoped SSH tunnel manager for Phase 6 Prometheus integration.

Creates local port forwarding to VPS-internal services (like Prometheus)
through the existing SSH connection, without exposing them publicly.

Architecture:
  JARVIS → SSH tunnel → VPS localhost:4001 → Prometheus

Lifecycle:
  1. Connect to VPS using existing SSH key (same as SSHSession)
  2. Open a direct-tcpip channel to VPS localhost:PORT
  3. Listen on a local port and forward traffic through the channel
  4. Clean up everything on close

Security:
  - Reuses existing VPS SSH key authentication
  - Never exposes Prometheus to the internet
  - Tunnel is scoped: created per-use, cleaned up automatically
  - Thread-safe lifecycle management
  - Bounded tunnel lifetime
"""
import logging
import os
import socket
import threading
import time
from typing import Optional

import paramiko

from app.config import Config

logger = logging.getLogger("ssh_tunnel")
audit_logger = logging.getLogger("ssh_audit")


# Tunnel defaults
DEFAULT_CONNECT_TIMEOUT = 10     # seconds to establish SSH connection
DEFAULT_TUNNEL_LIFETIME = 600    # 10 minutes max tunnel lifetime
DEFAULT_CHANNEL_TIMEOUT = 30     # seconds per channel operation
LOCAL_BIND_HOST = "127.0.0.1"    # only bind to localhost — never expose publicly


class SSHTunnelManager:
    """
    Manages a scoped SSH tunnel from localhost to a VPS-internal port.

    Uses the existing VPS SSH credentials (key-based auth) to create
    a direct-tcpip channel through the SSH transport. A local TCP
    server forwards traffic through the SSH channel.

    Usage as context manager:

        with SSHTunnelManager(remote_port=4001) as tunnel:
            # tunnel.local_port is the forwarded local port
            response = requests.get(f"http://127.0.0.1:{tunnel.local_port}/api/v1/query")

    Or manual lifecycle:

        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel.start()
        try:
            # use tunnel.local_port
            pass
        finally:
            tunnel.close()

    The tunnel is automatically closed when the maximum lifetime is reached.
    """

    def __init__(
        self,
        remote_host: str = "127.0.0.1",
        remote_port: int = 4001,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
        max_lifetime: int = DEFAULT_TUNNEL_LIFETIME,
    ):
        """
        Args:
            remote_host: The host to connect to on the VPS side (usually 127.0.0.1
                for services only listening on localhost).
            remote_port: The port on the VPS to forward to (e.g. 4001 for Prometheus).
            connect_timeout: Seconds to wait for SSH connection.
            max_lifetime: Maximum tunnel lifetime in seconds.
        """
        self._remote_host = remote_host
        self._remote_port = remote_port
        self._connect_timeout = connect_timeout
        self._max_lifetime = max_lifetime

        self._ssh_client: Optional[paramiko.SSHClient] = None
        self._local_server: Optional[socket.socket] = None
        self._local_port: Optional[int] = None
        self._forward_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._started = False
        self._closed = False
        self._start_time: Optional[float] = None
        self._tunnel_lifetime_seconds = 0.0

    @property
    def local_port(self) -> Optional[int]:
        """The local port that forwards to the VPS service. None if not started."""
        return self._local_port

    @property
    def is_active(self) -> bool:
        """Whether the tunnel is currently active and usable."""
        with self._lock:
            if not self._started or self._closed:
                return False
            if self._start_time is None:
                return False
            return (time.time() - self._start_time) < self._max_lifetime

    @property
    def local_url(self) -> Optional[str]:
        """The full local URL to reach the forwarded service."""
        if self._local_port:
            return f"http://{LOCAL_BIND_HOST}:{self._local_port}"
        return None

    def __enter__(self) -> "SSHTunnelManager":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def start(self) -> None:
        """Start the SSH tunnel. Raises on failure."""
        with self._lock:
            if self._started and not self._closed:
                return
            self._started = True
            self._start_time = time.time()

        # Validate VPS config
        if not Config.VPS_HOST or not Config.VPS_SSH_USER:
            raise RuntimeError(
                "VPS SSH not configured. Set VPS_HOST and VPS_SSH_USER in .env."
            )

        # Step 1: Establish SSH connection
        self._connect_ssh()

        # Step 2: Create local TCP server
        self._create_local_server()

        # Step 3: Start forwarding thread
        self._start_forwarding()

        self._tunnel_lifetime_seconds = 0.0
        audit_logger.info(
            "TUNNEL_OPENED remote=%s:%d local=%s:%d lifetime=%ds",
            self._remote_host, self._remote_port,
            LOCAL_BIND_HOST, self._local_port,
            self._max_lifetime,
        )
        logger.info(
            "ssh_tunnel_started local=%s:%d remote=%s:%d",
            LOCAL_BIND_HOST, self._local_port,
            self._remote_host, self._remote_port,
        )

    def _connect_ssh(self) -> None:
        """Establish SSH connection to VPS. Must be called with _lock not held."""
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        known_hosts = Config.VPS_KNOWN_HOSTS_PATH
        if known_hosts and os.path.exists(known_hosts):
            client.load_host_keys(known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())

        try:
            client.connect(
                hostname=Config.VPS_HOST,
                username=Config.VPS_SSH_USER,
                key_filename=Config.VPS_SSH_KEY_PATH,
                port=Config.VPS_SSH_PORT,
                timeout=self._connect_timeout,
            )
        except paramiko.AuthenticationException:
            raise RuntimeError(
                "SSH authentication failed for tunnel. Check VPS_SSH_KEY_PATH."
            )
        except paramiko.BadHostKeyException:
            raise RuntimeError(
                "Host key verification failed for tunnel. "
                "VPS key doesn't match known_hosts."
            )
        except (socket.timeout, ConnectionRefusedError, paramiko.SSHException) as e:
            raise RuntimeError(f"Couldn't establish SSH connection for tunnel: {e}")

        with self._lock:
            self._ssh_client = client

    def _create_local_server(self) -> None:
        """Create a local TCP server socket that listens for forwarding connections."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.settimeout(5.0)

        try:
            server.bind((LOCAL_BIND_HOST, 0))  # OS picks a free port
            server.listen(5)
            self._local_port = server.getsockname()[1]
        except Exception as e:
            server.close()
            raise RuntimeError(f"Failed to create local tunnel server: {e}")

        with self._lock:
            self._local_server = server

    def _start_forwarding(self) -> None:
        """Start the forwarding thread that accepts connections and tunnels them."""
        self._forward_thread = threading.Thread(
            target=self._forward_loop,
            daemon=True,
            name="ssh-tunnel-forward",
        )
        self._forward_thread.start()

    def _forward_loop(self) -> None:
        """Accept local connections and forward them through the SSH tunnel."""
        while True:
            # Check lifetime
            if not self.is_active:
                logger.info("ssh_tunnel_lifetime_exceeded — closing")
                break

            # Accept a local connection
            try:
                local_conn, local_addr = self._local_server.accept()
            except socket.timeout:
                continue
            except OSError:
                # Server socket closed
                break

            # Forward it in a thread
            t = threading.Thread(
                target=self._forward_connection,
                args=(local_conn,),
                daemon=True,
            )
            t.start()

    def _forward_connection(self, local_conn: socket.socket) -> None:
        """Forward a single local connection through the SSH tunnel."""
        transport = None
        channel = None
        try:
            # Get SSH transport
            with self._lock:
                client = self._ssh_client
            if client is None:
                local_conn.close()
                return
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                local_conn.close()
                return

            # Open direct-tcpip channel to VPS service
            channel = transport.open_channel(
                "direct-tcpip",
                (self._remote_host, self._remote_port),
                (LOCAL_BIND_HOST, 0),
                timeout=DEFAULT_CHANNEL_TIMEOUT,
            )
            if channel is None:
                local_conn.close()
                return

            # Bidirectional forwarding
            self._relay(local_conn, channel)

        except Exception as e:
            logger.debug("forward_connection_error: %s", e)
        finally:
            if channel:
                try:
                    channel.close()
                except Exception:
                    pass
            try:
                local_conn.close()
            except Exception:
                pass

    @staticmethod
    def _relay(local_sock: socket.socket, channel: paramiko.Channel) -> None:
        """Bidirectional relay between local socket and SSH channel."""
        channel.settimeout(DEFAULT_CHANNEL_TIMEOUT)
        local_sock.settimeout(DEFAULT_CHANNEL_TIMEOUT)

        def _pipe(src, dst, name: str) -> None:
            try:
                while True:
                    data = src.recv(8192)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

        t1 = threading.Thread(target=_pipe, args=(local_sock, channel, "local->ssh"), daemon=True)
        t2 = threading.Thread(target=_pipe, args=(channel, local_sock, "ssh->local"), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=DEFAULT_CHANNEL_TIMEOUT + 5)
        t2.join(timeout=DEFAULT_CHANNEL_TIMEOUT + 5)

    def close(self) -> None:
        """Close the tunnel and clean up all resources."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

            # Close local server
            if self._local_server:
                try:
                    self._local_server.close()
                except Exception:
                    pass
                self._local_server = None

            # Close SSH connection
            if self._ssh_client:
                try:
                    self._ssh_client.close()
                except Exception:
                    pass
                self._ssh_client = None

        if self._start_time:
            self._tunnel_lifetime_seconds = round(time.time() - self._start_time, 2)

        audit_logger.info(
            "TUNNEL_CLOSED local=%s:%d lifetime=%ss",
            LOCAL_BIND_HOST, self._local_port,
            self._tunnel_lifetime_seconds,
        )
        logger.info(
            "ssh_tunnel_closed lifetime=%ss", self._tunnel_lifetime_seconds,
        )


def create_tunnel(
    remote_port: int = 4001,
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    max_lifetime: int = DEFAULT_TUNNEL_LIFETIME,
) -> SSHTunnelManager:
    """Create a new SSH tunnel manager. Caller must start/close it."""
    return SSHTunnelManager(
        remote_port=remote_port,
        connect_timeout=connect_timeout,
        max_lifetime=max_lifetime,
    )
