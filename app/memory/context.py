from .retrieval import search_memories

def inject_context(repository, question, live_evidence=None, environment=None, component=None, limit=5, max_chars=1800):
    memories = search_memories(repository.store.list_all(include_inactive=False), question, env=environment, comp=component, limit=limit)
    entries, used = [], 0
    for memory in memories:
        item = {"id": memory.id, "title": memory.title, "content": memory.content, "confidence": memory.confidence, "source": memory.source, "environment": memory.environment, "component": memory.component}
        length = len(item["title"]) + len(item["content"])
        if used + length > max_chars: continue
        entries.append(item); used += length
    return {"live_evidence": live_evidence or [], "memories": entries, "instruction": "Current live evidence takes precedence over historical memory."}
