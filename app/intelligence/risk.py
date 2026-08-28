"""
Risk analysis and blast-radius thinking.
"""
from dataclasses import dataclass
from typing import List

@dataclass
class RiskProfile:
    score: str # HIGH, MEDIUM, LOW
    blast_radius: List[str] # affected components
    confidence: float # 0.0 to 1.0

def calculate_risk(severity: str, persistence: bool, affected_components: List[str]) -> RiskProfile:
    score = "LOW"
    if severity == "CRITICAL" or (severity == "HIGH" and persistence):
        score = "HIGH"
    elif severity == "HIGH" or persistence:
        score = "MEDIUM"
        
    return RiskProfile(
        score=score,
        blast_radius=affected_components,
        confidence=0.8
    )
