from pathlib import Path


def test_conversation_migration_has_owner_boundaries():
    sql = Path("db/migrations/001_saas_foundation.sql").read_text()
    assert "organization_id TEXT NOT NULL REFERENCES organizations" in sql
    assert "conversation_messages" in sql
    assert "idx_conversations_organization_updated" in sql


def test_conversation_repository_exposes_owned_history_operations():
    from app.conversation_repository import PostgresConversationRepository

    methods = {"create", "get", "list", "list_messages", "add_message", "rename", "update_provider", "delete"}
    assert methods.issubset(set(dir(PostgresConversationRepository)))
