from .store import MemoryStore

def record_feedback(store: MemoryStore, mem_id: str, is_positive: bool):
    memories = store.list_all()
    target = next((m for m in memories if m.id == mem_id), None)
    if target:
        if is_positive:
            new_conf = "High" if target.confidence == "Medium" else target.confidence
        else:
            new_conf = "Low" if target.confidence == "Medium" else target.confidence
        store.update_confidence(mem_id, new_conf)

def record_outcome(store: MemoryStore, mem_id: str, outcome: str):
    store.update_outcome(mem_id, outcome)
