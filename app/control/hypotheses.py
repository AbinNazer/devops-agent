"""
Structured Hypothesis Engine for Phase 5.

Maintains multiple hypotheses about infrastructure problems, tracks
evidence for and against each, and ranks them by grounded confidence.

Confidence is NOT invented by the LLM — it is computed from the
evidence count and consistency.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    WEAK = "weak"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass
class Hypothesis:
    """A structured hypothesis about an infrastructure issue."""
    id: str
    statement: str
    status: str = HypothesisStatus.PROPOSED.value
    evidence_for: List[str] = field(default_factory=list)
    evidence_against: List[str] = field(default_factory=list)
    confidence: float = 0.0
    related_components: List[str] = field(default_factory=list)
    source: str = "llm"  # llm, memory, correlation

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "statement": self.statement,
            "status": self.status,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "confidence": self.confidence,
            "related_components": self.related_components,
            "source": self.source,
        }


def compute_confidence(hypothesis: Hypothesis) -> float:
    """
    Compute confidence for a hypothesis based on its evidence.

    This grounds confidence in actual evidence rather than LLM invention.

    Formula:
    - Base: 0.1 (always some uncertainty)
    - Each piece of evidence FOR: +0.15
    - Each piece of evidence AGAINST: -0.2
    - Bonus if > 2 supporting evidence: +0.1
    - Clamp to [0.0, 1.0]
    """
    confidence = 0.1
    confidence += len(hypothesis.evidence_for) * 0.15
    confidence -= len(hypothesis.evidence_against) * 0.2

    if len(hypothesis.evidence_for) > 2:
        confidence += 0.1

    return max(0.0, min(1.0, confidence))


def update_hypothesis_status(hypothesis: Hypothesis) -> Hypothesis:
    """Update a hypothesis's status and confidence based on its evidence."""
    hypothesis.confidence = compute_confidence(hypothesis)

    if hypothesis.confidence >= 0.7:
        hypothesis.status = HypothesisStatus.SUPPORTED.value
    elif hypothesis.confidence >= 0.4:
        hypothesis.status = HypothesisStatus.WEAK.value
    elif len(hypothesis.evidence_against) > len(hypothesis.evidence_for):
        hypothesis.status = HypothesisStatus.REJECTED.value
    else:
        hypothesis.status = HypothesisStatus.UNKNOWN.value

    return hypothesis


def add_evidence_for(hypothesis: Hypothesis, evidence: str) -> Hypothesis:
    """Add supporting evidence and recompute confidence."""
    if evidence not in hypothesis.evidence_for:
        hypothesis.evidence_for.append(evidence)
    # Remove from against if present (contradiction resolved)
    if evidence in hypothesis.evidence_against:
        hypothesis.evidence_against.remove(evidence)
    return update_hypothesis_status(hypothesis)


def add_evidence_against(hypothesis: Hypothesis, evidence: str) -> Hypothesis:
    """Add contradicting evidence and recompute confidence."""
    if evidence not in hypothesis.evidence_against:
        hypothesis.evidence_against.append(evidence)
    # Remove from for if present
    if evidence in hypothesis.evidence_for:
        hypothesis.evidence_for.remove(evidence)
    return update_hypothesis_status(hypothesis)


def rank_hypotheses(hypotheses: List[Hypothesis]) -> List[Hypothesis]:
    """Rank hypotheses by confidence (highest first)."""
    return sorted(hypotheses, key=lambda h: h.confidence, reverse=True)


def select_best(hypotheses: List[Hypothesis]) -> Optional[Hypothesis]:
    """Select the highest-confidence hypothesis, if any meet minimum threshold."""
    ranked = rank_hypotheses(hypotheses)
    for h in ranked:
        if h.status in (HypothesisStatus.SUPPORTED.value, HypothesisStatus.WEAK.value):
            return h
    return ranked[0] if ranked else None


def create_hypothesis(
    statement: str,
    evidence_for: Optional[List[str]] = None,
    evidence_against: Optional[List[str]] = None,
    related_components: Optional[List[str]] = None,
    source: str = "llm",
    hypothesis_id: Optional[str] = None,
) -> Hypothesis:
    """Create and evaluate a new hypothesis."""
    import uuid
    h = Hypothesis(
        id=hypothesis_id or f"hyp-{uuid.uuid4().hex[:8]}",
        statement=statement,
        evidence_for=evidence_for or [],
        evidence_against=evidence_against or [],
        related_components=related_components or [],
        source=source,
    )
    return update_hypothesis_status(h)
