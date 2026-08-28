"""
Tool tests for Phase 2. Every tool now goes over SSH, so these mock
app.ssh_client.run_whitelisted_command directly — no real VPS is
contacted. This tests each tool's own logic: command building, output
parsing, and error handling — independent of ssh_client's own tests.
"""
from unittest.mock import patch

from app.tools.server import (
    get_cpu_usage, get_memory_usage, get_disk_usage, get_uptime, get_service_status,
)
from app.tools.docker import docker_status, docker_stats, docker_logs, docker_inspect, docker_health_status
from app.tools.network import check_network_connectivity
from app.tools.process import list_top_processes
from app.tool_registry import execute_tool, coerce_arguments


def _ok(stdout="", stderr="", exit_code=0):
    return {"success": True, "command": "mock", "stdout": stdout, "stderr": stderr, "exit_code": exit_code}


def _fail(error="SSH failed"):
    return {"success": False, "error": error}


# --- server.py ---

def test_get_uptime_success():
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok(" 10:00:01 up 5 days, load average: 0.10, 0.20, 0.30")):
        result = get_uptime()
    assert result["success"] is True
    assert "up 5 days" in result["raw"]


def test_get_uptime_ssh_failure_propagates():
    with patch("app.tools.server.run_whitelisted_command", return_value=_fail("Couldn't reach the VPS over SSH.")):
        result = get_uptime()
    assert result["success"] is False
    assert "SSH" in result["error"]


def test_get_memory_usage_parses_free_output():
    free_output = "              total        used        free\nMem:           7.6G        4.1G        1.2G\nSwap:          2.0G        0.0G        2.0G"
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok(free_output)):
        result = get_memory_usage()
    assert result["success"] is True
    assert result["memory_percent"] == round(4.1 / 7.6 * 100, 1)


def test_get_memory_usage_handles_unparseable_output():
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok("garbage output with no Mem line")):
        result = get_memory_usage()
    assert result["success"] is False


def test_get_disk_usage_parses_df_output():
    df_output = "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1        50G   36G   14G  72% /"
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok(df_output)):
        result = get_disk_usage()
    assert result["success"] is True
    assert result["disk_percent"] == 72


def test_get_disk_usage_handles_missing_root_mount():
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok("Filesystem Size Used Avail Use% Mounted on")):
        result = get_disk_usage()
    assert result["success"] is False


def test_get_cpu_usage_parses_load_average():
    # nproc will return successfully with 1 core by default if we don't mock it separately,
    # or if we just mock run_whitelisted_command generally, it returns the same for both.
    # To make it clean, let's just make the mock return something that works for both or
    # mock them specifically. Wait, the mock returns "load average: ..."
    # So `nproc` will parse "load average:" as int and fail, falling back to cores=1.
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok("load average: 0.50, 0.30, 0.20")):
        result = get_cpu_usage()
    assert result["success"] is True
    assert result["load_average_1min"] == 0.5
    assert result["load_per_core_1min"] == 0.5
    assert "cpu_percent" not in result
    assert "not CPU utilization" in result["note"]


def test_get_cpu_usage_handles_unparseable_output():
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok("no load info here")):
        result = get_cpu_usage()
    assert result["success"] is False


def test_get_service_status_success():
    with patch("app.tools.server.run_whitelisted_command", return_value=_ok("active (running)")):
        result = get_service_status("nginx")
    assert result["success"] is True
    assert "active" in result["status_output"]


def test_get_service_status_rejects_unsafe_name():
    result = get_service_status("nginx; reboot")
    assert result["success"] is False


# --- docker.py ---

def test_docker_status_returns_raw_output():
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok("CONTAINER ID   NAMES\nabc123   backend")):
        result = docker_status()
    assert result["success"] is True
    assert "backend" in result["raw"]


def test_docker_stats_returns_raw_output():
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok("CONTAINER   CPU %   MEM %\nbackend   1.2%   3.4%")):
        result = docker_stats()
    assert result["success"] is True
    assert "backend" in result["raw"]


