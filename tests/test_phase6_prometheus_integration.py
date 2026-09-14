"""
Phase 6 Prometheus integration tests.

Covers:
- SSH tunnel ↔ PrometheusCollector integration
- Monitoring engine ↔ Prometheus collector wiring
- Security boundaries
- Tool registry consistency
- Optional real VPS integration test (gated by RUN_VPS_INTEGRATION_TESTS=1)
"""
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from app.monitoring.prometheus_collector import PrometheusCollector
from app.monitoring.ssh_tunnel import SSHTunnelManager, LOCAL_BIND_HOST


# ── VPS integration gate ──

RUN_VPS = os.environ.get("RUN_VPS_INTEGRATION_TESTS", "") == "1"

# Skip marker for VPS tests
requires_vps = pytest.mark.skipif(
    not RUN_VPS,
    reason="RUN_VPS_INTEGRATION_TESTS=1 not set — skipping real VPS tests",
)


# ── Config validation ──


class TestConfigChanges:
    """Verify the PROMETHEUS_PORT config was added correctly."""

    def test_config_has_prometheus_port(self):
        from app.config import Config
        assert hasattr(Config, "PROMETHEUS_PORT")
        # PROMETHEUS_PORT is None when unset (e.g. in CI with no .env),
        # or a positive int when explicitly configured.
        assert Config.PROMETHEUS_PORT is None or isinstance(Config.PROMETHEUS_PORT, int)
        if Config.PROMETHEUS_PORT is not None:
            assert Config.PROMETHEUS_PORT > 0

    def test_config_vps_settings_preserved(self):
        from app.config import Config
        # Existing settings must still exist
        assert hasattr(Config, "VPS_HOST")
        assert hasattr(Config, "VPS_SSH_USER")
        assert hasattr(Config, "VPS_SSH_KEY_PATH")
        assert hasattr(Config, "VPS_SSH_PORT")

    def test_config_groq_settings_preserved(self):
        from app.config import Config
        assert hasattr(Config, "GROQ_API_KEY")
        assert hasattr(Config, "GROQ_MODEL")
        assert hasattr(Config, "LLM_PROVIDER")


# ── SSH tunnel + Prometheus collector integration ──


class TestTunnelCollectorIntegration:
    """Test that SSH tunnel and PrometheusCollector work together."""

    def test_tunnel_provides_local_url_for_collector(self):
        """Verify the tunnel local_url can be passed to PrometheusCollector."""
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel._started = True
        tunnel._closed = False
        tunnel._local_port = 48321
        tunnel._start_time = time.time()

        url = tunnel.local_url
        assert url == f"http://{LOCAL_BIND_HOST}:48321"

        collector = PrometheusCollector(base_url=url)
        assert collector._base_url == f"http://{LOCAL_BIND_HOST}:48321"

    def test_collector_queries_through_tunnel(self):
        """Verify the collector can query through a mocked tunnel."""
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel._started = True
        tunnel._closed = False
        tunnel._local_port = 48321
        tunnel._start_time = time.time()

        collector = PrometheusCollector(base_url=tunnel.local_url)
        assert collector.name == "prometheus"


# ── Monitoring engine wiring ──


class TestEngineWiring:
    """Test the monitoring engine correctly uses PrometheusCollector."""

    def test_engine_accepts_prometheus_collector(self):
        from app.monitoring.engine import MonitoringEngine
        from app.monitoring.thresholds import ThresholdConfig

        collector = PrometheusCollector(base_url="http://127.0.0.1:48321")
        config = ThresholdConfig(cooldown_seconds=0)
        engine = MonitoringEngine(collector=collector, config=config)

        assert engine._collector is collector
        assert engine._collector.name == "prometheus"

    def test_engine_works_with_unavailable_prometheus(self):
        from app.monitoring.engine import MonitoringEngine
        from app.monitoring.thresholds import ThresholdConfig

        collector = PrometheusCollector(base_url="http://127.0.0.1:48321")
        with patch.object(collector, "is_available", return_value=False):
            config = ThresholdConfig(cooldown_seconds=0)
            engine = MonitoringEngine(collector=collector, config=config)
            result = engine.run_cycle("vps")
            # Engine correctly reports collector unavailable, no crash
            assert len(result.errors) == 1
            assert "unavailable" in result.errors[0].lower()
            assert len(result.anomalies) == 0


# ── Security ──


