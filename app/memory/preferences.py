from .models import Memory

SAFE_PREFERENCES = {"response_style", "units", "format", "terminology"}

def get_preferences(repository):
    return {m.title.removeprefix("preference:"): m.content for m in repository.search(mtype="preference", limit=50)}

def save_preference(repository, name, value):
    if name not in SAFE_PREFERENCES: raise ValueError("unsupported preference")
    existing = repository.search(query="preference:" + name, mtype="preference", limit=1)
    memory = existing[0] if existing else Memory(type="preference", title="preference:" + name, importance=2.0, source="user")
    memory.content = str(value)
    repository.store_memory(memory)
    return memory

def delete_preference(repository, name):
    return repository.forget(query="preference:" + name)
