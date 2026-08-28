"""
Diagnostic Planner: planning layer for complex questions.
"""
from dataclasses import dataclass
from typing import List

@dataclass
class DiagnosticPlan:
    goal: str
    steps: List[str]
    next_tool: str
    
def plan_diagnostics(question: str) -> DiagnosticPlan:
    question_lower = question.lower()
    if "jenkins" in question_lower:
        return DiagnosticPlan(goal="Check Jenkins health", steps=["get_jenkins_status", "get_jenkins_logs"], next_tool="get_jenkins_status")
    elif "docker" in question_lower:
        return DiagnosticPlan(goal="Check Docker status", steps=["docker_health_status", "docker_stats"], next_tool="docker_health_status")
    else:
        return DiagnosticPlan(goal="General health sweep", steps=["check_infrastructure_health"], next_tool="check_infrastructure_health")
