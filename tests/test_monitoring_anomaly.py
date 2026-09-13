"""Tests for app/monitoring/anomaly.py — deterministic anomaly detection."""
import time
import pytest
from app.monitoring.collector import MockCollector
from app.monitoring.anomaly import (
    detect_system_anomalies, detect_container_anomalies,
    detect_service_anomalies, detect_log_anomalies,
    detect_oom_indicators, detect_crash_indicators,
)
from app.monitoring.thresholds import ThresholdConfig


class TestSystemAnomalies:
    def test_normal_cpu(self):
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=50.0, memory=60.0, disk=40.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        assert len(anomalies) == 0

    def test_cpu_warning(self):
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=85.0, memory=50.0, disk=40.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "cpu_high"
        assert anomalies[0].severity == "warning"

    def test_cpu_critical(self):
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=96.0, memory=50.0, disk=40.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].severity == "critical"

    def test_memory_warning(self):
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=50.0, memory=85.0, disk=40.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        assert any(a.anomaly_type == "memory_high" for a in anomalies)

    def test_disk_critical(self):
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=50.0, memory=50.0, disk=96.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        disk_anomaly = next(a for a in anomalies if a.anomaly_type == "disk_high")
        assert disk_anomaly.severity == "critical"

    def test_load_high(self):
        collector = MockCollector()
        # Load 10 on 4 cores = 2.5 per core > warning threshold of 2.0
        collector.set_system_metrics(host="vps", cpu=50.0, memory=50.0, disk=40.0,
                                     load_1m=10.0, cpu_cores=4)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        load_anomaly = next((a for a in anomalies if a.anomaly_type == "load_high"), None)
        assert load_anomaly is not None
        assert load_anomaly.current_value == 2.5

    def test_multiple_anomalies(self):
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=96.0, memory=96.0, disk=96.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps)
        assert len(anomalies) == 3  # cpu, memory, disk all critical

    def test_custom_config(self):
        config = ThresholdConfig(cpu_warning=50.0, cpu_critical=70.0)
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=55.0, memory=50.0, disk=40.0)
        snaps = collector.get_system_metrics("vps")
        anomalies = detect_system_anomalies(snaps, config)
        assert len(anomalies) == 1
        assert anomalies[0].severity == "warning"


class TestContainerAnomalies:
    def test_running_container_no_anomaly(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="running")
        snaps = collector.get_container_states("vps")
        anomalies = detect_container_anomalies(snaps)
        assert len(anomalies) == 0

    def test_stopped_container(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="exited")
        snaps = collector.get_container_states("vps")
        anomalies = detect_container_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "container_stopped"
        assert anomalies[0].severity == "warning"

    def test_unhealthy_container(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="running", health="unhealthy")
        snaps = collector.get_container_health("vps")
        anomalies = detect_container_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "container_unhealthy"

    def test_restarting_container(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="running", restart_count=5)
        snaps = collector.get_container_metrics("vps")
        anomalies = detect_container_anomalies(snaps)
        assert any(a.anomaly_type == "container_restarting" for a in anomalies)

    def test_restart_loop_critical(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="running", restart_count=15)
        snaps = collector.get_container_metrics("vps")
        anomalies = detect_container_anomalies(snaps)
        loop_anomaly = next((a for a in anomalies if a.anomaly_type == "container_restart_loop"), None)
        assert loop_anomaly is not None
        assert loop_anomaly.severity == "critical"

    def test_container_cpu_spike(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="running", cpu_percent=90.0)
        snaps = collector.get_container_metrics("vps")
        anomalies = detect_container_anomalies(snaps)
        assert any(a.anomaly_type == "container_cpu_spike" for a in anomalies)

    def test_container_memory_spike(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="running", memory_percent=90.0)
        snaps = collector.get_container_metrics("vps")
        anomalies = detect_container_anomalies(snaps)
        assert any(a.anomaly_type == "container_memory_spike" for a in anomalies)


class TestServiceAnomalies:
    def test_active_service_no_anomaly(self):
        collector = MockCollector()
        collector.set_service_state(host="vps", name="nginx", active=True, failed=False)
        snaps = collector.get_service_states("vps")
        anomalies = detect_service_anomalies(snaps)
        assert len(anomalies) == 0

    def test_failed_service(self):
        collector = MockCollector()
        collector.set_service_state(host="vps", name="nginx", active=False, failed=True)
        snaps = collector.get_service_states("vps")
        anomalies = detect_service_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "service_failed"
        assert anomalies[0].severity == "critical"

    def test_stopped_service(self):
        collector = MockCollector()
        collector.set_service_state(host="vps", name="nginx", active=False, failed=False)
        snaps = collector.get_service_states("vps")
        anomalies = detect_service_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "service_stopped"
        assert anomalies[0].severity == "warning"


class TestLogAnomalies:
    def test_error_spike(self):
        collector = MockCollector()
        collector.set_log_events(host="vps", component="app", events=[
            {"message": f"error {i}", "severity": "error"} for i in range(10)
        ])
        snaps = collector.get_log_events("vps")
        anomalies = detect_log_anomalies(snaps)
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "error_spike"

    def test_no_error_spike(self):
        collector = MockCollector()
        collector.set_log_events(host="vps", component="app", events=[
            {"message": "error once", "severity": "error"}
        ])
        snaps = collector.get_log_events("vps")
        anomalies = detect_log_anomalies(snaps)
        assert len(anomalies) == 0


class TestOOMAndCrash:
    def test_oom_detection(self):
        events = [{"message": "Out of memory: Kill process 1234"}, {"message": "oom-killer invoked"}]
        anomalies = detect_oom_indicators(events, host="vps", component="app")
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "oom_detected"
        assert anomalies[0].severity == "critical"

    def test_no_oom(self):
        events = [{"message": "Normal log message"}]
        anomalies = detect_oom_indicators(events, host="vps", component="app")
        assert len(anomalies) == 0

    def test_crash_indicators_single(self):
        events = [{"message": "panic: runtime error"}]
        anomalies = detect_crash_indicators(events, host="vps", component="app")
        assert len(anomalies) == 1
        assert anomalies[0].anomaly_type == "crash_indicator"
        assert anomalies[0].severity == "warning"  # single indicator = warning

    def test_crash_indicators_multiple(self):
        events = [
            {"message": "panic: runtime error"},
            {"message": "segfault at address"},
            {"message": "fatal signal 11"},
        ]
        anomalies = detect_crash_indicators(events, host="vps", component="app")
        assert len(anomalies) == 1
        assert anomalies[0].severity == "critical"  # 2+ indicators = critical

    def test_no_crash(self):
        events = [{"message": "INFO: server started"}]
        anomalies = detect_crash_indicators(events, host="vps", component="app")
        assert len(anomalies) == 0
