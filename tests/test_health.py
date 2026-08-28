"""
Tests for app/tools/health.py. Verifies that one failed check doesn't stop
the rest, that failures are UNKNOWN (never HEALTHY), and that the overall
status reflects the worst individual result.
"""
from unittest.mock import patch

from app.tools.health import check_infrastructure_health


def _patch_all_healthy():
    return [
        patch("app.tools.health.local_tools.get_local_cpu_usage", return_value={"success": True, "load_average_1min": 0.5, "cpu_count": 4}),
        patch("app.tools.health.local_tools.get_local_memory_usage", return_value={"success": True, "memory_percent": 40}),
        patch("app.tools.health.local_tools.get_local_disk_usage", return_value={"success": True, "disk_percent": 50}),
        patch("app.tools.health.local_tools.check_local_network", return_value={"success": True, "reachable": True}),
        patch("app.tools.health.local_tools.get_local_docker_status", return_value={"success": True, "available": True}),
        patch("app.tools.health.vps_server.get_cpu_usage", return_value={"success": True, "approx_cpu_percent": 20}),
        patch("app.tools.health.vps_server.get_memory_usage", return_value={"success": True, "memory_percent": 30}),
        patch("app.tools.health.vps_server.get_disk_usage", return_value={"success": True, "disk_percent": 40}),
        patch("app.tools.health.vps_server.get_uptime", return_value={"success": True, "raw": "up 5 days"}),
        patch("app.tools.health.vps_network.check_network_connectivity", return_value={"success": True, "reachable": True}),
        patch("app.tools.health.vps_docker.docker_health_status", return_value={"success": True, "containers": [], "summary": {"containers_with_problems": []}}),
        patch("app.tools.health.aws_tools.list_ec2_instances", return_value={"success": False, "error": "AWS credentials aren't configured."}),
    ]


def test_health_check_all_healthy_gives_healthy_overall():
    patches = _patch_all_healthy()
    for p in patches:
        p.start()
    try:
        result = check_infrastructure_health()
    finally:
        for p in patches:
            p.stop()

    assert result["success"] is True
    report = result["report"]
    assert report["local"]["cpu"]["status"] == "HEALTHY"
    assert report["vps"]["network"]["status"] == "HEALTHY"
    assert report["overall_status"] == "HEALTHY"
    # AWS not configured shouldn't drag overall status down
    assert report["aws"]["ec2"]["status"] == "NOT_CONFIGURED"


def test_health_check_one_failure_does_not_block_others():
    with patch("app.tools.health.local_tools.get_local_cpu_usage", side_effect=RuntimeError("boom")), \
         patch("app.tools.health.local_tools.get_local_memory_usage", return_value={"success": True, "memory_percent": 40}), \
         patch("app.tools.health.local_tools.get_local_disk_usage", return_value={"success": True, "disk_percent": 40}), \
         patch("app.tools.health.local_tools.check_local_network", return_value={"success": True, "reachable": True}), \
         patch("app.tools.health.local_tools.get_local_docker_status", return_value={"success": True, "available": True}), \
         patch("app.tools.health.vps_server.get_cpu_usage", return_value={"success": True, "approx_cpu_percent": 20}), \
         patch("app.tools.health.vps_server.get_memory_usage", return_value={"success": True, "memory_percent": 30}), \
         patch("app.tools.health.vps_server.get_disk_usage", return_value={"success": True, "disk_percent": 40}), \
         patch("app.tools.health.vps_server.get_uptime", return_value={"success": True, "raw": "up"}), \
         patch("app.tools.health.vps_network.check_network_connectivity", return_value={"success": True, "reachable": True}), \
         patch("app.tools.health.vps_docker.docker_health_status", return_value={"success": True, "containers": [], "summary": {"containers_with_problems": []}}), \
         patch("app.tools.health.aws_tools.list_ec2_instances", return_value={"success": False, "error": "not configured"}):
        result = check_infrastructure_health()

    assert result["success"] is True
    # the broken check is UNKNOWN, not missing and not silently HEALTHY
    assert result["report"]["local"]["cpu"]["status"] == "UNKNOWN"
    # everything else still ran despite that failure
    assert result["report"]["local"]["memory"]["status"] == "HEALTHY"
    assert result["report"]["vps"]["network"]["status"] == "HEALTHY"


def test_health_check_critical_disk_drives_overall_critical():
    patches = _patch_all_healthy()
    for p in patches:
        p.start()
    try:
        with patch("app.tools.health.vps_server.get_disk_usage", return_value={"success": True, "disk_percent": 95}):
            result = check_infrastructure_health()
    finally:
        for p in patches:
            p.stop()

    assert result["report"]["vps"]["disk"]["status"] == "CRITICAL"
    assert result["report"]["overall_status"] == "CRITICAL"


def test_health_check_never_reports_healthy_for_failed_check():
    with patch("app.tools.health.local_tools.get_local_cpu_usage", return_value={"success": False, "error": "boom"}), \
         patch("app.tools.health.local_tools.get_local_memory_usage", return_value={"success": True, "memory_percent": 40}), \
         patch("app.tools.health.local_tools.get_local_disk_usage", return_value={"success": True, "disk_percent": 40}), \
         patch("app.tools.health.local_tools.check_local_network", return_value={"success": True, "reachable": True}), \
         patch("app.tools.health.local_tools.get_local_docker_status", return_value={"success": True, "available": True}), \
         patch("app.tools.health.vps_server.get_cpu_usage", return_value={"success": True, "approx_cpu_percent": 20}), \
         patch("app.tools.health.vps_server.get_memory_usage", return_value={"success": True, "memory_percent": 30}), \
         patch("app.tools.health.vps_server.get_disk_usage", return_value={"success": True, "disk_percent": 40}), \
         patch("app.tools.health.vps_server.get_uptime", return_value={"success": True, "raw": "up"}), \
         patch("app.tools.health.vps_network.check_network_connectivity", return_value={"success": True, "reachable": True}), \
         patch("app.tools.health.vps_docker.docker_health_status", return_value={"success": True, "containers": [], "summary": {"containers_with_problems": []}}), \
         patch("app.tools.health.aws_tools.list_ec2_instances", return_value={"success": False, "error": "not configured"}):
        result = check_infrastructure_health()

    assert result["report"]["local"]["cpu"]["status"] == "UNKNOWN"
    assert result["report"]["local"]["cpu"]["status"] != "HEALTHY"


def test_health_check_docker_problems_flagged_as_warning():
    patches = _patch_all_healthy()
    for p in patches:
        p.start()
    try:
        with patch("app.tools.health.vps_docker.docker_health_status", return_value={
            "success": True,
            "containers": [{"name": "backend", "state": "exited", "health": "no_healthcheck"}],
            "summary": {"containers_with_problems": ["backend"]},
        }):
            result = check_infrastructure_health()
    finally:
        for p in patches:
            p.stop()

    assert result["report"]["vps"]["docker"]["status"] == "WARNING"