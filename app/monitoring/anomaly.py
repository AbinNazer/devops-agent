"""
Deterministic anomaly detection for Phase 6.

Produces Anomaly objects from metric snapshots.
Separates: normal → warning → anomaly → critical anomaly.

Does NOT equate a stopped container with a critical incident —
context matters (duration, restart policy, etc.).
"""
import time
from typing import List, Optional

from app.monitoring.models import (
    Anomaly, AnomalyStatus, ComponentType, MetricSnapshot, Severity,
)
from app.monitoring.thresholds import ThresholdConfig, check_threshold


def detect_system_anomalies(snapshots: List[MetricSnapshot],
                            config: Optional[ThresholdConfig] = None) -> List[Anomaly]:
    """Detect system-level anomalies from metric snapshots."""
    config = config or ThresholdConfig()
    anomalies = []
    by_host: dict = {}
    for s in snapshots:
        by_host.setdefault(s.host, {})[s.metric_name] = s

    for host, metrics in by_host.items():
        # CPU
        if "cpu_percent" in metrics:
            r = check_threshold(metrics["cpu_percent"].value, config.cpu_warning,
                                config.cpu_critical, "cpu_percent")
            if r.breached:
                anomalies.append(Anomaly(
                    host=host, component="system", component_type=ComponentType.SYSTEM.value,
                    anomaly_type="cpu_high", metric_name="cpu_percent",
                    severity=Severity.CRITICAL.value if r.level == "critical" else Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.critical_threshold if r.level == "critical" else r.warning_threshold,
                    message=f"CPU at {r.value:.1f}% ({r.level})",
                    evidence=[f"CPU {r.value:.1f}% exceeds {r.level} threshold"],
                ))

        # Memory
        if "memory_percent" in metrics:
            r = check_threshold(metrics["memory_percent"].value, config.memory_warning,
                                config.memory_critical, "memory_percent")
            if r.breached:
                anomalies.append(Anomaly(
                    host=host, component="system", component_type=ComponentType.SYSTEM.value,
                    anomaly_type="memory_high", metric_name="memory_percent",
                    severity=Severity.CRITICAL.value if r.level == "critical" else Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.critical_threshold if r.level == "critical" else r.warning_threshold,
                    message=f"Memory at {r.value:.1f}% ({r.level})",
                    evidence=[f"Memory {r.value:.1f}% exceeds {r.level} threshold"],
                ))

        # Disk
        if "disk_percent" in metrics:
            r = check_threshold(metrics["disk_percent"].value, config.disk_warning,
                                config.disk_critical, "disk_percent")
            if r.breached:
                anomalies.append(Anomaly(
                    host=host, component="system", component_type=ComponentType.SYSTEM.value,
                    anomaly_type="disk_high", metric_name="disk_percent",
                    severity=Severity.CRITICAL.value if r.level == "critical" else Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.critical_threshold if r.level == "critical" else r.warning_threshold,
                    message=f"Disk at {r.value:.1f}% ({r.level})",
                    evidence=[f"Disk {r.value:.1f}% exceeds {r.level} threshold"],
                ))

        # Load average per core
        if "load_1m" in metrics and "cpu_cores" in metrics:
            cores = max(metrics["cpu_cores"].value, 1.0)
            load_per_core = metrics["load_1m"].value / cores
            r = check_threshold(load_per_core, config.load_warning_per_core,
                                config.load_critical_per_core, "load_per_core")
            if r.breached:
                anomalies.append(Anomaly(
                    host=host, component="system", component_type=ComponentType.SYSTEM.value,
                    anomaly_type="load_high", metric_name="load_per_core",
                    severity=Severity.CRITICAL.value if r.level == "critical" else Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.critical_threshold if r.level == "critical" else r.warning_threshold,
                    message=f"Load per core {r.value:.2f} ({r.level})",
                    evidence=[f"Load per core {r.value:.2f} exceeds {r.level} threshold (cores={int(cores)})"],
                ))

    return anomalies


