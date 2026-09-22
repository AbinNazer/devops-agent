from .store import MemoryStore
from .models import Memory
from .retrieval import search_memories
from .patterns import redact_secrets

class MemoryRepository:
    def __init__(self, db_path="memory.db"):
        self.store = MemoryStore(db_path)
        from app.vector_store import VectorStore
        self.vector_store = VectorStore(str(db_path) + ".vectors.db")

    def store_memory(self, memory: Memory):
        memory.title, memory.content = redact_secrets(memory.title), redact_secrets(memory.content)
        memory.outcome, memory.source = redact_secrets(memory.outcome), redact_secrets(memory.source)
        self.store.save(memory)
        try:
            self.vector_store.upsert(memory.id, f"{memory.title}\n{memory.content}\n{memory.component}\n{' '.join(memory.tags)}", {"type": memory.type, "environment": memory.environment, "component": memory.component})
        except RuntimeError:
            pass
        return memory

    def search(self, query=None, mtype=None, env=None, comp=None, limit=20, include_inactive=False):
        memories = self.store.list_all(include_inactive=include_inactive)
        lexical = search_memories(memories, query, mtype, env, comp, limit=max(limit, 20))
        if not query:
            return lexical[:limit]
        by_id = {m.id: m for m in memories if m.status == "active"}
        semantic = []
        try:
            vector_hits = self.vector_store.query(query, limit=max(limit * 3, 10))
        except RuntimeError:
            vector_hits = []
        for hit in vector_hits:
            memory = by_id.get(hit["id"])
            if memory and (not mtype or memory.type == mtype) and (not env or not memory.environment or memory.environment.lower() == env.lower()) and (not comp or not memory.component or memory.component.lower() == comp.lower()):
                semantic.append((hit["score"], memory))
        ordered = []
        seen = set()
        for _, memory in sorted(semantic, key=lambda pair: pair[0], reverse=True):
            if memory.id not in seen: ordered.append(memory); seen.add(memory.id)
        for memory in lexical:
            if memory.id not in seen: ordered.append(memory); seen.add(memory.id)
        return ordered[:limit]

    def forget(self, memory_id=None, query=None, status="archived"):
        candidates = [self.store.get(memory_id)] if memory_id else self.search(query=query, limit=100)
        changed = []
        for memory in candidates:
            if memory: self.store.set_status(memory.id, status); changed.append(memory.id)
        return changed
