"""
Tests for app/tools/local.py. Confirms local checks never go through
ssh_client (they shouldn't import it at all), and that subprocess-based
checks handle missing binaries / failures cleanly.
"""
from unittest.mock import patch, MagicMock
import subprocess

from app.tools import local


def test_local_module_never_imports_ssh_client():
    import app.tools.local as mod
    assert "ssh_client" not in dir(mod) and "run_whitelisted_command" not in dir(mod)


def test_get_local_cpu_usage_shape():
    result = local.get_local_cpu_usage()
    # real call on whatever machine runs the tests — just check the shape
    assert result["success"] is True
    assert "load_average_1min" in result
    assert result["cpu_count"] >= 1


def test_get_local_memory_usage_handles_missing_proc(monkeypatch):
    def fake_open(*a, **kw):
        raise FileNotFoundError()
    monkeypatch.setattr("builtins.open", fake_open)
    result = local.get_local_memory_usage()
    assert result["success"] is False


def test_get_local_disk_usage_real_path():
    result = local.get_local_disk_usage("/")
    assert result["success"] is True
    assert 0 <= result["disk_percent"] <= 100


def test_get_local_disk_usage_bad_path():
    result = local.get_local_disk_usage("/this/path/does/not/exist/at/all")
    assert result["success"] is False


def test_get_local_os_info_shape():
    result = local.get_local_os_info()
    assert result["success"] is True
    assert "system" in result


def test_check_local_network_success():
    with patch("app.tools.local.socket.create_connection") as mock_conn:
        mock_conn.return_value.__enter__ = MagicMock()
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        result = local.check_local_network()
    assert result["success"] is True
    assert result["reachable"] is True


def test_check_local_network_failure():
    with patch("app.tools.local.socket.create_connection", side_effect=OSError("no route")):
        result = local.check_local_network()
    assert result["success"] is True
    assert result["reachable"] is False


def test_list_local_top_processes_invalid_limit():
    result = local.list_local_top_processes(0)
    assert result["success"] is False


def test_list_local_top_processes_parses_output():
    fake_proc = MagicMock()
    fake_proc.stdout = "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\nroot 1 20.0 5.0 100 50 ? S 0:00 0:01 nginx"
    with patch("app.tools.local.subprocess.run", return_value=fake_proc):
        result = local.list_local_top_processes(1)
    assert result["success"] is True
    assert result["processes"][0]["command"] == "nginx"


def test_list_local_top_processes_ps_not_found():
    with patch("app.tools.local.subprocess.run", side_effect=FileNotFoundError()):
        result = local.list_local_top_processes()
    assert result["success"] is False


def test_get_local_docker_status_not_installed():
    with patch("app.tools.local.subprocess.run", side_effect=FileNotFoundError()):
        result = local.get_local_docker_status()
    assert result["success"] is True
    assert result["available"] is False


def test_get_local_docker_status_daemon_not_running():
    fake_proc = MagicMock(returncode=1, stderr="Cannot connect to the Docker daemon", stdout="")
    with patch("app.tools.local.subprocess.run", return_value=fake_proc):
        result = local.get_local_docker_status()
    assert result["success"] is True
    assert result["available"] is False


def test_get_local_docker_status_running():
    fake_proc = MagicMock(returncode=0, stdout="CONTAINER ID   NAMES\nabc123   myapp", stderr="")
    with patch("app.tools.local.subprocess.run", return_value=fake_proc):
        result = local.get_local_docker_status()
    assert result["success"] is True
    assert result["available"] is True
