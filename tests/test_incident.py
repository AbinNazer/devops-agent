"""
Tests for app/control/incident.py — structured incident tracking.

Verifies that incidents are correctly created, tracked, closed,
and formatted for memory integration.
"""
import time

import pytest

from app.control.incident import (
    create_incident, Incident, TimelineEntry,
)


# --- Incident creation ---

def test_create_incident_basic():
    """Basic incident creation should work."""
    incident = create_incident("Backend is down")
    assert incident.title == "Backend is down"
    assert incident.status == "open"
    assert incident.id.startswith("INC-")


def test_create_incident_with_details():
    """Incident creation with all details should work."""
    incident = create_incident(
        title="Database timeout",
        description="PostgreSQL is not responding",
        severity="high",
        environment="prod",
        components=["postgresql", "erp-backend"],
    )
    assert incident.severity == "high"
    assert incident.environment == "prod"
    assert "postgresql" in incident.affected_components
    assert "erp-backend" in incident.affected_components


def test_incident_starts_with_creation_entry():
    """New incident should have a timeline entry for creation."""
    incident = create_incident("Test incident")
    assert len(incident.timeline) == 1
    assert incident.timeline[0].event == "incident_created"


# --- Timeline ---

def test_incident_add_timeline_entry():
    """Adding timeline entries should work."""
    incident = create_incident("Test")
    incident.add_timeline_entry("tool_executed", "docker_health_status checked")
    assert len(incident.timeline) == 2
    assert incident.timeline[1].event == "tool_executed"


def test_incident_add_timeline_event():
    """add_timeline_event should work (alias for add_timeline_entry)."""
    incident = create_incident("Test")
    incident.add_timeline_event("hypothesis_generated", "Redis is down")
    assert len(incident.timeline) == 2


def test_timeline_entry_to_dict():
    """TimelineEntry.to_dict should serialize properly."""
    entry = TimelineEntry(timestamp=time.time(), event="test", detail="detail", phase="observe")
    d = entry.to_dict()
    assert d["event"] == "test"
    assert d["detail"] == "detail"
    assert d["phase"] == "observe"
    assert "timestamp" in d


# --- Incident closure ---

def test_incident_close():
    """Closing an incident should set status and outcome."""
    incident = create_incident("Test")
    incident.close("success", "Problem resolved")
    assert incident.status == "closed"
    assert incident.outcome == "success"
    assert incident.outcome_summary == "Problem resolved"
    assert incident.end_time is not None


def test_incident_duration():
    """Duration should be calculated correctly."""
    incident = create_incident("Test")
    time.sleep(0.05)
    duration = incident.get_duration()
    assert duration >= 0.04  # Allow some timing tolerance


def test_incident_duration_after_close():
    """Duration should be fixed after close."""
    incident = create_incident("Test")
    time.sleep(0.05)
    incident.close("success")
    duration = incident.get_duration()
    assert duration >= 0.04


# --- Incident serialization ---

def test_incident_to_dict():
    """Incident.to_dict should serialize properly."""
    incident = create_incident("Test", severity="high")
    d = incident.to_dict()
    assert d["title"] == "Test"
    assert d["severity"] == "high"
    assert d["status"] == "open"
    assert "timeline" in d
    assert "id" in d


def test_incident_to_memory_content():
    """to_memory_content should format incident for memory storage."""
    incident = create_incident("Backend restart", severity="medium")
    incident.add_timeline_event("diagnosis_started", "Checking docker")
    incident.close("success", "Restarted successfully")

    content = incident.to_memory_content()
    assert "Backend restart" in content
    assert "medium" in content
    assert "success" in content
    assert "diagnosis_started" in content


# --- Full incident lifecycle ---

def test_full_incident_lifecycle():
    """Test a complete incident lifecycle from creation to closure."""
    # Create
    incident = create_incident(
        title="ERP backend down",
        severity="high",
        environment="prod",
        components=["erp-backend", "redis"],
    )

    # Timeline events
    incident.add_timeline_event("diagnosis_started", phase="observe")
    incident.add_timeline_event("docker_status_checked", phase="observe")
    incident.add_timeline_event("logs_retrieved", phase="observe")
    incident.add_timeline_event("redis_identified", phase="generate_hypotheses")
    incident.add_timeline_event("restart_proposed", phase="create_plan")
    incident.add_timeline_event("user_approved", phase="approval")
    incident.add_timeline_event("restart_executed", phase="execute")
    incident.add_timeline_event("verification_passed", phase="verify")

    # Evidence and hypothesis
    incident.evidence = [
        "Backend logs show Redis connection timeout",
        "Redis container is unhealthy",
    ]
    incident.selected_hypothesis = {
        "statement": "Backend failing due to Redis unavailability",
        "confidence": 0.85,
    }
    incident.confidence = 0.85

    # Close
    incident.close("success", "Restarted backend after Redis recovery")

    # Verify
    assert incident.status == "closed"
    assert incident.outcome == "success"
    assert len(incident.timeline) == 10  # 1 creation + 9 events
    assert incident.get_duration() >= 0
    assert incident.confidence == 0.85


# --- Edge cases ---

def test_incident_with_no_components():
    """Incident with no components should work."""
    incident = create_incident("General issue")
    assert incident.affected_components == []


def test_incident_with_empty_title():
    """Incident with empty title should work."""
    incident = create_incident("")
    assert incident.title == ""


def test_incident_timeline_ordering():
    """Timeline entries should be in chronological order."""
    incident = create_incident("Test")
    incident.add_timeline_event("first")
    time.sleep(0.01)
    incident.add_timeline_event("second")
    time.sleep(0.01)
    incident.add_timeline_event("third")

    timestamps = [e.timestamp for e in incident.timeline]
    assert timestamps == sorted(timestamps)
