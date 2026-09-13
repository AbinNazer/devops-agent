"""
PrometheusCollector — real Prometheus integration for Phase 6.

Collects metrics from a Prometheus instance via its HTTP API, normalizing
them into MetricSnapshot objects that the monitoring engine already
understands.

Architecture:
  PrometheusCollector → SSH tunnel → VPS localhost:4001 → Prometheus API

Queries used:
  - Instant queries:  /api/v1/query?query=<promql>
  - Range queries:    /api/v1/query_range?query=<promql>&start=&end=&step=
  - Readiness:        /-/ready
  - Targets:          /api/v1/targets

Metrics supported:
  - CPU (node_cpu_seconds_total → percent via cAdvisor/node-exporter)
  - Memory (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)
  - Disk (node_filesystem_avail_bytes / node_filesystem_size_bytes)
  - Load (node_load1, node_load5, node_load15)
  - CPU cores (count(cpu_count or node_cpu))
  - Container CPU (container_cpu_usage_seconds_total via cAdvisor)
  - Container memory (container_memory_usage_bytes via cAdvisor)
  - Container state (container_last_seen, container_running via cAdvisor)
  - Container restarts (container_start_time_seconds via cAdvisor)
  - Network (node_network_receive_bytes_total, node_network_transmit_bytes_total)
  - Prometheus target health

If Prometheus is unreachable or a query fails, UNKNOWN values are returned
instead of fabricated data.

Security:
  - Never exposes Prometheus publicly
  - All queries go through the SSH tunnel
  - No direct internet access to Prometheus
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

from app.monitoring.interfaces import MonitoringCollector
from app.monitoring.models import MetricSnapshot

logger = logging.getLogger("prometheus_collector")

# Query timeout in seconds
QUERY_TIMEOUT = 10

# Maximum number of series to process per query (safety bound)
MAX_SERIES = 500


class PrometheusCollector(MonitoringCollector):
    """
    Real Prometheus collector for Phase 6.

    Collects infrastructure metrics via the Prometheus HTTP API through
    an SSH tunnel to the VPS-local Prometheus instance.
    """

    def __init__(self, base_url: str, host_label: str = ""):
        """
        Args:
            base_url: The full base URL for the Prometheus API, e.g.
                "http://127.0.0.1:48321" (local port from SSH tunnel).
            host_label: Optional host label to tag collected metrics with.
                If empty, metrics are tagged with the host from Prometheus labels.
        """
        self._base_url = base_url.rstrip("/")
        self._host_label = host_label
        self._available: Optional[bool] = None  # None = not checked yet
        self._last_check_time: float = 0.0
        self._check_interval: float = 30.0  # re-check readiness every 30s
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    @property
    def name(self) -> str:
        return "prometheus"

    def is_available(self) -> bool:
        """Check if Prometheus is reachable and ready."""
        now = time.time()
        # Cache the result for a short period to avoid hammering the readiness endpoint
        if self._available is not None and (now - self._last_check_time) < self._check_interval:
            return self._available

        try:
            resp = self._session.get(
                f"{self._base_url}/-/ready",
                timeout=QUERY_TIMEOUT,
            )
            self._available = resp.status_code == 200
            self._last_check_time = now
            if self._available:
                logger.info("prometheus_available url=%s", self._base_url)
            else:
                logger.warning("prometheus_not_ready status=%d", resp.status_code)
        except requests.exceptions.RequestException as e:
            self._available = False
            self._last_check_time = now
            logger.warning("prometheus_unavailable error=%s", type(e).__name__)

        return self._available

    # ── Query primitives ──

    def instant_query(self, query: str) -> Optional[List[Dict]]:
        """
        Execute an instant PromQL query.

        Returns a list of result series, each with 'metric' (labels) and
        'value' (scalar value). Returns None on failure.
        """
        try:
            resp = self._session.get(
                f"{self._base_url}/api/v1/query",
                params={"query": query},
                timeout=QUERY_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("prometheus_query_failed status=%d query=%s", resp.status_code, query[:100])
                return None
            data = resp.json()
            if data.get("status") != "success":
                logger.warning("prometheus_query_error query=%s error=%s", query[:100], data.get("error"))
                return None
            results = data.get("data", {}).get("result", [])
            if len(results) > MAX_SERIES:
                results = results[:MAX_SERIES]
            return results
        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError) as e:
            logger.warning("prometheus_query_exception query=%s error=%s", query[:100], type(e).__name__)
            return None

    def range_query(self, query: str, duration_seconds: int = 300, step: int = 60) -> Optional[List[Dict]]:
        """
        Execute a range PromQL query.

        Returns a list of result series with 'metric' (labels) and 'values'
        (list of [timestamp, value] pairs). Returns None on failure.
        """
        try:
            end_time = time.time()
            start_time = end_time - duration_seconds
            resp = self._session.get(
                f"{self._base_url}/api/v1/query_range",
                params={
                    "query": query,
                    "start": start_time,
                    "end": end_time,
                    "step": step,
                },
                timeout=QUERY_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") != "success":
                return None
            results = data.get("data", {}).get("result", [])
            if len(results) > MAX_SERIES:
                results = results[:MAX_SERIES]
            return results
        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError):
            return None

    def get_targets(self) -> Optional[List[Dict]]:
        """Get Prometheus scrape target health information."""
        try:
            resp = self._session.get(
                f"{self._base_url}/api/v1/targets",
                timeout=QUERY_TIMEOUT,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") != "success":
                return None
            targets = data.get("data", {}).get("activeTargets", [])
            return [
                {
                    "job": t.get("labels", {}).get("job", ""),
                    "instance": t.get("labels", {}).get("instance", ""),
                    "health": t.get("health", "unknown"),
                    "last_error": t.get("lastError", ""),
                }
                for t in targets[:MAX_SERIES]
            ]
        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError):
            return None

    # ── Convenience helpers ──

    def _scalar_from_results(self, results: Optional[List[Dict]], default: float = 0.0) -> float:
        """Extract a single scalar value from the first series of a query result."""
        if not results:
            return default
        first = results[0]
        value = first.get("value")
        if value and isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return float(value[1])
            except (ValueError, TypeError):
                return default
        return default

    def _all_values(self, results: Optional[List[Dict]]) -> List[float]:
        """Extract all scalar values from query results."""
        if not results:
            return []
        values = []
        for series in results:
            value = series.get("value")
            if value and isinstance(value, (list, tuple)) and len(value) >= 2:
                try:
                    values.append(float(value[1]))
                except (ValueError, TypeError):
                    pass
        return values

    def _get_host_label(self, series: Dict, default: str = "vps") -> str:
        """Extract host label from a Prometheus result series."""
        if self._host_label:
            return self._host_label
        labels = series.get("metric", {})
        return labels.get("instance", labels.get("nodename", default))

    def _results_to_snapshots(
        self,
        results: Optional[List[Dict]],
        metric_name: str,
        component: str,
        value_extractor=None,
    ) -> List[MetricSnapshot]:
        """
        Convert Prometheus query results to MetricSnapshot objects.

        Args:
            results: Raw Prometheus query results
            metric_name: Name for the MetricSnapshot
            component: Component label
            value_extractor: Optional callable(series_dict) -> float. If None,
                uses the default scalar extraction.
        """
        if not results:
            return []

        snapshots = []
        ts = time.time()
        for series in results:
            host = self._get_host_label(series)
            if value_extractor:
                value = value_extractor(series)
            else:
                value = self._scalar_from_results([series])
            labels = {k: v for k, v in series.get("metric", {}).items()
                      if k not in ("__name__", "instance", "job", "nodename")}
            snapshots.append(MetricSnapshot(
                metric_name=metric_name,
                value=value,
                timestamp=ts,
                host=host,
                component=component,
                labels=labels,
            ))
        return snapshots

    # ── MonitoringCollector interface implementation ──

    def get_system_metrics(self, host: str = "") -> List[MetricSnapshot]:
        """Collect system-level metrics from Prometheus."""
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        # CPU usage percentage (from node-exporter or cAdvisor)
        # 100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)
        cpu_results = self.instant_query(
            '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        )
        if cpu_results:
            snapshots.extend(self._results_to_snapshots(
                cpu_results, "cpu_percent", "system",
            ))

        # Memory usage percentage
        mem_results = self.instant_query(
            '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
        )
        if mem_results:
            snapshots.extend(self._results_to_snapshots(
                mem_results, "memory_percent", "system",
            ))

        # Disk usage percentage (root filesystem)
        disk_results = self.instant_query(
            '(1 - (node_filesystem_avail_bytes{mountpoint="/"} / '
            'node_filesystem_size_bytes{mountpoint="/"})) * 100'
        )
        if disk_results:
            snapshots.extend(self._results_to_snapshots(
                disk_results, "disk_percent", "system",
            ))

        # Load averages
        for load_metric, snap_name in [
            ("node_load1", "load_1m"),
            ("node_load5", "load_5m"),
            ("node_load15", "load_15m"),
        ]:
            load_results = self.instant_query(load_metric)
            if load_results:
                snapshots.extend(self._results_to_snapshots(
                    load_results, snap_name, "system",
                ))

        # CPU core count
        core_results = self.instant_query('count(cpu_count) or count(count by (instance) (node_cpu_seconds_total{mode="idle"}))')
        if core_results:
            snapshots.extend(self._results_to_snapshots(
                core_results, "cpu_cores", "system",
            ))

        return snapshots

    def get_container_metrics(self, host: str = "") -> List[MetricSnapshot]:
        """Collect Docker container metrics from cAdvisor/Prometheus."""
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        # Container CPU usage (from cAdvisor)
        cpu_results = self.instant_query(
            'rate(container_cpu_usage_seconds_total{container!="",container!="POD"}[5m]) * 100'
        )
        if cpu_results:
            for series in cpu_results:
                labels = series.get("metric", {})
                name = labels.get("name", labels.get("container", "unknown"))
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                snapshots.append(MetricSnapshot(
                    metric_name="container_cpu_percent",
                    value=value, timestamp=ts, host=host_label,
                    component=name,
                    labels={"container": name},
                ))

        # Container memory usage (from cAdvisor)
        mem_results = self.instant_query(
            'container_memory_usage_bytes{container!="",container!="POD"} / '
            'container_spec_memory_limit_bytes{container!="",container!="POD"} * 100'
        )
        # Fall back to unnormalized memory if limits aren't set
        if not mem_results:
            mem_results = self.instant_query(
                'container_memory_usage_bytes{container!="",container!="POD"}'
            )
        if mem_results:
            for series in mem_results:
                labels = series.get("metric", {})
                name = labels.get("name", labels.get("container", "unknown"))
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                metric_name = "container_memory_percent" if "limit" in str(mem_results) else "container_memory_bytes"
                snapshots.append(MetricSnapshot(
                    metric_name=metric_name,
                    value=value, timestamp=ts, host=host_label,
                    component=name,
                    labels={"container": name},
                ))

        # Container restart count (from cAdvisor)
        restart_results = self.instant_query(
            'increase(container_start_time_seconds{container!="",container!="POD"}[1h])'
        )
        if restart_results:
            for series in restart_results:
                labels = series.get("metric", {})
                name = labels.get("name", labels.get("container", "unknown"))
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                snapshots.append(MetricSnapshot(
                    metric_name="container_restart_count",
                    value=value, timestamp=ts, host=host_label,
                    component=name,
                    labels={"container": name},
                ))

        return snapshots

    def get_container_states(self, host: str = "") -> List[MetricSnapshot]:
        """Collect container running states from cAdvisor/Prometheus."""
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        # Container running state
        running_results = self.instant_query(
            'container_last_seen{container!="",container!="POD"}'
        )
        if running_results:
            for series in running_results:
                labels = series.get("metric", {})
                name = labels.get("name", labels.get("container", "unknown"))
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                # If container_last_seen is recent (< 60s ago), it's running
                is_running = 1.0 if (ts - value) < 60 else 0.0
                snapshots.append(MetricSnapshot(
                    metric_name="container_state",
                    value=is_running, timestamp=ts, host=host_label,
                    component=name,
                    labels={"container": name, "state": "running" if is_running else "stopped"},
                ))

        return snapshots

    def get_container_health(self, host: str = "") -> List[MetricSnapshot]:
        """Collect container health status from cAdvisor/Prometheus."""
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        # Container health state (if healthchecks are configured in Docker)
        health_results = self.instant_query(
            'container_health_status{container!="",container!="POD"}'
        )
        if health_results:
            for series in health_results:
                labels = series.get("metric", {})
                name = labels.get("name", labels.get("container", "unknown"))
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                health_val = 1.0 if value == 1.0 else (0.0 if value == 0.0 else 0.5)
                health_label = "healthy" if value == 1.0 else ("unhealthy" if value == 0.0 else "no_healthcheck")
                snapshots.append(MetricSnapshot(
                    metric_name="container_health",
                    value=health_val, timestamp=ts, host=host_label,
                    component=name,
                    labels={"container": name, "health": health_label},
                ))

        return snapshots

    def get_service_states(self, host: str = "") -> List[MetricSnapshot]:
        """Collect systemd service states from node-exporter/textfile collector."""
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        # Systemd service state (from node-exporter textfile collector if available)
        service_results = self.instant_query(
            'node_systemd_unit_state{state="active"}'
        )
        if service_results:
            for series in service_results:
                labels = series.get("metric", {})
                name = labels.get("name", labels.get("unit", "unknown"))
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                snapshots.append(MetricSnapshot(
                    metric_name="service_active",
                    value=value, timestamp=ts, host=host_label,
                    component=name,
                    labels={"unit": name, "state": "active"},
                ))

        return snapshots

    def get_log_events(self, host: str = "", since_seconds: int = 300) -> List[MetricSnapshot]:
        """
        Collect log error/warning events from Prometheus (if logging pipeline
        feeds into Prometheus via Loki or similar).

        Returns empty list if no log metrics are available in Prometheus.
        """
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        # Try to get error counts from Prometheus (if log aggregation is configured)
        # This depends on whether logs are exported as metrics (e.g. via Promtail→Loki→Prometheus)
        error_results = self.instant_query(
            f'sum(rate({{__name__=~".*error.*|.*err.*"}}[{since_seconds}s])) by (job, instance)'
        )
        if error_results:
            for series in error_results:
                labels = series.get("metric", {})
                host_label = self._get_host_label(series)
                value = self._scalar_from_results([series])
                job_name = labels.get("job", "unknown")
                if value > 0:
                    snapshots.append(MetricSnapshot(
                        metric_name="log_error_count",
                        value=value, timestamp=ts, host=host_label,
                        component=job_name,
                        labels={"job": job_name},
                    ))

        return snapshots

    def get_target_health(self) -> List[Dict]:
        """Get health of Prometheus scrape targets."""
        return self.get_targets() or []

    def get_network_metrics(self, host: str = "") -> List[MetricSnapshot]:
        """Collect network I/O metrics from node-exporter."""
        if not self.is_available():
            return []

        snapshots: List[MetricSnapshot] = []
        ts = time.time()

        for metric, name in [
            ('rate(node_network_receive_bytes_total{device!="lo"}[5m])', "network_receive_bytes_per_sec"),
            ('rate(node_network_transmit_bytes_total{device!="lo"}[5m])', "network_transmit_bytes_per_sec"),
        ]:
            results = self.instant_query(metric)
            if results:
                snapshots.extend(self._results_to_snapshots(
                    results, name, "network",
                ))

        return snapshots
