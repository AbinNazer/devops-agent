"""
Jenkins monitoring. Jenkins runs as a Docker container on the VPS, so this
reuses the existing docker tools rather than adding new SSH commands —
"is Jenkins healthy" is really "is the jenkins container running/healthy",
which docker_health_status() already answers.

No HTTP reachability check (no curl/wget in the whitelist, and neither
belongs there — see app/tools/network.py for why). This only reports
container-level status, which is the read-only signal actually available.
"""
from app.config import Config
from app.tools.docker import docker_health_status, docker_stats, docker_logs


def get_jenkins_status() -> dict:
    """Is the Jenkins container running, and what state is it in?"""
    result = docker_health_status()
    if not result["success"]:
        return result

    name = Config.JENKINS_CONTAINER_NAME
    match = next((c for c in result["containers"] if c["name"] == name), None)
    if not match:
        return {
            "success": True,
            "found": False,
            "detail": f"No container named '{name}' found. Set JENKINS_CONTAINER_NAME in .env if it's named differently.",
        }
    return {"success": True, "found": True, "container": match}


def get_jenkins_stats() -> dict:
    """CPU/memory usage of the Jenkins container (from the general docker_stats snapshot)."""
    result = docker_stats()
    if not result["success"]:
        return result
    name = Config.JENKINS_CONTAINER_NAME
    lines = [l for l in result["raw"].splitlines() if name in l or l.strip().upper().startswith("CONTAINER")]
    if len(lines) < 2:
        return {"success": True, "found": False, "detail": f"'{name}' not found in docker stats (may not be running)."}
    return {"success": True, "found": True, "raw": "\n".join(lines)}


def get_jenkins_logs(lines: int = 50) -> dict:
    """Recent log lines from the Jenkins container."""
    return docker_logs(Config.JENKINS_CONTAINER_NAME, lines)
