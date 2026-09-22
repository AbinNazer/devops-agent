"""Conversation-level helpers: building the conversation-state block that
appears in the system prompt and resolving follow-up references.

The referent resolution is deliberately conservative: it only resolves a
pronoun when conversation state unambiguously holds a single active
subject. Anything ambiguous is left to the LLM (which sees full history).
"""
from __future__ import annotations

from typing import Optional

from app.personality.state import ConversationState

_REFERENCE_WORDS = ("it", "that", "them", "the container", "the service")


def resolve_referent(state: ConversationState, text: str) -> Optional[str]:
    """Best-effort explicit referent for "it/that" style follow-ups.

    Returns the active subject name, or None when state is ambiguous or
    the message isn't a follow-up. Never guesses when multiple subjects
    are equally recent.
    """
    lower = (text or "").strip().lower()
    if not any(lower.startswith(w + " ") or lower == w for w in _REFERENCE_WORDS):
        if "restart" not in lower or not any(w in lower.split() for w in _REFERENCE_WORDS):
            return None
    candidates = [s for s in (state.current_container, state.current_service) if s]
    if len(set(candidates)) == 1:
        return candidates[0]
    return None


def conversation_block(state: ConversationState) -> str:
    """Render conversation state as a prompt block. Empty when no state."""
    snap = state.summary()
    if not snap:
        return ""
    lines = ["Conversation state (for resolving references like \"it\" or \"that\"):"]
    lines += [f"- {key}: {value}" for key, value in sorted(snap.items())]
    return "\n".join(lines)
