from dataclasses import dataclass
@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    risk_level: str
CAPABILITIES = (Capability("system.cpu", "Read CPU/load", "READ"), Capability("system.memory", "Read memory", "READ"), Capability("system.disk", "Read disk", "READ"), Capability("docker.list_containers", "List containers", "READ"), Capability("docker.container_logs", "Read bounded logs", "READ"), Capability("docker.restart_container", "Restart an approved container", "MEDIUM"))
def advertised_capabilities(): return [item.name for item in CAPABILITIES]