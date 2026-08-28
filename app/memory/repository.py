from .store import MemoryStore
from .models import Memory
from .retrieval import search_memories
from .scoring import score_memory
from .patterns import redact_secrets

class MemoryRepository:
    def __init__(self, db_path="memory.db"):
        self.store = MemoryStore(db_path)
    
    def store_memory(self, memory: Memory):
        memory.content = redact_secrets(memory.content)
        self.store.save(memory)
    
    def search(self, query=None, mtype=None, env=None, comp=None):
        memories = self.store.list_all()
        return search_memories(memories, query, mtype, env, comp)
