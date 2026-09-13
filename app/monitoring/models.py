"""
Monitoring data models for Phase 6.

Strongly typed models for metrics, events, anomalies, and incidents.
All timestamps are float (time.time() output) for consistency.
"""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Enums ──

class MetricKind(str, Enum):
    GAUGE = "gauge"
    COUNTER = "counter"
    RATE = "rate"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AnomalyStatus(str, Enum):
    DETECTED = "detected"
    CONFIRMED = "confirmed"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class IncidentState(str, Enum):
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    CONFIRMED = "confirmed"
    MITIGATING = "mitigating"
    RESOLVED = "resolved"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    CLOSED = "closed"


class ComponentType(str, Enum):
    SYSTEM = "system"
    DOCKER = "docker"
    SERVICE = "service"
    LOG = "log"
    NETWORK = "network"
    UNKNOWN = "unknown"


# ── Metric models ──

@dataclass
class Metric:
    """Definition of a metric being monitored."""
    name: str
    kind: str = MetricKind.GAUGE.value
    unit: str = ""
    description: str = ""
    component_type: str = ComponentType.SYSTEM.value

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "unit": self.unit,
                "description": self.description, "component_type": self.component_type}


@dataclass
class MetricSnapshot:
    """A single measurement of a metric at a point in time."""
    metric_name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    host: str = ""
    component: str = ""
    labels: Dict[str, str] = field(default_factory=dict)
    unit: str = ""

    def to_dict(self) -> dict:
        return {"metric_name": self.metric_name, "value": self.value,
                "timestamp": self.timestamp, "host": self.host,
                "component": self.component, "labels": self.labels, "unit": self.unit}


@dataclass
class MetricSeries:
    """A time-series of snapshots for one metric on one host/component."""
    metric_name: str
    host: str = ""
    component: str = ""
    snapshots: List[MetricSnapshot] = field(default_factory=list)

    @property
    def latest(self) -> Optional[MetricSnapshot]:
        return self.snapshots[-1] if self.snapshots else None

    @property
    def values(self) -> List[float]:
        return [s.value for s in self.snapshots]

    def to_dict(self) -> dict:
        return {"metric_name": self.metric_name, "host": self.host,
                "component": self.component, "count": len(self.snapshots),
                "latest": self.latest.to_dict() if self.latest else None}


# ── Event / Anomaly models ──

