"""
Phase 2: reachability check.

The SSH whitelist deliberately excludes ping/curl/wget (both are on the
explicit "never allow" list), so this can't ask the VPS to probe an
arbitrary external host without adding a new whitelist entry. Instead this
repurposes the existing SSH connection itself as the check that actually
matters for a DevOps assistant: "is the VPS reachable and responsive over
SSH right now." No new command needed.
"""
import time

from app.ssh_client import run_whitelisted_command


def check_network_connectivity() -> dict:
    """Check whether the VPS is reachable and responsive over SSH."""
    start = time.time()
    result = run_whitelisted_command("uptime")
    latency_ms = round((time.time() - start) * 1000, 1)

    if not result["success"]:
        return {"success": True, "reachable": False, "latency_ms": None, "detail": result["error"]}
    return {"success": True, "reachable": True, "latency_ms": latency_ms}