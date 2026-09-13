"""
Tests for app/monitoring/prometheus_collector.py — real Prometheus collector.

All tests mock the HTTP requests. No real Prometheus or VPS access required.
"""
import json
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import requests

from app.monitoring.prometheus_collector import PrometheusCollector, QUERY_TIMEOUT


# ── Helpers ──

def _mock_response(status_code=200, json_data=None, text=""):
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


def _prometheus_query_result(value, labels=None):
    """Format a Prometheus instant query result."""
    labels = labels or {}
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": labels, "value": [time.time(), str(value)]}],
        },
    }


def _prometheus_empty_result():
    return {"status": "success", "data": {"resultType": "vector", "result": []}}


def _prometheus_targets(targets=None):
    """Format a Prometheus targets response."""
    if targets is None:
        targets = [
            {"labels": {"job": "node"}, "health": "up", "lastError": ""},
            {"labels": {"job": "cadvisor"}, "health": "up", "lastError": ""},
        ]
    return {
        "status": "success",
        "data": {"activeTargets": targets},
    }


# ── Availability / readiness ──


class TestPrometheusAvailability:
    def test_is_available_when_ready(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(200)):
            assert collector.is_available() is True

    def test_is_not_available_when_not_ready(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(503)):
            assert collector.is_available() is False

    def test_is_not_available_on_connection_error(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", side_effect=requests.ConnectionError("refused")):
            assert collector.is_available() is False

    def test_is_not_available_on_timeout(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", side_effect=requests.Timeout("timeout")):
            assert collector.is_available() is False

    def test_availability_cached_for_30s(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(200)) as mock_get:
            collector.is_available()
            # Second call should use cached result
            collector.is_available()
            assert mock_get.call_count == 1  # only one HTTP call

    def test_availability_recheck_after_interval(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(200)) as mock_get:
            collector.is_available()
            # Simulate time passing
            collector._last_check_time = time.time() - 60
            collector._available = False  # was down
            # Now should re-check
            with patch.object(collector._session, "get", return_value=_mock_response(200)):
                assert collector.is_available() is True

    def test_name_is_prometheus(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        assert collector.name == "prometheus"


# ── Instant query ──


class TestInstantQuery:
    def test_successful_query(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        result = _prometheus_query_result(42.0, {"instance": "vps"})
        with patch.object(collector._session, "get", return_value=_mock_response(200, result)):
            results = collector.instant_query("up")
            assert results is not None
            assert len(results) == 1
            assert float(results[0]["value"][1]) == 42.0

    def test_query_with_http_error(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(500)):
            results = collector.instant_query("up")
            assert results is None

    def test_query_with_error_status(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(
            200, {"status": "error", "error": "bad query"}
        )):
            results = collector.instant_query("bad{query")
            assert results is None

    def test_query_with_empty_result(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(200, _prometheus_empty_result())):
            results = collector.instant_query("nonexistent_metric")
            assert results == []

    def test_query_with_connection_error(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", side_effect=requests.ConnectionError):
            results = collector.instant_query("up")
            assert results is None

    def test_query_with_malformed_json(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = json.JSONDecodeError("bad", "", 0)
        with patch.object(collector._session, "get", return_value=resp):
            results = collector.instant_query("up")
            assert results is None

    def test_query_truncates_large_results(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        large_result = {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {}, "value": [time.time(), "1"]} for _ in range(600)],
            },
        }
        with patch.object(collector._session, "get", return_value=_mock_response(200, large_result)):
            results = collector.instant_query("up")
            assert len(results) == 500  # MAX_SERIES


# ── Range query ──


class TestRangeQuery:
    def test_successful_range_query(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        result = {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [{
                    "metric": {"instance": "vps"},
                    "values": [[time.time() - 120, "80"], [time.time() - 60, "85"], [time.time(), "90"]],
                }],
            },
        }
        with patch.object(collector._session, "get", return_value=_mock_response(200, result)):
            results = collector.range_query("rate(cpu[5m])", duration_seconds=300)
            assert results is not None
            assert len(results[0]["values"]) == 3

    def test_range_query_with_error(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(500)):
            results = collector.range_query("bad_query")
            assert results is None

    def test_range_query_empty(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        result = {"status": "success", "data": {"resultType": "matrix", "result": []}}
        with patch.object(collector._session, "get", return_value=_mock_response(200, result)):
            results = collector.range_query("nonexistent")
            assert results == []


# ── Targets ──


class TestTargets:
    def test_get_targets(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        targets = [
            {"labels": {"job": "node", "instance": "10.0.0.1:9100"}, "health": "up", "lastError": ""},
            {"labels": {"job": "cadvisor"}, "health": "down", "lastError": "connection refused"},
        ]
        with patch.object(collector._session, "get", return_value=_mock_response(200, _prometheus_targets(targets))):
            result = collector.get_targets()
            assert len(result) == 2
            assert result[0]["health"] == "up"
            assert result[1]["health"] == "down"
            assert "connection refused" in result[1]["last_error"]

    def test_get_targets_error(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector._session, "get", return_value=_mock_response(500)):
            result = collector.get_targets()
            assert result is None


# ── System metrics ──


class TestSystemMetrics:
    def test_returns_empty_when_unavailable(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=False):
            snapshots = collector.get_system_metrics()
            assert snapshots == []

    def test_collects_cpu_memory_disk(self):
        """System metrics are collected via instant_query calls."""
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            # Mock instant_query to return appropriate values based on query content
            def mock_instant_query(query):
                if "cpu_seconds" in query or "cpu" in query and "idle" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "75.0"]}]
                elif "memory" in query and "MemAvailable" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "60.0"]}]
                elif "filesystem" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "45.0"]}]
                elif "load1" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "2.5"]}]
                elif "load5" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "2.0"]}]
                elif "load15" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "1.5"]}]
                elif "cpu_count" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "4"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_system_metrics()
                metric_names = {s.metric_name for s in snapshots}
                assert "cpu_percent" in metric_names
                assert "memory_percent" in metric_names
                assert "disk_percent" in metric_names
                assert "load_1m" in metric_names

    def test_host_label_from_prometheus(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "cpu_seconds" in query or ("cpu" in query and "idle" in query):
                    return [{"metric": {"instance": "prod-vps:9100"}, "value": [time.time(), "50.0"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_system_metrics()
                cpu_snapshots = [s for s in snapshots if s.metric_name == "cpu_percent"]
                assert len(cpu_snapshots) == 1
                assert cpu_snapshots[0].host == "prod-vps:9100"

    def test_host_label_override(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090", host_label="myhost")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "cpu_seconds" in query or ("cpu" in query and "idle" in query):
                    return [{"metric": {"instance": "prod-vps:9100"}, "value": [time.time(), "50.0"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_system_metrics()
                cpu_snapshots = [s for s in snapshots if s.metric_name == "cpu_percent"]
                assert cpu_snapshots[0].host == "myhost"


# ── Container metrics ──


class TestContainerMetrics:
    def test_returns_empty_when_unavailable(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=False):
            assert collector.get_container_metrics() == []

    def test_collects_container_cpu_and_memory(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "cpu_usage" in query:
                    return [
                        {"metric": {"name": "backend", "instance": "vps"}, "value": [time.time(), "12.5"]},
                        {"metric": {"name": "redis", "instance": "vps"}, "value": [time.time(), "2.1"]},
                    ]
                elif "memory" in query and "container" in query:
                    return [
                        {"metric": {"name": "backend", "instance": "vps"}, "value": [time.time(), "68.3"]},
                    ]
                elif "restart" in query:
                    return []
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_container_metrics()
                cpu_snapshots = [s for s in snapshots if s.metric_name == "container_cpu_percent"]
                assert len(cpu_snapshots) == 2
                assert cpu_snapshots[0].component == "backend"
                assert cpu_snapshots[1].component == "redis"


# ── Container states ──


class TestContainerStates:
    def test_returns_empty_when_unavailable(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=False):
            assert collector.get_container_states() == []

    def test_collects_container_states(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        ts = time.time()
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "last_seen" in query:
                    return [
                        {"metric": {"name": "backend"}, "value": [ts, str(ts - 5)]},  # 5s ago → running
                        {"metric": {"name": "old"}, "value": [ts, str(ts - 300)]},  # 5min ago → stopped
                    ]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_container_states()
                assert len(snapshots) == 2
                backend = [s for s in snapshots if s.component == "backend"][0]
                assert backend.value == 1.0  # running
                old = [s for s in snapshots if s.component == "old"][0]
                assert old.value == 0.0  # stopped


# ── Target health ──


class TestTargetHealth:
    def test_get_target_health(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        targets = [
            {"labels": {"job": "node"}, "health": "up", "lastError": ""},
        ]
        with patch.object(collector._session, "get", return_value=_mock_response(200, _prometheus_targets(targets))):
            health = collector.get_target_health()
            assert len(health) == 1
            assert health[0]["job"] == "node"


# ── Network metrics ──


class TestNetworkMetrics:
    def test_returns_empty_when_unavailable(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=False):
            assert collector.get_network_metrics() == []

    def test_collects_network_metrics(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "receive" in query:
                    return [{"metric": {"instance": "vps", "device": "eth0"}, "value": [time.time(), "125000.0"]}]
                elif "transmit" in query:
                    return [{"metric": {"instance": "vps", "device": "eth0"}, "value": [time.time(), "98000.0"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_network_metrics()
                names = {s.metric_name for s in snapshots}
                assert "network_receive_bytes_per_sec" in names
                assert "network_transmit_bytes_per_sec" in names


# ── Log events ──


class TestLogEvents:
    def test_returns_empty_when_unavailable(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=False):
            assert collector.get_log_events() == []

    def test_returns_empty_when_no_log_metrics(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            with patch.object(collector, "instant_query", return_value=None):
                snapshots = collector.get_log_events()
                assert snapshots == []


# ── Scalar extraction ──


class TestScalarExtraction:
    def test_scalar_from_empty_results(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        assert collector._scalar_from_results(None) == 0.0
        assert collector._scalar_from_results([]) == 0.0
        assert collector._scalar_from_results(None, default=-1.0) == -1.0

    def test_scalar_from_valid_result(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        results = [{"value": [time.time(), "42.5"]}]
        assert collector._scalar_from_results(results) == 42.5

    def test_scalar_from_invalid_value(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        results = [{"value": [time.time(), "not_a_number"]}]
        assert collector._scalar_from_results(results) == 0.0

    def test_all_values(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        results = [
            {"value": [time.time(), "10"]},
            {"value": [time.time(), "20"]},
            {"value": [time.time(), "30"]},
        ]
        values = collector._all_values(results)
        assert values == [10.0, 20.0, 30.0]

    def test_all_values_empty(self):
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        assert collector._all_values(None) == []
        assert collector._all_values([]) == []


# ── Integration with monitoring engine ──


class TestPrometheusWithEngine:
    """Test that PrometheusCollector works with the MonitoringEngine."""

    def test_engine_uses_prometheus_collector(self):
        from app.monitoring.engine import MonitoringEngine
        from app.monitoring.thresholds import ThresholdConfig

        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")

        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "cpu_seconds" in query or ("cpu" in query and "idle" in query):
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "50.0"]}]
                elif "memory" in query and "MemAvailable" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "60.0"]}]
                elif "filesystem" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "40.0"]}]
                elif "load" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "1.0"]}]
                elif "cpu_count" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "4"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                config = ThresholdConfig(cooldown_seconds=0)
                engine = MonitoringEngine(collector=collector, config=config)
                result = engine.run_cycle("vps")
                # Should complete without error
                assert result.errors == []
                # Should have collected some metrics
                assert len(result.anomalies) == 0  # normal values, no anomaly

    def test_engine_falls_back_when_prometheus_unavailable(self):
        from app.monitoring.engine import MonitoringEngine
        from app.monitoring.thresholds import ThresholdConfig

        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")

        with patch.object(collector, "is_available", return_value=False):
            config = ThresholdConfig(cooldown_seconds=0)
            engine = MonitoringEngine(collector=collector, config=config)
            result = engine.run_cycle("vps")
            # Engine correctly reports collector unavailable
            assert len(result.errors) == 1
            assert "unavailable" in result.errors[0].lower()
            assert len(result.anomalies) == 0
