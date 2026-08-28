"""
Main entrypoint for intelligence analysis.
"""
from typing import List, Dict

def identify_log_errors(logs: List[str]) -> List[str]:
    return [log for log in logs if "error" in str(log).lower() or "fail" in str(log).lower()]

def detect_failure_patterns(errors: List[str]) -> List[str]:
    patterns = []
    if len(errors) > 5:
        patterns.append("High error frequency detected (possible loop/crash).")
    return patterns

def analyze_trends(current_metrics: Dict, historical_metrics: List[Dict]) -> str:
    return "Stable" if not historical_metrics else "Requires more data to establish trend."
