"""
Configurable threshold engine for Phase 6 anomaly detection.

All thresholds are centralized here, not scattered through detection code.
Safe defaults are provided; tests and production can override.
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class ThresholdConfig:
    """All configurable monitoring thresholds in one place."""

    # System thresholds (percent)
    cpu_warning: float = 80.0
    cpu_critical: float = 95.0
    memory_warning: float = 80.0
    memory_critical: float = 95.0
    disk_warning: float = 80.0
    disk_critical: float = 95.0

    # Load average: warning when load_per_core > this
    load_warning_per_core: float = 2.0
    load_critical_per_core: float = 4.0

    # Container thresholds
    container_restart_warning: int = 3
    container_restart_critical: int = 10
    container_cpu_warning: float = 80.0
    container_memory_warning: float = 85.0

    # Log thresholds (count within collection window)
    log_error_warning: int = 5
    log_error_critical: int = 20
    log_warning_spike: int = 15

    # OOM / crash indicators
    oom_per_window: int = 1  # 1 OOM in a window is critical
    crash_per_window: int = 2  # 2+ crashes = critical

    # Timing
    cooldown_seconds: int = 300  # 5 minutes between repeated incidents
    escalation_cooldown_seconds: int = 600  # 10 min before re-escalating
    polling_interval_seconds: int = 60
    collection_timeout_seconds: int = 30

    # Limits
    max_concurrent_incidents: int = 10
    max_events_per_cycle: int = 100
    max_evidence_per_incident: int = 50
    max_evidence_chars: int = 2000
    max_correlated_window_seconds: int = 600  # 10 min correlation window

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


DEFAULT_CONFIG = ThresholdConfig()


@dataclass
class ThresholdResult:
    """Result of checking a value against thresholds."""
    metric_name: str
    value: float
    warning_threshold: float
    critical_threshold: float
    breached: bool = False
    level: str = "normal"  # normal, warning, critical

    def to_dict(self) -> dict:
        return {"metric_name": self.metric_name, "value": self.value,
                "warning": self.warning_threshold, "critical": self.critical_threshold,
                "breached": self.breached, "level": self.level}


def check_threshold(value: float, warning: float, critical: float,
                    metric_name: str = "") -> ThresholdResult:
    """Check a single value against warning/critical thresholds."""
    level = "normal"
    breached = False
    if value >= critical:
        level = "critical"
        breached = True
    elif value >= warning:
        level = "warning"
        breached = True
    return ThresholdResult(
        metric_name=metric_name, value=value,
        warning_threshold=warning, critical_threshold=critical,
        breached=breached, level=level,
    )
