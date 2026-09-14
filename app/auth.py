"""Minimal in-memory browser session authentication for the web UI."""
import hashlib
import hmac
import secrets

from app.config import Config

_sessions: set[str] = set()


def authenticate(username: str, password: str) -> bool:
    return hmac.compare_digest(username, Config.WEB_USERNAME) and hmac.compare_digest(password, Config.WEB_PASSWORD)


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    _sessions.add(token)
    return token


def valid_session(token: str | None) -> bool:
    return bool(token) and token in _sessions


def delete_session(token: str | None) -> None:
    if token:
        _sessions.discard(token)
