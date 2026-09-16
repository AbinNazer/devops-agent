"""
In-memory conversation store with JSON file persistence.

Conversations are stored in sessions/ directory as JSON files, reusing
the existing session architecture from Phase 1.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional, List, Dict

from app.api.models import Conversation, ConversationSummary, ChatMessage

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "sessions")


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def _sort_timestamp(value: datetime) -> datetime:
    """Return a comparable UTC timestamp for old and new saved records."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class ConversationStore:
    """Thread-safe conversation store with file persistence."""

    def __init__(self):
        self._conversations: Dict[str, Conversation] = {}
        self._load_all()

    def _load_all(self):
        """Load all conversations from disk on startup."""
        _ensure_dir()
        for fname in os.listdir(SESSIONS_DIR):
            if not fname.startswith("web_") or not fname.endswith(".json"):
                continue
            conv_id = fname[4:-5]  # strip "web_" prefix and ".json"
            try:
                path = os.path.join(SESSIONS_DIR, fname)
                with open(path) as f:
                    data = json.load(f)
                conv = Conversation(**data)
                self._conversations[conv_id] = conv
            except Exception:
                pass  # skip corrupt files

    def _save(self, conv: Conversation):
        """Persist a conversation to disk."""
        _ensure_dir()
        path = os.path.join(SESSIONS_DIR, f"web_{conv.id}.json")
        with open(path, "w") as f:
            json.dump(conv.model_dump(mode="json"), f, indent=2)

    def create(self, title: str = "New Chat", provider: str = "groq") -> Conversation:
        conv = Conversation(title=title, provider=provider)
        self._conversations[conv.id] = conv
        self._save(conv)
        return conv

    def get(self, conv_id: str) -> Optional[Conversation]:
        return self._conversations.get(conv_id)

    def list_all(self) -> List[ConversationSummary]:
        convs = sorted(
            self._conversations.values(),
            key=lambda c: _sort_timestamp(c.updated_at),
            reverse=True,
        )
        return [
            ConversationSummary(
                id=c.id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at,
                message_count=len(c.messages),
            )
            for c in convs
        ]

    def add_message(self, conv_id: str, message: ChatMessage) -> Optional[ChatMessage]:
        conv = self._conversations.get(conv_id)
        if not conv:
            return None
        conv.messages.append(message)
        conv.updated_at = datetime.now(timezone.utc)
        # Auto-title from first user message
        if conv.title == "New Chat" and message.role.value == "user":
            conv.title = message.content[:60]
        self._save(conv)
        return message

    def get_history(self, conv_id: str) -> list:
        """Return the conversation history as raw dicts (for the LLM)."""
        conv = self._conversations.get(conv_id)
        if not conv:
            return []
        return [m.model_dump(mode="json") for m in conv.messages]

    def update_provider(self, conv_id: str, provider: str) -> bool:
        conv = self._conversations.get(conv_id)
        if not conv:
            return False
        conv.provider = provider
        conv.updated_at = datetime.now(timezone.utc)
        self._save(conv)
        return True

    def rename(self, conv_id: str, title: str) -> bool:
        conv = self._conversations.get(conv_id)
        if not conv:
            return False
        conv.title = title
        conv.updated_at = datetime.now(timezone.utc)
        self._save(conv)
        return True

    def delete(self, conv_id: str) -> bool:
        if conv_id not in self._conversations:
            return False
        del self._conversations[conv_id]
        path = os.path.join(SESSIONS_DIR, f"web_{conv_id}.json")
        if os.path.exists(path):
            os.remove(path)
        return True

    def search(self, query: str) -> List[ConversationSummary]:
        query_lower = query.lower()
        results = [
            c for c in self._conversations.values()
            if query_lower in c.title.lower()
            or any(query_lower in m.content.lower() for m in c.messages if m.role.value == "user")
        ]
        results.sort(key=lambda c: _sort_timestamp(c.updated_at), reverse=True)
        return [
            ConversationSummary(
                id=c.id,
                title=c.title,
                created_at=c.created_at,
                updated_at=c.updated_at,
                message_count=len(c.messages),
            )
            for c in results
        ]


# Singleton
_store: Optional[ConversationStore] = None


def get_store() -> ConversationStore:
    global _store
    if _store is None:
        _store = ConversationStore()
    return _store
