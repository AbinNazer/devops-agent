import logging
import asyncio
import time
from typing import Dict, Optional

from app.config import Config
from .session import TerminalSession, LocalTerminalSession

logger = logging.getLogger("terminal")

MAX_SESSIONS = 5
SESSION_IDLE_TIMEOUT = 3600
SESSION_MAX_LIFETIME = 28800


class TerminalManager:
    def __init__(self):
        self._sessions: Dict[str, TerminalSession] = {}

    async def create_session(self, rows=24, cols=80):
        if len(self._sessions) >= MAX_SESSIONS:
            self._cleanup_expired()
            if len(self._sessions) >= MAX_SESSIONS:
                raise RuntimeError("Too many active terminal sessions")

        mode = Config.EXECUTION_MODE.lower()
        if mode == "local":
            session = LocalTerminalSession()
        else:
            session = TerminalSession(
                host=Config.VPS_HOST,
                port=Config.VPS_SSH_PORT,
                username=Config.VPS_SSH_USER,
                key_path=Config.VPS_SSH_KEY_PATH,
                known_hosts_path=Config.VPS_KNOWN_HOSTS_PATH,
            )
        session.rows = rows
        session.cols = cols
        self._sessions[session.id] = session
        logger.info("terminal_created session=%s mode=%s total=%d", session.id, mode, len(self._sessions))
        # Return the session ID immediately. SSH connection and PTY setup can
        # take several seconds; the WebSocket will wait for this same session
        # to become ready instead of making the HTTP request block first.
        asyncio.create_task(self._connect_session(session), name=f"terminal-connect-{session.id}")
        return session

    async def _connect_session(self, session):
        try:
            await session.connect()
        except Exception:
            self._sessions.pop(session.id, None)
            session.close()

    def get_session(self, session_id: str) -> Optional[TerminalSession]:
        return self._sessions.get(session_id)

    def close_session(self, session_id: str) -> bool:
        session = self._sessions.pop(session_id, None)
        if session:
            session.close()
            return True
        return False

    def close_all(self):
        for s in list(self._sessions.values()):
            s.close()
        self._sessions.clear()

    def _cleanup_expired(self):
        now = time.time()
        expired = [
            sid for sid, s in self._sessions.items()
            if (now - s.last_activity) > SESSION_IDLE_TIMEOUT
            or (now - s.created_at) > SESSION_MAX_LIFETIME
            or not s.is_alive()
        ]
        for sid in expired:
            self.close_session(sid)

    @property
    def active_count(self) -> int:
        return len(self._sessions)


_manager: Optional[TerminalManager] = None


def get_terminal_manager() -> TerminalManager:
    global _manager
    if _manager is None:
        _manager = TerminalManager()
    return _manager
