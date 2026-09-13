"""
Collector interface abstraction for Phase 6.

The monitoring engine MUST NOT directly depend on Prometheus, cAdvisor,
or any specific telemetry backend. All data retrieval goes through
this interface. A MockCollector is provided for deterministic testing.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from app.monitoring.models import MetricSnapshot, ComponentType


class MonitoringCollector(ABC):
    """Abstract interface for collecting monitoring data."""

    @abstractmethod
    def get_system_metrics(self, host: str = "") -> List[MetricSnapshot]:
        """Collect CPU, memory, disk, load metrics for a host."""

    @abstractmethod
    def get_container_metrics(self, host: str = "") -> List[MetricSnapshot]:
        """Collect Docker container metrics (state, health, resources)."""

    @abstractmethod
    def get_service_states(self, host: str = "") -> List[MetricSnapshot]:
        """Collect systemd service states."""

    @abstractmethod
    def get_container_states(self, host: str = "") -> List[MetricSnapshot]:
        """Collect container running/stopped/restarting states."""

    @abstractmethod
    def get_container_health(self, host: str = "") -> List[MetricSnapshot]:
        """Collect container healthcheck status."""

    @abstractmethod
    def get_log_events(self, host: str = "", since_seconds: int = 300) -> List[MetricSnapshot]:
        """Collect recent log error/warning events."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the collector backend is reachable."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this collector."""
