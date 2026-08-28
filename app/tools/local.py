"""
Local machine (the machine running this agent, e.g. your WSL/Ubuntu
desktop) monitoring. Deliberately separate from app/ssh_client.py — this
NEVER goes over SSH to the VPS. Every command here is a fixed, hardcoded
argument list run directly via subprocess (no shell=True, no string
interpolation of any LLM-provided value), so there's no path from LLM
input to arbitrary local command execution, same guarantee as the SSH
whitelist gives for the VPS.
"""
import os
import platform
import shutil
import socket
import subprocess
import time


def get_local_cpu_usage() -> dict:
    """Approximate local CPU load from the OS load average."""
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        return {"success": False, "error": "Load average isn't available on this platform."}
    return {
        "success": True,
        "load_average_1min": load1,
        "load_average_5min": load5,
        "load_average_15min": load15,
        "cpu_count": os.cpu_count(),
    }


def get_local_memory_usage() -> dict:
    """Local memory usage, parsed from /proc/meminfo (Linux/WSL)."""
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])  # kB
    except FileNotFoundError:
        return {"success": False, "error": "/proc/meminfo not available (not Linux/WSL?)."}

    total_kb = meminfo.get("MemTotal")
    available_kb = meminfo.get("MemAvailable")
    if not total_kb or available_kb is None:
        return {"success": False, "error": "Couldn't parse /proc/meminfo."}

    used_kb = total_kb - available_kb
    return {
        "success": True,
        "memory_percent": round(used_kb / total_kb * 100, 1),
        "total_mb": round(total_kb / 1024, 1),
        "used_mb": round(used_kb / 1024, 1),
    }


def get_local_disk_usage(path: str = "/") -> dict:
    """Local disk usage for a given path (default: root)."""
    try:
        total, used, free = shutil.disk_usage(path)
    except OSError as e:
        return {"success": False, "error": f"Couldn't read disk usage for '{path}': {e}"}
    return {
        "success": True,
        "path": path,
        "disk_percent": round(used / total * 100, 1),
        "total_gb": round(total / 1024**3, 1),
        "used_gb": round(used / 1024**3, 1),
    }


def get_local_uptime() -> dict:
    """How long the local machine has been running, from /proc/uptime."""
    try:
        with open("/proc/uptime") as f:
            uptime_seconds = float(f.readline().split()[0])
    except FileNotFoundError:
        return {"success": False, "error": "/proc/uptime not available (not Linux/WSL?)."}
    return {"success": True, "uptime_hours": round(uptime_seconds / 3600, 1)}


def get_local_os_info() -> dict:
    """Basic OS/system identification."""
    return {
        "success": True,
        "system": platform.system(),
        "release": platform.release(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
    }


def check_local_network() -> dict:
    """
    Quick local network reachability check via a raw TCP connect (no
    ping/curl subprocess — just a socket, timed).
    """
    start = time.time()
    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=3):
            pass
        return {"success": True, "reachable": True, "latency_ms": round((time.time() - start) * 1000, 1)}
    except OSError as e:
        return {"success": True, "reachable": False, "detail": str(e)}


def list_local_top_processes(limit: int = 5) -> dict:
    """Top local processes by CPU usage (fixed command, no LLM-controlled args)."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"success": False, "error": f"limit must be a number, got: {limit!r}"}
    if limit < 1 or limit > 50:
        return {"success": False, "error": "limit must be between 1 and 50"}

    try:
        proc = subprocess.run(
            ["ps", "aux", "--sort=-%cpu"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"success": False, "error": f"Couldn't run local `ps`: {e}"}

    lines = proc.stdout.splitlines()
    if len(lines) < 2:
        return {"success": False, "error": "Couldn't parse local process list."}

    header = lines[0].split()
    try:
        cpu_idx, mem_idx = header.index("%CPU"), header.index("%MEM")
        cmd_idx, user_idx = header.index("COMMAND"), header.index("USER")
    except ValueError:
        return {"success": False, "error": "Unexpected local `ps` output format."}

    processes = []
    for line in lines[1:limit + 1]:
        parts = line.split(None, cmd_idx)
        if len(parts) <= cmd_idx:
            continue
        try:
            processes.append({
                "user": parts[user_idx],
                "cpu_percent": float(parts[cpu_idx]),
                "mem_percent": float(parts[mem_idx]),
                "command": parts[cmd_idx][:100],
            })
        except (ValueError, IndexError):
            continue
    return {"success": True, "processes": processes}


def get_local_docker_status() -> dict:
    """Local Docker status, if Docker is installed/running locally."""
    try:
        proc = subprocess.run(
            ["docker", "ps", "-a"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except FileNotFoundError:
        return {"success": True, "available": False, "detail": "Docker isn't installed locally."}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Local `docker ps` timed out."}

    if proc.returncode != 0:
        return {"success": True, "available": False, "detail": proc.stderr.strip() or "Docker daemon may not be running."}
    return {"success": True, "available": True, "raw": proc.stdout}