def test_docker_logs_success():
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok("line1\nline2", exit_code=0)):
        result = docker_logs("backend", 50)
    assert result["success"] is True
    assert result["logs"] == "line1\nline2"


def test_docker_logs_rejects_unsafe_container_name():
    result = docker_logs("backend; rm -rf /", 50)
    assert result["success"] is False


def test_docker_logs_nonzero_exit_reported_as_failure():
    with patch("app.tools.docker.run_whitelisted_command",
               return_value=_ok(stdout="", stderr="Error: No such container: ghost", exit_code=1)):
        result = docker_logs("ghost", 50)
    assert result["success"] is False
    assert "No such container" in result["error"]


def test_docker_inspect_success():
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok('[{"Name": "/backend"}]', exit_code=0)):
        result = docker_inspect("backend")
    assert result["success"] is True
    assert "backend" in result["details"]


def test_docker_inspect_rejects_unsafe_name():
    result = docker_inspect("backend`whoami`")
    assert result["success"] is False


def test_docker_health_status_running_no_healthcheck_is_not_unhealthy():
    """The core bug: a running container with no HEALTHCHECK must count as
    running=1, no_healthcheck=1 — NOT running=0 or unhealthy=1."""
    ps_output = "follow-up-ai|Up 9 minutes|running"
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok(ps_output)):
        result = docker_health_status()
    assert result["success"] is True
    c = result["containers"][0]
    assert c["state"] == "running"
    assert c["health"] == "no_healthcheck"
    summary = result["summary"]
    assert summary["running"] == 1
    assert summary["no_healthcheck"] == 1
    assert summary["unhealthy"] == 0
    assert summary["containers_with_problems"] == []


def test_docker_health_status_running_and_healthy():
    ps_output = "backend|Up 2 hours (healthy)|running"
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok(ps_output)):
        result = docker_health_status()
    c = result["containers"][0]
    assert c["state"] == "running"
    assert c["health"] == "healthy"
    assert result["summary"]["running"] == 1
    assert result["summary"]["healthy"] == 1


def test_docker_health_status_running_and_unhealthy():
    ps_output = "backend|Up 10 minutes (unhealthy)|running"
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok(ps_output)):
        result = docker_health_status()
    c = result["containers"][0]
    assert c["state"] == "running"
    assert c["health"] == "unhealthy"
    assert result["summary"]["running"] == 1
    assert result["summary"]["unhealthy"] == 1
    assert "backend" in result["summary"]["containers_with_problems"]


def test_docker_health_status_exited():
    ps_output = "old-job|Exited (0) 2 minutes ago|exited"
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok(ps_output)):
        result = docker_health_status()
    c = result["containers"][0]
    assert c["state"] == "exited"
    assert result["summary"]["running"] == 0
    assert result["summary"]["stopped"] == 1
    assert "old-job" in result["summary"]["containers_with_problems"]


def test_docker_health_status_health_starting():
    ps_output = "backend|Up 5 seconds (health: starting)|running"
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok(ps_output)):
        result = docker_health_status()
    c = result["containers"][0]
    assert c["health"] == "starting"


def test_docker_health_status_mixed_realistic_scenario():
    """Case E: a realistic mixture — verifies summary counts across all categories at once."""
    ps_output = "\n".join([
        "web|Up 1 hour (healthy)|running",
        "worker|Up 30 minutes (unhealthy)|running",
        "cache|Up 2 hours|running",           # running, no healthcheck
        "old-migration|Exited (0) 1 day ago|exited",
        "flaky-service|Restarting (1) 5 seconds ago|restarting",
    ])
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok(ps_output)):
        result = docker_health_status()

    summary = result["summary"]
    assert summary["total"] == 5
    assert summary["running"] == 3          # web, worker, cache
    assert summary["stopped"] == 1          # old-migration
    assert summary["restarting"] == 1       # flaky-service
    assert summary["healthy"] == 1          # web
    assert summary["unhealthy"] == 1        # worker
    assert summary["no_healthcheck"] == 3   # cache, old-migration, flaky-service (no health suffix)
    problems = set(summary["containers_with_problems"])
    assert problems == {"worker", "old-migration", "flaky-service"}
    assert "web" not in problems
    assert "cache" not in problems  # no healthcheck is NOT a problem