def detect_container_anomalies(snapshots: List[MetricSnapshot],
                               config: Optional[ThresholdConfig] = None) -> List[Anomaly]:
    """Detect container-level anomalies from metric snapshots."""
    config = config or ThresholdConfig()
    anomalies = []
    by_host_container: dict = {}
    for s in snapshots:
        key = (s.host, s.component)
        by_host_container.setdefault(key, {})[s.metric_name] = s

    for (host, container), metrics in by_host_container.items():
        # Container state
        if "container_state" in metrics:
            state_label = metrics["container_state"].labels.get("state", "unknown")
            if state_label not in ("running", "created", "paused"):
                anomalies.append(Anomaly(
                    host=host, component=container, component_type=ComponentType.DOCKER.value,
                    anomaly_type="container_stopped", metric_name="container_state",
                    severity=Severity.WARNING.value,
                    current_value=0.0, threshold_value=1.0,
                    message=f"Container '{container}' is {state_label}",
                    evidence=[f"Container state: {state_label}"],
                    labels={"state": state_label},
                ))

        # Container health
        if "container_health" in metrics:
            health_label = metrics["container_health"].labels.get("health", "unknown")
            if health_label == "unhealthy":
                anomalies.append(Anomaly(
                    host=host, component=container, component_type=ComponentType.DOCKER.value,
                    anomaly_type="container_unhealthy", metric_name="container_health",
                    severity=Severity.WARNING.value,
                    current_value=0.0, threshold_value=1.0,
                    message=f"Container '{container}' is unhealthy",
                    evidence=[f"Health status: unhealthy"],
                    labels={"health": health_label},
                ))

        # Container restart count
        if "container_restart_count" in metrics:
            count = int(metrics["container_restart_count"].value)
            if count >= config.container_restart_critical:
                anomalies.append(Anomaly(
                    host=host, component=container, component_type=ComponentType.DOCKER.value,
                    anomaly_type="container_restart_loop", metric_name="container_restart_count",
                    severity=Severity.CRITICAL.value,
                    current_value=float(count), threshold_value=float(config.container_restart_critical),
                    message=f"Container '{container}' restarted {count} times (critical)",
                    evidence=[f"Restart count: {count} (critical threshold: {config.container_restart_critical})"],
                ))
            elif count >= config.container_restart_warning:
                anomalies.append(Anomaly(
                    host=host, component=container, component_type=ComponentType.DOCKER.value,
                    anomaly_type="container_restarting", metric_name="container_restart_count",
                    severity=Severity.WARNING.value,
                    current_value=float(count), threshold_value=float(config.container_restart_warning),
                    message=f"Container '{container}' restarted {count} times",
                    evidence=[f"Restart count: {count} (warning threshold: {config.container_restart_warning})"],
                ))

        # Container CPU spike
        if "container_cpu_percent" in metrics:
            r = check_threshold(metrics["container_cpu_percent"].value,
                                config.container_cpu_warning, 100.0, "container_cpu")
            if r.breached:
                anomalies.append(Anomaly(
                    host=host, component=container, component_type=ComponentType.DOCKER.value,
                    anomaly_type="container_cpu_spike", metric_name="container_cpu_percent",
                    severity=Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.warning_threshold,
                    message=f"Container '{container}' CPU at {r.value:.1f}%",
                    evidence=[f"Container CPU {r.value:.1f}% exceeds {config.container_cpu_warning}%"],
                ))

        # Container memory spike
        if "container_memory_percent" in metrics:
            r = check_threshold(metrics["container_memory_percent"].value,
                                config.container_memory_warning, 100.0, "container_memory")
            if r.breached:
                anomalies.append(Anomaly(
                    host=host, component=container, component_type=ComponentType.DOCKER.value,
                    anomaly_type="container_memory_spike", metric_name="container_memory_percent",
                    severity=Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.warning_threshold,
                    message=f"Container '{container}' memory at {r.value:.1f}%",
                    evidence=[f"Container memory {r.value:.1f}% exceeds {config.container_memory_warning}%"],
                ))

    return anomalies