@dataclass
class MonitoringEvent:
    """A raw monitoring event detected by the engine."""
    id: str = field(default_factory=lambda: f"evt-{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)
    source: str = ""  # collector name
    host: str = ""
    component: str = ""
    component_type: str = ComponentType.UNKNOWN.value
    event_type: str = ""  # e.g. "container_stopped", "cpu_threshold", "log_error_spike"
    severity: str = Severity.INFO.value
    message: str = ""
    evidence: List[str] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    raw_data: Optional[Dict] = None

    def to_dict(self) -> dict:
        return {"id": self.id, "timestamp": self.timestamp, "source": self.source,
                "host": self.host, "component": self.component,
                "component_type": self.component_type, "event_type": self.event_type,
                "severity": self.severity, "message": self.message,
                "evidence": self.evidence, "labels": self.labels}


@dataclass
class Anomaly:
    """A confirmed anomaly derived from one or more monitoring events."""
    id: str = field(default_factory=lambda: f"anom-{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time.time)
    host: str = ""
    component: str = ""
    component_type: str = ComponentType.UNKNOWN.value
    anomaly_type: str = ""  # e.g. "cpu_high", "container_restarting", "oom_detected"
    metric_name: str = ""
    severity: str = Severity.WARNING.value
    status: str = AnomalyStatus.DETECTED.value
    current_value: float = 0.0
    threshold_value: float = 0.0
    message: str = ""
    evidence: List[str] = field(default_factory=list)
    event_ids: List[str] = field(default_factory=list)
    confidence: float = 0.8
    context: Dict[str, Any] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Stable fingerprint for deduplication (no timestamps)."""
        parts = [self.host, self.component, self.anomaly_type, self.metric_name]
        return "|".join(parts)

    def to_dict(self) -> dict:
        return {"id": self.id, "timestamp": self.timestamp, "host": self.host,
                "component": self.component, "component_type": self.component_type,
                "anomaly_type": self.anomaly_type, "metric_name": self.metric_name,
                "severity": self.severity, "status": self.status,
                "current_value": self.current_value, "threshold_value": self.threshold_value,
                "message": self.message, "evidence": self.evidence,
                "confidence": self.confidence, "fingerprint": self.fingerprint()}


# ── Incident models ──

@dataclass
class IncidentTimelineEvent:
    """A single event in an incident timeline."""
    timestamp: float = field(default_factory=time.time)
    event: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "event": self.event, "detail": self.detail}


@dataclass
class Incident:
    """A tracked monitoring incident with full lifecycle."""
    id: str = field(default_factory=lambda: f"MON-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None

    state: str = IncidentState.DETECTED.value
    severity: str = Severity.WARNING.value
    host: str = ""
    component: str = ""
    component_type: str = ComponentType.UNKNOWN.value

    # Detection
    anomaly_type: str = ""
    initial_event_id: str = ""
    detection_reason: str = ""

    # Evidence
    evidence: List[str] = field(default_factory=list)
    anomaly_ids: List[str] = field(default_factory=list)
    event_ids: List[str] = field(default_factory=list)

    # Timeline
    timeline: List[IncidentTimelineEvent] = field(default_factory=list)

    # Correlation
    related_incident_ids: List[str] = field(default_factory=list)

    # Resolution
    resolution: str = ""
    actions_taken: List[Dict] = field(default_factory=list)
    verification_result: Optional[Dict] = None

    # Dedup fingerprint
    fingerprint: str = ""

    def add_timeline(self, event: str, detail: str = "") -> None:
        self.timeline.append(IncidentTimelineEvent(event=event, detail=detail))
        self.updated_at = time.time()

    def close(self, resolution: str = "") -> None:
        self.state = IncidentState.CLOSED.value
        self.closed_at = time.time()
        self.resolution = resolution
        self.add_timeline("closed", resolution)

    @property
    def age_seconds(self) -> float:
        if self.closed_at:
            return self.closed_at - self.created_at
        return time.time() - self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id, "created_at": self.created_at, "updated_at": self.updated_at,
            "closed_at": self.closed_at, "state": self.state, "severity": self.severity,
            "host": self.host, "component": self.component,
            "component_type": self.component_type, "anomaly_type": self.anomaly_type,
            "detection_reason": self.detection_reason, "evidence": self.evidence,
            "anomaly_ids": self.anomaly_ids, "event_ids": self.event_ids,
            "timeline": [e.to_dict() for e in self.timeline],
            "related_incident_ids": self.related_incident_ids,
            "resolution": self.resolution, "fingerprint": self.fingerprint,
        }


@dataclass
class DetectionResult:
    """Result of a detection cycle for a single component."""
    timestamp: float = field(default_factory=time.time)
    host: str = ""
    component: str = ""
    events: List[MonitoringEvent] = field(default_factory=list)
    anomalies: List[Anomaly] = field(default_factory=list)
    incidents_created: List[str] = field(default_factory=list)
    incidents_updated: List[str] = field(default_factory=list)
    incidents_deduplicated: int = 0
    errors: List[str] = field(default_factory=list)

    @property
    def has_anomalies(self) -> bool:
        return len(self.anomalies) > 0

    def to_dict(self) -> dict:
        return {"timestamp": self.timestamp, "host": self.host, "component": self.component,
                "events": len(self.events), "anomalies": len(self.anomalies),
                "incidents_created": self.incidents_created,
                "incidents_updated": self.incidents_updated,
                "incidents_deduplicated": self.incidents_deduplicated,
                "errors": self.errors}
