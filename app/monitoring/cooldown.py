"""
Cooldown and alert suppression for Phase 6.

Prevents alert storms:
  🚨 incident
  🚨 incident
  🚨 incident

Every monitoring cycle.

Supports:
- Incident cooldown
- Recovery detection
- Suppression
- Escalation after repeated failure
"""
import time
from typing import Dict, Optional

from app.monitoring.thresholds import ThresholdConfig


class CooldownTracker:
    """Tracks cooldowns for components to prevent alert storms."""

    def __init__(self, config: Optional[ThresholdConfig] = None):
        self._config = config or ThresholdConfig()
        self._last_fired: Dict[str, float] = {}  # fingerprint → timestamp
        self._fire_count: Dict[str, int] = {}  # fingerprint → consecutive fires
        self._last_recovery: Dict[str, float] = {}  # fingerprint → recovery timestamp

    def can_fire(self, fingerprint: str) -> bool:
        """Check if an alert can fire for this fingerprint."""
        last = self._last_fired.get(fingerprint, 0)
        elapsed = time.time() - last

        # Basic cooldown
        if elapsed < self._config.cooldown_seconds:
            return False

        # Escalation cooldown: if fired many times, require longer wait
        count = self._fire_count.get(fingerprint, 0)
        if count >= 3 and elapsed < self._config.escalation_cooldown_seconds:
            return False

        return True

    def record_fire(self, fingerprint: str) -> None:
        """Record that an alert fired for this fingerprint."""
        self._last_fired[fingerprint] = time.time()
        self._fire_count[fingerprint] = self._fire_count.get(fingerprint, 0) + 1

    def record_recovery(self, fingerprint: str) -> None:
        """Record a recovery for this fingerprint."""
        self._last_recovery[fingerprint] = time.time()
        # Reset fire count on recovery
        self._fire_count.pop(fingerprint, None)

    def is_recovering(self, fingerprint: str, within_seconds: float = 300) -> bool:
        """Check if this component recently recovered."""
        last = self._last_recovery.get(fingerprint, 0)
        return (time.time() - last) < within_seconds

    def get_fire_count(self, fingerprint: str) -> int:
        """Get the number of consecutive fires for a fingerprint."""
        return self._fire_count.get(fingerprint, 0)

    def reset(self, fingerprint: str) -> None:
        """Reset tracking for a fingerprint."""
        self._last_fired.pop(fingerprint, None)
        self._fire_count.pop(fingerprint, None)
        self._last_recovery.pop(fingerprint, None)
