"""
Phase 2: real Docker status/logs/inspect/stats pulled over SSH from the
configured VPS, through the whitelist enforced in app/ssh_whitelist.py.
"""
from app.ssh_client import run_whitelisted_command, truncate
from app.ssh_whitelist import build_docker_logs_command, build_docker_inspect_command, ValidationError


def docker_status() -> dict:
    """List all containers, including stopped ones, and their status."""
    result = run_whitelisted_command("docker ps -a")
    if not result["success"]:
        return result
    return {"success": True, "raw": truncate(result["stdout"])}


def docker_stats() -> dict:
    """Real-time resource usage snapshot for currently running containers."""
    result = run_whitelisted_command("docker stats --no-stream")
    if not result["success"]:
        return result
    return {"success": True, "raw": truncate(result["stdout"])}


def docker_health_status() -> dict:
    """
    List all containers with their run STATE (running/exited/restarting/
    created/dead/paused/unknown) and, separately, their Docker HEALTHCHECK
    status (healthy/unhealthy/starting/no_healthcheck/unknown).

    These are deliberately kept separate: a container with no HEALTHCHECK
    defined is still RUNNING — it must never be counted as "unknown" state
    or implicitly treated as unhealthy just because no healthcheck exists.
    """
    result = run_whitelisted_command("docker ps -a --format '{{.Names}}|{{.Status}}|{{.State}}'")
    if not result["success"]:
        return result

    containers = []
    for line in result["stdout"].splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        name, status_text, state_field = parts[0], parts[1], parts[2]
        containers.append({
            "name": name,
            "state": _classify_state(state_field, status_text),
            "health": _classify_health(status_text),
            "status_text": status_text,
        })

    return {
        "success": True,
        "containers": containers,
        "summary": _summarize_docker_health(containers),
    }


_KNOWN_STATES = {"running", "exited", "restarting", "created", "dead", "paused", "removing"}


def _classify_state(state_field: str, status_text: str) -> str:
    """Container run state — authoritative from Docker's own .State field.
    Falls back to parsing .Status text only if .State is missing/unexpected."""
    state = (state_field or "").strip().lower()
    if state in _KNOWN_STATES:
        return state

    text = status_text.lower()
    if text.startswith("up"):
        return "running"
    if text.startswith("exited"):
        return "exited"
    if "restarting" in text:
        return "restarting"
    return "unknown"


def _classify_health(status_text: str) -> str:
    """
    Docker HEALTHCHECK status, parsed from the status text's parenthetical
    suffix (e.g. "Up 2 hours (healthy)"). A container with no HEALTHCHECK
    defined has no such suffix at all — that is "no_healthcheck", NOT
    "unhealthy" and NOT "unknown".
    """
    text = status_text.lower()
    if "unhealthy" in text:
        return "unhealthy"
    if "health: starting" in text or "(starting)" in text:
        return "starting"
    if "healthy" in text:  # checked after "unhealthy" so it doesn't false-match
        return "healthy"
    return "no_healthcheck"


def _summarize_docker_health(containers: list) -> dict:
    state_counts = {s: 0 for s in ("running", "exited", "restarting", "created", "dead", "paused", "removing", "unknown")}
    health_counts = {h: 0 for h in ("healthy", "unhealthy", "starting", "no_healthcheck", "unknown")}

    for c in containers:
        state_counts[c["state"]] = state_counts.get(c["state"], 0) + 1
        health_counts[c["health"]] = health_counts.get(c["health"], 0) + 1

    # A "problem" is a bad run state or an actual failing healthcheck.
    # Missing a healthcheck is NOT a problem — that's the whole point of this fix.
    problems = [
        c["name"] for c in containers
        if c["state"] in ("exited", "restarting", "dead") or c["health"] == "unhealthy"
    ]

    return {
        "total": len(containers),
        "running": state_counts["running"],
        "stopped": state_counts["exited"] + state_counts["dead"],
        "restarting": state_counts["restarting"],
        "healthy": health_counts["healthy"],
        "unhealthy": health_counts["unhealthy"],
        "no_healthcheck": health_counts["no_healthcheck"],
        "unknown": state_counts["unknown"] + health_counts["unknown"],
        "state_counts": state_counts,
        "health_counts": health_counts,
        "containers_with_problems": problems,
    }


def docker_logs(container_name: str, lines: int = 50) -> dict:
    """Get recent log lines for a specific container."""
    try:
        command = build_docker_logs_command(container_name, lines)
    except ValidationError as e:
        return {"success": False, "error": str(e)}

    result = run_whitelisted_command(command)
    if not result["success"]:
        return result
    if result["exit_code"] != 0:
        return {"success": False, "error": result["stderr"].strip() or f"docker logs exited with code {result['exit_code']}"}
    return {"success": True, "container": container_name, "logs": result["stdout"] or "(no output)"}


def docker_inspect(container_name: str) -> dict:
    """Get detailed inspect output for a specific container."""
    try:
        command = build_docker_inspect_command(container_name)
    except ValidationError as e:
        return {"success": False, "error": str(e)}

    result = run_whitelisted_command(command)
    if not result["success"]:
        return result
    if result["exit_code"] != 0:
        return {"success": False, "error": result["stderr"].strip() or f"docker inspect exited with code {result['exit_code']}"}
    return {"success": True, "container": container_name, "details": result["stdout"]}