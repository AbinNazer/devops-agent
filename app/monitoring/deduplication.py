"""
Deterministic incident deduplication for Phase 6.

RAM: 91% → 92% → 93% → 94% should NOT create four incidents.
It should update one incident with the same fingerprint.

Fingerprints use stable fields (host, component, anomaly_type, metric),
NOT timestamps.
"""
import time
from typing import Optional

from app.monitoring.models import Anomaly, Incident, IncidentState
from app.monitoring.incidents import IncidentStore, update_incident_with_anomaly
from app.monitoring.thresholds import ThresholdConfig


def find_or_create_incident(anomaly: Anomaly, store: IncidentStore,
                            config: Optional[ThresholdConfig] = None) -> tuple:
    """
    Find an existing incident for this anomaly's fingerprint, or create a new one.

    Returns (incident, is_new).
    """
    config = config or ThresholdConfig()
    fingerprint = anomaly.fingerprint()

    existing = store.get_by_fingerprint(fingerprint)
    if existing:
        # Check cooldown — don't update too frequently
        time_since_update = time.time() - existing.updated_at
        if time_since_update < config.cooldown_seconds:
            return existing, False  # Within cooldown, skip update

        update_incident_with_anomaly(existing, anomaly)
        return existing, False

    # Check active incident limit — evict oldest if at capacity
    if store.active_count >= config.max_concurrent_incidents:
        active = store.get_active()
        if active:
            # Prefer evicting info-severity, else evict oldest overall
            info_severity = [i for i in active if i.severity == "info"]
            oldest = min(info_severity or active, key=lambda i: i.created_at)
            store.close(oldest.id, "Evicted due to incident limit")

    from app.monitoring.incidents import create_incident_from_anomaly
    incident = create_incident_from_anomaly(anomaly)
    store.add(incident)
    return incident, True
