from typing import List
from .models import Memory

def search_memories(memories: List[Memory], query=None, mtype=None, env=None, comp=None):
    results = []
    for m in memories:
        if m.status != "active": continue
        if mtype and m.type != mtype: continue
        if env and m.environment != env: continue
        if comp and m.component != comp: continue
        if query and query.lower() not in m.content.lower() and query.lower() not in m.title.lower(): continue
        results.append(m)
    return sorted(results, key=lambda x: x.importance, reverse=True)
