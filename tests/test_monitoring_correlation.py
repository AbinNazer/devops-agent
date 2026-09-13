"""Tests for app/monitoring/correlation.py — deterministic event correlation."""
import time
import pytest
from app.monitoring.models import Anomaly, Severity
from app.monitoring.incidents import IncidentStore
from app.monitoring.correlation import (
    get_correlated_types, are_correlated, correlate_anomalies,
)
from app.monitoring.deduplication import find_or_create_incident
from app.monitoring.thresholds import ThresholdConfig


class TestGetCorrelatedTypes:
    def test_memory_spike_correlates_with_restart(self):
        types = get_correlated_types("container_memory_spike")
        assert "container_restart_loop" in types
        assert "oom_detected" in types

    def test_oom_correlates_with_memory(self):
        types = get_correlated_types("oom_detected")
        assert "container_memory_spike" in types
        assert "container_restart_loop" in types

    def test_unknown_type_returns_empty(self):
        types = get_correlated_types("unknown_type")
        assert types == set()


class TestAreCorrelated:
    def test_same_host_same_component_correlated(self):
        a1 = Anomaly(host="vps", component="backend", anomaly_type="container_memory_spike",
                     timestamp=time.time())
        a2 = Anomaly(host="vps", component="backend", anomaly_type="oom_detected",
                     timestamp=time.time())
        assert are_correlated(a1, a2) is True

    def test_different_host_not_correlated(self):
        a1 = Anomaly(host="vps1", component="backend", anomaly_type="container_memory_spike",
                     timestamp=time.time())
        a2 = Anomaly(host="vps2", component="backend", anomaly_type="oom_detected",
                     timestamp=time.time())
        assert are_correlated(a1, a2) is False

    def test_unrelated_types_not_correlated(self):
        a1 = Anomaly(host="vps", component="backend", anomaly_type="cpu_high",
                     timestamp=time.time())
        a2 = Anomaly(host="vps", component="backend", anomaly_type="disk_high",
                     timestamp=time.time())
        assert are_correlated(a1, a2) is False

    def test_host_memory_correlates_with_container_memory(self):
        a1 = Anomaly(host="vps", component="system", component_type="system",
                     anomaly_type="memory_high", timestamp=time.time())
        a2 = Anomaly(host="vps", component="backend", component_type="docker",
                     anomaly_type="container_memory_spike", timestamp=time.time())
        assert are_correlated(a1, a2) is True

    def test_time_window_check(self):
        a1 = Anomaly(host="vps", component="backend", anomaly_type="container_memory_spike",
                     timestamp=time.time() - 700)  # > 600s window
        a2 = Anomaly(host="vps", component="backend", anomaly_type="oom_detected",
                     timestamp=time.time())
        assert are_correlated(a1, a2, window_seconds=600.0) is False


class TestCorrelateAnomalies:
    def test_correlates_oom_with_existing_restart_incident(self):
        store = IncidentStore()
        config = ThresholdConfig(cooldown_seconds=0)

        # First: create an incident for restart loop
        restart_anomaly = Anomaly(host="vps", component="backend",
                                 anomaly_type="container_restart_loop",
                                 severity=Severity.WARNING.value,
                                 evidence=["Restart count: 5"])
        find_or_create_incident(restart_anomaly, store, config)

        # Then: correlate OOM with the existing restart incident
        oom_anomaly = Anomaly(host="vps", component="backend",
                             anomaly_type="oom_detected",
                             severity=Severity.CRITICAL.value,
                             evidence=["OOM detected"])
        affected = correlate_anomalies([oom_anomaly], store, config)
        assert len(affected) >= 1
        # The existing incident should have been updated
        inc = store.get_active()[0]
        assert "OOM detected" in inc.evidence

    def test_uncorrelated_critical_creates_new_incident(self):
        store = IncidentStore()
        config = ThresholdConfig(cooldown_seconds=0)
        anomaly = Anomaly(host="vps", component="backend",
                         anomaly_type="oom_detected",
                         severity=Severity.CRITICAL.value,
                         evidence=["OOM detected"])
        affected = correlate_anomalies([anomaly], store, config)
        assert len(affected) == 1
        assert store.active_count == 1

    def test_non_critical_not_correlated_creates_nothing(self):
        store = IncidentStore()
        config = ThresholdConfig(cooldown_seconds=0)
        # cpu_high is not in any correlation rules
        anomaly = Anomaly(host="vps", component="backend",
                         anomaly_type="cpu_high",
                         severity=Severity.WARNING.value,
                         evidence=["CPU high"])
        affected = correlate_anomalies([anomaly], store, config)
        # Should not create incident (not correlated, not critical enough on its own)
        # Actually: correlate_anomalies only auto-creates for critical uncorrelated
        assert store.active_count == 0
