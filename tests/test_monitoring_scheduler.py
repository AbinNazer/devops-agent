"""Tests for app/monitoring/scheduler.py — bounded monitoring scheduler."""
import threading
import time
import pytest
from app.monitoring.scheduler import MonitoringScheduler
from app.monitoring.thresholds import ThresholdConfig


class TestMonitoringScheduler:
    def test_run_one_cycle(self):
        call_count = [0]
        def collect():
            call_count[0] += 1
        scheduler = MonitoringScheduler(collect_fn=collect)
        result = scheduler.run_one_cycle()
        assert result["success"] is True
        assert call_count[0] == 1
        assert scheduler.cycle_count == 1

    def test_run_multiple_cycles(self):
        call_count = [0]
        def collect():
            call_count[0] += 1
        scheduler = MonitoringScheduler(collect_fn=collect)
        for _ in range(5):
            scheduler.run_one_cycle()
        assert call_count[0] == 5
        assert scheduler.cycle_count == 5

    def test_error_handling(self):
        def failing_collect():
            raise RuntimeError("Collector crashed")
        scheduler = MonitoringScheduler(collect_fn=failing_collect)
        result = scheduler.run_one_cycle()
        assert result["success"] is False
        assert "Collector crashed" in result["error"]
        assert scheduler.error_count == 1

    def test_start_stop(self):
        config = ThresholdConfig(polling_interval_seconds=0.1)
        call_count = [0]
        def collect():
            call_count[0] += 1
        scheduler = MonitoringScheduler(collect_fn=collect, config=config)
        scheduler.start()
        assert scheduler.is_running is True
        time.sleep(0.3)
        scheduler.stop(timeout=2.0)
        assert scheduler.is_running is False
        assert call_count[0] >= 1

    def test_stop_when_not_running(self):
        scheduler = MonitoringScheduler(collect_fn=lambda: None)
        scheduler.stop()  # Should not raise

    def test_double_start(self):
        config = ThresholdConfig(polling_interval_seconds=0.1)
        scheduler = MonitoringScheduler(collect_fn=lambda: None, config=config)
        scheduler.start()
        scheduler.start()  # Should be idempotent
        assert scheduler.is_running is True
        scheduler.stop(timeout=2.0)

    def test_clock_injection(self):
        """Test that injected clock works for deterministic testing."""
        time_val = [1000.0]
        def mock_clock():
            return time_val[0]
        scheduler = MonitoringScheduler(collect_fn=lambda: None, clock=mock_clock)
        scheduler.run_one_cycle()
        assert scheduler.cycle_count == 1

    def test_custom_config(self):
        config = ThresholdConfig(polling_interval_seconds=30)
        scheduler = MonitoringScheduler(collect_fn=lambda: None, config=config)
        result = scheduler.run_one_cycle()
        assert result["success"] is True
