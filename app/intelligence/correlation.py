"""
Cross-system correlation.
"""
def correlate_findings(metrics: dict, logs: list) -> list:
    correlations = []
    if metrics.get("memory_usage", 0) > 90 and any("jenkins" in str(log).lower() for log in logs):
        correlations.append("High memory usage correlated with Jenkins activity.")
    return correlations
