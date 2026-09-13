"""
Incident lifecycle management for Phase 6.

Creates and manages MonitoringIncidents from detected anomalies.
Every incident has an auditable timeline.
"""
import time
import uuid
from typing import Dict, List, Optional

from app.monitoring.models import (
    Anomaly, Incident, IncidentState, Severity,
)


def create_incident_from_anomaly(anomaly: Anomaly) -> Incident:
    """Create a new Incident from a detected anomaly."""
    incident = Incident(
        host=anomaly.host,
        component=anomaly.component,
        component_type=anomaly.component_type,
        anomaly_type=anomaly.anomaly_type,
        severity=anomaly.severity,
        detection_reason=anomaly.message,
        fingerprint=anomaly.fingerprint(),
        initial_event_id=anomaly.id,
    )
    incident.add_timeline("detected", anomaly.message)
    incident.add_timeline("anomaly_confirmed", f"Severity: {anomaly.severity}")
    incident.evidence = list(anomaly.evidence)
    incident.anomaly_ids = [anomaly.id]
    return incident


def update_incident_with_anomaly(incident: Incident, anomaly: Anomaly) -> None:
    """Add new anomaly evidence to an existing incident."""
    incident.anomaly_ids.append(anomaly.id)
    incident.evidence.extend(anomaly.evidence)
    # Escalate severity if new anomaly is worse
    severity_order = {"info": 0, "warning": 1, "critical": 2}
    if severity_order.get(anomaly.severity, 0) > severity_order.get(incident.severity, 0):
        old = incident.severity
        incident.severity = anomaly.severity
        incident.add_timeline("severity_escalated", f"{old} → {anomaly.severity}")
    incident.add_timeline("anomaly_added", anomaly.message)
    incident.updated_at = time.time()


def transition_incident(incident: Incident, new_state: str, detail: str = "") -> None:
    """Transition an incident to a new state with timeline entry."""
    old_state = incident.state
    incident.state = new_state
    incident.add_timeline(new_state, detail or f"Transitioned from {old_state}")
    incident.updated_at = time.time()


def resolve_incident(incident: Incident, resolution: str = "") -> None:
    """Mark an incident as resolved."""
    incident.state = IncidentState.RESOLVED.value
    incident.resolution = resolution
    incident.add_timeline("resolved", resolution)
    incident.closed_at = time.time()
    incident.updated_at = time.time()


def suppress_incident(incident: Incident, reason: str = "") -> None:
    """Suppress an incident (e.g., intentionally stopped container)."""
    incident.state = IncidentState.SUPPRESSED.value
    incident.add_timeline("suppressed", reason)
    incident.updated_at = time.time()


class IncidentStore:
    """In-memory store for active and recent incidents."""

    def __init__(self, max_active: int = 10, max_history: int = 100):
        self._active: Dict[str, Incident] = {}
        self._history: List[Incident] = []
        self._max_active = max_active
        self._max_history = max_history

    def add(self, incident: Incident) -> None:
        """Add a new incident to the store."""
        self._active[incident.id] = incident

    def get(self, incident_id: str) -> Optional[Incident]:
        """Get an incident by ID."""
        return self._active.get(incident_id) or next(
            (i for i in self._history if i.id == incident_id), None)

    def get_active(self) -> List[Incident]:
        """Get all active (non-closed) incidents."""
        return list(self._active.values())

    def get_by_fingerprint(self, fingerprint: str) -> Optional[Incident]:
        """Find an active incident matching a fingerprint."""
        for inc in self._active.values():
            if inc.fingerprint == fingerprint and inc.state not in (
                IncidentState.CLOSED.value, IncidentState.RESOLVED.value,
                IncidentState.SUPPRESSED.value):
                return inc
        return None

    def close(self, incident_id: str, resolution: str = "") -> Optional[Incident]:
        """Close an incident and move to history."""
        incident = self._active.pop(incident_id, None)
        if incident:
            resolve_incident(incident, resolution)
            self._add_to_history(incident)
        return incident

    def _add_to_history(self, incident: Incident) -> None:
        """Add to history, evicting oldest if at capacity."""
        self._history.append(incident)
        while len(self._history) > self._max_history:
            self._history.pop(0)

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def total_incidents(self) -> int:
        return len(self._active) + len(self._history)
