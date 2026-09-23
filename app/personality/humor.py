"""Context-aware humor gate.

Humor is a *decision*, not a decoration: the engine weighs severity,
response type, and recent humor frequency, then returns one of four
levels. It never changes technical content — downstream consumers only
use the level as prompt guidance.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.personality.profile import PersonalityConfig
from app.personality.tone import Severity


class HumorLevel(Enum):
    NONE = "none"
    LIGHT = "light"
    WITTY = "witty"
    SARCASTIC = "sarcastic"


# Response types that must never carry humor. "destructive_action" covers
# anything routed through the Phase 5 control pipeline — a playful framing
# must never make a mutation look casual.
_NO_HUMOR_RESPONSE_TYPES = frozenset({
    "destructive_action", "security", "failure_report", "error",
    "approval_request", "incident_report",
})

# Response types where light humor fits naturally.
_HUMOR_FRIENDLY_TYPES = frozenset({
    "acknowledgment", "success_report", "general_chat", "diagnostic_result",
})


@dataclass(frozen=True)
class HumorDecision:
    level: HumorLevel
    reasoning: str


def should_use_humor(profile: PersonalityConfig,
                     severity: Severity,
                     response_type: str,
                     recent_humor_count: int = 0) -> HumorDecision:
    """Decide the humor level for one response. Fully deterministic.

    Priority (matches the safety hierarchy): severity > response type >
    frequency damping > profile settings.
    """
    if not profile.humor.enabled or profile.humor.level <= 0:
        return HumorDecision(HumorLevel.NONE, "humor disabled in profile")
    if severity is Severity.CRITICAL:
        return HumorDecision(HumorLevel.NONE,
                             "critical severity — humor suppressed entirely")
    if response_type in _NO_HUMOR_RESPONSE_TYPES:
        return HumorDecision(HumorLevel.NONE,
                             f"response type '{response_type}' never carries humor")
    if not profile.humor.enabled:
        return HumorDecision(HumorLevel.NONE, "humor disabled")

    # Frequency damping: at most one humorous beat in the last ~4 responses,
    # but LOW-severity chat keeps a light beat so humor stays present. The
    # default profile is maxed (0.95), so damping only kicks in when the
    # model has genuinely been leaning on jokes.
    if recent_humor_count >= 4:
        return HumorDecision(HumorLevel.LIGHT if severity is Severity.LOW else HumorLevel.NONE,
                             "recent responses already had humor — damping")

    if severity is Severity.ELEVATED:
        return HumorDecision(HumorLevel.LIGHT,
                             "elevated severity — at most light, dry humor")
    if response_type in _HUMOR_FRIENDLY_TYPES:
        if profile.humor.style == "playful" or profile.humor.level >= 0.7:
            return HumorDecision(HumorLevel.SARCASTIC, "playful context — sarcasm welcome")
        return HumorDecision(HumorLevel.WITTY, "casual context, humor welcomed")
    return HumorDecision(HumorLevel.WITTY, "neutral context — a witty aside fits the default voice")
