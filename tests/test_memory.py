import pytest
import os
from app.memory.models import Memory
from app.memory.repository import MemoryRepository
from app.memory.patterns import redact_secrets

@pytest.fixture
def repo():
    if os.path.exists("test_memory.db"):
        os.remove("test_memory.db")
    repository = MemoryRepository("test_memory.db")
    yield repository
    if os.path.exists("test_memory.db"):
        os.remove("test_memory.db")

def test_store_and_retrieve(repo):
    mem = Memory(title="Test Incident", content="The database went down.", type="episodic", environment="prod", component="db")
    repo.store_memory(mem)
    
    results = repo.search(query="database")
    assert len(results) == 1
    assert results[0].title == "Test Incident"
    
def test_search_filters(repo):
    mem1 = Memory(title="Incident 1", content="App crash", type="episodic", environment="prod", component="app")
    mem2 = Memory(title="Fact 1", content="App is python", type="semantic", environment="prod", component="app")
    repo.store_memory(mem1)
    repo.store_memory(mem2)
    
    results = repo.search(mtype="episodic")
    assert len(results) == 1
    assert results[0].title == "Incident 1"

def test_secret_redaction(repo):
    mem = Memory(title="Secrets", content="My password is password=supersecret and api_key=12345")
    repo.store_memory(mem)
    
    results = repo.search()
    assert len(results) == 1
    assert "password=[REDACTED]" in results[0].content
    assert "api_key=[REDACTED]" in results[0].content
    assert "supersecret" not in results[0].content
    assert "12345" not in results[0].content

def test_feedback_updates_confidence(repo):
    from app.memory.feedback import record_feedback
    mem = Memory(title="Test", content="Data", confidence="Medium")
    repo.store_memory(mem)
    
    record_feedback(repo.store, mem.id, is_positive=True)
    results = repo.search()
    assert results[0].confidence == "High"

def test_redact_secrets_function():
    text = "Here is a ssh key: -----BEGIN RSA PRIVATE KEY-----\nbase64data\n-----END RSA PRIVATE KEY-----"
    redacted = redact_secrets(text)
    assert "[REDACTED KEY]" in redacted
    assert "base64data" not in redacted
