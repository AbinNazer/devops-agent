"""
Incident Tracking for Phase 5.

Creates structured incident records that integrate with the Phase 4 memory
system. Every diagnostic/action workflow produces an incident record
with a full timeline, evidence, hypotheses, actions, approvals, and outcome.
"""
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TimelineEntry:
    """A single event in the incident timeline."""
    timestamp: float
    event: str
    detail: str = ""
    phase: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "detail": self.detail,
            "phase": self.phase,
        }


@dataclass
class Incident:
    """Structured incident record for a diagnostic/action workflow."""
    id: str = field(default_factory=lambda: f"INC-{int(time.time())}-{uuid.uuid4().hex[:6]}")
    title: str = ""
    description: str = ""
    severity: str = "unknown"
    status: str = "open"

    # Timeline
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    timeline: List[TimelineEntry] = field(default_factory=list)

    # Affected components
    affected_components: List[str] = field(default_factory=list)
    environment: str = ""

    # Evidence
    evidence: List[str] = field(default_factory=list)
    observations: List[Dict] = field(default_factory=list)

    # Hypotheses
    hypotheses: List[Dict] = field(default_factory=list)
    selected_hypothesis: Optional[Dict] = None
    confidence: float = 0.0

    # Actions
    actions: List[Dict] = field(default_factory=list)
    approvals: List[Dict] = field(default_factory=list)

    # Verification
    verification_results: List[Dict] = field(default_factory=list)

    # Outcome
    outcome: str = "unknown"
    outcome_summary: str = ""

    # Memory integration
    memory_id: Optional[str] = None

    def add_timeline_entry(self, event: str, detail: str = "", phase: str = "") -> None:
        """Add an entry to the incident timeline."""
        self.timeline.append(TimelineEntry(
            timestamp=time.time(), event=event, detail=detail, phase=phase,
        ))

    def close(self, outcome: str, summary: str = "") -> None:
        """Close the incident with an outcome."""
        self.end_time = time.time()
        self.status = "closed"
        self.outcome = outcome
        self.outcome_summary = summary
        self.add_timeline_event("incident_closed", f"Outcome: {outcome}")

    def add_timeline_event(self, event: str, detail: str = "", phase: str = "") -> None:
        """Alias for add_timeline_entry for clarity."""
        self.add_timeline_entry(event, detail, phase)

    def get_duration(self) -> Optional[float]:
        """Get the incident duration in seconds."""
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> dict:
        """Serialize the incident for storage."""
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "severity": self.severity,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.get_duration(),
            "timeline": [e.to_dict() for e in self.timeline],
            "affected_components": self.affected_components,
            "environment": self.environment,
            "evidence": self.evidence,
            "observations": self.observations,
            "hypotheses": self.hypotheses,
            "selected_hypothesis": self.selected_hypothesis,
            "confidence": self.confidence,
            "actions": self.actions,
            "approvals": self.approvals,
            "verification_results": self.verification_results,
            "outcome": self.outcome,
            "outcome_summary": self.outcome_summary,
            "memory_id": self.memory_id,
        }

    def to_memory_content(self) -> str:
        """Format incident as memory content for Phase 4 integration."""
        lines = [
            f"Incident: {self.id}",
            f"Title: {self.title}",
            f"Severity: {self.severity}",
            f"Outcome: {self.outcome}",
            "",
            "Timeline:",
        ]
        for entry in self.timeline:
            ts = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            lines.append(f"  {ts} [{entry.phase}] {entry.event}: {entry.detail}")

        if self.selected_hypothesis:
            lines.append("")
            lines.append(f"Hypothesis: {self.selected_hypothesis.get('statement', 'N/A')}")
            lines.append(f"Confidence: {self.confidence:.0%}")

        if self.evidence:
            lines.append("")
            lines.append("Evidence:")
            for e in self.evidence:
                lines.append(f"  - {e}")

        if self.outcome_summary:
            lines.append("")
            lines.append(f"Summary: {self.outcome_summary}")

        return "\n".join(lines)


def create_incident(title: str, description: str = "", severity: str = "unknown",
                    environment: str = "", components: Optional[List[str]] = None) -> Incident:
    """Create a new incident."""
    incident = Incident(
        title=title,
        description=description,
        severity=severity,
        environment=environment,
        affected_components=components or [],
    )
    incident.add_timeline_entry("incident_created", f"Title: {title}")
    return incident
