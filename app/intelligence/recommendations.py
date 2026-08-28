"""
Recommendations Engine. Separate OBSERVATION, DIAGNOSIS, RECOMMENDATION, ACTION.
"""
from dataclasses import dataclass
from typing import List

@dataclass
class ActionItem:
    observation: str
    diagnosis: str
    recommendation: str
    action: str
    priority: str # CRITICAL, HIGH, MEDIUM, LOW, INFO

def rank_recommendations(items: List[ActionItem]) -> List[ActionItem]:
    priority_map = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    return sorted(items, key=lambda x: priority_map.get(x.priority, 99))
