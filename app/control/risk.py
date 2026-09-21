"""
Structured Risk Assessment Engine for Phase 5.

Evaluates proposed actions on multiple risk factors and assigns a risk level.
The LLM cannot bypass this — every action must pass through risk assessment
before reaching the policy or permission layers.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.control.state import RiskLevel


@dataclass
class RiskFactor:
    """A single risk factor assessment."""
    name: str
    score: float  # 0.0 to 1.0
    weight: float = 1.0
    detail: str = ""

    @property
    def weighted_score(self) -> float:
        return self.score * self.weight


@dataclass
class RiskAssessment:
    """Complete risk assessment for a proposed action."""
    action_type: str
    target: str
    factors: List[RiskFactor] = field(default_factory=list)
    overall_score: float = 0.0
    risk_level: str = RiskLevel.LOW.value
    reversible: bool = True
    explanation: str = ""
    rollback_available: bool = False

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "overall_score": self.overall_score,
            "risk_level": self.risk_level,
            "reversible": self.reversible,
            "explanation": self.explanation,
            "rollback_available": self.rollback_available,
            "factors": [{"name": f.name, "score": f.score, "detail": f.detail} for f in self.factors],
        }


# Risk profiles for known action types
_ACTION_RISK_PROFILES = {
    "start_container": {"severity": 0.4, "blast_radius": 0.3, "reversibility": 0.8, "production_impact": 0.4, "destructive_potential": 0.1},
    "stop_container": {"severity": 0.6, "blast_radius": 0.4, "reversibility": 0.8, "production_impact": 0.6, "destructive_potential": 0.2},
    "restart_container": {
        "severity": 0.4,
        "blast_radius": 0.3,
        "reversibility": 0.8,  # high = easily reversible
        "production_impact": 0.4,
        "destructive_potential": 0.1,
    },
    "restart_service": {
        "severity": 0.4,
        "blast_radius": 0.3,
        "reversibility": 0.8,
        "production_impact": 0.4,
        "destructive_potential": 0.1,
    },
    "check_status": {
        "severity": 0.0,
        "blast_radius": 0.0,
        "reversibility": 1.0,
        "production_impact": 0.0,
        "destructive_potential": 0.0,
    },
    "delete_resource": {
        "severity": 0.9,
        "blast_radius": 0.8,
        "reversibility": 0.1,
        "production_impact": 0.9,
        "destructive_potential": 0.9,
    },
    "modify_config": {
        "severity": 0.6,
        "blast_radius": 0.5,
        "reversibility": 0.6,
        "production_impact": 0.6,
        "destructive_potential": 0.4,
    },
}

# Which container/service names are considered high-risk (production databases, etc.)
_HIGH_RISK_TARGETS = {"mysql", "postgres", "postgresql", "redis", "mongodb", "elasticsearch", "kafka"}


def assess_risk(action_type: str, target: str = "", context: Optional[Dict] = None) -> RiskAssessment:
    """
    Assess the risk of a proposed action.

    Returns a RiskAssessment with individual factors and an overall risk level.
    """
    context = context or {}
    profile = _ACTION_RISK_PROFILES.get(action_type, _ACTION_RISK_PROFILES.get("check_status", {}))

    factors = []

    # Severity factor
    severity = profile.get("severity", 0.5)
    factors.append(RiskFactor(
        name="severity",
        score=severity,
        weight=1.0,
        detail=f"Action severity: {severity:.1f}",
    ))

    # Blast radius factor
    blast = profile.get("blast_radius", 0.3)
    if target.lower() in _HIGH_RISK_TARGETS:
        blast = min(1.0, blast + 0.3)
    factors.append(RiskFactor(
        name="blast_radius",
        score=blast,
        weight=1.2,  # weighted higher because blast radius matters
        detail=f"Blast radius: {blast:.1f}" + (" (high-risk target)" if target.lower() in _HIGH_RISK_TARGETS else ""),
    ))

    # Reversibility factor (inverted: higher score = less reversible = more risk)
    reversibility = profile.get("reversibility", 0.5)
    irreversibility = 1.0 - reversibility
    factors.append(RiskFactor(
        name="reversibility",
        score=irreversibility,
        weight=1.5,  # heavily weighted — reversibility is critical
        detail=f"Reversibility: {reversibility:.1f}" + (" (not reversible)" if reversibility < 0.3 else ""),
    ))

    # Production impact
    prod_impact = profile.get("production_impact", 0.3)
    factors.append(RiskFactor(
        name="production_impact",
        score=prod_impact,
        weight=1.3,
        detail=f"Production impact: {prod_impact:.1f}",
    ))

    # Destructive potential
    destructive = profile.get("destructive_potential", 0.1)
    factors.append(RiskFactor(
        name="destructive_potential",
        score=destructive,
        weight=2.0,  # highest weight — destructive actions are the most dangerous
        detail=f"Destructive potential: {destructive:.1f}",
    ))

    # Confidence factor (from context)
    confidence = context.get("confidence", 0.5)
    confidence_risk = 1.0 - confidence  # lower confidence = higher risk
    factors.append(RiskFactor(
        name="confidence",
        score=confidence_risk,
        weight=0.8,
        detail=f"Action confidence: {confidence:.1f} (uncertainty risk: {confidence_risk:.1f})",
    ))

    # Dependency impact (from context)
    deps = context.get("affected_dependencies", [])
    dep_risk = min(1.0, len(deps) * 0.2) if deps else 0.0
    factors.append(RiskFactor(
        name="dependency_impact",
        score=dep_risk,
        weight=1.0,
        detail=f"Affects {len(deps)} dependencies" if deps else "No known dependencies affected",
    ))

    # Compute weighted overall score
    total_weight = sum(f.weight for f in factors)
    overall_score = sum(f.weighted_score for f in factors) / total_weight if total_weight > 0 else 0.0

    # Determine risk level from score
    if overall_score >= 0.75:
        risk_level = RiskLevel.CRITICAL.value
    elif overall_score >= 0.5:
        risk_level = RiskLevel.HIGH.value
    elif overall_score >= 0.3:
        risk_level = RiskLevel.MEDIUM.value
    else:
        risk_level = RiskLevel.LOW.value

    reversible = reversibility >= 0.5
    rollback_available = reversible and action_type in ("start_container", "stop_container", "restart_container", "restart_service")

    explanation_parts = []
    if risk_level == RiskLevel.CRITICAL.value:
        explanation_parts.append("CRITICAL risk: this action could cause significant damage")
    elif risk_level == RiskLevel.HIGH.value:
        explanation_parts.append("HIGH risk: careful consideration required")
    elif risk_level == RiskLevel.MEDIUM.value:
        explanation_parts.append("MEDIUM risk: moderate impact possible")
    else:
        explanation_parts.append("LOW risk: minimal impact expected")

    if not reversible:
        explanation_parts.append("This action is NOT easily reversible")
    if target.lower() in _HIGH_RISK_TARGETS:
        explanation_parts.append(f"Target '{target}' is a high-risk production component")

    return RiskAssessment(
        action_type=action_type,
        target=target,
        factors=factors,
        overall_score=round(overall_score, 3),
        risk_level=risk_level,
        reversible=reversible,
        explanation="; ".join(explanation_parts),
        rollback_available=rollback_available,
    )
