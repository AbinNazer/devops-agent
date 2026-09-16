from app import postgres


def test_migration_directory_contains_saas_foundation():
    sql = (postgres.MIGRATIONS_DIR / "001_saas_foundation.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS organizations" in sql
    assert "CREATE TABLE IF NOT EXISTS infrastructure" in sql


def test_connect_requires_database_url():
    try:
        postgres.connect("")
    except ValueError as exc:
        assert "DATABASE_URL" in str(exc)
    else:
        raise AssertionError("empty database URL must fail closed")
