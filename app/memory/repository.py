from .store import MemoryStore
from .models import Memory
from .retrieval import search_memories
from .patterns import redact_secrets

class MemoryRepository:
    def __init__(self, db_path="memory.db"):
        self.store = MemoryStore(db_path)

    def store_memory(self, memory: Memory):
        memory.title, memory.content = redact_secrets(memory.title), redact_secrets(memory.content)
        memory.outcome, memory.source = redact_secrets(memory.outcome), redact_secrets(memory.source)
        self.store.save(memory)
        return memory

    def search(self, query=None, mtype=None, env=None, comp=None, limit=20, include_inactive=False):
        return search_memories(self.store.list_all(include_inactive=include_inactive), query, mtype, env, comp, limit)

    def forget(self, memory_id=None, query=None, status="archived"):
        candidates = [self.store.get(memory_id)] if memory_id else self.search(query=query, limit=100)
        changed = []
        for memory in candidates:
            if memory: self.store.set_status(memory.id, status); changed.append(memory.id)
        return changed
