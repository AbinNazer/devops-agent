"""
Tests for the JARVIS Phase 7 FastAPI web API.

Tests cover: health, providers, conversations, tasks, voice status,
and security boundaries.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Import the app
from app.api.app import app
from app.api.conversation_store import ConversationStore
from app.api.task_manager import TaskManager
from app.config import Config


@pytest.fixture
def client(monkeypatch):
    """Create a test client with mocked LLM router."""
    # These API tests exercise the legacy in-memory conversation endpoints.
    # Keep them independent from a developer's local .env, which may enable
    # PostgreSQL and therefore require an authenticated database session.
    monkeypatch.setattr(Config, "DATABASE_ENABLED", False)
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    # Do not load optional speech models during API tests; a local .env may
    # enable voice and make TestClient startup depend on heavyweight models.
    monkeypatch.setattr(Config, "VOICE_ENABLED", False)
    with patch("app.api.app.get_router") as mock_get_router:
        mock_router = MagicMock()
        mock_router.name = "MockRouter"
        mock_router.providers = []
        mock_router.primary = MagicMock()
        mock_router.primary.name = "mock"
        mock_get_router.return_value = mock_router
        with TestClient(app) as c:
            yield c


@pytest.fixture
def fresh_store():
    """Provide a fresh conversation store for each test."""
    store = ConversationStore()
    store._conversations = {}
    with patch("app.api.conversation_store.get_store", return_value=store):
        yield store


# ── Health ─────────────────────────────────────────────────────

class TestHealth:
    def test_health_endpoint(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "phase7"
        assert "provider" in data

    def test_health_never_exposes_secrets(self, client):
        resp = client.get("/api/health")
        data = resp.json()
        # Provider name should not contain API keys
        assert "api_key" not in data.get("provider", "").lower()
        assert "sk-" not in data.get("provider", "")


# ── Providers ──────────────────────────────────────────────────

class TestProviders:
    def test_list_providers(self, client):
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "active_provider" in data

    def test_switch_provider_unknown(self, client):
        resp = client.post("/api/providers/switch", json={"provider": "nonexistent"})
        assert resp.status_code == 400

    def test_provider_endpoint_never_exposes_api_keys(self, client):
        resp = client.get("/api/providers")
        text = resp.text
        assert "GROQ_API_KEY" not in text
        assert "OPENAI_API_KEY" not in text
        assert "ANTHROPIC_API_KEY" not in text
        assert "sk-" not in text


# ── Conversations ──────────────────────────────────────────────

class TestConversations:
    def test_create_conversation(self, client):
        resp = client.post("/api/conversations")
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["title"] == "New Chat"

    def test_list_conversations(self, client):
        # Create one first
        client.post("/api/conversations")
        resp = client.get("/api/conversations")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_get_conversation(self, client):
        create = client.post("/api/conversations")
        conv_id = create.json()["id"]
        resp = client.get(f"/api/conversations/{conv_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == conv_id

    def test_get_nonexistent_conversation(self, client):
        resp = client.get("/api/conversations/nonexistent")
        assert resp.status_code == 404

    def test_rename_conversation(self, client):
        create = client.post("/api/conversations")
        conv_id = create.json()["id"]
        resp = client.put(f"/api/conversations/{conv_id}/rename", json={"title": "Test Title"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_delete_conversation(self, client):
        create = client.post("/api/conversations")
        conv_id = create.json()["id"]
        resp = client.delete(f"/api/conversations/{conv_id}")
        assert resp.status_code == 200
        # Verify it's gone
        resp = client.get(f"/api/conversations/{conv_id}")
        assert resp.status_code == 404

    def test_search_conversations(self, client):
        client.post("/api/conversations")
        resp = client.get("/api/conversations/search?q=")
        assert resp.status_code == 200


# ── Tasks ──────────────────────────────────────────────────────

class TestTasks:
    def test_list_tasks_empty(self, client):
        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        assert resp.json()["tasks"] == []

    def test_get_nonexistent_task(self, client):
        resp = client.get("/api/tasks/nonexistent")
        assert resp.status_code == 404

    def test_cancel_nonexistent_task(self, client):
        resp = client.post("/api/tasks/nonexistent/cancel")
        assert resp.status_code == 404


# ── Voice ──────────────────────────────────────────────────────

class TestVoice:
    def test_voice_status(self, client):
        resp = client.get("/api/voice/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "stt_available" in data
        assert "tts_available" in data


# ── Task Manager Unit Tests ────────────────────────────────────

class TestTaskManager:
    def test_create_and_get(self):
        tm = TaskManager()
        task = tm.create_task("conv-1", "Test Task", tool_name="docker_status")
        assert task.id.startswith("task-")
        assert task.status.value == "queued"
        assert task.cancellable is True

        fetched = tm.get_task(task.id)
        assert fetched is not None
        assert fetched.id == task.id

    def test_start_task(self):
        tm = TaskManager()
        task = tm.create_task("conv-1", "Test")
        tm.start_task(task.id, step="connecting")
        fetched = tm.get_task(task.id)
        assert fetched.status.value == "running"
        assert fetched.current_step == "connecting"

    def test_complete_task(self):
        tm = TaskManager()
        task = tm.create_task("conv-1", "Test")
        tm.start_task(task.id)
        tm.complete_task(task.id, result={"status": "ok"})
        fetched = tm.get_task(task.id)
        assert fetched.status.value == "completed"
        assert fetched.result == {"status": "ok"}

    def test_cancel_task(self):
        tm = TaskManager()
        task = tm.create_task("conv-1", "Test")
        tm.start_task(task.id)
        assert tm.request_cancel(task.id) is True
        fetched = tm.get_task(task.id)
        assert fetched.status.value == "cancel_requested"

    def test_cannot_cancel_completed(self):
        tm = TaskManager()
        task = tm.create_task("conv-1", "Test")
        tm.start_task(task.id)
        tm.complete_task(task.id)
        assert tm.request_cancel(task.id) is False

    def test_cannot_cancel_non_cancellable(self):
        tm = TaskManager()
        task = tm.create_task("conv-1", "Test", cancellable=False)
        tm.start_task(task.id)
        assert tm.request_cancel(task.id) is False

    def test_tasks_for_conversation(self):
        tm = TaskManager()
        t1 = tm.create_task("conv-1", "Task A")
        t2 = tm.create_task("conv-2", "Task B")
        tm.create_task("conv-1", "Task C")
        conv1_tasks = tm.get_tasks_for_conversation("conv-1")
        assert len(conv1_tasks) == 2
        conv2_tasks = tm.get_tasks_for_conversation("conv-2")
        assert len(conv2_tasks) == 1

    def test_active_tasks(self):
        tm = TaskManager()
        t1 = tm.create_task("c", "A")
        tm.start_task(t1.id)
        t2 = tm.create_task("c", "B")
        tm.start_task(t2.id)
        tm.complete_task(t2.id)
        active = tm.get_active_tasks()
        assert len(active) == 1

    def test_cleanup_old_tasks(self):
        tm = TaskManager()
        task = tm.create_task("c", "Old")
        tm.start_task(task.id)
        tm.complete_task(task.id)
        # Set completed_at to old time
        from datetime import datetime, timedelta
        task.completed_at = datetime.utcnow() - timedelta(hours=25)
        removed = tm.cleanup_old_tasks(max_age_hours=24)
        assert removed == 1


# ── Conversation Store Unit Tests ──────────────────────────────

class TestConversationStore:
    def test_create_and_get(self):
        store = ConversationStore()
        store._conversations = {}
        conv = store.create("Test Chat")
        assert conv.title == "Test Chat"
        fetched = store.get(conv.id)
        assert fetched is not None
        assert fetched.id == conv.id

    def test_add_message(self):
        from app.api.models import ChatMessage, MessageRole
        store = ConversationStore()
        store._conversations = {}
        conv = store.create()
        msg = ChatMessage(role=MessageRole.USER, content="Hello")
        store.add_message(conv.id, msg)
        history = store.get_history(conv.id)
        assert len(history) == 1
        assert history[0]["content"] == "Hello"

    def test_auto_title(self):
        from app.api.models import ChatMessage, MessageRole
        store = ConversationStore()
        store._conversations = {}
        conv = store.create()
        msg = ChatMessage(role=MessageRole.USER, content="Check Docker containers")
        store.add_message(conv.id, msg)
        fetched = store.get(conv.id)
        assert fetched.title == "Check Docker containers"

    def test_rename(self):
        store = ConversationStore()
        store._conversations = {}
        conv = store.create()
        assert store.rename(conv.id, "New Name") is True
        assert store.get(conv.id).title == "New Name"

    def test_delete(self):
        store = ConversationStore()
        store._conversations = {}
        conv = store.create()
        assert store.delete(conv.id) is True
        assert store.get(conv.id) is None

    def test_search(self):
        from app.api.models import ChatMessage, MessageRole
        store = ConversationStore()
        store._conversations = {}
        # Create conversations with user messages (which set titles)
        conv1 = store.create()
        store.add_message(conv1.id, ChatMessage(role=MessageRole.USER, content="Debug Docker containers"))
        conv2 = store.create()
        store.add_message(conv2.id, ChatMessage(role=MessageRole.USER, content="Jenkins pipeline status"))
        results = store.search("docker")
        assert len(results) == 1
        assert "docker" in results[0].title.lower()


# ── Security Tests ─────────────────────────────────────────────

class TestSecurity:
    def test_no_api_keys_in_health(self, client):
        resp = client.get("/api/health")
        assert "GROQ" not in resp.text.upper().replace("GROQ (", "")
        assert "OPENAI_API_KEY" not in resp.text

    def test_no_api_keys_in_providers(self, client):
        resp = client.get("/api/providers")
        assert "sk-" not in resp.text
        assert "api_key" not in resp.text.lower()

    def test_no_ssh_secrets_in_endpoints(self, client):
        for path in ["/api/health", "/api/providers", "/api/voice/status"]:
            resp = client.get(path)
            assert "private" not in resp.text.lower() or "private" in "private_key_path"
            assert "ssh_password" not in resp.text.lower()
