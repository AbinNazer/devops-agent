"""Tests for app/monitoring/collector.py — MockCollector."""
import pytest
from app.monitoring.collector import MockCollector
from app.monitoring.interfaces import MonitoringCollector


class TestMockCollector:
    def setup_method(self):
        self.collector = MockCollector()

    def test_implements_interface(self):
        assert isinstance(self.collector, MonitoringCollector)

    def test_name(self):
        assert self.collector.name == "mock"

    def test_available_by_default(self):
        assert self.collector.is_available() is True

    def test_set_unavailable(self):
        self.collector.set_available(False)
        assert self.collector.is_available() is False

    def test_system_metrics(self):
        self.collector.set_system_metrics(host="vps", cpu=92.0, memory=85.0, disk=70.0)
        snaps = self.collector.get_system_metrics("vps")
        assert len(snaps) == 7  # cpu, mem, disk, load1, load5, load15, cores
        cpu_snap = next(s for s in snaps if s.metric_name == "cpu_percent")
        assert cpu_snap.value == 92.0
        assert cpu_snap.host == "vps"

    def test_container_states(self):
        self.collector.set_container_state(host="vps", name="backend", state="running")
        self.collector.set_container_state(host="vps", name="redis", state="exited")
        snaps = self.collector.get_container_states("vps")
        assert len(snaps) == 2
        backend = next(s for s in snaps if s.component == "backend")
        assert backend.value == 1.0  # running = 1.0
        redis = next(s for s in snaps if s.component == "redis")
        assert redis.value == 0.0  # exited = 0.0

    def test_container_health(self):
        self.collector.set_container_state(host="vps", name="app", state="running", health="unhealthy")
        snaps = self.collector.get_container_health("vps")
        assert len(snaps) == 1
        assert snaps[0].labels["health"] == "unhealthy"

    def test_container_metrics(self):
        self.collector.set_container_state(host="vps", name="app", state="running",
                                           restart_count=5, cpu_percent=90.0, memory_percent=88.0)
        snaps = self.collector.get_container_metrics("vps")
        assert len(snaps) == 3  # restart_count, cpu, memory
        rc = next(s for s in snaps if s.metric_name == "container_restart_count")
        assert rc.value == 5.0

    def test_service_states(self):
        self.collector.set_service_state(host="vps", name="nginx", active=True, failed=False)
        self.collector.set_service_state(host="vps", name="redis", active=False, failed=True)
        snaps = self.collector.get_service_states("vps")
        assert len(snaps) == 2
        nginx = next(s for s in snaps if s.component == "nginx")
        assert nginx.value == 1.0  # active
        redis = next(s for s in snaps if s.component == "redis")
        assert redis.value == 0.0  # inactive

    def test_log_events(self):
        self.collector.set_log_events(host="vps", component="backend", events=[
            {"message": "ERROR connection refused", "severity": "error"},
            {"message": "ERROR timeout", "severity": "error"},
            {"message": "WARN slow query", "severity": "warning"},
        ])
        snaps = self.collector.get_log_events("vps")
        error_snap = next(s for s in snaps if s.metric_name == "log_error_count")
        assert error_snap.value == 2.0
        warn_snap = next(s for s in snaps if s.metric_name == "log_warning_count")
        assert warn_snap.value == 1.0

    def test_log_details(self):
        events = [{"message": "crash", "severity": "fatal"}]
        self.collector.set_log_events(host="vps", component="app", events=events)
        details = self.collector.get_log_details("vps", "app")
        assert len(details) == 1
        assert details[0]["severity"] == "fatal"

    def test_collect_count_increments(self):
        assert self.collector._collect_count == 0
        self.collector.set_system_metrics(host="vps", cpu=50.0)
        self.collector.get_system_metrics("vps")
        assert self.collector._collect_count == 1
        self.collector.get_container_states("vps")
        assert self.collector._collect_count == 2

    def test_empty_metrics_return_empty(self):
        snaps = self.collector.get_system_metrics("nonexistent")
        assert snaps == []

    def test_all_empty_returns_empty(self):
        assert self.collector.get_container_states() == []
        assert self.collector.get_container_health() == []
        assert self.collector.get_container_metrics() == []
        assert self.collector.get_service_states() == []
        assert self.collector.get_log_events() == []
