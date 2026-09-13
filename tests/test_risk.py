"""
Tests for app/control/risk.py — structured risk assessment engine.

Verifies multi-factor risk scoring, risk level classification,
and high-risk target detection.
"""
import pytest

from app.control.risk import assess_risk, RiskAssessment, RiskFactor
from app.control.state import RiskLevel


# --- Risk assessment for known actions ---

def test_check_status_is_low_risk():
    """Read-only status checks should be low risk."""
    risk = assess_risk("check_status", "backend")
    assert risk.risk_level == RiskLevel.LOW.value
    assert risk.reversible is True


def test_restart_container_is_medium_risk():
    """Container restart should be medium risk."""
    risk = assess_risk("restart_container", "backend")
    assert risk.risk_level in (RiskLevel.LOW.value, RiskLevel.MEDIUM.value)
    assert risk.reversible is True
    assert risk.rollback_available is True


def test_restart_service_is_medium_risk():
    """Service restart should be medium risk."""
    risk = assess_risk("restart_service", "nginx")
    assert risk.risk_level in (RiskLevel.LOW.value, RiskLevel.MEDIUM.value)
    assert risk.reversible is True


def test_delete_resource_is_critical_risk():
    """Resource deletion should be critical risk."""
    risk = assess_risk("delete_resource", "data")
    assert risk.risk_level in (RiskLevel.HIGH.value, RiskLevel.CRITICAL.value)
    assert risk.reversible is False


def test_modify_config_is_medium_or_high_risk():
    """Config modification should be medium-to-high risk."""
    risk = assess_risk("modify_config", "nginx")
    assert risk.risk_level in (RiskLevel.MEDIUM.value, RiskLevel.HIGH.value)


# --- High-risk targets ---

def test_database_target_increases_risk():
    """Targeting a database should increase risk."""
    risk_db = assess_risk("restart_container", "mysql")
    risk_app = assess_risk("restart_container", "backend")
    assert risk_db.overall_score >= risk_app.overall_score


def test_redis_target_increases_risk():
    """Targeting Redis should increase risk."""
    risk_redis = assess_risk("restart_container", "redis")
    risk_app = assess_risk("restart_container", "myapp")
    assert risk_redis.overall_score >= risk_app.overall_score


# --- Risk factors ---

def test_risk_assessment_has_all_factors():
    """Risk assessment should have all required factors."""
    risk = assess_risk("restart_container", "backend")
    factor_names = {f.name for f in risk.factors}
    assert "severity" in factor_names
    assert "blast_radius" in factor_names
    assert "reversibility" in factor_names
    assert "production_impact" in factor_names
    assert "destructive_potential" in factor_names
    assert "confidence" in factor_names
    assert "dependency_impact" in factor_names


def test_risk_factor_weighted_score():
    """RiskFactor.weighted_score should compute correctly."""
    f = RiskFactor(name="test", score=0.5, weight=2.0)
    assert f.weighted_score == 1.0


def test_risk_overall_score_bounded():
    """Overall risk score should be between 0 and 1."""
    for action in ["check_status", "restart_container", "delete_resource", "modify_config"]:
        risk = assess_risk(action, "backend")
        assert 0.0 <= risk.overall_score <= 1.0


# --- Context effects ---

def test_low_confidence_increases_risk():
    """Low action confidence should increase risk."""
    risk_high_conf = assess_risk("restart_container", "backend", context={"confidence": 0.9})
    risk_low_conf = assess_risk("restart_container", "backend", context={"confidence": 0.2})
    assert risk_low_conf.overall_score >= risk_high_conf.overall_score


def test_many_dependencies_increase_risk():
    """Many affected dependencies should increase risk."""
    risk_deps = assess_risk("restart_container", "redis",
                           context={"affected_dependencies": ["app1", "app2", "app3"]})
    risk_no_deps = assess_risk("restart_container", "redis")
    assert risk_deps.overall_score >= risk_no_deps.overall_score


# --- Risk level classification ---

def test_risk_level_classification():
    """Risk levels should be classified correctly from scores."""
    risk = assess_risk("check_status", "")
    assert risk.risk_level in ("low", "medium", "high", "critical")

    risk = assess_risk("delete_resource", "mysql")
    assert risk.risk_level in ("high", "critical")


# --- Serialization ---

def test_risk_assessment_to_dict():
    """RiskAssessment.to_dict should serialize properly."""
    risk = assess_risk("restart_container", "backend")
    d = risk.to_dict()
    assert d["action_type"] == "restart_container"
    assert d["target"] == "backend"
    assert "risk_level" in d
    assert "factors" in d
    assert isinstance(d["factors"], list)


def test_risk_explanation():
    """Risk assessment should have an explanation."""
    risk = assess_risk("restart_container", "backend")
    assert isinstance(risk.explanation, str)
    assert len(risk.explanation) > 0


# --- Unknown action type ---

def test_unknown_action_type_gets_default_risk():
    """Unknown action types should get a default risk assessment."""
    risk = assess_risk("unknown_action", "target")
    assert risk.risk_level in ("low", "medium", "high", "critical")
    assert risk.overall_score >= 0.0
