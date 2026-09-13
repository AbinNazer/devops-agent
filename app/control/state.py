"""
Structured control state for the Phase 5 control loop.

Tracks the entire lifecycle of a diagnostic/action workflow from initial
request through evidence collection, hypothesis generation, action planning,
approval, execution, verification, and outcome recording.

Every step is auditable through the state's history trail.
"""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Phase(str, Enum):
    """Control loop phases in execution order."""
    IDLE = "idle"
    OBSERVE = "observe"
    BUILD_STATE = "build_state"
    RETRIEVE_MEMORY = "retrieve_memory"
    GENERATE_HYPOTHESES = "generate_hypotheses"
    CREATE_PLAN = "create_plan"
    RISK_ASSESSMENT = "risk_assessment"
    APPROVAL = "approval"
    EXECUTE = "execute"
    VERIFY = "verify"
    ROLLBACK = "rollback"
    RECORD_OUTCOME = "record_outcome"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    BLOCKED = "blocked"
    AUTO_APPROVED = "auto_approved"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class OutcomeStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    DEGRADED = "degraded"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass
class Observation:
    """A single observation from evidence collection."""
    tool: str
    result: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    source: str = "tool"  # tool, memory, user

    def to_dict(self) -> dict:
        return {"tool": self.tool, "result": self.result, "timestamp": self.timestamp, "source": self.source}


@dataclass
class ActionRecord:
    """Record of an action that was planned or executed."""
    action_type: str
    target: str
    command: str = ""
    risk_level: str = ""
    approval_status: str = ""
    approved_by: str = ""
    result: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None
    rollback: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "command": self.command,
            "risk_level": self.risk_level,
            "approval_status": self.approval_status,
            "result": self.result,
            "verification": self.verification,
            "rollback": self.rollback,
            "timestamp": self.timestamp,
        }


@dataclass
class AuditEntry:
    """Single audit trail entry."""
    phase: str
    action: str
    detail: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"phase": self.phase, "action": self.action, "detail": self.detail, "timestamp": self.timestamp}