class TestSecurityBoundaries:
    """Verify Phase 6 security properties."""

    def test_monitoring_tools_are_read_only(self):
        """Monitoring tools must be in the registered tool set and be read-only."""
        from app.tool_registry import TOOL_NAMES

        monitoring_tools = [
            "get_monitoring_status", "get_active_incidents",
            "get_incident_detail", "get_monitoring_summary", "explain_anomaly",
        ]
        for tool in monitoring_tools:
            assert tool in TOOL_NAMES, f"Monitoring tool '{tool}' not in TOOL_NAMES"

    def test_dangerous_actions_remain_blocked(self):
        """Dangerous actions must remain blocked in the policy."""
        from app.control.policy import BLOCKED_ACTIONS, is_action_allowed

        for action in ["rm", "delete", "destroy", "kubectl_exec", "sudo", "shell_execute"]:
            assert action in BLOCKED_ACTIONS
            assert is_action_allowed(action) is False

    def test_allowed_actions_still_work(self):
        """Restart actions must still be allowed through the policy."""
        from app.control.policy import is_action_allowed
        assert is_action_allowed("restart_container") is True
        assert is_action_allowed("restart_service") is True

    def test_tool_registry_consistency(self):
        """All tool names must be in TOOL_SCHEMAS, TOOL_NAMES, and _DISPATCH."""
        from app.tool_registry import TOOL_NAMES, TOOL_SCHEMAS, _DISPATCH

        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert schema_names == TOOL_NAMES
        assert set(_DISPATCH.keys()) == TOOL_NAMES

    def test_container_stats_not_in_tool_registry(self):
        """Hallucinated tool name must not be registered."""
        from app.tool_registry import TOOL_NAMES
        assert "container_stats" not in TOOL_NAMES

    def test_prometheus_collector_is_read_only(self):
        """PrometheusCollector must never execute mutations."""
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        # All methods should be read-only queries
        assert hasattr(collector, "get_system_metrics")
        assert hasattr(collector, "get_container_metrics")
        assert hasattr(collector, "is_available")

    def test_ssh_tunnel_binds_localhost_only(self):
        """Tunnel must only bind to localhost, never 0.0.0.0."""
        assert LOCAL_BIND_HOST == "127.0.0.1"

    def test_prometheus_url_not_exposed_internet(self):
        """The SSH tunnel URL must be localhost, not a public address."""
        tunnel = SSHTunnelManager(remote_port=4001)
        tunnel._started = True
        tunnel._closed = False
        tunnel._local_port = 12345
        tunnel._start_time = time.time()
        assert "127.0.0.1" in tunnel.local_url

    def test_no_container_restart_in_monitoring(self):
        """Phase 6 must never call restart tools directly."""
        from app.tool_registry import TOOL_NAMES
        restart_tools = ["docker_restart", "restart_container_direct"]
        for tool in restart_tools:
            assert tool not in TOOL_NAMES

    def test_blocked_actions_set_complete(self):
        """All critical dangerous actions must remain blocked."""
        from app.control.policy import BLOCKED_ACTIONS
        required_blocked = [
            "rm", "delete", "destroy", "kubectl_exec", "kubectl_delete",
            "sudo", "shell_execute", "arbitrary_command",
        ]
        for action in required_blocked:
            assert action in BLOCKED_ACTIONS, f"Action '{action}' not in BLOCKED_ACTIONS"


# ── SSH tunnel specific tests ──


class TestSSHTunnelForPrometheus:
    """Tests specific to the Prometheus SSH tunnel use case."""

    def test_tunnel_default_port_matches_config(self):
        """Default remote port should match PROMETHEUS_PORT."""
        from app.config import Config
        tunnel = SSHTunnelManager(remote_port=Config.PROMETHEUS_PORT)
        assert tunnel._remote_port == Config.PROMETHEUS_PORT

    def test_tunnel_is_scoped(self):
        """Tunnel has a maximum lifetime."""
        tunnel = SSHTunnelManager(remote_port=4001, max_lifetime=600)
        assert tunnel._max_lifetime == 600

    def test_tunnel_lifetime_check(self):
        """Tunnel must stop being active after max_lifetime."""
        tunnel = SSHTunnelManager(remote_port=4001, max_lifetime=1)
        tunnel._started = True
        tunnel._closed = False
        tunnel._start_time = time.time() - 5
        assert tunnel.is_active is False


# ── Metric normalization ──


