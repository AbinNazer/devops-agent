import re
from dataclasses import dataclass
from collections import defaultdict

@dataclass
class Pattern:
    id: str
    title: str
    component: str
    environment: str
    occurrence_count: int
    first_seen: float
    last_seen: float
    confidence: str
    related_incident_ids: list
    tags: list

def redact_secrets(text: str) -> str:
    text = str(text or "")
    patterns = [
        (r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(password\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(authorization:\s*(?:bearer|basic)\s+)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(aws_secret_access_key\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(aws_access_key_id\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"(?i)(cookie:\s*)[^\n]+", r"\1[REDACTED]"),
        (r"(?i)(access[_-]?token\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]"),
        (r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED KEY]"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.DOTALL if "BEGIN" in pattern else 0)
    return text

def detect_patterns(repository, minimum_occurrences=2):
    groups = defaultdict(list)
    for memory in repository.store.list_all(include_inactive=False):
        if memory.type not in ("episodic", "failure"): continue
        key = (memory.environment.lower(), memory.component.lower(), re.sub(r"\b\d+\b", "#", memory.title.lower()).strip())
        groups[key].append(memory)
    found = []
    for (environment, component, title), memories in groups.items():
        if len(memories) < minimum_occurrences: continue
        memories.sort(key=lambda item: item.timestamp)
        high = sum(item.confidence == "High" for item in memories)
        found.append(Pattern(f"pattern:{component}:{title}", memories[-1].title or "Recurring incident", component, environment, len(memories), memories[0].timestamp, memories[-1].timestamp, "High" if high else "Medium", [item.id for item in memories], sorted({tag for item in memories for tag in item.tags})))
    return found
