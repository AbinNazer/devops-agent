import pytest

from app import secret_store


def test_secret_store_requires_encryption_key(monkeypatch):
    monkeypatch.setattr(secret_store.Config, "ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        secret_store._fernet()
