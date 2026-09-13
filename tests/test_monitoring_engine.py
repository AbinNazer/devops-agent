"""Tests for app/monitoring/engine.py — main monitoring engine."""
import pytest
from app.monitoring.collector import MockCollector
from app.monitoring.engine import MonitoringEngine
from app.monitoring.models import Severity
from app.monitoring.thresholds import ThresholdConfig


class TestMonitoringEngine:
    def setup_method(self):
        self.collector = MockCollector()
        self.config = ThresholdConfig(cooldown_seconds=0)
        self.engine = MonitoringEngine(collector=self.collector, config=self.config)

    def test_run_cycle_normal(self):
        self.collector.set_system_metrics(host="vps", cpu=50.0, memory=60.0, disk=40.0)
        result = self.engine.run_cycle("vps")
        assert result.errors == []
        assert len(result.anomalies) == 0
        assert result.incidents_created == []

    def test_run_cycle_detects_cpu_anomaly(self):
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        result = self.engine.run_cycle("vps")
        assert len(result.anomalies) == 1
        assert result.anomalies[0].anomaly_type == "cpu_high"
        assert len(result.incidents_created) == 1

    def test_run_cycle_detects_container_stopped(self):
        self.collector.set_container_state(host="vps", name="backend", state="exited")
        result = self.engine.run_cycle("vps")
        assert len(result.anomalies) == 1
        assert result.anomalies[0].anomaly_type == "container_stopped"

    def test_run_cycle_detects_unhealthy_container(self):
        self.collector.set_container_state(host="vps", name="app", state="running", health="unhealthy")
        result = self.engine.run_cycle("vps")
        assert len(result.anomalies) == 1
        assert result.anomalies[0].anomaly_type == "container_unhealthy"

    def test_run_cycle_detects_service_failed(self):
        self.collector.set_service_state(host="vps", name="nginx", active=False, failed=True)
        result = self.engine.run_cycle("vps")
        assert len(result.anomalies) == 1
        assert result.anomalies[0].anomaly_type == "service_failed"

    def test_run_cycle_detects_error_spike(self):
        self.collector.set_log_events(host="vps", component="app", events=[
            {"message": f"error {i}", "severity": "error"} for i in range(10)
        ])
        result = self.engine.run_cycle("vps")
        assert any(a.anomaly_type == "error_spike" for a in result.anomalies)

    def test_run_cycle_detects_restart_loop(self):
        self.collector.set_container_state(host="vps", name="app", state="running", restart_count=15)
        result = self.engine.run_cycle("vps")
        assert any(a.anomaly_type == "container_restart_loop" for a in result.anomalies)

    def test_deduplication(self):
        """Same anomaly in consecutive cycles should deduplicate."""
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        r1 = self.engine.run_cycle("vps")
        assert len(r1.incidents_created) == 1
        r2 = self.engine.run_cycle("vps")
        assert len(r2.incidents_created) == 0  # deduped via find_or_create_incident
        # With cooldown_seconds=0, dedup goes through find_or_create_incident
        # which returns (existing, False) → incidents_updated, not deduplicated
        assert len(r2.incidents_updated) >= 1

    def test_cooldown_prevents_alert_storm(self):
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        r1 = self.engine.run_cycle("vps")
        r2 = self.engine.run_cycle("vps")
        # Second cycle should be deduped or cooldown-blocked
        assert r2.incidents_deduplicated >= 1 or len(r2.incidents_created) == 0

    def test_collector_unavailable(self):
        self.collector.set_available(False)
        result = self.engine.run_cycle("vps")
        assert "Collector unavailable" in result.errors

    def test_stats(self):
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        self.engine.run_cycle("vps")
        stats = self.engine.stats
        assert stats["cycles"] == 1
        assert stats["total_anomalies"] >= 1
        assert stats["incidents_created"] >= 1

    def test_get_summary(self):
        self.collector.set_system_metrics(host="vps", cpu=50.0, memory=50.0, disk=50.0)
        self.engine.run_cycle("vps")
        summary = self.engine.get_summary()
        assert summary["collector"] == "mock"
        assert summary["collector_available"] is True
        assert summary["cycle_count"] == 1

    def test_get_active_incidents(self):
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        self.engine.run_cycle("vps")
        incidents = self.engine.get_active_incidents()
        assert len(incidents) >= 1

    def test_get_incident_detail(self):
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        self.engine.run_cycle("vps")
        incidents = self.engine.get_active_incidents()
        inc_id = incidents[0]["id"]
        detail = self.engine.get_incident(inc_id)
        assert detail is not None
        assert detail["id"] == inc_id

    def test_get_incident_not_found(self):
        assert self.engine.get_incident("nonexistent") is None

    def test_on_incident_confirmed_callback(self):
        confirmed = []
        def callback(incident):
            confirmed.append(incident)
        engine = MonitoringEngine(collector=self.collector, config=self.config,
                                  on_incident_confirmed=callback)
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        engine.run_cycle("vps")
        # Callback should have been called for confirmed incidents
        assert len(confirmed) >= 1

    def test_no_mutation_in_engine(self):
        """Engine must NEVER directly restart/stop/delete anything."""
        self.collector.set_container_state(host="vps", name="backend", state="exited")
        result = self.engine.run_cycle("vps")
        # Engine only creates incidents, never executes mutations
        assert result.incidents_created  # Should detect the issue
        # Verify the container is still in the same state in the collector
        states = self.collector.get_container_states("vps")
        backend = next(s for s in states if s.component == "backend")
        assert backend.labels["state"] == "exited"  # Unchanged!

    def test_multiple_components(self):
        self.collector.set_system_metrics(host="vps", cpu=96.0, memory=96.0, disk=40.0)
        self.collector.set_container_state(host="vps", name="app", state="running", health="unhealthy")
        self.collector.set_service_state(host="vps", name="nginx", active=False, failed=True)
        result = self.engine.run_cycle("vps")
        assert len(result.anomalies) >= 3  # cpu, memory, container, service

    def test_empty_collector(self):
        result = self.engine.run_cycle("vps")
        assert result.errors == []
        assert len(result.anomalies) == 0
