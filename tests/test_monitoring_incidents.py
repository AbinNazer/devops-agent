"""Tests for app/monitoring/incidents.py — incident lifecycle and store."""
import time
import pytest
from app.monitoring.models import Anomaly, Incident, IncidentState, Severity
from app.monitoring.incidents import (
    create_incident_from_anomaly, update_incident_with_anomaly,
    transition_incident, resolve_incident, suppress_incident, IncidentStore,
)


@pytest.fixture
def sample_anomaly():
    return Anomaly(
        host="vps", component="backend", component_type="docker",
        anomaly_type="container_stopped", severity=Severity.WARNING.value,
        message="Container 'backend' is exited", evidence=["Container state: exited"],
    )


class TestCreateIncidentFromAnomaly:
    def test_creates_incident(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        assert inc.host == "vps"
        assert inc.component == "backend"
        assert inc.anomaly_type == "container_stopped"
        assert inc.severity == "warning"
        assert len(inc.timeline) >= 2
        assert inc.evidence == ["Container state: exited"]

    def test_fingerprint_set(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        assert inc.fingerprint == sample_anomaly.fingerprint()


class TestUpdateIncidentWithAnomaly:
    def test_adds_evidence(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        new_anomaly = Anomaly(
            host="vps", component="backend", anomaly_type="container_unhealthy",
            severity=Severity.WARNING.value, evidence=["Health: unhealthy"],
        )
        update_incident_with_anomaly(inc, new_anomaly)
        assert len(inc.anomaly_ids) == 2
        assert "Health: unhealthy" in inc.evidence

    def test_escalates_severity(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        assert inc.severity == "warning"
        critical_anomaly = Anomaly(
            host="vps", component="backend", anomaly_type="oom_detected",
            severity=Severity.CRITICAL.value, evidence=["OOM detected"],
        )
        update_incident_with_anomaly(inc, critical_anomaly)
        assert inc.severity == "critical"


class TestTransitionIncident:
    def test_transition(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        transition_incident(inc, IncidentState.INVESTIGATING.value, "Starting investigation")
        assert inc.state == IncidentState.INVESTIGATING.value
        assert any(e.event == IncidentState.INVESTIGATING.value for e in inc.timeline)


class TestResolveIncident:
    def test_resolve(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        resolve_incident(inc, "Container restarted")
        assert inc.state == IncidentState.RESOLVED.value
        assert inc.closed_at is not None
        assert inc.resolution == "Container restarted"


class TestSuppressIncident:
    def test_suppress(self, sample_anomaly):
        inc = create_incident_from_anomaly(sample_anomaly)
        suppress_incident(inc, "Intentionally stopped for maintenance")
        assert inc.state == IncidentState.SUPPRESSED.value


class TestIncidentStore:
    def test_add_and_get(self, sample_anomaly):
        store = IncidentStore()
        inc = create_incident_from_anomaly(sample_anomaly)
        store.add(inc)
        assert store.get(inc.id) is inc
        assert store.active_count == 1

    def test_get_active(self, sample_anomaly):
        store = IncidentStore()
        inc1 = create_incident_from_anomaly(sample_anomaly)
        inc2 = create_incident_from_anomaly(Anomaly(host="vps", component="redis", anomaly_type="stopped"))
        store.add(inc1)
        store.add(inc2)
        assert store.active_count == 2
        active = store.get_active()
        assert len(active) == 2

    def test_get_by_fingerprint(self, sample_anomaly):
        store = IncidentStore()
        inc = create_incident_from_anomaly(sample_anomaly)
        store.add(inc)
        found = store.get_by_fingerprint(sample_anomaly.fingerprint())
        assert found is inc

    def test_get_by_fingerprint_not_found(self):
        store = IncidentStore()
        assert store.get_by_fingerprint("nonexistent|fingerprint") is None

    def test_close_moves_to_history(self, sample_anomaly):
        store = IncidentStore()
        inc = create_incident_from_anomaly(sample_anomaly)
        store.add(inc)
        store.close(inc.id, "Fixed")
        assert store.active_count == 0
        assert store.get(inc.id) is inc  # still in history

    def test_close_nonexistent(self):
        store = IncidentStore()
        result = store.close("nonexistent")
        assert result is None

    def test_total_incidents(self, sample_anomaly):
        store = IncidentStore()
        inc = create_incident_from_anomaly(sample_anomaly)
        store.add(inc)
        assert store.total_incidents == 1
        store.close(inc.id, "fixed")
        assert store.total_incidents == 1  # still 1, just in history now

    def test_max_history(self):
        store = IncidentStore(max_history=3)
        for i in range(5):
            inc = Incident(host="vps", component=f"svc{i}")
            inc.close(f"resolved {i}")
            store._add_to_history(inc)
        assert len(store._history) == 3  # oldest evicted
