from dataclasses import dataclass, field
import uuid
from typing import List, Optional
import time

@dataclass
class Memory:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = "episodic" # episodic, semantic, procedural, preference, failure
    title: str = ""
    content: str = ""
    environment: str = ""
    component: str = ""
    tags: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    confidence: str = "Medium" # High, Medium, Low
    importance: float = 1.0 # 1 to 5
    source: str = ""
    outcome: str = ""
    status: str = "active" # active, archived
