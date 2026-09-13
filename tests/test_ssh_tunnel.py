"""
Tests for app/monitoring/ssh_tunnel.py — SSH tunnel manager.

All tests mock the real SSH connection. No VPS access required.
"""
import socket
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.monitoring.ssh_tunnel import SSHTunnelManager, LOCAL_BIND_HOST


@pytest.fixture
def mock_vps_config():
    with patch("app.monitoring.ssh_tunnel.Config") as mock_config:
        mock_config.VPS_HOST = "testhost"
        mock_config.VPS_SSH_USER = "testuser"
        mock_config.VPS_SSH_KEY_PATH = "/tmp/test_key"
        mock_config.VPS_SSH_PORT = 22
        mock_config.VPS_KNOWN_HOSTS_PATH = None
        yield mock_config


class TestSSHTunnelManagerLifecycle:
    """Test tunnel creation, start, and close lifecycle."""

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_start_sets_started_flag(self, mock_fwd, mock_server, mock_connect, mock_vps_config):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel.start()
        assert tunnel._started is True
        assert tunnel._closed is False
        tunnel.close()

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_close_sets_closed_flag(self, mock_fwd, mock_server, mock_connect, mock_vps_config):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel.start()
        tunnel.close()
        assert tunnel._closed is True

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_double_close_is_safe(self, mock_fwd, mock_server, mock_connect, mock_vps_config):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel.start()
        tunnel.close()
        tunnel.close()  # should not raise
        assert tunnel._closed is True

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_context_manager(self, mock_fwd, mock_server, mock_connect, mock_vps_config):
        with SSHTunnelManager(remote_port=4001) as tunnel:
            assert tunnel._started is True
        assert tunnel._closed is True

    def test_local_port_none_before_start(self):
        tunnel = SSHTunnelManager(remote_port=4001)
        assert tunnel.local_port is None

    def test_is_active_false_before_start(self):
        tunnel = SSHTunnelManager(remote_port=4001)
        assert tunnel.is_active is False

    def test_is_active_false_after_close(self):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel._closed = True
        assert tunnel.is_active is False

    def test_local_url_none_before_start(self):
        tunnel = SSHTunnelManager(remote_port=4001)
        assert tunnel.local_url is None

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_local_url_set_after_start(self, mock_fwd, mock_server, mock_connect, mock_vps_config):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel._local_port = 12345
        tunnel.start()
        assert tunnel.local_url == f"http://{LOCAL_BIND_HOST}:12345"


class TestSSHTunnelManagerConfiguration:
    """Test tunnel configuration parameters."""

    def test_default_remote_port(self):
        tunnel = SSHTunnelManager(remote_port=4001)
        assert tunnel._remote_port == 4001

    def test_default_remote_host(self):
        tunnel = SSHTunnelManager()
        assert tunnel._remote_host == "127.0.0.1"

    def test_custom_connect_timeout(self):
        tunnel = SSHTunnelManager(connect_timeout=30)
        assert tunnel._connect_timeout == 30

    def test_custom_max_lifetime(self):
        tunnel = SSHTunnelManager(max_lifetime=300)
        assert tunnel._max_lifetime == 300

    def test_default_bind_is_localhost(self):
        assert LOCAL_BIND_HOST == "127.0.0.1"


