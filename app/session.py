"""
Lightweight conversation persistence. Saves/loads `history` (the list of
message dicts) to JSON files on disk, so closing the CLI doesn't lose
context. Deliberately simple — no database, just files in sessions/.
"""
import json
import os
from datetime import datetime

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sessions")


def _ensure_dir():
    os.makedirs(SESSIONS_DIR, exist_ok=True)


def save_session(history: list, name: str = None) -> str:
    """Save history to sessions/<name or timestamp>.json. Returns the path used."""
    _ensure_dir()
    if not name:
        name = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    if not name.endswith(".json"):
        name += ".json"
    path = os.path.join(SESSIONS_DIR, name)
    with open(path, "w") as f:
        json.dump(history, f, indent=2)
    return path


def load_session(name: str) -> list:
    """Load history from sessions/<name>.json. Raises FileNotFoundError if missing."""
    if not name.endswith(".json"):
        name += ".json"
    path = os.path.join(SESSIONS_DIR, name)
    with open(path) as f:
        return json.load(f)


def list_sessions() -> list:
    """Return sorted list of saved session filenames (without .json extension)."""
    _ensure_dir()
    files = [f[:-5] for f in os.listdir(SESSIONS_DIR) if f.endswith(".json")]
    return sorted(files)
