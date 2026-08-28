import pytest
from app.intelligence.risk import calculate_risk
from app.intelligence.health_analyzer import evaluate_health
from app.intelligence.recommendations import rank_recommendations, ActionItem
from app.intelligence.summaries import generate_technical_summary
from app.intelligence.diagnosis import plan_diagnostics
from app.intelligence.incident_analyzer import analyze_incident
from app.intelligence.correlation import correlate_findings
from app.intelligence.analyzer import identify_log_errors, detect_failure_patterns, analyze_trends

def test_calculate_risk():
    risk = calculate_risk("CRITICAL", True, ["db", "api"])
    assert risk.score == "HIGH"
    assert risk.blast_radius == ["db", "api"]

def test_evaluate_health():
    health = evaluate_health("cpu_usage", 95, 80, 90)
    assert health.severity == "CRITICAL"
    assert health.threshold == 90
    
    health = evaluate_health("memory", 85, 80, 95)
    assert health.severity == "WARNING"

def test_rank_recommendations():
    items = [
        ActionItem("obs1", "diag1", "rec1", "act1", "LOW"),
        ActionItem("obs2", "diag2", "rec2", "act2", "CRITICAL"),
        ActionItem("obs3", "diag3", "rec3", "act3", "HIGH")
    ]
    ranked = rank_recommendations(items)
    assert ranked[0].priority == "CRITICAL"
    assert ranked[1].priority == "HIGH"
    assert ranked[2].priority == "LOW"

def test_generate_technical_summary():
    summary = generate_technical_summary({"cpu": 50}, [{"id": 1}], [{"severity": "LOW"}])
    assert summary.status == "CRITICAL"
    
    summary2 = generate_technical_summary({"cpu": 50}, [], [{"severity": "HIGH", "action": "Fix it"}])
    assert summary2.status == "WARNING"

def test_plan_diagnostics():
    plan = plan_diagnostics("Is Jenkins down?")
    assert "Jenkins" in plan.goal
    assert plan.next_tool == "get_jenkins_status"

def test_analyze_incident():
    incident = analyze_incident(["error 1", "error 2"], {"mem": 100}, ["deployment A"])
    assert incident.what_happened == "Service disruption"
    assert "deployment A" in incident.changed

def test_correlate_findings():
    correlations = correlate_findings({"memory_usage": 95}, ["Jenkins is slow"])
    assert len(correlations) == 1
    assert "Jenkins" in correlations[0]

def test_analyzer():
    errors = identify_log_errors(["info: started", "error: failed to bind"])
    assert len(errors) == 1
    
    patterns = detect_failure_patterns(["error"] * 6)
    assert len(patterns) == 1
    
    trend = analyze_trends({}, [{"cpu": 50}])
    assert trend == "Requires more data to establish trend."