@dataclass
class ControlState:
    """
    Complete structured state for one control loop iteration.

    Every field is auditable. The history list provides a chronological
    trail of everything that happened during this control cycle.
    """
    # Request context
    request: str = ""
    environment: str = ""
    target: str = ""
    incident_id: str = field(default_factory=lambda: f"INC-{int(time.time())}-{uuid.uuid4().hex[:6]}")

    # Current phase tracking
    current_phase: str = Phase.IDLE.value
    phase_history: List[str] = field(default_factory=list)

    # Evidence
    observations: List[Observation] = field(default_factory=list)
    executed_tools: List[str] = field(default_factory=list)
    executed_commands: List[str] = field(default_factory=list)

    # Hypotheses
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    selected_hypothesis: Optional[Dict[str, Any]] = None
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0

    # Planning
    action_plan: List[Dict[str, Any]] = field(default_factory=list)

    # Risk
    risk_level: str = RiskLevel.LOW.value

    # Approval
    approval_status: str = ApprovalStatus.PENDING.value
    approval_details: Optional[Dict[str, Any]] = None

    # Execution
    actions_attempted: List[ActionRecord] = field(default_factory=list)
    action_results: List[Dict[str, Any]] = field(default_factory=list)

    # Verification
    verification_results: List[Dict[str, Any]] = field(default_factory=list)

    # Rollback
    rollback_status: str = ""
    rollback_results: List[Dict[str, Any]] = field(default_factory=list)

    # Outcome
    final_outcome: str = OutcomeStatus.UNKNOWN.value
    outcome_summary: str = ""

    # Memory
    memory_references: List[str] = field(default_factory=list)
    memory_stored: List[str] = field(default_factory=list)

    # Audit trail
    audit_trail: List[AuditEntry] = field(default_factory=list)

    # Counters for resource safety
    diagnostic_command_count: int = 0
    action_count: int = 0

    def transition_to(self, phase: Phase, detail: str = "") -> None:
        """Record a phase transition."""
        self.current_phase = phase.value
        self.phase_history.append(phase.value)
        self.audit_trail.append(AuditEntry(
            phase=phase.value, action="phase_transition", detail=detail,
        ))

    def add_observation(self, tool: str, result: Dict[str, Any], source: str = "tool") -> None:
        """Record an observation from evidence collection."""
        obs = Observation(tool=tool, result=result, source=source)
        self.observations.append(obs)
        if tool not in self.executed_tools:
            self.executed_tools.append(tool)
        self.audit_trail.append(AuditEntry(
            phase=self.current_phase, action="observation",
            detail=f"tool={tool} success={result.get('success', False)}",
        ))

    def add_hypothesis(self, hypothesis: Dict[str, Any]) -> None:
        """Add a hypothesis to the state."""
        self.hypotheses.append(hypothesis)
        self.audit_trail.append(AuditEntry(
            phase=self.current_phase, action="hypothesis_added",
            detail=hypothesis.get("statement", "")[:100],
        ))

    def select_hypothesis(self, hypothesis: Dict[str, Any]) -> None:
        """Select the most likely hypothesis."""
        self.selected_hypothesis = hypothesis
        self.confidence = hypothesis.get("confidence", 0.0)
        self.audit_trail.append(AuditEntry(
            phase=self.current_phase, action="hypothesis_selected",
            detail=f"confidence={self.confidence}",
        ))

    def add_action_record(self, record: ActionRecord) -> None:
        """Record an action attempt."""
        self.actions_attempted.append(record)
        self.audit_trail.append(AuditEntry(
            phase=self.current_phase, action="action_recorded",
            detail=f"type={record.action_type} target={record.target} risk={record.risk_level}",
        ))

    def add_verification(self, result: Dict[str, Any]) -> None:
        """Record a verification result."""
        self.verification_results.append(result)
        self.audit_trail.append(AuditEntry(
            phase=self.current_phase, action="verification",
            detail=f"status={result.get('status', 'unknown')}",
        ))

    def set_outcome(self, status: OutcomeStatus, summary: str = "") -> None:
        """Record the final outcome."""
        self.final_outcome = status.value
        self.outcome_summary = summary
        self.audit_trail.append(AuditEntry(
            phase=Phase.RECORD_OUTCOME.value, action="outcome_set",
            detail=f"status={status.value} summary={summary[:200]}",
        ))

    def to_dict(self) -> dict:
        """Serialize the full state for storage or inspection."""
        return {
            "incident_id": self.incident_id,
            "request": self.request,
            "environment": self.environment,
            "target": self.target,
            "current_phase": self.current_phase,
            "phase_history": self.phase_history,
            "observations": [o.to_dict() for o in self.observations],
            "executed_tools": self.executed_tools,
            "executed_commands": self.executed_commands,
            "hypotheses": self.hypotheses,
            "selected_hypothesis": self.selected_hypothesis,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "action_plan": self.action_plan,
            "risk_level": self.risk_level,
            "approval_status": self.approval_status,
            "actions_attempted": [a.to_dict() for a in self.actions_attempted],
            "action_results": self.action_results,
            "verification_results": self.verification_results,
            "rollback_status": self.rollback_status,
            "rollback_results": self.rollback_results,
            "final_outcome": self.final_outcome,
            "outcome_summary": self.outcome_summary,
            "memory_references": self.memory_references,
            "diagnostic_command_count": self.diagnostic_command_count,
            "action_count": self.action_count,
            "audit_trail": [e.to_dict() for e in self.audit_trail],
        }

    def get_summary(self) -> str:
        """Human-readable summary of the control state."""
        lines = [
            f"Incident: {self.incident_id}",
            f"Request: {self.request}",
            f"Phase: {self.current_phase}",
            f"Observations: {len(self.observations)}",
            f"Hypotheses: {len(self.hypotheses)}",
            f"Actions: {len(self.actions_attempted)}",
            f"Outcome: {self.final_outcome}",
        ]
        if self.selected_hypothesis:
            lines.append(f"Selected hypothesis: {self.selected_hypothesis.get('statement', 'N/A')}")
            lines.append(f"Confidence: {self.confidence:.0%}")
        return "\n".join(lines)
