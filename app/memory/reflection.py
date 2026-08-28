from dataclasses import dataclass
from .models import Memory
from .patterns import redact_secrets

@dataclass
class Reflection:
    observed_problem: str
    diagnosis: str
    evidence: list
    recommendation: str
    outcome: str
    learned: str
    confidence: str
    tags: list

def reflect(repository, observed_problem="", diagnosis="", evidence=None, recommendation="", outcome="", confidence="Medium", tags=None, environment="", component="", title="Incident reflection"):
    evidence = [redact_secrets(str(item)) for item in (evidence or []) if item]
    reflection = Reflection(redact_secrets(observed_problem), redact_secrets(diagnosis), evidence, redact_secrets(recommendation), redact_secrets(outcome), redact_secrets(diagnosis or recommendation), confidence, list(tags or []))
    content = "\n".join(filter(None, [f"Observed: {reflection.observed_problem}", f"Diagnosis: {reflection.diagnosis}", f"Evidence: {'; '.join(reflection.evidence)}", f"Recommendation: {reflection.recommendation}", f"Outcome: {reflection.outcome}", f"Learned: {reflection.learned}"]))
    memory = Memory(type="episodic", title=title, content=content, environment=environment, component=component, tags=reflection.tags, confidence=confidence, importance=3.0, source="reflection", outcome=reflection.outcome)
    repository.store_memory(memory)
    return reflection, memory
