"""Organization-owned PostgreSQL conversation persistence."""
from __future__ import annotations
from typing import Any
import uuid

from app.postgres import connect


class ConversationOwnershipError(PermissionError):
    pass


class PostgresConversationRepository:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def create(self, organization_id: str, user_id: str, title: str = "New Chat", provider: str = "groq") -> dict[str, Any]:
        conversation_id = uuid.uuid4().hex[:12]
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO conversations (id, organization_id, user_id, title, provider) VALUES (%s, %s, %s, %s, %s)", (conversation_id, organization_id, user_id, title, provider))
            connection.commit()
        return {"id": conversation_id, "organization_id": organization_id, "user_id": user_id, "title": title, "provider": provider}

    def get(self, conversation_id: str, organization_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT id, organization_id, user_id, title, provider, created_at, updated_at FROM conversations WHERE id = %s AND organization_id = %s"
        params: list[Any] = [conversation_id, organization_id]
        if user_id:
            query += " AND user_id = %s"
            params.append(user_id)
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, tuple(params))
                row = cursor.fetchone()
                if not row:
                    return None
                columns = [item.name for item in cursor.description]
                return dict(zip(columns, row))

    def list(self, organization_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT id, organization_id, user_id, title, provider, created_at, updated_at FROM conversations WHERE organization_id = %s"
        params: list[Any] = [organization_id]
        if user_id:
            query += " AND user_id = %s"
            params.append(user_id)
        query += " ORDER BY updated_at DESC"
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, tuple(params))
                columns = [item.name for item in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def add_message(self, conversation_id: str, organization_id: str, user_id: str, message_id: str, role: str, content: str, provider: str | None = None) -> None:
        if not self.get(conversation_id, organization_id, user_id):
            raise ConversationOwnershipError("conversation is not owned by this user and organization")
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO conversation_messages (id, conversation_id, organization_id, user_id, role, content, provider) VALUES (%s, %s, %s, %s, %s, %s, %s)", (message_id, conversation_id, organization_id, user_id, role, content, provider))
                cursor.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = %s AND organization_id = %s", (conversation_id, organization_id))
            connection.commit()

    def list_messages(self, conversation_id: str, organization_id: str, user_id: str) -> list[dict[str, Any]]:
        if not self.get(conversation_id, organization_id, user_id):
            raise ConversationOwnershipError("conversation is not owned by this user and organization")
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, conversation_id, organization_id, user_id, role, content, provider, created_at FROM conversation_messages WHERE conversation_id = %s AND organization_id = %s AND user_id = %s ORDER BY created_at ASC", (conversation_id, organization_id, user_id))
                columns = [item.name for item in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_with_messages(self, conversation_id: str, organization_id: str, user_id: str) -> dict[str, Any] | None:
        conversation = self.get(conversation_id, organization_id, user_id)
        if conversation is not None:
            conversation["messages"] = self.list_messages(conversation_id, organization_id, user_id)
        return conversation

    def rename(self, conversation_id: str, organization_id: str, user_id: str, title: str) -> None:
        if not self.get(conversation_id, organization_id, user_id):
            raise ConversationOwnershipError("conversation is not owned by this user and organization")
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE conversations SET title = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND organization_id = %s AND user_id = %s", (title, conversation_id, organization_id, user_id))
            connection.commit()

    def update_provider(self, conversation_id: str, organization_id: str, user_id: str, provider: str) -> None:
        if not self.get(conversation_id, organization_id, user_id):
            raise ConversationOwnershipError("conversation is not owned by this user and organization")
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE conversations SET provider = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s AND organization_id = %s AND user_id = %s", (provider, conversation_id, organization_id, user_id))
            connection.commit()

    def delete(self, conversation_id: str, organization_id: str, user_id: str) -> None:
        if not self.get(conversation_id, organization_id, user_id):
            raise ConversationOwnershipError("conversation is not owned by this user and organization")
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM conversations WHERE id = %s AND organization_id = %s AND user_id = %s", (conversation_id, organization_id, user_id))
            connection.commit()
