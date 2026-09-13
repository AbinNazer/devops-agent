"""
Task manager for long-running DevOps operations.

Tracks running tasks per conversation, supports cancellation,
and provides progress updates.
"""
import threading
import time
from datetime import datetime
from typing import Optional, List, Dict
import uuid

from app.api.models import TaskInfo, TaskStatus


class TaskManager:
    """Manages long-running tasks with cancellation support."""

    def __init__(self):
        self._tasks: Dict[str, TaskInfo] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def create_task(
        self,
        conversation_id: str,
        title: str,
        tool_name: str = "",
        cancellable: bool = True,
    ) -> TaskInfo:
        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task = TaskInfo(
            id=task_id,
            conversation_id=conversation_id,
            title=title,
            status=TaskStatus.QUEUED,
            started_at=datetime.utcnow(),
            tool_name=tool_name,
            cancellable=cancellable,
        )
        with self._lock:
            self._tasks[task_id] = task
            self._cancel_events[task_id] = threading.Event()
        return task

    def start_task(self, task_id: str, step: str = "") -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.status = TaskStatus.RUNNING
            task.current_step = step
            return True

    def update_progress(self, task_id: str, progress: float, step: str = "") -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.progress = min(1.0, max(0.0, progress))
            if step:
                task.current_step = step
            return True

    def complete_task(self, task_id: str, result: Optional[dict] = None) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.status = TaskStatus.COMPLETED
            task.progress = 1.0
            task.completed_at = datetime.utcnow()
            task.result = result
            return True

    def fail_task(self, task_id: str, error: str = "") -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.utcnow()
            task.result = {"error": error}
            return True

    def request_cancel(self, task_id: str) -> bool:
        """Request cancellation of a task. The task checks is_cancelled()."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task or not task.cancellable:
                return False
            if task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
                return False
            task.status = TaskStatus.CANCEL_REQUESTED
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            return True

    def confirm_cancel(self, task_id: str) -> bool:
        """Mark a task as fully cancelled after safe shutdown."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow()
            return True

    def is_cancelled(self, task_id: str) -> bool:
        """Check if cancellation was requested. Call this in tight loops."""
        event = self._cancel_events.get(task_id)
        if event:
            return event.is_set()
        return False

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self._tasks.get(task_id)

    def get_tasks_for_conversation(self, conversation_id: str) -> List[TaskInfo]:
        return [
            t for t in self._tasks.values()
            if t.conversation_id == conversation_id
        ]

    def get_active_tasks(self) -> List[TaskInfo]:
        return [
            t for t in self._tasks.values()
            if t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCEL_REQUESTED)
        ]

    def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """Remove tasks older than max_age_hours. Returns count removed."""
        cutoff = time.time() - (max_age_hours * 3600)
        removed = 0
        with self._lock:
            to_remove = []
            for tid, task in self._tasks.items():
                if task.completed_at and task.completed_at.timestamp() < cutoff:
                    to_remove.append(tid)
            for tid in to_remove:
                del self._tasks[tid]
                self._cancel_events.pop(tid, None)
                removed += 1
        return removed


# Singleton
_manager: Optional[TaskManager] = None


def get_task_manager() -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager
