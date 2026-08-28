"""
Context management for differentiating LOCAL, VPS, AWS environments.
"""
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class EnvironmentContext:
    env_type: str # LOCAL, VPS, AWS
    metadata: Dict[str, Any]

class ContextManager:
    def __init__(self):
        self.environments = {
            "LOCAL": EnvironmentContext("LOCAL", {}),
            "VPS": EnvironmentContext("VPS", {}),
            "AWS": EnvironmentContext("AWS", {})
        }
    
    def get_context(self, env_type: str) -> EnvironmentContext:
        return self.environments.get(env_type.upper())
