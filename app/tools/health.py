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
from app.config import Config
from app.ssh_client import run_whitelisted_commands
import threading
import time
import os

_CACHE_TTL = 20
_cache_lock = threading.Lock()
_cached_report = None
_cached_at = 0.0


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

    Local, VPS, and AWS sweeps execute concurrently via ThreadPoolExecutor
    so the total wall-clock time is bounded by the slowest single lane
    (~1-3 s) rather than the sum of all lanes (previously 4-8+ s).
    """
    global _cached_report, _cached_at
    now = time.monotonic()
    with _cache_lock:
        if not os.getenv("PYTEST_CURRENT_TEST") and _cached_report is not None and now - _cached_at < _CACHE_TTL:
            return {**_cached_report, "cached": True, "cache_age_seconds": round(now - _cached_at, 1)}
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _local_sweep():
        return {
            "cpu":    _safe(local_tools.get_local_cpu_usage),
            "mem":    _safe(local_tools.get_local_memory_usage),
            "disk":   _safe(local_tools.get_local_disk_usage),
            "net":    _safe(local_tools.check_local_network),
            "docker": _safe(local_tools.get_local_docker_status),
        }

    def _vps_sweep():
        if Config.EXECUTION_MODE.lower() == "ssh":
            commands = ["uptime", "nproc", "free -h", "df -h", "docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'"]
            try:
                results = run_whitelisted_commands(commands, max_workers=5)
            except Exception:
                # Preserve the test/mock and degraded-connection behavior of
                # the individual tools if batch transport setup fails.
                return {
                    "cpu": _safe(vps_server.get_cpu_usage),
                    "mem": _safe(vps_server.get_memory_usage),
                    "disk": _safe(vps_server.get_disk_usage),
                    "uptime": _safe(vps_server.get_uptime),
                    "net": _safe(vps_network.check_network_connectivity),
                    "docker": _safe(vps_docker.docker_health_status),
                }
            mapped = dict(zip(commands, results))
            return {
                "cpu": _parse_cpu(mapped.get("uptime", {}), mapped.get("nproc", {})),
                "mem": _parse_memory(mapped.get("free -h", {})),
                "disk": _parse_disk(mapped.get("df -h", {})),
                "uptime": _parse_uptime(mapped.get("uptime", {})),
                "net": {"success": bool(mapped.get("uptime", {}).get("success")), "reachable": bool(mapped.get("uptime", {}).get("success")), "latency_ms": None},
                "docker": _parse_docker(mapped.get("docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'", {})),
            }
        return {
            "cpu":    _safe(vps_server.get_cpu_usage),
            "mem":    _safe(vps_server.get_memory_usage),
            "disk":   _safe(vps_server.get_disk_usage),
            "uptime": _safe(vps_server.get_uptime),
            "net":    _safe(vps_network.check_network_connectivity),
            "docker": _safe(vps_docker.docker_health_status),
        }

    def _aws_sweep():
        return {
            "ec2": _safe(aws_tools.list_ec2_instances),
        }

    report = {"local": {}, "vps": {}, "aws": {}}

    with ThreadPoolExecutor(max_workers=3) as pool:
        future_local = pool.submit(_local_sweep)
        future_vps   = pool.submit(_vps_sweep)
        future_aws   = pool.submit(_aws_sweep)

        local_data = future_local.result()
        vps_data   = future_vps.result()
        aws_data   = future_aws.result()

    # --- Local ---
    cpu, mem, disk, net, docker_local = (
        local_data["cpu"], local_data["mem"], local_data["disk"],
        local_data["net"], local_data["docker"],
    )
    report["local"]["cpu"] = _entry(cpu, lambda r: _status_for_percent(
        r.get("load_average_1min", 0) / max(r.get("cpu_count", 1), 1) * 100, 70, 90))
    report["local"]["memory"] = _entry(mem, lambda r: _status_for_percent(r.get("memory_percent"), 80, 90))
    report["local"]["disk"]   = _entry(disk, lambda r: _status_for_percent(r.get("disk_percent"), 75, 90))
    report["local"]["network"] = _entry(net, lambda r: "HEALTHY" if r.get("reachable") else "CRITICAL")
    report["local"]["docker"]  = _entry(docker_local, lambda r: "HEALTHY" if r.get("available") else "UNKNOWN")

    # --- VPS ---
    vps_cpu, vps_mem, vps_disk, vps_uptime, vps_net, vps_docker_health = (
        vps_data["cpu"], vps_data["mem"], vps_data["disk"],
        vps_data["uptime"], vps_data["net"], vps_data["docker"],
    )
    report["vps"]["cpu"]     = _entry(vps_cpu, lambda r: _status_for_percent(r.get("approx_cpu_percent"), 70, 90))
    report["vps"]["memory"]  = _entry(vps_mem, lambda r: _status_for_percent(r.get("memory_percent"), 80, 90))
    report["vps"]["disk"]    = _entry(vps_disk, lambda r: _status_for_percent(r.get("disk_percent"), 75, 90))
    report["vps"]["uptime"]  = _entry(vps_uptime, lambda r: "HEALTHY")
    report["vps"]["network"] = _entry(vps_net, lambda r: "HEALTHY" if r.get("reachable") else "CRITICAL")

    if vps_docker_health.get("success"):
        problems = vps_docker_health.get("summary", {}).get("containers_with_problems", [])
        status = "CRITICAL" if any(True for _ in problems) and len(problems) > 2 else ("WARNING" if problems else "HEALTHY")
        report["vps"]["docker"] = {"status": status, "data": vps_docker_health}
    else:
        report["vps"]["docker"] = {"status": "UNKNOWN", "data": vps_docker_health}

    # --- AWS ---
    ec2 = aws_data["ec2"]
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
    result = {"success": True, "report": report, "cached": False}
    with _cache_lock:
        _cached_report = result
        _cached_at = time.monotonic()
    return result


def _parse_uptime(result: dict) -> dict:
    return {"success": True, "raw": result.get("stdout", "").strip()} if result.get("success") else result


def _parse_cpu(uptime: dict, nproc: dict) -> dict:
    import re
    if not uptime.get("success"):
        return uptime
    match = re.search(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", uptime.get("stdout", ""))
    if not match:
        return {"success": False, "error": "Couldn't parse load average from uptime output."}
    try:
        cores = max(1, int(nproc.get("stdout", "1").strip()))
    except ValueError:
        cores = 1
    load = float(match.group(1))
    return {"success": True, "cores": cores, "load_average_1min": load, "load_average_5min": float(match.group(2)), "load_average_15min": float(match.group(3)), "load_per_core_1min": round(load / cores, 2), "note": "Load average is not CPU utilization."}


def _parse_memory(result: dict) -> dict:
    if not result.get("success"):
        return result
    try:
        line = next(item for item in result.get("stdout", "").splitlines() if item.lower().startswith("mem:"))
        parts = line.split()
        total, used = parts[1], parts[2]
        return {"success": True, "memory_percent": _human_percent(used, total), "total": total, "used": used}
    except (StopIteration, IndexError, ValueError):
        return {"success": False, "error": "Couldn't parse memory usage output."}


def _parse_disk(result: dict) -> dict:
    if not result.get("success"):
        return result
    try:
        line = next(item for item in result.get("stdout", "").splitlines() if item.split()[-1] == "/")
        parts = line.split()
        return {"success": True, "disk_percent": int(parts[4].rstrip("%")), "filesystem": parts[0], "size": parts[1], "used": parts[2]}
    except (StopIteration, IndexError, ValueError):
        return {"success": False, "error": "Couldn't parse disk usage output."}


def _human_percent(used: str, total: str) -> float:
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    def convert(value):
        value = value.strip()
        for unit, multiplier in units.items():
            if value.endswith(unit):
                return float(value[:-len(unit)]) * multiplier
        return float(value)
    return round(convert(used) / convert(total) * 100, 1)


def _parse_docker(result: dict) -> dict:
    if not result.get("success"):
        return result
    containers = []
    for line in result.get("stdout", "").splitlines():
        parts = line.split("|")
        if len(parts) >= 3:
            containers.append({"name": parts[0], "state": parts[2], "health": "healthy" if "healthy" in parts[1].lower() else ("unhealthy" if "unhealthy" in parts[1].lower() else "no_healthcheck"), "status_text": parts[1]})
    problems = [item["name"] for item in containers if item["state"] != "running" or item["health"] == "unhealthy"]
    return {"success": True, "containers": containers, "summary": {"total": len(containers), "running": sum(item["state"] == "running" for item in containers), "containers_with_problems": problems}}


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
