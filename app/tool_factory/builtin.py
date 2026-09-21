"""Built-in read-only tool definitions registered through the Tool Factory.

These mirror capabilities that already exist as direct JARVIS tools, but
expose them through the governed factory lifecycle (validation, versions,
RBAC, whitelist gate, audit). Mutating capabilities (docker restart, etc.)
are deliberately NOT here — they remain in the Phase 5 controlled pipeline.
"""
from __future__ import annotations

import logging

from app.tool_factory.service import get_tool_factory_service, ToolFactoryService
from app.tool_factory.validation import ToolValidationError

logger = logging.getLogger("tool_factory.builtin")

# org_default matches tenancy.registry's default organization id.
DEFAULT_ORG = "org_default"

BUILTIN_TOOLS: list[dict] = [
    {
        "name": "factory_docker_status",
        "display_name": "Docker Status",
        "description": "List all containers on the host, including stopped ones, with their status.",
        "category": "docker",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "docker ps -a",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 10,
        "max_output_bytes": 16384,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_docker_inspect",
        "display_name": "Docker Inspect",
        "description": "Detailed configuration and state info for a specific container.",
        "category": "docker",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "docker inspect {container}",
        "input_schema": {"type": "object", "properties": {"container": {"type": "string", "pattern": "^[\\w.\\-]{1,128}$"}}, "required": ["container"]},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 10,
        "max_output_bytes": 32768,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_docker_logs",
        "display_name": "Docker Logs",
        "description": "Recent log lines for a specific container.",
        "category": "docker",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "docker logs {container} --tail {n}",
        "input_schema": {"type": "object", "properties": {"container": {"type": "string", "pattern": "^[\\w.\\-]{1,128}$"}, "n": {"type": "integer", "minimum": 1, "maximum": 1000}}, "required": ["container", "n"]},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 15,
        "max_output_bytes": 32768,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_disk_usage",
        "display_name": "Disk Usage",
        "description": "Human-readable disk usage for the host filesystems.",
        "category": "system",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "df -h",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 10,
        "max_output_bytes": 8192,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_memory_usage",
        "display_name": "Memory Usage",
        "description": "Human-readable memory usage on the host.",
        "category": "system",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "free -h",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 10,
        "max_output_bytes": 4096,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_top_processes",
        "display_name": "Top Processes",
        "description": "Processes on the host sorted by CPU usage.",
        "category": "system",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "ps aux --sort=-%cpu",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 10,
        "max_output_bytes": 16384,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_service_status",
        "display_name": "Service Status",
        "description": "systemd status of a named service (read-only status, never start/stop).",
        "category": "system",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "systemctl status {service}",
        "input_schema": {"type": "object", "properties": {"service": {"type": "string", "pattern": "^[\\w.\\-]{1,128}$"}}, "required": ["service"]},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 10,
        "max_output_bytes": 8192,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_k8s_pods",
        "display_name": "Kubernetes Pods",
        "description": "List all Kubernetes pods across namespaces (read-only get).",
        "category": "kubernetes",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "kubectl get pods -A",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 15,
        "max_output_bytes": 32768,
        "required_permission": "infrastructure.read",
    },
    {
        "name": "factory_grep_root",
        "display_name": "Grep Allowed Root",
        "description": "Bounded grep under an allowed search root; secrets redacted. Requires JARVIS_SEARCH_ALLOWED_ROOTS.",
        "category": "search",
        "version": "1.0.0",
        "execution_mode": "both",
        "command_template": "grep -rIn -m 50 -e {query} /var/www",
        "allowed_paths": ["/var/www"],
        "input_schema": {"type": "object", "properties": {"query": {"type": "string", "pattern": "^[\\w.\\-@/:+ ]{1,120}$"}}, "required": ["query"]},
        "output_schema": {"type": "object", "properties": {"output": {"type": "string"}}},
        "timeout_seconds": 15,
        "max_output_bytes": 32768,
        "required_permission": "infrastructure.read",
    },
]


def ensure_builtin_tools(service: ToolFactoryService | None = None) -> dict:
    """Register built-in definitions as approved-but-inactive drafts.

    Definitions that already exist (by name) are left untouched so user
    state is never clobbered. Returns a summary dict.
    """
    service = service or get_tool_factory_service()
    created, skipped = [], []
    for payload in BUILTIN_TOOLS:
        payload = {**payload, "author": "builtin", "provenance": "builtin", "read_only": True}
        existing = service.repository.get_definition(DEFAULT_ORG, payload["name"])
        if existing is not None:
            skipped.append(payload["name"])
            continue
        try:
            definition = service.create_tool(DEFAULT_ORG, payload, actor="builtin")
            # Built-ins are pre-approved by the shipped code but still start
            # INACTIVE: an explicit activate step by an authorized user makes
            # them executable (defence in depth for the audit trail).
            service.approve_tool_definition(DEFAULT_ORG, definition.name, actor="builtin")
            created.append(definition.name)
        except ToolValidationError as exc:
            logger.error("builtin_tool_rejected tool=%s issues=%s", payload["name"], exc.issues)
            skipped.append(payload["name"])
    logger.info("builtin_tools_registered created=%s skipped=%s", created, skipped)
    return {"created": created, "skipped": skipped}
