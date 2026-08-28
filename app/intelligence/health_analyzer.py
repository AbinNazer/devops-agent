"""
Smart Health Analysis.
"""
from dataclasses import dataclass

@dataclass
class HealthFinding:
    metric: str
    observed_value: float
    threshold: float
    severity: str
    evidence: str
    explanation: str

def evaluate_health(metric: str, value: float, warn_threshold: float, crit_threshold: float) -> HealthFinding:
    severity = "HEALTHY"
    if value >= crit_threshold:
        severity = "CRITICAL"
    elif value >= warn_threshold:
        severity = "WARNING"
        
    return HealthFinding(
        metric=metric,
        observed_value=value,
        threshold=crit_threshold if severity == "CRITICAL" else warn_threshold,
        severity=severity,
        evidence=f"{metric} at {value}",
        explanation=f"Value is {'above' if severity != 'HEALTHY' else 'below'} thresholds."
    )
