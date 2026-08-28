"""
Single source of truth for available tools: the OpenAI-style schemas shown
to the LLM, and the dispatch table the Agent uses to actually call them.
Adding a new tool means adding one entry here — nothing else changes.

Also handles generic argument type-coercion: some models occasionally send
a number as a string (e.g. lines="60" instead of 60), which strict
providers reject outright. Rather than patching this per-tool as it comes
up, we coerce arguments against the declared schema type before dispatch,
for every tool, automatically.
"""
from app.tools.server import (
    get_cpu_usage, get_memory_usage, get_disk_usage, get_uptime, get_service_status,
)
from app.tools.docker import docker_status, docker_logs, docker_inspect, docker_stats, docker_health_status
from app.tools.network import check_network_connectivity
from app.tools.process import list_top_processes
from app.tools.jenkins import get_jenkins_status, get_jenkins_stats, get_jenkins_logs
from app.tools.k3s import (
    get_k3s_nodes, get_k3s_pods, get_k3s_deployments, get_k3s_services,
    get_k3s_events, get_k3s_cluster_status,
)
from app.tools.local import (
    get_local_cpu_usage, get_local_memory_usage, get_local_disk_usage,
    get_local_uptime, get_local_os_info, check_local_network,
    list_local_top_processes, get_local_docker_status,
)
from app.tools.aws import list_ec2_instances, get_ec2_cpu_metrics
from app.tools.health import check_infrastructure_health

from app.intelligence.diagnosis import plan_diagnostics
from app.intelligence.summaries import generate_technical_summary
from app.intelligence.health_analyzer import evaluate_health
from app.memory.repository import MemoryRepository
from app.memory.models import Memory
from app.memory.feedback import record_feedback as mem_record_feedback
from app.memory.feedback import record_outcome as mem_record_outcome

memory_repo = MemoryRepository("memory.db")

def search_memory(args):
    query = args.get("query")
    mtype = args.get("mtype")
    env = args.get("env")
    comp = args.get("comp")
    results = memory_repo.search(query, mtype, env, comp)
    return {"success": True, "memories": [{"id": m.id, "title": m.title, "content": m.content, "confidence": m.confidence} for m in results]}

def store_incident_memory(args):
    m = Memory(
        title=args.get("title", ""),
        content=args.get("content", ""),
        type="episodic",
        environment=args.get("env", ""),
        component=args.get("comp", "")
    )
    memory_repo.store_memory(m)
    return {"success": True, "memory_id": m.id}

def record_feedback(args):
    mem_record_feedback(memory_repo.store, args.get("memory_id"), args.get("is_positive", True))
    return {"success": True}

def record_outcome(args):
    mem_record_outcome(memory_repo.store, args.get("memory_id"), args.get("outcome", ""))
    return {"success": True}


