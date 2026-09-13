"""Tests for app/monitoring/thresholds.py — threshold engine."""
import pytest
from app.monitoring.thresholds import ThresholdConfig, check_threshold, DEFAULT_CONFIG


class TestThresholdConfig:
    def test_defaults_exist(self):
        config = ThresholdConfig()
        assert config.cpu_warning == 80.0
        assert config.cpu_critical == 95.0
        assert config.memory_warning == 80.0
        assert config.memory_critical == 95.0
        assert config.disk_warning == 80.0
        assert config.disk_critical == 95.0
        assert config.cooldown_seconds == 300
        assert config.polling_interval_seconds == 60
        assert config.max_concurrent_incidents == 10

    def test_to_dict(self):
        config = ThresholdConfig()
        d = config.to_dict()
        assert "cpu_warning" in d
        assert d["cpu_warning"] == 80.0

    def test_custom_config(self):
        config = ThresholdConfig(cpu_warning=70.0, cpu_critical=90.0)
        assert config.cpu_warning == 70.0
        assert config.cpu_critical == 90.0

    def test_default_config_singleton(self):
        assert DEFAULT_CONFIG.cpu_warning == 80.0


class TestCheckThreshold:
    def test_normal(self):
        r = check_threshold(50.0, 80.0, 95.0, "cpu")
        assert r.breached is False
        assert r.level == "normal"

    def test_warning(self):
        r = check_threshold(85.0, 80.0, 95.0, "cpu")
        assert r.breached is True
        assert r.level == "warning"

    def test_critical(self):
        r = check_threshold(96.0, 80.0, 95.0, "cpu")
        assert r.breached is True
        assert r.level == "critical"

    def test_exact_warning_boundary(self):
        r = check_threshold(80.0, 80.0, 95.0, "cpu")
        assert r.breached is True
        assert r.level == "warning"

    def test_exact_critical_boundary(self):
        r = check_threshold(95.0, 80.0, 95.0, "cpu")
        assert r.breached is True
        assert r.level == "critical"

    def test_zero(self):
        r = check_threshold(0.0, 80.0, 95.0, "cpu")
        assert r.breached is False
        assert r.level == "normal"

    def test_threshold_result_to_dict(self):
        r = check_threshold(90.0, 80.0, 95.0, "cpu")
        d = r.to_dict()
        assert d["metric_name"] == "cpu"
        assert d["value"] == 90.0
        assert d["breached"] is True
