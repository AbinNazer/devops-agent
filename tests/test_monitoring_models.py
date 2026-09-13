"""Tests for app/monitoring/models.py — monitoring data models."""
import time
import pytest
from app.monitoring.models import (
    Metric, MetricSnapshot, MetricSeries, MetricKind, ComponentType,
    MonitoringEvent, Anomaly, AnomalyStatus, Severity,
    Incident, IncidentState, IncidentTimelineEvent, DetectionResult,
)


class TestMetric:
    def test_metric_creation(self):
        m = Metric(name="cpu_percent", kind=MetricKind.GAUGE.value, unit="percent")
        assert m.name == "cpu_percent"
        assert m.kind == "gauge"

    def test_metric_to_dict(self):
        m = Metric(name="disk", kind=MetricKind.GAUGE.value, unit="percent")
        d = m.to_dict()
        assert d["name"] == "disk"
        assert "kind" in d


class TestMetricSnapshot:
    def test_snapshot_creation(self):
        s = MetricSnapshot("cpu_percent", 85.0, host="vps1", component="system")
        assert s.value == 85.0
        assert s.host == "vps1"

    def test_snapshot_to_dict(self):
        s = MetricSnapshot("mem", 50.0, host="h", component="c")
        d = s.to_dict()
        assert d["value"] == 50.0
        assert "timestamp" in d

    def test_snapshot_with_labels(self):
        s = MetricSnapshot("container_state", 0.0, labels={"state": "exited"})
        assert s.labels["state"] == "exited"


class TestMetricSeries:
    def test_series_empty(self):
        s = MetricSeries(metric_name="cpu")
        assert s.latest is None
        assert s.values == []

    def test_series_with_snapshots(self):
        ts = time.time()
        s = MetricSeries(
            metric_name="cpu",
            snapshots=[
                MetricSnapshot("cpu", 50.0, ts),
                MetricSnapshot("cpu", 90.0, ts + 1),
            ],
        )
        assert s.latest.value == 90.0
        assert s.values == [50.0, 90.0]

    def test_series_to_dict(self):
        s = MetricSeries(metric_name="cpu", host="vps", snapshots=[
            MetricSnapshot("cpu", 50.0)
        ])
        d = s.to_dict()
        assert d["count"] == 1
        assert d["latest"]["value"] == 50.0


class TestMonitoringEvent:
    def test_event_creation(self):
        e = MonitoringEvent(host="vps", component="backend", event_type="error_spike")
        assert e.id.startswith("evt-")
        assert e.host == "vps"

    def test_event_to_dict(self):
        e = MonitoringEvent(host="vps", severity=Severity.WARNING.value, message="test")
        d = e.to_dict()
        assert d["severity"] == "warning"
        assert d["message"] == "test"


class TestAnomaly:
    def test_anomaly_creation(self):
        a = Anomaly(host="vps", component="system", anomaly_type="cpu_high")
        assert a.id.startswith("anom-")
        assert a.anomaly_type == "cpu_high"

    def test_anomaly_fingerprint(self):
        a = Anomaly(host="vps", component="system", anomaly_type="cpu_high", metric_name="cpu_percent")
        fp = a.fingerprint()
        assert fp == "vps|system|cpu_high|cpu_percent"

    def test_anomaly_fingerprint_stable(self):
        """Fingerprint must not depend on timestamp."""
        a1 = Anomaly(host="vps", component="sys", anomaly_type="cpu_high", metric_name="cpu")
        a2 = Anomaly(host="vps", component="sys", anomaly_type="cpu_high", metric_name="cpu")
        assert a1.fingerprint() == a2.fingerprint()

    def test_anomaly_to_dict(self):
        a = Anomaly(host="vps", component="sys", anomaly_type="cpu_high",
                    severity=Severity.CRITICAL.value, confidence=0.9)
        d = a.to_dict()
        assert d["severity"] == "critical"
        assert d["confidence"] == 0.9


class TestIncident:
    def test_incident_creation(self):
        inc = Incident(host="vps", component="backend", severity="warning")
        assert inc.id.startswith("MON-")
        assert inc.state == IncidentState.DETECTED.value

    def test_incident_timeline(self):
        inc = Incident()
        inc.add_timeline("detected", "CPU high")
        inc.add_timeline("confirmed", "Evidence collected")
        assert len(inc.timeline) == 2
        assert inc.timeline[0].event == "detected"

    def test_incident_close(self):
        inc = Incident()
        inc.close("Restarted successfully")
        assert inc.state == IncidentState.CLOSED.value
        assert inc.closed_at is not None
        assert inc.resolution == "Restarted successfully"

    def test_incident_age(self):
        inc = Incident()
        assert inc.age_seconds >= 0

    def test_incident_to_dict(self):
        inc = Incident(host="vps", component="app", severity="critical")
        d = inc.to_dict()
        assert d["host"] == "vps"
        assert d["severity"] == "critical"
        assert "timeline" in d


class TestDetectionResult:
    def test_detection_result(self):
        r = DetectionResult(host="vps")
        assert r.has_anomalies is False
        r.anomalies.append(Anomaly())
        assert r.has_anomalies is True

    def test_detection_result_to_dict(self):
        r = DetectionResult(host="vps")
        d = r.to_dict()
        assert d["host"] == "vps"
        assert d["anomalies"] == 0
