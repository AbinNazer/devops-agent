"""
Intelligent Planning engine for the Phase 5 control loop.

The planner:
1. Understands the user's request and determines what evidence is needed.
2. Avoids redundant tools — deduplicates by tool name.
3. Reuses existing observations from the state.
4. Prefers cheap/read-only diagnostics first.
5. Escalates only when evidence justifies it.
6. Stops once sufficient evidence exists.
7. Produces an explicit action plan if remediation is appropriate.

Key design: the planner does NOT solve problems by increasing iteration
limits. It uses deduplication, state tracking, evidence reuse, step
budgets, and stop conditions.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.control.state import ControlState, Phase


# Resource limits
MAX_CONTROL_STEPS = 15
MAX_DIAGNOSTIC_COMMANDS = 20
MAX_ACTIONS_PER_RUN = 3
MAX_VERIFICATION_ATTEMPTS = 3


@dataclass
class DiagnosticStep:
    """A single step in a diagnostic plan."""
    tool: str
    arguments: Dict = field(default_factory=dict)
    reason: str = ""
    priority: int = 0  # lower = higher priority (run first)


@dataclass
class DiagnosticPlan:
    """A complete diagnostic plan with ordered steps and stop conditions."""
    goal: str
    steps: List[DiagnosticStep] = field(default_factory=list)
    stop_conditions: List[str] = field(default_factory=list)
    estimated_commands: int = 0


# Keywords that map to specific diagnostic paths
_DIAGNOSTIC_PATHS = {
    "docker": {
        "tools": [
            DiagnosticStep("docker_health_status", reason="Check container states"),
            DiagnosticStep("docker_stats", reason="Check resource usage"),
        ],
        "keywords": ["docker", "container", "containers"],
    },
    "jenkins": {
        "tools": [
            DiagnosticStep("get_jenkins_status", reason="Check Jenkins container state"),
            DiagnosticStep("get_jenkins_logs", {"lines": 50}, reason="Check recent Jenkins logs"),
            DiagnosticStep("get_jenkins_stats", reason="Check Jenkins resource usage"),
        ],
        "keywords": ["jenkins", "ci", "ci/cd", "build"],
    },
    "k3s": {
        "tools": [
            DiagnosticStep("get_k3s_nodes", reason="Check cluster nodes"),
            DiagnosticStep("get_k3s_pods", reason="Check pod states"),
            DiagnosticStep("get_k3s_events", reason="Check recent cluster events"),
        ],
        "keywords": ["k3s", "kubernetes", "k8s", "pod", "cluster", "deploy"],
    },
    "server": {
        "tools": [
            DiagnosticStep("get_cpu_usage", reason="Check CPU load"),
            DiagnosticStep("get_memory_usage", reason="Check memory usage"),
            DiagnosticStep("get_disk_usage", reason="Check disk usage"),
            DiagnosticStep("get_uptime", reason="Check uptime"),
        ],
        "keywords": ["server", "vps", "host", "machine", "cpu", "memory", "disk"],
    },
    "network": {
        "tools": [
            DiagnosticStep("check_network_connectivity", reason="Check VPS reachability"),
        ],
        "keywords": ["network", "reachability", "ping", "connect"],
    },
    "process": {
        "tools": [
            DiagnosticStep("list_top_processes", {"limit": 10}, reason="Check top processes"),
        ],
        "keywords": ["process", "processes", "cpu", "load", "slow"],
    },
    "health": {
        "tools": [
            DiagnosticStep("check_infrastructure_health", reason="Full infrastructure health sweep"),
        ],
        "keywords": ["health", "everything", "all", "status", "ok", "fine", "check"],
    },
}

# Generic fallback: broad health check
_FALLBACK_PLAN = DiagnosticPlan(
    goal="General infrastructure health assessment",
    steps=[DiagnosticStep("check_infrastructure_health", reason="Full health sweep")],
    stop_conditions=["sufficient evidence collected"],
)


def _extract_keywords(text: str) -> Set[str]:
    """Extract meaningful keywords from a request."""
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    # Remove noise words
    words -= {"the", "and", "for", "are", "but", "not", "you", "all", "can",
              "had", "her", "was", "one", "our", "out", "has", "have", "been",
              "what", "when", "where", "how", "why", "does", "this", "that",
              "with", "from", "they", "been", "said", "each", "which", "their"}
    return words


def plan_diagnostic_steps(state: ControlState) -> DiagnosticPlan:
    """
    Create a diagnostic plan based on the user's request and current state.

    Avoids redundant tools by checking what's already been executed.
    Returns an ordered plan with stop conditions.
    """
    request = state.request.lower()
    keywords = _extract_keywords(request)

    # Find matching diagnostic paths
    matched_paths = []
    for path_name, path_info in _DIAGNOSTIC_PATHS.items():
        for keyword in path_info["keywords"]:
            if keyword in keywords:
                matched_paths.append(path_info)
                break

    # If no specific match, use health check as fallback
    if not matched_paths:
        matched_paths = [_DIAGNOSTIC_PATHS["health"]]

    # Collect all steps from matched paths
    all_steps = []
    for path_info in matched_paths:
        all_steps.extend(path_info["tools"])

    # Deduplicate: remove tools already executed in this state
    already_done = set(state.executed_tools)
    unique_steps = []
    seen = set()
    for step in all_steps:
        if step.tool not in already_done and step.tool not in seen:
            unique_steps.append(step)
            seen.add(step.tool)

    # If everything's already been done, we have enough evidence
    if not unique_steps:
        return DiagnosticPlan(
            goal=f"Evidence already collected for: {state.request}",
            steps=[],
            stop_conditions=["all planned diagnostics already executed"],
            estimated_commands=0,
        )

    # Sort by priority (lower = higher priority)
    unique_steps.sort(key=lambda s: s.priority)

    # Add container-specific follow-ups if docker health shows problems
    if "docker_health_status" in seen:
        # The health check already ran; specific log/inspect can be added
        # only if evidence suggests it
        pass

    plan = DiagnosticPlan(
        goal=f"Diagnose: {state.request}",
        steps=unique_steps,
        stop_conditions=[
            "sufficient evidence collected",
            f"max {MAX_DIAGNOSTIC_COMMANDS} diagnostic commands reached",
            "root cause identified with high confidence",
        ],
        estimated_commands=len(unique_steps),
    )

    return plan


def should_stop_evidence_collection(state: ControlState) -> bool:
    """
    Determine if we have sufficient evidence to proceed to planning.

    Stop conditions:
    - Max diagnostic commands reached
    - Enough observations to form a hypothesis
    - High-confidence hypothesis selected
    """
    if state.diagnostic_command_count >= MAX_DIAGNOSTIC_COMMANDS:
        return True

    # If we have observations and at least one high-confidence hypothesis
    if (len(state.observations) >= 2 and
            state.selected_hypothesis and
            state.confidence >= 0.7):
        return True

    # If we've run a comprehensive health check
    if "check_infrastructure_health" in state.executed_tools:
        return True

    return False


def can_take_action(state: ControlState) -> bool:
    """Determine if we have enough evidence to propose an action."""
    if state.action_count >= MAX_ACTIONS_PER_RUN:
        return False

    # Need at least one hypothesis with some confidence
    if not state.selected_hypothesis:
        return False

    if state.confidence < 0.5:
        return False

    return True
