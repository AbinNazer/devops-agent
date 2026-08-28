from .store import MemoryStore
from .patterns import redact_secrets

def record_feedback(store: MemoryStore, mem_id: str, is_positive: bool, comment=""):
    target = store.get(mem_id)
    if not target: return False
    if is_positive:
        new_conf = {"Low": "Medium", "Medium": "High", "High": "High"}.get(target.confidence, "Medium")
        target.outcome = "confirmed: " + redact_secrets(comment or "recommendation helped")
    else:
        new_conf = {"High": "Medium", "Medium": "Low", "Low": "Low"}.get(target.confidence, "Low")
        target.outcome = "rejected: " + redact_secrets(comment or "recommendation did not help")
    store.update_confidence(mem_id, new_conf)
    store.update_outcome(mem_id, target.outcome)
    return True

def record_outcome(store: MemoryStore, mem_id: str, outcome: str):
    store.update_outcome(mem_id, redact_secrets(outcome))
