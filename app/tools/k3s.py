"""
K3s/Kubernetes monitoring. Only the exact `kubectl get ...` / `cluster-info`
commands in app/ssh_whitelist.py are reachable — no delete, apply, create,
patch, edit, rollout, scale, or exec is whitelisted, so none of those are
possible through this module regardless of what's asked.

If kubectl/k3s isn't installed on the VPS, these commands fail naturally
(non-zero exit, "command not found" in stderr) and that's surfaced as a
normal tool error — no special-casing needed.
"""
from app.ssh_client import run_whitelisted_command


def get_k3s_nodes() -> dict:
    """List cluster nodes and their status."""
    return _run("kubectl get nodes")


def get_k3s_pods() -> dict:
    """List all pods across all namespaces."""
    return _run("kubectl get pods -A")


def get_k3s_deployments() -> dict:
    """List all deployments across all namespaces."""
    return _run("kubectl get deployments -A")


def get_k3s_services() -> dict:
    """List all services across all namespaces."""
    return _run("kubectl get services -A")


def get_k3s_events() -> dict:
    """List recent cluster events across all namespaces."""
    return _run("kubectl get events -A")


def get_k3s_cluster_status() -> dict:
    """General cluster reachability/control-plane status."""
    return _run("kubectl cluster-info")


def _run(command: str) -> dict:
    result = run_whitelisted_command(command)
    if not result["success"]:
        return result
    if result["exit_code"] != 0:
        stderr = result["stderr"].strip()
        if "not found" in stderr.lower() or "command not found" in stderr.lower():
            return {"success": False, "error": "kubectl isn't available on the VPS (K3s may not be installed)."}
        return {"success": False, "error": stderr or f"Command exited with code {result['exit_code']}"}
    return {"success": True, "raw": result["stdout"]}
