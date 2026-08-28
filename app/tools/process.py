"""
Phase 2: real process listing via a single whitelisted `ps aux --sort=-%cpu`
call. Sorting/limiting happens in Python after the fixed command returns —
`limit` is never interpolated into the command string itself.
"""
from app.ssh_client import run_whitelisted_command

MAX_LIMIT = 50


def list_top_processes(limit: int = 5) -> dict:
    """List the top processes on the VPS by CPU usage."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"success": False, "error": f"limit must be a number, got: {limit!r}"}
    if limit < 1 or limit > MAX_LIMIT:
        return {"success": False, "error": f"limit must be between 1 and {MAX_LIMIT}"}

    result = run_whitelisted_command("ps aux --sort=-%cpu")
    if not result["success"]:
        return result

    lines = result["stdout"].splitlines()
    if len(lines) < 2:
        return {"success": False, "error": "Couldn't parse process list output."}

    header = lines[0].split()
    try:
        cpu_idx = header.index("%CPU")
        mem_idx = header.index("%MEM")
        cmd_idx = header.index("COMMAND")
        user_idx = header.index("USER")
        pid_idx = header.index("PID")
    except ValueError:
        return {"success": False, "error": "Unexpected `ps aux` output format."}

    processes = []
    for line in lines[1:limit + 1]:
        parts = line.split(None, cmd_idx)
        if len(parts) <= cmd_idx:
            continue
        try:
            processes.append({
                "user": parts[user_idx],
                "pid": parts[pid_idx],
                "cpu_percent": float(parts[cpu_idx]),
                "mem_percent": float(parts[mem_idx]),
                "command": parts[cmd_idx][:100],
            })
        except (ValueError, IndexError):
            continue

    return {"success": True, "processes": processes}