def test_docker_health_status_never_misparses_when_command_returns_no_output():
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok("")):
        result = docker_health_status()
    assert result["success"] is True
    assert result["containers"] == []
    assert result["summary"]["total"] == 0


def test_docker_health_status_uses_pipe_delimited_whitelisted_command():
    """Regression test for the actual root-cause bug: the whitelisted command
    must use a shell-safe delimiter (quoted pipe), not an unquoted \\t that
    the remote shell strips down to a bare 't' character."""
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok("")) as mock_run:
        docker_health_status()
    called_command = mock_run.call_args[0][0]
    assert "\\t" not in called_command
    assert called_command == "docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'"
    from app.ssh_whitelist import is_command_allowed
    assert is_command_allowed(called_command) is True


# --- network.py ---

def test_check_network_connectivity_reachable():
    with patch("app.tools.network.run_whitelisted_command", return_value=_ok("up")):
        result = check_network_connectivity()
    assert result["success"] is True
    assert result["reachable"] is True
    assert result["latency_ms"] is not None


def test_check_network_connectivity_unreachable():
    with patch("app.tools.network.run_whitelisted_command", return_value=_fail("Couldn't reach the VPS over SSH.")):
        result = check_network_connectivity()
    assert result["success"] is True  # the CHECK succeeded even though the VPS didn't respond
    assert result["reachable"] is False


# --- process.py (now real via ps aux --sort=-%cpu) ---

def test_list_top_processes_success():
    ps_output = "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\nroot 1 12.0 3.0 1000 500 ? Ss 00:00 0:01 nginx\nroot 2 5.0 1.0 800 400 ? S 00:00 0:00 redis"
    with patch("app.tools.process.run_whitelisted_command", return_value=_ok(ps_output)):
        result = list_top_processes(2)
    assert result["success"] is True
    assert len(result["processes"]) == 2
    assert result["processes"][0]["command"] == "nginx"


def test_list_top_processes_invalid_limit():
    result = list_top_processes(0)
    assert result["success"] is False


def test_list_top_processes_limit_too_high():
    result = list_top_processes(999)
    assert result["success"] is False


def test_list_top_processes_ssh_failure_propagates():
    with patch("app.tools.process.run_whitelisted_command", return_value=_fail("VPS unreachable")):
        result = list_top_processes()
    assert result["success"] is False
    assert "unreachable" in result["error"].lower()


# --- tool_registry.execute_tool / coerce_arguments integration ---

def test_execute_tool_dispatches_and_coerces_docker_logs():
    with patch("app.tools.docker.run_whitelisted_command", return_value=_ok("log line", exit_code=0)):
        result = execute_tool("docker_logs", {"container_name": "backend", "lines": "25"})
    assert result["success"] is True


def test_execute_tool_unknown_tool_name():
    result = execute_tool("delete_everything", {})
    assert result["success"] is False
    assert "Unknown tool" in result["error"]


def test_execute_tool_never_raises_on_internal_error(monkeypatch):
    import app.tool_registry as registry

    def broken(args):
        raise RuntimeError("simulated failure")

    monkeypatch.setitem(registry._DISPATCH, "get_cpu_usage", broken)
    result = registry.execute_tool("get_cpu_usage", {})
    assert result["success"] is False
    assert "simulated failure" in result["error"]


def test_coerce_arguments_string_to_integer_for_lines():
    coerced = coerce_arguments("docker_logs", {"container_name": "backend", "lines": "100"})
    assert coerced["lines"] == 100
    assert isinstance(coerced["lines"], int)