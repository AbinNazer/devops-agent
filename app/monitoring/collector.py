"""
MockCollector — deterministic, injectable monitoring data source for testing.

No network calls. No real infrastructure. Data is set programmatically
before each test run so assertions are fully deterministic.
"""
import time
from typing import Dict, List, Optional

from app.monitoring.interfaces import MonitoringCollector
from app.monitoring.models import MetricSnapshot, MetricKind, ComponentType


class MockCollector(MonitoringCollector):
    """
    Deterministic mock collector for testing the monitoring engine.

    Usage in tests:
        collector = MockCollector()
        collector.set_system_metrics(host="vps", cpu=92.0, memory=85.0, disk=70.0)
        collector.set_container_state(host="vps", name="backend", state="running")
        collector.set_log_events(host="vps", component="backend", events=[
            {"message": "ERROR connection refused", "severity": "error"},
        ])
    """

    def __init__(self):
        self._system_metrics: Dict[str, Dict] = {}
        self._container_states: Dict[str, Dict[str, Dict]] = {}
        self._container_health: Dict[str, Dict[str, Dict]] = {}
        self._container_resources: Dict[str, Dict[str, Dict]] = {}
        self._service_states: Dict[str, Dict[str, Dict]] = {}
        self._log_events: Dict[str, List[Dict]] = {}
        self._available = True
        self._collect_count = 0

    @property
    def name(self) -> str:
        return "mock"

    def is_available(self) -> bool:
        return self._available

    def set_available(self, available: bool) -> None:
        self._available = available

    # ── System metrics ──

    def set_system_metrics(self, host: str = "default", cpu: float = 0.0,
                           memory: float = 0.0, disk: float = 0.0,
                           load_1m: float = 0.0, load_5m: float = 0.0,
                           load_15m: float = 0.0, cpu_cores: int = 4,
                           memory_total_mb: float = 8192.0,
                           disk_total_gb: float = 50.0) -> None:
        self._system_metrics[host] = {
            "cpu_percent": cpu, "memory_percent": memory, "disk_percent": disk,
            "load_1m": load_1m, "load_5m": load_5m, "load_15m": load_15m,
            "cpu_cores": cpu_cores, "memory_total_mb": memory_total_mb,
            "disk_total_gb": disk_total_gb,
        }

    def get_system_metrics(self, host: str = "") -> List[MetricSnapshot]:
        self._collect_count += 1
        hosts = [host] if host and host in self._system_metrics else list(self._system_metrics.keys())
        snapshots = []
        for h in hosts:
            m = self._system_metrics[h]
            ts = time.time()
            snapshots.extend([
                MetricSnapshot("cpu_percent", m["cpu_percent"], ts, h, "system", unit="percent"),
                MetricSnapshot("memory_percent", m["memory_percent"], ts, h, "system", unit="percent"),
                MetricSnapshot("disk_percent", m["disk_percent"], ts, h, "system", unit="percent"),
                MetricSnapshot("load_1m", m["load_1m"], ts, h, "system"),
                MetricSnapshot("load_5m", m["load_5m"], ts, h, "system"),
                MetricSnapshot("load_15m", m["load_15m"], ts, h, "system"),
                MetricSnapshot("cpu_cores", float(m["cpu_cores"]), ts, h, "system"),
            ])
        return snapshots

    # ── Container states ──

    def set_container_state(self, host: str = "default", name: str = "app",
                            state: str = "running", health: str = "no_healthcheck",
                            restart_count: int = 0, cpu_percent: float = 0.0,
                            memory_percent: float = 0.0) -> None:
        if host not in self._container_states:
            self._container_states[host] = {}
        self._container_states[host][name] = {"state": state, "health": health}
        if host not in self._container_resources:
            self._container_resources[host] = {}
        self._container_resources[host][name] = {
            "restart_count": restart_count, "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
        }
        if host not in self._container_health:
            self._container_health[host] = {}
        self._container_health[host][name] = {"health": health}

    def get_container_states(self, host: str = "") -> List[MetricSnapshot]:
        self._collect_count += 1
        hosts = [host] if host and host in self._container_states else list(self._container_states.keys())
        snapshots = []
        for h in hosts:
            ts = time.time()
            for name, info in self._container_states[h].items():
                state_val = 1.0 if info["state"] == "running" else 0.0
                snapshots.append(MetricSnapshot(
                    "container_state", state_val, ts, h, name,
                    labels={"state": info["state"]},
                ))
        return snapshots

    def get_container_health(self, host: str = "") -> List[MetricSnapshot]:
        self._collect_count += 1
        hosts = [host] if host and host in self._container_health else list(self._container_health.keys())
        snapshots = []
        for h in hosts:
            ts = time.time()
            for name, info in self._container_health[h].items():
                health_val = 1.0 if info["health"] == "healthy" else (
                    0.5 if info["health"] == "no_healthcheck" else 0.0
                )
                snapshots.append(MetricSnapshot(
                    "container_health", health_val, ts, h, name,
                    labels={"health": info["health"]},
                ))
        return snapshots

    def get_container_metrics(self, host: str = "") -> List[MetricSnapshot]:
        self._collect_count += 1
        hosts = [host] if host and host in self._container_resources else list(self._container_resources.keys())
        snapshots = []
        for h in hosts:
            ts = time.time()
            for name, res in self._container_resources[h].items():
                snapshots.extend([
                    MetricSnapshot("container_restart_count", float(res["restart_count"]),
                                   ts, h, name, labels={"container": name}),
                    MetricSnapshot("container_cpu_percent", res["cpu_percent"],
                                   ts, h, name, unit="percent"),
                    MetricSnapshot("container_memory_percent", res["memory_percent"],
                                   ts, h, name, unit="percent"),
                ])
        return snapshots

    # ── Service states ──

    def set_service_state(self, host: str = "default", name: str = "nginx",
                          active: bool = True, failed: bool = False) -> None:
        if host not in self._service_states:
            self._service_states[host] = {}
        self._service_states[host][name] = {"active": active, "failed": failed}

    def get_service_states(self, host: str = "") -> List[MetricSnapshot]:
        self._collect_count += 1
        hosts = [host] if host and host in self._service_states else list(self._service_states.keys())
        snapshots = []
        for h in hosts:
            ts = time.time()
            for name, info in self._service_states[h].items():
                active_val = 1.0 if info["active"] else 0.0
                snapshots.append(MetricSnapshot(
                    "service_active", active_val, ts, h, name,
                    labels={"active": str(info["active"]), "failed": str(info["failed"])},
                ))
        return snapshots

    # ── Log events ──

    def set_log_events(self, host: str = "default", component: str = "",
                       events: Optional[List[Dict]] = None) -> None:
        key = f"{host}:{component}"
        self._log_events[key] = events or []

    def get_log_events(self, host: str = "", since_seconds: int = 300) -> List[MetricSnapshot]:
        self._collect_count += 1
        snapshots = []
        ts = time.time()
        for key, events in self._log_events.items():
            parts = key.split(":", 1)
            evt_host = parts[0] if len(parts) > 1 else ""
            evt_comp = parts[1] if len(parts) > 1 else parts[0]
            if host and evt_host != host:
                continue
            error_count = sum(1 for e in events if e.get("severity") in ("error", "fatal", "panic"))
            warn_count = sum(1 for e in events if e.get("severity") == "warning")
            if error_count > 0:
                snapshots.append(MetricSnapshot(
                    "log_error_count", float(error_count), ts, evt_host, evt_comp,
                    labels={"component": evt_comp},
                ))
            if warn_count > 0:
                snapshots.append(MetricSnapshot(
                    "log_warning_count", float(warn_count), ts, evt_host, evt_comp,
                    labels={"component": evt_comp},
                ))
        return snapshots

    def get_log_details(self, host: str = "", component: str = "") -> List[Dict]:
        """Return raw log event dicts for a component."""
        key = f"{host}:{component}"
        return list(self._log_events.get(key, []))
