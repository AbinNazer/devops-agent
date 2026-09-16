"""Server-side encrypted secret storage primitives."""
import base64
import hashlib
import uuid

from app.config import Config


def _fernet():
    if not Config.ENCRYPTION_KEY:
        raise RuntimeError("ENCRYPTION_KEY is required for encrypted secret storage")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise RuntimeError("Install cryptography before using encrypted secrets") from exc
    key = Config.ENCRYPTION_KEY.encode()
    try:
        base64.urlsafe_b64decode(key)
    except Exception as exc:
        raise ValueError("ENCRYPTION_KEY must be a Fernet URL-safe base64 key") from exc
    return Fernet(key)


class EncryptedSecretStore:
    """Encrypted PostgreSQL-backed secret store; plaintext never leaves this class."""

    def __init__(self, database_url: str):
        self.database_url = database_url

    def put(self, owner_id: str, secret_name: str, plaintext: str) -> None:
        from app.postgres import connect
        ciphertext = _fernet().encrypt(plaintext.encode()).decode()
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO encrypted_secrets (id, owner_id, secret_name, ciphertext) VALUES (%s, %s, %s, %s) ON CONFLICT (owner_id, secret_name) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, updated_at = CURRENT_TIMESTAMP", (uuid.uuid4().hex, owner_id, secret_name, ciphertext))
            connection.commit()

    def get(self, owner_id: str, secret_name: str) -> str | None:
        from app.postgres import connect
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT ciphertext FROM encrypted_secrets WHERE owner_id = %s AND secret_name = %s", (owner_id, secret_name))
                row = cursor.fetchone()
        return _fernet().decrypt(row[0].encode()).decode() if row else None

    def delete(self, owner_id: str, secret_name: str) -> None:
        from app.postgres import connect
        with connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM encrypted_secrets WHERE owner_id = %s AND secret_name = %s", (owner_id, secret_name))
            connection.commit()
