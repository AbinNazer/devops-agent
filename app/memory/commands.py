import re
from .models import Memory

def handle_memory_command(repository, text):
    value = (text or "").strip()
    lower = value.lower()
    remember = re.match(r"remember that\s+(.+)", value, re.I)
    forget = re.match(r"forget\s+(.+)", value, re.I)
    recall = re.match(r"(?:what do you remember about|have we seen)\s+(.+?)(?:\?|$)", value, re.I)
    if remember:
        fact = remember.group(1).rstrip(".")
        component = next((word for word in ("jenkins", "redis", "erp backend", "erp", "nginx", "k3s") if word in fact.lower()), "")
        memory = repository.store_memory(Memory(type="semantic", title=fact, content=fact, component=component, tags=["user-provided"], importance=3.0, source="user"))
        return f"Remembered: {memory.title} (memory {memory.id})."
    if forget:
        ids = repository.forget(query=forget.group(1).rstrip("."))
        return "Archived matching memory." if ids else "I could not find an active matching memory."
    if recall:
        query = recall.group(1).rstrip("?. ")
        records = repository.search(query=query, limit=5)
        if not records: return f"No active memory matches {query!r}."
        return "\n".join([f"- {m.title}: {m.content}" for m in records])
    return None
