"""
Unified infrastructure health check. This exists specifically to fix "check
everything and tell me if anything is wrong" bouncing between many
individual tool calls until the agent hits MAX_TOOL_ITERATIONS. Instead,
this ONE function internally calls each relevant check, catches failures
per-check so one broken component doesn't stop the rest, and returns a
single structured summary the agent can answer from in one tool call.

Status levels: HEALTHY, WARNING, CRITICAL, UNKNOWN. A check that failed to
run is UNKNOWN, never HEALTHY — the agent must never claim something is
fine when it couldn't actually find out.
"""
from app.tools import local as local_tools
from app.tools import server as vps_server
from app.tools import docker as vps_docker
from app.tools import network as vps_network
from app.tools import aws as aws_tools


def _safe(fn, *args, **kwargs) -> dict:
    """Run a check function, converting any raised exception into an
    UNKNOWN-worthy structured failure instead of propagating it — one
    broken check must never stop the rest of the health sweep."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"success": False, "error": f"Check raised an unexpected error: {e}"}


def _status_for_percent(value, warn_at: float, crit_at: float) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= crit_at:
        return "CRITICAL"
    if value >= warn_at:
        return "WARNING"
    return "HEALTHY"


def check_infrastructure_health() -> dict:
    """
    Run a full local + VPS + AWS health sweep in one call. Use this for
    broad requests like "check everything" or "is anything wrong" instead
    of calling individual tools one at a time.
    """
    report = {"local": {}, "vps": {}, "aws": {}}

    # --- Local ---
    cpu = _safe(local_tools.get_local_cpu_usage)
    mem = _safe(local_tools.get_local_memory_usage)
    disk = _safe(local_tools.get_local_disk_usage)
    net = _safe(local_tools.check_local_network)
    docker_local = _safe(local_tools.get_local_docker_status)

    report["local"]["cpu"] = _entry(cpu, lambda r: _status_for_percent(
        r.get("load_average_1min", 0) / max(r.get("cpu_count", 1), 1) * 100, 70, 90))
    report["local"]["memory"] = _entry(mem, lambda r: _status_for_percent(r.get("memory_percent"), 80, 90))
    report["local"]["disk"] = _entry(disk, lambda r: _status_for_percent(r.get("disk_percent"), 75, 90))
    report["local"]["network"] = _entry(net, lambda r: "HEALTHY" if r.get("reachable") else "CRITICAL")
    report["local"]["docker"] = _entry(docker_local, lambda r: "HEALTHY" if r.get("available") else "UNKNOWN")

    # --- VPS ---
    vps_cpu = _safe(vps_server.get_cpu_usage)
    vps_mem = _safe(vps_server.get_memory_usage)
    vps_disk = _safe(vps_server.get_disk_usage)
    vps_uptime = _safe(vps_server.get_uptime)
    vps_net = _safe(vps_network.check_network_connectivity)
    vps_docker_health = _safe(vps_docker.docker_health_status)

    report["vps"]["cpu"] = _entry(vps_cpu, lambda r: _status_for_percent(r.get("approx_cpu_percent"), 70, 90))
    report["vps"]["memory"] = _entry(vps_mem, lambda r: _status_for_percent(r.get("memory_percent"), 80, 90))
    report["vps"]["disk"] = _entry(vps_disk, lambda r: _status_for_percent(r.get("disk_percent"), 75, 90))
    report["vps"]["uptime"] = _entry(vps_uptime, lambda r: "HEALTHY")
    report["vps"]["network"] = _entry(vps_net, lambda r: "HEALTHY" if r.get("reachable") else "CRITICAL")

    if vps_docker_health.get("success"):
        problems = vps_docker_health.get("summary", {}).get("containers_with_problems", [])
        status = "CRITICAL" if any(True for _ in problems) and len(problems) > 2 else ("WARNING" if problems else "HEALTHY")
        report["vps"]["docker"] = {"status": status, "data": vps_docker_health}
    else:
        report["vps"]["docker"] = {"status": "UNKNOWN", "data": vps_docker_health}

    # --- AWS ---
    ec2 = _safe(aws_tools.list_ec2_instances)
    if ec2.get("success"):
        stopped = [i for i in ec2["instances"] if i["state"] not in ("running",)]
        status = "WARNING" if stopped else "HEALTHY"
        report["aws"]["ec2"] = {"status": status, "data": ec2}
    else:
        # Missing AWS config isn't a failure of YOUR infrastructure — it's
        # simply not set up, which is a different thing from broken.
        not_configured = any(k in ec2.get("error", "").lower() for k in ("credentials", "boto3", "region"))
        report["aws"]["ec2"] = {"status": "NOT_CONFIGURED" if not_configured else "UNKNOWN", "data": ec2}

    report["overall_status"] = _overall_status(report)
    return {"success": True, "report": report}


def _entry(result: dict, status_fn) -> dict:
    if not result.get("success", False):
        return {"status": "UNKNOWN", "data": result}
    return {"status": status_fn(result), "data": result}


def _overall_status(report: dict) -> str:
    """Worst status across every check wins, ignoring NOT_CONFIGURED (that's
    informational, not a problem with something that exists)."""
    order = ["CRITICAL", "WARNING", "UNKNOWN", "HEALTHY"]
    seen = set()
    for section in report.values():
        if not isinstance(section, dict):
            continue
        for entry in section.values():
            status = entry.get("status") if isinstance(entry, dict) else None
            if status and status != "NOT_CONFIGURED":
                seen.add(status)
    for level in order:
        if level in seen:
            return level
    return "UNKNOWN"
