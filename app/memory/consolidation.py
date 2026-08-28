from .patterns import redact_secrets

def _key(memory):
    return (memory.type, memory.environment.lower(), memory.component.lower(), memory.title.strip().lower(), memory.content.strip().lower())

def consolidate_memories(repository, low_importance=1.0):
    memories = repository.store.list_all(include_inactive=False)
    survivors, consolidated, archived = {}, [], []
    for memory in memories:
        key = _key(memory)
        if key not in survivors:
            survivors[key] = memory
            continue
        keeper = survivors[key]
        keeper.importance = max(keeper.importance, memory.importance)
        keeper.confidence = "High" if "High" in (keeper.confidence, memory.confidence) else keeper.confidence
        keeper.provenance = list(dict.fromkeys(keeper.provenance + memory.provenance + [memory.id]))
        repository.store_memory(keeper)
        repository.store.set_status(memory.id, "superseded")
        consolidated.append(memory.id)
    for memory in repository.store.list_all(include_inactive=False):
        if memory.importance <= low_importance and memory.type not in ("episodic", "failure") and memory.outcome == "":
            repository.store.set_status(memory.id, "archived")
            archived.append(memory.id)
    return {"consolidated": consolidated, "archived": archived}
