"""
Phase 2: real Docker status/logs/inspect/stats pulled over SSH from the
configured VPS, through the whitelist enforced in app/ssh_whitelist.py.
"""
from app.ssh_client import run_whitelisted_command, truncate
from app.executor import run_action_command
from app.ssh_whitelist import build_docker_logs_command, build_docker_inspect_command, validate_container_name, ValidationError


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


def resolve_container_name(query: str) -> dict:
    """Resolve a user's approximate container name without executing anything."""
    if not isinstance(query, str) or not query.strip():
        return {"success": False, "error": "A container name or fragment is required."}
    result = run_whitelisted_command("docker ps -a --format '{{.Names}}'")
    if not result.get("success"):
        return result
    names = [line.strip() for line in result.get("stdout", "").splitlines() if line.strip()]
    needle = query.strip().lower()
    compact = lambda value: "".join(ch for ch in value.lower() if ch.isalnum())
    exact = [name for name in names if name.lower() == needle or compact(name) == compact(needle)]
    if len(exact) == 1:
        return {"success": True, "match": exact[0], "candidates": exact}
    candidates = [name for name in names if needle in name.lower() or compact(needle) in compact(name)]
    if len(candidates) == 1:
        return {"success": True, "match": candidates[0], "candidates": candidates}
    return {"success": True, "match": "", "candidates": candidates[:20], "ambiguous": len(candidates) > 1}


def _docker_action(container_name: str, action: str) -> dict:
    try:
        name = validate_container_name(container_name)
    except ValidationError as exc:
        return {"success": False, "error": str(exc)}
    if action not in {"start", "stop", "restart"}:
        return {"success": False, "error": "Unsupported Docker action"}
    result = run_action_command(f"docker {action} {name}")
    return {**result, "container": name, "action": action, "success": bool(result.get("success") and result.get("exit_code") == 0)}


def docker_start_container(container_name: str) -> dict:
    return _docker_action(container_name, "start")


def docker_stop_container(container_name: str) -> dict:
    return _docker_action(container_name, "stop")


def docker_restart_container(container_name: str) -> dict:
    return _docker_action(container_name, "restart")


def docker_image_usage() -> dict:
    result = run_whitelisted_command("docker images --format '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.Size}}|{{.CreatedAt}}'")
    if not result.get("success") or result.get("exit_code") != 0:
        return result
    used = docker_status()
    references = {}
    if used.get("success"):
        for line in used.get("raw", "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                references.setdefault(parts[1], []).append(parts[0])
    images = []
    for line in result.get("stdout", "").splitlines():
        repo, tag, image_id, size, created = (line.split("|", 4) + [""] * 5)[:5]
        names = references.get(f"{repo}:{tag}", [])
        images.append({"repository": repo, "tag": tag, "id": image_id, "size": size, "created": created, "containers": names, "state": "USED" if names else ("DANGLING" if repo == "<none>" else "UNUSED")})
    return {"success": True, "images": images}


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
