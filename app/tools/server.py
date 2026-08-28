"""
Phase 2: real server metrics pulled over SSH from the configured VPS.
Signatures/return shapes are conceptually the same as Phase 1 — only the
internals swapped from mock random data to real whitelisted SSH commands.
"""
import re

from app.ssh_client import run_whitelisted_command, truncate
from app.ssh_whitelist import build_service_status_command, ValidationError


def get_uptime() -> dict:
    """Return the VPS's real uptime info (raw output of `uptime`)."""
    result = run_whitelisted_command("uptime")
    if not result["success"]:
        return result
    return {"success": True, "raw": result["stdout"].strip()}


def get_memory_usage() -> dict:
    """Return real memory usage percentage, parsed from `free -h`."""
    result = run_whitelisted_command("free -h")
    if not result["success"]:
        return result
    try:
        mem_line = next(l for l in result["stdout"].splitlines() if l.lower().startswith("mem:"))
        parts = mem_line.split()
        total, used = parts[1], parts[2]
        return {
            "success": True,
            "memory_percent": _percent_from_human(used, total),
            "total": total,
            "used": used,
        }
    except (StopIteration, IndexError, ValueError):
        return {"success": False, "error": "Couldn't parse memory usage output."}


def get_disk_usage() -> dict:
    """Return real root filesystem usage percentage, parsed from `df -h`."""
    result = run_whitelisted_command("df -h")
    if not result["success"]:
        return result
    try:
        root_line = next(l for l in result["stdout"].splitlines() if l.split()[-1] == "/")
        parts = root_line.split()
        return {
            "success": True,
            "disk_percent": int(parts[4].rstrip("%")),
            "filesystem": parts[0],
            "size": parts[1],
            "used": parts[2],
        }
    except (StopIteration, IndexError, ValueError):
        return {"success": False, "error": "Couldn't parse disk usage output (no '/' mount found)."}


def get_cpu_usage() -> dict:
    """
    Approximate CPU load from `uptime`'s load averages, relative to core count (`nproc`).
    """
    uptime_result = run_whitelisted_command("uptime")
    if not uptime_result["success"]:
        return uptime_result
    
    nproc_result = run_whitelisted_command("nproc")
    cores = 1
    if nproc_result["success"]:
        try:
            cores = int(nproc_result["stdout"].strip())
        except ValueError:
            pass

    match = re.search(r"load average:\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)", uptime_result["stdout"])
    if not match:
        return {"success": False, "error": "Couldn't parse load average from uptime output."}
    
    load_1min = float(match.group(1))
    cpu_percent = (load_1min / cores) * 100
    
    return {
        "success": True,
        "cores": cores,
        "load_average_1min": load_1min,
        "load_average_5min": float(match.group(2)),
        "load_average_15min": float(match.group(3)),
        "approx_cpu_percent": round(cpu_percent, 1),
        "note": f"Approximated from 1-min load average ({load_1min}) divided by cores ({cores}).",
    }


def get_service_status(service_name) -> dict:
    """Get the systemd status of a named service."""
    try:
        command = build_service_status_command(service_name)
    except ValidationError as e:
        return {"success": False, "error": str(e)}
    result = run_whitelisted_command(command)
    if not result["success"]:
        return result
    return {"success": True, "service": service_name, "status_output": truncate(result["stdout"])}


def _percent_from_human(used: str, total: str) -> float:
    """Convert human-readable sizes (e.g. '1.2G', '512M') from `free -h`/`df -h` to a percentage."""
    def to_bytes(s):
        s = s.strip()
        units = {
            "K": 1024,
            "Ki": 1024,
            "M": 1024**2,
            "Mi": 1024**2,
            "G": 1024**3,
            "Gi": 1024**3,
            "T": 1024**4,
            "Ti": 1024**4,
        }
        for unit in sorted(units, key=len, reverse=True):
            if s.endswith(unit):
                return float(s[:-len(unit)]) * units[unit]
        return float(s)

    total_b, used_b = to_bytes(total), to_bytes(used)
    if total_b == 0:
        raise ValueError("total is zero")
    return round((used_b / total_b) * 100, 1)