TOOL_SCHEMAS = [
    # --- VPS: server ---
    {
        "type": "function",
        "function": {
            "name": "get_cpu_usage",
            "description": "Get an approximate CPU load reading from the VPS's load averages.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory_usage",
            "description": "Get the current memory usage percentage of the VPS.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_disk_usage",
            "description": "Get the current disk usage percentage of the VPS's root filesystem.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_uptime",
            "description": "Get how long the VPS has been running without a restart.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "Get the systemd status of a named service on the VPS (e.g. nginx).",
            "parameters": {
                "type": "object",
                "properties": {"service_name": {"type": "string", "description": "e.g. 'nginx'"}},
                "required": ["service_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_top_processes",
            "description": "List the top processes on the VPS by CPU usage.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "How many processes, default 5"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_network_connectivity",
            "description": "Check whether the VPS is reachable and responsive right now.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # --- VPS: docker ---
    {
        "type": "function",
        "function": {
            "name": "docker_status",
            "description": "List all containers on the VPS, including stopped ones, and their raw status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_health_status",
            "description": "List VPS containers with classified state (running/exited/restarting/healthy/unhealthy) — use this to answer 'are any containers stopped/unhealthy' rather than docker_status.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_stats",
            "description": "Get a live CPU/memory usage snapshot for running containers on the VPS.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_logs",
            "description": "Get recent log lines for a specific container on the VPS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container_name": {"type": "string"},
                    "lines": {"type": "integer", "description": "default 50"},
                },
                "required": ["container_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_inspect",
            "description": "Get detailed configuration/state info for a specific container on the VPS.",
            "parameters": {
                "type": "object",
                "properties": {"container_name": {"type": "string"}},
                "required": ["container_name"],
            },
        },
    },
    # --- Jenkins ---
    {
        "type": "function",
        "function": {
            "name": "get_jenkins_status",
            "description": "Check whether the Jenkins container is running and its state — use for 'is Jenkins healthy/up'.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_jenkins_stats",
            "description": "CPU/memory usage of the Jenkins container.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_jenkins_logs",
            "description": "Recent log lines from the Jenkins container.",
            "parameters": {
                "type": "object",
                "properties": {"lines": {"type": "integer", "description": "default 50"}},
            },
        },
    },
    # --- K3s ---
    {"type": "function", "function": {"name": "get_k3s_nodes", "description": "List K3s cluster nodes and status.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_k3s_pods", "description": "List all K3s pods across all namespaces.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_k3s_deployments", "description": "List all K3s deployments across all namespaces.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_k3s_services", "description": "List all K3s services across all namespaces.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_k3s_events", "description": "List recent K3s cluster events across all namespaces.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_k3s_cluster_status", "description": "General K3s cluster/control-plane reachability status.", "parameters": {"type": "object", "properties": {}}}},
    # --- Local machine ---
    {"type": "function", "function": {"name": "get_local_cpu_usage", "description": "CPU load on the LOCAL machine running this agent (not the VPS).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_local_memory_usage", "description": "Memory usage on the LOCAL machine (not the VPS).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_local_disk_usage", "description": "Disk usage on the LOCAL machine (not the VPS).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_local_uptime", "description": "Uptime of the LOCAL machine (not the VPS).", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_local_os_info", "description": "OS/platform info of the LOCAL machine.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "check_local_network", "description": "Check LOCAL machine's network reachability (not the VPS's).", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {
            "name": "list_local_top_processes",
            "description": "Top processes by CPU on the LOCAL machine (not the VPS).",
            "parameters": {"type": "object", "properties": {"limit": {"type": "integer", "description": "default 5"}}},
        },
    },
    {"type": "function", "function": {"name": "get_local_docker_status", "description": "LOCAL machine's Docker status, if Docker is installed locally (not the VPS's Docker).", "parameters": {"type": "object", "properties": {}}}},
    # --- AWS ---
    {"type": "function", "function": {"name": "list_ec2_instances", "description": "List AWS EC2 instances with id, name, state, type, AZ, and IPs.", "parameters": {"type": "object", "properties": {}}}},
    {
        "type": "function",
        "function": {
            "name": "get_ec2_cpu_metrics",
            "description": "Average CloudWatch CPU utilization for a specific EC2 instance over recent minutes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string"},
                    "minutes": {"type": "integer", "description": "lookback window, default 30"},
                },
                "required": ["instance_id"],
            },
        },
    },
    # --- Unified health check ---
    {
        "type": "function",
        "function": {
            "name": "check_infrastructure_health",
            "description": (
                "Run a full local + VPS + AWS health sweep in ONE call. Use this for broad "
                "requests like 'check everything', 'is anything wrong', or 'how's my infrastructure' "
                "instead of calling many individual tools one at a time."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },

    # --- Intelligence Engine ---
    {
        "type": "function",
        "function": {
            "name": "plan_diagnostics",
            "description": "Create a diagnostic plan for a complex question to avoid duplicate calls.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The user's question or issue."}
                },
                "required": ["question"]
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_technical_summary",
            "description": "Generate a structured summary of infrastructure health.",
            "parameters": {
                "type": "object",
                "properties": {
                    "health_data_keys": {"type": "string", "description": "JSON string of health data."}
                }
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search agent memory for past incidents, facts, and context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "mtype": {"type": "string"},
                    "env": {"type": "string"},
                    "comp": {"type": "string"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "store_incident_memory",
            "description": "Store an incident or fact to agent memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "env": {"type": "string"},
                    "comp": {"type": "string"}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_feedback",
            "description": "Record user feedback for a specific memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "is_positive": {"type": "boolean"}
                },
                "required": ["memory_id", "is_positive"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_outcome",
            "description": "Record the outcome of applying a memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "outcome": {"type": "string"}
                },
                "required": ["memory_id", "outcome"]
            }
        }
    }
]

_DISPATCH = {
    "get_cpu_usage": lambda args: get_cpu_usage(),
    "get_memory_usage": lambda args: get_memory_usage(),
    "get_disk_usage": lambda args: get_disk_usage(),
    "get_uptime": lambda args: get_uptime(),
    "get_service_status": lambda args: get_service_status(args.get("service_name")),
    "list_top_processes": lambda args: list_top_processes(args.get("limit", 5)),
    "check_network_connectivity": lambda args: check_network_connectivity(),
    "docker_status": lambda args: docker_status(),
    "docker_health_status": lambda args: docker_health_status(),
    "docker_stats": lambda args: docker_stats(),
    "docker_logs": lambda args: docker_logs(args.get("container_name"), args.get("lines", 50)),
    "docker_inspect": lambda args: docker_inspect(args.get("container_name")),
    "get_jenkins_status": lambda args: get_jenkins_status(),
    "get_jenkins_stats": lambda args: get_jenkins_stats(),
    "get_jenkins_logs": lambda args: get_jenkins_logs(args.get("lines", 50)),
    "get_k3s_nodes": lambda args: get_k3s_nodes(),
    "get_k3s_pods": lambda args: get_k3s_pods(),
    "get_k3s_deployments": lambda args: get_k3s_deployments(),
    "get_k3s_services": lambda args: get_k3s_services(),
    "get_k3s_events": lambda args: get_k3s_events(),
    "get_k3s_cluster_status": lambda args: get_k3s_cluster_status(),
    "get_local_cpu_usage": lambda args: get_local_cpu_usage(),
    "get_local_memory_usage": lambda args: get_local_memory_usage(),
    "get_local_disk_usage": lambda args: get_local_disk_usage(),
    "get_local_uptime": lambda args: get_local_uptime(),
    "get_local_os_info": lambda args: get_local_os_info(),
    "check_local_network": lambda args: check_local_network(),
    "list_local_top_processes": lambda args: list_local_top_processes(args.get("limit", 5)),
    "get_local_docker_status": lambda args: get_local_docker_status(),
    "list_ec2_instances": lambda args: list_ec2_instances(),
    "get_ec2_cpu_metrics": lambda args: get_ec2_cpu_metrics(args.get("instance_id"), args.get("minutes", 30)),
    "check_infrastructure_health": lambda args: check_infrastructure_health(),

    "plan_diagnostics": lambda args: plan_diagnostics(args.get("question", "")).__dict__,
    "generate_technical_summary": lambda args: generate_technical_summary({}, [], []).__dict__,
    "search_memory": search_memory,
    "store_incident_memory": store_incident_memory,
    "record_feedback": record_feedback,
    "record_outcome": record_outcome,
}

# name -> {param_name: json_schema_type} for coercion
_PARAM_TYPES = {
    schema["function"]["name"]: {
        pname: pdef.get("type")
        for pname, pdef in schema["function"]["parameters"].get("properties", {}).items()
    }
    for schema in TOOL_SCHEMAS
}


def _coerce_value(value, json_type):
    """Best-effort coercion of a single value to the type the schema declares."""
    if value is None:
        return value
    try:
        if json_type == "integer" and not isinstance(value, int):
            return int(float(value))  # handles "60", "60.0", 60.0
        if json_type == "number" and not isinstance(value, (int, float)):
            return float(value)
        if json_type == "string" and not isinstance(value, str):
            return str(value)
        if json_type == "boolean" and not isinstance(value, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("true", "1", "yes")
            return bool(value)
    except (ValueError, TypeError):
        return value  # couldn't coerce — let the tool's own validation catch it
    return value


def coerce_arguments(tool_name: str, arguments: dict) -> dict:
    """Coerce each argument to the type declared in that tool's schema."""
    param_types = _PARAM_TYPES.get(tool_name, {})
    return {
        key: _coerce_value(value, param_types.get(key))
        for key, value in (arguments or {}).items()
    }


def execute_tool(name: str, arguments: dict) -> dict:
    """
    Execute a tool by name. Never raises — any failure (unknown tool, bad
    args, internal exception) comes back as a structured {"success": False,
    "error": ...} dict so the LLM gets something to reason about instead of
    the whole agent crashing.
    """
    if name not in _DISPATCH:
        return {"success": False, "error": f"Unknown tool: {name}"}
    try:
        coerced = coerce_arguments(name, arguments)
        return _DISPATCH[name](coerced)
    except Exception as e:
        return {"success": False, "error": f"Tool '{name}' failed: {e}"}