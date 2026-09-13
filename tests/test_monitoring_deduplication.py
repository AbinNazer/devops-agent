"""Tests for app/monitoring/deduplication.py — incident deduplication."""
import time
import pytest
from app.monitoring.models import Anomaly, Severity
from app.monitoring.incidents import IncidentStore
from app.monitoring.deduplication import find_or_create_incident
from app.monitoring.thresholds import ThresholdConfig


@pytest.fixture
def store():
    return IncidentStore(max_active=10)


@pytest.fixture
def config():
    return ThresholdConfig(cooldown_seconds=0)  # No cooldown for testing


class TestFindOrCreateIncident:
    def test_creates_new_incident(self, store, config):
        anomaly = Anomaly(host="vps", component="system", anomaly_type="cpu_high",
                         metric_name="cpu_percent", severity=Severity.WARNING.value,
                         evidence=["CPU at 85%"])
        inc, is_new = find_or_create_incident(anomaly, store, config)
        assert is_new is True
        assert store.active_count == 1

    def test_finds_existing_by_fingerprint(self, store, config):
        anomaly = Anomaly(host="vps", component="system", anomaly_type="cpu_high",
                         metric_name="cpu_percent", severity=Severity.WARNING.value,
                         evidence=["CPU at 85%"])
        inc1, _ = find_or_create_incident(anomaly, store, config)
        # Same fingerprint → finds same incident
        anomaly2 = Anomaly(host="vps", component="system", anomaly_type="cpu_high",
                          metric_name="cpu_percent", severity=Severity.WARNING.value,
                          evidence=["CPU at 90%"])
        inc2, is_new = find_or_create_incident(anomaly2, store, config)
        assert is_new is False
        assert inc1.id == inc2.id

    def test_different_fingerprint_creates_new(self, store, config):
        a1 = Anomaly(host="vps", component="system", anomaly_type="cpu_high",
                     metric_name="cpu_percent", evidence=["CPU high"])
        a2 = Anomaly(host="vps", component="system", anomaly_type="memory_high",
                     metric_name="memory_percent", evidence=["Mem high"])
        inc1, _ = find_or_create_incident(a1, store, config)
        inc2, _ = find_or_create_incident(a2, store, config)
        assert inc1.id != inc2.id
        assert store.active_count == 2

    def test_cooldown_prevents_update(self, store):
        config = ThresholdConfig(cooldown_seconds=300)
        anomaly = Anomaly(host="vps", component="system", anomaly_type="cpu_high",
                         metric_name="cpu_percent", evidence=["CPU at 85%"])
        inc1, _ = find_or_create_incident(anomaly, store, config)
        # Second call within cooldown → same incident, not updated
        anomaly2 = Anomaly(host="vps", component="system", anomaly_type="cpu_high",
                          metric_name="cpu_percent", evidence=["CPU at 95%"])
        inc2, is_new = find_or_create_incident(anomaly2, store, config)
        assert inc2.id == inc1.id
        assert is_new is False
        # Evidence should NOT be updated (within cooldown)
        assert len(inc2.evidence) == 1  # original evidence only

    def test_dedup_ram_91_92_93_94(self, store, config):
        """RAM increasing 91→92→93→94 should be ONE incident."""
        for pct in [91, 92, 93, 94]:
            anomaly = Anomaly(host="vps", component="system", anomaly_type="memory_high",
                            metric_name="memory_percent", severity=Severity.WARNING.value,
                            current_value=float(pct), evidence=[f"Memory at {pct}%"])
            inc, is_new = find_or_create_incident(anomaly, store, config)
        assert store.active_count == 1

    def test_max_concurrent_incidents(self, store, config):
        """Should not exceed max concurrent incidents."""
        config = ThresholdConfig(max_concurrent_incidents=3, cooldown_seconds=0)
        for i in range(5):
            anomaly = Anomaly(host="vps", component=f"svc{i}", anomaly_type="stopped",
                            metric_name="state", evidence=[f"svc{i} stopped"])
            find_or_create_incident(anomaly, store, config)
        assert store.active_count <= 3
