"""Tests for app/monitoring/cooldown.py — cooldown and alert suppression."""
import time
import pytest
from app.monitoring.cooldown import CooldownTracker
from app.monitoring.thresholds import ThresholdConfig


class TestCooldownTracker:
    def setup_method(self):
        self.config = ThresholdConfig(cooldown_seconds=10, escalation_cooldown_seconds=30)
        self.tracker = CooldownTracker(self.config)

    def test_can_fire_first_time(self):
        assert self.tracker.can_fire("test|fingerprint") is True

    def test_cooldown_prevents_fire(self):
        fp = "test|fingerprint"
        self.tracker.record_fire(fp)
        assert self.tracker.can_fire(fp) is False

    def test_cooldown_expires(self):
        fp = "test|fingerprint"
        self.tracker.record_fire(fp)
        # Manually set last_fired to past
        self.tracker._last_fired[fp] = time.time() - 20
        assert self.tracker.can_fire(fp) is True

    def test_escalation_cooldown(self):
        """After 3 fires, escalation cooldown kicks in."""
        fp = "test|fingerprint"
        for _ in range(3):
            self.tracker.record_fire(fp)
        # Reset last_fired to just past basic cooldown but within escalation
        self.tracker._last_fired[fp] = time.time() - 15
        assert self.tracker.can_fire(fp) is False  # escalation cooldown
        # Past escalation cooldown
        self.tracker._last_fired[fp] = time.time() - 35
        assert self.tracker.can_fire(fp) is True

    def test_fire_count(self):
        fp = "test|fingerprint"
        assert self.tracker.get_fire_count(fp) == 0
        self.tracker.record_fire(fp)
        assert self.tracker.get_fire_count(fp) == 1
        self.tracker.record_fire(fp)
        assert self.tracker.get_fire_count(fp) == 2

    def test_recovery_resets_fire_count(self):
        fp = "test|fingerprint"
        self.tracker.record_fire(fp)
        self.tracker.record_fire(fp)
        assert self.tracker.get_fire_count(fp) == 2
        self.tracker.record_recovery(fp)
        assert self.tracker.get_fire_count(fp) == 0

    def test_is_recovering(self):
        fp = "test|fingerprint"
        assert self.tracker.is_recovering(fp) is False
        self.tracker.record_recovery(fp)
        assert self.tracker.is_recovering(fp) is True

    def test_reset(self):
        fp = "test|fingerprint"
        self.tracker.record_fire(fp)
        self.tracker.record_fire(fp)
        self.tracker.reset(fp)
        assert self.tracker.get_fire_count(fp) == 0
        assert self.tracker.can_fire(fp) is True

    def test_different_fingerprints_independent(self):
        fp1 = "a|b|c|d"
        fp2 = "a|b|c|e"
        self.tracker.record_fire(fp1)
        assert self.tracker.can_fire(fp1) is False
        assert self.tracker.can_fire(fp2) is True
