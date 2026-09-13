"""
Deterministic event correlation for Phase 6.

Correlates related anomalies into single incidents rather than
creating separate incidents for each.

Examples:
- container memory spike + container restart + OOM log → one correlated incident
- high host memory + multiple container memory spikes → one correlated incident

Correlation is deterministic and explainable.
"""
import time
from typing import Dict, List, Optional

from app.monitoring.models import Anomaly, Incident, IncidentState
from app.monitoring.incidents import IncidentStore, update_incident_with_anomaly
from app.monitoring.thresholds import ThresholdConfig


# Correlation rules: maps anomaly_type → set of types that should be merged
_CORRELATION_RULES = {
    "container_memory_spike": {"container_restart_loop", "oom_detected", "crash_indicator"},
    "container_restart_loop": {"container_memory_spike", "oom_detected", "container_unhealthy"},
    "oom_detected": {"container_memory_spike", "container_restart_loop", "crash_indicator"},
    "crash_indicator": {"oom_detected", "container_restart_loop"},
    "memory_high": {"oom_detected", "container_memory_spike"},
    "container_cpu_spike": {"container_restart_loop"},
    "service_failed": {"crash_indicator"},
}


def get_correlated_types(anomaly_type: str) -> set:
    """Return the set of anomaly types that should be correlated with this one."""
    return _CORRELATION_RULES.get(anomaly_type, set())


def are_correlated(a1: Anomaly, a2: Anomaly, window_seconds: float = 600.0) -> bool:
    """Check if two anomalies are eligible for correlation."""
    # Must be same host
    if a1.host != a2.host:
        return False

    # Time window check FIRST — outside the window, nothing correlates
    if abs(a1.timestamp - a2.timestamp) > window_seconds:
        return False

    # Same component
    if a1.component == a2.component:
        # Check if either type correlates with the other
        if a2.anomaly_type in get_correlated_types(a1.anomaly_type):
            return True
        if a1.anomaly_type in get_correlated_types(a2.anomaly_type):
            return True

    # Host-level correlation: memory_high on host correlates with container_memory_spike
    if a1.component_type == "system" and a2.component_type == "docker":
        if a1.anomaly_type == "memory_high" and a2.anomaly_type in ("container_memory_spike", "oom_detected"):
            return True
    if a2.component_type == "system" and a1.component_type == "docker":
        if a2.anomaly_type == "memory_high" and a1.anomaly_type in ("container_memory_spike", "oom_detected"):
            return True

    return False


def correlate_anomalies(anomalies: List[Anomaly], store: IncidentStore,
                        config: Optional[ThresholdConfig] = None) -> List[Incident]:
    """
    Correlate a batch of anomalies and attach them to appropriate incidents.

    Returns list of incidents that were created or updated.
    """
    config = config or ThresholdConfig()
    affected_incidents = []

    for anomaly in anomalies:
        # Try to find an existing incident to correlate with
        correlated = False
        for incident in store.get_active():
            if incident.state in (IncidentState.CLOSED.value, IncidentState.SUPPRESSED.value):
                continue
            # Check if this anomaly type correlates with the incident's type
            if anomaly.anomaly_type in get_correlated_types(incident.anomaly_type):
                if abs(anomaly.timestamp - incident.created_at) < config.max_correlated_window_seconds:
                    if anomaly.host == incident.host:
                        update_incident_with_anomaly(incident, anomaly)
                        incident.add_timeline("correlated",
                            f"Correlated with {incident.anomaly_type}: {anomaly.anomaly_type}")
                        correlated = True
                        if incident not in affected_incidents:
                            affected_incidents.append(incident)
                        break

            # Also check if existing incident's type correlates with this one
            if incident.anomaly_type in get_correlated_types(anomaly.anomaly_type):
                if abs(anomaly.timestamp - incident.created_at) < config.max_correlated_window_seconds:
                    if anomaly.host == incident.host:
                        update_incident_with_anomaly(incident, anomaly)
                        incident.add_timeline("correlated",
                            f"Correlated {anomaly.anomaly_type} with existing {incident.anomaly_type}")
                        correlated = True
                        if incident not in affected_incidents:
                            affected_incidents.append(incident)
                        break

        if not correlated and anomaly.severity == "critical":
            # Critical anomalies that can't be correlated become new incidents
            from app.monitoring.deduplication import find_or_create_incident
            incident, is_new = find_or_create_incident(anomaly, store, config)
            if is_new or incident not in affected_incidents:
                affected_incidents.append(incident)

    return affected_incidents
