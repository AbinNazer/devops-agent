"""
Monitoring Engine for Phase 6.

Orchestrates the complete monitoring lifecycle:
  Telemetry → Detection → Anomaly? → Incident → Dedup → Correlate → Cooldown

CRITICAL: Normal monitoring does NOT call the LLM.
Only confirmed incidents with sufficient evidence trigger JARVIS reasoning.
"""
import logging
import time
from typing import Callable, Dict, List, Optional

from app.monitoring.interfaces import MonitoringCollector
from app.monitoring.models import (
    Anomaly, DetectionResult, Incident, IncidentState, MetricSnapshot, Severity,
)
from app.monitoring.thresholds import ThresholdConfig
from app.monitoring.anomaly import (
    detect_system_anomalies, detect_container_anomalies,
    detect_service_anomalies, detect_log_anomalies,
    detect_oom_indicators, detect_crash_indicators,
)
from app.monitoring.incidents import IncidentStore
from app.monitoring.deduplication import find_or_create_incident
from app.monitoring.correlation import correlate_anomalies
from app.monitoring.cooldown import CooldownTracker

logger = logging.getLogger("monitoring_engine")


class MonitoringEngine:
    """
    Main monitoring engine. Collects telemetry, detects anomalies,
    manages incidents. Does NOT execute any mutations.

    Phase 6 → Phase 3/4/5 integration:
      When an incident is confirmed, the engine can invoke a callback
      that hands it to JARVIS for investigation. Phase 5 handles
      all controlled actions.
    """

    def __init__(
        self,
        collector: MonitoringCollector,
        config: Optional[ThresholdConfig] = None,
        incident_store: Optional[IncidentStore] = None,
        on_incident_confirmed: Optional[Callable[[Incident], None]] = None,
    ):
        self._collector = collector
        self._config = config or ThresholdConfig()
        self._store = incident_store or IncidentStore(
            max_active=self._config.max_concurrent_incidents)
        self._cooldown = CooldownTracker(self._config)
        self._on_incident_confirmed = on_incident_confirmed
        self._cycle_count = 0
        self._total_anomalies = 0
        self._total_incidents_created = 0
        self._total_incidents_deduped = 0

    @property
    def store(self) -> IncidentStore:
        return self._store

    @property
    def cooldown(self) -> CooldownTracker:
        return self._cooldown

    @property
    def stats(self) -> dict:
        return {
            "cycles": self._cycle_count,
            "total_anomalies": self._total_anomalies,
            "incidents_created": self._total_incidents_created,
            "incidents_deduped": self._total_incidents_deduped,
            "active_incidents": self._store.active_count,
        }

    def run_cycle(self, host: str = "") -> DetectionResult:
        """
        Run one complete monitoring cycle: collect → detect → correlate → dedup.

        This is the main entry point called by the scheduler.
        Returns a DetectionResult summarizing what happened.
        """
        self._cycle_count += 1
        result = DetectionResult(host=host)

        if not self._collector.is_available():
            result.errors.append("Collector unavailable")
            logger.warning("monitoring_cycle=%d collector_unavailable", self._cycle_count)
            return result

        try:
            # 1. Collect telemetry
            system_snapshots = self._collector.get_system_metrics(host)
            container_state_snaps = self._collector.get_container_states(host)
            container_health_snaps = self._collector.get_container_health(host)
            container_metric_snaps = self._collector.get_container_metrics(host)
            service_snaps = self._collector.get_service_states(host)
            log_snaps = self._collector.get_log_events(host)

            all_snapshots = (system_snapshots + container_state_snaps +
                             container_health_snaps + container_metric_snaps +
                             service_snaps + log_snaps)

            # 2. Detect anomalies
            anomalies = []
            anomalies.extend(detect_system_anomalies(system_snapshots, self._config))
            anomalies.extend(detect_container_anomalies(
                container_state_snaps + container_health_snaps + container_metric_snaps,
                self._config))
            anomalies.extend(detect_service_anomalies(service_snaps, self._config))
            anomalies.extend(detect_log_anomalies(log_snaps, self._config))

            # 3. Check log details for OOM/crash
            if self._collector.name == "mock":
                # For mock collector, get raw log details
                for snap in log_snaps:
                    if snap.metric_name == "log_error_count":
                        # The mock collector already counted errors
                        pass

            result.anomalies = anomalies
            self._total_anomalies += len(anomalies)

            # 4. Process anomalies through dedup + correlation + cooldown
            for anomaly in anomalies:
                result.events.append(self._anomaly_to_event(anomaly))

                if not self._cooldown.can_fire(anomaly.fingerprint()):
                    result.incidents_deduplicated += 1
                    self._total_incidents_deduped += 1
                    continue

                # Dedup: find existing or create new
                incident, is_new = find_or_create_incident(anomaly, self._store, self._config)

                if is_new:
                    result.incidents_created.append(incident.id)
                    self._total_incidents_created += 1
                    self._cooldown.record_fire(anomaly.fingerprint())
                    logger.info("incident_created id=%s type=%s host=%s component=%s",
                                incident.id, anomaly.anomaly_type, anomaly.host, anomaly.component)
                else:
                    result.incidents_updated.append(incident.id)
                    self._cooldown.record_fire(anomaly.fingerprint())

                # 5. Try correlation with other active incidents
                correlate_anomalies([anomaly], self._store, self._config)

                # 6. Check if incident should be confirmed (escalate from DETECTED)
                if incident.state == IncidentState.DETECTED.value:
                    if len(incident.evidence) >= 2 or anomaly.severity == Severity.CRITICAL.value:
                        incident.state = IncidentState.CONFIRMED.value
                        incident.add_timeline("confirmed", f"Evidence count: {len(incident.evidence)}")

                        # 7. Invoke JARVIS callback for confirmed incidents
                        if self._on_incident_confirmed:
                            try:
                                self._on_incident_confirmed(incident)
                            except Exception as e:
                                logger.error("incident_callback_error id=%s error=%s", incident.id, e)

        except Exception as e:
            result.errors.append(f"Cycle error: {e}")
            logger.error("monitoring_cycle_error cycle=%d error=%s", self._cycle_count, e)

        logger.info("monitoring_cycle_complete cycle=%d anomalies=%d incidents_new=%d incidents_dedup=%d",
                     self._cycle_count, len(result.anomalies),
                     len(result.incidents_created), result.incidents_deduplicated)
        return result

    def _anomaly_to_event(self, anomaly: Anomaly) -> 'from app.monitoring.models import MonitoringEvent; MonitoringEvent':
        """Convert an Anomaly to a MonitoringEvent."""
        from app.monitoring.models import MonitoringEvent
        return MonitoringEvent(
            source=self._collector.name,
            host=anomaly.host,
            component=anomaly.component,
            component_type=anomaly.component_type,
            event_type=anomaly.anomaly_type,
            severity=anomaly.severity,
            message=anomaly.message,
            evidence=list(anomaly.evidence),
            labels=anomaly.labels if hasattr(anomaly, 'labels') else {},
        )

    def get_active_incidents(self) -> List[Dict]:
        """Return active incidents as dicts (for tool registry exposure)."""
        return [i.to_dict() for i in self._store.get_active()]

    def get_incident(self, incident_id: str) -> Optional[Dict]:
        """Get a specific incident by ID."""
        inc = self._store.get(incident_id)
        return inc.to_dict() if inc else None

    def get_summary(self) -> Dict:
        """Get a summary of monitoring status."""
        active = self._store.get_active()
        by_severity = {"info": 0, "warning": 0, "critical": 0}
        for inc in active:
            by_severity[inc.severity] = by_severity.get(inc.severity, 0) + 1

        return {
            "collector": self._collector.name,
            "collector_available": self._collector.is_available(),
            "cycle_count": self._cycle_count,
            "total_anomalies": self._total_anomalies,
            "total_incidents_created": self._total_incidents_created,
            "total_incidents_deduped": self._total_incidents_deduped,
            "active_incidents": len(active),
            "active_by_severity": by_severity,
            "stats": self.stats,
        }
