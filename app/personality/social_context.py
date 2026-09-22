"""Social context: reads the emotional/pragmatic signals in the user's
message so tone adaptation has something to work with beyond keywords.

Deliberately lightweight — no sentiment models, no LLM calls. The
detected signals feed tone.py's style selection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

_URGENT = ("!!", "?!", "now", "immediately", "right now", "urgent")
_NEGATIVE = ("not working", "still broken", "again", "why is this", "come on", "seriously")
_POSITIVE = ("thanks", "nice", "great", "awesome", "perfect", "worked")


@dataclass(frozen=True)
class SocialSignals:
    urgency: bool
    negative: bool
    positive: bool
    message_length: int


def detect_signals(text: str) -> SocialSignals:
    lower = (text or "").lower()
    return SocialSignals(
        urgency=any(m in lower for m in _URGENT),
        negative=any(m in lower for m in _NEGATIVE),
        positive=any(m in lower for m in _POSITIVE),
        message_length=len((text or "").strip()),
    )


def style_from_signals(signals: SocialSignals, base_style: str) -> str:
    """Nudge the base style by social signals without over-mirroring."""
    if signals.urgency and base_style != "serious":
        return "in_a_hurry"
    if signals.negative and base_style in ("casual", "joking"):
        return "frustrated"
    return base_style
