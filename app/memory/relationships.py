from .models import Memory

def store_relationship(repository, source, relation, target, environment="", evidence=""):
    content = f"{source} --{relation}--> {target}"
    return repository.store_memory(Memory(type="semantic", title=content, content=(content + (f"; evidence: {evidence}" if evidence else "")), environment=environment, component=source, tags=["relationship", relation], importance=3.0, source="user" if not evidence else "evidence"))

def get_relationships(repository, component=None, environment=None):
    records = repository.search(mtype="semantic", comp=component, env=environment, limit=50)
    return [m for m in records if "relationship" in m.tags]
