"""
Structured summary capability for infrastructure health.
"""
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TechnicalSummary:
    status: str
    key_findings: List[str]
    active_incidents: int
    recommendations: List[str]

def generate_technical_summary(health_data: dict, incidents: list, recommendations: list) -> TechnicalSummary:
    status = "HEALTHY"
    if incidents:
        status = "CRITICAL"
    elif any(r.get("severity") == "HIGH" for r in recommendations if isinstance(r, dict)):
        status = "WARNING"
        
    return TechnicalSummary(
        status=status,
        key_findings=["Processed health data"],
        active_incidents=len(incidents),
        recommendations=[r.get("action", "") if isinstance(r, dict) else str(r) for r in recommendations]
    )
