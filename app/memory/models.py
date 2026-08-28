from dataclasses import dataclass, field
import uuid
from typing import List
import time

@dataclass
class Memory:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "episodic"
    title: str = ""
    content: str = ""
    environment: str = ""
    component: str = ""
    tags: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    confidence: str = "Medium"
    importance: float = 1.0
    source: str = ""
    outcome: str = ""
    status: str = "active"
    updated_at: float = field(default_factory=time.time)
    provenance: List[str] = field(default_factory=list)

    def __post_init__(self):
        self.type = self.type if self.type in {"episodic", "semantic", "procedural", "preference", "failure"} else "episodic"
        self.status = self.status if self.status in {"active", "superseded", "invalid", "archived"} else "active"
        self.tags = list(dict.fromkeys(str(item).strip().lower() for item in self.tags if str(item).strip()))
        self.provenance = list(dict.fromkeys(self.provenance or ([self.source] if self.source else [])))
