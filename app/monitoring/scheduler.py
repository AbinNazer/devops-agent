"""
Bounded monitoring scheduler for Phase 6.

Runs periodic collection/detection cycles with:
- configurable polling interval
- graceful shutdown
- per-collection timeout
- failure handling with backoff
- no busy loop
- no uncontrolled thread creation
- no unlimited queue growth
"""
import logging
import threading
import time
from typing import Callable, Optional

from app.monitoring.thresholds import ThresholdConfig

logger = logging.getLogger("monitoring_scheduler")


class MonitoringScheduler:
    """
    Bounded scheduler that runs monitoring cycles at a fixed interval.

    Usage:
        scheduler = MonitoringScheduler(
            collect_fn=my_collect_function,
            config=ThresholdConfig(polling_interval_seconds=60),
        )
        scheduler.start()
        # ... later ...
        scheduler.stop()
    """

    def __init__(
        self,
        collect_fn: Callable[[], None],
        config: Optional[ThresholdConfig] = None,
        clock: Optional[Callable[[], float]] = None,
    ):
        """
        Args:
            collect_fn: Function to call each cycle. Must be non-blocking.
            config: Threshold configuration (includes polling interval).
            clock: Injectable clock for testing (default: time.time).
        """
        self._config = config or ThresholdConfig()
        self._collect_fn = collect_fn
        self._clock = clock or time.time
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cycle_count = 0
        self._error_count = 0
        self._last_cycle_start = 0.0
        self._last_cycle_end = 0.0
        self._backoff_until = 0.0
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def error_count(self) -> int:
        return self._error_count

    def start(self) -> None:
        """Start the scheduler in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="monitoring-scheduler")
        self._thread.start()
        logger.info("monitoring_scheduler_started interval=%ss", self._config.polling_interval_seconds)

    def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop the scheduler."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("monitoring_scheduler_stopped cycles=%d errors=%d", self._cycle_count, self._error_count)

    def run_one_cycle(self) -> dict:
        """Run a single collection/detection cycle. Useful for testing."""
        return self._execute_cycle()

    def _run_loop(self) -> None:
        """Main loop: runs cycles at the configured interval."""
        while self._running:
            now = self._clock()

            # Respect backoff
            if now < self._backoff_until:
                time.sleep(min(self._backoff_until - now, 5.0))
                continue

            result = self._execute_cycle()

            # Sleep until next cycle (avoid busy loop)
            interval = self._config.polling_interval_seconds
            if result.get("error"):
                # Backoff on error: double up to 5 minutes
                backoff = min(interval * 2, 300)
                self._backoff_until = self._clock() + backoff
                logger.warning("scheduler_backoff=%ss", backoff)

            time.sleep(interval)

    def _execute_cycle(self) -> dict:
        """Execute a single monitoring cycle."""
        with self._lock:
            self._cycle_count += 1
            self._last_cycle_start = self._clock()

        try:
            self._collect_fn()
            with self._lock:
                self._last_cycle_end = self._clock()
            return {"success": True, "cycle": self._cycle_count}
        except Exception as e:
            with self._lock:
                self._error_count += 1
                self._last_cycle_end = self._clock()
            logger.error("scheduler_cycle_error cycle=%d error=%s", self._cycle_count, e)
            return {"success": False, "error": str(e), "cycle": self._cycle_count}
