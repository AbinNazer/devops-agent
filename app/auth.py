"""Minimal in-memory browser session authentication for the web UI."""
import hashlib
import hmac
import secrets
import base64
from datetime import datetime, timedelta, timezone

from app.config import Config

_sessions: set[str] = set()
_session_users: dict[str, str] = {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _database_enabled() -> bool:
    return bool(Config.DATABASE_ENABLED and Config.DATABASE_URL)


def authenticate(username: str, password: str) -> bool:
    # Fail closed: if the server has no real credentials configured, NOTHING
    # authenticates — including an attacker submitting empty strings to match.
    if not Config.WEB_USERNAME or not Config.WEB_PASSWORD:
        return False
    return hmac.compare_digest(username, Config.WEB_USERNAME) and hmac.compare_digest(password, Config.WEB_PASSWORD)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, digest = encoded.split("$", 2)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def create_session(username: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    if username:
        _session_users[token] = username
    if username and _database_enabled():
        from app.postgres import connect
        from app.tenancy import new_user_id
        expires = datetime.now(timezone.utc) + timedelta(hours=24)
        with connect(Config.DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO users (id, username) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING", (new_user_id(username), username))
                cursor.execute("INSERT INTO web_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)", (_token_hash(token), new_user_id(username), expires))
            connection.commit()
    return token


def valid_session(token: str | None) -> bool:
    if not token:
        return False
    if _database_enabled():
        from app.postgres import connect
        with connect(Config.DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM web_sessions WHERE token_hash = %s AND expires_at > CURRENT_TIMESTAMP", (_token_hash(token),))
                return cursor.fetchone() is not None
    return token in _sessions


def delete_session(token: str | None) -> None:
    if token:
        _sessions.discard(token)
        _session_users.pop(token, None)
        if _database_enabled():
            from app.postgres import connect
            with connect(Config.DATABASE_URL) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM web_sessions WHERE token_hash = %s", (_token_hash(token),))
                connection.commit()


def delete_all_sessions(username: str) -> None:
    """Revoke every browser session for a user without exposing tokens."""
    _tokens = [token for token, owner in _session_users.items() if owner == username]
    for token in _tokens:
        _sessions.discard(token)
        _session_users.pop(token, None)
    if username and _database_enabled():
        from app.postgres import connect
        from app.tenancy import new_user_id
        with connect(Config.DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM web_sessions WHERE user_id = %s", (new_user_id(username),))
            connection.commit()


def session_user(token: str | None) -> str | None:
    if not valid_session(token):
        return None
    if _database_enabled():
        from app.postgres import connect
        with connect(Config.DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT username FROM users u JOIN web_sessions s ON s.user_id = u.id WHERE s.token_hash = %s AND s.expires_at > CURRENT_TIMESTAMP", (_token_hash(token),))
                row = cursor.fetchone()
                return row[0] if row else None
    return _session_users.get(token)