def detect_service_anomalies(snapshots: List[MetricSnapshot],
                             config: Optional[ThresholdConfig] = None) -> List[Anomaly]:
    """Detect systemd service anomalies."""
    anomalies = []
    for s in snapshots:
        if s.metric_name == "service_active":
            active = s.labels.get("active", "True") == "True"
            failed = s.labels.get("failed", "False") == "True"
            if failed:
                anomalies.append(Anomaly(
                    host=s.host, component=s.component, component_type=ComponentType.SERVICE.value,
                    anomaly_type="service_failed", metric_name="service_active",
                    severity=Severity.CRITICAL.value,
                    current_value=0.0, threshold_value=1.0,
                    message=f"Service '{s.component}' has failed",
                    evidence=[f"Service failed: active={active}, failed={failed}"],
                ))
            elif not active:
                anomalies.append(Anomaly(
                    host=s.host, component=s.component, component_type=ComponentType.SERVICE.value,
                    anomaly_type="service_stopped", metric_name="service_active",
                    severity=Severity.WARNING.value,
                    current_value=0.0, threshold_value=1.0,
                    message=f"Service '{s.component}' is not active",
                    evidence=[f"Service inactive: active={active}"],
                ))
    return anomalies


def detect_log_anomalies(snapshots: List[MetricSnapshot],
                         config: Optional[ThresholdConfig] = None) -> List[Anomaly]:
    """Detect log-based anomalies (error spikes, OOM, crash indicators)."""
    config = config or ThresholdConfig()
    anomalies = []
    for s in snapshots:
        if s.metric_name == "log_error_count":
            count = int(s.value)
            r = check_threshold(count, config.log_error_warning, config.log_error_critical, "log_errors")
            if r.breached:
                anomalies.append(Anomaly(
                    host=s.host, component=s.component, component_type=ComponentType.LOG.value,
                    anomaly_type="error_spike", metric_name="log_error_count",
                    severity=Severity.CRITICAL.value if r.level == "critical" else Severity.WARNING.value,
                    current_value=r.value, threshold_value=r.critical_threshold if r.level == "critical" else r.warning_threshold,
                    message=f"Error spike: {count} errors in window",
                    evidence=[f"{count} errors detected (threshold: {r.warning_threshold}/{r.critical_threshold})"],
                ))
    return anomalies


def detect_oom_indicators(log_events: List[dict], host: str = "", component: str = "") -> List[Anomaly]:
    """Detect OOM indicators from raw log events."""
    anomalies = []
    oom_count = sum(1 for e in log_events if "oom" in e.get("message", "").lower()
                    or "out of memory" in e.get("message", "").lower())
    if oom_count > 0:
        anomalies.append(Anomaly(
            host=host, component=component, component_type=ComponentType.LOG.value,
            anomaly_type="oom_detected", metric_name="oom_count",
            severity=Severity.CRITICAL.value,
            current_value=float(oom_count), threshold_value=1.0,
            message=f"OOM detected: {oom_count} occurrences",
            evidence=[f"OOM indicators found in {component} logs"],
        ))
    return anomalies


def detect_crash_indicators(log_events: List[dict], host: str = "", component: str = "") -> List[Anomaly]:
    """Detect panic/fatal/segfault indicators from raw log events."""
    anomalies = []
    indicators = ["panic", "fatal", "segfault", "killed", "core dumped"]
    found = []
    for e in log_events:
        msg = e.get("message", "").lower()
        for ind in indicators:
            if ind in msg:
                found.append(ind)
    if found:
        severity = Severity.CRITICAL.value if len(found) >= 2 else Severity.WARNING.value
        anomalies.append(Anomaly(
            host=host, component=component, component_type=ComponentType.LOG.value,
            anomaly_type="crash_indicator", metric_name="crash_indicators",
            severity=severity,
            current_value=float(len(found)), threshold_value=1.0,
            message=f"Crash indicators found: {', '.join(set(found))}",
            evidence=[f"Found indicators: {', '.join(set(found))}"],
        ))
    return anomalies
