"""Tests for Phase 6 integration — security, Phase 5 boundaries, LLM grounding."""
import pytest
from app.monitoring.collector import MockCollector
from app.monitoring.engine import MonitoringEngine
from app.monitoring.models import Severity, IncidentState
from app.monitoring.thresholds import ThresholdConfig
from app.agent import SYSTEM_PROMPT
from app.control.policy import is_action_allowed, BLOCKED_ACTIONS


# ── Security: Phase 6 never mutates ──

class TestPhase6Security:
    """Phase 6 must be observation-only. No mutations allowed."""

    def test_engine_has_no_restart_capability(self):
        """Engine must not have any method that restarts/starts/stops containers."""
        from app.monitoring.engine import MonitoringEngine
        import inspect
        methods = [m for m in dir(MonitoringEngine) if not m.startswith("_")]
        dangerous = {"restart", "stop", "delete", "start", "kill", "exec", "deploy"}
        for method in methods:
            for d in dangerous:
                assert d not in method.lower(), f"Engine has suspicious method: {method}"

    def test_collector_has_no_mutation(self):
        """MockCollector must not have any mutation methods."""
        import inspect
        methods = [m for m in dir(MockCollector) if not m.startswith("_")]
        dangerous = {"restart", "stop", "delete", "start", "kill", "exec", "deploy", "set_"}
        # set_* are allowed on MockCollector for test setup
        strict_dangerous = {"restart", "stop", "delete", "kill", "exec", "deploy"}
        for method in methods:
            for d in strict_dangerous:
                assert d not in method.lower(), f"Collector has suspicious method: {method}"

    def test_engine_only_creates_incidents(self):
        """When engine detects anomaly, it only creates incidents, never mutations."""
        collector = MockCollector()
        collector.set_container_state(host="vps", name="backend", state="exited")
        engine = MonitoringEngine(collector=collector, config=ThresholdConfig(cooldown_seconds=0))
        result = engine.run_cycle("vps")
        # Should create incident, not restart container
        assert len(result.incidents_created) >= 1
        # Container still stopped in collector
        states = collector.get_container_states("vps")
        backend = next(s for s in states if s.component == "backend")
        assert backend.labels["state"] == "exited"


# ── Phase 5 action boundaries ──

class TestPhase5Boundaries:
    """Phase 5 security boundaries must remain intact."""

    def test_only_restart_actions_allowed(self):
        assert is_action_allowed("restart_container") is True
        assert is_action_allowed("restart_service") is True

    def test_dangerous_actions_blocked(self):
        for action in ["rm", "delete", "destroy", "docker_exec",
                       "kubectl_delete", "sudo", "shell_execute", "arbitrary_command"]:
            assert is_action_allowed(action) is False

    def test_blocked_actions_set_unchanged(self):
        assert "rm" in BLOCKED_ACTIONS
        assert "delete" in BLOCKED_ACTIONS
        assert "sudo" in BLOCKED_ACTIONS
        assert "shell_execute" in BLOCKED_ACTIONS
        assert "arbitrary_command" in BLOCKED_ACTIONS


# ── Agent grounding ──

class TestAgentGrounding:
    """System prompt must contain grounding rules preventing hallucination."""

    def test_phase5_ground_truth_exists(self):
        assert "Phase 5 ground truth" in SYSTEM_PROMPT

    def test_phase6_grounding_exists(self):
        assert "Phase 6 monitoring ground truth" in SYSTEM_PROMPT

    def test_no_invent_implementation(self):
        assert "Never invent implementation details" in SYSTEM_PROMPT

    def test_monitoring_readonly(self):
        lower = SYSTEM_PROMPT.lower()
        assert "read-only" in lower or "read only" in lower

    def test_no_http_health_probe(self):
        lower = SYSTEM_PROMPT.lower()
        assert "no http health probing" in lower or "no curl-based" in lower

    def test_no_docker_stop_rollback(self):
        lower = SYSTEM_PROMPT.lower()
        assert "does not stop/recreate" in lower or "does not use docker run" in lower


# ── Intentionally stopped container ──

class TestIntentionallyStoppedContainer:
    """A stopped container should not be automatically treated as critical."""

    def test_stopped_container_creates_warning_not_critical(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="old-job", state="exited")
        engine = MonitoringEngine(collector=collector, config=ThresholdConfig(cooldown_seconds=0))
        result = engine.run_cycle("vps")
        stopped_anomaly = next((a for a in result.anomalies if a.anomaly_type == "container_stopped"), None)
        assert stopped_anomaly is not None
        # Container stopped = WARNING, not CRITICAL (without additional evidence)
        assert stopped_anomaly.severity == "warning"

    def test_stopped_container_with_restart_loop_is_critical(self):
        collector = MockCollector()
        collector.set_container_state(host="vps", name="app", state="exited", restart_count=15)
        engine = MonitoringEngine(collector=collector, config=ThresholdConfig(cooldown_seconds=0))
        result = engine.run_cycle("vps")
        restart_anomaly = next((a for a in result.anomalies if a.anomaly_type == "container_restart_loop"), None)
        assert restart_anomaly is not None
        assert restart_anomaly.severity == "critical"


# ── Tool registry ──

class TestToolRegistryPhase6:
    """Phase 6 tools must be available in the tool registry."""

    def test_monitoring_tools_in_dispatch(self):
        from app.tool_registry import _DISPATCH
        assert "get_monitoring_status" in _DISPATCH
        assert "get_active_incidents" in _DISPATCH
        assert "get_incident_detail" in _DISPATCH
        assert "get_monitoring_summary" in _DISPATCH
        assert "explain_anomaly" in _DISPATCH

    def test_monitoring_tools_in_schemas(self):
        from app.tool_registry import TOOL_SCHEMAS
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert "get_monitoring_status" in names
        assert "get_active_incidents" in names
        assert "get_incident_detail" in names
        assert "get_monitoring_summary" in names
        assert "explain_anomaly" in names

    def test_explain_anomaly_returns_real_data(self):
        from app.tool_registry import execute_tool
        result = execute_tool("explain_anomaly", {"anomaly_type": "cpu_high"})
        assert result["success"] is True
        assert "cpu" in result["explanation"].lower()
        assert "80" in result["explanation"]  # warning threshold

    def test_explain_anomaly_unknown_type(self):
        from app.tool_registry import execute_tool
        result = execute_tool("explain_anomaly", {"anomaly_type": "unknown"})
        assert result["success"] is False

    def test_explain_anomaly_missing_type(self):
        from app.tool_registry import execute_tool
        result = execute_tool("explain_anomaly", {})
        assert result["success"] is False

    def test_monitoring_status_tool(self):
        from app.tool_registry import execute_tool
        result = execute_tool("get_monitoring_status", {})
        assert result["success"] is True
        assert "status" in result

    def test_get_active_incidents_tool(self):
        from app.tool_registry import execute_tool
        result = execute_tool("get_active_incidents", {})
        assert result["success"] is True
        assert "incidents" in result