class TestSSHTunnelManagerConnection:
    """Test SSH connection handling (all mocked)."""

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_connect_ssh_uses_config(self, mock_fwd, mock_server):
        with patch("app.monitoring.ssh_tunnel.Config") as mock_config:
            mock_config.VPS_HOST = "testhost"
            mock_config.VPS_SSH_USER = "testuser"
            mock_config.VPS_SSH_KEY_PATH = "/tmp/test_key"
            mock_config.VPS_SSH_PORT = 22
            mock_config.VPS_KNOWN_HOSTS_PATH = "/tmp/test_known_hosts"

            with patch("paramiko.SSHClient") as MockClient:
                mock_instance = MagicMock()
                MockClient.return_value = mock_instance

                tunnel = SSHTunnelManager(remote_port=4001)
                tunnel.start()

                mock_instance.load_system_host_keys.assert_called_once()
                mock_instance.connect.assert_called_once()
                connect_kwargs = mock_instance.connect.call_args[1]
                assert connect_kwargs["hostname"] == "testhost"
                assert connect_kwargs["username"] == "testuser"
                assert connect_kwargs["port"] == 22

                tunnel.close()

    def test_start_raises_without_vps_config(self):
        with patch("app.monitoring.ssh_tunnel.Config") as mock_config:
            mock_config.VPS_HOST = ""
            mock_config.VPS_SSH_USER = ""

            tunnel = SSHTunnelManager(remote_port=4001)
            with pytest.raises(RuntimeError, match="VPS SSH not configured"):
                tunnel.start()

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_connect_ssh_auth_failure(self, mock_fwd, mock_server):
        import paramiko
        with patch("app.monitoring.ssh_tunnel.Config") as mock_config:
            mock_config.VPS_HOST = "testhost"
            mock_config.VPS_SSH_USER = "testuser"
            mock_config.VPS_SSH_KEY_PATH = "/tmp/test_key"
            mock_config.VPS_SSH_PORT = 22
            mock_config.VPS_KNOWN_HOSTS_PATH = None

            with patch("paramiko.SSHClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.connect.side_effect = paramiko.AuthenticationException("auth failed")
                MockClient.return_value = mock_instance

                tunnel = SSHTunnelManager(remote_port=4001)
                with pytest.raises(RuntimeError, match="authentication failed"):
                    tunnel.start()

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_connect_ssh_bad_host_key(self, mock_fwd, mock_server):
        import paramiko
        with patch("app.monitoring.ssh_tunnel.Config") as mock_config:
            mock_config.VPS_HOST = "testhost"
            mock_config.VPS_SSH_USER = "testuser"
            mock_config.VPS_SSH_KEY_PATH = "/tmp/test_key"
            mock_config.VPS_SSH_PORT = 22
            mock_config.VPS_KNOWN_HOSTS_PATH = None

            with patch("paramiko.SSHClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.connect.side_effect = paramiko.BadHostKeyException("host", None, None)
                MockClient.return_value = mock_instance

                tunnel = SSHTunnelManager(remote_port=4001)
                with pytest.raises(RuntimeError, match="Host key verification failed"):
                    tunnel.start()

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._create_local_server")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_connect_ssh_timeout(self, mock_fwd, mock_server):
        with patch("app.monitoring.ssh_tunnel.Config") as mock_config:
            mock_config.VPS_HOST = "testhost"
            mock_config.VPS_SSH_USER = "testuser"
            mock_config.VPS_SSH_KEY_PATH = "/tmp/test_key"
            mock_config.VPS_SSH_PORT = 22
            mock_config.VPS_KNOWN_HOSTS_PATH = None

            with patch("paramiko.SSHClient") as MockClient:
                mock_instance = MagicMock()
                mock_instance.connect.side_effect = socket.timeout("timed out")
                MockClient.return_value = mock_instance

                tunnel = SSHTunnelManager(remote_port=4001)
                with pytest.raises(RuntimeError, match="Couldn't establish SSH"):
                    tunnel.start()


class TestSSHTunnelManagerForwarding:
    """Test the local server and forwarding loop."""

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._start_forwarding")
    def test_create_local_server_binds_ephemeral_port(self, mock_fwd, mock_connect):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel._create_local_server()
        assert tunnel._local_port is not None
        assert tunnel._local_port > 0
        assert tunnel._local_server is not None
        tunnel._local_server.close()

    @patch("app.monitoring.ssh_tunnel.SSHTunnelManager._connect_ssh")
    def test_start_creates_server_and_starts_forwarding(self, mock_connect, mock_vps_config):
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel.start()
        assert tunnel._local_port is not None
        assert tunnel._forward_thread is not None
        assert tunnel._forward_thread.daemon is True
        tunnel.close()

    def test_factory_function(self):
        from app.monitoring.ssh_tunnel import create_tunnel
        tunnel = create_tunnel(remote_port=4001)
        assert isinstance(tunnel, SSHTunnelManager)
        assert tunnel._remote_port == 4001


class TestSSHTunnelManagerLifetime:
    """Test tunnel lifetime and expiry."""

    def test_is_active_false_when_expired(self):
        tunnel = SSHTunnelManager(remote_port=4001, max_lifetime=1)
        tunnel._started = True
        tunnel._closed = False
        tunnel._start_time = time.time() - 10  # started 10 seconds ago
        assert tunnel.is_active is False

    def test_is_active_true_when_within_lifetime(self):
        tunnel = SSHTunnelManager(remote_port=4001, max_lifetime=300)
        tunnel._started = True
        tunnel._closed = False
        tunnel._start_time = time.time()
        assert tunnel.is_active is True


class TestSSHTunnelManagerRelay:
    """Test the bidirectional relay mechanism."""

    def test_relay_closes_when_src_exhausted(self):
        """Relay should complete when source socket is closed."""
        # Create a pair of connected sockets
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((LOCAL_BIND_HOST, 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((LOCAL_BIND_HOST, port))
        conn, _ = server.accept()

        # Create a mock channel that sends data back when receiving
        mock_channel = MagicMock()
        mock_channel.settimeout = MagicMock()
        recv_results = [b"hello", b""]
        mock_channel.recv.side_effect = recv_results

        # Track sendall calls
        sent_data = []
        def track_sendall(data):
            sent_data.append(data)
        mock_channel.sendall.side_effect = track_sendall

        # Run relay directly (not in thread) for deterministic testing
        SSHTunnelManager._relay(conn, mock_channel)

        # Channel.close should have been called
        mock_channel.close.assert_called()
        client.close()
        server.close()

    def test_relay_handles_channel_error(self):
        """Relay should handle channel errors gracefully."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((LOCAL_BIND_HOST, 0))
        server.listen(1)
        port = server.getsockname()[1]

        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.connect((LOCAL_BIND_HOST, port))
        conn, _ = server.accept()

        mock_channel = MagicMock()
        mock_channel.settimeout = MagicMock()
        mock_channel.recv.side_effect = Exception("channel closed")

        # Should not raise
        SSHTunnelManager._relay(conn, mock_channel)
        mock_channel.close.assert_called()
        client.close()
        server.close()
