from dataclasses import dataclass
from typing import Any
@dataclass(frozen=True)
class TaskRequest:
    task_id: str; infrastructure_id: str; capability: str; arguments: dict[str, Any]; timeout: int = 30; risk_level: str = "READ"
@dataclass(frozen=True)
class TaskResult:
    task_id: str; status: str; result: dict[str, Any] | None = None; duration_ms: int | None = None; error: dict[str, str] | None = None