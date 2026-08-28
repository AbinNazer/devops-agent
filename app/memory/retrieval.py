import re
from .scoring import score_memory

def search_memories(memories, query=None, mtype=None, env=None, comp=None, limit=20):
    words = set(re.findall(r"[a-z0-9_-]+", (query or "").lower()))
    results = []
    for memory in memories:
        if memory.status != "active" or (mtype and memory.type != mtype): continue
        if env and memory.environment and memory.environment.lower() != env.lower(): continue
        if comp and memory.component and memory.component.lower() != comp.lower(): continue
        text = " ".join((memory.title, memory.content, memory.component, " ".join(memory.tags))).lower()
        overlap = sum(word in text for word in words)
        if words and not overlap: continue
        score = score_memory(memory) + overlap * 2
        if env and memory.environment.lower() == env.lower(): score += 2
        if comp and memory.component.lower() == comp.lower(): score += 3
        results.append((score, memory))
    return [m for _, m in sorted(results, key=lambda pair: (pair[0], pair[1].timestamp), reverse=True)[:limit]]
