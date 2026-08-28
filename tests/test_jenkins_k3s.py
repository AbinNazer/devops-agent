"""Tests for app/tools/jenkins.py and app/tools/k3s.py."""
from unittest.mock import patch

from app.tools.jenkins import get_jenkins_status, get_jenkins_stats
from app.tools.k3s import get_k3s_nodes, get_k3s_pods, get_k3s_cluster_status


def _ok(stdout="", stderr="", exit_code=0):
    return {"success": True, "command": "mock", "stdout": stdout, "stderr": stderr, "exit_code": exit_code}


def _fail(error="SSH failed"):
    return {"success": False, "error": error}


# --- jenkins.py ---

def test_get_jenkins_status_found_and_healthy():
    ps_output = "jenkins\tUp 3 hours (healthy)\trunning\nother\tUp 1 hour\trunning"
    with patch("app.tools.jenkins.docker_health_status") as mock_health:
        mock_health.return_value = {
            "success": True,
            "containers": [
                {"name": "jenkins", "state": "running", "health": "healthy", "status_text": "Up 3 hours (healthy)"},
                {"name": "other", "state": "running", "health": "no_healthcheck", "status_text": "Up 1 hour"},
            ],
        }
        result = get_jenkins_status()
    assert result["success"] is True
    assert result["found"] is True
    assert result["container"]["health"] == "healthy"


def test_get_jenkins_status_not_found():
    with patch("app.tools.jenkins.docker_health_status") as mock_health:
        mock_health.return_value = {"success": True, "containers": [{"name": "other", "state": "running", "health": "no_healthcheck", "status_text": "Up"}]}
        result = get_jenkins_status()
    assert result["success"] is True
    assert result["found"] is False


def test_get_jenkins_status_propagates_ssh_failure():
    with patch("app.tools.jenkins.docker_health_status", return_value=_fail("VPS unreachable")):
        result = get_jenkins_status()
    assert result["success"] is False


def test_get_jenkins_stats_found():
    with patch("app.tools.jenkins.docker_stats") as mock_stats:
        mock_stats.return_value = {"success": True, "raw": "CONTAINER   CPU %   MEM %\njenkins   5.0%   200MiB"}
        result = get_jenkins_stats()
    assert result["success"] is True
    assert result["found"] is True
    assert "jenkins" in result["raw"]


def test_get_jenkins_stats_not_found():
    with patch("app.tools.jenkins.docker_stats") as mock_stats:
        mock_stats.return_value = {"success": True, "raw": "CONTAINER   CPU %   MEM %\nother   5.0%   200MiB"}
        result = get_jenkins_stats()
    assert result["success"] is True
    assert result["found"] is False


# --- k3s.py ---

def test_get_k3s_nodes_success():
    with patch("app.tools.k3s.run_whitelisted_command", return_value=_ok("NAME   STATUS\nnode1   Ready", exit_code=0)):
        result = get_k3s_nodes()
    assert result["success"] is True
    assert "Ready" in result["raw"]


def test_get_k3s_pods_kubectl_not_found():
    with patch("app.tools.k3s.run_whitelisted_command", return_value=_ok("", "bash: kubectl: command not found", exit_code=127)):
        result = get_k3s_pods()
    assert result["success"] is False
    assert "isn't available" in result["error"].lower()


def test_get_k3s_cluster_status_ssh_failure_propagates():
    with patch("app.tools.k3s.run_whitelisted_command", return_value=_fail("VPS unreachable")):
        result = get_k3s_cluster_status()
    assert result["success"] is False


def test_get_k3s_pods_nonzero_exit_other_error():
    with patch("app.tools.k3s.run_whitelisted_command", return_value=_ok("", "connection refused", exit_code=1)):
        result = get_k3s_pods()
    assert result["success"] is False
    assert "connection refused" in result["error"]