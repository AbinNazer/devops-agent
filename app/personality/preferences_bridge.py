"""Bridge between the personality subsystem and Phase 4 memory preferences.

Reads the user's long-term communication preferences (stored via the
existing memory preference system) and exposes them to the personality
directive. Writing remains the memory system's job — this module never
promotes conversation chatter into memory on its own.
"""
from __future__ import annotations

from typing import Dict

from app.memory.preferences import get_preferences

# Preference names from memory/preferences.py SAFE_PREFERENCES that the
# personality layer understands.
_STYLE_KEYS = ("response_style", "format", "terminology")


def load_communication_preferences(repository) -> Dict[str, str]:
    """Fetch communication-relevant preferences from the memory repository.

    Returns {} when the repository is unavailable — personality degrades
    gracefully and the agent keeps its default voice (memory failure must
    never break the response path).
    """
    if repository is None:
        return {}
    try:
        all_prefs = get_preferences(repository)
        return {k: v for k, v in all_prefs.items() if k in _STYLE_KEYS}
    except Exception:
        return {}