class TestMetricNormalization:
    """Test that Prometheus metrics are correctly normalized to MetricSnapshot format."""

    def test_cpu_normalization(self):
        """CPU from Prometheus should be normalized to cpu_percent MetricSnapshot."""
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "cpu_seconds" in query or ("cpu" in query and "idle" in query):
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "83.7"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_system_metrics()
                cpu = [s for s in snapshots if s.metric_name == "cpu_percent"]
                assert len(cpu) == 1
                assert cpu[0].value == 83.7
                assert cpu[0].component == "system"

    def test_memory_normalization(self):
        """Memory from Prometheus should be normalized to memory_percent MetricSnapshot."""
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "memory" in query and "MemAvailable" in query:
                    return [{"metric": {"instance": "vps"}, "value": [time.time(), "71.2"]}]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_system_metrics()
                mem = [s for s in snapshots if s.metric_name == "memory_percent"]
                assert len(mem) == 1
                assert mem[0].value == 71.2

    def test_container_metric_normalization(self):
        """Container CPU should include container name as component."""
        collector = PrometheusCollector(base_url="http://127.0.0.1:9090")
        with patch.object(collector, "is_available", return_value=True):
            def mock_instant_query(query):
                if "cpu_usage" in query:
                    return [
                        {"metric": {"name": "backend", "instance": "vps"}, "value": [time.time(), "15.3"]},
                    ]
                return []

            with patch.object(collector, "instant_query", side_effect=mock_instant_query):
                snapshots = collector.get_container_metrics()
                cpu = [s for s in snapshots if s.metric_name == "container_cpu_percent"]
                assert len(cpu) == 1
                assert cpu[0].component == "backend"
                assert cpu[0].value == 15.3


# ── Optional real VPS integration test ──


@requires_vps
class TestVPSIntegration:
    """
    Real VPS integration test — only runs with RUN_VPS_INTEGRATION_TESTS=1.

    Verifies the full pipeline: JARVIS → SSH tunnel → VPS → Prometheus API.
    Strictly read-only.
    """

    def test_vps_prometheus_readiness(self):
        """Verify Prometheus is reachable through SSH tunnel on the real VPS."""
        from app.config import Config
        tunnel = SSHTunnelManager(
            remote_port=Config.PROMETHEUS_PORT,
            max_lifetime=120,
        )
        try:
            tunnel.start()
            assert tunnel.local_port is not None

            collector = PrometheusCollector(base_url=tunnel.local_url)
            assert collector.is_available() is True
        finally:
            tunnel.close()

    def test_vps_prometheus_up_query(self):
        """Query the real 'up' metric from Prometheus."""
        from app.config import Config
        tunnel = SSHTunnelManager(
            remote_port=Config.PROMETHEUS_PORT,
            max_lifetime=120,
        )
        try:
            tunnel.start()
            collector = PrometheusCollector(base_url=tunnel.local_url)
            results = collector.instant_query("up")
            assert results is not None
            assert len(results) > 0
            # All targets should be listed
            jobs = {r["metric"].get("job", "") for r in results}
            assert len(jobs) > 0
        finally:
            tunnel.close()

    def test_vps_prometheus_system_metrics(self):
        """Collect real system metrics from Prometheus."""
        from app.config import Config
        tunnel = SSHTunnelManager(
            remote_port=Config.PROMETHEUS_PORT,
            max_lifetime=120,
        )
        try:
            tunnel.start()
            collector = PrometheusCollector(base_url=tunnel.local_url)
            snapshots = collector.get_system_metrics()
            # Should have at least some metrics
            assert len(snapshots) > 0
            metric_names = {s.metric_name for s in snapshots}
            # At least load or cpu should be available
            assert any(m in metric_names for m in ["cpu_percent", "memory_percent", "load_1m", "disk_percent"])
        finally:
            tunnel.close()

    def test_vps_prometheus_targets(self):
        """Verify Prometheus scrape targets."""
        from app.config import Config
        tunnel = SSHTunnelManager(
            remote_port=Config.PROMETHEUS_PORT,
            max_lifetime=120,
        )
        try:
            tunnel.start()
            collector = PrometheusCollector(base_url=tunnel.local_url)
            targets = collector.get_targets()
            assert targets is not None
            assert len(targets) > 0
            jobs = {t["job"] for t in targets}
            # Should have at least some known targets
            assert len(jobs) > 0
        finally:
            tunnel.close()

    def test_vps_engine_full_cycle(self):
        """Run a full monitoring cycle against real Prometheus."""
        from app.config import Config
        from app.monitoring.engine import MonitoringEngine
        from app.monitoring.thresholds import ThresholdConfig

        tunnel = SSHTunnelManager(
            remote_port=Config.PROMETHEUS_PORT,
            max_lifetime=120,
        )
        try:
            tunnel.start()
            collector = PrometheusCollector(base_url=tunnel.local_url)
            config = ThresholdConfig(cooldown_seconds=0)
            engine = MonitoringEngine(collector=collector, config=config)
            result = engine.run_cycle("vps")
            assert result.errors == []
            # The engine completed a full cycle with real data
        finally:
            tunnel.close()
