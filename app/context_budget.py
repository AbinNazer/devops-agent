"""Bounded prompt construction to prevent history/tool-result bloat."""
import json, re

CORE_TOOL_NAMES = {"check_infrastructure_health", "get_cpu_usage", "get_memory_usage", "get_disk_usage", "docker_status", "run_diagnostic", "remote_search", "list_project_tree", "analyze_project"}
_COMPLEX = re.compile(r"\b(check|inspect|diagnos|debug|error|log|container|docker|vps|server|infrastructure|project|folder|file|nginx|cron|restart|status|health|research|search)\b", re.I)

def select_tools(tools, user_text):
    if not tools or _COMPLEX.search(user_text or ""):
        return tools
    selected = [t for t in tools if t.get("function", {}).get("name") in CORE_TOOL_NAMES]
    return selected or tools[:8]

def bound_history(history, max_messages=24, max_chars=48000, tool_result_chars=3500):
    if not history: return history
    bounded = []
    for message in history:
        item = dict(message)
        if item.get("role") == "tool" and isinstance(item.get("content"), str) and len(item["content"]) > tool_result_chars:
            item["content"] = item["content"][:tool_result_chars] + "\n...[tool result truncated in context]"
        bounded.append(item)
    if len(bounded) > max_messages:
        first = bounded[:1] if bounded[0].get("role") == "system" else []
        bounded = first + bounded[-(max_messages-len(first)):]
    while sum(len(json.dumps(m, default=str)) for m in bounded) > max_chars and len(bounded) > 2:
        index = 1 if bounded[0].get("role") == "system" else 0
        bounded.pop(index)
    return bounded
